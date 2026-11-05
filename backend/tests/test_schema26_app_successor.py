"""Owned native-shaped ports only; no SQL, cloud or application qualification."""

from __future__ import annotations

import ast
import copy
import json
import socket
import subprocess
import sys
from dataclasses import asdict, fields, replace
from types import ModuleType, SimpleNamespace

import pytest
from backend.tests.test_rolling_release import FixtureBoundary

from scripts import schema26_app_successor as s

r = s.rolling
API, ANALYSIS, EMPLOYER = r.release.SERVICES


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def denied(*args, **kwargs):
        raise AssertionError("Owned successor controls forbid network I/O")

    monkeypatch.setattr(socket, "create_connection", denied)
    monkeypatch.setattr(socket, "getaddrinfo", denied)
    monkeypatch.setattr(socket.socket, "connect", denied)
    monkeypatch.setattr(socket.socket, "connect_ex", denied)


@pytest.fixture
def context(tmp_path):
    tmp_path.chmod(0o700)
    fixture = FixtureBoundary()
    reference = {
        "name": "RATE_LIMIT_STORAGE_URL",
        "valueSource": {"secretKeyRef": {"secret": "owned-existing-redis", "version": "1"}},
    }
    fixture.states[API]["template"]["containers"][0]["env"].append(reference)
    fixture.revisions[(API, fixture.plan.pin(API).prior_revision)]["containers"][0]["env"].append(
        copy.deepcopy(reference)
    )
    # Real db8's Service template has an explicit revision; immutable revision
    # responses expose that identity through name instead of this template key.
    for pin in fixture.plan.services:
        fixture.states[pin.service]["template"]["revision"] = pin.prior_revision
        env = fixture.states[pin.service]["template"]["containers"][0]["env"]
        restored = [
            {
                "name": name,
                "value": "true"
                if pin.service == API or name == "EMPLOYER_DISCOVERY_ENABLED"
                else "false",
            }
            for name in sorted(s.app26.historical.PILOT_FLAGS)
        ]
        if pin.service == API:
            restored += [
                {"name": name, "value": value}
                for name, value in s.app26.MetadataReadPolicy().environment().items()
            ]
        env.extend(restored)
        fixture.revisions[(pin.service, pin.prior_revision)]["containers"][0]["env"].extend(
            copy.deepcopy(restored)
        )
    boundary = object.__new__(s.NativeSuccessorBoundary)
    boundary.root, boundary.journal, boundary._run = tmp_path, None, None
    for name in ("service", "revision", "iam", "patch", "operation", "health"):
        setattr(boundary, name, getattr(fixture, name))
    source_checks = []
    boundary._sources = lambda plan: source_checks.append(plan.controller_release)
    boundary._identity = lambda uid: r.need(
        uid == fixture.plan.runtime_uid, "owned_identity_changed"
    )
    boundary._reference = lambda secret, version: version
    boundary.observer = SimpleNamespace(verify_policy_configuration=lambda policies: None)
    expected = {
        (kind, name) for kind, names in s.app26.maintenance.RESOURCE_NAMES.items() for name in names
    }
    expected |= {("service", name) for name in s.app26.historical.EXTERNAL_SERVICES}
    resources = tuple(s.app26.ResourcePin(kind, name, "1" * 64) for kind, name in sorted(expected))
    boundary._resources = lambda worker: resources

    def database(*args, **kwargs):
        r.need(not fixture.finance_drift, "owned_financial_or_schema_drift")
        return fixture.plan.financial_sha256, ()

    boundary._database = database
    prior = s.SourcePin(
        fixture.plan.services[0].prior_release,
        "sha256:" + "f" * 64,
        "22222222-2222-4222-8222-222222222222",
        111,
    )
    pins = tuple(boundary._pin(name, prior) for name in r.release.SERVICES)
    plan = s.SuccessorPlan(
        **{field.name: getattr(fixture.plan, field.name) for field in fields(r.RollingPlan)},
        prior_source=prior,
        controller_release="9" * 40,
        controller_source_sha256="8" * 64,
        controller_ci_run_id=333,
        resources=resources,
        reachable_revisions=boundary._reachable(pins, prior),
        candidate_employer_cache=r.EmployerCacheBinding("owned-existing-redis"),
    )
    plan = replace(plan, services=pins)
    pins = tuple(
        replace(
            pin,
            candidate_template_sha256=r.template_sha(
                r.stage_body(plan, pin, boundary.service(pin.service))["template"], pin.service
            ),
        )
        if pin.service == EMPLOYER
        else pin
        for pin in pins
    )
    plan = replace(plan, services=pins)
    plan.validate()
    fixture.plan = plan
    return SimpleNamespace(
        plan=plan,
        boundary=boundary,
        fixture=fixture,
        journal=r.Journal(tmp_path, plan),
        source_checks=source_checks,
    )


def test_exact_worker_reference_is_hashed_before_once_send(context):
    c = context
    result = s.execute("advance", c.plan, c.boundary, c.journal)
    assert result["rolling_release_complete"] is True and result["schema"] == s.SCHEMA
    assert [name for name, _, _ in c.fixture.writes] == [
        API,
        ANALYSIS,
        EMPLOYER,
        ANALYSIS,
        EMPLOYER,
        API,
    ]
    for service, body, _ in c.fixture.writes[:3]:
        env = r.environment(body["template"])
        prior = r.environment(c.fixture.revisions[(service, c.plan.pin(service).prior_revision)])
        for name in s.app26.historical.PILOT_FLAGS:
            assert env[name] == prior[name]
        if service == API:
            for name in s.app26.MetadataReadPolicy().environment():
                assert env[name] == prior[name]
        assert ("REDIS_URL" in env) is (service == EMPLOYER)
        if service == EMPLOYER:
            assert env["REDIS_URL"] == {
                "name": "REDIS_URL",
                "valueSource": {"secretKeyRef": {"secret": "owned-existing-redis", "version": "1"}},
            }
        started = c.journal.read("stage." + service + ".started")
        assert started["request_sha256"] == r.sha(body)
        assert started["template_sha256"] == r.template_sha(body["template"], service)
    assert all(
        result[key] == 0
        for key in ("migrations", "data_transfers", "credential_changes", "iam_changes")
    )
    assert all(
        "synthetic-sensitive" not in path.read_text() for path in c.journal.directory.glob("*.json")
    )


@pytest.mark.parametrize(
    "changes",
    [
        {"service": API},
        {"service": ANALYSIS},
        {"environment_name": "DATABASE_URL"},
        {"version": "latest"},
        {"version": "2"},
        {"secret": "https://foreign.example/key"},
    ],
)
def test_cache_binding_has_no_service_key_version_or_url_wildcard(changes):
    with pytest.raises(r.RollingDenied):
        r.EmployerCacheBinding(**({"secret": "owned-existing-redis"} | changes)).validate()


