---
name: trace32-expert
description: Advanced reference guide focusing on standard debugging tasks (variable get/set, memory classes, line number syntax, register overrides, and call stack details). Use this to compose precise command strings for normal debugging operations.
---

# TRACE32 Standard Debugging Syntax & Details

This guide details the precise syntax, access classes, and formats for performing standard debugging tasks in TRACE32. Use these details to construct syntactically correct parameters for the MCP tools.

---

## 1. Advanced HLL Variable Access (Var.set & Var.VALUE)

When getting or setting variables via `t32_var_view`, `t32_eval`, or `Var.set`, use these C/C++ syntactic patterns:

### A. Reading Values
* Use `Var.VALUE(<expr>)` to retrieve a value as an integer/string.
  * *Pointer Dereference:* `Var.VALUE(*g_pBuffer)`
  * *Array Index:* `Var.VALUE(g_array[5])`
  * *Structure Field:* `Var.VALUE(g_device.config->baudrate)`

### B. Writing Values (Var.set)
Assign values using standard C syntax operators.
* **Basic Assignment:** `Var.set g_counter = 0`
* **Pointers:** `Var.set g_pData = &g_buffer[0]`
* **Arrays/Structs:**
  * `Var.set g_items[2].status = 0xAB`
  * `Var.set g_rect = {0, 0, 100, 100}`
* **Type Casting:** Use standard C-casts if TRACE32 demands type matching.
  * `Var.set g_flags = (uint32_t)0xFFFFFFFF`
  * `Var.set *(uint8_t*)0x200000A0 = 0xFF` (directly writes to memory via C-style pointer cast)

---

## 2. Memory Address Access Classes
In TRACE32, addresses are often prefixed with an **Access Class** indicating which memory bus or space to target. If a raw address fails, prepend the correct class:

| Prefix | Meaning | Usage Example |
| :--- | :--- | :--- |
| **`D:`** | Data Memory (RAM) | `D:0x20000000` |
| **`P:`** | Program Memory (Flash/ROM) | `P:0x08000000` |
| **`A:`** | Absolute physical memory | `A:0xE000ED00` |
| **`E:`** | Emulator/Virtual memory | `E:0x1000` |
| **`DAP:`** | CoreSight Debug Access Port | `DAP:0x40002000` |

* **MCP Memory Tools:** The tools `t32_read_memory` and `t32_write_memory` automatically handle address classes if you supply them in the address field (e.g. `address="D:0x20000000"`).

---

## 3. Register Reference Syntax
When reading or setting registers via `t32_write_register` or `t32_eval`:

* **In Expressions:** Always wrap the register name in the `Register()` function to retrieve its value:
  * *Correct:* `t32_eval(expression="Register(PC)")`
  * *Correct:* `t32_eval(expression="Register(R0) + 4")`
* **Writing Registers:** The `t32_write_register` tool accepts the plain name:
  * *Example:* `t32_write_register(name="SP", value=0x20004000)`

---

## 4. Breakpoint & Source Line Syntax (File\Line)

When specifying source locations for breakpoints, the program counter, or target addresses, follow these exact syntax rules:

* **File and Line format:** Use the syntax `<filename>\\<line_number>`. The double-backslash is mandatory to escape the separator.
  * *Example:* `main.c\\54`
  * *Example:* `drivers/uart.c\\108`
* **Address from Line:** Use the function `y.address(<file>\\<line>)` to resolve a line to its memory address.
  * *Example:* `t32_eval(expression="y.address(main.c\\54)")`
* **Setting PC to a Line:**
  * *Example:* `t32_run_command(command="Register.Set PC y.address(main.c\\54)")`

---

## 5. Call Stack Inspection Details (Frame Command)

To inspect local variables and callers at different levels of the call stack:

* **List Stack Frames:** `Frame.List` (standard call stack overview).
* **Dump Frame with Locals:** `Frame /Locals /Caller` dumps all caller functions and their local variables to the log.
* **Navigate Stack Frames:**
  * `Frame.down` — Move focus to the called function (down one level).
  * `Frame.up` — Move focus to the caller function (up one level).
  * `Frame.view` — View the variables of the currently selected stack frame.
* **Retrieve Backtrace:** Run the `Frame` command of choice, then call `t32_get_log(source="area")`.
