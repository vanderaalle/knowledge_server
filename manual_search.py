#!/usr/bin/env python3
"""
Script manuale per interrogare Qdrant con supporto ai nuovi metadati e tool.
"""

import sys
import os
import hashlib
from pathlib import Path
import ollama
from qdrant_client import QdrantClient
from qdrant_client.http import models

# Configurazione
OLLAMA_MODEL = os.getenv('OLLAMA_MODEL', 'nomic-embed-text')
QDRANT_HOST = os.getenv('QDRANT_HOST', 'localhost')
QDRANT_PORT = int(os.getenv('QDRANT_PORT', '6333'))
COLLECTION_NAME = os.getenv('COLLECTION_NAME', 'knowledge_base')

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
    """Ricerca semantica con visualizzazione metadati ricchi"""
    print(f"🔍 Ricerca per: '{query}'")
    print(f"   Modello: {OLLAMA_MODEL}")
    print(f"   Collection: {COLLECTION_NAME}\n")
    
    try:
        # 1. Genera embedding per la query
        print("⚙️  Generazione embedding...")
        response = ollama.embeddings(model=OLLAMA_MODEL, prompt=query)
        query_vector = response["embedding"]

        # 2. Cerca in Qdrant
        print(f"🗄️  Interrogazione Qdrant...")
        client = get_client()
        
        search_result = client.search(
            collection_name=COLLECTION_NAME,
            query_vector=query_vector,
            limit=limit
        )

        # 3. Stampa risultati con metadati ricchi
        print(f"\n{'='*70}")
        print(f"📊 Trovati {len(search_result)} risultati")
        print(f"{'='*70}\n")
        
        for i, result in enumerate(search_result):
            metadata = result.payload.get('metadata', {})
            text = result.payload.get('text', '')
            
            print(f"{'─'*70}")
            print(f"📄 Risultato {i+1} | Score: {result.score:.4f}")
            print(f"{'─'*70}")
            print(f"📚 Documento:  {metadata.get('document_title', 'N/A')}")
            print(f"📄 File:       {metadata.get('source', 'N/A')}")
            print(f"📍 Pagina:     {metadata.get('page_number', 'N/A')} / {metadata.get('total_pages', 'N/A')}")
            print(f"🧩 Chunk:      {metadata.get('chunk_index', 'N/A')} / {metadata.get('total_chunks', 'N/A')}")
            print(f"🏷️  Tipo:       {metadata.get('level', 'text')} ({metadata.get('content_type', 'text')})")
            print(f"🔗 File hash:  {metadata.get('file_hash', 'N/A')[:16]}...")
            
            if metadata.get('heading_context'):
                print(f"📌 Contesto:   {metadata.get('heading_context')[:80]}...")
            
            print(f"\n📝 Testo:")
            print(f"{text[:800]}{'...' if len(text) > 800 else ''}")
            print(f"{'─'*70}\n")

    except Exception as e:
        print(f"❌ Errore durante la ricerca: {e}")
        import traceback
        traceback.print_exc()

def get_document_info(file_hash):
    """Ottieni informazioni su un documento specifico"""
    print(f"📋 Info documento: {file_hash[:16]}...")
    
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
            print("❌ Nessun documento trovato con questo hash")
            return
        
        # Estrai metadati dal primo chunk
        first_chunk = all_chunks[0]
        metadata = first_chunk.payload.get('metadata', {})
        
        print(f"\n{'='*70}")
        print(f"📚 {metadata.get('document_title', 'Documento senza titolo')}")
        print(f"{'='*70}")
        print(f"📄 File:       {metadata.get('source', 'N/A')}")
        print(f"📍 Pagine:     {metadata.get('total_pages', 'N/A')}")
        print(f"🧩 Chunks:     {len(all_chunks)} / {metadata.get('total_chunks', 'N/A')}")
        print(f"📏 Dimensione: {metadata.get('file_size', 0) / 1024:.1f} KB")
        print(f"🔗 Hash:       {file_hash}")
        
        # Trova tutti gli heading
        print(f"\n📑 Struttura (headings trovati):")
        print(f"{'─'*70}")
        
        headings_found = []
        for chunk in sorted(all_chunks, key=lambda x: x.payload.get('metadata', {}).get('chunk_index', 0)):
            level = chunk.payload.get('metadata', {}).get('level', 'paragraph')
            if level in ['title', 'h1', 'h2', 'h3']:
                page = chunk.payload.get('metadata', {}).get('page_number', '?')
                text = chunk.payload.get('text', '')[:60]
                headings_found.append((level, page, text))
        
        if headings_found:
            for level, page, text in headings_found[:20]:  # Mostra primi 20
                indent = "  " * (0 if level == 'title' else 1 if level == 'h1' else 2)
                print(f"{indent}[{level.upper():6}] Pag. {page:3}: {text}...")
            
            if len(headings_found) > 20:
                print(f"\n  ... e altri {len(headings_found) - 20} headings")
        else:
            print("  Nessun heading rilevato")
            
        print(f"{'='*70}\n")
        
    except Exception as e:
        print(f"❌ Errore: {e}")
        import traceback
        traceback.print_exc()

