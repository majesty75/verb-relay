---
name: trace32-debugging
description: Drive Lauterbach TRACE32 (PowerView/simulator) via the trace32 MCP server to debug targets. Focuses on standard workflows: getting/setting global and local variables, reading/writing memory and registers, setting breakpoints, finding source locations, reading source code, and checking the call stack. Use whenever the user wants to debug or inspect an active TRACE32 target.
---

# Debugging with TRACE32 (Standard Developer Workflows)

This skill provides step-by-step guidance on how to perform standard debugging operations using the `trace32` MCP server. Follow these recipes to get/set variables, read memory/registers, manage breakpoints, find source code locations, and check call stacks.

---

## 1. Get and Set Variables (HLL Globals/Locals)

TRACE32 can read and write High-Level Language (C/C++) variables directly by name.

### A. Get a Variable's Value
* **Best Tool:** Use `t32_var_view(name="<variable_name>")`. This automatically formats simple values as well as complex structures and arrays.
* **Expression Option:** Use `t32_eval(expression="Var.VALUE(<variable_name>)")` for simple variables.

### B. Set a Variable's Value
There is no typed tool for setting HLL variables. You must run the `Var.set` command:
* **Tool:** Use `t32_run_command(command="Var.set <variable_name> = <value_or_expression>")`.
* **C-Style Structs/Arrays:** You can target sub-fields directly.
  * *Example:* `t32_run_command(command="Var.set g_state = 2")`
  * *Example:* `t32_run_command(command="Var.set g_myStruct.isActive = TRUE")`

---

## 2. Read and Write Memory
For raw memory operations (reading/writing peripheral registers or buffers via address):

* **Read Memory:** Use `t32_read_memory(address=<addr>, length=<bytes>)`. This returns raw hex bytes and an ASCII hexdump.
* **Write Memory:** Use `t32_write_memory(address=<addr>, hex_bytes="<hex_string>")`.
  * *Example:* `t32_write_memory(address=0x20000000, hex_bytes="deadbeef")`

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

1. **Tool:** Run `t32_run_command(command="Frame /Locals /Caller")` to dump the backtrace and local variables to the TRACE32 message area.
2. **Tool:** Immediately call `t32_get_log(source="area")` to retrieve and read the printed backtrace from the message area.

---

## CRITICAL: Commands vs. Functions Rule
Weaker models frequently fail by calling commands as functions. Always remember:
* **Commands** (do actions: `Go`, `Step`, `Var.set`, `Frame`) -> Call via `t32_run_command` or specialized tool. **Never** put them in `t32_eval`.
* **Functions** (evaluate values: `Register(PC)`, `Var.VALUE(x)`, `y.line(...)`) -> Call via `t32_eval`.
