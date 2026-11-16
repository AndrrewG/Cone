# ARSE — Autonomous Road Systems Engineering
## Mk.I Autonomous Traffic Cone — Engineering Roadmap & Task Register

> **Document Ref:** `ARSE-ENG-TRK-001`  
> **Division:** Conical Mobility & Tactical Roadway Guidance  
> **Platform:** Pimoroni Yukon (`RP2040` / `RP2350`) • MicroPython Runtime  
> **Hardware Baseline:** [HARDWARE.md](HARDWARE.md) (`[INSTALLED]` vs `[PLANNED]` Source of Truth)

---

## Completed Milestones
- [x] **Hardware Bring-up & Slot Verification:** Verified all 5 populated Yukon module slots (2x Dual Motor, LED Strip, RM2 Wireless, Audio Amp) and I2C sensor bus (`ICM-20948` 9DoF IMU at `0x68`, `PA1010D` GNSS at `0x10`) via [hardware_test.py](hardware_test.py).
- [x] **Core Sensor & Actuator Drivers:** Implemented MicroPython drivers for IMU ([icm20948.py](drivers/icm20948.py)), non-blocking GPS NMEA streaming ([pa1010d.py](drivers/pa1010d.py)), 3-wheel equilateral omni-drive kinematics ([omni_kinematics.py](drivers/omni_kinematics.py)), and hazard lighting ([lights.py](drivers/lights.py)).
- [x] **Single-Motor Bench Qualification & ADC Tare:** Verified smooth PWM speed-ramped forward/reverse operation on `JGA25-370DG` drive unit (Slot 1, Motor 1) and characterised Yukon's `~0.35 A` zero-current e-Fuse floor ([motor_test.py](motor_test.py)).
- [x] **Automated Field-Sync Tooling:** Built chunked Raw-REPL USB serial uploader ([sync_all.ps1](tools/sync_all.ps1)), wireless HTTP OTA sync ([sync_wifi.ps1](tools/sync_wifi.ps1)), and diagnostic executor ([exec_repl.ps1](tools/exec_repl.ps1)).
- [x] **Coulomb-Counting Battery SoC & Mobile Web Telemetry UI:**
  - Pure `10 Hz` Coulomb counting (`discharged_mah` / `remaining_mah`) in [battery.py](drivers/battery.py) with atomic flash persistence (`battery_state.json`) and zero idle voltage-curve drift.
  - Smart Wi-Fi manager ([wifi_manager.py](drivers/wifi_manager.py)) with `TrafficCone` AP failover.
  - Configurable capacity (default `2200 mAh`), 2-tap **"New Full Battery Inserted (100%)"** reset, manual % override, and swap detection.
- [x] **Corporate Engineering Standards (ARSE):** Applied formal **ARSE (Autonomous Road Systems Engineering)** header blocks and documentation across firmware and UI.
- [x] **Task 3 — Phone Remote Control, Single-Motor Bench & Auxiliary Actuators:**
  - Verified live **Single-Motor Bench & Live Current** UI ([index.html](web/index.html)) driving Slot 1 Motor 1 with live e-Fuse current feedback.
  - Added 4-tab mobile Operator Terminal ([index.html](web/index.html)) with virtual 2D holonomic joystick (`vx`, `vy`) + spin buttons (`omega`), live 3-wheel output bars, arming interlock, speed caps (`Indoor 35%`, `Normal 65%`, `Turbo 100%`), persistent **E-STOP**, and `650 ms` deadman watchdog ([drive_controller.py](drivers/drive_controller.py)).
  - Upgraded [lights.py](drivers/lights.py) to non-blocking `20 Hz` effects (`Off`, `Beacon`, `Strobe`, `Solid`, `Pulse`, `Police`, `Rainbow` + brightness) with automatic Slot 4 `5 V` power gating.
  - Installed speaker on Slot 6 `TAS2780` amp and verified formant WAV vocaliser (`MEEP!` / `Meep Meep!` via [meep.wav](meep.wav) & [generate_sfx.ps1](tools/generate_sfx.ps1)) and soundboard ([audio_sfx.py](drivers/audio_sfx.py)).
