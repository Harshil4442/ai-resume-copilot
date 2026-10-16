"""Actual native emulator plus real Storage SDK and synthetic intercepted HTTP.

Fresh isolated localhost databases only; no fake candidate reader, SQL rollback
substitute, ambient credentials, production signing, cloud or portal mutation.
"""
from __future__ import annotations

import hashlib
import json
import os
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
import test_gcp_pairing_emulator as pairing_tests
from backend.tests.fixtures.gcp_closure import closed_writer, install_witness
from google.api_core.exceptions import Aborted
from pydantic import ValidationError
from test_gcp_journal_sdk import response
from test_gcp_password_lifetime_emulator import bind, names, session
from test_gcp_password_lifetime_emulator import lifetime as lifetime
from test_gcp_password_reauth_emulator import candidate

from app.domains.recovery.contracts import canonical
from app.domains.recovery.gcp_closure_contracts import (
    AuthorityCloseIntent,
    NativeClosedControl,
    closure_path,
)
from app.domains.recovery.gcp_contracts import AmbiguousCommit, JournalIntent, OpeningHold
from app.domains.recovery.gcp_password_attempts import GcsPasswordSigning
from app.domains.recovery.gcp_password_closure import GcsClosedInventory
from app.domains.recovery.gcp_password_lifetime import GcpPasswordLifetimeCoordinator
from app.domains.recovery.gcp_password_lifetime_contracts import (
    AdvancePasswordAuthGeneration,
    BindPasswordAccount,
    CreatePasswordWebSession,
    DeletePasswordSubject,
    RevokePasswordWebSession,
    TombstonePasswordAccount,
)
from app.domains.recovery.gcp_service import control_record
from app.domains.recovery.pairing_contracts import CandidateAssertion, digest
from app.domains.recovery.password_reauth import CandidateChallengeProof
from app.domains.recovery.store import GuardDenied, GuardUnavailable


@pytest.fixture
def case(monkeypatch):
    # Scoped endpoint selection only. Behavior and real transactional transport
    # remain the existing fixture; no global emulator/schema reset occurs.
    endpoint = os.environ.get("HIREWIZ_DENIAL_TEST_EMULATOR_ENDPOINT", pairing_tests.ENDPOINT)
    assert endpoint in {"127.0.0.1:58877", "127.0.0.1:58879", "127.0.0.1:58880"}
    monkeypatch.setattr(pairing_tests, "ENDPOINT", endpoint)
    yield from pairing_tests.case.__wrapped__(monkeypatch)


def prepared(c):
    bind(c)
    _, context = session(c)
    c.session = str(context.session_id)
    install_witness(c)
    c.lifetime.fence = c.fence
    _, challenge, raw = candidate(SimpleNamespace(case=c))
    proof = CandidateChallengeProof(operation="confirm_pairing", challenge_id=raw["challenge_id"], nonce=raw["nonce"])
    identity, payload, now = c.coordinator.read_password_candidate(context, proof)
    assertion = CandidateAssertion(protocol_version=2, assertion_id=uuid4(), issuer="fixture_auth",
        audience="hirewiz:pairing-only", operation="confirm_pairing", subject_uuid=identity["subject_uuid"],
        auth_generation=identity["auth_generation"], principal_sha256=identity["principal_sha256"],
        session_id=context.session_id, method="password_reauth", authenticated_at_ms=now,
        issued_at_ms=now, expires_at_ms=min(now + 60_000, payload["expires_at_ms"]),
        binding_sha256=digest("candidate-context", payload), confirmed=True)
    return context, proof, assertion, challenge


def command(c, context, kind):
    if kind == "session":
        return RevokePasswordWebSession(account_binding_id=c.binding_id, session_id=context.session_id)
    if kind == "generation":
        return AdvancePasswordAuthGeneration(account_binding_id=c.binding_id, expected_auth_generation=1)
    if kind == "subject":
        return DeletePasswordSubject(account_binding_id=c.binding_id, subject_uuid=UUID(c.subject))
    return TombstonePasswordAccount(account_binding_id=c.binding_id)


