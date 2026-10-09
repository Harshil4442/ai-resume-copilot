"""Actual PostgreSQL observations, owned schemas/roles, no cloud operations."""

from __future__ import annotations

import copy
import hashlib
import json
import os
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from backend.tests.test_employer_admissions_postgres import _migrate
from backend.tests.test_employer_admissions_postgres import pg_engine as owned_pg_engine
from sqlalchemy import create_engine, event, text
from sqlalchemy.pool import NullPool

from scripts import monetary_cutover_preflight as preflight


@pytest.fixture
def pg_engine():
    yield from owned_pg_engine.__wrapped__()


@pytest.fixture
def context(pg_engine):
    _migrate(pg_engine, "20261008_0009")
    roles = {
        alias: "cutover_" + uuid4().hex
        for alias in ("retired", "replacement_runtime", "replacement_migration")
    }
    observer = "cutover_observer_" + uuid4().hex
    with pg_engine.begin() as connection:
        namespace = connection.execute(text("SELECT current_schema()")).scalar_one()
        operator = connection.execute(text("SELECT current_user")).scalar_one()
        identifier = connection.execute(
            text("SELECT system_identifier::text FROM pg_control_system()")
        ).scalar_one()
        db_oid = int(
            connection.execute(
                text("SELECT oid FROM pg_database WHERE datname = current_database()")
            ).scalar_one()
        )
        for role in roles.values():
            connection.execute(text(f'CREATE ROLE "{role}" NOLOGIN'))
        connection.execute(
            text(f"CREATE ROLE \"{observer}\" LOGIN PASSWORD 'synthetic-local-observer'")
        )
        connection.execute(text(f'GRANT pg_read_all_stats TO "{observer}"'))
        connection.execute(
            text(f'GRANT EXECUTE ON FUNCTION pg_catalog.pg_control_system() TO "{observer}"')
        )
        connection.execute(text(f'GRANT USAGE ON SCHEMA "{namespace}" TO "{observer}"'))
        connection.execute(
            text(f'GRANT SELECT ON ALL TABLES IN SCHEMA "{namespace}" TO "{observer}"')
        )
        connection.execute(
            text(
                f'ALTER DEFAULT PRIVILEGES IN SCHEMA "{namespace}" GRANT SELECT ON TABLES TO "{observer}"'
            )
        )
    observing = create_engine(
        pg_engine.url.set(username=observer, password="synthetic-local-observer"),
        poolclass=NullPool,
        connect_args={"options": f"-csearch_path={namespace}"},
    )
    pg_engine.cutover_readonly_observer = observing
    data = {
        "format_version": 1,
        "reviewed_at": datetime.now(UTC).isoformat(),
        "scope": "named_project_and_declared_external_consumers",
        "consumer_inventory_complete": True,
        "shared_credentials_resolved": True,
        "unknown_consumer_count": 0,
        "provider_fencing_verified": True,
        "queue_admission_closed": True,
        "protected_backup_verified": True,
        "serving_commit": "e" * 40,
        "candidate_commit": "a" * 40,
        "candidate_image": "sha256:" + "b" * 64,
        "expected_database_schema": "20261008_0009",
        "candidate_schema": "20261009_0010",
        "expected_system_identifier_sha256": hashlib.sha256(str(identifier).encode()).hexdigest(),
        "expected_database_oid": db_oid,
        "database_namespace": namespace,
        "role_env": {alias: "HIREWIZ_CUTOVER_ROLE_" + alias.upper() for alias in roles},
        "database_operator_role_env": ["HIREWIZ_CUTOVER_OPERATOR_LOCAL"],
        "consumers": [
            {
                "label": kind,
                "kind": kind,
                "credential": "retired",
                "release": "e" * 40,
                "state": "fenced",
                "generation_disabled": True,
            }
            for kind in ("api", "analysis", "employer", "migration")
        ],
    }
    try:
        yield pg_engine, roles, operator, data
    finally:
        observing.dispose()
        with pg_engine.begin() as connection:
            for role in (*roles.values(), observer):
                connection.execute(text(f'DROP OWNED BY "{role}"'))
                connection.execute(text(f'DROP ROLE "{role}"'))


def report(context, **updates):
    engine, roles, operator, original = context
    data = copy.deepcopy(original)
    data.update(updates)
    inventory = preflight.Inventory.model_validate(data)
    observed = preflight.observe(engine.cutover_readonly_observer, inventory, roles, (operator,))
    return preflight.evaluate(inventory, "0" * 64, observed, datetime.now(UTC))


def assert_refused(result, reason):
    assert not result["database_gate_passed"] and not result["cutover_ready"]
    assert reason in result["database_gate_reasons"]


def test_actual_snapshot_clear_database_gate_is_never_cutover_authority(context):
    result = report(context)
    assert result["database_gate_passed"] is True
    assert result["cutover_ready"] is False
    assert result["observed_database"]["observation"] == "single_read_only_repeatable_read_snapshot"
    for role in context[1].values():
        assert role not in json.dumps(result)
    assert context[2] not in json.dumps(result)
    assert not result["observed_database"]["legacy_history_is_cost_proof"]


@pytest.mark.parametrize(
    "updates,reason",
    [
        ({"expected_database_schema": "20261009_0010"}, "database_schema_mismatch"),
        ({"expected_database_oid": 1}, "database_identity_mismatch"),
        ({"expected_system_identifier_sha256": "0" * 64}, "database_identity_mismatch"),
        ({"consumer_inventory_complete": False}, "consumer_inventory_unresolved"),
        ({"shared_credentials_resolved": False}, "consumer_inventory_unresolved"),
        ({"unknown_consumer_count": 1}, "consumer_inventory_unresolved"),
    ],
)
def test_actual_wrong_database_or_unreviewed_inventory_refuses(context, updates, reason):
    assert_refused(report(context, **updates), reason)


