#!/usr/bin/env python3
"""
Export a human-readable report of all indexed books.

Reads directly from Qdrant — no external library manager required.

Usage:
  python export_library_report.py                  # print to stdout (markdown)
  python export_library_report.py --csv            # CSV output
  python export_library_report.py -o report.md     # write to file
"""

import os
import sys
import argparse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

QDRANT_HOST = os.getenv("QDRANT_HOST", "localhost")
QDRANT_PORT = int(os.getenv("QDRANT_PORT", "6333"))
COLLECTION_NAME = os.getenv("COLLECTION_NAME", "pdf_library")


def get_qdrant_books():
    """Return list of {title, source, chunks} sorted by title."""
    from qdrant_client import QdrantClient
    client = QdrantClient(host=QDRANT_HOST, port=QDRANT_PORT)

    docs = {}  # source_path -> {title, chunks}
    offset = None
    scanned = 0

    while True:
        pts, offset = client.scroll(
            collection_name=COLLECTION_NAME, limit=500, offset=offset,
            with_payload=True, with_vectors=False
        )
        for p in pts:
            meta = (p.payload or {}).get("metadata", {})
            sp = meta.get("source_path", "")
            if not sp:
                continue
            if sp not in docs:
                docs[sp] = {"title": meta.get("document_title", ""), "chunks": 0}
            docs[sp]["chunks"] += 1
        scanned += len(pts)
        print(f"  Scanned {scanned} chunks...", end="\r", flush=True, file=sys.stderr)
        if offset is None:
            break

    print(f"  Scanned {scanned} chunks total.", file=sys.stderr)

    records = [
        {"title": info["title"] or os.path.splitext(os.path.basename(sp))[0],
         "source": os.path.basename(sp),
         "chunks": info["chunks"]}
        for sp, info in docs.items()
    ]
    records.sort(key=lambda r: r["title"].lower())
    return records


def main():
    parser = argparse.ArgumentParser(description="Export library report")
    parser.add_argument("--csv", action="store_true", help="Output as CSV")
    parser.add_argument("-o", "--output", help="Write to file instead of stdout")
    args = parser.parse_args()

    print("Loading Qdrant index...", file=sys.stderr)
    records = get_qdrant_books()

    out = open(args.output, "w") if args.output else sys.stdout

    if args.csv:
        import csv
        writer = csv.DictWriter(out, fieldnames=["title", "chunks", "source"])
        writer.writeheader()
        writer.writerows(records)
    else:
        out.write(f"# Library Report\n\n")
        out.write(f"**Total indexed books:** {len(records)}\n\n")
        out.write(f"| # | Title | Chunks |\n")
        out.write(f"|---|-------|--------|\n")
        for i, r in enumerate(records, 1):
            title = r["title"].replace("|", "\\|")
            out.write(f"| {i} | {title} | {r['chunks']} |\n")

    if args.output:
        out.close()
        print(f"Written to {args.output}", file=sys.stderr)

    print(f"Done. {len(records)} books.", file=sys.stderr)


if __name__ == "__main__":
    main()
