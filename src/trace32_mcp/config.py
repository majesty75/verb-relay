from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class T32Config:
    """Defaults for the TRACE32 RCL connection.

    Per-call overrides take precedence over these defaults so a single MCP
    process can talk to multiple instances (e.g. local sim + remote PowerDebug).
    """

    host: str = "127.0.0.1"
    port: int = 20000
    node_name: str = "T32"
    packet_length: int = 1024
    # Path to a T32 installation that contains demo/api/python/ and the
    # libt32api shared library. If unset we search common locations.
    t32sys: str | None = None
    # Sibling repo containing the manuals sqlite-vec DB.
    manuals_db_path: Path | None = None


def load_config() -> T32Config:
    # manuals_db_path here is informational only — actual discovery is done by
    # trace32_mcp.manuals.config.discover_db_paths() which handles shards.
    explicit_db = os.environ.get("T32_MANUALS_DB")
    return T32Config(
        host=os.environ.get("T32_HOST", "127.0.0.1"),
        port=int(os.environ.get("T32_PORT", "20000")),
        node_name=os.environ.get("T32_NODE_NAME", "T32"),
        packet_length=int(os.environ.get("T32_PACKET_LEN", "1024")),
        t32sys=os.environ.get("T32SYS"),
        manuals_db_path=Path(explicit_db) if explicit_db else None,
    )
