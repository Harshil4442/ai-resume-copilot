"""Inert catalog, transport and original-journal fixtures; no database/provider IO."""

from __future__ import annotations

import hashlib
import importlib
import importlib.util
import sys
from contextlib import contextmanager
from dataclasses import asdict, replace
from pathlib import Path
from types import SimpleNamespace

import pytest
from backend.tests.test_neon_schema19_release_transport import (
    Reader,
    Rows,
    Session,
    fresh_receipt,  # noqa: F401 - existing inert native-source fixture
)
from backend.tests.test_rolling_release import FixtureBoundary

from scripts import native_resume_schema20_contract as contract
from scripts import native_resume_schema20_step as step
from scripts import neon_direct_identity as neon

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "infra/gcp"))
reviewed = importlib.import_module("native_resume_schema20_release")


@pytest.mark.parametrize("alias", tuple(neon.FRESH_RELEASE_ACTORS))
def test20_target_is_exact_numeric_source_native_identity_and_opaque_purpose(alias, monkeypatch):
    reader = Reader(alias)
    binding, target = neon.collect_schema20_release_target(
        reader, reader.reference, alias=alias, database_owner_oid=neon.FRESH_DATABASE_OWNER_OID
    )
    assert reader.calls == ["describe", "access", "describe"]
    assert binding.purpose == "schema20_fresh_release_capture"
    assert neon.bound_schema(binding) == contract.SCHEMA
    assert target._url.query["sslmode"] == "verify-full"
    assert target.dump_environment()["PGOPTIONS"].startswith("-c default_transaction_read_only=on ")
    monkeypatch.setattr(
        neon, "FRESH_CLUSTER", hashlib.sha256(b"schema19-synthetic-native-cluster").hexdigest()
    )
    connection = Session(binding)
    neon.verify_native_target(connection, binding, neon.FRESH_DATABASE_OID)
    assert len(connection.queries) == 3
    with pytest.raises(neon.NeonIdentityDenied):
        neon.verify_native_target(Session(binding, "schema"), binding, neon.FRESH_DATABASE_OID)
    for forged in (
        replace(binding),
        replace(binding, purpose="schema19_fresh_release_capture"),
        replace(binding, expected_schema=contract.BEFORE),
    ):
        with pytest.raises(neon.NeonIdentityDenied):
            neon.bound_schema(forged)


@pytest.mark.parametrize(
    "factory",
    [
        neon.collect_fresh_release_target,
        neon.collect_schema19_release_target,
        neon.collect_release_target,
        neon.collect_source_release_target,
    ],
)
def test_existing_purposes_do_not_adopt20(factory):
    reader = Reader()
    with pytest.raises(neon.NeonIdentityDenied):
        factory(
            reader,
            reader.reference,
            alias="observer",
            database_owner_oid=neon.FRESH_DATABASE_OWNER_OID,
            expected_schema=contract.SCHEMA,
        )
    assert reader.calls == []


@pytest.mark.parametrize("schema", [contract.BEFORE, "20261015_0018", "latest", None, True])
def test20_factory_refuses_other_schema_before_any_secret_read(schema):
    reader = Reader()
    with pytest.raises(neon.NeonIdentityDenied):
        neon.collect_schema20_release_target(
            reader,
            reader.reference,
            alias="observer",
            database_owner_oid=neon.FRESH_DATABASE_OWNER_OID,
            expected_schema=schema,
        )
    assert reader.calls == []


def definition(formats):
    return (
        "CHECK (((source_format)::text = ANY ((ARRAY["
        + ", ".join(f"'{value}'::character varying" for value in formats)
        + "])::text[])))"
    )


class Catalog:
    def __init__(self, schema=contract.SCHEMA):
        self.schema, self.queries = schema, []
        self.columns = [
            {
                "relkind": "r",
                "relpersistence": "p",
                "relispartition": False,
                "column_type": "character varying(8)",
                "attnotnull": True,
                "attidentity": "",
                "attgenerated": "",
                "default_collation": True,
                "column_default": None,
            }
        ]
        self.checks = [
            {
                "conname": "ck_resume_upload_format",
                "contype": "c",
                "convalidated": True,
                "condeferrable": False,
                "condeferred": False,
                "conislocal": True,
                "coninhcount": 0,
                "connoinherit": False,
                "columns": ["source_format"],
                "definition": definition(
                    ("pdf", "docx")
                    if schema == contract.BEFORE
                    else ("pdf", "docx", "tex", "texzip")
                ),
            }
        ]

    def execute(self, statement, _params=None):
        sql = str(statement)
        self.queries.append(sql)
        if "version_num" in sql:
            return Rows([(self.schema,)])
        rows = self.checks if "pg_constraint" in sql else self.columns
        return SimpleNamespace(mappings=lambda: SimpleNamespace(all=lambda: rows))


