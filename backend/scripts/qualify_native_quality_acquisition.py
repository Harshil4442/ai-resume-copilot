"""Finite controller/source verifier; never a processing or funding authority."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, cast

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
HISTORICAL_RELEASE = "7be8cac2a332a5ad19366402013d5f385f97105b"
HISTORICAL_FINGERPRINT = "739d9751132a9864bda0f3fda89b6f03a095f3c2c195c3f9ad9696fb5943ce86"
APP_SHA256 = "9eca9ce7c267fab5789b292a4937bf7eab7c12cf37140209223702636edd677e"
ENTRY = "backend/scripts/native_acquisition_job.py"
ENTRY_SHA256 = "8eef532ef4d4978e9dda000e47ee694b43b55e16a168f93f9c7da724ea9b8740"
COMPONENT_PATHS = (
    "infra/gcp/native_quality_acquisition_isolation.py",
    "backend/scripts/qualify_native_quality_acquisition.py",
    "backend/tests/test_native_quality_acquisition_isolation.py",
    "backend/tests/test_qualify_native_quality_acquisition.py",
    "docs/runbooks/NATIVE_QUALITY_ISOLATION_V1.md",
)
SUITES = ("tests/test_native_quality_acquisition_isolation.py", "tests/test_qualify_native_quality_acquisition.py")
RUNNER_PATHS = (
    "Dockerfile", "backend/pyproject.toml", "backend/uv.lock", ".github/workflows/ci.yml",
    "backend/alembic.ini", "infra/gcp/document_worker.py", "infra/gcp/native_tex_worker.py",
    "infra/gcp/document_network.py", "infra/native-tex/policy.json", ENTRY,
    "backend/scripts/native_acquisition_channel.py", "backend/app/domains/analysis/native_input_acquisition.py",
    "backend/app/services/document_ingestion.py", "backend/app/services/native_tex_transport.py",
)

PROOF_PATHS = ("backend/tests/conftest.py", "backend/tests/expense_policy_fixtures.py",
               "backend/tests/model_cost_fixtures.py")
CURRENT_PROOF_PATHS = ("backend/scripts/qualify_schema26_compatibility.py",)
# Only the explicitly reviewed runtime and prospective V3 controller successors
# may differ from the failed historical runner. Other execution inputs stay fixed.
CURRENT_EXECUTION_PATHS = (
    "backend/app/domains/credits/canary.py",
    "backend/app/domains/recovery/browser_pairing_production.py",
    "backend/scripts/document_refresh.py",
    "backend/scripts/schema25_app_release_contract_v3.py",
    "infra/gcp/document_consumed_native.py",
    "infra/gcp/document_refresh_recovery.py",
    "infra/gcp/document_refresh_transition.py",
    "infra/gcp/document_source_candidate.py",
    "infra/gcp/document_source_release.py",
    "infra/gcp/schema26_app_rolling_v3.py",
    "infra/gcp/schema26_lifecycle_join.py",
    "infra/gcp/schema26_maintenance_guard.py",
    "infra/gcp/v3_readonly_adapter.py",
)
# Maintenance reads the current operator registry. The qualified runner retains
# the exact registry bytes shipped at this immutable app source; neither source
# nor provider acceptance is inferred from an operator's appended row.
OPERATOR_REGISTRY_PATH = "infra/document-processing/reviewed-application-builds.json"
REGISTRY_RUNNER_RELEASE = "f361ac16aa17a53c640740a75c627421a9e758c9"
REGISTRY_RUNNER_SHA256 = "f99d4294c232de5e09769e3582abf1cd4ba245bb65fa4faebd027ff16d7f16a5"
ABSENT_ANCESTORS = ("backend/__init__.py", "backend/tests/__init__.py", "backend/scripts/__init__.py",
                    "infra/__init__.py", "infra/gcp/__init__.py")


def need(value, code):
    if not value:
        raise ValueError("native_quality_source_" + code)


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def git(root, *args):
    environment = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    environment.update(GIT_NO_LAZY_FETCH="1", GIT_NO_REPLACE_OBJECTS="1", GIT_TERMINAL_PROMPT="0",
                       GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL="/dev/null")
    result = subprocess.run(["/usr/bin/git", "-c", "core.fsmonitor=false", "-C", str(root), *args],
        capture_output=True, check=False, timeout=10, env=environment, stdin=subprocess.DEVNULL)
    need(result.returncode == 0 and len(result.stdout) <= 16 * 1024**2, "git_unavailable")
    return result.stdout


def component_hashes(root: Path) -> dict[str, str]:
    entries = {}
    for name in COMPONENT_PATHS:
        p = root / name
        need(p.is_file() and not p.is_symlink() and p.resolve() == p
             and 0 < p.stat().st_size <= 512 * 1024, "component_path")
        entries[name] = hashlib.sha256(p.read_bytes()).hexdigest()
    return entries


def ancestor_fence(root: Path) -> None:
    need(all(not (root / name).exists() and not (root / name).is_symlink()
             for name in ABSENT_ANCESTORS), "undeclared_package_ancestor")


def proof_hashes(root: Path) -> dict[str, str]:
    entries = {}
    for name in PROOF_PATHS:
        path = root / name
        need(path.is_file() and path.resolve() == path and not path.is_symlink(), "proof_input_path")
        raw = path.read_bytes()
        need(raw == git(root, "show", HISTORICAL_RELEASE + ":" + name), "historical_proof_input_changed")
        entries[name] = hashlib.sha256(raw).hexdigest()
    return entries


def current_proof_hashes(root: Path) -> dict[str, str]:
    """Current CI observer is source-bound data, never acquisition runtime code."""
    entries = {}
    for name in CURRENT_PROOF_PATHS:
        path = root / name
        need(path.is_file() and path.resolve() == path and not path.is_symlink()
             and 0 < path.stat().st_size <= 512 * 1024, "current_proof_path")
        raw = path.read_bytes()
        need(raw == git(root, "show", "HEAD:" + name), "uncommitted_current_proof_changed")
        entries[name] = hashlib.sha256(raw).hexdigest()
    return entries


def current_execution_hashes(root: Path) -> dict[str, str]:
    """Finite successor bytes bound to this checkout's exact committed HEAD."""
    head = git(root, "rev-parse", "HEAD").decode().strip()
    need(len(head) == 40 and all(c in "0123456789abcdef" for c in head), "current_execution_head")
    entries = {}
    for name in CURRENT_EXECUTION_PATHS:
        path = root / name
        need(path.is_file() and path.resolve() == path and not path.is_symlink()
             and 0 < path.stat().st_size <= 512 * 1024, "current_execution_path")
        raw = path.read_bytes()
        need(raw == git(root, "show", head + ":" + name), "uncommitted_current_execution_changed")
        entries[name] = hashlib.sha256(raw).hexdigest()
    need(git(root, "rev-parse", "HEAD").decode().strip() == head, "current_execution_head_changed")
    return entries


