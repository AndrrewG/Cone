# ==============================================================================
# ARSE — Autonomous Road Systems Engineering
# Desktop Verification Suite: Task 2.1 (50 Hz Dead-Reckoning Core & compass_box)
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
        step_fn = None
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    self.assertNotIn(alias.name.split(".")[0].lower(), forbidden_imports)
            elif isinstance(node, ast.ImportFrom) and node.module:
                self.assertNotIn(node.module.split(".")[0].lower(), forbidden_imports)
            elif isinstance(node, ast.FunctionDef) and node.name == "step":
                step_fn = node

        self.assertIsNotNone(step_fn)
        for sub in ast.walk(step_fn):
            self.assertNotIsInstance(
                sub,
                (ast.List, ast.Dict, ast.ListComp, ast.DictComp, ast.GeneratorExp),
                "50 Hz PatternRunner.step() must not allocate lists, dicts, or comprehensions",
            )


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

    def _request(self, method, path, body=None, token=None):
        raw_body = b"" if body is None else json.dumps(body).encode("utf-8")
        hdrs = [f"{method} {path} HTTP/1.1", "Host: cone.local"]
        if token is not None:
            hdrs.append(f"Authorization: Bearer {token}")
        if body is not None:
            hdrs.append("Content-Type: application/json")
            hdrs.append(f"Content-Length: {len(raw_body)}")
        raw_req = ("\r\n".join(hdrs) + "\r\n\r\n").encode("utf-8") + raw_body

        reader = _MemoryReader(raw_req)
        writer = _MemoryWriter()
        _drive_coro(self.server._handle_client(reader, writer))
        resp_bytes = bytes(writer.buf)
        head, payload = resp_bytes.split(b"\r\n\r\n", 1)
        status_code = int(head.splitlines()[0].split()[1])
        return status_code, json.loads(payload.decode("utf-8"))

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


if __name__ == "__main__":
    unittest.main()
