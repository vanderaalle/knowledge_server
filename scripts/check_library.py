#!/usr/bin/env python3
"""
Audit the library against the conventions in library_checks.py and write a
review sheet. Read-only: it changes no file and no index entry.

Reports, one row per finding:
  location   file at the library root or outside BOOKS_DIR
  name       filename not in "Author - Title (Year)" form
  junk       looks like a figure/scrap rather than a book or paper
  text       no text layer (will be OCR'd) or a suspect/garbled one
  index      on disk but not in the index, or in the index but missing on disk

Usage:
  python scripts/check_library.py                     # writes BOOKS_DIR/library_audit.tsv
  python scripts/check_library.py --only name,junk    # restrict to some checks
"""

import argparse
import csv
import os
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import library_checks as lc
from pdf_processor import _calculate_file_hash

CHECKS = ("location", "name", "junk", "text", "index")


def inspect(path: str) -> dict:
    return {"path": path, "hash": _calculate_file_hash(Path(path)),
            "text": lc.text_layer_status(path), "junk": lc.junk_hint(path)}


def main():
    parser = argparse.ArgumentParser(description="Audit the library (read-only)")
    parser.add_argument("--only", default=",".join(CHECKS), help=f"Comma-separated subset of {CHECKS}")
    parser.add_argument("--out", default=os.path.join(lc.BOOKS_DIR, "library_audit.tsv"))
    args = parser.parse_args()
    only = set(args.only.split(","))

    files = sorted(str(p) for p in Path(lc.BOOKS_DIR).rglob("*") if p.suffix.lower() in lc.BOOK_EXTENSIONS)
    with ProcessPoolExecutor(max_workers=8) as ex:
        info = list(ex.map(inspect, files, chunksize=8))

    rows = []
    for i in info:
        rel = os.path.relpath(i["path"], lc.BOOKS_DIR)
        if "location" in only:
            rows += [("location", rel, p) for p in lc.location_problems(i["path"])]
        if "name" in only and lc.name_problems(i["path"]):
            rows.append(("name", rel, "; ".join(lc.name_problems(i["path"]))))
        if "junk" in only and i["junk"]:
            rows.append(("junk", rel, i["junk"]))
        if "text" in only and i["text"] != "text":
            rows.append(("text", rel, {"none": "no text layer (OCR at indexing)",
                                       "garbled": "suspect text layer: check by eye; force-OCR if garbled"}[i["text"]]))

    if "index" in only:
        from vector_db import get_db
        indexed = get_db().get_all_indexed_hashes()
        on_disk = {i["hash"] for i in info}
        for i in info:
            if i["hash"] not in indexed:
                rows.append(("index", os.path.relpath(i["path"], lc.BOOKS_DIR), "not in the index"))
        missing = indexed - on_disk
        if missing:
            rows.append(("index", "-", f"{len(missing)} indexed books whose file is gone (orphans)"))

    with open(args.out, "w", newline="") as f:
        w = csv.writer(f, delimiter="\t")
        w.writerow(["check", "file", "finding", "action (fill in)"])
        w.writerows(r + ("",) for r in rows)

    counts = {c: sum(1 for r in rows if r[0] == c) for c in CHECKS if c in only}
    print(f"{len(files)} files checked: " + ", ".join(f"{n} {c}" for c, n in counts.items()))
    print(f"Review sheet: {args.out}")


if __name__ == "__main__":
    main()
