"""Actual production factory, data-plane GAPIC emulator and synthetic UID/GCS custody.

Only the existing synthetic operator baseline is seeded. Native enrollment and
session creation execute normally. UID/IAM/cloud Storage are NOT provider proof.
"""
from __future__ import annotations

import socket
import threading
import time
from collections import Counter
from contextlib import contextmanager
from contextvars import ContextVar
from types import SimpleNamespace
from unittest.mock import Mock
from urllib.parse import parse_qs, urlsplit

import google.auth
import grpc
import httpx
import pytest
import requests
from google.auth.credentials import AnonymousCredentials
from google.cloud.firestore_admin_v1.types import Database, ListDatabasesResponse
from google.cloud.firestore_v1.services.firestore import FirestoreClient
from google.cloud.firestore_v1.services.firestore.transports.grpc import FirestoreGrpcTransport
from google.cloud.firestore_v1.types import Document, Write

from app.domains.recovery.contracts import canonical
from app.domains.recovery.gcp_buffer import BufferedTransaction
from tests.test_gcp_rpc import ScriptedSdk

_request_scope: ContextVar[object | None] = ContextVar("stage5_owned_request_scope", default=None)


class ThreadSet:
    """Request-local exclusion including sync-dependency worker handoffs.

    AnyIO copies ContextVars into worker threads; thread identity alone would
    leave stale SQL exclusions when cleanup runs on a different worker. Setup
    outside HTTP has a thread-local fallback, never another request's scope.
    """
    def __init__(self):
        self.lock = threading.RLock()
        self.scopes: dict[object, set] = {}

    def key(self):
        return _request_scope.get() or ("setup-thread", threading.get_ident())

    def _values(self):
        return self.scopes.setdefault(self.key(), set())

    def add(self, value):
        with self.lock:
            self._values().add(value)

    def discard(self, value):
        with self.lock:
            self._values().discard(value)

    def __bool__(self):
        with self.lock:
            return bool(self._values())

    def clear_current(self):
        with self.lock:
            return self.scopes.pop(self.key(), set())


class RequestGuardScope:
    """Observation-only outer ASGI scope; never an auth/database override."""
    def __init__(self, app, sets, counters):
        self.app, self.sets, self.counters = app, sets, counters

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        token = _request_scope.set(object())
        try:
            return await self.app(scope, receive, send)
        finally:
            for value in self.sets:
                if value.clear_current():
                    self.counters.add("request_transaction_guard_leaks")
            _request_scope.reset(token)


class Counters:
    def __init__(self):
        self.lock = threading.Lock()
        self.values: Counter[str] = Counter()

    def add(self, key, amount=1):
        with self.lock:
            self.values[key] += amount

    def snapshot(self):
        with self.lock:
            return dict(self.values)


class EmulatorNative:
    def __init__(self, client, counters):
        self.client, self.counters = client, counters
        self.sdk, self.identities, self.views = ScriptedSdk(), {}, ThreadSet()
        transport = self.sdk.client.transport
        for name in ("begin_transaction", "batch_get_documents", "commit", "rollback"):
            method = {"begin_transaction": self.begin, "batch_get_documents": self.read,
                      "commit": self.commit, "rollback": self.rollback}[name]
            getattr(transport, name).side_effect = method
        self.sdk.admin.transport.get_database.side_effect = self.identity
        self.sdk.admin.transport.list_databases.side_effect = self.listed
        self.sdk.admin.list_databases = Mock(wraps=self.sdk.admin.list_databases)

    def call(self, name, request, **kwargs):
        if kwargs.get("retry") is not None or not 0 < kwargs.get("timeout", 0) <= 2:
            raise AssertionError("Emulator RPC retry/timeout differs")
        self.counters.add("data_" + name)
        return getattr(self.client, name)(request=request, retry=None, timeout=2)

    def identity(self, request, **kwargs):
        assert not self.views
        self.counters.add("synthetic_admin_get_database")
        return Database(name=request.name, uid=self.identities[request.name], type_=1)

    def listed(self, request, **kwargs):
        assert not self.views and request.parent == "projects/hirewiz-local-authority"
        assert request.show_deleted is False and kwargs["timeout"] == 2.0
        self.counters.add("synthetic_admin_list_databases")
        return ListDatabasesResponse(databases=[
            Database(name=name, uid=uid, type_=1) for name, uid in self.identities.items()])

    def begin(self, request, **kwargs):
        reply = self.call("begin_transaction", request, **kwargs)
        self.views.add(bytes(reply.transaction))
        return reply

    def read(self, request, **kwargs):
        # Consume the actual stream while the request's owned transaction is live.
        return iter(list(self.call("batch_get_documents", request, **kwargs)))

    def commit(self, request, **kwargs):
        try:
            return self.call("commit", request, **kwargs)
        finally:
            self.views.discard(bytes(request.transaction))

    def rollback(self, request, **kwargs):
        try:
            return self.call("rollback", request, **kwargs)
        finally:
            self.views.discard(bytes(request.transaction))

    def seed(self, registry, namespace, key, value):
        path = BufferedTransaction(registry.rpc, b"seed").path(namespace, key)
        reply = self.client.commit(request={"database": registry.rpc.database_resource,
            "writes": [Write(update=Document(name=path, fields={"body": {"bytes_value":
                canonical({"namespace": namespace, "key": key, "value": value}).encode()}}),
                current_document={"exists": False})]}, retry=None, timeout=2)
        assert len(reply.write_results) == 1


