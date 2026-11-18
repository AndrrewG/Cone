# HARDWARE.md — ARSE Mk.I Autonomous Traffic Cone Physical Architecture

> **Document Ref:** `ARSE-HW-SPEC-001`  
> **Repository:** `C:\Users\Andrew\Cone`  
> **Role:** Canonical source of truth for body-frame coordinate conventions, Pimoroni Yukon slot allocations, RP2040 / `TCA9555` GPIO mappings, bus addresses, chassis geometry, and `[INSTALLED]` vs `[PLANNED]` peripheral status.

---

## Section 1: Body-Frame Coordinate Convention

### 1.1 Right-Handed Robot Body Frame (`+X` Forward, `+Y` Left, `+Z` Up)

All motion control, inverse kinematics, sensor fusion, and reactive safety filters on the **ARSE Mk.I** use a standard right-handed Cartesian body frame anchored at the geometric centre of the 3-wheel base:

| Axis / Rotation | Direction | Positive Sign Convention | Canonical SI Variable & Units |
| :--- | :--- | :--- | :--- |
| **`+X` (Surge)** | Forward through the cone's front nose (`0 deg` body azimuth) | Forward linear motion (`+vx_mps`) | `x_m`, `vx_mps`, `accel_x_mps2` |
| **`+Y` (Sway)** | Left flank (`+90 deg` counter-clockwise from `+X`) | Leftward holonomic strafe (`+vy_mps`) | `y_m`, `vy_mps`, `accel_y_mps2` |
| **`+Z` (Heave / Yaw)** | Vertically upward through the cone apex | Counter-clockwise (`CCW`) yaw when viewed from above (`+omega_rad_s` / `+omega_dps`) | `z_m`, `omega_dps`, `omega_rad_s`, `alpha_dps2` |

* **Operator UI Joystick Mapping (`web/index.html`):** Screen coordinates (`joyX` right, `joyY` down) map onto body twist via `vx = -joyY`, `vy = -joyX`, and `+omega` for counter-clockwise spin.
* **World Navigation Frame vs Body Yaw (`drivers/navigator.py`):**
  * World GNSS offsets (`north_m`, `east_m`) and compass bearings (`heading_deg`, `bearing_deg`) follow standard marine/aviation convention: **`0.0 deg` = North, `90.0 deg` = East, increasing clockwise (`CW`) in `[0.0, 360.0) deg`**.
  * Body angular velocity `+omega` is **counter-clockwise (`CCW`)**, so a clockwise bearing error (`yaw_err_cw_deg > 0`) produces a negative yaw command (`omega = -kp_yaw * yaw_err_cw_deg`), and relative clockwise bearing `rel_cw_rad = radians(bearing_deg - heading_deg)` decomposes into `vx = spd * cos(rel_cw_rad)` and `vy = -spd * sin(rel_cw_rad)`.

### 1.2 9DoF IMU (`ICM-20948` + `AK09916`) Axis Mapping onto Body Frame

The `ICM-20948` 9DoF breakout (`drivers/icm20948.py`) combines a 6-axis MEMS accelerometer/gyroscope die (`0x68`) with an internal Asahi Kasei `AK09916` 3-axis Hall magnetometer die (`0x0C`) in a single package whose internal magnetometer axes are inverted on `Y` and `Z` relative to the accelerometer/gyroscope die:

1. **Accelerometer & Gyroscope Die (`ICM-20948` at `0x68`):**
   * Read directly as `(ax_g, ay_g, az_g)` (`±2 g`, `16384 LSB/g`) and `(gx_dps, gy_dps, gz_dps)` (`±250 dps`, `131.0 LSB/dps`).
   * **Level Resting Vector:** `az_g ≈ +1.0 g` (`+9.81 m/s²` upward normal reaction along `+Z`).
   * **Attitude Angles (`drivers/icm20948.py`):**
     * `pitch_deg = degrees(atan2(-ax_g, sqrt(ay_g^2 + az_g^2)))` (1st-order IIR low-pass filtered: `0.75 * prev + 0.25 * raw`).
     * `roll_deg = degrees(atan2(ay_g, az_g))` (1st-order IIR low-pass filtered).
     * **Tip-Over / Snatch Cutoff:** Triggers `tilt_fault = True` after `4` consecutive `100 ms` (`10 Hz`) samples (`400 ms` debounce) where `|pitch_deg| > 40.0 deg`, `|roll_deg| > 40.0 deg`, or `az_g < 0.25 g`.
   * **Gyroscope Yaw Integration:** `+gz_dps` measures **counter-clockwise (`+Z`)** yaw rate, advancing clockwise compass heading via `pred_heading_deg = (heading_deg - gz_dps * dt_s) % 360.0`.
