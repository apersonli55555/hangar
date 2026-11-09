"""Pitches: file format, fingerprints, the no-edit rule, backdating, Markdown, archive."""
import re

import pytest

from pipeline import pitches as P

GOOD = """+++
ticker = "KTOS"
direction = "long"
date = 2026-11-15
horizon = "12 months"
one_line = "Drone contracts not in consensus"
position = "none"
+++

## Thesis

Kratos wins **more** than the market expects.
"""
NAME = "2026-11-15-KTOS-long.md"
UPDATE = "\n## Update 2026-12-01\n\nRPO rose again.\n"
CLOSED = "\n## Closed 2027-11-15\n\nI was wrong about timing.\n"


# ---------------------------------------------------------------- format

def test_parse_good_pitch():
    p = P.parse(NAME, GOOD)
    assert (p["ticker"], p["direction"], p["date"], p["status"]) == ("KTOS", "long", "2026-11-15", "open")
    assert p["addenda"] == [] and len(p["sha256"]) == 64


@pytest.mark.parametrize("name,text,msg", [
    ("KTOS-long.md", GOOD, "file name"),
    ("2026-11-15-KTOS-short.md", GOOD, "must match"),
    ("2026-11-16-KTOS-long.md", GOOD, "must match"),
    (NAME, GOOD.replace('direction = "long"', 'direction = "buy"'), "direction must be"),
    (NAME, GOOD.replace('position = "none"', 'position = "maybe"'), "position"),
    (NAME, GOOD.replace('horizon = "12 months"\n', ""), "missing 'horizon'"),
    (NAME, GOOD.replace("date = 2026-11-15", 'date = "2026-11-15"'), "wrong type"),
    (NAME, GOOD.replace("date = 2026-11-15", "date = 2026-11-15T10:00:00"), "wrong type"),
    (NAME, GOOD.replace("+++\n\n## Thesis", "+++\n").split("Kratos")[0], "no text"),
    (NAME, "## no header\n", "header block"),
])
def test_parse_rejects(name, text, msg):
    with pytest.raises(P.PitchError, match=msg):
        P.parse(name, text)


def test_addenda_split_and_status():
    p = P.parse(NAME, GOOD + UPDATE + CLOSED)
    assert [a["part"] for a in p["addenda"]] == ["update 2026-12-01", "closed 2027-11-15"]
    assert p["status"] == "closed"
    assert "RPO rose" not in p["original"]          # notes are not part of the original
    assert p["sha256"] == P.parse(NAME, GOOD)["sha256"]   # appending doesn't change the original's fingerprint


def test_addenda_rules():
    with pytest.raises(P.PitchError, match="out of date order"):
        P.parse(NAME, GOOD + CLOSED + UPDATE.replace("2026-12-01", "2026-12-01"))
    with pytest.raises(P.PitchError, match="out of date order"):
        P.parse(NAME, GOOD + UPDATE.replace("2026-12-01", "2026-11-01"))   # before the pitch
    with pytest.raises(P.PitchError, match="nothing may follow"):
        P.parse(NAME, GOOD + CLOSED + UPDATE.replace("2026-12-01", "2027-12-01"))
    with pytest.raises(P.PitchError, match="same heading"):
        P.parse(NAME, GOOD + UPDATE + UPDATE)


# ---------------------------------------------------------------- fingerprints

def test_fingerprint_ignores_line_endings_but_not_edits():
    a = P.parse(NAME, GOOD)["sha256"]
    assert P.parse(NAME, GOOD.replace("\n", "\r\n"))["sha256"] == a
    assert P.parse(NAME, GOOD.replace("expects.", "expects.   "))["sha256"] == a   # trailing spaces
    assert P.parse(NAME, GOOD.replace("more", "more,"))["sha256"] != a
    assert P.parse(NAME, GOOD.replace('horizon = "12 months"', 'horizon = "6 months"'))["sha256"] != a


