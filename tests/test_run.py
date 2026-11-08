"""Tests for the pipeline runner: no network, temp folders only."""
import csv
import gzip
from datetime import date

import pytest

from pipeline import run, sources


def test_no_requests_without_a_contact_in_github_actions(monkeypatch):
    from pipeline import http
    monkeypatch.delenv("HANGAR_CONTACT", raising=False)
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    with pytest.raises(RuntimeError, match="HANGAR_CONTACT"):
        http.user_agent()
    monkeypatch.setenv("HANGAR_CONTACT", "Hangar project, hangar@example.org")
    assert "hangar@example.org" in http.user_agent()


def test_truncated_capture_is_rejected():
    good = '<html><div class="body"><p>x</p></div></html>'
    assert run.page_looks_complete(good) is None
    assert "truncated" in run.page_looks_complete(good[:-7])
    assert "container" in run.page_looks_complete("<html><p>x</p></html>")


def test_slug_dates():
    m = sources._SLUG_RE.search(
        "https://www.war.gov/News/Contracts/Contract/Article/4609970/contracts-for-sept-23-2026/")
    assert m.group(1) == "4609970"
    assert sources._slug_date(m.group(2), m.group(3), m.group(4)) == "2026-09-23"
    assert sources._slug_date("sept", "31", "2026") is None      # impossible date


def test_raw_archive_url():
    assert sources.raw_archive_url("https://web.archive.org/web/20261107223004/https://www.war.gov/a/") \
        == "https://web.archive.org/web/20261107223004id_/https://www.war.gov/a/"


@pytest.fixture
def workdir(tmp_path, monkeypatch):
    monkeypatch.setattr(run, "DATA", tmp_path)
    monkeypatch.setattr(run, "RAW", tmp_path / "raw")
    monkeypatch.setattr(run, "MANIFEST", tmp_path / "pages.csv")
    return tmp_path


def test_human_review_clears_flag_but_never_changes_the_number(tmp_path):
    from pipeline.parse import Award
    a = Award(announcement_date="2026-07-30", branch="ARMY", source_url="s", archive_url="a",
              paragraph_index=0, text="t", kind="award", company="Lockheed Martin Corp.",
              amount_usd=53_859_843_289,
              review_reasons=["unusually large single amount ($10B+): confirm against the source"])
    p = tmp_path / "reviewed.csv"
    p.write_text("announcement_date,company,amount_usd,verdict,reviewed_on,comment\n"
                 "2026-07-30,Lockheed Martin Corp.,53859843289,confirmed,2026-11-08,PAC-3 multiyear\n",
                 encoding="utf-8")
    assert run.apply_reviews([a], p) == 1
    assert a.review_reasons == [] and a.amount_usd == 53_859_843_289
    assert "confirmed (PAC-3 multiyear)" in a.notes[-1]


def test_manifest_round_trip(workdir):
    rows = {"1": run.PageRow("1", "2026-11-06", "https://src/1/", "https://arch/1/", "fetched")}
    run.save_manifest(rows)
    assert run.load_manifest() == rows


def test_build_end_to_end_with_coverage(workdir):
    """Two fetched pages + one missing day -> awards.csv, coverage.csv."""
    page = ('<html><title>Contracts for Nov. {d}, 2026</title><div class="body">'
            '<p><strong>NAVY</strong></p><p>Lockheed Martin Corp., Fort Worth, Texas, is awarded a '
            '$10,000,000 contract (N00019-26-C-000{d}). Naval Air Systems Command, Patuxent River, '
            'Maryland, is the contracting activity.</p></div></html>')
    rows = {}
    for d in (2, 4):  # Nov 3 (a Tuesday) is deliberately missing
        r = run.PageRow(str(d), f"2026-11-0{d}", f"https://src/{d}/", f"https://arch/{d}/", "fetched")
        run.raw_path(r).parent.mkdir(parents=True, exist_ok=True)
        run.raw_path(r).write_bytes(gzip.compress(page.format(d=d).encode()))
        rows[r.article_id] = r
    run.build(rows, today=date(2026, 11, 4))
    awards = list(csv.DictReader(open(workdir / "awards.csv", encoding="utf-8")))
    assert [(a["ticker"], a["amount_usd"]) for a in awards] == [("LMT", "10000000.0")] * 2
    cov = {r["date"]: r["status"] for r in csv.DictReader(open(workdir / "coverage.csv", encoding="utf-8"))}
    assert cov == {"2026-11-02": "have", "2026-11-03": "missing", "2026-11-04": "have"}
