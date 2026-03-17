#!/usr/bin/env python3
"""
Batch OCR indexer for high-priority books not yet indexed.

Scans the Books directory for PDFs not yet in Qdrant, filters by
a priority keyword list, and OCR-indexes them.

Usage:
  python ocr_priority.py            # process Tier 1 list only
  python ocr_priority.py --all      # process all unindexed books
  python ocr_priority.py --dry-run  # show what would be processed
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

DRY_RUN = "--dry-run" in sys.argv
ALL_BOOKS = "--all" in sys.argv

# Load personal config (gitignored)
_config_path = os.path.join(os.path.dirname(__file__), "ocr_priority_config.py")
if os.path.exists(_config_path):
    import importlib.util
    _spec = importlib.util.spec_from_file_location("ocr_priority_config", _config_path)
    _cfg = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(_cfg)
    BOOKS_DIR = getattr(_cfg, "BOOKS_DIR", os.path.expanduser("~/Books"))
    TIER1_KEYWORDS = _cfg.TIER1_KEYWORDS
    SKIP_KEYWORDS = getattr(_cfg, "SKIP_KEYWORDS", [])
else:
    print("⚠️  No ocr_priority_config.py found. Copy scripts/ocr_priority_config.example.py and edit it.")
    BOOKS_DIR = os.path.expanduser("~/Books")
    TIER1_KEYWORDS = []
    SKIP_KEYWORDS = []


def get_indexed_paths():
    """Return set of source_paths already in Qdrant."""
    from qdrant_client import QdrantClient
    client = QdrantClient(host=os.getenv("QDRANT_HOST", "localhost"),
                          port=int(os.getenv("QDRANT_PORT", "6333")))
    collection = os.getenv("COLLECTION_NAME", "pdf_library")
    paths = set()
    offset = None
    while True:
        pts, offset = client.scroll(collection_name=collection, limit=500,
                                     offset=offset, with_payload=True, with_vectors=False)
        for p in pts:
            sp = (p.payload or {}).get("metadata", {}).get("source_path", "")
            if sp:
                paths.add(sp)
        if offset is None:
            break
    return paths


def is_tier1(filename: str) -> bool:
    name = filename.lower()
    if any(kw.lower() in name for kw in SKIP_KEYWORDS):
        return False
    return any(kw.lower() in name for kw in TIER1_KEYWORDS)


def ocr_and_index(pdf_path: str):
    from server import _index_directory
    indexed, skipped, errors, ocr_skip = _index_directory(
        file_list=[pdf_path], use_ocr=True
    )
    return indexed > 0


def main():
    print(f"Scanning {BOOKS_DIR} for unindexed PDFs...")
    all_pdfs = [os.path.join(BOOKS_DIR, f)
                for f in os.listdir(BOOKS_DIR) if f.lower().endswith(".pdf")]

    print("Loading indexed paths from Qdrant...")
    indexed_paths = get_indexed_paths()

    unindexed = [p for p in all_pdfs if p not in indexed_paths]
    print(f"  {len(all_pdfs)} PDFs total, {len(unindexed)} not yet indexed\n")

    if ALL_BOOKS:
        books = unindexed
        print(f"Processing ALL {len(books)} unindexed books\n")
    else:
        books = [p for p in unindexed if is_tier1(os.path.basename(p))]
        print(f"Tier 1 priority: {len(books)} books matched\n")

    if DRY_RUN:
        print("DRY RUN — would process:")
        for p in books:
            print(f"  {os.path.basename(p)}")
        return

    success = 0
    failed = 0
    for i, pdf_path in enumerate(books, 1):
        print(f"\n[{i}/{len(books)}] {os.path.basename(pdf_path)[:70]}")
        try:
            if ocr_and_index(pdf_path):
                success += 1
            else:
                failed += 1
        except Exception as e:
            print(f"  ✗ Error: {e}")
            failed += 1

    print(f"\nDone. Indexed: {success} | Skipped/Failed: {failed}")


if __name__ == "__main__":
    main()
