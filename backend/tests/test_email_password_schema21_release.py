"""Inert exact21 transport, source, one-shot and retained-data controls; no native IO."""
from __future__ import annotations

import hashlib
import importlib
import sys
from contextlib import contextmanager
from dataclasses import asdict, replace
from pathlib import Path
from types import SimpleNamespace

import pytest
from backend.tests.test_native_resume_schema20_contract import Rows, Session
from backend.tests.test_neon_schema19_release_transport import Reader, fresh_receipt  # noqa: F401
from backend.tests.test_rolling_release import FixtureBoundary

from scripts import email_password_schema21_contract as contract
from scripts import email_password_schema21_step as step
from scripts import neon_direct_identity as neon

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "infra/gcp"))
reviewed = importlib.import_module("email_password_schema21_release")


@pytest.mark.parametrize("alias", tuple(neon.FRESH_RELEASE_ACTORS))
def test21_target_is_exact_numeric_source_native_identity_and_opaque_purpose(alias, monkeypatch):
    reader = Reader(alias)
    binding, target = neon.collect_schema21_release_target(
        reader, reader.reference, alias=alias, database_owner_oid=neon.FRESH_DATABASE_OWNER_OID
    )
    assert reader.calls == ["describe", "access", "describe"]
    assert binding.purpose == "schema21_fresh_release_capture"
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
        neon.collect_schema20_release_target,
        neon.collect_release_target,
        neon.collect_source_release_target,
    ],
)
def test_existing_purposes_do_not_adopt21(factory):
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


@pytest.mark.parametrize("schema", [contract.BEFORE, contract.INTERMEDIATE, "20261015_0018", "latest", None, True])
def test21_factory_refuses_other_schema_before_any_secret_read(schema):
    reader = Reader()
    with pytest.raises(neon.NeonIdentityDenied):
        neon.collect_schema21_release_target(
            reader,
            reader.reference,
            alias="observer",
            database_owner_oid=neon.FRESH_DATABASE_OWNER_OID,
            expected_schema=schema,
        )
    assert reader.calls == []


def test_source20_chain_is_distinct_and_19_source_verifier_remains_frozen(monkeypatch):
    old = object.__new__(reviewed.previous.Source19Boundary)
    old.root = ROOT
    new = object.__new__(reviewed.Source21Boundary)
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
    with pytest.raises(reviewed.rolling.RollingDenied, match="schema21_exact_source_chain"):
        new.verify_source_image(**values)


def test21_plan_cannot_be_adopted_by19_or18_loaders(tmp_path):
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
    boundary = reviewed.NativeSchema21Boundary(ROOT, contract.BEFORE)
    with pytest.raises(reviewed.rolling.RollingDenied, match="original_plan_journal"):
        reviewed.migration(before, boundary, old_journal)


@pytest.mark.parametrize("phase", [contract.BEFORE, contract.SCHEMA])
def test_native20_target_uses_distinct_phase_reader_and_retains_transport_acl_guards(
    monkeypatch, phase
):
    observed = []
    monkeypatch.setattr(contract, "verify_reset_permissions", lambda *args: observed.append("exact_reset_acl"))

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

    boundary = object.__new__(reviewed.NativeSchema21Boundary)
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
    monkeypatch.setattr(neon, "collect_schema21_release_target", collector("21"))
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
    label = "21" if phase == contract.SCHEMA else "19"
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


@pytest.mark.parametrize("before", [contract.BEFORE, contract.SCHEMA])
def test21_upgrade_uses_only_existing_migration_in_the_original_transaction(monkeypatch, before):
    identity = reviewed.bootstrap_identity()
    state = {"schema": before, "ddl": 0}
    events = []
    monkeypatch.setattr(contract, "verify_reset_permissions", lambda *args: events.append("exact_reset_acl"))

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
    monkeypatch.setattr(contract, "verify_shape", lambda *args: events.append("exact21_catalog"))
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
        with pytest.raises(contract.Schema21Denied, match="never_replay"):
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
            < events.index("exact21_catalog")
        )
        assert events.count("native_identity") == 2 and "effective_nologin_owner" in events
        assert all(not value.startswith("ALTER ROLE ") for value in events)
        assert sum(value.startswith("GRANT CREATE ON SCHEMA ") for value in events) == 1
        assert sum(value.startswith("REVOKE CREATE ON SCHEMA ") for value in events) == 1
        assert sum(value.startswith("GRANT SELECT,INSERT,UPDATE,DELETE ON TABLE ") for value in events) == 1
        assert sum(value.startswith("GRANT SELECT ON TABLE ") for value in events) == 1
        assert events.index("original_ddl") < events.index("exact_reset_acl")


