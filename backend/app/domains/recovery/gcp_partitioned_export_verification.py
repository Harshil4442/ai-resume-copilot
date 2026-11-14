"""Offline integrity verification of an externally pinned closed V3 export."""

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field, ValidationError

from .contracts import Digest, canonical, fingerprint
from .gcp_contracts import Generation
from .gcp_partitioned_contracts import (
    LaneHead,
    PartitionedCompleteReceipt,
    PartitionedContract,
    PartitionedCut,
    PartitionedPin,
    PartitionedRecord,
    PartitionedResource,
    SegmentRegistration,
)
from .gcp_partitioned_publication import partitioned_intent
from .gcp_scoped_census import validate_scoped_rows
from .store import GuardUnavailable

MAX_OBJECT_BYTES = 65_536
MAX_CHUNKS = 128
MAX_ENTRIES = MAX_CHUNKS * 64
ZERO = "0" * 64
BoundedReader = Callable[[str, str, str, int], bytes]
Count = Annotated[int, Field(strict=True, ge=0, le=MAX_ENTRIES)]


class ObjectReference(PartitionedContract):
    path: Annotated[str, Field(min_length=1, max_length=300)]
    generation: Generation
    sha256: Digest
    byte_size: Annotated[int, Field(strict=True, ge=1, le=MAX_OBJECT_BYTES)]


class IntentEntry(ObjectReference):
    kind: Literal["canonical_intent"]
    operation_id: UUID


class RowEntry(ObjectReference):
    kind: Literal["native_authority_row"]
    identity_sha256: Digest


class Chunk(PartitionedContract):
    version: Literal[3]
    pin: PartitionedPin
    close_id: UUID
    ordinal: Annotated[int, Field(strict=True, ge=1, le=MAX_CHUNKS)]
    previous: Digest
    entries: Annotated[list[Annotated[IntentEntry | RowEntry, Field(discriminator="kind")]],
                       Field(min_length=1, max_length=64)]


class Manifest(PartitionedContract):
    version: Literal[3]
    pin: PartitionedPin
    close_id: UUID
    native_cut: ObjectReference
    chunks: Annotated[list[ObjectReference], Field(max_length=MAX_CHUNKS)]
    chunk_chain_sha256: Digest
    records_count: Count
    authority_rows_count: Count
    entries_count: Annotated[int, Field(strict=True, ge=1, le=MAX_ENTRIES)]
    complete_prefix: Literal[True]
    projection_complete: Literal[False]


@dataclass(frozen=True)
class VerifiedExport:
    """Archived content evidence only. No grants, replay target, or OPEN capability."""

    close_id: UUID
    manifest_sha256: str
    records_count: int
    authority_rows_count: int


def _need(condition: bool, reason: str) -> None:
    if not condition:
        raise GuardUnavailable(reason)


