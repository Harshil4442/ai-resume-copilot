"""Local Firestore emulator data RPC proof, never UID, IAM or retention proof.

This isolated test file targets only an explicitly owned loopback emulator.
No SDK client is constructed from ADC, environment settings or a cloud endpoint.
The operator seed below is synthetic fixture initialization, not runtime bootstrap.
"""
from __future__ import annotations

import json
import os
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Lock
from unittest.mock import MagicMock
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

import google.auth
import grpc
import pytest
import requests
from backend.tests.fixtures.firestore_emulator import local_firestore_endpoint
from backend.tests.fixtures.gcp_publication import publication_for
from google.api_core.exceptions import Aborted
from google.auth.credentials import AnonymousCredentials
from google.cloud.firestore_v1.services.firestore import FirestoreClient
from google.cloud.firestore_v1.services.firestore.transports.grpc import FirestoreGrpcTransport
from google.cloud.firestore_v1.types import Document, Write
from google.cloud.storage import Client
from test_gcp_journal_sdk import response

from app.domains.recovery.contracts import canonical
from app.domains.recovery.gcp_buffer import BufferedRegistry, BufferedTransaction
from app.domains.recovery.gcp_contracts import (
    JournalIntent,
    OpeningHold,
    RegistryPin,
    RpcWrite,
)
from app.domains.recovery.gcp_journal import GcsJournal
from app.domains.recovery.gcp_rpc import FirestoreRpc
from app.domains.recovery.gcp_service import SafetyCoordinator, control_record
from app.domains.recovery.store import GuardDenied, GuardUnavailable

ENDPOINT = local_firestore_endpoint()
PROJECT = "hirewiz-local-authority"


class NoMetadata:
    def get_database(self, **kwargs):
        raise AssertionError("Emulator data tests cannot establish database UID or IAM")


class Emulator:
    def __init__(self):
        self.database = f"projects/{PROJECT}/databases/authority-test-{uuid4().hex[:12]}"
        self.channel = grpc.insecure_channel(ENDPOINT)
        transport = FirestoreGrpcTransport(host=ENDPOINT, channel=self.channel,
                                          credentials=AnonymousCredentials())
        self.client = FirestoreClient(transport=transport)
        self.rpc = FirestoreRpc(self.client, NoMetadata(), database_resource=self.database,
                                rpc_timeout=5.0)
        self.registry = BufferedRegistry(self.rpc, deadline_seconds=10.0)
        self.sent = []
        self.original_commit = self.rpc.commit
        self.rpc.commit = self.record_commit

    def record_commit(self, transaction_id, writes):
        self.sent.append((transaction_id, writes))
        return self.original_commit(transaction_id, writes)

    def path(self, namespace: str, key: str) -> str:
        return BufferedTransaction(self.rpc, b"path-only").path(namespace, key)

    def operator_seed(self):
        writes = []
        for namespace, key, value in (
            ("control", "meta", {"state": "OPEN", "synthetic_operator_seed": True}),
            ("head", "global", {"sequence": 0, "digest": "0" * 64}),
        ):
            raw = canonical({"namespace": namespace, "key": key, "value": value}).encode()
            writes.append(Write(update=Document(name=self.path(namespace, key),
                fields={"body": {"bytes_value": raw}}), current_document={"exists": False}))
        response = self.client.commit(request={"database": self.database, "writes": writes},
                                      retry=None, timeout=5.0)
        assert len(response.write_results) == 2


