"""Synthetic source/CI/Build ports; no native qualification or funding claim."""
from __future__ import annotations

import copy
import hashlib
import io
import json
import os
import socket
import subprocess
import sys
import tarfile
from pathlib import Path
from types import SimpleNamespace

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
    assert value["operator_data_inputs"] == q.operator_data_hashes(ROOT)
    assert value["runner_snapshot_inputs"] == {q.OPERATOR_REGISTRY_PATH: {
        "release": q.REGISTRY_RUNNER_RELEASE, "sha256": q.REGISTRY_RUNNER_SHA256}}
    assert set(value["proof_inputs"]) == set(q.PROOF_PATHS) and len(q.PROOF_PATHS) == 3
    assert value["absent_package_ancestors"] == list(q.ABSENT_ANCESTORS)
    assert set(value["current_proof_inputs"]) == set(q.CURRENT_PROOF_PATHS)
    assert len(value["execution_dependencies"]) == 466 and len(q.CURRENT_PROOF_PATHS) == 1
    assert q.CURRENT_EXECUTION_PATHS == (
        ".github/workflows/ci.yml",
        "backend/app/domains/credits/canary.py", "backend/app/domains/employer/connectors.py",
        "backend/app/domains/employer/tasks.py", "backend/app/domains/recovery/browser_pairing_production.py",
        "backend/scripts/document_refresh.py", "backend/scripts/schema25_app_release_contract_v3.py",
        "infra/gcp/document_consumed_native.py", "infra/gcp/document_refresh_recovery.py",
        "infra/gcp/document_refresh_transition.py", "infra/gcp/document_source_candidate.py",
        "infra/gcp/document_source_release.py", "infra/gcp/rolling_release.py", "infra/gcp/schema26_app_rolling_v3.py",
        "infra/gcp/schema26_lifecycle_join.py",
        "infra/gcp/schema26_maintenance_guard.py",
        "infra/gcp/v3_readonly_adapter.py",
    )
    assert q.CURRENT_ADDED_EXECUTION_PATHS == ("backend/app/domains/employer/source_revalidation.py",
        "backend/scripts/schema26_app_successor.py")
    assert set(value["current_execution_inputs"]) == {*q.CURRENT_EXECUTION_PATHS, *q.CURRENT_ADDED_EXECUTION_PATHS}
    assert all(value["execution_dependencies"][name] == digest
        for name, digest in value["current_execution_inputs"].items())
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
        app_files=290, entry_sha256=q.ENTRY_SHA256, runtime_dependencies_sha256="b" * 64)
    receipt = dict(schema_version="native-quality-qualified-runner-v1",
        repository="https://github.com/Harshil4442/ai-resume-copilot.git", **observed,
        ci_run_id=1, build_id=build_id, image=image, qualification_contract="hirewiz_schema26_app_rolling_v3",
        entry_path=q.ENTRY, observed_at="2026-10-31T00:00:00Z", ci_metadata_sha256=q.digest(ci),
        build_metadata_sha256=q.digest(build))
    from test_native_quality_acquisition_isolation import plan
    raw = json.dumps(receipt, sort_keys=True, separators=(",", ":")).encode()
    p = plan(controller_sha256="f" * 64, qualified_runner_receipt_sha256=hashlib.sha256(raw).hexdigest())
    identity = {"controller_sha256": "f" * 64, "runtime_dependencies_sha256": "b" * 64}
    observed["current_proof_sha256"] = q.digest({q.CURRENT_PROOF_PATHS[0]: "8" * 64})
    identity["current_proof_inputs"] = {q.CURRENT_PROOF_PATHS[0]: "8" * 64}
    state = {"ci": ci, "build": build, "observed": observed, "controller_head": controller_head, "controller_ci_head": controller_head,
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
            body.update(headSha=state["controller_ci_head"], conclusion=state["controller_conclusion"])
            return json.dumps(body).encode()
        assert args[0] == "api"
        return json.dumps({"path": ".github/workflows/ci.yml", "head_sha": release if args[1].endswith("/1") else state["controller_ci_head"],
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
    monkeypatch.setattr(q, "git", lambda root, *args: b"" if args[0] == "status" else state["controller_head"].encode())
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
        app_files=290, entry_sha256=q.ENTRY_SHA256, runtime_dependencies_sha256="b" * 64,
        current_proof_sha256="8" * 64)
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


@pytest.mark.parametrize("name", q.PROOF_PATHS)
def test_actual_default_pytest_fixture_drift_refuses_controller_identity(monkeypatch, name):
    original = Path.read_bytes
    reads = []
    def changed(path):
        raw = original(path)
        if path == ROOT / name:
            reads.append(str(path))
            return raw + b"\n# unreviewed proof-input drift\n"
        return raw
    monkeypatch.setattr(Path, "read_bytes", changed)
    with pytest.raises(ValueError, match="historical_proof_input_changed"):
        q.proof_hashes(ROOT)
    assert reads == [str(ROOT / name)]


@pytest.mark.parametrize("name", q.ABSENT_ANCESTORS)
def test_new_executable_package_ancestor_is_refused_without_import(tmp_path, name):
    q.ancestor_fence(tmp_path)
    target = tmp_path / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text('raise AssertionError("must_never_import")\n')
    with pytest.raises(ValueError, match="undeclared_package_ancestor"):
        q.ancestor_fence(tmp_path)



def test_current_observer_only_committed_drift_changes_controller_not_runtime(monkeypatch):
    before = q.source_identity(ROOT)
    name = q.CURRENT_PROOF_PATHS[0]
    original_read, original_git = Path.read_bytes, q.git
    changed = (ROOT / name).read_bytes() + b"\n# synthetic current CI observer change\n"
    def read(path):
        return changed if path == ROOT / name else original_read(path)
    def git(root, *args):
        return changed if args == ("show", "HEAD:" + name) else original_git(root, *args)
    monkeypatch.setattr(Path, "read_bytes", read)
    with pytest.raises(ValueError, match="uncommitted_current_proof_changed"):
        q.source_identity(ROOT)
    monkeypatch.setattr(q, "git", git)
    after = q.source_identity(ROOT)
    assert before["controller_sha256"] != after["controller_sha256"]
    assert before["current_proof_inputs"] != after["current_proof_inputs"]
    assert before["execution_dependencies"] == after["execution_dependencies"]
    assert before["runtime_dependencies_sha256"] == after["runtime_dependencies_sha256"]


@pytest.mark.parametrize("name", (*q.CURRENT_EXECUTION_PATHS, *q.CURRENT_ADDED_EXECUTION_PATHS))
def test_uncommitted_successor_file_is_refused_in_actual_execution_closure(monkeypatch, name):
    original = Path.read_bytes
    def changed(path):
        raw = original(path)
        return raw + b"\n# uncommitted successor drift\n" if path == ROOT / name else raw
    monkeypatch.setattr(Path, "read_bytes", changed)
    with pytest.raises(ValueError, match="uncommitted_current_execution_changed"):
        q.controller_dependency_hashes(ROOT)


@pytest.mark.parametrize("name", ["Dockerfile", "backend/app/domains/analysis/native_input_acquisition.py"])
def test_unrelated_execution_file_still_requires_exact_failed_baseline_bytes(monkeypatch, name):
    original = Path.read_bytes
    def changed(path):
        raw = original(path)
        return raw + b"\n# unrelated runtime drift\n" if path == ROOT / name else raw
    monkeypatch.setattr(Path, "read_bytes", changed)
    with pytest.raises(ValueError, match="historical_execution_dependency_changed"):
        q.controller_dependency_hashes(ROOT)


@pytest.mark.parametrize("name", (*q.CURRENT_EXECUTION_PATHS, *q.CURRENT_ADDED_EXECUTION_PATHS))
def test_committed_foreign_successor_changes_runtime_closure_and_refuses_runner(tmp_path, name):
    # A real local Git checkout with shared read-only objects avoids pretending
    # a committed foreign byte change is merely an uncommitted working-tree edit.
    # It supplies no CI, Build, native or funding authority.
    before = q.controller_dependency_hashes(ROOT)
    for path in before:
        target = tmp_path / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(q._registry_snapshot(ROOT) if path == q.OPERATOR_REGISTRY_PATH
                           else (ROOT / path).read_bytes())
    (tmp_path / name).write_bytes((tmp_path / name).read_bytes() + b"\n# committed foreign successor\n")
    environment = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    environment.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL="/dev/null", GIT_TERMINAL_PROMPT="0")
    def run(*args):
        subprocess.run(["git", "-c", "user.name=Synthetic source test", "-c", "user.email=source@example.invalid",
            "-C", str(tmp_path), *args], cwd=tmp_path, env=environment,
            capture_output=True, check=True, timeout=10)
    run("init", "-q")
    common = Path(q.git(ROOT, "rev-parse", "--git-common-dir").decode().strip())
    common = common if common.is_absolute() else ROOT / common
    (tmp_path / ".git/objects/info/alternates").write_text(str((common / "objects").resolve()) + "\n")
    run("add", "--all")
    run("commit", "-q", "-m", "Synthetic foreign source only")
    assert not q.git(tmp_path, "status", "--porcelain")
    after = q.dependency_hashes(tmp_path)
    assert len(before) == len(after) == 466
    assert {key for key in before if before[key] != after[key]} == {name}
    assert q.digest(before) != q.digest(after)
    with pytest.raises(ValueError, match="controller_runner_execution_dependencies"):
        q._runner_source(tmp_path)


def registry_commit(root):
    environment = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    environment.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL="/dev/null", GIT_TERMINAL_PROMPT="0")
    for args in (("add", "--all"), ("commit", "-q", "-m", "Owned synthetic registry control")):
        subprocess.run(["git", "-c", "core.hooksPath=/dev/null", "-c", "commit.gpgsign=false",
            "-c", "user.name=Synthetic registry test", "-c", "user.email=registry@example.invalid",
            "-C", str(root), *args], env=environment, capture_output=True, check=True, timeout=10)


