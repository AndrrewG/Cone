# ARSE Mk.I — Idea Spec & Coding Prompt: Dual-ToF Collision Avoidance & Frontier Mapping (`IDEA-002`)

* **Project:** ARSE Mk.I Autonomous Traffic Cone (`C:\Users\Andrew\Cone`)
* **Hardware & Runtime:** Pimoroni Yukon (RP2040 / RP2350) running MicroPython, Dual Pimoroni `VL53L5CX` (`PIM622`) 8×8 Multi-Zone Time-of-Flight Breakouts, `PA1010D` GNSS receiver, 9DoF IMU/compass, 50-LED addressable warning array, 25W acoustic annunciator, 3-wheel holonomic drive base
* **Mission Protocol:** `arse-mission/v1` JSON + live occupancy map telemetry (`GET /api/map`, `POST /api/explore`)
* **Target Folder:** `arse / cone / ideas`

---

## Part 1: Hardware, Kinematics & Mapping Architecture

### 1. Dual `VL53L5CX` Electrical & Mechanical Integration on Pimoroni Yukon

Each Pimoroni `VL53L5CX` breakout (`PIM622`) provides an `8 × 8` (`64`-zone) 940 nm VCSEL depth matrix across a `45° × 45°` (`65°` diagonal) field of view at `15 Hz`, drawing `~100 mA` typical (`150 mA` peak)—well within the Yukon's `3 A` 3.3 V regulator budget for a dual-sensor installation.

#### Deterministic Boot-Time I2C Address Assignment
Because `VL53L5CX` stores its 7-bit I2C address (`0x29` default) in volatile RAM and resets on power-cycle, both sensors can share the Yukon's onboard Qwiic/STEMMA QT I2C bus (`400 kHz` or `1 MHz` Fast-Mode Plus) without an external I2C multiplexer by gating their `LPn` (active-low I2C standby) pins via two spare Yukon GPIOs:

1. At boot, drive `LPN_LEFT = 0` and `LPN_RIGHT = 0` for `20 ms` to place both sensors into I2C standby.
2. Drive `LPN_LEFT = 1`, wait `10 ms`, initialise **Sensor Left** at default address `0x29` (uploading the ST Ultra Lite Driver firmware blob), and immediately call `sensor_left.set_i2c_address(0x30)`.
3. Drive `LPN_RIGHT = 1`, wait `10 ms`, and initialise **Sensor Right** at default address `0x29`.
4. Configure both sensors to `RESOLUTION_8X8` and `15 Hz` ranging rate, staggering `start_ranging()` by `33 ms` (`half-frame` phase offset) so I2C bus reads alternate cleanly every `33 ms` without burst contention.

#### Splayed `±21°` Yaw & `11°` Downward Pitch Mounting
Mount both breakouts on a rigid 3D-printed nose bezel at `h ≈ 300 mm` above ground level (just above the upper reflective collar):

* **Horizontal Splay (`±21°` Yaw):** Angle **Sensor Left** at `-21°` (covering `-43.5°` to `+1.5°`) and **Sensor Right** at `+21°` (covering `-1.5°` to `+43.5°`). Merging their columns produces a continuous **`87°` wide-angle depth fan** across `16` horizontal azimuth bins (`~5.44°` per column, taking the minimum valid depth on the `3°` centre overlap).
* **Downward Pitch (`11°`):** Pitching both sensors downward by `11°` tilts the `45°` vertical FoV to span `+11.5°` (top row `0`) down to `-33.5°` (bottom row `7`):
  * **Obstacle Rows (`0–5`):** Scan horizontally across the pavement from `0.4 m` out to `3.5 m`, catching pedestrians, dogs, prams, wheelie bins, bollards, and parked cars even during diagonal holonomic `"glide"` translation.
  * **Ground-Plane Kerb/Cliff Rows (`6–7`):** Strike the tarmac or paving slabs `~0.55 m – 1.05 m` ahead of the front omni-wheel. Given mounting height $h$ and downward ray elevation angle $\phi_r$, expected flat-pavement slant range is $r_{\text{expected}}(r) = h / \sin(\phi_r)$. Any deviation $|r_{\text{measured}} - r_{\text{expected}}(r)| > 0.12\text{ m}$ flags either a raised un-dropped kerbstone (shorter range) or a vertical step/kerb drop (longer range or invalid return).

