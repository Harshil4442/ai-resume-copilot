"""Owned negative-only closure; no provider, model or database call."""

from __future__ import annotations

import copy
import hashlib
from dataclasses import asdict
from types import SimpleNamespace
from uuid import UUID

import pytest
from infra.gcp import schema27_native_admission_closure as op


class Fixture:
    def __init__(self):
        self.states, self.revisions, self.operations, self.writes, self.health_calls = (
            {},
            {},
            {},
            [],
            [],
        )
        self.pending = self.lose_ack = self.fail_health = False
        for index, service in enumerate(op.READS):
            revision = service + "-synthetic-prior"
            env = [
                {"name": "APP_RELEASE", "value": "7" * 40},
                {"name": "OPTIONAL_AI_GENERATION_ENABLED", "value": "false"},
                {
                    "name": "RESUME_DIRECT_UPLOAD_ENABLED",
                    "value": "false" if service == op.ANALYSIS else "true",
                },
                {"name": "RESUME_UPLOAD_COORDINATOR_SCAN_ENABLED", "value": "true"},
                {"name": "PRIVATE_SECRET", "value": "synthetic-sensitive-literal"},
            ]
            template = {
                "revision": revision,
                "serviceAccount": op.owned.NEW_API_EMAIL
                if service == op.API
                else op.restore.identities.readback.COORDINATOR_ACCOUNT,
                "containers": [{"image": "example.invalid/app@sha256:" + "a" * 64, "env": env}],
                "timeout": "300s",
                "scaling": {"maxInstanceCount": 1},
            }
            resource = op.rolling.PARENT + "/services/" + service
            self.states[service] = {
                "name": resource,
                "uid": op.restore.identities.readback.application.UIDS[service],
                "generation": 30 + index,
                "observedGeneration": 30 + index,
                "etag": "etag-" + service,
                "template": template,
                "terminalCondition": {"state": "CONDITION_SUCCEEDED"},
                "latestCreatedRevision": resource + "/revisions/" + revision,
                "latestReadyRevision": resource + "/revisions/" + revision,
                "trafficStatuses": [
                    {
                        "type": "TRAFFIC_TARGET_ALLOCATION_TYPE_REVISION",
                        "revision": revision,
                        "percent": 100,
                        "tag": "existing",
                    }
                ],
                "ingress": "INGRESS_TRAFFIC_INTERNAL_LOAD_BALANCER",
                "uri": "https://" + service + ".run.app",
            }
            self.states[service]["traffic"] = copy.deepcopy(self.states[service]["trafficStatuses"])
            self.revisions[(service, revision)] = self.make_revision(service, template, index + 1)

    def make_revision(self, service, template, identifier):
        return {k: copy.deepcopy(v) for k, v in template.items() if k != "revision"} | {
            "name": op.rolling.PARENT
            + "/services/"
            + service
            + "/revisions/"
            + template["revision"],
            "service": service,
            "uid": str(UUID(int=identifier)),
            "conditions": [{"type": "Ready", "state": "CONDITION_SUCCEEDED"}],
            "containerStatuses": [{"name": "app", "imageDigest": "sha256:" + "a" * 64}],
        }

    def service(self, name):
        return copy.deepcopy(self.states[name])

    def revision(self, service, name):
        return copy.deepcopy(self.revisions.get((service, name)))

    def patch(self, service, body, mask):
        assert service in op.WRITES
        before = self.states[service]
        assert body["etag"] == before["etag"]
        self.writes.append((service, copy.deepcopy(body), mask))
        value = copy.deepcopy(before)
        value.update(
            generation=before["generation"] + 1,
            observedGeneration=before["generation"] + 1,
            etag="etag-action-" + str(len(self.writes)),
            trafficStatuses=copy.deepcopy(body["traffic"]),
        )
        if "template" in body:
            value["template"] = copy.deepcopy(body["template"])
            name = body["template"]["revision"]
            self.revisions[(service, name)] = self.make_revision(
                service, body["template"], 100 + len(self.writes)
            )
            value["latestCreatedRevision"] = value["latestReadyRevision"] = (
                op.rolling.PARENT + "/services/" + service + "/revisions/" + name
            )
        value["traffic"] = copy.deepcopy(body["traffic"])
        self.states[service] = value
        operation = op.rolling.PARENT + "/operations/synthetic-" + str(len(self.writes))
        self.operations[operation] = {"name": operation, "done": not self.pending}
        if self.lose_ack:
            raise RuntimeError("synthetic_lost_ack")
        return {"name": operation}

    def operation(self, name):
        return copy.deepcopy(self.operations[name])

    def health(self, _plan, pin):
        self.health_calls.append((pin.service, pin.revision))
        if self.fail_health:
            raise RuntimeError("synthetic_health_failure")
        env = op.rolling.environment(self.revisions[(pin.service, pin.revision)])
        assert all(env[key] == {"name": key, "value": "false"} for key in op.FLAGS[pin.service])


