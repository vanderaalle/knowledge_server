#!/usr/bin/env python3
"""
Backfill full chunk text in Qdrant.

Previously, only the first 700 chars of each chunk were stored as the 'text'
payload field, making search_text blind to the rest. This script re-extracts
text from each source file and updates the payload with the full chunk text.

No re-embedding — vectors are unchanged.

Usage:
  python backfill_full_text.py           # dry run — show how many chunks need update
  python backfill_full_text.py --apply   # apply updates
"""

import sys
import os
import argparse
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from qdrant_client import QdrantClient
from qdrant_client.http.models import PointIdsList
from pdf_processor import PDFProcessor, SimpleTextSplitter

QDRANT_URL = os.getenv("QDRANT_URL", "http://localhost:6333")
COLLECTION_NAME = os.getenv("COLLECTION_NAME", "pdf_library")

CHUNK_SIZE = 800
CHUNK_OVERLAP = 150


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="Apply updates")
    args = parser.parse_args()

    client = QdrantClient(QDRANT_URL)
    processor = PDFProcessor()

    print("Scanning Qdrant chunks...")

    # Group point IDs by source_path, keyed by chunk_index
    # { source_path: { chunk_index: (point_id, current_text) } }
    by_source = defaultdict(dict)
    offset = None
    total = 0

    while True:
        pts, next_offset = client.scroll(
            COLLECTION_NAME, limit=500, offset=offset,
            with_payload=True, with_vectors=False
        )
        for p in pts:
            total += 1
            meta = (p.payload or {}).get("metadata", {})
            source_path = meta.get("source_path", "")
            chunk_index = meta.get("chunk_index")
            text = (p.payload or {}).get("text", "")
            if source_path and chunk_index is not None:
                by_source[source_path][chunk_index] = (p.id, text)
        if next_offset is None:
            break
        offset = next_offset

    print(f"Total chunks: {total}")
    print(f"Unique source files: {len(by_source)}")

    # Detect truncated chunks (text ends abruptly at ~700 chars)
    truncated_sources = {}
    for source_path, chunks in by_source.items():
        truncated = sum(1 for _, (_, text) in chunks.items() if len(text) >= 690)
        if truncated > 0:
            truncated_sources[source_path] = truncated

    print(f"Files with likely truncated chunks: {len(truncated_sources)}")

    if not args.apply:
        print(f"\nRun with --apply to update chunks in {len(truncated_sources)} files.")
        return

    updated = 0
    failed = 0

    for source_path, chunks in by_source.items():
        if not os.path.exists(source_path):
            continue

        try:
            suffix = os.path.splitext(source_path)[1].lower()
            if suffix == ".epub":
                text, page_texts, total_pages, _ = processor.extract_text_from_epub(source_path)
            else:
                text, page_texts, total_pages, _ = processor.extract_text_from_pdf(source_path)

            if not text.strip():
                continue

            # Re-chunk to get full texts
            splitter = SimpleTextSplitter(chunk_size=CHUNK_SIZE, chunk_overlap=CHUNK_OVERLAP)
            chunk_texts = splitter.split_text(text)

            # Update each chunk that has a matching index
            for chunk_index, full_text in enumerate(chunk_texts):
                if chunk_index not in chunks:
                    continue
                point_id, stored_text = chunks[chunk_index]
                if stored_text == full_text:
                    continue  # already correct

                client.set_payload(
                    collection_name=COLLECTION_NAME,
                    payload={"text": full_text},
                    points=[point_id]
                )
                updated += 1

            if updated % 1000 == 0 and updated > 0:
                print(f"  Updated {updated} chunks so far...")

        except Exception as e:
            print(f"  ⚠️  Failed: {source_path}: {e}")
            failed += 1

    print(f"\n✅ Done. Updated: {updated} chunks | Failed files: {failed}")


if __name__ == "__main__":
    main()