#### Outdoor Sunlight & Status Filtering
Direct UK summer sunlight elevates ambient SPAD noise on 940 nm ToF receivers, compressing maximum range from `4.0 m` to `~1.8 m – 2.2 m`. Discard any zone whose `target_status` is not `5` or `9` (ST ULD high-confidence valid target codes) or whose `range_sigma_mm` exceeds `45 mm` so bright reflections on wet paving slabs never trigger phantom emergency halts.

---

### 2. Reactive Collision Avoidance During `arse-mission/v1` Missions

To keep uploaded `arse-mission/v1` waypoint lists immutable and auditable, insert a **16-Bin Vector Field Histogram (VFH) Reactive Avoidance Filter** directly between the **Waypoint Navigator** and the **Holonomic Inverse Kinematics** solver inside the MicroPython control loop.

#### 16-Bin Horizontal Clearance Vector
Every `66 ms` (`15 Hz`), merge the latest Left and Right `8 × 8` frames into a 16-element clearance array $D[0..15]$ (in metres):
* For each azimuth column $c \in [0..15]$, take the minimum valid distance across obstacle rows `0..5`.
* Evaluate kerb/cliff rows `6..7`: if a raised kerb or drop-off is detected in column $c$, inject a synthetic `0.45 m` virtual hazard into $D[c]$ so the cone treats un-dropped kerbs as solid walls.

#### Three-Zone Kinematic Escalation Envelope
Let $(v_x, v_y, \omega_z)$ be the body-frame velocity command requested by the Waypoint Navigator (`+X` forward, `+Y` left):

1. **Free-Cruise Envelope ($D_{\min} > 1.80\text{ m}$):**
   * Pass $(v_x, v_y, \omega_z)$ straight through unmodified at mission cruise speed.
2. **Holonomic Sidestep / Caution Envelope ($0.75\text{ m} \le D_{\min} \le 1.80\text{ m}$):**
   * Attenuate forward velocity smoothly: $\alpha = \text{clamp}\left(\frac{D_{\min} - 0.75}{1.05},\; 0.25,\; 1.0\right)$, setting $v_x' = \alpha \cdot v_x$.
   * Compute a **repulsive lateral strafe velocity** $v_{y,\text{avoid}}$ toward whichever flank (`left` bins `0..7` vs `right` bins `8..15`) offers the largest contiguous open depth window. Because the ARSE Mk.I is omnidirectional, it glides smoothly around stationary pedestrians or wheelie bins without losing its compass heading.
   * Switch the 50-LED array to `"pulse"` (`"strobe"` below `1.10 m`) and emit a single courtesy `"chirp"` on entering caution state.
3. **Critical Halt & Bounded Box-Detour Envelope ($D_{\min} < 0.75\text{ m}$):**
   * Immediately command $v_x' = 0, v_y' = 0$, activate `"strobe"` LEDs, and play `"meep"`.
   * Hold stationary for `3.0 s` to let moving pedestrians, cyclists, or dogs clear the pavement naturally.
   * If the obstruction remains after `3.0 s`, enter a **Local Box-Detour State**: yaw `±45°` to verify flank clearance with the `87°` dual array, strafe laterally (`1.2 m`) into the clear lane, advance `2.0 m` parallel to the original course, and return control to the Waypoint Navigator. If both flanks are blocked (e.g., a closed gate), hold position, sound `"horn"` or `"alarm"`, and set `status: "OBSTACLE_BLOCKED"` in `/api/telemetry`.

#### Lookahead Yaw Guard for `"drive_style": "glide"`
Even with an `87°` forward array, pure sideways translation (`|v_y| > |v_x|`) during `"glide"` missions could move outside the forward cone of vision. Whenever translational speed exceeds `0.15 m/s` and any bin reports $D[c] < 2.0\text{ m}$, temporarily bias the cone's target yaw toward the instantaneous velocity vector $\text{atan2}(v_E, v_N)$ until clearance exceeds `2.2 m`.

---

### 3. Autonomous Mapping & Frontier Exploration Mode

When switched into **Exploration Mode** (`POST /api/explore`), the cone maps an unknown garden, driveway, courtyard, or cul-de-sac around its latched `home` datum and streams a live occupancy grid to the Web UI.

#### Zero-Allocation `6,400`-Byte Log-Odds Occupancy Grid
To eliminate MicroPython heap fragmentation on the RP2040/RP2350, allocate a single flat `bytearray` at module import time:

