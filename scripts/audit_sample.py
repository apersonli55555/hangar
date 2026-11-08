"""Draw a random, stratified sample of records for a human to check against
the source pages. Opens fine in Excel.

    python -m scripts.audit_sample            # writes data/audit_sample.csv

Why stratified: a purely random sample would be ~60% plain awards. We want to
look hardest at the cases where the parser made a judgment call.
"""
import csv
import random

GROUPS = [
    ("plain award", 10, lambda r: r["kind"] == "award" and not r["notes"] and not r["review_reasons"]),
    ("your 11 tickers", 8, lambda r: r["ticker"] in {"LMT", "RTX", "NOC", "GD", "LHX", "BA", "HII", "GE",
                                                      "TDG", "HWM", "KTOS"} and r["kind"] == "award"),
    ("shared pool", 5, lambda r: r["kind"] == "shared_award"),
    ("amount from a later sentence", 5, lambda r: "later sentence" in r["notes"]),
    ("one company, multiple-award ceiling", 3, lambda r: "multiple-award" in r["notes"]),
    ("repeat (not counted)", 3, lambda r: r["kind"] == "repeat"),
    ("flagged for review", 5, lambda r: bool(r["review_reasons"])),
]


def main(seed: int = 2026) -> None:
    rows = list(csv.DictReader(open("data/awards.csv", encoding="utf-8")))
    rng = random.Random(seed)  # fixed seed: the same sample every time you run it
    picked, seen = [], set()
    for name, n, test in GROUPS:
        pool = [r for r in rows if test(r) and id(r) not in seen]
        for r in rng.sample(pool, min(n, len(pool))):
            seen.add(id(r))
            picked.append({"check_group": name, **r})
    # plus the 5 largest single-company amounts: errors there hurt the most
    biggest = sorted((r for r in rows if r["amount_usd"] and id(r) not in seen),
                     key=lambda r: -float(r["amount_usd"]))[:5]
    picked += [{"check_group": "largest amounts", **r} for r in biggest]

    cols = ["check_group", "correct_Y_N", "your_comment", "announcement_date", "company", "ticker",
            "kind", "amount_usd", "amount_qualifier", "shared_amount_usd", "shared_with_count",
            "obligated_at_award_usd", "contract_number", "review_reasons", "notes",
            "archive_url", "source_url", "text"]
    with open("data/audit_sample.csv", "w", newline="", encoding="utf-8-sig") as f:  # -sig: Excel-friendly
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        for r in picked:
            w.writerow({**r, "correct_Y_N": "", "your_comment": ""})
    print(f"{len(picked)} records -> data/audit_sample.csv")


if __name__ == "__main__":
    main()
