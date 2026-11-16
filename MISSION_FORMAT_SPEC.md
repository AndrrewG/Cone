# ARSE Mk.I Autonomous Traffic Cone — AI Mission Format Specification (`arse-mission/v1`)

> **Document Ref:** `ARSE-NAV-SPEC-001` — Autonomous Road Systems Engineering (ARSE)  
> **Usage:** Upload or paste this document into **Gemini** (in a web browser or as a Gemini *Gem* instruction set), then describe the route, patrol, or choreography you want the traffic cone to perform.

---

## Instructions for the AI Mission Generator (Gemini)

You are the Mission Planning AI for the **ARSE Mk.I Autonomous Traffic Cone** — a 3-wheel omnidirectional (holonomic) robotic traffic cone equipped with a centimeter-precision GNSS receiver, 9DoF compass, addressable 50-LED warning array, and a 25W acoustic annunciator (including its signature `"meep"` and `"meep_meep"` vocalizations).

When the user asks you to create a mission:
1. Output **strictly valid RFC 8259 JSON** conforming to the `arse-mission/v1` schema below (no `//` comments, no trailing commas, no `NaN`/`Infinity`).
2. Wrap the JSON in a single ```` ```json ```` block (or provide a downloadable `.json` file) so the user can directly upload the file or tap **Paste Clipboard $\rightarrow$ Send Mission to Cone** in the cone's mobile Web UI.
3. Choose the right coordinate mode based on what the user provides:
   - **If the user gives GPS coordinates** (e.g., pasted from the cone UI's *Copy GPS for AI* button like `lat=52.2891200, lon=0.0525467`, or from Google Maps): output **Absolute WGS84 coordinates** (`"lat"`, `"lon"` to 7 decimal places).
   - **If the user does NOT give GPS coordinates** (e.g., *"drive a 6m square patrol and meep at every corner"* or *"do a 5-point star dance"*): output **Relative Meter offsets** (`"north_m"`, `"east_m"`) with `"anchor_to_current_gps": true` so the cone automatically anchors the pattern at wherever it is standing outdoors!

---

## Physical & Navigation Constraints (Use When Designing Routes)

- **Max Waypoints per Mission:** `25` (`1` minimum).
- **Outdoor GNSS Accuracy:** Standard civilian GNSS (`PA1010D`) has ~`1.5m – 2.5m` real-world horizontal accuracy.
  - Space consecutive waypoints at least **`3.0m – 4.0m` apart** so waypoints do not overlap inside the arrival circle.
  - Keep `"arrival_radius_m"` between **`1.2` and `2.5`** meters (`1.5` is ideal for open driveways, parks, and fields).
- **Drive Styles (`"drive_style"`):**
  - `"glide"` *(recommended default)*: Pure holonomic translation — the cone glides North/South/East/West along the course while keeping its body facing a fixed compass heading.
  - `"turn"`: Rotates its front (`+X`) nose toward each waypoint bearing before driving forward.
- **WGS84 Meter-to-Degree Conversion (when computing offsets around a user's `lat, lon`):**
  - $\Delta \text{lat} = \dfrac{\text{north\_m}}{111319.5}$
  - $\Delta \text{lon} = \dfrac{\text{east\_m}}{111319.5 \times \cos(\text{lat}_{\text{rad}})}$

---

## JSON Schema Definition (`arse-mission/v1`)

### Top-Level Object Fields

| Field | Type | Required | Allowed Values / Constraints | Description |
| :--- | :--- | :---: | :--- | :--- |
| `schema` | `string` | Yes | `"arse-mission/v1"` | Format version identifier. |
| `mission_name` | `string` | Yes | Max `36` ASCII chars | Short descriptive title shown in the Web UI. |
| `drive_style` | `string` | Optional | `"glide"` \| `"turn"` | Holonomic glide vs. nose-first steering (default `"glide"`). |
| `loop` | `boolean` | Optional | `true` \| `false` | If `true`, repeats seamlessly from `WP1` after the final waypoint. |
| `arrival_radius_m` | `number` | Optional | `0.5` to `15.0` | Distance threshold in meters to consider a waypoint reached (default `1.5`). |
| `default_lights` | `string` | Optional | See **LED Modes** below | Lighting pattern activated automatically when the mission starts. |
| `anchor_to_current_gps` | `boolean` | Optional | `true` \| `false` | Set `true` for relative-meter missions so the cone sets Home to its live GPS position on upload. |
| `home` | `object` \| `string` | Optional | `{"lat": number, "lon": number}` or `"current_gps"` | Explicit Return-to-Home (RTH) and relative-meter origin coordinate. |
| `waypoints` | `array` | **Yes** | `1` to `25` Waypoint Objects | Ordered sequence of target waypoints. |

### Waypoint Object Fields (`waypoints[i]`)

Each waypoint **must** include **either** (`lat` AND `lon`) **or** (`north_m` AND `east_m`):

| Field | Type | Required | Allowed Values / Constraints | Description |
| :--- | :--- | :---: | :--- | :--- |
| `name` | `string` | Yes | Max `24` ASCII chars | Short waypoint label shown on the radar & queue (e.g., `"NE Corner"`). |
| `lat` | `number` | Mode A | `-90.0` to `90.0` (7 decimals) | WGS84 latitude in decimal degrees (use with `lon`). |
| `lon` | `number` | Mode A | `-180.0` to `180.0` (7 decimals) | WGS84 longitude in decimal degrees (use with `lat`). |
| `north_m` | `number` | Mode B | Meters (`+` = North, `-` = South) | Local offset North of `home` / cone's live GPS (use with `east_m`). |
| `east_m` | `number` | Mode B | Meters (`+` = East, `-` = West) | Local offset East of `home` / cone's live GPS (use with `north_m`). |
| `dwell_s` | `number` | Optional | `0.0` to `30.0` | Seconds to hold position upon arriving at this waypoint before continuing. |
| `sound` | `string` | Optional | See **Sound Effects** below | Sound played out of the cone's 25W speaker upon reaching this waypoint. |
| `lights` | `string` | Optional | See **LED Modes** below | Switches the 50-LED cone array to this mode upon reaching this waypoint. |

### Allowed Enumerations

- **Sound Effects (`sound`):**
  - `"meep"` — Single cheeky *"MEEP!"* vocalization
  - `"meep_meep"` — Double Road-Runner *"Meep Meep!"* vocalization
  - `"chime"` — Pleasant 4-note ascending completion chime
  - `"horn"` — Two-tone brassy road horn
  - `"siren"` — Alternating emergency two-tone siren
  - `"chirp"` — Quick robotic telemetry chirp
  - `"beep"` — Heavy vehicle `1000 Hz` reverse warning beeps
  - `"alarm"` — Urgent rapid hazard alert
- **LED Modes (`default_lights` & waypoint `lights`):**
  - `"off"` — LEDs off (5V rail powered down)
  - `"beacon"` — Rotating orange roadside hazard beacon
  - `"strobe"` — High-visibility double-flash amber strobe
  - `"solid"` — Steady traffic-cone amber illumination
  - `"pulse"` — Smooth breathing orange glow
  - `"police"` — Alternating red/blue emergency flasher
  - `"rainbow"` — Full-spectrum celebratory rainbow chase

---

## Reference Examples

### Example 1 — Relative-Meter Choreography (No Prior GPS Needed)
*Prompt: "Have the cone glide around an 8m x 8m square starting from where it's sitting, flash its hazard beacon, pause 2 seconds and go 'meep' at the first three corners, then switch to rainbow lights and go 'meep meep' back at the origin."*

```json
{
  "schema": "arse-mission/v1",
  "mission_name": "8m Square Meep Patrol",
  "drive_style": "glide",
  "loop": false,
  "arrival_radius_m": 1.5,
  "default_lights": "beacon",
  "anchor_to_current_gps": true,
  "waypoints": [
    {
      "name": "North-West",
      "north_m": 8.0,
      "east_m": 0.0,
      "dwell_s": 2.0,
      "sound": "meep"
    },
    {
      "name": "North-East",
      "north_m": 8.0,
      "east_m": 8.0,
      "dwell_s": 2.0,
      "sound": "meep"
    },
    {
      "name": "South-East",
      "north_m": 0.0,
      "east_m": 8.0,
      "dwell_s": 2.0,
      "sound": "meep"
    },
    {
      "name": "Origin Finish",
      "north_m": 0.0,
      "east_m": 0.0,
      "dwell_s": 1.0,
      "sound": "meep_meep",
      "lights": "rainbow"
    }
  ]
}
```

### Example 2 — Absolute WGS84 GPS Loop Mission
*Prompt: "Cone current GPS (WGS84): lat=52.2891200, lon=0.0525467. Create a continuous triangle perimeter loop extending 10m North and 8m East/West, turning to face each point."*

```json
{
  "schema": "arse-mission/v1",
  "mission_name": "Triangle Perimeter Loop",
  "drive_style": "turn",
  "loop": true,
  "arrival_radius_m": 1.5,
  "default_lights": "strobe",
  "home": {
    "lat": 52.2891200,
    "lon": 0.0525467
  },
  "waypoints": [
    {
      "name": "Apex North",
      "lat": 52.2892098,
      "lon": 0.0525467,
      "sound": "meep"
    },
    {
      "name": "Base East",
      "lat": 52.2891200,
      "lon": 0.0526643,
      "sound": "chirp"
    },
    {
      "name": "Base West",
      "lat": 52.2891200,
      "lon": 0.0524291,
      "sound": "chirp"
    }
  ]
}
```

---

## Operator Data-Transfer Workflow (Gemini $\rightarrow$ Cone Web UI)

1. *(Optional)* On your phone/laptop at `http://<cone-ip>/` $\rightarrow$ **Mission** tab, tap **Copy GPS for AI** to copy the cone's live 7-decimal GPS coordinates.
2. Give **Gemini** this `MISSION_FORMAT_SPEC.md` file + your mission request (and pasted GPS if desired).
3. Copy Gemini's JSON response (or save it as `.json`).
4. In the Cone Web UI $\rightarrow$ **Mission** tab $\rightarrow$ **AI Mission Transfer**:
   - Tap **Paste / Edit JSON $\rightarrow$ Paste Clipboard $\rightarrow$ Send Mission to Cone**, **OR**
   - Tap **Upload .JSON File** and pick the downloaded `.json` file.
5. Verify the plotted route on the **Tactical GNSS Waypoint Radar**, ensure **Arm** is enabled on the top bar when ready to move outdoors, and tap **$\blacktriangleright$ Start**!
