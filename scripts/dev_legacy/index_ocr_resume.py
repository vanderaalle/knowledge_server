#!/usr/bin/env python3
"""
Ripresa indicizzazione OCR - file rimanenti
"""
import os
import sys
import time
import hashlib
from pathlib import Path

os.chdir('.')
sys.path.insert(0, '.')

from server import pdf_processor, get_db, OLLAMA_MODEL, _calculate_file_hash
import ollama
from qdrant_client.models import PointStruct
from tqdm import tqdm
import fitz

def needs_ocr(pdf_path):
    try:
        doc = fitz.open(pdf_path)
        if len(doc) == 0:
            doc.close()
            return False
        for i, page in enumerate(doc[:3]):
            text = page.get_text().strip()
            if text and len(text) > 50:
                doc.close()
                return False
        doc.close()
        return True
    except:
        return False

def index_with_ocr_single(file_path, db):
    try:
        file_hash = _calculate_file_hash(Path(file_path))
        if not file_hash:
            return None, "error_hash"
        
        if db.is_file_indexed(file_hash):
            return None, "already_indexed"
        
        print(f"  🔍 OCR: {os.path.basename(file_path)[:50]}...")
        text, page_texts, total_pages, used_ocr = pdf_processor.extract_text_from_pdf(
            str(file_path), use_ocr=True
        )
        
        if not text.strip():
            return None, "no_text"
        
        document_title = pdf_processor.extract_document_title(str(file_path), text)
        relative_path = os.path.relpath(file_path, "./alias_books")
        
        base_metadata = {
            "source": relative_path,
            "source_path": str(file_path),
            "file_hash": file_hash,
            "document_title": document_title
        }
        
        chunks = pdf_processor.chunk_text(text, page_texts, total_pages, base_metadata)
        
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
    log_file = "./index_ocr_resume.log"
    
    def log(msg):
        timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
        line = f"[{timestamp}] {msg}"
        print(line)
        with open(log_file, "a") as f:
            f.write(line + "\n")
    
    log("="*60)
    log("🚀 RIPRESA OCR - File rimanenti")
    log("="*60)
    
    # Leggi lista rimanenti
    with open("missing_pdfs_remaining.txt", "r") as f:
        remaining_files = [line.strip() for line in f if line.strip()]
    
    log(f"File da processare: {len(remaining_files)}")
    
    db = get_db()
    stats = {"indexed": 0, "skipped": 0, "errors": 0, "total_chunks": 0}
    
    for i, file_path in enumerate(remaining_files, 1):
        log(f"[{i}/{len(remaining_files)}] {os.path.basename(file_path)[:60]}")
        
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
            log(f"  ❌ {status}")
        
        time.sleep(0.5)
    
    log("="*60)
    log("✅ COMPLETATO!")
    log(f"  📄 Indicizzati: {stats['indexed']}")
    log(f"  ⏭️  Saltati: {stats['skipped']}")
    log(f"  ❌ Errori: {stats['errors']}")
    log(f"  🧩 Chunks: {stats['total_chunks']}")
    log("="*60)

if __name__ == "__main__":
    main()
