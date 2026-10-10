"""Actual schema0011 SQL observations; no cloud or native-account authority."""

from __future__ import annotations

import json
import os
from uuid import uuid4

import pytest
from backend.tests.test_employer_admissions_postgres import _migrate
from backend.tests.test_monetary_cutover_preflight_postgres import (
    assert_refused,
    insert_liability,
    report,
)
from backend.tests.test_monetary_cutover_preflight_postgres import (
    context as original_context,
)
from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import make_url
from sqlalchemy.pool import NullPool

from scripts import monetary_cutover_preflight as preflight


@pytest.fixture
def context():
    # The optional second local port isolates author/reviewer sessions from the
    # shared cluster; CI uses its existing dedicated disposable database.
    configured = os.getenv("HIREWIZ_SCHEMA11_TEST_POSTGRES_URL") or os.getenv(
        "HIREWIZ_TEST_POSTGRES_URL"
    )
    if not configured:
        pytest.skip("Set the dedicated local disposable PostgreSQL test URL")
    url = make_url(configured)
    if (
        url.drivername != "postgresql+psycopg"
        or url.host != "127.0.0.1"
        or url.port not in {55433, 55435}
        or url.database != "hirewiz_admission_test"
    ):
        pytest.fail("Schema0011 tests accept only a dedicated local disposable database")
    namespace = "monetary_schema11_" + uuid4().hex
    admin = create_engine(url, poolclass=NullPool)
    with admin.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{namespace}"'))
    engine = create_engine(
        url,
        poolclass=NullPool,
        connect_args={"options": f"-csearch_path={namespace} -clock_timeout=4000"},
    )
    try:
        yield from original_context.__wrapped__(engine)
    finally:
        engine.dispose()
        with admin.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{namespace}" CASCADE'))
        admin.dispose()


def schema11_report(context, **updates):
    return report(
        context,
        expected_database_schema="20261009_0011",
        candidate_schema="20261009_0011",
        **updates,
    )


def test_actual_schema11_has_financial_snapshot_but_never_cutover_authority(context):
    _migrate(context[0], "20261009_0011")
    result = schema11_report(context)
    assert result["database_gate_passed"] and not result["cutover_ready"]
    assert result["observed_database"]["schema"] == "20261009_0011"
    assert result["observed_database"]["liabilities"]["present"]
    assert result["observed_database"]["liabilities"]["row_count"] == 0
    assert result["observed_database"]["observation"] == "single_read_only_repeatable_read_snapshot"


def test_schema9_observation_for_candidate11_keeps_cost_proof_unavailable(context):
    result = report(context, candidate_schema="20261009_0011")
    assert result["database_gate_passed"] and not result["cutover_ready"]
    assert result["observed_database"]["schema"] == "20261008_0009"
    assert not result["observed_database"]["liabilities"]["present"]
    assert result["observed_database"]["liabilities"]["held_cost_micros"] is None
    assert not result["observed_database"]["legacy_history_is_cost_proof"]


def test_schema10_observation_for_candidate11_keeps_unknown_liability(context):
    engine = context[0]
    _migrate(engine, "20261009_0010")
    insert_liability(engine, state="outcome_unknown", settled=None)
    result = report(context, expected_database_schema="20261009_0010", candidate_schema="20261009_0011")
    assert_refused(result, "unresolved_obligations")
    assert result["observed_database"]["liabilities"]["held_cost_micros"] == 1267


@pytest.mark.parametrize("state", ["reserved", "outcome_unknown", "usage_unavailable"])
def test_actual_schema11_keeps_unknown_model_cost_holds(context, state):
    engine = context[0]
    _migrate(engine, "20261009_0011")
    insert_liability(engine, state=state, settled=None)
    result = schema11_report(context)
    assert_refused(result, "unresolved_obligations")
    assert result["observed_database"]["liabilities"]["unresolved_count"] == 1
    assert result["observed_database"]["liabilities"]["held_cost_micros"] == 1267
    with engine.connect() as connection:
        assert connection.execute(
            text("SELECT reserved_cost_micros FROM model_cost_liabilities")
        ).scalar_one() == 1267


def test_actual_schema11_retains_independently_priced_settled_liability(context):
    engine = context[0]
    _migrate(engine, "20261009_0011")
    insert_liability(engine)
    result = schema11_report(context)
    assert result["database_gate_passed"] and not result["cutover_ready"]
    assert result["observed_database"]["liabilities"]["settled_cost_micros"] == 17


def test_schema11_missing_liability_table_is_not_ignored(context):
    engine = context[0]
    _migrate(engine, "20261009_0011")
    with engine.begin() as connection:
        connection.execute(text("DROP TABLE model_cost_liabilities CASCADE"))
    with pytest.raises(preflight.Denied, match="^database_liability_table_missing$"):
        schema11_report(context)