@pytest.fixture
def emulator(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("ADC or a secure/cloud transport is forbidden in local emulator tests")
    monkeypatch.setattr(google.auth, "default", forbidden)
    monkeypatch.setattr(grpc, "secure_channel", forbidden)
    instance = Emulator()
    try:
        try:
            grpc.channel_ready_future(instance.channel).result(timeout=3.0)
        except grpc.FutureTimeoutError:
            if os.getenv("HIREWIZ_AUTHORITY_EMULATOR_REQUIRED") == "true":
                pytest.fail(f"Required root-owned local emulator unavailable at {ENDPOINT}")
            pytest.skip(f"Explicit root-owned local emulator unavailable at {ENDPOINT}")
        instance.operator_seed()
        yield instance
    finally:
        instance.channel.close()


def test_named_database_round_trip_missing_existing_and_create_preconditions(emulator):
    first = emulator.path("records", "first")
    second = emulator.path("records", "second")
    transaction = emulator.rpc.begin()
    assert {item.raw for item in emulator.rpc.read(transaction, (first, second)).values()} == {None}
    assert emulator.rpc.commit(transaction, (RpcWrite(first, b'{"value":1}', None),
        RpcWrite(second, b'{"value":2}', None))).write_count == 2
    transaction = emulator.rpc.begin()
    snapshots = emulator.rpc.read(transaction, (first, second))
    assert snapshots[first].raw == b'{"value":1}' and snapshots[first].version is not None
    assert snapshots[second].raw == b'{"value":2}' and snapshots[second].version is not None
    assert emulator.rpc.commit(transaction, (RpcWrite(first, b'{"value":3}',
        snapshots[first].version),)).write_count == 1
    transaction = emulator.rpc.begin()
    assert emulator.rpc.read(transaction, (first,))[first].raw == b'{"value":3}'
    emulator.rpc.rollback(transaction)


def test_buffer_overlay_later_reads_and_atomic_event_visibility(emulator):
    observed = []
    def stage(tx):
        value = {"active": True, "nested": {"count": 1}}
        tx.put("records", "first", value, immutable=True)
        value["nested"]["count"] = 99
        # A native read after staged writes is legal because no RPC write was queued.
        assert tx.get("records", "unrelated") is None
        copy = tx.get("records", "first")
        assert copy == {"active": True, "nested": {"count": 1}}
        copy["nested"]["count"] = 77
        assert tx.get("records", "first")["nested"]["count"] == 1
        tx.put("records", "second", {"active": False}, immutable=True)
        event = tx.append("stable-event", "fixture-created", {"records": 2})
        observed.append(event)
        assert emulator.sent == []
        assert emulator.registry.read("records", "first") is None
        assert emulator.registry.read("events", "stable-event") is None
    assert emulator.registry.run(stage) is None
    assert len(emulator.sent) == 1 and len(emulator.sent[0][1]) == 4
    assert emulator.registry.read("records", "first")["nested"]["count"] == 1
    assert emulator.registry.read("records", "second") == {"active": False}
    assert emulator.registry.read("events", "stable-event") == observed[0]
    assert emulator.registry.read("head", "global") == {
        "sequence": 1, "digest": observed[0]["digest"]}


def test_failed_callback_rolls_back_without_partial_records_or_event(emulator):
    def stage(tx):
        tx.put("records", "first", {"created": True}, immutable=True)
        tx.append("stable-event", "fixture-created", {"records": 1})
        raise GuardDenied("Fixture rejects before Commit")
    with pytest.raises(GuardDenied):
        emulator.registry.run(stage)
    assert emulator.sent == []
    assert emulator.registry.read("records", "first") is None
    assert emulator.registry.read("events", "stable-event") is None
    assert emulator.registry.read("head", "global") == {"sequence": 0, "digest": "0" * 64}


def test_direct_contended_transactions_return_definite_aborted_without_replay(
    emulator, monkeypatch, record_property,
):
    native_begin = MagicMock(wraps=emulator.client.begin_transaction)
    native_commit = MagicMock(wraps=emulator.client.commit)
    monkeypatch.setattr(emulator.client, "begin_transaction", native_begin)
    monkeypatch.setattr(emulator.client, "commit", native_commit)
    path = emulator.path("records", "contended")
    first, second = emulator.rpc.begin(), emulator.rpc.begin()
    assert first != second
    assert emulator.rpc.read(first, (path,))[path].raw is None
    assert emulator.rpc.read(second, (path,))[path].raw is None
    def compete(transaction, owner):
        try:
            result = emulator.rpc.commit(transaction, (
                RpcWrite(path, canonical({"winner": owner}).encode(), None),))
            assert result.write_count == 1
            return "COMMITTED", owner, transaction
        except Aborted:
            return "ABORTED", owner, transaction
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(compete, transaction, owner)
                   for transaction, owner in ((first, 1), (second, 2))]
        outcomes = [future.result(timeout=10.0) for future in futures]
    # Contention need not produce an original winner. The emulator explicitly
    # differs from production locking; both native attempts may be aborted.
    # https://docs.cloud.google.com/firestore/native/docs/emulator#transactions
    statuses = [status for status, _, _ in outcomes]
    assert set(statuses) <= {"ABORTED", "COMMITTED"}
    assert statuses.count("COMMITTED") <= 1 and statuses.count("ABORTED") >= 1
    record_property("initial_contention_outcomes", sorted(statuses))
    assert len(emulator.sent) == native_commit.call_count == 2
    assert native_begin.call_count == 2
    assert {call.kwargs["request"]["transaction"]
            for call in native_commit.call_args_list} == {first, second}
    assert all(call.kwargs["retry"] is None for call in native_commit.call_args_list)
    assert all(len(call.kwargs["request"]["writes"]) == 1 and
               Write.pb(call.kwargs["request"]["writes"][0]).current_document.WhichOneof(
                   "condition_type") == "exists" and
               call.kwargs["request"]["writes"][0].current_document.exists is False
               for call in native_commit.call_args_list)
    aborted = [(owner, transaction) for status, owner, transaction in outcomes
               if status == "ABORTED"]
    for _, transaction in aborted:
        emulator.rpc.rollback(transaction)
    winner = next((owner for status, owner, _ in outcomes if status == "COMMITTED"), None)
    inspection = emulator.rpc.begin()
    snapshot = emulator.rpc.read(inspection, (path,))[path]
    assert snapshot.raw == (None if winner is None else canonical({"winner": winner}).encode())
    emulator.rpc.rollback(inspection)

    # The caller explicitly restarts the read/create sequence after a definite
    # abort. Fresh reads preserve an existing owner instead of replaying the
    # stale absent-document snapshot or overwriting a concurrent winner.
    fresh_ids: set[bytes] = set()
    fresh_outcomes: list[str] = []
    for owner, _ in aborted:
        fresh = emulator.rpc.begin()
        assert fresh not in {first, second, inspection} | fresh_ids
        fresh_ids.add(fresh)
        snapshot = emulator.rpc.read(fresh, (path,))[path]
        if snapshot.raw is None:
            assert winner is None and snapshot.version is None
            committed = emulator.rpc.commit(fresh, (
                RpcWrite(path, canonical({"winner": owner}).encode(), None),))
            assert committed.write_count == 1
            winner = owner
            fresh_outcomes.append("COMMITTED")
        else:
            assert winner is not None and snapshot.version is not None
            assert snapshot.raw == canonical({"winner": winner}).encode()
            emulator.rpc.rollback(fresh)
            fresh_outcomes.append("CONFLICT")
    assert statuses.count("COMMITTED") + fresh_outcomes.count("COMMITTED") == 1
    assert fresh_outcomes.count("CONFLICT") == 1
    expected_sends = 2 + (1 if statuses.count("COMMITTED") == 0 else 0)
    assert len(emulator.sent) == native_commit.call_count == expected_sends
    sent_ids = [call.kwargs["request"]["transaction"] for call in native_commit.call_args_list]
    assert len(sent_ids) == len(set(sent_ids))
    assert all(call.kwargs["retry"] is None for call in native_commit.call_args_list)
    transaction = emulator.rpc.begin()
    assert emulator.rpc.read(transaction, (path,))[path].raw == canonical({"winner": winner}).encode()
    emulator.rpc.rollback(transaction)
    assert native_begin.call_count == 4 + len(aborted)
    assert all(call.kwargs["retry"] is None and
               call.kwargs["request"]["options"] == {"read_write": {}}
               for call in native_begin.call_args_list)


