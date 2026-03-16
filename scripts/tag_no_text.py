#!/usr/bin/env python3
"""
Scan Calibre library for PDFs with no extractable text,
then tag them with "no-text" in Calibre using calibredb.

Safe to re-run: already-tagged books are skipped.
The tag is purely organizational — no files are moved or deleted.
"""

import subprocess
import sys
import json
import fitz  # PyMuPDF

try:
    from config import CALIBRE_LIBRARY
except ImportError:
    CALIBRE_LIBRARY = os.path.expanduser("~/Calibre Library")
TAG = "no-text"
TEXT_THRESHOLD = 150  # characters across first 3 pages to be considered "has text"


def calibredb(*args):
    return subprocess.run(
        ["calibredb", *args, "--with-library", CALIBRE_LIBRARY],
        capture_output=True, text=True
    )


def get_all_books():
    """Return list of {id, title, pdf_path} for all books with a PDF format."""
    result = calibredb("list", "--fields", "id,title,formats", "--limit", "99999", "--for-machine")
    books_raw = json.loads(result.stdout)
    books = []
    for b in books_raw:
        pdfs = [f for f in b.get("formats", []) if f.lower().endswith(".pdf")]
        if pdfs:
            books.append({"id": b["id"], "title": b["title"], "pdf": pdfs[0]})
    return books


def get_existing_tags(book_id: int) -> list[str]:
    result = calibredb("list", "--fields", "id,tags", "--search", f"id:{book_id}", "--for-machine")
    data = json.loads(result.stdout)
    if data:
        tags = data[0].get("tags", [])
        if isinstance(tags, list):
            return tags
        if isinstance(tags, str) and tags:
            return [t.strip() for t in tags.split(",")]
    return []


def has_text(pdf_path: str) -> bool:
    """Return True if the PDF has meaningful extractable text."""
    try:
        doc = fitz.open(pdf_path)
        total_text = ""
        for i, page in enumerate(doc):
            total_text += page.get_text()
            if i >= 2:  # check first 3 pages
                break
        doc.close()
        return len(total_text.strip()) >= TEXT_THRESHOLD
    except Exception as e:
        print(f"  [error reading PDF: {e}]", file=sys.stderr)
        return True  # Don't tag files we can't open


def tag_book(book_id: int, existing_tags: list[str]):
    new_tags = list(existing_tags)
    if TAG not in new_tags:
        new_tags.append(TAG)
    calibredb("set_metadata", "--field", f"tags:{','.join(new_tags)}", str(book_id))


def main():
    print(f"Scanning Calibre library: {CALIBRE_LIBRARY}")
    books = get_all_books()
    print(f"Found {len(books)} books with PDF format\n")

    tagged = 0
    skipped_has_text = 0
    skipped_already_tagged = 0

    for i, book in enumerate(books, 1):
        book_id = book["id"]
        title = book["title"]
        pdf_path = book["pdf"]

        print(f"[{i}/{len(books)}] {title[:65]}", end=" ", flush=True)

        existing_tags = get_existing_tags(book_id)
        if TAG in existing_tags:
            print("→ already tagged, skip")
            skipped_already_tagged += 1
            continue

        if has_text(pdf_path):
            print("→ has text")
            skipped_has_text += 1
        else:
            print("→ NO TEXT — tagging")
            tag_book(book_id, existing_tags)
            tagged += 1

    print(f"\nDone.")
    print(f"  Tagged '{TAG}':    {tagged}")
    print(f"  Has text (skip):  {skipped_has_text}")
    print(f"  Already tagged:   {skipped_already_tagged}")


if __name__ == "__main__":
    main()
