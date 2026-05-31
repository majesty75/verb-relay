"""Spawn / track / shutdown TRACE32 PowerView instances.

A TRACE32 instance is one PowerView (or simulator) process bound to an RCL
port. Each binary corresponds to one CPU family:

    t32marm     -> ARM / Cortex-M / Cortex-A simulator
    t32mppc     -> PowerPC
    t32mtricore -> Infineon TriCore
    t32mrv      -> RISC-V
    ...

This module:
  * locates the right binary in $T32SYS/bin/<host_triple>/
  * picks a free TCP port if the caller didn't pin one
  * generates a minimal config.t32 (RCL=NETASSIST, PORT, NODE)
  * launches the process with stdout/stderr → log file
  * waits for the RCL port to respond (or kills the process and reports timeout)
  * tracks every spawned process in a registry so we can list / shutdown them
  * registers atexit handlers so a crashing MCP doesn't leave orphan T32s
"""

from __future__ import annotations

import atexit
import os
import platform
import shutil
import signal
import socket
import subprocess
import tempfile
import threading
import time
from contextlib import closing
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional


# --- arch → binary map ------------------------------------------------------

ARCH_BINARIES: dict[str, str] = {
    "arm":      "t32marm",
    "cortexm":  "t32marm",
    "cortexa":  "t32marm",
    "armv8":    "t32marm64",
    "armv9":    "t32marm64",
    "tricore":  "t32mtc",
    "ppc":      "t32mppc",
    "powerpc":  "t32mppc",
    "riscv":    "t32mriscv",
    "x86":      "t32mx86",
    "x86_64":   "t32mx86_64",
    "xtensa":   "t32mxtensa",
}


def supported_arches() -> list[str]:
    return sorted(ARCH_BINARIES.keys())


def _host_triples() -> list[str]:
    """T32 ships per-host subdirs under bin/. Names vary across releases:
       * macOS x64 / Apple Silicon: macosx64 (universal, current), mac64 (legacy)
       * Linux x86_64:              pc_linux64, pc_linux
       * Linux aarch64:             pc_linux_arm64, pc_linux_arm, pc_linux
       * Windows x64:               windows64, windows
       * Windows x86:               windows
    Return all plausible names in priority order; the first existing wins.
    """
    sysname = platform.system()
    arch = platform.machine().lower()
    if sysname == "Darwin":
        # T32 ships a universal Mach-O so the same dir serves Intel + Apple Silicon.
        return ["macosx64", "mac64"]
    if sysname == "Linux":
        if arch in ("x86_64", "amd64"):
            return ["pc_linux64", "pc_linux"]
        if arch in ("aarch64", "arm64"):
            return ["pc_linux_arm64", "pc_linux_arm", "pc_linux"]
        if arch.startswith("arm"):
            return ["pc_linux_arm", "pc_linux"]
        return ["pc_linux"]
    if sysname == "Windows":
        return ["windows64", "windows"] if arch in ("amd64", "x86_64") else ["windows"]
    raise RuntimeError(f"unsupported host: {sysname} / {arch}")


def _binary_variants(bin_name: str) -> list[str]:
    """T32 binary may carry an OS-specific suffix:
       * macOS:   t32marm-qt   (Qt GUI build, current)
       * Linux:   t32marm-qt / t32marm
       * Windows: t32marm.exe
    Return candidate filenames in priority order.
    """
    sysname = platform.system()
    if sysname == "Windows":
        return [bin_name + ".exe"]
    if sysname == "Darwin":
        return [bin_name + "-qt", bin_name]
    # Linux: modern installs ship -qt build alongside the legacy plain one
    return [bin_name + "-qt", bin_name]


def find_t32_binary(arch: str, t32sys: str | os.PathLike | None = None) -> Path:
    """Resolve the PowerView/simulator binary for a CPU family."""
    bin_name = ARCH_BINARIES.get(arch.lower())
    if bin_name is None:
        raise ValueError(f"unknown arch {arch!r}. Supported: {supported_arches()}")
    variants = _binary_variants(bin_name)

    roots: list[Path] = []
    if t32sys:
        roots.append(Path(t32sys).expanduser())
    env = os.environ.get("T32SYS")
    if env:
        roots.append(Path(env).expanduser())
    roots.extend([
        Path("/Applications/t32"),
        Path.home() / "t32",
        Path("/opt/t32"),
        Path("/usr/local/t32"),
        Path("C:\\T32"),
    ])

    triples = _host_triples()
    for root in roots:
        for triple in triples:
            for variant in variants:
                # Standard layout: <root>/bin/<host_triple>/<binary>
                candidate = root / "bin" / triple / variant
                if candidate.exists():
                    return candidate
        for variant in variants:
            # Some installs flatten to <root>/bin/<binary>
            candidate = root / "bin" / variant
            if candidate.exists():
                return candidate

    # Fall back to PATH lookup
    for variant in variants:
        on_path = shutil.which(variant)
        if on_path:
            return Path(on_path)
    raise FileNotFoundError(
        f"could not find any of {variants}. Set $T32SYS to a TRACE32 installation, "
        f"or put one of those binaries on $PATH."
    )


