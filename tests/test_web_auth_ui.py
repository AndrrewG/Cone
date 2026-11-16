# ==============================================================================
# ARSE — Autonomous Road Systems Engineering
# Suite:       tests/test_web_auth_ui.py
# Description: Task 1.3 verification suite covering semantic HTML autofill attributes,
#              unauthenticated /auth.js serving, standalone beacon rotation, and
#              headless JS execution of web/auth.js (sessionStorage, apiFetch, 401/429).
# ==============================================================================

import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest
from html.parser import HTMLParser

from security.auth_manager import AuthManager
from telemetry_server import TelemetryServer
from tests.test_auth_middleware import (
    FakeConeApp,
    MockFollowController,
    _MemoryReader,
    _MemoryWriter,
    _drive_coro,
)

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HTML_PATH = os.path.join(REPO_ROOT, "web", "index.html")
AUTH_JS_PATH = os.path.join(REPO_ROOT, "web", "auth.js")


class _DomAuditParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.elements = []
        self._form_stack = []
        self._in_script = False
        self._current_script_src = None
        self._script_buf = []
        self.scripts = []

    def handle_starttag(self, tag, attrs):
        attr_dict = {k.lower(): (v if v is not None else "") for k, v in attrs}
        parent_form = self._form_stack[-1] if self._form_stack else None
        rec = {"tag": tag.lower(), "attrs": attr_dict, "form_id": parent_form}
        self.elements.append(rec)
        if tag.lower() == "form":
            self._form_stack.append(attr_dict.get("id", ""))
        elif tag.lower() == "script":
            self._in_script = True
            self._current_script_src = attr_dict.get("src")
            self._script_buf = []

    def handle_endtag(self, tag):
        t = tag.lower()
        if t == "form" and self._form_stack:
            self._form_stack.pop()
        elif t == "script" and self._in_script:
            self.scripts.append(
                {"src": self._current_script_src, "code": "".join(self._script_buf)}
            )
            self._in_script = False
            self._current_script_src = None
            self._script_buf = []

    def handle_data(self, data):
        if self._in_script:
            self._script_buf.append(data)


class WebIndexSemanticHtmlAuditTests(unittest.TestCase):
    """Audits web/index.html for desktop & Android Password Manager autofill compliance."""

    @classmethod
    def setUpClass(cls):
        with open(HTML_PATH, "r", encoding="utf-8") as f:
            cls.raw_html = f.read()
        cls.dom = _DomAuditParser()
        cls.dom.feed(cls.raw_html)

    def test_auth_js_script_loaded_before_inline_app_script(self):
        self.assertGreaterEqual(len(self.dom.scripts), 2)
        self.assertEqual(self.dom.scripts[0]["src"], "/auth.js")
        self.assertIsNone(self.dom.scripts[1]["src"])

    def test_login_modal_uses_semantic_form_and_autofill_attributes(self):
        overlays = [
            e
            for e in self.dom.elements
            if e["attrs"].get("id") == "loginOverlay"
        ]
        self.assertEqual(len(overlays), 1)

        forms = [
            e
            for e in self.dom.elements
            if e["tag"] == "form" and e["attrs"].get("id") == "loginForm"
        ]
        self.assertEqual(len(forms), 1)
        self.assertEqual(forms[0]["attrs"].get("action"), "/api/auth/login")
        self.assertEqual(forms[0]["attrs"].get("method", "").lower(), "post")

        login_inputs = [
            e
            for e in self.dom.elements
            if e["tag"] == "input" and e["form_id"] == "loginForm"
        ]
        by_name = {e["attrs"].get("name"): e["attrs"] for e in login_inputs}
        self.assertIn("username", by_name)
        self.assertIn("password", by_name)

        self.assertEqual(by_name["username"].get("type", "text"), "text")
        self.assertEqual(by_name["username"].get("autocomplete"), "username")
        self.assertIn("required", by_name["username"])

        self.assertEqual(by_name["password"].get("type"), "password")
        self.assertEqual(by_name["password"].get("autocomplete"), "current-password")
        self.assertIn("required", by_name["password"])

        submit_btns = [
            e
            for e in self.dom.elements
            if e["tag"] == "button"
            and e["form_id"] == "loginForm"
            and e["attrs"].get("type") == "submit"
        ]
        self.assertEqual(len(submit_btns), 1)

    def test_security_and_account_card_exposes_logout_credentials_and_beacon_controls(self):
        ids = {e["attrs"].get("id") for e in self.dom.elements if "id" in e["attrs"]}
        for required_id in (
            "securityCard",
            "btnLockCone",
            "btnLockQuick",
            "credUpdateForm",
            "credUsername",
            "credNewPassword",
            "credConfirmPassword",
            "credRotateBeacon",
            "btnSaveCredentials",
            "secBeaconDisplay",
            "btnRevealBeacon",
            "btnRotateBeacon",
        ):
            self.assertIn(required_id, ids)

        cred_inputs = {
            e["attrs"].get("id"): e["attrs"]
            for e in self.dom.elements
            if e["tag"] == "input" and e["form_id"] == "credUpdateForm"
        }
        self.assertEqual(cred_inputs["credUsername"].get("autocomplete"), "username")
        self.assertEqual(cred_inputs["credNewPassword"].get("autocomplete"), "new-password")
        self.assertEqual(cred_inputs["credConfirmPassword"].get("autocomplete"), "new-password")

    def test_inline_script_routes_all_requests_through_apifetch(self):
        inline_js = "\n".join(
            s["code"] for s in self.dom.scripts if s["src"] is None
        )
        raw_fetch_calls = re.findall(r"(?<![A-Za-z0-9_$.])fetch\s*\(", inline_js)
        self.assertEqual(
            raw_fetch_calls,
            [],
            f"Found unauthenticated raw fetch() calls in web/index.html: {raw_fetch_calls}",
        )
        self.assertGreaterEqual(len(re.findall(r"\bapiFetch\s*\(", inline_js)), 4)

    def test_sync_scripts_include_web_auth_js(self):
        for script_rel in ("tools/sync_all.ps1", "tools/sync_wifi.ps1"):
            with open(os.path.join(REPO_ROOT, script_rel), "r", encoding="utf-8") as f:
                txt = f.read()
            self.assertIn("web/auth.js", txt)