@pytest.mark.parametrize("kind", ["session", "generation", "account", "subject"])
def test_retained_unknown_denial_blocks_actual_old_read_and_signing_admission(lifetime, monkeypatch, kind):
    c = lifetime
    context, proof, assertion, _ = prepared(c)
    denial = command(c, context, kind)
    closed = c.close_service.close(denial)
    assert closed.status == "COMMITTED" and closed.denial_fence is not None
    writer = GcpPasswordLifetimeCoordinator(c.registry, c.pin, epoch_generation=1,
        journal=c.lifetime_journal, closed_denial=closed.denial_fence, now_ms=lambda: c.now)
    plan = writer.allocate(denial)
    commit = c.rpc.commit
    def unknown(transaction, writes):
        assert "password_lifetime_operations" in names(writes)
        c.rpc.rollback(transaction)
        raise AmbiguousCommit("Synthetic unpersisted denial Commit")
    monkeypatch.setattr(c.rpc, "commit", unknown)
    result = writer.execute(plan)
    assert result.status.status == "UNKNOWN" and result.context is None
    path, raw = c.lifetime_journal._bytes(plan.intent)
    assert c.http.objects[path][0] == raw
    assert c.registry.read("pairing_sessions", str(context.session_id))["active"] is True
    assert c.registry.read("pairing_account_tombstones", str(c.binding_id)) is None
    monkeypatch.setattr(c.rpc, "commit", commit)
    # Simulate restoring native OPEN control while the independent close survives.
    # This is a concrete pending-denial proof, not a complete authority restore.
    c.operator_replace("control", "meta", control_record(c.pin))
    assert c.registry.read("control", "meta")["state"] == "OPEN"
    before = len(c.commits)
    for action in (lambda: c.coordinator.read_password_candidate(context, proof),
                   lambda: c.coordinator.admit_password_candidate(context, proof, assertion),
                   lambda: c.lifetime.allocate(CreatePasswordWebSession(account_binding_id=c.binding_id,
                       session_id=uuid4(), expected_auth_generation=1, expires_at_ms=c.now + 1_000))):
        with pytest.raises(GuardUnavailable, match="protected closure"):
            action()
    assert len(c.commits) == before and c.coordinator._password_admitted == {}
    assert writer.status(plan.intent).status == "UNKNOWN" and not c.active.any()


@pytest.mark.parametrize("phase", ["protected", "native"])
@pytest.mark.parametrize("persisted", [False, True])
def test_unknown_close_does_not_return_owner_and_retained_close_denies(phase, persisted, lifetime, monkeypatch):
    c = lifetime
    context, proof, _, _ = prepared(c)
    denial = command(c, context, "account")
    if phase == "protected":
        create = c.closure.create
        def unknown(body):
            if persisted:
                create(body)
            return None
        monkeypatch.setattr(c.closure, "create", unknown)
    else:
        commit = c.rpc.commit
        def unknown(transaction, writes):
            assert names(writes) == {"control"}
            if persisted:
                commit(transaction, writes)
            else:
                c.rpc.rollback(transaction)
            raise AmbiguousCommit("Synthetic unknown close Commit")
        monkeypatch.setattr(c.rpc, "commit", unknown)
    result = c.close_service.close(denial)
    assert result.status == "UNKNOWN" and result.denial_fence is None
    assert (closure_path(c.pin) in c.http.objects) == (phase == "native" or persisted)
    if phase == "native" or persisted:
        with pytest.raises(GuardUnavailable):
            c.coordinator.read_password_candidate(context, proof)
    else:
        # A truly absent close is not acknowledged as a successful denial.
        assert c.coordinator.read_password_candidate(context, proof)[0]["subject_uuid"] == c.subject


