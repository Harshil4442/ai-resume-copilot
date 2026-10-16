"""Synthetic, local durable guard proofs; not production recovery evidence."""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from backend.tests.fixtures.recovery_authority import FileAuthority
from pydantic import ValidationError

from app.domains.recovery.contracts import AuthenticatedActor, Binding, Revocation
from app.domains.recovery.service import RecoveryGuard
from app.domains.recovery.store import GuardDenied, GuardUnavailable

NOW = 1_800_000_000_000


@dataclass
class Case:
    store: FileAuthority
    guard: RecoveryGuard
    candidate: AuthenticatedActor
    device: AuthenticatedActor
    operator: AuthenticatedActor
    binding: Binding
    clock: list[int]

    def prepared(self, binding: Binding | None = None) -> dict:
        target = binding or self.binding
        challenge = self.guard.challenge(self.candidate)
        self.guard.register_approval(self.candidate, challenge["id"], target)
        return self.guard.prepare(self.device, target.approval_id)


def case(path: Path, *, opened: bool = True) -> Case:
    authority_id, subject_uuid = uuid4(), uuid4()
    store = FileAuthority(path, authority_id, create=True)
    control = store.transact(lambda tx: tx.get("control", "current"))
    assert control is not None
    candidate = AuthenticatedActor(actor_id="candidate-one", role="candidate",
        subject_uuid=subject_uuid, key_sha256="a" * 64, executor_revision="candidate-v1")
    device = AuthenticatedActor(actor_id="device-actor-one", role="device",
        subject_uuid=subject_uuid, device_id="device-one", key_sha256="b" * 64,
        executor_revision="fill-v1")
    operator = AuthenticatedActor(actor_id="fixture-operator", role="operator",
        key_sha256="c" * 64, executor_revision="operator-v1")
    binding = Binding(authority_id=authority_id, subject_uuid=subject_uuid,
        epoch_id=control["epoch_id"], epoch_generation=control["generation"],
        application_id="synthetic-application-one", employer_key="d" * 64,
        tenant_id="fixture-tenant", opening_key="e" * 64, approval_id="approval-one",
        approval_revision=1, admission_id="synthetic-admission-one", policy_sha256="1" * 64,
        pricing_sha256="2" * 64, artifact_sha256="3" * 64, artifact_generation="7",
        package_digest="4" * 64, review_digest="5" * 64, grant_id="fixture-grant",
        grant_revision=1, device_id=device.device_id, device_key_sha256=device.key_sha256,
        executor_revision=device.executor_revision, origin="https://fixture.invalid",
        action="fill", field_id="synthetic-role-field", value_sha256="6" * 64,
        deadline_ms=NOW + 60_000)
    store.provision_fixture([candidate, device, operator], binding)
    clock = [NOW]
    guard = RecoveryGuard(store, lambda: clock[0])
    if opened:
        guard.open(operator, guard.barrier(operator))
    return Case(store, guard, candidate, device, operator, binding, clock)


@pytest.fixture
def fixture_case(tmp_path: Path) -> Case:
    return case(tmp_path / "independent-authority.sqlite")


def test_production_store_is_always_absent_without_fixture_fallback(monkeypatch, fixture_case):
    monkeypatch.setenv("RECOVERY_AUTHORITY_SQLITE_PATH", str(fixture_case.store.path))
    monkeypatch.setenv("RECOVERY_GUARD_ENABLED", "true")
    with pytest.raises(GuardUnavailable, match="not implemented"):
        RecoveryGuard().challenge(fixture_case.candidate)


def test_default_closed_and_stale_replay_barrier_never_open(tmp_path):
    ctx = case(tmp_path / "authority.sqlite", opened=False)
    with pytest.raises(GuardDenied, match="closed"):
        ctx.guard.challenge(ctx.candidate)
    barrier = ctx.guard.barrier(ctx.operator)
    ctx.guard.close(ctx.operator, "close-one")
    with pytest.raises(GuardDenied, match="Replay barrier"):
        ctx.guard.open(ctx.operator, barrier)
    with pytest.raises(GuardDenied, match="not authorized"):
        ctx.guard.open(ctx.candidate, ctx.guard.barrier(ctx.operator))
    control = ctx.guard.open(ctx.operator, ctx.guard.barrier(ctx.operator))
    assert control["status"] == "OPEN" and control["generation"] == 2


