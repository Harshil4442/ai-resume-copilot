"""Owned synthetic promotion handoff; no provider, auth or SQL execution."""
from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import asdict

import pytest
from infra.gcp import schema27_api_identity_operator as rehearsal
from infra.gcp import schema27_api_identity_promotion as promotion
from infra.gcp import schema27_mixed_document_proof as documents
from infra.gcp import schema27_mixed_identity_profile as identities
from test_schema27_api_identity_operator import captured
from test_schema27_api_identity_operator import controlled as controlled  # noqa: F401


@pytest.fixture
def handoff(controlled, tmp_path, monkeypatch):
    controlled[1]["files"] = {"infra/gcp/schema27_api_identity_operator.py": promotion.REHEARSAL_CODE,
                              "infra/gcp/schema27_api_identity_readback.py": promotion.REHEARSAL_READER}
    plan, boundary, original, _ = captured(controlled, tmp_path)
    boundary.approve_document_invokers()
    rehearsal.stage(plan, boundary, original)
    stamp = {"release": "f" * 40, "sha256": "a" * 64,
             "promotion_sha256": hashlib.sha256(promotion.Path(promotion.__file__).read_bytes()).hexdigest(),
             "helpers_sha256": {name: hashlib.sha256((promotion.ROOT / name).read_bytes()).hexdigest()
                                for name in promotion.HANDOFF_HELPERS}}
    monkeypatch.setattr(promotion, "controller_source", lambda: copy.deepcopy(stamp))
    boundary.document_release = lambda: "a" * 40
    boundary.document_authorization = authorization
    successor = promotion.capture(boundary, plan, journal_directory=tmp_path / "successor")
    journal = rehearsal.Journal(tmp_path / "successor", successor, create=True)
    return successor, boundary, original, journal, stamp


def authorization(plan):
    profile = identities.StagedProfile(plan)
    policies = {name: copy.deepcopy(plan.handoff["callers"][name]) for name in identities.readback.DOCUMENTS}
    binary = dict.fromkeys(documents.prior.NativeAcceptance.__dataclass_fields__, "synthetic-component")
    binary.update(source_sha256={"synthetic-worker.py": "c" * 64}, contract_version=5, required_case_count=17)
    value = {"kind": documents.KIND, "identity_profile_sha256": profile.digest, "actual_policies": policies,
        "actual_policy_sha256": promotion.rolling.sha(policies), "actual_etags": {name: policy["etag"] for name, policy in policies.items()},
        "caller_authorization_verified": True, "binary_corpus_component": {"scope": "retained_document_binary_and_corpus",
            "acceptance": binary, "sha256": promotion.rolling.sha(binary)},
        "legacy_policy_components_sha256": {name: promotion.rolling.sha(profile.legacy_policy_component(name, policy))
            for name, policy in policies.items()}, "native_approval": False}
    return documents.validate_receipt(profile, value)


def test_new_handoff_is_separate_custody_and_promotes_only_api(handoff):
    plan, boundary, original, journal, _ = handoff
    reference = {p.name: p.read_bytes() for p in original.directory.glob("*.json")}
    assert promotion.Plan.from_dict(json.loads(json.dumps(asdict(plan)))) == plan
    assert promotion.promote(plan, boundary, journal)["phase"] == "promote"
    assert len(boundary.writes) == 2 and boundary.writes[-1][1] == "traffic"
    assert all(row["revision"] == plan.transition.candidate_revision for row in boundary.services[promotion.API]["traffic"])
    assert {p.name: p.read_bytes() for p in original.directory.glob("*.json")} == reference
    assert journal.plan_hash != original.plan_hash
    assert promotion.reconcile(plan, boundary, journal)["phase"] == "promote"
    assert len(boundary.writes) == 2


