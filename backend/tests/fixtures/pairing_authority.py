"""Explicit test-only SQLite authority with journal/projection equality.

Full/WAL, real spawned-process transactions and separate app/authority files model
local ordering, not production retention/IAM or rollback of this entire authority.
No production module imports this fixture; seed data and signing keys are synthetic.
"""
from __future__ import annotations

import base64
import json
import sqlite3
from collections.abc import Callable
from pathlib import Path
from typing import TypeVar
from uuid import UUID, uuid4

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature

from app.domains.recovery.contracts import canonical, fingerprint
from app.domains.recovery.pairing_auth import PinnedAssertions
from app.domains.recovery.pairing_contracts import CandidateAssertion, canonical_bytes
from app.domains.recovery.pairing_service import PairingService
from app.domains.recovery.store import GuardDenied, GuardUnavailable, Transaction

T = TypeVar("T")
ZERO = "0" * 64
NOW = 1_800_000_000_000
EXTENSION, RELEASE = "synthetic_extension", "fixture_release_1"


class ProjectionTransaction:
    def __init__(self, connection: sqlite3.Connection):
        self.connection = connection
        self.pending: dict[tuple[str, str], dict] = {}

    def get(self, namespace: str, key: str) -> dict | None:
        row = self.connection.execute("SELECT payload FROM records WHERE namespace=? AND key=?",
                                      (namespace, key)).fetchone()
        return json.loads(row[0]) if row else None

    def put(self, namespace: str, key: str, value: dict, *, immutable: bool = False) -> None:
        prior = self.get(namespace, key)
        if immutable and prior is not None:
            if prior != value:
                raise GuardDenied("Immutable fixture record conflicts")
            return
        self.connection.execute("INSERT INTO records VALUES (?,?,?) ON CONFLICT(namespace,key) "
                                "DO UPDATE SET payload=excluded.payload", (namespace, key, canonical(value)))
        self.pending[(namespace, key)] = value

    @staticmethod
    def _event(row: tuple) -> dict:
        return {"sequence": row[0], "event_id": row[1], "kind": row[2], "payload": json.loads(row[3]),
                "digest": row[4], "previous_digest": row[5]}

    def event(self, event_id: str) -> dict | None:
        row = self.connection.execute("SELECT * FROM events WHERE event_id=?", (event_id,)).fetchone()
        if row is None:
            return None
        event = self._event(row)
        return {**event, "payload": event["payload"]["data"]}

    def head(self) -> tuple[int, str]:
        row = self.connection.execute("SELECT sequence,digest FROM events ORDER BY sequence DESC LIMIT 1").fetchone()
        return (row[0], row[1]) if row else (0, ZERO)

    def append(self, event_id: str, kind: str, payload: dict) -> dict:
        changes = [{"namespace": ns, "key": key, "value": value}
                   for (ns, key), value in sorted(self.pending.items())]
        wrapped = {"data": payload, "changes": changes}
        prior = self.event(event_id)
        if prior:
            if prior["kind"] != kind or prior["payload"] != payload:
                raise GuardDenied("Immutable fixture event conflicts")
            return prior
        sequence, previous = self.head()
        event = {"sequence": sequence + 1, "event_id": event_id, "kind": kind,
                 "payload": wrapped, "previous_digest": previous}
        event_sha = fingerprint(event)
        self.connection.execute("INSERT INTO events VALUES (?,?,?,?,?,?)", (
            sequence + 1, event_id, kind, canonical(wrapped), event_sha, previous,
        ))
        self.pending.clear()
        return {**event, "payload": payload, "digest": event_sha}

    def verify(self, expected: str) -> None:
        if self.pending:
            raise GuardUnavailable("Unjournaled fixture projection changes")
        projected: dict[tuple[str, str], dict] = {}
        sequence, previous = 0, ZERO
        for row in self.connection.execute("SELECT * FROM events ORDER BY sequence"):
            event = self._event(row)
            actual_digest = event.pop("digest")
            if (event["sequence"] != sequence + 1 or event["previous_digest"] != previous
                    or fingerprint(event) != actual_digest):
                raise GuardUnavailable("Journal chain is corrupt or cut")
            for change in event["payload"]["changes"]:
                projected[(change["namespace"], change["key"])] = change["value"]
            sequence, previous = event["sequence"], actual_digest
        records = {(ns, key): json.loads(payload) for ns, key, payload in
                   self.connection.execute("SELECT * FROM records")}
        if (sequence == 0 or projected.keys() != records.keys() or
                any(canonical(value) != canonical(records[key]) for key, value in projected.items())):
            raise GuardUnavailable("Journal/projection partitions disagree")
        if records.get(("meta", "identity")) != {"authority_id": expected, "schema": "pairing-fixture-1"}:
            raise GuardUnavailable("Authority identity is absent or replaced")
        control = records.get(("control", "current"))
        if not control or control.get("authority_id") != expected:
            raise GuardUnavailable("Authority control evidence is missing")


