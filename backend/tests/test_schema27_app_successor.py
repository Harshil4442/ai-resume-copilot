"""Application-only traffic and refusal proof; native-shaped owned fixtures only."""
from __future__ import annotations

import copy
import sys
from dataclasses import asdict, fields, replace
from types import SimpleNamespace

import pytest
from infra.gcp import schema27_app_source as sources
from infra.gcp import schema27_app_successor as app
from test_schema26_app_successor import context as context
from test_schema26_app_successor import no_network as no_network
from test_schema27_continuity_bridge import lane as lane
from test_schema27_continuity_bridge_plan import current as current


def descriptor(release):
    return sources.QualifiedSource(release, "1" * 64, 316, "2" * 64, "3" * 64)


@pytest.fixture
def application(lane, tmp_path, monkeypatch):
    original = lane.plan
    values = {field.name: getattr(original, field.name) for field in fields(app.plans.App27Plan)
              if hasattr(original, field.name) and field.name not in {"schema_contract", "database_schema"}}
    plan = app.plans.App27Plan(**values, source_contract=descriptor(original.release),
        prior_contract=descriptor(original.prior_source.release), retained_acl_sha256="4" * 64)
    plan.validate()
    boundary = object.__new__(app.NativeApp27Boundary)
    boundary.__dict__.update(lane.boundary.__dict__)
    boundary.observer = SimpleNamespace(descriptor=plan.source_contract, verify_policy_configuration=lambda _: None)
    boundary.prior_observer = SimpleNamespace(descriptor=plan.prior_contract)
    observed = SimpleNamespace(catalog_sha256=plan.financial_sha256,
        retained_acl_sha256=plan.retained_acl_sha256, protected_liabilities=plan.protected_liabilities)
    calls = []

    def observe(database, nonce, *, expected_liabilities):
        assert database == plan.database and nonce == plan.nonce
        app.need(expected_liabilities == observed.protected_liabilities, "protected_liability_changed")
        calls.append("exact27")
        return observed

    monkeypatch.setattr(app, "observe_catalog27", observe)
    folder = tmp_path / "application-only"
    folder.mkdir(mode=0o700)
    lane.fixture.plan = plan
    return SimpleNamespace(plan=plan, boundary=boundary, fixture=lane.fixture,
        journal=app.bridge._bind_new(plan, folder), observed=observed, catalog_calls=calls)


def run(value, mode):
    return app.execute(mode, value.plan, value.boundary, value.journal)


def test_distinct_plan_roundtrip_never_adopts_bridge_or_other_schema(application, lane):
    c = application
    raw = app.rolling.canonical(asdict(c.plan))
    assert app.plans.load(raw, app.rolling.sha(asdict(c.plan))) == c.plan
    for changed in (replace(c.plan, database_schema=app.successor.SCHEMA),
                    replace(c.plan, source_contract=descriptor(c.plan.prior_source.release)),
                    replace(c.plan, database=replace(c.plan.database, runtime_role=("foreign", 9)))):
        with pytest.raises(app.rolling.RollingDenied):
            changed.validate()
    with pytest.raises(app.rolling.RollingDenied):
        app.execute("stage", c.plan, c.boundary, lane.journal)
    assert not c.fixture.writes


@pytest.mark.parametrize("change,reason", [("catalog_sha256", "database_catalog"),
    ("retained_acl_sha256", "retained_acl"), ("protected_liabilities", "protected_liability_changed")])
def test_changed_catalog_acl_or_obligations_stop_before_patch(application, change, reason):
    c = application
    setattr(c.observed, change, (("foreign", "f" * 64, 1),) if change == "protected_liabilities" else "f" * 64)
    with pytest.raises(app.rolling.RollingDenied, match=reason):
        run(c, "stage")
    assert not c.fixture.writes


def test_changed_qualified_prior_cannot_stage(application):
    c = application
    c.boundary.prior_observer.descriptor = replace(c.plan.prior_contract, app_sha256="f" * 64)
    with pytest.raises(app.rolling.RollingDenied, match="qualified_source_changed"):
        run(c, "stage")
    assert not c.fixture.writes