def _validate_native_census(cut: PartitionedCut, values: dict[tuple[str, str], dict],
                            records: dict[str, PartitionedRecord]) -> None:
    pin = cut.root.pin
    consumed = {("v3_root", "current"), *(("v3_records", key) for key in records)}
    registrations_by_lane: dict[int, list[SegmentRegistration]] = {lane: [] for lane in range(pin.lanes)}
    registrations_by_shard: dict[int, list[SegmentRegistration]] = {shard: [] for shard in range(pin.directory_shards)}
    records_by_segment: dict[str, list[PartitionedRecord]] = {}
    for record in records.values():
        records_by_segment.setdefault(record.segment_id, []).append(record)

    for segment in cut.segments:
        registration = segment.registration
        segment_id = fingerprint({"pin": pin.model_dump(mode="json"), "lane": registration.lane,
                                  "lane_ordinal": registration.lane_ordinal})
        shard = int(segment_id, 16) % pin.directory_shards
        identity = "v3_segments", segment_id
        _need(registration.lane in registrations_by_lane and registration.segment_id == segment_id
              and registration.directory_shard == shard and identity not in consumed
              and values.get(identity) == segment.model_dump(mode="json"), "Closed export segment identity disagrees")
        consumed.add(identity)
        registrations_by_lane[registration.lane].append(registration)
        registrations_by_shard[shard].append(registration)

        ordered_records = sorted(records_by_segment.pop(segment_id, []), key=lambda value: value.ordinal)
        _need(0 < len(ordered_records) == segment.count <= pin.segment_size, "Closed export segment count disagrees")
        previous = ZERO
        for ordinal, record in enumerate(ordered_records, 1):
            operation_id = str(record.reference.operation_id)
            lane = int(hashlib.sha256(operation_id.encode()).hexdigest(), 16) % pin.lanes
            slot = "v3_record_slots", f"{segment_id}:{ordinal}"
            _need(record.ordinal == ordinal and record.previous == previous and lane == registration.lane
                  and values.get(slot) == {"operation_id": operation_id,
                                           "record_sha256": fingerprint(record.model_dump(mode="json"))},
                  "Closed export record slot chain disagrees")
            consumed.add(slot)
            previous = record.chain_digest
        _need(segment.digest == previous, "Closed export segment chain disagrees")
    _need(not records_by_segment, "Closed export has records outside its segments")

    for lane, registrations in registrations_by_lane.items():
        identity = "v3_lanes", str(lane)
        head = LaneHead.model_validate(values.get(identity))
        ordered = sorted(registrations, key=lambda value: value.lane_ordinal)
        _need(head.lane == lane and head.segment_count == len(ordered)
              and head.segment_id == (ordered[-1].segment_id if ordered else None)
              and all(registration.lane_ordinal == ordinal for ordinal, registration in enumerate(ordered, 1)),
              "Closed export lane census disagrees")
        consumed.add(identity)

    _need(len(cut.directory_heads) == pin.directory_shards, "Closed export directory count disagrees")
    for shard, head in enumerate(cut.directory_heads):
        identity = "v3_directory", str(shard)
        ordered = sorted(registrations_by_shard[shard], key=lambda value: value.directory_ordinal)
        _need(head.shard == shard and head.count == len(ordered) and values.get(identity) == head.model_dump(mode="json"),
              "Closed export directory census disagrees")
        consumed.add(identity)
        previous = ZERO
        for ordinal, registration in enumerate(ordered, 1):
            slot = "v3_registration_slots", f"{shard}:{ordinal}"
            _need(registration.directory_ordinal == ordinal and registration.previous == previous
                  and values.get(slot) == registration.model_dump(mode="json"),
                  "Closed export registration slot chain disagrees")
            consumed.add(slot)
            previous = registration.digest
        _need(head.digest == previous, "Closed export directory chain disagrees")

    consumed.update(validate_scoped_rows(values))
    _need(consumed == set(values), "Closed export contains unregistered native rows")


