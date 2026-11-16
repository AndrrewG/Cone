# ==============================================================================
# ARSE — Autonomous Road Systems Engineering
# Adversarial Desktop Verification Suite: Task 1.2 (HTTP Auth & Bearer Middleware)
# Target:      telemetry_server.py & main.py (IDEA-007)
# Platform:    Pimoroni Yukon (RP2040 / RP2350) — Desktop Python 3 Harness
# ==============================================================================

import ast
import asyncio
import base64
import inspect
import json
import os
import re
import tempfile
import unittest
from unittest import mock

from security.auth_manager import AuthManager
from telemetry_server import TelemetryServer


URLSAFE_TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{32}$")
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# ==============================================================================
# Deterministic Clock & Lightweight Subsystem Doubles (Zero Global Patching)
# ==============================================================================


class FakeClock:
    """Deterministic millisecond clock injected into AuthManager via time_ms_fn."""

    def __init__(self, start_ms=10_000):
        self.now_ms = int(start_ms)

    def __call__(self):
        return self.now_ms

    def advance(self, delta_ms):
        self.now_ms += int(delta_ms)
        return self.now_ms

    def set(self, value_ms):
        self.now_ms = int(value_ms)
        return self.now_ms


class FakeVoiceEngine:
    """Mock Conish VoiceEngine recording semantic say_event() triggers."""

    def __init__(self):
        self.events = []

    def say_event(self, event_name):
        self.events.append(event_name)


class FakeOverlayLights:
    """Mock Slot 4 50-LED controller recording mode changes and tamper overlays."""

    def __init__(self):
        self.mode = "off"
        self.brightness = 0.4
        self.overlays = []
        self.calls = []

    def set_temporary_overlay(self, mode, duration_ms):
        self.overlays.append((mode, int(duration_ms)))

    def set_mode(self, mode, brightness=None):
        self.calls.append(("set_mode", mode, brightness))
        self.mode = mode
        if brightness is not None:
            self.brightness = float(brightness)
        return self.get_status()

    def set_brightness(self, brightness):
        self.calls.append(("set_brightness", brightness))
        self.brightness = float(brightness)
        return self.get_status()

    def get_status(self):
        return {"mode": self.mode, "brightness": self.brightness}


class FakeYukon:
    def __init__(self, main_on=True):
        self.main_on = bool(main_on)

    def is_main_output_enabled(self):
        return self.main_on

    def enable_main_output(self):
        self.main_on = True

    def disable_main_output(self):
        self.main_on = False


class FakeDrive:
    """Mock 3-wheel holonomic DriveController enforcing explicit arming tracking."""

    def __init__(self):
        self.armed = False
        self.estop_latched = False
        self.speed_mode = "indoor"
        self.target_wheels = [0.0, 0.0, 0.0]
        self.calls = []
        self.set_armed_calls = []

    def set_armed(self, armed):
        val = bool(armed)
        self.calls.append(("set_armed", val))
        self.set_armed_calls.append(val)
        self.armed = val
        if val:
            self.estop_latched = False
        else:
            self.target_wheels = [0.0, 0.0, 0.0]
        return self.get_status()

    def set_speed_mode(self, mode):
        self.calls.append(("set_speed_mode", mode))
        if mode not in ("indoor", "normal", "turbo"):
            raise ValueError("Invalid speed mode")
        self.speed_mode = mode
        return self.get_status()

    def set_manual_twist(self, vx, vy, omega):
        self.calls.append(("set_manual_twist", vx, vy, omega))
        return abs(vx) > 0.05 or abs(vy) > 0.05 or abs(omega) > 0.05

    def set_bench_motor(self, channel, speed, duration_ms=0):
        self.calls.append(("set_bench_motor", channel, speed, duration_ms))
        return abs(speed) > 0.01

    def estop(self):
        self.calls.append(("estop",))
        self.armed = False
        self.estop_latched = True
        self.target_wheels = [0.0, 0.0, 0.0]
        return self.get_status()

    def get_status(self):
        return {
            "armed": self.armed,
            "estop_latched": self.estop_latched,
            "speed_mode": self.speed_mode,
            "wheels": list(self.target_wheels),
        }


class FakeAudio:
    def __init__(self):
        self.volume = 0.35
        self.playing = None
        self.calls = []

    def set_volume(self, volume):
        self.calls.append(("set_volume", volume))
        self.volume = float(volume)
        return self.get_status()

    def play(self, clip):
        self.calls.append(("play", clip))
        self.playing = clip
        return self.get_status()

    def stop(self):
        self.calls.append(("stop",))
        self.playing = None
        return self.get_status()

    def get_status(self):
        return {"volume": self.volume, "playing": self.playing}


class FakeBattery:
    def __init__(self):
        self.capacity_mah = 2200.0
        self.soc_percent = 85.0
        self.calls = []

    def mark_new_full_battery(self):
        self.calls.append(("mark_new_full_battery",))
        self.soc_percent = 100.0
        return self.get_status()

    def set_capacity_mah(self, cap):
        self.calls.append(("set_capacity_mah", cap))
        self.capacity_mah = float(cap)
        return self.get_status()

    def set_current_soc(self, soc):
        self.calls.append(("set_current_soc", soc))
        self.soc_percent = float(soc)
        return self.get_status()

    def dismiss_fresh_hint(self):
        self.calls.append(("dismiss_fresh_hint",))

    def save(self):
        self.calls.append(("save",))

    def get_status(self):
        return {
            "voltage_v": 11.8,
            "soc_percent": self.soc_percent,
            "capacity_mah": self.capacity_mah,
            "is_critical": False,
        }


class FakeIMU:
    def __init__(self):
        self.mag_online = True
        self.calls = []

    def start_calibration(self):
        self.calls.append(("start_calibration",))
        return {"ok": True, "calibrating": True}

    def save_calibration(self):
        self.calls.append(("save_calibration",))
        return {"ok": True, "calibrated": True}

    def cancel_calibration(self):
        self.calls.append(("cancel_calibration",))
        return {"ok": True, "calibrating": False}


class FakeGPS:
    def __init__(self, has_fix=True):
        self.has_fix = bool(has_fix)

    def get_status(self):
        return {
            "has_fix": self.has_fix,
            "satellites": 8 if self.has_fix else 0,
            "lat_e7": 515073500,
            "lon_e7": -1277580,
        }


class FakeNav:
    def __init__(self, active=False):
        self.active = bool(active)
        self.home = {"lat_e7": 515073500, "lon_e7": -1277580}
        self.calls = []

    def is_active(self):
        return bool(self.active)

    def pause(self, reason=""):
        self.calls.append(("pause", reason))
        self.active = False
        return {"ok": True, "state": "PAUSED"}

    def stop(self):
        self.calls.append(("stop",))
        self.active = False
        return {"ok": True, "state": "IDLE"}

    def start(self, current_heading_deg=0.0):
        self.calls.append(("start", current_heading_deg))
        self.active = True
        return {"ok": True, "state": "RUNNING"}

    def clear_waypoints(self):
        self.calls.append(("clear_waypoints",))
        return {"ok": True}

    def import_mission(self, mission_obj, gps_status=None):
        self.calls.append(("import_mission", mission_obj))
        return {"ok": True, "imported": 0}

    def pop_light_cmd(self):
        return None

    def get_status(self, gps_st=None):
        return {
            "state": "RUNNING" if self.active else "IDLE",
            "waypoint_count": 0,
        }