class WebServerAuthAssetAndBeaconRotationTests(unittest.TestCase):
    """Verifies live TelemetryServer serving of real web/index.html, web/auth.js & beacon rotation."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.secrets_path = os.path.join(self._tmp.name, "secrets.json")
        self.clock_ms = 10_000
        self.app = FakeConeApp()
        self.app.follow = MockFollowController()
        self.auth = AuthManager(
            secrets_path=self.secrets_path,
            voice_engine=self.app.audio,
            led_controller=self.app.lights,
            time_ms_fn=lambda: self.clock_ms,
            print_bootstrap=False,
        )
        self.password = self.auth.bootstrapped_password
        self.app.auth = self.auth
        self.server = TelemetryServer(
            app=self.app,
            html_path=HTML_PATH,
            port=0,
            auth_manager=self.auth,
        )

    def tearDown(self):
        self._tmp.cleanup()

    def _req(self, method, target, headers=None, json_body=None):
        hdrs = {"Host": "192.168.50.13", "Connection": "close"}
        if headers:
            hdrs.update(headers)
        body_bytes = b""
        if json_body is not None:
            body_bytes = json.dumps(json_body).encode("utf-8")
            hdrs["Content-Type"] = "application/json"
            hdrs["Content-Length"] = str(len(body_bytes))
        elif method == "POST":
            hdrs["Content-Length"] = "0"
        wire = (
            f"{method} {target} HTTP/1.1\r\n"
            + "".join(f"{k}: {v}\r\n" for k, v in hdrs.items())
            + "\r\n"
        ).encode("utf-8") + body_bytes
        reader = _MemoryReader(wire)
        writer = _MemoryWriter(peer_ip="192.168.50.25")
        _drive_coro(self.server._handle_client(reader, writer))
        head, _, body = bytes(writer.buf).partition(b"\r\n\r\n")
        status_code = int(head.decode("iso-8859-1").split("\r\n")[0].split(" ")[1])
        return status_code, head.decode("iso-8859-1"), body

    def test_real_web_index_and_auth_js_served_unauthenticated(self):
        with open(HTML_PATH, "rb") as f:
            expected_html = f.read()
        with open(AUTH_JS_PATH, "rb") as f:
            expected_js = f.read()

        sc_html, hdr_html, body_html = self._req("GET", "/")
        self.assertEqual(sc_html, 200)
        self.assertIn("text/html", hdr_html.lower())
        self.assertEqual(body_html, expected_html)

        for js_route in ("/auth.js", "/web/auth.js"):
            sc_js, hdr_js, body_js = self._req("GET", js_route)
            self.assertEqual(sc_js, 200)
            self.assertIn("javascript", hdr_js.lower())
            self.assertEqual(body_js, expected_js)

    def test_standalone_rotate_beacon_only_preserves_operator_session_and_revokes_old_beacon(self):
        # Unauthenticated rotate rejected with 401
        sc_unauth, _, _ = self._req(
            "POST", "/api/auth/credentials", json_body={"rotate_beacon_only": True}
        )
        self.assertEqual(sc_unauth, 401)

        # Beacon-only token rejected with 403
        old_beacon = self.auth.get_beacon_token()
        sc_b_forbid, _, _ = self._req(
            "POST",
            "/api/auth/credentials",
            headers={"Authorization": f"Bearer {old_beacon}"},
            json_body={"rotate_beacon_only": True},
        )
        self.assertEqual(sc_b_forbid, 403)

        # Operator logs in and rotates beacon_token without changing password
        sc_login, _, b_login = self._req(
            "POST",
            "/api/auth/login",
            json_body={"username": "admin", "password": self.password},
        )
        self.assertEqual(sc_login, 200)
        tok = json.loads(b_login.decode("utf-8"))["session_token"]
        op_hdr = {"Authorization": f"Bearer {tok}"}

        sc_rot, _, b_rot = self._req(
            "POST",
            "/api/auth/credentials",
            headers=op_hdr,
            json_body={"rotate_beacon_only": True},
        )
        self.assertEqual(sc_rot, 200)
        rot_data = json.loads(b_rot.decode("utf-8"))
        self.assertTrue(rot_data["ok"])
        self.assertTrue(rot_data["beacon_rotated"])
        new_beacon = rot_data["beacon_token"]
        self.assertNotEqual(new_beacon, old_beacon)

        # Operator's session_token remains valid!
        sc_tel, _, _ = self._req("GET", "/api/telemetry", headers=op_hdr)
        self.assertEqual(sc_tel, 200)

        # Old beacon rejected (401); new beacon accepted on /api/follow/beacon (200) & 403 elsewhere
        sc_old_b, _, _ = self._req(
            "POST",
            "/api/follow/beacon",
            headers={"Authorization": f"Bearer {old_beacon}"},
            json_body={"lat": 51.5, "lon": -0.1},
        )
        self.assertEqual(sc_old_b, 401)

        sc_new_b, _, _ = self._req(
            "POST",
            "/api/follow/beacon",
            headers={"Authorization": f"Bearer {new_beacon}"},
            json_body={"lat": 51.5, "lon": -0.1},
        )
        self.assertEqual(sc_new_b, 200)


@unittest.skipUnless(shutil.which("node"), "Node.js runtime unavailable")
class AuthJsRuntimeBehaviourTests(unittest.TestCase):
    """Executes web/auth.js in Node.js with mocked DOM, sessionStorage & fetch."""

    def test_auth_js_login_apifetch_401_429_logout_and_credential_controls(self):
        node_script = r"""
