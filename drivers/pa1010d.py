# ==============================================================================
# ARSE — Autonomous Road Systems Engineering
# Proprietary & Confidential — Division of Conical Mobility & Traffic Robotics
# ------------------------------------------------------------------------------
# Module:      drivers/pa1010d.py
# Platform:    ARSE Mk.I Autonomous Traffic Cone (Pimoroni Yukon RP2040)
# Description: PA1010D multi-constellation GNSS receiver driver over I2C (0x10).
#              Parses NMEA 0183 GNRMC/GNGGA directly into 1e-7 degree integers
#              (1.1 cm resolution) to avoid RP2040 32-bit float precision loss.
# Copyright (c) 2026 Autonomous Road Systems Engineering (ARSE).
# ==============================================================================

import time


class PA1010D:
    def __init__(self, i2c, address=0x10):
        self.i2c = i2c
        self.address = address
        self.buffer = ""
        self._rx_buf = bytearray(48)
        self._clean_buf = bytearray(48)
        self._clean_mv = memoryview(self._clean_buf)

        # Telemetry state (lat_e7 / lon_e7 store deg * 10_000_000 as exact ints)
        self.has_fix = False
        self.lat_e7 = 0
        self.lon_e7 = 0
        self.latitude = 0.0
        self.longitude = 0.0
        self.altitude = 0.0
        self.speed_kmh = 0.0
        self.course_deg = 0.0
        self.satellites = 0
        self.hdop = 99.9
        self.utc_time = ""
        self.last_update = 0

    def update(self):
        try:
            if hasattr(self.i2c, "readfrom_into"):
                self.i2c.readfrom_into(self.address, self._rx_buf)
                raw = self._rx_buf
            else:
                raw = self.i2c.readfrom(self.address, 48)
        except OSError:
            return False

        n = 0
        has_buf = bool(self.buffer)
        cb = self._clean_buf
        for b in raw:
            if 32 <= b <= 126:
                cb[n] = b
                n += 1
                has_buf = True
            elif b == 10 and has_buf:
                cb[n] = 10
                n += 1
                has_buf = False
        if n == 0:
            return False

        self.buffer += bytes(self._clean_mv[:n]).decode("ascii")
        if len(self.buffer) > 512:
            self.buffer = self.buffer[-256:]

        updated = False
        while "\n" in self.buffer:
            line, self.buffer = self.buffer.split("\n", 1)
            line = line.strip()
            if line.startswith("$") and self._verify_checksum(line):
                self._parse_sentence(line)
                updated = True
                self.last_update = time.ticks_ms()

        return updated

    def _verify_checksum(self, sentence):
        if "*" not in sentence:
            return False
        content, csum_str = sentence[1:].rsplit("*", 1)
        try:
            expected_csum = int(csum_str, 16)
        except ValueError:
            return False

        csum = 0
        for char in content:
            csum ^= ord(char)
        return csum == expected_csum

    def _parse_sentence(self, sentence):
        parts = sentence.split("*")[0].split(",")
        tag = parts[0]

        # RMC: Recommended Minimum Navigation Information
        if tag in ("$GNRMC", "$GPRMC"):
            if len(parts) >= 8:
                self.utc_time = parts[1]
                self.has_fix = (parts[2] == "A")
                if self.has_fix:
                    lat_i = self._parse_coord_e7(parts[3], parts[4])
                    lon_i = self._parse_coord_e7(parts[5], parts[6])
                    if lat_i is not None and lon_i is not None:
                        self.lat_e7 = lat_i
                        self.lon_e7 = lon_i
                        self.latitude = lat_i / 10000000.0
                        self.longitude = lon_i / 10000000.0
                    if parts[7]:
                        try:
                            self.speed_kmh = float(parts[7]) * 1.852
                        except ValueError:
                            pass
                    if len(parts) >= 9 and parts[8]:
                        try:
                            self.course_deg = float(parts[8])
                        except ValueError:
                            pass

        # GGA: Global Positioning System Fix Data
        elif tag in ("$GNGGA", "$GPGGA"):
            if len(parts) >= 10:
                self.utc_time = parts[1]
                fix_quality = parts[6]
                self.has_fix = fix_quality not in ("", "0")
                if parts[7]:
                    try:
                        self.satellites = int(parts[7])
                    except ValueError:
                        pass
                if parts[8]:
                    try:
                        self.hdop = float(parts[8])
                    except ValueError:
                        pass
                if self.has_fix:
                    lat_i = self._parse_coord_e7(parts[2], parts[3])
                    lon_i = self._parse_coord_e7(parts[4], parts[5])
                    if lat_i is not None and lon_i is not None:
                        self.lat_e7 = lat_i
                        self.lon_e7 = lon_i
                        self.latitude = lat_i / 10000000.0
                        self.longitude = lon_i / 10000000.0
                    if parts[9]:
                        try:
                            self.altitude = float(parts[9])
                        except ValueError:
                            pass

    def _parse_coord_e7(self, raw_val, hemisphere):
        # Pure integer NMEA DDMM.MMMM -> 1e-7 deg (1.1 cm resolution on 32-bit float)
        if not raw_val or "." not in raw_val:
            return None
        try:
            whole, frac = raw_val.split(".", 1)
            if len(whole) < 3:
                return None
            deg = int(whole[:-2])
            mins = int(whole[-2:])
            frac_padded = (frac + "0000000")[:7]
            t_mins = mins * 10000000 + int(frac_padded)
            frac_e7 = (t_mins + 30) // 60
            val_e7 = deg * 10000000 + frac_e7
            if hemisphere in ("S", "W"):
                val_e7 = -val_e7
            return val_e7
        except (ValueError, IndexError):
            return None

    def get_status(self):
        return {
            "has_fix": self.has_fix,
            "lat_e7": self.lat_e7,
            "lon_e7": self.lon_e7,
            "latitude": round(self.latitude, 6),
            "longitude": round(self.longitude, 6),
            "altitude_m": round(self.altitude, 1),
            "speed_kmh": round(self.speed_kmh, 1),
            "course_deg": round(self.course_deg, 1),
            "satellites": self.satellites,
            "hdop": round(self.hdop, 1),
            "utc_time": self.utc_time,
        }
