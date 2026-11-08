"""Backlog (RPO) and revenue from SEC EDGAR, with a link to the filing for every value.

    python -m pipeline.edgar fetch     # download companyfacts JSON (needs HANGAR_CONTACT)
    python -m pipeline.edgar build     # cached JSON -> data/financials.csv + data/edgar_coverage.csv
    python -m pipeline.edgar daily     # fetch + build (what GitHub Actions runs)

Source: the SEC's free "companyfacts" API, one JSON file per company holding
every number the company tagged in its 10-K/10-Q filings (XBRL). Public domain.
SEC fair-access rules allow 10 requests/second; we make 12 requests per run,
2 seconds apart, with a User-Agent naming the project and a contact
(pipeline/http.py).

Same two stages as pipeline/run.py: FETCH (slow, network) and BUILD (fast,
offline, tested). The raw JSON is cached in data/edgar/ but not committed
(a few MB per company, and it changes every filing); the CSVs keep the
accession number and a link for every value, so everything stays traceable.

Rules (each one has a test in tests/test_edgar.py):
  * Metrics. Backlog = RevenueRemainingPerformanceObligation (RPO), the
    standard tag closest to "backlog". It is NOT the same as the backlog
    companies quote: RPO leaves out unfunded orders and unexercised options.
    Many companies never tag their quoted backlog; we don't read it from the
    10-K text. Revenue = Revenues or RevenueFromContractWithCustomerExcluding
    AssessedTax, one tag per company (see choose_revenue_tag).
  * Periods come from the filing's own start/end dates, never the calendar:
    ~365 days = annual (fiscal year), ~91 days = quarter, anything else
    (6- and 9-month year-to-date) is ignored. A balance (RPO) has only an end.
  * Duplicates. Every 10-K repeats last year's figures as comparatives. We
    keep the FIRST filing that reported a period: that is what investors knew
    at the time, and it keeps the "as of" rule honest (a 2024 figure must not
    look like it was published in 2026). If a later filing reports a
    different value for the same period, it is kept as a revision with its
    own link, never silently swapped in.
  * Gaps are written down, never filled: see coverage().
"""
from __future__ import annotations

import argparse
import csv
import gzip
import json
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
RAW = DATA / "edgar"                       # cached companyfacts (gitignored)
COMPANIES = DATA / "edgar_companies.csv"
FINANCIALS = DATA / "financials.csv"
COVERAGE = DATA / "edgar_coverage.csv"

TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
FACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json"

RPO_TAG = "RevenueRemainingPerformanceObligation"
REVENUE_TAGS = ["Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax"]
FORMS = {"10-K", "10-Q", "10-K/A", "10-Q/A"}
SINCE = "2019-01-01"                       # keep periods ending on or after this date

# Per-company revenue tag choices made by hand, with the reason. Empty means
# the automatic rule in choose_revenue_tag decides (and says why).
REVENUE_TAG_OVERRIDE: dict[str, tuple[str, str]] = {}

COLUMNS = ["ticker", "cik", "metric", "tag", "period_type", "period_start", "period_end",
           "fiscal_year", "value_usd", "form", "accession", "filed", "filing_url",
           "revised_value_usd", "revised_form", "revised_filed", "revised_accession",
           "revised_filing_url"]
COVERAGE_COLUMNS = ["ticker", "cik", "metric", "status", "tag", "tag_reason", "first_period",
                    "last_period", "n_values", "gaps", "latest_filing", "latest_filing_filed"]


# --------------------------------------------------------------- pure rules

def filing_url(cik: int | str, accession: str) -> str:
    """Link to the filing's index page on EDGAR (lists the 10-K/10-Q and exhibits)."""
    return (f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/"
            f"{accession.replace('-', '')}/{accession}-index.htm")


def period_type(start: str | None, end: str) -> str | None:
    """instant (a balance on one date), annual, quarter, or None (year-to-date etc.).
    52/53-week years (364 or 371 days) and 13/14-week quarters both fit."""
    if not start:
        return "instant"
    days = (date.fromisoformat(end) - date.fromisoformat(start)).days + 1
    if 350 <= days <= 380:
        return "annual"
    if 80 <= days <= 100:
        return "quarter"
    return None


def fiscal_year(end: str) -> int:
    """Fiscal year label = calendar year the period ends in, except a 52/53-week
    year that ends in the first days of January belongs to the year before
    (a year ending Jan 1, 2023 is fiscal 2022)."""
    return (date.fromisoformat(end) - timedelta(days=7)).year


def usd_facts(facts: dict, tag: str) -> list[dict]:
    return facts.get("facts", {}).get("us-gaap", {}).get(tag, {}).get("units", {}).get("USD", [])


