"""Parser tests built from REAL award paragraphs (war.gov, Oct-Nov 2026).

Each test pins one trap we found in the source text. If the government
changes its wording, a test should fail loudly instead of the site quietly
showing a wrong number.

Run:  python -m pytest -q
"""
import gzip
import json
from datetime import date
from pathlib import Path

import pytest

from pipeline.parse import (contract_numbers, format_piid, has_malformed_money, money_values,
                            parse_chunk, parse_page, split_awards)

FIXTURES = Path(__file__).parent / "fixtures" / "pages"
CTX = dict(branch="TEST", ann_date=date(2026, 11, 6), source_url="https://example/src",
           archive_url="https://example/arch", index=0)


def one(text):
    awards = parse_chunk(text, **CTX)
    assert len(awards) == 1
    return awards[0]


# ---------------------------------------------------------------- helpers

def test_money_parsing():
    assert money_values("a $1,500,444,621); and $82,388,528, have") == [1500444621, 82388528]
    assert money_values("$20,945,615.72.") == [20945615.72]
    assert money_values("$4.2 billion") == [4.2e9]


def test_malformed_money_is_detected_not_misread():
    # Real typo from the Oct. 23 page: an extra digit.
    assert has_malformed_money("funds in the amount of $42,758,8967 are obligated")
    assert money_values("$42,758,8967") == []          # never read as $42,758,896
    assert not has_malformed_money("$42,758,897 and $9.5")


def test_contract_number_formats():
    text = "W912EK-22-D-0002, N0001923C0049, N6247321D-1205 and P00006"
    assert [format_piid(p) for p in contract_numbers(text)] == [
        "W912EK-22-D-0002", "N00019-23-C-0049", "N62473-21-D-1205"]


# ---------------------------------------------------------- single awards

def test_simple_modification_with_cumulative_value():
    a = one("Raytheon Co., Tucson, Arizona, has been awarded a $350,000,000 ceiling increase "
            "modification (P00014) to previously awarded contract (FA8672-19-D-0001) for Small "
            "Diameter Bomb II life cycle support (GBU-53/B). The modification brings the total "
            "cumulative face value of the contract to $950,000,000 from $600,000,000. Work will be "
            "performed in Tucson, Arizona, and is expected to be completed by Dec. 31, 2030. No funds "
            "are being obligated at the time of award. The Air Force Life Cycle Management Center, "
            "Eglin Air Force Base, Florida, is the contracting activity.")
    assert (a.company, a.city, a.state_or_country) == ("Raytheon Co.", "Tucson", "Arizona")
    assert a.amount_usd == 350_000_000
    assert a.cumulative_face_value_usd == 950_000_000      # not the "from $600M" figure
    assert a.obligated_at_award_usd == 0
    assert a.action_type == "modification" and a.modification_number == "P00014"
    assert a.contract_number == "FA8672-19-D-0001"
    assert a.review_reasons == []


def test_division_name_stays_in_company_and_stars_mark_small_business():
    a = one("Lockheed Martin, Mission Systems and Training, Baltimore, Maryland, is awarded a "
            "$13,444,766 cost-plus-award-fee order (N6278626F1000) against the previously awarded "
            "basic ordering agreement (N00024-19-G-2319). The Supervisor of Shipbuilding, "
            "Conversion, and Repair, Bath, Maine, is the contracting activity.")
    assert a.company == "Lockheed Martin, Mission Systems and Training"
    assert a.contract_number == "N62786-26-F-1000"            # the order, not the BOA
    assert a.action_type == "order"
    b = one("Advon Corp.,* Tallahassee, Florida, was awarded a $22,301,940 firm-fixed-price "
            "contract to construct a new air traffic control tower. U.S. Army Corps of Engineers, "
            "Savannah, Georgia, is the contracting activity (W912HN-27-C-A003).")
    assert b.company == "Advon Corp." and b.small_business == "small business"


