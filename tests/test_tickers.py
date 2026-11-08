"""Ticker mapping tests: real names from the announcements."""
from datetime import date

import pytest

from pipeline.parse import Award
from pipeline.run import mark_repeats, us_federal_holidays
from pipeline.tickers import TickerMapper, normalize

M = TickerMapper.load()


def t(name, on="2026-06-01"):
    m = M.match(name, on)
    return m.ticker if m and m.status == "public" else (m.status if m else None)


@pytest.mark.parametrize("name,ticker", [
    ("Lockheed Martin Aeronautics Co.", "LMT"),
    ("The Lockheed Martin Corp., Rotary and Mission Systems", "LMT"),
    ("Lockheed Martin, Mission Systems and Training", "LMT"),
    ("Sikorsky Aircraft Corp.", "LMT"),
    ("Raytheon Co.", "RTX"),
    ("RTX Corp., Pratt & Whitney Military Engines", "RTX"),
    ("Pratt & Whitney", "RTX"),
    ("Northrop Grumman Systems Corp.", "NOC"),
    ("Northrup Grumman Systems Corp.", "NOC"),          # typo in the source
    ("General Dynamics Electric Boat Corp.", "GD"),
    ("Huntington Ingalls Inc., Newport News Shipbuilding", "HII"),
    ("The Boeing Co.", "BA"),
    ("Bell Textron Inc.", "TXT"),
    ("Parsons Government Services Inc.", "PSN"),
    ("V2X Systems LLC", "VVX"),
    ("Fluor Marine Propulsion LLC", "FLR"),
    ("CAE USA Inc.", "CAE"),
])
def test_known_public_names(name, ticker):
    assert t(name) == ticker


def test_triaged_names_from_the_backfill():
    assert t("DRS Sustainment Systems Inc.") == "DRS"
    assert t("Vertex Aerospace LLC") == "VVX"
    assert t("The John Hopkins University Applied Physics Laboratory LLC") == "private"
    assert t("Kongsberg Defence & Aerospace") == "foreign_listed"
    assert t("Gentex Corp.") is None        # deliberately unmapped: two unrelated Gentexes


def test_look_alike_names_are_not_mapped():
    assert t("HHI Corp.") is None                       # not Huntington Ingalls (HII)
    assert t("Parsons Brinckerhoff Inc.") is None       # not Parsons Corp.
    assert t("Bellwether Construction") is None


def test_joint_ventures_are_never_auto_mapped():
    assert t("Bering-Weston JV LLC") == "joint_venture"
    assert t("Bell Boeing Joint Program Office") == "joint_venture"
    assert t("Kleinfelder – Stantec USACE JV") == "joint_venture"


def test_ownership_dates_are_respected():
    assert t("Amentum Services Inc.", "2024-06-01") == "private"   # before the Sept 2024 merger
    assert t("Amentum Services Inc.", "2024-10-01") == "AMTM"
    assert t("Aerojet Rocketdyne Inc.", "2023-01-01") is None      # traded as AJRD then
    assert t("Aerojet Rocketdyne Inc.", "2024-01-01") == "LHX"


def test_rule_order_leonardo_drs_vs_leonardo():
    assert t("Leonardo DRS Inc.") == "DRS"
    assert t("Leonardo SpA") == "foreign_listed"


def test_normalize():
    assert normalize("The Boeing Co.,*") == "boeing co"
    assert normalize("Black and Veatch–Geosyntec Civil Works JV") == "black and veatch geosyntec civil works jv"
    assert normalize("Pratt & Whitney") == "pratt and whitney"


def test_repeated_paragraph_on_a_later_page_is_not_counted_twice():
    def award(day, text, company="Huntington Ingalls Inc."):
        return Award(announcement_date=day, branch="NAVY", source_url="s", archive_url="a",
                     paragraph_index=1, text=text, kind="award", company=company,
                     amount_usd=5_099_973_132)
    txt = "Huntington Ingalls Inc., Newport News, Virginia, is awarded a $5,099,973,132 contract."
    awards = [award("2026-09-28", txt), award("2026-09-29", txt + " "),
              award("2026-09-29", txt, company="Other Co.")]
    assert mark_repeats(awards) == 1
    assert [a.kind for a in awards] == ["award", "repeat", "award"]
    assert "2026-09-28" in awards[1].notes[0]


def test_federal_holidays_2026():
    h = us_federal_holidays(2026)
    assert date(2026, 7, 3) in h        # July 4 is a Saturday -> observed Friday
    assert date(2026, 11, 26) in h      # Thanksgiving
    assert date(2026, 1, 19) in h       # MLK Day
    assert date(2026, 11, 6) not in h
