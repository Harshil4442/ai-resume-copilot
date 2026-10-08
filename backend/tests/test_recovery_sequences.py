"""V2 local-only proofs; all v1 tests remain unchanged."""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from backend.tests.test_recovery_authority import Case, case
from pydantic import ValidationError

from app.domains.recovery.contracts import Revocation
from app.domains.recovery.sequence_contracts import (
    Artifact,
    FillStep,
    Manifest,
    Observation,
    OutcomeDecision,
    SequenceBase,
    StepDecision,
    Target,
    nonce_hash,
    sequence_hash,
    typed_value_hash,
)
from app.domains.recovery.sequence_service import SequenceGuard, step_key
from app.domains.recovery.store import GuardDenied, GuardUnavailable


@dataclass
class SequenceCase:
    legacy: Case
    guard: SequenceGuard
    manifest: Manifest

    @property
    def store(self):
        return self.legacy.store

    @property
    def candidate(self):
        return self.legacy.candidate

    @property
    def device(self):
        return self.legacy.device

    def prepared(self, index=0, continuation=None):
        return self.guard.prepare(self.device, self.manifest.attempt_id, index,
                                  self.manifest.sequence_digest, continuation)

    def begun(self, permit, continuation=None):
        return self.guard.begin(self.device, permit["id"], self.manifest.context_digest,
                                self.manifest.step_digest(permit["index"]), continuation)

    def filled(self, decision, event_id=None):
        may_act = decision.may_act
        assert may_act is not None
        return self.guard.outcome(self.device, self.manifest.attempt_id,
            observation(self.manifest, may_act.index, may_act.permit_id, may_act.begun_sequence),
            event_id or str(uuid4()), may_act.decision_nonce)


def observation(manifest, index, permit_id, begun_sequence, status="LOCAL_FILLED") -> Observation:
    return Observation(context_digest=manifest.context_digest, index=index, permit_id=UUID(str(permit_id)),
        begun_sequence=begun_sequence, document_id=manifest.base.target.document_id,
        value_sha256=manifest.steps[index].value_sha256, status=status)


def sequence_case(path: Path, *, count=4, approved=True) -> SequenceCase:
    legacy = case(path)
    binding = legacy.binding
    base = binding.model_dump(mode="json", exclude={"artifact_sha256", "artifact_generation",
        "origin", "action", "field_id", "value_sha256", "review_digest"})
    model = SequenceBase(**base, artifact=Artifact(sha256=binding.artifact_sha256,
        generation=binding.artifact_generation, descriptor_sha256="a" * 64, size_bytes=100,
        media_type="application/pdf"), target=Target(origin=binding.origin, url_sha256="b" * 64,
        tab_id=10, frame_id=0, document_id="synthetic-document", adapter_id="synthetic-v2",
        adapter_revision="rev-1", form_version="form-1", form_sha256="c" * 64),
        review_payload_sha256="d" * 64, acknowledgement_version="fill-autosave-v2")
    steps = tuple(FillStep(index=index, step_id=f"step-{index}", field_id=f"field-{index}",
        action="fill", control_type="checkbox" if index == count-1 else "text",
        value_type="boolean" if index == count-1 else "string", field_descriptor_sha256="e" * 64,
        value_sha256=typed_value_hash(True if index == count-1 else f"Synthetic value {index}"),
        expected_before_sha256=typed_value_hash(False if index == count-1 else "")) for index in range(count))
    guard = SequenceGuard(legacy.store, lambda: legacy.clock[0])
    manifest = guard.seal(legacy.candidate, model, steps)
    ctx = SequenceCase(legacy, guard, manifest)
    if approved:
        approve(ctx, manifest)
    return ctx


def approve(ctx, manifest):
    challenge = ctx.guard.challenge(ctx.candidate, manifest.attempt_id, manifest.review_digest)
    return ctx.guard.approve(ctx.candidate, manifest.attempt_id, challenge["id"], manifest.review_digest)


@pytest.fixture
def seq(tmp_path):
    return sequence_case(tmp_path / "independent-authority.sqlite")


def test_four_fields_have_fresh_decisions_and_end_locally_filled(seq):
    continuation = None
    nonces = set()
    for index in range(len(seq.manifest.steps)):
        permit = seq.prepared(index, continuation)
        assert permit["status"] == "PREPARED" and "may_act" not in permit
        begun = seq.begun(permit, continuation)
        assert begun.may_act is not None
        nonces.add(begun.may_act.decision_nonce)
        outcome = seq.filled(begun)
        continuation = outcome.continuation_nonce
        assert outcome.next_index == index + 1
    status = seq.guard.status(seq.device, seq.manifest.attempt_id)
    assert status["state"] == "LOCAL_SEQUENCE_FILLED" and status["next_index"] == 4
    assert len(nonces) == 4 and continuation is None
    assert "receipt" not in status