def test_cache_reuses_api_reference_and_cannot_replace_worker_key(context):
    c = context
    with pytest.raises(r.RollingDenied, match="existing_api_reference"):
        replace(
            c.plan, candidate_employer_cache=r.EmployerCacheBinding("foreign-secret")
        ).validate()
    pin = replace(
        c.plan.pin(EMPLOYER),
        secrets=(
            *c.plan.pin(EMPLOYER).secrets,
            r.SecretPin("REDIS_URL", "owned-existing-redis", "1"),
        ),
    )
    with pytest.raises(r.RollingDenied, match="already_present"):
        replace(
            c.plan, services=tuple(pin if p.service == EMPLOYER else p for p in c.plan.services)
        ).validate()


def test_legacy_serialized_fields_hash_and_default_request_are_byte_identical(monkeypatch):
    fixture = FixtureBoundary()
    plan = fixture.plan
    assert "candidate_employer_cache" not in asdict(plan)
    assert r.employer_cache_binding(plan) is None
    raw = subprocess.run(
        ["git", "show", "09fb8203fb5897eb733ba0305b69db85d84dec77:infra/gcp/rolling_release.py"],
        cwd=s.ROOT,
        check=True,
        capture_output=True,
    ).stdout
    old = next(
        node
        for node in ast.parse(raw).body
        if isinstance(node, ast.ClassDef) and node.name == "RollingPlan"
    )
    assert [node.target.id for node in old.body if isinstance(node, ast.AnnAssign)] == [
        field.name for field in fields(plan)
    ]
    # Execute only this named frozen helper's definitions, under denied sockets.
    # No checkout/qualification/receipt is substituted for a real source proof.
    legacy = ModuleType("owned_legacy_rolling_release")
    monkeypatch.setitem(sys.modules, legacy.__name__, legacy)
    exec(compile(raw, "owned_legacy_rolling_release.py", "exec"), legacy.__dict__)
    old_plan = legacy.RollingPlan(
        **{field.name: getattr(plan, field.name) for field in fields(plan)}
    )
    body = r.stage_body(plan, plan.pin(EMPLOYER), fixture.service(EMPLOYER))
    assert "REDIS_URL" not in r.environment(body["template"])
    assert r.canonical(asdict(plan)) == legacy.canonical(asdict(old_plan))
    assert r.sha(asdict(plan)) == legacy.sha(asdict(old_plan))
    for pin in plan.services:
        assert r.canonical(
            r.stage_body(plan, pin, fixture.service(pin.service))
        ) == legacy.canonical(legacy.stage_body(old_plan, pin, fixture.service(pin.service)))


@pytest.mark.parametrize(
    "fault", ["finance", "iam", "resource", "secret", "target-hash", "generation", "template"]
)
def test_fresh_global_drift_stops_before_any_send(context, fault):
    c = context
    if fault == "finance":
        c.fixture.finance_drift = True
    elif fault == "iam":
        c.fixture.policies[API]["etag"] = "foreign-epoch"
    elif fault == "resource":
        c.boundary._resources = lambda _: ()
    elif fault == "secret":
        c.boundary._reference = lambda secret, version: "999"
    elif fault == "target-hash":
        pin = replace(c.plan.pin(EMPLOYER), candidate_template_sha256="f" * 64)
        c.plan = replace(
            c.plan, services=tuple(pin if p.service == EMPLOYER else p for p in c.plan.services)
        )
        c.journal = r.Journal(c.journal.directory, c.plan)
    elif fault == "generation":
        c.fixture.states[API]["generation"] = "43"
    else:
        c.fixture.states[API]["template"]["containers"][0]["env"].append(
            {"name": "UNREVIEWED", "value": "true"}
        )
    with pytest.raises(r.RollingDenied):
        s.execute("advance", c.plan, c.boundary, c.journal)
    assert not c.fixture.writes


def test_pending_and_unknown_ack_never_replay(context):
    c = context
    c.fixture.pause = True
    assert s.execute("advance", c.plan, c.boundary, c.journal)["rolling_release_complete"] is False
    assert len(c.fixture.writes) == 1
    s.execute("reconcile", c.plan, c.boundary, c.journal)
    assert len(c.fixture.writes) == 1
    for operation in c.fixture.operations.values():
        operation["done"] = True
    c.fixture.pause, c.fixture.lose_ack = False, True
    with pytest.raises(RuntimeError):
        s.execute("advance", c.plan, c.boundary, c.journal)
    writes = len(c.fixture.writes)
    with pytest.raises(r.RollingDenied, match="unknown_mutation"):
        s.execute("advance", c.plan, c.boundary, c.journal)
    s.execute("reconcile", c.plan, c.boundary, c.journal)
    assert len(c.fixture.writes) == writes


def test_empty_observation_modes_never_start_sends(context):
    for mode in ("observe", "reconcile"):
        assert (
            s.execute(mode, context.plan, context.boundary, context.journal)[
                "rolling_release_complete"
            ]
            is False
        )
        assert not context.fixture.writes


def test_broken_candidate_rollback_requires_only_prior_http(context):
    c = context
    s.execute("stage", c.plan, c.boundary, c.journal)

    def health(plan, service, revision, release_id, tag):
        assert release_id == c.plan.prior_source.release
        c.fixture.health(plan, service, revision, release_id, tag)

    c.boundary.health = health
    result = s.execute("rollback", c.plan, c.boundary, c.journal)
    assert result["rolling_release_complete"] is False
    assert all(mask == "traffic" for _, _, mask in c.fixture.writes[3:])
    with pytest.raises(r.RollingDenied, match="rollback"):
        s.execute("advance", c.plan, c.boundary, c.journal)
    writes = len(c.fixture.writes)
    s.execute("reconcile", c.plan, c.boundary, c.journal)
    s.execute("observe", c.plan, c.boundary, c.journal)
    assert len(c.fixture.writes) == writes


def test_owner_lock_refuses_concurrent_mutation(context):
    with context.journal.locked(), pytest.raises(BlockingIOError):
        s.execute("advance", context.plan, context.boundary, context.journal)
    assert not context.fixture.writes


def test_closed_plan_roundtrip_and_refusals(context):
    raw = r.canonical(asdict(context.plan))
    assert s.load(raw) == context.plan
    for changed in (
        {"database_schema": "20261028_0025"},
        {"schema_contract": s.app26.contract.CONTRACT},
        {"unreviewed": True},
        {"prior_source": None},
    ):
        with pytest.raises((r.RollingDenied, TypeError)):
            s.load(r.canonical(asdict(context.plan) | changed))
    with pytest.raises(r.RollingDenied, match="duplicate"):
        s.load(b'{"schema_contract":"x","schema_contract":"y"}')