def test_amount_stated_in_a_later_sentence():
    a = one("HHI Corp., Ogden, Utah, was awarded a firm-fixed-price contract for maintenance and "
            "repair on the General Stilwell Memorial Army Reserve Center. The amount of this action "
            "is $36,259,921 with a total cumulative face value of $36,544,727. U.S. Army Corps of "
            "Engineers, Louisville, Kentucky, is the contracting activity (W912QR-27-C-A001).")
    assert a.amount_usd == 36_259_921


def test_awarded_date_differs_from_announcement_date():
    a = one("Pratt & Whitney, Hartford, Connecticut, has been awarded a $25,193,724 ceiling "
            "cost-plus-fixed-fee contract for the component improvement program. The Air Force "
            "Sustainment Center, Hill Air Force Base, Utah, is the contracting activity "
            "(FA8252-27-D-B001). (Awarded on Oct. 28, 2026).")
    assert a.award_date == "2026-10-28" and a.announcement_date == "2026-11-06"
    assert a.amount_qualifier == "ceiling"


def test_no_funds_will_expire_is_not_zero_obligation():
    a = one("Northrup Grumman Systems Corp., Magna, Utah, is awarded a $43,856,500 firm-fixed-price "
            "contract (N0003027C2002) to support the design of new rocket motor facilities. Fiscal "
            "2026 weapons procurement (Navy) funds in the amount of $43,856,500 will be obligated on "
            "this award and no funds will expire at the end of this fiscal year. PAE Strategic "
            "Systems Programs, Washington, D.C., is the contracting activity.")
    assert a.obligated_at_award_usd == 43_856_500
    assert a.company == "Northrup Grumman Systems Corp."   # source typo kept verbatim


def test_of_which_is_a_subset_not_extra_money():
    a = one("Fluor Marine Propulsion LLC, Arlington, Virginia, was awarded a $2,409,077,590 "
            "cost-plus-fixed-fee modification to a previously awarded contract (N0002418C2130). "
            "Fiscal 2026 operations and maintenance (Navy) funds in the amount of $582,062 (97%); "
            "and fiscal 2026 shipbuilding and conversion (Navy) funds in the amount of $15,838 "
            "(3%), was obligated at time of award, of which funds in the amount of $15,838 expired "
            "at the end of the fiscal 2026. The Naval Sea Systems Command, Washington, D.C., is "
            "the contracting activity.")
    assert a.obligated_at_award_usd == 582_062 + 15_838     # not + another 15,838


def test_ambiguous_obligation_is_flagged_not_guessed():
    a = one("CAPE-APTIM JV1 LLC,* Norcross, Georgia, was awarded a $46,259,489 cost-plus-fixed-fee "
            "contract for environmental remedial action. Fiscal 2024 and fiscal 2025 Environmental "
            "Protection Agency hazardous substance funds in the amount of $15,000,000 each were "
            "obligated at the time of the award. U.S. Army Corps of Engineers, Kansas City, "
            "Missouri, is the contracting activity (W912DQ-27-C-A002).")
    assert a.amount_usd == 46_259_489
    assert a.obligated_at_award_usd is None
    assert any("ambiguous" in n for n in a.notes)   # a note: the headline amount is fine
    assert a.review_reasons == []


# ------------------------------------- patterns found in the 2024-26 backfill

def test_is_being_awarded():
    a = one("L3 Technologies Inc., Camden, New Jersey, is being awarded a $26,945,335 "
            "firm-fixed-price contract (N66001-26-C-0021) for communications equipment. This "
            "contract was awarded as a sole-source acquisition. Naval Information Warfare Center "
            "Pacific, San Diego, California, is the contracting activity.")
    assert a.company == "L3 Technologies Inc." and a.amount_usd == 26_945_335
    assert a.review_reasons == []


