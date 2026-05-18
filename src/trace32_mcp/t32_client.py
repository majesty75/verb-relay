"""TRACE32 client via Lauterbach's official PYRCL package.

Replaces the older ctypes-based wrapper. PYRCL is `lauterbach.trace32.rcl` —
a native Python implementation of the RCL protocol (no DLL loading, works
identically on macOS / Linux / Windows). Lauterbach recommends PYRCL for new
projects (app_python.pdf §"PYRCL versus TRACE32 Legacy Approach", p5).

Design:

  * **TCP first, UDP fallback.** PYRCL supports both; the docs say TCP is
    recommended (faster, multi-client, no PACKLEN tuning). We try TCP, and
    if that fails (older T32 build without NETTCP support, network policy,
    ...) we fall back to UDP/NETASSIST. Both RCL sections are written in
    the generated config.t32 (same PORT — UDP and TCP sockets are distinct
    at the OS level).

  * **Same observable surface as before.** Tools still receive a
    CommandResult-shaped dict from every PRACTICE command, including the
    error / mode flags / PRACTICE state. The fake mode (T32_MCP_FAKE=1)
    keeps working untouched.

  * **No more $T32SYS / t32api.py / libt32api*** plumbing.** PYRCL doesn't
    use the shared library — it speaks the RCL wire protocol directly over
    Python sockets.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Any


# Default sequence we try; first one to succeed wins.
DEFAULT_PROTOCOL_PREFERENCE = ("TCP", "UDP")


# ---------------------------------------------------------------------------
# Error-mode parsing (preserved for backward compat with downstream tools).
# PYRCL exposes message mode as an int; we keep our decoded flag names.
# ---------------------------------------------------------------------------

MODE_ERROR_INFO  = 0x01
MODE_ERROR       = 0x02
MODE_STATE       = 0x04
MODE_WARN        = 0x08
MODE_INFO        = 0x10
MODE_TARGET_INFO = 0x20
ERROR_MASK       = MODE_ERROR | MODE_ERROR_INFO

PRACTICE_IDLE = 0
PRACTICE_RUN  = 1
PRACTICE_ERR  = 2


def decode_mode(mode: int) -> list[str]:
    flags = []
    if mode & MODE_ERROR_INFO:  flags.append("ERROR_INFO")
    if mode & MODE_ERROR:       flags.append("ERROR")
    if mode & MODE_STATE:       flags.append("STATE")
    if mode & MODE_WARN:        flags.append("WARN")
    if mode & MODE_INFO:        flags.append("INFO")
    if mode & MODE_TARGET_INFO: flags.append("TARGET_INFO")
    return flags


# ---------------------------------------------------------------------------
# Public dataclasses
# ---------------------------------------------------------------------------

class T32Error(RuntimeError):
    def __init__(self, op: str, code: int = -1, message: str = "") -> None:
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
    # `t32sys` is kept for backward compat with attach/spawn signatures,
    # but PYRCL does not use it.
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
            "ok": self.ok, "cmd": self.cmd, "text": self.text,
            "mode": self.mode, "mode_flags": self.mode_flags,
            "practice_state": self.practice_state, "error": self.error,
        }


# ---------------------------------------------------------------------------
# Connection — TCP-first with UDP fallback
# ---------------------------------------------------------------------------

def _pyrcl():
    """Lazy import so the package is only required at first connect."""
    import lauterbach.trace32.rcl as t32  # type: ignore
    return t32


def try_connect(
    host: str, port: int,
    *, protocols: tuple[str, ...] = DEFAULT_PROTOCOL_PREFERENCE,
    packlen: int = 1024, timeout: float = 10.0,
) -> tuple[Any, str]:
    """Try each protocol in order. Returns (dbg, protocol_used).

    Raises T32Error if every protocol fails.
    """
    t32 = _pyrcl()
    last_error: Exception | None = None
    for proto in protocols:
        try:
            if proto.upper() == "TCP":
                dbg = t32.connect(node=host, port=port, protocol="TCP", timeout=timeout)
            elif proto.upper() == "UDP":
                dbg = t32.connect(
                    node=host, port=port, protocol="UDP",
                    packlen=packlen, timeout=timeout,
                )
            else:
                raise ValueError(f"unsupported protocol {proto!r}")
            return dbg, proto.upper()
        except Exception as e:  # noqa: BLE001 — PYRCL raises various types
            last_error = e
            continue
    raise T32Error(
        "PYRCL connect", message=(
            f"could not reach RCL at {host}:{port} over any of {list(protocols)}. "
            f"Last error: {last_error!r}"
        ),
    )


def is_rcl_responsive(
    host: str, port: int,
    t32sys: str | None = None,  # accepted for back-compat; unused by PYRCL
    *, protocols: tuple[str, ...] = DEFAULT_PROTOCOL_PREFERENCE,
    timeout_per_try: float = 2.0,
) -> bool:
    """Quick readiness probe — try to PYRCL-connect, then close."""
    try:
        dbg, _ = try_connect(host, port, protocols=protocols, timeout=timeout_per_try)
    except Exception:
        return False
    try:
        dbg.disconnect()
    except Exception:
        pass
    return True


# ---------------------------------------------------------------------------
# T32Client — drop-in for the old ctypes wrapper
# ---------------------------------------------------------------------------

class T32Client:
    """One client per logical T32 instance. Thread-safe."""

    def __init__(
        self,
        endpoint: T32Endpoint,
        *,
        keep_open: bool = True,
        protocols: tuple[str, ...] = DEFAULT_PROTOCOL_PREFERENCE,
    ) -> None:
        self.endpoint = endpoint
        self._lock = threading.Lock()
        self._keep_open = keep_open
        self._dbg = None
        self._connected_protocol: str | None = None
        self._area_setup_done = False
        self._protocols = protocols

    # ---- lifecycle ----------------------------------------------------------

    def _ensure_connected(self) -> None:
        if self._dbg is not None:
            return
        dbg, proto = try_connect(
            self.endpoint.host, self.endpoint.port,
            protocols=self._protocols,
            packlen=self.endpoint.packet_length,
            timeout=10.0,
        )
        self._dbg = dbg
        self._connected_protocol = proto
        if not self._area_setup_done:
            self._setup_area()
            self._area_setup_done = True

    def _setup_area(self) -> None:
        """Create our dedicated MCPLOG AREA so command output is always
        retrievable even if the user has no AREA window open."""
        for setup_cmd in (
            "AREA.CREATE MCPLOG 200. 1000.",
            "AREA.Select MCPLOG",
            "AREA.CLEAR MCPLOG",
        ):
            try:
                self._dbg.cmd(setup_cmd)
            except Exception:
                pass

    def close(self) -> None:
        with self._lock:
            if self._dbg is not None:
                try:
                    self._dbg.disconnect()
                except Exception:
                    pass
                self._dbg = None
                self._connected_protocol = None
                self._area_setup_done = False

    @property
    def connected_protocol(self) -> str | None:
        return self._connected_protocol

    # ---- core verb ----------------------------------------------------------

    def run(self, line: str) -> CommandResult:
        """Run one PRACTICE command line and return a typed result."""
        with self._lock:
            try:
                self._ensure_connected()
            except Exception as e:
                return CommandResult(
                    ok=False, cmd=line, text="", mode=0, mode_flags=[],
                    practice_state=0, error=f"connect failed: {e}",
                )
            text = ""
            mode = 0
            pstate = 0
            err: str | None = None
            try:
                self._dbg.cmd(line)
            except Exception as e:
                err = f"cmd raised: {e}"
            # Pull the AREA message + PRACTICE state regardless of the cmd outcome.
            try:
                msg = self._dbg.get_message()
                # PYRCL's get_message() returns either a string or a (text, mode) tuple
                # depending on version — handle both.
                if isinstance(msg, tuple) and len(msg) >= 2:
                    text, mode = str(msg[0]), int(msg[1])
                else:
                    text = "" if msg is None else str(msg)
            except Exception:
                pass
            try:
                pstate = int(self._dbg.get_practice_state())
            except Exception:
                pass
            ok = (err is None) and (not (mode & ERROR_MASK)) and (pstate != PRACTICE_ERR)
            if not ok and err is None:
                err = text.strip() or (
                    f"mode={decode_mode(mode)} practice_state={pstate}"
                )
            return CommandResult(
                ok=ok, cmd=line, text=text,
                mode=mode, mode_flags=decode_mode(mode),
                practice_state=pstate, error=err,
            )

    # Back-compat aliases
    def cmd(self, line: str) -> CommandResult:
        return self.run(line)

    def cmd_with_message(self, line: str) -> dict:
        return self.run(line).to_dict()

    def eval_practice(self, expression: str) -> dict:
        return self.run(f"PRINT {expression}").to_dict()

    # ---- state --------------------------------------------------------------

    def state(self) -> dict[str, Any]:
        """Report system state (down/halted/running) + CPU + endpoint."""
        with self._lock:
            self._ensure_connected()
            state_int = 0
            cpu = ""
            try:
                state_int = int(self._dbg.get_state())
            except Exception:
                pass
            state_map = {0: "down", 1: "halted_no_debugger", 2: "stopped", 3: "running"}
            try:
                cpu = str(self._dbg.fnc("STRing.UPpeR(CPU())"))
            except Exception:
                pass
            return {
                "raw_state": state_int,
                "state": state_map.get(state_int, "unknown"),
                "cpu": cpu,
                "protocol": self._connected_protocol,
                "endpoint": {
                    "host": self.endpoint.host,
                    "port": self.endpoint.port,
                    "node": self.endpoint.node_name,
                },
            }

    # ---- memory -------------------------------------------------------------

    def read_memory(self, address: int, length: int, access: str = "ANY") -> bytes:
        with self._lock:
            self._ensure_connected()
            try:
                return bytes(self._dbg.memory.read(address=address, length=length, access=access))
            except AttributeError:
                # Older PYRCL: positional API
                return bytes(self._dbg.memory_read(address, length, access))

    def write_memory(self, address: int, data: bytes, access: str = "ANY") -> None:
        with self._lock:
            self._ensure_connected()
            try:
                self._dbg.memory.write(address=address, data=bytes(data), access=access)
            except AttributeError:
                self._dbg.memory_write(address, bytes(data), access)

    # ---- AREA log -----------------------------------------------------------

    def read_area_log(self, area: str = "MCPLOG", lines: int | None = None) -> str:
        """Save the named AREA to a temp file on the T32 host and read it back.

        Only useful when the MCP and T32 share a filesystem (true for local
        sim). For remote PowerDebug returns the path-style empty result and
        the caller should fall back to per-command CommandResult.text.
        """
        import tempfile
        import time as _time
        from pathlib import Path as _P

        tmp = _P(tempfile.gettempdir()) / f"trace32_mcp_area_{area}.txt"
        try:
            tmp.unlink()
        except FileNotFoundError:
            pass

        with self._lock:
            self._ensure_connected()
            try:
                self._dbg.cmd(f'AREA.SAVE "{tmp}" {area}')
            except Exception:
                return ""

        for _ in range(20):
            if tmp.exists():
                break
            _time.sleep(0.05)
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
