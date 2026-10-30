"""Pure controlled ports and crypto only; no DB, native SDK, mail or network."""
from __future__ import annotations

import copy
import hashlib
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from cryptography.exceptions import InvalidTag

from app.domains.candidate_accounts import deletion_provenance, email_reset
from app.domains.candidate_accounts import email_erasure as erasure
from app.domains.notifications import privacy_erasure as mail
from app.models import (
    AdminAuditEvent,
    CandidatePasswordAccount,
    NotificationOutbox,
    PasswordResetToken,
    User,
)
from app.routers.privacy_erasure import ErasureConfirm

NOW = datetime.now(UTC)


def test_closed_ownership_uses_actual_configured_canonical_text_and_witness_boundary(monkeypatch):
    """Real configuration/composition shell; retained ownership reads are inert ports."""
    from app.domains.candidate_accounts import production
    from app.domains.candidate_accounts.production import (
        CandidateNativeConfiguration,
        ConfiguredCandidateAccounts,
    )
    from app.domains.candidate_accounts.service import credential_digest
    from app.domains.recovery.contracts import canonical, fingerprint
    from app.domains.recovery.gcp_contracts import RegistryPin, WitnessBody, WitnessPin
    from app.domains.recovery.gcp_partitioned_contracts import PartitionedPin, PartitionedResource
    from app.domains.recovery.gcp_publication_contracts import PublicationPin
    from app.domains.recovery.password_reauth import CandidateWebSession
    from app.domains.recovery.store import GuardDenied

    execution = RegistryPin(database="projects/synthetic-mail-proof/databases/execution", database_uid=uuid4(),
        authority_id=uuid4(), incarnation=1, epoch_id=uuid4())
    protected = execution.model_copy(update={"database": "projects/synthetic-mail-proof/databases/protected",
        "database_uid": uuid4(), "authority_id": uuid4(), "epoch_id": uuid4()})
    pin = PartitionedPin(authority=PublicationPin(registry=protected, target=execution,
        journal_bucket="synthetic-mail-proof"), lanes=2, directory_shards=2)
    resource = PartitionedResource(pin=pin, bucket=pin.authority.journal_bucket, generation="101",
        sha256=fingerprint(pin.model_dump(mode="json")))
    body = WitnessBody(registry=execution, state="OPEN", manifest_generation="1",
        manifest_sha256="a" * 64, partitions_sha256="b" * 64)
    witness = WitnessPin(registry=execution, bucket=resource.bucket, path="candidate/witness", generation="102",
        sha256=fingerprint(body.model_dump(mode="json")))
    config = CandidateNativeConfiguration(execution_witness_pin=witness, execution_witness_body=body,
        publication_resource=resource, epoch_generation=1)
    configured = ConfiguredCandidateAccounts(config)
    assert type(configured._raw) is str and configured._raw == canonical(config.model_dump(mode="json"))
    assert len(configured._raw.encode()) > 1024
    with pytest.raises(GuardDenied):
        credential_digest(configured._raw)  # Password limits are not configuration limits.
    port = Port()
    revision = deletion_provenance.closed_revision(port.get(CandidatePasswordAccount, 1), port.get(User, 1))
    context = CandidateWebSession(candidate_id=revision.candidate_id, account_binding_id=revision.account_binding_id,
        session_id=uuid4(), authentication_method="password")
    trace = []
    first = {"controlled_owned_revision": revision.model_dump(mode="json")}
    def denied(candidate, *, auth_generation):
        assert candidate == context and auth_generation == revision.auth_generation
        trace.append("denial")
        return True
    logout = SimpleNamespace(confirm_deleted_cookie=denied)
    def read(current, shown_revision, email_sha):
        assert current is logout and shown_revision == revision and email_sha == "d" * 64
        trace.append("owned-read")
        return context, first
    def configuration():
        trace.append("configuration")
        return config
    fence = SimpleNamespace(pin=SimpleNamespace(registry=execution), check=lambda expected: trace.append("witness"))
    composition = SimpleNamespace(service=SimpleNamespace(cookie_logout=logout), fence=fence)
    monkeypatch.setattr(production, "_configuration", configuration)
    monkeypatch.setattr(production, "_build", lambda expected: composition)
    monkeypatch.setattr(production, "production_candidate_accounts", lambda: configured)
    monkeypatch.setattr(deletion_provenance, "_read", read)
    monkeypatch.setenv("APP_RELEASE", "c" * 40)
    proof = deletion_provenance.read_closed_ownership(revision, "d" * 64)
    assert proof == fingerprint({"ownership": first, "configuration_sha256": hashlib.sha256(configured._raw.encode()).hexdigest(),
        "application_source": "c" * 40, "adapter_sha256": hashlib.sha256(Path(deletion_provenance.__file__).read_bytes()).hexdigest()})
    assert trace == ["configuration", "witness", "owned-read", "denial", "owned-read", "configuration", "witness"]


