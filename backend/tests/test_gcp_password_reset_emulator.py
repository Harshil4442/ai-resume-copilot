"""Actual request-bound Native, owned PostgreSQL and authenticated HTTP probes."""
from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from threading import Lock, get_ident
from uuid import uuid4

import pytest
from backend.tests.fixtures.candidate_ingress import create, signed_headers
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import event, text
from test_candidate_scoped_lifecycle_emulator import service as service
from test_gcp_pairing_emulator import case as case
from test_gcp_password_lifetime_emulator import lifetime as lifetime
from test_gcp_scoped_authority_emulator import action, enroll, session
from test_gcp_scoped_authority_emulator import scoped as scoped

from app.database import get_db
from app.domains.candidate_accounts import service as account_module
from app.domains.candidate_accounts.service import credential_digest
from app.domains.candidate_ingress import replay
from app.domains.recovery.gcp_candidate_lifetimes import GcpProtectedCandidateLifetimes
from app.domains.recovery.gcp_contracts import AmbiguousCommit
from app.domains.recovery.gcp_partitioned_contracts import ScopedCredentialRevision
from app.domains.recovery.gcp_partitioned_export import PartitionedCutExporter
from app.domains.recovery.gcp_password_lifetime_contracts import CreateVerifiedPasswordWebSession
from app.domains.recovery.gcp_password_reset import GcpPasswordCredentialProjection
from app.domains.recovery.store import GuardDenied, GuardUnavailable
from app.models import CandidatePasswordAccount, User
from app.rate_limiter import limiter
from app.routers import candidate_accounts

OLD = "synthetic-password-only-20261009"
NEW = "synthetic-new-password-only-20261009"


def revisions(c, context, digest="e" * 64):
    subject, _ = c.scoped.resolve(context)
    old = ScopedCredentialRevision.model_validate(subject.model_dump(exclude={"enrollment_operation"}))
    return old, old.model_copy(update={"auth_generation": old.auth_generation + 1, "credential_sha256": digest})


def names(writes):
    return {json.loads(w.raw)["namespace"] for w in writes}


def accounts(c):
    a, _ = enroll(c)
    a1, a2 = session(c, a), session(c, a)
    b, _ = enroll(c, 102)
    b1 = session(c, b)
    return a, a1, a2, b1, GcpPasswordCredentialProjection(c.lifetime, c.scoped)


def test_exact_original_request_native_projection_protected_completion_and_full_cut(scoped):
    c = scoped
    a, a1, a2, b1, p = accounts(c)
    old, new = revisions(c, a1)
    plan = p.allocate(a1, old, new)
    execution = p.execute(plan)
    assert execution.status.status == "COMMITTED" and execution.owned_ack is not None
    with pytest.raises(GuardDenied, match="not completed"):
        c.scoped.check_account(new)
    for context in (a1, a2):
        with pytest.raises(GuardDenied):
            c.scoped.resolve(context)
    assert c.scoped.complete_password_reset(p, execution.owned_ack)
    with pytest.raises(GuardDenied):
        c.lifetime.allocate(CreateVerifiedPasswordWebSession(account_binding_id=a.account_binding_id,
            session_id=uuid4(), expected_auth_generation=1, credential_sha256=old.credential_sha256,
            expires_at_ms=c.now + 60_000))
    assert c.registry.read("password_credential_revisions", str(a.subject_uuid)) == {
        "subject_uuid": str(a.subject_uuid), "auth_generation": 2, "credential_sha256": new.credential_sha256}
    assert c.scoped.begin(b1, action(c, b1)) is not None
    assert c.v3.registry.read("v3_root", "current")["phase"] == "OPEN"
    repeated = p.execute(plan)
    assert repeated.status.status == "COMMITTED" and repeated.owned_ack is None
    with pytest.raises(GuardDenied):
        c.scoped.complete_password_reset(p, execution.owned_ack)
    close = c.v3.close()
    receipt = PartitionedCutExporter(c.v3).complete(close)
    assert receipt.records_count == 7 and not receipt.projection_complete and not c.active.any()