* **Dimensions & Resolution:** `80 × 80` cells at `0.25 m` per cell (`20.0 m × 20.0 m` arena spanning `[-10.0 m, +10.0 m]` in North and East around `(0.0, 0.0)`), consuming exactly **`6,400 bytes` (`6.25 KB`)** of RAM.
* **Cell Value Encoding (`uint8`):**
  * `127` — Unexplored / Unknown (`p = 0.50`)
  * `0 .. 114` — Free space (decremented by `12` per ray traversal, clamped at `0`)
  * `140 .. 255` — Occupied obstacle / wall / kerb (incremented by `28` per valid ray hit, clamped at `255`)

#### Integer Bresenham Ray-Casting & 180° Holonomic Sweep Scans
* **5 Hz Ray Integration:** Using the cone's fused local pose $(x, y, \psi)$, project all `16` horizontal clearance rays ($c = 0..15$, world angle $\psi + \theta_c$) out to measured range $r_c$. Step from cone cell $(g_{x0}, g_{y0})$ to endpoint cell $(g_{x1}, g_{y1})$ using pure integer **Bresenham line drawing**—marking intermediate cells as free and, whenever $r_c < 3.60\text{ m}$, marking the terminal cell as occupied.
* **Fast `180°` Panoramic Sweeps:** Because the dual `VL53L5CX` array already spans `87°` (`~90°`), a full `360°` environmental scan only requires a **`180°` on-the-spot holonomic rotation** (`3.0 seconds` at `60°/s`), triggered every `3.5 m` of exploration travel or whenever no frontier candidates remain in the forward hemisphere.

#### Frontier Detection, Leash Geofence & Mission Export
1. **Frontier Clustering (`0.5 Hz`):** Scan the `80 × 80` `bytearray` for free cells (`val < 115`) adjacent (4-connected) to unknown cells (`115 <= val <= 139`). Group contiguous frontier cells into clusters (discarding clusters `< 3` cells to ignore sensor noise) and select the highest-scoring cluster centroid $(x_f, y_f)$:
   $$\text{Score} = \frac{\text{Cluster Cell Count}}{\text{Euclidean Distance} + 1.0}$$
2. **Configurable Exploration Leash:** Clamp all candidate frontiers within `max_explore_radius_m` (default `9.5 m` inside the `10.0 m` grid half-width) so the cone maps your entire property or cul-de-sac safely without wandering into public roads.
3. **Completion & Web UI Patrol Export:** When all reachable frontier clusters are exhausted (or battery voltage drops below threshold), sound `"chime"`, switch LEDs to `"rainbow"`, and return to `(0.0, 0.0)`. Meanwhile, `GET /api/map` serves a Run-Length Encoded (RLE) snapshot (`~400–900 bytes`) rendered on an HTML5 `<canvas>` in the Web UI, complete with **"Click-to-Go"** waypoint dispatch and **"Export Safe Patrol"** to save a verified `arse-mission/v1` perimeter loop.

---

## Part 2: Coding AI Implementation Prompt (`agy` / VS Code)

Copy and paste the prompt below directly into your VS Code Gemini CLI (`agy`) session inside `C:\Users\Andrew\Cone`:

