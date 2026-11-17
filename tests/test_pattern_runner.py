# ==============================================================================
# ARSE — Autonomous Road Systems Engineering
# Desktop Verification Suite: Tasks 2.1 & 2.2 (50 Hz Dead-Reckoning Core,
#                             compass_box, Closed-Loop circle & Safety Clamping)
# Target:      nav/pattern_runner.py, drivers/drive_controller.py,
#              drivers/omni_kinematics.py, drivers/icm20948.py,
#              telemetry_server.py & main.py
# ==============================================================================

import ast
import asyncio
import json
import math
import os
import tempfile
import time
import unittest
from unittest import mock

from drivers.drive_controller import DriveController
from drivers.omni_kinematics import Omni3Kinematics
from nav import PatternRunner
from security.auth_manager import AuthManager
from telemetry_server import TelemetryServer


REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _push_cpython_ticks_if_missing():
    added = []
    if not hasattr(time, "ticks_ms"):
        time.ticks_ms = lambda: int(time.monotonic() * 1000)
        added.append("ticks_ms")
    if not hasattr(time, "ticks_diff"):
        time.ticks_diff = lambda a, b: a - b
        added.append("ticks_diff")
    if not hasattr(time, "ticks_add"):
        time.ticks_add = lambda a, b: a + b
        added.append("ticks_add")
    if not hasattr(time, "sleep_ms"):
        time.sleep_ms = lambda ms: None
        added.append("sleep_ms")
    return added


def _pop_cpython_ticks(added):
    for name in added:
        if hasattr(time, name):
            delattr(time, name)


class MockIMU:
    def __init__(self, heading_deg=0.0, gz_dps=0.0):
        self.heading_deg = float(heading_deg)
        self.gz_dps = float(gz_dps)
        self.last_gyro_dps = (0.0, 0.0, float(gz_dps))
        self.last_accel_g = (0.0, 0.0, 1.0)
        self.mag_online = True


class MockLEDs:
    def __init__(self):
        self.modes = []
        self.mode = "off"

    def set_mode(self, mode, brightness=None):
        self.mode = mode
        self.modes.append(mode)
        return {"mode": mode, "brightness": brightness or 0.4}

    def get_status(self):
        return {"mode": self.mode, "brightness": 0.4, "enabled": self.mode != "off", "power_good": True}


class MockMotorChan:
    def __init__(self):
        self.spd = 0.0

    def speed(self, val):
        self.spd = float(val)

    def stop(self):
        self.spd = 0.0


class MockDualMotorMod:
    def __init__(self):
        self.motor1 = MockMotorChan()
        self.motor2 = MockMotorChan()
        self._en = False

    def enable(self):
        self._en = True

    def disable(self):
        self._en = False

    def is_enabled(self):
        return self._en

    def read_temperature(self):
        return 28.5

    def read_fault(self):
        return False


class MockYukon:
    def __init__(self, main_on=True):
        self.main_on = bool(main_on)

    def is_main_output_enabled(self):
        return self.main_on


class _MemoryReader:
    def __init__(self, raw_bytes):
        self._buf = bytes(raw_bytes)
        self._pos = 0

    async def readline(self):
        n = len(self._buf)
        if self._pos >= n:
            return b""
        nl = self._buf.find(b"\n", self._pos)
        if nl < 0:
            chunk = self._buf[self._pos :]
            self._pos = n
            return chunk
        chunk = self._buf[self._pos : nl + 1]
        self._pos = nl + 1
        return chunk

    async def read(self, count=-1):
        n = len(self._buf)
        if self._pos >= n:
            return b""
        if count is None or count < 0:
            chunk = self._buf[self._pos :]
            self._pos = n
            return chunk
        end = min(n, self._pos + int(count))
        chunk = self._buf[self._pos : end]
        self._pos = end
        return chunk


class _MemoryWriter:
    def __init__(self, peer_ip="192.168.50.99", peer_port=51234):
        self.peer = (peer_ip, int(peer_port))
        self.buf = bytearray()
        self.closed = False

    def get_extra_info(self, name, default=None):
        if name == "peername":
            return self.peer
        return default

    def write(self, data):
        if isinstance(data, memoryview):
            self.buf.extend(bytes(data))
        else:
            self.buf.extend(data)

    async def drain(self):
        return None

    def close(self):
        self.closed = True

    async def wait_closed(self):
        return None


def _drive_coro(coro):
    import telemetry_server as _ts_mod

    async def _instant_wait_for(aw, timeout=None):
        return await aw

    def _step_raw(c):
        while True:
            try:
                c.send(None)
            except StopIteration as stop:
                return stop.value

    with mock.patch.object(_ts_mod.asyncio, "wait_for", side_effect=_instant_wait_for):
        return _step_raw(coro)