class FakeSentry:
    def __init__(self, enabled=False):
        self.enabled = bool(enabled)
        self.sensitivity = "medium"
        self.calls = []

    def is_enabled(self):
        return bool(self.enabled)

    def set_sensitivity(self, level):
        self.calls.append(("set_sensitivity", level))
        self.sensitivity = str(level)

    def arm(self):
        self.calls.append(("arm",))
        self.enabled = True
        return {"ok": True, "sentry": self.get_status()}

    def disarm(self):
        self.calls.append(("disarm",))
        self.enabled = False
        return {"ok": True, "sentry": self.get_status()}

    def get_status(self):
        return {"enabled": self.enabled, "sensitivity": self.sensitivity}


class FakeWiFi:
    def get_status(self):
        return {"mode": "STA", "ssid": "ARSE_Bench_WiFi", "ip": "192.168.50.13"}


class MockFollowController:
    """Duck-typed Follow-Me controller recording scoped beacon & stamp calls."""

    def __init__(self):
        self.calls = []

    def handle_beacon(self, data):
        self.calls.append(("handle_beacon", data))
        return {"ok": True, "source": "http_beacon"}

    def ingest_beacon(self, data):
        self.calls.append(("ingest_beacon", data))
        return {"ok": True, "source": "http_beacon"}

    def update_beacon(self, data):
        self.calls.append(("update_beacon", data))
        return {"ok": True, "source": "http_beacon"}

    def on_beacon(self, data):
        self.calls.append(("on_beacon", data))
        return {"ok": True, "source": "http_beacon"}

    def beacon(self, data):
        self.calls.append(("beacon", data))
        return {"ok": True, "source": "http_beacon"}

    def handle_stamp(self, data=None):
        self.calls.append(("handle_stamp", data))
        return {"ok": True, "stamped": True}

    def record_stamp(self, data=None):
        self.calls.append(("record_stamp", data))
        return {"ok": True, "stamped": True}

    def add_stamp(self, data=None):
        self.calls.append(("add_stamp", data))
        return {"ok": True, "stamped": True}

    def stamp(self, data=None):
        self.calls.append(("stamp", data))
        return {"ok": True, "stamped": True}

    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)

        def _recorder(*args, **kwargs):
            self.calls.append((name, args, kwargs))
            return {"ok": True, "method": name}

        return _recorder


class FakeConeApp:
    """Minimal ConeApp test double exposing all subsystem interfaces."""

    def __init__(self, auth_manager=None, lights=None):
        self.yukon = FakeYukon(main_on=True)
        self.battery = FakeBattery()
        self.lights = lights or FakeOverlayLights()
        self.audio = FakeAudio()
        self.drive = FakeDrive()
        self.nav = FakeNav(active=False)
        self.imu = FakeIMU()
        self.gps = FakeGPS(has_fix=True)
        self.sentry = FakeSentry(enabled=False)
        self.wifi = FakeWiFi()
        self.auth = auth_manager
        self.follow = None
        self.imu_state = {
            "online": True,
            "mag_online": True,
            "heading_deg": 42.5,
            "pitch_deg": 0.2,
            "roll_deg": -0.1,
            "tilt_fault": False,
        }

    def get_telemetry_snapshot(self):
        gps_st = self.gps.get_status()
        snap = {
            "battery": self.battery.get_status(),
            "drive": self.drive.get_status(),
            "lights": self.lights.get_status(),
            "audio": self.audio.get_status(),
            "imu": dict(self.imu_state),
            "gps": gps_st,
            "nav": self.nav.get_status(gps_st),
            "sentry": self.sentry.get_status(),
            "wifi": self.wifi.get_status(),
            "system": {
                "uptime_s": 120,
                "board_temp_c": 31.4,
                "main_output_enabled": self.yukon.is_main_output_enabled(),
                "mem_free_kb": 96.2,
            },
        }
        if self.auth is not None:
            snap["auth"] = self.auth.get_public_status()
        return snap


# ==============================================================================
# Base Async Loopback Server Harness (Ephemeral Port 0 on 127.0.0.1)
# ==============================================================================


class _MemoryReader:
    """In-memory async StreamReader requiring zero OS sockets or event loops."""

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
    """In-memory async StreamWriter recording response bytes and peer IP."""

    def __init__(self, peer_ip="127.0.0.1", peer_port=54321):
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
    """Step an async coroutine deterministically without socket.socketpair()."""
    import telemetry_server as _ts_mod

    pending_tasks = []

    async def _instant_wait_for(aw, timeout=None):
        return await aw

    async def _instant_sleep(_delay=0):
        while pending_tasks:
            t_coro = pending_tasks.pop(0)
            _step_raw(t_coro)
        return None

    def _instant_create_task(t_coro):
        pending_tasks.append(t_coro)
        return t_coro

    def _step_raw(c):
        while True:
            try:
                c.send(None)
            except StopIteration as stop:
                return stop.value

    with (
        mock.patch.object(_ts_mod.asyncio, "wait_for", side_effect=_instant_wait_for),
        mock.patch.object(_ts_mod.asyncio, "create_task", side_effect=_instant_create_task),
        mock.patch.object(asyncio, "sleep", side_effect=_instant_sleep),
    ):
        result = _step_raw(coro)
        while pending_tasks:
            _step_raw(pending_tasks.pop(0))
        return result


