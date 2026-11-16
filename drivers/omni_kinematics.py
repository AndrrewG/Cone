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
    def __init__(self, wheel_radius_m=0.029, base_radius_m=0.090, max_speed_mps=0.5):
        self.r = wheel_radius_m     # Wheel radius
        self.R = base_radius_m      # Distance from center of robot to wheels
        self.max_speed = max_speed_mps

        # Wheel mounting angles (radians) around the perimeter:
        # Wheel 1: Front-Right (+30 deg)
        # Wheel 2: Front-Left  (+150 deg)
        # Wheel 3: Rear        (+270 deg / -90 deg)
        self.angles = [
            math.radians(30.0),
            math.radians(150.0),
            math.radians(270.0)
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
