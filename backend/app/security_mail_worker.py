"""Private Cloud Tasks receiver for committed password-reset and erasure links."""
from __future__ import annotations

import os
import re
from typing import Literal, cast

from fastapi import FastAPI, HTTPException, Request, Response
from starlette.concurrency import run_in_threadpool

from .resume_upload_worker import CallerConfig, _caller_config, _require_empty_body, authenticate

DeliveryStatus = Literal["sent", "finished", "retry", "unavailable"]
WORKER = "security-mail-worker"
ROUTE = "/internal/security-mail/wake"
app = FastAPI(title="Security Mail Worker", docs_url=None, redoc_url=None, openapi_url=None, redirect_slashes=False)


def caller_config() -> CallerConfig:
    try:
        config = _caller_config("SECURITY_MAIL_WORKER")
        project = os.getenv("GOOGLE_CLOUD_PROJECT", "")
        if (
            config.email == f"hirewiz-security-mail-task@{project}.iam.gserviceaccount.com"
            and "---" not in config.audience
            and re.fullmatch(
                r"https://hirewiz-security-mail-worker-[a-z0-9-]+(?:\.[a-z]+-[a-z]+[1-9][0-9]*)?\.run\.app",
                config.audience,
            )
        ):
            return config
    except HTTPException:
        pass
    raise HTTPException(503, "security_mail_configuration_unavailable") from None


def execute(notification_id: str) -> DeliveryStatus:
    from .database import SessionLocal
    from .domains.notifications.service import retry_security_email

    with SessionLocal() as db:
        return retry_security_email(db, notification_id, link_only=True).status


@app.middleware("http")
async def private_response(request: Request, call_next) -> Response:
    response = await call_next(request)
    response.headers["Cache-Control"] = "private, no-store"
    return cast(Response, response)


@app.get("/api/health")
def health() -> dict[str, object]:
    return {"ok": True, "role": WORKER}


@app.post(ROUTE + "/{notification_id}")
async def deliver(request: Request, notification_id: str) -> Response:
    if os.getenv("SERVICE_ROLE") != WORKER or os.getenv("SECURITY_MAIL_WORKER_ENABLED") != "true":
        raise HTTPException(503, "security_mail_wake_disabled")
    if not re.fullmatch(r"ntf_[a-f0-9]{32}", notification_id):
        raise HTTPException(400, "security_mail_wake_refused")
    expected_path = f"{ROUTE}/{notification_id}".encode("ascii")
    if request.url.query or request.scope.get("raw_path") != expected_path:
        raise HTTPException(400, "security_mail_wake_refused")
    try:
        await run_in_threadpool(authenticate, request, caller_config())
    except HTTPException as error:
        raise HTTPException(error.status_code, "security_mail_caller_refused") from None
    try:
        await _require_empty_body(request)
    except HTTPException:
        raise HTTPException(400, "security_mail_wake_refused") from None
    try:
        outcome = await run_in_threadpool(execute, notification_id)
    except Exception:
        # Exception text can contain data from the database or mail provider.
        raise HTTPException(503, "security_mail_wake_unavailable") from None
    if outcome in {"sent", "finished"}:
        return Response(status_code=204)
    if outcome == "retry":
        return Response(status_code=409)
    raise HTTPException(503, "security_mail_wake_unavailable")
