"""Screenshot of the PowerView window."""

from __future__ import annotations

import base64
import tempfile
from pathlib import Path

from pydantic import Field

from ._common import TargetSelector, resolve_target


class ScreenshotInput(TargetSelector):
    window: str | None = Field(
        default=None,
        description="Optional PowerView window name (e.g. 'B::Register.view'). Default: whole screen.",
    )


def t32_screenshot(args: dict) -> dict:
    p = ScreenshotInput(**args)
    _inst, client = resolve_target(p)
    out_path = Path(tempfile.mkstemp(suffix=".png", prefix="t32_shot_")[1])
    target = f'"{out_path}"'
    cmd = f'PRINT.IMAGE {target} {p.window}' if p.window else f'PRINT.IMAGE {target}'
    res = client.run(cmd).to_dict()
    encoded = None
    if out_path.exists():
        encoded = base64.b64encode(out_path.read_bytes()).decode("ascii")
        try:
            out_path.unlink()
        except Exception:
            pass
    return {
        "ok": res["ok"],
        "cmd": cmd,
        "path_on_t32_host": str(out_path),
        "png_base64": encoded,
        "result": res,
    }
