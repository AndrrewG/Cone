# ==============================================================================
# ARSE — Autonomous Road Systems Engineering
# Adversarial Desktop Verification Suite: Task 1.1 (security/auth_manager.py)
# ==============================================================================

import ast
import contextlib
import hashlib
import io
import json
import os
import re
import tempfile
import unittest

import security
import security.auth_manager as auth_mod
from security.auth_manager import (
    AuthManager,
    AuthRateLimitError,
    constant_time_compare,
    hash_password,
)


URLSAFE_TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{32}$")
HEX_LOWER_64_RE = re.compile(r"^[0-9a-f]{64}$")
HEX_32_RE = re.compile(r"^[0-9a-fA-F]{32}$")


def _reference_hash_password(password_str: str, salt_bytes: bytes, rounds: int = 1000) -> str:
    """Independent CPython reference implementation of the IDEA-007 hash contract."""
    salt = bytes(salt_bytes)
    digest = hashlib.sha256(salt + password_str.encode("utf-8")).digest()
    for _ in range(rounds - 1):
        digest = hashlib.sha256(salt + digest).digest()
    return digest.hex()


class FakeClock:
    """Deterministic millisecond clock injected via time_ms_fn."""

    def __init__(self, start_ms: int = 0):
        self.now_ms = int(start_ms)

    def __call__(self) -> int:
        return self.now_ms

    def advance(self, delta_ms: int) -> int:
        self.now_ms += int(delta_ms)
        return self.now_ms

    def set(self, value_ms: int) -> int:
        self.now_ms = int(value_ms)
        return self.now_ms


class FakeVoiceEngine:
    """Mock Conish VoiceEngine supporting semantic say_event()."""

    def __init__(self):
        self.events = []

    def say_event(self, event_name):
        self.events.append(event_name)


class FakeConeAudioFallback:
    """Mock legacy ConeAudio supporting play() without say_event()."""

    def __init__(self):
        self.played = []

    def play(self, clip_name):
        self.played.append(clip_name)


class FakeOverlayLights:
    """Mock LED controller supporting transient set_temporary_overlay()."""

    def __init__(self):
        self.overlays = []
        self.modes = []

    def set_temporary_overlay(self, mode, duration_ms):
        self.overlays.append((mode, duration_ms))

    def set_mode(self, mode):
        self.modes.append(mode)


class FakeConeLightsFallback:
    """Mock ConeLights supporting only set_mode() without temporary overlay."""

    def __init__(self):
        self.modes = []

    def set_mode(self, mode):
        self.modes.append(mode)


