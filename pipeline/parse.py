"""Turn one daily "Contracts for <date>" page into structured award records.

Design rules (these are the ones to explain in an interview):

1. Never guess. Every field is either extracted by an explicit rule or left
   empty, and every empty/odd field adds a human-readable reason to
   `review_reasons`. The site shows those awards with a "needs review" badge.
2. Keep the evidence. Each record stores the exact paragraph text it came
   from plus links to the official page and the archived copy.
3. Don't double count. Awards shared by several companies ("will compete for
   each order of the $X contract") keep the shared amount in
   `shared_amount_usd` and leave `amount_usd` empty, so per-company totals
   never add the same dollars once per company.

Only the Python standard library is used.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field, asdict
from datetime import date
from html.parser import HTMLParser

# --------------------------------------------------------------------------
# Step 1: HTML -> (heading, paragraph) list
# --------------------------------------------------------------------------


class _BodyExtractor(HTMLParser):
    """Collects the <p> paragraphs inside <div class="body">, remembering
    whether each one is a bold heading such as ARMY or NAVY."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.in_body = False
        self.div_depth = 0
        self.current: list[str] | None = None
        self.bold_lead = False
        self.paragraphs: list[tuple[bool, str]] = []
        self.in_title = False
        self.title = ""

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "title":
            self.in_title = True
        if tag == "div" and not self.in_body and "body" in (attrs.get("class") or "").split():
            self.in_body, self.div_depth = True, 0
        if not self.in_body:
            return
        if tag == "div":
            self.div_depth += 1
        elif tag == "p":
            self.current, self.bold_lead = [], False
        elif tag in ("strong", "b") and self.current is not None and not "".join(self.current).strip():
            self.bold_lead = True  # bold text at the very start of the paragraph
        elif tag == "br" and self.current is not None:
            self.current.append(" ")

    def handle_endtag(self, tag):
        if tag == "title":
            self.in_title = False
        if not self.in_body:
            return
        if tag == "p" and self.current is not None:
            text = " ".join("".join(self.current).split())  # collapse whitespace
            if text:
                self.paragraphs.append((self.bold_lead, text))
            self.current = None
        elif tag == "div":
            self.div_depth -= 1
            if self.div_depth == 0:
                self.in_body = False

    def handle_data(self, data):
        if self.in_title:
            self.title += data
        if self.current is not None:
            self.current.append(data)


# --------------------------------------------------------------------------
# Small helpers: dates, money, contract numbers
# --------------------------------------------------------------------------

_MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}
_DATE_RE = re.compile(r"\b(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sept?|Oct|Nov|Dec)[a-z]*\.?\s+(\d{1,2}),\s+(\d{4})")


def parse_date(text: str) -> date | None:
    m = _DATE_RE.search(text)
    if not m:
        return None
    return date(int(m.group(3)), _MONTHS[m.group(1)[:3].lower()], int(m.group(2)))


# "$1,234,567" or "$1,234,567.89", optionally "$4.2 billion". The lookahead
# rejects malformed numbers such as the source typo "$42,758,8967".
_MONEY_RE = re.compile(
    r"\$ ?(\d{1,3}(?:,\d{3})*(?:\.\d{1,2})?|\d+(?:\.\d+)?)(?!\d|,\d)(\s+(?:million|billion))?")
_MONEY_TOKEN_RE = re.compile(r"\$[\d,]*\d(?:\.\d+)?")
_WELL_FORMED_RE = re.compile(r"\d{1,3}(?:,\d{3})*(?:\.\d{1,2})?|\d+(?:\.\d+)?")


def has_malformed_money(text: str) -> bool:
    """True if any '$...' figure isn't a properly comma-grouped number,
    e.g. the source typo '$42,758,8967'."""
    return any(not _WELL_FORMED_RE.fullmatch(t[1:]) for t in _MONEY_TOKEN_RE.findall(text))


