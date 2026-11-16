# ==============================================================================
# ARSE — Autonomous Road Systems Engineering
# Module:      security/auth_manager.py
# Platform:    ARSE Mk.I Autonomous Traffic Cone (Pimoroni Yukon RP2040 / RP2350)
# Description: Zero-dependency auth, 4-slot Bearer session table & 8-slot IP lockout.
# ==============================================================================

import gc
import os
import sys

try:
    import uhashlib as _hashlib
except ImportError:
    import hashlib as _hashlib

try:
    import ubinascii as _binascii
except ImportError:
    import binascii as _binascii

try:
    import ujson as _json
except ImportError:
    import json as _json

try:
    import utime as _time
except ImportError:
    import time as _time

_AUTH_KEYS = ("username", "password_salt_hex", "password_hash_hex", "beacon_token")
_UNSET = object()


class AuthRateLimitError(Exception):
    def __init__(self, retry_after_s, message=None):
        self.retry_after_s = max(1, int(retry_after_s))
        super().__init__(
            message or "Too many failed login attempts; retry after {} s".format(self.retry_after_s)
        )


class _SessionSlot:
    __slots__ = ("active", "token", "expires_ms", "created_seq")

    def __init__(self):
        self.clear()

    def clear(self):
        self.active = False
        self.token = ""
        self.expires_ms = 0
        self.created_seq = 0


class _LockoutSlot:
    __slots__ = ("active", "ip", "fail_count", "window_start_ms", "locked", "locked_until_ms", "seq")

    def __init__(self):
        self.clear()

    def clear(self):
        self.active = False
        self.ip = ""
        self.fail_count = 0
        self.window_start_ms = 0
        self.locked = False
        self.locked_until_ms = 0
        self.seq = 0


def hash_password(password_str, salt_bytes, rounds=1000):
    if not isinstance(password_str, str):
        raise TypeError("password_str must be a str")
    if not isinstance(salt_bytes, (bytes, bytearray, memoryview)):
        raise TypeError("salt_bytes must be bytes-like")
    salt = bytes(salt_bytes)
    if len(salt) < 8:
        raise ValueError("salt_bytes must be >= 8 bytes")
    if not isinstance(rounds, int) or isinstance(rounds, bool) or rounds < 1:
        raise ValueError("rounds must be int >= 1")
    digest = _hashlib.sha256(salt + password_str.encode("utf-8")).digest()
    for _ in range(rounds - 1):
        digest = _hashlib.sha256(salt + digest).digest()
    return _binascii.hexlify(digest).decode("ascii").lower()


def constant_time_compare(a, b):
    if isinstance(a, str) and isinstance(b, str):
        a_b = a.encode("utf-8")
        b_b = b.encode("utf-8")
    elif isinstance(a, (bytes, bytearray, memoryview)) and isinstance(b, (bytes, bytearray, memoryview)):
        a_b = bytes(a)
        b_b = bytes(b)
    else:
        return False
    la = len(a_b)
    lb = len(b_b)
    diff = la ^ lb
    lim = la if la >= lb else lb
    for i in range(lim):
        diff |= (a_b[i] if i < la else 0) ^ (b_b[i] if i < lb else 0)
    return diff == 0


def _urlsafe_token(n_bytes):
    b64 = _binascii.b2a_base64(os.urandom(n_bytes)).decode("ascii").strip()
    return b64.replace("+", "-").replace("/", "_").rstrip("=")


def _is_valid_username(val):
    if not isinstance(val, str) or not (1 <= len(val) <= 32):
        return False
    for ch in val:
        if not (0x21 <= ord(ch) <= 0x7E):
            return False
    return True


def _is_valid_beacon_token(val):
    if not isinstance(val, str) or not (16 <= len(val) <= 128):
        return False
    for ch in val:
        c = ord(ch)
        if not ((0x30 <= c <= 0x39) or (0x41 <= c <= 0x5A) or (0x61 <= c <= 0x7A) or ch in ("-", "_", ".", "~")):
            return False
    return True