- [x] **Task 4 — 9DoF Compass Heading & Attitude Safety (`ICM-20948` + `AK09916`):**
  - Extended [icm20948.py](drivers/icm20948.py) with I2C bypass access to internal `AK09916` magnetometer (`0x0C`) with die-to-body axis alignment.
  - Added phone-triggered `360 deg` spin calibration (`compass_cal.json`), tilt-compensated gyro/compass complementary heading filter (`0–360 deg`), and debounced tip-over / pickup safety cutoff (`> 40 deg`).
- [x] **Task 5 — Autonomous GPS Waypoint Navigation:**
  - Upgraded [pa1010d.py](drivers/pa1010d.py) to parse NMEA directly into `1e-7` degree integers (`0.011 m` resolution on 32-bit MicroPython).
  - Built closed-loop holonomic waypoint follower & mission manager ([navigator.py](drivers/navigator.py)) supporting `Glide` (hold heading) and `Turn & Drive` styles, `Loop` mode, `Return-to-Home (RTH)`, `mission_state.json` persistence, and safety auto-pause on GPS loss / tilt / critical battery.
  - Added interactive top-down **Tactical GNSS Radar Map** to [index.html](web/index.html) supporting both **Drop Cone GPS** and **Tap-Radar-to-Plot** waypoints.
- [x] **Task 6 — Diagnose & Fix Rapid `MEEP!` Button Spam Crash & Pop-Free Amp Shutdown:**
  - Replaced Pimoroni's blocking `WavPlayer` in [audio_sfx.py](drivers/audio_sfx.py) with a direct, zero-spinlock `machine.I2S` ping-pong DMA streamer (`512 B` chunks, `3072 B` ring buffer, continuous clock without `I2S.deinit()` so `CYW43` `DMA_IRQ_0` Wi-Fi stays alive), atomic `/meep.wav` rewind (`seek(44)`), 3-stage pop-free `TAS2780` idle shutdown (`DVC=0xCF` + `MODE_CTRL=0x81` $\rightarrow$ `MODE_CTRL=0x02` Hi-Z $\rightarrow$ `SDZ` low), and zero-allocation `512 B` HTML chunk streaming in [telemetry_server.py](telemetry_server.py).
- [x] **Task 7 — Perceptual / Logarithmic Audio Volume Curve:**
  - Replaced linear mapping with a Stevens' Power Law psychoacoustic taper (`dB = -4.0 + 33.22 * log10(v)`, hard mute `0xCF` at `0%`, `-4.0 dB` transducer-safe ceiling at `100%`) directly on `TAS2780` register `0x1A` (`DVC`) in [audio_sfx.py](drivers/audio_sfx.py), with live dB feedback in [index.html](web/index.html).
- [x] **Task 8 — AI-Generated Waypoint Mission Upload & Gemini Specification Document:**
  - Implemented `arse-mission/v1` bulk importer in [navigator.py](drivers/navigator.py) & [telemetry_server.py](telemetry_server.py) (`POST /api/nav/upload`) supporting both 7-decimal WGS84 `lat`/`lon` and relative `north_m`/`east_m` offsets, plus per-waypoint `dwell_s`, `sound`, and `lights` choreography, alongside standalone [MISSION_FORMAT_SPEC.md](MISSION_FORMAT_SPEC.md).
- [x] **Task 9 — Client-Side High-Zoom Satellite Map Window in Web UI:**
  - Embedded zero-Cone-bandwidth Leaflet + Esri World Imagery aerial/satellite map window (Zoom 22) on the **Mission** tab of [index.html](web/index.html) with automatic offline fallback to the Tactical Radar Canvas.
