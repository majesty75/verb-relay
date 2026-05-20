# TRACE32 quick reference (for LLMs driving this MCP)

This file is a cheat-sheet of the most common Lauterbach TRACE32 / PRACTICE
commands so you can compose scripts **without** calling `t32_search_manuals`
for every routine task. Reach for the manuals search only for advanced or
device-specific topics. Default working target here is **Cortex-M7** (sim or
PowerDebug); examples assume that unless noted.

---

## 0. Golden rules (read first)

- **Commands are not functions.** `SYStem.Up`, `Go`, `Data.Set` are *commands*
  (run with `t32_run_command` / `t32_control`). `Register(PC)`, `Var.VALUE(x)`,
  `CPU()` are *functions* (evaluate with `t32_eval`). Do **not** write
  `PRACTICE.STATE()` or `Go()` — calling a command as a function raises
  *"no function … exists, don't use commands as functions"*.
- **Prefer the typed tools over raw PRACTICE** when one fits: `t32_control`,
  `t32_read_memory`, `t32_write_memory`, `t32_read_registers`,
  `t32_write_register`, `t32_load_program`, `t32_breakpoint`, `t32_eval`.
  Use `t32_run_command` / `t32_run_practice` only as the escape hatch.
- **Use a preset to start.** `t32_spawn(preset="cortexm7-sim")` instead of
  hand-writing init PRACTICE. `t32_list_presets` shows all.
- **Free/unlicensed simulator caps a script at 50 commands** (RLM Error[-1]).
  Keep startup scripts short.
- Commands are **case-insensitive** but documented with meaningful capitals
  (the capitalised part is the shortest legal abbreviation): `Data.dump` ==
  `d.dump` == `DATA.DUMP`.

## 1. System init / bring-up

```
SYStem.CPU CORTEXM7          ; select the core (must match target)
SYStem.MemAccess DAP         ; allow memory access while running (hardware)
SYStem.CONFIG.DEBUGPORTTYPE SWD   ; SWD or JTAG (hardware only)
SYStem.Up                    ; attach + reset + halt at reset vector
SYStem.Mode Attach           ; attach WITHOUT resetting a running target
SYStem.Down                  ; release the target
SYStem.RESet                 ; reset TRACE32's config (not the chip)
```

Common modes for `t32_reset` / `SYStem.Mode`: `Down`, `Up`, `Go`, `Attach`,
`StandBy`. On the simulator only `SYStem.CPU` + `SYStem.Up` are needed.

## 2. Load a program (ELF/AXF)

```
Data.LOAD.Elf "app.axf"            ; symbols + code + data, set PC to entry
Data.LOAD.Elf "app.axf" /NoCODE    ; symbols only (code already in flash)
Data.LOAD.Elf "app.axf" /NoREG     ; don't touch registers/PC
Data.LOAD.Binary "blob.bin" 0x8000000  ; raw bytes at an address
```

Prefer `t32_load_program(path="app.axf")`.

## 3. Run control

| Action      | PRACTICE          | Tool                          |
|-------------|-------------------|-------------------------------|
| run         | `Go`              | `t32_control(action="run")`   |
| halt        | `Break`           | `t32_control(action="halt")`  |
| step 1 line | `Step`            | `t32_control(action="step")`  |
| step over   | `Step.Over`       | `t32_control(action="step_over")` |
| step asm    | `Step.Asm`        | `t32_control(action="step_asm")`  |
| run to ret  | `Go.Return`       | `t32_control(action="step_out")`  |

Check state with `t32_status` (down / halted / running).

## 4. Registers

```
Register.Set PC 0x8000100     ; write a core register (command)
Register.Set R0 0x1
```
Read via functions (`t32_eval`):
```
Register(PC)        ; → program counter value
Register(R0)        ; → R0
Register(SP)        ; stack pointer
```
Or dump all with `t32_read_registers` (optional group e.g. `"FPU"`).
Write one with `t32_write_register(name="R0", value=1)`.

## 5. Memory

