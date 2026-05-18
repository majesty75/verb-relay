# Trace32-MCP

A **Model Context Protocol** server that lets any MCP-capable AI assistant
drive **Lauterbach TRACE32 PowerView / PowerDebug** — spawn or attach to
PowerView instances, load AXF + BIN, set breakpoints, step, read memory,
evaluate symbols, run arbitrary PRACTICE (.cmm) scripts, and search the
bundled TRACE32 manuals when the LLM doesn't know the syntax yet.

Supports macOS, Linux, and **Windows** (see [docs/WINDOWS.md](docs/WINDOWS.md)).

## Install — `uvx`, no clone needed

Install directly from the git repo — `uv`/`uvx` builds and runs in an
isolated environment:

```bash
# one-off run (recommended for MCP clients)
uvx --from "git+https://github.com/<OWNER>/<REPO>.git" trace32-mcp

# persistent install
uv tool install --from "git+https://github.com/<OWNER>/<REPO>.git" trace32-mcp
trace32-mcp   # now on $PATH
```

(Use the `https://` variant with a PAT if you don't have SSH keys configured.)

uvx clones the repo into its own cache, builds a wheel from the
`Trace32-MCP/` subdirectory, and runs the `trace32-mcp` entry point in an
isolated environment. The bundled manuals DB (~191 MB sharded across 5
sqlite-vec files at `src/trace32_mcp/db/`) is shipped inside the wheel — no
extra download or DB rebuild needed.

### When you DO want the source checkout

```bash
git clone <repo-url>
cd Trace32-MCP

python -m venv .venv && . .venv/bin/activate
pip install -e .

# only if you want to rebuild the manuals DB from help.zip
pip install -e '.[build]'
```

## The 24 tools

### Lifecycle — **the AI must call `t32_spawn` or `t32_attach` first**

| Tool | Purpose |
|---|---|
| `t32_spawn` | Launch a NEW PowerView/Sim. Auto-picks free port, generates `config.t32`, captures stderr to log file. |
| `t32_attach` | ATTACH to a TRACE32 already running at host:port (user gave you an IP). |
| `t32_list_instances` | List every tracked instance (spawned + attached) with alive/uptime. |
| `t32_shutdown` | RCL Quit → SIGTERM (`force=true` → SIGKILL / TerminateProcess) for spawned instances. |
| `t32_disconnect` | Drop the cached RCL client without killing the T32 process. |
| `t32_status` | Target state (down/halted/running) + CPU. No args ⇒ lists all instances. |
| `t32_get_log` | Subprocess stdout/stderr + the dedicated MCPLOG AREA window. Use after errors. |
| `t32_healthcheck` | TCP open → RCL handshake → state query → echo command → AREA readable. Per-step latency + pass/fail. |

### Program / control / inspect

| Tool | Purpose |
|---|---|
| `t32_load_program` | `Data.LOAD.Elf` + optional `Data.LOAD.Binary`. |
| `t32_reset` | `SYStem.RESet` + `SYStem.Mode <Up\|Go\|Attach\|Down\|…>`. |
| `t32_control` | run / halt / step / step_over / step_out / step_asm. |
| `t32_breakpoint` | set / clear / clear_all / list / enable / disable. Types: program/read/write/rw. Optional condition. |
| `t32_eval` | Any PRACTICE expression. |
| `t32_read_memory` / `t32_write_memory` | Direct memory access (hex + decoded). |
| `t32_read_registers` / `t32_write_register` | CPU regs. |
| `t32_list_symbols` / `t32_var_view` | Symbol nav, typed variable view (structs/arrays). |

### Scripting escape hatch

| Tool | Purpose |
|---|---|
| `t32_run_practice` | Run a .cmm body or path. **Universal escape hatch** — PRACTICE does nearly anything. |
| `t32_run_command` | Single PRACTICE line. |

### Docs (no T32 connection required)

| Tool | Purpose |
|---|---|
| `t32_search_manuals` | Vector-search 17,310+ chunks across the Lauterbach manual archive. |
| `t32_lookup_command` | Precise alphabetical lookup by command name. |

### Extra

| Tool | Purpose |
|---|---|
| `t32_screenshot` | `PRINT.IMAGE` window/screen → base64 PNG. |

## Error feedback (no visual debugger required)

Every action tool returns a structured result with:

- `ok` — false if T32 reported an error
- `result.text` — AREA window output
- `result.mode_flags` — `ERROR / WARN / INFO / STATE / TARGET_INFO` decoded from `T32_GetMessage` mode bits
- `result.practice_state` — `0=idle / 1=running / 2=ERROR` from `T32_GetPracticeState`
- `result.error` — error message text when something failed

`t32_get_log` reads both the spawned subprocess stderr we capture and the
**MCPLOG** AREA we auto-create at every connect (so messages are captured
even when no AREA window is visible in PowerView).

## Spawn vs attach (explicit, never inferred)

The model decides:

```
user: "Connect to our lab debugger at 10.0.5.7:20000"
→ AI calls t32_attach(host="10.0.5.7", port=20000)

user: "Spin up a Cortex-M sim and load this firmware"
→ AI calls t32_spawn(arch="cortexm") then t32_load_program(...)

user: "Use the one I already started" (no IP given)
→ AI calls t32_list_instances(), picks one, operates by node_name
```

After spawn/attach you don't repeat host/port — pass `node_name` (or omit
entirely to use the most-recently-registered instance).

