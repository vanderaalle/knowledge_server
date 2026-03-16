#!/usr/bin/env python3
"""
Audit which Calibre books are indexed in Qdrant (by file hash).

Outputs three lists:
  - Indexed: Calibre books found in Qdrant
  - Not indexed: Calibre books missing from Qdrant
  - Summary counts

Usage:
  python audit_calibre_coverage.py
  python audit_calibre_coverage.py --missing   # print only missing books
"""

import sys
import os
import json
import hashlib
import subprocess

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    from config import CALIBRE_LIBRARY
except ImportError:
    CALIBRE_LIBRARY = os.path.expanduser("~/Calibre Library")
QDRANT_HOST = os.getenv("QDRANT_HOST", "localhost")
QDRANT_PORT = int(os.getenv("QDRANT_PORT", "6333"))
COLLECTION = os.getenv("COLLECTION_NAME", "pdf_library")

MISSING_ONLY = "--missing" in sys.argv


def calibredb(*args):
    return subprocess.run(
        ["calibredb", *args, "--with-library", CALIBRE_LIBRARY],
        capture_output=True, text=True
    )


def get_calibre_books():
    result = calibredb("list", "--fields", "id,title,formats", "--limit", "99999", "--for-machine")
    books = []
    for b in json.loads(result.stdout):
        pdfs = [f for f in b.get("formats", []) if f.lower().endswith(".pdf")]
        if pdfs:
            books.append({"id": b["id"], "title": b["title"], "pdf": pdfs[0]})
    return books


def md5(path: str) -> str:
    h = hashlib.md5()
    try:
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                h.update(chunk)
        return h.hexdigest()
    except Exception:
        return ""


def get_qdrant_hashes() -> set:
    from qdrant_client import QdrantClient
    client = QdrantClient(host=QDRANT_HOST, port=QDRANT_PORT)
    hashes = set()
    offset = None
    scanned = 0
    while True:
        pts, next_offset = client.scroll(
            COLLECTION, limit=500, offset=offset,
            with_payload=["metadata.file_hash"], with_vectors=False
        )
        for p in pts:
            h = (p.payload or {}).get("metadata", {}).get("file_hash")
            if h:
                hashes.add(h)
        scanned += len(pts)
        print(f"  Scanned {scanned} Qdrant chunks...", end="\r", flush=True)
        if next_offset is None:
            break
        offset = next_offset
    print(f"  Scanned {scanned} chunks → {len(hashes)} unique hashes    ")
    return hashes


def main():
    print("Loading Calibre books...")
    books = get_calibre_books()
    print(f"  {len(books)} books with PDF\n")

    print("Loading Qdrant hashes...")
    qdrant_hashes = get_qdrant_hashes()
    print()

    indexed = []
    missing = []
    errors = []

    for i, book in enumerate(books, 1):
        print(f"  Hashing [{i}/{len(books)}] {book['title'][:60]}", end="\r", flush=True)
        h = md5(book["pdf"])
        if not h:
            errors.append(book)
        elif h in qdrant_hashes:
            indexed.append(book)
        else:
            missing.append(book)

    print(f"  Done hashing {len(books)} books.                              \n")

    if not MISSING_ONLY:
        print(f"=== INDEXED ({len(indexed)}) ===")
        for b in indexed:
            print(f"  [{b['id']}] {b['title']}")
        print()

    print(f"=== NOT INDEXED ({len(missing)}) ===")
    for b in missing:
        print(f"  [{b['id']}] {b['title']}")

    if errors:
        print(f"\n=== ERRORS (could not read PDF) ({len(errors)}) ===")
        for b in errors:
            print(f"  [{b['id']}] {b['title']}")

    print(f"\n{'='*40}")
    print(f"Total Calibre books with PDF : {len(books)}")
    print(f"Indexed in Qdrant            : {len(indexed)} ({100*len(indexed)//len(books)}%)")
    print(f"Not indexed                  : {len(missing)} ({100*len(missing)//len(books)}%)")
    if errors:
        print(f"Errors (unreadable PDF)      : {len(errors)}")


if __name__ == "__main__":
    main()
