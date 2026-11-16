# ARSE Mk.I — Idea Spec & Coding Prompt: Declarative Acoustic Language Engine (`IDEA-003`)

* **Project:** ARSE Mk.I Autonomous Traffic Cone (`C:\Users\Andrew\Cone`)
* **Hardware & Runtime:** Pimoroni Yukon (RP2040 / RP2350) running MicroPython, Pimoroni Yukon `AudioAmpModule` (MAX98357A mono Class-D I2S amplifier), 50-LED addressable warning array, `PA1010D` GNSS receiver, Dual `VL53L5CX` 8×8 ToF array, 3-wheel holonomic drive base
* **Language Schema:** `arse-lang/v1` (`/flash/conish_lexicon.json`) + HTTP API (`GET /api/lexicon`, `POST /api/lexicon`, `POST /api/say`)
* **Target Folder:** `arse / cone / ideas`

---

## Part 1: System Architecture & Engineering Design

### 1. Semantic Decoupling via `arse-lang/v1`

Hardcoding raw audio triggers (`"meep"`, `"horn"`, `"chirp"`) across navigation, sensor, and web modules tightly couples firmware state machines to acoustic synthesis details. Introducing a declarative **Language Definition File** (`/flash/conish_lexicon.json`) separates **communicative intent** from **audio-visual rendering**:

1. **Semantic Intent Tokens:** Firmware subsystems emit abstract state events—such as `STATE_AWAITING_INPUT`, `ACK_MISSION_START`, `DATUM_LOCKED`, `WARN_OBSTACLE_SIDESTEP`, or `ERR_OBSTACLE_BLOCKED`—rather than frequencies or file paths.
2. **Configurable Lexicon Mapping:** Each entry in `conish_lexicon.json` maps a canonical "Conish" word (`"meep"`, `"boop"`, `"ech_ung"`, `"meep_meep"`, `"uh_oh"`, `"brrr_up"`) to one or more semantic trigger tokens, an arbitration priority (`1..4`), an anti-spam cooldown timer (`cooldown_ms`), a transient 50-LED visual overlay, and a phonetic synthesis recipe.
3. **Backwards Compatibility with `arse-mission/v1`:** Waypoint `"sound"` fields inside `arse-mission/v1` JSON missions continue to work unchanged by resolving directly against vocabulary keys in `conish_lexicon.json` (plus any custom words you add via the Web UI).

---

### 2. Pimoroni Yukon `AudioAmpModule` I2S DMA Pipeline

The Pimoroni Yukon `AudioAmpModule` routes `DATA`, `BCLK`, and `LRCLK` through a Yukon slot's high-speed `FAST1..FAST3` GPIOs to an onboard MAX98357A Class-D I2S amplifier, while controlling the amplifier shutdown/enable line via the slot's IO expander (`module.enable()` / `module.disable()`).

#### Zero-Allocation Cooperative `uasyncio` Streaming
To ensure expressive multi-syllable speech never blocks the `15 Hz` dual `VL53L5CX` collision avoider or introduces PWM/PID motor jitter:

* **Audio Format:** Configure `machine.I2S` (via `AudioAmpModule`) at **`16,000 Hz` sample rate, `16-bit` signed little-endian (`I2S.MONO`)**, with an internal DMA buffer (`ibuf`) of `4,096 bytes` (`128 ms` of audio headroom).
* **Asynchronous DMA Writes:** Wrap the `I2S` peripheral in `uasyncio.StreamWriter(audio_amp.i2s)`. A dedicated `voice_task()` coroutine fills a single pre-allocated `1,024`-byte (`512`-sample / `32 ms`) `bytearray` buffer and awaits `swriter.drain()`, yielding cooperatively to the scheduler while the RP2040/RP2350 DMA engine clocks samples out to the amplifier.
* **Dual Synthesis Backends:**
  1. **Parametric Wavetable Synthesis (`"syllables"`):** Pre-allocate one `512`-entry `int16` (`1,024`-byte) lookup table per waveform (`"sine"`, `"triangle"`, `"sawtooth"`, `"square"`). For each syllable (`start_hz`, `end_hz`, `duration_ms`, `attack_ms`, `decay_ms`, `gain`, `gap_after_ms`), step a 32-bit fixed-point phase accumulator across `32 ms` DMA frames with linear/exponential pitch interpolation and envelope shaping—requiring **zero heap allocation** during playback.
  2. **Flash PCM `.wav` Clip Streaming (`"wav_file"`):** If an entry specifies an optional `16 kHz` 16-bit mono `.wav` file on `/flash/audio/` (such as `/flash/audio/ech_ung.wav`), validate and skip the 44-byte RIFF header and stream `1,024`-byte sectors into the same DMA buffer. If the file is missing or unreadable, fall back seamlessly to the entry's `"syllables"` recipe.
