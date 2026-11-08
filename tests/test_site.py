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


def test_every_backlog_and_revenue_number_links_to_an_sec_filing():
    for page in (SITE / "company").glob("*.html"):
        html = page.read_text(encoding="utf-8")
        for cell in re.findall(r'<td class="num" data-fin>(.*?)</td>', html, re.S):
            assert 'href="https://www.sec.gov/Archives/edgar/data/' in cell, (page.name, cell[:120])
        for cell in re.findall(r'<td data-label="Size vs. company" class="num mat">(.*?)</td>', html, re.S):
            for a in re.findall(r"<a [^>]*>", cell):
                assert 'href="https://www.sec.gov/Archives/edgar/data/' in a, (page.name, a)
        for circle in re.findall(r"<a href=\"([^\"]+)\"><(?:circle|rect) class=\"(?:l-r|bar-ratio)", html):
            assert circle.startswith("https://www.sec.gov/Archives/edgar/data/"), (page.name, circle)
