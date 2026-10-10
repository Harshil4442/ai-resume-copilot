"""Disabled recent-password boundary connected to independent pairing transactions.

No bearer, SQL lifetime bootstrap, assertion-signing key or HTTP route fallback is
provided. Credential IO and assertion signing run outside authority transactions.
The independent identity binding is rechecked inside the actual consumption commit.
"""
from __future__ import annotations

import json
import math
import sqlite3
from collections.abc import Callable
from copy import copy
from pathlib import Path
from threading import Lock
from time import monotonic
from typing import Literal, Protocol, TypeVar
from uuid import UUID, uuid4

import psycopg
from pydantic import ConfigDict, Field, SecretStr, TypeAdapter, ValidationError, field_validator
from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import QueuePool

from ...security import verify_password
from .contracts import Digest, Identifier, Timestamp
from .pairing_contracts import (
    CANDIDATE_MS,
    CandidateAssertion,
    PairingContract,
    PositiveInteger,
    canonical_bytes,
    digest,
    nonce_digest,
)
from .pairing_service import PairingService
from .store import AuthorityStore, GuardDenied, GuardUnavailable, Transaction

T = TypeVar("T")
FAILURE = "Recent password authentication failed"
UNAVAILABLE = "Recent password authentication is unavailable"
PASSWORD_MAX_BYTES = 1024


class PasswordReauthRequest(PairingContract):
    """Only client confirmation inputs; identity, clocks and issuer are server-owned."""

    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True,
                              revalidate_instances="always")
    protocol_version: Literal[2]
    operation: Literal["confirm_pairing", "revoke_device"]
    method: Literal["password_reauth"] = "password_reauth"
    challenge_id: UUID
    nonce: SecretStr = Field(repr=False)
    password: SecretStr = Field(repr=False)
    confirmed: Literal[True]

    @field_validator("confirmed", mode="before")
    @classmethod
    def explicit_confirmation(cls, value: object) -> object:
        if type(value) is not bool or value is not True:
            raise ValueError("Explicit true confirmation is required")
        return value

    @field_validator("password", "nonce", mode="before")
    @classmethod
    def secret_bounds(cls, value: object, info) -> SecretStr:
        if isinstance(value, SecretStr):
            value = value.get_secret_value()
        if type(value) is not str:
            raise ValueError("A bounded secret string is required")
        try:
            encoded = value.encode("utf-8")
        except UnicodeEncodeError:
            raise ValueError("A bounded secret string is required") from None
        if info.field_name == "nonce":
            if len(encoded) != 36:
                raise ValueError("A canonical challenge nonce is required")
            try:
                if str(UUID(value)) != value:
                    raise ValueError
            except ValueError:
                raise ValueError("A canonical challenge nonce is required") from None
        elif not 0 < len(encoded) <= PASSWORD_MAX_BYTES:
            raise ValueError("A bounded secret string is required")
        return SecretStr(value)


class CandidateWebSession(PairingContract):
    """Injected trusted server context; never constructed from body/bearer claims."""

    candidate_id: PositiveInteger
    account_binding_id: UUID
    session_id: UUID


class PasswordAccountBinding(PairingContract):
    """Retained operator registration, not a SQL-derived identity projection."""

    account_binding_id: UUID
    candidate_id: PositiveInteger
    subject_uuid: UUID
    principal_sha256: Digest
    registration_event_id: UUID


class RetainedWebSession(PairingContract):
    session_id: UUID
    account_binding_id: UUID
    subject_uuid: UUID
    principal_sha256: Digest
    auth_generation: PositiveInteger
    registration_event_id: UUID
    issued_at_ms: Timestamp
    expires_at_ms: Timestamp
    active: Literal[True]

    @field_validator("active", mode="before")
    @classmethod
    def exact_active(cls, value: object) -> object:
        if type(value) is not bool or value is not True:
            raise ValueError("An active retained session is required")
        return value


class PasswordCredentials(Protocol):
    def verify(self, candidate_id: int, password: str) -> bool:
        """Re-read current credentials; never trust a bearer/cached ORM identity."""
        ...


class CandidateAssertionSigner(Protocol):
    def sign(self, payload: dict) -> dict:
        """Sign exact CandidateAssertion only; fixed trusted issuer/key policy."""
        ...


