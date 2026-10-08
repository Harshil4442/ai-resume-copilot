"""Local scripted contract faults, explicitly not production GCP/IAM evidence."""
from __future__ import annotations

import hashlib
from collections import deque
from copy import deepcopy
from types import SimpleNamespace
from typing import cast
from uuid import UUID, uuid4

import pytest
from google.api_core.exceptions import Aborted, NotFound, PreconditionFailed
from google.cloud.storage.bucket import Bucket
from pydantic import ValidationError

from app.domains.recovery.contracts import canonical
from app.domains.recovery.gcp_buffer import BufferedRegistry, BufferedTransaction
from app.domains.recovery.gcp_contracts import (
    AmbiguousCommit,
    DenyScope,
    JournalIntent,
    KeyOwnership,
    OpeningHold,
    RegistryPin,
    RpcCommit,
    RpcSnapshot,
    RpcWrite,
    WitnessBody,
    WitnessPin,
)
from app.domains.recovery.gcp_journal import GcsJournal, GcsWitnessFence
from app.domains.recovery.gcp_service import SafetyCoordinator, control_record
from app.domains.recovery.store import GuardDenied, GuardUnavailable, production_store

SUBJECT = UUID("a4451f4a-6431-4541-9a89-3a77019e4218")
OTHER = UUID("f291b46b-2b3b-4c30-bab2-6b596b24bb1e")
NOW = 1_800_000_000_000


class ScriptedRpc:
    database_resource = "projects/hirewiz-local-authority/databases/authority-test"

    def __init__(self) -> None:
        self.data: dict[str, tuple[bytes, int]] = {}
        self.views: dict[bytes, dict[str, tuple[bytes, int]]] = {}
        self.reads: dict[bytes, set[str]] = {}
        self.calls: list[tuple] = []
        self.faults: deque[str] = deque()
        self.pending: tuple[bytes, tuple[RpcWrite, ...]] | None = None
        self.uid = "9012403f-fbb6-4cda-b94f-a9dc7200e928"

    def identity(self) -> dict[str, str]:
        return {"name": self.database_resource, "uid": self.uid, "type": "FIRESTORE_NATIVE"}

    def begin(self) -> bytes:
        key = str(len([call for call in self.calls if call[0] == "begin"]) + 1).encode()
        self.views[key], self.reads[key] = deepcopy(self.data), set()
        self.calls.append(("begin", key))
        return key

    def read(self, transaction_id: bytes, paths: tuple[str, ...]) -> dict[str, RpcSnapshot]:
        self.calls.append(("read", transaction_id, paths))
        self.reads[transaction_id].update(paths)
        return {path: RpcSnapshot(path, *self.views[transaction_id].get(path, (None, None)))
                for path in paths}

    def commit(self, transaction_id: bytes, writes: tuple[RpcWrite, ...]) -> RpcCommit:
        self.calls.append(("commit", transaction_id, writes))
        assert all(write.path in self.reads[transaction_id] for write in writes)
        fault = self.faults.popleft() if self.faults else ""
        if fault == "abort":
            raise Aborted("definite scripted abort")
        if fault == "pending":
            self.pending = transaction_id, writes
            raise AmbiguousCommit("scripted pending commit")
        self.apply(transaction_id, writes)
        if fault == "reply_loss":
            raise AmbiguousCommit("scripted reply loss after atomic commit")
        return RpcCommit(len(writes))

    def apply(self, transaction_id: bytes, writes: tuple[RpcWrite, ...]) -> None:
        for write in writes:
            existing = self.data.get(write.path)
            if (existing[1] if existing else None) != write.version:
                raise Aborted("scripted version conflict")
        self.data.update({write.path: (write.raw, (cast(int, write.version) if write.version else 0) + 1)
                          for write in writes})
        self.views.pop(transaction_id, None)

    def rollback(self, transaction_id: bytes) -> None:
        self.calls.append(("rollback", transaction_id))
        self.views.pop(transaction_id, None)

    def seed(self, namespace: str, key: str, value: dict) -> None:
        path = BufferedTransaction(self, b"seed").path(namespace, key)
        self.data[path] = (canonical({"namespace": namespace, "key": key, "value": value}).encode(), 1)


