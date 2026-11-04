"""Finite development40 transport successor; never a dispatcher or permit.

The original V1 resource Plan is reused only as typed resource/funding input.
Original V1 runner receipts/Job templates remain strict and inapplicable here.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.abc
import json
import os
import re
import socket
import stat
import subprocess
import sys
import sysconfig
import tempfile
import types
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, cast

from pydantic import BaseModel, ConfigDict, Field


def _dependency_roots():
    """Only the running interpreter's installed libraries, never user site."""
    prefix = Path(sys.prefix).resolve()
    result = set()
    for key, value in sysconfig.get_paths().items():
        raw = Path(value)
        if key in {"purelib", "platlib"} and raw.is_absolute() and raw.resolve().is_relative_to(prefix):
            result.add((raw, raw.resolve()))
            result.add((raw.resolve(), raw.resolve()))
    return tuple(result)


_DEPENDENCY_ROOTS = _dependency_roots()


def _project_source(path: Path, root: Path, dependencies=None) -> bool:
    resolved = path.resolve()
    if not (path.is_relative_to(root) or resolved.is_relative_to(root)):
        return False
    for raw, physical in _DEPENDENCY_ROOTS if dependencies is None else dependencies:
        # Both the import spelling and its target must be installed dependency
        # locations. A project symlink pointing into site-packages is not exempt.
        if path.is_relative_to(raw) and resolved.is_relative_to(physical):
            return False
    return True


ROOT = Path(__file__).resolve().parents[2]
if any((ROOT / name).exists() or (ROOT / name).is_symlink() for name in (
    "backend/__init__.py", "backend/scripts/__init__.py", "infra/__init__.py", "infra/gcp/__init__.py",
)):
    raise ValueError("development_source_undeclared_package_ancestor")