- [x] **Task 10 — Anti-Theft IMU Sentry Mode ("Unauthorised Cone Displacement Deterrence"):**
  - Implemented [sentry.py](drivers/sentry.py) (`SentryGuard`) with `3000 ms` arming countdown, `0 mA` e-Fuse power cut (`yukon.disable_main_output()`), `CYW43` beacon power-save, `5 Hz` (`200 ms`) 6-axis IMU tamper watch across `High`/`Medium`/`Low` presets, and `12000 ms` Police strobe + `25 W` siren deterrence with auto-rearm.
- [x] **Engineering Governance, Subagent Team & Canonical Hardware Specification:**
  - Configured three-role Antigravity subagent workflow (`implementer`, `verifier`, `orthogonal-builder` under `.agents/agents/`) and authored canonical [HARDWARE.md](HARDWARE.md) separating `[INSTALLED]` hardware from `[PLANNED]` expansion modules.

---

## Incremental Feature Backlog (Single-Slice Workflow)

> **Workflow Rule ([Agents.md](Agents.md)):** Implement **one sub-task per session**, verify with adversarial desktop `pytest` tests under `tests/`, run a physical bench/floor check (never auto-arming motors), and pause for operator sign-off before advancing. Never import or initialise `[PLANNED]` hardware from [HARDWARE.md](HARDWARE.md) Section 3 until physically fitted and promoted to Section 2.

---

### Priority 1 — Web UI Security & Access Control (`IDEA-007` • `[INSTALLED]` Hardware)

- [x] **Task 1.1 — Zero-Dependency Auth, Session & Brute-Force Lockout Engine ([auth_manager.py](security/auth_manager.py)):**
  - Auto-bootstrap git-ignored `/flash/secrets.json` (`secrets.json` on MicroPython root) on first boot with `username` (`"admin"`), `16-byte` random `password_salt_hex`, `1,000`-round iterated salted `uhashlib.sha256` `password_hash_hex` (printing the generated initial password once to USB serial REPL), and a `24-byte` URL-safe `beacon_token`.
  - Implement `hash_password(password_str, salt_bytes, rounds=1000)` and `constant_time_compare(a, b)` via fixed-length XOR byte accumulation.
  - Maintain a pre-allocated **4-slot RAM `Bearer` session table** (`3600 s` rolling idle TTL, evicting the oldest slot when full) and a pre-allocated **8-slot IP brute-force rate-limiter** (`5` failed logins within `60000 ms` $\rightarrow$ `300000 ms` lockout, triggering `"strobe"` LEDs and optional acoustic fault hook).
  - Expose `login()`, `validate_bearer()` (returning `"operator"`, `"beacon_only"`, or `None`), `logout_all()`, and atomic `update_credentials(new_username, new_password, rotate_beacon=False)`.
  - **Verification:** Adversarial desktop `pytest` suite in `tests/test_auth_manager.py` covering iterated `SHA-256` vectors, constant-time equality, 4-slot eviction, 8-slot IP lockout/expiry, and `"beacon_only"` token scoping.

- [x] **Task 1.2 — HTTP Auth Endpoints & `Bearer` Route Protection Middleware (`web/server.py` / [telemetry_server.py](telemetry_server.py)):**
  - Add `POST /api/auth/login` (`200` + `session_token`, `401` invalid credentials, `429` + `Retry-After: 300` when IP-locked), `POST /api/auth/logout` (clearing active RAM sessions and disarming drive motors if no autonomous mission is running), `POST /api/auth/credentials` (`"operator"` scope), and unauthenticated `GET /api/health` (`{"online": true, "locked": bool}`).
  - Leave `GET /` (and static UI shell assets) unauthenticated so browsers can render the login modal, while requiring `Authorization: Bearer <token>` with `"operator"` scope on all `/api/*` control, telemetry, mission, sentry, and OTA routes.
  - Enforce `"beacon_only"` least-privilege scope: permit **only** `POST /api/follow/beacon` and `POST /api/follow/stamp`, returning `HTTP 403 Forbidden` on every other endpoint; ensure login never auto-arms drive motors.
  - **Verification:** Desktop `pytest` suite (`tests/test_auth_middleware.py`) testing unauthenticated `401` rejections, `429` lockout headers, `"beacon_only"` `403` isolation, logout disarming, and OTA route protection.