@pytest.mark.parametrize("value,expected", [
    (True, "b7b0c96cfc2e10d5154aec5a8a89752a1dd3a0f53e84013c6af18ec8cbf9cb3f"),
    (False, "ff8ae5a1cb1fea09d576f726024f4857e03f9118f9bd8435bfb1d83bc67f734a"),
    ("true", "7a8ff14ff5eac867d771915c0ff0e4df9ac789a9975be6eb2bd77daf118eadb6"),
    ("", "7b435c1e2e7b5dfc29cbdb4a87868a636f42f2a68d1b21345f87906edd87f44b"),
    ('café🙂\n"\\', "87711379f0b4a249f59eef28e302bc5d6c66363fcb834b7e3574ab4e1c68ee9a"),
    ("e\u0301", "58a61b1374bf1affaf02e56e561ef153bd0481d23a167e555c308ddd671eb576"),
    ("é", "1628ab604ad218445cf5d6a2a297b02eb919ba492d937848f7d5b1573501117f"),
])
def test_canonical_typed_value_vectors_match_independent_javascript(value, expected):
    # Constants independently calculated with Node crypto and ASCII-key canonical
    # JSON, including escaped text, astral Unicode and intentionally distinct NFC/NFD.
    assert typed_value_hash(value) == expected


@pytest.mark.parametrize("value", [0, 1, 1.0, None, [], {}, "\ud800"])
def test_typed_values_reject_coercion_and_lone_surrogates(value):
    with pytest.raises((ValueError, UnicodeError)):
        typed_value_hash(value)


@pytest.mark.parametrize("value", [True, "1", 1.0, 2**53])
@pytest.mark.parametrize("field", ["epoch_generation", "approval_revision", "grant_revision"])
def test_all_protocol_revisions_are_strict_js_safe_integers(seq, field, value):
    data = seq.manifest.base.model_dump(mode="json")
    with pytest.raises(ValidationError):
        SequenceBase.model_validate({**data, field: value})
    assert SequenceBase.model_validate({**data, field: 2**53 - 1}).model_dump(mode="json")[field] == 2**53 - 1


@pytest.mark.parametrize("field,value", [("tab_id", 2**53), ("tab_id", True),
    ("tab_id", 10.0), ("frame_id", False), ("frame_id", 0.0)])
def test_browser_numeric_bindings_are_strict_and_bounded(seq, field, value):
    with pytest.raises(ValidationError):
        Target.model_validate({**seq.manifest.base.target.model_dump(mode="json"), field: value})


@pytest.mark.parametrize("value", ["2", 2.0, True, 1])
def test_protocol_version_is_an_exact_v2_integer(seq, value):
    with pytest.raises(ValidationError):
        Manifest.model_validate({**seq.manifest.model_dump(mode="json"), "protocol_version": value})


@pytest.mark.parametrize("value", [2**53, True, 1.0, "1"])
def test_observation_event_references_are_js_safe(seq, value):
    with pytest.raises(ValidationError):
        observation(seq.manifest, 0, uuid4(), value)


def test_generation_is_a_canonical_string_and_canonical_hash_rejects_ambiguous_data(seq):
    artifact = seq.manifest.base.artifact.model_dump(mode="json")
    generation = "9876543210987654321098765432109"
    assert Artifact.model_validate({**artifact, "generation": generation}).generation == generation
    for value in (7, "07", "0", "+7", "1.0"):
        with pytest.raises(ValidationError):
            Artifact.model_validate({**artifact, "generation": value})
    for data in ({"value": 2**53}, {"value": 1.0}, {"é": 1}, {"value": "\udfff"}):
        with pytest.raises((ValueError, UnicodeError)):
            sequence_hash("context", data)
    assert sequence_hash("context", {"b": [True, None, 2**53 - 1], "a": generation}) == sequence_hash(
        "context", {"a": generation, "b": [True, None, 2**53 - 1]})