def test_lost21_ddl_acknowledgement_is_reconciled_without_replaying_or_adopting_old_journal(
    tmp_path, monkeypatch
):
    import sqlalchemy
    monkeypatch.setattr(contract, "verify_reset_permissions", lambda *args: None)

    raw = {
        **asdict(FixtureBoundary().plan),
        "schema_contract": contract.CONTRACT,
        "database_schema": contract.BEFORE,
        "prior_sources": [],
    }
    plan = reviewed.load_plan(reviewed.rolling.canonical(raw))
    tmp_path.chmod(0o700)
    journal = reviewed.rolling.Journal(tmp_path, plan)
    boundary = reviewed.NativeSchema21Boundary(ROOT, contract.BEFORE)
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

    monkeypatch.setattr(reviewed.NativeSchema21Boundary, "verify_global", global_gate)
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
    assert journal.read("schema21-migration.started") is not None
    assert journal.read("schema19-migration.started") is None
    with pytest.raises(reviewed.rolling.RollingDenied, match="reconcile_only"):
        reviewed.migration(plan, boundary, journal)
    state["schema"] = contract.BEFORE
    before_result = reviewed.migration(plan, boundary, journal, reconcile=True)
    assert before_result["status"] == "schema19_observed_reconcile_only"
    assert state["ddl"] == 1 and journal.read("schema21-migration.checked") is None
    state["schema"] = contract.SCHEMA
    with pytest.raises(reviewed.rolling.RollingDenied, match="fresh_configuration"):
        reviewed.migration(plan, boundary, journal, reconcile=True)
    assert state["ddl"] == 1 and journal.read("schema21-migration.checked") is None
    state["deny_fresh"] = False
    result = reviewed.migration(plan, boundary, journal, reconcile=True)
    assert result["schema"] == contract.SCHEMA and result["replay_authorized"] is False
    assert state["ddl"] == 1 and journal.read("schema21-migration.checked") is not None


class NewColumns:
    def __init__(self):
        self.rows = [dict(relname="password_reset_tokens", relkind="r", relpersistence="p", relispartition=False,
                         attname=name, column_type=kind, attnotnull=name not in {"native_revision_sha256", "consumed_at"},
                         attidentity="", attgenerated="", default_collation=True, column_default=None)
                     for name, kind in contract._COLUMNS.items()]
        self.rows.append(dict(relname="users", relkind="r", relpersistence="p", relispartition=False,
                              attname="credential_epoch", column_type="bigint", attnotnull=True,
                              attidentity="", attgenerated="", default_collation=True, column_default="'0'::bigint"))

    def execute(self, statement, params):
        assert str(statement).startswith("SELECT ") and params == {"namespace": 2200}
        return SimpleNamespace(mappings=lambda: SimpleNamespace(all=lambda: self.rows))


@pytest.mark.parametrize("fault", [None, "extra", "epoch_default", "nullable", "type", "generated", "partition", "duplicate"])
def test21_exact_new_column_contract_rejects_drift(fault):
    db = NewColumns()
    if fault == "extra":
        db.rows.append({**db.rows[0], "attname": "unreviewed"})
    elif fault == "epoch_default":
        db.rows[-1]["column_default"] = "1"
    elif fault == "nullable":
        db.rows[2]["attnotnull"] = False
    elif fault == "type":
        db.rows[2]["column_type"] = "text"
    elif fault == "generated":
        db.rows[2]["attgenerated"] = "s"
    elif fault == "partition":
        db.rows[2]["relispartition"] = True
    elif fault == "duplicate":
        db.rows.append(db.rows[0])
    if fault is None:
        contract._columns(db, 2200)
    else:
        with pytest.raises(contract.Schema21Denied, match="column_shape"):
            contract._columns(db, 2200)


