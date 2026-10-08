"""Injected bounded Firestore buffering; no legacy/production-store bridge."""
from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Callable
from typing import Protocol

from google.api_core.exceptions import Aborted

from .contracts import canonical, fingerprint
from .gcp_contracts import AmbiguousCommit, RpcCommit, RpcSnapshot, RpcWrite
from .store import GuardDenied, GuardUnavailable


class RegistryRpc(Protocol):
    database_resource: str

    def begin(self) -> bytes: ...
    def read(self, transaction_id: bytes, paths: tuple[str, ...]) -> dict[str, RpcSnapshot]: ...
    def commit(self, transaction_id: bytes, writes: tuple[RpcWrite, ...]) -> RpcCommit: ...
    def rollback(self, transaction_id: bytes) -> None: ...


def _raw(value: dict) -> bytes:
    try:
        raw = canonical(value).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise GuardDenied("Record must be canonical JSON") from exc
    if len(raw) > 65_536:
        raise GuardDenied("Record byte budget exceeded")
    return raw


def _copy(value: dict) -> dict:
    return json.loads(_raw(value))


class BufferedTransaction:
    def __init__(self, rpc: RegistryRpc, transaction_id: bytes,
                 before_read: Callable[[], None] = lambda: None) -> None:
        self.rpc, self.transaction_id = rpc, transaction_id
        self.before_read = before_read
        self.original: dict[str, RpcSnapshot] = {}
        self.overlay: dict[str, bytes] = {}

    def path(self, namespace: str, key: str) -> str:
        if not namespace or not key or len(namespace) > 80 or len(key) > 200:
            raise GuardDenied("Record identity is missing or oversized")
        digest = fingerprint({"namespace": namespace, "key": key})
        return f"{self.rpc.database_resource}/documents/authority_records/{digest}"

    def _original(self, path: str) -> RpcSnapshot:
        if path not in self.original:
            if len(self.original) >= 64:
                raise GuardDenied("Transaction read budget exceeded")
            self.before_read()
            result = self.rpc.read(self.transaction_id, (path,))
            if set(result) != {path} or result[path].path != path:
                raise GuardUnavailable("Incomplete transactional snapshot")
            self.original[path] = result[path]
        return self.original[path]

    def get(self, namespace: str, key: str) -> dict | None:
        path = self.path(namespace, key)
        raw = self.overlay.get(path)
        if raw is None:
            raw = self._original(path).raw
        if raw is None:
            return None
        try:
            body = json.loads(raw)
            if (_raw(body) != raw or set(body) != {"namespace", "key", "value"}
                    or body["namespace"] != namespace or body["key"] != key
                    or not isinstance(body["value"], dict)):
                raise ValueError("Stored identity/canonical bytes mismatch")
            return _copy(body["value"])
        except (ValueError, TypeError, KeyError) as exc:
            raise GuardUnavailable("Corrupt authority record") from exc

    def _stage(self, namespace: str, key: str, value: dict, *, immutable: bool) -> None:
        path = self.path(namespace, key)
        old = self.get(namespace, key)  # Original read/absence before any write.
        value = _copy(value)
        if immutable and old is not None and _raw(old) != _raw(value):
            raise GuardDenied("Immutable authority record conflict")
        raw = _raw({"namespace": namespace, "key": key, "value": value})
        if old is not None and _raw(old) == _raw(value):
            return
        if path not in self.overlay and len(self.overlay) >= 64:
            raise GuardDenied("Transaction write budget exceeded")
        self.overlay[path] = raw

    def put(self, namespace: str, key: str, value: dict, *, immutable: bool = False) -> None:
        if namespace in {"control", "clock", "events", "head"}:
            raise GuardDenied("Runtime cannot write operator control or legacy clock/head")
        if namespace in {"opening_hold", "key_ownership", "deny", "operations"}:
            immutable = True
        self._stage(namespace, key, value, immutable=immutable)

    def head(self) -> tuple[int, str]:
        head = self.get("head", "global")
        if (head is None or set(head) != {"sequence", "digest"}
                or type(head["sequence"]) is not int or not 0 <= head["sequence"] <= 2**53 - 1
                or not isinstance(head["digest"], str) or len(head["digest"]) != 64
                or any(char not in "0123456789abcdef" for char in head["digest"])):
            raise GuardUnavailable("An operator-initialized global head is required")
        return head["sequence"], head["digest"]

    def event(self, event_id: str) -> dict | None:
        return self.get("events", event_id)

    def append(self, event_id: str, kind: str, payload: dict) -> dict:
        existing = self.event(event_id)
        if existing is not None:
            if existing.get("kind") != kind or _raw(existing.get("payload", {})) != _raw(payload):
                raise GuardDenied("Event identity conflict")
            return existing
        sequence, digest = self.head()
        if sequence == 2**53 - 1:
            raise GuardUnavailable("Global head canonical sequence budget exhausted")
        event = {"sequence": sequence + 1, "previous": digest, "kind": kind,
                 "payload": _copy(payload), "event_id": event_id}
        event["digest"] = hashlib.sha256(_raw(event)).hexdigest()
        self._stage("events", event_id, event, immutable=True)
        self._stage("head", "global", {"sequence": sequence + 1, "digest": event["digest"]},
                    immutable=False)
        return _copy(event)

    def writes(self) -> tuple[RpcWrite, ...]:
        return tuple(RpcWrite(path, raw, self.original[path].version)
                     for path, raw in sorted(self.overlay.items()))


