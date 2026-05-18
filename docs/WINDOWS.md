# Trace32-MCP on Windows

Tested target: **Windows 10/11 x64 with a real TRACE32 installation**.

This is the deployment story the project was designed around: the LLM runs
remotely (or locally), the MCP server runs on the same Windows box as TRACE32,
and the MCP spawns or attaches to PowerView / PowerDebug instances over RCL.

---

## 1. Prerequisites

| What | Why | Where to get it |
|---|---|---|
| **Python 3.10+** | Runtime for the MCP | https://www.python.org/downloads/ (check "Add to PATH") |
| **TRACE32 installation** | The actual debugger / simulator | Already installed by you. Should contain `C:\T32\demo\api\python\t32api.py` and `C:\T32\demo\api\python\t32api64.dll`. |
| **Tesseract** (only if you'll rebuild the manuals DB) | OCR for screenshot pages | https://github.com/UB-Mannheim/tesseract/wiki — add to PATH |
| **git** | To clone this repo | https://git-scm.com/ |

You **do not** need a TRACE32 Simulator License unless you want to run more
than 50 PRACTICE commands after the first `Go` / `Step`. Hardware PowerDebug
license activates everything automatically.

---

## 2. Install the MCP

### Option A — `uvx` (recommended, no clone, no venv)

Install `uv` first if you don't have it:
```powershell
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

Then either of these — `uvx` clones the private repo into its own cache,
builds the wheel from the `Trace32-MCP/` subdirectory, and runs the entry
point. The 191 MB sharded manuals DB ships inside the wheel.

```powershell
# one-off (good for `.mcp.json` configuration)
uvx --from "git+https://github.com/<OWNER>/<REPO>.git" trace32-mcp

# or persistent: installs to %USERPROFILE%\.local\bin\trace32-mcp.exe
uv tool install --from "git+https://github.com/<OWNER>/<REPO>.git" trace32-mcp
trace32-mcp   # now on $PATH
```

If SSH isn't set up on Windows, use HTTPS + a PAT:
```powershell
uvx --from "git+https://<USER>:<PAT>@github.com/<OWNER>/<REPO>.git" trace32-mcp
```

### Option B — clone + editable install (use if you want to modify the code)

```powershell
git clone <repo-url>
cd Trace32-MCP

python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -U pip
pip install -e .

# Optional, only if you'll rebuild the manuals DB:
pip install -e ".[build]"
```

---

## 3. Point the MCP at your TRACE32 install

The MCP auto-probes common paths (`C:\T32`, `~\t32`, `/Applications/t32`).
If your install is elsewhere, set:

```powershell
$env:T32SYS = "D:\Tools\T32"
```

Quick verification — should print a real path without error:

```powershell
.venv\Scripts\python -c "from trace32_mcp.t32_bridge import _resolve_t32sys, _resolve_lib; r = _resolve_t32sys(None); print('root:', r); print('lib :', _resolve_lib(r))"
```

You should see something like:
```
root: C:\T32
lib : C:\T32\demo\api\python\t32api64.dll
```

---

## 4. Configure TRACE32 for RCL

The MCP talks to PowerView over the Lauterbach Remote Control protocol. Edit
the `config.t32` of any instance you want the MCP to drive (or let the MCP
spawn its own — it generates the right config automatically).

For a **manually-launched** PowerView, add to its `config.t32`:

```
RCL=NETASSIST
PORT=20000
PACKLEN=1024
```

then restart PowerView. The MCP defaults to `127.0.0.1:20000`.

For **MCP-spawned** PowerView, no manual config needed — `t32_spawn` writes a
config file with auto-picked free port.

---

## 5. Sanity check (no T32 needed)

```powershell
pytest tests\
# Expected: 27 passed, 8 skipped (live tests gated behind T32_MCP_LIVE=1)
```

---

## 6. Live test against your installed TRACE32

```powershell
$env:T32_MCP_LIVE = "1"
$env:T32SYS = "C:\T32"
pytest tests\ -v -k live
```

This runs:
1. `test_live_pre_go.py` — spawn sim, healthcheck, load demo AXF, list symbols, set breakpoint, read AREA log. Stays below the free-sim `Go`/`Step` limit.
2. `test_live_oneshot.py` — full debug loop: load → Break.Set → Go → halt → register/memory inspect → shutdown. Fits in <50 PRACTICE commands.

If you have an ARM demo AXF elsewhere:
```powershell
$env:T32_DEMO_AXF = "C:\T32\demo\arm\compiler\arm\sieve.axf"
```

---

## 7. Wire it into an MCP client

### Claude Code

`%APPDATA%\Claude\.mcp.json` — uvx style (no clone needed):

```json
{
  "mcpServers": {
    "trace32": {
      "command": "uvx",
      "args": [
        "--from",
        "git+https://github.com/<OWNER>/<REPO>.git",
        "trace32-mcp"
      ],
      "env": {
        "T32SYS": "C:\\T32"
      }
    }
  }
}
```

Or if you used `uv tool install` / `pip install`:
```json
{
  "mcpServers": {
    "trace32": {
      "command": "trace32-mcp",
      "env": { "T32SYS": "C:\\T32" }
    }
  }
}
```

### Cursor / Cline / others
Point the MCP host at `trace32-mcp.exe` (in your venv's `Scripts\` dir) over
stdio.

---

## 8. First conversation with the AI

Things the LLM is expected to do on first interaction:

1. Call `t32_list_instances` — empty list confirms a fresh start.
2. Call `t32_spawn` with `arch="arm"` (or `cortexm`) — launches a sim, returns `node_name`.
3. Call `t32_healthcheck` — verifies TCP / RCL / state / echo / AREA log all work.
4. Call `t32_load_program` with your AXF path.
5. Then whatever the user asked for: breakpoint, run, eval, etc.

`t32_search_manuals` works even before any instance is spawned — useful when
the AI needs to look up syntax first.

---

## 9. Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `Could not locate a TRACE32 installation` | `T32SYS` unset and `C:\T32` doesn't exist | `$env:T32SYS = "your-install-path"` |
| `Could not find a t32api shared library` | TRACE32 install is missing the API folder | Re-run the Lauterbach installer with "API" component checked |
| `T32_Attach failed (code=...)` | Nothing listening on the requested port | Check `config.t32` has `RCL=NETASSIST` and `PORT=<your port>` |
| `tcp_port_open: false` from `t32_healthcheck` | Firewall blocking 127.0.0.1:port | Allow Python in Windows Firewall, or use a different port |
| MCP-spawned T32 dies right away | License missing for that CPU family | Use a different arch (sim allows ARM unlicensed) or set up Reprise License Mgr |
| "OCR fragment" results in search | These are screenshot-extracted texts | Working as designed — they're prefixed `[OCR pN]` |

For deeper diagnostics, the MCP logs to stderr at `INFO` by default:

```powershell
$env:TRACE32_MCP_LOG = "DEBUG"
```

And every spawn captures its TRACE32 subprocess stdout/stderr — use
`t32_get_log` from the AI side.