@pytest.fixture
def prepared(tmp_path, monkeypatch):
    fixture = Fixture()
    boundary = object.__new__(op.NativeBoundary)
    for name in ("service", "revision", "patch", "operation", "health"):
        setattr(boundary, name, getattr(fixture, name))
    row = fixture.states[op.COORDINATOR]
    revisions = {
        r["revision"]: fixture.revisions[(op.COORDINATOR, r["revision"])]
        for r in row["trafficStatuses"]
    }
    baseline = op.restore.identities.readback.project_service(op.COORDINATOR, row, revisions)
    base = SimpleNamespace(
        application=SimpleNamespace(
            identities=SimpleNamespace(
                promotion=SimpleNamespace(before={"services": {op.COORDINATOR: baseline}})
            )
        )
    )
    complete = {
        "services": {s: op.restore._projection(fixture.states[s], s) for s in (op.API, op.ANALYSIS)}
    }
    monkeypatch.setattr(op, "parent_read", lambda *_args, **_kwargs: (base, complete))
    source = {"release": "9" * 40, "tree": "8" * 40, "files": {"synthetic.py": "7" * 64}}
    monkeypatch.setattr(op, "source", lambda: copy.deepcopy(source))
    monkeypatch.setattr(
        op, "historical_source", lambda actual: op.need(actual == source, "source_bytes")
    )
    parent = {
        "root": str(tmp_path),
        "plan": str(tmp_path / "old.json"),
        "plan_sha256": "1" * 64,
        "journal": str(tmp_path / "parent"),
        "complete_sha256": "2" * 64,
    }
    directory = tmp_path / "close"
    directory.mkdir(mode=0o700)
    plan = op.capture(parent, boundary, "f" * 32, directory)
    return SimpleNamespace(
        fixture=fixture,
        boundary=boundary,
        plan=plan,
        journal=op.owned.Journal(directory, plan, create=True),
    )


def test_closure_preserves_saved_reads_and_all_unrelated_configuration(prepared):
    c = prepared
    before, revisions = copy.deepcopy(c.fixture.states), copy.deepcopy(c.fixture.revisions)
    result = op.execute("close", c.plan, c.boundary, c.journal)
    assert result["new_admission_closed"] is True and result["native_mutations"] == 4
    assert (
        result["document_processing_enabled"] is False
        and result["payment_activation_authorized"] is False
    )
    assert [s for s, _, _ in c.fixture.writes] == [op.API, op.API, op.COORDINATOR, op.COORDINATOR]
    assert [mask for _, _, mask in c.fixture.writes] == ["template,traffic", "traffic"] * 2
    assert len(c.fixture.health_calls) == 4
    for service in op.WRITES:
        current = c.fixture.states[service]
        changed, initial = (
            op.rolling.environment(v["template"]) for v in (current, before[service])
        )
        assert {k: v for k, v in changed.items() if k not in op.FLAGS[service]} == {
            k: v for k, v in initial.items() if k not in op.FLAGS[service]
        }
        assert all(changed[k]["value"] == "false" for k in op.FLAGS[service])
        assert (
            current["template"]["serviceAccount"] == before[service]["template"]["serviceAccount"]
        )
        assert (
            current["template"]["containers"][0]["image"]
            == before[service]["template"]["containers"][0]["image"]
        )
        assert all(
            r["revision"] == next(p.revision for p in c.plan.services if p.service == service)
            for r in current["trafficStatuses"]
        )
    assert (
        op.rolling.environment(c.fixture.states[op.API]["template"])[
            "RESUME_DIRECT_UPLOAD_ENABLED"
        ]["value"]
        == "true"
    )
    assert c.fixture.states[op.ANALYSIS] == before[op.ANALYSIS]
    assert all(c.fixture.revisions[k] == v for k, v in revisions.items())
    assert all(
        "synthetic-sensitive-literal" not in p.read_text()
        for p in c.journal.directory.glob("*.json")
    )
    proof = op.ClosedAdmission(c.plan, c.journal)
    assert proof.parent_restore_plan_sha256 == "1" * 64
    assert set(proof.verify_current(c.boundary)) == set(op.WRITES)
    assert op.execute("close", c.plan, c.boundary, c.journal)["native_mutations"] == 0
    assert len(c.fixture.writes) == 4


