# ARSE Mk.I — Idea Spec & Coding Prompt: Pre-Programmed Holonomic Movement Routines (`IDEA-005`)

* **Project:** ARSE Mk.I Autonomous Traffic Cone (`C:\Users\Andrew\Cone`)
* **Build Priority:** **Milestone 1 (Build First)** — Runs immediately on existing Pimoroni Yukon hardware (3-wheel holonomic drive base, 9DoF IMU/compass/accelerometer, 50-LED addressable array) with **zero GPS dependency** and **no LiDAR/ToF hardware required initially**, while exposing plug-in hooks for `DualVL53L5CX` (`IDEA-002`) and `VoiceEngine` (`IDEA-003`).
* **Control API & Export:** `POST /api/routines/start`, `POST /api/routines/stop`, `POST /api/routines/trigger` + optional export of geometric polygons/patrols to `arse-mission/v1` JSON.
* **Target Folder:** `arse / cone / ideas`

---

## Part 1: Kinematic Architecture & Routine Catalogue

### 1. Design Principles: GPS-Free, Phase-1 Ready, Phase-2 Extensible

1. **Pure Local Euclidean Frame (No GNSS Required):**
   * Every routine anchors its local origin `(x = 0.0 m, y = 0.0 m)` and reference bearing `psi_0` at the moment `start()` is invoked. No `PA1010D` satellite lock is required, making all routines immediately usable indoors on living-room carpet, in a workshop, or on a shaded driveway.
2. **IMU-Closed-Loop Heading + Velocity Dead Reckoning (`50 Hz`):**
   * A `50 Hz` (`20 ms`) `uasyncio` control task fuses 9DoF gyroscope and magnetometer data to run a tight yaw PID controller (`omega_cmd`) while integrating body-frame velocities rotated by the **measured IMU heading** $\psi(t)$ to maintain local pose `(x, y)`.
   * Continuous curves (`circle`, `figure8`, `spiral`) add a closed-loop radial restoring term $v_{\text{radial}} = K_r (r_{\text{target}} - r_{\text{actual}})$ so omni-wheel friction never causes the path to spiral outward.
3. **Physical Safety & Tip-Over Guardrails:**
   * **Environment Profiles:** Two one-tap presets in the Web UI—`"indoor"` (`max_speed_mps: 0.45`, `max_leash_m: 1.8`, `a_centripetal_max: 0.75`) and `"outdoor"` (`max_speed_mps: 0.95`, `max_leash_m: 5.5`, `a_centripetal_max: 1.15`).
   * **Centripetal Acceleration Clamp:** Automatically caps tangential speed on curved paths via $v_{\text{safe}} = \min(v_{\text{req}}, \sqrt{a_{c,\max} \cdot r})$ to prevent lateral tip-over on tight radii.
   * **Virtual Leash Containment:** If `(x, y)` reaches `max_leash_m` from `(0, 0)`, non-chaos routines smoothly halt and flag `LEASH_LIMIT_REACHED`, while `chaos` mode elastically reflects its velocity vector back inside the leash.
4. **Pluggable Phase-2 Upgrade Hooks:**
   * `PatternRunner` accepts optional `range_provider=None` (compatible with `IDEA-002`'s `DualVL53L5CX.get_sector_minima()`) and `sound_callback=None` (compatible with `IDEA-003`'s `VoiceEngine.say_event()`).
   * When `range_provider` is `None` (Phase 1), `timid` mode triggers via **IMU accelerometer bump/stomp detection** or a Web UI `"Startle!"` button, and `chaos` mode bounces off the virtual leash. Once `DualVL53L5CX` is attached (Phase 2), the exact same routines automatically gain physical obstacle avoidance, wall ricochet, and `1.6 m` proximity startle triggers.

---

### 2. Holonomic Orientation Modes

Because the 3-wheel omni-drive chassis decouples translation from rotation, geometric path routines (`circle`, `polygon`, `figure8`, `compass_box`, `spiral`) accept an `orientation_mode` parameter:

* **`"tangent"`:** Continuously aligns the cone's `+X` nose with the instantaneous velocity vector.
* **`"compass_locked"`:** Locks chassis heading to the starting IMU bearing `psi_0`, tracing the entire shape via pure omnidirectional strafing.
* **`"orbit_inward"`:** Continuously rotates the chassis so its `+X` nose points toward the pattern's geometric centre `(cx, cy)`.
* **`"orbit_outward"`:** Continuously rotates the chassis so its `+X` nose faces radially outward away from `(cx, cy)`.

---

### 3. Complete Nine-Routine Catalogue

