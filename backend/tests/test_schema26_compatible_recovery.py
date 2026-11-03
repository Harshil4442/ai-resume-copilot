"""Owned source/rolling/custody ports only; never native recovery or RTO proof."""
from __future__ import annotations

import ast
import copy
import importlib.util
import io
import json
import os
import socket
import subprocess
import sys
import tarfile
from dataclasses import asdict, replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from test_schema25_app_rolling_v3 import ClosedV3Fixture
from test_schema25_coordinator_pause import Coordinator
from test_schema25_maintenance import Controls

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("owned_compatible_recovery", ROOT / "backend/operations/schema26_compatible_recovery.py")
assert spec is not None and spec.loader is not None
recovery = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = recovery
spec.loader.exec_module(recovery)
m = recovery._helpers()
r = m.r


@pytest.fixture(autouse=True)
def no_external(monkeypatch):
    attempts = []

    def deny(*_args, **_kwargs):
        attempts.append("forbidden")
        raise AssertionError("owned tests forbid native network/SQL")

    monkeypatch.setattr(socket.socket, "connect", deny)
    monkeypatch.setattr(socket.socket, "connect_ex", deny)
    monkeypatch.setattr(socket, "create_connection", deny)
    monkeypatch.setattr(socket, "getaddrinfo", deny)
    from sqlalchemy.engine import Engine
    monkeypatch.setattr(Engine, "connect", deny)
    yield attempts
    assert not attempts


def private(directory):
    directory.mkdir(mode=0o700)
    return directory


def resource(reader, iam):
    """The original external-resource schema applied to an owned coordinator."""
    row = reader.service(m.c.SERVICE)
    traffic = r.pinned_traffic(m.c.SERVICE, row)
    created = r.short_revision(row["latestCreatedRevision"], m.c.SERVICE)
    revisions = [(name, r.template_sha({k: v for k, v in reader.revision(m.c.SERVICE, name).items()
        if k not in m.a.maintenance.REVISION_READ_OUTPUTS}, m.c.SERVICE))
        for name in sorted({created} | {p["revision"] for p in traffic})]
    return m.a.ResourcePin("service", m.c.SERVICE, r.sha({"uid": row["uid"],
        "generation": row["generation"], "template": r.template_sha(row["template"], m.c.SERVICE),
        "static": r.static_sha(row), "traffic": traffic, "created": created,
        "revisions": revisions, "iam": r.sha(iam)}))


@pytest.fixture
def context(tmp_path):
    tmp_path.chmod(0o700)
    case = ClosedV3Fixture(tmp_path / "maintenance")
    controls, coordinator = Controls(), Coordinator()
    cplan = m.c.capture(coordinator, "c" * 32)
    cj = r.Journal(private(tmp_path / "coordinator"), cplan)
    m.c.action(cplan, coordinator, cj, "pause")
    ciam = {"bindings": [{"role": "roles/run.invoker", "members": ["serviceAccount:owned"]}]}
    paused = resource(coordinator, ciam)
    m.c.action(cplan, coordinator, cj, "restore")
    for pin in m.s.schedules(case.maintenance_plan):
        m.a.maintenance.resource_action(pin, controls, case.maintenance_journal, "pause")
    resources = []
    for pin in case.plan.resources:
        if pin.kind == "schedule":
            original = next(p for p in case.maintenance_plan.resources if p.kind == "schedule" and p.name == pin.name)
            pin = replace(pin, fingerprint=r.sha({"state": "PAUSED", "configuration": original.configuration_sha256}))
        elif pin.name == m.c.SERVICE:
            pin = paused
        resources.append(pin)
    a = replace(case.plan, release=recovery.BASE, compatibility_sha256=recovery.BASE_FP,
        resources=tuple(sorted(resources, key=lambda p: (p.kind, p.name))), metadata_read_policy=m.a.MetadataReadPolicy())
    case.plan = a
    case.targets = {p.service: m.a.candidate_template(a, p, case.service(p.service)) for p in a.services}
    aj = r.Journal(private(tmp_path / "application"), a)
    assert m.a.execute("advance", a, case, aj)["rolling_release_complete"]
    njdir = private(tmp_path / "migration")
    n = m.n.Migration26Plan("d" * 32, m.a.PriorSource(a.release, a.image_digest, a.build_id, a.ci_run_id),
        a.compatibility_sha256, m.n.operator_sources(ROOT), a.maintenance_baseline, a.runtime_secret,
        a.observer_secret, a.retired_migration_secret, a.workers_release, a.resources,
        "a" * 64, "b" * 64, a.protected_liabilities, "c" * 64, "d" * 64, "e" * 64)
    n.validate()
    nj = r.Journal(njdir, n)
    nj.record(m.n.MARKER + ".started", {"expected": m.a.schema26.BEFORE, "target": m.a.schema26.SCHEMA,
        "operator_sources_sha256": r.sha(n.operator_sources), "maintenance_sha256": r.sha(asdict(n.maintenance_baseline)),
        "protected_liabilities_sha256": r.sha(n.protected_liabilities), "replay_authorized": False})
    nj.record(m.n.MARKER + ".transaction-ready", {"affected_sha256": "a" * 64, "schema_acl": [],
        "source_sha256": m.a.schema26.verify_source(ROOT), "acl_sha256": n.acl_sha256, "replay_authorized": False})
    nj.record(m.n.MARKER + ".commit-acknowledged", {"schema": m.a.schema26.SCHEMA, "replay_authorized": False})
    nj.record(m.n.MARKER + ".checked", {"schema": m.a.schema26.SCHEMA,
        "transaction_ready_sha256": r.sha(nj.read(m.n.MARKER + ".transaction-ready")), "replay_authorized": False})
    mj = case.maintenance_journal
    mj.record(m.s.BINDING, {"application_plan_sha256": aj.plan_hash, "release": a.release,
        "image_digest": a.image_digest, "compatibility_sha256": a.compatibility_sha256,
        "operator_sha256": recovery.hashlib.sha256(Path(m.s.__file__).read_bytes()).hexdigest(),
        "schedule_pins_sha256": r.sha([asdict(p) for p in m.s.schedules(case.maintenance_plan)]),
        "replay_authorized": False})
    for pin in m.s.schedules(case.maintenance_plan):
        m.a.maintenance.resource_action(pin, controls, mj, "restore")
    originals = {"maintenance": case.maintenance_plan, "migration": n, "application": a, "coordinator": cplan}
    journals = {"maintenance": mj, "migration": nj, "application": aj, "coordinator": cj}
    refs = {}
    for role in recovery.ROLES:
        path = tmp_path / (role + ".json")
        path.write_bytes(r.canonical(asdict(originals[role])) + b"\n")
        path.chmod(0o600)
        refs[role] = (str(path), recovery.hashlib.sha256(path.read_bytes()).hexdigest(), str(journals[role].directory))
    custody = recovery.Custody(**refs)
    events: list[str] = []
    failures: set[str] = set()

    def gate(name):
        events.append(name)
        if name in failures:
            raise recovery.RecoveryDenied("owned " + name)

    app = object.__new__(m.a.NativeApp26Boundary)
    app.service, app.revision, app.iam, app.patch, app.operation = case.service, case.revision, case.iam, case.patch, case.operation
    app._run = None
    app._verify_maintenance_baseline = lambda b: m.a.verify_closed_api_baseline(b, case.maintenance_plan, mj, case)
    app._identity = lambda _uid: gate("role-identity")
    app._reference = lambda _secret, version: version

    def database(*_args, **kwargs):
        assert kwargs["expected"] == a.protected_liabilities
        gate("schema26-role-fence-liability")
        return a.financial_sha256, a.protected_liabilities

    app._database = database
    app.health = case.health
    app.observer = SimpleNamespace(verify_policy_configuration=lambda _p: gate("policy"),
        _cloud=lambda *_args: copy.deepcopy(ciam))

    def current_resources(_release):
        gate("resource-document")
        return tuple(replace(p, fingerprint=r.sha({"state": controls.get("schedule", p.name)["state"],
            "configuration": m.a.maintenance.configuration(controls.get("schedule", p.name))}))
            if p.kind == "schedule" else resource(coordinator, ciam) if p.name == m.c.SERVICE else p for p in a.resources)

    app._resources = current_resources
    boundary = object.__new__(recovery.NativeRecoveryBoundary)
    boundary.root, boundary.m, boundary.custody = ROOT, m, custody
    boundary.host, boundary.host_ci_run_id = {"release": "f" * 40, "source_sha256": "e" * 64}, 100
    boundary.original, boundary.journals = originals, journals
    boundary.app, boundary.controls, boundary.coordinator = app, controls, coordinator
    boundary.recovery_journal = None
    boundary._source = lambda: gate("source")
    boundary._original_compatibility = lambda _a: gate("compatibility")
    plan = boundary.capture("a" * 32)
    journal = r.Journal(private(tmp_path / "recovery"), plan)
    case.health_checks.clear()
    initial_writes = len(case.writes)
    return SimpleNamespace(boundary=boundary, plan=plan, journal=journal, case=case,
        events=events, failures=failures, initial_writes=initial_writes, ciam=ciam, tmp=tmp_path)