class ScriptedBucket:
    name = "synthetic-authority-journal"

    def __init__(self) -> None:
        self.live: dict[str, int] = {}
        self.versions: dict[tuple[str, int], bytes] = {}
        self.calls: list[tuple] = []
        self.fault: str | None = None
        self.active_rpc: ScriptedRpc | None = None
        self.client = SimpleNamespace(_http=object())

    def blob(self, path: str, generation: int | None = None) -> ScriptedBlob:
        return ScriptedBlob(self, path, generation)

    def seed(self, path: str, raw: bytes) -> int:
        generation = self.live.get(path, 40) + 1
        self.live[path], self.versions[path, generation] = generation, raw
        return generation


class ScriptedBlob:
    def __init__(self, bucket: ScriptedBucket, path: str, generation: int | None) -> None:
        self.bucket, self.path, self.generation = bucket, path, generation
        self._properties: dict = {}

    def _record(self, kind: str, kwargs: dict) -> None:
        # GCS must never run while the coordinator's Firestore transaction is live.
        if self.bucket.active_rpc:
            assert not self.bucket.active_rpc.views
        assert kwargs["retry"] is None and 0 < kwargs["timeout"] <= 10
        self.bucket.calls.append((kind, self.path, self.generation, kwargs))

    def upload_from_string(self, raw: bytes, **kwargs) -> None:
        self._record("upload", kwargs)
        assert kwargs["if_generation_match"] == 0 and kwargs["checksum"] is None
        if self.path in self.bucket.live:
            raise PreconditionFailed("synthetic existing object")
        if self.bucket.fault == "missing":
            raise TimeoutError("synthetic unconfirmed upload without stored object")
        self.bucket.seed(self.path, b"corrupt" if self.bucket.fault == "corrupt" else raw)
        if self.bucket.fault == "timeout":
            raise TimeoutError("synthetic stored upload then response loss")

    def reload(self, **kwargs) -> None:
        self._record("reload", kwargs)
        if self.path not in self.bucket.live:
            raise NotFound("synthetic missing object")
        if self.generation is None:
            self.generation = self.bucket.live[self.path]
        self._properties = {"generation": str(self.generation),
                            "size": str(len(self.bucket.versions[self.path, self.generation]))}

    def download_to_file(self, sink, **kwargs) -> None:
        self._record("download", kwargs)
        assert self.generation is not None
        assert kwargs["if_generation_match"] == self.generation and kwargs["raw_download"] is True
        assert kwargs["start"] == 0 and kwargs["single_shot_download"] is False
        sink.write(self.bucket.versions[self.path, self.generation])


class FixtureFence:
    def __init__(self) -> None:
        self.calls = 0
        self.close_on: int | None = None

    def check(self, pin: RegistryPin) -> None:
        self.calls += 1
        if self.close_on and self.calls >= self.close_on:
            raise GuardUnavailable("synthetic witness closed")


def pin() -> RegistryPin:
    return RegistryPin(database=ScriptedRpc.database_resource,
        database_uid="9012403f-fbb6-4cda-b94f-a9dc7200e928",
        authority_id="ad4d3c65-f9f4-4dd2-a55b-78bdbd151025", incarnation=1,
        epoch_id="571d949d-1a23-4f10-ad39-4eff638ccf04")


def intent(**changes) -> JournalIntent:
    values = {"operation_id": uuid4(), "pin": pin(), "effect": OpeningHold(
        subject_uuid=SUBJECT, employer_key="a" * 64, tenant_id="synthetic-tenant",
        opening_key="b" * 64, binding_sha256="c" * 64),
        "created_at_ms": NOW, "deadline_ms": NOW + 10_000}
    return JournalIntent.model_validate({**values, **changes})


def setup() -> tuple[SafetyCoordinator, ScriptedRpc, ScriptedBucket, FixtureFence]:
    rpc, bucket, fence = ScriptedRpc(), ScriptedBucket(), FixtureFence()
    rpc.seed("control", "meta", control_record(pin()))
    rpc.seed("head", "global", {"sequence": 0, "digest": "0" * 64})
    bucket.active_rpc = rpc
    service = SafetyCoordinator(BufferedRegistry(rpc), GcsJournal(cast(Bucket, bucket)), pin(),
                                fence=fence, now_ms=lambda: NOW + 1)
    return service, rpc, bucket, fence


def count(rpc: ScriptedRpc, kind: str) -> int:
    return sum(call[0] == kind for call in rpc.calls)


