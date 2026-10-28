"""Inert tests for the finite post-schema25 pilot reopening contract."""
from __future__ import annotations

import copy
import importlib
import sys
from dataclasses import asdict, replace
from pathlib import Path
from types import SimpleNamespace

import pytest
from backend.tests.test_rolling_release import FixtureBoundary

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "infra/gcp"))
pilot = importlib.import_module("schema25_pilot_reopen")


class Controls:
    def get(self, kind, name):
        return {"name": pilot.rolling.PARENT + "/" + kind + "/" + name,
                "state": "RUNNING" if kind == "queue" else "ENABLED"}


def context(tmp_path, monkeypatch, *, document_pins=None):
    run = FixtureBoundary()
    requested = ("EMPLOYER_DISCOVERY_ENABLED", "RESUME_DIRECT_UPLOAD_ENABLED")
    originals = {pilot.SERVICES[0]: "RESUME_DIRECT_UPLOAD_ENABLED",
                 pilot.SERVICES[2]: "EMPLOYER_DISCOVERY_ENABLED"}
    for service, flag in originals.items():
        prior = run.plan.pin(service).prior_revision
        for row in (run.states[service]["template"], run.revisions[(service, prior)]):
            row["containers"][0]["env"].append({"name": flag, "value": "true"})
    if document_pins is not None:
        for service in pilot.SERVICES[:2]:
            prior = run.plan.pin(service).prior_revision
            for row in (run.states[service]["template"], run.revisions[(service, prior)]):
                row["containers"][0]["env"].extend(
                    {"name": key, "value": value} for key, value in document_pins.items())
    before = pilot.maintenance.capture(run, Controls(), "b" * 32)
    for service in pilot.SERVICES:
        current = run.states[service]
        template = current["template"]
        template["containers"][0]["image"] = run.plan.image
        env = pilot.rolling.environment(template)
        env["APP_RELEASE"]["value"] = run.plan.release
        for flag in requested:
            env[flag] = {"name": flag, "value": "false"}
        template["containers"][0]["env"] = list(env.values())
        revision = run.plan.revision(service)
        template["revision"] = revision
        current["generation"] = current["observedGeneration"] = "43"
        current["etag"] = "etag-43"
        current["latestReadyRevision"] = current["latestCreatedRevision"] = (
            current["name"] + "/revisions/" + revision)
        current["traffic"] = current["trafficStatuses"] = [{
            "type": "TRAFFIC_TARGET_ALLOCATION_TYPE_REVISION", "revision": revision,
            "tag": "rolling-test", "percent": 100,
        }]
        run.revisions[(service, revision)] = {
            **copy.deepcopy(template), "name": current["latestReadyRevision"],
            "conditions": [{"type": "Ready", "state": "CONDITION_SUCCEEDED"}],
        }
    gates = []
    monkeypatch.setattr(pilot, "_restored", lambda *args: gates.append("restored"))
    monkeypatch.setattr(pilot, "_deployed", lambda *args: gates.append("deployed"))
    plan = pilot.capture(run.plan, None, before, None, before, None,
                         run, None, None, requested, "a" * 32)
    tmp_path.chmod(0o700)
    journal = pilot.rolling.Journal(tmp_path, plan)
    return run, before, plan, journal, gates


def test_capture_requires_prior_true_and_exact_supported_flags(tmp_path, monkeypatch):
    run, before, plan, journal, gates = context(tmp_path, monkeypatch)
    assert gates == ["restored", "deployed"]
    assert pilot.load(pilot.rolling.canonical(asdict(plan))) == plan
    assert [pin.reopen_flags for pin in plan.services] == [
        ("RESUME_DIRECT_UPLOAD_ENABLED",), (), ("EMPLOYER_DISCOVERY_ENABLED",)]
    assert not set(plan.requested_flags) & pilot.FORBIDDEN
    with pytest.raises(pilot.rolling.RollingDenied, match="requested_flags_refused"):
        pilot.capture(run.plan, None, before, None, before, None, run, None, None,
                      ("RAZORPAY_CHECKOUT_ENABLED",), "c" * 32)
    with pytest.raises(pilot.rolling.RollingDenied, match="requested_flag_was_not_running"):
        pilot.capture(run.plan, None, before, None, before, None, run, None, None,
                      ("BROWSER_ASSISTANCE_ENABLED",), "c" * 32)
    assert run.writes == [] and list(journal.directory.glob("*.json")) == []


