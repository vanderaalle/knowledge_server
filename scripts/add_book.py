#!/usr/bin/env python3
"""
Add a book to the library and the knowledge base, enforcing the conventions
in library_checks.py. Nothing is moved or indexed until every check passes
(or is explicitly overridden).

Steps:
  1. Check: not already indexed (same file hash); destination is a shelf folder;
     filename is "Author - Title (Year)"; not debris; text layer readable.
  2. Move (not copy) the file into its shelf, renamed if --name is given.
  3. Index it (scanned pages are OCR'd; --force-ocr for garbled text layers).
  4. Set the indexed title from the filename (or --title).
  5. Verify chunks and page coverage.

Usage:
  python add_book.py ~/Downloads/x.pdf --shelf Semiotics/General-Theory --name "Umberto Eco - Kant and the Platypus (1997)"
  python add_book.py ~/Books/Computer-Science/"Author - Title (2001).pdf"     # already in place
  python add_book.py FILE --check                                              # checks only, change nothing
  python add_book.py --scan                                                    # check + index every new book in BOOKS_DIR

Overrides (use only after looking): --allow-name, --allow-junk, --allow-garbled.
"""

import argparse
import os
import shutil
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import library_checks as lc
from library_checks import title_from_filename  # noqa: F401  (kept importable from here)
from pdf_processor import _calculate_file_hash
from pathlib import Path

BOOKS_DIR = lc.BOOKS_DIR


def collection():
    return os.getenv("COLLECTION_NAME", "pdf_library")


def client():
    from vector_db import get_db
    return get_db().client


def is_indexed(file_hash: str):
    """source_path of the indexed copy of this file, or None."""
    from qdrant_client import models
    pts, _ = client().scroll(collection(), scroll_filter=lc._hash_filter(models, file_hash), limit=1,
                             with_vectors=False, with_payload=["metadata.source_path"])
    return pts[0].payload["metadata"].get("source_path", "?") if pts else None


def plan_destination(src: str, shelf: str, name: str) -> str:
    """Final path of the book: its shelf (given or current) plus its name (given or current)."""
    ext = os.path.splitext(src)[1].lower()
    filename = (name + ext) if name else os.path.basename(src)
    if shelf:
        folder = os.path.join(BOOKS_DIR, shelf)
        if not os.path.isdir(folder):
            sys.exit(f"Shelf not found: {shelf} (shelves are existing folders under {BOOKS_DIR}; create new ones by hand)")
        return os.path.join(folder, filename)
    return os.path.join(os.path.dirname(os.path.abspath(src)), filename)


def check(src: str, dest: str, args) -> list:
    """Blocking problems for adding src as dest (empty list = go ahead)."""
    blocking = []
    h = _calculate_file_hash(Path(src))
    where = is_indexed(h)
    if where:
        blocking.append(f"already indexed (same file) as {where}")
    if os.path.exists(dest) and os.path.abspath(dest) != os.path.abspath(src):
        blocking.append(f"destination exists: {dest}")
    for p in lc.location_problems(dest):
        blocking.append(f"location: {p} (use --shelf)")
    problems = lc.name_problems(dest)
    if problems and not args.allow_name:
        blocking.append(f"name: {'; '.join(problems)} (use --name \"Author - Title (Year)\")")
    junk = lc.junk_hint(src)
    if junk and not args.allow_junk:
        blocking.append(f"junk? {junk}: move it to {lc.DEBRIS_DIR} instead, or --allow-junk")
    status = lc.text_layer_status(src)
    print(f"  text layer: {status}" + ("  (scanned: pages will be OCR'd)" if status == "none" else ""))
    if status == "garbled" and not (args.force_ocr or args.allow_garbled):
        blocking.append("text layer looks garbled: check it; then --force-ocr if it is, or --allow-garbled if not "
                        "(music scores, formulas and non-Latin scripts also score low)")
    return blocking


def index_and_finish(path: str, args) -> bool:
    """Index one file in place, set its title, verify. True on success."""
    from server import _index_directory, _add_force_ocr_hash
    h = _calculate_file_hash(Path(path))
    if args.force_ocr:
        _add_force_ocr_hash(h)
    indexed, skipped, errors, _ = _index_directory(directory_path=BOOKS_DIR, file_list=[path], use_ocr=True)
    if not indexed:
        print(f"  ❌ Not indexed (skipped={skipped}, errors={errors}): no extractable text?")
        return False
    title = lc.set_filename_title(client(), collection(), h, path, args.title)
    v = lc.verify_indexed(client(), collection(), h)
    print(f"  ✅ {v['chunks']} chunks, text on {v['pages_with_text']}/{v['total_pages']} pages "
          f"({v['coverage']:.0%}), title: {title}")
    if v["coverage"] < 0.5:
        print("  ⚠️  Less than half the pages have text: scanned images, or a garbled text layer?")
    return True


def add_one(args):
    src = os.path.abspath(os.path.expanduser(args.file))
    if not os.path.exists(src):
        sys.exit(f"File not found: {src}")
    dest = plan_destination(src, args.shelf, args.name)
    print(f"── {os.path.basename(src)}\n   → {os.path.relpath(dest, BOOKS_DIR) if dest.startswith(BOOKS_DIR) else dest}")
    blocking = check(src, dest, args)
    if blocking:
        print("  Not added:")
        for b in blocking:
            print(f"   • {b}")
        sys.exit(1)
    if args.check:
        print("  All checks pass (--check: nothing changed).")
        return
    if dest != src:
        shutil.move(src, dest)
        print("  Moved.")
    index_and_finish(dest, args)


def scan(args):
    """Check and index every book under BOOKS_DIR that is not indexed yet."""
    from vector_db import get_db
    indexed = get_db().get_all_indexed_hashes()
    new = [p for p in sorted(Path(BOOKS_DIR).rglob("*")) if p.suffix.lower() in lc.BOOK_EXTENSIONS
           and _calculate_file_hash(p) not in indexed]
    print(f"{len(new)} new books under {BOOKS_DIR}")
    held = []
    for p in new:
        print(f"── {p.relative_to(BOOKS_DIR)}")
        blocking = [b for b in check(str(p), str(p), args) if not b.startswith("already indexed")]
        if blocking:
            held.append((p, blocking))
            print("  Held back: " + " | ".join(blocking))
        elif not args.check:
            index_and_finish(str(p), args)
    if held:
        print(f"\n{len(held)} held back; fix them (rename, move, debris) and run again.")


def main():
    parser = argparse.ArgumentParser(description="Add a book to the library and the knowledge base")
    parser.add_argument("file", nargs="?", help="Book to add (PDF or EPUB)")
    parser.add_argument("--shelf", help="Destination shelf folder, relative to BOOKS_DIR (e.g. Semiotics/General-Theory)")
    parser.add_argument("--name", help="New filename without extension: \"Author - Title (Year)\"")
    parser.add_argument("--title", help="Indexed title, if it should differ from the filename")
    parser.add_argument("--check", action="store_true", help="Only run the checks")
    parser.add_argument("--scan", action="store_true", help=f"Check and index all new books in {BOOKS_DIR}")
    parser.add_argument("--force-ocr", action="store_true", help="OCR every page (garbled text layer)")
    parser.add_argument("--allow-name", action="store_true", help="Accept a non-conforming filename")
    parser.add_argument("--allow-junk", action="store_true", help="Accept a file that looks like debris")
    parser.add_argument("--allow-garbled", action="store_true", help="Accept a low-scoring text layer as is")
    args = parser.parse_args()

    if args.scan:
        scan(args)
    elif args.file:
        add_one(args)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
