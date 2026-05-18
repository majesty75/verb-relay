"""Screenshot of the PowerView window.

Note on PNG vs ASCII: TRACE32's PRACTICE screenshot path is the `PRinTer.*`
family. On full PowerView builds the file extension drives the format; on
restricted/free builds (and on macOS) only ASCIIE (enhanced ASCII) is
available, so the captured "screenshot" is actually a text-art rendering.
That's still useful to the AI for layout/state inspection but is NOT a real
image. We surface the format in the result so the caller knows.
"""

from __future__ import annotations

import base64
import tempfile
from pathlib import Path

from pydantic import Field

from ._common import TargetSelector, resolve_target


class ScreenshotInput(TargetSelector):
    window: str | None = Field(
        default=None,
        description="Optional PowerView window name (e.g. 'Register.view'). If given, the previously-opened window of that name is captured via WinPRINT; default is the whole TRACE32 main window via PRinTer.HardCopy.",
    )


def _is_png(b: bytes) -> bool:
    return len(b) >= 4 and b[:4] == b"\x89PNG"


def t32_screenshot(args: dict) -> dict:
    p = ScreenshotInput(**args)
    _inst, client = resolve_target(p)
    # Pick a temp path *on the TRACE32 host* — for local sim this is the same
    # filesystem as the MCP. For remote PowerDebug the file lands on the T32
    # host, not on us; the caller will see an empty payload in that case.
    out_path = Path(tempfile.mkstemp(suffix=".png", prefix="t32_shot_")[1])
    try:
        # Sequence per ide_ref.pdf §PRinTer.FILE + §PRinTer.HardCopy:
        #   1. PRinTer.FILE "<path>"    — destination
        #   2. PRinTer.HardCopy           — emit current window/screen there
        # If `window` given, use WinPRINT to capture that specific window.
        client.run(f'PRinTer.FILE "{out_path.as_posix()}"', capture_area=False)
        if p.window:
            cmd = f'WinPRINT.{p.window}'
        else:
            cmd = 'PRinTer.HardCopy'
        res = client.run(cmd, capture_area=False).to_dict()
    except Exception as e:
        return {"ok": False, "error": f"screenshot failed: {e}", "error_type": type(e).__name__}

    encoded = None
    fmt = None
    data: bytes = b""
    if out_path.exists():
        data = out_path.read_bytes()
        encoded = base64.b64encode(data).decode("ascii")
        fmt = "PNG" if _is_png(data) else "ASCIIE"
        try:
            out_path.unlink()
        except Exception:
            pass

    return {
        "ok": bool(data),
        "cmd": res.get("cmd"),
        "path_on_t32_host": str(out_path),
        "format": fmt,
        "bytes": len(data),
        "png_base64": encoded if fmt == "PNG" else None,
        "ascii_text": data.decode("latin-1", errors="replace") if fmt == "ASCIIE" else None,
        "result": res,
        "note": (
            "Real PNG capture only available on full PowerView builds where "
            "PRinTer accepts a raster format. On restricted/free editions and "
            "on macOS this returns ASCIIE (text-art) — still informative for "
            "the AI but not a true image."
        ),
    }
