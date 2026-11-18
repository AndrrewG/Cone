# ==============================================================================
# ARSE — Autonomous Road Systems Engineering
# Proprietary & Confidential — Division of Conical Mobility & Traffic Robotics
# ------------------------------------------------------------------------------
# Module:      drivers/icm20948.py
# Platform:    ARSE Mk.I Autonomous Traffic Cone (Pimoroni Yukon RP2040)
# Description: Full 9DoF ICM-20948 + AK09916 Magnetometer driver & AHRS filter.
#              Provides tilt-compensated gyro/compass heading (0-360 deg),
#              hard/soft-iron flash calibration, and tip-over safety detection.
# Copyright (c) 2026 Autonomous Road Systems Engineering (ARSE).
# ==============================================================================

import json
import math
import os
import struct
import time


class ICM20948:
    # ICM-20948 Bank 0 Registers
    REG_WHO_AM_I = 0x00
    REG_USER_CTRL = 0x03
    REG_PWR_MGMT_1 = 0x06
    REG_PWR_MGMT_2 = 0x07
    REG_INT_PIN_CFG = 0x0F
    REG_ACCEL_XOUT_H = 0x2D
    REG_GYRO_XOUT_H = 0x33
    REG_TEMP_OUT_H = 0x39
    REG_BANK_SEL = 0x7F

    # ICM-20948 Bank 2 Registers
    REG_GYRO_CONFIG_1 = 0x01
    REG_ACCEL_CONFIG = 0x14

    # AK09916 Magnetometer Registers (I2C 0x0C via Bypass)
    AK_WHO_AM_I = 0x01      # Expected 0x09
    AK_ST1 = 0x10           # Bit 0: DRDY
    AK_HXL = 0x11           # 6 bytes HXL..HZH + 2 bytes dummy/ST2 (0x18)
    AK_CNTL2 = 0x31         # 0x08 = 100 Hz continuous mode 4
    AK_CNTL3 = 0x32         # 0x01 = soft reset

    ACCEL_SCALE_2G = 16384.0
    GYRO_SCALE_250 = 131.0
    MAG_SCALE_UT = 0.15

    def __init__(
        self,
        i2c,
        address=0x68,
        mag_address=0x0C,
        cal_file="compass_cal.json",
        declination_deg=0.0,
        max_tilt_deg=40.0,
        mount_yaw_deg=0.0,
    ):
        self.i2c = i2c
        self.address = address
        self.mag_address = mag_address
        self.cal_file = cal_file
        self.declination_deg = float(declination_deg)
        self.max_tilt_deg = float(max_tilt_deg)
        self.mount_yaw_deg = float(mount_yaw_deg)
        yaw_rad = math.radians(self.mount_yaw_deg)
        self._cos_m = math.cos(yaw_rad)
        self._sin_m = math.sin(yaw_rad)
        self._current_bank = -1

        # Verify ICM-20948
        self._set_bank(0)
        chip_id = self._read_reg(self.REG_WHO_AM_I)
        if chip_id != 0xEA:
            raise RuntimeError("ICM-20948 WHO_AM_I mismatch: 0x{:02X}".format(chip_id))

        self._reset()
        self._wake()

        # Initialize AK09916 magnetometer over I2C bypass
        self.mag_online = self._init_magnetometer()

        # Hard-iron offsets & soft-iron scales (in body frame, uT)
        self.mag_offsets = [0.0, 0.0, 0.0]
        self.mag_scales = [1.0, 1.0, 1.0]
        self.is_calibrated = False
        self._load_calibration()

        # Live calibration state
        self.calibrating = False
        self._cal_min = [9999.0, 9999.0, 9999.0]
        self._cal_max = [-9999.0, -9999.0, -9999.0]
        self.cal_samples = 0

        # AHRS fused attitude state
        self.pitch_deg = 0.0
        self.roll_deg = 0.0
        self.heading_deg = 0.0
        self.mag_heading_deg = 0.0
        self.temp_c = 25.0
        self.last_mag_ut = (0.0, 0.0, 0.0)
        self.last_accel_g = (0.0, 0.0, 1.0)
        self.last_gyro_dps = (0.0, 0.0, 0.0)
        self.ax_g = 0.0
        self.ay_g = 0.0
        self.az_g = 1.0
        self.gx_dps = 0.0
        self.gy_dps = 0.0
        self.gz_dps = 0.0
        self._buf14 = bytearray(14)
        self._mag_list = [0.0, 0.0, 0.0]
        self._status = {
            "online": True,
            "mag_online": self.mag_online,
            "heading_deg": 0.0,
            "mag_heading_deg": 0.0,
            "pitch_deg": 0.0,
            "roll_deg": 0.0,
            "tilt_fault": False,
            "calibrated": self.is_calibrated,
            "calibrating": False,
            "cal_samples": 0,
            "mag_ut": self._mag_list,
            "temp_c": 25.0,
        }
        self._heading_seeded = False
        self._tilt_ticks = 0
        self.tilt_fault = False
        self._last_update_ms = time.ticks_ms()

    def _set_bank(self, bank):
        if self._current_bank != bank:
            self.i2c.writeto_mem(self.address, self.REG_BANK_SEL, bytes([bank << 4]))
            self._current_bank = bank

    def _read_reg(self, reg):
        return self.i2c.readfrom_mem(self.address, reg, 1)[0]

    def _write_reg(self, reg, val):
        self.i2c.writeto_mem(self.address, reg, bytes([val]))

    def _reset(self):
        self._set_bank(0)
        self._write_reg(self.REG_PWR_MGMT_1, 0x80)
        time.sleep_ms(60)

    def _wake(self):
        self._set_bank(0)
        self._write_reg(self.REG_PWR_MGMT_1, 0x01)  # Auto clock, wake
        self._write_reg(self.REG_PWR_MGMT_2, 0x00)  # Enable accel + gyro
        time.sleep_ms(25)

        # Configure DLPF + +-250 dps gyro and +-2g accel in Bank 2
        self._set_bank(2)
        self._write_reg(self.REG_GYRO_CONFIG_1, 0x19)   # DLPF enabled, +-250 dps
        self._write_reg(self.REG_ACCEL_CONFIG, 0x19)    # DLPF enabled, +-2g
        self._set_bank(0)

    def _init_magnetometer(self):
        try:
            self._set_bank(0)
            # Disable internal I2C master so auxiliary pins can enter bypass mode
            u_ctrl = self._read_reg(self.REG_USER_CTRL)
            self._write_reg(self.REG_USER_CTRL, u_ctrl & ~0x20)
            time.sleep_ms(5)
            # Enable I2C bypass on auxiliary bus (BYPASS_EN = bit 1)
            self._write_reg(self.REG_INT_PIN_CFG, 0x02)
            time.sleep_ms(10)

            wia2 = self.i2c.readfrom_mem(self.mag_address, self.AK_WHO_AM_I, 1)[0]
            if wia2 != 0x09:
                return False

            # Soft-reset AK09916 then set 100 Hz continuous measurement mode 4
            self.i2c.writeto_mem(self.mag_address, self.AK_CNTL3, b"\x01")
            time.sleep_ms(15)
            self.i2c.writeto_mem(self.mag_address, self.AK_CNTL2, b"\x08")
            time.sleep_ms(10)
            return True
        except Exception:
            return False

    def read_mag_raw_ut(self):
        # Aligns AK09916 die (+X, -Y, -Z) to ICM-20948 body frame; reads ST2 to unlatch
        if not self.mag_online:
            return None
        try:
            # Read ST1 (0x10) through ST2 (0x18) = 9 bytes (reading ST2 unlatches data)
            buf = self.i2c.readfrom_mem(self.mag_address, self.AK_ST1, 9)
            st1 = buf[0]
            st2 = buf[8]
            if not (st1 & 0x01):
                return None
            if st2 & 0x08:  # Magnetic sensor overflow (HOFL)
                return None
            rx, ry, rz = struct.unpack("<hhh", buf[1:7])
            # Align AK09916 die frame -> ICM-20948 die frame -> Cone body frame
            dx = rx * self.MAG_SCALE_UT
            dy = -ry * self.MAG_SCALE_UT
            mz = -rz * self.MAG_SCALE_UT
            mx = dx * self._cos_m - dy * self._sin_m
            my = dx * self._sin_m + dy * self._cos_m
            return (mx, my, mz)
        except OSError:
            return None

    def _load_calibration(self):
        try:
            with open(self.cal_file, "r") as f:
                data = json.load(f)
            offs = data.get("offsets", [0.0, 0.0, 0.0])
            scls = data.get("scales", [1.0, 1.0, 1.0])
            if len(offs) == 3 and len(scls) == 3:
                self.mag_offsets = [float(x) for x in offs]
                self.mag_scales = [float(x) for x in scls]
                self.is_calibrated = True
        except Exception:
            pass

    def start_calibration(self):
        self.calibrating = True
        self._cal_min = [9999.0, 9999.0, 9999.0]
        self._cal_max = [-9999.0, -9999.0, -9999.0]
        self.cal_samples = 0
        return {"ok": True, "calibrating": True}

    def cancel_calibration(self):
        self.calibrating = False
        return {"ok": True, "calibrating": False}

    def save_calibration(self):
        self.calibrating = False
        if self.cal_samples < 15:
            return {"ok": False, "error": "Not enough samples collected — rotate the cone first."}

        spans = [self._cal_max[i] - self._cal_min[i] for i in range(3)]
        if spans[0] < 12.0 or spans[1] < 12.0:
            return {
                "ok": False,
                "error": "Insufficient rotation span ({:.0f}/{:.0f} uT). Spin cone 360 deg flat.".format(
                    spans[0], spans[1]
                ),
            }

        offsets = [(self._cal_max[i] + self._cal_min[i]) * 0.5 for i in range(3)]
        # Use X/Y horizontal average chord if Z wasn't tilted fully during flat spin
        avg_xy = (spans[0] + spans[1]) * 0.5
        scales = [
            avg_xy / spans[0],
            avg_xy / spans[1],
            (avg_xy / spans[2]) if spans[2] >= 15.0 else 1.0,
        ]

        self.mag_offsets = offsets
        self.mag_scales = scales
        self.is_calibrated = True

        payload = {
            "offsets": [round(v, 3) for v in offsets],
            "scales": [round(v, 4) for v in scales],
            "samples": self.cal_samples,
        }
        tmp = self.cal_file + ".tmp"
        try:
            with open(tmp, "w") as f:
                json.dump(payload, f)
            try:
                os.rename(tmp, self.cal_file)
            except OSError:
                try:
                    os.remove(self.cal_file)
                except OSError:
                    pass
                os.rename(tmp, self.cal_file)
        except Exception as e:
            return {"ok": False, "error": str(e)}

        return {"ok": True, "calibrated": True, "offsets": payload["offsets"]}

    def read_all(self):
        self._set_bank(0)
        data = self.i2c.readfrom_mem(self.address, self.REG_ACCEL_XOUT_H, 14)
        ax, ay, az, gx, gy, gz, t = struct.unpack(">hhhhhhh", data)
        dax = ax / self.ACCEL_SCALE_2G
        day = ay / self.ACCEL_SCALE_2G
        dgx = gx / self.GYRO_SCALE_250
        dgy = gy / self.GYRO_SCALE_250
        cm, sm = self._cos_m, self._sin_m
        return {
            "accel": (
                dax * cm - day * sm,
                dax * sm + day * cm,
                az / self.ACCEL_SCALE_2G,
            ),
            "gyro": (
                dgx * cm - dgy * sm,
                dgx * sm + dgy * cm,
                gz / self.GYRO_SCALE_250,
            ),
            "temp": (t / 333.87) + 21.0,
        }

    def read_motion_fast(self):
        # Lightweight 6-axis accel/gyro read for 5 Hz low-power Sentry watch (skips mag & trig)
        d = self.read_all()
        self.last_accel_g = d["accel"]
        self.last_gyro_dps = d["gyro"]
        self.temp_c = d["temp"]
        return self.last_accel_g, self.last_gyro_dps

    def step_fast_50hz(self):
        # Zero-allocation 50 Hz (20 ms) 6-axis gyro/accel burst & yaw integration
        now = time.ticks_ms()
        dt = time.ticks_diff(now, self._last_update_ms) * 0.001
        if dt < 0.001:
            dt = 0.001
        elif dt > 0.200:
            dt = 0.200
        self._last_update_ms = now

        self._set_bank(0)
        if hasattr(self.i2c, "readfrom_mem_into"):
            self.i2c.readfrom_mem_into(self.address, self.REG_ACCEL_XOUT_H, self._buf14)
            ax, ay, az, gx, gy, gz, t = struct.unpack_from(">hhhhhhh", self._buf14, 0)
        else:
            data = self.i2c.readfrom_mem(self.address, self.REG_ACCEL_XOUT_H, 14)
            ax, ay, az, gx, gy, gz, t = struct.unpack(">hhhhhhh", data)

        dax = ax / self.ACCEL_SCALE_2G
        day = ay / self.ACCEL_SCALE_2G
        dgx = gx / self.GYRO_SCALE_250
        dgy = gy / self.GYRO_SCALE_250
        cm, sm = self._cos_m, self._sin_m
        self.ax_g = dax * cm - day * sm
        self.ay_g = dax * sm + day * cm
        self.az_g = az / self.ACCEL_SCALE_2G
        self.gx_dps = dgx * cm - dgy * sm
        self.gy_dps = dgx * sm + dgy * cm
        self.gz_dps = gz / self.GYRO_SCALE_250
        self.temp_c = (t / 333.87) + 21.0
        self.heading_deg = (self.heading_deg - self.gz_dps * dt) % 360.0
        return self.heading_deg

    def update_ahrs(self):
        # Updates pitch, roll, tilt-compensated gyro/compass heading (0..360 CW), and tilt_fault
        now = time.ticks_ms()
        dt = max(0.001, min(0.5, time.ticks_diff(now, self._last_update_ms) / 1000.0))
        self._last_update_ms = now

        self._set_bank(0)
        if hasattr(self.i2c, "readfrom_mem_into"):
            self.i2c.readfrom_mem_into(self.address, self.REG_ACCEL_XOUT_H, self._buf14)
            raw_ax, raw_ay, raw_az, raw_gx, raw_gy, raw_gz, raw_t = struct.unpack_from(">hhhhhhh", self._buf14, 0)
        else:
            data = self.i2c.readfrom_mem(self.address, self.REG_ACCEL_XOUT_H, 14)
            raw_ax, raw_ay, raw_az, raw_gx, raw_gy, raw_gz, raw_t = struct.unpack(">hhhhhhh", data)
        dax = raw_ax / self.ACCEL_SCALE_2G
        day = raw_ay / self.ACCEL_SCALE_2G
        dgx = raw_gx / self.GYRO_SCALE_250
        dgy = raw_gy / self.GYRO_SCALE_250
        cm, sm = self._cos_m, self._sin_m
        ax = dax * cm - day * sm
        ay = dax * sm + day * cm
        az = raw_az / self.ACCEL_SCALE_2G
        gx = dgx * cm - dgy * sm
        gy = dgx * sm + dgy * cm
        gz = raw_gz / self.GYRO_SCALE_250
        self.ax_g, self.ay_g, self.az_g = ax, ay, az
        self.gx_dps, self.gy_dps, self.gz_dps = gx, gy, gz
        self.last_accel_g = (ax, ay, az)
        self.last_gyro_dps = (gx, gy, gz)
        self.temp_c = (raw_t / 333.87) + 21.0

        # Low-pass filtered pitch & roll (degrees)
        raw_pitch = math.degrees(math.atan2(-ax, math.sqrt(ay * ay + az * az)))
        raw_roll = math.degrees(math.atan2(ay, az if abs(az) > 0.01 else 0.01))
        self.pitch_deg = 0.75 * self.pitch_deg + 0.25 * raw_pitch
        self.roll_deg = 0.75 * self.roll_deg + 0.25 * raw_roll

        # Tip-over / pickup detection (debounced ~400ms at 10Hz)
        tilted = (
            abs(self.pitch_deg) > self.max_tilt_deg
            or abs(self.roll_deg) > self.max_tilt_deg
            or az < 0.25
        )
        if tilted:
            self._tilt_ticks = min(15, self._tilt_ticks + 1)
        else:
            self._tilt_ticks = max(0, self._tilt_ticks - 1)
        self.tilt_fault = (self._tilt_ticks >= 4)

        # Integrate gyro Z yaw rate (Note: +gz is CCW, compass heading is CW from North)
        pred_heading = (self.heading_deg - gz * dt) % 360.0

        # Read magnetometer & apply tilt compensation
        raw_m = self.read_mag_raw_ut()
        if raw_m is not None:
            if self.calibrating:
                for i in range(3):
                    if raw_m[i] < self._cal_min[i]:
                        self._cal_min[i] = raw_m[i]
                    if raw_m[i] > self._cal_max[i]:
                        self._cal_max[i] = raw_m[i]
                self.cal_samples += 1

            mx = (raw_m[0] - self.mag_offsets[0]) * self.mag_scales[0]
            my = (raw_m[1] - self.mag_offsets[1]) * self.mag_scales[1]
            mz = (raw_m[2] - self.mag_offsets[2]) * self.mag_scales[2]
            self.last_mag_ut = (mx, my, mz)

            # Tilt-compensate to horizontal plane when not inverted
            p_rad = math.radians(self.pitch_deg)
            r_rad = math.radians(self.roll_deg)
            cos_p = math.cos(p_rad)
            sin_p = math.sin(p_rad)
            cos_r = math.cos(r_rad)
            sin_r = math.sin(r_rad)

            mx_h = mx * cos_p + my * sin_r * sin_p + mz * cos_r * sin_p
            my_h = my * cos_r - mz * sin_r

            horiz_norm = math.sqrt(mx_h * mx_h + my_h * my_h)
            if horiz_norm > 4.0 and not self.tilt_fault:
                mag_hdg = (math.degrees(math.atan2(-my_h, mx_h)) + self.declination_deg) % 360.0
                self.mag_heading_deg = mag_hdg

                if not self._heading_seeded:
                    self.heading_deg = mag_hdg
                    self._heading_seeded = True
                else:
                    # Shortest-angle circular complementary blend (94% gyro, 6% compass)
                    err = (mag_hdg - pred_heading + 180.0) % 360.0 - 180.0
                    self.heading_deg = (pred_heading + 0.06 * err) % 360.0
            else:
                self.heading_deg = pred_heading
        else:
            self.heading_deg = pred_heading

        return self.get_status()

    def get_status(self):
        mx, my, mz = self.last_mag_ut
        ml = self._mag_list
        ml[0] = round(mx, 1)
        ml[1] = round(my, 1)
        ml[2] = round(mz, 1)
        st = self._status
        st["online"] = True
        st["mag_online"] = self.mag_online
        st["heading_deg"] = round(self.heading_deg, 1)
        st["mag_heading_deg"] = round(self.mag_heading_deg, 1)
        st["pitch_deg"] = round(self.pitch_deg, 1)
        st["roll_deg"] = round(self.roll_deg, 1)
        st["tilt_fault"] = self.tilt_fault
        st["calibrated"] = self.is_calibrated
        st["calibrating"] = self.calibrating
        st["cal_samples"] = self.cal_samples
        st["mag_ut"] = ml
        st["temp_c"] = round(self.temp_c, 1)
        return st