class CandidateDispatchCommand(PairingContract):
    """Private server-to-authority command; never a browser request/response."""

    context: CandidateWebSession
    operation: Literal["confirm_pairing", "revoke_device"]
    challenge_id: UUID
    nonce: SecretStr = Field(repr=False)
    assertion: CandidateAssertion
    signature: str = Field(pattern=r"^[A-Za-z0-9_-]{86}$", repr=False)


class CandidateAssertionDispatch(Protocol):
    def consume(self, command: CandidateDispatchCommand) -> dict:
        """Consume only after independently acknowledged admission/current fences.

        The trusted adapter must recheck independent identity in the actual core
        transaction. Uncertain admission/commit is status-only, never retried.
        """
        ...


class _ConfirmedResult(PairingContract):
    pairing_id: UUID
    device_id: UUID
    status: Literal["CANDIDATE_CONFIRMED"]


class _RevokedResult(PairingContract):
    device_id: UUID
    status: Literal["REVOKED"]
    event_sequence: PositiveInteger


class SqlPasswordCredentials:
    """Owned bounded connection pool; hashes never enter ORM result-row logging.

    Only the reviewed synchronous pysqlite/psycopg DBAPI paths are supported.
    The source engine supplies trusted connection identity, never a browser DSN.
    """

    def __init__(self, source: Engine, *, dummy_hash: str, timeout_seconds: float = 2.0):
        # The operator supplies a valid same-policy dummy hash for missing users.
        # A hash/key is never generated or journaled by this disabled adapter.
        if (type(dummy_hash) is not str or not dummy_hash or not isinstance(source, Engine)
                or type(timeout_seconds) not in {int, float} or not math.isfinite(timeout_seconds)
                or not 0 < timeout_seconds <= 5):
            raise GuardUnavailable(UNAVAILABLE)
        self._timeout, self._dummy_hash = float(timeout_seconds), dummy_hash
        self._milliseconds = max(1, math.ceil(self._timeout * 1000))
        url = source.url
        connect_args: dict[str, object]
        if url.drivername in {"sqlite", "sqlite+pysqlite"}:
            if (url.database is None or not Path(url.database).is_absolute()
                    or url.query or url.host is not None):
                raise GuardUnavailable(UNAVAILABLE)
            self._driver = "sqlite"
            connect_args = {"timeout": self._timeout, "check_same_thread": False}
        elif url.drivername == "postgresql+psycopg":
            self._driver = "postgresql"
            connect_args = {"connect_timeout": max(1, math.ceil(self._timeout)),
                "options": f"-c statement_timeout={self._milliseconds} -c lock_timeout={self._milliseconds} -c idle_in_transaction_session_timeout={self._milliseconds}"}
        else:
            raise GuardUnavailable(UNAVAILABLE)
        self._engine = create_engine(url, connect_args=connect_args, poolclass=QueuePool,
            pool_size=1, max_overflow=0, pool_timeout=self._timeout, pool_pre_ping=False,
            hide_parameters=True)
        self._sessions = sessionmaker(bind=self._engine, autoflush=False, expire_on_commit=False)

    def close(self) -> None:
        self._engine.dispose()

    def verify(self, candidate_id: int, password: str) -> bool:
        with self._sessions() as db:
            native = db.connection().connection.driver_connection
            deadline = monotonic() + self._timeout
            if self._driver == "sqlite" and isinstance(native, sqlite3.Connection):
                native.set_progress_handler(lambda: int(monotonic() >= deadline), 100)
                cursor = native.cursor()
                try:
                    cursor.execute(f"PRAGMA busy_timeout={self._milliseconds}")
                    cursor.execute("PRAGMA query_only=ON")
                    cursor.execute("SELECT CASE WHEN length(CAST(password_hash AS BLOB)) BETWEEN 1 AND 1024 THEN password_hash ELSE NULL END FROM users WHERE id = ? LIMIT 1", (candidate_id,))
                    row = cursor.fetchone()
                finally:
                    cursor.close()
                    native.set_progress_handler(None, 0)
            elif self._driver == "postgresql" and isinstance(native, psycopg.Connection):
                with native.cursor() as cursor:
                    cursor.execute("SET TRANSACTION READ ONLY")
                    cursor.execute("SELECT CASE WHEN octet_length(password_hash) BETWEEN 1 AND 1024 THEN password_hash ELSE NULL END FROM users WHERE id = %s LIMIT 1", (candidate_id,))
                    row = cursor.fetchone()
            else:
                raise GuardUnavailable(UNAVAILABLE)
            if monotonic() >= deadline:
                raise GuardUnavailable(UNAVAILABLE)
            encoded = row[0] if row is not None else None
        usable = type(encoded) is str and bool(encoded)
        checked_hash = encoded if isinstance(encoded, str) and usable else self._dummy_hash
        result = verify_password(password, checked_hash)
        return usable and result