2. **Magnetometer Die-to-Body Alignment (`AK09916` at `0x0C`):**
   * Raw little-endian `int16` magnetometer registers `(rx, ry, rz)` (`0.15 µT/LSB`) are transformed into the `ICM-20948` frame inside `read_mag_raw_ut()`:
     * `mx_ut = +rx * 0.15`
     * `my_ut = -ry * 0.15`
     * `mz_ut = -rz * 0.15`
   * After applying hard-iron offsets and soft-iron chord scales from `/compass_cal.json` and tilt-compensating `(mx_ut, my_ut, mz_ut)` by `(pitch_deg, roll_deg)` onto the horizontal plane `(mx_h, my_h)`, clockwise magnetic heading is computed as:
     * `mag_heading_deg = (degrees(atan2(-my_h, mx_h)) + declination_deg) % 360.0`
   * Fused AHRS heading blends `94%` gyro prediction with `6%` tilt-compensated magnetometer heading per `100 ms` update.
3. **Physical Breakout-to-Chassis Nose Alignment (`[INSTALLED]` — CAD Verified, Bench Tilt Check Pending):**
   * Viewed from above (`+Z` out of page, front flat ToF bezel at the bottom of `pics/IMU Location.jpg`, `+X` Forward down, `+Y` Left to the right toward the `XT30` connector), both the Pimoroni Yukon PCB and the `ICM-20948` breakout sit flat horizontally (`+Z_die = +Z_body`), with the `ICM-20948` 5-pin header on the `-Y` left edge of the Yukon PCB and its silkscreen text upright to read (`x_m ≈ 0.000 m`, `y_m ≈ -0.025 m` toward Starboard / speaker side; centripetal acceleration offset `< 0.025 g` at `180 dps`).
   * Configured via `IMU_MOUNT_YAW_DEG = 90.0 deg` (`config.py`, `drivers/icm20948.py`: `x_body = -y_die`, `y_body = +x_die`, `z_body = +z_die`); verify pitch vs roll sign with a quick nose-down tilt once bolted in.

---

## Section 2: Installed & Active Hardware (`[INSTALLED]`)

All subsystems in this section are physically purchased (`BOM #PI796120`), supported by active MicroPython drivers in `drivers/`, and verified on the bench.

### 2.1 Host Controller & Core System Buses (`[INSTALLED]`)

* **Board Model:** **Pimoroni Yukon** (`PIMORONI_YUKON_W` wireless MicroPython firmware, `pimoroni_yukon` library `v1.0.3`).
* **Microcontroller:** **Raspberry Pi `RP2040`** (`B1` stepping, dual-core ARM Cortex-M0+, `264 KB` SRAM, `16 MB` external QSPI flash). *(Note: `AGENTS.md` architecture rules also maintain forward compatibility with `RP2350`, though the installed unit is `RP2040`.)*
* **Onboard I/O Expanders & Multiplexed Telemetry Bus:**
  * Dual onboard `TCA9555` I2C GPIO expanders providing `EXT_GPIO0`..`EXT_GPIO31` for module `SLOW1..SLOW3` control/ID lines, `MAIN_EN` (`EXT_GPIO6`), onboard switches `SW_A` (`EXT_GPIO17`), `SW_B` (`EXT_GPIO18`), `USER_SW` (`EXT_GPIO7`), user LEDs `LED_A` (`EXT_GPIO19`), `LED_B` (`EXT_GPIO20`), and analogue multiplexer select lines (`ADC_ADDR_1..3` on `EXT_GPIO12..14`, `ADC_MUX_EN_1..2` on `EXT_GPIO15`, `EXT_GPIO11`).
  * Shared analogue sense input: **`SHARED_ADC = GPIO29`** (`ADC3`).