const assert = require('assert');
const fs = require('fs');

function makeEl(id, init = {}) {
  const classes = new Set(init.classes || []);
  const attrs = {};
  return {
    id,
    value: init.value || '',
    type: init.type || 'text',
    checked: !!init.checked,
    disabled: false,
    textContent: '',
    style: { display: 'none' },
    className: '',
    focused: false,
    selected: false,
    classList: {
      add: (c) => classes.add(c),
      remove: (c) => classes.delete(c),
      contains: (c) => classes.has(c),
      toggle: (c, force) => {
        const on = force === undefined ? !classes.has(c) : !!force;
        if (on) classes.add(c); else classes.delete(c);
        return on;
      }
    },
    setAttribute: (k, v) => { attrs[k] = v; },
    getAttribute: (k) => attrs[k],
    focus: function () { this.focused = true; },
    select: function () { this.selected = true; }
  };
}

const els = {
  loginOverlay: makeEl('loginOverlay'),
  loginUsername: makeEl('loginUsername', { value: 'admin' }),
  loginPassword: makeEl('loginPassword', { type: 'password' }),
  loginSubmitBtn: makeEl('loginSubmitBtn'),
  loginErrorBox: makeEl('loginErrorBox'),
  loginHealthBadge: makeEl('loginHealthBadge'),
  btnToggleLoginPw: makeEl('btnToggleLoginPw'),
  secOperatorName: makeEl('secOperatorName'),
  secBeaconDisplay: makeEl('secBeaconDisplay'),
  btnRevealBeacon: makeEl('btnRevealBeacon'),
  btnRotateBeacon: makeEl('btnRotateBeacon'),
  credUsername: makeEl('credUsername', { value: 'admin' }),
  credNewPassword: makeEl('credNewPassword', { type: 'password' }),
  credConfirmPassword: makeEl('credConfirmPassword', { type: 'password' }),
  credRotateBeacon: makeEl('credRotateBeacon', { type: 'checkbox' }),
  btnSaveCredentials: makeEl('btnSaveCredentials')
};