def test_nologin_does_not_hide_existing_old_session(context):
    engine, roles, _, _ = context
    old = roles["retired"]
    with engine.begin() as connection:
        connection.execute(text(f"ALTER ROLE \"{old}\" LOGIN PASSWORD 'synthetic-local-only'"))
    old_engine = create_engine(engine.url.set(username=old, password="synthetic-local-only"))
    try:
        with old_engine.connect() as old_connection:
            old_connection.execute(text("SELECT 1"))
            with engine.begin() as connection:
                connection.execute(text(f'ALTER ROLE "{old}" NOLOGIN'))
            result = report(context)
            assert result["observed_database"]["roles"]["retired"]["login_enabled"] is False
            assert result["observed_database"]["roles"]["retired"]["active_session_count"] == 1
            assert_refused(result, "retired_database_sessions_present")
    finally:
        old_engine.dispose()


def test_retired_nologin_role_write_privileges_remain_a_blocker(context):
    engine, roles, _, data = context
    with engine.begin() as connection:
        connection.execute(
            text(f'GRANT USAGE ON SCHEMA "{data["database_namespace"]}" TO "{roles["retired"]}"')
        )
        connection.execute(
            text(
                f'GRANT INSERT ON "{data["database_namespace"]}".analysis_runs TO "{roles["retired"]}"'
            )
        )
    assert_refused(report(context), "retired_database_role_not_fenced")


def test_elevated_replacement_runtime_refuses(context):
    engine, roles, _, _ = context
    with engine.begin() as connection:
        connection.execute(text(f'ALTER ROLE "{roles["replacement_runtime"]}" CREATEROLE'))
    assert_refused(report(context), "replacement_runtime_role_elevated")


def test_unknown_queued_analysis_obligation_refuses_without_loading_candidate_payload(context):
    engine = context[0]
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO users (id,email,password_hash,ai_credits,job_service_credits) VALUES (701,'synthetic@example.invalid','not-a-password',50,0)"
            )
        )
        connection.execute(
            text(
                "INSERT INTO analysis_runs (id,user_id,operation,status,idempotency_key,input_fingerprint,input_payload,estimated_units,committed_units,usage_state,attempt_count,generation_attempt_limit,generation_attempt_count,cancel_requested,created_at,updated_at) VALUES ('private_obligation',701,'resume.tailor','queued','synthetic','synthetic','{}',1,0,'pending',0,3,0,false,now(),now())"
            )
        )
    result = report(context)
    assert_refused(result, "unresolved_obligations")
    assert "private_obligation" not in json.dumps(result)


@pytest.mark.parametrize(
    "state,settled",
    [
        ("reserved", False),
        ("outcome_unknown", False),
        ("usage_unavailable", False),
        ("overrun", True),
    ],
)
def test_actual_detached_unknown_or_overrun_liability_blocks(context, state, settled):
    engine = context[0]
    _migrate(engine, "20261009_0010")
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO model_cost_liabilities (id,financial_group_id,attempt_number,provider,model,endpoint_key,currency,pricing_quote,input_token_estimate,reserved_cost_micros,settled_cost_micros,cost_state,created_at,settled_at) VALUES ('private','private',1,'synthetic','synthetic','synthetic','USD','{}',1,1267,:settled,:state,now(),:settled_at)"
            ),
            {
                "state": state,
                "settled": 17 if settled else None,
                "settled_at": datetime.now(UTC) if settled else None,
            },
        )
    result = report(context, expected_database_schema="20261009_0010")
    assert_refused(result, "unresolved_obligations")
    assert result["observed_database"]["liabilities"]["unresolved_count"] == 1
    with engine.connect() as connection:
        assert (
            connection.execute(
                text("SELECT reserved_cost_micros FROM model_cost_liabilities")
            ).scalar_one()
            == 1267
        )


def valid_quote():
    return {
        "version": "synthetic-v1",
        "input_rate_micros_per_million": 1_000_000,
        "output_rate_micros_per_million": 1_000_000,
        "max_input_tokens": 1000,
        "max_output_tokens": 1000,
        "output_limit_parameter": "max_completion_tokens",
        "token_estimator": "utf8-json-bytes-framing-v1",
        "operation_policy_version": "synthetic-v1",
        "admission_policy_version": "synthetic-v1",
        "authorized_ceiling_micros": 5000,
    }


def insert_liability(engine, **updates):
    values = {
        "provider": "openai",
        "quote": json.dumps(valid_quote()),
        "input_tokens": 5,
        "output_tokens": 12,
        "provenance": "chat-completion-including-reasoning-v1",
        "reserved": 1267,
        "settled": 17,
        "estimate": 267,
        "state": "settled",
        "endpoint": "0" * 64,
    }
    values.update(updates)
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO model_cost_liabilities (id,financial_group_id,attempt_number,provider,model,endpoint_key,currency,pricing_quote,input_token_estimate,reserved_cost_micros,settled_cost_micros,input_tokens,output_tokens,usage_provenance,cost_state,created_at,settled_at) VALUES ('private','private',1,:provider,'synthetic',:endpoint,'USD',CAST(:quote AS json),:estimate,:reserved,:settled,:input_tokens,:output_tokens,:provenance,:state,now(),now())"
            ),
            values,
        )


def test_settled_detached_liability_remains_without_blocking_sql_gate(context):
    engine = context[0]
    _migrate(engine, "20261009_0010")
    insert_liability(engine)
    result = report(context, expected_database_schema="20261009_0010")
    assert result["database_gate_passed"] and not result["cutover_ready"]
    assert result["observed_database"]["liabilities"]["row_count"] == 1
    assert result["observed_database"]["liabilities"]["settled_cost_micros"] == 17


def test_database_transaction_actually_rejects_writes(context):
    engine = context[0].cutover_readonly_observer
    attempted = []

    def before_execute(connection, cursor, statement, parameters, context_, executemany):
        if statement.startswith("SELECT version_num"):
            attempted.append(True)
            cursor.execute("CREATE TABLE preflight_must_never_write (id int)")

    event.listen(engine, "before_cursor_execute", before_execute)
    try:
        with pytest.raises(Exception) as caught:
            report(context)
        assert "read-only" in str(caught.value)
        assert attempted == [True]
    finally:
        event.remove(engine, "before_cursor_execute", before_execute)
    with engine.connect() as connection:
        assert (
            connection.execute(
                text("SELECT to_regclass('preflight_must_never_write')")
            ).scalar_one()
            is None
        )


