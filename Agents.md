# AGENTS.md — ARSE Mk.I Autonomous Traffic Cone (`C:\Users\Andrew\Cone`)

You are pair-programming with Andrew on the **ARSE Mk.I Autonomous Traffic Cone**, built on the **Pimoroni Yukon** (`RP2040` / `RP2350`) running **MicroPython**.

---

## 1. Repository Orientation & Source-of-Truth Hierarchy

Before planning or modifying any code, inspect the existing project documentation in the repository root so you never duplicate logic or contradict established physical or protocol contracts:

1. **`HARDWARE.md`:** Sole source of truth for physical wiring, Pimoroni Yukon slot assignments, GPIO/I2C/UART pinouts, 3-wheel omni-drive chassis dimensions, body-frame axis conventions, and `[INSTALLED]` vs `[PLANNED]` peripheral status.
   * *Note:* If `HARDWARE.md` does not yet exist and Andrew asks you to generate it, audit `HANDOFF.md`, `README.md`, and all existing `.py` files to populate it accurately without guessing unknown constants.
   * Once `HARDWARE.md` exists, **never write firmware that imports or initialises peripherals marked `[PLANNED]`**.
2. **`HANDOFF.md`:** Living engineering log capturing current build state, active bench calibration offsets, recent floor-test findings, and the immediate next micro-step.
3. **`README.md` & Mission Format Specification (`arse-mission/v1`):** Repository setup, deployment workflow, and the authoritative JSON schema for waypoint missions (`"turn"` / `"glide"` drive styles, LED modes, and acoustic triggers).
4. **`ROADMAP.md` & `IDEA-001`..`IDEA-007` (if present):** Long-term phased roadmap. Treat future milestones strictly as background architectural context—never implement future-milestone features unless explicitly requested in the current prompt.

---

## 2. Mandatory Interactive Workflow (One Micro-Feature at a Time)

You MUST obey these collaboration rules on every single turn:

1. **One Small Slice Only:** Never implement multiple features, multiple movement routines, or entire milestones in one pass. Implement **only the single specific sub-feature requested in the current prompt**.
2. **Inspect Existing Code First:** Before writing new code, inspect `HARDWARE.md`, `HANDOFF.md`, `main.py`, existing motor/kinematics modules, IMU/sensor drivers, LED controllers, and `web/` files so your changes integrate cleanly with existing interfaces and naming conventions.
3. **No Speculative Scaffolding:** Do **not** create placeholder files, stub classes, or unused hooks for future hardware or milestones (such as GNSS, `VL53L5CX` ToF sensors, I2S audio synthesis, or multi-cone swarming) unless explicitly requested in the current prompt.
4. **Ask When Hardware Details Are Ambiguous:** If any Yukon slot assignment, GPIO pin, IMU axis orientation, or wheel geometry constant is not already defined in `HARDWARE.md`, `HANDOFF.md`, or the codebase, **stop and ask Andrew** rather than guessing.
5. **Mandatory Test-and-Pause Gate:** Every code increment must conclude by:
   * Adding or updating focused desktop Python 3 unit tests under `tests/` (`pytest`) using lightweight mocks for MicroPython modules (`machine`, `utime`, `uasyncio`).
   * Running `pytest` to verify all tests pass.
   * Summarising the exact files changed, providing a concise **Bench / Floor Test Checklist** for Andrew to run on the physical cone, and **stopping to await his feedback** before starting the next sub-feature.

---

## 3. Pimoroni Yukon & MicroPython Engineering Guardrails

1. **MicroPython Standard Library Only:**
   * Target MicroPython on Pimoroni Yukon (`uasyncio`, `ujson`, `uhashlib`, `ubinascii`, `os`, `struct`, `math`, `array`, `utime`, `gc`, `machine`, `network`). Never import CPython-only packages (`numpy`, `scipy`, `cryptography`, etc.) in firmware modules.
2. **Zero-Allocation Real-Time Loops:**
   * Do not allocate lists, dictionaries, or large bytearrays inside high-rate ($50\text{ Hz}$) motor/IMU control loops or ($15\text{ Hz}$) sensor loops. Pre-allocate buffers at module initialisation and invoke `gc.collect()` deterministically only when stationary between manoeuvres.
3. **Integer Micro-Degree GNSS Arithmetic (When GNSS Is Used):**
   * Standard RP2040 MicroPython builds use 32-bit single-precision floats ($\approx 7$ significant digits), introducing $1\text{--}2\text{ m}$ quantisation error on raw WGS84 `lat`/`lon` degrees. Always convert incoming coordinates to integer micro-degrees (`int(round(deg * 10_000_000))`) before subtracting the latched `home` datum and projecting to local `(north_m, east_m)` metres.
