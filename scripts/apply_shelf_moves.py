#!/usr/bin/env python3
"""
Apply a reviewed shelf-move sheet (BOOKS_DIR/shelf_review.tsv): move each book
to its new shelf and update its paths in every collection, without
re-embedding. Rows marked "n" are skipped; a shelf typed in the first column
overrides the proposed one; "~/Books Extra/debris" moves the file out of the
library and deletes its chunks.

Waits for a running re-index to finish (its lock file) so books are not moved
under it. Empty shelf folders left behind are removed.

Usage:
  python scripts/apply_shelf_moves.py --dry-run
  python scripts/apply_shelf_moves.py
"""

import argparse
import csv
import fcntl
import os
import shutil
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import library_checks as lc
from pdf_processor import _calculate_file_hash
from qdrant_client import QdrantClient, models
from vector_db import QDRANT_HOST, QDRANT_PORT

COLLECTIONS = ["pdf_library", "pdf_library_v2"]
STATE_DIR = os.path.expanduser("~/.local/share/knowledge_server")


def main():
    parser = argparse.ArgumentParser(description="Apply shelf_review.tsv")
    parser.add_argument("--sheet", default=os.path.join(lc.BOOKS_DIR, "shelf_review.tsv"))
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    # Don't move books while the nightly re-index is reading them.
    if not args.dry_run:
        lock = open(os.path.join(STATE_DIR, "reindex_pdf_library_v2.lock"), "w")
        print("Waiting for the re-index lock...", flush=True)
        fcntl.flock(lock, fcntl.LOCK_EX)

    client = QdrantClient(host=QDRANT_HOST, port=QDRANT_PORT)
    colls = [c for c in COLLECTIONS if client.collection_exists(c)]
    rows = list(csv.reader(open(args.sheet), delimiter="\t"))[1:]
    moved = skipped = debris = missing = 0
    for approve, cur, proposed, _why in rows:
        approve = approve.strip()
        if approve.lower() == "n":
            skipped += 1
            continue
        target = approve or proposed
        src = os.path.join(lc.BOOKS_DIR, cur)
        if not os.path.exists(src):
            print(f"  missing, skipped: {cur}")
            missing += 1
            continue
        name = os.path.basename(cur)
        to_debris = "debris" in target
        dest_dir = lc.DEBRIS_DIR if to_debris else os.path.join(lc.BOOKS_DIR, target)
        dest = os.path.join(dest_dir, name)
        if os.path.exists(dest):
            print(f"  destination exists, skipped: {dest}")
            missing += 1
            continue
        if args.dry_run:
            print(f"  {cur}  →  {target}")
            continue
        h = _calculate_file_hash(Path(src))
        os.makedirs(dest_dir, exist_ok=True)
        shutil.move(src, dest)
        flt = models.Filter(must=[models.FieldCondition(key="metadata.file_hash", match=models.MatchValue(value=h))])
        for c in colls:
            if to_debris:
                client.delete(c, points_selector=models.FilterSelector(filter=flt), wait=True)
            else:
                client.set_payload(c, key="metadata", points=flt, wait=True, payload={
                    "source": os.path.relpath(dest, lc.BOOKS_DIR), "source_path": dest})
        debris += to_debris
        moved += not to_debris

    if not args.dry_run:
        for dirpath, dirnames, filenames in sorted(os.walk(lc.BOOKS_DIR, topdown=False)):
            if dirpath != lc.BOOKS_DIR and not os.listdir(dirpath):
                os.rmdir(dirpath)
                print(f"  removed empty shelf: {os.path.relpath(dirpath, lc.BOOKS_DIR)}")
    print(f"{'Would move' if args.dry_run else 'Moved'} {moved if not args.dry_run else len(rows) - skipped - missing} "
          f"| debris {debris} | skipped (n) {skipped} | missing/collisions {missing}", flush=True)


if __name__ == "__main__":
    main()
