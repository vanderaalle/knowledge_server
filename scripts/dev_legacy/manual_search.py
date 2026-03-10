#!/usr/bin/env python3
"""
Script manuale per interrogare Qdrant con supporto ai nuovi metadati e tool.
"""

import sys
import os
import hashlib
import platform
import shutil
import subprocess
from pathlib import Path
import ollama
from qdrant_client import QdrantClient
from qdrant_client.http import models
from prompt_toolkit import prompt
from prompt_toolkit.shortcuts import radiolist_dialog
from prompt_toolkit.styles import Style

# Configurazione
OLLAMA_MODEL = os.getenv('OLLAMA_MODEL', 'mxbai-embed-large')
QDRANT_HOST = os.getenv('QDRANT_HOST', 'localhost')
QDRANT_PORT = int(os.getenv('QDRANT_PORT', '6333'))
COLLECTION_NAME = os.getenv('COLLECTION_NAME', 'pdf_library')

def open_pdf(file_path, page_number=1):
    """Open a PDF at a specific page using the system viewer."""
    if not os.path.exists(file_path):
        print(f"❌ File not found: {file_path}")
        return
    system = platform.system()
    if system == "Darwin":
        script = (
            f'tell application "Preview" to open POSIX file "{file_path}"\n'
            f'delay 1\n'
            f'tell application "Preview" to tell front document '
            f'to set current page to page {page_number}'
        )
        subprocess.Popen(["osascript", "-e", script])
    elif system == "Windows":
        sumatra = shutil.which("SumatraPDF")
        if sumatra:
            subprocess.Popen([sumatra, "-page", str(page_number), file_path])
        else:
            os.startfile(file_path)
    else:
        if shutil.which("evince"):
            subprocess.Popen(["evince", f"--page-index={page_number - 1}", file_path])
        elif shutil.which("okular"):
            subprocess.Popen(["okular", "--page", str(page_number), file_path])
        elif shutil.which("zathura"):
            subprocess.Popen(["zathura", "--page", str(page_number - 1), file_path])
        else:
            subprocess.Popen(["xdg-open", file_path])
    print(f"✅ Opened {os.path.basename(file_path)} at page {page_number}")


def get_client():
    """Get Qdrant client"""
    return QdrantClient(host=QDRANT_HOST, port=QDRANT_PORT)

def calculate_file_hash(file_path):
    """Calculate MD5 hash of a file"""
    hash_md5 = hashlib.md5()
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(4096), b""):
            hash_md5.update(chunk)
    return hash_md5.hexdigest()

def search(query, limit=5):
    """Semantic search with rich metadata display"""
    print(f"🔍 Searching for: '{query}'")
    print(f"   Model: {OLLAMA_MODEL}")
    print(f"   Collection: {COLLECTION_NAME}\n")

    try:
        print("⚙️  Generating embedding...")
        response = ollama.embeddings(model=OLLAMA_MODEL, prompt=query)
        query_vector = response["embedding"]

        print(f"🗄️  Querying Qdrant...")
        client = get_client()

        search_result = client.search(
            collection_name=COLLECTION_NAME,
            query_vector=query_vector,
            limit=limit
        )

        print(f"\n{'='*70}")
        print(f"📊 Found {len(search_result)} results")
        print(f"{'='*70}\n")

        hits = []
        for i, result in enumerate(search_result):
            metadata = result.payload.get('metadata', {})
            text = result.payload.get('text', '')
            hits.append((metadata, text, result.score))

            print(f"{'─'*70}")
            print(f"📄 Result {i+1} | Score: {result.score:.4f}")
            print(f"{'─'*70}")
            print(f"📚 Document:   {metadata.get('document_title', 'N/A')}")
            print(f"📄 File:       {metadata.get('source', 'N/A')}")
            print(f"📍 Page:       {metadata.get('page_number', 'N/A')} / {metadata.get('total_pages', 'N/A')}")
            print(f"🧩 Chunk:      {metadata.get('chunk_index', 'N/A')} / {metadata.get('total_chunks', 'N/A')}")
            print(f"🔗 File hash:  {metadata.get('file_hash', 'N/A')}")

            if metadata.get('heading_context'):
                print(f"📌 Context:    {metadata.get('heading_context')[:80]}...")

            print(f"\n📝 Text:")
            print(f"{text[:800]}{'...' if len(text) > 800 else ''}")
            print(f"{'─'*70}\n")

        # Interactive picker
        if sys.stdin.isatty():
            choices = [
                (
                    str(i),
                    f"[{score:.3f}] {meta.get('document_title', 'N/A')}  —  p. {meta.get('page_number', '?')}"
                )
                for i, (meta, _, score) in enumerate(hits)
            ]
            choices.append(("skip", "— don't open anything —"))

            style = Style.from_dict({"dialog.body": "bg:#1e1e2e", "button": "bg:#89b4fa"})
            selected = radiolist_dialog(
                title="Open PDF",
                text="Select a result to open:",
                values=choices,
                style=style,
            ).run()

            if selected is not None and selected != "skip":
                meta, _, _ = hits[int(selected)]
                source_path = meta.get('source_path') or meta.get('source', '')
                page = meta.get('page_number', 1)
                open_pdf(source_path, page)
        else:
            print("Open which result? (number, or Enter to skip): ", end="", flush=True)
            choice = input().strip()
            if choice.isdigit() and 0 < int(choice) <= len(hits):
                meta, _, _ = hits[int(choice) - 1]
                source_path = meta.get('source_path') or meta.get('source', '')
                page = meta.get('page_number', 1)
                open_pdf(source_path, page)

    except Exception as e:
        print(f"❌ Error during search: {e}")
        import traceback
        traceback.print_exc()

