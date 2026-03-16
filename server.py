#!/usr/bin/env python3
"""
Knowledge Base MCP Server
Multi-threaded PDF indexing, vector search, and Anna's Archive integration.
"""

import os
import sys
import re
import json

# Suppress MuPDF noise (cmsOpenProfileFromMem, appearance stream errors).
# Must happen before pdf_processor is imported (which imports fitz).
_devnull_fd = os.open(os.devnull, os.O_WRONLY)
os.dup2(_devnull_fd, 2)
os.close(_devnull_fd)
del _devnull_fd
sys.stderr = open(os.devnull, 'w')

import time
import hashlib
import concurrent.futures
import platform
import shutil
from pathlib import Path
from typing import List

import logging
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

import ollama
from qdrant_client.models import PointStruct
from mcp.server.fastmcp import FastMCP
from tqdm import tqdm
import requests
from bs4 import BeautifulSoup

from pdf_processor import (
    TextChunk, PDFProcessor,
    _process_single_pdf, _worker_init,
    MAX_WORKERS
)
from vector_db import VectorDBClient, get_db, OLLAMA_MODEL, COLLECTION_NAME


# Anna's Archive configuration
USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
ANNAS_URLS = ["https://annas-archive.gl", "https://annas-archive.se", "https://annas-archive.org", "https://annas-archive.li"]

mcp = FastMCP("knowledge-server-optimized")

# Cache of file hashes known to have no extractable text — persisted to disk
# so index_library skips them instantly on future runs without opening the file.
try:
    from config import EMPTY_HASH_CACHE as _EMPTY_HASH_CACHE_PATH
    _EMPTY_HASH_CACHE_PATH = os.path.expanduser(_EMPTY_HASH_CACHE_PATH)
except ImportError:
    _EMPTY_HASH_CACHE_PATH = os.path.expanduser("~/.local/share/knowledge_server/empty_hashes.json")

def _load_empty_hashes() -> set:
    try:
        with open(_EMPTY_HASH_CACHE_PATH) as f:
            return set(json.load(f))
    except Exception:
        return set()

def _save_empty_hash(file_hash: str):
    try:
        os.makedirs(os.path.dirname(_EMPTY_HASH_CACHE_PATH), exist_ok=True)
        hashes = _load_empty_hashes()
        hashes.add(file_hash)
        with open(_EMPTY_HASH_CACHE_PATH, "w") as f:
            json.dump(list(hashes), f)
    except Exception:
        pass


def _get_annas_response(endpoint: str, params: dict = None, stream: bool = False):
    """Try each Anna's Archive mirror in order."""
    last_error = None
    for base_url in ANNAS_URLS:
        url = f"{base_url}{endpoint}"
        try:
            resp = requests.get(url, params=params, headers={'User-Agent': USER_AGENT}, timeout=10, stream=stream)
            resp.raise_for_status()
            return resp, base_url
        except Exception as e:
            last_error = e
            continue
    raise last_error


