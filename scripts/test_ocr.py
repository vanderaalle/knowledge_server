import sys
from pathlib import Path
from pdf2image import convert_from_path
import pytesseract
import os

# Configure paths
pytesseract.pytesseract.tesseract_cmd = '/opt/homebrew/bin/tesseract'
POPPLER_PATH = '/opt/homebrew/bin'

pdf_path = '/Users/barbagallo/Library/CloudStorage/GoogleDrive-carlo.barbagallo@conservatoriotorino.eu/Il mio Drive/DOCENTE/Libri+Dispense/PDF Backup Downloads - 241020/nono-1964-la-fabbrica-illuminata_compress.pdf'

print(f"Testing OCR on: {pdf_path}")
print(f"Tesseract Path: {pytesseract.pytesseract.tesseract_cmd}")
print(f"Poppler Path: {POPPLER_PATH}")

if not os.path.exists(pdf_path):
    print(f"FATAL: File does not exist at {pdf_path}")
    sys.exit(1)

try:
    print("Converting first page to image...")
    images = convert_from_path(pdf_path, first_page=1, last_page=1, poppler_path=POPPLER_PATH)
    if images:
        print("Success: Image generated.")
        print("Running OCR on image...")
        text = pytesseract.image_to_string(images[0])
        print(f"Extracted Text (first 200 chars):\n{text[:200]}")
        if not text.strip():
            print("Warning: Extracted text is empty.")
    else:
        print("Error: No images returned by convert_from_path.")

except Exception as e:
    import traceback
    traceback.print_exc()
