import hashlib
import json
import os
from typing import Any, Optional

try:
    import redis
except Exception:  # pragma: no cover - optional dependency at runtime
    redis = None


DEFAULT_TTL_SECONDS = int(os.getenv("MARKET_CACHE_TTL_SECONDS", "21600"))


def cache_key(prefix: str, payload: dict) -> str:
    raw = json.dumps(payload, sort_keys=True, default=str)
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    return f"{prefix}:{digest}"


def _client():
    url = os.getenv("REDIS_URL", "").strip()
    if not url or redis is None:
        return None
    try:
        return redis.from_url(url, decode_responses=True)
    except Exception:
        return None


def get_json(key: str) -> Optional[Any]:
    client = _client()
    if client is None:
        return None
    try:
        raw = client.get(key)
        return json.loads(raw) if raw else None
    except Exception:
        return None


def set_json(key: str, value: Any, ttl_seconds: int = DEFAULT_TTL_SECONDS) -> None:
    client = _client()
    if client is None:
        return
    try:
        client.setex(key, ttl_seconds, json.dumps(value, default=str))
    except Exception:
        return

