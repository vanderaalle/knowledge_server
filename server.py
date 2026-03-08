#!/usr/bin/env python3
"""
Knowledge Base MCP Server - OPTIMIZED VERSION
Multi-threaded initialization & Fast PDF processing with PyMuPDF (fitz)

Features:
- Parallel Processing (using ThreadPoolExecutor/ProcessPoolExecutor)
- Fast PDF Text Extraction via PyMuPDF (10x faster than pypdf)
- Intelligent Batching for Embedding Requests
"""

import os
import sys
import re
import time
import hashlib
import asyncio
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple, Set
from dataclasses import dataclass
import concurrent.futures

# Fast PDF Library
try:
    import fitz  # PyMuPDF
except ImportError:
    print("PyMuPDF (fitz) not found. Installing...", file=sys.stderr)
    import subprocess
    subprocess.check_call([sys.executable, "-m", "pip", "install", "pymupdf"])
    import fitz

# Fallback for OCR or specific metadata needs
from pypdf import PdfReader
from pdf2image import convert_from_path
import pytesseract
from PIL import Image

# OCR Configuration
TESSERACT_CMD = os.getenv('TESSERACT_CMD', '/opt/homebrew/bin/tesseract')
POPPLER_PATH = os.getenv('POPPLER_PATH', '/opt/homebrew/bin')
if os.path.exists(TESSERACT_CMD):
    pytesseract.pytesseract.tesseract_cmd = TESSERACT_CMD

import ollama
from qdrant_client import QdrantClient
from qdrant_client.models import VectorParams, Distance, PointStruct
from qdrant_client.http import models
from mcp.server.fastmcp import FastMCP
from tqdm import tqdm
import requests
from bs4 import BeautifulSoup
import shutil


# Configuration
ANNAS_URL = "https://annas-archive.li"
USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
OLLAMA_MODEL = os.getenv('OLLAMA_MODEL', 'mxbai-embed-large')
QDRANT_HOST = os.getenv('QDRANT_HOST', 'localhost')
QDRANT_PORT = int(os.getenv('QDRANT_PORT', '6333'))
COLLECTION_NAME = os.getenv('COLLECTION_NAME', 'pdf_library')
MAX_WORKERS = int(os.getenv('MAX_WORKERS', os.cpu_count() or 4))

# Initialize FastMCP server
mcp = FastMCP("knowledge-server-optimized")


@dataclass
class TextChunk:
    """Chunk di testo semplificato - metadati essenziali"""
    text: str
    chunk_index: int
    page_number: int
    document_title: str
    total_pages: int
    source: str
    source_path: str
    file_hash: str
    
    def to_metadata(self) -> Dict[str, Any]:
        return {
            "chunk_index": self.chunk_index,
            "page_number": self.page_number,
            "document_title": self.document_title,
            "total_pages": self.total_pages,
            "source": self.source,
            "source_path": self.source_path,
            "file_hash": self.file_hash,
        }


class SimpleTextSplitter:
    """Text splitter semplice e veloce"""
    def __init__(self, chunk_size: int = 1000, chunk_overlap: int = 200):
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap
        self.separators = ["\n\n", "\n", ". ", "! ", "? ", " ", ""]
    
    def split_text(self, text: str) -> List[str]:
        """Split text into chunks"""
        final_chunks = []
        separator = ""
        for sep in self.separators:
            if sep == "" or sep in text:
                separator = sep
                break
        
        splits = text.split(separator) if separator else list(text)
        current_chunk = []
        current_length = 0
        
        for split in splits:
            split_len = len(split)
            if current_length + split_len + len(separator) > self.chunk_size:
                if current_chunk:
                    final_chunks.append(separator.join(current_chunk))
                    # Overlap semplificato
                    overlap_chunk = []
                    overlap_len = 0
                    for item in reversed(current_chunk):
                        if overlap_len + len(item) + len(separator) <= self.chunk_overlap:
                            overlap_chunk.insert(0, item)
                            overlap_len += len(item) + len(separator)
                        else:
                            break
                    current_chunk = overlap_chunk
                    current_length = overlap_len
            
            current_chunk.append(split)
            current_length += split_len + len(separator)
        
        if current_chunk:
            final_chunks.append(separator.join(current_chunk))
        
        return final_chunks


