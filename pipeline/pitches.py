"""Timestamped stock pitches: written once, never edited, publicly witnessed.

    python -m pipeline.pitches check     # validate pitches/ against the ledger (no writes)
    python -m pipeline.pitches record    # add new pitches/updates to data/pitch_ledger.csv
    python -m pipeline.pitches archive   # ask the Internet Archive to save new pitch pages

A pitch is one Markdown file in pitches/, named <date>-<TICKER>-<long|short>.md,
with a TOML header between +++ lines (see pitches/_TEMPLATE.md). Later notes are
appended as "## Update YYYY-MM-DD" or "## Closed YYYY-MM-DD" sections.

Why you can trust the dates (say this in an interview):
  1. Git/GitHub record when each file was pushed.
  2. Each part (the original pitch, then every update) gets a SHA-256
     fingerprint, stored in data/pitch_ledger.csv the first time the daily job
     sees it. Change one character and the fingerprint no longer matches, the
     tests fail and nothing is published. Parts can be added, never edited or
     removed.
  3. After the page goes live, the Internet Archive is asked to save a copy:
     a dated witness neither Hangar nor its author controls.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import tomllib
from datetime import date, datetime, timedelta, timezone
from html import escape
from pathlib import Path
from urllib.parse import quote

ROOT = Path(__file__).resolve().parent.parent
PITCHES = ROOT / "pitches"
LEDGER = ROOT / "data" / "pitch_ledger.csv"
LEDGER_COLUMNS = ["file", "part", "sha256", "first_seen", "archive_url", "archived_at"]

REQUIRED = {"ticker": str, "direction": str, "date": date, "horizon": str,
            "one_line": str, "position": str}
DIRECTIONS = {"long", "short"}
POSITIONS = {"none", "long", "short"}
MAX_BACKDATE_DAYS = 3          # a pitch's own date may be at most this much before it was first recorded
NAME_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})-([A-Z0-9-]+)-(long|short)\.md$")
ADDENDUM_RE = re.compile(r"^## (Update|Closed) (\d{4}-\d{2}-\d{2})\s*$", re.M)


class PitchError(ValueError):
    pass


# --------------------------------------------------------------- reading

def normalize(text: str) -> str:
    """What gets fingerprinted: Windows/Mac line endings and trailing spaces
    don't count as edits; everything else does."""
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    return "\n".join(line.rstrip() for line in lines).strip() + "\n"


def sha256(text: str) -> str:
    return hashlib.sha256(normalize(text).encode("utf-8")).hexdigest()


def parse(name: str, text: str) -> dict:
    """One pitch file -> dict with header fields, the original part and addenda."""
    m = NAME_RE.match(name)
    if not m:
        raise PitchError(f"{name}: file name must look like 2026-11-15-KTOS-long.md")
    text = text.replace("\r\n", "\n")
    parts = text.split("+++")
    if not text.startswith("+++") or len(parts) < 3:
        raise PitchError(f"{name}: must start with a +++ header block")
    header_src, body = parts[1], "+++".join(parts[2:])
    try:
        header = tomllib.loads(header_src)
    except tomllib.TOMLDecodeError as e:
        raise PitchError(f"{name}: header is not valid TOML ({e})") from None
    for key, typ in REQUIRED.items():
        if key not in header:
            raise PitchError(f"{name}: header is missing '{key}'")
        if not isinstance(header[key], typ) or isinstance(header[key], datetime):
            raise PitchError(f"{name}: '{key}' has the wrong type (see pitches/_TEMPLATE.md)")
    if header["direction"] not in DIRECTIONS:
        raise PitchError(f"{name}: direction must be long or short")
    if header["position"] not in POSITIONS:
        raise PitchError(f"{name}: position must be none, long or short")
    file_date, file_ticker, file_dir = m.groups()
    if (header["date"].isoformat(), header["ticker"].replace(".", "-"), header["direction"]) != \
            (file_date, file_ticker, file_dir):
        raise PitchError(f"{name}: file name must match the header's date, ticker and direction")

    # Split the body at "## Update/Closed <date>" headings.
    marks = list(ADDENDUM_RE.finditer(body))
    original = body[:marks[0].start()] if marks else body
    if not original.strip():
        raise PitchError(f"{name}: the pitch has no text")
    addenda = []
    for i, mk in enumerate(marks):
        end = marks[i + 1].start() if i + 1 < len(marks) else len(body)
        kind, d = mk.group(1), mk.group(2)
        if addenda and d < addenda[-1]["date"] or d < header["date"].isoformat():
            raise PitchError(f"{name}: '{mk.group(0).strip()}' is out of date order")
        if addenda and addenda[-1]["kind"] == "Closed":
            raise PitchError(f"{name}: nothing may follow a Closed note")
        content = body[mk.start():end]
        addenda.append({"kind": kind, "date": d, "part": f"{kind.lower()} {d}",
                        "body": body[mk.end():end], "sha256": sha256(content)})
    if len({a["part"] for a in addenda}) != len(addenda):
        raise PitchError(f"{name}: two notes with the same heading and date")
    return {
        "file": name, "slug": name[:-3], **header, "date": header["date"].isoformat(),
        "original": original, "sha256": sha256(f"+++{header_src}+++{original}"),
        "addenda": addenda, "status": "closed" if addenda and addenda[-1]["kind"] == "Closed" else "open",
    }


