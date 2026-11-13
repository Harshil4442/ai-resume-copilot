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
    PartitionedCompleteReceipt,
    PartitionedContract,
    PartitionedCut,
    PartitionedPin,
    PartitionedRecord,
)
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

    rows, records = [], {}
    root_seen = False
    for identity, entry in sorted(row_entries.items()):
        row = object_value(entry, f"authority-v3-native-rows/{prefix}/{identity}.json")
        _need(set(row) == {"namespace", "key", "value"} and type(row["namespace"]) is str
              and type(row["key"]) is str and type(row["value"]) is dict
              and fingerprint({"namespace": row["namespace"], "key": row["key"]}) == identity,
              "Closed native row identity disagrees")
        rows.append(row)
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
    authority = hashlib.sha256(str(pin.authority.target.authority_id).encode()).hexdigest()
    for key, record in records.items():
        ref, entry = record.reference, intent_entries[key]
        _need(ref.path == f"authority-intents/{authority}/{ref.partition}/{key}.json"
              and entry.sha256 == ref.sha256 and entry.byte_size == ref.byte_size
              and object_value(entry, ref.path) == record.intent, "Closed export intent disagrees with native record")
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
