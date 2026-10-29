"""Exact25 transport/source and one-shot operator controls; all provider IO inert."""

from __future__ import annotations

import hashlib
import importlib
import sys
from contextlib import contextmanager
from dataclasses import asdict, replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from backend.tests.test_native_resume_schema20_contract import Session
from backend.tests.test_neon_schema19_release_transport import Reader, fresh_receipt  # noqa: F401
from backend.tests.test_rolling_release import FixtureBoundary

from scripts import neon_direct_identity as neon
from scripts import unified_credit_schema25_contract as contract
from scripts import unified_credit_schema25_step as step

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "infra/gcp"))
reviewed = importlib.import_module("unified_credit_schema25_release")
identity_step = reviewed.identity_step


@pytest.mark.parametrize("alias", tuple(neon.FRESH_RELEASE_ACTORS))
def test25_target_is_exact_numeric_source_native_identity_and_opaque_purpose(alias, monkeypatch):
    reader = Reader(alias)
    binding, target = neon.collect_schema25_release_target(
        reader, reader.reference, alias=alias, database_owner_oid=neon.FRESH_DATABASE_OWNER_OID
    )
    assert reader.calls == ["describe", "access", "describe"]
    assert binding.purpose == "schema25_fresh_release_capture"
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
        neon.collect_schema21_release_target,
        neon.collect_release_target,
        neon.collect_source_release_target,
    ],
)
def test_existing_purposes_do_not_adopt25(factory):
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


@pytest.mark.parametrize(
    "schema", [contract.BEFORE, "20261021_0020", "20261015_0018", "latest", None, True]
)
def test25_factory_refuses_other_schema_before_any_secret_read(schema):
    reader = Reader()
    with pytest.raises(neon.NeonIdentityDenied):
        neon.collect_schema25_release_target(
            reader,
            reader.reference,
            alias="observer",
            database_owner_oid=neon.FRESH_DATABASE_OWNER_OID,
            expected_schema=schema,
        )
    assert reader.calls == []


def test_source25_chain_is_distinct_and_21_source_verifier_remains_frozen(monkeypatch):
    old = object.__new__(reviewed.previous.Source21Boundary)
    old.root = ROOT
    new = object.__new__(reviewed.Source25Boundary)
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
    with pytest.raises(reviewed.rolling.RollingDenied, match="schema21_exact_source_chain"):
        old.verify_source_image(**values)
    monkeypatch.setattr(
        reviewed.release.source_guard,
        "migration_chain",
        lambda *args, **kwargs: reviewed.previous.CHAIN,
    )
    with pytest.raises(reviewed.rolling.RollingDenied, match="schema25_exact_source_chain"):
        new.verify_source_image(**values)


def test25_plan_cannot_be_adopted_by19_or18_loaders(tmp_path):
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
    boundary = reviewed.NativeSchema25Boundary(ROOT, contract.BEFORE)
    with pytest.raises(reviewed.rolling.RollingDenied, match="original_plan_journal"):
        reviewed.migration(before, boundary, old_journal)


