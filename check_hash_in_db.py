from pathlib import Path
import sys
import os
import hashlib

# Add current dir to sys.path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from server import get_db, _calculate_file_hash

def check_hash(path):
    p = Path(path)
    if not p.exists():
        print(f"File not found: {path}")
        return

    print(f"Calculating hash for: {p.name}")
    try:
        file_hash = _calculate_file_hash(p)
        print(f"Hash: {file_hash}")
        
        db = get_db()
        is_indexed = db.is_file_indexed(file_hash)
        
        if is_indexed:
            print("✅ Hash FOUND in DB! The file content is already indexed.")
            # Let's see the paths associated with this hash
            chunks = db.get_chunks_by_file(file_hash)
            if chunks:
                unique_paths = set(c['metadata'].get('source_path') for c in chunks)
                print(f"Indexed under paths: {unique_paths}")
        else:
            print("❌ Hash NOT found in DB. This file is genuinely missing.")
            
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python3 check_hash_in_db.py <path>")
        sys.exit(1)
    check_hash(sys.argv[1])