def dependency_hashes(root: Path) -> dict[str, str]:
    """Historical inventory plus finite HEAD-bound reviewed successors."""
    ancestor_fence(root)
    objects = {}
    for row in git(root, "ls-tree", "-r", "-z", "--full-tree", HISTORICAL_RELEASE).split(b"\0"):
        if row:
            metadata, name = row.split(b"\t", 1)
            mode, kind, object_id = metadata.split()
            objects[name.decode()] = (mode, kind, object_id)
    names = set(objects)
    paths = sorted({*RUNNER_PATHS, *(name for name in names if
        name.startswith(("backend/app/", "backend/scripts/", "backend/alembic/", "infra/gcp/",
                         "infra/document-processing/", "infra/native-tex/"))
        and Path(name).suffix in {".py", ".json"})} - set(CURRENT_PROOF_PATHS))
    successors = current_execution_hashes(root)
    need(set(successors) <= set(paths), "current_execution_not_historical")
    current = set(git(root, "ls-tree", "-r", "--name-only", "HEAD").decode().splitlines())
    code_roots = ("backend/app/", "backend/scripts/", "backend/alembic/", "infra/gcp/",
                  "infra/document-processing/", "infra/native-tex/")
    need(not {name for name in current - set(names) - set(COMPONENT_PATHS)
              if name.startswith(code_roots) and Path(name).suffix in {".py", ".json"}}, "new_execution_dependency")
    entries = {}
    for name in paths:
        path = root / name
        need(path.is_file() and path.resolve() == path and not path.is_symlink(), "dependency_path")
        raw = path.read_bytes()
        mode, kind, expected = objects[name]
        need(mode in {b"100644", b"100755"} and kind == b"blob", "historical_dependency_type")
        if name in successors:
            need(hashlib.sha256(raw).hexdigest() == successors[name], "current_execution_changed_during_read")
        else:
            need(hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw,
                             usedforsecurity=False).hexdigest().encode() == expected,
                 "historical_execution_dependency_changed")
        entries[name] = hashlib.sha256(raw).hexdigest()
    return entries


