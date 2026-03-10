import sys
import fitz  # PyMuPDF
import os
from pathlib import Path

def debug_pdf(path):
    print(f"DEBUGGING: {path}")
    if not os.path.exists(path):
        print("❌ File not found!")
        return

    try:
        doc = fitz.open(path)
        print(f"✅ Opened successfully.")
        print(f"   Pages: {len(doc)}")
        print(f"   Encrypted: {doc.is_encrypted}")
        print(f"   Metadata: {doc.metadata}")
        
        text_count = 0
        empty_pages = 0
        
        for i, page in enumerate(doc):
            text = page.get_text()
            if not text.strip():
                empty_pages += 1
            text_count += len(text)
            
            if i < 3:
                print(f"   --- Page {i+1} Preview ---")
                preview = text[:200].replace('\n', ' ')
                print(f"[{preview}]" if preview else "[EMPTY]")
                print("   ------------------------")
        
        print(f"\n📊 SUMMARY:")
        print(f"   Total Text Length: {text_count} chars")
        print(f"   Empty Pages: {empty_pages}/{len(doc)}")
        
        if text_count < 100:
            print("⚠️  Extracted text is very short or empty. Likely needs OCR.")
        else:
            print("✅ Text extraction seems promising.")
            
    except Exception as e:
        print(f"❌ ERROR: {e}")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python3 debug_pdf.py <path>")
        sys.exit(1)
    debug_pdf(sys.argv[1])