@pytest.mark.parametrize("fault", [None, "public", "grant_option", "owner", "missing", "extra"])
def test21_new_table_delegated_acl_is_exact_and_preserves_owner(fault):
    rows = [dict(owner_name="owner", owner_grant=True, grantee="runtime", privilege_type=p, is_grantable=False)
            for p in ("SELECT", "INSERT", "UPDATE", "DELETE")]
    rows.append(dict(owner_name="owner", owner_grant=True, grantee="observer", privilege_type="SELECT", is_grantable=False))
    if fault == "public":
        rows[0]["grantee"] = "PUBLIC"
    elif fault == "grant_option":
        rows[0]["is_grantable"] = True
    elif fault == "owner":
        rows[0]["owner_name"] = "foreign"
    elif fault == "missing":
        rows.pop()
    elif fault == "extra":
        rows.append({**rows[0], "privilege_type": "TRUNCATE"})
    db = SimpleNamespace(execute=lambda *args: SimpleNamespace(mappings=lambda: SimpleNamespace(all=lambda: rows)))
    if fault is None:
        contract.verify_reset_permissions(db, "public", "runtime", "owner", "observer")
    else:
        with pytest.raises(contract.Schema21Denied, match="reset_table_acl"):
            contract.verify_reset_permissions(db, "public", "runtime", "owner", "observer")


class RetainedRows:
    def __init__(self, schema, *, changed=False, epoch=0, tokens=0):
        self.schema, self.changed, self.epoch, self.tokens = schema, changed, epoch, tokens
        self.queries = []

    def execute(self, statement, params=None):
        sql = str(statement)
        self.queries.append(sql)
        if "version_num" in sql:
            return Rows([(self.schema,)])
        if "pg_class" in sql:
            return Rows([(name,) for name in ["alembic_version", "users", "candidate_identity_history", "service_credit_events"]
                        + (["password_reset_tokens"] if self.schema == contract.SCHEMA else [])])
        if "count(*)" in sql:
            return SimpleNamespace(scalar_one=lambda: self.epoch if "credential_epoch" in sql else self.tokens)
        if '"users"' in sql and self.schema == contract.SCHEMA:
            assert "to_jsonb(t)-'credential_epoch'" in sql
        elif self.schema == contract.BEFORE:
            assert "credential_epoch" not in sql
        return [("b" * 64 if self.changed and '"service_credit_events"' in sql else "a" * 64,)]


def test21_retention_normalizes_only_new_zero_epoch_and_empty_token_table():
    before = RetainedRows(contract.BEFORE)
    after = RetainedRows(contract.SCHEMA)
    identity = reviewed.bootstrap_identity()
    assert step.retained_digest(before, identity) == step.retained_digest(after, identity)
    assert step.retained_digest(after, identity) != step.retained_digest(RetainedRows(contract.SCHEMA, changed=True), identity)
    assert any('"candidate_identity_history"' in sql for sql in after.queries)
    assert any('"service_credit_events"' in sql for sql in after.queries)


@pytest.mark.parametrize("values", [{"epoch": 1}, {"tokens": 1}])
def test21_retention_cannot_ignore_new_reset_history_or_changed_credentials(values):
    with pytest.raises(contract.Schema21Denied, match="new_reset_state_changed"):
        step.retained_digest(RetainedRows(contract.SCHEMA, **values), reviewed.bootstrap_identity())


def test21_intermediate_or_unknown_marker_never_counts_as_committed_target():
    for schema in (contract.INTERMEDIATE, "latest", True):
        with pytest.raises(contract.Schema21Denied, match="marker_refused"):
            contract.marker(SimpleNamespace(execute=lambda *args, schema=schema: Rows([(schema,)])))