def money_values(text: str) -> list[float]:
    out = []
    for m in _MONEY_RE.finditer(text):
        value = float(m.group(1).replace(",", ""))
        if m.group(2):
            value *= 1e9 if "billion" in m.group(2) else 1e6
        out.append(value)
    return out


def first_money(text: str) -> float | None:
    vals = money_values(text)
    return vals[0] if vals else None


# DoD contract numbers (PIIDs): 6-char office code, 2-digit fiscal year,
# 1 letter for the instrument type, 4 serial characters, dashes optional.
# e.g. W912EK-22-D-0002, N0001923C0049, FA8672-19-D-0001, SPE2DE-27-D-0001
# Office codes usually start with a letter (W912EK), but GSA's can start with
# a digit (47QTCK); we require at least one letter somewhere in the code.
_PIID_RE = re.compile(r"\b([A-Z0-9]{6})-?(\d{2})-?([A-Z])-?([A-Z0-9]{4})\b")
# The type letter tells us what kind of document the number is.
PIID_TYPES = {"C": "contract", "D": "IDIQ contract", "F": "order", "G": "basic ordering agreement",
              "A": "agreement", "P": "BPA", "R": "solicitation", "Q": "solicitation", "M": "order"}
_MOD_RE = re.compile(r"\b(P\d{5}|A\d{5})\b")


def contract_numbers(text: str) -> list[str]:
    seen: list[str] = []
    for m in _PIID_RE.finditer(text):
        if not re.search(r"[A-Z]", m.group(1)):
            continue
        piid = "".join(m.groups())
        if piid not in seen:
            seen.append(piid)
    return seen


def piid_type(piid: str) -> str:
    return PIID_TYPES.get(piid[8], "unknown")


def format_piid(piid: str) -> str:
    """N0001923C0049 -> N00019-23-C-0049 (the dashed form people search for)."""
    return f"{piid[:6]}-{piid[6:8]}-{piid[8]}-{piid[9:]}"


# --------------------------------------------------------------------------
# Step 2: split a paragraph into award "chunks"
# --------------------------------------------------------------------------

# The verb that ends the company/location part of an award sentence.
# The company is the subject of a PASSIVE verb: "X was / is being / has been /
# have each been / will be awarded". Active "USTRANSCOM has awarded ... to X"
# deliberately does not match (the subject there is the agency, not the company).
_LEAD_VERB_RE = re.compile(
    r"\b(?:(?:was|is|were|are)\s+(?:each\s+)?(?:being\s+)?"
    r"|(?:has|have)\s+(?:each\s+)?been\s+(?:each\s+)?"
    r"|will\s+(?:each\s+)?(?:be\s+)?(?:each\s+)?)"
    r"(?:awarded|added as an awardee|compete for|sharing|share|receiving)\b")
# A previous award ends with "... is the contracting activity (X)." optionally
# followed by "(Awarded Oct. 23, 2026)."
_AWARD_END_RE = re.compile(
    r"contracting activity[^.]*?\.(?:\s*\(Awarded[^)]*\)\.?)?\s+(?=[A-Z])")


# Sentences that continue the award above rather than starting a new one.
_CONTINUATION_RE = re.compile(
    r"(This|The) (contract|order|task order|modification|action|award|effort)\b")


def split_awards(paragraph: str) -> list[str]:
    """Most paragraphs hold one award, but the source sometimes runs two
    together. Split after "...contracting activity." when more award text
    follows."""
    chunks, start = [], 0
    for m in _AWARD_END_RE.finditer(paragraph):
        rest = paragraph[m.end():]
        if _CONTINUATION_RE.match(rest):   # "This contract was awarded Dec. 19, 2025."
            continue
        if _LEAD_VERB_RE.search(rest[:400]):
            chunks.append(paragraph[start:m.end()].strip())
            start = m.end()
    chunks.append(paragraph[start:].strip())
    return [c for c in chunks if c]


