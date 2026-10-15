"""Actual schema18→19 PostgreSQL migration, cursor bounds and retention guards.

Only the dedicated loopback database accepted by pg_engine is permitted. The
full historical Alembic chain is executed; metadata.create_all is not migration
evidence. Shipping SQL/crypto services operate on synthetic owned package rows.
"""
from __future__ import annotations

import copy

import pytest
from backend.tests import test_browser_assistance_isolated as flow
from backend.tests import test_employer_admissions_postgres as admission
from sqlalchemy import MetaData, inspect, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker


@pytest.fixture
def migrated_browser():
    engines = admission.pg_engine.__wrapped__()
    engine = next(engines)
    owned = flow.BrowserAssistanceFlow()
    owned.setUp()
    source_db, source_engine = owned.db, owned.engine
    try:
        admission._migrate(engine, "20261015_0018")
        target_metadata = MetaData()
        target_metadata.reflect(bind=engine)
        with source_engine.connect() as source, engine.begin() as target:
            for table in flow.Base.metadata.sorted_tables:
                rows = [dict(row) for row in source.execute(table.select()).mappings()]
                if rows:
                    target.execute(target_metadata.tables[table.name].insert(), rows)
        source_db.close()
        source_engine.dispose()
        owned.db, owned.engine = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)(), engine
        owned.app = owned.db.get(flow.employer.EmployerApplication, "apply_test")
        yield owned
    finally:
        owned.tearDown()
        source_db.close()
        source_engine.dispose()
        engines.close()


def version(engine):
    with engine.connect() as db:
        return db.scalar(text("SELECT version_num FROM alembic_version"))


def review_snapshot(engine):
    with engine.connect() as db:
        return [dict(row) for row in db.execute(text(
            "SELECT id, payload, next_index, state, review_digest FROM browser_assistance_reviews ORDER BY id"
        )).mappings()]


def columns(engine):
    return [{**column, "type": str(column["type"])}
            for column in inspect(engine).get_columns("browser_assistance_reviews")]


def set_cursor(engine, index):
    with engine.begin() as db:
        db.execute(text("UPDATE browser_assistance_reviews SET next_index=:index"), {"index": index})


def assert_cursor_refused(engine, index):
    with pytest.raises(IntegrityError) as failure:
        set_cursor(engine, index)
    assert failure.value.orig.sqlstate == "23514"
    assert failure.value.orig.diag.constraint_name == "ck_browser_review_index"


def test_actual_schema18_to19_preserves_v1_rows_and_only_expands_cursor_then_round_trips(migrated_browser):
    owned = migrated_browser
    owned.approved()
    tables_before = set(inspect(owned.engine).get_table_names())
    columns_before = columns(owned.engine)
    before = copy.deepcopy(review_snapshot(owned.engine))
    assert_cursor_refused(owned.engine, 8)
    admission._migrate(owned.engine, "20261015_0019")
    assert version(owned.engine) == "20261015_0019"
    assert set(inspect(owned.engine).get_table_names()) == tables_before
    assert columns(owned.engine) == columns_before
    assert review_snapshot(owned.engine) == before
    for index in (0, 7, 8, 12):
        set_cursor(owned.engine, index)
        assert review_snapshot(owned.engine)[0]["next_index"] == index
    for index in (-1, 13):
        assert_cursor_refused(owned.engine, index)
    # A retained historical v1 review fits the old bound and can round-trip.
    set_cursor(owned.engine, 7)
    before_downgrade = review_snapshot(owned.engine)
    admission._migrate(owned.engine, "20261015_0018", direction="downgrade")
    assert version(owned.engine) == "20261015_0018"
    assert review_snapshot(owned.engine) == before_downgrade
    assert_cursor_refused(owned.engine, 8)
    assert_cursor_refused(owned.engine, -1)


def test_actual_migrated_postgres_supports_twelve_signed_actions_without_charge_or_receipt(migrated_browser):
    owned = migrated_browser
    admission._migrate(owned.engine, "20261015_0019")
    owned.test_typed_twelve_field_sql_flow_has_explicit_signed_control_actions_and_zero_fee()
    assert review_snapshot(owned.engine)[0]["next_index"] == 12
    with pytest.raises(RuntimeError, match="Typed-control review history must be retained"):
        admission._migrate(owned.engine, "20261015_0018", direction="downgrade")
    assert version(owned.engine) == "20261015_0019"
    assert review_snapshot(owned.engine)[0]["next_index"] == 12
    assert_cursor_refused(owned.engine, 13)


@pytest.mark.parametrize("index,payload", [(0, {"review_protocol_version": 2}),
    (7, {"review_protocol_version": 2}), (8, {}), (12, {})])
def test_downgrade_guard_is_transactional_for_typed_payload_or_extended_cursor(migrated_browser, index, payload):
    owned = migrated_browser
    owned.approved()
    admission._migrate(owned.engine, "20261015_0019")
    with owned.engine.begin() as db:
        db.execute(flow.models.BrowserAssistanceReview.__table__.update().values(next_index=index, payload=payload))
    before = review_snapshot(owned.engine)
    with pytest.raises(RuntimeError, match="Typed-control review history must be retained"):
        admission._migrate(owned.engine, "20261015_0018", direction="downgrade")
    assert version(owned.engine) == "20261015_0019"
    assert review_snapshot(owned.engine) == before
    # Failure must leave the schema19 constraint in place, without a partial DDL downgrade.
    set_cursor(owned.engine, 12)
    assert_cursor_refused(owned.engine, 13)
