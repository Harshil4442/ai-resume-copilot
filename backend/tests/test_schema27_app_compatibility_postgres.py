"""Current27 / actual qualified45b3 app processes share additive storage.

The prior is its exact290-file application archive, never a relabelled current
implementation. This is owned PostgreSQL behavior, not serving-prior, mixed
native authority, provider, or rollback permission. The full retained26 suite
has its separate immutable checkout and original schema26 catalog.
"""
from __future__ import annotations

import hashlib
import os
from uuid import uuid4

import pytest
from alembic.config import Config
from sqlalchemy import text
from test_schema25_app_compatibility_postgres import (  # noqa: F401
    ROOT,
    app25_database,
    release,
    run_app,
)

from alembic import command
from scripts import notification_schema27_contract as schema27
from scripts import schema25_app_release_contract as financial
from scripts import schema27_app_release_contract as current

PRIOR_SOURCE = "45b3e808c88d78c1d7d182cd4af4482df25c13f0"
PRIOR_APP_SHA256 = "f2775c3b9f8d8df1f4f3d97abbe190cf5d2eb46b87beeb942b5252c3840d8670"


@pytest.fixture
def current27_database(request):
    if (os.getenv("CI") != "true" or os.getenv("GITHUB_ACTIONS") != "true"
            or os.getenv("HIREWIZ_APP_COMPATIBILITY_REQUIRED") != "true"
            or not os.getenv("HIREWIZ_TEST_POSTGRES_URL") or os.getenv("HIREWIZ_APP25_TEST_ROOT")):
        pytest.fail("Current27 mixed-source compatibility requires prepared owned GitHub PostgreSQL17")
    engine = request.getfixturevalue("app25_database")
    configuration = Config(str(ROOT / "backend/alembic.ini"))
    configuration.set_main_option("script_location", str(ROOT / "backend/alembic"))
    with engine.begin() as connection:
        configuration.attributes["connection"] = connection
        command.upgrade(configuration, schema27.SCHEMA)
        assert connection.scalar(text("SELECT version_num FROM public.alembic_version")) == schema27.SCHEMA
        schema27.verify_shape(connection, connection.scalar(text(
            "SELECT oid FROM pg_catalog.pg_namespace WHERE nspname='public'")), "public")
    return engine


@pytest.fixture
def actual_app_roots(tmp_path):
    prior = release.initial.prior_app_files(ROOT, PRIOR_SOURCE)
    assert len(prior) == 290 and release.initial.prior_app_digest(prior) == PRIOR_APP_SHA256
    # The new family binds current312 app + additive HTTP + every owned proof
    # helper. The old immutable family is deliberately not widened to current27.
    old_anchor = release.initial.prior_app_files(ROOT, current.PRIOR_RELEASE)
    assert current.source_fingerprint(ROOT, old_anchor)
    for name, raw in prior.items():
        target = tmp_path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(raw)
    return {"prior": tmp_path / "backend", "current": ROOT / "backend"}


def snapshot(engine):
    with engine.connect() as connection:
        namespace = connection.scalar(text("SELECT oid FROM pg_catalog.pg_namespace WHERE nspname='public'"))
        schema27.verify_shape(connection, namespace, "public")
        return (
            schema27.before.previous.catalog_digest(connection, namespace, "public"),
            financial.liability_pins(connection),
            tuple(connection.execute(text("SELECT * FROM public.notification_preferences ORDER BY user_id")).all()),
        )


@pytest.mark.parametrize("producer,consumer", [("prior", "current"), ("current", "prior")])
def test_actual_prior26_and_current27_processes_exchange_preserved_storage(
        current27_database, actual_app_roots, producer, consumer, record_property):
    engine = current27_database
    record_property("prior_source", PRIOR_SOURCE)
    record_property("prior_app_sha256", PRIOR_APP_SHA256)
    record_property("current_app_sha256", current.CANDIDATE_APP_SHA256)
    identity = "current27-mixed-" + uuid4().hex
    receipt = run_app(actual_app_roots[producer], engine, "produce", identity)
    with engine.begin() as connection:
        # An inert disabled projection is preserved data, not opt-in/native proof.
        connection.execute(text("INSERT INTO public.notification_preferences "
            "(user_id,subject_uuid,account_binding_id,principal_sha256,native_generation,"
            "native_head_sha256,recipient_sha256,policy_version,enabled,next_due_at,operation_id,updated_at) "
            "VALUES (1,:subject,:binding,:principal,1,:head,:recipient,'weekly-digest-v1',false,NULL,:operation,now())"),
            {"subject": str(uuid4()), "binding": str(uuid4()), "principal": "a" * 64,
             "head": "b" * 64, "recipient": "c" * 64, "operation": str(uuid4())})
    before = snapshot(engine)
    assert run_app(actual_app_roots[consumer], engine, "consume", identity, receipt) == {
        "status": "actual-source-compatibility-observed"}
    assert snapshot(engine) == before
    with engine.connect() as connection:
        with pytest.raises(schema27.before.Schema26Denied, match="exact_marker"):
            schema27.before.observe(connection)
        assert connection.scalar(text("SELECT count(*) FROM public.credit_execution_holds")) == 0
        assert connection.scalar(text("SELECT count(*) FROM public.model_call_events")) == 0
        assert hashlib.sha256(connection.scalar(text(
            "SELECT source_document FROM public.resumes WHERE id=:id"), {"id": receipt["resume"]})).hexdigest() == receipt["source_sha256"]


def test_missing_current27_context_refuses_before_any_database_or_archive(monkeypatch):
    monkeypatch.delenv("HIREWIZ_APP_COMPATIBILITY_REQUIRED", raising=False)

    class NoFallback:
        def getfixturevalue(self, name):
            raise AssertionError("No database or source fallback is permitted")

    with pytest.raises(pytest.fail.Exception, match="prepared owned GitHub PostgreSQL17"):
        current27_database.__wrapped__(NoFallback())
