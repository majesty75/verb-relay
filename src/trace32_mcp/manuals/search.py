"""High-level search API used by the MCP server.

Supports sharded DBs: each shard is queried in parallel, results merged by
distance, top-k returned. Per-shard `doc_filter` / `category_filter` is also
applied. Falls back to auto-download if no shards are present.
"""

from __future__ import annotations

import concurrent.futures
import logging
import os
import sqlite3
from pathlib import Path
from typing import Optional

from .config import ManualsSettings, default_download_target, load_settings
from .store import open_db, search as vec_search

log = logging.getLogger("trace32-mcp.manuals")

_EMBEDDER = None  # type: ignore[var-annotated]


def _make_embedder(settings: ManualsSettings):
    """Pick the embedding backend.

    Default: the vendored ONNX model (onnxruntime + tokenizers, no torch, no
    network) — this is what ships in the wheel and works air-gapped. If no
    ONNX model is present (e.g. a dev source checkout that hasn't run
    scripts/build_onnx_model.py), fall back to the sentence-transformers /
    torch path, loading strictly offline when the model is already cached so a
    locked-down network can't hang the load on a HF revision-check.

    Override with T32_MANUALS_BACKEND=onnx|torch.
    """
    backend = os.environ.get("T32_MANUALS_BACKEND", "auto").lower()

    if backend in ("auto", "onnx"):
        try:
            from .onnx_embed import OnnxEmbedder, onnx_model_available
            if backend == "onnx" or onnx_model_available():
                emb = OnnxEmbedder(batch_size=settings.batch_size)
                log.info("manuals: using vendored ONNX embedder (dim=%d, no torch)", emb.dim)
                return emb
        except Exception as e:
            if backend == "onnx":
                raise
            log.warning("ONNX embedder unavailable (%s) — falling back to sentence-transformers", e)

    # sentence-transformers / torch fallback
    from .embed import Embedder
    cached = False
    try:
        from .prefetch import is_model_cached
        cached = is_model_cached(settings.model_name)
    except Exception:
        cached = False
    if cached:
        os.environ.setdefault("HF_HUB_OFFLINE", "1")
        os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
    else:
        log.warning(
            "embedding model %r not cached and no vendored ONNX model — first "
            "search will download it (~400-450 MB) from the Hugging Face Hub. "
            "Run `trace32-mcp-prefetch`, or ship the ONNX model "
            "(scripts/build_onnx_model.py).",
            settings.model_name,
        )
    log.info("manuals: using sentence-transformers/torch embedder")
    return Embedder(
        settings.model_name, device=settings.device,
        batch_size=settings.batch_size, local_files_only=cached,
    )


def _embedder(settings: ManualsSettings):
    global _EMBEDDER
    if _EMBEDDER is None:
        _EMBEDDER = _make_embedder(settings)
    return _EMBEDDER


def _ensure_dbs(settings: ManualsSettings) -> list[Path]:
    if settings.db_paths:
        return settings.db_paths
    # No DB cached — try auto-download
    from . import download
    target = default_download_target()
    download.ensure_db(target)
    # Re-resolve (download may have produced shards if URL was a manifest)
    return load_settings().db_paths or [target]


def search_manuals(
    query: str,
    k: int = 6,
    doc_filter: list[str] | None = None,
    category_filter: list[str] | None = None,
    settings: ManualsSettings | None = None,
) -> list[dict]:
    s = settings or load_settings()
    dbs = _ensure_dbs(s)
    if not dbs:
        raise FileNotFoundError("no manuals DB available — set T32_MANUALS_DB or build one")

    emb = _embedder(s)
    qvec = emb.encode_query(query)

    # Each shard returns its own top-k; we merge and keep the global top-k.
    per_shard_k = max(k * 2, 10)

    def _query_one(db_path: Path) -> list[dict]:
        results = vec_search(
            db_path, qvec, k=per_shard_k,
            doc_filter=doc_filter, category_filter=category_filter,
        )
        for r in results:
            r["shard"] = db_path.name
        return results

    all_hits: list[dict] = []
    if len(dbs) == 1 or not s.parallel_search:
        for db in dbs:
            all_hits.extend(_query_one(db))
    else:
        with concurrent.futures.ThreadPoolExecutor(max_workers=min(len(dbs), 4)) as ex:
            for shard_hits in ex.map(_query_one, dbs):
                all_hits.extend(shard_hits)

    # Smaller distance = more similar (sqlite-vec returns L2 on normalised vecs)
    all_hits.sort(key=lambda r: r["distance"])
    return all_hits[:k]


def lookup_command(
    command: str,
    settings: ManualsSettings | None = None,
) -> list[dict]:
    """Direct alphabetical-reference lookup across all shards."""
    s = settings or load_settings()
    dbs = _ensure_dbs(s)
    if not dbs:
        raise FileNotFoundError("no manuals DB available")
    pat = f"%{command}%"
    rows: list[dict] = []
    for db in dbs:
        with open_db(db) as conn:
            conn.row_factory = sqlite3.Row
            for r in conn.execute(
                """
                SELECT doc_file, doc_title, category, section, page_start, page_end,
                       substr(text, 1, 1200) AS preview
                FROM chunks
                WHERE category IN ('general_ref', 'general')
                  AND (section LIKE ? OR text LIKE ?)
                ORDER BY
                  CASE WHEN section LIKE ? THEN 0 ELSE 1 END,
                  page_start
                LIMIT 12
                """,
                (pat, pat, pat),
            ).fetchall():
                d = dict(r)
                d["shard"] = db.name
                rows.append(d)
    # Deduplicate by (doc_file, page_start, section) across shards
    seen: set[tuple] = set()
    out: list[dict] = []
    for r in rows:
        key = (r["doc_file"], r["page_start"], r["section"])
        if key in seen:
            continue
        seen.add(key)
        out.append(r)
    return out[:12]
