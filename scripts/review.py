"""Print a data-quality review of data/awards.csv.

    python -m scripts.review [--top N]
"""
import csv
import sys
from collections import Counter


def main(top: int = 40) -> None:
    rows = list(csv.DictReader(open("data/awards.csv", encoding="utf-8")))
    awards = [r for r in rows if r["kind"] != "correction"]
    print(f"{len(rows)} records ({len(awards)} excluding corrections)")
    print("kind:", dict(Counter(r["kind"] for r in rows)))
    print("ticker status:", dict(Counter(r["ticker_status"] for r in awards)))
    print("tickers:", Counter(r["ticker"] for r in awards if r["ticker"]).most_common())

    flagged = [r for r in rows if r["review_reasons"]]
    print(f"\n--- flagged for review: {len(flagged)} ({len(flagged) / max(len(rows), 1):.1%})")
    print(dict(Counter(reason for r in flagged for reason in r["review_reasons"].split("; "))))
    noted = [r for r in rows if r.get("notes")]
    print(f"notes (headline fine, secondary info missing/repaired): {len(noted)}")
    print(dict(Counter(n for r in noted for n in r["notes"].split("; "))))
    for r in flagged[:top]:
        print(f"  {r['announcement_date']} {r['company']} | {r['review_reasons']}\n      {r['text'][:160]}")

    print("\n--- largest single-company amounts (sanity check: should all be real)")
    big = sorted((r for r in rows if r["amount_usd"]), key=lambda r: -float(r["amount_usd"]))
    for r in big[:12]:
        i = r["text"].find("awarded")
        print(f"  ${float(r['amount_usd']):>16,.0f}  {r['ticker'] or '-':6} {r['company'][:40]:40} "
              f"| {r['text'][i:i + 80]}")

    print(f"\n--- top unmapped companies (by dollars)")
    for u in list(csv.DictReader(open("data/unmapped_companies.csv", encoding="utf-8")))[:top]:
        print(f"  {u['company']:<70} {u['records']:>3}  ${float(u['amount_usd']):>16,.0f}")


if __name__ == "__main__":
    main(int(sys.argv[sys.argv.index("--top") + 1]) if "--top" in sys.argv else 40)