class CryptoPrimitivesTests(unittest.TestCase):
    """Adversarial verification of hash_password and constant_time_compare."""

    def test_package_and_module_exports_match(self):
        self.assertIs(security.AuthManager, AuthManager)
        self.assertIs(security.AuthRateLimitError, AuthRateLimitError)
        self.assertIs(security.hash_password, hash_password)
        self.assertIs(security.constant_time_compare, constant_time_compare)
        self.assertIs(auth_mod.AuthManager, AuthManager)

    def test_hash_password_matches_independent_iterated_sha256_vectors(self):
        salt_16 = bytes.fromhex("00112233445566778899aabbccddeeff")
        salt_8 = b"ARSEsalt"

        for rounds in (1, 2, 10, 1000):
            expected = _reference_hash_password("CorrectHorse!99", salt_16, rounds=rounds)
            actual = hash_password("CorrectHorse!99", salt_16, rounds=rounds)
            self.assertEqual(actual, expected)
            self.assertRegex(actual, HEX_LOWER_64_RE)

        # Default rounds must equal 1000
        self.assertEqual(
            hash_password("CorrectHorse!99", salt_16),
            _reference_hash_password("CorrectHorse!99", salt_16, rounds=1000),
        )

        # UTF-8 multi-byte characters & buffer protocol salts (bytearray / memoryview)
        unicode_pw = "pavement_kérb_manoeuvre_🚧"
        expected_utf8 = _reference_hash_password(unicode_pw, salt_8, rounds=1000)
        self.assertEqual(hash_password(unicode_pw, bytearray(salt_8)), expected_utf8)
        self.assertEqual(hash_password(unicode_pw, memoryview(salt_8)), expected_utf8)

        # Salt and password sensitivity (avalanche check)
        flipped_salt = bytearray(salt_16)
        flipped_salt[-1] ^= 0x01
        self.assertNotEqual(
            hash_password("CorrectHorse!99", salt_16),
            hash_password("CorrectHorse!99", bytes(flipped_salt)),
        )
        self.assertNotEqual(
            hash_password("CorrectHorse!99", salt_16),
            hash_password("CorrectHorse!98", salt_16),
        )

    def test_hash_password_rejects_invalid_types_and_boundary_parameters(self):
        valid_salt = b"0123456789abcdef"

        # Non-str passwords must raise TypeError
        for bad_pw in (None, b"bytes_pw", bytearray(b"bytes_pw"), 12345678, ["pw"], {"pw": 1}, True):
            with self.subTest(bad_pw=bad_pw):
                with self.assertRaises(TypeError):
                    hash_password(bad_pw, valid_salt)

        # Non-bytes-like salts must raise TypeError
        for bad_salt in (None, "0123456789abcdef", 12345678, [0] * 16, {"s": 1}, True):
            with self.subTest(bad_salt=bad_salt):
                with self.assertRaises(TypeError):
                    hash_password("valid_password", bad_salt)

        # Short salts (< 8 bytes) must raise ValueError
        for short_salt in (b"", b"1", b"1234567", bytearray(7), memoryview(b"seven77")):
            with self.subTest(short_salt_len=len(short_salt)):
                with self.assertRaises(ValueError):
                    hash_password("valid_password", short_salt)

        # Rounds < 1 or bool rounds must raise ValueError
        for bad_rounds in (0, -1, -1000, True, False):
            with self.subTest(bad_rounds=bad_rounds):
                with self.assertRaises(ValueError):
                    hash_password("valid_password", valid_salt, rounds=bad_rounds)

        # Non-integer rounds must raise TypeError or ValueError
        for non_int_rounds in ("1000", 10.5, None, [1000]):
            with self.subTest(non_int_rounds=non_int_rounds):
                with self.assertRaises((TypeError, ValueError)):
                    hash_password("valid_password", valid_salt, rounds=non_int_rounds)

    def test_constant_time_compare_truth_table_and_adversarial_inputs(self):
        # Matching str vs str
        self.assertTrue(constant_time_compare("", ""))
        self.assertTrue(constant_time_compare("admin", "admin"))
        self.assertTrue(constant_time_compare("kérb_🚧", "kérb_🚧"))
        tok = "aB3_-90123456789abcdefABCDEF0123"
        self.assertTrue(constant_time_compare(tok, str(tok)))

        # Matching bytes-like vs bytes-like
        raw = b"\x00\xff\x10\x20\x7f\x80abcdef0123"
        self.assertTrue(constant_time_compare(raw, bytes(raw)))
        self.assertTrue(constant_time_compare(raw, bytearray(raw)))
        self.assertTrue(constant_time_compare(bytearray(raw), memoryview(raw)))

        # Single-bit differences at start, middle, and final byte
        for idx in (0, len(raw) // 2, len(raw) - 1):
            mutated = bytearray(raw)
            mutated[idx] ^= 0x01
            self.assertFalse(constant_time_compare(raw, bytes(mutated)))

        self.assertFalse(constant_time_compare("admin", "Admin"))
        self.assertFalse(constant_time_compare("admin", "admiN"))
        self.assertFalse(constant_time_compare("admin", "admin0"))
        self.assertFalse(constant_time_compare("admin", ""))
        self.assertFalse(constant_time_compare("é", "e"))  # 2 UTF-8 bytes vs 1 ASCII byte

        # Type mismatches and None must return False cleanly without raising
        adversarial_pairs = [
            (None, None),
            (None, "admin"),
            ("admin", None),
            (None, b"admin"),
            ("admin", b"admin"),
            (b"admin", "admin"),
            (12345, 12345),
            ("12345", 12345),
            (["a", "b"], ["a", "b"]),
            ({"k": "v"}, {"k": "v"}),
            (True, True),
            (object(), object()),
        ]
        for left, right in adversarial_pairs:
            with self.subTest(left=left, right=right):
                self.assertIs(constant_time_compare(left, right), False)


class BootstrapAndPersistenceTests(unittest.TestCase):
    """Verification of secrets.json bootstrap, Wi-Fi preservation, and corruption recovery."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.secrets_path = os.path.join(self._tmp.name, "secrets.json")

    def tearDown(self):
        self._tmp.cleanup()

    def test_class_constants_and_rate_limit_error_attributes(self):
        self.assertEqual(AuthManager.DEFAULT_USERNAME, "admin")
        self.assertEqual(AuthManager.HASH_ROUNDS, 1000)
        self.assertEqual(AuthManager.SALT_BYTES, 16)
        self.assertEqual(AuthManager.TOKEN_BYTES, 24)
        self.assertEqual(AuthManager.SESSION_SLOTS, 4)
        self.assertEqual(AuthManager.SESSION_TTL_MS, 3_600_000)
        self.assertEqual(AuthManager.LOCKOUT_SLOTS, 8)
        self.assertEqual(AuthManager.MAX_FAILED_ATTEMPTS, 5)
        self.assertEqual(AuthManager.FAIL_WINDOW_MS, 60_000)
        self.assertEqual(AuthManager.LOCKOUT_DURATION_MS, 300_000)

        err = AuthRateLimitError(300)
        self.assertIsInstance(err, Exception)
        self.assertIsInstance(err.retry_after_s, int)
        self.assertEqual(err.retry_after_s, 300)

        err_clamped = AuthRateLimitError(0)
        self.assertIsInstance(err_clamped.retry_after_s, int)
        self.assertGreaterEqual(err_clamped.retry_after_s, 1)

    def test_first_boot_creates_secrets_without_plaintext_leak_and_honours_print_flag(self):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            mgr = AuthManager(secrets_path=self.secrets_path, print_bootstrap=True)
        printed = buf.getvalue()

        self.assertIsInstance(mgr.bootstrapped_password, str)
        self.assertGreaterEqual(len(mgr.bootstrapped_password), 8)
        self.assertIn("admin", printed)
        self.assertIn(mgr.bootstrapped_password, printed)

        self.assertTrue(os.path.isfile(self.secrets_path))
        with open(self.secrets_path, "r", encoding="utf-8") as f:
            raw_text = f.read()
        data = json.loads(raw_text)

        # Plaintext bootstrapped password MUST NEVER be written to flash JSON
        self.assertNotIn(mgr.bootstrapped_password, raw_text)
        self.assertNotIn("bootstrapped_password", data)

        self.assertEqual(data["username"], "admin")
        self.assertRegex(data["password_salt_hex"], HEX_32_RE)
        self.assertRegex(data["password_hash_hex"], HEX_LOWER_64_RE)
        self.assertRegex(data["beacon_token"], URLSAFE_TOKEN_RE)
        self.assertEqual(data["beacon_token"], mgr.get_beacon_token())

        expected_hash = _reference_hash_password(
            mgr.bootstrapped_password,
            bytes.fromhex(data["password_salt_hex"]),
            rounds=1000,
        )
        self.assertEqual(data["password_hash_hex"], expected_hash)

        # Verify print_bootstrap=False produces zero stdout output
        quiet_path = os.path.join(self._tmp.name, "quiet_secrets.json")
        quiet_buf = io.StringIO()
        with contextlib.redirect_stdout(quiet_buf):
            mgr_quiet = AuthManager(secrets_path=quiet_path, print_bootstrap=False)
        self.assertEqual(quiet_buf.getvalue(), "")
        self.assertIsInstance(mgr_quiet.bootstrapped_password, str)

    def test_reload_from_valid_secrets_sets_bootstrapped_password_none(self):
        mgr1 = AuthManager(secrets_path=self.secrets_path, print_bootstrap=False)
        initial_pw = mgr1.bootstrapped_password
        initial_beacon = mgr1.get_beacon_token()

        reload_buf = io.StringIO()
        with contextlib.redirect_stdout(reload_buf):
            mgr2 = AuthManager(secrets_path=self.secrets_path, print_bootstrap=True)

        self.assertIsNone(mgr2.bootstrapped_password)
        self.assertEqual(reload_buf.getvalue(), "")
        self.assertEqual(mgr2.get_beacon_token(), initial_beacon)

        tok = mgr2.login("admin", initial_pw, client_ip="192.168.4.2")
        self.assertIsNotNone(tok)
        self.assertRegex(tok, URLSAFE_TOKEN_RE)

    def test_bootstrap_preserves_existing_wifi_manager_credentials(self):
        wifi_seed = {
            "ssid": "ARSE_Field_Hotspot",
            "password": "WPA2_SuperSecret_Field_Key",
            "custom_note": "preserve_me",
        }
        with open(self.secrets_path, "w", encoding="utf-8") as f:
            json.dump(wifi_seed, f)

        mgr = AuthManager(secrets_path=self.secrets_path, print_bootstrap=False)
        self.assertIsInstance(mgr.bootstrapped_password, str)

        with open(self.secrets_path, "r", encoding="utf-8") as f:
            merged = json.load(f)

        # Pre-existing Wi-Fi keys must remain untouched for drivers/wifi_manager.py
        self.assertEqual(merged["ssid"], "ARSE_Field_Hotspot")
        self.assertEqual(merged["password"], "WPA2_SuperSecret_Field_Key")
        self.assertEqual(merged["custom_note"], "preserve_me")

        # Auth keys must be present and distinct from the Wi-Fi password
        self.assertEqual(merged["username"], "admin")
        self.assertRegex(merged["password_salt_hex"], HEX_32_RE)
        self.assertRegex(merged["password_hash_hex"], HEX_LOWER_64_RE)
        self.assertRegex(merged["beacon_token"], URLSAFE_TOKEN_RE)

        self.assertIsNone(mgr.login("admin", "WPA2_SuperSecret_Field_Key", client_ip="10.0.0.5"))
        self.assertIsNotNone(mgr.login("admin", mgr.bootstrapped_password, client_ip="10.0.0.5"))

    def test_corrupt_or_incomplete_secrets_json_safely_rebootstraps(self):
        corrupt_payloads = [
            "",
            "{truncated json...",
            "[]",
            "12345",
            json.dumps({"username": "admin"}),
            json.dumps(
                {
                    "username": "admin",
                    "password_salt_hex": "not_valid_hex_string_at_all_zzzz",
                    "password_hash_hex": "a" * 64,
                    "beacon_token": "B" * 32,
                }
            ),
            json.dumps(
                {
                    "username": "admin",
                    "password_salt_hex": "a" * 32,
                    "password_hash_hex": "short_hash",
                    "beacon_token": "B" * 32,
                }
            ),
            json.dumps(
                {
                    "username": "admin",
                    "password_salt_hex": "a" * 32,
                    "password_hash_hex": "b" * 64,
                    "beacon_token": "",
                }
            ),
        ]

        for idx, bad_content in enumerate(corrupt_payloads):
            with self.subTest(case_index=idx):
                path = os.path.join(self._tmp.name, f"corrupt_{idx}.json")
                with open(path, "w", encoding="utf-8") as f:
                    f.write(bad_content)

                mgr = AuthManager(secrets_path=path, print_bootstrap=False)
                self.assertIsInstance(mgr.bootstrapped_password, str)
                self.assertGreaterEqual(len(mgr.bootstrapped_password), 8)
                tok = mgr.login("admin", mgr.bootstrapped_password, client_ip="192.168.4.9")
                self.assertIsNotNone(tok)


class SessionAndBearerTests(unittest.TestCase):
    """Verification of login, 4-slot session eviction/reuse, rolling TTL, and beacon scope."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.secrets_path = os.path.join(self._tmp.name, "secrets.json")
        self.clock = FakeClock(start_ms=0)
        self.voice = FakeVoiceEngine()
        self.lights = FakeOverlayLights()
        self.mgr = AuthManager(
            secrets_path=self.secrets_path,
            voice_engine=self.voice,
            led_controller=self.lights,
            time_ms_fn=self.clock,
            print_bootstrap=False,
        )
        self.password = self.mgr.bootstrapped_password

    def tearDown(self):
        self._tmp.cleanup()

    def test_valid_login_triggers_voice_ack_and_fallback_audio(self):
        tok = self.mgr.login("admin", self.password, client_ip="192.168.4.10")
        self.assertIsNotNone(tok)
        self.assertRegex(tok, URLSAFE_TOKEN_RE)
        self.assertNotEqual(tok, self.mgr.get_beacon_token())
        self.assertEqual(self.voice.events, ["ACK_COMMAND"])

        # Fallback to ConeAudio.play("chirp") when say_event is absent
        audio_fb = FakeConeAudioFallback()
        mgr_fb = AuthManager(
            secrets_path=self.secrets_path,
            voice_engine=audio_fb,
            time_ms_fn=self.clock,
            print_bootstrap=False,
        )
        tok2 = mgr_fb.login("admin", self.password, client_ip="192.168.4.11")
        self.assertIsNotNone(tok2)
        self.assertEqual(audio_fb.played, ["chirp"])

    def test_login_rejects_malformed_unicode_null_and_oversized_inputs_cleanly(self):
        bad_attempts = [
            ("admin", "wrong_password"),
            ("Admin", self.password),
            ("", self.password),
            ("admin", ""),
            (None, self.password),
            ("admin", None),
            (None, None),
            (123, 456),
            (b"admin", self.password),
            ("admin", self.password.encode("utf-8")),
            (["admin"], [self.password]),
            ({"u": "admin"}, {"p": self.password}),
            ("admin\x00extra", self.password),
            ("admin", self.password + "\x00"),
            ("аdmin", self.password),  # Cyrillic 'а' homoglyph
            ("A" * 1024, self.password),  # Oversized > 256 chars
            ("admin", "P" * 4096),  # Oversized > 256 chars
        ]
        for idx, (bad_user, bad_pw) in enumerate(bad_attempts):
            with self.subTest(idx=idx):
                res = self.mgr.login(bad_user, bad_pw, client_ip=f"10.20.0.{idx + 1}")
                self.assertIsNone(res)

        self.assertEqual(self.voice.events, [])

    def test_four_slot_session_table_evicts_oldest_created_and_reuses_expired_first(self):
        # Fill all 4 session slots at distinct timestamps
        self.clock.set(1_000)
        s1 = self.mgr.login("admin", self.password, client_ip="192.168.4.2")
        self.clock.set(2_000)
        s2 = self.mgr.login("admin", self.password, client_ip="192.168.4.3")
        self.clock.set(3_000)
        s3 = self.mgr.login("admin", self.password, client_ip="192.168.4.4")
        self.clock.set(4_000)
        s4 = self.mgr.login("admin", self.password, client_ip="192.168.4.5")

        self.assertEqual(len({s1, s2, s3, s4}), 4)
        for tok in (s1, s2, s3, s4):
            self.assertEqual(self.mgr.validate_bearer(tok), "operator")
        self.assertEqual(self.mgr.get_public_status()["active_sessions"], 4)

        # 5th concurrent login must evict oldest created session (s1)
        self.clock.set(5_000)
        s5 = self.mgr.login("admin", self.password, client_ip="192.168.4.6")
        self.assertIsNone(self.mgr.validate_bearer(s1))
        for tok in (s2, s3, s4, s5):
            self.assertEqual(self.mgr.validate_bearer(tok), "operator")
        self.assertEqual(self.mgr.get_public_status()["active_sessions"], 4)

        # Verify an expired slot is reused first WITHOUT evicting active older-created sessions:
        # Refresh s2 at t = 3_500_000 so s2 stays alive until 7_100_000, while letting s3 expire.
        self.clock.set(3_500_000)
        self.assertEqual(self.mgr.validate_bearer(s2), "operator")
        self.assertEqual(self.mgr.validate_bearer(s4), "operator")
        self.assertEqual(self.mgr.validate_bearer(s5), "operator")

        # Advance to t = 4_000_000 -> s3 (last touched at 5_000 -> TTL 3_605_000) is now expired!
        self.clock.set(4_000_000)
        s6 = self.mgr.login("admin", self.password, client_ip="192.168.4.7")
        self.assertIsNone(self.mgr.validate_bearer(s3))
        for active_tok in (s2, s4, s5, s6):
            self.assertEqual(self.mgr.validate_bearer(active_tok), "operator")
        self.assertEqual(self.mgr.get_public_status()["active_sessions"], 4)

    def test_validate_bearer_header_parsing_rolling_idle_ttl_and_beacon_scoping(self):
        self.clock.set(0)
        tok_active = self.mgr.login("admin", self.password, client_ip="192.168.4.10")
        tok_idle = self.mgr.login("admin", self.password, client_ip="192.168.4.11")

        # Header format acceptance ("Bearer <tok>", "bearer <tok>", raw "<tok>")
        self.assertEqual(self.mgr.validate_bearer(tok_active), "operator")
        self.assertEqual(self.mgr.validate_bearer(f"Bearer {tok_active}"), "operator")
        self.assertEqual(self.mgr.validate_bearer(f"bearer {tok_active}"), "operator")

        # Malformed bearer strings must return None
        for bad_hdr in (
            None,
            12345,
            b"Bearer token",
            "",
            "   ",
            "Bearer",
            "Bearer ",
            "Bearer    ",
            f"Basic {tok_active}",
            f"Bearer {tok_active} trailing",
            f"{tok_active[:10]} {tok_active[11:]}",
            "0" * 32,
        ):
            with self.subTest(bad_hdr=bad_hdr):
                self.assertIsNone(self.mgr.validate_bearer(bad_hdr))

        # Rolling 3_600_000 ms idle TTL:
        # Touch tok_active at t = 3_000_000 -> extends expiry to t = 6_600_000
        self.clock.set(3_000_000)
        self.assertEqual(self.mgr.validate_bearer(tok_active), "operator")

        # tok_idle sat untouched since t = 0 -> expires at t = 3_600_000
        self.clock.set(3_600_000)
        self.assertIsNone(self.mgr.validate_bearer(tok_idle))

        # tok_active must still be valid right up to 6_599_999 (check without refreshing another copy:
        # let's verify at 5_000_000 it is valid, which extends to 8_600_000, then expires at 8_600_000)
        self.clock.set(5_000_000)
        self.assertEqual(self.mgr.validate_bearer(tok_active), "operator")

        self.clock.set(8_600_000)
        self.assertIsNone(self.mgr.validate_bearer(tok_active))

        # Scoped beacon_token ALWAYS returns "beacon_only" (never "operator", never idle-expires)
        beacon = self.mgr.get_beacon_token()
        self.assertEqual(self.mgr.validate_bearer(beacon), "beacon_only")
        self.assertEqual(self.mgr.validate_bearer(f"Bearer {beacon}"), "beacon_only")
        self.assertEqual(self.mgr.validate_bearer(f"bearer {beacon}"), "beacon_only")
        self.clock.advance(86_400_000)  # +24 hours
        self.assertEqual(self.mgr.validate_bearer(beacon), "beacon_only")

    def test_logout_single_session_logout_all_and_beacon_token_protection(self):
        s1 = self.mgr.login("admin", self.password, client_ip="192.168.4.21")
        s2 = self.mgr.login("admin", self.password, client_ip="192.168.4.22")
        s3 = self.mgr.login("admin", self.password, client_ip="192.168.4.23")
        beacon = self.mgr.get_beacon_token()

        # Calling logout on beacon_token must NOT invalidate beacon_token or operator sessions
        self.mgr.logout(beacon)
        self.mgr.logout(f"Bearer {beacon}")
        self.assertEqual(self.mgr.validate_bearer(beacon), "beacon_only")
        self.assertEqual(self.mgr.get_public_status()["active_sessions"], 3)

        # Single-token logout invalidates only s2
        self.mgr.logout(f"Bearer {s2}")
        self.assertIsNone(self.mgr.validate_bearer(s2))
        self.assertEqual(self.mgr.validate_bearer(s1), "operator")
        self.assertEqual(self.mgr.validate_bearer(s3), "operator")

        # Safe handling of None / unknown tokens on logout (must return False & preserve sessions)
        self.assertFalse(self.mgr.logout(None))
        self.assertFalse(self.mgr.logout("nonexistent_session_token_123456"))
        self.assertEqual(self.mgr.get_public_status()["active_sessions"], 2)

        # logout_all clears all remaining operator sessions while keeping beacon_token valid
        cleared = self.mgr.logout_all()
        self.assertEqual(cleared, 2)
        self.assertEqual(self.mgr.logout_all(), 0)
        self.assertIsNone(self.mgr.validate_bearer(s1))
        self.assertIsNone(self.mgr.validate_bearer(s3))
        self.assertEqual(self.mgr.validate_bearer(beacon), "beacon_only")

    def test_default_cpython_clock_when_time_ms_fn_is_none(self):
        mgr_default = AuthManager(
            secrets_path=self.secrets_path,
            time_ms_fn=None,
            print_bootstrap=False,
        )
        tok = mgr_default.login("admin", self.password, client_ip="192.168.4.50")
        self.assertIsNotNone(tok)
        self.assertEqual(mgr_default.validate_bearer(f"Bearer {tok}"), "operator")
        self.assertEqual(mgr_default.get_public_status()["active_sessions"], 1)
        self.assertFalse(mgr_default.logout(None))
        self.assertEqual(mgr_default.get_public_status()["active_sessions"], 1)
        mgr_default.logout()
        self.assertEqual(mgr_default.get_public_status()["active_sessions"], 0)
        self.assertIsNone(mgr_default.validate_bearer(tok))


class BruteForceLockoutTests(unittest.TestCase):
    """Adversarial tests for 8-slot IP lockout, tamper alerts, window reset, and IP-spray."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.secrets_path = os.path.join(self._tmp.name, "secrets.json")
        self.clock = FakeClock(start_ms=10_000)
        self.voice = FakeVoiceEngine()
        self.lights = FakeOverlayLights()
        self.mgr = AuthManager(
            secrets_path=self.secrets_path,
            voice_engine=self.voice,
            led_controller=self.lights,
            time_ms_fn=self.clock,
            print_bootstrap=False,
        )
        self.password = self.mgr.bootstrapped_password

    def tearDown(self):
        self._tmp.cleanup()

    def test_five_failures_trigger_300s_lockout_strobe_overlay_and_fault_voice(self):
        attacker_ip = "192.168.4.66"

        # Failures 1..4 within 60_000 ms return None without locking out
        for i in range(4):
            self.clock.advance(2_000)
            self.assertIsNone(self.mgr.login("admin", "bad_pw", client_ip=attacker_ip))
            self.assertEqual(self.lights.overlays, [])
            self.assertEqual(self.voice.events, [])

        # 5th failure within 60_000 ms triggers 300s lockout + strobe + ERR_SYSTEM_FAULT
        self.clock.advance(2_000)
        lock_time = self.clock.now_ms
        with self.assertRaises(AuthRateLimitError) as ctx:
            self.mgr.login("admin", "bad_pw", client_ip=attacker_ip)

        self.assertEqual(ctx.exception.retry_after_s, 300)
        self.assertEqual(self.lights.overlays, [("strobe", 1500)])
        self.assertEqual(self.voice.events, ["ERR_SYSTEM_FAULT"])

        # While locked (< 300_000 ms), EVEN VALID CREDENTIALS from attacker_ip raise AuthRateLimitError!
        for offset_ms in (0, 1_000, 150_000, 299_999):
            self.clock.set(lock_time + offset_ms)
            with self.assertRaises(AuthRateLimitError) as locked_ctx:
                self.mgr.login("admin", self.password, client_ip=attacker_ip)
            self.assertGreaterEqual(locked_ctx.exception.retry_after_s, 1)
            self.assertLessEqual(locked_ctx.exception.retry_after_s, 300)

        # Legitimate operator on another IP can still log in cleanly during attacker lockout
        self.clock.set(lock_time + 50_000)
        legit_tok = self.mgr.login("admin", self.password, client_ip="192.168.4.99")
        self.assertIsNotNone(legit_tok)

        # Once 300_000 ms elapses, attacker_ip unlocks and can log in with valid credentials
        self.clock.set(lock_time + 300_000)
        unlocked_tok = self.mgr.login("admin", self.password, client_ip=attacker_ip)
        self.assertIsNotNone(unlocked_tok)

    def test_lockout_fallback_hooks_for_cone_lights_and_cone_audio(self):
        audio_fb = FakeConeAudioFallback()
        lights_fb = FakeConeLightsFallback()
        mgr_fb = AuthManager(
            secrets_path=self.secrets_path,
            voice_engine=audio_fb,
            led_controller=lights_fb,
            time_ms_fn=self.clock,
            print_bootstrap=False,
        )

        for _ in range(4):
            self.assertIsNone(mgr_fb.login("admin", "wrong", client_ip="10.0.0.44"))
        with self.assertRaises(AuthRateLimitError):
            mgr_fb.login("admin", "wrong", client_ip="10.0.0.44")

        self.assertEqual(lights_fb.modes, ["strobe"])
        self.assertEqual(audio_fb.played, ["alarm"])

    def test_failure_window_expiry_and_valid_login_reset_failure_counter(self):
        ip_window = "192.168.4.70"
        # 4 failures at t = 10_000..13_000
        for _ in range(4):
            self.clock.advance(1_000)
            self.assertIsNone(self.mgr.login("admin", "wrong", client_ip=ip_window))

        # Advance > 60_000 ms past prior failures -> 5th failure must reset fail_count to 1
        self.clock.advance(60_001)
        self.assertIsNone(self.mgr.login("admin", "wrong", client_ip=ip_window))

        # Need 4 more failures in the new window before lockout triggers
        for _ in range(3):
            self.clock.advance(1_000)
            self.assertIsNone(self.mgr.login("admin", "wrong", client_ip=ip_window))
        self.clock.advance(1_000)
        with self.assertRaises(AuthRateLimitError):
            self.mgr.login("admin", "wrong", client_ip=ip_window)

        # Valid login after 4 failures clears failure count for that IP
        ip_reset = "192.168.4.71"
        for _ in range(4):
            self.assertIsNone(self.mgr.login("admin", "wrong", client_ip=ip_reset))
        tok = self.mgr.login("admin", self.password, client_ip=ip_reset)
        self.assertIsNotNone(tok)
        for _ in range(4):
            self.assertIsNone(self.mgr.login("admin", "wrong", client_ip=ip_reset))

    def test_anti_eviction_ip_spray_cannot_dislodge_active_lockout(self):
        locked_ip_a = "10.99.0.1"
        locked_ip_b = "10.99.0.2"

        for ip in (locked_ip_a, locked_ip_b):
            for _ in range(4):
                self.mgr.login("admin", "wrong", client_ip=ip)
            with self.assertRaises(AuthRateLimitError):
                self.mgr.login("admin", "wrong", client_ip=ip)

        # Spray single/double failed attempts from 24 distinct spoofed IPs (> 8 LOCKOUT_SLOTS)
        for decoy_idx in range(1, 25):
            self.clock.advance(500)
            decoy_ip = f"172.16.0.{decoy_idx}"
            self.assertIsNone(self.mgr.login("admin", "wrong", client_ip=decoy_ip))
            self.assertIsNone(self.mgr.login("admin", "wrong", client_ip=decoy_ip))

        # Active lockouts on locked_ip_a and locked_ip_b MUST still be enforced!
        for ip in (locked_ip_a, locked_ip_b):
            with self.assertRaises(AuthRateLimitError):
                self.mgr.login("admin", self.password, client_ip=ip)


class CredentialUpdateAndPublicStatusTests(unittest.TestCase):
    """Verification of atomic credential updates, beacon rotation, and status redaction."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.secrets_path = os.path.join(self._tmp.name, "secrets.json")
        with open(self.secrets_path, "w", encoding="utf-8") as f:
            json.dump({"ssid": "ConeMesh_AP", "password": "KeepThisWifiPassword!"}, f)

        self.clock = FakeClock(start_ms=5_000)
        self.mgr = AuthManager(
            secrets_path=self.secrets_path,
            time_ms_fn=self.clock,
            print_bootstrap=False,
        )
        self.initial_pw = self.mgr.bootstrapped_password

    def tearDown(self):
        self._tmp.cleanup()

    def test_update_credentials_rejects_invalid_usernames_and_passwords_without_side_effects(self):
        tok = self.mgr.login("admin", self.initial_pw, client_ip="192.168.4.5")
        self.assertEqual(self.mgr.validate_bearer(tok), "operator")

        invalid_usernames = [
            "",
            "   ",
            "has space",
            "tab\tname",
            "newline\nname",
            "cr\rname",
            "null\x00name",
            "ctrl\x1fname",
            "u" * 33,
        ]
        for bad_user in invalid_usernames:
            with self.subTest(bad_user=repr(bad_user)):
                with self.assertRaises(ValueError):
                    self.mgr.update_credentials(bad_user, "ValidNewPassword!123")

        invalid_passwords = [
            "",
            "short77",  # 7 chars (< 8)
            "1234567",
            "p" * 129,  # 129 chars (> 128)
        ]
        for bad_pw in invalid_passwords:
            with self.subTest(bad_pw_len=len(bad_pw)):
                with self.assertRaises(ValueError):
                    self.mgr.update_credentials("valid_admin", bad_pw)

        for non_str_u, non_str_p in ((None, "ValidPass123"), ("admin", None), (123, "ValidPass123")):
            with self.assertRaises((TypeError, ValueError)):
                self.mgr.update_credentials(non_str_u, non_str_p)

        # Existing credentials and active session must remain completely intact
        self.assertEqual(self.mgr.validate_bearer(tok), "operator")
        self.assertIsNotNone(self.mgr.login("admin", self.initial_pw, client_ip="192.168.4.6"))

    def test_update_credentials_persists_atomically_revokes_sessions_and_rotates_beacon(self):
        s1 = self.mgr.login("admin", self.initial_pw, client_ip="192.168.4.10")
        s2 = self.mgr.login("admin", self.initial_pw, client_ip="192.168.4.11")
        beacon_before = self.mgr.get_beacon_token()

        with open(self.secrets_path, "r", encoding="utf-8") as f:
            salt_before = json.load(f)["password_salt_hex"]

        # 1. Update without beacon rotation
        self.mgr.update_credentials("highway_ops", "NewOperatorPass!2026", rotate_beacon=False)

        # Existing sessions revoked; beacon preserved
        self.assertIsNone(self.mgr.validate_bearer(s1))
        self.assertIsNone(self.mgr.validate_bearer(s2))
        self.assertEqual(self.mgr.get_public_status()["active_sessions"], 0)
        self.assertEqual(self.mgr.get_beacon_token(), beacon_before)
        self.assertEqual(self.mgr.validate_bearer(beacon_before), "beacon_only")

        # Old credentials rejected; new credentials accepted
        self.assertIsNone(self.mgr.login("admin", self.initial_pw, client_ip="192.168.4.12"))
        new_tok = self.mgr.login("highway_ops", "NewOperatorPass!2026", client_ip="192.168.4.12")
        self.assertIsNotNone(new_tok)

        # Atomic persistence check: no leftover .tmp files, Wi-Fi keys preserved, fresh salt, no plaintext
        self.assertEqual(os.listdir(self._tmp.name), ["secrets.json"])
        with open(self.secrets_path, "r", encoding="utf-8") as f:
            raw_updated = f.read()
        persisted = json.loads(raw_updated)
        self.assertNotIn("NewOperatorPass!2026", raw_updated)
        self.assertEqual(persisted["ssid"], "ConeMesh_AP")
        self.assertEqual(persisted["password"], "KeepThisWifiPassword!")
        self.assertEqual(persisted["username"], "highway_ops")
        self.assertNotEqual(persisted["password_salt_hex"], salt_before)

        # 2. Update WITH beacon rotation (rotate_beacon=True)
        self.mgr.update_credentials("highway_ops", "RotatedSecretPass#88", rotate_beacon=True)
        beacon_after = self.mgr.get_beacon_token()
        self.assertRegex(beacon_after, URLSAFE_TOKEN_RE)
        self.assertNotEqual(beacon_after, beacon_before)
        self.assertIsNone(self.mgr.validate_bearer(beacon_before))
        self.assertEqual(self.mgr.validate_bearer(beacon_after), "beacon_only")
        self.assertIsNone(self.mgr.validate_bearer(new_tok))

        # 3. Fresh AuthManager instance reloads updated credentials & rotated beacon cleanly
        mgr_reloaded = AuthManager(secrets_path=self.secrets_path, print_bootstrap=False)
        self.assertIsNone(mgr_reloaded.bootstrapped_password)
        self.assertEqual(mgr_reloaded.get_beacon_token(), beacon_after)
        self.assertIsNotNone(
            mgr_reloaded.login("highway_ops", "RotatedSecretPass#88", client_ip="192.168.4.20")
        )

    def test_get_public_status_schema_and_secret_redaction(self):
        st_idle = self.mgr.get_public_status()
        self.assertEqual(set(st_idle.keys()), {"locked", "active_sessions"})
        self.assertIsInstance(st_idle["locked"], bool)
        self.assertEqual(st_idle["active_sessions"], 0)

        tok = self.mgr.login("admin", self.initial_pw, client_ip="192.168.4.30")
        st_active = self.mgr.get_public_status()
        self.assertEqual(set(st_active.keys()), {"locked", "active_sessions"})
        self.assertIsInstance(st_active["locked"], bool)
        self.assertEqual(st_active["active_sessions"], 1)

        with open(self.secrets_path, "r", encoding="utf-8") as f:
            disk_secrets = json.load(f)

        serialised = json.dumps([st_idle, st_active])
        for secret_val in (
            self.initial_pw,
            tok,
            self.mgr.get_beacon_token(),
            disk_secrets["password_salt_hex"],
            disk_secrets["password_hash_hex"],
            disk_secrets["password"],
        ):
            self.assertNotIn(secret_val, serialised)


class ArchitecturalGuardrailAuditTests(unittest.TestCase):
    """Static audit ensuring MicroPython compatibility and HARDWARE.md guardrails."""

    def test_auth_manager_has_no_planned_hardware_motor_or_cpython_only_imports(self):
        repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        target_files = [
            os.path.join(repo_root, "security", "__init__.py"),
            os.path.join(repo_root, "security", "auth_manager.py"),
        ]

        forbidden_modules = {
            # [PLANNED] hardware (HARDWARE.md Section 3)
            "vl53l5cx",
            "dual_tof",
            "espnow",
            "mesh_transport",
            "swarm_node",
            "formation_planner",
            # Motor & navigation drivers (auth must never touch or arm motors)
            "drive_controller",
            "omni_kinematics",
            "navigator",
            "pattern_runner",
            # CPython-only / MicroPython-colliding modules (note: secrets collides with secrets.py!)
            "secrets",
            "hmac",
            "cryptography",
            "bcrypt",
            "argon2",
            "numpy",
            "scipy",
            "ssl",
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
                f"Forbidden imports found in {os.path.basename(file_path)}: {sorted(overlap)}",
            )

    def test_gitignore_excludes_secrets_json(self):
        repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        gitignore_path = os.path.join(repo_root, ".gitignore")
        with open(gitignore_path, "r", encoding="utf-8") as f:
            lines = [ln.strip() for ln in f if ln.strip() and not ln.strip().startswith("#")]
        self.assertTrue(
            any("secrets.json" in pattern for pattern in lines),
            "secrets.json must be listed in .gitignore",
        )

    @unittest.skipUnless(os.name == "nt", "PowerShell credential helper check runs on Windows")
    def test_set_password_ps1_generates_bom_free_compatible_secrets(self):
        import subprocess

        repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        script_path = os.path.join(repo_root, "tools", "set_password.ps1")
        with tempfile.TemporaryDirectory() as tmpdir:
            sec_path = os.path.join(tmpdir, "secrets.json")
            with open(sec_path, "w", encoding="utf-8") as f:
                json.dump({"ssid": "HomeMesh", "password": "WifiPassphrase123"}, f)

            proc = subprocess.run(
                [
                    "powershell.exe",
                    "-NoProfile",
                    "-NonInteractive",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-File",
                    script_path,
                    "-Username",
                    "andrew",
                    "-PlainPassword",
                    "ConeField!2026",
                    "-SecretsPath",
                    sec_path,
                ],
                capture_output=True,
                text=True,
                timeout=15,
            )
            self.assertEqual(proc.returncode, 0, msg=proc.stderr or proc.stdout)

            with open(sec_path, "rb") as f:
                raw_bytes = f.read()
            self.assertFalse(raw_bytes.startswith(b"\xef\xbb\xbf"), "secrets.json must not contain UTF-8 BOM")

            parsed = json.loads(raw_bytes.decode("utf-8"))
            self.assertEqual(parsed["ssid"], "HomeMesh")
            self.assertEqual(parsed["password"], "WifiPassphrase123")

            mgr = AuthManager(secrets_path=sec_path, print_bootstrap=False)
            self.assertIsNone(mgr.bootstrapped_password)
            self.assertEqual(mgr.username, "andrew")
            tok = mgr.login("andrew", "ConeField!2026", client_ip="192.168.50.20")
            self.assertIsNotNone(tok)
            self.assertEqual(mgr.validate_bearer(tok), "operator")


if __name__ == "__main__":
    unittest.main()