def test_schema11_held_run_budget_is_still_an_unresolved_obligation(context):
    engine = context[0]
    _migrate(engine, "20261009_0011")
    with engine.begin() as connection:
        connection.execute(text(
            "INSERT INTO users (id,email,password_hash,ai_credits,job_service_credits) "
            "VALUES (912,'synthetic-budget@example.invalid','not-a-password',50,0)"
        ))
        connection.execute(text(
            "INSERT INTO analysis_runs "
            "(id,user_id,operation,status,idempotency_key,input_fingerprint,input_payload,"
            "estimated_units,committed_units,usage_state,attempt_count,generation_attempt_limit,"
            "generation_attempt_count,cancel_requested,created_at,updated_at,"
            "model_cost_reserved_micros,model_cost_settled_micros,model_cost_state) "
            "VALUES ('synthetic-held-budget',912,'resume.tailor','succeeded','synthetic','synthetic',"
            "'{}',1,0,'pending',0,3,0,false,now(),now(),12,0,'unquoted')"
        ))
    result = schema11_report(context)
    assert_refused(result, "unresolved_obligations")
    assert result["observed_database"]["invalid_or_held_model_budget_run_count"] == 1


def test_schema10_candidate_cannot_adopt_schema11_by_marker_only(context):
    _migrate(context[0], "20261009_0011")
    with pytest.raises(preflight.Denied, match="^database_schema_unsupported$"):
        report(context, expected_database_schema="20261009_0011")


@pytest.mark.parametrize("table", ["candidate_password_accounts", "candidate_lifetime_history"])
def test_schema11_marker_with_missing_projection_refuses(context, table):
    engine = context[0]
    _migrate(engine, "20261009_0011")
    with engine.begin() as connection:
        connection.execute(text(f'DROP TABLE "{table}" CASCADE'))
    with pytest.raises(preflight.Denied, match="^database_candidate_projection_tables_missing$"):
        schema11_report(context)


@pytest.mark.parametrize(
    "alteration",
    [
        "ALTER TABLE candidate_password_accounts ALTER COLUMN credential_sha256 DROP NOT NULL",
        "ALTER TABLE candidate_password_accounts ALTER COLUMN credential_sha256 TYPE text",
        "ALTER TABLE candidate_password_accounts ADD COLUMN unknown_projection text NOT NULL DEFAULT ''",
        "DROP TABLE candidate_lifetime_history; CREATE VIEW candidate_lifetime_history AS SELECT 'synthetic'::varchar(36) AS registration_id",
    ],
)
def test_schema11_wrong_projection_shape_refuses(context, alteration):
    engine = context[0]
    _migrate(engine, "20261009_0011")
    with engine.begin() as connection:
        # Static test-owned DDL, never caller text or production credentials.
        connection.exec_driver_sql(alteration)
    with pytest.raises(preflight.Denied, match="^database_candidate_projection_shape_mismatch$"):
        schema11_report(context)


def test_old_marker_cannot_hide_present_candidate_projection(context):
    engine = context[0]
    _migrate(engine, "20261009_0011")
    with engine.begin() as connection:
        connection.execute(text("UPDATE alembic_version SET version_num='20261009_0010'"))
    with pytest.raises(preflight.Denied, match="^database_schema_shape_mismatch$"):
        report(context, expected_database_schema="20261009_0010")


def test_actual_schema11_observation_does_not_read_or_emit_candidate_values(context):
    engine = context[0]
    _migrate(engine, "20261009_0011")
    subject, binding = str(uuid4()), str(uuid4())
    with engine.begin() as connection:
        connection.execute(text(
            "INSERT INTO users (id,email,password_hash,ai_credits,job_service_credits) "
            "VALUES (911,'private-input@example.invalid','private-hash',50,0)"
        ))
        connection.execute(text(
            "INSERT INTO candidate_password_accounts "
            "(user_id,subject_uuid,account_binding_id,principal_sha256,credential_sha256,auth_generation,state) "
            "VALUES (911,:subject,:binding,:principal,:credential,1,'PENDING')"
        ), {"subject": subject, "binding": binding, "principal": "a" * 64, "credential": "b" * 64})
    statements = []

    def capture(connection, cursor, statement, parameters, execution_context, executemany):
        statements.append(statement)

    observer = engine.cutover_readonly_observer
    event.listen(observer, "before_cursor_execute", capture)
    try:
        result = schema11_report(context)
    finally:
        event.remove(observer, "before_cursor_execute", capture)
    serialized = json.dumps(result)
    for value in (subject, binding, "private-input@example.invalid", "private-hash", "a" * 64, "b" * 64):
        # These synthetic identifiers have not been passed to the helper.
        assert value not in json.dumps(result["observed_database"])
    assert subject not in serialized and binding not in serialized
    assert not any("FROM candidate_password_accounts" in statement for statement in statements)
    assert not any("FROM candidate_lifetime_history" in statement for statement in statements)
    assert result["database_gate_passed"] and not result["cutover_ready"]


def test_actual_schema11_retired_role_candidate_column_authority_remains_a_blocker(context):
    engine, roles, _, data = context
    _migrate(engine, "20261009_0011")
    with engine.begin() as connection:
        connection.execute(text(
            f'GRANT UPDATE (credential_sha256) ON "{data["database_namespace"]}".candidate_password_accounts '
            f'TO "{roles["retired"]}"'
        ))
    result = schema11_report(context)
    assert_refused(result, "retired_database_role_not_fenced")
    assert result["observed_database"]["roles"]["retired"]["writable_table_count"] == 1