def test20_catalog_requires_exact_marker_and_preserved19_shape(monkeypatch):
    calls = []
    monkeypatch.setattr(contract.previous, "verify_shape", lambda *args: calls.append(args[1:]))
    db = Catalog()
    contract.verify_shape(db, 2200)
    assert calls == [(2200, "public")]
    assert len(db.queries) == 3 and all(query.startswith("SELECT ") for query in db.queries)
    assert all("LIMIT 3" in query for query in db.queries)
    contract.verify_before_shape(Catalog(contract.BEFORE), 2200)
    with pytest.raises(contract.Schema20Denied, match="exact_marker"):
        contract.verify_shape(Catalog(contract.BEFORE), 2200)


@pytest.mark.parametrize(
    "fault",
    [
        "column",
        "nullable",
        "default",
        "partition",
        "duplicate",
        "unvalidated",
        "extra",
        "broader",
        "literal_space",
        "literal_parentheses",
        "old",
    ],
)
def test20_catalog_refuses_upload_format_drift(monkeypatch, fault):
    monkeypatch.setattr(contract.previous, "verify_shape", lambda *args: None)
    db = Catalog()
    if fault == "column":
        db.columns[0]["column_type"] = "text"
    elif fault == "nullable":
        db.columns[0]["attnotnull"] = False
    elif fault == "default":
        db.columns[0]["column_default"] = "'pdf'"
    elif fault == "partition":
        db.columns[0]["relispartition"] = True
    elif fault == "duplicate":
        db.checks *= 2
    elif fault == "unvalidated":
        db.checks[0]["convalidated"] = False
    elif fault == "extra":
        db.checks[0]["columns"] = ["source_format", "state"]
    elif fault == "broader":
        db.checks[0]["definition"] += " OR true"
    elif fault == "literal_space":
        db.checks[0]["definition"] = db.checks[0]["definition"].replace("'texzip'", "'tex zip'")
    elif fault == "literal_parentheses":
        db.checks[0]["definition"] = db.checks[0]["definition"].replace("'tex'", "'t(e)x'")
    else:
        db.checks[0]["definition"] = definition(("pdf", "docx"))
    with pytest.raises(contract.Schema20Denied):
        contract.verify_shape(db, 2200)


def test_source20_chain_is_distinct_and_19_source_verifier_remains_frozen(monkeypatch):
    old = object.__new__(reviewed.previous.Source19Boundary)
    old.root = ROOT
    new = object.__new__(reviewed.Source20Boundary)
    new.root = ROOT
    monkeypatch.setattr(reviewed.release.source_guard, "checked_files", lambda *args: ["inert"])
    monkeypatch.setattr(
        reviewed.release.source_guard, "migration_chain", lambda *args, **kwargs: reviewed.CHAIN
    )
    calls = []
    monkeypatch.setattr(
        new, "_verify_source_image_provenance", lambda **kwargs: calls.append(kwargs)
    )
    values = dict(
        release="a" * 40, image_digest="sha256:" + "b" * 64, build_id="c" * 36, ci_run_id=1
    )
    new.verify_source_image(**values)
    assert calls == [values]
    with pytest.raises(reviewed.rolling.RollingDenied, match="schema19_exact_source_chain"):
        old.verify_source_image(**values)
    monkeypatch.setattr(
        reviewed.release.source_guard,
        "migration_chain",
        lambda *args, **kwargs: reviewed.previous.CHAIN,
    )
    with pytest.raises(reviewed.rolling.RollingDenied, match="schema20_exact_source_chain"):
        new.verify_source_image(**values)