# --------------------------------------------------------------------------
# Step 3: parse the "who" part: company, city, state (+ contract no.)
# --------------------------------------------------------------------------

US_STATES = {
    "Alabama", "Alaska", "Arizona", "Arkansas", "California", "Colorado", "Connecticut", "Delaware",
    "Florida", "Georgia", "Hawaii", "Idaho", "Illinois", "Indiana", "Iowa", "Kansas", "Kentucky",
    "Louisiana", "Maine", "Maryland", "Massachusetts", "Michigan", "Minnesota", "Mississippi",
    "Missouri", "Montana", "Nebraska", "Nevada", "New Hampshire", "New Jersey", "New Mexico",
    "New York", "North Carolina", "North Dakota", "Ohio", "Oklahoma", "Oregon", "Pennsylvania",
    "Rhode Island", "South Carolina", "South Dakota", "Tennessee", "Texas", "Utah", "Vermont",
    "Virginia", "Washington", "West Virginia", "Wisconsin", "Wyoming", "D.C.", "District of Columbia",
    "Puerto Rico", "Guam", "U.S. Virgin Islands",
}
SMALL_BUSINESS_MARKS = {"*": "small business", "**": "women-owned small business",
                        "***": "service-disabled veteran-owned small business"}


@dataclass
class Party:
    company: str
    city: str | None
    state_or_country: str | None
    small_business: str | None
    contract_numbers: list[str]
    own_amount_usd: float | None  # only for "(PIID, $X)" style lists
    note: str | None = None       # set when we repaired the source's punctuation


def _repair_location(tokens: list[str]) -> tuple[str, str, str] | None:
    """Fix two typos seen in the source, only when the fix is unambiguous:
      'Explus Inc., Sterling Virginia'          (comma missing before state)
      'Caddell Nova JV. Montgomery, Alabama'    (period instead of comma)"""
    if len(tokens) != 2:
        return None
    first, last = tokens
    for state in US_STATES:
        if last.endswith(" " + state) and len(last) > len(state) + 1:
            return first, last[: -len(state)].strip(), state
    if ". " in first:
        company, city = first.rsplit(". ", 1)
        return company + ".", city, last
    if " of " in first and last in US_STATES:   # "Lockheed Martin Space of Titusville, Florida"
        company, city = first.rsplit(" of ", 1)
        return company, city, last
    return None


def parse_party(text: str) -> Party:
    text = text.strip().rstrip(",").strip()
    # Pull out any "(...)" groups: they hold contract numbers and amounts.
    parens = re.findall(r"\(([^)]*)\)", text)
    text = re.sub(r"\s*\([^)]*\)", "", text).strip().rstrip(",").strip()
    piids, own_amount = [], None
    for p in parens:
        piids += contract_numbers(p)
        if own_amount is None:
            own_amount = first_money(p)
    # Small-business stars come right after the company name: "Inc.,* City"
    small = None
    m = re.search(r",?(\*{1,3})", text)
    if m:
        small = SMALL_BUSINESS_MARKS.get(m.group(1))
        text = (text[:m.start()] + "," + text[m.end():]).replace(",,", ",")
    tokens = [t.strip() for t in text.split(",") if t.strip()]
    note = None
    # Use the FIRST "City, State" pair: "Seemann Composites LLC., Gulfport, Mississippi
    # (a wholly owned subsidiary of ...), Huntington Beach, California" lists two.
    first_state = next((j for j in range(2, len(tokens) - 1) if tokens[j] in US_STATES), None)
    if first_state is not None:
        company = ", ".join(tokens[:first_state - 1])
        city, region = tokens[first_state - 1], tokens[first_state]
        note = "additional location listed: " + ", ".join(tokens[first_state + 1:])
    elif len(tokens) >= 3:
        company = ", ".join(tokens[:-2])
        city, region = tokens[-2], tokens[-1]
    elif (fixed := _repair_location(tokens)) is not None:
        company, city, region = fixed
        note = "source punctuation repaired in company/city/state"
    elif len(tokens) == 1:
        # e.g. "VIASAT Inc., (FA880726FB004)" or "Raytheon Co., is being awarded":
        # the source gives no location at all
        company, city, region = tokens[0], None, None
        note = "no location given in the source"
    else:
        company, city, region = ", ".join(tokens), None, None
    return Party(company=company, city=city, state_or_country=region, small_business=small,
                 contract_numbers=piids, own_amount_usd=own_amount, note=note)