class TestPatternRunnerPhysics(unittest.TestCase):
    def setUp(self):
        self.kin = Omni3Kinematics(
            wheel_radius_m=0.029,
            base_radius_m=0.090,
            max_speed_mps=1.297,
        )
        self.imu = MockIMU(heading_deg=0.0)
        self.leds = MockLEDs()
        self.sounds = []
        self.runner = PatternRunner(
            kinematics=self.kin,
            imu=self.imu,
            led_controller=self.leds,
            range_provider=None,
            sound_callback=lambda tok: self.sounds.append(tok),
        )

    def test_presets_and_centripetal_speed_clamp(self):
        indoor = self.runner.apply_preset("indoor")
        self.assertAlmostEqual(indoor["max_speed_mps"], 0.45)
        self.assertAlmostEqual(indoor["max_leash_m"], 1.8)
        self.assertAlmostEqual(indoor["a_c_max_mps2"], 0.75)

        # Tight radius r = 0.10 m indoors -> sqrt(0.75 * 0.10) = 0.27386 m/s < 0.45 m/s
        v_tight = self.runner.clamp_centripetal_speed(0.45, 0.10)
        self.assertAlmostEqual(v_tight, math.sqrt(0.75 * 0.10), places=5)

        # Wide radius r = 2.0 m indoors -> capped by indoor max_speed_mps = 0.45 m/s
        self.assertAlmostEqual(self.runner.clamp_centripetal_speed(0.80, 2.0), 0.45, places=5)
        self.assertEqual(self.runner.clamp_centripetal_speed(0.40, 0.0), 0.0)

        outdoor = self.runner.apply_preset("outdoor")
        self.assertAlmostEqual(outdoor["max_speed_mps"], 0.95)
        self.assertAlmostEqual(outdoor["max_leash_m"], 5.5)
        self.assertAlmostEqual(outdoor["a_c_max_mps2"], 1.15)
        self.assertAlmostEqual(
            self.runner.clamp_centripetal_speed(0.95, 0.25),
            math.sqrt(1.15 * 0.25),
            places=5,
        )

        with self.assertRaises(ValueError):
            self.runner.apply_preset("orbital")

    def test_trapezoidal_linear_and_angular_acceleration_ramps(self):
        self.imu.heading_deg = 90.0
        self.runner.start_compass_box(side_length_m=1.0, laps=1, speed_mps=0.45, preset="indoor")

        # Inject a sudden 25 deg CW heading perturbation so yaw error is large
        self.imu.heading_deg = 115.0
        dt_s = 0.020
        self.runner.step(dt_s=dt_s)

        max_lin_step = self.runner.accel_mps2 * dt_s
        actual_lin = math.sqrt(self.runner.cmd_vx_mps ** 2 + self.runner.cmd_vy_mps ** 2)
        self.assertLessEqual(actual_lin, max_lin_step + 1e-6)
        self.assertGreater(actual_lin, 0.0)

        max_ang_step = self.runner.alpha_dps2 * dt_s
        self.assertLessEqual(abs(self.runner.cmd_omega_dps), max_ang_step + 1e-6)
        # CW drift (+25 deg CW from 90 -> 115) MUST command +CCW (+omega_dps > 0) restoring spin
        self.assertGreater(self.runner.cmd_omega_dps, 0.0)

    def test_yaw_wraparound_across_north_360_boundary(self):
        self.imu.heading_deg = 358.0
        self.runner.start_compass_box(side_length_m=0.8, laps=1, speed_mps=0.30, preset="indoor")

        # Cone drifts +5 deg CW across North to 3.0 deg -> must correct CCW (+omega_dps > 0)
        self.imu.heading_deg = 3.0
        self.runner.step(dt_s=0.020)
        self.assertGreater(self.runner.cmd_omega_dps, 0.0)

        # Cone drifts -6 deg CCW to 352.0 deg -> must correct CW (-omega_dps < 0)
        self.runner.cmd_omega_dps = 0.0
        self.imu.heading_deg = 352.0
        self.runner.step(dt_s=0.020)
        self.assertLess(self.runner.cmd_omega_dps, 0.0)

    def test_compass_box_four_leg_square_and_heading_perturbed_odometry(self):
        # Start at an arbitrary non-zero compass bearing (137 deg SE) with zero GNSS
        self.imu.heading_deg = 137.0
        st = self.runner.start_compass_box(
            side_length_m=0.80,
            laps=1,
            speed_mps=0.35,
            preset="indoor",
        )
        self.assertEqual(st["state"], "RUNNING")
        self.assertEqual(st["active_routine"], "compass_box")
        self.assertIn("beacon", self.leds.modes)
        self.assertIn("ACK_MISSION_START", self.sounds)

        visited_leg_peaks = {}
        dt_s = 0.020
        for tick in range(2500):
            if not self.runner.is_active():
                break
            leg_before = self.runner.leg_index
            # Simulate slight 6 deg sinusoidal floor yaw disturbance + gyro recovery
            self.imu.heading_deg = (137.0 + 6.0 * math.sin(tick * 0.07)) % 360.0
            w0, w1, w2, active = self.runner.step(dt_s=dt_s)
            self.assertLessEqual(max(abs(w0), abs(w1), abs(w2)), 1.0 + 1e-6)
            if self.runner.leg_index != leg_before or not active:
                visited_leg_peaks[leg_before] = (self.runner.local_x_m, self.runner.local_y_m)

        self.assertEqual(self.runner.state, "COMPLETED")
        self.assertIsNone(self.runner.active_routine)
        self.assertAlmostEqual(self.runner.routine_progress, 1.0, places=3)
        self.assertIn("EVENT_MISSION_COMPLETE", self.sounds)

        # Verify all 4 corners (+X -> -Y -> -X -> +Y) were reached accurately in local frame
        self.assertEqual(set(visited_leg_peaks.keys()), {0, 1, 2, 3})
        c0 = visited_leg_peaks[0]
        c1 = visited_leg_peaks[1]
        c2 = visited_leg_peaks[2]
        c3 = visited_leg_peaks[3]
        self.assertAlmostEqual(c0[0], 0.80, delta=0.04)
        self.assertAlmostEqual(c0[1], 0.00, delta=0.04)
        self.assertAlmostEqual(c1[0], 0.80, delta=0.04)
        self.assertAlmostEqual(c1[1], -0.80, delta=0.04)
        self.assertAlmostEqual(c2[0], 0.00, delta=0.04)
        self.assertAlmostEqual(c2[1], -0.80, delta=0.04)
        self.assertAlmostEqual(c3[0], 0.00, delta=0.04)
        self.assertAlmostEqual(c3[1], 0.00, delta=0.04)

    def test_compass_box_multi_lap_completion(self):
        self.imu.heading_deg = 20.0
        self.runner.start_compass_box(side_length_m=0.50, laps=2, speed_mps=0.40, preset="indoor")
        for _ in range(3000):
            if not self.runner.is_active():
                break
            self.runner.step(dt_s=0.020)
        self.assertEqual(self.runner.state, "COMPLETED")
        self.assertEqual(self.runner.completed_laps, 2)
        self.assertIn("ACK_COMMAND", self.sounds)
        self.assertIn("EVENT_MISSION_COMPLETE", self.sounds)

    def test_virtual_circular_leash_halts_with_leash_limit_reached(self):
        # Indoor max_leash_m = 1.8 m; commanding side_length_m = 2.5 m must trip leash on Leg 0
        self.runner.start_compass_box(side_length_m=2.50, laps=1, speed_mps=0.45, preset="indoor")
        for _ in range(1000):
            w0, w1, w2, active = self.runner.step(dt_s=0.020)
            if not active:
                break

        self.assertFalse(self.runner.is_active())
        self.assertEqual(self.runner.state, "LEASH_LIMIT_REACHED")
        self.assertEqual(self.runner.stop_reason, "LEASH_LIMIT_REACHED")
        r_m = math.sqrt(self.runner.local_x_m ** 2 + self.runner.local_y_m ** 2)
        self.assertGreaterEqual(r_m, 1.80)
        self.assertLess(r_m, 1.83)
        self.assertEqual((w0, w1, w2), (0.0, 0.0, 0.0))
        self.assertIn("ERR_SYSTEM_FAULT", self.sounds)

    def test_zero_allocation_and_planned_hardware_ast_guardrails(self):
        pr_path = os.path.join(REPO_ROOT, "nav", "pattern_runner.py")
        with open(pr_path, "r", encoding="utf-8") as f:
            src = f.read()
        tree = ast.parse(src, filename=pr_path)

        forbidden_imports = {"numpy", "scipy", "cryptography", "vl53l5cx", "espnow"}
        hot_50hz_methods = {
            "step",
            "_step_circle_local_vel",
            "_step_compass_box_local_vel",
            "_compute_target_heading_deg",
            "_read_heading_deg",
            "_read_gyro_z_dps",
            "is_active",
            "_emit_sound",
            "_set_leds",
        }
        hot_50hz_module_helpers = {"_ticks_ms", "_ticks_diff", "wrap_angle_180_deg"}

        runner_cls = None
        found_module_helpers = {}
        for node in tree.body:
            if isinstance(node, ast.ClassDef) and node.name == "PatternRunner":
                runner_cls = node
            elif isinstance(node, ast.FunctionDef) and node.name in hot_50hz_module_helpers:
                found_module_helpers[node.name] = node

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    self.assertNotIn(alias.name.split(".")[0].lower(), forbidden_imports)
            elif isinstance(node, ast.ImportFrom) and node.module:
                self.assertNotIn(node.module.split(".")[0].lower(), forbidden_imports)

        self.assertIsNotNone(runner_cls, "PatternRunner class not found in nav/pattern_runner.py")
        self.assertEqual(set(found_module_helpers.keys()), hot_50hz_module_helpers)

        found_methods = {
            item.name: item
            for item in runner_cls.body
            if isinstance(item, ast.FunctionDef) and item.name in hot_50hz_methods
        }
        self.assertEqual(
            set(found_methods.keys()),
            hot_50hz_methods,
            f"Missing expected 50 Hz helper methods: {hot_50hz_methods - set(found_methods.keys())}",
        )

        forbidden_alloc_nodes = (
            ast.List,
            ast.Dict,
            ast.Set,
            ast.ListComp,
            ast.DictComp,
            ast.SetComp,
            ast.GeneratorExp,
        )
        forbidden_call_names = {"list", "dict", "set", "bytearray", "bytes"}

        all_hot_fns = list(found_methods.items()) + list(found_module_helpers.items())
        for fn_name, fn_node in all_hot_fns:
            for sub in ast.walk(fn_node):
                self.assertNotIsInstance(
                    sub,
                    forbidden_alloc_nodes,
                    f"50 Hz hot-path function '{fn_name}' must not allocate container literals or comprehensions",
                )
                if isinstance(sub, ast.Call):
                    if isinstance(sub.func, ast.Name):
                        self.assertNotIn(
                            sub.func.id,
                            forbidden_call_names,
                            f"50 Hz hot-path function '{fn_name}' must not call {sub.func.id}()",
                        )
                    elif (
                        isinstance(sub.func, ast.Attribute)
                        and isinstance(sub.func.value, ast.Name)
                        and sub.func.value.id == "gc"
                        and sub.func.attr == "collect"
                    ):
                        self.fail(f"50 Hz hot-path function '{fn_name}' must not invoke gc.collect()")