class PDFProcessor:
    """Processore PDF - Versione Ottimizzata con PyMuPDF"""
    
    def __init__(self):
        self.text_splitter = SimpleTextSplitter(chunk_size=800, chunk_overlap=150)
    
    def extract_text_from_pdf(self, pdf_path: str, use_ocr: bool = False) -> tuple:
        """
        Estrai testo da PDF usando PyMuPDF (molto più veloce).
        
        Args:
            pdf_path: Percorso PDF
            use_ocr: Se True, usa OCR per pagine senza testo (lento, richiede pypdf/tesseract)
        
        Returns: (full_text, page_texts, total_pages, used_ocr)
        """
        try:
            doc = fitz.open(pdf_path)
            total_pages = len(doc)
            page_texts = []
            full_text = ""
            used_ocr = False
            
            # --- Fast Path: PyMuPDF ---
            for content in doc:
                text = content.get_text()
                page_texts.append(text)
                full_text += text + "\n"
            
            doc.close()
            
            # --- Slow Path: OCR Fallback ---
            # Se il testo è vuoto e OCR è richiesto, usiamo il vecchio metodo (lento)
            if not full_text.strip() and use_ocr:
                try:
                    reader = PdfReader(pdf_path)
                    page_texts = []
                    full_text = ""
                    for i, page in enumerate(reader.pages):
                        page_text = page.extract_text() or ""
                        if not page_text.strip():
                            try:
                                images = convert_from_path(pdf_path, first_page=i+1, last_page=i+1, poppler_path=POPPLER_PATH)
                                if images:
                                    page_text = pytesseract.image_to_string(images[0])
                                    used_ocr = True
                            except:
                                page_text = ""
                        page_texts.append(page_text)
                        full_text += page_text + "\n"
                except Exception as e:
                    print(f"OCR Error for {pdf_path}: {e}", file=sys.stderr)
            
            return full_text, page_texts, total_pages, used_ocr
            
        except Exception as e:
            print(f"Error extracting text from {pdf_path}: {e}", file=sys.stderr)
            return "", [], 0, False
    
    def extract_document_title(self, pdf_path: str, first_page_text: str) -> str:
        """Estrai titolo dal PDF (ottimizzato)."""
        try:
            doc = fitz.open(pdf_path)
            title = doc.metadata.get('title', '')
            doc.close()
            
            if title:
                return str(title)
        except:
            pass
        
        # Prova prime righe
        lines = first_page_text.strip().split('\n')
        for line in lines[:5]:
            line = line.strip()
            if line and len(line) > 3 and len(line) < 200:
                if not re.match(r'^[\d\s\.]+$', line):
                    return line
        
        # Fallback a nome file
        return Path(pdf_path).stem.replace('_', ' ').replace('-', ' ').title()
    
    def chunk_text(self, text: str, page_texts: List[str], total_pages: int, 
                   base_metadata: Dict[str, Any]) -> List[TextChunk]:
        """Crea chunk con metadati essenziali (logica identica per compatibilità)"""
        chunks = self.text_splitter.split_text(text)
        chunk_objects = []
        
        # Calcola boundaries pagine
        page_boundaries = []
        char_count = 0
        for page_text in page_texts:
            page_boundaries.append((char_count, char_count + len(page_text)))
            char_count += len(page_text) + 1
        
        document_title = base_metadata.get('document_title', 'Unknown')
        text_pos = 0
        
        for i, chunk_text in enumerate(chunks):
            # Cerca la posizione approssimativa del chunk nel testo completo
            # (non perfetto se ci sono ripetizioni, ma veloce e sufficiente)
            chunk_start = text.find(chunk_text, text_pos)
            
            # Trova pagina di appartenenza
            page_number = 1
            if chunk_start != -1:
                # Se trovato, aggiorna la posizione per la prossima ricerca
                text_pos = chunk_start + 1
                for page_idx, (start, end) in enumerate(page_boundaries):
                    if chunk_start >= start and chunk_start < end:
                        page_number = page_idx + 1
                        break
            else:
                 # Se non trovato (strano), resetta o stima
                pass

            
            chunk_obj = TextChunk(
                text=chunk_text,
                chunk_index=i,
                page_number=page_number,
                document_title=document_title,
                total_pages=total_pages,
                source=base_metadata.get('source', ''),
                source_path=base_metadata.get('source_path', ''),
                file_hash=base_metadata.get('file_hash', '')
            )
            
            chunk_objects.append(chunk_obj)
        
        return chunk_objects


