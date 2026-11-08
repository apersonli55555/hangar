"""Pipeline entry point.

    python -m pipeline.run backfill --years 2024 2025 2026   # one-time history
    python -m pipeline.run daily                              # what GitHub Actions runs
    python -m pipeline.run build                              # re-parse cached pages -> CSVs

Two stages, kept separate on purpose:
  FETCH  downloads raw pages into data/raw/ (slow, polite, only new pages)
  BUILD  parses every cached page and writes data/awards.csv (fast, offline)
So a parser fix never requires downloading anything again.
"""
from __future__ import annotations

import argparse
import csv
import gzip
import re
import sys
from dataclasses import asdict, dataclass, fields
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from . import sources, tickers
from .parse import parse_page

DATA = Path("data")
RAW = DATA / "raw"
MANIFEST = DATA / "pages.csv"


# --------------------------------------------------------------- manifest

@dataclass
class PageRow:
    article_id: str
    slug_date: str
    source_url: str
    archive_url: str = ""
    status: str = "pending"        # pending | fetched | waiting_for_archive | error
    fetched_at: str = ""
    error: str = ""


def load_manifest() -> dict[str, PageRow]:
    if not MANIFEST.exists():
        return {}
    with MANIFEST.open(newline="", encoding="utf-8") as f:
        return {r["article_id"]: PageRow(**r) for r in csv.DictReader(f)}


def save_manifest(rows: dict[str, PageRow]) -> None:
    MANIFEST.parent.mkdir(parents=True, exist_ok=True)
    tmp = MANIFEST.with_suffix(".tmp")
    with tmp.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=[x.name for x in fields(PageRow)])
        w.writeheader()
        for r in sorted(rows.values(), key=lambda r: (r.slug_date, r.article_id)):
            w.writerow(asdict(r))
    tmp.replace(MANIFEST)  # atomic: a crash never leaves a half-written file


def raw_path(row: PageRow) -> Path:
    return RAW / row.slug_date[:4] / f"{row.article_id}.html.gz"


# ------------------------------------------------------------------ fetch

def page_looks_complete(html: str) -> str | None:
    """Return a reason if the saved copy looks cut off, else None."""
    if "</html>" not in html[-5000:].lower():
        return "page has no closing </html> tag (capture may be truncated)"
    if 'class="body"' not in html:
        return "award text container not found"
    return None


def fetch(rows: dict[str, PageRow], limit: int | None = None) -> None:
    todo = [r for r in rows.values() if r.status != "fetched"]
    todo.sort(key=lambda r: r.slug_date, reverse=True)  # newest first
    if limit:
        todo = todo[:limit]
    print(f"{len(todo)} pages to fetch", flush=True)
    for i, row in enumerate(todo, 1):
        try:
            if not row.archive_url:
                row.archive_url = sources.archived_copy(row.source_url) or ""
                if not row.archive_url:
                    row.status = "waiting_for_archive"
                    continue
            html = sources.http.get(sources.raw_archive_url(row.archive_url)).decode("utf-8", "replace")
            problem = page_looks_complete(html)
            if problem:
                # The archive often holds several copies; try up to 3 others.
                for alt in [u for u in sources.list_captures(row.source_url) if u != row.archive_url][:3]:
                    alt_html = sources.http.get(sources.raw_archive_url(alt)).decode("utf-8", "replace")
                    if page_looks_complete(alt_html) is None:
                        html, problem, row.archive_url = alt_html, None, alt
                        break
            if problem:
                row.status, row.error = "error", problem
                continue
            path = raw_path(row)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(gzip.compress(html.encode("utf-8")))
            row.status, row.error = "fetched", ""
            row.fetched_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        except Exception as e:  # keep going; the error is recorded and retried next run
            row.status, row.error = "error", f"{type(e).__name__}: {e}"[:200]
        finally:
            if i % 10 == 0 or i == len(todo):
                save_manifest(rows)
                print(f"  {i}/{len(todo)}  last={row.slug_date} {row.status}", flush=True)
    save_manifest(rows)


def discover_backfill(rows: dict[str, PageRow], years: list[int]) -> None:
    for p in sources.list_archived_pages(years):
        if p.article_id not in rows:
            rows[p.article_id] = PageRow(p.article_id, p.slug_date, p.source_url, p.archive_url)


def discover_daily(rows: dict[str, PageRow]) -> None:
    for ann in sources.list_announcements(max_items=20):
        m = re.search(r"/Article/(\d+)/", ann.source_url)
        if not m or m.group(1) in rows:
            continue
        d = sources._SLUG_RE.search(ann.source_url)
        slug_date = sources._slug_date(d.group(2), d.group(3), d.group(4)) if d else None
        rows[m.group(1)] = PageRow(m.group(1), slug_date or "", ann.source_url)


# ------------------------------------------------------------------ build

def us_federal_holidays(year: int) -> set[date]:
    """Observed federal holidays (days with no contract announcements)."""
    def nth(weekday, n, month):            # n-th weekday of a month
        d = date(year, month, 1)
        d += timedelta((weekday - d.weekday()) % 7)
        return d + timedelta(weeks=n - 1)

    def last(weekday, month):
        d = date(year, month + 1, 1) - timedelta(1) if month < 12 else date(year, 12, 31)
        return d - timedelta((d.weekday() - weekday) % 7)

    def observed(d):
        return d - timedelta(1) if d.weekday() == 5 else d + timedelta(1) if d.weekday() == 6 else d

    return {observed(date(year, 1, 1)), nth(0, 3, 1), nth(0, 3, 2), last(0, 5),
            observed(date(year, 6, 19)), observed(date(year, 7, 4)), nth(0, 1, 9),
            nth(0, 2, 10), observed(date(year, 11, 11)), nth(3, 4, 11),
            observed(date(year, 12, 25))}


