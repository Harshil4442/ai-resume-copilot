"""Exact physical request wrapper for private candidate transport only."""

from __future__ import annotations

from fastapi import HTTPException, Request

from ..domains.candidate_ingress import replay
from ..domains.candidate_ingress.contracts import CANONICAL_OPERATIONS, HEADER, MAX_HEADER_BYTES
from ..domains.recovery.store import GuardDenied, GuardUnavailable

_CHECKED = object()


def require_candidate_ingress(request: Request) -> None:
    if getattr(request.state, "_private_candidate_ingress", None) is _CHECKED:
        return
    try:
        raw_path = request.scope.get("raw_path")
        if (
            request.scope.get("scheme") != "https"
            or type(raw_path) is not bytes
            or request.scope.get("query_string")
            or request.scope.get("root_path")
            or (request.method, raw_path.decode("ascii")) not in CANONICAL_OPERATIONS
            or request.scope.get("path") != raw_path.decode("ascii")
        ):
            raise GuardDenied("Private candidate transport authentication failed")
        values = request.headers.getlist(HEADER)
        authorization = request.headers.getlist("authorization")
        raw = getattr(request.state, "_bounded_sensitive_body", None)
        if (
            len(values) != 1
            or not 0 < len(values[0]) <= MAX_HEADER_BYTES
            or len(authorization) > 1
            or type(raw) is not bytes
            or len(raw) > 8192
        ):
            raise GuardDenied("Private candidate transport authentication failed")
        bearer = authorization[0].encode("utf8") if authorization else b""
        if len(bearer) > 8192:
            raise GuardDenied("Private candidate transport authentication failed")
        replay.production_candidate_ingress().authorize(
            values[0], method=request.method, path=raw_path, raw=raw, authorization=bearer
        )
        request.state._private_candidate_ingress = _CHECKED
    except GuardUnavailable:
        raise HTTPException(
            503,
            "Private candidate transport is unavailable",
            headers={"Cache-Control": "private, no-store", "Pragma": "no-cache"},
        ) from None
    except (GuardDenied, UnicodeError, ValueError):
        raise HTTPException(
            403,
            "Private candidate transport authentication failed",
            headers={"Cache-Control": "private, no-store", "Pragma": "no-cache"},
        ) from None


def native_bearer_requires_private_ingress(request: Request) -> bool:
    """Classify a signed native bearer before deletion's native dependencies."""
    from jose import JWTError, jwt

    from ..security import JWT_ALGORITHM, JWT_SECRET

    value = request.headers.get("authorization", "")
    scheme, _, token = value.partition(" ")
    if scheme.lower() != "bearer" or not token:
        return False
    try:
        claims = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        return "candidate_lifetime" in claims
    except JWTError:
        return False  # ordinary auth still rejects; this creates no legacy grant
