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
python - <<'PY' || true
from dotenv import load_dotenv
load_dotenv(".env")

from app.database import SessionLocal
from app.models import User

db = SessionLocal()
try:
    try:
        u = db.query(User).filter(User.id == 1).first()
        if not u:
            db.add(User(id=1, email="demo@local", password_hash=""))
            db.commit()
            print("[OK] Seeded users.id=1")
        else:
            print("[OK] users.id=1 already exists")
    except Exception as e:
        print("\n❌ INLINE SCRIPT DB ERROR: Could not connect to database.")
        print(f"Error: {e}\n")
finally:
    db.close()
PY

# Start FastAPI (public)
echo "[INFO] Backend starting on 0.0.0.0:${PORT}"
exec uvicorn app.main:app --host 0.0.0.0 --port "${PORT}"