@pytest.mark.parametrize("phase", [contract.BEFORE, contract.SCHEMA])
def test_native25_target_uses_distinct_phase_reader_and_retains_transport_acl_guards(
    monkeypatch, phase
):
    observed: list[Any] = []
    monkeypatch.setattr(
        step, "verify_new_permissions", lambda *args: observed.append("exact_new_acl")
    )
    monkeypatch.setattr(
        contract, "verify_empty_new_tables", lambda *args: observed.append("new_empty")
    )
    monkeypatch.setattr(
        reviewed.previous.contract,
        "verify_reset_permissions",
        lambda *args: observed.append("exact_reset_acl"),
    )

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

    boundary = object.__new__(reviewed.NativeSchema25Boundary)
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
    monkeypatch.setattr(neon, "collect_schema21_release_target", collector("21"))
    monkeypatch.setattr(neon, "collect_schema25_release_target", collector("25"))
    monkeypatch.setattr(neon, "verify_transport", lambda *args: observed.append("transport"))
    monkeypatch.setattr(
        neon, "verify_native_target", lambda *args: observed.append("native_target")
    )
    monkeypatch.setattr(
        reviewed.release, "verify_fresh_role_phase", lambda *args: observed.append("serving_acl")
    )
    monkeypatch.setattr(
        identity_step,
        "verify_financial_catalog",
        lambda _db, _module: observed.append("additive_financial_catalog"),
    )
    monkeypatch.setattr(contract, "verify_shape", lambda *args: observed.append("catalog20"))
    monkeypatch.setattr(contract, "verify_before_shape", lambda *args: observed.append("catalog19"))
    monkeypatch.setattr(
        reviewed, "verify_permissions", lambda *args: observed.append("exact19_permissions")
    )
    monkeypatch.setattr(identity_step, "drained", lambda *args: observed.append("drained"))
    monkeypatch.setattr(reviewed.native, "financial_digest", lambda *args: "a" * 64)
    assert boundary._target("inert-runtime:2", "inert-observer:2", "inert-retired:2") == "a" * 64
    label = "25" if phase == contract.SCHEMA else "21"
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
    assert "additive_financial_catalog" in observed
    assert "exact_reset_acl" in observed
    assert ("exact_new_acl" in observed) is (phase == contract.SCHEMA)
    assert ("new_empty" in observed) is (phase == contract.SCHEMA)
    assert observed.count("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY") == 2


@pytest.mark.parametrize("coordinator_reopened", [False, True])
def test_lost25_ddl_acknowledgement_is_reconciled_without_replaying_or_adopting_old_journal(
    tmp_path, monkeypatch, coordinator_reopened
):
    import sqlalchemy

    monkeypatch.setattr(reviewed.previous.contract, "verify_reset_permissions", lambda *args: None)

    raw = {
        **asdict(FixtureBoundary().plan),
        "schema_contract": contract.CONTRACT,
        "database_schema": contract.BEFORE,
        "prior_sources": [],
    }
    plan = reviewed.load_plan(reviewed.rolling.canonical(raw))
    tmp_path.chmod(0o700)
    journal = reviewed.rolling.Journal(tmp_path, plan)
    boundary = reviewed.NativeSchema25Boundary(ROOT, contract.BEFORE)
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

    monkeypatch.setattr(reviewed.NativeSchema25Boundary, "verify_global", global_gate)
    monkeypatch.setattr(reviewed.migration_metadata, "initialized_metadata", lambda *args: transaction())
    def coordinator_gate(*args):
        if coordinator_reopened:
            raise reviewed.rolling.RollingDenied("schema25_coordinator_admission_not_closed")

    monkeypatch.setattr(reviewed.NativeSchema25Boundary, "verify_coordinator_closed", coordinator_gate)
    monkeypatch.setattr(
        reviewed.NativeSchema25Boundary,
        "observed_disabled_flags",
        lambda *args: {name: "false" for name in contract.DISABLED_FLAGS},
    )
    monkeypatch.setattr(step, "verify_new_permissions", lambda *args: None)
    monkeypatch.setattr(
        reviewed.docker, "bootstrap_password", lambda _deadline: "inert-fixture-only"
    )
    monkeypatch.setattr(sqlalchemy, "create_engine", lambda *args, **kwargs: Engine())
    monkeypatch.setattr(identity_step, "verify_identity", lambda *args: None)
    monkeypatch.setattr(contract, "marker", lambda *args: state["schema"])
    monkeypatch.setattr(step, "retained_digest", lambda *args: "a" * 64)
    monkeypatch.setattr(identity_step, "schema_acl", lambda *args: [])
    monkeypatch.setattr(identity_step, "drained", lambda *args: None)
    monkeypatch.setattr(contract, "verify_shape", lambda *args: None)
    monkeypatch.setattr(reviewed, "verify_permissions", lambda *args: None)

    def ddl(*args, **kwargs):
        state["ddl"] += 1
        state["schema"] = contract.SCHEMA
        raise RuntimeError("inert lost original acknowledgement")

    monkeypatch.setattr(step, "upgrade_transaction", ddl)
    if coordinator_reopened:
        with pytest.raises(reviewed.rolling.RollingDenied, match="admission_not_closed"):
            reviewed.migration(plan, boundary, journal)
        assert journal.read("schema25-migration.started") is None
        assert state["ddl"] == 0 and state["schema"] == contract.BEFORE
        return
    with pytest.raises(RuntimeError, match="lost original"):
        reviewed.migration(plan, boundary, journal)
    assert journal.read("schema25-migration.started") is not None
    assert journal.read("schema19-migration.started") is None
    with pytest.raises(reviewed.rolling.RollingDenied, match="reconcile_only"):
        reviewed.migration(plan, boundary, journal)
    state["schema"] = contract.BEFORE
    before_result = reviewed.migration(plan, boundary, journal, reconcile=True)
    assert before_result["status"] == "schema21_observed_reconcile_only"
    assert state["ddl"] == 1 and journal.read("schema25-migration.checked") is None
    state["schema"] = contract.SCHEMA
    with pytest.raises(reviewed.rolling.RollingDenied, match="fresh_configuration"):
        reviewed.migration(plan, boundary, journal, reconcile=True)
    assert state["ddl"] == 1 and journal.read("schema25-migration.checked") is None
    state["deny_fresh"] = False
    result = reviewed.migration(plan, boundary, journal, reconcile=True)
    assert result["schema"] == contract.SCHEMA and result["replay_authorized"] is False
    assert state["ddl"] == 1 and journal.read("schema25-migration.checked") is not None


