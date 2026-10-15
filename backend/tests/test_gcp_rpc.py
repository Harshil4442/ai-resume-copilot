"""Actual SDK/GAPIC requests intercepted locally; not emulator, IAM or cloud proof."""
from __future__ import annotations

from collections import defaultdict, deque
from unittest.mock import Mock, create_autospec

import google.auth
import grpc
import pytest
from google.api_core.exceptions import Aborted, DeadlineExceeded, ServiceUnavailable
from google.cloud.firestore_admin_v1.services.firestore_admin import FirestoreAdminClient
from google.cloud.firestore_admin_v1.services.firestore_admin.transports.base import (
    DEFAULT_CLIENT_INFO as ADMIN_INFO,
)
from google.cloud.firestore_admin_v1.services.firestore_admin.transports.base import (
    FirestoreAdminTransport,
)
from google.cloud.firestore_admin_v1.services.firestore_admin.transports.grpc import (
    FirestoreAdminGrpcTransport,
)
from google.cloud.firestore_admin_v1.types import Database, GetDatabaseRequest
from google.cloud.firestore_v1.services.firestore import FirestoreClient
from google.cloud.firestore_v1.services.firestore.transports.base import (
    DEFAULT_CLIENT_INFO,
    FirestoreTransport,
)
from google.cloud.firestore_v1.services.firestore.transports.grpc import FirestoreGrpcTransport
from google.cloud.firestore_v1.types import (
    BatchGetDocumentsRequest,
    BatchGetDocumentsResponse,
    BeginTransactionRequest,
    BeginTransactionResponse,
    CommitRequest,
    CommitResponse,
    Document,
    RollbackRequest,
    WriteResult,
)
from google.protobuf.empty_pb2 import Empty
from google.protobuf.timestamp_pb2 import Timestamp

from app.domains.recovery.gcp_contracts import AmbiguousCommit, RpcWrite
from app.domains.recovery.gcp_rpc import MAX_BODY_BYTES, MAX_DOCUMENTS, FirestoreRpc
from app.domains.recovery.store import (
    GuardDenied,
    GuardUnavailable,
    UnavailableStore,
    production_store,
)

DATABASE = "projects/local-proof/databases/recovery-proof"
FIRST = DATABASE + "/documents/records/first"
SECOND = DATABASE + "/documents/records/second"
UID = "5c809df5-a1d9-4cbe-af4a-552daf3e4bab"
BODY = b'{"active":true,"generation":1}'


def version() -> Timestamp:
    return Timestamp(seconds=1234567890, nanos=123)


def found(path: str = FIRST, body: bytes = BODY) -> BatchGetDocumentsResponse:
    return BatchGetDocumentsResponse(found=Document(name=path, fields={
        "body": {"bytes_value": body}}, update_time=version()))


def committed(count: int = 1) -> CommitResponse:
    return CommitResponse(commit_time=version(), write_results=[
        WriteResult(update_time=version()) for _ in range(count)])


class ScriptedSdk:
    """Explicit fake transport endpoints wrapped by real generated SDK retry policies."""

    def __init__(self):
        self.scripts: dict[str, deque] = defaultdict(deque)
        self.calls: dict[str, list] = defaultdict(list)
        transport = create_autospec(FirestoreGrpcTransport, instance=True)
        admin_transport = create_autospec(FirestoreAdminGrpcTransport, instance=True)
        for target in (transport, admin_transport):
            target.host = "local-contract.invalid:443"
            target._credentials = None
        for name in ("begin_transaction", "batch_get_documents", "commit", "rollback"):
            getattr(transport, name).side_effect = self.endpoint(name)
        admin_transport.get_database.side_effect = self.endpoint("get_database")
        # Real installed SDK wrappers retain default retries. The adapter must override them.
        FirestoreTransport._prep_wrapped_messages(transport, DEFAULT_CLIENT_INFO)
        FirestoreAdminTransport._prep_wrapped_messages(admin_transport, ADMIN_INFO)
        self.client = FirestoreClient(transport=transport)
        self.admin = FirestoreAdminClient(transport=admin_transport)
        for name in ("begin_transaction", "batch_get_documents", "commit", "rollback"):
            setattr(self.client, name, Mock(wraps=getattr(self.client, name)))
        self.admin.get_database = Mock(wraps=self.admin.get_database)
        self.rpc = FirestoreRpc(self.client, self.admin, database_resource=DATABASE)

    def endpoint(self, name: str):
        def invoke(request, **kwargs):
            self.calls[name].append((request, kwargs))
            if not self.scripts[name]:
                raise AssertionError(f"Unexpected second or unscripted local RPC: {name}")
            result = self.scripts[name].popleft()
            if isinstance(result, Exception):
                raise result
            return result
        return invoke

    def queue(self, name: str, *responses) -> None:
        self.scripts[name].extend(responses)


