#!/usr/bin/env python3
"""
Script per l'indicizzazione OCR dei soli file mancanti in Qdrant.
Punti di forza:
- Identificazione automatica file mancanti (per hash)
- OCR abilitato di default per i nuovi file
- Nessuna duplicazione (upsert con controllo preventivo)
- Logging dettagliato
"""
import os
import sys
import time
import hashlib
from pathlib import Path
from qdrant_client.models import PointStruct
from tqdm import tqdm

# Impostazione percorsi
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
os.chdir(BASE_DIR)
sys.path.insert(0, BASE_DIR)


from server import pdf_processor, get_db, OLLAMA_MODEL, _calculate_file_hash, COLLECTION_NAME
import ollama

LOG_FILE = "index_ocr_remaining.log"

def log(msg):
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{timestamp}] {msg}"
    print(line)
    with open(LOG_FILE, "a") as f:
        f.write(line + "\n")

def get_missing_files(db, scan_path):
    log("Recupero lista hash indicizzati da Qdrant...")
    indexed_hashes = set()
    offset = None
    while True:
        result = db.client.scroll(
            collection_name=COLLECTION_NAME,
            limit=1000, offset=offset,
            with_payload=True, with_vectors=False
        )
        points, next_offset = result
        for point in points:
            f_hash = point.payload.get('metadata', {}).get('file_hash')
            if f_hash:
                indexed_hashes.add(f_hash)
        if not next_offset: break
        offset = next_offset
    
    log(f"File unici nel DB: {len(indexed_hashes)}")
    
    log(f"Scansione file locali in: {scan_path}...")
    missing = []
    all_pdfs = []
    for root, _, files in os.walk(scan_path):
        for file in files:
            if file.lower().endswith('.pdf'):
                all_pdfs.append(os.path.join(root, file))
    
    total_local = len(all_pdfs)
    log(f"Trovati {total_local} PDF locali. Calcolo hash...")
    
    for i, full_path in enumerate(all_pdfs, 1):
        if i % 100 == 0:
            log(f"  Progresso hashing: {i}/{total_local}...")
        f_hash = _calculate_file_hash(Path(full_path))
        if f_hash and f_hash not in indexed_hashes:
            missing.append((full_path, f_hash))
    
    log(f"File da indicizzare: {len(missing)}")
    return missing


def process_file(file_path, file_hash, db):
    try:
        # Estrazione OCR
        text, page_texts, total_pages, used_ocr = pdf_processor.extract_text_from_pdf(
            str(file_path), use_ocr=True
        )
        
        if not text.strip():
            return 0, "no_text"
        
        document_title = pdf_processor.extract_document_title(str(file_path), text)
        relative_path = os.path.relpath(file_path, os.path.join(BASE_DIR, "alias_books"))
        
        base_metadata = {
            "source": relative_path,
            "source_path": str(file_path),
            "file_hash": file_hash,
            "document_title": document_title
        }
        
        chunks = pdf_processor.chunk_text(text, page_texts, total_pages, base_metadata)
        
        points = []
        for chunk in chunks:
            text_to_embed = chunk.text[:700].strip()
            if not text_to_embed: continue
            
            try:
                response = ollama.embeddings(model=OLLAMA_MODEL, prompt=text_to_embed)
                embedding = response["embedding"]
                point_id = hashlib.md5((str(file_path) + str(chunk.chunk_index)).encode()).hexdigest()
                
                point = PointStruct(
                    id=point_id,
                    vector=embedding,
                    payload={"text": text_to_embed, "metadata": chunk.to_metadata()}
                )
                points.append(point)
            except Exception as e:
                log(f"  ⚠️ Error embedding chunk {chunk.chunk_index}: {e}")
        
        if points:
            db.upsert_points(points)
            return len(points), "ok"
        return 0, "no_chunks"
        
    except Exception as e:
        return 0, f"error: {e}"

def main():
    log("="*60)
    log("🚀 INIZIO INDICIZZAZIONE OCR RIMANENTI")
    log("="*60)
    
    db = get_db()
    missing_files = get_missing_files(db, os.path.join(BASE_DIR, "alias_books"))
    
    if not missing_files:
        log("✅ Tutto indicizzato! Nessun file mancante.")
        return

    stats = {"indexed": 0, "errors": 0, "total_chunks": 0}
    
    for i, (path, f_hash) in enumerate(missing_files, 1):
        filename = os.path.basename(path)
        log(f"[{i}/{len(missing_files)}] Elaborazione: {filename}")
        
        n_chunks, status = process_file(path, f_hash, db)
        
        if status == "ok":
            stats["indexed"] += 1
            stats["total_chunks"] += n_chunks
            log(f"  ✅ Completato ({n_chunks} chunks)")
        else:
            stats["errors"] += 1
            log(f"  ❌ {status}")
            
    log("="*60)
    log("✅ OPERAZIONI COMPLETATE")
    log(f"  📄 File indicizzati: {stats['indexed']}")
    log(f"  ❌ Errori: {stats['errors']}")
    log(f"  🧩 Chunks totali: {stats['total_chunks']}")
    log("="*60)

if __name__ == "__main__":
    main()
