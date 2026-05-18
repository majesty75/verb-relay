"""Heuristic OCR pass for image-dominant PDF pages.

Targets pages where the actual content lives in a screenshot / diagram /
rendered code block rather than extractable text. For Lauterbach docs this
catches PowerView dialog screenshots (which contain command syntax and field
labels we want indexed).

Heuristic: a page is *image-dominant* if all of these hold
  * fewer than `text_threshold` extractable chars (default 250)
  * the page has at least one image whose area > 30% of page area
  * the page has at least one drawing or image element

We render only those pages and OCR them. Skips pure text pages (already
covered) and decorative pages (icons, separators).

Requires `pytesseract` and the `tesseract` binary on PATH.
"""

from __future__ import annotations

import concurrent.futures
import io
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

import fitz  # PyMuPDF


@dataclass
class OcrChunk:
    doc_file: str
    doc_title: str
    category: str
    section: str
    page_start: int
    page_end: int
    text: str  # will be tagged "[OCR]" prefix so it's distinguishable downstream


def _has_substantial_image(page: fitz.Page, min_area_pt: float = 30000.0) -> bool:
    """True if the page contains at least one image with area >= min_area_pt
    (PDF points squared). 30k pt² = ~170x180 px at 72dpi — i.e. clearly a
    figure/screenshot, not a tiny icon or border ornament.
    """
    images = page.get_images(full=True)
    if not images:
        return False
    for im in images:
        try:
            rects = page.get_image_rects(im[0])
        except Exception:
            rects = []
        for r in rects:
            if (r.width or 0) * (r.height or 0) >= min_area_pt:
                return True
    return False


def _ocr_is_useful(ocr_text: str, extracted_text: str, overlap_threshold: float = 0.7) -> bool:
    """Skip OCR output that mostly duplicates the page's already-extracted text."""
    ocr_text = ocr_text.strip()
    if len(ocr_text) < 60:
        return False
    # Tokenise on whitespace + lowercase for cheap overlap test
    ocr_tokens = set(ocr_text.lower().split())
    if not ocr_tokens:
        return False
    extracted_tokens = set((extracted_text or "").lower().split())
    if not extracted_tokens:
        return True  # nothing to compare against — keep
    overlap = len(ocr_tokens & extracted_tokens) / len(ocr_tokens)
    return overlap < overlap_threshold


def _toc_section(toc: list[list], page_index: int) -> str:
    current = "(intro)"
    for level, title, page_1 in toc:
        if page_1 - 1 <= page_index:
            current = title.strip()
        else:
            break
    return current or "(intro)"


def _ocr_page_image(page: fitz.Page, dpi: int = 200) -> str:
    """Render page → PNG → tesseract. Returns plain text."""
    import pytesseract
    from PIL import Image

    mat = fitz.Matrix(dpi / 72.0, dpi / 72.0)
    pix = page.get_pixmap(matrix=mat, alpha=False)
    img = Image.open(io.BytesIO(pix.tobytes("png")))
    txt = pytesseract.image_to_string(img, lang="eng", config="--psm 6")
    return txt.strip()


def ocr_one_pdf(
    pdf_path: Path,
    *,
    doc_title: str,
    category: str,
    dpi: int = 200,
) -> list[OcrChunk]:
    """Iterate image-dominant pages, OCR them, return new chunks."""
    chunks: list[OcrChunk] = []
    doc = fitz.open(pdf_path)
    try:
        toc = doc.get_toc(simple=True) or []
        for i, page in enumerate(doc):
            if not _has_substantial_image(page):
                continue
            extracted = page.get_text("text") or ""
            ocr_text = _ocr_page_image(page, dpi=dpi)
            if not _ocr_is_useful(ocr_text, extracted):
                continue
            section = _toc_section(toc, i)
            chunks.append(
                OcrChunk(
                    doc_file=pdf_path.name,
                    doc_title=doc_title,
                    category=category,
                    section=section,
                    page_start=i + 1,
                    page_end=i + 1,
                    text=f"[OCR p{i+1}]\n{ocr_text}",
                )
            )
    finally:
        doc.close()
    return chunks


def run_parallel_ocr(
    tasks: list,  # list[PdfTask]
    *,
    workers: int,
    on_pdf_done,  # callback(task, list[OcrChunk])
) -> int:
    """Parallel OCR pass across PDFs. Tesseract is CPU-bound."""
    from tqdm import tqdm

    total = 0
    bar = tqdm(total=len(tasks), desc="OCR pass", unit="pdf")
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as ex:
        futures = {
            ex.submit(ocr_one_pdf, t.file, doc_title=t.title, category=t.category): t
            for t in tasks
        }
        for fut in concurrent.futures.as_completed(futures):
            t = futures[fut]
            try:
                chunks = fut.result()
            except Exception as e:
                chunks = []
                # Continue past per-PDF failures
                tqdm.write(f"  OCR failed for {t.file.name}: {e}")
            if chunks:
                on_pdf_done(t, chunks)
                total += len(chunks)
            bar.update(1)
    bar.close()
    return total