@pytest.mark.parametrize(
    "fault",
    [
        "head",
        "repository",
        "event",
        "workflow",
        "job",
        "step",
        "missing-step",
        "v1-missing",
        "v1-skipped",
        "openapi-missing",
        "openapi-skipped",
    ],
)
def test_actual_ci_verifier_requires_exact_source_jobs_and_native_steps(fault):
    release = "a" * 40
    jobs = [
        {
            "name": name,
            "status": "completed",
            "conclusion": "success",
            "steps": [
                {"name": step, "status": "completed", "conclusion": "success"}
                for step in sorted(s.REQUIRED_STEPS)
            ]
            if name == "backend"
            else [],
        }
        for name in r.release.CI_JOBS
    ]
    ci = {
        "headSha": release,
        "workflowName": "CI",
        "status": "completed",
        "conclusion": "success",
        "jobs": jobs,
    }
    workflow = {
        "head_sha": release,
        "path": ".github/workflows/ci.yml",
        "event": "pull_request",
        "repository": {"full_name": r.release.REPOSITORY},
    }
    observer = SimpleNamespace(
        _command=lambda *args: json.dumps(ci if args[1] == "run" else workflow)
    )
    s._native_ci(observer, release, 1)
    if fault == "head":
        workflow["head_sha"] = "b" * 40
    elif fault == "repository":
        workflow["repository"]["full_name"] = "foreign/repo"
    elif fault == "event":
        workflow["event"] = "workflow_dispatch"
    elif fault == "workflow":
        workflow["path"] = ".github/workflows/foreign.yml"
    elif fault == "job":
        jobs[0]["conclusion"] = "failure"
    elif fault == "step":
        next(row for row in jobs if row["name"] == "backend")["steps"][0]["conclusion"] = "failure"
    elif fault in {"v1-missing", "v1-skipped", "openapi-missing", "openapi-skipped"}:
        name = (
            "Qualify retained actual-source schema25 app v1"
            if fault.startswith("v1")
            else "Check OpenAPI artifact drift"
        )
        steps = next(row for row in jobs if row["name"] == "backend")["steps"]
        target = next(row for row in steps if row["name"] == name)
        if fault.endswith("missing"):
            steps.remove(target)
        else:
            target["conclusion"] = "skipped"
    else:
        next(row for row in jobs if row["name"] == "backend")["steps"].pop()
    with pytest.raises(r.RollingDenied):
        s._native_ci(observer, release, 1)


def test_capture_provenance_refusal_precedes_secret_sql_and_native_resource_reads(
    context, monkeypatch
):
    c = context
    monkeypatch.setattr(s, "controller_source", lambda root: ("9" * 40, "8" * 64))

    def refuse(*args):
        raise r.RollingDenied("owned_invalid_source")

    c.boundary._verify_sources = refuse
    c.boundary._pin = lambda *args: pytest.fail("Native capture preceded source verification")
    with pytest.raises(r.RollingDenied, match="invalid_source"):
        c.boundary.capture(
            s.SourcePin(c.plan.release, c.plan.image_digest, c.plan.build_id, c.plan.ci_run_id),
            c.plan.prior_source,
            controller_ci_run_id=333,
            nonce=c.plan.nonce,
            observer_secret=c.plan.observer_secret,
            retired_migration_secret=c.plan.retired_migration_secret,
            workers_release=c.plan.workers_release,
        )


def test_pending_lro_observable_while_service_metadata_reconciling(context):
    c = context
    c.fixture.pause = True
    assert not s.execute("advance", c.plan, c.boundary, c.journal)["rolling_release_complete"]
    c.fixture.states[API]["reconciling"] = True
    c.fixture.states[API]["observedGeneration"] = "42"
    c.fixture.states[API]["trafficStatuses"] = list(c.plan.pin(API).traffic)
    c.boundary._database = lambda *args, **kwargs: pytest.fail(
        "Pending LRO mistaken for settled Service"
    )
    result = s.execute("reconcile", c.plan, c.boundary, c.journal)
    assert result["status"] == "same_native_operation_pending" and result["native_mutations"] == 0
    assert len(c.fixture.writes) == 1 and c.source_checks
    assert c.journal.read("stage." + API + ".checked") is None


def test_full_cache_reference_refused_before_source_or_native_capture(context, monkeypatch):
    c = context

    def forbidden(*args, **kwargs):
        pytest.fail("Malformed cache reference reached source or native capture")

    monkeypatch.setattr(s, "controller_source", forbidden)
    for name in ("_verify_sources", "_pin", "_database", "_resources", "service"):
        setattr(c.boundary, name, forbidden)
    with pytest.raises(r.RollingDenied, match="rolling_employer_cache_binding_invalid"):
        c.boundary.capture(
            s.SourcePin(c.plan.release, c.plan.image_digest, c.plan.build_id, c.plan.ci_run_id),
            c.plan.prior_source,
            controller_ci_run_id=c.plan.controller_ci_run_id,
            nonce=c.plan.nonce,
            observer_secret=c.plan.observer_secret,
            retired_migration_secret=c.plan.retired_migration_secret,
            workers_release=c.plan.workers_release,
            employer_cache_secret="owned-existing-redis:1",
        )
    assert not c.fixture.writes and not c.source_checks


def test_repeated_aliases_pin_each_reachable_revision_once_without_changing_traffic(context):
    c = context
    for service in (ANALYSIS, EMPLOYER):
        state = c.fixture.states[service]
        traffic = copy.deepcopy(state["traffic"])
        traffic.extend(
            {
                "type": "TRAFFIC_TARGET_ALLOCATION_TYPE_REVISION",
                "revision": traffic[0]["revision"],
                "tag": tag,
            }
            for tag in ("rolling-owned-first", "rolling-owned-second", "rolling-owned-third")
        )
        state["traffic"], state["trafficStatuses"] = traffic, copy.deepcopy(traffic)
    pins = tuple(
        replace(
            c.boundary._pin(pin.service, c.plan.prior_source),
            candidate_template_sha256=pin.candidate_template_sha256,
        )
        for pin in c.plan.services
    )
    original = copy.deepcopy(pins)
    reachable = c.boundary._reachable(pins, c.plan.prior_source)
    plan = replace(c.plan, services=pins, reachable_revisions=reachable)
    plan.validate()
    assert len(reachable) == 3
    assert {(row.service, row.revision) for row in reachable} == {
        (pin.service, pin.prior_revision) for pin in pins
    }
    assert pins == original
    for pin in pins:
        staged = r.stage_body(plan, pin, c.boundary.service(pin.service))
        assert all(row in staged["traffic"] for row in pin.traffic)
    with pytest.raises(r.RollingDenied, match="reachable_prior_pins_invalid"):
        replace(plan, reachable_revisions=(*reachable, reachable[0])).validate()


