"""Return tracking: arithmetic on hand-typed prices, and strict validation of them."""
from datetime import date

import pytest

from pipeline import pitch_returns as pr
from pipeline import pitches as pitch_lib

HEADER = "date,ticker,close_usd,source,source_url\n"
TODAY = date(2027, 1, 15)


def marks(tmp_path, body):
    p = tmp_path / "pitch_marks.csv"
    p.write_text(HEADER + body, encoding="utf-8")
    return pr.load_marks(p, today=TODAY)


def pitch(direction="long", day="2026-11-09", closed=None):
    return {"ticker": "KTOS", "direction": direction, "date": day,
            "addenda": [{"kind": "Closed", "date": closed}] if closed else []}


BASE = ("2026-11-06,KTOS,35.10,Yahoo Finance,\n"
        "2026-11-06,SPY,735.29,Yahoo Finance,\n")


def test_long_return_and_difference(tmp_path):
    m = marks(tmp_path, BASE + "2026-12-04,KTOS,38.61,Kratos investor site,https://ir.kratosdefense.com/\n"
                               "2026-12-04,SPY,750.00,Kratos investor site,\n")
    s = pr.score(pitch(), m)
    assert s["status"] == "scored" and s["entry"]["date"] == "2026-11-06"   # Friday close for a Monday pitch
    row = s["latest"]
    assert row["pitch_return"] == pytest.approx(38.61 / 35.10 - 1)          # +10.0%
    assert row["spy_return"] == pytest.approx(750.00 / 735.29 - 1)          # +2.0%
    assert row["vs_spy"] == pytest.approx(row["pitch_return"] - row["spy_return"])
    assert pr.pct(row["pitch_return"]) == "+10.0%" and pr.points(row["vs_spy"]) == "+8.0 pts"


def test_short_gains_when_the_stock_falls(tmp_path):
    m = marks(tmp_path, BASE + "2026-12-04,KTOS,31.59,x,\n2026-12-04,SPY,735.29,x,\n")
    row = pr.score(pitch("short"), m)["latest"]
    assert pr.pct(row["pitch_return"]) == "+10.0%" and pr.pct(row["spy_return"]) == "0.0%"


def test_no_later_price_yet(tmp_path):
    s = pr.score(pitch(), marks(tmp_path, BASE))
    assert s["status"] == "scored" and s["rows"] == [] and s["latest"] is None


def test_date_without_a_spy_price_is_not_scored(tmp_path):
    m = marks(tmp_path, BASE + "2026-12-04,KTOS,40.00,x,\n")
    assert pr.score(pitch(), m)["rows"] == []


def test_missing_entry_prices_are_reported_not_guessed(tmp_path):
    only_later = marks(tmp_path, "2026-12-04,KTOS,40.00,x,\n2026-12-04,SPY,750.00,x,\n")
    assert pr.score(pitch(), only_later)["status"] == "no entry price"
    no_spy = marks(tmp_path, "2026-11-06,KTOS,35.10,x,\n")
    assert "SPY" in pr.score(pitch(), no_spy)["reason"]
    too_old = marks(tmp_path, "2026-10-30,KTOS,35.10,x,\n2026-10-30,SPY,700.00,x,\n")
    assert pr.score(pitch(), too_old)["status"] == "no entry price"         # 10 days before the pitch


def test_closed_pitch_stops_being_scored(tmp_path):
    m = marks(tmp_path, BASE + "2026-12-04,KTOS,38.61,x,\n2026-12-04,SPY,750.00,x,\n"
                               "2027-01-08,KTOS,50.00,x,\n2027-01-08,SPY,760.00,x,\n")
    s = pr.score(pitch(closed="2026-12-04"), m)
    assert [r["date"] for r in s["rows"]] == ["2026-12-04"]
    assert pr.score(pitch(closed="2026-11-09"), m)["rows"] == []            # closed on day one


@pytest.mark.parametrize("row,problem", [
    ("2026-11-07,KTOS,35.10,x,\n", "weekend"),
    ("2027-02-01,KTOS,35.10,x,\n", "future"),
    ("2026-11-06,KTOS,abc,x,\n", "number"),
    ("2026-11-06,KTOS,0,x,\n", "above zero"),
    ("2026-11-06,KTOS,35.10,,\n", "where you looked"),
    ("2026-11-06,KTOS,35.10,x,http://insecure.example\n", "https://"),
    ("11/06/2026,KTOS,35.10,x,\n", "date must look like"),
    ("2026-11-06,KTOS,35.10,x,\n2026-11-06,ktos,36.00,x,\n", "second price"),
])
def test_bad_rows_stop_the_build(tmp_path, row, problem):
    with pytest.raises(pr.MarksError, match=problem):
        marks(tmp_path, row)


def test_wrong_header_is_rejected(tmp_path):
    p = tmp_path / "pitch_marks.csv"
    p.write_text("date,ticker,price\n", encoding="utf-8")
    with pytest.raises(pr.MarksError, match="header"):
        pr.load_marks(p, today=TODAY)


def test_real_marks_file_is_valid_and_every_pitch_has_an_entry_price():
    m = pr.load_marks()
    for p in pitch_lib.load():
        s = pr.score(p, m)
        assert s["status"] == "scored", (p["file"], s.get("reason"))
        # the entry price in the file is the one the published pitch states
        text = p["original"] + "".join(a["body"] for a in p["addenda"])
        assert f"${s['entry']['close']:,.2f}" in text, p["file"]
        assert f"${s['entry']['spy_close']:,.2f}" in text, p["file"]
