"""Bound sensitive JSON before FastAPI parsing; never reflect authentication input.

Auth models containing credential fields and explicitly opted-in candidate
lifecycle bodies use this route boundary.
The budget is for bytes retained/read at the application boundary; the ASGI server
still owns HTTP framing and its incoming chunk/header limits.
"""
from __future__ import annotations

import asyncio
import json
import logging
import math
from collections.abc import Callable
from typing import cast

from fastapi import HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from pydantic import BaseModel
from starlette.requests import ClientDisconnect
from starlette.responses import Response

AUTH_JSON_MAX_BYTES = 8_192
AUTH_JSON_READ_DEADLINE_SECONDS = 2.0
AUTH_INPUT_ERROR = "Invalid authentication request"
_CREDENTIAL_FIELDS = frozenset({"password", "current_password", "new_password", "id_token"})
_log = logging.getLogger("ai_resume_copilot.auth")


class _RejectedAuthInput(Exception):
    def __init__(self, status_code: int):
        self.status_code = status_code
        super().__init__(AUTH_INPUT_ERROR)


def _pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise _RejectedAuthInput(422)
        result[key] = value
    return result


def _constant(_: str) -> object:
    raise _RejectedAuthInput(422)


def _float(value: str) -> float:
    parsed = float(value)
    if not math.isfinite(parsed):
        raise _RejectedAuthInput(422)
    return parsed


async def _read_json(request: Request, allowed: frozenset[str]) -> None:
    encoding = request.headers.get("content-encoding", "identity").strip().lower()
    if encoding != "identity":
        raise _RejectedAuthInput(415)
    content_type = request.headers.get("content-type")
    if content_type is not None:
        media = content_type.split(";", 1)[0].strip().lower()
        if not (media == "application/json" or (media.startswith("application/") and media.endswith("+json"))):
            raise _RejectedAuthInput(415)
    lengths = request.headers.getlist("content-length")
    expected: int | None = None
    if lengths:
        if len(lengths) != 1 or len(lengths[0]) > 20 or not lengths[0].isascii() or not lengths[0].isdigit():
            raise _RejectedAuthInput(400)
        expected = int(lengths[0])
        if expected > AUTH_JSON_MAX_BYTES:
            raise _RejectedAuthInput(413)
        if request.headers.get("transfer-encoding") is not None:
            raise _RejectedAuthInput(400)
    body = bytearray()
    try:
        async with asyncio.timeout(AUTH_JSON_READ_DEADLINE_SECONDS):
            async for chunk in request.stream():
                if len(chunk) > AUTH_JSON_MAX_BYTES - len(body):
                    raise _RejectedAuthInput(413)
                body.extend(chunk)
    except TimeoutError:
        raise _RejectedAuthInput(408) from None
    except ClientDisconnect:
        raise _RejectedAuthInput(400) from None
    if expected is not None and len(body) != expected:
        raise _RejectedAuthInput(400)
    raw = bytes(body)
    try:
        parsed = json.loads(raw.decode("utf-8"), object_pairs_hook=_pairs,
                            parse_constant=_constant, parse_float=_float)
    except (ValueError, UnicodeError, RecursionError):
        raise _RejectedAuthInput(422) from None
    if type(parsed) is not dict or not set(parsed).issubset(allowed):
        raise _RejectedAuthInput(422)
    # FastAPI receives exactly the checked, bounded JSON and never rereads the
    # stream or uses its permissive duplicate/nonfinite parser.
    request._body = raw
    request._json = parsed


def _failure(status_code: int) -> JSONResponse:
    return JSONResponse(status_code=status_code, content={"detail": AUTH_INPUT_ERROR},
                        headers={"Cache-Control": "no-store, private", "Pragma": "no-cache"})


class SensitiveAuthRoute(APIRoute):
    bound_auth_body = False

    def get_route_handler(self) -> Callable:
        handler = super().get_route_handler()
        allowed: frozenset[str] = frozenset()
        for field in self.dependant.body_params:
            model = field.field_info.annotation
            if isinstance(model, type) and issubclass(model, BaseModel):
                names = frozenset(cast(type[BaseModel], model).model_fields)
                if names & _CREDENTIAL_FIELDS or self.bound_auth_body:
                    allowed = names
        if not allowed:
            return handler
        # Describe the actual fixed error envelope instead of advertising
        # FastAPI's default validation-error input serialization on these routes.
        for code in (400, 408, 413, 415, 422, 500):
            self.responses[code] = {
                "description": "Authentication input rejected without reflecting private values",
                "content": {"application/json": {"schema": {
                    "type": "object", "additionalProperties": False,
                    "required": ["detail"], "properties": {
                        "detail": {"type": "string", "const": AUTH_INPUT_ERROR}}}}},
            }

        async def sensitive(request: Request) -> Response:
            try:
                await _read_json(request, allowed)
                return await handler(request)
            except _RejectedAuthInput as error:
                return _failure(error.status_code)
            except RequestValidationError:
                # hide_input_in_errors does not sanitize FastAPI errors() JSON.
                # Never serialize, log or stringify this sensitive exception.
                return _failure(422)
            except HTTPException:
                raise  # Existing fixed authentication/rate-limit responses.
            except Exception:
                # Provider/SQL exception text and tracebacks can hold credentials.
                _log.error("Sensitive authentication handler failed")
                return _failure(500)

        return sensitive