* **Primary Sensor I2C Bus (`yukon.i2c` — Active Indoors & Outdoors):**
  * Peripheral: RP2040 **`I2C(0)`** at **`400 kHz`**
  * Pins: **`SDA = GPIO24`**, **`SCL = GPIO25`**, shared interrupt **`INT = GPIO28`**
  * Connected devices: `ICM-20948` (`0x68`), `AK09916` via bypass (`0x0C`), `PA1010D` GNSS (`0x10`).
* **Unallocated Expansion Header Pins:**
  * `GP26` (`GPIO26` / `A0` / `EX_I2C_SDA` / `EX_SPI_SCK`) and `GP27` (`GPIO27` / `A1` / `EX_I2C_SCL` / `EX_SPI_MOSI`) — currently unused.

### 2.2 Yukon 6-Slot Allocation Summary

| Yukon Slot | Installed Module | RP2040 Fast Pins (`FAST1..FAST4`) | `TCA9555` Slow Pins (`SLOW1..SLOW3`) | Status & Environment |
| :--- | :--- | :--- | :--- | :--- |
| **Slot 1** | `DualMotorModule` #1 (`DRV8424P`) | `GPIO0`, `GPIO1`, `GPIO2`, `GPIO3` | `EXT_GPIO3`, `EXT_GPIO4`, `EXT_GPIO5` | `[INSTALLED]` — Wheel A (`M1`, Front-Left `+60 deg`) & Wheel B (`M2`, Front-Right `300 deg`) |
| **Slot 2** | `DualMotorModule` #2 (`DRV8424P`) | `GPIO4`, `GPIO5`, `GPIO6`, `GPIO7` | `EXT_GPIO0`, `EXT_GPIO1`, `EXT_GPIO2` | `[INSTALLED]` — Wheel C (`M1`, Rear `180 deg`); `M2` spare |
| **Slot 3** | *Unpopulated* | `GPIO8`, `GPIO9`, `GPIO10`, `GPIO11` | `EXT_GPIO8`, `EXT_GPIO9`, `EXT_GPIO10` | Empty — Reserved for future expansion |
| **Slot 4** | `LEDStripModule` (`WS2812` + `5 V` Buck) | `GPIO12`, `GPIO13`, `GPIO14`, `GPIO15` | `EXT_GPIO23`, `EXT_GPIO21`, `EXT_GPIO22` | `[INSTALLED]` — Indoor & Outdoor |
| **Slot 5** | `RM2WirelessModule` (`CYW43439`) | `GPIO16`, `GPIO17`, `GPIO18`, `GPIO19` | `EXT_GPIO31`, `EXT_GPIO30`, `EXT_GPIO29` | `[INSTALLED]` — Indoor & Outdoor (Mandatory Slot 5) |
| **Slot 6** | `AudioAmpModule` (`TAS2780` `25 W`) | `GPIO20`, `GPIO21`, `GPIO22`, `GPIO23` | `EXT_GPIO26`, `EXT_GPIO28`, `EXT_GPIO27` | `[INSTALLED]` — Indoor & Outdoor (`3"` `4 Ω` `5 W` speaker fitted) |

### 2.3 Power Supply & Coulomb-Counting Battery Management (`[INSTALLED]`)

* **Battery Chemistry & Connector:** **3S LiPo (`3` cells in series, `2200 mAh` nominal capacity)** feeding Yukon's **`XT30`** primary input (via `XT60`-to-`XT30` adapter; two `2200 mAh` packs on hand).
* **Switched High-Power Bus (`MAIN_EN = EXT_GPIO6`):** Electronic e-Fuse rail supplying Slots 1–6 (`PVDD` for motor H-bridges, Slot 4 `5 V` buck converter, and Slot 6 `25 W` Class-D amplifier). Cutting `MAIN_EN` (`yukon.disable_main_output()`) drops downstream actuator quiescent draw to `0 mA` while keeping the `3.3 V` logic buck (`RP2040`, `RM2` Wi-Fi, `ICM-20948`, `PA1010D`) alive.
* **Voltage Thresholds & Telemetry (`config.py`, `drivers/battery.py` — Active Indoors & Outdoors at `10 Hz` / `100 ms`):**
  * `voltage_full_mv`: **`12600 mV`** (`12.60 V`, `4200 mV` per cell)
  * `voltage_fresh_hint_mv`: **`12400 mV`** (`12.40 V`, triggers fresh-pack swap prompt if persisted SoC `< 82%`)
  * `voltage_nominal_mv`: **`11100 mV`** (`11.10 V`, `3700 mV` per cell)
  * `voltage_low_warning_mv`: **`10500 mV`** (`10.50 V`, `3500 mV` per cell — low-battery warning)
  * `voltage_cutoff_mv`: **`9900 mV`** (`9.90 V`, `3300 mV` per cell — critical cutoff; `10` consecutive `100 ms` samples = `1000 ms` debounce triggers E-Stop and `yukon.disable_main_output()`)
  * `yukon_overvoltage_limit_mv`: **`17200 mV`** (`17.20 V` default Yukon firmware trip; `18000 mV` absolute max)