@pytest.mark.parametrize("mutation", ["skip", "repeat-field", "repeat-step", "reorder", "zero", "nine", "upload", "submit", "untyped-checkbox"])
def test_manifest_is_static_contiguous_unique_fill_only(seq, mutation):
    data = seq.manifest.model_dump(mode="json")
    if mutation == "skip":
        data["steps"][1]["index"] = 2
    elif mutation == "repeat-field":
        data["steps"][1]["field_id"] = data["steps"][0]["field_id"]
    elif mutation == "repeat-step":
        data["steps"][1]["step_id"] = data["steps"][0]["step_id"]
    elif mutation == "reorder":
        data["steps"] = list(reversed(data["steps"]))
    elif mutation == "zero":
        data["steps"] = []
    elif mutation == "nine":
        data["steps"] = [dict(data["steps"][0], index=index, step_id=f"step-{index}", field_id=f"field-{index}") for index in range(9)]
    elif mutation == "untyped-checkbox":
        data["steps"][0]["control_type"] = "checkbox"
    else:
        data["steps"][0]["action"] = mutation
    with pytest.raises(ValidationError):
        Manifest.model_validate(data)


@pytest.mark.parametrize("path", ["artifact:sha256", "artifact:generation", "artifact:descriptor_sha256",
    "artifact:size_bytes", "target:url_sha256", "target:tab_id", "target:document_id", "target:adapter_revision",
    "target:form_version", "target:form_sha256", "step:field_descriptor_sha256", "step:expected_before_sha256",
    "step:value_sha256", "review_payload_sha256", "acknowledgement_version"])
def test_new_exact_file_form_field_and_review_bindings_change_all_contexts(seq, path):
    data = seq.manifest.model_dump(mode="json")
    if ":" in path:
        container, field = path.split(":")
        target = data["steps"][0] if container == "step" else data["base"][container]
    else:
        target, field = data["base"], path
    previous = target[field]
    target[field] = previous + 1 if type(previous) is int else ("f" * 64 if len(previous) == 64 else previous + "2")
    changed = Manifest.model_validate(data)
    assert changed.sequence_digest != seq.manifest.sequence_digest
    assert changed.review_digest != seq.manifest.review_digest
    assert changed.context_digest != seq.manifest.context_digest
    # Later step digest also binds every earlier/static change through the context.
    assert changed.step_digest(1) != seq.manifest.step_digest(1)
    permit = seq.prepared()
    with pytest.raises(GuardDenied, match="Exact step binding"):
        seq.guard.begin(seq.device, permit["id"], changed.context_digest, changed.step_digest(0))
    assert seq.store.transact(lambda tx: tx.get("claims", seq.manifest.opening_claim_key)) is None


def test_unconfigured_production_adapter_never_uses_the_local_fixture(seq, monkeypatch):
    monkeypatch.setenv("RECOVERY_AUTHORITY_SQLITE_PATH", str(seq.store.path))
    monkeypatch.setenv("RECOVERY_GUARD_ENABLED", "true")
    with pytest.raises(GuardUnavailable, match="not implemented"):
        SequenceGuard().seal(seq.candidate, seq.manifest.base, seq.manifest.steps)


def test_seal_is_non_authorizing_and_requires_fresh_exact_review_challenge(tmp_path):
    ctx = sequence_case(tmp_path / "authority.sqlite", approved=False)
    assert ctx.guard.status(ctx.device, ctx.manifest.attempt_id)["state"] == "SEALED_DRAFT"
    assert ctx.store.transact(lambda tx: tx.get("claims", ctx.manifest.opening_claim_key)) is None
    with pytest.raises(GuardDenied, match="approval"):
        ctx.prepared()
    with pytest.raises(GuardDenied, match="Fresh exact candidate challenge"):
        ctx.guard.approve(ctx.candidate, ctx.manifest.attempt_id, "restored-app-approval", ctx.manifest.review_digest)
    with pytest.raises(GuardDenied, match="Exact presealed review"):
        ctx.guard.challenge(ctx.candidate, ctx.manifest.attempt_id, "f" * 64)
    challenge = ctx.guard.challenge(ctx.candidate, ctx.manifest.attempt_id, ctx.manifest.review_digest)
    assert challenge["expires_at_ms"] <= ctx.manifest.base.deadline_ms
    with pytest.raises(GuardDenied, match="Fresh exact candidate challenge"):
        ctx.guard.approve(ctx.candidate, ctx.manifest.attempt_id, challenge["id"], "f" * 64)
    ctx.guard.approve(ctx.candidate, ctx.manifest.attempt_id, challenge["id"], ctx.manifest.review_digest)
    with pytest.raises(GuardDenied, match="Fresh exact candidate challenge"):
        ctx.guard.approve(ctx.candidate, ctx.manifest.attempt_id, challenge["id"], ctx.manifest.review_digest)
    assert ctx.prepared()["status"] == "PREPARED"


