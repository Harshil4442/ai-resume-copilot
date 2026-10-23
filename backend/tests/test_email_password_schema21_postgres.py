"""Genuine19→21 PG17/NOLOGIN transaction via the existing owned CI fixture.

Not run by source preparation. Reuses the existing explicitly admitted loopback
fixture; no production database, provider, credential or container creation.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from backend.tests.test_browser_schema19_step_postgres import owned_step  # noqa: F401
from backend.tests.test_native_resume_schema20_postgres import owned20  # noqa: F401
from backend.tests.test_schema21_historical_holds import cohort
from sqlalchemy import MetaData, Table, text

from scripts import email_password_schema21_contract as contract
from scripts import email_password_schema21_step as step


def test_real21_transaction_keeps_every19_row_and_restores_exact_owner_permissions(request):
    owned = request.getfixturevalue("owned20")
    with owned.engine.connect() as db:
        before = step.retained_digest(db, owned.identity)
        acl = step.schema_acl(db, owned.identity)
    with owned.bootstrap.begin() as db:
        result = step.upgrade_transaction(db, owned.identity, root=Path(__file__).resolve().parents[2])
    assert result["schema"] == contract.SCHEMA and result["retained_sha256"] == before
    with owned.engine.connect() as db:
        contract.verify_shape(db, owned.identity.namespace_oid, owned.namespace)
        contract.verify_reset_permissions(db, owned.namespace, owned.identity.runtime, owned.identity.migrator, owned.identity.observer)
        assert step.retained_digest(db, owned.identity) == before
        assert step.schema_acl(db, owned.identity) == acl
        assert db.scalar(text(f'SELECT count(*) FROM "{owned.namespace}".password_reset_tokens')) == 0
        assert db.scalar(text(f'SELECT count(*) FROM "{owned.namespace}".users WHERE credential_epoch<>0 OR credential_epoch IS NULL')) == 0
        assert db.scalar(text("SELECT rolcanlogin FROM pg_catalog.pg_roles WHERE oid=:oid"), {"oid": owned.identity.migrator_oid}) is False
    with owned.bootstrap.begin() as db, pytest.raises(contract.Schema21Denied, match="never_replay"):
        step.upgrade_transaction(db, owned.identity, root=Path(__file__).resolve().parents[2])


def test_real21_outer_rollback_keeps19_marker_and_no_reset_objects(request):
    owned = request.getfixturevalue("owned20")
    class Rollback(Exception):
        pass
    with pytest.raises(Rollback), owned.bootstrap.begin() as db:
        step.upgrade_transaction(db, owned.identity, root=Path(__file__).resolve().parents[2])
        raise Rollback()
    with owned.engine.connect() as db:
        contract.verify_before_shape(db, owned.identity.namespace_oid, owned.namespace)
        assert step.schema_acl(db, owned.identity) == owned.before_acl20
        assert step.retained_digest(db, owned.identity) == owned.before_digest20


def test_real21_transaction_preserves_terminal_unknown_holds_and_old_guards(request):
    owned = request.getfixturevalue('owned20')
    runs, events, liabilities = cohort()
    with owned.engine.begin() as db:
        metadata = MetaData()
        for row in runs:
            row.update(user_id=1, idempotency_key=row['id'], input_fingerprint='a'*64,
                input_payload={}, cancel_requested=False, updated_at=row['completed_at'], generation_attempt_limit=3)
        for row in events:
            row.update(user_id=1, prompt_version='synthetic-historical', latency_ms=0)
        for name, rows in [('analysis_runs',runs), ('model_cost_liabilities',liabilities), ('model_call_events',events)]:
            table = Table(name, metadata, schema=owned.namespace, autoload_with=db)
            db.execute(table.insert(), rows)
    with owned.engine.connect() as db:
        step.drained(db, owned.identity)
        before = step.retained_digest(db, owned.identity)
        # Older fresh/19/20 paths remain strict; only explicit21 admits retained holds.
        with pytest.raises(RuntimeError, match='unresolved'):
            step.previous.drained(db, owned.identity)
    with owned.bootstrap.begin() as db:
        result = step.upgrade_transaction(db, owned.identity, root=Path(__file__).resolve().parents[2])
    assert result['retained_sha256'] == before
    with owned.engine.connect() as db:
        assert step.retained_digest(db, owned.identity) == before
        assert db.scalar(text(f'SELECT sum(reserved_cost_micros) FROM "{owned.namespace}".model_cost_liabilities WHERE cost_state=\'outcome_unknown\'')) == 120742
        assert db.scalar(text(f'SELECT count(*) FROM "{owned.namespace}".model_cost_liabilities WHERE cost_state=\'outcome_unknown\' AND settled_cost_micros IS NULL AND settled_at IS NULL')) == 2
        assert db.scalar(text(f'SELECT job_service_credits FROM "{owned.namespace}".users WHERE id=1')) == 100
