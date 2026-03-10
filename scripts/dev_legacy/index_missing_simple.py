#!/usr/bin/env python3
"""
Script per indicizzare i PDF mancanti - Versione semplificata senza multiprocessing
"""
import os
import sys
import hashlib
from pathlib import Path

os.chdir('.')
sys.path.insert(0, '.')

from server import pdf_processor, get_db, OLLAMA_MODEL, _calculate_file_hash
import ollama
from qdrant_client.models import PointStruct
import hashlib as hl
from tqdm import tqdm

def index_single_file(file_path):
    """Indicizza un singolo PDF"""
    try:
        # Calcola hash
        file_hash = _calculate_file_hash(Path(file_path))
        if not file_hash:
            return None, "error_hash"
        
        # Estrai testo
        text, page_texts, total_pages, used_ocr = pdf_processor.extract_text_from_pdf(
            str(file_path), use_ocr=False
        )
        
        if not text.strip():
            return None, "error_no_text"
        
        # Metadata
        document_title = pdf_processor.extract_document_title(str(file_path), text)
        relative_path = os.path.relpath(file_path, "./alias_books")
        
        base_metadata = {
            "source": relative_path,
            "source_path": str(file_path),
            "file_hash": file_hash,
            "document_title": document_title
        }
        
        # Chunking
        chunks = pdf_processor.chunk_text(text, page_texts, total_pages, base_metadata)
        
        return {
            "file_path": str(file_path),
            "file_hash": file_hash,
            "chunks": chunks,
            "title": document_title
        }, "ok"
        
    except Exception as e:
        return None, f"error: {e}"


def main():
    # Leggi lista file mancanti
    with open("missing_pdfs.txt", "r") as f:
        missing_files = [line.strip() for line in f if line.strip()]
    
    print(f"🚀 Indicizzazione di {len(missing_files)} PDF mancanti...")
    print(f"{'='*60}")
    
    db = get_db()
    
    stats = {"indexed": 0, "skipped": 0, "errors": 0, "total_chunks": 0}
    
    for i, file_path in enumerate(tqdm(missing_files, desc="Processing PDFs"), 1):
        # Verifica se già indicizzato
        result, status = index_single_file(file_path)
        
        if status != "ok":
            stats["errors"] += 1
            continue
        
        # Verifica duplicato
        if db.is_file_indexed(result["file_hash"]):
            stats["skipped"] += 1
            continue
        
        # Embedding e upsert
        points = []
        for chunk in result["chunks"]:
            try:
                text_to_embed = chunk.text[:700].strip()
                if not text_to_embed:
                    continue
                
                response = ollama.embeddings(model=OLLAMA_MODEL, prompt=text_to_embed)
                embedding = response["embedding"]
                
                point_id = hl.md5((result["file_path"] + str(chunk.chunk_index)).encode()).hexdigest()
                
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
                print(f"  ⚠️ Embedding error: {e}")
        
        if points:
            db.upsert_points(points)
            stats["indexed"] += 1
            stats["total_chunks"] += len(points)
        
        # Stampa progresso ogni 10 file
        if i % 10 == 0:
            print(f"  📊 Progresso: {i}/{len(missing_files)} - Indicizzati: {stats['indexed']}, Errori: {stats['errors']}")
    
    print(f"\n{'='*60}")
    print(f"✅ COMPLETATO!")
    print(f"  📄 File indicizzati: {stats['indexed']}")
    print(f"  ⏭️  Saltati (già presenti): {stats['skipped']}")
    print(f"  ❌ Errori: {stats['errors']}")
    print(f"  🧩 Total chunks aggiunti: {stats['total_chunks']}")


if __name__ == "__main__":
    main()
