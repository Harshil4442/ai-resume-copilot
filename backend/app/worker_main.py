from __future__ import annotations

import time
import uuid

from fastapi import FastAPI, Request

from .observability import configure_observability, correlation_id_var
from .routers.worker import router as worker_router

configure_observability()

app = FastAPI(
    title="HireWiz Analysis Worker",
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)


@app.middleware("http")
async def worker_request_context(request: Request, call_next):
    incoming = (
        request.headers.get("X-Correlation-ID")
        or request.headers.get("X-CloudTasks-TaskName")
        or ""
    ).strip()
    correlation_id = (
        incoming[-64:]
        if incoming and all(character.isalnum() or character in "-_." for character in incoming[-64:])
        else uuid.uuid4().hex[:16]
    )
    token = correlation_id_var.set(correlation_id)
    started = time.perf_counter()
    try:
        response = await call_next(request)
    finally:
        correlation_id_var.reset(token)
    response.headers["X-Correlation-ID"] = correlation_id
    response.headers["Server-Timing"] = (
        f'worker;dur={(time.perf_counter() - started) * 1000:.1f}'
    )
    response.headers["Cache-Control"] = "no-store"
    return response


@app.get("/api/health")
def health() -> dict[str, object]:
    return {"ok": True, "role": "analysis-worker"}


app.include_router(worker_router)
