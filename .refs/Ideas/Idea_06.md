# ARSE Mk.I — Idea Spec & Coding Prompt: Multi-Cone Swarming over Dedicated ESP-NOW Mesh (`IDEA-006`)

* **Project:** ARSE Mk.I Autonomous Traffic Cone (`C:\Users\Andrew\Cone`)
* **Hardware & Runtime:** Pimoroni Yukon (RP2040 / RP2350) running MicroPython + Dedicated ESP32-C3/ESP32-S3 Mesh Co-Processor over `921,600 baud` COBS-framed UART, `PA1010D` GNSS receiver, 9DoF IMU/compass, Dual `VL53L5CX` 8×8 ToF array (`IDEA-002`), Yukon `AudioAmpModule` (`IDEA-003`), 50-LED addressable array, 3-wheel holonomic drive base
* **Protocols:** Inter-cone mesh `arse-swarm/v1` (ESP-NOW with `TTL = 3` multi-hop relay) + Single-Master Web UI / AI Mission Planner (`arse-mission/v1`)
* **Target Folder:** `arse / cone / ideas`

---

## Part 1: Swarm Communication, Positioning & Choreography Architecture

### 1. Split-Plane Dual-Radio Topology

To prevent multi-cone telemetry chatter from saturating the operator's Wi-Fi link—and to avoid soft-AP station limits or slow IP mesh routing-tree rebuilds—each cone separates user communication from inter-cone coordination across two distinct radios:

1. **User-to-Master Plane (Wi-Fi AP / Hotspot):**
   * **Only the Master cone** runs an active Wi-Fi link to the operator's phone or laptop (`http://<master-ip>/`), serving the unified Fleet Radar Web UI and hosting the off-board AI Mission Planner (`IDEA-001`).
   * Follower cones keep their primary Wi-Fi interfaces idle during swarm operation to save `~120 mA` quiescent current and eliminate RF channel contention.
2. **Inter-Cone Swarm Plane (Dedicated ESP-NOW Mesh Co-Processor via UART):**
   * Each Pimoroni Yukon connects via a spare 3.3 V UART header (`921,600 baud`, COBS-framed packets with CRC-16) to a small dedicated ESP32-C3/ESP32-S3 radio module (abstracted behind `SwarmRadioTransport` so exact module variants remain swappable).
   * The co-processor runs connectionless, AES-CCMP encrypted **ESP-NOW broadcasts** on a fixed 2.4 GHz channel separated from the Master's Wi-Fi AP channel, achieving `< 5 ms` single-hop latency for `20 Hz` formation updates.
   * **Bounded-TTL Multi-Hop Relay (`max_hops = 3`):** Each mesh frame includes `(origin_id, seq_num, ttl)`. Any cone receiving an unseen `(origin_id, seq_num)` with `ttl > 1` decrements `ttl` and re-broadcasts immediately, extending fleet range around corners or down long roadworks tapers without routing tables.
3. **Graceful Failover & Dead-Man Safety:**
   * All cones run identical firmware and select their role at boot (`MASTER` vs `FOLLOWER`, switchable via Web UI **Promote to Master**).
   * If any Follower misses `SWARM_HEARTBEAT` / `SWARM_FORMATION_TICK` frames from the Master for `> 1,200 ms`, it automatically enters `SWARM_HOLD`—braking smoothly to `0 m/s`, activating `"strobe"` LEDs, and speaking `"meep"` (`STATE_AWAITING_INPUT`).

---

### 2. Mesh Frame Vocabulary (`arse-swarm/v1`)

* **`SWARM_HEARTBEAT` (`2 Hz` from every node):** Broadcasts `node_id`, `role`, `battery_mv`, `armed`, `gnss_fix_ok`, `local_n_m`, `local_e_m`, `heading_deg`, `tof_cone_locked`, and `state` (`IDLE`, `MOVING`, `PARKED`, `HOLD`, `FAULT`).
* **`SWARM_DATUM_SYNC` (Reliable ACKed frame on join/reset):** Distributes the Master's latched `HomeDatumFilter` integer micro-degrees (`home_lat_u7`, `home_lon_u7`) and fleet geofence radius so every Follower operates in the exact same local tangent plane.
* **`SWARM_FORMATION_TICK` (`20 Hz` broadcast during moving formations):** Streams `seq`, `formation` (`"convoy"`, `"echelon_left"`, `"echelon_right"`, `"vee"`, `"box_escort"`), `spacing_m`, `master_n_m`, `master_e_m`, `master_hdg_deg`, `master_vn_mps`, `master_ve_mps`, and `led_mode`.
* **`SWARM_DEPLOY_SLOT` (Reliable ACKed unicast per Follower):** Assigns a specific Follower to a parked target station `(target_n_m, target_e_m, target_hdg_deg)` with `depart_delay_s`, terminal `led_mode`, and Conish arrival cue.
* **`SWARM_ESTOP_HOLD` (High-priority `TTL = 3` emergency broadcast):** Immediately disarms or halts all cones across the mesh upon operator E-Stop or critical fault (`"ech_ung"`).

