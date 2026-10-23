"""
Library conventions and the checks that enforce them, shared by
scripts/add_book.py, the MCP indexing tools and scripts/check_library.py.

Conventions:
  - Every book lives in a shelf folder under BOOKS_DIR (never the root).
  - Filenames are "Author - Title (Year).ext": "A & B - ..." for two authors,
    "A, B & C - ..." for three, "A et al. - ..." for more, "Name (ed.) - ..." /
    "A & B (eds.) - ..." for edited volumes; subtitles joined with " - ".
  - The indexed title (metadata.document_title) is derived from the filename.
  - Things that are not books or papers (figures, style guides, one-page
    scraps) go to ~/Books Extra/debris, which is not indexed.
"""

import os
import re
from pathlib import Path
from typing import List

import fitz  # PyMuPDF

try:
    from config import BOOKS_DIR
except ImportError:
    BOOKS_DIR = "~/Books"
BOOKS_DIR = os.path.expanduser(BOOKS_DIR)
DEBRIS_DIR = os.path.expanduser("~/Books Extra/debris")

BOOK_EXTENSIONS = (".pdf", ".epub")

# Raw-download and slug patterns that a curated filename never contains.
_RAW_PATTERNS = [
    (re.compile(r" -- "), "download-site name (' -- ' separators)"),
    (re.compile(r"isbn13|Anna.s Archive|libgen|z-lib", re.I), "download-site name"),
    (re.compile(r"%[0-9A-Fa-f]{2}"), "URL-encoded characters"),
    (re.compile(r"^\[[^\]]*\]"), "leading [journal/series] tag"),
    (re.compile(r"\{[^}]*\}"), "{...} tag"),
    (re.compile(r"_"), "underscores"),
    (re.compile(r"\b(Untitled|Unknown|Document Title|Microsoft Word)\b", re.I), "placeholder name"),
]


def title_from_filename(path: str) -> str:
    """Derive a clean title from the filename."""
    name = os.path.splitext(os.path.basename(path))[0]
    # Strip leading [N] or (N) markers
    name = re.sub(r'^\[\d+\]\s*', '', name)
    name = re.sub(r'^\(\d+\)\s*', '', name)
    # Strip trailing _indexed, _ocr, _ridotto suffixes
    name = re.sub(r'[_\s]+(indexed|ocr|ridotto|reduced|compressed)$', '', name, flags=re.IGNORECASE)
    # Replace underscores with spaces
    name = name.replace('_', ' ')
    return name.strip()


def name_problems(path: str) -> List[str]:
    """Ways a filename departs from "Author - Title (Year)"; empty if it conforms.
    A missing year is not a problem by itself (many papers have none on them)."""
    name = os.path.splitext(os.path.basename(path))[0]
    problems = [why for rx, why in _RAW_PATTERNS if rx.search(name)]
    if " - " not in name:
        problems.append("no 'Author - Title' separator")
    elif re.fullmatch(r"[a-z0-9\-]+", name.replace(" - ", "-")):
        problems.append("lowercase slug")
    return list(dict.fromkeys(problems))


def location_problems(path: str) -> List[str]:
    """Where the file sits relative to the library's shelves."""
    p = Path(path).resolve()
    root = Path(BOOKS_DIR).resolve()
    if root not in p.parents:
        return [f"not inside {BOOKS_DIR}"]
    if p.parent == root:
        return ["at the library root, not in a shelf folder"]
    return []


def text_layer_status(path: str, sample_pages: int = 8) -> str:
    """'text', 'none' (scanned: will be OCR'd), or 'garbled' (few word-like tokens
    in the text layer). 'garbled' is a suspicion to check by eye: broken font
    encodings (fix with force-OCR) score like this, but so do music scores,
    formula-heavy pages and invented scripts. EPUBs are always 'text'."""
    if path.lower().endswith(".epub"):
        return "text"
    try:
        doc = fitz.open(path)
    except Exception:
        return "none"
    n = len(doc)
    # Sample pages spread through the book, skipping covers.
    picks = sorted({min(n - 1, int(n * f)) for f in (0.1, 0.25, 0.4, 0.5, 0.6, 0.75, 0.9)} | {0})[:sample_pages]
    text = " ".join(doc[i].get_text() for i in picks)
    doc.close()
    tokens = text.split()
    if len(tokens) < 30:
        return "none"
    wordlike = sum(1 for t in tokens if re.fullmatch(r"[^\W\d_]{2,}[.,;:!?)\]'’\"”]*", t))
    return "text" if wordlike / len(tokens) >= 0.55 else "garbled"


def junk_hint(path: str) -> str:
    """A reason this file looks like debris rather than a book/paper, or ''."""
    if path.lower().endswith(".epub"):
        return ""
    try:
        doc = fitz.open(path)
        pages, words = len(doc), len(doc[0].get_text().split()) if len(doc) else 0
        creator = (doc.metadata or {}).get("creator", "") or ""
        doc.close()
    except Exception:
        return ""
    if pages == 1 and words < 150:
        return "single page with little text (figure or scrap?)"
    if re.search(r"OmniGraffle|Keynote|PowerPoint", creator) and pages <= 2:
        return f"made with {creator.split()[0]} (figure/slide?)"
    return ""


# --- After indexing ---------------------------------------------------------

def _hash_filter(models, file_hash):
    return models.Filter(must=[models.FieldCondition(key="metadata.file_hash",
                                                     match=models.MatchValue(value=file_hash))])


def set_filename_title(client, collection: str, file_hash: str, path: str, title: str = None) -> str:
    """Set document_title from the filename (or `title`), keeping the extracted
    one as document_title_original. Returns the title set."""
    from qdrant_client import models
    flt = _hash_filter(models, file_hash)
    pts, _ = client.scroll(collection, scroll_filter=flt, limit=1, with_vectors=False,
                           with_payload=["metadata.document_title", "metadata.document_title_original"])
    if not pts:
        return ""
    meta = pts[0].payload.get("metadata", {})
    new = title or title_from_filename(path)
    payload = {"document_title": new}
    if not meta.get("document_title_original") and meta.get("document_title") != new:
        payload["document_title_original"] = meta.get("document_title", "")
    client.set_payload(collection, payload=payload, key="metadata", points=flt, wait=True)
    return new


def verify_indexed(client, collection: str, file_hash: str) -> dict:
    """Chunk count and page coverage of an indexed book."""
    from qdrant_client import models
    pages, total, chunks, offset = set(), 0, 0, None
    while True:
        pts, offset = client.scroll(collection, scroll_filter=_hash_filter(models, file_hash), limit=2000,
                                    offset=offset, with_vectors=False,
                                    with_payload=["metadata.page_number", "metadata.total_pages"])
        for p in pts:
            m = p.payload.get("metadata", {})
            pages.add(m.get("page_number"))
            total = m.get("total_pages") or total
        chunks += len(pts)
        if offset is None:
            break
    return {"chunks": chunks, "pages_with_text": len(pages), "total_pages": total,
            "coverage": len(pages) / total if total else 0.0}


def warnings_for(path: str) -> List[str]:
    """Human-readable convention warnings for a file about to be (or just) indexed."""
    out = [f"location: {p}" for p in location_problems(path)]
    out += [f"name: {p}" for p in name_problems(path)]
    junk = junk_hint(path)
    if junk:
        out.append(f"junk? {junk} (debris goes to {DEBRIS_DIR})")
    status = text_layer_status(path)
    if status == "garbled":
        out.append("text layer looks garbled: check search results; use reindex_with_ocr if so")
    return out