- [x] **Task 1.3 — Browser Login Modal, Autofill & Credential Controls ([index.html](web/index.html) & [auth.js](web/auth.js)):**
  - Add a semantic `<form>` **Login Overlay Modal** (`<input name="username" autocomplete="username">` and `<input type="password" name="password" autocomplete="current-password">`) compatible with desktop browsers and Android Google Password Manager fingerprint autofill over HTTP.
  - Implement `apiFetch(url, options)` in `web/auth.js` storing `session_token` in `sessionStorage`, injecting `Authorization: Bearer <session_token>`, and re-opening the login modal automatically on `HTTP 401`.
  - Add a **Security & Account** card in the Web UI with **Lock Cone Now (Logout)**, **Update Username / Password**, and **Copy / Rotate Locked-Phone Beacon Token** controls.
  - **Verification:** Desktop tests + live bench browser/phone login, lockout, password-change, and logout disarm check.

---

### Priority 2 — GPS-Free Holonomic Movement Routines (`IDEA-005` • Phase 1 `[INSTALLED]` Hardware Only)

- [x] **Task 2.1 — `50 Hz` IMU Dead-Reckoning Core & `compass_box` Routine (`nav/pattern_runner.py`):**
  - Build zero-allocation `50 Hz` (`20 ms`) `PatternRunner` anchoring local Euclidean origin `(x_m = 0.0, y_m = 0.0)` and reference IMU heading `psi_0_deg` at start, integrating body velocity `(vx_mps, vy_mps)` rotated by measured 9DoF heading `psi_deg`, and running closed-loop yaw hold (`omega_dps`).
  - Enforce trapezoidal linear (`accel_mps2`) and angular (`alpha_dps2`) ramps, `"indoor"` (`max_speed_mps = 0.45`, `max_leash_m = 1.8`, `a_c_max_mps2 = 0.75`) and `"outdoor"` (`max_speed_mps = 0.95`, `max_leash_m = 5.5`, `a_c_max_mps2 = 1.15`) presets, virtual circular leash halt (`LEASH_LIMIT_REACHED`), optional `range_provider=None` / `sound_callback=None` hooks, and strict motor-armed gate (`HTTP 409` if disarmed).
  - Implement the **`compass_box`** locked-heading square strafe (`+X` $\rightarrow$ `-Y` $\rightarrow$ `-X` $\rightarrow$ `+Y`) + `POST /api/routines/start` & `POST /api/routines/stop` + live `(local_x_m, local_y_m)` telemetry fields.
  - **Verification:** Desktop `pytest` suite (`tests/test_pattern_runner.py`) verifying zero-GNSS local odometry, trapezoidal ramps, disarmed rejection, virtual leash halt, and `compass_box` leg transitions.

- [ ] **Task 2.2 — Closed-Loop `circle` Routine & Centripetal Tip-Over Clamping (`nav/pattern_runner.py`):**
  - Implement `circle(radius_m, laps, direction, speed_mps, orientation_mode)` with closed-loop radial restoring velocity $v_{\text{radial}} = K_r (r_{\text{target}} - r_{\text{actual}})$, `"cw"` / `"ccw"` orbit direction, `"pulse"` LEDs, and all four holonomic orientation modes (`"tangent"`, `"compass_locked"`, `"orbit_inward"`, `"orbit_outward"`).
  - Enforce centripetal tip-over speed clamping $v_{\text{safe}} = \min\left(v_{\text{req}}, \sqrt{a_{c,\max} \cdot r}\right)$ across `"indoor"` and `"outdoor"` presets.
  - **Verification:** Desktop `pytest` tests verifying centripetal speed clamping on tight radii, radial drift correction, and all four orientation modes.