class Query:
    def __init__(self, db, model):
        self.db, self.model, self.conditions = db, model, []

    def filter_by(self, **values):
        self.conditions.extend(values.items())
        return self

    def order_by(self, *args):
        return self

    def with_for_update(self, **kwargs):
        self.db.locked = True
        self.db.trace.append(("locked", self.model.__name__))
        return self

    def all(self):
        return [row for row in self.db.rows if type(row) is self.model
            and all(getattr(row, key) == value for key, value in self.conditions)]

    def one_or_none(self):
        rows = self.all()
        assert len(rows) <= 1
        return next(iter(rows), None)

    def first(self):
        return next(iter(self.all()), None)

    def __iter__(self):
        return iter(self.all())


class Port:
    def __init__(self):
        owner = User(id=1, email="erasure-owner@example.invalid", password_hash="synthetic-stored-hash", credential_epoch=7)
        account = CandidatePasswordAccount(user_id=1, subject_uuid=str(uuid4()), account_binding_id=str(uuid4()),
            principal_sha256="a"*64, credential_sha256=erasure.digest(owner.password_hash), auth_generation=2, state="DELETE_DENIED")
        self.rows = [owner, account, User(id=2, email="foreign@example.invalid", password_hash="foreign-hash", credential_epoch=4)]
        self.saved, self.locked, self.active = copy.deepcopy(self.rows), False, False
        self.trace, self.commit_mode, self.erased = [], None, 0

    def query(self, model):
        self.active = True
        return Query(self, model)

    def get(self, model, identifier, **kwargs):
        self.active = True
        key = "user_id" if model is CandidatePasswordAccount else "id"
        return next((row for row in self.rows if type(row) is model and getattr(row, key) == identifier), None)

    def add(self, row):
        self.trace.append(("added", type(row).__name__))
        self.rows.append(row)

    def rollback(self):
        self.rows, self.active, self.locked = copy.deepcopy(self.saved), False, False

    def commit(self):
        if self.commit_mode == "before":
            raise RuntimeError("Synthetic failed commit")
        self.saved = copy.deepcopy(self.rows)
        self.active, self.locked = False, False
        if self.commit_mode == "after":
            self.commit_mode = None
            raise RuntimeError("Synthetic unknown committed result")

    def flush(self, items=None):
        self.trace.append("flush")


