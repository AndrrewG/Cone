# ARSE Mk.I — Idea Spec & Coding Prompt: Username/Password Authentication & Hijack Prevention (`IDEA-007`)

* **Project:** ARSE Mk.I Autonomous Traffic Cone (`C:\Users\Andrew\Cone`)
* **Hardware & Runtime:** Pimoroni Yukon (RP2040 / RP2350) running MicroPython (`uhashlib`, `ubinascii`, `os.urandom`, `uasyncio`, `ujson`, `utime`)
* **Authentication Model:** Standard Username + Password Login (`POST /api/auth/login` over HTTP, compatible with desktop browsers and Android Password Manager fingerprint autofill) + `Authorization: Bearer <session_token>` + Scoped Locked-Phone GPS Beacon Token (`IDEA-004`)
* **Target Folder:** `arse / cone / ideas`

---

## Part 1: Lightweight Security Architecture

### 1. Layer 1 — Wireless Perimeter & Isolated Secrets (`/flash/secrets.json`)

1. **WPA2-PSK Wi-Fi Perimeter:**
   * Whether the cone broadcasts its own Wi-Fi Access Point (`ARSE-Cone-AP`) or joins your personal hotspot as a station, enforce **WPA2-PSK** with a strong passphrase so casual passers-by cannot associate at the wireless layer.
2. **Git-Ignored Secrets File (`/flash/secrets.json`):**
   * Store all authentication credentials separately from application configuration in `/flash/secrets.json` (explicitly listed in `.gitignore` and excluded from any telemetry or config export endpoints):
     * `username`: Operator login name (default `"admin"`).
     * `password_salt_hex`: `16-byte` random salt generated on first boot via `os.urandom(16)`.
     * `password_hash_hex`: `1,000`-round iterated `SHA-256` digest of `salt + password.encode("utf-8")` (never stored in plaintext).
     * `beacon_token`: `24-byte` URL-safe random token scoped exclusively to locked-phone background GPS updates (`IDEA-004`).

---

### 2. Layer 2 — Username/Password Login & `Bearer` Session Tokens

1. **Constant-Time Verification (`constant_time_compare`):**
   * Compare both the username and the iterated `SHA-256` password digest using a fixed-length XOR byte accumulator so response timing never leaks credential characters.
2. **Login Flow (`POST /api/auth/login`):**
   * Accepts `{"username": "...", "password": "..."}`.
   * On valid credentials, generates a cryptographically random `24-byte` URL-safe `session_token` via `os.urandom(24)`, inserts `(session_token, expires_ms)` into a fixed **4-slot in-memory session table** (`3,600 s` rolling idle TTL), plays `"boop"` (`ACK_COMMAND`) via `VoiceEngine`, and returns `{"authenticated": true, "session_token": "<token>", "expires_in_s": 3600}`.
   * If all 4 session slots are occupied, evicts the oldest session entry cleanly without dynamic memory growth.
3. **Browser Storage & Biometric Autofill Synergy:**
   * The Web UI uses standard semantic `<form>` inputs (`name="username" autocomplete="username"` and `name="password" autocomplete="current-password"`), enabling one-click autofill in desktop browsers during bench testing and **Android fingerprint-gated autofill** via Google Password Manager in the field.
   * The browser stores `session_token` in `sessionStorage` and attaches `Authorization: Bearer <session_token>` to all `fetch()` calls. Because browsers never send `Authorization` headers automatically across sites, Cross-Site Request Forgery (CSRF) from other open browser tabs is prevented without cookies or CSRF tokens.

---

### 3. Layer 3 — Brute-Force Rate Limiting, Conish Tamper Alerts & Least-Privilege Scope

* **Fixed 8-Slot IP Brute-Force Lockout:**
  * Track failed login attempts per client IP inside a pre-allocated 8-slot circular table `(ip_str, fail_count, window_start_ms, locked_until_ms)`.
  * After **`5` failed attempts within `60 seconds`**, reject further login attempts from that IP for **`300 seconds`** (`HTTP 429 Too Many Requests` with `Retry-After: 300`), flash the 50-LED array in `"strobe"` mode for `1.5 s`, and trigger **`"ech_ung"`** (`voice_engine.say_event("ERR_SYSTEM_FAULT")`) so tampering is immediately obvious.
* **Scoped Locked-Phone Beacon Token (`IDEA-004`):**
  * Background location apps running while your phone is locked authenticate via `Authorization: Bearer <beacon_token>` (or `?token=<beacon_token>`).
  * Middleware assigns `"beacon_only"` scope to this token, permitting access **only** to `POST /api/follow/beacon` and `POST /api/follow/stamp`, while returning `HTTP 403 Forbidden` on `/api/arm`, `/api/mission`, `/api/routines/*`, `/api/lexicon`, `/api/auth/*`, and `/api/swarm/*`.
* **Explicit Hardware Arming & Logout Disarm:**
  * Logging in unlocks telemetry and mission staging, but drive motors remain **disarmed** until an explicit authenticated `POST /api/arm` (`"brrr_up"`) is executed.
  * Calling `POST /api/auth/logout` clears all active sessions from the 4-slot RAM table and disarms drive motors if no autonomous mission is currently running.

---

## Part 2: Coding AI Implementation Prompt (`agy` / VS Code)

