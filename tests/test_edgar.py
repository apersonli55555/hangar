"""Rules for SEC backlog/revenue extraction and as-of materiality.

Small hand-made facts test each rule in isolation; tests at the bottom run on
real companyfacts files saved in tests/fixtures/edgar/ (trimmed to the tags we use).
"""
import gzip
import json
from pathlib import Path

import pytest

from pipeline import edgar, metrics

FIX = Path(__file__).parent / "fixtures" / "edgar"


def fact(val, end, filed, accn, form="10-K", start=None):
    f = {"val": val, "end": end, "filed": filed, "accn": accn, "form": form}
    if start:
        f["start"] = start
    return f


def facts(cik=1, **tags):
    return {"cik": cik, "facts": {"us-gaap": {t: {"units": {"USD": v}} for t, v in tags.items()}}}


# ---------------------------------------------------------------- periods

@pytest.mark.parametrize("start,end,expected", [
    (None, "2024-12-31", "instant"),
    ("2024-01-01", "2024-12-31", "annual"),
    ("2023-10-01", "2024-09-30", "annual"),        # TDG: fiscal year ends in September
    ("2022-01-03", "2023-01-01", "annual"),        # 52-week year (364 days)
    ("2020-12-28", "2022-01-02", "annual"),        # 53-week year (371 days)
    ("2024-07-01", "2024-09-29", "quarter"),
    ("2024-01-01", "2024-06-30", None),            # 6-month year-to-date: ignored
    ("2024-01-01", "2024-09-29", None),            # 9-month year-to-date: ignored
])
def test_period_type(start, end, expected):
    assert edgar.period_type(start, end) == expected


def test_fiscal_year_label():
    assert edgar.fiscal_year("2024-09-30") == 2024   # TDG FY2024
    assert edgar.fiscal_year("2024-12-31") == 2024
    assert edgar.fiscal_year("2023-01-01") == 2022   # 52/53-week year ending in early January


def test_filing_url():
    assert edgar.filing_url("0000936468", "0000936468-25-000010") == \
        "https://www.sec.gov/Archives/edgar/data/936468/000093646825000010/0000936468-25-000010-index.htm"


# ---------------------------------------------------------------- dedupe

def test_dedupe_keeps_first_filing_and_ignores_identical_repeats():
    raw = [fact(100, "2023-12-31", "2025-02-01", "B"),       # repeated as comparative a year later
           fact(100, "2023-12-31", "2024-02-01", "A"),
           fact(100, "2023-12-31", "2024-04-25", "Q", form="10-Q")]
    [r] = edgar.dedupe(raw, {"instant"})
    assert r["first"]["accn"] == "A" and r["revised"] is None


def test_dedupe_keeps_a_changed_value_as_a_revision_not_a_replacement():
    raw = [fact(100, "2023-12-31", "2024-02-01", "A"),
           fact(105, "2023-12-31", "2025-02-01", "B"),
           fact(100, "2023-12-31", "2024-04-25", "Q", form="10-Q")]
    [r] = edgar.dedupe(raw, {"instant"})
    assert r["first"]["val"] == 100 and r["revised"]["val"] == 105 and r["revised"]["accn"] == "B"


def test_dedupe_separates_annual_from_quarter_with_same_end():
    raw = [fact(400, "2024-12-31", "2025-02-01", "A", start="2024-01-01"),
           fact(110, "2024-12-31", "2025-02-01", "A", start="2024-10-01"),
           fact(300, "2024-09-29", "2024-10-25", "Q", form="10-Q", start="2024-01-01")]  # 9M YTD
    assert [r["first"]["val"] for r in edgar.dedupe(raw, {"annual"})] == [400]
    assert [r["first"]["val"] for r in edgar.dedupe(raw, {"quarter"})] == [110]


def test_dedupe_drops_other_forms_and_old_periods():
    raw = [fact(1, "2024-12-31", "2025-01-20", "X", form="8-K"),
           fact(2, "2017-12-31", "2018-02-01", "Y")]
    assert edgar.dedupe(raw, {"instant"}) == []


# ---------------------------------------------------------------- revenue tag choice