class VectorDBClient:
    """Client Qdrant - Singleton-ish"""
    
    def __init__(self, host: str = QDRANT_HOST, port: int = QDRANT_PORT):
        self.client = QdrantClient(host=host, port=port)
        self._ensure_collection_exists()
    
    def _ensure_collection_exists(self):
        """Crea collection se non esiste"""
        try:
            collections = self.client.get_collections().collections
            collection_names = [c.name for c in collections]
            
            if COLLECTION_NAME not in collection_names:
                # Dummy embedding request just to get size
                try:
                    test_embedding = ollama.embeddings(model=OLLAMA_MODEL, prompt="test")["embedding"]
                    vector_size = len(test_embedding)
                    
                    self.client.create_collection(
                        collection_name=COLLECTION_NAME,
                        vectors_config=VectorParams(size=vector_size, distance=Distance.COSINE)
                    )
                    print(f"Created collection '{COLLECTION_NAME}'", file=sys.stderr)
                except Exception as e:
                     print(f"Error checking model embedding size: {e}. Is Ollama running?", file=sys.stderr)

        except Exception as e:
            print(f"Error creating/checking collection: {e}", file=sys.stderr)
    
    def upsert_points(self, points: List[PointStruct]):
        """Inserisci o aggiorna punti"""
        try:
            self.client.upsert(collection_name=COLLECTION_NAME, points=points)
        except Exception as e:
            print(f"Error upserting points: {e}", file=sys.stderr)

    def is_file_indexed(self, file_hash: str) -> bool:
        """Controlla se file già indicizzato"""
        try:
            result = self.client.scroll(
                collection_name=COLLECTION_NAME,
                scroll_filter=models.Filter(
                    must=[models.FieldCondition(
                        key="metadata.file_hash",
                        match=models.MatchValue(value=file_hash)
                    )]
                ),
                limit=1,
                with_payload=False,
                with_vectors=False
            )
            return len(result[0]) > 0
        except:
            return False
    
    def search(self, query_vector: List[float], limit: int = 5) -> List[Dict[str, Any]]:
        """Cerca vettori simili"""
        try:
            search_result = self.client.search(
                collection_name=COLLECTION_NAME,
                query_vector=query_vector,
                limit=limit
            )
            
            results = []
            for result in search_result:
                results.append({
                    "text": result.payload.get("text", ""),
                    "metadata": result.payload.get("metadata", {}),
                    "score": result.score
                })
            return results
        except Exception as e:
            print(f"Error searching: {e}", file=sys.stderr)
            return []
    
    def get_chunks_by_file(self, file_hash: str) -> List[Dict[str, Any]]:
        """Recupera tutti i chunk di un file"""
        try:
            all_chunks = []
            offset = None
            
            while True:
                result = self.client.scroll(
                    collection_name=COLLECTION_NAME,
                    scroll_filter=models.Filter(
                        must=[models.FieldCondition(
                            key="metadata.file_hash",
                            match=models.MatchValue(value=file_hash)
                        )]
                    ),
                    limit=100,
                    offset=offset,
                    with_payload=True,
                    with_vectors=False
                )
                
                chunks, next_offset = result
                for chunk in chunks:
                    all_chunks.append({
                        "text": chunk.payload.get("text", ""),
                        "metadata": chunk.payload.get("metadata", {})
                    })
                
                if not next_offset:
                    break
                offset = next_offset
            
            # Ordina per chunk_index
            all_chunks.sort(key=lambda x: x["metadata"].get("chunk_index", 0))
            return all_chunks
        except Exception as e:
            print(f"Error getting chunks: {e}", file=sys.stderr)
            return []

    def get_head_chunks_by_file(self, file_hash: str, limit: int = 5) -> List[Dict[str, Any]]:
        """Recupera solo i primi chunks (per classificazione veloce)"""
        try:
            # Filtra per file_hash E ordina per chunk_index? 
            # Scroll non garantisce ordine, ma se chiediamo limit basso è veloce.
            # Meglio prendere un po' di chunks e ordinarli.
            
            result, _ = self.client.scroll(
                collection_name=COLLECTION_NAME,
                scroll_filter=models.Filter(
                    must=[models.FieldCondition(
                        key="metadata.file_hash",
                        match=models.MatchValue(value=file_hash)
                    )]
                ),
                limit=limit * 2, # Ne prendiamo di più per sicurezza
                with_payload=True,
                with_vectors=False
            )
            
            msg_chunks = []
            for chunk in result:
                msg_chunks.append({
                    "text": chunk.payload.get("text", ""),
                    "metadata": chunk.payload.get("metadata", {})
                })
            
            # Ordina e prendi i primi 'limit'
            msg_chunks.sort(key=lambda x: x["metadata"].get("chunk_index", 0))
            return msg_chunks[:limit]
            
        except Exception as e:
            print(f"Error getting head chunks: {e}", file=sys.stderr)
            return []
    
    def delete_collection(self):
        """Cancella collection intera"""
        try:
            self.client.delete_collection(collection_name=COLLECTION_NAME)
            print(f"Deleted collection '{COLLECTION_NAME}'", file=sys.stderr)
            self._ensure_collection_exists()
            return True
        except Exception as e:
            print(f"Error deleting collection: {e}", file=sys.stderr)
            return False