def get_document_info(file_hash):
    """Get information about a specific document"""
    print(f"📋 Document info: {file_hash[:16]}...")
    
    try:
        client = get_client()
        
        # Recupera tutti i chunk del documento
        all_chunks = []
        offset = None
        
        while True:
            result = client.scroll(
                collection_name=COLLECTION_NAME,
                scroll_filter=models.Filter(
                    must=[
                        models.FieldCondition(
                            key="metadata.file_hash",
                            match=models.MatchValue(value=file_hash)
                        )
                    ]
                ),
                limit=100,
                offset=offset,
                with_payload=True,
                with_vectors=False
            )
            
            chunks, next_offset = result
            all_chunks.extend(chunks)
            
            if not next_offset:
                break
            offset = next_offset
        
        if not all_chunks:
            print("❌ No document found with this hash")
            return
        
        # Estrai metadati dal primo chunk
        first_chunk = all_chunks[0]
        metadata = first_chunk.payload.get('metadata', {})
        
        print(f"\n{'='*70}")
        print(f"📚 {metadata.get('document_title', 'Untitled document')}")
        print(f"{'='*70}")
        print(f"📄 File:       {metadata.get('source', 'N/A')}")
        print(f"📍 Pages:      {metadata.get('total_pages', 'N/A')}")
        print(f"🧩 Chunks:     {len(all_chunks)} / {metadata.get('total_chunks', 'N/A')}")
        print(f"📏 Size:       {metadata.get('file_size', 0) / 1024:.1f} KB")
        print(f"🔗 Hash:       {file_hash}")

        print(f"\n📑 Structure (headings found):")
        print(f"{'─'*70}")

        headings_found = []
        for chunk in sorted(all_chunks, key=lambda x: x.payload.get('metadata', {}).get('chunk_index', 0)):
            level = chunk.payload.get('metadata', {}).get('level', 'paragraph')
            if level in ['title', 'h1', 'h2', 'h3']:
                page = chunk.payload.get('metadata', {}).get('page_number', '?')
                text = chunk.payload.get('text', '')[:60]
                headings_found.append((level, page, text))

        if headings_found:
            for level, page, text in headings_found[:20]:
                indent = "  " * (0 if level == 'title' else 1 if level == 'h1' else 2)
                print(f"{indent}[{level.upper():6}] p. {page:3}: {text}...")
            if len(headings_found) > 20:
                print(f"\n  ... and {len(headings_found) - 20} more headings")
        else:
            print("  No headings detected")

        print(f"{'='*70}\n")

    except Exception as e:
        print(f"❌ Error: {e}")
        import traceback
        traceback.print_exc()

