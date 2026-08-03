from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from sqlalchemy import create_engine, inspect


def test_alembic_builds_clean_database_and_product_tables(tmp_path):
    backend_dir = Path(__file__).resolve().parents[1]
    database_path = tmp_path / "migration.db"
    env = os.environ.copy()
    env.update(
        {
            "APP_ENV": "test",
            "DATABASE_URL": f"sqlite:///{database_path}",
            "JWT_SECRET": "migration-test-secret-at-least-thirty-two-characters",
            "PYTHONPATH": str(backend_dir),
        }
    )
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=backend_dir,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    inspector = inspect(create_engine(f"sqlite:///{database_path}"))
    tables = set(inspector.get_table_names())
    assert {
        "alembic_version",
        "users",
        "payment_orders",
        "opportunities",
        "analysis_runs",
        "usage_events",
        "evidence_items",
        "resume_versions",
        "career_memory_entries",
        "notification_outbox",
        "admin_audit_events",
    }.issubset(tables)
