---
name: trace32-debugging
description: Drive Lauterbach TRACE32 (PowerView / PowerDebug / simulator) through the trace32 MCP server to debug embedded targets — bring up a CPU, load an ELF/AXF, run/halt/step, set breakpoints and watchpoints, read/write registers and memory, inspect variables, and configure Cortex-M ETM/ITM trace. Use whenever the user mentions TRACE32, Lauterbach, PowerView, PowerDebug, PRACTICE/.cmm scripts, t32 commands, on-chip debugging, or wants to debug a Cortex-M/-A/-R, RISC-V, TriCore, or PowerPC target via this MCP.
---

# Debugging with TRACE32 (via the trace32 MCP)

This skill tells you how to drive Lauterbach TRACE32 through the `trace32` MCP
tools reliably. The MCP exposes typed tools (`t32_*`) plus a PRACTICE escape
hatch. Follow the rules below — weaker syntax mistakes (especially treating
commands as functions) are the #1 cause of failures.

## Prerequisites — there is NO implicit connection

Every tool except the docs tools needs a live TRACE32 instance. You must
**spawn or attach first**:

- **Spawn a new sim/PowerView**: `t32_spawn`. Prefer a preset over hand-written
  PRACTICE — call `t32_list_presets`, then `t32_spawn(preset="cortexm7-sim")`.
  Returns `node_name`, `host`, `port`, `pid`. Free port is auto-picked.
- **Attach to a running one**: `t32_attach(host, port)` (e.g. the user gave an IP).
- Check what's alive: `t32_list_instances`, `t32_status`.
- Always `t32_shutdown(node_name)` instances you spawned when finished.

Most tools accept `node_name` to target a specific instance; with one instance
it's inferred.

## Golden rules

1. **Commands are not functions.** `SYStem.Up`, `Go`, `Data.Set`, `Break.Set`
   are *commands* → run with `t32_control` / `t32_run_command`. `Register(PC)`,
   `Var.VALUE(x)`, `Data.Long(addr)`, `CPU()` are *functions* → evaluate with
   `t32_eval`. Calling a command as a function raises *"no function … exists,
   don't use commands as functions"*.
2. **Prefer typed tools over raw PRACTICE** when one fits — they parse output
   into structured fields: `t32_control`, `t32_read_memory`, `t32_write_memory`,
   `t32_read_registers`, `t32_write_register`, `t32_load_program`,
   `t32_breakpoint`, `t32_eval`, `t32_list_symbols`, `t32_var_view`.
   Use `t32_run_command` / `t32_run_practice` only when nothing else fits.
3. **Use a preset** to start a target instead of composing init PRACTICE.
4. The **free/unlicensed simulator caps a script at 50 commands** — keep
   startup scripts short.
5. When unsure of exact syntax, **`t32_search_manuals`** (vector search) or
   **`t32_lookup_command`** (exact command lookup) before guessing.

## Core workflow

```
t32_spawn(preset="cortexm7-sim")          # bring up the target
t32_load_program(path="app.axf")          # load symbols + code, PC at entry
t32_breakpoint(action="set", type="program", location="main")
t32_control(action="run")                 # Go
t32_status()                              # halted at main?
t32_read_registers()                      # inspect
t32_var_view(name="myStruct")             # typed variable
t32_control(action="step_over")
...
t32_shutdown(node_name=...)               # clean up
```

## Tool ↔ PRACTICE map

| Goal                 | Tool                                            | PRACTICE under the hood |
|----------------------|-------------------------------------------------|-------------------------|
| bring up CPU         | `t32_spawn(preset=...)` / `t32_reset`           | `SYStem.CPU` + `SYStem.Up` |
| load program         | `t32_load_program`                              | `Data.LOAD.Elf` |
| run / halt           | `t32_control(action="run"/"halt")`              | `Go` / `Break` |
| step                 | `t32_control(action="step"/"step_over"/"step_out"/"step_asm")` | `Step` / `Step.Over` / `Go.Return` / `Step.Asm` |
| breakpoint           | `t32_breakpoint(action,type,location,condition)`| `Break.Set` |
| read/write reg       | `t32_read_registers` / `t32_write_register`     | `Register(...)` / `Register.Set` |
| read/write memory    | `t32_read_memory` / `t32_write_memory`          | `Data.dump` / `Data.Set` |
| evaluate expression  | `t32_eval`                                      | `PRINT`/functions |
| variables / symbols  | `t32_var_view`, `t32_list_symbols`              | `Var.View`, `sYmbol.List` |
| anything else        | `t32_run_command` / `t32_run_practice`          | arbitrary PRACTICE |

## Recipes

**Bring up a Cortex-M7 sim and stop at main**
```
t32_spawn(preset="cortexm7-sim")
t32_load_program(path="firmware.axf")
t32_breakpoint(action="set", type="program", location="main")
t32_control(action="run")
```

**Read a variable / memory**
```
t32_var_view(name="g_counter")
t32_read_memory(address=0x20000000, length=64)   # hex + decoded + ASCII
t32_eval(expression="Var.VALUE(g_state)")
```

**Write memory / register**
```
t32_write_memory(address=0x20000000, hex_bytes="deadbeef")
t32_write_register(name="R0", value=1)
```

**Data watchpoint (break on write to a variable)**
```
t32_breakpoint(action="set", type="write", location="g_flag")
t32_control(action="run")
```

**Real hardware (SWD via USB PowerDebug)** — use a hardware preset, e.g.
`t32_spawn(preset="stm32h743-swd")`, or `t32_spawn(arch="cortexm", backend="usb",
startup_script="SYStem.CPU STM32H743ZI\nSYStem.CONFIG.DEBUGPORTTYPE SWD\nSYStem.MemAccess DAP\nSYStem.Up")`.
For Ethernet PowerDebug use `backend="net", target_host="<ip-or-name>"`.

## Cortex-M trace (important nuance)

- Cortex-M **ETM is instruction-only — no data trace via ETM.** Program-flow
  trace: `Trace.METHOD Analyzer` (or `CAnalyzer` off-chip), `ETM.ON`,
  `Trace.List`.
- **Data trace** (variable values/addresses) comes from **DWT → ITM**, with
  only **4 DWT comparators**: `ITM.DataTrace ON`, then
  `Var.Break.Set <var> /TraceData` per variable.
- Multi-core M7 is heterogeneous **AMP**: one PowerView instance per core.
  Share an off-chip trace port with matching
  `SYStem.CONFIG.<component>.Name "shared-x"` + `PROGramable OFF` on the slave.
- Off-chip calibration (PowerTrace/AutoFocus): `Trace.AutoFocus`,
  `Trace.ShowFocus`; detect CoreSight base addresses with
  `SYStem.CONFIG.state /COmponents`.

Run any of these via `t32_run_command`, and `t32_search_manuals` for device
specifics.

## Troubleshooting

- **A tool times out / `t32_search_manuals` is slow on first use** → the
  embedding model is downloading once; run `trace32-mcp-prefetch` in a terminal.
  Tools return a structured `TimeoutError` rather than hanging forever.
- **`t32_spawn` fails to bind the RCL port** → inspect the planned config with
  `t32_render_config`, verify the PowerDebug is connected/reachable.
- **Something printed an error but the tool said ok / vice-versa** → read the
  AREA log with `t32_get_log(source="area")`, or `t32_healthcheck`.
- **Don't know a command's exact spelling/options** → `t32_lookup_command`.