# --- free port ---------------------------------------------------------------

_PORT_PICK_LOCK = threading.Lock()
_PORT_RECENTLY_PICKED: set[int] = set()


def pick_free_port() -> int:
    """Pick an ephemeral port the kernel says is free.

    Race-aware: when many spawns happen back-to-back the kernel can hand the
    same ephemeral port to two callers before either has bound TRACE32 on it.
    We keep an in-process set of recently-picked ports to avoid handing the
    same port to the next caller within the same process — full immunity also
    needs the spawn loop to retry on bind failure, which it does.
    """
    with _PORT_PICK_LOCK:
        for _ in range(50):
            with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as s:
                s.bind(("127.0.0.1", 0))
                p = int(s.getsockname()[1])
            if p not in _PORT_RECENTLY_PICKED:
                _PORT_RECENTLY_PICKED.add(p)
                # Cap the set so it doesn't grow unbounded.
                if len(_PORT_RECENTLY_PICKED) > 256:
                    _PORT_RECENTLY_PICKED.clear()
                    _PORT_RECENTLY_PICKED.add(p)
                return p
        # Couldn't find one in 50 tries — let the caller bind anyway.
        return p


def is_port_open(host: str, port: int, timeout: float = 0.3) -> bool:
    """Whether something is listening on host:port (TCP).

    WARNING: TRACE32's `RCL=NETASSIST` opens a UDP port, NOT a TCP one. This
    check will FALSE NEGATIVE for a working T32 RCL endpoint. Use
    `is_rcl_responsive()` for spawn/attach readiness checks.
    """
    try:
        with closing(socket.create_connection((host, port), timeout=timeout)):
            return True
    except OSError:
        return False


def _find_pid_holding_port(port: int) -> int | None:
    """Return the PID of the process listening on TCP `port`, or None.

    Used on macOS where the Qt-built TRACE32 forks (parent rc=0 exit, real
    debugger in detached child). Cross-platform behaviour:

      * macOS / BSD: `lsof` (preinstalled).
      * Linux:       `lsof` first (often present), then parse /proc/net/tcp
                     + /proc/<pid>/fd/* — works on minimal containers where
                     lsof isn't shipped.
      * Windows:     return None. Windows TRACE32 doesn't double-fork so
                     `proc.pid` is already correct; nothing to adopt.
    """
    sysname = platform.system()
    if sysname == "Windows":
        return None

    # 1. lsof (preferred — single call, works on macOS + most Linux)
    try:
        out = subprocess.check_output(
            ["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN", "-t"],
            stderr=subprocess.DEVNULL, timeout=2.0,
        ).decode().strip()
        if out:
            try:
                return int(out.splitlines()[0])
            except (ValueError, IndexError):
                pass
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired,
            FileNotFoundError, OSError):
        pass

    # 2. /proc fallback (Linux only — minimal containers without lsof)
    if sysname == "Linux":
        try:
            return _linux_pid_for_listen_port(port)
        except Exception:
            pass

    return None


def _linux_pid_for_listen_port(port: int) -> int | None:
    """Read /proc/net/tcp[6] to find a listener inode, then scan
    /proc/<pid>/fd/* to find which process owns it.

    No external binaries. Costs an O(N) fd scan on first call but is fine
    for spawn-time adoption.
    """
    target_inode = None
    listen_state = "0A"  # TCP_LISTEN
    for proc_path in ("/proc/net/tcp", "/proc/net/tcp6"):
        try:
            with open(proc_path) as fh:
                next(fh, None)  # header
                for line in fh:
                    parts = line.split()
                    if len(parts) < 10:
                        continue
                    local = parts[1]
                    state = parts[3]
                    inode = parts[9]
                    if state != listen_state:
                        continue
                    try:
                        line_port = int(local.rsplit(":", 1)[1], 16)
                    except (ValueError, IndexError):
                        continue
                    if line_port == port:
                        target_inode = inode
                        break
        except FileNotFoundError:
            continue
        if target_inode:
            break
    if not target_inode:
        return None
    needle = f"socket:[{target_inode}]"
    proc_root = Path("/proc")
    for pid_dir in proc_root.iterdir():
        if not pid_dir.name.isdigit():
            continue
        fd_dir = pid_dir / "fd"
        try:
            for fd in fd_dir.iterdir():
                try:
                    if fd.readlink() == needle:
                        return int(pid_dir.name)
                except OSError:
                    continue
        except (OSError, PermissionError):
            continue
    return None


def is_rcl_responsive(host: str, port: int, t32sys: str | None = None,
                      timeout_per_try: float = 2.0) -> bool:
    """Probe a TRACE32 RCL endpoint via PYRCL (tries TCP then UDP).

    Re-exported from t32_client so callers don't need to know the protocol
    layer. `t32sys` is accepted for back-compat but ignored — PYRCL doesn't
    need the T32 install path.
    """
    # Lazy import keeps t32_process importable without PYRCL installed
    # (e.g. during test collection in fake mode).
    from .t32_client import is_rcl_responsive as _probe
    return _probe(host, port, t32sys=t32sys, timeout_per_try=timeout_per_try)


# --- config.t32 generation ---------------------------------------------------