@pytest.fixture
def flow(monkeypatch):
    from app.domains.notifications import mail_budget
    db = Port()
    monkeypatch.setattr(erasure, "_schema26", lambda port: None)
    monkeypatch.setattr(mail, "configured", lambda: True)
    monkeypatch.setattr(mail_budget, "reserve_messages", lambda port, keys, **kwargs: port.trace.append(("reserved", tuple(keys))))

    def lock(port, uid):
        assert uid == 1
        port.active = port.locked = True
        port.trace.append("owner_application_then_analysis_locks")

    def erase(port, uid):
        assert port.locked and uid == 1
        port.erased += 1
        port.rows = [row for row in port.rows if not (
            isinstance(row, User) and row.id == uid or not isinstance(row, User) and getattr(row, "user_id", None) == uid)]

    monkeypatch.setattr(erasure, "lock_account_erasure", lock)
    monkeypatch.setattr(erasure, "erase_account_sql", erase)
    monkeypatch.setattr(erasure, "_no_live_owner", lambda port, uid: None if port.get(User, uid) is None else pytest.fail("Owner remains"))
    calls = []

    def native(revision, mailbox):
        assert not db.active and not db.locked, "No SQL transaction may cross native ownership reads"
        assert revision.candidate_id == 1 and mailbox == erasure.digest("erasure-owner@example.invalid")
        calls.append(revision)
        return "b"*64

    return db, native, calls


def issued(flow):
    db, native, _ = flow
    identifier = erasure.request_erasure(db, "erasure-owner@example.invalid", now=NOW, read_native=native)
    item = db.get(NotificationOutbox, identifier)
    token = mail.message(item)[1].split("#token=", 1)[1].split("\n", 1)[0]
    db.rollback()
    return token


def test_fresh_purpose_token_is_separate_from_reset_and_ciphertext_is_authenticated(flow):
    token = issued(flow)
    db, _, _ = flow
    row = next(row for row in db.rows if isinstance(row, PasswordResetToken))
    item = next(row for row in db.rows if isinstance(row, NotificationOutbox))
    assert row.purpose == "privacy_erasure" and row.expires_at == NOW+timedelta(minutes=10)
    assert erasure.valid_token(row, token) and not email_reset._valid_token(row, token)
    assert row.token_sha256 == erasure.lookup(token) != email_reset.digest(token)
    assert token not in str(item.payload) and item.status == "erasure_pending"
    item.recipient = "foreign@example.invalid"
    with pytest.raises(InvalidTag):
        mail.message(item)


def test_actual_e23_reset_crypto_and_lookup_reject_the_raw_erasure_token(flow):
    import ast
    import base64
    import hashlib
    import hmac
    import json
    import subprocess
    token = issued(flow)
    db, _, _ = flow
    row = next(row for row in db.rows if isinstance(row, PasswordResetToken))
    root = Path(__file__).resolve().parents[2]
    raw = subprocess.run(["git", "show", "e23c940e6133323c3c1ff11e646a4ca9b2b0b165:backend/app/domains/candidate_accounts/email_reset.py"],
        cwd=root, check=True, capture_output=True).stdout
    tree = ast.parse(raw)
    functions = [node for node in tree.body if isinstance(node, ast.FunctionDef)
        and node.name in {"digest", "_token_mac", "_valid_token"}]
    namespace = {"base64": base64, "hashlib": hashlib, "hmac": hmac, "json": json,
        "UTC": UTC, "JWT_SECRET": erasure.JWT_SECRET, "PasswordResetToken": PasswordResetToken}
    exec(compile(ast.Module(body=functions, type_ignores=[]), "actual-e23-reset-crypto", "exec"), namespace)
    assert namespace["digest"](token) != row.token_sha256
    assert namespace["_valid_token"](row, token) is False


def test_exact_completion_survives_token_user_fk_deletion_and_same_token_retries(flow):
    token = issued(flow)
    db, native, calls = flow
    erasure.confirm_erasure(db, token, True, now=NOW, read_native=native)
    assert db.erased == 1 and db.get(User, 1) is None and db.get(User, 2) is not None
    audit = next(row for row in db.rows if isinstance(row, AdminAuditEvent))
    assert audit.actor_user_id is None and audit.after_state["confirmed"] is True
    before = len(calls)
    erasure.confirm_erasure(db, token, True, now=NOW+timedelta(days=1), read_native=native)
    assert len(calls) == before and db.erased == 1


