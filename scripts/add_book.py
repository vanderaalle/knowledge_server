#!/usr/bin/env python3
"""
Full pipeline for adding a new book to the knowledge base.

Steps:
  1. Copy PDF to ~/Books/ (if not already there)
  2. Index it (with optional OCR)
  3. Set the title from the filename (strips brackets, year, suffixes)
  4. Clean up any orphan chunks

Usage:
  python add_book.py /path/to/book.pdf
  python add_book.py /path/to/book.pdf --ocr        # force OCR (scanned book)
  python add_book.py --scan                          # index + fix all new books in ~/Books/
"""

import sys
import os
import shutil
import argparse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    from config import BOOKS_DIR
    BOOKS_DIR = os.path.expanduser(BOOKS_DIR)
except ImportError:
    BOOKS_DIR = os.path.expanduser("~/Books")


def step_copy(src: str) -> str:
    """Copy PDF to ~/Books/ if it's not already there. Returns final path."""
    src = os.path.abspath(src)
    if os.path.dirname(src) == BOOKS_DIR:
        print(f"  Already in Books folder: {os.path.basename(src)}")
        return src
    dest = os.path.join(BOOKS_DIR, os.path.basename(src))
    if os.path.exists(dest):
        print(f"  Already exists in Books: {os.path.basename(dest)}")
        return dest
    shutil.copy2(src, dest)
    print(f"  Copied → {dest}")
    return dest


def step_index(path: str, use_ocr: bool):
    """Index a single file or directory."""
    from server import _index_directory
    if os.path.isdir(path):
        indexed, skipped, errors, ocr_skip = _index_directory(path, use_ocr=use_ocr)
    else:
        indexed, skipped, errors, ocr_skip = _index_directory(file_list=[path], use_ocr=use_ocr)
    print(f"  Indexed: {indexed} | Skipped: {skipped} | Errors: {errors} | OCR skipped: {ocr_skip}")
    return indexed


def title_from_filename(pdf_path: str) -> str:
    """Derive a clean title from the PDF filename."""
    import re
    name = os.path.splitext(os.path.basename(pdf_path))[0]
    # Strip leading [N] or (N) markers
    name = re.sub(r'^\[\d+\]\s*', '', name)
    name = re.sub(r'^\(\d+\)\s*', '', name)
    # Strip trailing _indexed, _ocr, _ridotto suffixes
    name = re.sub(r'[_\s]+(indexed|ocr|ridotto|reduced|compressed)$', '', name, flags=re.IGNORECASE)
    # Replace underscores with spaces
    name = name.replace('_', ' ')
    return name.strip()


def step_fix_title(pdf_path: str):
    """Set document title from filename (stripped of brackets, year, suffixes)."""
    from qdrant_client import QdrantClient

    QDRANT_HOST = os.getenv("QDRANT_HOST", "localhost")
    QDRANT_PORT = int(os.getenv("QDRANT_PORT", "6333"))
    COLLECTION = os.getenv("COLLECTION_NAME", "pdf_library")

    client = QdrantClient(host=QDRANT_HOST, port=QDRANT_PORT)

    all_pts = []
    offset = None
    while True:
        pts, offset = client.scroll(collection_name=COLLECTION, limit=500,
                                     offset=offset, with_payload=True, with_vectors=False)
        for p in pts:
            meta = (p.payload or {}).get("metadata", {})
            if meta.get("source_path") == pdf_path:
                all_pts.append(p)
        if offset is None:
            break

    if not all_pts:
        print("  No chunks found in Qdrant for this file — was it indexed?")
        return

    current_title = (all_pts[0].payload or {}).get("metadata", {}).get("document_title", "")
    suggested = title_from_filename(pdf_path)

    print(f"  Suggested title: {suggested}")
    print(f"  Enter title (Author - Title format) or press Enter to accept: ", end="", flush=True)
    try:
        user_input = input().strip()
    except (EOFError, KeyboardInterrupt):
        user_input = ""

    new_title = user_input if user_input else suggested

    if new_title == current_title:
        print(f"  Title unchanged: {new_title}")
        return

    print(f"  New title: {new_title}")

    for p in all_pts:
        old_payload = p.payload or {}
        old_meta = old_payload.get("metadata", {})
        new_meta = {**old_meta, "document_title": new_title}
        client.overwrite_payload(collection_name=COLLECTION,
                                 payload={**old_payload, "metadata": new_meta},
                                 points=[p.id])
    print(f"  Updated {len(all_pts)} chunks")