def _bootstrap_child():
    """Fence imports before loading the unchanged trusted controller helper.

    Only our own committed q source is executed, never target checkout code.
    A private empty pycache prefix prevents timestamp-valid ignored bytecode
    from being substituted for the source bytes checked by the exec fence.
    """
    if "--source-child" not in sys.argv:
        return None
    cache = Path(sys.pycache_prefix or "/")
    if (not sys.flags.isolated or not sys.dont_write_bytecode or not sys.pycache_prefix
            or not cache.is_absolute() or cache.exists() or cache.is_symlink()
            or cache.parent.resolve() != cache.parent or cache.parent.stat().st_uid != os.getuid()
            or stat.S_IMODE(cache.parent.stat().st_mode) != 0o700):
        raise ValueError("development_source_private_cache_required")
    target = Path(sys.argv[sys.argv.index("--root") + 1]).resolve()
    env = {"PATH": "/usr/bin:/bin", "HOME": str(cache.parent), "GIT_NO_LAZY_FETCH": "1",
        "GIT_NO_REPLACE_OBJECTS": "1", "GIT_TERMINAL_PROMPT": "0", "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": "/dev/null"}
    result = subprocess.run(["/usr/bin/git", "-c", "core.fsmonitor=false", "-C", str(ROOT),
        "ls-tree", "-r", "-z", "--full-tree", "HEAD"], env=env, stdin=subprocess.DEVNULL,
        capture_output=True, timeout=10, check=True)
    if len(result.stdout) > 16 * 1024**2:
        raise ValueError("development_source_bootstrap_tree_bound")
    entries = {}
    for row in result.stdout.split(b"\0"):
        if row:
            metadata, name = row.split(b"\t", 1)
            entries[name.decode()] = tuple(metadata.split())

    def checked(path):
        relative = str(path.relative_to(ROOT))
        allowed = relative.startswith(("backend/app/", "backend/scripts/", "backend/alembic/", "infra/gcp/"))
        allowed = allowed or relative in {"backend/evaluation/development40_transport_v2.py",
            "backend/evaluation/development40_qualification_v2.py"}
        mode, kind, expected = entries.get(relative, (None, None, None))
        if (not allowed or path.suffix != ".py" or path.resolve() != path or path.is_symlink() or not path.is_file()
                or mode not in {b"100644", b"100755"} or kind != b"blob"):
            raise ValueError("development_source_bootstrap_import_refused")
        raw = path.read_bytes()
        actual = hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw,
            usedforsecurity=False).hexdigest().encode()
        if actual != expected:
            raise ValueError("development_source_bootstrap_import_changed")
        return raw

    def audit(event, args):
        if event == "exec":
            filename = getattr(args[0], "co_filename", "")
            if filename and Path(filename).is_absolute() and _project_source(Path(filename), ROOT):
                checked(Path(filename))
    sys.addaudithook(audit)
    finders = tuple(sys.meta_path)

    class ImportOrigins(importlib.abc.MetaPathFinder):
        def find_spec(self, fullname, path=None, target=None):
            for finder in finders:
                spec = finder.find_spec(fullname, path, target)
                if spec is None:
                    continue
                origin = spec.origin
                if origin and Path(origin).is_absolute() and _project_source(Path(origin), ROOT):
                    # Check the actual loader origin before execution. co_filename
                    # alone cannot attest flat sourceless pyc or extension code.
                    checked(Path(origin))
                for location in spec.submodule_search_locations or ():
                    directory = Path(location)
                    if _project_source(directory, ROOT) and directory.resolve() != directory:
                        raise ValueError("development_source_bootstrap_namespace_link")
                return spec
            return None

    sys.meta_path[:] = [ImportOrigins()]
    module_name = "scripts.qualify_native_quality_acquisition"
    path = ROOT / "backend/scripts/qualify_native_quality_acquisition.py"
    module = types.ModuleType(module_name)
    module.__file__, module.__package__ = str(path), "scripts"
    sys.modules[module_name] = module
    raw = checked(path)
    if hashlib.sha256(raw).hexdigest() != "5ea766a363fa04aaa85fbdd407e7ea93e58dd969081c6e47656861ce928a0648":
        raise ValueError("development_source_bootstrap_helper_changed")

    def deny(*args, **kwargs):
        raise ValueError("development_source_child_IO_denied")
    for attribute in ("create_connection", "getaddrinfo", "gethostbyname"):
        setattr(socket, attribute, deny)
    for attribute in ("connect", "connect_ex", "listen"):
        setattr(socket.socket, attribute, deny)

    def deny_bind(*args, **kwargs):
        # urllib3's local IPv6 capability probe catches OSError. It receives
        # that denial without allocating even a loopback listener.
        raise OSError("development source child binding denied")
    cast(Any, socket.socket).bind = deny_bind
    exec(compile(raw, str(path), "exec"), module.__dict__)
    # Prime only the installed SDK's Requests dependency under the denial and
    # origin fence. The unchanged q guard then installs its complete SQL,
    # credential, subprocess and network controls before other local imports.
    import requests  # noqa: F401 -- guarded import capability initialization
    module._child_guards(target)
    return target


_CHILD_ROOT = _bootstrap_child()
sys.path[:0] = [str(ROOT), str(ROOT / "backend"), str(ROOT / "infra/gcp")]
from infra.gcp import native_quality_acquisition_isolation as original  # noqa: E402

from scripts import qualify_native_quality_acquisition as q  # noqa: E402

VERSION = "development40-transport-qualified-runner-v2"
ENTRY = "backend/evaluation/development40_transport_v2.py"
SEMANTIC_ENTRY = q.ENTRY
SEMANTIC_SHA256 = q.ENTRY_SHA256
CHANNEL = "backend/scripts/native_acquisition_channel.py"
CHANNEL_SHA256 = "89b65c3c3fb874368f28702f0048c19a3bbeccc7ab910dd12bc35a593225864e"
COMPONENTS = (ENTRY, "backend/evaluation/development40_qualification_v2.py",
    "backend/tests/test_native_quality_development_v2.py", "docs/runbooks/NATIVE_QUALITY_DEVELOPMENT_V2.md")
CODE_ROOTS = ("backend/app/", "backend/scripts/", "backend/alembic/", "infra/gcp/",
              "infra/document-processing/", "infra/native-tex/")
FIXED = ("Dockerfile", "backend/pyproject.toml", "backend/uv.lock", ".github/workflows/ci.yml")
HASH = r"[a-f0-9]{64}"
NO_BUCKET_METADATA = "DISABLE_GCS_PYTHON_CLIENT_OTEL_BUCKET_METADATA"