def test_complete_recovery_moves_all_aliases_in_stock_order_and_preserves_original_records(context):
    c = context
    before = {role: {p.name: p.read_bytes() for p in j.directory.glob("*.json")}
              for role, j in c.boundary.journals.items()}
    result = recovery.execute("advance", c.plan, c.boundary, c.journal)
    assert result["status"] == "schema26_compatible_recovery_observed"
    assert not result["full_R03_qualified"] and not result["funded_recovery_qualified"]
    writes = c.case.writes[c.initial_writes:]
    assert [n for n, _, _ in writes] == [*m.a.SERVICES, *m.a.SERVICES[1:], m.a.SERVICES[0]]
    assert [mask for _, _, mask in writes] == ["template,traffic"] * 3 + ["traffic"] * 3
    assert all(source == recovery.TARGET[0] for _, _, source, _ in c.case.health_checks)
    for role, journal in c.boundary.journals.items():
        for name, raw in before[role].items():
            assert (journal.directory / name).read_bytes() == raw
        assert set(p.name for p in journal.directory.glob("*.json")) == set(before[role]) | (
            {recovery.BINDING + ".json"} if role == "application" else set())
    for pin in c.plan.services:
        row = c.case.service(pin.service)
        assert all(t["revision"] == c.plan.revision(pin.service) for t in r.pinned_traffic(pin.service, row))
        m.a.v3_flags(row["template"], c.plan.metadata_read_policy if pin.service == m.a.SERVICES[0] else None)
    assert recovery.execute("observe", c.plan, c.boundary, c.journal)["status"].endswith("observed")
    recovery.execute("reconcile", c.plan, c.boundary, c.journal)
    assert len(c.case.writes) - c.initial_writes == 6


def test_new_plan_roundtrip_is_not_an_old_plan_or_migration_authority(context):
    plan = context.plan
    raw = r.canonical(asdict(plan)) + b"\n"
    assert recovery.load(raw) == plan
    for loader in (m.a.load, m.a.historical.load, m.a.historical.load_v2, m.n.load_plan):
        with pytest.raises((r.RollingDenied, recovery.RecoveryDenied)):
            loader(raw)
    assert plan.host_release != plan.release and plan.ci_run_id == recovery.TARGET[3]


@pytest.mark.parametrize("release,profile", [
    ("d0646218058ac1b8ce7cb5ca0d85d5ff3bbc912b", "96e5f18f171878fbcee0a642bab49f24ad525e88cbe5e5f52d2c2b5c732e25b6"),
    ("d0646218058ac1b8ce7cb5ca0d85d5ff3bbc912b", recovery.BASE_FP),
    (recovery.BASE, "96e5f18f171878fbcee0a642bab49f24ad525e88cbe5e5f52d2c2b5c732e25b6"),
    ("c75cb13f9d8ed1cfc583c9340c9b0c58906e8e97", "c0d68b7044ea68756ff63560b1cea1a17d77a874baf15ea42c40fa46b28f52b7"),
    ("c75cb13f9d8ed1cfc583c9340c9b0c58906e8e97", recovery.BASE_FP),
    (recovery.BASE, "c0d68b7044ea68756ff63560b1cea1a17d77a874baf15ea42c40fa46b28f52b7"),
])
def test_old_d064_c75_or_mixed_original_application_custody_refuses_before_native_reads(context, release, profile):
    c = context
    original = c.boundary.original["application"]
    c.boundary.original["application"] = replace(original, release=release, compatibility_sha256=profile)
    with pytest.raises(recovery.RecoveryDenied, match="original_application_source"):
        c.boundary._application_tuple()
    assert len(c.case.writes) == c.initial_writes


