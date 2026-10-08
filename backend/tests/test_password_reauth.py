"""Actual passwords/SQL + independently journaled pairing consumption; synthetic only."""
from __future__ import annotations

import json
import logging
import os
import sqlite3
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from uuid import uuid4

import psycopg
import pytest
from backend.tests.fixtures.pairing_authority import Scenario, sign
from sqlalchemy import create_engine, delete, event, update
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker

from app.domains.recovery.contracts import Revocation
from app.domains.recovery.pairing_contracts import CandidateAssertion
from app.domains.recovery.password_reauth import (
    FAILURE,
    UNAVAILABLE,
    CandidateWebSession,
    DirectPairingCandidateDispatch,
    PasswordAccountBinding,
    PasswordReauthRequest,
    PasswordReauthService,
    RegisteredPasswordIdentity,
    RetainedWebSession,
    SqlPasswordCredentials,
)
from app.domains.recovery.store import GuardDenied, GuardUnavailable, UnavailableStore
from app.models import User
from app.security import hash_password

PASSWORD = "  synthetic-password-é  "


class SyntheticSigner:
    def __init__(self, case: Scenario, hook: Callable[[], None] | None = None):
        self.case, self.hook = case, hook
        self.calls: list[dict] = []

    def sign(self, payload: dict) -> dict:
        self.calls.append(dict(payload))
        if self.hook:
            self.hook()
        return {"payload": payload, "signature": sign(self.case.auth_key, "candidate-assertion", payload)}


class Boundary:
    def __init__(self, path):
        self.case = Scenario(path)
        self.engine = create_engine("sqlite:///" + str(path / "candidate.sqlite"))
        User.__table__.create(self.engine)
        self.sessions = sessionmaker(bind=self.engine, autoflush=False)
        self.password_hash = hash_password(PASSWORD)
        with self.sessions() as db:
            db.add(User(id=1, email="synthetic@example.invalid", password_hash=self.password_hash))
            db.commit()
        self.context = CandidateWebSession(candidate_id=1, account_binding_id=uuid4(), session_id=self.case.session)
        self.binding = PasswordAccountBinding(account_binding_id=self.context.account_binding_id,
            candidate_id=1, subject_uuid=self.case.subject, principal_sha256=self.principal,
            registration_event_id=uuid4())
        self.web_session = RetainedWebSession(session_id=self.context.session_id,
            account_binding_id=self.context.account_binding_id, subject_uuid=self.case.subject,
            principal_sha256=self.principal, auth_generation=1, registration_event_id=uuid4(),
            issued_at_ms=self.case.now - 1000, expires_at_ms=self.case.now + 300_000, active=True)
        def seed(tx):
            tx.put("pairing_account_bindings", str(self.context.account_binding_id), self.binding.model_dump(mode="json"), immutable=True)
            tx.append(str(self.binding.registration_event_id), "PASSWORD_ACCOUNT_BOUND",
                      self.binding.model_dump(mode="json", exclude={"registration_event_id"}))
            tx.put("pairing_web_sessions", self.case.session, self.web_session.model_dump(mode="json"), immutable=True)
            tx.append(str(self.web_session.registration_event_id), "PASSWORD_WEB_SESSION_CREATED",
                      self.web_session.model_dump(mode="json", exclude={"registration_event_id"}))
        self.case.store.transact(seed)
        self.credentials = SqlPasswordCredentials(self.engine, dummy_hash=hash_password("synthetic-dummy"))
        self.signer = SyntheticSigner(self.case)
        self.service = self.build()

    @property
    def principal(self):
        return self.case.store.transact(lambda tx: tx.get("subjects", self.case.subject))["principal_sha256"]

    def build(self, **changes):
        identity = changes.pop("identity", RegisteredPasswordIdentity(self.case.store))
        return PasswordReauthService(**{"credentials": self.credentials,
            "identity": identity, "pairing": self.case.service,
            "dispatch": DirectPairingCandidateDispatch(identity, self.case.service),
            "signer": self.signer, "issuer": "fixture_auth", **changes})

    def request(self, operation="confirm_pairing", device_id=None):
        if operation == "confirm_pairing":
            pairing = self.case.requested()
            challenge = self.case.service.candidate_challenge(pairing["pairing_id"], self.case.subject, self.case.session)
        else:
            challenge = self.case.service.revocation_challenge(device_id, self.case.subject, self.case.session)
        return {"protocol_version": 2, "operation": operation, "challenge_id": challenge["payload"]["challenge_id"],
                "nonce": challenge["nonce"], "password": PASSWORD, "confirmed": True}, challenge

    def read(self, namespace, key):
        return self.case.store.transact(lambda tx: tx.get(namespace, key))