* **Idle Mute & Pop Suppression:** Keep the MAX98357A disabled (`amp.disable()`) at idle to eliminate Class-D quiescent hiss and save battery. When an utterance is dequeued, assert `amp.enable()`, stream `10 ms` (`160` samples) of zero-valued silence to settle the Class-D output stage without a click/pop transient, play the utterance, and disable the amplifier after `250 ms` of queue inactivity.

---

### 3. Priority Arbitration, Cooldowns & Multimodal LED Sync

* **4-Slot Ring Buffer & Preemption:** Maintain a fixed 4-slot circular queue of pending utterances with four priority levels (`1` = Ambient/Telemetry, `2` = Interactive/Ack, `3` = Caution/Detour, `4` = Critical Error/Halt). If a `priority: 4` fault (`"ech_ung"`) arrives while a `priority: 2` `"meep"` is playing, immediately abort the current syllable, flush the DMA frame, and speak `"ech_ung"`.
* **Per-Word Cooldown Guard:** Track `last_spoken_ticks_ms` per vocabulary entry so oscillatory conditions (such as hovering near the `1.80 m` ToF caution boundary) cannot trigger repetitive audio spam.
* **Synchronised 50-LED Expression Overlay:** When an utterance begins, temporarily override the 50-LED cone array with the word's `led_overlay` (`"pulse"`, `"solid"`, `"strobe"`, `"police"`, `"rainbow"`) for `duration_ms`, automatically restoring the active mission's `default_lights` mode once complete.

---

### 4. Starter Lexicon Definition (`arse-lang/v1`)

```json
{
  "schema": "arse-lang/v1",
  "language_name": "Conish Mk.I",
  "default_volume": 0.8,
  "vocabulary": {
    "meep": {
      "meaning": "Awaiting user input, arming confirmation, or operator prompt",
      "triggers": ["STATE_AWAITING_INPUT", "PROMPT_OPERATOR", "DWELL_HOLD"],
      "priority": 2,
      "cooldown_ms": 4000,
      "led_overlay": {
        "mode": "pulse",
        "duration_ms": 600
      },
      "syllables": [
        {
          "waveform": "triangle",
          "start_hz": 540,
          "end_hz": 1020,
          "duration_ms": 140,
          "attack_ms": 15,
          "decay_ms": 35,
          "gain": 0.85,
          "gap_after_ms": 0
        }
      ]
    },
    "boop": {
      "meaning": "Positive acknowledgement, mission accepted, or home datum locked",
      "triggers": ["ACK_MISSION_START", "ACK_COMMAND", "DATUM_LOCKED"],
      "priority": 2,
      "cooldown_ms": 500,
      "led_overlay": {
        "mode": "solid",
        "duration_ms": 350
      },
      "syllables": [
        {
          "waveform": "sine",
          "start_hz": 480,
          "end_hz": 390,
          "duration_ms": 110,
          "attack_ms": 10,
          "decay_ms": 40,
          "gain": 0.9,
          "gap_after_ms": 0
        }
      ]
    },
    "ech_ung": {
      "meaning": "Error encountered, invalid mission JSON, sensor fault, or blocked path",
      "triggers": ["ERR_SYSTEM_FAULT", "ERR_INVALID_MISSION", "ERR_OBSTACLE_BLOCKED"],
      "priority": 4,
      "cooldown_ms": 2500,
      "wav_file": "/flash/audio/ech_ung.wav",
      "led_overlay": {
        "mode": "strobe",
        "duration_ms": 900
      },
      "syllables": [
        {
          "waveform": "sawtooth",
          "start_hz": 310,
          "end_hz": 205,
          "duration_ms": 160,
          "attack_ms": 10,
          "decay_ms": 30,
          "gain": 0.95,
          "gap_after_ms": 65
        },
        {
          "waveform": "sawtooth",
          "start_hz": 230,
          "end_hz": 135,
          "duration_ms": 240,
          "attack_ms": 15,
          "decay_ms": 70,
          "gain": 1.0,
          "gap_after_ms": 0
        }
      ]
    },
    "uh_oh": {
      "meaning": "Caution: obstacle or kerb detected, initiating holonomic sidestep",
      "triggers": ["WARN_OBSTACLE_SIDESTEP", "WARN_KERB_DETECTED"],
      "priority": 3,
      "cooldown_ms": 3000,
      "led_overlay": {
        "mode": "pulse",
        "duration_ms": 500
      },
      "syllables": [
        {
          "waveform": "triangle",
          "start_hz": 450,
          "end_hz": 370,
          "duration_ms": 130,
          "attack_ms": 12,
          "decay_ms": 35,
          "gain": 0.85,
          "gap_after_ms": 0
        }
      ]
    },
    "brrr_up": {
      "meaning": "Drive motors armed and ready for autonomous motion",
      "triggers": ["STATE_ARMED"],
      "priority": 3,
      "cooldown_ms": 1000,
      "led_overlay": {
        "mode": "strobe",
        "duration_ms": 500
      },
      "syllables": [
        {
          "waveform": "sine",
          "start_hz": 330,
          "end_hz": 330,
          "duration_ms": 70,
          "attack_ms": 8,
          "decay_ms": 15,
          "gain": 0.8,
          "gap_after_ms": 20
        },
        {
          "waveform": "sine",
          "start_hz": 440,
          "end_hz": 440,
          "duration_ms": 70,
          "attack_ms": 8,
          "decay_ms": 15,
          "gain": 0.85,
          "gap_after_ms": 20
        },
        {
          "waveform": "sine",
          "start_hz": 660,
          "end_hz": 660,
          "duration_ms": 110,
          "attack_ms": 8,
          "decay_ms": 30,
          "gain": 0.9,
          "gap_after_ms": 0
        }
      ]
    },
    "meep_meep": {
      "meaning": "Mission completed or arrived home safely",
      "triggers": ["EVENT_MISSION_COMPLETE", "EVENT_ARRIVED_HOME"],
      "priority": 2,
      "cooldown_ms": 1500,
      "led_overlay": {
        "mode": "rainbow",
        "duration_ms": 1200
      },
      "syllables": [
        {
          "waveform": "triangle",
          "start_hz": 580,
          "end_hz": 1080,
          "duration_ms": 110,
          "attack_ms": 10,
          "decay_ms": 25,
          "gain": 0.9,
          "gap_after_ms": 45
        },
        {
          "waveform": "triangle",
          "start_hz": 620,
          "end_hz": 1160,
          "duration_ms": 130,
          "attack_ms": 10,
          "decay_ms": 35,
          "gain": 0.95,
          "gap_after_ms": 0
        }
      ]
    }
  }
}
```