@pytest.mark.parametrize("prior_release", [
    "d0646218058ac1b8ce7cb5ca0d85d5ff3bbc912b", "c75cb13f9d8ed1cfc583c9340c9b0c58906e8e97",
])
def test_old_prior_service_epoch_cannot_become_db8_recovery_authority(context, prior_release):
    old_pins = tuple(replace(pin, prior_release=prior_release)
        for pin in context.plan.services)
    old_plan = replace(context.plan, rollout=replace(context.plan.rollout, services=old_pins))
    with pytest.raises(recovery.RecoveryDenied, match="exact_c6_target"):
        old_plan.validate()
    assert len(context.case.writes) == context.initial_writes


@pytest.mark.parametrize("gate", ["source", "compatibility", "role-identity", "schema26-role-fence-liability", "policy", "resource-document"])
def test_each_current_source_role_fence_liability_policy_document_gate_blocks_before_patch(context, gate):
    context.failures.add(gate)
    with pytest.raises(recovery.RecoveryDenied):
        recovery.execute("advance", context.plan, context.boundary, context.journal)
    assert len(context.case.writes) == context.initial_writes


@pytest.mark.parametrize("field", ["UNIFIED_CREDIT_EXECUTION_ENABLED", "OPTIONAL_AI_GENERATION_ENABLED",
    "PRIVACY_ERASURE_RECOVERY_ENABLED", "BROWSER_SUBMIT_CANARY_ENABLED", "CANDIDATE_METADATA_CALLS_PER_MINUTE",
    "CANDIDATE_METADATA_ADMISSION_ENABLED", "CANDIDATE_ACCOUNT_LIFECYCLE_ENABLED"])
def test_new_exact_type_explicitly_enforces_flags(context, field):
    row = context.case.states[m.a.SERVICES[0]]["template"]
    env = r.environment(row)
    env[field] = {"name": field, "value": "999" if field.endswith("MINUTE") else
        "false" if env.get(field, {}).get("value") == "true" else "true"}
    row["containers"][0]["env"] = list(env.values())
    with pytest.raises((r.RollingDenied, recovery.RecoveryDenied)):
        recovery.execute("advance", context.plan, context.boundary, context.journal)
    assert len(context.case.writes) == context.initial_writes


@pytest.mark.parametrize("fault", ["iam", "secret", "static", "alias", "queue", "schedule", "coordinator", "original-record"])
def test_unrelated_current_state_and_original_custody_drift_refuse(context, fault):
    c = context
    if fault == "iam":
        c.case.policies[m.a.SERVICES[0]]["bindings"].append({"role": "extra", "members": ["extra"]})
    elif fault == "secret":
        r.environment(c.case.states[m.a.SERVICES[0]]["template"])["DATABASE_URL"]["valueSource"]["secretKeyRef"]["version"] = "99"
    elif fault == "static":
        c.case.states[m.a.SERVICES[0]]["ingress"] = "changed"
    elif fault == "alias":
        c.case.states[m.a.SERVICES[0]]["trafficStatuses"][0]["tag"] = "unknown"
    elif fault == "queue":
        old = c.boundary.app._resources
        c.boundary.app._resources = lambda release: (replace(old(release)[0], fingerprint="f" * 64), *old(release)[1:])
    elif fault == "schedule":
        c.boundary.controls.rows["schedule", m.a.maintenance.SCHEDULES[0]]["state"] = "PAUSED"
    elif fault == "coordinator":
        c.boundary.coordinator.state["template"]["containers"][0]["image"] = "wrong@sha256:" + "f" * 64
    else:
        p = c.boundary.journals["migration"].directory / (m.n.MARKER + ".checked.json")
        p.write_bytes(p.read_bytes().replace(b'"schema":', b'"changed":'))
    with pytest.raises((r.RollingDenied, recovery.RecoveryDenied)):
        recovery.execute("advance", c.plan, c.boundary, c.journal)
    assert len(c.case.writes) == c.initial_writes


@pytest.mark.parametrize("role,marker", [("migration", "schema26-migration.commit-acknowledged"),
    ("application", "promote.ai-resume-parser.ack"), ("coordinator", "coordinator.restore.acknowledged"),
    ("maintenance", "schedule.hirewiz-resume-cleanup.restore.checked")])
def test_uncertain_original_owner_is_not_a_recovery_entry_state(context, role, marker):
    (context.boundary.journals[role].directory / (marker + ".json")).unlink()
    with pytest.raises((r.RollingDenied, recovery.RecoveryDenied)):
        context.boundary.capture("b" * 32)
    assert len(context.case.writes) == context.initial_writes


def test_broken_current_health_is_never_called_but_bad_c6_health_stops_before_promotion(context):
    c = context

    def failed(_plan, _service, _revision, release, _tag):
        assert release == recovery.TARGET[0]
        raise recovery.RecoveryDenied("owned C6 target unavailable")

    c.boundary.app.health = failed
    with pytest.raises(recovery.RecoveryDenied):
        recovery.execute("advance", c.plan, c.boundary, c.journal)
    assert [mask for _, _, mask in c.case.writes[c.initial_writes:]] == ["template,traffic"]
    assert c.journal.read("stage." + m.a.SERVICES[0] + ".ack")
    assert c.journal.read("promote." + m.a.SERVICES[0] + ".started") is None