@pytest.fixture
def registry_tree(tmp_path):
    paths = {*q.controller_dependency_hashes(ROOT), *q.COMPONENT_PATHS,
             *q.PROOF_PATHS, *q.CURRENT_PROOF_PATHS}
    for name in paths:
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(q._registry_snapshot(ROOT) if name == q.OPERATOR_REGISTRY_PATH
                         else (ROOT / name).read_bytes())
    subprocess.run(["git", "-C", str(tmp_path), "init", "-q"], check=True, capture_output=True, timeout=10)
    common = Path(q.git(ROOT, "rev-parse", "--git-common-dir").decode().strip())
    common = common if common.is_absolute() else ROOT / common
    (tmp_path / ".git/objects/info/alternates").write_text(str((common / "objects").resolve()) + "\n")
    # The owned checkout descends from the real source snapshot, while all new
    # registry values below are synthetic metadata, never provider receipts.
    subprocess.run(["git", "-C", str(tmp_path), "reset", "--soft", q.REGISTRY_RUNNER_RELEASE],
                   check=True, capture_output=True, timeout=10)
    subprocess.run(["git", "-C", str(tmp_path), "read-tree", "--empty"],
                   check=True, capture_output=True, timeout=10)
    registry_commit(tmp_path)
    return tmp_path


def synthetic_registry_row(index=1):
    return {"release": f"{index:040x}", "image_digest": "sha256:" + f"{index:064x}",
            "build_id": f"00000000-0000-0000-0000-{index:012x}", "ci_run_id": index}


def write_registry(root, value, *, commit=True):
    (root / q.OPERATOR_REGISTRY_PATH).write_text(json.dumps(value, indent=2) + "\n")
    if commit:
        registry_commit(root)


def test_owned_append_changes_only_operator_context_and_not_qualified_runner_snapshot(registry_tree):
    before = q.source_identity(registry_tree)
    strict = q.dependency_hashes(registry_tree)
    value = q._registry_value(q._registry_snapshot(registry_tree))
    value["sources"].append(synthetic_registry_row())
    write_registry(registry_tree, value)
    after = q.source_identity(registry_tree)
    assert before["execution_dependencies"] == after["execution_dependencies"] == strict
    assert len(strict) == 466 and before["runtime_dependencies_sha256"] == after["runtime_dependencies_sha256"]
    assert before["component_paths"] == after["component_paths"]
    assert before["operator_data_inputs"] != after["operator_data_inputs"]
    assert before["controller_sha256"] != after["controller_sha256"]
    assert before["runner_snapshot_inputs"] == after["runner_snapshot_inputs"]
    with pytest.raises(ValueError, match="historical_execution_dependency_changed"):
        q.dependency_hashes(registry_tree)
    with pytest.raises(ValueError, match="historical_execution_dependency_changed"):
        q._runner_source(registry_tree)


@pytest.mark.parametrize("case", ["dirty", "reorder", "delete", "baseline_edit", "duplicate_release",
    "extra_key", "top_key", "overflow", "version_bool", "ci_bool", "bad_build_id"])
