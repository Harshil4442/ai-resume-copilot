"""Synthetic source/CI/Build ports; no native qualification or funding claim."""
from __future__ import annotations

import copy
import hashlib
import json
import subprocess
from pathlib import Path

import pytest

from scripts import qualify_native_quality_acquisition as q

ROOT = Path(__file__).resolve().parents[2]


def test_actual_immutable_dependencies_and_independent_five_path_controller():
    value = q.source_identity(ROOT)
    assert value["historical_baseline"] == {"release": q.HISTORICAL_RELEASE,
        "qualification_sha256": q.HISTORICAL_FINGERPRINT, "ci_run_id": 41175964104,
        "eligible": False, "state": "FAILED_no_Build"}
    assert set(value["component_paths"]) == set(q.COMPONENT_PATHS) and len(q.COMPONENT_PATHS) == 5
    assert value["full_ci_suites"] == list(q.SUITES)
    assert value["runner_entry_sha256"] == q.ENTRY_SHA256
    assert value["runtime_dependencies_sha256"] == q.digest(value["execution_dependencies"])
    assert "runner_release" not in value and "runner_app_and_contract_sha256" not in value


@pytest.mark.parametrize("name", q.COMPONENT_PATHS)
def test_actual_component_file_only_drift_invalidates_its_closure(tmp_path, name):
    for path in q.COMPONENT_PATHS:
        dest = tmp_path / path
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes((ROOT / path).read_bytes())
    before = q.component_hashes(tmp_path)
    (tmp_path / name).write_bytes((tmp_path / name).read_bytes() + b"\n# source-only drift\n")
    after = q.component_hashes(tmp_path)
    assert {key for key in before if before[key] != after[key]} == {name}


@pytest.fixture
def native_ports(monkeypatch):
    from infra.gcp import native_quality_acquisition_isolation as isolation

    native, contract, _ = q._source_modules()
    image = isolation.RUNNER_REPOSITORY + "@sha256:" + "e" * 64
    release, controller_head = "c" * 40, "d" * 40
    build_id = "11111111-1111-1111-1111-111111111111"
    names = (contract.COMPATIBILITY_STEP, contract.PROTOCOL_STEP, contract.RETAINED_STEP)
    jobs = [{"name": name, "status": "completed", "conclusion": "success",
             "steps": [{"name": step, "status": "completed", "conclusion": "success"} for step in names]
             if name == "backend" else []} for name in sorted(native.rolling.release.CI_JOBS)]
    ci = {"headSha": release, "workflowName": "CI", "status": "completed", "conclusion": "success", "jobs": jobs}
    build = {"id": build_id, "projectId": isolation.PROJECT, "status": "SUCCESS",
        "sourceProvenance": {"resolvedGitSource": {"url": "https://github.com/Harshil4442/ai-resume-copilot.git",
            "revision": release, "dir": "."}}, "steps": [
            {"name": "gcr.io/cloud-builders/docker", "args": ["build", "--tag", image.split("@")[0] + ":" + release, "."]},
            {"name": "gcr.io/cloud-builders/docker", "args": ["push", image.split("@")[0] + ":" + release]}],
        "results": {"images": [{"name": image.split("@")[0] + ":" + release, "digest": "sha256:" + "e" * 64}]}}
    observed = dict(release=release, qualification_sha256="a" * 64, app_sha256=q.APP_SHA256,
        app_files=289, entry_sha256=q.ENTRY_SHA256, runtime_dependencies_sha256="b" * 64)
    receipt = dict(schema_version="native-quality-qualified-runner-v1",
        repository="https://github.com/Harshil4442/ai-resume-copilot.git", **observed,
        ci_run_id=1, build_id=build_id, image=image, qualification_contract="hirewiz_schema26_app_rolling_v3",
        entry_path=q.ENTRY, observed_at="2026-10-31T00:00:00Z", ci_metadata_sha256=q.digest(ci),
        build_metadata_sha256=q.digest(build))
    from test_native_quality_acquisition_isolation import plan
    raw = json.dumps(receipt, sort_keys=True, separators=(",", ":")).encode()
    p = plan(controller_sha256="f" * 64, qualified_runner_receipt_sha256=hashlib.sha256(raw).hexdigest())
    identity = {"controller_sha256": "f" * 64, "runtime_dependencies_sha256": "b" * 64}
    state = {"ci": ci, "build": build, "observed": observed, "controller_head": controller_head,
             "controller_conclusion": "success", "commands": [], "source_calls": 0}

    def command(self, executable, *args):
        state["commands"].append((executable, args))
        if executable == "gcloud":
            assert args[:3] == ("builds", "describe", build_id)
            return json.dumps(state["build"]).encode()
        assert executable == "gh"
        if args[:3] == ("run", "view", "1"):
            return json.dumps(state["ci"]).encode()
        if args[:3] == ("run", "view", "2"):
            body = copy.deepcopy(ci)
            body.update(headSha=state["controller_head"], conclusion=state["controller_conclusion"])
            return json.dumps(body).encode()
        assert args[0] == "api"
        return json.dumps({"path": ".github/workflows/ci.yml", "head_sha": release if args[1].endswith("/1") else state["controller_head"],
            "event": "push", "repository": {"full_name": "Harshil4442/ai-resume-copilot"}}).encode()

    def source(root):
        state["source_calls"] += 1
        return copy.deepcopy(state["observed"])
    # Exercise the ORIGINAL immutable Git-Build/five-job verifier against fake
    # metadata, skipping only the child-owned local checkout check already faked.
    monkeypatch.setattr(native.Source26Boundary, "verify_source_image", lambda self, **kwargs:
        self._verify_source_image_provenance(**kwargs))
    monkeypatch.setattr(native.Source26Boundary, "_command", command)
    monkeypatch.setattr(q, "source_identity", lambda root: copy.deepcopy(identity))
    monkeypatch.setattr(q, "runner_source", source)
    monkeypatch.setattr(q, "git", lambda root, *args: b"" if args[0] == "status" else controller_head.encode())
    monkeypatch.setenv("CLOUDSDK_CORE_DISABLE_FILE_LOGGING", "true")
    monkeypatch.setenv("CLOUDSDK_CORE_LOG_HTTP", "false")
    return p, raw, state, identity, receipt