class BaseMiddlewareAsyncTestCase(unittest.TestCase):
    """Deterministic zero-socket HTTP/1.1 harness exercising TelemetryServer."""

    CREATE_AUTH_JS = True

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        for attr_name, attr_val in list(cls.__dict__.items()):
            if attr_name.startswith("test_") and inspect.iscoroutinefunction(attr_val):
                def _make_sync(async_fn):
                    def _wrapped(self):
                        return _drive_coro(async_fn(self))
                    _wrapped.__name__ = async_fn.__name__
                    _wrapped.__doc__ = async_fn.__doc__
                    return _wrapped
                setattr(cls, attr_name, _make_sync(attr_val))

    def setUp(self):
        _drive_coro(self.asyncSetUp())

    def tearDown(self):
        _drive_coro(self.asyncTearDown())

    async def asyncSetUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.web_dir = os.path.join(self._tmp.name, "web")
        os.makedirs(self.web_dir, exist_ok=True)

        self.html_bytes = (
            b"<!DOCTYPE html><html><head><title>ARSE Operator Login</title></head>"
            b"<body><h1>ARSE Mk.I Terminal</h1></body></html>\n"
        )
        self.html_path = os.path.join(self.web_dir, "index.html")
        with open(self.html_path, "wb") as f:
            f.write(self.html_bytes)

        self.auth_js_bytes = (
            b"// ARSE Mk.I Bearer Session Wrapper\n"
            b"window.apiFetch = async function(u, o) { return fetch(u, o); };\n"
        )
        self.auth_js_path = os.path.join(self.web_dir, "auth.js")
        if self.CREATE_AUTH_JS:
            with open(self.auth_js_path, "wb") as f:
                f.write(self.auth_js_bytes)

        self.secrets_path = os.path.join(self._tmp.name, "secrets.json")
        with open(self.secrets_path, "w", encoding="utf-8") as f:
            json.dump(
                {
                    "ssid": "ARSE_Field_WPA2",
                    "password": "SuperSecretWifiPassphrase!2026",
                },
                f,
            )

        self.clock = FakeClock(start_ms=10_000)
        self.voice = FakeVoiceEngine()
        self.lights = FakeOverlayLights()
        self.auth = AuthManager(
            secrets_path=self.secrets_path,
            voice_engine=self.voice,
            led_controller=self.lights,
            time_ms_fn=self.clock,
            print_bootstrap=False,
        )
        self.password = self.auth.bootstrapped_password
        self.app = FakeConeApp(auth_manager=self.auth, lights=self.lights)

        self.server = TelemetryServer(
            app=self.app,
            html_path=self.html_path,
            port=0,
            auth_manager=self.auth,
        )

        class _FakeBoundSocket:
            def getsockname(self):
                return ("0.0.0.0", 49152)

        class _FakeAsyncServer:
            sockets = [_FakeBoundSocket()]

            def close(self):
                pass

            async def wait_closed(self):
                return None

        async def _fake_start_server(cb, host, port, backlog=8):
            return _FakeAsyncServer()

        import telemetry_server as _ts_mod

        with mock.patch.object(_ts_mod.asyncio, "start_server", side_effect=_fake_start_server):
            self._tcp_server = await self.server.start()
        self.bound_port = self._tcp_server.sockets[0].getsockname()[1]

    async def asyncTearDown(self):
        if getattr(self, "_tcp_server", None) is not None:
            self._tcp_server.close()
            try:
                await self._tcp_server.wait_closed()
            except Exception:
                pass
        self._tmp.cleanup()

    async def http_request(
        self,
        method="GET",
        target="/",
        headers=None,
        json_body=None,
        raw_body=None,
        timeout=5.0,
        client_ip="127.0.0.1",
    ):
        """Execute a wire-formatted HTTP/1.1 request through TelemetryServer._handle_client."""
        hdrs = {}
        if headers:
            hdrs.update(headers)

        if json_body is not None:
            body_bytes = json.dumps(json_body).encode("utf-8")
            hdrs.setdefault("Content-Type", "application/json")
        elif raw_body is not None:
            body_bytes = (
                raw_body.encode("utf-8") if isinstance(raw_body, str) else bytes(raw_body)
            )
        else:
            body_bytes = b""

        if (body_bytes or method.upper() == "POST") and "Content-Length" not in hdrs:
            hdrs["Content-Length"] = str(len(body_bytes))
        hdrs.setdefault("Host", f"127.0.0.1:{self.bound_port}")
        hdrs.setdefault("Connection", "close")

        req_lines = [f"{method} {target} HTTP/1.1\r\n"]
        for k, v in hdrs.items():
            req_lines.append(f"{k}: {v}\r\n")
        req_lines.append("\r\n")
        wire_bytes = "".join(req_lines).encode("utf-8") + body_bytes

        reader = _MemoryReader(wire_bytes)
        writer = _MemoryWriter(peer_ip=client_ip)
        await self.server._handle_client(reader, writer)
        return self._parse_http_response(bytes(writer.buf))

    @staticmethod
    def _parse_http_response(raw_resp):
        if not raw_resp:
            raise AssertionError("Server closed connection without sending an HTTP response")
        head_bytes, sep, body_bytes = raw_resp.partition(b"\r\n\r\n")
        if not sep:
            raise AssertionError(f"Malformed HTTP response missing CRLFCRLF: {raw_resp!r}")

        lines = head_bytes.decode("iso-8859-1").split("\r\n")
        status_parts = lines[0].split(" ", 2)
        if len(status_parts) < 2 or not status_parts[0].startswith("HTTP/"):
            raise AssertionError(f"Invalid HTTP status line: {lines[0]!r}")
        status_code = int(status_parts[1])
        reason = status_parts[2] if len(status_parts) > 2 else ""

        headers_lower = {}
        for hline in lines[1:]:
            if ":" in hline:
                hk, hv = hline.split(":", 1)
                headers_lower[hk.strip().lower()] = hv.strip()

        parsed_json = None
        if body_bytes:
            ctype = headers_lower.get("content-type", "").lower()
            if "application/json" in ctype or body_bytes[:1] in (b"{", b"["):
                try:
                    parsed_json = json.loads(body_bytes.decode("utf-8"))
                except Exception:
                    parsed_json = None

        return {
            "status": status_code,
            "reason": reason,
            "headers": headers_lower,
            "body": body_bytes,
            "json": parsed_json,
        }

    async def login_operator(self, username="admin", password=None):
        pw = self.password if password is None else password
        resp = await self.http_request(
            "POST",
            "/api/auth/login",
            json_body={"username": username, "password": pw},
        )
        self.assertEqual(resp["status"], 200, msg=resp)
        tok = resp["json"]["session_token"]
        # Reset voice events after helper login so tests observe subsequent events cleanly
        self.voice.events.clear()
        return tok


# ==============================================================================
# 1. Static Constructor & MicroPython / Hardware Guardrail Audit
# ==============================================================================


class MiddlewareConstructorAndGuardrailAuditTests(unittest.TestCase):
    """Static contract and architectural guardrail checks for Task 1.2."""

    def test_telemetry_server_constructor_signature_and_defaults(self):
        sig = inspect.signature(TelemetryServer.__init__)
        params = list(sig.parameters.values())
        param_names = [p.name for p in params]
        self.assertEqual(
            param_names[:5],
            ["self", "app", "html_path", "port", "auth_manager"],
        )
        self.assertEqual(sig.parameters["html_path"].default, "web/index.html")
        self.assertEqual(sig.parameters["port"].default, 80)
        self.assertIsNone(sig.parameters["auth_manager"].default)

    def test_production_modules_obey_micropython_and_planned_hardware_guardrails(self):
        target_files = [
            os.path.join(REPO_ROOT, "telemetry_server.py"),
            os.path.join(REPO_ROOT, "main.py"),
        ]
        forbidden_modules = {
            # [PLANNED] hardware in HARDWARE.md Section 3
            "vl53l5cx",
            "dual_tof",
            "espnow",
            "mesh_transport",
            "swarm_node",
            "formation_planner",
            # CPython-only / MicroPython-incompatible modules
            "secrets",
            "hmac",
            "cryptography",
            "bcrypt",
            "argon2",
            "ssl",
            "numpy",
            "scipy",
            "http",
            "urllib",
            "requests",
            "flask",
            "fastapi",
            "aiohttp",
            "websockets",
        }

        for file_path in target_files:
            with open(file_path, "r", encoding="utf-8") as f:
                tree = ast.parse(f.read(), filename=file_path)

            imported = set()
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        imported.update(alias.name.split("."))
                elif isinstance(node, ast.ImportFrom) and node.module:
                    imported.update(node.module.split("."))

            overlap = imported & forbidden_modules
            self.assertEqual(
                overlap,
                set(),
                f"Forbidden imports in {os.path.basename(file_path)}: {sorted(overlap)}",
            )

    def test_main_py_wires_auth_manager_without_auto_arming_or_secret_leak(self):
        main_path = os.path.join(REPO_ROOT, "main.py")
        with open(main_path, "r", encoding="utf-8") as f:
            source = f.read()
        tree = ast.parse(source, filename=main_path)

        # 1. AuthManager must be imported and instantiated in main.py
        has_auth_import = False
        has_auth_call = False
        has_telemetry_auth_arg = False

        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module in (
                "security",
                "security.auth_manager",
            ):
                if any(alias.name == "AuthManager" for alias in node.names):
                    has_auth_import = True
            elif isinstance(node, ast.Call):
                func_name = None
                if isinstance(node.func, ast.Name):
                    func_name = node.func.id
                elif isinstance(node.func, ast.Attribute):
                    func_name = node.func.attr

                if func_name == "AuthManager":
                    has_auth_call = True
                elif func_name == "TelemetryServer":
                    if len(node.args) >= 4 or any(
                        kw.arg == "auth_manager" for kw in node.keywords
                    ):
                        has_telemetry_auth_arg = True
                elif func_name == "set_armed":
                    # Firmware boot/executive must NEVER call set_armed(True)
                    for arg in node.args:
                        if isinstance(arg, ast.Constant) and arg.value is True:
                            self.fail("main.py must NEVER auto-arm drive motors!")

        self.assertTrue(has_auth_import, "main.py must import AuthManager")
        self.assertTrue(has_auth_call, "main.py must instantiate AuthManager")
        self.assertTrue(
            has_telemetry_auth_arg,
            "main.py must pass auth_manager into TelemetryServer(...)",
        )

        # 2. get_telemetry_snapshot must never expose secret attributes
        forbidden_snapshot_attrs = {
            "get_beacon_token",
            "_beacon_token",
            "_password_hash_hex",
            "_password_salt_hex",
            "bootstrapped_password",
        }
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "get_telemetry_snapshot":
                for sub in ast.walk(node):
                    if isinstance(sub, ast.Attribute):
                        self.assertNotIn(
                            sub.attr,
                            forbidden_snapshot_attrs,
                            f"get_telemetry_snapshot must not access {sub.attr}",
                        )


