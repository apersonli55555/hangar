"""Build the static website from data/*.csv.

    python -m pipeline.site            # writes site/ (open site/index.html)

Architecture: CSV files -> metrics.py (what counts) -> Jinja2 templates -> plain
HTML/CSS in site/. No JavaScript, no database, no server: GitHub Pages serves
the files as-is. Charts are SVG drawn here in Python.
"""
from __future__ import annotations

import csv
import os
import shutil
import zlib
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
from html import escape
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from . import metrics, pitches as pitch_lib
from .metrics import BRANCH_ORDER, branch_group, classify, fmt_pct, fmt_usd

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
SRC = ROOT / "site_src"
OUT = ROOT / "site"

CORE = ["LMT", "RTX", "NOC", "GD", "LHX", "BA", "HII", "GE", "TDG", "HWM", "KTOS"]
CSV_COLUMNS = ["announcement_date", "award_date", "branch", "company", "ticker", "kind",
               "amount_usd", "amount_qualifier", "shared_amount_usd", "obligated_at_award_usd",
               "cumulative_face_value_usd", "action_type", "contract_number", "is_idiq",
               "involves_fms", "review_reasons", "notes", "source_url", "archive_url", "text"]


def read_csv(path: Path) -> list[dict]:
    with path.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


# ------------------------------------------------------------------ charts

def bar_chart_svg(quarters: list[str], data: dict[str, dict[str, float]],
                  partial_last: bool = False) -> str:
    """Stacked quarterly bars of DEFINITE awards by branch. Each segment has a
    <title> tooltip with the exact quarter, branch and amount."""
    w, h, left, bottom, top = 720, 260, 56, 28, 12
    totals = [sum(data.get(q, {}).values()) for q in quarters]
    vmax = max(totals + [1.0])
    step = _nice_step(vmax / 4)
    ymax = step * (int(vmax / step) + 1)
    plot_w, plot_h = w - left - 8, h - bottom - top
    bw = plot_w / max(len(quarters), 1)
    parts = [f'<svg class="chart" viewBox="0 0 {w} {h}" role="img" '
             f'aria-label="Definite contract awards per quarter, stacked by branch">']
    for i in range(int(ymax / step) + 1):            # gridlines + y labels
        v = i * step
        y = top + plot_h - v / ymax * plot_h
        parts.append(f'<line class="grid" x1="{left}" x2="{w - 8}" y1="{y:.1f}" y2="{y:.1f}"/>')
        label = f"${v / 1e9:g}B" if v >= 1e9 else f"${v / 1e6:g}M" if v else "$0"
        parts.append(f'<text class="axis" x="{left - 6}" y="{y + 4:.1f}" text-anchor="end">'
                     f'{escape(label)}</text>')
    for i, q in enumerate(quarters):
        x = left + i * bw + bw * 0.15
        y = top + plot_h
        for b in BRANCH_ORDER:
            v = data.get(q, {}).get(b, 0.0)
            if v <= 0:
                continue
            bh = v / ymax * plot_h
            y -= bh
            cls = "s-" + b.lower().replace(" ", "-")
            parts.append(f'<rect class="seg {cls}" x="{x:.1f}" y="{y:.1f}" width="{bw * 0.7:.1f}" '
                         f'height="{bh:.1f}"><title>{q} · {b}: {fmt_usd(v)} (${v:,.0f})</title></rect>')
        last = i == len(quarters) - 1
        if len(quarters) <= 12 or i % 2 == 0 or last:   # thin out x labels when crowded
            star = "*" if (last and partial_last) else ""
            parts.append(f'<text class="axis" x="{x + bw * 0.35:.1f}" y="{h - 8}" text-anchor="middle">'
                         f'{q[2:4]}{q[-2:]}{star}</text>')
    parts.append("</svg>")
    return "".join(parts)


def _nice_step(raw: float) -> float:
    mag = 10 ** (len(str(int(raw))) - 1) if raw >= 1 else 1
    for m in (1, 2, 2.5, 5, 10):
        if raw <= m * mag:
            return m * mag
    return 10 * mag