def test_owned_operator_registry_refuses_non_append_only_or_uncommitted_data(registry_tree, case):
    value = q._registry_value(q._registry_snapshot(registry_tree))
    row = synthetic_registry_row()
    value["sources"].append(row)
    if case == "reorder":
        value["sources"][0:2] = reversed(value["sources"][0:2])
    elif case == "delete":
        value["sources"].pop(0)
    elif case == "baseline_edit":
        value["sources"][0]["ci_run_id"] += 1
    elif case == "duplicate_release":
        row["release"] = value["sources"][0]["release"]
    elif case == "extra_key":
        row["approved"] = True
    elif case == "top_key":
        value["approved"] = True
    elif case == "overflow":
        value["sources"].extend(synthetic_registry_row(i) for i in range(2, 5))
    elif case == "version_bool":
        value["version"] = True
    elif case == "ci_bool":
        row["ci_run_id"] = True
    elif case == "bad_build_id":
        row["build_id"] = "-" * 36
    write_registry(registry_tree, value, commit=case != "dirty")
    with pytest.raises(ValueError, match="operator_registry"):
        q.source_identity(registry_tree)


def test_registry_duplicate_json_keys_and_symlinks_refuse(registry_tree):
    path = registry_tree / q.OPERATOR_REGISTRY_PATH
    raw = path.read_text().replace('"version": 1,', '"version": 1, "version": 1,')
    path.write_text(raw)
    registry_commit(registry_tree)
    with pytest.raises(ValueError, match="operator_registry_duplicate_key"):
        q.operator_data_hashes(registry_tree)
    path.unlink()
    path.symlink_to(ROOT / q.OPERATOR_REGISTRY_PATH)
    with pytest.raises(ValueError, match="operator_registry_path"):
        q.operator_data_hashes(registry_tree)


@pytest.mark.parametrize("case", ["edit", "delete", "reorder", "hidden_edit"])
def test_previously_appended_record_cannot_be_rewritten_later(registry_tree, case):
    value = q._registry_value(q._registry_snapshot(registry_tree))
    value["sources"].extend([synthetic_registry_row(), synthetic_registry_row(2)])
    write_registry(registry_tree, value)
    if case in {"edit", "hidden_edit"}:
        value["sources"][-1]["ci_run_id"] += 10
    elif case == "delete":
        value["sources"].pop()
    else:
        value["sources"][-2:] = reversed(value["sources"][-2:])
    write_registry(registry_tree, value)
    if case == "hidden_edit":
        (registry_tree / "operator-note.txt").write_text("An unrelated later commit cannot hide a prior rewrite.\n")
        registry_commit(registry_tree)
    with pytest.raises(ValueError, match="operator_registry_history_not_append_only"):
        q.source_identity(registry_tree)


def test_fork_before_snapshot_cannot_hide_rewrite_behind_prefix_compatible_merge(registry_tree):
    main = q.git(registry_tree, "rev-parse", "HEAD").decode().strip()
    fork = q.git(registry_tree, "rev-parse", q.REGISTRY_RUNNER_RELEASE + "^").decode().strip()
    assert q.git(registry_tree, "show", fork + ":" + q.OPERATOR_REGISTRY_PATH) == q._registry_snapshot(registry_tree)

    def commit_tree(*parents):
        q.git(registry_tree, "add", "--all")
        tree = q.git(registry_tree, "write-tree").decode().strip()
        return q.git(registry_tree, "-c", "user.name=Synthetic registry test",
            "-c", "user.email=registry@example.invalid", "-c", "commit.gpgsign=false",
            "commit-tree", tree, *(arg for parent in parents for arg in ("-p", parent)),
            "-m", "Owned synthetic fork and merge control").decode().strip()

    value = q._registry_value(q._registry_snapshot(registry_tree))
    value["sources"].append(synthetic_registry_row())
    write_registry(registry_tree, value, commit=False)
    first = commit_tree(fork)
    value["sources"][-1]["ci_run_id"] += 10
    write_registry(registry_tree, value, commit=False)
    rewritten = commit_tree(first)
    merged = commit_tree(main, rewritten)
    q.git(registry_tree, "reset", "--hard", merged)
    # Both final parents are prefixes. Only the reachable side history exposes
    # the rewrite; it is not a descendant of the pinned f361 snapshot.
    old_history = q.git(registry_tree, "log", "--format=%H", "--full-history", "--ancestry-path",
        q.REGISTRY_RUNNER_RELEASE + ".." + merged, "--", q.OPERATOR_REGISTRY_PATH).decode().splitlines()
    assert rewritten not in old_history and first not in old_history
    with pytest.raises(ValueError, match="operator_registry_history_not_append_only"):
        q.source_identity(registry_tree)


def test_other_committed_execution_dependency_is_not_operator_data(registry_tree):
    path = registry_tree / "backend/app/domains/analysis/native_input_acquisition.py"
    path.write_bytes(path.read_bytes() + b"\n# unrelated synthetic dependency drift\n")
    registry_commit(registry_tree)
    with pytest.raises(ValueError, match="historical_execution_dependency_changed"):
        q.controller_dependency_hashes(registry_tree)


def _owned_source_clone(target, release):
    """Share immutable local objects without checking out evaluation data."""
    environment = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    environment.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL="/dev/null", GIT_TERMINAL_PROMPT="0",
        GIT_NO_LAZY_FETCH="1", GIT_NO_REPLACE_OBJECTS="1")
    for args in (("clone", "--quiet", "--shared", "--no-checkout", str(ROOT), str(target)),
                 ("-C", str(target), "reset", "--soft", release),
                 ("-C", str(target), "read-tree", "--empty")):
        subprocess.run(["git", "-c", "core.hooksPath=/dev/null", "-c", "core.fsmonitor=false", *args],
                       env=environment, check=True, capture_output=True, timeout=30)
    target.chmod(0o700)