@pytest.mark.parametrize("flag", contract.DISABLED_FLAGS)
@pytest.mark.parametrize("location", ["current", "prior"])
def test_fresh_flags_refuse_enabled_or_secret_backed_admission(flag, location):
    fixture = FixtureBoundary()
    plan = reviewed.load_plan(
        reviewed.rolling.canonical(
            {
                **asdict(fixture.plan),
                "schema_contract": contract.CONTRACT,
                "database_schema": contract.SCHEMA,
                "prior_sources": [],
            }
        )
    )
    boundary = object.__new__(reviewed.NativeSchema25Boundary)
    current: dict[str, Any] = {"template": {"containers": [{"env": []}]}}
    prior: dict[str, Any] = {"containers": [{"env": []}]}
    boundary.service = lambda _name: current
    boundary.revision = lambda *_args: prior
    assert boundary.observed_disabled_flags(plan) == {
        name: "false" for name in contract.DISABLED_FLAGS
    }
    target = current["template"] if location == "current" else prior
    for value in (
        {"value": "true"},
        {"valueSource": {"secretKeyRef": {"secret": "inert", "version": "1"}}},
    ):
        target["containers"][0]["env"] = [{"name": flag, **value}]
        with pytest.raises(reviewed.rolling.RollingDenied, match="admission_must_stay_closed"):
            boundary.observed_disabled_flags(plan)


