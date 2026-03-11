#!/usr/bin/env python3
"""
Sync LLM-generated titles from Qdrant to Calibre.

For each book in Calibre that has a PDF indexed in Qdrant:
- Reads the document_title from Qdrant (set by fix_titles.py)
- Updates the Calibre title via calibredb set_metadata

Matching is done by PDF file path (source_path in Qdrant == PDF path in Calibre).

Usage:
  python sync_titles_to_calibre.py           # dry run (preview only)
  python sync_titles_to_calibre.py --apply   # actually update Calibre
"""

import subprocess
import sys
import json
import os

CALIBRE_LIBRARY = "/home/andrea/Calibre Library"
QDRANT_HOST = os.getenv("QDRANT_HOST", "localhost")
QDRANT_PORT = int(os.getenv("QDRANT_PORT", "6333"))
COLLECTION = os.getenv("COLLECTION_NAME", "pdf_library")

DRY_RUN = "--apply" not in sys.argv


def calibredb(*args):
    return subprocess.run(
        ["calibredb", *args, "--with-library", CALIBRE_LIBRARY],
        capture_output=True, text=True
    )


def get_calibre_books():
    """Return dict: pdf_path -> {id, title}"""
    result = calibredb("list", "--fields", "id,title,formats", "--limit", "99999", "--for-machine")
    books = {}
    for b in json.loads(result.stdout):
        pdfs = [f for f in b.get("formats", []) if f.lower().endswith(".pdf")]
        if pdfs:
            books[pdfs[0]] = {"id": b["id"], "title": b["title"]}
    return books


def get_qdrant_titles():
    """Return dict: source_path -> document_title (one entry per unique document)"""
    from qdrant_client import QdrantClient
    client = QdrantClient(host=QDRANT_HOST, port=QDRANT_PORT)

    titles = {}  # source_path -> (title, original_title, best_page)
    offset = None
    scanned = 0

    while True:
        pts, next_offset = client.scroll(
            COLLECTION, limit=500, offset=offset,
            with_payload=True, with_vectors=False
        )
        for p in pts:
            meta = (p.payload or {}).get("metadata", {})
            path = meta.get("source_path")
            title = meta.get("document_title")
            orig = meta.get("document_title_original")
            page = meta.get("page_number", 9999)

            if not path or not title:
                continue
            # Keep the entry from the lowest page number (most representative)
            if path not in titles or page < titles[path][2]:
                titles[path] = (title, orig, page)

        scanned += len(pts)
        print(f"  Scanned {scanned} chunks...", end="\r", flush=True)
        if next_offset is None:
            break
        offset = next_offset

    print(f"  Scanned {scanned} chunks total.         ")
    # Return just path -> title
    return {path: t[0] for path, t in titles.items()}


def is_good_title(title: str) -> bool:
    """Filter out OCR garbage and clearly wrong titles."""
    if not title or len(title.strip()) < 4:
        return False
    # Reject titles with too many non-alphanumeric chars (OCR garbage)
    alnum = sum(c.isalnum() or c.isspace() for c in title)
    if alnum / len(title) < 0.7:
        return False
    # Reject titles that are clearly institution/place names used as titles
    garbage_patterns = ["PDF Version by", "Prepared exclusively for", "UNKNOWN"]
    if any(p.lower() in title.lower() for p in garbage_patterns):
        return False
    return True


def update_calibre_title(book_id: int, new_title: str):
    calibredb("set_metadata", "--field", f"title:{new_title}", str(book_id))


def main():
    if DRY_RUN:
        print("DRY RUN mode — pass --apply to actually update Calibre\n")
    else:
        print("APPLY mode — updating Calibre titles\n")

    print("Loading Calibre library...")
    calibre_books = get_calibre_books()
    print(f"  {len(calibre_books)} books with PDF\n")

    print("Loading Qdrant titles...")
    qdrant_titles = get_qdrant_titles()
    print(f"  {len(qdrant_titles)} unique documents in Qdrant\n")

    updated = 0
    unchanged = 0
    not_in_qdrant = 0

    for pdf_path, book in calibre_books.items():
        calibre_title = book["title"]
        book_id = book["id"]
        qdrant_title = qdrant_titles.get(pdf_path)

        if not qdrant_title:
            not_in_qdrant += 1
            continue

        if qdrant_title == calibre_title:
            unchanged += 1
            continue

        # Skip titles that look like OCR garbage or are clearly wrong
        if not is_good_title(qdrant_title):
            print(f"[{book_id}] SKIP (garbage): {qdrant_title[:55]}")
            not_in_qdrant += 1  # count as skipped
            continue

        print(f"[{book_id}] {calibre_title[:55]}")
        print(f"     → {qdrant_title[:55]}")

        if not DRY_RUN:
            update_calibre_title(book_id, qdrant_title)

        updated += 1

    print(f"\n{'Would update' if DRY_RUN else 'Updated'}: {updated}")
    print(f"Already matching: {unchanged}")
    print(f"Not in Qdrant:    {not_in_qdrant}")

    if DRY_RUN and updated > 0:
        print("\nRun with --apply to apply these changes.")


if __name__ == "__main__":
    main()
