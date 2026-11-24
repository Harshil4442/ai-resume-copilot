"""Policy rollover uses owned ports only; no provider, SQL, or paid-model calls."""
from __future__ import annotations

import copy
import hashlib
from dataclasses import asdict, replace
from types import SimpleNamespace

import pytest
from infra.gcp import schema27_mixed_policy_successor as op
from test_schema27_mixed_app_successor import application as application
from test_schema27_mixed_app_successor import context as context
from test_schema27_mixed_app_successor import controlled as controlled
from test_schema27_mixed_app_successor import current as current
from test_schema27_mixed_app_successor import handoff as handoff
from test_schema27_mixed_app_successor import lane as lane
from test_schema27_mixed_app_successor import mixed_app as mixed_app
from test_schema27_mixed_app_successor import no_network as no_network
from test_schema27_mixed_app_successor import owned_app_roots as owned_app_roots
from test_schema27_mixed_app_successor import profile as profile


@pytest.fixture
def candidate(mixed_app, tmp_path, monkeypatch):
    c = mixed_app
    monkeypatch.setattr(op, "SOURCE70", c.plan.prior_source.release)
    monkeypatch.setattr(op, "SOURCE3F7", c.plan.release)
    target = replace(c.plan.policies,
        expense_secret=c.plan.policies.expense_secret.split(":")[0] + ":2",
        expense_sha256="5" * 64,
        model_policy_secret=c.plan.policies.model_policy_secret.split(":")[0] + ":2",
        model_policy_sha256="6" * 64)
    directory = tmp_path / "policy-successor"
    directory.mkdir(mode=0o700)
    location = op.scope.journal_location(SimpleNamespace(directory=directory))
    plan = op.Plan(c.plan.application, c.plan.identities, location, target, ())
    stamps = tuple(op.CandidateTemplate(pin.service, op.rolling.template_sha(
        op.stage_body(plan, pin, c.boundary.service(pin.service))["template"], pin.service))
        for pin in plan.services)
    plan = replace(plan, candidate_templates=stamps)
    plan.validate()
    boundary = object.__new__(op.NativeBoundary)
    boundary.__dict__.update(c.boundary.__dict__)
    boundary._sources = lambda actual: op.need(actual == plan, "synthetic_source_changed")
    historical, funded = [], []
    monkeypatch.setattr(op.app.successor, "verify_historical_policy",
                        lambda native, value: historical.append(value))
    boundary.observer.verify_policy_configuration = lambda value: funded.append(value)
    c.fixture.plan = plan
    return SimpleNamespace(plan=plan, boundary=boundary, fixture=c.fixture, c=c,
        journal=op.app.bridge._bind_new(plan, directory), historical=historical, funded=funded)


def run(c, mode):
    return op.execute(mode, c.plan, c.boundary, c.journal)


def test_v2_roundtrip_and_v1_remain_disjoint(candidate):
    c = candidate
    raw = op.rolling.canonical(asdict(c.plan))
    assert op.load(raw, hashlib.sha256(raw).hexdigest()) == c.plan
    assert c.plan.application.candidate_policies is None
    assert all(pin.candidate_template_sha256 is None for pin in c.plan.application.services)
    c.plan.application.validate()
    assert all(pin.candidate_template_sha256 for pin in c.plan.services)
    with pytest.raises(op.rolling.RollingDenied):
        op.v1.load(raw, hashlib.sha256(raw).hexdigest())
    old = op.rolling.canonical(asdict(c.c.plan))
    with pytest.raises(op.rolling.RollingDenied):
        op.load(old, hashlib.sha256(old).hexdigest())
    with pytest.raises(op.rolling.RollingDenied):
        op.execute("stage", c.plan, c.boundary, c.c.journal)
    assert not c.fixture.writes


