"""Explicit identity-only transport. Legacy bearer authentication grants nothing."""
from __future__ import annotations

import asyncio
from typing import Literal

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ValidationError
from starlette.concurrency import run_in_threadpool

from ...domains.recovery.browser_pairing import (
    MAX_BODY,
    MAX_RESPONSE,
    ChallengeProof,
    CreationProof,
    DeviceLookup,
    DeviceSelection,
    NativeBrowserPairing,
    PairingSelection,
    PrepareRequest,
    UnavailableBrowserPairing,
    production_browser_pairing,
)
from ...domains.recovery.password_reauth import PasswordReauthRequest
from ...domains.recovery.store import GuardDenied, GuardUnavailable

router = APIRouter(prefix="/browser-pairing", tags=["browser-pairing"])
HEADERS = {"Cache-Control": "private, no-store, max-age=0", "Pragma": "no-cache",
           "X-Content-Type-Options": "nosniff"}


def _request_contract(models: tuple[type[BaseModel], ...]) -> dict:
    return {"requestBody": {"required": True, "content": {"application/json": {
        "schema": {"oneOf": [model.model_json_schema() for model in models]}}}}}


def get_browser_pairing() -> NativeBrowserPairing | UnavailableBrowserPairing:
    return production_browser_pairing()


async def _body(request: Request) -> bytes:
    if (request.headers.get("content-type") != "application/json"
            or request.headers.get("content-encoding") is not None
            or request.url.query or request.headers.get("authorization") is not None
            or request.headers.get("cookie") is not None):
        raise GuardDenied("Pairing transport headers were rejected")
    raw = bytearray()
    async with asyncio.timeout(3):
        async for part in request.stream():
            if len(raw) + len(part) > MAX_BODY:
                raise GuardDenied("Pairing transport body exceeded its bound")
            raw.extend(part)
    return bytes(raw)


async def _dispatch(request: Request, operation: str,
                    service: NativeBrowserPairing | UnavailableBrowserPairing, *, candidate: bool):
    try:
        raw = await _body(request)
        if candidate:
            assertion = request.headers.get("x-hirewiz-gateway-assertion", "")
            signature = request.headers.get("x-hirewiz-gateway-signature", "")
            result = await run_in_threadpool(service.candidate, operation, raw, assertion, signature)
        else:
            if (request.headers.get("x-hirewiz-gateway-assertion") is not None
                    or request.headers.get("x-hirewiz-gateway-signature") is not None):
                raise GuardDenied("Candidate assertions are not device credentials")
            result = await run_in_threadpool(service.device, operation, raw, request.headers.get("origin", ""))
        response = JSONResponse(result, headers=HEADERS)
        if len(response.body) > MAX_RESPONSE:
            raise GuardUnavailable("Pairing response exceeded its bound")
        return response
    except (GuardDenied, ValidationError, ValueError, TypeError, KeyError):
        return JSONResponse({"detail": "Browser pairing request was rejected"}, status_code=403, headers=HEADERS)
    except Exception:
        # Private credentials, gateway payloads and native exceptions never enter output/logs.
        return JSONResponse({"detail": "Browser pairing is currently unavailable"}, status_code=503, headers=HEADERS)


@router.post("/candidate/{operation}", openapi_extra=_request_contract((PairingSelection, DeviceSelection, PasswordReauthRequest)))
async def candidate(operation: Literal["challenge", "confirm", "revoke-challenge", "revoke"],
                    request: Request,
                    service: NativeBrowserPairing | UnavailableBrowserPairing = Depends(get_browser_pairing)):
    return await _dispatch(request, operation, service, candidate=True)


@router.post("/device/{operation}", openapi_extra=_request_contract((PrepareRequest, DeviceLookup, CreationProof, ChallengeProof)))
async def device(operation: Literal["prepare", "create", "challenge", "complete", "status", "refresh-challenge", "refresh"],
                 request: Request,
                 service: NativeBrowserPairing | UnavailableBrowserPairing = Depends(get_browser_pairing)):
    return await _dispatch(request, operation, service, candidate=False)
