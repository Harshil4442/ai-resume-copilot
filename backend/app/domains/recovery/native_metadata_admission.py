"""Shared finite RPC admission; never an identity cache or authorization grant."""
from __future__ import annotations

import math
import os
import re

from limits.storage import RedisStorage
from redis.connection import SSLConnection
from redis.retry import Retry

from .native_diagnostics import native_phase
from .store import GuardUnavailable

MAX_CALLS_PER_MINUTE = 40
CAPACITY_RETRY_SECONDS = 60
_PROJECT = re.compile(r"[a-z][a-z0-9-]{4,28}[a-z0-9]\Z")


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
            # One atomic shared moving-window acquisition for one actual RPC.
            # No user, browser, IP or request-selected namespace can split it.
            # Use Redis time so API instances cannot split the window through
            # different local clocks. This is the installed limits Lua script;
            # its normal wrapper coerces unknown replies with bool(), so retain
            # and strictly check the actual integer acknowledgement here.
            clock = storage.storage.time()
            if (type(clock) not in (tuple, list) or len(clock) != 2
                    or any(type(part) is not int for part in clock)
                    or not 0 < clock[0] < 2**53 or not 0 <= clock[1] < 1_000_000):
                raise MetadataAdmissionUnavailable()
            timestamp = clock[0] + clock[1] / 1_000_000
            # Deliberately independent of budget, user, IP and method: mixed
            # lower budgets still consume the same project-wide counter.
            key = storage.prefixed_key("hirewiz-native-metadata-v1/" + project)
            acquired = storage.lua_acquire_moving_window([key], [timestamp, int(amount), 60, 1])
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
