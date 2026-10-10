"""Required coordination for bounded, unauthenticated Lever API GETs.

One fixed API host owns one lease, across tenants and worker processes. Redis
server time owns cooldowns; missing state or a new server incarnation quarantines
the host rather than trusting an old permit. This is pacing, not a claim that a
lease can fence physical HTTP dispatch after arbitrary process suspension.
"""
from __future__ import annotations

import os
import threading
import time
import uuid
from dataclasses import dataclass
from typing import Protocol
from urllib.parse import urlsplit

import redis
from redis.backoff import NoBackoff
from redis.retry import Retry

HOSTS = frozenset({"api.lever.co", "api.eu.lever.co"})
REQUEST_SECONDS = 20.0
CLOSE_SECONDS = 2.0
LEASE_SECONDS = 30.0
COOLDOWN_SECONDS = 1.0
WAIT_SECONDS = 30.0
KEY_PREFIX = "hirewiz:employer:host-pacing:v1:"

# INFO/TIME and the state transition run atomically. A restarted/promoted Redis
# incarnation cannot grant an old persisted lease. Keys deliberately have no TTL.
_GATE = r"""
local server = redis.call('INFO', 'server')
local memory = redis.call('INFO', 'memory')
local incarnation = string.match(server, 'run_id:([a-zA-Z0-9]+)')
if not incarnation or not string.find(memory, 'maxmemory_policy:noeviction\r\n', 1, true) then
  return {-3, 0}
end
local tm = redis.call('TIME')
local now = tonumber(tm[1]) * 1000 + math.floor(tonumber(tm[2]) / 1000)
local key, action, token = KEYS[1], ARGV[1], ARGV[2]
local lease, cooldown, quarantine, minimum = tonumber(ARGV[3]), tonumber(ARGV[4]), tonumber(ARGV[5]), tonumber(ARGV[6])
if redis.call('HGET', key, 'incarnation') ~= incarnation then
  redis.call('HSET', key, 'incarnation', incarnation, 'owner', '', 'lease_until', 0, 'next', now + quarantine)
  return {0, quarantine}
end
local owner = redis.call('HGET', key, 'owner') or ''
local expires = tonumber(redis.call('HGET', key, 'lease_until')) or 0
local next_at = tonumber(redis.call('HGET', key, 'next')) or 0
if owner ~= '' and expires <= now then
  redis.call('HSET', key, 'owner', '', 'lease_until', 0, 'next', now + quarantine)
  return {0, quarantine}
end
if action == 'verify' then
  if owner == token and expires - now >= minimum then return {1, expires - now} end
  return {-1, 0}
end
if action == 'release' then
  if owner ~= token then return {-1, 0} end
  redis.call('HSET', key, 'owner', '', 'lease_until', 0, 'next', now + cooldown)
  return {1, cooldown}
end
if owner ~= '' then return {0, expires - now} end
if next_at > now then return {0, next_at - now} end
redis.call('HSET', key, 'owner', token, 'lease_until', now + lease)
return {1, lease}
"""


class PacingError(ValueError):
    pass


class Coordinator(Protocol):
    def acquire(self, host: str, deadline: float) -> Permit: ...
    def verify(self, permit: Permit) -> None: ...
    def release(self, permit: Permit) -> None: ...
    def close(self) -> None: ...


@dataclass(frozen=True)
class Permit:
    host: str
    token: str


class RedisCoordinator:
    def __init__(self, client: redis.Redis, *, prefix: str = KEY_PREFIX,
                 lease_seconds: float = LEASE_SECONDS,
                 cooldown_seconds: float = COOLDOWN_SECONDS,
                 request_seconds: float = REQUEST_SECONDS,
                 close_seconds: float = CLOSE_SECONDS):
        self.client = client
        self.prefix = prefix
        self.lease_ms = int(lease_seconds * 1000)
        self.cooldown_ms = int(cooldown_seconds * 1000)
        # State loss waits out one bounded in-flight request plus its cooldown.
        self.minimum_ms = int((request_seconds + close_seconds + cooldown_seconds) * 1000)
        if self.lease_ms <= self.minimum_ms or self.cooldown_ms <= 0:
            raise ValueError("lease must exceed the request deadline and cooldown")

    def _gate(self, action: str, permit: Permit) -> tuple[int, int]:
        if permit.host not in HOSTS:
            raise PacingError("host_pacing_invalid_host")
        try:
            result = self.client.eval(_GATE, 1, self.prefix + permit.host, action,
                                      permit.token, self.lease_ms, self.cooldown_ms,
                                      self.minimum_ms, self.minimum_ms)
            status, delay = int(result[0]), int(result[1])
        except (redis.RedisError, OSError, ValueError, TypeError, IndexError) as exc:
            raise PacingError("host_pacing_unavailable") from exc
        if status == -3:
            raise PacingError("host_pacing_configuration_invalid")
        return status, delay

    def acquire(self, host: str, deadline: float) -> Permit:
        permit = Permit(host, uuid.uuid4().hex)
        deadline = min(deadline, time.monotonic() + WAIT_SECONDS)
        while time.monotonic() < deadline:
            status, delay = self._gate("acquire", permit)
            # A slow/ambiguous coordinator call must not grant after our budget.
            if time.monotonic() >= deadline:
                raise PacingError("host_pacing_wait_timeout")
            if status == 1:
                return permit
            time.sleep(min(max(delay / 1000, 0.001), 1.0, deadline - time.monotonic()))
        raise PacingError("host_pacing_wait_timeout")

    def verify(self, permit: Permit) -> None:
        if self._gate("verify", permit)[0] != 1:
            raise PacingError("host_pacing_ownership_lost")

    def release(self, permit: Permit) -> None:
        if self._gate("release", permit)[0] != 1:
            raise PacingError("host_pacing_ownership_lost")

    def close(self) -> None:
        self.client.close()