def test_lost_ack_after_effect_reconciles_same_owner_without_next_send(context):
    c = context
    c.case.lose_ack = True
    with pytest.raises(RuntimeError):
        recovery.execute("advance", c.plan, c.boundary, c.journal)
    with pytest.raises(r.RollingDenied, match="reconcile_only"):
        recovery.execute("advance", c.plan, c.boundary, c.journal)
    assert len(c.case.writes) - c.initial_writes == 1
    recovery.execute("reconcile", c.plan, c.boundary, c.journal)
    assert len(c.case.writes) - c.initial_writes == 1
    assert recovery.execute("advance", c.plan, c.boundary, c.journal)["status"].endswith("observed")
    assert len(c.case.writes) - c.initial_writes == 6


def test_unknown_before_effect_never_resends_or_changes_directory(context):
    c = context
    sends = []

    def unknown(*args):
        sends.append(args)
        raise OSError("owned lost before effect")

    c.boundary.app.patch = unknown
    with pytest.raises(OSError):
        recovery.execute("advance", c.plan, c.boundary, c.journal)
    for mode in ("reconcile", "advance"):
        with pytest.raises((r.RollingDenied, recovery.RecoveryDenied)):
            recovery.execute(mode, c.plan, c.boundary, c.journal)
    alternate = r.Journal(private(c.tmp / "other-journal"), c.plan)
    with pytest.raises(recovery.RecoveryDenied, match="binding_changed"):
        recovery.execute("advance", c.plan, c.boundary, alternate)
    with pytest.raises(recovery.RecoveryDenied, match="already_bound"):
        c.boundary.capture("b" * 32)
    assert len(sends) == 1


def test_pending_operation_blocks_then_same_journal_can_continue(context):
    c = context
    c.case.pause = True
    result = recovery.execute("advance", c.plan, c.boundary, c.journal)
    assert result["detail"]["status"] == "same_native_operation_pending"
    assert len(c.case.writes) - c.initial_writes == 1
    result = recovery.execute("reconcile", c.plan, c.boundary, c.journal)
    assert result["detail"]["status"] == "same_native_operation_pending"
    c.case.pause = False
    for operation in c.case.operations.values():
        operation["done"] = True
    recovery.execute("reconcile", c.plan, c.boundary, c.journal)
    recovery.execute("advance", c.plan, c.boundary, c.journal)
    assert len(c.case.writes) - c.initial_writes == 6


@pytest.mark.parametrize("marker", [recovery.BINDING, "stage.ai-resume-parser.ack", "stage.ai-resume-parser.checked"])
def test_local_ack_failure_retains_original_once_claim(context, marker):
    c = context
    target = c.boundary.journals["application"] if marker == recovery.BINDING else c.journal
    original = target.record
    failed: list[bool] = []

    def lost(name, value):
        original(name, value)
        if name == marker and not failed:
            failed.append(True)
            raise OSError("owned local ACK loss")

    target.record = lost
    with pytest.raises(OSError):
        recovery.execute("advance", c.plan, c.boundary, c.journal)
    target.record = original
    sends = len(c.case.writes)
    recovery.execute("reconcile", c.plan, c.boundary, c.journal)
    assert len(c.case.writes) == sends
    recovery.execute("advance", c.plan, c.boundary, c.journal)
    assert len(c.case.writes) - c.initial_writes == 6


@pytest.mark.parametrize("field,value", [("release", "0" * 40), ("image_digest", "sha256:" + "0" * 64),
    ("build_id", "00000000-0000-4000-8000-000000000000"), ("ci_run_id", 7)])
def test_target_tuple_cannot_be_borrowed_or_relabelled(context, field, value):
    plan = replace(context.plan, rollout=replace(context.plan.rollout, **{field: value}))
    with pytest.raises(recovery.RecoveryDenied, match="exact_c6_target"):
        recovery.execute("advance", plan, context.boundary, r.Journal(context.journal.directory, plan))
    assert len(context.case.writes) == context.initial_writes


@pytest.mark.parametrize("fault", [None, "head", "failed", "job", "path", "event", "repository", "backend-skip"])
def test_real_source_gate_separates_current_host_ci_from_original_c6_and_db8_provenance(context, monkeypatch, fault):
    c = context
    required = ["Qualify retained actual-source schema25 app v1", "Qualify retained actual-source schema25 app v2",
        "Qualify schema26 app v3 compatibility", "Qualify schema26 app v3 native protocol", "Run backend tests"]
    jobs = [{"name": name, "status": "completed", "conclusion": "success", "steps": [
        {"name": step, "status": "completed", "conclusion": "success"} for step in required] if name == "backend" else []}
        for name in sorted(r.release.CI_JOBS)]
    ci = {"headSha": c.boundary.host["release"], "status": "completed", "conclusion": "success", "jobs": jobs,
          "workflowName": "CI"}
    native = {"head_sha": ci["headSha"], "path": ".github/workflows/ci.yml", "event": "pull_request",
              "repository": {"full_name": r.release.REPOSITORY}}
    if fault == "head":
        ci["headSha"] = recovery.TARGET[0]
    elif fault == "failed":
        ci["conclusion"] = "failure"
    elif fault == "job":
        jobs.pop()
    elif fault in {"path", "event"}:
        native[fault] = "unreviewed"
    elif fault == "repository":
        native["repository"] = {"full_name": "foreign/repo"}
    elif fault == "backend-skip":
        next(j for j in jobs if j["name"] == "backend")["steps"][-1]["conclusion"] = "skipped"
    reads, provenance = [], []

    def command(*args):
        reads.append(args)
        assert args[0] == "gh"
        return json.dumps(native if args[1] == "api" else ci).encode()

    monkeypatch.setattr(recovery, "source_identity", lambda root: c.boundary.host)
    c.boundary.app.observer._command = command
    c.boundary.app.observer._verify_source_image_provenance = lambda **kw: provenance.append(kw)
    if fault is None:
        recovery.NativeRecoveryBoundary._source(c.boundary)
        assert [p["release"] for p in provenance] == [recovery.TARGET[0], recovery.BASE]
        assert provenance[0] == dict(zip(("release", "image_digest", "build_id", "ci_run_id"), recovery.TARGET, strict=True))
    else:
        with pytest.raises(recovery.RecoveryDenied):
            recovery.NativeRecoveryBoundary._source(c.boundary)
        assert not provenance
    assert len(reads) == 2 and len(c.case.writes) == c.initial_writes