def test_revenue_tag_prefers_the_one_reaching_the_latest_year():
    f = facts(Revenues=[fact(9, "2020-12-31", "2021-02-01", "a", start="2020-01-01")],
              RevenueFromContractWithCustomerExcludingAssessedTax=[
                  fact(10, "2024-12-31", "2025-02-01", "b", start="2024-01-01")])
    tag, why = edgar.choose_revenue_tag(f)
    assert tag == "RevenueFromContractWithCustomerExcludingAssessedTax" and "latest" in why


def test_revenue_tag_none_when_missing():
    assert edgar.choose_revenue_tag(facts())[0] is None


# ---------------------------------------------------------------- gaps

def test_missing_rpo_is_not_reported_never_estimated():
    f = facts(Revenues=[fact(10, "2024-12-31", "2025-02-01", "b", start="2024-01-01")])
    rows = edgar.extract("XYZ", f)
    assert {r["metric"] for r in rows} == {"revenue"}
    cov = {c["metric"]: c for c in edgar.coverage("XYZ", f, rows)}
    assert cov["rpo"]["status"] == "not_reported" and cov["rpo"]["tag_reason"] == "not reported in XBRL"
    assert cov["revenue"]["status"] == "reported"


def test_breaks_in_series_are_listed():
    rpo = [fact(1, "2023-03-31", "2023-04-25", "a", form="10-Q"),
           fact(1, "2023-12-31", "2024-02-01", "b")]                 # Q2, Q3 missing
    f = facts(RevenueRemainingPerformanceObligation=rpo)
    cov = {c["metric"]: c for c in edgar.coverage("XYZ", f, edgar.extract("XYZ", f))}
    assert cov["rpo"]["status"] == "gaps"
    assert "between 2023-03-31 and 2023-12-31" in cov["rpo"]["gaps"]


def test_tag_dropped_in_newer_filings_is_a_gap():
    f = facts(RevenueRemainingPerformanceObligation=[fact(1, "2023-12-31", "2024-02-01", "a")],
              Revenues=[fact(9, "2024-12-31", "2025-02-01", "z", start="2024-01-01")])
    cov = {c["metric"]: c for c in edgar.coverage("XYZ", f, edgar.extract("XYZ", f))}
    assert "not tagged in the 10-K filed 2025-02-01" in cov["rpo"]["gaps"]


# ---------------------------------------------------------------- as-of materiality

def row(end, value, filed, **kw):
    return {"period_end": end, "value_usd": str(value), "filed": filed, "form": "10-Q",
            "filing_url": f"u-{end}", "revised_value_usd": "", "revised_filed": "",
            "revised_form": "", "revised_filing_url": "", **kw}


def award(d, amount, **kw):
    base = {"kind": "award", "review_reasons": "", "amount_usd": str(amount), "amount_qualifier": "",
            "is_idiq": "False", "action_type": "contract", "announcement_date": d}
    return {**base, **kw}


RPO = [row("2024-03-31", 100e9, "2024-04-23"), row("2024-06-30", 110e9, "2024-07-23")]


def test_as_of_uses_filing_date_not_period_end():
    # Quarter ended June 30 but wasn't filed until July 23: an award on July 10
    # must use the March figure (no look-ahead).
    assert metrics.as_of(RPO, "2024-07-10")["period_end"] == "2024-03-31"
    assert metrics.as_of(RPO, "2024-07-23")["period_end"] == "2024-06-30"
    assert metrics.as_of(RPO, "2024-01-01") is None


def test_revision_used_only_after_it_was_filed():
    r = row("2023-12-31", 100, "2024-02-01", revised_value_usd="120", revised_filed="2025-02-01",
            revised_form="10-K", revised_filing_url="rev")
    assert metrics.value_as_of(r, "2024-06-01")[0] == 100
    assert metrics.value_as_of(r, "2025-03-01")[:2] == (120, "rev")


def test_materiality_percentages_and_refs():
    rev = [row("2023-12-31", 50e9, "2024-01-25", form="10-K")]
    m = metrics.materiality(award("2024-05-01", 1e9), RPO, rev)
    assert m["pct_rpo"] == pytest.approx(1.0) and m["pct_rev"] == pytest.approx(2.0)
    assert m["rpo_ref"]["url"] == "u-2024-03-31"