def step_cleanup():
    """Remove orphan chunks (source files no longer on disk)."""
    from qdrant_client import QdrantClient
    QDRANT_HOST = os.getenv("QDRANT_HOST", "localhost")
    QDRANT_PORT = int(os.getenv("QDRANT_PORT", "6333"))
    COLLECTION = os.getenv("COLLECTION_NAME", "pdf_library")

    client = QdrantClient(host=QDRANT_HOST, port=QDRANT_PORT)
    orphan_ids = []
    checked = {}
    offset = None
    while True:
        pts, offset = client.scroll(collection_name=COLLECTION, limit=500,
                                     offset=offset, with_payload=True, with_vectors=False)
        for p in pts:
            path = (p.payload or {}).get("metadata", {}).get("source_path", "")
            if path:
                if path not in checked:
                    checked[path] = os.path.exists(path)
                if not checked[path]:
                    orphan_ids.append(p.id)
        if offset is None:
            break

    if not orphan_ids:
        print("  No orphans found.")
        return

    print(f"  Removing {len(orphan_ids)} orphan chunks from {len([p for p in checked.values() if not p])} missing files...")
    batch_size = 100
    for i in range(0, len(orphan_ids), batch_size):
        client.delete(collection_name=COLLECTION, points_selector=orphan_ids[i:i+batch_size])
    print("  Done.")


def main():
    parser = argparse.ArgumentParser(description="Add a book to the knowledge base")
    parser.add_argument("pdf", nargs="?", help="Path to the PDF file to add")
    parser.add_argument("--ocr", action="store_true", help="Force OCR (use for scanned books)")
    parser.add_argument("--scan", action="store_true",
                        help=f"Index all new books in {BOOKS_DIR}, fix their titles, clean up")
    parser.add_argument("--no-fix-title", action="store_true", help="Skip filename-based title fix")
    parser.add_argument("--no-cleanup", action="store_true", help="Skip orphan cleanup")
    args = parser.parse_args()

    if not args.pdf and not args.scan:
        parser.print_help()
        sys.exit(1)

    if args.scan:
        print(f"\n── Step 1: Index new books in {BOOKS_DIR}")
        indexed = step_index(BOOKS_DIR, use_ocr=args.ocr)
        if indexed == 0:
            print("  Nothing new to index.")
        if not args.no_fix_title and indexed > 0:
            print("\n── Step 2: Fix titles (new documents only)")
            # fix_titles.py already skips already-fixed docs — just run it
            import subprocess
            subprocess.run([sys.executable,
                            os.path.join(os.path.dirname(__file__), "fix_titles.py")])
        if not args.no_cleanup:
            print("\n── Step 3: Clean up orphans")
            step_cleanup()
    else:
        pdf = args.pdf
        if not os.path.exists(pdf):
            print(f"File not found: {pdf}")
            sys.exit(1)

        print(f"\n── Step 1: Copy to Books folder")
        final_path = step_copy(pdf)

        print(f"\n── Step 2: Index")
        step_index(final_path, use_ocr=args.ocr)

        if not args.no_fix_title:
            print(f"\n── Step 3: Fix title")
            step_fix_title(final_path)

        if not args.no_cleanup:
            print(f"\n── Step 4: Clean up orphans")
            step_cleanup()

    print("\n✅ Done.")


if __name__ == "__main__":
    main()