@pytest.mark.parametrize(("application_release", "different"), [
    ("c6a03af0636c7e9815159f807c9b126336d03233", {
        "infra/gcp/schema26_lifecycle_join.py", "infra/gcp/schema26_maintenance_guard.py",
        "infra/gcp/v3_readonly_adapter.py", "backend/scripts/schema25_app_release_contract_v3.py",
    }),
    ("c75cb13f9d8ed1cfc583c9340c9b0c58906e8e97", {
        "infra/gcp/schema26_lifecycle_join.py", "backend/scripts/schema25_app_release_contract_v3.py",
    }),
], ids=("registered-c6", "synthetic-c75-code-frozen-registry"))
def test_previous_source_execution_cannot_qualify_current_runtime(tmp_path, application_release, different):
    # C6 registration attests its genuine old event, not current byte equality.
    # The c75 case compares source only; it supplies no CI, Build or image proof.
    if application_release == "c6a03af0636c7e9815159f807c9b126336d03233":
        rows = q._registry_value((ROOT / q.OPERATOR_REGISTRY_PATH).read_bytes())["sources"]
        assert len([row for row in rows if row["release"] == application_release]) == 1
    expected = q.controller_dependency_hashes(ROOT)
    runner = tmp_path / "historical-runner"
    _owned_source_clone(runner, application_release)
    # Only source execution inputs are materialized. The mismatch must refuse
    # before a full-checkout qualification, import, CI or provider operation.
    assert not q.git(ROOT, "ls-tree", "-r", "--name-only", application_release, "--",
                     *q.CURRENT_ADDED_EXECUTION_PATHS).strip()
    historical_paths = set(expected) - set(q.CURRENT_ADDED_EXECUTION_PATHS)
    archive = q.git(ROOT, "archive", "--format=tar", application_release, "--", *sorted(historical_paths))
    materialized = set()
    with tarfile.open(fileobj=io.BytesIO(archive), mode="r:") as source:
        for member in source.getmembers():
            if member.isdir():
                continue
            assert member.isfile() and member.name in expected and member.size <= 1024 * 1024
            stream = source.extractfile(member)
            assert stream is not None
            target = runner / member.name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(stream.read())
            materialized.add(member.name)
    assert materialized == set(expected) - set(q.CURRENT_ADDED_EXECUTION_PATHS)
    assert all(not (runner / name).exists() for name in q.CURRENT_ADDED_EXECUTION_PATHS)
    assert not (runner / "backend/evals").exists()
    if application_release == "c75cb13f9d8ed1cfc583c9340c9b0c58906e8e97":
        # Preserve c75 execution code with the declared frozen registry DATA
        # reference in an owned synthetic commit. This is neither the actual
        # c75 image/receipt nor a claim about that release's source identity.
        registry = q._registry_snapshot(ROOT)
        assert hashlib.sha256(registry).hexdigest() == q.REGISTRY_RUNNER_SHA256
        (runner / q.OPERATOR_REGISTRY_PATH).write_bytes(registry)
        registry_commit(runner)
    common = {name: hashlib.sha256((runner / name).read_bytes()).hexdigest() for name in materialized}
    assert {name for name in common if common[name] != expected[name]} == different | {
        "backend/app/domains/employer/connectors.py", "backend/app/domains/employer/tasks.py",
        "infra/gcp/rolling_release.py", ".github/workflows/ci.yml",
    }
    with pytest.raises(ValueError, match="current_execution_not_committed"):
        q.dependency_hashes(runner)
    with pytest.raises(ValueError, match="current_execution_not_committed"):
        q._runner_source(runner)
    with pytest.raises(ValueError, match="runner_source_child_refused"):
        q.runner_source(runner)


def test_source_child_keeps_actual_application_qualification_and_runner_bytes(tmp_path):
    # The synthetic code-only tree has today's exact runtime and the immutable
    # runner registry snapshot. It is not a registered release/CI/Build receipt.
    _, contract, release = q._source_modules()
    import document_source_candidate as document

    dependencies = q.controller_dependency_hashes(ROOT)
    worker = document.validate_policy((ROOT / document.POLICY_PATH).read_bytes(), ROOT)
    paths = {*dependencies, *q.COMPONENT_PATHS, *q.PROOF_PATHS, *q.CURRENT_PROOF_PATHS,
        *contract.v2.QUALIFICATION_PATHS, *contract.v2.TRANSPORT_PATHS, *contract.v2.BOOTSTRAP_PATHS,
        *contract.HISTORICAL_PATHS, *contract.NEW_PATHS, *contract.FOLLOWON_PATHS,
        *contract.PROTOCOL_FIXTURES, *("backend/" + name for name in contract.PROOF_ROOTS),
        *worker, *document.CONTRACT_PATHS, document.POLICY_PATH}
    public_fonts = {"backend/evals/assets/courier_prime/" + name for name in (
        "CourierPrime-Regular.ttf", "CourierPrime-Bold.ttf", "CourierPrime-Italic.ttf",
        "CourierPrime-BoldItalic.ttf", "OFL.txt", "provenance.json")}
    assert {name for name in paths if name.startswith("backend/evals/")} == public_fonts
    assert not any(".." in Path(name).parts for name in paths)
    runner = tmp_path / "owned-current-source"
    _owned_source_clone(runner, q.git(ROOT, "rev-parse", "HEAD").decode().strip())
    for name in paths:
        target = runner / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(q._registry_snapshot(ROOT) if name == q.OPERATOR_REGISTRY_PATH
                           else (ROOT / name).read_bytes())
    registry_commit(runner)
    synthetic_release = q.git(runner, "rev-parse", "HEAD").decode().strip()
    assert not (runner / "backend/evals/unified_credit_v2").exists()
    assert (runner / q.OPERATOR_REGISTRY_PATH).read_bytes() == q._registry_snapshot(ROOT)
    assert q.dependency_hashes(runner) == dependencies and len(dependencies) == 466
    observed = q.runner_source(runner)
    fingerprint = contract.source_fingerprint(ROOT, release.prior_app_files(ROOT, contract.PRIOR_RELEASE))
    assert observed["release"] == synthetic_release
    assert observed["qualification_sha256"] == fingerprint
    assert observed["app_files"] == 290 and observed["app_sha256"] == q.APP_SHA256
    assert observed["runtime_dependencies_sha256"] == q.digest(dependencies)


def test_child_registry_git_reads_have_exact_finite_argument_shapes():
    head = "a" * 40
    commands = [
        ["merge-base", "--is-ancestor", q.REGISTRY_RUNNER_RELEASE, head],
        ["rev-list", "--parents", "-n", "1", head],
        ["log", "--format=%H", "--full-history", "--max-count=65",
         q.REGISTRY_RUNNER_RELEASE + ".." + head, "--", q.OPERATOR_REGISTRY_PATH],
    ]
    for command in commands:
        assert q._registry_git_read(command)
        assert not q._registry_git_read(command + ["--all"])
        assert not q._registry_git_read(command[:-1])
    for command in (["merge-base", "--is-ancestor", "b" * 40, head],
                    ["rev-list", "--all"], ["log", "--all"], ["reset", "--hard", head],
                    commands[2][:-1] + ["backend/app/main.py"], commands[0][:-1] + ["HEAD"],
                    commands[1][:-1] + ["HEAD"], commands[2][:3] + ["--max-count=9999"] + commands[2][4:],
                    commands[2][:3] + ["--ancestry-path"] + commands[2][3:]):
        assert not q._registry_git_read(command)


def test_current_observer_context_must_match_both_actual_roots(native_ports):
    p, raw, state, _, _ = native_ports
    state["observed"]["current_proof_sha256"] = "0" * 64
    with pytest.raises(ValueError, match="current_proof_context_changed"):
        q.verify_qualified_runner(p, raw, runner_root=ROOT, controller_ci_run_id=2)
    assert state["commands"] == []


