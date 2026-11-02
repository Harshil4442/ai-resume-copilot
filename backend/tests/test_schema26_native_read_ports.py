"""Actual installed GAPIC/Storage calls with owned no-network channels/sessions."""

from types import SimpleNamespace
from unittest.mock import patch

import grpc
import pytest
import requests
from google.auth.credentials import AnonymousCredentials
from google.cloud.firestore_admin_v1.services.firestore_admin import (
    FirestoreAdminClient,
)
from google.cloud.firestore_admin_v1.services.firestore_admin.transports.grpc import (
    FirestoreAdminGrpcTransport,
)
from google.cloud.firestore_v1 import types
from google.cloud.firestore_v1.services.firestore import FirestoreClient
from google.cloud.firestore_v1.services.firestore.transports.grpc import (
    FirestoreGrpcTransport,
)
from google.cloud.storage import Client
from test_schema26_maintenance_guard import m, resource

from app.domains.recovery.gcp_contracts import WitnessPin


def config():
    bound = resource()
    witness = WitnessPin(
        registry=bound.pin.authority.target,
        bucket=bound.bucket,
        path="synthetic/witness",
        generation="1",
        sha256="a" * 64,
    )
    return SimpleNamespace(publication_resource=bound, execution_witness_pin=witness)


class Channel(grpc.Channel):
    def __init__(self):
        self.sent = []

    def close(self):
        pass

    def subscribe(self, *a, **k):
        pass

    def unsubscribe(self, *a, **k):
        pass

    def call(self, method, *a, **k):
        parent = self

        class MustNotSend:
            def __call__(self, *a, **k):
                parent.sent.append(method)
                raise AssertionError("no actual network is available")

            with_call = __call__
            future = __call__

        return MustNotSend()

    unary_unary = call
    unary_stream = call
    stream_unary = call
    stream_stream = call


@pytest.mark.parametrize(
    "method",
    ["commit", "create_document", "update_document", "delete_document", "write"],
)
def test_actual_firestore_gapic_mutations_are_denied_before_underlying_channel(method):
    cfg = config()
    raw = Channel()
    counters = {"denied_mutations": 0}
    channel = m._read_channel(raw, cfg, counters)
    client = FirestoreClient(transport=FirestoreGrpcTransport(channel=channel))
    database = cfg.execution_witness_pin.registry.database
    request = {
        "commit": {"database": database},
        "create_document": {
            "parent": database + "/documents",
            "collection_id": "authority_records",
            "document_id": "synthetic",
        },
        "update_document": {
            "document": {"name": database + "/documents/authority_records/synthetic"}
        },
        "delete_document": {"name": database + "/documents/authority_records/synthetic"},
    }
    try:
        with pytest.raises(m.MaintenanceDenied):
            if method == "write":
                client.write(
                    requests=iter([types.WriteRequest(database=database)]),
                    retry=None,
                    timeout=0.1,
                )
            else:
                getattr(client, method)(request=request[method], retry=None, timeout=0.1)
        assert raw.sent == [] and counters["denied_mutations"] == 1
    finally:
        client.transport.close()


def test_actual_admin_create_is_denied_before_underlying_channel():
    cfg = config()
    raw = Channel()
    counters = {"denied_mutations": 0}
    client = FirestoreAdminClient(
        transport=FirestoreAdminGrpcTransport(channel=m._read_channel(raw, cfg, counters))
    )
    try:
        with pytest.raises(m.MaintenanceDenied):
            client.create_database(
                request={"parent": "projects/hirewiz-test", "database_id": "synthetic"},
                retry=None,
                timeout=0.1,
            )
        assert raw.sent == [] and counters["denied_mutations"] == 1
    finally:
        client.transport.close()


