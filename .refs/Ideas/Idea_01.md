# ARSE Mk.I — Idea Spec & Coding Prompt: Natural-Language AI Mission Dispatch (`IDEA-001`)

* **Project:** ARSE Mk.I Autonomous Traffic Cone (`C:\Users\Andrew\Cone`)
* **Hardware & Runtime:** Pimoroni Yukon (RP2040 / RP2350) running MicroPython, `PA1010D` GNSS receiver, 9DoF IMU/compass, 50-LED addressable warning array, 25W acoustic annunciator, 3-wheel holonomic drive base
* **Mission Protocol:** `arse-mission/v1` JSON over local Wi-Fi HTTP API (`http://<cone-ip>/`)
* **Target Folder:** `arse / cone / ideas`
* **Representative Prompts:**
  * *"Take Ava to school"* — Multi-leg pedestrian transit from `24 Links Lane` to a saved nursery or school destination via Northstowe greenways and dropped kerbs, culminating in a stationary arrival chime and rainbow light sequence.
  * *"Go and guard the entrance to the nearest Co-op"* — Autonomous transit to the nearest Co-op Food shopfront followed by a continuous oscillating picket patrol across the entrance with amber strobe lights and acoustic alerts.

---

## Part 1: System Architecture & Engineering Design

### 1. Why a Planner–Executor Split Is Essential on the Pimoroni Yukon

Attempting to run natural-language inference or full OpenStreetMap road-and-footway graph routing directly on the Pimoroni Yukon is neither practical nor desirable. While the RP2040/RP2350 has ample deterministic I/O to manage three motor drive modules, parse `PA1010D` NMEA UART sentences, fuse 9DoF compass headings over I2C, drive 50 addressable LEDs, and trigger the 25W audio annunciator, it lacks the RAM and compute headroom for transformer models or continental spatial graphs.

Treating natural-language prompting as an **off-board semantic compiler** that emits deterministic `arse-mission/v1` JSON solves this cleanly:

1. **Zero Token Latency Inside the Real-Time Control Loop:** Holonomic inverse kinematics, heading PID loops, and local ultrasonic/ToF obstacle stops run deterministically under MicroPython `uasyncio` without stalling on cellular or Wi-Fi round-trips.
2. **Pre-Flight Schema & Geofence Enforcement:** Every AI-synthesised waypoint array passes through a deterministic validator (checking waypoint count, minimum spacing, maximum bounding-box radius, and dropped-kerb alignment) before the Yukon arms its motor drivers.
3. **Graceful Offline Degradation:** Once a mission chunk is buffered in flash on the Yukon, the cone executes autonomously even if mobile signal or Wi-Fi drops mid-journey, falling back to an on-board macro parser or safe kerbside hold if connectivity is lost.

---

### 2. Three Progressive Implementation Tiers

#### Tier 1: Mobile PWA + Gemini Tool-Calling Bridge *(Recommended Initial Build)*
Extend the cone's existing browser-based Web UI (`http://<cone-ip>/`) so your mobile phone acts as the semantic co-processor while tethered to the cone's Wi-Fi access point or personal hotspot:

