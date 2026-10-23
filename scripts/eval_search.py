#!/usr/bin/env python3
"""
Compare search quality between collections on a fixed set of questions
(scripts/eval_queries.tsv: a query and a substring of the book that should
answer it). Read-only.

For each query and collection: the rank of the first passage from the expected
book in the top K results (books ranked by their best passage, as Jorge does
for books). Summary: hit@1, hit@5, hit@K and MRR. While a re-index is still
running, the comparison is restricted to queries whose expected book is
already complete in every collection ("comparable"); the others are listed as
pending.

Usage:
  python scripts/eval_search.py                                   # pdf_library vs pdf_library_v2
  python scripts/eval_search.py --collections pdf_library_v2 --k 10
"""

import argparse
import csv
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from qdrant_client import QdrantClient, models
from vector_db import embed_query, QDRANT_HOST, QDRANT_PORT

HERE = os.path.dirname(os.path.abspath(__file__))
STATE_DIR = os.path.expanduser("~/.local/share/knowledge_server")


def book_hashes(client, collection, needle):
    """file_hashes of books in `collection` whose title contains `needle`."""
    found, offset = set(), None
    while True:
        pts, offset = client.scroll(collection, limit=10000, offset=offset, with_vectors=False,
                                    with_payload=["metadata.file_hash", "metadata.document_title"])
        for p in pts:
            m = p.payload.get("metadata", {})
            if needle.lower() in (m.get("document_title") or "").lower():
                found.add(m["file_hash"])
        if offset is None:
            return found


def complete_in(collection):
    """Hashes a re-index has fully written (None = treat everything as complete)."""
    path = os.path.join(STATE_DIR, f"reindex_{collection}_done.json")
    if not os.path.exists(path):
        return None
    with open(path) as f:
        return {h for h, d in json.load(f).items() if d.get("chunks")}


def rank_of(client, collection, vector, wanted, k):
    """1-based rank of the expected book among distinct books in the top results, or None."""
    res = client.search(collection, query_vector=vector, limit=k * 20, with_payload=["metadata.file_hash"])
    books = []
    for r in res:
        h = r.payload["metadata"]["file_hash"]
        if h not in books:
            books.append(h)
        if len(books) == k:
            break
    for i, h in enumerate(books, 1):
        if h in wanted:
            return i
    return None


def main():
    parser = argparse.ArgumentParser(description="Compare search quality between collections")
    parser.add_argument("--collections", default="pdf_library,pdf_library_v2")
    parser.add_argument("--queries", default=os.path.join(HERE, "eval_queries.tsv"))
    parser.add_argument("--k", type=int, default=10, help="Books considered per query")
    args = parser.parse_args()
    colls = args.collections.split(",")
    client = QdrantClient(host=QDRANT_HOST, port=QDRANT_PORT)

    with open(args.queries) as f:
        queries = [r for r in csv.reader(f, delimiter="\t")][1:]

    # Expected books, resolved by title in the live collection (titles carry over to v2).
    wanted = {needle: book_hashes(client, colls[0], needle) for _, needle in queries}
    missing = [n for n, h in wanted.items() if not h]
    if missing:
        print("Expected books not found by title:", ", ".join(missing))
    done = {c: complete_in(c) for c in colls}

    rows, comparable = [], []
    for q, needle in queries:
        hs = wanted[needle]
        if not hs:
            continue
        ready = all(done[c] is None or hs & done[c] for c in colls)
        v = embed_query(q)
        ranks = {c: rank_of(client, c, v, hs, args.k) for c in colls}
        rows.append((q, needle, ready, ranks))
        if ready:
            comparable.append(ranks)

    width = max(len(q) for q, *_ in rows)
    print(f"\n{'query':{width}}  " + "  ".join(f"{c[-14:]:>14}" for c in colls))
    for q, needle, ready, ranks in rows:
        cells = "  ".join(f"{(ranks[c] or '-')!s:>14}" for c in colls)
        print(f"{q:{width}}  {cells}" + ("" if ready else "   (pending re-index)"))

    def summary(sets, label):
        if not sets:
            print(f"\n{label}: no queries yet")
            return
        print(f"\n{label} ({len(sets)} queries):")
        for c in colls:
            r = [s[c] for s in sets]
            hit = lambda n: sum(1 for x in r if x and x <= n) / len(r)
            mrr = sum(1 / x for x in r if x) / len(r)
            print(f"  {c:16} hit@1 {hit(1):.0%}  hit@5 {hit(5):.0%}  hit@{args.k} {hit(args.k):.0%}  MRR {mrr:.2f}")

    summary(comparable, "Comparable (expected book complete in every collection)")
    summary([r[3] for r in rows], "All queries")


if __name__ == "__main__":
    main()