@pytest.mark.parametrize("persisted", [False, True])
def test_unknown_native_projection_blocks_old_scope_without_detached_completion(scoped, monkeypatch, persisted):
    c = scoped
    a, a1, a2, b1, p = accounts(c)
    plan = p.allocate(a1, *revisions(c, a1))
    original = c.registry.rpc.commit
    def lose(tx, writes):
        if "password_credential_revisions" in names(writes):
            if persisted:
                original(tx, writes)
            else:
                c.registry.rpc.rollback(tx)
            raise AmbiguousCommit("Synthetic reset native projection ACK loss")
        return original(tx, writes)
    monkeypatch.setattr(c.registry.rpc, "commit", lose)
    execution = p.execute(plan)
    assert execution.status.status == "UNKNOWN" and execution.owned_ack is None
    monkeypatch.setattr(c.registry.rpc, "commit", original)
    assert c.registry.read("password_credential_revisions", str(a.subject_uuid))["auth_generation"] == (2 if persisted else 1)
    for context in (a1, a2):
        with pytest.raises(GuardDenied):
            c.scoped.resolve(context)
    repeated = p.execute(plan)
    assert repeated.owned_ack is None and repeated.status.status == ("COMMITTED" if persisted else "UNKNOWN")
    assert c.scoped.begin(b1, action(c, b1)) is not None
    assert c.v3.registry.read("v3_reset_completions", str(plan.intent.operation_id)) is None
    assert c.v3.registry.read("v3_root", "current")["phase"] == "OPEN"


@pytest.mark.parametrize("persisted", [False, True])
def test_unknown_protected_completion_never_recreates_native_ack(scoped, monkeypatch, persisted):
    c = scoped
    a, a1, a2, b1, p = accounts(c)
    plan = p.allocate(a1, *revisions(c, a1))
    execution = p.execute(plan)
    assert execution.owned_ack is not None
    original = c.v3.registry.rpc.commit
    def lose(tx, writes):
        if "v3_reset_completions" in names(writes):
            if persisted:
                original(tx, writes)
            else:
                c.v3.registry.rpc.rollback(tx)
            raise AmbiguousCommit("Synthetic protected reset completion ACK loss")
        return original(tx, writes)
    monkeypatch.setattr(c.v3.registry.rpc, "commit", lose)
    assert c.scoped.complete_password_reset(p, execution.owned_ack) is False
    monkeypatch.setattr(c.v3.registry.rpc, "commit", original)
    assert (c.v3.registry.read("v3_reset_completions", str(plan.intent.operation_id)) is not None) is persisted
    with pytest.raises(GuardDenied):
        c.scoped.complete_password_reset(p, execution.owned_ack)
    with pytest.raises(GuardDenied):
        c.scoped.resolve(a1)
    assert c.scoped.begin(b1, action(c, b1)) is not None


def test_native_current_rollback_with_surviving_event_refuses_completion(scoped):
    c = scoped
    a, a1, a2, b1, p = accounts(c)
    plan = p.allocate(a1, *revisions(c, a1))
    execution = p.execute(plan)
    cmd = plan.intent.command
    before = {(r.namespace, r.key): r.value for r in cmd.native_effects.before}
    def restore(tx):
        for ns in ("subjects", "pairing_auth_high_water", "password_credential_revisions"):
            tx.put(ns, str(a.subject_uuid), before[ns, str(a.subject_uuid)])
    c.registry.run(restore)
    assert p.status(plan.intent).status == "COMMITTED"  # Historical status only.
    with pytest.raises(GuardUnavailable):
        c.scoped.complete_password_reset(p, execution.owned_ack)
    with pytest.raises(GuardDenied):
        c.scoped.resolve(a1)
    assert c.scoped.begin(b1, action(c, b1)) is not None


