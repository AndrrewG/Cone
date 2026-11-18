# ==============================================================================
# ARSE — Autonomous Road Systems Engineering
# Proprietary & Confidential — Division of Conical Mobility & Traffic Robotics
# ------------------------------------------------------------------------------
# Module:      config.py
# Platform:    ARSE Mk.I Autonomous Traffic Cone (Pimoroni Yukon RP2040)
# Description: Master hardware slot allocation, electrical protection thresholds,
#              kinematic geometry, AHRS compass, and waypoint navigation limits.
# Copyright (c) 2026 Autonomous Road Systems Engineering (ARSE).
# ==============================================================================

# ------------------------------------------------------------------------------
# Yukon Module Slot Allocation
# ------------------------------------------------------------------------------
SLOT_MOTOR_1 = 1                # Wheel A (M1: Front-Left +60 deg), Wheel B (M2: Front-Right 300 deg)
SLOT_MOTOR_2 = 2                # Wheel C (M1: Rear 180 deg), M2: Auxiliary Spare
SLOT_LED = 4                    # WS2812 Addressable LED Strip Module
SLOT_WIRELESS = 5               # RM2 Wireless Module (CYW43439 Wi-Fi & BLE)
SLOT_AUDIO = 6                  # Audio Amp Module (TAS2780 25W Class-D Mono Amp)
AUDIO_I2S_ID = 0

# ------------------------------------------------------------------------------
# Traction Battery & Coulomb-Counting Telemetry (2x 3S LiPo 2200mAh Packs)
# ------------------------------------------------------------------------------
BATTERY_CAPACITY_MAH = 2200.0
BATTERY_CELLS = 3
VOLTAGE_FULL = 12.60            # 4.20V per cell (100%)
VOLTAGE_NOMINAL = 11.10         # 3.70V per cell
VOLTAGE_LOW_WARNING = 10.50     # 3.50V per cell -> low battery advisory
VOLTAGE_CUTOFF = 9.90           # 3.30V per cell -> critical bus disconnect
LOGIC_QUIESCENT_AMPS = 0.045    # Upstream 3.3V buck draw (RP2040 + RM2 + GNSS + IMU)
BATTERY_STATE_FILE = "battery_state.json"
BATTERY_SAVE_INTERVAL_MS = 30000

# ------------------------------------------------------------------------------
# Wireless Command Link & Field Telemetry UI
# ------------------------------------------------------------------------------
WIFI_AP_SSID = "TrafficCone"    # Retained standalone field hotspot SSID
WIFI_AP_PASSWORD = ""           # Open AP fallback when outside station coverage
WIFI_CONNECT_TIMEOUT_MS = 9000
WEB_SERVER_PORT = 80

# ------------------------------------------------------------------------------
# Holonomic Drive Train, Slew Control & Motion Safety
# ------------------------------------------------------------------------------
WHEEL_DIAMETER_MM = 58.0
WHEEL_RADIUS_M = 0.029
WHEEL_INNER_RADIUS_MM = 108.378
WHEEL_OUTER_RADIUS_MM = 133.375
ROBOT_BASE_RADIUS_M = 0.12088   # Mid-plane wheel contact radius: (108.378 + 133.375) / 2000 m
WHEEL_ANGLES_DEG = (60.0, 300.0, 180.0)  # (Wheel A Front-Left, Wheel B Front-Right, Wheel C Rear)
MOTOR_POLARITY = (1, 1, 1)      # +1 = positive command drives wheel rim in +CCW yaw sense
MOTOR_MAX_RPM = 427.0
MAX_LINEAR_SPEED_MPS = (MOTOR_MAX_RPM / 60.0) * 3.14159 * (WHEEL_DIAMETER_MM / 1000.0)
MOTOR_CURRENT_LIMIT_AMPS = 1.0  # DRV8424 H-Bridge current limit

DRIVE_SLEW_PER_SEC = 2.8        # Max normalized wheel speed change per second (0->1 in ~0.35s)
DEADMAN_TIMEOUT_MS = 650        # Auto-stop manual drive if phone heartbeat drops >650ms
SPEED_MODES = {
    "indoor": 0.35,             # Kid-safe / bench speed cap (35%)
    "normal": 0.65,             # Standard outdoor cruise cap (65%)
    "turbo": 1.00,              # Full rated output (100%)
}
DEFAULT_SPEED_MODE = "indoor"

# ------------------------------------------------------------------------------
# 9DoF Inertial & Compass Subsystem (ICM-20948 + AK09916)
# ------------------------------------------------------------------------------
I2C_ADDR_ICM20948 = 0x68        # Accel + Gyro die
I2C_ADDR_AK09916 = 0x0C         # Internal 3-axis Magnetometer via I2C bypass
COMPASS_CAL_FILE = "compass_cal.json"
COMPASS_DECLINATION_DEG = 0.0   # Magnetic declination offset (deg)
IMU_MOUNT_YAW_DEG = 90.0        # Top-down CCW die-to-body yaw (+X_die -> +Y_body Left, +Y_die -> -X_body Rear)
IMU_OFFSET_XY_M = (0.000, -0.025)  # IMU position relative to base centre (x_m, y_m)
MAX_SAFE_TILT_DEG = 40.0        # Tip-over / pickup safety cutoff angle (deg)

# ------------------------------------------------------------------------------
# GNSS & Autonomous Waypoint Navigation (PA1010D)
# ------------------------------------------------------------------------------
I2C_ADDR_PA1010D = 0x10         # PA1010D GNSS Receiver
MISSION_STATE_FILE = "mission_state.json"
WAYPOINT_ARRIVAL_RADIUS_M = 1.5
WAYPOINT_SLOWDOWN_RADIUS_M = 4.0
NAV_CRUISE_SPEED = 0.55
NAV_MIN_SPEED = 0.18
NAV_KP_YAW = 0.02               # Proportional yaw-hold gain (rad/s per deg error)

# ------------------------------------------------------------------------------
# Visual Warning Array & Acoustic Annunciator
# ------------------------------------------------------------------------------
NUM_LEDS = 60
LED_BRIGHTNESS = 0.4
DEFAULT_AUDIO_VOLUME = 0.35

# ------------------------------------------------------------------------------
# Anti-Theft IMU Sentry Mode ("Cone-Nicking Deterrence")
# ------------------------------------------------------------------------------
SENTRY_ARM_DELAY_MS = 3000          # 3s settling countdown after arming
SENTRY_ALARM_HOLD_MS = 12000        # Keep police siren/lights active 12s after motion stops
SENTRY_WATCH_INTERVAL_MS = 200      # 5 Hz low-power IMU watch polling period
SENTRY_LOW_POWER_AMPS = 0.025       # Reduced 3.3V logic draw during Wi-Fi PM + 5 Hz watch
SENTRY_SENSITIVITY = {
    "high":   (0.06, 10.0),         # (accel_delta_g, gyro_dps) — hair-trigger bench test
    "medium": (0.13, 18.0),         # Standard outdoor anti-theft threshold
    "low":    (0.24, 32.0),         # High-wind / heavy roadside vibration threshold
}
DEFAULT_SENTRY_SENSITIVITY = "medium"