class PairingAuthority:
    def __init__(self, path: Path, authority_id: str, *, create: bool = False,
                 after_commit: Callable[[], None] | None = None):
        self.path, self.authority_id, self.after_commit = Path(path), str(UUID(authority_id)), after_commit
        if not self.path.is_absolute() or self.path.is_symlink():
            raise ValueError("Independent absolute fixture path required")
        if create:
            with self.path.open("xb"):
                pass
            self._initialize()

    def _connect(self) -> sqlite3.Connection:
        if not self.path.is_file() or self.path.is_symlink():
            raise GuardUnavailable("Independent authority file is missing")
        db = sqlite3.connect(self.path.as_uri() + "?mode=rw", uri=True, timeout=5, isolation_level=None)
        db.execute("PRAGMA synchronous=FULL")
        return db

    def _initialize(self) -> None:
        db = self._connect()
        try:
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript("""
                CREATE TABLE records(namespace TEXT,key TEXT,payload TEXT,PRIMARY KEY(namespace,key));
                CREATE TABLE events(sequence INTEGER PRIMARY KEY,event_id TEXT UNIQUE,kind TEXT,
                                    payload TEXT,digest TEXT,previous_digest TEXT);
                CREATE TRIGGER events_no_update BEFORE UPDATE ON events
                    BEGIN SELECT RAISE(ABORT,'Append only'); END;
                CREATE TRIGGER events_no_delete BEFORE DELETE ON events
                    BEGIN SELECT RAISE(ABORT,'Append only'); END;
            """)
            db.execute("BEGIN IMMEDIATE")
            tx = ProjectionTransaction(db)
            tx.put("meta", "identity", {"authority_id": self.authority_id, "schema": "pairing-fixture-1"})
            tx.put("control", "current", {"authority_id": self.authority_id, "incarnation": 1,
                   "epoch_id": str(uuid4()), "generation": 1, "status": "OPEN"})
            tx.put("pairing_extensions", f"{EXTENSION}:{RELEASE}", {"extension_id": EXTENSION,
                   "executor_revision": RELEASE, "protocol_version": 2, "active": True})
            tx.append(str(uuid4()), "FIXTURE_GENESIS", {})
            tx.verify(self.authority_id)
            db.commit()
        finally:
            db.close()

    def transact(self, operation: Callable[[Transaction], T]) -> T:
        db = self._connect()
        try:
            db.execute("BEGIN IMMEDIATE")
            tx = ProjectionTransaction(db)
            tx.verify(self.authority_id)
            result = operation(tx)
            tx.verify(self.authority_id)
            db.commit()
            if self.after_commit:
                self.after_commit()
            return result
        except sqlite3.Error as exc:
            db.rollback()
            raise GuardUnavailable("Fixture transaction unavailable") from exc
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def seed(self, records: list[tuple[str, str, dict]], *, kind: str = "FIXTURE_ADMIN") -> None:
        def operation(tx: Transaction) -> None:
            for namespace, key, value in records:
                tx.put(namespace, key, value)
            tx.append(str(uuid4()), kind, {})
        self.transact(operation)

    def subject(self, subject_id: str | None = None) -> tuple[str, str]:
        subject_id, session_id = subject_id or str(uuid4()), str(uuid4())
        principal = fingerprint({"fixture_subject": subject_id})
        registration_id = str(uuid4())
        def operation(tx: Transaction) -> None:
            tx.put("subjects", subject_id, {"active": True, "principal_sha256": principal,
                "auth_generation": 1, "registration_event_id": registration_id}, immutable=True)
            tx.put("pairing_sessions", session_id, {"subject_uuid": subject_id,
                "principal_sha256": principal, "auth_generation": 1, "active": True}, immutable=True)
            tx.append(registration_id, "SUBJECT_REGISTERED", {"subject_uuid": subject_id})
        self.transact(operation)
        return subject_id, session_id