def _index_directory(directory_path: str = None, use_ocr: bool = False, delete_all: bool = False, file_list: list = None) -> tuple:
    """
    Core indexing function. Parses PDFs in parallel (ProcessPool), then
    embeds and upserts in the main thread with incremental Qdrant writes.
    Returns (indexed, skipped, errors, ocr_skipped).
    """
    db = get_db()

    if delete_all:
        db.delete_collection()

    if file_list:
        all_pdfs = [Path(p) for p in file_list if Path(p).exists()]
        directory_path = directory_path or str(Path(file_list[0]).parent)
    elif directory_path:
        all_pdfs = list(Path(directory_path).rglob("*.pdf")) + list(Path(directory_path).rglob("*.epub"))
    else:
        return 0, 0, 0, 0

    total_pdfs = len(all_pdfs)
    if total_pdfs == 0:
        return 0, 0, 0, 0

    empty_hashes = _load_empty_hashes()

    indexed_hashes = db.get_all_indexed_hashes()

    stats = {"indexed": 0, "skipped": 0, "errors": 0, "ocr_skipped": 0}
    start_time = time.time()
    total_vectors = 0

    worker_args = [(p, directory_path, use_ocr, indexed_hashes, empty_hashes) for p in all_pdfs]

    with concurrent.futures.ProcessPoolExecutor(max_workers=MAX_WORKERS, initializer=_worker_init) as executor:
        futures = {executor.submit(_process_single_pdf, arg): arg[0] for arg in worker_args}

        for future in tqdm(concurrent.futures.as_completed(futures), total=total_pdfs, desc="Parsing PDFs", unit="file"):
            file_path = futures[future]
            try:
                res = future.result()

                if res["status"] == "already_indexed":
                    stats["skipped"] += 1
                    continue

                if res["status"] == "error":
                    print(f"❌ Error: {file_path.name}: {res.get('error_message')}")
                    stats["errors"] += 1
                    continue

                if res["status"] == "skipped_ocr":
                    stats["ocr_skipped"] += 1
                    continue

                if res["status"] == "error_no_text":
                    print(f"⚠️  No text: {file_path.name}")
                    if res.get("file_hash"):
                        _save_empty_hash(res["file_hash"])
                    stats["errors"] += 1
                    continue

                chunks: List[TextChunk] = res["chunks"]
                ocr_tag = " [OCR]" if res.get("used_ocr") else ""
                print(f"⏳ Embedding: {file_path.name} ({len(chunks)} chunks){ocr_tag}")

                FLUSH_EVERY = 100
                points = []
                book_vectors = 0

                for chunk in chunks:
                    try:
                        text_to_embed = chunk.text[:700].strip()
                        if not text_to_embed:
                            continue

                        try:
                            response = ollama.embeddings(model=OLLAMA_MODEL, prompt=text_to_embed)
                        except Exception:
                            continue

                        embedding = response["embedding"]
                        point_id = hashlib.md5(
                            (res["file_path"] + str(chunk.chunk_index)).encode()
                        ).hexdigest()

                        points.append(PointStruct(
                            id=point_id,
                            vector=embedding,
                            payload={"text": text_to_embed, "metadata": chunk.to_metadata()}
                        ))

                        if len(points) >= FLUSH_EVERY:
                            db.upsert_points(points)
                            book_vectors += len(points)
                            points = []

                    except Exception as e:
                        print(f"  ⚠️  Chunk error in {file_path.name}: {e}", file=sys.stderr)

                if points:
                    db.upsert_points(points)
                    book_vectors += len(points)

                if book_vectors:
                    stats["indexed"] += 1
                    total_vectors += book_vectors
                    elapsed = time.time() - start_time
                    done = stats["indexed"]
                    processable = total_pdfs - len(indexed_hashes)
                    if done > 0 and processable > 0:
                        rate = done / (elapsed / 60)
                        remaining = processable - done
                        eta_min = remaining / rate if rate > 0 else 0
                        eta_str = f"{eta_min/60:.1f}h" if eta_min > 90 else f"{eta_min:.0f}min"
                        print(f"  ✅ Done: {file_path.name} ({book_vectors} chunks) | {done}/{processable} files | ETA ~{eta_str}")
                    else:
                        print(f"  ✅ Done: {file_path.name} ({book_vectors} chunks)")

            except Exception as e:
                stats["errors"] += 1
                print(f"  ❌ Failed: {file_path.name}: {e}", file=sys.stderr)

    return stats["indexed"], stats["skipped"], stats["errors"], stats["ocr_skipped"]


# ---------------------------------------------------------------------------
# MCP Tools
# ---------------------------------------------------------------------------

@mcp.tool()
def index_library(path: str, delete_all: bool = False) -> str:
    """Indicizza PDF in parallelo e velocemente."""
    if not path:
        return "❌ Path richiesto"
    try:
        indexed, skipped, errors, ocr_skip = _index_directory(path, use_ocr=False, delete_all=delete_all)
        result = f"""✅ Indicizzazione ottimizzata completata!

📊 Risultati:
• PDF indicizzati: {indexed}
• Già presenti: {skipped}
• Richiedono OCR: {ocr_skip}
• Errori: {errors}
"""
        if ocr_skip > 0:
            result += f"\n💡 Tip: {ocr_skip} PDF richiedono OCR (usa index_with_ocr)."
        return result
    except Exception as e:
        import traceback
        return f"❌ Errore: {str(e)}\n{traceback.format_exc()}"


