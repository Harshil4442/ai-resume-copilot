"""Authored actual-PG erasure races with a controlled read-only native-proof port.

Never starts/discovers a database. These component schedules do not establish
real native custody, provider/mail delivery, production owner recovery or rollout.
"""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import uuid4

import pytest
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.orm import Session
from test_schema25_app_compatibility_postgres import ROOT, app25_database  # noqa: F401

from alembic import command
from app.domains.candidate_accounts import email_erasure as recovery
from app.domains.notifications import privacy_erasure as mail
from app.models import (
    AdminAuditEvent,
    CandidatePasswordAccount,
    NotificationOutbox,
    PasswordResetToken,
    User,
)


@pytest.fixture
def owned_erasure(request, monkeypatch):
    import os
    if not os.getenv("HIREWIZ_TEST_POSTGRES_URL") or os.getenv("HIREWIZ_APP25_TEST_ROOT"):
        pytest.fail("Erasure qualification requires its exact dedicated PostgreSQL17 URL; no fallback")
    engine = request.getfixturevalue("app25_database")
    config = Config(str(ROOT / "backend/alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "backend/alembic"))
    with engine.begin() as db:
        config.attributes["connection"] = db
        command.upgrade(config, "20261031_0026")
    with Session(engine) as db:
        owner = User(id=1, email="synthetic-erasure@example.invalid", password_hash="synthetic-stored-hash", credential_epoch=7)
        db.add_all([owner, User(id=2, email="foreign-erasure@example.invalid", password_hash="foreign", credential_epoch=1)])
        db.flush()
        db.add(CandidatePasswordAccount(user_id=1, subject_uuid=str(uuid4()), account_binding_id=str(uuid4()),
            principal_sha256="a"*64, credential_sha256=recovery.digest(owner.password_hash), auth_generation=2, state="DELETE_DENIED"))
        db.commit()
    monkeypatch.setattr(mail, "configured", lambda: True)
    with Session(engine, autoflush=False) as db:
        identifier = recovery.request_erasure(db, "synthetic-erasure@example.invalid", read_native=lambda *_: "b"*64)
        item = db.get(NotificationOutbox, identifier)
        token = mail.message(item)[1].split("#token=", 1)[1].split("\n", 1)[0]
    return engine, token


def test_concurrent_same_fresh_intent_commits_one_erasure_and_one_detached_completion(owned_erasure):
    engine, token = owned_erasure
    barrier = Barrier(2)
    def confirm(_):
        with Session(engine, autoflush=False) as db:
            def native(*args):
                assert not db.in_transaction()
                barrier.wait(timeout=5)
                return "b"*64
            recovery.confirm_erasure(db, token, True, read_native=native)
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(confirm, range(2)))
    with Session(engine) as db:
        assert db.get(User, 1) is None and db.get(User, 2) is not None
        assert db.query(PasswordResetToken).count() == db.query(NotificationOutbox).count() == 0
        assert db.query(AdminAuditEvent).filter_by(action=recovery.ACTION).count() == 1
        assert db.scalar(text("SELECT count(*) FROM model_cost_liabilities")) == 0
        recovery.confirm_erasure(db, token, True, read_native=lambda *_: pytest.fail("Completed intent needs no native action"))


@pytest.mark.parametrize("committed", [False, True])
def test_rollback_and_unknown_commit_observe_the_same_completion_without_repeating_native(owned_erasure, committed):
    engine, token = owned_erasure
    with Session(engine, autoflush=False) as db:
        commit = db.commit
        def uncertain():
            if committed:
                commit()
            raise RuntimeError("Synthetic controlled commit acknowledgement loss")
        db.commit = uncertain
        with pytest.raises(recovery.ErasureUnavailable):
            recovery.confirm_erasure(db, token, True, read_native=lambda *_: "b"*64)
    with Session(engine) as db:
        assert (db.get(User, 1) is None) is committed
        assert db.query(AdminAuditEvent).filter_by(action=recovery.ACTION).count() == int(committed)
        recovery.confirm_erasure(db, token, True, read_native=lambda *_: "b"*64)
        assert db.get(User, 1) is None and db.get(User, 2) is not None


# This child imports only the pinned archived app. The retention IO and ingress
# boundary are controlled; neither prepare_delete nor the HTTP erasure body is
# replaced. ASGI TestClient needs no listening socket or external auth provider.
INTERRUPTED_OLD_DELETE = r'''
import json, select, sys
from pathlib import Path
from fastapi.testclient import TestClient
from sqlalchemy import event
import app as archived_app
from app import models
from app.database import SessionLocal, engine
from app.domains.candidate_accounts import service as accounts_module
from app.domains.candidate_accounts.service import CandidateAccountService
from app.domains.recovery.password_reauth import CandidateWebSession
from app.main import app
from app.routers import auth, candidate_ingress
from app.security import create_access_token

assert Path(archived_app.__file__).resolve().parent == Path(sys.path[0]).resolve()/'app'
fixture = json.loads(sys.stdin.readline())
context = CandidateWebSession.model_validate(fixture['context'])
calls, errors, pending = [], [], [False]
denial_keys = {'v3_records:'+fixture['denial_operation'],
    'v3_account_tombstones:'+fixture['revision']['account_binding_id'],
    'v3_subject_tombstones:'+fixture['revision']['subject_uuid']}
assert denial_keys.issubset(fixture['retained'])
native_records = {key:value for key,value in fixture['retained'].items() if key not in denial_keys}
class ControlledRetention:
    def check_session(self, shown, revision):
        assert shown == context and revision.model_dump(mode='json') == fixture['revision']
        assert not denial_keys.intersection(native_records)
        return not calls
    def delete_account(self, shown, revision):
        assert shown == context and revision.model_dump(mode='json') == fixture['revision']
        assert not calls
        calls.append('controlled-native-delete')
        native_records.update({key:fixture['retained'][key] for key in denial_keys})
        assert native_records == fixture['retained']
        pending[0] = True
        return True

accounts = CandidateAccountService(engine, ControlledRetention())
accounts_module.production_candidate_accounts = lambda: accounts
auth.require_candidate_ingress = lambda request: None  # Explicit synthetic BFF ingress only.
candidate_ingress.require_candidate_ingress = auth.require_candidate_ingress
def after_denial_commit(session):
    if not pending[0]:
        return
    assert any(isinstance(row, models.CandidatePasswordAccount) and row.user_id == 1
        and row.state == 'DELETE_DENIED' for row in session.identity_map.values())
    pending[0] = False
    print(json.dumps({'phase':'denial_committed', 'delete_calls':len(calls),
        'retained':native_records}), flush=True)
    assert select.select([sys.stdin], [], [], 30)[0], 'Bounded migration release missing'
    assert sys.stdin.readline().strip() == 'release-after-schema26'
event.listen(accounts.sessions, 'after_commit', after_denial_commit)
event.listen(engine, 'handle_error', lambda failure:
    errors.append(getattr(failure.original_exception, 'sqlstate', None)))
bearer = create_access_token(subject='1', candidate_lifetime={
    'version':1, 'context':context.model_dump(mode='json'), 'auth_generation':1})
headers = {'Authorization':'Bearer '+bearer}
with TestClient(app, raise_server_exceptions=False) as client:
    failed = client.post('/api/auth/delete-account', headers=headers)
    assert failed.status_code == 500 and errors == ['42501']
    denied = client.get('/api/auth/me', headers=headers)
    assert denied.status_code == 401
with SessionLocal() as db:
    assert db.get(models.CandidatePasswordAccount, 1).state == 'DELETE_DENIED'
    assert db.get(models.User, 1) is not None
print(json.dumps({'phase':'outer_rollback', 'sqlstate':errors[0],
    'normal_auth':denied.status_code, 'delete_calls':len(calls)}), flush=True)
'''


def _interrupted_native_fixture(revision, context, email):
    """Full typed retained records; this fixture issues no native grants or ACKs.

    Storage/SDK ownership and the production configuration/witness gate remain
    controlled ports. Actual provenance parsing and original-cookie verification
    consume these records; their success is only component source evidence.
    """
    from app.domains.recovery.contracts import canonical, fingerprint
    from app.domains.recovery.gcp_contracts import JournalReceipt, OperationStatus, RegistryPin
    from app.domains.recovery.gcp_partitioned_contracts import (
        PartitionedPin,
        PartitionedRecord,
        PartitionedResource,
        ScopedDenial,
        ScopedJournalIntent,
        ScopedSession,
        ScopedSubject,
        ScopedTombstone,
    )
    from app.domains.recovery.gcp_partitioned_publication import partitioned_intent
    from app.domains.recovery.gcp_password_lifetime_contracts import (
        CreateVerifiedPasswordWebSession,
        EnrollPasswordAccount,
        LifetimeGrantEvidence,
        LifetimeRecord,
        PasswordLifetimeEffects,
        PasswordLifetimeIntent,
    )
    from app.domains.recovery.gcp_publication_contracts import PublicationPin

    def pin(name):
        return RegistryPin(database="projects/hirewiz-owned-synthetic/databases/"+name,
            database_uid=uuid4(), authority_id=uuid4(), incarnation=1, epoch_id=uuid4())

    target = pin("retained-original")
    pin26 = PartitionedPin(authority=PublicationPin(registry=pin("retained-independent"),
        target=target, journal_bucket="synthetic-erasure-witness"), lanes=2, directory_shards=2)
    resource = PartitionedResource(pin=pin26, bucket=pin26.authority.journal_bucket,
        generation="1", sha256=fingerprint(pin26.model_dump(mode="json")))
    records = {}

    def retain(intent):
        raw = canonical(intent.model_dump(mode="json")).encode()
        reference, value = partitioned_intent(raw, resource)
        body = dict(reference=reference.model_dump(mode="json"), segment_id="a"*64,
            ordinal=1, previous="0"*64)
        full = PartitionedRecord(**body, chain_digest=fingerprint(body), intent=value)
        records["v3_records:"+str(intent.operation_id)] = full.model_dump(mode="json")
        if isinstance(intent, PasswordLifetimeIntent):
            receipt = JournalReceipt(bucket=resource.bucket, path=reference.path,
                generation="1", sha256=reference.sha256)
            status = OperationStatus(status="COMMITTED", operation_id=intent.operation_id,
                intent_sha256=intent.digest, journal=receipt)
            retained = LifetimeGrantEvidence(intent=intent, status=status.model_dump(mode="json"))
            records["v3_activations:"+str(intent.operation_id)] = retained.model_dump(mode="json")

    stamp = 1_790_000_000_000
    # These bounded complete fixture effect records are deliberately synthetic;
    # the test never treats them as independent native/provider acceptance.
    effects = PasswordLifetimeEffects(before=(LifetimeRecord(namespace="control", key="synthetic", value={}),),
        after=(LifetimeRecord(namespace="control", key="synthetic", value={}),), event={"synthetic": True})
    enrollment_id, session_id, denial_id = uuid4(), uuid4(), uuid4()
    enrollment = EnrollPasswordAccount(**revision.model_dump(exclude={"auth_generation"}),
        subject_registration_event_id=uuid4())
    session = CreateVerifiedPasswordWebSession(account_binding_id=context.account_binding_id,
        session_id=context.session_id, expected_auth_generation=1,
        credential_sha256=revision.credential_sha256, expires_at_ms=stamp+86_400_000)
    for operation, command26 in ((enrollment_id, enrollment), (session_id, session)):
        retain(PasswordLifetimeIntent(operation_id=operation, event_id=uuid4(), pin=target,
            epoch_generation=1, command=command26, subject_uuid=revision.subject_uuid,
            effects=effects, created_at_ms=stamp, deadline_ms=stamp+30_000))
    subject = ScopedSubject(**revision.model_dump(), enrollment_operation=enrollment_id)
    original_session = ScopedSession(subject_uuid=revision.subject_uuid, account_binding_id=revision.account_binding_id,
        session_id=context.session_id, auth_generation=1, principal_sha256=revision.principal_sha256,
        expires_at_ms=session.expires_at_ms, credential_sha256=revision.credential_sha256,
        activation_operation=session_id)
    denial = ScopedDenial(scope="subject", subject=subject, session=original_session)
    retain(ScopedJournalIntent(operation_id=denial_id, pin=target, command=denial,
        created_at_ms=stamp+1, deadline_ms=stamp+30_001))
    tombstone = ScopedTombstone(subject_uuid=revision.subject_uuid,
        account_binding_id=revision.account_binding_id, operation_id=denial_id).model_dump(mode="json")
    owner = {"subject_uuid": str(revision.subject_uuid), "account_binding_id": str(revision.account_binding_id),
        "candidate_id": 1, "enrollment_operation": str(enrollment_id)}
    records.update({"v3_account_tombstones:"+str(revision.account_binding_id): tombstone,
        "v3_subject_tombstones:"+str(revision.subject_uuid): tombstone,
        "v3_account_owners:"+str(revision.account_binding_id): owner,
        "v3_candidate_owners:1": owner,
        "v3_recovery_mailboxes:"+str(revision.subject_uuid): {"version": 1,
            "subject_uuid": str(revision.subject_uuid), "candidate_id": 1,
            "account_binding_id": str(revision.account_binding_id),
            "principal_sha256": revision.principal_sha256, "email_sha256": recovery.digest(email)}})
    return {"revision": revision.model_dump(mode="json"), "context": context.model_dump(mode="json"),
        "denial_operation": str(denial_id),
        "retained": records, "resource": resource.model_dump(mode="json")}


def test_archived_e23_denial_commit_then_schema26_fence_requires_fresh_owner_intent(
    request, tmp_path, monkeypatch, record_property,
):
    """Actual old HTTP/SQL + new recovery adapter; controlled native/config IO.

    Auth claims/ingress, retained native transactions and config/witness ports
    are synthetic. No production transport, native ACK custody, mailbox delivery
    or owner recovery qualification follows from this authored schedule.
    """
    import copy
    import json
    import os
    import select
    import subprocess
    import sys
    from datetime import UTC, datetime
    from types import SimpleNamespace

    import httpx
    import requests
    from fastapi import HTTPException
    from test_schema25_app_compatibility_postgres import release

    from app.domains.candidate_accounts import production
    from app.domains.candidate_accounts.contracts import CandidateCredentialRevision
    from app.domains.recovery.gcp_candidate_logout import GcpCandidateCookieLogout
    from app.domains.recovery.gcp_partitioned_contracts import PartitionedResource
    from app.domains.recovery.gcp_scoped_authority import ScopedCandidateAuthority
    from app.domains.recovery.password_reauth import CandidateWebSession
    from app.models import EntitlementLedger, EvidenceItem, ModelCostLiability, PaymentOrder, Resume
    from app.security import create_access_token, get_current_user
    from scripts import schema25_app_release_contract_v3 as contract

    if not os.getenv("HIREWIZ_TEST_POSTGRES_URL") or os.getenv("HIREWIZ_APP25_TEST_ROOT"):
        pytest.fail("Interrupted erasure requires its exact dedicated PostgreSQL17 URL; no fallback")
    engine = request.getfixturevalue("app25_database")  # Starts at25, before the old admitted call.
    archived = release.initial.prior_app_files(ROOT, contract.PRIOR_RELEASE)
    assert contract.PRIOR_RELEASE == "e23c940e6133323c3c1ff11e646a4ca9b2b0b165"
    assert release.initial.prior_app_digest(archived) == contract.PRIOR_APP_SHA256
    for name, raw in archived.items():
        target = tmp_path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(raw)
    record_property("archived_source", contract.PRIOR_RELEASE)
    record_property("archived_app_sha256", contract.PRIOR_APP_SHA256)
    record_property("native_proof_seam", "typed retained records; controlled read/config/witness ports")
    now = datetime.now(UTC)
    context = CandidateWebSession(candidate_id=1, account_binding_id=uuid4(), session_id=uuid4())
    revision = CandidateCredentialRevision(candidate_id=1, subject_uuid=uuid4(),
        account_binding_id=context.account_binding_id, principal_sha256="a"*64,
        auth_generation=1, credential_sha256=recovery.digest("synthetic-interrupted-stored-hash"))
    email = "interrupted-erasure@example.invalid"
    fixture = _interrupted_native_fixture(revision, context, email)
    with Session(engine) as db:
        db.add_all([User(id=1, email=email, password_hash="synthetic-interrupted-stored-hash", credential_epoch=7),
            User(id=2, email="foreign-interrupted@example.invalid", password_hash="foreign", credential_epoch=2)])
        db.flush()
        db.add(CandidatePasswordAccount(**revision.model_dump(mode="json", exclude={"candidate_id"}),
            user_id=1, state="ACTIVE"))
        for uid in (1, 2):
            resume = Resume(user_id=uid, original_filename="synthetic.pdf", raw_text="Owned original source",
                sections={"experience": "Owned source"}, source_document=b"%PDF-synthetic-owned-only", source_format="pdf")
            db.add(resume)
            db.flush()
            db.add(EvidenceItem(id="interrupted-evidence-"+str(uid), user_id=uid, resume_id=resume.id,
                category="experience", title="Reviewed synthetic fact", evidence_text="Reviewed owned fact.",
                source_ref="resume:owned\ntranscription-review:v1:synthetic", approval_state="approved"))
        order = PaymentOrder(public_id="interrupted-paid", user_id=1, sku="synthetic", catalog_version="synthetic",
            entitlement_kind="analysis_units", entitlement_quantity=3, billing_country_confirmed_at=now,
            gross_amount_minor=100, status="paid")
        db.add(order)
        db.flush()
        db.add_all([EntitlementLedger(user_id=1, source_order_id=order.id, source_provider="synthetic",
            sku="synthetic", entitlement_kind="analysis_units", quantity=3, starts_at=now, status="active"),
            NotificationOutbox(id="interrupted-old-outbox", user_id=1, notification_type="welcome", recipient=email,
                idempotency_key="interrupted-old-outbox", payload={"synthetic": True}, status="pending"),
            AdminAuditEvent(id="interrupted-owner-audit", actor_user_id=1, actor_email=email, action="synthetic",
                target_type="synthetic", target_id="1", reason="Synthetic rollback control", before_state={}, after_state={}),
            ModelCostLiability(id="interrupted-unknown", financial_group_id="synthetic-interrupted-group", attempt_number=1,
                provider="synthetic", model="synthetic", endpoint_key="d"*64, currency="USD", pricing_quote={"synthetic": True},
                input_token_estimate=1, reserved_cost_micros=12345, cost_state="outcome_unknown")])
        db.commit()

    def snapshot():
        with engine.connect() as db:
            return {name: db.execute(text(f"SELECT to_jsonb(r) FROM public.{name} r ORDER BY id")).scalars().all()
                for name in ("users", "resumes", "evidence_items", "payment_orders", "entitlement_ledger",
                    "notification_outbox", "admin_audit_events", "model_cost_liabilities")}

    before = snapshot()
    child = subprocess.Popen([sys.executable, "-I", "-B", "-c",
        "import sys;sys.path.insert(0,sys.argv.pop(1));"+INTERRUPTED_OLD_DELETE, str(tmp_path / "backend")],
        cwd=tmp_path / "backend", stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        env={"PATH": os.environ.get("PATH", ""), "APP_ENV": "test",
            "DATABASE_URL": engine.url.render_as_string(hide_password=False),
            "JWT_SECRET": "interrupted-erasure-synthetic-source-only", "RATE_LIMIT_STORAGE_URL": "memory://",
            "AUTO_DB_MIGRATE": "false", "PYTHON_DOTENV_DISABLED": "1", "OTEL_SDK_DISABLED": "true",
            **{name: "false" for name in release.initial.OFF}})
    try:
        assert child.stdin is not None and child.stdout is not None
        child.stdin.write(json.dumps(fixture)+"\n")
        child.stdin.flush()
        assert select.select([child.stdout], [], [], 20)[0], "Old denial commit did not reach its bounded barrier"
        admitted = json.loads(child.stdout.readline())
        assert admitted == {"phase": "denial_committed", "delete_calls": 1, "retained": fixture["retained"]}
        with Session(engine) as db:
            assert db.get(CandidatePasswordAccount, 1).state == "DELETE_DENIED"
        assert snapshot() == before  # Independent denial never claims SQL privacy completion.
        cfg = Config(str(ROOT / "backend/alembic.ini"))
        cfg.set_main_option("script_location", str(ROOT / "backend/alembic"))
        with engine.begin() as db:
            cfg.attributes["connection"] = db
            command.upgrade(cfg, "20261031_0026")
        child.stdin.write("release-after-schema26\n")
        child.stdin.flush()
        output, _ = child.communicate(timeout=30)
        assert child.returncode == 0, "Archived controlled HTTP schedule failed; no provider/auth acceptance asserted"
        assert json.loads(output.strip()) == {"phase": "outer_rollback", "sqlstate": "42501", "normal_auth": 401, "delete_calls": 1}
    finally:
        if child.poll() is None:
            child.terminate()
            try:
                child.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                child.kill()
                child.communicate(timeout=5)
        for stream in (child.stdin, child.stdout, child.stderr):
            if stream is not None:
                stream.close()
    assert snapshot() == before  # Full reviewed source, old outbox, money links and unknown liability rolled back.

    # Actual new read-only adapter and actual original-cookie provenance parser.
    # Only their retained-record transaction and config/witness gates are ports;
    # object.__new__ avoids constructing any SDK client, registry, bucket or RPC.
    retained, observations, posts = copy.deepcopy(admitted["retained"]), [], []
    resource = PartitionedResource.model_validate(fixture["resource"])
    class ReadOnlyRecords:
        def get(self, namespace, key):
            observations.append((namespace, key))
            return copy.deepcopy(retained.get(namespace+":"+key))
    scoped = object.__new__(ScopedCandidateAuthority)
    def root(tx, *, opened):
        assert opened is True and isinstance(tx, ReadOnlyRecords)
        observations.append("controlled-witness-root")
    scoped.publication = SimpleNamespace(resource=resource, _root=root)
    scoped._run = lambda read: read(ReadOnlyRecords())
    logout = GcpCandidateCookieLogout(scoped)
    def controlled_configuration_gate(observe):
        assert not db.in_transaction(), "Native ownership reads cannot hold SQL transactions or locks"
        observations.append("controlled-config-and-witness-before")
        result = observe(SimpleNamespace(cookie_logout=logout))
        observations.append("controlled-config-and-witness-after")
        return result
    configured = SimpleNamespace(_raw=b"synthetic-pinned-config-only", _invoke=controlled_configuration_gate)
    monkeypatch.setattr(production, "production_candidate_accounts", lambda: configured)
    monkeypatch.setenv("APP_RELEASE", "c"*40)  # Synthetic component source binding, never serving identity.
    monkeypatch.setattr(mail, "configured", lambda: True)
    def no_post(*args, **kwargs):
        posts.append("forbidden-post")
        raise AssertionError("Recovery must never issue a second native/provider POST")
    monkeypatch.setattr(requests.Session, "request", no_post)
    monkeypatch.setattr(httpx.Client, "post", no_post)
    with Session(engine, autoflush=False) as db:
        token26 = create_access_token(subject="1", credential_epoch=7)
        with pytest.raises(HTTPException) as denied:
            get_current_user(token=token26, db=db)
        assert denied.value.status_code == 401
        db.rollback()
        with pytest.raises(recovery.ErasureLinkInvalid):
            recovery.confirm_erasure(db, "A"*43, True)  # Generic denial alone supplies no erasure intent.
        db.rollback()
        notification = recovery.request_erasure(db, email)
        item = db.get(NotificationOutbox, notification)
        proof = db.query(PasswordResetToken).one()
        assert proof.purpose == "privacy_erasure" and proof.status == "PENDING"
        token = mail.message(item)[1].split("#token=", 1)[1].split("\n", 1)[0]
        with pytest.raises(recovery.ErasureLinkInvalid):
            recovery.confirm_erasure(db, token, False)
        db.rollback()
        assert db.get(User, 1) is not None and db.get(User, 2) is not None
        db.rollback()
        recovery.confirm_erasure(db, token, True)
        audit = db.query(AdminAuditEvent).filter_by(action=recovery.ACTION).one()
        assert audit.actor_user_id is None and audit.after_state["owner_id"] == 1
        assert audit.after_state["confirmed"] is True and audit.after_state["token_sha256"] == recovery.lookup(token)
        assert audit.after_state["purpose"] == "privacy_erasure"
        assert db.get(User, 1) is None and db.get(User, 2) is not None
        assert db.query(EvidenceItem).filter_by(user_id=1).count() == db.query(Resume).filter_by(user_id=1).count() == 0
        assert db.query(NotificationOutbox).filter_by(user_id=1).count() == db.query(PasswordResetToken).count() == 0
        recovery.confirm_erasure(db, token, True)  # Exact audit joins the same fresh intent, no native read/action.
    after = snapshot()
    assert after["model_cost_liabilities"] == before["model_cost_liabilities"]
    for name in ("users", "resumes", "evidence_items"):
        assert [row for row in after[name] if row.get("user_id", row.get("id")) == 2] == [
            row for row in before[name] if row.get("user_id", row.get("id")) == 2]
    assert after["payment_orders"][0]["user_id"] is None and after["payment_orders"][0]["status"] == "paid"
    assert after["entitlement_ledger"][0]["user_id"] is None
    assert after["entitlement_ledger"][0]["status"] == "ended_account_deleted"
    assert retained == admitted["retained"] and posts == []
    assert observations.count("controlled-config-and-witness-before") == observations.count("controlled-config-and-witness-after") == 2