class BufferedRegistry:
    """Status primitives only; callback results are always discarded."""

    def __init__(self, rpc: RegistryRpc, *, max_attempts: int = 3,
                 deadline_seconds: float = 5.0, monotonic: Callable[[], float] = time.monotonic) -> None:
        if type(max_attempts) is not int or not 1 <= max_attempts <= 3:
            raise ValueError("At most three definite-abort attempts are allowed")
        if not 0 < deadline_seconds <= 10:
            raise ValueError("Registry total retry deadline is invalid")
        self.rpc, self.max_attempts = rpc, max_attempts
        self.deadline_seconds, self.monotonic = deadline_seconds, monotonic

    def run(self, operation: Callable[[BufferedTransaction], None]) -> None:
        deadline = self.monotonic() + self.deadline_seconds
        def check_deadline() -> None:
            if self.monotonic() >= deadline:
                raise GuardUnavailable("Transaction retry deadline exceeded")

        for attempt in range(self.max_attempts):
            check_deadline()
            try:
                transaction_id = self.rpc.begin()
            except Aborted:
                if attempt + 1 == self.max_attempts:
                    raise GuardUnavailable("Definite-abort retry budget exhausted") from None
                continue
            tx = BufferedTransaction(self.rpc, transaction_id, check_deadline)
            try:
                operation(tx)
                if self.monotonic() >= deadline:
                    raise GuardUnavailable("Transaction deadline exceeded before Commit")
                writes = tx.writes()
                if not writes:
                    # A verified exact replay is a read-only snapshot. It returns no
                    # callback value and creates no permission or mutation receipt.
                    self._rollback(transaction_id)
                    return
                committed = self.rpc.commit(transaction_id, writes)
                if committed.write_count != len(writes):
                    raise AmbiguousCommit("Incomplete committed write acknowledgement")
                return
            except Aborted:
                self._rollback(transaction_id)
                if attempt + 1 == self.max_attempts:
                    raise GuardUnavailable("Definite-abort retry budget exhausted") from None
            except AmbiguousCommit:
                # No rollback or second Commit can establish the unknown outcome.
                raise
            except Exception:
                self._rollback(transaction_id)
                raise
        raise GuardUnavailable("Transaction did not commit")

    def read(self, namespace: str, key: str) -> dict | None:
        return self.read_many(((namespace, key),))[namespace, key]

    def read_many(self, keys: tuple[tuple[str, str], ...]) -> dict[tuple[str, str], dict | None]:
        if not 0 < len(keys) <= 64 or len(set(keys)) != len(keys):
            raise GuardDenied("Read-only record set must be unique and bounded")
        try:
            transaction_id = self.rpc.begin()
        except Aborted as exc:
            raise GuardUnavailable("Read-only transaction could not begin") from exc
        try:
            tx = BufferedTransaction(self.rpc, transaction_id)
            return {(namespace, key): tx.get(namespace, key) for namespace, key in keys}
        except Aborted as exc:
            raise GuardUnavailable("Read-only transactional snapshot was aborted") from exc
        finally:
            self._rollback(transaction_id)

    def _rollback(self, transaction_id: bytes) -> None:
        try:
            self.rpc.rollback(transaction_id)
        except Exception:
            # A failed read/definite-abort cleanup never authorizes an action.
            pass