def _x_scale(dates: list[str], left: float, right: float):
    d0 = date.fromisoformat(min(dates))
    d1 = date.fromisoformat(max(dates))
    span = max((d1 - d0).days, 1)
    return lambda d: left + (date.fromisoformat(d) - d0).days / span * (right - left)


def _usd_label(v: float) -> str:
    return f"${v / 1e9:g}B" if v >= 1e9 else f"${v / 1e6:g}M" if v else "$0"


def _y_axis(parts: list[str], vmax: float, top: float, plot_h: float, left: float, w: float,
            fmt=_usd_label) -> float:
    """Gridlines and labels from 0 to a round number above vmax; returns that number."""
    if vmax / 4 >= 1:
        step = _nice_step(vmax / 4)
    else:                                   # small ratios: few ticks so phone labels don't collide
        step = 1.0 if vmax >= 2 else 0.5
    ymax = step * (int(vmax / step) + 1)
    for i in range(int(ymax / step) + 1):
        v = i * step
        y = top + plot_h - v / ymax * plot_h
        parts.append(f'<line class="grid" x1="{left}" x2="{w - 8}" y1="{y:.1f}" y2="{y:.1f}"/>')
        parts.append(f'<text class="axis" x="{left - 6}" y="{y + 4:.1f}" text-anchor="end">'
                     f'{escape(fmt(v))}</text>')
    return ymax


def financials_chart_svg(rpo: list[dict], revenue: list[dict]) -> str:
    """RPO balances (solid line) and fiscal-year revenue (dashed line) over time.
    Every point is a link to the SEC filing it came from, with a tooltip.
    Values are as first filed (see pipeline/edgar.py)."""
    w, h, left, bottom, top = 720, 260, 56, 28, 12
    pts = rpo + revenue
    vmax = max(float(r["value_usd"]) for r in pts)
    plot_h = h - bottom - top
    parts = [f'<svg class="chart" viewBox="0 0 {w} {h}" role="img" '
             f'aria-label="Remaining performance obligations and annual revenue over time">']
    ymax = _y_axis(parts, vmax, top, plot_h, left, w)
    first = min(r["period_end"] for r in pts)
    x = _x_scale([r["period_end"] for r in pts], left + 10, w - 20)
    for y in sorted({r["period_end"][:4] for r in pts}):    # a label at each Jan 1
        d = f"{y}-01-01"
        if d >= first:
            parts.append(f'<text class="axis" x="{x(d):.1f}" y="{h - 8}" text-anchor="middle">{y}</text>')
    for cls, series in (("l-rev", revenue), ("l-rpo", rpo)):
        coords = [(x(r["period_end"]), top + plot_h - float(r["value_usd"]) / ymax * plot_h, r)
                  for r in series]
        if len(coords) > 1:
            path = " ".join(f"{cx:.1f},{cy:.1f}" for cx, cy, _ in coords)
            parts.append(f'<polyline class="{cls}" points="{path}"/>')
        for cx, cy, r in coords:
            what = (f"FY{r['fiscal_year']} revenue" if cls == "l-rev"
                    else f"RPO at {r['period_end']}")
            parts.append(f'<a href="{escape(r["filing_url"])}"><circle class="{cls}" cx="{cx:.1f}" '
                         f'cy="{cy:.1f}" r="4"><title>{what}: {fmt_usd(float(r["value_usd"]))} '
                         f'({r["form"]} filed {r["filed"]})</title></circle></a>')
    parts.append("</svg>")
    return "".join(parts)


def ratio_rows(rpo: list[dict], revenue: list[dict]) -> list[dict]:
    """RPO at each fiscal year end divided by that year's revenue. Only years
    where both exist for the SAME date (both come from the same 10-K); nearby
    dates are never paired."""
    by_end = {r["period_end"]: r for r in rpo}
    out = []
    for rev in revenue:
        b = by_end.get(rev["period_end"])
        if b and float(rev["value_usd"]) > 0:
            out.append({"fiscal_year": rev["fiscal_year"], "period_end": rev["period_end"],
                        "ratio": float(b["value_usd"]) / float(rev["value_usd"]),
                        "rpo": b, "rev": rev})
    return out


