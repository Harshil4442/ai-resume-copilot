"""Two durable host rows; short primary transactions never span employer HTTP.

Restore operations must pause/drain the feed and reseed this coordination table.
Restoring old rows into the same running server cannot be detected by an epoch
stored in those rows. Native server restart, missing state and expired ownership
are detected and require a full monotonic recovery quarantine.
"""
from __future__ import annotations

import math
import re
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.exc import SQLAlchemyError

from .host_pacing import (
    CLOSE_SECONDS,
    COOLDOWN_SECONDS,
    HOSTS,
    LEASE_SECONDS,
    REQUEST_SECONDS,
    PacingError,
    Permit,
)
from .models import EmployerHostPacing

RECOVERY_SECONDS = 32.0
WAIT_SECONDS = 65.0
PENDING_MARGIN_SECONDS = 5.0
TOKEN = re.compile(r"[a-f0-9]{32}")


@dataclass(frozen=True)
class PostgresPermit(Permit):
    authority: str
    server_started_at: datetime
    latest_dispatch: float

    def check_dispatch_deadline(self) -> None:
        if time.monotonic() >= self.latest_dispatch:
            raise PacingError("host_pacing_ownership_lost")


class PostgresCoordinator:
    def __init__(self, engine: Engine, *, lease_seconds: float = LEASE_SECONDS,
                 request_seconds: float = REQUEST_SECONDS,
                 close_seconds: float = CLOSE_SECONDS,
                 cooldown_seconds: float = COOLDOWN_SECONDS,
                 recovery_seconds: float = RECOVERY_SECONDS,
                 wait_seconds: float = WAIT_SECONDS):
        self.engine = engine
        self.lease = lease_seconds
        self.minimum = request_seconds + close_seconds + cooldown_seconds
        self.cooldown, self.recovery, self.wait = cooldown_seconds, recovery_seconds, wait_seconds
        if (engine.dialect.name != "postgresql"
                or not all(math.isfinite(v) and v > 0 for v in (
                    self.lease, self.minimum, self.cooldown, self.recovery, self.wait))
                or self.lease <= self.minimum or self.recovery < self.lease + close_seconds):
            raise PacingError("host_pacing_configuration_invalid")

    @staticmethod
    def _clock(connection: Connection) -> tuple[datetime, datetime]:
        now, started, replica = connection.execute(text(
            "SELECT clock_timestamp(), pg_postmaster_start_time(), pg_is_in_recovery()"
        )).one()
        if (replica is not False or not isinstance(now, datetime)
                or not isinstance(started, datetime) or now.tzinfo is None or started.tzinfo is None):
            raise PacingError("host_pacing_unavailable")
        return now, started

    @staticmethod
    def _invalidate(connection: Connection, host: str, now: datetime, started: datetime,
                    *, highwater: datetime | None = None) -> None:
        connection.execute(update(EmployerHostPacing).where(EmployerHostPacing.host == host).values(
            authority=uuid.uuid4().hex, server_started_at=started, owner_token=None, phase="idle",
            pending_until=None, lease_until=None, recovery_required=True,
            last_observed_db_time=highwater or now))

    def _step(self, host: str, action: str, token: str,
              permit: PostgresPermit | None = None) -> tuple[str, float, str, datetime | None]:
        """Commit invalidations before reporting refusal; ambiguous commits never grant."""
        try:
            with self.engine.begin() as connection:
                connection.execute(text("SET LOCAL statement_timeout = '500ms'"))
                connection.execute(text("SET LOCAL lock_timeout = '200ms'"))
                if action == "claim":
                    now, started = self._clock(connection)
                    connection.execute(pg_insert(EmployerHostPacing).values(
                        host=host, authority=uuid.uuid4().hex, server_started_at=started,
                        phase="idle", recovery_required=True, last_observed_db_time=now,
                    ).on_conflict_do_nothing(index_elements=[EmployerHostPacing.host]))
                row = connection.execute(select(EmployerHostPacing.__table__).where(
                    EmployerHostPacing.host == host).with_for_update()).mappings().one_or_none()
                if row is None:
                    return "lost", 0, "", None
                # Observe after locking: a preceding holder cannot leave a newer
                # timestamp behind the current observation due to lock waiting.
                now, started = self._clock(connection)
                if not TOKEN.fullmatch(row["authority"]):
                    raise PacingError("host_pacing_configuration_invalid")
                if row["server_started_at"] != started:
                    self._invalidate(connection, host, now, started)
                    return "reset", 0, "", None
                if now < row["last_observed_db_time"]:
                    self._invalidate(connection, host, now, started,
                                     highwater=row["last_observed_db_time"])
                    return "clock", 0, "", None
                connection.execute(update(EmployerHostPacing).where(
                    EmployerHostPacing.host == host).values(last_observed_db_time=now))
                if row["phase"] != "idle":
                    expiry = row["pending_until"] if row["phase"] == "waiting" else row["lease_until"]
                    if expiry is None or expiry <= now:
                        self._invalidate(connection, host, now, started)
                        return "reset", 0, "", None
                if action == "claim":
                    if row["phase"] != "idle":
                        return "busy", 0.1, "", None
                    delay = self.recovery if row["recovery_required"] else self.cooldown
                    connection.execute(update(EmployerHostPacing).where(
                        EmployerHostPacing.host == host).values(
                            phase="waiting", owner_token=token, lease_until=None,
                            pending_until=now + timedelta(seconds=delay + PENDING_MARGIN_SECONDS)))
                    return "waiting", delay, row["authority"], started
                if (permit is None or row["authority"] != permit.authority
                        or started != permit.server_started_at or row["owner_token"] != token):
                    return "lost", 0, "", None
                if action == "grant" and row["phase"] == "waiting":
                    connection.execute(update(EmployerHostPacing).where(
                        EmployerHostPacing.host == host).values(
                            phase="held", pending_until=None,
                            lease_until=now + timedelta(seconds=self.lease), recovery_required=False))
                    return "granted", 0, row["authority"], started
                if row["phase"] != "held":
                    return "lost", 0, "", None
                if action == "verify":
                    if (row["lease_until"] - now).total_seconds() < self.minimum:
                        return "lost", 0, "", None
                    return "verified", 0, row["authority"], started
                if action == "release":
                    connection.execute(update(EmployerHostPacing).where(
                        EmployerHostPacing.host == host).values(
                            phase="idle", owner_token=None, lease_until=None, pending_until=None))
                    return "released", 0, row["authority"], started
                return "lost", 0, "", None
        except PacingError:
            raise
        except (SQLAlchemyError, OSError, ValueError, TypeError, KeyError):
            raise PacingError("host_pacing_unavailable") from None

    @staticmethod
    def _host(host: str) -> None:
        if host not in HOSTS:
            raise PacingError("host_pacing_invalid_host")

    def acquire(self, host: str, deadline: float) -> Permit:
        self._host(host)
        deadline = min(deadline, time.monotonic() + self.wait)
        token = uuid.uuid4().hex
        while time.monotonic() < deadline:
            status, delay, authority, started = self._step(host, "claim", token)
            if status == "clock":
                raise PacingError("host_pacing_clock_rollback")
            if status == "waiting":
                # Start after the committed claim response, so SQL latency or a
                # DB wall-clock jump cannot shorten cooldown/recovery.
                not_before = time.monotonic() + delay
                if not_before >= deadline:
                    raise PacingError("host_pacing_wait_timeout")
                time.sleep(delay)
                grant_start = time.monotonic()
                if grant_start < not_before or grant_start >= deadline or started is None:
                    raise PacingError("host_pacing_wait_timeout")
                permit = PostgresPermit(host, token, authority, started,
                                        grant_start + self.lease - self.minimum)
                status, _, _, _ = self._step(host, "grant", token, permit)
                if status != "granted":
                    raise PacingError("host_pacing_ownership_lost")
                if time.monotonic() >= min(deadline, permit.latest_dispatch):
                    raise PacingError("host_pacing_wait_timeout")
                return permit
            time.sleep(min(delay or .01, max(0, deadline - time.monotonic())))
        raise PacingError("host_pacing_wait_timeout")

    def verify(self, permit: Permit) -> None:
        self._host(permit.host)
        if not isinstance(permit, PostgresPermit) or time.monotonic() >= permit.latest_dispatch:
            raise PacingError("host_pacing_ownership_lost")
        status, _, _, _ = self._step(permit.host, "verify", permit.token, permit)
        if status != "verified" or time.monotonic() >= permit.latest_dispatch:
            raise PacingError("host_pacing_ownership_lost")

    def release(self, permit: Permit) -> None:
        self._host(permit.host)
        if not isinstance(permit, PostgresPermit):
            raise PacingError("host_pacing_ownership_lost")
        if self._step(permit.host, "release", permit.token, permit)[0] != "released":
            raise PacingError("host_pacing_ownership_lost")

    def close(self) -> None:
        # This coordinator owns no pooled connection or long-lived transaction.
        pass
