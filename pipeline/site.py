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

from . import metrics
from .metrics import BRANCH_ORDER, branch_group, classify, fmt_usd

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


# ------------------------------------------------------------------ rows

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
        "notes_list": [x for x in r["notes"].split("; ") if x],
    }


# ------------------------------------------------------------------ build

def build() -> None:
    awards = read_csv(DATA / "awards.csv")
    pages = read_csv(DATA / "pages.csv")
    coverage = read_csv(DATA / "coverage.csv")
    aliases = [a for a in read_csv(DATA / "ticker_aliases.csv")
               if a["pattern"] and not a["pattern"].startswith("#")]

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
        _render(env, "company.html", OUT / "company" / f"{_slug(t)}.html", root="../",
                ticker=t, info=summaries[t], years=year_list[:1],
                older=[(y, len(r)) for y, r in year_list[1:]], slug=_slug(t),
                chart=bar_chart_svg(quarters, qdata, partial_last=partial) if qdata else "",
                partial_quarter=quarters[-1] if partial else None,
                chart_rows=[(q, qdata.get(q, {})) for q in quarters],
                branches=BRANCH_ORDER, entities=entities, rules=rules,
                rule_rows=[a for a in aliases if a["ticker"] == t],
                csv_name=f"{_slug(t)}.csv", since_12m=since_12m, price_url=price_link(t), **common)
        _write_ticker_csv(OUT / "data" / f"{_slug(t)}.csv", rows)

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
            flagged=flagged, aliases=aliases, **common)
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