def _owned_host_fingerprint():
    """Code/proof-only test computation; never populate the real host pin."""
    return m.a.contract.source_fingerprint(ROOT, m.a.initial.prior_app_files(ROOT, m.a.PRIOR.release))


def test_unbound_host_pin_refuses_before_lineage_import_or_native_port(monkeypatch):
    monkeypatch.setattr(recovery, "HOST_FP", "REFUSE_UNBOUND_REVIEWED_TIMING_HOST_FINGERPRINT")
    monkeypatch.setattr(recovery, "_proof_lineage", lambda *_: pytest.fail("unbound pin read lineage"))
    with pytest.raises(recovery.RecoveryDenied, match="host_fingerprint_unbound"):
        recovery._proof_fingerprints(ROOT, m)


def test_exact_original_to_host_delta_preserves_all_464_runtime_objects_and_app289():
    recovery._proof_lineage(ROOT)
    old = recovery.tree_metadata(ROOT, recovery.BASE)
    current = recovery.tree_metadata(ROOT, recovery.HOST_BASE)
    raw = (ROOT / "backend/scripts/qualify_native_quality_acquisition.py").read_bytes()
    values: dict[str, Any] = {}

    def literal(node):
        if isinstance(node, ast.Name):
            return values[node.id]
        if isinstance(node, (ast.Tuple, ast.List)):
            return tuple(literal(value) for value in node.elts)
        return ast.literal_eval(node)

    for node in ast.parse(raw).body:
        if isinstance(node, ast.Assign):
            try:
                value = literal(node.value)
            except (ValueError, KeyError, TypeError):
                continue
            for target in node.targets:
                if isinstance(target, ast.Name):
                    values[target.id] = value
    historical = recovery.tree_metadata(ROOT, values["HISTORICAL_RELEASE"])
    roots = ("backend/app/", "backend/scripts/", "backend/alembic/", "infra/gcp/",
             "infra/document-processing/", "infra/native-tex/")
    paths = ({*values["RUNNER_PATHS"], *(name for name in historical
        if name.startswith(roots) and Path(name).suffix in {".py", ".json"})}
        - set(values["CURRENT_PROOF_PATHS"]))
    assert len(paths) == 464 and not paths.intersection(recovery.PROOF_DELTA)
    assert all(old[name] == current[name] for name in paths)
    assert {name: value for name, value in old.items() if name.startswith("backend/app/")}
    assert {name: value for name, value in old.items() if name.startswith("backend/app/")} == {
        name: value for name, value in current.items() if name.startswith("backend/app/")}
    app = m.a.initial.prior_app_files(ROOT, recovery.BASE)
    assert len(app) == 289 and m.a.initial.prior_app_digest(app) == m.a.contract.CANDIDATE_APP_SHA256


def test_hash_projection_changes_only_three_exact_historical_proof_entries(monkeypatch):
    owned_fp = _owned_host_fingerprint()
    assert recovery.HOST_FP.startswith("REFUSE_") or recovery.HOST_FP == owned_fp
    assert owned_fp != recovery.BASE_FP
    monkeypatch.setattr(recovery, "HOST_FP", owned_fp)
    digest, captured = m.a.contract.digest, []

    def observe(value):
        if isinstance(value, dict) and set(recovery.PROOF_DELTA) <= set(value):
            captured.append(dict(value))
        return digest(value)

    monkeypatch.setattr(m.a.contract, "digest", observe)
    assert recovery._proof_fingerprints(ROOT, m) == (owned_fp, recovery.BASE_FP)
    ordinary, reconstructed, historical = captured[-3:]
    assert ordinary == reconstructed and digest(ordinary) == owned_fp
    assert {name for name in ordinary if ordinary[name] != historical[name]} == set(recovery.PROOF_DELTA)
    for name, (old, new) in recovery.PROOF_DELTA.items():
        assert ordinary[name] == new and historical[name] == old
    assert digest(historical) == "23b1447987e1b3686646ce709c9898cc326e6a88d7d2379ccc085e0286c28b87"


@pytest.mark.parametrize("fault", ["host", "reconstruction", "historical"])
def test_each_fingerprint_domain_is_independently_required(monkeypatch, fault):
    owned_fp = _owned_host_fingerprint()
    monkeypatch.setattr(recovery, "HOST_FP", "f" * 64 if fault == "host" else owned_fp)
    if fault != "host":
        digest = m.a.contract.digest
        calls = []

        def altered(value):
            if isinstance(value, dict) and set(recovery.PROOF_DELTA) <= set(value):
                calls.append(value)
                # 1=ordinary contract; 2=reconstruction; 3=historical projection.
                if len(calls) == (2 if fault == "reconstruction" else 3):
                    return "f" * 64
            return digest(value)

        monkeypatch.setattr(m.a.contract, "digest", altered)
    code = {"host": "host_execution_changed", "reconstruction": "host_fingerprint_projection_changed",
            "historical": "original_application_proof_changed"}[fault]
    with pytest.raises(recovery.RecoveryDenied, match=code):
        recovery._proof_fingerprints(ROOT, m)


@pytest.mark.parametrize("which", ["old", "new"])
def test_neither_original_nor_current_proof_pin_can_be_supplied_arbitrarily(monkeypatch, which):
    replacement = dict(recovery.PROOF_DELTA)
    name = next(iter(replacement))
    old, new = replacement[name]
    replacement[name] = ("0" * 64, new) if which == "old" else (old, "0" * 64)
    monkeypatch.setattr(recovery, "PROOF_DELTA", replacement)
    with pytest.raises(recovery.RecoveryDenied, match="host_proof_delta_bytes"):
        recovery._proof_lineage(ROOT)


