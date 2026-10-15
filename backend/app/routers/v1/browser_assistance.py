"""Bounded OAuth candidate review and device-key transport, separate from native experiments."""
import asyncio
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from pydantic import ValidationError
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from ... import models
from ...database import get_db
from ...domains.browser_assistance import schemas, service
from ...rate_limiter import limiter
from ...security import get_current_user

router = APIRouter(prefix="/browser-assistance", tags=["browser-assistance"])
HEADERS = {"Cache-Control": "private, no-store", "Pragma": "no-cache", "X-Content-Type-Options": "nosniff"}


@router.get("/availability")
def availability(user: models.User = Depends(get_current_user)):
    return JSONResponse(service.availability(), headers=HEADERS)


@router.post("/candidate/{operation}")
def candidate(operation: Literal["device", "confirm", "revoke", "review", "revise", "approve", "cancel"],
              payload: schemas.CandidateRequest, db: Session = Depends(get_db),
              user: models.User = Depends(get_current_user)):
    return JSONResponse(service.candidate(db, user.id, operation, payload), headers=HEADERS)


@router.post("/device/{operation}")
@limiter.limit("60/minute")
async def device(request: Request, operation: Literal["prepare", "status", "application", "inspect", "begin", "artifact", "outcome", "cancel"],
                 db: Session = Depends(get_db)):
    if (request.headers.get("content-type") != "application/json" or request.headers.get("content-encoding")
            or request.headers.get("authorization") or request.headers.get("cookie") or request.url.query):
        raise HTTPException(403, service.ERROR, headers=HEADERS)
    raw = bytearray()
    try:
        async with asyncio.timeout(3):
            async for chunk in request.stream():
                raw.extend(chunk)
                if len(raw) > 65_536:
                    raise HTTPException(413, service.ERROR, headers=HEADERS)
        payload = schemas.DeviceRequest.model_validate_json(bytes(raw))
        service.require(operation == payload.operation, 403)
        result = await run_in_threadpool(service.device, db, request.headers.get("origin", ""), payload)
    except HTTPException as exc:
        db.rollback()
        exc.headers = {**(exc.headers or {}), **HEADERS}
        raise
    except (ValidationError, ValueError):
        db.rollback()
        return JSONResponse({"detail": service.ERROR}, status_code=422, headers=HEADERS)
    except Exception:
        db.rollback()
        return JSONResponse({"detail": service.ERROR}, status_code=503, headers=HEADERS)
    if isinstance(result, service.attachments.BinaryDelivery):
        return Response(result.content, media_type=result.descriptor["media_type"], headers={
            **HEADERS, "X-Artifact-SHA256": result.descriptor["sha256"],
            "X-Artifact-Filename": result.descriptor["filename"], "Content-Length": str(len(result.content))})
    return JSONResponse(result, headers=HEADERS)
