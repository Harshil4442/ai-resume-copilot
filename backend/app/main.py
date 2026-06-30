import logging
import os
import uuid
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

log = logging.getLogger("ai_resume_copilot")

# Optional: load backend/.env for local dev
try:
    from dotenv import load_dotenv
    load_dotenv(dotenv_path=Path(__file__).resolve().parents[1] / ".env", override=False)
except Exception:
    pass

from .routers import auth, resume, jobs, recommendations, llm, analytics, rag, market, billing, public_endpoints  # noqa: E402
from .database import engine  # noqa: E402
from .models import Base  # noqa: E402
from .migrations import run_migrations  # noqa: E402

from .rate_limiter import limiter
from slowapi.errors import RateLimitExceeded
from slowapi import _rate_limit_exceeded_handler

app = FastAPI(title="AI Resume CoPilot API")
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# Create tables and run schema migrations
Base.metadata.create_all(bind=engine)
run_migrations()

# CORS
origins_env = os.getenv("FRONTEND_ORIGINS", "*")
allow_origins = ["*"] if origins_env.strip() == "*" else [o.strip() for o in origins_env.split(",") if o.strip()]

app.add_middleware(
    CORSMiddleware,
    allow_origins=allow_origins,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

from jose import JWTError, jwt
from .security import JWT_SECRET, JWT_ALGORITHM

@app.middleware("http")
async def jwt_validation_middleware(request: Request, call_next):
    path = request.url.path
    # Protect all /api endpoints except health, login, register, webhook, and public end points
    if path.startswith("/api") and not (
        path == "/api/health" or
        path.startswith("/api/auth/login") or
        path.startswith("/api/auth/register") or
        path.startswith("/api/public") or
        path.startswith("/api/v1/billing/webhook") or
        path.startswith("/api/billing/webhook")
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
app.include_router(public_endpoints.router, prefix="/api")

@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    # Log full traceback server-side, never leak it to the client.
    correlation_id = uuid.uuid4().hex[:12]
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