- [ ] **Task 2.3 — Regular `polygon` Routine (`arse-mission/v1` Export) & `sentry` Picket (`nav/pattern_runner.py`):**
  - Implement `polygon(sides, side_length_m, laps, vertex_dwell_s, orientation_mode)` ($n \ge 3$) computing circumradius $R = \frac{s}{2\sin(\pi/n)}$, trapezoidal leg profiles, corner dwells, and `export_polygon_as_mission()` emitting valid relative-metre `arse-mission/v1` JSON (`anchor_to_current_gps: true`, $n \le 24$).
  - Implement `sentry(width_m, dwell_s, speed_mps)` oscillating laterally across a `width_m` picket line while holding outward heading `psi_0_deg` with `"police"` / `"strobe"` LEDs.
  - **Verification:** Desktop `pytest` tests covering circumradius/vertex maths, `arse-mission/v1` export bounds ($n > 24$ rejection), and picket oscillation reversals.

- [ ] **Task 2.4 — Trapezoidal `pirouette`, `figure8` & Archimedean `spiral` Routines (`nav/pattern_runner.py`):**
  - Implement `pirouette(sub_mode="spot"|"waltz", revolutions, max_yaw_dps, orbit_radius_m)` ramping angular rate at `alpha_dps2 = 240.0` and decelerating cleanly onto `psi_0_deg` with `"rainbow"` LEDs.
  - Implement `figure8(lobe_radius_m, laps, speed_mps, orientation_mode)` (two tangent counter-rotating lobes with smooth zero-yaw crossover) and `spiral(start_radius_m, end_radius_m, pitch_m, speed_mps)` ($r(\theta) = r_0 + b\theta$ with dynamic centripetal speed clamping up to `end_radius_m` / `max_leash_m`).
  - **Verification:** Desktop `pytest` tests verifying exact `pirouette` revolution stopping angle, `figure8` lobe handover, and `spiral` radius/leash termination.

- [ ] **Task 2.5 — Leash-Bounded Lévy-Flight `chaos` Mode (`nav/pattern_runner.py`):**
  - Implement `chaos(duration_s, max_leash_m, burst_speed_mps)` generating bounded random velocity vectors, spin rates, and burst durations (`800–2500 ms`), elastically reflecting the velocity vector inward upon reaching `max_leash_m` (or `range_provider < 0.85 m` when present) with `"strobe"` LED flashes.
  - **Verification:** Desktop `pytest` Monte Carlo / boundary tests proving `(x_m, y_m)` remains bounded by `max_leash_m` across 1,000+ simulated ticks.

- [ ] **Task 2.6 — Interactive `timid` (Startled Cone) Mode & Web UI Routines Panel (`nav/pattern_runner.py`, `web/index.html`, `web/routines.js`):**
  - Implement `timid(dodge_distance_m=0.8, sensitivity=1.0)` holding motionless with breathing `"pulse"` LEDs until triggered by an IMU accelerometer tap/floor-stomp jerk spike or `POST /api/routines/trigger` (`{"event": "startle"}`)—then darting laterally `dodge_distance_m`, rotating `+X` nose toward the disturbance, flashing `"strobe"`, and re-freezing.
  - Build the **Routines & Tricks** Web UI panel (`web/routines.js` & `web/index.html`) with Indoor/Outdoor preset toggle, parameter cards for all 9 routines, **Startle Cone!** trigger, **Export Polygon to Mission JSON**, and live GNSS-free `(local_x_m, local_y_m)` radar trajectory plotting.
  - **Verification:** Desktop `pytest` IMU jerk-trigger & dodge-vector tests + bench dry-run tap/UI verification.

---

### Priority 3 — Conish Acoustic Language Engine (`IDEA-003` / `arse-lang/v1` • `[INSTALLED]` Slot 6 `TAS2780`)