def test_frozen_native_cut_never_retargets_after_unrelated_native_event(scoped):
    c = scoped
    a, a1, a2, b1, p = accounts(c)
    plan = p.allocate(a1, *revisions(c, a1))
    b, _ = enroll(c, 103)
    third = session(c, b)
    execution = p.execute(plan)
    assert execution.status.status == "UNKNOWN" and execution.owned_ack is None
    assert c.registry.read("password_credential_revisions", str(a.subject_uuid))["auth_generation"] == 1
    with pytest.raises(GuardDenied):
        c.scoped.resolve(a1)
    assert c.scoped.begin(third, action(c, third)) is not None


def test_detached_plan_ack_and_wrong_revision_cannot_arm_or_complete(scoped):
    c = scoped
    a, a1, a2, b1, p = accounts(c)
    old, new = revisions(c, a1)
    with pytest.raises(GuardDenied):
        p.allocate(a1, old, new.model_copy(update={"candidate_id": 999}))
    plan = p.allocate(a1, old, new)
    with pytest.raises(GuardDenied):
        p.execute(replace(plan))
    assert c.v3.registry.read("v3_pending_subject_denials", f"{a.subject_uuid}:1") is None
    execution = p.execute(plan)
    with pytest.raises(GuardDenied):
        c.scoped.complete_password_reset(p, replace(execution.owned_ack))
    assert c.scoped.complete_password_reset(p, execution.owned_ack)


def test_two_concurrent_original_reset_requests_have_one_native_winner(scoped):
    c = scoped
    a, a1, a2, b1, p = accounts(c)
    old, new = revisions(c, a1)
    plans = [p.allocate(a1, old, new), p.allocate(a2, old, new.model_copy(update={"credential_sha256": "f" * 64}))]
    def attempt(plan):
        try:
            return p.execute(plan)
        except (GuardDenied, GuardUnavailable):
            return None
    with ThreadPoolExecutor(max_workers=2) as pool:
        outputs = list(pool.map(attempt, plans))
    winners = [v for v in outputs if v is not None and v.owned_ack is not None]
    assert len(winners) == 1 and c.scoped.complete_password_reset(p, winners[0].owned_ack)
    assert sum(c.registry.read("password_lifetime_operations", str(v.intent.operation_id)) is not None for v in plans) == 1
    assert c.scoped.begin(b1, action(c, b1)) is not None and not c.active.any()


def test_definite_abort_budget_withholds_reset_ack_and_preserves_scope_denial(scoped, monkeypatch):
    from google.api_core.exceptions import Aborted

    c = scoped
    account, first, second, unrelated, projector = accounts(c)
    old, new = revisions(c, first)
    plan = projector.allocate(first, old, new)
    original = c.registry.rpc.commit
    attempts = []

    def abort_projection(transaction, writes):
        if "password_credential_revisions" in names(writes):
            attempts.append(transaction)
            raise Aborted("Synthetic definite pre-commit reset contention")
        return original(transaction, writes)

    monkeypatch.setattr(c.registry.rpc, "commit", abort_projection)
    result = projector.execute(plan)
    assert len(attempts) == c.registry.max_attempts
    assert result.status.status == "UNKNOWN" and result.owned_ack is None
    assert c.registry.read("password_credential_revisions", str(account.subject_uuid))["auth_generation"] == 1
    for context in (first, second):
        with pytest.raises(GuardDenied):
            c.scoped.resolve(context)
    with pytest.raises(GuardDenied, match="not completed"):
        c.scoped.check_account(new)
    assert c.scoped.begin(unrelated, action(c, unrelated)) is not None
    assert c.v3.registry.read("v3_root", "current")["phase"] == "OPEN"
    repeated = projector.execute(plan)
    assert repeated.status.status == "UNKNOWN" and repeated.owned_ack is None
    assert len(attempts) == c.registry.max_attempts


