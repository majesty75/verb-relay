"""sqlite-vec backed storage for chunks + their embeddings.

Single-file DB so the MCP can ship it as one artifact. We keep chunk metadata
in a normal table and the vectors in a vec0 virtual table, joined by rowid.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterable

import numpy as np
import sqlite_vec

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS chunks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    doc_file TEXT NOT NULL,
    doc_title TEXT NOT NULL,
    category TEXT NOT NULL,
    section TEXT NOT NULL,
    page_start INTEGER NOT NULL,
    page_end INTEGER NOT NULL,
    text TEXT NOT NULL,
    UNIQUE (doc_file, page_start, page_end, section, text)
);

CREATE INDEX IF NOT EXISTS idx_chunks_doc ON chunks(doc_file);
CREATE INDEX IF NOT EXISTS idx_chunks_category ON chunks(category);
"""


def _vec_schema(dim: int) -> str:
    return f"CREATE VIRTUAL TABLE IF NOT EXISTS chunks_vec USING vec0(embedding float[{dim}]);"


@contextmanager
def open_db(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.enable_load_extension(True)
    sqlite_vec.load(conn)
    conn.enable_load_extension(False)
    try:
        yield conn
    finally:
        conn.close()


def init_db(path: Path, dim: int, model_name: str) -> None:
    with open_db(path) as conn:
        conn.executescript(SCHEMA)
        conn.execute(_vec_schema(dim))
        conn.execute(
            "INSERT OR REPLACE INTO meta(key, value) VALUES(?, ?)",
            ("embedding_dim", str(dim)),
        )
        conn.execute(
            "INSERT OR REPLACE INTO meta(key, value) VALUES(?, ?)",
            ("model_name", model_name),
        )
        conn.commit()


def insert_chunks(
    path: Path,
    rows: Iterable[dict],
    embeddings: np.ndarray,
) -> int:
    """Insert chunks + embeddings. Rows: dicts matching chunks columns (no id).

    Returns number of rows actually inserted (UNIQUE violations are silently skipped).
    """
    rows = list(rows)
    if not rows:
        return 0
    assert len(rows) == embeddings.shape[0]
    inserted = 0
    with open_db(path) as conn:
        for row, vec in zip(rows, embeddings):
            cur = conn.execute(
                """INSERT OR IGNORE INTO chunks
                   (doc_file, doc_title, category, section, page_start, page_end, text)
                   VALUES (:doc_file, :doc_title, :category, :section, :page_start, :page_end, :text)""",
                row,
            )
            if cur.rowcount == 0:
                continue
            chunk_id = cur.lastrowid
            conn.execute(
                "INSERT INTO chunks_vec(rowid, embedding) VALUES (?, ?)",
                (chunk_id, vec.astype(np.float32).tobytes()),
            )
            inserted += 1
        conn.commit()
    return inserted


def stats(path: Path) -> dict:
    if not path.exists():
        return {"db_exists": False, "path": str(path)}
    with open_db(path) as conn:
        n_chunks = conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
        n_docs = conn.execute("SELECT COUNT(DISTINCT doc_file) FROM chunks").fetchone()[0]
        by_cat = conn.execute(
            "SELECT category, COUNT(*) FROM chunks GROUP BY category ORDER BY 2 DESC"
        ).fetchall()
        meta = dict(conn.execute("SELECT key, value FROM meta").fetchall())
    return {
        "db_exists": True,
        "path": str(path),
        "size_bytes": path.stat().st_size,
        "chunks": n_chunks,
        "documents": n_docs,
        "by_category": dict(by_cat),
        "meta": meta,
    }


def search(
    path: Path,
    query_vec: np.ndarray,
    k: int,
    doc_filter: list[str] | None = None,
    category_filter: list[str] | None = None,
) -> list[dict]:
    """Return top-k chunks by cosine similarity (sqlite-vec uses L2 on normalized vectors).

    `doc_filter` matches against `doc_file`; `category_filter` against `category`.
    Both are exact-match lists.
    """
    with open_db(path) as conn:
        # Vec search returns rowid + distance; join chunks for the rest.
        rows = conn.execute(
            """
            SELECT c.id, c.doc_file, c.doc_title, c.category, c.section,
                   c.page_start, c.page_end, c.text, v.distance
            FROM chunks_vec v
            JOIN chunks c ON c.id = v.rowid
            WHERE v.embedding MATCH ? AND k = ?
            ORDER BY v.distance
            """,
            (query_vec.astype(np.float32).tobytes(), max(k * 4, 20)),
        ).fetchall()

    out: list[dict] = []
    for r in rows:
        rec = {
            "id": r[0],
            "doc_file": r[1],
            "doc_title": r[2],
            "category": r[3],
            "section": r[4],
            "page_start": r[5],
            "page_end": r[6],
            "text": r[7],
            "distance": float(r[8]),
        }
        if doc_filter and rec["doc_file"] not in doc_filter:
            continue
        if category_filter and rec["category"] not in category_filter:
            continue
        out.append(rec)
        if len(out) >= k:
            break
    return out
