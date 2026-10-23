# Design: a second source for Markdown notes

Status: planned, 2026-10-23. Not implemented. Start after the `pdf_library_v2`
switch-over (around 2026-10-31), because steps 1–2 touch the same indexing code
that `scripts/reindex_nightly.py` runs every night.

## Goal

Make `~/Notes` searchable alongside `~/Books`. `~/Notes` is a folder of Markdown
research notes with one subfolder per project, and it doubles as an Obsidian vault.
The user never tags, files or indexes notes by hand: notes are picked up when they
are saved, and whatever metadata exists is written by Claude when the note is created.

Notes must not crowd out books in normal searches.

## Today

The server assumes one source:

- **Folder:** `BOOKS_DIR` in `config.py`. Sources are stored relative to it, and
  `library_checks.py` enforces the book conventions (shelves, `Author - Title (Year)`).
- **Collection:** `COLLECTION_NAME`, a module-level variable in `vector_db.py`, set
  from the environment. `scripts/add_book.py` reads the same variable.
- **File types:** `BOOK_EXTENSIONS = (".pdf", ".epub")` in `library_checks.py`.
  `server.py` and `scripts/reindex_nightly.py` also hard-code PDF and EPUB globs.
- **Change detection:** by content hash. A file whose hash is already indexed is
  skipped. Point IDs are `md5(file_path + chunk_index)`.

## Design

### 1. Sources in config

```python
SOURCES = {
    "books": {"dir": "~/Books", "collection": "pdf_library_v2",
              "exts": [".pdf", ".epub"], "conventions": True},
    "notes": {"dir": "~/Notes", "collection": "notes",
              "exts": [".md"], "conventions": False},
}
```

`BOOKS_DIR` and `COLLECTION_NAME` stay as aliases for `SOURCES["books"]`, so
`add_book.py`, Jorge and the library checks keep working unchanged.
`VectorDBClient` takes a collection name instead of reading the global.

The two collections must use the same embedding model (`OLLAMA_MODEL`), so that
scores from both collections can be compared when results are merged.

### 2. Markdown extraction

Add `extract_text_from_markdown()` to `PDFProcessor`, returning the usual
`(full_text, page_texts, total_pages, used_ocr)`:

- **Pages:** each top-level section (`#` / `##` heading) counts as a page, so
  `read_page` and `reconstruct_document` work unchanged.
- **Front matter:** strip it from the text and read `project`, `source` and the
  date fields into the chunk metadata. If there is no front matter, `project`
  comes from the first subfolder under `~/Notes`.
- **Title:** the first `#` heading, or else the file name. Never ask an LLM.
- **Links:** keep `[[Author - Title (Year)]]` links in the text. They match book
  file names, so a later step can resolve them to books in the library.

The book conventions (`library_checks`) are not applied to notes.

### 3. Re-indexing edited notes

Notes change, but books don't. With hash-based detection, an edited note gets a
new hash and is indexed again. The old version's extra chunks are left behind,
though: point IDs are per path and chunk index, so a note that shrinks keeps its
old trailing chunks.

For the notes source, index **by path**: delete every point whose
`metadata.source_path` matches (a new `delete_by_path()` next to `delete_by_file()`),
then insert the new chunks. A deleted or renamed note gets its points deleted.

Trigger: a small watcher (`scripts/watch_notes.py`, using `watchdog` or inotify)
run as a systemd user service, with a few seconds of debounce so that an editor
saving repeatedly doesn't index every keystroke. A nightly catch-up pass
re-indexes anything whose modification time is newer than its indexed copy, in
case the watcher was down.

### 4. Search scope

`query_library(query, n_results=5, scope="books")`, where `scope` is `"books"`,
`"notes"` or `"all"`. With `all`, search both collections, merge the results by
score, and label each one `[book]` or `[note]`. `search_text` gets the same
parameter. The default stays `books`, so existing behaviour doesn't change.

Optionally, a `project` filter for notes (e.g. only `bsm`).

### 5. Later (optional)

- Jorge: a Notes tab.
- Resolve `[[Author - Title (Year)]]` links in notes to library books, so a
  search result can show which books a note cites, and the reverse.

## Not chosen

**Putting `~/Notes` inside `~/Books`** would index notes with no code change.
But the library audit would flag every note as a misnamed book, the nightly
re-index would treat notes as books, and notes would mix into every book search.

## Order of work

1. Config `SOURCES` + collection parameter in `VectorDBClient` (no behaviour change; check that book queries return the same results).
2. Markdown extractor + `delete_by_path` + index `~/Notes` once.
3. `scope` on `query_library` / `search_text`.
4. Watcher service + nightly catch-up.