---

## Part 2: Coding AI Implementation Prompt (`agy` / VS Code)

Copy and paste the prompt below directly into your VS Code Gemini CLI (`agy`) session inside `C:\Users\Andrew\Cone`:

```markdown
You are implementing the declarative **Conish Acoustic Language Engine (`arse-lang/v1`)** in MicroPython (plus a browser Soundboard & Lexicon Editor) for the **ARSE Mk.I Autonomous Traffic Cone** using the **Pimoroni Yukon `AudioAmpModule`**.

### Hardware & Architecture Context
- **Audio Hardware:** Pimoroni Yukon `AudioAmpModule` (MAX98357A mono Class-D I2S amplifier) installed in a configurable Yukon slot (`AUDIO_AMP_SLOT`). Drive audio asynchronously via `machine.I2S` (`16,000 Hz`, `16-bit` signed little-endian mono, `ibuf=4096`) wrapped in `uasyncio.StreamWriter` so audio playback never blocks the `15 Hz` dual `VL53L5CX` collision avoider or holonomic motor PID loop.
- **Language Definition File:** `/flash/conish_lexicon.json` (`schema: "arse-lang/v1"`) maps Conish words (`"meep"`, `"boop"`, `"ech_ung"`, `"uh_oh"`, `"brrr_up"`, `"meep_meep"`) to semantic trigger tokens (`STATE_AWAITING_INPUT`, `ACK_MISSION_START`, `ERR_SYSTEM_FAULT`, etc.), priority levels (`1..4`), cooldown timers (`cooldown_ms`), optional `led_overlay` patterns, optional `16 kHz` mono `.wav` clips (`/flash/audio/*.wav`), and parametric `syllables` definitions.

### Tasks to Implement

#### 1. Zero-Allocation I2S Parametric & WAV Voice Engine (`audio/voice_engine.py`)
- Implement `VoiceEngine(yukon, audio_amp_module, led_controller, lexicon_path="/flash/conish_lexicon.json")`:
  - Pre-allocate `512`-sample (`1,024`-byte) `int16` lookup tables for `"sine"`, `"triangle"`, `"sawtooth"`, and `"square"` waveforms, plus a single `1,024`-byte DMA output `bytearray` (`32 ms` per chunk at `16 kHz`) at initialisation so zero heap allocations occur during audio playback.
  - Maintain a fixed 4-slot circular speech queue with priority arbitration (`1..4`) and per-word `cooldown_ms` tracking (`utime.ticks_ms()` / `ticks_diff()`).
  - If an incoming utterance has `priority > current_utterance.priority` (for example, `priority: 4` `"ech_ung"` interrupting `priority: 2` `"meep"`), immediately abort the active syllable and play the higher-priority entry.
  - In the `uasyncio` `run()` loop:
    - Keep `audio_amp_module.disable()` asserted when idle; enable the amplifier and write `10 ms` of zero-amplitude silence before the first audio frame to prevent pop/click transients, then disable the amplifier `250 ms` after the queue drains.
    - If a lexicon entry defines `wav_file` and the file exists on `/flash`, validate its 44-byte RIFF/WAVE header (`16 kHz`, 16-bit, mono PCM) and stream `1,024`-byte chunks via `await swriter.drain()`; otherwise synthesise the entry's `syllables` array (`start_hz` $\rightarrow$ `end_hz` linear pitch sweep, `attack_ms`, `decay_ms`, `gain`, `gap_after_ms`) scaled by `default_volume`.
    - Trigger `led_controller.set_temporary_overlay(mode, duration_ms)` when `led_overlay` is present on the spoken word.
  - Expose `say_word(word, override_priority=None)` and `say_event(event_token)` (resolving `event_token` via the lexicon's `triggers` lists), plus `reload_lexicon()`.

#### 2. Default Lexicon Bootstrap & Firmware Event Hooks (`flash/conish_lexicon.json` & integration)
- Seed `/flash/conish_lexicon.json` with the `arse-lang/v1` starter vocabulary (`"meep"` for `STATE_AWAITING_INPUT`/`DWELL_HOLD`, `"boop"` for `ACK_MISSION_START`/`DATUM_LOCKED`, `"ech_ung"` for `ERR_SYSTEM_FAULT`/`ERR_INVALID_MISSION`/`ERR_OBSTACLE_BLOCKED`, `"uh_oh"` for `WARN_OBSTACLE_SIDESTEP`, `"brrr_up"` for `STATE_ARMED`, and `"meep_meep"` for `EVENT_MISSION_COMPLETE`).
- Wire `voice.say_event(...)` calls into boot completion, motor arming, mission upload validation (success $\rightarrow$ `"boop"`, schema/geofence rejection $\rightarrow$ `"ech_ung"`), waypoint `"sound"` execution (`voice.say_word(wp["sound"])`), and dual-ToF obstacle alerts.

#### 3. HTTP Endpoints & Web UI Soundboard / Lexicon Editor (`web/server.py`, `web/index.html`, `web/lexicon.js`)
- Add endpoints to `web/server.py`:
  - `GET /api/lexicon`: Return the active `/flash/conish_lexicon.json` document.
  - `POST /api/lexicon`: Validate `arse-lang/v1` structure, write atomically to `/flash/conish_lexicon.json`, call `voice.reload_lexicon()`, and respond with `"boop"` (or `"ech_ung"` on validation error).
  - `POST /api/say`: Accept `{"word": "ech_ung"}` or `{"event": "STATE_AWAITING_INPUT"}` to audition utterances immediately over the Yukon Audio Amp.
- Add a **"Conish Voice & Lexicon"** panel to the Web UI:
  - Render one-tap soundboard buttons (`"meep"`, `"boop"`, `"ech_ung"`, etc.) displaying each word's semantic meaning and trigger tags.
  - Provide an inline editor allowing the operator to adjust `default_volume`, edit syllable frequencies/durations/waveforms, add new Conish words, test them live via `POST /api/say`, and save changes to flash via `POST /api/lexicon`.

### Constraints & Verification
- Target MicroPython on Pimoroni Yukon (`machine.I2S`, `uasyncio`, `ujson`, `math`, `array`, `utime`, `gc`); avoid CPython-only libraries and prevent dynamic memory allocation inside the `32 ms` DMA synthesis loop.
- Add desktop Python 3 unit tests in `tests/test_voice_engine.py` verifying event-to-word resolution, priority preemption, cooldown suppression, WAV header validation/fallback, and `arse-lang/v1` schema validation.
```

