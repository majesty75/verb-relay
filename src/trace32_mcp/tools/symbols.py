"""Symbol listing + typed variable view."""

from __future__ import annotations

from pydantic import Field

from ._common import TargetSelector, resolve_target


class ListSymbolsInput(TargetSelector):
    pattern: str = Field(default="*", description="Glob pattern (e.g. 'main', 'foo*'). Default '*' lists all functions.")
    limit: int = Field(default=200, ge=1, le=5000, description="Max output lines.")


class VarViewInput(TargetSelector):
    name: str = Field(description="Variable name (global or local). Structs/arrays return a formatted view.")


def t32_list_symbols(args: dict) -> dict:
    """Symbol listing via PYRCL's symbol service + AREA fallback.

    For an exact name we hit `dbg.symbol.query_by_name` (fast, structured).
    For globs we fall back to the PRACTICE `sYmbol.List.Function` command
    and parse the AREA capture (works but slower).
    """
    p = ListSymbolsInput(**args)
    _inst, client = resolve_target(p)

    # If the pattern is an exact name (no wildcards), use the fast structured API.
    if p.pattern and not any(c in p.pattern for c in "*?["):
        sym = client.symbol_query(p.pattern)
        if sym is None:
            return {"ok": True, "pattern": p.pattern, "count": 0, "symbols": [],
                    "note": "no symbol with that exact name; try a glob like 'foo*' for fuzzy lookup"}
        return {"ok": True, "pattern": p.pattern, "count": 1, "symbols": [sym]}

    # Glob: fall back to the AREA-parsing path.
    syms = client.symbol_list(p.pattern, limit=p.limit)
    if syms and isinstance(syms[0], dict) and "_error" in syms[0]:
        return {"ok": False, "pattern": p.pattern, "error": syms[0]["_error"]}
    return {"ok": True, "pattern": p.pattern, "count": len(syms), "symbols": syms}


def t32_var_view(args: dict) -> dict:
    p = VarViewInput(**args)
    _inst, client = resolve_target(p)
    # Var.View opens a window; for inline output use Var.PRINT/Var.STRing.
    # We try the fnc path first (cheap, structured) then fall back to a
    # Var.PRINT scraped from AREA.
    val = client.eval_practice(f"Var.VALUE({p.name})")
    if val.get("ok") and "value" in val:
        return {"ok": True, "name": p.name, "value": val["value"], "method": val.get("method")}
    # Fallback: Var.PRINT into AREA so we can capture
    res = client.run(f"Var.PRINT {p.name}").to_dict()
    return {"ok": res["ok"], "name": p.name, "result": res}