## Testing

The repo has 3 tiers of tests:

```bash
# Tier 1: fake mode, no TRACE32 install needed (27 tests, ~12 s)
pytest tests/

# Tiers 2+3: live tests against a real TRACE32 sim
T32_MCP_LIVE=1 pytest tests/ -v -k live
```

- **Tier 1 (`test_fake_e2e.py`, `test_server_smoke.py`)**: drives every tool via a `FakeT32Client` (activated by `T32_MCP_FAKE=1`). Verifies schemas, tool wiring, lifecycle registry, error propagation, AREA capture.
- **Tier 2 (`test_live_pre_go.py`)**: spawn a real sim and exercise pre-`Go` ops (healthcheck, load, list_symbols, breakpoint, eval, AREA read). The free sim allows unlimited PRACTICE commands until the first `Go`/`Step`.
- **Tier 3 (`test_live_oneshot.py`)**: one full debug loop (load → break → go → halt → regs/memory/eval → shutdown). Stays under the 50-command post-`Go` limit of the unlicensed sim.

Auto-skips Tier 2+3 without `T32_MCP_LIVE=1`.

## Building the manuals DB yourself

```bash
pip install '.[build]'

# 1. Fetch the source PDFs (~325 MB extracted, gitignored)
mkdir -p data/raw
curl -L -o /tmp/help.zip "https://repo.lauterbach.com/cgi-bin/update.pl?file=help.zip"
unzip -d data/raw /tmp/help.zip

# 2. Build sharded DBs (parallel parse + MPS embed + heuristic OCR on image pages)
t32-rag ingest --full --shards --shard-dir src/trace32_mcp/db \
               --chunk-chars 800 --overlap 100 --workers 6 \
               --ocr --ocr-workers 4

# 3. Search from the CLI
t32-rag query "set a hardware breakpoint on a memory write"
t32-rag lookup Data.LOAD.Elf
```

Build options:

| Flag | Default | Notes |
|---|---|---|
| `--chunk-chars` | 800 | Smaller = more precise retrieval, bigger DB |
| `--overlap` | 100 | Char overlap between consecutive chunks |
| `--workers` | auto (60% of CPU cores) | PDF parser threads |
| `--ocr` | off | Heuristic OCR on image-dominant pages (requires `tesseract` on PATH) |
| `--ocr-workers` | same as --workers | CPU-bound, separate from parse pool |
| `--shards` | off | Split by category into 5 DBs each under GitHub's 100 MB cap |

## Layout

```
Trace32-MCP/
├── src/trace32_mcp/
│   ├── server.py             # MCP entrypoint + 24-tool registry
│   ├── config.py             # default endpoints + DB path env vars
│   ├── session.py            # per-endpoint client cache + ensure_instance
│   ├── t32_bridge.py         # locate + load Lauterbach's t32api shared lib (mac/lin/win)
│   ├── t32_client.py         # typed RCL wrapper w/ error detection + MCPLOG
│   ├── t32_fake.py           # FakeT32Client for T32_MCP_FAKE=1
│   ├── t32_process.py        # spawn / attach / shutdown (Windows + POSIX)
│   ├── tools/                # one module per concern group
│   ├── manuals/              # runtime: search the bundled vector DB
│   ├── manuals_build/        # optional [build] extra: ingest + OCR + t32-rag CLI
│   └── db/                   # bundled sharded sqlite-vec DBs (manuals_*.db)
├── tests/                    # 3 tiers — Tier 1 fake, Tier 2 pre-Go live, Tier 3 one-shot live
├── docs/
│   └── WINDOWS.md            # Windows-specific install + RCL setup + live test
├── pyproject.toml
└── README.md
```

## Config reference

| Env var | Default | Notes |
|---|---|---|
| `T32SYS` | auto-probed | TRACE32 install root |
| `T32_MANUALS_DB` | bundled shards | Override DB path (comma/colon-separated for multiple shards) |
| `T32_MANUALS_DB_URL` | — | Auto-download URL when no DB present |
| `T32_MANUALS_MODEL` | `BAAI/bge-base-en-v1.5` | Embedding model |
| `T32_MANUALS_DEVICE` | `auto` | mps / cuda / cpu |
| `T32_MANUALS_PARALLEL` | `1` | Parallel sharded search (set `0` to serialize) |
| `T32_MCP_FAKE` | — | Set `1` to swap in the FakeT32Client (zero install, CI) |
| `T32_MCP_LIVE` | — | Set `1` to enable live TRACE32 tests |
| `T32_DEMO_AXF` | auto-search | Path to a demo .axf for live tests |
| `TRACE32_MCP_LOG` | `INFO` | Server log level |

## Caveats

- The TRACE32 free simulator caps at **50 PRACTICE commands after the first `Go`/`Step`**. Pre-`Go` is unlimited. Hardware PowerDebug license activates the sim too.
- `t32_screenshot` requires the MCP and T32 to share a filesystem (true for local sim; you'll need to fetch the PNG yourself for a remote PowerDebug box).
- The `t32api` library serialises calls in module-global state — we wrap every call in a per-client lock.
- Spawned T32 processes are killed at MCP exit via an atexit hook. If Python dies hard you may need to kill orphans manually.