def test_new_api_and_worker_templates_apply_only_reviewed_policy_pair(candidate):
    c = candidate
    originals = copy.deepcopy(c.fixture.states)
    assert run(c, "stage")["rolling_release_complete"] is False
    assert len(c.fixture.writes) == 3
    assert c.historical and all(value == c.plan.policies for value in c.historical)
    assert c.funded and all(value == c.plan.candidate_policies for value in c.funded)
    for service, body, _ in c.fixture.writes:
        before = op.rolling.environment(originals[service]["template"])
        after = op.rolling.environment(body["template"])
        assert body["template"]["serviceAccount"] == c.plan.identities.identity(service).email
        assert after["DATABASE_URL"] == before["DATABASE_URL"]
        assert after["HIREWIZ_EXPENSE_POLICY_JSON"]["valueSource"]["secretKeyRef"]["version"] == "2"
        if service != op.rolling.release.SERVICES[2]:
            assert after["LLM_MODEL_COST_POLICY_JSON"]["valueSource"]["secretKeyRef"]["version"] == "2"
            for name in ("LLM_API_KEY", "LLM_MODEL", "LLM_API_BASE"):
                assert after[name] == before[name]
        assert all(after.get(name, {"value": "false"})["value"] == "false" for name in c.plan.disabled_flags())
    result = run(c, "advance")
    assert result["rolling_release_complete"] is True
    assert result["schema"] == "20261105_0027"
    assert result["migration_authorized"] is False
    assert result["payment_activation_authorized"] is False
    assert len(c.fixture.writes) == 6
    for service in op.rolling.release.SERVICES:
        assert all(row["revision"] == c.plan.revision(service)
                   for row in c.fixture.states[service]["trafficStatuses"])
    assert not any("synthetic-sensitive" in file.read_text() for file in c.journal.directory.glob("*.json"))
    run(c, "rollback")
    for service in op.rolling.release.SERVICES:
        traffic = c.fixture.states[service]["trafficStatuses"]
        assert next(row["revision"] for row in traffic if row.get("percent")) == c.plan.pin(service).prior_revision
    with pytest.raises(op.rolling.RollingDenied, match="rollback"):
        run(c, "advance")


@pytest.mark.parametrize("fault", ["key", "model", "endpoint", "only_expense", "only_model", "foreign_secret",
                                  "missing_hash", "wrong_source"])
def test_v2_route_and_template_invariants_refuse(candidate, fault):
    c = candidate
    pair = c.plan.candidate_policies
    if fault == "key":
        pair = replace(pair, model_key_secret="foreign-key:1")
    elif fault == "model":
        pair = replace(pair, model_primary="gemini-another")
    elif fault == "endpoint":
        pair = replace(pair, model_api_base="https://foreign.example")
    elif fault == "only_expense":
        pair = replace(pair, model_policy_secret=c.plan.policies.model_policy_secret)
    elif fault == "only_model":
        pair = replace(pair, expense_secret=c.plan.policies.expense_secret)
    elif fault == "foreign_secret":
        pair = replace(pair, expense_secret="foreign-expense:2")
    plan = replace(c.plan, candidate_policies=pair)
    if fault == "missing_hash":
        plan = replace(plan, candidate_templates=())
    elif fault == "wrong_source":
        plan = replace(plan, application=replace(plan.application,
            source=replace(plan.source, release="7" * 40)))
    with pytest.raises(op.rolling.RollingDenied):
        plan.validate()
    assert not c.fixture.writes


@pytest.mark.parametrize("fault", ["catalog", "acl", "liability", "api_identity", "worker_identity", "admission"])
def test_real_schema27_and_identity_guards_remain_fresh(candidate, fault):
    c = candidate
    if fault == "catalog":
        c.c.source.observed.catalog_sha256 = "0" * 64
    elif fault == "acl":
        c.c.source.observed.retained_acl_sha256 = "0" * 64
    elif fault == "liability":
        c.c.source.observed.protected_liabilities = (("changed", "0" * 64, 1),)
    elif fault.endswith("identity"):
        service = op.v1.API if fault == "api_identity" else op.rolling.release.SERVICES[1]
        c.c.identities[c.plan.identities.identity(service).email]["uid"] = "9" * 21
    else:
        c.fixture.states[op.v1.API]["template"]["containers"][0]["env"].append(
            {"name": "OPTIONAL_AI_GENERATION_ENABLED", "value": "true"})
    with pytest.raises(op.rolling.RollingDenied):
        run(c, "stage")
    assert not c.fixture.writes


