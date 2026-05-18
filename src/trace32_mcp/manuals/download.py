"""One-time download of manuals.db from a GitHub Release.

Default release URL is a placeholder until the repo is published; the user can
always override via the T32_MANUALS_DB_URL env var (most useful when hosting
internally on a corporate Artifactory / S3 bucket / etc).
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

DEFAULT_URL = os.environ.get(
    "T32_MANUALS_DB_URL",
    # Placeholder — replace with the real GitHub Release URL after publishing.
    "https://github.com/REPLACE_ME/Trace32-MCP/releases/latest/download/manuals.db.zst",
)


def ensure_db(target: Path, url: str = DEFAULT_URL) -> Path:
    """Make sure `target` exists; download + decompress if not.

    Raises FileNotFoundError with a clear message if download is impossible
    (placeholder URL still in place, no network, etc).
    """
    if target.exists():
        return target

    if "REPLACE_ME" in url:
        raise FileNotFoundError(
            f"manuals DB not found at {target} and no release URL configured.\n"
            "Either set T32_MANUALS_DB_URL to a downloadable .db (.zst optional), "
            "or T32_MANUALS_DB to a local DB path, or build one locally with "
            "`pip install trace32-mcp[build]` and `t32-rag ingest`."
        )

    target.parent.mkdir(parents=True, exist_ok=True)
    print(f"[trace32-mcp] downloading manuals DB from {url} ...", file=sys.stderr)

    # Use urllib to avoid a hard requests dep at runtime
    import urllib.request

    suffix = ".zst" if url.endswith(".zst") else ".db"
    with tempfile.NamedTemporaryFile("wb", suffix=suffix, delete=False) as tmp:
        tmp_path = Path(tmp.name)
        with urllib.request.urlopen(url) as resp:
            total = int(resp.headers.get("Content-Length", "0"))
            read = 0
            chunk = 1024 * 256
            while True:
                buf = resp.read(chunk)
                if not buf:
                    break
                tmp.write(buf)
                read += len(buf)
                if total:
                    pct = 100.0 * read / total
                    print(f"\r[trace32-mcp]   {read/1e6:6.1f} / {total/1e6:6.1f} MB ({pct:5.1f}%)",
                          end="", file=sys.stderr)
            print("", file=sys.stderr)

    if suffix == ".zst":
        try:
            import zstandard as zstd
        except ImportError:
            raise RuntimeError(
                "Downloaded a .zst archive but zstandard is not installed. "
                "pip install zstandard"
            )
        dctx = zstd.ZstdDecompressor()
        with open(tmp_path, "rb") as fin, open(target, "wb") as fout:
            dctx.copy_stream(fin, fout)
        tmp_path.unlink(missing_ok=True)
    else:
        tmp_path.rename(target)

    print(f"[trace32-mcp] DB ready at {target} ({target.stat().st_size/1e6:.1f} MB)", file=sys.stderr)
    return target
