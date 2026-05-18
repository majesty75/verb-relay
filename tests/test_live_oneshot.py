"""Tier 3: one full debug loop against the live sim, staying under 50 cmds.

The free TRACE32 simulator caps at 50 script commands after the first Go/Step.
This test:
  1. Spawn sim (0 script commands)
  2. Load AXF (1)
  3. Break.Set on main (2)
  4. Go (3 — triggers the 50-cmd budget)
  5. Wait for halt (state polling is allowed)
  6. ~10 inspection commands (regs, memory, struct view)
  7. Shutdown (0 — QUIT)

That's well under 50. Gate with T32_MCP_LIVE=1.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

pytestmark = pytest.mark.live


def _find_demo_axf() -> Path | None:
    if "T32_DEMO_AXF" in os.environ:
        return Path(os.environ["T32_DEMO_AXF"])
    roots = []
    if "T32SYS" in os.environ:
        roots.append(Path(os.environ["T32SYS"]))
    roots += [Path("/Applications/t32"), Path.home() / "t32", Path("/opt/t32")]
    for r in roots:
        if not r.exists():
            continue
        for sub in ("demo/arm/compiler/arm", "demo/arm/compiler/gcc"):
            for ext in ("*.axf", "*.elf"):
                hits = list((r / sub).rglob(ext)) if (r / sub).exists() else []
                if hits:
                    return hits[0]
    return None


def test_full_debug_loop():
    axf = _find_demo_axf()
    if axf is None:
        pytest.skip("no Lauterbach demo AXF found")

    from trace32_mcp.tools.control import t32_breakpoint, t32_control
    from trace32_mcp.tools.inspect import t32_eval, t32_read_memory, t32_read_registers
    from trace32_mcp.tools.instances import t32_get_log, t32_shutdown, t32_spawn
    from trace32_mcp.tools.program import t32_load_program
    from trace32_mcp.tools.session import t32_status

    sp = t32_spawn({"arch": "cortexm"})
    assert sp["ok"], sp
    node = sp["instance"]["node_name"]

    try:
        # 1. load (counts as 1 PRACTICE command)
        ld = t32_load_program({"node_name": node, "axf_path": str(axf)})
        assert ld["ok"], f"load: {ld}"

        # 2. breakpoint on main (1)
        bp = t32_breakpoint({"node_name": node, "action": "set", "location": "main"})
        assert bp["ok"], f"bp: {bp}"

        # 3. Go — triggers the 50-cmd budget. Now careful.
        go = t32_control({"node_name": node, "action": "run"})
        assert go["ok"], f"go: {go}"

        # 4. Wait up to 5s for halt (state polling — does each count? assume yes)
        for _ in range(10):
            st = t32_status({"node_name": node})
            if st["ok"] and st["target"]["state"] != "running":
                break
            time.sleep(0.5)

        # 5. Inspection — keep <= 10 commands
        t32_read_registers({"node_name": node})
        t32_read_memory({"node_name": node, "address": 0x20000000, "length": 16, "width": 4})
        t32_eval({"node_name": node, "expression": "Register(PC)"})

        # 6. Verify we captured something in MCPLOG
        log = t32_get_log({"node_name": node, "source": "area"})
        assert log["ok"]
    finally:
        t32_shutdown({"node_name": node, "force": True})
