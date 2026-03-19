#!/usr/bin/env python3
"""
Generate BibTeX entries for PDFs in Qdrant that are not in miaBiblio.bib.

Strategy (in order):
  1. DOI found in first-page text  -> CrossRef authoritative BibTeX
  2. Fallback                      -> Ollama LLM extraction (extract-only, no invention)
  3. Validate                      -> reject if title or author missing

Incremental: results written immediately, progress saved to a checkpoint file.
Restart safely — already-processed files are skipped.

Usage:
  python generate_bibtex.py                        # generate for all Qdrant docs (no deduplication)
  python generate_bibtex.py --bib ~/mylib.bib      # skip docs already in existing bib
  python generate_bibtex.py --out ~/my.bib         # custom output file
  python generate_bibtex.py --reset                # clear checkpoint and start over
"""

import argparse
import json
import os
import re
import sys

import requests
from qdrant_client import QdrantClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# ── defaults ──────────────────────────────────────────────────────────────────

QDRANT_HOST   = os.getenv("QDRANT_HOST", "localhost")
QDRANT_PORT   = int(os.getenv("QDRANT_PORT", "6333"))
COLLECTION    = os.getenv("COLLECTION_NAME", "pdf_library")
OLLAMA_URL    = "http://localhost:11434/api/chat"
OLLAMA_MODEL  = "llama3.2"
CROSSREF_URL  = "https://api.crossref.org/works/{doi}/transform/application/x-bibtex"

DEFAULT_BIB   = None   # pass --bib <file> to compare against an existing library
DEFAULT_OUT   = os.path.expanduser("~/qdrant_generated.bib")
DEFAULT_LOG   = os.path.expanduser("~/qdrant_generated.log")
DEFAULT_CKPT  = os.path.expanduser("~/qdrant_generated.ckpt.json")


# ── helpers ───────────────────────────────────────────────────────────────────

def norm(s):
    return re.sub(r"\W+", " ", s.lower()).strip()


def get_field(name, block):
    m = re.search(
        rf"{name}\s*=\s*[{{\"](.*?)[}}\"]\s*[,}}]", block, re.IGNORECASE | re.DOTALL
    )
    return m.group(1).strip() if m else ""


def extract_doi(text):
    m = re.search(r"\b(10\.\d{4,}/[^\s,;>\"\\]]+)", text)
    return m.group(1).rstrip(".") if m else None


def crossref_bibtex(doi):
    try:
        r = requests.get(
            CROSSREF_URL.format(doi=doi),
            headers={"User-Agent": "knowledge-server/1.0"},
            timeout=15,
        )
        if r.status_code == 200 and r.text.strip().startswith("@"):
            return r.text.strip()
    except Exception:
        pass
    return None


def llm_extract(filename, title, text):
    prompt = (
        "Extract bibliographic metadata from the PDF first page below.\n"
        "Rules:\n"
        "- Only use information EXPLICITLY present in the text.\n"
        "- If a field is not in the text, write UNKNOWN for its value.\n"
        "- Do NOT invent or guess journal names, volume numbers, or page ranges.\n"
        "- Choose entry type: @article, @book, @inproceedings, @techreport, @misc.\n"
        "- Citation key: a SINGLE alphanumeric token, NO spaces, NO commas, NO special chars.\n"
        "  Format: FirstAuthorSurnameYYYY (e.g. Smith2010, MullerUNKNOWN). One word only.\n"
        "- Return ONLY the BibTeX entry, no markdown, no explanation.\n"
        "- Example format:\n"
        "  @article{Smith2010,\n"
        "    author = {Smith, John},\n"
        "    title = {My Title},\n"
        "    year = {2010},\n"
        "  }\n\n"
        f"Filename: {filename}\nStored title: {title}\n\n"
        f"--- FIRST PAGE ---\n{text[:2500]}\n--- END ---"
    )
    try:
        r = requests.post(
            OLLAMA_URL,
            json={
                "model": OLLAMA_MODEL,
                "messages": [{"role": "user", "content": prompt}],
                "stream": False,
            },
            timeout=60,
        )
        entry = r.json()["message"]["content"].strip()
        if not entry.startswith("@"):
            idx = entry.find("@")
            entry = entry[idx:] if idx >= 0 else ""
        return entry or None
    except Exception:
        return None


def validate(entry, source_text):
    """Return (ok, warnings). ok=False means reject entirely."""
    if not entry or not entry.startswith("@"):
        return False, ["no valid bibtex structure"]
    for req in ("title", "author"):
        val = get_field(req, entry)
        if not val or val.upper() == "UNKNOWN":
            return False, [f"missing required field: {req}"]
    warnings = []
    for fname in ("journal", "booktitle", "volume", "pages", "year"):
        val = get_field(fname, entry)
        if val and val.upper() == "UNKNOWN":
            warnings.append(f"{fname}=UNKNOWN")
    yr = get_field("year", entry)
    if yr and yr.upper() != "UNKNOWN" and yr not in source_text:
        warnings.append(f"year={yr} not in source text — verify")
    return True, warnings


