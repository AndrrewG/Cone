# ==============================================================================
# ARSE — Autonomous Road Systems Engineering
# Proprietary & Confidential — Division of Conical Mobility & Traffic Robotics
# ------------------------------------------------------------------------------
# Module:      main.py
# Platform:    ARSE Mk.I Autonomous Traffic Cone (Pimoroni Yukon RP2040)
# Description: Integrated flight-control, navigation & field telemetry executive.
#              Runs 20 Hz holonomic control, LEDs & audio alongside 10 Hz AHRS,
#              Coulomb battery BMS, GNSS waypoint guidance, and HTTP operator UI.
# Copyright (c) 2026 Autonomous Road Systems Engineering (ARSE).
# ==============================================================================

import asyncio
import gc
import sys
import time
gc.collect()
# Reserve contiguous DMA span before imports so ConeAudio I2S(ibuf=3072) never fragments
_i2s_reserve = bytearray(3584)


class _StubMod:
    pass


class _StubDev:
    def __init__(self, *a, **kw):
        pass

    def __stop_i2s(self):
        pass

    def __start_i2s(self):
        pass


# Prevent pimoroni_yukon from compiling 20 KB of unused WavPlayer / Stepper drivers
if "pimoroni_yukon" not in sys.modules:
    _d_pkg = _StubMod()
    _d_aud = _StubMod()
    _d_aud.WavPlayer = _StubDev
    _d_stp = _StubMod()
    _d_stp.Stepper = _StubDev
    sys.modules["pimoroni_yukon.devices"] = _d_pkg
    sys.modules["pimoroni_yukon.devices.audio"] = _d_aud
    sys.modules["pimoroni_yukon.devices.stepper"] = _d_stp
    del _d_pkg, _d_aud, _d_stp

import pimoroni_yukon as _pyk
import pimoroni_yukon.modules as _pyk_mods
from pimoroni_yukon import Yukon, SLOT1, SLOT2, SLOT4, SLOT5, SLOT6
from pimoroni_yukon.modules.dual_motor import DualMotorModule
from pimoroni_yukon.modules.led_strip import LEDStripModule
from pimoroni_yukon.modules.rm2_wireless import RM2WirelessModule
from pimoroni_yukon.modules.audio_amp import AudioAmpModule

# Reclaim RAM from the 7 uninstalled Yukon slot drivers before compiling Cone modules
_INSTALLED_MODS = (AudioAmpModule, DualMotorModule, LEDStripModule, RM2WirelessModule)
_pyk.KNOWN_MODULES = _INSTALLED_MODS
_pyk_mods.KNOWN_MODULES = _INSTALLED_MODS
for _um, _uc in (
    ("bench_power", "BenchPowerModule"),
    ("big_motor", "BigMotorModule"),
    ("dual_output", "DualOutputModule"),
    ("proto", "ProtoPotModule"),
    ("quad_servo_direct", "QuadServoDirectModule"),
    ("quad_servo_reg", "QuadServoRegModule"),
    ("serial_servo", "SerialServoModule"),
):
    if hasattr(_pyk_mods, _um):
        delattr(_pyk_mods, _um)
    if hasattr(_pyk_mods, _uc):
        delattr(_pyk_mods, _uc)
    sys.modules.pop("pimoroni_yukon.modules." + _um, None)
del _pyk, _pyk_mods, _INSTALLED_MODS, _um, _uc
gc.collect()

import config
from telemetry_server import TelemetryServer
gc.collect()
from drivers.navigator import WaypointNavigator
gc.collect()
from security import AuthManager
gc.collect()
from drivers.audio_sfx import ConeAudio
gc.collect()
from nav import PatternRunner
gc.collect()
from drivers.icm20948 import ICM20948
gc.collect()
from drivers.sentry import SentryGuard
gc.collect()
from drivers.battery import BatteryMonitor
from drivers.drive_controller import DriveController
gc.collect()
from drivers.pa1010d import PA1010D
from drivers.lights import ConeLights
from drivers.wifi_manager import ConeWiFi
from drivers.omni_kinematics import Omni3Kinematics
gc.collect()


