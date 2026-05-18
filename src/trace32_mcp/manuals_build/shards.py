"""Shard scheme: split the curated/full manifest into multiple DBs.

Each shard stays small enough to ship directly in a GitHub repo (well under
the 100 MB single-file cap), and the runtime queries them concurrently.
"""

from __future__ import annotations

from dataclasses import dataclass

# Category-prefix → shard name. A PDF whose derived category (filename prefix
# before the first underscore) maps to a shard goes into that shard.
SHARD_MAP: dict[str, str] = {
    # core reference — always relevant
    "practice":   "reference",
    "api":        "reference",
    "general":    "reference",
    "general_ref":"reference",
    "ide":        "reference",
    "concepts":   "reference",
    "trace32":    "reference",
    "installation":"reference",
    # arch + per-CPU + app notes
    "debugger":   "archs",
    "simulator":  "archs",
    "app":        "archs",
    "nexus":      "archs",
    "trace":      "archs",
    "powerprobe": "archs",
    "powerintegrator": "archs",
    "serialtrace":"archs",
    # rtos
    "rtos":       "rtos",
    # training
    "training":   "training",
    # back-ends / misc
    "backend":    "misc",
    "int":        "misc",
    "boot":       "misc",
    "converter":  "misc",
    "flash":      "misc",
    "tools":      "misc",
    "uefi":       "misc",
    "tpu":        "misc",
    "windows":    "misc",
    "virtual":    "misc",
    "updates":    "misc",
    "support":    "misc",
    "release":    "misc",
    "directory":  "misc",
    "test":       "misc",
}


def shard_for_category(category: str) -> str:
    return SHARD_MAP.get(category, "misc")


def shard_for_filename(filename: str) -> str:
    stem = filename.removesuffix(".pdf")
    prefix = stem.split("_", 1)[0] if "_" in stem else stem
    return shard_for_category(prefix)


def known_shards() -> list[str]:
    return sorted(set(SHARD_MAP.values()))
