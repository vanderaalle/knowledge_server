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
1. Ensure Ollama is running with `mxbai-embed-large`.
2. Ensure Qdrant is running on port 6333.
3. Install dependencies: `pip install -r requirements.txt`.
4. Add the server to your MCP client (Claude, etc.).

---
*Created and maintained by Antigravity AI.*