class IndependentPasswordIdentity(Protocol):
    store: AuthorityStore

    def resolve(self, tx: Transaction, context: CandidateWebSession, core: PairingService,
                now_ms: int) -> dict:
        """Resolve canonical active lifetime/session within this authority transaction."""
        ...


class RegisteredPasswordIdentity:
    """Connected retained-registration reader; production provisioning remains absent.

    New identity namespaces require independently protected registration/session
    writers and native adapter permission review. This reader never creates them.
    """

    def __init__(self, store: AuthorityStore):
        self.store = store

    def resolve(self, tx: Transaction, context: CandidateWebSession, core: PairingService,
                now_ms: int) -> dict:
        core._control(tx)
        binding_id = str(context.account_binding_id)
        raw = tx.get("pairing_account_bindings", binding_id)
        if raw is None or tx.get("pairing_account_tombstones", binding_id) is not None:
            raise GuardDenied(FAILURE)
        binding = PasswordAccountBinding.model_validate(raw)
        if binding.account_binding_id != context.account_binding_id or binding.candidate_id != context.candidate_id:
            raise GuardDenied(FAILURE)
        registration = tx.event(str(binding.registration_event_id))
        expected = binding.model_dump(mode="json", exclude={"registration_event_id"})
        if registration is None or registration["kind"] != "PASSWORD_ACCOUNT_BOUND" or registration["payload"] != expected:
            raise GuardDenied(FAILURE)
        subject_id = str(binding.subject_uuid)
        subject = core._subject(tx, subject_id)
        if subject["principal_sha256"] != binding.principal_sha256:
            raise GuardDenied(FAILURE)
        raw_session = tx.get("pairing_web_sessions", str(context.session_id))
        if raw_session is None:
            raise GuardDenied(FAILURE)
        session = RetainedWebSession.model_validate(raw_session)
        if (session.session_id != context.session_id or session.account_binding_id != context.account_binding_id
                or session.subject_uuid != binding.subject_uuid or session.principal_sha256 != binding.principal_sha256
                or session.auth_generation != subject["auth_generation"]
                or not session.issued_at_ms <= now_ms < session.expires_at_ms):
            raise GuardDenied(FAILURE)
        registered_session = tx.event(str(session.registration_event_id))
        if (registered_session is None or registered_session["kind"] != "PASSWORD_WEB_SESSION_CREATED"
                or registered_session["payload"] != session.model_dump(mode="json", exclude={"registration_event_id"})):
            raise GuardDenied(FAILURE)
        identity = {"subject_uuid": subject_id, "session_id": str(context.session_id),
                    "principal_sha256": binding.principal_sha256, "auth_generation": subject["auth_generation"]}
        exact_session = {**{name: identity[name] for name in (
            "subject_uuid", "principal_sha256", "auth_generation")}, "active": True}
        current_session = tx.get("pairing_sessions", str(context.session_id))
        if (type(current_session) is not dict or canonical_bytes("registry", current_session)
                != canonical_bytes("registry", exact_session)):
            raise GuardDenied(FAILURE)
        return identity


class _IdentityFencedStore:
    def __init__(self, repository: IndependentPasswordIdentity, context: CandidateWebSession,
                 expected: dict, core: PairingService):
        self.repository, self.context, self.expected, self.core = repository, context, expected, core

    def transact(self, operation: Callable[[Transaction], T]) -> T:
        def fenced(tx: Transaction) -> T:
            now = self.core._now(tx, record=False)
            if self.repository.resolve(tx, self.context, self.core, now) != self.expected:
                raise GuardDenied(FAILURE)
            return operation(tx)
        return self.repository.store.transact(fenced)