@pytest.mark.parametrize("change", ["expired", "candidate-key", "epoch"])
def test_challenge_expiry_key_rotation_or_epoch_change_cannot_approve(tmp_path, change):
    ctx = sequence_case(tmp_path / "authority.sqlite", approved=False)
    challenge = ctx.guard.challenge(ctx.candidate, ctx.manifest.attempt_id, ctx.manifest.review_digest)
    candidate = ctx.candidate
    if change == "expired":
        ctx.legacy.clock[0] += 10_000
    elif change == "epoch":
        ctx.legacy.guard.close(ctx.legacy.operator, "epoch-change")
    else:
        candidate = candidate.model_copy(update={"key_sha256": "f" * 64})
        ctx.store.transact(lambda tx: tx.put("actors", candidate.actor_id,
            {"identity": candidate.model_dump(mode="json"), "active": True}))
    with pytest.raises(GuardDenied):
        ctx.guard.approve(candidate, ctx.manifest.attempt_id, challenge["id"], ctx.manifest.review_digest)
    assert ctx.store.transact(lambda tx: tx.get("sequence_approvals", ctx.manifest.base.approval_id)) is None


@pytest.mark.parametrize("field,value", [("actor_id", "other-device"), ("subject_uuid", UUID(int=1)),
    ("device_id", "other-device"), ("key_sha256", "f" * 64), ("executor_revision", "v3")])
def test_caller_identity_cannot_change_between_prepare_and_begin(seq, field, value):
    permit = seq.prepared()
    changed = seq.device.model_copy(update={field: value})
    with pytest.raises(GuardDenied):
        seq.guard.begin(changed, permit["id"], seq.manifest.context_digest, seq.manifest.step_digest(0))
    assert seq.store.transact(lambda tx: tx.get("claims", seq.manifest.opening_claim_key)) is None


@pytest.mark.parametrize("change", ["candidate", "device", "subject", "approval", "grant", "artifact", "epoch"])
def test_current_authority_and_tombstones_are_rechecked_for_each_next_step(seq, change):
    completed = seq.filled(seq.begun(seq.prepared()))
    continuation = completed.continuation_nonce
    assert continuation is not None
    permit = seq.prepared(1, continuation)
    if change == "candidate":
        identity = seq.candidate.model_copy(update={"key_sha256": "f" * 64})
        seq.store.transact(lambda tx: tx.put("actors", identity.actor_id,
            {"identity": identity.model_dump(mode="json"), "active": True}))
    elif change == "epoch":
        seq.legacy.guard.close(seq.legacy.operator, "epoch-change")
    elif change == "approval":
        seq.guard.revoke_approval(seq.candidate, seq.manifest.attempt_id, "revoke-approval")
    else:
        base = seq.manifest.base
        targets = {"device": (base.device_id, 0), "subject": (str(base.subject_uuid), 0),
            "approval": (base.approval_id, base.approval_revision), "grant": (base.grant_id, base.grant_revision),
            "artifact": (base.artifact.sha256, int(base.artifact.generation))}
        target, revision = targets[change]
        seq.legacy.guard.revoke(seq.legacy.operator, Revocation(subject_uuid=base.subject_uuid,
            kind=change, target=target, revision=revision), f"revoke-{change}")
    with pytest.raises(GuardDenied):
        seq.begun(permit, continuation)
    status = seq.guard.status(seq.device, seq.manifest.attempt_id)
    assert status["steps"][1]["status"] == "UNBEGUN" and status["next_index"] == 1


def test_contiguous_prefix_and_one_time_continuation_are_required(seq):
    with pytest.raises(GuardDenied):
        seq.prepared(1)
    with pytest.raises(GuardDenied):
        seq.prepared(0, uuid4())
    first = seq.begun(seq.prepared())
    with pytest.raises(GuardDenied):
        seq.prepared(0)
    with pytest.raises(GuardDenied):
        seq.prepared(1)
    outcome = seq.filled(first)
    continuation = outcome.continuation_nonce
    assert continuation is not None
    for index, nonce in ((0, None), (1, None), (1, uuid4()), (2, continuation), (True, continuation)):
        with pytest.raises(GuardDenied):
            seq.prepared(index, nonce)
    second = seq.prepared(1, continuation)
    with pytest.raises(GuardDenied, match="acknowledgement"):
        seq.begun(second, uuid4())
    begun = seq.begun(second, continuation)
    assert begun.may_act is not None
    assert seq.filled(begun).continuation_nonce != continuation
    with pytest.raises(GuardDenied):
        seq.prepared(2, continuation)