@pytest.mark.parametrize("fault", [None, "status", "cascade", "unvalidated", "extra", "duplicate"])
def test21_reset_constraints_require_exact_finite_status_and_owned_cascade(fault):
    common = dict(relname="password_reset_tokens", convalidated=True, condeferrable=False, condeferred=False,
                  conislocal=True, coninhcount=0, connoinherit=False, referenced_namespace=None,
                  referenced_table=None, referenced_columns=[], confupdtype=" ", confdeltype=" ", confmatchtype=" ")
    rows = [dict(common, conname="password_reset_tokens_pkey", contype="p", columns=["id"], definition=""),
            dict(common, conname="password_reset_tokens_token_sha256_key", contype="u", columns=["token_sha256"], definition=""),
            {**common, "conname": "password_reset_tokens_user_id_fkey", "contype": "f", "columns": ["user_id"],
             "referenced_namespace": "public", "referenced_table": "users", "referenced_columns": ["id"],
             "confupdtype": "a", "confdeltype": "c", "confmatchtype": "s", "definition": ""},
            dict(common, conname="ck_password_reset_status", contype="c", columns=["status"], definition=
                 "CHECK (((status)::text = ANY ((ARRAY['PENDING'::character varying, 'PROCESSING'::character varying, 'COMPLETED'::character varying, 'INVALIDATED'::character varying, 'UNAVAILABLE'::character varying])::text[])))")]
    if fault == "status":
        rows[-1]["definition"] = rows[-1]["definition"].replace("'PENDING'", "'PENDING '")
    elif fault == "cascade":
        rows[2]["confdeltype"] = "a"
    elif fault == "unvalidated":
        rows[-1]["convalidated"] = False
    elif fault == "extra":
        rows.append({**rows[-1], "conname": "unknown_check"})
    elif fault == "duplicate":
        rows.append(rows[0])
    db = SimpleNamespace(execute=lambda *args: SimpleNamespace(mappings=lambda: SimpleNamespace(all=lambda: rows)))
    if fault is None:
        contract._constraints(db, 2200, "public")
    else:
        with pytest.raises(contract.Schema21Denied):
            contract._constraints(db, 2200, "public")


@pytest.mark.parametrize("fault", [None, "partial", "expression", "direction", "uniqueness", "missing"])
def test21_reset_indexes_preserve_complete_token_uniqueness_and_owner_time_order(fault):
    common = dict(relname="password_reset_tokens", amname="btree", indisvalid=True, indisready=True,
                  indislive=True, indimmediate=True, indnullsnotdistinct=False, unconditional=True, plain_columns=True)
    rows = []
    for name, columns, unique, primary in [
        ("password_reset_tokens_pkey", ["id"], True, True),
        ("password_reset_tokens_token_sha256_key", ["token_sha256"], True, False),
        ("ix_password_reset_owner_created", ["user_id", "created_at"], False, False),
    ]:
        rows.append(dict(common, index_name=name, columns=columns, indisunique=unique, indisprimary=primary,
                         indnatts=len(columns), indnkeyatts=len(columns), options=[0]*len(columns),
                         collations=[0]*len(columns), column_collations=[0]*len(columns),
                         opclasses=["pg_catalog.int4_ops" if col=="user_id" else "pg_catalog.timestamp_ops" if col=="created_at" else "pg_catalog.text_ops" for col in columns]))
    if fault == "partial":
        rows[1]["unconditional"] = False
    elif fault == "expression":
        rows[1]["plain_columns"] = False
    elif fault == "direction":
        rows[-1]["options"] = [0, 1]
    elif fault == "uniqueness":
        rows[1]["indisunique"] = False
    elif fault == "missing":
        rows.pop()
    db = SimpleNamespace(execute=lambda *args: SimpleNamespace(mappings=lambda: SimpleNamespace(all=lambda: rows)))
    if fault is None:
        contract._indexes(db, 2200)
    else:
        with pytest.raises(contract.Schema21Denied):
            contract._indexes(db, 2200)
