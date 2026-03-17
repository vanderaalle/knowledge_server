# Personal configuration template.
# Copy this file to config.py and edit for your machine.

# Path to your books folder
BOOKS_DIR = "/home/youruser/Books"

# Where the empty-hash cache is stored (skips no-text files on re-index)
EMPTY_HASH_CACHE = "~/.local/share/knowledge_server/empty_hashes.json"

# Viewers — set to None to use system default (xdg-open)
PDF_VIEWER = "evince"       # e.g. "evince", "okular", "zathura", None
EPUB_VIEWER = "foliate"     # e.g. "foliate", "ebook-viewer", None
