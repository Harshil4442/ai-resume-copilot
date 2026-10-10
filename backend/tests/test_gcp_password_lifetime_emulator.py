"""Real native lifetime writes; synthetic protected Storage, no cloud/credentials."""
from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from threading import Barrier, Lock
from uuid import UUID, uuid4

import pytest
from backend.tests.fixtures.gcp_closure import closed_writer
from google.api_core.exceptions import Aborted
from pydantic import ValidationError
from test_gcp_pairing_emulator import case as case

from app.domains.recovery.contracts import Revocation
from app.domains.recovery.gcp_contracts import AmbiguousCommit
from app.domains.recovery.gcp_pairing import _PairingTransaction
from app.domains.recovery.gcp_password_lifetime import GcpPasswordLifetimeCoordinator
from app.domains.recovery.gcp_password_lifetime_contracts import (
    AdvancePasswordAuthGeneration,
    BindPasswordAccount,
    CreatePasswordWebSession,
    DeletePasswordSubject,
    PasswordLifetimeIntent,
    RevokePasswordWebSession,
    TombstonePasswordAccount,
)
from app.domains.recovery.gcp_password_lifetime_journal import GcsPasswordLifetimeJournal
from app.domains.recovery.password_reauth import RegisteredPasswordIdentity
from app.domains.recovery.store import GuardDenied, GuardUnavailable


@pytest.fixture
def lifetime(case, monkeypatch):
    journal = GcsPasswordLifetimeJournal(case.journal.bucket, publication=case.publication)
    original = journal.create
    def allow(intent):
        path, raw = journal._bytes(intent)
        case.http.allowed[path] = raw
        return original(intent)
    monkeypatch.setattr(journal, "create", allow)
    case.lifetime = GcpPasswordLifetimeCoordinator(case.registry, case.pin, epoch_generation=1,
        journal=journal, fence=case.fence, now_ms=lambda: case.now)
    case.lifetime_journal = journal
    return case


def bind(c, candidate_id=1):
    command = BindPasswordAccount(candidate_id=candidate_id, account_binding_id=uuid4(),
        subject_uuid=UUID(c.subject), principal_sha256=c.principal)
    plan = c.lifetime.allocate(command)
    result = c.lifetime.execute(plan)
    assert result.status.status == "COMMITTED" and result.context is None
    c.binding_id = command.account_binding_id
    return plan


def session(c, *, generation=1):
    plan = c.lifetime.allocate(CreatePasswordWebSession(account_binding_id=c.binding_id,
        session_id=uuid4(), expected_auth_generation=generation, expires_at_ms=c.now + 600_000))
    result = c.lifetime.execute(plan)
    assert result.status.status == "COMMITTED" and result.context is not None
    return plan, result.context


class IdentityRead:
    """Test operator adapter; generic pairing remains namespace-restricted."""
    def __init__(self, tx, c):
        self.tx, self.base = tx, _PairingTransaction(tx, c.coordinator, None)
    def get(self, namespace, key):
        if namespace in {"pairing_account_bindings", "pairing_account_tombstones", "pairing_web_sessions"}:
            return self.tx.get(namespace, key)
        return self.base.get(namespace, key)
    def event(self, key):
        return self.base.event(key)


def resolve(c, context):
    captured = []
    def read(tx):
        captured.append(RegisteredPasswordIdentity(c.coordinator.core.store).resolve(
            IdentityRead(tx, c), context, c.coordinator.core, c.now))
    c.registry.run(read, before_attempt=lambda: c.fence.check(c.pin))
    return captured[-1]


def names(writes):
    return {json.loads(w.raw)["namespace"] for w in writes}


def test_real_protected_binding_session_reader_and_revoke(lifetime):
    c = lifetime
    binding_plan = bind(c)
    plan, context = session(c)
    assert resolve(c, context)["subject_uuid"] == c.subject
    assert c.lifetime.status(binding_plan.intent).status == "COMMITTED"
    before_posts = sum(method == "POST" for method, _ in c.http.calls)
    duplicate = c.lifetime.execute(plan)
    assert duplicate.status.status == "COMMITTED" and duplicate.context is None
    assert sum(method == "POST" for method, _ in c.http.calls) == before_posts
    revoke = closed_writer(c, RevokePasswordWebSession(account_binding_id=c.binding_id, session_id=context.session_id))
    assert c.lifetime.execute(revoke).status.status == "COMMITTED"
    assert c.registry.read("pairing_web_sessions", str(context.session_id))["active"] is True
    assert c.registry.read("pairing_sessions", str(context.session_id))["active"] is False
    with pytest.raises((GuardDenied, GuardUnavailable)):
        resolve(c, context)
    with pytest.raises((GuardDenied, GuardUnavailable)):
        c.lifetime.allocate(plan.intent.command)
    # A verified historical receipt remains status only; closure grants no
    # session context, signer invocation, action or new permission.
    assert c.lifetime.status(plan.intent).status == "COMMITTED"