def test_real_original_provenance_and_three_steps_plus_controller_CI_are_all_required(native_ports):
    p, raw, state, _, _ = native_ports
    out = q.verify_qualified_runner(p, raw, runner_root=ROOT, controller_ci_run_id=2)
    assert out["fresh_source_CI_Build_verified"] and not out["processing_authorized"] and not out["native_approval"]
    assert state["source_calls"] == 2
    assert {command for command, _ in state["commands"]} == {"gh", "gcloud"}


@pytest.mark.parametrize("change", [
    lambda s: s["observed"].update(release="0" * 40),
    lambda s: s["observed"].update(qualification_sha256="0" * 64),
    lambda s: s["observed"].update(app_sha256="0" * 64),
    lambda s: s["observed"].update(app_files=288),
    lambda s: s["observed"].update(entry_sha256="0" * 64),
    lambda s: s["observed"].update(runtime_dependencies_sha256="0" * 64),
    lambda s: s["ci"].update(headSha="0" * 40),
    lambda s: s["ci"].update(conclusion="failure"),
    lambda s: s["ci"]["jobs"].pop(),
    lambda s: s["build"].update(status="FAILURE"),
    lambda s: s["build"]["sourceProvenance"]["resolvedGitSource"].update(revision="0" * 40),
    lambda s: s["build"]["results"]["images"][0].update(digest="sha256:" + "0" * 64),
    lambda s: s["build"]["steps"][0].update(env=["UNREVIEWED=1"]),
    lambda s: s.update(controller_conclusion="failure"),
])
def test_claimed_receipt_cannot_override_actual_source_CI_Build_or_controller(change, native_ports):
    p, raw, state, _, _ = native_ports
    change(state)
    with pytest.raises((ValueError, RuntimeError)):
        q.verify_qualified_runner(p, raw, runner_root=ROOT, controller_ci_run_id=2)


@pytest.mark.parametrize("mode", ["missing", "failed", "duplicate"])
def test_actual_backend_qualification_step_failures_refuse(mode, native_ports):
    p, raw, state, _, _ = native_ports
    steps = next(row for row in state["ci"]["jobs"] if row["name"] == "backend")["steps"]
    if mode == "missing":
        steps.pop()
    elif mode == "failed":
        steps[0]["conclusion"] = "failure"
    else:
        steps.append(copy.deepcopy(steps[0]))
    with pytest.raises(ValueError, match="three_qualification_steps"):
        q.verify_qualified_runner(p, raw, runner_root=ROOT, controller_ci_run_id=2)


