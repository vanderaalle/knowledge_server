import sys
from pathlib import Path
from pdf2image import convert_from_path
import pytesseract
import os
import subprocess

# Configure paths
TESSERACT_BIN = '/opt/homebrew/bin/tesseract'
POPPLER_PATH = '/opt/homebrew/bin'
pytesseract.pytesseract.tesseract_cmd = TESSERACT_BIN

pdf_path = '/Users/barbagallo/Library/CloudStorage/GoogleDrive-carlo.barbagallo@conservatoriotorino.eu/Il mio Drive/DOCENTE/Libri+Dispense/PDF Backup Downloads - 241020/nono-1964-la-fabbrica-illuminata_compress.pdf'

print(f"Testing OCR on: {pdf_path}")

try:
    print("Converting first page to image...")
    images = convert_from_path(pdf_path, first_page=1, last_page=1, poppler_path=POPPLER_PATH)
    if images:
        img = images[0]
        temp_img = '/tmp/test_ocr_page1.png'
        print(f"Saving temp image to {temp_img}...")
        img.save(temp_img)
        
        print(f"Running tesseract command manually on {temp_img}...")
        result = subprocess.run([TESSERACT_BIN, temp_img, 'stdout'], capture_output=True)
        
        if result.returncode == 0:
            print("Success! Manual Tesseract output (first 500 chars):")
            stdout_text = result.stdout.decode('utf-8', errors='replace')
            print(stdout_text[:500])
        else:
            print(f"Manual Tesseract failed with code {result.returncode}")
            stderr_text = result.stderr.decode('utf-8', errors='replace')
            print(f"Error: {stderr_text}")
            
        print("\nTrying pytesseract with the same PNG file...")
        text = pytesseract.image_to_string(temp_img)
        print(f"Pytesseract Output (first 100 chars):\n{text[:100]}")

    else:
        print("Error: No images returned by convert_from_path.")

except Exception as e:
    import traceback
    traceback.print_exc()
