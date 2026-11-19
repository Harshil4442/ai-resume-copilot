"""Mixed principals on the real post27 controller with owned synthetic ports."""
from __future__ import annotations

import copy
import hashlib
from dataclasses import asdict, replace
from types import SimpleNamespace

import pytest
from infra.gcp import schema27_api_identity_promotion as promotion
from infra.gcp import schema27_mixed_app_successor as mixed
from infra.gcp import schema27_mixed_document_proof as documents
from infra.gcp import schema27_mixed_identity_profile as identities
from test_schema26_app_successor import context as context
from test_schema26_app_successor import no_network as no_network
from test_schema26_app_successor import owned_app_roots as owned_app_roots
from test_schema27_api_identity_operator import controlled as controlled
from test_schema27_api_identity_promotion import handoff as handoff
from test_schema27_app_successor import application as application
from test_schema27_continuity_bridge import lane as lane
from test_schema27_continuity_bridge_plan import current as current


@pytest.fixture
def profile(handoff):
    plan, boundary, _, journal, _ = handoff
    promotion.promote(plan, boundary, journal)
    return identities.from_promoted(plan, journal), boundary


def forward_receipt(profile):
    policies = {name: copy.deepcopy(profile.promotion.handoff["callers"][name])
                for name in identities.readback.DOCUMENTS}
    binary = dict.fromkeys(documents.prior.NativeAcceptance.__dataclass_fields__, "synthetic-component")
    binary.update(source_sha256={"synthetic-worker.py": "c" * 64}, contract_version=5, required_case_count=17)
    result = {"kind": documents.KIND, "identity_profile_sha256": profile.digest, "actual_policies": policies,
        "actual_policy_sha256": mixed.rolling.sha(policies), "actual_etags": {name: value["etag"] for name, value in policies.items()},
        "caller_authorization_verified": True,
        "binary_corpus_component": {"scope": "retained_document_binary_and_corpus", "acceptance": binary,
                                   "sha256": mixed.rolling.sha(binary)},
        "legacy_policy_components_sha256": {name: mixed.rolling.sha(profile.legacy_policy_component(name, value))
            for name, value in policies.items()}, "native_approval": False}
    return documents.validate_receipt(profile, result)


@pytest.fixture
def mixed_app(application, profile, tmp_path):
    original, principal_boundary = profile
    c = application
    boundary = object.__new__(mixed.NativeBoundary)
    boundary.__dict__.update(c.boundary.__dict__)
    boundary.identity_profile = original
    for pin in c.plan.services:
        c.fixture.states[pin.service]["uid"] = original.promotion.before["services"][pin.service]["uid"]
        account = original.identity(pin.service).email
        c.fixture.states[pin.service]["template"]["serviceAccount"] = account
        c.fixture.revisions[(pin.service, pin.prior_revision)]["serviceAccount"] = account
    pins = tuple(boundary._pin(service, c.plan.prior_source) for service in mixed.rolling.release.SERVICES)
    application_plan = replace(c.plan, services=pins, runtime_uid=original.transition.old_api.uid,
        reachable_revisions=boundary._reachable(pins, c.plan.prior_source))
    directory = tmp_path / "mixed-app"
    directory.mkdir(mode=0o700)
    location = mixed.scope.journal_location(SimpleNamespace(directory=directory))
    plan = mixed.Plan(application_plan, original, location)
    plan.validate()
    c.fixture.plan = plan
    identities_by_email = principal_boundary.identities

    def cloud(*args):
        if args[:3] == ("iam", "service-accounts", "describe"):
            identity = identities_by_email[args[3]]
            return {"email": identity["email"], "uniqueId": identity["uid"], "disabled": not identity["enabled"]}
        assert args[:4] == ("iam", "service-accounts", "keys", "list")
        return []
    boundary.observer._cloud = cloud
    boundary.observer.last_document_authorization = forward_receipt(original)
    boundary._sources = lambda actual: mixed.need(actual == plan, "owned_source_changed")
    return SimpleNamespace(plan=plan, boundary=boundary, fixture=c.fixture,
        journal=mixed.app.bridge._bind_new(plan, directory), source=c, identities=identities_by_email)


def run(value, mode):
    return mixed.execute(mode, value.plan, value.boundary, value.journal)


def test_distinct_mixed_plan_roundtrip_and_no_original_journal_adoption(mixed_app, application):
    c = mixed_app
    raw = mixed.rolling.canonical(asdict(c.plan))
    assert mixed.load(raw, hashlib.sha256(raw).hexdigest()) == c.plan
    with pytest.raises(mixed.rolling.RollingDenied):
        mixed.execute("stage", c.plan, c.boundary, application.journal)
    assert not c.fixture.writes


def test_later_application_rollout_retains_new_api_and_shared_workers_then_rolls_back(mixed_app):
    c = mixed_app
    initial = copy.deepcopy(c.fixture.states)
    run(c, "stage")
    assert len(c.fixture.writes) == 3
    for service, body, _ in c.fixture.writes:
        assert body["template"]["serviceAccount"] == c.plan.identities.identity(service).email
        assert body["template"]["containers"][0]["image"] == c.plan.image
    run(c, "advance")
    assert [service for service, _, mask in c.fixture.writes if mask == "traffic"] == [
        *mixed.rolling.release.SERVICES[1:], mixed.API]
    run(c, "rollback")
    for service in mixed.rolling.release.SERVICES:
        assert c.fixture.states[service]["template"]["serviceAccount"] == initial[service]["template"]["serviceAccount"]
    before = len(c.fixture.writes)
    run(c, "reconcile")
    assert len(c.fixture.writes) == before


@pytest.mark.parametrize("drift", ["api_uid", "worker_uid", "api_account", "worker_account", "acl", "liability"])
def test_identity_and_actual27_global_drift_refuse_before_patch(mixed_app, drift):
    c = mixed_app
    if drift in {"api_uid", "worker_uid"}:
        identity = c.plan.identities.transition.new_api if drift == "api_uid" else c.plan.identities.transition.old_api
        c.identities[identity.email]["uid"] = "9" * 21
    elif drift in {"api_account", "worker_account"}:
        service = mixed.API if drift == "api_account" else mixed.rolling.release.SERVICES[1]
        c.fixture.states[service]["template"]["serviceAccount"] = "foreign@invalid.test"
    elif drift == "acl":
        c.source.observed.retained_acl_sha256 = "f" * 64
    else:
        c.source.observed.protected_liabilities = (("foreign", "e" * 64, 1),)
    with pytest.raises(mixed.rolling.RollingDenied):
        run(c, "stage")
    assert not c.fixture.writes


def test_later_app_uses_unchanged_controller_target_equivalence_guard(owned_app_roots):
    assert mixed.NativeBoundary._verify_sources is mixed.app.NativeApp27Boundary._verify_sources
    roots = owned_app_roots
    mixed.app.successor.verify_app_equivalence(roots.controller, roots.target)
    (roots.target / "backend/app/policy.py").write_text("POLICY = 'different-combined-app'\n")
    roots.commit(roots.target)
    with pytest.raises(mixed.rolling.RollingDenied, match="controller_target_app_differ"):
        mixed.app.successor.verify_app_equivalence(roots.controller, roots.target)
