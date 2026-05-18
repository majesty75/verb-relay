"""Thin, typed wrapper around Lauterbach's RCL Python API.

Adds three things on top of the bare `t32api` calls:

1. **Structured error detection.** TRACE32 returns RCL-level OK even when a
   PRACTICE command fails (e.g. `Data.LOAD.Elf nonexistent.elf` → "file not
   found" in the AREA window). We:
     * parse the `mode` bits returned by `T32_GetMessage`
     * call `T32_GetPracticeState()` after every command
     * return `{ok, text, mode_bits, practice_state, error?}` consistently.

2. **Dedicated AREA window for log capture.** On first connect we issue
   `AREA.CREATE MCPLOG / AREA.Select MCPLOG / AREA.CLEAR MCPLOG` so all command
   output is captured even when no AREA window is visible in PowerView.

3. **Per-endpoint serialisation.** The underlying `t32api` is procedural and
   keeps connection state in module globals; we wrap every call in a lock.
"""

from __future__ import annotations

import ctypes
import threading
import time
from dataclasses import dataclass, field
from typing import Any

from .t32_bridge import load_t32api


# T32_MESSAGE_* mode bits. These match the typical Lauterbach RCL header but
# we treat them defensively — any bit in ERROR_MASK flips ok=False.
MODE_ERROR_INFO = 0x01
MODE_ERROR      = 0x02
MODE_STATE      = 0x04
MODE_WARN       = 0x08
MODE_INFO       = 0x10
MODE_TARGET_INFO = 0x20

ERROR_MASK = MODE_ERROR | MODE_ERROR_INFO

# T32_GetPracticeState values
PRACTICE_IDLE = 0
PRACTICE_RUN  = 1
PRACTICE_ERR  = 2


def decode_mode(mode: int) -> list[str]:
    flags = []
    if mode & MODE_ERROR_INFO: flags.append("ERROR_INFO")
    if mode & MODE_ERROR:      flags.append("ERROR")
    if mode & MODE_STATE:      flags.append("STATE")
    if mode & MODE_WARN:       flags.append("WARN")
    if mode & MODE_INFO:       flags.append("INFO")
    if mode & MODE_TARGET_INFO:flags.append("TARGET_INFO")
    return flags


class T32Error(RuntimeError):
    def __init__(self, op: str, code: int, message: str = "") -> None:
        super().__init__(f"T32 {op} failed (code={code}): {message}".strip())
        self.op = op
        self.code = code
        self.message = message


@dataclass(frozen=True)
class T32Endpoint:
    host: str
    port: int
    node_name: str = "T32"
    packet_length: int = 1024
    t32sys: str | None = None


@dataclass
class CommandResult:
    """Outcome of a single PRACTICE command."""
    ok: bool
    cmd: str
    text: str
    mode: int
    mode_flags: list[str]
    practice_state: int
    error: str | None = None

    def to_dict(self) -> dict:
        return {
            "ok": self.ok,
            "cmd": self.cmd,
            "text": self.text,
            "mode": self.mode,
            "mode_flags": self.mode_flags,
            "practice_state": self.practice_state,
            "error": self.error,
        }


