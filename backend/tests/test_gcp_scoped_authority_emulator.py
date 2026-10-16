"""Actual owned lifetime ACK → protected activation/denial/BEGIN transactions."""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from threading import Event, get_ident
from uuid import uuid4

import pytest
from backend.tests.fixtures.gcp_partitioned_publication import partitioned_for
from test_gcp_pairing_emulator import case as case
from test_gcp_password_lifetime_emulator import lifetime as lifetime

from app.domains.recovery.contracts import Binding
from app.domains.recovery.gcp_contracts import AmbiguousCommit
from app.domains.recovery.gcp_password import GcpPasswordCandidateBoundary
from app.domains.recovery.gcp_password_lifetime_contracts import (
    CreateVerifiedPasswordWebSession,
    EnrollPasswordAccount,
)
from app.domains.recovery.gcp_scoped_authority import ScopedCandidateAuthority, ScopedDenialAck
from app.domains.recovery.store import GuardDenied, GuardUnavailable


@pytest.fixture
def scoped(lifetime):
    c = lifetime
    c.v3 = partitioned_for(c)
    c.lifetime_journal.publication = c.v3
    c.scoped = ScopedCandidateAuthority(c.v3, now_ms=lambda: c.now)
    return c


def enroll(c, candidate=101, credential="d" * 64):
    command = EnrollPasswordAccount(candidate_id=candidate, subject_uuid=uuid4(), account_binding_id=uuid4(),
        principal_sha256="a" * 64, credential_sha256=credential, subject_registration_event_id=uuid4())
    # The genuine new subject must be absent, never operator-seeded.
    assert c.registry.read("subjects", str(command.subject_uuid)) is None
    plan = c.lifetime.allocate(command)
    result = c.lifetime.execute(plan)
    assert result.status.status == "COMMITTED" and result.owned_ack is not None
    assert c.scoped.activate(c.lifetime, result.owned_ack) is None
    return command, result.owned_ack


def session(c, account, credential="d" * 64):
    command = CreateVerifiedPasswordWebSession(account_binding_id=account.account_binding_id,
        session_id=uuid4(), expected_auth_generation=1, credential_sha256=credential,
        expires_at_ms=c.now + 600_000)
    result = c.lifetime.execute(c.lifetime.allocate(command))
    assert result.status.status == "COMMITTED" and result.owned_ack is not None
    context = c.scoped.activate(c.lifetime, result.owned_ack)
    assert context is not None
    return context


def action(c, context):
    subject, _ = c.scoped.resolve(context)
    return Binding(authority_id=c.pin.authority_id, subject_uuid=subject.subject_uuid, epoch_id=c.pin.epoch_id,
        epoch_generation=1, application_id="synthetic", employer_key="a" * 64, tenant_id="synthetic",
        opening_key=uuid4().hex * 2, approval_id=uuid4().hex, approval_revision=1, admission_id=uuid4().hex,
        policy_sha256="b" * 64, pricing_sha256="c" * 64, artifact_sha256="d" * 64,
        artifact_generation="1", package_digest="e" * 64, review_digest="f" * 64,
        grant_id=uuid4().hex, grant_revision=1, device_id="synthetic", device_key_sha256="1" * 64,
        executor_revision="synthetic", origin="https://boards.greenhouse.io", action="fill",
        field_id="synthetic", value_sha256="2" * 64, deadline_ms=c.now + 60_000)


def names(writes):
    return {json.loads(w.raw)["namespace"] for w in writes}


def test_genuine_owned_enrollment_and_session_activation_replay_never_grants(scoped):
    c = scoped
    account, ack = enroll(c)
    with pytest.raises(GuardDenied, match="consumed"):
        c.scoped.activate(c.lifetime, ack)
    with pytest.raises(GuardDenied):
        c.scoped.activate(c.lifetime, replace(ack))
    context = session(c, account)
    subject, retained = c.scoped.resolve(context)
    assert subject.subject_uuid == account.subject_uuid and subject.credential_sha256 == "d" * 64
    assert retained.session_id == context.session_id
    with pytest.raises(GuardDenied):
        c.scoped.resolve(context.model_copy(update={"candidate_id": 999}))