def test_password_reset_invalidates_old_session_and_new_session_uses_new_generation(lifetime):
    c = lifetime
    bind(c)
    _, old = session(c)
    plan = closed_writer(c, AdvancePasswordAuthGeneration(account_binding_id=c.binding_id, expected_auth_generation=1))
    assert c.lifetime.execute(plan).status.status == "COMMITTED"
    with pytest.raises((GuardDenied, GuardUnavailable)):
        resolve(c, old)
    with pytest.raises((GuardDenied, GuardUnavailable)):
        session(c, generation=1)
    # This conservative first barrier pauses the whole pin; a new generation
    # cannot issue a session until a separate verified restore/open protocol.
    with pytest.raises((GuardDenied, GuardUnavailable)):
        session(c, generation=2)
    assert c.registry.read("pairing_auth_high_water", c.subject)["auth_generation"] == 2
    with pytest.raises(GuardDenied):
        c.lifetime.allocate(plan.intent.command)


@pytest.mark.parametrize("kind", ["account", "subject"])
def test_account_and_subject_deletion_consistently_deny_lifetime_reuse(lifetime, kind):
    c = lifetime
    bind(c)
    _, context = session(c)
    command = (TombstonePasswordAccount(account_binding_id=c.binding_id) if kind == "account" else
        DeletePasswordSubject(account_binding_id=c.binding_id, subject_uuid=UUID(c.subject)))
    plan = closed_writer(c, command)
    assert c.lifetime.execute(plan).status.status == "COMMITTED"
    scope = Revocation(subject_uuid=UUID(c.subject), kind="subject", target=c.subject, revision=0)
    assert c.registry.read("revocations", scope.key) == scope.model_dump(mode="json")
    assert c.registry.read("pairing_account_tombstones", str(c.binding_id)) is not None
    assert c.registry.read("subjects", c.subject)["active"] is False
    with pytest.raises((GuardDenied, GuardUnavailable)):
        resolve(c, context)
    with pytest.raises((GuardDenied, GuardUnavailable)):
        c.lifetime.allocate(CreatePasswordWebSession(account_binding_id=c.binding_id, session_id=uuid4(),
            expected_auth_generation=1, expires_at_ms=c.now + 1_000))
    with pytest.raises((GuardDenied, GuardUnavailable)):
        c.lifetime.allocate(BindPasswordAccount(candidate_id=2, account_binding_id=uuid4(),
            subject_uuid=UUID(c.subject), principal_sha256=c.principal))
    subject_before = next(item.value for item in plan.intent.effects.before if item.namespace == "subjects")
    c.operator_replace("subjects", c.subject, subject_before)
    with pytest.raises((GuardDenied, GuardUnavailable)):
        c.lifetime.allocate(BindPasswordAccount(candidate_id=1, account_binding_id=uuid4(),
            subject_uuid=UUID(c.subject), principal_sha256=c.principal))


@pytest.mark.parametrize("fault", ["principal", "subject", "event", "extra_secret", "clock", "control", "head"])
def test_no_sql_email_or_corrupt_subject_bootstrap(lifetime, fault):
    c = lifetime
    command = BindPasswordAccount(candidate_id=1, account_binding_id=uuid4(),
        subject_uuid=UUID(c.subject), principal_sha256=c.principal)
    if fault == "principal":
        command = command.model_copy(update={"principal_sha256": "0" * 64})
    elif fault == "subject":
        command = command.model_copy(update={"subject_uuid": uuid4()})
    elif fault in {"event", "extra_secret"}:
        subject = c.registry.read("subjects", c.subject)
        changed = {"registration_event_id": str(uuid4())} if fault == "event" else {"password_hash": "synthetic-forbidden-private-hash"}
        c.operator_replace("subjects", c.subject, {**subject, **changed})
    elif fault == "clock":
        c.operator_replace("pairing_clock", "observed", {"now_ms": c.now + 1})
    elif fault == "control":
        control = c.registry.read("control", "meta")
        c.operator_replace("control", "meta", {**control, "state": "CLOSED"})
    else:
        c.operator_replace("head", "global", {"sequence": True, "digest": "0" * 64})
    before = len(c.commits)
    with pytest.raises((GuardDenied, GuardUnavailable, ValidationError)):
        c.lifetime.allocate(command)
    assert len(c.commits) == before and not c.http.objects and not c.http.calls