def test_missing_hash_bound_inventory_cli_never_attempts_database(tmp_path):
    sentinel = "SECRET-MUST-NOT-APPEAR"
    environment = {**os.environ, "HIREWIZ_CUTOVER_DATABASE_URL": sentinel}
    command = [
        str(Path(__file__).resolve().parents[1] / ".venv/bin/python"),
        str(Path(preflight.__file__)),
        str(tmp_path / "missing.json"),
        "--inventory-sha256",
        "0" * 64,
    ]
    completed = subprocess.run(
        command, env=environment, text=True, capture_output=True, timeout=10, check=False
    )
    assert completed.returncode == 65
    assert json.loads(completed.stdout)["error"] == "invalid_input_or_environment"
    assert sentinel not in completed.stdout + completed.stderr


@pytest.mark.parametrize("state", ["reserved", "future_unknown"])
def test_paid_service_hold_or_unknown_state_is_not_dropped_from_obligations(context, state):
    engine = context[0]
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO service_credit_reservations (id,operation,source_id,unit_price,requested_count,reserved_amount,committed_amount,released_amount,state,pricing_version,created_at) VALUES ('private','employer.search','private',2,3,6,0,0,:state,'synthetic',now())"
            ),
            {"state": state},
        )
    result = report(context)
    assert_refused(result, "unresolved_obligations")
    assert result["observed_database"]["pending_service_credit_hold_count"] == 1


def test_hash_mismatch_expired_and_duplicate_inventory_are_rejected(context, tmp_path):
    data = copy.deepcopy(context[3])
    path = tmp_path / "inventory.json"
    path.write_text(json.dumps(data))
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    with pytest.raises(preflight.Denied, match="inventory_hash_mismatch"):
        preflight.load_inventory(path, "0" * 64, datetime.now(UTC))
    loaded, actual = preflight.load_inventory(path, digest, datetime.now(UTC))
    assert actual == digest and loaded.serving_commit == "e" * 40
    data["reviewed_at"] = "2000-01-01T00:00:00+00:00"
    path.write_text(json.dumps(data))
    with pytest.raises(preflight.Denied, match="inventory_expired_or_future"):
        preflight.load_inventory(
            path, hashlib.sha256(path.read_bytes()).hexdigest(), datetime.now(UTC)
        )
    path.write_text('{"format_version":1,"format_version":1}')
    with pytest.raises(preflight.Denied, match="inventory_duplicate_key"):
        preflight.load_inventory(
            path, hashlib.sha256(path.read_bytes()).hexdigest(), datetime.now(UTC)
        )


def test_unexpected_login_writer_outside_reviewed_role_aliases_refuses(context):
    engine, _, _, data = context
    stranger = "cutover_stranger_" + uuid4().hex
    try:
        with engine.begin() as connection:
            connection.execute(text(f'CREATE ROLE "{stranger}" LOGIN'))
            connection.execute(
                text(
                    f'GRANT INSERT ON "{data["database_namespace"]}".analysis_runs TO "{stranger}"'
                )
            )
        assert_refused(report(context), "unclassified_database_writers_or_sessions")
    finally:
        with engine.begin() as connection:
            connection.execute(text(f'DROP OWNED BY "{stranger}"'))
            connection.execute(text(f'DROP ROLE "{stranger}"'))


def test_actual_database_cli_separates_assertions_and_never_prints_role_or_url_values(
    context, tmp_path
):
    engine, roles, operator, data = context
    inventory = tmp_path / "inventory.json"
    inventory.write_text(json.dumps(data))
    digest = hashlib.sha256(inventory.read_bytes()).hexdigest()
    environment = {
        **os.environ,
        "HIREWIZ_CUTOVER_DATABASE_URL": engine.cutover_readonly_observer.url.render_as_string(
            hide_password=False
        ),
    }
    for alias, name in data["role_env"].items():
        environment[name] = roles[alias]
    environment["HIREWIZ_CUTOVER_OPERATOR_LOCAL"] = operator
    command = [
        str(Path(__file__).resolve().parents[1] / ".venv/bin/python"),
        str(Path(preflight.__file__)),
        str(inventory),
        "--inventory-sha256",
        digest,
    ]
    completed = subprocess.run(
        command, env=environment, text=True, capture_output=True, timeout=30, check=False
    )
    assert completed.returncode == 0
    result = json.loads(completed.stdout)
    assert result["database_gate_passed"] and not result["cutover_ready"]
    assert result["caller_assertions_not_verified"]["provider_fencing_verified"] is True
    assert result["caller_release_pins_not_verified"]["candidate_commit"] == "a" * 40
    for sensitive in (
        *roles.values(),
        operator,
        engine.cutover_readonly_observer.url.render_as_string(hide_password=False),
    ):
        assert sensitive not in completed.stdout + completed.stderr
    environment["HIREWIZ_CUTOVER_DATABASE_URL"] = (
        "postgresql+psycopg://not_real:REDACTION_SENTINEL@127.0.0.1:1/secret_database"
    )
    refused = subprocess.run(
        command, env=environment, text=True, capture_output=True, timeout=10, check=False
    )
    assert refused.returncode == 65
    assert json.loads(refused.stdout)["error"] == "database_observation_unavailable"
    assert "REDACTION_SENTINEL" not in refused.stdout + refused.stderr
    assert "secret_database" not in refused.stdout + refused.stderr


def test_writable_observer_refuses_even_when_explicitly_classified_as_operator(context):
    engine, roles, operator, data = context
    inventory = preflight.Inventory.model_validate(data)
    observed = preflight.observe(engine, inventory, roles, (operator,))
    result = preflight.evaluate(inventory, "0" * 64, observed, datetime.now(UTC))
    assert observed["inspector_has_writer_authority"] is True
    assert_refused(result, "database_observer_has_writer_authority")