class T32Client:
    """One client per logical T32 instance."""

    def __init__(self, endpoint: T32Endpoint, keep_open: bool = False) -> None:
        self.endpoint = endpoint
        self._api = load_t32api(endpoint.t32sys)
        self._lock = threading.Lock()
        self._keep_open = keep_open
        self._connected = False
        self._area_setup_done = False

    # ---- lifecycle ----------------------------------------------------------

    def _ensure_connected(self) -> None:
        if self._connected:
            return
        api = self._api
        api.T32_Config(b"NODE=", self.endpoint.node_name.encode())
        api.T32_Config(b"HOSTNAME=", self.endpoint.host.encode())
        api.T32_Config(b"PORT=", str(self.endpoint.port).encode())
        api.T32_Config(b"PACKLEN=", str(self.endpoint.packet_length).encode())
        rc = api.T32_Init()
        if rc:
            raise T32Error("T32_Init", rc, "could not initialise RCL")
        rc = api.T32_Attach(1)  # 1 = ICD (debugger)
        if rc:
            api.T32_Exit()
            raise T32Error("T32_Attach", rc, "no T32 PowerView listening")
        self._connected = True
        if not self._area_setup_done:
            self._setup_area()
            self._area_setup_done = True

    def _setup_area(self) -> None:
        """Create the MCPLOG AREA so we can scrape command output reliably."""
        for setup_cmd in (
            "AREA.CREATE MCPLOG 200. 1000.",  # 200 cols, 1000 lines
            "AREA.Select MCPLOG",
            "AREA.CLEAR MCPLOG",
        ):
            try:
                self._api.T32_Cmd(setup_cmd.encode("utf-8"))
            except Exception:
                # Best-effort; older T32 may use slightly different syntax
                pass

    def _maybe_disconnect(self) -> None:
        if self._keep_open or not self._connected:
            return
        try:
            self._api.T32_Exit()
        finally:
            self._connected = False
            self._area_setup_done = False

    def close(self) -> None:
        with self._lock:
            if self._connected:
                try:
                    self._api.T32_Exit()
                finally:
                    self._connected = False
                    self._area_setup_done = False

    # ---- helpers ------------------------------------------------------------

    def _read_message(self) -> tuple[str, int]:
        buf = ctypes.create_string_buffer(4096)
        mode = ctypes.c_uint16(0)
        try:
            rc = self._api.T32_GetMessage(buf, ctypes.byref(mode))
        except TypeError:
            # Some versions take mode by value via int pointer differently
            rc = self._api.T32_GetMessage(buf, mode)
        if rc:
            return "", 0
        return buf.value.decode("utf-8", errors="replace"), int(mode.value)

    def _read_practice_state(self) -> int:
        if not hasattr(self._api, "T32_GetPracticeState"):
            return 0
        st = ctypes.c_int(0)
        try:
            rc = self._api.T32_GetPracticeState(ctypes.byref(st))
        except TypeError:
            rc = self._api.T32_GetPracticeState(st)
        if rc:
            return 0
        return int(st.value)

    # ---- core verb: send command, capture structured outcome ----------------

    def run(self, line: str) -> CommandResult:
        """Run one PRACTICE command line and return a typed result.

        Always reports `ok`, AREA message, mode bits, and PRACTICE state. If
        anything looks like an error we set ok=False and populate `error`.
        """
        with self._lock:
            self._ensure_connected()
            try:
                rc = self._api.T32_Cmd(line.encode("utf-8"))
                if rc:
                    # RCL-level failure: no AREA output to read
                    return CommandResult(
                        ok=False,
                        cmd=line,
                        text="",
                        mode=0,
                        mode_flags=[],
                        practice_state=0,
                        error=f"T32_Cmd rc={rc}",
                    )
                text, mode = self._read_message()
                # PRACTICE state takes a tick to update on async scripts
                pstate = self._read_practice_state()
                ok = not (mode & ERROR_MASK) and pstate != PRACTICE_ERR
                err = None
                if not ok:
                    err = text.strip() or f"mode={decode_mode(mode)} practice_state={pstate}"
                return CommandResult(
                    ok=ok,
                    cmd=line,
                    text=text,
                    mode=mode,
                    mode_flags=decode_mode(mode),
                    practice_state=pstate,
                    error=err,
                )
            finally:
                self._maybe_disconnect()

    # ---- convenience verbs --------------------------------------------------

    def cmd(self, line: str) -> CommandResult:
        """Alias for run() — kept for back-compat with older tools."""
        return self.run(line)

    def cmd_with_message(self, line: str) -> dict:
        """Legacy adapter that returns the same shape older tools expect."""
        return self.run(line).to_dict()

    def eval_practice(self, expression: str) -> dict:
        return self.run(f"PRINT {expression}").to_dict()

    def state(self) -> dict[str, Any]:
        """Report system state (running / halted / down) + CPU + endpoint."""
        with self._lock:
            self._ensure_connected()
            try:
                state_int = ctypes.c_int(0)
                rc = self._api.T32_GetState(ctypes.byref(state_int))
                if rc:
                    raise T32Error("T32_GetState", rc)
                state_map = {0: "down", 1: "halted_no_debugger", 2: "stopped", 3: "running"}
                # CPU info: signature varies. Defensive call.
                cpu = ""
                if hasattr(self._api, "T32_GetCpuInfo"):
                    cpu_buf = ctypes.create_string_buffer(64)
                    try:
                        self._api.T32_GetCpuInfo(
                            cpu_buf, ctypes.c_uint16(64), ctypes.c_uint16(0), ctypes.c_uint16(0)
                        )
                        cpu = cpu_buf.value.decode("utf-8", errors="replace")
                    except Exception:
                        pass
                return {
                    "raw_state": int(state_int.value),
                    "state": state_map.get(int(state_int.value), "unknown"),
                    "cpu": cpu,
                    "endpoint": {
                        "host": self.endpoint.host,
                        "port": self.endpoint.port,
                        "node": self.endpoint.node_name,
                    },
                }
            finally:
                self._maybe_disconnect()

    # ---- memory -------------------------------------------------------------

    def read_memory(self, address: int, length: int, access: str = "ANY") -> bytes:
        with self._lock:
            self._ensure_connected()
            try:
                buf = (ctypes.c_uint8 * length)()
                access_int = 0
                if hasattr(self._api, "T32_GetMemoryAccessNumber"):
                    try:
                        access_int = self._api.T32_GetMemoryAccessNumber(access.encode())
                    except Exception:
                        access_int = 0
                rc = self._api.T32_ReadMemory(
                    ctypes.c_uint32(address & 0xFFFFFFFF),
                    ctypes.c_int(access_int),
                    buf,
                    ctypes.c_int(length),
                )
                if rc:
                    raise T32Error("T32_ReadMemory", rc, f"@0x{address:X} len={length}")
                return bytes(buf)
            finally:
                self._maybe_disconnect()

    def write_memory(self, address: int, data: bytes, access: str = "ANY") -> None:
        with self._lock:
            self._ensure_connected()
            try:
                buf = (ctypes.c_uint8 * len(data))(*data)
                access_int = 0
                if hasattr(self._api, "T32_GetMemoryAccessNumber"):
                    try:
                        access_int = self._api.T32_GetMemoryAccessNumber(access.encode())
                    except Exception:
                        access_int = 0
                rc = self._api.T32_WriteMemory(
                    ctypes.c_uint32(address & 0xFFFFFFFF),
                    ctypes.c_int(access_int),
                    buf,
                    ctypes.c_int(len(data)),
                )
                if rc:
                    raise T32Error("T32_WriteMemory", rc, f"@0x{address:X} len={len(data)}")
            finally:
                self._maybe_disconnect()

    # ---- AREA log capture ---------------------------------------------------

    def read_area_log(self, area: str = "MCPLOG", lines: int | None = None) -> str:
        """Save the AREA contents to a temp file (on T32 host) then read it.

        Only works when the MCP and T32 share a filesystem (true for local sim
        and for any setup where /tmp is shared). For remote T32, this returns
        a best-effort empty string and the caller should fall back to the
        per-command CommandResult.text.
        """
        import tempfile
        from pathlib import Path

        tmp = Path(tempfile.gettempdir()) / f"trace32_mcp_area_{area}.txt"
        try:
            tmp.unlink()
        except FileNotFoundError:
            pass

        save_cmd = f'AREA.SAVE "{tmp}" {area}'
        with self._lock:
            self._ensure_connected()
            try:
                self._api.T32_Cmd(save_cmd.encode("utf-8"))
            finally:
                self._maybe_disconnect()

        # Give T32 a moment to write
        for _ in range(20):
            if tmp.exists():
                break
            time.sleep(0.05)
        if not tmp.exists():
            return ""
        text = tmp.read_text(errors="replace")
        try:
            tmp.unlink()
        except OSError:
            pass
        if lines is not None:
            text = "\n".join(text.splitlines()[-lines:])
        return text