def test_completion_requires_actual_winning_response_and_duplicate_is_status_only(seq):
    permit = seq.prepared()
    begun = seq.begun(permit)
    proof = begun.may_act
    assert proof is not None
    o = observation(seq.manifest, 0, proof.permit_id, proof.begun_sequence)
    for nonce in (None, uuid4()):
        with pytest.raises(GuardDenied, match="Winning begin response"):
            seq.guard.outcome(seq.device, seq.manifest.attempt_id, o, "local-outcome", nonce)
    outcome = seq.guard.outcome(seq.device, seq.manifest.attempt_id, o, "local-outcome", proof.decision_nonce)
    assert outcome.continuation_nonce is not None
    for event_id in ("local-outcome", "duplicate-with-new-id"):
        duplicate = seq.guard.outcome(seq.device, seq.manifest.attempt_id, o, event_id, proof.decision_nonce)
        assert duplicate.continuation_nonce is None and duplicate.status == "LOCAL_FILLED"
    replay = seq.begun(permit)
    assert replay.status == "LOCAL_FILLED" and replay.may_act is None
    step = seq.store.transact(lambda tx: tx.get("sequence_steps", step_key(seq.manifest.attempt_id, 0)))
    assert step["decision_hash"] == nonce_hash(proof.decision_nonce)
    assert step["continuation_hash"] == nonce_hash(outcome.continuation_nonce)
    assert "decision_nonce" not in step and "continuation_nonce" not in step
    status = seq.guard.status(seq.device, seq.manifest.attempt_id)
    assert "decision_hash" not in str(status) and "continuation_hash" not in str(status)


@pytest.mark.parametrize("field,value", [("context_digest", "f" * 64), ("index", 1),
    ("permit_id", UUID(int=1)), ("begun_sequence", 1), ("document_id", "different-document"),
    ("value_sha256", "f" * 64)])
def test_observation_cannot_claim_another_value_document_or_step(seq, field, value):
    begun = seq.begun(seq.prepared())
    proof = begun.may_act
    assert proof is not None
    o = observation(seq.manifest, 0, proof.permit_id, proof.begun_sequence)
    changed = Observation.model_validate({**o.model_dump(mode="json"), field: value})
    with pytest.raises(GuardDenied):
        seq.guard.outcome(seq.device, seq.manifest.attempt_id, changed, "different-observation", proof.decision_nonce)
    assert seq.guard.status(seq.device, seq.manifest.attempt_id)["steps"][0]["status"] == "BEGUN"


def test_unknown_and_cancellation_never_reopen_or_issue_continuation(seq):
    permit = seq.prepared()
    proof = seq.begun(permit).may_act
    assert proof is not None
    unknown = observation(seq.manifest, 0, proof.permit_id, proof.begun_sequence, "UNKNOWN")
    outcome = seq.guard.outcome(seq.device, seq.manifest.attempt_id, unknown, "unknown")
    assert outcome.continuation_nonce is None
    with pytest.raises(GuardDenied, match="Terminal outcome"):
        seq.filled(StepDecision(status="BEGUN", may_act=proof))
    seq.guard.cancel(seq.candidate, seq.manifest.attempt_id, "cancel-after-unknown")
    for index in (0, 1):
        with pytest.raises(GuardDenied):
            seq.prepared(index)
    assert seq.begun(permit).status == "UNKNOWN"
    assert seq.guard.status(seq.device, seq.manifest.attempt_id)["state"] == "CANCELLED"
    assert seq.store.transact(lambda tx: tx.get("claims", seq.manifest.opening_claim_key)) is not None


@pytest.mark.parametrize("stop", ["cancel", "epoch", "candidate"])
def test_late_local_observation_can_record_facts_but_cannot_unlock(seq, stop):
    permit = seq.prepared()
    begun = seq.begun(permit)
    if stop == "cancel":
        seq.guard.cancel(seq.candidate, seq.manifest.attempt_id, "cancel")
    elif stop == "epoch":
        seq.legacy.guard.close(seq.legacy.operator, "epoch-close")
    else:
        identity = seq.candidate.model_copy(update={"key_sha256": "f" * 64})
        seq.store.transact(lambda tx: tx.put("actors", identity.actor_id,
            {"identity": identity.model_dump(mode="json"), "active": True}))
    outcome = seq.filled(begun)
    assert outcome.continuation_nonce is None
    assert seq.guard.status(seq.device, seq.manifest.attempt_id)["state"] == ("CANCELLED" if stop == "cancel" else "HANDOFF")
    with pytest.raises(GuardDenied):
        seq.prepared(1)