def ledger_for(text, today="2026-11-15"):
    return P.record([P.parse(NAME, text)], [], today)


def test_record_adds_parts_once():
    led = ledger_for(GOOD)
    assert [(r["part"], r["first_seen"]) for r in led] == [("original", "2026-11-15")]
    led2 = P.record([P.parse(NAME, GOOD + UPDATE)], led, "2026-12-02")
    assert [(r["part"], r["first_seen"]) for r in led2] == \
        [("original", "2026-11-15"), ("update 2026-12-01", "2026-12-02")]
    assert P.record([P.parse(NAME, GOOD + UPDATE)], led2, "2026-12-09") == led2   # nothing new


def test_editing_a_published_pitch_is_caught():
    led = ledger_for(GOOD)
    edited = P.parse(NAME, GOOD.replace("more", "much more"))
    [err] = P.check([edited], led)
    assert "was edited" in err and "Update" in err
    with pytest.raises(P.PitchError, match="was edited"):
        P.record([edited], led, "2026-11-20")


def published_with_update():
    led = ledger_for(GOOD)
    return P.record([P.parse(NAME, GOOD + UPDATE)], led, "2026-12-01")


def test_editing_a_published_update_is_caught():
    led = published_with_update()
    edited = P.parse(NAME, GOOD + UPDATE.replace("rose", "fell"))
    assert any("'update 2026-12-01' was edited" in e for e in P.check([edited], led))


def test_removing_or_deleting_is_caught():
    led = published_with_update()
    assert any("was removed" in e for e in P.check([P.parse(NAME, GOOD)], led))
    assert any("deleted or renamed" in e for e in P.check([], led))


def test_backdating_and_future_dates_refused():
    with pytest.raises(P.PitchError, match="at most 3 days"):
        ledger_for(GOOD, today="2026-11-19")
    assert ledger_for(GOOD, today="2026-11-18")          # 3 days is allowed
    with pytest.raises(P.PitchError, match="future"):
        ledger_for(GOOD, today="2026-11-14")


def test_repo_pitches_match_their_ledger():
    """The guard the daily job relies on: every published pitch is unchanged."""
    assert P.check(P.load(), P.read_ledger()) == []


# ---------------------------------------------------------------- Markdown subset

def test_markdown_blocks():
    html = P.markdown("## Thesis\n\nOne\ntwo.\n\n- a\n- b\ncontinued\n\n1. x\n2. y\n\n> quoted")
    assert html == ("<h3>Thesis</h3>\n<p>One two.</p>\n<ul><li>a</li><li>b continued</li></ul>\n"
                    "<ol><li>x</li><li>y</li></ol>\n<blockquote>quoted</blockquote>")


def test_markdown_inline():
    assert P.inline("**bold** and *it* and `x<y`") == \
        "<strong>bold</strong> and <em>it</em> and <code>x&lt;y</code>"
    assert P.inline("[10-K](https://www.sec.gov/x)") == '<a href="https://www.sec.gov/x" rel="noopener">10-K</a>'
    assert P.inline("2 * 3 * 4") == "2 * 3 * 4"            # spaced asterisks aren't italics


def test_markdown_cannot_inject_html_or_script_links():
    html = P.markdown("<script>alert(1)</script>\n\n[x](javascript:alert(1))")
    assert "<script>" not in html and "&lt;script&gt;" in html
    assert "href" not in html


# ---------------------------------------------------------------- Internet Archive