def _registry_snapshot(root: Path) -> bytes:
    raw = git(root, "show", REGISTRY_RUNNER_RELEASE + ":" + OPERATOR_REGISTRY_PATH)
    need(hashlib.sha256(raw).hexdigest() == REGISTRY_RUNNER_SHA256, "registry_runner_snapshot_changed")
    return raw


def _registry_value(raw: bytes) -> dict:
    def pairs(rows):
        need(len({name for name, _ in rows}) == len(rows), "operator_registry_duplicate_key")
        return dict(rows)
    need(0 < len(raw) <= 16384, "operator_registry_size")
    value = json.loads(raw, object_pairs_hook=pairs)
    need(type(value) is dict and set(value) == {"version", "sources"}
         and type(value["version"]) is int and value["version"] == 1
         and type(value["sources"]) is list and 1 <= len(value["sources"]) <= 8,
         "operator_registry_shape")
    for row in value["sources"]:
        need(type(row) is dict and set(row) == {"release", "image_digest", "build_id", "ci_run_id"}
             and type(row["release"]) is str and re.fullmatch(r"[a-f0-9]{40}", row["release"])
             and type(row["image_digest"]) is str and re.fullmatch(r"sha256:[a-f0-9]{64}", row["image_digest"])
             and type(row["build_id"]) is str
             and re.fullmatch(r"[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}", row["build_id"])
             and type(row["ci_run_id"]) is int and row["ci_run_id"] > 0, "operator_registry_record")
    need(len({row["release"] for row in value["sources"]}) == len(value["sources"]),
         "operator_registry_duplicate_release")
    return value


def operator_data_hashes(root: Path) -> dict[str, str]:
    """Committed append-only operator data, never native CI/Build authority."""
    snapshot = _registry_value(_registry_snapshot(root))
    head = git(root, "rev-parse", "HEAD").decode().strip()
    need(re.fullmatch(r"[a-f0-9]{40}", head), "operator_registry_head")
    path = root / OPERATOR_REGISTRY_PATH
    need(path.is_file() and not path.is_symlink() and path.resolve() == path, "operator_registry_path")
    raw = path.read_bytes()
    need(raw == git(root, "show", head + ":" + OPERATOR_REGISTRY_PATH), "uncommitted_operator_registry_changed")
    value = _registry_value(raw)
    prior = snapshot["sources"]
    need(value["sources"][:len(prior)] == prior, "operator_registry_not_append_only")
    git(root, "merge-base", "--is-ancestor", REGISTRY_RUNNER_RELEASE, head)
    changed = git(root, "log", "--format=%H", "--full-history", "--max-count=65",
                  REGISTRY_RUNNER_RELEASE + ".." + head, "--", OPERATOR_REGISTRY_PATH).decode().splitlines()
    need(len(changed) <= 64, "operator_registry_history_bound")
    for commit in changed:
        rows = _registry_value(git(root, "show", commit + ":" + OPERATOR_REGISTRY_PATH))["sources"]
        parents = git(root, "rev-list", "--parents", "-n", "1", commit).decode().split()[1:]
        need(parents, "operator_registry_parent_required")
        for parent in parents:
            previous = _registry_value(git(root, "show", parent + ":" + OPERATOR_REGISTRY_PATH))["sources"]
            need(rows[:len(previous)] == previous, "operator_registry_history_not_append_only")
    need(git(root, "rev-parse", "HEAD").decode().strip() == head, "operator_registry_head_changed")
    return {OPERATOR_REGISTRY_PATH: hashlib.sha256(raw).hexdigest()}


