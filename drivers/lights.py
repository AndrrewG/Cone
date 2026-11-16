# ==============================================================================
# ARSE — Autonomous Road Systems Engineering
# Proprietary & Confidential — Division of Conical Mobility & Traffic Robotics
# ------------------------------------------------------------------------------
# Module:      drivers/lights.py
# Platform:    ARSE Mk.I Autonomous Traffic Cone (Pimoroni Yukon RP2040)
# Description: Non-blocking conical LED array state machine (WS2812 / Plasma).
#              Automatically gates Slot 4 5V power rail when off to conserve mAh.
# Copyright (c) 2026 Autonomous Road Systems Engineering (ARSE).
# ==============================================================================

import math
import time


class ConeLights:
    COLOR_AMBER = (255, 130, 0)
    COLOR_ORANGE = (255, 75, 0)
    COLOR_RED = (255, 0, 0)
    COLOR_BLUE = (0, 80, 255)
    COLOR_GREEN = (0, 220, 60)
    COLOR_OFF = (0, 0, 0)

    MODES = ("off", "beacon", "strobe", "solid", "pulse", "police", "rainbow")

    def __init__(self, strip, num_leds=50, led_module=None, brightness=0.4):
        self.strip = strip
        self.num_leds = num_leds
        self.led_mod = led_module
        self.brightness = max(0.05, min(1.0, float(brightness)))
        self.mode = "off"
        self._step = 0
        self._solid_dirty = False
        self.clear()
        if self.led_mod:
            self.led_mod.disable()

    def _ensure_power(self, on):
        if not self.led_mod:
            return
        if on and not self.led_mod.is_enabled():
            self.led_mod.enable()
        elif not on and self.led_mod.is_enabled():
            self.led_mod.disable()

    def set_mode(self, mode, brightness=None):
        if brightness is not None:
            self.brightness = max(0.05, min(1.0, float(brightness)))
            self._solid_dirty = True

        m = str(mode).lower()
        if m not in self.MODES:
            raise ValueError("Unknown LED mode: {}".format(m))

        if m == "off":
            self.mode = "off"
            self.clear()
            self._ensure_power(False)
        else:
            self._ensure_power(True)
            self.mode = m
            self._step = 0
            self._solid_dirty = True
        return self.get_status()

    def set_brightness(self, brightness):
        self.brightness = max(0.05, min(1.0, float(brightness)))
        self._solid_dirty = True
        return self.get_status()

    def clear(self):
        for i in range(self.num_leds):
            self.strip.set_rgb(i, 0, 0, 0)
        self.strip.update()

    def fill(self, r, g, b):
        br = self.brightness
        sr = int(r * br)
        sg = int(g * br)
        sb = int(b * br)
        for i in range(self.num_leds):
            self.strip.set_rgb(i, sr, sg, sb)
        self.strip.update()

    def tick(self):
        if self.mode == "off":
            return

        br = self.brightness
        n = self.num_leds

        if self.mode == "solid":
            if self._solid_dirty:
                self.fill(*self.COLOR_AMBER)
                self._solid_dirty = False

        elif self.mode == "beacon":
            # Rotating 8-LED glowing comet around perimeter
            self._step = (self._step + 2) % n
            for i in range(n):
                dist = (self._step - i) % n
                if dist < 8:
                    k = ((8 - dist) / 8.0) * br
                    self.strip.set_rgb(i, int(255 * k), int(130 * k), 0)
                else:
                    self.strip.set_rgb(i, 0, 0, 0)
            self.strip.update()

        elif self.mode == "strobe":
            # Crisp roadside double-flash amber strobe (16-tick = 0.8s cycle at 20Hz)
            phase = self._step % 16
            self._step += 1
            on = phase in (0, 1, 4, 5)
            if on:
                self.fill(*self.COLOR_AMBER)
            else:
                self.clear()

        elif self.mode == "pulse":
            # Smooth breathing amber safety glow
            self._step = (self._step + 1) % 40
            wave = 0.2 + 0.8 * (0.5 * (1.0 + math.sin(2.0 * math.pi * self._step / 40.0)))
            k = wave * br
            r = int(255 * k)
            g = int(115 * k)
            for i in range(n):
                self.strip.set_rgb(i, r, g, 0)
            self.strip.update()

        elif self.mode == "police":
            # Alternating half-strip red / blue emergency flash
            phase = self._step % 12
            self._step += 1
            half = n // 2
            left_on = phase in (0, 2)
            right_on = phase in (6, 8)
            for i in range(n):
                if i < half and left_on:
                    self.strip.set_rgb(i, int(255 * br), 0, 0)
                elif i >= half and right_on:
                    self.strip.set_rgb(i, 0, int(70 * br), int(255 * br))
                else:
                    self.strip.set_rgb(i, 0, 0, 0)
            self.strip.update()

        elif self.mode == "rainbow":
            self._step = (self._step + 1) % 100
            offset = self._step / 100.0
            for i in range(n):
                hue = (offset + (i / n)) % 1.0
                self.strip.set_hsv(i, hue, 1.0, br)
            self.strip.update()

    # Legacy helpers kept for hardware_test.py compatibility
    def effect_beacon(self, color=COLOR_AMBER, speed_step=1):
        self._ensure_power(True)
        self._step = (self._step + speed_step) % self.num_leds
        for i in range(self.num_leds):
            dist = (i - self._step) % self.num_leds
            if dist < 6:
                k = (6 - dist) / 6.0
                self.strip.set_rgb(i, int(color[0] * k), int(color[1] * k), int(color[2] * k))
            else:
                self.strip.set_rgb(i, 0, 0, 0)
        self.strip.update()

    def effect_hazard_strobe(self, count=2):
        self._ensure_power(True)
        for _ in range(count):
            self.fill(*self.COLOR_AMBER)
            time.sleep_ms(60)
            self.clear()
            time.sleep_ms(60)
        time.sleep_ms(300)

    def effect_solid_cone(self):
        self._ensure_power(True)
        self.fill(*self.COLOR_AMBER)

    def effect_rainbow_test(self):
        self._ensure_power(True)
        for i in range(self.num_leds):
            self.strip.set_hsv(i, i / self.num_leds, 1.0, 0.8)
        self.strip.update()

    def get_status(self):
        pgood = self.led_mod.read_power_good() if (self.led_mod and self.led_mod.is_enabled()) else None
        return {
            "mode": self.mode,
            "brightness": round(self.brightness, 2),
            "enabled": self.led_mod.is_enabled() if self.led_mod else (self.mode != "off"),
            "power_good": pgood,
        }