def test_distinct_reachable_revisions_remain_pinned_and_foreign_source_is_refused(context):
    c = context
    service = ANALYSIS
    pin = c.plan.pin(service)
    revision = service + "-00000-owned"
    value = copy.deepcopy(c.fixture.revisions[(service, pin.prior_revision)])
    value["name"] = r.PARENT + "/services/" + service + "/revisions/" + revision
    c.fixture.revisions[(service, revision)] = value
    state = c.fixture.states[service]
    traffic = copy.deepcopy(state["traffic"])
    traffic.append(
        {
            "type": "TRAFFIC_TARGET_ALLOCATION_TYPE_REVISION",
            "revision": revision,
            "tag": "retained-distinct",
        }
    )
    state["traffic"], state["trafficStatuses"] = traffic, copy.deepcopy(traffic)
    pins = tuple(
        c.boundary._pin(service, c.plan.prior_source) if row.service == service else row
        for row in c.plan.services
    )
    reachable = c.boundary._reachable(pins, c.plan.prior_source)
    replace(c.plan, services=pins, reachable_revisions=reachable).validate()
    assert len(reachable) == 4
    assert {row.revision for row in reachable if row.service == service} == {
        pin.prior_revision,
        revision,
    }
    r.environment(value)["APP_RELEASE"]["value"] = "f" * 40
    with pytest.raises(r.RollingDenied, match="reachable_prior_source_or_admission_changed"):
        c.boundary._reachable(pins, c.plan.prior_source)


def test_second_successor_preserves_existing_cache_and_prior_journal(context):
    c = context
    assert s.execute("advance", c.plan, c.boundary, c.journal)["rolling_release_complete"]
    previous = {path.name: path.read_bytes() for path in c.journal.directory.glob("*.json")}
    prior = s.SourcePin(c.plan.release, c.plan.image_digest, c.plan.build_id, c.plan.ci_run_id)
    pins = tuple(c.boundary._pin(name, prior) for name in r.release.SERVICES)
    second = replace(
        c.plan,
        release="c" * 40,
        nonce="d" * 32,
        prior_source=prior,
        services=pins,
        reachable_revisions=c.boundary._reachable(pins, prior),
        candidate_employer_cache=None,
    )
    second.validate()
    directory = c.journal.directory / "second-successor"
    directory.mkdir(mode=0o700)
    journal = r.Journal(directory, second)
    c.fixture.plan = second
    assert s.execute("advance", second, c.boundary, journal)["rolling_release_complete"]
    assert {path.name: path.read_bytes() for path in c.journal.directory.glob("*.json")} == previous
    for service, body, mask in c.fixture.writes[6:9]:
        assert mask == "template,traffic"
        assert ("REDIS_URL" in r.environment(body["template"])) is (service == EMPLOYER)


@pytest.mark.parametrize(
    "fault", ["wrong-target", "wrong-prior", "target-source-refused", "prior-source-refused"]
)
def test_source_roots_and_provenance_are_not_claimed_plan_substitutes(context, monkeypatch, fault):
    c = context
    release, prior = c.plan.release, c.plan.prior_source
    target_root, prior_root = c.journal.directory / "target", c.journal.directory / "prior"
    c.boundary.target_root, c.boundary.prior_root = target_root, prior_root
    calls = []

    def verify(label, **pin):
        calls.append(label)
        if fault == label + "-source-refused":
            raise r.RollingDenied("owned_source_refused")

    c.boundary.observer = SimpleNamespace(verify_source_image=lambda **pin: verify("target", **pin))
    c.boundary.prior_observer = SimpleNamespace(
        verify_source_image=lambda **pin: verify("prior", **pin)
    )
    monkeypatch.setattr(
        s,
        "controller_source",
        lambda root: (c.plan.controller_release, c.plan.controller_source_sha256),
    )
    monkeypatch.setattr(s, "_native_ci", lambda *args: None)
    monkeypatch.setattr(s, "verify_app_equivalence", lambda *args: None)
    monkeypatch.setattr(s, "verify_controller_app_imports", lambda *args: None)

    def git(root, *args):
        head = release if root == target_root else prior.release
        if (
            fault == "wrong-target"
            and root == target_root
            or fault == "wrong-prior"
            and root == prior_root
        ):
            head = "0" * 40
        return head.encode()

    monkeypatch.setattr(s, "_git", git)
    with pytest.raises(r.RollingDenied):
        c.boundary._verify_sources(
            c.plan.controller_release,
            c.plan.controller_source_sha256,
            c.plan.controller_ci_run_id,
            s.SourcePin(release, c.plan.image_digest, c.plan.build_id, c.plan.ci_run_id),
            prior,
        )
    assert len(calls) <= 2


@pytest.fixture
def owned_app_roots(tmp_path):
    roots = {name: tmp_path / name for name in ("controller", "target", "prior")}

    def commit(root):
        s._git(root, "add", "--all")
        s._git(
            root,
            "-c",
            "core.hooksPath=/dev/null",
            "-c",
            "commit.gpgsign=false",
            "-c",
            "user.name=Owned app-source control",
            "-c",
            "user.email=owned@example.invalid",
            "commit",
            "-qm",
            "Owned app-source proof",
        )

    for name, root in roots.items():
        root.mkdir(mode=0o700)
        s._git(root, "init", "-q")
        app = root / "backend/app"
        app.mkdir(parents=True)
        (app / "policy.py").write_text("POLICY = 'same-reviewed-target'\n")
        if name != "prior":
            (app / "pipeline.json").write_text('{"schema":1}\n')
        commit(root)
    return SimpleNamespace(**roots, commit=commit)


def test_equal_target_code_allows_distinct_prior_before_native_reads(owned_app_roots, monkeypatch):
    t = owned_app_roots
    s.verify_app_equivalence(t.controller, t.target)
    prior = s.SourcePin(
        s._git(t.prior, "rev-parse", "HEAD").decode().strip(),
        "sha256:" + "f" * 64,
        "22222222-2222-4222-8222-222222222222",
        111,
    )
    target = replace(
        prior, release=s._git(t.target, "rev-parse", "HEAD").decode().strip(), ci_run_id=222
    )
    boundary = object.__new__(s.NativeSuccessorBoundary)
    boundary.root, boundary.target_root, boundary.prior_root = t.controller, t.target, t.prior
    calls = []
    boundary.observer = SimpleNamespace(
        verify_source_image=lambda **pin: calls.append("target_source")
    )
    boundary.prior_observer = SimpleNamespace(
        verify_source_image=lambda **pin: calls.append("prior_source")
    )
    monkeypatch.setattr(s, "controller_source", lambda root: ("9" * 40, "8" * 64))
    monkeypatch.setattr(s, "verify_controller_app_imports", lambda root: None)
    monkeypatch.setattr(s, "_native_ci", lambda *args: calls.append("ci"))
    boundary._verify_sources("9" * 40, "8" * 64, 333, target, prior)
    assert calls == ["ci", "target_source", "ci", "prior_source", "ci"]
    assert not (t.prior / "backend/app/pipeline.json").exists()


