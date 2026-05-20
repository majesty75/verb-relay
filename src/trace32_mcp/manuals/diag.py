"""Standalone self-test for the manuals search path.

Run this OUTSIDE Claude Code to find out exactly where `t32_search_manuals`
hangs:

    trace32-mcp-selftest

It walks the search pipeline one stage at a time, printing each step (with
timing) BEFORE doing it, so the last line you see is the stage that's stuck.
It also arms faulthandler to dump the full thread stack every --dump-after
seconds, so even an unattended hang leaves a traceback pointing at the exact
blocking call (e.g. a Hugging Face network probe vs. a torch import).
"""

from __future__ import annotations

import argparse
import faulthandler
import sys
import time


def _stage(msg: str) -> None:
    print(f"[selftest] {msg}", file=sys.stderr, flush=True)


def selftest(query: str = "hardware breakpoint cortex-m7", k: int = 3,
             dump_after: float = 30.0) -> int:
    faulthandler.enable()
    # If any stage blocks longer than dump_after, print every thread's stack
    # to stderr (repeating) — this is what reveals a hung network call.
    if dump_after > 0:
        faulthandler.dump_traceback_later(dump_after, repeat=True, file=sys.stderr)

    t_total = time.time()
    try:
        _stage("1/7 loading settings + discovering DB shards ...")
        from .config import load_settings
        s = load_settings()
        _stage(f"      DBs found: {[str(p) for p in s.db_paths] or 'NONE'}")
        _stage(f"      model={s.model_name!r} device={s.device}")

        _stage("2/7 importing torch (first import can be slow) ...")
        t = time.time(); import torch  # noqa: F401
        _stage(f"      torch {torch.__version__} in {time.time()-t:.1f}s")

        _stage("3/7 importing sentence_transformers ...")
        t = time.time(); import sentence_transformers as st  # noqa: F401
        _stage(f"      sentence-transformers {st.__version__} in {time.time()-t:.1f}s")

        _stage("4/7 checking model cache (local only, no network) ...")
        from .prefetch import is_model_cached
        cached = is_model_cached(s.model_name)
        _stage(f"      cached={cached}  (cached -> loads offline)")

        _stage("5/7 loading the embedding model "
               "(THIS is where a corporate-network hang usually happens) ...")
        t = time.time()
        from .search import _embedder
        emb = _embedder(s)
        _stage(f"      model loaded in {time.time()-t:.1f}s, dim={emb.dim}")

        _stage("6/7 encoding the query ...")
        t = time.time(); _ = emb.encode_query(query)
        _stage(f"      encoded in {time.time()-t:.1f}s")

        _stage("7/7 running the full vector search ...")
        t = time.time()
        from .search import search_manuals
        hits = search_manuals(query, k=k)
        _stage(f"      search in {time.time()-t:.1f}s, {len(hits)} hits:")
        for h in hits:
            _stage(f"        - {h['doc_file']} p{h.get('page_start')} :: {h.get('section')}")

        if dump_after > 0:
            faulthandler.cancel_dump_traceback_later()
        _stage(f"DONE — total {time.time()-t_total:.1f}s. search path is healthy.")
        return 0
    except KeyboardInterrupt:
        _stage("INTERRUPTED — the stage printed just above is where it was stuck.")
        return 130
    except Exception as e:
        import traceback
        _stage(f"FAILED at the stage above: {type(e).__name__}: {e}")
        traceback.print_exc()
        return 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="trace32-mcp-selftest",
        description="Diagnose where t32_search_manuals hangs (stage-by-stage).",
    )
    ap.add_argument("--query", default="hardware breakpoint cortex-m7")
    ap.add_argument("--k", type=int, default=3)
    ap.add_argument("--dump-after", type=float, default=30.0,
                    help="Dump all thread stacks if a stage blocks this many "
                         "seconds (0 to disable). Default 30.")
    args = ap.parse_args(argv)
    return selftest(args.query, k=args.k, dump_after=args.dump_after)


if __name__ == "__main__":
    raise SystemExit(main())
