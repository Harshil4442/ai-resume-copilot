"""Proposal-only fake SQL sequencing; never native migration qualification."""

import ast
import importlib.util
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

BASE = Path(os.getenv("HIREWIZ_REVIEW_SOURCE_ROOT", str(Path(__file__).resolve().parents[2])))
PROPOSAL = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(BASE), str(BASE / "backend"), str(BASE / "infra/gcp")]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


step = load("proposal_schema26_step", PROPOSAL / "backend/scripts/evidence_writer_schema26_step.py")
maintenance26 = importlib.import_module("schema26_maintenance_guard")
guard_fixtures = importlib.import_module("test_schema26_maintenance_guard")
baseline, observation = guard_fixtures.baseline, guard_fixtures.observation

metadata = load("proposal_schema26_metadata", BASE / "infra/gcp/schema26_migration_metadata.py")


class Result:
    def __init__(self, value=None, rows=()):
        self.value, self.rows = value, rows

    def scalar_one(self):
        return self.value

    def mappings(self):
        return self

    def all(self):
        return list(self.rows)

    def __iter__(self):
        return iter(self.rows)


class FakeConnection:
    dialect = SimpleNamespace(name="postgresql")

    def __init__(self):
        self.commands, self.calls = [], []
        self.active, self.lock, self.after = True, True, False
        self.purpose_bad, self.changed_data = 0, False
        self.acl_changed, self.cost_changed, self.table_acl_changed = False, False, False

    def in_transaction(self):
        return self.active

    def exec_driver_sql(self, sql):
        self.commands.append(sql)

    def execute(self, sql, params=None):
        value = str(sql)
        self.calls.append((value, params))
        if value == maintenance26.ACCOUNT_SQL:
            return Result(rows=[])
        if "relacl::text" in value:
            acl = "changed" if self.after and self.table_acl_changed else "same"
            return Result(rows=[(table, 24578, acl) for table in step.TABLES])
        if "pg_try_advisory" in value:
            return Result(self.lock)
        if "purpose<>" in value:
            return Result(self.purpose_bad)
        if "to_jsonb" in value:
            digest = "b" * 64 if self.after and self.changed_data else "a" * 64
            return Result(rows=[(digest,)])
        raise AssertionError("Unexpected fake SQL")


def prepared_guard():
    # Explicit inert native port exercises the real one-use final SQL/native
    # check; it cannot be supplied through the production constructor.
    result = object.__new__(maintenance26.NativeMaintenanceDdlGuard)
    result.prepared, result.started, result.closed = True, False, False
    result.baseline, result.owners = baseline(), ()
    result.sql_sha256 = maintenance26.fingerprint({"rows": [], "absence": {}})
    result.retired_sha256 = maintenance26.retirement_fingerprint(observation(), result.baseline)

    class Reader:
        calls = 0

        def observe(self):
            self.calls += 1
            assert self.calls == 1
            return observation()

        def close(self):
            pass

    result.final_reader = Reader()
    return result


@pytest.fixture
def fixture(monkeypatch):
    db = FakeConnection()
    final_guard = prepared_guard()
    identity = step.identity_step.MigrationIdentity(
        "a" * 64,
        16395,
        16391,
        "public",
        2200,
        6171,
        "hirewiz_bootstrap_20261013",
        16391,
        "hirewiz_migrator_20261013_r1",
        24578,
        "hirewiz_runtime_20261013_r1",
        24576,
        "hirewiz_observer_20261013_r1",
        24580,
    )
    source_calls, markers, upgrades = [], [], []

    def source(_root):
        source_calls.append(True)
        return "a" * 64

    def before(*_args, **_kwargs):
        markers.append("before25")

    def upgrade(cfg, revision):
        assert cfg.attributes["connection"] is db
        assert revision == "20261031_0026"
        assert cfg.get_main_option("script_location") == str(BASE / "backend/alembic")
        upgrades.append(revision)
        db.after = True

    monkeypatch.setattr(step.contract, "verify_source", source)
    monkeypatch.setattr(
        step.identity_step, "verify_identity", lambda *_: markers.append("identity")
    )
    monkeypatch.setattr(
        step.identity_step, "effective_owner", lambda *_: markers.append("effective_owner")
    )
    monkeypatch.setattr(
        step.identity_step,
        "schema_acl",
        lambda *_: [("acl", "changed" if db.after and db.acl_changed else "same")],
    )
    monkeypatch.setattr(step.previous, "verify_shape", before)
    monkeypatch.setattr(step.financial, "observe", lambda *_a, **_k: markers.append("financial25"))
    monkeypatch.setattr(step.financial, "liability_pins", lambda *_: (("held", "c" * 64, 100),))

    def preserve(_db, pins):
        assert pins == (("held", "c" * 64, 100),)
        step.need(not db.cost_changed, "unknown_obligation_changed")

    monkeypatch.setattr(step.financial, "verify_liability_pins", preserve)
    monkeypatch.setattr(step.contract, "observe", lambda *_a, **_k: markers.append("after26"))
    monkeypatch.setattr(
        step.contract,
        "verify_writer_permissions",
        lambda *_a, **_k: markers.append("writer_permissions"),
    )
    monkeypatch.setattr(
        step.previous_step,
        "verify_new_permissions",
        lambda *_: markers.append("financial_permissions"),
    )
    monkeypatch.setattr(step.command, "upgrade", upgrade)
    flags = {name: "false" for name in step.previous.DISABLED_FLAGS}
    return SimpleNamespace(
        db=db,
        final_guard=final_guard,
        identity=identity,
        flags=flags,
        source_calls=source_calls,
        markers=markers,
        upgrades=upgrades,
        run=lambda: step.upgrade_transaction(
            db, identity, root=BASE, flags=flags, maintenance_guard=final_guard
        ),
    )


