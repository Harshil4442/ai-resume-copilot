"""Actual native/protected enrollment and credential-bound sessions; no seeded candidate identity."""
from __future__ import annotations

from dataclasses import replace
from uuid import uuid4

import pytest
from test_gcp_pairing_emulator import case as case
from test_gcp_password_lifetime_emulator import lifetime as lifetime

from app.domains.recovery.gcp_contracts import AmbiguousCommit
from app.domains.recovery.gcp_password_lifetime import PasswordLifetimeOwnedAck
from app.domains.recovery.gcp_password_lifetime_contracts import (
    CreateVerifiedPasswordWebSession,
    EnrollPasswordAccount,
)
from app.domains.recovery.store import GuardDenied


def enrollment(c):
    command = EnrollPasswordAccount(candidate_id=91, account_binding_id=uuid4(), subject_uuid=uuid4(),
        principal_sha256="a" * 64, credential_sha256="b" * 64, subject_registration_event_id=uuid4())
    assert c.registry.read("subjects", str(command.subject_uuid)) is None
    plan = c.lifetime.allocate(command)
    result = c.lifetime.execute(plan)
    return command, plan, result


def test_genuine_native_enrollment_retains_registration_binding_credential_and_original_ack(lifetime):
    c = lifetime
    command, plan, result = enrollment(c)
    assert result.status.status == "COMMITTED" and result.context is None and result.owned_ack is not None
    subject = c.registry.read("subjects", str(command.subject_uuid))
    event = c.registry.read("events", str(subject["registration_event_id"]))
    assert event["kind"] == "SUBJECT_REGISTERED" and event["payload"] == {"subject_uuid": str(command.subject_uuid)}
    assert c.registry.read("password_credential_revisions", str(command.subject_uuid)) == {
        "subject_uuid": str(command.subject_uuid), "auth_generation": 1, "credential_sha256": command.credential_sha256}
    bound = c.registry.read("pairing_account_bindings", str(command.account_binding_id))
    assert bound["candidate_id"] == 91 and bound["subject_uuid"] == str(command.subject_uuid)
    assert c.registry.read("events", str(bound["registration_event_id"]))["sequence"] == event["sequence"] + 1
    evidence = c.lifetime.consume_owned_ack(result.owned_ack)
    assert evidence.intent.digest == plan.intent.digest
    assert c.lifetime.execute(plan).owned_ack is None
    with pytest.raises(GuardDenied):
        c.lifetime.consume_owned_ack(result.owned_ack)


def test_verified_session_uses_sql_expected_credential_generation_not_native_adoption(lifetime):
    c = lifetime
    command, _, result = enrollment(c)
    c.lifetime.consume_owned_ack(result.owned_ack)
    issued = c.lifetime.execute(c.lifetime.allocate(CreateVerifiedPasswordWebSession(
        account_binding_id=command.account_binding_id, session_id=uuid4(), expected_auth_generation=1,
        credential_sha256=command.credential_sha256, expires_at_ms=c.now + 1000)))
    assert issued.context is not None and issued.owned_ack is not None
    evidence = c.lifetime.consume_owned_ack(issued.owned_ack)
    assert evidence.intent.command.credential_sha256 == command.credential_sha256
    for field, value in (("credential_sha256", "c" * 64), ("expected_auth_generation", 2)):
        values = dict(account_binding_id=command.account_binding_id, session_id=uuid4(), expected_auth_generation=1,
            credential_sha256=command.credential_sha256, expires_at_ms=c.now + 1000)
        values[field] = value
        before = len(c.commits)
        with pytest.raises(GuardDenied):
            c.lifetime.allocate(CreateVerifiedPasswordWebSession(**values))
        assert len(c.commits) == before


@pytest.mark.parametrize("retained", [False, True])
def test_unknown_native_commit_never_creates_original_ack_or_session_context(lifetime, monkeypatch, retained):
    c = lifetime
    original = c.rpc.commit
    def unknown(transaction, writes):
        if retained:
            original(transaction, writes)
        else:
            c.rpc.rollback(transaction)
        raise AmbiguousCommit("Synthetic unknown native outcome")
    command = EnrollPasswordAccount(candidate_id=92, account_binding_id=uuid4(), subject_uuid=uuid4(),
        principal_sha256="c" * 64, credential_sha256="d" * 64, subject_registration_event_id=uuid4())
    plan = c.lifetime.allocate(command)
    monkeypatch.setattr(c.rpc, "commit", unknown)
    result = c.lifetime.execute(plan)
    assert result.status.status == "UNKNOWN" and result.owned_ack is None and result.context is None
    monkeypatch.setattr(c.rpc, "commit", original)
    assert c.lifetime.execute(plan).owned_ack is None
    assert c.lifetime.status(plan.intent).status == ("COMMITTED" if retained else "UNKNOWN")


def test_forged_ack_copy_other_issuer_and_expiry_refuse_without_new_commit(lifetime):
    c = lifetime
    _, _, result = enrollment(c)
    ack = result.owned_ack
    assert ack is not None
    before = len(c.commits)
    for forged in (replace(ack), PasswordLifetimeOwnedAck(ack._raw, object())):
        with pytest.raises(GuardDenied):
            c.lifetime.consume_owned_ack(forged)
    c.now += 60_000
    with pytest.raises(GuardDenied):
        c.lifetime.consume_owned_ack(ack)
    assert len(c.commits) == before
    with pytest.raises(GuardDenied):
        c.lifetime.consume_owned_ack(ack)


def test_absent_native_subject_cannot_be_bootstrapped_by_verified_login(lifetime):
    c = lifetime
    before = len(c.commits)
    with pytest.raises(GuardDenied):
        c.lifetime.allocate(CreateVerifiedPasswordWebSession(account_binding_id=uuid4(), session_id=uuid4(),
            expected_auth_generation=1, credential_sha256="a" * 64, expires_at_ms=c.now + 1000))
    assert len(c.commits) == before


def test_two_event_enrollment_is_accepted_by_complete_protected_inventory_parser(lifetime):
    from app.domains.recovery.gcp_password_closure import GcsClosedInventory
    c = lifetime
    _, plan, result = enrollment(c)
    path, raw = c.lifetime_journal._bytes(plan.intent)
    receipt = result.status.journal
    parsed = GcsClosedInventory._reference(path, receipt.generation, raw, c.pin)
    assert parsed.kind == "password_lifetime_effects" and parsed.byte_size == len(raw)
    assert parsed.partition == plan.intent.partition