@pytest.mark.parametrize(
    "fault",
    [
        "target-committed",
        "controller-committed",
        "target-dirty",
        "controller-dirty",
        "target-extra",
        "target-symlink",
    ],
)
def test_app_drift_refuses_before_native_ci_secret_or_sql(owned_app_roots, monkeypatch, fault):
    t = owned_app_roots
    root = t.controller if fault.startswith("controller") else t.target
    path = root / "backend/app/policy.py"
    if fault == "target-extra":
        (root / "backend/app/foreign.py").write_text("FOREIGN = True\n")
    elif fault == "target-symlink":
        outside = root / "outside.py"
        outside.write_text("POLICY = 'same-reviewed-target'\n")
        path.unlink()
        path.symlink_to(outside)
    else:
        path.write_text("POLICY = 'changed'\n")
    if not fault.endswith("dirty"):
        t.commit(root)
    boundary = object.__new__(s.NativeSuccessorBoundary)
    boundary.root, boundary.target_root, boundary.prior_root = t.controller, t.target, t.prior
    boundary.observer = boundary.prior_observer = SimpleNamespace(
        verify_source_image=lambda **pin: pytest.fail("Native source read preceded app equivalence")
    )
    monkeypatch.setattr(s, "controller_source", lambda root: ("9" * 40, "8" * 64))
    monkeypatch.setattr(
        s, "_native_ci", lambda *args: pytest.fail("Native CI read preceded app equivalence")
    )
    prior = s.SourcePin(
        s._git(t.prior, "rev-parse", "HEAD").decode().strip(),
        "sha256:" + "f" * 64,
        "22222222-2222-4222-8222-222222222222",
        111,
    )
    target = replace(
        prior, release=s._git(t.target, "rev-parse", "HEAD").decode().strip(), ci_run_id=222
    )
    with pytest.raises(r.RollingDenied, match="app_"):
        boundary._verify_sources("9" * 40, "8" * 64, 333, target, prior)


def test_cached_app_and_policy_origins_are_controller_bound(monkeypatch, tmp_path):
    s.verify_controller_app_imports(s.ROOT)
    foreign = ModuleType("app.billing.cost_policy")
    foreign.__file__ = str(tmp_path / "foreign_policy.py")
    monkeypatch.setitem(sys.modules, "app.billing.cost_policy", foreign)
    with pytest.raises(r.RollingDenied, match="app_origin"):
        s.verify_controller_app_imports(s.ROOT)


def test_fixed_failure_code_is_bounded_and_other_exception_text_redacted():
    assert (
        s.failure_projection(r.RollingDenied("schema26_successor_controller_source"))["fixed_code"]
        == "schema26_successor_controller_source"
    )

    class ForeignDenied(r.RollingDenied):
        pass

    for error in (
        RuntimeError("schema26_successor_secret_value"),
        ValueError("postgresql://private-value"),
        r.RollingDenied("provider-body private-value"),
        r.RollingDenied("schema26_successor_" + "x" * 200),
        ForeignDenied("schema26_successor_controller_source"),
    ):
        assert s.failure_projection(error) == {
            "status": "schema26_successor_stopped_reconcile_only",
            "replay_authorized": False,
        }


@pytest.fixture
def recovery_context(context, tmp_path, monkeypatch):
    c = context
    pins = []
    for pin in c.plan.services:
        traffic = r.pinned_traffic(
            pin.service,
            {
                "trafficStatuses": [
                    *pin.traffic,
                    {
                        "type": "TRAFFIC_TARGET_ALLOCATION_TYPE_REVISION",
                        "revision": pin.prior_revision,
                        "tag": "owned-original-alias",
                    },
                ]
            },
        )
        c.fixture.states[pin.service]["traffic"] = copy.deepcopy(traffic)
        c.fixture.states[pin.service]["trafficStatuses"] = copy.deepcopy(traffic)
        pins.append(replace(pin, traffic=tuple(traffic)))
    c.plan = replace(c.plan, services=tuple(pins))
    c.plan = replace(c.plan, reachable_revisions=c.boundary._reachable(pins, c.plan.prior_source))
    c.fixture.plan = c.plan
    original = tmp_path / "original"
    original.mkdir(mode=0o700)
    c.journal = r.Journal(original, c.plan)
    s.execute("stage", c.plan, c.boundary, c.journal)
    c.original_bytes = {p.name: p.read_bytes() for p in original.iterdir()}
    # Cloud Run revisions are immutable. The older shared fixture creates a
    # revision on every template PATCH; preserve an existing revision response
    # when the exact old revision name is reused by restoration.
    patch = c.fixture.patch

    def immutable_revision_patch(service, body, mask):
        retained = copy.deepcopy(c.fixture.revisions)
        try:
            return patch(service, body, mask)
        finally:
            for key, revision in retained.items():
                c.fixture.revisions[key] = revision

    c.boundary.patch = immutable_revision_patch
    monkeypatch.setattr(s, "controller_source", lambda _: ("7" * 40, "6" * 64))
    c.historical, c.ci = [], []
    monkeypatch.setattr(
        s, "historical_controller_source", lambda root, release: c.plan.controller_source_sha256
    )
    monkeypatch.setattr(
        s, "verify_historical_policy", lambda boundary, policies: c.historical.append(policies)
    )
    monkeypatch.setattr(s, "_native_ci", lambda boundary, release, ci: c.ci.append((release, ci)))
    c.original_plan_raw = r.canonical(asdict(c.plan)) + b"\n"
    c.recovery = s.capture_recovery(
        c.original_plan_raw, c.boundary, c.journal, controller_ci_run_id=555, nonce="5" * 32
    )
    recovery = tmp_path / "recovery"
    recovery.mkdir(mode=0o700)
    c.recovery_journal = r.Journal(recovery, c.recovery)
    return c


