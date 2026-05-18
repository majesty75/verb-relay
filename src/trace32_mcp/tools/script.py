"""run_practice (.cmm) + run_command (single line)."""

from __future__ import annotations

import tempfile
from pathlib import Path

from pydantic import Field

from ._common import TargetSelector, resolve_target


class RunPracticeInput(TargetSelector):
    script: str | None = Field(
        default=None,
        description="Inline PRACTICE script body. Either this or `path` must be set.",
    )
    path: str | None = Field(
        default=None,
        description="Absolute path to a .cmm file (must be visible to the T32 host).",
    )
    args: list[str] = Field(default_factory=list, description="Positional script arguments (passed via DO)")
    use_play: bool = Field(
        default=False,
        description="PLAY (compile+run) instead of DO (interpret). PLAY is faster for big scripts.",
    )


class RunCommandInput(TargetSelector):
    line: str = Field(description="A single PRACTICE command line to execute")


def t32_run_practice(args: dict) -> dict:
    p = RunPracticeInput(**args)
    if not p.script and not p.path:
        return {"ok": False, "error": "provide `script` (inline) or `path` (.cmm file)"}
    if p.script and p.path:
        return {"ok": False, "error": "provide only one of `script` or `path`"}

    _inst, client = resolve_target(p)

    cleanup: Path | None = None
    if p.script:
        tmp = tempfile.NamedTemporaryFile("w", suffix=".cmm", delete=False)
        tmp.write(p.script)
        tmp.flush()
        tmp.close()
        cleanup = Path(tmp.name)
        cmm_path = cleanup
    else:
        cmm_path = Path(p.path).expanduser().resolve()
        if not cmm_path.exists():
            return {"ok": False, "error": f".cmm not found: {cmm_path}"}

    verb = "PLAY" if p.use_play else "DO"
    extra = (" " + " ".join(p.args)) if p.args else ""
    cmd = f'{verb} "{cmm_path}"{extra}'
    try:
        res = client.run(cmd).to_dict()
    finally:
        if cleanup is not None:
            try:
                cleanup.unlink(missing_ok=True)
            except Exception:
                pass

    return {"ok": res["ok"], "cmd": cmd, "result": res}


def t32_run_command(args: dict) -> dict:
    p = RunCommandInput(**args)
    _inst, client = resolve_target(p)
    res = client.run(p.line).to_dict()
    return {"ok": res["ok"], "cmd": p.line, "result": res}