@pytest.mark.parametrize("fault", [None, "failed", "skipped", "missing", "duplicate", "foreign-owner"])
def test_historical_compatibility_retains_exact_original_ci_checks(context, monkeypatch, fault):
    c = context
    plan = c.boundary.original["application"]
    names = (m.a.contract.COMPATIBILITY_STEP, m.a.contract.PROTOCOL_STEP, m.a.contract.RETAINED_STEP)
    steps = [{"name": name, "status": "completed", "conclusion": "success"} for name in names]
    if fault in {"failed", "skipped"}:
        steps[0]["conclusion"] = "failure" if fault == "failed" else "skipped"
    elif fault == "missing":
        steps.pop()
    elif fault == "duplicate":
        steps.append(copy.deepcopy(steps[0]))
    projected, reads = [], []
    def project(root, modules):
        projected.append((root, modules))
        return "a" * 64, recovery.BASE_FP

    monkeypatch.setattr(recovery, "_proof_fingerprints", project)

    def command(*args):
        reads.append(args)
        assert args == ("gh", "run", "view", str(plan.ci_run_id), "--repo", r.release.REPOSITORY, "--json", "jobs")
        return json.dumps({"jobs": [{"name": "backend", "steps": steps}]}).encode()

    c.boundary.app.observer._command = command
    candidate = replace(plan) if fault == "foreign-owner" else plan
    if fault is None:
        recovery.NativeRecoveryBoundary._original_compatibility(c.boundary, candidate)
    else:
        with pytest.raises(recovery.RecoveryDenied):
            recovery.NativeRecoveryBoundary._original_compatibility(c.boundary, candidate)
    assert len(projected) == len(reads) == (0 if fault == "foreign-owner" else 1)
    assert len(c.case.writes) == c.initial_writes


def test_stock_compatibility_still_refuses_current_host_as_original_app(context, monkeypatch):
    c = context
    monkeypatch.setattr(m.a.contract, "source_fingerprint", lambda *_: "f" * 64)
    c.boundary.app.root = ROOT
    c.boundary.app.observer._command = lambda *_: pytest.fail("stock mismatch reached CI")
    with pytest.raises(r.RollingDenied, match="compatibility_source_changed"):
        m.a.NativeApp26Boundary._compatibility(c.boundary.app, c.boundary.original["application"])
    assert len(c.case.writes) == c.initial_writes


def _child(root, program, tmp, *, cache=None, extra_env=None):
    cache = private(tmp / "bytecode") if cache is None else cache
    return subprocess.run([sys.executable, "-I", "-B", "-X", "pycache_prefix=" + str(cache), "-"],
        input=program, text=True, capture_output=True, cwd=tmp, timeout=40,
        env={"PATH": os.defpath, "APP_ENV": "test", "DATABASE_URL": "sqlite://", "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
             **(extra_env or {})})


def _source_child_program(root):
    return '''import sys,socket,subprocess,importlib.util,json,builtins,io,os
from pathlib import Path
root=Path(ROOT)
real=subprocess.Popen
counts={'external':0,'git':0,'denied_local_import_probe':0,'evaluation_data_open':0}
public={str(root/'backend/evals/assets/courier_prime'/name) for name in (
 'CourierPrime-Regular.ttf','CourierPrime-Bold.ttf','CourierPrime-Italic.ttf','CourierPrime-BoldItalic.ttf','OFL.txt','provenance.json')}
blocked={'evals','corpus','inputs','outputs','oracle','oracles','selection','heldout','held-out'}
def forbidden(path):
 if isinstance(path,int):return False
 p=Path(os.fsdecode(path))
 if not p.is_absolute():p=Path.cwd()/p
 for candidate in (p,p.resolve()):
  if candidate.is_relative_to(root) and str(candidate) not in public and set(candidate.relative_to(root).parts)&blocked:return True
 return False
def evaluation_denied():counts['evaluation_data_open']+=1;raise AssertionError('evaluation data open denied before I/O')
def wrap_open(original):
 def guarded(path,*a,**k):
  if forbidden(path):return evaluation_denied()
  return original(path,*a,**k)
 return guarded
builtins.open=wrap_open(builtins.open);io.open=wrap_open(io.open);os.open=wrap_open(os.open)
def denied(*a,**k): counts['external']+=1;raise AssertionError('owned child forbids external I/O')
socket.create_connection=socket.getaddrinfo=denied
socket.socket.connect=socket.socket.connect_ex=denied
def bind(sock,address):
 if sock.family==socket.AF_INET6 and address==('::1',0):
  counts['denied_local_import_probe']+=1
  raise OSError('owned local capability probe denied')
 return denied()
socket.socket.bind=bind
def process(argv,*a,**k):
 if type(argv) is not list or argv[0]!='git': return denied()
 if k.get('cwd')!=root or not k.get('stdout') is subprocess.PIPE or not k.get('stderr') is subprocess.PIPE: return denied()
 for argument in argv:
  if ':' in argument and forbidden(root/argument.split(':',1)[1]):return evaluation_denied()
 if 'archive' in argv and argv[-1]!='backend/app':return evaluation_denied()
 counts['git']+=1
 return real(argv,*a,**k)
subprocess.Popen=process
sys.path.insert(0,str(root/'backend'))
from sqlalchemy.engine import Engine
Engine.connect=denied
spec=importlib.util.spec_from_file_location('owned_source',root/'backend/operations/schema26_compatible_recovery.py')
module=importlib.util.module_from_spec(spec);sys.modules[spec.name]=module;spec.loader.exec_module(module)
try:
 previous_git={k:v for k,v in os.environ.items() if k.startswith('GIT_')}
 identity=module.source_identity(root)
 print(json.dumps({'identity':identity,'counts':counts,
  'git_environment_restored':previous_git=={k:v for k,v in os.environ.items() if k.startswith('GIT_')},
  'base_tree':module._git(root,'rev-parse',module.BASE+'^{tree}').decode().strip()}))
except module.RecoveryDenied as error:
 print(json.dumps({'denied':str(error),'counts':counts,
  'git_environment_restored':previous_git=={k:v for k,v in os.environ.items() if k.startswith('GIT_')}}))
'''.replace("ROOT", repr(str(root)))


