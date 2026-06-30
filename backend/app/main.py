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

from .routers import auth, resume, jobs, recommendations, llm, analytics, rag, market  # noqa: E402
from .database import engine  # noqa: E402
from .models import Base  # noqa: E402

app = FastAPI(title="AI Resume CoPilot API")

# Create tables (simple starter approach; for production prefer migrations)
Base.metadata.create_all(bind=engine)

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