@pytest.fixture
def boundary(tmp_path):
    value = Boundary(tmp_path)
    yield value
    value.credentials.close()
    value.engine.dispose()


def denied(boundary, request, *, context=None):
    with pytest.raises(GuardDenied) as exc:
        boundary.service.execute(context or boundary.context, request)
    assert str(exc.value) == FAILURE and exc.value.__cause__ is None


def test_actual_password_builds_exact_server_assertion_and_consumes_challenge(boundary, caplog):
    request, challenge = boundary.request()
    result = boundary.service.execute(boundary.context, request)
    assert result["status"] == "CANDIDATE_CONFIRMED"
    assert set(result) == {"pairing_id", "device_id", "status"}
    assert len(boundary.signer.calls) == 1
    assertion = CandidateAssertion.model_validate(boundary.signer.calls[0])
    assert assertion.method == "password_reauth" and assertion.issuer == "fixture_auth"
    assert assertion.authenticated_at_ms == assertion.issued_at_ms == boundary.case.now
    assert assertion.expires_at_ms == challenge["payload"]["expires_at_ms"]
    assert str(assertion.subject_uuid) == boundary.case.subject and str(assertion.session_id) == boundary.case.session
    assert assertion.auth_generation == 1 and assertion.principal_sha256 == boundary.principal
    assert assertion.binding_sha256 == challenge["binding_sha256"]
    assert boundary.read("pairing_challenges", request["challenge_id"])["consumed_by"] is not None
    assert boundary.read("pairing_assertions", str(assertion.assertion_id)) is not None
    with sqlite3.connect(boundary.case.store.path) as db:
        retained = json.dumps(list(db.execute("SELECT * FROM records"))) + json.dumps(list(db.execute("SELECT * FROM events")))
    for secret in (PASSWORD, boundary.password_hash, request["nonce"], "synthetic-bearer-token"):
        assert secret not in retained and secret not in caplog.text and secret not in json.dumps(result)
    assert "password" not in repr(PasswordReauthRequest.model_validate(request)).replace("password_reauth", "")
    denied(boundary, request)
    assert len(boundary.signer.calls) == 1


def test_revoke_actual_owned_device_without_creating_action_authority(boundary):
    pairing, _ = boundary.case.complete()
    request, _ = boundary.request("revoke_device", pairing["device_id"])
    result = boundary.service.execute(boundary.context, request)
    assert result["status"] == "REVOKED"
    assert boundary.read("devices", pairing["device_id"])["active"] is False
    assert boundary.read("pairing_device_tombstones", pairing["device_id"]) is not None
    assert boundary.signer.calls[0]["operation"] == "revoke_device"
    for namespace in ("actors", "grants", "approvals", "permits", "claims", "sequence_attempts"):
        assert boundary.read(namespace, pairing["device_id"]) is None


@pytest.mark.parametrize("problem", ["wrong", "missing", "google_only", "changed"])
def test_fresh_scalar_password_check_never_accepts_existing_authenticated_user(boundary, problem):
    request, _ = boundary.request()
    with boundary.sessions() as stale:
        stale_user = stale.get(User, 1)
        assert stale_user.password_hash == boundary.password_hash
        if problem == "wrong":
            request["password"] = "wrong"
        else:
            with boundary.sessions() as writer:
                if problem == "missing":
                    writer.execute(delete(User).where(User.id == 1))
                else:
                    writer.execute(update(User).where(User.id == 1).values(password_hash="" if problem == "google_only" else hash_password("new-password")))
                writer.commit()
        denied(boundary, request)
        assert len(boundary.signer.calls) == 0
        assert boundary.read("pairing_challenges", request["challenge_id"])["consumed_by"] is None
        if problem == "changed":
            request["password"] = "new-password"
            assert boundary.service.execute(boundary.context, request)["status"] == "CANDIDATE_CONFIRMED"


