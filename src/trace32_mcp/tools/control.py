"""Execution control + breakpoints."""

from __future__ import annotations

from pydantic import Field

from ._common import TargetSelector, resolve_target


_CONTROL_CMD = {
    "run":       "Go",
    "halt":      "Break",
    "step":      "Step",
    "step_over": "Step.Over",
    "step_out":  "Step.Out",
    "step_asm":  "Step.Asm",
}

_BP_TYPE = {
    "program": "Program",
    "read":    "Read",
    "write":   "Write",
    "rw":      "ReadWrite",
}


class ControlInput(TargetSelector):
    action: str = Field(description=f"One of {sorted(_CONTROL_CMD.keys())}")


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
    if p.action not in _CONTROL_CMD:
        return {"ok": False, "error": f"action must be one of {sorted(_CONTROL_CMD)}, got {p.action}"}
    _inst, client = resolve_target(p)
    cmd = _CONTROL_CMD[p.action]
    res = client.run(cmd).to_dict()
    return {"ok": res["ok"], "action": p.action, "cmd": cmd, "result": res, "target": client.state()}


def t32_breakpoint(args: dict) -> dict:
    p = BreakpointInput(**args)
    _inst, client = resolve_target(p)
    action = p.action.lower()

    if action == "list":
        return {"ok": True, "action": action, "result": client.run("Break.List").to_dict()}
    if action == "clear_all":
        return {"ok": True, "action": action, "result": client.run("Break.Delete").to_dict()}

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