def test_new_controller_context_cannot_reuse_stale_CI_head(native_ports):
    p, raw, state, _, _ = native_ports
    state["controller_head"] = "e" * 40  # Actual source advanced; returned CI stays at d.
    with pytest.raises(ValueError, match="controller_exact_head_CI_required"):
        q.verify_qualified_runner(p, raw, runner_root=ROOT, controller_ci_run_id=2)


def test_same_actual_head_and_CI_can_bind_both_independent_roles(native_ports):
    p, raw, state, _, _ = native_ports
    state["controller_head"] = state["controller_ci_head"] = state["observed"]["release"]
    out = q.verify_qualified_runner(p, raw, runner_root=ROOT, controller_ci_run_id=1)
    assert out["controller_head"] == out["runner_release"]
    assert out["fresh_source_CI_Build_verified"] and not out["processing_authorized"]


def _history_decoder_step():
    import yaml

    steps = yaml.safe_load((ROOT / ".github/workflows/document-refresh.yml").read_text())["jobs"]["maintenance"]["steps"]
    return next(step["run"] for step in steps if step.get("name") == "Normalize only the exact completed source-readback history")


@pytest.fixture(scope="module")
def frozen_history_decoder(tmp_path_factory):
    target = tmp_path_factory.mktemp("frozen-history-decoder") / "source"
    environment = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    environment.update(GIT_NO_LAZY_FETCH="1", GIT_NO_REPLACE_OBJECTS="1", GIT_CONFIG_NOSYSTEM="1",
        GIT_CONFIG_GLOBAL="/dev/null", GIT_TERMINAL_PROMPT="0")
    for args in (("clone", "--quiet", "--shared", "--no-checkout", str(ROOT), str(target)),
                 ("-C", str(target), "checkout", "--quiet", "--detach", "f5e9ca03383afac4c6cda17134bfc854c3ba8116")):
        subprocess.run(["git", "-c", "core.hooksPath=/dev/null", "-c", "core.fsmonitor=false", *args],
                       env=environment, check=True, capture_output=True, timeout=30)
    target.chmod(0o700)
    return target


def _validate_frozen_history_decoder(target, monkeypatch, *, current=ROOT):
    code = _history_decoder_step().split("<<'PY'\n", 1)[1].split("\nPY\n", 1)[0]
    with monkeypatch.context() as context:
        context.setattr(sys, "argv", ["owned-frozen-source-check", str(current), str(target)])
        exec(compile(code, "owned-frozen-history-source-check", "exec"), {"__name__": "__main__"})


def test_history_decoder_is_full_pinned_source_and_fixed_read_only_role(frozen_history_decoder, monkeypatch):
    step = _history_decoder_step()
    assert "umask 077" in step and "worktree add --detach" in step
    assert "--mode select-history" in step and step.count("--mode ") == 1
    assert 'GITHUB_SHA=' not in step and 'export GITHUB_' not in step
    assert '"$GITHUB_WORKSPACE/backend/.venv/bin/python" -I -B' in step
    _validate_frozen_history_decoder(frozen_history_decoder, monkeypatch)


@pytest.mark.parametrize("change", ["tracked_code", "ignored_import", "lock", "wrong_head"])
def test_frozen_history_decoder_refuses_drift_before_import(frozen_history_decoder, monkeypatch, tmp_path, change):
    target = frozen_history_decoder
    path = target / ("infra/gcp/document_source_readback_resume.py" if change == "tracked_code" else
                     "backend/__pycache__/injected.pyc" if change == "ignored_import" else "backend/uv.lock")
    original = path.read_bytes() if path.exists() else None
    head = q.git(target, "rev-parse", "HEAD").decode().strip()
    try:
        if change == "wrong_head":
            q.git(target, "checkout", "--quiet", "--detach", head + "^")
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes((original or b"") + b"\nOWNED-TOY-DRIFT\n")
        with pytest.raises((AssertionError, subprocess.CalledProcessError)):
            _validate_frozen_history_decoder(target, monkeypatch)
    finally:
        if change == "wrong_head":
            q.git(target, "checkout", "--quiet", "--detach", head)
        elif original is None:
            path.unlink()
            path.parent.rmdir()
        else:
            path.write_bytes(original)


