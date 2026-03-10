#!/usr/bin/env python3
"""
Fix document titles in Qdrant using llama3.2 via Ollama.

For each unique document (by file_hash):
- Finds the chunk from page 1
- Sends its text to llama3.2 and asks for the title
- Saves the original title as `document_title_original`
- Updates `document_title` in all chunks for that document

No vectors are touched. Safe to run multiple times (skips already-fixed docs).
"""

import os
import sys
import json
import requests
from qdrant_client import QdrantClient
from qdrant_client.http.models import Filter, FieldCondition, MatchValue, ScrollRequest

QDRANT_HOST = os.getenv("QDRANT_HOST", "localhost")
QDRANT_PORT = int(os.getenv("QDRANT_PORT", "6333"))
COLLECTION = os.getenv("COLLECTION_NAME", "pdf_library")
OLLAMA_URL = "http://localhost:11434/api/chat"
OLLAMA_MODEL = "llama3.2"


def ask_llm_for_title(text: str) -> str:
    prompt = (
        "You are given the first page of a PDF document. "
        "Extract the document title. "
        "Return ONLY the title, nothing else — no explanation, no quotes, no punctuation at the end. "
        "If you cannot determine the title, return UNKNOWN.\n\n"
        f"--- FIRST PAGE ---\n{text[:2000]}\n--- END ---"
    )
    payload = {
        "model": OLLAMA_MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "stream": False,
    }
    try:
        r = requests.post(OLLAMA_URL, json=payload, timeout=30)
        r.raise_for_status()
        return r.json()["message"]["content"].strip()
    except Exception as e:
        print(f"    LLM error: {e}", file=sys.stderr)
        return ""


def main():
    client = QdrantClient(host=QDRANT_HOST, port=QDRANT_PORT)

    print(f"Connected to Qdrant — collection: {COLLECTION}")

    # Scroll all points to collect unique file_hashes
    print("Scanning collection for unique documents...")
    hash_to_info: dict[str, dict] = {}  # hash -> {title, title_original, page1_text, point_ids}

    offset = None
    total_scanned = 0
    while True:
        response = client.scroll(
            collection_name=COLLECTION,
            scroll_filter=None,
            limit=200,
            offset=offset,
            with_payload=True,
            with_vectors=False,
        )
        points, next_offset = response

        for point in points:
            payload = point.payload or {}
            meta = payload.get("metadata", {})
            fhash = meta.get("file_hash")
            if not fhash:
                continue

            page_num = meta.get("page_number", 9999)

            if fhash not in hash_to_info:
                hash_to_info[fhash] = {
                    "title": meta.get("document_title", ""),
                    "title_original": meta.get("document_title_original"),  # None if not yet fixed
                    "best_page": page_num,
                    "best_text": payload.get("text", ""),
                    "point_ids": [point.id],
                }
            else:
                hash_to_info[fhash]["point_ids"].append(point.id)
                # Keep the lowest page number for best first-page text
                if page_num < hash_to_info[fhash]["best_page"]:
                    hash_to_info[fhash]["best_page"] = page_num
                    hash_to_info[fhash]["best_text"] = payload.get("text", "")

        total_scanned += len(points)
        print(f"  Scanned {total_scanned} chunks, {len(hash_to_info)} unique documents so far...", end="\r")

        if next_offset is None:
            break
        offset = next_offset

    print(f"\nFound {len(hash_to_info)} unique documents.")

    # Process each document
    fixed = 0
    skipped = 0
    failed = 0

    for i, (fhash, info) in enumerate(hash_to_info.items(), 1):
        current_title = info["title"]
        already_fixed = info["title_original"] is not None

        print(f"\n[{i}/{len(hash_to_info)}] {current_title[:70]}")

        if already_fixed:
            print(f"  → already fixed (original: {info['title_original'][:60]}), skipping")
            skipped += 1
            continue

        page_text = info["best_text"]
        if not page_text.strip():
            print("  → no text available, skipping")
            skipped += 1
            continue

        new_title = ask_llm_for_title(page_text)

        if not new_title or new_title == "UNKNOWN":
            print(f"  → LLM returned nothing useful, keeping original")
            failed += 1
            continue

        if new_title == current_title:
            print(f"  → title unchanged: {new_title[:70]}")
            skipped += 1
            continue

        print(f"  → new title: {new_title[:70]}")

        # Update all chunks for this document
        point_ids = info["point_ids"]
        try:
            client.set_payload(
                collection_name=COLLECTION,
                payload={
                    "metadata": {
                        **{},  # We need to update nested field carefully
                    }
                },
                points=point_ids,
            )
        except Exception:
            pass

        # Qdrant doesn't support nested key updates directly — update via overwrite per point
        # We set payload keys at top level using a workaround: read each point and rewrite metadata
        # Use batch scroll by IDs to get current payloads
        batch_size = 50
        for start in range(0, len(point_ids), batch_size):
            batch_ids = point_ids[start:start + batch_size]
            fetched = client.retrieve(
                collection_name=COLLECTION,
                ids=batch_ids,
                with_payload=True,
                with_vectors=False,
            )
            for fp in fetched:
                old_payload = fp.payload or {}
                old_meta = old_payload.get("metadata", {})
                new_meta = {
                    **old_meta,
                    "document_title": new_title,
                    "document_title_original": old_meta.get("document_title", current_title),
                }
                client.overwrite_payload(
                    collection_name=COLLECTION,
                    payload={**old_payload, "metadata": new_meta},
                    points=[fp.id],
                )

        print(f"  → updated {len(point_ids)} chunks")
        fixed += 1

    print(f"\nDone. Fixed: {fixed} | Skipped: {skipped} | Failed/unchanged: {failed}")


if __name__ == "__main__":
    main()