def test_current_candidate_expiry_refuses_further_stage_or_promotion(candidate):
    c = candidate
    run(c, "stage")
    writes = len(c.fixture.writes)

    def expired(value):
        assert value == c.plan.candidate_policies
        raise op.rolling.RollingDenied("synthetic_target_expired")

    c.boundary.observer.verify_policy_configuration = expired
    with pytest.raises(op.rolling.RollingDenied, match="target_expired"):
        run(c, "advance")
    assert len(c.fixture.writes) == writes


def test_pending_and_lost_ack_never_replay_or_promote(candidate):
    c = candidate
    c.fixture.pause = True
    assert run(c, "stage")["status"] == "same_native_operation_pending"
    assert len(c.fixture.writes) == 1
    run(c, "reconcile")
    assert len(c.fixture.writes) == 1
    for value in c.fixture.operations.values():
        value["done"] = True
    c.fixture.pause = False
    run(c, "reconcile")
    c.fixture.lose_ack = True
    with pytest.raises(RuntimeError):
        run(c, "stage")
    assert len(c.fixture.writes) == 2
    for mode in ("reconcile", "advance", "stage", "rollback"):
        with pytest.raises(op.rolling.RollingDenied, match="missing_native_ack"):
            run(c, mode)
    assert len(c.fixture.writes) == 2
    assert not any(mask == "traffic" for _, _, mask in c.fixture.writes)


@pytest.mark.parametrize("expense,model", [(None, "model:2"), ("expense:2", None),
                                         ("expense:latest", "model:2"), ("expense:2", "https://foreign.example")])
def test_malformed_capture_pair_refused_before_native_or_catalog_calls(candidate, expense, model):
    c = candidate

    def forbidden(*_args, **_kwargs):
        pytest.fail("Malformed pair reached native capture")

    c.boundary._verify_sources = forbidden
    with pytest.raises(op.rolling.RollingDenied, match="candidate_numeric_pair"):
        c.boundary.capture(c.plan.source, c.plan.prior_source,
            controller_ci_run_id=1, nonce="4" * 32, workers_release="5" * 40,
            journal_directory=c.journal.directory, candidate_expense_secret=expense,
            candidate_model_policy_secret=model)


@pytest.mark.parametrize("refusal", [None, "target_expired", "four_hour_window"])
def test_capture_requires_fresh_pair_and_four_hour_window_before_catalog(candidate, monkeypatch, tmp_path, refusal):
    c = candidate
    calls = []
    c.boundary.root = tmp_path
    monkeypatch.setattr(op.app.successor, "controller_source",
                        lambda root: (c.plan.controller_release, c.plan.controller_source_sha256))
    c.boundary._verify_sources = lambda *values: calls.append("source")
    c.boundary.observer._secret = lambda ref: "owned-policy-" + ref

    def funded(value):
        calls.append("funded")
        assert value.expense_secret == c.plan.candidate_policies.expense_secret
        assert value.model_policy_secret == c.plan.candidate_policies.model_policy_secret
        if refusal == "target_expired":
            raise op.rolling.RollingDenied("owned_target_expired")

    def window(boundary, value):
        calls.append("window")
        assert boundary is c.boundary
        assert value.expense_secret == c.plan.candidate_policies.expense_secret
        if refusal == "four_hour_window":
            raise op.rolling.RollingDenied("owned_four_hour_window")

    def catalog(database, nonce):
        calls.append("catalog")
        assert database == c.plan.database and nonce == c.plan.nonce
        return c.c.source.observed

    c.boundary.observer.verify_policy_configuration = funded
    monkeypatch.setattr(op.app.successor, "verify_rollout_window", window)
    monkeypatch.setattr(op.scope, "capture_database_pin", lambda nonce: c.plan.database)
    monkeypatch.setattr(op.app, "observe_catalog27", catalog)
    directory = tmp_path / "fresh-capture"
    directory.mkdir(mode=0o700)
    options = {"controller_ci_run_id": c.plan.controller_ci_run_id, "nonce": c.plan.nonce,
        "workers_release": c.plan.workers_release, "journal_directory": directory,
        "candidate_expense_secret": c.plan.candidate_policies.expense_secret,
        "candidate_model_policy_secret": c.plan.candidate_policies.model_policy_secret}
    if refusal:
        with pytest.raises(op.rolling.RollingDenied, match="owned_" + refusal):
            c.boundary.capture(c.plan.source, c.plan.prior_source, **options)
        assert "catalog" not in calls
    else:
        result = c.boundary.capture(c.plan.source, c.plan.prior_source, **options)
        result.validate()
        assert calls.index("funded") < calls.index("window") < calls.index("catalog")
        assert all(pin.candidate_template_sha256 is None for pin in result.application.services)
        assert all(pin.candidate_template_sha256 for pin in result.services)
    assert c.historical
    assert not c.fixture.writes


