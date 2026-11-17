# ==============================================================================
# ARSE — Autonomous Road Systems Engineering
# Module:      telemetry_server.py
# Platform:    ARSE Mk.I Autonomous Traffic Cone (Pimoroni Yukon RP2040 / RP2350)
# Description: Async HTTP/1.1 & REST control/telemetry server with Bearer auth.
# ==============================================================================

import gc
import os

try:
    import uasyncio as asyncio
except ImportError:
    import asyncio

try:
    import ujson as json
except ImportError:
    import json

try:
    import ubinascii as _binascii
except ImportError:
    import binascii as _binascii

from security.auth_manager import AuthRateLimitError

_QUERY_TOKEN_ROUTES = ("/api/follow/beacon", "/api/follow/stamp", "/ws/follow")
_BEACON_POST_ROUTES = ("/api/follow/beacon", "/api/follow/stamp")


def _stat_mode(path):
    try:
        return os.stat(path)[0] & 0xF000
    except OSError:
        return -1


def _extract_client_ip(writer):
    try:
        if hasattr(writer, "get_extra_info"):
            peer = writer.get_extra_info("peername")
            if isinstance(peer, (tuple, list)) and peer and peer[0]:
                return str(peer[0])
            if isinstance(peer, str) and peer.strip():
                return peer.strip()
    except Exception:
        pass
    return "0.0.0.0"


def _extract_query_token(query_str):
    if not query_str:
        return None
    for pair in query_str[:512].split("&"):
        if "=" not in pair:
            continue
        k, v = pair.split("=", 1)
        if k == "token":
            v = v.replace("+", " ")
            if "%" in v:
                out = bytearray()
                i = 0
                n = len(v)
                while i < n:
                    if v[i] == "%" and i + 2 < n:
                        try:
                            out.append(int(v[i + 1 : i + 3], 16))
                            i += 3
                            continue
                        except ValueError:
                            pass
                    out.extend(v[i].encode("utf-8"))
                    i += 1
                v = out.decode("utf-8", "ignore")
            v = v.strip()
            return v[:256] if v else ""
    return None


def _normalise_ota_rel_path(raw_path):
    if not isinstance(raw_path, str) or not raw_path or raw_path[0] in ("/", "\\"):
        return None
    rel = raw_path.replace("\\", "/")
    if ":" in rel or "\x00" in rel:
        return None
    for ch in rel:
        if ord(ch) < 0x20:
            return None
    for seg in rel.split("/"):
        if not seg or seg == ".." or seg.startswith(".") or seg.lower() in ("secrets.json", "secrets.py"):
            return None
    return rel


def _ensure_parent_dirs(rel_path):
    idx = rel_path.rfind("/")
    if idx <= 0:
        return
    parent = rel_path[:idx]
    if hasattr(os, "makedirs"):
        os.makedirs(parent, exist_ok=True)
        return
    cur = ""
    for seg in parent.split("/"):
        if seg:
            cur = seg if not cur else (cur + "/" + seg)
            if _stat_mode(cur) != 0x4000:
                try:
                    os.mkdir(cur)
                except OSError:
                    pass


