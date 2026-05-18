"""Symbol listing + typed variable view."""

from __future__ import annotations

from pydantic import Field

from ._common import TargetSelector, resolve_target


class ListSymbolsInput(TargetSelector):
    pattern: str = Field(default="*", description="Glob pattern. Default '*' lists all symbols (large output).")
    limit: int = Field(default=200, ge=1, le=5000, description="Max output lines.")


class VarViewInput(TargetSelector):
    name: str = Field(description="Variable name (global or local). Structs/arrays return formatted view.")


def t32_list_symbols(args: dict) -> dict:
    p = ListSymbolsInput(**args)
    _inst, client = resolve_target(p)
    cmd = f"sYmbol.LIST.Function {p.pattern}" if p.pattern not in {"*", ""} else "sYmbol.LIST.Function"
    res = client.run(cmd).to_dict()
    text = res.get("text") or ""
    lines = text.splitlines()
    if len(lines) > p.limit:
        text = "\n".join(lines[: p.limit]) + f"\n... ({len(lines) - p.limit} more lines truncated)"
        res = {**res, "text": text}
    return {"ok": res["ok"], "cmd": cmd, "matches": len(lines), "result": res}


def t32_var_view(args: dict) -> dict:
    p = VarViewInput(**args)
    _inst, client = resolve_target(p)
    cmd = f"Var.View %ALL {p.name}"
    res = client.run(cmd).to_dict()
    return {"ok": res["ok"], "name": p.name, "cmd": cmd, "result": res}