class ConeApp:
    def __init__(self):
        self.boot_ticks = time.ticks_ms()
        self.yukon = Yukon()

        # Register populated Yukon hardware slots
        self.motor_mod1 = DualMotorModule()
        self.motor_mod2 = DualMotorModule()
        self.led_mod = LEDStripModule(
            LEDStripModule.NEOPIXEL, 0, 0, config.NUM_LEDS, config.LED_BRIGHTNESS
        )
        self.wireless_mod = RM2WirelessModule()
        self.audio_mod = AudioAmpModule(config.AUDIO_I2S_ID)

        self.yukon.register_with_slot(self.motor_mod1, SLOT1)
        self.yukon.register_with_slot(self.motor_mod2, SLOT2)
        self.yukon.register_with_slot(self.led_mod, SLOT4)
        self.yukon.register_with_slot(self.wireless_mod, SLOT5)
        self.yukon.register_with_slot(self.audio_mod, SLOT6)

        self.battery = None
        self.lights = None
        self.audio = None
        self.drive = None
        self.nav = None
        self.patterns = None
        self.imu = None
        self.gps = None
        self.wifi = None
        self.sentry = None
        self.auth = None
        self.server = None
        self._b_critical = False

        self.imu_state = {
            "online": False,
            "mag_online": False,
            "heading_deg": 0.0,
            "mag_heading_deg": 0.0,
            "pitch_deg": 0.0,
            "roll_deg": 0.0,
            "tilt_fault": False,
            "calibrated": False,
            "calibrating": False,
            "cal_samples": 0,
            "mag_ut": [0.0, 0.0, 0.0],
            "temp_c": 0.0,
        }
        self.board_temp_c = 0.0

    def setup_hardware(self):
        print("\n" + "=" * 64)
        print("  ARSE - AUTONOMOUS ROAD SYSTEMS ENGINEERING")
        print("  Mk.I Autonomous Traffic Cone | Guidance, Drive & Telemetry")
        print("=" * 64)

        self.yukon.verify_and_initialise()
        self.motor_mod1.set_current_limit(config.MOTOR_CURRENT_LIMIT_AMPS)
        self.motor_mod2.set_current_limit(config.MOTOR_CURRENT_LIMIT_AMPS)

        # Release reserved contiguous span right into ConeAudio's 3072 B I2S ring buffer (MAIN_EN still off)
        globals().pop("_i2s_reserve", None)
        gc.collect()
        self.audio = ConeAudio(
            self.audio_mod,
            self.yukon,
            default_volume=config.DEFAULT_AUDIO_VOLUME,
        )
        gc.collect()

        # Tare 0A ADC floor BEFORE enabling high-power bus so Coulomb integration is exact
        self.battery = BatteryMonitor(
            self.yukon,
            capacity_mah=config.BATTERY_CAPACITY_MAH,
            logic_idle_amps=config.LOGIC_QUIESCENT_AMPS,
            state_file=config.BATTERY_STATE_FILE,
            save_interval_ms=config.BATTERY_SAVE_INTERVAL_MS,
        )
        b_init = self.battery.get_status()
        print(
            "[ARSE-BMS] {:.2f}V | SoC={:.1f}% ({:.0f}/{:.0f} mAh, mode={}) | 0A Tare={:.3f}A".format(
                b_init["voltage_v"],
                b_init["soc_percent"],
                b_init["remaining_mah"],
                b_init["capacity_mah"],
                b_init["seed_mode"],
                b_init["zero_offset_a"],
            )
        )

        # Energise main power rail if traction pack exceeds critical cell cutoff
        if b_init["battery_present"] and b_init["voltage_v"] > config.VOLTAGE_CUTOFF:
            self.yukon.enable_main_output()
            time.sleep_ms(80)
        self.motor_mod1.disable()
        self.motor_mod2.disable()

        # Actuators & Drive Controllers
        gc.collect()
        self.lights = ConeLights(
            self.led_mod.strip,
            num_leds=config.NUM_LEDS,
            led_module=self.led_mod,
            brightness=config.LED_BRIGHTNESS,
        )
        gc.collect()

        kin = Omni3Kinematics(
            wheel_radius_m=config.WHEEL_RADIUS_M,
            base_radius_m=config.ROBOT_BASE_RADIUS_M,
            max_speed_mps=config.MAX_LINEAR_SPEED_MPS,
        )
        self.drive = DriveController(
            self.yukon,
            self.motor_mod1,
            self.motor_mod2,
            kinematics=kin,
            speed_modes=config.SPEED_MODES,
            default_mode=config.DEFAULT_SPEED_MODE,
            slew_per_sec=config.DRIVE_SLEW_PER_SEC,
            deadman_ms=config.DEADMAN_TIMEOUT_MS,
        )
        self.nav = WaypointNavigator(
            state_file=config.MISSION_STATE_FILE,
            arrival_radius_m=config.WAYPOINT_ARRIVAL_RADIUS_M,
            slowdown_radius_m=config.WAYPOINT_SLOWDOWN_RADIUS_M,
            cruise_speed=config.NAV_CRUISE_SPEED,
            min_speed=config.NAV_MIN_SPEED,
            kp_yaw=config.NAV_KP_YAW,
        )
        self.board_temp_c = self.yukon.read_temperature()

        # Initialise I2C navigation sensors (9DoF IMU + AK09916 Mag + PA1010D GNSS)
        devices = self.yukon.i2c.scan()
        if config.I2C_ADDR_ICM20948 in devices:
            try:
                self.imu = ICM20948(
                    self.yukon.i2c,
                    address=config.I2C_ADDR_ICM20948,
                    mag_address=config.I2C_ADDR_AK09916,
                    cal_file=config.COMPASS_CAL_FILE,
                    declination_deg=config.COMPASS_DECLINATION_DEG,
                    max_tilt_deg=config.MAX_SAFE_TILT_DEG,
                )
                self.imu_state = self.imu.update_ahrs()
                print(
                    "[ARSE-IMU] ICM-20948 online | AK09916 Mag={} | Heading={:.1f} deg".format(
                        "OK" if self.imu.mag_online else "OFFLINE",
                        self.imu_state["heading_deg"],
                    )
                )
            except Exception as e:
                print("[ARSE-IMU] Init fault:", e)

        if config.I2C_ADDR_PA1010D in devices:
            try:
                self.gps = PA1010D(self.yukon.i2c, address=config.I2C_ADDR_PA1010D)
                print("[ARSE-GNSS] PA1010D receiver online")
            except Exception as e:
                print("[ARSE-GNSS] Init fault:", e)

        self.patterns = PatternRunner(
            kinematics=kin,
            imu=self.imu,
            led_controller=self.lights,
        )
        gc.collect()

        # Establish wireless link (STA credentials first, fallback to 'TrafficCone' AP)
        self.wifi = ConeWiFi(
            ap_ssid=config.WIFI_AP_SSID,
            ap_password=config.WIFI_AP_PASSWORD,
            connect_timeout_ms=config.WIFI_CONNECT_TIMEOUT_MS,
        )
        w_stat = self.wifi.connect_or_start_ap()
        gc.collect()

        self.sentry = SentryGuard(
            self.yukon,
            self.imu,
            self.lights,
            self.audio,
            self.drive,
            self.nav,
            self.wifi,
            self.battery,
            presets=config.SENTRY_SENSITIVITY,
            default_sensitivity=config.DEFAULT_SENTRY_SENSITIVITY,
            arm_delay_ms=config.SENTRY_ARM_DELAY_MS,
            alarm_hold_ms=config.SENTRY_ALARM_HOLD_MS,
            normal_idle_amps=config.LOGIC_QUIESCENT_AMPS,
            low_power_amps=config.SENTRY_LOW_POWER_AMPS,
            patterns=self.patterns,
        )

        # Initialise operator authentication & session lockout manager
        self.auth = AuthManager(
            secrets_path="/flash/secrets.json",
            voice_engine=self.audio,
            led_controller=self.lights,
        )
        gc.collect()

        self.server = TelemetryServer(
            app=self,
            html_path="web/index.html",
            port=config.WEB_SERVER_PORT,
            auth_manager=self.auth,
        )

        gc.collect()
        print("-" * 64)
        print("ARSE OPERATOR TERMINAL READY: http://{}/ (Free Heap: {} B)".format(w_stat["ip"], gc.mem_free()))
        if w_stat["mode"] == "AP":
            print("(Connect field device Wi-Fi to '{}')".format(w_stat["ssid"]))
        print("-" * 64)

    def get_telemetry_snapshot(self):
        uptime_s = time.ticks_diff(time.ticks_ms(), self.boot_ticks) // 1000
        gps_st = self.gps.get_status() if self.gps else {"has_fix": False, "satellites": 0}
        pat_st = (
            self.patterns.get_status()
            if self.patterns
            else {
                "state": "IDLE",
                "active_routine": None,
                "preset": "indoor",
                "progress": 0.0,
                "local_x_m": 0.0,
                "local_y_m": 0.0,
            }
        )
        return {
            "battery": self.battery.get_status(),
            "drive": self.drive.get_status(),
            "lights": self.lights.get_status(),
            "audio": self.audio.get_status(),
            "imu": self.imu_state,
            "gps": gps_st,
            "nav": self.nav.get_status(gps_st),
            "routines": pat_st,
            "active_routine": pat_st["active_routine"],
            "routine_progress": pat_st["progress"],
            "local_x_m": pat_st["local_x_m"],
            "local_y_m": pat_st["local_y_m"],
            "sentry": self.sentry.get_status() if self.sentry else {},
            "wifi": self.wifi.get_status() if self.wifi else {},
            "auth": (
                self.auth.get_public_status()
                if self.auth
                else {"locked": False, "active_sessions": 0}
            ),
            "system": {
                "uptime_s": uptime_s,
                "board_temp_c": round(self.board_temp_c, 1),
                "main_output_enabled": self.yukon.is_main_output_enabled(),
                "mem_free_kb": round(gc.mem_free() / 1024.0, 1),
            },
        }

    async def control_and_telemetry_loop(self):
        # 50 Hz (20 ms) holonomic loop; throttles to 5 Hz (200 ms) during Sentry WATCH
        tick = 0
        watch_tick = 0
        while True:
            if self.sentry and self.sentry.is_low_power_watch():
                self.sentry.update()
                # Remain in 5 Hz sleep unless tamper just tripped ALARM
                if self.sentry.is_low_power_watch():
                    if watch_tick == 0:
                        b_stat = self.battery.update()
                        self._b_critical = bool(b_stat["is_critical"])
                        if self._b_critical and self.yukon.is_main_output_enabled():
                            self.yukon.disable_main_output()
                            self.battery.save()
                        self.board_temp_c = self.yukon.read_temperature()
                    watch_tick = (watch_tick + 1) % 5
                    await asyncio.sleep_ms(config.SENTRY_WATCH_INTERVAL_MS)
                    continue

            if (tick % 5) == 0:
                b_stat = self.battery.update()
                self._b_critical = bool(b_stat["is_critical"])

                # Critical 3S LiPo over-discharge protection
                if self._b_critical and self.yukon.is_main_output_enabled():
                    print("[ARSE-SAFETY] Critical cell voltage! Isolating main power bus.")
                    if self.patterns and self.patterns.is_active():
                        self.patterns.stop("Critical battery cutoff")
                    self.drive.estop()
                    self.lights.set_mode("off")
                    self.audio.stop()
                    self.yukon.disable_main_output()
                    self.battery.save()

                if self.imu:
                    try:
                        self.imu_state = self.imu.update_ahrs()
                    except Exception:
                        self.imu_state["online"] = False

                if self.sentry and self.sentry.is_enabled():
                    self.sentry.update()
                else:
                    if self.gps:
                        self.gps.update()

                    if not (self.patterns and self.patterns.is_active()):
                        # Closed-loop autonomous GNSS waypoint update (10 Hz)
                        gps_st = self.gps.get_status() if self.gps else {"has_fix": False}
                        vx, vy, om, nav_active, sfx = self.nav.update(gps_st, self.imu_state, b_stat)
                        self.drive.set_auto_twist(vx, vy, om, active=nav_active)
                        led_cmd = self.nav.pop_light_cmd()
                        if led_cmd and self.yukon.is_main_output_enabled():
                            try:
                                self.lights.set_mode(led_cmd)
                            except Exception:
                                pass
                        if sfx and self.yukon.is_main_output_enabled():
                            try:
                                self.audio.play(sfx)
                            except Exception:
                                pass
            elif self.imu and self.imu_state.get("online"):
                # Zero-allocation 50 Hz gyro Z heading integration between 10 Hz mag updates
                try:
                    self.imu_state["heading_deg"] = round(self.imu.step_fast_50hz(), 1)
                except Exception:
                    self.imu_state["online"] = False

            safety_ok = (not self.imu_state.get("tilt_fault", False)) and (not self._b_critical)

            # 50 Hz GPS-free dead-reckoning routine update
            if self.patterns and self.patterns.is_active():
                if (not safety_ok) or (not self.drive.armed) or (not self.imu_state.get("online", False)):
                    reason = (
                        "Tilt / tip-over safety"
                        if self.imu_state.get("tilt_fault")
                        else ("Motors disarmed" if not self.drive.armed else "Safety cutoff")
                    )
                    self.patterns.stop(reason)
                    self.drive.set_routine_wheels(0.0, 0.0, 0.0, active=False)
                else:
                    w0, w1, w2, pat_active = self.patterns.step()
                    self.drive.set_routine_wheels(w0, w1, w2, active=pat_active)
            elif self.drive._routine_active:
                self.drive.set_routine_wheels(0.0, 0.0, 0.0, active=False)

            # 50 Hz motor slew & safety gate
            self.drive.tick(safety_ok=safety_ok)

            # 20 Hz LED & audio updates (sub-ticks 0 & 2 of each 100 ms block)
            sub = tick % 5
            if sub == 0 or sub == 2:
                self.lights.tick()
                self.audio.tick()

            if tick == 0:
                self.board_temp_c = self.yukon.read_temperature()
                self.drive.sample_driver_diagnostics()
                if (not self.drive.armed) or (self.drive.active_source == "idle"):
                    gc.collect()

            tick = (tick + 1) % 50
            await asyncio.sleep_ms(20)

    async def run_async(self):
        await self.server.start()
        await self.control_and_telemetry_loop()

    def start(self):
        try:
            self.setup_hardware()
            asyncio.run(self.run_async())
        except KeyboardInterrupt:
            print("\n[ARSE] Executive halted by operator.")
        finally:
            if self.server and self.server._server:
                try:
                    self.server._server.close()
                except Exception:
                    pass
            if self.sentry and self.sentry.is_enabled():
                self.sentry.disarm()
            if self.patterns and self.patterns.is_active():
                self.patterns.stop("Shutdown")
            if self.drive:
                self.drive.estop()
            if self.lights:
                self.lights.set_mode("off")
            if self.audio:
                self.audio._shutdown_hardware()
            if self.battery:
                self.battery.save()
            self.yukon.reset()


if __name__ == "__main__":
    app = ConeApp()
    app.start()