@pytest.mark.parametrize("fault", ["bind_again", "owner", "generation", "floor", "other_subject", "session_ttl"])
def test_typed_ownership_generation_and_bounded_lifetimes(lifetime, fault):
    c = lifetime
    first = bind(c)
    command = CreatePasswordWebSession(account_binding_id=c.binding_id, session_id=uuid4(),
        expected_auth_generation=1, expires_at_ms=c.now + 600_000)
    if fault == "bind_again":
        command = first.intent.command.model_copy(update={"account_binding_id": uuid4(), "candidate_id": 2})
    elif fault == "owner":
        c.operator_replace("pairing_subject_accounts", c.subject, {"candidate_id": 2})
    elif fault == "generation":
        command = command.model_copy(update={"expected_auth_generation": 2})
    elif fault == "floor":
        c.operator_replace("pairing_auth_high_water", c.subject,
            {"subject_uuid": c.subject, "auth_generation": 2, "principal_sha256": c.principal})
    elif fault == "other_subject":
        command = DeletePasswordSubject(account_binding_id=c.binding_id, subject_uuid=uuid4())
    else:
        command = command.model_copy(update={"expires_at_ms": c.now + 86_400_001})
    count = len(c.http.calls)
    with pytest.raises((GuardDenied, GuardUnavailable)):
        c.lifetime.allocate(command)
    assert len(c.http.calls) == count


@pytest.mark.parametrize("phase", ["protected", "native"])
@pytest.mark.parametrize("persisted", [True, False])
def test_unknown_writes_never_adopted_retry_or_reconstruct_context(lifetime, monkeypatch, phase, persisted):
    c = lifetime
    bind(c)
    plan = c.lifetime.allocate(CreatePasswordWebSession(account_binding_id=c.binding_id, session_id=uuid4(),
        expected_auth_generation=1, expires_at_ms=c.now + 1_000))
    original_commit, original_create = c.rpc.commit, c.lifetime_journal.create
    if phase == "protected":
        def uncertain_create(intent):
            if persisted:
                original_create(intent)
            return None
        monkeypatch.setattr(c.lifetime_journal, "create", uncertain_create)
    else:
        def uncertain_commit(transaction, writes):
            assert "password_lifetime_operations" in names(writes)
            if persisted:
                original_commit(transaction, writes)
            else:
                c.rpc.rollback(transaction)
            raise AmbiguousCommit("Synthetic lost Commit response")
        monkeypatch.setattr(c.rpc, "commit", uncertain_commit)
    result = c.lifetime.execute(plan)
    assert result.status.status == "UNKNOWN" and result.context is None
    monkeypatch.setattr(c.rpc, "commit", original_commit)
    monkeypatch.setattr(c.lifetime_journal, "create", original_create)
    before = len(c.commits)
    again = c.lifetime.execute(plan)
    assert again.context is None and len(c.commits) == before
    assert again.status.status == ("COMMITTED" if phase == "native" and persisted else "UNKNOWN")
    fresh = GcpPasswordLifetimeCoordinator(c.registry, c.pin, epoch_generation=1,
        journal=c.lifetime_journal, fence=c.fence, now_ms=lambda: c.now)
    with pytest.raises(GuardDenied):
        fresh.execute(plan)
    assert fresh.status(plan.intent).status == again.status.status
    assert (c.registry.read("pairing_sessions", str(plan.intent.command.session_id)) is not None) == (phase == "native" and persisted)


@pytest.mark.parametrize("fault", ["before", "after_ack", "retry", "after_commit"])
def test_fresh_external_fence_before_every_native_attempt_and_after_ack(lifetime, monkeypatch, fault):
    c = lifetime
    bind(c)
    plan = c.lifetime.allocate(CreatePasswordWebSession(account_binding_id=c.binding_id, session_id=uuid4(),
        expected_auth_generation=1, expires_at_ms=c.now + 1_000))
    commit, create, fired = c.rpc.commit, c.lifetime_journal.create, []
    if fault == "before":
        c.fence.closed = True
    elif fault == "after_ack":
        def closed_ack(intent):
            receipt = create(intent)
            c.fence.closed = True
            return receipt
        monkeypatch.setattr(c.lifetime_journal, "create", closed_ack)
    else:
        def altered(transaction, writes):
            if not fired:
                fired.append(True)
                c.fence.closed = True
                if fault == "retry":
                    c.rpc.rollback(transaction)
                    raise Aborted("Synthetic definite abort then closed witness")
            return commit(transaction, writes)
        monkeypatch.setattr(c.rpc, "commit", altered)
    if fault in {"before", "after_ack"}:
        with pytest.raises(GuardUnavailable):
            c.lifetime.execute(plan)
    else:
        result = c.lifetime.execute(plan)
        assert result.status.status == "UNKNOWN" and result.context is None
    assert (c.registry.read("pairing_sessions", str(plan.intent.command.session_id)) is not None) == (fault == "after_commit")
    assert not c.active.any()