def test_native_close_races_reader_snapshot_and_final_fence_withholds_result(lifetime, monkeypatch):
    c = lifetime
    context, proof, _, _ = prepared(c)
    rollback, triggered = c.rpc.rollback, []
    def close_after_read(transaction):
        rollback(transaction)  # Native read is over before any external IO.
        if not triggered:
            triggered.append(True)
            assert c.close_service.close(command(c, context, "account")).status == "COMMITTED"
    monkeypatch.setattr(c.rpc, "rollback", close_after_read)
    with pytest.raises(GuardUnavailable, match="protected closure"):
        c.coordinator.read_password_candidate(context, proof)
    assert triggered == [True] and not c.active.any()


def test_native_close_races_signing_admission_commit_and_no_signing_authority_escapes(lifetime, monkeypatch):
    c = lifetime
    context, proof, assertion, _ = prepared(c)
    signing = GcsPasswordSigning(c.journal.bucket)
    consume = signing.consume
    def allow(marker):
        path, raw = signing._bytes(marker)
        c.http.allowed[path] = raw
        return consume(marker)
    monkeypatch.setattr(signing, "consume", allow)
    c.coordinator.password_signing = signing
    commit, fired = c.rpc.commit, []
    def close_after_admit(transaction, writes):
        result = commit(transaction, writes)
        if names(writes) == {"pairing_password_signing_attempts"} and not fired:
            fired.append(True)
            assert c.close_service.close(command(c, context, "account")).status == "COMMITTED"
        return result
    monkeypatch.setattr(c.rpc, "commit", close_after_admit)
    with pytest.raises(GuardUnavailable, match="protected closure"):
        c.coordinator.admit_password_candidate(context, proof, assertion)
    assert fired == [True] and c.coordinator._password_admitted == {}
    assert c.registry.read("pairing_password_signing_attempts", str(proof.challenge_id)) is not None
    assert not c.active.any()


def test_closed_owner_rejects_session_grants_retarget_and_second_owner(lifetime):
    c = lifetime
    bind(c)
    _, context = session(c)
    denial = command(c, context, "account")
    plan = closed_writer(c, denial)
    before = len(c.commits)
    for grant in (BindPasswordAccount(candidate_id=2, account_binding_id=uuid4(),
        subject_uuid=UUID(c.subject), principal_sha256=c.principal),
        CreatePasswordWebSession(account_binding_id=c.binding_id, session_id=uuid4(),
            expected_auth_generation=1, expires_at_ms=c.now + 1_000),
        command(c, context, "session")):
        with pytest.raises(GuardDenied):
            c.lifetime.allocate(grant)
    with pytest.raises(GuardUnavailable):
        c.close_service.close(denial)
    assert len(c.commits) == before
    assert c.lifetime.execute(plan).status.status == "COMMITTED"
    assert c.closed.control.state == "CLOSED" and not hasattr(c.close_service, "open")


def test_denial_without_protected_closed_context_is_rejected_before_journal(lifetime):
    c = lifetime
    bind(c)
    _, context = session(c)
    before = len(c.http.calls)
    with pytest.raises(GuardUnavailable, match="protected acknowledged closure"):
        c.lifetime.allocate(command(c, context, "account"))
    assert len(c.http.calls) == before and closure_path(c.pin) not in c.http.objects


@pytest.mark.parametrize("fault", ["corrupt_object", "403", "timeout"])
def test_unknown_or_any_close_object_never_becomes_absence(lifetime, fault):
    c = lifetime
    context, proof, _, _ = prepared(c)
    path = closure_path(c.pin)
    if fault == "corrupt_object":
        c.http.objects[path] = (b"corrupt closed content", "301")
    else:
        request = c.witness_transport.request
        def unavailable(method, url, **kwargs):
            if method == "GET" and "authority-closures" in url:
                if fault == "timeout":
                    raise TimeoutError("Synthetic closure read timeout")
                return response(b'{"error":{"code":403}}', status=403)
            return request(method, url, **kwargs)
        c.journal.bucket.client._http.request.side_effect = unavailable
    with pytest.raises(GuardUnavailable):
        c.coordinator.read_password_candidate(context, proof)
    assert c.coordinator._password_admitted == {}


