"""Upgrade preserves old rights; populated accounting schemas cannot be dropped."""
import os
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

from backend.app.domains.credits.service import grant_credits
from backend.app.models import User
from sqlalchemy import create_engine, inspect
from sqlalchemy.orm import Session


def migrate(database, *arguments):
    root = Path(__file__).resolve().parents[1]
    env = os.environ | {"APP_ENV": "test", "DATABASE_URL": f"sqlite:///{database}",
                        "JWT_SECRET": "isolated-migration-secret-longer-than-thirty-two", "PYTHONPATH": str(root)}
    return subprocess.run([sys.executable, "-m", "alembic", *arguments], cwd=root,
                          env=env, text=True, capture_output=True, timeout=30, check=False)


def test_upgrade_and_empty_rollback_preserve_legacy_balances(tmp_path):
    database = tmp_path / "existing.db"
    result = migrate(database, "upgrade", "20261023_0021")
    assert result.returncode == 0, result.stderr
    engine = create_engine(f"sqlite:///{database}")
    with Session(engine) as db:
        db.add(User(id=42, email="legacy-migration@example.test", ai_credits=17, job_service_credits=13))
        db.commit()
    result = migrate(database, "upgrade", "20261027_0023")
    assert result.returncode == 0, result.stderr
    assert {"credit_grants", "credit_purchase_quotes", "credit_service_quotes", "credit_execution_holds",
            "credit_execution_attempts"}.issubset(inspect(engine).get_table_names())
    with Session(engine) as db:
        user = db.get(User, 42)
        assert (user.ai_credits, user.job_service_credits) == (17, 13)
    result = migrate(database, "downgrade", "20261023_0021")
    assert result.returncode == 0, result.stderr
    assert "credit_grants" not in inspect(engine).get_table_names()
    with Session(engine) as db:
        assert db.get(User, 42).ai_credits == 17
    engine.dispose()


def test_downgrade_refuses_to_destroy_wallet_history(tmp_path):
    database = tmp_path / "populated.db"
    result = migrate(database, "upgrade", "20261027_0022")
    assert result.returncode == 0, result.stderr
    engine = create_engine(f"sqlite:///{database}")
    with Session(engine) as db:
        db.add(User(id=42, email="retained-history@example.test"))
        db.commit()
        grant_credits(db, user_id=42, quantity=40, source_type="payment_order", source_id="migration-fixture",
                      funding_kind="paid", terms_snapshot={"credits": 40}, expires_at=datetime.now(UTC)+timedelta(days=30))
        db.commit()
    result = migrate(database, "downgrade", "20261023_0021")
    assert result.returncode != 0
    assert "Unified credit history must be retained" in result.stderr
    assert "credit_grants" in inspect(engine).get_table_names()
    engine.dispose()