# ==============================================================================
# 2. Unauthenticated Public Shell & Health Endpoint Tests
# ==============================================================================


class PublicRoutesAndHealthTests(BaseMiddlewareAsyncTestCase):
    """Verifies public UI shell routes, /auth.js, 204 probes, and /api/health."""

    async def test_ephemeral_port_zero_binds_and_serves_root_html_unauthenticated(self):
        self.assertGreater(self.bound_port, 0)

        for target in ("/", "/index.html", "/?v=1"):
            with self.subTest(target=target):
                resp = await self.http_request("GET", target)
                self.assertEqual(resp["status"], 200)
                self.assertIn("text/html", resp["headers"].get("content-type", "").lower())
                self.assertEqual(resp["body"], self.html_bytes)

    async def test_auth_js_served_unauthenticated_when_present_alongside_html(self):
        for target in ("/auth.js", "/web/auth.js", "/auth.js?v=1"):
            with self.subTest(target=target):
                resp = await self.http_request("GET", target)
                self.assertEqual(resp["status"], 200)
                self.assertIn(
                    "application/javascript",
                    resp["headers"].get("content-type", "").lower(),
                )
                self.assertEqual(resp["body"], self.auth_js_bytes)

    async def test_auth_js_returns_404_not_401_when_absent(self):
        os.remove(self.auth_js_path)
        orig_cwd = os.getcwd()
        with tempfile.TemporaryDirectory() as empty_cwd:
            os.chdir(empty_cwd)
            try:
                for target in ("/auth.js", "/web/auth.js"):
                    with self.subTest(target=target):
                        resp = await self.http_request("GET", target)
                        self.assertNotIn(resp["status"], (401, 403))
                        self.assertEqual(resp["status"], 404)
            finally:
                os.chdir(orig_cwd)

    async def test_favicon_and_captive_portal_generate_204_unauthenticated(self):
        for target in ("/favicon.ico", "/generate_204"):
            with self.subTest(target=target):
                resp = await self.http_request("GET", target)
                self.assertEqual(resp["status"], 204)
                self.assertEqual(resp["body"], b"")

    async def test_api_health_tracks_lock_status_and_leaks_zero_secrets_or_telemetry(self):
        # 1. Locked when no operator session exists
        r_locked = await self.http_request("GET", "/api/health")
        self.assertEqual(r_locked["status"], 200)
        self.assertIsNotNone(r_locked["json"])
        self.assertIs(r_locked["json"].get("online"), True)
        self.assertIs(r_locked["json"].get("locked"), True)

        # Beacon token is NOT an operator session -> cone must still report locked: True
        _ = self.auth.get_beacon_token()
        r_still_locked = await self.http_request("GET", "/api/health")
        self.assertIs(r_still_locked["json"].get("locked"), True)

        # 2. Unlocked once an operator logs in
        op_tok = await self.login_operator()
        r_unlocked = await self.http_request("GET", "/api/health")
        self.assertEqual(r_unlocked["status"], 200)
        self.assertIs(r_unlocked["json"].get("online"), True)
        self.assertIs(r_unlocked["json"].get("locked"), False)

        # 3. Verify zero telemetry keys and zero secrets leak in /api/health
        with open(self.secrets_path, "r", encoding="utf-8") as f:
            disk_secrets = json.load(f)

        combined_raw = (r_locked["body"] + b"\n" + r_unlocked["body"]).decode("utf-8")
        forbidden_keys = {
            "battery",
            "drive",
            "lights",
            "audio",
            "imu",
            "gps",
            "nav",
            "sentry",
            "wifi",
            "system",
            "session_token",
            "beacon_token",
            "password_hash_hex",
            "password_salt_hex",
        }
        self.assertEqual(set(r_locked["json"].keys()) & forbidden_keys, set())
        self.assertEqual(set(r_unlocked["json"].keys()) & forbidden_keys, set())

        for secret_str in (
            self.password,
            op_tok,
            self.auth.get_beacon_token(),
            disk_secrets["password_salt_hex"],
            disk_secrets["password_hash_hex"],
            disk_secrets["password"],
            disk_secrets["ssid"],
        ):
            self.assertNotIn(secret_str, combined_raw)

        # 4. Re-locks automatically once operator session expires
        self.clock.advance(AuthManager.SESSION_TTL_MS)
        r_expired = await self.http_request("GET", "/api/health")
        self.assertEqual(r_expired["status"], 200)
        self.assertIs(r_expired["json"].get("online"), True)
        self.assertIs(r_expired["json"].get("locked"), True)


# ==============================================================================
# 3. Login Endpoint, Safety Interlocks, Malformed Payloads & 429 Rate-Limiting
# ==============================================================================


