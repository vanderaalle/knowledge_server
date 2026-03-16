#!/usr/bin/env python3
"""
Remove Qdrant chunks whose source_path no longer exists on disk.

Use this after removing or replacing books in Calibre to keep
the vector DB in sync with the actual library.

Usage:
  python cleanup_orphans.py           # dry run — show what would be removed
  python cleanup_orphans.py --apply   # actually delete orphan chunks
"""

import sys
import os
import argparse
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from qdrant_client import QdrantClient
from qdrant_client.http.models import PointIdsList

QDRANT_URL = os.getenv("QDRANT_URL", "http://localhost:6333")
COLLECTION_NAME = os.getenv("COLLECTION_NAME", "pdf_library")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="Delete orphan chunks")
    args = parser.parse_args()

    client = QdrantClient(QDRANT_URL)

    print("Scanning Qdrant chunks for missing source files...")

    orphan_ids = []
    path_counts = defaultdict(int)
    checked_paths = {}  # cache path -> exists

    offset = None
    total = 0
    while True:
        pts, next_offset = client.scroll(
            COLLECTION_NAME, limit=500, offset=offset,
            with_payload=True, with_vectors=False
        )
        for p in pts:
            total += 1
            path = (p.payload or {}).get("metadata", {}).get("source_path", "")
            if not path:
                continue
            if path not in checked_paths:
                checked_paths[path] = os.path.exists(path)
            if not checked_paths[path]:
                orphan_ids.append(p.id)
                path_counts[path] += 1

        if next_offset is None:
            break
        offset = next_offset

    print(f"\nTotal chunks scanned: {total}")
    print(f"Missing files:        {len(path_counts)}")
    print(f"Orphan chunks:        {len(orphan_ids)}")

    if path_counts:
        print("\nMissing files:")
        for path, count in sorted(path_counts.items()):
            print(f"  {count:4d} chunks — {path}")

    if not orphan_ids:
        print("\nNothing to clean up.")
        return

    if args.apply:
        print(f"\nDeleting {len(orphan_ids)} orphan chunks...")
        batch_size = 100
        for i in range(0, len(orphan_ids), batch_size):
            batch = orphan_ids[i:i + batch_size]
            client.delete(
                collection_name=COLLECTION_NAME,
                points_selector=PointIdsList(points=batch)
            )
        print("✅ Done.")
    else:
        print(f"\nRun with --apply to delete {len(orphan_ids)} orphan chunks.")


if __name__ == "__main__":
    main()