def test_prepare_never_grants_permission_and_only_first_begin_can_act(fixture_case):
    ctx = fixture_case
    prepared = ctx.prepared()
    assert prepared["status"] == "PREPARED" and "may_act" not in prepared
    first = ctx.guard.begin(ctx.device, prepared["permit_id"], ctx.binding)
    assert first.status == "BEGUN" and first.may_act is not None
    assert first.may_act.expires_at_ms <= NOW + 2_000
    assert ctx.store.transact(lambda tx: tx.get("claims", ctx.binding.opening_claim_key))[
        "begun_sequence"] == first.may_act.begun_sequence
    assert ctx.guard.begin(ctx.device, prepared["permit_id"], ctx.binding).model_dump() == {
        "status": "ALREADY_BEGUN", "may_act": None}
    with pytest.raises(GuardDenied, match="possible disclosure"):
        ctx.guard.prepare(ctx.device, ctx.binding.approval_id)


def test_fresh_single_use_subject_epoch_and_actor_bound_challenge(fixture_case):
    ctx = fixture_case
    with pytest.raises(GuardDenied, match="Fresh challenge"):
        ctx.guard.register_approval(ctx.candidate, "restored-sql-approval", ctx.binding)
    challenge = ctx.guard.challenge(ctx.candidate)
    ctx.guard.register_approval(ctx.candidate, challenge["id"], ctx.binding)
    changed = ctx.binding.model_copy(update={"approval_id": "approval-two"})
    with pytest.raises(GuardDenied, match="Fresh challenge"):
        ctx.guard.register_approval(ctx.candidate, challenge["id"], changed)
    second = ctx.guard.challenge(ctx.candidate)
    ctx.clock[0] += 10_000
    with pytest.raises(GuardDenied, match="Fresh challenge"):
        ctx.guard.register_approval(ctx.candidate, second["id"], changed)


def test_candidate_key_rotation_invalidates_old_challenge(fixture_case):
    ctx = fixture_case
    challenge = ctx.guard.challenge(ctx.candidate)
    rotated = ctx.candidate.model_copy(update={"key_sha256": "f" * 64})
    ctx.store.transact(lambda tx: tx.put("actors", rotated.actor_id,
        {"identity": rotated.model_dump(mode="json"), "active": True}))
    with pytest.raises(GuardDenied, match="Fresh challenge"):
        ctx.guard.register_approval(rotated, challenge["id"], ctx.binding)


def test_consumed_challenge_projection_rollback_cannot_register_another_opening(fixture_case):
    ctx = fixture_case
    challenge = ctx.guard.challenge(ctx.candidate)
    ctx.guard.register_approval(ctx.candidate, challenge["id"], ctx.binding)
    with sqlite3.connect(ctx.store.path) as connection:
        restored_challenge = {**challenge, "consumed": False}
        connection.execute("UPDATE records SET payload=? WHERE namespace='challenges' AND key=?",
                           (json.dumps(restored_challenge), challenge["id"]))
    other_opening = ctx.binding.model_copy(update={"approval_id": "second-approval",
                                                   "opening_key": "f" * 64})
    with pytest.raises(GuardUnavailable, match="Independent approval evidence is incomplete"):
        ctx.guard.register_approval(ctx.candidate, challenge["id"], other_opening)
    with sqlite3.connect(ctx.store.path) as connection:
        assert connection.execute("SELECT count(*) FROM records WHERE namespace='approvals'").fetchone()[0] == 1


@pytest.mark.parametrize("mutation", ["key", "executor", "inactive"])
def test_current_approving_identity_is_rechecked_before_begin(fixture_case, mutation):
    ctx = fixture_case
    prepared = ctx.prepared()
    identity = ctx.candidate.model_dump(mode="json")
    if mutation == "key":
        identity["key_sha256"] = "f" * 64
    if mutation == "executor":
        identity["executor_revision"] = "candidate-v2"
    ctx.store.transact(lambda tx: tx.put("actors", ctx.candidate.actor_id,
        {"identity": identity, "active": mutation != "inactive"}))
    with pytest.raises(GuardDenied, match="Approving identity"):
        ctx.guard.begin(ctx.device, prepared["permit_id"], ctx.binding)
    assert ctx.store.transact(lambda tx: tx.get("claims", ctx.binding.opening_claim_key)) is None