@pytest.mark.parametrize("operation", ["revoke", "generation", "delete"])
def test_stale_create_cannot_cross_revocation_reset_or_deletion(lifetime, operation):
    c = lifetime
    bind(c)
    _, context = session(c)
    create = c.lifetime.allocate(CreatePasswordWebSession(account_binding_id=c.binding_id, session_id=uuid4(),
        expected_auth_generation=1, expires_at_ms=c.now + 1_000))
    if operation == "revoke":
        command = RevokePasswordWebSession(account_binding_id=c.binding_id, session_id=context.session_id)
    elif operation == "generation":
        command = AdvancePasswordAuthGeneration(account_binding_id=c.binding_id, expected_auth_generation=1)
    else:
        command = TombstonePasswordAccount(account_binding_id=c.binding_id)
    deny = closed_writer(c, command)
    assert c.lifetime.execute(deny).status.status == "COMMITTED"
    with pytest.raises((GuardDenied, GuardUnavailable)):
        c.lifetime.execute(create)
    assert c.registry.read("pairing_sessions", str(create.intent.command.session_id)) is None


def test_two_native_writers_one_exact_cut_wins_without_retargeting(lifetime, monkeypatch):
    c = lifetime
    bind(c)
    a, b = [c.lifetime.allocate(CreatePasswordWebSession(account_binding_id=c.binding_id,
        session_id=uuid4(), expected_auth_generation=1, expires_at_ms=c.now + 1_000)) for _ in range(2)]
    create, barrier, publication_lock = c.lifetime_journal.create, Barrier(2), Lock()
    def paired(intent):
        # This case proves the ordinary-native effect cut. Admit both journal
        # owners before racing that cut; independent bounded publication
        # contention can legitimately refuse an upload owner and is a separate
        # gate, not evidence that both native commit contenders were reached.
        with publication_lock:
            receipt = create(intent)
            assert receipt is not None
        barrier.wait(timeout=10)
        return receipt
    monkeypatch.setattr(c.lifetime_journal, "create", paired)
    def attempt(plan):
        try:
            return c.lifetime.execute(plan)
        except (GuardDenied, GuardUnavailable):
            return None
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(attempt, (a, b)))
    assert sum(result is not None and result.status.status == "COMMITTED" for result in results) == 1
    assert sum(c.registry.read("pairing_sessions", str(plan.intent.command.session_id)) is not None for plan in (a, b)) == 1
    assert len(c.http.objects) == 3


def test_definite_abort_retries_same_complete_ids_bytes_and_fresh_witness(lifetime, monkeypatch):
    c = lifetime
    bind(c)
    plan = c.lifetime.allocate(CreatePasswordWebSession(account_binding_id=c.binding_id, session_id=uuid4(),
        expected_auth_generation=1, expires_at_ms=c.now + 1_000))
    commit, staged = c.rpc.commit, []
    def abort_once(transaction, writes):
        staged.append(tuple((w.path, w.raw) for w in writes))
        if len(staged) == 1:
            c.rpc.rollback(transaction)
            raise Aborted("Synthetic definite abort")
        return commit(transaction, writes)
    monkeypatch.setattr(c.rpc, "commit", abort_once)
    witness_before = c.fence.calls
    assert c.lifetime.execute(plan).status.status == "COMMITTED"
    assert len(staged) == 2 and staged[0] == staged[1]
    assert c.fence.calls - witness_before >= 5


def test_complete_protected_effects_reconstruct_deny_without_reopening(lifetime):
    c = lifetime
    bind(c)
    _, context = session(c)
    deletion = closed_writer(c, TombstonePasswordAccount(account_binding_id=c.binding_id))
    assert c.lifetime.execute(deletion).status.status == "COMMITTED"
    path, _ = c.lifetime_journal._bytes(deletion.intent)
    protected = PasswordLifetimeIntent.model_validate(json.loads(c.http.objects[path][0]))
    assert protected == deletion.intent
    before = {(record.namespace, record.key): record.value for record in protected.effects.before}
    projected = dict(before)
    for record in protected.effects.after:
        projected[record.namespace, record.key] = record.value
    assert before["subjects", c.subject]["active"] is True
    assert projected["subjects", c.subject]["active"] is False
    assert projected["pairing_account_tombstones", str(c.binding_id)]["subject_uuid"] == c.subject
    scope = Revocation(subject_uuid=UUID(c.subject), kind="subject", target=c.subject, revision=0)
    assert projected["revocations", scope.key] == scope.model_dump(mode="json")
    assert projected["events", str(protected.event_id)] == protected.effects.event
    assert projected["head", "global"]["digest"] == protected.effects.event["digest"]
    c.http.objects.pop(c.witness_transport.path)
    assert c.lifetime.status(deletion.intent).status == "UNKNOWN"
    with pytest.raises(GuardUnavailable):
        c.lifetime.allocate(CreatePasswordWebSession(account_binding_id=c.binding_id, session_id=uuid4(),
            expected_auth_generation=1, expires_at_ms=c.now + 1_000))
    assert c.registry.read("pairing_sessions", str(context.session_id))["active"] is True