def load(folder: Path | None = None) -> list[dict]:
    """Every pitch in the folder, newest first. Files starting with _ (the
    template) and README.md are skipped; any other .md must be a valid pitch."""
    out = []
    folder = folder or PITCHES
    for p in sorted(folder.glob("*.md")) if folder.exists() else []:
        if p.name.startswith("_") or p.name.lower() == "readme.md":
            continue
        out.append(parse(p.name, p.read_text(encoding="utf-8")))
    return sorted(out, key=lambda x: (x["date"], x["file"]), reverse=True)


def parts(pitch: dict) -> list[tuple[str, str]]:
    """[(part name, fingerprint)] in order: the original, then each note."""
    return [("original", pitch["sha256"])] + [(a["part"], a["sha256"]) for a in pitch["addenda"]]


# --------------------------------------------------------------- the ledger

def read_ledger(path: Path | None = None) -> list[dict]:
    path = path or LEDGER
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def write_ledger(rows: list[dict], path: Path | None = None) -> None:
    path = path or LEDGER
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    with tmp.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=LEDGER_COLUMNS)
        w.writeheader()
        w.writerows(rows)
    tmp.replace(path)


def check(pitches: list[dict], ledger: list[dict]) -> list[str]:
    """Every way a published pitch could have been tampered with. Empty = OK."""
    errors = []
    by_file = {p["file"]: p for p in pitches}
    for row in ledger:
        p = by_file.get(row["file"])
        if p is None:
            errors.append(f"{row['file']}: published pitch was deleted or renamed")
            continue
        current = dict(parts(p))
        if row["part"] not in current:
            errors.append(f"{row['file']}: published part '{row['part']}' was removed")
        elif current[row["part"]] != row["sha256"]:
            errors.append(f"{row['file']}: published part '{row['part']}' was edited "
                          f"(fingerprint {row['sha256'][:12]}… no longer matches). "
                          f"Add a '## Update YYYY-MM-DD' note instead.")
    return errors


def record(pitches: list[dict], ledger: list[dict], today: str) -> list[dict]:
    """Ledger with every not-yet-recorded part added (first_seen = today).
    Refuses tampering and backdating."""
    errors = check(pitches, ledger)
    known = {(r["file"], r["part"]) for r in ledger}
    new = []
    for p in pitches:
        for part, digest in parts(p):
            if (p["file"], part) in known:
                continue
            claimed = p["date"] if part == "original" else part.split(" ", 1)[1]
            earliest = (date.fromisoformat(today) - timedelta(days=MAX_BACKDATE_DAYS)).isoformat()
            if claimed < earliest:
                errors.append(f"{p['file']}: '{part}' is dated {claimed} but is first published "
                              f"{today}; dates may be at most {MAX_BACKDATE_DAYS} days earlier")
            if claimed > today:
                errors.append(f"{p['file']}: '{part}' is dated in the future ({claimed})")
            new.append({"file": p["file"], "part": part, "sha256": digest, "first_seen": today,
                        "archive_url": "", "archived_at": ""})
    if errors:
        raise PitchError("\n".join(errors))
    return ledger + new


def attach(pitches: list[dict], ledger: list[dict]) -> None:
    """Add ledger facts (first published, archive link) to each part, for the site."""
    by_key = {(r["file"], r["part"]): r for r in ledger}
    for p in pitches:
        for item, part in [(p, "original")] + [(a, a["part"]) for a in p["addenda"]]:
            r = by_key.get((p["file"], part), {})
            item["first_seen"] = r.get("first_seen", "")
            item["archive_url"] = r.get("archive_url", "")


# --------------------------------------------------------------- Markdown subset

_INLINE = [
    (re.compile(r"`([^`]+)`"), r"<code>\1</code>"),
    (re.compile(r"\*\*(.+?)\*\*"), r"<strong>\1</strong>"),
    (re.compile(r"(?<![\w*])\*(?!\s)(.+?)(?<!\s)\*(?![\w*])"), r"<em>\1</em>"),
    (re.compile(r"\[([^\]]+)\]\((https?://[^\s)]+)\)"), r'<a href="\2" rel="noopener">\1</a>'),
]