Access classes (prefix before the address) — pick the bus/space:
`P:` program, `D:` data, `SD:` system data, `C:` core, `DAP:`/`AHB:`/`APB:`/`AXI:`
CoreSight buses (route through the MEM-AP). Default is usually `D:`.

```
Data.dump D:0x20000000              ; hex/ASCII dump window
Data.Set  D:0x20000000 %Long 0xCAFEBABE   ; write a 32-bit word
Data.Set  D:0x20000000 %Byte 0xAA         ; write one byte
```
Width selectors: `%Byte` (8), `%Word` (16), `%Long` (32), `%Quad` (64).
Read values as functions:
```
Data.Long(D:0x20000000)     ; → 32-bit value at address
Data.Byte(D:0x20000000)
Data.Word(D:0x20000000)
```
Prefer the tools: `t32_read_memory(address=0x20000000, length=64)` and
`t32_write_memory(address=..., hex_bytes="deadbeef")`.

## 6. Symbols & variables

```
sYmbol.List.Function           ; list functions
sYmbol.Browse \\program        ; symbol browser
Var.View %Hex myStruct         ; typed view of a variable (handles structs/arrays)
Var.Set myVar = 42             ; write a C variable by name
```
Functions for `t32_eval`:
```
Var.VALUE(myVar)               ; → current value of a C variable
sYmbol.RANGE(myFunc)           ; address range of a symbol
ADDRESS.OFFSET(\\app\main)     ; address of a symbol
```
Tools: `t32_list_symbols(pattern="usb*")`, `t32_var_view(name="myStruct")`.

## 7. Breakpoints

```
Break.Set main                       ; program (execution) bp at symbol
Break.Set 0x8000100                  ; bp at address
Break.Set myVar /Write               ; data write bp (watchpoint)
Break.Set myVar /Read                ; data read bp
Break.Set func /Program /VarCONDition (counter>10)   ; conditional
Break.Delete main                    ; remove one
Break.Delete                         ; remove all
Break.List                           ; list
```
Tool: `t32_breakpoint(action="set", type="program", location="main")`;
types `program | read | write | rw`; `action` set/clear/clear_all/list/enable/disable.

## 8. Cortex-M7 trace (ETM / ITM / DWT)

Cortex-M **ETM is instruction-only — there is no data trace via ETM.**
- **Instruction trace** (program flow) → ETM → TPIU/ETB:
  ```
  Trace.METHOD Analyzer          ; or CAnalyzer for off-chip (PowerTrace)
  ETM.ON
  Trace.List                     ; view decoded trace
  ```
- **Data trace** (variable values / addresses) comes from **DWT → ITM**, not ETM.
  DWT has **4 comparators**:
  ```
  ITM.DataTrace ON               ; enable data trace via ITM
  Var.Break.Set myVar /TraceData ; route a variable through a DWT comparator
  ```
- Multi-core M7 is heterogeneous **AMP**: one PowerView instance per core.
  Share an off-chip trace port across instances with matching
  `SYStem.CONFIG.<component>.Name "shared-x"` and `PROGramable OFF` on the slave.
- Off-chip trace calibration (PowerTrace/AutoFocus): `Trace.AutoFocus`,
  `Trace.ShowFocus`, save/restore with `STOre <file> AnalyzerFocus`.

## 9. Handy functions (use with t32_eval)

```
CPU()                ; CPU name string
STATE.RUN()          ; 1 if target running
Register(PC)         ; current PC
FOUND()              ; did the last search/condition match
OS.PresentDirectory(); cwd of TRACE32
```

## 10. Diagnostics

```
PRINT "msg"                  ; print to the AREA/message line
AREA.view                    ; message area
SYStem.CONFIG.state /COmponents   ; show detected CoreSight components + base addrs
                                   ; (this is where FUNNEL/TPIU base addresses come from)
```

---

### When to still use `t32_search_manuals`
Device-specific setup (flash programming a particular MCU, exotic
`SYStem.CONFIG` for custom CoreSight layouts), unusual trace configs, RTOS
awareness, or any command whose exact syntax you're unsure of. Everything in
this file you can use directly without a lookup.
