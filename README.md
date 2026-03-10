# Knowledge Server MCP (Optimized)

A powerful MCP server for managing a semantic PDF library with indexing and OCR capabilities.

## How it works

Your PDF library lives on disk. When you index it, `server.py` extracts text from each file (fast via PyMuPDF, with OCR fallback for scanned books), splits it into chunks, and sends each chunk to a local Ollama model (`mxbai-embed-large`) which converts it into an **embedding** — a list of 1024 numbers that captures the semantic meaning of the text. These are stored in **Qdrant**, a vector database running in Docker on your machine.

When you search, your query goes through the same process: converted to numbers, then Qdrant finds the stored chunks whose numbers are most similar. No keywords needed — just natural language.

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
- **Anna's Archive Integration**: Search and download books directly.

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
Open `ks_sandbox.ipynb`, set `PDF_DIR` to your folder, and run the indexing cell.

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
- **`query_library(query, n_results)`** - Semantic search across your library
- **`get_document_info(file_hash)`** - Get document metadata
- **`read_page(file_hash, page_number)`** - Read a specific page
- **`reconstruct_document(file_hash)`** - Reconstruct full document text
- **`open_pdf_page(file_path, page_number)`** - Open a PDF at a specific page in Document Viewer
- **`search_annas_archive(query, limit)`** - Search Anna's Archive for books
- **`download_from_annas_archive(md5)`** - Download books from Anna's Archive

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

3. **Find and download a book**:
   ```
   search_annas_archive("generative deep learning", limit=3)
   download_from_annas_archive("c17f7a3108c48634ff635f34497c977b")
   ```