_FROZEN_HISTORY_ROLE_CONTROL = r'''
import importlib.util, io, json, os, socket, subprocess, sys, zipfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

root, work, scenario = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3]
sys.path[:0] = [str(root), str(root / "backend"), str(root / "infra/gcp")]
counts = {"outbound": 0, "local_import_bind_denied": 0, "mutations": 0, "historical_build_reads": 0}
def denied(*args, **kwargs):
    counts["outbound"] += 1
    raise AssertionError("Owned control denied external IO")
def no_bind(*args, **kwargs):
    counts["local_import_bind_denied"] += 1
    raise OSError("Owned control denied import capability bind")
socket.socket.connect = socket.socket.connect_ex = socket.create_connection = socket.getaddrinfo = denied
socket.socket.bind = no_bind
import psycopg
from sqlalchemy.engine import Engine
psycopg.connect = Engine.connect = Engine.raw_connection = denied
from infra.gcp import document_source_readback_resume as resume
build, prior, source = resume.modules(root)
prior.build_module = lambda: build  # One owned fake native-read port for the real historical path.
profile = source.profile
for module in (resume, build, source, source.transition):
    assert Path(module.__file__).resolve().is_relative_to(root)
def mutation(*args, **kwargs):
    counts["mutations"] += 1
    raise AssertionError("Owned control reached forbidden mutation/current authority")
for name in ("claim", "resume", "trusted_context", "verify_operations"):
    setattr(resume, name, mutation)
for name in ("native_build", "source_definitions_candidate", "full_source_candidate", "finish_refresh"):
    setattr(build, name, mutation)
source.promote = source.transition.transition = prior.Clients = mutation
source.SourceProbe._acceptance = source.SourceProbe.verify_current_successor = mutation
profile.require_working_contract = mutation

current_image = resume.IMAGE if scenario == "expired_current" else build.REPOSITORY + "@sha256:" + "b" * 64
current_expiry = (datetime.now(UTC) - timedelta(days=2)).isoformat()
assert current_image and current_expiry  # Current state is deliberately unavailable to this historical role.
issued = (datetime.now(UTC) - timedelta(days=5)).replace(microsecond=0)
local = {"corpus": [{"scan": {"definitions": "123/" + issued.isoformat()}}] * 10,
    "docx_layout_corpus": [{}] * 8, "pdf_reading_order_corpus": [{}] * 8, "request_clamd_corpus": [{}] * 6}
local_raw = resume.canonical(local)
frozen = profile.committed(resume.ORIGINAL_SOURCE, root=root)
identity = {"version": 1, "kind": profile.KIND, "nonce": resume.NONCE,
    "build_id": resume.BUILD_ID, "build_resource": prior.PARENT + "/builds/" + resume.BUILD_ID,
    "builder_service_account": build.BUILDER, "source_bucket": build.PROJECT + "_cloudbuild",
    "source_object": build.FULL_SOURCE_PREFIX + resume.NONCE + "/source.tar.gz", "source_generation": "123",
    "source_archive_sha256": "a" * 64, "refresh_dockerfile_sha256": frozen.sources[frozen.dockerfile],
    "fixed_renderer_base": source.courier.IMAGE, "immutable_image": resume.IMAGE,
    "policy_sha256": frozen.policy, "source_sha256": frozen.sources, "source_commit": resume.ORIGINAL_SOURCE,
    "qualification_sha256": frozen.qualification_sha256, "local_native_proof_sha256": resume.sha(local_raw)}
binding = {"version": 4, "image": resume.IMAGE, "build_identity": identity}
probe = source.SourceProbe(identity, root=root, serving_read=True)
accepted = dict.fromkeys(prior.NativeAcceptance.__dataclass_fields__, "owned-historical-metadata-not-acceptance")
accepted.update(image=resume.IMAGE, build_id=resume.BUILD_ID, build_resource=identity["build_resource"],
    source_archive_sha256=identity["source_archive_sha256"], refresh_dockerfile_sha256=identity["refresh_dockerfile_sha256"],
    source_sha256=frozen.sources, policy_sha256=frozen.policy, renderer_version=source.courier.RENDERER,
    font_manifest_sha256=source.courier.FONT, contract_version=4, required_case_count=17,
    job_name=prior.PARENT + "/jobs/hirewiz-doc-six-" + resume.NONCE[:12],
    probe_sha256=resume.sha(probe._rendered(resume.IMAGE).encode()),
    definition_expires_at=(issued + timedelta(days=3)).isoformat())
complete = {"status": "document_refresh_serving", "image": resume.IMAGE, "prior_image": source.courier.IMAGE,
    "native_acceptance_sha256": resume.sha(resume.canonical(accepted)), "all_three_caller_pins_verified": True,
    "admission_restored": True, "synchronous_callers_drained": False, "current_native_proof_retained": True,
    "automatic_replay": False}
result = {**complete, "source_commit": resume.ORIGINAL_SOURCE, "renderer_contract_version": 4,
    "required_case_count": 17, "definition_expires_at": accepted["definition_expires_at"],
    "prior_evidence_retained": True, "prior_jobs_retained": True}
history = work / "history"; history.mkdir(mode=0o700)
original = history / str(resume.ORIGINAL_ARTIFACT); original.mkdir(mode=0o700)
target = history / "13007164250"; target.mkdir(mode=0o700)
def write(base, name, value):
    path = base / name
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.write_bytes(value if isinstance(value, bytes) else resume.canonical(value)); path.chmod(0o600)
original_files = {"marker.json": b'{"owned_original":true}', "native-proof/binding.json": resume.canonical(binding),
                  "local-native-proof.json": local_raw}
resume.FILES = {name: resume.sha(raw) for name, raw in original_files.items()}
for name, raw in original_files.items(): write(original, name, raw); write(target, name, raw)
context = {"run": 41119586258, "attempt": 1, "head": "f5e9ca03383afac4c6cda17134bfc854c3ba8116",
    "path": resume.WORKFLOW, "operator_sha256": resume.operator_contract(root, "HEAD", working=True)}
native = {}
for mode, short, suffix in (("inventory", "1f2186a3d9a0", "7vc5n"), ("corpus", "0e31d2e468f2", "cshdp")):
    job = prior.PARENT + "/jobs/hirewiz-doc-six-" + short
    native[mode] = {"job": job, "job_uid": resume.JOBS[mode][0], "execution": job + "/executions/" + job.rsplit("/", 1)[1] + "-" + suffix,
                    "execution_uid": resume.JOBS[mode][1]}
preimage = {"owned_historical": True}; body = resume.claim_body(identity, native, preimage, context)
ack = {"name": resume.CLAIM_OBJECT, "bucket": build.PROJECT + "_cloudbuild", "generation": "123"}
manifest = {"version": 1, "kind": "completed_source_readback_v1", "original_run": resume.ORIGINAL_RUN,
    "original_artifact": resume.ORIGINAL_ARTIFACT, "original_zip_sha256": resume.ORIGINAL_ZIP_SHA,
    "original_files": resume.FILES, "candidate_nonce": resume.NONCE,
    "build_identity_sha256": resume.sha(resume.canonical(identity)), "continuation": context,
    "claim_body": body, "claim_acknowledgment": ack, "result_sha256": resume.sha(resume.canonical(result))}
for name, value in {resume.MANIFEST_FILE: manifest, "result.json": result, "continuation-preimage.json": preimage,
    "continuation-claim.started.json": {"body_sha256": resume.sha(resume.canonical(body)), "automatic_replay": False},
    "continuation-claim.acknowledged.json": ack, "native-proof/acceptance.json": accepted,
    "native-proof/transition.started.json": {"build_identity_sha256": resume.sha(resume.canonical(identity)),
        "transition_journal": str(target / "transition"), "automatic_replay": False,
        "unknown_outcome": "read_only_reconcile_exact_transition_only"},
    "transition/native-acceptance.json": accepted, "transition/complete.json": complete}.items(): write(target, name, value)
failed = history / "13034966606"; failed.mkdir(mode=0o700)
failed_files = {name: b'{"owned_failed_build":true}' for name in
    ("build-started.json", "source-upload-acknowledged.json", "build-create-started.json", "build-acknowledged.json")}
for name, raw in failed_files.items(): write(failed, name, raw)
stream = io.BytesIO()
with zipfile.ZipFile(stream, "w") as z:
    for path in target.rglob("*.json"): z.writestr(str(path.relative_to(target)), path.read_bytes())
archive = stream.getvalue()
run = {"id": context["run"], "head_sha": context["head"], "run_attempt": 1, "path": resume.WORKFLOW,
    "head_branch": "main", "status": "completed", "conclusion": "success", "event": "workflow_dispatch",
    "repository": {"full_name": resume.REPOSITORY}}
artifact = {"id": int(target.name), "name": "document-refresh-full-source-refresh-41119586258-1", "expired": False,
    "digest": "sha256:" + resume.sha(archive), "workflow_run": {"id": context["run"], "head_sha": context["head"], "head_branch": "main"}}
def github(path, *, binary=False):
    if path == "actions/artifacts/" + str(resume.ORIGINAL_ARTIFACT):
        return {"id": resume.ORIGINAL_ARTIFACT, "name": resume.ORIGINAL_NAME, "digest": "sha256:" + resume.ORIGINAL_ZIP_SHA,
            "expired": False, "size_in_bytes": 1, "workflow_run": {"id": resume.ORIGINAL_RUN,
                "head_sha": resume.ORIGINAL_SOURCE, "head_branch": "main"}}
    if path == "actions/runs/" + str(resume.ORIGINAL_RUN):
        return {**run, "id": resume.ORIGINAL_RUN, "head_sha": resume.ORIGINAL_SOURCE, "conclusion": "failure"}
    if path.endswith("/artifacts?per_page=100"): return {"artifacts": [artifact]}
    if path.endswith("/zip"): assert binary; return archive
    assert path == "actions/runs/41119586258"; return run
resume.github = github
wire = []
class Http:
    def request(self, uri, method="GET", body=None, **kwargs):
        assert method == "GET" and body is None
        assert uri.startswith(("https://storage.googleapis.com/", "https://cloudbuild.googleapis.com/"))
        wire.append((method, uri))
        return SimpleNamespace(status=200), resume.canonical(globals()["body"])
    def close(self): pass
build.native_http = Http
def historical_build(value):
    assert value == identity
    counts["historical_build_reads"] += 1
    Http().request("https://cloudbuild.googleapis.com/v1/" + identity["build_resource"])
    Http().request("https://storage.googleapis.com/download/storage/v1/b/owned/o/source?generation=123")
    return identity
build.verify_native_build = historical_build
def historical_local(value, contract, sources, *, now, **kwargs):
    assert value == local and sources == frozen.sources and now == issued and kwargs["source_revision"] is True
    return {}
build.proof_contract = historical_local
before_sha = os.environ.get("GITHUB_SHA")
entry_spec = importlib.util.spec_from_file_location("owned_frozen_history_cli", root / "backend/scripts/resume_completed_document_source.py")
entry = importlib.util.module_from_spec(entry_spec); entry_spec.loader.exec_module(entry)
sys.argv = [entry.__file__, "--mode", "select-history", "--history-directory", str(history), "--output-directory", str(work / "normalized")]
assert entry.main() == 0
assert os.environ.get("GITHUB_SHA") == before_sha
normalized = work / "normalized"
assert not (normalized / original.name).exists() and (normalized / target.name / resume.MANIFEST_FILE).is_file()
assert all((original / name).read_bytes() == raw for name, raw in original_files.items())
assert all((normalized / failed.name / name).read_bytes() == raw and (failed / name).read_bytes() == raw for name, raw in failed_files.items())
assert len(list(history.iterdir())) == 3 and len(list(normalized.iterdir())) == 2
assert counts["mutations"] == counts["outbound"] == 0 and counts["historical_build_reads"] == 1
assert wire and all(method == "GET" for method, _ in wire)
print(json.dumps({"state": "owned_read_only_history_control_passed", "counts": counts,
    "current_worker_unused": True, "originals_preserved": True, "native_approval": False}))
'''