def test_complete_closed_inventory_keeps_unknown_deny_receipt_and_no_restore_grant(lifetime, monkeypatch):
    c = lifetime
    bind(c)
    _, context = session(c)
    plan = closed_writer(c, command(c, context, "account"))
    commit = c.rpc.commit
    def unknown(transaction, writes):
        c.rpc.rollback(transaction)
        raise AmbiguousCommit("Synthetic orphan deny intent")
    monkeypatch.setattr(c.rpc, "commit", unknown)
    assert c.lifetime.execute(plan).status.status == "UNKNOWN"
    monkeypatch.setattr(c.rpc, "commit", commit)
    inventory = GcsClosedInventory(c.journal.bucket)
    result = inventory.seal(c.registry, c.closed)
    assert result is not None
    manifest, receipt = result
    assert manifest.complete_prefix is True and manifest.projection_complete is False
    assert len(manifest.entries) == 3 and len(manifest.partitions) == 1
    path, raw = c.lifetime_journal._bytes(plan.intent)
    orphan = next(item for item in manifest.entries if item.path == path)
    assert orphan.sha256 == hashlib.sha256(raw).hexdigest() and orphan.kind == "password_lifetime_effects"
    assert c.http.objects[receipt.path][0] == canonical(manifest.model_dump(mode="json")).encode()
    assert c.registry.read("pairing_account_tombstones", str(c.binding_id)) is None
    with pytest.raises(GuardUnavailable):
        c.fence.check(c.pin)
    assert not hasattr(inventory, "open") and not c.active.any()


@pytest.mark.parametrize("fault", ["omitted_page", "duplicate", "too_many", "foreign_path", "corrupt_bytes", "changed_generation", "head_changed"])
def test_incomplete_or_racing_cut_cannot_be_sealed(lifetime, monkeypatch, fault):
    c = lifetime
    bind(c)
    _, context = session(c)
    plan = closed_writer(c, command(c, context, "account"))
    assert c.lifetime.execute(plan).status.status == "COMMITTED"
    inventory = GcsClosedInventory(c.journal.bucket)
    request = c.witness_transport.request
    lists = []
    path, raw = c.lifetime_journal._bytes(plan.intent)
    def changed(method, url, **kwargs):
        if method == "GET" and urlsplit_path(url).endswith("/o"):
            lists.append(True)
            if fault == "omitted_page":
                return response(b'{"items":[],"nextPageToken":"0"}')
            if fault in {"duplicate", "too_many", "foreign_path"}:
                item = {"name": path if fault != "foreign_path" else "other/path", "generation": c.http.objects[path][1]}
                count = 65 if fault == "too_many" else 2 if fault == "duplicate" else 1
                return response(json.dumps({"items": [item] * count}).encode())
            if len(lists) == 2 and fault == "changed_generation":
                c.http.objects[path] = (raw, "999")
            if len(lists) == 2 and fault == "head_changed":
                head = c.registry.read("head", "global")
                c.operator_replace("head", "global", {**head, "sequence": head["sequence"] + 1})
        if fault == "corrupt_bytes" and method == "GET" and kwargs.get("stream"):
            from urllib.parse import unquote, urlsplit
            found = unquote(urlsplit(url).path.split("/o/", 1)[1])
            if found == path:
                return response(b"x" * len(raw))
        return request(method, url, **kwargs)
    c.journal.bucket.client._http.request.side_effect = changed
    with pytest.raises(GuardUnavailable):
        inventory.seal(c.registry, c.closed)
    assert not any(key.startswith("authority-closed-cuts/") for key in c.http.objects)
    with pytest.raises(GuardUnavailable):
        c.fence.check(c.pin)


def urlsplit_path(url):
    from urllib.parse import urlsplit
    return urlsplit(url).path


def test_definite_close_abort_retries_same_marker_and_shared_control_only(lifetime, monkeypatch):
    c = lifetime
    context, _, _, _ = prepared(c)
    commit, staged = c.rpc.commit, []
    def aborted(transaction, writes):
        staged.append(tuple((item.path, item.raw) for item in writes))
        if len(staged) == 1:
            c.rpc.rollback(transaction)
            raise Aborted("Synthetic definite close abort")
        return commit(transaction, writes)
    monkeypatch.setattr(c.rpc, "commit", aborted)
    result = c.close_service.close(command(c, context, "account"))
    assert result.status == "COMMITTED" and len(staged) == 2 and staged[0] == staged[1]
    control = NativeClosedControl.model_validate(c.registry.read("control", "meta"))
    assert control.close == result.close and control.head_at_close == result.denial_fence.control.head_at_close