def test_recovery_restores_exact_templates_aliases_and_retains_original_journal(recovery_context):
    c = recovery_context
    revisions = set(c.fixture.revisions)
    result = s.execute_recovery(
        "restore",
        c.recovery,
        c.boundary,
        c.journal,
        c.recovery_journal,
        original_plan_raw=c.original_plan_raw,
    )
    assert result["all_original_templates_restored"] is True
    assert result["native_mutations"] == 3 and result["forward_continuation_authorized"] is False
    for service, body, mask in c.fixture.writes[3:]:
        pin = c.plan.pin(service)
        assert mask == "template,traffic"
        assert r.template_sha(body["template"], service) == pin.template_sha256
        assert body["traffic"] == list(pin.traffic)
        assert all(row.get("tag") != c.plan.tag for row in body["traffic"])
        assert (
            next(row for row in body["traffic"] if row.get("percent"))["revision"]
            == pin.prior_revision
        )
        assert all(
            r.environment(body["template"]).get(flag, {"value": "false"})["value"] == "false"
            for flag in c.plan.disabled_flags()
        )
    assert set(c.fixture.revisions) == revisions
    assert {p.name: p.read_bytes() for p in c.journal.directory.iterdir()} == c.original_bytes
    assert all(
        "synthetic-sensitive" not in p.read_text()
        for p in c.recovery_journal.directory.glob("*.json")
    )
    assert c.ci and c.historical
    writes = len(c.fixture.writes)
    for mode in ("restore", "reconcile"):
        s.execute_recovery(
            mode,
            c.recovery,
            c.boundary,
            c.journal,
            c.recovery_journal,
            original_plan_raw=c.original_plan_raw,
        )
    assert len(c.fixture.writes) == writes


def test_recovery_empty_reconcile_never_starts_sends(recovery_context):
    c = recovery_context
    result = s.execute_recovery(
        "reconcile",
        c.recovery,
        c.boundary,
        c.journal,
        c.recovery_journal,
        original_plan_raw=c.original_plan_raw,
    )
    assert result["all_original_templates_restored"] is False
    assert result["native_mutations"] == 0 and len(c.fixture.writes) == 3


def test_recovery_pending_and_unknown_outcomes_never_replay(recovery_context):
    c = recovery_context
    c.fixture.pause = True
    result = s.execute_recovery(
        "restore",
        c.recovery,
        c.boundary,
        c.journal,
        c.recovery_journal,
        original_plan_raw=c.original_plan_raw,
    )
    assert result["status"] == "same_native_operation_pending"
    count = len(c.fixture.writes)
    for mode in ("restore", "reconcile"):
        s.execute_recovery(
            mode,
            c.recovery,
            c.boundary,
            c.journal,
            c.recovery_journal,
            original_plan_raw=c.original_plan_raw,
        )
    assert len(c.fixture.writes) == count
    c.fixture.pause = False
    for operation in c.fixture.operations.values():
        operation["done"] = True
    c.fixture.lose_ack = True
    with pytest.raises(RuntimeError):
        s.execute_recovery(
            "restore",
            c.recovery,
            c.boundary,
            c.journal,
            c.recovery_journal,
            original_plan_raw=c.original_plan_raw,
        )
    count = len(c.fixture.writes)
    with pytest.raises(r.RollingDenied, match="unknown_mutation"):
        s.execute_recovery(
            "restore",
            c.recovery,
            c.boundary,
            c.journal,
            c.recovery_journal,
            original_plan_raw=c.original_plan_raw,
        )
    s.execute_recovery(
        "reconcile",
        c.recovery,
        c.boundary,
        c.journal,
        c.recovery_journal,
        original_plan_raw=c.original_plan_raw,
    )
    assert len(c.fixture.writes) == count
    s.execute_recovery(
        "restore",
        c.recovery,
        c.boundary,
        c.journal,
        c.recovery_journal,
        original_plan_raw=c.original_plan_raw,
    )
    assert len(c.fixture.writes) == 6


@pytest.mark.parametrize(
    "fault",
    [
        "journal",
        "ack",
        "pending",
        "promote",
        "traffic",
        "finance",
        "iam",
        "resource",
        "secret",
        "template",
        "prior",
    ],
)
def test_recovery_preserves_original_native_and_ownership_fences(recovery_context, fault):
    c = recovery_context
    if fault == "journal":
        path = c.journal.directory / ("stage." + API + ".checked.json")
        value = json.loads(path.read_text())
        value["generation"] += 1
        path.write_text(json.dumps(value))
    elif fault == "ack":
        (c.journal.directory / ("stage." + API + ".ack.json")).unlink()
    elif fault == "pending":
        next(iter(c.fixture.operations.values()))["done"] = False
    elif fault == "promote":
        c.journal.record("promote." + API + ".started", {"phase": "promote"})
    elif fault == "traffic":
        c.fixture.states[API]["trafficStatuses"].append(
            {
                "type": "TRAFFIC_TARGET_ALLOCATION_TYPE_REVISION",
                "revision": c.plan.pin(API).prior_revision,
                "tag": "foreign-alias",
            }
        )
    elif fault == "finance":
        c.fixture.finance_drift = True
    elif fault == "iam":
        c.fixture.policies[API]["etag"] = "foreign-policy"
    elif fault == "resource":
        c.boundary._resources = lambda _: ()
    elif fault == "secret":
        c.boundary._reference = lambda *args: "999"
    else:
        template = (
            c.fixture.states[API]["template"]
            if fault == "template"
            else c.fixture.revisions[(API, c.plan.pin(API).prior_revision)]
        )
        template["containers"][0]["env"].append({"name": "UNOWNED", "value": "true"})
    with pytest.raises(r.RollingDenied):
        s.execute_recovery(
            "restore",
            c.recovery,
            c.boundary,
            c.journal,
            c.recovery_journal,
            original_plan_raw=c.original_plan_raw,
        )
    assert len(c.fixture.writes) == 3


def test_recovery_refuses_both_journal_locks(recovery_context):
    c = recovery_context
    for journal in (c.journal, c.recovery_journal):
        with journal.locked(), pytest.raises(BlockingIOError):
            s.execute_recovery(
                "restore",
                c.recovery,
                c.boundary,
                c.journal,
                c.recovery_journal,
                original_plan_raw=c.original_plan_raw,
            )
    assert len(c.fixture.writes) == 3


def test_recovery_marker_tampering_cannot_authorize_next_send(recovery_context):
    c = recovery_context
    c.fixture.pause = True
    s.execute_recovery(
        "restore",
        c.recovery,
        c.boundary,
        c.journal,
        c.recovery_journal,
        original_plan_raw=c.original_plan_raw,
    )
    for op in c.fixture.operations.values():
        op["done"] = True
    path = c.recovery_journal.directory / ("restore." + API + ".started.json")
    value = json.loads(path.read_text())
    value["expected_generation"] += 1
    path.write_text(json.dumps(value))
    with pytest.raises(r.RollingDenied, match="restore_intent_changed"):
        s.execute_recovery(
            "reconcile",
            c.recovery,
            c.boundary,
            c.journal,
            c.recovery_journal,
            original_plan_raw=c.original_plan_raw,
        )
    assert len(c.fixture.writes) == 4


