# ARSE Mk.I — Engineering Known Issues & Investigation Log

> **Document Ref:** `ARSE-ENG-ISS-001`  
> **Platform:** Pimoroni Yukon (`RP2040` / `RP2350`) • MicroPython Runtime  
> **Hardware Baseline:** [HARDWARE.md](HARDWARE.md)

---

## Open Issues

### `ISSUE-001` — Audible Speaker Pop When Slot 6 `TAS2780` Audio Amp Powers Off
* **Status:** Open (Low Severity — Deferred Investigation)
* **Reported:** 2026-11-16 (Bench Verification)
* **Subsystem:** Slot 6 `AudioAmpModule` (`TI TAS2780` `25 W` Class-D Mono I2S Amplifier + `3"` `4 Ω` `5 W` speaker)
* **Relevant Files:** [drivers/audio_sfx.py](drivers/audio_sfx.py), [HARDWARE.md](HARDWARE.md) §2.6
* **Symptom:** When the audio amplifier turns off (after the `8000 ms` idle timeout or on executive shutdown/reset), the speaker still emits an audible pop/click despite the current 3-stage register sequence (`DVC = 0xCF` + `MODE_CTRL = 0x81` $\rightarrow$ `MODE_CTRL = 0x02` $\rightarrow$ `amp.disable()` / `SDZ` low).
* **Hypotheses to Investigate Later (e.g. alongside Priority 3 `VoiceEngine`):**
  1. **Simultaneous `DVC` Mute & `MODE_CTRL` Shutdown:** Writing `MODE_CTRL = 0x81` (`SW_RESET` / software shutdown) in the exact same tick as `DVC = 0xCF` may bypass the `TAS2780` internal zero-crossing digital volume ramp, snapping the Class-D PWM bridge off mid-cycle before `DVC` finishes ramping down.
  2. **`SDZ` (`GPIO23`) Hardware Pin Pull-Down Transient:** Pulling `SDZ` (`SLOT6_FAST4`) low via `amp.disable()` collapses the analogue output bias/common-mode voltage across the speaker load; holding `SDZ` high while parked in `TAS2780` software shutdown (`MODE_CTRL = 0x02`, microamp quiescent draw) whenever `MAIN_EN` is active may eliminate the DC common-mode step completely.
  3. **Synchronous Shutdown Path (`_shutdown_hardware`):** On `Ctrl-C` / USB hot-sync / Sentry `WATCH` entry, `_shutdown_hardware()` executes synchronously without waiting for an I2S silence frame + `DVC` ramp window before de-asserting `SDZ`.
