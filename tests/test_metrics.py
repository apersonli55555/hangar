from pipeline.metrics import (all_quarters, branch_group, classify, fmt_usd,
                              quarterly_definite_by_branch, summarize)


def row(**kw):
    base = dict(kind="award", review_reasons="", amount_usd="1000000.0", amount_qualifier="",
                is_idiq="False", announcement_date="2026-05-01", branch="NAVY")
    return {**base, **kw}


def test_classify():
    assert classify(row()) == ("definite", "")
    assert classify(row(amount_qualifier="ceiling")) == ("potential", "")
    assert classify(row(is_idiq="True")) == ("potential", "")
    assert classify(row(is_idiq="True", action_type="modification")) == ("definite", "")
    assert classify(row(is_idiq="True", action_type="order")) == ("definite", "")
    assert classify(row(kind="shared_award", amount_usd="")) == ("excluded", "shared pool")
    assert classify(row(kind="repeat")) == ("excluded", "repeat")
    assert classify(row(kind="correction", amount_usd="")) == ("excluded", "correction")
    assert classify(row(review_reasons="unusually large")) == ("excluded", "needs review")
    assert classify(row(amount_usd="")) == ("excluded", "no amount")
    assert classify(row(kind="added_awardee", amount_qualifier="")) == ("definite", "")


def test_summarize_never_mixes_categories():
    rows = [row(amount_usd="100.0"), row(amount_usd="50.0", amount_qualifier="maximum"),
            row(amount_usd="999.0", kind="repeat"), row(amount_usd="7.0", announcement_date="2023-01-01")]
    s = summarize(rows, since="2024-01-01")
    assert (s["definite_usd"], s["definite_n"]) == (100.0, 1)
    assert (s["potential_usd"], s["potential_n"]) == (50.0, 1)
    assert s["excluded"] == {"repeat": 1}


def test_branch_groups_absorb_source_typos():
    assert branch_group("DEFENSE LOGISTCS AGENCY") == "Defense Logistics Agency"
    assert branch_group("SPACE FORCE") == "Air Force"
    assert branch_group("DEFNSE COUNTERINTELLIGENCE AND SECURITY AGENCY") == "Other agencies"


def test_quarters():
    assert all_quarters("2025-11-03", "2026-04-01") == ["2025 Q4", "2026 Q1", "2026 Q2"]
    q = quarterly_definite_by_branch([row(), row(branch="ARMY", announcement_date="2026-01-02")])
    assert q == {"2026 Q2": {"Navy": 1e6}, "2026 Q1": {"Army": 1e6}}


def test_price_link_window_and_symbol():
    from pipeline.site import price_link
    url = price_link("MOG.A", "2025-11-07")
    assert url.startswith("https://finance.yahoo.com/quote/MOG-A/history/?period1=")
    # 2025-10-31 00:00 UTC and 2025-11-22 00:00 UTC
    assert "period1=1761868800" in url and "period2=1763769600" in url


def test_fmt_usd():
    assert fmt_usd(53_859_843_289) == "$53.86B"
    assert fmt_usd(83_300_055) == "$83.3M"
    assert fmt_usd(2_500) == "$2,500"
