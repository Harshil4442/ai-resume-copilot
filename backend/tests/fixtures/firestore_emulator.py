"""Explicit loopback-only endpoint for an independently owned test emulator."""
from __future__ import annotations

import os
import re


def local_firestore_endpoint() -> str:
    port = os.getenv("HIREWIZ_AUTHORITY_EMULATOR_PORT", "58877")
    if re.fullmatch(r"[1-9][0-9]{3,4}", port) is None or not 1024 <= int(port) <= 65535:
        raise ValueError("A nonprivileged loopback test-emulator port is required")
    return f"127.0.0.1:{port}"