def test_old_issued_session_plan_cannot_cross_unknown_native_close_without_persistence(lifetime, monkeypatch):
    c = lifetime
    context, _, _, _ = prepared(c)
    old_writer = c.lifetime
    grant = old_writer.allocate(CreatePasswordWebSession(account_binding_id=c.binding_id,
        session_id=uuid4(), expected_auth_generation=1, expires_at_ms=c.now + 1_000))
    commit = c.rpc.commit
    def unknown(transaction, writes):
        assert names(writes) == {"control"}
        c.rpc.rollback(transaction)
        raise AmbiguousCommit("Synthetic nonpersisted close")
    monkeypatch.setattr(c.rpc, "commit", unknown)
    assert c.close_service.close(command(c, context, "account")).status == "UNKNOWN"
    monkeypatch.setattr(c.rpc, "commit", commit)
    assert c.registry.read("control", "meta")["state"] == "OPEN"
    before_posts = sum(method == "POST" for method, _ in c.http.calls)
    with pytest.raises(GuardUnavailable, match="protected closure"):
        old_writer.execute(grant)
    path, _ = c.lifetime_journal._bytes(grant.intent)
    assert path not in c.http.objects and c.registry.read("pairing_sessions", str(grant.intent.command.session_id)) is None
    assert sum(method == "POST" for method, _ in c.http.calls) == before_posts


@pytest.mark.parametrize("fault", ["foreign_binding", "foreign_session", "stale_generation", "wrong_subject"])
def test_invalid_independent_denial_scope_cannot_close_whole_cohort(lifetime, fault):
    c = lifetime
    context, proof, _, _ = prepared(c)
    invalid = (TombstonePasswordAccount(account_binding_id=uuid4()) if fault == "foreign_binding" else
        RevokePasswordWebSession(account_binding_id=c.binding_id, session_id=uuid4()) if fault == "foreign_session" else
        AdvancePasswordAuthGeneration(account_binding_id=c.binding_id, expected_auth_generation=2) if fault == "stale_generation" else
        DeletePasswordSubject(account_binding_id=c.binding_id, subject_uuid=uuid4()))
    with pytest.raises((GuardDenied, GuardUnavailable)):
        c.close_service.close(invalid)
    assert closure_path(c.pin) not in c.http.objects
    assert c.coordinator.read_password_candidate(context, proof)[0]["subject_uuid"] == c.subject


@pytest.mark.parametrize("extra", [60, 61])
def test_real_sdk_exhaustive_multi_page_partition_inventory_has_hard_complete_bound(lifetime, extra):
    c = lifetime
    bind(c)
    _, context = session(c)
    install_witness(c)
    for _ in range(extra):
        retained = JournalIntent(operation_id=uuid4(), pin=c.pin,
            effect=OpeningHold(subject_uuid=uuid4(), employer_key="a" * 64, tenant_id="synthetic",
                opening_key="b" * 64, binding_sha256="c" * 64),
            created_at_ms=c.now, deadline_ms=c.now + 60_000)
        c.http.allow(retained)
        assert c.journal.write(retained).sha256 == retained.digest
    closed = c.close_service.close(command(c, context, "account"))
    assert closed.status == "COMMITTED" and closed.denial_fence is not None
    writer = GcpPasswordLifetimeCoordinator(c.registry, c.pin, epoch_generation=1,
        journal=c.lifetime_journal, closed_denial=closed.denial_fence, now_ms=lambda: c.now)
    plan = writer.allocate(command(c, context, "account"))
    if extra == 61:
        assert writer.execute(plan).status.status == "UNKNOWN"
        assert c.lifetime_journal._bytes(plan.intent)[0] not in c.http.objects
    else:
        assert writer.execute(plan).status.status == "COMMITTED"
    result = GcsClosedInventory(c.journal.bucket).seal(c.registry, closed.denial_fence)
    assert result is not None
    manifest, _ = result
    assert len(manifest.entries) == 63
    assert len(manifest.partitions) == extra + 1
    assert len(c.witness_transport.lists) == 12
    assert any(token == 48 for _, token, _ in c.witness_transport.lists)