def test_recovery_serialization_is_closed_and_exact_original_file_bound(recovery_context):
    c = recovery_context
    assert s.load_recovery(r.canonical(asdict(c.recovery))) == c.recovery
    for change in (
        {"unreviewed": True},
        {"nonce": c.plan.nonce},
        {"schema_contract": s.KIND},
        {"original_plan_sha256": "0" * 64},
    ):
        with pytest.raises((r.RollingDenied, TypeError)):
            s.load_recovery(r.canonical(asdict(c.recovery) | change))
    with pytest.raises(r.RollingDenied, match="original_plan_file_changed"):
        s.execute_recovery(
            "restore",
            c.recovery,
            c.boundary,
            c.journal,
            c.recovery_journal,
            original_plan_raw=c.original_plan_raw + b"\n",
        )
    assert len(c.fixture.writes) == 3


def test_historical_expired_policy_is_not_a_current_funding_authority(context, monkeypatch):
    import hashlib

    from backend.tests.expense_policy_fixtures import estimated_expense_policy
    from backend.tests.model_cost_fixtures import synthetic_policy

    from app.billing.cost_policy import CostPolicyUnavailable, current_policy

    expense = estimated_expense_policy()
    expense.update(
        review_status="approved",
        reviewed_by="owned-reviewer",
        valid_from="2026-10-01T00:00:00Z",
        expires_at="2026-10-08T00:00:00Z",
        reconciled_through="2026-10-01T00:00:00Z",
    )
    model = synthetic_policy(ceiling=250000)
    raw_expense, raw_model = json.dumps(expense), json.dumps(model)
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("HIREWIZ_EXPENSE_POLICY_JSON", raw_expense)
    with pytest.raises(CostPolicyUnavailable):
        current_policy()
    metadata = []
    observer = SimpleNamespace(
        _secret=lambda ref: {"owned-expense:4": raw_expense, "owned-model:2": raw_model}[ref],
        _verify_secret_version=lambda ref: metadata.append(ref),
    )
    boundary = SimpleNamespace(observer=observer, root=context.boundary.root)
    monkeypatch.setattr(r.release, "_gemini_fallbacks", lambda _: ("gemini-3.6-flash",))
    policies = replace(
        context.plan.policies,
        expense_secret="owned-expense:4",
        expense_sha256=hashlib.sha256(raw_expense.encode()).hexdigest(),
        model_policy_secret="owned-model:2",
        model_policy_sha256=hashlib.sha256(raw_model.encode()).hexdigest(),
    )
    s.verify_historical_policy(boundary, policies)
    assert metadata == [policies.model_key_secret]
    with pytest.raises(r.RollingDenied, match="historical_expense_hash"):
        s.verify_historical_policy(boundary, replace(policies, expense_sha256="0" * 64))
    expense["provenance"] = "synthetic_test"
    raw_expense = json.dumps(expense)
    with pytest.raises(r.RollingDenied, match="historical_expense_approval"):
        s.verify_historical_policy(
            boundary,
            replace(policies, expense_sha256=hashlib.sha256(raw_expense.encode()).hexdigest()),
        )


def test_candidate_policy_pair_roundtrip_changes_only_reviewed_references(context, monkeypatch):
    c = context
    target = replace(
        c.plan.policies,
        expense_secret="owned-reviewed-expense:5",
        model_policy_secret="owned-reviewed-model:3",
    )
    plan = replace(
        c.plan,
        candidate_policies=target,
        services=tuple(replace(pin, candidate_template_sha256=None) for pin in c.plan.services),
    )
    pins = tuple(
        replace(
            pin,
            candidate_template_sha256=r.template_sha(
                r.stage_body(plan, pin, c.boundary.service(pin.service))["template"], pin.service
            ),
        )
        for pin in plan.services
    )
    plan = replace(plan, services=pins)
    assert s.load(r.canonical(asdict(plan))) == plan
    historical, funded = [], []
    monkeypatch.setattr(
        s, "verify_historical_policy", lambda boundary, policies: historical.append(policies)
    )
    c.boundary.observer.verify_policy_configuration = lambda policies: funded.append(policies)
    c.fixture.plan = plan
    journal = r.Journal(c.journal.directory, plan)
    s.execute("stage", plan, c.boundary, journal)
    assert historical and funded and all(value == target for value in funded)
    for service, body, _ in c.fixture.writes:
        env = r.environment(body["template"])
        assert env["HIREWIZ_EXPENSE_POLICY_JSON"]["valueSource"]["secretKeyRef"] == {
            "secret": "owned-reviewed-expense",
            "version": "5",
        }
        if service != EMPLOYER:
            assert env["LLM_MODEL_COST_POLICY_JSON"]["valueSource"]["secretKeyRef"] == {
                "secret": "owned-reviewed-model",
                "version": "3",
            }
            assert (
                env["LLM_API_KEY"]
                == r.environment(c.fixture.revisions[(service, plan.pin(service).prior_revision)])[
                    "LLM_API_KEY"
                ]
            )
    from app.billing.cost_policy import CostPolicyUnavailable

    c.boundary.observer.verify_policy_configuration = lambda _: (_ for _ in ()).throw(
        CostPolicyUnavailable("owned expired target")
    )
    count = len(c.fixture.writes)
    with pytest.raises(CostPolicyUnavailable):
        s.execute("advance", plan, c.boundary, journal)
    assert len(c.fixture.writes) == count


def test_historical_controller_identity_reads_only_finite_code_blobs(monkeypatch, tmp_path):
    import hashlib

    release = "2" * 40
    bodies = {
        "backend/scripts/schema26_app_successor.py": b"# owned old controller\n",
        "backend/app/example.py": b"# owned app source\n",
        ".github/workflows/ci.yml": b"name: owned\n",
    }
    ignored = (
        "backend/evaluation/heldout/oracle.json",
        "infra/outputs/result.json",
        "docs/note.md",
    )
    tree = []
    for name, body in {**bodies, **dict.fromkeys(ignored, b"MUST NOT READ")}.items():
        oid = hashlib.sha1(
            b"blob " + str(len(body)).encode() + b"\0" + body, usedforsecurity=False
        ).hexdigest()
        tree.append(b"100644 blob " + oid.encode() + b"\t" + name.encode())
    requested = []

    def git(root, *args):
        if args == ("ls-tree", "-r", "-z", release):
            return b"\0".join(tree) + b"\0"
        assert args[0] == "show" and args[1].startswith(release + ":")
        name = args[1].removeprefix(release + ":")
        requested.append(name)
        return bodies[name]

    monkeypatch.setattr(s, "_git", git)
    expected = r.sha({name: hashlib.sha256(body).hexdigest() for name, body in bodies.items()})
    assert s.historical_controller_source(tmp_path, release) == expected
    assert set(requested) == set(bodies)


