"""Detached V3 export verification with synthetic bytes and no transport."""

import hashlib
import json
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.domains.recovery.contracts import canonical, fingerprint
from app.domains.recovery.gcp_contracts import DenyScope, JournalIntent, RegistryPin
from app.domains.recovery.gcp_partitioned_contracts import (
    DirectoryHead,
    PartitionedCompleteReceipt,
    PartitionedCut,
    PartitionedPin,
    PartitionedRecord,
    PartitionedResource,
    PartitionedRoot,
    SegmentRegistration,
    SegmentState,
)
from app.domains.recovery.gcp_partitioned_export_verification import (
    MAX_OBJECT_BYTES,
    verify_closed_export,
)
from app.domains.recovery.gcp_partitioned_publication import partitioned_intent
from app.domains.recovery.gcp_publication_contracts import PublicationPin
from app.domains.recovery.store import GuardUnavailable


@pytest.fixture
def archive():
    def registry(name):
        return RegistryPin(database=f"projects/hirewiz-local/databases/{name}", database_uid=uuid4(),
                           authority_id=uuid4(), incarnation=1, epoch_id=uuid4())

    pin = PartitionedPin(authority=PublicationPin(registry=registry("export"), target=registry("application"),
                         journal_bucket="synthetic-bucket"), lanes=2, directory_shards=2)
    root = PartitionedRoot(pin=pin, phase="CLOSED", close_id=uuid4())
    prefix = f"{fingerprint(pin.model_dump(mode='json'))}/{root.close_id}"
    objects = {}

    def store(path, body, generation="1"):
        raw = canonical(body).encode()
        objects[path] = (generation, raw)
        return {"path": path, "generation": generation, "sha256": fingerprint(body), "byte_size": len(raw)}

    subject = uuid4()
    intent = JournalIntent(operation_id=uuid4(), pin=pin.authority.target,
                           effect=DenyScope(subject_uuid=subject, scope="subject", target=str(subject), revision="1"),
                           created_at_ms=1_800_000_000_000, deadline_ms=1_800_000_060_000)
    resource = PartitionedResource(pin=pin, bucket=pin.authority.journal_bucket, generation="1",
                                   sha256=fingerprint(pin.model_dump(mode="json")))
    reference, body = partitioned_intent(canonical(intent.model_dump(mode="json")).encode(), resource)
    key = str(reference.operation_id)
    zero = "0" * 64
    registration = {"segment_id": "b" * 64, "lane": int(hashlib.sha256(key.encode()).hexdigest(), 16) % 2,
                    "lane_ordinal": 1, "directory_shard": 0, "directory_ordinal": 1, "previous": zero}
    registration = SegmentRegistration.model_validate({**registration, "digest": fingerprint(registration)})
    binding = {"reference": reference.model_dump(mode="json"), "segment_id": registration.segment_id,
               "ordinal": 1, "previous": zero}
    record = PartitionedRecord.model_validate({**binding, "chain_digest": fingerprint(binding), "intent": body})
    directory = DirectoryHead(shard=0, count=1, digest=registration.digest)
    segment = SegmentState(registration=registration, phase="SEALED", count=1, digest=record.chain_digest)

    # One orphan deny is durably recorded, with no matching consuming grant.
    rows = {
        ("v3_root", "current"): root.model_dump(mode="json"),
        ("v3_records", key): record.model_dump(mode="json"),
        ("v3_directory", "0"): directory.model_dump(mode="json"),
        ("v3_directory", "1"): {"shard": 1, "count": 0, "digest": zero},
        ("v3_segments", registration.segment_id): segment.model_dump(mode="json"),
        ("v3_registration_slots", "0:1"): registration.model_dump(mode="json"),
        ("v3_record_slots", f"{registration.segment_id}:1"): {
            "operation_id": key, "record_sha256": fingerprint(record.model_dump(mode="json")),
        },
        ("v3_lanes", str(registration.lane)): {
            "lane": registration.lane, "segment_count": 1, "segment_id": registration.segment_id,
        },
        ("v3_lanes", str(1 - registration.lane)): {
            "lane": 1 - registration.lane, "segment_count": 0, "segment_id": None,
        },
    }
    native_rows = {fingerprint({"namespace": ns, "key": row_key}): {"namespace": ns, "key": row_key, "value": value}
                   for (ns, row_key), value in rows.items()}
    cut = PartitionedCut(root=root, directory_heads=(directory, DirectoryHead(shard=1, count=0, digest=zero)),
                         segments=(segment,), records_sha256=fingerprint({"records": [record.model_dump(mode="json")]}),
                         records_count=1, authority_rows_count=len(rows),
                         authority_rows_sha256=fingerprint({"authority_rows": [native_rows[k] for k in sorted(native_rows)]}))
    entries = [{"kind": "canonical_intent", "operation_id": key, **store(reference.path, body, "2")}]
    entries.extend({"kind": "native_authority_row", "identity_sha256": identity,
                    **store(f"authority-v3-native-rows/{prefix}/{identity}.json", row, "3")}
                   for identity, row in sorted(native_rows.items()))
    chunk_path = f"authority-v3-cut-chunks/{prefix}/1.json"
    manifest_path = f"authority-v3-complete-cuts/{prefix}.json"
    manifest = {"version": 3, "pin": pin.model_dump(mode="json"), "close_id": str(root.close_id),
                "native_cut": store(f"authority-v3-native-cuts/{prefix}.json", cut.model_dump(mode="json")),
                "chunks": [], "chunk_chain_sha256": zero, "records_count": 1, "authority_rows_count": len(rows),
                "entries_count": len(entries), "complete_prefix": True, "projection_complete": False}

    def seal(previous=zero):
        chunk = store(chunk_path, {"version": 3, "pin": pin.model_dump(mode="json"), "close_id": str(root.close_id),
                     "ordinal": 1, "previous": previous, "entries": entries}, "4")
        manifest["chunks"] = [chunk]
        manifest["chunk_chain_sha256"] = chunk["sha256"]
        ref = store(manifest_path, manifest, "5")
        return PartitionedCompleteReceipt(pin=pin, close_id=root.close_id, native_cut_sha256=manifest["native_cut"]["sha256"],
                                         records_count=1, authority_rows_count=len(rows), manifest_path=manifest_path,
                                         manifest_generation=ref["generation"], manifest_sha256=ref["sha256"])

    reads = []

    def read(bucket, path, generation, bound):
        reads.append(path)
        assert bucket == pin.authority.journal_bucket and 0 < bound <= MAX_OBJECT_BYTES
        found_generation, value = objects[path]
        if found_generation != generation:
            raise OSError("Synthetic generation changed")
        assert len(value) <= bound
        return value

    return SimpleNamespace(seal=seal, read=read, reads=reads, objects=objects, entries=entries, chunk=chunk_path,
                           intent=reference.path, root=entries[1]["path"], rows=len(rows), store=store)


