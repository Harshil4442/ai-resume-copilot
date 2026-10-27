"""Inert coordinator-native shapes; no GCP calls or fabricated deployment proof."""

import copy
import importlib
import json
import sys
from dataclasses import asdict
from pathlib import Path

import pytest
from backend.tests.test_rolling_release import FixtureBoundary

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "infra/gcp"))
pause = importlib.import_module("schema25_coordinator_pause")
reviewed = importlib.import_module("unified_credit_schema25_release")


class Coordinator:
    def __init__(self, flag="true"):
        source = FixtureBoundary()
        self.state = source.service(source.plan.services[0].service)
        self.state["name"] = pause.RESOURCE
        self.state["template"]["serviceAccount"] = pause.ACCOUNT
        env = pause.rolling.environment(self.state["template"])
        env["SERVICE_ROLE"]["value"] = "resume-upload-coordinator"
        if flag is not None:
            env[pause.FLAG] = {"name": pause.FLAG, "value": flag}
        env["RESUME_UPLOAD_COORDINATOR_SCAN_ENABLED"] = {"name": "RESUME_UPLOAD_COORDINATOR_SCAN_ENABLED", "value": "true"}
        env["RESUME_DIRECT_UPLOAD_ENABLED"] = {"name": "RESUME_DIRECT_UPLOAD_ENABLED", "value": "true"}
        env["INERT_PRIVATE_VALUE"] = {"name": "INERT_PRIVATE_VALUE", "value": "synthetic-sensitive"}
        self.state["template"]["containers"][0]["env"] = list(env.values())
        self.original = pause.SERVICE + "-original"
        name = pause.RESOURCE + "/revisions/" + self.original
        self.state["latestCreatedRevision"] = self.state["latestReadyRevision"] = name
        self.state["traffic"] = self.state["trafficStatuses"] = [{
            "type": "TRAFFIC_TARGET_ALLOCATION_TYPE_REVISION", "revision": self.original, "percent": 100,
        }]
        self.revisions = {self.original: {**copy.deepcopy(self.state["template"]), "name": name,
            "conditions": [{"type": "Ready", "state": "CONDITION_SUCCEEDED"}]}}
        self.writes, self.operations = [], {}
        self.lose_ack, self.pending = False, False

    def service(self, service):
        assert service == pause.SERVICE
        return copy.deepcopy(self.state)

    def revision(self, service, name):
        assert service == pause.SERVICE
        return copy.deepcopy(self.revisions.get(name))

    def patch(self, service, body, mask):
        assert service == pause.SERVICE and mask == "template,traffic"
        assert body["etag"] == self.state["etag"]
        self.writes.append(copy.deepcopy(body))
        self.state["template"] = copy.deepcopy(body["template"])
        self.state["traffic"] = self.state["trafficStatuses"] = copy.deepcopy(body["traffic"])
        name = body["template"]["revision"]
        resource = pause.RESOURCE + "/revisions/" + name
        self.state["latestCreatedRevision"] = self.state["latestReadyRevision"] = resource
        self.state["generation"] = self.state["observedGeneration"] = str(int(self.state["generation"]) + 1)
        self.state["etag"] = "etag-" + self.state["generation"]
        self.revisions[name] = {**copy.deepcopy(body["template"]), "name": resource,
            "conditions": [{"type": "Ready", "state": "CONDITION_SUCCEEDED"}]}
        operation = pause.rolling.PARENT + "/operations/coordinator-" + str(len(self.writes))
        self.operations[operation] = {"name": operation, "done": not self.pending}
        if self.lose_ack:
            self.lose_ack = False
            raise RuntimeError("inert sensitive lost provider ACK")
        return {"name": operation}

    def operation(self, name):
        return copy.deepcopy(self.operations[name])


def context(tmp_path, flag="true"):
    reader = Coordinator(flag)
    plan = pause.capture(reader, "a" * 32)
    tmp_path.chmod(0o700)
    return reader, plan, pause.rolling.Journal(tmp_path, plan)


def test_active_cleanup_is_refused_even_when_scan_is_disabled():
    reader = Coordinator()
    for row in [reader.state["template"], reader.revisions[reader.original]]:
        env = pause.rolling.environment(row)
        env["RESUME_UPLOAD_COORDINATOR_SCAN_ENABLED"]["value"] = "false"
        env["RESUME_DIRECT_UPLOAD_ENABLED"]["value"] = "false"
    with pytest.raises(pause.rolling.RollingDenied, match="admission_not_closed"):
        pause.observe_closed(reader)
    assert reader.writes == []


