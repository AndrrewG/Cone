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


def _finite_float(val, name):
    if isinstance(val, bool) or not isinstance(val, (int, float)):
        raise ValueError("{} must be a finite number".format(name))
    f = float(val)
    if math.isnan(f) or math.isinf(f):
        raise ValueError("{} must be a finite number".format(name))
    return f


def _strict_int(val, name):
    if isinstance(val, bool) or not isinstance(val, int):
        raise ValueError("{} must be an integer".format(name))
    return int(val)


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
            "default_radius_m": 0.50,
        },
        "outdoor": {
            "max_speed_mps": 0.95,
            "max_leash_m": 5.5,
            "a_c_max_mps2": 1.15,
            "accel_mps2": 1.10,
            "alpha_dps2": 240.0,
            "default_speed_mps": 0.55,
            "default_side_m": 1.50,
            "default_radius_m": 1.20,
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
        max_yaw_hold_dps=180.0,
        kr_radial_per_s=1.8,
    ):
        self.kin = kinematics
        self.imu = imu
        self.leds = led_controller
        self.range_provider = range_provider
        self.sound_callback = sound_callback
        self.kp_yaw_dps_per_deg = _finite_float(kp_yaw_dps_per_deg, "kp_yaw_dps_per_deg")
        self.kd_yaw_s = _finite_float(kd_yaw_s, "kd_yaw_s")
        self.max_yaw_hold_dps = _finite_float(max_yaw_hold_dps, "max_yaw_hold_dps")
        self.kr_radial_per_s = _finite_float(kr_radial_per_s, "kr_radial_per_s")

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
        self.circle_radius_m = 0.50
        self.circle_direction = "ccw"
        self._circle_dir_sign = 1.0
        self._circle_cx_m = 0.0
        self._circle_cy_m = 0.50
        self._circle_prev_phi_rad = -0.5 * math.pi
        self._circle_accum_rad = 0.0

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

    def _resolve_preset_name(self, preset):
        if preset is None:
            p_name = "indoor"
        elif isinstance(preset, str):
            p_name = preset.strip().lower()
        else:
            raise ValueError("Invalid preset: {}".format(preset))
        if p_name not in self.PRESETS:
            raise ValueError("Invalid preset: {}".format(preset))
        return p_name

    def apply_preset(self, preset):
        p_name = self._resolve_preset_name(preset)
        cfg = self.PRESETS[p_name]
        self.preset = p_name
        self.max_speed_mps = cfg["max_speed_mps"]
        self.max_leash_m = cfg["max_leash_m"]
        self.a_c_max_mps2 = cfg["a_c_max_mps2"]
        self.accel_mps2 = cfg["accel_mps2"]
        self.alpha_dps2 = cfg["alpha_dps2"]
        return cfg

    def clamp_centripetal_speed(self, speed_req_mps, radius_m, a_c_max_mps2=None):
        raw_v = _finite_float(speed_req_mps, "speed_req_mps")
        ac_mps2 = (
            self.a_c_max_mps2
            if a_c_max_mps2 is None
            else _finite_float(a_c_max_mps2, "a_c_max_mps2")
        )
        if ac_mps2 < 0.0:
            raise ValueError("a_c_max_mps2 must be non-negative")
        v_req = max(0.0, min(self.max_speed_mps, raw_v))
        if radius_m is None:
            return v_req
        r_m = _finite_float(radius_m, "radius_m")
        if r_m <= 0.0:
            return 0.0
        v_tip_mps = math.sqrt(ac_mps2 * r_m)
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
        if not isinstance(routine, str):
            raise ValueError("Unsupported routine: {}".format(routine))
        r_name = routine.strip().lower()
        if params is not None and not isinstance(params, dict):
            raise ValueError("params must be a dictionary")
        p_name = self._resolve_preset_name(preset)
        cfg = self.PRESETS[p_name]
        p = params if isinstance(params, dict) else {}
        if r_name == "compass_box":
            return self.start_compass_box(
                side_length_m=p.get("side_length_m", cfg["default_side_m"]),
                laps=p.get("laps", 1),
                speed_mps=p.get("speed_mps", cfg["default_speed_mps"]),
                preset=p_name,
                orientation_mode=p.get("orientation_mode", "compass_locked"),
            )
        if r_name == "circle":
            return self.start_circle(
                radius_m=p.get("radius_m", cfg["default_radius_m"]),
                laps=p.get("laps", 1),
                direction=p.get("direction", "ccw"),
                speed_mps=p.get("speed_mps", cfg["default_speed_mps"]),
                orientation_mode=p.get("orientation_mode", "tangent"),
                preset=p_name,
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
        p_name = self.preset if preset is None else self._resolve_preset_name(preset)
        cfg = self.PRESETS[p_name]
        side_m = _finite_float(side_length_m, "side_length_m")
        if side_m < 0.10 or side_m > 10.0:
            raise ValueError("side_length_m must be between 0.10 and 10.0 metres")
        laps_i = _strict_int(laps, "laps")
        if laps_i < 0 or laps_i > 100:
            raise ValueError("laps must be between 0 and 100")
        if not isinstance(orientation_mode, str):
            raise ValueError("Invalid orientation_mode: {}".format(orientation_mode))
        orient = orientation_mode.strip().lower()
        if orient not in self.ORIENTATION_MODES:
            raise ValueError("Invalid orientation_mode: {}".format(orientation_mode))
        raw_spd = cfg["default_speed_mps"] if speed_mps is None else _finite_float(speed_mps, "speed_mps")
        if raw_spd <= 0.0:
            raise ValueError("speed_mps must be positive")

        if preset is not None:
            self.apply_preset(p_name)
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

    def start_circle(
        self,
        radius_m=None,
        laps=1,
        direction="ccw",
        speed_mps=None,
        orientation_mode="tangent",
        preset=None,
    ):
        p_name = self.preset if preset is None else self._resolve_preset_name(preset)
        cfg = self.PRESETS[p_name]
        r_m = (
            cfg["default_radius_m"]
            if radius_m is None
            else _finite_float(radius_m, "radius_m")
        )
        if r_m < 0.05 or r_m > 10.0:
            raise ValueError("radius_m must be between 0.05 and 10.0 metres")
        laps_i = _strict_int(laps, "laps")
        if laps_i < 0 or laps_i > 100:
            raise ValueError("laps must be between 0 and 100")
        if not isinstance(direction, str):
            raise ValueError("direction must be 'cw' or 'ccw'")
        dir_s = direction.strip().lower()
        if dir_s not in ("cw", "ccw"):
            raise ValueError("direction must be 'cw' or 'ccw'")
        if not isinstance(orientation_mode, str):
            raise ValueError("Invalid orientation_mode: {}".format(orientation_mode))
        orient = orientation_mode.strip().lower()
        if orient not in self.ORIENTATION_MODES:
            raise ValueError("Invalid orientation_mode: {}".format(orientation_mode))
        raw_spd = (
            cfg["default_speed_mps"]
            if speed_mps is None
            else _finite_float(speed_mps, "speed_mps")
        )
        if raw_spd <= 0.0:
            raise ValueError("speed_mps must be positive")

        if preset is not None:
            self.apply_preset(p_name)
        gc.collect()
        self._anchor_origin()

        dir_sign = 1.0 if dir_s == "ccw" else -1.0
        self.circle_radius_m = r_m
        self.circle_direction = dir_s
        self._circle_dir_sign = dir_sign
        self._circle_cx_m = 0.0
        self._circle_cy_m = dir_sign * r_m
        self._circle_prev_phi_rad = math.atan2(-dir_sign * r_m, 0.0)
        self._circle_accum_rad = 0.0

        self.speed_req_mps = self.clamp_centripetal_speed(raw_spd, r_m)
        self.laps = laps_i
        self.completed_laps = 0
        self.leg_index = 0
        self.orientation_mode = orient
        self.psi_target_deg = self._compute_target_heading_deg(
            1.0, 0.0, self._circle_cx_m, self._circle_cy_m
        )
        self.active_routine = "circle"
        self.state = "RUNNING"
        self._set_leds("pulse")
        self._emit_sound("ACK_MISSION_START")
        return self.get_status()

    compass_box = start_compass_box
    circle = start_circle

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
            if vx_loc_mps * vx_loc_mps + vy_loc_mps * vy_loc_mps > 1e-9:
                return (self.psi_0_deg - math.degrees(math.atan2(vy_loc_mps, vx_loc_mps))) % 360.0
            return self.psi_target_deg
        dx_m = cx_m - self.local_x_m
        dy_m = cy_m - self.local_y_m
        if mode == "orbit_outward":
            dx_m, dy_m = -dx_m, -dy_m
        if dx_m * dx_m + dy_m * dy_m > 1e-9:
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

    def _step_circle_local_vel(self):
        rx_m = self.local_x_m - self._circle_cx_m
        ry_m = self.local_y_m - self._circle_cy_m
        r_actual_m = math.sqrt(rx_m * rx_m + ry_m * ry_m)

        two_pi = 2.0 * math.pi
        if r_actual_m > 1e-5:
            phi_rad = math.atan2(ry_m, rx_m)
            dphi_ccw_rad = ((phi_rad - self._circle_prev_phi_rad + math.pi) % two_pi) - math.pi
            self._circle_prev_phi_rad = phi_rad
            accum_rad = self._circle_accum_rad + (dphi_ccw_rad * self._circle_dir_sign)
            if accum_rad < 0.0:
                accum_rad = 0.0
            self._circle_accum_rad = accum_rad
            inv_r = 1.0 / r_actual_m
            ux = rx_m * inv_r
            uy = ry_m * inv_r
        else:
            accum_rad = self._circle_accum_rad
            ux = 0.0
            uy = -self._circle_dir_sign

        full_laps = int(accum_rad // two_pi)
        if self.laps > 0:
            total_rad = float(self.laps) * two_pi
            rem_rad = total_rad - accum_rad
            rem_arc_m = rem_rad * self.circle_radius_m
            if rem_rad <= 0.0 or rem_arc_m <= 0.015:
                self.completed_laps = self.laps
                self.routine_progress = 1.0
                self.active_routine = None
                self.state = "COMPLETED"
                self.stop_reason = ""
                self._emit_sound("EVENT_MISSION_COMPLETE")
                return 0.0, 0.0, 0.0, False
            if full_laps > self.completed_laps and full_laps < self.laps:
                self.completed_laps = full_laps
                self._emit_sound("ACK_COMMAND")
            self.routine_progress = min(1.0, accum_rad / total_rad)
            v_brake_mps = math.sqrt(2.0 * self.accel_mps2 * (rem_arc_m if rem_arc_m > 0.0 else 0.0))
            crawl_mps = self.min_crawl_mps if self.min_crawl_mps < self.speed_req_mps else self.speed_req_mps
            if v_brake_mps < crawl_mps:
                v_brake_mps = crawl_mps
            v_lim_mps = self.speed_req_mps if self.speed_req_mps < v_brake_mps else v_brake_mps
        else:
            if full_laps > self.completed_laps:
                self.completed_laps = full_laps
                self._emit_sound("ACK_COMMAND")
            self.routine_progress = (accum_rad % two_pi) / two_pi
            v_lim_mps = self.speed_req_mps

        v_rad_mps = self.kr_radial_per_s * (self.circle_radius_m - r_actual_m)
        max_v_rad_mps = 0.6 * v_lim_mps
        if v_rad_mps > max_v_rad_mps:
            v_rad_mps = max_v_rad_mps
        elif v_rad_mps < -max_v_rad_mps:
            v_rad_mps = -max_v_rad_mps

        tx = -self._circle_dir_sign * uy
        ty = self._circle_dir_sign * ux

        v_tan_sq = (v_lim_mps * v_lim_mps) - (v_rad_mps * v_rad_mps)
        v_tan_mps = math.sqrt(v_tan_sq) if v_tan_sq > 0.0 else 0.0

        vx_loc_mps = (v_tan_mps * tx) + (v_rad_mps * ux)
        vy_loc_mps = (v_tan_mps * ty) + (v_rad_mps * uy)

        if self.orientation_mode == "compass_locked":
            omega_ff_dps = 0.0
        else:
            r_ref_m = self.circle_radius_m if self.circle_radius_m > 0.05 else 0.05
            omega_ff_dps = self._circle_dir_sign * math.degrees(v_tan_mps / r_ref_m)

        return vx_loc_mps, vy_loc_mps, omega_ff_dps, True

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

        omega_ff_dps = 0.0
        if self.active_routine == "compass_box":
            vx_loc_mps, vy_loc_mps, keep_going = self._step_compass_box_local_vel()
            if not keep_going:
                self.cmd_vx_mps = 0.0
                self.cmd_vy_mps = 0.0
                self.cmd_omega_dps = 0.0
                return 0.0, 0.0, 0.0, False
            half_s = 0.5 * self.side_length_m
            self.psi_target_deg = self._compute_target_heading_deg(vx_loc_mps, vy_loc_mps, half_s, -half_s)
        elif self.active_routine == "circle":
            vx_loc_mps, vy_loc_mps, omega_ff_dps, keep_going = self._step_circle_local_vel()
            if not keep_going:
                self.cmd_vx_mps = 0.0
                self.cmd_vy_mps = 0.0
                self.cmd_omega_dps = 0.0
                return 0.0, 0.0, 0.0, False
            self.psi_target_deg = self._compute_target_heading_deg(
                vx_loc_mps, vy_loc_mps, self._circle_cx_m, self._circle_cy_m
            )
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
        gz_dps = self._read_gyro_z_dps()
        des_omega_dps = (
            omega_ff_dps
            - (self.kp_yaw_dps_per_deg * err_cw_deg)
            - (self.kd_yaw_s * (gz_dps - omega_ff_dps))
        )
        des_omega_dps = max(-self.max_yaw_hold_dps, min(self.max_yaw_hold_dps, des_omega_dps))

        cur_spd = math.sqrt(self.cmd_vx_mps * self.cmd_vx_mps + self.cmd_vy_mps * self.cmd_vy_mps)
        dvx = des_vx_mps - self.cmd_vx_mps
        dvy = des_vy_mps - self.cmd_vy_mps
        max_dv_tan = self.accel_mps2 * dt
        if cur_spd < 1e-6:
            dv = math.sqrt(dvx * dvx + dvy * dvy)
            if dv <= max_dv_tan or dv < 1e-9:
                self.cmd_vx_mps, self.cmd_vy_mps = des_vx_mps, des_vy_mps
            else:
                scl = max_dv_tan / dv
                self.cmd_vx_mps += dvx * scl
                self.cmd_vy_mps += dvy * scl
        else:
            inv_cur = 1.0 / cur_spd
            ex = self.cmd_vx_mps * inv_cur
            ey = self.cmd_vy_mps * inv_cur
            dv_tan = dvx * ex + dvy * ey
            dv_norm = -dvx * ey + dvy * ex
            lat_lim_mps2 = 1.15 * self.a_c_max_mps2
            if self.accel_mps2 > lat_lim_mps2:
                lat_lim_mps2 = self.accel_mps2
            max_dv_norm = lat_lim_mps2 * dt
            r_tan = dv_tan / max_dv_tan if max_dv_tan > 1e-9 else 0.0
            r_norm = dv_norm / max_dv_norm if max_dv_norm > 1e-9 else 0.0
            ellip_sq = (r_tan * r_tan) + (r_norm * r_norm)
            if ellip_sq <= 1.0:
                self.cmd_vx_mps, self.cmd_vy_mps = des_vx_mps, des_vy_mps
            else:
                scl = 1.0 / math.sqrt(ellip_sq)
                self.cmd_vx_mps += dvx * scl
                self.cmd_vy_mps += dvy * scl

        cmd_spd = math.sqrt(self.cmd_vx_mps * self.cmd_vx_mps + self.cmd_vy_mps * self.cmd_vy_mps)
        spd_ceil = cur_spd + max_dv_tan
        if self.max_speed_mps < spd_ceil:
            spd_ceil = self.max_speed_mps
        if cmd_spd > spd_ceil and cmd_spd > 1e-6:
            cap_scl = spd_ceil / cmd_spd
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
            "orientation_mode": self.orientation_mode,
            "direction": self.circle_direction,
            "radius_m": round(self.circle_radius_m, 3),
            "speed_target_mps": round(self.speed_req_mps, 3),
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