def inline(text: str) -> str:
    """Escape HTML first (so a pitch can't inject markup), then apply
    `code`, **bold**, *italic* and [links](https://...). Only http(s) links."""
    out = escape(text, quote=False)
    for pattern, repl in _INLINE:
        out = pattern.sub(repl, out)
    return out


def markdown(text: str) -> str:
    """A small, tested Markdown subset: ## and ### headings, paragraphs,
    - / * bullet lists, 1. numbered lists, > quotes, and the inline styles
    above. Anything else is shown as plain text, never dropped."""
    html, para, items, kind = [], [], [], None

    def flush():
        nonlocal para, items, kind
        if para:
            html.append(f"<p>{inline(' '.join(para))}</p>")
        if items:
            html.append(f"<{kind}>" + "".join(f"<li>{inline(i)}</li>" for i in items) + f"</{kind}>")
        para, items, kind = [], [], None

    for raw in text.replace("\r\n", "\n").split("\n"):
        line = raw.strip()
        bullet = re.match(r"^[-*] +(.*)", line)
        number = re.match(r"^\d+[.)] +(.*)", line)
        heading = re.match(r"^(#{1,6}) +(.*)", line)
        if not line:
            flush()
        elif heading:
            flush()
            level = min(max(len(heading.group(1)) + 1, 3), 4)   # ## -> h3, ### -> h4 (page has h1/h2)
            html.append(f"<h{level}>{inline(heading.group(2))}</h{level}>")
        elif bullet or number:
            want = "ul" if bullet else "ol"
            if para or (kind and kind != want):
                flush()
            kind = want
            items.append((bullet or number).group(1))
        elif line.startswith(">"):
            flush()
            html.append(f"<blockquote>{inline(line.lstrip('> '))}</blockquote>")
        elif items:                      # continuation of the last list item
            items[-1] += " " + line
        else:
            para.append(line)
    flush()
    return "\n".join(html)


# --------------------------------------------------------------- Internet Archive witness

def page_url(site_url: str, pitch: dict) -> str:
    return site_url.rstrip("/") + f"/pitches/{pitch['slug']}.html"


def archive(pitches: list[dict], ledger: list[dict], site_url: str) -> list[dict]:
    """For each pitch with an unarchived part: ask the Internet Archive to save
    the live page, then look up the snapshot and record it if it is at least
    as new as the part. One save + one lookup per pitch, 4+ seconds apart
    (pipeline/http.py). Failures are left for the next run."""
    from . import http
    by_file = {p["file"]: p for p in pitches}
    todo = sorted({r["file"] for r in ledger if not r["archive_url"] and r["file"] in by_file})
    for f in todo:
        url = page_url(site_url, by_file[f])
        try:
            http.get("https://web.archive.org/save/" + url, timeout=120)
            info = json.loads(http.get("https://archive.org/wayback/available?url=" + quote(url, safe="")))
        except Exception as e:                                  # retry next run
            print(f"  {f}: archive request failed ({e})")
            continue
        snap = info.get("archived_snapshots", {}).get("closest") or {}
        ts = snap.get("timestamp", "")                          # YYYYMMDDhhmmss
        if not snap.get("available") or not ts:
            print(f"  {f}: no snapshot yet")
            continue
        snap_day = f"{ts[:4]}-{ts[4:6]}-{ts[6:8]}"
        for r in ledger:
            if r["file"] == f and not r["archive_url"] and snap_day >= r["first_seen"]:
                r["archive_url"] = snap["url"].replace("http://", "https://", 1)
                r["archived_at"] = snap_day
        print(f"  {f}: archived {snap['url']}")
    return ledger


# --------------------------------------------------------------- command line

def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["check", "record", "archive"])
    a = ap.parse_args()
    pitches, ledger = load(), read_ledger()
    if a.cmd == "check":
        errors = check(pitches, ledger)
        if errors:
            raise SystemExit("\n".join(errors))
        print(f"{len(pitches)} pitches OK")
    elif a.cmd == "record":
        today = datetime.now(timezone.utc).date().isoformat()
        new = record(pitches, ledger, today)
        write_ledger(new)
        print(f"{len(new) - len(ledger)} new part(s) recorded; {len(pitches)} pitches")
    elif a.cmd == "archive":
        site = os.environ.get("HANGAR_SITE_URL", "").strip()
        if not site:
            raise SystemExit("HANGAR_SITE_URL is not set")
        write_ledger(archive(pitches, ledger, site))


if __name__ == "__main__":
    main()