* **Current Sensing & Tare Calibration:**
  * Upstream logic quiescent current added to e-Fuse measurement: **`45 mA`** (`0.045 A`, `LOGIC_QUIESCENT_AMPS`) in normal active mode; **`25 mA`** (`0.025 A`, `SENTRY_LOW_POWER_AMPS`) during Sentry `WATCH` (`5 Hz` loop + `CYW43` beacon power-save).
  * Zero-current e-Fuse ADC offset (`zero_offset_a`): **`~0.349 A` nominal floor**, tared live across `32` samples at boot **prior** to enabling `MAIN_EN`.
  * State of Charge (`battery_state.json`): Persisted atomically every `30000 ms` when discharged capacity changes by `>= 0.2 mAh`.

### 2.4 3-Wheel Holonomic Omni-Drive Subsystem (`[INSTALLED]` — Chassis Calibration Pending)

* **Traction Motors:** 3× **`JGA25-370DG`** brushed DC gearmotors (`12 V` nominal, `427 RPM` no-load speed).
* **Motor Driver Modules:** 2× Pimoroni **Dual Motor / Bipolar Stepper Modules** (`TI DRV8424P` H-bridges, `25000 Hz` PWM, `70.0 °C` thermal trip):
  * **Wheel A (Channel 1 — `Slot 1, Motor 1`, Front-Left `+60.0 deg`):** `PWM_N = GPIO0` (`SLOT1_FAST1`), `PWM_P = GPIO1` (`SLOT1_FAST2`).
  * **Wheel B (Channel 2 — `Slot 1, Motor 2`, Front-Right `300.0 deg` / `-60.0 deg`):** `PWM_N = GPIO2` (`SLOT1_FAST3`), `PWM_P = GPIO3` (`SLOT1_FAST4`).
  * **Wheel C (Channel 3 — `Slot 2, Motor 1`, Rear `180.0 deg`):** `PWM_N = GPIO4` (`SLOT2_FAST1`), `PWM_P = GPIO5` (`SLOT2_FAST2`).
  * **Spare Channel (`Slot 2, Motor 2`):** `PWM_N = GPIO6` (`SLOT2_FAST3`), `PWM_P = GPIO7` (`SLOT2_FAST4`) — unallocated.
  * **H-Bridge Enable & Current Regulation:**
    * Slot 1: `MOTORS_EN = EXT_GPIO5` (`SLOW3`), `VREF1 = EXT_GPIO3` (`SLOW1`), `VREF2 = EXT_GPIO4` (`SLOW2`), `nFAULT = ADC1` (`mux 0`), `TEMP = ADC2` (`mux 3`).
    * Slot 2: `MOTORS_EN = EXT_GPIO2` (`SLOW3`), `VREF1 = EXT_GPIO0` (`SLOW1`), `VREF2 = EXT_GPIO1` (`SLOW2`), `nFAULT = ADC1` (`mux 1`), `TEMP = ADC2` (`mux 6`).
    * Configured `MOTOR_CURRENT_LIMIT_AMPS = 1.0 A` (`config.py`), which selects the discrete `DRV8424P` step **`0.786 A`** (`CURRENT_LIMIT_4`; setting `1.2 A` selects `1.143 A` if required under full cone mass).