- [ ] **Task 3.1 — Zero-Allocation Parametric & WAV `VoiceEngine` (`audio/voice_engine.py` & `conish_lexicon.json`):**
  - Implement `VoiceEngine` and seed `/conish_lexicon.json` (`schema: "arse-lang/v1"`: `"meep"`, `"boop"`, `"ech_ung"`, `"uh_oh"`, `"brrr_up"`, `"meep_meep"`) with pre-allocated `int16` waveform lookup tables (`"sine"`, `"triangle"`, `"sawtooth"`, `"square"`), linear pitch/envelope syllable synthesis, `.wav` clip fallback, 4-slot priority queue (`1..4` with higher-priority preemption), per-word `cooldown_ms`, and transient 50-LED `led_overlay` sync.
  - Strictly obey [HARDWARE.md](HARDWARE.md) Section 2.6 (`TAS2780` `-4.0 dB` `5 W` speaker ceiling, 3-stage pop-free shutdown, and zero `I2S.deinit()` calls to protect `CYW43` Wi-Fi).
  - **Verification:** Desktop `pytest` suite (`tests/test_voice_engine.py`) testing event-to-word mapping, priority preemption, cooldown suppression, WAV header validation/fallback, and `arse-lang/v1` schema checks.

- [ ] **Task 3.2 — Firmware Semantic Event Wiring (`main.py`, `auth_manager.py`, `drive_controller.py`, `navigator.py`, `pattern_runner.py`):**
  - Connect semantic intent tokens (`STATE_AWAITING_INPUT`, `STATE_ARMED`, `ACK_COMMAND`, `ACK_MISSION_START`, `DATUM_LOCKED`, `WARN_OBSTACLE_SIDESTEP`, `ERR_INVALID_MISSION`, `ERR_SYSTEM_FAULT`, `EVENT_MISSION_COMPLETE`) to `voice.say_event(...)` while keeping `arse-mission/v1` waypoint `"sound"` keys backwards-compatible.
  - **Verification:** Desktop `pytest` integration tests verifying semantic event dispatch and cooldown non-blocking behaviour.

- [ ] **Task 3.3 — Lexicon HTTP API & Browser Soundboard / Syllable Editor (`web/server.py`, `web/index.html`, `web/lexicon.js`):**
  - Add authenticated `GET /api/lexicon`, `POST /api/lexicon` (validating `arse-lang/v1` JSON and atomically reloading), and `POST /api/say`, plus a **Conish Voice & Lexicon** Web UI panel for auditioning, editing, and saving custom Conish words.
  - **Verification:** Desktop `pytest` malformed-lexicon rejection tests + live Slot 6 speaker bench audition.

---

### Priority 4 — Dual `VL53L5CX` Collision Avoidance & Frontier Mapping (`IDEA-002` • **Gated on `[PLANNED]` Hardware in [HARDWARE.md](HARDWARE.md) §3.1**)

- [ ] **Task 4.1 — Bench Bring-Up & Dual `VL53L5CX` 16-Bin Horizon Driver (`sensors/dual_tof.py`):**
  - *Prerequisite:* Physically wire two Pimoroni `VL53L5CX` (`PIM622`) breakouts, confirm `LPN_LEFT` / `LPN_RIGHT` GPIOs and bezel angles on bench, and promote §3.1 to Section 2 (`[INSTALLED]`) in [HARDWARE.md](HARDWARE.md).
  - Implement `LPn` boot address split (`0x30` Left, `0x29` Right), staggered `15 Hz` `8×8` ranging, sunlight status/sigma filtering (`status in (5, 9)`, `sigma_mm <= 45`), 16-bin `87 deg` clearance fusion, and ground-row (`6–7`) raised-kerb / drop-off virtual hazard injection (`0.45 m`) + `tests/test_dual_tof.py`.

- [ ] **Task 4.2 — 16-Bin VFH Reactive Avoidance Filter (`nav/obstacle_avoidance.py`):**
  - Implement `ReactiveAvoider` between navigation/routines and `Omni3Kinematics` (`> 1.80 m` pass-through, `0.75–1.80 m` forward attenuation + holonomic lateral sidestep, `< 0.75 m` `3000 ms` halt + bounded box-detour / `OBSTACLE_BLOCKED`) plus `"glide"` lookahead yaw guard + `tests/test_obstacle_avoidance.py`.