ALTERATIONS = {
    "authority_id": uuid4(), "subject_uuid": uuid4(), "epoch_id": uuid4(), "epoch_generation": 2,
    "application_id": "different-application", "employer_key": "f" * 64, "tenant_id": "other-tenant",
    "opening_key": "f" * 64, "approval_id": "different-approval", "approval_revision": 2,
    "admission_id": "other-admission", "policy_sha256": "f" * 64, "pricing_sha256": "f" * 64,
    "artifact_sha256": "f" * 64, "artifact_generation": "8", "package_digest": "f" * 64,
    "review_digest": "f" * 64, "grant_id": "other-grant", "grant_revision": 2,
    "device_id": "other-device", "device_key_sha256": "f" * 64,
    "executor_revision": "fill-v2", "origin": "https://other.invalid", "field_id": "other-field",
    "value_sha256": "f" * 64, "deadline_ms": NOW + 60_001,
}


@pytest.mark.parametrize("field", ALTERATIONS)
def test_every_exact_binding_field_is_fenced(fixture_case, field):
    ctx = fixture_case
    prepared = ctx.prepared()
    changed = Binding.model_validate({**ctx.binding.model_dump(mode="json"), field: ALTERATIONS[field]})
    with pytest.raises(GuardDenied, match="Exact action binding changed"):
        ctx.guard.begin(ctx.device, prepared["permit_id"], changed)
    assert ctx.store.transact(lambda tx: tx.get("claims", ctx.binding.opening_claim_key)) is None


@pytest.mark.parametrize("field,value", [("actor_id", "other-device-actor"),
    ("subject_uuid", uuid4()), ("device_id", "other-device"), ("key_sha256", "f" * 64),
    ("executor_revision", "fill-v2")])
def test_caller_cannot_self_declare_owner_device_key_or_protocol(fixture_case, field, value):
    ctx = fixture_case
    prepared = ctx.prepared()
    changed = ctx.device.model_copy(update={field: value})
    with pytest.raises(GuardDenied, match="identity"):
        ctx.guard.begin(changed, prepared["permit_id"], ctx.binding)


@pytest.mark.parametrize("origin", ["http://fixture.invalid", "https://fixture.invalid/path",
    "https://fixture.invalid?query=1", "https://user@fixture.invalid", "https://fixture.invalid:8443",
    "https://fixture.invalid#fragment", "https://fixture.invalid "])
def test_contract_rejects_nonexact_origins_and_nonfill_actions(fixture_case, origin):
    data = fixture_case.binding.model_dump(mode="json")
    with pytest.raises(ValidationError):
        Binding.model_validate({**data, "origin": origin})
    for action in ("upload", "submit", "autosave"):
        with pytest.raises(ValidationError):
            Binding.model_validate({**data, "action": action})


@pytest.mark.parametrize("kind", ["subject", "approval", "device", "grant", "artifact"])
def test_monotonic_revocations_stop_prepared_actions_and_are_idempotent(fixture_case, kind):
    ctx = fixture_case
    prepared = ctx.prepared()
    targets = {"subject": (str(ctx.binding.subject_uuid), 0),
        "approval": (ctx.binding.approval_id, ctx.binding.approval_revision),
        "device": (ctx.binding.device_id, 0), "grant": (ctx.binding.grant_id, 1),
        "artifact": (ctx.binding.artifact_sha256, int(ctx.binding.artifact_generation))}
    target, revision = targets[kind]
    scope = Revocation(subject_uuid=ctx.binding.subject_uuid, kind=kind,
                       target=target, revision=revision)
    first = ctx.guard.revoke(ctx.candidate, scope, "revoke-one")
    assert ctx.guard.revoke(ctx.candidate, scope, "revoke-one") == first
    with pytest.raises(GuardDenied, match="revoked"):
        ctx.guard.begin(ctx.device, prepared["permit_id"], ctx.binding)
    assert ctx.store.transact(lambda tx: tx.get("revocations", scope.key)) is not None
    assert ctx.store.transact(lambda tx: tx.get("claims", ctx.binding.opening_claim_key)) is None


def test_tombstone_scope_cannot_revoke_a_different_subject_or_revision(fixture_case):
    ctx = fixture_case
    ctx.prepared()
    scope = Revocation(subject_uuid=uuid4(), kind="device", target=ctx.binding.device_id, revision=0)
    with pytest.raises(GuardDenied, match="another subject"):
        ctx.guard.revoke(ctx.candidate, scope, "foreign-revoke")
    with pytest.raises(GuardDenied, match="scope is invalid"):
        ctx.guard.revoke(ctx.operator, scope, "foreign-revoke")
    wrong = Revocation(subject_uuid=ctx.binding.subject_uuid, kind="approval",
                       target=ctx.binding.approval_id, revision=2)
    with pytest.raises(GuardDenied, match="revision"):
        ctx.guard.revoke(ctx.candidate, wrong, "bad-revision")