def test_one_amount_for_many_companies_is_a_shared_pool_whatever_the_wording():
    text = ("The Haskell Co.,* Jacksonville, Florida (N40085-26-D-0086); Walsh Federal LLC,* "
            "Chicago, Illinois (N40085-26-D-0087); and Consigli Construction Co. Inc.,* Durham, "
            "North Carolina (N40085-26-D-0096), are awarded a $975,000,000 firm-fixed-price, "
            "indefinite-delivery/indefinite-quantity, multiple award, construction contract for "
            "general construction services. NAVFAC Mid-Atlantic, Norfolk, Virginia, is the "
            "contracting activity.")
    awards = parse_chunk(text, **CTX)
    assert all(a.kind == "shared_award" and a.amount_usd is None for a in awards)
    assert all(a.shared_amount_usd == 975_000_000 and a.shared_with_count == 3 for a in awards)
    assert all(a.review_reasons == [] for a in awards)


def test_gsa_contract_number_starting_with_a_digit():
    assert [format_piid(p) for p in contract_numbers("activity (47QTCK-18-D-0056).")] == [
        "47QTCK-18-D-0056"]
    assert contract_numbers("in 2026 there were 202618 0001") == []


def test_source_punctuation_typos_are_repaired_and_noted():
    a = one("Explus Inc., Sterling Virginia is awarded a $24,000,000 firm-fixed-price contract "
            "(N00024-26-C-1234). NAVSEA, Washington, D.C., is the contracting activity.")
    assert (a.company, a.city, a.state_or_country) == ("Explus Inc.", "Sterling", "Virginia")
    assert any("repaired" in n for n in a.notes) and a.review_reasons == []
    text = ("Alberici Constructors Inc., St. Louis, Missouri (N44255-26-D-1811); Caddell Nova JV. "
            "Montgomery, Alabama (N44255-26-D-1808); (N44255-26-D-1812); and Walsh Federal LLC, "
            "Chicago, Illinois (N44255-26-D-1800), are awarded a combined-maximum-value "
            "$9,000,000,000 indefinite-delivery/indefinite-quantity contract.")
    awards = parse_chunk(text, **CTX)
    assert [a.company for a in awards] == ["Alberici Constructors Inc.", "Caddell Nova JV.",
                                           "Walsh Federal LLC"]
    assert awards[1].city == "Montgomery"
    assert all(a.shared_with_count == 3 for a in awards)


def test_single_company_on_a_multiple_award_vehicle_is_not_credited_the_ceiling():
    a = one("Integrity Technologies Corp.,* Grand Forks, North Dakota, is being awarded a "
            "$55,000,000,000 multiple award firm-fixed price, indefinite-delivery/indefinite-"
            "quantity Worldwide Expeditionary multiple award contract. The contract will run "
            "concurrently with other announced awardees under the contract.")
    assert a.kind == "shared_award" and a.amount_usd is None
    assert a.shared_amount_usd == 55_000_000_000 and a.shared_with_count is None
    b = one("Fujifilm Sonosite Inc., Bothell, Washington, has been awarded a maximum $957,714,000 "
            "firm-fixed-price, indefinite-delivery/indefinite-quantity contract for ultrasound "
            "systems. Other contracts are expected to be awarded under this solicitation "
            "(SPE2D1-26-R-0002) and awardees will compete for a portion of the maximum dollar value.")
    assert b.kind == "shared_award" and b.amount_usd is None
    c = one("Oracle America Inc., Austin, Texas, is awarded a $90,000,000 single-award, "
            "firm-fixed-price contract (N00039-26-D-0001). It is not a multiple award contract.")
    assert c.kind == "award" and c.amount_usd == 90_000_000


def test_compete_for_each_order_is_not_each_ambiguity():
    awards = parse_chunk(
        "Engineering Partners Inc.,* Hilo, Hawaii (W9128A-26-D-A019); and MK Engineers Ltd.,* "
        "Mililani, Hawaii (W9128A-26-D-A021), will compete for each order of the $9,900,000 "
        "firm-fixed-price contract for architect-engineer services.", **CTX)
    assert all(a.notes == [] for a in awards)