def test_original_deadline_never_extends_for_a_new_step(seq):
    continuation = seq.filled(seq.begun(seq.prepared())).continuation_nonce
    assert continuation is not None
    seq.legacy.clock[0] = seq.manifest.base.deadline_ms - 1
    permit = seq.prepared(1, continuation)
    assert permit["expires_at_ms"] == seq.manifest.base.deadline_ms
    seq.legacy.clock[0] += 1
    with pytest.raises(GuardDenied):
        seq.begun(permit, continuation)
    assert seq.guard.status(seq.device, seq.manifest.attempt_id)["steps"][1]["status"] == "UNBEGUN"


def test_expired_unbegun_permit_can_be_replaced_but_never_replayed(seq):
    expired = seq.prepared()
    seq.legacy.clock[0] += 10_000
    with pytest.raises(GuardDenied, match="expired"):
        seq.begun(expired)
    assert seq.store.transact(lambda tx: tx.get("claims", seq.manifest.opening_claim_key)) is None
    replacement = seq.prepared()
    assert seq.begun(replacement).may_act is not None
    with pytest.raises(GuardDenied):
        seq.begun(expired)


@pytest.mark.parametrize("phase", ["begin", "outcome"])
def test_uncertain_committed_response_can_only_be_observed_never_reissued(seq, phase):
    permit = seq.prepared()
    proof = None if phase == "begin" else seq.begun(permit).may_act
    def delivery_lost():
        raise ConnectionError("synthetic lost reply")
    seq.store.after_commit = delivery_lost
    with pytest.raises(GuardUnavailable, match="delivery is uncertain"):
        if phase == "begin":
            seq.begun(permit)
        else:
            assert proof is not None
            seq.filled(StepDecision(status="BEGUN", may_act=proof), "committed-local")
    seq.store.after_commit = None
    if phase == "begin":
        assert seq.begun(permit).model_dump() == {"status": "ALREADY_BEGUN", "may_act": None}
        with pytest.raises(GuardDenied):
            seq.prepared(1)
    else:
        assert proof is not None
        duplicate = seq.filled(StepDecision(status="BEGUN", may_act=proof), "committed-local")
        assert duplicate.continuation_nonce is None
        with pytest.raises(GuardDenied):
            seq.prepared(1)


@pytest.mark.parametrize("phase", ["begin", "outcome"])
def test_reply_deadline_rechecks_after_commit_without_reconstructing_permission(seq, phase):
    permit = seq.prepared()
    proof = None if phase == "begin" else seq.begun(permit).may_act
    seq.store.after_commit = lambda: seq.legacy.clock.__setitem__(0,
        seq.legacy.clock[0] + 2_000 if phase == "begin" else seq.manifest.base.deadline_ms)
    if phase == "begin":
        result = seq.begun(permit)
        assert result.may_act is None and result.status == "ALREADY_BEGUN"
    else:
        assert proof is not None
        result = seq.filled(StepDecision(status="BEGUN", may_act=proof), "delayed-local")
        assert result.continuation_nonce is None
    seq.store.after_commit = None
    assert seq.begun(permit).may_act is None


def test_v1_possible_disclosure_claim_cannot_be_adopted_as_a_sequence_prefix(seq):
    binding = seq.legacy.binding.model_copy(update={"approval_id": "legacy-approval"})
    prepared = seq.legacy.prepared(binding)
    legacy = seq.legacy.guard.begin(seq.device, prepared["permit_id"], binding)
    assert legacy.may_act is not None
    with pytest.raises(GuardDenied, match="Opening belongs"):
        seq.prepared()
    claim = seq.store.transact(lambda tx: tx.get("claims", seq.manifest.opening_claim_key))
    assert "attempt_id" not in claim and claim["permit_id"] == prepared["permit_id"]


def test_epoch_and_new_approval_do_not_change_permanent_opening_ownership(seq):
    seq.begun(seq.prepared())
    prior_claim = seq.store.transact(lambda tx: tx.get("claims", seq.manifest.opening_claim_key))
    control = seq.legacy.guard.close(seq.legacy.operator, "close")
    seq.legacy.guard.open(seq.legacy.operator, seq.legacy.guard.barrier(seq.legacy.operator))
    base = seq.manifest.base.model_copy(update={"epoch_id": UUID(control["epoch_id"]),
        "epoch_generation": control["generation"], "approval_id": "fresh-epoch-approval",
        "approval_revision": 2, "package_digest": "f" * 64})
    fresh = seq.guard.seal(seq.candidate, base, seq.manifest.steps)
    approve(seq, fresh)
    assert fresh.opening_claim_key == seq.manifest.opening_claim_key
    with pytest.raises(GuardDenied, match="Opening belongs"):
        seq.guard.prepare(seq.device, fresh.attempt_id, 0, fresh.sequence_digest)
    assert seq.store.transact(lambda tx: tx.get("claims", seq.manifest.opening_claim_key)) == prior_claim