@pytest.mark.parametrize("mode", ["before", "after"])
def test_failed_or_unknown_commit_preserves_atomicity_and_reconciles_only_same_token(flow, mode):
    token = issued(flow)
    db, native, calls = flow
    db.commit_mode = mode
    with pytest.raises(erasure.ErasureUnavailable):
        erasure.confirm_erasure(db, token, True, now=NOW, read_native=native)
    if mode == "before":
        assert db.get(User, 1) is not None and not any(isinstance(row, AdminAuditEvent) for row in db.rows)
        assert next(row for row in db.rows if isinstance(row, PasswordResetToken)).status == "PENDING"
        db.rollback()
        db.commit_mode = None
    before = len(calls)
    erasure.confirm_erasure(db, token, True, now=NOW, read_native=native)
    assert db.get(User, 1) is None
    assert len(calls) == before + (mode == "before")


@pytest.mark.parametrize("change", ["expired", "email", "credential", "epoch", "state", "native", "purpose"])
def test_stale_or_cross_purpose_proof_never_erases(flow, change):
    token = issued(flow)
    db, native, _ = flow
    owner, account = db.get(User, 1), db.get(CandidatePasswordAccount, 1)
    row = next(row for row in db.rows if isinstance(row, PasswordResetToken))
    if change == "expired":
        row.expires_at = NOW-timedelta(seconds=1)
    elif change == "email":
        owner.email = "changed@example.invalid"
    elif change == "credential":
        owner.password_hash = "changed"
    elif change == "epoch":
        owner.credential_epoch += 1
    elif change == "state":
        account.state = "ACTIVE"
    elif change == "purpose":
        row.purpose = "password_reset"
    else:
        def native(*_):
            return "c"*64
    db.commit()
    with pytest.raises((erasure.ErasureLinkInvalid, erasure.ErasureUnavailable)):
        erasure.confirm_erasure(db, token, True, now=NOW, read_native=native)
    assert db.erased == 0 and db.get(User, 2) is not None


@pytest.mark.parametrize("value", [False, 1, "true", None])
def test_explicit_fresh_confirmation_is_required_before_any_io(value):
    with pytest.raises(ValueError):
        ErasureConfirm(token="a"*43, confirm_erasure=value)
    with pytest.raises(erasure.ErasureLinkInvalid):
        erasure.confirm_erasure(object(), "a"*43, value)


def test_native_denial_and_sql_state_alone_never_complete_erasure(flow):
    db, native, _ = flow
    with pytest.raises(erasure.ErasureLinkInvalid):
        erasure.confirm_erasure(db, "a"*43, True, now=NOW, read_native=native)
    assert db.erased == 0 and db.get(User, 1) is not None
    account = db.get(CandidatePasswordAccount, 1)
    account.credential_sha256 = "e"*64
    with pytest.raises(ValueError):
        deletion_provenance.closed_revision(account, db.get(User, 1))


@pytest.mark.parametrize("stage", ["native", "locks", "precommit"])
@pytest.mark.parametrize("late", [timedelta(0), timedelta(microseconds=1)])
def test_default_clock_refreshes_after_native_lock_and_sql_work_without_ttl_extension(flow, monkeypatch, stage, late):
    token = issued(flow)
    db, _, _ = flow
    expiry = next(row.expires_at for row in db.rows if isinstance(row, PasswordResetToken))
    clock = {"now": expiry-timedelta(microseconds=1)}
    monkeypatch.setattr(erasure, "utcnow", lambda: clock["now"])
    lock, erase = erasure.lock_account_erasure, erasure.erase_account_sql
    def native(*_):
        assert not db.active and not db.locked
        if stage == "native":
            clock["now"] = expiry+late
        return "b"*64
    def delayed_lock(port, uid):
        lock(port, uid)
        if stage == "locks":
            clock["now"] = expiry+late
    def delayed_erase(port, uid):
        erase(port, uid)
        if stage == "precommit":
            clock["now"] = expiry+late
    monkeypatch.setattr(erasure, "lock_account_erasure", delayed_lock)
    monkeypatch.setattr(erasure, "erase_account_sql", delayed_erase)
    with pytest.raises(erasure.ErasureLinkInvalid):
        erasure.confirm_erasure(db, token, True, read_native=native)
    assert db.get(User, 1) is not None and not any(isinstance(row, AdminAuditEvent) for row in db.rows)
    assert next(row for row in db.rows if isinstance(row, PasswordResetToken)).status == "PENDING"


