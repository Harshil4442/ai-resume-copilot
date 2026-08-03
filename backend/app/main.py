import logging
import os
import sys
import time
import uuid
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .observability import configure_observability, correlation_id_var

configure_observability()

log = logging.getLogger("ai_resume_copilot")

# Optional: load backend/.env for local dev
try:
    from dotenv import load_dotenv
    load_dotenv(dotenv_path=Path(__file__).resolve().parents[1] / ".env", override=False)
except Exception:
    pass

from .routers import auth, resume, jobs, recommendations, llm, analytics, rag, market, billing, public_endpoints  # noqa: E402
from .routers.v1 import router as v1_router  # noqa: E402
from .rate_limiter import limiter
from slowapi.errors import RateLimitExceeded
from slowapi import _rate_limit_exceeded_handler

app = FastAPI(title="HireWiz API")
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# CORS
origins_env = os.getenv("FRONTEND_ORIGINS", "*")
allow_origins = ["*"] if origins_env.strip() == "*" else [o.strip() for o in origins_env.split(",") if o.strip()]
app_env = (os.getenv("APP_ENV") or "production").strip().lower()
is_test_process = bool(os.getenv("PYTEST_CURRENT_TEST")) or "pytest" in sys.modules
if not is_test_process and app_env not in {"development", "dev", "local", "test"} and (
    not allow_origins or allow_origins == ["*"]
):
    raise RuntimeError("FRONTEND_ORIGINS must list explicit HTTPS origins in production.")

app.add_middleware(
    CORSMiddleware,
    allow_origins=allow_origins,
    allow_credentials=False,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "Accept", "Idempotency-Key", "X-Correlation-ID"],
)

from jose import JWTError, jwt
from .security import JWT_SECRET, JWT_ALGORITHM


@app.middleware("http")
async def request_context_middleware(request: Request, call_next):
    incoming = (request.headers.get("X-Correlation-ID") or "").strip()
    correlation_id = (
        incoming
        if incoming and len(incoming) <= 64 and all(ch.isalnum() or ch in "-_." for ch in incoming)
        else uuid.uuid4().hex[:16]
    )
    request.state.correlation_id = correlation_id
    token = correlation_id_var.set(correlation_id)
    started = time.perf_counter()
    try:
        response = await call_next(request)
    finally:
        correlation_id_var.reset(token)
    response.headers["X-Correlation-ID"] = correlation_id
    response.headers["Server-Timing"] = f'app;dur={(time.perf_counter() - started) * 1000:.1f}'
    return response

@app.middleware("http")
async def jwt_validation_middleware(request: Request, call_next):
    path = request.url.path
    if request.method == "OPTIONS":
        return await call_next(request)
        
    # Protect all /api endpoints except the deliberately public surface and the
    # signature-authenticated Razorpay webhook.
    if path.startswith("/api") and not (
        path == "/api/health" or
        path == "/api/auth/login" or
        path == "/api/auth/google-login" or
        path == "/api/auth/register" or
        path.startswith("/api/public") or
        path == "/api/billing/webhooks/razorpay"
    ):
        auth_header = request.headers.get("Authorization")
        if not auth_header or not auth_header.startswith("Bearer "):
            return JSONResponse(
                status_code=401,
                content={"detail": "Not authenticated - Missing or invalid Authorization header"},
            )
        token = auth_header.split(" ")[1]
        try:
            payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
            sub = payload.get("sub")
            if sub is None:
                return JSONResponse(
                    status_code=401,
                    content={"detail": "Not authenticated - Invalid token claims"},
                )
            request.state.user_id = int(sub)
        except (JWTError, ValueError):
            return JSONResponse(
                status_code=401,
                content={"detail": "Not authenticated - Expired or malformed token"},
            )

    response = await call_next(request)
    if path.startswith("/api") and not (
        path == "/api/health" or path.startswith("/api/public")
    ):
        response.headers["Cache-Control"] = "no-store, private"
        response.headers["Pragma"] = "no-cache"
    return response


@app.get("/api/health")
def health():
    return {"ok": True}

# Routers
app.include_router(auth.router, prefix="/api")
app.include_router(resume.router, prefix="/api")
app.include_router(jobs.router, prefix="/api")
app.include_router(recommendations.router, prefix="/api")
app.include_router(llm.router, prefix="/api")
app.include_router(analytics.router, prefix="/api")
app.include_router(rag.router, prefix="/api")
app.include_router(market.router, prefix="/api")
app.include_router(billing.router, prefix="/api")
app.include_router(billing.public_router, prefix="/api")
app.include_router(public_endpoints.router, prefix="/api")
app.include_router(v1_router, prefix="/api")

@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    # Log full traceback server-side, never leak it to the client.
    correlation_id = getattr(request.state, "correlation_id", uuid.uuid4().hex[:12])
    log.exception(
        "Unhandled error [%s] on %s %s: %s",
        correlation_id, request.method, request.url.path, exc,
    )
    return JSONResponse(
        status_code=500,
        content={
            "detail": "Internal server error",
            "correlation_id": correlation_id,
        },
    )