- [ ] **Task 4.3 — Zero-Allocation `6,400 B` Occupancy Grid & Frontier Explorer (`nav/occupancy_mapper.py`):**
  - Implement flat `80 × 80` (`0.25 m` cell, `20 m × 20 m`) `bytearray` log-odds grid, integer Bresenham ray-casting, 4-connected frontier cluster scoring within `max_explore_radius_m`, `180 deg` holonomic sweep scans, and `memoryview` RLE compression + `tests/test_occupancy_mapper.py`.

- [ ] **Task 4.4 — Exploration HTTP API & Live Radar Occupancy Overlay (`web/server.py`, `web/radar_map.js`):**
  - Expose `GET /api/map` and `POST /api/explore` (never auto-arming motors), render the RLE occupancy map under the Tactical Radar canvas, and add **Click-to-Go** + **Export Safe Patrol** (`arse-mission/v1`).

---

### Priority 5 — Off-Board AI Mission Dispatch & Segmented Queue (`IDEA-001` • `[INSTALLED]` Hardware + Browser PWA)

- [ ] **Task 5.1 — Integer Micro-Degree Datum & Sliding-Window `MissionQueue` (`nav/mission_queue.py`):**
  - Implement two-slot mission buffering (`active_mission` in RAM + `/staged_mission.json` on flash) with `1e-7` integer micro-degree tangent-plane maths, seamless chunk promotion (`> 25` total waypoints) without disarming motors, deterministic `gc.collect()`, `needs_next_chunk` telemetry flag, and `POST /api/mission` (`"replace"` | `"append"`, `>= 2.5 m` spacing & `<= 2000 m` geofence validation) + `tests/test_mission_queue.py`.

- [ ] **Task 5.2 — On-Cone Offline Alias & Parametric Macro Compiler (`nav/offline_macros.py`):**
  - Implement zero-network deterministic command parsing (`aliases.json`, `"come home"`, `"return to base"`, `"patrol drive"`, `"square patrol <N>m"`) via `POST /api/prompt_offline` + `tests/test_offline_macros.py`.

- [ ] **Task 5.3 — Browser PWA Gemini Tool-Calling Planner & Chunk Streamer (`web/index.html` & `web/ai_planner.js`):**
  - Add text + Web Speech API prompt dispatch, client-side Gemini tool-calling (saved aliases, Nominatim/Overpass POI lookup, footway routing snapped to dropped kerbs, `8–15 m` resampling, terminal choreography), multi-chunk radar preview, and automatic `needs_next_chunk` background upload.

---

### Priority 6 — Locked-Phone Follow-Me & Breadcrumb Logging (`IDEA-004`)

- [ ] **Task 6.1 — Source-Arbitrated `FollowController` & Alpha-Beta Predictor (`nav/follow_controller.py`):**
  - Fuse `"websocket"` (`5 Hz`, `1500 ms` deadman) and `"http_beacon"` (`1–2 Hz`, `4500 ms` deadman) phone fixes (`acc_m <= 8.0 m`, `1e-7` integer micro-degrees) with alpha-beta velocity/course extrapolation, `"trail"` / `"escort"` offsets, and pluggable `DualVL53L5CX` close-range (`<= 3.2 m`) silhouette lock + `tests/test_follow_controller.py`.

- [ ] **Task 6.2 — Cone-Pose `BreadcrumbLogger` & RDP Mission Compiler (`nav/breadcrumb_logger.py`):**
  - Record decimated cone poses (`>= 12.0 m` straight leg or `>= 25 deg` turn after `>= 4.0 m`), support manual/auto `4000 ms` kerb-crossing dwell stamps, and simplify via Ramer-Douglas-Peucker into `arse-mission/v1` missions + `tests/test_breadcrumb_logger.py`.

- [ ] **Task 6.3 — Follow WebSocket, OwnTracks Scoped Beacon API & Web UI (`web/server.py`, `web/follow.js`):**
  - Expose `/ws/follow`, `"beacon_only"`-compatible `POST /api/follow/beacon` (OwnTracks + flat JSON), `/config`, `/stamp`, and `/save_mission` (preserving motor arming interlocks), plus the **Follow-Me & Route Recorder** UI panel with Wake Lock and OwnTracks setup card.

