"""Synthetic source/CI/Build ports; no native qualification or funding claim."""
from __future__ import annotations

import copy
import hashlib
import json
import os
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
    assert value["operator_data_inputs"] == q.operator_data_hashes(ROOT)
    assert value["runner_snapshot_inputs"] == {q.OPERATOR_REGISTRY_PATH: {
        "release": q.REGISTRY_RUNNER_RELEASE, "sha256": q.REGISTRY_RUNNER_SHA256}}
    assert set(value["proof_inputs"]) == set(q.PROOF_PATHS) and len(q.PROOF_PATHS) == 3
    assert value["absent_package_ancestors"] == list(q.ABSENT_ANCESTORS)
    assert set(value["current_proof_inputs"]) == set(q.CURRENT_PROOF_PATHS)
    assert len(value["execution_dependencies"]) == 464 and len(q.CURRENT_PROOF_PATHS) == 1
    assert q.CURRENT_EXECUTION_PATHS == (
        "backend/app/domains/credits/canary.py", "backend/app/domains/recovery/browser_pairing_production.py",
        "backend/scripts/document_refresh.py", "backend/scripts/schema25_app_release_contract_v3.py",
        "infra/gcp/document_consumed_native.py", "infra/gcp/document_refresh_recovery.py",
        "infra/gcp/document_refresh_transition.py", "infra/gcp/document_source_candidate.py",
        "infra/gcp/document_source_release.py", "infra/gcp/schema26_app_rolling_v3.py",
    )
    assert set(value["current_execution_inputs"]) == set(q.CURRENT_EXECUTION_PATHS)
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
        app_files=289, entry_sha256=q.ENTRY_SHA256, runtime_dependencies_sha256="b" * 64,
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


@pytest.mark.parametrize("name", q.CURRENT_EXECUTION_PATHS)
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


@pytest.mark.parametrize("name", q.CURRENT_EXECUTION_PATHS)
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
    assert len(before) == len(after) == 464
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
    assert len(strict) == 464 and before["runtime_dependencies_sha256"] == after["runtime_dependencies_sha256"]
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


def test_source_child_keeps_actual_application_qualification_and_runner_bytes(tmp_path):
    # A local shared-object clone supplies actual committed source, not a mocked
    # qualification result. The existing isolated child refuses executing or
    # importing any file in this clone and denies provider/credential I/O.
    runner = tmp_path / "runner"
    current = q._registry_value((ROOT / q.OPERATOR_REGISTRY_PATH).read_bytes())["sources"]
    baseline = q._registry_value(q._registry_snapshot(ROOT))["sources"]
    # Before registration, inspect this actual source HEAD. Once actual Build
    # tuples exist, inspect the latest registered app source, not the later
    # operator-only HEAD whose changed registry must fail strict runner reads.
    application_release = (current[-1]["release"] if len(current) > len(baseline)
                           else q.git(ROOT, "rev-parse", "HEAD").decode().strip())
    q.git(ROOT, "cat-file", "-e", application_release + "^{commit}")
    q.git(ROOT, "merge-base", "--is-ancestor", application_release,
          q.git(ROOT, "rev-parse", "HEAD").decode().strip())
    environment = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    environment.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL="/dev/null", GIT_TERMINAL_PROMPT="0")
    for args in (("clone", "--quiet", "--shared", "--no-checkout", str(ROOT), str(runner)),
                 ("-C", str(runner), "checkout", "--quiet", "--detach", application_release)):
        subprocess.run(["git", "-c", "core.hooksPath=/dev/null", *args], env=environment,
                       check=True, capture_output=True, timeout=30)
    observed = q.runner_source(runner)
    _, contract, release = q._source_modules()
    fingerprint = contract.source_fingerprint(ROOT, release.prior_app_files(ROOT, contract.PRIOR_RELEASE))
    assert observed["release"] == application_release
    assert observed["qualification_sha256"] == fingerprint
    assert observed["app_files"] == 289 and observed["app_sha256"] == q.APP_SHA256
    assert observed["runtime_dependencies_sha256"] == q.digest(q.controller_dependency_hashes(ROOT))


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
