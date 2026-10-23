# Personal configuration template.
# Copy this file to config.py and edit for your machine.

# Path to your books folder
BOOKS_DIR = "/home/youruser/Books"

# Where the empty-hash cache is stored (skips no-text files on re-index)
EMPTY_HASH_CACHE = "~/.local/share/knowledge_server/empty_hashes.json"

# Hashes of PDFs that are always OCR'd (garbled text layers); managed by reindex_with_ocr
FORCE_OCR_LIST = "~/.local/share/knowledge_server/force_ocr.json"

# Viewers — set to None to use system default (xdg-open)
PDF_VIEWER = "evince"       # e.g. "evince", "okular", "zathura", None
EPUB_VIEWER = "foliate"     # e.g. "foliate", "ebook-viewer", None

# Jorge (library browser) icon — a PNG/SVG path, or None for the bundled drawing
JORGE_ICON = None