@pytest.mark.parametrize("change", [
    {"password": ""}, {"password": "é" * 513}, {"password": "\ud800"}, {"password": 7},
    {"nonce": "bad"}, {"nonce": 7}, {"challenge_id": "not-a-uuid"},
    {"confirmed": False}, {"confirmed": 1}, {"method": "provider_reauth"},
    {"bearer": "synthetic-bearer-token"}, {"subject_uuid": str(uuid4())},
    {"auth_generation": 1}, {"issued_at_ms": 123}, {"issuer": "evil"},
    {"protocol_version": True}, {"protocol_version": "2"},
])
def test_bounded_client_contract_uniformly_rejects_privilege_or_secret_coercion(boundary, change):
    request, _ = boundary.request()
    denied(boundary, {**request, **change})
    assert not boundary.signer.calls


@pytest.mark.parametrize("change", ["candidate", "binding", "session", "deleted", "tombstone", "expired", "registration"])
def test_independent_lifetime_registration_session_and_owner_are_required(boundary, change):
    request, _ = boundary.request()
    context = boundary.context
    if change in {"candidate", "binding", "session"}:
        field = {"candidate": "candidate_id", "binding": "account_binding_id", "session": "session_id"}[change]
        context = CandidateWebSession.model_validate({**context.model_dump(), field: 2 if change == "candidate" else uuid4()})
        if change == "candidate":
            with boundary.sessions() as db:
                db.add(User(id=2, email="other@example.invalid", password_hash=boundary.password_hash))
                db.commit()
    elif change == "deleted":
        scope = Revocation(subject_uuid=boundary.binding.subject_uuid, kind="subject", target=boundary.case.subject, revision=0)
        boundary.case.store.seed([("revocations", scope.key, {"scope": scope.model_dump(mode="json")})])
    elif change == "tombstone":
        # Even restored SQL id/password cannot resurrect a retained closed binding.
        boundary.case.store.seed([("pairing_account_tombstones", str(context.account_binding_id), {"closed": True})])
    elif change == "registration":
        binding = boundary.binding.model_dump(mode="json")
        boundary.case.store.seed([("pairing_account_bindings", str(context.account_binding_id), {**binding, "registration_event_id": str(uuid4())})])
    else:
        boundary.case.now = boundary.web_session.expires_at_ms
    denied(boundary, request, context=context)
    assert not boundary.signer.calls


@pytest.mark.parametrize("change", ["nonce", "challenge", "operation", "expiry", "epoch"])
def test_exact_challenge_proof_operation_and_epoch_are_required(boundary, change):
    request, _ = boundary.request()
    if change == "nonce":
        request["nonce"] = str(uuid4())
    elif change == "challenge":
        request["challenge_id"] = str(uuid4())
    elif change == "operation":
        request["operation"] = "revoke_device"
    elif change == "expiry":
        boundary.case.now += 60_000
    else:
        control = boundary.read("control", "current")
        boundary.case.store.seed([("control", "current", {**control, "generation": 2, "epoch_id": str(uuid4())})])
    denied(boundary, request)
    assert not boundary.signer.calls


@pytest.mark.parametrize("change", ["generation", "tombstone", "session", "binding"])
def test_revoke_between_sign_and_commit_is_rechecked_in_consumption_transaction(boundary, change):
    request, _ = boundary.request()
    def mutation():
        if change == "generation":
            subject = boundary.read("subjects", boundary.case.subject)
            records = [("subjects", boundary.case.subject, {**subject, "auth_generation": 2})]
        elif change == "tombstone":
            records = [("pairing_account_tombstones", str(boundary.context.account_binding_id), {"closed": True})]
        elif change == "session":
            row = boundary.read("pairing_sessions", boundary.case.session)
            records = [("pairing_sessions", boundary.case.session, {**row, "active": False})]
        else:
            row = boundary.binding.model_dump(mode="json")
            records = [("pairing_account_bindings", str(boundary.context.account_binding_id), {**row, "candidate_id": 2})]
        boundary.case.store.seed(records)
    boundary.signer.hook = mutation
    denied(boundary, request)
    assert len(boundary.signer.calls) == 1
    assert boundary.read("pairing_challenges", request["challenge_id"])["consumed_by"] is None