CONFIG_T32_TEMPLATE = """\
; auto-generated by trace32-mcp
OS=
ID=T32_{node}
TMP={tmp_path}
{sys_line}
{pbi_section}
RCL=NETTCP
PORT={port}

RCL=NETASSIST
PORT={port}
PACKLEN={packlen}

SCREEN=
HEADER=Trace32-MCP {node}
"""
# Layout notes (verified via the bundled manuals search + pyrcl docs):
#   * Each section starts with `KEY=` and is terminated by a blank line.
#   * PBI section picks the backend (sim / usb / net / usb_proxy / custom).
#   * BOTH RCL=NETTCP and RCL=NETASSIST sections are emitted so PYRCL can pick
#     TCP (preferred) or fall back to UDP — same PORT value works for both
#     because UDP/TCP are distinct sockets at the OS level.
#     PYRCL example (https://pyrcl.readthedocs.io/en/latest/sub/intro_basics.html):
#         RCL=NETTCP       <- TCP, stream, no PACKLEN
#         PORT=20000
#
#         RCL=NETASSIST    <- UDP, packet, PACKLEN required
#         PORT=20000
#         PACKLEN=1024
#   * SYS= under OS= explicitly tells T32 where its install lives, per
#     installation.pdf p37 ("OS section / SYS=<path>"). When sys_path is unset
#     we omit the line and TRACE32 falls back to deriving it from the
#     executable path.


# ---------------------------------------------------------------------------
# Supported backends. The PBI variation is the *runtime* backend (sim vs real
# debugger); arch (above) still chooses the *binary* (t32marm, t32mppc, ...).
# Both have to be set correctly: e.g. arch=cortexm + backend=usb runs
# `t32marm` against a real PowerDebug via USB.
# ---------------------------------------------------------------------------
SUPPORTED_BACKENDS = ("sim", "usb", "net", "usb_proxy", "custom")


def _pbi_section(
    backend: str,
    *,
    target_host: str | None = None,
    target_node: str | None = None,
    proxy_port: int = 8866,
) -> str:
    """Build the PBI section for a chosen backend.

    Why the form differs per backend:
      * SIM has no extra parameters, so the single-line `PBI=SIM` form is the
        idiom used by Lauterbach's own examples (int_codeblock.pdf p7,
        app_python.pdf p9).
      * Hardware backends (USB / NET) carry extra parameters like NODE= or
        PROXY*. Those parameters MUST sit inside the PBI section, which forces
        the multi-line `PBI=\\n<VARIATION>\\n<KEY>=<VAL>` layout (per
        installation.pdf p42-47).
    Both forms are accepted by TRACE32 for sectionless backends like SIM, but
    we match the documented form for each case to keep the config recognisable.
    """
    # Each branch returns a block ending in "\n". Combined with the template's
    # own newline after {pbi_section}, that becomes a blank line separator
    # between the PBI section and the next section — required per
    # api_remote_c.pdf p17 ("between two empty lines").
    if backend == "sim":
        return "PBI=SIM\n"
    if backend == "usb":
        lines = ["PBI=", "USB"]
        if target_node:
            lines.append(f"NODE={target_node}")
        return "\n".join(lines) + "\n"
    if backend == "net":
        if not target_host:
            raise ValueError(
                "backend='net' requires target_host (the PowerDebug device's "
                "Ethernet name or IP, e.g. 'training1' or '10.0.5.7')"
            )
        return f"PBI=\nNET\nNODE={target_host}\n"
    if backend == "usb_proxy":
        if not target_host:
            raise ValueError(
                "backend='usb_proxy' requires target_host (proxy machine IP that "
                "runs t32tcpusb in front of the USB PowerDebug)"
            )
        lines = ["PBI=", "USB", f"PROXYNAME={target_host}", f"PROXYPORT={proxy_port}"]
        if target_node:
            lines.append(f"NODE={target_node}")
        return "\n".join(lines) + "\n"
    if backend == "custom":
        return ""  # caller provides via extra_config
    raise ValueError(f"unknown backend {backend!r}. Supported: {SUPPORTED_BACKENDS}")


def _sys_line(sys_path: str | os.PathLike | None) -> str:
    """Render the optional SYS= line (under the OS= section)."""
    if not sys_path:
        return ""
    # TRACE32 accepts forward-slash paths on every host.
    return f"SYS={Path(sys_path).as_posix()}\n"


def write_config_t32(
    dst_dir: Path,
    *,
    port: int,
    node: str,
    packlen: int = 1024,
    backend: str = "sim",
    target_host: str | None = None,
    target_node: str | None = None,
    proxy_port: int = 8866,
    sys_path: str | os.PathLike | None = None,
    extra_config: str | None = None,
) -> Path:
    """Generate a TRACE32 config.t32 in dst_dir.

    Emits both RCL=NETTCP and RCL=NETASSIST on the same PORT so PYRCL can
    prefer TCP and fall back to UDP. `sys_path` becomes SYS= under OS=.
    `extra_config` is appended verbatim after the standard sections.
    """
    import tempfile as _tf
    dst_dir.mkdir(parents=True, exist_ok=True)
    tmp_path = Path(_tf.gettempdir()).as_posix()
    pbi_block = _pbi_section(
        backend, target_host=target_host, target_node=target_node, proxy_port=proxy_port,
    )
    body = CONFIG_T32_TEMPLATE.format(
        port=port, node=node, packlen=packlen, tmp_path=tmp_path,
        pbi_section=pbi_block, sys_line=_sys_line(sys_path),
    )
    if extra_config:
        body += "\n" + extra_config.rstrip() + "\n"
    cfg = dst_dir / "config.t32"
    cfg.write_text(body)
    return cfg