def test_normal_session_logout_preserves_second_session_and_unrelated_candidate(scoped):
    c = scoped
    a, _ = enroll(c)
    a1, a2 = session(c, a), session(c, a)
    b, _ = enroll(c, 102)
    b1 = session(c, b)
    prepared = action(c, a1)
    receipt = c.scoped.deny(a1, scope="session")
    assert receipt is not None and c.scoped.verify_denial(receipt).command.scope == "session"
    with pytest.raises(GuardDenied):
        c.scoped.resolve(a1)
    with pytest.raises(GuardDenied):
        c.scoped.begin(a1, prepared)
    assert c.scoped.begin(a2, action(c, a2)) is not None
    assert c.scoped.begin(b1, action(c, b1)) is not None
    assert c.v3.registry.read("v3_root", "current")["phase"] == "OPEN"
    assert not c.active.any()


@pytest.mark.parametrize("persisted", [False, True])
def test_unknown_effect_commit_still_has_retained_pending_denial_and_other_session_works(scoped, monkeypatch, persisted):
    c = scoped
    account, _ = enroll(c)
    old, other = session(c, account), session(c, account)
    original = c.v3.registry.rpc.commit
    def lose(tx, writes):
        if "v3_session_tombstones" not in names(writes):
            return original(tx, writes)
        if persisted:
            original(tx, writes)
        else:
            c.v3.registry.rpc.rollback(tx)
        raise AmbiguousCommit("Synthetic scoped effect acknowledgement loss")
    monkeypatch.setattr(c.v3.registry.rpc, "commit", lose)
    assert c.scoped.deny(old, scope="session") is None
    monkeypatch.setattr(c.v3.registry.rpc, "commit", original)
    assert c.v3.registry.read("v3_pending_session_denials", str(old.session_id)) is not None
    assert (c.v3.registry.read("v3_session_tombstones", str(old.session_id)) is not None) is persisted
    with pytest.raises(GuardDenied):
        c.scoped.resolve(old)
    assert c.scoped.begin(other, action(c, other)) is not None


@pytest.mark.parametrize("persisted", [False, True])
def test_unknown_initial_registration_has_no_owner_and_exact_observed_retention(scoped, monkeypatch, persisted):
    c = scoped
    account, _ = enroll(c)
    old = session(c, account)
    original = c.v3.registry.rpc.commit
    posts = len([call for call in c.v3.test_state.requests if call[0] == "POST"])
    def lose(tx, writes):
        if "v3_pending_session_denials" not in names(writes):
            return original(tx, writes)
        if persisted:
            original(tx, writes)
        else:
            c.v3.registry.rpc.rollback(tx)
        raise AmbiguousCommit("Synthetic scoped registration acknowledgement loss")
    monkeypatch.setattr(c.v3.registry.rpc, "commit", lose)
    assert c.scoped.deny(old, scope="session") is None
    monkeypatch.setattr(c.v3.registry.rpc, "commit", original)
    assert len([call for call in c.v3.test_state.requests if call[0] == "POST"]) == posts
    if persisted:
        with pytest.raises(GuardDenied):
            c.scoped.resolve(old)
    else:
        # The earliest unpersisted request failure is NOT achieved revocation.
        assert c.scoped.resolve(old)[1].session_id == old.session_id
    assert c.v3.registry.read("v3_session_tombstones", str(old.session_id)) is None


def test_credential_high_water_rollback_denies_old_sql_revision_without_affecting_other_candidate(scoped):
    c = scoped
    a, _ = enroll(c)
    a1, a2 = session(c, a), session(c, a)
    b, _ = enroll(c, 102)
    b1 = session(c, b)
    prior = c.v3.registry.read("v3_subjects", str(a.subject_uuid))
    receipt = c.scoped.deny(a1, scope="generation", next_credential_sha256="e" * 64)
    assert receipt is not None and c.scoped.verify_denial(receipt).command.next_credential_sha256 == "e" * 64
    for context in (a1, a2):
        with pytest.raises(GuardDenied):
            c.scoped.resolve(context)
    # Same UID, old mutable current subject restored, immutable history survives.
    c.v3.registry.run(lambda tx: tx.put("v3_subjects", str(a.subject_uuid), prior))
    with pytest.raises(GuardUnavailable, match="rolled back"):
        c.scoped.resolve(a1)
    assert c.scoped.begin(b1, action(c, b1)) is not None


