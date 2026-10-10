"""Pinned retirement identities and real deletion edges in the owned PG17 fixture."""

from __future__ import annotations

import copy
from contextlib import contextmanager
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from backend.tests.test_monetary_cutover_preflight_postgres import (
    assert_refused,
    report,
)
from backend.tests.test_monetary_cutover_preflight_postgres import (
    context as context,
)
from backend.tests.test_monetary_cutover_preflight_postgres import (
    pg_engine as pg_engine,
)
from pydantic import ValidationError
from sqlalchemy import create_engine, text
from sqlalchemy.pool import NullPool

from scripts import monetary_cutover_preflight as preflight


def inventory_data():
    return {
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
        "expected_system_identifier_sha256": "c" * 64,
        "expected_database_oid": 1,
        "database_namespace": "owned_schema",
        "role_env": {
            alias: "HIREWIZ_CUTOVER_ROLE_" + alias.upper()
            for alias in ("retired", "replacement_runtime", "replacement_migration")
        },
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


def test_existing_inventory_defaults_to_disabled_role_mode():
    inventory = preflight.Inventory.model_validate(inventory_data())
    assert inventory.retired_role_mode == "disabled" and inventory.retired_role_oid is None


@pytest.mark.parametrize("oid", [None, True, False, "123", 0, -1, 2**32, 1.5])
def test_deleted_identity_requires_strict_valid_pinned_oid(oid):
    with pytest.raises(ValidationError):
        preflight.Inventory.model_validate(
            {**inventory_data(), "retired_role_mode": "deleted", "retired_role_oid": oid}
        )


@pytest.mark.parametrize(
    "field", ["expected_system_identifier_sha256", "expected_database_oid", "role_env"]
)
def test_deleted_identity_still_requires_cluster_database_and_name_alias(field):
    data = {**inventory_data(), "retired_role_mode": "deleted", "retired_role_oid": 123}
    del data[field]
    with pytest.raises(ValidationError):
        preflight.Inventory.model_validate(data)


@contextmanager
def deleted_retired(context):
    engine, roles, _, _ = context
    old = roles["retired"]
    with engine.begin() as connection:
        oid = int(
            connection.execute(
                text("SELECT oid FROM pg_roles WHERE rolname=:name"), {"name": old}
            ).scalar_one()
        )
        connection.execute(text(f'DROP ROLE "{old}"'))
    try:
        yield oid
    finally:
        # Restore only this fixture's role name so its unchanged cleanup succeeds.
        with engine.begin() as connection:
            if not connection.execute(
                text("SELECT EXISTS(SELECT 1 FROM pg_roles WHERE rolname=:name)"), {"name": old}
            ).scalar_one():
                connection.execute(text(f'CREATE ROLE "{old}" NOLOGIN'))


def observed_deleted(context, oid, *, role_names=None, operators=None, **updates):
    engine, roles, operator, original = context
    data = {
        **copy.deepcopy(original),
        "retired_role_mode": "deleted",
        "retired_role_oid": oid,
        **updates,
    }
    inventory = preflight.Inventory.model_validate(data)
    observed = preflight.observe(
        engine.cutover_readonly_observer, inventory, role_names or roles, operators or (operator,)
    )
    return inventory, observed, preflight.evaluate(inventory, "0" * 64, observed, datetime.now(UTC))


def test_actual_role_deletion_passes_only_local_database_gate(context):
    with deleted_retired(context) as oid:
        _, observed, result = observed_deleted(context, oid)
        assert observed["roles"]["retired"] == {
            "state": "absent",
            "bound_oid": oid,
            "active_session_count": 0,
        }
        assert result["database_gate_passed"] is True
        assert result["cutover_ready"] is False
        assert "login_enabled" not in observed["roles"]["retired"]
        assert observed["prepared_transaction_count"] == 0


@pytest.mark.parametrize(
    "field,value", [("expected_system_identifier_sha256", "0" * 64), ("expected_database_oid", 1)]
)
def test_deleted_role_absence_on_wrong_database_refuses(context, field, value):
    with deleted_retired(context) as oid:
        with pytest.raises(preflight.Denied, match="deleted_role_database_identity_mismatch"):
            observed_deleted(context, oid, **{field: value})


def test_disabled_mode_still_requires_present_role_after_deletion(context):
    with deleted_retired(context):
        with pytest.raises(preflight.Denied, match="database_expected_role_missing"):
            report(context)


def test_disabled_mode_supplied_oid_must_match_actual_role(context):
    with pytest.raises(preflight.Denied, match="retired_role_oid_mismatch"):
        report(context, retired_role_oid=1)


def test_recreated_old_name_with_new_oid_refuses(context):
    with deleted_retired(context) as oid:
        with context[0].begin() as connection:
            connection.execute(text(f'CREATE ROLE "{context[1]["retired"]}" NOLOGIN'))
            replacement_oid = int(
                connection.execute(
                    text("SELECT oid FROM pg_roles WHERE rolname=:name"),
                    {"name": context[1]["retired"]},
                ).scalar_one()
            )
        assert replacement_oid != oid
        with pytest.raises(preflight.Denied, match="deleted_role_name_or_oid_present"):
            observed_deleted(context, oid)


@pytest.mark.parametrize("classification", ["unclassified", "replacement_runtime", "operator"])
def test_renamed_old_oid_cannot_be_absent_or_relabelled(context, classification):
    engine, roles, operator, _ = context
    renamed = "cutover_renamed_" + uuid4().hex
    with engine.begin() as connection:
        oid = int(
            connection.execute(
                text("SELECT oid FROM pg_roles WHERE rolname=:name"), {"name": roles["retired"]}
            ).scalar_one()
        )
        connection.execute(text(f'ALTER ROLE "{roles["retired"]}" RENAME TO "{renamed}"'))
    try:
        names = roles.copy()
        operators = (operator,)
        if classification == "replacement_runtime":
            names[classification] = renamed
        elif classification == "operator":
            operators = (operator, renamed)
        reason = (
            "deleted_role_name_or_oid_present"
            if classification == "unclassified"
            else "retired_role_identity_class_overlap"
        )
        with pytest.raises(preflight.Denied, match=reason):
            observed_deleted(context, oid, role_names=names, operators=operators)
    finally:
        with engine.begin() as connection:
            connection.execute(text(f'ALTER ROLE "{renamed}" RENAME TO "{roles["retired"]}"'))


def test_pinned_oid_occupied_by_unrelated_actual_role_refuses(context):
    with deleted_retired(context):
        with context[0].connect() as connection:
            occupied_oid = int(
                connection.execute(
                    text("SELECT oid FROM pg_roles WHERE rolname='pg_read_all_stats'")
                ).scalar_one()
            )
        with pytest.raises(preflight.Denied, match="deleted_role_name_or_oid_present"):
            observed_deleted(context, occupied_oid)


def test_absent_old_name_cannot_be_classified_as_operator(context):
    with deleted_retired(context) as oid:
        with pytest.raises(preflight.Denied, match="retired_role_identity_class_overlap"):
            observed_deleted(context, oid, operators=(context[2], context[1]["retired"]))


def test_actual_orphan_old_oid_session_survives_deletion_and_refuses(context):
    engine, roles, _, _ = context
    with engine.begin() as connection:
        connection.execute(
            text(f"ALTER ROLE \"{roles['retired']}\" LOGIN PASSWORD 'synthetic-local-old'")
        )
    old_engine = create_engine(
        engine.url.set(username=roles["retired"], password="synthetic-local-old"),
        poolclass=NullPool,
    )
    try:
        with old_engine.connect() as old_connection:
            old_connection.execute(text("SELECT 1"))
            with deleted_retired(context) as oid:
                _, observed, result = observed_deleted(context, oid)
                assert observed["roles"]["retired"]["active_session_count"] == 1
                assert_refused(result, "retired_database_sessions_present")
                assert_refused(result, "unclassified_database_writers_or_sessions")
    finally:
        old_engine.dispose()


@pytest.mark.parametrize(
    "mutation",
    [
        "missing_state",
        "wrong_state",
        "missing_oid",
        "wrong_oid",
        "bool_oid",
        "missing_count",
        "bool_count",
        "negative_count",
        "fabricated_capabilities",
    ],
)
def test_malformed_absent_report_cannot_pass(context, mutation):
    with deleted_retired(context) as oid:
        inventory, observed, _ = observed_deleted(context, oid)
        retired = observed["roles"]["retired"]
        if mutation == "missing_state":
            del retired["state"]
        elif mutation == "wrong_state":
            retired["state"] = "disabled"
        elif mutation == "missing_oid":
            del retired["bound_oid"]
        elif mutation == "wrong_oid":
            retired["bound_oid"] = oid + 1
        elif mutation == "bool_oid":
            retired["bound_oid"] = True
        elif mutation == "missing_count":
            del retired["active_session_count"]
        elif mutation == "bool_count":
            retired["active_session_count"] = False
        elif mutation == "negative_count":
            retired["active_session_count"] = -1
        else:
            retired["login_enabled"] = False
        result = preflight.evaluate(inventory, "0" * 64, observed, datetime.now(UTC))
        assert_refused(result, "retired_database_role_not_fenced")


def test_deleted_mode_preserves_consumer_writer_obligation_and_prepared_gates(context):
    with deleted_retired(context) as oid:
        inventory, observed, _ = observed_deleted(context, oid, consumer_inventory_complete=False)
        assert_refused(
            preflight.evaluate(inventory, "0" * 64, observed, datetime.now(UTC)),
            "consumer_inventory_unresolved",
        )
        for field, reason in [
            ("unexpected_writer_login_role_count", "unclassified_database_writers_or_sessions"),
            ("inspector_has_writer_authority", "database_observer_has_writer_authority"),
            ("prepared_transaction_count", "unresolved_obligations"),
        ]:
            changed = copy.deepcopy(observed)
            changed[field] = 1
            assert_refused(
                preflight.evaluate(inventory, "0" * 64, changed, datetime.now(UTC)), reason
            )
