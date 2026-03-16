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

CALIBRE_LIBRARY = "/home/andrea/Calibre Library"
DRY_RUN = "--dry-run" in sys.argv
ALL_NO_TEXT = "--all" in sys.argv

# Tier 1: high-value books to OCR first (matched against Calibre title, case-insensitive substring)
TIER1_KEYWORDS = [
    "russolo",
    "rosenblueth",
    "edvac",
    "cybernetics",
    "ecological approach",
    "notes on the synthesis of form",
    "languages of art",
    "inquiries into truth",
    "simondon",
    "vampyroteuthis",
    "pandora",
    "song books",
    "cage silence",
    "die reihe",
    "xenakis",
    "kagel",
    "nono",
    "boulez",
    "traite de l'orchestration",
    "traité de l'orchestration",
    "new musical resources",
    "harmony book",
    "tuning, timbre",
    "microsound",
    "technology of computer music",
    "elements of computer music",
    "origins of order",
    "course in general linguistics",
    "tesniere",
    "tesnière",
    "bertin",
    "metaphors on vision",
    "on weaving",
    "partch",
    "mindstorms",
    "deep learning with python",
    "concrete mathematics",
    "thinking in postscript",
    "audible past",
    "cracked media",
    "cinema by other means",
    "composing electronic music",
    "sound poetry",
    "wireless imagination",
    "composing with tape",
    "african fractals",
    "emergence-from-chaos",
    "theory of recursive function",
    "giant brains",
    "studies in the way of words",
    "readings in zoosemiotics",
    "cowell",
    "designing sound",
    "persichetti",
    "bailey",
    "berlioz",
    "piston",
    "oxford history of music",
    "american minimal music",
]


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


SKIP_KEYWORDS = ["kagel", "cybernetics", "at home in the universe"]

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