@mcp.tool()
def index_single_pdf(file_path: str) -> str:
    """Indicizza un singolo PDF (con OCR se necessario)."""
    if not file_path:
        return "❌ Path richiesto"
    try:
        indexed, skipped, errors, ocr_skip = _index_directory(file_list=[file_path], use_ocr=True)
        if skipped > 0:
            return f"⏭️  Already indexed: {file_path}"
        if indexed > 0:
            ocr_note = " (OCR)" if ocr_skip == 0 else ""
            return f"✅ Indexed{ocr_note}: {file_path}"
        return f"⚠️  No text extracted from: {file_path}"
    except Exception as e:
        import traceback
        return f"❌ Errore: {str(e)}\n{traceback.format_exc()}"


@mcp.tool()
def index_with_ocr(path: str) -> str:
    """Indicizza con OCR (Lento ma accurato per scansioni)."""
    if not path:
        return "❌ Path richiesto"
    try:
        indexed, skipped, errors, _ = _index_directory(path, use_ocr=True, delete_all=False)
        return f"✅ OCR Completato. Indicizzati: {indexed}, Saltati: {skipped}, Errori: {errors}"
    except Exception as e:
        return f"❌ Errore: {str(e)}"


@mcp.tool()
def search_text(term: str, n_results: int = 5) -> str:
    """Literal text search across all indexed chunks. Use for exact terms, names, or coined words."""
    if not term:
        return "❌ Term richiesto"
    from qdrant_client.http.models import Filter, FieldCondition, MatchText
    db = get_db()
    try:
        results, _ = db.client.scroll(
            collection_name=COLLECTION_NAME,
            scroll_filter=Filter(must=[FieldCondition(key="text", match=MatchText(text=term))]),
            limit=n_results,
            with_payload=True,
            with_vectors=False
        )
        if not results:
            return f"No results for '{term}'."
        formatted = []
        for i, r in enumerate(results, 1):
            meta = (r.payload or {}).get("metadata", {})
            text = (r.payload or {}).get("text", "")
            # highlight the term in context
            idx = text.lower().find(term.lower())
            snippet = text[max(0, idx-100):idx+200].replace("\n", " ") if idx != -1 else text[:200]
            formatted.append(
                f"{i}. {meta.get('document_title', 'Unknown')}\n"
                f"   Page {meta.get('page_number', '?')}/{meta.get('total_pages', '?')}\n"
                f"   Path: {meta.get('source_path', 'N/A')}\n"
                f"   ...{snippet}..."
            )
        return "\n\n".join(formatted)
    except Exception as e:
        return f"❌ Errore: {str(e)}"


@mcp.tool()
def query_library(query: str, n_results: int = 5) -> str:
    """Cerca nella knowledge base."""
    if not query:
        return "❌ Query richiesta"
    db = get_db()
    try:
        response = ollama.embeddings(model=OLLAMA_MODEL, prompt=query)
        results = db.search(response["embedding"], limit=n_results)
        if not results:
            return "Nessun risultato."
        formatted = []
        for i, result in enumerate(results, 1):
            meta = result['metadata']
            formatted.append(
                f"{i}. [{result['score']:.3f}] {meta.get('document_title', 'Unknown')}\n"
                f"   Page {meta.get('page_number', '?')}/{meta.get('total_pages', '?')}\n"
                f"   Path: {meta.get('source_path', meta.get('source', 'N/A'))}\n"
                f"   Hash: {meta.get('file_hash', 'N/A')}\n"
                f"   {result['text'][:300]}...\n"
            )
        return "\n".join(formatted)
    except Exception as e:
        return f"❌ Errore: {str(e)}"


