#!/usr/bin/env python3
"""
Semantic Book Organizer for Knowledge Server (Optimized)
"""

import os
import sys
import shutil
import json
import argparse
import time
from pathlib import Path
import ollama

# Add current directory to sys.path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from server import PDFProcessor, get_db, _calculate_file_hash, OLLAMA_MODEL

# Define Semantic Categories
CATEGORIES = {
    "Music & Sound Art": [
        "Composition & Theory",
        "Electronic & Computer Music",
        "Sound Design & Engineering",
        "Improvisation & Performance"
    ],
    "Critical Theory & Society": [
        "Politics & Sociology",
        "Media & Culture",
        "Philosophy & Aesthetics",
        "Collectivism & Collaboration"
    ],
    "Science & Tech": [
        "Programming & CS",
        "Math & Science"
    ],
    "Literature": [
        "Novels",
        "Poetry",
        "Essays"
    ],
    "Uncategorized": [] # Fallback
}

def get_classification_prompt(filename, title, text_snippet):
    categories_str = json.dumps(CATEGORIES, indent=2)
    return f"""
    You are a librarian assistant. Classify the following book into one of the provided categories.
    
    Book Filename: {filename}
    Book Title: {title}
    Text Snippet: {text_snippet[:500]}...

    Available Categories:
    {categories_str}

    Instructions:
    1. Analyze the title and snippet.
    2. Select the BEST Main Category and Sub Category.
    3. Return ONLY a JSON object in this format: {{"main_category": "Name", "sub_category": "Name"}}
    4. If no specific sub-category fits, use the Main Category name as sub-category or "General".
    5. If unsure, use "Uncategorized".
    """

def classify_book(filename, title, text_snippet, model_name="qwen2.5-coder:1.5b"):
    """Query Ollama to classify the book."""
    
    try:
        response = ollama.generate(
            model=model_name,
            prompt=get_classification_prompt(filename, title, text_snippet),
            format="json",
            stream=False
        )
        content = response['response']
        
        # Determine strict JSON start/end just in case
        start = content.find('{')
        end = content.rfind('}')
        if start != -1 and end != -1:
            content = content[start:end+1]
            
        return json.loads(content)
    except Exception as e:
        # For debugging purposes, print the full error and response
        if 'response' in locals():
             print(f"Error parse JSON for {filename}: {e}\nRaw Response: {response.get('response', '')}", file=sys.stderr)
        else:
             print(f"Error calling Ollama for {filename}: {e}", file=sys.stderr)
        return {"main_category": "Uncategorized", "sub_category": "General"}

def scan_and_organize(root_dir, dry_run=True, limit=0, model_name="qwen2.5-coder:1.5b"):
    root_path = Path(root_dir).resolve()
    if not root_path.exists():
        print(f"Directory not found: {root_path}")
        return

    print(f"Scanning {root_path} with model {model_name}...")
    
    # Initialize tools
    pdf_processor = PDFProcessor()
    db = get_db() # Helper to get DB client
    
    moved_files_map = {} # old_path -> new_path
    
    # Files to process
    files_to_move = []
    
    for file_path in root_path.rglob("*.pdf"):
        files_to_move.append(file_path)

    print(f"Found {len(files_to_move)} PDF files.")
    
    report_lines = []
    
    file_map_path = Path("moved_files_map.json")
    if file_map_path.exists():
         with open(file_map_path, "r") as f:
             moved_files_map = json.load(f)

    for i, file_path in enumerate(files_to_move):
        if limit > 0 and i >= limit:
            print(f"Limit of {limit} files reached.")
            break
            
        filename = file_path.name
        print(f"[{i+1}/{len(files_to_move)}] Processing: {filename}")
        
        # 1. Calculate Hash
        file_hash = _calculate_file_hash(file_path)
        
        # 2. Check DB for metadata
        title = ""
        text_snippet = ""
        
        chunks = db.get_head_chunks_by_file(file_hash, limit=5)
        if chunks:
            # Found in DB! Use existing metadata
            meta = chunks[0]['metadata']
            title = meta.get('document_title', filename)
            # Combine first few chunks for context
            text_snippet = "\n".join([c['text'] for c in chunks[:3]])
            print(f"  -> Found in Index (Title: {title})")
        else:
            # Fallback: Extract from PDF
            print(f"  -> Not in Index. Extracting...")
            try:
                full_text, _, _, _ = pdf_processor.extract_text_from_pdf(str(file_path))
                text_snippet = full_text[:1000]
                title = pdf_processor.extract_document_title(str(file_path), text_snippet)
            except Exception as e:
                print(f"  -> Error extracting text: {e}")
                continue

        # 3. Classify
        classification = classify_book(filename, title, text_snippet, model_name=model_name)
        main_cat = classification.get("main_category", "Uncategorized")
        sub_cat = classification.get("sub_category", "General")
        
        # Normalize categories
        if main_cat not in CATEGORIES:
            main_cat = "Uncategorized"
        
        # 4. Determine New Path
        new_dir = root_path / main_cat / sub_cat
        new_path = new_dir / filename
        
        # Handle duplicates/collisions
        if new_path == file_path:
             print(f"  -> Already in correct place.")
             continue
             
        if new_path.exists() and not dry_run:
             # Simple rename to avoid overwrite
             stem = new_path.stem
             suffix = new_path.suffix
             new_path = new_dir / f"{stem}_copy{suffix}"

        action = "WOULD MOVE" if dry_run else "MOVING"
        print(f"  -> {action} to: {main_cat}/{sub_cat}/{filename}")
        
        report_lines.append(f"{file_path} -> {new_path}")

        if not dry_run:
            try:
                os.makedirs(new_dir, exist_ok=True)
                shutil.move(file_path, new_path)
                moved_files_map[str(file_path)] = str(new_path)
            except Exception as move_err:
                print(f"  -> Error moving: {move_err}")
    
    # Save Report
    with open("organization_report.txt", "w") as f:
        f.write("\n".join(report_lines))
    
    # Save Map
    if not dry_run:
        with open("moved_files_map.json", "w") as f:
            json.dump(moved_files_map, f, indent=2)
        print("Organization complete. Mapping saved to 'moved_files_map.json'.")
        print("Run 'update_index_paths.py' to update the Knowledge Base index.")
    else:
        print("Dry Run complete. Check 'organization_report.txt' for details.")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Organize books semantically.")
    parser.add_argument("path", help="Path to 'alias_books' folder")
    parser.add_argument("--apply", action="store_true", help="Apply changes (disable Dry Run)")
    parser.add_argument("--limit", type=int, default=0, help="Limit number of files to process (for testing)")
    parser.add_argument("--model", default="qwen2.5:1.5b", help="Ollama model to use")
    
    args = parser.parse_args()
    
    scan_and_organize(args.path, dry_run=not args.apply, limit=args.limit, model_name=args.model)
