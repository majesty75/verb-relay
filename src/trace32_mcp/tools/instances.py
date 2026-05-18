"""Tools that manage T32 instance lifecycle (spawn / list / shutdown / log)."""

from __future__ import annotations

from pydantic import BaseModel, Field

from ..session import all_instances, ensure_instance, shutdown_instance
from ..t32_process import registry, supported_arches


class SpawnInput(BaseModel):
    arch: str = Field(
        default="arm",
        description=f"CPU family. One of: {supported_arches()}",
    )
    port: int | None = Field(default=None, description="Pin a specific port. Default: pick a free one.")
    node_name: str | None = Field(default=None, description="Friendly node id. Default: T32_<ARCH>_<port>.")
    t32sys: str | None = Field(default=None, description="Override $T32SYS for this spawn.")
    headless: bool = Field(default=False, description="Hint at no-display mode (Linux only).")


class ShutdownInput(BaseModel):
    node_name: str = Field(description="Instance node_name returned by t32_spawn / t32_list_instances.")
    force: bool = Field(default=False, description="SIGKILL if SIGTERM doesn't take effect within 5s.")


class ListInstancesInput(BaseModel):
    """No arguments."""


class GetLogInput(BaseModel):
    node_name: str | None = Field(default=None, description="Which instance to query (default: most recent).")
    lines: int = Field(default=120, ge=1, le=2000, description="Number of trailing lines to return.")
    source: str = Field(
        default="auto",
        description="'process' (subprocess stdout/stderr) | 'area' (T32 MCPLOG window) | 'auto' (both).",
    )


def t32_spawn(args: dict) -> dict:
    p = SpawnInput(**args)
    inst, _client = ensure_instance(
        port=p.port,
        node_name=p.node_name,
        arch=p.arch,
        t32sys=p.t32sys,
        auto_spawn=True,
        headless=p.headless,
    )
    return {"ok": True, "instance": inst.to_dict()}


def t32_list_instances(_args: dict) -> dict:
    return {"ok": True, "instances": all_instances()}


def t32_shutdown(args: dict) -> dict:
    p = ShutdownInput(**args)
    return shutdown_instance(p.node_name, force=p.force)


def t32_get_log(args: dict) -> dict:
    p = GetLogInput(**args)
    reg = registry()
    if p.node_name:
        inst = reg.get_by_node(p.node_name)
        if inst is None:
            return {"ok": False, "error": f"no instance {p.node_name!r}"}
    else:
        lst = reg.list()
        if not lst:
            return {"ok": False, "error": "no tracked instances"}
        inst = lst[-1]

    out: dict = {"ok": True, "node_name": inst.node_name, "sources": {}}
    if p.source in ("process", "auto"):
        out["sources"]["process"] = (
            inst.tail_log(p.lines) if inst.spawned_by_us else "(external instance — no process log)"
        )
    if p.source in ("area", "auto"):
        try:
            from ..session import get_client
            client = get_client(host=inst.host, port=inst.port, node_name=inst.node_name)
            out["sources"]["area_MCPLOG"] = client.read_area_log("MCPLOG", lines=p.lines)
        except Exception as e:
            out["sources"]["area_MCPLOG"] = f"(failed to read AREA: {e})"
    return out
