// ==============================================================================
// ARSE — Autonomous Road Systems Engineering
// Module:      web/auth.js
// Platform:    ARSE Mk.I Autonomous Traffic Cone (Pimoroni Yukon RP2040 / RP2350)
// Description: Browser Bearer session manager, apiFetch() wrapper, Android/Desktop
//              password-manager autofill login flow & credential rotation controls.
// ==============================================================================
(function (global) {
  'use strict';

  var TOKEN_KEY = 'arse_session_token';
  var USER_KEY = 'arse_username';
  var memToken = '';
  var memUser = 'admin';
  var lockoutTimer = null;
  var lockoutRemainingS = 0;
  var latestBeaconToken = '';
  var beaconRevealed = false;
  var rotateConfirmArmed = false;
  var rotateConfirmTimer = null;

  function getToken() {
    try {
      var v = global.sessionStorage ? global.sessionStorage.getItem(TOKEN_KEY) : '';
      return v || memToken || '';
    } catch (e) {
      return memToken || '';
    }
  }

  function getUsername() {
    try {
      var u = global.sessionStorage ? global.sessionStorage.getItem(USER_KEY) : '';
      return u || memUser || 'admin';
    } catch (e) {
      return memUser || 'admin';
    }
  }

  function setSession(token, username) {
    memToken = token || '';
    if (username) memUser = username;
    try {
      if (global.sessionStorage) {
        if (memToken) global.sessionStorage.setItem(TOKEN_KEY, memToken);
        else global.sessionStorage.removeItem(TOKEN_KEY);
        if (username) global.sessionStorage.setItem(USER_KEY, username);
      }
    } catch (e) {}
    syncOperatorLabels();
  }

  function clearSession() {
    memToken = '';
    latestBeaconToken = '';
    try {
      if (global.sessionStorage) {
        global.sessionStorage.removeItem(TOKEN_KEY);
      }
    } catch (e) {}
    renderBeaconField();
  }

  function hasSession() {
    return !!getToken();
  }

  function byId(id) {
    return global.document ? global.document.getElementById(id) : null;
  }

  function notifyToast(msg) {
    if (typeof global.showToast === 'function') {
      global.showToast(msg);
    }
  }

  function syncOperatorLabels() {
    var u = getUsername();
    var opEl = byId('secOperatorName');
    if (opEl) opEl.textContent = u;
    var credUser = byId('credUsername');
    if (credUser && !credUser.value) credUser.value = u;
    var loginUser = byId('loginUsername');
    if (loginUser && !loginUser.value) loginUser.value = u;
  }

  function setLoginError(msg, isLockout) {
    var box = byId('loginErrorBox');
    if (!box) return;
    if (!msg) {
      box.style.display = 'none';
      box.textContent = '';
      box.classList.remove('lockout');
      return;
    }
    box.textContent = msg;
    box.style.display = 'block';
    box.classList.toggle('lockout', !!isLockout);
  }

  function stopLockoutCountdown() {
    if (lockoutTimer) {
      clearInterval(lockoutTimer);
      lockoutTimer = null;
    }
    lockoutRemainingS = 0;
    var btn = byId('loginSubmitBtn');
    if (btn) {
      btn.disabled = false;
      btn.textContent = 'Sign In to Operator Terminal';
    }
  }

  function startLockoutCountdown(seconds) {
    stopLockoutCountdown();
    lockoutRemainingS = Math.max(1, parseInt(seconds, 10) || 300);
    var btn = byId('loginSubmitBtn');

    function tick() {
      if (lockoutRemainingS <= 0) {
        stopLockoutCountdown();
        setLoginError('Lockout expired — you may sign in now.', false);
        return;
      }
      if (btn) {
        btn.disabled = true;
        btn.textContent = 'Locked (' + lockoutRemainingS + 's)';
      }
      setLoginError(
        'Too many failed sign-in attempts. Cone locked for ' + lockoutRemainingS + 's.',
        true
      );
      lockoutRemainingS -= 1;
    }

    tick();
    lockoutTimer = setInterval(tick, 1000);
  }

  function isModalOpen() {
    var ov = byId('loginOverlay');
    return !!(ov && ov.classList.contains('open'));
  }

  function showLoginModal(message) {
    var ov = byId('loginOverlay');
    if (!ov) return;
    syncOperatorLabels();
    ov.classList.add('open');
    ov.setAttribute('aria-hidden', 'false');
    if (message !== undefined && !lockoutTimer) {
      setLoginError(message, false);
    }
    probeHealthBanner();
    setTimeout(function () {
      var uInp = byId('loginUsername');
      var pInp = byId('loginPassword');
      if (uInp && !uInp.value) uInp.focus();
      else if (pInp) pInp.focus();
    }, 60);
  }

  function hideLoginModal() {
    var ov = byId('loginOverlay');
    if (!ov) return;
    stopLockoutCountdown();
    setLoginError('', false);
    ov.classList.remove('open');
    ov.setAttribute('aria-hidden', 'true');
  }

  async function probeHealthBanner() {
    var badge = byId('loginHealthBadge');
    if (!badge) return;
    try {
      var r = await global.fetch('/api/health', { cache: 'no-store' });
      if (!r.ok) throw new Error('HTTP ' + r.status);
      var d = await r.json();
      badge.className = 'login-health online';
      badge.textContent = d && d.online
        ? 'Cone Online \u2022 Motors Disarmed Until Explicit Arm'
        : 'Cone Reachable';
    } catch (e) {
      badge.className = 'login-health offline';
      badge.textContent = 'Cone Offline \u2022 Waiting for Wi-Fi link...';
    }
  }

  async function apiFetch(url, options) {
    var opts = Object.assign({}, options || {});
    var skipAuth = !!opts.skipAuth;
    delete opts.skipAuth;

    var headers = {};
    if (opts.headers) {
      if (typeof Headers !== 'undefined' && opts.headers instanceof Headers) {
        opts.headers.forEach(function (v, k) { headers[k] = v; });
      } else {
        Object.assign(headers, opts.headers);
      }
    }

    var tok = getToken();
    if (!skipAuth && tok && !headers['Authorization'] && !headers['authorization']) {
      headers['Authorization'] = 'Bearer ' + tok;
    }
    opts.headers = headers;

    var resp = await global.fetch(url, opts);
    if (resp.status === 401 && !skipAuth && String(url).indexOf('/api/auth/login') === -1) {
      clearSession();
      if (typeof global.onArseAuthLocked === 'function') {
        global.onArseAuthLocked();
      }
      if (!isModalOpen()) {
        showLoginModal('Authentication required — please sign in.');
      }
    }
    return resp;
  }

  async function handleLoginSubmit(ev) {
    if (ev && typeof ev.preventDefault === 'function') ev.preventDefault();
    if (lockoutTimer) return false;

    var uInp = byId('loginUsername');
    var pInp = byId('loginPassword');
    var btn = byId('loginSubmitBtn');
    var username = uInp ? uInp.value.trim() : '';
    var password = pInp ? pInp.value : '';

    if (!username || !password) {
      setLoginError('Enter both username and password.', false);
      return false;
    }

    if (btn) {
      btn.disabled = true;
      btn.textContent = 'Verifying...';
    }
    setLoginError('', false);

    try {
      var resp = await apiFetch('/api/auth/login', {
        method: 'POST',
        skipAuth: true,
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ username: username, password: password })
      });

      var data = {};
      try { data = await resp.json(); } catch (e) {}

      if (resp.status === 429) {
        var retryHdr = resp.headers ? resp.headers.get('Retry-After') : null;
        var retryS = (data && data.retry_after_s) || parseInt(retryHdr || '300', 10) || 300;
        startLockoutCountdown(retryS);
        return false;
      }

      if (resp.status === 401 || !resp.ok || !data.authenticated || !data.session_token) {
        if (pInp) { pInp.value = ''; pInp.focus(); }
        setLoginError((data && data.error) || 'Invalid username or password.', false);
        return false;
      }

      setSession(data.session_token, data.username || username);
      if (pInp) pInp.value = '';
      hideLoginModal();
      notifyToast('Signed in as ' + (data.username || username));
      refreshSecurityCard();
      if (typeof global.onArseAuthUnlocked === 'function') {
        global.onArseAuthUnlocked();
      }
    } catch (e) {
      setLoginError('Cannot reach Cone over Wi-Fi. Check power & connection.', false);
    } finally {
      if (btn && !lockoutTimer) {
        btn.disabled = false;
        btn.textContent = 'Sign In to Operator Terminal';
      }
    }
    return false;
  }

  function toggleLoginPasswordVisibility() {
    var pInp = byId('loginPassword');
    var tBtn = byId('btnToggleLoginPw');
    if (!pInp) return;
    var show = pInp.type === 'password';
    pInp.type = show ? 'text' : 'password';
    if (tBtn) tBtn.textContent = show ? 'Hide' : 'Show';
  }

  async function lockConeNow() {
    if (typeof global.onArseAuthLocked === 'function') {
      global.onArseAuthLocked();
    }
    var disarmed = true;
    try {
      var resp = await apiFetch('/api/auth/logout', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: '{}'
      });
      var d = await resp.json();
      if (d && d.motors_disarmed === false && d.mission_active) {
        disarmed = false;
      }
    } catch (e) {}
    clearSession();
    showLoginModal(
      disarmed
        ? 'Cone locked — sessions cleared & drive motors disarmed.'
        : 'Terminal locked — active mission left running.'
    );
    notifyToast('Cone Locked');
  }

  function buildBeaconUrl(token) {
    var origin = (global.location && global.location.origin && global.location.origin !== 'null')
      ? global.location.origin
      : 'http://192.168.50.13';
    return origin + '/api/follow/beacon?token=' + encodeURIComponent(token || '');
  }

  function maskToken(tok) {
    if (!tok) return 'Not loaded';
    if (tok.length <= 8) return '\u2022\u2022\u2022\u2022\u2022\u2022\u2022\u2022';
    return tok.slice(0, 4) + '\u2022\u2022\u2022\u2022\u2022\u2022\u2022\u2022\u2022\u2022\u2022\u2022' + tok.slice(-4);
  }

  function renderBeaconField() {
    var inp = byId('secBeaconDisplay');
    var revBtn = byId('btnRevealBeacon');
    if (!inp) return;
    if (!latestBeaconToken) {
      inp.value = 'Sign in to view scoped beacon token';
      if (revBtn) revBtn.textContent = 'Show';
      return;
    }
    inp.value = beaconRevealed ? latestBeaconToken : maskToken(latestBeaconToken);
    if (revBtn) revBtn.textContent = beaconRevealed ? 'Hide' : 'Show';
  }

  function toggleBeaconReveal() {
    if (!latestBeaconToken) {
      refreshSecurityCard();
      return;
    }
    beaconRevealed = !beaconRevealed;
    renderBeaconField();
  }

  async function refreshSecurityCard() {
    syncOperatorLabels();
    if (!hasSession()) return;
    try {
      var resp = await apiFetch('/api/auth/beacon', { cache: 'no-store' });
      if (!resp.ok) return;
      var d = await resp.json();
      if (d && d.ok) {
        if (d.username) setSession(getToken(), d.username);
        latestBeaconToken = d.beacon_token || '';
        renderBeaconField();
      }
    } catch (e) {}
  }

  async function copyTextSafe(text, label) {
    if (!text) {
      notifyToast('Nothing to copy yet');
      return false;
    }
    try {
      if (global.navigator && global.navigator.clipboard && global.navigator.clipboard.writeText) {
        await global.navigator.clipboard.writeText(text);
        notifyToast('Copied ' + label);
        return true;
      }
    } catch (e) {}
    try {
      var ta = global.document.createElement('textarea');
      ta.value = text;
      ta.setAttribute('readonly', '');
      ta.style.position = 'fixed';
      ta.style.top = '-9999px';
      global.document.body.appendChild(ta);
      ta.focus();
      ta.select();
      var ok = global.document.execCommand('copy');
      global.document.body.removeChild(ta);
      if (ok) {
        notifyToast('Copied ' + label);
        return true;
      }
    } catch (e) {}
    beaconRevealed = true;
    renderBeaconField();
    var disp = byId('secBeaconDisplay');
    if (disp) { disp.value = text; disp.focus(); disp.select(); }
    notifyToast('Long-press highlighted field to copy');
    return false;
  }

  async function copyBeaconToken() {
    if (!latestBeaconToken) await refreshSecurityCard();
    return copyTextSafe(latestBeaconToken, 'beacon token');
  }

  async function copyBeaconUrl() {
    if (!latestBeaconToken) await refreshSecurityCard();
    if (!latestBeaconToken) {
      notifyToast('Sign in first to load beacon URL');
      return false;
    }
    return copyTextSafe(buildBeaconUrl(latestBeaconToken), 'locked-phone beacon URL');
  }

  async function rotateBeaconTokenNow() {
    var btn = byId('btnRotateBeacon');
    if (!rotateConfirmArmed) {
      rotateConfirmArmed = true;
      if (btn) {
        btn.classList.add('armed-on');
        btn.textContent = 'Tap Again to Rotate';
      }
      rotateConfirmTimer = setTimeout(function () {
        rotateConfirmArmed = false;
        if (btn) {
          btn.classList.remove('armed-on');
          btn.textContent = 'Rotate Token';
        }
      }, 4000);
      return;
    }
    clearTimeout(rotateConfirmTimer);
    rotateConfirmArmed = false;
    if (btn) {
      btn.classList.remove('armed-on');
      btn.textContent = 'Rotating...';
    }
    try {
      var resp = await apiFetch('/api/auth/credentials', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ rotate_beacon_only: true })
      });
      var d = await resp.json();
      if (resp.ok && d && d.ok && d.beacon_token) {
        latestBeaconToken = d.beacon_token;
        renderBeaconField();
        notifyToast('Rotated locked-phone beacon token');
      } else {
        notifyToast((d && d.error) || 'Failed to rotate beacon token');
      }
    } catch (e) {
      notifyToast('Connection error rotating beacon token');
    } finally {
      if (btn) btn.textContent = 'Rotate Token';
    }
  }

  async function submitCredentialUpdate(ev) {
    if (ev && typeof ev.preventDefault === 'function') ev.preventDefault();
    var uInp = byId('credUsername');
    var pInp = byId('credNewPassword');
    var cInp = byId('credConfirmPassword');
    var rChk = byId('credRotateBeacon');
    var btn = byId('btnSaveCredentials');

    var newUser = uInp ? uInp.value.trim() : '';
    var newPass = pInp ? pInp.value : '';
    var confirmPass = cInp ? cInp.value : '';
    var rotBeacon = !!(rChk && rChk.checked);

    if (!newUser || newUser.length > 32 || /\s/.test(newUser)) {
      notifyToast('Username must be 1..32 non-space characters');
      return false;
    }
    if (!newPass || newPass.length < 8 || newPass.length > 128) {
      notifyToast('New password must be 8..128 characters');
      return false;
    }
    if (cInp && newPass !== confirmPass) {
      notifyToast('New password and confirmation do not match');
      return false;
    }

    if (btn) {
      btn.disabled = true;
      btn.textContent = 'Saving...';
    }
    try {
      var resp = await apiFetch('/api/auth/credentials', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          new_username: newUser,
          new_password: newPass,
          rotate_beacon: rotBeacon
        })
      });
      var d = await resp.json();
      if (!resp.ok || !d || !d.ok) {
        notifyToast((d && d.error) || 'Failed to update credentials');
        return false;
      }

      if (d.beacon_token) {
        latestBeaconToken = d.beacon_token;
        renderBeaconField();
      }
      if (pInp) pInp.value = '';
      if (cInp) cInp.value = '';
      if (rChk) rChk.checked = false;

      // Re-authenticate seamlessly with the new credentials so session continues uninterrupted
      var loginResp = await apiFetch('/api/auth/login', {
        method: 'POST',
        skipAuth: true,
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ username: newUser, password: newPass })
      });
      var loginData = await loginResp.json();
      if (loginResp.ok && loginData && loginData.session_token) {
        setSession(loginData.session_token, newUser);
        notifyToast('Credentials updated for ' + newUser);
      } else {
        clearSession();
        showLoginModal('Credentials saved — sign in with your new password.');
      }
    } catch (e) {
      notifyToast('Network error updating credentials');
    } finally {
      if (btn) {
        btn.disabled = false;
        btn.textContent = 'Update Username & Password';
      }
    }
    return false;
  }

  function initAuth() {
    syncOperatorLabels();
    renderBeaconField();
    if (hasSession()) {
      hideLoginModal();
      refreshSecurityCard();
    } else {
      if (typeof global.onArseAuthLocked === 'function') {
        global.onArseAuthLocked();
      }
      showLoginModal('');
    }
  }

  var api = {
    apiFetch: apiFetch,
    getToken: getToken,
    getUsername: getUsername,
    hasSession: hasSession,
    setSession: setSession,
    clearSession: clearSession,
    isModalOpen: isModalOpen,
    showLoginModal: showLoginModal,
    hideLoginModal: hideLoginModal,
    handleLoginSubmit: handleLoginSubmit,
    toggleLoginPasswordVisibility: toggleLoginPasswordVisibility,
    lockConeNow: lockConeNow,
    refreshSecurityCard: refreshSecurityCard,
    toggleBeaconReveal: toggleBeaconReveal,
    copyBeaconToken: copyBeaconToken,
    copyBeaconUrl: copyBeaconUrl,
    rotateBeaconTokenNow: rotateBeaconTokenNow,
    submitCredentialUpdate: submitCredentialUpdate,
    buildBeaconUrl: buildBeaconUrl,
    initAuth: initAuth
  };

  global.apiFetch = apiFetch;
  global.ArseAuth = api;
  if (typeof module !== 'undefined' && module.exports) {
    module.exports = api;
  }
})(typeof window !== 'undefined' ? window : globalThis);
