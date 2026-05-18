"""Parallel pipeline: PDF parse (thread pool) → MPS embed (single) → DB write.

Designed for Apple Silicon: PDF parsing releases the GIL in MuPDF's C core, so
threads give real parallelism. The embedding model loads once on MPS and is
fed by the main thread. Worker count is capped so we don't peg every P-core
(thermal headroom matters on sustained runs).
"""

from __future__ import annotations

import concurrent.futures
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable

from tqdm import tqdm

from .ingest import iter_pdf_chunks


def safe_worker_count(default: int = 6) -> int:
    """Pick a worker count that leaves headroom for the embedder + OS."""
    n_cpu = os.cpu_count() or 4
    # Heuristic: 60% of cores, min 2, max 8
    auto = max(2, min(8, int(n_cpu * 0.6)))
    return int(os.environ.get("T32_RAG_WORKERS", str(default if default else auto)))


@dataclass
class PdfTask:
    file: Path
    title: str
    category: str


def _parse_one(task: PdfTask, chunk_chars: int, overlap: int) -> tuple[PdfTask, list[dict]]:
    rows: list[dict] = []
    for ch in iter_pdf_chunks(
        task.file,
        doc_title=task.title,
        category=task.category,
        chunk_chars=chunk_chars,
        overlap=overlap,
    ):
        rows.append({
            "doc_file": ch.doc_file,
            "doc_title": ch.doc_title,
            "category": ch.category,
            "section": ch.section,
            "page_start": ch.page_start,
            "page_end": ch.page_end,
            "text": ch.text,
        })
    return task, rows


def run_parallel_ingest(
    tasks: list[PdfTask],
    chunk_chars: int,
    overlap: int,
    workers: int,
    embedder,  # Embedder
    insert_fn: Callable[[PdfTask, list[dict]], int],  # batched DB write
    embed_batch: int = 32,
) -> int:
    """Run PDF parsing in a thread pool, embed + insert in the main thread.

    `insert_fn(task, rows_with_embeddings_already_attached)` receives one
    completed PDF's worth of work. It's called serially so DB writes don't
    contend across shards.
    """
    total_chunks = 0
    bar = tqdm(total=len(tasks), desc="PDFs (parallel)", unit="pdf")
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as ex:
        futures = [ex.submit(_parse_one, t, chunk_chars, overlap) for t in tasks]
        for fut in concurrent.futures.as_completed(futures):
            task, rows = fut.result()
            if rows:
                texts = [r["text"] for r in rows]
                # Embed in micro-batches to keep MPS memory bounded
                vecs = embedder.encode_passages(texts)
                inserted = insert_fn(task, rows, vecs)
                total_chunks += inserted
            bar.update(1)
    bar.close()
    return total_chunks