def test_amount_in_a_total_value_sentence():
    a = one("ITC Defense Corp, North Charleston, South Carolina, is being awarded a competitive "
            "cost-plus-fixed-fee contract. The total value of this contract is $47,457,037. The "
            "Missile Defense Agency, Huntsville, Alabama, is the contracting activity "
            "(HQ0851-26-C-0001).")
    assert a.amount_usd == 47_457_037
    b = one("Hive Group LLC,* McLean, Virginia, is being awarded a competitive cost-plus-fixed-fee "
            "contract. The total value of this contract, including all options, is $82,000,000. "
            "The Missile Defense Agency, Huntsville, Alabama, is the contracting activity "
            "(HQ0851-26-C-0002).")
    assert b.amount_usd == 82_000_000 and b.amount_qualifier == "including options"
    c = one("Verus Research LLC, Huntsville, Alabama, was awarded a cost-plus-fixed-fee contract "
            "with a maximum ceiling of $ 94,949,999 for laser effects research. Air Force Research "
            "Laboratory, Kirtland, New Mexico, is the contracting activity (FA9451-26-D-0001).")
    assert c.amount_usd == 94_949_999 and c.amount_qualifier in ("maximum", "ceiling")


def test_are_sharing_and_is_receiving():
    awards = parse_chunk(
        "US Vanadium LLC,* Hot Springs National Park, Arkansas (SP8000-26-D-0015) and Largo "
        "Resources USA Inc.,* McLean, Virginia (SP8000-26-D-0016) are sharing a maximum "
        "$400,000,000 firm-fixed-price contract for vanadium.", **CTX)
    assert [a.company for a in awards] == ["US Vanadium LLC", "Largo Resources USA Inc."]
    assert all(a.kind == "shared_award" and a.shared_amount_usd == 400_000_000 for a in awards)
    b = one("CACI Inc.-Federal, Chantilly, Virginia, is receiving a $12,000,000 cost-plus-fixed-fee "
            "modification (P00001) to contract N32205-26-C-0001.")
    assert b.company == "CACI Inc.-Federal" and b.amount_usd == 12_000_000


def test_companies_with_contract_numbers_but_no_location():
    awards = parse_chunk(
        "VIASAT Inc., (FA880726FB004), and INTELSAT General Communications LLC, (FA880726FB005), "
        "have been awarded a combined $437,665,005 firm-fixed-price contract.", **CTX)
    assert [a.company for a in awards] == ["VIASAT Inc.", "INTELSAT General Communications LLC"]
    assert all(a.review_reasons == [] and "no location" in a.notes[0] for a in awards)


def test_stray_continuation_paragraph_is_glued_to_the_award_above():
    html = ("<title>Contracts for July 9, 2026</title><div class=\"body\">"
            "<p><strong>NAVY</strong></p>"
            "<p>Acme Corp., Dallas, Texas, is awarded a $20,000,000 contract (N00024-26-C-0001).</p>"
            "<p>This contract was awarded on June 30, 2026.</p></div>")
    page = parse_page(html, source_url="s", archive_url="a")
    assert len(page.awards) == 1 and page.awards[0].text.endswith("June 30, 2026.")


def test_update_without_an_award_verb_is_a_correction():
    a = one("UPDATE: The $19,791,630 firm-fixed-price contract action (W15QKN-26-F-A236) announced "
            "on July 28, 2026, was actually for a different program.")
    assert a.kind == "correction"


def test_malformed_headline_amount_is_a_review_item():
    a = one("The Boeing Co., Long Beach, California, has been awarded a $13,781,0130 "
            "cost-plus-fixed-fee contract for cargo ramp lock actuators. The Air Force Life Cycle "
            "Management Center, Robins AFB, Georgia, is the contracting activity (FA8526-26-C-0001).")
    assert a.amount_usd is None
    assert any("malformed" in r for r in a.review_reasons)


# --------------------------------------------------- multi-company awards