def need(value: object, code: str) -> None:
    if not value:
        raise ValueError("development_source_" + code)


def digest(value) -> str:
    return q.digest(value)


def _pairs(rows):
    need(len({key for key, _ in rows}) == len(rows), "duplicate_json_key")
    return dict(rows)


def _ports() -> None:
    need(Path(q.__file__).resolve() == ROOT / "backend/scripts/qualify_native_quality_acquisition.py"
         and Path(original.__file__).resolve() == ROOT / "infra/gcp/native_quality_acquisition_isolation.py",
         "foreign_validator")


def _committed(root: Path, path: str, entry: tuple[bytes, bytes, bytes]) -> str:
    source = root / path
    need(source.is_file() and source.resolve() == source and not source.is_symlink()
         and 0 <= source.stat().st_size <= 4 * 1024**2, "source_path")
    raw = source.read_bytes()
    mode, kind, expected = entry
    need(mode in {b"100644", b"100755"} and kind == b"blob"
         and hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw,
             usedforsecurity=False).hexdigest().encode() == expected, "source_not_committed")
    return hashlib.sha256(raw).hexdigest()


def source_identity(root: Path) -> dict:
    """Committed code/proof identity only; no native data/corpus or authority."""
    _ports()
    need(root.is_absolute() and root.resolve() == root, "root")
    need(not q.git(root, "status", "--porcelain", "--untracked-files=all"), "dirty_source")
    head = q.git(root, "rev-parse", "HEAD").decode().strip()
    tree = q.git(root, "rev-parse", head + "^{tree}").decode().strip()
    need(re.fullmatch(r"[a-f0-9]{40}", head) and re.fullmatch(r"[a-f0-9]{40}", tree), "git_identity")
    entries = {}
    for row in q.git(root, "ls-tree", "-r", "-z", "--full-tree", head).split(b"\0"):
        if row:
            metadata, name = row.split(b"\t", 1)
            entries[name.decode()] = tuple(metadata.split())
    tracked = set(entries)
    need(set(COMPONENTS) <= tracked and not any((root / n).exists() or (root / n).is_symlink()
        for n in q.ABSENT_ANCESTORS), "component_or_ancestor")
    # This is a new finite current-source inventory, not an exemption added to
    # the historical V1 inventory. Dynamic runtime data stays bound to the
    # actual Git tree and the stock full-source/Build verifier before native use.
    names = {n for n in tracked if n.startswith(CODE_ROOTS) and Path(n).suffix in {".py", ".json"}}
    execution = {n: _committed(root, n, entries[n]) for n in sorted(names | set(FIXED) | {ENTRY})}
    components = {n: _committed(root, n, entries[n]) for n in COMPONENTS}
    need(all((root / n).stat().st_size > 0 for n in COMPONENTS), "empty_component")
    need(execution[SEMANTIC_ENTRY] == SEMANTIC_SHA256 and execution[CHANNEL] == CHANNEL_SHA256,
         "delegated_semantics_changed")
    body = {"schema_version": VERSION, "entry_path": ENTRY, "entry_sha256": components[ENTRY],
        "semantic_entry_path": SEMANTIC_ENTRY, "semantic_entry_sha256": SEMANTIC_SHA256,
        "channel_sha256": CHANNEL_SHA256, "execution_dependencies": execution,
        "component_paths": components, "git_tree": tree, "expected_app_sha256": q.APP_SHA256,
        "expected_app_files": 290, "source_only": True}
    need(q.git(root, "rev-parse", "HEAD").decode().strip() == head
         and not q.git(root, "status", "--porcelain", "--untracked-files=all"), "source_changed")
    return {**body, "release": head, "qualification_sha256": digest(body),
        "runtime_dependencies_sha256": digest(execution), "component_sha256": digest(components)}


