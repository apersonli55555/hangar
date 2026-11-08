"""One-off helper: save recent archived announcement pages as test fixtures.

    python -m scripts.save_fixtures 10
"""
import gzip
import json
import re
import sys
from pathlib import Path

from pipeline import http, sources

OUT = Path("tests/fixtures/pages")


def main(n: int) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    index = []
    for ann in sources.list_announcements(max_items=n):
        arch = sources.archived_copy(ann.source_url)
        if arch is None:
            print("no archive copy yet:", ann.title)
            continue
        m = re.search(r"/Article/(\d+)/", ann.source_url)
        name = f"{m.group(1)}.html.gz"
        (OUT / name).write_bytes(gzip.compress(http.get(sources.raw_archive_url(arch))))
        index.append({"file": name, "title": ann.title,
                      "source_url": ann.source_url, "archive_url": arch})
        print("saved", ann.title)
    (OUT / "index.json").write_text(json.dumps(index, indent=2))


if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 10)