def dedupe(raw: list[dict], keep: set[str]) -> list[dict]:
    """One record per period (start, end): the first filing that reported it,
    plus the latest later filing that reported a DIFFERENT value, if any."""
    groups: dict[tuple, list[dict]] = {}
    for f in raw:
        if f.get("form") not in FORMS or f["end"] < SINCE:
            continue
        ptype = period_type(f.get("start"), f["end"])
        if ptype not in keep:
            continue
        groups.setdefault((f.get("start", ""), f["end"]), []).append({**f, "ptype": ptype})
    out = []
    for (start, end), fs in groups.items():
        fs.sort(key=lambda f: (f["filed"], f["accn"]))
        first = fs[0]
        changed = [f for f in fs[1:] if f["val"] != first["val"]]
        out.append({"start": start, "end": end, "ptype": first["ptype"], "first": first,
                    "revised": changed[-1] if changed else None})
    return sorted(out, key=lambda r: (r["end"], r["start"]))


def choose_revenue_tag(facts: dict) -> tuple[str | None, str]:
    """Pick ONE revenue tag per company, so the series never mixes definitions.
    Rule: the tag whose annual series reaches the most recent fiscal year;
    on a tie, the one with more fiscal years since 2019. Returns (tag, reason)."""
    ticker = facts.get("_ticker", "")
    if ticker in REVENUE_TAG_OVERRIDE:
        return REVENUE_TAG_OVERRIDE[ticker]
    scored = []
    for tag in REVENUE_TAGS:
        years = dedupe(usd_facts(facts, tag), {"annual"})
        if years:
            scored.append((years[-1]["end"], len(years), tag))
    if not scored:
        return None, "neither revenue tag is reported"
    scored.sort(reverse=True)
    best = scored[0]
    if len(scored) == 1:
        return best[2], "only revenue tag reported"
    other = scored[1]
    if best[0] > other[0]:
        why = f"latest fiscal year (to {best[0]}); {other[2]} stops at {other[0]}"
    else:
        why = f"more fiscal years ({best[1]} vs {other[1]})"
    return best[2], why


def extract(ticker: str, facts: dict) -> list[dict]:
    """Rows for data/financials.csv: RPO balances and annual revenue."""
    cik = int(facts["cik"])
    rows = []
    series = [("rpo", RPO_TAG, {"instant"})]
    rev_tag, _ = choose_revenue_tag({**facts, "_ticker": ticker})
    if rev_tag:
        series.append(("revenue", rev_tag, {"annual"}))
    for metric, tag, keep in series:
        for r in dedupe(usd_facts(facts, tag), keep):
            f, rv = r["first"], r["revised"]
            rows.append({
                "ticker": ticker, "cik": cik, "metric": metric, "tag": tag,
                "period_type": r["ptype"], "period_start": r["start"], "period_end": r["end"],
                "fiscal_year": fiscal_year(r["end"]) if r["ptype"] == "annual" else "",
                "value_usd": f["val"], "form": f["form"], "accession": f["accn"],
                "filed": f["filed"], "filing_url": filing_url(cik, f["accn"]),
                "revised_value_usd": rv["val"] if rv else "",
                "revised_form": rv["form"] if rv else "",
                "revised_filed": rv["filed"] if rv else "",
                "revised_accession": rv["accn"] if rv else "",
                "revised_filing_url": filing_url(cik, rv["accn"]) if rv else "",
            })
    return rows


def latest_filing(facts: dict) -> dict | None:
    """The most recent 10-K/10-Q in the file (any tag): used to tell whether a
    metric stopped being tagged."""
    best = None
    for ns in facts.get("facts", {}).values():
        for tagdata in ns.values():
            for unit in tagdata.get("units", {}).values():
                for f in unit:
                    if f.get("form") in FORMS and (best is None or f["filed"] > best["filed"]):
                        best = f
    return best


def find_gaps(ends: list[str], max_days: int) -> list[str]:
    """Breaks in a series: consecutive period ends further apart than max_days."""
    out = []
    for a, b in zip(ends, ends[1:]):
        if (date.fromisoformat(b) - date.fromisoformat(a)).days > max_days:
            out.append(f"no XBRL value between {a} and {b}")
    return out


def coverage(ticker: str, facts: dict, rows: list[dict]) -> list[dict]:
    """One row per (company, metric): is it reported, which tag, and every gap.
    status: reported | gaps | not_reported."""
    cik = int(facts["cik"])
    last = latest_filing(facts)
    rev_tag, rev_reason = choose_revenue_tag({**facts, "_ticker": ticker})
    out = []
    for metric, tag, reason, max_days in [
            ("rpo", RPO_TAG, "standard tag for remaining performance obligations", 100),
            ("revenue", rev_tag, rev_reason, 380)]:
        mine = [r for r in rows if r["ticker"] == ticker and r["metric"] == metric]
        ends = [r["period_end"] for r in mine]
        gaps = find_gaps(ends, max_days)
        if mine and last:
            last_value_filed = max(r["filed"] for r in mine)
            # A newer 10-K (or, for balances, a newer 10-Q) without this tag.
            newer_forms = {"10-K", "10-Q"} if metric == "rpo" else {"10-K"}
            newer = [f for f in _filings(facts) if f["filed"] > last_value_filed
                     and f["form"] in newer_forms]
            if newer:
                n = newer[0]
                gaps.append(f"not tagged in the {n['form']} filed {n['filed']} or later")
        status = "not_reported" if not mine else ("gaps" if gaps else "reported")
        out.append({
            "ticker": ticker, "cik": cik, "metric": metric, "status": status,
            "tag": tag or "", "tag_reason": reason if mine else "not reported in XBRL",
            "first_period": ends[0] if ends else "", "last_period": ends[-1] if ends else "",
            "n_values": len(mine), "gaps": "; ".join(gaps),
            "latest_filing": last["accn"] if last else "",
            "latest_filing_filed": last["filed"] if last else "",
        })
    return out