def test20_plan_cannot_be_adopted_by19_or18_loaders(tmp_path):
    old = FixtureBoundary().plan
    raw = reviewed.rolling.canonical(
        {
            **asdict(old),
            "schema_contract": contract.CONTRACT,
            "database_schema": contract.SCHEMA,
            "prior_sources": [],
        }
    )
    plan = reviewed.load_plan(raw)
    assert plan.database_schema == contract.SCHEMA
    with pytest.raises(reviewed.rolling.RollingDenied):
        reviewed.previous.load_plan(raw)
    with pytest.raises(TypeError):
        reviewed.native.load_plan(raw)
    assert contract.SCHEMA not in neon.RELEASE_SCHEMAS
    assert reviewed.previous.CHAIN[-1] == contract.BEFORE
    tmp_path.chmod(0o700)
    old_journal = reviewed.rolling.Journal(tmp_path, old)
    before = replace(plan, database_schema=contract.BEFORE)
    boundary = reviewed.NativeSchema20Boundary(ROOT, contract.BEFORE)
    with pytest.raises(reviewed.rolling.RollingDenied, match="original_plan_journal"):
        reviewed.migration(before, boundary, old_journal)


@pytest.mark.parametrize("phase", [contract.BEFORE, contract.SCHEMA])
def test_native20_target_uses_distinct_phase_reader_and_retains_transport_acl_guards(
    monkeypatch, phase
):
    observed = []

    @contextmanager
    def transaction():
        yield

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def begin(self):
            return transaction()

        def exec_driver_sql(self, sql):
            observed.append(sql)

    class Engine:
        def connect(self):
            return Connection()

        def dispose(self):
            observed.append("dispose")

    def collector(label):
        def collect(_reader, _reference, **values):
            assert values["expected_schema"] == phase
            assert values["database_owner_oid"] == neon.FRESH_DATABASE_OWNER_OID
            observed.append((label, values["alias"]))
            return values["alias"], SimpleNamespace(engine=lambda: Engine())

        return collect

    boundary = object.__new__(reviewed.NativeSchema20Boundary)
    boundary.root = ROOT
    boundary.schema = phase
    module = SimpleNamespace(
        neon_identity=neon, _native_session_ids=lambda *args, **kwargs: ([], {})
    )
    boundary.observer = SimpleNamespace(
        _module=lambda *args: module,
        describe_release_secret=lambda reference: {
            "name": "projects/157225724590/secrets/inert-retired/versions/2",
            "state": "DISABLED",
        },
    )
    monkeypatch.setattr(neon, "collect_schema19_release_target", collector("19"))
    monkeypatch.setattr(neon, "collect_schema20_release_target", collector("20"))
    monkeypatch.setattr(neon, "verify_transport", lambda *args: observed.append("transport"))
    monkeypatch.setattr(
        neon, "verify_native_target", lambda *args: observed.append("native_target")
    )
    monkeypatch.setattr(
        reviewed.release, "verify_fresh_role_phase", lambda *args: observed.append("serving_acl")
    )
    monkeypatch.setattr(
        reviewed.fresh,
        "verify_financial_state",
        lambda _db, _module, schema: observed.append(("financial18", schema)),
    )
    monkeypatch.setattr(contract, "verify_shape", lambda *args: observed.append("catalog20"))
    monkeypatch.setattr(contract, "verify_before_shape", lambda *args: observed.append("catalog19"))
    monkeypatch.setattr(
        reviewed, "verify_permissions", lambda *args: observed.append("exact19_permissions")
    )
    monkeypatch.setattr(step, "drained", lambda *args: observed.append("drained"))
    monkeypatch.setattr(reviewed.native, "financial_digest", lambda *args: "a" * 64)
    assert boundary._target("inert-runtime:2", "inert-observer:2", "inert-retired:2") == "a" * 64
    label = "20" if phase == contract.SCHEMA else "19"
    assert [(label, "observer"), (label, "replacement_runtime")] == [
        row for row in observed if isinstance(row, tuple) and row[0] == label
    ]
    assert (
        observed.count("transport")
        == observed.count("native_target")
        == observed.count("dispose")
        == 2
    )
    assert "serving_acl" in observed and "exact19_permissions" in observed and "drained" in observed
    assert ("catalog20" if phase == contract.SCHEMA else "catalog19") in observed
    assert ("financial18", reviewed.previous.step.BEFORE) in observed
    assert observed.count("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY") == 2


