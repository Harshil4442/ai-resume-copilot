#!/usr/bin/env bash
set -euo pipefail

echo "[INFO] Starting AI Resume CoPilot (frontend + backend) in one container..."

export PORT="${PORT:-10000}"

# Backend
cd /app/backend
[ -f .env ] || touch .env

# Create tables (safe for demo; if DB is down, don't crash hard)
python create_tables.py || true

# Seed user id=1 to avoid FK errors in demo flows
python - <<'PY'
from dotenv import load_dotenv
load_dotenv(".env")

from app.database import SessionLocal
from app.models import User

db = SessionLocal()
try:
    u = db.query(User).filter(User.id == 1).first()
    if not u:
        db.add(User(id=1, email="demo@local", password_hash=""))
        db.commit()
        print("[OK] Seeded users.id=1")
    else:
        print("[OK] users.id=1 already exists")
finally:
    db.close()
PY || true

# Start FastAPI (internal)
uvicorn app.main:app --host 127.0.0.1 --port 8000 &
echo "[INFO] Backend started on 127.0.0.1:8000"

# Frontend (public)
cd /app/frontend
echo "[INFO] Frontend starting on 0.0.0.0:${PORT}"
exec ./node_modules/.bin/next start -H 0.0.0.0 -p "${PORT}"
