#!/usr/bin/env python3
"""
Resumable re-index of the whole library into a new collection, meant to run
in nightly windows (see scripts/systemd/) until it is complete.

Each run indexes books one at a time and stops before starting a new book
once the window closes. A book is recorded as done only after all its chunks
are written, so a run cut short (suspend, kill, reboot) never leaves a
half-indexed book behind: on the next run any book that has points but no
done record is deleted from the collection and indexed again.

EPUBs go first (they were the worst affected by the old chunking), then PDFs.
Books added to BOOKS_DIR during the week are picked up by the next run.

Usage:
  python scripts/reindex_nightly.py --collection pdf_library_v2 --until 08:00
  python scripts/reindex_nightly.py --collection pdf_library_v2 --status
"""

import argparse
import datetime as dt
import fcntl
import json
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LIVE_COLLECTION = "pdf_library"
STATE_DIR = os.path.expanduser("~/.local/share/knowledge_server")


def parse_args():
    parser = argparse.ArgumentParser(description="Nightly resumable re-index")
    parser.add_argument("--collection", required=True, help="Target Qdrant collection (not the live one)")
    parser.add_argument("--from", dest="start", default="20:00", help="Window start (HH:MM); outside the window the run exits")
    parser.add_argument("--until", default="08:00", help="Window end (HH:MM): no new book is started after it")
    parser.add_argument("--status", action="store_true", help="Print progress and exit")
    parser.add_argument("--books-dir", help="Library root (default: BOOKS_DIR from config.py)")
    parser.add_argument("--sync-titles", action="store_true",
                        help="Copy current titles from the live collection onto every finished book, then exit")
    return parser.parse_args()


def window_deadline(start: str, until: str):
    """End of the current window, or None if now is outside it. Windows may cross
    midnight (20:00-08:00), so a catch-up run after a late wake still stops at 08:00
    and one started in the daytime exits at once."""
    now = dt.datetime.now()
    def at(hhmm):
        hh, mm = map(int, hhmm.split(":"))
        return now.replace(hour=hh, minute=mm, second=0, microsecond=0)
    begin, end = at(start), at(until)
    if begin < end:
        return end if begin <= now < end else None
    if now >= begin:
        return end + dt.timedelta(days=1)
    return end if now < end else None


def live_title(client, models, file_hash) -> dict:
    """Curated title fields for a book in the live collection (titles there were
    rewritten after indexing by fix_titles.py, add_book.py or by hand), or {}.
    Read at the moment of use, so edits made during the re-index carry over."""
    pts, _ = client.scroll(LIVE_COLLECTION, limit=1, with_vectors=False,
                           scroll_filter=models.Filter(must=[models.FieldCondition(
                               key="metadata.file_hash", match=models.MatchValue(value=file_hash))]),
                           with_payload=["metadata.document_title", "metadata.document_title_original"])
    meta = pts[0].payload.get("metadata", {}) if pts else {}
    return {k: meta[k] for k in ("document_title", "document_title_original") if meta.get(k)}


