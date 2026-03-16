#!/usr/bin/env python3
"""
Batch OCR indexer for high-priority no-text books.

Reads books tagged "no-text" in Calibre, filters by a priority list,
runs Tesseract OCR on each page, and indexes the result into Qdrant.

Usage:
  python ocr_priority.py            # process Tier 1 list
  python ocr_priority.py --all      # process all no-text books
  python ocr_priority.py --dry-run  # show what would be processed
"""

import sys
import os
import json
import subprocess

# Add parent dir so we can import from server.py
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

DRY_RUN = "--dry-run" in sys.argv
ALL_NO_TEXT = "--all" in sys.argv

# Load personal config (gitignored) — fall back to empty defaults
_config_path = os.path.join(os.path.dirname(__file__), "ocr_priority_config.py")
if os.path.exists(_config_path):
    import importlib.util
    _spec = importlib.util.spec_from_file_location("ocr_priority_config", _config_path)
    _cfg = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(_cfg)
    CALIBRE_LIBRARY = _cfg.CALIBRE_LIBRARY
    TIER1_KEYWORDS = _cfg.TIER1_KEYWORDS
    SKIP_KEYWORDS = getattr(_cfg, "SKIP_KEYWORDS", [])
else:
    print("⚠️  No ocr_priority_config.py found. Copy scripts/ocr_priority_config.example.py and edit it.")
    CALIBRE_LIBRARY = os.path.expanduser("~/Calibre Library")
    TIER1_KEYWORDS = []
    SKIP_KEYWORDS = []


def calibredb(*args):
    return subprocess.run(
        ["calibredb", *args, "--with-library", CALIBRE_LIBRARY],
        capture_output=True, text=True
    )


def get_no_text_books():
    result = calibredb("list", "--fields", "id,title,formats",
                       "--search", 'tags:"no-text"', "--limit", "99999", "--for-machine")
    books = []
    for b in json.loads(result.stdout):
        pdfs = [f for f in b.get("formats", []) if f.lower().endswith(".pdf")]
        if pdfs:
            books.append({"id": b["id"], "title": b["title"], "pdf": pdfs[0]})
    return books



def is_tier1(title: str) -> bool:
    t = title.lower()
    if any(kw in t for kw in SKIP_KEYWORDS):
        return False
    return any(kw in t for kw in TIER1_KEYWORDS)


def ocr_and_index(pdf_path: str):
    """OCR a single PDF and index it using server.py's full pipeline."""
    from server import _index_directory
    indexed, skipped, errors, ocr_skip = _index_directory(
        file_list=[pdf_path], use_ocr=True
    )
    return indexed > 0


def main():
    print("Loading no-text books from Calibre...")
    all_books = get_no_text_books()
    print(f"  {len(all_books)} no-text books found\n")

    if ALL_NO_TEXT:
        books = all_books
        print("Processing ALL no-text books\n")
    else:
        books = [b for b in all_books if is_tier1(b["title"])]
        print(f"Tier 1 priority: {len(books)} books matched\n")

    if DRY_RUN:
        print("DRY RUN — would process:")
        for b in books:
            print(f"  [{b['id']}] {b['title']}")
        return

    success = 0
    failed = 0
    for i, book in enumerate(books, 1):
        print(f"\n[{i}/{len(books)}] {book['title'][:70]}")
        print(f"  {book['pdf']}")
        try:
            if ocr_and_index(book["pdf"]):
                success += 1
            else:
                failed += 1
        except Exception as e:
            print(f"  ✗ Error: {e}")
            failed += 1

    print(f"\nDone. Indexed: {success} | Skipped/Failed: {failed}")


if __name__ == "__main__":
    main()
