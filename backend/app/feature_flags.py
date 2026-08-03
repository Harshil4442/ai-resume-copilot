from __future__ import annotations

import hashlib
import os
import sys
from dataclasses import asdict, dataclass

FEATURE_DEFAULTS: dict[str, tuple[bool, int]] = {
    "career_workspace": (True, 100),
    "evidence_tailoring": (True, 100),
    "async_analysis": (True, 100),
    "referral_credit": (False, 0),
}


def _runtime_defaults(key: str) -> tuple[bool, int]:
    app_env = (os.getenv("APP_ENV") or "").strip().lower()
    if not app_env and "pytest" in sys.modules:
        app_env = "test"
    if app_env in {"development", "dev", "local", "test", "staging"}:
        return FEATURE_DEFAULTS[key]
    return False, 0


@dataclass(frozen=True)
class FeatureDecision:
    key: str
    enabled: bool
    variant: str
    rollout_percent: int
    bucket: int
    reason: str

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _env_percent(name: str, default: int) -> int:
    try:
        return max(0, min(100, int(os.getenv(name, str(default)))))
    except ValueError:
        return default


def _csv(name: str) -> set[str]:
    return {
        value.strip().lower()
        for value in (os.getenv(name) or "").split(",")
        if value.strip()
    }


def decide_feature(
    key: str,
    *,
    user_id: int,
    email: str,
) -> FeatureDecision:
    if key not in FEATURE_DEFAULTS:
        raise KeyError(f"Unknown feature: {key}")
    default_enabled, default_percent = _runtime_defaults(key)
    prefix = f"FEATURE_{key.upper()}"
    globally_enabled = _env_bool(f"{prefix}_ENABLED", default_enabled)
    rollout_percent = _env_percent(f"{prefix}_ROLLOUT_PERCENT", default_percent)
    digest = hashlib.sha256(f"hirewiz:{key}:{user_id}".encode()).digest()
    bucket = int.from_bytes(digest[:4], "big") % 100

    internal_emails = _csv("FEATURE_INTERNAL_EMAILS")
    invited_ids = _csv(f"{prefix}_USER_IDS")
    if not globally_enabled:
        enabled, reason = False, "kill_switch"
    elif email.strip().lower() in internal_emails:
        enabled, reason = True, "internal"
    elif str(user_id) in invited_ids:
        enabled, reason = True, "invited"
    elif bucket < rollout_percent:
        enabled, reason = True, "percentage"
    else:
        enabled, reason = False, "percentage"
    return FeatureDecision(
        key=key,
        enabled=enabled,
        variant="on" if enabled else "off",
        rollout_percent=rollout_percent,
        bucket=bucket,
        reason=reason,
    )


def decisions_for_user(*, user_id: int, email: str) -> dict[str, dict[str, object]]:
    return {
        key: decide_feature(key, user_id=user_id, email=email).to_dict()
        for key in FEATURE_DEFAULTS
    }
