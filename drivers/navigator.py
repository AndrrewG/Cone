# ==============================================================================
# ARSE — Autonomous Road Systems Engineering
# Proprietary & Confidential — Division of Conical Mobility & Traffic Robotics
# ------------------------------------------------------------------------------
# Module:      drivers/navigator.py
# Platform:    ARSE Mk.I Autonomous Traffic Cone (Pimoroni Yukon RP2040)
# Description: Centimeter-precision (1e-7 deg integer) GNSS waypoint mission
#              planner, AI mission importer (arse-mission/v1), and closed-loop
#              holonomic path follower with dwell/audio/lighting choreography.
# Copyright (c) 2026 Autonomous Road Systems Engineering (ARSE).
# ==============================================================================

import json
import math
import os
import time


class WaypointNavigator:
    MAX_WAYPOINTS = 25
    DEG_E7_TO_METERS = 0.01113195  # 1e-7 deg latitude in meters (1.113 cm)

    VALID_SOUNDS = ("meep", "meep_meep", "chime", "horn", "siren", "chirp", "beep", "alarm")
    VALID_LIGHTS = ("off", "beacon", "strobe", "solid", "pulse", "police", "rainbow")

    def __init__(
        self,
        state_file="mission_state.json",
        arrival_radius_m=1.5,
        slowdown_radius_m=4.0,
        cruise_speed=0.55,
        min_speed=0.18,
        kp_yaw=0.02,
    ):
        self.state_file = state_file
        self.arrival_radius_m = float(arrival_radius_m)
        self.slowdown_radius_m = float(slowdown_radius_m)
        self.cruise_speed = float(cruise_speed)
        self.min_speed = float(min_speed)
        self.kp_yaw = float(kp_yaw)

        self.mission_name = "Field Mission"
        self.default_lights = None
        self.home = None             # {"lat_e7": int, "lon_e7": int}
        self.waypoints = []          # [{"lat_e7": int, "lon_e7": int, "name": str, ...}, ...]
        self.loop_mission = False
        self.drive_style = "glide"   # "glide" (hold initial heading) or "turn" (face waypoint)

        self.state = "IDLE"          # IDLE, RUNNING, PAUSED, RTH, COMPLETED
        self.pause_reason = ""
        self.current_index = 0
        self.hold_heading_deg = 0.0
        self._dwell_active = False
        self._dwell_until_ms = 0
        self._pending_light_mode = None

        # Live navigation telemetry
        self.target_distance_m = 0.0
        self.target_bearing_deg = 0.0
        self._load()

    @staticmethod
    def parse_deg_e7(val):
        if isinstance(val, int):
            # Distinguish raw degrees (e.g. 52) from pre-scaled e7 integers
            if abs(val) > 180:
                return val
            return val * 10000000
        s = str(val).strip()
        if not s:
            raise ValueError("Empty coordinate")
        neg = s.startswith("-")
        if neg or s.startswith("+"):
            s = s[1:]
        if "." in s:
            whole, frac = s.split(".", 1)
        else:
            whole, frac = s, ""
        whole_i = int(whole) if whole else 0
        frac_7 = int((frac + "0000000")[:7])
        res = whole_i * 10000000 + frac_7
        return -res if neg else res

    @classmethod
    def _sanitize_wp(cls, w, idx):
        item = {
            "lat_e7": int(w["lat_e7"]),
            "lon_e7": int(w["lon_e7"]),
            "name": str(w.get("name") or "WP{}".format(idx))[:24],
        }
        dwell = float(w.get("dwell_s", 0.0) or 0.0)
        if dwell > 0.0:
            item["dwell_s"] = round(min(30.0, dwell), 1)
        snd = w.get("sound")
        if snd and str(snd).lower() in cls.VALID_SOUNDS:
            item["sound"] = str(snd).lower()
        lgt = w.get("lights")
        if lgt and str(lgt).lower() in cls.VALID_LIGHTS:
            item["lights"] = str(lgt).lower()
        return item

    def _load(self):
        try:
            with open(self.state_file, "r") as f:
                d = json.load(f)
            if d.get("mission_name"):
                self.mission_name = str(d["mission_name"])[:36]
            dl = d.get("default_lights")
            if dl and str(dl).lower() in self.VALID_LIGHTS:
                self.default_lights = str(dl).lower()
            if isinstance(d.get("home"), dict) and "lat_e7" in d["home"]:
                self.home = {
                    "lat_e7": int(d["home"]["lat_e7"]),
                    "lon_e7": int(d["home"]["lon_e7"]),
                }
            wps = d.get("waypoints", [])
            if isinstance(wps, list):
                self.waypoints = [
                    self._sanitize_wp(w, i + 1)
                    for i, w in enumerate(wps[: self.MAX_WAYPOINTS])
                    if "lat_e7" in w and "lon_e7" in w
                ]
            self.loop_mission = bool(d.get("loop_mission", False))
            style = str(d.get("drive_style", "glide"))
            if style in ("glide", "turn"):
                self.drive_style = style
            if "arrival_radius_m" in d:
                self.arrival_radius_m = max(0.5, min(15.0, float(d["arrival_radius_m"])))
        except Exception:
            pass

    def save(self):
        payload = {
            "mission_name": self.mission_name,
            "default_lights": self.default_lights,
            "home": self.home,
            "waypoints": self.waypoints,
            "loop_mission": self.loop_mission,
            "drive_style": self.drive_style,
            "arrival_radius_m": round(self.arrival_radius_m, 2),
        }
        tmp = self.state_file + ".tmp"
        try:
            with open(tmp, "w") as f:
                json.dump(payload, f)
            try:
                os.rename(tmp, self.state_file)
            except OSError:
                try:
                    os.remove(self.state_file)
                except OSError:
                    pass
                os.rename(tmp, self.state_file)
        except Exception as e:
            print("[ARSE-NAV] Save fault:", e)

    @classmethod
    def offset_meters(cls, orig_lat_e7, orig_lon_e7, tgt_lat_e7, tgt_lon_e7):
        dlat = int(tgt_lat_e7) - int(orig_lat_e7)
        dlon = int(tgt_lon_e7) - int(orig_lon_e7)
        cos_lat = math.cos(math.radians(orig_lat_e7 / 10000000.0))
        north_m = dlat * cls.DEG_E7_TO_METERS
        east_m = dlon * cls.DEG_E7_TO_METERS * cos_lat
        return north_m, east_m

    @classmethod
    def add_meters_to_e7(cls, orig_lat_e7, orig_lon_e7, north_m, east_m):
        cos_lat = max(0.1, math.cos(math.radians(orig_lat_e7 / 10000000.0)))
        dlat_e7 = int(round(float(north_m) / cls.DEG_E7_TO_METERS))
        dlon_e7 = int(round(float(east_m) / (cls.DEG_E7_TO_METERS * cos_lat)))
        return int(orig_lat_e7) + dlat_e7, int(orig_lon_e7) + dlon_e7

    def pop_light_cmd(self):
        cmd = self._pending_light_mode
        self._pending_light_mode = None
        return cmd

    def set_home(self, lat_e7, lon_e7):
        self.home = {"lat_e7": int(lat_e7), "lon_e7": int(lon_e7)}
        self.save()
        return {"ok": True}

    def add_waypoint(self, lat_e7, lon_e7, name=None):
        if len(self.waypoints) >= self.MAX_WAYPOINTS:
            return {"ok": False, "error": "Max {} waypoints reached".format(self.MAX_WAYPOINTS)}
        idx = len(self.waypoints) + 1
        wp = {
            "lat_e7": int(lat_e7),
            "lon_e7": int(lon_e7),
            "name": (str(name)[:24] if name else "WP{}".format(idx)),
        }
        self.waypoints.append(wp)
        if self.home is None:
            self.home = {"lat_e7": int(lat_e7), "lon_e7": int(lon_e7)}
        self.save()
        return {"ok": True, "count": len(self.waypoints)}

    def add_waypoint_offset(self, ref_lat_e7, ref_lon_e7, north_m, east_m, name=None):
        origin = self.home if self.home else {"lat_e7": int(ref_lat_e7), "lon_e7": int(ref_lon_e7)}
        lat_e7, lon_e7 = self.add_meters_to_e7(origin["lat_e7"], origin["lon_e7"], north_m, east_m)
        return self.add_waypoint(lat_e7, lon_e7, name=name)

    def import_mission(self, data, gps_status=None):
        if not isinstance(data, dict):
            return {"ok": False, "error": "Mission payload must be a JSON object"}
        raw_wps = data.get("waypoints")
        if not isinstance(raw_wps, list) or len(raw_wps) == 0:
            return {"ok": False, "error": "Mission must contain a non-empty 'waypoints' array"}
        if len(raw_wps) > self.MAX_WAYPOINTS:
            return {"ok": False, "error": "Mission exceeds max {} waypoints".format(self.MAX_WAYPOINTS)}

        # Resolve optional Home / local-tangent origin
        new_home = None
        h_in = data.get("home")
        if isinstance(h_in, dict):
            if "lat_e7" in h_in and "lon_e7" in h_in:
                new_home = {"lat_e7": int(h_in["lat_e7"]), "lon_e7": int(h_in["lon_e7"])}
            elif "lat" in h_in and "lon" in h_in:
                new_home = {
                    "lat_e7": self.parse_deg_e7(h_in["lat"]),
                    "lon_e7": self.parse_deg_e7(h_in["lon"]),
                }
        elif h_in == "current_gps" or data.get("anchor_to_current_gps"):
            if gps_status and gps_status.get("has_fix"):
                new_home = {"lat_e7": int(gps_status["lat_e7"]), "lon_e7": int(gps_status["lon_e7"])}

        origin = new_home
        if origin is None and gps_status and gps_status.get("has_fix"):
            origin = {"lat_e7": int(gps_status["lat_e7"]), "lon_e7": int(gps_status["lon_e7"])}
        elif origin is None and self.home is not None:
            origin = self.home

        parsed_wps = []
        for idx, rw in enumerate(raw_wps):
            if not isinstance(rw, dict):
                return {"ok": False, "error": "Waypoint #{} must be an object".format(idx + 1)}

            if "lat_e7" in rw and "lon_e7" in rw:
                lat_e7 = int(rw["lat_e7"])
                lon_e7 = int(rw["lon_e7"])
            elif "lat" in rw and "lon" in rw:
                lat_e7 = self.parse_deg_e7(rw["lat"])
                lon_e7 = self.parse_deg_e7(rw["lon"])
            elif ("north_m" in rw or "n_m" in rw) and ("east_m" in rw or "e_m" in rw):
                if origin is None:
                    return {
                        "ok": False,
                        "error": "Relative-meter waypoints require GNSS lock or a 'home' lat/lon in JSON",
                    }
                nm = float(rw.get("north_m", rw.get("n_m", 0.0)))
                em = float(rw.get("east_m", rw.get("e_m", 0.0)))
                lat_e7, lon_e7 = self.add_meters_to_e7(origin["lat_e7"], origin["lon_e7"], nm, em)
            else:
                return {
                    "ok": False,
                    "error": "Waypoint #{} needs ('lat','lon') or ('north_m','east_m')".format(idx + 1),
                }

            if not (-900000000 <= lat_e7 <= 900000000 and -1800000000 <= lon_e7 <= 1800000000):
                return {"ok": False, "error": "Waypoint #{} coordinates out of WGS84 bounds".format(idx + 1)}

            raw_copy = dict(rw)
            raw_copy["lat_e7"] = lat_e7
            raw_copy["lon_e7"] = lon_e7
            parsed_wps.append(self._sanitize_wp(raw_copy, idx + 1))

        self.stop()
        self.waypoints = parsed_wps
        self.current_index = 0

        if new_home is not None:
            self.home = new_home
        elif origin is not None and self.home is None:
            self.home = {"lat_e7": origin["lat_e7"], "lon_e7": origin["lon_e7"]}
        elif self.home is None and parsed_wps:
            self.home = {"lat_e7": parsed_wps[0]["lat_e7"], "lon_e7": parsed_wps[0]["lon_e7"]}

        m_name = data.get("mission_name") or data.get("name")
        self.mission_name = str(m_name)[:36] if m_name else "AI Waypoint Mission"

        if "loop" in data:
            self.loop_mission = bool(data["loop"])
        elif "loop_mission" in data:
            self.loop_mission = bool(data["loop_mission"])

        style = str(data.get("drive_style", "")).lower()
        if style in ("glide", "turn"):
            self.drive_style = style

        if "arrival_radius_m" in data and data["arrival_radius_m"] is not None:
            self.arrival_radius_m = max(0.5, min(15.0, float(data["arrival_radius_m"])))

        dl = data.get("default_lights")
        if dl and str(dl).lower() in self.VALID_LIGHTS:
            self.default_lights = str(dl).lower()
        else:
            self.default_lights = None

        self.save()
        return {
            "ok": True,
            "count": len(self.waypoints),
            "mission_name": self.mission_name,
        }

    def delete_waypoint(self, index):
        idx = int(index)
        if 0 <= idx < len(self.waypoints):
            self.waypoints.pop(idx)
            if self.current_index >= len(self.waypoints):
                self.current_index = max(0, len(self.waypoints) - 1)
            self.save()
            return {"ok": True}
        return {"ok": False, "error": "Invalid waypoint index"}

    def clear_waypoints(self):
        self.stop()
        self.waypoints = []
        self.mission_name = "Field Mission"
        self.default_lights = None
        self.current_index = 0
        self.save()
        return {"ok": True}

    def configure(self, loop_mission=None, drive_style=None, arrival_radius_m=None):
        if loop_mission is not None:
            self.loop_mission = bool(loop_mission)
        if drive_style in ("glide", "turn"):
            self.drive_style = drive_style
        if arrival_radius_m is not None:
            self.arrival_radius_m = max(0.5, min(15.0, float(arrival_radius_m)))
        self.save()
        return {"ok": True}

    def start(self, current_heading_deg=0.0):
        if not self.waypoints:
            return {"ok": False, "error": "Add or upload at least one waypoint first"}
        if self.state in ("IDLE", "COMPLETED", "RTH"):
            self.current_index = 0
        self.hold_heading_deg = float(current_heading_deg)
        self._dwell_active = False
        if self.default_lights:
            self._pending_light_mode = self.default_lights
        self.state = "RUNNING"
        self.pause_reason = ""
        return {"ok": True, "state": self.state}

    def pause(self, reason="Operator paused"):
        if self.state in ("RUNNING", "RTH"):
            self.state = "PAUSED"
            self.pause_reason = reason
            self._dwell_active = False
        return {"ok": True, "state": self.state}

    def stop(self):
        self.state = "IDLE"
        self.pause_reason = ""
        self.target_distance_m = 0.0
        self._dwell_active = False
        return {"ok": True, "state": self.state}

    def return_to_home(self, current_heading_deg=0.0):
        if not self.home:
            return {"ok": False, "error": "No Home point recorded yet"}
        self.hold_heading_deg = float(current_heading_deg)
        self._dwell_active = False
        self.state = "RTH"
        self.pause_reason = ""
        return {"ok": True, "state": self.state}

    def is_active(self):
        return self.state in ("RUNNING", "RTH")

    def _advance_after_arrival(self, wp):
        custom_sfx = wp.get("sound")
        self.current_index += 1
        if self.current_index >= len(self.waypoints):
            if self.loop_mission:
                self.current_index = 0
                return (0.0, 0.0, 0.0, True, custom_sfx or "chirp")
            self.state = "COMPLETED"
            self.target_distance_m = 0.0
            return (0.0, 0.0, 0.0, False, custom_sfx or "chime")
        return (0.0, 0.0, 0.0, True, custom_sfx or "chirp")

    def update(self, gps_status, imu_status, battery_status):
        if self.state not in ("RUNNING", "RTH"):
            return (0.0, 0.0, 0.0, False, None)

        if battery_status.get("is_critical"):
            self.stop()
            self.pause_reason = "Critical battery cutoff"
            return (0.0, 0.0, 0.0, False, "alarm")

        if imu_status.get("tilt_fault"):
            self.pause("Tilt / tip-over safety")
            return (0.0, 0.0, 0.0, False, "alarm")

        if not gps_status.get("has_fix"):
            self.pause("Lost GNSS satellite lock")
            return (0.0, 0.0, 0.0, False, None)

        if not imu_status.get("online") or not imu_status.get("mag_online"):
            self.pause("Compass offline")
            return (0.0, 0.0, 0.0, False, None)

        # Handle active waypoint dwell timer
        if self._dwell_active:
            if time.ticks_diff(time.ticks_ms(), self._dwell_until_ms) < 0:
                return (0.0, 0.0, 0.0, True, None)
            self._dwell_active = False
            wp = self.waypoints[self.current_index] if self.current_index < len(self.waypoints) else {}
            # Sound was already played on initial arrival; advance silently
            self.current_index += 1
            if self.current_index >= len(self.waypoints):
                if self.loop_mission:
                    self.current_index = 0
                else:
                    self.state = "COMPLETED"
                    self.target_distance_m = 0.0
                    return (0.0, 0.0, 0.0, False, "chime")
            return (0.0, 0.0, 0.0, True, None)

        if self.state == "RTH":
            target = self.home
        else:
            if not self.waypoints or self.current_index >= len(self.waypoints):
                self.state = "COMPLETED"
                return (0.0, 0.0, 0.0, False, "chime")
            target = self.waypoints[self.current_index]

        north_m, east_m = self.offset_meters(
            gps_status["lat_e7"],
            gps_status["lon_e7"],
            target["lat_e7"],
            target["lon_e7"],
        )
        dist_m = math.sqrt(north_m * north_m + east_m * east_m)
        bearing_deg = (math.degrees(math.atan2(east_m, north_m)) + 360.0) % 360.0
        self.target_distance_m = dist_m
        self.target_bearing_deg = bearing_deg

        # Check waypoint arrival
        if dist_m <= self.arrival_radius_m:
            if self.state == "RTH":
                self.state = "COMPLETED"
                self.target_distance_m = 0.0
                return (0.0, 0.0, 0.0, False, "chime")

            if target.get("lights"):
                self._pending_light_mode = target["lights"]

            dwell_s = float(target.get("dwell_s", 0.0) or 0.0)
            if dwell_s > 0.0:
                self._dwell_active = True
                self._dwell_until_ms = time.ticks_add(time.ticks_ms(), int(dwell_s * 1000.0))
                return (0.0, 0.0, 0.0, True, target.get("sound") or "chirp")

            return self._advance_after_arrival(target)

        # Proportional speed taper inside slowdown radius
        if dist_m >= self.slowdown_radius_m:
            spd = self.cruise_speed
        else:
            frac = (dist_m - self.arrival_radius_m) / max(
                0.2, self.slowdown_radius_m - self.arrival_radius_m
            )
            spd = self.min_speed + max(0.0, min(1.0, frac)) * (self.cruise_speed - self.min_speed)

        heading_deg = imu_status["heading_deg"]
        rel_cw_rad = math.radians(bearing_deg - heading_deg)

        if self.drive_style == "turn":
            yaw_err_cw = (bearing_deg - heading_deg + 180.0) % 360.0 - 180.0
            omega = max(-0.5, min(0.5, -self.kp_yaw * yaw_err_cw))
            align_scale = max(0.0, math.cos(rel_cw_rad))
            vx = spd * align_scale
            vy = 0.0
        else:
            vx = spd * math.cos(rel_cw_rad)
            vy = -spd * math.sin(rel_cw_rad)
            yaw_err_cw = (self.hold_heading_deg - heading_deg + 180.0) % 360.0 - 180.0
            omega = max(-0.35, min(0.35, -self.kp_yaw * yaw_err_cw))

        return (vx, vy, omega, True, None)

    def get_status(self, gps_status=None):
        origin = self.home
        if origin is None and gps_status and gps_status.get("has_fix"):
            origin = {"lat_e7": gps_status["lat_e7"], "lon_e7": gps_status["lon_e7"]}
        elif origin is None and self.waypoints:
            origin = self.waypoints[0]

        cone_ne = None
        if origin and gps_status and gps_status.get("has_fix"):
            n, e = self.offset_meters(
                origin["lat_e7"], origin["lon_e7"], gps_status["lat_e7"], gps_status["lon_e7"]
            )
            cone_ne = [round(n, 2), round(e, 2)]

        wp_list = []
        for w in self.waypoints:
            n, e = (0.0, 0.0)
            if origin:
                n, e = self.offset_meters(origin["lat_e7"], origin["lon_e7"], w["lat_e7"], w["lon_e7"])
            entry = {
                "name": w["name"],
                "lat_e7": w["lat_e7"],
                "lon_e7": w["lon_e7"],
                "lat": round(w["lat_e7"] / 10000000.0, 6),
                "lon": round(w["lon_e7"] / 10000000.0, 6),
                "n_m": round(n, 2),
                "e_m": round(e, 2),
            }
            if "dwell_s" in w:
                entry["dwell_s"] = w["dwell_s"]
            if "sound" in w:
                entry["sound"] = w["sound"]
            if "lights" in w:
                entry["lights"] = w["lights"]
            wp_list.append(entry)

        return {
            "mission_name": self.mission_name,
            "default_lights": self.default_lights,
            "state": "DWELL" if self._dwell_active else self.state,
            "pause_reason": self.pause_reason,
            "current_index": self.current_index,
            "total_waypoints": len(self.waypoints),
            "distance_m": round(self.target_distance_m, 2),
            "bearing_deg": round(self.target_bearing_deg, 1),
            "arrival_radius_m": round(self.arrival_radius_m, 1),
            "loop_mission": self.loop_mission,
            "drive_style": self.drive_style,
            "has_home": (self.home is not None),
            "home": self.home,
            "cone_ne": cone_ne,
            "waypoints": wp_list,
        }