@pytest.mark.parametrize("path", ["direct_set", "two_hop_set", "admin_without_set"])
def test_actual_set_or_admin_enabled_role_write_is_never_classified_readonly(context, path):
    engine, _, _, data = context
    namespace = data["database_namespace"]
    parent, middle, child = ["cutover_capability_" + uuid4().hex for _ in range(3)]
    login = None
    try:
        with engine.begin() as connection:
            for role in (parent, middle):
                connection.execute(text(f'CREATE ROLE "{role}" NOLOGIN NOINHERIT'))
            connection.execute(
                text(
                    f"CREATE ROLE \"{child}\" LOGIN NOINHERIT PASSWORD 'synthetic-capability-only'"
                )
            )
            connection.execute(text(f'CREATE TABLE "{namespace}".capability_probe (id int)'))
            connection.execute(text(f'GRANT USAGE ON SCHEMA "{namespace}" TO "{parent}"'))
            connection.execute(
                text(f'GRANT INSERT ON "{namespace}".capability_probe TO "{parent}"')
            )
            if path == "two_hop_set":
                connection.execute(
                    text(f'GRANT "{parent}" TO "{middle}" WITH INHERIT FALSE, SET TRUE')
                )
                connection.execute(
                    text(f'GRANT "{middle}" TO "{child}" WITH INHERIT FALSE, SET TRUE')
                )
            elif path == "admin_without_set":
                connection.execute(
                    text(f'GRANT "{parent}" TO "{child}" WITH ADMIN TRUE, INHERIT FALSE, SET FALSE')
                )
            else:
                connection.execute(
                    text(f'GRANT "{parent}" TO "{child}" WITH INHERIT FALSE, SET TRUE')
                )
        login = create_engine(
            engine.url.set(username=child, password="synthetic-capability-only"), poolclass=NullPool
        )
        with login.begin() as connection:
            if path == "admin_without_set":
                # Real administration can enable a previously unreachable path.
                connection.execute(text(f'GRANT "{parent}" TO "{child}" WITH SET TRUE'))
            connection.execute(text(f'SET ROLE "{parent}"'))
            connection.execute(text(f'INSERT INTO "{namespace}".capability_probe VALUES (1)'))
        login.dispose()
        with engine.begin() as connection:
            if path == "admin_without_set":
                connection.execute(text(f'GRANT "{parent}" TO "{child}" WITH SET FALSE'))
            assert not connection.execute(
                text("SELECT has_table_privilege(:role,:table,'INSERT')"),
                {"role": child, "table": namespace + ".capability_probe"},
            ).scalar_one()
            assert (
                connection.execute(
                    text(f'SELECT count(*) FROM "{namespace}".capability_probe')
                ).scalar_one()
                == 1
            )
        result = report(context)
        assert result["observed_database"]["unexpected_writer_login_role_count"] >= 1
        assert_refused(result, "unclassified_database_writers_or_sessions")
    finally:
        if login is not None:
            login.dispose()
        with engine.begin() as connection:
            for role in (child, middle, parent):
                connection.execute(text(f'DROP OWNED BY "{role}"'))
                connection.execute(text(f'DROP ROLE "{role}"'))


def test_actual_column_only_update_is_never_omitted_from_writer_inventory(context):
    engine, _, _, data = context
    namespace = data["database_namespace"]
    child = "cutover_column_" + uuid4().hex
    login = None
    try:
        with engine.begin() as connection:
            connection.execute(
                text(f"CREATE ROLE \"{child}\" LOGIN PASSWORD 'synthetic-column-only'")
            )
            connection.execute(
                text(f'CREATE TABLE "{namespace}".column_probe (id int, marker text)')
            )
            connection.execute(
                text(f"INSERT INTO \"{namespace}\".column_probe VALUES (1,'before')")
            )
            connection.execute(text(f'GRANT USAGE ON SCHEMA "{namespace}" TO "{child}"'))
            connection.execute(
                text(f'GRANT UPDATE (marker) ON "{namespace}".column_probe TO "{child}"')
            )
        login = create_engine(
            engine.url.set(username=child, password="synthetic-column-only"), poolclass=NullPool
        )
        with login.begin() as connection:
            connection.execute(text(f"UPDATE \"{namespace}\".column_probe SET marker='after'"))
        login.dispose()
        with engine.connect() as connection:
            assert not connection.execute(
                text("SELECT has_table_privilege(:role,:table,'UPDATE')"),
                {"role": child, "table": namespace + ".column_probe"},
            ).scalar_one()
            assert (
                connection.execute(
                    text(f'SELECT marker FROM "{namespace}".column_probe')
                ).scalar_one()
                == "after"
            )
        result = report(context)
        assert result["observed_database"]["unexpected_writer_login_role_count"] >= 1
        assert_refused(result, "unclassified_database_writers_or_sessions")
    finally:
        if login is not None:
            login.dispose()
        with engine.begin() as connection:
            connection.execute(text(f'DROP OWNED BY "{child}"'))
            connection.execute(text(f'DROP ROLE "{child}"'))


@pytest.mark.parametrize(
    "updates",
    [
        {"input_tokens": None},
        {"output_tokens": None},
        {"input_tokens": -1},
        {"output_tokens": -1},
        {"provenance": None},
        {"provenance": "future_unknown_usage"},
        {"provider": "future_provider"},
        {"settled": 18},
        {"quote": "{}"},
        {"endpoint": "not-a-digest"},
        {"estimate": -1},
    ],
)
def test_settled_label_does_not_resolve_invalid_usage_provenance_quote_or_arithmetic(
    context, updates
):
    engine = context[0]
    _migrate(engine, "20261009_0010")
    insert_liability(engine, **updates)
    result = report(context, expected_database_schema="20261009_0010")
    assert_refused(result, "unresolved_obligations")
    assert result["observed_database"]["liabilities"]["unresolved_count"] == 1
    assert result["observed_database"]["liabilities"]["held_cost_micros"] == 1267
    assert result["observed_database"]["liabilities"]["settled_cost_micros"] == 0


def test_unknown_terminal_run_cost_state_is_not_assumed_settled(context):
    engine = context[0]
    _migrate(engine, "20261009_0010")
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO users (id,email,password_hash,ai_credits,job_service_credits) VALUES (701,'synthetic@example.invalid','not-a-password',50,0)"
            )
        )
        connection.execute(
            text(
                "INSERT INTO analysis_runs (id,user_id,operation,status,idempotency_key,input_fingerprint,input_payload,estimated_units,committed_units,usage_state,attempt_count,generation_attempt_limit,generation_attempt_count,cancel_requested,created_at,updated_at,model_cost_reserved_micros,model_cost_settled_micros,model_cost_state) VALUES ('private_obligation',701,'resume.tailor','succeeded','synthetic','synthetic','{}',1,0,'settled',0,3,0,false,now(),now(),0,0,'future_unknown')"
            )
        )
    result = report(context, expected_database_schema="20261009_0010")
    assert_refused(result, "unresolved_obligations")
    assert result["observed_database"]["invalid_or_held_model_budget_run_count"] == 1