def test_reopen_workers_before_api_preserves_image_secret_and_denied_flags(tmp_path, monkeypatch):
    run, before, plan, journal, _ = context(tmp_path, monkeypatch)
    health = []
    result = pilot.advance(plan, journal, run, lambda *args: health.append(args),
                           lambda first: None, before)
    assert result["status"] == "pilot_reopen_observed"
    assert [service for service, _, _ in run.writes] == [pilot.SERVICES[2], pilot.SERVICES[0]]
    assert len(health) == 2
    for service, body, mask in run.writes:
        assert mask == "template,traffic"
        assert body["template"]["containers"][0]["image"] == plan.image
        env = pilot.rolling.environment(body["template"])
        assert env["RAZORPAY_KEY_SECRET"]["value"] == "synthetic-sensitive-literal"
        assert all(env.get(flag, {"value": "false"})["value"] == "false"
                   for flag in pilot.FORBIDDEN if flag in env)
        assert body["traffic"][-1]["revision"] == plan.revision(service)
    assert all("synthetic-sensitive" not in path.read_text()
               for path in journal.directory.glob("*.json"))


@pytest.mark.parametrize("drift", ["image", "unrelated_env", "etag", "traffic"])
def test_preimage_drift_refuses_any_patch(tmp_path, monkeypatch, drift):
    run, before, plan, journal, _ = context(tmp_path, monkeypatch)
    current = run.states[pilot.SERVICES[2]]
    if drift == "image":
        current["template"]["containers"][0]["image"] += "-changed"
    elif drift == "unrelated_env":
        pilot.rolling.environment(current["template"])["RAZORPAY_KEY_SECRET"]["value"] = "changed"
    elif drift == "etag":
        current["etag"] = "changed"
    else:
        current["trafficStatuses"][0]["tag"] = "changed"
    with pytest.raises(pilot.rolling.RollingDenied, match="preimage_changed"):
        pilot.advance(plan, journal, run, lambda *args: None, lambda first: None, before)
    assert run.writes == []


def test_unknown_ack_never_replays_and_reconcile_starts_no_next_write(tmp_path, monkeypatch):
    run, before, plan, journal, _ = context(tmp_path, monkeypatch)
    run.lose_ack = True
    with pytest.raises(RuntimeError, match="synthetic-sensitive"):
        pilot.advance(plan, journal, run, lambda *args: None, lambda first: None, before)
    assert len(run.writes) == 1
    with pytest.raises(pilot.rolling.RollingDenied, match="unknown_write_reconcile_only"):
        pilot.advance(plan, journal, run, lambda *args: None, lambda first: None, before)
    assert pilot.advance(plan, journal, run, lambda *args: None, lambda first: None, before,
                         reconcile=True)["status"] == "pilot_phase_partial"
    assert len(run.writes) == 1
    assert pilot.advance(plan, journal, run, lambda *args: None,
                         lambda first: None, before)["status"] == "pilot_reopen_observed"
    assert len(run.writes) == 2


def test_restoration_or_document_gate_failure_prevents_reopen(tmp_path, monkeypatch):
    run, before, plan, journal, _ = context(tmp_path, monkeypatch)
    observed = []

    def gate(first):
        observed.append(first)
        raise pilot.rolling.RollingDenied("schema25_pilot_resource_changed")

    with pytest.raises(pilot.rolling.RollingDenied, match="resource_changed"):
        pilot.advance(plan, journal, run, lambda *args: None, gate, before)
    assert observed == [True] and run.writes == []


@pytest.mark.parametrize("reconcile", [False, True])
def test_tampered_plan_cannot_open_a_flag_absent_from_prior_revision(
        tmp_path, monkeypatch, reconcile):
    run, before, plan, _, _ = context(tmp_path, monkeypatch)
    pins = list(plan.services)
    pins[1] = replace(pins[1], reopen_flags=("EMPLOYER_DISCOVERY_ENABLED",))
    tampered = replace(plan, services=tuple(pins))
    tampered.validate()  # A valid shape and fresh journal hash are insufficient.
    journal = pilot.rolling.Journal(tmp_path, tampered)
    with pytest.raises(pilot.rolling.RollingDenied, match="reopen_flags_not_prior_true"):
        pilot.advance(tampered, journal, run, lambda *args: None,
                      lambda first: None, before, reconcile=reconcile)
    assert run.writes == []