def ratio_chart_svg(rows: list[dict]) -> str:
    w, h, left, bottom, top = 720, 200, 56, 28, 18
    plot_h = h - bottom - top
    parts = [f'<svg class="chart" viewBox="0 0 {w} {h}" role="img" '
             f'aria-label="RPO divided by fiscal-year revenue">']
    ymax = _y_axis(parts, max(r["ratio"] for r in rows), top, plot_h, left, w,
                   fmt=lambda v: f"{v:g}×")
    bw = (w - left - 8) / len(rows)
    for i, r in enumerate(rows):
        bh = r["ratio"] / ymax * plot_h
        x0 = left + i * bw + bw * 0.25
        mid = x0 + bw * 0.25
        parts.append(f'<a href="{escape(r["rev"]["filing_url"])}"><rect class="bar-ratio" x="{x0:.1f}" '
                     f'y="{top + plot_h - bh:.1f}" width="{bw * 0.5:.1f}" height="{bh:.1f}">'
                     f'<title>FY{r["fiscal_year"]}: RPO {fmt_usd(float(r["rpo"]["value_usd"]))} / '
                     f'revenue {fmt_usd(float(r["rev"]["value_usd"]))} = {r["ratio"]:.2f}×'
                     f'</title></rect></a>')
        parts.append(f'<text class="axis" x="{mid:.1f}" y="{top + plot_h - bh - 4:.1f}" '
                     f'text-anchor="middle">{r["ratio"]:.1f}×</text>')
        parts.append(f'<text class="axis" x="{mid:.1f}" y="{h - 8}" text-anchor="middle">'
                     f'FY{r["fiscal_year"]}</text>')
    parts.append("</svg>")
    return "".join(parts)


# ------------------------------------------------------------------ rows

# ticker -> {"rpo": [...], "revenue": [...]} from data/financials.csv (set in build)
FIN: dict[str, dict[str, list[dict]]] = {}

def price_link(ticker: str, iso_date: str | None = None) -> str:
    """A LINK to the stock's daily prices on Yahoo Finance. Hangar never fetches
    or shows price data itself: the free data APIs' terms don't allow public
    display (see methodology). With a date, the window runs from a week before
    to two weeks after the announcement."""
    symbol = ticker.replace(".", "-")            # MOG.A -> MOG-A on Yahoo
    if not iso_date:
        return f"https://finance.yahoo.com/quote/{symbol}/history/"
    d = datetime.fromisoformat(iso_date).replace(tzinfo=timezone.utc)
    start = int((d - timedelta(days=7)).timestamp())
    end = int((d + timedelta(days=15)).timestamp())
    return f"https://finance.yahoo.com/quote/{symbol}/history/?period1={start}&period2={end}"


def price_link_since(ticker: str, iso_date: str, today: str) -> str:
    """Yahoo Finance daily prices from a pitch date to today (a LINK only)."""
    symbol = ticker.replace(".", "-")
    start = int(datetime.fromisoformat(iso_date).replace(tzinfo=timezone.utc).timestamp())
    end = int((datetime.fromisoformat(today).replace(tzinfo=timezone.utc) + timedelta(days=1)).timestamp())
    return f"https://finance.yahoo.com/quote/{symbol}/history/?period1={start}&period2={end}"


def since_pitch(p: dict, rows: list[dict], fin: dict, today: str) -> dict:
    """Hangar's own data for the pitched company after the pitch date: awards
    announced AFTER the pitch (the pitch date's own announcements came out at
    5 p.m. and may have been known), and RPO/revenue as known then vs. now."""
    after = [r for r in rows if r["announcement_date"] > p["date"]]
    s = metrics.summarize(after)
    top = sorted((r for r in after if classify(r)[0] == "definite"),
                 key=lambda r: float(r["amount_usd"]), reverse=True)[:5]
    figures = []
    for key, label in (("rpo", "RPO (backlog)"), ("revenue", "Revenue, latest fiscal year")):
        series = fin.get(key, [])
        then, now = metrics.as_of(series, p["date"]), metrics.as_of(series, today)
        if then or now:
            figures.append({"label": label, "then": then, "now": now,
                            "changed": bool(then and now and then is not now)})
    return {"summary": s, "top": [present(r) for r in top], "figures": figures,
            "n_after": len(after)}


