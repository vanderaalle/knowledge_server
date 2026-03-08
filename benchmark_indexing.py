import time
import sys
import shutil
import os
from pathlib import Path

# Add the knowledge_server directory to the path mainly for finding relative modules if needed
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from server import _index_directory

def main():
    if len(sys.argv) < 2:
        print("Usage: python3 benchmark_indexing.py <path_to_pdf_directory>")
        sys.exit(1)

    source_dir = Path(sys.argv[1])
    if not source_dir.exists():
        print(f"Directory {source_dir} not found.")
        sys.exit(1)

    print(f"Benchmarking indexing on: {source_dir}")
    print("This will index the directory using the current implementation.")
    
    start_time = time.time()
    
    # We use delete_all=True to ensure a clean slate and measure full indexing time
    indexed, skipped, errors, ocr_skipped = _index_directory(str(source_dir), use_ocr=False, delete_all=True)
    
    end_time = time.time()
    duration = end_time - start_time
    
    print("\n" + "="*30)
    print(f"BENCHMARK RESULTS")
    print("="*30)
    print(f"Time taken: {duration:.2f} seconds")
    print(f"Indexed: {indexed}")
    print(f"Skipped: {skipped}")
    print(f"Errors: {errors}")
    print(f"OCR Skipped: {ocr_skipped}")
    print("="*30)

if __name__ == "__main__":
    main()