@pytest.mark.parametrize(
    "expense,model",
    [
        ("owned:5", None),
        (None, "owned:3"),
        ("owned:latest", "owned:3"),
        ("owned:5", "https://foreign.example/key"),
    ],
)
def test_candidate_policy_pair_shape_refusal_precedes_any_native_read(context, expense, model):
    def forbidden(*args, **kwargs):
        pytest.fail("Malformed candidate pair reached source or provider checks")

    context.boundary._verify_sources = forbidden
    with pytest.raises(r.RollingDenied, match="candidate_policy"):
        context.boundary.capture(
            context.plan.prior_source,
            replace(context.plan.prior_source, release="4" * 40),
            controller_ci_run_id=555,
            nonce="5" * 32,
            observer_secret="owned-observer:2",
            retired_migration_secret="owned-retired:2",
            workers_release="3" * 40,
            candidate_expense_secret=expense,
            candidate_model_policy_secret=model,
        )
    assert not context.fixture.writes


def test_recovery_prior_health_failure_retains_ack_and_never_sends_next_restore(recovery_context):
    c = recovery_context
    health = c.boundary.health
    c.boundary.health = lambda *args: (_ for _ in ()).throw(
        r.RollingDenied("owned_prior_health_failed")
    )
    with pytest.raises(r.RollingDenied, match="prior_health_failed"):
        s.execute_recovery(
            "restore",
            c.recovery,
            c.boundary,
            c.journal,
            c.recovery_journal,
            original_plan_raw=c.original_plan_raw,
        )
    assert len(c.fixture.writes) == 4
    assert c.recovery_journal.read("restore." + API + ".ack") is not None
    assert c.recovery_journal.read("restore." + API + ".checked") is None
    c.boundary.health = health
    s.execute_recovery(
        "restore",
        c.recovery,
        c.boundary,
        c.journal,
        c.recovery_journal,
        original_plan_raw=c.original_plan_raw,
    )
    assert len(c.fixture.writes) == 6


def test_later_pending_restore_is_observed_before_checked_prior_joined_read(
    recovery_context, monkeypatch
):
    c = recovery_context
    patch = c.boundary.patch

    def pause_analysis(service, body, mask):
        c.fixture.pause = service == ANALYSIS
        return patch(service, body, mask)

    c.boundary.patch = pause_analysis
    result = s.execute_recovery(
        "restore",
        c.recovery,
        c.boundary,
        c.journal,
        c.recovery_journal,
        original_plan_raw=c.original_plan_raw,
    )
    assert result["status"] == "same_native_operation_pending" and result["service"] == ANALYSIS
    assert c.recovery_journal.read("restore." + API + ".checked") is not None
    assert c.recovery_journal.read("restore." + ANALYSIS + ".ack") is not None
    assert c.recovery_journal.read("restore." + EMPLOYER + ".started") is None
    ack = c.recovery_journal.read("restore." + ANALYSIS + ".ack")
    c.fixture.states[ANALYSIS]["reconciling"] = True
    count = len(c.fixture.writes)
    with monkeypatch.context() as proof:
        proof.setattr(
            s,
            "verify_recovery_global",
            lambda *args: pytest.fail("Joined Service shape was read before pending LRO"),
        )
        for mode in ("restore", "reconcile"):
            result = s.execute_recovery(
                mode,
                c.recovery,
                c.boundary,
                c.journal,
                c.recovery_journal,
                original_plan_raw=c.original_plan_raw,
            )
            assert result == {
                "status": "same_native_operation_pending",
                "service": ANALYSIS,
                "native_mutations": 0,
                "replay_authorized": False,
            }
            assert c.fixture.polls[-1] == ack["operation"]
    assert len(c.fixture.writes) == count == 5
    c.fixture.states[ANALYSIS]["reconciling"] = False
    c.fixture.operations[ack["operation"]]["done"] = True
    s.execute_recovery(
        "restore",
        c.recovery,
        c.boundary,
        c.journal,
        c.recovery_journal,
        original_plan_raw=c.original_plan_raw,
    )
    assert len(c.fixture.writes) == 6


@pytest.mark.parametrize(
    "expense_seconds,model_seconds,expected",
    [
        (14399, 86400, "expense"),
        (86400, 14399, "model"),
        (14400, 14400, None),
        (14401, 14401, None),
    ],
)
def test_forward_capture_requires_four_hour_exact_target_policy_window(
    context, monkeypatch, expense_seconds, model_seconds, expected
):
    import hashlib
    from datetime import UTC, datetime, timedelta

    from backend.tests.expense_policy_fixtures import estimated_expense_policy
    from backend.tests.model_cost_fixtures import synthetic_policy

    now = datetime(2026, 11, 5, tzinfo=UTC)

    class FixedClock(datetime):
        @classmethod
        def now(cls, tz=None):
            return now

    monkeypatch.setattr(s, "datetime", FixedClock)
    monkeypatch.setattr(r.release, "_gemini_fallbacks", lambda _: ("gemini-3.6-flash",))
    expense = estimated_expense_policy()
    expense.update(
        valid_from=now.isoformat(),
        expires_at=(now + timedelta(seconds=expense_seconds)).isoformat(),
        reconciled_through=now.isoformat(),
    )
    model = synthetic_policy(ceiling=250000)
    for quote in model["pricing_quotes"]:
        quote.update(
            valid_from=now.isoformat(),
            expires_at=(now + timedelta(seconds=model_seconds)).isoformat(),
        )
    raw_expense, raw_model = json.dumps(expense), json.dumps(model)
    observer = SimpleNamespace(
        _secret=lambda ref: {"owned-expense:5": raw_expense, "owned-model:3": raw_model}[ref]
    )
    boundary = SimpleNamespace(root=context.boundary.root, observer=observer)
    policies = replace(
        context.plan.policies,
        expense_secret="owned-expense:5",
        expense_sha256=hashlib.sha256(raw_expense.encode()).hexdigest(),
        model_policy_secret="owned-model:3",
        model_policy_sha256=hashlib.sha256(raw_model.encode()).hexdigest(),
    )
    if expected:
        with pytest.raises(
            r.RollingDenied, match="rollout_" + expected + "_window_below_four_hours"
        ):
            s.verify_rollout_window(boundary, policies)
    else:
        s.verify_rollout_window(boundary, policies)
    for field in ("expense_sha256", "model_policy_sha256"):
        with pytest.raises(r.RollingDenied, match="rollout_.*_hash"):
            s.verify_rollout_window(boundary, replace(policies, **{field: "0" * 64}))
