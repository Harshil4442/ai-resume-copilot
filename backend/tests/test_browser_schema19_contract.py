"""Actual catalog admission on one owned loopback PostgreSQL17 namespace.

These tests run the historical migrations, then roll back individual catalog
faults. They do not establish production identity, native authority or readiness.
"""

from __future__ import annotations

import os
from pathlib import Path
from uuid import uuid4

import pytest
from alembic.config import Config
from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import make_url
from sqlalchemy.pool import NullPool

from alembic import command
from scripts import browser_schema19_contract as contract
from scripts import monetary_cutover_preflight as previous
from scripts import monetary_schema_step as old_step
from scripts import neon_direct_identity as old_identity


@pytest.fixture(scope="module")
def catalog():
    configured = os.getenv("HIREWIZ_TEST_POSTGRES_URL")
    if not configured:
        pytest.skip("Set the dedicated local disposable PostgreSQL test URL")
    url = make_url(configured)
    if (
        url.drivername != "postgresql+psycopg"
        or url.host != "127.0.0.1"
        or url.port != 55433
        or url.database != "hirewiz_admission_test"
    ):
        pytest.fail("Schema19 catalog tests accept only the owned loopback admission database")
    namespace = "browser_schema19_" + uuid4().hex
    admin = create_engine(url, poolclass=NullPool, hide_parameters=True)
    with admin.begin() as db:
        assert 170000 <= int(db.scalar(text("SHOW server_version_num"))) < 180000
        db.execute(text(f'CREATE SCHEMA "{namespace}"'))
    engine = create_engine(
        url,
        poolclass=NullPool,
        hide_parameters=True,
        connect_args={
            "options": f"-csearch_path={namespace} -clock_timeout=4000 -cstatement_timeout=10000",
        },
    )
    try:
        root = Path(__file__).resolve().parents[1]
        cfg = Config(str(root / "alembic.ini"))
        cfg.set_main_option("script_location", str(root / "alembic"))
        with engine.begin() as db:
            cfg.attributes["connection"] = db
            command.upgrade(cfg, contract.SCHEMA)
        with engine.connect() as db:
            namespace_oid = db.scalar(
                text("SELECT oid FROM pg_catalog.pg_namespace WHERE nspname=:name"),
                {"name": namespace},
            )
        yield engine, namespace, namespace_oid
    finally:
        engine.dispose()
        with admin.begin() as db:
            db.execute(text(f'DROP SCHEMA "{namespace}" CASCADE'))
        admin.dispose()


def verify(db, catalog):
    contract.verify_shape(db, catalog[2], catalog[1])