def _ends_with_state(text: str) -> bool:
    cleaned = re.sub(r"\s*\([^)]*\)", "", text).rstrip(" ,*")
    return any(cleaned.endswith(s) for s in US_STATES)


def split_parties(lead: str) -> list[str]:
    """Split the 'who' part into one string per company.

    'A, City, ST (X); B, City, ST (Y); and C, City, ST (Z)' -> 3 strings.
    'A, City, ST, and B, City, ST' -> 2 strings. We only split at an "and"
    that directly follows a state name, because company names contain "and"
    too (e.g. "Systems Planning and Analysis Inc.").
    """
    if ";" in lead:
        parts = re.split(r";\s*(?:and\s+)?", lead)
    else:
        parts, start = [], 0
        for m in re.finditer(r",?\s+and\s+(?=[A-Z])", lead):
            prefix = lead[start:m.start()]
            if _ends_with_state(prefix) or contract_numbers(prefix[prefix.rfind("("):] if prefix.rstrip(" ,").endswith(")") else ""):
                parts.append(lead[start:m.start()])
                start = m.end()
        parts.append(lead[start:])
    return [re.sub(r"^and\s+", "", p.strip()) for p in parts if p.strip()]


# --------------------------------------------------------------------------
# Step 4: the award record
# --------------------------------------------------------------------------


@dataclass
class Award:
    # provenance
    announcement_date: str
    branch: str
    source_url: str
    archive_url: str
    paragraph_index: int          # position on the page, for linking/debugging
    text: str                     # the exact source text of this award
    # classification
    kind: str                     # award | shared_award | added_awardee | correction
    # who
    company: str | None = None
    city: str | None = None
    state_or_country: str | None = None
    small_business: str | None = None
    # how much
    amount_usd: float | None = None          # this company's headline amount
    amount_qualifier: str | None = None      # e.g. "maximum", "ceiling"
    shared_amount_usd: float | None = None   # pool shared by several companies
    shared_with_count: int | None = None
    cumulative_face_value_usd: float | None = None
    value_with_options_usd: float | None = None
    obligated_at_award_usd: float | None = None
    # what
    action_type: str | None = None           # contract | modification | order
    contract_number: str | None = None
    contract_number_type: str | None = None
    modification_number: str | None = None
    all_contract_numbers: list[str] = field(default_factory=list)
    award_date: str | None = None            # "(Awarded Oct. 28, 2026)" if stated
    is_idiq: bool = False
    involves_fms: bool = False
    # quality control
    # review_reasons: the headline company or amount may be wrong or missing.
    # notes: secondary info is missing or was repaired; headline numbers are fine.
    review_reasons: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def to_row(self) -> dict:
        row = asdict(self)
        row["all_contract_numbers"] = " ".join(self.all_contract_numbers)
        row["review_reasons"] = "; ".join(self.review_reasons)
        row["notes"] = "; ".join(self.notes)
        return row


_SHARED_RE = re.compile(r"\b(shared?|sharing|combined|compete for each order|compete for)\b", re.I)
# Pool amounts stated in a later sentence, in the exact phrasings seen in 2024-26.
_POOL_RE = re.compile(
    r"(?:shares? an? (?:programmatic )?ceiling of"
    r"|estimated aggregate ceiling for all contracts is"
    r"|shared ceiling for [^$.]{0,30}is"
    r"|maximum dollar value for [^$.]{0,30}combined is"
    r"|overall maximum ceiling value of)\s*(\$ ?[\d,.]+)", re.I)
