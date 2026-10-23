#!/usr/bin/env python3
"""
Jorge — a local web UI to explore the indexed library.

Named for Jorge Luis Borges (The Library of Babel) and Jorge of Burgos, the blind
librarian of the Aedificium in Eco's The Name of the Rose. Unlike him, this one
shows you every book.

The catalog is derived live from the Qdrant collection (one entry per file_hash)
and cross-checked against BOOKS_DIR on disk, so it follows whatever is indexed.
It is cached in memory and rebuilt whenever the collection's point count changes.

Run:     python jorge.py [--port 8765] [--no-open]   server in the foreground
Launch:  python jorge.py --launch                  start it in the background if
                                                   needed, open an app window, exit
"""

import argparse
import os
import platform
import shutil
import subprocess
import sys
import threading
import time
import urllib.request
import webbrowser
from pathlib import Path

import numpy as np
import ollama
import uvicorn
from qdrant_client.http import models
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, Response
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from vector_db import get_db, embed_query, OLLAMA_MODEL, COLLECTION_NAME

try:
    import config as _config
except ImportError:
    _config = None

BOOKS_DIR = os.path.expanduser(getattr(_config, "BOOKS_DIR", None) or "~/Books")
PDF_VIEWER = getattr(_config, "PDF_VIEWER", None)
EPUB_VIEWER = getattr(_config, "EPUB_VIEWER", None)
# Optional personal icon; defaults to the bundled jorge.png (Eco's plan of the Aedificium).
JORGE_ICON = getattr(_config, "JORGE_ICON", None)
BOOK_EXTS = {".pdf", ".epub"}
HERE = Path(__file__).resolve().parent
if platform.system() == "Windows":
    CACHE_DIR = Path(os.environ.get("LOCALAPPDATA", os.path.expanduser("~"))) / "knowledge_server" / "Cache"
elif platform.system() == "Darwin":
    CACHE_DIR = Path(os.path.expanduser("~/Library/Caches/knowledge_server"))