def sparse_snapshot(destination):
    # Git object IDs are metadata; the sparse workspace contains only the exact
    # code/proof/config union and six expressly public Courier runtime assets.
    subprocess.run(["git", "clone", "--shared", "--no-checkout", str(ROOT), str(destination)],
        check=True, capture_output=True, timeout=20)
    root = destination.resolve()
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, check=True, capture_output=True, timeout=10).stdout.decode().strip()
    metadata = recovery.tree_metadata(ROOT, head)
    allowed = recovery.execution_names(metadata) | recovery.proof_names(m) | set(recovery.PATHS)
    allowed |= {name for name in metadata if name.startswith("backend/app/") and name.endswith(".json")}
    assert allowed - set(recovery.PATHS) <= set(metadata) and not any(recovery.excluded_data(name) for name in allowed)
    # Escape literal route brackets in Git's no-cone wildmatch input.
    patterns = "\n".join("/" + name.replace("[", "\\[").replace("]", "\\]") for name in sorted(allowed)) + "\n"
    subprocess.run(["git", "sparse-checkout", "set", "--no-cone", "--stdin"], cwd=root,
        input=patterns, text=True, check=True, capture_output=True, timeout=20)
    subprocess.run(["git", "checkout", "--detach", head], cwd=root, check=True, capture_output=True, timeout=20)
    # This is an OWNED code fixture, never a qualified image/current host. During
    # authoring the real source intentionally retains a refusing HOST_FP pin.
    # Compute the ordinary explicit code/proof-only contract, then bind it only
    # in the disposable fixture. Original db8 FP and all proof-leaf pins remain.
    prior = m.a.initial.prior_app_files(ROOT, m.a.PRIOR.release)
    owned_fp = m.a.contract.source_fingerprint(ROOT, prior)
    for name in recovery.PATHS:
        raw = (ROOT / name).read_bytes()
        if name == recovery.PATHS[0]:
            literal = ("HOST_FP = " + json.dumps(recovery.HOST_FP)).encode()
            assert raw.count(literal) == 1
            raw = raw.replace(literal, ("HOST_FP = " + json.dumps(owned_fp)).encode())
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
    subprocess.run(["git", "add", "--sparse", *recovery.PATHS], cwd=root,
        check=True, capture_output=True, timeout=10)
    subprocess.run(["git", "-c", "user.name=Owned", "-c", "user.email=owned@example.invalid",
        "commit", "--allow-empty", "-m", "Owned timing-host code fixture; no image or native authority"], cwd=root,
        check=True, capture_output=True, timeout=20)
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=root,
        check=True, capture_output=True, timeout=10).stdout.decode().strip()
    assert {str(p.relative_to(root)) for p in (root / "backend/evals").rglob("*") if p.is_file()} == recovery.PUBLIC_ASSETS
    assert not (root / "backend/evals/unified_credit_v2/cases.jsonl").exists()
    return root, head


@pytest.mark.parametrize("poison", [None, "bytecode", "ignored-package", "dirty", "foreign-scope", "redirected-git", "replaced-base", "replaced-c6", "replaced-prior", "evaluation-open"])
def test_actual_committed_source_child_refuses_cache_shadow_dirty_or_unreviewed_scope(tmp_path, poison, record_property):
    root, head = sparse_snapshot(tmp_path / "root")
    cache = private(tmp_path / "cache")
    extra_env = {}
    base_tree = subprocess.run(["git", "--no-replace-objects", "rev-parse", recovery.BASE + "^{tree}"],
        cwd=root, check=True, capture_output=True, timeout=10).stdout.decode().strip()
    sentinel = tmp_path / "untrusted-executed"
    if poison == "bytecode":
        (cache / "poison.pyc").write_bytes(b"untrusted")
    elif poison == "ignored-package":
        (root / ".git/info/exclude").write_text("backend/scripts/__init__.py\n")
        (root / "backend/scripts/__init__.py").write_text("from pathlib import Path\nPath(" + repr(str(sentinel)) + ").write_text('bad')\n")
    elif poison == "dirty":
        with (root / recovery.PATHS[0]).open("a") as stream:
            stream.write("\n# uncommitted owned negative\n")
    elif poison == "foreign-scope":
        path = root / "backend/foreign.py"
        path.write_text("# owned unreviewed source\n")
        subprocess.run(["git", "add", "--sparse", "backend/foreign.py"], cwd=root, check=True, capture_output=True, timeout=10)
        subprocess.run(["git", "-c", "user.name=Owned", "-c", "user.email=owned@example.invalid",
            "commit", "-m", "Owned foreign-scope negative"], cwd=root, check=True, capture_output=True, timeout=10)
    elif poison == "redirected-git":
        foreign = tmp_path / "foreign"
        subprocess.run(["git", "clone", "--shared", "--no-checkout", str(ROOT), str(foreign)],
            check=True, capture_output=True, timeout=20)
        subprocess.run(["git", "update-ref", "HEAD", recovery.BASE + "^"], cwd=foreign,
            check=True, capture_output=True, timeout=20)
        extra_env = {"GIT_DIR": str(foreign / ".git"), "GIT_WORK_TREE": str(root),
                     "GIT_INDEX_FILE": str(foreign / ".git/index")}
    elif poison == "replaced-base":
        subprocess.run(["git", "replace", "-f", recovery.BASE, recovery.BASE + "^"], cwd=root,
            check=True, capture_output=True, timeout=10)
        redirected = subprocess.run(["git", "rev-parse", recovery.BASE + "^{tree}"], cwd=root,
            check=True, capture_output=True, timeout=10).stdout.decode().strip()
        assert redirected != base_tree  # The replacement really changes the queried graph.
    elif poison in {"replaced-c6", "replaced-prior"}:
        # Construct a tiny code-only replacement, never a corpus-bearing tree.
        clean_env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
        clean_env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull)

        def owned_git(args, text):
            return subprocess.run(["git", *args], cwd=root, input=text, text=True, env=clean_env,
                check=True, capture_output=True, timeout=10).stdout.strip()

        blob = owned_git(["hash-object", "-w", "--stdin"], "# owned-code-only-replacement\n")
        app_tree = owned_git(["mktree"], "100644 blob " + blob + "\t__init__.py\n")
        backend_tree = owned_git(["mktree"], "040000 tree " + app_tree + "\tapp\n")
        root_tree = owned_git(["mktree"], "040000 tree " + backend_tree + "\tbackend\n")
        commit = owned_git(["-c", "user.name=Owned", "-c", "user.email=owned@example.invalid",
            "commit-tree", root_tree], "Owned code-only archive replacement\n")
        replaced = recovery.TARGET[0] if poison == "replaced-c6" else m.a.contract.PRIOR_RELEASE
        owned_git(["replace", "-f", replaced, commit], "")
        raw = subprocess.run(["git", "archive", "--format=tar", replaced, "--", "backend/app"],
            cwd=root, env=clean_env, check=True, capture_output=True, timeout=10).stdout
        with tarfile.open(fileobj=io.BytesIO(raw), mode="r:") as controlled:
            leaves = [member for member in controlled if member.isfile()]
        assert [member.name for member in leaves] == ["backend/app/__init__.py"]
        assert b"owned-code-only-replacement" in raw
    # A hostile CWD is not an import source under -I.
    (tmp_path / "rolling_release.py").write_text("raise AssertionError('hostile cwd')\n")
    program = _source_child_program(root)
    if poison == "evaluation-open":
        program += '''
try:Path(root/'backend/evals/unified_credit_v2/cases.jsonl').read_bytes()
except AssertionError:print(json.dumps({'evaluation_guard_denied':True,'counts':counts}))
'''
    result = _child(root, program, tmp_path, cache=cache, extra_env=extra_env)
    assert result.returncode == 0, result.stderr[-2048:]
    payloads = [json.loads(line) for line in result.stdout.splitlines()]
    payload = payloads[0]
    assert payload["counts"]["external"] == 0 and not sentinel.exists()
    assert payload["git_environment_restored"] is True
    assert payload["counts"]["evaluation_data_open"] == 0
    if poison == "evaluation-open":
        assert payloads[1]["evaluation_guard_denied"] and payloads[1]["counts"]["evaluation_data_open"] == 1
        record_property("intentional_denied_evaluation_open", "1")
    assert payload["counts"]["denied_local_import_probe"] <= 1
    record_property("owned_source_child_counts", json.dumps(payload["counts"], sort_keys=True))
    if poison in (None, "redirected-git", "replaced-base", "replaced-c6", "replaced-prior", "evaluation-open"):
        assert payload["identity"]["release"] == head
        assert payload["base_tree"] == base_tree
        owned_fp = m.a.contract.source_fingerprint(ROOT, m.a.initial.prior_app_files(ROOT, m.a.PRIOR.release))
        expected_identity = r.sha({"kind": recovery.KIND, "host_base": recovery.HOST_BASE,
            "execution_sha256": owned_fp,
            "original_application": {"release": recovery.BASE, "compatibility_sha256": recovery.BASE_FP},
            "proof_delta": recovery.PROOF_DELTA,
            "files": {name: recovery.hashlib.sha256((root / name).read_bytes()).hexdigest() for name in recovery.PATHS}})
        assert payload["identity"]["source_sha256"] == expected_identity != recovery.BASE_FP
        assert payload["identity"]["public_evaluation_assets"] == 6
        record_property("owned_source_identity", json.dumps(payload["identity"], sort_keys=True))
        assert payload["counts"]["git"] > 0
    else:
        assert "denied" in payload and "identity" not in payload