class LoginAndRateLimitMiddlewareTests(BaseMiddlewareAsyncTestCase):
    """Adversarial tests for POST /api/auth/login and 5-attempt IP lockout."""

    async def test_valid_login_returns_contract_payload_and_never_arms_motors(self):
        self.assertFalse(self.app.drive.armed)

        resp = await self.http_request(
            "POST",
            "/api/auth/login",
            json_body={"username": "admin", "password": self.password},
        )
        self.assertEqual(resp["status"], 200)
        body = resp["json"]
        self.assertIsNotNone(body)
        self.assertIs(body.get("ok"), True)
        self.assertIs(body.get("authenticated"), True)
        self.assertEqual(body.get("username"), "admin")
        self.assertEqual(body.get("expires_in_s"), 3600)
        self.assertRegex(body.get("session_token", ""), URLSAFE_TOKEN_RE)
        self.assertEqual(
            self.auth.validate_bearer(body["session_token"]), "operator"
        )
        self.assertEqual(self.voice.events, ["ACK_COMMAND"])

        # NON-BYPASSABLE SAFETY INVARIANT: Login MUST NEVER auto-arm drive motors
        self.assertFalse(self.app.drive.armed)
        self.assertEqual(self.app.drive.set_armed_calls, [])
        self.assertEqual(self.app.drive.calls, [])

    async def test_invalid_credentials_return_401_with_www_authenticate_bearer(self):
        bad_vectors = [
            {"username": "admin", "password": "wrong_password_123"},
            {"username": "intruder", "password": self.password},
            {"username": "", "password": self.password},
            {"username": "admin", "password": ""},
        ]
        for idx, payload in enumerate(bad_vectors):
            # Advance > 60s between cases so we test pure 401 before rate-limit lockout
            if idx > 0:
                self.clock.advance(AuthManager.FAIL_WINDOW_MS + 1_000)
            with self.subTest(idx=idx, payload=payload):
                resp = await self.http_request(
                    "POST", "/api/auth/login", json_body=payload
                )
                self.assertEqual(resp["status"], 401)
                self.assertIn(
                    "bearer",
                    resp["headers"].get("www-authenticate", "").lower(),
                )
                self.assertIsNotNone(resp["json"])
                self.assertIs(resp["json"].get("authenticated"), False)
                self.assertNotIn("session_token", resp["json"])
                self.assertFalse(self.app.drive.armed)

    async def test_login_handles_malformed_non_object_and_oversized_payloads_safely(self):
        # 1. Syntax-broken JSON and non-object JSON primitives/arrays -> 400 Bad Request
        for bad_raw in (
            b"{truncated_json",
            b"not_json_at_all",
            b'["admin", "secret"]',
            b'"just_a_string"',
            b"12345",
            b"\xff\xfe\x00\x01",
        ):
            with self.subTest(bad_raw=bad_raw):
                resp = await self.http_request(
                    "POST",
                    "/api/auth/login",
                    headers={"Content-Type": "application/json"},
                    raw_body=bad_raw,
                )
                self.assertEqual(resp["status"], 400)

        # 2. Oversized login payloads (> 512 bytes) -> 413 Payload Too Large or 400 Bad Request
        for size in (513, 1024, 8192):
            with self.subTest(oversize_bytes=size):
                big_json = json.dumps(
                    {"username": "admin", "password": "A" * size}
                ).encode("utf-8")
                self.assertGreater(len(big_json), 512)
                resp = await self.http_request(
                    "POST",
                    "/api/auth/login",
                    headers={"Content-Type": "application/json"},
                    raw_body=big_json,
                )
                self.assertIn(resp["status"], (400, 413))

        # 3. Server remains alive and responsive for subsequent valid login
        self.clock.advance(AuthManager.FAIL_WINDOW_MS + 5_000)
        valid_resp = await self.http_request(
            "POST",
            "/api/auth/login",
            json_body={"username": "admin", "password": self.password},
        )
        self.assertEqual(valid_resp["status"], 200)
        self.assertIs(valid_resp["json"].get("authenticated"), True)

    async def test_brute_force_5_failures_returns_429_retry_after_and_blocks_valid_password(self):
        # Attempts 1..4 within 60 s -> 401 Unauthorized (no lockout yet)
        for attempt in range(1, 5):
            self.clock.advance(2_000)
            r = await self.http_request(
                "POST",
                "/api/auth/login",
                json_body={"username": "admin", "password": f"bad_{attempt}"},
            )
            self.assertEqual(r["status"], 401)
            self.assertEqual(self.lights.overlays, [])
            self.assertEqual(self.voice.events, [])

        # 5th failed attempt within 60 s -> 429 Too Many Requests + Retry-After: 300 + tamper alerts
        self.clock.advance(2_000)
        r5 = await self.http_request(
            "POST",
            "/api/auth/login",
            json_body={"username": "admin", "password": "bad_5"},
        )
        self.assertEqual(r5["status"], 429)
        self.assertEqual(r5["headers"].get("retry-after"), "300")
        if r5["json"] is not None:
            self.assertIsNot(r5["json"].get("authenticated"), True)
            self.assertNotIn("session_token", r5["json"])

        # Strobe LED + fault audio hook MUST have fired
        self.assertTrue(
            self.lights.overlays == [("strobe", 1500)]
            or ("set_mode", "strobe", None) in self.lights.calls,
            "5th failed login must trigger strobe LED alert",
        )
        self.assertEqual(self.voice.events, ["ERR_SYSTEM_FAULT"])

        # Attempt 6 (+120 s into lockout, 180 s left) EVEN WITH VALID PASSWORD -> still 429!
        self.clock.advance(120_000)
        r_valid_during_lock = await self.http_request(
            "POST",
            "/api/auth/login",
            json_body={"username": "admin", "password": self.password},
        )
        self.assertEqual(r_valid_during_lock["status"], 429)
        retry_hdr = r_valid_during_lock["headers"].get("retry-after")
        self.assertIsNotNone(retry_hdr)
        self.assertEqual(int(retry_hdr), 180)
        if r_valid_during_lock["json"] is not None:
            self.assertNotIn("session_token", r_valid_during_lock["json"])

        # Cone must still report locked: True
        r_health = await self.http_request("GET", "/api/health")
        self.assertIs(r_health["json"].get("locked"), True)

        # Advance remaining 180 s (total 300 s elapsed) -> valid login succeeds again!
        self.clock.advance(180_000)
        r_unlocked = await self.http_request(
            "POST",
            "/api/auth/login",
            json_body={"username": "admin", "password": self.password},
        )
        self.assertEqual(r_unlocked["status"], 200)
        self.assertRegex(r_unlocked["json"]["session_token"], URLSAFE_TOKEN_RE)

    async def test_per_ip_peername_isolation_locks_attacker_without_blocking_operator(self):
        """Exercises _handle_client with distinct peername IPs to verify real IP extraction."""

        async def _stream_login(ip_addr, pw_str):
            body = json.dumps({"username": "admin", "password": pw_str}).encode("utf-8")
            raw_req = (
                b"POST /api/auth/login HTTP/1.1\r\n"
                b"Host: 192.168.4.1\r\n"
                b"Content-Type: application/json\r\n"
                + f"Content-Length: {len(body)}\r\n\r\n".encode("ascii")
                + body
            )
            reader = _MemoryReader(raw_req)

            class _PeerWriter:
                def __init__(self, peer_ip):
                    self.peer = (peer_ip, 54000)
                    self.buf = bytearray()
                    self.closed = False

                def get_extra_info(self, name, default=None):
                    if name == "peername":
                        return self.peer
                    return default

                def write(self, data):
                    self.buf.extend(data)

                async def drain(self):
                    pass

                def close(self):
                    self.closed = True

                async def wait_closed(self):
                    pass

            writer = _PeerWriter(ip_addr)
            await self.server._handle_client(reader, writer)
            return self._parse_http_response(bytes(writer.buf))

        # Lock out attacker IP 192.168.4.66
        for _ in range(4):
            res = await _stream_login("192.168.4.66", "wrong_pw")
            self.assertEqual(res["status"], 401)
        res_lock = await _stream_login("192.168.4.66", "wrong_pw")
        self.assertEqual(res_lock["status"], 429)

        # Operator on 192.168.4.99 is NOT locked out and logs in cleanly
        res_op = await _stream_login("192.168.4.99", self.password)
        self.assertEqual(res_op["status"], 200)
        self.assertIs(res_op["json"].get("authenticated"), True)

        # Attacker on 192.168.4.66 remains locked out even with valid password
        res_attacker_retry = await _stream_login("192.168.4.66", self.password)
        self.assertEqual(res_attacker_retry["status"], 429)


# ==============================================================================
# 4. Unauthenticated Rejection (401) & Query-Token Rejection Tests
# ==============================================================================


