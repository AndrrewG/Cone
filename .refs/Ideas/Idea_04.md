# ARSE Mk.I — Idea Spec & Coding Prompt: Locked-Phone Follow-Me & Breadcrumb Waypoint Logging (`IDEA-004`)

* **Project:** ARSE Mk.I Autonomous Traffic Cone (`C:\Users\Andrew\Cone`)
* **Hardware & Runtime:** Pimoroni Yukon (RP2040 / RP2350) running MicroPython, `PA1010D` GNSS receiver, 9DoF IMU/compass, Dual `VL53L5CX` 8×8 ToF array (`IDEA-002`), Yukon `AudioAmpModule` (`IDEA-003`), 50-LED addressable warning array, 3-wheel holonomic drive base
* **Communication & Output Schemas:** Foreground WebSocket (`ws://<cone-ip>/ws/follow`) + Locked-Phone HTTP Beacon (`POST /api/follow/beacon`, OwnTracks-compatible) $\rightarrow$ compiles to `arse-mission/v1` JSON
* **Target Folder:** `arse / cone / ideas`

---

## Part 1: System Architecture & Engineering Design

### 1. Dual-Channel Phone-to-Yukon Telemetry Architecture

Mobile browsers (Android Chrome and iOS Safari) suspend JavaScript execution and throttle `navigator.geolocation.watchPosition()` within seconds of the screen locking. To allow the **ARSE Mk.I** to follow you reliably while your phone is locked in your pocket—while still providing rich live radar telemetry and one-tap controls when unlocked—the follow-me subsystem supports two interchangeable input transports feeding a unified `FollowController`:

#### Channel A: Foreground Web UI WebSocket (`ws://<cone-ip>/ws/follow` at `5 Hz`)
* **Low-Overhead Persistent Socket:** Avoids repeated TCP handshakes and lwIP socket exhaustion on the RP2040/RP2350 by streaming compact `~70`-byte JSON frames every `200 ms` (`{"lat": 52.2890606, "lon": 0.0524495, "acc_m": 3.0, "vel_mps": 1.2, "cog_deg": 182.0}`) and receiving live cone pose, ToF target-lock status, and newly logged breadcrumb waypoints in return.
* **Browser Secure-Origin & Wake Lock Handling:** Requests `navigator.wakeLock.request('screen')` while active and works over local self-signed TLS (`https://cone.local/`) or Android Chrome's insecure-origin flag during street testing.

#### Channel B: Locked-Phone Background HTTP Beacon (`POST /api/follow/beacon` at `1–2 Hz`)
* **Native Background Location Compatibility:** Accepts standard JSON HTTP POSTs (authenticated via a lightweight `token` query parameter or header) from background-capable mobile apps that run as OS-privileged services with the screen off—specifically **OwnTracks** (open-source iOS/Android HTTP mode emitting `{"_type": "location", "lat": ..., "lon": ..., "acc": ..., "vel": ..., "cog": ...}`), **Home Assistant Companion App** high-accuracy background webhooks, **Tasker** (Android), or **iOS Shortcuts**.
* **Adaptive Dead-Man Watchdog & Alpha-Beta Predictor:**
  * Because background HTTP beacons arrive at `1–2 Hz` rather than `5 Hz`, `FollowController` runs a linear **alpha-beta kinematic predictor** using reported speed (`vel`) and course-over-ground (`cog`) to project your live position smoothly between updates.
  * The watchdog timeout adapts automatically to the active source (`1,500 ms` hard stop on WebSocket; `4,500 ms` hard stop on background HTTP beacon). If packets cease beyond the deadline, the cone decelerates smoothly to `0 m/s`, sets LEDs to `"strobe"`, and emits `"meep"` (`STATE_AWAITING_INPUT`) via `VoiceEngine`.

---

### 2. Two-Stage Hybrid Sensor Fusion (Phone GNSS + Dual `VL53L5CX` Visual Lock)

Two independent civilian GNSS receivers (`PA1010D` on the cone and a smartphone in a pocket) each exhibit `1.5 m – 3.0 m` of horizontal multipath drift, which would cause pure GPS-following to weave erratically across a pavement. Fusing phone GNSS with the **`87°` dual-`VL53L5CX` horizon array (`IDEA-002`)** eliminates close-range jitter:

1. **Integer Micro-Degree Frame & Accuracy Gate:**
   * Reject any phone update with horizontal accuracy `acc_m > 8.0 m`. Convert `lat`/`lon` into integer micro-degrees (`int(round(deg * 10_000_000))`) before subtracting the cone's latched `home` datum so 32-bit MicroPython floats never introduce `1–2 m` quantisation error.