class TestCircleRoutineAndHolonomicModes(unittest.TestCase):
    def setUp(self):
        self.kin = Omni3Kinematics(
            wheel_radius_m=0.029,
            base_radius_m=0.090,
            max_speed_mps=1.297,
        )
        self.imu = MockIMU(heading_deg=0.0)
        self.leds = MockLEDs()
        self.sounds = []
        self.runner = PatternRunner(
            kinematics=self.kin,
            imu=self.imu,
            led_controller=self.leds,
            range_provider=None,
            sound_callback=lambda tok: self.sounds.append(tok),
        )

    def _step_closed_loop(self, dt_s=0.020):
        w0, w1, w2, active = self.runner.step(dt_s=dt_s)
        self.imu.heading_deg = (self.imu.heading_deg - self.runner.cmd_omega_dps * dt_s) % 360.0
        self.imu.gz_dps = self.runner.cmd_omega_dps
        self.imu.last_gyro_dps = (0.0, 0.0, self.runner.cmd_omega_dps)
        return w0, w1, w2, active

    @staticmethod
    def _local_to_world_ne(dx_loc_m, dy_loc_m, psi_0_deg):
        # +X_local points along compass psi_0_deg (CW from North);
        # +Y_local points 90 deg CCW (psi_0_deg - 90 deg CW).
        psi0_rad = math.radians(psi_0_deg)
        c0 = math.cos(psi0_rad)
        s0 = math.sin(psi0_rad)
        north = dx_loc_m * c0 + dy_loc_m * s0
        east = dx_loc_m * s0 - dy_loc_m * c0
        return north, east

    @staticmethod
    def _angle_between_2d_deg(ax, ay, bx, by):
        na = math.hypot(ax, ay)
        nb = math.hypot(bx, by)
        if na < 1e-9 or nb < 1e-9:
            return 0.0
        cos_ang = max(-1.0, min(1.0, (ax * bx + ay * by) / (na * nb)))
        return math.degrees(math.acos(cos_ang))

    def test_centripetal_speed_clamping_indoor_and_outdoor_across_full_orbit(self):
        cases = (
            # (preset, radius_m, speed_req_mps, expected_a_c_max, expected_max_spd)
            ("indoor", 0.08, 0.45, 0.75, 0.45),
            ("indoor", 0.15, 0.45, 0.75, 0.45),
            ("indoor", 0.25, 0.45, 0.75, 0.45),
            ("indoor", 0.50, 0.20, 0.75, 0.45),  # v_req dominates
            ("indoor", 0.75, 0.90, 0.75, 0.45),  # indoor max_speed_mps (0.45) dominates over sqrt(0.75*0.75)=0.75
            ("outdoor", 0.08, 0.95, 1.15, 0.95),
            ("outdoor", 0.15, 0.95, 1.15, 0.95),
            ("outdoor", 0.25, 0.95, 1.15, 0.95),
            ("outdoor", 1.50, 1.25, 1.15, 0.95),  # outdoor max_speed_mps (0.95) dominates
        )
        for preset, r_m, v_req, ac_max, vmax in cases:
            with self.subTest(preset=preset, radius_m=r_m, speed_req_mps=v_req):
                v_safe = min(v_req, vmax, math.sqrt(ac_max * r_m))
                self.imu.heading_deg = 25.0
                self.imu.gz_dps = 0.0
                st = self.runner.start_circle(
                    radius_m=r_m,
                    laps=1,
                    direction="ccw",
                    speed_mps=v_req,
                    orientation_mode="tangent",
                    preset=preset,
                )
                self.assertAlmostEqual(self.runner.speed_req_mps, v_safe, places=6)
                self.assertAlmostEqual(st["speed_target_mps"], round(v_safe, 3), places=3)

                peak_body_spd = 0.0
                ticks = 0
                while self.runner.is_active() and ticks < 4000:
                    w0, w1, w2, _ = self._step_closed_loop(dt_s=0.020)
                    self.assertLessEqual(max(abs(w0), abs(w1), abs(w2)), 1.0 + 1e-6)
                    inst_spd = math.hypot(self.runner.cmd_vx_mps, self.runner.cmd_vy_mps)
                    if inst_spd > peak_body_spd:
                        peak_body_spd = inst_spd
                    self.assertLessEqual(
                        inst_spd,
                        v_safe + 1e-6,
                        f"Instantaneous body speed {inst_spd:.5f} exceeded v_safe {v_safe:.5f}",
                    )
                    ticks += 1

                self.assertEqual(self.runner.state, "COMPLETED")
                self.assertGreater(peak_body_spd, 0.80 * v_safe)

    def test_min_crawl_mps_never_pushes_speed_above_v_safe(self):
        # Subcase 1: ultra-slow requested speed (0.020 m/s < min_crawl_mps = 0.045 m/s)
        self.imu.heading_deg = 0.0
        self.runner.start_circle(
            radius_m=0.08,
            laps=1,
            direction="cw",
            speed_mps=0.020,
            orientation_mode="compass_locked",
            preset="indoor",
        )
        self.assertAlmostEqual(self.runner.speed_req_mps, 0.020, places=6)
        for _ in range(3000):
            if not self.runner.is_active():
                break
            self._step_closed_loop(dt_s=0.020)
            inst_spd = math.hypot(self.runner.cmd_vx_mps, self.runner.cmd_vy_mps)
            self.assertLessEqual(inst_spd, 0.020 + 1e-6)
        self.assertEqual(self.runner.state, "COMPLETED")

        # Subcase 2: min_crawl_mps configured higher than tight-radius centripetal v_safe
        # r = 0.08 m indoor -> v_safe = sqrt(0.75 * 0.08) = 0.244949 m/s < min_crawl_mps = 0.35 m/s
        self.runner.min_crawl_mps = 0.35
        v_safe_tight = math.sqrt(0.75 * 0.08)
        self.runner.start_circle(
            radius_m=0.08,
            laps=1,
            direction="ccw",
            speed_mps=0.45,
            orientation_mode="compass_locked",
            preset="indoor",
        )
        self.assertAlmostEqual(self.runner.speed_req_mps, v_safe_tight, places=6)
        for _ in range(1500):
            if not self.runner.is_active():
                break
            self._step_closed_loop(dt_s=0.020)
            inst_spd = math.hypot(self.runner.cmd_vx_mps, self.runner.cmd_vy_mps)
            self.assertLessEqual(inst_spd, v_safe_tight + 1e-6)
        self.assertEqual(self.runner.state, "COMPLETED")

    def test_closed_loop_radial_drift_correction_outward_inward_and_singular_centre(self):
        # 1. Outward perturbation (r_actual > r_target) across a 3-lap run
        r_target = 0.60
        self.imu.heading_deg = 15.0
        self.runner.start_circle(
            radius_m=r_target,
            laps=3,
            direction="ccw",
            speed_mps=0.35,
            orientation_mode="compass_locked",
            preset="indoor",
        )
        for _ in range(40):
            self._step_closed_loop(dt_s=0.020)

        # Push position radially outward from (cx=0.0, cy=0.60) to r_actual = 0.85 m (+0.25 m error)
        cx, cy = self.runner._circle_cx_m, self.runner._circle_cy_m
        rx = self.runner.local_x_m - cx
        ry = self.runner.local_y_m - cy
        r_now = math.hypot(rx, ry)
        ux, uy = rx / r_now, ry / r_now
        self.runner.local_x_m = cx + ux * 0.85
        self.runner.local_y_m = cy + uy * 0.85

        vx_loc, vy_loc, _, keep = self.runner._step_circle_local_vel()
        self.assertTrue(keep)
        # Radial projection of commanded local velocity must point strictly inward (< 0)
        self.assertLess(vx_loc * ux + vy_loc * uy, -0.10)

        sampled_outward_errs = [abs(math.hypot(self.runner.local_x_m - cx, self.runner.local_y_m - cy) - r_target)]
        for window in range(6):
            for _ in range(20):  # 0.40 s per window
                self._step_closed_loop(dt_s=0.020)
            err_m = abs(math.hypot(self.runner.local_x_m - cx, self.runner.local_y_m - cy) - r_target)
            sampled_outward_errs.append(err_m)

        # Error must decay strictly while above 1 cm and settle well below 0.02 m
        for prev_e, next_e in zip(sampled_outward_errs, sampled_outward_errs[1:]):
            if prev_e > 0.01:
                self.assertLess(next_e, prev_e)
            else:
                self.assertLess(next_e, 0.02)
        self.assertLess(sampled_outward_errs[-1], 0.02)

        # Continue remaining multi-lap orbit and verify zero outward spiralling
        steady_max_err = 0.0
        while self.runner.is_active():
            if self.runner.routine_progress < 0.96:
                err_m = abs(math.hypot(self.runner.local_x_m - cx, self.runner.local_y_m - cy) - r_target)
                if err_m > steady_max_err:
                    steady_max_err = err_m
            self._step_closed_loop(dt_s=0.020)
        self.assertEqual(self.runner.state, "COMPLETED")
        self.assertEqual(self.runner.completed_laps, 3)
        self.assertLess(steady_max_err, 0.02)

        # 2. Inward perturbation (r_actual < r_target)
        self.runner.start_circle(
            radius_m=r_target,
            laps=2,
            direction="cw",
            speed_mps=0.35,
            orientation_mode="compass_locked",
            preset="indoor",
        )
        for _ in range(40):
            self._step_closed_loop(dt_s=0.020)

        cx, cy = self.runner._circle_cx_m, self.runner._circle_cy_m
        rx = self.runner.local_x_m - cx
        ry = self.runner.local_y_m - cy
        r_now = math.hypot(rx, ry)
        ux, uy = rx / r_now, ry / r_now
        self.runner.local_x_m = cx + ux * 0.32
        self.runner.local_y_m = cy + uy * 0.32

        vx_loc, vy_loc, _, keep = self.runner._step_circle_local_vel()
        self.assertTrue(keep)
        # Radial projection of commanded local velocity must point strictly outward (> 0)
        self.assertGreater(vx_loc * ux + vy_loc * uy, 0.10)

        sampled_inward_errs = [abs(math.hypot(self.runner.local_x_m - cx, self.runner.local_y_m - cy) - r_target)]
        for _ in range(6):
            for _ in range(20):
                self._step_closed_loop(dt_s=0.020)
            err_m = abs(math.hypot(self.runner.local_x_m - cx, self.runner.local_y_m - cy) - r_target)
            sampled_inward_errs.append(err_m)

        for prev_e, next_e in zip(sampled_inward_errs, sampled_inward_errs[1:]):
            if prev_e > 0.01:
                self.assertLess(next_e, prev_e)
            else:
                self.assertLess(next_e, 0.02)
        self.assertLess(sampled_inward_errs[-1], 0.02)

        # 3. Exact singular centre perturbation (r_actual == 0.0) recovers without NaN/ZeroDivisionError
        self.runner.start_circle(
            radius_m=r_target,
            laps=0,
            direction="ccw",
            speed_mps=0.35,
            orientation_mode="compass_locked",
            preset="indoor",
        )
        cx, cy = self.runner._circle_cx_m, self.runner._circle_cy_m
        self.runner.local_x_m = cx
        self.runner.local_y_m = cy
        for _ in range(220):  # 4.4 s: covers 0.48 m saturated crawl at 0.21 m/s + exponential tail
            w0, w1, w2, active = self._step_closed_loop(dt_s=0.020)
            self.assertTrue(active)
            self.assertFalse(math.isnan(w0) or math.isnan(w1) or math.isnan(w2))
        err_after_singularity = abs(math.hypot(self.runner.local_x_m - cx, self.runner.local_y_m - cy) - r_target)
        self.assertLess(err_after_singularity, 0.01)
        self.runner.stop()

    def test_cw_and_ccw_orbit_geometry_cues_and_finite_laps(self):
        # CCW: centre at (0, +R), starts forward (+X), sweeps +Y up to +2R, returns near (0, 0)
        self.sounds.clear()
        self.leds.modes.clear()
        self.imu.heading_deg = 0.0
        st_ccw = self.runner.start(
            "circle",
            preset="indoor",
            params={"radius_m": 0.50, "laps": 1, "direction": "ccw", "speed_mps": 0.35},
        )
        self.assertEqual(st_ccw["active_routine"], "circle")
        self.assertEqual(st_ccw["direction"], "ccw")
        self.assertAlmostEqual(self.runner._circle_cx_m, 0.0)
        self.assertAlmostEqual(self.runner._circle_cy_m, 0.50)
        self.assertIn("pulse", self.leds.modes)
        self.assertEqual(self.sounds, ["ACK_MISSION_START"])

        ccw_min_y, ccw_max_y = 0.0, 0.0
        early_x_ccw = None
        for tick in range(2000):
            if not self.runner.is_active():
                break
            self._step_closed_loop(dt_s=0.020)
            if tick == 10:
                early_x_ccw = self.runner.local_x_m
            ccw_min_y = min(ccw_min_y, self.runner.local_y_m)
            ccw_max_y = max(ccw_max_y, self.runner.local_y_m)

        self.assertGreater(early_x_ccw, 0.01)
        self.assertAlmostEqual(ccw_max_y, 1.00, delta=0.03)
        self.assertGreaterEqual(ccw_min_y, -0.02)
        self.assertEqual(self.runner.state, "COMPLETED")
        self.assertEqual(self.runner.completed_laps, 1)
        self.assertAlmostEqual(self.runner.routine_progress, 1.0, places=3)
        self.assertLess(math.hypot(self.runner.local_x_m, self.runner.local_y_m), 0.04)
        self.assertIn("EVENT_MISSION_COMPLETE", self.sounds)

        # CW: centre at (0, -R), starts forward (+X), sweeps -Y down to -2R, completes 2 laps cleanly
        self.sounds.clear()
        self.leds.modes.clear()
        self.imu.heading_deg = 0.0
        st_cw = self.runner.circle(
            radius_m=0.50,
            laps=2,
            direction="cw",
            speed_mps=0.35,
            orientation_mode="tangent",
            preset="indoor",
        )
        self.assertEqual(st_cw["direction"], "cw")
        self.assertAlmostEqual(self.runner._circle_cx_m, 0.0)
        self.assertAlmostEqual(self.runner._circle_cy_m, -0.50)
        self.assertIn("pulse", self.leds.modes)
        self.assertEqual(self.sounds, ["ACK_MISSION_START"])

        cw_min_y, cw_max_y = 0.0, 0.0
        early_x_cw = None
        for tick in range(3500):
            if not self.runner.is_active():
                break
            self._step_closed_loop(dt_s=0.020)
            if tick == 10:
                early_x_cw = self.runner.local_x_m
            cw_min_y = min(cw_min_y, self.runner.local_y_m)
            cw_max_y = max(cw_max_y, self.runner.local_y_m)

        self.assertGreater(early_x_cw, 0.01)
        self.assertAlmostEqual(cw_min_y, -1.00, delta=0.03)
        self.assertLessEqual(cw_max_y, 0.02)
        self.assertEqual(self.runner.state, "COMPLETED")
        self.assertEqual(self.runner.completed_laps, 2)
        self.assertLess(math.hypot(self.runner.local_x_m, self.runner.local_y_m), 0.04)
        self.assertEqual(self.sounds.count("ACK_COMMAND"), 1)
        self.assertEqual(self.sounds[-1], "EVENT_MISSION_COMPLETE")

    def test_infinite_circle_laps_zero_and_virtual_leash_halt(self):
        # 1. laps=0 keeps running past 2 full revolutions until stop()
        self.sounds.clear()
        self.imu.heading_deg = 120.0
        self.runner.start_circle(
            radius_m=0.40,
            laps=0,
            direction="ccw",
            speed_mps=0.45,
            orientation_mode="compass_locked",
            preset="indoor",
        )
        for _ in range(1200):  # 24 s -> ~3.8 revolutions at 0.45 m/s around 2*pi*0.40 = 2.51 m
            w0, w1, w2, active = self._step_closed_loop(dt_s=0.020)
            self.assertTrue(active)
            self.assertLessEqual(max(abs(w0), abs(w1), abs(w2)), 1.0 + 1e-6)
            self.assertGreaterEqual(self.runner.routine_progress, 0.0)
            self.assertLess(self.runner.routine_progress, 1.0)

        self.assertTrue(self.runner.is_active())
        self.assertEqual(self.runner.state, "RUNNING")
        self.assertGreaterEqual(self.runner.completed_laps, 3)
        self.assertGreaterEqual(self.sounds.count("ACK_COMMAND"), 3)
        self.assertNotIn("EVENT_MISSION_COMPLETE", self.sounds)

        stop_st = self.runner.stop()
        self.assertEqual(stop_st["state"], "STOPPED")
        self.assertIsNone(stop_st["active_routine"])
        self.assertFalse(self.runner.is_active())
        self.assertEqual(
            (self.runner.cmd_vx_mps, self.runner.cmd_vy_mps, self.runner.cmd_omega_dps),
            (0.0, 0.0, 0.0),
        )
        self.assertEqual(self.runner.step(dt_s=0.020), (0.0, 0.0, 0.0, False))

        # 2. Virtual circular leash halt when 2 * radius_m > max_leash_m (1.1 m * 2 = 2.2 m > 1.8 m indoor)
        for direction in ("ccw", "cw"):
            with self.subTest(direction=direction):
                self.sounds.clear()
                self.imu.heading_deg = 0.0
                self.runner.start_circle(
                    radius_m=1.10,
                    laps=1,
                    direction=direction,
                    speed_mps=0.45,
                    orientation_mode="tangent",
                    preset="indoor",
                )
                w0 = w1 = w2 = 1.0
                active = True
                for _ in range(1500):
                    w0, w1, w2, active = self._step_closed_loop(dt_s=0.020)
                    if not active:
                        break
                self.assertFalse(active)
                self.assertFalse(self.runner.is_active())
                self.assertEqual(self.runner.state, "LEASH_LIMIT_REACHED")
                self.assertEqual(self.runner.stop_reason, "LEASH_LIMIT_REACHED")
                dist_from_origin = math.hypot(self.runner.local_x_m, self.runner.local_y_m)
                self.assertGreaterEqual(dist_from_origin, 1.80)
                self.assertLess(dist_from_origin, 1.84)
                self.assertEqual((w0, w1, w2), (0.0, 0.0, 0.0))
                self.assertIn("ERR_SYSTEM_FAULT", self.sounds)

    def test_all_four_holonomic_orientation_modes_closed_loop_yaw(self):
        test_matrix = (
            ("compass_locked", "ccw", 73.0),
            ("compass_locked", "cw", 352.0),
            ("tangent", "ccw", 73.0),
            ("tangent", "cw", 352.0),
            ("orbit_inward", "ccw", 73.0),
            ("orbit_inward", "cw", 352.0),
            ("orbit_outward", "ccw", 73.0),
            ("orbit_outward", "cw", 352.0),
        )
        for mode, direction, psi_0_deg in test_matrix:
            with self.subTest(mode=mode, direction=direction, psi_0_deg=psi_0_deg):
                self.imu.heading_deg = psi_0_deg
                self.imu.gz_dps = 0.0
                self.runner.start_circle(
                    radius_m=0.60,
                    laps=1,
                    direction=direction,
                    speed_mps=0.35,
                    orientation_mode=mode,
                    preset="indoor",
                )
                cx, cy = self.runner._circle_cx_m, self.runner._circle_cy_m
                max_yaw_err_deg = 0.0
                checked_samples = 0

                for _ in range(2500):
                    if not self.runner.is_active():
                        break
                    prev_x, prev_y = self.runner.local_x_m, self.runner.local_y_m
                    w0, w1, w2, active = self._step_closed_loop(dt_s=0.020)
                    self.assertLessEqual(max(abs(w0), abs(w1), abs(w2)), 1.0 + 1e-6)
                    if not active:
                        break

                    dx_step = self.runner.local_x_m - prev_x
                    dy_step = self.runner.local_y_m - prev_y
                    prog = self.runner.routine_progress

                    nose_n = math.cos(math.radians(self.imu.heading_deg))
                    nose_e = math.sin(math.radians(self.imu.heading_deg))

                    if mode == "compass_locked":
                        tgt_n = math.cos(math.radians(psi_0_deg))
                        tgt_e = math.sin(math.radians(psi_0_deg))
                        err_deg = self._angle_between_2d_deg(nose_n, nose_e, tgt_n, tgt_e)
                        max_yaw_err_deg = max(max_yaw_err_deg, err_deg)
                        checked_samples += 1
                    elif mode == "tangent" and 0.15 <= prog <= 0.92:
                        tgt_n, tgt_e = self._local_to_world_ne(dx_step, dy_step, psi_0_deg)
                        err_deg = self._angle_between_2d_deg(nose_n, nose_e, tgt_n, tgt_e)
                        max_yaw_err_deg = max(max_yaw_err_deg, err_deg)
                        checked_samples += 1
                    elif mode == "orbit_inward" and 0.28 <= prog <= 0.92:
                        tgt_n, tgt_e = self._local_to_world_ne(
                            cx - self.runner.local_x_m,
                            cy - self.runner.local_y_m,
                            psi_0_deg,
                        )
                        err_deg = self._angle_between_2d_deg(nose_n, nose_e, tgt_n, tgt_e)
                        max_yaw_err_deg = max(max_yaw_err_deg, err_deg)
                        checked_samples += 1
                    elif mode == "orbit_outward" and 0.28 <= prog <= 0.92:
                        tgt_n, tgt_e = self._local_to_world_ne(
                            self.runner.local_x_m - cx,
                            self.runner.local_y_m - cy,
                            psi_0_deg,
                        )
                        err_deg = self._angle_between_2d_deg(nose_n, nose_e, tgt_n, tgt_e)
                        max_yaw_err_deg = max(max_yaw_err_deg, err_deg)
                        checked_samples += 1

                    if 0.25 <= prog <= 0.90:
                        r_act = math.hypot(self.runner.local_x_m - cx, self.runner.local_y_m - cy)
                        self.assertAlmostEqual(r_act, 0.60, delta=0.025)

                self.assertEqual(self.runner.state, "COMPLETED")
                self.assertGreater(checked_samples, 100)
                tol_deg = 1.0 if mode == "compass_locked" else 6.0
                self.assertLess(
                    max_yaw_err_deg,
                    tol_deg,
                    f"Mode {mode} ({direction}) exceeded yaw tracking tolerance: {max_yaw_err_deg:.2f} deg",
                )
                self.assertLess(math.hypot(self.runner.local_x_m, self.runner.local_y_m), 0.05)

    def test_circle_adversarial_boundary_and_malformed_inputs(self):
        nan_v = float("nan")
        inf_v = float("inf")
        ninf_v = float("-inf")

        bad_kwargs_list = (
            {"radius_m": nan_v},
            {"radius_m": inf_v},
            {"radius_m": ninf_v},
            {"radius_m": True},
            {"radius_m": False},
            {"radius_m": "0.5"},
            {"radius_m": 0.0},
            {"radius_m": -0.25},
            {"radius_m": 0.0499},
            {"radius_m": 10.0001},
            {"laps": True},
            {"laps": False},
            {"laps": -1},
            {"laps": 101},
            {"laps": 1.0},
            {"laps": 1.5},
            {"laps": "1"},
            {"laps": None},
            {"direction": None},
            {"direction": True},
            {"direction": 1},
            {"direction": ""},
            {"direction": "left"},
            {"direction": "right"},
            {"orientation_mode": None},
            {"orientation_mode": False},
            {"orientation_mode": 99},
            {"orientation_mode": ""},
            {"orientation_mode": "inward"},
            {"orientation_mode": "sideways"},
            {"speed_mps": 0.0},
            {"speed_mps": -0.1},
            {"speed_mps": nan_v},
            {"speed_mps": inf_v},
            {"speed_mps": ninf_v},
            {"speed_mps": True},
            {"speed_mps": False},
            {"speed_mps": "0.3"},
            {"preset": "lunar"},
            {"preset": True},
            {"preset": 123},
        )
        for bad_kw in bad_kwargs_list:
            with self.subTest(bad_kw=bad_kw):
                with self.assertRaises(ValueError):
                    self.runner.start_circle(**bad_kw)
                self.assertFalse(self.runner.is_active())

        for bad_params in ([1, 2], "not_a_dict", 42, True):
            with self.assertRaises(ValueError):
                self.runner.start("circle", preset="indoor", params=bad_params)

        for bad_routine in (None, 123, True, "", "hexagon_dance"):
            with self.assertRaises(ValueError):
                self.runner.start(bad_routine, preset="indoor", params={})

        for bad_ac in (-0.1, nan_v, inf_v, True):
            with self.assertRaises(ValueError):
                self.runner.clamp_centripetal_speed(0.3, 0.5, a_c_max_mps2=bad_ac)

        with self.assertRaises(ValueError):
            PatternRunner(self.kin, self.imu, kp_yaw_dps_per_deg=nan_v)
        with self.assertRaises(ValueError):
            PatternRunner(self.kin, self.imu, kr_radial_per_s=True)

        # Exact inclusive domain boundaries must be accepted cleanly
        st_min = self.runner.start_circle(
            radius_m=0.05,
            laps=0,
            direction=" CW ",
            speed_mps=0.10,
            orientation_mode=" ORBIT_OUTWARD ",
            preset=" INDOOR ",
        )
        self.assertEqual(st_min["state"], "RUNNING")
        self.assertEqual(st_min["direction"], "cw")
        self.assertEqual(st_min["orientation_mode"], "orbit_outward")
        self.runner.stop()

        st_max = self.runner.start_circle(
            radius_m=10.0,
            laps=100,
            direction="ccw",
            speed_mps=0.95,
            orientation_mode="orbit_inward",
            preset="outdoor",
        )
        self.assertEqual(st_max["state"], "RUNNING")
        self.assertEqual(st_max["laps"], 100)
        self.runner.stop()