# Inizializza globali
pdf_processor = PDFProcessor()
# Lazy init di vector_db per evitare problemi di connessione all'avvio se non serve
vector_db = None

def get_db():
    global vector_db
    if vector_db is None:
        vector_db = VectorDBClient()
    return vector_db


def _calculate_file_hash(file_path: Path) -> str:
    """Calcola hash MD5 file (veloce)"""
    hash_md5 = hashlib.md5()
    try:
        with open(file_path, "rb") as f:
            for chunk in iter(lambda: f.read(8192), b""):
                hash_md5.update(chunk)
        return hash_md5.hexdigest()
    except Exception as e:
        print(f"Error hashing {file_path}: {e}", file=sys.stderr)
        return ""


def _process_single_pdf(args):
    """
    Funzione eseguita nel ProcessPool/ThreadPool.
    Prende un file path, estrae testo, calcola hash.
    NON fa chiamate al DB o a Ollama qui per evitare overhead di pickling connessioni.
    Ritorna dict con dati pronti per embedding.
    """
    file_path, directory_path, use_ocr = args
    result = {
        "status": "error",
        "file_path": str(file_path),
        "file_hash": "",
        "chunks": [],
        "skip_reason": None
    }
    
    try:
        # 1. Calc Hash
        current_hash = _calculate_file_hash(file_path)
        result["file_hash"] = current_hash
        
        # 2. Extract Text
        text, page_texts, total_pages, used_ocr = pdf_processor.extract_text_from_pdf(
            str(file_path), use_ocr=use_ocr
        )
        
        if used_ocr and not use_ocr:
            result["status"] = "skipped_ocr"
            return result
            
        if not text.strip():
            result["status"] = "error_no_text"
            return result
        
        # 3. Metadata & Chunking
        document_title = pdf_processor.extract_document_title(str(file_path), text)
        relative_path = os.path.relpath(file_path, directory_path)
        
        base_metadata = {
            "source": relative_path,
            "source_path": str(file_path),
            "file_hash": current_hash,
            "document_title": document_title
        }
        
        chunks = pdf_processor.chunk_text(text, page_texts, total_pages, base_metadata)
        
        result["chunks"] = chunks
        result["status"] = "ok"
        return result
        
    except Exception as e:
        result["error_message"] = str(e)
        return result


