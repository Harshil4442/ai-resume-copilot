"""Small authenticated control requests; source bytes never cross this route."""
from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.routing import APIRoute
from sqlalchemy.orm import Session

from .. import models
from ..database import get_db
from ..domains.resume_uploads import service
from ..domains.resume_uploads.schemas import SourceAccess, UploadCreate, UploadIntent, UploadStatus
from ..domains.resume_uploads.storage import GCSObjectStore, ObjectStore, StorageUnavailable
from ..security import get_current_user


class BoundedControlRoute(APIRoute):
    def get_route_handler(self):
        handler = super().get_route_handler()
        async def bounded(request: Request):
            if request.method == "POST":
                if request.headers.get("content-encoding", "identity") != "identity":
                    raise HTTPException(400, {"code": "control_body_invalid"})
                body = bytearray()
                try:
                    async with asyncio.timeout(5):
                        async for chunk in request.stream():
                            if len(body) + len(chunk) > 4096:
                                raise HTTPException(413, {"code": "control_body_too_large"})
                            body.extend(chunk)
                except TimeoutError:
                    raise HTTPException(408, {"code": "control_body_timeout"}) from None
                request._body = bytes(body)
            response = await handler(request)
            response.headers["Cache-Control"] = "private, no-store"
            return response
        return bounded


def storage() -> ObjectStore:
    service.enabled()
    try:
        return GCSObjectStore()
    except StorageUnavailable:
        raise HTTPException(503, {"code": "direct_upload_unavailable"}) from None


router = APIRouter(prefix="/resume", tags=["resume"], route_class=BoundedControlRoute)


@router.post("/uploads", response_model=UploadIntent)
def create(body: UploadCreate, idempotency_key: str = Header("", max_length=128),
           db: Session = Depends(get_db), owner: models.User = Depends(get_current_user), store: ObjectStore = Depends(storage)):
    return service.create(db, owner.id, body, idempotency_key, store)


@router.get("/uploads/{upload_id}", response_model=UploadStatus)
def status(upload_id: str, db: Session = Depends(get_db), owner: models.User = Depends(get_current_user)):
    return service.status(db, owner.id, upload_id)


@router.post("/uploads/{upload_id}/complete", response_model=UploadStatus)
def complete(upload_id: str, db: Session = Depends(get_db), owner: models.User = Depends(get_current_user)):
    return service.complete(db, owner.id, upload_id)


@router.delete("/uploads/{upload_id}", response_model=UploadStatus)
def cancel(upload_id: str, db: Session = Depends(get_db), owner: models.User = Depends(get_current_user)):
    return service.cancel(db, owner.id, upload_id)


@router.get("/{resume_id}/source-access", response_model=SourceAccess)
def source_access(resume_id: int, db: Session = Depends(get_db), owner: models.User = Depends(get_current_user), store: ObjectStore = Depends(storage)):
    return service.source_access(db, owner.id, resume_id, store)
