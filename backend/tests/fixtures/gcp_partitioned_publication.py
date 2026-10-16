"""Independently owned actual native V3 resource; synthetic operator pins only."""

from __future__ import annotations

import atexit
import hashlib
import json
from types import SimpleNamespace
from unittest.mock import MagicMock
from urllib.parse import parse_qs, unquote, urlsplit
from uuid import uuid4

import grpc
import requests
from backend.tests.fixtures.firestore_emulator import local_firestore_endpoint
from google.auth.credentials import AnonymousCredentials
from google.cloud.firestore_admin_v1.types import Database
from google.cloud.firestore_v1.services.firestore import FirestoreClient
from google.cloud.firestore_v1.services.firestore.transports.grpc import FirestoreGrpcTransport
from google.cloud.storage import Client
from test_gcp_journal_sdk import response

from app.domains.recovery.contracts import canonical
from app.domains.recovery.gcp_buffer import BufferedRegistry
from app.domains.recovery.gcp_contracts import RegistryPin
from app.domains.recovery.gcp_partitioned_contracts import (
    DirectoryHead,
    LaneHead,
    PartitionedPin,
    PartitionedResource,
    PartitionedRoot,
)
from app.domains.recovery.gcp_partitioned_publication import PartitionedPublicationCoordinator
from app.domains.recovery.gcp_publication_contracts import PublicationPin
from app.domains.recovery.gcp_rpc import FirestoreRpc


def partitioned_for(c, *, segment_size=4):
    endpoint = local_firestore_endpoint()
    channel = grpc.insecure_channel(endpoint)
    grpc.channel_ready_future(channel).result(timeout=3)
    atexit.register(channel.close)
    client = FirestoreClient(transport=FirestoreGrpcTransport(host=endpoint, channel=channel,
        credentials=AnonymousCredentials()))
    native = RegistryPin(database=f"projects/hirewiz-local-authority/databases/pub-v3-{uuid4().hex[:12]}",
        database_uid=uuid4(), authority_id=uuid4(), incarnation=1, epoch_id=uuid4())
    class Admin:
        reported_uid = str(native.database_uid)
        def get_database(self, **kwargs):
            assert not c.active, "V3 identity entered a native transaction"
            return Database(name=native.database, uid=self.reported_uid, type_=Database.DatabaseType.FIRESTORE_NATIVE)
    rpc = FirestoreRpc(client, Admin(), database_resource=native.database, rpc_timeout=5)
    begin, commit, rollback = rpc.begin, rpc.commit, rpc.rollback
    def tracked_begin():
        tx = begin()
        c.active.add(tx)
        return tx
    def tracked_commit(tx, writes):
        try:
            return commit(tx, writes)
        finally:
            c.active.discard(tx)
    def tracked_rollback(tx):
        try:
            return rollback(tx)
        finally:
            c.active.discard(tx)
    rpc.begin, rpc.commit, rpc.rollback = tracked_begin, tracked_commit, tracked_rollback
    registry = BufferedRegistry(rpc, deadline_seconds=10)
    pin = PartitionedPin(authority=PublicationPin(registry=native, target=c.pin,
        journal_bucket=c.journal.bucket.name), lanes=2, directory_shards=2, segment_size=segment_size)
    def seed(tx):
        tx.put("v3_root", "current", PartitionedRoot(pin=pin, phase="OPEN").model_dump(mode="json"))
        for lane in range(pin.lanes):
            tx.put("v3_lanes", str(lane), LaneHead(lane=lane, segment_count=0, segment_id=None).model_dump(mode="json"))
        for shard in range(pin.directory_shards):
            tx.put("v3_directory", str(shard), DirectoryHead(shard=shard, count=0, digest="0" * 64).model_dump(mode="json"))
    registry.run(seed)
    raw = canonical(pin.model_dump(mode="json")).encode()
    resource = PartitionedResource(pin=pin, bucket=c.journal.bucket.name,
        generation="501", sha256=hashlib.sha256(raw).hexdigest())
    # Every injected Storage SDK client for this bucket shares one actual HTTP
    # object store; otherwise an exporter could fabricate a parallel bucket.
    c.http.objects[resource.path] = (raw, "501")
    state = SimpleNamespace(raw=raw, generation="501", missing=False, requests=[], rpc=rpc, registry=registry,
        objects=c.http.objects, on_request=None)
    def request(method, url, **kwargs):
        assert not c.active, "V3 witness entered a native transaction"
        assert method in {"GET", "POST"} and kwargs["timeout"] == 2
        state.requests.append((method, url))
        if state.on_request is not None:
            state.on_request(method, url, kwargs)
        query = parse_qs(urlsplit(url).query)
        if method == "GET" and urlsplit(url).path.endswith("/o"):
            prefix = query["prefix"][0]
            token = int(query.get("pageToken", ["0"])[0])
            limit = int(query["maxResults"][0])
            assert 1 <= limit <= 64
            items = [{"name": path, "bucket": resource.bucket, "generation": generation, "size": str(len(data))}
                for path, (data, generation) in sorted(state.objects.items()) if path.startswith(prefix)]
            end = token + limit
            body = {"items": items[token:end]}
            if end < len(items):
                body["nextPageToken"] = str(end)
            return response(json.dumps(body).encode())
        if method == "POST":
            path = json.loads(kwargs["data"].split(b"\r\n\r\n", 1)[1].split(b"\r\n--", 1)[0])["name"]
            assert path == resource.close_path or path.startswith(("authority-intents/", "authority-v3-"))
            if path in state.objects:
                return response(b'{"error":{"code":412}}', status=412)
            data = kwargs["data"].split(b"\r\n\r\n")[2].rsplit(b"\r\n--", 1)[0]
            if path == resource.close_path:
                PartitionedRoot.model_validate_json(data)
            generation = str(600 + len(state.objects))
            state.objects[path] = (data, generation)
            return response(json.dumps({"name": path, "bucket": resource.bucket,
                "generation": generation, "size": str(len(data))}).encode())
        path = unquote(urlsplit(url).path.split("/o/", 1)[1])
        assert path in {resource.path, resource.close_path} or path.startswith(("authority-intents/", "authority-v3-"))
        if path not in state.objects:
            return response(b'{"error":{"code":404}}', status=404)
        if state.missing:
            return response(b'{"error":{"code":404}}', status=404)
        data, generation = (state.raw, state.generation) if path == resource.path else state.objects[path]
        if parse_qs(urlsplit(url).query).get("alt") == ["media"]:
            return response(data)
        return response(json.dumps({"name": path, "bucket": resource.bucket,
            "generation": generation, "size": str(len(data))}).encode())
    http = MagicMock(spec=requests.Session)
    http.is_mtls = False
    http.request.side_effect = request
    storage = Client(project="hirewiz-local-authority", credentials=AnonymousCredentials(), _http=http)
    atexit.register(storage.close)
    result = PartitionedPublicationCoordinator(registry, resource, storage.bucket(resource.bucket))
    result.test_state = state
    return result
