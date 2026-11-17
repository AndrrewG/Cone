# ==============================================================================
# ARSE — Autonomous Road Systems Engineering
# Proprietary & Confidential — Division of Conical Mobility & Traffic Robotics
# ------------------------------------------------------------------------------
# Module:      drivers/omni_kinematics.py
# Platform:    ARSE Mk.I Autonomous Traffic Cone (Pimoroni Yukon RP2040)
# Description: 3-wheel equilateral holonomic inverse kinematics solver mapping
#              planar body twist (vx, vy, omega) to normalized wheel commands.
# Copyright (c) 2026 Autonomous Road Systems Engineering (ARSE).
# ==============================================================================
import math

class Omni3Kinematics:
    def __init__(
        self,
        wheel_radius_m=0.029,
        base_radius_m=0.12088,
        max_speed_mps=0.5,
        wheel_angles_deg=(60.0, 300.0, 180.0),
    ):
        self.r = wheel_radius_m     # Wheel radius (m)
        self.R = base_radius_m      # Mid-plane centre-to-wheel contact radius (m)
        self.max_speed = max_speed_mps

        # Wheel contact azimuths (CCW from +X Forward nose, +Y Left, +Z Up):
        # Wheel A (Ch 1, Slot 1 M1): Front-Left  (+60 deg)
        # Wheel B (Ch 2, Slot 1 M2): Front-Right (300 deg / -60 deg)
        # Wheel C (Ch 3, Slot 2 M1): Rear vertex (180 deg)
        self.angles = [
            math.radians(wheel_angles_deg[0]),
            math.radians(wheel_angles_deg[1]),
            math.radians(wheel_angles_deg[2]),
        ]

    def set_wheel_angles(self, deg1, deg2, deg3):
        self.angles = [
            math.radians(deg1),
            math.radians(deg2),
            math.radians(deg3)
        ]

    def compute_wheel_speeds(self, vx, vy, omega):
        a0, a1, a2 = self.angles[0], self.angles[1], self.angles[2]
        rw = self.R * omega
        s0 = -math.sin(a0) * vx + math.cos(a0) * vy + rw
        s1 = -math.sin(a1) * vx + math.cos(a1) * vy + rw
        s2 = -math.sin(a2) * vx + math.cos(a2) * vy + rw

        # Normalise without heap allocation so no motor exceeds +-1.0
        max_val = abs(s0)
        if abs(s1) > max_val:
            max_val = abs(s1)
        if abs(s2) > max_val:
            max_val = abs(s2)
        if max_val > 1.0:
            inv = 1.0 / max_val
            return s0 * inv, s1 * inv, s2 * inv

        return s0, s1, s2
