"""PDF → structured chunks.

PyMuPDF is fast and preserves page numbers + outline (TOC). We use the TOC to
slice each PDF into sections and then split long sections into overlapping
character windows. Each chunk records source file, page range, and section
heading so the MCP can cite the manual precisely.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

import fitz  # PyMuPDF


@dataclass
class Chunk:
    doc_file: str          # e.g. "practice_ref.pdf"
    doc_title: str         # human-friendly title from manifest
    category: str          # e.g. "practice"
    section: str           # nearest heading from TOC
    page_start: int        # 1-indexed
    page_end: int
    text: str

    def fingerprint(self) -> str:
        return f"{self.doc_file}#p{self.page_start}-{self.page_end}#{self.section[:60]}"


_WS_RE = re.compile(r"[ \t]+")
_NEWLINES_RE = re.compile(r"\n{3,}")


def _clean(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = _WS_RE.sub(" ", text)
    text = _NEWLINES_RE.sub("\n\n", text)
    return text.strip()


def _toc_section_for_page(toc: list[list], page_index: int) -> str:
    """Walk the TOC and return the most-specific heading covering page_index (0-indexed).

    PyMuPDF TOC entries are [level, title, page_1indexed].
    """
    current = "(intro)"
    for level, title, page_1 in toc:
        if page_1 - 1 <= page_index:
            current = title.strip()
        else:
            break
    return current or "(intro)"


def _window(text: str, size: int, overlap: int) -> Iterator[str]:
    if len(text) <= size:
        yield text
        return
    step = max(1, size - overlap)
    i = 0
    while i < len(text):
        end = min(len(text), i + size)
        # Prefer to break on paragraph boundary near `end`
        if end < len(text):
            break_at = text.rfind("\n\n", i, end)
            if break_at != -1 and break_at - i > size // 2:
                end = break_at
        yield text[i:end].strip()
        if end >= len(text):
            return
        i = end - overlap


def iter_pdf_chunks(
    pdf_path: Path,
    *,
    doc_title: str,
    category: str,
    chunk_chars: int,
    overlap: int,
) -> Iterator[Chunk]:
    """Yield Chunks from one PDF, section-aware where possible.

    Strategy:
    1. Extract per-page text + map each page to its nearest TOC section.
    2. Concatenate consecutive pages that share the same section into one block.
    3. Window-split each block into chunks of ~chunk_chars with overlap.
    """
    doc = fitz.open(pdf_path)
    try:
        toc = doc.get_toc(simple=True) or []
        pages_text: list[tuple[int, str, str]] = []  # (page_1, section, text)
        for i, page in enumerate(doc):
            section = _toc_section_for_page(toc, i)
            text = _clean(page.get_text("text"))
            if text:
                pages_text.append((i + 1, section, text))
        if not pages_text:
            return

        # Group consecutive pages with the same section
        groups: list[dict] = []
        for page_no, section, text in pages_text:
            if groups and groups[-1]["section"] == section:
                groups[-1]["pages"].append(page_no)
                groups[-1]["text"] += "\n\n" + text
            else:
                groups.append({"section": section, "pages": [page_no], "text": text})

        for grp in groups:
            for window_text in _window(grp["text"], chunk_chars, overlap):
                if len(window_text) < 80:
                    # Skip near-empty windows (page numbers / running headers only)
                    continue
                yield Chunk(
                    doc_file=pdf_path.name,
                    doc_title=doc_title,
                    category=category,
                    section=grp["section"],
                    page_start=grp["pages"][0],
                    page_end=grp["pages"][-1],
                    text=window_text,
                )
    finally:
        doc.close()