---

### 3. Three-Layer Relative Positioning & Retroreflective Cone Lock

1. **Common-Mode GNSS Datum:** Adopting the Master's integer micro-degree origin cancels common-mode satellite and atmospheric drift across the fleet.
2. **Retroreflective Sleeve Optical Lock via `VL53L5CX` (`IDEA-002`):** Standard UK traffic cones carry high-gain retroreflective sleeves that produce a distinctive `signal_per_spad` intensity peak on `940 nm` Time-of-Flight sensors. Within `0.4 m – 3.5 m`, Followers fuse their predicted formation offset with the optical bearing and slant range of the adjacent/leading cone's reflective collar—achieving centimetre-level spacing and straight-line alignment in convoys and tapers.
3. **Mutual VFH Exclusion Bubbles:** Every cone injects neighbouring cones' broadcast `(local_n_m, local_e_m)` coordinates into its local `ReactiveAvoider` (`IDEA-002`) as `0.65 m` dynamic repulsive obstacles so trajectories never collide during slot transitions.

---

### 4. Moving Formations, Static Patterns & Natural-Language Grounding

* **Moving Formations:** `"convoy"` (following the Master's breadcrumb trail from `IDEA-004` + ToF sleeve lock), `"echelon_left"`, `"echelon_right"`, `"vee"`, and `"box_escort"`.
* **Static Parking Patterns:** `"taper"` (diagonal lane closure of length $L$ and lateral shift $W$), `"cordon_line"`, `"cordon_arc"`, `"cordon_polygon"`, and `"portal_gate"`.
* **Hungarian Minimum-Cost Slot Assignment:** Before launching a static deployment, the Master runs a pure-Python Hungarian (Kuhn–Munkres) solver on the $N \times N$ distance matrix between current cone positions and target stations so paths never cross, dispatching cones in `1.5 s` staggered waves (furthest station first) and playing a cascading wave of Conish `"boop"` $\rightarrow$ `"boop"` $\rightarrow$ `"meep_meep"` cues (`IDEA-003`) as each cone locks into place.
* **Natural-Language Spatial Tools (`IDEA-001` Extension):**
  * `resolve_deictic_target("over there")`: Projects `5.0 m` along the Master's live 9DoF compass bearing $\psi_{\text{master}}$ (or uses a Web UI radar tap) to centre a cordon.
  * `extract_lane_taper_geometry(lat, lon, heading_deg)`: Queries OpenStreetMap carriageway bearing and UK single-lane width (`~3.2 m`) to compile a `12 m` diagonal `"taper"` across the lane.

---

## Part 2: Coding AI Implementation Prompt (`agy` / VS Code)

Copy and paste the prompt below directly into your VS Code Gemini CLI (`agy`) session inside `C:\Users\Andrew\Cone`:

```markdown
You are implementing **Multi-Cone Swarming over a Dedicated ESP-NOW Mesh Bridge (`arse-swarm/v1`)** in MicroPython (plus companion ESP32 bridge firmware and Master Web UI fleet controls) for the **ARSE Mk.I Autonomous Traffic Cone** (Pimoroni Yukon RP2040/RP2350).

### Architecture & Integration Context
- **Dual-Radio Split:** Only the designated **Master cone** runs Wi-Fi for the human Web UI (`http://<master-ip>/`) and off-board AI planner (`IDEA-001`). Inter-cone communication runs exclusively over a dedicated ESP32-C3/ESP32-S3 co-processor connected to a Yukon 3.3 V UART header (`921,600 baud`), using connectionless encrypted ESP-NOW broadcasts with a deduplicated `TTL = 3` multi-hop relay.
- **Subsystem Hooks:** Integrates with `DualVL53L5CX` (`sensors/dual_tof.py`, adding `signal_per_spad` retroreflective sleeve peak detection), `ReactiveAvoider` (`nav/obstacle_avoidance.py`, dynamic neighbour exclusion bubbles), `BreadcrumbLogger` (`nav/breadcrumb_logger.py`), and `VoiceEngine` (`audio/voice_engine.py`, Conish cues `"boop"`, `"meep"`, `"ech_ung"`, `"meep_meep"`).

### Tasks to Implement

#### 1. COBS-Framed UART Mesh Transport & ESP32 Co-Processor Bridge (`swarm/mesh_transport.py` & `esp32_bridge/main.py`)
- Implement `SwarmRadioTransport` on the Yukon using `uasyncio` UART streams with **COBS framing** and **CRC-16** integrity checks so binary/JSON `arse-swarm/v1` frames (`<= 240 bytes`) transfer cleanly without blocking motor control.
- Include a 32-entry `(origin_id, seq_num)` deduplication ring buffer and `TTL` decrement logic (`max_hops = 3`) so multi-hop relaying works identically whether handled in the ESP32 bridge firmware or Yukon driver.
- Provide `esp32_bridge/main.py` (MicroPython/ESP-IDF bridge sketch for ESP32-C3/S3) forwarding COBS UART packets to `espnow.ESPNow()` broadcast peers on a fixed configurable Wi-Fi channel.

#### 2. Swarm Node State Machine, Common-Mode Datum & Retroreflective ToF Lock (`swarm/swarm_node.py`)
- Implement `SwarmNode` supporting runtime role switching (`"MASTER"` vs `"FOLLOWER"`):
  - **Followers:** Adopt the Master's `SWARM_DATUM_SYNC` integer micro-degree origin, broadcast `SWARM_HEARTBEAT` at `2 Hz`, and enforce a `1,200 ms` dead-man watchdog (entering `SWARM_HOLD` with `"strobe"` LEDs and `"meep"` if Master ticks cease).
  - **Hybrid Formation Tracking:** Combine `20 Hz` `SWARM_FORMATION_TICK` kinematic offsets (`convoy`, `echelon_left`, `echelon_right`, `vee`, `box_escort`) with `DualVL53L5CX` retroreflective sleeve detection (`high signal_per_spad` peak within `0.4 m – 3.5 m`) for centimetre-level spacing lock, while injecting all neighbour poses from `SWARM_HEARTBEAT` as `0.65 m` repulsive obstacles in `ReactiveAvoider`.

#### 3. Pattern Generator, Hungarian Slot Allocator & Cascading Choreography (`swarm/formation_planner.py`)
- Implement static pattern generators returning `N` target poses `(north_m, east_m, heading_deg)` for `"taper"` (length `L`, lateral shift `W`), `"cordon_line"`, `"cordon_arc"`, `"cordon_polygon"`, and `"portal_gate"`.
- Implement a pure-Python **Hungarian (Kuhn–Munkres) minimum-cost assignment** solver matching current cone poses to target stations without crossing trajectories, assigning `1.5 s` staggered departure delays (furthest station first) and sequential arrival chime delays (`"boop"` $\rightarrow$ `"meep_meep"`).

#### 4. Master Web UI Fleet Dashboard & AI Swarm Grounding (`web/server.py`, `web/index.html`, `web/swarm.js`, `web/ai_planner.js`)
- Expose Master-only endpoints: `GET /api/swarm/roster`, `POST /api/swarm/formation` (`convoy`, `echelon_left`, `vee`, etc.), `POST /api/swarm/deploy` (`taper`, `cordon_polygon`, `portal_gate`, etc.), `POST /api/swarm/estop`, and `POST /api/swarm/promote`.
- Extend the Web UI Tactical Radar to render every online cone (ID, battery, role badge, trajectory vector, and parked status) with one-tap formation/cordon deployment controls and a fleet **ESTOP ALL** button.
- Extend `web/ai_planner.js` with deictic (`"over there"` $\rightarrow$ Master compass projection) and carriageway (`"taper off this lane"` $\rightarrow$ OSM lane bearing + `3.2 m` width) tool definitions so natural-language swarm commands compile directly into `POST /api/swarm/deploy` calls.

### Constraints & Verification
- Target MicroPython on Pimoroni Yukon (`uasyncio`, `ujson`, `struct`, `math`, `utime`, `gc`); keep `20 Hz` formation tick handling allocation-light and ensure `SWARM_ESTOP_HOLD` overrides all active motion immediately.
- Add desktop Python 3 unit tests in `tests/test_swarm_planner.py` verifying COBS + CRC-16 framing, `TTL = 3` deduplication, taper/cordon geometry synthesis, Hungarian non-crossing slot assignment, and dead-man `SWARM_HOLD` timeout behaviour.
```