def test_materiality_only_for_definite_awards():
    m = metrics.materiality(award("2024-05-01", 1e9, amount_qualifier="ceiling"), RPO, [])
    assert m["pct_rpo"] is None
    m = metrics.materiality(award("2024-05-01", 1e9, kind="shared_award"), RPO, [])
    assert m["pct_rpo"] is None


def test_materiality_empty_when_no_filing_yet():
    m = metrics.materiality(award("2019-01-02", 1e9), RPO, [])
    assert m["pct_rpo"] is None and m["pct_rev"] is None


def test_fmt_pct():
    assert metrics.fmt_pct(None) == ""
    assert metrics.fmt_pct(0.004) == "<0.01%"
    assert metrics.fmt_pct(1.234) == "1.23%"
    assert metrics.fmt_pct(12.34) == "12.3%"


# ---------------------------------------------------------------- real filings

fixtures = sorted(FIX.glob("*.json.gz")) if FIX.exists() else []


@pytest.mark.skipif(not fixtures, reason="no saved companyfacts fixtures yet")
@pytest.mark.parametrize("path", fixtures, ids=lambda p: p.name.split(".")[0])
def test_real_companyfacts(path):
    ticker = path.name.split(".")[0]
    f = json.loads(gzip.decompress(path.read_bytes()))
    rows = edgar.extract(ticker, f)
    for metric in ("rpo", "revenue"):
        mine = [r for r in rows if r["metric"] == metric]
        # one value per period, sorted, every one linked to an SEC filing
        assert len({(r["period_start"], r["period_end"]) for r in mine}) == len(mine)
        assert [r["period_end"] for r in mine] == sorted(r["period_end"] for r in mine)
        for r in mine:
            assert r["filing_url"].startswith("https://www.sec.gov/Archives/edgar/data/")
            assert r["accession"] and r["filed"] and r["form"] in edgar.FORMS
            assert r["filed"] > r["period_end"]          # a figure is filed after its period ends
    for r in rows:
        if r["metric"] == "revenue":
            assert r["period_type"] == "annual" and r["fiscal_year"]


def _real(ticker):
    f = edgar.load_facts(ticker, FIX)
    rows = edgar.extract(ticker, f)
    return f, rows, {c["metric"]: c for c in edgar.coverage(ticker, f, rows)}


@pytest.mark.skipif(not fixtures, reason="no saved companyfacts fixtures yet")
class TestRealFilings:
    def test_tdg_fiscal_year_ends_in_september(self):
        _, rows, _ = _real("TDG")
        fy25 = [r for r in rows if r["metric"] == "revenue" and r["fiscal_year"] == 2025]
        assert [(r["period_start"], r["period_end"], r["value_usd"]) for r in fy25] == \
            [("2024-10-01", "2025-09-30", 8831000000)]

    @pytest.mark.parametrize("ticker", ["TDG", "HWM"])
    def test_no_rpo_tag_is_reported_as_missing(self, ticker):
        _, rows, cov = _real(ticker)
        assert not [r for r in rows if r["metric"] == "rpo"]
        assert cov["rpo"]["status"] == "not_reported"

    def test_lhx_fy2025_revenue_missing_not_filled(self):
        # The 10-K tags the year's revenue to a 3-month period; we don't repair it.
        _, rows, cov = _real("LHX")
        assert max(r["period_end"] for r in rows if r["metric"] == "revenue") == "2025-01-03"
        assert "not tagged in the 10-K filed 2026-02-12" in cov["revenue"]["gaps"]

    def test_lmt_rpo_first_filed_value_and_link(self):
        _, rows, cov = _real("LMT")
        rpo = [r for r in rows if r["metric"] == "rpo"]
        assert cov["rpo"]["status"] == "reported" and len(rpo) >= 31
        assert all(r["form"] in ("10-K", "10-Q") for r in rpo)

    def test_ge_restatements_kept_as_revisions(self):
        _, rows, _ = _real("GE")
        fy23 = next(r for r in rows if r["metric"] == "revenue" and r["period_end"] == "2023-12-31")
        assert fy23["value_usd"] == 67954000000 and fy23["filed"] < "2024-03-01"   # as first filed
        assert fy23["revised_value_usd"] == 35348000000                           # recast after spin-off
