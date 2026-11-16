# ==============================================================================
# ARSE — Autonomous Road Systems Engineering
# Proprietary & Confidential — Division of Conical Mobility & Traffic Robotics
# ------------------------------------------------------------------------------
# Module:      drivers/sentry.py
# Platform:    ARSE Mk.I Autonomous Traffic Cone (Pimoroni Yukon RP2040)
# Description: Anti-theft IMU Sentry Mode controller. Gates main e-Fuse power
#              rail & Wi-Fi PM for low-power watch, detects unauthorised cone
#              displacement via ICM-20948, and triggers Police strobe + siren.
# Copyright (c) 2026 Autonomous Road Systems Engineering (ARSE).
# ==============================================================================

import math
import time


class SentryGuard:
    STATE_OFF = "OFF"
    STATE_ARMING = "ARMING"
    STATE_WATCH = "WATCH"
    STATE_ALARM = "ALARM"

    def __init__(
        self,
        yukon,
        imu,
        lights,
        audio,
        drive,
        nav,
        wifi,
        battery,
        presets=None,
        default_sensitivity="medium",
        arm_delay_ms=3000,
        alarm_hold_ms=12000,
        normal_idle_amps=0.045,
        low_power_amps=0.025,
    ):
        self.yukon = yukon
        self.imu = imu
        self.lights = lights
        self.audio = audio
        self.drive = drive
        self.nav = nav
        self.wifi = wifi
        self.battery = battery

        self.presets = presets or {
            "high": (0.06, 10.0),
            "medium": (0.13, 18.0),
            "low": (0.24, 32.0),
        }
        self.sensitivity = default_sensitivity if default_sensitivity in self.presets else "medium"
        self.arm_delay_ms = int(arm_delay_ms)
        self.alarm_hold_ms = int(alarm_hold_ms)
        self.normal_idle_amps = float(normal_idle_amps)
        self.low_power_amps = float(low_power_amps)

        self.state = self.STATE_OFF
        self.alarm_count = 0
        self.last_trigger = ""
        self.last_accel_delta_g = 0.0
        self.last_gyro_dps = 0.0

        self._ref_accel = (0.0, 0.0, 1.0)
        self._prev_alarm_accel = (0.0, 0.0, 1.0)
        self._state_until_ms = 0
        self._motion_hits = 0
        self._led_toggle = False
        self._saved_led_brightness = 0.4
        self._saved_audio_vol = None

    def _set_board_leds(self, a_on, b_on):
        try:
            self.yukon.set_led("A", bool(a_on))
            self.yukon.set_led("B", bool(b_on))
        except Exception:
            pass

    def is_enabled(self):
        return self.state != self.STATE_OFF

    def is_low_power_watch(self):
        return self.state == self.STATE_WATCH

    def set_sensitivity(self, level):
        lvl = str(level).lower()
        if lvl not in self.presets:
            raise ValueError("Invalid sensitivity: {}".format(lvl))
        self.sensitivity = lvl
        return self.get_status()

    def arm(self):
        if not self.imu:
            return {"ok": False, "error": "ICM-20948 IMU offline — cannot arm Sentry"}

        # Safely halt traction motors and pause any active autonomous route
        if self.drive:
            self.drive.estop()
        if self.nav and self.nav.is_active():
            self.nav.pause("Sentry Mode armed")

        self._saved_led_brightness = self.lights.brightness if self.lights else 0.4
        self._motion_hits = 0
        self.last_accel_delta_g = 0.0
        self.last_gyro_dps = 0.0
        self.state = self.STATE_ARMING
        self._state_until_ms = time.ticks_add(time.ticks_ms(), self.arm_delay_ms)

        # Short arming confirmation chirp while rail is still active
        if self.audio and self.yukon.is_main_output_enabled():
            try:
                self.audio.play("chirp")
            except Exception as e:
                print("[ARSE-SENTRY] Arming chirp error:", e)
        if self.lights:
            self.lights.set_mode("off")

        return {"ok": True, "sentry": self.get_status()}

    def disarm(self):
        was_active = self.state != self.STATE_OFF
        self.state = self.STATE_OFF
        self._motion_hits = 0
        self._set_board_leds(False, False)

        # Restore normal Wi-Fi responsiveness & Coulomb idle current baseline
        if self.wifi:
            self.wifi.set_power_save(False)
        if self.battery:
            self.battery.logic_idle_amps = self.normal_idle_amps

        # Silence alarm actuators and restore main power rail for normal operation
        if self.audio:
            self.audio.stop()
            if self._saved_audio_vol is not None:
                self.audio.set_volume(self._saved_audio_vol)
                self._saved_audio_vol = None
        if self.lights:
            self.lights.set_mode("off", brightness=self._saved_led_brightness)

        if was_active and not self.yukon.is_main_output_enabled():
            b_st = self.battery.get_status() if self.battery else {}
            if b_st.get("battery_present", False) and not b_st.get("is_critical", False):
                try:
                    self.yukon.enable_main_output()
                except Exception:
                    pass

        return {"ok": True, "sentry": self.get_status()}

    def _enter_low_power_watch(self):
        # Power down LEDs, Class-D amp, and main high-power e-Fuse rail (0 mA module draw)
        # BEFORE snapshotting resting gravity so speaker vibration cannot corrupt baseline.
        self._set_board_leds(False, False)
        was_alarm = (self.state == self.STATE_ALARM)
        if self.audio:
            self.audio._shutdown_hardware()
            if self._saved_audio_vol is not None:
                self.audio.volume = self._saved_audio_vol
                self.audio.gain_db = self.audio._calc_gain_db(self.audio.volume)
                self._saved_audio_vol = None
        if self.lights:
            self.lights.set_mode("off", brightness=self._saved_led_brightness)

        if self.yukon.is_main_output_enabled():
            try:
                self.yukon.disable_main_output()
            except Exception:
                pass

        if was_alarm:
            time.sleep_ms(40)

        # Snapshot resting gravity vector (handles sloped driveways / kerbs)
        if self.imu:
            try:
                acc, _ = self.imu.read_motion_fast()
                self._ref_accel = acc
            except Exception:
                self._ref_accel = self.imu.last_accel_g

        if self.wifi:
            self.wifi.set_power_save(True)
        if self.battery:
            self.battery.logic_idle_amps = self.low_power_amps

        self._motion_hits = 0
        self.last_accel_delta_g = 0.0
        self.last_gyro_dps = 0.0
        self.state = self.STATE_WATCH

    def _trigger_police_alarm(self, da_g, gyro_dps):
        now = time.ticks_ms()
        self.state = self.STATE_ALARM
        self.alarm_count += 1
        self._state_until_ms = time.ticks_add(now, self.alarm_hold_ms)
        self._prev_alarm_accel = self.imu.last_accel_g if self.imu else (0.0, 0.0, 1.0)
        self.last_trigger = "Tamper {:.2f}g / {:.0f} deg/s".format(da_g, gyro_dps)
        print("[ARSE-SENTRY] ALARM #{}: {}".format(self.alarm_count, self.last_trigger))
        self._set_board_leds(True, False)

        if self.wifi:
            self.wifi.set_power_save(False)
        if self.battery:
            self.battery.logic_idle_amps = self.normal_idle_amps

        # Re-energize high-power rail for Slot 4 Police Strobe & Slot 6 25W Siren
        b_st = self.battery.get_status() if self.battery else {}
        if (
            not self.yukon.is_main_output_enabled()
            and b_st.get("battery_present", False)
            and not b_st.get("is_critical", False)
        ):
            try:
                self.yukon.enable_main_output()
                time.sleep_ms(30)
            except Exception:
                pass

        if self.yukon.is_main_output_enabled():
            if self.lights:
                self.lights.set_mode("police", brightness=0.90)
            if self.audio:
                # If operator had muted slider to 0%, temporarily wake to 35% so siren is audible
                if self.audio.volume <= 0.01:
                    self._saved_audio_vol = self.audio.volume
                    self.audio.set_volume(0.35)
                try:
                    self.audio.play("siren")
                except Exception:
                    pass

    def _measure_tamper_motion(self, use_fast_read=False):
        if not self.imu:
            return 0.0, 0.0, (False, False)

        if use_fast_read:
            acc, gyr = self.imu.read_motion_fast()
        else:
            acc = self.imu.last_accel_g
            gyr = self.imu.last_gyro_dps

        dx = acc[0] - self._ref_accel[0]
        dy = acc[1] - self._ref_accel[1]
        dz = acc[2] - self._ref_accel[2]
        da_g = math.sqrt(dx * dx + dy * dy + dz * dz)
        gyro_dps = math.sqrt(gyr[0] * gyr[0] + gyr[1] * gyr[1] + gyr[2] * gyr[2])

        self.last_accel_delta_g = da_g
        self.last_gyro_dps = gyro_dps

        thr_g, thr_dps = self.presets.get(self.sensitivity, (0.13, 18.0))
        exceeded = (da_g >= thr_g) or (gyro_dps >= thr_dps)
        hard_snatch = (da_g >= thr_g * 2.4) or (gyro_dps >= thr_dps * 2.4)
        return da_g, gyro_dps, (exceeded, hard_snatch)

    def update(self):
        if self.state == self.STATE_OFF:
            return

        now = time.ticks_ms()

        if self.state == self.STATE_ARMING:
            self._led_toggle = not self._led_toggle
            self._set_board_leds(self._led_toggle, False)
            if time.ticks_diff(now, self._state_until_ms) >= 0:
                self._enter_low_power_watch()
            return

        if self.state == self.STATE_WATCH:
            try:
                da_g, gyro_dps, (exceeded, hard_snatch) = self._measure_tamper_motion(use_fast_read=True)
            except Exception:
                return

            if hard_snatch:
                self._trigger_police_alarm(da_g, gyro_dps)
            elif exceeded:
                self._motion_hits += 1
                if self._motion_hits >= 2:
                    self._trigger_police_alarm(da_g, gyro_dps)
            else:
                self._motion_hits = 0
            return

        if self.state == self.STATE_ALARM:
            self._led_toggle = not self._led_toggle
            self._set_board_leds(self._led_toggle, not self._led_toggle)

            # Keep siren looping continuously throughout ALARM state
            if self.audio and self.yukon.is_main_output_enabled():
                if self.audio.active_sound != "siren":
                    try:
                        self.audio.play("siren")
                    except Exception:
                        pass

            if self.lights and self.lights.mode != "police" and self.yukon.is_main_output_enabled():
                self.lights.set_mode("police", brightness=0.90)

            # Extend alarm hold timer if culprit is still actively moving/carrying the cone
            if self.imu:
                acc = self.imu.last_accel_g
                gyr = self.imu.last_gyro_dps
                ddx = acc[0] - self._prev_alarm_accel[0]
                ddy = acc[1] - self._prev_alarm_accel[1]
                ddz = acc[2] - self._prev_alarm_accel[2]
                dyn_g = math.sqrt(ddx * ddx + ddy * ddy + ddz * ddz)
                gyro_dps = math.sqrt(gyr[0] * gyr[0] + gyr[1] * gyr[1] + gyr[2] * gyr[2])
                self._prev_alarm_accel = acc
                self.last_accel_delta_g = dyn_g
                self.last_gyro_dps = gyro_dps
                thr_g, thr_dps = self.presets.get(self.sensitivity, (0.13, 18.0))
                if dyn_g >= max(thr_g, 0.12) or gyro_dps >= max(thr_dps, 12.0):
                    self._state_until_ms = time.ticks_add(now, self.alarm_hold_ms)

            # Once stationary for full hold window, auto-rearm back to low-power WATCH
            if time.ticks_diff(now, self._state_until_ms) >= 0:
                print("[ARSE-SENTRY] Motion ceased — returning to low-power WATCH.")
                self._enter_low_power_watch()

    def get_status(self):
        rem_s = 0.0
        if self.state in (self.STATE_ARMING, self.STATE_ALARM):
            rem_ms = time.ticks_diff(self._state_until_ms, time.ticks_ms())
            if rem_ms > 0:
                rem_s = round(rem_ms / 1000.0, 1)
        return {
            "state": self.state,
            "sensitivity": self.sensitivity,
            "alarm_count": self.alarm_count,
            "last_trigger": self.last_trigger,
            "accel_delta_g": round(self.last_accel_delta_g, 3),
            "gyro_dps": round(self.last_gyro_dps, 1),
            "timer_s": rem_s,
            "low_power_active": (self.state == self.STATE_WATCH),
        }
