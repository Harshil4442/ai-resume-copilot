"""Production-engine migration rehearsal inside disposable test-only schemas."""
from datetime import UTC, datetime, timedelta

import pytest
from backend.app.domains.credits.models import CreditGrant
from backend.app.domains.credits.service import grant_credits
from backend.app.models import User
from backend.tests.test_employer_admissions_postgres import _migrate, pg_engine
from sqlalchemy import inspect, text
from sqlalchemy.orm import Session

assert pg_engine


def test_postgres_additive_upgrade_and_empty_rollback_preserve_old_rights(pg_engine):
    _migrate(pg_engine, "20261023_0021")
    with Session(pg_engine) as db:
        db.add(User(id=42, email="pg-legacy-migration@example.test", ai_credits=17, job_service_credits=13))
        db.commit()
    _migrate(pg_engine, "20261028_0025")
    assert {"credit_grants", "credit_execution_holds", "free_core_reservations"}.issubset(inspect(pg_engine).get_table_names())
    with Session(pg_engine) as db:
        user = db.get(User, 42)
        assert (user.ai_credits, user.job_service_credits) == (17, 13)
    _migrate(pg_engine, "20261023_0021", direction="downgrade")
    assert "credit_grants" not in inspect(pg_engine).get_table_names()
    with Session(pg_engine) as db:
        user = db.get(User, 42)
        assert (user.ai_credits, user.job_service_credits) == (17, 13)


def test_postgres_populated_downgrade_is_atomic_and_retains_credit_history(pg_engine):
    _migrate(pg_engine, "20261028_0025")
    with Session(pg_engine) as db:
        db.add(User(id=42, email="pg-retained-migration@example.test"))
        db.commit()
        grant_credits(db, user_id=42, quantity=40, source_type="payment_order", source_id="pg-migration-fixture",
            funding_kind="paid", terms_snapshot={"credits": 40}, expires_at=datetime.now(UTC) + timedelta(days=30))
        db.commit()
        head = db.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
    with pytest.raises(RuntimeError, match="Unified credit history must be retained"):
        _migrate(pg_engine, "20261023_0021", direction="downgrade")
    with Session(pg_engine) as db:
        assert db.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == head
        assert db.query(CreditGrant).one().quantity == 40
    assert "free_core_reservations" in inspect(pg_engine).get_table_names()
