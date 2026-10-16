"""Injected Firestore GAPIC calls; no client, credentials or production factory.

The SDK transport is supplied explicitly. Local intercepted-transport tests establish
request/retry contracts only, not emulator, IAM or cloud durability evidence.
"""
from __future__ import annotations

import json
import math
import re
from typing import Any
from uuid import UUID

from google.api_core.exceptions import Aborted

from .contracts import canonical
from .gcp_contracts import AmbiguousCommit, RpcCommit, RpcSnapshot, RpcWrite
from .store import GuardDenied, GuardUnavailable

MAX_DOCUMENTS = 64
MAX_BODY_BYTES = 128 * 1024
_DATABASE = re.compile(r"projects/[a-z][a-z0-9-]{4,28}[a-z0-9]/databases/[a-z][a-z0-9-]{2,61}[a-z0-9]")


def _body(raw: bytes) -> bytes:
    try:
        if type(raw) is not bytes or not 0 < len(raw) <= MAX_BODY_BYTES:
            raise ValueError
        value = json.loads(raw.decode("utf-8"))
        if type(value) is not dict or canonical(value).encode("utf-8") != raw:
            raise ValueError
    except (ValueError, TypeError, UnicodeError, RecursionError) as exc:
        raise GuardDenied("RPC body must be bounded canonical JSON object bytes") from exc
    return raw


def _timestamp(value: Any) -> Any:
    from google.protobuf.timestamp_pb2 import Timestamp

    if not isinstance(value, Timestamp) or not 0 <= value.nanos < 1_000_000_000:
        raise ValueError("RPC timestamp is malformed")
    # Validate the full protobuf range, not just its Python truthiness.
    value.ToDatetime()
    copied = Timestamp()
    copied.CopyFrom(value)
    return copied