@pytest.fixture
def reset_service(service, monkeypatch):
    c = service
    owners, owner_lock = {}, Lock()
    def begun(connection):
        with owner_lock:
            owners[id(connection)] = get_ident()
    def ended(connection):
        with owner_lock:
            owners.pop(id(connection), None)
    event.listen(c.account_engine, "begin", begun)
    event.listen(c.account_engine, "commit", ended)
    event.listen(c.account_engine, "rollback", ended)
    c.sql_io_seen = {"native": 0, "storage": 0}
    def outside_sql(kind):
        with owner_lock:
            assert get_ident() not in owners.values(), "Actual reset SDK IO entered its SQL credential transaction"
        c.sql_io_seen[kind] += 1
    for rpc in (c.registry.rpc, c.v3.registry.rpc):
        original = rpc.begin
        def begin(original=original):
            outside_sql("native")
            return original()
        monkeypatch.setattr(rpc, "begin", begin)
    http = c.v3.bucket.client._http
    original_request = http.request.side_effect
    def storage(method, url, **kwargs):
        outside_sql("storage")
        return original_request(method, url, **kwargs)
    http.request.side_effect = storage
    c.projector = GcpPasswordCredentialProjection(c.lifetime, c.scoped)
    c.account_service.retention.protected = GcpProtectedCandidateLifetimes(c.scoped, reset=c.projector)
    return c


def registered(c, email="candidate-a@example.invalid"):
    c.account_service.register(email=email, password=OLD, policy_version="synthetic")
    return c.account_service.login(email=email, password=OLD)


def sql_state(c, candidate):
    with c.account_service.sessions() as db:
        user = db.query(User).filter_by(id=candidate).one()
        account = db.query(CandidatePasswordAccount).filter_by(user_id=candidate).one()
        return user.password_hash, account.auth_generation, account.credential_sha256


def test_real_sql_password_change_new_login_old_session_and_old_hash_restore(reset_service):
    c = reset_service
    a1 = registered(c)
    a2 = c.account_service.login(email="candidate-a@example.invalid", password=OLD)
    b1 = registered(c, "candidate-b@example.invalid")
    before = sql_state(c, a1.context.candidate_id)
    c.account_service.change_password(a1.context, current_password=OLD, new_password=NEW)
    encoded, generation, digest = sql_state(c, a1.context.candidate_id)
    assert generation == 2 and digest == credential_digest(encoded) and encoded != before[0]
    with c.account_service.sessions() as db:
        subject_id = db.query(CandidatePasswordAccount).filter_by(user_id=a1.context.candidate_id).one().subject_uuid
    assert c.registry.read("password_credential_revisions", subject_id)["credential_sha256"] == digest
    assert c.v3.registry.read("v3_subjects", subject_id)["credential_sha256"] == digest
    assert c.v3.registry.read("v3_generation_history", f"{subject_id}:2")["credential_sha256"] == digest
    for prior in (a1, a2):
        with pytest.raises(GuardDenied):
            c.account_service.validate(prior.context, auth_generation=1)
    c.account_service.validate(b1.context, auth_generation=1)
    with pytest.raises(GuardDenied):
        c.account_service.login(email="candidate-a@example.invalid", password=OLD)
    current = c.account_service.login(email="candidate-a@example.invalid", password=NEW)
    assert current.auth_generation == 2 and current.context.session_id not in {a1.context.session_id, a2.context.session_id}
    with c.account_service.sessions() as db:
        if c.account_engine.dialect.name == "postgresql":
            db.execute(text("SET LOCAL session_replication_role = replica"))
        user = db.query(User).filter_by(id=a1.context.candidate_id).one()
        account = db.query(CandidatePasswordAccount).filter_by(user_id=a1.context.candidate_id).one()
        user.password_hash, account.auth_generation, account.credential_sha256 = before
        db.commit()
    with pytest.raises(GuardDenied):
        c.account_service.login(email="candidate-a@example.invalid", password=OLD)
    c.account_service.validate(b1.context, auth_generation=1)