2. **Stage 1 — Long-Range GNSS Catch-Up (`distance > 3.2 m`):**
   * When you walk ahead or round a corner out of ToF range, the cone navigates toward a trailing "virtual rabbit" offset `standoff_m` (default `1.5 m`) behind your predicted trajectory, relying on the dual `VL53L5CX` array for reactive obstacle and dropped-kerb protection.
3. **Stage 2 — Close-Range ToF Silhouette Lock (`0.8 m <= distance <= 3.2 m`):**
   * Once within `3.2 m`, the `16`-column `VL53L5CX` horizon aggregator (`rows 0–5`) searches within `±25°` of the phone's GNSS bearing for the closest contiguous vertical target cluster (your legs/coat).
   * The controller locks onto that **ToF azimuth angle** and regulates forward speed to maintain an exact `1.5 m` stand-off distance (configurable `1.0 m – 3.0 m`)—or shifts laterally by `1.2 m` in **`"escort"` mode** so the holonomic cone glides neatly beside a pram along wide Northstowe greenways.

---

### 3. Optional Waypoint Logging ("Breadcrumb" Mode) $\rightarrow$ `arse-mission/v1`

Enabling **Breadcrumb Logging** during a Follow-Me walk lets you physically walk a route along pavements and dropped kerbs once and compile the recorded path into a reusable `arse-mission/v1` mission:

* **Log the Cone's Own Fused Path:** Records the **cone's own fused GNSS + odometry position** (`lat`/`lon` to 7 decimal places and local `north_m`/`east_m`), ensuring recorded waypoints stay centred on flat paving slabs even if you step onto a grass verge while walking.
* **Distance + Heading-Change Decimation:** Appends a raw breadcrumb only when:
  1. Euclidean distance from the last logged point reaches `>= 12.0 m` on a straight leg, **OR**
  2. Cumulative 9DoF heading changes by `>= 25°` after travelling at least `4.0 m` since the previous point (capturing every pavement bend and junction).
* **Hands-Free Crossing Stamps (Unlocked or Locked Phone):**
  * **Manual Stamp Endpoint (`POST /api/follow/stamp`):** Triggered from the Web UI radar or a lock-screen Quick Settings tile / iOS Shortcut to inject an immediate waypoint at the cone's current pose (`{"name": "Road Crossing", "dwell_s": 3.0, "sound": "horn"}`), acknowledged audibly with `"boop"`.
  * **Optional Auto-Crossing Dwell:** When enabled, pausing stationary at a kerb for `>= 4.0 s` automatically tags the current breadcrumb as a `dwell_s: 3.0` caution checkpoint.
* **Ramer-Douglas-Peucker Simplification & Chunking:** On stopping recording, the breadcrumb buffer is simplified to `<= 25` waypoints (enforcing `>= 3.5 m` minimum separation for `PA1010D` arrival circles)—or partitioned into chained chunks compatible with `IDEA-001`'s `MissionQueue`—appended with a `"meep_meep"` + `"rainbow"` arrival waypoint, and saved to `/flash/missions/`.

---

## Part 2: Coding AI Implementation Prompt (`agy` / VS Code)

Copy and paste the prompt below directly into your VS Code Gemini CLI (`agy`) session inside `C:\Users\Andrew\Cone`:

```markdown
You are implementing **Locked-Phone Follow-Me Mode and Optional Breadcrumb Waypoint Logging** in MicroPython (plus Web UI controls) for the **ARSE Mk.I Autonomous Traffic Cone** (Pimoroni Yukon RP2040/RP2350).

### Architecture & Integration Context
- **Existing Subsystems:** Integrates with `DualVL53L5CX` (`sensors/dual_tof.py`, `16`-column `87°` horizon clearance), `ReactiveAvoider` (`nav/obstacle_avoidance.py`), `MissionQueue` (`nav/mission_queue.py`, `arse-mission/v1` JSON), and `VoiceEngine` (`audio/voice_engine.py`, `arse-lang/v1` Conish cues `"boop"`, `"meep"`, `"uh_oh"`, `"meep_meep"`).
- **Locked-Phone Requirement:** Because mobile browsers suspend JavaScript/WebSockets when the screen is locked, the cone must accept location updates from **both** a `5 Hz` foreground WebSocket (`ws://<cone-ip>/ws/follow`) **and** a `1–2 Hz` background HTTP POST endpoint (`POST /api/follow/beacon`) compatible with native background location publishers such as **OwnTracks** HTTP mode (`{"_type": "location", "lat": ..., "lon": ..., "acc": ..., "vel": ..., "cog": ...}`).

### Tasks to Implement