def test_signer_error_is_sanitized_and_latched_without_consumption_or_second_signature(boundary):
    request, _ = boundary.request()
    def outage():
        raise RuntimeError("private-signer-token " + PASSWORD)
    boundary.signer.hook = outage
    with pytest.raises(GuardUnavailable, match="^" + UNAVAILABLE + "$") as exc:
        boundary.service.execute(boundary.context, request)
    assert exc.value.__cause__ is None
    assert boundary.read("pairing_challenges", request["challenge_id"])["consumed_by"] is None
    boundary.signer.hook = None
    denied(boundary, request)
    assert len(boundary.signer.calls) == 1


def test_confirmed_commit_with_lost_response_does_not_reissue_or_consume_again(boundary):
    request, _ = boundary.request()
    original = boundary.case.store.transact
    def lose_reply(operation):
        result = original(operation)
        if isinstance(result, dict) and result.get("status") == "CANDIDATE_CONFIRMED":
            raise GuardUnavailable("synthetic lost commit response")
        return result
    boundary.case.store.transact = lose_reply
    with pytest.raises(GuardUnavailable, match="^" + UNAVAILABLE + "$"):
        boundary.service.execute(boundary.context, request)
    boundary.case.store.transact = original
    assert boundary.case.service.status(boundary.signer.calls[0] and boundary.read("pairing_challenges", request["challenge_id"])["payload"]["pairing_id"])["status"] == "CANDIDATE_CONFIRMED"
    denied(boundary, request)
    assert len(boundary.signer.calls) == 1


def test_malformed_or_issuer_changed_signer_cannot_authorize(boundary):
    request, _ = boundary.request()
    class EvilSigner:
        def sign(self, payload):
            changed = {**payload, "issuer": "untrusted"}
            return {"payload": changed, "signature": sign(boundary.case.auth_key, "candidate-assertion", changed)}
    boundary.service = boundary.build(signer=EvilSigner())
    denied(boundary, request)
    assert boundary.read("pairing_challenges", request["challenge_id"])["consumed_by"] is None


def test_no_defaults_or_sql_repository_fallback_and_bounded_pending_latch(boundary):
    with pytest.raises(TypeError):
        PasswordReauthService()  # type: ignore[call-arg]  # Deliberately prove no fallback.
    with pytest.raises(GuardUnavailable):
        boundary.build(identity=RegisteredPasswordIdentity(UnavailableStore()))
    boundary.service = boundary.build(max_pending=1)
    request, _ = boundary.request()
    def outage():
        raise GuardUnavailable("synthetic")
    boundary.signer.hook = outage
    with pytest.raises(GuardUnavailable):
        boundary.service.execute(boundary.context, request)
    other, _ = boundary.request()
    boundary.signer.hook = None
    with pytest.raises(GuardUnavailable):
        boundary.service.execute(boundary.context, other)
    assert len(boundary.signer.calls) == 1


def test_concurrent_same_challenge_cannot_issue_two_assertions(boundary):
    request, _ = boundary.request()
    entered, release = Event(), Event()
    def pause():
        entered.set()
        assert release.wait(5)
    boundary.signer.hook = pause
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(boundary.service.execute, boundary.context, request)
        assert entered.wait(5)
        second = pool.submit(boundary.service.execute, boundary.context, request)
        with pytest.raises(GuardDenied, match="^" + FAILURE + "$"):
            second.result(timeout=5)
        release.set()
        assert first.result(timeout=5)["status"] == "CANDIDATE_CONFIRMED"
    assert len(boundary.signer.calls) == 1


def test_another_subjects_exact_valid_challenge_does_not_bind_current_candidate(boundary):
    subject, session = boundary.case.store.subject()
    pairing = boundary.case.requested()
    challenge = boundary.case.service.candidate_challenge(pairing["pairing_id"], subject, session)
    request = {"protocol_version": 2, "operation": "confirm_pairing", "confirmed": True,
               "challenge_id": challenge["payload"]["challenge_id"], "nonce": challenge["nonce"], "password": PASSWORD}
    denied(boundary, request)
    assert not boundary.signer.calls


