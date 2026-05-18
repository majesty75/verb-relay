"""Loader for Lauterbach's t32api Python wrapper + shared library.

The official Python wrapper (`t32api.py`) ships inside any TRACE32 installation
under `demo/api/python/`. We import it dynamically so this repo does not vendor
licensed Lauterbach material.

Layout we look for:
    $T32SYS/demo/api/python/t32api.py
    $T32SYS/demo/api/python/<libname>          <-- shared lib

`<libname>` is platform-dependent:
    macOS  : libt32api.dylib  (also libt32api64.dylib)
    Linux  : libt32api64.so   (also libt32api.so)
    Windows: t32api64.dll     (also t32api.dll)

If T32SYS is unset we probe common defaults.
"""

from __future__ import annotations

import ctypes
import importlib.util
import os
import platform
import sys
from pathlib import Path
from typing import Any

DEFAULT_T32_PATHS = [
    "/Applications/t32",
    str(Path.home() / "t32"),
    "/opt/t32",
    "/usr/local/t32",
    "C:\\T32",
]


def _candidate_libnames() -> list[str]:
    sysname = platform.system()
    if sysname == "Darwin":
        return ["libt32api.dylib", "libt32api64.dylib"]
    if sysname == "Linux":
        return ["libt32api64.so", "libt32api.so"]
    if sysname == "Windows":
        return ["t32api64.dll", "t32api.dll"]
    raise RuntimeError(f"unsupported platform: {sysname}")


def _resolve_t32sys(explicit: str | None) -> Path:
    cands: list[str] = []
    if explicit:
        cands.append(explicit)
    env = os.environ.get("T32SYS")
    if env:
        cands.append(env)
    cands.extend(DEFAULT_T32_PATHS)
    for c in cands:
        p = Path(c).expanduser()
        if (p / "demo" / "api" / "python" / "t32api.py").exists():
            return p
    raise RuntimeError(
        "Could not locate a TRACE32 installation. Set the T32SYS env var to a "
        "directory containing demo/api/python/t32api.py "
        f"(tried: {cands})"
    )


def _resolve_lib(t32sys: Path) -> Path:
    api_dir = t32sys / "demo" / "api" / "python"
    for name in _candidate_libnames():
        p = api_dir / name
        if p.exists():
            return p
    # Fall back to T32's bin directory layout
    bin_dir = t32sys / "bin"
    for name in _candidate_libnames():
        for sub in bin_dir.glob("*"):
            p = sub / name
            if p.exists():
                return p
    raise RuntimeError(
        f"Could not find a t32api shared library under {api_dir} or {bin_dir}. "
        f"Looked for: {_candidate_libnames()}"
    )


_API_CACHE: dict[str, Any] = {}


def load_t32api(t32sys: str | None = None) -> Any:
    """Import the t32api module from a T32 install and bind the shared lib.

    Returns the loaded module. Cached per t32sys path.
    """
    key = t32sys or "(auto)"
    if key in _API_CACHE:
        return _API_CACHE[key]

    root = _resolve_t32sys(t32sys)
    py_path = root / "demo" / "api" / "python" / "t32api.py"
    lib_path = _resolve_lib(root)

    # The Lauterbach module expects to dlopen the lib relative to cwd or via
    # an env var depending on version, so set both and also preload it.
    os.environ.setdefault("T32SYS", str(root))
    ctypes.CDLL(str(lib_path))  # preload so the wrapper's CDLL succeeds

    spec = importlib.util.spec_from_file_location("t32api", py_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"could not load t32api from {py_path}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["t32api"] = mod
    spec.loader.exec_module(mod)
    _API_CACHE[key] = mod
    return mod
