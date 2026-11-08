"""Checks on the built site (runs only if site/ exists: `python -m pipeline.site`)."""
import re
from pathlib import Path
from urllib.parse import urlparse

import pytest

SITE = Path(__file__).resolve().parent.parent / "site"
pages = sorted(SITE.rglob("*.html")) if SITE.exists() else []
pytestmark = pytest.mark.skipif(not pages, reason="site not built")


@pytest.mark.parametrize("page", pages, ids=lambda p: str(p.relative_to(SITE)))
def test_internal_links_resolve(page):
    html = page.read_text(encoding="utf-8")
    for href in re.findall(r'href="([^"]+)"', html):
        u = urlparse(href)
        if u.scheme or href.startswith("#"):
            continue
        target = (page.parent / u.path).resolve()
        assert target.exists(), f"{page.name}: broken link {href}"


@pytest.mark.parametrize("page", pages, ids=lambda p: str(p.relative_to(SITE)))
def test_every_page_has_disclaimer(page):
    assert "not investment advice" in page.read_text(encoding="utf-8")


def test_every_amount_links_to_its_source():
    for page in (SITE / "company").glob("*.html"):
        html = page.read_text(encoding="utf-8")
        for cell in re.findall(r'<td data-label="Amount" class="num">(.*?)</td>', html, re.S):
            if "$" in cell:
                assert "web.archive.org" in cell, (page.name, cell[:120])
