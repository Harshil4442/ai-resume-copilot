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

if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql+psycopg://", 1)
elif DATABASE_URL.startswith("postgresql://"):
    DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+psycopg://", 1)

Base = declarative_base()

connect_args = {}
if DATABASE_URL.startswith("sqlite"):
    connect_args = {"check_same_thread": False}

pool_options = {}
if not DATABASE_URL.startswith("sqlite"):
    # Bound total connections against max_instances × processes × (pool + overflow).
    pool_options = {
        "pool_size": int(os.getenv("DB_POOL_SIZE", "4")),
        "max_overflow": int(os.getenv("DB_MAX_OVERFLOW", "2")),
        "pool_timeout": int(os.getenv("DB_POOL_TIMEOUT_SECONDS", "10")),
        "pool_recycle": int(os.getenv("DB_POOL_RECYCLE_SECONDS", "300")),
    }
engine = create_engine(DATABASE_URL, connect_args=connect_args, pool_pre_ping=True, **pool_options)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