def test_request_does_not_accept_token_or_outbox_expired_while_waiting_for_owner_lock(flow, monkeypatch):
    db, _, _ = flow
    clock = {"now": NOW}
    monkeypatch.setattr(erasure, "utcnow", lambda: clock["now"])
    original = Query.with_for_update
    def lock(query, **kwargs):
        result = original(query, **kwargs)
        clock["now"] = NOW+timedelta(minutes=10)
        return result
    monkeypatch.setattr(Query, "with_for_update", lock)
    assert erasure.request_erasure(db, "erasure-owner@example.invalid", read_native=lambda *_: "b"*64) is None
    assert not any(isinstance(row, (PasswordResetToken, NotificationOutbox)) for row in db.rows)


def test_request_locks_owner_before_shared_budget_and_funds_before_acceptance(flow):
    issued(flow)
    db, _, _ = flow
    owner_lock = db.trace.index(("locked", "User"))
    account_lock = db.trace.index(("locked", "CandidatePasswordAccount"))
    reserved = next(index for index, value in enumerate(db.trace) if type(value) is tuple and value[0] == "reserved")
    assert owner_lock < account_lock < reserved
    assert reserved < db.trace.index(("added", "PasswordResetToken")) < db.trace.index(("added", "NotificationOutbox"))


def test_request_expiring_during_budget_wait_rolls_back_without_accepting_work(flow, monkeypatch):
    from app.domains.notifications import mail_budget
    db, _, _ = flow
    clock = {"now": NOW}
    monkeypatch.setattr(erasure, "utcnow", lambda: clock["now"])
    def reserve(*args, **kwargs):
        clock["now"] = NOW + timedelta(minutes=10)
    monkeypatch.setattr(mail_budget, "reserve_messages", reserve)
    assert erasure.request_erasure(db, "erasure-owner@example.invalid", read_native=lambda *_: "b"*64) is None
    assert not any(isinstance(row, (PasswordResetToken, NotificationOutbox)) for row in db.rows)


def test_privacy_gate_is_separate_from_existing_configured_reset(monkeypatch):
    from app.domains.notifications import password_reset
    monkeypatch.setenv("RESEND_API_KEY", "synthetic-key-no-provider-call")
    monkeypatch.setenv("EMAIL_FROM", "synthetic@example.invalid")
    monkeypatch.delenv("PRIVACY_ERASURE_RECOVERY_ENABLED", raising=False)
    assert password_reset.configured() is True
    assert mail.configured() is False
    monkeypatch.setenv("PRIVACY_ERASURE_RECOVERY_ENABLED", "true")
    assert mail.configured() is True and password_reset.configured() is True
    monkeypatch.setenv("PRIVACY_ERASURE_RECOVERY_ENABLED", "TRUE")
    assert mail.configured() is False and password_reset.configured() is True


def test_new_statuses_are_disjoint_from_both_actual_historical_worker_selectors():
    import subprocess
    root = Path(__file__).resolve().parents[2]
    old = "e23c940e6133323c3c1ff11e646a4ca9b2b0b165"
    generic = subprocess.run(["git", "show", old+":backend/app/domains/notifications/service.py"],
        cwd=root, check=True, capture_output=True, text=True).stdout
    reset = subprocess.run(["git", "show", old+":backend/app/domains/notifications/password_reset.py"],
        cwd=root, check=True, capture_output=True, text=True).stdout
    assert 'models.NotificationOutbox.status == "pending"' in generic
    assert 'status="pending"' in reset
    assert mail.PENDING != "pending" and mail.PROCESSING != "processing"
    assert mail.UNKNOWN not in {"pending", "processing"}