def main():
    args = parse_args()
    if args.collection == "pdf_library":
        sys.exit("Refusing to re-index into the live collection; pick a new name.")

    # vector_db reads COLLECTION_NAME at import time.
    os.environ["COLLECTION_NAME"] = args.collection
    sys.path.insert(0, ROOT)
    from server import _index_directory, _load_empty_hashes
    from pdf_processor import _calculate_file_hash
    from vector_db import get_db
    from qdrant_client import models
    sys.path.insert(0, os.path.join(ROOT, "scripts"))
    from library_checks import title_from_filename
    from pathlib import Path

    try:
        from config import BOOKS_DIR
    except ImportError:
        BOOKS_DIR = "~/Books"
    books_dir = os.path.expanduser(args.books_dir or BOOKS_DIR)

    os.makedirs(STATE_DIR, exist_ok=True)
    done_path = os.path.join(STATE_DIR, f"reindex_{args.collection}_done.json")
    try:
        with open(done_path) as f:
            done = json.load(f)  # file_hash -> {"path", "chunks", "at"}
    except (FileNotFoundError, json.JSONDecodeError):
        done = {}

    def save_done():
        tmp = done_path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(done, f, indent=1)
        os.replace(tmp, done_path)

    files = sorted(Path(books_dir).rglob("*.epub")) + sorted(Path(books_dir).rglob("*.pdf"))
    db = get_db()

    if args.status:
        chunks = sum(d["chunks"] for d in done.values())
        print(f"{len(done)}/{len(files)} files done, {chunks} chunks in {args.collection}")
        return

    # One run per collection at a time: a second one (e.g. the 20:00 timer firing
    # during a manual daytime run) would index the same books concurrently.
    lock_file = open(os.path.join(STATE_DIR, f"reindex_{args.collection}.lock"), "w")
    try:
        fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        print(f"{dt.datetime.now():%Y-%m-%d %H:%M} Another re-index run is active, exiting.", flush=True)
        return

    def apply_title(file_hash, rel):
        fields = live_title(db.client, models, file_hash) or {"document_title": title_from_filename(rel)}
        db.client.set_payload(collection_name=args.collection, payload=fields, key="metadata", wait=True,
                              points=models.Filter(must=[models.FieldCondition(
                                  key="metadata.file_hash", match=models.MatchValue(value=file_hash))]))
        done[file_hash]["titled"] = True

    done.pop("", None)  # left by older runs for files that vanished mid-run

    # Books finished before titles were carried over (or all of them, with --sync-titles).
    untitled = [h for h, d in done.items() if d["chunks"] and (args.sync_titles or not d.get("titled"))]
    for h in untitled:
        apply_title(h, done[h]["path"])
    if untitled:
        save_done()
        print(f"Set titles on {len(untitled)} earlier books", flush=True)
    if args.sync_titles:
        return

    # Drop books left half-written by an interrupted run.
    for file_hash in db.get_all_indexed_hashes() - set(done):
        print(f"Removing partial book {file_hash}", flush=True)
        db.delete_by_file(file_hash)

    deadline = window_deadline(args.start, args.until)
    if deadline is None:
        print(f"Outside the {args.start}-{args.until} window, nothing to do.", flush=True)
        return
    start = time.time()
    run_files = run_chunks = 0
    print(f"=== Run started {dt.datetime.now():%Y-%m-%d %H:%M}, window closes {deadline:%Y-%m-%d %H:%M}, "
          f"{len(done)}/{len(files)} files already done", flush=True)

    for path in files:
        if dt.datetime.now() >= deadline:
            print("Window closed, stopping before next book.", flush=True)
            break
        if not path.exists():
            continue  # moved or deleted since the run started; picked up at its new path next run
        file_hash = _calculate_file_hash(path)
        if not file_hash or file_hash in done:
            continue
        rel = os.path.relpath(path, books_dir)
        t0 = time.time()
        try:
            indexed, _, errors, _ = _index_directory(directory_path=books_dir, file_list=[str(path)], use_ocr=True)
        except Exception as e:
            # Leave it unrecorded: its partial points are removed and it is retried next run.
            print(f"FAILED {rel}: {e}", flush=True)
            continue
        if not indexed and errors and file_hash not in _load_empty_hashes():
            # A real failure; files with no extractable text are cached as empty and count as done.
            print(f"FAILED {rel}: indexing reported errors", flush=True)
            continue
        chunks = db.client.count(collection_name=args.collection, exact=True, count_filter=models.Filter(
            must=[models.FieldCondition(key="metadata.file_hash", match=models.MatchValue(value=file_hash))]
        )).count if indexed else 0
        done[file_hash] = {"path": rel, "chunks": chunks, "at": dt.datetime.now().isoformat(timespec="seconds")}
        if chunks:
            apply_title(file_hash, rel)
        save_done()
        run_files += 1
        run_chunks += chunks
        print(f"done {len(done)}/{len(files)}  {chunks:5d} chunks  {time.time() - t0:6.0f}s  {rel}", flush=True)

    elapsed = time.time() - start
    rate = run_chunks / elapsed if elapsed else 0
    print(f"=== Run ended {dt.datetime.now():%Y-%m-%d %H:%M}: {run_files} files, {run_chunks} chunks "
          f"in {elapsed / 3600:.1f} h ({rate:.2f} chunks/s); {len(done)}/{len(files)} files done", flush=True)
    if len(done) >= len(files):
        print(f"=== COMPLETE: every file is indexed in {args.collection}", flush=True)


if __name__ == "__main__":
    main()