@pytest.mark.parametrize(
    "provider,provenance,parameter",
    [
        ("google", "google-total-including-thoughts-v1", "max_output_tokens"),
        ("google", "google-explicit-thoughts-v1", "max_output_tokens"),
        ("openai_compatible", "chat-completion-including-reasoning-v1", "max_tokens"),
    ],
)
def test_current_supported_usage_contracts_can_resolve_frozen_financial_record(
    context, provider, provenance, parameter
):
    engine = context[0]
    _migrate(engine, "20261009_0010")
    quote = valid_quote()
    quote["output_limit_parameter"] = parameter
    insert_liability(engine, provider=provider, provenance=provenance, quote=json.dumps(quote))
    result = report(context, expected_database_schema="20261009_0010")
    assert result["database_gate_passed"] and not result["cutover_ready"]
    assert result["observed_database"]["liabilities"]["unresolved_count"] == 0
    assert result["observed_database"]["liabilities"]["settled_cost_micros"] == 17


def test_unknown_terminal_model_call_financial_state_refuses(context):
    engine = context[0]
    _migrate(engine, "20261009_0010")
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO users (id,email,password_hash,ai_credits,job_service_credits) VALUES (701,'synthetic@example.invalid','not-a-password',50,0)"
            )
        )
        connection.execute(
            text(
                "INSERT INTO analysis_runs (id,user_id,operation,status,idempotency_key,input_fingerprint,input_payload,estimated_units,committed_units,usage_state,attempt_count,generation_attempt_limit,generation_attempt_count,cancel_requested,created_at,updated_at,model_cost_reserved_micros,model_cost_settled_micros,model_cost_state) VALUES ('private_obligation',701,'resume.tailor','succeeded','synthetic','synthetic','{}',1,0,'settled',0,3,0,false,now(),now(),0,0,'unquoted')"
            )
        )
        connection.execute(
            text(
                "INSERT INTO model_call_events (id,analysis_run_id,user_id,provider,model,prompt_version,input_tokens,output_tokens,tokens_estimated,latency_ms,status,created_at,cost_state) VALUES ('private_call','private_obligation',701,'synthetic','synthetic','synthetic',0,0,true,0,'succeeded',now(),'future_unknown')"
            )
        )
    result = report(context, expected_database_schema="20261009_0010")
    assert_refused(result, "unresolved_obligations")
    assert result["observed_database"]["invalid_model_call_financial_state_count"] == 1


def terminal_run(engine):
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO users (id,email,password_hash,ai_credits,job_service_credits) VALUES (701,'synthetic@example.invalid','not-a-password',50,0)"
            )
        )
        connection.execute(
            text(
                "INSERT INTO analysis_runs (id,user_id,operation,status,idempotency_key,input_fingerprint,input_payload,estimated_units,committed_units,usage_state,attempt_count,generation_attempt_limit,generation_attempt_count,cancel_requested,created_at,updated_at,model_cost_reserved_micros,model_cost_settled_micros,model_cost_state) VALUES ('private_obligation',701,'resume.tailor','failed','synthetic','synthetic','{}',1,0,'settled',0,3,0,false,now(),now(),0,0,'unquoted')"
            )
        )


def unavailable_call(engine, estimated=None, **fields):
    allowed = {
        "reserved_cost_micros",
        "settled_cost_micros",
        "settled_at",
        "pricing_quote",
        "liability_id",
        "token_estimate_provenance",
        "usage_provenance",
        "output_token_limit",
    }
    assert set(fields).issubset(allowed)
    columns = ",".join(fields)
    values = ",".join(
        "CAST(:pricing_quote AS json)" if field == "pricing_quote" else ":" + field
        for field in fields
    )
    extra_columns = "," + columns if fields else ""
    extra_values = "," + values if fields else ""
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO model_call_events (id,analysis_run_id,user_id,provider,model,prompt_version,input_tokens,output_tokens,tokens_estimated,attempt_number,latency_ms,status,cost_state,estimated_cost_micros,created_at"
                + extra_columns
                + ") VALUES ('private_call','private_obligation',701,'openai','synthetic','synthetic',0,0,true,1,0,'failed','unavailable',:estimated,now()"
                + extra_values
                + ")"
            ),
            {"estimated": estimated, **fields},
        )


@pytest.mark.parametrize(
    "field,value",
    [
        ("reserved_cost_micros", 1267),
        ("reserved_cost_micros", 0),
        ("settled_cost_micros", 0),
        ("settled_at", datetime.now(UTC)),
        ("pricing_quote", "{}"),
        ("liability_id", "private"),
        ("token_estimate_provenance", "logical-character-estimate-v1"),
        ("usage_provenance", "chat-completion-including-reasoning-v1"),
        ("output_token_limit", 1000),
    ],
)
def test_unavailable_with_any_new_financial_metadata_is_not_legacy_history(context, field, value):
    engine = context[0]
    _migrate(engine, "20261009_0010")
    terminal_run(engine)
    if field == "liability_id":
        insert_liability(engine)
    unavailable_call(engine, **{field: value})
    result = report(context, expected_database_schema="20261009_0010")
    assert_refused(result, "unresolved_obligations")
    assert result["observed_database"]["invalid_model_call_financial_state_count"] == 1
    with engine.connect() as connection:
        assert connection.execute(text("SELECT count(*) FROM model_call_events")).scalar_one() == 1