* **Kinematic Geometry & Control Limits (`config.py`, `drivers/omni_kinematics.py`, `drivers/drive_controller.py`):**
  * **`wheel_radius_m`:** **`0.029 m`** (`58.0 mm` diameter, `25.0 mm` width omni-wheels; confirmed from BOM & CAD).
  * **`chassis_radius_m`:** **`0.12088 m`** (`120.877 mm` mid-plane wheel contact radius derived from CAD measurements: `108.378 mm` inner wheel face, `133.375 mm` outer wheel face; `ROBOT_BASE_RADIUS_M = 0.12088` in `config.py`).
  * **`max_linear_speed_mps`:** **`1.297 m/s`** theoretical unloaded wheel rim speed (`(427 / 60) * pi * 0.058`), scaled by operator speed caps (`indoor = 0.35`, `normal = 0.65`, `turbo = 1.00`), normalized wheel slew rate `2.8 /s`, and `deadman_timeout_ms = 650 ms`. Motors always boot **disarmed** (`armed = False`).
  * **Wheel Mounting Angles (`WHEEL_ANGLES_DEG`):** Verified from CAD (`pics/Bottom view Motor Definition.jpg` & `pics/IMU Location.jpg`, `+X` Forward out through the flat front ToF bezel, `+Y` Left toward the `XT30` / LiPo lead flank, `+Z` Up):
    * **Wheel A (Ch 1 — `Slot 1, Motor 1`, Front-Left):** **`60.0 deg`** (`1.0472 rad`)
    * **Wheel B (Ch 2 — `Slot 1, Motor 2`, Front-Right):** **`300.0 deg`** (`-60.0 deg`, `5.2360 rad`)
    * **Wheel C (Ch 3 — `Slot 2, Motor 1`, Rear vertex):** **`180.0 deg`** (`3.1416 rad`; rolling axis along `±Y`, zero spin during pure `+X` surge)
    * Inverse kinematics: `v_i = -sin(alpha_i) * vx + cos(alpha_i) * vy + chassis_radius_m * omega`.
  * **Motor Polarities (`MOTOR_POLARITY`):** Configurable 3-tuple **`(1, 1, 1)`** in `config.py` applied at the `DRV8424P` H-bridge stage in `DriveController`. All 3 motors are mounted with identical `C3` radial symmetry (gearboxes pointing radially inward) and wired identically, so bench verification will select either `(1, 1, 1)` or `(-1, -1, -1)` uniformly.

### 2.5 60-LED Addressable Ring / Warning Array (`[INSTALLED]`)

* **Module & LED Hardware:** Pimoroni **LED Strip Module** in **Slot 4** driving a **`60`-pixel addressable `WS2812` RGB LED strip** (The Pi Hut SKU `104554`, `60 LED/m`, `NEOPIXEL` single-strip mode). *(Module verified; final physical cone wrap/mounting noted in `TASKS.md`.)*
* **Slot 4 Pin Mapping (`LEDStripModule` on `PIO0`, `SM0`):**
  * **WS2812 Data Pin:** **`GPIO15`** (`SLOT4_FAST4`)
  * **Unused Secondary Data/Clock Pin:** `GPIO14` (`SLOT4_FAST3`)
  * **5 V Buck Enable (`POWER_EN`):** **`GPIO13`** (`SLOT4_FAST2`, active-high; automatically disabled when LED mode is `"off"` to eliminate buck/LED quiescent current)
  * **5 V Power-Good (`PGOOD`):** **`GPIO12`** (`SLOT4_FAST1`, input with pull-up)
  * **Thermistor:** `ADC2` (`mux 7`, `80.0 °C` trip)
* **Operating Parameters:** `NUM_LEDS = 60`, default brightness `0.40` (`0.05..1.00`), non-blocking `20 Hz` (`50 ms`) refresh across `"off"`, `"beacon"`, `"strobe"`, `"solid"`, `"pulse"`, `"police"`, and `"rainbow"`. Active **indoors and outdoors**.

### 2.6 Yukon `AudioAmpModule` & Speaker Subsystem (`[INSTALLED]`)