* **Voice & Text Capture:** Enter or dictate *"Go and guard the entrance to the nearest Co-op"* via the Web Speech API in the Mission tab.
* **Client-Side Function-Calling Loop:** JavaScript in the Web UI invokes the Gemini API with four structured tools:
  1. `resolve_saved_alias(name)` — Resolves personal landmarks stored in `aliases.json` (e.g., `"Home"` $\rightarrow$ `24 Links Lane, Northstowe`, `"Ava's Nursery"` $\rightarrow$ Northstowe Nursery / Bella's Childcare in Longstanton).
  2. `search_nearby_poi(query, origin_lat, origin_lon)` — Queries Google Places or OpenStreetMap Nominatim for nearby points of interest such as `"Co-op Food"`.
  3. `get_pedestrian_route(start, end)` — Queries a pedestrian footway routing engine (Valhalla `pedestrian` costing or OSRM `foot` profile) so waypoints follow pavements and greenways rather than the centreline of motor carriageways.
  4. `compile_arse_missions(route_polyline, terminal_behaviour)` — Resamples the footway polyline into `8 m – 15 m` segments (respecting the `3.0 m – 4.0 m` minimum spacing needed for `PA1010D` `1.5 m – 2.5 m` civilian GNSS accuracy), appends terminal choreography (`"guard"` $\rightarrow$ 3-point oscillating shopfront picket with `"lights": "strobe"` and `"sound": "horn"`/`"meep"`), and partitions routes exceeding the 25-waypoint `arse-mission/v1` ceiling into sequential chunks.
* **Operator Radar Verification:** The PWA renders the entire multi-chunk trajectory on the **Tactical GNSS Waypoint Radar** for visual inspection before streaming Chunk 1 to `POST /api/mission`.

#### Tier 2: Companion Raspberry Pi Zero 2 W / Home Server Bridge *(Out-of-Sight Autonomy)*
For remote dispatch when your mobile phone is not physically beside the cone (e.g., messaging the cone from Northstowe Tap & Social to meet you):

* Mount a **Raspberry Pi Zero 2 W** (or an ESP32-S3 LTE modem bridge) inside the cone chassis wired to a spare Yukon UART header.
* A lightweight Python daemon on the Pi maintains a WireGuard/Tailscale tunnel to your home server or a Telegram bot, runs the LLM and pedestrian router off-board, and feeds sliding-window `arse-mission/v1` chunks over UART whenever the Yukon emits `needs_next_chunk` in telemetry.

#### Tier 3: On-Cone MicroPython Offline Alias & Macro Parser *(Zero-Network Fallback)*
Embed a compact deterministic parser (`nav/offline_macros.py`) directly in the Yukon's flash filesystem so routine household tasks execute without internet access:

* Maps canonical commands (`"come home"`, `"return to base"`, `"patrol the drive"`, `"guard the front door"`) to pre-validated `arse-mission/v1` JSON templates stored in `/flash/missions/`.
* Supports parametric geometry synthesis—for example, *"do a 10 metre square patrol"* dynamically generates a 4-waypoint relative-offset mission with `"anchor_to_current_gps": true`.

---

### 3. Pimoroni Yukon & MicroPython Engineering Guardrails

* **Integer Micro-Degree Datum Conversion:** Standard MicroPython builds on RP2040 use 32-bit single-precision floating-point arithmetic (`~7` significant digits), which introduces `~1 m – 2 m` quantisation error on raw WGS84 `lat`/`lon` coordinates (`52.2890606`). Upon parsing incoming `arse-mission/v1` JSON, immediately convert `lat` and `lon` strings/floats into integer micro-degrees (`int(round(deg * 10_000_000))`), subtract the latched `home` datum in integer space, and only then convert to local tangent-plane metres (`north_m`, `east_m`).
* **Sliding-Window Mission Chunking (`> 25` Waypoints):** Because `arse-mission/v1` caps each payload at `25` waypoints and MicroPython heap fragmentation must be avoided, maintain an `active_mission` in RAM alongside a single `staged_next_mission` file on `/flash/staged_mission.json`. When `total_waypoints - active_wp_index <= 5`, set `needs_next_chunk: true` in `/api/telemetry` so the client pre-stages Chunk $N+1$ before Chunk $N$ finishes.
* **Cooperative `uasyncio` & Garbage Collection Discipline:** Isolate the HTTP server, `PA1010D` NMEA UART reader, 9DoF heading estimator, and motor PID controller into separate `uasyncio` coroutines, invoking `gc.collect()` deterministically at waypoint arrival transitions rather than mid-manoeuvre.
* **Pavement, Dropped-Kerb & Dynamic Obstacle Safety:** Because small holonomic omni-wheels will stall against a standard `100 mm` vertical kerb, off-board pedestrian routes must snap road crossings exclusively to dropped kerbs (`kerb=lowered` or `kerb=flush` in OpenStreetMap). On board, local ultrasonic/ToF rangefinders must override GNSS velocity commands whenever a pedestrian, pram, or wheelie bin enters the forward clearance envelope, pausing with `"lights": "strobe"` and a courtesy `"meep"`.

---

## Part 2: Coding AI Implementation Prompt (`agy` / VS Code)

Copy and paste the prompt below directly into your VS Code Gemini CLI (`agy`) session inside `C:\Users\Andrew\Cone`:

```markdown
You are implementing on-board MicroPython firmware support and a Web UI client for Natural-Language AI Mission Dispatch on the **ARSE Mk.I Autonomous Traffic Cone** (Pimoroni Yukon RP2040/RP2350).

### Context & Existing Schema
The cone executes `arse-mission/v1` JSON missions (maximum 25 waypoints per payload; supporting either WGS84 `lat`/`lon` to 7 decimal places or local `north_m`/`east_m` offsets with `"anchor_to_current_gps": true`, `"drive_style"` of `"glide"` or `"turn"`, `"arrival_radius_m"`, `"dwell_s"`, `"sound"`, and `"lights"`).
High-level natural-language prompts (such as *"take Ava to school"* or *"go and guard the entrance to the nearest Co-op"*) compile into multi-leg pedestrian footway routes that frequently exceed 25 waypoints. We need both:
1. **On-Cone MicroPython Segmented Mission Queue & Offline Macro Parser**
2. **Browser Web UI AI Prompt Planner & Chunk Streamer**

### Tasks to Implement

#### 1. MicroPython Integer-Microdegree Datum & Segmented Mission Queue (`nav/mission_queue.py`)
- Implement a memory-safe `MissionQueue` class for MicroPython that prevents 32-bit float quantisation loss on WGS84 coordinates:
  - Convert incoming `lat`/`lon` coordinates into integer micro-degrees (`int(round(val * 10_000_000))`) before computing local tangent-plane `(north_m, east_m)` offsets relative to the latched `home` datum.
  - Support two mission slots: `active_mission` in RAM and `staged_next_mission` persisted to `/flash/staged_mission.json` to keep heap usage low.
  - When `active_mission` completes its final waypoint and `staged_next_mission` is available, seamlessly promote `staged_next_mission` to `active_mission` without disarming motor outputs or resetting the `home` datum.
  - Call `gc.collect()` deterministically at waypoint transitions.

#### 2. Offline Natural-Language Macro Parser (`nav/offline_macros.py`)
- Implement a lightweight deterministic parser for offline fallback commands when the browser has no internet access:
  - Load saved location aliases from `/flash/aliases.json` (seed defaults: `"home"`, `"driveway"`, `"front_door"`).
  - Match canonical command patterns (`"come home"`, `"return to base"`, `"patrol drive"`, `"guard entrance"`, `"square patrol <N>m"`) and synthesise a valid `arse-mission/v1` dictionary on the fly.

#### 3. HTTP API Extensions (`web/server.py`)
- Extend the Yukon's `uasyncio` HTTP server with:
  - `POST /api/mission`: Accept `arse-mission/v1` JSON with an optional `"queue_mode": "replace" | "append"` parameter so the Web UI can load Chunk 1 (`replace`) and pre-stage Chunk 2 (`append`). Enforce schema version, waypoint count (`1..25`), minimum waypoint separation (`>= 2.5 m`), and maximum geofence radius (`<= 2000 m` from `home`).
  - `GET /api/telemetry`: Expose `active_wp_index`, `total_waypoints`, `has_staged_chunk`, `needs_next_chunk` (`true` when `total_waypoints - active_wp_index <= 5` and `not has_staged_chunk`), and `home_datum`.
  - `POST /api/prompt_offline`: Accept `{"prompt": "..."}` and dispatch via `nav/offline_macros.py`.

#### 4. Web UI AI Mission Planner (`web/index.html` & `web/ai_planner.js`)
- Add an **"AI Voice / Text Dispatch"** card to the **Mission** tab in the Web UI:
  - Provide a text prompt field, Web Speech API microphone trigger, and Gemini API key / endpoint configuration saved in `localStorage`.
  - Implement a browser-side tool-calling planner that:
    1. Resolves user-saved place aliases from `localStorage` (`"Home"`, `"Ava's School"`, etc.) or queries OpenStreetMap Nominatim / Overpass API for nearby POIs (e.g., `"Co-op"`).
    2. Queries a pedestrian footway router (OSRM `foot` profile or Valhalla `pedestrian` profile) for pavement geometry, resampling waypoints at `8 m – 15 m` intervals.
    3. Appends terminal choreography based on intent verbs (`"guard"` $\rightarrow$ 3-point oscillating shopfront patrol with `"strobe"` lights and `"horn"`/`"meep"` alerts; `"take"` / `"deliver"` $\rightarrow$ arrival `"chime"`, `"rainbow"` lights, and `dwell_s: 10.0`).
    4. Partitions routes longer than 25 waypoints into sequential `arse-mission/v1` chunks, renders the full multi-chunk route on the Tactical GNSS Radar for operator approval, and streams subsequent chunks via `POST /api/mission` (`"queue_mode": "append"`) whenever `needs_next_chunk` becomes `true` in `/api/telemetry`.

### Constraints & Verification
- Target MicroPython on Pimoroni Yukon (`uasyncio`, `ujson`, `math`, `gc`); avoid CPython-only libraries.
- Preserve all hardware safety interlocks: never auto-arm motors from an AI prompt; require explicit operator **Arm** confirmation in the Web UI before motion starts.
- Add unit tests in `tests/test_mission_queue.py` runnable under desktop Python 3 to verify integer micro-degree conversion accuracy, chunk promotion, and schema validation.
```