@pytest.mark.parametrize("promoted", [False, True])
def test_functional_rollback_before_or_after_promotion_keeps_actual_old_identity(handoff, promoted):
    plan, boundary, _, journal, _ = handoff
    if promoted:
        promotion.promote(plan, boundary, journal)
    promotion.rollback(plan, boundary, journal)
    assert boundary.services[promotion.API]["template"]["serviceAccount"] == plan.transition.old_api.email
    assert all(row["revision"] == plan.rollback_revision for row in boundary.services[promotion.API]["traffic"])
    assert not any(row.get("tag") == plan.transition.candidate_tag for row in boundary.services[promotion.API]["traffic"])
    writes = copy.deepcopy(boundary.writes)
    with pytest.raises(promotion.rolling.RollingDenied):
        promotion.promote(plan, boundary, journal)
    promotion.reconcile(plan, boundary, journal)
    assert boundary.writes == writes


@pytest.mark.parametrize("persisted", [False, True])
def test_unknown_promotion_is_never_replayed(handoff, persisted):
    plan, boundary, _, journal, _ = handoff
    boundary.lose_ack, boundary.persist_unknown = True, persisted
    with pytest.raises(RuntimeError, match="Synthetic acknowledgement"):
        promotion.promote(plan, boundary, journal)
    with pytest.raises(promotion.rolling.RollingDenied, match="started_reconcile_only"):
        promotion.promote(plan, boundary, journal)
    if persisted:
        promotion.reconcile(plan, boundary, journal)
        assert journal.read("promote.checked")["operation_ack_retained"] is False
    else:
        with pytest.raises(promotion.rolling.RollingDenied):
            promotion.reconcile(plan, boundary, journal)
    assert len(boundary.writes) == 2


def test_source_and_predecessor_tamper_refuse_before_new_patch(handoff):
    plan, boundary, original, journal, stamp = handoff
    stamp["sha256"] = "b" * 64
    before = len(boundary.writes)
    with pytest.raises(promotion.rolling.RollingDenied, match="controller_changed"):
        promotion.promote(plan, boundary, journal)
    assert len(boundary.writes) == before
    stamp["sha256"] = "a" * 64
    path = original.directory / "stage.checked.json"
    value = json.loads(path.read_bytes())
    value["retained"] = {}
    path.write_bytes(promotion.rolling.canonical(value))
    with pytest.raises(promotion.rolling.RollingDenied):
        promotion.promote(plan, boundary, journal)
    assert len(boundary.writes) == before


def test_identity_only_promotion_uses_fixed70_native_template_without_app_equivalence(handoff, monkeypatch):
    plan, boundary, _, journal, _ = handoff
    monkeypatch.setattr(promotion.source, "verify_app_equivalence", lambda *_: pytest.fail("identity-only has no app target"))
    result = promotion.promote(plan, boundary, journal)
    assert result["phase"] == "promote" and set(boundary.writes[-1][0]) == {"name", "etag", "traffic"}
    assert rehearsal.rolling.environment(boundary.services[promotion.API]["template"])["APP_RELEASE"]["value"] == rehearsal.APPLICATION_RELEASE


def test_changed_runtime_source_cannot_enter_identity_only_handoff(handoff, tmp_path):
    plan, boundary, _, _, _ = handoff
    for row in boundary.services[promotion.API]["template"]["containers"][0]["env"]:
        if row["name"] == "APP_RELEASE":
            row["value"] = "e" * 40
    with pytest.raises(promotion.rolling.RollingDenied, match="fixed70_source_changed"):
        promotion.capture(boundary, plan.predecessor, journal_directory=tmp_path / "foreign-source")
    assert len(boundary.writes) == 1


def test_stale_document_authorization_blocks_new_promotion_but_not_functional_rollback(handoff):
    plan, boundary, _, journal, _ = handoff
    boundary.document_authorization = lambda _: (_ for _ in ()).throw(RuntimeError("synthetic scanner proof unavailable"))
    with pytest.raises(RuntimeError, match="scanner proof unavailable"):
        promotion.promote(plan, boundary, journal)
    assert not journal.claimed("promote.started") and len(boundary.writes) == 1
    promotion.rollback(plan, boundary, journal)
    assert boundary.services[promotion.API]["template"]["serviceAccount"] == plan.transition.old_api.email