def test20_retained_digest_includes_google_protocol_and_cleanup_columns():
    class DigestRows:
        def __init__(self, changed=False):
            self.queries = []
            self.changed = changed

        def execute(self, statement, _params=None):
            sql = str(statement)
            self.queries.append(sql)
            if "pg_class" in sql:
                return Rows(
                    [
                        (name,)
                        for name in [
                            "alembic_version",
                            "browser_assistance_reviews",
                            "candidate_google_identities",
                            "resume_upload_cleanups",
                        ]
                    ]
                )
            return [("b" * 64 if self.changed and "resume_upload_cleanups" in sql else "a" * 64,)]

    first, changed = DigestRows(), DigestRows(True)
    assert step.retained_digest(first, reviewed.bootstrap_identity()) != step.retained_digest(
        changed, reviewed.bootstrap_identity()
    )
    assert len(first.queries) == 4
    assert all("review_protocol_version'" not in sql for sql in first.queries)
    assert any('"candidate_google_identities"' in sql for sql in first.queries)


@pytest.mark.parametrize("before", [contract.BEFORE, contract.SCHEMA])
def test20_upgrade_uses_only_existing_migration_in_the_original_transaction(monkeypatch, before):
    identity = reviewed.bootstrap_identity()
    state = {"schema": before, "ddl": 0}
    events = []

    class Connection:
        def in_transaction(self):
            return True

        def exec_driver_sql(self, sql):
            events.append(sql)

        def execute(self, statement, _params=None):
            sql = str(statement)
            if "advisory" in sql:
                return SimpleNamespace(scalar_one=lambda: True)
            assert "relowner" in sql
            return SimpleNamespace(scalar_one=lambda: 0)

    db = Connection()
    monkeypatch.setattr(step, "verify_identity", lambda *args: events.append("native_identity"))
    monkeypatch.setattr(
        step, "effective_owner", lambda *args: events.append("effective_nologin_owner")
    )
    monkeypatch.setattr(step, "marker", lambda *args: state["schema"])
    monkeypatch.setattr(
        contract, "verify_before_shape", lambda *args: events.append("exact19_catalog")
    )
    monkeypatch.setattr(contract, "verify_shape", lambda *args: events.append("exact20_catalog"))
    monkeypatch.setattr(step, "schema_acl", lambda *args: [(10, 11, "USAGE", False)])
    monkeypatch.setattr(step, "drained", lambda *args: events.append("drained"))
    monkeypatch.setattr(step, "retained_digest", lambda *args: "a" * 64)

    def upgrade(configuration, target):
        assert target == contract.SCHEMA
        assert configuration.attributes["connection"] is db
        assert Path(configuration.get_main_option("script_location")) == ROOT / "backend/alembic"
        state["ddl"] += 1
        events.append("original_ddl")
        state["schema"] = target

    monkeypatch.setattr(step.command, "upgrade", upgrade)
    if before == contract.SCHEMA:
        with pytest.raises(contract.Schema20Denied, match="never_replay"):
            step.upgrade_transaction(db, identity, root=ROOT)
        assert state["ddl"] == 0
    else:
        result = step.upgrade_transaction(db, identity, root=ROOT)
        assert result["schema"] == contract.SCHEMA and result["retained_sha256"] == "a" * 64
        assert result["replay_authorized"] is False and state["ddl"] == 1
        assert (
            events.index("native_identity")
            < events.index("exact19_catalog")
            < events.index("original_ddl")
            < events.index("exact20_catalog")
        )
        assert events.count("native_identity") == 2 and "effective_nologin_owner" in events
        assert all(not value.startswith(("GRANT ", "REVOKE ", "ALTER ROLE ")) for value in events)