class UnauthenticatedRouteProtectionTests(BaseMiddlewareAsyncTestCase):
    """Verifies 401 Unauthorized + WWW-Authenticate: Bearer on all protected routes."""

    PROTECTED_ROUTE_MATRIX = (
        ("GET", "/api/telemetry", None),
        ("GET", "/api/auth/beacon", None),
        ("POST", "/api/drive/cmd", {"vx": 0.6, "vy": 0.0, "omega": 0.0}),
        ("POST", "/api/drive/bench", {"channel": 1, "speed": 0.5, "duration_ms": 500}),
        ("POST", "/api/drive/config", {"armed": True, "speed_mode": "turbo"}),
        ("POST", "/api/drive/estop", {}),
        ("POST", "/api/lights", {"mode": "police", "brightness": 1.0}),
        ("POST", "/api/audio", {"sound": "siren", "volume": 1.0}),
        ("POST", "/api/battery/config", {"capacity_mah": 5000, "soc_percent": 99}),
        ("POST", "/api/battery/new_full", {}),
        ("POST", "/api/battery/dismiss_hint", {}),
        ("POST", "/api/imu/cal", {"action": "start"}),
        ("POST", "/api/nav/action", {"action": "clear"}),
        ("POST", "/api/nav/upload", {"schema": "arse-mission/v1", "waypoints": []}),
        ("POST", "/api/sentry", {"action": "arm"}),
        (
            "POST",
            "/api/ota/chunk",
            {"path": "unauth_pwn.py", "mode": "wb", "b64": "cHduZWQ="},
        ),
        ("POST", "/api/ota/reboot", {}),
        ("POST", "/api/auth/logout", {}),
        (
            "POST",
            "/api/auth/credentials",
            {"username": "hacker", "password": "HackedPassword!99"},
        ),
        ("POST", "/api/follow/beacon", {"lat": 51.50735, "lon": -0.12776, "acc": 3.0}),
        ("POST", "/api/follow/stamp", {}),
        ("GET", "/ws/follow", None),
    )

    async def test_all_protected_routes_reject_missing_malformed_and_bogus_headers_with_401(self):
        self.app.follow = MockFollowController()
        bad_header_sets = [
            {},
            {"Authorization": ""},
            {"Authorization": "Bearer"},
            {"Authorization": "Bearer    "},
            {"Authorization": "Basic YWRtaW46cGFzc3dvcmQ="},
            {"Authorization": "Bearer bogus_nonexistent_token_0123456789"},
        ]

        for hdrs in bad_header_sets:
            for method, path, body in self.PROTECTED_ROUTE_MATRIX:
                extra_hdrs = dict(hdrs)
                if path == "/ws/follow":
                    extra_hdrs.update(
                        {
                            "Upgrade": "websocket",
                            "Connection": "Upgrade",
                            "Sec-WebSocket-Key": "dGhlIHNhbXBsZSBub25jZQ==",
                            "Sec-WebSocket-Version": "13",
                        }
                    )
                with self.subTest(method=method, path=path, headers=hdrs):
                    resp = await self.http_request(
                        method, path, headers=extra_hdrs, json_body=body
                    )
                    self.assertEqual(resp["status"], 401)
                    self.assertIn(
                        "bearer",
                        resp["headers"].get("www-authenticate", "").lower(),
                    )

        # Verify ZERO actuator / state mutations occurred across the entire cone
        self.assertFalse(self.app.drive.armed)
        self.assertEqual(self.app.drive.calls, [])
        self.assertEqual(self.app.lights.calls, [])
        self.assertEqual(self.app.audio.calls, [])
        self.assertEqual(self.app.battery.calls, [])
        self.assertEqual(self.app.imu.calls, [])
        self.assertEqual(self.app.nav.calls, [])
        self.assertEqual(self.app.sentry.calls, [])
        self.assertEqual(self.app.follow.calls, [])
        self.assertFalse(os.path.exists("unauth_pwn.py"))
        self.assertEqual(self.auth.username, "admin")

    async def test_expired_session_token_returns_401_with_www_authenticate(self):
        tok = await self.login_operator()
        auth_hdr = {"Authorization": f"Bearer {tok}"}

        r_ok = await self.http_request("GET", "/api/telemetry", headers=auth_hdr)
        self.assertEqual(r_ok["status"], 200)

        # Advance clock past 3600 s rolling TTL
        self.clock.advance(AuthManager.SESSION_TTL_MS)
        for method, path, body in (
            ("GET", "/api/telemetry", None),
            ("POST", "/api/drive/config", {"armed": True}),
            ("POST", "/api/auth/logout", {}),
        ):
            with self.subTest(method=method, path=path):
                resp = await self.http_request(
                    method, path, headers=auth_hdr, json_body=body
                )
                self.assertEqual(resp["status"], 401)
                self.assertIn(
                    "bearer",
                    resp["headers"].get("www-authenticate", "").lower(),
                )
        self.assertFalse(self.app.drive.armed)

    async def test_operator_session_token_in_query_string_rejected_on_operator_routes(self):
        tok = await self.login_operator()

        query_targets = [
            ("GET", f"/api/telemetry?token={tok}", None),
            ("GET", f"/api/auth/beacon?token={tok}", None),
            ("POST", f"/api/drive/config?token={tok}", {"armed": True}),
            ("POST", f"/api/drive/cmd?token={tok}", {"vx": 0.5, "vy": 0.0, "omega": 0.0}),
            (
                "POST",
                f"/api/ota/chunk?token={tok}",
                {"path": "query_pwn.py", "mode": "wb", "b64": "cHdu"},
            ),
            ("POST", f"/api/auth/logout?token={tok}", {}),
        ]
        for method, target, body in query_targets:
            with self.subTest(method=method, target=target):
                resp = await self.http_request(method, target, json_body=body)
                self.assertEqual(resp["status"], 401)
                self.assertIn(
                    "bearer",
                    resp["headers"].get("www-authenticate", "").lower(),
                )

        # Motors stayed disarmed and operator session was NOT logged out via query string
        self.assertFalse(self.app.drive.armed)
        self.assertFalse(os.path.exists("query_pwn.py"))
        self.assertEqual(self.auth.validate_bearer(tok), "operator")


# ==============================================================================
# 5. "beacon_only" Least-Privilege Scope Isolation (403 Forbidden)
# ==============================================================================


class BeaconOnlyScopeIsolationTests(BaseMiddlewareAsyncTestCase):
    """Verifies beacon_token can ONLY POST to /api/follow/beacon and /api/follow/stamp."""

    async def test_beacon_token_allowed_on_follow_beacon_and_stamp_via_header_and_query(self):
        beacon_tok = self.auth.get_beacon_token()
        beacon_hdr = {"Authorization": f"Bearer {beacon_tok}"}

        # 1. When app.follow is None -> middleware passes auth, route returns 404 (NOT 401/403!)
        self.app.follow = None
        for target, hdrs in (
            ("/api/follow/beacon", beacon_hdr),
            ("/api/follow/stamp", beacon_hdr),
            (f"/api/follow/beacon?token={beacon_tok}", None),
            (f"/api/follow/stamp?token={beacon_tok}", None),
        ):
            with self.subTest(follow_attached=False, target=target):
                resp = await self.http_request(
                    "POST",
                    target,
                    headers=hdrs,
                    json_body={"lat": 51.50735, "lon": -0.12776, "acc": 2.5},
                )
                self.assertNotIn(resp["status"], (401, 403))
                self.assertEqual(resp["status"], 404)

        # 2. When mock app.follow is attached -> returns 200 OK and invokes follow controller
        mock_follow = MockFollowController()
        self.app.follow = mock_follow

        for target, hdrs in (
            ("/api/follow/beacon", beacon_hdr),
            ("/api/follow/stamp", beacon_hdr),
            (f"/api/follow/beacon?token={beacon_tok}", None),
            (f"/api/follow/stamp?token={beacon_tok}", None),
        ):
            with self.subTest(follow_attached=True, target=target):
                before_calls = len(mock_follow.calls)
                resp = await self.http_request(
                    "POST",
                    target,
                    headers=hdrs,
                    json_body={"lat": 51.50735, "lon": -0.12776, "acc": 2.5},
                )
                self.assertEqual(resp["status"], 200)
                self.assertGreater(len(mock_follow.calls), before_calls)

        self.assertFalse(self.app.drive.armed)

    async def test_beacon_token_returns_403_forbidden_on_every_other_route_and_method(self):
        op_tok = await self.login_operator()
        beacon_tok = self.auth.get_beacon_token()
        beacon_hdr = {"Authorization": f"Bearer {beacon_tok}"}
        self.app.follow = MockFollowController()

        forbidden_targets = [
            ("GET", "/api/telemetry", beacon_hdr, None),
            ("GET", "/api/auth/beacon", beacon_hdr, None),
            ("POST", "/api/drive/config", beacon_hdr, {"armed": True}),
            ("POST", "/api/drive/cmd", beacon_hdr, {"vx": 0.5, "vy": 0.0, "omega": 0.0}),
            ("POST", "/api/drive/bench", beacon_hdr, {"channel": 1, "speed": 0.4}),
            ("POST", "/api/drive/estop", beacon_hdr, {}),
            ("POST", "/api/lights", beacon_hdr, {"mode": "strobe"}),
            ("POST", "/api/audio", beacon_hdr, {"sound": "meep"}),
            ("POST", "/api/battery/config", beacon_hdr, {"soc_percent": 50}),
            ("POST", "/api/battery/new_full", beacon_hdr, {}),
            ("POST", "/api/imu/cal", beacon_hdr, {"action": "start"}),
            ("POST", "/api/nav/action", beacon_hdr, {"action": "start"}),
            ("POST", "/api/nav/upload", beacon_hdr, {"waypoints": []}),
            ("POST", "/api/sentry", beacon_hdr, {"action": "disarm"}),
            (
                "POST",
                "/api/ota/chunk",
                beacon_hdr,
                {"path": "beacon_pwn.py", "mode": "wb", "b64": "cHdu"},
            ),
            ("POST", "/api/ota/reboot", beacon_hdr, {}),
            ("POST", "/api/auth/logout", beacon_hdr, {}),
            (
                "POST",
                "/api/auth/credentials",
                beacon_hdr,
                {"username": "hijack", "password": "HijackPassword!99"},
            ),
            (
                "GET",
                "/ws/follow",
                {
                    "Authorization": f"Bearer {beacon_tok}",
                    "Upgrade": "websocket",
                    "Connection": "Upgrade",
                },
                None,
            ),
            (
                "GET",
                f"/ws/follow?token={beacon_tok}",
                {"Upgrade": "websocket", "Connection": "Upgrade"},
                None,
            ),
            # Wrong HTTP method on follow routes must ALSO return 403 Forbidden!
            ("GET", "/api/follow/beacon", beacon_hdr, None),
            ("GET", "/api/follow/stamp", beacon_hdr, None),
            # Non-whitelisted /api/follow/* sub-routes must ALSO return 403 Forbidden!
            ("POST", "/api/follow/config", beacon_hdr, {"mode": "escort"}),
            ("POST", "/api/follow/save_mission", beacon_hdr, {"name": "route"}),
        ]

        for method, target, hdrs, body in forbidden_targets:
            with self.subTest(method=method, target=target):
                resp = await self.http_request(
                    method, target, headers=hdrs, json_body=body
                )
                self.assertEqual(resp["status"], 403, msg=f"{method} {target} -> {resp}")

        # Verify zero side-effects: motors disarmed, operator session intact, credentials intact
        self.assertFalse(self.app.drive.armed)
        self.assertEqual(self.app.drive.calls, [])
        self.assertEqual(self.app.lights.calls, [])
        self.assertEqual(self.app.audio.calls, [])
        self.assertEqual(self.app.follow.calls, [])
        self.assertFalse(os.path.exists("beacon_pwn.py"))
        self.assertEqual(self.auth.validate_bearer(op_tok), "operator")
        self.assertEqual(self.auth.username, "admin")


