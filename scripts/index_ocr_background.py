#!/usr/bin/env python3
"""
Script per indicizzare PDF con OCR in background
Processa gradualmente i PDF scansionati
"""
import os
import sys
import time
import hashlib
from pathlib import Path

os.chdir('/Users/barbagallo/Desktop/python/knowledge_server')
sys.path.insert(0, '/Users/barbagallo/Desktop/python/knowledge_server')

from server import pdf_processor, get_db, OLLAMA_MODEL, _calculate_file_hash
import ollama
from qdrant_client.models import PointStruct
from tqdm import tqdm
import fitz

def needs_ocr(pdf_path):
    """Verifica se un PDF necessita OCR (ha pagine ma nessun testo)"""
    try:
        doc = fitz.open(pdf_path)
        if len(doc) == 0:
            doc.close()
            return False, "empty_pdf"
        
        # Controlla le prime 3 pagine
        text_found = False
        for i, page in enumerate(doc[:3]):
            text = page.get_text().strip()
            if text and len(text) > 50:  # Almeno 50 caratteri di testo
                text_found = True
                break
        
        doc.close()
        
        if text_found:
            return False, "has_text"
        return True, "needs_ocr"
        
    except Exception as e:
        return False, f"error: {e}"

def index_with_ocr_single(file_path, db):
    """Indicizza un singolo PDF usando OCR"""
    try:
        # Calcola hash
        file_hash = _calculate_file_hash(Path(file_path))
        if not file_hash:
            return None, "error_hash"
        
        # Verifica duplicato
        if db.is_file_indexed(file_hash):
            return None, "already_indexed"
        
        # Estrai testo con OCR
        print(f"  🔍 OCR per: {os.path.basename(file_path)[:50]}...")
        text, page_texts, total_pages, used_ocr = pdf_processor.extract_text_from_pdf(
            str(file_path), use_ocr=True
        )
        
        if not text.strip():
            return None, "no_text_after_ocr"
        
        # Metadata
        document_title = pdf_processor.extract_document_title(str(file_path), text)
        relative_path = os.path.relpath(file_path, "/Users/barbagallo/Desktop/python/knowledge_server/alias_books")
        
        base_metadata = {
            "source": relative_path,
            "source_path": str(file_path),
            "file_hash": file_hash,
            "document_title": document_title
        }
        
        # Chunking
        chunks = pdf_processor.chunk_text(text, page_texts, total_pages, base_metadata)
        
        # Embedding e upsert
        points = []
        for chunk in chunks:
            try:
                text_to_embed = chunk.text[:700].strip()
                if not text_to_embed:
                    continue
                
                response = ollama.embeddings(model=OLLAMA_MODEL, prompt=text_to_embed)
                embedding = response["embedding"]
                
                point_id = hashlib.md5((str(file_path) + str(chunk.chunk_index)).encode()).hexdigest()
                
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
                print(f"    ⚠️ Embedding error: {e}")
        
        if points:
            db.upsert_points(points)
            return len(points), "ok"
        return 0, "no_chunks"
        
    except Exception as e:
        return None, f"error: {e}"

def main():
    log_file = "/Users/barbagallo/Desktop/python/knowledge_server/index_ocr.log"
    
    def log(msg):
        timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
        line = f"[{timestamp}] {msg}"
        print(line)
        with open(log_file, "a") as f:
            f.write(line + "\n")
    
    log("="*60)
    log("🚀 Avvio indicizzazione OCR in background")
    log("="*60)
    
    # Leggi lista file mancanti
    with open("missing_pdfs.txt", "r") as f:
        all_missing = [line.strip() for line in f if line.strip()]
    
    log(f"Totale file da analizzare: {len(all_missing)}")
    
    # Filtra solo quelli che necessitano OCR
    ocr_candidates = []
    for fp in all_missing:
        needs, reason = needs_ocr(fp)
        if needs:
            ocr_candidates.append(fp)
    
    log(f"PDF che necessitano OCR: {len(ocr_candidates)}")
    
    if not ocr_candidates:
        log("Nessun PDF richiede OCR!")
        return
    
    # Inizializza DB
    db = get_db()
    
    # Statistiche
    stats = {"indexed": 0, "skipped": 0, "errors": 0, "total_chunks": 0}
    
    # Processa i file
    for i, file_path in enumerate(ocr_candidates, 1):
        log(f"[{i}/{len(ocr_candidates)}] Processing: {os.path.basename(file_path)[:60]}")
        
        result, status = index_with_ocr_single(file_path, db)
        
        if status == "ok":
            stats["indexed"] += 1
            stats["total_chunks"] += result
            log(f"  ✅ Indicizzato ({result} chunks)")
        elif status == "already_indexed":
            stats["skipped"] += 1
            log(f"  ⏭️  Già presente")
        else:
            stats["errors"] += 1
            log(f"  ❌ Errore: {status}")
        
        # Pausa tra i file per non sovraccaricare
        time.sleep(0.5)
    
    log("="*60)
    log("✅ COMPLETATO!")
    log(f"  📄 File indicizzati: {stats['indexed']}")
    log(f"  ⏭️  Saltati: {stats['skipped']}")
    log(f"  ❌ Errori: {stats['errors']}")
    log(f"  🧩 Total chunks: {stats['total_chunks']}")
    log("="*60)

if __name__ == "__main__":
    main()
