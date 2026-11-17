import sys
import time
import types
import unittest


def _install_micropython_mocks():
    # Patch MicroPython time helpers onto standard CPython time module
    if not hasattr(time, "ticks_ms"):
        time._mock_ms = 1000
        time.ticks_ms = lambda: time._mock_ms
        time.ticks_add = lambda t, d: t + int(d)
        time.ticks_diff = lambda a, b: a - b
        time.sleep_ms = lambda ms: setattr(time, "_mock_ms", time._mock_ms + int(ms))

    # Mock machine.I2S
    machine_mod = types.ModuleType("machine")

    class FakeI2S:
        TX = 1
        MONO = 1
        events = []

        def __init__(self, i2s_id, **kwargs):
            self.i2s_id = i2s_id
            self.kwargs = kwargs
            self._irq = None
            self._deinited = False
            FakeI2S.events.append("i2s_init")

        def irq(self, handler):
            self._irq = handler
            if handler is None:
                FakeI2S.events.append("i2s_irq_clear")

        def write(self, buf):
            if self._deinited:
                raise RuntimeError("Use-after-free I2S write after deinit!")
            FakeI2S.events.append(("i2s_write", len(buf)))
            return len(buf)

        def deinit(self):
            self._deinited = True
            FakeI2S.events.append("i2s_deinit")

    machine_mod.I2S = FakeI2S
    sys.modules["machine"] = machine_mod

    # Mock network.WLAN with RP2040 CYW43 constants
    network_mod = types.ModuleType("network")

    class FakeWLAN:
        STA_IF = 0
        AP_IF = 1
        PM_NONE = 0xA11140
        PM_PERFORMANCE = 0xA11142
        PM_POWERSAVE = 0x00111022

        def __init__(self, iface):
            self.iface = iface
            self.pm_history = []
            self._connected = True

        def active(self, val=None):
            return True

        def isconnected(self):
            return self._connected

        def config(self, *args, **kwargs):
            if "pm" in kwargs:
                self.pm_history.append(kwargs["pm"])
            return b"\x28\xcd\xc1\x01\x02\x03"

        def ifconfig(self):
            return ("192.168.50.13", "255.255.255.0", "192.168.50.1", "192.168.50.1")

    network_mod.WLAN = FakeWLAN
    sys.modules["network"] = network_mod


_install_micropython_mocks()

from drivers.audio_sfx import ConeAudio  # noqa: E402
from drivers.sentry import SentryGuard  # noqa: E402
from drivers.wifi_manager import ConeWiFi  # noqa: E402
from telemetry_server import TelemetryServer  # noqa: E402


class FakePin:
    def __init__(self, val=0):
        self.val = val

    def value(self, v=None):
        if v is not None:
            self.val = 1 if v else 0
        return self.val


class FakeSlot:
    def __init__(self):
        self.FAST1 = FakePin()
        self.FAST2 = FakePin()
        self.FAST3 = FakePin()
        self.SLOW1 = FakePin(0)


class FakeAudioAmpModule:
    # Mimics Pimoroni AudioAmpModule name-mangled calls to self.player
    def __init__(self):
        self.slot = FakeSlot()
        self.player = None
        self.regs = []

    def is_enabled(self):
        return self.slot.SLOW1.value() == 1

    def enable(self):
        sys.modules["machine"].I2S.events.append("amp_enable")
        self.slot.SLOW1.value(1)
        self.write_i2c_reg(0x02, 0x80)
        self.player._AudioAmpModule__start_i2s()

    def disable(self):
        if self.is_enabled():
            self.player._AudioAmpModule__stop_i2s()
        self.slot.SLOW1.value(0)
        sys.modules["machine"].I2S.events.append("amp_disable")

    def write_i2c_reg(self, reg, val):
        self.regs.append((reg, val))
        sys.modules["machine"].I2S.events.append(("i2c", reg, val))


