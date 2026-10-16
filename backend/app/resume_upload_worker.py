"""Default-disabled, private direct-resume scan/cleanup transport.

Scheduler headers are not authentication. Google verifies signature/time/audience;
fixed workload issuer, account email and native account subject are also bound.
"""
from __future__ import annotations

import asyncio
import os
import re
import ssl
from dataclasses import asdict, dataclass
from urllib.parse import urlsplit

import certifi
from fastapi import FastAPI, HTTPException, Request
from starlette.concurrency import run_in_threadpool

from .services.resume_upload_coordinator import CoordinatorUnavailable, Mode, enabled, execute

app = FastAPI(title="Resume Upload Coordinator", docs_url=None, redoc_url=None, openapi_url=None)


@dataclass(frozen=True)
class CallerConfig:
    audience: str
    email: str
    subject: str


def caller_config() -> CallerConfig:
    try:
        project = os.getenv("GOOGLE_CLOUD_PROJECT", "")
        audience = os.getenv("RESUME_UPLOAD_COORDINATOR_AUDIENCE", "")
        email = os.getenv("RESUME_UPLOAD_COORDINATOR_CALLER_EMAIL", "")
        subject = os.getenv("RESUME_UPLOAD_COORDINATOR_CALLER_SUBJECT", "")
        target = urlsplit(audience)
        if (not re.fullmatch(r"[a-z][a-z0-9-]{4,28}[a-z0-9]", project)
                or len(audience) > 512 or target.scheme != "https"
                or not re.fullmatch(r"[a-z0-9-]+(?:\.[a-z0-9-]+)*\.run\.app", target.hostname or "")
                or target.username is not None or target.password is not None
                or target.port is not None or target.path or target.query or target.fragment
                or not re.fullmatch(r"[a-z][a-z0-9-]{4,28}@" + re.escape(project) + r"\.iam\.gserviceaccount\.com", email)
                or not re.fullmatch(r"[1-9][0-9]{20}", subject)):
            raise ValueError
        return CallerConfig(audience, email, subject)
    except Exception:
        raise HTTPException(503, "resume_coordinator_configuration_unavailable") from None


def _verified_claims(token: str, audience: str) -> dict:
    import requests
    from google.auth.transport.requests import Request as GoogleRequest
    from google.oauth2.id_token import verify_oauth2_token

    class TLSAdapter(requests.adapters.HTTPAdapter):
        def init_poolmanager(self, *args, **kwargs):
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
            context.load_verify_locations(cafile=certifi.where())
            kwargs["ssl_context"] = context
            return super().init_poolmanager(*args, **kwargs)

    # Reuse Google's ID-token verifier and the private document client's
    # explicit-trust transport pattern; no issuer selection or local JWT fallback.
    with requests.Session() as session:
        session.trust_env = False
        session.verify = certifi.where()
        session.mount("https://", TLSAdapter())
        transport = GoogleRequest(session=session)
        def bounded_request(*args, **kwargs):
            if args and args[0] != "https://www.googleapis.com/oauth2/v1/certs":
                raise ValueError
            if kwargs.get("url", "https://www.googleapis.com/oauth2/v1/certs") != "https://www.googleapis.com/oauth2/v1/certs":
                raise ValueError
            kwargs["timeout"] = 5
            kwargs["allow_redirects"] = False
            return transport(*args, **kwargs)
        return dict(verify_oauth2_token(token, bounded_request, audience=audience))


def authenticate(request: Request, config: CallerConfig) -> None:
    headers = request.headers.getlist("authorization")
    if len(headers) != 1 or not re.fullmatch(r"Bearer [A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+", headers[0]) or len(headers[0]) > 16 * 1024:
        raise HTTPException(401, "resume_coordinator_auth_required")
    try:
        claims = _verified_claims(headers[0][7:], config.audience)
    except Exception:
        raise HTTPException(401, "resume_coordinator_auth_required") from None
    if (claims.get("iss") not in {"accounts.google.com", "https://accounts.google.com"}
            or claims.get("aud") != config.audience or claims.get("email") != config.email
            or claims.get("sub") != config.subject or claims.get("email_verified") is not True):
        raise HTTPException(403, "resume_coordinator_caller_refused")


async def _tick(request: Request, mode: Mode) -> dict:
    if not enabled(mode):
        raise HTTPException(503, "resume_coordinator_disabled")
    if request.url.query:
        raise HTTPException(400, "resume_coordinator_body_refused")
    config = caller_config()
    await run_in_threadpool(authenticate, request, config)
    # No filename/content/IDs/signed URL in coordinator requests. Read at most a
    # first nonempty chunk, under an absolute empty-body deadline.
    async def empty_body():
        async for chunk in request.stream():
            if chunk:
                raise HTTPException(400, "resume_coordinator_body_refused")
    try:
        await asyncio.wait_for(empty_body(), timeout=5)
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(400, "resume_coordinator_body_refused") from None
    try:
        return asdict(await run_in_threadpool(execute, mode))
    except CoordinatorUnavailable:
        raise HTTPException(503, "resume_coordinator_unavailable") from None


@app.middleware("http")
async def private_response(request: Request, call_next):
    response = await call_next(request)
    response.headers["Cache-Control"] = "no-store"
    return response


@app.get("/api/health")
def health():
    return {"ok": True, "role": "resume-upload-coordinator",
            "scan_enabled": enabled("scan"), "cleanup_enabled": enabled("cleanup")}


@app.post("/internal/resume-uploads/scan")
async def scan(request: Request):
    return await _tick(request, "scan")


@app.post("/internal/resume-uploads/cleanup")
async def cleanup(request: Request):
    return await _tick(request, "cleanup")
