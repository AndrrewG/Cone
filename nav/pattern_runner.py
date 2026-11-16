# ==============================================================================
# ARSE — Autonomous Road Systems Engineering
# Module:      nav/pattern_runner.py
# Description: Zero-allocation 50 Hz IMU dead-reckoning holonomic pattern runner.
# ==============================================================================

import array
import gc
import math

try:
    import utime as _time
except ImportError:
    import time as _time

try:
    import uasyncio as _asyncio
except ImportError:
    import asyncio as _asyncio


def _ticks_ms():
    return _time.ticks_ms() if hasattr(_time, "ticks_ms") else int(_time.monotonic() * 1000)


def _ticks_diff(a_ms, b_ms):
    return _time.ticks_diff(a_ms, b_ms) if hasattr(_time, "ticks_diff") else (a_ms - b_ms)


def wrap_angle_180_deg(angle_deg):
    return ((angle_deg + 180.0) % 360.0) - 180.0


class PatternRunner:
    TICK_PERIOD_MS = 20
    NOMINAL_DT_S = 0.020
    MAX_VERTICES = 24

    PRESETS = {
        "indoor": {
            "max_speed_mps": 0.45,
            "max_leash_m": 1.8,
            "a_c_max_mps2": 0.75,
            "accel_mps2": 0.65,
            "alpha_dps2": 240.0,
            "default_speed_mps": 0.30,
            "default_side_m": 0.80,
        },
        "outdoor": {
            "max_speed_mps": 0.95,
            "max_leash_m": 5.5,
            "a_c_max_mps2": 1.15,
            "accel_mps2": 1.10,
            "alpha_dps2": 240.0,
            "default_speed_mps": 0.55,
            "default_side_m": 1.50,
        },
    }

    ORIENTATION_MODES = ("compass_locked", "tangent", "orbit_inward", "orbit_outward")

    def __init__(
        self,
        kinematics,
        imu,
        led_controller=None,
        range_provider=None,
        sound_callback=None,
        kp_yaw_dps_per_deg=3.0,
        kd_yaw_s=0.06,
        max_yaw_hold_dps=90.0,
    ):
        self.kin = kinematics
        self.imu = imu
        self.leds = led_controller
        self.range_provider = range_provider
        self.sound_callback = sound_callback
        self.kp_yaw_dps_per_deg = float(kp_yaw_dps_per_deg)
        self.kd_yaw_s = float(kd_yaw_s)
        self.max_yaw_hold_dps = float(max_yaw_hold_dps)

        self._wp_x_m = array.array("f", [0.0] * self.MAX_VERTICES)
        self._wp_y_m = array.array("f", [0.0] * self.MAX_VERTICES)
        self._wp_count = 0

        self.preset = "indoor"
        self.max_speed_mps = 0.45
        self.max_leash_m = 1.8
        self.a_c_max_mps2 = 0.75
        self.accel_mps2 = 0.65
        self.alpha_dps2 = 240.0

        self.local_x_m = 0.0
        self.local_y_m = 0.0
        self.psi_0_deg = 0.0
        self.psi_target_deg = 0.0
        self.cmd_vx_mps = 0.0
        self.cmd_vy_mps = 0.0
        self.cmd_omega_dps = 0.0

        self.state = "IDLE"
        self.active_routine = None
        self.orientation_mode = "compass_locked"
        self.routine_progress = 0.0
        self.stop_reason = ""

        self.side_length_m = 0.80
        self.speed_req_mps = 0.30
        self.laps = 1
        self.completed_laps = 0
        self.leg_index = 0
        self.arrival_tol_m = 0.025
        self.min_crawl_mps = 0.045
        self._last_step_ms = _ticks_ms()

    def _emit_sound(self, token):
        if self.sound_callback is not None:
            try:
                self.sound_callback(token)
            except Exception:
                pass

    def _set_leds(self, mode):
        if self.leds is not None and mode:
            try:
                self.leds.set_mode(mode)
            except Exception:
                pass

    def _read_heading_deg(self):
        if self.imu is None:
            return 0.0
        if isinstance(self.imu, dict):
            return float(self.imu.get("heading_deg", 0.0)) % 360.0
        return float(getattr(self.imu, "heading_deg", 0.0)) % 360.0

    def _read_gyro_z_dps(self):
        if self.imu is None:
            return 0.0
        if isinstance(self.imu, dict):
            g = self.imu.get("gyro_dps")
            return float(g[2]) if g and len(g) >= 3 else float(self.imu.get("gz_dps", 0.0))
        if hasattr(self.imu, "gz_dps"):
            return float(self.imu.gz_dps)
        g = getattr(self.imu, "last_gyro_dps", None)
        return float(g[2]) if (g and len(g) >= 3) else 0.0

    def apply_preset(self, preset):
        p_name = str(preset or "indoor").lower()
        if p_name not in self.PRESETS:
            raise ValueError("Invalid preset: {}".format(preset))
        cfg = self.PRESETS[p_name]
        self.preset = p_name
        self.max_speed_mps = cfg["max_speed_mps"]
        self.max_leash_m = cfg["max_leash_m"]
        self.a_c_max_mps2 = cfg["a_c_max_mps2"]
        self.accel_mps2 = cfg["accel_mps2"]
        self.alpha_dps2 = cfg["alpha_dps2"]
        return cfg

    def clamp_centripetal_speed(self, speed_req_mps, radius_m, a_c_max_mps2=None):
        v_req = max(0.0, min(self.max_speed_mps, float(speed_req_mps)))
        if radius_m is None:
            return v_req
        r_m = float(radius_m)
        if r_m <= 0.0:
            return 0.0
        ac_mps2 = self.a_c_max_mps2 if a_c_max_mps2 is None else float(a_c_max_mps2)
        v_tip_mps = math.sqrt(max(0.0, ac_mps2 * r_m))
        return v_req if v_req < v_tip_mps else v_tip_mps

    def is_active(self):
        return self.state == "RUNNING" and self.active_routine is not None

    def _anchor_origin(self):
        self.local_x_m = 0.0
        self.local_y_m = 0.0
        self.psi_0_deg = self._read_heading_deg()
        self.psi_target_deg = self.psi_0_deg
        self.cmd_vx_mps = 0.0
        self.cmd_vy_mps = 0.0
        self.cmd_omega_dps = 0.0
        self.routine_progress = 0.0
        self.stop_reason = ""
        self._last_step_ms = _ticks_ms()

    def start(self, routine, preset="indoor", params=None):
        r_name = str(routine or "").strip().lower()
        cfg = self.apply_preset(preset)
        p = params if isinstance(params, dict) else {}
        if r_name == "compass_box":
            return self.start_compass_box(
                side_length_m=p.get("side_length_m", cfg["default_side_m"]),
                laps=p.get("laps", 1),
                speed_mps=p.get("speed_mps", cfg["default_speed_mps"]),
                preset=self.preset,
                orientation_mode=p.get("orientation_mode", "compass_locked"),
            )
        raise ValueError("Unsupported routine: {}".format(routine))

    def start_compass_box(
        self,
        side_length_m=0.80,
        laps=1,
        speed_mps=None,
        preset=None,
        orientation_mode="compass_locked",
    ):
        cfg = self.apply_preset(preset) if preset is not None else self.PRESETS[self.preset]
        side_m = float(side_length_m)
        if side_m < 0.10 or side_m > 10.0:
            raise ValueError("side_length_m must be between 0.10 and 10.0 metres")
        laps_i = int(laps)
        if laps_i < 0 or laps_i > 100:
            raise ValueError("laps must be between 0 and 100")
        orient = str(orientation_mode or "compass_locked").lower()
        if orient not in self.ORIENTATION_MODES:
            raise ValueError("Invalid orientation_mode: {}".format(orientation_mode))
        raw_spd = cfg["default_speed_mps"] if speed_mps is None else float(speed_mps)
        if raw_spd <= 0.0:
            raise ValueError("speed_mps must be positive")

        gc.collect()
        self._anchor_origin()
        self._wp_x_m[0], self._wp_y_m[0] = side_m, 0.0
        self._wp_x_m[1], self._wp_y_m[1] = side_m, -side_m
        self._wp_x_m[2], self._wp_y_m[2] = 0.0, -side_m
        self._wp_x_m[3], self._wp_y_m[3] = 0.0, 0.0
        self._wp_count = 4

        self.side_length_m = side_m
        self.speed_req_mps = max(self.min_crawl_mps, min(self.max_speed_mps, raw_spd))
        self.laps = laps_i
        self.completed_laps = 0
        self.leg_index = 0
        self.orientation_mode = orient
        self.active_routine = "compass_box"
        self.state = "RUNNING"
        self._set_leds("beacon")
        self._emit_sound("ACK_MISSION_START")
        return self.get_status()

    def stop(self, reason="Operator stopped"):
        was_running = self.is_active()
        self.active_routine = None
        self.cmd_vx_mps = 0.0
        self.cmd_vy_mps = 0.0
        self.cmd_omega_dps = 0.0
        if reason == "LEASH_LIMIT_REACHED":
            self.state = "LEASH_LIMIT_REACHED"
            self.stop_reason = "LEASH_LIMIT_REACHED"
        else:
            if was_running or self.state == "RUNNING":
                self.state = "STOPPED"
            self.stop_reason = str(reason or "")
        return self.get_status()

    def _compute_target_heading_deg(self, vx_loc_mps, vy_loc_mps, cx_m=0.0, cy_m=0.0):
        mode = self.orientation_mode
        if mode == "compass_locked":
            return self.psi_0_deg
        if mode == "tangent":
            if vx_loc_mps * vx_loc_mps + vy_loc_mps * vy_loc_mps > 1e-5:
                return (self.psi_0_deg - math.degrees(math.atan2(vy_loc_mps, vx_loc_mps))) % 360.0
            return self.psi_target_deg
        dx_m = cx_m - self.local_x_m
        dy_m = cy_m - self.local_y_m
        if mode == "orbit_outward":
            dx_m, dy_m = -dx_m, -dy_m
        if dx_m * dx_m + dy_m * dy_m > 1e-5:
            return (self.psi_0_deg - math.degrees(math.atan2(dy_m, dx_m))) % 360.0
        return self.psi_target_deg

    def _step_compass_box_local_vel(self):
        idx = self.leg_index
        dx_m = self._wp_x_m[idx] - self.local_x_m
        dy_m = self._wp_y_m[idx] - self.local_y_m
        rem_m = math.sqrt(dx_m * dx_m + dy_m * dy_m)

        if rem_m <= self.arrival_tol_m:
            self.cmd_vx_mps = 0.0
            self.cmd_vy_mps = 0.0
            idx += 1
            if idx >= self._wp_count:
                self.completed_laps += 1
                if self.laps > 0 and self.completed_laps >= self.laps:
                    self.leg_index = self._wp_count
                    self.routine_progress = 1.0
                    self.active_routine = None
                    self.state = "COMPLETED"
                    self.stop_reason = ""
                    self._emit_sound("EVENT_MISSION_COMPLETE")
                    return 0.0, 0.0, False
                idx = 0
                self._emit_sound("ACK_COMMAND")
            self.leg_index = idx
            dx_m = self._wp_x_m[idx] - self.local_x_m
            dy_m = self._wp_y_m[idx] - self.local_y_m
            rem_m = math.sqrt(dx_m * dx_m + dy_m * dy_m)

        side_m = self.side_length_m if self.side_length_m > 1e-4 else 1.0
        leg_frac = min(1.0, max(0.0, 1.0 - (rem_m / side_m)))
        if self.laps > 0:
            done_legs = (self.completed_laps * self._wp_count) + idx + leg_frac
            self.routine_progress = min(1.0, done_legs / float(self.laps * self._wp_count))
        else:
            self.routine_progress = (idx + leg_frac) / float(self._wp_count)

        if rem_m <= 1e-6:
            return 0.0, 0.0, True

        v_brake_mps = math.sqrt(2.0 * self.accel_mps2 * rem_m)
        spd_mps = self.speed_req_mps if self.speed_req_mps < v_brake_mps else v_brake_mps
        spd_mps = max(self.min_crawl_mps, min(self.max_speed_mps, spd_mps))
        inv_r = spd_mps / rem_m
        return dx_m * inv_r, dy_m * inv_r, True

    def step(self, dt_s=None):
        now_ms = _ticks_ms()
        dt = (
            max(0.002, min(0.100, _ticks_diff(now_ms, self._last_step_ms) * 0.001))
            if dt_s is None
            else max(0.001, min(0.200, float(dt_s)))
        )
        self._last_step_ms = now_ms

        if not self.is_active():
            return 0.0, 0.0, 0.0, False

        if self.active_routine == "compass_box":
            vx_loc_mps, vy_loc_mps, keep_going = self._step_compass_box_local_vel()
            if not keep_going:
                self.cmd_vx_mps = 0.0
                self.cmd_vy_mps = 0.0
                self.cmd_omega_dps = 0.0
                return 0.0, 0.0, 0.0, False
            half_s = 0.5 * self.side_length_m
            self.psi_target_deg = self._compute_target_heading_deg(vx_loc_mps, vy_loc_mps, half_s, -half_s)
        else:
            self.stop("Unknown active routine")
            return 0.0, 0.0, 0.0, False

        psi_deg = self._read_heading_deg()
        theta_ccw_rad = math.radians(wrap_angle_180_deg(self.psi_0_deg - psi_deg))
        cos_t = math.cos(theta_ccw_rad)
        sin_t = math.sin(theta_ccw_rad)

        des_vx_mps = vx_loc_mps * cos_t + vy_loc_mps * sin_t
        des_vy_mps = -vx_loc_mps * sin_t + vy_loc_mps * cos_t

        err_cw_deg = wrap_angle_180_deg(self.psi_target_deg - psi_deg)
        des_omega_dps = (-self.kp_yaw_dps_per_deg * err_cw_deg) - (self.kd_yaw_s * self._read_gyro_z_dps())
        des_omega_dps = max(-self.max_yaw_hold_dps, min(self.max_yaw_hold_dps, des_omega_dps))

        dvx = des_vx_mps - self.cmd_vx_mps
        dvy = des_vy_mps - self.cmd_vy_mps
        dv = math.sqrt(dvx * dvx + dvy * dvy)
        max_dv = self.accel_mps2 * dt
        if dv <= max_dv or dv < 1e-6:
            self.cmd_vx_mps, self.cmd_vy_mps = des_vx_mps, des_vy_mps
        else:
            scl = max_dv / dv
            self.cmd_vx_mps += dvx * scl
            self.cmd_vy_mps += dvy * scl

        cmd_spd = math.sqrt(self.cmd_vx_mps * self.cmd_vx_mps + self.cmd_vy_mps * self.cmd_vy_mps)
        if cmd_spd > self.max_speed_mps and cmd_spd > 1e-6:
            cap_scl = self.max_speed_mps / cmd_spd
            self.cmd_vx_mps *= cap_scl
            self.cmd_vy_mps *= cap_scl

        dom = des_omega_dps - self.cmd_omega_dps
        max_dom = self.alpha_dps2 * dt
        if abs(dom) <= max_dom:
            self.cmd_omega_dps = des_omega_dps
        else:
            self.cmd_omega_dps += max_dom if dom > 0.0 else -max_dom

        inv_vmax = 1.0 / max(0.1, float(getattr(self.kin, "max_speed", 1.297)))
        vx_n = self.cmd_vx_mps * inv_vmax
        vy_n = self.cmd_vy_mps * inv_vmax
        rw_n = self.kin.R * math.radians(self.cmd_omega_dps) * inv_vmax
        a0, a1, a2 = self.kin.angles[0], self.kin.angles[1], self.kin.angles[2]

        w0 = -math.sin(a0) * vx_n + math.cos(a0) * vy_n + rw_n
        w1 = -math.sin(a1) * vx_n + math.cos(a1) * vy_n + rw_n
        w2 = -math.sin(a2) * vx_n + math.cos(a2) * vy_n + rw_n

        sat_scale = 1.0
        m_abs = max(abs(w0), abs(w1), abs(w2))
        if m_abs > 1.0:
            sat_scale = 1.0 / m_abs
            w0 *= sat_scale
            w1 *= sat_scale
            w2 *= sat_scale

        act_vx_mps = self.cmd_vx_mps * sat_scale
        act_vy_mps = self.cmd_vy_mps * sat_scale
        self.local_x_m += (act_vx_mps * cos_t - act_vy_mps * sin_t) * dt
        self.local_y_m += (act_vx_mps * sin_t + act_vy_mps * cos_t) * dt

        if math.sqrt(self.local_x_m * self.local_x_m + self.local_y_m * self.local_y_m) >= self.max_leash_m:
            self.stop("LEASH_LIMIT_REACHED")
            self._emit_sound("ERR_SYSTEM_FAULT")
            return 0.0, 0.0, 0.0, False

        return w0, w1, w2, True

    async def run_loop(self, on_wheels=None):
        while True:
            w0, w1, w2, active = self.step()
            if on_wheels is not None:
                on_wheels(w0, w1, w2, active)
            if hasattr(_asyncio, "sleep_ms"):
                await _asyncio.sleep_ms(self.TICK_PERIOD_MS)
            else:
                await _asyncio.sleep(self.NOMINAL_DT_S)

    def get_status(self):
        return {
            "state": self.state,
            "active_routine": self.active_routine,
            "preset": self.preset,
            "progress": round(self.routine_progress, 3),
            "local_x_m": round(self.local_x_m, 3),
            "local_y_m": round(self.local_y_m, 3),
            "psi_0_deg": round(self.psi_0_deg, 1),
            "psi_target_deg": round(self.psi_target_deg, 1),
            "max_speed_mps": self.max_speed_mps,
            "max_leash_m": self.max_leash_m,
            "lap": self.completed_laps,
            "laps": self.laps,
            "leg_index": self.leg_index,
            "stop_reason": self.stop_reason,
        }