def test_revoking_another_owned_device_cannot_tombstone_it(boundary):
    subject, session = boundary.case.store.subject()
    pairing = boundary.case.confirmed(subject=subject, session=session)
    boundary.case.service.complete_device(**boundary.case.proof(pairing))
    request, _ = boundary.request("revoke_device", pairing["device_id"])
    denied(boundary, request)
    assert boundary.read("devices", pairing["device_id"])["active"] is True
    assert boundary.read("pairing_device_tombstones", pairing["device_id"]) is None


def test_assertion_expiry_is_capped_by_short_remaining_pairing_window(boundary):
    pairing = boundary.case.requested()
    boundary.case.now += 119_000
    challenge = boundary.case.service.candidate_challenge(pairing["pairing_id"], boundary.case.subject, boundary.case.session)
    request = {"protocol_version": 2, "operation": "confirm_pairing", "confirmed": True,
               "challenge_id": challenge["payload"]["challenge_id"], "nonce": challenge["nonce"], "password": PASSWORD}
    result = boundary.service.execute(boundary.context, request)
    assert result["status"] == "CANDIDATE_CONFIRMED"
    assertion = boundary.signer.calls[0]
    assert assertion["expires_at_ms"] == assertion["issued_at_ms"] + 1000


def test_unknown_authority_read_is_not_retried_or_used_to_sign(boundary):
    request, _ = boundary.request()
    original = boundary.case.store.transact
    calls = []
    def unavailable(operation):
        calls.append(operation)
        raise GuardUnavailable("synthetic authority bearer/password " + PASSWORD)
    boundary.case.store.transact = unavailable
    with pytest.raises(GuardUnavailable, match="^" + UNAVAILABLE + "$"):
        boundary.service.execute(boundary.context, request)
    boundary.case.store.transact = original
    denied(boundary, request)
    assert len(calls) == 1 and not boundary.signer.calls


def test_malformed_credential_adapter_result_cannot_attest_password_success(boundary):
    request, _ = boundary.request()
    class BadCredentials:
        def verify(self, candidate_id, password):
            return "true"
    boundary.service = boundary.build(credentials=BadCredentials())
    denied(boundary, request)
    assert not boundary.signer.calls


@pytest.mark.parametrize("change", [{"active": 1}, {"auth_generation": True}, {"extra": "untrusted"}])
def test_pairing_session_uses_exact_canonical_types_before_any_signature(boundary, change):
    request, _ = boundary.request()
    session = boundary.read("pairing_sessions", boundary.case.session)
    boundary.case.store.seed([("pairing_sessions", boundary.case.session, {**session, **change})])
    denied(boundary, request)
    assert not boundary.signer.calls


def test_trusted_dispatch_port_gets_frozen_server_assertion_without_password(boundary):
    request, _ = boundary.request()
    direct = boundary.service.dispatch
    captured = []
    class RecordingDispatch:
        def consume(self, command):
            captured.append(command)
            return direct.consume(command)
    boundary.service = boundary.build(dispatch=RecordingDispatch())
    result = boundary.service.execute(boundary.context, request)
    assert result["status"] == "CANDIDATE_CONFIRMED" and len(captured) == 1
    command = captured[0]
    assert command.context == boundary.context and command.assertion.issuer == "fixture_auth"
    assert str(command.assertion.subject_uuid) == boundary.case.subject
    for secret in (PASSWORD, boundary.password_hash, request["nonce"]):
        assert secret not in command.model_dump_json() and secret not in repr(command)
    with pytest.raises(ValueError):
        command.context.candidate_id = 2
    with pytest.raises(ValueError):
        command.assertion.auth_generation = 2


@pytest.mark.parametrize("fault", ["envelope", "target", "unknown"])
def test_dispatch_result_is_exact_redacted_and_unknown_never_retries(boundary, fault):
    request, _ = boundary.request()
    direct = boundary.service.dispatch
    calls = []
    class FaultyDispatch:
        def consume(self, command):
            calls.append(command)
            result = direct.consume(command)
            if fault == "envelope":
                return {**result, "assertion": command.assertion.model_dump(mode="json")}
            if fault == "target":
                return {**result, "device_id": str(uuid4())}
            raise GuardUnavailable("synthetic lost transport reply " + PASSWORD)
    boundary.service = boundary.build(dispatch=FaultyDispatch())
    if fault == "unknown":
        with pytest.raises(GuardUnavailable, match="^" + UNAVAILABLE + "$"):
            boundary.service.execute(boundary.context, request)
    else:
        denied(boundary, request)
    assert boundary.read("pairing_challenges", request["challenge_id"])["consumed_by"] is not None
    denied(boundary, request)
    assert len(calls) == len(boundary.signer.calls) == 1


