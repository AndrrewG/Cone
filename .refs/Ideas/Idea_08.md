# ARSE Mk.I — Idea Spec & Coding Prompt: Annoy Eufy Mode (`IDEA-008`)

* **Project:** ARSE Mk.I Autonomous Traffic Cone (`C:\Users\Andrew\Cone`)
* **Target Adversary:** Eufy S1 Pro Floor-Cleaning Mop Robot & Omni/Base Station
* **Hardware Phases:**
  * **Phase 1 (`[INSTALLED]` Hardware):** Base-station spawn camping, dock-return blockade, and scheduled/waypoint dock patrol using 3-wheel omni-drive, `ICM-20948` IMU dead-reckoning (`PatternRunner`), `60`-LED hazard array, and Slot 6 `TAS2780` `"meep_meep"` taunts.
  * **Phase 2 (Gated on `[PLANNED]` `DualVL53L5CX` in `HARDWARE.md` §3.1):** Live low-profile moving-target tracking, dynamic cut-offs, corner box-ins, and matador dodges.
* **Control API:** `POST /api/eufy/start`, `POST /api/eufy/stop`, `POST /api/eufy/set_dock`, `GET /api/eufy/status`
* **Note on Numbering:** Requested as *"IDEA 7"*; catalogued as **`IDEA-008`** because `IDEA-007` is already assigned to Web UI Authentication & Hijack Prevention.

---

## Part 1: Tactical Harassment Architecture

### 1. Sub-Mode Catalogue ("Annoy Eufy" Playbook)

| Sub-Mode ID | Phase | Behaviour & Tactical Objective |
| :--- | :--- | :--- |
| **`base_station_ambush`** | Phase 1 (`[INSTALLED]`) | Drives to saved local dock coordinates `(dock_x_m, dock_y_m)` and parks squarely `0.25–0.35 m` in front of the Eufy S1 Pro base-station ramp so it cannot undock to start its clean. Oscillates laterally (`±0.15 m`) if desired to defeat narrow edge-around attempts. |
| **`dock_blockade_return`** | Phase 1 / Phase 2 | Patrols the approach corridor in front of the base station (or intercepts when the Eufy approaches within `1.2 m` of the dock in Phase 2) and parks across the docking funnel so the Eufy cannot align with its charging contacts or wash station. |
| **`shadow_cut_off`** | Phase 2 (`DualVL53L5CX`) | Tracks the Eufy S1 Pro's low-profile silhouette (`rows 4–7` of the `8×8` ToF grid, `0.10–0.35 m` height) as it mops, then holonomically strafes ahead of its velocity vector to park `0.25 m` directly in its path—forcing constant LiDAR re-planning. |
| **`box_in`** | Phase 2 (`DualVL53L5CX`) | Mirrors the Eufy's evasive turn direction using lateral omni-strafing to herd and pin it against skirting boards, table legs, or room corners for a configurable `max_pin_s` (`15 s` default) before backing off `0.30 m` to let it think it escaped. |
| **`taunt_pirouette`** | Phase 1 / Phase 2 | Triggered whenever the Eufy stalls, reverses, or rotates `>= 45 deg` in frustration (detected via ToF angular shift or IMU bump): executes a `360 deg` celebratory pirouette, flashes `"police"` / `"strobe"` LEDs, and plays `"meep_meep"` via `VoiceEngine` before re-blocking. |
| **`wet_floor_waltz`** | Phase 2 (`DualVL53L5CX`) | Trails `0.60–0.80 m` behind the Eufy S1 Pro while it lays down clean mopped stripes, weaving in a slow lateral figure-8 across the freshly mopped floor for maximum psychological warfare. |
| **`matador_sidestep`** | Phase 2 (`DualVL53L5CX`) | Holds position until the Eufy closes to `0.22 m` and commits to a turn, then executes a crisp `0.40 m` lateral holonomic dodge—immediately sliding back in front of the Eufy's newly chosen heading. |
| **`scheduled_ambush`** | Phase 1 / Phase 2 | Accepts a scheduled trigger (local clock window or Home Assistant / HTTP webhook) while **explicitly armed** by the operator, waking from low-power idle 2 minutes before the Eufy's scheduled cleaning run to occupy the base-station exit. |

---

### 2. Low-Profile Target Discrimination (`DualVL53L5CX`, Phase 2)