def test_one_supplied_transaction_uses_standard_alembic_and_exact_role_acl_sequence(fixture):
    result = fixture.run()
    assert fixture.upgrades == ["20261031_0026"]
    assert result["committed"] is False and result["transaction_ready_to_commit"] is True
    assert (
        result["replay_authorized"]
        is result["migration_login_reenabled"]
        is result["new_admission_enabled"]
        is False
    )
    commands = fixture.db.commands
    assert commands[0] == "SET TRANSACTION ISOLATION LEVEL READ COMMITTED READ WRITE"
    assert commands[-2:] == [
        "RESET ROLE",
        'REVOKE CREATE ON SCHEMA "public" FROM "hirewiz_migrator_20261013_r1"',
    ]
    assert 'GRANT CREATE ON SCHEMA "public" TO "hirewiz_migrator_20261013_r1"' in commands
    assert commands.index(
        'LOCK TABLE "public"."evidence_items" IN ACCESS EXCLUSIVE MODE'
    ) < commands.index('LOCK TABLE "public"."password_reset_tokens" IN ACCESS EXCLUSIVE MODE')
    assert not any("LOGIN" in sql or "TRUNCATE" in sql or "DISABLE" in sql for sql in commands)
    assert fixture.markers == [
        "identity",
        "before25",
        "effective_owner",
        "financial25",
        "after26",
        "financial_permissions",
        "identity",
        "writer_permissions",
    ]
    assert len(fixture.source_calls) == 2
    token_queries = [
        sql for sql, _ in fixture.db.calls if '"password_reset_tokens"' in sql and "to_jsonb" in sql
    ]
    assert (
        len(token_queries) == 2
        and "- 'purpose'" not in token_queries[0]
        and "- 'purpose'" in token_queries[1]
    )


@pytest.mark.parametrize(
    "change",
    [
        "not_transaction",
        "wrong_dialect",
        "wrong_namespace",
        "enabled_credits",
        "lock_taken",
        "already26",
    ],
)
def test_before_refusals_send_no_ddl(fixture, monkeypatch, change):
    if change == "not_transaction":
        fixture.db.active = False
    elif change == "wrong_dialect":
        fixture.db.dialect = SimpleNamespace(name="sqlite")
    elif change == "wrong_namespace":
        from dataclasses import replace

        fixture.identity = replace(fixture.identity, namespace="foreign")
        fixture.run = lambda: step.upgrade_transaction(
            fixture.db,
            fixture.identity,
            root=BASE,
            flags=fixture.flags,
            maintenance_guard=fixture.final_guard,
        )
    elif change == "enabled_credits":
        fixture.flags["UNIFIED_CREDITS_ENABLED"] = "true"
    elif change == "lock_taken":
        fixture.db.lock = False
    else:
        monkeypatch.setattr(
            step.previous, "verify_shape", lambda *_: step.need(False, "exact25_required")
        )
    with pytest.raises((step.Migration26Denied, step.previous.Schema25Denied)):
        fixture.run()
    assert not fixture.upgrades and not any("GRANT" in sql for sql in fixture.db.commands)


@pytest.mark.parametrize(
    "change",
    [
        "changed_data",
        "purpose_bad",
        "acl_changed",
        "table_acl_changed",
        "cost_changed",
        "lost_transaction",
        "source_changed",
    ],
)
def test_after_refusals_never_repeat_ddl_or_report_commit(fixture, monkeypatch, change):
    if change in {"changed_data", "acl_changed", "table_acl_changed", "cost_changed"}:
        setattr(fixture.db, change, True)
    elif change == "purpose_bad":
        fixture.db.purpose_bad = 1
    elif change == "lost_transaction":
        original = step.command.upgrade

        def lost(*args):
            original(*args)
            fixture.db.active = False

        monkeypatch.setattr(step.command, "upgrade", lost)
    else:
        monkeypatch.setattr(
            step.contract, "verify_source", lambda *_: "b" * 64 if fixture.db.after else "a" * 64
        )
    with pytest.raises(step.Migration26Denied):
        fixture.run()
    assert len(fixture.upgrades) == 1