---

### Priority 7 — Multi-Cone ESP-NOW Mesh Swarming (`IDEA-006` / `arse-swarm/v1` • **Gated on `[PLANNED]` Hardware in [HARDWARE.md](HARDWARE.md) §3.2**)

- [ ] **Task 7.1 — COBS + CRC-16 UART Mesh Transport & ESP32 Bridge (`swarm/mesh_transport.py` & `esp32_bridge/main.py`):**
  - *Prerequisite:* Wire dedicated `ESP32-C3`/`ESP32-S3` co-processor, confirm UART pins on bench, and promote §3.2 to Section 2 (`[INSTALLED]`) in [HARDWARE.md](HARDWARE.md).
  - Implement `921600 baud` COBS-framed + `CRC-16` `uasyncio` UART transport (`<= 240 B`), 32-entry `(origin_id, seq_num)` deduplication ring buffer, `TTL = 3` multi-hop relay, and ESP32 ESP-NOW broadcast bridge + `tests/test_mesh_transport.py`.

- [ ] **Task 7.2 — `SwarmNode` Role Engine, Datum Sync & Retroreflective Collar Lock (`swarm/swarm_node.py`):**
  - Implement `MASTER` / `FOLLOWER` state machine, `SWARM_DATUM_SYNC` common-mode `1e-7` deg origin, `2 Hz` `SWARM_HEARTBEAT`, `1200 ms` `SWARM_HOLD` deadman cutoff, `SWARM_ESTOP_HOLD` fleet emergency disarm, `20 Hz` moving formations (`convoy`, `echelon_left`, `echelon_right`, `vee`, `box_escort`), `0.65 m` VFH neighbour bubbles, and `VL53L5CX` `signal_per_spad` retroreflective sleeve lock + `tests/test_swarm_node.py`.

- [ ] **Task 7.3 — Static Roadworks Geometries & Hungarian Non-Crossing Slot Allocator (`swarm/formation_planner.py`):**
  - Generate `N`-cone target stations for `"taper"`, `"cordon_line"`, `"cordon_arc"`, `"cordon_polygon"`, and `"portal_gate"`, paired with a pure-Python Kuhn–Munkres minimum-cost assignment solver, `1500 ms` staggered departures, and cascading Conish arrival cues + `tests/test_formation_planner.py`.

- [ ] **Task 7.4 — Master Fleet Dashboard & Deictic / Carriageway AI Grounding (`web/server.py`, `web/swarm.js`, `web/ai_planner.js`):**
  - Expose Master fleet endpoints (`/api/swarm/roster`, `/formation`, `/deploy`, `/estop`, `/promote`), multi-cone Tactical Radar badges + **ESTOP ALL**, and deictic (`"over there"`) / OSM lane-taper tools.

---

## Ongoing Mechanical, Bench & Repository Housekeeping
- [ ] **Known Issues Log ([ISSUES.md](ISSUES.md)):** Investigate `ISSUE-001` — audible speaker pop when the Slot 6 `TAS2780` audio amplifier turns off ([drivers/audio_sfx.py](drivers/audio_sfx.py)).
- [ ] **Repository & Tooling Cleanup:** Commit verified firmware + [HARDWARE.md](HARDWARE.md) baseline to `git` and prune any temporary diagnostic scripts in `tools/`.
- [ ] **Slot 4 Physical LED Wire Mounting:** Mount the `50`-LED `WS2812` wire onto the cone body and verify all modes (`Beacon`, `Strobe`, `Rainbow`, etc.) and `5 V` `PGOOD` status via the Web UI.
- [ ] **Full 3-Channel Chassis Assembly & Calibration:** Once the 3D-printed equilateral triangle base is ready, wire Motors 1–3, verify `wheel_angles_deg`, `motor_polarity`, and `ICM-20948` nose alignment, and measure exact `chassis_radius_m` (`ROBOT_BASE_RADIUS_M`) in [config.py](config.py) & [HARDWARE.md](HARDWARE.md).
