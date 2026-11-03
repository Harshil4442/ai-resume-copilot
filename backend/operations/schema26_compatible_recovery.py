"""One post-restoration schema26 recovery to genuine C6; no migration authority.

Source inspection is inert. Native use requires a separately qualified current
host, immutable original M/N/A/S/C custody, and fresh native checks. This is not
a model fallback, a pre-restoration recovery, or full R03 qualification.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib
import importlib.abc
import importlib.machinery
import json
import os
import re
import stat
import subprocess
import sys
from contextlib import ExitStack, contextmanager
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
BASE = "c75cb13f9d8ed1cfc583c9340c9b0c58906e8e97"
BASE_FP = "c0d68b7044ea68756ff63560b1cea1a17d77a874baf15ea42c40fa46b28f52b7"
TARGET = ("c6a03af0636c7e9815159f807c9b126336d03233",
          "sha256:2ebc717174904f6becc3309aedc5f487a47e31b7a5c60e4fc34b3494f6f8acb1",
          "32bc5596-a64b-4cf4-9cc0-311389d46f9d", 41500782361)
PATHS = ("backend/operations/schema26_compatible_recovery.py",
         "backend/tests/test_schema26_compatible_recovery.py",
         "docs/runbooks/schema26-compatible-recovery.md")
KIND = "hirewiz_schema26_post_restoration_c6_recovery_v1"
BINDING = "schema26.compatible-recovery.v1.binding"
ROLES = ("maintenance", "migration", "application", "coordinator")
PUBLIC_ASSETS = frozenset("backend/evals/assets/courier_prime/" + name for name in (
    "CourierPrime-Regular.ttf", "CourierPrime-Bold.ttf", "CourierPrime-Italic.ttf",
    "CourierPrime-BoldItalic.ttf", "OFL.txt", "provenance.json"))
PREIMPORT_CONFIG = ("infra/native-tex/policy.json", "infra/document-processing/policy.json",
    "infra/document-processing/policy.source-fixed-renderer.json",
    "backend/scripts/unified_credit_schema25_manifest.json")
DATA_DIRECTORIES = frozenset({"evals", "corpus", "inputs", "outputs", "oracle", "oracles",
                              "selection", "heldout", "held-out"})
GIT_READ_ENV = {"GIT_NO_REPLACE_OBJECTS": "1", "GIT_NO_LAZY_FETCH": "1",
    "GIT_TERMINAL_PROMPT": "0", "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull}
_M = None


class RecoveryDenied(RuntimeError):
    """Fixed nonsecret failures; no exception body is a provider receipt."""


def need(value, code):
    if not value:
        raise RecoveryDenied("schema26_recovery_" + code)


def _git(root, *args):
    environment = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    environment.update(GIT_READ_ENV)
    return subprocess.run(["git", "--no-replace-objects", "-c", "core.fsmonitor=false", *args], cwd=root,
        env=environment, check=True, capture_output=True, timeout=20).stdout


@contextmanager
def _git_environment():
    """Inherited frozen helpers get canonical local Git, with exact restoration."""
    previous = {key: value for key, value in os.environ.items() if key.startswith("GIT_")}
    for key in previous:
        os.environ.pop(key)
    os.environ.update(GIT_READ_ENV)
    try:
        yield
    finally:
        for key in tuple(os.environ):
            if key.startswith("GIT_"):
                os.environ.pop(key)
        os.environ.update(previous)


class _Sources(importlib.abc.MetaPathFinder):
    def __init__(self, root, tracked):
        self.root, self.tracked = root, tracked

    def find_spec(self, fullname, path=None, target=None):
        spec = importlib.machinery.PathFinder.find_spec(fullname, path, target)
        if spec is not None and isinstance(spec.origin, str) and spec.origin not in ("built-in", "frozen"):
            origin = Path(spec.origin)
            resolved = origin.resolve()
            if origin.is_relative_to(self.root) or resolved.is_relative_to(self.root):
                need(resolved == origin and origin.is_relative_to(self.root) and origin.relative_to(self.root).as_posix()
                     in self.tracked, "untracked_import")
        elif spec is not None and spec.submodule_search_locations is not None:
            for location in spec.submodule_search_locations:
                logical = Path(location)
                physical = logical.resolve()
                if logical.is_relative_to(self.root) or physical.is_relative_to(self.root):
                    need(logical == physical and logical.is_relative_to(self.root)
                         and any((self.root / n).is_relative_to(logical) for n in self.tracked), "namespace_origin")
        return spec


def excluded_data(name):
    """Six exact public runtime assets are the only evaluation-tree exception."""
    return name not in PUBLIC_ASSETS and bool(set(Path(name).parts) & DATA_DIRECTORIES)


def tree_metadata(root, head):
    """Names, modes and object IDs only; never open an evaluation-data blob."""
    values = {}
    for entry in _git(root, "ls-tree", "-r", "-z", head).split(b"\0"):
        if entry:
            metadata, raw_name = entry.split(b"\t", 1)
            mode, kind, stamp = metadata.split()
            name = os.fsdecode(raw_name)
            need(mode in (b"100644", b"100755") and kind == b"blob"
                 and not Path(name).is_absolute() and ".." not in Path(name).parts, "source_file_metadata")
            values[name] = (mode, kind, stamp)
    return values


def execution_names(metadata):
    prefixes = ("backend/app/", "backend/scripts/", "backend/alembic/", "infra/")
    return {name for name in metadata if not excluded_data(name) and name.endswith(".py")
            and (name.startswith(prefixes) or name == PATHS[0])}


def proof_names(modules):
    """The unchanged reviewed V3 union, not a JSON/ZIP suffix exemption."""
    contract = modules.a.contract
    document = importlib.import_module("document_source_candidate")
    return {*contract.v2.QUALIFICATION_PATHS, *contract.v2.TRANSPORT_PATHS, *contract.v2.BOOTSTRAP_PATHS,
        *contract.HISTORICAL_PATHS, *contract.NEW_PATHS, *contract.FOLLOWON_PATHS,
        *contract.PROTOCOL_FIXTURES, *("backend/" + n for n in contract.PROOF_ROOTS),
        *document.SOURCES, *document.CONTRACT_PATHS, document.POLICY_PATH,
        document.LEGACY_POLICY_PATH, document.courier.POLICY_PATH,
        *PUBLIC_ASSETS, *PREIMPORT_CONFIG, *PATHS}


def _verify_bytes(root, metadata, names):
    for name in sorted(names):
        need(name in metadata and not excluded_data(name), "finite_source_path")
        path = root / name
        need(path.resolve() == path and path.is_file(), "source_file")
        raw = path.read_bytes()
        actual = hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw, usedforsecurity=False)
        need(actual.hexdigest().encode() == metadata[name][2], "source_bytes")


@_git_environment()
def source_identity(root):
    """Bind the new operator separately; never call a C6 image the new host."""
    root = root.resolve()
    need(root == ROOT and Path(__file__).resolve() == root / PATHS[0], "exact_operator_root")
    need(_git(root, "rev-parse", "--show-toplevel").decode().strip() == str(root), "source_root")
    head = _git(root, "rev-parse", "HEAD").decode().strip()
    need(re.fullmatch(r"[a-f0-9]{40}", head) and head != BASE, "new_committed_host_required")
    need(not _git(root, "status", "--porcelain", "--untracked-files=all"), "dirty_host")
    _git(root, "merge-base", "--is-ancestor", BASE, head)
    need(set(_git(root, "diff", "--name-only", BASE, head).decode().splitlines())
         == set(PATHS), "finite_successor_scope")
    metadata, baseline = tree_metadata(root, head), tree_metadata(root, BASE)
    need(set(metadata) - set(baseline) == set(PATHS)
         and set(baseline) <= set(metadata)
         and all(metadata[name] == value for name, value in baseline.items()), "base_tree_changed")
    for name in metadata:
        path = root / name
        # Excluded sparse files may be absent; no data bytes are opened here.
        need(path.resolve() == path and not path.is_symlink(), "source_link")
    execution = execution_names(metadata)
    app_config = {n for n in metadata if n.startswith("backend/app/") and n.endswith(".json")}
    # The fixed native-TeX helper reads its policy at import time. Verify exact
    # bootstrap config BEFORE importing it, not just at the later V3 digest.
    _verify_bytes(root, metadata, execution | app_config | set(PREIMPORT_CONFIG))
    # Private bytecode cache is a CLI prerequisite; never consume an ignored pyc.
    need(sys.flags.isolated and sys.dont_write_bytecode and sys.pycache_prefix is not None
         and Path(sys.pycache_prefix).is_absolute(), "private_bytecode_required")
    cache = Path(str(sys.pycache_prefix))
    need(cache.resolve() == cache and cache.is_dir() and cache.stat().st_uid == os.getuid()
         and stat.S_IMODE(cache.stat().st_mode) == 0o700 and not any(cache.iterdir()), "private_empty_bytecode")
    for _name, module in tuple(sys.modules.items()):
        origin = getattr(module, "__file__", None)
        if isinstance(origin, str):
            logical, physical = Path(origin), Path(origin).resolve()
            if logical.is_relative_to(root) or physical.is_relative_to(root):
                need(logical == physical and logical.is_relative_to(root)
                     and logical.relative_to(root).as_posix() in execution, "loaded_import_origin")
    if not any(type(f) is _Sources and f.root == root for f in sys.meta_path):
        sys.meta_path.insert(0, _Sources(root, execution))
    modules = _helpers()
    names = proof_names(modules) | execution
    # App JSON is runtime code/config, not corpus; reject an untracked dynamic
    # app leaf before the unchanged V3 rglob ever opens it.
    names |= app_config
    for path in (root / "backend/app").rglob("*"):
        if path.is_file() and path.suffix in {".py", ".json"} and "__pycache__" not in path.parts:
            need(path.relative_to(root).as_posix() in names and path.resolve() == path, "untracked_app_leaf")
    _verify_bytes(root, metadata, names)
    prior = modules.a.initial.prior_app_files(root, modules.a.PRIOR.release)
    need(modules.a.contract.source_fingerprint(root, prior) == BASE_FP, "host_execution_changed")
    c6 = modules.a.initial.prior_app_files(root, TARGET[0])
    need(len(c6) == modules.a.contract.CANDIDATE_APP_FILES
         and modules.a.initial.prior_app_digest(c6) == modules.a.contract.CANDIDATE_APP_SHA256,
         "c6_application_bytes_changed")
    body = {"kind": KIND, "base": BASE, "execution_sha256": BASE_FP,
            "files": {name: hashlib.sha256((root / name).read_bytes()).hexdigest()
                      for name in PATHS}}
    need(_git(root, "rev-parse", "HEAD").decode().strip() == head, "source_changed")
    return {"release": head, "source_sha256": modules.r.sha(body),
            "execution_files": len(execution), "proof_files": len(names),
            "public_evaluation_assets": len(PUBLIC_ASSETS)}


def _helpers():
    global _M
    if _M is None:
        sys.path[:0] = [str(ROOT / "infra/gcp"), str(ROOT / "backend"), str(ROOT)]
        a = importlib.import_module("schema26_app_rolling_v3")
        _M = SimpleNamespace(a=a, r=a.rolling,
            s=importlib.import_module("schema26_schedule_resume"),
            c=importlib.import_module("schema25_coordinator_pause"),
            n=importlib.import_module("schema26_native_migration"))
    return _M


@dataclass(frozen=True)
class Custody:
    """Original absolute file/hash/journal tuples, not fresh recovery authority."""
    maintenance: tuple[str, str, str]
    migration: tuple[str, str, str]
    application: tuple[str, str, str]
    coordinator: tuple[str, str, str]

    def validate(self):
        for role in ROLES:
            row = getattr(self, role)
            need(type(row) is tuple and len(row) == 3
                 and all(type(value) is str for value in row)
                 and re.fullmatch(r"[a-f0-9]{64}", row[1])
                 and all(Path(row[i]).is_absolute() and str(Path(row[i]).resolve()) == row[i]
                         for i in (0, 2)), "custody_paths")
        need(len({getattr(self, role)[2] for role in ROLES}) == 4, "distinct_owner_journals")


@dataclass(frozen=True)
class RecoveryPlan:
    rollout: Any
    host_release: str
    host_source_sha256: str
    host_ci_run_id: int
    custody: Custody
    custody_sha256: str
    resources: tuple
    protected_liabilities: tuple
    metadata_read_policy: Any
    schema_contract: str = KIND
    database_schema: str = "20261031_0026"

    def __getattr__(self, name):
        # Reuse only stock plan mechanics, never an old exact-type authority.
        return getattr(self.rollout, name)

    def disabled_flags(self):
        return _helpers().a.CLOSED_FLAGS

    def promoted_aliases(self, pin, rows):
        return _helpers().a.App26V3Plan.promoted_aliases(self, pin, rows)

    def validate(self):
        m = _helpers()
        need(type(self) is RecoveryPlan and type(self.rollout) is m.r.RollingPlan
             and self.schema_contract == KIND and self.database_schema == m.a.schema26.SCHEMA,
             "distinct_plan")
        self.rollout.validate()
        self.custody.validate()
        need((self.release, self.image_digest, self.build_id, self.ci_run_id) == TARGET
             and self.candidate_policies is None
             and all(pin.prior_release == BASE for pin in self.services), "exact_c6_target")
        need(re.fullmatch(r"[a-f0-9]{40}", self.host_release)
             and self.host_release not in (BASE, TARGET[0])
             and re.fullmatch(r"[a-f0-9]{64}", self.host_source_sha256)
             and re.fullmatch(r"[a-f0-9]{64}", self.custody_sha256)
             and type(self.host_ci_run_id) is int and self.host_ci_run_id > 0,
             "separate_host_identity")
        need(type(self.metadata_read_policy) is m.a.MetadataReadPolicy, "exact_metadata_policy")
        self.metadata_read_policy.validate()
        need(tuple(pin.service for pin in self.services) == m.a.SERVICES, "services")
        expected = {(kind, name) for kind, names in m.a.maintenance.RESOURCE_NAMES.items() for name in names}
        expected |= {("service", name) for name in m.a.historical.EXTERNAL_SERVICES}
        need(len(self.resources) == len(expected)
             and {(p.kind, p.name) for p in self.resources} == expected
             and all(m.r.HASH.fullmatch(p.fingerprint) for p in self.resources), "resource_scope")
        need(type(self.protected_liabilities) is tuple and len(self.protected_liabilities) <= 256
             and len({row[0] for row in self.protected_liabilities}) == len(self.protected_liabilities)
             and all(type(row) is tuple and len(row) == 3 and re.fullmatch(r"[A-Za-z0-9_:-]{1,64}", row[0])
                     and m.r.HASH.fullmatch(row[1]) and type(row[2]) is int and row[2] > 0
                     for row in self.protected_liabilities), "protected_liabilities")


def _snapshot(journals):
    """Hash every original record; only the new purpose binding is excluded."""
    m, result = _helpers(), {}
    for role, journal in journals.items():
        entries: dict[str, str] = {}
        for path in sorted(journal.directory.iterdir()):
            if path.name == "owner.lock" or (role == "application" and path.name == BINDING + ".json"):
                continue
            need(path.suffix == ".json" and len(entries) < 128, "original_journal_scope")
            journal.read(path.stem)  # Original owner/mode/no-follow/plan checks.
            entries[path.name] = hashlib.sha256(path.read_bytes()).hexdigest()
        result[role] = {"plan_sha256": journal.plan_hash, "records": entries}
    return m.r.sha(result)


def _terminal(run, operation):
    m = _helpers()
    need(type(operation) is str and m.r.OPERATION.fullmatch(operation), "operation_id")
    value = run.operation(operation)
    need(value.get("name") == operation and value.get("done") is True
         and "error" not in value, "original_operation_unknown")


def _coordinator_projection(plan, journal, reader, iam):
    """Verify actual restore and reconstruct ONLY its original paused resource."""
    m = _helpers()
    plan.validate()
    for phase, offset in (("pause", 0), ("restore", 1)):
        marker = "coordinator." + phase
        started, ack, checked = (journal.read(marker + suffix)
            for suffix in (".started", ".acknowledged", ".checked"))
        need(started is not None and ack is not None and checked is not None
             and started.get("prior_generation") == plan.generation + offset
             and started.get("replay_authorized") is False
             and checked.get("generation") == plan.generation + offset + 1
             and m.r.short_revision(checked.get("revision", ""), m.c.SERVICE) == plan.revision_name(phase),
             "coordinator_terminal_custody")
        template, traffic = m.c.desired(plan, reader, phase)
        need(m.r.HASH.fullmatch(started.get("request_sha256", "")), "coordinator_request")
        _terminal(reader, ack.get("operation"))
    restored = m.c.readback(plan, reader, "restore", journal.read("coordinator.restore.started"))
    paused_template, paused_traffic = m.c.desired(plan, reader, "pause")
    paused = m.c.revision(reader, plan.revision_name("pause"))
    need({key: paused.get(key) for key in plan.template_keys}
         == {key: paused_template.get(key) for key in plan.template_keys}, "coordinator_pause_revision")
    # Native SDK JSON encodes int64 generation as a decimal string. Keep that
    # exact representation; do not admit alternative historical fingerprints.
    need(type(restored.get("generation")) is str, "native_generation_encoding")
    static = m.r.static_sha(restored)
    return m.r.sha({"uid": plan.uid, "generation": str(plan.generation + 1),
        "template": m.r.template_sha(paused_template, m.c.SERVICE), "static": static,
        "traffic": paused_traffic, "created": plan.revision_name("pause"),
        "revisions": [(plan.revision_name("pause"), m.r.template_sha(
            {k: v for k, v in paused.items() if k not in m.a.maintenance.REVISION_READ_OUTPUTS}, m.c.SERVICE))],
        "iam": m.r.sha(iam)})


class NativeRecoveryBoundary:
    """Fixed native adapter; tests replace owned ports, never native authority."""
    def __init__(self, root, custody, host_ci_run_id):
        need(type(host_ci_run_id) is int and host_ci_run_id > 0, "host_ci_identity")
        self.root, self.host, self.host_ci_run_id = root.resolve(), source_identity(root), host_ci_run_id
        self.m = _helpers()
        custody.validate()
        self.custody = custody
        loaders = (self.m.a.maintenance.load, self.m.n.load_plan, self.m.a.load, self.m.c.load_plan)
        self.original, self.journals = {}, {}
        for role, loader in zip(ROLES, loaders, strict=True):
            path, stamp, directory = getattr(custody, role)
            plan = loader(self.m.a.initial._read_pinned(Path(path), stamp))
            self.original[role] = plan
            self.journals[role] = self.m.r.Journal(Path(directory), plan)
        self.app: Any = None
        self.controls: Any = None
        self.coordinator: Any = None
        try:
            self.app = self.m.a.NativeApp26Boundary(self.root,
                maintenance_plan_path=Path(custody.maintenance[0]),
                maintenance_plan_sha256=custody.maintenance[1], maintenance_journal=Path(custody.maintenance[2]))
            self.controls = self.m.a.maintenance.NativeControls()
            self.coordinator = self.m.c.NativeCoordinatorRun()
        except Exception:
            self.close()
            raise
        self.recovery_journal = None

    def service(self, name):
        return self.app.service(name)

    def revision(self, service, name):
        return self.app.revision(service, name)

    def iam(self, name):
        return self.app.iam(name)

    def patch(self, service, body, mask):
        return self.app.patch(service, body, mask)

    def operation(self, name):
        return self.app.operation(name)

    def health(self, plan, service, revision, release_id, tag):
        need(release_id == TARGET[0], "only_c6_health")
        self.app.health(plan, service, revision, release_id, tag)

    def _source(self):
        m, observer = self.m, self.app.observer
        need(source_identity(self.root) == self.host, "host_source_changed")
        ci = m.r.release._json(observer._command("gh", "run", "view", str(self.host_ci_run_id),
            "--repo", m.r.release.REPOSITORY, "--json", "headSha,status,conclusion,jobs,workflowName"))
        native = m.r.release._json(observer._command("gh", "api",
            f"repos/{m.r.release.REPOSITORY}/actions/runs/{self.host_ci_run_id}"))
        jobs = ci.get("jobs", [])
        need(ci.get("headSha") == native.get("head_sha") == self.host["release"]
             and ci.get("workflowName") == "CI" and native.get("path") == ".github/workflows/ci.yml"
             and native.get("event") in {"push", "pull_request"}
             and native.get("repository", {}).get("full_name") == m.r.release.REPOSITORY
             and ci.get("status") == "completed" and ci.get("conclusion") == "success"
             and len(jobs) == len(m.r.release.CI_JOBS)
             and {row.get("name") for row in jobs} == m.r.release.CI_JOBS
             and all(row.get("status") == "completed" and row.get("conclusion") == "success" for row in jobs),
             "current_host_ci_required")
        backend = next(row for row in jobs if row.get("name") == "backend")
        required = {"Qualify retained actual-source schema25 app v1", "Qualify retained actual-source schema25 app v2",
            "Qualify schema26 app v3 compatibility", "Qualify schema26 app v3 native protocol", "Run backend tests"}
        steps = [step for step in backend.get("steps", []) if step.get("name") in required]
        need(len(steps) == len(required) and {step.get("name") for step in steps} == required
             and all(step.get("status") == "completed" and step.get("conclusion") == "success" for step in steps),
             "current_host_native_tests_required")
        for release, image_digest, build_id, ci_run_id in (TARGET, self._application_tuple()):
            observer._verify_source_image_provenance(release=release, image_digest=image_digest,
                build_id=build_id, ci_run_id=ci_run_id)

    def _application_tuple(self):
        a = self.original["application"]
        need(a.release == BASE and a.compatibility_sha256 == BASE_FP, "original_application_source")
        return a.release, a.image_digest, a.build_id, a.ci_run_id

    @_git_environment()
    def _custody(self):
        m, a = self.m, self.original["application"]
        n, maintenance = self.original["migration"], self.original["maintenance"]
        aj, mj, nj = (self.journals[role] for role in ("application", "maintenance", "migration"))
        for role, plan in self.original.items():
            plan.validate()
            need(self.journals[role].plan_hash == m.r.sha(asdict(plan)), "original_plan_owner")
        need(a.maintenance_baseline is not None
             and a.maintenance_baseline.maintenance_plan_sha256 == mj.plan_hash
             and n.maintenance_baseline == a.maintenance_baseline
             and (n.release, n.candidate.image_digest, n.candidate.build_id, n.ci_run_id) == self._application_tuple()
             and n.compatibility_sha256 == a.compatibility_sha256
             and n.protected_liabilities == a.protected_liabilities
             and n.resources == a.resources
             and all(getattr(n, key) == getattr(a, key) for key in
                     ("runtime_secret", "observer_secret", "retired_migration_secret", "workers_release")),
             "maintenance_migration_application_join")
        checked, ready = nj.read(m.n.MARKER + ".checked"), nj.read(m.n.MARKER + ".transaction-ready")
        need(nj.read(m.n.MARKER + ".started") == {"expected": m.a.schema26.BEFORE,
                 "target": m.a.schema26.SCHEMA, "operator_sources_sha256": m.r.sha(n.operator_sources),
                 "maintenance_sha256": m.r.sha(asdict(n.maintenance_baseline)),
                 "protected_liabilities_sha256": m.r.sha(n.protected_liabilities),
                 "replay_authorized": False, "plan_sha256": nj.plan_hash}
             and n.operator_sources == m.n.operator_sources(self.root)
             and ready is not None and set(ready) == {"affected_sha256", "schema_acl", "source_sha256",
                 "acl_sha256", "replay_authorized", "plan_sha256"}
             and ready["source_sha256"] == m.a.schema26.verify_source(self.root)
             and ready["acl_sha256"] == n.acl_sha256 and ready["replay_authorized"] is False
             and nj.read(m.n.MARKER + ".commit-acknowledged") == {"schema": m.a.schema26.SCHEMA,
                 "replay_authorized": False, "plan_sha256": nj.plan_hash}
             and checked == {"schema": m.a.schema26.SCHEMA, "transaction_ready_sha256": m.r.sha(ready),
                 "replay_authorized": False, "plan_sha256": nj.plan_hash}, "migration_commit_custody")
        need(aj.read(m.a.historical.ROLLBACK_INTENT) is None
             and not any(aj.read("rollback." + name + ".started") for name in m.a.SERVICES),
             "original_rollback_or_unknown_owner")
        self.app._verify_maintenance_baseline(a.maintenance_baseline)
        self.app._compatibility(a)
        for pin in a.services:
            m.r._old_revision(self.app, pin)
            prior = self.revision(pin.service, pin.prior_revision)
            original = {key: prior.get(key) for key in pin.revision_keys}
            if m.r.template_sha(original, pin.service) != pin.template_sha256:
                original["revision"] = pin.prior_revision
            need(m.r.template_sha(original, pin.service) == pin.template_sha256, "original_template")
            expected = m.a.candidate_template(a, pin, {**self.service(pin.service),
                "template": original, "generation": pin.generation, "observedGeneration": pin.generation,
                "trafficStatuses": list(pin.traffic)})
            target = self.revision(pin.service, a.revision(pin.service))
            need(target is not None and {key: target.get(key) for key in pin.revision_keys}
                 == {key: expected.get(key) for key in pin.revision_keys}, "original_application_revision")
            for phase, offset in (("stage", 1), ("promote", 2)):
                marker = phase + "." + pin.service
                started, ack, observed = (aj.read(marker + suffix) for suffix in (".started", ".ack", ".checked"))
                need(started is not None and ack is not None and observed is not None
                     and started.get("phase") == phase and started.get("replay_authorized") is False
                     and started.get("service_uid") == pin.uid
                     and started.get("expected_generation") == pin.generation + offset
                     and started.get("template_sha256") == m.r.template_sha(expected, pin.service)
                     and started.get("traffic") == m.r._traffic_for(a, pin, phase == "promote")
                     and ack.get("request_sha256") == started.get("request_sha256")
                     and observed.get("service_uid") == pin.uid
                     and observed.get("revision") == a.revision(pin.service)
                     and observed.get("generation") == pin.generation + offset
                     and observed.get("operation_ack_retained") is True,
                     "original_promotion_not_terminal")
                _terminal(self.app, ack.get("operation"))
        expected_binding = {"application_plan_sha256": aj.plan_hash, "release": a.release,
            "image_digest": a.image_digest, "compatibility_sha256": a.compatibility_sha256,
            "operator_sha256": hashlib.sha256(Path(m.s.__file__).read_bytes()).hexdigest(),
            "schedule_pins_sha256": m.r.sha([asdict(p) for p in m.s.schedules(maintenance)]),
            "replay_authorized": False, "plan_sha256": mj.plan_hash}
        need(mj.read(m.s.BINDING) == expected_binding, "original_schedule_binding")
        states = m.s.restore_states(maintenance, mj, self.controls)
        need(all(states[p.name] == "ENABLED" and mj.read("schedule." + p.name + ".restore.checked") is not None
                 for p in m.s.schedules(maintenance)), "fully_restored_schedules_required")
        c = self.original["coordinator"]
        iam = self.app.observer._cloud("run", "services", "get-iam-policy", m.c.SERVICE,
            "--region", m.r.release.REGION)
        paused = _coordinator_projection(c, self.journals["coordinator"], self.coordinator, iam)
        resources = self.app._resources(a.workers_release)
        expected = []
        for pin in a.resources:
            if pin.kind == "schedule":
                schedule = next(p for p in maintenance.resources if p.kind == "schedule" and p.name == pin.name)
                need(pin.fingerprint == m.r.sha({"state": "PAUSED", "configuration": schedule.configuration_sha256}),
                     "original_paused_schedule_pin")
                pin = replace(pin, fingerprint=m.r.sha({"state": "ENABLED", "configuration": schedule.configuration_sha256}))
            elif (pin.kind, pin.name) == ("service", m.c.SERVICE):
                need(pin.fingerprint == paused, "coordinator_paused_application_join")
                pin = next(p for p in resources if p.kind == "service" and p.name == m.c.SERVICE)
            expected.append(pin)
        need(resources == tuple(expected), "resource_or_document_drift")
        return resources

    @_git_environment()
    def verify_global(self, plan):
        plan.validate()
        need((plan.host_release, plan.host_source_sha256, plan.host_ci_run_id)
             == (self.host["release"], self.host["source_sha256"], self.host_ci_run_id)
             and plan.custody == self.custody, "controller_binding")
        a = self.original["application"]
        need(all(getattr(plan, key) == getattr(a, key) for key in ("runtime_uid", "runtime_secret",
             "observer_secret", "retired_migration_secret", "workers_release", "financial_sha256", "policies",
             "protected_liabilities", "metadata_read_policy")), "original_configuration_binding")
        for pin, old in zip(plan.services, a.services, strict=True):
            need(pin.service == old.service and pin.uid == old.uid and pin.generation == old.generation + 2
                 and pin.prior_revision == a.revision(old.service) and pin.secrets == old.secrets
                 and pin.static_sha256 == old.static_sha256 and pin.iam_sha256 == old.iam_sha256
                 and list(pin.traffic) == self.m.r._traffic_for(a, old, True), "original_service_binding")
        self._source()
        need(_snapshot(self.journals) == plan.custody_sha256, "original_custody_changed")
        need(self._custody() == plan.resources, "captured_resources_changed")
        self.app.observer.verify_policy_configuration(plan.policies)
        self.app._identity(plan.runtime_uid)
        signature, _ = self.app._database(plan.runtime_secret, plan.observer_secret,
            plan.retired_migration_secret, expected=plan.protected_liabilities)
        need(signature == plan.financial_sha256, "schema26_roles_fence_liabilities")
        for pin in plan.services:
            current = self.service(pin.service)
            m = self.m
            m.r.worker_iam(pin.service, self.iam(pin.service))
            need(m.r.sha(self.iam(pin.service)) == pin.iam_sha256
                 and m.r.static_sha(current) == pin.static_sha256, "identity_or_static_drift")
            m.r._old_revision(self, pin)
            prior = self.revision(pin.service, pin.prior_revision)
            original = {key: prior.get(key) for key in pin.revision_keys}
            if m.r.template_sha(original, pin.service) != pin.template_sha256:
                original["revision"] = pin.prior_revision
            need(m.r.template_sha(original, pin.service) == pin.template_sha256, "recovery_prior_template")
            target = m.a.historical.candidate_template(plan, pin, {**current, "template": original,
                "generation": pin.generation, "observedGeneration": pin.generation,
                "trafficStatuses": list(pin.traffic)})
            phase = m.a.historical.current_phase(plan, pin, current, target)
            need(phase in {"prior", "stage", "promote"}, "unplanned_recovery_phase")
            if phase != "prior":
                need(self.recovery_journal is not None
                     and self.recovery_journal.read(phase + "." + pin.service + ".started") is not None,
                     "unowned_recovery_effect")
            for template in (prior, current["template"], target):
                m.a.historical.observed_flags(template, pin.service)
                # Explicit even for this NEW type: inherited V3 dispatch skips it.
                m.a.v3_flags(template, plan.metadata_read_policy if pin.service == m.a.SERVICES[0] else None)
                m.r.verify_policy_bindings(plan.policies, pin, template)
                for secret in pin.secrets:
                    need(m.r.environment(template).get(secret.environment_name) == {"name": secret.environment_name,
                        "valueSource": {"secretKeyRef": {"secret": secret.secret, "version": secret.version}}}
                        and self.app._reference(secret.secret, secret.version) == secret.version, "secret_drift")

    def capture(self, nonce):
        self._source()
        resources = self._custody()
        m, a = self.m, self.original["application"]
        need(self.journals["application"].read(BINDING) is None, "original_owner_already_bound")
        pins = []
        for old in a.services:
            started = self.journals["application"].read("promote." + old.service + ".started")
            m.r._readback_metadata(a, self.app, old, started)  # No broken candidate HTTP check.
            row = self.service(old.service)
            revision = self.revision(old.service, a.revision(old.service))
            keys = tuple(sorted(set(row["template"]) - {"revision"}))
            pins.append(m.r.ServicePin(old.service, row["uid"], int(row["generation"]), a.revision(old.service),
                a.release, m.r.template_sha(row["template"], old.service), m.r.static_sha(row),
                m.r.sha(self.iam(old.service)), m.r.sha({key: revision.get(key) for key in keys}), keys,
                tuple(m.r.pinned_traffic(old.service, row)), old.secrets))
        rollout = m.r.RollingPlan(*TARGET, nonce, a.runtime_uid, a.runtime_secret, a.observer_secret,
            a.retired_migration_secret, a.workers_release, a.financial_sha256, a.policies, tuple(pins))
        plan = RecoveryPlan(rollout, self.host["release"], self.host["source_sha256"], self.host_ci_run_id,
            self.custody, _snapshot(self.journals), resources, a.protected_liabilities, a.metadata_read_policy)
        self.verify_global(plan)
        need(all(self.revision(p.service, plan.revision(p.service)) is None for p in pins), "c6_revision_exists")
        return plan

    def close(self):
        sessions = [getattr(self.controls, "session", None), getattr(getattr(self.coordinator, "stock", None), "session", None)]
        if getattr(self.app, "_run", None) is not None:
            sessions.append(self.app._run.stock.session)
        failed = False
        for session in sessions:
            if session is not None:
                try:
                    session.close()
                except Exception:
                    failed = True
        need(not failed, "owned_transport_cleanup")


def execute(mode, plan, boundary, journal):
    m = _helpers()
    need(type(boundary) is NativeRecoveryBoundary and type(journal) is m.r.Journal
         and journal.plan_hash == m.r.sha(asdict(plan)) and mode in {"stage", "advance", "reconcile", "observe"},
         "exact_native_controller")
    plan.validate()
    need(journal.directory not in {j.directory for j in boundary.journals.values()}, "separate_recovery_journal")
    original = boundary.journals["application"]
    # Stock advance/reconcile locks the recovery journal; hold only the four
    # original owners here, in fixed order, so no lock is acquired recursively.
    with ExitStack() as stack:
        for role in ROLES:
            stack.enter_context(boundary.journals[role].locked())
        binding = {"recovery_plan_sha256": journal.plan_hash, "recovery_journal": str(journal.directory),
            "host_release": plan.host_release, "host_source_sha256": plan.host_source_sha256,
            "target_release": TARGET[0], "replay_authorized": False, "plan_sha256": original.plan_hash}
        retained = original.read(BINDING)
        if retained is None:
            need(mode in {"stage", "advance"} and not any(journal.directory.glob("*.json")), "binding_reconcile_only")
            boundary.verify_global(plan)
            original.record(BINDING, {k: v for k, v in binding.items() if k != "plan_sha256"})
            retained = original.read(BINDING)
        need(retained == binding, "original_owner_binding_changed")
        boundary.recovery_journal = journal
        if mode == "advance":
            result = m.r.advance(plan, boundary, journal)
        elif mode == "reconcile":
            result = m.r.reconcile(plan, boundary, journal)
        else:
            with journal.locked():
                if mode == "stage":
                    need(not any(journal.read("promote." + p.service + ".started") for p in plan.services), "stage_already_passed")
                    for pin in plan.services:
                        if not m.r.action(plan, boundary, journal, pin, "stage"):
                            return {"status": "same_native_operation_pending", "replay_authorized": False}
                else:
                    for pin in plan.services:
                        phase = "promote" if journal.read("promote." + pin.service + ".started") else "stage"
                        started = journal.read(phase + "." + pin.service + ".started")
                        if started is not None:
                            m.r._readback(plan, boundary, pin, started)
                boundary.verify_global(plan)
                result = {"rolling_release_complete": all(journal.read("promote." + p.service + ".checked") for p in plan.services)}
        return {"status": "schema26_compatible_recovery_observed" if result.get("rolling_release_complete")
                else "schema26_compatible_recovery_reconcile_only", "detail": result,
                "schema": plan.database_schema, "migrations": 0, "replay_authorized": False,
                "full_R03_qualified": False, "funded_recovery_qualified": False}


def load(raw):
    m = _helpers()
    value = m.r.release._json(raw)
    need(type(value) is dict and value.get("schema_contract") == KIND, "distinct_plan")
    base = value["rollout"]
    base["policies"] = m.r.PolicyPins(**base["policies"])
    pins = []
    for row in base["services"]:
        row["secrets"] = tuple(m.r.SecretPin(**s) for s in row["secrets"])
        row["revision_keys"], row["traffic"] = tuple(row["revision_keys"]), tuple(row["traffic"])
        pins.append(m.r.ServicePin(**row))
    base["services"] = tuple(pins)
    value["rollout"] = m.r.RollingPlan(**base)
    value["custody"] = Custody(**{k: tuple(v) for k, v in value["custody"].items()})
    value["resources"] = tuple(m.a.ResourcePin(**p) for p in value["resources"])
    value["protected_liabilities"] = tuple(tuple(p) for p in value["protected_liabilities"])
    value["metadata_read_policy"] = m.a.MetadataReadPolicy(**value["metadata_read_policy"])
    plan = RecoveryPlan(**value)
    plan.validate()
    need(raw == m.r.canonical(asdict(plan)) + b"\n", "canonical_plan")
    return plan


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("source", "capture", "stage", "advance", "reconcile", "observe"))
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--custody", type=Path)
    parser.add_argument("--custody-sha256")
    parser.add_argument("--host-ci-run-id", type=int)
    parser.add_argument("--nonce")
    parser.add_argument("--plan", type=Path)
    parser.add_argument("--plan-sha256")
    parser.add_argument("--journal", type=Path)
    args = parser.parse_args()
    boundary = None
    try:
        identity = source_identity(args.root)
        if args.mode == "source":
            print(json.dumps({**identity, "native_authority": False, "full_R03_qualified": False}, sort_keys=True))
            return
        need(os.getenv("CLOUDSDK_CORE_DISABLE_FILE_LOGGING") == "true"
             and os.getenv("CLOUDSDK_CORE_LOG_HTTP") == "false", "private_logging")
        m = _helpers()
        need(args.plan is not None, "plan_required")
        if args.mode == "capture":
            need(args.custody is not None and args.custody_sha256 is not None, "custody_required")
            raw = m.a.initial._read_pinned(args.custody, args.custody_sha256)
            custody = Custody(**{k: tuple(v) for k, v in m.r.release._json(raw).items()})
            boundary = NativeRecoveryBoundary(args.root, custody, args.host_ci_run_id)
            with ExitStack() as stack:
                for role in ROLES:
                    stack.enter_context(boundary.journals[role].locked())
                plan = boundary.capture(args.nonce)
                m.n.save_plan(args.plan, plan)
            print(json.dumps({"status": "recovery_plan_captured_no_native_mutation",
                "plan_sha256": hashlib.sha256(args.plan.read_bytes()).hexdigest(), "native_mutations": 0}, sort_keys=True))
        else:
            plan = load(m.a.initial._read_pinned(args.plan, args.plan_sha256))
            boundary = NativeRecoveryBoundary(args.root, plan.custody, plan.host_ci_run_id)
            need(args.journal is not None, "journal_required")
            print(json.dumps(execute(args.mode, plan, boundary, m.r.Journal(args.journal, plan)), sort_keys=True))
    except Exception:
        print(json.dumps({"status": "schema26_compatible_recovery_stopped_reconcile_only", "replay_authorized": False}))
        raise SystemExit(1) from None
    finally:
        if boundary is not None:
            boundary.close()


if __name__ == "__main__":
    main()
