"""Inert tests for the finite post-schema25 pilot reopening contract."""
from __future__ import annotations

import copy
import importlib
import sys
from dataclasses import asdict
from pathlib import Path

import pytest
from backend.tests.test_rolling_release import FixtureBoundary

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "infra/gcp"))
pilot = importlib.import_module("schema25_pilot_reopen")


class Controls:
    def get(self, kind, name):
        return {"name": pilot.rolling.PARENT + "/" + kind + "/" + name,
                "state": "RUNNING" if kind == "queue" else "ENABLED"}


def context(tmp_path, monkeypatch):
    run = FixtureBoundary()
    requested = ("EMPLOYER_DISCOVERY_ENABLED", "RESUME_DIRECT_UPLOAD_ENABLED")
    originals = {pilot.SERVICES[0]: "RESUME_DIRECT_UPLOAD_ENABLED",
                 pilot.SERVICES[2]: "EMPLOYER_DISCOVERY_ENABLED"}
    for service, flag in originals.items():
        prior = run.plan.pin(service).prior_revision
        for row in (run.states[service]["template"], run.revisions[(service, prior)]):
            row["containers"][0]["env"].append({"name": flag, "value": "true"})
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
    run, _, plan, journal, _ = context(tmp_path, monkeypatch)
    health = []
    result = pilot.advance(plan, journal, run, lambda *args: health.append(args),
                           lambda first: None)
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
    run, _, plan, journal, _ = context(tmp_path, monkeypatch)
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
        pilot.advance(plan, journal, run, lambda *args: None, lambda first: None)
    assert run.writes == []


def test_unknown_ack_never_replays_and_reconcile_starts_no_next_write(tmp_path, monkeypatch):
    run, _, plan, journal, _ = context(tmp_path, monkeypatch)
    run.lose_ack = True
    with pytest.raises(RuntimeError, match="synthetic-sensitive"):
        pilot.advance(plan, journal, run, lambda *args: None, lambda first: None)
    assert len(run.writes) == 1
    with pytest.raises(pilot.rolling.RollingDenied, match="unknown_write_reconcile_only"):
        pilot.advance(plan, journal, run, lambda *args: None, lambda first: None)
    assert pilot.advance(plan, journal, run, lambda *args: None, lambda first: None,
                         reconcile=True)["status"] == "pilot_phase_partial"
    assert len(run.writes) == 1
    assert pilot.advance(plan, journal, run, lambda *args: None,
                         lambda first: None)["status"] == "pilot_reopen_observed"
    assert len(run.writes) == 2


def test_restoration_or_document_gate_failure_prevents_reopen(tmp_path, monkeypatch):
    run, _, plan, journal, _ = context(tmp_path, monkeypatch)
    observed = []

    def gate(first):
        observed.append(first)
        raise pilot.rolling.RollingDenied("schema25_pilot_resource_changed")

    with pytest.raises(pilot.rolling.RollingDenied, match="resource_changed"):
        pilot.advance(plan, journal, run, lambda *args: None, gate)
    assert observed == [True] and run.writes == []