def install_egress_guard(patch, ports: set[int], counters: Counters):
    """No DNS/HTTP/ADC/native TLS channel to a provider; only exact owned ports."""
    original_connect, original_connect_ex = socket.socket.connect, socket.socket.connect_ex
    original_send, original_async = httpx.Client.send, httpx.AsyncClient.send
    original_insecure = grpc.insecure_channel

    def allowed(address):
        return (type(address) is tuple and len(address) >= 2 and address[0] == "127.0.0.1"
                and address[1] in ports)

    def forbidden(*args, **kwargs):
        counters.add("blocked_external_attempts")
        raise AssertionError("External credential or transport access is forbidden")

    def connect(stream, address):
        if not allowed(address):
            return forbidden()
        return original_connect(stream, address)

    def connect_ex(stream, address):
        if not allowed(address):
            return forbidden()
        return original_connect_ex(stream, address)

    def checked_url(url):
        value = urlsplit(str(url))
        if value.hostname != "127.0.0.1" or value.port not in ports or value.scheme not in ("http", "https"):
            forbidden()

    def send(client, request, *args, **kwargs):
        checked_url(request.url)
        return original_send(client, request, *args, **kwargs)

    async def async_send(client, request, *args, **kwargs):
        checked_url(request.url)
        return await original_async(client, request, *args, **kwargs)

    def channel(target, *args, **kwargs):
        if target not in {f"127.0.0.1:{port}" for port in ports}:
            return forbidden()
        return original_insecure(target, *args, **kwargs)

    patch.setattr(socket.socket, "connect", connect)
    patch.setattr(socket.socket, "connect_ex", connect_ex)
    patch.setattr(google.auth, "default", forbidden)
    patch.setattr(grpc, "secure_channel", forbidden)
    patch.setattr(grpc, "insecure_channel", channel)
    patch.setattr(requests.Session, "request", forbidden)
    patch.setattr(httpx.Client, "send", send)
    patch.setattr(httpx.AsyncClient, "send", async_send)


@contextmanager
def configured_accounts(*, engine, directory, endpoint: str, counters):
    # This shares the source-qualified fixture's operator pins, real Storage SDK
    # request checks and unchanged production factory. No get_current_user override.
    import tests.test_candidate_production_factory as fixture

    patch = pytest.MonkeyPatch()
    channel = grpc.insecure_channel(endpoint)
    generator = None
    try:
        grpc.channel_ready_future(channel).result(timeout=45)
        client = FirestoreClient(transport=FirestoreGrpcTransport(host=endpoint, channel=channel,
                                                                credentials=AnonymousCredentials()))
        native = EmulatorNative(client, counters)
        patch.setattr(fixture, "InterceptedNative", lambda: native)
        patch.setattr(fixture, "create_engine", lambda *args, **kwargs: engine)
        patch.setattr(fixture, "NOW", time.time_ns() // 1_000_000)
        generator = fixture.configured.__wrapped__(patch, directory)
        c = next(generator)
        c.sql_active = ThreadSet()
        # Preserve the existing no-SQL/no-native-transaction Storage guard;
        # count its actual SDK transport requests, never payloads or paths.
        original = c.storage._http.request.side_effect
        def storage_request(*args, **kwargs):
            counters.add("synthetic_storage_http")
            return original(*args, **kwargs)
        c.storage._http.request.side_effect = storage_request
        from app.domains.candidate_accounts import service
        patch.setattr(service, "production_candidate_accounts", lambda: c.accounts)
        yield SimpleNamespace(case=c, patch=patch, native=native)
    finally:
        if generator is not None:
            generator.close()
        channel.close()
        patch.undo()


def check_body(path: str, body: dict, *, user_id: int, buyer: bool, heavy: bool) -> bool:
    from scripts.stage5_seed import owned_opportunity_ids, synthetic_email

    if type(body) is not dict:
        return False
    if path == "/api/auth/bootstrap":
        return (type(body.get("profile")) is dict and type(body.get("credit_wallet")) is dict
                and body.get("schema_version") == "owned-bootstrap-v1" and
                body["profile"].get("email") == synthetic_email(user_id) and
                body.get("unavailable") == [] and
                body.get("credit_wallet", {}).get("available_credits") == (90 if buyer else 0))
    if path == "/api/resume/list":
        return len(body.get("resumes", [])) == 1 and body["resumes"][0]["id"] == user_id
    if urlsplit(path).path == "/api/v1/opportunities":
        query = parse_qs(urlsplit(path).query, strict_parsing=True)
        if set(query) - {"limit", "offset"} or any(len(values) != 1 for values in query.values()):
            return False
        expected = owned_opportunity_ids(user_id, limit=int(query.get("limit", ["100"])[0]),
                                         offset=int(query.get("offset", ["0"])[0]))
        items = body.get("items", [])
        return (heavy == (user_id == 8) and body.get("total") == (500 if heavy else 10)
                and [row.get("id") for row in items] == expected
                and all(row.get("resume_id") == user_id for row in items))
    if path == "/api/v1/skill-roi":
        return body.get("opportunity_count") == (500 if heavy else 10)
    if path == "/api/v1/credits/wallet":
        return body.get("available_credits") == (90 if buyer else 0)
    return False