def test_expiry_epoch_close_and_open_never_reopen_a_begun_action(fixture_case):
    ctx = fixture_case
    prepared = ctx.prepared()
    ctx.guard.begin(ctx.device, prepared["permit_id"], ctx.binding)
    closed = ctx.guard.close(ctx.operator, "restore-close")
    assert ctx.guard.close(ctx.operator, "restore-close") == closed
    assert closed["generation"] == 2
    assert ctx.guard.begin(ctx.device, prepared["permit_id"], ctx.binding).may_act is None
    ctx.guard.open(ctx.operator, ctx.guard.barrier(ctx.operator))
    fresh = ctx.binding.model_copy(update={"epoch_id": UUID(closed["epoch_id"]),
        "epoch_generation": closed["generation"], "approval_id": "fresh-approval",
        "application_id": "restored-application", "package_digest": "f" * 64})
    challenge = ctx.guard.challenge(ctx.candidate)
    ctx.guard.register_approval(ctx.candidate, challenge["id"], fresh)
    with pytest.raises(GuardDenied, match="possible disclosure"):
        ctx.guard.prepare(ctx.device, fresh.approval_id)
    ctx.clock[0] += 120_000
    assert ctx.guard.begin(ctx.device, prepared["permit_id"], ctx.binding).may_act is None


def test_unused_short_lived_permit_expires_without_a_disclosure(fixture_case):
    ctx = fixture_case
    prepared = ctx.prepared()
    ctx.clock[0] += 10_000
    with pytest.raises(GuardDenied, match="permit expired"):
        ctx.guard.begin(ctx.device, prepared["permit_id"], ctx.binding)
    assert ctx.store.transact(lambda tx: tx.get("claims", ctx.binding.opening_claim_key)) is None


def test_epoch_closure_invalidates_unbegun_permit_and_old_challenge(fixture_case):
    ctx = fixture_case
    prepared = ctx.prepared()
    old_challenge = ctx.guard.challenge(ctx.candidate)
    control = ctx.guard.close(ctx.operator, "epoch-transition")
    with pytest.raises(GuardDenied, match="closed"):
        ctx.guard.begin(ctx.device, prepared["permit_id"], ctx.binding)
    ctx.guard.open(ctx.operator, ctx.guard.barrier(ctx.operator))
    with pytest.raises(GuardDenied, match="Epoch is not current"):
        ctx.guard.begin(ctx.device, prepared["permit_id"], ctx.binding)
    fresh_binding = ctx.binding.model_copy(update={"epoch_id": UUID(control["epoch_id"]),
        "epoch_generation": control["generation"], "approval_id": "new-epoch-approval"})
    with pytest.raises(GuardDenied, match="Fresh challenge"):
        ctx.guard.register_approval(ctx.candidate, old_challenge["id"], fresh_binding)


@pytest.mark.parametrize("clock", [NOW - 1, 0, True])
def test_invalid_or_backward_authority_clock_cannot_authorize(fixture_case, clock):
    ctx = fixture_case
    prepared = ctx.prepared()
    ctx.clock[0] = clock
    with pytest.raises(GuardDenied, match="clock"):
        ctx.guard.begin(ctx.device, prepared["permit_id"], ctx.binding)
    assert ctx.store.transact(lambda tx: tx.get("claims", ctx.binding.opening_claim_key)) is None


@pytest.mark.parametrize("reply_clock", [NOW + 2_000, NOW - 1])
def test_delayed_or_backward_clock_reply_does_not_return_a_permission(fixture_case, reply_clock):
    ctx = fixture_case
    prepared = ctx.prepared()
    store = FileAuthority(ctx.store.path, ctx.store.authority_id,
        after_commit=lambda: ctx.clock.__setitem__(0, reply_clock))
    result = RecoveryGuard(store, lambda: ctx.clock[0]).begin(ctx.device, prepared["permit_id"], ctx.binding)
    assert result.model_dump() == {"status": "ALREADY_BEGUN", "may_act": None}
    assert ctx.store.transact(lambda tx: tx.get("claims", ctx.binding.opening_claim_key)) is not None