class FakeYukon:
    def __init__(self, main_on=True):
        self.main_on = main_on
        self.leds = {"A": False, "B": False}

    def is_main_output_enabled(self):
        return self.main_on

    def enable_main_output(self):
        self.main_on = True

    def disable_main_output(self):
        self.main_on = False

    def set_led(self, name, state):
        self.leds[name] = bool(state)


class FakeIMU:
    def __init__(self, yukon, audio):
        self.yukon = yukon
        self.audio = audio
        self.last_accel_g = (0.0, 0.0, 1.0)
        self.last_gyro_dps = (0.0, 0.0, 0.0)
        self.next_motion = ((0.0, 0.0, 1.0), (0.0, 0.0, 0.0))
        self.snapshot_states = []

    def read_motion_fast(self):
        # Record whether amp & main output are already off when baseline is sampled
        self.snapshot_states.append(
            (self.yukon.is_main_output_enabled(), self.audio.amp.is_enabled())
        )
        self.last_accel_g, self.last_gyro_dps = self.next_motion
        return self.next_motion


class FakeLights:
    def __init__(self):
        self.mode = "off"
        self.brightness = 0.4

    def set_mode(self, mode, brightness=None):
        self.mode = mode
        if brightness is not None:
            self.brightness = brightness
        return {"mode": self.mode, "brightness": self.brightness}


class FakeDrive:
    def __init__(self):
        self.armed = True
        self.estop_calls = 0

    def estop(self):
        self.armed = False
        self.estop_calls += 1


class FakeBattery:
    def __init__(self):
        self.logic_idle_amps = 0.045

    def get_status(self):
        return {"battery_present": True, "is_critical": False}


