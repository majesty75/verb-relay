---
name: trace32-debugging
description: Drive Lauterbach TRACE32 (PowerView / PowerDebug / simulator) through the trace32 MCP server to debug embedded targets — bring up a CPU, load an ELF/AXF, run/halt/step, set breakpoints and watchpoints, read/write registers and memory, inspect variables, and configure Cortex-M ETM/ITM trace. Use whenever the user mentions TRACE32, Lauterbach, PowerView, PowerDebug, PRACTICE/.cmm scripts, t32 commands, on-chip debugging, or wants to debug a Cortex-M/-A/-R, RISC-V, TriCore, or PowerPC target via this MCP.
---

# Debugging with TRACE32 (via the trace32 MCP)

This skill provides essential guidelines to drive Lauterbach TRACE32 through the `trace32` MCP tools reliably. The MCP exposes typed tools (`t32_*`) and a PRACTICE scripting escape hatch. Follow these rules to avoid common LLM pitfalls—syntax mistakes are the #1 cause of tool failures.

## 1. Instance Discovery & Lifecycle — NO Implicit Connection

Every debugging tool requires an active TRACE32 instance. You must connect to or discover an instance before calling inspect or control tools:

* **Auto-Discovery (Always Check First):** Run `t32_list_instances` to see if the user already has a TRACE32 debugger open on their machine. If you see an auto-detected instance (e.g. `T32_auto_20000`), **do not spawn a new one**. Use `t32_status(node_name="T32_auto_20000")` to inspect it and target it directly.
* **Spawn a new simulator/PowerView:** If no running instance is found or if the user requests a fresh simulator, call `t32_spawn`.
  * **Tip:** Always try to use a preset by calling `t32_list_presets` first, then calling `t32_spawn(preset="cortexm7-sim")`. This guarantees a correct startup configuration.
* **Attach to a known host/port:** Call `t32_attach(host, port)` (e.g. if the user gives you a specific remote IP and port).
* **Targeting Multiple Instances:** If multiple instances are running (e.g. in AMP or multi-core debug configurations), each core runs on its own port. You must pass `node_name` to target a specific core.
* **Clean up:** Always call `t32_shutdown(node_name=...)` on instances you spawned when your task is complete.

---

## 2. CRITICAL: Commands vs. Functions

TRACE32 PRACTICE has a strict syntactic distinction between **Commands** (which perform actions and return nothing) and **Functions** (which calculate/retrieve values). Calling a command as a function will crash the tool.

### A. Commands (Perform Actions)
* **PRACTICE Examples:** `SYStem.Up`, `Go`, `Step`, `Register.Set`, `Data.Set`, `Break.Set`.
* **MCP Tool:** Run via `t32_run_command(command="<command>")` or specialized typed control tools.
* **CRITICAL RULE:** **NEVER** run commands inside `t32_eval`. Doing so will fail with a "no function exists" error.

### B. Functions (Retrieve Values)
* **PRACTICE Examples:** `Register(<name>)`, `Var.VALUE(<expr>)`, `Data.Long(<addr>)`, `CPU()`.
* **MCP Tool:** Run via `t32_eval(expression="<function>")`.
* **CRITICAL RULE:** Functions are surrounded by parenthesis or evaluate expressions. They must be executed via `t32_eval` to get their value.

### Correct vs. Incorrect Examples

| Action | ❌ INCORRECT (Will Fail) | ✔️ CORRECT (Will Pass) |
| :--- | :--- | :--- |
| **Bring up CPU** | `t32_eval(expression="SYStem.Up")` | `t32_run_command(command="SYStem.Up")` or `t32_reset(mode="Up")` |
| **Get Register PC** | `t32_run_command(command="Register(PC)")` | `t32_eval(expression="Register(PC)")` or `t32_read_registers(group="GPR")` |
| **Run / Go** | `t32_eval(expression="Go")` | `t32_control(action="run")` or `t32_run_command(command="Go")` |
| **Read Variable** | `t32_run_command(command="Var.VALUE(my_var)")` | `t32_eval(expression="Var.VALUE(my_var)")` or `t32_var_view(name="my_var")` |
| **Read Memory** | `t32_run_command(command="Data.Long(D:0x20000000)")` | `t32_eval(expression="Data.Long(D:0x20000000)")` or `t32_read_memory(...)` |

---

## 3. Golden Rules for Tool Selection

1. **Prefer typed tools over raw PRACTICE:** Typed tools parse raw outputs into clean, structured JSON fields. Use the following map before resorting to `t32_run_command`:
   * To control execution (run, step, halt): Use `t32_control`.
   * To load code/symbols: Use `t32_load_program`.
   * To manage breakpoints: Use `t32_breakpoint`.
   * To read/write memory: Use `t32_read_memory` / `t32_write_memory`.
   * To read/write registers: Use `t32_read_registers` / `t32_write_register`.
   * To view complex variables/structs: Use `t32_var_view`.
2. **Use RAG Search before guessing syntax:** If you need to perform an operation not covered by a typed tool (e.g. enabling trace, configuring MMUs, setting special watchpoints), call `t32_lookup_command` (exact name check) or `t32_search_manuals` (vector search) to find the correct command syntax.
3. **PRACTICE Script Limitations:** The free/unlicensed TRACE32 simulator limits inline scripts to **50 commands**. Keep startup and initialization scripts brief.
4. **Script Automation:** For complex initialization, write the PRACTICE script to a temporary `.cmm` file using standard file writing tools, and then execute it in one call using `t32_run_practice(path="<path_to_script>")`.

---

## 4. Diagnostics & Troubleshooting

* **Tool timeouts:** Vector searches (`t32_search_manuals`) can be slow on first use while the embedding model is downloaded. If you experience tool timeouts, prompt the user to run `trace32-mcp-prefetch` in their terminal to pre-populate the model.
* **Command printed error, but tool returned OK:** Check the actual debugger command area log using `t32_get_log(source="area")`. Sometimes TRACE32 prints configuration syntax warnings to its GUI AREA log instead of returning an error code.
* **`t32_spawn` port binding failures:** Run `t32_render_config` to dry-run the generated config file. Check that the port is free, there are no firewall issues blocking UDP, and the PowerDebug hardware is plugged in and powered on.