def test_actual_read_protobuf_shapes_reach_only_owned_continuation_and_foreign_route_refuses():
    cfg = config()
    counters = {"denied_mutations": 0}
    owned = []
    with patch.object(grpc, "intercept_channel", lambda channel, interceptor: interceptor):
        gate = m._read_channel(object(), cfg, counters)
    database = cfg.execution_witness_pin.registry.database
    protected = cfg.publication_resource.pin.authority.registry.database
    calls = [
        (
            "BeginTransaction",
            types.BeginTransactionRequest.pb(
                types.BeginTransactionRequest(database=database, options={"read_write": {}})
            ),
        ),
        (
            "BatchGetDocuments",
            types.BatchGetDocumentsRequest.pb(
                types.BatchGetDocumentsRequest(
                    database=database,
                    documents=[database + "/documents/authority_records/" + "a" * 64],
                    transaction=b"synthetic",
                )
            ),
        ),
        (
            "Rollback",
            types.RollbackRequest.pb(
                types.RollbackRequest(database=database, transaction=b"synthetic")
            ),
        ),
        (
            "RunQuery",
            types.RunQueryRequest.pb(
                types.RunQueryRequest(
                    parent=protected + "/documents",
                    structured_query={
                        "from_": [{"collection_id": "authority_records"}],
                        "order_by": [
                            {
                                "field": {"field_path": "__name__"},
                                "direction": "ASCENDING",
                            }
                        ],
                        "limit": 32,
                    },
                )
            ),
        ),
    ]
    for method, request in calls:
        details = SimpleNamespace(method="/google.firestore.v1.Firestore/" + method)
        gate.intercept_unary_unary(lambda d, r: owned.append(d.method), details, request)
    assert len(owned) == 4 and counters["denied_mutations"] == 0
    request = types.BeginTransactionRequest.pb(
        types.BeginTransactionRequest(database="projects/foreign/databases/other")
    )
    with pytest.raises(m.MaintenanceDenied):
        gate.intercept_unary_unary(
            lambda *a: pytest.fail("foreign resource must not send"),
            SimpleNamespace(method="/google.firestore.v1.Firestore/BeginTransaction"),
            request,
        )


@pytest.mark.parametrize("method", ["upload", "delete", "create_bucket"])
def test_actual_storage_sdk_mutations_are_denied_before_send(method, monkeypatch):
    monkeypatch.setenv("DISABLE_GCS_PYTHON_CLIENT_OTEL_BUCKET_METADATA", "true")
    cfg = config()
    counters = {"denied_mutations": 0, "storage_gets": 0}
    sends = []
    http = requests.Session()
    http.is_mtls = False
    http.send = lambda *a, **k: sends.append(True)
    m._read_storage(http, cfg, counters)
    client = Client(project="hirewiz-test", credentials=AnonymousCredentials(), _http=http)
    bucket = client.bucket(cfg.publication_resource.bucket)
    try:
        with pytest.raises(m.MaintenanceDenied):
            if method == "upload":
                bucket.blob("synthetic").upload_from_string(
                    b"synthetic-only", retry=None, timeout=0.1
                )
            if method == "delete":
                bucket.blob("synthetic").delete(retry=None, timeout=0.1)
            if method == "create_bucket":
                bucket.create(retry=None, timeout=0.1)
        assert sends == [] and counters["storage_gets"] == 0
    finally:
        client.close()


def test_storage_exact_witness_read_shape_and_foreign_object_or_origin_refuse():
    cfg = config()
    counters = {"denied_mutations": 0, "storage_gets": 0}
    sent = []
    http = requests.Session()
    http.is_mtls = False
    http.send = lambda req, **kw: sent.append((req.url, kw)) or SimpleNamespace(status_code=200)
    m._read_storage(http, cfg, counters)
    url = (
        "https://storage.googleapis.com/storage/v1/b/"
        + cfg.execution_witness_pin.bucket
        + "/o/synthetic%2Fwitness?projection=noAcl"
    )
    http.send(requests.Request("GET", url).prepare())
    assert (
        counters["storage_gets"] == 1
        and sent[0][1]["timeout"] == 2.0
        and sent[0][1]["allow_redirects"] is False
    )
    for other in (
        url.replace("storage.googleapis.com", "evil.example"),
        url.replace("synthetic%2Fwitness", "unrelated"),
    ):
        with pytest.raises(m.MaintenanceDenied):
            http.send(requests.Request("GET", other).prepare())
    assert len(sent) == 1