def test_unknown_alembic_error_propagates_exactly_without_retry(fixture, monkeypatch):
    error = RuntimeError("synthetic unknown execution")
    sends = []

    def unknown(*_):
        sends.append(True)
        raise error

    monkeypatch.setattr(step.command, "upgrade", unknown)
    with pytest.raises(RuntimeError) as caught:
        fixture.run()
    assert caught.value is error and sends == [True]


def test_affected_row_bound_and_no_cleartext_export(fixture, monkeypatch):
    monkeypatch.setattr(step, "MAX_ROWS", 1)
    with pytest.raises(step.Migration26Denied, match="affected_rows_bound"):
        fixture.run()
    assert not fixture.upgrades
    assert all("to_jsonb(t)" not in str(params) for _, params in fixture.db.calls)


def test_metadata_change_is_only_the_explicit_disjoint25_collector_and_schema():
    old = ast.parse((BASE / "infra/gcp/schema25_migration_metadata.py").read_text())
    current = ast.parse((BASE / "infra/gcp/schema26_migration_metadata.py").read_text())
    old.body.pop(0)
    current.body.pop(0)
    for node in ast.walk(current):
        if isinstance(node, ast.Attribute) and node.attr == "collect_schema25_release_target":
            node.attr = "collect_schema21_release_target"
        if isinstance(node, ast.Constant) and node.value == "20261028_0025":
            node.value = "20261023_0021"
    assert ast.dump(old, include_attributes=False) == ast.dump(current, include_attributes=False)


def test_metadata_refuses_a_preloaded_application_before_any_credential_port(monkeypatch):
    monkeypatch.setitem(sys.modules, "app.database", SimpleNamespace())
    with pytest.raises(metadata.rolling.RollingDenied, match="fresh_process_required"):
        with metadata.initialized_metadata(BASE, object(), "unused-reference"):
            pytest.fail("No scope allowed")


def test_metadata_selects_exact25_without_widening_the_old21_purpose(monkeypatch):
    for name in ("app.database", "backend.app.database", "app.models", "backend.app.models", "app"):
        monkeypatch.delitem(sys.modules, name, raising=False)
    error = RuntimeError("controlled stop before any target/credential/import")
    calls = []

    def collect(_reader, _reference, **kwargs):
        calls.append(kwargs)
        raise error

    observer = SimpleNamespace(
        _module=lambda *_: SimpleNamespace(
            neon_identity=SimpleNamespace(
                FRESH_DATABASE_OWNER_OID=16391, collect_schema25_release_target=collect
            )
        )
    )
    with pytest.raises(RuntimeError) as caught:
        with metadata.initialized_metadata(BASE, observer, "synthetic:1"):
            pytest.fail("No scope allowed")
    assert caught.value is error and calls == [
        {
            "alias": "replacement_runtime",
            "database_owner_oid": 16391,
            "expected_schema": "20261028_0025",
        }
    ]


def test_all_three_affected_tables_locked_and_exact_final_guard_consumed(fixture):
    fixture.run()
    assert step.TABLES == ("browser_assistance_reviews", "evidence_items", "password_reset_tokens")
    assert [sql for sql in fixture.db.commands if sql.startswith("LOCK TABLE")] == [
        f'LOCK TABLE "public"."{name}" IN ACCESS EXCLUSIVE MODE' for name in step.TABLES
    ]
    assert fixture.final_guard.started and fixture.final_guard.closed
    assert fixture.final_guard.final_reader.calls == 1
    assert len([sql for sql, _ in fixture.db.calls if sql == maintenance26.ACCOUNT_SQL]) == 1


def test_real_final_guard_metadata_refusal_executes_zero_alembic(fixture):
    def refuse():
        raise maintenance26.MaintenanceDenied("synthetic_final_metadata_refusal")

    fixture.final_guard.final_reader.observe = refuse
    with pytest.raises(maintenance26.MaintenanceDenied):
        fixture.run()
    assert fixture.upgrades == []
    assert fixture.final_guard.started and fixture.final_guard.closed


def test_plain_or_consumed_guard_cannot_reach_any_sql_or_ddl(fixture):
    for invalid in (object(), SimpleNamespace(prepared=True, started=False, closed=False)):
        with pytest.raises(step.Migration26Denied, match="native_maintenance_guard_required"):
            step.upgrade_transaction(
                fixture.db,
                fixture.identity,
                root=BASE,
                flags=fixture.flags,
                maintenance_guard=invalid,
            )
    assert fixture.db.commands == [] and fixture.upgrades == []
    fixture.run()
    before = list(fixture.db.commands)
    with pytest.raises(step.Migration26Denied, match="native_maintenance_guard_required"):
        fixture.run()
    assert fixture.db.commands == before and len(fixture.upgrades) == 1
