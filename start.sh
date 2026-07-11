#!/usr/bin/env bash
set -euo pipefail

echo "[INFO] Starting the HireWiz backend..."

export PORT="${PORT:-10000}"

# Backend
cd /app/backend
[ -f .env ] || touch .env

# Create tables (safe for demo; if DB is down, don't crash hard)
python create_tables.py || true

# Optional demo seeding — DISABLED by default. Only runs when SEED_DEMO=true.
# This previously seeded a passwordless user with id=1 unconditionally, which
# is unsafe for any environment with real users.
if [ "${SEED_DEMO:-false}" = "true" ]; then
  echo "[INFO] SEED_DEMO=true — seeding demo user."
  python - <<'PY' || true
from dotenv import load_dotenv
load_dotenv(".env")

from app.database import SessionLocal
from app.models import User
from app.security import hash_password

db = SessionLocal()
try:
    u = db.query(User).filter(User.email == "demo@local").first()
    if not u:
        db.add(User(email="demo@local", password_hash=hash_password("demo-password-change-me")))
        db.commit()
        print("[OK] Seeded demo@local user (password: demo-password-change-me)")
    else:
        print("[OK] demo@local already exists")
except Exception as e:
    print(f"[WARN] Demo seed failed: {e}")
finally:
    db.close()
PY
fi

# Start FastAPI (public)
echo "[INFO] Backend starting on 0.0.0.0:${PORT}"
exec uvicorn app.main:app --host 0.0.0.0 --port "${PORT}"