const storageMap = new Map();
const fetchLog = [];
let nextResponses = [];
let lockedCount = 0;
let unlockedCount = 0;
let copiedText = '';
const toasts = [];

global.window = global;
global.location = { origin: 'http://192.168.50.13' };
global.sessionStorage = {
  getItem: (k) => (storageMap.has(k) ? storageMap.get(k) : null),
  setItem: (k, v) => storageMap.set(k, String(v)),
  removeItem: (k) => storageMap.delete(k)
};
Object.defineProperty(global, 'navigator', {
  value: {
    clipboard: {
      writeText: async (t) => { copiedText = t; }
    }
  },
  configurable: true
});
global.document = {
  getElementById: (id) => els[id] || null,
  createElement: () => makeEl('tmp'),
  body: { appendChild: () => {}, removeChild: () => {} },
  execCommand: () => true
};
global.showToast = (m) => toasts.push(m);
global.onArseAuthLocked = () => { lockedCount += 1; };
global.onArseAuthUnlocked = () => { unlockedCount += 1; };

function queueResp(status, bodyObj, headers = {}) {
  nextResponses.push({
    status,
    ok: status >= 200 && status < 300,
    headers: { get: (k) => headers[k] || headers[k.toLowerCase()] || null },
    json: async () => bodyObj
  });
}

global.fetch = async (url, opts = {}) => {
  if (url === '/api/health') {
    return { status: 200, ok: true, headers: { get: () => null }, json: async () => ({ online: true, locked: true }) };
  }
  fetchLog.push({ url, opts });
  if (!nextResponses.length) {
    return { status: 200, ok: true, headers: { get: () => null }, json: async () => ({ ok: true }) };
  }
  return nextResponses.shift();
};

const ArseAuth = require(process.argv[1]);

