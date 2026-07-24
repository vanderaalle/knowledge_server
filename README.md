# Knowledge Server MCP

A local, fully private semantic search engine for your PDF and epub library, integrated with [Claude Code](https://claude.ai/code) via MCP.

**What it does:** index your book collection once, then ask Claude natural-language questions and get answers with exact page references — all running on your own machine, nothing sent to the cloud except your conversation with Claude.

**Stack:** [Qdrant](https://qdrant.tech/) (vector DB) · [Ollama](https://ollama.ai/) (local embeddings + LLM) · [Tesseract](https://github.com/tesseract-ocr/tesseract) (OCR for scanned books) · [FastMCP](https://github.com/jlowin/fastmcp) (MCP server)

> **The MCP layer is optional.** The indexing pipeline and vector database work standalone — you can query them directly from Python, from the included Jupyter notebook, or from any MCP-compatible client (Claude Code, GitHub Copilot, Cursor, etc.). The Qdrant collection is also queryable via its REST API from any tool.

## How it works

Your PDF library lives on disk. When you index it, `server.py` extracts text from each file (fast via PyMuPDF, with OCR fallback for scanned books), splits it into chunks, and sends each chunk to a local Ollama model (`mxbai-embed-large`) which converts it into an **embedding** — a list of 1024 numbers that captures the semantic meaning of the text. These are stored in **Qdrant**, a vector database running in Docker on your machine.

When you search, your query goes through the same process: converted to numbers, then Qdrant finds the stored chunks whose numbers are most similar. No keywords needed — just natural language.

### Semantic search vs. exact search

The server provides two complementary search tools:

- **`query_library`** — semantic search. Finds text that is *about* the same concept, even if it uses different words. Best when you have a topic or idea in mind but don't know exactly where it appears. Example: searching "sound granulation" will also surface chunks about microsound, particle synthesis, stochastic clouds.
- **`search_text`** — literal search. Finds chunks that contain the exact term or phrase. Best when you know a specific word, name, or coined term. Example: searching "semethic" will find exactly where Hoffmeyer uses that word.

If you connect this server to **Claude Code** via MCP, you can ask Claude questions directly and it will query your library under the hood.

```
You ask a question
  → Claude converts it to an embedding (via Ollama)
  → Qdrant finds the most similar PDF chunks
  → Claude reads them and answers you
```

Everything runs locally. No data leaves your machine except your messages to Claude.

### Key paths
| What | Where |
|------|-------|
| Vector database | `~/qdrant_storage/` |
| Server code | `server.py` |
| Claude Code MCP config | `~/.claude/settings.json` |
| Setup notebook | `install.ipynb` |
| Usage notebook | `knowledge_server.ipynb` |

## Features
- **Fast Indexing**: Uses `PyMuPDF` for high-speed text extraction.
- **OCR Support**: Fallback to Tesseract OCR for scans and images.
- **Semantic Search**: Powered by Ollama (`mxbai-embed-large`) and Qdrant.
- **Literal + Semantic Search**: Exact term lookup and concept-based search.

## Structure
- `server.py`: The main MCP server entry point.
- `scripts/`: Utility and maintenance scripts (`manage_index.py`, `organize_books.py`, etc.).
- `scripts/dev_legacy/`: Old dev/debug scripts, kept for reference.
- `install.ipynb`: Setup and dependency installation notebook.
- `knowledge_server.ipynb`: Notebook for indexing and querying interactively.

## Quick Start (Linux)

### 1. Install system dependencies
```bash
sudo apt install docker.io tesseract-ocr poppler-utils python3 python3-venv
```

### 2. Install Ollama
```bash
curl -fsSL https://ollama.com/install.sh | sh
ollama pull mxbai-embed-large
ollama serve &
```

### 3. Start Qdrant
```bash
sudo docker run -d \
  --name qdrant \
  -p 6333:6333 \
  --restart unless-stopped \
  -v ~/qdrant_storage:/qdrant/storage \
  qdrant/qdrant
```

### 4. Set up Python environment
```bash
cd knowledge_server
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 5. Configure Claude Code
Edit `~/.claude/settings.json` (replace paths with your actual paths):
```json
{
  "mcpServers": {
    "knowledge-server": {
      "command": "/home/youruser/knowledge_server/.venv/bin/python",
      "args": ["/home/youruser/knowledge_server/server.py"],
      "env": {
        "OLLAMA_MODEL": "mxbai-embed-large",
        "QDRANT_HOST": "localhost",
        "QDRANT_PORT": "6333",
        "COLLECTION_NAME": "pdf_library"
      }
    }
  }
}
```

### 6. Index your PDFs
Open `knowledge_server.ipynb`, set `PDF_DIR` to your folder, and run the indexing cell.

### 7. Search
Restart Claude Code and ask: *"find something about [your topic]"*

> **Note:** `open_pdf_page` uses `evince` (Document Viewer on Ubuntu/Debian). On macOS replace `evince` with `open` in `server.py`. On Windows use `start`.

---

## Quick Start (macOS)

### 1. Install system dependencies
```bash
brew install tesseract poppler python
```
Install Docker Desktop from https://www.docker.com/products/docker-desktop

### 2. Install Ollama
```bash
brew install ollama
ollama pull mxbai-embed-large
ollama serve &
```

### 3. Start Qdrant
```bash
docker run -d \
  --name qdrant \
  -p 6333:6333 \
  --restart unless-stopped \
  -v ~/qdrant_storage:/qdrant/storage \
  qdrant/qdrant
```

### 4. Set up Python environment
```bash
cd knowledge_server
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 5. Configure Claude Code
Edit `~/.claude/settings.json`:
```json
{
  "mcpServers": {
    "knowledge-server": {
      "command": "/Users/youruser/knowledge_server/.venv/bin/python",
      "args": ["/Users/youruser/knowledge_server/server.py"],
      "env": {
        "OLLAMA_MODEL": "mxbai-embed-large",
        "QDRANT_HOST": "localhost",
        "QDRANT_PORT": "6333",
        "COLLECTION_NAME": "pdf_library"
      }
    }
  }
}
```

### 6. Fix PDF viewer
In `server.py`, find `open_pdf_page` and replace `"evince"` with `"open"`:
```python
subprocess.Popen(["open", "-a", "Preview", file_path])
```
> Note: macOS Preview doesn't support opening to a specific page via CLI. For page-level navigation install `mupdf`: `brew install mupdf-tools` and use `mupdf`.

### 7. Index and search
Same as Linux: open `knowledge_server.ipynb`, index, restart Claude Code and search.

---

## Quick Start (Windows)

### 1. Install system dependencies
Install from their official sites:
- [Python 3.10+](https://www.python.org/downloads/)
- [Docker Desktop](https://www.docker.com/products/docker-desktop)
- [Tesseract OCR](https://github.com/UB-Mannheim/tesseract/wiki)
- [Poppler for Windows](https://github.com/oschwartz10612/poppler-windows/releases)

### 2. Install Ollama
Download from https://ollama.com, then in PowerShell:
```powershell
ollama pull mxbai-embed-large
ollama serve
```

### 3. Start Qdrant
```powershell
docker run -d `
  --name qdrant `
  -p 6333:6333 `
  --restart unless-stopped `
  -v $env:USERPROFILE\qdrant_storage:/qdrant/storage `
  qdrant/qdrant
```

### 4. Set up Python environment
```powershell
cd knowledge_server
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

### 5. Configure Claude Code
Edit `%USERPROFILE%\.claude\settings.json`:
```json
{
  "mcpServers": {
    "knowledge-server": {
      "command": "C:\\Users\\youruser\\knowledge_server\\.venv\\Scripts\\python.exe",
      "args": ["C:\\Users\\youruser\\knowledge_server\\server.py"],
      "env": {
        "OLLAMA_MODEL": "mxbai-embed-large",
        "QDRANT_HOST": "localhost",
        "QDRANT_PORT": "6333",
        "COLLECTION_NAME": "pdf_library",
        "TESSERACT_CMD": "C:\\Program Files\\Tesseract-OCR\\tesseract.exe",
        "POPPLER_PATH": "C:\\path\\to\\poppler\\bin"
      }
    }
  }
}
```

### 6. Fix PDF viewer
In `server.py`, find `open_pdf_page` and replace the `subprocess.Popen` line with:
```python
subprocess.Popen(["start", "", f"/p {page_number}", file_path], shell=True)
```

### 7. Index and search
Same as Linux: open `knowledge_server.ipynb`, index, restart Claude Code and search.

---

## Setup

### Prerequisites
1. **Ollama** running with `mxbai-embed-large`:
   ```bash
   ollama pull mxbai-embed-large
   ollama serve
   ```

2. **Qdrant** running on port 6333, with a persistent bind mount so your indexed data survives container restarts and is easy to back up:
   ```bash
   docker run -d \
     --name qdrant \
     -p 6333:6333 \
     --restart unless-stopped \
     -v ~/qdrant_storage:/qdrant/storage \
     qdrant/qdrant
   ```
   The database will be stored at `~/qdrant_storage/` on your machine.
   > **Warning:** the plain `docker run -d -p 6333:6333 qdrant/qdrant` command stores data inside the container — it will be lost if the container is removed.

3. **Python dependencies**:
   ```bash
   pip install -r requirements.txt
   ```

### MCP Configuration

Add the server to your MCP client configuration:

#### For Roo/Cline (VS Code)

Edit your MCP settings file (usually at `~/Library/Application Support/Antigravity/User/globalStorage/rooveterinaryinc.roo-cline/settings/mcp_settings.json` on macOS):

```json
{
  "mcpServers": {
    "knowledge-server": {
      "command": "/path/to/your/python",
      "args": [
        "/path/to/knowledge_server/server.py"
      ],
      "env": {
        "OLLAMA_MODEL": "mxbai-embed-large",
        "QDRANT_HOST": "localhost",
        "QDRANT_PORT": "6333",
        "COLLECTION_NAME": "pdf_library"
      },
      "disabled": false,
      "autoApprove": []
    }
  }
}
```

#### For Claude Code

Edit `~/.claude/settings.json`:

```json
{
  "mcpServers": {
    "knowledge-server": {
      "command": "/path/to/knowledge_server/.venv/bin/python",
      "args": ["/path/to/knowledge_server/server.py"],
      "env": {
        "OLLAMA_MODEL": "mxbai-embed-large",
        "QDRANT_HOST": "localhost",
        "QDRANT_PORT": "6333",
        "COLLECTION_NAME": "pdf_library"
      }
    }
  }
}
```

#### For Claude Desktop

Add to your Claude Desktop config (`~/Library/Application Support/Claude/claude_desktop_config.json` on macOS):

```json
{
  "mcpServers": {
    "knowledge-server": {
      "command": "/path/to/your/python",
      "args": ["/path/to/knowledge_server/server.py"],
      "env": {
        "OLLAMA_MODEL": "mxbai-embed-large",
        "QDRANT_HOST": "localhost",
        "QDRANT_PORT": "6333"
      }
    }
  }
}
```

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `OLLAMA_MODEL` | `mxbai-embed-large` | Embedding model for semantic search |
| `QDRANT_HOST` | `localhost` | Qdrant database host |
| `QDRANT_PORT` | `6333` | Qdrant database port |
| `COLLECTION_NAME` | `pdf_library` | Vector collection name |
| `TESSERACT_CMD` | `/opt/homebrew/bin/tesseract` | Path to Tesseract OCR |
| `POPPLER_PATH` | `/opt/homebrew/bin` | Path to Poppler utils |

### Available MCP Tools

Once configured, the following tools are available:

- **`index_library(path)`** - Fast parallel PDF indexing
- **`index_single_pdf(file_path)`** - Index a single PDF file
- **`index_with_ocr(path)`** - OCR-based indexing for scanned documents
- **`query_library(query, n_results)`** - Semantic search: finds text *about* the same concept, even with different words
- **`search_text(term, n_results)`** - Literal search: finds exact words or coined terms (e.g. author-specific terminology)
- **`get_document_info(file_hash)`** - Get document metadata
- **`read_page(file_hash, page_number)`** - Read a specific page
- **`reconstruct_document(file_hash)`** - Reconstruct full document text
- **`open_pdf_page(file_path, page_number)`** - Open a PDF at a specific page in Document Viewer

### Searching with Claude Code

When the MCP server is connected, ask Claude naturally and it will search your library and present numbered results:

```
You:   "find something about spectral harmony"

Claude: Found 5 results:

        1. Murail - Spectral Music (p.42/210)
           "...the harmonic series as a structural principle..."

        2. Grisey - Temporal Spaces (p.17/180)
           "...spectral harmony differs from traditional tonality..."

        Open which? (e.g. "1", "2 and 3", "all")

You:   "2"

Claude: [opens Grisey - Temporal Spaces at page 17 in Document Viewer]
```

### Usage Example

1. **Index your PDF library**:
   ```
   index_library("/path/to/your/pdfs")
   ```

2. **Search your documents**:
   ```
   query_library("machine learning music generation", n_results=5)
   ```

---

## Recommended setup: flat Books folder

The simplest and most maintainable layout is a flat folder of PDFs — no library manager required.

```
~/Books/
  Silence - John Cage.pdf
  Computer Music Tutorial - Curtis Roads.pdf
  Syntactic Structures - Noam Chomsky.pdf
  ...
```

1. Drop your PDFs into `~/Books/` (or any flat folder).
2. Point the indexer at it:
   ```
   index_library("/home/youruser/Books")
   ```
3. Search with natural language via Claude Code or `manual_search.py`:
   ```
   query_library("semiotic interaction and sound")
   ```
4. Claude returns ranked results with titles, pages, and snippets — pick one and it opens in your PDF viewer at the exact page.

The knowledge server is the index — filenames and Qdrant are the only metadata you need. No external library manager, no path drift when metadata changes.

> **Using Calibre?** It works too — just point `index_library` at your Calibre Library folder. But be aware that renaming books in Calibre moves their files, which can make source paths in Qdrant go stale. `scripts/cleanup_orphans.py` cleans those up.

---

## Configuration

On a new machine, copy the templates and edit for your setup:

```bash
cp config.example.py config.py
cp scripts/ocr_priority_config.example.py scripts/ocr_priority_config.py
```

### config.py

| Setting | Typical value | Description |
|---------|--------------|-------------|
| `BOOKS_DIR` | `~/Books` | Path to your flat PDF folder |
| `EMPTY_HASH_CACHE` | `~/.local/share/knowledge_server/empty_hashes.json` | Cache of no-text file hashes — skip on future re-indexes |
| `PDF_VIEWER` | `evince` | App to open PDFs (`evince`, `okular`, `zathura`, or `None` for system default) |
| `EPUB_VIEWER` | `foliate` | App to open epubs (`foliate`, or `None` for system default) |

### Environment variables (Qdrant / Ollama)

These are set in `~/.claude/settings.json` under `mcpServers.env`, or exported in your shell:

| Variable | Default | Description |
|----------|---------|-------------|
| `QDRANT_HOST` | `localhost` | Qdrant host |
| `QDRANT_PORT` | `6333` | Qdrant port |
| `COLLECTION_NAME` | `pdf_library` | Qdrant collection name |
| `OLLAMA_MODEL` | `mxbai-embed-large` | Embedding model |
| `OLLAMA_URL` | `http://localhost:11434` | Ollama base URL |

### scripts/ocr_priority_config.py

| Setting | Description |
|---------|-------------|
| `BOOKS_DIR` | Inherited from `config.py` — no need to set separately |
| `TIER1_KEYWORDS` | List of filename substrings to OCR first (your high-value books) |
| `SKIP_KEYWORDS` | Books to skip even if they match Tier 1 |

---

## Maintenance

### Full re-index (clean slate)

Do this when you want a fresh database — e.g. after a code update that changes the metadata format, or when the collection has accumulated stale/duplicate entries.

**Takes several hours. Do it when you can leave the machine running overnight.**

```python
# In knowledge_server.ipynb — run cells in order:

# 1. Wipe the collection (cell 4)
db = server.get_db()
db.delete_collection()

# 2. Re-index all text PDFs and epubs (no OCR) (cell 5)
server.index_library("/home/youruser/Books")
```

```bash
# 3. After step 2 finishes, run OCR on scanned books
python scripts/ocr_priority.py

# 4. Fix/improve titles using the LLM
python scripts/fix_titles.py
```

Steps 2–4 are safe to resume if interrupted — each one skips already-indexed files (by hash) or already-fixed titles.

### Adding a new book

The easiest way — runs the full pipeline (copy → index → fix title → clean orphans):

```bash
# Add a single book (copies to ~/Books/ if not already there)
python scripts/add_book.py /path/to/book.pdf

# For a scanned book needing OCR
python scripts/add_book.py /path/to/book.pdf --ocr

# Index everything new in ~/Books/ at once
python scripts/add_book.py --scan
```

### Removing or replacing a book

When you delete or replace a PDF, its old chunks remain in Qdrant pointing to a path that no longer exists. Clean them up with:

```bash
# Dry run — shows what would be deleted
python scripts/cleanup_orphans.py

# Apply
python scripts/cleanup_orphans.py --apply
```

Then run `index_library` to pick up any new/replacement files.

### Maintenance scripts

| Script | What it does |
|--------|-------------|
| `scripts/add_book.py` | Full pipeline: copy PDF → index → fix title → clean orphans. Use for adding single books or scanning for new ones (`--scan`) |
| `scripts/fix_titles.py` | Uses `llama3.2` to generate clean titles from first-page text and stores them in Qdrant |
| `scripts/ocr_priority.py` | Scans `~/Books/` for unindexed PDFs, runs Tesseract OCR on priority books |
| `scripts/export_library_report.py` | Generates a markdown or CSV report of all indexed books from Qdrant |
| `scripts/backfill_file_hash.py` | Backfills missing `file_hash` metadata on existing Qdrant chunks |
| `scripts/cleanup_orphans.py` | Removes Qdrant chunks whose source file no longer exists on disk |
| `scripts/backfill_full_text.py` | Backfills full chunk text in Qdrant (fixes truncation from older index runs) |

## Acknowledgments

This project started from an original idea and initial implementation by [Carlo Barbagallo](https://github.com/nojarec) ([nojarec/knowledge_server](https://github.com/nojarec/knowledge_server)). This repo has since diverged significantly and is maintained independently.