class DirectPairingCandidateDispatch:
    """Explicit local-core adapter; it cannot bypass a native coordinator scope.

    A native GCP adapter must implement the dispatch port inside its restricted
    coordinator.execute lifecycle and authorize the identity read namespaces.
    """

    def __init__(self, identity: IndependentPasswordIdentity, pairing: PairingService):
        if identity.store is not pairing.store:
            raise GuardUnavailable(UNAVAILABLE)
        self.identity, self.pairing = identity, pairing

    def consume(self, command: CandidateDispatchCommand) -> dict:
        assertion = command.assertion
        if assertion.operation != command.operation or assertion.session_id != command.context.session_id:
            raise GuardDenied(FAILURE)
        expected = {"subject_uuid": str(assertion.subject_uuid), "session_id": str(assertion.session_id),
                    "principal_sha256": assertion.principal_sha256, "auth_generation": assertion.auth_generation}
        # Preserve the trusted core's verifier/clock/deterministic allocator.
        guarded = copy(self.pairing)
        guarded.store = _IdentityFencedStore(self.identity, command.context, expected, self.pairing)
        method = guarded.confirm_candidate if command.operation == "confirm_pairing" else guarded.revoke_device
        return method(str(command.challenge_id), command.nonce.get_secret_value(),
                      {"payload": assertion.model_dump(mode="json"), "signature": command.signature})


class CandidateChallengeProof(PairingContract):
    """Password-free server projection of the validated confirmation request."""

    operation: Literal["confirm_pairing", "revoke_device"]
    challenge_id: UUID
    nonce: SecretStr = Field(repr=False)

    @field_validator("nonce", mode="before")
    @classmethod
    def exact_nonce(cls, value: object) -> SecretStr:
        plain = value.get_secret_value() if isinstance(value, SecretStr) else value
        if type(plain) is not str or len(plain) != 36:
            raise ValueError("A canonical challenge nonce is required")
        try:
            if str(UUID(plain)) != plain:
                raise ValueError
        except ValueError:
            raise ValueError("A canonical challenge nonce is required") from None
        return SecretStr(plain)


class CandidateChallengeReader(Protocol):
    def read(self, context: CandidateWebSession, proof: CandidateChallengeProof) -> tuple[dict, dict, int]: ...


class CandidateSigningAdmission(Protocol):
    def admit(self, context: CandidateWebSession, proof: CandidateChallengeProof,
              assertion: CandidateAssertion) -> None: ...


def read_candidate_challenge(repository: IndependentPasswordIdentity, core: PairingService,
                             tx: Transaction, context: CandidateWebSession,
                             proof: CandidateChallengeProof) -> tuple[dict, dict, int]:
    """Shared exact reader; caller supplies only its operation-restricted transaction."""
    now = core._now(tx, record=False)
    identity = repository.resolve(tx, context, core, now)
    row = tx.get("pairing_challenges", str(proof.challenge_id))
    if row is None or row.get("kind") != "candidate" or row.get("consumed_by") is not None:
        raise GuardDenied(FAILURE)
    payload = row["payload"]
    if (payload["challenge_id"] != str(proof.challenge_id) or payload["operation"] != proof.operation
            or payload["audience"] != "hirewiz:pairing-only" or payload["protocol_version"] != 2
            or row["expires_at_ms"] != payload["expires_at_ms"]
            or not payload["issued_at_ms"] <= now < payload["expires_at_ms"]
            or payload["nonce_sha256"] != nonce_digest(proof.nonce.get_secret_value())
            or any(payload[name] != value for name, value in identity.items())):
        raise GuardDenied(FAILURE)
    core._epoch(core._control(tx), payload)
    canonical_bytes("candidate-context", payload)
    return identity, json.loads(json.dumps(payload)), now