def test_all_reads_precede_atomic_commit_and_copies_cannot_mutate_staged_or_original() -> None:
    rpc = ScriptedRpc()
    rpc.seed("records", "one", {"nested": {"value": 1}})
    def stage(tx):
        result = tx.get("records", "one")
        result["nested"]["value"] = 9
        assert tx.get("records", "one") == {"nested": {"value": 1}}
        proposed = {"nested": {"value": 2}}
        tx.put("records", "two", proposed, immutable=True)
        proposed["nested"]["value"] = 3
        assert tx.get("records", "two") == {"nested": {"value": 2}}
        tx.put("records", "three", {"value": 4})
        assert count(rpc, "commit") == 0
    assert BufferedRegistry(rpc).run(stage) is None
    assert [call[0] for call in rpc.calls] == ["begin", "read", "read", "read", "commit"]
    assert len(rpc.calls[-1][2]) == 2


def test_immutable_values_use_canonical_types_not_python_true_equals_one() -> None:
    rpc = ScriptedRpc()
    rpc.seed("records", "fixed", {"value": True})
    with pytest.raises(GuardDenied, match="Immutable"):
        BufferedRegistry(rpc).run(lambda tx: tx.put("records", "fixed", {"value": 1}, immutable=True))
    assert count(rpc, "commit") == 0


@pytest.mark.parametrize("namespace", ["opening_hold", "key_ownership", "deny", "operations"])
def test_permanent_namespaces_cannot_be_rewritten_by_omitting_immutable_flag(namespace: str) -> None:
    rpc = ScriptedRpc()
    rpc.seed(namespace, "permanent", {"owner": str(SUBJECT)})
    with pytest.raises(GuardDenied, match="Immutable"):
        BufferedRegistry(rpc).run(lambda tx: tx.put(namespace, "permanent", {"owner": str(OTHER)}))
    assert count(rpc, "commit") == 0


@pytest.mark.parametrize("namespace", ["control", "clock", "events", "head"])
def test_runtime_cannot_repair_control_or_write_legacy_clock(namespace: str) -> None:
    rpc = ScriptedRpc()
    with pytest.raises(GuardDenied):
        BufferedRegistry(rpc).run(lambda tx: tx.put(namespace, "meta", {"state": "OPEN"}))
    assert count(rpc, "commit") == 0


def test_missing_head_does_not_bootstrap_and_no_partial_effect_is_committed() -> None:
    service, rpc, _, _ = setup()
    rpc.data.pop(BufferedTransaction(rpc, b"seed").path("head", "global"))
    with pytest.raises(GuardUnavailable, match="head"):
        service.execute(intent())
    assert count(rpc, "commit") == 0
    assert len(rpc.data) == 1


def test_definite_abort_replays_fresh_reads_but_stable_intent_and_one_event() -> None:
    service, rpc, bucket, _ = setup()
    rpc.faults.append("abort")
    operation = intent()
    result = service.execute(operation)
    assert result.status == "COMMITTED"
    assert count(rpc, "begin") == count(rpc, "commit") == 2
    assert sum(call[0] == "upload" for call in bucket.calls) == 1
    assert service.registry.read("head", "global")["sequence"] == 1
    assert service.registry.read("operations", str(operation.operation_id))["intent_sha256"] == operation.digest


def test_abort_budget_is_bounded_and_does_not_release_prepared_journal_intent() -> None:
    service, rpc, bucket, _ = setup()
    rpc.faults.extend(["abort"] * 4)
    with pytest.raises(GuardUnavailable, match="budget"):
        service.execute(intent())
    assert count(rpc, "begin") == count(rpc, "commit") == 3
    assert len(bucket.live) == 1 and len(rpc.data) == 2


def test_retry_uses_fresh_time_and_stops_before_late_commit() -> None:
    service, rpc, _, _ = setup()
    rpc.faults.append("abort")
    values = iter([NOW + 1, NOW + 2, NOW + 3, NOW + 20_000])
    service.now_ms = lambda: next(values)
    with pytest.raises(GuardDenied, match="lifetime"):
        service.execute(intent())
    assert count(rpc, "commit") == 1


def test_buffer_retry_deadline_is_checked_before_each_new_read() -> None:
    rpc = ScriptedRpc()
    times = iter([0.0, 0.1, 0.2, 6.0])
    registry = BufferedRegistry(rpc, monotonic=lambda: next(times))
    with pytest.raises(GuardUnavailable, match="deadline"):
        registry.run(lambda tx: (tx.get("records", "one"), tx.get("records", "two")))
    assert count(rpc, "read") == 1 and count(rpc, "commit") == 0