@pytest.mark.parametrize("persisted", [False, True])
def test_unknown_native_reset_never_changes_sql_or_claims_success(reset_service, monkeypatch, persisted):
    c = reset_service
    old = registered(c)
    b1 = registered(c, "candidate-b@example.invalid")
    before = sql_state(c, old.context.candidate_id)
    original = c.registry.rpc.commit
    def lose(tx, writes):
        if "password_credential_revisions" in names(writes):
            if persisted:
                original(tx, writes)
            else:
                c.registry.rpc.rollback(tx)
            raise AmbiguousCommit("Synthetic ordinary native reset ACK lost")
        return original(tx, writes)
    monkeypatch.setattr(c.registry.rpc, "commit", lose)
    with pytest.raises(GuardUnavailable):
        c.account_service.change_password(old.context, current_password=OLD, new_password=NEW)
    monkeypatch.setattr(c.registry.rpc, "commit", original)
    assert sql_state(c, old.context.candidate_id) == before
    with pytest.raises(GuardDenied):
        c.account_service.validate(old.context, auth_generation=1)
    with pytest.raises(GuardDenied):
        c.account_service.login(email="candidate-a@example.invalid", password=OLD)
    c.account_service.validate(b1.context, auth_generation=1)


def test_fresh_wrong_password_cannot_register_reset_or_change_sql(reset_service):
    c = reset_service
    old = registered(c)
    before = sql_state(c, old.context.candidate_id)
    with pytest.raises(GuardDenied):
        c.account_service.change_password(old.context, current_password="synthetic-wrong-password", new_password=NEW)
    assert sql_state(c, old.context.candidate_id) == before and not c.projector._plans
    c.account_service.validate(old.context, auth_generation=1)


def test_actual_authenticated_fastapi_password_change_uses_real_native_and_sql(reset_service, monkeypatch):
    c = reset_service
    email = "candidate-reset@example.com"
    registered(c, email)
    app = FastAPI()
    app.state.limiter = limiter
    app.include_router(candidate_accounts.router, prefix="/api")
    def database():
        with c.account_service.sessions() as db:
            yield db
    app.dependency_overrides[get_db] = database  # Actual owned SQL, never fake identity.
    monkeypatch.setattr(candidate_accounts, "production_candidate_accounts", lambda: c.account_service)
    monkeypatch.setattr(account_module, "production_candidate_accounts", lambda: c.account_service)
    ingress = create(action_pins=(c.pin, c.v3.resource.pin.authority.registry))
    monkeypatch.setattr(replay, "production_candidate_ingress", lambda: ingress.ingress)
    try:
        with TestClient(app, base_url="https://testserver", client=("owned-reset-" + uuid4().hex, 12345)) as client:
            def private_post(path, payload, authorization=""):
                raw = json.dumps(payload).encode()
                return client.post(path, content=raw,
                    headers=signed_headers(ingress, path, raw, authorization=authorization))
            logged = private_post("/api/auth/candidate/v1/login", {"email": email, "password": OLD})
            assert logged.status_code == 200
            old_token = logged.json()["access_token"]
            changed = private_post("/api/auth/candidate/v1/password", {"current_password": OLD, "new_password": NEW},
                authorization="Bearer " + old_token)
            assert changed.status_code == 200 and changed.json() == {"status": "CHANGED_REAUTHENTICATION_REQUIRED"}
            assert OLD not in changed.text and NEW not in changed.text
            repeated = private_post("/api/auth/candidate/v1/password", {"current_password": OLD, "new_password": NEW},
                authorization="Bearer " + old_token)
            assert repeated.status_code == 401
            assert private_post("/api/auth/candidate/v1/login", {"email": email, "password": OLD}).status_code == 401
            assert private_post("/api/auth/candidate/v1/login", {"email": email, "password": NEW}).status_code == 200
    finally:
        ingress.channel.close()
    assert not c.active.any()


def test_actual_storage_callback_cannot_mutate_coordinator_owned_native_plan(scoped):
    from app.domains.recovery.contracts import canonical
    c = scoped
    a, a1, a2, b1, p = accounts(c)
    plan = p.allocate(a1, *revisions(c, a1))
    original_intent = plan.intent
    changed = []
    def mutate(method, url, kwargs):
        if method == "POST" and plan._raw in kwargs["data"] and not changed:
            changed.append(True)
            moved = original_intent.model_copy(update={"deadline_ms": original_intent.deadline_ms - 1})
            object.__setattr__(plan, "_raw", canonical(moved.model_dump(mode="json")).encode())
    c.v3.test_state.on_request = mutate
    execution = p.execute(plan)
    assert changed and execution.status.status == "UNKNOWN" and execution.owned_ack is None
    assert c.registry.read("password_lifetime_operations", str(original_intent.operation_id)) is None
    assert c.v3.registry.read("v3_records", str(original_intent.operation_id))["intent"] == original_intent.model_dump(mode="json")
    with pytest.raises(GuardDenied):
        c.scoped.resolve(a1)
    assert c.scoped.begin(b1, action(c, b1)) is not None