@pytest.mark.parametrize("estimated", [None, 0, 37])
def test_genuine_legacy_unavailable_keeps_old_estimate_but_never_proves_cost(context, estimated):
    engine = context[0]
    _migrate(engine, "20261009_0010")
    terminal_run(engine)
    unavailable_call(engine, estimated=estimated)
    result = report(context, expected_database_schema="20261009_0010")
    assert result["database_gate_passed"] and not result["cutover_ready"]
    assert result["observed_database"]["invalid_model_call_financial_state_count"] == 0
    assert result["observed_database"]["legacy_model_event_count"] == 1
    assert not result["observed_database"]["legacy_history_is_cost_proof"]
    with engine.connect() as connection:
        assert (
            connection.execute(
                text("SELECT estimated_cost_micros FROM model_call_events")
            ).scalar_one()
            == estimated
        )


def group_liabilities(engine, specs):
    from datetime import timedelta

    base = datetime.now(UTC).replace(tzinfo=None) - timedelta(hours=1)
    with engine.begin() as connection:
        for index, spec in enumerate(specs, 1):
            quote = valid_quote()
            quote["max_output_tokens"] = spec.get("maximum_output", 1000)
            quote["authorized_ceiling_micros"] = spec.get("ceiling", 5000)
            quote["admission_policy_version"] = f"synthetic-admission-{index}"
            output = spec.get("output", 12)
            values = {
                "id": f"private_{10 - index}",
                "group": spec.get("group", "private_group"),
                "attempt": spec.get("attempt", index),
                "quote": json.dumps(quote),
                "reserved": 267 + quote["max_output_tokens"],
                "settled": 5 + output,
                "output": output,
                "created": base + timedelta(seconds=spec.get("created", index * 2)),
                "completed": base + timedelta(seconds=spec.get("completed", index * 2 + 1)),
            }
            connection.execute(
                text(
                    "INSERT INTO model_cost_liabilities (id,financial_group_id,attempt_number,provider,model,endpoint_key,currency,pricing_quote,input_token_estimate,reserved_cost_micros,settled_cost_micros,input_tokens,output_tokens,usage_provenance,cost_state,created_at,settled_at) VALUES (:id,:group,:attempt,'openai','synthetic',:endpoint,'USD',CAST(:quote AS json),267,:reserved,:settled,5,:output,'chat-completion-including-reasoning-v1','settled',:created,:completed)"
                ),
                {"endpoint": "0" * 64, **values},
            )


def test_same_detached_group_cannot_overspend_each_recorded_authorization(context):
    engine = context[0]
    _migrate(engine, "20261009_0010")
    group_liabilities(
        engine, [{"ceiling": 1500, "output": 1000}, {"ceiling": 1500, "output": 1000}]
    )
    result = report(context, expected_database_schema="20261009_0010")
    assert_refused(result, "unresolved_obligations")
    summary = result["observed_database"]["liabilities"]
    assert summary["invalid_group_count"] == 1 and summary["unresolved_count"] == 2
    assert summary["recorded_individually_valid_settled_cost_micros"] == 2010
    with engine.connect() as connection:
        assert (
            connection.execute(
                text("SELECT sum(settled_cost_micros) FROM model_cost_liabilities")
            ).scalar_one()
            == 2010
        )


@pytest.mark.parametrize(
    "specs",
    [
        [{"ceiling": 1267, "output": 1000}, {"ceiling": 2272, "output": 1000}],
        [{"ceiling": 2267, "maximum_output": 2000}, {"ceiling": 1284}],
        [
            {"ceiling": 2534, "output": 1000, "created": 0, "completed": 3},
            {"ceiling": 2534, "output": 1000, "created": 1, "completed": 4},
        ],
    ],
)
def test_legitimate_changed_caps_and_full_prior_holds_pass_exact_admission_boundary(context, specs):
    engine = context[0]
    _migrate(engine, "20261009_0010")
    group_liabilities(engine, specs)
    result = report(context, expected_database_schema="20261009_0010")
    assert result["database_gate_passed"] and not result["cutover_ready"]
    summary = result["observed_database"]["liabilities"]
    assert summary["invalid_group_count"] == 0 and summary["unresolved_count"] == 0
    assert summary["row_count"] == 2 and summary["settled_cost_micros"] > 0


@pytest.mark.parametrize(
    "specs",
    [
        [{"ceiling": 1267, "output": 1000}, {"ceiling": 2271, "output": 1000}],
        [
            {"ceiling": 2533, "output": 1000, "created": 0, "completed": 3},
            {"ceiling": 2533, "output": 1000, "created": 1, "completed": 4},
        ],
        [{"attempt": 1}, {"attempt": 3}],
        [{"created": 2, "completed": 3}, {"created": 1, "completed": 2}],
        [{"group": ""}],
    ],
)
def test_unverifiable_or_over_budget_admission_sequence_stays_blocked(context, specs):
    engine = context[0]
    _migrate(engine, "20261009_0010")
    group_liabilities(engine, specs)
    result = report(context, expected_database_schema="20261009_0010")
    assert_refused(result, "unresolved_obligations")
    assert result["observed_database"]["liabilities"]["invalid_group_count"] >= 1


@pytest.mark.parametrize(
    "quote", ["null", "[]", '{"version":"x","input_rate_micros_per_million":true}']
)
def test_malformed_frozen_quote_never_creates_a_clear_financial_gate(context, quote):
    engine = context[0]
    _migrate(engine, "20261009_0010")
    insert_liability(engine, quote=quote)
    result = report(context, expected_database_schema="20261009_0010")
    assert_refused(result, "unresolved_obligations")


@pytest.mark.parametrize("kind", ["version_whitespace", "zero_estimate", "empty_model"])
def test_malformed_frozen_identity_and_estimator_metadata_stay_unresolved(context, kind):
    engine = context[0]
    _migrate(engine, "20261009_0010")
    quote = valid_quote()
    updates = {}
    if kind == "version_whitespace":
        quote["version"] = "not a source policy identifier"
        updates["quote"] = json.dumps(quote)
    elif kind == "zero_estimate":
        updates.update(estimate=0, reserved=1000)
    insert_liability(engine, **updates)
    if kind == "empty_model":
        with engine.begin() as connection:
            connection.execute(text("UPDATE model_cost_liabilities SET model=''"))
    result = report(context, expected_database_schema="20261009_0010")
    assert_refused(result, "unresolved_obligations")