def _cut(text: str, n: int = 170) -> str:
    """First ~n characters, ending at a word boundary."""
    if len(text) <= n:
        return text
    return text[:text.rfind(" ", 0, n)] + "…"


def present(r: dict) -> dict:
    """Everything a template needs to show one award row."""
    cat, reason = classify(r)
    amount = float(r["amount_usd"]) if r["amount_usd"] else None
    shared = float(r["shared_amount_usd"]) if r["shared_amount_usd"] else None
    return {
        **r,
        "category": cat,
        "excluded_reason": reason,
        "amount_short": fmt_usd(amount) if amount is not None else "",
        "amount_exact": f"${amount:,.2f}".replace(".00", "") if amount is not None else "",
        "shared_short": fmt_usd(shared) if shared is not None else "",
        "branch_group": branch_group(r["branch"]),
        # summary shows the first part; the expanded view shows only the rest,
        # so the text isn't sent twice
        "snippet": _cut(r["text"]),
        "rest": r["text"][len(_cut(r["text"]).rstrip("…")):].lstrip(),
        # stable across builds (Python's hash() is randomized per run; crc32 is not)
        "anchor": f"a{r['announcement_date'].replace('-', '')}-{r['paragraph_index']}-"
                  f"{zlib.crc32(r['company'].encode()) % 10**6}",
        "price_url": price_link(r["ticker"], r["announcement_date"]) if r.get("ticker") else "",
        "flags": [x for x in r["review_reasons"].split("; ") if x],
        "mat": _materiality(r),
        "notes_list": [x for x in r["notes"].split("; ") if x],
    }


def _materiality(r: dict) -> dict:
    fin = FIN.get(r.get("ticker", ""), {})
    m = metrics.materiality(r, fin.get("rpo", []), fin.get("revenue", []))
    m["pct_rpo_s"], m["pct_rev_s"] = fmt_pct(m["pct_rpo"]), fmt_pct(m["pct_rev"])
    return m


def load_financials() -> tuple[dict, dict]:
    """(series by ticker, coverage by (ticker, metric)); empty if not built yet."""
    fin: dict[str, dict[str, list[dict]]] = defaultdict(lambda: {"rpo": [], "revenue": []})
    cov = {}
    if (DATA / "financials.csv").exists():
        for r in read_csv(DATA / "financials.csv"):
            fin[r["ticker"]][r["metric"]].append(r)
    if (DATA / "edgar_coverage.csv").exists():
        cov = {(c["ticker"], c["metric"]): c for c in read_csv(DATA / "edgar_coverage.csv")}
    return dict(fin), cov


# Plain facts a reader needs to read a company's figures correctly.
COMPANY_NOTES = {
    "GE": "GE spun off GE HealthCare (January 2023) and GE Vernova (April 2024). Figures filed "
          "before each spin-off include the businesses that later left, so the series jumps.",
    "LHX": "L3Harris's 10-K filed Feb 12, 2026 tags its fiscal 2025 revenue ($21.9B) to a "
           "3-month period (Oct 4, 2025 to Jan 2, 2026) instead of the full year, apparently a "
           "tagging error in the filing. Hangar doesn't correct filings, so fiscal 2025 revenue "
           "shows as missing here; it is in the 10-K itself.",
}


# ------------------------------------------------------------------ build

