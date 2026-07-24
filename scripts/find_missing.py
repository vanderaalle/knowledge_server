#!/usr/bin/env python3
"""
Script to find PDF files present on disk but NOT indexed in Qdrant.
"""

import os
import sys
from pathlib import Path
from qdrant_client import QdrantClient

from qdrant_client import QdrantClient
from hashlib import md5

# Configuration (must match server.py)
QDRANT_HOST = os.getenv('QDRANT_HOST', 'localhost')
QDRANT_PORT = int(os.getenv('QDRANT_PORT', '6333'))
COLLECTION_NAME = os.getenv('COLLECTION_NAME', 'pdf_library')

def _calculate_file_hash(file_path: Path) -> str:
    """Compute the file's MD5 hash (fast)"""
    hash_md5 = md5()
    try:
        with open(file_path, "rb") as f:
            for chunk in iter(lambda: f.read(4096), b""):
                hash_md5.update(chunk)
        return hash_md5.hexdigest()
    except Exception as e:
        return ""

def get_indexed_hashes(client):
    """Return a set of indexed hashes"""
    print("Fetching indexed hash list from Qdrant...", file=sys.stderr)
    indexed_hashes = set()
    offset = None

    while True:
        result = client.scroll(
            collection_name=COLLECTION_NAME,
            limit=1000,
            offset=offset,
            with_payload=True,
            with_vectors=False
        )

        points, next_offset = result
        for point in points:
            f_hash = point.payload.get('metadata', {}).get('file_hash')
            if f_hash:
                indexed_hashes.add(f_hash)

        if not next_offset:
            break
        offset = next_offset

    print(f"Unique hashes found in DB: {len(indexed_hashes)}", file=sys.stderr)
    return indexed_hashes

def main():
    if len(sys.argv) < 2:
        print("Usage: python3 find_missing.py <path_to_scan>")
        sys.exit(1)

    scan_path = os.path.abspath(sys.argv[1])
    if not os.path.exists(scan_path):
        print(f"Path not found: {scan_path}")
        sys.exit(1)

    client = QdrantClient(host=QDRANT_HOST, port=QDRANT_PORT)

    # 1. Fetch indexed hashes
    try:
        indexed_hashes = get_indexed_hashes(client)
    except Exception as e:
         print(f"Qdrant connection error: {e}")
         sys.exit(1)

    # 2. Scan local directory and compute hashes
    print(f"Scanning local files in: {scan_path}...", file=sys.stderr)
    missing_files = []
    total_local = 0

    # Use tqdm if available for local progress
    try:
        from tqdm import tqdm
        has_tqdm = True
    except ImportError:
        has_tqdm = False

    all_local_pdfs = []
    for root, dirs, files in os.walk(scan_path):
        for file in files:
            if file.lower().endswith('.pdf'):
                all_local_pdfs.append(os.path.join(root, file))

    total_local = len(all_local_pdfs)

    iterator = tqdm(all_local_pdfs, desc="Checking hashes") if has_tqdm else all_local_pdfs

    for full_path in iterator:
        f_hash = _calculate_file_hash(Path(full_path))
        if f_hash and f_hash not in indexed_hashes:
            missing_files.append(full_path)

    # 3. Report
    print(f"\n{'='*60}")
    print(f"MISSING FILES REPORT (UNIQUE CONTENT)")
    print(f"{'='*60}")
    print(f"Total local PDFs:    {total_local}")
    print(f"Truly missing:       {len(missing_files)}")
    print(f"Already indexed:     {total_local - len(missing_files)}")
    print(f"{'='*60}\n")

    if missing_files:
        print("List of files with unique, unindexed content:")
        for f in sorted(missing_files):
            try:
                rel_path = os.path.relpath(f, scan_path)
                print(f"- {rel_path}")
            except:
                print(f"- {f}")

        # Save to file
        with open("missing_files_report.txt", "w") as f_out:
            for line in missing_files:
                f_out.write(f"{line}\n")
        print("\nReport saved to: missing_files_report.txt")

if __name__ == "__main__":
    main()