# ==============================================================================
# 6. Logout Disarm Interlock Tests (POST /api/auth/logout)
# ==============================================================================


class LogoutDisarmInterlockTests(BaseMiddlewareAsyncTestCase):
    """Verifies session clearing and mission-aware motor disarm on logout."""

    async def test_logout_disarms_motors_and_clears_all_sessions_when_idle(self):
        s1 = await self.login_operator()
        s2 = await self.login_operator()

        # Operator explicitly arms drive motors while nav is idle
        r_arm = await self.http_request(
            "POST",
            "/api/drive/config",
            headers={"Authorization": f"Bearer {s1}"},
            json_body={"armed": True},
        )
        self.assertEqual(r_arm["status"], 200)
        self.assertTrue(self.app.drive.armed)
        self.assertFalse(self.app.nav.is_active())

        # Logout from s1 must clear ALL operator sessions AND disarm motors
        r_logout = await self.http_request(
            "POST",
            "/api/auth/logout",
            headers={"Authorization": f"Bearer {s1}"},
            json_body={},
        )
        self.assertEqual(r_logout["status"], 200)
        body = r_logout["json"]
        self.assertIs(body.get("ok"), True)
        self.assertIs(body.get("motors_disarmed"), True)
        self.assertFalse(self.app.drive.armed)

        # Both s1 and s2 are revoked; /api/health reports locked: True
        self.assertIsNone(self.auth.validate_bearer(s1))
        self.assertIsNone(self.auth.validate_bearer(s2))
        r_health = await self.http_request("GET", "/api/health")
        self.assertIs(r_health["json"].get("locked"), True)

        r_after = await self.http_request(
            "GET", "/api/telemetry", headers={"Authorization": f"Bearer {s2}"}
        )
        self.assertEqual(r_after["status"], 401)

    async def test_logout_preserves_motor_arming_when_autonomous_mission_is_active(self):
        s1 = await self.login_operator()
        s2 = await self.login_operator()

        # Simulate an active autonomous waypoint mission with armed motors
        self.app.drive.set_armed(True)
        self.app.nav.active = True
        self.app.drive.calls.clear()
        self.app.drive.set_armed_calls.clear()

        r_logout = await self.http_request(
            "POST",
            "/api/auth/logout",
            headers={"Authorization": f"Bearer {s1}"},
            json_body={},
        )
        self.assertEqual(r_logout["status"], 200)
        body = r_logout["json"]
        self.assertIs(body.get("ok"), True)
        self.assertGreaterEqual(body.get("sessions_cleared", 0), 2)
        self.assertIs(body.get("motors_disarmed"), False)
        self.assertIs(body.get("mission_active"), True)

        # Motors MUST remain armed mid-mission, while all RAM sessions are cleared!
        self.assertTrue(self.app.drive.armed)
        self.assertEqual(self.app.drive.calls, [])
        self.assertIsNone(self.auth.validate_bearer(s1))
        self.assertIsNone(self.auth.validate_bearer(s2))

        r_health = await self.http_request("GET", "/api/health")
        self.assertIs(r_health["json"].get("locked"), True)


# ==============================================================================
# 7. Credential Rotation, OTA Path Traversal & Telemetry Secret Redaction
# ==============================================================================


