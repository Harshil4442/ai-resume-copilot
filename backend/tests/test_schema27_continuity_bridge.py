"""Exercise the continuity dispatcher with real local journals and owned Cloud Run fixtures."""
from __future__ import annotations

import copy
import sys
from dataclasses import replace
from types import ModuleType, SimpleNamespace

import pytest
from infra.gcp import schema27_continuity_bridge as native
from test_schema26_app_successor import context as context
from test_schema27_continuity_bridge_plan import current as current


@pytest.fixture
def lane(context, current, tmp_path, monkeypatch):
    plan, templates = current
    fixture = context.fixture
    for pin in plan.services:
        fixture.states[pin.service]["template"] = templates[pin.service]["template"]
        revision = fixture.revisions[(pin.service, pin.prior_revision)]
        revision["containers"] = copy.deepcopy(templates[pin.service]["template"]["containers"])
    boundary = object.__new__(native.NativeContinuityBoundary)
    for name in ("service", "revision", "iam", "patch", "operation", "health", "_identity", "_reference",
                 "observer"):
        setattr(boundary, name, getattr(context.boundary, name))
    monkeypatch.setattr(native.successor.NativeSuccessorBoundary, "_resources",
                        lambda _self, release: context.boundary._resources(release))
    coordinator = native.historical.coordinator
    secret, version = native.scope.COORDINATOR_REFERENCE.split(":")
    template = {"serviceAccount": coordinator.ACCOUNT, "containers": [{
        "image": plan.image, "env": [
            {"name": "APP_RELEASE", "value": plan.prior_source.release},
            {"name": "SERVICE_ROLE", "value": "resume-upload-coordinator"},
            {"name": "DATABASE_URL", "valueSource": {"secretKeyRef": {"secret": secret, "version": version}}},
        ],
    }]}
    current = coordinator.SERVICE + "-current"
    tagged = coordinator.SERVICE + "-tagged"
    service = {
        "name": coordinator.RESOURCE, "generation": "2", "observedGeneration": "2", "etag": "owned",
        "terminalCondition": {"state": "CONDITION_SUCCEEDED"}, "template": template,
        "latestReadyRevision": current, "latestCreatedRevision": current,
        "trafficStatuses": [{"revision": current, "percent": 100}, {"revision": tagged, "tag": "pilot"}],
    }
    revisions = {name: copy.deepcopy(template) | {
        "name": f"{coordinator.RESOURCE}/revisions/{name}",
        "conditions": [{"type": "Ready", "state": "CONDITION_SUCCEEDED"}],
    } for name in (current, tagged)}
    coordinator_reads = []
    reader = SimpleNamespace(
        service=lambda _name: copy.deepcopy(service),
        revision=lambda _service, name: coordinator_reads.append(name) or copy.deepcopy(revisions[name]),
        stock=SimpleNamespace(session=SimpleNamespace(close=lambda: None)),
    )
    monkeypatch.setattr(coordinator, "NativeCoordinatorRun", lambda: reader)
    pins = tuple(boundary._pin(service, plan.prior_source) for service in native.rolling.release.SERVICES)
    plan = replace(plan, services=pins, reachable_revisions=boundary._reachable(pins, plan.prior_source))
    fixture.plan = plan
    source_reads = []
    boundary._verify_sources = lambda *pins: source_reads.append(pins)
    database = SimpleNamespace(catalog_sha256=plan.financial_sha256)

    def observe(custody, journal, *, expected_liabilities):
        native.scope.verify_journal(custody, journal)
        assert expected_liabilities == plan.protected_liabilities
        return database

    catalog_name = "infra.gcp.schema27_continuity_catalog"
    module = ModuleType(catalog_name)
    module.observe_schema26 = observe
    monkeypatch.setitem(sys.modules, catalog_name, module)
    import infra.gcp as gcp
    monkeypatch.setattr(gcp, "schema27_continuity_catalog", module, raising=False)
    monkeypatch.setattr(native.runtime, "observe_runtime", lambda *a: 49154)
    folder = tmp_path / "continuity"
    folder.mkdir(mode=0o700)
    return SimpleNamespace(plan=plan, boundary=boundary, fixture=fixture, database=database,
                           source_reads=source_reads, journal=native._bind_new(plan, folder),
                           coordinator_reads=coordinator_reads, coordinator_revisions=revisions, tagged=tagged)


