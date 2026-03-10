#!/usr/bin/env python3
import sys
import os

# Cambia nella directory del progetto
os.chdir('.')
sys.path.insert(0, '.')

from server import _index_directory

# Leggo la lista dei file mancanti
with open("missing_pdfs.txt", "r") as f:
    missing_files = [line.strip() for line in f if line.strip()]

print(f"Inizio indicizzazione di {len(missing_files)} PDF mancanti...")
print(f"Primi 5 file:")
for f in missing_files[:5]:
    print(f"  - {os.path.basename(f)}")
print("...")

# Indicizza
indexed, skipped, errors, ocr_skipped = _index_directory(
    directory_path=None,
    use_ocr=False,
    delete_all=False,
    file_list=missing_files
)

print(f"\n✅ Indicizzazione completata!")
print(f"  - Indicizzati: {indexed}")
print(f"  - Saltati (già presenti): {skipped}")
print(f"  - Errori: {errors}")
print(f"  - Richiedono OCR: {ocr_skipped}")