class SentryAndAudioTests(unittest.TestCase):
    def setUp(self):
        time._mock_ms = 1000
        sys.modules["machine"].I2S.events.clear()

    def test_persistent_i2s_never_deinits_and_keeps_amp_warm_before_idle_timeout(self):
        yukon = FakeYukon(main_on=True)
        amp_mod = FakeAudioAmpModule()
        audio = ConeAudio(amp_mod, yukon, default_volume=0.35)

        # I2S(0) must be allocated once at boot before TAS2780 enable()
        self.assertEqual(audio._i2s.kwargs.get("ibuf"), ConeAudio.I2S_IBUF_BYTES)

        st = audio.play("chirp")
        self.assertEqual(st["playing"], "chirp")
        self.assertTrue(st["amp_enabled"])

        events = sys.modules["machine"].I2S.events
        i2s_idx = events.index("i2s_init")
        amp_idx = events.index("amp_enable")
        self.assertLess(
            i2s_idx,
            amp_idx,
            "I2S bitclock must be running before TAS2780 enable() to prevent clock-fault mute",
        )

        # Simulate DMA latching note 0, then advancing to note 1: must NOT overwrite in-flight buffer
        note0_snapshot = bytes(audio._tone_buf)
        inflight_idx = audio._dma_active_idx
        audio._i2s_irq(None)
        self.assertEqual(audio._dma_inflight_idx, inflight_idx)

        time._mock_ms += 70
        audio.tick()
        self.assertNotEqual(
            audio._dma_active_idx,
            inflight_idx,
            "Next chirp note must synthesise into a separate buffer while previous note is in-flight",
        )
        self.assertEqual(
            bytes(audio._dma_bufs[inflight_idx][: len(note0_snapshot)]),
            note0_snapshot,
            "In-flight DMA buffer must not be overwritten mid-transfer",
        )

        # Finish chirp and check amp stays warm at +4s (< 15s IDLE_SHUTDOWN_MS)
        audio.stop()
        time._mock_ms += 4000
        audio.tick()
        self.assertTrue(amp_mod.is_enabled())

        # Advance past IDLE_SHUTDOWN_MS -> 3-stage non-blocking pop-free ramp before SDZ drops
        events.clear()
        time._mock_ms += audio.IDLE_SHUTDOWN_MS
        audio.tick()  # Stage 1: DVC soft-mute + Active Muted (SDZ still high)
        self.assertTrue(amp_mod.is_enabled())
        self.assertIn(("i2c", 0x1A, 0xCF), events)
        self.assertIn(("i2c", 0x02, 0x81), events)

        time._mock_ms += audio.SOFT_RAMP_STEP_MS
        audio.tick()  # Stage 2: Software Shutdown anti-pop H-bridge ramp (SDZ still high)
        self.assertTrue(amp_mod.is_enabled())
        self.assertIn(("i2c", 0x02, 0x02), events)

        time._mock_ms += audio.SOFT_RAMP_STEP_MS
        audio.tick()  # Stage 3: H-bridge now Hi-Z -> pull SDZ low cleanly
        audio._i2s_irq(None)
        self.assertIsNone(audio.active_sound)
        self.assertFalse(amp_mod.is_enabled())
        self.assertLess(events.index(("i2c", 0x1A, 0xCF)), events.index(("i2c", 0x02, 0x02)))
        self.assertLess(events.index(("i2c", 0x02, 0x02)), events.index("amp_disable"))
        self.assertNotIn(
            "i2s_deinit",
            events,
            "I2S.deinit() must never run at runtime because it disables RP2040 DMA_IRQ_0 used by CYW43 Wi-Fi",
        )

    def test_wifi_power_save_avoids_aggressive_cyw43_sleep(self):
        wifi = ConeWiFi()
        wlan_cls = sys.modules["network"].WLAN
        wifi.mode = "STA"
        wifi._sta = wlan_cls(wlan_cls.STA_IF)

        wifi.set_power_save(True)
        wifi.set_power_save(False)

        self.assertEqual(wifi._sta.pm_history[0], wlan_cls.PM_PERFORMANCE)
        self.assertNotEqual(wifi._sta.pm_history[0], wlan_cls.PM_POWERSAVE)
        self.assertEqual(wifi._sta.pm_history[1], wlan_cls.PM_NONE)

    def test_sentry_lifecycle_and_vibration_free_rearm_baseline(self):
        yukon = FakeYukon(main_on=True)
        amp_mod = FakeAudioAmpModule()
        audio = ConeAudio(amp_mod, yukon, default_volume=0.35)
        imu = FakeIMU(yukon, audio)
        lights = FakeLights()
        drive = FakeDrive()
        wifi = ConeWiFi()
        wlan_cls = sys.modules["network"].WLAN
        wifi.mode = "STA"
        wifi._sta = wlan_cls(wlan_cls.STA_IF)
        battery = FakeBattery()

        sentry = SentryGuard(
            yukon=yukon,
            imu=imu,
            lights=lights,
            audio=audio,
            drive=drive,
            nav=None,
            wifi=wifi,
            battery=battery,
            arm_delay_ms=3000,
            alarm_hold_ms=12000,
        )

        # 1. Arm -> plays confirmation chirp & disarms drive
        res = sentry.arm()
        self.assertTrue(res["ok"])
        self.assertEqual(sentry.state, SentryGuard.STATE_ARMING)
        self.assertFalse(drive.armed)
        self.assertEqual(audio.active_sound, "chirp")

        # 2. Advance past 3s arming window -> enters low-power WATCH
        time._mock_ms += 3100
        imu.snapshot_states.clear()
        sentry.update()
        self.assertEqual(sentry.state, SentryGuard.STATE_WATCH)
        self.assertFalse(yukon.is_main_output_enabled())
        self.assertFalse(amp_mod.is_enabled())
        # Resting baseline must be sampled AFTER rail & speaker are off
        self.assertEqual(imu.snapshot_states[0], (False, False))

        # 3. Simulate cone snatch -> triggers POLICE ALARM
        imu.next_motion = ((0.6, 0.0, 0.8), (55.0, 0.0, 0.0))
        sentry.update()
        self.assertEqual(sentry.state, SentryGuard.STATE_ALARM)
        self.assertEqual(sentry.alarm_count, 1)
        self.assertTrue(yukon.is_main_output_enabled())
        self.assertEqual(lights.mode, "police")
        self.assertEqual(audio.active_sound, "siren")

        # 4. Cone set down motionless for 12s -> auto-rearms cleanly to WATCH
        imu.next_motion = ((0.1, 0.0, 0.99), (0.0, 0.0, 0.0))
        imu.last_accel_g = (0.1, 0.0, 0.99)
        imu.last_gyro_dps = (0.0, 0.0, 0.0)
        sentry._prev_alarm_accel = (0.1, 0.0, 0.99)
        time._mock_ms += 12500
        imu.snapshot_states.clear()
        sentry.update()
        self.assertEqual(sentry.state, SentryGuard.STATE_WATCH)
        self.assertEqual(imu.snapshot_states[-1], (False, False))
        self.assertNotIn("i2s_deinit", sys.modules["machine"].I2S.events)

    def test_html_streaming_uses_preallocated_buffer_for_full_web_ui(self):
        import os

        html_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "web",
            "index.html",
        )
        with open(html_path, "rb") as hf:
            expected_bytes = hf.read()

        class FakeWriter:
            def __init__(self):
                self.chunks = bytearray()

            def write(self, data):
                self.chunks.extend(data)

            async def drain(self):
                pass

        srv = TelemetryServer(app=None, html_path=html_path, port=80)
        writer = FakeWriter()
        coro = srv._send_html_file(writer)
        try:
            while True:
                coro.send(None)
        except StopIteration:
            pass

        raw = bytes(writer.chunks)
        hdr, body = raw.split(b"\r\n\r\n", 1)
        self.assertIn(b"HTTP/1.1 200 OK", hdr)
        self.assertEqual(len(body), len(expected_bytes))
        self.assertEqual(body, expected_bytes)
        self.assertFalse(srv._html_busy)

    def test_pa1010d_readfrom_into_discards_idle_lf_and_parses_gnrmc(self):
        from drivers.pa1010d import PA1010D

        class FakeI2C:
            def __init__(self, frames):
                self.frames = list(frames)

            def readfrom_into(self, addr, buf):
                frame = self.frames.pop(0) if self.frames else (b"\n" * len(buf))
                buf[: len(frame)] = frame

        nmea = b"$GNRMC,123519,A,5130.3000,N,00007.5000,W,0.0,0.0,151126,,,A*"
        csum = 0
        for b in nmea[1:-1]:
            csum ^= b
        full = nmea + f"{csum:02X}\r\n".encode("ascii")
        chunk1 = full[:48]
        chunk2 = full[48:] + b"\n" * (48 - len(full[48:]))

        i2c = FakeI2C([b"\n" * 48, chunk1, chunk2, b"\n" * 48])
        gps = PA1010D(i2c)

        # Idle 0x0A frame leaves buffer empty and returns False
        self.assertFalse(gps.update())
        self.assertEqual(gps.buffer, "")

        # Split GNRMC across two 48-byte frames parses cleanly into 1e-7 deg integers
        self.assertFalse(gps.update())
        self.assertTrue(gps.update())
        self.assertTrue(gps.has_fix)
        self.assertEqual(gps.lat_e7, 515050000)
        self.assertEqual(gps.lon_e7, -1250000)

        # Subsequent idle frame stays empty
        self.assertFalse(gps.update())
        self.assertEqual(gps.buffer, "")


