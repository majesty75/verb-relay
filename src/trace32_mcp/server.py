"""MCP server entrypoint.

Registers the v1 tools over stdio. Docs tools run without a TRACE32 connection;
everything else dispatches to a registered T32 instance (you must call
`t32_attach` or `t32_spawn` first — there is no implicit auto-connect).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Any, Callable

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool

from .tools import control as ctl
from .tools import docs as docs_tools
from .tools import extra
from .tools import healthcheck as hc
from .tools import inspect as insp
from .tools import instances as inst
from .tools import program as prog
from .tools import script as scr
from .tools import session as sess
from .tools import symbols as syms

log = logging.getLogger("trace32-mcp")


def _schema(model_cls) -> dict[str, Any]:
    return model_cls.model_json_schema()


# (name, description, handler, input model)
TOOLS: list[tuple[str, str, Callable[[dict], dict], Any]] = [
    # ---- instance lifecycle (explicit spawn vs attach — AI decides) --------
    ("t32_spawn",
        "Launch a NEW TRACE32 PowerView / simulator process. Use this when the user wants you to "
        "start a fresh sim from scratch. Returns the instance's node_name, host, port, pid, log path. "
        "Free port is picked automatically unless you pass `port`.",
        inst.t32_spawn, inst.SpawnInput),
    ("t32_attach",
        "ATTACH to a TRACE32 that's already running at a given host:port (e.g. the user gave you an IP). "
        "Fails if nothing is listening. Use t32_spawn instead to launch a new one.",
        sess.t32_attach, sess.AttachInput),
    ("t32_list_instances",
        "List every TRACE32 instance currently tracked (spawned by us + attached external). "
        "Includes node_name, endpoint, alive, uptime.",
        inst.t32_list_instances, inst.ListInstancesInput),
    ("t32_shutdown",
        "Shut down a tracked instance: RCL Quit, then SIGTERM (or SIGKILL with force=true) if we spawned it. "
        "For attached external instances, only QUIT is sent.",
        inst.t32_shutdown, inst.ShutdownInput),
    ("t32_disconnect",
        "Drop the cached RCL client for an endpoint without killing the T32 process. "
        "Use t32_shutdown if you also want to kill it.",
        sess.t32_disconnect, sess.DisconnectInput),
    ("t32_status",
        "Get target state (running/halted/down), CPU, endpoint. With no arguments lists every registered instance.",
        sess.t32_status, sess.StatusInput),
    ("t32_get_log",
        "Read recent log output from an instance: its subprocess stdout/stderr and/or the dedicated MCPLOG AREA window. "
        "Use this when something fails and you need to see what TRACE32 actually printed.",
        inst.t32_get_log, inst.GetLogInput),
    ("t32_healthcheck",
        "Battery of readiness checks — TCP open, RCL handshake, state query, echo command, AREA log readable. "
        "Returns per-step pass/fail + latency. Use before kicking off long automation to be sure TRACE32 is responsive.",
        hc.t32_healthcheck, hc.HealthcheckInput),

    # ---- program loading --------------------------------------------------
    ("t32_load_program",
        "Load an ELF/AXF (symbols + sections) and optionally a raw .bin at base_addr. "
        "Wraps Data.LOAD.Elf / Data.LOAD.Binary.",
        prog.t32_load_program, prog.LoadProgramInput),
    ("t32_reset",
        "Change SYStem.Mode (Up/Go/Attach/Down/...), optionally preceded by SYStem.RESet.",
        prog.t32_reset, prog.ResetInput),

    # ---- control + breakpoints --------------------------------------------
    ("t32_control",
        "Execution control: run | halt | step | step_over | step_out | step_asm.",
        ctl.t32_control, ctl.ControlInput),
    ("t32_breakpoint",
        "Manage breakpoints: set | clear | clear_all | list | enable | disable. "
        "Types: program / read / write / rw. Optional condition.",
        ctl.t32_breakpoint, ctl.BreakpointInput),

    # ---- inspect ----------------------------------------------------------
    ("t32_eval",
        "Evaluate any PRACTICE expression (Var.VALUE, Data.value, Register, symbol, arithmetic, ...). "
        "Returns the printed value plus error flags if anything failed.",
        insp.t32_eval, insp.EvalInput),
    ("t32_read_memory",
        "Read N bytes from a target address. Returns raw hex, width-decoded ints, and an ASCII hexdump.",
        insp.t32_read_memory, insp.ReadMemoryInput),
    ("t32_write_memory",
        "Write hex bytes to a target address.",
        insp.t32_write_memory, insp.WriteMemoryInput),
    ("t32_read_registers",
        "Dump CPU registers (optional group like 'GPR' or 'FPU').",
        insp.t32_read_registers, insp.ReadRegistersInput),
    ("t32_write_register",
        "Set a single CPU register.",
        insp.t32_write_register, insp.WriteRegisterInput),

    # ---- symbols ----------------------------------------------------------
    ("t32_list_symbols",
        "List symbols matching a glob. Output capped by `limit`.",
        syms.t32_list_symbols, syms.ListSymbolsInput),
    ("t32_var_view",
        "Typed variable view (handles structs/arrays).",
        syms.t32_var_view, syms.VarViewInput),

    # ---- scripting (universal escape hatch) ------------------------------
    ("t32_run_practice",
        "Run a PRACTICE (.cmm) script: inline body OR path, with positional args. "
        "Captures AREA window output + error state. Use this when no typed wrapper fits — "
        "PRACTICE can do almost anything in TRACE32.",
        scr.t32_run_practice, scr.RunPracticeInput),
    ("t32_run_command",
        "Run a single PRACTICE command line and return its message + error state.",
        scr.t32_run_command, scr.RunCommandInput),

    # ---- docs (no T32 needed) ---------------------------------------------
    ("t32_search_manuals",
        "Vector-search the bundled TRACE32 manuals. Returns top-k chunks with PDF filename, "
        "page range, and section heading. Use this to learn syntax before composing PRACTICE.",
        docs_tools.t32_search_manuals, docs_tools.SearchManualsInput),
    ("t32_lookup_command",
        "Precise lookup of a PRACTICE command name in the alphabetical reference. "
        "Faster + more accurate than vector search for known commands.",
        docs_tools.t32_lookup_command, docs_tools.LookupCommandInput),

    # ---- extra ------------------------------------------------------------
    ("t32_screenshot",
        "Capture a PowerView window (or whole screen) as PNG. Useful when the AI needs to literally "
        "see the debugger UI.",
        extra.t32_screenshot, extra.ScreenshotInput),
]


def build_server() -> Server:
    server: Server = Server("trace32-mcp")

    @server.list_tools()
    async def _list_tools() -> list[Tool]:
        return [
            Tool(name=name, description=desc, inputSchema=_schema(model_cls))
            for name, desc, _handler, model_cls in TOOLS
        ]

    handlers: dict[str, Callable[[dict], dict]] = {name: h for name, _, h, _ in TOOLS}

    @server.call_tool()
    async def _call_tool(name: str, arguments: dict | None) -> list[TextContent]:
        if name not in handlers:
            raise ValueError(f"unknown tool: {name}")
        args = arguments or {}
        try:
            result = await asyncio.to_thread(handlers[name], args)
        except Exception as e:
            log.exception("tool %s failed", name)
            result = {"ok": False, "error": str(e), "error_type": type(e).__name__}
        return [TextContent(type="text", text=json.dumps(result, indent=2, default=str))]

    return server


async def _amain() -> None:
    logging.basicConfig(
        level=os.environ.get("TRACE32_MCP_LOG", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=__import__("sys").stderr,
    )
    server = build_server()
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


def main() -> None:
    asyncio.run(_amain())


if __name__ == "__main__":
    main()