def b64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode()


def jwk(private: ec.EllipticCurvePrivateKey) -> dict:
    numbers = private.public_key().public_numbers()
    return {"kty": "EC", "crv": "P-256", "x": b64(numbers.x.to_bytes(32, "big")),
            "y": b64(numbers.y.to_bytes(32, "big"))}


def sign(private: ec.EllipticCurvePrivateKey, kind: str, payload: dict) -> str:
    der = private.sign(canonical_bytes(kind, payload), ec.ECDSA(hashes.SHA256()))
    r, s = decode_dss_signature(der)
    return b64(r.to_bytes(32, "big") + s.to_bytes(32, "big"))


class FixtureClaims:
    def __init__(self, private: ec.EllipticCurvePrivateKey):
        self.private = private

    def sign(self, payload: dict) -> dict:
        return {"payload": payload, "signature": sign(self.private, "device-claim", payload)}


class Scenario:
    def __init__(self, directory: Path):
        self.now = NOW
        self.store = PairingAuthority(directory / "authority.sqlite", str(uuid4()), create=True)
        self.subject, self.session = self.store.subject()
        self.auth_key = ec.generate_private_key(ec.SECP256R1())
        self.device_key = ec.generate_private_key(ec.SECP256R1())
        self.claim_key = ec.generate_private_key(ec.SECP256R1())
        self.service = PairingService(self.store, assertions=PinnedAssertions(issuer="fixture_auth", public_key=jwk(self.auth_key)),
                                     claims=FixtureClaims(self.claim_key), now_ms=lambda: self.now)

    def assertion(self, challenge: dict, **changes: object) -> dict:
        context = challenge["payload"]
        payload = CandidateAssertion(protocol_version=2, assertion_id=uuid4(), issuer="fixture_auth",
            audience="hirewiz:pairing-only", operation=context["operation"], subject_uuid=context["subject_uuid"],
            auth_generation=context["auth_generation"], principal_sha256=context["principal_sha256"],
            session_id=context["session_id"], method="password_reauth", authenticated_at_ms=self.now,
            issued_at_ms=self.now, expires_at_ms=self.now + 60_000,
            binding_sha256=challenge["binding_sha256"], confirmed=True).model_dump(mode="json")
        payload.update(changes)
        return {"payload": payload, "signature": sign(self.auth_key, "candidate-assertion", payload)}

    def requested(self, key: ec.EllipticCurvePrivateKey | None = None) -> dict:
        key = key or self.device_key
        initial = self.service.prepare_request(jwk(key), EXTENSION, RELEASE)
        request = initial["request"]
        self.service.create_request(request["pairing_id"], initial["nonce"], sign(key, "request", request))
        return request

    def confirmed(self, request: dict | None = None, *, subject: str | None = None, session: str | None = None) -> dict:
        request = request or self.requested()
        challenge = self.service.candidate_challenge(request["pairing_id"], subject or self.subject, session or self.session)
        self.service.confirm_candidate(challenge["payload"]["challenge_id"], challenge["nonce"], self.assertion(challenge))
        return request

    def proof(self, request: dict | None = None) -> dict:
        request = request or self.confirmed()
        challenge = self.service.device_challenge(request["pairing_id"])
        return {"challenge_id": challenge["payload"]["challenge_id"], "nonce": challenge["nonce"],
                "signature": sign(self.device_key, "device-challenge", challenge["payload"])}

    def complete(self) -> tuple[dict, dict]:
        request = self.confirmed()
        return request, self.service.complete_device(**self.proof(request))