def _corrupt_record(path, namespace, key, change):
    with sqlite3.connect(path) as connection:
        row = connection.execute("SELECT payload FROM records WHERE namespace=? AND key=?", (namespace, key)).fetchone()
        assert row is not None
        data = json.loads(row[0])
        change(data)
        connection.execute("UPDATE records SET payload=? WHERE namespace=? AND key=?", (json.dumps(data), namespace, key))


@pytest.mark.parametrize("damage", ["manifest", "cursor", "in-flight", "claim", "ack", "step", "permit", "challenge", "approval", "cancel", "extra-step"])
def test_projection_restore_or_corruption_cannot_erase_disclosure_prefix(seq, damage):
    first = seq.begun(seq.prepared())
    continuation = seq.filled(first).continuation_nonce
    assert continuation is not None
    permit = seq.prepared(1, continuation)
    attempt_id = str(seq.manifest.attempt_id)
    if damage in {"manifest", "cursor", "in-flight"}:
        def change(data):
            if damage == "manifest":
                data["manifest"]["base"]["target"]["document_id"] = "restored-old-document"
            else:
                data["next_index" if damage == "cursor" else "in_flight"] = 0
        _corrupt_record(seq.store.path, "sequence_attempts", attempt_id, change)
    elif damage == "claim":
        _corrupt_record(seq.store.path, "claims", seq.manifest.opening_claim_key,
            lambda data: data.update(attempt_id=str(uuid4())))
    elif damage == "ack":
        _corrupt_record(seq.store.path, "sequence_steps", step_key(seq.manifest.attempt_id, 0),
            lambda data: data.update(continuation_hash="f" * 64))
    elif damage == "step":
        _corrupt_record(seq.store.path, "sequence_steps", step_key(seq.manifest.attempt_id, 0),
            lambda data: data.update(status="UNBEGUN"))
    elif damage == "permit":
        _corrupt_record(seq.store.path, "sequence_permits", permit["id"],
            lambda data: data.update(index=2))
    elif damage == "challenge":
        key = seq.store.transact(lambda tx: tx.get("sequence_approvals", seq.manifest.base.approval_id))["challenge_id"]
        _corrupt_record(seq.store.path, "sequence_challenges", key, lambda data: data.update(consumed=False))
    elif damage == "approval":
        _corrupt_record(seq.store.path, "sequence_approvals", seq.manifest.base.approval_id,
            lambda data: data.update(context_digest="f" * 64))
    elif damage == "cancel":
        seq.guard.cancel(seq.candidate, seq.manifest.attempt_id, "cancel")
        with sqlite3.connect(seq.store.path) as connection:
            connection.execute("DELETE FROM records WHERE namespace='sequence_cancellations'")
    else:
        with sqlite3.connect(seq.store.path) as connection:
            connection.execute("INSERT INTO records VALUES ('sequence_steps','unowned-step',?)", (json.dumps({"status": "UNBEGUN"}),))
    with pytest.raises(GuardUnavailable):
        seq.begun(permit, continuation)


def test_sequence_approval_tombstone_is_registered_subject_bound_and_monotonic(seq):
    permit = seq.prepared()
    wrong_subject = seq.candidate.model_copy(update={"actor_id": "another-candidate", "subject_uuid": UUID(int=1)})
    seq.store.transact(lambda tx: tx.put("actors", wrong_subject.actor_id,
        {"identity": wrong_subject.model_dump(mode="json"), "active": True}))
    with pytest.raises(GuardDenied, match="another subject"):
        seq.guard.revoke_approval(wrong_subject, seq.manifest.attempt_id, "other-revocation")
    first = seq.guard.revoke_approval(seq.candidate, seq.manifest.attempt_id, "revoke-approval")
    assert seq.guard.revoke_approval(seq.candidate, seq.manifest.attempt_id, "revoke-approval") == first
    with pytest.raises(GuardDenied, match="revoked"):
        seq.begun(permit)
    assert seq.store.transact(lambda tx: tx.get("claims", seq.manifest.opening_claim_key)) is None


def test_unknown_outcome_contract_cannot_carry_a_continuation():
    with pytest.raises(ValidationError):
        OutcomeDecision(status="UNKNOWN", next_index=0, continuation_nonce=uuid4())


