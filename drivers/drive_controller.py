# ==============================================================================
# ARSE — Autonomous Road Systems Engineering
# Proprietary & Confidential — Division of Conical Mobility & Traffic Robotics
# ------------------------------------------------------------------------------
# Module:      drivers/drive_controller.py
# Platform:    ARSE Mk.I Autonomous Traffic Cone (Pimoroni Yukon RP2040)
# Description: Holonomic 3-wheel & single-channel bench motor controller with
#              slew-rate ramping, arming interlock, and Wi-Fi deadman watchdog.
# Copyright (c) 2026 Autonomous Road Systems Engineering (ARSE).
# ==============================================================================

import time


class DriveController:
    def __init__(
        self,
        yukon,
        motor_mod1,
        motor_mod2,
        kinematics,
        speed_modes,
        default_mode="indoor",
        slew_per_sec=2.8,
        deadman_ms=650,
        motor_polarity=(1, 1, 1),
    ):
        self.yukon = yukon
        self.m1 = motor_mod1
        self.m2 = motor_mod2
        self.kin = kinematics
        self.speed_modes = speed_modes
        self.speed_mode = default_mode if default_mode in speed_modes else "indoor"
        self.slew_per_sec = float(slew_per_sec)
        self.deadman_ms = int(deadman_ms)
        self._pol0 = -1.0 if motor_polarity[0] < 0 else 1.0
        self._pol1 = -1.0 if motor_polarity[1] < 0 else 1.0
        self._pol2 = -1.0 if motor_polarity[2] < 0 else 1.0

        self.armed = False
        self.estop_latched = False

        # Holonomic manual twist [-1..1]
        self._man_vx = 0.0
        self._man_vy = 0.0
        self._man_omega = 0.0
        self._last_man_ms = 0

        # Direct single-motor bench state
        self._bench_ch = 1           # 1=Slot1.M1, 2=Slot1.M2, 3=Slot2.M1, 0=all
        self._bench_spd = 0.0
        self._bench_until_ms = 0

        # Autonomous twist [-1..1]
        self._auto_vx = 0.0
        self._auto_vy = 0.0
        self._auto_omega = 0.0
        self._auto_active = False

        # Pre-scaled 50 Hz routine wheel commands [-1..1]
        self._routine_w0 = 0.0
        self._routine_w1 = 0.0
        self._routine_w2 = 0.0
        self._routine_active = False

        self.target_wheels = [0.0, 0.0, 0.0]
        self.current_wheels = [0.0, 0.0, 0.0]
        self.active_source = "idle"
        self.drv_temp_c = 0.0
        self.drv_fault = False
        self._last_tick_ms = time.ticks_ms()

    @property
    def speed_scale(self):
        return self.speed_modes.get(self.speed_mode, 0.35)

    def set_armed(self, armed):
        self.armed = bool(armed)
        if self.armed:
            self.estop_latched = False
        else:
            self._bench_spd = 0.0
            self._routine_active = False
            self._routine_w0 = 0.0
            self._routine_w1 = 0.0
            self._routine_w2 = 0.0
            self._halt_hardware()
        return self.get_status()

    def set_speed_mode(self, mode):
        m = str(mode).lower()
        if m not in self.speed_modes:
            raise ValueError("Invalid speed mode: {}".format(m))
        self.speed_mode = m
        return self.get_status()

    def estop(self):
        self.armed = False
        self.estop_latched = True
        self._man_vx = 0.0
        self._man_vy = 0.0
        self._man_omega = 0.0
        self._bench_spd = 0.0
        self._bench_until_ms = 0
        self._auto_vx = 0.0
        self._auto_vy = 0.0
        self._auto_omega = 0.0
        self._auto_active = False
        self._routine_w0 = 0.0
        self._routine_w1 = 0.0
        self._routine_w2 = 0.0
        self._routine_active = False
        self.target_wheels[0] = 0.0
        self.target_wheels[1] = 0.0
        self.target_wheels[2] = 0.0
        self._halt_hardware()
        return self.get_status()

    def _halt_hardware(self):
        self.current_wheels = [0.0, 0.0, 0.0]
        try:
            self.m1.motor1.stop()
            self.m1.motor2.stop()
            self.m2.motor1.stop()
            self.m1.disable()
            self.m2.disable()
        except Exception:
            pass

    def set_bench_motor(self, channel, speed, duration_ms=0):
        ch = int(channel)
        if ch not in (0, 1, 2, 3):
            raise ValueError("Channel must be 1, 2, 3, or 0 (all)")
        spd = max(-1.0, min(1.0, float(speed)))
        self._bench_ch = ch
        self._bench_spd = spd
        now = time.ticks_ms()
        win = int(duration_ms) if int(duration_ms) > 0 else self.deadman_ms
        self._bench_until_ms = time.ticks_add(now, win) if abs(spd) > 0.005 else now
        return abs(spd) > 0.01

    def set_manual_twist(self, vx, vy, omega):
        self._man_vx = max(-1.0, min(1.0, float(vx)))
        self._man_vy = max(-1.0, min(1.0, float(vy)))
        self._man_omega = max(-1.0, min(1.0, float(omega)))
        self._last_man_ms = time.ticks_ms()
        return (
            abs(self._man_vx) > 0.05
            or abs(self._man_vy) > 0.05
            or abs(self._man_omega) > 0.05
        )

    def set_auto_twist(self, vx, vy, omega, active=True):
        self._auto_active = bool(active)
        if active:
            self._auto_vx = max(-1.0, min(1.0, float(vx)))
            self._auto_vy = max(-1.0, min(1.0, float(vy)))
            self._auto_omega = max(-1.0, min(1.0, float(omega)))
        else:
            self._auto_vx = 0.0
            self._auto_vy = 0.0
            self._auto_omega = 0.0

    def set_routine_wheels(self, w0, w1, w2, active=True):
        self._routine_active = bool(active)
        if active:
            self._routine_w0 = max(-1.0, min(1.0, float(w0)))
            self._routine_w1 = max(-1.0, min(1.0, float(w1)))
            self._routine_w2 = max(-1.0, min(1.0, float(w2)))
        else:
            self._routine_w0 = 0.0
            self._routine_w1 = 0.0
            self._routine_w2 = 0.0

    def sample_driver_diagnostics(self):
        try:
            t1 = self.m1.read_temperature()
            t2 = self.m2.read_temperature()
            self.drv_temp_c = max(t1, t2)
            if self.m1.is_enabled() or self.m2.is_enabled():
                self.drv_fault = bool(self.m1.read_fault() or self.m2.read_fault())
            else:
                self.drv_fault = False
        except Exception:
            pass

    def tick(self, safety_ok=True):
        now = time.ticks_ms()
        dt = max(0.005, min(0.25, time.ticks_diff(now, self._last_tick_ms) / 1000.0))
        self._last_tick_ms = now

        # Check bench motor window
        bench_active = (
            abs(self._bench_spd) > 0.005
            and time.ticks_diff(self._bench_until_ms, now) > 0
        )
        if not bench_active:
            self._bench_spd = 0.0

        # Check manual joystick deadman window
        man_fresh = time.ticks_diff(now, self._last_man_ms) <= self.deadman_ms
        if not man_fresh:
            self._man_vx = 0.0
            self._man_vy = 0.0
            self._man_omega = 0.0

        has_man = man_fresh and (
            abs(self._man_vx) > 0.01 or abs(self._man_vy) > 0.01 or abs(self._man_omega) > 0.01
        )

        tw = self.target_wheels
        if bench_active:
            self.active_source = "bench"
            if self._bench_ch == 0:
                tw[0] = self._bench_spd
                tw[1] = self._bench_spd
                tw[2] = self._bench_spd
            else:
                tw[0] = 0.0
                tw[1] = 0.0
                tw[2] = 0.0
                tw[self._bench_ch - 1] = self._bench_spd
        elif has_man:
            self.active_source = "manual"
            omega_rad = (self._man_omega / self.kin.R) if self.kin.R > 0.001 else self._man_omega
            w1, w2, w3 = self.kin.compute_wheel_speeds(self._man_vx, self._man_vy, omega_rad)
            cap = self.speed_scale
            tw[0] = w1 * cap
            tw[1] = w2 * cap
            tw[2] = w3 * cap
        elif self._routine_active:
            self.active_source = "routine"
            tw[0] = self._routine_w0
            tw[1] = self._routine_w1
            tw[2] = self._routine_w2
        elif self._auto_active:
            self.active_source = "auto"
            omega_rad = (self._auto_omega / self.kin.R) if self.kin.R > 0.001 else self._auto_omega
            w1, w2, w3 = self.kin.compute_wheel_speeds(self._auto_vx, self._auto_vy, omega_rad)
            cap = self.speed_scale
            tw[0] = w1 * cap
            tw[1] = w2 * cap
            tw[2] = w3 * cap
        else:
            self.active_source = "idle"
            tw[0] = 0.0
            tw[1] = 0.0
            tw[2] = 0.0

        if not safety_ok or not self.yukon.is_main_output_enabled():
            if self.armed and not safety_ok:
                self.armed = False
            self._halt_hardware()
            return

        if not self.armed:
            if self.m1.is_enabled() or self.m2.is_enabled():
                self._halt_hardware()
            return

        max_step = self.slew_per_sec * dt
        for i in range(3):
            diff = self.target_wheels[i] - self.current_wheels[i]
            if abs(diff) <= max_step:
                self.current_wheels[i] = self.target_wheels[i]
            elif diff > 0:
                self.current_wheels[i] += max_step
            else:
                self.current_wheels[i] -= max_step

        need_m1 = (
            abs(self.current_wheels[0]) > 0.008
            or abs(self.current_wheels[1]) > 0.008
            or abs(self.target_wheels[0]) > 0.008
            or abs(self.target_wheels[1]) > 0.008
        )
        need_m2 = abs(self.current_wheels[2]) > 0.008 or abs(self.target_wheels[2]) > 0.008

        if need_m1:
            if not self.m1.is_enabled():
                self.m1.enable()
            self.m1.motor1.speed(self.current_wheels[0] * self._pol0)
            self.m1.motor2.speed(self.current_wheels[1] * self._pol1)
        elif self.m1.is_enabled():
            self.m1.motor1.stop()
            self.m1.motor2.stop()
            self.m1.disable()

        if need_m2:
            if not self.m2.is_enabled():
                self.m2.enable()
            self.m2.motor1.speed(self.current_wheels[2] * self._pol2)
        elif self.m2.is_enabled():
            self.m2.motor1.stop()
            self.m2.disable()

    def get_status(self):
        return {
            "armed": self.armed,
            "estop": self.estop_latched,
            "speed_mode": self.speed_mode,
            "speed_cap_pct": int(round(self.speed_scale * 100)),
            "source": self.active_source,
            "bench_channel": self._bench_ch,
            "target_wheels": [round(w, 2) for w in self.target_wheels],
            "actual_wheels": [round(w, 2) for w in self.current_wheels],
            "drv_temp_c": round(self.drv_temp_c, 1),
            "drv_fault": self.drv_fault,
        }
