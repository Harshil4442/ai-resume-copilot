import os
import sys
from collections.abc import Callable
from typing import Any

from fastapi import HTTPException, Request
from limits.errors import StorageError
from redis.backoff import NoBackoff
from redis.exceptions import RedisError
from redis.retry import Retry
from slowapi import Limiter
from slowapi.util import get_remote_address

_SIGN_IN_PATHS = frozenset({"/api/auth/login", "/api/auth/google-login"})


class AuthDependencyUnavailable(HTTPException):
    def __init__(self) -> None:
        super().__init__(
            status_code=503,
            detail={
                "code": "AUTH_DEPENDENCY_UNAVAILABLE",
                "message": "Sign-in is temporarily unavailable. Please try again later.",
            },
            headers={"Retry-After": "30", "Cache-Control": "private, no-store"},
        )


class AuthDependencyLimiter(Limiter):
    def _check_request_limit(
        self,
        request: Request,
        endpoint_func: Callable[..., object] | None,
        in_middleware: bool = True,
    ) -> None:
        try:
            super()._check_request_limit(request, endpoint_func, in_middleware)
        except (RedisError, StorageError):
            if request.method == "POST" and request.url.path in _SIGN_IN_PATHS:
                # Admission remains denied. Never continue the auth handler or
                # replace unavailable shared storage with per-instance counters.
                raise AuthDependencyUnavailable() from None
            raise


# Shared rate admission scales across API instances; ledger state remains in SQL.
# Tests keep isolated in-memory storage and never reach a production Redis.
storage_uri = "memory://" if "pytest" in sys.modules else (
    os.getenv("RATE_LIMIT_STORAGE_URL") or os.getenv("REDIS_URL") or "memory://"
)
storage_options: dict[str, Any] = ({
    "socket_connect_timeout": 1.0, "socket_timeout": 1.0,
    "retry": Retry(NoBackoff(), 0), "retry_on_timeout": False,
} if storage_uri.startswith(("redis://", "rediss://")) else {})
limiter = AuthDependencyLimiter(key_func=get_remote_address, storage_uri=storage_uri,
    storage_options=storage_options)
