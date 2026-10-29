"""Shared finite RPC admission; never an identity cache or authorization grant."""
from __future__ import annotations

import math
import os
import re
from hashlib import sha256

from limits.storage import RedisStorage
from redis.connection import SSLConnection
from redis.retry import Retry

from .native_diagnostics import native_phase
from .store import GuardUnavailable

MAX_CALLS_PER_MINUTE = 40
CAPACITY_RETRY_SECONDS = 60
_PROJECT = re.compile(r"[a-z][a-z0-9-]{4,28}[a-z0-9]\Z")
_INSTALLED_WINDOW_SHA256 = "573d07908fdb4852d6ebaf25115c3ca5a2a54cb12e5388d99357e9752bb3df38"


def _atomic_window_script() -> bytes:
    """Keep the locked limits algorithm, with one atomic server clock and 0/1 ack."""
    body = RedisStorage.SCRIPT_ACQUIRE_MOVING_WINDOW
    if type(body) is not bytes or sha256(body).hexdigest() != _INSTALLED_WINDOW_SHA256:
        raise MetadataAdmissionUnavailable()
    return b"""local clock = redis.call('TIME')
local seconds = tonumber(clock[1])
local micros = tonumber(clock[2])
if not seconds or not micros or seconds <= 0 or seconds >= 9007199254740992
    or micros < 0 or micros >= 1000000 then
    return redis.error_reply('Native metadata clock unavailable')
end
local function acquire()
    local ARGV = {seconds + micros / 1000000, ARGV[1], 60, 1}
""" + body + b"""
end
local result = acquire()
if result == true then return 1 end
if result == false then return 0 end
return redis.error_reply('Native metadata acknowledgement unavailable')
"""


def _acquire(storage: RedisStorage, key: str, amount: int):
    return storage.storage.register_script(_atomic_window_script())([key], [amount])


class MetadataAdmissionError(GuardUnavailable):
    pass


class MetadataCapacityUnavailable(MetadataAdmissionError):
    def __init__(self):
        super().__init__("Shared native metadata capacity is temporarily allocated")


class MetadataAdmissionUnavailable(MetadataAdmissionError):
    def __init__(self):
        super().__init__("Shared native metadata admission is unavailable")


def require_enabled() -> None:
    if os.getenv("CANDIDATE_METADATA_ADMISSION_ENABLED", "false") != "true":
        raise MetadataAdmissionUnavailable()


def _shared_storage():
    from ...rate_limiter import limiter

    return limiter._storage


def before_metadata_rpc(project: str, *, required: bool = False) -> None:
    mode = os.getenv("CANDIDATE_METADATA_ADMISSION_ENABLED", "false")
    if mode == "false":
        if required:
            raise MetadataAdmissionUnavailable()
        return
    with native_phase("firestore_rpc", "metadata_admission"):
        try:
            amount = os.getenv("CANDIDATE_METADATA_CALLS_PER_MINUTE", "")
            if (mode != "true" or type(project) is not str or not _PROJECT.fullmatch(project)
                    or project != os.getenv("GOOGLE_CLOUD_PROJECT", "") or not re.fullmatch(r"[1-9][0-9]?", amount)
                    or not 1 <= int(amount) <= MAX_CALLS_PER_MINUTE):
                raise MetadataAdmissionUnavailable()
            storage = _shared_storage()
            if type(storage) is not RedisStorage:
                raise MetadataAdmissionUnavailable()
            pool = storage.storage.connection_pool
            options = pool.connection_kwargs
            if (not issubclass(pool.connection_class, SSLConnection)
                    or any(type(options.get(key)) not in (int, float) or not math.isfinite(options[key])
                           or not 0 < options[key] <= 1 for key in ("socket_timeout", "socket_connect_timeout"))
                    or type(options.get("retry")) is not Retry or options["retry"].get_retries() != 0
                    or options.get("retry_on_timeout") is not False
                    or options.get("ssl_cert_reqs", "required") not in ("required", 2)
                    or options.get("ssl_check_hostname", True) is not True):
                raise MetadataAdmissionUnavailable()
            # TIME and the installed moving-window body execute in one Lua
            # invocation. No caller timestamp can become stale while paused.
            # The wrapper turns the body's true/false into exact integer 1/0;
            # RESP2 nil/unknown replies are dependency failures, never grants.
            # Deliberately independent of budget, user, IP and method: mixed
            # lower budgets still consume the same project-wide counter.
            key = storage.prefixed_key("hirewiz-native-metadata-v1/" + project)
            acquired = _acquire(storage, key, int(amount))
            if type(acquired) is not int or acquired not in (0, 1):
                raise MetadataAdmissionUnavailable()
            if acquired == 0:
                raise MetadataCapacityUnavailable()
        except MetadataAdmissionError:
            raise
        except Exception:
            # Unknown Redis acknowledgements are not successful admission.
            # Never retry, use process memory, or include URI/driver text.
            raise MetadataAdmissionUnavailable() from None
