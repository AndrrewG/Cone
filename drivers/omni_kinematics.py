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
        speeds = []
        for alpha in self.angles:
            # v_wheel = -sin(alpha) * vx + cos(alpha) * vy + R * omega
            v_i = -math.sin(alpha) * vx + math.cos(alpha) * vy + (self.R * omega)
            speeds.append(v_i)

        # Normalize so no motor exceeds +-1.0 full speed while preserving direction
        max_val = max(abs(s) for s in speeds)
        if max_val > 1.0:
            speeds = [s / max_val for s in speeds]

        return speeds[0], speeds[1], speeds[2]