def build(rows: dict[str, PageRow], today: date | None = None) -> None:
    awards, page_report = [], []
    for row in sorted(rows.values(), key=lambda r: r.slug_date):
        if row.status != "fetched":
            page_report.append({**asdict(row), "records": 0, "problem": row.error or row.status})
            continue
        html = gzip.decompress(raw_path(row).read_bytes()).decode("utf-8", "replace")
        try:
            page = parse_page(html, source_url=row.source_url, archive_url=row.archive_url)
        except ValueError as e:
            page_report.append({**asdict(row), "records": 0, "problem": str(e)})
            continue
        problem = ""
        if page.announcement_date.isoformat() != row.slug_date:
            problem = f"title date {page.announcement_date} != URL date {row.slug_date}"
        awards += page.awards
        page_report.append({**asdict(row), "records": len(page.awards), "problem": problem})

    mark_repeats(awards)
    apply_reviews(awards)

    mapper = tickers.TickerMapper.load()
    out_rows = []
    for a in awards:
        r = a.to_row()
        m = mapper.match(a.company, a.award_date or a.announcement_date) if a.company else None
        r["ticker"] = m.ticker if m else ""
        r["ticker_parent"] = m.parent if m else ""
        r["ticker_rule"] = m.pattern if m else ""
        r["ticker_status"] = m.status if m else ("" if a.kind in ("correction", "repeat") else "unmapped")
        out_rows.append(r)

    _write_csv(DATA / "awards.csv", out_rows)
    _write_csv(DATA / "page_report.csv", page_report)
    _write_coverage(rows, today or date.today())
    _write_unmapped(out_rows)
    print(f"{len(out_rows)} records from {sum(1 for p in page_report if p['records'])} pages")


def mark_repeats(awards: list) -> int:
    """The same paragraph sometimes appears on more than one day's page (e.g.
    the Sept. 29, 2026 page repeats 55 paragraphs from Sept. 28). Keep the
    first appearance; later copies become kind='repeat' so they are never
    counted twice. `awards` must be in page-date order."""
    first_seen: dict[tuple[str, str], str] = {}
    n = 0
    for a in awards:
        if a.kind in ("correction", "repeat"):
            continue
        key = (a.company or "", " ".join(a.text.split()))
        if key in first_seen:
            a.kind = "repeat"
            a.notes.append(f"repeat of text first announced on {first_seen[key]} (not counted again)")
            n += 1
        else:
            first_seen[key] = a.announcement_date
    return n


REVIEWED = Path("data/reviewed.csv")


def apply_reviews(awards: list, path: Path | None = None) -> int:
    """data/reviewed.csv records a human's check of one record against the source:
        announcement_date,company,amount_usd,verdict,reviewed_on,comment
    verdict "confirmed" clears the review flags (the number is exactly what the
    source says). We never edit a number here: if the SOURCE is wrong, the
    record keeps its flag and the comment explains why."""
    path = path or REVIEWED
    if not path.exists():
        return 0
    with path.open(newline="", encoding="utf-8") as f:
        reviews = {(r["announcement_date"], r["company"], float(r["amount_usd"] or 0)): r
                   for r in csv.DictReader(f)}
    n = 0
    for a in awards:
        rv = reviews.get((a.announcement_date, a.company, float(a.amount_usd or 0)))
        if not rv:
            continue
        if rv["verdict"] == "confirmed":
            a.review_reasons = []
        a.notes.append(f"human review {rv['reviewed_on']}: {rv['verdict']}"
                       + (f" ({rv['comment']})" if rv["comment"] else ""))
        n += 1
    return n


def _write_csv(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        if not rows:
            return
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)


def _write_coverage(rows: dict[str, PageRow], end: date) -> None:
    """Every business day since the first page: do we have its announcement?"""
    have = {r.slug_date for r in rows.values() if r.status == "fetched"}
    known = {r.slug_date: r.status for r in rows.values()}
    if not known:
        return
    start = date.fromisoformat(min(known))
    holidays = set().union(*(us_federal_holidays(y) for y in range(start.year, end.year + 1)))
    out, d = [], start
    while d <= end:
        if d.weekday() < 5 and d not in holidays:
            iso = d.isoformat()
            out.append({"date": iso, "status": "have" if iso in have else known.get(iso, "missing")})
        d += timedelta(1)
    _write_csv(DATA / "coverage.csv", out)


def _write_unmapped(rows: list[dict]) -> None:
    """Companies without a ticker, biggest dollar totals first, for review."""
    agg: dict[str, dict] = {}
    for r in rows:
        if r["ticker_status"] != "unmapped":
            continue
        a = agg.setdefault(r["company"], {"company": r["company"], "records": 0, "amount_usd": 0.0})
        a["records"] += 1
        a["amount_usd"] += float(r["amount_usd"] or 0)
    _write_csv(DATA / "unmapped_companies.csv",
               sorted(agg.values(), key=lambda a: -a["amount_usd"]))


# -------------------------------------------------------------------- cli

def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("command", choices=["backfill", "daily", "build"])
    ap.add_argument("--years", type=int, nargs="*", default=[])
    ap.add_argument("--limit", type=int, default=None)
    args = ap.parse_args(argv)

    rows = load_manifest()
    if args.command == "backfill":
        discover_backfill(rows, args.years)
        save_manifest(rows)
        fetch(rows, args.limit)
    elif args.command == "daily":
        discover_daily(rows)
        fetch(rows, args.limit)
    build(rows)


if __name__ == "__main__":
    sys.exit(main())