def _create_credit_definer(connection, namespace, kind="FUNCTION"):
    connection.execute(
        text(
            "INSERT INTO users (id,email,password_hash,ai_credits,job_service_credits) "
            "VALUES (901,'definer-probe@example.invalid','synthetic-only',50,0)"
        )
    )
    result_type = " RETURNS void" if kind == "FUNCTION" else ""
    connection.execute(
        text(
            f'CREATE {kind} "{namespace}".synthetic_credit_write(){result_type} '
            f"LANGUAGE sql SECURITY DEFINER SET search_path=pg_catalog AS $$ "
            f'UPDATE "{namespace}".users SET job_service_credits=job_service_credits+7 '
            f"WHERE id=901 $$"
        )
    )
    connection.execute(
        text(f'REVOKE ALL ON {kind} "{namespace}".synthetic_credit_write() FROM PUBLIC')
    )


def _assert_actual_definer_write(engine, namespace, kind="FUNCTION", set_role=None):
    with engine.begin() as connection:
        if set_role:
            connection.execute(text(f'SET ROLE "{set_role}"'))
        assert not connection.execute(
            text("SELECT has_table_privilege(current_user,:table,'INSERT,UPDATE,DELETE,TRUNCATE')"),
            {"table": namespace + ".users"},
        ).scalar_one()
        assert not connection.execute(
            text("SELECT has_any_column_privilege(current_user,:table,'INSERT,UPDATE,REFERENCES')"),
            {"table": namespace + ".users"},
        ).scalar_one()
        assert not connection.execute(
            text("SELECT has_schema_privilege(current_user,:namespace,'CREATE')"),
            {"namespace": namespace},
        ).scalar_one()
        connection.execute(
            text(
                f"{'SELECT' if kind == 'FUNCTION' else 'CALL'} "
                f'"{namespace}".synthetic_credit_write()'
            )
        )


@pytest.mark.parametrize("kind", ["FUNCTION", "PROCEDURE"])
def test_execute_only_observer_is_writer_despite_readonly_observation_transaction(context, kind):
    admin, _, _, data = context
    namespace = data["database_namespace"]
    observer = admin.cutover_readonly_observer
    with admin.begin() as connection:
        _create_credit_definer(connection, namespace, kind)
        connection.execute(
            text(
                f'GRANT EXECUTE ON {kind} "{namespace}".synthetic_credit_write() '
                f'TO "{observer.url.username}"'
            )
        )
    _assert_actual_definer_write(observer, namespace, kind)
    with admin.connect() as connection:
        assert (
            connection.execute(
                text("SELECT job_service_credits FROM users WHERE id=901")
            ).scalar_one()
            == 7
        )
    with observer.connect() as connection:
        connection.exec_driver_sql("SET TRANSACTION READ ONLY")
        with pytest.raises(Exception) as caught:
            connection.execute(
                text(
                    f'{"SELECT" if kind == "FUNCTION" else "CALL"} "{namespace}".synthetic_credit_write()'
                )
            )
        assert getattr(getattr(caught.value, "orig", caught.value), "sqlstate", None) == "25006"
    result = report(context)
    assert_refused(result, "database_observer_has_writer_authority")
    observed = result["observed_database"]
    assert observed["inspector_has_writer_authority"]
    assert observed["inspector_executable_security_definer_count"] == 1
    assert observed["inspector_executable_security_definer_known_writer_owner_count"] == 1
    assert observed["unexpected_writer_login_role_count"] >= 1
    assert observed["security_definer_inventory"]["body_read_or_audited"] is False
    assert "synthetic_credit_write" not in json.dumps(result)


def test_public_cross_namespace_security_definer_is_reachable_writer_authority(context):
    admin, _, _, data = context
    namespace = data["database_namespace"]
    other = "cutover_routine_" + uuid4().hex
    observer = admin.cutover_readonly_observer
    try:
        with admin.begin() as connection:
            connection.execute(text(f'CREATE SCHEMA "{other}"'))
            connection.execute(text(f'GRANT USAGE ON SCHEMA "{other}" TO PUBLIC'))
            connection.execute(
                text(
                    "INSERT INTO users (id,email,password_hash,ai_credits,job_service_credits) VALUES (901,'definer-cross@example.invalid','synthetic-only',50,0)"
                )
            )
            connection.execute(
                text(
                    f'CREATE FUNCTION "{other}".synthetic_cross_write() RETURNS void LANGUAGE sql SECURITY DEFINER SET search_path=pg_catalog AS $$ UPDATE "{namespace}".users SET job_service_credits=job_service_credits+7 WHERE id=901 $$'
                )
            )
        with observer.begin() as connection:
            connection.execute(text(f'SELECT "{other}".synthetic_cross_write()'))
        with admin.connect() as connection:
            assert (
                connection.execute(
                    text("SELECT job_service_credits FROM users WHERE id=901")
                ).scalar_one()
                == 7
            )
        result = report(context)
        assert_refused(result, "database_observer_has_writer_authority")
        assert_refused(result, "retired_database_role_not_fenced")
        assert (
            result["observed_database"]["roles"]["retired"]["executable_security_definer_count"]
            == 1
        )
    finally:
        with admin.begin() as connection:
            connection.execute(text(f'DROP SCHEMA IF EXISTS "{other}" CASCADE'))