def test_protected_retention_failure_after_definite_denial_withholds_native_projection(scoped):
    c = scoped
    a, a1, a2, b1, p = accounts(c)
    plan = p.allocate(a1, *revisions(c, a1))
    def fail(method, url, kwargs):
        if method == "POST" and plan._raw in kwargs["data"]:
            raise TimeoutError("Synthetic protected reset retention failure")
    c.v3.test_state.on_request = fail
    result = p.execute(plan)
    assert result.status.status == "UNKNOWN" and result.owned_ack is None
    assert c.registry.read("password_credential_revisions", str(a.subject_uuid))["auth_generation"] == 1
    with pytest.raises(GuardDenied):
        c.scoped.resolve(a1)
    assert c.scoped.begin(b1, action(c, b1)) is not None


@pytest.mark.parametrize("persisted", [False, True])
def test_unknown_protected_completion_never_changes_real_sql(reset_service, monkeypatch, persisted):
    c = reset_service
    old = registered(c)
    other = registered(c, "candidate-b@example.invalid")
    before = sql_state(c, old.context.candidate_id)
    original = c.v3.registry.rpc.commit
    def lose(tx, writes):
        if "v3_reset_completions" in names(writes):
            if persisted:
                original(tx, writes)
            else:
                c.v3.registry.rpc.rollback(tx)
            raise AmbiguousCommit("Synthetic final protected reset completion lost ACK")
        return original(tx, writes)
    monkeypatch.setattr(c.v3.registry.rpc, "commit", lose)
    with pytest.raises(GuardUnavailable):
        c.account_service.change_password(old.context, current_password=OLD, new_password=NEW)
    monkeypatch.setattr(c.v3.registry.rpc, "commit", original)
    assert sql_state(c, old.context.candidate_id) == before
    with pytest.raises(GuardDenied):
        c.account_service.validate(old.context, auth_generation=1)
    c.account_service.validate(other.context, auth_generation=1)


def test_concurrent_real_sql_reset_requests_have_one_new_hash_and_generation(reset_service):
    c = reset_service
    first = registered(c)
    second = c.account_service.login(email="candidate-a@example.invalid", password=OLD)
    other = registered(c, "candidate-b@example.invalid")
    choices = [(first.context, NEW), (second.context, NEW + "-other")]
    def attempt(choice):
        context, password = choice
        try:
            c.account_service.change_password(context, current_password=OLD, new_password=password)
            return True
        except (GuardDenied, GuardUnavailable):
            return False
    with ThreadPoolExecutor(max_workers=2) as pool:
        outputs = list(pool.map(attempt, choices))
    assert outputs.count(True) == 1
    encoded, generation, digest = sql_state(c, first.context.candidate_id)
    assert generation == 2 and credential_digest(encoded) == digest
    winner_password = choices[outputs.index(True)][1]
    current = c.account_service.login(email="candidate-a@example.invalid", password=winner_password)
    assert current.auth_generation == 2
    for prior in (first, second):
        with pytest.raises(GuardDenied):
            c.account_service.validate(prior.context, auth_generation=1)
    c.account_service.validate(other.context, auth_generation=1)
    assert c.sql_io_seen["native"] > 0 and c.sql_io_seen["storage"] > 0