@pytest.fixture
def sdk(monkeypatch) -> ScriptedSdk:
    def forbidden(*args, **kwargs):
        raise AssertionError("Credential resolution, channel creation or RPC retry is forbidden")
    monkeypatch.setattr(google.auth, "default", forbidden)
    monkeypatch.setattr(grpc, "secure_channel", forbidden)
    monkeypatch.setattr(grpc, "insecure_channel", forbidden)
    monkeypatch.setattr("google.api_core.retry.retry_unary.time.sleep", forbidden)
    return ScriptedSdk()


def test_actual_sdk_request_shapes_and_all_retry_timeout_overrides(sdk):
    sdk.queue("get_database", Database(name=DATABASE, uid=UID, type_=1))
    sdk.queue("begin_transaction", BeginTransactionResponse(transaction=b"local-tx"))
    sdk.queue("batch_get_documents", iter([BatchGetDocumentsResponse(missing=SECOND), found()]))
    sdk.queue("commit", committed(2))
    sdk.queue("rollback", Empty())
    assert sdk.rpc.identity() == {"name": DATABASE, "uid": UID, "type": "FIRESTORE_NATIVE"}
    transaction = sdk.rpc.begin()
    snapshots = sdk.rpc.read(transaction, (FIRST, SECOND))
    assert snapshots[FIRST].raw == BODY and snapshots[SECOND].raw is None
    assert snapshots[FIRST].version == version() and snapshots[SECOND].version is None
    assert sdk.rpc.commit(transaction, (RpcWrite(FIRST, BODY, snapshots[FIRST].version),
        RpcWrite(SECOND, BODY, None))).write_count == 2
    sdk.rpc.rollback(transaction)
    request_types = {"get_database": GetDatabaseRequest, "begin_transaction": BeginTransactionRequest,
        "batch_get_documents": BatchGetDocumentsRequest, "commit": CommitRequest, "rollback": RollbackRequest}
    for name, request_type in request_types.items():
        public_call = getattr(sdk.admin if name == "get_database" else sdk.client, name)
        assert public_call.call_args.kwargs["retry"] is None
        assert public_call.call_args.kwargs["timeout"] == 2.0
        assert len(sdk.calls[name]) == 1
        request, options = sdk.calls[name][0]
        assert isinstance(request, request_type)
        assert 0 < options["timeout"] <= 2.0
        assert request_type.deserialize(request_type.serialize(request)) == request
    begin = sdk.calls["begin_transaction"][0][0]
    assert begin.database == DATABASE and begin.options._pb.WhichOneof("mode") == "read_write"
    read = sdk.calls["batch_get_documents"][0][0]
    assert read.transaction == transaction and tuple(read.documents) == (FIRST, SECOND)
    assert read._pb.WhichOneof("consistency_selector") == "transaction"
    assert not read._pb.HasField("mask")
    commit = sdk.calls["commit"][0][0]
    assert commit.database == DATABASE and commit.transaction == transaction
    existing, created = commit.writes
    assert existing.current_document._pb.WhichOneof("condition_type") == "update_time"
    assert existing.current_document._pb.update_time == version()
    assert created.current_document._pb.WhichOneof("condition_type") == "exists"
    assert created.current_document.exists is False
    assert set(existing.update.fields) == {"body"} and existing.update.fields["body"].bytes_value == BODY
    assert existing._pb.WhichOneof("operation") == "update"


def test_snapshot_timestamp_is_copied(sdk):
    response = found()
    sdk.queue("batch_get_documents", iter([response]))
    snapshot = sdk.rpc.read(b"tx", (FIRST,))[FIRST]
    Document.pb(response.found).update_time.seconds += 1
    assert snapshot.version == version()


@pytest.mark.parametrize("responses", [
    [], [found(), found()], [found(SECOND)], [BatchGetDocumentsResponse()],
    [BatchGetDocumentsResponse(missing=FIRST), found()], [None],
    [BatchGetDocumentsResponse(found=Document(name=FIRST, fields={"body": {"string_value": "{}"}}, update_time=version()))],
    [BatchGetDocumentsResponse(found=Document(name=FIRST, fields={"body": {"bytes_value": BODY}}))],
    [BatchGetDocumentsResponse(found=Document(name=FIRST, fields={"body": {"bytes_value": BODY}, "extra": {"integer_value": 1}}, update_time=version()))],
    [found(body=b'{ "active": true }')], [found(body=b'{}\n')], [found(body=b'[]')],
])
def test_incomplete_or_malformed_read_stream_never_becomes_absence(sdk, responses):
    sdk.queue("batch_get_documents", iter(responses))
    with pytest.raises(GuardUnavailable):
        sdk.rpc.read(b"tx", (FIRST,))
    assert len(sdk.calls["batch_get_documents"]) == 1


def test_midstream_failure_discards_already_observed_documents(sdk):
    def partial():
        yield found()
        raise ServiceUnavailable("local stream stopped")
    sdk.queue("batch_get_documents", partial())
    with pytest.raises(GuardUnavailable):
        sdk.rpc.read(b"tx", (FIRST, SECOND))