def controller_dependency_hashes(root: Path) -> dict[str, str]:
    """463 actual execution inputs plus one explicit immutable runner snapshot.

    Actual current registry bytes are separately bound by operator_data_hashes.
    The actual runner always uses unchanged dependency_hashes, which refuses an
    appended registry. No other dependency receives this data-context split.
    """
    operator = operator_data_hashes(root)
    snapshot = _registry_snapshot(root)
    ancestor_fence(root)
    objects = {}
    for row in git(root, "ls-tree", "-r", "-z", "--full-tree", HISTORICAL_RELEASE).split(b"\0"):
        if row:
            metadata, name = row.split(b"\t", 1)
            mode, kind, object_id = metadata.split()
            objects[name.decode()] = (mode, kind, object_id)
    names = set(objects)
    paths = sorted({*RUNNER_PATHS, *(name for name in names if
        name.startswith(("backend/app/", "backend/scripts/", "backend/alembic/", "infra/gcp/",
                         "infra/document-processing/", "infra/native-tex/"))
        and Path(name).suffix in {".py", ".json"})} - set(CURRENT_PROOF_PATHS))
    need(OPERATOR_REGISTRY_PATH in paths, "operator_registry_not_in_runner_snapshot")
    successors = current_execution_hashes(root)
    need(set(successors) <= set(paths) and OPERATOR_REGISTRY_PATH not in successors,
         "current_execution_not_historical")
    current = set(git(root, "ls-tree", "-r", "--name-only", "HEAD").decode().splitlines())
    code_roots = ("backend/app/", "backend/scripts/", "backend/alembic/", "infra/gcp/",
                  "infra/document-processing/", "infra/native-tex/")
    need(not {name for name in current - set(names) - set(COMPONENT_PATHS)
              if name.startswith(code_roots) and Path(name).suffix in {".py", ".json"}}, "new_execution_dependency")
    entries = {}
    for name in paths:
        path = root / name
        need(path.is_file() and path.resolve() == path and not path.is_symlink(), "dependency_path")
        raw = path.read_bytes()
        mode, kind, expected = objects[name]
        need(mode in {b"100644", b"100755"} and kind == b"blob", "historical_dependency_type")
        if name == OPERATOR_REGISTRY_PATH:
            need(hashlib.sha256(raw).hexdigest() == operator[name], "operator_registry_changed_during_read")
            raw = snapshot
        if name in successors:
            need(hashlib.sha256(raw).hexdigest() == successors[name], "current_execution_changed_during_read")
        else:
            need(hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw,
                             usedforsecurity=False).hexdigest().encode() == expected,
                 "historical_execution_dependency_changed")
        entries[name] = hashlib.sha256(raw).hexdigest()
    need(operator_data_hashes(root) == operator, "operator_registry_changed_during_identity")
    return entries


def source_identity(root: Path) -> dict:
    need(root.is_absolute() and root.resolve() == root, "root")
    entries = component_hashes(root)
    operator = operator_data_hashes(root)
    dependencies = controller_dependency_hashes(root)
    need(operator_data_hashes(root) == operator, "operator_registry_changed_during_identity")
    successors = current_execution_hashes(root)
    need(all(dependencies[name] == value for name, value in successors.items()),
         "current_execution_changed_during_identity")
    body = {"schema_version": "native-quality-component-v2", "component_paths": entries,
            "execution_dependencies": dependencies, "proof_inputs": proof_hashes(root),
            "operator_data_inputs": operator,
            "runner_snapshot_inputs": {OPERATOR_REGISTRY_PATH: {
                "release": REGISTRY_RUNNER_RELEASE, "sha256": REGISTRY_RUNNER_SHA256}},
            "current_proof_inputs": current_proof_hashes(root),
            "current_execution_inputs": successors,
            "absent_package_ancestors": list(ABSENT_ANCESTORS), "full_ci_suites": list(SUITES)}
    return {**body, "controller_sha256": digest(body), "runtime_dependencies_sha256": digest(dependencies),
        "runner_entry_sha256": ENTRY_SHA256,
        "historical_baseline": {"release": HISTORICAL_RELEASE, "qualification_sha256": HISTORICAL_FINGERPRINT,
            "ci_run_id": 41175964104, "eligible": False, "state": "FAILED_no_Build"}}


