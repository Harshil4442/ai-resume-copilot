"""Local durable test authority, never an application/production adapter.

SQLite FULL/WAL transactions and a separate file provide real multi-process
ownership and application-only restore evidence. They DO NOT prove independent
Firestore/GCS durability, journal retention/IAM, authority-file rollback detection,
real authentication, device pairing, or physical old-process egress fencing.
No production module imports this fixture. No HTTP or employer operation exists.
"""
from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable
from pathlib import Path
from typing import TypeVar
from uuid import UUID, uuid4

from app.domains.recovery.contracts import (
    AuthenticatedActor,
    Binding,
    Revocation,
    canonical,
    fingerprint,
)
from app.domains.recovery.store import GuardDenied, GuardUnavailable, Transaction

T = TypeVar("T")
ZERO_DIGEST = "0" * 64


class FileTransaction:
    def __init__(self, connection: sqlite3.Connection):
        self.connection = connection

    def get(self, namespace: str, key: str) -> dict | None:
        row = self.connection.execute("SELECT payload FROM records WHERE namespace=? AND key=?",
                                      (namespace, key)).fetchone()
        return json.loads(row[0]) if row else None

    def put(self, namespace: str, key: str, value: dict, *, immutable: bool = False) -> None:
        prior = self.get(namespace, key)
        if immutable and prior is not None:
            if prior != value:
                raise GuardDenied("Immutable authority record conflicts")
            return
        self.connection.execute("INSERT INTO records(namespace,key,payload) VALUES (?,?,?) "
                                "ON CONFLICT(namespace,key) DO UPDATE SET payload=excluded.payload",
                                (namespace, key, canonical(value)))

    def event(self, event_id: str) -> dict | None:
        row = self.connection.execute("SELECT sequence,event_id,kind,payload,digest,previous_digest "
                                      "FROM events WHERE event_id=?", (event_id,)).fetchone()
        if row is None:
            return None
        return self._event(row)

    @staticmethod
    def _event(row: tuple) -> dict:
        return {"sequence": row[0], "event_id": row[1], "kind": row[2],
                "payload": json.loads(row[3]), "digest": row[4], "previous_digest": row[5]}

    def append(self, event_id: str, kind: str, payload: dict) -> dict:
        prior = self.event(event_id)
        if prior:
            if prior["kind"] != kind or prior["payload"] != payload:
                raise GuardDenied("Authority event ID conflicts")
            return prior
        sequence, previous = self.head()
        event = {"sequence": sequence + 1, "event_id": event_id, "kind": kind,
                 "payload": payload, "previous_digest": previous}
        digest = fingerprint(event)
        self.connection.execute("INSERT INTO events VALUES (?,?,?,?,?,?)",
                                (sequence + 1, event_id, kind, canonical(payload), digest, previous))
        return {**event, "digest": digest}

    def head(self) -> tuple[int, str]:
        row = self.connection.execute("SELECT sequence,digest FROM events ORDER BY sequence DESC LIMIT 1").fetchone()
        return (row[0], row[1]) if row else (0, ZERO_DIGEST)

    def verify(self, expected_authority_id: UUID) -> None:
        meta = self.get("meta", "identity")
        if meta != {"authority_id": str(expected_authority_id), "schema": 1}:
            raise GuardUnavailable("Missing or replaced authority identity")
        control = self.get("control", "current")
        if not control or control.get("authority_id") != str(expected_authority_id):
            raise GuardUnavailable("Authority control is missing or replaced")
        sequence, previous = 0, ZERO_DIGEST
        projected_control: tuple[str, int, str] | None = None
        for row in self.connection.execute("SELECT sequence,event_id,kind,payload,digest,previous_digest "
                                           "FROM events ORDER BY sequence"):
            event = self._event(row)
            digest = event.pop("digest")
            if (event["sequence"] != sequence + 1 or event["previous_digest"] != previous or
                    fingerprint(event) != digest):
                raise GuardUnavailable("Authority journal is corrupt or incomplete")
            payload = event["payload"]
            if event["kind"] in {"CREATED_CLOSED", "EPOCH_CLOSED", "EPOCH_OPENED"}:
                status = "OPEN" if event["kind"] == "EPOCH_OPENED" else "CLOSED"
                projected_control = (payload["epoch_id"], payload["generation"], status)
            elif event["kind"] == "ACTION_BEGUN":
                claim = self.get("claims", payload["opening_claim_key"])
                permit = self.get("permits", payload["permit_id"])
                if (claim != {"permit_id": payload["permit_id"],
                              "binding_digest": payload["binding_digest"],
                              "begun_sequence": event["sequence"]} or
                        not permit or permit["status"] not in {"BEGUN", "UNKNOWN", "LOCAL_FILLED"} or
                        permit["fingerprint"] != payload["binding_digest"] or
                        permit.get("begun_sequence") != event["sequence"]):
                    raise GuardUnavailable("Possible disclosure evidence is incomplete")
            elif event["kind"] == "REVOKED":
                scope = Revocation.model_validate(payload["scope"])
                tombstone = self.get("revocations", scope.key)
                if (not tombstone or tombstone["scope"] != payload["scope"] or
                        tombstone["sequence"] > event["sequence"]):
                    raise GuardUnavailable("Revocation evidence is incomplete")
            elif event["kind"] == "APPROVAL_REGISTERED":
                approval = self.get("approvals", payload["approval_id"])
                challenge = self.get("challenges", payload["challenge_id"])
                if (not approval or approval["fingerprint"] != payload["binding_digest"] or
                        fingerprint(approval["binding"]) != payload["binding_digest"] or
                        approval["actor_id"] != payload["actor_id"] or
                        approval["actor_digest"] != payload["actor_digest"] or
                        approval["challenge_id"] != payload["challenge_id"] or
                        not challenge or challenge.get("consumed") is not True or
                        challenge["actor_id"] != payload["actor_id"] or
                        challenge["actor_digest"] != payload["actor_digest"] or
                        challenge["subject_uuid"] != payload["subject_uuid"]):
                    raise GuardUnavailable("Independent approval evidence is incomplete")
            sequence, previous = event["sequence"], digest
        if sequence == 0:
            raise GuardUnavailable("Authority journal is absent")
        if projected_control != (control.get("epoch_id"), control.get("generation"), control.get("status")):
            raise GuardUnavailable("Epoch closure evidence is incomplete")