@pytest.mark.parametrize("flag", ["true", "false", None])
def test_pause_and_restore_preserve_source_other_config_original_flag_and_traffic(tmp_path, flag):
    reader, plan, journal = context(tmp_path, flag)
    original = copy.deepcopy(reader.state)
    assert pause.load_plan(pause.rolling.canonical(asdict(plan))) == plan
    result = pause.action(plan, reader, journal, "pause")
    assert result["status"] == "coordinator_pause_observed"
    assert result["inflight_invocations_proven_drained"] is False
    pause.observe_closed(reader)
    paused_env = pause.rolling.environment(reader.state["template"])
    assert paused_env[pause.FLAG]["value"] == "false"
    assert paused_env["RESUME_UPLOAD_COORDINATOR_SCAN_ENABLED"]["value"] == "true"
    assert paused_env["RESUME_DIRECT_UPLOAD_ENABLED"]["value"] == "true"
    assert pause.action(plan, reader, journal, "restore")["status"] == "coordinator_restore_observed"
    assert len(reader.writes) == 2
    assert pause.template_hash(reader.state["template"]) == pause.template_hash(original["template"])
    assert pause.rolling.pinned_traffic(pause.SERVICE, reader.state) == list(plan.original_traffic)
    assert reader.state["template"]["containers"][0]["image"] == plan.image
    assert reader.original in reader.revisions
    for path in journal.directory.glob("*.json"):
        assert "synthetic-sensitive" not in path.read_text()


def test_lost_pause_ack_requires_reconcile_without_replaying_patch(tmp_path):
    reader, plan, journal = context(tmp_path)
    reader.lose_ack = True
    with pytest.raises(RuntimeError, match="lost provider ACK"):
        pause.action(plan, reader, journal, "pause")
    with pytest.raises(pause.rolling.RollingDenied, match="unknown_write_reconcile_only"):
        pause.action(plan, reader, journal, "pause")
    assert len(reader.writes) == 1
    assert pause.action(plan, reader, journal, "pause", reconcile=True)["status"] == "coordinator_pause_observed"
    assert journal.read("coordinator.pause.checked")["reconciled_without_ack"] is True
    assert len(reader.writes) == 1


def test_pending_ack_polls_original_operation_only(tmp_path):
    reader, plan, journal = context(tmp_path)
    reader.pending = True
    assert pause.action(plan, reader, journal, "pause")["status"] == "coordinator_operation_pending"
    assert pause.action(plan, reader, journal, "pause", reconcile=True)["status"] == "coordinator_operation_pending"
    assert len(reader.writes) == 1
    next(iter(reader.operations.values()))["done"] = True
    assert pause.action(plan, reader, journal, "pause", reconcile=True)["status"] == "coordinator_pause_observed"


def test_lost_restore_ack_recovers_prior_flag_and_traffic_without_a_third_write(tmp_path):
    reader, plan, journal = context(tmp_path)
    pause.action(plan, reader, journal, "pause")
    reader.lose_ack = True
    with pytest.raises(RuntimeError, match="lost provider ACK"):
        pause.action(plan, reader, journal, "restore")
    with pytest.raises(pause.rolling.RollingDenied, match="unknown_write_reconcile_only"):
        pause.action(plan, reader, journal, "restore")
    assert pause.action(plan, reader, journal, "restore", reconcile=True)["status"] == "coordinator_restore_observed"
    assert len(reader.writes) == 2
    assert pause.rolling.environment(reader.state["template"])[pause.FLAG] == plan.original_flag
    assert pause.rolling.pinned_traffic(pause.SERVICE, reader.state) == list(plan.original_traffic)


def test_concurrent_change_after_pause_refuses_restore_without_overwriting(tmp_path):
    reader, plan, journal = context(tmp_path)
    pause.action(plan, reader, journal, "pause")
    reader.state["generation"] = reader.state["observedGeneration"] = str(int(reader.state["generation"]) + 1)
    reader.state["template"]["maxInstanceRequestConcurrency"] = 12
    with pytest.raises(pause.rolling.RollingDenied, match="readback_changed"):
        pause.action(plan, reader, journal, "restore")
    assert len(reader.writes) == 1
    assert journal.read("coordinator.restore.started") is None


def test_capture_refuses_a_reachable_revision_with_another_source():
    reader = Coordinator()
    pause.rolling.environment(reader.revisions[reader.original])["APP_RELEASE"]["value"] = "f" * 40
    with pytest.raises(pause.rolling.RollingDenied, match="reachable_source_changed"):
        pause.capture(reader, "a" * 32)
    assert reader.writes == []


@pytest.mark.parametrize("change", ["generation", "source", "image", "configuration"])
def test_stale_capture_or_concurrent_change_refuses_without_write(tmp_path, change):
    reader, plan, journal = context(tmp_path)
    if change == "generation":
        reader.state["generation"] = reader.state["observedGeneration"] = "43"
    elif change == "source":
        pause.rolling.environment(reader.state["template"])["APP_RELEASE"]["value"] = "c" * 40
    elif change == "image":
        reader.state["template"]["containers"][0]["image"] = plan.image[:-64] + "c" * 64
    else:
        reader.state["template"]["maxInstanceRequestConcurrency"] = 9
    with pytest.raises(pause.rolling.RollingDenied, match="preimage_changed"):
        pause.action(plan, reader, journal, "pause")
    assert reader.writes == []