4. **Non-Bypassable Physical Safety Interlocks:**
   * **Explicit Arming Gate:** Drive motors must always boot **disarmed** and never auto-arm when a routine, mission, or command is selected.
   * **Smooth Kinematics & Tip-Over Prevention:** Enforce trapezoidal linear (`accel_mps2`) and angular (`alpha_dps2`) acceleration ramps, plus centripetal tip-over speed clamping:
     $$v_{\text{safe}} = \min\left(v_{\text{req}}, \sqrt{a_{c,\max} \cdot r}\right)$$
     so high-centre-of-gravity cone hardware cannot tip over on tight turns.
   * **Virtual Leash Containment:** Enforce a configurable virtual circular leash (`max_leash_m`) around the local starting origin $(0.0\text{ m}, 0.0\text{ m})$ during dead-reckoning routines so runaway integration halts safely.
5. **Language & Units Convention:**
   * Use **British English** spelling across all documentation, UI copy, comments, and identifiers (`initialise`, `synchronise`, `behaviour`, `kerb`, `pavement`, `centre`, `metre`, `manoeuvre`, `colour`, `optimise`).
   * Suffix all physical variables with explicit SI units (`_m`, `_mps`, `_mps2`, `_deg`, `_rad`, `_dps`, `_ms`, `_mv`).

---

## 4. Core Protocol Identifiers (Reference Only)

* **`arse-mission/v1`:** JSON waypoint mission format (`<= 25` waypoints per segment, `"turn"` or `"glide"` drive styles).
* **`arse-lang/v1`:** Declarative Conish acoustic lexicon (`/flash/conish_lexicon.json`) mapping semantic robot events to priority-arbitrated utterances (`"meep"`, `"boop"`, `"ech_ung"`, `"uh_oh"`, `"brrr_up"`, `"meep_meep"`).
* **`arse-swarm/v1`:** COBS + CRC-16 framed inter-cone mesh protocol for multi-cone formations and deployments.


## Execution Autonomy & Clarifying Question Policy

1. **Execute Routine Verification Immediately (Never Ask Permission):**
   - Do **not** end a turn asking *"May I run this script?"*, *"Shall I run pytest?"*, *"Would you like me to push this to the Pi?"*, or *"Can I try this on the hardware?"*.
   - Whenever you write or modify code for the active `TASKS.md` slice, **immediately** run the corresponding desktop `pytest` suite, static checks, and non-destructive hardware-in-the-loop verification commands (`scripts/sync_and_test_pi.ps1`, `ssh pi@arse-td.local`, `mpremote run`/`fs cp`, or `pio test`/`pio run`) in the same turn, and report the actual command output.
   - Respect physical safety interlocks during autonomous hardware runs:
     - **Cone (`Pimoroni Yukon`):** Never auto-arm drive motors in firmware or test scripts; leave final floor arming to Andrew.
     - **ARSE-TD (`Raspberry Pi`):** Never overwrite live `config/node_calibration.toml` offsets or transmit unhashed VRMs externally.
     - **WinchRemote (`Heltec HT-CT62`):** Verify `150 ms` dead-man relay drop-out and mutual-exclusion interlocks on bench LEDs before any mains relay energisation.

2. **Use Structured Q&A Only When Genuinely Necessary:**
   - Reserve clarifying questions strictly for the following four situations where guessing risks wasted effort or hardware faults:
     1. **Missing Physical Constants:** A Yukon slot, GPIO pin, I2C/SPI address, RF frequency offset, or chassis dimension is absent from `HARDWARE.md` and cannot be derived from existing source files.
     2. **Unsettled Architectural Trade-Offs:** Two or more valid engineering designs exist that are not already decided in `ROADMAP.md` or `features/IDEA-*.md`.
     3. **Destructive or Irreversible Actions:** Erasing flash/NVS partitions, deleting live secrets or calibration files, or rewriting Git history.
     4. **Physical Human Action Required:** A bench or floor step requires Andrew's hands (e.g., placing the cone on the floor and pressing Arm, positioning the handheld SPL meter beside the roadside microphone, pulling out the winch pendant E-Stop, or plugging in a USB-C cable).
   - When asking a clarifying question, use a concise **structured Q&A format**—presenting 2–3 concrete engineering options with a clearly labelled recommended default—rather than open-ended prose.
   