"""Runtime config for the manuals search.

DB resolution order:
  1. `T32_MANUALS_DB` env var — explicit override (single path, or comma/colon-separated for shards)
  2. ~/.cache/trace32-mcp/manuals_*.db    → user-installed shards (override-without-reinstall)
  3. ~/.cache/trace32-mcp/manuals.db      → legacy single-file user cache
  4. trace32_mcp/db/manuals_*.db          → bundled with the wheel (package data)
  5. <repo>/db/manuals_*.db               → dev source-checkout fallback
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_MODEL = "BAAI/bge-base-en-v1.5"


def user_cache_dir() -> Path:
    base = os.environ.get("XDG_CACHE_HOME")
    if base:
        return Path(base) / "trace32-mcp"
    return Path.home() / ".cache" / "trace32-mcp"


def _split_paths(spec: str) -> list[Path]:
    sep = "," if "," in spec else (";" if ";" in spec else ":")
    return [Path(p).expanduser() for p in spec.split(sep) if p.strip()]


def discover_db_paths() -> list[Path]:
    """Return the ordered list of DB shards the runtime should query.

    May return an empty list if nothing is cached yet — the caller is expected
    to trigger an auto-download.
    """
    explicit = os.environ.get("T32_MANUALS_DB")
    if explicit:
        paths = _split_paths(explicit)
        return [p for p in paths if p.exists()]

    # 1. User cache (overrides bundled DB so users can update without reinstalling)
    cache = user_cache_dir()
    shards = sorted(cache.glob("manuals_*.db"))
    if shards:
        return shards
    legacy = cache / "manuals.db"
    if legacy.exists():
        return [legacy]

    # 2. Bundled with the wheel: trace32_mcp/db/manuals_*.db
    here = Path(__file__).resolve()
    bundled_dir = here.parent.parent / "db"
    if bundled_dir.exists():
        bundled_shards = sorted(bundled_dir.glob("manuals_*.db"))
        if bundled_shards:
            return bundled_shards
        bundled_legacy = bundled_dir / "manuals.db"
        if bundled_legacy.exists():
            return [bundled_legacy]

    # 3. Dev fallback for editable installs from a source checkout
    repo_root = here.parents[3]  # src/trace32_mcp/manuals/config.py → repo root
    candidate = repo_root / "db"
    if candidate.exists():
        cs = sorted(candidate.glob("manuals_*.db"))
        if cs:
            return cs
        single = candidate / "manuals.db"
        if single.exists():
            return [single]

    return []


def default_download_target() -> Path:
    """Where a single auto-downloaded DB lands when no shards exist."""
    return user_cache_dir() / "manuals.db"


@dataclass(frozen=True)
class ManualsSettings:
    db_paths: list[Path] = field(default_factory=list)
    model_name: str = DEFAULT_MODEL
    device: str = "auto"
    batch_size: int = 32
    parallel_search: bool = True

    @property
    def primary_db(self) -> Path | None:
        return self.db_paths[0] if self.db_paths else None


def load_settings() -> ManualsSettings:
    return ManualsSettings(
        db_paths=discover_db_paths(),
        model_name=os.environ.get("T32_MANUALS_MODEL", DEFAULT_MODEL),
        device=os.environ.get("T32_MANUALS_DEVICE", "auto"),
        batch_size=int(os.environ.get("T32_MANUALS_BATCH", "32")),
        parallel_search=os.environ.get("T32_MANUALS_PARALLEL", "1") != "0",
    )


def resolve_device(device: str) -> str:
    if device != "auto":
        return device
    try:
        import torch
        if torch.backends.mps.is_available():
            return "mps"
        if torch.cuda.is_available():
            return "cuda"
    except Exception:
        pass
    return "cpu"
