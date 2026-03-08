# Knowledge Server MCP (Optimized)

A powerful MCP server for managing a semantic PDF library with indexing and OCR capabilities.

## Features
- **Fast Indexing**: Uses `PyMuPDF` for high-speed text extraction.
- **OCR Support**: Fallback to Tesseract OCR for scans and images.
- **Semantic Search**: Powered by Ollama (`mxbai-embed-large`) and Qdrant.
- **Anna's Archive Integration**: Search and download books directly.

## Structure
- `server.py`: The main MCP server entry point.
- `index_remaining_ocr.py`: Main utility for incremental indexing and OCR.
- `scripts/`: Collection of utility and maintenance scripts.
- `mcp_settings.json`: Configuration for the MCP client.

## Setup

### Prerequisites
1. **Ollama** running with `mxbai-embed-large`:
   ```bash
   ollama pull mxbai-embed-large
   ollama serve
   ```

2. **Qdrant** running on port 6333:
   ```bash
   docker run -d -p 6333:6333 qdrant/qdrant
   ```

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
- **`search_annas_archive(query, limit)`** - Search Anna's Archive for books
- **`download_from_annas_archive(md5)`** - Download books from Anna's Archive

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