def test_detached_export_checks_every_native_and_orphan_intent_object(archive):
    receipt = archive.seal()
    proof = verify_closed_export(receipt, archive.read)
    assert proof.close_id == receipt.close_id and proof.manifest_sha256 == receipt.manifest_sha256
    assert (proof.records_count, proof.authority_rows_count) == (1, archive.rows)
    assert len(archive.reads) == len(set(archive.reads)) == len(archive.objects)


def test_detached_export_suppresses_reader_error_bodies(archive):
    def read(*args):
        raise RuntimeError("Synthetic provider error body")

    with pytest.raises(GuardUnavailable, match="^Closed export object could not be read$") as error:
        verify_closed_export(archive.seal(), read)
    assert error.value.__suppress_context__


@pytest.mark.parametrize("fault", ["missing_chunk", "wrong_generation", "modified_bytes", "missing_intent",
                                   "wrong_chain", "duplicate_entry", "replaced_row", "other_intent"])
def test_detached_export_refuses_changed_or_incomplete_evidence(archive, fault):
    receipt = archive.seal()
    if fault == "missing_chunk":
        del archive.objects[archive.chunk]
    if fault == "wrong_generation":
        archive.objects[archive.chunk] = ("9", archive.objects[archive.chunk][1])
    if fault == "modified_bytes":
        generation, raw = archive.objects[archive.root]
        archive.objects[archive.root] = generation, raw[:-1] + b" "
    if fault == "missing_intent":
        del archive.objects[archive.intent]
    if fault == "wrong_chain":
        receipt = archive.seal("a" * 64)
    if fault == "duplicate_entry":
        archive.entries[-1] = archive.entries[-2]
        receipt = archive.seal()
    if fault == "replaced_row":
        entry = archive.entries[-1]
        row = json.loads(archive.objects[entry["path"]][1])
        row["value"] = {"synthetic_replacement": True}
        entry.update(archive.store(entry["path"], row, "6"))
        receipt = archive.seal()
    if fault == "other_intent":
        entry = archive.entries[0]
        entry.update(archive.store(entry["path"], {"synthetic_replacement": True}, "6"))
        receipt = archive.seal()
    with pytest.raises(GuardUnavailable):
        verify_closed_export(receipt, archive.read)