def test_plan_mutation_and_generic_namespace_grants_rejected(lifetime):
    c = lifetime
    bind(c)
    plan = c.lifetime.allocate(CreatePasswordWebSession(account_binding_id=c.binding_id, session_id=uuid4(),
        expected_auth_generation=1, expires_at_ms=c.now + 1_000))
    forged = replace(plan, intent=plan.intent.model_copy(update={"subject_uuid": uuid4()}))
    with pytest.raises(GuardDenied):
        c.lifetime.execute(forged)
    for namespace in ("pairing_subject_accounts", "pairing_sql_accounts", "pairing_auth_high_water", "password_lifetime_operations"):
        with pytest.raises(GuardDenied):
            c.registry.run(lambda tx, ns=namespace: _PairingTransaction(tx, c.coordinator, None).get(ns, "any"))
    assert c.registry.read("actors", c.subject) is None and c.registry.read("grants", c.subject) is None
    protected = b" ".join(value for value, _ in c.http.objects.values()).decode()
    assert all(key not in protected for key in ('"password"', '"password_hash"', '"cookie"', '"bearer"', '"signature"'))


def test_default_unavailable_journal_does_not_enable_writer(lifetime):
    c = lifetime
    safe = GcpPasswordLifetimeCoordinator(c.registry, c.pin, epoch_generation=1,
        fence=c.fence, now_ms=lambda: c.now)
    plan = safe.allocate(BindPasswordAccount(candidate_id=1, account_binding_id=uuid4(),
        subject_uuid=UUID(c.subject), principal_sha256=c.principal))
    with pytest.raises(GuardUnavailable):
        safe.execute(plan)
    assert not c.http.objects


def test_protected_writer_to_actual_recent_password_native_consumption(lifetime, tmp_path, monkeypatch):
    from types import SimpleNamespace

    from sqlalchemy import create_engine
    from test_gcp_password_reauth_emulator import PASSWORD, SyntheticSigner, candidate

    from app.domains.recovery.gcp_password import GcpPasswordCandidateBoundary
    from app.domains.recovery.gcp_password_attempts import GcsPasswordSigning
    from app.domains.recovery.password_reauth import SqlPasswordCredentials
    from app.security import hash_password

    c = lifetime
    bind(c)
    _, context = session(c)
    c.session = str(context.session_id)
    signing = GcsPasswordSigning(c.journal.bucket)
    consume = signing.consume
    def allow_signing(marker):
        path, raw = signing._bytes(marker)
        c.http.allowed[path] = raw
        return consume(marker)
    monkeypatch.setattr(signing, "consume", allow_signing)
    original_write = c.attempts.write_intent
    def allow_intent(intent):
        c.http.allow(intent)
        return original_write(intent)
    monkeypatch.setattr(c.attempts, "write_intent", allow_intent)
    c.coordinator.password_signing = signing
    engine = create_engine(f"sqlite:///{tmp_path / 'only-synthetic-password.sqlite'}")
    with engine.begin() as db:
        db.exec_driver_sql("CREATE TABLE users (id INTEGER PRIMARY KEY, password_hash TEXT)")
        db.exec_driver_sql("INSERT INTO users VALUES (?, ?)", (1, hash_password(PASSWORD)))
    credentials = SqlPasswordCredentials(engine, dummy_hash=hash_password("synthetic-dummy"))
    signer = SyntheticSigner(c)
    native = SimpleNamespace(case=c)
    boundary = GcpPasswordCandidateBoundary(c.coordinator)
    try:
        request, _, raw = candidate(native)
        result = boundary.service(credentials=credentials, signer=signer, issuer="fixture_auth").execute(context, raw)
        assert result["status"] == "CANDIDATE_CONFIRMED" and len(signer.calls) == 1
        assert c.registry.read("pairing_confirmations", request["pairing_id"]) is not None
        complete = b" ".join(value for value, _ in c.http.objects.values()).decode()
        assert PASSWORD not in complete and raw["nonce"] not in complete
    finally:
        credentials.close()
        engine.dispose()


def test_unknown_protected_denial_cannot_leave_old_open_reader_authorized(lifetime, monkeypatch):
    c = lifetime
    bind(c)
    _, context = session(c)
    plan = closed_writer(c, TombstonePasswordAccount(account_binding_id=c.binding_id))
    def unknown_without_native_persistence(transaction, writes):
        c.rpc.rollback(transaction)
        raise AmbiguousCommit("Synthetic denied effect retained only in protected Storage")
    monkeypatch.setattr(c.rpc, "commit", unknown_without_native_persistence)
    result = c.lifetime.execute(plan)
    assert result.status.status == "UNKNOWN" and result.context is None
    path, _ = c.lifetime_journal._bytes(plan.intent)
    assert path in c.http.objects
    # The actual witness sees the independently retained closure before any
    # authority can escape, even though the native denial never persisted.
    with pytest.raises(GuardUnavailable):
        resolve(c, context)
    assert c.registry.read("pairing_account_tombstones", str(c.binding_id)) is None


