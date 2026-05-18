"""`t32-rag` CLI: ingest PDFs (parallel + optional OCR), inspect DB stats, query.

Available only when installed with the [build] extra.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import click

from ..manuals.embed import Embedder
from ..manuals.store import init_db, insert_chunks, stats
from .config import load_build_settings, resolve_device
from .manifest import CURATED_MANIFEST
from .parallel import PdfTask, run_parallel_ingest, safe_worker_count
from .shards import known_shards, shard_for_filename


@click.group()
def main() -> None:
    """TRACE32 manuals RAG build tool."""


@main.command()
@click.option("--raw-dir", type=click.Path(path_type=Path), default=None,
              help="Directory containing the extracted help.zip PDFs")
@click.option("--db-path", type=click.Path(path_type=Path), default=None,
              help="Where to write the sqlite-vec DB")
@click.option("--manifest", "manifest_path", type=click.Path(path_type=Path), default=None,
              help="Optional JSON manifest override (list of {file,category,title})")
@click.option("--full", is_flag=True, help="Ignore manifest, ingest every PDF in raw-dir")
@click.option("--shards", is_flag=True,
              help="Split into multiple sharded DBs (each <100MB, ships directly in repo)")
@click.option("--shard-dir", type=click.Path(path_type=Path), default=None,
              help="When --shards is set, where to write the per-shard DBs")
@click.option("--chunk-chars", type=int, default=None,
              help="Override chunk size (default 800 here for finer retrieval)")
@click.option("--overlap", type=int, default=None, help="Override chunk overlap (default 100)")
@click.option("--workers", type=int, default=0,
              help="PDF parser threads. 0 = auto (60%% of CPU cores, capped). Set lower for less thermal load.")
@click.option("--ocr/--no-ocr", default=False,
              help="Also OCR image-dominant pages (Tesseract). Captures screenshot content.")
@click.option("--ocr-workers", type=int, default=0,
              help="OCR worker threads (CPU-bound). 0 = same as --workers")
def ingest(raw_dir, db_path, manifest_path, full, shards, shard_dir,
           chunk_chars, overlap, workers, ocr, ocr_workers) -> None:
    """Parse PDFs, embed in parallel, and optionally OCR image-dominant pages."""
    settings = load_build_settings()
    raw_dir = raw_dir or settings.raw_dir
    db_path = db_path or settings.db_path
    # Default to 800/100 — finer retrieval than the legacy 1800/200
    eff_chunk = chunk_chars if chunk_chars is not None else 800
    eff_overlap = overlap if overlap is not None else 100
    eff_workers = workers if workers > 0 else safe_worker_count()
    eff_ocr_workers = ocr_workers if ocr_workers > 0 else eff_workers

    if not raw_dir.exists():
        raise click.ClickException(
            f"raw_dir {raw_dir} does not exist. Extract help.zip into it first."
        )

    if full:
        def _cat_for(name: str) -> str:
            stem = name.removesuffix(".pdf")
            return stem.split("_", 1)[0] if "_" in stem else stem
        manifest = [
            {"file": p.name, "category": _cat_for(p.name), "title": p.stem.replace("_", " ").title()}
            for p in sorted(raw_dir.glob("*.pdf"))
        ]
    elif manifest_path:
        manifest = json.loads(manifest_path.read_text())
    else:
        manifest = CURATED_MANIFEST

    missing = [m["file"] for m in manifest if not (raw_dir / m["file"]).exists()]
    if missing:
        click.echo(f"WARN: {len(missing)} manifest files missing: {missing[:5]}...", err=True)
        manifest = [m for m in manifest if (raw_dir / m["file"]).exists()]

    click.echo(f"Loading embedding model {settings.model_name} on {resolve_device(settings.device)}...")
    emb = Embedder(settings.model_name, device=settings.device, batch_size=settings.batch_size)
    click.echo(f"Workers: {eff_workers} parse / {eff_ocr_workers} ocr  |  chunks={eff_chunk}/{eff_overlap}  |  OCR={'on' if ocr else 'off'}")

    # Decide target DBs (single vs sharded)
    if shards:
        out_dir = shard_dir or db_path.parent
        out_dir.mkdir(parents=True, exist_ok=True)
        targets: dict[str, Path] = {name: out_dir / f"manuals_{name}.db" for name in known_shards()}
        for p in targets.values():
            if p.exists():
                p.unlink()
            init_db(p, emb.dim, settings.model_name)
        click.echo(f"Sharded DBs in {out_dir}")
    else:
        init_db(db_path, emb.dim, settings.model_name)
        targets = {"_single": db_path}
        click.echo(f"Single DB at {db_path}")

    # ------------------------------------------------------------------
    # Phase 1: parallel parse + embed + insert
    # ------------------------------------------------------------------
    tasks = [
        PdfTask(file=raw_dir / m["file"], title=m["title"], category=m["category"])
        for m in manifest
    ]

    totals: dict[str, int] = {k: 0 for k in targets}

    def _insert(task: PdfTask, rows, vecs) -> int:
        target_key = shard_for_filename(task.file.name) if shards else "_single"
        target_db = targets.get(target_key) or targets["_single"]
        ins = insert_chunks(target_db, rows, vecs)
        totals[target_key] = totals.get(target_key, 0) + ins
        return ins

    text_total = run_parallel_ingest(
        tasks, eff_chunk, eff_overlap, eff_workers, emb, _insert,
    )
    click.echo(f"Phase 1 (text): inserted {text_total} chunks")

    # ------------------------------------------------------------------
    # Phase 2: OCR pass (optional)
    # ------------------------------------------------------------------
    if ocr:
        from .ocr import run_parallel_ocr

        ocr_total = 0

        def _ocr_done(task: PdfTask, ocr_chunks) -> None:
            nonlocal ocr_total
            if not ocr_chunks:
                return
            rows = [
                {
                    "doc_file": c.doc_file,
                    "doc_title": c.doc_title,
                    "category": c.category,
                    "section": c.section,
                    "page_start": c.page_start,
                    "page_end": c.page_end,
                    "text": c.text,
                }
                for c in ocr_chunks
            ]
            texts = [r["text"] for r in rows]
            vecs = emb.encode_passages(texts)
            ocr_total += _insert(task, rows, vecs)

        n = run_parallel_ocr(tasks, workers=eff_ocr_workers, on_pdf_done=_ocr_done)
        click.echo(f"Phase 2 (OCR): inserted {ocr_total} chunks from {n} OCR fragments")

    click.echo(f"\nDone. Inserted by target: {totals}")
    for tk, p in targets.items():
        click.echo(f"--- {p.name} ---")
        click.echo(json.dumps(stats(p), indent=2, default=str))


@main.command("stats")
@click.option("--db-path", type=click.Path(path_type=Path), default=None)
def stats_cmd(db_path) -> None:
    s = load_build_settings()
    path = db_path or s.db_path
    click.echo(json.dumps(stats(path), indent=2, default=str))


@main.command()
@click.argument("query")
@click.option("-k", "topk", default=6, show_default=True)
@click.option("--doc", "doc_filter", multiple=True)
@click.option("--category", "cat_filter", multiple=True)
def query(query, topk, doc_filter, cat_filter) -> None:
    from ..manuals.search import search_manuals
    results = search_manuals(
        query, k=topk,
        doc_filter=list(doc_filter) or None,
        category_filter=list(cat_filter) or None,
    )
    for r in results:
        click.echo("=" * 80)
        click.echo(f"[{r.get('shard','?')}/{r['doc_file']} p{r['page_start']}-{r['page_end']}] {r['section']}  (d={r['distance']:.4f})")
        click.echo(r["text"][:600] + ("..." if len(r["text"]) > 600 else ""))


@main.command("lookup")
@click.argument("command")
def lookup_cmd(command) -> None:
    from ..manuals.search import lookup_command
    for r in lookup_command(command):
        click.echo("=" * 80)
        click.echo(f"[{r.get('shard','?')}/{r['doc_file']} p{r['page_start']}-{r['page_end']}] {r['section']}")
        click.echo(r["preview"])


if __name__ == "__main__":
    main()