def test_definite_begin_abort_restarts_before_allocating_any_callback_state() -> None:
    class BeginAbort(ScriptedRpc):
        def __init__(self):
            super().__init__()
            self.starts = 0

        def begin(self):
            self.starts += 1
            if self.starts == 1:
                raise Aborted("synthetic definite begin abort")
            return super().begin()
    rpc = BeginAbort()
    invoked = []
    def stage(tx):
        invoked.append(1)
        tx.put("records", "stable", {"held": True}, immutable=True)
    BufferedRegistry(rpc).run(stage)
    assert rpc.starts == 2 and invoked == [1] and count(rpc, "commit") == 1


def test_status_read_abort_is_unknown_without_retry_or_mutating_repair() -> None:
    service, rpc, _, _ = setup()
    operation = intent()
    service.execute(operation)
    def aborted_read(transaction_id, paths):
        raise Aborted("synthetic status-only contention")
    rpc.read = aborted_read
    assert service.status(operation).status == "UNKNOWN"
    assert count(rpc, "commit") == 1 and count(rpc, "rollback") == 1


def test_global_head_exhaustion_does_not_reset_counter_or_commit_a_hold() -> None:
    service, rpc, _, _ = setup()
    rpc.seed("head", "global", {"sequence": 2**53 - 1, "digest": "0" * 64})
    with pytest.raises(GuardUnavailable, match="sequence budget"):
        service.execute(intent())
    assert count(rpc, "commit") == 0


@pytest.mark.parametrize("fault", ["reply_loss", "pending"])
def test_commit_ambiguity_returns_only_unknown_and_never_retries(fault: str) -> None:
    service, rpc, _, _ = setup()
    rpc.faults.append(fault)
    operation = intent()
    result = service.execute(operation)
    assert result.status == "UNKNOWN" and result.journal is None
    assert set(result.model_dump()) == {"status", "operation_id", "intent_sha256", "journal"}
    assert count(rpc, "begin") == count(rpc, "commit") == 1
    assert count(rpc, "rollback") == 0
    if fault == "pending":
        # Even a strongly consistent missing receipt cannot prove pending abort.
        assert service.status(operation).status == "UNKNOWN"
        assert count(rpc, "commit") == 1
        pending = rpc.pending
        assert pending is not None
        rpc.apply(*pending)
    assert service.status(operation).status == "COMMITTED"
    assert count(rpc, "commit") == 1


def test_exact_replay_is_status_only_no_new_event_or_effect() -> None:
    service, rpc, _, _ = setup()
    operation = intent()
    first, second = service.execute(operation), service.execute(operation)
    assert first == second and first.status == "COMMITTED"
    assert rpc.calls[-1][0] == "rollback" and count(rpc, "commit") == 1
    assert service.registry.read("head", "global")["sequence"] == 1


def test_changed_stable_operation_intent_is_rejected_by_exact_journal_bytes() -> None:
    service, rpc, _, _ = setup()
    operation = intent()
    service.execute(operation)
    changed = operation.model_copy(update={"deadline_ms": NOW + 9_000})
    before = count(rpc, "begin")
    with pytest.raises(GuardUnavailable, match="bytes"):
        service.execute(changed)
    assert count(rpc, "begin") == before


def test_opening_hold_cannot_be_replaced_by_fresh_binding_operation_or_epoch() -> None:
    service, rpc, bucket, fence = setup()
    first = intent()
    service.execute(first)
    new_pin = pin().model_copy(update={"incarnation": 2, "epoch_id": uuid4()})
    rpc.seed("control", "meta", control_record(new_pin))
    current = SafetyCoordinator(service.registry, GcsJournal(cast(Bucket, bucket)), new_pin,
                                fence=fence, now_ms=lambda: NOW + 1)
    changed = intent(pin=new_pin, effect=first.effect.model_copy(update={"binding_sha256": "f" * 64}))
    assert changed.effect.record_key == first.effect.record_key
    with pytest.raises(GuardDenied, match="Immutable"):
        current.execute(changed)
    assert current.registry.read("opening_hold", first.effect.record_key)["operation_id"] == str(first.operation_id)