* **Module & Speaker:** Pimoroni **Audio Amp Module** (Texas Instruments **`TAS2780`** `25 W` Class-D Mono I2S Amplifier) in **Slot 6**, driving an installed **3" `4 Ω` (`5 W` continuous) speaker**.
* **Slot 6 Pin Mapping (`drivers/audio_sfx.py`):**
  * **RP2040 `I2S(0)` Digital Audio Bus (`44100 Hz`, `16-bit` mono, `3072 B` ring buffer, `512 B` ping-pong DMA buffers):**
    * **`SD` (Serial Data Out):** **`GPIO20`** (`SLOT6_FAST1`)
    * **`SCK` / `BCLK` (Bit Clock):** **`GPIO21`** (`SLOT6_FAST2`)
    * **`WS` / `LRCLK` (Word Select / Frame Sync):** **`GPIO22`** (`SLOT6_FAST3`)
    * *(Unused fast pin: `GPIO23` / `SLOT6_FAST4`)*
  * **Amplifier Hardware Shutdown (`AMP_EN` / `SDZ`):** **`EXT_GPIO26`** (`SLOT6_SLOW1`, active-high)
  * **Software-Bit-Banged Amplifier Control I2C Bus (`TAS2780` 7-bit address `0x38`):**
    * **`SLOW_SDA`:** **`EXT_GPIO28`** (`SLOT6_SLOW2`)
    * **`SLOW_SCL`:** **`EXT_GPIO27`** (`SLOT6_SLOW3`)
  * **Thermistor:** `ADC2` (`mux 10`, `50.0 °C` trip)
* **Hardware Guardrails (Active Indoors & Outdoors):**
  * **Never Call `I2S.deinit()` at Runtime:** On Yukon Wireless (`RP2040` + `RM2`), `machine.I2S.deinit()` disables shared `DMA_IRQ_0` globally and severes `CYW43439` PIO SPI Wi-Fi communication. `I2S(0)` is initialised once at boot and fed zero-filled silence when idle.
  * **5 W Speaker Protection Ceiling & Pop-Free Shutdown:** `TAS2780` digital volume (`DVC` reg `0x1A`) follows a Stevens' Power Law taper capped at **`-4.0 dB`** (`100%` UI volume) down to `-54.0 dB` (`0%` = `0xCF` hard mute). After `15000 ms` of inactivity, `ConeAudio` executes a non-blocking 3-stage pop-free shutdown (`50 ms` per step: `DVC=0xCF` + `MODE_CTRL=0x81` $\rightarrow$ `MODE_CTRL=0x02` Hi-Z Software Shutdown $\rightarrow$ `SDZ` low).

### 2.7 Wireless Command & Telemetry Radio (`[INSTALLED]`)

* **Module:** Pimoroni **RM2 Wireless Module** (`Infineon CYW43439` `2.4 GHz` 802.11b/g/n Wi-Fi + Bluetooth) in **Slot 5** (fixed by Pimoroni firmware).
* **Slot 5 Pin Mapping:** `WL_REG_ON = GPIO16` (`FAST1`), `WL_CS = GPIO17` (`FAST2`), `WL_CLOCK = GPIO18` (`FAST3`), shared `WL_DATA_OUT / DATA_IN / HOST_WAKE = GPIO19` (`FAST4`).
* **Network Behaviour (Active Indoors & Outdoors):** Connects as a Wi-Fi `STA` (`9000 ms` timeout) using local git-ignored credentials (`secrets.py` or `secrets.json`); falls back automatically to open standalone Wi-Fi `AP` **`TrafficCone`** (`192.168.4.1`, HTTP port `80`).

### 2.8 `PA1010D` GNSS Receiver (`[INSTALLED]` — Outdoor Navigation Only)

* **Module:** Pimoroni **`PA1010D` GPS Breakout** (CDTop `PA1010D` multi-constellation GPS/GLONASS/Galileo/BDS receiver with integrated patch antenna).
* **Physical Bus Interface (I2C, *Not* UART):**
  * **Important Hardware Note:** In this build, the `PA1010D` is **not** connected via UART. It is soldered directly to the Yukon primary **`I2C(0)`** header (**`SDA = GPIO24`**, **`SCL = GPIO25`**, `400 kHz`) at 7-bit I2C address **`0x10`** (`PA1010D` UART pins are unused / unconnected).
  * `drivers/pa1010d.py` polls `48-byte` ASCII NMEA 0183 chunks over I2C (`$GNRMC`, `$GNGGA`), validates XOR checksums, and parses coordinates directly into exact 32-bit signed micro-degree integers (`lat_e7`, `lon_e7` = `deg * 10_000_000`, `0.01113 m` resolution) to prevent RP2040 single-precision `float` quantisation error.