@pytest.mark.parametrize("scenario", ["expired_current", "changed_definition_descendant"])
def test_frozen_selector_keeps_historical_authority_separate_from_current_worker(frozen_history_decoder, tmp_path, scenario):
    result = subprocess.run([sys.executable, "-I", "-B", "-c", _FROZEN_HISTORY_ROLE_CONTROL,
        str(frozen_history_decoder), str(tmp_path), scenario], stdin=subprocess.DEVNULL,
        capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr[-3000:]
    observed = json.loads(result.stdout.splitlines()[-1])
    assert observed["current_worker_unused"] and observed["originals_preserved"] and not observed["native_approval"]
    assert observed["counts"]["outbound"] == observed["counts"]["mutations"] == 0


def test_registered_c6_tuple_is_append_only_actual_data_not_runner_source():
    from infra.gcp import document_application_context as applications

    value = q._registry_value((ROOT / q.OPERATOR_REGISTRY_PATH).read_bytes())
    baseline = q._registry_value(q._registry_snapshot(ROOT))
    assert value["sources"][:5] == baseline["sources"] and 6 <= len(value["sources"]) <= 8
    record = value["sources"][5]
    assert record == {"release": "c6a03af0636c7e9815159f807c9b126336d03233",
        "image_digest": "sha256:2ebc717174904f6becc3309aedc5f487a47e31b7a5c60e4fc34b3494f6f8acb1",
        "build_id": "32bc5596-a64b-4cf4-9cc0-311389d46f9d", "ci_run_id": 41500782361}
    pin = applications.Source(**record)
    pin.validate()
    q.git(ROOT, "merge-base", "--is-ancestor", record["release"], "HEAD")
    assert q.operator_data_hashes(ROOT)[q.OPERATOR_REGISTRY_PATH] != q.REGISTRY_RUNNER_SHA256


@pytest.fixture(params=q.CURRENT_ADDED_EXECUTION_PATHS)
def current_added_owned_tree(tmp_path, monkeypatch, request):
    """Four tiny owned Git files; no actual checkout, provider or corpus reads."""
    def denied(*args, **kwargs):
        raise AssertionError("Owned execution-manifest tests forbid network I/O")

    monkeypatch.setattr(socket, "create_connection", denied)
    monkeypatch.setattr(socket, "getaddrinfo", denied)
    monkeypatch.setattr(socket.socket, "connect", denied)
    monkeypatch.setattr(socket.socket, "connect_ex", denied)
    root = tmp_path.resolve()
    base = "backend/app/owned_baseline.py"
    added = request.param
    monkeypatch.setattr(q, "CURRENT_ADDED_EXECUTION_PATHS", (added,))
    registry = json.dumps({"version": 1, "sources": [synthetic_registry_row()]}).encode()
    q.git(root, "init", "-q")

    def write(name, raw):
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)

    def commit():
        q.git(root, "add", "--all")
        q.git(root, "-c", "core.hooksPath=/dev/null", "-c", "commit.gpgsign=false",
            "-c", "user.name=Owned source control", "-c", "user.email=owned@example.invalid",
            "commit", "-q", "-m", "Owned synthetic runtime manifest")
        return q.git(root, "rev-parse", "HEAD").decode().strip()

    write("Dockerfile", b"FROM owned-invalid.example/no-image\n")
    write(base, b"VALUE = 'historical'\n")
    write(q.OPERATOR_REGISTRY_PATH, registry)
    historical = commit()
    monkeypatch.setattr(q, "ROOT", root)
    monkeypatch.setattr(q, "HISTORICAL_RELEASE", historical)
    monkeypatch.setattr(q, "REGISTRY_RUNNER_RELEASE", historical)
    monkeypatch.setattr(q, "REGISTRY_RUNNER_SHA256", hashlib.sha256(registry).hexdigest())
    monkeypatch.setattr(q, "RUNNER_PATHS", ("Dockerfile",))
    monkeypatch.setattr(q, "CURRENT_EXECUTION_PATHS", (base,))
    write(base, b"VALUE = 'current'\n")
    write(added, b"HELPER = 'owned-current-runtime'\n")
    commit()
    return SimpleNamespace(root=root, base=base, added=added, historical=historical, write=write, commit=commit)