def test_shared_pool_is_not_credited_to_each_company():
    text = ("Clark Construction Group LLC, Bethesda, Maryland (W519TC-27-D-A005); BAE Systems "
            "Ordnance Systems Inc., Kingsport, Tennessee (W519TC-27-D-A014); and MSM Group North "
            "America Inc., St. Petersburg, Florida (W519TC-27-D-A015), will compete for each order "
            "of the $4,171,491,000 firm-fixed-price contract for Army ammunition plant design. Army "
            "Contracting Command, Rock Island Arsenal, Illinois, is the contracting activity.")
    awards = parse_chunk(text, **CTX)
    assert [a.company for a in awards] == [
        "Clark Construction Group LLC", "BAE Systems Ordnance Systems Inc.", "MSM Group North America Inc."]
    assert all(a.kind == "shared_award" and a.amount_usd is None for a in awards)
    assert all(a.shared_amount_usd == 4_171_491_000 and a.shared_with_count == 3 for a in awards)
    assert awards[1].contract_number == "W519TC-27-D-A014"   # each firm keeps its own number
    assert awards[0].action_type == "contract"


def test_each_company_with_its_own_amount():
    text = ("Cardinal Health, Waukegan, Illinois (SPE2DV-27-D-0001, $1,500,444,621); McKesson "
            "Medical-Surgical Government Solutions LLC, Henrico, Virginia (SPE2DV-27-D-0003, "
            "$741,496,756); and Cardinal Health, Waukegan, Illinois (SPE2DV-27-D-0004, $82,388,528), "
            "have each been awarded fixed-price, indefinite-delivery/indefinite-quantity contracts "
            "under solicitation SPE2DV-26-R-0001 for the Medical Surgical Prime Vendor program.")
    awards = parse_chunk(text, **CTX)
    assert [a.amount_usd for a in awards] == [1_500_444_621, 741_496_756, 82_388_528]
    assert all(a.kind == "award" and a.is_idiq for a in awards)


def test_update_adds_an_awardee():
    a = one("UPDATE: Cepheid, Sunnyvale, California (SPE2DE-27-D-0001, $49,000,000) has been added "
            "as an awardee to the multiple award contract for laboratory equipment, issued against "
            "solicitation (SPE2DE-22-R-0006) and awarded Feb. 24, 2023.")
    assert a.kind == "added_awardee" and a.company == "Cepheid" and a.amount_usd == 49_000_000
    assert a.contract_number == "SPE2DE-27-D-0001"


def test_correction_is_never_counted_as_an_award():
    a = one("CORRECTION: The modification (P00018) to contract W56HZV-22-D-0004 announced on Oct. "
            "14, 2026, to Oshkosh Defense LLC, Oshkosh, Wisconsin, was actually awarded for $28,811,667.")
    assert a.kind == "correction" and a.amount_usd is None


def test_companies_joined_by_and_are_split():
    text = ("Raytheon Co., Tucson, Arizona, and Lockheed Martin Corp., Orlando, Florida, were "
            "awarded a shared $500,000,000 contract for missile work. Army Contracting Command, "
            "Redstone Arsenal, Alabama, is the contracting activity (W31P4Q-27-D-0001).")
    awards = parse_chunk(text, **CTX)
    assert [(a.company, a.city) for a in awards] == [
        ("Raytheon Co.", "Tucson"), ("Lockheed Martin Corp.", "Orlando")]
    assert all(a.shared_amount_usd == 500_000_000 and a.amount_usd is None for a in awards)


def test_and_inside_a_company_name_is_not_a_split():
    a = one("Systems Planning and Analysis Inc., Alexandria, Virginia, has been awarded a "
            "$30,752,715 modification (P00031) to previously awarded contract FA8806-25-F-B002. "
            "The Space Systems Command, Los Angeles Air Force Base, California, is the "
            "contracting activity.")
    assert a.company == "Systems Planning and Analysis Inc." and a.review_reasons == []