def build() -> None:
    awards = read_csv(DATA / "awards.csv")
    pages = read_csv(DATA / "pages.csv")
    coverage = read_csv(DATA / "coverage.csv")
    aliases = [a for a in read_csv(DATA / "ticker_aliases.csv")
               if a["pattern"] and not a["pattern"].startswith("#")]

    fin, fin_cov = load_financials()
    FIN.clear()
    FIN.update(fin)
    latest = max(r["announcement_date"] for r in awards)
    first = min(r["announcement_date"] for r in awards)
    since_12m = (date.fromisoformat(latest) - timedelta(days=365)).isoformat()
    built = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    by_ticker: dict[str, list[dict]] = defaultdict(list)
    for r in awards:
        if r["ticker"] and r["ticker_status"] == "public":
            by_ticker[r["ticker"]].append(r)
    parent = {a["ticker"]: a["parent"] for a in aliases if a["ticker"]}
    tickers = sorted(by_ticker, key=lambda t: (t not in CORE, CORE.index(t) if t in CORE else 0, t))
    tickers = [t for t in CORE if t not in tickers] + tickers   # core always present

    env = Environment(loader=FileSystemLoader(SRC / "templates"),
                      autoescape=select_autoescape(["html"]), trim_blocks=True, lstrip_blocks=True)
    env.filters["usd"] = fmt_usd
    css = (SRC / "static" / "style.css").read_bytes()
    common = dict(latest=latest, first=first, built=built, n_pages=len(pages),
                  goatcounter=os.environ.get("GOATCOUNTER_CODE", ""),
                  repo_url=os.environ.get("HANGAR_REPO_URL", ""),   # set once on GitHub
                  # cache-busting: the URL changes whenever the CSS changes
                  css_version=f"{zlib.crc32(css):08x}")

    if OUT.exists():
        shutil.rmtree(OUT)
    (OUT / "company").mkdir(parents=True)
    (OUT / "data").mkdir()
    shutil.copy(SRC / "static" / "style.css", OUT / "style.css")

    pitches_by_ticker = defaultdict(list)
    for p in pitch_lib.load():
        pitches_by_ticker[p["ticker"]].append(p)

    # --- one page per ticker -------------------------------------------
    summaries = {}
    for t in tickers:
        rows = sorted(by_ticker.get(t, []), key=lambda r: (r["announcement_date"], r["paragraph_index"]),
                      reverse=True)
        s12, sall = metrics.summarize(rows, since=since_12m), metrics.summarize(rows)
        summaries[t] = {"name": parent.get(t, t), "s12": s12, "sall": sall, "n": len(rows),
                        "last": rows[0]["announcement_date"] if rows else None}
        quarters = metrics.all_quarters(first, latest)
        qdata = metrics.quarterly_definite_by_branch(rows)
        years = defaultdict(list)
        for r in rows:
            years[r["announcement_date"][:4]].append(present(r))
        year_list = sorted(years.items(), reverse=True)
        # Older years get their own page so the main page stays light on phones.
        for y, yrows in year_list[1:]:
            _render(env, "year.html", OUT / "company" / f"{_slug(t)}-{y}.html", root="../",
                    ticker=t, info=summaries[t], year=y, rows=yrows, **common)
        entities = Counter(r["company"] for r in rows).most_common()
        rules = sorted({r["ticker_rule"] for r in rows})
        partial = metrics.quarter(latest) == quarters[-1]
        f = fin.get(t, {"rpo": [], "revenue": []})
        ratios = ratio_rows(f["rpo"], f["revenue"])
        financials = {
            "rpo": list(reversed(f["rpo"])), "revenue": list(reversed(f["revenue"])),
            "ratios": list(reversed(ratios)),
            "chart": financials_chart_svg(f["rpo"], f["revenue"]) if f["rpo"] or f["revenue"] else "",
            "ratio_chart": ratio_chart_svg(ratios) if ratios else "",
            "cov": {m: fin_cov.get((t, m)) for m in ("rpo", "revenue")},
            "note": COMPANY_NOTES.get(t, ""),
        }
        _render(env, "company.html", OUT / "company" / f"{_slug(t)}.html", root="../",
                ticker=t, info=summaries[t], years=year_list[:1],
                older=[(y, len(r)) for y, r in year_list[1:]], slug=_slug(t),
                chart=bar_chart_svg(quarters, qdata, partial_last=partial) if qdata else "",
                partial_quarter=quarters[-1] if partial else None,
                chart_rows=[(q, qdata.get(q, {})) for q in quarters],
                branches=BRANCH_ORDER, entities=entities, rules=rules,
                rule_rows=[a for a in aliases if a["ticker"] == t],
                csv_name=f"{_slug(t)}.csv", since_12m=since_12m, price_url=price_link(t),
                fin=financials, pitches=pitches_by_ticker.get(t, []), **common)
        _write_ticker_csv(OUT / "data" / f"{_slug(t)}.csv", rows)

    # --- pitches ---------------------------------------------------------
    # A tampered or backdated pitch stops the build (pipeline/pitches.py).
    pitch_list = pitch_lib.load()
    ledger = pitch_lib.read_ledger()
    errors = pitch_lib.check(pitch_list, ledger)
    if errors:
        raise SystemExit("pitch check failed:\n" + "\n".join(errors))
    pitch_lib.attach(pitch_list, ledger)
    today = datetime.now(timezone.utc).date().isoformat()
    (OUT / "pitches").mkdir()
    for p in pitch_list:
        p["html"] = pitch_lib.markdown(p["original"])
        for a in p["addenda"]:
            a["html"] = pitch_lib.markdown(a["body"])
        p["has_page"] = p["ticker"] in by_ticker or p["ticker"] in CORE
        p["since"] = since_pitch(p, by_ticker.get(p["ticker"], []), fin.get(p["ticker"], {}), today)
        p["price_url"] = price_link_since(p["ticker"], p["date"], today)
        _render(env, "pitch.html", OUT / "pitches" / f"{p['slug']}.html", root="../",
                p=p, today=today, latest_awards=latest, **common)
    _render(env, "pitches.html", OUT / "pitches.html", root="", pitches=pitch_list, **common)

    # --- home page -----------------------------------------------------
    recent = sorted((r for t in tickers for r in by_ticker.get(t, []) if classify(r)[0] != "excluded"),
                    key=lambda r: (r["announcement_date"], r["paragraph_index"]), reverse=True)[:30]
    _render(env, "index.html", OUT / "index.html", root="",
            core=[(t, summaries[t]) for t in CORE],
            others=[(t, summaries[t]) for t in tickers if t not in CORE],
            recent=[present(r) for r in recent], since_12m=since_12m, **common)

    # --- methodology ----------------------------------------------------
    missing = [c["date"] for c in coverage if c["status"] != "have"]
    kinds = Counter(r["kind"] for r in awards)
    flagged = sum(1 for r in awards if r["review_reasons"])
    _render(env, "methodology.html", OUT / "methodology.html", root="",
            missing=_group_dates(missing), n_missing=len(missing),
            n_business_days=len(coverage), n_records=len(awards), kinds=kinds,
            flagged=flagged, aliases=aliases,
            fin_cov=sorted(fin_cov.values(), key=lambda c: (
                CORE.index(c["ticker"]) if c["ticker"] in CORE else 99, c["metric"] != "rpo")),
            **common)
    print(f"site/ built: {len(tickers)} company pages, data through {latest}")


def _render(env, template: str, path: Path, **ctx) -> None:
    path.write_text(env.get_template(template).render(**ctx), encoding="utf-8")


def _slug(ticker: str) -> str:
    return ticker.replace(".", "-")


def _write_ticker_csv(path: Path, rows: list[dict]) -> None:
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=CSV_COLUMNS + ["counted_as"], extrasaction="ignore")
        w.writeheader()
        for r in rows:
            cat, reason = classify(r)
            w.writerow({**r, "counted_as": cat + (f" ({reason})" if reason else "")})


def _group_dates(dates: list[str]) -> list[tuple[str, str, int]]:
    """Collapse consecutive business days into ranges: [(start, end, n_days)]."""
    out: list[list] = []
    for d in sorted(dates):
        dd = date.fromisoformat(d)
        if out and (dd - date.fromisoformat(out[-1][1])).days <= 4:   # bridges weekends/holidays
            out[-1][1] = d
            out[-1][2] += 1
        else:
            out.append([d, d, 1])
    return [tuple(x) for x in out]


if __name__ == "__main__":
    build()