@pytest.mark.parametrize("mode", ["logger", "echo", "mid_query"])
def test_native_password_read_never_enters_sqlalchemy_hash_row_logging(boundary, caplog, mode):
    logger = logging.getLogger("sqlalchemy.engine.Engine")
    old_level = logger.level
    caplog.set_level(logging.DEBUG)
    if mode == "logger":
        logger.setLevel(logging.DEBUG)
    elif mode == "echo":
        boundary.credentials._engine.echo = "debug"
    else:
        logger.setLevel(logging.WARNING)
        @event.listens_for(boundary.credentials._engine, "connect")
        def flip_during_select(native, record):
            native.set_trace_callback(lambda statement: logger.setLevel(logging.DEBUG)
                                      if statement.startswith("SELECT CASE") else None)
    try:
        request, _ = boundary.request()
        caplog.clear()
        assert boundary.service.execute(boundary.context, request)["status"] == "CANDIDATE_CONFIRMED"
        assert boundary.password_hash not in caplog.text and PASSWORD not in caplog.text
        assert "Row (" not in caplog.text
        assert boundary.credentials._engine.pool.checkedout() == 0
        if mode == "mid_query":
            assert logger.getEffectiveLevel() == logging.DEBUG
    finally:
        logger.setLevel(old_level)


def test_native_cursor_is_closed_and_session_released_on_query_fault(boundary, monkeypatch):
    request, _ = boundary.request()
    real_connection = sqlite3.connect
    closed = []
    class CheckedCursor(sqlite3.Cursor):
        def execute(self, statement, parameters=()):
            if statement.startswith("SELECT CASE"):
                raise sqlite3.OperationalError("synthetic query failure")
            return super().execute(statement, parameters)
        def close(self):
            closed.append(True)
            return super().close()
    class CheckedConnection(sqlite3.Connection):
        def cursor(self, *args, **kwargs):
            return super().cursor(factory=CheckedCursor)
    def connection(*args, **kwargs):
        return real_connection(*args, factory=CheckedConnection, **kwargs)
    monkeypatch.setattr(boundary.credentials._engine.dialect.loaded_dbapi, "connect", connection)
    with pytest.raises(GuardUnavailable, match="^" + UNAVAILABLE + "$"):
        boundary.service.execute(boundary.context, request)
    assert closed and boundary.credentials._engine.pool.checkedout() == 0
    assert not boundary.signer.calls


def test_native_sqlite_connection_is_read_only_and_has_bounded_waits(boundary):
    assert boundary.credentials.verify(1, PASSWORD) is True
    assert boundary.credentials._engine.pool.timeout() == 2.0
    with boundary.credentials._engine.connect() as connection:
        native = connection.connection.driver_connection
        assert native.execute("PRAGMA query_only").fetchone() == (1,)
        assert native.execute("PRAGMA busy_timeout").fetchone() == (2000,)
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            native.execute("UPDATE users SET password_hash='forbidden' WHERE id=1")


def test_oversized_sql_hash_is_not_truncated_into_a_valid_credential(boundary):
    with boundary.sessions() as writer:
        writer.execute(update(User).where(User.id == 1).values(password_hash=boundary.password_hash + "x" * 2000))
        writer.commit()
    assert boundary.credentials.verify(1, PASSWORD) is False


@pytest.mark.parametrize("timeout", [0, -1, True, float("inf"), float("nan"), 6])
def test_credential_pool_and_native_wait_budgets_must_be_positive_finite(boundary, timeout):
    with pytest.raises(GuardUnavailable, match="^" + UNAVAILABLE + "$"):
        SqlPasswordCredentials(boundary.engine, dummy_hash=boundary.password_hash, timeout_seconds=timeout)