def render_config_t32(
    *,
    port: int = 20000,
    node: str = "T32",
    packlen: int = 1024,
    backend: str = "sim",
    target_host: str | None = None,
    target_node: str | None = None,
    proxy_port: int = 8866,
    sys_path: str | os.PathLike | None = None,
    extra_config: str | None = None,
) -> str:
    """Return the config.t32 contents that would be written for a given spawn,
    without touching the filesystem. Useful for AI agents to sanity-check
    the planned config before invoking t32_spawn."""
    import tempfile as _tf
    tmp_path = Path(_tf.gettempdir()).as_posix()
    pbi_block = _pbi_section(
        backend, target_host=target_host, target_node=target_node, proxy_port=proxy_port,
    )
    body = CONFIG_T32_TEMPLATE.format(
        port=port, node=node, packlen=packlen, tmp_path=tmp_path,
        pbi_section=pbi_block, sys_line=_sys_line(sys_path),
    )
    if extra_config:
        body += "\n" + extra_config.rstrip() + "\n"
    return body


# --- instance dataclass + registry ------------------------------------------

@dataclass
class T32Instance:
    node_name: str
    host: str
    port: int
    arch: str
    pid: int
    binary: str
    config_path: str
    log_path: str
    work_dir: str
    spawned_by_us: bool
    started_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["uptime_seconds"] = round(time.time() - self.started_at, 1)
        d["alive"] = self.is_alive()
        return d

    def is_alive(self) -> bool:
        if not self.spawned_by_us:
            # External T32 — probe the port using RCL handshake (supports both UDP and TCP)
            return is_rcl_responsive(self.host, self.port, timeout_per_try=0.2)
        if self.pid <= 0:
            # Fake instance (pid==0) — never call os.kill which would target
            # the whole process group; just say "no kernel process".
            return False
        try:
            os.kill(self.pid, 0)
        except OSError:
            return False
        return True

    def tail_log(self, n: int = 80) -> str:
        try:
            with open(self.log_path, "rb") as f:
                # Read at most last 64 KB then slice last n lines
                f.seek(0, os.SEEK_END)
                size = f.tell()
                f.seek(max(0, size - 65536))
                data = f.read().decode("utf-8", errors="replace")
            lines = data.splitlines()
            return "\n".join(lines[-n:])
        except FileNotFoundError:
            return ""


