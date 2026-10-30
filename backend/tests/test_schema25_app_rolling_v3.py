"""Prospective26 source/transport ports; never deployment or SQL acceptance."""
from __future__ import annotations

import copy
import importlib
import json
import os
import subprocess
import sys
from dataclasses import asdict, fields, replace
from pathlib import Path

import pytest
from test_schema25_app_rolling_release import AppFixture

from scripts import schema25_app_release_contract_v3 as contract

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "infra/gcp"))
app = importlib.import_module("schema26_app_rolling_v3")
rolling = app.rolling


class V3Fixture(AppFixture):
    def __init__(self):
        super().__init__()
        pins = []
        for pin in self.plan.services:
            state = self.states[pin.service]
            template = state["template"]
            template["labels"]["hirewiz-source"] = app.PRIOR.release
            template["containers"][0]["image"] = self.plan.image.split("@")[0] + "@" + app.PRIOR.image_digest
            rolling.environment(template)["APP_RELEASE"]["value"] = app.PRIOR.release
            revision = {**copy.deepcopy(template), "name": state["latestReadyRevision"],
                "conditions": [{"type": "Ready", "state": "CONDITION_SUCCEEDED"}]}
            self.revisions[(pin.service, pin.prior_revision)] = revision
            pins.append(replace(pin, prior_release=app.PRIOR.release,
                template_sha256=rolling.template_sha(template, pin.service),
                revision_sha256=rolling.sha({key: revision.get(key) for key in pin.revision_keys})))
        values = {field.name: getattr(self.plan, field.name) for field in fields(app.historical.App25Plan)
            if field.name not in {"schema_contract", "database_schema", "prior_source", "services", "reachable_revisions"}}
        self.plan = app.App26V3Plan(**values, services=tuple(pins), reachable_revisions=tuple(
            app.historical.reachable_pin(pin.service, pin.prior_revision, self.revisions[(pin.service, pin.prior_revision)],
                prior_source=app.PRIOR) for pin in pins))
        self.targets = {pin.service: app.candidate_template(self.plan, pin, self.service(pin.service)) for pin in pins}
        self.plan.validate()


@pytest.fixture
def context(tmp_path):
    tmp_path.chmod(0o700)
    boundary = V3Fixture()
    return boundary, rolling.Journal(tmp_path, boundary.plan)


def test_complete_frozen_app_and_disjoint_schema26_source_is_required():
    prior = app.initial.prior_app_files(ROOT, contract.PRIOR_RELEASE)
    assert contract.source_fingerprint(ROOT, prior)
    assert app.PRIOR.release == contract.PRIOR_RELEASE
    assert app.PRIOR.image_digest.startswith("sha256:df4618fa")


def test_standalone_source_entrypoint_uses_only_the_trusted_checkout(tmp_path):
    # -I removes pytest's repo-root injection and all PYTHONPATH authority.
    # A hostile CWD must not satisfy an accidentally missing package bootstrap.
    for name in ("infra.py", "scripts.py", "rolling_release.py"):
        (tmp_path / name).write_text("raise AssertionError('Untrusted CWD imported')\n")
    script = ROOT / "infra/gcp/schema26_app_rolling_v3.py"
    program = """
import runpy, socket, sys
from sqlalchemy.engine import Engine
def deny(*args, **kwargs):
    raise AssertionError('No database/network in source-only entrypoint')
for name in ('connect', 'connect_ex', 'bind', 'listen'):
    setattr(socket.socket, name, deny)
socket.getaddrinfo = socket.create_connection = Engine.connect = deny
sys.argv = [SCRIPT, 'source']
runpy.run_path(SCRIPT, run_name='__main__')
""".replace("SCRIPT", repr(str(script)))
    result = subprocess.run([sys.executable, "-I", "-B", "-"], cwd=tmp_path, input=program,
        text=True, capture_output=True, timeout=30, check=False,
        env={"PATH": os.defpath, "APP_ENV": "test", "DATABASE_URL": "sqlite://",
            "PYTHONDONTWRITEBYTECODE": "1", "PYTHONPATH": str(tmp_path)})
    assert result.returncode == 0, result.stderr[-2048:]
    assert not result.stderr
    assert json.loads(result.stdout) == {
        "source_sha256": contract.source_fingerprint(ROOT, app.initial.prior_app_files(ROOT, contract.PRIOR_RELEASE)),
        "serving_prior_observed": False, "schema26_applied": False,
        "migration_authority": False, "rollout_authority": False,
    }


@pytest.mark.parametrize("name", ["backend/tests/test_schema25_app_rolling_release.py",
    "backend/tests/test_rolling_release.py", "backend/tests/test_document_worker_deployment.py",
    "frontend/app/api/browser-pairing/device/[operation]/route.ts",
    "browser-companion/extension/pairing-transport.js", "browser-companion/extension/pairing-runtime.js",
    "browser-companion/tests/pairing-readiness-fixture.js", "backend/tests/browser_paired_assistance_chrome_bridge.py",
    "browser-companion/scripts/package-reviewed.py", "browser-companion/package-lock.json",
    "frontend/components/BrowserAssistanceReview.tsx", "frontend/public/downloads/hirewiz-browser-companion-0.8.6.zip"])