def read_page(file_hash, page_number):
    """Read a specific page of a document"""
    print(f"📖 Reading page {page_number}...")
    
    try:
        client = get_client()
        
        # Cerca chunk nella pagina specifica
        result = client.scroll(
            collection_name=COLLECTION_NAME,
            scroll_filter=models.Filter(
                must=[
                    models.FieldCondition(
                        key="metadata.file_hash",
                        match=models.MatchValue(value=file_hash)
                    ),
                    models.FieldCondition(
                        key="metadata.page_number",
                        match=models.MatchValue(value=page_number)
                    )
                ]
            ),
            limit=100,
            with_payload=True,
            with_vectors=False
        )
        
        chunks = result[0]
        
        if not chunks:
            print(f"❌ No content found for page {page_number}")
            return

        chunks.sort(key=lambda x: x.payload.get('metadata', {}).get('chunk_index', 0))

        metadata = chunks[0].payload.get('metadata', {})

        print(f"\n{'='*70}")
        print(f"📚 {metadata.get('document_title', 'Document')}")
        print(f"📄 Page {page_number} / {metadata.get('total_pages', 'N/A')}")
        print(f"🧩 {len(chunks)} chunk(s) on this page")
        print(f"{'='*70}\n")
        
        for chunk in chunks:
            text = chunk.payload.get('text', '')
            chunk_meta = chunk.payload.get('metadata', {})
            level = chunk_meta.get('level', 'text')
            
            if level in ['title', 'h1', 'h2', 'h3']:
                print(f"\n{'─'*70}")
                print(f"[{level.upper()}]")
                print(f"{'─'*70}")
            
            print(text)
            print()
        
        print(f"{'='*70}\n")
        
    except Exception as e:
        print(f"❌ Error: {e}")
        import traceback
        traceback.print_exc()

def reconstruct_document(file_hash, max_chars=None):
    """Reconstruct a full document from its chunks"""
    print(f"🏗️  Reconstructing document: {file_hash[:16]}...")
    
    try:
        client = get_client()
        
        # Recupera tutti i chunk
        all_chunks = []
        offset = None
        
        while True:
            result = client.scroll(
                collection_name=COLLECTION_NAME,
                scroll_filter=models.Filter(
                    must=[
                        models.FieldCondition(
                            key="metadata.file_hash",
                            match=models.MatchValue(value=file_hash)
                        )
                    ]
                ),
                limit=100,
                offset=offset,
                with_payload=True,
                with_vectors=False
            )
            
            chunks, next_offset = result
            all_chunks.extend(chunks)
            
            if not next_offset:
                break
            offset = next_offset
        
        if not all_chunks:
            print("❌ Document not found")
            return

        all_chunks.sort(key=lambda x: x.payload.get('metadata', {}).get('chunk_index', 0))

        metadata = all_chunks[0].payload.get('metadata', {})

        print(f"\n{'='*70}")
        print(f"📚 {metadata.get('document_title', 'Document')}")
        print(f"📄 {metadata.get('source', 'N/A')}")
        print(f"📍 {metadata.get('total_pages', 'N/A')} pages")
        print(f"🧩 {len(all_chunks)} chunks reconstructed")
        print(f"{'='*70}\n")
        
        # Ricostruisci testo
        full_text = "\n\n".join(
            chunk.payload.get('text', '') 
            for chunk in all_chunks
        )
        
        if max_chars and len(full_text) > max_chars:
            print(full_text[:max_chars])
            print(f"\n... [truncated, total: {len(full_text)} characters]")
        else:
            print(full_text)

        print(f"\n{'='*70}")
        print(f"✅ Document reconstructed: {len(full_text)} characters")
        print(f"{'='*70}\n")

    except Exception as e:
        print(f"❌ Error: {e}")
        import traceback
        traceback.print_exc()