class CredentialsOtaAndRedactionTests(BaseMiddlewareAsyncTestCase):
    """Verifies /api/auth/credentials, /api/auth/beacon, /api/ota/*, and redaction."""

    async def test_credential_rotation_revokes_sessions_rotates_beacon_and_rejects_weak_pw(self):
        self.app.follow = MockFollowController()
        s1 = await self.login_operator()
        hdr1 = {"Authorization": f"Bearer {s1}"}

        # 1. Operator can read scoped beacon_token via GET /api/auth/beacon
        r_beacon = await self.http_request("GET", "/api/auth/beacon", headers=hdr1)
        self.assertEqual(r_beacon["status"], 200)
        beacon_initial = r_beacon["json"].get("beacon_token")
        self.assertEqual(beacon_initial, self.auth.get_beacon_token())

        # 2. Weak (< 8 char) passwords and invalid usernames rejected with 400 Bad Request
        for bad_cred in (
            {"username": "admin", "password": "short77"},
            {"username": "admin", "password": ""},
            {"username": "bad space", "password": "ValidPassword!2026"},
            {"username": "", "password": "ValidPassword!2026"},
        ):
            with self.subTest(bad_cred=bad_cred):
                r_bad = await self.http_request(
                    "POST", "/api/auth/credentials", headers=hdr1, json_body=bad_cred
                )
                self.assertEqual(r_bad["status"], 400)

        # Session s1 remains valid after rejected credential change
        self.assertEqual(self.auth.validate_bearer(s1), "operator")

        # 3. Valid username/password update WITHOUT beacon rotation
        r_upd = await self.http_request(
            "POST",
            "/api/auth/credentials",
            headers=hdr1,
            json_body={
                "username": "highway_ops",
                "password": "StrongFieldPassword!42",
                "rotate_beacon": False,
            },
        )
        self.assertEqual(r_upd["status"], 200)
        self.assertIs(r_upd["json"].get("ok"), True)
        self.assertEqual(r_upd["json"].get("username"), "highway_ops")
        self.assertIs(r_upd["json"].get("beacon_rotated"), False)
        self.assertFalse(self.app.drive.armed)

        # Old session s1 is immediately revoked (401)
        r_old_sess = await self.http_request("GET", "/api/telemetry", headers=hdr1)
        self.assertEqual(r_old_sess["status"], 401)

        # Old login fails (401); new login succeeds (200)
        r_old_login = await self.http_request(
            "POST",
            "/api/auth/login",
            json_body={"username": "admin", "password": self.password},
        )
        self.assertEqual(r_old_login["status"], 401)

        s2 = await self.login_operator(
            username="highway_ops", password="StrongFieldPassword!42"
        )
        hdr2 = {"Authorization": f"Bearer {s2}"}

        # Initial beacon_token still works when rotate_beacon=False
        r_b_ok = await self.http_request(
            "POST",
            "/api/follow/beacon",
            headers={"Authorization": f"Bearer {beacon_initial}"},
            json_body={"lat": 51.5, "lon": -0.1, "acc": 2.0},
        )
        self.assertEqual(r_b_ok["status"], 200)

        # 4. Update credentials WITH rotate_beacon=True -> invalidates old beacon_token!
        r_rot = await self.http_request(
            "POST",
            "/api/auth/credentials",
            headers=hdr2,
            json_body={
                "username": "highway_ops",
                "password": "RotatedFieldPassword!99",
                "rotate_beacon": True,
            },
        )
        self.assertEqual(r_rot["status"], 200)
        self.assertIs(r_rot["json"].get("beacon_rotated"), True)

        # Old beacon_token is now rejected with 401 Unauthorized!
        r_b_old = await self.http_request(
            "POST",
            "/api/follow/beacon",
            headers={"Authorization": f"Bearer {beacon_initial}"},
            json_body={"lat": 51.5, "lon": -0.1, "acc": 2.0},
        )
        self.assertEqual(r_b_old["status"], 401)

        # Re-login and verify new beacon_token works
        s3 = await self.login_operator(
            username="highway_ops", password="RotatedFieldPassword!99"
        )
        r_b_new_info = await self.http_request(
            "GET", "/api/auth/beacon", headers={"Authorization": f"Bearer {s3}"}
        )
        self.assertEqual(r_b_new_info["status"], 200)
        beacon_new = r_b_new_info["json"]["beacon_token"]
        self.assertNotEqual(beacon_new, beacon_initial)

        r_b_new = await self.http_request(
            "POST",
            "/api/follow/beacon",
            headers={"Authorization": f"Bearer {beacon_new}"},
            json_body={"lat": 51.5, "lon": -0.1, "acc": 2.0},
        )
        self.assertEqual(r_b_new["status"], 200)

    async def test_ota_chunk_writes_valid_relative_files_and_rejects_path_traversal(self):
        tok = await self.login_operator()
        hdr = {"Authorization": f"Bearer {tok}"}

        orig_cwd = os.getcwd()
        with tempfile.TemporaryDirectory() as ota_dir:
            os.chdir(ota_dir)
            try:
                # 1. Valid top-level write + append + nested subdirectory creation
                part1 = b"# ARSE OTA line 1\n"
                part2 = b"VALUE = 42\n"
                r1 = await self.http_request(
                    "POST",
                    "/api/ota/chunk",
                    headers=hdr,
                    json_body={
                        "path": "ota_test.py",
                        "mode": "wb",
                        "b64": base64.b64encode(part1).decode("ascii"),
                    },
                )
                self.assertEqual(r1["status"], 200)

                r2 = await self.http_request(
                    "POST",
                    "/api/ota/chunk",
                    headers=hdr,
                    json_body={
                        "path": "ota_test.py",
                        "mode": "ab",
                        "b64": base64.b64encode(part2).decode("ascii"),
                    },
                )
                self.assertEqual(r2["status"], 200)
                with open(os.path.join(ota_dir, "ota_test.py"), "rb") as f:
                    self.assertEqual(f.read(), part1 + part2)

                nested_payload = b"def helper():\n    return True\n"
                r_nested = await self.http_request(
                    "POST",
                    "/api/ota/chunk",
                    headers=hdr,
                    json_body={
                        "path": "drivers/sub_mod/helper.py",
                        "mode": "wb",
                        "b64": base64.b64encode(nested_payload).decode("ascii"),
                    },
                )
                self.assertEqual(r_nested["status"], 200)
                with open(
                    os.path.join(ota_dir, "drivers", "sub_mod", "helper.py"), "rb"
                ) as f:
                    self.assertEqual(f.read(), nested_payload)

                # 2. Path traversal, absolute paths, dotfiles, drive letters & secrets -> 400!
                malicious_paths = [
                    "",
                    ".",
                    "..",
                    "../secrets.json",
                    "..\\secrets.json",
                    "drivers/../../escape.py",
                    "/etc/passwd",
                    "/flash/secrets.json",
                    ".git/config",
                    ".github/workflows/ci.yml",
                    "C:\\Windows\\win.ini",
                    "C:/temp/evil.py",
                    "secrets.json",
                ]
                for bad_path in malicious_paths:
                    with self.subTest(bad_path=bad_path):
                        r_bad = await self.http_request(
                            "POST",
                            "/api/ota/chunk",
                            headers=hdr,
                            json_body={
                                "path": bad_path,
                                "mode": "wb",
                                "b64": base64.b64encode(b"pwned").decode("ascii"),
                            },
                        )
                        self.assertEqual(
                            r_bad["status"],
                            400,
                            msg=f"Malicious OTA path {bad_path!r} was not rejected with 400!",
                        )
            finally:
                os.chdir(orig_cwd)

        # 3. Verify operator POST /api/ota/reboot schedules _deferred_reboot cleanly
        reboot_scheduled = []

        async def _fake_deferred_reboot():
            reboot_scheduled.append(True)

        with mock.patch.object(
            self.server, "_deferred_reboot", side_effect=_fake_deferred_reboot
        ):
            r_reb = await self.http_request(
                "POST", "/api/ota/reboot", headers=hdr, json_body={}
            )
            self.assertEqual(r_reb["status"], 200)
            await asyncio.sleep(0)
            self.assertEqual(reboot_scheduled, [True])

    async def test_telemetry_endpoint_never_leaks_tokens_hashes_salts_or_passwords(self):
        tok = await self.login_operator()
        resp = await self.http_request(
            "GET",
            "/api/telemetry",
            headers={"Authorization": f"Bearer {tok}"},
        )
        self.assertEqual(resp["status"], 200)
        data = resp["json"]
        self.assertIsNotNone(data)
        for expected_section in (
            "battery",
            "drive",
            "lights",
            "audio",
            "imu",
            "gps",
            "nav",
            "sentry",
            "wifi",
            "system",
        ):
            self.assertIn(expected_section, data)

        raw_str = resp["body"].decode("utf-8")
        for forbidden_key in (
            "beacon_token",
            "password_hash_hex",
            "password_salt_hex",
            "session_token",
        ):
            self.assertNotIn(f'"{forbidden_key}"', raw_str)

        with open(self.secrets_path, "r", encoding="utf-8") as f:
            disk_secrets = json.load(f)

        for secret_val in (
            self.password,
            tok,
            self.auth.get_beacon_token(),
            disk_secrets["password_salt_hex"],
            disk_secrets["password_hash_hex"],
            disk_secrets["password"],
        ):
            self.assertNotIn(secret_val, raw_str)


if __name__ == "__main__":
    unittest.main()