```markdown
You are implementing dual-sensor `VL53L5CX` Time-of-Flight collision avoidance and occupancy-grid exploration mode in MicroPython (plus Web UI visualisation) for the **ARSE Mk.I Autonomous Traffic Cone** (Pimoroni Yukon RP2040/RP2350).

### Hardware & Architecture Context
- **Sensors:** Two Pimoroni `VL53L5CX` (`PIM622`) 8×8 ToF breakouts wired to the Yukon Qwiic I2C bus (`400 kHz`), mounted on the `+X` nose at `h = 0.30 m` height, pitched downward by `11°`, and splayed horizontally at `-21°` (Left) and `+21°` (Right) for an `87°` combined horizontal FoV (`16` azimuth columns).
- **I2C Address Boot Split:** Both sensors default to 7-bit address `0x29` in volatile RAM. Wire `LPn` pins to two configurable Yukon GPIOs (`LPN_LEFT_PIN`, `LPN_RIGHT_PIN`). At boot, hold both `LPn` low, bring `LPN_LEFT` high, initialise Sensor Left and reassign its I2C address to `0x30`, then bring `LPN_RIGHT` high and initialise Sensor Right at `0x29`.

### Tasks to Implement

#### 1. Dual `VL53L5CX` Driver & 16-Bin Horizon Aggregator (`sensors/dual_tof.py`)
- Implement `DualVL53L5CX`:
  - Perform the `LPn` GPIO boot sequence to assign `0x30` (Left) and `0x29` (Right), configure `8×8` resolution at `15 Hz`, and stagger `start_ranging()` by `33 ms`.
  - Filter out zones where `target_status not in (5, 9)` or `range_sigma_mm > 45`.
  - Collapse the two `8 × 8` depth frames into a pre-allocated 16-element float array `clearance_m[0..15]` spanning `-43.5°` to `+43.5°`:
    - Take the minimum valid distance across obstacle rows `0..5` for each column.
    - Check ground-plane rows `6..7` against expected flat-pavement slant range `r_expected = mounting_height_m / sin(ray_elevation_rad)`. If `abs(measured - r_expected) > 0.12`, inject a `0.45 m` virtual hazard into that column to flag un-dropped kerbs or step drops.
  - Expose `get_sector_minima()` returning `(left_min_m, centre_min_m, right_min_m)`.

#### 2. Reactive VFH Collision Avoidance Filter (`nav/obstacle_avoidance.py`)
- Implement `ReactiveAvoider` sitting between the Waypoint Navigator and Holonomic Inverse Kinematics:
  - **Free (`min_d > 1.80 m`):** Pass `(vx, vy, omega)` through unchanged.
  - **Caution (`0.75 m <= min_d <= 1.80 m`):** Scale forward `vx` smoothly (`0.25..1.0`), add repulsive lateral strafe `vy_avoid` toward the side (`0..7` vs `8..15`) with greater mean depth, set LEDs to `"pulse"` (`"strobe"` below `1.10 m`), and trigger `"chirp"` on entry.
  - **Critical Halt & Box-Detour (`min_d < 0.75 m`):** Clamp `vx = 0, vy = 0`, set LEDs to `"strobe"`, play `"meep"`, and hold for `3.0 s`. If still blocked after `3.0 s`, execute a bounded box-detour (`1.2 m` lateral strafe toward clear flank + `2.0 m` forward advance) before resuming waypoint tracking. If both flanks are blocked, hold position, play `"horn"`, and report `OBSTACLE_BLOCKED`.
  - In `"drive_style": "glide"`, bias target yaw toward the instantaneous velocity vector whenever speed `> 0.15 m/s` and `min_d < 2.0 m`.

#### 3. Zero-Allocation Occupancy Grid & Frontier Explorer (`nav/occupancy_mapper.py`)
- Allocate a single `6,400`-byte `bytearray` representing an `80 × 80` grid at `0.25 m` cell resolution (`20 m × 20 m` centred on `home` `(0, 0)`), initialised to `127` (unknown).
- Implement `integrate_scan(pose_n, pose_e, heading_rad, clearance_m)` using pure integer **Bresenham line drawing** to decrement free traversed cells toward `0` and increment occupied terminal cells (`r < 3.60 m`) toward `255`.
- Implement `FrontierExplorer`:
  - Detect 4-connected frontier clusters (`cell < 115` bordering `115 <= neighbour <= 139`), filter out clusters `< 3` cells, and score candidates by `cluster_size / (distance_m + 1.0)` within `max_explore_radius_m` (default `9.5 m`).
  - Trigger a `180°` holonomic in-place sweep scan every `3.5 m` of travel or when no frontiers exist in the forward sector.
  - Return to `(0.0, 0.0)`, set LEDs to `"rainbow"`, and play `"chime"` once all reachable frontiers are mapped.
  - Provide `encode_rle()` using `memoryview` slices for compact HTTP serialisation.

#### 4. HTTP API & Web UI Live Map Overlay (`web/server.py`, `web/index.html`, `web/radar_map.js`)
- Add `GET /api/map` returning JSON `{"width": 80, "height": 80, "res_m": 0.25, "rle": [...]}` and `POST /api/explore` (`{"action": "start" | "stop", "max_radius_m": 9.5}`).
- Render the RLE occupancy grid underneath the **Tactical GNSS Radar** canvas in the Web UI (showing free space, walls/kerbs, unexplored grey, and frontier targets), plus a **"Click-to-Go"** waypoint dispatcher and **"Export Safe Patrol"** button that converts mapped free-space perimeter waypoints into a valid `arse-mission/v1` JSON file.

### Constraints & Verification
- Target MicroPython on Pimoroni Yukon (`uasyncio`, `ujson`, `math`, `gc`); avoid dynamic allocations inside the `15 Hz` ToF loop and run `gc.collect()` only between exploration legs.
- Preserve all arming interlocks: never auto-arm drive motors when starting exploration mode.
- Add desktop Python 3 unit tests in `tests/test_tof_and_mapper.py` verifying kerb slant-range detection, VFH lateral strafe direction, integer Bresenham ray-casting, frontier clustering, and RLE round-trip decoding.
```