def _source_child(root: Path) -> dict:
    need(_CHILD_ROOT == root, "source_child_bootstrap_required")
    import release_preflight
    need(Path(release_preflight.__file__).resolve() == ROOT / "infra/gcp/release_preflight.py", "foreign_source_guard")
    own, actual = source_identity(ROOT), source_identity(root)
    need(all(own[key] == actual[key] for key in ("execution_dependencies", "component_paths")),
         "foreign_runner_sources")
    declared = {**own["execution_dependencies"], **own["component_paths"]}

    def audit(event, args):
        if event == "exec":
            filename = getattr(args[0], "co_filename", "")
            if filename and Path(filename).is_absolute() and _project_source(Path(filename), ROOT):
                local = Path(filename) if Path(filename).is_relative_to(ROOT) else Path(filename).resolve()
                path = str(local.relative_to(ROOT))
                need(path in declared, "undeclared_local_execution")
    sys.addaudithook(audit)
    # Target checkout files are inspected as bytes, never imported/executed.
    release_preflight.checked_files(root, actual["release"])
    for module in list(sys.modules.values()):
        filename = getattr(module, "__file__", None)
        if filename and Path(filename).is_absolute() and _project_source(Path(filename), ROOT):
            local = Path(filename) if Path(filename).is_relative_to(ROOT) else Path(filename).resolve()
            path = str(local.relative_to(ROOT))
            need(path in declared and Path(filename) == ROOT / path
                 and hashlib.sha256(Path(filename).read_bytes()).hexdigest() == declared[path],
                 "undeclared_local_module")
    need(source_identity(ROOT) == own and source_identity(root) == actual, "source_changed")
    return {key: actual[key] for key in ("release", "git_tree", "qualification_sha256",
        "runtime_dependencies_sha256", "component_sha256", "entry_sha256", "semantic_entry_sha256", "channel_sha256")}


def runner_source(root: Path) -> dict:
    with tempfile.TemporaryDirectory(prefix="hirewiz-development-source-") as name:
        private = Path(name).resolve()
        env = {"PATH": "/usr/bin:/bin", "HOME": str(private), "XDG_CONFIG_HOME": str(private),
               "LANG": "C.UTF-8", "PYTHONDONTWRITEBYTECODE": "1",
               "CLOUDSDK_CORE_DISABLE_FILE_LOGGING": "true", "CLOUDSDK_CORE_LOG_HTTP": "false"}
        argv = [sys.executable, "-I", "-B", "-X", "pycache_prefix=" + str(private / "cache"),
            str(ROOT / COMPONENTS[1]), "--root", str(root), "--source-child"]
        result = subprocess.run(argv, env=env, cwd=private, stdin=subprocess.DEVNULL,
            capture_output=True, timeout=45, check=False)
        need(result.returncode == 0 and 0 < len(result.stdout) <= 65536 and not result.stderr, "source_child_refused")
        value = json.loads(result.stdout)
        need(type(value) is dict and set(value) == {"release", "git_tree", "qualification_sha256",
            "runtime_dependencies_sha256", "component_sha256", "entry_sha256", "semantic_entry_sha256", "channel_sha256"},
            "source_child_shape")
        return value