def test_combined_after_the_amount_is_not_a_shared_pool():
    a = one("Acme Corp., Dallas, Texas, was awarded a $50,000,000 combined-arms training "
            "contract. Army Contracting Command, Orlando, Florida, is the contracting activity "
            "(W900KK-27-C-0001).")
    assert a.kind == "award" and a.amount_usd == 50_000_000


def test_plural_verb_with_one_company_is_flagged():
    a = one("Acme Corp. and Beta Inc., Dallas, Texas, were awarded a $20,000,000 contract. Army "
            "Contracting Command, Orlando, Florida, is the contracting activity (W900KK-27-C-0002).")
    assert any("plural verb" in r for r in a.review_reasons)


# -------------------------------------------------- paragraph splitting

def test_two_awards_run_together_are_split():
    text = ("Accenture Federal Services LLC, Arlington, Virginia, has been awarded a $151,462,808 "
            "firm-fixed-price task order for engineering services. The Air Force Nuclear Weapons "
            "Center, Hill AFB, Utah, is the contracting activity (FA8204-27-F-B001). (Awarded on Oct. "
            "23, 2026). Amentum Technology Inc., Tullahoma, Tennessee, has been awarded a $16,390,078 "
            "definitive contract for System Integration Laboratory services. The Space Systems "
            "Command, Peterson Space Force Base, Colorado, is the contracting activity (FA8723-27-C-B001).")
    chunks = split_awards(text)
    assert len(chunks) == 2 and chunks[1].startswith("Amentum")
    assert "(Awarded on Oct. 23, 2026)." in chunks[0]


def test_this_contract_was_awarded_sentence_is_not_a_new_award():
    text = ("Acme Corp., Dallas, Texas, is awarded a $20,000,000 contract. Naval Air Systems Command, "
            "Patuxent River, Maryland, is the contracting activity (N00019-26-C-0001). This contract "
            "was awarded Dec. 19, 2025.")
    assert len(split_awards(text)) == 1


@pytest.mark.parametrize("sentence,amount", [
    ("The contracts share a programmatic ceiling of $25,357,000,000.", 25_357_000_000),
    ("The shared ceiling for all three contracts is $49,000,000 with the companies competing.", 49_000_000),
    ("The maximum dollar value for all five contracts combined is $99,613,488.", 99_613_488),
    ("The estimated aggregate ceiling for all contracts is $70,000,000, with orders competed.", 70_000_000),
])
def test_pool_amount_in_a_later_sentence(sentence, amount):
    awards = parse_chunk(
        "General Dynamics Mission Systems Inc., Taunton, Massachusetts (HQ072726DE003); and Leidos "
        "Inc., Reston, Virginia (HQ072726DE005), are awarded indefinite-delivery/indefinite-quantity "
        "contracts for missile defense engineering. " + sentence, **CTX)
    assert all(a.kind == "shared_award" and a.shared_amount_usd == amount for a in awards)
    assert all(a.review_reasons == [] for a in awards)


def test_pool_change_with_from_to_figures_stays_flagged():
    awards = parse_chunk(
        "Contrack Watts Inc., McLean, Virginia (N44255-21-D-5003); and Manson Construction Co., "
        "Seattle, Washington (N44255-21-D-5004), are awarded bilateral modifications. The "
        "modifications reflect an increase in the shared aggregate maximum ordering capacity from "
        "$326,000,000 to $380,000,000, representing a total increase of $54,000,000.", **CTX)
    assert all("shared award without a pool amount" in a.review_reasons for a in awards)