def test_process_death_after_durable_start_retains_same_once_owner(context, record_property):
    c = context
    original = c.boundary.journals["application"]
    original.record(recovery.BINDING, {"recovery_plan_sha256": c.journal.plan_hash,
        "recovery_journal": str(c.journal.directory), "host_release": c.plan.host_release,
        "host_source_sha256": c.plan.host_source_sha256, "target_release": recovery.TARGET[0], "replay_authorized": False})
    pin = c.plan.services[0]
    current = c.boundary.service(pin.service)
    body = r.stage_body(c.plan, pin, current)
    started = {"phase": "stage", "request_sha256": r.sha(body), "mask": "template,traffic",
        "template_sha256": r.template_sha(body["template"], pin.service), "traffic": body["traffic"],
        "expected_generation": int(current["generation"]) + 1, "service_uid": pin.uid,
        "previous_etag": current["etag"], "replay_authorized": False}
    plan_path = c.tmp / "recovery.json"
    plan_path.write_bytes(r.canonical(asdict(c.plan)) + b"\n")
    death_directory = private(c.tmp / "death-snapshot")
    snapshot, _ = sparse_snapshot(death_directory / "root")
    # Same no-network/SQL/data-open boundary as source children, without running
    # source qualification or a real native controller in the journal writer.
    header = _source_child_program(snapshot).split("spec=importlib.util.spec_from_file_location", 1)[0]
    program = header + '''spec=importlib.util.spec_from_file_location('owned_death', SCRIPT)
module=importlib.util.module_from_spec(spec);sys.modules[spec.name]=module;spec.loader.exec_module(module)
plan=module.load(Path(PLAN).read_bytes())
journal=module._helpers().r.Journal(Path(JOURNAL),plan)
with journal.locked():journal.record(MARKER,STARTED)
assert counts['external']==counts['evaluation_data_open']==0
Path(COUNTS).write_text(json.dumps(counts))
os._exit(23)
'''.replace("SCRIPT", repr(str(snapshot / recovery.PATHS[0]))).replace(
        "PLAN", repr(str(plan_path))).replace("JOURNAL", repr(str(c.journal.directory))).replace(
        "MARKER", repr("stage." + pin.service + ".started")).replace("STARTED", repr(started)).replace(
        "COUNTS", repr(str(c.tmp / "death-counts.json")))
    result = _child(snapshot, program, c.tmp)
    assert result.returncode == 23 and not result.stdout and not result.stderr
    death_counts = json.loads((c.tmp / "death-counts.json").read_text())
    assert death_counts["external"] == death_counts["evaluation_data_open"] == 0
    record_property("owned_process_death_counts", json.dumps(death_counts, sort_keys=True))
    assert c.journal.read("stage." + pin.service + ".started")["replay_authorized"] is False
    for mode in ("reconcile", "advance"):
        with pytest.raises((r.RollingDenied, recovery.RecoveryDenied)):
            recovery.execute(mode, c.plan, c.boundary, c.journal)
    assert len(c.case.writes) == c.initial_writes