def test_key_ownership_cannot_move_to_another_subject_or_device() -> None:
    service, _, _, _ = setup()
    owned = intent(effect=KeyOwnership(subject_uuid=SUBJECT, device_id="one", key_sha256="a" * 64))
    service.execute(owned)
    with pytest.raises(GuardDenied, match="Immutable"):
        service.execute(intent(effect=KeyOwnership(subject_uuid=OTHER, device_id="two", key_sha256="a" * 64)))
    assert service.registry.read("key_ownership", "a" * 64)["effect"]["subject_uuid"] == str(SUBJECT)


def test_revocation_retains_unknown_opening_hold_and_tombstone_beyond_intent_expiry() -> None:
    service, rpc, _, _ = setup()
    rpc.faults.append("reply_loss")
    hold = intent()
    assert service.execute(hold).status == "UNKNOWN"
    revoke = intent(effect=DenyScope(subject_uuid=SUBJECT, scope="device", target="one", revision="1"))
    assert service.execute(revoke).status == "COMMITTED"
    service.now_ms = lambda: NOW + 900_000
    assert service.status(revoke).status == "COMMITTED"
    assert service.registry.read("opening_hold", hold.effect.record_key) is not None
    assert service.registry.read("deny", revoke.effect.record_key) is not None
    assert count(rpc, "commit") == 2


def test_changed_witness_after_confirmed_commit_suppresses_success() -> None:
    service, rpc, _, fence = setup()
    fence.close_on = 2
    operation = intent()
    assert service.execute(operation).status == "UNKNOWN"
    assert count(rpc, "commit") == 1
    assert service.status(operation).status == "UNKNOWN"
    assert service.registry.read("operations", str(operation.operation_id)) is not None


@pytest.mark.parametrize("incarnation", [True, 1.0])
def test_stored_control_types_cannot_alias_the_pinned_integer_incarnation(incarnation: object) -> None:
    service, rpc, _, _ = setup()
    operation = intent()
    service.execute(operation)
    changed = control_record(pin())
    changed["incarnation"] = incarnation
    rpc.seed("control", "meta", changed)
    with pytest.raises(GuardUnavailable, match="control"):
        service.execute(intent())
    assert service.status(operation).status == "UNKNOWN"


@pytest.mark.parametrize("fault", [None, "timeout"])
def test_journal_upload_and_412_response_reconciliation_verify_generation_bytes(fault: str | None) -> None:
    bucket = ScriptedBucket()
    bucket.fault = fault
    journal = GcsJournal(cast(Bucket, bucket))
    operation = intent()
    first, second = journal.write(operation), journal.write(operation)
    assert first == second and first.generation == "41" and first.sha256 == operation.digest
    assert [call[0] for call in bucket.calls] == ["upload", "reload", "reload", "download"] * 2
    assert all(call[2] == 41 for call in bucket.calls if call[0] == "download")


@pytest.mark.parametrize("fault", ["missing", "corrupt"])
def test_unverifiable_journal_never_starts_firestore_transaction(fault: str) -> None:
    service, rpc, bucket, _ = setup()
    bucket.fault = fault
    with pytest.raises(GuardUnavailable):
        service.execute(intent())
    assert count(rpc, "begin") == 0


def witness() -> tuple[GcsWitnessFence, ScriptedBucket, ScriptedRpc, WitnessPin]:
    rpc, bucket = ScriptedRpc(), ScriptedBucket()
    body = WitnessBody(registry=pin(), state="OPEN", manifest_generation="123",
                       manifest_sha256="d" * 64, partitions_sha256="e" * 64)
    raw = canonical(body.model_dump(mode="json")).encode()
    generation = bucket.seed("witness/current", raw)
    config = WitnessPin(registry=pin(), bucket=bucket.name, path="witness/current",
                        generation=str(generation), sha256=hashlib.sha256(raw).hexdigest())
    return GcsWitnessFence(cast(Bucket, bucket), rpc, config, body), bucket, rpc, config


def test_live_witness_version_is_checked_even_if_old_open_version_remains_readable() -> None:
    fence, bucket, _, config = witness()
    fence.check(pin())
    assert (config.path, int(config.generation)) in bucket.versions
    bucket.seed(config.path, b"closed-current-witness")
    with pytest.raises(GuardUnavailable, match="generation"):
        fence.check(pin())
    # Old body still exists; reading only that generation would have been unsafe.
    assert (config.path, int(config.generation)) in bucket.versions


