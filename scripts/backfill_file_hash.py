#!/usr/bin/env python3
"""
Backfill file_hash for Qdrant chunks that are missing it.

For each chunk without file_hash:
- Reads source_path from metadata
- Computes MD5 of the file
- Updates the chunk metadata with file_hash

This is safe to re-run (skips chunks that already have file_hash).
After running, is_file_indexed() will correctly skip already-indexed books.

Usage:
  python backfill_file_hash.py
"""

import os
import sys
import hashlib

QDRANT_HOST = os.getenv("QDRANT_HOST", "localhost")
QDRANT_PORT = int(os.getenv("QDRANT_PORT", "6333"))
COLLECTION = os.getenv("COLLECTION_NAME", "pdf_library")

BATCH_SIZE = 200


def compute_md5(path: str) -> str:
    h = hashlib.md5()
    try:
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(8192), b""):
                h.update(chunk)
        return h.hexdigest()
    except Exception:
        return ""


def main():
    from qdrant_client import QdrantClient

    client = QdrantClient(host=QDRANT_HOST, port=QDRANT_PORT)

    print(f"Scanning {COLLECTION} for chunks without file_hash...")

    # Collect all point IDs that need backfilling, grouped by source_path
    # to avoid re-hashing the same file for every chunk
    path_to_ids: dict[str, list] = {}
    already_done = 0
    missing_path = 0
    offset = None
    scanned = 0

    while True:
        pts, next_offset = client.scroll(
            COLLECTION, limit=500, offset=offset,
            with_payload=True, with_vectors=False
        )
        for p in pts:
            meta = (p.payload or {}).get("metadata", {})
            if meta.get("file_hash"):
                already_done += 1
                continue
            path = meta.get("source_path", "")
            if not path:
                missing_path += 1
                continue
            if path not in path_to_ids:
                path_to_ids[path] = []
            path_to_ids[path].append(p.id)

        scanned += len(pts)
        print(f"  Scanned {scanned} chunks, {len(path_to_ids)} unique paths to fix...", end="\r", flush=True)

        if next_offset is None:
            break
        offset = next_offset

    total_to_fix = sum(len(ids) for ids in path_to_ids.values())
    print(f"\n  Already have hash: {already_done}")
    print(f"  Need backfill:     {total_to_fix} chunks across {len(path_to_ids)} files")
    print(f"  No source_path:    {missing_path}\n")

    fixed_chunks = 0
    fixed_files = 0
    skipped_missing = 0

    for i, (path, point_ids) in enumerate(path_to_ids.items(), 1):
        if i % 50 == 0 or i == len(path_to_ids):
            print(f"  [{i}/{len(path_to_ids)}] {fixed_chunks} chunks updated...", end="\r", flush=True)

        if not os.path.exists(path):
            skipped_missing += 1
            continue

        file_hash = compute_md5(path)
        if not file_hash:
            skipped_missing += 1
            continue

        # Update in batches
        for start in range(0, len(point_ids), BATCH_SIZE):
            batch = point_ids[start:start + BATCH_SIZE]
            fetched = client.retrieve(
                collection_name=COLLECTION,
                ids=batch,
                with_payload=True,
                with_vectors=False,
            )
            for fp in fetched:
                old_payload = fp.payload or {}
                old_meta = old_payload.get("metadata", {})
                new_meta = {**old_meta, "file_hash": file_hash}
                client.overwrite_payload(
                    collection_name=COLLECTION,
                    payload={**old_payload, "metadata": new_meta},
                    points=[fp.id],
                )
            fixed_chunks += len(batch)

        fixed_files += 1

    print(f"\n\nDone.")
    print(f"  Files backfilled:    {fixed_files}")
    print(f"  Chunks updated:      {fixed_chunks}")
    print(f"  Files not found:     {skipped_missing}")
    print(f"  Already had hash:    {already_done} chunks")


if __name__ == "__main__":
    main()