class InstanceRegistry:
    """In-memory registry of TRACE32 instances we either spawned or attached to.

    Thread-safe. Cleans up spawned processes on interpreter exit.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._by_node: dict[str, T32Instance] = {}
        self._last_scan = 0.0
        atexit.register(self._cleanup_atexit)

    def register(self, inst: T32Instance) -> None:
        with self._lock:
            self._by_node[inst.node_name] = inst

    def get_by_node(self, node: str) -> T32Instance | None:
        with self._lock:
            return self._by_node.get(node)

    def get_by_endpoint(self, host: str, port: int) -> T32Instance | None:
        with self._lock:
            for inst in self._by_node.values():
                if inst.host == host and inst.port == port:
                    return inst
        return None

    def remove(self, node: str) -> T32Instance | None:
        with self._lock:
            return self._by_node.pop(node, None)

    def list(self) -> list[T32Instance]:
        now = time.time()
        if now - self._last_scan > 5.0:
            self._last_scan = now
            try:
                detect_and_register_external_instances()
            except Exception:
                pass
        with self._lock:
            return list(self._by_node.values())

    def shutdown(self, node: str, force: bool = False, timeout: float = 5.0) -> dict:
        inst = self.get_by_node(node)
        if inst is None:
            return {"ok": False, "error": f"no instance named {node!r}"}

        # Best-effort RCL Quit first
        try:
            from .t32_client import T32Client, T32Endpoint
            client = T32Client(T32Endpoint(host=inst.host, port=inst.port, node_name=inst.node_name))
            try:
                client.cmd("QUIT")
            except Exception:
                pass
            finally:
                client.close()
        except Exception:
            pass

        if not inst.spawned_by_us:
            self.remove(node)
            return {"ok": True, "method": "detach_only", "instance": inst.to_dict()}

        if inst.pid <= 0:
            # Fake instance — nothing to signal
            self.remove(node)
            return {"ok": True, "method": "fake_remove", "instance": inst.to_dict()}

        # Process management — portable terminate/kill
        _terminate(inst.pid)

        deadline = time.time() + timeout
        while time.time() < deadline:
            if not inst.is_alive():
                break
            time.sleep(0.1)

        if inst.is_alive() and force:
            _kill(inst.pid)

        self.remove(node)
        return {"ok": True, "method": "signal", "instance": inst.to_dict()}

    def _cleanup_atexit(self) -> None:
        for inst in self.list():
            if not inst.spawned_by_us:
                continue
            if inst.pid <= 0:
                # Fake / sentinel — nothing to signal
                continue
            if inst.is_alive():
                _terminate(inst.pid)


_REGISTRY = InstanceRegistry()


def registry() -> InstanceRegistry:
    return _REGISTRY


# --- spawn -------------------------------------------------------------------

class SpawnTimeout(RuntimeError):
    pass


def _terminate(pid: int) -> None:
    """Portable graceful terminate. SIGTERM on POSIX, CTRL_BREAK_EVENT on Windows."""
    if pid <= 0:
        return
    try:
        if platform.system() == "Windows":
            # Send CTRL_BREAK_EVENT to the process group we created via CREATE_NEW_PROCESS_GROUP.
            os.kill(pid, signal.CTRL_BREAK_EVENT)  # type: ignore[attr-defined]
        else:
            os.kill(pid, signal.SIGTERM)
    except (ProcessLookupError, OSError):
        pass


def _kill(pid: int) -> None:
    """Portable hard kill. SIGKILL on POSIX, TerminateProcess via SIGTERM on Windows."""
    if pid <= 0:
        return
    try:
        if platform.system() == "Windows":
            # On Windows, signal.SIGTERM under os.kill calls TerminateProcess
            os.kill(pid, signal.SIGTERM)
        else:
            os.kill(pid, signal.SIGKILL)
    except (ProcessLookupError, OSError):
        pass


def spawn(
    arch: str,
    *,
    port: int | None = None,
    node_name: str | None = None,
    t32sys: str | None = None,
    headless: bool = False,
    backend: str = "sim",
    target_host: str | None = None,
    target_node: str | None = None,
    proxy_port: int = 8866,
    extra_args: list[str] | None = None,
    extra_config: str | None = None,
    startup_script: str | None = None,
    timeout_seconds: float = 45.0,
) -> T32Instance:
    """Launch a TRACE32 PowerView (sim or PowerDebug) and wait for its RCL port.

    `arch` chooses the binary (t32marm, t32mppc, ...). `backend` chooses the
    runtime PBI section (sim / usb / net / usb_proxy / custom).

    Two distinct "extras":
      * `extra_config` — additional **config.t32** lines (e.g. `PRINTER=`,
        `HEADER=`, custom `SCREEN=` settings). NOT for PRACTICE commands like
        `SYStem.CPU` — those are runtime debugger commands and TRACE32 will
        reject them as "wrong section" if put in config.t32.
      * `startup_script` — inline **PRACTICE** body (.cmm content) to execute
        after PowerView boots. The canonical mechanism for `SYStem.CPU
        <name>`, `SYStem.MemAccess`, target-specific configuration, etc.
        We write it to <work_dir>/startup.cmm and pass it to TRACE32 via
        `-s <path>` (per installation.pdf p53-62, practice_user.pdf p15-16).

    Raises SpawnTimeout if the process starts but RCL never responds.
    In fake mode (T32_MCP_FAKE=1) returns a registered fake instance without
    launching anything.
    """
    from .t32_fake import is_fake_mode, make_fake_instance

    if is_fake_mode():
        inst = make_fake_instance(arch, port, node_name)
        _REGISTRY.register(inst)
        return inst

    binary = find_t32_binary(arch, t32sys=t32sys)
    chosen_port = port if port is not None else pick_free_port()
    node = node_name or f"T32_{arch.upper()}_{chosen_port}"
    sys_root = binary.parent.parent.parent  # bin/<triple>/<bin> → install root

    work_dir = Path(tempfile.mkdtemp(prefix=f"trace32_mcp_{node}_"))
    config_path = write_config_t32(
        work_dir,
        port=chosen_port, node=node,
        backend=backend, target_host=target_host, target_node=target_node,
        proxy_port=proxy_port,
        sys_path=sys_root,
        extra_config=extra_config,
    )
    log_path = work_dir / "t32.log"

    argv: list[str] = [str(binary), "-c", str(config_path)]
    if startup_script:
        # TRACE32 runs autostart.cmm first, then this script. Per
        # installation.pdf p53-62, this is the proper way to chain runtime
        # PRACTICE setup (e.g. `SYStem.CPU CORTEXM4`, `SYStem.MemAccess DAP`).
        startup_path = work_dir / "startup.cmm"
        startup_path.write_text(startup_script.rstrip() + "\n")
        argv += ["-s", str(startup_path)]
    if headless and platform.system() != "Windows":
        # T32 does not have a true headless mode, but we can hint at it on Linux
        argv += ["-nofbsync"]
    if extra_args:
        argv += extra_args

    env = os.environ.copy()
    env.setdefault("T32SYS", str(binary.parent.parent.parent))  # bin/<triple>/<bin> -> root

    log_fh = open(log_path, "wb")
    popen_kwargs: dict = {
        "cwd": str(work_dir),
        "env": env,
        "stdout": log_fh,
        "stderr": subprocess.STDOUT,
    }
    if platform.system() == "Windows":
        # CREATE_NEW_PROCESS_GROUP lets us send CTRL_BREAK_EVENT to just this group
        popen_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        popen_kwargs["start_new_session"] = True  # POSIX: detach from our session
    proc = subprocess.Popen(argv, **popen_kwargs)

    # Wait for the RCL port. NOTE: RCL=NETASSIST is UDP — we must probe with the
    # actual RCL handshake, not a TCP connect (which would always false-negative).
    #
    # macOS-specific: the Qt-built TRACE32 forks. The parent we spawned exits
    # with rc=0 within ~1s and the real debugger runs as a detached child.
    # We treat a clean parent exit as "keep polling for the child" and adopt
    # the child PID via port lookup once RCL responds. A non-zero parent exit
    # is still a hard failure (real crash).
    deadline = time.time() + timeout_seconds
    parent_exited_clean = False
    rcl_up = False
    while time.time() < deadline:
        rc = proc.poll()
        if rc is not None:
            if rc != 0 and not parent_exited_clean:
                log_fh.close()
                raise RuntimeError(
                    f"TRACE32 binary exited early (rc={rc}). "
                    f"Log tail:\n{Path(log_path).read_text(errors='replace')[-2000:]}"
                )
            parent_exited_clean = True
        # Real RCL handshake (UDP) — TCP probe would always false-negative here.
        if is_rcl_responsive("127.0.0.1", chosen_port, t32sys=str(binary.parent.parent.parent),
                             timeout_per_try=0.5):
            rcl_up = True
            break
        time.sleep(0.3)
    if not rcl_up:
        _terminate(proc.pid)
        log_fh.close()
        # Surface as much context as possible so the AI / user can diagnose:
        # the config we wrote (TRACE32 may have rejected it silently) and the
        # tail of stdout/stderr (TRACE32 sometimes logs config errors to stderr).
        try:
            log_tail = Path(log_path).read_text(errors='replace')[-2000:]
        except Exception:
            log_tail = "(no log captured)"
        try:
            cfg_text = Path(config_path).read_text(errors='replace')
        except Exception:
            cfg_text = "(config unreadable)"
        raise SpawnTimeout(
            f"TRACE32 spawned (pid={proc.pid}) but port {chosen_port} never "
            f"opened within {timeout_seconds}s.\n\n"
            f"--- generated config.t32 ({config_path}) ---\n{cfg_text}\n"
            f"--- TRACE32 stdout/stderr tail ({log_path}) ---\n{log_tail}\n"
            f"--- diagnostic checklist ---\n"
            f"  * RCL=NETASSIST opens a UDP port (not TCP). Readiness probe\n"
            f"    uses the real T32_Init+T32_Attach handshake; failure means\n"
            f"    either T32 isn't listening or the t32api wrapper can't reach\n"
            f"    the configured port.\n"
            f"  * Backend was '{backend}'. For 'usb' / 'net' / 'usb_proxy',\n"
            f"    TRACE32 also has to find the PowerDebug hardware itself —\n"
            f"    if the device isn't connected / reachable, PowerView opens\n"
            f"    in serial-monitor mode and RCL never arms. Check the\n"
            f"    subprocess log above for 'no device' / 'cannot connect'.\n"
            f"  * Windows Defender Firewall can silently block UDP bind on a\n"
            f"    new port the first time PowerView runs. Allow inbound UDP\n"
            f"    on the TRACE32 executable for the chosen port.\n"
            f"  * For CPU-specific setup (`SYStem.CPU <name>`, `SYStem.MemAccess`,\n"
            f"    `SYStem.CONFIG.*`), pass the PRACTICE body via `startup_script`\n"
            f"    to t32_spawn — NOT via `extra_config` (those are PRACTICE\n"
            f"    commands, not config.t32 directives; TRACE32 errors `wrong\n"
            f"    section` if they end up in config.t32).\n"
            f"  * Run t32_render_config (with the same backend / target_host)\n"
            f"    first to dry-run the config and compare against working\n"
            f"    examples — see installation.pdf p42-47 via t32_search_manuals.\n"
        )

    log_fh.close()

    # macOS Qt build forks; adopt the real listener PID so shutdown can
    # actually terminate the debugger. On Linux/Windows proc.pid is correct
    # and the lookup is best-effort.
    adopted_pid = proc.pid
    if parent_exited_clean:
        listener_pid = _find_pid_holding_port(chosen_port)
        if listener_pid:
            adopted_pid = listener_pid

    inst = T32Instance(
        node_name=node,
        host="127.0.0.1",
        port=chosen_port,
        arch=arch,
        pid=adopted_pid,
        binary=str(binary),
        config_path=str(config_path),
        log_path=str(log_path),
        work_dir=str(work_dir),
        spawned_by_us=True,
    )
    _REGISTRY.register(inst)
    return inst


def attach(
    host: str,
    port: int,
    *,
    node_name: str | None = None,
    arch: str = "unknown",
) -> T32Instance:
    """Register an externally-started TRACE32 in our registry.

    No process management responsibility (spawned_by_us=False) — shutdown will
    only QUIT it over RCL, not SIGTERM.
    """
    from .t32_fake import is_fake_mode
    if is_fake_mode():
        node = node_name or f"FAKE_external_{host}_{port}"
        existing = _REGISTRY.get_by_endpoint(host, port)
        if existing:
            return existing
        inst = T32Instance(
            node_name=node, host=host, port=port, arch=arch,
            pid=0, binary="(fake-external)", config_path="", log_path="", work_dir="",
            spawned_by_us=False,
        )
        _REGISTRY.register(inst)
        return inst

    # Use the real RCL handshake — RCL=NETASSIST is UDP and would silently
    # fail any TCP-based probe.
    if not is_rcl_responsive(host, port, timeout_per_try=1.5):
        raise ConnectionError(
            f"RCL not responding at {host}:{port}. Either nothing is listening "
            "or the T32 there isn't configured with `RCL=NETASSIST PORT={port}`."
        )
    node = node_name or f"T32_external_{host}_{port}"
    existing = _REGISTRY.get_by_endpoint(host, port)
    if existing:
        return existing
    inst = T32Instance(
        node_name=node,
        host=host,
        port=port,
        arch=arch,
        pid=0,
        binary="(external)",
        config_path="",
        log_path="",
        work_dir="",
        spawned_by_us=False,
    )
    _REGISTRY.register(inst)
    return inst


def connect_or_spawn(
    arch: str = "arm",
    *,
    host: str = "127.0.0.1",
    port: int | None = None,
    node_name: str | None = None,
    t32sys: str | None = None,
    headless: bool = False,
) -> T32Instance:
    """If something already listens on host:port, attach to it.
    Otherwise spawn a new instance (on the requested port, or an ephemeral free one).
    """
    if port is not None and is_port_open(host, port):
        return attach(host, port, node_name=node_name, arch=arch)
    return spawn(
        arch,
        port=port,
        node_name=node_name,
        t32sys=t32sys,
        headless=headless,
    )


def _parse_config_path_from_cmdline(cmdline: str) -> str | None:
    if not cmdline:
        return None
    parts = []
    current = []
    in_quotes = False
    quote_char = None
    for char in cmdline:
        if char in ('"', "'"):
            if in_quotes and char == quote_char:
                in_quotes = False
                quote_char = None
            elif not in_quotes:
                in_quotes = True
                quote_char = char
            else:
                current.append(char)
        elif char == ' ' and not in_quotes:
            if current:
                parts.append("".join(current))
                current = []
        else:
            current.append(char)
    if current:
        parts.append("".join(current))
        
    for i, part in enumerate(parts):
        if part.lower() in ("-c", "/c") and i + 1 < len(parts):
            return parts[i+1]
    return None


def _parse_rcl_port_from_config(config_path: str) -> int | None:
    try:
        with open(config_path, "r", encoding="utf-8", errors="ignore") as f:
            lines = f.readlines()
    except Exception:
        return None
        
    in_rcl_section = False
    port = None
    for line in lines:
        line = line.strip()
        if not line or line.startswith(";"):
            if in_rcl_section and port is not None:
                return port
            in_rcl_section = False
            continue
            
        if line.upper().startswith("RCL="):
            val = line.split("=", 1)[1].strip().upper()
            if val in ("NETASSIST", "NETTCP"):
                in_rcl_section = True
        elif in_rcl_section and line.upper().startswith("PORT="):
            try:
                port = int(line.split("=", 1)[1].strip())
            except Exception:
                pass
                
    if in_rcl_section and port is not None:
        return port
    return None


def _find_running_t32_processes_windows() -> list[dict]:
    import subprocess
    import json

    # Try using PowerShell Get-CimInstance, as wmic is deprecated and disabled in modern Windows 11
    try:
        ps_cmd = [
            "powershell",
            "-NoProfile",
            "-Command",
            "Get-CimInstance Win32_Process -Filter 'name like ''t32m%''' | "
            "Select-Object ProcessId, Name, ExecutablePath, CommandLine | ConvertTo-Json -Compress"
        ]
        out = subprocess.check_output(ps_cmd, stderr=subprocess.DEVNULL, text=True, timeout=8.0).strip()
        if out:
            data = json.loads(out)
            items = data if isinstance(data, list) else [data]
            mapped = []
            for item in items:
                pid = item.get("ProcessId")
                if pid:
                    mapped.append({
                        "Id": int(pid),
                        "Name": item.get("Name", ""),
                        "Path": item.get("ExecutablePath", ""),
                        "Cmd": item.get("CommandLine", "")
                    })
            return mapped
    except Exception:
        pass

    # Fallback to the original wmic method
    cmd = [
        "wmic",
        "process",
        "where",
        "name like 't32m%'",
        "get",
        "CommandLine,ExecutablePath,Name,ProcessId",
        "/format:list"
    ]
    try:
        out = subprocess.check_output(cmd, stderr=subprocess.DEVNULL, text=True, timeout=3.0)
        results = []
        current = {}
        for line in out.splitlines():
            line = line.strip()
            if not line:
                if current:
                    results.append(current)
                    current = {}
                continue
            if "=" in line:
                k, v = line.split("=", 1)
                current[k] = v
        if current:
            results.append(current)
        mapped = []
        for item in results:
            pid = item.get("ProcessId")
            if pid:
                try:
                    pid = int(pid)
                except ValueError:
                    continue
                mapped.append({
                    "Id": pid,
                    "Name": item.get("Name", ""),
                    "Path": item.get("ExecutablePath", ""),
                    "Cmd": item.get("CommandLine", "")
                })
        if mapped:
            return mapped
    except Exception:
        pass

    # PowerShell fallback
    cmd_ps = [
        "powershell",
        "-NoProfile",
        "-Command",
        "Get-CimInstance Win32_Process -Filter \"Name like 't32m%'\" | "
        "Select-Object @{Name='Id';Expression={$_.ProcessId}}, Name, @{Name='Path';Expression={$_.ExecutablePath}}, @{Name='Cmd';Expression={$_.CommandLine}} | "
        "ConvertTo-Json -Compress"
    ]
    try:
        out = subprocess.check_output(cmd_ps, stderr=subprocess.DEVNULL, text=True, timeout=8.0)
        if out.strip():
            import json
            data = json.loads(out)
            if isinstance(data, dict):
                return [data]
            elif isinstance(data, list):
                return data
    except Exception:
        pass
        
    return []


def _find_running_t32_processes_linux() -> list[dict]:
    import glob
    results = []
    for proc_dir in glob.glob("/proc/[0-9]*"):
        try:
            pid = int(Path(proc_dir).name)
            exe_link = os.readlink(os.path.join(proc_dir, "exe"))
            exe_name = os.path.basename(exe_link)
            if exe_name.startswith("t32m"):
                with open(os.path.join(proc_dir, "cmdline"), "rb") as f:
                    cmdline_bytes = f.read()
                parts = [p.decode("utf-8", errors="ignore") for p in cmdline_bytes.split(b"\x00") if p]
                cmdline_str = " ".join(parts)
                results.append({
                    "Id": pid,
                    "Name": exe_name,
                    "Path": exe_link,
                    "Cmd": cmdline_str
                })
        except Exception:
            continue
    return results


def _find_running_t32_processes_macos() -> list[dict]:
    import subprocess
    results = []
    try:
        out = subprocess.check_output(["ps", "-ax", "-o", "pid,comm,args"], stderr=subprocess.DEVNULL, text=True)
        for line in out.splitlines()[1:]:
            line = line.strip()
            if not line:
                continue
            parts = line.split(None, 2)
            if len(parts) >= 2:
                pid = int(parts[0])
                comm = parts[1]
                args = parts[2] if len(parts) > 2 else comm
                exe_name = os.path.basename(comm)
                if exe_name.startswith("t32m") or "t32m" in comm:
                    results.append({
                        "Id": pid,
                        "Name": exe_name,
                        "Path": comm,
                        "Cmd": args
                    })
    except Exception:
        pass
    return results


def detect_and_register_external_instances() -> None:
    """Scan the host OS for running t32m* processes, parse their config, and
    register any responsive instances that aren't already tracked.
    """
    from .t32_fake import is_fake_mode
    if is_fake_mode():
        return

    sysname = platform.system()
    processes = []
    if sysname == "Windows":
        processes = _find_running_t32_processes_windows()
    elif sysname == "Linux":
        processes = _find_running_t32_processes_linux()
    elif sysname == "Darwin":
        processes = _find_running_t32_processes_macos()
        
    for p in processes:
        pid = p.get("Id")
        if not pid:
            continue
            
        # Check if already tracked by PID
        already_tracked = False
        with _REGISTRY._lock:
            for inst in _REGISTRY._by_node.values():
                if inst.pid == pid:
                    already_tracked = True
                    break
        if already_tracked:
            continue
            
        exe_path = p.get("Path")
        cmd = p.get("Cmd")
        
        config_path = _parse_config_path_from_cmdline(cmd)
        if not config_path and exe_path:
            exe_dir = Path(exe_path).parent
            for candidate_dir in (exe_dir, exe_dir.parent.parent):
                candidate = candidate_dir / "config.t32"
                if candidate.exists():
                    config_path = str(candidate)
                    break
            
            if not config_path:
                candidate = Path("config.t32")
                if candidate.exists():
                    config_path = str(candidate.resolve())
                    
        if config_path:
            port = _parse_rcl_port_from_config(config_path)
            if port:
                host = "127.0.0.1"
                existing = _REGISTRY.get_by_endpoint(host, port)
                if not existing:
                    # Quick check if responsive before registering
                    if is_rcl_responsive(host, port, timeout_per_try=0.2):
                        exe_name = p.get("Name", "")
                        arch = "unknown"
                        for a in ARCH_BINARIES:
                            if ARCH_BINARIES[a] in exe_name:
                                arch = a
                                break
                        node = f"T32_auto_{port}"
                        inst = T32Instance(
                            node_name=node,
                            host=host,
                            port=port,
                            arch=arch,
                            pid=pid,
                            binary=exe_path or "(external)",
                            config_path=config_path,
                            log_path="",
                            work_dir="",
                            spawned_by_us=False,
                        )
                        _REGISTRY.register(inst)