def run(lane, mode):
    return native.execute(mode, lane.plan, lane.boundary, lane.journal)


def test_stage_preserves_prior_and_advance_uses_owned_promotions(lane):
    assert run(lane, "observe")["native_mutations"] == 0
    outcome = run(lane, "stage")
    assert outcome == {"status": "continuity_bridge_staged", "rolling_release_complete": False,
                       "schema": native.bridge.DB_SCHEMA, "replay_authorized": False}
    assert len(lane.fixture.writes) == 3
    assert lane.source_reads
    assert all(pins == (
        lane.plan.controller_release, lane.plan.controller_source_sha256,
        lane.plan.controller_ci_run_id, lane.plan.source, lane.plan.prior_source,
    ) for pins in lane.source_reads)
    assert all(mask == "template,traffic" for _, _, mask in lane.fixture.writes)
    assert all(any(row["revision"] == pin.prior_revision and row["percent"] == 100
                   for row in native.rolling.pinned_traffic(pin.service, lane.boundary.service(pin.service)))
               for pin in lane.plan.services)
    with pytest.raises(native.rolling.RollingDenied, match="promotion_receipt_missing"):
        native.promoted_receipts(lane.plan, lane.journal)
    result = run(lane, "advance")
    assert result["rolling_release_complete"] is True and result["schema"] == native.bridge.DB_SCHEMA
    assert [name for name, _, mask in lane.fixture.writes if mask == "traffic"] == [
        *native.rolling.release.SERVICES[1:], native.rolling.release.SERVICES[0],
    ]
    assert native.rolling.HASH.fullmatch(native.promoted_receipts(lane.plan, lane.journal))
    service = lane.plan.services[0].service
    receipt = f"promote.{service}.ack"
    ack = lane.journal.read(receipt)
    path = lane.journal.directory / (receipt + ".json")
    path.write_bytes(native.rolling.canonical(ack | {"request_sha256": "e" * 64}))
    with pytest.raises(native.rolling.RollingDenied, match="promotion_ack_lineage"):
        native.promoted_receipts(lane.plan, lane.journal)


def test_catalog_drift_and_foreign_journal_refuse_before_patch(lane, context):
    with pytest.raises(native.rolling.RollingDenied):
        native.execute("advance", lane.plan, lane.boundary, context.journal)
    lane.database.catalog_sha256 = "f" * 64
    with pytest.raises(native.rolling.RollingDenied, match="database_catalog"):
        run(lane, "advance")
    assert not lane.fixture.writes


def test_stale_coordinator_tag_refuses_before_staging(lane):
    revision = lane.coordinator_revisions[lane.tagged]
    env = native.rolling.environment(revision)
    env["DATABASE_URL"]["valueSource"]["secretKeyRef"]["version"] = "1"
    with pytest.raises(native.rolling.RollingDenied, match="coordinator_database_reference"):
        run(lane, "stage")
    assert lane.tagged in lane.coordinator_reads
    assert not lane.fixture.writes


def test_unverified_coordinator_secret_refuses_before_staging(lane):
    original = lane.boundary._reference
    secret, version = native.scope.COORDINATOR_REFERENCE.split(":")
    references = []

    def describe(name, selector):
        references.append((name, selector))
        return "1" if (name, selector) == (secret, version) else original(name, selector)

    lane.boundary._reference = describe
    with pytest.raises(native.rolling.RollingDenied, match="coordinator_secret_version"):
        run(lane, "stage")
    assert (secret, version) in references
    assert not lane.fixture.writes


def test_acknowledged_pending_operation_is_observed_with_no_resend(lane):
    lane.fixture.pause = True
    assert run(lane, "stage")["status"] == "same_native_operation_pending"
    assert run(lane, "advance")["status"] == "same_native_operation_pending"
    assert len(lane.fixture.writes) == 1
    for operation in lane.fixture.operations.values():
        operation["done"] = True
    assert run(lane, "reconcile")["native_mutations"] == 0
    assert len(lane.fixture.writes) == 1