def test_archive_records_snapshot_only_if_new_enough(monkeypatch):
    from pipeline import http
    calls = []

    def fake_get(url, timeout=60):
        calls.append(url)
        if "wayback/available" in url:
            return (b'{"archived_snapshots":{"closest":{"available":true,'
                    b'"url":"http://web.archive.org/web/20261116000000/x","timestamp":"20261116000000"}}}')
        return b"ok"
    monkeypatch.setattr(http, "get", fake_get)
    p = P.parse(NAME, GOOD + UPDATE)
    led = [{"file": NAME, "part": "original", "sha256": "", "first_seen": "2026-11-15",
            "archive_url": "", "archived_at": ""},
           {"file": NAME, "part": "update 2026-12-01", "sha256": "", "first_seen": "2026-12-01",
            "archive_url": "", "archived_at": ""}]
    P.archive([p], led, "https://example.github.io/hangar/")
    assert calls[0] == "https://web.archive.org/save/https://example.github.io/hangar/pitches/2026-11-15-KTOS-long.html"
    assert led[0]["archive_url"] == "https://web.archive.org/web/20261116000000/x"
    assert led[1]["archive_url"] == ""          # snapshot predates the update: try again next run


def test_archive_failure_leaves_ledger_unchanged(monkeypatch):
    from pipeline import http

    def boom(url, timeout=60):
        raise OSError("down")
    monkeypatch.setattr(http, "get", boom)
    led = [{"file": NAME, "part": "original", "sha256": "", "first_seen": "2026-11-15",
            "archive_url": "", "archived_at": ""}]
    P.archive([P.parse(NAME, GOOD)], led, "https://x/")
    assert led[0]["archive_url"] == ""


# ---------------------------------------------------------------- page

def test_pitch_page_builds_with_proof_and_linked_numbers(tmp_path, monkeypatch):
    from pipeline import site
    folder = tmp_path / "pitches"
    folder.mkdir()
    text = GOOD.replace("KTOS", "LMT").replace("2026-11-15", "2025-06-02")
    name = "2025-06-02-LMT-long.md"
    (folder / name).write_text(text, encoding="utf-8")
    monkeypatch.setattr(P, "PITCHES", folder)
    monkeypatch.setattr(P, "LEDGER", tmp_path / "ledger.csv")
    P.write_ledger(P.record(P.load(), [], "2025-06-02"))
    # an update the daily job hasn't recorded yet
    (folder / name).write_text(text + UPDATE.replace("2026-12-01", "2025-09-01"), encoding="utf-8")
    monkeypatch.setattr(site, "OUT", tmp_path / "site")
    site.build()
    html = (tmp_path / "site" / "pitches" / "2025-06-02-LMT-long.html").read_text(encoding="utf-8")
    assert "not investment advice" in html
    assert P.parse(name, text)["sha256"] in html and "First published 2025-06-02" in html
    assert "Not yet recorded" in html                            # the update
    assert "What happened since" in html
    for cell in re.findall(r'<td class="num" data-fin>(.*?)</td>', html, re.S):
        if "$" in cell:
            assert "https://www.sec.gov/Archives/edgar/data/" in cell
    for cell in re.findall(r'<td data-label="Amount" class="num">(.*?)</td>', html, re.S):
        assert "web.archive.org" in cell
    index = (tmp_path / "site" / "pitches.html").read_text(encoding="utf-8")
    assert "pitches/2025-06-02-LMT-long.html" in index
    company = (tmp_path / "site" / "company" / "LMT.html").read_text(encoding="utf-8")
    assert "../pitches/2025-06-02-LMT-long.html" in company


def test_tampered_pitch_stops_the_build(tmp_path, monkeypatch):
    from pipeline import site
    folder = tmp_path / "pitches"
    folder.mkdir()
    (folder / NAME).write_text(GOOD, encoding="utf-8")
    monkeypatch.setattr(P, "PITCHES", folder)
    monkeypatch.setattr(P, "LEDGER", tmp_path / "ledger.csv")
    P.write_ledger(P.record(P.load(), [], "2026-11-15"))
    (folder / NAME).write_text(GOOD.replace("more", "less"), encoding="utf-8")
    monkeypatch.setattr(site, "OUT", tmp_path / "site")
    with pytest.raises(SystemExit, match="was edited"):
        site.build()