def test_changed_financial_state_after_staging_never_promotes(application):
    c = application
    run(c, "stage")
    writes = list(c.fixture.writes)
    c.observed.protected_liabilities = (("accepted-work-changed", "e" * 64, 1),)
    with pytest.raises(app.rolling.RollingDenied, match="protected_liability_changed"):
        run(c, "advance")
    assert c.fixture.writes == writes


def test_stage_then_workers_then_api_preserves_configuration_and_rollback(application):
    c = application
    originals = copy.deepcopy(c.fixture.states)
    assert run(c, "stage")["schema"] == app.plans.SCHEMA
    assert len(c.fixture.writes) == 3
    for service, body, mask in c.fixture.writes:
        assert mask == "template,traffic" and body["traffic"] == list(c.plan.pin(service).traffic) + [
            {"type": "TRAFFIC_TARGET_ALLOCATION_TYPE_REVISION", "revision": c.plan.revision(service), "tag": c.plan.tag}]
        before, after = app.rolling.environment(originals[service]["template"]), app.rolling.environment(body["template"])
        assert all(after[k] == v for k, v in before.items() if k != "APP_RELEASE")
        # Stock staging makes absent baked-off flags explicit; it cannot enable
        # an action or alter any other retained configuration.
        added = set(after) - set(before)
        assert added <= {*c.plan.disabled_flags(), "PGSSLROOTCERT"}
        assert all(after[k] == {"name": k, "value": "false"} for k in added - {"PGSSLROOTCERT"})
        assert after["PGSSLROOTCERT"] == {"name": "PGSSLROOTCERT", "value": app.rolling.release.SAFE_ENV["PGSSLROOTCERT"]}
    outcome = run(c, "advance")
    assert outcome["rolling_release_complete"] and outcome["migration_authorized"] is False
    assert outcome["payment_activation_authorized"] is False
    assert [service for service, _, mask in c.fixture.writes if mask == "traffic"] == [
        *app.rolling.release.SERVICES[1:], app.rolling.release.SERVICES[0]]
    writes = list(c.fixture.writes)
    assert run(c, "reconcile")["native_mutations"] == 0 and c.fixture.writes == writes
    assert run(c, "rollback")["schema"] == app.plans.SCHEMA
    assert all(any(row.get("revision") == pin.prior_revision and row.get("percent") == 100
                   for row in app.rolling.pinned_traffic(pin.service, c.boundary.service(pin.service)))
               for pin in c.plan.services)


def test_partial_stage_ack_reconciliation_does_not_resend(application):
    c = application
    c.fixture.pause = True
    assert run(c, "stage")["status"] == "same_native_operation_pending"
    assert run(c, "advance")["status"] == "same_native_operation_pending"
    assert len(c.fixture.writes) == 1
    for operation in c.fixture.operations.values():
        operation["done"] = True
    assert run(c, "reconcile")["native_mutations"] == 0
    assert len(c.fixture.writes) == 1


def test_unknown_ack_never_replays_write(application):
    c = application
    c.fixture.lose_ack = True
    with pytest.raises(RuntimeError):
        run(c, "stage")
    writes = list(c.fixture.writes)
    run(c, "reconcile")
    assert c.fixture.writes == writes and len(writes) == 1
    assert not c.journal.read(f"stage.{c.plan.services[0].service}.ack")


def test_raw_or_wrong_schema_catalog_handle_refused_before_sql():
    with pytest.raises(app.rolling.RollingDenied, match="catalog_session_unverified"):
        app.read_catalog27(SimpleNamespace())


def test_help_is_inert_and_exposes_no_migration_or_activation_mode(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["schema27_app_successor.py", "--help"])
    monkeypatch.setattr(app, "NativeApp27Boundary", lambda *_: pytest.fail("help constructed native boundary"))
    with pytest.raises(SystemExit) as done:
        app.main()
    assert done.value.code == 0
    help_text = capsys.readouterr().out
    assert "{capture,stage,advance,observe,reconcile,rollback}" in help_text
    assert "--bootstrap" not in help_text and "--activate" not in help_text