def read_page(file_hash, page_number):
    """Leggi una pagina specifica di un documento"""
    print(f"📖 Lettura pagina {page_number}...")
    
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
            print(f"❌ Nessun contenuto trovato per pagina {page_number}")
            return
        
        # Ordina per chunk_index
        chunks.sort(key=lambda x: x.payload.get('metadata', {}).get('chunk_index', 0))
        
        metadata = chunks[0].payload.get('metadata', {})
        
        print(f"\n{'='*70}")
        print(f"📚 {metadata.get('document_title', 'Documento')}")
        print(f"📄 Pagina {page_number} / {metadata.get('total_pages', 'N/A')}")
        print(f"🧩 {len(chunks)} chunk(s) in questa pagina")
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
        print(f"❌ Errore: {e}")
        import traceback
        traceback.print_exc()

def reconstruct_document(file_hash, max_chars=None):
    """Ricostruisci un documento completo dai chunk"""
    print(f"🏗️  Ricostruzione documento: {file_hash[:16]}...")
    
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
            print("❌ Nessun documento trovato")
            return
        
        # Ordina per chunk_index
        all_chunks.sort(key=lambda x: x.payload.get('metadata', {}).get('chunk_index', 0))
        
        metadata = all_chunks[0].payload.get('metadata', {})
        
        print(f"\n{'='*70}")
        print(f"📚 {metadata.get('document_title', 'Documento')}")
        print(f"📄 {metadata.get('source', 'N/A')}")
        print(f"📍 {metadata.get('total_pages', 'N/A')} pagine")
        print(f"🧩 {len(all_chunks)} chunks ricostruiti")
        print(f"{'='*70}\n")
        
        # Ricostruisci testo
        full_text = "\n\n".join(
            chunk.payload.get('text', '') 
            for chunk in all_chunks
        )
        
        if max_chars and len(full_text) > max_chars:
            print(full_text[:max_chars])
            print(f"\n... [troncato, totale: {len(full_text)} caratteri]")
        else:
            print(full_text)
        
        print(f"\n{'='*70}")
        print(f"✅ Documento ricostruito: {len(full_text)} caratteri")
        print(f"{'='*70}\n")
        
    except Exception as e:
        print(f"❌ Errore: {e}")
        import traceback
        traceback.print_exc()

def list_documents():
    """Elenca tutti i documenti indicizzati"""
    print(f"📚 Elenco documenti in '{COLLECTION_NAME}':\n")
    
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
            print("❌ Nessun documento trovato")
            return
        
        # Raggruppa per file_hash
        documents = {}
        for point in all_points:
            metadata = point.payload.get('metadata', {})
            file_hash = metadata.get('file_hash', 'unknown')
            
            if file_hash not in documents:
                documents[file_hash] = {
                    'title': metadata.get('document_title', 'Senza titolo'),
                    'source': metadata.get('source', 'N/A'),
                    'pages': metadata.get('total_pages', 0),
                    'chunks': 0,
                    'file_size': metadata.get('file_size', 0)
                }
            
            documents[file_hash]['chunks'] += 1
        
        # Stampa risultati
        print(f"Trovati {len(documents)} documenti:\n")
        print(f"{'Hash':<18} {'Titolo':<35} {'Pagine':<8} {'Chunks':<8}")
        print(f"{'─'*70}")
        
        for file_hash, info in sorted(documents.items(), key=lambda x: x[1]['title']):
            title = info['title'][:32] + '...' if len(info['title']) > 35 else info['title']
            print(f"{file_hash[:16]:<18} {title:<35} {info['pages']:<8} {info['chunks']:<8}")
        
        print(f"\n💡 Usa: python manual_search.py info <hash> per dettagli")
        print(f"💡 Usa: python manual_search.py page <hash> <num_pagina> per leggere una pagina")
        print(f"💡 Usa: python manual_search.py reconstruct <hash> per ricostruire il documento\n")
        
    except Exception as e:
        print(f"❌ Errore: {e}")
        import traceback
        traceback.print_exc()

def print_help():
    """Print usage help"""
    print(f"""
📖 KNOWLEDGE SERVER - MANUAL SEARCH TOOL
{'='*70}

USO:
  python manual_search.py <comando> [argomenti]

COMANDI:
  search "query"              Ricerca semantica (default)
  list                        Elenca tutti i documenti indicizzati
  info <hash>                 Mostra info e struttura di un documento
  page <hash> <n>             Leggi pagina specifica di un documento
  reconstruct <hash> [max]    Ricostruisci documento completo
  help                        Mostra questo aiuto

ESEMPI:
  python manual_search.py "intelligenza artificiale"
  python manual_search.py list
  python manual_search.py info abc123def456...
  python manual_search.py page abc123def456... 5
  python manual_search.py reconstruct abc123def456... 5000

CONFIGURAZIONE:
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