| Routine ID | Parameters | Kinematic Behaviour & LED/Event Cues |
| :--- | :--- | :--- |
| **`circle`** | `radius_m`, `laps` (`0` = infinite), `direction` (`"cw"`/`"ccw"`), `speed_mps`, `orientation_mode` | Anchors centre `radius_m` perpendicular to start pose; applies closed-loop radial restoring gain $K_r$ and centripetal speed limit $\sqrt{a_{c,\max} r}$. LEDs: `"pulse"`. |
| **`polygon`** | `sides` (`n >= 3`), `side_length_m` (`s`), `laps`, `vertex_dwell_s`, `orientation_mode` | Computes circumradius $R = \frac{s}{2\sin(\pi/n)}$ and $n$ local vertices; drives straight legs with trapezoidal velocity ramps and pauses `vertex_dwell_s` at each corner (`ACK_COMMAND`). Can export directly to `arse-mission/v1` JSON (`n <= 24`). |
| **`pirouette`** | `sub_mode` (`"spot"`/`"waltz"`), `revolutions`, `max_yaw_dps`, `orbit_radius_m` | Ramps angular rate $\omega_z$ trapezoidally (`240°/s²`) up to `max_yaw_dps` and decelerates cleanly onto start heading `psi_0` after `revolutions`. `"waltz"` blends continuous spin with a circular orbit. LEDs: `"rainbow"`. |
| **`chaos`** | `duration_s`, `max_leash_m`, `burst_speed_mps` | Leash-bounded Lévy-flight random walk: selects random velocity vectors, spin rates, and burst durations (`0.8 s – 2.5 s`). Reflects inward upon hitting `max_leash_m` (or ToF obstacle `< 0.85 m` in Phase 2) with `"strobe"` LEDs and `WARN_OBSTACLE_SIDESTEP` cues. |
| **`timid`** | `dodge_distance_m` (`0.8`), `sensitivity` | **Startled / Shy Cone:** Holds motionless with slow breathing `"pulse"` LEDs until triggered by an IMU accelerometer tap/floor-stomp, Web UI `"Startle"` button, or ToF proximity `< 1.6 m` (Phase 2)—then darts laterally `dodge_distance_m`, turns to face the trigger direction, flashes `"strobe"`, and freezes again. |
| **`figure8`** | `lobe_radius_m`, `laps`, `speed_mps`, `orientation_mode` | Drives two tangent counter-rotating circles of radius `lobe_radius_m`, smoothly ramping yaw rate through zero at the central crossover. |
| **`sentry`** | `width_m`, `dwell_s`, `speed_mps` | Oscillates laterally left-to-right across a `width_m` picket line while holding outward IMU heading `psi_0` with `"police"` or `"strobe"` LEDs. |
| **`compass_box`** | `side_length_m`, `laps`, `speed_mps` | Drives a crisp square (`+X` $\rightarrow$ `-Y` $\rightarrow$ `-X` $\rightarrow$ `+Y`) with `orientation_mode="compass_locked"` (zero chassis yaw)—ideal for verifying 3-wheel omni-drive balance and IMU heading hold. |
| **`spiral`** | `start_radius_m` (`0.25`), `end_radius_m`, `pitch_m`, `speed_mps` | Expands outward along an Archimedean spiral $r(\theta) = r_0 + b\theta$ at constant tangential speed until reaching `end_radius_m` or `max_leash_m`. |

---

## Part 2: Coding AI Implementation Prompt (`agy` / VS Code)

Copy and paste the prompt below directly into your VS Code Gemini CLI (`agy`) session inside `C:\Users\Andrew\Cone`:

