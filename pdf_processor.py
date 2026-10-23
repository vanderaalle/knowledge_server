"""
PDF processing: text extraction, chunking, OCR fallback.
Used by server.py directly and by worker subprocesses via _process_single_pdf.
"""

import os
import sys
import re
import hashlib
from pathlib import Path
from typing import List, Dict, Any, Tuple
from dataclasses import dataclass
from concurrent.futures import ThreadPoolExecutor

try:
    import fitz  # PyMuPDF
except ImportError:
    import subprocess
    subprocess.check_call([sys.executable, "-m", "pip", "install", "pymupdf"])
    import fitz

try:
    import ebooklib
    from ebooklib import epub
    from bs4 import BeautifulSoup
    EPUB_SUPPORT = True
except ImportError:
    EPUB_SUPPORT = False

fitz.TOOLS.mupdf_display_errors(False)

from pypdf import PdfReader
from pdf2image import convert_from_path
import pytesseract

# OCR Configuration
TESSERACT_CMD = os.getenv('TESSERACT_CMD', '/opt/homebrew/bin/tesseract')
_poppler_default = '/opt/homebrew/bin'
POPPLER_PATH = os.getenv('POPPLER_PATH', _poppler_default if os.path.exists(_poppler_default) else None)
MAX_WORKERS = int(os.getenv('MAX_WORKERS', os.cpu_count() or 4))
# Pages OCR'd at once. Each runs its own tesseract process, so threads suffice.
OCR_THREADS = int(os.getenv('OCR_THREADS', max(1, (os.cpu_count() or 4) // 2)))

if os.path.exists(TESSERACT_CMD):
    pytesseract.pytesseract.tesseract_cmd = TESSERACT_CMD


@dataclass
class TextChunk:
    """Chunk di testo semplificato - metadati essenziali"""
    text: str
    chunk_index: int
    page_number: int
    document_title: str
    total_pages: int
    source: str
    source_path: str
    file_hash: str

    def to_metadata(self) -> Dict[str, Any]:
        return {
            "chunk_index": self.chunk_index,
            "page_number": self.page_number,
            "document_title": self.document_title,
            "total_pages": self.total_pages,
            "source": self.source,
            "source_path": self.source_path,
            "file_hash": self.file_hash,
        }


class SimpleTextSplitter:
    """Text splitter semplice e veloce"""
    def __init__(self, chunk_size: int = 1000, chunk_overlap: int = 200):
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap
        self.separators = ["\n\n", "\n", ". ", "! ", "? ", " ", ""]

    def split_text(self, text: str) -> List[str]:
        return self._split(text, self.separators)

    def _split(self, text: str, separators: List[str]) -> List[str]:
        """Split on the coarsest separator present, recursing into pieces that are
        still too long with the finer separators. Every returned chunk is a
        contiguous substring of the input (chunk_text relies on this)."""
        separator, finer = "", []
        for i, sep in enumerate(separators):
            if sep == "" or sep in text:
                separator, finer = sep, separators[i + 1:]
                break

        splits = text.split(separator) if separator else list(text)
        final_chunks, good = [], []
        for split in splits:
            if len(split) <= self.chunk_size:
                good.append(split)
                continue
            if good:
                final_chunks.extend(self._merge(good, separator))
                good = []
            if finer:
                final_chunks.extend(self._split(split, finer))
            else:
                final_chunks.append(split)
        if good:
            final_chunks.extend(self._merge(good, separator))
        return [c for c in final_chunks if c.strip()]

    def _merge(self, splits: List[str], separator: str) -> List[str]:
        """Greedily join short pieces into chunks of at most chunk_size, carrying
        up to chunk_overlap characters of trailing pieces into the next chunk."""
        chunks, current, length = [], [], 0
        for split in splits:
            split_len = len(split)
            if current and length + split_len + len(separator) > self.chunk_size:
                chunks.append(separator.join(current))
                while current and (length > self.chunk_overlap or
                                   length + split_len + len(separator) > self.chunk_size):
                    length -= len(current[0]) + len(separator)
                    current.pop(0)
            current.append(split)
            length += split_len + len(separator)
        if current:
            chunks.append(separator.join(current))
        return chunks


class PDFProcessor:
    """Processore PDF - Versione Ottimizzata con PyMuPDF"""

    def __init__(self):
        self.text_splitter = SimpleTextSplitter(chunk_size=800, chunk_overlap=150)

    def _ocr_page(self, pdf_path: str, page_number: int, dpi: int) -> str:
        """Render one page (1-based) and OCR it; only that page is held in memory."""
        images = convert_from_path(pdf_path, dpi=dpi, first_page=page_number,
                                   last_page=page_number, poppler_path=POPPLER_PATH)
        return pytesseract.image_to_string(images[0]) if images else ""

    def _ocr_pages(self, pdf_path: str, page_numbers: List[int], dpi: int) -> Dict[int, str]:
        """OCR several pages in parallel; a page that fails yields ""."""
        def ocr(n):
            try:
                return self._ocr_page(pdf_path, n, dpi)
            except Exception:
                return ""
        with ThreadPoolExecutor(max_workers=OCR_THREADS) as pool:
            return dict(zip(page_numbers, pool.map(ocr, page_numbers)))

    def _ocr_all_pages(self, pdf_path: str) -> tuple:
        """OCR every page, ignoring any embedded text layer."""
        total = len(PdfReader(pdf_path).pages)
        texts = self._ocr_pages(pdf_path, list(range(1, total + 1)), dpi=300)
        page_texts = [texts[n] for n in range(1, total + 1)]
        return "\n".join(page_texts) + "\n", page_texts, len(page_texts), True

    def extract_text_from_pdf(self, pdf_path: str, use_ocr: bool = False, force_ocr: bool = False) -> tuple:
        """
        force_ocr: OCR every page even if a text layer exists. Use for PDFs whose
        text layer is garbled (broken font encodings), which the normal fallback
        never catches because the extracted text is non-empty.
        """
        try:
            if force_ocr:
                return self._ocr_all_pages(pdf_path)

            doc = fitz.open(pdf_path)
            total_pages = len(doc)
            page_texts = []
            full_text = ""
            used_ocr = False

            for content in doc:
                text = content.get_text()
                page_texts.append(text)
                full_text += text + "\n"
            doc.close()

            if not full_text.strip() and use_ocr:
                try:
                    reader = PdfReader(pdf_path)
                    page_texts = [page.extract_text() or "" for page in reader.pages]
                    blank = [i + 1 for i, t in enumerate(page_texts) if not t.strip()]
                    if blank:
                        # pdf2image's default resolution (200 dpi), as before.
                        for n, text in self._ocr_pages(pdf_path, blank, dpi=200).items():
                            page_texts[n - 1] = text
                        used_ocr = True
                    full_text = "".join(t + "\n" for t in page_texts)
                except Exception:
                    pass

            return full_text, page_texts, total_pages, used_ocr

        except Exception:
            return "", [], 0, False

    def extract_document_title(self, pdf_path: str, first_page_text: str) -> str:
        try:
            doc = fitz.open(pdf_path)
            title = doc.metadata.get('title', '')
            doc.close()
            if title:
                return str(title)
        except:
            pass

        lines = first_page_text.strip().split('\n')
        for line in lines[:5]:
            line = line.strip()
            if line and len(line) > 3 and len(line) < 200:
                if not re.match(r'^[\d\s\.]+$', line):
                    return line

        return Path(pdf_path).stem.replace('_', ' ').replace('-', ' ').title()

    def extract_text_from_epub(self, epub_path: str) -> tuple:
        """Extract text from an epub file. Returns (full_text, page_texts, total_pages, used_ocr)."""
        if not EPUB_SUPPORT:
            return "", [], 0, False
        try:
            book = epub.read_epub(epub_path, options={"ignore_ncx": True})
            chapters = [
                item for item in book.get_items()
                if item.get_type() == ebooklib.ITEM_DOCUMENT
            ]
            page_texts = []
            full_text = ""
            for chapter in chapters:
                soup = BeautifulSoup(chapter.get_body_content(), "html.parser")
                text = soup.get_text(separator="\n", strip=True)
                if text.strip():
                    page_texts.append(text)
                    full_text += text + "\n\n"
            return full_text, page_texts, len(page_texts), False
        except Exception:
            return "", [], 0, False

    def chunk_text(self, text: str, page_texts: List[str], total_pages: int,
                   base_metadata: Dict[str, Any]) -> List[TextChunk]:
        chunks = self.text_splitter.split_text(text)
        chunk_objects = []

        # Locate each page in the joined text (PDF pages are joined with "\n",
        # epub chapters with "\n\n"), so the mapping holds for both.
        page_boundaries = []
        char_count = 0
        for page_text in page_texts:
            start = text.find(page_text, char_count) if page_text else -1
            if start == -1:
                start = char_count
            page_boundaries.append((start, start + len(page_text)))
            char_count = start + len(page_text)

        document_title = base_metadata.get('document_title', 'Unknown')
        text_pos = 0

        for i, chunk_text in enumerate(chunks):
            chunk_start = text.find(chunk_text, text_pos)
            page_number = 1
            if chunk_start != -1:
                text_pos = chunk_start + 1
                for page_idx, (start, end) in enumerate(page_boundaries):
                    if chunk_start >= start and chunk_start < end:
                        page_number = page_idx + 1
                        break

            chunk_obj = TextChunk(
                text=chunk_text,
                chunk_index=i,
                page_number=page_number,
                document_title=document_title,
                total_pages=total_pages,
                source=base_metadata.get('source', ''),
                source_path=base_metadata.get('source_path', ''),
                file_hash=base_metadata.get('file_hash', '')
            )
            chunk_objects.append(chunk_obj)

        return chunk_objects


def _suppress_stderr():
    """Redirect fd 2 to /dev/null to silence MuPDF C-level noise."""
    devnull_fd = os.open(os.devnull, os.O_WRONLY)
    os.dup2(devnull_fd, 2)
    os.close(devnull_fd)


def _worker_init():
    _suppress_stderr()


# Module-level singleton used by _process_single_pdf in worker processes
pdf_processor = PDFProcessor()


def _calculate_file_hash(file_path: Path) -> str:
    """Calcola hash MD5 file (veloce)"""
    hash_md5 = hashlib.md5()
    try:
        with open(file_path, "rb") as f:
            for chunk in iter(lambda: f.read(8192), b""):
                hash_md5.update(chunk)
        return hash_md5.hexdigest()
    except Exception:
        return ""


def _process_single_pdf(args):
    """
    Executed in ProcessPool workers.
    Extracts text and chunks a single PDF — no DB or Ollama calls here.
    Returns a dict ready for embedding in the main thread.
    """
    file_path, directory_path, use_ocr, indexed_hashes, empty_hashes, force_ocr_hashes = args
    result = {
        "status": "error",
        "file_path": str(file_path),
        "file_hash": "",
        "chunks": [],
        "skip_reason": None
    }

    try:
        current_hash = _calculate_file_hash(file_path)
        result["file_hash"] = current_hash
        if current_hash in indexed_hashes:
            result["status"] = "already_indexed"
            return result
        if current_hash in empty_hashes:
            result["status"] = "already_indexed"
            return result

        force_ocr = current_hash in force_ocr_hashes
        if file_path.suffix.lower() == ".epub":
            text, page_texts, total_pages, used_ocr = pdf_processor.extract_text_from_epub(str(file_path))
        else:
            text, page_texts, total_pages, used_ocr = pdf_processor.extract_text_from_pdf(
                str(file_path), use_ocr=use_ocr, force_ocr=force_ocr
            )

        result["used_ocr"] = used_ocr
        if used_ocr and not use_ocr and not force_ocr:
            result["status"] = "skipped_ocr"
            return result

        if not text.strip():
            result["status"] = "error_no_text"
            return result

        document_title = pdf_processor.extract_document_title(str(file_path), text)
        relative_path = os.path.relpath(file_path, directory_path)

        base_metadata = {
            "source": relative_path,
            "source_path": str(file_path),
            "file_hash": current_hash,
            "document_title": document_title
        }

        chunks = pdf_processor.chunk_text(text, page_texts, total_pages, base_metadata)

        result["chunks"] = chunks
        result["status"] = "ok"
        return result

    except Exception as e:
        result["error_message"] = str(e)
        return result