@pytest.mark.parametrize("sentence,amount,qual", [
    ("The value of this contract will be $48,098,000.", 48_098_000, None),
    ("The total value of the modification award is $179,900,000.", 179_900_000, None),
    ("The maximum dollar value, including the base period and four option years, is $90,000,000.",
     90_000_000, "including options"),
    ("The overall estimated value of this BPA is $9,690,000,000.", 9_690_000_000, "estimated"),
    ("This is a firm-fixed-price order with a value of $9,309,352 and more.", 9_309_352, None),
    ("The maximum contract ceiling, including all ordering years, is $43,331,000.", 43_331_000, "ceiling"),
])
def test_amount_from_a_value_sentence(sentence, amount, qual):
    a = one("Acme Corp., Dallas, Texas, is awarded a firm-fixed-price contract (N00024-26-C-0009) "
            "for widgets. " + sentence)
    assert a.amount_usd == amount and a.amount_qualifier == qual
    assert "amount taken from a later sentence" in a.notes and a.review_reasons == []


def test_ceiling_change_from_to_is_not_guessed():
    a = one("Raytheon Co., Woburn, Massachusetts, is being awarded a contract modification (P00038) "
            "that increases the contract ceiling from $2,605,033,000 to $2,999,000,000.")
    assert a.amount_usd is None and "no dollar amount found" in a.review_reasons


def test_agency_as_subject_is_not_a_company():
    awards = parse_chunk("USTRANSCOM has awarded a firm-fixed price contract, with a face value of "
                         "$10,915,926.84, to SSA Atlantic LLC, Jacksonville, Florida.", **CTX)
    assert awards[0].company is None
    assert "could not find 'awarded' in the text" in awards[0].review_reasons


def test_company_of_city_and_no_location_forms():
    a = one("Lockheed Martin Space of Titusville, Florida, is being awarded an $18,867,588 "
            "cost-plus-fixed-fee modification (P00022) to contract HQ0851-21-C-0001.")
    assert (a.company, a.city, a.state_or_country) == ("Lockheed Martin Space", "Titusville", "Florida")
    b = one("Raytheon Co., is being awarded a $773,500,000 noncompetitive contract modification "
            "(P00031) to contract HQ0147-22-C-0001.")
    assert b.company == "Raytheon Co." and b.review_reasons == [] and "no location" in " ".join(b.notes)


def test_one_company_with_two_locations():
    a = one("FlightSafety Inc., Columbus, Ohio; and Savannah, Georgia, has been awarded a "
            "firm-fixed-requirements contract with a contract maximum of $45,221,572 for Gulfstream "
            "training (FA8617-24-D-6001).")
    assert a.company == "FlightSafety Inc." and a.city == "Columbus"
    assert any("second location" in n for n in a.notes)


# ------------------------------------------------ found in the human audit

def test_order_under_a_multiple_award_contract_is_the_companys_own_money():
    a = one("Fairwinds Technologies LLC,* Annapolis, Maryland, is awarded $11,744,357 for "
            "firm-fixed-price delivery order N00104-26-F-E63T under previously awarded Defense "
            "Logistics Agency Maritime Acquisition Advancement Contract multiple-award, "
            "indefinite-delivery contract SPRMM125CMD01 for the repair of 100 electron tubes.")
    assert a.kind == "award" and a.amount_usd == 11_744_357


def test_per_company_amounts_in_later_sentences():
    awards = parse_chunk(
        "General Dynamics Electric Boat Corp., Groton, Connecticut (N00024-17-C-2100); and Huntington "
        "Ingalls Inc., Newport News Shipbuilding, Newport News, Virginia (N00024-21-C-2106), are "
        "awarded contract modifications for construction of two Virginia-class submarines. General "
        "Dynamics Electric Boat Corp. is being awarded $12,418,145,463, and if all options are "
        "exercised the total value will be $17,152,265,971; and Huntington Ingalls Inc., Newport "
        "News Shipbuilding, is being awarded $1,293,694,000.", **CTX)
    assert [(a.kind, a.amount_usd) for a in awards] == [
        ("award", 12_418_145_463), ("award", 1_293_694_000)]


def test_first_city_state_pair_is_used():
    a = one("Seemann Composites LLC., Gulfport, Mississippi (a wholly owned subsidiary of Karman "
            "Space & Defense), Huntington Beach, California, is awarded a $31,315,866 contract "
            "(N00167-26-D-1002).")
    assert (a.company, a.city, a.state_or_country) == ("Seemann Composites LLC.", "Gulfport", "Mississippi")


