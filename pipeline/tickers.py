"""Map contractor names to stock tickers.

Rules live in data/ticker_aliases.csv so they can be reviewed and edited
without touching code. Each rule is a regular expression matched against a
NORMALIZED company name (lowercase, punctuation removed, leading "the" dropped),
plus an optional date range, because ownership changes over time.

Statuses:
  public          -> has a ticker
  private         -> known private company (no ticker, on purpose)
  foreign_listed  -> parent trades abroad; out of scope for Phase 1
  joint_venture   -> never auto-mapped: a JV's revenue splits across owners
  (no match)      -> "unmapped": shows up in data/unmapped_companies.csv
"""
from __future__ import annotations

import csv
import re
from dataclasses import dataclass
from pathlib import Path

ALIASES = Path("data/ticker_aliases.csv")
_JV_RE = re.compile(r"\bjv\d*\b|\bjoint (venture|program|project)\b")


def normalize(name: str) -> str:
    s = name.lower().replace("&", " and ")
    s = re.sub(r"[–—\-/]", " ", s)       # dashes and slashes -> space
    s = re.sub(r"[^a-z0-9 ]", "", s)                # drop . , ' * ( ) etc.
    s = re.sub(r"\s+", " ", s).strip()
    return re.sub(r"^the ", "", s)


@dataclass
class Rule:
    pattern: str
    ticker: str
    parent: str
    status: str
    valid_from: str
    valid_to: str
    evidence: str
    note: str

    def applies(self, norm: str, on_date: str) -> bool:
        if self.valid_from and on_date < self.valid_from:
            return False
        if self.valid_to and on_date > self.valid_to:
            return False
        return re.search(self.pattern, norm) is not None


@dataclass
class Match:
    ticker: str
    parent: str
    pattern: str
    status: str


class TickerMapper:
    def __init__(self, rules: list[Rule]):
        self.rules = rules

    @classmethod
    def load(cls, path: Path = ALIASES) -> "TickerMapper":
        with path.open(newline="", encoding="utf-8") as f:
            rows = [r for r in csv.DictReader(f) if r["pattern"] and not r["pattern"].startswith("#")]
        return cls([Rule(**r) for r in rows])

    def match(self, company: str, on_date: str) -> Match | None:
        """First rule (in file order) that matches wins. `on_date` is the
        award date, so ownership changes are respected."""
        norm = normalize(company)
        if _JV_RE.search(norm):
            return Match("", "", "joint venture", "joint_venture")
        for rule in self.rules:
            if rule.applies(norm, on_date):
                return Match(rule.ticker, rule.parent, rule.pattern, rule.status)
        return None