# A single company named in a contract whose ceiling is shared with others.
_SHARED_VEHICLE_RE = re.compile(
    r"multiple[- ]award|other contracts (?:are|may be) expected to be awarded"
    r"|compete for a portion|concurrently with other|other (?:announced )?awardees", re.I)
# "No funds are being obligated" means $0. Careful: "no funds will expire"
# is about expiry, not obligation, so the verb must be "obligated".
_ZERO_OBLIGATION_RE = re.compile(
    r"\bno funds (?:are|were|will be|is|have been)\s+(?:being\s+)?obligated"
    r"|\b(?:funds|funding) will not be obligated", re.I)


def _sentences(text: str) -> list[str]:
    return re.split(r"(?<=[.;])\s+(?=[A-Z(])", text)


def _obligated(text: str, reasons: list[str]) -> float | None:
    sents = [s for s in _sentences(text) if "obligat" in s.lower()]
    if not sents:
        return None  # simply not stated; not an error
    # "...$582,062 (97%); and $15,838 (3%), of which $15,838 will expire..."
    # Everything after "of which" is a SUBSET of the money before it, so drop it.
    sents = [re.split(r",?\s+of which\b", s, maxsplit=1)[0] for s in sents]
    joined = " ".join(sents)
    if has_malformed_money(joined):
        reasons.append("obligated amount has a malformed number in the source")
        return None
    if re.search(r"\beach\b|\bfirst task order\b|\bsubsequently\b.*\$", joined):
        reasons.append("obligated amount wording is ambiguous")
        return None
    amounts = money_values(joined)
    zero = bool(_ZERO_OBLIGATION_RE.search(joined))
    if zero and not amounts:
        return 0.0
    if zero and amounts:
        reasons.append("obligated amount wording is ambiguous")
        return None
    return sum(amounts) if amounts else None


def _headline_amount(after_verb: str) -> tuple[float | None, str | None]:
    """Amount stated right after 'awarded', plus a qualifier word if any."""
    first_sentence = _sentences(after_verb)[0]
    m = _MONEY_RE.search(first_sentence)
    # "...ceiling from $2.6B to $3.0B": the first figure is the OLD value, not the award.
    if m and re.search(r"\bfrom\s*$", first_sentence[:m.start()]):
        m = None
    if m:
        before = first_sentence[:m.start()].lower()
        after = first_sentence[m.end():m.end() + 12].lower()
        qualifier = None
        for word in ("maximum", "shared", "combined"):
            if word in before.split()[-3:]:
                qualifier = word
        if after.strip().startswith("ceiling"):
            qualifier = "ceiling"
        elif after.strip().startswith("maximum"):     # "$15,000,000,000 maximum, firm-fixed-price ..."
            qualifier = "maximum"
        return money_values(m.group(0))[0], qualifier
    # Fallback phrasings seen in the source, each with the qualifier it implies
    money = r"(\$ ?[\d,.]+)"
    for pat, qual in (
            (r"amount of this action is " + money, None),
            (r"increasing by " + money, None),
            (r"total value of this (?:contract|order|modification), including all options, is " + money,
             "including options"),
            (r"total value of this (?:contract|order|modification) is " + money, None),
            (r"base value of this contract is " + money, None),
            (r"with a maximum ceiling of " + money, "ceiling")):
        m2 = re.search(pat, after_verb)
        if m2:
            return first_money(m2.group(1)), qual
    # General "the <kind of> value ... is $X" / "with a <kind of> value of $X".
    m3 = _VALUE_SENTENCE_RE.search(after_verb)
    if m3:
        words = m3.group(0).lower()
        qual = ("including options" if "option" in words else
                "ceiling" if "ceiling" in words else
                "maximum" if "maximum" in words else
                "estimated" if "estimated" in words else None)
        return first_money(m3.group("amt") or m3.group("amt2")), qual
    return None, None


