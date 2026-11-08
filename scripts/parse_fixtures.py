"""Parse every saved fixture page and write data/preview_awards.csv.

    python -m scripts.parse_fixtures
"""
import csv
import gzip
import json
from collections import Counter
from pathlib import Path

from pipeline.parse import Award, parse_page

PAGES = Path("tests/fixtures/pages")
OUT = Path("data/preview_awards.csv")


def main() -> None:
    awards: list[Award] = []
    for entry in json.loads((PAGES / "index.json").read_text()):
        html = gzip.decompress((PAGES / entry["file"]).read_bytes()).decode("utf-8", "replace")
        page = parse_page(html, source_url=entry["source_url"], archive_url=entry["archive_url"])
        awards += page.awards
    OUT.parent.mkdir(exist_ok=True)
    with OUT.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(awards[0].to_row()))
        w.writeheader()
        w.writerows(a.to_row() for a in awards)

    print(f"{len(awards)} records -> {OUT}")
    print("by kind:", dict(Counter(a.kind for a in awards)))
    flagged = [a for a in awards if a.review_reasons]
    print(f"flagged for review: {len(flagged)}")
    for a in flagged:
        print(f"  {a.announcement_date} {a.company!r}: {'; '.join(a.review_reasons)}")


if __name__ == "__main__":
    main()