def _source_modules():
    """Load only this controller's pinned validators, before they inspect another root."""
    sys.path[:0] = [str(ROOT / "backend"), str(ROOT / "infra/gcp")]
    import document_source_candidate
    import schema26_app_rolling_v3
    import unified_credit_schema25_release

    from scripts import schema25_app_release_contract_v3

    for module, name in ((document_source_candidate, "infra/gcp/document_source_candidate.py"),
            (schema26_app_rolling_v3, "infra/gcp/schema26_app_rolling_v3.py"),
            (unified_credit_schema25_release, "infra/gcp/unified_credit_schema25_release.py"),
            (schema25_app_release_contract_v3, "backend/scripts/schema25_app_release_contract_v3.py")):
        need(Path(module.__file__).resolve() == ROOT / name, "foreign_validator_import")
    return schema26_app_rolling_v3, schema25_app_release_contract_v3, unified_credit_schema25_release


def _runner_source(root: Path) -> dict:
    """Executed in an isolated I/O-denied child; runner files are data, never imports."""
    need(root.is_absolute() and root.resolve() == root, "runner_root")
    dependencies = dependency_hashes(root)
    need(controller_dependency_hashes(ROOT) == dependencies, "controller_runner_execution_dependencies")
    native, contract, release = _source_modules()
    head = git(root, "rev-parse", "HEAD").decode().strip()
    native.rolling.release.source_guard.checked_files(root, head)
    fingerprint = contract.source_fingerprint(root, release.prior_app_files(root, contract.PRIOR_RELEASE))
    return {"release": head, "qualification_sha256": fingerprint,
        "app_sha256": contract.CANDIDATE_APP_SHA256, "app_files": contract.CANDIDATE_APP_FILES,
        "entry_sha256": hashlib.sha256((root / ENTRY).read_bytes()).hexdigest(),
        "runtime_dependencies_sha256": digest(dependencies),
        "current_proof_sha256": digest(current_proof_hashes(root))}


def _registry_git_read(values: list[str]) -> bool:
    """Only the fixed controller registry's bounded, read-only history queries."""
    if not all(type(value) is str for value in values):
        return False
    if len(values) == 4 and values[:3] == ["merge-base", "--is-ancestor", REGISTRY_RUNNER_RELEASE]:
        return re.fullmatch(r"[a-f0-9]{40}", values[3]) is not None
    if len(values) == 5 and values[:4] == ["rev-list", "--parents", "-n", "1"]:
        return re.fullmatch(r"[a-f0-9]{40}", values[4]) is not None
    return (len(values) == 7
        and values[:4] == ["log", "--format=%H", "--full-history", "--max-count=65"]
        and re.fullmatch(re.escape(REGISTRY_RUNNER_RELEASE) + r"\.\.[a-f0-9]{40}", values[4]) is not None
        and values[5:] == ["--", OPERATOR_REGISTRY_PATH])