def _filings(facts: dict) -> list[dict]:
    """Every distinct 10-K/10-Q (by accession) in the file, oldest first."""
    seen = {}
    for ns in facts.get("facts", {}).values():
        for tagdata in ns.values():
            for unit in tagdata.get("units", {}).values():
                for f in unit:
                    if f.get("form") in {"10-K", "10-Q"} and f["filed"] >= SINCE:
                        seen.setdefault(f["accn"], {"accn": f["accn"], "form": f["form"],
                                                    "filed": f["filed"]})
    return sorted(seen.values(), key=lambda f: f["filed"])


# --------------------------------------------------------------- fetch / build

def _core() -> list[str]:
    from .site import CORE
    return CORE


def fetch(tickers: list[str]) -> None:
    from . import http
    print("company list:", TICKERS_URL)
    listing = json.loads(http.get(TICKERS_URL))
    by_ticker = {v["ticker"]: v for v in listing.values()}
    companies = []
    RAW.mkdir(parents=True, exist_ok=True)
    for t in tickers:
        if t not in by_ticker:
            print(f"  {t}: not in the SEC ticker list, skipped")
            continue
        cik = int(by_ticker[t]["cik_str"])
        companies.append({"ticker": t, "cik": cik, "sec_name": by_ticker[t]["title"]})
        url = FACTS_URL.format(cik=cik)
        print(f"  {t}: {url}")
        try:
            body = http.get(url)
            json.loads(body)                               # refuse to cache a broken file
        except Exception as e:                             # one company failing keeps the rest
            print(f"  {t}: FAILED ({e}); its previous figures are kept")
            continue
        (RAW / f"{t}.json.gz").write_bytes(gzip.compress(body))
    _write(COMPANIES, companies, ["ticker", "cik", "sec_name"])


def load_facts(ticker: str, folder: Path = RAW) -> dict | None:
    p = folder / f"{ticker}.json.gz"
    return json.loads(gzip.decompress(p.read_bytes())) if p.exists() else None


def _read(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def build(tickers: list[str], folder: Path = RAW) -> None:
    """Rewrite the CSVs from cached JSON. A company with no cached file (e.g.
    the SEC was down) keeps its previous rows instead of disappearing."""
    rows, cov = [], []
    old_rows, old_cov = _read(FINANCIALS), _read(COVERAGE)
    for t in tickers:
        facts = load_facts(t, folder)
        if facts is None:
            print(f"  {t}: no cached companyfacts; keeping previous figures")
            rows += [r for r in old_rows if r["ticker"] == t]
            cov += [c for c in old_cov if c["ticker"] == t]
            continue
        r = extract(t, facts)
        rows += r
        cov += coverage(t, facts, r)
    _write(FINANCIALS, rows, COLUMNS)
    _write(COVERAGE, cov, COVERAGE_COLUMNS)
    for c in cov:
        if c["status"] != "reported":
            print(f"  {c['ticker']} {c['metric']}: {c['status']} {c['gaps']}")
    print(f"{FINANCIALS.name}: {len(rows)} values for {len({r['ticker'] for r in rows})} companies")


def trim_fixture(ticker: str, out: Path) -> None:
    """Save a real companyfacts file for tests, keeping only the tags we use
    plus the cover-page share count (present in every 10-K/10-Q, so the
    'latest filing' rule still sees every filing). Values are untouched."""
    facts = load_facts(ticker)
    keep = {RPO_TAG, *REVENUE_TAGS}
    small = {"cik": facts["cik"], "entityName": facts["entityName"], "facts": {
        "us-gaap": {k: v for k, v in facts["facts"].get("us-gaap", {}).items() if k in keep},
        "dei": {k: v for k, v in facts["facts"].get("dei", {}).items()
                if k == "EntityCommonStockSharesOutstanding"}}}
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(gzip.compress(json.dumps(small, separators=(",", ":")).encode(), mtime=0))


def _write(path: Path, rows: list[dict], cols: list[str]) -> None:
    tmp = path.with_suffix(".tmp")
    with tmp.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerows(rows)
    tmp.replace(path)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["fetch", "build", "daily", "fixture"])
    ap.add_argument("--tickers", nargs="*")
    a = ap.parse_args()
    tickers = a.tickers or _core()
    if a.cmd in ("fetch", "daily"):
        fetch(tickers)
    if a.cmd in ("build", "daily"):
        build(tickers)
    if a.cmd == "fixture":
        for t in tickers:
            trim_fixture(t, ROOT / "tests" / "fixtures" / "edgar" / f"{t}.json.gz")


if __name__ == "__main__":
    main()
