"""Polite HTTP helper shared by every pipeline step.

Rules we follow (see README "Data sources & etiquette"):
  * An honest User-Agent naming the project and a contact. The contact comes
    from the HANGAR_CONTACT environment variable (a GitHub Actions secret in
    production) so no personal email is ever committed to the repo.
  * A minimum delay between requests to the same host.
  * We never disguise ourselves as a browser or try to get past bot blocks.
"""
from __future__ import annotations

import gzip
import os
import time
import urllib.error
import urllib.request
from urllib.parse import urlparse

MIN_DELAY_SECONDS = 2.0  # per host; deliberately much slower than any limit
# The Internet Archive is a non-profit; be extra gentle (max ~15 requests/min).
HOST_DELAY_SECONDS = {"web.archive.org": 4.0, "archive.org": 4.0}
_last_request: dict[str, float] = {}


def user_agent() -> str:
    contact = os.environ.get("HANGAR_CONTACT", "").strip()
    if not contact:
        if os.environ.get("GITHUB_ACTIONS") == "true":
            # In production, never send requests without a way to reach us.
            raise RuntimeError("HANGAR_CONTACT secret is not set: add it in the repo's "
                               "Settings > Secrets and variables > Actions")
        contact = "contact not configured"
    return f"Hangar/0.1 (aerospace & defense research project; {contact})"


def get(url: str, timeout: int = 60, retries: int = 2) -> bytes:
    """GET a URL politely and return the body. Raises on final failure."""
    host = urlparse(url).netloc
    for attempt in range(retries + 1):
        delay = HOST_DELAY_SECONDS.get(host, MIN_DELAY_SECONDS)
        wait = delay - (time.monotonic() - _last_request.get(host, 0.0))
        if wait > 0:
            time.sleep(wait)
        _last_request[host] = time.monotonic()
        req = urllib.request.Request(url, headers={"User-Agent": user_agent()})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                body = resp.read()
                # Some servers gzip the body even when we don't ask; unzip it.
                # Trust the gzip magic bytes, not the header (the header can be wrong).
                if body[:2] == b"\x1f\x8b":
                    body = gzip.decompress(body)
                return body
        except urllib.error.HTTPError as e:
            # 403 means "you're not welcome": stop instead of retrying.
            if e.code in (403, 404) or attempt == retries:
                raise
            if e.code == 429:  # "slow down": wait a full minute
                time.sleep(60)
        except urllib.error.URLError:
            if attempt == retries:
                raise
        time.sleep(5 * (attempt + 1))  # back off before retrying
    raise RuntimeError("unreachable")