@pytest.mark.parametrize("persisted", [False, True])
def test_unknown_initial_reset_registration_has_no_false_revocation_success(scoped, monkeypatch, persisted):
    c = scoped
    a, a1, a2, b1, p = accounts(c)
    plan = p.allocate(a1, *revisions(c, a1))
    original = c.v3.registry.rpc.commit
    def lose(tx, writes):
        if "v3_pending_subject_denials" in names(writes):
            if persisted:
                original(tx, writes)
            else:
                c.v3.registry.rpc.rollback(tx)
            raise AmbiguousCommit("Synthetic first reset registration lost ACK")
        return original(tx, writes)
    monkeypatch.setattr(c.v3.registry.rpc, "commit", lose)
    output = p.execute(plan)
    monkeypatch.setattr(c.v3.registry.rpc, "commit", original)
    assert output.status.status == "UNKNOWN" and output.owned_ack is None
    assert c.registry.read("password_credential_revisions", str(a.subject_uuid))["auth_generation"] == 1
    assert c.v3.registry.read("v3_reset_completions", str(plan.intent.operation_id)) is None
    if persisted:
        with pytest.raises(GuardDenied):
            c.scoped.resolve(a1)
    else:
        # No intent/fence persisted: request honestly unachieved, never called
        # successful reset/revocation. No Native/SQL credential change occurred.
        assert c.scoped.resolve(a1)[0].auth_generation == 1
    assert c.scoped.begin(b1, action(c, b1)) is not None


@pytest.mark.parametrize("persisted", [False, True])
def test_unknown_protected_high_water_keeps_pending_fence_before_native_projection(scoped, monkeypatch, persisted):
    c = scoped
    a, a1, a2, b1, p = accounts(c)
    plan = p.allocate(a1, *revisions(c, a1))
    original = c.v3.registry.rpc.commit
    def lose(tx, writes):
        if "v3_generation_history" in names(writes):
            if persisted:
                original(tx, writes)
            else:
                c.v3.registry.rpc.rollback(tx)
            raise AmbiguousCommit("Synthetic protected reset high-water lost ACK")
        return original(tx, writes)
    monkeypatch.setattr(c.v3.registry.rpc, "commit", lose)
    output = p.execute(plan)
    monkeypatch.setattr(c.v3.registry.rpc, "commit", original)
    assert output.status.status == "UNKNOWN" and output.owned_ack is None
    assert c.registry.read("password_credential_revisions", str(a.subject_uuid))["auth_generation"] == 1
    assert c.v3.registry.read("v3_pending_subject_denials", f"{a.subject_uuid}:1") is not None
    for old in (a1, a2):
        with pytest.raises(GuardDenied):
            c.scoped.resolve(old)
    assert c.scoped.begin(b1, action(c, b1)) is not None


def test_early_sql_new_hash_cannot_activate_until_original_protected_completion(reset_service):
    from app.security import hash_password
    c = reset_service
    prior = registered(c)
    other = registered(c, "candidate-b@example.invalid")
    revision = c.account_service._current(prior.context)
    old = ScopedCredentialRevision.model_validate_json(revision.model_dump_json())
    encoded = hash_password(NEW)
    new = old.model_copy(update={"auth_generation": 2, "credential_sha256": credential_digest(encoded)})
    plan = c.projector.allocate(prior.context, old, new)
    executed = c.projector.execute(plan)
    assert executed.owned_ack is not None
    # Simulate a faulty/privileged ordinary SQL projection advancing before the
    # protected completion. The real password is valid, but it is not a grant.
    with c.account_service.sessions() as db:
        row = db.query(CandidatePasswordAccount).filter_by(user_id=prior.context.candidate_id).one()
        user = db.query(User).filter_by(id=prior.context.candidate_id).one()
        row.auth_generation, row.credential_sha256, user.password_hash = 2, new.credential_sha256, encoded
        db.commit()
    with pytest.raises(GuardDenied, match="not completed"):
        c.account_service.login(email="candidate-a@example.invalid", password=NEW)
    c.account_service.validate(other.context, auth_generation=1)
    assert c.scoped.complete_password_reset(c.projector, executed.owned_ack)
    fresh = c.account_service.login(email="candidate-a@example.invalid", password=NEW)
    assert fresh.auth_generation == 2