@pytest.mark.parametrize("reader", ["dependency_hashes", "controller_dependency_hashes"])
def test_current_added_owned_runtime_is_committed_and_changes_runtime_fingerprint(current_added_owned_tree, reader):
    owned = current_added_owned_tree
    read = getattr(q, reader)
    before = read(owned.root)
    successors = q.current_execution_hashes(owned.root)
    assert set(before) == {"Dockerfile", owned.base, owned.added, q.OPERATOR_REGISTRY_PATH}
    assert set(successors) == {owned.base, owned.added}
    assert before[owned.added] == hashlib.sha256((owned.root / owned.added).read_bytes()).hexdigest()
    assert owned.added not in {*q.COMPONENT_PATHS, *q.PROOF_PATHS, *q.CURRENT_PROOF_PATHS}
    owned.write(owned.added, b"HELPER = 'different-committed-runtime'\n")
    owned.commit()
    after = read(owned.root)
    assert {name for name in before if before[name] != after[name]} == {owned.added}
    assert q.digest(before) != q.digest(after)


@pytest.mark.parametrize("reader", ["dependency_hashes", "controller_dependency_hashes"])
@pytest.mark.parametrize("fault,code", [
    ("uncommitted", "uncommitted_current_execution_changed"),
    ("missing", "current_execution_path"), ("worktree-symlink", "current_execution_path"),
    ("committed-symlink", "current_execution_type"), ("gitlink", "current_execution_type"),
    ("untracked", "current_execution_not_committed"), ("empty", "current_execution_path"),
    ("oversized", "current_execution_path"),
])
def test_current_added_owned_requires_regular_committed_bounded_bytes(current_added_owned_tree, reader, fault, code):
    owned = current_added_owned_tree
    path = owned.root / owned.added
    if fault == "uncommitted":
        path.write_bytes(path.read_bytes() + b"# changed\n")
    elif fault == "missing":
        path.unlink()
    elif fault in {"worktree-symlink", "committed-symlink"}:
        owned.write("owned-target.txt", b"Never import this target\n")
        path.unlink()
        path.symlink_to(owned.root / "owned-target.txt")
        if fault == "committed-symlink":
            owned.commit()
    elif fault == "gitlink":
        head = q.git(owned.root, "rev-parse", "HEAD").decode().strip()
        q.git(owned.root, "update-index", "--add", "--cacheinfo", "160000," + head + "," + owned.added)
        q.git(owned.root, "-c", "core.hooksPath=/dev/null", "-c", "commit.gpgsign=false",
            "-c", "user.name=Owned source control", "-c", "user.email=owned@example.invalid",
            "commit", "-q", "-m", "Owned non-blob negative")
    elif fault == "untracked":
        q.git(owned.root, "rm", "--cached", "--", owned.added)
        q.git(owned.root, "-c", "core.hooksPath=/dev/null", "-c", "commit.gpgsign=false",
            "-c", "user.name=Owned source control", "-c", "user.email=owned@example.invalid",
            "commit", "-q", "-m", "Owned untracked negative")
    else:
        path.write_bytes(b"" if fault == "empty" else b"x" * (512 * 1024 + 1))
        owned.commit()
    with pytest.raises(ValueError, match=code):
        getattr(q, reader)(owned.root)


@pytest.mark.parametrize("reader", ["dependency_hashes", "controller_dependency_hashes"])
@pytest.mark.parametrize("name", ["backend/app/undeclared.py", "infra/gcp/undeclared.json"])
def test_current_added_owned_does_not_exempt_any_other_new_execution_path(current_added_owned_tree, reader, name):
    owned = current_added_owned_tree
    owned.write(name, b"{}\n")
    owned.commit()
    with pytest.raises(ValueError, match="new_execution_dependency"):
        getattr(q, reader)(owned.root)


@pytest.mark.parametrize("overlap", ["historical", "current", "component", "proof", "current-proof", "registry"])
def test_current_added_owned_manifest_is_absent_historically_and_disjoint(current_added_owned_tree, monkeypatch, overlap):
    owned = current_added_owned_tree
    if overlap == "historical":
        monkeypatch.setattr(q, "HISTORICAL_RELEASE", q.git(owned.root, "rev-parse", "HEAD").decode().strip())
        code = "current_added_execution_historical"
    else:
        name = {"current": owned.base, "component": q.COMPONENT_PATHS[0], "proof": q.PROOF_PATHS[0],
                "current-proof": q.CURRENT_PROOF_PATHS[0], "registry": q.OPERATOR_REGISTRY_PATH}[overlap]
        monkeypatch.setattr(q, "CURRENT_ADDED_EXECUTION_PATHS", (name,))
        code = "current_execution_manifest" if overlap == "current" else "current_execution_manifest_overlap"
    for read in (q.dependency_hashes, q.controller_dependency_hashes):
        with pytest.raises(ValueError, match=code):
            read(owned.root)


def test_current_added_owned_old_runner_without_helper_refuses_before_validator_import(current_added_owned_tree, monkeypatch):
    owned = current_added_owned_tree
    q.git(owned.root, "reset", "--hard", owned.historical)
    assert not (owned.root / owned.added).exists()
    monkeypatch.setattr(q, "_source_modules", lambda: pytest.fail("Old runner reached current validators"))
    for read in (q.dependency_hashes, q.controller_dependency_hashes, q._runner_source):
        with pytest.raises(ValueError, match="current_execution_not_committed"):
            read(owned.root)


def test_current_added_owned_two_exact_additions_are_runtime_bytes(current_added_owned_tree, monkeypatch):
    owned = current_added_owned_tree
    additions = ("backend/app/domains/employer/source_revalidation.py", "backend/scripts/schema26_app_successor.py")
    for name in additions:
        if name != owned.added:
            owned.write(name, b"SECOND_OWNED_RUNTIME = True\n")
    owned.commit()
    monkeypatch.setattr(q, "CURRENT_ADDED_EXECUTION_PATHS", additions)
    for reader in (q.dependency_hashes, q.controller_dependency_hashes):
        actual = reader(owned.root)
        assert set(actual) == {"Dockerfile", owned.base, q.OPERATOR_REGISTRY_PATH, *additions}
        for name in additions:
            assert actual[name] == hashlib.sha256((owned.root / name).read_bytes()).hexdigest()