def _child_guards(runner_root: Path):
    import socket

    def deny(*args, **kwargs):
        raise ValueError("native_quality_source_child_IO_denied")
    for name in ("connect", "connect_ex", "bind", "listen"):
        setattr(socket.socket, name, deny)
    socket.create_connection = socket.getaddrinfo = socket.gethostbyname = deny
    os.system = deny
    original = subprocess.Popen

    def popen(args, *a, **kw):
        values = list(args) if isinstance(args, (list, tuple)) else []
        allowed = {"show", "status", "rev-parse", "ls-tree", "diff", "cat-file", "archive"}
        need(values and values.pop(0) in {"git", "/usr/bin/git"}, "child_process_denied")
        directory = None
        while values and values[0].startswith("-"):
            flag = values.pop(0)
            if flag == "--no-replace-objects":
                continue
            need(flag in {"-C", "-c"} and values, "child_git_option")
            parameter = values.pop(0)
            need((flag == "-C" and parameter in {str(ROOT), str(runner_root)})
                 or (flag == "-c" and parameter == "core.fsmonitor=false"), "child_git_option")
            if flag == "-C":
                directory = parameter
        need(values and (values[0] in allowed or directory == str(ROOT) and _registry_git_read(values)),
             "child_process_denied")
        kw["env"] = {**os.environ, "GIT_NO_LAZY_FETCH": "1", "GIT_NO_REPLACE_OBJECTS": "1",
            "GIT_TERMINAL_PROMPT": "0", "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": "/dev/null"}
        return original(args, *a, **kw)
    cast(Any, subprocess).Popen = popen
    import google.auth
    import grpc
    import psycopg
    from sqlalchemy.engine import Engine
    google.auth.default = deny
    grpc.insecure_channel = grpc.secure_channel = deny
    Engine.connect = Engine.raw_connection = deny
    psycopg.connect = deny
    cast(Any, psycopg.Connection).connect = deny

    def audit(event, args):
        if event == "exec" and runner_root != ROOT:
            name = getattr(args[0], "co_filename", "")
            if name and Path(name).is_absolute():
                need(not Path(name).resolve().is_relative_to(runner_root), "foreign_runner_execution")
        if event == "import" and len(args) > 1 and args[1] and runner_root != ROOT:
            name = Path(args[1])
            need(not (name.is_absolute() and name.resolve().is_relative_to(runner_root)), "foreign_runner_import")
        if event in {"socket.connect", "socket.bind"}:
            deny()
    sys.addaudithook(audit)


def runner_source(root: Path) -> dict:
    """Bounded, credential-free source child; never the receipt's claimed result."""
    with tempfile.TemporaryDirectory(prefix="hirewiz-quality-source-") as directory:
        private = Path(directory)
        environment = {"PATH": "/usr/bin:/bin", "HOME": str(private), "XDG_CONFIG_HOME": str(private),
            "LANG": "C.UTF-8", "PYTHONDONTWRITEBYTECODE": "1",
            "CLOUDSDK_CORE_DISABLE_FILE_LOGGING": "true", "CLOUDSDK_CORE_LOG_HTTP": "false"}
        command = [sys.executable, "-I", "-B", "-X", "pycache_prefix=" + str(private / "cache"),
                   str(ROOT / COMPONENT_PATHS[1]), "--source-child", "--root", str(root)]
        result = subprocess.run(command, env=environment, stdin=subprocess.DEVNULL, capture_output=True,
                                timeout=30, check=False, cwd=private)
        need(result.returncode == 0 and 0 < len(result.stdout) <= 65536 and not result.stderr, "runner_source_child_refused")
        value = json.loads(result.stdout)
        need(type(value) is dict and set(value) == {"release", "qualification_sha256", "app_sha256", "app_files",
             "entry_sha256", "runtime_dependencies_sha256", "current_proof_sha256"}, "runner_source_child_shape")
        return value


def _successful_ci(observer, root: Path, head: str, run_id: int):
    native, _, _ = _source_modules()
    release = native.rolling.release
    release.verify_ci_checkout_contract(root)
    ci = release._json(observer._command("gh", "run", "view", str(run_id), "--repo", release.REPOSITORY,
        "--json", "headSha,status,conclusion,jobs,workflowName"))
    workflow = release._json(observer._command("gh", "api", f"repos/{release.REPOSITORY}/actions/runs/{run_id}"))
    need(workflow.get("path") == ".github/workflows/ci.yml" and workflow.get("head_sha") == head
         and workflow.get("event") in {"push", "pull_request"}
         and workflow.get("repository", {}).get("full_name") == release.REPOSITORY
         and ci.get("headSha") == head and ci.get("workflowName") == "CI" and ci.get("status") == "completed"
         and ci.get("conclusion") == "success" and len(ci.get("jobs", [])) == len(release.CI_JOBS)
         and {row.get("name") for row in ci["jobs"]} == release.CI_JOBS
         and all(row.get("status") == "completed" and row.get("conclusion") == "success" for row in ci["jobs"]),
         "controller_exact_head_CI_required")
    return ci