def test_actual_schema19_metadata_passes_in_read_only_snapshot_without_application_reads(catalog):
    engine, namespace, _ = catalog
    before = (
        previous.SCHEMA_ORDER,
        set(previous.SCHEMAS),
        old_step.CHAIN,
        old_identity.RELEASE_SCHEMAS,
    )
    statements = []

    def capture(_connection, _cursor, statement, _parameters, _context, _many):
        statements.append(statement)

    with engine.connect() as db:
        db.exec_driver_sql("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        event.listen(db, "before_cursor_execute", capture)
        verify(db, catalog)
        event.remove(db, "before_cursor_execute", capture)
        assert db.scalar(text("SHOW transaction_read_only")) == "on"
    assert len(statements) == 7
    assert all(
        statement.startswith("SELECT ")
        and "pg_catalog." in statement
        and f'FROM "{namespace}".' not in statement
        for statement in statements
    )
    assert before == (
        previous.SCHEMA_ORDER,
        set(previous.SCHEMAS),
        old_step.CHAIN,
        old_identity.RELEASE_SCHEMAS,
    )
    assert contract.SCHEMA not in previous.SCHEMAS | old_identity.RELEASE_SCHEMAS
    assert old_step.CHAIN[-1] == "20261015_0018"
    assert contract.CONTRACT == "hirewiz_browser_schema19_v1"


@pytest.mark.parametrize(
    "sql",
    [
        "ALTER TABLE candidate_google_enrollment_challenges DROP COLUMN nonce_sha256",
        "ALTER TABLE candidate_google_enrollment_challenges ALTER COLUMN used SET DEFAULT false",
        "ALTER TABLE candidate_google_enrollment_challenges ALTER COLUMN used DROP NOT NULL",
        "ALTER TABLE candidate_google_identities ALTER COLUMN principal_sha256 TYPE varchar(63)",
        "ALTER TABLE candidate_google_identities ADD COLUMN unreviewed_identity text",
        'ALTER TABLE candidate_google_identities ALTER COLUMN principal_sha256 TYPE varchar(64) COLLATE "C"',
        "DROP TABLE candidate_identity_history",
        "ALTER TABLE browser_assistance_reviews ALTER COLUMN review_protocol_version DROP NOT NULL",
        "ALTER TABLE browser_assistance_reviews ALTER COLUMN review_protocol_version SET DEFAULT 2",
        "ALTER TABLE browser_assistance_reviews ALTER COLUMN review_protocol_version DROP DEFAULT",
        "ALTER TABLE browser_assistance_reviews ALTER COLUMN review_protocol_version TYPE bigint",
        "ALTER TABLE browser_assistance_reviews DROP COLUMN review_protocol_version",
    ],
)
def test_changed_or_missing_google_and_durable_protocol_columns_refuse(catalog, sql):
    with catalog[0].connect() as db, db.begin() as transaction:
        db.exec_driver_sql(sql)
        with pytest.raises(contract.Schema19Denied, match="schema19_column_shape_mismatch"):
            verify(db, catalog)
        transaction.rollback()


@pytest.mark.parametrize(
    "name,expression,validated",
    [
        ("ck_browser_review_protocol", "review_protocol_version BETWEEN 1 AND 5", True),
        ("ck_browser_review_protocol", "review_protocol_version BETWEEN 1 AND 4", False),
        ("ck_browser_review_index", "next_index BETWEEN 0 AND 65", True),
        ("ck_browser_review_index", "next_index BETWEEN 0 AND 64", False),
        ("ck_browser_review_index", "next_index BETWEEN 0 AND 7", True),
        (
            "ck_browser_review_protocol",
            "review_protocol_version>=1 OR review_protocol_version<=4",
            True,
        ),
    ],
)
def test_weakened_old_or_unvalidated_review_bounds_refuse(catalog, name, expression, validated):
    with catalog[0].connect() as db, db.begin() as transaction:
        db.exec_driver_sql(f"ALTER TABLE browser_assistance_reviews DROP CONSTRAINT {name}")
        db.exec_driver_sql(
            f"ALTER TABLE browser_assistance_reviews ADD CONSTRAINT {name} CHECK ({expression})"
            + ("" if validated else " NOT VALID")
        )
        with pytest.raises(
            contract.Schema19Denied, match="schema19_(constraint_shape|review_checks)_mismatch"
        ):
            verify(db, catalog)
        transaction.rollback()


@pytest.mark.parametrize(
    "table,kind,replacement",
    [
        ("candidate_google_identities", "u", None),
        ("candidate_identity_history", "p", None),
        ("candidate_google_enrollment_challenges", "f", None),
        ("candidate_google_identities", "f", "FOREIGN KEY(user_id) REFERENCES users(id)"),
        (
            "candidate_google_identities",
            "f",
            "FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE DEFERRABLE",
        ),
        (
            "candidate_google_identities",
            "f",
            "FOREIGN KEY(user_id) REFERENCES candidate_password_accounts(user_id) ON DELETE CASCADE",
        ),
    ],
)
def test_missing_uniqueness_primary_key_or_changed_google_owner_foreign_key_refuse(
    catalog, table, kind, replacement
):
    with catalog[0].connect() as db, db.begin() as transaction:
        name = db.scalar(
            text(
                "SELECT k.conname FROM pg_catalog.pg_constraint k "
                "JOIN pg_catalog.pg_class c ON c.oid=k.conrelid "
                "WHERE c.relnamespace=:namespace AND c.relname=:table AND k.contype=:kind"
            ),
            {"namespace": catalog[2], "table": table, "kind": kind},
        )
        assert name
        db.exec_driver_sql(f'ALTER TABLE "{table}" DROP CONSTRAINT "{name}"')
        if replacement:
            db.exec_driver_sql(f'ALTER TABLE "{table}" ADD CONSTRAINT "{name}" {replacement}')
        with pytest.raises(contract.Schema19Denied, match="schema19_constraint_shape_mismatch"):
            verify(db, catalog)
        transaction.rollback()


@pytest.mark.parametrize(
    "replacement",
    [
        None,
        "(used)",
        "(user_id) WHERE used=false",
        "(user_id) INCLUDE (used)",
        "(user_id DESC)",
        "UNIQUE (user_id)",
    ],
)
def test_google_challenge_lookup_index_must_match_exact_reviewed_shape(catalog, replacement):
    with catalog[0].connect() as db, db.begin() as transaction:
        db.exec_driver_sql("DROP INDEX ix_candidate_google_enrollment_challenges_user_id")
        if replacement:
            unique = replacement.startswith("UNIQUE ")
            keys = replacement.removeprefix("UNIQUE ")
            db.exec_driver_sql(
                "CREATE "
                + ("UNIQUE " if unique else "")
                + "INDEX ix_candidate_google_enrollment_challenges_user_id "
                + "ON candidate_google_enrollment_challenges "
                + keys
            )
        with pytest.raises(contract.Schema19Denied, match="schema19_index_shape_mismatch"):
            verify(db, catalog)
        transaction.rollback()


@pytest.mark.parametrize("suffix", ['COLLATE "C"', "text_pattern_ops"])
def test_google_principal_uniqueness_cannot_change_collation_or_operator_class(catalog, suffix):
    with catalog[0].connect() as db, db.begin() as transaction:
        db.exec_driver_sql(
            "ALTER TABLE candidate_google_identities "
            "DROP CONSTRAINT candidate_google_identities_principal_sha256_key"
        )
        db.exec_driver_sql(
            "CREATE UNIQUE INDEX candidate_google_identities_principal_sha256_key "
            f"ON candidate_google_identities (principal_sha256 {suffix})"
        )
        with pytest.raises(contract.Schema19Denied, match="schema19_index_shape_mismatch"):
            contract._indexes(db, catalog[2])
        # A similarly named index also cannot replace the migrated constraint.
        with pytest.raises(contract.Schema19Denied, match="schema19_constraint_shape_mismatch"):
            verify(db, catalog)
        transaction.rollback()


def test_review_check_no_inherit_is_not_the_migrated_constraint(catalog):
    with catalog[0].connect() as db, db.begin() as transaction:
        db.exec_driver_sql(
            "ALTER TABLE browser_assistance_reviews DROP CONSTRAINT ck_browser_review_protocol"
        )
        db.exec_driver_sql(
            "ALTER TABLE browser_assistance_reviews ADD CONSTRAINT ck_browser_review_protocol "
            "CHECK (review_protocol_version BETWEEN 1 AND 4) NO INHERIT"
        )
        with pytest.raises(contract.Schema19Denied, match="schema19_review_checks_mismatch"):
            verify(db, catalog)
        transaction.rollback()


@pytest.mark.parametrize(
    "sql",
    [
        "ALTER TABLE employer_host_pacing DROP COLUMN owner_token",
        "ALTER TABLE candidate_password_accounts DROP COLUMN credential_sha256",
    ],
)
def test_schema19_retains_frozen_candidate_and_extension_shape_checks(catalog, sql):
    with catalog[0].connect() as db, db.begin() as transaction:
        db.exec_driver_sql(sql)
        with pytest.raises(
            contract.Schema19Denied, match="schema19_preserved_projection_shape_mismatch"
        ):
            verify(db, catalog)
        transaction.rollback()


def test_namespace_oid_and_name_must_describe_the_same_actual_namespace(catalog):
    with catalog[0].connect() as db:
        with pytest.raises(contract.Schema19Denied, match="schema19_namespace_identity_mismatch"):
            contract.verify_shape(db, catalog[2], "public")


@pytest.mark.parametrize(
    "oid,namespace",
    [
        (False, "public"),
        (0, "public"),
        (-1, "public"),
        (2**32, "public"),
        ("2200", "public"),
        (2200, "public;SELECT 1"),
        (2200, "Public"),
        (2200, ""),
    ],
)
def test_invalid_namespace_inputs_refuse_before_any_sql(oid, namespace):
    class NoSQL:
        def execute(self, *_args, **_kwargs):
            pytest.fail("Invalid schema19 input must not reach SQL")

    with pytest.raises(contract.Schema19Denied, match="schema19_namespace(_oid)?_invalid"):
        contract.verify_shape(NoSQL(), oid, namespace)


def test_unavailable_catalog_returns_only_fixed_nonsecret_failure():
    class Unavailable:
        def execute(self, *_args, **_kwargs):
            raise RuntimeError("unretained private database details")

    with pytest.raises(contract.Schema19Denied) as error:
        contract.verify_shape(Unavailable(), 2200)
    assert str(error.value) == "schema19_catalog_observation_unavailable"


def test_schema19_application_reader_keeps_full_schema18_manual_report_predicate():
    assert contract.unresolved_application_sql("public") == previous.unresolved_application_sql(
        "public", "20261015_0018"
    )
    for version in ("20261015_0018", "20261015_0020", None):
        with pytest.raises(contract.Schema19Denied, match="schema19_application_reader_invalid"):
            contract.unresolved_application_sql("public", version)