def test_open_tagged_revision_is_never_mistaken_for_closed_coordinator(tmp_path):
    reader, plan, journal = context(tmp_path)
    pause.action(plan, reader, journal, "pause")
    reader.state["trafficStatuses"].append({"type": "TRAFFIC_TARGET_ALLOCATION_TYPE_REVISION",
        "revision": reader.original, "tag": "old-open"})
    with pytest.raises(pause.rolling.RollingDenied, match="admission_not_closed"):
        pause.observe_closed(reader)


def test_pause_closes_tag_routes_and_restore_preserves_original_targets(tmp_path):
    reader = Coordinator()
    tagged_name = pause.SERVICE + "-tagged"
    reader.revisions[tagged_name] = copy.deepcopy(reader.revisions[reader.original])
    reader.revisions[tagged_name]["name"] = pause.RESOURCE + "/revisions/" + tagged_name
    reader.state["trafficStatuses"].append({"type": "TRAFFIC_TARGET_ALLOCATION_TYPE_REVISION",
        "revision": tagged_name, "tag": "review"})
    plan = pause.capture(reader, "a" * 32)
    tmp_path.chmod(0o700)
    journal = pause.rolling.Journal(tmp_path, plan)
    pause.action(plan, reader, journal, "pause")
    assert {row["revision"] for row in reader.state["trafficStatuses"]} == {plan.revision_name("pause")}
    assert any(row.get("tag") == "review" for row in reader.state["trafficStatuses"])
    pause.observe_closed(reader)
    pause.action(plan, reader, journal, "restore")
    assert pause.rolling.pinned_traffic(pause.SERVICE, reader.state) == list(plan.original_traffic)


def test_restore_refuses_changed_prior_source_and_reconciliation_never_starts_write(tmp_path):
    reader, plan, journal = context(tmp_path)
    with pytest.raises(pause.rolling.RollingDenied, match="reconcile_never_starts_write"):
        pause.action(plan, reader, journal, "pause", reconcile=True)
    pause.action(plan, reader, journal, "pause")
    reader.revisions[reader.original]["containers"][0]["image"] = plan.image[:-64] + "d" * 64
    with pytest.raises(pause.rolling.RollingDenied, match="prior_revision_changed"):
        pause.action(plan, reader, journal, "restore")
    assert len(reader.writes) == 1


def test_native_transport_scope_keeps_three_service_plan_unchanged():
    assert pause.native.NativeRun.SERVICES == pause.rolling.release.SERVICES
    assert pause.SERVICE not in pause.native.NativeRun.SERVICES
    assert pause.NativeCoordinatorRun.SERVICES == (pause.SERVICE,)
    assert pause.NativeCoordinatorRun.patch is pause.native.NativeRun.patch


def test_bounded_cli_capture_pause_reconcile_restore_use_exact_private_plan(tmp_path, monkeypatch, capsys):
    tmp_path.chmod(0o700)
    plan_path = tmp_path / "coordinator.json"
    journal_path = tmp_path / "journal"
    journal_path.mkdir(mode=0o700)
    reader = Coordinator()
    monkeypatch.setattr(pause, "NativeCoordinatorRun", lambda: reader)

    def invoke(mode, extra=()):
        monkeypatch.setattr(sys, "argv", ["schema25", mode, "--plan", str(plan_path), *extra])
        reviewed.main()
        return json.loads(capsys.readouterr().out)

    captured = invoke("coordinator-capture", ["--nonce", "a" * 32])
    assert captured["provider_mutations"] == 0 and reader.writes == []
    private_args = ["--plan-sha256", captured["plan_sha256"], "--journal", str(journal_path)]
    assert invoke("coordinator-pause", private_args)["status"] == "coordinator_pause_observed"
    assert invoke("coordinator-reconcile", private_args)["status"] == "coordinator_pause_observed"
    assert invoke("coordinator-restore", private_args)["status"] == "coordinator_restore_observed"
    assert invoke("coordinator-reconcile", private_args)["status"] == "coordinator_restore_observed"
    assert len(reader.writes) == 2
    assert reviewed.failure_response(pause.rolling.RollingDenied(
        "schema25_coordinator_admission_not_closed"))["code"] == "schema25_coordinator_admission_not_closed"


def test_global_operator_observation_checks_coordinator_before_database_or_release_checks(monkeypatch):
    fixture = FixtureBoundary()
    plan = reviewed.load_plan(pause.rolling.canonical({**asdict(fixture.plan),
        "schema_contract": reviewed.contract.CONTRACT, "database_schema": reviewed.contract.SCHEMA,
        "prior_sources": []}))
    boundary = object.__new__(reviewed.NativeSchema25Boundary)
    boundary.schema = reviewed.contract.SCHEMA
    calls = []

    def active():
        calls.append("coordinator")
        raise pause.rolling.RollingDenied("schema25_coordinator_admission_not_closed")

    boundary.verify_coordinator_closed = active
    monkeypatch.setattr(pause.native.NativeRollingBoundary, "verify_global", lambda *args: calls.append("database"))
    with pytest.raises(pause.rolling.RollingDenied, match="admission_not_closed"):
        boundary.verify_global(plan)
    assert calls == ["coordinator"]