def test_permanent_opening_hold_race_keeps_one_owner_and_fresh_retry(
    emulator, monkeypatch, record_property,
):
    hold = OpeningHold(subject_uuid=uuid4(), employer_key="a" * 64, tenant_id="synthetic",
                       opening_key="b" * 64, binding_sha256="c" * 64)
    barrier, lock = Barrier(2), Lock()
    attempts = {"first": 0, "second": 0}
    transaction_ids: dict[str, list[bytes]] = {owner: [] for owner in attempts}
    read_states: dict[str, list[dict | None]] = {owner: [] for owner in attempts}
    outcomes = {}
    native_begin = MagicMock(wraps=emulator.client.begin_transaction)
    native_commit = MagicMock(wraps=emulator.client.commit)
    monkeypatch.setattr(emulator.client, "begin_transaction", native_begin)
    monkeypatch.setattr(emulator.client, "commit", native_commit)
    def contender(owner):
        def stage(tx):
            attempts[owner] += 1
            transaction_ids[owner].append(tx.transaction_id)
            read_states[owner].append(tx.get(hold.kind, hold.record_key))
            if attempts[owner] == 1:
                barrier.wait(timeout=5.0)
            tx.put(hold.kind, hold.record_key,
                   {"effect": hold.model_dump(mode="json"), "owner": owner}, immutable=True)
        try:
            emulator.registry.run(stage)
            outcome = "COMMITTED"
        except GuardDenied:
            outcome = "CONFLICT"
        except GuardUnavailable as exc:
            # Only exhaustion after definite ABORTED replies can be explicitly
            # retried here. AmbiguousCommit and other outages must fail the test.
            assert str(exc) == "Definite-abort retry budget exhausted"
            outcome = "EXHAUSTED"
        with lock:
            outcomes[owner] = outcome
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(contender, owner) for owner in attempts]
        for future in futures:
            future.result(timeout=20.0)
    assert list(outcomes.values()).count("COMMITTED") <= 1
    assert set(outcomes.values()) <= {"COMMITTED", "CONFLICT", "EXHAUSTED"}
    assert emulator.registry.max_attempts == 3
    assert all(1 <= count <= 3 for count in attempts.values())
    assert 2 <= native_begin.call_count <= 6
    ids = [identity for values in transaction_ids.values() for identity in values]
    assert len(ids) == len(set(ids))
    assert all(states[0] is None for states in read_states.values())
    assert native_commit.call_count == len(emulator.sent)
    assert all(call.kwargs["retry"] is None for call in native_commit.call_args_list)
    assert all(call.kwargs["retry"] is None for call in native_begin.call_args_list)
    sent_ids = [call.kwargs["request"]["transaction"] for call in native_commit.call_args_list]
    assert len(sent_ids) == len(set(sent_ids))
    record_property("opening_hold_callback_attempts", attempts)
    record_property("opening_hold_initial_outcomes", outcomes)

    winner = next((owner for owner, status in outcomes.items() if status == "COMMITTED"), None)
    existing = emulator.registry.read(hold.kind, hold.record_key)
    if winner is None:
        assert existing is None and set(outcomes.values()) == {"EXHAUSTED"}
        # The prior operations terminated after their original three-attempt
        # budgets. This is an explicit new caller operation with ONE attempt,
        # not an expanded retry budget or replay of an ambiguous Commit.
        winner = "first"
        before = len(emulator.sent)
        single_attempt = BufferedRegistry(emulator.rpc, max_attempts=1, deadline_seconds=10.0)
        def fresh_create(tx):
            assert tx.transaction_id not in ids
            assert tx.get(hold.kind, hold.record_key) is None
            tx.put(hold.kind, hold.record_key,
                   {"effect": hold.model_dump(mode="json"), "owner": winner}, immutable=True)
        single_attempt.run(fresh_create)
        assert len(emulator.sent) == before + 1
    else:
        assert read_states[winner][-1] is None
    expected = {"effect": hold.model_dump(mode="json"), "owner": winner}
    assert emulator.registry.read(hold.kind, hold.record_key) == expected
    loser = next(owner for owner in outcomes if owner != winner)
    if outcomes[loser] == "CONFLICT":
        assert attempts[loser] >= 2 and read_states[loser][-1] == expected
    else:
        assert outcomes[loser] == "EXHAUSTED" and attempts[loser] == 3
    before = len(emulator.sent)
    def fresh_conflict(tx):
        assert tx.transaction_id not in ids
        assert tx.get(hold.kind, hold.record_key) == expected
        tx.put(hold.kind, hold.record_key,
               {"effect": hold.model_dump(mode="json"), "owner": loser}, immutable=True)
    with pytest.raises(GuardDenied, match="Immutable authority record conflict"):
        BufferedRegistry(emulator.rpc, max_attempts=1, deadline_seconds=10.0).run(fresh_conflict)
    assert len(emulator.sent) == before
    # A new binding, epoch-independent operation, or retry cannot replace old ownership.
    renewed = hold.model_copy(update={"binding_sha256": "d" * 64})
    assert renewed.record_key == hold.record_key
    with pytest.raises(GuardDenied):
        emulator.registry.run(lambda tx: tx.put(renewed.kind, renewed.record_key,
            {"effect": renewed.model_dump(mode="json"), "owner": "later"}, immutable=True))
    assert emulator.registry.read(hold.kind, hold.record_key) == expected