class TestChassisGeometryAndImuMounting(unittest.TestCase):
    def test_cad_chassis_geometry_wheel_angles_and_motor_polarity(self):
        import math
        import config
        from drivers.omni_kinematics import Omni3Kinematics
        from drivers.drive_controller import DriveController

        self.assertAlmostEqual(config.ROBOT_BASE_RADIUS_M, 0.12088, places=5)
        self.assertEqual(config.WHEEL_ANGLES_DEG, (60.0, 300.0, 180.0))

        kin = Omni3Kinematics(
            wheel_radius_m=config.WHEEL_RADIUS_M,
            base_radius_m=config.ROBOT_BASE_RADIUS_M,
            max_speed_mps=config.MAX_LINEAR_SPEED_MPS,
            wheel_angles_deg=config.WHEEL_ANGLES_DEG,
        )
        # Pure forward surge (+X): Wheel A (FL) < 0, Wheel B (FR) > 0, Wheel C (Rear) == 0
        wa, wb, wc = kin.compute_wheel_speeds(1.0, 0.0, 0.0)
        self.assertAlmostEqual(wa, -math.sqrt(3.0) * 0.5, places=5)
        self.assertAlmostEqual(wb, +math.sqrt(3.0) * 0.5, places=5)
        self.assertAlmostEqual(wc, 0.0, places=5)

        # Pure leftward sway (+Y): Wheel A == +0.5, Wheel B == +0.5, Wheel C == -1.0
        wa_y, wb_y, wc_y = kin.compute_wheel_speeds(0.0, 1.0, 0.0)
        self.assertAlmostEqual(wa_y, 0.5, places=5)
        self.assertAlmostEqual(wb_y, 0.5, places=5)
        self.assertAlmostEqual(wc_y, -1.0, places=5)

        # Pure CCW spin (+omega): all 3 radially symmetric wheels receive identical +R*omega
        wa_r, wb_r, wc_r = kin.compute_wheel_speeds(0.0, 0.0, 2.0)
        self.assertAlmostEqual(wa_r, wb_r, places=6)
        self.assertAlmostEqual(wb_r, wc_r, places=6)
        self.assertAlmostEqual(wa_r, 2.0 * 0.12088, places=5)

        # Verify DriveController motor_polarity inverts H-bridge outputs cleanly
        class _Chan:
            def __init__(self):
                self.spd = 0.0
            def speed(self, v):
                self.spd = v
            def stop(self):
                self.spd = 0.0

        class _Mod:
            def __init__(self):
                self.motor1 = _Chan()
                self.motor2 = _Chan()
                self.en = False
            def enable(self):
                self.en = True
            def disable(self):
                self.en = False
            def is_enabled(self):
                return self.en

        m1, m2 = _Mod(), _Mod()
        drv = DriveController(
            FakeYukon(main_on=True),
            m1,
            m2,
            kinematics=kin,
            speed_modes={"indoor": 0.35},
            slew_per_sec=100.0,
            motor_polarity=(-1, -1, -1),
        )
        drv.set_armed(True)
        drv.set_routine_wheels(0.4, -0.3, 0.2, active=True)
        drv._last_tick_ms = time.ticks_ms() - 50
        drv.tick(safety_ok=True)
        self.assertAlmostEqual(m1.motor1.spd, -0.4, places=4)
        self.assertAlmostEqual(m1.motor2.spd, +0.3, places=4)
        self.assertAlmostEqual(m2.motor1.spd, -0.2, places=4)

    def test_icm20948_mount_yaw_90_deg_body_frame_remap(self):
        import struct
        from drivers.icm20948 import ICM20948

        class _MockImuI2C:
            def __init__(self):
                # Die sees: ax_die=0, ay_die=-0.5g (cone pitched nose UP: x_body = -ay_die = +0.5g), az_die=+0.866g
                self.accel_gyro = struct.pack(
                    ">hhhhhhh",
                    0,
                    int(-0.5 * 16384),
                    int(0.866 * 16384),
                    0,
                    int(-20.0 * 131.0),
                    int(45.0 * 131.0),
                    0,
                )
                # AK09916 raw: rx=+100 (dx=+15 uT -> my_body=+15 uT), ry=+200 (dy=-30 uT -> mx_body=+30 uT)
                self.mag_frame = bytes([0x01]) + struct.pack("<hhh", 100, 200, -50) + bytes([0x00, 0x00])

            def writeto_mem(self, addr, reg, data):
                pass

            def readfrom_mem(self, addr, reg, n):
                if addr == 0x68 and reg == 0x00:
                    return bytes([0xEA])
                if addr == 0x68 and reg == 0x03:
                    return bytes([0x00])
                if addr == 0x0C and reg == 0x01:
                    return bytes([0x09])
                if addr == 0x68 and reg == 0x2D:
                    return self.accel_gyro
                if addr == 0x0C and reg == 0x10:
                    return self.mag_frame
                return bytes(n)

            def readfrom_mem_into(self, addr, reg, buf):
                buf[:] = self.accel_gyro

        imu = ICM20948(_MockImuI2C(), mount_yaw_deg=90.0)
        d = imu.read_all()
        self.assertAlmostEqual(d["accel"][0], +0.5, places=2)
        self.assertAlmostEqual(d["accel"][1], 0.0, places=2)
        self.assertAlmostEqual(d["accel"][2], +0.866, places=2)
        self.assertAlmostEqual(d["gyro"][0], +20.0, places=1)
        self.assertAlmostEqual(d["gyro"][2], +45.0, places=1)

        imu._last_update_ms = time.ticks_ms() - 20
        imu.step_fast_50hz()
        self.assertAlmostEqual(imu.ax_g, +0.5, places=2)
        self.assertAlmostEqual(imu.gz_dps, +45.0, places=1)

        mx, my, mz = imu.read_mag_raw_ut()
        self.assertAlmostEqual(mx, +30.0, places=2)
        self.assertAlmostEqual(my, +15.0, places=2)
        self.assertAlmostEqual(mz, +7.5, places=2)

    def test_wifi_sta_connect_disables_cyw43_dtim_power_save(self):
        import types
        net_mod = types.ModuleType("network")

        class _FakeWlan:
            STA_IF = 0
            AP_IF = 1
            PM_NONE = 0xA11140
            PM_PERFORMANCE = 0xA11142
            instances = []

            def __init__(self, iface):
                self.iface = iface
                self.pm_calls = []
                self.connected = False
                _FakeWlan.instances.append(self)

            def active(self, val=None):
                return True

            def config(self, *args, **kwargs):
                if args and args[0] == "mac":
                    return b"\x28\xcd\xc1\x01\x02\x03"
                if "pm" in kwargs:
                    self.pm_calls.append(kwargs["pm"])

            def scan(self):
                return [(b"HomeLAN", b"", 6, -52, 3, 1)]

            def connect(self, ssid, pwd):
                self.connected = True

            def isconnected(self):
                return self.connected

            def ifconfig(self):
                return ("192.168.50.13", "255.255.255.0", "192.168.50.1", "192.168.50.1")

            def status(self, key):
                return -52

        net_mod.STA_IF = 0
        net_mod.AP_IF = 1
        net_mod.WLAN = _FakeWlan
        old_net = sys.modules.get("network")
        sys.modules["network"] = net_mod
        sys.modules.pop("drivers.wifi_manager", None)
        try:
            from drivers.wifi_manager import ConeWiFi
            cw = ConeWiFi()
            cw._load_configured_networks = lambda: [("HomeLAN", "pw12345678")]
            st = cw.connect_or_start_ap()
            self.assertEqual(st["mode"], "STA")
            self.assertIn(0xA11140, _FakeWlan.instances[0].pm_calls)
            self.assertEqual(_FakeWlan.instances[0].pm_calls[-1], 0xA11140)
        finally:
            if old_net is None:
                sys.modules.pop("network", None)
            else:
                sys.modules["network"] = old_net
            sys.modules.pop("drivers.wifi_manager", None)


if __name__ == "__main__":
    unittest.main()