def test_roundtrip_keeps_no_inline_credential(prepared):
    raw = op.rolling.canonical(asdict(prepared.plan))
    assert op.load(raw, hashlib.sha256(raw).hexdigest()) == prepared.plan
    assert b"synthetic-sensitive-literal" not in raw and b"PRIVATE_SECRET" not in raw


@pytest.mark.parametrize(
    "fault", ["uid", "etag", "generation", "static", "analysis", "alias", "image", "identity"]
)
def test_preimage_drift_refuses_before_any_write(prepared, fault):
    c = prepared
    row = c.fixture.states[op.API]
    if fault == "uid":
        row["uid"] = str(UUID(int=999))
    elif fault == "etag":
        row["etag"] = "foreign"
    elif fault == "generation":
        row["generation"] += 1
        row["observedGeneration"] += 1
    elif fault == "static":
        row["ingress"] = "INGRESS_TRAFFIC_ALL"
    elif fault == "analysis":
        c.fixture.states[op.ANALYSIS]["etag"] = "foreign"
    elif fault == "alias":
        row["trafficStatuses"].append({"revision": "unowned", "tag": "foreign"})
    elif fault == "image":
        row["template"]["containers"][0]["image"] = "foreign@sha256:" + "c" * 64
    else:
        row["template"]["serviceAccount"] = "foreign@invalid.test"
    with pytest.raises(op.rolling.RollingDenied):
        op.execute("close", c.plan, c.boundary, c.journal)
    assert not c.fixture.writes


@pytest.mark.parametrize("mode", ["close", "reconcile", "observe"])
def test_lost_ack_never_replays_or_adopts_native_state(prepared, mode):
    c = prepared
    c.fixture.lose_ack = True
    with pytest.raises(RuntimeError, match="synthetic_lost_ack"):
        op.execute("close", c.plan, c.boundary, c.journal)
    assert len(c.fixture.writes) == 1
    with pytest.raises(op.rolling.RollingDenied, match="missing_or_changed_native_ack"):
        op.execute(mode, c.plan, c.boundary, c.journal)
    assert len(c.fixture.writes) == 1 and c.journal.read("stage." + op.API + ".ack") is None


def test_pending_poll_and_reconciliation_do_not_start_route(prepared):
    c = prepared
    c.fixture.pending = True
    assert (
        op.execute("close", c.plan, c.boundary, c.journal)["status"]
        == "same_native_operation_pending"
    )
    assert op.execute("reconcile", c.plan, c.boundary, c.journal)["native_mutations"] == 0
    for value in c.fixture.operations.values():
        value["done"] = True
    op.execute("reconcile", c.plan, c.boundary, c.journal)
    assert len(c.fixture.writes) == 1 and c.journal.read("route." + op.API + ".started") is None


def test_failed_zero_traffic_health_keeps_old_default_allocation(prepared):
    c = prepared
    c.fixture.fail_health = True
    with pytest.raises(RuntimeError, match="synthetic_health_failure"):
        op.execute("close", c.plan, c.boundary, c.journal)
    assert len(c.fixture.writes) == 1
    assert all(
        r["revision"] == c.plan.services[0].before["created"]
        for r in c.fixture.states[op.API]["trafficStatuses"]
        if r.get("percent")
    )
    assert c.journal.read("route." + op.API + ".started") is None


def test_incomplete_proof_is_not_a_closed_admission(prepared):
    with pytest.raises(op.rolling.RollingDenied, match="closure_incomplete"):
        op.ClosedAdmission(prepared.plan, prepared.journal)


def test_native_writer_cannot_mutate_analysis():
    with pytest.raises(op.rolling.RollingDenied, match="write_scope"):
        object.__new__(op.NativeBoundary).patch(op.ANALYSIS, {}, "traffic")


@pytest.mark.parametrize(
    "field", ["payment_activation_authorized", "new_admission_closed", "required_flags"]
)
def test_read_only_consumer_rejects_tampered_scope(prepared, field):
    import json

    c = prepared
    op.execute("close", c.plan, c.boundary, c.journal)
    path = c.journal.directory / "complete.json"
    value = json.loads(path.read_text())
    value[field] = not value[field] if type(value[field]) is bool else {}
    path.write_text(json.dumps(value))
    with pytest.raises(op.rolling.RollingDenied):
        op.ClosedAdmission(c.plan, c.journal)


def test_operator_pause_refuses_new_writes_but_allows_acknowledged_readback(prepared):
    c = prepared
    marker = c.journal.directory / "operator.pause-requested"
    marker.touch(mode=0o600)
    with pytest.raises(op.rolling.RollingDenied, match="operator_pause_requested"):
        op.execute("close", c.plan, c.boundary, c.journal)
    assert not c.fixture.writes
    assert op.execute("observe", c.plan, c.boundary, c.journal)["native_mutations"] == 0