(async () => {
  // 1. Boot with empty sessionStorage -> modal opens & onArseAuthLocked fires
  ArseAuth.initAuth();
  assert.strictEqual(ArseAuth.hasSession(), false);
  assert.strictEqual(ArseAuth.isModalOpen(), true);
  assert.strictEqual(lockedCount, 1);

  // 2. Password visibility toggle
  ArseAuth.toggleLoginPasswordVisibility();
  assert.strictEqual(els.loginPassword.type, 'text');
  ArseAuth.toggleLoginPasswordVisibility();
  assert.strictEqual(els.loginPassword.type, 'password');

  // 3. 429 lockout response disables button and starts countdown
  els.loginUsername.value = 'admin';
  els.loginPassword.value = 'wrongpass';
  queueResp(429, { ok: false, error: 'Too many failed login attempts', retry_after_s: 300 }, { 'Retry-After': '300' });
  await ArseAuth.handleLoginSubmit({ preventDefault: () => {} });
  assert.strictEqual(els.loginSubmitBtn.disabled, true);
  assert.ok(els.loginErrorBox.textContent.includes('300s') || els.loginErrorBox.textContent.includes('299s'));
  ArseAuth.hideLoginModal(); // clears countdown timer cleanly

  // 4. Valid login stores session_token in sessionStorage, hides modal, loads beacon
  ArseAuth.showLoginModal('');
  els.loginPassword.value = 'CorrectHorseBatteryStaple';
  queueResp(200, { ok: true, authenticated: true, session_token: 'tok_abc123', username: 'admin', expires_in_s: 3600 });
  queueResp(200, { ok: true, username: 'admin', beacon_token: 'bcn_secret_9876543210' });
  await ArseAuth.handleLoginSubmit({ preventDefault: () => {} });
  await new Promise((r) => setTimeout(r, 20));

  assert.strictEqual(ArseAuth.getToken(), 'tok_abc123');
  assert.strictEqual(storageMap.get('arse_session_token'), 'tok_abc123');
  assert.strictEqual(ArseAuth.isModalOpen(), false);
  assert.strictEqual(unlockedCount, 1);
  assert.ok(els.secBeaconDisplay.value.includes('\u2022'));

  // 5. apiFetch injects Authorization: Bearer <token>
  fetchLog.length = 0;
  queueResp(200, { battery: {} });
  const rTel = await global.apiFetch('/api/telemetry', { cache: 'no-store' });
  assert.strictEqual(rTel.status, 200);
  assert.strictEqual(fetchLog[0].opts.headers['Authorization'], 'Bearer tok_abc123');

  // 6. Copy beacon URL formats locked-phone endpoint accurately
  await ArseAuth.copyBeaconUrl();
  assert.strictEqual(copiedText, 'http://192.168.50.13/api/follow/beacon?token=bcn_secret_9876543210');

  // 7. 2-tap Rotate Beacon Token
  await ArseAuth.rotateBeaconTokenNow(); // tap 1 arms confirmation
  assert.ok(els.btnRotateBeacon.textContent.includes('Tap Again'));
  queueResp(200, { ok: true, username: 'admin', beacon_rotated: true, beacon_token: 'bcn_rotated_11112222' });
  await ArseAuth.rotateBeaconTokenNow(); // tap 2 executes rotation
  await ArseAuth.copyBeaconToken();
  assert.strictEqual(copiedText, 'bcn_rotated_11112222');

  // 8. Credential update validates mismatch locally, then updates & re-authenticates seamlessly
  els.credUsername.value = 'road_ops';
  els.credNewPassword.value = 'NewStrongPass!88';
  els.credConfirmPassword.value = 'MismatchPass!88';
  fetchLog.length = 0;
  await ArseAuth.submitCredentialUpdate({ preventDefault: () => {} });
  assert.strictEqual(fetchLog.length, 0);

  els.credConfirmPassword.value = 'NewStrongPass!88';
  els.credRotateBeacon.checked = true;
  queueResp(200, { ok: true, username: 'road_ops', beacon_rotated: true, beacon_token: 'bcn_after_pw_change' });
  queueResp(200, { ok: true, authenticated: true, session_token: 'tok_fresh_999', username: 'road_ops', expires_in_s: 3600 });
  await ArseAuth.submitCredentialUpdate({ preventDefault: () => {} });
  assert.strictEqual(ArseAuth.getToken(), 'tok_fresh_999');
  assert.strictEqual(ArseAuth.getUsername(), 'road_ops');

  // 9. HTTP 401 on protected endpoint clears session & re-opens Login Overlay Modal
  queueResp(401, { ok: false, error: 'Authentication required' });
  const r401 = await global.apiFetch('/api/telemetry');
  assert.strictEqual(r401.status, 401);
  assert.strictEqual(ArseAuth.hasSession(), false);
  assert.strictEqual(ArseAuth.isModalOpen(), true);

  // 10. Explicit Lock Cone Now calls /api/auth/logout and opens modal
  ArseAuth.setSession('tok_temp_55', 'road_ops');
  ArseAuth.hideLoginModal();
  queueResp(200, { ok: true, logged_out: true, motors_disarmed: true });
  await ArseAuth.lockConeNow();
  assert.strictEqual(ArseAuth.hasSession(), false);
  assert.strictEqual(ArseAuth.isModalOpen(), true);

  process.exit(0);
})().catch((err) => {
  console.error(err);
  process.exit(1);
});
"""
        proc = subprocess.run(
            ["node", "-e", node_script, AUTH_JS_PATH],
            capture_output=True,
            text=True,
            timeout=10,
        )
        self.assertEqual(
            proc.returncode,
            0,
            msg=f"Node.js auth.js test failed:\nSTDOUT: {proc.stdout}\nSTDERR: {proc.stderr}",
        )


if __name__ == "__main__":
    unittest.main()