def test_actual_storage_sdk_original_exact_bytes_reads_pass_fixed_get_only_port(
    monkeypatch,
):
    import json
    from urllib.parse import parse_qs, urlsplit

    from app.domains.recovery.gcp_journal import _exact_bytes

    monkeypatch.setenv("DISABLE_GCS_PYTHON_CLIENT_OTEL_BUCKET_METADATA", "true")
    cfg = config()
    counters = {"denied_mutations": 0, "storage_gets": 0}
    sent = []
    http = requests.Session()
    http.is_mtls = False
    raw = b'{"synthetic":1}'

    def reply(request, **kwargs):
        sent.append(request.url)
        body = (
            raw
            if parse_qs(urlsplit(request.url).query).get("alt") == ["media"]
            else json.dumps(
                {
                    "name": cfg.execution_witness_pin.path,
                    "bucket": cfg.execution_witness_pin.bucket,
                    "generation": "1",
                    "size": str(len(raw)),
                    "contentEncoding": "identity",
                }
            ).encode()
        )
        response = requests.Response()
        response.status_code = 200
        import io

        from urllib3.response import HTTPResponse

        response._content = False
        response._content_consumed = False
        response.raw = HTTPResponse(
            body=io.BytesIO(body), preload_content=False, decode_content=False
        )
        response.headers["content-type"] = "application/json"
        response.headers["content-length"] = str(len(body))
        return response

    http.send = reply
    m._read_storage(http, cfg, counters)
    client = Client(project="hirewiz-test", credentials=AnonymousCredentials(), _http=http)
    try:
        assert (
            _exact_bytes(
                client.bucket(cfg.execution_witness_pin.bucket),
                cfg.execution_witness_pin.path,
                raw,
                generation="1",
                timeout=2.0,
            )
            == "1"
        )
        assert len(sent) == counters["storage_gets"] == 3 and counters["denied_mutations"] == 0
    finally:
        client.close()


@pytest.mark.parametrize("pending", ["none", "queue", "job"])
def test_readonly_producer_component_keeps_exact_queries_and_refuses_executable_work(
    pending, monkeypatch
):
    import schema25_coordinator_pause as coordinator
    import schema25_maintenance as maintenance

    closed = []
    reader = SimpleNamespace(
        stock=SimpleNamespace(session=SimpleNamespace(close=lambda: closed.append(True)))
    )
    monkeypatch.setattr(coordinator, "NativeCoordinatorRun", lambda: reader)
    monkeypatch.setattr(
        coordinator,
        "observe_closed",
        lambda actual: pytest.fail("wrong reader") if actual is not reader else None,
    )
    calls = []

    def query(*args):
        calls.append(args)
        if args[:2] == ("tasks", "list"):
            assert args[2] == "--queue" and args[3] in maintenance.QUEUES
            assert args[4:] == ("--location", "us-central1", "--limit", "257")
            return [{"name": "synthetic-pending"}] if pending == "queue" else []
        assert args == (
            "run",
            "jobs",
            "executions",
            "list",
            "--region",
            "us-central1",
            "--limit",
            "257",
        )
        return (
            [{"status": {}}]
            if pending == "job"
            else [{"status": {"completionTime": "synthetic-completed"}}]
        )

    boundary = SimpleNamespace(observer=SimpleNamespace(_cloud=query))
    if pending == "none":
        m.readonly_producer_drain(boundary)
        assert len(calls) == len(maintenance.QUEUES) + 1
    else:
        with pytest.raises(m.MaintenanceDenied):
            m.readonly_producer_drain(boundary)
    assert closed == [True]
    assert not any("schema21" in str(call) for call in calls)


def test_original_liability_pin_reader_retains_positive_unknown_without_settlement_or_zero():
    from scripts import schema25_app_release_contract as financial

    row = {
        "id": "synthetic-held",
        "reserved_cost_micros": 12345,
        "cost_state": "outcome_unknown",
        "pricing_quote": {"synthetic": "retained-original"},
        "settled_cost_micros": None,
    }

    class Rows:
        def mappings(self):
            return self

        def all(self):
            return [dict(row)]

    class Sql:
        def execute(self, statement):
            assert (
                str(statement)
                == "SELECT * FROM public.model_cost_liabilities ORDER BY id LIMIT 257"
            )
            return Rows()

    pins = financial.liability_pins(Sql())
    assert pins[0][0] == row["id"] and pins[0][2] == 12345
    financial.verify_liability_pins(Sql(), pins)
    assert row["cost_state"] == "outcome_unknown" and row["settled_cost_micros"] is None
    row["reserved_cost_micros"] = 12346
    with pytest.raises(financial.schema25.Schema25Denied) as failure:
        financial.verify_liability_pins(Sql(), pins)
    assert str(failure.value) == "schema25_app_protected_liability_changed"