def test_diagnostic_preserves_only_fixed_new_prefix():
    expected = "schema27_mixed_policy_missing_native_ack_reconcile_only"
    result = op.diagnostic(op.rolling.RollingDenied(expected), "reconcile")
    assert result["denial_code"] == expected and result["native_approval"] is False
    for error in (RuntimeError("private provider payload must not appear"),
                  op.rolling.RollingDenied("schema27_mixed_policy_private/value")):
        assert op.diagnostic(error, "reconcile")["denial_code"] == "schema27_forward_operation_unavailable"
        assert "private" not in str(op.diagnostic(error, "reconcile"))


@pytest.mark.parametrize("fail_partial", [False, True])
def test_only_successful_immutable_source_tuple_is_cached_and_local_checks_repeat(candidate, monkeypatch,
                                                                                tmp_path, fail_partial):
    c = candidate
    boundary = object.__new__(op.NativeBoundary)
    boundary.root, boundary.target_root, boundary.prior_root = tmp_path, tmp_path / "target", tmp_path / "prior"
    boundary._verified_source_tuple = None
    calls, local, state = [], [], {"fail": fail_partial, "dirty": False}

    def local_checks(*values):
        local.append(values)
        op.need(not state["dirty"], "controller_source_changed")

    def provenance(**values):
        calls.append(("build", values["release"]))
        if state["fail"] and values["release"] == c.plan.prior_source.release:
            raise op.rolling.RollingDenied("owned_partial_provenance_failure")

    boundary._local_sources = local_checks
    boundary.observer = SimpleNamespace(root=boundary.target_root, _verify_source_image_provenance=provenance)
    boundary.prior_observer = SimpleNamespace(root=boundary.prior_root, _verify_source_image_provenance=provenance)
    monkeypatch.setattr(op.app.successor, "_native_ci", lambda observer, release, run: calls.append(("ci", release)))
    monkeypatch.setattr(op.app.native27.application27, "verify_native_ci",
                        lambda observer, run: calls.append(("family_ci", run)))
    args = (c.plan.controller_release, c.plan.controller_source_sha256, c.plan.controller_ci_run_id,
            c.plan.source, c.plan.prior_source)
    if fail_partial:
        with pytest.raises(op.rolling.RollingDenied, match="partial_provenance"):
            boundary._verify_sources(*args)
        assert boundary._verified_source_tuple is None
        state["fail"] = False
    boundary._verify_sources(*args)
    before, local_before = len(calls), len(local)
    assert boundary._verified_source_tuple is not None
    boundary._verify_sources(*args)
    assert len(calls) == before and len(local) == local_before + 1
    state["dirty"] = True
    with pytest.raises(op.rolling.RollingDenied, match="controller_source_changed"):
        boundary._verify_sources(*args)
    assert len(calls) == before
    state["dirty"] = False
    for changed in ((*args[:3], replace(args[3], image_digest="sha256:" + "0" * 64), args[4]),
                    (args[0], args[1], args[2] + 1, *args[3:])):
        with pytest.raises(op.rolling.RollingDenied, match="source_tuple_changed"):
            boundary._verify_sources(*changed)
    boundary.prior_root = tmp_path / "another-prior"
    with pytest.raises(op.rolling.RollingDenied, match="source_tuple_changed"):
        boundary._verify_sources(*args)
    assert len(calls) == before