def test_wrong_raw_receipt_controller_crossbinding_and_failed_historical_source(native_ports):
    p, raw, state, identity, receipt = native_ports
    for value in (p.model_copy(update={"controller_sha256": "0" * 64}),
                  p.model_copy(update={"qualified_runner_receipt_sha256": "0" * 64})):
        with pytest.raises(ValueError):
            q.verify_qualified_runner(value, raw, runner_root=ROOT, controller_ci_run_id=2)
    receipt["release"] = q.HISTORICAL_RELEASE
    state["observed"]["release"] = q.HISTORICAL_RELEASE
    raw = json.dumps(receipt).encode()
    p = p.model_copy(update={"qualified_runner_receipt_sha256": hashlib.sha256(raw).hexdigest()})
    with pytest.raises(ValueError, match="failed_historical"):
        q.verify_qualified_runner(p, raw, runner_root=ROOT, controller_ci_run_id=2)
    assert identity["controller_sha256"] == "f" * 64


def test_source_child_has_no_credential_env_and_is_bounded(monkeypatch, tmp_path):
    observed = dict(release="c" * 40, qualification_sha256="a" * 64, app_sha256=q.APP_SHA256,
        app_files=289, entry_sha256=q.ENTRY_SHA256, runtime_dependencies_sha256="b" * 64)
    monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS", "/never/read")
    monkeypatch.setenv("GH_TOKEN", "synthetic-not-a-token")
    def run(command, **kwargs):
        assert command[:3] == [q.sys.executable, "-I", "-B"]
        assert "--source-child" in command and kwargs["timeout"] == 30
        assert set(kwargs["env"]) == {"PATH", "HOME", "XDG_CONFIG_HOME", "LANG", "PYTHONDONTWRITEBYTECODE",
            "CLOUDSDK_CORE_DISABLE_FILE_LOGGING", "CLOUDSDK_CORE_LOG_HTTP"}
        assert kwargs["stdin"] == subprocess.DEVNULL and kwargs["capture_output"]
        return subprocess.CompletedProcess(command, 0, json.dumps(observed).encode(), b"")
    monkeypatch.setattr(q.subprocess, "run", run)
    assert q.runner_source(tmp_path) == observed


def test_post_native_source_mutation_invalidates_before_return(monkeypatch, native_ports):
    p, raw, state, _, _ = native_ports
    original = q.runner_source
    def changing(root):
        value = original(root)
        if state["source_calls"] == 2:
            value["qualification_sha256"] = "0" * 64
        return value
    monkeypatch.setattr(q, "runner_source", changing)
    with pytest.raises(ValueError, match="source_changed_during"):
        q.verify_qualified_runner(p, raw, runner_root=ROOT, controller_ci_run_id=2)


def test_legitimate_new_QFP_is_external_not_the_failed_historical_constant(native_ports):
    p, _, state, _, receipt = native_ports
    state["observed"]["qualification_sha256"] = receipt["qualification_sha256"] = "9" * 64
    raw = json.dumps(receipt).encode()
    p = p.model_copy(update={"qualified_runner_receipt_sha256": hashlib.sha256(raw).hexdigest()})
    out = q.verify_qualified_runner(p, raw, runner_root=ROOT, controller_ci_run_id=2)
    assert out["fresh_source_CI_Build_verified"] and not out["processing_authorized"]
    assert receipt["qualification_sha256"] != q.HISTORICAL_FINGERPRINT


@pytest.mark.parametrize("field", ["ci_metadata_sha256", "build_metadata_sha256"])
def test_receipt_custody_hash_must_match_fresh_original_metadata(field, native_ports):
    p, _, _, _, receipt = native_ports
    receipt[field] = "0" * 64
    raw = json.dumps(receipt).encode()
    p = p.model_copy(update={"qualified_runner_receipt_sha256": hashlib.sha256(raw).hexdigest()})
    with pytest.raises(ValueError, match="metadata_custody_changed"):
        q.verify_qualified_runner(p, raw, runner_root=ROOT, controller_ci_run_id=2)
