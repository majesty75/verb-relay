from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from ..manuals.config import DEFAULT_MODEL, resolve_device, user_cache_dir


@dataclass(frozen=True)
class BuildSettings:
    raw_dir: Path
    db_path: Path
    model_name: str = DEFAULT_MODEL
    chunk_chars: int = 1800
    chunk_overlap: int = 200
    device: str = "auto"
    batch_size: int = 32


def load_build_settings() -> BuildSettings:
    raw = os.environ.get("T32_RAG_RAW_DIR")
    db = os.environ.get("T32_MANUALS_DB") or os.environ.get("T32_RAG_DB_PATH")
    default_raw = Path.cwd() / "data" / "raw" / "pdf"
    default_db = user_cache_dir() / "manuals.db"
    return BuildSettings(
        raw_dir=Path(raw or default_raw),
        db_path=Path(db or default_db),
        model_name=os.environ.get("T32_RAG_MODEL", DEFAULT_MODEL),
        chunk_chars=int(os.environ.get("T32_RAG_CHUNK_CHARS", "1800")),
        chunk_overlap=int(os.environ.get("T32_RAG_CHUNK_OVERLAP", "200")),
        device=os.environ.get("T32_RAG_DEVICE", "auto"),
        batch_size=int(os.environ.get("T32_RAG_BATCH", "32")),
    )


__all__ = ["BuildSettings", "load_build_settings", "resolve_device"]
