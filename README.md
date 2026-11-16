# ARSE Mk.I Autonomous Traffic Cone

**Autonomous Road Systems Engineering (ARSE)**  
*Division of Conical Mobility, Tactical Hazard Delineation & Driveway Robotics*

An autonomous, 3-wheel holonomic robotic traffic cone engineered on the **Pimoroni Yukon (RP2040)** platform with custom **ARSE** MicroPython flight-control, navigation, and telemetry firmware.

---

## 1. Hardware Architecture & Yukon Slot Allocation

Engineered around BOM Specification `#PI796120`:

| Subsystem Location | Hardware Module | Operational Role |
| :--- | :--- | :--- |
| **Slot 1** | **Dual Motor Module** | Drives Omni-Wheels 1 & 2 (`DRV8424P`, `JGA25-370DG` 12V 427 RPM) |
| **Slot 2** | **Dual Motor Module** | Drives Omni-Wheel 3 (`DRV8424P`) + 1 auxiliary actuator channel |
| **Slot 3** | *Unpopulated* | Reserved for ARSE Phase-II Optical LiDAR / Proximity Package |
| **Slot 4** | **LED Strip Module** | High-visibility addressable RGB illumination (`WS2812`, 5V rail gated) |
| **Slot 5** | **RM2 Wireless Module** | 2.4 GHz Wi-Fi & BLE Command/Telemetry Link (`CYW43439`) |
| **Slot 6** | **Audio Amp Module** | 25W Mono Acoustic Annunciator (`TAS2780`) driving 3" 4Ω 5W speaker |
| **I2C Bus (`0x68`/`0x0C`)** | **ICM-20948 + AK09916** | 9DoF Inertial & Magnetometer AHRS (`0–360°` heading + tilt cutoff) |
| **I2C Bus (`0x10`)** | **PA1010D** | Multi-constellation GNSS Receiver (`1e-7°` / `1.1 cm` integer parser) |
| **Primary Bus (`XT30`)** | **3S 2200mAh LiPo** | Main traction & logic supply (`11.1V` nominal, 10 Hz Coulomb-monitored) |

---

## 2. Firmware & Repository Layout

```
Cone/
├── boot.py                     # Minimal boot initializer for maximum heap availability
├── config.py                   # ARSE hardware pinouts, safety limits & kinematic geometry
├── main.py                     # 20 Hz unified executive: drive, AHRS, BMS, nav & web UI
├── telemetry_server.py         # Async HTTP/1.1 & REST control/telemetry server
├── web/
│   └── index.html              # 4-tab ARSE Mobile Operator Terminal (Drive, Mission, Battery, Sensors)
├── drivers/
│   ├── battery.py              # Calibrated 3S LiPo Coulomb counter with atomic flash persistence
│   ├── drive_controller.py     # Holonomic drive controller, slew limiter, arming & deadman watchdog
│   ├── navigator.py            # Centimeter-accurate GNSS waypoint planner & arse-mission/v1 importer
│   ├── sentry.py               # Low-power IMU anti-theft Sentry Mode & Police strobe/siren deterrence
│   ├── icm20948.py             # 9DoF ICM-20948 + AK09916 compass driver, spin cal & AHRS fusion
│   ├── pa1010d.py              # Non-blocking PA1010D NMEA integer-coordinate GNSS parser
│   ├── omni_kinematics.py      # 3-wheel equilateral holonomic inverse kinematics solver
│   ├── lights.py               # Non-blocking 20 Hz conical LED array controller
│   ├── audio_sfx.py            # Spam-proof DMA I2S formant WAV ("MEEP!") & log-volume synthesizer
│   └── wifi_manager.py         # Known-network STA auto-connect with 'TrafficCone' AP fallback
├── meep.wav                    # 44.1 kHz 16-bit formant-synthesized "MEEP!" vocalization
├── MISSION_FORMAT_SPEC.md      # AI mission format specification (arse-mission/v1) for Gemini
├── hardware_test.py            # Full-system bench qualification & slot verification suite
├── motor_test.py               # Slew-ramped traction motor & current calibration bench script
├── TASKS.md                    # ARSE Engineering Roadmap & Task Register (ARSE-ENG-TRK-001)
└── tools/
    ├── generate_sfx.ps1        # Formant & harmonic acoustic synthesizer for meep.wav
    ├── sync_wifi.ps1           # Wireless HTTP OTA firmware & UI updater (zero USB needed)
    ├── sync_all.ps1            # Automated Raw-REPL flash synchronizer & soft-reboot launcher
    └── exec_repl.ps1           # USB Serial Raw-REPL diagnostic command utility
```

---

## 3. Field Deployment & Mobile Operator Terminal

### Flash Synchronization (Wireless OTA or USB-C)
```powershell
# Wireless HTTP OTA over Wi-Fi:
powershell -NoProfile -ExecutionPolicy Bypass -File .\tools\sync_wifi.ps1 -HostIp 192.168.50.13 -Reboot

# Or USB-C Raw REPL:
powershell -NoProfile -ExecutionPolicy Bypass -File .\tools\sync_all.ps1 -Port COM3 -StartMain
```

### Operator Terminal Tabs (`http://<cone-ip>/` or `http://192.168.4.1/`)
* **Drive:** Virtual holonomic 2D thumbstick + spin buttons, **Anti-Theft IMU Sentry Mode** (`0 mA` low-power watch + Police strobe/siren deterrence), bench dry-run arming toggle, speed caps (`Indoor 35%` / `Normal 65%` / `Turbo 100%`), live 3-wheel kinematic bars, LED mode/brightness controls, and Slot 6 formant `"MEEP!"` soundboard with perceptual log volume.
* **Mission:** Client-side high-zoom **Satellite Aerial Map** (Zoom 22) + offline **Tactical GNSS Radar**, **AI Mission Transfer** (`arse-mission/v1` JSON upload/paste/export), `Holonomic Glide` vs. `Turn & Drive` guidance, `Loop` patrol toggle, and `Return-to-Home (RTH)`.
* **Battery:** Real-time 10 Hz Coulomb SoC %, remaining/discharged mAh, 2-step **"New Full Battery Inserted (100%)"** reset, and configurable pack capacity.
* **Sensors:** Live `0–360°` tilt-compensated gyro/compass heading, interactive 360° spin calibration, tip-over safety monitor, and GNSS / Yukon diagnostics.

---

*Copyright © 2026 Autonomous Road Systems Engineering (ARSE). Unauthorised cone displacement is strictly prohibited.*