def _index_directory(directory_path: str = None, use_ocr: bool = False, delete_all: bool = False, file_list: list = None) -> tuple:
    """
    Indicizza directory o lista di file in PARALLELO.
    """
    if file_list:
        all_pdfs = [Path(p) for p in file_list if Path(p).exists()]
        directory_path = "Custom File List"
    else:
        path_obj = Path(directory_path)
        if not path_obj.exists():
            raise ValueError(f"Path {directory_path} does not exist")
    
    db = get_db()
    
    if delete_all:
        db.delete_collection()
    
    # 1. Scan directory or file
    print(f"📁 Scanning: {directory_path}", file=sys.stderr)
    
    if not file_list:
        if path_obj.is_file():
            if path_obj.suffix.lower() == '.pdf':
                all_pdfs = [path_obj]
            else:
                 print("❌ File is not a PDF", file=sys.stderr)
                 return 0, 0, 0, 0
        else:
            all_pdfs = list(path_obj.rglob("*.pdf"))

    total_pdfs = len(all_pdfs)
    
    if total_pdfs == 0:
        return 0, 0, 0, 0
    
    # 2. Filter indexed files (Batch checking is better if API supports it, but simple loop is fast for DB read)
    # Per speed: get all hashes from DB? No, too many. Check individually but fast.
    # Optimization: Check hash AFTER processing? No, check BEFORE to save CPU.
    # But hash calc takes time. 
    # Strategy: Compute hash in main thread or parallel? 
    # Parallelize everything: Hash check inside worker? No, worker doesn't have DB access.
    # Better: 
    # Phase A: Collect all files.
    # Phase B: Parallel Hash + Extract + Chunk.
    # Phase C: Main thread checks DB (hash already computed) -> if exists allow skip.
    # Phase D: Embedding + Upsert (Parallel).
    
    print(f"📊 Process {total_pdfs} PDFs with {MAX_WORKERS} workers", file=sys.stderr)
    
    stats = {
        "indexed": 0,
        "skipped": 0,
        "errors": 0,
        "ocr_skipped": 0
    }
    
    # Prepare args for workers
    worker_args = [(p, directory_path, use_ocr) for p in all_pdfs]
    
    # Using ThreadPool is easier for IO bound (Ollama), but Parsing is CPU bound.
    # Hybrid approach:
    # 1. ProcessPool for Parsing (CPU)
    # 2. ThreadPool for Embeddings (Network/GPU wait)
    
    # Since we can't easily share the huge QdrantClient/OllamaClient across processes,
    # We use ProcessPool to generate Chunks, then Main thread (or ThreadPool) to Embed & Upsert.
    
    with concurrent.futures.ProcessPoolExecutor(max_workers=MAX_WORKERS) as executor:
        # Submit all parsing jobs
        # Use tqdm for progress
        futures = {executor.submit(_process_single_pdf, arg): arg[0] for arg in worker_args}
        
        for future in tqdm(concurrent.futures.as_completed(futures), total=total_pdfs, desc="Parsing PDFs", unit="file"):
            file_path = futures[future]
            try:
                res = future.result()
                
                if res["status"] == "error":
                    print(f"❌ Error processing {file_path.name}: {res.get('error_message')}", file=sys.stderr)
                    stats["errors"] += 1
                    continue
                
                if res["status"] == "skipped_ocr":
                    stats["ocr_skipped"] += 1
                    continue
                
                if res["status"] == "error_no_text":
                    stats["errors"] += 1
                    continue
                
                # Check duplication
                if db.is_file_indexed(res["file_hash"]):
                    #print(f"  ⏭️  Already indexed: {file_path.name}", file=sys.stderr)
                    stats["skipped"] += 1
                    continue
                
                # Now we have chunks ready for embedding.
                # Do this in current thread (or another ThreadPool if we want to pipeline parsing/embedding)
                # For simplicity, do it here. Ollama is likely the bottleneck.
                chunks: List[TextChunk] = res["chunks"]
                
                points = []
                for chunk in chunks:
                    try:
                        # Truncate to safe length (more aggressive to avoid context limits)
                        text_to_embed = chunk.text[:700].strip() 
                        
                        if not text_to_embed:
                             continue

                        try:
                            response = ollama.embeddings(model=OLLAMA_MODEL, prompt=text_to_embed)
                        except Exception as ollama_err:
                            print(f"  ❌ Ollama Error for {file_path.name}: {ollama_err} (Prompt len: {len(text_to_embed)}, Model: {OLLAMA_MODEL})", file=sys.stderr)
                            continue
                            
                        embedding = response["embedding"]
                        
                        point_id = hashlib.md5(
                            (res["file_path"] + str(chunk.chunk_index)).encode()
                        ).hexdigest()
                        
                        point = PointStruct(
                            id=point_id,
                            vector=embedding,
                            payload={
                                "text": text_to_embed, 
                                "metadata": chunk.to_metadata()
                            }
                        )
                        points.append(point)
                    except Exception as e:
                        print(f"  ⚠️  Embedding error {file_path.name}: {e}", file=sys.stderr)
                
                if points:
                    db.upsert_points(points)
                    stats["indexed"] += 1
                    #print(f"  ✅ Indexed {file_path.name} ({len(points)} chunks)", file=sys.stderr)
            
            except Exception as e:
                print(f"CRITICAL ERROR processing {file_path}: {e}", file=sys.stderr)
                stats["errors"] += 1
    
    return stats["indexed"], stats["skipped"], stats["errors"], stats["ocr_skipped"]


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
        return f"❌ Errore critico: {str(e)}\n{traceback.format_exc()}"