class QualifiedRunner(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    schema_version: Literal["development40-transport-qualified-runner-v2"]
    repository: Literal["https://github.com/Harshil4442/ai-resume-copilot.git"]
    release: str = Field(pattern=r"^[a-f0-9]{40}$")
    git_tree: str = Field(pattern=r"^[a-f0-9]{40}$")
    ci_run_id: int = Field(gt=0)
    build_id: str = Field(pattern=r"^[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}$")
    image: str = Field(pattern="^" + re.escape(original.RUNNER_REPOSITORY) + r"@sha256:" + HASH + "$")
    qualification_sha256: str = Field(pattern="^" + HASH + "$")
    app_compatibility_sha256: str = Field(pattern="^" + HASH + "$")
    app_sha256: Literal["f2775c3b9f8d8df1f4f3d97abbe190cf5d2eb46b87beeb942b5252c3840d8670"]
    app_files: Literal[290]
    entry_path: Literal["backend/evaluation/development40_transport_v2.py"]
    entry_sha256: str = Field(pattern="^" + HASH + "$")
    semantic_entry_path: Literal["backend/scripts/native_acquisition_job.py"]
    semantic_entry_sha256: Literal["8eef532ef4d4978e9dda000e47ee694b43b55e16a168f93f9c7da724ea9b8740"]
    channel_sha256: Literal["89b65c3c3fb874368f28702f0048c19a3bbeccc7ab910dd12bc35a593225864e"]
    runtime_dependencies_sha256: str = Field(pattern="^" + HASH + "$")
    component_sha256: str = Field(pattern="^" + HASH + "$")
    observed_at: datetime
    ci_metadata_sha256: str = Field(pattern="^" + HASH + "$")
    build_metadata_sha256: str = Field(pattern="^" + HASH + "$")


def bound_runner(plan, raw: bytes) -> QualifiedRunner:
    original.checked_plan(plan)
    need(type(raw) is bytes and 0 < len(raw) <= 16384
         and hashlib.sha256(raw).hexdigest() == plan.qualified_runner_receipt_sha256, "receipt_bytes")
    json.loads(raw, object_pairs_hook=_pairs)
    receipt = QualifiedRunner.model_validate_json(raw)
    need(receipt.release != q.HISTORICAL_RELEASE and receipt.observed_at.tzinfo is not None, "receipt_identity")
    return receipt


def expected_job(plan, raw_runner: bytes, *, batch: int) -> dict:
    runner = bound_runner(plan, raw_runner)
    need(type(batch) is int and 0 <= batch < 9, "batch")
    return {"taskCount": 1, "parallelism": 1, "template": {
        "serviceAccount": original.EVALUATOR, "executionEnvironment": "EXECUTION_ENVIRONMENT_GEN2",
        "maxRetries": 0, "timeout": "240s" if batch == 0 else "1200s",
        "vpcAccess": {"egress": "PRIVATE_RANGES_ONLY", "networkInterfaces": [
            {"network": "hirewiz-private", "subnetwork": "hirewiz-callers-us-central1"}]},
        "containers": [{"image": runner.image, "command": ["/app/backend/.venv/bin/python"],
            "args": ["-I", "-B", "/app/" + ENTRY, "--namespace", plan.namespace,
                "--bundle-name", plan.bundle_name, "--bundle-generation", plan.bundle_generation,
                "--bundle-sha256", plan.bundle_sha256, "--workspace", original.WORKSPACE],
            "env": [{"name": NO_BUCKET_METADATA, "value": "true"}],
            "resources": {"limits": {"cpu": "2", "memory": "4Gi"}}}]}}


def check_job(plan, raw_runner: bytes, job: dict, *, batch: int) -> None:
    original.checked_plan(plan)
    need(job.get("name") == f"projects/{original.PROJECT}/locations/{original.REGION}/jobs/{plan.job_name}"
         and type(job.get("uid")) is str and bool(job["uid"])
         and re.fullmatch(r"[1-9][0-9]{0,18}", str(job.get("generation")))
         and str(job.get("generation")) == str(job.get("observedGeneration"))
         and job.get("reconciling", False) is False
         and job.get("terminalCondition", {}).get("state") == "CONDITION_SUCCEEDED"
         and not any(job.get(k) for k in ("startExecutionToken", "runExecutionToken", "binaryAuthorization")), "job_identity")
    need(original.documents._same(job.get("template"), expected_job(plan, raw_runner, batch=batch)), "job_template")


def check_future_bundle(plan, raw_runner: bytes, raw: bytes, *, batch: int) -> None:
    """V1 Bundle remains V1 data; new transport provenance is bound separately."""
    from scripts.native_acquisition_job import Bundle
    runner = bound_runner(plan, raw_runner)
    need(type(batch) is int and 0 <= batch < 9 and type(raw) is bytes and 0 < len(raw) <= 256 * 1024
         and hashlib.sha256(raw).hexdigest() == plan.bundle_sha256, "bundle_bytes")
    bundle = Bundle.model_validate(json.loads(raw, object_pairs_hook=_pairs))
    need(bundle.entry_sha256 == runner.semantic_entry_sha256 and bundle.job_name == plan.job_name
         and bundle.configuration.namespace == plan.namespace
         and bundle.authorization.experiment_id == plan.experiment_id
         and bundle.authorization.state == "authorized"
         and bundle.authorization.request_limit == 80
         and bundle.authorization.funding_evidence.sha256 == plan.funding_evidence_sha256
         and bundle.dispatch.external_authority_review.sha256 == plan.native_dispatch_review_sha256
         and digest(bundle.authorization.model_dump(mode="json")) == plan.acquisition_authorization_sha256,
         "bundle_authorization")
    need(bundle.authorization.valid_from <= plan.valid_from < plan.expires_at <= bundle.authorization.expires_at
         and bundle.authorization.journal_path == original.WORKSPACE + "/journal"
         and bundle.authorization.inspector.url == original.documents.ACCEPTED_ORIGIN + "/inspect"
         and bundle.authorization.inspector.image == plan.inspector_image
         and bundle.authorization.compiler.url == original.tex.ACCEPTED_ORIGIN + "/compile"
         and bundle.authorization.compiler.image == plan.compiler_image
         and bundle.authorization.compiler.policy == original.tex.POLICY_SHA256,
         "bundle_native_bindings")
    import document_source_candidate
    need(bundle.authorization.inspector.policy == document_source_candidate.POLICY_SHA256
         and len(bundle.native_files) == 41
         and {row.path for row in bundle.native_files} >= {"materialization-manifest.json"}
         and bundle.dispatch.maximum_requests == (1 if batch == 0 else 9 if batch == 8 else 10), "bundle_population")


def check(plan, snapshot: dict, *, controller_sha256: str) -> dict:
    """Reuse the exact original resource snapshot, never a rewritten view."""
    result = original.check(plan, snapshot, controller_sha256=controller_sha256)
    return {**result, "transport_profile": VERSION}


def verify_qualified_runner(plan, raw: bytes, *, runner_root: Path, controller_ci_run_id: int) -> dict:
    """Fresh genuine source/CI/Build checks, still no processing/funding permit."""
    receipt = bound_runner(plan, raw)
    need(type(controller_ci_run_id) is int and controller_ci_run_id > 0
         and plan.valid_from <= datetime.now(UTC) < plan.expires_at, "active_controller_CI_required")
    need(os.environ.get("CLOUDSDK_CORE_DISABLE_FILE_LOGGING") == "true"
         and os.environ.get("CLOUDSDK_CORE_LOG_HTTP") == "false", "private_logging")
    own = source_identity(ROOT)
    need(plan.controller_sha256 == own["qualification_sha256"], "controller_identity")
    observed = runner_source(runner_root)
    need(observed == {key: getattr(receipt, key) for key in observed}, "actual_runner_source")
    native, contract, release = q._source_modules()
    compatibility = contract.source_fingerprint(runner_root, release.prior_app_files(runner_root, contract.PRIOR_RELEASE))
    need(compatibility == receipt.app_compatibility_sha256, "app_compatibility")
    observer = native.Source26Boundary(runner_root)
    observer.verify_source_image(release=receipt.release, image_digest=receipt.image.split("@", 1)[1],
        build_id=receipt.build_id, ci_run_id=receipt.ci_run_id)
    ci = q._successful_ci(observer, runner_root, receipt.release, receipt.ci_run_id)
    names = (contract.COMPATIBILITY_STEP, contract.PROTOCOL_STEP, contract.RETAINED_STEP)
    backend = [job for job in ci["jobs"] if job.get("name") == "backend"]
    steps = [step for job in backend for step in job.get("steps", []) if step.get("name") in names]
    need(len(backend) == 1 and len(steps) == 3 and {s.get("name") for s in steps} == set(names)
         and all(s.get("status") == "completed" and s.get("conclusion") == "success" for s in steps), "qualification_steps")
    build = observer._cloud("builds", "describe", receipt.build_id, "--region", original.REGION)
    need(digest(ci) == receipt.ci_metadata_sha256 and digest(build) == receipt.build_metadata_sha256, "native_metadata_custody")
    q._successful_ci(native.Source26Boundary(ROOT), ROOT, own["release"], controller_ci_run_id)
    need(source_identity(ROOT) == own and runner_source(runner_root) == observed
         and plan.valid_from <= datetime.now(UTC) < plan.expires_at, "source_changed")
    return {"runner_receipt_sha256": hashlib.sha256(raw).hexdigest(), "fresh_source_CI_Build_verified": True,
        "native_approval": False, "processing_authorized": False, "funding_authorized": False}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--source-child", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    try:
        result = _source_child(args.root) if args.source_child else source_identity(args.root)
        print(json.dumps(result, sort_keys=True))
        return 0
    except Exception:
        print(json.dumps({"state": "development_transport_source_refused", "native_approval": False,
            "processing_authorized": False}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