_VALUE_WORDS = (r"(?:total |overall |estimated |maximum |cumulative |face |aggregate )*"
                r"(?:value|dollar value|contract value|contract award|contract ceiling)")
_VALUE_SENTENCE_RE = re.compile(
    r"\b" + _VALUE_WORDS + r"(?: \([^)]*\))?(?: (?:of|for) (?:this|the|all) [A-Za-z0-9 -]+?)?"
    r"(?:, including .*?,)? (?:is|will be) (?P<amt>\$ ?[\d,.]+)"
    r"|\b(?:with|has) an? " + _VALUE_WORDS + r" of (?P<amt2>\$ ?[\d,.]+)", re.I)


def _primary_contract(chunk: str, piids: list[str]) -> str | None:
    if not piids:
        return None
    # Best: the number in the final "contracting activity (X)" parenthesis.
    m = re.search(r"contracting activity[^.]*\(([^)]*)\)", chunk)
    if m:
        tail = contract_numbers(m.group(1))
        if tail:
            return tail[0]
    # Otherwise the first number that is not a solicitation or agreement.
    for p in piids:
        if piid_type(p) not in ("solicitation", "basic ordering agreement"):
            return p
    return piids[0]


def parse_chunk(chunk: str, *, branch: str, ann_date: date, source_url: str,
                archive_url: str, index: int) -> list[Award]:
    base = dict(announcement_date=ann_date.isoformat(), branch=branch, source_url=source_url,
                archive_url=archive_url, paragraph_index=index, text=chunk)

    body = chunk
    if re.match(r"^correction\b", body, re.I):
        return [Award(kind="correction", **base)]
    is_update = bool(re.match(r"^update\s*:", body, re.I))
    body = re.sub(r"^update\s*:\s*", "", body, flags=re.I)

    verb = _LEAD_VERB_RE.search(body)
    if not verb and is_update:
        # e.g. "UPDATE: The $19.8M contract announced on July 28 ... was actually ..."
        return [Award(kind="correction", **base)]
    if not verb:
        return [Award(kind="award", review_reasons=["could not find 'awarded' in the text"], **base)]
    lead, after = body[:verb.start()], body[verb.end():]
    verb_text = verb.group(0)

    reasons: list[str] = []
    notes: list[str] = []
    if len(lead) > 300 and ";" not in lead:
        reasons.append("company part is unusually long; the 'awarded' verb may have been missed")

    amount, qualifier = _headline_amount(after)
    if amount is not None and not _MONEY_RE.search(_sentences(after)[0]):
        notes.append("amount taken from a later sentence")
    if has_malformed_money(chunk):
        (reasons if amount is None else notes).append("source text contains a malformed dollar figure")
    lead_sentence = (verb_text + " " + _sentences(after)[0]).lower()
    # "shared"/"combined" only counts if it comes BEFORE the dollar figure
    # ("awarded a shared $98M"), not after ("a $50M combined-arms contract").
    dollar = after.find("$")
    shared = bool(_SHARED_RE.search(verb_text + " " + (after[:dollar] if dollar >= 0 else after[:60])))

    mod_m = _MOD_RE.search(chunk)
    if "modification" in lead_sentence or mod_m:
        action = "modification"
    elif re.search(r"\border\b", lead_sentence.replace("each order of", "")):
        action = "order"
    else:
        action = "contract"

    piids_all = contract_numbers(chunk)
    awarded_m = re.search(r"\(Awarded(?: on)?\s+([^)]*)\)", chunk)
    award_date = parse_date(awarded_m.group(1)) if awarded_m else None

    common = dict(
        action_type=action,
        modification_number=mod_m.group(1) if mod_m else None,
        all_contract_numbers=[format_piid(p) for p in piids_all],
        award_date=(award_date or ann_date).isoformat(),
        is_idiq=bool(re.search(r"indefinite[- ]delivery|indefinite[- ]quantity|\bIDIQ\b|multiple[- ]award",
                               chunk, re.I)) or qualifier in ("maximum", "ceiling"),
        involves_fms=bool(re.search(r"foreign military sale|\bFMS\b", chunk, re.I)),
    )
    cum = re.search(r"total cumulative face value[^$]{0,60}(\$[\d,.]+)", chunk)
    if cum:
        common["cumulative_face_value_usd"] = first_money(cum.group(1))
    opt = re.search(r"(?:if exercised[^$]{0,80}|total estimated value[^$]{0,40})(\$[\d,.]+)", chunk)
    if opt:
        common["value_with_options_usd"] = first_money(opt.group(1))

    parties = []
    for t in split_parties(lead):
        p = parse_party(t)
        # "FlightSafety Inc., Columbus, Ohio; and Savannah, Georgia, has been awarded":
        # a bare "City, State" after a company is a second work location, not a company.
        bare = re.fullmatch(r"([^,]+), ([^,]+)", p.company or "")
        if (parties and p.city is None and not p.contract_numbers and bare
                and bare.group(2) in US_STATES):
            notes.append(f"second location listed: {p.company}")
            continue
        if p.company:
            parties.append(p)
        elif parties:  # e.g. a stray "(N44255-26-D-1812);" with no company name
            parties[-1].contract_numbers += p.contract_numbers
            notes.append("source lists a contract number with no company name")

    awards = []
    multi = len(parties) > 1
    # Several companies but only ONE dollar figure means a shared pool, whatever
    # the wording ("ceiling", "combined", "estimated", "multiple award", ...).
    per_company = multi and any(p.own_amount_usd is not None for p in parties)
    pre_dollar = (verb_text + " " + after[:dollar]) if dollar >= 0 else verb_text
    # "General Dynamics Electric Boat Corp. is being awarded $12.4B ...; and Huntington
    # Ingalls Inc., Newport News Shipbuilding, is being awarded $1.3B": each company's
    # own amount stated in a later sentence.
    if multi and not per_company:
        found = []
        for p in parties:
            m = re.search(re.escape(p.company) + r"[^.;$]{0,40}?(?:is|was) (?:being )?awarded (\$ ?[\d,.]+)",
                          after)
            found.append(first_money(m.group(1)) if m else None)
        if all(v is not None for v in found):
            for p, v in zip(parties, found):
                p.own_amount_usd = v
            per_company = True
            notes.append("per-company amounts taken from later sentences")
    if multi and not per_company and amount is None:
        pool = _POOL_RE.search(chunk)
        if pool:
            amount, qualifier = first_money(pool.group(1)), "ceiling"
            notes.append("shared ceiling taken from a later sentence")
    for party in parties:
        r, n = list(reasons), list(notes)
        if party.note:
            n.append(party.note)
        if party.city is None and party.note is None:
            r.append("could not split company / city / state")
        if multi and not per_company:
            kind, own, shared_amt = "shared_award", None, amount
            if re.search(r"\beach\b(?! order)", pre_dollar):
                n.append("'each' wording: the amount may be per company rather than shared")
        elif multi:
            kind, own, shared_amt = "award", party.own_amount_usd, None
            if own is None:
                r.append("multi-company award without a per-company amount")
        elif (not is_update and action == "contract" and _SHARED_VEHICLE_RE.search(chunk)
              and not re.search(r"single[- ]award|\border\b[^.]{0,80}\bunder\b", chunk, re.I)):
            # Only for a NEW contract. An order or modification "under a multiple-award
            # contract" is the company's own money; the phrase describes the parent.
            # One company named, but the dollar figure is the ceiling of a
            # vehicle shared with awardees announced on other days.
            kind, own, shared_amt = "shared_award", None, amount
            n.append("one company named, but the amount is the ceiling of a multiple-award "
                     "contract shared with other awardees")
        else:
            kind = "added_awardee" if is_update else ("shared_award" if shared else "award")
            own = party.own_amount_usd if is_update else (None if shared else amount)
            shared_amt = amount if shared else None
        if kind != "shared_award" and own is None:
            r.append("no dollar amount found")
        if kind == "shared_award" and shared_amt is None:
            r.append("shared award without a pool amount")
        # Plausibility: a single firm amount of $10B+ is rare; a typo in the source
        # (e.g. "$51,384,801,016 ... for Cyber Transport Systems sustainment") must
        # not silently dominate totals. A human confirms it first.
        if (own is not None and own >= 10e9 and qualifier is None and not common["is_idiq"]):
            r.append("unusually large single amount ($10B+): confirm against the source")
        if not multi and re.match(r"(were|are|have|will)\b", verb_text):
            r.append("plural verb but only one company found")
        piids = party.contract_numbers or piids_all
        primary = (party.contract_numbers[0] if party.contract_numbers
                   else _primary_contract(chunk, piids_all))
        obligated = _obligated(chunk, n) if not multi else None
        if not piids:
            n.append("no contract number found")
        awards.append(Award(
            kind=kind, company=party.company, city=party.city,
            state_or_country=party.state_or_country, small_business=party.small_business,
            amount_usd=own, amount_qualifier=qualifier if own is not None else None,
            shared_amount_usd=shared_amt,
            # How many companies share the pool: known only when they're all listed here.
            shared_with_count=len(parties) if (shared_amt and multi) else None,
            obligated_at_award_usd=obligated,
            contract_number=format_piid(primary) if primary else None,
            contract_number_type=piid_type(primary) if primary else None,
            review_reasons=r, notes=n, **common, **base))
    return awards