@pytest.mark.parametrize("fault", ["bool_activity", "bool_owner", "bool_floor", "bool_control"])
def test_noncanonical_bool_integer_aliases_cannot_grant_lifetime(lifetime, fault):
    c = lifetime
    if fault == "bool_activity":
        subject = c.registry.read("subjects", c.subject)
        c.operator_replace("subjects", c.subject, {**subject, "active": 1})
        command = BindPasswordAccount(candidate_id=1, account_binding_id=uuid4(),
            subject_uuid=UUID(c.subject), principal_sha256=c.principal)
    else:
        bind(c)
        command = CreatePasswordWebSession(account_binding_id=c.binding_id, session_id=uuid4(),
            expected_auth_generation=1, expires_at_ms=c.now + 1_000)
        if fault == "bool_owner":
            owner = c.registry.read("pairing_subject_accounts", c.subject)
            c.operator_replace("pairing_subject_accounts", c.subject, {**owner, "candidate_id": True})
        elif fault == "bool_floor":
            floor = c.registry.read("pairing_auth_high_water", c.subject)
            c.operator_replace("pairing_auth_high_water", c.subject, {**floor, "auth_generation": True})
        else:
            control = c.registry.read("pairing_control", "current")
            c.operator_replace("pairing_control", "current", {**control, "generation": True})
    before = len(c.http.calls)
    with pytest.raises((GuardDenied, GuardUnavailable, ValidationError)):
        c.lifetime.allocate(command)
    assert len(c.http.calls) == before


@pytest.mark.parametrize("fault", ["existing", "malformed_ack", "expired_ack"])
def test_protected_create_requires_own_fresh_exact_ack_before_any_native_write(lifetime, monkeypatch, fault):
    from test_gcp_journal_sdk import response

    c = lifetime
    plan = c.lifetime.allocate(BindPasswordAccount(candidate_id=1, account_binding_id=uuid4(),
        subject_uuid=UUID(c.subject), principal_sha256=c.principal))
    create = c.lifetime_journal.create
    if fault == "existing":
        assert create(plan.intent) is not None
    elif fault == "malformed_ack":
        request = c.http.request
        def malformed(method, url, **kwargs):
            result = request(method, url, **kwargs)
            if method == "POST":
                return response(b'{"generation":"01","size":"1","name":"wrong","bucket":"wrong"}')
            return result
        c.journal.bucket.client._http.request.side_effect = malformed
    else:
        def expires(intent):
            result = create(intent)
            c.now = intent.deadline_ms
            return result
        monkeypatch.setattr(c.lifetime_journal, "create", expires)
    before = len(c.commits)
    if fault == "expired_ack":
        with pytest.raises(GuardDenied):
            c.lifetime.execute(plan)
    else:
        assert c.lifetime.execute(plan).status.status == "UNKNOWN"
    assert len(c.commits) == before and c.registry.read("pairing_account_bindings", str(plan.intent.command.account_binding_id)) is None


@pytest.mark.parametrize("operation", ["generation", "delete"])
def test_create_first_then_protected_closure_keeps_every_old_session_denied(lifetime, operation):
    c = lifetime
    bind(c)
    _, old = session(c)
    command = (AdvancePasswordAuthGeneration(account_binding_id=c.binding_id, expected_auth_generation=1)
        if operation == "generation" else TombstonePasswordAccount(account_binding_id=c.binding_id))
    _, recent = session(c)
    fresh_denial = closed_writer(c, command)
    assert c.lifetime.execute(fresh_denial).status.status == "COMMITTED"
    for context in (old, recent):
        with pytest.raises((GuardDenied, GuardUnavailable)):
            resolve(c, context)
    closed_before = next(item.value for item in fresh_denial.intent.effects.before
        if item.namespace == "control")
    assert closed_before["state"] == "CLOSED"
    assert c.lifetime.status(fresh_denial.intent).status == "COMMITTED"


@pytest.mark.parametrize("fault", ["event", "effects", "receipt", "head_bool", "head_extra", "head_bad_digest"])
def test_status_requires_complete_exact_native_and_protected_evidence(lifetime, fault):
    c = lifetime
    plan = bind(c)
    key = str(plan.intent.operation_id)
    if fault == "event":
        event = c.registry.read("events", str(plan.intent.event_id))
        c.operator_replace("events", str(plan.intent.event_id), {**event, "kind": "wrong"})
    elif fault == "effects":
        effects = c.registry.read("password_lifetime_effects", key)
        c.operator_replace("password_lifetime_effects", key, {**effects, "after": []})
    elif fault == "receipt":
        row = c.registry.read("password_lifetime_operations", key)
        c.operator_replace("password_lifetime_operations", key,
            {**row, "journal": {**row["journal"], "generation": "1"}})
    else:
        head = c.registry.read("head", "global")
        changed = ({"sequence": True} if fault == "head_bool" else
            {"extra": "unapproved"} if fault == "head_extra" else {"digest": "Z" * 64})
        c.operator_replace("head", "global", {**head, **changed})
    assert c.lifetime.status(plan.intent).status == "UNKNOWN"
    assert c.lifetime.execute(plan).status.status == "UNKNOWN"