class FileAuthority:
    """Opt-in test-only file; mode=rw forbids recreation after evidence loss."""
    def __init__(self, path: Path, expected_authority_id: UUID, *, create: bool = False,
                 timeout_seconds: float = 1.0, after_commit: Callable[[], None] | None = None):
        self.path = Path(path)
        if not self.path.is_absolute() or str(path) == ":memory:" or self.path.is_symlink():
            raise ValueError("An absolute, separate test authority file is required")
        self.authority_id = expected_authority_id
        self.timeout_seconds = timeout_seconds
        self.after_commit = after_commit
        if create:
            with self.path.open("xb"):
                pass
            self._initialize()

    def _connect(self) -> sqlite3.Connection:
        if not self.path.is_file() or self.path.is_symlink():
            raise GuardUnavailable("Independent authority evidence is unavailable")
        connection = sqlite3.connect(self.path.as_uri() + "?mode=rw", uri=True,
                                     timeout=self.timeout_seconds, isolation_level=None)
        connection.execute("PRAGMA synchronous=FULL")
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    def _initialize(self) -> None:
        connection = self._connect()
        try:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.executescript("""
                CREATE TABLE records(namespace TEXT NOT NULL,key TEXT NOT NULL,payload TEXT NOT NULL,
                                     PRIMARY KEY(namespace,key));
                CREATE TABLE events(sequence INTEGER PRIMARY KEY,event_id TEXT UNIQUE NOT NULL,
                                    kind TEXT NOT NULL,payload TEXT NOT NULL,digest TEXT NOT NULL,
                                    previous_digest TEXT NOT NULL);
                CREATE TRIGGER immutable_events_update BEFORE UPDATE ON events
                    BEGIN SELECT RAISE(ABORT,'Append-only authority events'); END;
                CREATE TRIGGER immutable_events_delete BEFORE DELETE ON events
                    BEGIN SELECT RAISE(ABORT,'Append-only authority events'); END;
            """)
            connection.execute("BEGIN IMMEDIATE")
            tx = FileTransaction(connection)
            tx.put("meta", "identity", {"authority_id": str(self.authority_id), "schema": 1})
            control = {"authority_id": str(self.authority_id), "epoch_id": str(uuid4()),
                       "generation": 1, "status": "CLOSED"}
            tx.put("control", "current", control)
            tx.append("fixture-bootstrap", "CREATED_CLOSED", control)
            connection.commit()
        finally:
            connection.close()

    def transact(self, operation: Callable[[Transaction], T]) -> T:
        connection: sqlite3.Connection | None = None
        try:
            connection = self._connect()
            connection.execute("BEGIN IMMEDIATE")
            tx = FileTransaction(connection)
            tx.verify(self.authority_id)
            result = operation(tx)
            tx.verify(self.authority_id)
            connection.commit()
            if self.after_commit:
                try:
                    self.after_commit()
                except Exception as exc:
                    # Deliberately discard a computed permission after uncertain
                    # reply delivery. A retry can observe status, never replay it.
                    raise GuardUnavailable("Committed response delivery is uncertain") from exc
            return result
        except (sqlite3.Error, json.JSONDecodeError, KeyError, TypeError) as exc:
            raise GuardUnavailable("Local authority storage cannot confirm the operation") from exc
        finally:
            if connection is not None:
                if connection.in_transaction:
                    connection.rollback()
                connection.close()

    def provision_fixture(self, actors: list[AuthenticatedActor], binding: Binding) -> None:
        """Explicit synthetic registry setup; not a pairing/grant API."""
        def operation(tx: Transaction) -> None:
            tx.put("subjects", str(binding.subject_uuid), {"active": True}, immutable=True)
            for actor in actors:
                tx.put("actors", actor.actor_id, {"identity": actor.model_dump(mode="json"),
                                                 "active": True}, immutable=True)
            tx.put("devices", binding.device_id, {
                "subject_uuid": str(binding.subject_uuid), "key_sha256": binding.device_key_sha256,
                "executor_revision": binding.executor_revision, "active": True,
            }, immutable=True)
            tx.put("grants", binding.grant_id, {"active": True, "scope": {
                "subject_uuid": str(binding.subject_uuid), "employer_key": binding.employer_key,
                "tenant_id": binding.tenant_id, "origin": binding.origin, "action": "fill",
                "revision": binding.grant_revision,
            }}, immutable=True)
            tx.append(str(uuid4()), "FIXTURE_REGISTRY_PROVISIONED", {
                "subject_uuid": str(binding.subject_uuid), "device_id": binding.device_id,
                "grant_id": binding.grant_id, "actor_ids": [actor.actor_id for actor in actors],
            })
        self.transact(operation)
