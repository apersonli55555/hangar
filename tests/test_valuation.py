"""EBITDA helper: trailing-12-month arithmetic on real reported figures.

The numbers below are Lockheed Martin's and RTX's, as reported in their SEC
filings (in $ millions, scaled to dollars)."""
from scripts import valuation as v

M = 1_000_000


def fact(start, end, val, form="10-Q", filed="2026-10-21", accn="0001628280-26-066928"):
    return {"start": start, "end": end, "val": val * M, "form": form, "filed": filed, "accn": accn}


def facts(**tags):
    return {"cik": 936468, "facts": {"us-gaap": {t: {"units": {"USD": fs}} for t, fs in tags.items()}}}


LMT_OP = [
    fact("2025-01-01", "2025-09-28", 5400, filed="2025-10-21", accn="0001628280-25-045693"),
    fact("2025-01-01", "2025-12-31", 7731, form="10-K", filed="2026-01-29", accn="0001628280-26-004195"),
    fact("2026-01-01", "2026-06-28", 4542, filed="2026-07-21", accn="0001628280-26-049411"),
    fact("2026-06-29", "2026-09-27", 2443),                     # the quarter alone: not used
    fact("2026-01-01", "2026-09-27", 6985),
]
LMT_DA = [
    fact("2025-01-01", "2025-09-28", 1224),
    fact("2025-01-01", "2025-12-31", 1687, form="10-K", filed="2026-01-29", accn="0001628280-26-004195"),
    fact("2026-01-01", "2026-09-27", 1223),
]


def test_ttm_is_fiscal_year_plus_ytd_minus_last_years_ytd():
    t = v.ttm(facts(OperatingIncomeLoss=LMT_OP), ["OperatingIncomeLoss"])
    assert t["value"] == (7731 + 6985 - 5400) * M          # $9,316 million
    assert t["end"] == "2026-09-27"
    assert [p for p, _ in t["parts"]] == ["fiscal year", "year to date", "minus same months a year earlier"]


def test_restated_comparison_uses_the_latest_filing():
    restated = LMT_OP + [fact("2025-01-01", "2025-09-28", 5450, filed="2026-10-21")]
    t = v.ttm(facts(OperatingIncomeLoss=restated), ["OperatingIncomeLoss"])
    assert t["value"] == (7731 + 6985 - 5450) * M


def test_when_the_10k_is_newest_ttm_is_the_fiscal_year():
    t = v.ttm(facts(OperatingIncomeLoss=LMT_OP[:2]), ["OperatingIncomeLoss"])
    assert t["value"] == 7731 * M and [p for p, _ in t["parts"]] == ["fiscal year"]


def test_ebitda_adds_operating_income_and_da():
    e = v.ebitda(facts(OperatingIncomeLoss=LMT_OP, DepreciationAmortizationAndAccretionNet=LMT_DA))
    assert e["ebitda"] == (9316 + 1686) * M


def test_da_tag_that_lines_up_with_operating_income_wins():
    # RTX: one D&A tag only in the 10-K (ends a year earlier), another every quarter.
    annual_only = [fact("2025-01-01", "2025-12-31", 4378, form="10-K", filed="2026-02-05")]
    e = v.ebitda(facts(OperatingIncomeLoss=LMT_OP, DepreciationDepletionAndAmortization=annual_only,
                       DepreciationAmortizationAndAccretionNet=LMT_DA))
    assert e["da"]["tag"] == "DepreciationAmortizationAndAccretionNet" and e["ebitda"] is not None


def test_mismatched_periods_are_refused_not_combined():
    annual_only = [fact("2025-01-01", "2025-12-31", 4378, form="10-K", filed="2026-02-05")]
    e = v.ebitda(facts(OperatingIncomeLoss=LMT_OP, DepreciationDepletionAndAmortization=annual_only))
    assert e["ebitda"] is None and "ends" in e["reason"]


def test_missing_tags_give_a_reason():
    e = v.ebitda(facts(OperatingIncomeLoss=LMT_OP))
    assert e["ebitda"] is None and "not found" in e["reason"]


# ---- found in the Codex review
def test_overlapping_fiscal_year_and_ytd_are_not_combined():
    op = [fact("2025-01-01", "2025-12-31", 100, form="10-K"),
          fact("2025-12-25", "2026-09-30", 90),          # starts before the year ended
          fact("2025-01-01", "2025-09-30", 60)]
    assert v.ttm(facts(OperatingIncomeLoss=op), ["OperatingIncomeLoss"]) is None


def test_exact_da_period_beats_a_nearly_matching_one():
    op = [fact("2025-01-01", "2025-12-31", 100, form="10-K")]
    near = [fact("2024-12-25", "2025-12-24", 20, form="10-K")]
    exact = [fact("2025-01-01", "2025-12-31", 30, form="10-K")]
    e = v.ebitda(facts(OperatingIncomeLoss=op, DepreciationDepletionAndAmortization=near,
                       DepreciationAmortizationAndAccretionNet=exact))
    assert e["ebitda"] == 130 * M


def test_nearly_matching_periods_are_refused():
    op = [fact("2025-01-01", "2025-12-31", 100, form="10-K")]
    near = [fact("2024-12-25", "2025-12-24", 20, form="10-K")]
    e = v.ebitda(facts(OperatingIncomeLoss=op, DepreciationDepletionAndAmortization=near))
    assert e["ebitda"] is None
