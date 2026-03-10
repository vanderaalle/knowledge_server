import sys
import os
from pdf2image import convert_from_path
import pytesseract

def test_ocr(pdf_path):
    print(f"Testing OCR on: {pdf_path}")
    if not os.path.exists(pdf_path):
        print("File not found")
        return

    try:
        print("Converting pages to images...")
        images = convert_from_path(pdf_path)
        if not images:
            print("❌ No images generated from PDF.")
            return
            
        print(f"✅ Images generated: {len(images)}")
        
        for i, img in enumerate(images):
            print(f"Running Tesseract on page {i+1}...")
            text = pytesseract.image_to_string(img)
            
            print(f"\n--- Page {i+1} OCR Result (first 500 chars) ---")
            print(text[:500])
            print("------------------------------------")
            
            if not text.strip():
                print(f"⚠️  Page {i+1} OCR returned empty string.")
            else:
                print(f"✅ Page {i+1} OCR Success! Length: {len(text)}")
            
    except Exception as e:
        print(f"❌ OCR FAILED: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python3 debug_ocr_run.py <pdf_path>")
        sys.exit(1)
    test_ocr(sys.argv[1])