def test_storage_response_loss_reconciles_before_atomic_emulator_commit_and_exact_replay(
    emulator, monkeypatch,
):
    """Real data RPC plus intercepted Storage SDK; the fence/UID are synthetic."""
    pin = RegistryPin(database=emulator.database, database_uid=uuid4(), authority_id=uuid4(),
                      incarnation=1, epoch_id=uuid4())
    intent = JournalIntent(operation_id=uuid4(), pin=pin,
        effect=OpeningHold(subject_uuid=uuid4(), employer_key="a" * 64, tenant_id="synthetic",
                           opening_key="b" * 64, binding_sha256="c" * 64),
        created_at_ms=1_800_000_000_000, deadline_ms=1_800_000_010_000)
    # Explicit operator fixture replaces only the synthetic control seed. Runtime
    # BufferedTransaction.put remains forbidden for both control and global head.
    transaction = emulator.rpc.begin()
    control_path = emulator.path("control", "meta")
    control_version = emulator.rpc.read(transaction, (control_path,))[control_path].version
    emulator.rpc.rollback(transaction)
    raw_control = canonical({"namespace": "control", "key": "meta",
                             "value": control_record(pin)}).encode()
    operator_result = emulator.client.commit(request={"database": emulator.database,
        "writes": [Write(update=Document(name=control_path,
            fields={"body": {"bytes_value": raw_control}}),
            current_document={"update_time": control_version})]}, retry=None, timeout=5.0)
    assert len(operator_result.write_results) == 1

    active_transactions, ordering, sdk_commit_replies = set(), [], []
    sdk_commit = emulator.client.commit
    def capture_commit(**kwargs):
        reply = sdk_commit(**kwargs)
        sdk_commit_replies.append({"write_count": len(kwargs["request"]["writes"]),
            "has_commit_time": type(reply).pb(reply).HasField("commit_time")})
        return reply
    monkeypatch.setattr(emulator.client, "commit", capture_commit)
    real_begin, real_commit, real_rollback = (
        emulator.rpc.begin, emulator.rpc.commit, emulator.rpc.rollback)
    def begin():
        transaction_id = real_begin()
        active_transactions.add(transaction_id)
        ordering.append("firestore-begin")
        return transaction_id
    def commit(transaction_id, writes):
        ordering.append("firestore-commit")
        try:
            return real_commit(transaction_id, writes)
        finally:
            active_transactions.discard(transaction_id)
    def rollback(transaction_id):
        try:
            return real_rollback(transaction_id)
        finally:
            active_transactions.discard(transaction_id)
    monkeypatch.setattr(emulator.rpc, "begin", begin)
    monkeypatch.setattr(emulator.rpc, "commit", commit)
    monkeypatch.setattr(emulator.rpc, "rollback", rollback)

    monkeypatch.setenv("DISABLE_GCS_PYTHON_CLIENT_OTEL_BUCKET_METADATA", "true")
    session = MagicMock(spec=requests.Session)
    session.is_mtls = False
    storage = Client(project=PROJECT, credentials=AnonymousCredentials(), _http=session)
    raw_intent = canonical(intent.model_dump(mode="json")).encode()
    metadata = json.dumps({"name": "synthetic", "bucket": "synthetic-authority-journal",
                           "generation": "41", "size": str(len(raw_intent))}).encode()
    replies = deque([requests.exceptions.Timeout("synthetic upload response loss"),
        response(metadata), response(metadata), response(raw_intent),
        response(b'{"error":{"code":412,"message":"synthetic exists"}}', status=412),
        response(metadata), response(metadata), response(raw_intent)])
    def intercepted_request(*args, **kwargs):
        assert not active_transactions, "GCS IO entered a real Firestore transaction"
        ordering.append("gcs-request")
        result = replies.popleft()
        if isinstance(result, Exception):
            raise result
        return result
    session.request.side_effect = intercepted_request

    class SyntheticFence:
        """No actual UID, witness, IAM or retention verification is provided."""
        def __init__(self):
            self.checks = 0
        def check(self, actual_pin):
            assert actual_pin == pin and not active_transactions
            self.checks += 1
    fence = SyntheticFence()
    coordinator = SafetyCoordinator(emulator.registry,
        GcsJournal(storage.bucket("synthetic-authority-journal"), publication=publication_for(pin, "synthetic-authority-journal")), pin, fence=fence,
        now_ms=lambda: intent.created_at_ms + 1)
    result = coordinator.execute(intent)
    assert result.status == "COMMITTED" and result.journal.generation == "41"
    assert result.journal.sha256 == intent.digest and fence.checks == 2
    assert ordering == ["gcs-request"] * 4 + ["firestore-begin", "firestore-commit"]
    assert len(emulator.sent) == 1 and len(emulator.sent[0][1]) == 4
    receipt = result.journal.model_dump(mode="json")
    hold = emulator.registry.read(intent.effect.kind, intent.effect.record_key)
    event = emulator.registry.read("events", str(intent.operation_id))
    head = emulator.registry.read("head", "global")
    operation = emulator.registry.read("operations", str(intent.operation_id))
    assert hold == {"effect": intent.effect.model_dump(mode="json"),
                    "operation_id": str(intent.operation_id), "journal": receipt}
    assert operation == {"intent_sha256": intent.digest, "journal": receipt}
    assert event["sequence"] == 1 and event["payload"]["journal_generation"] == "41"
    assert head == {"sequence": 1, "digest": event["digest"]}
    replay_start = len(ordering)
    replay = coordinator.execute(intent)
    assert replay == result and fence.checks == 4, sdk_commit_replies
    assert ordering[replay_start:] == ["gcs-request"] * 4 + ["firestore-begin"]
    assert len(emulator.sent) == 1
    assert sdk_commit_replies == [{"write_count": 4, "has_commit_time": True}]
    assert emulator.registry.read("events", str(intent.operation_id)) == event
    assert emulator.registry.read("head", "global") == head
    assert emulator.registry.read(intent.effect.kind, intent.effect.record_key) == hold
    assert emulator.registry.read("operations", str(intent.operation_id)) == operation
    assert not replies and session.request.call_count == 8
    calls = session.request.call_args_list
    methods = [call.args[0] if call.args else call.kwargs["method"] for call in calls]
    urls = [call.args[1] if len(call.args) > 1 else call.kwargs["url"] for call in calls]
    queries = [parse_qs(urlsplit(url).query) for url in urls]
    assert methods == ["POST", "GET", "GET", "GET"] * 2
    for offset in (0, 4):
        assert queries[offset]["ifGenerationMatch"] == ["0"]
        assert raw_intent in calls[offset].kwargs["data"]
        assert "generation" not in queries[offset + 1]
        assert queries[offset + 2]["generation"] == ["41"]
        assert queries[offset + 2]["ifGenerationMatch"] == ["41"]
        assert queries[offset + 3]["generation"] == ["41"]
        assert queries[offset + 3]["ifGenerationMatch"] == ["41"]
        assert calls[offset + 3].kwargs["headers"]["range"] == f"bytes=0-{len(raw_intent)}"
    assert all(call.kwargs["timeout"] == 2.0 for call in calls)
