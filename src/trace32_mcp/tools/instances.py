"""Tools that manage T32 instance lifecycle (spawn / list / shutdown / log / render_config)."""

from __future__ import annotations

from pydantic import BaseModel, Field

from ..presets import get_preset, list_presets
from ..session import all_instances, ensure_instance, shutdown_instance
from ..t32_process import SUPPORTED_BACKENDS, registry, render_config_t32, supported_arches


class SpawnInput(BaseModel):
    arch: str = Field(
        default="arm",
        description=f"CPU family — picks the binary (t32marm/t32mppc/...). One of: {supported_arches()}",
    )
    backend: str = Field(
        default="sim",
        description=(
            f"Runtime backend, picks the PBI section. One of: {list(SUPPORTED_BACKENDS)}. "
            "'sim' = instruction-set simulator (no hardware). "
            "'usb' = PowerDebug attached via USB on this host. "
            "'net' = PowerDebug attached via Ethernet (provide target_host). "
            "'usb_proxy' = PowerDebug on a remote host running t32tcpusb (provide target_host). "
            "'custom' = supply the entire PBI section via extra_config."
        ),
    )
    target_host: str | None = Field(
        default=None,
        description=(
            "For backend='net': PowerDebug device name or IP (becomes NODE=). "
            "For backend='usb_proxy': proxy machine IP (becomes PROXYNAME=). "
            "Not used by 'sim' or 'usb'."
        ),
    )
    target_node: str | None = Field(
        default=None,
        description=(
            "Optional PowerDebug device disambiguation name (NODE=) for 'usb' / 'usb_proxy' "
            "when multiple PowerDebugs are connected. Default: TRACE32 picks first found."
        ),
    )
    proxy_port: int = Field(
        default=8866, ge=1, le=65535,
        description="For backend='usb_proxy': t32tcpusb port on the proxy host (default 8866).",
    )
    port: int | None = Field(default=None, description="RCL port to bind. Default: pick a free one.")
    node_name: str | None = Field(default=None, description="Friendly node id. Default: T32_<ARCH>_<port>.")
    t32sys: str | None = Field(default=None, description="Override $T32SYS for this spawn.")
    headless: bool = Field(default=False, description="Hint at no-display mode (Linux only).")
    extra_config: str | None = Field(
        default=None,
        description=(
            "Optional extra **config.t32** lines appended after the standard sections. "
            "ONLY for legitimate config.t32 directives (e.g. PRINTER=, custom SCREEN= "
            "settings, license paths, additional sections). DO NOT use this for "
            "PRACTICE commands like `SYStem.CPU` — those are runtime commands and "
            "TRACE32 will reject them with 'wrong section'. Use `startup_script` "
            "for PRACTICE. With backend='custom' you also supply the full PBI section here."
        ),
    )
    startup_script: str | None = Field(
        default=None,
        description=(
            "Optional inline **PRACTICE** script body (.cmm content) that runs "
            "automatically after PowerView boots. This is the canonical mechanism for "
            "`SYStem.CPU <name>`, `SYStem.MemAccess`, `SYStem.CONFIG.*`, target preconfig. "
            "We write it to <work_dir>/startup.cmm and pass `-s <path>` to TRACE32 "
            "(per installation.pdf p53-62, practice_user.pdf p15-16)."
        ),
    )
    timeout_seconds: float = Field(
        default=45.0, ge=5.0, le=300.0,
        description="How long to wait for the RCL port to open after spawn. Bump for slow Windows boxes / hardware boot.",
    )
    preset: str | None = Field(
        default=None,
        description=(
            "Optional named target preset (call t32_list_presets to enumerate). "
            "If given, the preset's `arch`, `backend`, and `startup_script` are "
            "applied as defaults — any explicit field on this call still wins. "
            "USE THIS when you're not confident composing PRACTICE by hand."
        ),
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
    backend: str = Field(
        default="sim",
        description=f"PBI backend variation. One of: {list(SUPPORTED_BACKENDS)}",
    )
    target_host: str | None = Field(default=None, description="For 'net' / 'usb_proxy' backends.")
    target_node: str | None = Field(default=None, description="Optional PowerDebug NODE disambiguation.")
    proxy_port: int = Field(default=8866, ge=1, le=65535, description="For 'usb_proxy' backend.")
    extra_config: str | None = Field(default=None, description="Optional extra lines appended after standard sections.")


def t32_spawn(args: dict) -> dict:
    p = SpawnInput(**args)

    # Apply preset *as defaults* — explicit fields on the call win. This
    # lets the AI pick a known-good config by name and still override the
    # CPU / port / node_name when needed.
    arch = p.arch
    backend = p.backend
    startup_script = p.startup_script
    preset_applied: dict | None = None
    if p.preset:
        preset = get_preset(p.preset)
        if preset is None:
            return {
                "ok": False,
                "error": f"unknown preset {p.preset!r}",
                "hint": "call t32_list_presets to see available names",
            }
        preset_applied = preset.to_dict()
        # Apply only fields the caller didn't explicitly set.
        # `arch`/`backend` have non-None defaults in pydantic, so we treat
        # "still the default" as "not explicitly set" via a sentinel check.
        if "arch" not in args or args.get("arch") is None:
            arch = preset.arch
        if "backend" not in args or args.get("backend") is None:
            backend = preset.backend
        if startup_script is None:
            startup_script = preset.startup_script

    try:
        inst, _client = ensure_instance(
            port=p.port,
            node_name=p.node_name,
            arch=arch,
            t32sys=p.t32sys,
            auto_spawn=True,
            headless=p.headless,
            backend=backend,
            target_host=p.target_host,
            target_node=p.target_node,
            proxy_port=p.proxy_port,
            extra_config=p.extra_config,
            startup_script=startup_script,
            timeout_seconds=p.timeout_seconds,
        )
    except Exception as e:
        return {"ok": False, "error": str(e), "error_type": type(e).__name__,
                "preset_applied": preset_applied}
    out: dict = {"ok": True, "instance": inst.to_dict()}
    if preset_applied:
        out["preset_applied"] = preset_applied
    return out


class ListPresetsInput(BaseModel):
    """No arguments."""


def t32_list_presets(_args: dict) -> dict:
    """List curated TRACE32 target presets. Use the `name` of any entry
    as the `preset=` argument to `t32_spawn` to get a known-good `arch` +
    `backend` + `startup_script` combination.
    """
    return {"ok": True, "presets": list_presets()}


def t32_list_instances(_args: dict) -> dict:
    return {"ok": True, "instances": all_instances()}


def t32_shutdown(args: dict) -> dict:
    p = ShutdownInput(**args)
    return shutdown_instance(p.node_name, force=p.force)


def t32_render_config(args: dict) -> dict:
    """Dry-run: return the literal config.t32 that t32_spawn would write,
    without actually spawning. Use this when RCL fails to bind, when you
    want to verify the PBI section for a real-hardware backend, or to copy
    the config into an existing PowerView's config.t32 by hand."""
    p = RenderConfigInput(**args)
    try:
        body = render_config_t32(
            port=p.port, node=p.node_name,
            backend=p.backend, target_host=p.target_host,
            target_node=p.target_node, proxy_port=p.proxy_port,
            extra_config=p.extra_config,
        )
    except ValueError as e:
        return {"ok": False, "error": str(e)}
    return {
        "ok": True,
        "backend": p.backend,
        "config_t32": body,
        "note": (
            "This is what t32_spawn would write to config.t32. TRACE32 reads it "
            "at startup; if RCL doesn't bind the port for hardware backends, "
            "verify the PowerDebug is connected / reachable and the NODE= line "
            "matches the device identity (see installation.pdf §PBI via "
            "t32_search_manuals)."
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