@mcp.tool()
def index_with_ocr(path: str) -> str:
    """Indicizza con OCR (Lento ma accurato per scansioni)."""
    if not path: return "❌ Path richiesto"
    try:
        indexed, skipped, errors, _ = _index_directory(path, use_ocr=True, delete_all=False)
        return f"✅ OCR Completato. Indicizzati: {indexed}, Saltati: {skipped}, Errori: {errors}"
    except Exception as e:
        return f"❌ Errore: {str(e)}"


# --- Tools inviariati (Query, Read, Info, etc.) ---
# Per brevità riutilizziamo la logica, ma dobbiamo riesporli come tool MCP.

@mcp.tool()
def query_library(query: str, n_results: int = 5) -> str:
    """Cerca nella knowledge base."""
    if not query: return "❌ Query richiesta"
    db = get_db()
    try:
        response = ollama.embeddings(model=OLLAMA_MODEL, prompt=query)
        results = db.search(response["embedding"], limit=n_results)
        
        if not results: return "Nessun risultato."
        
        formatted = []
        for i, result in enumerate(results, 1):
            meta = result['metadata']
            formatted.append(
                f"{i}. [{result['score']:.3f}] {meta.get('document_title', 'Unknown')}\n"
                f"   Pagina {meta.get('page_number', '?')}/{meta.get('total_pages', '?')}\n"
                f"   File: {meta.get('source', 'N/A')}\n"
                f"   {result['text'][:300]}...\n"
            )
        return "\n".join(formatted)
    except Exception as e: return f"❌ Errore: {str(e)}"

@mcp.tool()
def get_document_info(file_hash: str) -> str:
    """Info documento e struttura via hash."""
    db = get_db()
    try:
        chunks = db.get_chunks_by_file(file_hash)
        if not chunks: return "❌ Documento non trovato"
        meta = chunks[0]["metadata"]
        return f"📚 {meta.get('document_title','Unknown')}\nFile: {meta.get('source')}\nPagine: {meta.get('total_pages')}\nChunks: {len(chunks)}"
    except Exception as e: return f"Error: {e}"

@mcp.tool()
def read_page(file_hash: str, page_number: int) -> str:
    """Leggi pagina specifica."""
    db = get_db()
    try:
        chunks = db.get_chunks_by_file(file_hash)
        page_chunks = [c for c in chunks if c["metadata"].get("page_number") == page_number]
        if not page_chunks: return "Pagina non trovata"
        return "\n\n".join([c["text"] for c in page_chunks])
    except Exception as e: return str(e)

@mcp.tool()
def reconstruct_document(file_hash: str) -> str:
    """Ricostruisci intero documento."""
    db = get_db()
    try:
        chunks = db.get_chunks_by_file(file_hash)
        return "\n\n".join([c["text"] for c in chunks])
    except Exception as e: return str(e)





