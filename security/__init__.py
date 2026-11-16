# ==============================================================================
# ARSE — Autonomous Road Systems Engineering
# Proprietary & Confidential — Division of Conical Mobility & Traffic Robotics
# ------------------------------------------------------------------------------
# Module:      security/__init__.py
# Platform:    ARSE Mk.I Autonomous Traffic Cone (Pimoroni Yukon RP2040 / RP2350)
# Description: Public security package exports for operator authentication,
#              constant-time comparison, salted SHA-256 hashing, and lockouts.
# Copyright (c) 2026 Autonomous Road Systems Engineering (ARSE).
# ==============================================================================

from security.auth_manager import (
    AuthManager,
    AuthRateLimitError,
    constant_time_compare,
    hash_password,
)

__all__ = [
    "AuthManager",
    "AuthRateLimitError",
    "hash_password",
    "constant_time_compare",
]
