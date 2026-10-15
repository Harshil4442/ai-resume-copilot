"""Bounded disabled complete-cut export; inventory only, no restore/open/grant."""

from __future__ import annotations

import hashlib
import math
from collections.abc import Callable
from time import monotonic

from google.api_core.exceptions import NotFound

from .contracts import canonical, fingerprint
from .gcp_journal import _exact_bytes, _generation, _metadata
from .gcp_pairing_attempts import GcsPairingAttempts
from .gcp_partitioned_contracts import PartitionedCompleteReceipt
from .gcp_partitioned_publication import ClosedPartitionedRoot, PartitionedPublicationCoordinator
from .store import GuardUnavailable


class PartitionedCutExporter:
    def __init__(self, publication: PartitionedPublicationCoordinator, *,
                 deadline_seconds: float = 60, clock: Callable[[], float] = monotonic):
        if (type(publication) is not PartitionedPublicationCoordinator
                or type(deadline_seconds) not in {int, float} or not math.isfinite(deadline_seconds)
                or not 0 < deadline_seconds <= 60 or not callable(clock)):
            raise ValueError("Exact V3 publication and bounded export clock required")
        self.publication, self.deadline_seconds, self.clock = publication, deadline_seconds, clock

    def complete(self, close: ClosedPartitionedRoot, *, max_documents: int = 4096) -> PartitionedCompleteReceipt:
        deadline = self.clock() + self.deadline_seconds
        publication = self.publication
        def check() -> None:
            if self.clock() >= deadline:
                raise GuardUnavailable("Complete V3 export deadline exhausted; no partial cut receipt")
            publication._closed_root(close)
        check()
        cut, records = publication.seal_native(close, max_documents=max_documents)
        cut_raw = canonical(cut.model_dump(mode="json")).encode()
        if len(cut_raw) > 65_536:
            raise GuardUnavailable("Development native-cut object bound exceeded; no complete cut receipt")
        check()
        _, _, actual_records, rows = publication._census(close, max_documents=max_documents)
        if (records != actual_records or len(rows) != cut.authority_rows_count
                or fingerprint({"authority_rows": [r.envelope for r in rows]}) != cut.authority_rows_sha256):
            raise GuardUnavailable("Complete V3 native export cut changed")
        prefix = f"{fingerprint(publication.resource.pin.model_dump(mode='json'))}/{close.root.close_id}"
        def export(path: str, raw: bytes) -> dict:
            check()
            if not 0 < len(raw) <= 65_536:
                raise GuardUnavailable("Development protected export object bound exceeded")
            blob = publication.bucket.blob(path)
            generation: str | None
            try:
                blob.reload(retry=None, timeout=2.0)
                generation = _metadata(blob, len(raw))
            except NotFound:
                generation = GcsPairingAttempts(publication.bucket)._fresh_create(path, raw)
                if generation is None:
                    raise GuardUnavailable("Protected export create has no definite own acknowledgement") from None
            _exact_bytes(publication.bucket, path, raw, generation=generation, timeout=2.0)
            check()
            return {"path": path, "generation": generation, "sha256": hashlib.sha256(raw).hexdigest(),
                    "byte_size": len(raw)}
        cut_receipt = export(f"authority-v3-native-cuts/{prefix}.json", cut_raw)
        entries = []
        for record in records:
            raw = canonical(record.intent).encode()
            entries.append({"kind": "canonical_intent", "operation_id": str(record.reference.operation_id),
                **export(record.reference.path, raw)})
        for row in rows:
            identity = {"namespace": row.envelope["namespace"], "key": row.envelope["key"]}
            entries.append({"kind": "native_authority_row", "identity_sha256": fingerprint(identity),
                **export(f"authority-v3-native-rows/{prefix}/{fingerprint(identity)}.json", row.raw)})
        expected_prefix = {e["path"]: e["generation"] for e in entries if e["kind"] == "canonical_intent"}
        def complete_prefix() -> None:
            check()
            authority = hashlib.sha256(str(publication.resource.pin.authority.target.authority_id).encode()).hexdigest()
            intent_prefix = f"authority-intents/{authority}/"
            iterator = publication.bucket.client.list_blobs(publication.bucket, prefix=intent_prefix,
                page_size=64, max_results=max_documents + 1, retry=None, timeout=2.0)
            found: dict[str, str] = {}
            pages = 0
            for page in iterator.pages:
                check()
                pages += 1
                if pages > 256:
                    raise GuardUnavailable("Complete GCS prefix page bound exhausted")
                for blob in page:
                    path = blob._properties.get("name")
                    if (type(path) is not str or not path.startswith(intent_prefix) or path in found
                            or len(found) >= max_documents or blob._properties.get("bucket") != publication.bucket.name):
                        raise GuardUnavailable("Complete GCS prefix contains duplicate, foreign or overflowing objects")
                    found[path] = _generation(blob._properties.get("generation"))
            if iterator.next_page_token or found != expected_prefix:
                raise GuardUnavailable("Canonical GCS prefix contains missing or unregistered full intents")
            check()
        complete_prefix()
        chunks: list[dict] = []
        previous = "0" * 64
        for start in range(0, len(entries), 64):
            ordinal = len(chunks) + 1
            if ordinal > 128:
                raise GuardUnavailable("Development complete-export chunk cardinality exceeded")
            body = {"version": 3, "pin": publication.resource.pin.model_dump(mode="json"),
                "close_id": str(close.root.close_id), "ordinal": ordinal, "previous": previous,
                "entries": entries[start:start + 64]}
            receipt = export(f"authority-v3-cut-chunks/{prefix}/{ordinal}.json", canonical(body).encode())
            chunks.append(receipt)
            previous = receipt["sha256"]
        manifest = {"version": 3, "pin": publication.resource.pin.model_dump(mode="json"),
            "close_id": str(close.root.close_id), "native_cut": cut_receipt, "chunks": chunks,
            "chunk_chain_sha256": previous, "records_count": len(records), "authority_rows_count": len(rows),
            "entries_count": len(entries), "complete_prefix": True, "projection_complete": False}
        path = f"authority-v3-complete-cuts/{prefix}.json"
        raw = canonical(manifest).encode()
        own = export(path, raw)
        # Completeness rests on the transactional CLOSED boundary and exhaustive
        # unfiltered census. This recheck diagnoses corruption; it is not a fence.
        _, _, final_records, final_rows = publication._census(close, max_documents=max_documents)
        if (final_records != records or fingerprint({"authority_rows": [r.envelope for r in final_rows]})
                != cut.authority_rows_sha256):
            raise GuardUnavailable("Protected native cut changed during final manifest acknowledgement")
        complete_prefix()
        check()
        return PartitionedCompleteReceipt(pin=publication.resource.pin, close_id=close.root.close_id,
            native_cut_sha256=fingerprint(cut.model_dump(mode="json")), records_count=len(records),
            authority_rows_count=len(rows), manifest_path=path, manifest_generation=own["generation"],
            manifest_sha256=own["sha256"])
