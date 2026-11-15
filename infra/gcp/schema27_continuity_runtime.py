"""Verify the pinned serving credential on the continuity database read-only."""
from __future__ import annotations

from subprocess import SubprocessError

import schema27_continuity_scope as scope
from scripts.neon_direct_identity import NativeReleaseSecretReader
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError

PROJECT_NUMBER = "157225724590"
PRODUCTION_TLS = {
    "sslmode": "verify-full",
    "channel_binding": "require",
    "sslrootcert": "/etc/ssl/certs/ca-certificates.crt",
}


def _runtime_password(reader: NativeReleaseSecretReader, pin: scope.ContinuityDatabasePin) -> str:
    pin.validate()
    reference = pin.runtime_reference
    name, version = reference.split(":")
    resource = f"projects/{PROJECT_NUMBER}/secrets/{name}/versions/{version}"
    try:
        before = reader.describe_release_secret(reference)
        etag = before.get("etag") if type(before) is dict else None
        scope._need(type(etag) is str and 1 <= len(etag) <= 256 and not any(c.isspace() for c in etag)
                    and before.get("name") == resource and before.get("state") == "ENABLED",
                    "runtime_source_mismatch")
        value = reader.access_release_secret(reference)
        scope._need(type(value) is str and 1 <= len(value.encode()) <= 8192 and "\x00" not in value,
                    "runtime_secret_shape")
        scope._need(reader.describe_release_secret(reference) == before, "runtime_source_changed")
        url = make_url(value)
        if ((url.drivername, url.username, url.host, url.port, url.database, dict(url.query)) !=
                ("postgresql", scope.neon.RUNTIME, pin.host, 5432, pin.database, PRODUCTION_TLS)
                or type(url.password) is not str or not url.password):
            raise scope.rolling.RollingDenied("schema27_continuity_runtime_target_mismatch")
        return url.password
    except scope.rolling.RollingDenied:
        raise
    except (ArgumentError, OSError, RuntimeError, SubprocessError, ValueError):
        raise scope.rolling.RollingDenied("schema27_continuity_runtime_ref_unavailable") from None


def observe_runtime(custody: scope.BridgeCustody, journal: scope.rolling.Journal, purpose: scope.neon.Purpose,
                    reader: NativeReleaseSecretReader) -> int:
    scope.verify_journal(custody, journal)
    scope._need(type(purpose) is scope.neon.Purpose, "runtime_purpose_invalid")
    password = _runtime_password(reader, custody.database)
    return scope.neon.observe_runtime(purpose, runtime_password=password)
