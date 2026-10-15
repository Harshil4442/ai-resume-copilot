"""Disabled V3 native lanes/registration/full-row cut; no product or ADC factory."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from uuid import UUID, uuid4

from google.api_core.exceptions import NotFound
from google.cloud.storage import Bucket

from .contracts import canonical, fingerprint
from .gcp_buffer import BufferedRegistry, BufferedTransaction
from .gcp_contracts import AmbiguousCommit
from .gcp_journal import _exact_bytes
from .gcp_pairing_attempts import GcsPairingAttempts
from .gcp_partitioned_contracts import (
    DirectoryHead,
    LaneHead,
    PartitionedCut,
    PartitionedRecord,
    PartitionedReference,
    PartitionedResource,
    PartitionedRoot,
    ScopedJournalIntent,
    SegmentRegistration,
    SegmentState,
)
from .gcp_publication import record
from .gcp_publication_scan import NativeAuthorityScanner, ScannedAuthorityRecord
from .gcp_rpc import FirestoreRpc
from .store import GuardDenied, GuardUnavailable


def _copy(value: dict) -> dict:
    return json.loads(canonical(value))


def partitioned_intent(raw: bytes, resource: PartitionedResource) -> tuple[PartitionedReference, dict]:
    if type(raw) is not bytes or not 0 < len(raw) <= 65_536:
        raise GuardDenied("Exact bounded canonical publication bytes are required")
    try:
        legacy = record(raw, resource.pin.authority)
        return PartitionedReference.model_validate(legacy.reference.model_dump(mode="json")), legacy.intent
    except GuardDenied:
        try:
            intent = ScopedJournalIntent.model_validate_json(raw)
            if intent.pin != resource.pin.authority.target or canonical(intent.model_dump(mode="json")).encode() != raw:
                raise ValueError("Scoped canonical intent/pin")
            authority = hashlib.sha256(str(intent.pin.authority_id).encode()).hexdigest()
            ref = PartitionedReference(operation_id=intent.operation_id, sha256=hashlib.sha256(raw).hexdigest(),
                partition=intent.partition, path=f"authority-intents/{authority}/{intent.partition}/{intent.operation_id}.json",
                byte_size=len(raw), kind="scoped_admission")
            return ref, intent.model_dump(mode="json")
        except Exception:
            raise GuardDenied("Full canonical typed V3 publication intent is required") from None


@dataclass(frozen=True)
class ClosedPartitionedRoot:
    _raw: bytes
    _generation: str
    _issuer: object

    @property
    def root(self) -> PartitionedRoot:
        return PartitionedRoot.model_validate_json(self._raw)


class PartitionedPublicationCoordinator:
    def __init__(self, registry: BufferedRegistry, resource: PartitionedResource,
                 witness_bucket: Bucket):
        if (type(registry.rpc) is not FirestoreRpc
                or registry.rpc.database_resource != resource.pin.authority.registry.database
                or witness_bucket.name != resource.bucket):
            raise GuardDenied("Actual independently pinned V3 native and witness resources required")
        self.registry, self.bucket = registry, witness_bucket
        self._resource = canonical(resource.model_dump(mode="json")).encode()
        self._issuer = object()
        self._closed: dict[UUID, bytes] = {}

    @property
    def resource(self) -> PartitionedResource:
        return PartitionedResource.model_validate_json(self._resource)

    def _fresh(self) -> None:
        try:
            resource = self.resource
            rpc = self.registry.rpc
            assert isinstance(rpc, FirestoreRpc)
            native = resource.pin.authority.registry
            if rpc.identity() != {"name": native.database, "uid": str(native.database_uid), "type": "FIRESTORE_NATIVE"}:
                raise ValueError("V3 immutable database identity changed")
            _exact_bytes(self.bucket, resource.path, canonical(resource.pin.model_dump(mode="json")).encode(),
                generation=resource.generation, timeout=2.0)
        except Exception:
            raise GuardUnavailable("Fresh protected V3 identity/resource is unavailable") from None

    def _root(self, tx: BufferedTransaction, *, opened: bool) -> PartitionedRoot:
        try:
            value = PartitionedRoot.model_validate(tx.get("v3_root", "current"))
            if value.pin != self.resource.pin or (opened and value.phase != "OPEN"):
                raise ValueError("V3 root pin/admission")
            return value
        except Exception:
            raise GuardUnavailable("Exact current V3 root admission is unavailable") from None

    def _fresh_open(self) -> None:
        self._fresh()
        try:
            self.bucket.blob(self.resource.close_path).reload(retry=None, timeout=2.0)
        except NotFound:
            return
        except Exception:
            raise GuardUnavailable("Protected V3 close absence cannot be established") from None
        raise GuardUnavailable("Protected V3 emergency closure denies new admission")

    def _lane(self, operation: UUID) -> int:
        return int(hashlib.sha256(str(operation).encode()).hexdigest(), 16) % self.resource.pin.lanes

    def _append(self, tx: BufferedTransaction, raw: bytes) -> PartitionedRecord:
        self._root(tx, opened=True)
        reference, intent = partitioned_intent(raw, self.resource)
        key = str(reference.operation_id)
        if tx.get("v3_records", key) is not None:
            raise GuardUnavailable("V3 retained original publication cannot be adopted")
        lane_id = self._lane(reference.operation_id)
        lane = LaneHead.model_validate(tx.get("v3_lanes", str(lane_id)))
        if lane.lane != lane_id:
            raise GuardUnavailable("Derived lane and retained lane pin disagree")
        state = (SegmentState.model_validate(tx.get("v3_segments", lane.segment_id))
                 if lane.segment_id is not None else None)
        if state is not None and (state.phase != "OPEN" or state.registration.lane != lane_id):
            raise GuardUnavailable("Derived segment registration is unavailable")
        if state is None or state.count == self.resource.pin.segment_size:
            segment_id = fingerprint({"pin": self.resource.pin.model_dump(mode="json"),
                "lane": lane_id, "lane_ordinal": lane.segment_count + 1})
            shard = int(segment_id, 16) % self.resource.pin.directory_shards
            directory = DirectoryHead.model_validate(tx.get("v3_directory", str(shard)))
            if directory.shard != shard or tx.get("v3_segments", segment_id) is not None:
                raise GuardUnavailable("V3 exact fresh registration census is unavailable")
            fields = {"segment_id": segment_id, "lane": lane_id,
                "lane_ordinal": lane.segment_count + 1, "directory_shard": shard,
                "directory_ordinal": directory.count + 1, "previous": directory.digest}
            registration = SegmentRegistration.model_validate({**fields, "digest": fingerprint(fields)})
            state = SegmentState(registration=registration, phase="OPEN", count=0, digest="0" * 64)
            tx.put("v3_registration_slots", f"{shard}:{registration.directory_ordinal}",
                registration.model_dump(mode="json"), immutable=True)
            tx.put("v3_directory", str(shard), DirectoryHead(shard=shard,
                count=registration.directory_ordinal, digest=registration.digest).model_dump(mode="json"))
            lane = LaneHead(lane=lane_id, segment_count=registration.lane_ordinal, segment_id=segment_id)
            tx.put("v3_lanes", str(lane_id), lane.model_dump(mode="json"))
        fields = {"reference": reference.model_dump(mode="json"),
                  "segment_id": state.registration.segment_id, "ordinal": state.count + 1,
                  "previous": state.digest}
        full = PartitionedRecord.model_validate({**fields, "chain_digest": fingerprint(fields), "intent": intent})
        tx.put("v3_records", key, full.model_dump(mode="json"), immutable=True)
        tx.put("v3_record_slots", f"{full.segment_id}:{full.ordinal}",
            {"operation_id": key, "record_sha256": fingerprint(full.model_dump(mode="json"))}, immutable=True)
        updated = SegmentState(registration=state.registration, phase="OPEN", count=full.ordinal, digest=full.chain_digest)
        tx.put("v3_segments", full.segment_id, updated.model_dump(mode="json"))
        return full

    def retain(self, raw: bytes, bucket: str) -> None:
        if bucket != self.resource.bucket:
            raise GuardDenied("V3 publication journal bucket changed")
        fixed = bytes(raw)
        output: list[PartitionedRecord] = []
        def publish(tx: BufferedTransaction) -> None:
            output[:] = [self._append(tx, fixed)]
        try:
            self.registry.run(publish, before_attempt=self._fresh_open)
        except AmbiguousCommit:
            raise GuardUnavailable("V3 publication Commit unknown; no upload owner") from None
        self._fresh_open()
        expected = output[0]
        def still_open(tx: BufferedTransaction) -> None:
            self._root(tx, opened=True)
        self.registry.run(still_open, before_attempt=self._fresh_open)
        stored = self.registry.read("v3_records", str(expected.reference.operation_id))
        if stored is None or canonical(stored) != canonical(expected.model_dump(mode="json")):
            raise GuardUnavailable("Own V3 publication acknowledgement lost exact full record")
        self._fresh_open()

    def close(self) -> ClosedPartitionedRoot | None:
        close_id = uuid4()
        expected = PartitionedRoot(pin=self.resource.pin, phase="CLOSED", close_id=close_id)
        self._fresh_open()
        raw = canonical(expected.model_dump(mode="json")).encode()
        generation = GcsPairingAttempts(self.bucket)._fresh_create(self.resource.close_path, raw)
        if generation is None:
            return None
        _exact_bytes(self.bucket, self.resource.close_path, raw, generation=generation, timeout=2.0)
        def shut(tx: BufferedTransaction) -> None:
            self._root(tx, opened=True)
            tx.put("v3_root", "current", expected.model_dump(mode="json"))
        try:
            self.registry.run(shut, before_attempt=self._fresh)
        except AmbiguousCommit:
            return None
        self._fresh()
        current = self.registry.read("v3_root", "current")
        if current is None or canonical(current) != canonical(expected.model_dump(mode="json")):
            raise GuardUnavailable("V3 root close lacks its own exact native acknowledgement")
        self._closed[close_id] = raw
        return ClosedPartitionedRoot(raw, generation, self._issuer)

    def _closed_root(self, cap: ClosedPartitionedRoot) -> None:
        if (type(cap) is not ClosedPartitionedRoot or cap._issuer is not self._issuer
                or cap.root.close_id is None or self._closed.get(cap.root.close_id) != cap._raw):
            raise GuardDenied("An original acknowledged V3 root close is required")
        self._fresh()
        _exact_bytes(self.bucket, self.resource.close_path, cap._raw, generation=cap._generation, timeout=2.0)
        current = self.registry.read("v3_root", "current")
        if current is None or canonical(current).encode() != cap._raw:
            raise GuardUnavailable("V3 closed root was restored or changed")

    def _census(self, cap: ClosedPartitionedRoot, *, max_documents: int) -> tuple[
            tuple[DirectoryHead, ...], tuple[SegmentState, ...], tuple[PartitionedRecord, ...], tuple[ScannedAuthorityRecord, ...]]:
        from .gcp_scoped_census import SCOPED_NAMESPACES, validate_scoped_rows
        self._closed_root(cap)
        rpc = self.registry.rpc
        assert isinstance(rpc, FirestoreRpc)
        rows = NativeAuthorityScanner(rpc).scan(max_documents=max_documents)
        values: dict[tuple[str, str], dict] = {}
        allowed = {"v3_root", "v3_lanes", "v3_directory", "v3_registration_slots",
            "v3_segments", "v3_records", "v3_record_slots"} | SCOPED_NAMESPACES
        for row in rows:
            entry = row.envelope
            key = entry["namespace"], entry["key"]
            if key[0] not in allowed or key in values:
                raise GuardUnavailable("V3 census contains unsupported or duplicate authority rows")
            values[key] = entry["value"]
        root_row = values.get(("v3_root", "current"))
        if root_row is None or canonical(root_row).encode() != cap._raw:
            raise GuardUnavailable("V3 complete census root changed")
        config = self.resource.pin
        directories = tuple(DirectoryHead.model_validate(values.get(("v3_directory", str(s))))
                            for s in range(config.directory_shards))
        lanes = tuple(LaneHead.model_validate(values.get(("v3_lanes", str(lane_id)))) for lane_id in range(config.lanes))
        segments = tuple(SegmentState.model_validate(v) for (n, _), v in values.items() if n == "v3_segments")
        records = tuple(PartitionedRecord.model_validate(v) for (n, _), v in values.items() if n == "v3_records")
        registrations = {s.registration.segment_id: s.registration for s in segments}
        if (len(registrations) != len(segments) or len(segments) != sum(h.count for h in directories)
                or len(segments) != sum(lane.segment_count for lane in lanes)
                or len(records) != sum(s.count for s in segments)):
            raise GuardUnavailable("Full retained V3 rows disagree with declared census cardinality")
        consumed = {("v3_root", "current")}
        for h in directories:
            consumed.add(("v3_directory", str(h.shard)))
            ordered = sorted((r for r in registrations.values() if r.directory_shard == h.shard),
                             key=lambda r: r.directory_ordinal)
            previous = "0" * 64
            for ordinal, r in enumerate(ordered, 1):
                identity = "v3_registration_slots", f"{h.shard}:{ordinal}"
                if r.directory_ordinal != ordinal or r.previous != previous or values.get(identity) != r.model_dump(mode="json"):
                    raise GuardUnavailable("Complete V3 registration slot chain is missing or corrupt")
                consumed.add(identity)
                previous = r.digest
            if h.count != len(ordered) or h.digest != previous:
                raise GuardUnavailable("V3 directory head disagrees with full retained registration history")
        for lane in lanes:
            consumed.add(("v3_lanes", str(lane.lane)))
            ordered = sorted((r for r in registrations.values() if r.lane == lane.lane), key=lambda r: r.lane_ordinal)
            if ([r.lane_ordinal for r in ordered] != list(range(1, lane.segment_count + 1))
                    or (ordered[-1].segment_id if ordered else None) != lane.segment_id):
                raise GuardUnavailable("V3 lane registration census has a gap or rollback")
        for segment in segments:
            r = segment.registration
            consumed.add(("v3_segments", r.segment_id))
            ordered_records = sorted((v for v in records if v.segment_id == r.segment_id), key=lambda v: v.ordinal)
            previous = "0" * 64
            for ordinal, full in enumerate(ordered_records, 1):
                reference, _ = partitioned_intent(canonical(full.intent).encode(), self.resource)
                operation_key = str(full.reference.operation_id)
                slot = "v3_record_slots", f"{r.segment_id}:{ordinal}"
                if (full.ordinal != ordinal or full.previous != previous or self._lane(full.reference.operation_id) != r.lane
                        or full.reference != reference or values.get(slot) != {"operation_id": operation_key,
                            "record_sha256": fingerprint(full.model_dump(mode="json"))}):
                    raise GuardUnavailable("Complete V3 canonical full-record slot chain disagrees")
                consumed.update({("v3_records", operation_key), slot})
                previous = full.chain_digest
            if segment.count != len(ordered_records) or segment.count > config.segment_size or segment.digest != previous:
                raise GuardUnavailable("V3 segment cut omits retained full publication history")
        consumed.update(validate_scoped_rows(values))
        if consumed != set(values):
            raise GuardUnavailable("V3 full flat census contains unregistered retained history")
        self._closed_root(cap)
        return directories, tuple(sorted(segments, key=lambda v: v.registration.segment_id)), tuple(sorted(records, key=lambda v: str(v.reference.operation_id))), rows

    def seal_native(self, cap: ClosedPartitionedRoot, *, max_documents: int = 4096) -> tuple[PartitionedCut, tuple[PartitionedRecord, ...]]:
        _, segments, before, _ = self._census(cap, max_documents=max_documents)
        for state in segments:
            def seal(tx: BufferedTransaction, expected=state) -> None:
                root = self._root(tx, opened=False)
                if canonical(root.model_dump(mode="json")).encode() != cap._raw:
                    raise GuardUnavailable("Exact V3 root closure is required to seal a segment")
                current = SegmentState.model_validate(tx.get("v3_segments", expected.registration.segment_id))
                if current != expected or current.phase != "OPEN":
                    raise GuardUnavailable("V3 segment seal cannot adopt a changed or existing cut")
                closed = SegmentState(registration=current.registration, phase="SEALED", count=current.count, digest=current.digest)
                tx.put("v3_segments", current.registration.segment_id, closed.model_dump(mode="json"))
            try:
                self.registry.run(seal, before_attempt=self._fresh)
            except AmbiguousCommit:
                raise GuardUnavailable("V3 segment seal Commit unknown; no complete cut") from None
        directories, sealed, records, rows = self._census(cap, max_documents=max_documents)
        if records != before or any(s.phase != "SEALED" for s in sealed):
            raise GuardUnavailable("V3 complete publication cut changed during seal")
        cut = PartitionedCut(root=cap.root, directory_heads=directories, segments=sealed,
            records_sha256=fingerprint({"records": [r.model_dump(mode="json") for r in records]}), records_count=len(records),
            authority_rows_count=len(rows), authority_rows_sha256=fingerprint({"authority_rows": [r.envelope for r in rows]}))
        return cut, records