def test_restored_database_uid_is_rejected_before_witness_download() -> None:
    fence, bucket, rpc, _ = witness()
    rpc.uid = str(uuid4())
    with pytest.raises(GuardUnavailable, match="incarnation"):
        fence.check(pin())
    assert bucket.calls == []


def test_status_revalidates_exact_generation_and_never_repairs_missing_object() -> None:
    service, rpc, bucket, _ = setup()
    operation = intent()
    result = service.execute(operation)
    assert result.journal is not None
    bucket.live.pop(result.journal.path)
    before = count(rpc, "commit")
    assert service.status(operation).status == "UNKNOWN"
    assert count(rpc, "commit") == before
    assert sum(call[0] == "upload" for call in bucket.calls) == 1


@pytest.mark.parametrize("missing", ["opening_hold", "events", "head"])
def test_partial_projection_cannot_report_committed_or_repair_from_receipt(missing: str) -> None:
    service, rpc, _, _ = setup()
    operation = intent()
    service.execute(operation)
    key = (operation.effect.record_key if missing == "opening_hold" else
           str(operation.operation_id) if missing == "events" else "global")
    path = BufferedTransaction(rpc, b"seed").path(missing, key)
    rpc.data.pop(path)
    before = count(rpc, "commit")
    assert service.status(operation).status == "UNKNOWN"
    with pytest.raises(GuardUnavailable, match="effect|event|head"):
        service.execute(operation)
    assert count(rpc, "commit") == before and path not in rpc.data


def test_missing_operation_receipt_is_not_reconstructed_from_surviving_effect_and_event() -> None:
    service, rpc, _, _ = setup()
    operation = intent()
    service.execute(operation)
    path = BufferedTransaction(rpc, b"seed").path("operations", str(operation.operation_id))
    rpc.data.pop(path)
    before = count(rpc, "commit")
    assert service.status(operation).status == "UNKNOWN"
    with pytest.raises(GuardUnavailable, match="closed recovery"):
        service.execute(operation)
    assert count(rpc, "commit") == before and path not in rpc.data


def test_expiry_during_reads_is_rechecked_before_any_commit() -> None:
    service, rpc, _, _ = setup()
    values = iter([NOW + 1, NOW + 2, NOW + 20_000])
    service.now_ms = lambda: next(values)
    with pytest.raises(GuardDenied, match="lifetime"):
        service.execute(intent())
    assert count(rpc, "commit") == 0


@pytest.mark.parametrize("timeout", [True, float("inf"), float("nan"), 0, -1, 11])
def test_journal_rpc_budget_rejects_boolean_nonfinite_and_out_of_range(timeout: float) -> None:
    with pytest.raises(ValueError):
        GcsJournal(cast(Bucket, ScriptedBucket()), rpc_timeout=timeout)


def test_default_fence_and_production_factory_are_unavailable_without_any_io() -> None:
    _, rpc, bucket, _ = setup()
    service = SafetyCoordinator(BufferedRegistry(rpc), GcsJournal(cast(Bucket, bucket)), pin(),
                                now_ms=lambda: NOW + 1)
    with pytest.raises(GuardUnavailable, match="configuration"):
        service.execute(intent())
    assert rpc.calls == [] and bucket.calls == []
    with pytest.raises(GuardUnavailable, match="not implemented"):
        production_store().transact(lambda tx: tx.put("unsafe", "one", {}))


@pytest.mark.parametrize("revision", ["0", "01", "-1", "1e5", 1, True, "1" * 32, str(2**63), str(2**64)])
def test_generation_contract_rejects_noncanonical_revisions(revision: object) -> None:
    with pytest.raises(ValidationError):
        DenyScope(subject_uuid=SUBJECT, scope="artifact", target="one", revision=revision)


def test_int64_generation_above_javascript_safe_integer_remains_exact_string() -> None:
    value = str(2**63 - 1)
    assert DenyScope(subject_uuid=SUBJECT, scope="artifact", target="one", revision=value).revision == value


@pytest.mark.parametrize("changes", [
    {"deadline_ms": NOW}, {"deadline_ms": NOW + 300_001}, {"created_at_ms": True},
    {"extra": "secret"},
])
def test_intent_lifetime_and_extra_data_are_rejected(changes: dict) -> None:
    with pytest.raises(ValidationError):
        intent(**changes)
