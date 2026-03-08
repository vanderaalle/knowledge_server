import sys
import os
from pathlib import Path

# Add the server directory to sys.path
sys.path.append('/Users/barbagallo/Desktop/python/knowledge_server')

try:
    from server import _calculate_file_hash, pdf_processor, COLLECTION_NAME, get_db
    import concurrent.futures
    import sys
    
    print("🚀 Starting SEQUENTIAL OCR Recovery Pass...")
    path_root = Path("/Users/barbagallo/Desktop/python/knowledge_server/alias_books")
    all_pdfs = list(path_root.rglob("*.pdf"))
    
    db = get_db()
    
    indexed = 0
    skipped = 0
    errors = 0
    
    for i, file_path in enumerate(all_pdfs):
        print(f"[{i+1}/{len(all_pdfs)}] Checking {file_path.name}...", end=" ", flush=True)
        try:
            # 1. Check if already in DB
            h = _calculate_file_hash(file_path)
            # Short-circuit check in DB
            res = db.client.scroll(
                collection_name=COLLECTION_NAME,
                scroll_filter={"must": [{"key": "metadata.file_hash", "match": {"value": h}}]},
                limit=1
            )
            if res[0]:
                print("Skipped (Already indexed)")
                skipped += 1
                continue
            
            print("Processing with OCR...", end=" ", flush=True)
            text, page_texts, total_pages, used_ocr = pdf_processor.extract_text_from_pdf(str(file_path), use_ocr=True)
            
            if not text.strip():
                print("Error: No text extracted (likely OCR failure)")
                errors += 1
                continue
            
            print(f"Success ({'OCR' if used_ocr else 'Text'})!", end=" ", flush=True)
            # 2. Chunk and Index
            doc_title = pdf_processor.extract_document_title(str(file_path), text)
            rel_path = os.path.relpath(file_path, path_root)
            meta = {
                "source": rel_path,
                "source_path": str(file_path),
                "file_hash": h,
                "document_title": doc_title
            }
            chunks = pdf_processor.chunk_text(text, page_texts, total_pages, meta)
            
            # 3. Add to DB
            db.upsert_chunks(chunks)
            print(f"✅ Indexed {len(chunks)} chunks")
            indexed += 1
            
        except Exception as e:
            print(f"❌ Error: {e}")
            errors += 1

    print(f"\n✅ Pass completed!")
    print(f"📊 Results: {indexed} indexed, {skipped} skipped, {errors} errors")

except Exception as e:
    import traceback
    traceback.print_exc()
    sys.exit(1)
