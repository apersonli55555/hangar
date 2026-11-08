"""How award rows turn into the numbers on the site.

The key rule (say this in an interview): a contract announcement's dollar
figure means different things, so we never add them all together.

  definite   a stated award amount for this company (contract, modification, order)
  potential  a ceiling, maximum, estimate or IDIQ: what the government COULD
             order, often far more than it ever does
  excluded   not counted at all, with a reason:
               shared pool  (split among several companies; whose share is unknown)
               repeat       (same text announced again)
               correction   (fixes an earlier announcement)
               needs review (the parser or a human flagged the headline number)
               no amount    (the source gives none)

Neither definite nor potential is revenue: contracts pay out over years.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date

POTENTIAL_QUALIFIERS = {"maximum", "ceiling", "estimated", "including options"}

# Branch headings as written in the source (typos included) -> chart group.
_BRANCH_GROUPS = {
    "NAVY": "Navy", "ARMY": "Army", "AIR FORCE": "Air Force", "SPACE FORCE": "Air Force",
}


def branch_group(branch: str) -> str:
    b = branch.upper()
    if b in _BRANCH_GROUPS:
        return _BRANCH_GROUPS[b]
    if "LOGIST" in b:                       # also catches the "LOGISTCS" typo
        return "Defense Logistics Agency"
    return "Other agencies"


BRANCH_ORDER = ["Navy", "Army", "Air Force", "Defense Logistics Agency", "Other agencies"]


def classify(row: dict) -> tuple[str, str]:
    """Return (category, reason) for one row of data/awards.csv."""
    kind = row["kind"]
    if kind == "correction":
        return "excluded", "correction"
    if kind == "repeat":
        return "excluded", "repeat"
    if kind == "shared_award":
        return "excluded", "shared pool"
    if row["review_reasons"]:
        return "excluded", "needs review"
    if not row["amount_usd"]:
        return "excluded", "no amount"
    if row["amount_qualifier"] in POTENTIAL_QUALIFIERS:
        return "potential", ""
    # A NEW indefinite-delivery contract is a ceiling. An order or modification
    # under one is real money for that action, so it stays "definite".
    if row["is_idiq"] == "True" and row.get("action_type", "contract") == "contract":
        return "potential", ""
    return "definite", ""


def quarter(iso_date: str) -> str:
    d = date.fromisoformat(iso_date)
    return f"{d.year} Q{(d.month - 1) // 3 + 1}"


def all_quarters(first: str, last: str) -> list[str]:
    """Every quarter between two dates, so empty quarters still show as gaps."""
    y, q = int(first[:4]), (int(first[5:7]) - 1) // 3 + 1
    ly, lq = int(last[:4]), (int(last[5:7]) - 1) // 3 + 1
    out = []
    while (y, q) <= (ly, lq):
        out.append(f"{y} Q{q}")
        y, q = (y + 1, 1) if q == 4 else (y, q + 1)
    return out


def summarize(rows: list[dict], since: str | None = None) -> dict:
    """Totals for a set of rows (usually one ticker), optionally from a date on."""
    s = {"definite_usd": 0.0, "definite_n": 0, "potential_usd": 0.0, "potential_n": 0,
         "excluded": defaultdict(int)}
    for r in rows:
        if since and r["announcement_date"] < since:
            continue
        cat, reason = classify(r)
        if cat == "excluded":
            s["excluded"][reason] += 1
        else:
            s[f"{cat}_usd"] += float(r["amount_usd"])
            s[f"{cat}_n"] += 1
    s["excluded"] = dict(s["excluded"])
    return s


def quarterly_definite_by_branch(rows: list[dict]) -> dict[str, dict[str, float]]:
    """{quarter: {branch group: definite $}} for the chart."""
    out: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    for r in rows:
        if classify(r)[0] == "definite":
            out[quarter(r["announcement_date"])][branch_group(r["branch"])] += float(r["amount_usd"])
    return {q: dict(v) for q, v in out.items()}


def fmt_usd(v: float) -> str:
    """$1.23B / $456.7M / $12,345: short form for display. The exact figure
    is always available next to it (title attribute and the source link)."""
    if v >= 1e9:
        return f"${v / 1e9:,.2f}B"
    if v >= 1e6:
        return f"${v / 1e6:,.1f}M"
    return f"${v:,.0f}"
