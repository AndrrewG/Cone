# ==============================================================================
# ARSE — Autonomous Road Systems Engineering
# Proprietary & Confidential — Division of Conical Mobility & Traffic Robotics
# ------------------------------------------------------------------------------
# Module:      drivers/wifi_manager.py
# Platform:    ARSE Mk.I Autonomous Traffic Cone (Pimoroni Yukon RP2040)
# Description: Wireless RF link manager for RM2 (CYW43439). Associates with
#              authorized station networks and provides 'TrafficCone' AP failover.
# Copyright (c) 2026 Autonomous Road Systems Engineering (ARSE).
# ==============================================================================
import json
import network
import time


class ConeWiFi:
    def __init__(self, ap_ssid="TrafficCone", ap_password="", connect_timeout_ms=9000):
        self.ap_ssid = ap_ssid
        self.ap_password = ap_password
        self.connect_timeout_ms = connect_timeout_ms

        self.mode = "OFF"
        self.ssid = ""
        self.ip = "0.0.0.0"
        self.mac = ""
        self._sta = None
        self._ap = None

    def _load_configured_networks(self):
        networks = []
        seen = set()

        # 1. Check secrets.py (WIFI_NETWORKS list)
        try:
            import secrets
            if hasattr(secrets, "WIFI_NETWORKS"):
                for item in secrets.WIFI_NETWORKS:
                    ssid = item.get("ssid", "")
                    pwd = item.get("password", "")
                    if ssid and ssid not in seen:
                        networks.append((ssid, pwd))
                        seen.add(ssid)
        except Exception:
            pass

        # 2. Check secrets.json
        try:
            with open("secrets.json", "r") as f:
                data = json.load(f)
            ssid = data.get("ssid", "")
            pwd = data.get("password", "")
            if ssid and ssid not in seen:
                networks.append((ssid, pwd))
                seen.add(ssid)
        except Exception:
            pass

        return networks

    def connect_or_start_ap(self):
        candidates = self._load_configured_networks()

        if candidates:
            self._sta = network.WLAN(network.STA_IF)
            self._sta.active(True)
            try:
                self.mac = ":".join("{:02X}".format(b) for b in self._sta.config("mac"))
            except Exception:
                pass

            # Scan once to see which configured networks are actually in range
            visible = {}
            try:
                for entry in self._sta.scan():
                    s = entry[0].decode("utf-8", "ignore")
                    rssi = entry[3]
                    if s and (s not in visible or rssi > visible[s]):
                        visible[s] = rssi
            except Exception:
                pass

            for ssid, pwd in candidates:
                if visible and ssid not in visible:
                    continue
                print("[Wi-Fi] Connecting to '{}'...".format(ssid))
                try:
                    self._sta.connect(ssid, pwd)
                except Exception as e:
                    print("[Wi-Fi] Connect error:", e)
                    continue

                t0 = time.ticks_ms()
                while not self._sta.isconnected():
                    if time.ticks_diff(time.ticks_ms(), t0) > self.connect_timeout_ms:
                        break
                    time.sleep_ms(150)

                if self._sta.isconnected():
                    self.mode = "STA"
                    self.ssid = ssid
                    self.ip = self._sta.ifconfig()[0]
                    print("[Wi-Fi] Connected! SSID='{}' | URL: http://{}/".format(self.ssid, self.ip))
                    return self.get_status()

            # Clean up failed STA before starting AP
            try:
                self._sta.disconnect()
                self._sta.active(False)
            except Exception:
                pass

        return self.start_ap()

    def start_ap(self):
        """Starts standalone TrafficCone Wi-Fi Access Point (192.168.4.1)."""
        self._ap = network.WLAN(network.AP_IF)
        if self.ap_password and len(self.ap_password) >= 8:
            self._ap.config(essid=self.ap_ssid, password=self.ap_password)
        else:
            self._ap.config(essid=self.ap_ssid, security=0)
        self._ap.active(True)
        time.sleep_ms(400)

        self.mode = "AP"
        self.ssid = self.ap_ssid
        self.ip = self._ap.ifconfig()[0]
        try:
            self.mac = ":".join("{:02X}".format(b) for b in self._ap.config("mac"))
        except Exception:
            pass

        print("[Wi-Fi] Access Point active! SSID='{}' | URL: http://{}/".format(self.ssid, self.ip))
        return self.get_status()

    def set_power_save(self, enabled):
        # Toggle CYW43439 beacon-synchronised PM during Sentry low-power watch.
        # Avoid PM_POWERSAVE (0x00111022) as CYW43 aggressive sleep drops inbound HTTP SYNs.
        if self.mode == "STA" and self._sta:
            try:
                pm_on = getattr(network.WLAN, "PM_PERFORMANCE", 0xA11142)
                pm_off = getattr(network.WLAN, "PM_NONE", 0xA11140)
                self._sta.config(pm=pm_on if enabled else pm_off)
            except Exception:
                pass

    def get_rssi(self):
        if self.mode == "STA" and self._sta and self._sta.isconnected():
            try:
                return self._sta.status("rssi")
            except Exception:
                return None
        return None

    def get_status(self):
        if self.mode == "STA" and self._sta and self._sta.isconnected():
            self.ip = self._sta.ifconfig()[0]
        return {
            "mode": self.mode,
            "ssid": self.ssid,
            "ip": self.ip,
            "rssi": self.get_rssi(),
            "mac": self.mac,
        }