#### 1. Source-Arbitrated Follow Controller & Hybrid ToF+GNSS Tracker (`nav/follow_controller.py`)
- Implement `FollowController`:
  - Accept phone fixes from either `"websocket"` or `"http_beacon"`, discarding any fix with `acc_m > 8.0`. Convert `lat`/`lon` to integer micro-degrees (`int(round(val * 10_000_000))`) before computing local tangent-plane `(north_m, east_m)` offsets relative to `home`.
  - Run an **alpha-beta kinematic predictor** (`vel_mps`, `cog_deg`) to extrapolate target position smoothly between `1–2 Hz` background HTTP updates.
  - Enforce an adaptive dead-man watchdog (`1500 ms` timeout when active source is `"websocket"`, `4500 ms` when `"http_beacon"`). On timeout, command `vx = 0, vy = 0`, set LEDs to `"strobe"`, and call `voice.say_event("STATE_AWAITING_INPUT")` (`"meep"`).
  - Support `"trail"` formation (offset `standoff_m` behind target heading, default `1.5 m`) and `"escort"` formation (offset `1.2 m` laterally left/right of target heading).
  - **Two-Stage Handover:**
    - When target range `> 3.2 m`, drive toward the predicted GNSS offset point through `ReactiveAvoider`.
    - When target range `<= 3.2 m`, query `DualVL53L5CX` columns within `±25°` of the phone bearing for the closest contiguous valid target cluster and regulate velocity to hold `standoff_m` directly from ToF depth (eliminating GNSS jitter). Play `"uh_oh"` (`WARN_OBSTACLE_SIDESTEP`) if ToF visual lock is lost around a corner.

#### 2. Breadcrumb Waypoint Logger & `arse-mission/v1` Compiler (`nav/breadcrumb_logger.py`)
- Implement `BreadcrumbLogger` recording the **cone's own fused pose** `(lat, lon, north_m, east_m)` while follow-me is active:
  - Decimate automatically: log a point when distance from previous breadcrumb `>= 12.0 m`, OR cumulative heading change `>= 25°` after `>= 4.0 m` travel.
  - Provide `stamp_waypoint(name, dwell_s=0.0, sound=None, lights=None)` for manual crossing/guard stamps (triggering `"boop"` via `VoiceEngine`), plus optional `auto_crossing_dwell` (`dwell_s: 3.0`, `sound: "horn"` when stationary for `>= 4.0 s`).
  - Provide `compile_mission(mission_name, drive_style="turn", max_waypoints=25)`: run **Ramer-Douglas-Peucker** polyline simplification, enforce `>= 3.5 m` minimum spacing between consecutive waypoints, append a final `"meep_meep"` + `"rainbow"` arrival waypoint, and return valid `arse-mission/v1` JSON (or chunked list if `> 25` waypoints) saved to `/flash/missions/`.

#### 3. WebSocket & HTTP Endpoints (`web/server.py`)
- Add `ws://<cone-ip>/ws/follow`: Bidirectional `5 Hz` JSON stream receiving phone GPS/velocity frames and broadcasting cone pose, `tof_locked` boolean, `active_source`, and `logged_count`.
- Add `POST /api/follow/beacon`: Token-authenticated (`?token=` or `X-Beacon-Token`) endpoint parsing both flat `{"lat", "lon", "acc_m", "vel_mps", "cog_deg"}` and **OwnTracks** `{"_type": "location", "lat", "lon", "acc", "vel", "cog"}` payloads so locked-phone tracking works natively.
- Add `POST /api/follow/config` (`{"enabled": bool, "formation": "trail"|"escort", "standoff_m": 1.5, "logging": bool, "auto_crossing_dwell": bool}`), `POST /api/follow/stamp`, and `POST /api/follow/save_mission`.

#### 4. Web UI Follow-Me & Breadcrumb Panel (`web/index.html` & `web/follow.js`)
- Add a **"Follow-Me & Route Recorder"** panel to the Web UI:
  - Toggle switches for **Start Follow-Me** (acquiring `navigator.wakeLock` and opening `/ws/follow`), **Formation (`Trail` / `Escort`)**, **Stand-Off Slider (`1.0 m – 3.0 m`)**, and **Record Breadcrumbs**.
  - Display a **"Locked-Phone Background Setup"** collapsible card showing the exact `POST /api/follow/beacon?token=...` URL and OwnTracks HTTP configuration instructions so the operator can lock their phone and pocket it.
  - Render the live breadcrumb trail on the Tactical GNSS Radar with one-tap **`+ Crossing Pause (3s + Horn)`**, **`+ Guard Point`**, and **`Finish & Save Mission`** buttons.

### Constraints & Verification
- Target MicroPython on Pimoroni Yukon (`uasyncio`, `ujson`, `math`, `utime`, `gc`); keep WebSocket and beacon handlers allocation-light and preserve all motor arming safety interlocks (never auto-arm motors when a beacon arrives).
- Add desktop Python 3 unit tests in `tests/test_follow_and_logger.py` verifying OwnTracks payload parsing, alpha-beta prediction, watchdog timeout fallback, ToF-to-GNSS handover thresholds, and Ramer-Douglas-Peucker `arse-mission/v1` compilation.
```