# ── main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Generate BibTeX for Qdrant docs not in master bib")
    parser.add_argument("--bib",   default=DEFAULT_BIB,  help="Existing .bib file to compare against (omit to generate for all Qdrant docs)")
    parser.add_argument("--out",   default=DEFAULT_OUT,  help="Output bib file")
    parser.add_argument("--log",   default=DEFAULT_LOG,  help="Log file")
    parser.add_argument("--ckpt",  default=DEFAULT_CKPT, help="Checkpoint file")
    parser.add_argument("--reset", action="store_true",  help="Clear checkpoint and start over")
    args = parser.parse_args()

    if args.reset and os.path.exists(args.ckpt):
        os.remove(args.ckpt)
        print("Checkpoint cleared.")

    # Load master bib titles
    bib_titles = set()
    if args.bib and os.path.exists(args.bib):
        with open(args.bib) as f:
            raw = f.read()
        for block in re.split(r"(?=@\w+\s*\{)", raw):
            block = block.strip()
            if not block.startswith("@"):
                continue
            t = re.sub(r"[{}]", "", get_field("title", block))
            nt = norm(t)
            if nt:
                bib_titles.add(nt)
        print(f"Master bib: {args.bib} ({len(bib_titles)} titles)")
    elif args.bib:
        print(f"Warning: bib file not found: {args.bib} — comparing against empty set")
    else:
        print("No --bib provided — generating for all Qdrant docs")

    # Load Qdrant docs
    client = QdrantClient(QDRANT_HOST, port=QDRANT_PORT)
    docs = {}
    offset = None
    total = 0
    while True:
        pts, offset = client.scroll(
            COLLECTION, limit=500, offset=offset,
            with_payload=True, with_vectors=False
        )
        for p in pts:
            meta = (p.payload or {}).get("metadata", {})
            sp = meta.get("source_path", "")
            if not sp:
                continue
            pg = meta.get("page_number", 9999)
            txt = (p.payload or {}).get("text", "")
            if sp not in docs:
                docs[sp] = {
                    "title": meta.get("document_title", "")
                              or os.path.splitext(os.path.basename(sp))[0],
                    "best_page": 9999,
                    "page1_text": "",
                }
            if pg < docs[sp]["best_page"] and txt.strip():
                docs[sp]["best_page"] = pg
                docs[sp]["page1_text"] = txt
        total += len(pts)
        print(f"  Scanned {total} chunks...", end="\r", flush=True)
        if offset is None:
            break
    print(f"\nQdrant docs: {len(docs)}")

    # Filter docs not in bib
    to_generate = []
    for sp, info in docs.items():
        nt = norm(info["title"])
        if nt and nt in bib_titles:
            continue
        if nt and any(
            (len(nt) > 15 and nt in bt) or (len(bt) > 15 and bt in nt)
            for bt in bib_titles
        ):
            continue
        to_generate.append((sp, info))
    print(f"Docs needing BibTeX: {len(to_generate)}")

    # Load checkpoint
    checkpoint = {}
    if os.path.exists(args.ckpt):
        with open(args.ckpt) as f:
            checkpoint = json.load(f)
    already_done = len(checkpoint)
    if already_done:
        print(f"Resuming: {already_done} already processed, {len(to_generate) - already_done} remaining")

    # Init output (append)
    if not os.path.exists(args.out):
        with open(args.out, "w") as f:
            f.write("% Generated BibTeX — incremental, resumes on restart\n")
            f.write("% CrossRef entries are authoritative; LLM entries marked % VERIFY where uncertain\n\n")

    log = open(args.log, "a")

    ok = sum(1 for v in checkpoint.values() if v == "ok")
    skipped = sum(1 for v in checkpoint.values() if v.startswith("skip"))
    invalid = sum(1 for v in checkpoint.values() if v.startswith("invalid"))

    for sp, info in to_generate:
        if sp in checkpoint:
            continue

        filename = os.path.basename(sp)
        text = info["page1_text"].strip()
        done = sum(1 for v in checkpoint.values())
        print(f"  [{done+1}/{len(to_generate)}] {filename[:60]}")

        if not text:
            checkpoint[sp] = "skip:no_text"
            log.write(f"SKIP_NO_TEXT: {filename}\n")
            log.flush()
            skipped += 1
            with open(args.ckpt, "w") as f:
                json.dump(checkpoint, f)
            continue

        entry = None
        method = "llm"
        doi = extract_doi(text)
        if doi:
            cr = crossref_bibtex(doi)
            if cr:
                entry = cr
                method = f"crossref:{doi}"

        if not entry:
            entry = llm_extract(filename, info["title"], text)

        if not entry:
            checkpoint[sp] = "invalid:no_entry"
            log.write(f"ERROR: {filename}\n")
            log.flush()
            invalid += 1
            with open(args.ckpt, "w") as f:
                json.dump(checkpoint, f)
            continue

        is_ok, warnings = validate(entry, text)
        if not is_ok:
            checkpoint[sp] = f"invalid:{warnings[0]}"
            log.write(f"INVALID ({warnings[0]}): {filename}\n")
            log.flush()
            invalid += 1
            with open(args.ckpt, "w") as f:
                json.dump(checkpoint, f)
            continue

        with open(args.out, "a") as f:
            f.write(f"% source: {filename}\n")
            f.write(f"% method: {method}\n")
            if warnings:
                f.write(f"% VERIFY: {', '.join(warnings)}\n")
            f.write(entry + "\n\n")

        checkpoint[sp] = "ok"
        log.write(f"OK [{method}]: {filename}\n")
        log.flush()
        ok += 1
        with open(args.ckpt, "w") as f:
            json.dump(checkpoint, f)

    log.close()
    print(f"\n{'='*60}")
    print(f"Done: {ok} OK | {skipped} skipped (no text) | {invalid} invalid/rejected")
    print(f"Output:     {args.out}")
    print(f"Checkpoint: {args.ckpt}")


if __name__ == "__main__":
    main()
