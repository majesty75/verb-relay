"""Pre-download the embedding model so the first MCP search isn't a silent hang.

The vector DB ships inside the wheel, but the sentence-transformers embedding
model (default BAAI/bge-base-en-v1.5, ~420 MB) is fetched from the Hugging Face
Hub on first use and cached under ~/.cache/huggingface. On a fresh machine
(especially a locked-down corporate Windows laptop) that first fetch is what
makes `t32_search_manuals` appear to "keep loading".

Run this once, in a terminal, to download it with a visible progress bar:

    trace32-mcp-prefetch

After it completes the model is cached and every later search is fast and
fully offline. Set HF_HUB_OFFLINE=1 afterwards if your network blocks the Hub.
"""

from __future__ import annotations

import argparse
import os
import sys
import time

from .config import DEFAULT_MODEL, load_settings, resolve_device


def is_model_cached(model_name: str) -> bool:
    """Best-effort check whether the model is already in the HF cache.

    Avoids a network round-trip when we can prove it's local. Returns False if
    we can't tell (caller will just attempt a normal load, which is a no-op
    when fully cached).
    """
    try:
        from huggingface_hub import scan_cache_dir
    except Exception:
        return False
    needle = model_name.replace("/", "--").lower()
    try:
        for repo in scan_cache_dir().repos:
            if needle in repo.repo_id.replace("/", "--").lower():
                # A config.json revision being present is enough to skip.
                return repo.size_on_disk > 0
    except Exception:
        return False
    return False


def ensure_model(model_name: str | None = None, *, force: bool = False,
                 device: str = "auto") -> dict:
    """Make sure the embedding model is downloaded. Shows HF progress on stderr.

    Returns a small dict describing what happened (cached/downloaded, device,
    elapsed seconds) so callers / the CLI can report it.
    """
    name = model_name or load_settings().model_name or DEFAULT_MODEL

    # If a vendored ONNX model is bundled, the runtime never touches the Hugging
    # Face Hub or torch — there is nothing to prefetch.
    try:
        from .onnx_embed import onnx_model_available, model_dir
        if onnx_model_available() and os.environ.get("T32_MANUALS_BACKEND", "auto").lower() != "torch":
            print(f"[trace32-mcp] vendored ONNX model present ({model_dir()}); "
                  "no download needed (runtime uses onnxruntime, not torch).",
                  file=sys.stderr, flush=True)
            return {"ok": True, "model": name, "status": "onnx_bundled", "elapsed_s": 0.0}
    except Exception:
        pass

    # Make sure HF progress bars are visible — never suppress them here, this
    # is the one place the user explicitly wants to watch the download.
    os.environ.pop("HF_HUB_DISABLE_PROGRESS_BARS", None)

    already = is_model_cached(name)
    if already and not force:
        return {"ok": True, "model": name, "status": "already_cached", "elapsed_s": 0.0}

    # We are about to (re)download. If the user has pinned offline mode (e.g.
    # in the MCP server env, or via `setx HF_HUB_OFFLINE 1`), clear it for this
    # process so the fetch can actually reach the Hub — otherwise --force is a
    # no-op against a corrupt cache.
    for var in ("HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE"):
        if os.environ.pop(var, None):
            print(f"[trace32-mcp] {var} was set — clearing it so the download can proceed.",
                  file=sys.stderr, flush=True)

    resolved = resolve_device(device)
    print(
        f"[trace32-mcp] downloading embedding model {name!r} "
        f"(first run only, ~400-450 MB) → device={resolved} ...",
        file=sys.stderr, flush=True,
    )
    t0 = time.time()
    # Import here so the heavy torch/sentence-transformers import only happens
    # when the user actually asks to prefetch.
    from sentence_transformers import SentenceTransformer

    model = SentenceTransformer(name, device=resolved)
    # Touch the model so weights are actually materialised, not just metadata.
    _ = model.get_sentence_embedding_dimension()
    elapsed = time.time() - t0
    print(
        f"[trace32-mcp] model ready ({elapsed:.0f}s). Cached for all future runs.",
        file=sys.stderr, flush=True,
    )
    return {"ok": True, "model": name, "status": "downloaded", "device": resolved,
            "elapsed_s": round(elapsed, 1)}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="trace32-mcp-prefetch",
        description="Download the TRACE32-manuals embedding model once, with progress.",
    )
    ap.add_argument("--model", default=None,
                    help=f"Override model name (default: {DEFAULT_MODEL} or $T32_MANUALS_MODEL).")
    ap.add_argument("--device", default="auto", help="auto|cpu|cuda|mps (default: auto).")
    ap.add_argument("--force", action="store_true",
                    help="Re-load even if it looks cached.")
    args = ap.parse_args(argv)
    try:
        info = ensure_model(args.model, force=args.force, device=args.device)
    except Exception as e:  # pragma: no cover - surfaced to the terminal
        print(f"[trace32-mcp] prefetch FAILED: {type(e).__name__}: {e}", file=sys.stderr)
        print(
            "  If your network blocks huggingface.co, set HF_ENDPOINT to an "
            "internal mirror, or copy the model cache from another machine and "
            "set HF_HUB_OFFLINE=1.",
            file=sys.stderr,
        )
        return 1
    if info["status"] == "already_cached":
        print(f"[trace32-mcp] model {info['model']!r} already cached — nothing to do.",
              file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
