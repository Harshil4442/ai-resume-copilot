"""Current native flat-record census; no ADC, historical read or authority output."""

from __future__ import annotations

import json
import math
import time
from collections.abc import Callable
from dataclasses import dataclass

from google.cloud.firestore_v1.types import Document, RunQueryResponse

from .contracts import canonical, fingerprint
from .gcp_rpc import FirestoreRpc, _body
from .store import GuardDenied, GuardUnavailable


@dataclass(frozen=True)
class ScannedAuthorityRecord:
    path: str
    _raw: bytes

    @property
    def envelope(self) -> dict:
        return json.loads(self._raw)

    @property
    def raw(self) -> bytes:
        return self._raw


class NativeAuthorityScanner:
    """Exhaustive bounded __name__ census of the actual flat authority collection.

    No namespace or indexed-field filter can hide an orphan full publication row.
    The caller must first close its transactional admission/registration boundary.
    This returns inventory evidence only, never dispatch or OPEN authority.
    """

    def __init__(self, rpc: FirestoreRpc, *, monotonic: Callable[[], float] = time.monotonic):
        if type(rpc) is not FirestoreRpc:
            raise GuardDenied("An actual pinned native RPC is required for the complete census")
        self.rpc, self.monotonic = rpc, monotonic

    def scan(self, *, page_size: int = 32, max_documents: int = 4096,
             max_pages: int = 256, deadline_seconds: float = 30.0) -> tuple[ScannedAuthorityRecord, ...]:
        if (type(page_size) is not int or not 1 <= page_size <= 64
                or type(max_documents) is not int or not 1 <= max_documents <= 65_536
                or type(max_pages) is not int or not 1 <= max_pages <= 4096
                or type(deadline_seconds) not in {int, float}
                or not 0 < deadline_seconds <= 60 or not math.isfinite(deadline_seconds)):
            raise GuardDenied("Native census bounds must be explicit positive finite values")
        prefix = self.rpc.database_resource + "/documents/authority_records/"
        found: list[ScannedAuthorityRecord] = []
        after: str | None = None
        deadline = self.monotonic() + deadline_seconds
        for _ in range(max_pages):
            if self.monotonic() >= deadline:
                raise GuardUnavailable("Complete native census deadline exhausted")
            query = {"from_": [{"collection_id": "authority_records"}],
                     "order_by": [{"field": {"field_path": "__name__"}, "direction": "ASCENDING"}],
                     "limit": page_size}
            if after is not None:
                query["start_at"] = {"values": [{"reference_value": after}], "before": False}
            page: list[ScannedAuthorityRecord] = []
            try:
                stream = self.rpc.client.run_query(request={"parent": self.rpc.database_resource + "/documents",
                    "structured_query": query}, retry=None, timeout=self.rpc.rpc_timeout)
                for response in stream:
                    if self.monotonic() >= deadline:
                        raise GuardUnavailable("Complete native census streaming deadline exhausted")
                    if not isinstance(response, RunQueryResponse) or response.skipped_results != 0:
                        raise ValueError("Native census stream returned unexpected response or offset")
                    if not response.document.name:
                        if page:
                            raise ValueError("Native census stream has unexpected trailing metadata")
                        continue
                    document = Document.pb(response.document)
                    name = document.name
                    if (not name.startswith(prefix) or "/" in name[len(prefix):]
                            or name <= (page[-1].path if page else after or "")
                            or len(page) >= page_size or set(document.fields) != {"body"}
                            or not document.HasField("update_time")
                            or document.fields["body"].WhichOneof("value_type") != "bytes_value"):
                        raise ValueError("Native census identity/order/body disagrees")
                    raw = _body(document.fields["body"].bytes_value)
                    value = json.loads(raw)
                    if (set(value) != {"namespace", "key", "value"}
                            or type(value["namespace"]) is not str or type(value["key"]) is not str
                            or type(value["value"]) is not dict or canonical(value).encode() != raw
                            or name != prefix + fingerprint({"namespace": value["namespace"], "key": value["key"]})):
                        raise ValueError("Native census canonical identity binding disagrees")
                    page.append(ScannedAuthorityRecord(name, raw))
                    if len(found) + len(page) > max_documents:
                        raise GuardUnavailable("Complete native census document bound exhausted")
            except Exception:
                raise GuardUnavailable("Complete current native census is unavailable") from None
            found.extend(page)
            if len(page) < page_size:
                if self.monotonic() >= deadline:
                    raise GuardUnavailable("Native census deadline exhausted before terminal return")
                return tuple(found)
            after = page[-1].path
        raise GuardUnavailable("Complete native census page bound exhausted")