@pytest.mark.parametrize("fault", ["retained_high_water", "registration_ahead"])
def test_partial_lifetime_loss_or_future_provenance_cannot_bootstrap_binding(lifetime, fault):
    c = lifetime
    if fault == "retained_high_water":
        c.registry.run(lambda tx: tx.put("pairing_auth_high_water", c.subject,
            {"subject_uuid": c.subject, "auth_generation": 1, "principal_sha256": c.principal}))
    else:
        c.operator_replace("head", "global", {"sequence": 0, "digest": "0" * 64})
    command = BindPasswordAccount(candidate_id=1, account_binding_id=uuid4(),
        subject_uuid=UUID(c.subject), principal_sha256=c.principal)
    before = len(c.commits)
    with pytest.raises(GuardDenied):
        c.lifetime.allocate(command)
    assert len(c.commits) == before and not c.http.objects


def rewrite_nested_effects(exported, replacement):
    """Exact audit mutation: dicts only, preserving command, UUIDs and tuples."""
    for name in ("before", "after"):
        original, current = getattr(exported.effects, name), getattr(replacement, name)
        assert [(r.namespace, r.key) for r in original] == [(r.namespace, r.key) for r in current]
        for old, new in zip(original, current, strict=True):
            if old.value is None:
                assert new.value is None
            else:
                assert new.value is not None
                old.value.clear()
                old.value.update(new.value)
    exported.effects.event.clear()
    exported.effects.event.update(replacement.event)


def current_effects(c, exported):
    captured = []
    c.registry.run(lambda tx: captured.append(c.lifetime._prepare(tx, exported.command,
        operation_id=exported.operation_id, event_id=exported.event_id, now=exported.created_at_ms)))
    return captured[-1][1]


@pytest.mark.parametrize("phase", ["public_entry", "public_fence", "create_export", "verify_export"])
def test_exact_audit_retarget_regression_preserves_original_cut_after_real_concurrent_commit(lifetime, monkeypatch, phase):
    c = lifetime
    bind(c)
    old = c.lifetime.allocate(CreatePasswordWebSession(account_binding_id=c.binding_id,
        session_id=uuid4(), expected_auth_generation=1, expires_at_ms=c.now + 600_000))
    original_digest = old.intent.digest
    session(c)  # Actual session B commits after A's immutable cut was allocated.
    replacement = current_effects(c, old.intent)
    create, verify, fence, fired = c.lifetime_journal.create, c.lifetime_journal.verify, c.fence.check, []
    if phase == "public_entry":
        rewrite_nested_effects(old.intent, replacement)
    elif phase == "public_fence":
        def changed_fence(pin):
            fence(pin)
            if not fired:
                fired.append(True)
                rewrite_nested_effects(old.intent, replacement)
        monkeypatch.setattr(c.fence, "check", changed_fence)
    elif phase == "create_export":
        def changed_create(exported):
            assert exported is not old.intent and exported.effects is not old.intent.effects
            rewrite_nested_effects(exported, replacement)
            assert exported.digest != original_digest
            return create(exported)
        monkeypatch.setattr(c.lifetime_journal, "create", changed_create)
    else:
        def changed_verify(exported, receipt):
            verify(exported, receipt)
            assert exported is not old.intent and exported.effects is not old.intent.effects
            rewrite_nested_effects(exported, replacement)
        monkeypatch.setattr(c.lifetime_journal, "verify", changed_verify)
    before = len(c.commits)
    with pytest.raises(GuardDenied):
        c.lifetime.execute(old)
    assert len(c.commits) == before
    assert c.registry.read("pairing_sessions", str(old.intent.command.session_id)) is None
    assert c.registry.read("password_lifetime_operations", str(old.intent.operation_id)) is None
    if phase.endswith("export"):
        assert old.intent.digest == original_digest
    owned = c.lifetime._issued[old.intent.operation_id]
    assert owned.sha256 == original_digest
    assert PasswordLifetimeIntent.model_validate_json(owned.raw).digest == original_digest