def test_document_gate_accepts_exact_schema25_tagged_callers_and_refuses_drift(
        tmp_path, monkeypatch):
    worker_image = pilot.document_probe.REPOSITORY + "@sha256:" + "1" * 64
    doc_pins = {
        "DOCUMENT_WORKER_URL": pilot.document_probe.WORKER_URL,
        "DOCUMENT_WORKER_IMAGE_DIGEST": worker_image,
        "DOCUMENT_WORKER_POLICY_SHA256": "2" * 64,
        "DOCUMENT_WORKER_DOCX_RENDERER_VERSION": "synthetic-renderer",
        "DOCUMENT_WORKER_DOCX_FONT_MANIFEST_SHA256": "3" * 64,
    }
    run, before, plan, journal, _ = context(tmp_path, monkeypatch,
                                            document_pins=doc_pins)
    name = pilot.coordinator.SERVICE
    resource = pilot.document_probe.PARENT + "/services/" + name
    uid = "00000000-0000-4000-8000-000000000004"
    prior_name, restore_name = name + "-prior", name + "-restore"
    original = {"labels": {"hirewiz-source": plan.source_release},
                "serviceAccount": pilot.coordinator.ACCOUNT,
                "containers": [{"image": plan.image, "env": [
                    {"name": "APP_RELEASE", "value": plan.source_release},
                    *({"name": key, "value": value} for key, value in doc_pins.items())]}]}
    original_keys = tuple(sorted(original))
    original_revision = {**copy.deepcopy(original),
                         "name": resource + "/revisions/" + prior_name,
                         "service": resource, "uid": uid,
                         "conditions": [{"type": "Ready", "state": "CONDITION_SUCCEEDED"}]}
    restore_template = {**copy.deepcopy(original), "revision": restore_name}
    restore_revision = {**copy.deepcopy(restore_template),
                        "name": resource + "/revisions/" + restore_name,
                        "service": resource, "uid": uid,
                        "conditions": [{"type": "Ready", "state": "CONDITION_SUCCEEDED"}]}
    traffic = [{"type": "TRAFFIC_TARGET_ALLOCATION_TYPE_REVISION",
                "revision": prior_name, "tag": "coordinator-tag", "percent": 100}]
    coordinator_service = {
        "name": resource, "uid": uid, "etag": "coordinator-etag",
        "generation": "9", "observedGeneration": "9", "reconciling": False,
        "terminalCondition": {"state": "CONDITION_SUCCEEDED"},
        "latestReadyRevision": restore_revision["name"],
        "latestCreatedRevision": restore_revision["name"],
        "template": restore_template, "traffic": copy.deepcopy(traffic),
        "trafficStatuses": copy.deepcopy(traffic),
    }
    coordinator_plan = SimpleNamespace(
        uid=uid, image=plan.image, source_release=plan.source_release,
        prior_revision=prior_name,
        prior_revisions=((prior_name, pilot.rolling.sha(original_revision)),),
        template_keys=original_keys,
        template_sha256=pilot.coordinator.template_hash(original),
        original_traffic=tuple(traffic),
        static_sha256=pilot.coordinator.static_hash(coordinator_service),
        revision_name=lambda phase: restore_name,
    )

    class FakeClients:
        def __init__(self, *, read_only_release):
            assert read_only_release is True

        def service(self, path):
            service = path.rsplit("/", 1)[-1]
            if service == "hirewiz-document-inspector":
                return {"template": {"containers": [{"image": worker_image}]}}
            return (copy.deepcopy(coordinator_service) if service == name
                    else run.service(service))

        def revision(self, path):
            service, revision = path.split("/services/", 1)[1].split("/revisions/")
            if service == name:
                return copy.deepcopy(original_revision if revision == prior_name
                                     else restore_revision)
            row = run.revision(service, revision)
            row["uid"] = run.service(service)["uid"]
            row["service"] = pilot.document_probe.PARENT + "/services/" + service
            return row

        def close(self):
            pass

    monkeypatch.setattr(pilot.document_probe, "Clients", FakeClients)
    monkeypatch.setattr(pilot.document_probe, "verify_current_successor_for_release",
                        lambda image, worker: SimpleNamespace(
                            image=image, policy_sha256=doc_pins["DOCUMENT_WORKER_POLICY_SHA256"],
                            renderer_version=doc_pins["DOCUMENT_WORKER_DOCX_RENDERER_VERSION"],
                            font_manifest_sha256=doc_pins[
                                "DOCUMENT_WORKER_DOCX_FONT_MANIFEST_SHA256"]))
    pilot.verify_document_pins(plan, journal, coordinator_plan)
    assert pilot.advance(plan, journal, run, lambda *args: None,
                         lambda first: None, before)["status"] == "pilot_reopen_observed"
    pilot.verify_document_pins(plan, journal, coordinator_plan)
    run.states[pilot.SERVICES[0]]["trafficStatuses"][0]["tag"] = "drifted-tag"
    with pytest.raises(pilot.rolling.RollingDenied, match="document_caller_state_changed"):
        pilot.verify_document_pins(plan, journal, coordinator_plan)
