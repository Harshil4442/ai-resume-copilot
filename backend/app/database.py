import os
import sys
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base

_configured_database_url = (os.getenv("DATABASE_URL") or "").strip()
_app_env = (os.getenv("APP_ENV") or "production").strip().lower()
_is_test = bool(os.getenv("PYTEST_CURRENT_TEST")) or "pytest" in sys.modules

if not _configured_database_url:
    if not _is_test and _app_env not in {"development", "dev", "local", "test"}:
        raise RuntimeError("DATABASE_URL must be configured in production.")
    _configured_database_url = "sqlite:///./app.db"

if (
    not _is_test
    and _app_env not in {"development", "dev", "local", "test"}
    and _configured_database_url.startswith("sqlite")
):
    raise RuntimeError("SQLite is not supported for production payment processing.")

DATABASE_URL = _configured_database_url

Base = declarative_base()

connect_args = {}
if DATABASE_URL.startswith("sqlite"):
    connect_args = {"check_same_thread": False}

engine = create_engine(DATABASE_URL, connect_args=connect_args, pool_pre_ping=True)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