def verify_qualified_runner(plan, raw: bytes, *, runner_root: Path, controller_ci_run_id: int) -> dict:
    """Fresh fixed read-only source/CI/Build verification, never processing permission."""
    from infra.gcp import native_quality_acquisition_isolation as isolation

    receipt = isolation.bound_runner(plan, raw)
    need(os.environ.get("CLOUDSDK_CORE_DISABLE_FILE_LOGGING") == "true"
         and os.environ.get("CLOUDSDK_CORE_LOG_HTTP") == "false", "safe_cloud_logging_required")
    need(type(controller_ci_run_id) is int and controller_ci_run_id > 0, "controller_CI_required")
    need(not git(ROOT, "status", "--porcelain", "--untracked-files=all"), "clean_controller_required")
    identity = source_identity(ROOT)
    need(plan.controller_sha256 == identity["controller_sha256"], "controller_plan_changed")
    observed = runner_source(runner_root)
    fields = {"release", "qualification_sha256", "app_sha256", "app_files", "entry_sha256", "runtime_dependencies_sha256"}
    expected = {key: getattr(receipt, key) for key in fields}
    need({key: observed[key] for key in fields} == expected
         and observed["runtime_dependencies_sha256"] == identity["runtime_dependencies_sha256"],
         "runner_receipt_source_changed")
    need(observed["current_proof_sha256"] == digest(identity["current_proof_inputs"]),
         "runner_controller_current_proof_context_changed")
    need(receipt.release != HISTORICAL_RELEASE, "failed_historical_runner_ineligible")
    native, contract, _ = _source_modules()
    observer = native.Source26Boundary(runner_root)
    observer.verify_source_image(release=receipt.release, image_digest=receipt.image.split("@", 1)[1],
                                 build_id=receipt.build_id, ci_run_id=receipt.ci_run_id)
    ci = native.rolling.release._json(observer._command("gh", "run", "view", str(receipt.ci_run_id),
        "--repo", native.rolling.release.REPOSITORY, "--json", "jobs"))
    jobs = [row for row in ci.get("jobs", []) if row.get("name") == "backend"]
    names = (contract.COMPATIBILITY_STEP, contract.PROTOCOL_STEP, contract.RETAINED_STEP)
    steps = [step for job in jobs for step in job.get("steps", []) if step.get("name") in names]
    need(len(jobs) == 1 and len(steps) == len(names) and {step.get("name") for step in steps} == set(names)
         and all(step.get("status") == "completed" and step.get("conclusion") == "success" for step in steps),
         "runner_three_qualification_steps_required")
    metadata = native.rolling.release._json(observer._command("gh", "run", "view", str(receipt.ci_run_id),
        "--repo", native.rolling.release.REPOSITORY, "--json", "headSha,status,conclusion,jobs,workflowName"))
    build = observer._cloud("builds", "describe", receipt.build_id, "--region", native.rolling.release.REGION)
    need(digest(metadata) == receipt.ci_metadata_sha256 and digest(build) == receipt.build_metadata_sha256,
         "runner_metadata_custody_changed")
    head = git(ROOT, "rev-parse", "HEAD").decode().strip()
    _successful_ci(native.Source26Boundary(ROOT), ROOT, head, controller_ci_run_id)
    # Recheck both roots after native metadata I/O; never accept a changed checkout.
    need(runner_source(runner_root) == observed and source_identity(ROOT) == identity
         and not git(ROOT, "status", "--porcelain", "--untracked-files=all")
         and git(ROOT, "rev-parse", "HEAD").decode().strip() == head, "source_changed_during_verification")
    return {"runner_receipt_sha256": hashlib.sha256(raw).hexdigest(), "runner_release": receipt.release,
            "controller_head": head, "controller_sha256": identity["controller_sha256"],
            "fresh_source_CI_Build_verified": True, "processing_authorized": False, "native_approval": False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--source-child", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    try:
        if args.source_child:
            _child_guards(args.root)
            result = _runner_source(args.root)
        else:
            need(args.root == ROOT and not git(args.root, "status", "--porcelain"), "clean_controller_required")
            result = {**source_identity(args.root), "controller_head": git(args.root, "rev-parse", "HEAD").decode().strip(),
                "source_only": True, "CI_qualification_claimed": False, "native_approval": False,
                "provider_calls": 0, "processing_authorized": False}
        print(json.dumps(result, sort_keys=True))
        return 0
    except Exception:
        print(json.dumps({"state": "native_quality_source_refused", "native_approval": False}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