@pytest.fixture
def password_postgres():
    configured = os.getenv("PAIRING_PASSWORD_TEST_POSTGRES_ADMIN_URL")
    if not configured:
        if os.getenv("PAIRING_PASSWORD_TEST_POSTGRES_REQUIRED") == "true":
            pytest.fail("Required disposable password PostgreSQL admin URL is missing")
        pytest.skip("Set local disposable password PostgreSQL admin URL for native-driver evidence")
    url = make_url(configured)
    if (url.drivername != "postgresql+psycopg" or url.host != "127.0.0.1" or url.port != 55433
            or url.database != "postgres" or url.username != "hirewiz"):
        pytest.fail("Password-driver evidence requires the owned localhost disposable PostgreSQL cluster")
    name = "reauth_" + uuid4().hex
    admin = create_engine(url, isolation_level="AUTOCOMMIT", connect_args={"connect_timeout": 2})
    with admin.connect() as connection:
        connection.exec_driver_sql(f'CREATE DATABASE "{name}"')
    engine = create_engine(url.set(database=name))
    try:
        User.__table__.create(engine)
        encoded = hash_password(PASSWORD)
        with sessionmaker(bind=engine)() as db:
            db.add(User(id=1, email="synthetic@example.invalid", password_hash=encoded))
            db.commit()
        yield engine, encoded
    finally:
        engine.dispose()
        with admin.connect() as connection:
            connection.exec_driver_sql(f'DROP DATABASE "{name}"')
        admin.dispose()


@pytest.mark.parametrize("mode", ["debug", "mid_query"])
def test_actual_psycopg_reader_has_no_hash_row_logs_and_read_only_transaction(password_postgres, caplog, monkeypatch, mode):
    engine, encoded = password_postgres
    credentials = SqlPasswordCredentials(engine, dummy_hash=hash_password("synthetic-dummy"))
    logger = logging.getLogger("sqlalchemy.engine.Engine")
    old_level = logger.level
    observed = []
    real_cursor = psycopg.Connection.cursor
    class SpyCursor(psycopg.Cursor):
        def execute(self, query, params=None, **kwargs):
            if isinstance(query, str) and query.startswith("SELECT CASE"):
                if mode == "mid_query":
                    logger.setLevel(logging.DEBUG)
                super().execute("SHOW transaction_read_only")
                observed.append(self.fetchone()[0])
            return super().execute(query, params, **kwargs)
    def cursor(connection, *args, **kwargs):
        return SpyCursor(connection) if not args and not kwargs else real_cursor(connection, *args, **kwargs)
    monkeypatch.setattr(psycopg.Connection, "cursor", cursor)
    caplog.set_level(logging.DEBUG)
    logger.setLevel(logging.DEBUG if mode == "debug" else logging.WARNING)
    try:
        caplog.clear()
        assert credentials.verify(1, PASSWORD) is True
        assert credentials.verify(1, "wrong-password") is False
        assert credentials.verify(2, PASSWORD) is False
        assert encoded not in caplog.text and PASSWORD not in caplog.text
        assert logger.getEffectiveLevel() == logging.DEBUG
        if mode == "debug":
            # Dialect initialization may log its harmless server/schema rows;
            # the native credential SELECT must never enter that row logger.
            assert "select pg_catalog.version()" in caplog.text
        assert observed == ["on", "on", "on"] and credentials._engine.pool.checkedout() == 0
        with credentials._engine.connect() as connection:
            with connection.connection.driver_connection.cursor() as native:
                for option in ("statement_timeout", "lock_timeout", "idle_in_transaction_session_timeout"):
                    native.execute("SHOW " + option)
                    assert native.fetchone() == ("2s",)
    finally:
        credentials.close()
        logger.setLevel(old_level)


def test_actual_postgresql_lock_timeout_releases_owned_cursor_and_connection(password_postgres):
    engine, encoded = password_postgres
    credentials = SqlPasswordCredentials(engine, dummy_hash=encoded, timeout_seconds=0.05)
    with engine.connect() as blocker:
        blocker.exec_driver_sql("LOCK TABLE users IN ACCESS EXCLUSIVE MODE")
        try:
            with pytest.raises((psycopg.errors.QueryCanceled, psycopg.errors.LockNotAvailable)):
                credentials.verify(1, PASSWORD)
            assert credentials._engine.pool.checkedout() == 0
        finally:
            blocker.rollback()
            credentials.close()