def list_documents():
    """List all indexed documents"""
    print(f"📚 Documents in '{COLLECTION_NAME}':\n")
    
    try:
        client = get_client()
        
        # Ottieni tutti i punti
        all_points = []
        offset = None
        
        while True:
            result = client.scroll(
                collection_name=COLLECTION_NAME,
                limit=100,
                offset=offset,
                with_payload=True,
                with_vectors=False
            )
            
            points, next_offset = result
            all_points.extend(points)
            
            if not next_offset:
                break
            offset = next_offset
        
        if not all_points:
            print("❌ No documents found")
            return

        documents = {}
        for point in all_points:
            metadata = point.payload.get('metadata', {})
            file_hash = metadata.get('file_hash', 'unknown')

            if file_hash not in documents:
                documents[file_hash] = {
                    'title': metadata.get('document_title', 'Untitled'),
                    'source': metadata.get('source', 'N/A'),
                    'pages': metadata.get('total_pages', 0),
                    'chunks': 0,
                    'file_size': metadata.get('file_size', 0)
                }

            documents[file_hash]['chunks'] += 1

        print(f"Found {len(documents)} documents:\n")
        print(f"{'Hash':<18} {'Title':<35} {'Pages':<8} {'Chunks':<8}")
        print(f"{'─'*70}")

        for file_hash, info in sorted(documents.items(), key=lambda x: x[1]['title']):
            title = info['title'][:32] + '...' if len(info['title']) > 35 else info['title']
            print(f"{file_hash[:16]:<18} {title:<35} {info['pages']:<8} {info['chunks']:<8}")

        print(f"\n💡 Use: python manual_search.py info <hash> for details")
        print(f"💡 Use: python manual_search.py page <hash> <page_num> to read a page")
        print(f"💡 Use: python manual_search.py reconstruct <hash> to reconstruct the document\n")

    except Exception as e:
        print(f"❌ Error: {e}")
        import traceback
        traceback.print_exc()

def print_help():
    """Print usage help"""
    print(f"""
📖 KNOWLEDGE SERVER - MANUAL SEARCH TOOL
{'='*70}

USAGE:
  python manual_search.py <command> [arguments]

COMMANDS:
  search "query"              Semantic search (default)
  list                        List all indexed documents
  info <hash>                 Show document info and structure
  page <hash> <n>             Read a specific page of a document
  reconstruct <hash> [max]    Reconstruct full document
  help                        Show this help

EXAMPLES:
  python manual_search.py "semethic interaction"
  python manual_search.py list
  python manual_search.py info abc123def456...
  python manual_search.py page abc123def456... 5
  python manual_search.py reconstruct abc123def456... 5000

CONFIG:
  OLLAMA_MODEL: {OLLAMA_MODEL}
  QDRANT: {QDRANT_HOST}:{QDRANT_PORT}
  COLLECTION: {COLLECTION_NAME}
{'='*70}
""")

def main():
    if len(sys.argv) < 2:
        print_help()
        sys.exit(1)
    
    command = sys.argv[1].lower()
    
    if command in ['help', '-h', '--help']:
        print_help()
    
    elif command == 'search' or command == 's':
        if len(sys.argv) < 3:
            print("❌ Specifica la query di ricerca")
            print("Uso: python manual_search.py search \"tua query\"")
            sys.exit(1)
        query_text = sys.argv[2]
        limit = int(sys.argv[3]) if len(sys.argv) > 3 else 5
        search(query_text, limit)
    
    elif command == 'list' or command == 'ls':
        list_documents()
    
    elif command == 'info' or command == 'i':
        if len(sys.argv) < 3:
            print("❌ Specifica l'hash del file")
            print("Uso: python manual_search.py info <hash>")
            sys.exit(1)
        file_hash = sys.argv[2]
        get_document_info(file_hash)
    
    elif command == 'page' or command == 'p':
        if len(sys.argv) < 4:
            print("❌ Specifica hash e numero pagina")
            print("Uso: python manual_search.py page <hash> <num_pagina>")
            sys.exit(1)
        file_hash = sys.argv[2]
        try:
            page_num = int(sys.argv[3])
        except ValueError:
            print("❌ Il numero pagina deve essere un intero")
            sys.exit(1)
        read_page(file_hash, page_num)
    
    elif command == 'reconstruct' or command == 'r':
        if len(sys.argv) < 3:
            print("❌ Specifica l'hash del file")
            print("Uso: python manual_search.py reconstruct <hash> [max_chars]")
            sys.exit(1)
        file_hash = sys.argv[2]
        max_chars = int(sys.argv[3]) if len(sys.argv) > 3 else None
        reconstruct_document(file_hash, max_chars)
    
    else:
        # Default: treat as search query
        query_text = sys.argv[1]
        limit = int(sys.argv[2]) if len(sys.argv) > 2 else 5
        search(query_text, limit)

if __name__ == "__main__":
    main()