else:
    CACHE_DIR = Path(os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache")) / "knowledge_server"
COVER_CACHE = CACHE_DIR / "covers"
COVER_CACHE.mkdir(parents=True, exist_ok=True)

_META_FIELDS = ["metadata.file_hash", "metadata.source_path", "metadata.source",
                "metadata.document_title", "metadata.total_pages"]


# ---------------------------------------------------------------- catalog

class Catalog:
    """Document-level view of the collection, rebuilt when the point count changes."""

    def __init__(self):
        self._lock = threading.Lock()
        self._points = None
        self.docs = {}          # file_hash -> doc dict
        self.unindexed = []     # files on disk with no chunks in the index
        self.built_at = 0.0

    def _point_count(self) -> int:
        return get_db().client.get_collection(COLLECTION_NAME).points_count or 0

    def get(self, force: bool = False):
        with self._lock:
            count = self._point_count()
            if force or count != self._points:
                self._build()
                self._points = count
            return self

    def _build(self):
        client = get_db().client
        docs, offset = {}, None
        while True:
            pts, offset = client.scroll(COLLECTION_NAME, limit=5000, offset=offset,
                                        with_payload=_META_FIELDS, with_vectors=False)
            for p in pts:
                m = (p.payload or {}).get("metadata", {})
                h = m.get("file_hash")
                if not h:
                    continue
                d = docs.get(h)
                if d is None:
                    path = m.get("source_path") or m.get("source") or ""
                    d = docs[h] = {
                        "hash": h,
                        "title": m.get("document_title") or Path(path).stem,
                        "path": path,
                        "file": os.path.basename(path),
                        "folder": _folder_of(path),
                        "ext": Path(path).suffix.lower().lstrip("."),
                        "pages": m.get("total_pages") or 0,
                        "chunks": 0,
                    }
                d["chunks"] += 1
            if offset is None:
                break

        for d in docs.values():
            try:
                st = os.stat(d["path"])
                d["exists"], d["size"], d["mtime"] = True, st.st_size, st.st_mtime
            except OSError:
                d["exists"], d["size"], d["mtime"] = False, 0, 0

        indexed_paths = {d["path"] for d in docs.values()}
        unindexed = []
        for root, dirs, files in os.walk(BOOKS_DIR):
            dirs[:] = [x for x in dirs if not x.startswith(".")]
            for f in files:
                if Path(f).suffix.lower() in BOOK_EXTS:
                    full = os.path.join(root, f)
                    if full not in indexed_paths:
                        st = os.stat(full)
                        unindexed.append({"path": full, "file": f, "folder": _folder_of(full),
                                          "size": st.st_size, "mtime": st.st_mtime})
        self.docs, self.unindexed, self.built_at = docs, unindexed, time.time()


def _folder_of(path: str) -> str:
    """Folder relative to BOOKS_DIR ('' = root); outside paths go under '~other/<parent>'."""
    parent = os.path.dirname(path)
    try:
        rel = os.path.relpath(parent, BOOKS_DIR)
    except ValueError:
        rel = ".."
    if rel.startswith(".."):
        return "~other/" + parent.replace("\\", "/").replace(":", "").strip("/")
    return "" if rel == "." else rel.replace(os.sep, "/")


CATALOG = Catalog()


# ---------------------------------------------------------------- semantic map

class BookMap:
    """Per-book mean embedding, kNN edges and a 2-D starting layout.

    Book vectors (mean of all a book's chunk vectors) are cached on disk and only
    recomputed for books that are new or whose chunk count changed (re-indexed).
    The first build reads every vector in the collection (~1.5 min for 240k).
    """
    K = 6
    CACHE = CACHE_DIR / "book_vectors.npz"

    def __init__(self):
        self._lock = threading.Lock()
        self.status, self.progress, self.error = "idle", 0.0, None
        self.result, self._key = None, None

    def get(self):
        """Return the map if it is current; otherwise start (or keep) building it."""
        cat = CATALOG.get()
        key = (cat._points, len(cat.docs))
        with self._lock:
            if self.result is not None and self._key == key:
                return {"status": "ready", **self.result}
            if self.status != "building":
                self.status, self.progress, self.error = "building", 0.0, None
                threading.Thread(target=self._build, args=(cat, key), daemon=True).start()
            return {"status": self.status, "progress": self.progress, "error": self.error}

    def _load_cache(self):
        try:
            z = np.load(self.CACHE)
            return {h: (v, int(c)) for h, v, c in zip(z["hashes"], z["vectors"], z["counts"])}
        except Exception:
            return {}

    def _build(self, cat, key):
        try:
            cache = self._load_cache()
            want = {h: d["chunks"] for h, d in cat.docs.items()}
            todo = [h for h, n in want.items() if h not in cache or cache[h][1] != n]
            if todo:
                self._fetch_means(todo, cache, sum(want[h] for h in todo))
            vecs = {h: cache[h] for h in want if h in cache}
            np.savez(self.CACHE, hashes=np.array(list(vecs)), counts=np.array([c for _, c in vecs.values()]),
                     vectors=np.stack([v for v, _ in vecs.values()]))
            result = self._layout(list(vecs), np.stack([v for v, _ in vecs.values()]))
            with self._lock:
                self.result, self._key, self.status = result, key, "ready"
        except Exception as e:
            with self._lock:
                self.status, self.error = "error", str(e)

    def _fetch_means(self, hashes, cache, total):
        client = get_db().client
        flt = None if len(hashes) == len(CATALOG.docs) else _hash_filter(hashes)
        sums, counts, offset, seen = {}, {}, None, 0
        while True:
            pts, offset = client.scroll(COLLECTION_NAME, scroll_filter=flt, limit=2000, offset=offset,
                                        with_payload=["metadata.file_hash"], with_vectors=True)
            for p in pts:
                h = p.payload["metadata"]["file_hash"]
                v = np.asarray(p.vector, dtype=np.float32)
                if h in sums:
                    sums[h] += v
                    counts[h] += 1
                else:
                    sums[h], counts[h] = v, 1
            seen += len(pts)
            self.progress = min(0.99, seen / max(total, 1))
            if offset is None:
                break
        for h in hashes:
            if h in sums:
                cache[h] = (sums[h] / counts[h], counts[h])

    def _layout(self, hashes, X):
        X = X / np.linalg.norm(X, axis=1, keepdims=True)
        # Mean embeddings share a large common direction; removing it spreads similarities out.
        Xc = X - X.mean(axis=0)
        Xc /= np.linalg.norm(Xc, axis=1, keepdims=True)
        S = Xc @ Xc.T
        np.fill_diagonal(S, -np.inf)
        k = min(self.K, len(hashes) - 1)
        nn = np.argpartition(-S, k, axis=1)[:, :k]
        edges = {}
        for i, row in enumerate(nn):
            for j in row:
                a, b = (i, int(j)) if i < j else (int(j), i)
                edges[(a, b)] = float(S[a, b])
        # Starting positions: top two principal components (the force layout refines them).
        _, _, vt = np.linalg.svd(Xc, full_matrices=False)
        P = Xc @ vt[:2].T
        P = (P - P.mean(axis=0)) / (P.std(axis=0) + 1e-9)
        return {
            "nodes": [{"hash": h, "x": round(float(x), 4), "y": round(float(y), 4)} for h, (x, y) in zip(hashes, P)],
            "edges": [[a, b, round(w, 4)] for (a, b), w in edges.items()],
        }


BOOK_MAP = BookMap()


def _doc_or_404(file_hash: str):
    d = CATALOG.get().docs.get(file_hash)
    if d is None:
        return None, JSONResponse({"error": "unknown document"}, status_code=404)
    return d, None


def _hash_filter(hashes):
    return models.Filter(must=[models.FieldCondition(
        key="metadata.file_hash", match=models.MatchAny(any=list(hashes)))])


def _folder_hashes(folder: str):
    if not folder:
        return None
    docs = CATALOG.get().docs.values()
    return [d["hash"] for d in docs if d["folder"] == folder or d["folder"].startswith(folder + "/")]


def _snippet(text: str, term: str = "", width: int = 360) -> str:
    text = " ".join(text.split())
    if term:
        i = text.lower().find(term.lower())
        if i != -1:
            start = max(0, i - width // 3)
            return ("…" if start else "") + text[start:start + width] + "…"
    return text[:width] + ("…" if len(text) > width else "")


def _hit(meta: dict, text: str, score=None, term: str = ""):
    return {"hash": meta.get("file_hash"), "page": meta.get("page_number"),
            "score": score, "snippet": _snippet(text, term)}


# ---------------------------------------------------------------- endpoints

async def api_catalog(request: Request):
    cat = CATALOG.get(force=request.query_params.get("refresh") == "1")
    return JSONResponse({
        "books_dir": BOOKS_DIR,
        "built_at": cat.built_at,
        "points": cat._points,
        "docs": list(cat.docs.values()),
        "unindexed": cat.unindexed,
    })


def api_search(request: Request):
    q = request.query_params.get("q", "").strip()
    mode = request.query_params.get("mode", "semantic")
    n = min(int(request.query_params.get("n", 40)), 200)
    if not q:
        return JSONResponse({"hits": []})
    hashes = _folder_hashes(request.query_params.get("folder", ""))
    if hashes == []:
        return JSONResponse({"hits": []})
    flt = _hash_filter(hashes) if hashes else None
    client = get_db().client

    if mode == "text":
        conds = [models.FieldCondition(key="text", match=models.MatchText(text=q))]
        if flt:
            conds += flt.must
        pts, _ = client.scroll(COLLECTION_NAME, scroll_filter=models.Filter(must=conds),
                               limit=n, with_payload=True, with_vectors=False)
        hits = [_hit(p.payload.get("metadata", {}), p.payload.get("text", ""), term=q) for p in pts]
    elif mode == "books":
        return JSONResponse({"books": _rank_books(client, q, flt, n)})
    else:
        vec = embed_query(q)
        res = client.search(COLLECTION_NAME, query_vector=vec, query_filter=flt,
                            limit=n, with_payload=True)
        hits = [_hit(r.payload.get("metadata", {}), r.payload.get("text", ""), r.score) for r in res]
    return JSONResponse({"hits": hits})


# Weights for a book's best passages: one strong page counts, but a book that
# returns to the topic across several passages ranks above a passing mention.
_BOOK_WEIGHTS = [1.0, 0.6, 0.4, 0.3, 0.2]
_BOOK_POOL = 600   # passages retrieved before grouping them by book


def _rank_books(client, q: str, flt, n: int):
    vec = embed_query(q)
    res = client.search(COLLECTION_NAME, query_vector=vec, query_filter=flt,
                        limit=_BOOK_POOL, with_payload=True)
    by_book = {}
    for r in res:  # already sorted by score
        meta = r.payload.get("metadata", {})
        by_book.setdefault(meta.get("file_hash"), []).append((r, meta))
    books = []
    for h, rs in by_book.items():
        top = [r.score for r, _ in rs[:len(_BOOK_WEIGHTS)]]
        score = sum(s * w for s, w in zip(top, _BOOK_WEIGHTS)) / sum(_BOOK_WEIGHTS)
        books.append({
            "hash": h, "score": score, "passages": len(rs),
            "pages": sorted({m.get("page_number") for _, m in rs if m.get("page_number") is not None}),
            "hits": [_hit(m, r.payload.get("text", ""), r.score) for r, m in rs[:3]],
        })
    books.sort(key=lambda b: -b["score"])
    return books[:n]


def api_map(request: Request):
    return JSONResponse(BOOK_MAP.get())


def api_doc(request: Request):
    d, err = _doc_or_404(request.path_params["hash"])
    if err:
        return err
    pts, _ = get_db().client.scroll(
        COLLECTION_NAME, scroll_filter=_hash_filter([d["hash"]]), limit=100000,
        with_payload=["metadata.page_number"], with_vectors=False)
    pages = sorted({p.payload["metadata"].get("page_number") for p in pts
                    if p.payload.get("metadata", {}).get("page_number") is not None})
    return JSONResponse({**d, "text_pages": pages})


def api_page(request: Request):
    d, err = _doc_or_404(request.path_params["hash"])
    if err:
        return err
    page = int(request.path_params["page"])
    flt = models.Filter(must=[
        models.FieldCondition(key="metadata.file_hash", match=models.MatchValue(value=d["hash"])),
        models.FieldCondition(key="metadata.page_number", match=models.MatchValue(value=page)),
    ])
    pts, _ = get_db().client.scroll(COLLECTION_NAME, scroll_filter=flt, limit=200,
                                    with_payload=True, with_vectors=False)
    pts.sort(key=lambda p: p.payload["metadata"].get("chunk_index", 0))
    return JSONResponse({"page": page, "chunks": [p.payload.get("text", "") for p in pts]})


def api_similar(request: Request):
    """Books whose chunks sit close to a sample of this book's chunks."""
    d, err = _doc_or_404(request.path_params["hash"])
    if err:
        return err
    client = get_db().client
    pts, _ = client.scroll(COLLECTION_NAME, scroll_filter=_hash_filter([d["hash"]]),
                           limit=100000, with_payload=False, with_vectors=False)
    ids = [p.id for p in pts]
    if not ids:
        return JSONResponse({"similar": []})
    step = max(1, len(ids) // 12)
    sample = ids[::step][:12]
    exclude = models.Filter(must_not=[models.FieldCondition(
        key="metadata.file_hash", match=models.MatchValue(value=d["hash"]))])
    scores = {}
    for pid in sample:
        for r in client.recommend(COLLECTION_NAME, positive=[pid], query_filter=exclude,
                                  limit=15, with_payload=["metadata.file_hash"]):
            h = r.payload["metadata"]["file_hash"]
            scores[h] = scores.get(h, 0) + r.score
    ranked = sorted(scores.items(), key=lambda kv: -kv[1])[:10]
    return JSONResponse({"similar": [{"hash": h, "score": s / len(sample)} for h, s in ranked]})


def api_cover(request: Request):
    d, err = _doc_or_404(request.path_params["hash"])
    if err or not d["exists"]:
        return Response(status_code=404)
    out = COVER_CACHE / f"{d['hash']}.jpg"
    if not out.exists():
        import fitz
        try:
            with fitz.open(d["path"]) as doc:
                page = doc[0]
                zoom = 260 / max(page.rect.width, 1)
                page.get_pixmap(matrix=fitz.Matrix(zoom, zoom)).save(str(out), jpg_quality=70)
        except Exception:
            return Response(status_code=404)
    return FileResponse(out, media_type="image/jpeg",
                        headers={"Cache-Control": "max-age=604800"})


def file_raw(request: Request):
    d, err = _doc_or_404(request.path_params["hash"])
    if err or not d["exists"]:
        return Response("File not found", status_code=404)
    media = "application/pdf" if d["ext"] == "pdf" else "application/epub+zip"
    return FileResponse(d["path"], media_type=media,
                        headers={"Content-Disposition": f'inline; filename="{d["hash"]}.{d["ext"]}"'})


async def api_open(request: Request):
    body = await request.json()
    d, err = _doc_or_404(body.get("hash", ""))
    if err:
        return err
    if not os.path.exists(d["path"]):
        return JSONResponse({"error": "file missing on disk"}, status_code=404)
    if body.get("reveal"):
        _reveal(d["path"])
    else:
        _open_in_viewer(d["path"], int(body.get("page") or 1))
    return JSONResponse({"ok": True})


SYSTEM = platform.system()   # "Linux", "Darwin" or "Windows"


def _open_default(path: str):
    """Open a file or folder with the system's default application."""
    if SYSTEM == "Windows":
        os.startfile(path)
    elif SYSTEM == "Darwin":
        subprocess.Popen(["open", path])
    else:
        subprocess.Popen(["xdg-open", path])


def _reveal(path: str):
    """Show the file in the system file manager, selected where the platform allows."""
    if SYSTEM == "Windows":
        subprocess.Popen(["explorer", "/select,", os.path.normpath(path)])
    elif SYSTEM == "Darwin":
        subprocess.Popen(["open", "-R", path])
    else:
        subprocess.Popen(["xdg-open", os.path.dirname(path)])


def _sumatra():
    found = shutil.which("SumatraPDF")
    if found:
        return found
    for base in (os.environ.get("LOCALAPPDATA"), os.environ.get("ProgramFiles"), os.environ.get("ProgramFiles(x86)")):
        if base and os.path.exists(os.path.join(base, "SumatraPDF", "SumatraPDF.exe")):
            return os.path.join(base, "SumatraPDF", "SumatraPDF.exe")
    return None


def _open_in_viewer(path: str, page: int):
    """Open a book in a desktop viewer, at `page` where the viewer supports it."""
    if path.lower().endswith(".epub"):
        if EPUB_VIEWER and shutil.which(EPUB_VIEWER):
            subprocess.Popen([EPUB_VIEWER, path])
        else:
            _open_default(path)
        return
    viewer = PDF_VIEWER if PDF_VIEWER and shutil.which(PDF_VIEWER) else None
    if SYSTEM == "Darwin":
        if viewer:
            subprocess.Popen(["open", "-a", viewer, path])
        else:
            script = (f'tell application "Preview" to open POSIX file "{path}"\n'
                      f'delay 1\n'
                      f'tell application "System Events" to tell process "Preview"\n'
                      f'  keystroke "g" using {{option down, command down}}\n'
                      f'  delay 0.3\n  keystroke "{page}"\n  key code 36\nend tell')
            subprocess.Popen(["osascript", "-e", script])
    elif SYSTEM == "Windows":
        sumatra = _sumatra()
        if sumatra:
            subprocess.Popen([sumatra, "-reuse-instance", "-page", str(page), path])
        else:
            os.startfile(path)
    elif viewer == "evince" or (not viewer and shutil.which("evince")):
        subprocess.Popen(["evince", f"--page-index={page - 1}", path])
    elif viewer == "okular" or (not viewer and shutil.which("okular")):
        subprocess.Popen(["okular", "--page", str(page), path])
    elif viewer == "zathura" or (not viewer and shutil.which("zathura")):
        subprocess.Popen(["zathura", "--page", str(page - 1), path])
    elif viewer:
        subprocess.Popen([viewer, path])
    else:
        _open_default(path)


def icon(request: Request):
    if JORGE_ICON and os.path.exists(os.path.expanduser(JORGE_ICON)):
        return FileResponse(os.path.expanduser(JORGE_ICON), headers={"Cache-Control": "no-cache"})
    return FileResponse(HERE / "jorge_static" / "jorge.png", headers={"Cache-Control": "no-cache"})


async def index(request: Request):
    return FileResponse(HERE / "jorge_static" / "index.html",
                        headers={"Cache-Control": "no-cache"})


app = Starlette(routes=[
    Route("/", index),
    Route("/icon", icon),
    Route("/api/catalog", api_catalog),
    Route("/api/search", api_search),
    Route("/api/map", api_map),
    Route("/api/doc/{hash}", api_doc),
    Route("/api/doc/{hash}/page/{page:int}", api_page),
    Route("/api/doc/{hash}/similar", api_similar),
    Route("/api/cover/{hash}", api_cover),
    Route("/api/open", api_open, methods=["POST"]),
    Route("/file/{hash}", file_raw),
    Mount("/static", StaticFiles(directory=HERE / "jorge_static"), name="static"),
])


# ---------------------------------------------------------------- launcher

def _chromium():
    """A Chromium-family browser that supports --app windows, or None."""
    if SYSTEM == "Windows":
        roots = [os.environ.get(k) for k in ("ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA")]
        rels = [r"Google\Chrome\Application\chrome.exe", r"Microsoft\Edge\Application\msedge.exe",
                r"BraveSoftware\Brave-Browser\Application\brave.exe", r"Chromium\Application\chrome.exe"]
        candidates = [os.path.join(r, rel) for rel in rels for r in roots if r]
    elif SYSTEM == "Darwin":
        apps = ["Google Chrome", "Microsoft Edge", "Brave Browser", "Chromium"]
        candidates = [os.path.join(base, f"{a}.app", "Contents", "MacOS", a)
                      for a in apps for base in ("/Applications", os.path.expanduser("~/Applications"))]
    else:
        candidates = [shutil.which(n) for n in ("google-chrome", "google-chrome-stable", "chromium",
                                                  "chromium-browser", "microsoft-edge", "brave-browser")]
    return next((c for c in candidates if c and os.path.exists(c)), None)


def open_window(url: str):
    """Open Jorge in its own app window if a Chromium browser is around, else a browser tab."""
    browser = _chromium()
    if browser:
        subprocess.Popen([browser, f"--app={url}"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    else:
        webbrowser.open(url)


def _is_up(url: str) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=1):
            return True
    except Exception:
        return False


def launch(port: int):
    """Start the server in the background unless it is already running, then open a window."""
    url = f"http://127.0.0.1:{port}/"
    if not _is_up(url):
        log = open(CACHE_DIR / "jorge.log", "a")
        python = sys.executable
        kwargs = {}
        if SYSTEM == "Windows":
            pythonw = os.path.join(os.path.dirname(python), "pythonw.exe")
            python = pythonw if os.path.exists(pythonw) else python
            kwargs["creationflags"] = (subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
                                       | subprocess.CREATE_NO_WINDOW)
        else:
            kwargs["start_new_session"] = True
        subprocess.Popen([python, str(Path(__file__).resolve()), "--port", str(port), "--no-open"],
                         stdout=log, stderr=log, stdin=subprocess.DEVNULL, cwd=str(HERE), **kwargs)
        for _ in range(60):
            if _is_up(url):
                break
            time.sleep(0.25)
    open_window(url)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", type=int, default=int(os.environ.get("JORGE_PORT", 8765)))
    ap.add_argument("--no-open", action="store_true", help="don't open a browser window")
    ap.add_argument("--launch", action="store_true",
                    help="start the server in the background if needed, open an app window and exit")
    args = ap.parse_args()
    if args.launch:
        return launch(args.port)
    url = f"http://127.0.0.1:{args.port}/"
    if not args.no_open:
        threading.Timer(1.0, lambda: open_window(url)).start()
    print(f"Jorge on {url}")
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
