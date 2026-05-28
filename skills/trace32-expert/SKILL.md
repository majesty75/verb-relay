---
name: trace32-expert
description: Deep reference guide for advanced Lauterbach TRACE32 capabilities, including SMP (Symmetric Multiprocessing) debugging, AMP (Asymmetric Multiprocessing) debugging, OS/Linux Kernel awareness, hardware trace configurations, MMU address space translation (SpaceID), and PRACTICE (.cmm) scripting best practices. Use this when the user asks advanced questions about TRACE32 capabilities, multiprocessing configuration, kernel/RTOS debugging, trace ports, or how to write modular PRACTICE scripts.
---

# Advanced TRACE32 Debugging & Capabilities

This skill contains reference knowledge on advanced Lauterbach TRACE32 capabilities, system architectures, and PRACTICE scripting rules. Use this to formulate solutions for complex multi-core, RTOS, or OS-level tasks.

---

## 1. Multiprocessing Debugging: SMP vs. AMP

TRACE32 handles multi-core debug architectures in two distinct ways depending on the operating system model:

### A. SMP (Symmetric Multiprocessing)
* **Definition:** A single operating system instance (e.g. Linux or an SMP RTOS) runs across multiple identical cores.
* **TRACE32 Configuration:** Controlled by a **single TRACE32 GUI instance**.
* **Operation:** 
  * The GUI displays a unified system view.
  * System execution controls (Run, Halt) apply to all cores simultaneously.
  * Breakpoints are set globally; if any core hits a breakpoint, all cores halt (synchronized stopping).
  * Select active cores to view core-specific registers with `CORE.select <number>`.

### B. AMP (Asymmetric Multiprocessing)
* **Definition:** Multiple cores running independent OSs, different operating systems, or mixed bare-metal applications (heterogeneous architectures).
* **TRACE32 Configuration:** Requires **one TRACE32 GUI instance per core** (or per independent OS). Each instance runs on its own RCL and Intercom port.
* **Operation:**
  * Control is decentralized. The instances communicate and synchronize state via the **Intercom** protocol (UDP) or hardware cross-triggering lines.
  * Use `Intercom.Send <node_name> <command>` to send commands to another core's GUI.
  * Cores can share trace sources using matching component settings (e.g., `SYStem.CONFIG.FUNNEL.Name` or `SYStem.CONFIG.ETF.Name`).

---

## 2. OS & Linux Kernel Awareness

TRACE32 can parse kernel symbols and memory structures to enable task-aware and process-aware debugging:

* **Kernel Loading:** Load the uncompressed kernel image (`vmlinux`) compiled with debugging symbols:
  ```cmm
  Data.LOAD.Elf vmlinux /NoCODE
  ```
* **Virtual Addressing & SpaceID:** Linux uses virtual memory managed by the MMU. To distinguish between identical virtual addresses in different user processes, TRACE32 uses **SpaceIDs**:
  * Load symbols for a specific user space process with a SpaceID:
    ```cmm
    sYmbol.LOAD.Elf app.elf /SpaceID 0x1001
    ```
  * Inspect memory at a specific SpaceID: `Data.dump D:0x1001:0x00008000`.
* **Extensions:** Invoke OS-aware features by running the target OS script located in the TRACE32 system path:
  ```cmm
  TASK.CONFIG ~~/demo/arm/kernel/linux/linux.t32  ; Load Linux Task awareness
  MENU.ReProgram ~~/demo/arm/kernel/linux/linux.men ; Load OS-specific GUI menus
  ```
  This creates the `TASK` menu to list active processes, threads, loaded kernel modules, and device drivers.

---

## 3. Advanced PRACTICE (.cmm) Scripting Rules

When writing or executing PRACTICE scripts, adhere to these rules for robust execution:

### A. Directory & Path Resolving Macros
Always use TRACE32's built-in path resolution macros to make scripts portable:
* `~~~~/` : The directory containing the **currently executing script** (crucial for loading local ELFs relative to the script).
* `~~/` : The **T32SYS directory** (the TRACE32 installation root, e.g. `C:\T32`).
* `./` : The **current working directory** of the process.

### B. Macros (Variables)
* Macros start with an ampersand (`&`) and are case-sensitive.
* Define macro scopes explicitly:
  * `LOCAL &myVar` — Scope is limited to the current block/subroutine (preferred).
  * `PRIVATE &myVar` — Scope is restricted to the current script file.
  * `GLOBAL &myVar` — Persistent across script invocations.
* Set values: `&myVar="CORTEXM4"` or `&myVar=5`.
* Perform macro text replacement: `SYStem.CPU &myVar`.

### C. Flow Control & Subroutines
* Subroutines: Define with a label followed by a colon, call using `GOSUB`, and exit using `RETURN`.
  ```cmm
  GOSUB InitializeTarget
  END
  
  InitializeTarget:
    SYStem.RESet
    SYStem.CPU CORTEXM4
    SYStem.Up
    RETURN
  ```
* Script execution chaining: Call external scripts with `DO <path> <arguments>`. Receive arguments in the sub-script using `ENTRY &arg1 &arg2`.

---

## 4. Hardware Trace & Profiling

TRACE32 records real-time, non-intrusive instruction/data traces using hardware trace modules (ETM, ITM, HTM, PTB):

* **Configuring ETM (Embedded Trace Macrocell):**
  * Instruction trace is non-intrusive. To activate:
    ```cmm
    Trace.METHOD Analyzer    ; Select hardware trace probe (or CAnalyzer)
    ETM.ON                   ; Enable instruction trace
    Trace.Init               ; Initialize trace buffer
    Trace.Arm                ; Arm the trigger
    ```
  * List trace buffer output: `Trace.List`.
* **Data Tracing (DWT/ITM):**
  * ETM does not trace data on Cortex-M. Data trace requires the DWT/ITM components.
  * Set a watchpoint to log data changes to trace:
    ```cmm
    ITM.DataTrace ON
    Var.Break.Set myVariable /TraceData
    ```
* **CoreSight Diagnostics:** Run `SYStem.CONFIG.state /Components` to discover register base addresses for ROM tables, Funnels, and Trace Ports.
* **AutoFocus (Trace Port Calibration):** Calibrate trace line signals to match target board timing:
  ```cmm
  Trace.AutoFocus
  Trace.ShowFocus
  ```