class TelemetryServer:
    MAX_HEADERS = 48
    MAX_LOGIN_BODY_BYTES = 512
    MAX_BODY_BYTES = 6144

    def __init__(self, app, html_path="web/index.html", port=80, auth_manager=None):
        self.app = app
        self.html_path = html_path
        self.port = port
        self.auth = auth_manager
        self._server = None
        self._req_count = 0
        self._html_buf = bytearray(512)
        self._html_mv = memoryview(self._html_buf)
        self._html_busy = False

    def _get_auth(self):
        return self.auth if self.auth is not None else getattr(self.app, "auth", None)

    def _sibling_path(self, filename):
        norm = self.html_path.replace("\\", "/")
        idx = norm.rfind("/")
        if idx < 0:
            return filename
        return ("/" + filename) if idx == 0 else (norm[:idx] + "/" + filename)

    async def start(self):
        self._server = await asyncio.start_server(self._handle_client, "0.0.0.0", self.port, backlog=8)
        print("[ARSE-WEB] Operator terminal & API listening on port {}".format(self.port))
        return self._server

    async def _send_json(self, writer, status_code, status_text, payload, extra_headers=None):
        gc.collect()
        body = json.dumps(payload).encode("utf-8")
        payload = None
        extra_str = ""
        if extra_headers:
            items = extra_headers.items() if isinstance(extra_headers, dict) else extra_headers
            extra_str = "".join(
                "{}: {}\r\n".format(str(k).replace("\r", "").replace("\n", ""), str(v).replace("\r", "").replace("\n", ""))
                for k, v in items
            )
        hdr = (
            "HTTP/1.1 {} {}\r\n"
            "Content-Type: application/json; charset=utf-8\r\n"
            "Content-Length: {}\r\n"
            "Cache-Control: no-store, no-cache\r\n"
            "{}"
            "Connection: close\r\n\r\n"
        ).format(status_code, status_text, len(body), extra_str)
        writer.write(hdr.encode("utf-8"))
        writer.write(body)
        del body
        await writer.drain()

    async def _send_static_file(self, writer, file_path, content_type):
        use_shared = not self._html_busy
        if use_shared:
            self._html_busy = True
            buf, mv = self._html_buf, self._html_mv
        else:
            gc.collect()
            buf = bytearray(512)
            mv = memoryview(buf)
        headers_sent = False
        try:
            gc.collect()
            size = os.stat(file_path)[6]
            with open(file_path, "rb") as f:
                hdr = (
                    "HTTP/1.1 200 OK\r\nContent-Type: {}\r\nContent-Length: {}\r\n"
                    "Cache-Control: no-cache\r\nConnection: close\r\n\r\n"
                ).format(content_type, size)
                writer.write(hdr.encode("utf-8"))
                del hdr
                headers_sent = True
                await writer.drain()
                while True:
                    n = f.readinto(buf)
                    if not n:
                        break
                    writer.write(mv if n == 512 else mv[:n])
                    await writer.drain()
        except Exception as e:
            print("[ARSE-WEB] Static load error:", e)
            if not headers_sent:
                writer.write(b"HTTP/1.1 500 Internal Server Error\r\nConnection: close\r\n\r\n")
                writer.write("Error loading UI: {}".format(e).encode("utf-8"))
                await writer.drain()
        finally:
            if use_shared:
                self._html_busy = False
            gc.collect()

    async def _send_html_file(self, writer):
        await self._send_static_file(writer, self.html_path, "text/html; charset=utf-8")

    async def _read_json_dict(self, reader, content_len, require_non_empty=False):
        if content_len == 0:
            return None if require_non_empty else {}
        if content_len > 256:
            gc.collect()
        body_buf = bytearray(content_len)
        mv = memoryview(body_buf)
        got = 0
        while got < content_len:
            part = await asyncio.wait_for(reader.read(content_len - got), 5)
            if not part:
                break
            plen = len(part)
            mv[got : got + plen] = part
            got += plen
            del part
        del mv
        if got < content_len:
            return None
        try:
            gc.collect()
            try:
                data = json.loads(body_buf)
            except TypeError:
                data = json.loads(bytes(body_buf).decode("utf-8"))
        except Exception:
            data = None
        finally:
            body_buf = None
        return data if isinstance(data, dict) else None

    async def _handle_post(self, path, data):
        app = self.app

        if path == "/api/drive/bench":
            spd = data.get("speed", 0.0)
            if app.sentry and app.sentry.is_enabled():
                if abs(spd) > 0.01:
                    return {"ok": False, "error": "Disarm Sentry Mode first"}
                return {"ok": True, "armed": False, "w": [0.0, 0.0, 0.0]}
            if app.drive.set_bench_motor(data.get("channel", 1), spd, data.get("duration_ms", 0)):
                if app.nav.is_active():
                    app.nav.pause("Bench motor override")
                if getattr(app, "patterns", None) and app.patterns.is_active():
                    app.patterns.stop("Bench motor override")
                    app.drive.set_routine_wheels(0.0, 0.0, 0.0, active=False)
            return {"ok": True, "armed": app.drive.armed, "w": [round(x, 2) for x in app.drive.target_wheels]}

        if path == "/api/drive/cmd":
            vx, vy, om = data.get("vx", 0.0), data.get("vy", 0.0), data.get("omega", 0.0)
            if app.sentry and app.sentry.is_enabled():
                if abs(vx) > 0.01 or abs(vy) > 0.01 or abs(om) > 0.01:
                    return {"ok": False, "error": "Disarm Sentry Mode first"}
                return {"ok": True, "w": [0.0, 0.0, 0.0]}
            if app.drive.set_manual_twist(vx, vy, om):
                if app.nav.is_active():
                    app.nav.pause("Manual joystick override")
                if getattr(app, "patterns", None) and app.patterns.is_active():
                    app.patterns.stop("Manual joystick override")
                    app.drive.set_routine_wheels(0.0, 0.0, 0.0, active=False)
            return {"ok": True, "w": [round(x, 2) for x in app.drive.target_wheels]}

        if path == "/api/drive/estop":
            if app.sentry and app.sentry.is_enabled():
                app.sentry.disarm()
            if getattr(app, "patterns", None):
                app.patterns.stop("E-STOP")
            app.nav.stop()
            app.audio.stop()
            return {"ok": True, "drive": app.drive.estop()}

        if path == "/api/drive/config":
            if "armed" in data:
                if data["armed"] and app.sentry and app.sentry.is_enabled():
                    return {"ok": False, "error": "Disarm Sentry Mode before arming motors"}
                if data["armed"] and app.imu_state.get("tilt_fault"):
                    return {"ok": False, "error": "Cannot arm while cone is tilted!"}
                if (not data["armed"]) and getattr(app, "patterns", None) and app.patterns.is_active():
                    app.patterns.stop("Motors disarmed")
                app.drive.set_armed(data["armed"])
            if "speed_mode" in data:
                app.drive.set_speed_mode(data["speed_mode"])
            return {"ok": True, "drive": app.drive.get_status()}

        if path == "/api/lights":
            if "mode" in data:
                st = app.lights.set_mode(data["mode"], data.get("brightness"))
            elif "brightness" in data:
                st = app.lights.set_brightness(data["brightness"])
            else:
                st = app.lights.get_status()
            return {"ok": True, "lights": st}

        if path == "/api/audio":
            if "volume" in data and data["volume"] is not None:
                app.audio.set_volume(data["volume"])
            snd = data.get("sound")
            st = app.audio.stop() if snd == "stop" else (app.audio.play(snd) if snd else app.audio.get_status())
            return {"ok": True, "audio": st}

        if path == "/api/battery/new_full":
            return {"ok": True, "battery": app.battery.mark_new_full_battery()}

        if path == "/api/battery/config":
            st = None
            if "capacity_mah" in data and data["capacity_mah"] is not None:
                st = app.battery.set_capacity_mah(data["capacity_mah"])
            if "soc_percent" in data and data["soc_percent"] is not None:
                st = app.battery.set_current_soc(data["soc_percent"])
            return {"ok": True, "battery": st or app.battery.get_status()}

        if path == "/api/battery/dismiss_hint":
            app.battery.dismiss_fresh_hint()
            return {"ok": True}

        if path == "/api/imu/cal":
            if not app.imu or not app.imu.mag_online:
                return {"ok": False, "error": "Magnetometer not detected"}
            act = data.get("action", "")
            if act == "start":
                return app.imu.start_calibration()
            if act == "save":
                return app.imu.save_calibration()
            if act == "cancel":
                return app.imu.cancel_calibration()
            return {"ok": False, "error": "Unknown calibration action"}

        if path == "/api/nav/action":
            act = data.get("action", "")
            gps_st = app.gps.get_status() if app.gps else {"has_fix": False}
            hdg = app.imu_state.get("heading_deg", 0.0)
            if act == "drop_here":
                if not gps_st.get("has_fix"):
                    return {"ok": False, "error": "Waiting for GNSS satellite lock"}
                res = app.nav.add_waypoint(gps_st["lat_e7"], gps_st["lon_e7"], data.get("name"))
            elif act == "add_offset":
                if not gps_st.get("has_fix") and not app.nav.home:
                    return {"ok": False, "error": "Need GNSS lock or Home point first"}
                res = app.nav.add_waypoint_offset(
                    gps_st.get("lat_e7", 0), gps_st.get("lon_e7", 0), data.get("north_m", 0.0), data.get("east_m", 0.0), data.get("name")
                )
            elif act == "add_latlon":
                lat_e7 = int(data["lat_e7"]) if "lat_e7" in data else app.nav.parse_deg_e7(data["lat"])
                lon_e7 = int(data["lon_e7"]) if "lon_e7" in data else app.nav.parse_deg_e7(data["lon"])
                res = app.nav.add_waypoint(lat_e7, lon_e7, data.get("name"))
            elif act == "set_home":
                if not gps_st.get("has_fix"):
                    return {"ok": False, "error": "Waiting for GNSS satellite lock"}
                res = app.nav.set_home(gps_st["lat_e7"], gps_st["lon_e7"])
            elif act == "delete":
                res = app.nav.delete_waypoint(data.get("index", -1))
            elif act == "clear":
                res = app.nav.clear_waypoints()
            elif act == "start":
                if app.sentry and app.sentry.is_enabled():
                    return {"ok": False, "error": "Disarm Sentry Mode before starting mission"}
                if not gps_st.get("has_fix"):
                    return {"ok": False, "error": "Cannot start mission without GNSS lock"}
                if getattr(app, "patterns", None) and app.patterns.is_active():
                    app.patterns.stop("GNSS mission started")
                    app.drive.set_routine_wheels(0.0, 0.0, 0.0, active=False)
                res = app.nav.start(current_heading_deg=hdg)
                led_cmd = app.nav.pop_light_cmd()
                if led_cmd and app.yukon.is_main_output_enabled():
                    try:
                        app.lights.set_mode(led_cmd)
                    except Exception:
                        pass
            elif act == "pause":
                res = app.nav.pause("Operator paused")
            elif act == "stop":
                res = app.nav.stop()
            elif act == "rth":
                if app.sentry and app.sentry.is_enabled():
                    return {"ok": False, "error": "Disarm Sentry Mode before starting RTH"}
                if not gps_st.get("has_fix"):
                    return {"ok": False, "error": "Cannot RTH without GNSS lock"}
                if getattr(app, "patterns", None) and app.patterns.is_active():
                    app.patterns.stop("RTH started")
                    app.drive.set_routine_wheels(0.0, 0.0, 0.0, active=False)
                res = app.nav.return_to_home(current_heading_deg=hdg)
            elif act == "config":
                res = app.nav.configure(
                    loop_mission=data.get("loop_mission"),
                    drive_style=data.get("drive_style"),
                    arrival_radius_m=data.get("arrival_radius_m"),
                )
            elif act == "upload":
                mission_obj = data.get("mission") if isinstance(data.get("mission"), dict) else data
                res = app.nav.import_mission(mission_obj, gps_status=gps_st)
            else:
                return {"ok": False, "error": "Unknown nav action"}
            res["nav"] = app.nav.get_status(gps_st)
            return res

        if path == "/api/nav/upload":
            gc.collect()
            gps_st = app.gps.get_status() if app.gps else {"has_fix": False}
            mission_obj = data.get("mission") if isinstance(data.get("mission"), dict) else data
            res = app.nav.import_mission(mission_obj, gps_status=gps_st)
            res["nav"] = app.nav.get_status(gps_st)
            return res

        if path == "/api/ota/chunk":
            rel = _normalise_ota_rel_path(data.get("path", ""))
            if rel is None:
                return {"ok": False, "error": "Invalid OTA path"}
            mode = "wb" if data.get("mode") == "wb" else "ab"
            b64 = data.pop("b64", "")
            if not isinstance(b64, str):
                return {"ok": False, "error": "Invalid base64 payload"}
            gc.collect()
            raw = _binascii.a2b_base64(b64)
            del b64
            _ensure_parent_dirs(rel)
            if mode == "wb":
                alt = (rel[:-4] + ".py") if rel.endswith(".mpy") else ((rel[:-3] + ".mpy") if rel.endswith(".py") else None)
                if alt:
                    try:
                        os.remove(alt)
                    except OSError:
                        pass
            with open(rel, mode) as f:
                f.write(raw)
            del raw
            gc.collect()
            return {"ok": True}

        if path == "/api/ota/reboot":
            asyncio.create_task(self._deferred_reboot())
            return {"ok": True}

        if path == "/api/routines/start":
            patterns = getattr(app, "patterns", None)
            if patterns is None:
                return {"ok": False, "error": "Pattern runner unavailable", "_status": (503, "Service Unavailable")}
            if not getattr(app, "drive", None) or not app.drive.armed:
                return {
                    "ok": False,
                    "error": "Motors must be explicitly armed before starting a routine",
                    "_status": (409, "Conflict"),
                }
            if getattr(app, "sentry", None) and app.sentry.is_enabled():
                return {"ok": False, "error": "Disarm Sentry Mode before starting a routine", "_status": (409, "Conflict")}
            imu_st = getattr(app, "imu_state", None)
            if isinstance(imu_st, dict):
                if imu_st.get("tilt_fault"):
                    return {"ok": False, "error": "Cannot start routine while cone is tilted", "_status": (409, "Conflict")}
                if not imu_st.get("online", True):
                    return {"ok": False, "error": "9DoF IMU offline — cannot run dead-reckoning routine", "_status": (409, "Conflict")}
            if getattr(app, "battery", None) and app.battery.get_status().get("is_critical"):
                return {"ok": False, "error": "Critical battery cutoff active", "_status": (409, "Conflict")}
            if getattr(app, "nav", None) and app.nav.is_active():
                app.nav.pause("Routine started")
                app.drive.set_auto_twist(0.0, 0.0, 0.0, active=False)
            params = data.get("params") if isinstance(data.get("params"), dict) else data
            st = patterns.start(
                data.get("routine", ""),
                preset=data.get("preset", "indoor"),
                params=params,
            )
            return {"ok": True, "routines": st}

        if path == "/api/routines/stop":
            patterns = getattr(app, "patterns", None)
            if patterns is None:
                return {"ok": False, "error": "Pattern runner unavailable", "_status": (503, "Service Unavailable")}
            st = patterns.stop("Operator stopped")
            if getattr(app, "drive", None) is not None and hasattr(app.drive, "set_routine_wheels"):
                app.drive.set_routine_wheels(0.0, 0.0, 0.0, active=False)
            return {"ok": True, "routines": st}

        if path == "/api/sentry":
            if not app.sentry:
                return {"ok": False, "error": "Sentry controller unavailable"}
            if "sensitivity" in data and data["sensitivity"] is not None:
                app.sentry.set_sensitivity(data["sensitivity"])
            act = data.get("action")
            if act == "arm":
                return app.sentry.arm()
            if act == "disarm":
                return app.sentry.disarm()
            return {"ok": True, "sentry": app.sentry.get_status()}

        if path == "/api/auth/logout":
            auth = self._get_auth()
            if auth is None:
                return {"ok": False, "error": "Auth manager unavailable", "_status": (503, "Service Unavailable")}
            cleared = auth.logout_all()
            mission_active = bool(
                (getattr(app, "nav", None) and app.nav.is_active())
                or (getattr(app, "patterns", None) and app.patterns.is_active())
            )
            motors_disarmed = False
            if not mission_active and getattr(app, "drive", None) is not None:
                app.drive.set_armed(False)
                motors_disarmed = True
            return {
                "ok": True,
                "logged_out": True,
                "sessions_cleared": cleared,
                "motors_disarmed": motors_disarmed,
                "mission_active": mission_active,
            }

        if path == "/api/auth/credentials":
            auth = self._get_auth()
            if auth is None:
                return {"ok": False, "error": "Auth manager unavailable", "_status": (503, "Service Unavailable")}
            if data.get("rotate_beacon_only") is True:
                return auth.rotate_beacon_token()
            new_user = data["new_username"] if ("new_username" in data and data["new_username"] is not None) else data.get("username", "")
            new_pw = data["new_password"] if ("new_password" in data and data["new_password"] is not None) else data.get("password", "")
            try:
                res = auth.update_credentials(new_user, new_pw, rotate_beacon=bool(data.get("rotate_beacon", False)))
            except ValueError as e:
                return {"ok": False, "error": str(e)}
            return {
                "ok": True,
                "username": res["username"],
                "beacon_rotated": res["beacon_rotated"],
                "beacon_token": auth.get_beacon_token(),
            }

        if path in _BEACON_POST_ROUTES:
            follow = getattr(app, "follow", None)
            if follow is not None:
                m_name = "handle_beacon" if path.endswith("/beacon") else "handle_stamp"
                if hasattr(follow, m_name):
                    return getattr(follow, m_name)(data)
            return None

        return None

    async def _deferred_reboot(self):
        if hasattr(asyncio, "sleep_ms"):
            await asyncio.sleep_ms(350)
        else:
            await asyncio.sleep(0.35)
        if self.app.sentry and self.app.sentry.is_enabled():
            self.app.sentry.disarm()
        if self.app.battery:
            self.app.battery.save()
        if self.app.drive:
            self.app.drive.estop()
        import machine
        machine.reset()

    async def _handle_client(self, reader, writer):
        try:
            req_line = await asyncio.wait_for(reader.readline(), 5)
            if not req_line:
                return
            parts = req_line.decode("utf-8", "ignore").strip().split(" ")
            if len(parts) < 2:
                return
            method = parts[0].upper()
            raw_target = parts[1]
            path, query_str = raw_target.split("?", 1) if "?" in raw_target else (raw_target, "")
            client_ip = _extract_client_ip(writer)

            content_len = 0
            seen_cl = False
            auth_header = None
            h_count = 0
            bad_hdr = False
            while True:
                hline = await asyncio.wait_for(reader.readline(), 5)
                if not hline or hline in (b"\r\n", b"\n"):
                    break
                h_count += 1
                if h_count > self.MAX_HEADERS:
                    bad_hdr = True
                    break
                if len(hline) >= 15 and hline[:15].lower() == b"content-length:":
                    if seen_cl:
                        content_len = -1
                    else:
                        seen_cl = True
                        try:
                            content_len = int(hline[15:].strip())
                        except ValueError:
                            content_len = -1
                elif len(hline) >= 14 and hline[:14].lower() == b"authorization:":
                    raw_auth = hline[14:].strip()[:256]
                    auth_header = raw_auth.decode("utf-8", "ignore").strip() if auth_header is None else ""
            hline = None

            if bad_hdr or content_len < 0:
                await self._send_json(writer, 400, "Bad Request", {"ok": False, "error": "Malformed HTTP headers"})
                return

            auth = self._get_auth()

            if method == "GET":
                if path in ("/", "/index.html"):
                    await self._send_html_file(writer)
                    return
                if path in ("/auth.js", "/web/auth.js"):
                    js_path = self._sibling_path("auth.js")
                    if _stat_mode(js_path) not in (-1, 0x4000):
                        await self._send_static_file(writer, js_path, "application/javascript; charset=utf-8")
                    else:
                        await self._send_json(writer, 404, "Not Found", {"ok": False, "error": "Not found"})
                    return
                if path in ("/favicon.ico", "/generate_204"):
                    writer.write(b"HTTP/1.1 204 No Content\r\nConnection: close\r\n\r\n")
                    await writer.drain()
                    return
                if path == "/api/health":
                    locked = bool(auth.get_public_status()["locked"]) if auth else False
                    await self._send_json(writer, 200, "OK", {"online": True, "locked": locked})
                    return

            if method == "POST" and path == "/api/auth/login":
                if content_len > self.MAX_LOGIN_BODY_BYTES:
                    await self._send_json(writer, 413, "Payload Too Large", {"ok": False, "error": "Payload too large"})
                    return
                data = await self._read_json_dict(reader, content_len, require_non_empty=True)
                if data is None:
                    await self._send_json(writer, 400, "Bad Request", {"ok": False, "error": "Invalid JSON payload"})
                    return
                if auth is None:
                    await self._send_json(writer, 503, "Service Unavailable", {"ok": False, "error": "Auth manager unavailable"})
                    return
                try:
                    tok = auth.login(data.get("username", ""), data.get("password", ""), client_ip=client_ip)
                except AuthRateLimitError as exc:
                    await self._send_json(
                        writer,
                        429,
                        "Too Many Requests",
                        {"ok": False, "authenticated": False, "error": str(exc), "retry_after_s": exc.retry_after_s},
                        extra_headers=(("Retry-After", str(exc.retry_after_s)),),
                    )
                    return
                finally:
                    data = None
                if tok is None:
                    await self._send_json(
                        writer,
                        401,
                        "Unauthorized",
                        {"ok": False, "authenticated": False, "error": "Invalid username or password"},
                        extra_headers=(("WWW-Authenticate", "Bearer"),),
                    )
                    return
                await self._send_json(
                    writer,
                    200,
                    "OK",
                    {"ok": True, "authenticated": True, "session_token": tok, "expires_in_s": auth.SESSION_TTL_MS // 1000, "username": auth.username},
                )
                return

            if auth is not None:
                cand = None
                if auth_header is not None:
                    if len(auth_header) >= 7 and auth_header[:7].lower() == "bearer ":
                        cand = auth_header
                elif query_str and path in _QUERY_TOKEN_ROUTES:
                    cand = _extract_query_token(query_str)
                scope = auth.validate_bearer(cand)
                if scope is None:
                    await self._send_json(
                        writer, 401, "Unauthorized", {"ok": False, "error": "Authentication required"}, extra_headers=(("WWW-Authenticate", "Bearer"),)
                    )
                    return
                if scope == "beacon_only" and not (method == "POST" and path in _BEACON_POST_ROUTES):
                    await self._send_json(writer, 403, "Forbidden", {"ok": False, "error": "Forbidden: beacon_only scope"})
                    return

            if content_len > self.MAX_BODY_BYTES:
                await self._send_json(writer, 413, "Payload Too Large", {"ok": False, "error": "Payload too large"})
                return

            if method == "GET" and path == "/api/telemetry":
                gc.collect()
                await self._send_json(writer, 200, "OK", self.app.get_telemetry_snapshot())
            elif method == "GET" and path == "/api/auth/beacon":
                if auth is None:
                    await self._send_json(writer, 503, "Service Unavailable", {"ok": False, "error": "Auth manager unavailable"})
                else:
                    await self._send_json(writer, 200, "OK", {"ok": True, "username": auth.username, "beacon_token": auth.get_beacon_token()})
            elif method == "POST":
                data = await self._read_json_dict(reader, content_len, require_non_empty=False)
                if data is None:
                    await self._send_json(writer, 400, "Bad Request", {"ok": False, "error": "Invalid JSON payload"})
                    return
                try:
                    gc.collect()
                    res = await self._handle_post(path, data)
                    del data
                    if res is None:
                        await self._send_json(writer, 404, "Not Found", {"ok": False, "error": "Not found"})
                    elif isinstance(res, dict) and "_status" in res:
                        sc, st = res.pop("_status")
                        await self._send_json(writer, sc, st, res)
                    elif res.get("ok") is False:
                        await self._send_json(writer, 400, "Bad Request", res)
                    else:
                        await self._send_json(writer, 200, "OK", res)
                except Exception as e:
                    await self._send_json(writer, 400, "Bad Request", {"ok": False, "error": str(e)})
            else:
                await self._send_json(writer, 404, "Not Found", {"ok": False, "error": "Not found"})
        except Exception as e:
            print("[ARSE-WEB] Client error:", e)
        finally:
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass
            self._req_count += 1
            if (self._req_count & 7) == 0:
                gc.collect()