@pytest.mark.parametrize("condition", ["inactive-subject", "inactive-device", "rotated-candidate"])
def test_current_owner_can_revoke_after_context_stops_and_late_observation_cannot_unlock(seq, condition):
    begun = seq.begun(seq.prepared())
    candidate = seq.candidate
    if condition == "inactive-subject":
        seq.store.transact(lambda tx: tx.put("subjects", str(seq.manifest.base.subject_uuid), {"active": False}))
    elif condition == "inactive-device":
        seq.store.transact(lambda tx: tx.put("actors", seq.device.actor_id,
            {"identity": seq.device.model_dump(mode="json"), "active": False}))
    else:
        candidate = seq.candidate.model_copy(update={"key_sha256": "f" * 64})
        seq.store.transact(lambda tx: tx.put("actors", candidate.actor_id,
            {"identity": candidate.model_dump(mode="json"), "active": True}))
        with pytest.raises(GuardDenied, match="identity"):
            seq.guard.revoke_approval(seq.candidate, seq.manifest.attempt_id, "stale-candidate-key")
    assert seq.guard.revoke_approval(candidate, seq.manifest.attempt_id, "current-owner-revocation")["status"] == "REVOKED"
    outcome = seq.filled(begun)
    assert outcome.continuation_nonce is None
    assert seq.guard.status(seq.device, seq.manifest.attempt_id)["state"] == "HANDOFF"
    assert seq.store.transact(lambda tx: tx.get("claims", seq.manifest.opening_claim_key)) is not None


def test_inactive_candidate_cannot_revoke_but_registered_operator_can(seq):
    seq.store.transact(lambda tx: tx.put("actors", seq.candidate.actor_id,
        {"identity": seq.candidate.model_dump(mode="json"), "active": False}))
    with pytest.raises(GuardDenied, match="not authorized"):
        seq.guard.revoke_approval(seq.candidate, seq.manifest.attempt_id, "inactive-candidate")
    assert seq.guard.revoke_approval(seq.legacy.operator, seq.manifest.attempt_id, "operator-revoke")["status"] == "REVOKED"


@pytest.mark.parametrize("cursor", [True, 1.0])
def test_projection_numeric_types_do_not_equate_boolean_float_and_integer(seq, cursor):
    continuation = seq.filled(seq.begun(seq.prepared())).continuation_nonce
    assert continuation is not None
    permit = seq.prepared(1, continuation)
    _corrupt_record(seq.store.path, "sequence_attempts", str(seq.manifest.attempt_id),
        lambda data: data.update(next_index=cursor))
    with pytest.raises(GuardUnavailable):
        seq.begun(permit, continuation)


def test_complete_manifest_golden_vectors_match_independent_node_crypto(seq):
    data = seq.manifest.model_dump(mode="json")
    data["attempt_id"] = str(UUID(int=4))
    data["candidate_actor_digest"] = "f" * 64
    for field, value in (("authority_id", 1), ("subject_uuid", 2), ("epoch_id", 3)):
        data["base"][field] = str(UUID(int=value))
    for field in ("epoch_generation", "approval_revision", "grant_revision", "deadline_ms"):
        data["base"][field] = 2**53 - 1
    data["base"]["artifact"]["generation"] = "9876543210987654321098765432109"
    data["base"]["target"]["tab_id"] = 2**53 - 1
    data["steps"][0]["value_sha256"] = typed_value_hash('café🙂\n"\\')
    data["steps"][0]["expected_before_sha256"] = typed_value_hash("e\u0301")
    manifest = Manifest.model_validate(data)
    # Independently recalculated with Node24 crypto and recursively ASCII-key-
    # sorted JSON; no Node runtime dependency is needed for this regression.
    assert manifest.sequence_digest == "4823f5f0dd1f0c4d139755d4e55a009a005c4886c9a160a3bc28adfdcdec462f"
    assert manifest.review_digest == "8989cef34b5f9c4596425d307e2b7784042d8d691c964083e3f7d504674a86ae"
    assert manifest.context_digest == "fc00c55475c08691215a0b712457823b37c21e96d3b9ac8f5ded8a74eed62448"
    assert [manifest.step_digest(index) for index in range(4)] == [
        "6542eeb60615b087de930b1d5ef0ed887a8080b5ab8184e9efe624e11d29fe40",
        "defa0eba06942f033d7e75bea307d71d396ae863cda6961367cc32b24104f90d",
        "6e992c02947f0e6c3071c863eca6c1c5f3695e0978e58d415e22b04bce62c2e2",
        "7b179a7430a1dad43402fbb24986cd40d1a5052e65cfc7228f643ef3da214dcd",
    ]
