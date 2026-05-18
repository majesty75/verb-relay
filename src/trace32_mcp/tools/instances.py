"""Tools that manage T32 instance lifecycle (spawn / list / shutdown / log / render_config)."""

from __future__ import annotations

from pydantic import BaseModel, Field

from ..session import all_instances, ensure_instance, shutdown_instance
from ..t32_process import registry, render_config_t32, supported_arches


class SpawnInput(BaseModel):
    arch: str = Field(
        default="arm",
        description=f"CPU family. One of: {supported_arches()}",
    )
    port: int | None = Field(default=None, description="Pin a specific port. Default: pick a free one.")
    node_name: str | None = Field(default=None, description="Friendly node id. Default: T32_<ARCH>_<port>.")
    t32sys: str | None = Field(default=None, description="Override $T32SYS for this spawn.")
    headless: bool = Field(default=False, description="Hint at no-display mode (Linux only).")
    extra_config: str | None = Field(
        default=None,
        description=(
            "Optional extra config.t32 lines appended after the standard "
            "OS/PBI/RCL/SCREEN sections. Use this for `SYStem.CPU <name>`-"
            "style preconfig, license file paths, or custom AREAs. Sections "
            "in TRACE32 config are separated by blank lines."
        ),
    )
    timeout_seconds: float = Field(
        default=45.0, ge=5.0, le=300.0,
        description="How long to wait for the RCL port to open after spawn. Bump on slow Windows boxes.",
    )


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


class RenderConfigInput(BaseModel):
    port: int = Field(default=20000, description="Port to bake into the RCL section.")
    node_name: str = Field(default="T32", description="Node id used in ID= and HEADER=.")
    extra_config: str | None = Field(default=None, description="Optional extra lines appended after standard sections.")


def t32_spawn(args: dict) -> dict:
    p = SpawnInput(**args)
    try:
        inst, _client = ensure_instance(
            port=p.port,
            node_name=p.node_name,
            arch=p.arch,
            t32sys=p.t32sys,
            auto_spawn=True,
            headless=p.headless,
            extra_config=p.extra_config,
            timeout_seconds=p.timeout_seconds,
        )
    except Exception as e:
        return {"ok": False, "error": str(e), "error_type": type(e).__name__}
    return {"ok": True, "instance": inst.to_dict()}


def t32_list_instances(_args: dict) -> dict:
    return {"ok": True, "instances": all_instances()}


def t32_shutdown(args: dict) -> dict:
    p = ShutdownInput(**args)
    return shutdown_instance(p.node_name, force=p.force)


def t32_render_config(args: dict) -> dict:
    """Dry-run: return the literal config.t32 that t32_spawn would write,
    without actually spawning. Use this when RCL fails to bind and you need
    to inspect or debug the planned config before launching TRACE32."""
    p = RenderConfigInput(**args)
    return {
        "ok": True,
        "config_t32": render_config_t32(
            port=p.port, node=p.node_name, extra_config=p.extra_config,
        ),
        "note": (
            "This is what t32_spawn would write to config.t32. TRACE32 reads it "
            "at startup; if RCL doesn't bind the port, double-check the "
            "PBI / RCL section layout against your TRACE32 build's docs and "
            "consider passing `extra_config` to t32_spawn with a `SYStem.CPU` "
            "line for the exact CPU you're targeting."
        ),
    }


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