@mcp.tool()
def search_annas_archive(query: str, limit: int = 5, lang: str = '', ext: str = '') -> str:
    """
    Cerca libri su Anna's Archive.
    Supporta ricerca semantica automatica se la query sembra naturale.
    Ritorna una lista di risultati con MD5 per il download.
    """
    # 1. Semantic Expansion (Simple heuristic: > 3 words)
    search_query = query
    if len(query.split()) > 3:
        try:
            prompt = f"Convert this natural language request into specific keywords for a book search. Output ONLY keywords. Request: '{query}'"
            response = ollama.generate(model="deepseek-r1:7b", prompt=prompt, stream=False)
            if 'response' in response:
                keywords = response['response'].strip().replace('"', '')
                # Clean reasoning tags if present
                if "</think>" in keywords:
                    keywords = keywords.split("</think>")[-1].strip()
                print(f"[Annas] Semantic expansion: '{query}' -> '{keywords}'", file=sys.stderr)
                search_query = keywords
        except Exception as e:
            print(f"[Annas] Semantic error: {e}", file=sys.stderr)

    # 2. Search
    params = {'q': search_query}
    if lang: params['lang'] = lang
    if ext: params['ext'] = ext
    
    url = f"{ANNAS_URL}/search"
    
    try:
        response = requests.get(url, params=params, headers={'User-Agent': USER_AGENT}, timeout=15)
        response.raise_for_status()
        
        soup = BeautifulSoup(response.content, 'html.parser')
        
        results = []
        seen_md5s = set()
        
        for a_tag in soup.find_all('a', href=True):
            href = a_tag['href']
            if '/md5/' in href:
                md5 = href.split('/md5/')[1]
                title = a_tag.get_text(separator=" ", strip=True)
                
                # Filter out empty titles (like the cover image link)
                if not title or len(title) < 3: continue
                
                # Check if we already have this MD5
                if md5 not in seen_md5s:
                    seen_md5s.add(md5)
                    
                    # Extract more info from the container
                    container = a_tag.parent
                    full_text = container.get_text(separator=" | ", strip=True)
                    
                    results.append(f"{len(results)+1}. {title}\n   MD5: {md5}\n   Info: {full_text[:150]}...")
                
                if len(results) >= limit: break
        
        if not results:
            return f"❌ Nessun risultato per '{search_query}'. Prova con keyword diverse."
            
        return "\n\n".join(results) + "\n\n💡 Usa 'download_from_annas_archive(md5)' per scaricare."

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
        print(f"[Annas] Starting download for MD5: {md5}", file=sys.stderr)
        url = f"{ANNAS_URL}/md5/{md5}"
        output_dir = "/Users/barbagallo/Library/CloudStorage/GoogleDrive-carlo.barbagallo@conservatoriotorino.eu/Il mio Drive/DOCENTE/Libri+Dispense"
        os.makedirs(output_dir, exist_ok=True)
        
        response = requests.get(url, headers={'User-Agent': USER_AGENT}, timeout=15)
        response.raise_for_status()
        
        soup = BeautifulSoup(response.content, 'html.parser')
        
        # Improved title and author extraction
        title = ""
        author = ""
        
        title_tag = soup.find('div', class_='text-3xl font-bold')
        if title_tag:
            title = title_tag.get_text(strip=True)
            
        author_div = soup.find('div', class_='italic')
        if author_div:
            # Try to get the first author link or all text
            author_tag = author_div.find('a')
            if author_tag:
                author = author_tag.get_text(strip=True)
            else:
                author = author_div.get_text(strip=True)
        
        # Fallback if extraction fails
        if not title: title = f"Book_{md5}"
        
        # Construct sanitized filename
        full_name = f"{author} - {title}" if author else title
        safe_name = re.sub(r'[\\/*?:"<>|]', "", full_name).strip()
        # Remove trailing periods which can cause issues on some systems
        safe_name = safe_name.rstrip('.')
        
        # Find links and try to guess extension
        ipfs_links = []
        libgen_links = []
        slow_links = []
        detected_ext = "pdf" # Default fallback
        
        for a in soup.find_all('a', href=True):
            href = a['href']
            if href.startswith('ipfs://'):
                cid = href.replace('ipfs://', '')
                ipfs_links.append(f"https://ipfs.io/ipfs/{cid}")
                ipfs_links.append(f"https://dweb.link/ipfs/{cid}")
            elif 'libgen.li' in href:
                libgen_links.append(href)
            elif '/slow_download/' in href:
                slow_links.append(f"{ANNAS_URL}{href}")
        
        # Try automated download (IPFS preferred)
        all_auto_links = ipfs_links + libgen_links
        
        for link in all_auto_links:
            try:
                print(f"[Annas] Tenta download da: {link}", file=sys.stderr)
                with requests.get(link, stream=True, timeout=20) as r:
                    r.raise_for_status()
                    content_type = r.headers.get('Content-Type', '').lower()
                    
                    if 'application/pdf' in content_type:
                        ext = 'pdf'
                        filepath = os.path.join(output_dir, f"{safe_name}.{ext}")
                        
                        print(f"[Annas] Start writing to {filepath}", file=sys.stderr)
                        with open(filepath, 'wb') as f:
                            for chunk in r.iter_content(chunk_size=8192):
                                f.write(chunk)
                        print(f"[Annas] Download finished: {filepath}", file=sys.stderr)
                        return f"✅ Download completato: {filepath}"
                    else:
                        print(f"[Annas] Skipped non-PDF link ({content_type}) from {link}", file=sys.stderr)
                        continue
                
            except Exception as e:
                print(f"[Annas] Error downloading {link}: {e}", file=sys.stderr)
                continue
                
        # If automated failed, return links
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
    print("[DEBUG] Starting Optimized Knowledge Server...", file=sys.stderr)
    mcp.run()