def test_release_wrapper_requires_exact25_plan_journal_and_reports_real_schema(
    tmp_path, monkeypatch
):
    old = FixtureBoundary().plan
    plan = reviewed.load_plan(
        reviewed.rolling.canonical(
            {
                **asdict(old),
                "schema_contract": contract.CONTRACT,
                "database_schema": contract.SCHEMA,
                "prior_sources": [],
            }
        )
    )
    tmp_path.chmod(0o700)
    journal = reviewed.rolling.Journal(tmp_path, plan)
    boundary = reviewed.NativeSchema25Boundary(ROOT)
    calls = []

    def advanced(*args):
        calls.append(args)
        return {
            "status": "joined_schema18_release_observed",
            "schema": "20261015_0018",
            "rolling_release_complete": True,
            "queue_admission": "closed",
            "goal_complete": False,
        }

    monkeypatch.setattr(reviewed.rolling, "advance", advanced)
    result = reviewed.advance(plan, boundary, journal)
    assert result["schema"] == contract.SCHEMA
    assert result["status"] == "joined_schema25_release_observed"
    assert result["goal_complete"] is False and result["queue_admission"] == "closed"
    before = replace(plan, database_schema=contract.BEFORE)
    with pytest.raises(reviewed.rolling.RollingDenied, match="migration_must_be_observed"):
        reviewed.advance(before, boundary, journal)
    assert len(calls) == 1
    assert set(contract.DISABLED_FLAGS) <= set(plan.disabled_flags())


def test_staged_services_explicitly_keep_all_new_flags_off():
    fixture = FixtureBoundary()
    plan = reviewed.load_plan(
        reviewed.rolling.canonical(
            {
                **asdict(fixture.plan),
                "schema_contract": contract.CONTRACT,
                "database_schema": contract.SCHEMA,
                "prior_sources": [],
            }
        )
    )
    for pin in plan.services:
        body = reviewed.rolling.stage_body(plan, pin, fixture.service(pin.service))
        flags = reviewed.rolling.environment(body["template"])
        assert all(
            flags[name] == {"name": name, "value": "false"} for name in contract.DISABLED_FLAGS
        )
        assert flags["AUTO_DB_MIGRATE"]["value"] == "false"


def test_rollback_requires_exact_prior_image_provenance_and_rehearsed_old_app(monkeypatch):
    fixture = FixtureBoundary()
    previous = fixture.plan.services[0].prior_release
    proof = reviewed.PriorSource(previous, "sha256:" + "f" * 64, "c" * 36, 123)
    plan = reviewed.load_plan(
        reviewed.rolling.canonical(
            {
                **asdict(fixture.plan),
                "schema_contract": contract.CONTRACT,
                "database_schema": contract.SCHEMA,
                "prior_sources": [asdict(proof)],
            }
        )
    )
    calls = []
    boundary = object.__new__(reviewed.NativeSchema25Boundary)
    boundary.root = ROOT
    boundary.revision = fixture.revision
    boundary.observer = SimpleNamespace(
        _verify_source_image_provenance=lambda **kwargs: calls.append(kwargs)
    )
    monkeypatch.setattr(
        reviewed, "prior_app_files", lambda *args: {"backend/app/main.py": b"inert prior"}
    )
    monkeypatch.setattr(reviewed, "prior_app_digest", lambda *args: reviewed.ROLLBACK_APP_SHA256)
    reviewed.verify_rollback_sources(plan, boundary)
    assert calls == [
        dict(
            release=previous,
            image_digest=proof.image_digest,
            build_id=proof.build_id,
            ci_run_id=123,
        )
    ]
    with pytest.raises(reviewed.rolling.RollingDenied, match="native_source_proof_required"):
        reviewed.verify_rollback_sources(replace(plan, prior_sources=()), boundary)
    monkeypatch.setattr(reviewed, "prior_app_digest", lambda *args: "0" * 64)
    with pytest.raises(reviewed.rolling.RollingDenied, match="compatibility_unqualified"):
        reviewed.verify_rollback_sources(plan, boundary)


def test_cli_observe_requires_private_exact_plan_and_does_not_mutate(tmp_path, monkeypatch, capsys):
    plan = reviewed.load_plan(
        reviewed.rolling.canonical(
            {
                **asdict(FixtureBoundary().plan),
                "schema_contract": contract.CONTRACT,
                "database_schema": contract.SCHEMA,
                "prior_sources": [],
            }
        )
    )
    path = tmp_path / "plan.json"
    raw = reviewed.rolling.canonical(asdict(plan))
    path.write_bytes(raw)
    path.chmod(0o600)
    calls = []
    monkeypatch.setattr(
        reviewed.NativeSchema25Boundary, "verify_global", lambda *args: calls.append("read")
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "schema25",
            "observe",
            "--plan",
            str(path),
            "--plan-sha256",
            hashlib.sha256(raw).hexdigest(),
        ],
    )
    reviewed.main()
    assert calls == ["read"] and '"provider_mutations": 0' in capsys.readouterr().out
    path.write_bytes(raw + b" ")
    with pytest.raises(reviewed.rolling.RollingDenied, match="exact_plan_pin"):
        reviewed.main()
    assert calls == ["read"]