```markdown
You are implementing **Pre-Programmed Holonomic Movement Routines (`IDEA-005`)** in MicroPython (plus Web UI controls) for the **ARSE Mk.I Autonomous Traffic Cone** (Pimoroni Yukon RP2040/RP2350).

### Hardware & Build-Order Context
- **Milestone 1 (Build First):** This module runs on existing Pimoroni Yukon hardware **without GPS (`PA1010D`)** and **without requiring ToF/LiDAR sensors initially**. Closed-loop control relies on the 3-wheel holonomic inverse kinematics solver, the 9DoF IMU (gyroscope + magnetometer + accelerometer), and the 50-LED addressable array.
- **Phase-2 Extensibility:** Accept optional `range_provider=None` (returning `(left_m, centre_m, right_m)` when `DualVL53L5CX` is added later) and `sound_callback=None` (invoked with semantic tokens like `"ACK_COMMAND"`, `"WARN_OBSTACLE_SIDESTEP"`, `"EVENT_MISSION_COMPLETE"` when `VoiceEngine` is added later).

### Tasks to Implement

#### 1. IMU-Stabilised Local Odometry & Pattern Runner (`nav/pattern_runner.py`)
- Implement `PatternRunner(kinematics, imu, led_controller, range_provider=None, sound_callback=None)`:
  - Run a `50 Hz` (`20 ms`) `uasyncio` control loop anchoring `(x = 0.0, y = 0.0)` and `psi_0` at routine start.
  - Integrate commanded body-frame velocity `(vx, vy)` rotated by measured IMU heading `psi` to maintain local pose `(x, y)`, and run a yaw PID loop against target heading `psi_target`.
  - Enforce environment presets (`"indoor"` vs `"outdoor"`) clamping `max_speed_mps`, `max_leash_m`, and centripetal acceleration $v_{\text{safe}} = \min(v_{\text{req}}, \sqrt{a_{c,\max} \cdot r})$ so tight circles/spirals cannot tip the cone.
  - Enforce trapezoidal linear and angular acceleration ramps (`accel_mps2`, `alpha_dps2`).
  - Implement all 9 routines with configurable `orientation_mode` (`"tangent"`, `"compass_locked"`, `"orbit_inward"`, `"orbit_outward"`):
    1. `circle(radius_m, laps, direction, speed_mps, orientation_mode)` with closed-loop radial restoring gain.
    2. `polygon(sides, side_length_m, laps, vertex_dwell_s, orientation_mode)` computing circumradius $R = \frac{s}{2\sin(\pi/n)}$ and vertices.
    3. `pirouette(sub_mode="spot"|"waltz", revolutions, max_yaw_dps, orbit_radius_m)` with trapezoidal spin ramps and clean return to `psi_0`.
    4. `chaos(duration_s, max_leash_m, burst_speed_mps)` implementing a leash-bounded Lévy-flight random walk that reflects inward on hitting `max_leash_m` (or `range_provider` `< 0.85 m` when available).
    5. `timid(dodge_distance_m=0.8, sensitivity=1.0)` holding motionless until triggered by an IMU accelerometer jerk/tap spike, manual `trigger_startle()` call, or `range_provider` `< 1.6 m`—then darting laterally `dodge_distance_m` and turning to face the trigger direction.
    6. `figure8(lobe_radius_m, laps, speed_mps, orientation_mode)`.
    7. `sentry(width_m, dwell_s, speed_mps)`.
    8. `compass_box(side_length_m, laps, speed_mps)` (locked heading square).
    9. `spiral(start_radius_m, end_radius_m, pitch_m, speed_mps)`.
  - Include `export_polygon_as_mission(sides, side_length_m)` returning a valid `arse-mission/v1` dict (`anchor_to_current_gps: true`) when `sides <= 24`.

#### 2. HTTP Control Endpoints (`web/server.py`)
- Add endpoints to `web/server.py`:
  - `POST /api/routines/start`: Accepts `{"routine": "circle" | "polygon" | ..., "preset": "indoor" | "outdoor", "params": {...}}`. Must reject with HTTP 409 if motors are not explicitly armed.
  - `POST /api/routines/stop`: Immediately ramps velocity to zero and cancels the active routine.
  - `POST /api/routines/trigger`: Triggers interactive events such as `{"event": "startle"}` for `timid` mode.
  - `GET /api/telemetry`: Include `active_routine`, `routine_progress`, and local `(local_x_m, local_y_m)` pose in telemetry.

#### 3. Web UI "Routines & Tricks" Panel (`web/index.html` & `web/routines.js`)
- Add a **"Routines & Tricks"** tab/card to the Web UI:
  - Include an **Indoor / Outdoor Safety Preset** toggle at the top.
  - Provide clean parameter cards and **Run / Stop** buttons for all 9 routines (e.g., `r` slider for Circle; `n` sides and `s` side-length inputs for Polygon plus an **"Export to Mission JSON"** button; `revolutions` & `spot/waltz` toggle for Pirouette; leash slider for Chaos; and a big **"Startle Cone!"** button for Timid mode).
  - Plot the cone's live local `(x, y)` trajectory on the Tactical Radar canvas even when GNSS fix is unavailable.

### Constraints & Verification
- Target MicroPython on Pimoroni Yukon (`uasyncio`, `ujson`, `math`, `urandom`, `utime`, `gc`); keep the `50 Hz` control loop allocation-free and preserve all motor arming interlocks.
- Add desktop Python 3 unit tests in `tests/test_pattern_runner.py` verifying polygon circumradius and vertex geometry, centripetal speed clamping, virtual leash reflection in `chaos` mode, IMU jerk startle triggering in `timid` mode, and `arse-mission/v1` polygon export.
```