@pytest.mark.parametrize("response", [None, CommitResponse(), CommitResponse(commit_time=version()),
    committed(2), CommitResponse(commit_time=version(), write_results=[WriteResult()])])
def test_malformed_commit_reply_is_ambiguous_after_single_send(sdk, response):
    sdk.queue("commit", response)
    with pytest.raises(AmbiguousCommit):
        sdk.rpc.commit(b"tx", (RpcWrite(FIRST, BODY, None),))
    assert len(sdk.calls["commit"]) == 1


@pytest.mark.parametrize("error", [ServiceUnavailable("local"), DeadlineExceeded("local"), RuntimeError("local")])
def test_commit_errors_do_not_use_sdk_default_retries(sdk, error):
    sdk.queue("commit", error, committed())
    with pytest.raises(AmbiguousCommit) as caught:
        sdk.rpc.commit(b"tx", (RpcWrite(FIRST, BODY, None),))
    assert caught.value.__cause__ is error and len(sdk.calls["commit"]) == 1
    assert len(sdk.scripts["commit"]) == 1


@pytest.mark.parametrize("method", ["begin_transaction", "batch_get_documents", "commit", "rollback"])
def test_genuine_aborted_is_preserved_for_owner_retry_loop(sdk, method):
    error = Aborted("definite local transaction abort")
    sdk.queue(method, error)
    with pytest.raises(Aborted) as caught:
        if method == "begin_transaction":
            sdk.rpc.begin()
        elif method == "batch_get_documents":
            sdk.rpc.read(b"tx", (FIRST,))
        elif method == "commit":
            sdk.rpc.commit(b"tx", (RpcWrite(FIRST, BODY, None),))
        else:
            sdk.rpc.rollback(b"tx")
    assert caught.value is error and len(sdk.calls[method]) == 1


@pytest.mark.parametrize("metadata", [Database(), Database(name=DATABASE, uid=UID, type_=2),
    Database(name=DATABASE, uid=UID.upper(), type_=1), Database(name=DATABASE, uid="not-uuid", type_=1),
    Database(name=DATABASE.replace("local-proof", "other-project"), uid=UID, type_=1), None])
def test_bad_metadata_cannot_establish_identity(sdk, metadata):
    sdk.queue("get_database", metadata)
    with pytest.raises(GuardUnavailable):
        sdk.rpc.identity()


@pytest.mark.parametrize("resource", ["", "recovery-proof", "projects/local-proof/databases/(default)",
    DATABASE + "/documents", "projects/local-proof/databases/a", "projects/local-proof/databases/" + UID])
def test_explicit_named_database_is_required(sdk, resource):
    with pytest.raises(GuardDenied):
        FirestoreRpc(sdk.client, sdk.admin, database_resource=resource)


@pytest.mark.parametrize("timeout", [True, None, "2", 0, -1, 10.1, float("nan"), float("inf"), 10**1000])
def test_timeout_is_explicit_finite_and_bounded(sdk, timeout):
    with pytest.raises(GuardDenied):
        FirestoreRpc(sdk.client, sdk.admin, database_resource=DATABASE, rpc_timeout=timeout)


@pytest.mark.parametrize("paths", [(), (FIRST, FIRST), [FIRST], ({},),
    (DATABASE.replace("local-proof", "other-project") + "/documents/records/first",),
    (DATABASE + "/documents/records",), (DATABASE + "/documents/records/../x/y",),
    tuple(DATABASE + f"/documents/records/{index}" for index in range(MAX_DOCUMENTS + 1))])
def test_invalid_read_scope_is_rejected_before_transport(sdk, paths):
    with pytest.raises(GuardDenied):
        sdk.rpc.read(b"tx", paths)
    assert sdk.calls["batch_get_documents"] == []


@pytest.mark.parametrize("writes", [(RpcWrite(FIRST, b'{ "a": 1 }', None),),
    (RpcWrite(FIRST, b'{"a":NaN}', None),), (RpcWrite(FIRST, b'[]', None),),
    (RpcWrite(FIRST, b'{"x":"' + b'x' * MAX_BODY_BYTES + b'"}', None),),
    (RpcWrite(FIRST, BODY, "not-version"),), (RpcWrite(FIRST, BODY, Timestamp(nanos=-1)),),
    (RpcWrite(FIRST, BODY, None), RpcWrite(FIRST, BODY, None)), (object(),)])
def test_invalid_writes_are_definite_local_rejection_before_send(sdk, writes):
    with pytest.raises(GuardDenied):
        sdk.rpc.commit(b"tx", writes)
    assert sdk.calls["commit"] == []


def test_zero_write_commit_still_requires_confirmed_commit_time(sdk):
    sdk.queue("commit", committed(0))
    assert sdk.rpc.commit(b"tx", ()).write_count == 0


def test_inert_default_store_is_unchanged():
    assert isinstance(production_store(), UnavailableStore)