1. **Height-Band Filtering:**
   * A Eufy S1 Pro is roughly `0.096 m` tall (`~0.35 m` diameter) and hugs the floor.
   * Use the lower `VL53L5CX` matrix rows (`rows 4–7`) minus static background baseline while ignoring tall objects (`rows 0–2` at the same bearing/range) so the cone targets the robot rather than human ankles, chair legs, or walls.
2. **Velocity & Motion-Delta Lock:**
   * Maintain a `16-bin` background range EMA; bins exhibiting coherent lateral/radial range change (`0.08–0.50 m/s`) consistent with a floor robot acquire a `target_locked` track `(bearing_deg, range_m, est_vx_mps, est_vy_mps)`.
3. **Human / Pet Yield Override:**
   * If tall-row (`rows 0–2`) ToF returns close within `< 0.50 m` (indicating a person walking up), immediately pause harassment, hold position, and play `"boop"` until clear.

---

### 3. Physical Safety & Hardware Guardrails

* **Explicit Arming Gate:** `POST /api/eufy/start` must never auto-arm drive motors; returns `HTTP 409 Conflict` if disarmed.
* **Indoor Speed & Leash Caps:** Strictly enforces the `"indoor"` profile (`max_speed_mps = 0.45`, `a_c_max_mps2 = 0.75`, `max_leash_m = 2.5`) so the high-CG cone never tips over during sharp cut-offs.
* **Gentle Standoff & Bumper Back-Off:** Target standoff is `>= 0.20 m` (minimum `0.15 m` hard stop). If an IMU impact spike (`> 0.35 g`) occurs—because the Eufy nudged the cone—back off `0.15 m` cleanly and re-lock rather than pushing back.
* **Zero-Allocation `50 Hz` Control Loop:** Pre-allocate all `16-bin` tracking buffers and state variables at module initialisation; no per-tick heap allocations.

---

## Part 2: Coding AI Implementation Prompt (`agy` / VS Code)

```markdown
You are implementing **Annoy Eufy Mode (`IDEA-008`)** in MicroPython for the **ARSE Mk.I Autonomous Traffic Cone** (`C:\Users\Andrew\Cone`).

### Scope & Hardware Gate
- **Phase 1 (Immediate `[INSTALLED]` Hardware):** Implement `nav/eufy_harasser.py` (`EufyHarasser`) supporting `base_station_ambush`, `dock_blockade_return` (waypoint/corridor patrol), and `taunt_pirouette` using `PatternRunner` / `Omni3Kinematics`, `ICM-20948` IMU dead-reckoning, `lights.py`, and optional `sound_callback`.
- **Phase 2 (`DualVL53L5CX` Gated):** Accept an optional `tof_provider=None` interface (`get_target_vector()` returning `(bearing_deg, range_m, confidence)` from lower `8x8` rows) for `shadow_cut_off`, `box_in`, `wet_floor_waltz`, and `matador_sidestep`. Never import `VL53L5CX` directly until it is promoted to `[INSTALLED]` in `HARDWARE.md`.

### Tasks to Implement
1. **Harassment State Machine (`nav/eufy_harasser.py`):**
   - States: `IDLE`, `APPROACHING_DOCK`, `CAMPING_DOCK`, `INTERCEPTING`, `SHADOWING`, `BOXING_IN`, `MATADOR_DODGE`, `TAUNT_PIROUETTE`, `BUMP_BACKOFF`, `HUMAN_YIELD`.
   - Enforce disarmed rejection, `indoor` speed (`<= 0.45 m/s`), `max_leash_m`, `>= 0.15 m` standoff, and `> 0.35 g` IMU bump back-off.
2. **HTTP API & Web UI Controls (`telemetry_server.py`, `web/index.html`):**
   - Add `POST /api/eufy/set_dock` (records current local `(x_m, y_m, heading_deg)` as the Eufy base-station pose), `POST /api/eufy/start` (`sub_mode`), `POST /api/eufy/stop`, and `GET /api/eufy/status`.
   - Add an **Annoy Eufy** control card in the Web UI with **Set Eufy Dock Here**, sub-mode selector, and **Stop** button.
3. **Desktop Verification (`tests/test_eufy_harasser.py`):**
   - Test disarmed rejection, dock-camping coordinate hold, virtual leash clamping, IMU bump back-off, human-yield pause, and simulated Phase-2 ToF target cut-off steering.
```