@pytest.mark.parametrize("outcome", ["UNKNOWN", "LOCAL_FILLED"])
def test_outcomes_never_synthesize_receipts_refunds_or_reopen(fixture_case, outcome):
    ctx = fixture_case
    prepared = ctx.prepared()
    ctx.guard.begin(ctx.device, prepared["permit_id"], ctx.binding)
    ctx.guard.close(ctx.operator, "after-begin-close")
    result = ctx.guard.outcome(ctx.device, prepared["permit_id"], outcome)
    assert result.model_dump() == {"status": outcome, "may_act": None}
    assert ctx.guard.outcome(ctx.device, prepared["permit_id"], outcome) == result
    assert ctx.guard.begin(ctx.device, prepared["permit_id"], ctx.binding) == result
    other = "UNKNOWN" if outcome == "LOCAL_FILLED" else "LOCAL_FILLED"
    with pytest.raises(GuardDenied, match="Terminal outcome"):
        ctx.guard.outcome(ctx.device, prepared["permit_id"], other)
    assert ctx.store.transact(lambda tx: tx.get("claims", ctx.binding.opening_claim_key)) is not None
    with pytest.raises(GuardDenied, match="supported local outcome"):
        ctx.guard.outcome(ctx.device, prepared["permit_id"], "CONFIRMED")


def test_committed_reply_loss_discards_may_act_and_retry_observes_status(fixture_case):
    ctx = fixture_case
    prepared = ctx.prepared()
    def response_loss():
        raise OSError("Synthetic response delivery failure")
    uncertain = FileAuthority(ctx.store.path, ctx.store.authority_id, after_commit=response_loss)
    with pytest.raises(GuardUnavailable, match="response delivery is uncertain"):
        RecoveryGuard(uncertain, lambda: NOW).begin(ctx.device, prepared["permit_id"], ctx.binding)
    assert ctx.guard.begin(ctx.device, prepared["permit_id"], ctx.binding).may_act is None
    assert ctx.store.transact(lambda tx: tx.get("claims", ctx.binding.opening_claim_key)) is not None


def test_event_journal_is_append_only_and_contains_no_candidate_values(fixture_case):
    ctx = fixture_case
    prepared = ctx.prepared()
    ctx.guard.begin(ctx.device, prepared["permit_id"], ctx.binding)
    with sqlite3.connect(ctx.store.path) as connection:
        for sql in ("DELETE FROM events", "UPDATE events SET kind='REPLACED'"):
            with pytest.raises(sqlite3.IntegrityError, match="Append-only"):
                connection.execute(sql)
        journal = json.dumps(connection.execute("SELECT kind,payload FROM events").fetchall())
    assert "value_sha256" not in journal  # only its binding digest is journalled
    assert "resume bytes" not in journal and "answers" not in journal
    assert "ACTION_BEGUN" in journal


@pytest.mark.parametrize("failure", ["missing", "replacement", "journal_gap"])
def test_missing_replaced_or_incomplete_authority_fails_closed(fixture_case, failure):
    ctx = fixture_case
    if failure == "missing":
        ctx.store.path.unlink()
    elif failure == "replacement":
        wrong = FileAuthority(ctx.store.path, uuid4())
        with pytest.raises(GuardUnavailable, match="identity"):
            wrong.transact(lambda tx: tx.head())
        return
    else:
        with sqlite3.connect(ctx.store.path) as connection:
            connection.execute("DROP TRIGGER immutable_events_delete")
            connection.execute("DELETE FROM events WHERE sequence=2")
    with pytest.raises(GuardUnavailable):
        ctx.guard.challenge(ctx.candidate)
    if failure == "missing":
        assert not ctx.store.path.exists()  # no default recreation/fallback


def test_event_id_cannot_change_operation_or_payload(fixture_case):
    ctx = fixture_case
    ctx.prepared()
    scope = Revocation(subject_uuid=ctx.binding.subject_uuid, kind="approval",
                       target=ctx.binding.approval_id, revision=1)
    ctx.guard.revoke(ctx.candidate, scope, "bound-event")
    with pytest.raises(GuardDenied, match="Event ID conflicts"):
        ctx.guard.close(ctx.operator, "bound-event")
    other = scope.model_copy(update={"revision": 0})
    with pytest.raises(GuardDenied, match="event ID conflicts"):
        ctx.guard.revoke(ctx.candidate, other, "bound-event")


def test_operation_ids_cannot_record_raw_personal_data(fixture_case):
    ctx = fixture_case
    head = ctx.store.transact(lambda tx: tx.head())
    with pytest.raises(GuardDenied, match="opaque bounded identifier"):
        ctx.guard.close(ctx.operator, "candidate@example.invalid")
    assert ctx.store.transact(lambda tx: tx.head()) == head