class PasswordReauthService:
    """Explicit injected adapters only. A confirmed result never contains an assertion.

    The default local one-shot latch is not durable restart authorization.
    The explicit native ports add protected challenge-scoped signing admission.
    Unknown/signing failures require status/fresh challenge; no automatic replay.
    """

    def __init__(self, *, credentials: PasswordCredentials, identity: IndependentPasswordIdentity,
                 pairing: PairingService, signer: CandidateAssertionSigner, issuer: str,
                 dispatch: CandidateAssertionDispatch,
                 reader: CandidateChallengeReader | None = None,
                 signing_admission: CandidateSigningAdmission | None = None,
                 max_pending: int = 1024):
        if pairing.store is not identity.store or type(max_pending) is not int or not 0 < max_pending <= 100_000:
            raise GuardUnavailable(UNAVAILABLE)
        # Validate issuer without manufacturing a usable assertion/key.
        self.issuer = TypeAdapter(Identifier).validate_python(issuer)
        self.credentials, self.identity, self.pairing, self.signer = credentials, identity, pairing, signer
        self.dispatch, self.reader, self.signing_admission = dispatch, reader, signing_admission
        self.max_pending = max_pending
        self._attempted: set[str] = set()
        self._lock = Lock()

    def execute(self, context: CandidateWebSession, raw_request: object) -> dict:
        """Uniform credential/ownership/proof failures; infrastructure remains unavailable."""
        try:
            return self._execute(context, PasswordReauthRequest.model_validate(raw_request))
        except (GuardDenied, ValidationError, ValueError, TypeError, KeyError):
            # Do not retain exceptions containing supplied secrets or provider text.
            raise GuardDenied(FAILURE) from None
        except GuardUnavailable:
            raise GuardUnavailable(UNAVAILABLE) from None
        except Exception:
            raise GuardUnavailable(UNAVAILABLE) from None

    def _execute(self, context: CandidateWebSession, request: PasswordReauthRequest) -> dict:
        context = CandidateWebSession.model_validate(context)
        if self.credentials.verify(context.candidate_id, request.password.get_secret_value()) is not True:
            raise GuardDenied(FAILURE)
        authenticated = self.pairing.now_ms()
        with self._lock:
            key = str(request.challenge_id)
            if key in self._attempted:
                raise GuardDenied(FAILURE)
            if len(self._attempted) >= self.max_pending:
                raise GuardUnavailable(UNAVAILABLE)
            self._attempted.add(key)

        proof = CandidateChallengeProof(operation=request.operation, challenge_id=request.challenge_id, nonce=request.nonce)
        try:
            if self.reader is not None:
                identity, payload, issued = self.reader.read(context, proof)
            else:
                identity, payload, issued = self.identity.store.transact(
                    lambda tx: read_candidate_challenge(self.identity, self.pairing, tx, context, proof))
        except GuardDenied:
            # A definite failed read has made no signature or consuming write.
            with self._lock:
                self._attempted.discard(key)
            raise
        assertion = CandidateAssertion(protocol_version=2, audience="hirewiz:pairing-only",
            assertion_id=uuid4(), issuer=self.issuer, operation=request.operation,
            subject_uuid=identity["subject_uuid"], session_id=identity["session_id"],
            principal_sha256=identity["principal_sha256"], auth_generation=identity["auth_generation"],
            method="password_reauth", authenticated_at_ms=authenticated, issued_at_ms=issued,
            expires_at_ms=min(payload["expires_at_ms"], issued + CANDIDATE_MS),
            binding_sha256=digest("candidate-context", payload), confirmed=True).model_dump(mode="json")
        if self.signing_admission is not None:
            self.signing_admission.admit(context, proof, CandidateAssertion.model_validate(assertion))
        envelope = self.signer.sign(dict(assertion))
        if type(envelope) is not dict or set(envelope) != {"payload", "signature"} or envelope["payload"] != assertion:
            raise GuardDenied(FAILURE)
        command = CandidateDispatchCommand(context=context, operation=request.operation,
            challenge_id=request.challenge_id, nonce=request.nonce,
            assertion=CandidateAssertion.model_validate(assertion), signature=envelope["signature"])
        response = self.dispatch.consume(command)
        if request.operation == "confirm_pairing":
            result = _ConfirmedResult.model_validate(response).model_dump(mode="json")
            if result["pairing_id"] != payload["pairing_id"] or result["device_id"] != payload["device_id"]:
                raise GuardDenied(FAILURE)
        else:
            result = _RevokedResult.model_validate(response).model_dump(mode="json")
            if result["device_id"] != payload["device_id"]:
                raise GuardDenied(FAILURE)
        with self._lock:
            self._attempted.discard(str(request.challenge_id))
        return result