def test_changed_rolling_proof_helper_cannot_reuse_a_previous_plan(monkeypatch, name):
    original = Path.read_bytes
    prior = app.initial.prior_app_files(ROOT, contract.PRIOR_RELEASE)
    baseline = contract.source_fingerprint(ROOT, prior)
    case = V3Fixture()
    plan = replace(case.plan, compatibility_sha256=baseline)
    native = object.__new__(app.NativeApp26Boundary)
    native.root = ROOT
    monkeypatch.setattr(Path, "read_bytes", lambda path: original(path) + b"\n# unreviewed proof helper\n"
        if path == ROOT / name else original(path))
    assert contract.source_fingerprint(ROOT, prior) != baseline
    # No observer or command seam is installed: source drift must stop first.
    with pytest.raises(rolling.RollingDenied, match="compatibility_source_changed"):
        native._compatibility(plan)


@pytest.mark.parametrize("name", ["backend/app/domains/career/evidence_write_fence.py",
    "backend/app/domains/career/evidence_fence_definition.py", "backend/app/domains/credits/reconciliation_schema26.py",
    "backend/app/services/provider_input_guard.py"])
def test_changed_guard_or_successor_cannot_reuse_full_app_pin(monkeypatch, name):
    original = Path.read_bytes
    monkeypatch.setattr(Path, "read_bytes", lambda path: original(path) + b"\n# unreviewed code\n"
        if path == ROOT / name else original(path))
    with pytest.raises(Exception, match="source_changed|candidate_app_unqualified"):
        contract.source_fingerprint(ROOT, app.initial.prior_app_files(ROOT, contract.PRIOR_RELEASE))


def test_distinct_plan_never_adopts25_or_migration_authority(context):
    boundary, _ = context
    raw = rolling.canonical(asdict(boundary.plan))
    assert app.load(raw) == boundary.plan
    for loader in (app.historical.load, app.historical.load_v2, app.initial.load_plan):
        with pytest.raises(rolling.RollingDenied):
            loader(raw)
    old = asdict(boundary.plan) | {"schema_contract": app.historical.contract_v2.CONTRACT,
        "database_schema": "20261028_0025"}
    with pytest.raises(rolling.RollingDenied, match="distinct_plan_required"):
        app.load(rolling.canonical(old))
    assert not boundary.writes


def test_stage_advance_and_restricted_old_source_rollback_keep_original_journal(context):
    boundary, journal = context
    assert app.execute("stage", boundary.plan, boundary, journal)["status"] == "schema26_app_staged_zero_traffic"
    assert app.execute("advance", boundary.plan, boundary, journal)["status"] == "schema26_app_release_observed"
    result = app.execute("rollback", boundary.plan, boundary, journal)
    assert result["status"] == "schema26_app_prior_traffic_restored_writes_restricted"
    assert result["evidence_fence_required"] is True and result["old_source_evidence_writes_authorized"] is False
    assert result["migrations"] == result["resource_access_changes"] == 0
    writes = len(boundary.writes)
    assert app.execute("reconcile", boundary.plan, boundary, journal)["replay_authorized"] is False
    assert len(boundary.writes) == writes


@pytest.mark.parametrize("mode", ["pause", "lose_ack"])
def test_unknown_stage_never_replays_the_original_write(context, mode):
    boundary, journal = context
    setattr(boundary, mode, True)
    try:
        result = app.execute("stage", boundary.plan, boundary, journal)
        assert result["rolling_release_complete"] is False
    except RuntimeError:
        assert mode == "lose_ack"
    count = len(boundary.writes)
    if mode == "lose_ack":
        with pytest.raises(rolling.RollingDenied):
            app.execute("stage", boundary.plan, boundary, journal)
    else:
        result = app.execute("stage", boundary.plan, boundary, journal)
        assert result["status"] == "schema26_app_same_operation_pending"
    assert len(boundary.writes) == count


@pytest.mark.parametrize("flag", contract.STAGE5_FLAGS)
def test_stage5_activation_is_not_hidden_in_a_v3_template(context, flag):
    boundary, _ = context
    pin = boundary.plan.services[0]
    current = boundary.service(pin.service)
    current["template"]["containers"][0]["env"].append({"name": flag, "value": "true"})
    with pytest.raises(rolling.RollingDenied, match="stage5_activation_outside_release"):
        app.candidate_template(boundary.plan, pin, current)


def test_new_privacy_mail_is_closed_without_disabling_the_authorized_password_reset(context):
    boundary, _ = context
    pin = boundary.plan.services[0]
    current = boundary.service(pin.service)
    app.candidate_template(boundary.plan, pin, current)
    current["template"]["containers"][0]["env"].append({"name": contract.PRIVACY_FLAG, "value": "true"})
    with pytest.raises(rolling.RollingDenied, match="privacy_mail_activation_outside_release"):
        app.candidate_template(boundary.plan, pin, current)


@pytest.mark.parametrize("wrong", ["prior_source", "ready_zero_traffic"])
def test_native_capture_requires_actual_e23_serving_not_prospective_pins(context, wrong):
    boundary, _ = context
    pin = boundary.plan.services[0]
    native = object.__new__(app.NativeApp26Boundary)
    native.service, native.revision, native.iam = boundary.service, boundary.revision, boundary.iam
    native._reference = lambda _name, version: version
    assert native._pin(pin.service).prior_release == contract.PRIOR_RELEASE
    if wrong == "prior_source":
        rolling.environment(boundary.states[pin.service]["template"])["APP_RELEASE"]["value"] = app.historical.PRIOR_V2.release
    else:
        boundary.states[pin.service]["latestCreatedRevision"] = rolling.PARENT + "/services/" + pin.service + "/revisions/" + pin.service + "-new-ready-zero"
    with pytest.raises(rolling.RollingDenied):
        native._pin(pin.service)
    assert not boundary.writes