def test_actual_registry_before_attempt_deadline_has_no_begin_and_finite_subphase_projection():
    from app.domains.recovery.gcp_buffer import BufferedRegistry

    now, begun, fences = [0.0], [], []
    rpc = SimpleNamespace(begin=lambda: begun.append(True))
    registry = BufferedRegistry(rpc, monotonic=lambda: now[0])

    def fence(_pin):
        fences.append(True)
        if len(fences) == 2:
            now[0] = 6.0

    lifetime = SimpleNamespace(pin=object(), fence=SimpleNamespace(check=fence), registry=registry)
    with pytest.raises(m.MaintenanceDenied) as error:
        m._native_snapshot((SimpleNamespace(_fresh_open=lambda: None), lifetime, object()))
    assert begun == [] and len(fences) == 2
    assert error.value.native_subphase == "execution_control_after_witness"
    assert error.value.native_failure == [
        {"exception_class": "GuardUnavailable", "known_code": "registry_total_deadline"}
    ]


def test_native_failure_projection_never_exposes_unlisted_messages_or_custom_class_names():
    from app.domains.recovery.store import GuardUnavailable

    private = type("SyntheticPrivateCandidateClass", (RuntimeError,), {})(
        "synthetic-password-and-body"
    )
    outer = GuardUnavailable("Firestore transaction could not begin")
    outer.__cause__ = private
    result = m.safe_native_failure(outer)
    assert result == [
        {
            "exception_class": "GuardUnavailable",
            "known_code": "firestore_begin_unavailable",
        },
        {"exception_class": "unclassified", "known_code": "unclassified"},
    ]
    assert "password" not in str(result) and "Candidate" not in str(result)
    unknown = m.MaintenanceDenied("schema26_maintenance_unlisted-" + "synthetic-secret" * 100)
    assert m.safe_native_failure(unknown) == [
        {"exception_class": "MaintenanceDenied", "known_code": "unclassified"}
    ]


@pytest.mark.parametrize("elapsed", [6.0, 9.999])
def test_admin_registry_retains_cold_witness_then_one_read_and_rollback(elapsed):
    from app.domains.recovery.contracts import canonical
    from app.domains.recovery.gcp_contracts import RpcSnapshot

    now, calls = [0.0], []
    database = "projects/hirewiz-test/databases/hirewiz-test"

    def read(transaction_id, paths):
        calls.append("read")
        assert transaction_id == b"synthetic-tx" and len(paths) == 1
        raw = canonical(
            {"namespace": "control", "key": "global", "value": {"synthetic": True}}
        ).encode()
        return {paths[0]: RpcSnapshot(paths[0], raw, "synthetic-version")}

    rpc = SimpleNamespace(
        database_resource=database,
        begin=lambda: calls.append("begin") or b"synthetic-tx",
        read=read,
        rollback=lambda tx: calls.append("rollback"),
        commit=lambda *args: pytest.fail("read collector must never commit"),
    )
    registry = m._readonly_registry(rpc)
    registry.monotonic = lambda: now[0]

    def witness():
        calls.append("witness")
        now[0] = elapsed

    observed = []
    registry.run(lambda tx: observed.append(tx.get("control", "global")), before_attempt=witness)
    assert observed == [{"synthetic": True}]
    assert calls == ["witness", "begin", "read", "rollback"]
    assert registry.max_attempts == 1 and registry.deadline_seconds == 10.0


@pytest.mark.parametrize("elapsed", [10.0, 10.001])
def test_admin_registry_exact_budget_refuses_before_begin_without_retry(elapsed):
    from app.domains.recovery.store import GuardUnavailable

    now, calls = [0.0], []
    registry = m._readonly_registry(SimpleNamespace(begin=lambda: calls.append("begin")))
    registry.monotonic = lambda: now[0]

    def witness():
        calls.append("witness")
        now[0] = elapsed

    with pytest.raises(GuardUnavailable, match="Transaction retry deadline exceeded"):
        registry.run(
            lambda tx: pytest.fail("deadline must precede callback"),
            before_attempt=witness,
        )
    assert calls == ["witness"]