@pytest.mark.parametrize("path", ["inherited", "set", "two_hop_set"])
def test_unclassified_role_inherited_or_set_definer_executes_real_write(context, path):
    admin, _, _, data = context
    namespace = data["database_namespace"]
    child = "cutover_definer_" + uuid4().hex
    executor = "cutover_definer_" + uuid4().hex
    middle = "cutover_definer_" + uuid4().hex
    created = [executor, middle, child]
    candidate = None
    try:
        with admin.begin() as connection:
            for name in created:
                mode = "LOGIN" if name == child else "NOLOGIN"
                inherited = "INHERIT" if path == "inherited" else "NOINHERIT"
                connection.execute(
                    text(
                        f"CREATE ROLE \"{name}\" {mode} {inherited} PASSWORD 'synthetic-definer-only'"
                    )
                )
                connection.execute(text(f'GRANT USAGE ON SCHEMA "{namespace}" TO "{name}"'))
            _create_credit_definer(connection, namespace)
            connection.execute(
                text(
                    f'GRANT EXECUTE ON FUNCTION "{namespace}".synthetic_credit_write() TO "{executor}"'
                )
            )
            if path == "two_hop_set":
                connection.execute(
                    text(f'GRANT "{executor}" TO "{middle}" WITH INHERIT FALSE, SET TRUE')
                )
                connection.execute(
                    text(f'GRANT "{middle}" TO "{child}" WITH INHERIT FALSE, SET TRUE')
                )
            elif path == "set":
                connection.execute(
                    text(f'GRANT "{executor}" TO "{child}" WITH INHERIT FALSE, SET TRUE')
                )
            else:
                connection.execute(
                    text(f'GRANT "{executor}" TO "{child}" WITH INHERIT TRUE, SET FALSE')
                )
        candidate = create_engine(
            admin.url.set(username=child, password="synthetic-definer-only"),
            poolclass=NullPool,
            connect_args={"options": f"-csearch_path={namespace}"},
        )
        _assert_actual_definer_write(
            candidate, namespace, set_role=executor if path != "inherited" else None
        )
        with admin.connect() as connection:
            assert (
                connection.execute(
                    text("SELECT job_service_credits FROM users WHERE id=901")
                ).scalar_one()
                == 7
            )
        result = report(context)
        assert_refused(result, "unclassified_database_writers_or_sessions")
        assert not result["observed_database"]["inspector_has_writer_authority"]
        assert result["observed_database"]["unexpected_writer_login_role_count"] >= 1
    finally:
        if candidate is not None:
            candidate.dispose()
        with admin.begin() as connection:
            for name in created:
                connection.execute(text(f'DROP OWNED BY "{name}"'))
                connection.execute(text(f'DROP ROLE "{name}"'))


def test_nonexecutable_definer_keeps_genuine_select_stats_observer_clear(context):
    admin, _, _, data = context
    with admin.begin() as connection:
        _create_credit_definer(connection, data["database_namespace"])
    result = report(context)
    assert result["database_gate_passed"] is True
    assert not result["cutover_ready"]
    assert not result["observed_database"]["inspector_has_writer_authority"]
    assert result["observed_database"]["security_definer_inventory"]["routine_count"] == 1


def test_apparently_harmless_definer_with_nonwriter_owner_still_refuses(context):
    admin, _, _, data = context
    namespace = data["database_namespace"]
    owner = "cutover_definer_owner_" + uuid4().hex
    try:
        with admin.begin() as connection:
            connection.execute(text(f'CREATE ROLE "{owner}" NOLOGIN'))
            connection.execute(text(f'GRANT USAGE ON SCHEMA "{namespace}" TO "{owner}"'))
            connection.execute(
                text(
                    f'CREATE FUNCTION "{namespace}".synthetic_select_only() RETURNS integer LANGUAGE sql SECURITY DEFINER AS $$ SELECT 7 $$'
                )
            )
            connection.execute(
                text(f'REVOKE ALL ON FUNCTION "{namespace}".synthetic_select_only() FROM PUBLIC')
            )
            connection.execute(
                text(f'ALTER FUNCTION "{namespace}".synthetic_select_only() OWNER TO "{owner}"')
            )
            connection.execute(
                text(
                    f'GRANT EXECUTE ON FUNCTION "{namespace}".synthetic_select_only() TO "{admin.cutover_readonly_observer.url.username}"'
                )
            )
        with admin.cutover_readonly_observer.connect() as connection:
            assert (
                connection.execute(
                    text(f'SELECT "{namespace}".synthetic_select_only()')
                ).scalar_one()
                == 7
            )
        result = report(context)
        assert_refused(result, "database_observer_has_writer_authority")
        assert result["observed_database"]["inspector_executable_security_definer_count"] == 1
        assert (
            result["observed_database"][
                "inspector_executable_security_definer_known_writer_owner_count"
            ]
            == 0
        )
    finally:
        with admin.begin() as connection:
            connection.execute(text(f'DROP OWNED BY "{owner}"'))
            connection.execute(text(f'DROP ROLE "{owner}"'))


def test_unsupported_definer_language_inventory_refuses(context):
    admin, _, _, data = context
    namespace = data["database_namespace"]
    with admin.begin() as connection:
        connection.execute(
            text(
                f"CREATE FUNCTION \"{namespace}\".synthetic_internal(integer) RETURNS integer LANGUAGE internal SECURITY DEFINER AS 'int4inc'"
            )
        )
        connection.execute(
            text(f'REVOKE ALL ON FUNCTION "{namespace}".synthetic_internal(integer) FROM PUBLIC')
        )
    with pytest.raises(preflight.Denied, match="database_function_inventory_unsupported"):
        report(context)


def test_overbound_definer_inventory_refuses_even_without_observer_execute(context):
    admin, _, _, data = context
    namespace = data["database_namespace"]
    with admin.begin() as connection:
        for index in range(257):
            connection.execute(
                text(
                    f'CREATE FUNCTION "{namespace}".synthetic_bound_{index}() RETURNS integer LANGUAGE sql SECURITY DEFINER AS $$ SELECT 1 $$'
                )
            )
            connection.execute(
                text(f'REVOKE ALL ON FUNCTION "{namespace}".synthetic_bound_{index}() FROM PUBLIC')
            )
    with pytest.raises(preflight.Denied, match="database_function_inventory_bound"):
        report(context)


def test_fault_injected_missing_definer_projection_refuses_on_actual_catalog_count(context):
    admin, _, _, data = context
    with admin.begin() as connection:
        _create_credit_definer(connection, data["database_namespace"])

    def omit_projection(connection, cursor, statement, parameters, execution_context, many):
        if statement.startswith("SELECT p.oid, p.proowner, p.prokind"):
            statement = statement.replace(
                "WHERE p.prosecdef ORDER", "WHERE p.prosecdef AND false ORDER"
            )
        return statement, parameters

    observing = admin.cutover_readonly_observer
    event.listen(observing, "before_cursor_execute", omit_projection, retval=True)
    try:
        with pytest.raises(preflight.Denied, match="database_function_inventory_incomplete"):
            report(context)
    finally:
        event.remove(observing, "before_cursor_execute", omit_projection)