def test_retained_pending_denial_wins_before_actual_consuming_begin_transaction(scoped, monkeypatch):
    c = scoped
    a, _ = enroll(c)
    old, other = session(c, a), session(c, a)
    binding = action(c, old)
    original = c.v3.registry.rpc.begin
    waiting, release = Event(), Event()
    owner = get_ident()
    worker_begins = 0
    def pause():
        nonlocal worker_begins
        if get_ident() != owner:
            worker_begins += 1
            if worker_begins == 2:
                # Precheck succeeded. Pause before the ACTUAL consuming TX.
                waiting.set()
                assert release.wait(10)
        return original()
    monkeypatch.setattr(c.v3.registry.rpc, "begin", pause)
    with ThreadPoolExecutor(max_workers=2) as pool:
        pending = pool.submit(c.scoped.begin, old, binding)
        assert waiting.wait(5)
        try:
            receipt = c.scoped.deny(old, scope="session")
            assert receipt is not None
        finally:
            release.set()
        with pytest.raises((GuardDenied, GuardUnavailable)):
            pending.result(timeout=15)
    assert c.scoped.begin(other, action(c, other)) is not None
    assert not c.active.any()


def test_actual_begin_wins_first_then_denial_blocks_every_later_begin(scoped):
    c = scoped
    a, _ = enroll(c)
    old, other = session(c, a), session(c, a)
    assert c.scoped.begin(old, action(c, old)) is not None
    assert c.scoped.deny(old, scope="session") is not None
    with pytest.raises(GuardDenied):
        c.scoped.begin(old, action(c, other))
    assert c.scoped.begin(other, action(c, other)) is not None


def test_forged_denial_receipt_and_unknown_activation_never_grant_context(scoped, monkeypatch):
    c = scoped
    account, ack = enroll(c)
    with pytest.raises(GuardDenied):
        c.scoped.verify_denial(ScopedDenialAck(ack._raw, object()))
    command = CreateVerifiedPasswordWebSession(account_binding_id=account.account_binding_id,
        session_id=uuid4(), expected_auth_generation=1, credential_sha256="d" * 64, expires_at_ms=c.now + 600_000)
    result = c.lifetime.execute(c.lifetime.allocate(command))
    original = c.v3.registry.rpc.commit
    def lose(tx, writes):
        if "v3_activations" in names(writes):
            c.v3.registry.rpc.rollback(tx)
            raise AmbiguousCommit("Synthetic activation acknowledgement loss")
        return original(tx, writes)
    monkeypatch.setattr(c.v3.registry.rpc, "commit", lose)
    with pytest.raises(GuardUnavailable, match="unknown"):
        c.scoped.activate(c.lifetime, result.owned_ack)
    monkeypatch.setattr(c.v3.registry.rpc, "commit", original)
    with pytest.raises(GuardDenied, match="consumed"):
        c.scoped.activate(c.lifetime, result.owned_ack)
    assert c.v3.registry.read("v3_sessions", str(command.session_id)) is None


def test_actual_legacy_reader_with_v3_scope_refuses_retained_unknown_pending_denial(scoped, monkeypatch):
    from types import SimpleNamespace

    from test_gcp_password_reauth_emulator import candidate

    from app.domains.recovery.password_reauth import CandidateChallengeProof
    c = scoped
    account, _ = enroll(c)
    context = session(c, account)
    c.subject, c.principal, c.session = str(account.subject_uuid), account.principal_sha256, str(context.session_id)
    _, _, raw = candidate(SimpleNamespace(case=c))
    proof = CandidateChallengeProof(operation="confirm_pairing", challenge_id=raw["challenge_id"], nonce=raw["nonce"])
    boundary = GcpPasswordCandidateBoundary(c.coordinator, scoped=c.scoped)
    assert boundary.read(context, proof)[0]["subject_uuid"] == c.subject
    original = c.v3.registry.rpc.commit
    def lose(tx, writes):
        if "v3_session_tombstones" in names(writes):
            c.v3.registry.rpc.rollback(tx)
            raise AmbiguousCommit("Synthetic protected denial effect unpersisted")
        return original(tx, writes)
    monkeypatch.setattr(c.v3.registry.rpc, "commit", lose)
    assert c.scoped.deny(context, scope="session") is None
    monkeypatch.setattr(c.v3.registry.rpc, "commit", original)
    # Ordinary native remains OPEN/active, but actual composed old reader denies.
    assert c.registry.read("pairing_sessions", str(context.session_id))["active"] is True
    assert c.registry.read("control", "meta")["state"] == "OPEN"
    with pytest.raises(GuardDenied):
        boundary.read(context, proof)
