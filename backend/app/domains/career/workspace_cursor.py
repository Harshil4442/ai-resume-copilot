"""Purpose-isolated keyset tokens: pagination context is never authorization."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
from datetime import UTC, datetime

from fastapi import HTTPException

from ...security import JWT_SECRET

MAX_CURSOR_LENGTH = 1024
_PURPOSE = b"hirewiz:workspace-page:v1\0"
_ALPHABET = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_")


def naive_utc(value: datetime) -> datetime:
    return value.astimezone(UTC).replace(tzinfo=None) if value.tzinfo is not None else value


def encode(context: tuple, stamp: datetime, identity: str) -> str:
    raw = json.dumps([1, *context, naive_utc(stamp).isoformat(), identity], separators=(",", ":")).encode()
    signature = hmac.new(JWT_SECRET.encode(), _PURPOSE + raw, hashlib.sha256).digest()
    return base64.urlsafe_b64encode(raw + signature).decode().rstrip("=")


def decode(value: str, context: tuple) -> tuple[datetime, str]:
    try:
        if not value or len(value) > MAX_CURSOR_LENGTH or any(c not in _ALPHABET for c in value):
            raise ValueError
        decoded = base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
        raw, signature = decoded[:-32], decoded[-32:]
        expected = hmac.new(JWT_SECRET.encode(), _PURPOSE + raw, hashlib.sha256).digest()
        if not hmac.compare_digest(signature, expected):
            raise ValueError
        data = json.loads(raw)
        if (type(data) is not list or len(data) != len(context) + 3 or type(data[0]) is not int or data[0] != 1
                or data[1:-2] != list(context) or any(type(a) is not type(b) for a, b in zip(data[1:-2], context, strict=True))
                or type(data[-2]) is not str or type(data[-1]) is not str or not 1 <= len(data[-1]) <= 64):
            raise ValueError
        stamp = datetime.fromisoformat(data[-2])
        if (stamp.tzinfo is not None or stamp.isoformat() != data[-2]
                or base64.urlsafe_b64encode(decoded).decode().rstrip("=") != value):
            raise ValueError
        return stamp, data[-1]
    except (ValueError, TypeError, UnicodeError):
        raise HTTPException(400, detail={"code": "invalid_workspace_cursor", "message": "Refresh this workspace collection and try again."}) from None