class _DummyNav:
    def __init__(self):
        self.active = False
        self.pause_reason = ""

    def is_active(self):
        return self.active

    def pause(self, reason=""):
        self.active = False
        self.pause_reason = reason
        return {"ok": True}

    def stop(self):
        self.active = False
        return {"ok": True}

    def get_status(self, gps_st=None):
        return {"state": "RUNNING" if self.active else "IDLE"}


class _DummySentry:
    def __init__(self):
        self.enabled = False

    def is_enabled(self):
        return self.enabled

    def disarm(self):
        self.enabled = False
        return {"ok": True}

    def get_status(self):
        return {"state": "WATCH" if self.enabled else "OFF"}


class _DummyBattery:
    def __init__(self):
        self.critical = False

    def get_status(self):
        return {"voltage_v": 12.2, "soc_percent": 90.0, "is_critical": self.critical}


class _DummyAudio:
    def stop(self):
        return {"playing": None}

    def get_status(self):
        return {"playing": None, "volume": 0.35}


class _DummyApp:
    def __init__(self, secrets_path):
        self.yukon = MockYukon(main_on=True)
        self.m1 = MockDualMotorMod()
        self.m2 = MockDualMotorMod()
        self.kin = Omni3Kinematics(0.029, 0.090, 1.297)
        self.drive = DriveController(
            self.yukon,
            self.m1,
            self.m2,
            kinematics=self.kin,
            speed_modes={"indoor": 0.35, "normal": 0.65, "turbo": 1.0},
        )
        self.imu = MockIMU(heading_deg=45.0)
        self.imu_state = {"online": True, "mag_online": True, "heading_deg": 45.0, "tilt_fault": False}
        self.lights = MockLEDs()
        self.audio = _DummyAudio()
        self.battery = _DummyBattery()
        self.nav = _DummyNav()
        self.sentry = _DummySentry()
        self.patterns = PatternRunner(self.kin, self.imu, led_controller=self.lights)
        self.auth = AuthManager(secrets_path=secrets_path, print_bootstrap=False)

    def get_telemetry_snapshot(self):
        pat_st = self.patterns.get_status()
        return {
            "drive": self.drive.get_status(),
            "routines": pat_st,
            "active_routine": pat_st["active_routine"],
            "routine_progress": pat_st["progress"],
            "local_x_m": pat_st["local_x_m"],
            "local_y_m": pat_st["local_y_m"],
        }