@pytest.mark.parametrize("phase", ["native_read", "commit_before_ack", "commit_after_ack", "definite_abort"])
def test_native_midflight_mutation_preserves_commit_bytes_and_withholds_context(lifetime, monkeypatch, phase):
    c = lifetime
    bind(c)
    plan = c.lifetime.allocate(CreatePasswordWebSession(account_binding_id=c.binding_id,
        session_id=uuid4(), expected_auth_generation=1, expires_at_ms=c.now + 600_000))
    original_digest, original_effects = plan.intent.digest, plan.intent.effects.model_dump(mode="json")
    commit, read, fired = c.rpc.commit, c.rpc.read, []
    def mutate():
        plan.intent.effects.event["payload"]["auth_generation"] = 2
        assert plan.intent.digest != original_digest
    if phase == "native_read":
        def changed_read(transaction, paths):
            result = read(transaction, paths)
            if not fired:
                fired.append(True)
                mutate()
            return result
        monkeypatch.setattr(c.rpc, "read", changed_read)
    else:
        def changed_commit(transaction, writes):
            if fired:
                raise AssertionError("A changed public plan cannot make another Commit")
            fired.append(True)
            if phase == "definite_abort":
                mutate()
                c.rpc.rollback(transaction)
                raise Aborted("Synthetic definite abort after mutable export changed")
            if phase == "commit_before_ack":
                mutate()
            result = commit(transaction, writes)
            if phase == "commit_after_ack":
                mutate()
            return result
        monkeypatch.setattr(c.rpc, "commit", changed_commit)
    if phase in {"native_read", "definite_abort"}:
        with pytest.raises(GuardDenied):
            c.lifetime.execute(plan)
        assert c.registry.read("password_lifetime_operations", str(plan.intent.operation_id)) is None
    else:
        result = c.lifetime.execute(plan)
        assert result.status.status == "UNKNOWN" and result.context is None
        assert result.status.intent_sha256 == original_digest
        assert c.registry.read("password_lifetime_operations", str(plan.intent.operation_id))["intent_sha256"] == original_digest
        assert c.registry.read("password_lifetime_effects", str(plan.intent.operation_id)) == original_effects
        assert c.registry.read("pairing_sessions", str(plan.intent.command.session_id))["auth_generation"] == 1
    assert fired == [True]
    assert c.lifetime._issued[plan.intent.operation_id].sha256 == original_digest


@pytest.mark.parametrize("phase", ["entry", "fence", "native_read", "verify_export"])
def test_status_midflight_mutation_cannot_be_adopted_as_committed_evidence(lifetime, monkeypatch, phase):
    c = lifetime
    plan = bind(c)
    original_digest = plan.intent.digest
    fence, read, verify, fired = c.fence.check, c.rpc.read, c.lifetime_journal.verify, []
    def mutate(exported):
        exported.effects.event["payload"]["candidate_id"] = 2
    if phase == "entry":
        mutate(plan.intent)
    elif phase == "fence":
        def changed_fence(pin):
            fence(pin)
            if not fired:
                fired.append(True)
                mutate(plan.intent)
        monkeypatch.setattr(c.fence, "check", changed_fence)
    elif phase == "native_read":
        def changed_read(transaction, paths):
            result = read(transaction, paths)
            if not fired:
                fired.append(True)
                mutate(plan.intent)
            return result
        monkeypatch.setattr(c.rpc, "read", changed_read)
    else:
        def changed_verify(exported, receipt):
            verify(exported, receipt)
            assert exported is not plan.intent and exported.effects is not plan.intent.effects
            mutate(exported)
        monkeypatch.setattr(c.lifetime_journal, "verify", changed_verify)
    before = len(c.commits)
    result = c.lifetime.status(plan.intent)
    assert result.status == "UNKNOWN" and result.journal is None
    assert result.intent_sha256 == (plan.intent.digest if phase == "entry" else original_digest)
    assert len(c.commits) == before
    if phase == "verify_export":
        assert plan.intent.digest == original_digest


def test_each_journal_export_is_detached_from_private_authority_and_other_exports(lifetime, monkeypatch):
    c = lifetime
    plan = c.lifetime.allocate(BindPasswordAccount(candidate_id=1, account_binding_id=uuid4(),
        subject_uuid=UUID(c.subject), principal_sha256=c.principal))
    create, verify, exports = c.lifetime_journal.create, c.lifetime_journal.verify, []
    def detached(exported):
        assert exported is not plan.intent and exported.effects is not plan.intent.effects
        assert exported.effects.event is not plan.intent.effects.event
        assert all(exported is not old and exported.effects.event is not old.effects.event for old in exports)
        exports.append(exported)
    def track_create(exported):
        detached(exported)
        return create(exported)
    def track_verify(exported, receipt):
        detached(exported)
        return verify(exported, receipt)
    monkeypatch.setattr(c.lifetime_journal, "create", track_create)
    monkeypatch.setattr(c.lifetime_journal, "verify", track_verify)
    result = c.lifetime.execute(plan)
    assert result.status.status == "COMMITTED" and len(exports) >= 4
    for exported in exports:
        exported.effects.event["payload"]["candidate_id"] = 2
    assert plan.intent.digest == result.status.intent_sha256
    assert c.lifetime.status(plan.intent).status == "COMMITTED"