@mcp.tool()
def get_document_info(file_hash: str) -> str:
    """Info documento e struttura via hash."""
    db = get_db()
    try:
        chunks = db.get_chunks_by_file(file_hash)
        if not chunks:
            return "❌ Documento non trovato"
        meta = chunks[0]["metadata"]
        return f"📚 {meta.get('document_title','Unknown')}\nFile: {meta.get('source')}\nPagine: {meta.get('total_pages')}\nChunks: {len(chunks)}"
    except Exception as e:
        return f"Error: {e}"


@mcp.tool()
def read_page(file_hash: str, page_number: int) -> str:
    """Leggi pagina specifica."""
    db = get_db()
    try:
        chunks = db.get_chunks_by_file(file_hash)
        page_chunks = [c for c in chunks if c["metadata"].get("page_number") == page_number]
        if not page_chunks:
            return "Pagina non trovata"
        return "\n\n".join([c["text"] for c in page_chunks])
    except Exception as e:
        return str(e)


@mcp.tool()
def reconstruct_document(file_hash: str) -> str:
    """Ricostruisci intero documento."""
    db = get_db()
    try:
        chunks = db.get_chunks_by_file(file_hash)
        return "\n\n".join([c["text"] for c in chunks])
    except Exception as e:
        return str(e)


@mcp.tool()
def open_pdf_page(file_path: str, page_number: int = 1) -> str:
    """Open a PDF file at a specific page using the system default PDF viewer."""
    import subprocess
    if not os.path.exists(file_path):
        return f"❌ File not found: {file_path}"
    try:
        system = platform.system()
        if system == "Darwin":
            script = (
                f'tell application "Preview" to open POSIX file "{file_path}"\n'
                f'delay 1\n'
                f'tell application "Preview" to tell front document '
                f'to set current page to page {page_number}'
            )
            subprocess.Popen(["osascript", "-e", script])
        elif system == "Windows":
            sumatra = shutil.which("SumatraPDF")
            if sumatra:
                subprocess.Popen([sumatra, "-page", str(page_number), file_path])
            else:
                os.startfile(file_path)
        else:
            if shutil.which("evince"):
                subprocess.Popen(["evince", f"--page-index={page_number - 1}", file_path])
            elif shutil.which("okular"):
                subprocess.Popen(["okular", "--page", str(page_number), file_path])
            elif shutil.which("zathura"):
                subprocess.Popen(["zathura", "--page", str(page_number - 1), file_path])
            else:
                subprocess.Popen(["xdg-open", file_path])
                return f"✅ Opened {os.path.basename(file_path)} (page navigation not supported by default viewer)"
        return f"✅ Opened {os.path.basename(file_path)} at page {page_number}"
    except Exception as e:
        return f"❌ Could not open PDF: {e}"


@mcp.tool()
def search_annas_archive(query: str, limit: int = 5, lang: str = '', ext: str = '') -> str:
    """
    Cerca libri su Anna's Archive.
    Supporta ricerca semantica automatica se la query sembra naturale.
    Ritorna una lista di risultati con MD5 per il download.
    """
    search_query = query
    if len(query.split()) > 3:
        try:
            prompt = f"Convert this natural language request into specific keywords for a book search. Output ONLY keywords. Request: '{query}'"
            response = ollama.generate(model="deepseek-r1:7b", prompt=prompt, stream=False)
            if 'response' in response:
                keywords = response['response'].strip().replace('"', '')
                if "</think>" in keywords:
                    keywords = keywords.split("</think>")[-1].strip()
                search_query = keywords
        except Exception:
            pass

    params = {'q': search_query}
    if lang:
        params['lang'] = lang
    if ext:
        params['ext'] = ext

    try:
        response = None
        current_url = None
        last_error = None
        for base_url in ANNAS_URLS:
            url = f"{base_url}/search"
            try:
                resp = requests.get(url, params=params, headers={'User-Agent': USER_AGENT}, timeout=10)
                resp.raise_for_status()
                response = resp
                current_url = base_url
                break
            except Exception as e:
                last_error = e
                continue

        if not response:
            raise last_error

        soup = BeautifulSoup(response.content, 'html.parser')
        results = []
        seen_md5s = set()

        for a_tag in soup.find_all('a', href=True):
            href = a_tag['href']
            if '/md5/' in href:
                md5 = href.split('/md5/')[1]
                title = a_tag.get_text(separator=" ", strip=True)
                if not title or len(title) < 3:
                    continue
                if md5 not in seen_md5s:
                    seen_md5s.add(md5)
                    container = a_tag.parent
                    full_text = container.get_text(separator=" | ", strip=True)
                    results.append(f"{len(results)+1}. {title}\n   MD5: {md5}\n   Info: {full_text[:150]}...")
                if len(results) >= limit:
                    break

        if not results:
            return f"❌ Nessun risultato trovato per '{search_query}'."

        return f"V2.1 - {len(results)} risultati\n\n" + "\n\n".join(results) + "\n\n💡 Usa 'download_from_annas_archive(md5)' per scaricare."

    except Exception as e:
        return f"❌ Errore ricerca: {e}"