class TestRoutinesHttpApiAndSafetyInterlocks(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._added_ticks = _push_cpython_ticks_if_missing()

    @classmethod
    def tearDownClass(cls):
        _pop_cpython_ticks(cls._added_ticks)

    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.secrets_path = os.path.join(self.tmp_dir.name, "secrets.json")
        self.app = _DummyApp(self.secrets_path)
        self.app.auth.update_credentials("admin", "ConeSafePass99!")
        self.op_token = self.app.auth.login("admin", "ConeSafePass99!", client_ip="192.168.50.10")
        self.beacon_token = self.app.auth.get_beacon_token()
        self.server = TelemetryServer(self.app, auth_manager=self.app.auth)

    def tearDown(self):
        self.tmp_dir.cleanup()

    def _request_raw_bytes(self, method, path, raw_body=b"", token=None, content_len_override=None):
        hdrs = [f"{method} {path} HTTP/1.1", "Host: cone.local"]
        if token is not None:
            hdrs.append(f"Authorization: Bearer {token}")
        if raw_body or content_len_override is not None:
            clen = len(raw_body) if content_len_override is None else int(content_len_override)
            hdrs.append("Content-Type: application/json")
            hdrs.append(f"Content-Length: {clen}")
        raw_req = ("\r\n".join(hdrs) + "\r\n\r\n").encode("utf-8") + raw_body

        reader = _MemoryReader(raw_req)
        writer = _MemoryWriter()
        _drive_coro(self.server._handle_client(reader, writer))
        resp_bytes = bytes(writer.buf)
        head, payload = resp_bytes.split(b"\r\n\r\n", 1)
        status_code = int(head.splitlines()[0].split()[1])
        return status_code, json.loads(payload.decode("utf-8"))

    def _request(self, method, path, body=None, token=None):
        if body is None:
            return self._request_raw_bytes(method, path, raw_body=b"", token=token)
        raw_body = json.dumps(body, allow_nan=True).encode("utf-8")
        return self._request_raw_bytes(
            method,
            path,
            raw_body=raw_body,
            token=token,
            content_len_override=len(raw_body),
        )

    def test_auth_required_and_beacon_scope_rejected(self):
        sc, _ = self._request("POST", "/api/routines/start", {"routine": "compass_box"})
        self.assertEqual(sc, 401)

        sc, _ = self._request(
            "POST", "/api/routines/start", {"routine": "compass_box"}, token=self.beacon_token
        )
        self.assertEqual(sc, 403)

        sc, _ = self._request("POST", "/api/routines/stop", {}, token=self.beacon_token)
        self.assertEqual(sc, 403)

    def test_disarmed_motors_rejected_with_http_409_and_never_auto_armed(self):
        self.assertFalse(self.app.drive.armed)
        sc, body = self._request(
            "POST",
            "/api/routines/start",
            {"routine": "compass_box", "preset": "indoor", "params": {"side_length_m": 0.8}},
            token=self.op_token,
        )
        self.assertEqual(sc, 409)
        self.assertFalse(body["ok"])
        self.assertFalse(self.app.drive.armed)
        self.assertFalse(self.app.patterns.is_active())

    def test_sentry_and_tilt_interlocks_reject_with_http_409(self):
        self.app.drive.set_armed(True)

        self.app.sentry.enabled = True
        sc, _ = self._request(
            "POST", "/api/routines/start", {"routine": "compass_box"}, token=self.op_token
        )
        self.assertEqual(sc, 409)
        self.app.sentry.enabled = False

        self.app.imu_state["tilt_fault"] = True
        sc, _ = self._request(
            "POST", "/api/routines/start", {"routine": "compass_box"}, token=self.op_token
        )
        self.assertEqual(sc, 409)
        self.app.imu_state["tilt_fault"] = False

    def test_start_telemetry_and_stop_lifecycle_when_explicitly_armed(self):
        self.app.drive.set_armed(True)
        self.app.nav.active = True

        sc, body = self._request(
            "POST",
            "/api/routines/start",
            {
                "routine": "compass_box",
                "preset": "indoor",
                "params": {"side_length_m": 0.8, "laps": 1, "speed_mps": 0.35},
            },
            token=self.op_token,
        )
        self.assertEqual(sc, 200)
        self.assertTrue(body["ok"])
        self.assertEqual(body["routines"]["active_routine"], "compass_box")
        self.assertFalse(self.app.nav.is_active())

        # Step 15 ticks (300 ms) and inspect GET /api/telemetry fields
        for _ in range(15):
            w0, w1, w2, active = self.app.patterns.step(dt_s=0.020)
            self.app.drive.set_routine_wheels(w0, w1, w2, active=active)
            self.app.drive.tick(safety_ok=True)

        sc_t, tel = self._request("GET", "/api/telemetry", token=self.op_token)
        self.assertEqual(sc_t, 200)
        self.assertEqual(tel["active_routine"], "compass_box")
        self.assertGreater(tel["local_x_m"], 0.0)
        self.assertAlmostEqual(tel["local_y_m"], 0.0, places=3)
        self.assertGreater(tel["routine_progress"], 0.0)
        self.assertEqual(tel["drive"]["source"], "routine")

        # Stop via POST /api/routines/stop
        sc_s, stop_body = self._request("POST", "/api/routines/stop", {}, token=self.op_token)
        self.assertEqual(sc_s, 200)
        self.assertTrue(stop_body["ok"])
        self.assertIsNone(stop_body["routines"]["active_routine"])
        self.assertFalse(self.app.patterns.is_active())

    def test_manual_joystick_and_estop_immediately_cancel_active_routine(self):
        self.app.drive.set_armed(True)
        self._request(
            "POST", "/api/routines/start", {"routine": "compass_box"}, token=self.op_token
        )
        self.assertTrue(self.app.patterns.is_active())

        # Manual joystick twist cancels routine immediately
        self._request(
            "POST", "/api/drive/cmd", {"vx": 0.4, "vy": 0.0, "omega": 0.0}, token=self.op_token
        )
        self.assertFalse(self.app.patterns.is_active())
        self.assertEqual(self.app.patterns.stop_reason, "Manual joystick override")

        # Restart and verify E-STOP cancels routine and disarms motors
        self._request(
            "POST", "/api/routines/start", {"routine": "compass_box"}, token=self.op_token
        )
        self.assertTrue(self.app.patterns.is_active())
        self._request("POST", "/api/drive/estop", {}, token=self.op_token)
        self.assertFalse(self.app.patterns.is_active())
        self.assertFalse(self.app.drive.armed)

    def test_circle_http_disarmed_409_and_imu_battery_interlocks(self):
        # 1. Disarmed motors MUST return HTTP 409 Conflict and NEVER auto-arm (valid or invalid params)
        self.assertFalse(self.app.drive.armed)
        for payload in (
            {
                "routine": "circle",
                "preset": "indoor",
                "params": {"radius_m": 0.5, "laps": 1, "direction": "ccw", "orientation_mode": "tangent"},
            },
            {
                "routine": "circle",
                "preset": "outdoor",
                "params": {"radius_m": -5.0, "laps": True},
            },
        ):
            sc, body = self._request("POST", "/api/routines/start", payload, token=self.op_token)
            self.assertEqual(sc, 409)
            self.assertFalse(body["ok"])
            self.assertFalse(self.app.drive.armed)
            self.assertFalse(self.app.patterns.is_active())

        # 2. Armed, but 9DoF IMU offline or critical battery active -> HTTP 409 Conflict
        self.app.drive.set_armed(True)
        self.app.imu_state["online"] = False
        sc_imu, body_imu = self._request(
            "POST", "/api/routines/start", {"routine": "circle"}, token=self.op_token
        )
        self.assertEqual(sc_imu, 409)
        self.assertFalse(body_imu["ok"])
        self.assertFalse(self.app.patterns.is_active())
        self.app.imu_state["online"] = True

        self.app.battery.critical = True
        sc_bat, body_bat = self._request(
            "POST", "/api/routines/start", {"routine": "circle"}, token=self.op_token
        )
        self.assertEqual(sc_bat, 409)
        self.assertFalse(body_bat["ok"])
        self.assertFalse(self.app.patterns.is_active())
        self.app.battery.critical = False

    def test_circle_http_armed_invalid_payloads_rejected_with_http_400(self):
        self.app.drive.set_armed(True)
        bad_payloads = (
            {"routine": "circle", "preset": "indoor", "params": {"radius_m": 0.0}},
            {"routine": "circle", "preset": "indoor", "params": {"radius_m": -0.5}},
            {"routine": "circle", "preset": "indoor", "params": {"radius_m": 0.04}},
            {"routine": "circle", "preset": "outdoor", "params": {"radius_m": 10.5}},
            {"routine": "circle", "preset": "indoor", "params": {"radius_m": True}},
            {"routine": "circle", "preset": "indoor", "params": {"radius_m": float("nan")}},
            {"routine": "circle", "preset": "indoor", "params": {"radius_m": float("inf")}},
            {"routine": "circle", "preset": "indoor", "params": {"laps": -1}},
            {"routine": "circle", "preset": "indoor", "params": {"laps": 101}},
            {"routine": "circle", "preset": "indoor", "params": {"laps": True}},
            {"routine": "circle", "preset": "indoor", "params": {"laps": 1.5}},
            {"routine": "circle", "preset": "indoor", "params": {"direction": "diagonal"}},
            {"routine": "circle", "preset": "indoor", "params": {"orientation_mode": "upside_down"}},
            {"routine": "circle", "preset": "indoor", "params": {"speed_mps": 0.0}},
            {"routine": "circle", "preset": "indoor", "params": {"speed_mps": -0.2}},
            {"routine": "circle", "preset": "indoor", "params": {"speed_mps": False}},
            {"routine": "circle", "preset": "indoor", "params": {"speed_mps": float("-inf")}},
            {"routine": "circle", "preset": "stratosphere", "params": {"radius_m": 0.5}},
            {"routine": "unknown_trick", "preset": "indoor", "params": {}},
        )
        for bad in bad_payloads:
            with self.subTest(bad=bad):
                sc, body = self._request("POST", "/api/routines/start", bad, token=self.op_token)
                self.assertEqual(sc, 400)
                self.assertFalse(body["ok"])
                self.assertFalse(self.app.patterns.is_active())

        # Truncated / malformed JSON body -> HTTP 400 Bad Request
        sc_trunc, body_trunc = self._request_raw_bytes(
            "POST",
            "/api/routines/start",
            raw_body=b'{"routine": "circle", "params": {',
            token=self.op_token,
            content_len_override=64,
        )
        self.assertEqual(sc_trunc, 400)
        self.assertFalse(body_trunc["ok"])
        self.assertFalse(self.app.patterns.is_active())

    def test_circle_http_armed_start_telemetry_and_stop_lifecycle(self):
        self.app.drive.set_armed(True)
        sc, body = self._request(
            "POST",
            "/api/routines/start",
            {
                "routine": "circle",
                "preset": "indoor",
                "params": {
                    "radius_m": 0.15,
                    "laps": 2,
                    "direction": "cw",
                    "speed_mps": 0.45,
                    "orientation_mode": "orbit_inward",
                },
            },
            token=self.op_token,
        )
        self.assertEqual(sc, 200)
        self.assertTrue(body["ok"])
        rt = body["routines"]
        self.assertEqual(rt["active_routine"], "circle")
        self.assertEqual(rt["direction"], "cw")
        self.assertEqual(rt["orientation_mode"], "orbit_inward")
        # Centripetal clamp for r=0.15 indoor: sqrt(0.75 * 0.15) = 0.33541 -> 0.335 m/s
        self.assertAlmostEqual(rt["speed_target_mps"], round(math.sqrt(0.75 * 0.15), 3), places=3)
        self.assertIn("pulse", self.app.lights.modes)

        for _ in range(20):
            w0, w1, w2, active = self.app.patterns.step(dt_s=0.020)
            self.app.imu.heading_deg = (self.app.imu.heading_deg - self.app.patterns.cmd_omega_dps * 0.020) % 360.0
            self.app.imu.gz_dps = self.app.patterns.cmd_omega_dps
            self.app.drive.set_routine_wheels(w0, w1, w2, active=active)
            self.app.drive.tick(safety_ok=True)

        sc_t, tel = self._request("GET", "/api/telemetry", token=self.op_token)
        self.assertEqual(sc_t, 200)
        self.assertEqual(tel["active_routine"], "circle")
        self.assertGreater(tel["routine_progress"], 0.0)
        self.assertEqual(tel["drive"]["source"], "routine")

        sc_s, stop_body = self._request("POST", "/api/routines/stop", {}, token=self.op_token)
        self.assertEqual(sc_s, 200)
        self.assertTrue(stop_body["ok"])
        self.assertIsNone(stop_body["routines"]["active_routine"])
        self.assertFalse(self.app.patterns.is_active())


if __name__ == "__main__":
    unittest.main()