# --------------------------------------------------------------------------
# Step 5: whole page
# --------------------------------------------------------------------------


@dataclass
class ParsedPage:
    announcement_date: date
    title: str
    awards: list[Award]
    skipped: list[str]            # footnotes etc. that are deliberately ignored


def _is_heading(bold: bool, text: str) -> bool:
    return (bold or text.isupper()) and len(text) < 90 and "$" not in text


def _is_footnote(text: str) -> bool:
    return text.startswith("*") and len(text) < 160


def parse_page(html: str, *, source_url: str, archive_url: str) -> ParsedPage:
    ex = _BodyExtractor()
    ex.feed(html)
    title = " ".join(ex.title.split())
    ann_date = parse_date(title)
    if ann_date is None:
        raise ValueError(f"cannot find the announcement date in page title: {title!r}")

    awards: list[Award] = []
    skipped: list[str] = []
    branch = "UNKNOWN"
    # First pass: (branch, index, text), gluing stray continuation paragraphs
    # such as "This contract was awarded on June 30, 2026." onto the award above.
    items: list[list] = []
    for i, (bold, text) in enumerate(ex.paragraphs):
        if _is_heading(bold, text):
            branch = text.strip().upper()
            continue
        if _is_footnote(text):
            skipped.append(text)
            continue
        continuation = (
            _CONTINUATION_RE.match(text)
            or (not _LEAD_VERB_RE.search(text) and re.match(r"(Work|Fiscal|Funds|Funding)\b", text)))
        if items and items[-1][0] == branch and continuation:
            items[-1][2] += " " + text
            continue
        items.append([branch, i, text])

    for branch, i, text in items:
        for chunk in split_awards(text):
            awards.extend(parse_chunk(chunk, branch=branch, ann_date=ann_date,
                                      source_url=source_url, archive_url=archive_url, index=i))
    if not awards:
        raise ValueError("page parsed to zero awards; the page layout may have changed")
    return ParsedPage(announcement_date=ann_date, title=title, awards=awards, skipped=skipped)
