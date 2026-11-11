"""Score each pitch against the market, from prices the author typed in.

Hangar's code never downloads a price (the free price services forbid public
display; see the methodology page). Instead the author looks up closing prices
by hand and records them in data/pitch_marks.csv:

    date,ticker,close_usd,source,source_url
    2026-11-06,KTOS,35.10,Yahoo Finance,
    2026-11-06,SPY,735.29,Yahoo Finance,

A "mark" is one closing price on one date. SPY (an S&P 500 fund) is the
benchmark. This module only does arithmetic on those marks, and the site labels
every result as a calculation:

    stock return   = close / entry close - 1
    pitch return   = stock return for a long, minus the stock return for a short
    SPY return     = SPY close / SPY entry close - 1
    vs S&P 500     = pitch return - SPY return      (in percentage points)

Price return only: dividends are ignored for both the stock and SPY, and a
short ignores borrowing costs.
"""
from __future__ import annotations

import csv
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MARKS = ROOT / "data" / "pitch_marks.csv"
COLUMNS = ["date", "ticker", "close_usd", "source", "source_url"]
BENCHMARK = "SPY"
MAX_ENTRY_GAP_DAYS = 5      # the entry mark may be at most this long before the pitch date


class MarksError(ValueError):
    pass


def load_marks(path: Path | None = None, today: date | None = None) -> list[dict]:
    """Read and validate the marks file. Any bad row raises MarksError with
    every problem listed, so a typo stops the build instead of publishing a
    wrong return."""
    path = path or MARKS
    if not path.exists():
        return []
    today = today or date.today()
    with path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        if reader.fieldnames != COLUMNS:
            raise MarksError(f"{path.name}: header must be exactly {','.join(COLUMNS)}")
        rows = list(reader)
    errors, seen, out = [], set(), []
    for n, r in enumerate(rows, start=2):               # line 1 is the header
        where = f"{path.name} line {n}"
        try:
            d = date.fromisoformat(r["date"])
        except ValueError:
            errors.append(f"{where}: date must look like 2026-12-04")
            continue
        if d > today:
            errors.append(f"{where}: {r['date']} is in the future")
        if d.weekday() >= 5:
            errors.append(f"{where}: {r['date']} is a weekend; markets were closed")
        ticker = r["ticker"].strip().upper()
        if not ticker:
            errors.append(f"{where}: ticker is empty")
        try:
            close = float(r["close_usd"])
        except ValueError:
            errors.append(f"{where}: close_usd must be a number like 35.10")
            continue
        if close <= 0:
            errors.append(f"{where}: close_usd must be above zero")
        if not r["source"].strip():
            errors.append(f"{where}: say where you looked the price up (source)")
        url = r["source_url"].strip()
        if url and not url.startswith("https://"):
            errors.append(f"{where}: source_url must start with https:// (or be empty)")
        if (r["date"], ticker) in seen:
            errors.append(f"{where}: second price for {ticker} on {r['date']}")
        seen.add((r["date"], ticker))
        out.append({"date": r["date"], "ticker": ticker, "close": close,
                    "source": r["source"].strip(), "source_url": url})
    if errors:
        raise MarksError("\n".join(errors))
    return out


def score(pitch: dict, marks: list[dict]) -> dict:
    """Returns for one pitch. Result keys:
      status   "scored" | "no entry price" (with `reason`)
      entry    {date, close, spy_close, source, ...}
      rows     one per later date that has BOTH a stock and a SPY mark, oldest first
      latest   the last row, or None if no later price has been recorded yet
    """
    ticker, start = pitch["ticker"], pitch["date"]
    closed = next((a["date"] for a in pitch["addenda"] if a["kind"] == "Closed"), None)
    by = {(m["date"], m["ticker"]): m for m in marks}
    mine = sorted(m["date"] for m in marks if m["ticker"] == ticker)

    # Entry = the last close on or before the pitch date (a pitch written at
    # the weekend uses Friday's close), and SPY must have a mark the same day.
    earliest = (date.fromisoformat(start) - timedelta(days=MAX_ENTRY_GAP_DAYS)).isoformat()
    entry_dates = [d for d in mine if earliest <= d <= start]
    if not entry_dates:
        return {"status": "no entry price", "reason": f"no {ticker} price recorded for the pitch date",
                "rows": [], "latest": None}
    entry_date = entry_dates[-1]
    spy_entry = by.get((entry_date, BENCHMARK))
    if spy_entry is None:
        return {"status": "no entry price", "reason": f"no {BENCHMARK} price recorded for {entry_date}",
                "rows": [], "latest": None}
    entry = by[(entry_date, ticker)]
    sign = 1 if pitch["direction"] == "long" else -1

    rows = []
    for d in mine:
        if d <= entry_date or (closed and d > closed):   # a closed pitch stops being scored
            continue
        spy = by.get((d, BENCHMARK))
        if spy is None:                                  # can't compare without the benchmark
            continue
        m = by[(d, ticker)]
        pitch_ret = sign * (m["close"] / entry["close"] - 1)
        spy_ret = spy["close"] / spy_entry["close"] - 1
        rows.append({"date": d, "close": m["close"], "spy_close": spy["close"],
                     "source": m["source"], "source_url": m["source_url"],
                     "pitch_return": pitch_ret, "spy_return": spy_ret,
                     "vs_spy": pitch_ret - spy_ret})
    return {"status": "scored", "closed": closed,
            "entry": {"date": entry_date, "close": entry["close"], "spy_close": spy_entry["close"],
                      "source": entry["source"], "source_url": entry["source_url"]},
            "rows": rows, "latest": rows[-1] if rows else None}


def pct(x: float) -> str:
    """0.1234 -> '+12.3%'. A true zero has no sign."""
    return "0.0%" if abs(x) < 0.0005 else f"{x * 100:+.1f}%"


def points(x: float) -> str:
    """Difference between two percentages, in percentage points."""
    return "0.0 pts" if abs(x) < 0.0005 else f"{x * 100:+.1f} pts"
