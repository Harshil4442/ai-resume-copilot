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
