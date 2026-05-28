---
name: trace32-debugging
description: Drive Lauterbach TRACE32 (PowerView/simulator) via the trace32 MCP server to debug targets. Focuses on standard workflows: spawning a simulator, loading programs, getting/setting global and local variables (including complex C structures in SRAM), reading/writing memory and registers, setting breakpoints, finding source locations, reading source code, and checking the call stack. Use whenever the user wants to debug or inspect an active TRACE32 target.
---

# Debugging with TRACE32 (Standard Developer Workflows)

This skill provides step-by-step guidance on how to perform standard debugging operations using the `trace32` MCP server. Follow these recipes exactly — each operation should be **one tool call**, not a chain of manual PRACTICE commands.

---

## 0. Setup: Spawn Simulator & Load Program

Before inspecting any variables, you must have a running TRACE32 instance with a program loaded.

### A. Spawn a Simulator (if none is running)
1. Call `t32_list_presets()` to see available presets (e.g. `cortexm3-sim`).
2. Call `t32_spawn(preset="cortexm3-sim", t32sys="C:\\T32")`. This returns `node_name`, `host`, `port`.
3. Note the `node_name` — pass it to all subsequent tool calls.

### B. Load the ELF/AXF Program
* Call `t32_load_program(axf_path="<path_to_axf>", node_name="<node_name>")`.
* This loads symbols + code sections. After this, all symbol/variable tools work.

### C. Quick Check
* Call `t32_status(node_name="<node_name>")` to verify the CPU state.
* Call `t32_list_symbols(pattern="main")` to verify symbols are loaded.

---

## 1. Get and Set Variables (HLL Globals/Locals)

TRACE32 can read and write High-Level Language (C/C++) variables directly by name.

### A. Inspect a Complex Structure Variable (RECOMMENDED — 1 call)
* **Tool:** `t32_inspect_structure(name="<variable_name>")`
* **What it does:** Recursively retrieves ALL members, their C types, and their current values in a single structured JSON output. Works for structs, nested structs, arrays of structs, bitfields, pointers — everything.
* **Example:** `t32_inspect_structure(name="ast")` returns:
  ```json
  {
    "ok": true,
    "structure": {
      "name": "ast",
      "type": "strtype1",
      "members": [
        {"name": "word", "type": "char *", "value": "0x0 ** NULL"},
        {"name": "count", "type": "int", "value": "0"},
        {"name": "left", "type": "struct struct1 *", "value": "0x0 ** NULL"},
        {"name": "field1", "type": "int:2", "value": "0"}
      ]
    }
  }
  ```
* **NEVER** try to use `t32_eval`, `t32_run_command`, or compose PRACTICE scripts manually to read structure values. Always use this tool.

### B. Search for Global Variables by Pattern
* **Tool:** `t32_search_variables(pattern="<glob_pattern>")`
* **What it does:** Finds all global HLL variables matching the glob pattern (e.g. `*state*`, `my_var*`, `*`). Returns name, C type, address, and size for each.
* **Use this when:** You don't know the exact variable name, or want to list all globals in SRAM.

### C. Simple Scalar Variables
* **Tool:** `t32_var_view(name="<variable_name>")` or `t32_eval(expression="Var.VALUE(<variable_name>)")`.

### D. Set a Variable's Value
* **Tool:** `t32_run_command(line="Var.set <variable_name> = <value_or_expression>")`.
* **C-Style Structs/Arrays:** Target sub-fields directly.
  * *Example:* `t32_run_command(line="Var.set g_state = 2")`
  * *Example:* `t32_run_command(line="Var.set g_myStruct.isActive = TRUE")`

---

## 2. Read and Write Memory
For raw memory operations (reading/writing peripheral registers or buffers via address):

* **Read Memory:** Use `t32_read_memory(address=<addr>, length=<bytes>)`. This returns raw hex bytes and an ASCII hexdump.
* **Write Memory:** Use `t32_write_memory(address=<addr>, data_hex="<hex_string>")`.
  * *Example:* `t32_write_memory(address=0x20000000, data_hex="deadbeef")`

---

## 3. Read and Write CPU Registers
To read or write CPU registers (GPRs like R0-R15, PC, SP, etc.):

* **Read Registers:** Use `t32_read_registers()`. You can optionally specify a group (e.g. `group="GPR"`).
* **Write Register:** Use `t32_write_register(name="<reg>", value=<int>)`.
  * *Example:* `t32_write_register(name="R0", value=0)`
  * *Example:* `t32_write_register(name="PC", value=0x08000100)`

---

## 4. Set Breakpoints and Watchpoints
Control when the target halts based on code execution or memory access:

* **Code Breakpoint (by Symbol):** `t32_breakpoint(action="set", type="program", location="main")`
* **Code Breakpoint (by Line Number):** Specify the file and line number separated by a backslash.
  * *Example:* `t32_breakpoint(action="set", type="program", location="main.c\\42")`
* **Data Watchpoint (Halt on Write):** To stop the CPU whenever a global variable is modified:
  * *Example:* `t32_breakpoint(action="set", type="write", location="g_counter")`
* **Clear Breakpoint:** `t32_breakpoint(action="clear", location="main.c\\42")`

---

## 5. Find Source Location and Read Source Code
When the target stops, you can locate the exact line of source code that is running.

### A. Get Current File and Line
* **Tool:** Use `t32_eval(expression="y.line(Register(PC))")`.
* **Output:** Returns a string formatted as `"<file_path>:<line_number>"` (e.g., `"C:/Projects/src/main.c:115"`).

### B. Read the Source Code
Once you have the `<file_path>` from the step above:
* **Tool:** Use your local workspace file viewer (`view_file`) to open and read the file at that path. Do not try to read files through TRACE32; reading them directly from the local disk is faster and more reliable.

---

## 6. Check the Call Stack (Backtrace)
To check the chain of function calls leading to the current halt:

1. **Tool:** Run `t32_run_command(line="Frame /Locals /Caller")` to dump the backtrace and local variables to the TRACE32 message area.
2. **Tool:** Immediately call `t32_get_log(source="area")` to retrieve and read the printed backtrace from the message area.

---

## CRITICAL RULES

### Commands vs. Functions
* **Commands** (do actions: `Go`, `Step`, `Var.set`, `Frame`) → Call via `t32_run_command` or specialized tool. **Never** put them in `t32_eval`.
* **Functions** (evaluate values: `Register(PC)`, `Var.VALUE(x)`, `y.line(...)`) → Call via `t32_eval`.

### Variable Inspection — Use the Right Tool
* **For structures:** Always use `t32_inspect_structure`. ONE call. Do NOT compose PRACTICE scripts.
* **For finding variables:** Always use `t32_search_variables`. ONE call. Do NOT use `t32_eval` with `sYmbol.ForEach`.
* **For simple scalars:** Use `t32_var_view` or `t32_eval(expression="Var.VALUE(...)")`.