def test_cut_deadline_loss_has_no_storage_or_native_mutation(lifetime):
    c = lifetime
    bind(c)
    _, context = session(c)
    closed_writer(c, command(c, context, "account"))
    clock = iter((0.0, 31.0))
    inventory = GcsClosedInventory(c.journal.bucket, monotonic=lambda: next(clock))
    before = len(c.http.calls), len(c.commits)
    with pytest.raises(GuardUnavailable, match="deadline"):
        inventory.seal(c.registry, c.closed)
    assert before == (len(c.http.calls), len(c.commits))


@pytest.mark.parametrize("version", [True, "1", 1.0])
def test_closure_contract_rejects_noncanonical_version_aliases(lifetime, version):
    c = lifetime
    with pytest.raises(ValidationError):
        AuthorityCloseIntent(version=version, pin=c.pin, close_id=uuid4(),
            command_sha256="d" * 64, created_at_ms=c.now)


def test_closed_denial_keeps_original_cut_and_cannot_mutate_back_to_open(lifetime, monkeypatch):
    c = lifetime
    bind(c)
    _, context = session(c)
    plan = closed_writer(c, command(c, context, "account"))
    original_digest = plan.intent.digest
    create = c.lifetime_journal.create
    def mutated(exported):
        receipt = create(exported)
        before = next(item.value for item in plan.intent.effects.before if item.namespace == "control")
        before["state"] = "OPEN"
        return receipt
    monkeypatch.setattr(c.lifetime_journal, "create", mutated)
    with pytest.raises(GuardDenied, match="bytes changed"):
        c.lifetime.execute(plan)
    path, _ = c.lifetime_journal._bytes(plan.intent)
    stored = json.loads(c.http.objects[path][0])
    assert next(item["value"] for item in stored["effects"]["before"] if item["namespace"] == "control")["state"] == "CLOSED"
    assert hashlib.sha256(c.http.objects[path][0]).hexdigest() == original_digest
    assert c.registry.read("pairing_account_tombstones", str(c.binding_id)) is None
    assert c.registry.read("control", "meta")["state"] == "CLOSED"
    detached = c.closed.control.model_dump(mode="json")
    detached["state"] = "OPEN"
    assert c.closed.control.state == "CLOSED"
    with pytest.raises(GuardUnavailable):
        c.fence.check(c.pin)


@pytest.mark.parametrize("fault", ["replaced_digest", "skipped_sequence", "missing_operation"])
def test_cut_cannot_accept_replaced_head_or_unproved_post_close_transition(lifetime, fault):
    c = lifetime
    bind(c)
    _, context = session(c)
    plan = closed_writer(c, command(c, context, "account"))
    if fault != "replaced_digest":
        assert c.lifetime.execute(plan).status.status == "COMMITTED"
    head = c.registry.read("head", "global")
    if fault == "replaced_digest":
        c.operator_replace("head", "global", {**head, "digest": "f" * 64})
    elif fault == "skipped_sequence":
        c.operator_replace("head", "global", {**head, "sequence": head["sequence"] + 1})
    else:
        c.operator_replace("password_lifetime_operations", str(plan.intent.operation_id),
            {"intent_sha256": "f" * 64, "event_id": str(plan.intent.event_id), "journal": {}})
    with pytest.raises(GuardUnavailable):
        GcsClosedInventory(c.journal.bucket).seal(c.registry, c.closed)
    assert not any(path.startswith("authority-closed-cuts/") for path in c.http.objects)
    with pytest.raises(GuardUnavailable):
        c.fence.check(c.pin)