def test_implausibly_large_firm_amount_needs_human_confirmation():
    a = one("Tyto Government Solutions Inc., Reston, Virginia, was awarded a $51,384,801,016 "
            "firm-fixed-price contract for Cyber Transport Systems sustainment (FA8771-26-C-0001).")
    assert a.amount_usd == 51_384_801_016          # never changed: it is what the source says
    assert any("confirm against the source" in r for r in a.review_reasons)


def test_will_share():
    awards = parse_chunk(
        "The Arora Group Inc., Gaithersburg, Maryland (HT001524D9000); and Vesa Health & Technology "
        "Inc., San Antonio, Texas (HT001524D9010), will share a multiple award, indefinite-delivery/"
        "indefinite-quantity contract with a maximum cumulative ceiling of $43,000,000,000.", **CTX)
    assert [a.company for a in awards] == ["The Arora Group Inc.", "Vesa Health & Technology Inc."]
    assert all(a.kind == "shared_award" and a.shared_amount_usd == 43e9 for a in awards)


def test_will_be_awarded():
    a = one("InDyne Inc., Lexington Park, Maryland, will be awarded a ceiling $1,104,000,000 "
            "indefinite-delivery/indefinite-quantity contract for missile warning (FA2517-26-D-0001).")
    assert a.company == "InDyne Inc." and a.amount_usd == 1_104_000_000


def test_place_names_with_periods_do_not_cause_false_splits():
    text = ("Boeing Co., St. Louis, Missouri, is awarded a $10,000,000 contract. The contracting "
            "activity is Naval Air Systems Command, St. Louis, Missouri. Work is in St. Louis.")
    assert len(split_awards(text)) == 1


# ---------------------------------------------- whole pages (all fixtures)

def _pages():
    index = json.loads((FIXTURES / "index.json").read_text())
    for e in index:
        html = gzip.decompress((FIXTURES / e["file"]).read_bytes()).decode("utf-8", "replace")
        yield e, parse_page(html, source_url=e["source_url"], archive_url=e["archive_url"])


@pytest.mark.parametrize("entry,page", list(_pages()), ids=lambda x: getattr(x, "title", ""))
def test_fixture_page_invariants(entry, page):
    assert page.announcement_date.isoformat() in {a.announcement_date for a in page.awards}
    for a in page.awards:
        assert a.source_url == entry["source_url"] and a.archive_url == entry["archive_url"]
        assert a.text and a.branch != "UNKNOWN"
        if a.kind == "correction":
            continue
        assert a.company, a.text[:80]
        # Every award either has a number or says why it doesn't.
        has_money = a.amount_usd is not None or a.shared_amount_usd is not None
        assert has_money or a.review_reasons, a.text[:80]
        # Every number we show must appear literally in the source text.
        for v in (a.amount_usd, a.shared_amount_usd, a.cumulative_face_value_usd,
                  a.value_with_options_usd):
            if v is not None:
                assert f"{v:,.0f}" in a.text or f"{v:,.2f}" in a.text, (v, a.text[:80])


def test_fixture_totals_are_stable():
    """A snapshot of the 12 saved days. If the parser changes, this tells us."""
    awards = [a for _, p in _pages() for a in p.awards]
    kinds = {k: sum(a.kind == k for a in awards) for k in
             ("award", "shared_award", "added_awardee", "correction")}
    # 104/40 (was 105/39): Fujifilm's "maximum $957,714,000" turned out to be the
    # ceiling of a multiple-award contract, so it moved from award to shared_award.
    assert kinds == {"award": 104, "shared_award": 40, "added_awardee": 5, "correction": 7}
    fuji = next(a for a in awards if a.company == "Fujifilm Sonosite Inc.")
    assert fuji.kind == "shared_award"