def test_joined_flags_off_release_then_traffic_only_rollback_retains_old_state(
    tmp_path, monkeypatch
):
    fixture = FixtureBoundary()
    plan = reviewed.load_plan(
        reviewed.rolling.canonical(
            {
                **asdict(fixture.plan),
                "schema_contract": contract.CONTRACT,
                "database_schema": contract.SCHEMA,
                "prior_sources": [],
            }
        )
    )
    fixture.plan = plan
    boundary = object.__new__(reviewed.NativeSchema25Boundary)
    boundary.root, boundary.schema = ROOT, contract.SCHEMA
    for name in ("service", "revision", "iam", "patch", "operation", "health"):
        setattr(boundary, name, getattr(fixture, name))
    active = {"new_credit_activity": False}

    def global_check(current):
        if active["new_credit_activity"]:
            raise contract.Schema25Denied("schema25_new_financial_activity_refused")
        fixture.verify_global(current)
        boundary.observed_disabled_flags(current)

    boundary.verify_global = global_check
    proofs = []
    monkeypatch.setattr(
        reviewed, "verify_rollback_sources", lambda *args: proofs.append("prior-source")
    )
    tmp_path.chmod(0o700)
    journal = reviewed.rolling.Journal(tmp_path, plan)
    assert reviewed.advance(plan, boundary, journal)["schema"] == contract.SCHEMA
    assert len(fixture.writes) == 6
    active["new_credit_activity"] = True
    with pytest.raises(contract.Schema25Denied, match="financial_activity"):
        reviewed.rollback(plan, boundary, journal)
    assert len(fixture.writes) == 6 and proofs == []
    active["new_credit_activity"] = False
    result = reviewed.rollback(plan, boundary, journal)
    assert result["schema"] == contract.SCHEMA
    assert result["status"] == "prior_traffic_restored_schema25"
    assert len(fixture.writes) == 9 and proofs == ["prior-source"]
    assert all(mask == "traffic" for _, _, mask in fixture.writes[-3:])
    for pin in plan.services:
        active_traffic = [
            row for row in fixture.service(pin.service)["traffic"] if row.get("percent")
        ]
        assert active_traffic == list(pin.traffic)
        assert fixture.revision(pin.service, pin.prior_revision) is not None


@pytest.mark.parametrize(
    "error",
    [
        RuntimeError("vendor rejected postgresql://private-password@secret-host"),
        reviewed.rolling.RollingDenied("schema25_private_credential_identifier"),
        reviewed.rolling.RollingDenied("schema25_exact_plan_pin_required", "secret second arg"),
    ],
)
def test_cli_failure_response_redacts_vendor_and_unapproved_messages(error):
    result = reviewed.failure_response(error)
    assert result == {
        "status": "schema25_stopped_reconcile_only",
        "code": "unclassified_failure",
        "replay_authorized": False,
        "goal_complete": False,
    }


@pytest.mark.parametrize(
    "error",
    [
        reviewed.rolling.RollingDenied("schema25_exact_plan_pin_required"),
        reviewed.rolling.RollingDenied("schema25_all_candidate_admission_must_stay_closed"),
        contract.Schema25Denied("schema25_new_admission_must_remain_closed"),
    ],
)
def test_cli_known_constant_plan_and_flag_refusals_remain_actionable(error):
    assert reviewed.failure_response(error)["code"] == str(error)
