"""Trailing-12-month EBITDA (and its parts) from SEC filings, with links.

    python -m scripts.valuation LMT RTX NOC GD
    python -m scripts.valuation KTOS AVAV --facts tests/fixtures/edgar

Reads the companyfacts files that `python -m pipeline.edgar fetch` caches in
data/edgar/ (pass --facts to read another folder). Prints, for each company,
operating income, depreciation & amortization (D&A) and EBITDA over the last
four reported quarters, and the three filings each figure comes from, so a
pitch can cite them. It only reads and prints: nothing is saved or published.

Why "trailing 12 months" (TTM) needs three numbers: a 10-Q reports profit for
the year so far (for example January to September). To get the last 12 months:

    TTM = last full fiscal year + this year so far - same months last year
        = FY2025 + (Jan-Sep 2026) - (Jan-Sep 2025)

EBITDA here = operating income + D&A from the cash-flow statement. It is a
calculation by Hangar, not a figure the companies report, and companies define
their own "adjusted EBITDA" differently.
"""
from __future__ import annotations

import argparse
from datetime import date, timedelta
from pathlib import Path

from pipeline import edgar

OPERATING_INCOME = ["OperatingIncomeLoss"]
# The cash-flow statement's D&A line; companies use different tags for it.
DA_TAGS = ["DepreciationDepletionAndAmortization", "DepreciationAmortizationAndAccretionNet",
           "DepreciationAndAmortization"]
FORMS = {"10-K", "10-Q", "10-K/A", "10-Q/A"}
SLACK_DAYS = 10            # 52/53-week calendars shift period ends by a few days


def _days(start: str, end: str) -> int:
    return (date.fromisoformat(end) - date.fromisoformat(start)).days + 1


def _near(a: str, b: str, days: int = SLACK_DAYS) -> bool:
    return abs((date.fromisoformat(a) - date.fromisoformat(b)).days) <= days


def durations(facts: dict, tag: str) -> list[dict]:
    """One fact per (start, end) period with a duration, keeping the most
    recently filed value (later filings restate the comparison periods)."""
    best: dict[tuple, dict] = {}
    for f in edgar.usd_facts(facts, tag):
        if f.get("form") not in FORMS or not f.get("start"):
            continue
        k = (f["start"], f["end"])
        if k not in best or (f["filed"], f["accn"]) > (best[k]["filed"], best[k]["accn"]):
            best[k] = f
    return sorted(best.values(), key=lambda f: (f["end"], f["start"]))


def ttm(facts: dict, tags: list[str]) -> dict | None:
    """Trailing-12-month value for the first tag that works, or None.
    Returns {tag, value, end, parts: [(label, fact)]}."""
    for tag in tags:
        facts_ = durations(facts, tag)
        if not facts_:
            continue
        annuals = [f for f in facts_ if 350 <= _days(f["start"], f["end"]) <= 380]
        if not annuals:
            continue
        latest_end = max(f["end"] for f in facts_)
        last_fy = max(annuals, key=lambda f: f["end"])
        if last_fy["end"] == latest_end:                  # the 10-K is the newest filing
            return {"tag": tag, "value": last_fy["val"], "end": last_fy["end"],
                    "parts": [("fiscal year", last_fy)]}
        # Year-to-date: starts the day after the last fiscal year ended (no overlap,
        # no gap, even on a 52/53-week calendar), ends at the latest date.
        day_after = (date.fromisoformat(last_fy["end"]) + timedelta(days=1)).isoformat()
        ytd = [f for f in facts_ if f["end"] == latest_end and f["start"] == day_after
               and _days(f["start"], f["end"]) < 350]
        if not ytd:
            continue
        cur = max(ytd, key=lambda f: _days(f["start"], f["end"]))
        length = _days(cur["start"], cur["end"])
        # Same months a year earlier: starts exactly when the last fiscal year started.
        prior = [f for f in facts_ if f["start"] == last_fy["start"]
                 and abs(_days(f["start"], f["end"]) - length) <= SLACK_DAYS]
        if not prior:
            continue
        prev = min(prior, key=lambda f: abs(_days(f["start"], f["end"]) - length))
        return {"tag": tag, "value": last_fy["val"] + cur["val"] - prev["val"], "end": cur["end"],
                "parts": [("fiscal year", last_fy), ("year to date", cur), ("minus same months a year earlier", prev)]}
    return None


def ebitda(facts: dict) -> dict:
    op = ttm(facts, OPERATING_INCOME)
    # Use the first D&A tag whose 12 months end when operating income's do: some
    # companies report one D&A tag only annually and another every quarter (RTX).
    # Only an EXACT end-date match counts; "close enough" could mix periods.
    candidates = [x for x in (ttm(facts, [t]) for t in DA_TAGS) if x]
    matching = [x for x in candidates if op and x["end"] == op["end"]]
    da = (matching or candidates or [None])[0]
    out = {"operating_income": op, "da": da, "ebitda": None, "reason": ""}
    if op is None or da is None:
        out["reason"] = "operating income or D&A not found in the filings"
    elif op["end"] != da["end"]:
        out["reason"] = f"operating income ends {op['end']} but D&A ends {da['end']}"
    else:
        out["ebitda"] = op["value"] + da["value"]
    return out


def _million(v: float) -> str:
    return f"${v / 1e6:,.0f} million"


def report(ticker: str, facts: dict) -> str:
    cik = facts.get("cik")
    e = ebitda(facts)
    lines = [f"{ticker} ({facts.get('entityName', '')})"]
    for label, item in (("Operating income", e["operating_income"]), ("D&A", e["da"])):
        if item is None:
            lines.append(f"  {label}: not found")
            continue
        lines.append(f"  {label}, 12 months to {item['end']}: {_million(item['value'])}  [{item['tag']}]")
        for part, f in item["parts"]:
            lines.append(f"    {part}: {f['start']} to {f['end']} = {_million(f['val'])}  "
                         f"{f['form']} filed {f['filed']}  {edgar.filing_url(cik, f['accn'])}")
    if e["ebitda"] is None:
        lines.append(f"  EBITDA: not calculated ({e['reason']})")
    else:
        lines.append(f"  EBITDA (calculated) = {_million(e['ebitda'])}")
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("tickers", nargs="+")
    ap.add_argument("--facts", type=Path, default=edgar.RAW, help="folder of <TICKER>.json.gz files")
    args = ap.parse_args()
    for t in args.tickers:
        facts = edgar.load_facts(t.upper(), args.facts)
        print(report(t.upper(), facts) if facts else
              f"{t.upper()}: no file in {args.facts}; run `python -m pipeline.edgar fetch {t.upper()}` first")
        print()


if __name__ == "__main__":
    main()