def test_original_migration20_refuses_cleanup_only_without_retrieving_or_deleting_payload(
    monkeypatch,
):
    path = ROOT / "backend/alembic/versions/20261023_0020_native_resume_quarantine.py"
    spec = importlib.util.spec_from_file_location("inert_native20_downgrade", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    queries, batches = [], []

    def execute(statement):
        queries.append(str(statement))
        return SimpleNamespace(first=lambda: (1,))

    monkeypatch.setattr(
        module.op,
        "get_bind",
        lambda: SimpleNamespace(execute=execute, dialect=SimpleNamespace(name="sqlite")),
    )
    monkeypatch.setattr(
        module.op, "batch_alter_table", lambda *args, **kwargs: batches.append(args)
    )
    with pytest.raises(RuntimeError, match="cleanup history"):
        module.downgrade()
    assert (
        queries
        == [
            "SELECT 1 FROM resume_upload_cleanups c LEFT JOIN resume_uploads u ON u.id=c.upload_id "
            "WHERE u.source_format IS NULL OR u.source_format NOT IN ('pdf','docx') LIMIT 1"
        ]
        and batches == []
    )


@pytest.mark.parametrize("retained", [True, False])
def test_operator_downgrade_guard_has_same_native_or_unclassifiable_cleanup_cut(retained):
    queries = []

    def execute(statement):
        queries.append(str(statement))
        return Rows([(retained,)])

    connection = SimpleNamespace(execute=execute)
    if retained:
        with pytest.raises(contract.Schema20Denied, match="downgrade_refused"):
            contract.require_schema19_downgrade_safe(connection)
    else:
        contract.require_schema19_downgrade_safe(connection)
    assert len(queries) == 1 and "resume_upload_cleanups" in queries[0]


def test_lost20_ddl_acknowledgement_is_reconciled_without_replaying_or_adopting_old_journal(
    tmp_path, monkeypatch
):
    import sqlalchemy

    raw = {
        **asdict(FixtureBoundary().plan),
        "schema_contract": contract.CONTRACT,
        "database_schema": contract.BEFORE,
        "prior_sources": [],
    }
    plan = reviewed.load_plan(reviewed.rolling.canonical(raw))
    tmp_path.chmod(0o700)
    journal = reviewed.rolling.Journal(tmp_path, plan)
    boundary = reviewed.NativeSchema20Boundary(ROOT, contract.BEFORE)
    state = {"schema": contract.BEFORE, "ddl": 0, "deny_fresh": True}

    @contextmanager
    def transaction():
        yield

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def begin(self):
            return transaction()

        def exec_driver_sql(self, _sql):
            pass

    class Engine:
        def connect(self):
            return Connection()

        def dispose(self):
            pass

    def global_gate(self, current):
        if current.database_schema == contract.SCHEMA and state["deny_fresh"]:
            raise reviewed.rolling.RollingDenied("inert_fresh_configuration_changed")

    monkeypatch.setattr(reviewed.NativeSchema20Boundary, "verify_global", global_gate)
    monkeypatch.setattr(
        reviewed.docker, "bootstrap_password", lambda _deadline: "inert-fixture-only"
    )
    monkeypatch.setattr(sqlalchemy, "create_engine", lambda *args, **kwargs: Engine())
    monkeypatch.setattr(step, "verify_identity", lambda *args: None)
    monkeypatch.setattr(step, "marker", lambda *args: state["schema"])
    monkeypatch.setattr(step, "retained_digest", lambda *args: "a" * 64)
    monkeypatch.setattr(step, "schema_acl", lambda *args: [])
    monkeypatch.setattr(step, "drained", lambda *args: None)
    monkeypatch.setattr(contract, "verify_shape", lambda *args: None)
    monkeypatch.setattr(reviewed, "verify_permissions", lambda *args: None)

    def ddl(*args, **kwargs):
        state["ddl"] += 1
        state["schema"] = contract.SCHEMA
        raise RuntimeError("inert lost original acknowledgement")

    monkeypatch.setattr(step, "upgrade_transaction", ddl)
    with pytest.raises(RuntimeError, match="lost original"):
        reviewed.migration(plan, boundary, journal)
    assert journal.read("schema20-migration.started") is not None
    assert journal.read("schema19-migration.started") is None
    with pytest.raises(reviewed.rolling.RollingDenied, match="reconcile_only"):
        reviewed.migration(plan, boundary, journal)
    state["schema"] = contract.BEFORE
    before_result = reviewed.migration(plan, boundary, journal, reconcile=True)
    assert before_result["status"] == "schema19_observed_reconcile_only"
    assert state["ddl"] == 1 and journal.read("schema20-migration.checked") is None
    state["schema"] = contract.SCHEMA
    with pytest.raises(reviewed.rolling.RollingDenied, match="fresh_configuration"):
        reviewed.migration(plan, boundary, journal, reconcile=True)
    assert state["ddl"] == 1 and journal.read("schema20-migration.checked") is None
    state["deny_fresh"] = False
    result = reviewed.migration(plan, boundary, journal, reconcile=True)
    assert result["schema"] == contract.SCHEMA and result["replay_authorized"] is False
    assert state["ddl"] == 1 and journal.read("schema20-migration.checked") is not None