def test_admin_registry_definite_abort_does_not_repeat_witness_or_begin():
    from google.api_core.exceptions import Aborted

    from app.domains.recovery.store import GuardUnavailable

    calls = []

    def begin():
        calls.append("begin")
        raise Aborted("synthetic definite abort")

    registry = m._readonly_registry(SimpleNamespace(begin=begin))
    with pytest.raises(GuardUnavailable, match="Definite-abort retry budget exhausted"):
        registry.run(
            lambda tx: pytest.fail("aborted before callback"),
            before_attempt=lambda: calls.append("witness"),
        )
    assert calls == ["witness", "begin"]


def test_runtime_registry_defaults_and_snapshot_witness_calls_are_unchanged():
    import ast
    import inspect

    from app.domains.recovery.gcp_buffer import BufferedRegistry

    runtime = BufferedRegistry(object())
    assert runtime.max_attempts == 3 and runtime.deadline_seconds == 5.0
    graph = ast.parse(inspect.getsource(m._native_graph))
    calls = [
        node
        for node in ast.walk(graph)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "_readonly_registry"
    ]
    assert len(calls) == 2
    snapshot = inspect.getsource(m._snapshot_reads)
    assert "lifetime.registry.run(check_control, before_attempt=before_control)" in snapshot
    assert snapshot.count("lifetime.fence.check(lifetime.pin)") == 3


def test_actual_gapic_census_proto_plus_reaches_owned_channel_without_descriptor_error():
    cfg, raw = config(), Channel()
    counters = {"denied_mutations": 0}
    client = FirestoreClient(
        transport=FirestoreGrpcTransport(channel=m._read_channel(raw, cfg, counters))
    )
    try:
        with pytest.raises(AssertionError, match="no actual network is available"):
            client.run_query(
                request={
                    "parent": cfg.publication_resource.pin.authority.registry.database
                    + "/documents",
                    "structured_query": {
                        "from_": [{"collection_id": "authority_records"}],
                        "order_by": [
                            {
                                "field": {"field_path": "__name__"},
                                "direction": "ASCENDING",
                            }
                        ],
                        "limit": 32,
                    },
                },
                retry=None,
                timeout=0.1,
            )
        assert counters == {"denied_mutations": 0, "RunQuery": 1}
        assert len(raw.sent) == 1 and raw.sent[0].endswith("/RunQuery")
    finally:
        client.transport.close()


def test_original_full_scanner_accepts_real_gapic_proto_plus_over_owned_stream():
    from app.domains.recovery.contracts import canonical, fingerprint
    from app.domains.recovery.gcp_publication_scan import NativeAuthorityScanner
    from app.domains.recovery.gcp_rpc import FirestoreRpc

    cfg = config()
    database = cfg.publication_resource.pin.authority.registry.database
    envelopes = [
        {"namespace": "synthetic_namespace", "key": str(i), "value": {"synthetic": i}}
        for i in range(2)
    ]
    records = sorted(
        (
            database
            + "/documents/authority_records/"
            + fingerprint({"namespace": e["namespace"], "key": e["key"]}),
            e,
        )
        for e in envelopes
    )

    class StreamChannel(Channel):
        def unary_stream(self, method, *args, **kwargs):
            if not method.endswith("/RunQuery"):
                return super().unary_stream(method, *args, **kwargs)

            def stream(request, **_kwargs):
                self.sent.append(method)
                assert type(request) is types.RunQueryRequest
                assert request.parent == database + "/documents"
                return iter(
                    [
                        types.RunQueryResponse(
                            document={
                                "name": name,
                                "fields": {"body": {"bytes_value": canonical(e).encode()}},
                                "update_time": "2026-10-31T00:00:00Z",
                            }
                        )
                        for name, e in records
                    ]
                )

            return stream

    raw, counters = StreamChannel(), {"denied_mutations": 0}
    client = FirestoreClient(
        transport=FirestoreGrpcTransport(channel=m._read_channel(raw, cfg, counters))
    )
    rpc = FirestoreRpc(client, object(), database_resource=database)
    try:
        result = NativeAuthorityScanner(rpc).scan(max_documents=4096)
        assert [row.envelope for row in result] == [e for _, e in records]
        assert [row.path for row in result] == [name for name, _ in records]
        assert counters == {"denied_mutations": 0, "RunQuery": 1}
        assert len(raw.sent) == 1
    finally:
        client.transport.close()
