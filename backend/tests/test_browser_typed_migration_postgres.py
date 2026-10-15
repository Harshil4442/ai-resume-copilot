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


def seed_legacy_review(engine):
    # Generate the historical signed review in an owned SQLite fixture, then
    # insert only actual schema18 columns (which lack the new durable marker).
    source = flow.BrowserAssistanceFlow()
    source.setUp()
    try:
        source.approved()
        metadata = MetaData()
        metadata.reflect(bind=engine)
        with source.engine.connect() as original, engine.begin() as target:
            for name in ("browser_assistance_devices", "browser_assistance_reviews", "browser_assistance_requests"):
                table = metadata.tables[name]
                rows = [{key: value for key, value in dict(row).items() if key in table.c}
                        for row in original.execute(flow.Base.metadata.tables[name].select()).mappings()]
                if rows:
                    target.execute(table.insert(), rows)
    finally:
        source.tearDown()


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
    seed_legacy_review(owned.engine)
    tables_before = set(inspect(owned.engine).get_table_names())
    columns_before = columns(owned.engine)
    before = copy.deepcopy(review_snapshot(owned.engine))
    assert_cursor_refused(owned.engine, 8)
    admission._migrate(owned.engine, "20261015_0019")
    assert version(owned.engine) == "20261015_0019"
    assert set(inspect(owned.engine).get_table_names()) == tables_before | {
        "candidate_google_enrollment_challenges", "candidate_google_identities", "candidate_identity_history"}
    after_columns = columns(owned.engine)
    assert [column for column in after_columns if column["name"] != "review_protocol_version"] == columns_before
    protocol_column = next(column for column in after_columns if column["name"] == "review_protocol_version")
    assert protocol_column["type"] == "INTEGER" and protocol_column["nullable"] is False
    with owned.engine.connect() as db:
        assert db.scalar(text("SELECT review_protocol_version FROM browser_assistance_reviews")) == 1
    assert review_snapshot(owned.engine) == before
    for index in (0, 7, 8, 12, 48, 64):
        set_cursor(owned.engine, index)
        assert review_snapshot(owned.engine)[0]["next_index"] == index
    for index in (-1, 65):
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
    assert_cursor_refused(owned.engine, 65)


@pytest.mark.parametrize("index,payload", [(0, {"review_protocol_version": 2}),
    (7, {"review_protocol_version": 2}), (8, {}), (12, {})])
def test_downgrade_guard_is_transactional_for_typed_payload_or_extended_cursor(migrated_browser, index, payload):
    owned = migrated_browser
    seed_legacy_review(owned.engine)
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
    assert_cursor_refused(owned.engine, 65)


def test_schema19_backfills_existing_typed_payload_marker(migrated_browser):
    owned = migrated_browser
    seed_legacy_review(owned.engine)
    with owned.engine.begin() as db:
        db.execute(text("UPDATE browser_assistance_reviews SET payload=CAST(:payload AS JSON)"), {"payload": '{"review_protocol_version":2}'})
    admission._migrate(owned.engine, "20261015_0019")
    with owned.engine.connect() as db:
        assert db.scalar(text("SELECT review_protocol_version FROM browser_assistance_reviews")) == 2
    with pytest.raises(RuntimeError, match="Typed-control review history must be retained"):
        admission._migrate(owned.engine, "20261015_0018", direction="downgrade")


@pytest.mark.parametrize("protocol", [2, 3])
@pytest.mark.parametrize("state", ["filled", "cancelled", "expired"])
def test_erased_completed_cancelled_or_expired_v2_v3_keeps_durable_marker_and_downgrade_guard(migrated_browser, protocol, state):
    owned = migrated_browser
    seed_legacy_review(owned.engine)
    admission._migrate(owned.engine, "20261015_0019")
    row = owned.db.query(flow.models.BrowserAssistanceReview).one()
    row.review_protocol_version = protocol
    row.payload = {"review_protocol_version": protocol}
    row.state = "draft" if state == "expired" else state
    if state == "cancelled":
        flow.service.candidate(owned.db, 1, "cancel", flow.schemas.CandidateRequest(identity=row.id, confirmed=True))
    else:
        row.expires_at = flow.service.now() - flow.service.timedelta(seconds=1)
        owned.db.commit()
        flow.service.expire_reviews(owned.db)
    owned.db.refresh(row)
    assert row.payload == {} and row.review_protocol_version == protocol and row.state == state
    owned.db.rollback()
    with pytest.raises(RuntimeError, match="Typed-control review history must be retained"):
        admission._migrate(owned.engine, "20261015_0018", direction="downgrade")


def test_actual_migrated_postgres_supports_one_review_with_five_hosted_employment_rows(migrated_browser):
    import os
    from unittest.mock import patch

    from backend.tests import test_browser_hosted_controls_isolated as hosted_tests
    owned = migrated_browser
    admission._migrate(owned.engine, "20261015_0019")
    with patch.dict(os.environ, {"BROWSER_ASSISTANCE_TYPED_CONTROLS_ENABLED": "true"}):
        hosted_tests.test_five_rows_fit_one_v2_review_with_signed_sql_actions_and_no_fee(owned)
    assert review_snapshot(owned.engine)[0]["next_index"] == 38
    with pytest.raises(RuntimeError, match="Typed-control review history must be retained"):
        admission._migrate(owned.engine, "20261015_0018", direction="downgrade")


@pytest.mark.parametrize("state",["filled","cancelled","expired"])
def test_schema19_backfills_and_retains_final_submit_version_after_private_payload_erasure(migrated_browser,state):
    owned=migrated_browser
    seed_legacy_review(owned.engine)
    with owned.engine.begin() as db:
        db.execute(text("UPDATE browser_assistance_reviews SET payload=CAST(:payload AS JSON)"),{"payload":'{"review_protocol_version":4}'})
    admission._migrate(owned.engine,"20261015_0019")
    with owned.engine.begin() as db:
        assert db.scalar(text("SELECT review_protocol_version FROM browser_assistance_reviews"))==4
        db.execute(text("UPDATE browser_assistance_reviews SET state=:state,payload=CAST('{}' AS JSON),next_index=:index"),{"state":state,"index":1 if state=="filled" else 0})
    with pytest.raises(RuntimeError,match="Typed-control review history must be retained"):
        admission._migrate(owned.engine,"20261015_0018",direction="downgrade")
    with owned.engine.connect() as db:
        assert db.scalar(text("SELECT review_protocol_version FROM browser_assistance_reviews"))==4
        assert db.scalar(text("SELECT state FROM browser_assistance_reviews"))==state
