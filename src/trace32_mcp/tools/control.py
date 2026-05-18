"""Execution control + breakpoints.

Uses PYRCL's native go()/break_()/step() etc. instead of PRACTICE commands
so we get back proper Python exceptions on failure (and avoid the stale-
popup output capture problem). Breakpoint listing also goes through the
structured breakpoint service.
"""

from __future__ import annotations

from pydantic import Field

from ._common import TargetSelector, resolve_target


# Maps action → (native method on T32Client, fallback PRACTICE command).
# Native methods raise on failure; PRACTICE fallback always returns a result.
_CONTROL = {
    "run":       ("native_go",         "Go"),
    "halt":      ("native_break",      "Break"),
    "step":      ("native_step",       "Step"),
    "step_over": ("native_step_over",  "Step.Over"),
    "step_out":  ("native_go_return",  "Step.Out"),
    "step_asm":  ("native_step_asm",   "Step.Asm"),
}

_BP_TYPE = {
    "program": "Program",
    "read":    "Read",
    "write":   "Write",
    "rw":      "ReadWrite",
}


class ControlInput(TargetSelector):
    action: str = Field(description=f"One of {sorted(_CONTROL.keys())}")


class BreakpointInput(TargetSelector):
    action: str = Field(description="One of: set | clear | clear_all | list | enable | disable")
    location: str | None = Field(
        default=None,
        description="Symbol, source line (file\\line), or hex address. Required for set/clear/enable/disable.",
    )
    type: str = Field(default="program", description=f"One of {sorted(_BP_TYPE.keys())}")
    condition: str | None = Field(
        default=None, description="Optional PRACTICE conditional expression evaluated when hit",
    )


def t32_control(args: dict) -> dict:
    p = ControlInput(**args)
    if p.action not in _CONTROL:
        return {"ok": False, "error": f"action must be one of {sorted(_CONTROL)}, got {p.action}"}
    _inst, client = resolve_target(p)
    native, fallback = _CONTROL[p.action]
    err: str | None = None
    try:
        getattr(client, native)()
    except Exception as e:
        err = f"native {native}() raised: {e}; falling back to PRACTICE {fallback!r}"
        # Fall back to plain PRACTICE
        res = client.run(fallback).to_dict()
        return {"ok": res["ok"], "action": p.action, "cmd": fallback,
                "method": "practice_fallback", "result": res,
                "warning": err, "target": client.state()}
    return {"ok": True, "action": p.action, "cmd": native,
            "method": "native", "target": client.state()}


def t32_breakpoint(args: dict) -> dict:
    p = BreakpointInput(**args)
    _inst, client = resolve_target(p)
    action = p.action.lower()

    if action == "list":
        bps = client.bp_list()
        if bps and isinstance(bps[0], dict) and "_error" in bps[0]:
            return {"ok": False, "action": action, "error": bps[0]["_error"]}
        return {"ok": True, "action": action, "count": len(bps), "breakpoints": bps}
    if action == "clear_all":
        res = client.run("Break.Delete").to_dict()
        return {"ok": res["ok"], "action": action, "result": res}

    if not p.location:
        return {"ok": False, "error": f"action={action} requires `location`"}
    bp_type = _BP_TYPE.get(p.type.lower())
    if bp_type is None:
        return {"ok": False, "error": f"type must be one of {sorted(_BP_TYPE)}, got {p.type}"}

    if action == "set":
        cmd = f"Break.Set {p.location} /{bp_type}"
        if p.condition:
            cmd += f' /CONDition "{p.condition}"'
    elif action == "clear":
        cmd = f"Break.Delete {p.location}"
    elif action == "enable":
        cmd = f"Break.Enable {p.location}"
    elif action == "disable":
        cmd = f"Break.Disable {p.location}"
    else:
        return {"ok": False, "error": f"unknown action: {action}"}

    res = client.run(cmd).to_dict()
    return {"ok": res["ok"], "action": action, "cmd": cmd, "result": res}