@mcp.tool()
def download_from_annas_archive(md5: str) -> str:
    """
    Scarica un libro da Anna's Archive dato il suo MD5.
    Salva il file in 'alias_books/'.
    Tenta download automatico da IPFS/Libgen, altrimenti ritorna link manuali.
    """
    try:
        response = None
        current_url = None
        last_error = None
        for base_url in ANNAS_URLS:
            url = f"{base_url}/md5/{md5}"
            try:
                resp = requests.get(url, headers={'User-Agent': USER_AGENT}, timeout=10)
                resp.raise_for_status()
                response = resp
                current_url = base_url
                break
            except Exception as e:
                last_error = e
                continue

        if not response:
            raise last_error

        output_dir = "alias_books/"
        os.makedirs(output_dir, exist_ok=True)

        soup = BeautifulSoup(response.content, 'html.parser')

        title = ""
        author = ""
        title_tag = soup.find('div', class_='text-3xl font-bold')
        if title_tag:
            title = title_tag.get_text(strip=True)
        author_div = soup.find('div', class_='italic')
        if author_div:
            author_tag = author_div.find('a')
            if author_tag:
                author = author_tag.get_text(strip=True)
            else:
                author = author_div.get_text(strip=True)

        if not title:
            title = f"Book_{md5}"

        full_name = f"{author} - {title}" if author else title
        safe_name = re.sub(r'[\\/*?:"<>|]', "", full_name).strip().rstrip('.')

        ipfs_links = []
        libgen_links = []
        slow_links = []

        for a in soup.find_all('a', href=True):
            href = a['href']
            if href.startswith('ipfs://'):
                cid = href.replace('ipfs://', '')
                ipfs_links.append(f"https://ipfs.io/ipfs/{cid}")
                ipfs_links.append(f"https://dweb.link/ipfs/{cid}")
            elif 'libgen.li' in href:
                libgen_links.append(href)
            elif '/slow_download/' in href:
                slow_links.append(f"{current_url}{href}")

        for link in ipfs_links + libgen_links:
            try:
                with requests.get(link, stream=True, timeout=20) as r:
                    r.raise_for_status()
                    content_type = r.headers.get('Content-Type', '').lower()
                    if 'application/pdf' in content_type:
                        filepath = os.path.join(output_dir, f"{safe_name}.pdf")
                        with open(filepath, 'wb') as f:
                            for chunk in r.iter_content(chunk_size=8192):
                                f.write(chunk)
                        return f"✅ Download completato: {filepath}"
            except Exception:
                continue

        msg = f"❌ Download automatico fallito. Non sono stati trovati link diretti in formato PDF per '{full_name}'.\n\nEcco i link per il controllo manuale:\n"
        for link in slow_links[:3]:
            msg += f"🔗 Slow Server: {link}\n"
        for link in libgen_links[:1]:
            msg += f"🔗 Libgen: {link}\n"
        return msg

    except Exception as e:
        import traceback
        traceback.print_exc(file=sys.stderr)
        return f"❌ Errore recupero dettagli: {e}"


if __name__ == "__main__":
    mcp.run()