Copy and paste the prompt below directly into your VS Code Gemini CLI (`agy`) session inside `C:\Users\Andrew\Cone`:

```markdown
You are implementing **Username/Password Web UI Authentication, Session Management & Brute-Force Lockout (`IDEA-007`)** in MicroPython (plus a clean Web UI login modal) for the **ARSE Mk.I Autonomous Traffic Cone** (Pimoroni Yukon RP2040/RP2350).

### Security & Runtime Context
- **Runtime:** Target MicroPython on Pimoroni Yukon (`uhashlib`, `ubinascii`, `os.urandom`, `uasyncio`, `ujson`, `utime`, `gc`). Do not use CPython-only libraries.
- **Cross-Platform Simplicity:** Use a standard HTTP `POST /api/auth/login` username/password flow (`Authorization: Bearer <session_token>` stored in browser `sessionStorage`) so it works identically on desktop browsers during bench testing and on Android Chrome (where standard password fields trigger native Google Password Manager fingerprint autofill) without requiring TLS certificates or mDNS workarounds.

### Tasks to Implement

#### 1. Zero-Dependency Auth & Rate-Limiter Module (`security/auth_manager.py`)
- Implement `AuthManager(secrets_path="/flash/secrets.json", voice_engine=None, led_controller=None)`:
  - On first boot, if `/flash/secrets.json` is missing, generate `password_salt_hex` (`16 bytes`), a random initial password hashed with `1,000` rounds of salted `uhashlib.sha256` (printing the initial username `"admin"` and generated password once to the USB serial REPL), and a `24-byte` URL-safe `beacon_token`. Ensure `secrets.json` is added to `.gitignore`.
  - Implement `hash_password(password_str, salt_bytes, rounds=1000)` and `constant_time_compare(a, b)` using a byte-by-byte XOR accumulator.
  - Implement a fixed 8-slot IP rate-limiter: lock out any client IP for `300 s` after `5` failed login attempts within `60 s`, flashing `"strobe"` LEDs and calling `voice_engine.say_event("ERR_SYSTEM_FAULT")` (`"ech_ung"`) upon lockout.
  - Implement `login(username_str, password_str, client_ip)`: verify credentials, generate a `24-byte` URL-safe `session_token`, store `(token, expires_ms)` in a fixed 4-slot RAM table (`3600 s` idle TTL, evicting oldest if full), trigger `"boop"` (`ACK_COMMAND`), and return `session_token`.
  - Implement `validate_bearer(token_str)` returning `"operator"` (refreshing idle expiry), `"beacon_only"` (matching `beacon_token`), or `None`.
  - Implement `logout_all()` and `update_credentials(new_username, new_password, rotate_beacon=False)` (persisting atomically to `/flash/secrets.json`).

#### 2. HTTP & WebSocket Auth Middleware (`web/server.py`)
- Add authentication endpoints to `web/server.py`:
  - `POST /api/auth/login`: Accepts `{"username": "...", "password": "..."}` $\rightarrow$ returns `{"authenticated": true, "session_token": "...", "expires_in_s": 3600}` (or `HTTP 401` on invalid credentials, `HTTP 429` with `Retry-After` if locked out).
  - `POST /api/auth/logout`: Clears active RAM sessions and disarms drive motors if idle.
  - `POST /api/auth/credentials`: Authenticated (`"operator"`) endpoint allowing username/password changes and `beacon_token` rotation (playing `"boop"` on success).
- Protect existing endpoints in `web/server.py`:
  - Allow unauthenticated `GET /` (serving the login screen UI shell) and `GET /api/health` (`{"online": true, "locked": true}`).
  - Require `"operator"` `Authorization: Bearer <token>` header on all telemetry, mission, routine, exploration, lexicon, arming, and swarm endpoints (`/api/*`) as well as `/ws/follow` (accepting `?token=<session_token>` on WebSocket upgrade).
  - Restrict `"beacon_only"` tokens strictly to `POST /api/follow/beacon` and `POST /api/follow/stamp` (`HTTP 403 Forbidden` on every other route).

#### 3. Desktop & Mobile Web UI Login Screen (`web/index.html` & `web/auth.js`)
- Add a clean **Login Overlay Modal** in `web/index.html` containing a standard `<form>` with `<input type="text" name="username" autocomplete="username">` and `<input type="password" name="password" autocomplete="current-password">` so desktop browsers and Android fingerprint password autofill work natively.
- Wrap all browser `fetch()` calls in `web/auth.js` (`apiFetch(url, options)`) injecting `Authorization: Bearer <session_token>` from `sessionStorage` and automatically showing the login overlay upon any `HTTP 401` response.
- Add a **"Security & Account"** card in the Web UI Settings tab with **Lock Cone Now (Logout)**, **Update Username / Password**, and **Copy / Rotate Locked-Phone Beacon URL** controls.

### Constraints & Verification
- Target MicroPython on Pimoroni Yukon (`uhashlib`, `ubinascii`, `os`, `uasyncio`, `ujson`, `utime`, `gc`); keep login hashing fast (`< 20 ms`) and avoid unbounded IP tracking structures.
- Add desktop Python 3 unit tests in `tests/test_auth_manager.py` verifying salted iterated `SHA-256` hashing, constant-time comparison, 4-slot session eviction, 5-attempt IP lockout, and `"beacon_only"` route isolation.
```

