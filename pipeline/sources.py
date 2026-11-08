"""Where announcement pages come from.

1. war.gov's RSS feed lists the daily "Contracts for <date>" pages. The feed is
   open to automated readers; the pages themselves are not (they return 403).
2. For each page we read the Internet Archive's saved copy. Every record we
   store keeps BOTH links: the official war.gov URL and the archive copy.
"""
from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import date
from urllib.parse import quote, urlencode

from . import http

RSS_URL = ("https://www.war.gov/DesktopModules/ArticleCS/RSS.ashx"
           "?ContentType=400&Site=945&max={max}")


@dataclass
class Announcement:
    title: str          # e.g. "Contracts for Nov. 6, 2026"
    source_url: str     # official war.gov page
    published: str      # RSS pubDate (RFC 822, GMT)


def list_announcements(max_items: int = 20) -> list[Announcement]:
    root = ET.fromstring(http.get(RSS_URL.format(max=max_items)))
    out = []
    for item in root.iter("item"):
        out.append(Announcement(
            title=(item.findtext("title") or "").strip(),
            source_url=(item.findtext("link") or "").strip(),
            published=(item.findtext("pubDate") or "").strip(),
        ))
    return out


def archived_copy(source_url: str) -> str | None:
    """Return the archive URL of the saved copy, or None if none exists yet."""
    api = "https://archive.org/wayback/available?url=" + quote(source_url, safe="")
    data = json.loads(http.get(api))
    snap = data.get("archived_snapshots", {}).get("closest")
    if not snap or snap.get("status") != "200":
        return None
    return snap["url"].replace("http://", "https://", 1)


CDX_URL = "https://web.archive.org/cdx/search/cdx"
# Daily pages look like .../Contract/Article/4644106/contracts-for-nov-6-2026/
_SLUG_RE = re.compile(r"/Article/(\d+)/contracts-for-([a-z]+)-(\d{1,2})-(\d{4})/?$", re.I)


@dataclass
class ArchivedPage:
    article_id: str
    slug_date: str        # date written in the URL, ISO format
    source_url: str       # canonical war.gov URL
    archive_url: str      # https://web.archive.org/web/<timestamp>/<original>


def _slug_date(month: str, day: str, year: str) -> str | None:
    months = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"]
    key = month[:3].lower()
    if key not in months:
        return None
    try:
        return date(int(year), months.index(key) + 1, int(day)).isoformat()
    except ValueError:
        return None


def list_archived_pages(years: list[int]) -> list[ArchivedPage]:
    """Ask the Internet Archive's index (CDX API) for every saved daily
    contracts page in the given years, under both the old defense.gov and new
    war.gov addresses. One entry per article id; the most recent capture wins."""
    found: dict[str, ArchivedPage] = {}
    for host in ("www.war.gov", "www.defense.gov"):
        for year in years:
            params = {
                "url": f"{host}/News/Contracts/Contract/Article/",
                "matchType": "prefix", "output": "json",
                "fl": "timestamp,original", "filter": ["statuscode:200",
                                                       f"original:.*contracts-for-.*-{year}/?$"],
            }
            rows = json.loads(http.get(CDX_URL + "?" + urlencode(params, doseq=True), timeout=180) or b"[]")
            for timestamp, original in rows[1:]:  # first row is the header
                m = _SLUG_RE.search(original)
                if not m:
                    continue
                d = _slug_date(m.group(2), m.group(3), m.group(4))
                if d is None:
                    continue
                aid = m.group(1)
                canonical = f"https://www.war.gov/News/Contracts/Contract/Article/{aid}/{m.group(0).split('/')[3]}/"
                page = ArchivedPage(aid, d, canonical,
                                    f"https://web.archive.org/web/{timestamp}/{original}")
                prev = found.get(aid)
                if prev is None or page.archive_url > prev.archive_url:
                    found[aid] = page
    return sorted(found.values(), key=lambda p: p.slug_date)


def list_captures(source_url: str, limit: int = 5) -> list[str]:
    """All successful archive captures of one page, newest first (used when the
    first copy we tried turned out truncated or empty)."""
    params = {"url": source_url.replace("https://", ""), "output": "json",
              "fl": "timestamp,original", "filter": "statuscode:200"}
    rows = json.loads(http.get(CDX_URL + "?" + urlencode(params), timeout=120) or b"[]")[1:]
    rows.sort(reverse=True)
    return [f"https://web.archive.org/web/{ts}/{orig}" for ts, orig in rows[:limit]]


def raw_archive_url(archive_url: str) -> str:
    """The 'id_' form returns the page exactly as captured, without the
    archive's toolbar injected into the HTML."""
    head, sep, rest = archive_url.partition("/web/")
    timestamp, _, original = rest.partition("/")
    return f"{head}{sep}{timestamp}id_/{original}"