def _decode_valid_hex(val, min_bytes, exact_bytes=None):
    if not isinstance(val, str) or not val or (len(val) & 1):
        return None
    blen = len(val) >> 1
    if blen < min_bytes or (exact_bytes is not None and blen != exact_bytes):
        return None
    for ch in val:
        c = ord(ch)
        if not ((0x30 <= c <= 0x39) or (0x41 <= c <= 0x46) or (0x61 <= c <= 0x66)):
            return None
    try:
        return bytes(_binascii.unhexlify(val.encode("ascii")))
    except Exception:
        return None


def _stat_mode(path):
    try:
        return os.stat(path)[0] & 0xF000
    except OSError:
        return -1


def _ensure_parent_dir(file_path):
    norm = file_path.replace("\\", "/")
    idx = norm.rfind("/")
    if idx <= 0:
        return
    parent = file_path[:idx]
    if not parent or parent.endswith(":"):
        return
    if hasattr(os, "makedirs"):
        os.makedirs(parent, exist_ok=True)
        return
    cur = "/" if norm.startswith("/") else ""
    for part in norm[:idx].split("/"):
        if not part:
            continue
        cur = (cur + part) if cur in ("", "/") else (cur + "/" + part)
        if _stat_mode(cur) != 0x4000:
            try:
                os.mkdir(cur)
            except OSError:
                pass


def _resolve_secrets_path(secrets_path):
    if not isinstance(secrets_path, str) and hasattr(secrets_path, "__fspath__"):
        secrets_path = secrets_path.__fspath__()
    if not isinstance(secrets_path, str) or not secrets_path.strip():
        raise ValueError("secrets_path must be non-empty")
    if secrets_path == "/flash/secrets.json":
        return "/flash/secrets.json" if _stat_mode("/flash") == 0x4000 else "secrets.json"
    return secrets_path


def _extract_bearer_token(token_str):
    if not isinstance(token_str, str):
        return None
    lead = token_str.lstrip()
    if not lead:
        return None
    raw = lead[7:].strip() if (len(lead) >= 7 and lead[:7].lower() == "bearer ") else lead.rstrip()
    if not raw or len(raw) > 128:
        return None
    for ch in raw:
        if not (0x21 <= ord(ch) <= 0x7E):
            return None
    return raw