* **Indoor vs Outdoor Operational Availability:**
  * **Indoors:** The `PA1010D` responds on I2C (`0x10`) and outputs valid NMEA frames, **but satellite lock (`has_fix`) is unavailable indoors** (or severely degraded). Autonomous GNSS waypoint missions (`WaypointNavigator`) require `has_fix == True` and are **outdoor-only**, whereas manual holonomic drive, IMU/compass AHRS, LEDs, audio, battery Coulomb counting, and IMU Sentry Mode operate fully indoors without GNSS.

*(Note on Uninstalled Spares from `BOM #PI796120`: One Pimoroni Regulated Quad Servo Module and one Proto Module were purchased in the initial order; wheel retraction servos were descoped in `HANDOFF.md`, and neither module is installed on the Yukon.)*

---

## Section 3: Planned Expansion Hardware (`[PLANNED]` — Do NOT Initialise in Firmware Yet)

> [!IMPORTANT]
> **Strict Non-Initialisation Rule for `[PLANNED]` Hardware:**  
> All peripherals in Section 3 are currently **unpurchased and/or unfitted**. Firmware modules (`boot.py`, `config.py`, `main.py`, `telemetry_server.py`, and `drivers/*.py`) **MUST NOT** import drivers for, reserve or toggle GPIOs for, probe I2C addresses for, or open UART peripherals for any `[PLANNED]` hardware until Andrew confirms physical installation on the bench and promotes the subsystem into **Section 2 (`[INSTALLED]`)**.

### 3.1 Dual Pimoroni `VL53L5CX` (`PIM622`) 8×8 Multi-Zone ToF Array (`[PLANNED]` — `IDEA-002`)

* **Hardware:** 2× Pimoroni **`VL53L5CX` Time-of-Flight Sensor Breakouts (`PIM622`)** (`8 × 8` = `64` depth zones per sensor, `940 nm` VCSEL, `45 deg × 45 deg` FoV, `15 Hz` frame rate, `~100 mA` typical / `150 mA` peak @ `3.3 V` each).
* **Planned I2C Bus & Boot Address Assignment:**
  * Bus: Shared Yukon primary `I2C(0)` (`SDA = GPIO24`, `SCL = GPIO25`).
  * Dynamic 7-bit I2C addresses assigned at boot via `LPn` (active-low I2C standby) sequencing:
    * **Left `VL53L5CX` Sensor:** Reassigned at boot from volatile default `0x29` $\rightarrow$ **`0x30`**
    * **Right `VL53L5CX` Sensor:** Held in standby during Left reassignment, then woken at default **`0x29`**
* **Planned Control Pins & Mounting Geometry:**
  * `LPN_LEFT` GPIO pin: **`[TBD — verify on bench]`** (not yet wired; candidate spare pins on unpopulated Slot 3 `GPIO8..GPIO11` / `EXT_GPIO8..10` or expansion `GPIO26`/`GPIO27`).
  * `LPN_RIGHT` GPIO pin: **`[TBD — verify on bench]`**.
  * Nose bezel mounting geometry (`IDEA-002` design target: `height_m ≈ 0.300 m`, yaw splay `±21.0 deg` for `87.0 deg` horizontal fan, downward pitch `11.0 deg` for kerb/drop-off rows): **`[TBD — verify on bench]`**.

### 3.2 Dedicated `ESP32-C3` / `ESP32-S3` ESP-NOW Mesh Co-Processor (`[PLANNED]` — `IDEA-006`)

* **Hardware:** Dedicated external **`ESP32-C3` or `ESP32-S3`** `2.4 GHz` radio co-processor running AES-CCMP encrypted, connectionless **ESP-NOW** multi-hop (`TTL = 3`) inter-cone swarming (`arse-swarm/v1`), isolating `20 Hz` formation traffic from the Slot 5 `RM2` operator Wi-Fi link.
* **Planned Host Interface:** `3.3 V` asynchronous serial UART (`921600 baud`, COBS packet framing with `CRC-16`).
* **Planned RP2040 UART Instance & GPIO Pins:**
  * `UART_ID`, `TX` pin, `RX` pin, and optional `RST` pin: **`[TBD — verify on bench]`** *(Note: RP2040 `GPIO0/1` and `GPIO4/5` are occupied by Slot 1 & Slot 2 motor PWM channels; unpopulated Slot 3 exposes `GPIO8` (`UART1 TX`) and `GPIO9` (`UART1 RX`), subject to future slot allocation alongside `VL53L5CX` `LPn` lines).*
