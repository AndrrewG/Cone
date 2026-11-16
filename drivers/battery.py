# ==============================================================================
# ARSE — Autonomous Road Systems Engineering
# Proprietary & Confidential — Division of Conical Mobility & Traffic Robotics
# ------------------------------------------------------------------------------
# Module:      drivers/battery.py
# Platform:    ARSE Mk.I Autonomous Traffic Cone (Pimoroni Yukon RP2040)
# Description: Calibrated 3S LiPo Coulomb-counting Battery Management Subsystem
#              with atomic flash persistence and zero-offset ADC compensation.
# Copyright (c) 2026 Autonomous Road Systems Engineering (ARSE).
# ==============================================================================

import json
import os
import time


class BatteryMonitor:
    # 3S LiPo safety voltage thresholds (strictly for readout & cell protection)
    V_FULL = 12.60       # 4.20V / cell
    V_FRESH_HINT = 12.40 # >= 4.13V / cell resting suggests a freshly charged pack
    V_WARNING = 10.50    # 3.50V / cell (Low battery warning)
    V_CUTOFF = 9.90      # 3.30V / cell (Critical safety cutoff)

    # Resting 3S LiPo Open-Circuit Voltage (OCV) table — used ONLY on very first
    # boot when no persisted state exists yet on flash.
    OCV_CURVE = (
        (12.60, 100.0),
        (12.45, 95.0),
        (12.30, 88.0),
        (12.15, 80.0),
        (11.95, 70.0),
        (11.75, 60.0),
        (11.58, 50.0),
        (11.42, 40.0),
        (11.28, 30.0),
        (11.13, 20.0),
        (10.95, 12.0),
        (10.65, 5.0),
        (9.90, 0.0),
    )

    def __init__(
        self,
        yukon,
        capacity_mah=2200.0,
        logic_idle_amps=0.045,
        state_file="battery_state.json",
        save_interval_ms=30000,
    ):
        self.yukon = yukon
        self.default_capacity_mah = float(capacity_mah)
        self.logic_idle_amps = float(logic_idle_amps)
        self.state_file = state_file
        self.save_interval_ms = int(save_interval_ms)

        # Sample initial resting pack voltage before high-power output turns on
        init_v = yukon.read_input_voltage(32)
        self.boot_voltage = init_v
        self.filtered_voltage = init_v
        self.efuse_current_amps = 0.0
        self.last_current_amps = 0.0
        self.avg_current_amps = self.logic_idle_amps if init_v > 5.5 else 0.0

        # Tare 0A ADC offset if main output is currently off
        has_live_tare = False
        if not yukon.is_main_output_enabled():
            self.zero_offset_amps = yukon.read_current(32)
            has_live_tare = True
        else:
            self.zero_offset_amps = 0.349

        # Default state prior to loading persisted JSON
        self.capacity_mah = self.default_capacity_mah
        self.initial_soc_percent = 100.0
        self.discharged_mah = 0.0
        self.seed_mode = "voltage_estimate"
        self.fresh_battery_suspected = False
        self._critical_samples = 0

        loaded = self._load_state(keep_live_tare=has_live_tare)
        if not loaded:
            # First-time initialization (no saved state yet): seed once from resting OCV
            ocv_soc = self.voltage_to_soc(init_v) if init_v > 5.5 else 0.0
            self.initial_soc_percent = ocv_soc
            self.discharged_mah = 0.0
            self.seed_mode = "voltage_estimate"
            self._recalculate()
            self.save()
        else:
            self._recalculate()
            # Check if user may have swapped in a fresh pack while powered off
            if init_v >= self.V_FRESH_HINT and self.soc_percent < 82.0:
                self.fresh_battery_suspected = True

        now = time.ticks_ms()
        self._last_time = now
        self._last_save_time = now
        self._last_saved_discharged = self.discharged_mah

    def _recalculate(self):
        start_mah = self.capacity_mah * (self.initial_soc_percent / 100.0)
        self._remaining_mah = max(0.0, min(self.capacity_mah, start_mah - self.discharged_mah))
        if self.capacity_mah > 0:
            self.soc_percent = (self._remaining_mah / self.capacity_mah) * 100.0
        else:
            self.soc_percent = 0.0

    def _load_state(self, keep_live_tare=True):
        try:
            with open(self.state_file, "r") as f:
                data = json.load(f)
            cap = float(data.get("capacity_mah", self.default_capacity_mah))
            if 100.0 <= cap <= 50000.0:
                self.capacity_mah = cap
            self.initial_soc_percent = max(0.0, min(100.0, float(data.get("initial_soc_pct", 100.0))))
            self.discharged_mah = max(0.0, float(data.get("discharged_mah", 0.0)))
            self.seed_mode = str(data.get("seed_mode", "voltage_estimate"))
            if not keep_live_tare and "zero_offset_a" in data:
                self.zero_offset_amps = float(data["zero_offset_a"])
            return True
        except Exception:
            return False

    def save(self):
        payload = {
            "capacity_mah": round(self.capacity_mah, 1),
            "initial_soc_pct": round(self.initial_soc_percent, 2),
            "discharged_mah": round(self.discharged_mah, 3),
            "remaining_mah": round(self._remaining_mah, 2),
            "seed_mode": self.seed_mode,
            "zero_offset_a": round(self.zero_offset_amps, 4),
        }
        tmp_file = self.state_file + ".tmp"
        try:
            with open(tmp_file, "w") as f:
                json.dump(payload, f)
            try:
                os.rename(tmp_file, self.state_file)
            except OSError:
                try:
                    os.remove(self.state_file)
                except OSError:
                    pass
                os.rename(tmp_file, self.state_file)
            self._last_save_time = time.ticks_ms()
            self._last_saved_discharged = self.discharged_mah
            return True
        except Exception as e:
            print("[Battery] Save error:", e)
            return False

    def mark_new_full_battery(self):
        self.initial_soc_percent = 100.0
        self.discharged_mah = 0.0
        self.seed_mode = "full_charge"
        self.fresh_battery_suspected = False
        self._recalculate()
        self.save()
        return self.get_status()

    def set_capacity_mah(self, new_capacity_mah):
        cap = float(new_capacity_mah)
        if cap < 100.0 or cap > 50000.0:
            raise ValueError("Capacity must be between 100 and 50000 mAh")
        self.capacity_mah = cap
        self._recalculate()
        self.save()
        return self.get_status()

    def set_current_soc(self, soc_percent):
        pct = max(0.0, min(100.0, float(soc_percent)))
        self.initial_soc_percent = pct
        self.discharged_mah = 0.0
        self.seed_mode = "full_charge" if pct >= 99.9 else "manual_set"
        self.fresh_battery_suspected = False
        self._recalculate()
        self.save()
        return self.get_status()

    def dismiss_fresh_hint(self):
        self.fresh_battery_suspected = False

    def calibrate_zero(self, samples=32):
        if not self.yukon.is_main_output_enabled():
            self.zero_offset_amps = self.yukon.read_current(samples)
        return self.zero_offset_amps

    def read_calibrated_current(self, samples=8):
        raw_c = self.yukon.read_current(samples)
        z = self.zero_offset_amps
        if raw_c <= z + 0.012:
            return 0.0
        if raw_c < 1.0 and z < 0.95:
            return (raw_c - z) / (1.0 - z)
        return raw_c

    @classmethod
    def voltage_to_soc(cls, voltage):
        if voltage >= cls.OCV_CURVE[0][0]:
            return 100.0
        if voltage <= cls.OCV_CURVE[-1][0]:
            return 0.0

        for i in range(len(cls.OCV_CURVE) - 1):
            v_high, p_high = cls.OCV_CURVE[i]
            v_low, p_low = cls.OCV_CURVE[i + 1]
            if v_low <= voltage <= v_high:
                frac = (voltage - v_low) / (v_high - v_low)
                return p_low + frac * (p_high - p_low)
        return 0.0

    def update(self):
        now = time.ticks_ms()
        dt_s = max(0.0, time.ticks_diff(now, self._last_time) / 1000.0)
        self._last_time = now

        # Cap dt_s in case of long pause so a stale timestamp doesn't spike integration
        if dt_s > 5.0:
            dt_s = 5.0

        raw_v = self.yukon.read_input_voltage(8)
        efuse_a = self.read_calibrated_current(8) if self.yukon.is_main_output_enabled() else 0.0
        has_pack = (raw_v > 5.5)
        total_a = (efuse_a + self.logic_idle_amps) if has_pack else 0.0

        self.efuse_current_amps = efuse_a
        self.last_current_amps = total_a
        self.filtered_voltage = (0.20 * raw_v) + (0.80 * self.filtered_voltage)
        self.avg_current_amps = (0.05 * total_a) + (0.95 * self.avg_current_amps)

        # Pure Coulomb integration (mAh = A * 1000 * hours)
        if has_pack and dt_s > 0.0:
            dmah = (total_a * 1000.0) * (dt_s / 3600.0)
            self.discharged_mah += dmah
            self._recalculate()

        # Debounce critical cutoff (require ~1s of sustained low voltage)
        if has_pack and self.filtered_voltage <= self.V_CUTOFF:
            self._critical_samples = min(20, self._critical_samples + 1)
        else:
            self._critical_samples = max(0, self._critical_samples - 1)

        # Periodic flash persistence
        if (
            time.ticks_diff(now, self._last_save_time) >= self.save_interval_ms
            and abs(self.discharged_mah - self._last_saved_discharged) >= 0.2
        ):
            self.save()

        return self.get_status()

    def get_status(self):
        v = self.filtered_voltage
        has_pack = (v > 5.5)
        cell_v = (v / 3.0) if has_pack else 0.0
        power_w = v * self.last_current_amps if has_pack else 0.0
        if self.avg_current_amps > 0.005 and has_pack:
            est_min = (self._remaining_mah / (self.avg_current_amps * 1000.0)) * 60.0
        else:
            est_min = 0.0

        return {
            "battery_present": has_pack,
            "voltage_v": round(v, 2),
            "cell_voltage_v": round(cell_v, 2),
            "current_a": round(self.last_current_amps, 3),
            "efuse_current_a": round(self.efuse_current_amps, 3),
            "power_w": round(power_w, 2),
            "soc_percent": round(self.soc_percent, 1),
            "initial_soc_percent": round(self.initial_soc_percent, 1),
            "capacity_mah": round(self.capacity_mah, 0),
            "remaining_mah": round(self._remaining_mah, 1),
            "discharged_mah": round(self.discharged_mah, 2),
            "consumed_mah": round(self.discharged_mah, 2),  # compat alias
            "est_minutes": round(est_min, 1),
            "seed_mode": self.seed_mode,
            "fresh_battery_suspected": self.fresh_battery_suspected,
            "zero_offset_a": round(self.zero_offset_amps, 3),
            "is_low": has_pack and (v < self.V_WARNING),
            "is_critical": has_pack and (self._critical_samples >= 10),
        }