class LocalCoordinator:
    """Explicit test/development only: thread coordination, never distributed."""

    def __init__(self, *, lease_seconds: float = LEASE_SECONDS,
                 cooldown_seconds: float = COOLDOWN_SECONDS,
                 request_seconds: float = REQUEST_SECONDS,
                 close_seconds: float = CLOSE_SECONDS):
        self.lease = lease_seconds
        self.cooldown = cooldown_seconds
        self.minimum = request_seconds + close_seconds + cooldown_seconds
        self.lock = threading.Lock()
        self.state: dict[str, tuple[str, float, float]] = {}

    def _gate(self, action: str, permit: Permit) -> tuple[int, float]:
        if permit.host not in HOSTS:
            raise PacingError("host_pacing_invalid_host")
        with self.lock:
            now = time.monotonic()
            owner, expires, next_at = self.state.get(permit.host, ("", 0, 0))
            if owner and expires <= now:
                self.state[permit.host] = ("", 0, now + self.minimum)
                return 0, self.minimum
            if action == "verify":
                return (1, expires - now) if owner == permit.token and expires - now >= self.minimum else (-1, 0)
            if action == "release":
                if owner != permit.token:
                    return -1, 0
                self.state[permit.host] = ("", 0, now + self.cooldown)
                return 1, self.cooldown
            if owner:
                return 0, expires - now
            if next_at > now:
                return 0, next_at - now
            self.state[permit.host] = (permit.token, now + self.lease, 0)
            return 1, self.lease

    def acquire(self, host: str, deadline: float) -> Permit:
        permit = Permit(host, uuid.uuid4().hex)
        deadline = min(deadline, time.monotonic() + WAIT_SECONDS)
        while time.monotonic() < deadline:
            status, delay = self._gate("acquire", permit)
            if status == 1:
                return permit
            time.sleep(min(delay, 1.0, max(0, deadline - time.monotonic())))
        raise PacingError("host_pacing_wait_timeout")

    def verify(self, permit: Permit) -> None:
        if self._gate("verify", permit)[0] != 1:
            raise PacingError("host_pacing_ownership_lost")

    def release(self, permit: Permit) -> None:
        if self._gate("release", permit)[0] != 1:
            raise PacingError("host_pacing_ownership_lost")

    def close(self) -> None:
        pass


_local = LocalCoordinator()


def coordinator() -> Coordinator:
    mode = os.getenv("EMPLOYER_HOST_PACING_MODE", "redis").strip().lower()
    environment = os.getenv("APP_ENV", "production").strip().lower()
    if mode == "local" and environment in {"local", "development", "test"}:
        return _local
    if mode != "redis":
        raise PacingError("host_pacing_configuration_invalid")
    # A dedicated coordination authority avoids changing global cache/limiter
    # behavior just to enable a feed. Explicit configuration never falls back
    # to another authority when it is malformed or unavailable.
    url = (os.getenv("EMPLOYER_HOST_PACING_REDIS_URL", "").strip()
           or os.getenv("REDIS_URL", "").strip()
           or os.getenv("RATE_LIMIT_STORAGE_URL", "").strip())
    try:
        scheme = urlsplit(url).scheme
    except ValueError as exc:
        raise PacingError("host_pacing_configuration_invalid") from exc
    if scheme not in {"redis", "rediss"}:
        raise PacingError("host_pacing_unavailable")
    try:
        client = redis.Redis.from_url(url, decode_responses=True, socket_timeout=0.5,
                                      socket_connect_timeout=0.5, retry=Retry(NoBackoff(), 0),
                                      retry_on_timeout=False)
    except (ValueError, redis.RedisError) as exc:
        raise PacingError("host_pacing_unavailable") from exc
    return RedisCoordinator(client)