def _verify(receipt: PartitionedCompleteReceipt, read: BoundedReader) -> VerifiedExport:
    _need(type(receipt) is PartitionedCompleteReceipt, "An exact closed export receipt is required")
    receipt = PartitionedCompleteReceipt.model_validate(receipt)
    pin = receipt.pin
    prefix = f"{fingerprint(pin.model_dump(mode='json'))}/{receipt.close_id}"
    bucket = pin.authority.journal_bucket

    def load(path: str, generation: str, sha: str, size: int | None = None) -> dict:
        try:
            raw = read(bucket, path, generation, size or MAX_OBJECT_BYTES)
        except Exception:
            raise GuardUnavailable("Closed export object could not be read") from None
        _need(type(raw) is bytes and 0 < len(raw) <= MAX_OBJECT_BYTES and (size is None or size == len(raw))
              and hashlib.sha256(raw).hexdigest() == sha, "Closed export object bytes disagree")
        value = json.loads(raw)
        _need(type(value) is dict and canonical(value).encode() == raw, "Closed export object is not canonical")
        return value

    def object_value(ref: ObjectReference, path: str) -> dict:
        _need(ref.path == path, "Closed export object path disagrees")
        return load(path, ref.generation, ref.sha256, ref.byte_size)

    manifest_path = f"authority-v3-complete-cuts/{prefix}.json"
    _need(receipt.manifest_path == manifest_path, "Closed export receipt path disagrees")
    manifest = Manifest.model_validate(load(manifest_path, receipt.manifest_generation, receipt.manifest_sha256))
    _need(manifest.pin == pin and manifest.close_id == receipt.close_id
          and manifest.records_count == receipt.records_count
          and manifest.authority_rows_count == receipt.authority_rows_count
          and manifest.entries_count == manifest.records_count + manifest.authority_rows_count
          and len(manifest.chunks) == (manifest.entries_count + 63) // 64
          and manifest.native_cut.sha256 == receipt.native_cut_sha256, "Closed export receipt disagrees with manifest")
    cut = PartitionedCut.model_validate(object_value(manifest.native_cut, f"authority-v3-native-cuts/{prefix}.json"))
    _need(cut.root.pin == pin and cut.root.close_id == receipt.close_id
          and cut.records_count == manifest.records_count and cut.authority_rows_count == manifest.authority_rows_count,
          "Closed native cut identity or counts disagree")

    intent_entries: dict[str, IntentEntry] = {}
    row_entries: dict[str, RowEntry] = {}
    previous = ZERO
    for ordinal, ref in enumerate(manifest.chunks, 1):
        chunk = Chunk.model_validate(object_value(ref, f"authority-v3-cut-chunks/{prefix}/{ordinal}.json"))
        _need(chunk.pin == pin and chunk.close_id == receipt.close_id
              and chunk.ordinal == ordinal and chunk.previous == previous
              and len(chunk.entries) == min(64, manifest.entries_count - (ordinal - 1) * 64),
              "Closed export chunk chain disagrees")
        for entry in chunk.entries:
            if isinstance(entry, IntentEntry):
                operation_id = str(entry.operation_id)
                _need(operation_id not in intent_entries, "Closed export contains a repeated entry")
                intent_entries[operation_id] = entry
            else:
                _need(entry.identity_sha256 not in row_entries, "Closed export contains a repeated entry")
                row_entries[entry.identity_sha256] = entry
        previous = ref.sha256
    _need(previous == manifest.chunk_chain_sha256 and len(intent_entries) == manifest.records_count
          and len(row_entries) == manifest.authority_rows_count, "Closed export entry census disagrees")

    rows: list[dict] = []
    values: dict[tuple[str, str], dict] = {}
    records: dict[str, PartitionedRecord] = {}
    root_seen = False
    for identity, entry in sorted(row_entries.items()):
        row = object_value(entry, f"authority-v3-native-rows/{prefix}/{identity}.json")
        _need(set(row) == {"namespace", "key", "value"} and type(row["namespace"]) is str
              and type(row["key"]) is str and type(row["value"]) is dict
              and fingerprint({"namespace": row["namespace"], "key": row["key"]}) == identity,
              "Closed native row identity disagrees")
        rows.append(row)
        values[row["namespace"], row["key"]] = row["value"]
        if row["namespace"] == "v3_root":
            _need(row["key"] == "current" and row["value"] == cut.root.model_dump(mode="json"),
                  "Closed native root row disagrees")
            root_seen = True
        if row["namespace"] == "v3_records":
            record = PartitionedRecord.model_validate(row["value"])
            key = str(record.reference.operation_id)
            _need(key == row["key"] and key not in records, "Closed native record identity disagrees")
            records[key] = record
    _need(root_seen and fingerprint({"authority_rows": rows}) == cut.authority_rows_sha256
          and set(records) == set(intent_entries)
          and fingerprint({"records": [records[key].model_dump(mode="json") for key in sorted(records)]})
          == cut.records_sha256, "Closed native census digest disagrees")
    # The pure intent decoder uses only the pin. The offline resource provides
    # its typed configuration without implying any provider-resource custody.
    resource = PartitionedResource(pin=pin, bucket=bucket, generation="1", sha256=fingerprint(pin.model_dump(mode="json")))
    for key, record in records.items():
        ref, entry = record.reference, intent_entries[key]
        intent = object_value(entry, ref.path)
        decoded, body = partitioned_intent(canonical(intent).encode(), resource)
        _need(decoded == ref and body == record.intent and entry.sha256 == ref.sha256 and entry.byte_size == ref.byte_size,
              "Closed export intent disagrees with native record")
    _validate_native_census(cut, values, records)
    return VerifiedExport(receipt.close_id, receipt.manifest_sha256, len(records), len(rows))


def verify_closed_export(receipt: PartitionedCompleteReceipt, read: BoundedReader) -> VerifiedExport:
    """Verify offline bytes from an independently trusted receipt.

    The read port accepts bucket, path, exact generation and maximum byte size.
    Its caller must bind custody to that generation and bound any I/O. This
    verifier does not discover a receipt, verify current freshness, or replay.
    """
    try:
        return _verify(receipt, read)
    except (ValidationError, TypeError, ValueError, KeyError, OSError, RecursionError):
        raise GuardUnavailable("Closed export evidence unavailable or malformed") from None