class AuthManager:
    DEFAULT_USERNAME = "admin"
    HASH_ROUNDS = 1000
    SALT_BYTES = 16
    TOKEN_BYTES = 24
    BOOTSTRAP_PASSWORD_BYTES = 9
    SESSION_SLOTS = 4
    SESSION_TTL_MS = 3_600_000
    LOCKOUT_SLOTS = 8
    MAX_FAILED_ATTEMPTS = 5
    FAIL_WINDOW_MS = 60_000
    LOCKOUT_DURATION_MS = 300_000

    def __init__(
        self,
        secrets_path="/flash/secrets.json",
        voice_engine=None,
        led_controller=None,
        time_ms_fn=None,
        print_bootstrap=True,
    ):
        self.secrets_path = _resolve_secrets_path(secrets_path)
        self.voice_engine = voice_engine
        self.led_controller = led_controller
        self._custom_clock = time_ms_fn is not None
        self._time_ms_fn = time_ms_fn
        self._sessions = tuple(_SessionSlot() for _ in range(self.SESSION_SLOTS))
        self._lockouts = tuple(_LockoutSlot() for _ in range(self.LOCKOUT_SLOTS))
        self._session_seq = 0
        self._lockout_seq = 0
        self.username = self.DEFAULT_USERNAME
        self._salt_bytes = b""
        self._password_salt_hex = ""
        self._password_hash_hex = ""
        self._beacon_token = ""
        self._extra_secrets = {}
        self.bootstrapped_password = None
        self._initialise_secrets(bool(print_bootstrap))

    def _now_ms(self):
        if self._custom_clock:
            return int(self._time_ms_fn())
        return _time.ticks_ms() if hasattr(_time, "ticks_ms") else int(_time.monotonic() * 1000)

    def _ticks_diff_ms(self, a_ms, b_ms):
        if self._custom_clock or not hasattr(_time, "ticks_diff"):
            return int(a_ms) - int(b_ms)
        return _time.ticks_diff(a_ms, b_ms)

    def _ticks_add_ms(self, base_ms, delta_ms):
        if self._custom_clock or not hasattr(_time, "ticks_add"):
            return int(base_ms) + int(delta_ms)
        return _time.ticks_add(base_ms, int(delta_ms))

    def _read_existing_dict(self):
        if _stat_mode(self.secrets_path) in (-1, 0x4000):
            return None
        try:
            with open(self.secrets_path, "rb") as f:
                parsed = _json.loads(f.read().decode("utf-8"))
            return parsed if isinstance(parsed, dict) else None
        except Exception:
            return None

    def _extract_extra_keys(self, data_dict):
        extra = {}
        if isinstance(data_dict, dict):
            for k, v in data_dict.items():
                if k not in _AUTH_KEYS and k != "bootstrapped_password":
                    extra[k] = v
        return extra

    def _write_secrets_atomic(self, extra_dict):
        _ensure_parent_dir(self.secrets_path)
        payload = {}
        if extra_dict:
            for k, v in extra_dict.items():
                if k not in _AUTH_KEYS and k != "bootstrapped_password":
                    payload[k] = v
        payload["username"] = self.username
        payload["password_salt_hex"] = self._password_salt_hex
        payload["password_hash_hex"] = self._password_hash_hex
        payload["beacon_token"] = self._beacon_token
        tmp_path = self.secrets_path + ".tmp"
        with open(tmp_path, "wb") as f:
            f.write((_json.dumps(payload) + "\n").encode("utf-8"))
        if hasattr(os, "replace"):
            try:
                os.replace(tmp_path, self.secrets_path)
                return
            except OSError:
                pass
        try:
            os.rename(tmp_path, self.secrets_path)
        except OSError:
            try:
                os.remove(self.secrets_path)
            except OSError:
                pass
            os.rename(tmp_path, self.secrets_path)

    def _initialise_secrets(self, print_bootstrap=True):
        existing = self._read_existing_dict()
        self._extra_secrets = self._extract_extra_keys(existing)
        if existing is not None:
            u = existing.get("username")
            s_hex = existing.get("password_salt_hex")
            h_hex = existing.get("password_hash_hex")
            b_tok = existing.get("beacon_token")
            s_raw = _decode_valid_hex(s_hex, min_bytes=self.SALT_BYTES)
            h_raw = _decode_valid_hex(h_hex, min_bytes=32, exact_bytes=32)
            if _is_valid_username(u) and s_raw is not None and h_raw is not None and _is_valid_beacon_token(b_tok):
                self.username = u
                self._salt_bytes = s_raw
                self._password_salt_hex = s_hex.lower()
                self._password_hash_hex = h_hex.lower()
                self._beacon_token = b_tok
                self.bootstrapped_password = None
                return

        salt_raw = os.urandom(self.SALT_BYTES)
        init_pw = _urlsafe_token(self.BOOTSTRAP_PASSWORD_BYTES)
        self.username = self.DEFAULT_USERNAME
        self._salt_bytes = salt_raw
        self._password_salt_hex = _binascii.hexlify(salt_raw).decode("ascii").lower()
        self._password_hash_hex = hash_password(init_pw, salt_raw, self.HASH_ROUNDS)
        self._beacon_token = _urlsafe_token(self.TOKEN_BYTES)
        self.bootstrapped_password = init_pw
        self._write_secrets_atomic(self._extra_secrets)
        gc.collect()
        if print_bootstrap:
            print(
                "[ARSE-AUTH] First-boot credentials initialised | Username: {} | Password: {} | Secrets: {}".format(
                    self.username, init_pw, self.secrets_path
                )
            )

    def _trigger_ack_sound(self):
        v = self.voice_engine
        if v is not None:
            try:
                if hasattr(v, "say_event"):
                    v.say_event("ACK_COMMAND")
                elif hasattr(v, "play"):
                    v.play("chirp")
            except Exception:
                pass

    def _trigger_lockout_alert(self):
        led = self.led_controller
        if led is not None:
            try:
                if hasattr(led, "set_temporary_overlay"):
                    led.set_temporary_overlay("strobe", 1500)
                elif hasattr(led, "set_mode"):
                    led.set_mode("strobe")
            except Exception:
                pass
        v = self.voice_engine
        if v is not None:
            try:
                if hasattr(v, "say_event"):
                    v.say_event("ERR_SYSTEM_FAULT")
                elif hasattr(v, "play"):
                    v.play("alarm")
            except Exception:
                pass

    def _normalise_ip(self, client_ip):
        if client_ip is None:
            return "0.0.0.0"
        ip = str(client_ip).strip()
        return ip[:128] if ip else "0.0.0.0"

    def _purge_expired_lockouts(self, now_ms):
        for slot in self._lockouts:
            if not slot.active:
                continue
            if slot.locked:
                if self._ticks_diff_ms(slot.locked_until_ms, now_ms) <= 0:
                    slot.clear()
            else:
                el = self._ticks_diff_ms(now_ms, slot.window_start_ms)
                if el > self.FAIL_WINDOW_MS or el < 0:
                    slot.clear()

    def _find_ip_slot(self, ip, now_ms):
        self._purge_expired_lockouts(now_ms)
        for slot in self._lockouts:
            if slot.active and slot.ip == ip:
                return slot
        return None

    def _select_lockout_slot(self):
        for slot in self._lockouts:
            if not slot.active:
                return slot
        oldest_u = None
        for slot in self._lockouts:
            if not slot.locked and (oldest_u is None or slot.seq < oldest_u.seq):
                oldest_u = slot
        if oldest_u is not None:
            return oldest_u
        oldest_l = self._lockouts[0]
        for slot in self._lockouts:
            if slot.seq < oldest_l.seq:
                oldest_l = slot
        return oldest_l

    def _record_ip_failure(self, ip, now_ms):
        slot = self._find_ip_slot(ip, now_ms)
        if slot is not None:
            el = self._ticks_diff_ms(now_ms, slot.window_start_ms)
            if el > self.FAIL_WINDOW_MS or el < 0:
                self._lockout_seq += 1
                slot.window_start_ms = now_ms
                slot.fail_count = 1
                slot.seq = self._lockout_seq
            else:
                slot.fail_count += 1
        else:
            slot = self._select_lockout_slot()
            self._lockout_seq += 1
            slot.active = True
            slot.ip = ip
            slot.fail_count = 1
            slot.window_start_ms = now_ms
            slot.locked = False
            slot.locked_until_ms = 0
            slot.seq = self._lockout_seq

        if slot.fail_count >= self.MAX_FAILED_ATTEMPTS:
            self._lockout_seq += 1
            slot.locked = True
            slot.locked_until_ms = self._ticks_add_ms(now_ms, self.LOCKOUT_DURATION_MS)
            slot.fail_count = 0
            slot.seq = self._lockout_seq
            self._trigger_lockout_alert()
            raise AuthRateLimitError(retry_after_s=self.LOCKOUT_DURATION_MS // 1000)

    def get_lockout_remaining_s(self, client_ip):
        ip = self._normalise_ip(client_ip)
        now_ms = self._now_ms()
        slot = self._find_ip_slot(ip, now_ms)
        if slot is None or not slot.locked:
            return 0
        rem_ms = self._ticks_diff_ms(slot.locked_until_ms, now_ms)
        if rem_ms <= 0:
            slot.clear()
            return 0
        return max(1, (rem_ms + 999) // 1000)

    def is_ip_locked(self, client_ip):
        return self.get_lockout_remaining_s(client_ip) > 0

    def _purge_expired_sessions(self, now_ms):
        for slot in self._sessions:
            if slot.active and self._ticks_diff_ms(now_ms, slot.expires_ms) >= 0:
                slot.clear()

    def login(self, username_str, password_str, client_ip="0.0.0.0"):
        ip = self._normalise_ip(client_ip)
        rem_s = self.get_lockout_remaining_s(ip)
        if rem_s > 0:
            raise AuthRateLimitError(retry_after_s=rem_s)
        now_ms = self._now_ms()
        if (
            not isinstance(username_str, str)
            or not isinstance(password_str, str)
            or len(username_str) > 256
            or len(password_str) > 256
        ):
            hash_password("", self._salt_bytes, self.HASH_ROUNDS)
            self._record_ip_failure(ip, now_ms)
            return None

        cand_hash = hash_password(password_str, self._salt_bytes, self.HASH_ROUNDS)
        u_ok = constant_time_compare(username_str, self.username)
        p_ok = constant_time_compare(cand_hash, self._password_hash_hex)
        if not (u_ok and p_ok):
            self._record_ip_failure(ip, now_ms)
            return None

        for slot in self._lockouts:
            if slot.active and slot.ip == ip:
                slot.clear()

        tok = _urlsafe_token(self.TOKEN_BYTES)
        self._purge_expired_sessions(now_ms)
        target = None
        for slot in self._sessions:
            if not slot.active:
                target = slot
                break
        if target is None:
            target = self._sessions[0]
            for slot in self._sessions:
                if slot.created_seq < target.created_seq:
                    target = slot
        self._session_seq += 1
        target.active = True
        target.token = tok
        target.expires_ms = self._ticks_add_ms(now_ms, self.SESSION_TTL_MS)
        target.created_seq = self._session_seq
        self._trigger_ack_sound()
        return tok

    def validate_bearer(self, token_str):
        raw = _extract_bearer_token(token_str)
        if raw is None:
            return None
        now_ms = self._now_ms()
        matched = None
        for slot in self._sessions:
            if not slot.active:
                continue
            if self._ticks_diff_ms(now_ms, slot.expires_ms) >= 0:
                slot.clear()
                continue
            if constant_time_compare(raw, slot.token):
                matched = slot
        if matched is not None:
            matched.expires_ms = self._ticks_add_ms(now_ms, self.SESSION_TTL_MS)
            return "operator"
        if constant_time_compare(raw, self._beacon_token):
            return "beacon_only"
        return None

    def logout(self, token_str=_UNSET):
        if token_str is _UNSET:
            self.logout_all()
            return True
        raw = _extract_bearer_token(token_str)
        self._purge_expired_sessions(self._now_ms())
        if raw is None:
            return False
        removed = False
        for slot in self._sessions:
            if slot.active and constant_time_compare(raw, slot.token):
                slot.clear()
                removed = True
        return removed

    def logout_all(self):
        now_ms = self._now_ms()
        cleared = 0
        for slot in self._sessions:
            if slot.active:
                if self._ticks_diff_ms(now_ms, slot.expires_ms) < 0:
                    cleared += 1
                slot.clear()
        return cleared

    def update_credentials(self, new_username, new_password, rotate_beacon=False):
        if not isinstance(new_username, str):
            raise ValueError("Username must be a string")
        cleaned = new_username.strip()
        if not _is_valid_username(cleaned):
            raise ValueError("Username must be 1..32 printable non-whitespace ASCII")
        if not isinstance(new_password, str) or not (8 <= len(new_password) <= 128):
            raise ValueError("Password must be 8..128 characters")

        new_salt = os.urandom(self.SALT_BYTES)
        new_salt_hex = _binascii.hexlify(new_salt).decode("ascii").lower()
        new_hash_hex = hash_password(new_password, new_salt, self.HASH_ROUNDS)
        new_beacon = _urlsafe_token(self.TOKEN_BYTES) if rotate_beacon else self._beacon_token

        disk_dict = self._read_existing_dict()
        if disk_dict is not None:
            self._extra_secrets = self._extract_extra_keys(disk_dict)

        prev = (self.username, self._salt_bytes, self._password_salt_hex, self._password_hash_hex, self._beacon_token)
        self.username = cleaned
        self._salt_bytes = new_salt
        self._password_salt_hex = new_salt_hex
        self._password_hash_hex = new_hash_hex
        self._beacon_token = new_beacon
        try:
            self._write_secrets_atomic(self._extra_secrets)
        except Exception:
            self.username, self._salt_bytes, self._password_salt_hex, self._password_hash_hex, self._beacon_token = prev
            raise

        self.bootstrapped_password = None
        self.logout_all()
        self._trigger_ack_sound()
        gc.collect()
        return {"ok": True, "username": self.username, "beacon_rotated": bool(rotate_beacon)}

    def get_beacon_token(self):
        return self._beacon_token

    def get_public_status(self):
        self._purge_expired_sessions(self._now_ms())
        active = 0
        for slot in self._sessions:
            if slot.active:
                active += 1
        return {"locked": active == 0, "active_sessions": active}