class FirestoreRpc:
    def __init__(self, client: Any, admin: Any, *, database_resource: str,
                 rpc_timeout: float = 2.0):
        if (type(database_resource) is not str or not _DATABASE.fullmatch(database_resource)
                or re.fullmatch(r"[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}",
                                database_resource.rsplit("/", 1)[-1])):
            raise GuardDenied("An explicit named Firestore database resource is required")
        if (type(rpc_timeout) not in (int, float) or not 0 < rpc_timeout <= 10
                or not math.isfinite(rpc_timeout)):
            raise GuardDenied("RPC timeout must be finite and between zero and ten seconds")
        if client is None or admin is None:
            raise GuardUnavailable("Explicit Firestore and metadata clients are required")
        self.client, self.admin = client, admin
        self.database_resource, self.rpc_timeout = database_resource, float(rpc_timeout)

    @staticmethod
    def _transaction(transaction_id: bytes) -> None:
        if type(transaction_id) is not bytes or not 0 < len(transaction_id) <= 4096:
            raise GuardDenied("An explicit bounded Firestore transaction ID is required")

    def _path(self, path: str) -> None:
        prefix = self.database_resource + "/documents/"
        try:
            if type(path) is not str or not path.startswith(prefix) or len(path.encode()) > 6144:
                raise ValueError
            parts = path[len(prefix):].split("/")
            if (len(parts) % 2 or any(not part or part in {".", ".."}
                    or re.fullmatch(r"__.*__", part) or len(part.encode()) > 1500 for part in parts)):
                raise ValueError
        except (ValueError, UnicodeError) as exc:
            raise GuardDenied("RPC document path must belong to the pinned database") from exc

    def identity(self) -> dict:
        from google.cloud.firestore_admin_v1.types import Database

        try:
            response = self.admin.get_database(request={"name": self.database_resource},
                                               retry=None, timeout=self.rpc_timeout)
            if (not isinstance(response, Database) or response.name != self.database_resource
                    or response.type_ != Database.DatabaseType.FIRESTORE_NATIVE
                    or str(UUID(response.uid)) != response.uid):
                raise ValueError("Database metadata differs from the explicit identity")
            return {"name": response.name, "uid": response.uid, "type": "FIRESTORE_NATIVE"}
        except Exception as exc:
            raise GuardUnavailable("Pinned database metadata is unavailable") from exc

    def begin(self) -> bytes:
        from google.cloud.firestore_v1.types import BeginTransactionResponse

        try:
            response = self.client.begin_transaction(request={"database": self.database_resource,
                "options": {"read_write": {}}}, retry=None, timeout=self.rpc_timeout)
            if not isinstance(response, BeginTransactionResponse):
                raise ValueError("Malformed transaction response")
            self._transaction(response.transaction)
            return response.transaction
        except Aborted:
            raise
        except Exception as exc:
            raise GuardUnavailable("Firestore transaction could not begin") from exc

    def read(self, transaction_id: bytes, paths: tuple[str, ...]) -> dict[str, RpcSnapshot]:
        from google.cloud.firestore_v1.types import BatchGetDocumentsResponse, Document

        self._transaction(transaction_id)
        if (type(paths) is not tuple or not 0 < len(paths) <= MAX_DOCUMENTS
                or any(type(path) is not str for path in paths)
                or len(set(paths)) != len(paths)):
            raise GuardDenied("RPC reads need unique bounded document paths")
        for path in paths:
            self._path(path)
        try:
            stream = self.client.batch_get_documents(request={"database": self.database_resource,
                "transaction": transaction_id, "documents": paths}, retry=None,
                timeout=self.rpc_timeout)
            snapshots: dict[str, RpcSnapshot] = {}
            for response in stream:
                if not isinstance(response, BatchGetDocumentsResponse):
                    raise ValueError("Malformed streamed document response")
                result = BatchGetDocumentsResponse.pb(response).WhichOneof("result")
                if result == "missing":
                    snapshot = RpcSnapshot(response.missing, None, None)
                elif result == "found":
                    document = Document.pb(response.found)
                    if (set(document.fields) != {"body"} or not document.HasField("update_time")
                            or document.fields["body"].WhichOneof("value_type") != "bytes_value"):
                        raise ValueError("Document body or version is malformed")
                    snapshot = RpcSnapshot(document.name, _body(document.fields["body"].bytes_value),
                                           _timestamp(document.update_time))
                else:
                    raise ValueError("A requested document has neither found nor missing evidence")
                if snapshot.path not in paths or snapshot.path in snapshots:
                    raise ValueError("Unexpected or duplicate streamed document")
                snapshots[snapshot.path] = snapshot
            if set(snapshots) != set(paths):
                raise ValueError("Stream omitted requested document evidence")
            return snapshots
        except Aborted:
            raise
        except Exception as exc:
            raise GuardUnavailable("Complete transactional document evidence is unavailable") from exc

    def commit(self, transaction_id: bytes, writes: tuple[RpcWrite, ...]) -> RpcCommit:
        from google.cloud.firestore_v1.types import CommitResponse, Write

        self._transaction(transaction_id)
        if type(writes) is not tuple or len(writes) > MAX_DOCUMENTS:
            raise GuardDenied("RPC commit exceeds the bounded write set")
        prepared, seen = [], set()
        for write in writes:
            if not isinstance(write, RpcWrite):
                raise GuardDenied("A typed RPC write is required")
            self._path(write.path)
            if write.path in seen:
                raise GuardDenied("RPC writes must use unique document paths")
            seen.add(write.path)
            _body(write.raw)
            try:
                condition = ({"exists": False} if write.version is None else
                             {"update_time": _timestamp(write.version)})
                prepared.append(Write(update={"name": write.path, "fields": {
                    "body": {"bytes_value": write.raw}}}, current_document=condition))
            except (TypeError, ValueError, OverflowError) as exc:
                raise GuardDenied("RPC write version is malformed") from exc
        # No result escapes after this send unless the complete commit reply validates.
        try:
            response = self.client.commit(request={"database": self.database_resource,
                "transaction": transaction_id, "writes": prepared}, retry=None,
                timeout=self.rpc_timeout)
            if not isinstance(response, CommitResponse):
                raise ValueError("Malformed commit response")
            committed = CommitResponse.pb(response)
            if not committed.HasField("commit_time") or len(committed.write_results) != len(writes):
                raise ValueError("Incomplete commit evidence")
            _timestamp(committed.commit_time)
            for result in committed.write_results:
                if not result.HasField("update_time"):
                    raise ValueError("Missing write version")
                _timestamp(result.update_time)
            return RpcCommit(len(writes))
        except Aborted:
            raise
        except Exception as exc:
            raise AmbiguousCommit("Firestore commit was sent without complete confirmed evidence") from exc

    def rollback(self, transaction_id: bytes) -> None:
        self._transaction(transaction_id)
        try:
            self.client.rollback(request={"database": self.database_resource,
                "transaction": transaction_id}, retry=None, timeout=self.rpc_timeout)
        except Aborted:
            raise
        except Exception as exc:
            raise GuardUnavailable("Firestore transaction cleanup is unavailable") from exc
