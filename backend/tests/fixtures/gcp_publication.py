"""Real independent native publication resource; synthetic operator UID/GCS witness.

This is fixture-only provisioning, not product initialization or IAM/WORM proof.
Every created database is unique; no shared database reset/deletion occurs.
"""

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

from app.domains.recovery.contracts import canonical
from app.domains.recovery.gcp_buffer import BufferedRegistry
from app.domains.recovery.gcp_contracts import RegistryPin
from app.domains.recovery.gcp_publication import GcpPublicationCoordinator
from app.domains.recovery.gcp_publication_contracts import (
    PublicationGate,
    PublicationPin,
    PublicationResourceReceipt,
    publication_resource_path,
)
from app.domains.recovery.gcp_rpc import FirestoreRpc


def publication_for(target, journal_bucket, *, active=None):
    from test_gcp_journal_sdk import response

    endpoint = local_firestore_endpoint()
    channel = grpc.insecure_channel(endpoint)
    grpc.channel_ready_future(channel).result(timeout=3.0)
    atexit.register(channel.close)
    client = FirestoreClient(
        transport=FirestoreGrpcTransport(
            host=endpoint, channel=channel, credentials=AnonymousCredentials()
        )
    )
    native = RegistryPin(
        database=f"projects/hirewiz-local-authority/databases/publication-{uuid4().hex[:12]}",
        database_uid=uuid4(),
        authority_id=uuid4(),
        incarnation=1,
        epoch_id=uuid4(),
    )
    pin = PublicationPin(registry=native, target=target, journal_bucket=journal_bucket)

    class Admin:
        reported_uid = str(native.database_uid)

        def get_database(self, **kwargs):
            assert active is None or not active, (
                "Publication identity IO entered a native transaction"
            )
            return Database(
                name=native.database,
                uid=self.reported_uid,
                type_=Database.DatabaseType.FIRESTORE_NATIVE,
            )

    rpc = FirestoreRpc(client, Admin(), database_resource=native.database, rpc_timeout=5.0)
    if active is not None:
        begin, commit, rollback = rpc.begin, rpc.commit, rpc.rollback

        def tracked_begin():
            t = begin()
            active.add(t)
            return t

        def tracked_commit(t, writes):
            try:
                return commit(t, writes)
            finally:
                active.discard(t)

        def tracked_rollback(t):
            try:
                return rollback(t)
            finally:
                active.discard(t)

        rpc.begin, rpc.commit, rpc.rollback = tracked_begin, tracked_commit, tracked_rollback
    registry = BufferedRegistry(rpc, deadline_seconds=10.0)
    gate = PublicationGate(pin=pin, phase="OPEN")
    registry.run(
        lambda tx: tx._stage(
            "control", "publication", gate.model_dump(mode="json"), immutable=False
        )
    )
    raw = canonical(pin.model_dump(mode="json")).encode()
    path = publication_resource_path(pin)
    state = SimpleNamespace(
        raw=raw,
        generation="301",
        missing=False,
        timeout=False,
        native=native,
        requests=[],
        registry=registry,
        rpc=rpc,
    )

    def request(method, url, **kwargs):
        assert active is None or not active, "Publication witness IO entered a native transaction"
        assert method == "GET" and kwargs["timeout"] == 2.0
        state.requests.append((method, url))
        if state.timeout:
            raise requests.exceptions.Timeout("Synthetic publication witness loss")
        if state.missing:
            return response(b'{"error":{"code":404}}', status=404)
        query = parse_qs(urlsplit(url).query)
        assert unquote(urlsplit(url).path.split("/o/", 1)[1]) == path
        if query.get("alt") == ["media"]:
            return response(state.raw)
        return response(
            json.dumps(
                {
                    "name": path,
                    "bucket": journal_bucket,
                    "generation": state.generation,
                    "size": str(len(state.raw)),
                }
            ).encode()
        )

    session = MagicMock(spec=requests.Session)
    session.is_mtls = False
    session.request.side_effect = request
    storage = Client(
        project="hirewiz-local-authority", credentials=AnonymousCredentials(), _http=session
    )
    atexit.register(storage.close)
    resource = PublicationResourceReceipt(
        pin=pin, bucket=journal_bucket, generation="301", sha256=hashlib.sha256(raw).hexdigest()
    )
    result = GcpPublicationCoordinator(registry, resource, storage.bucket(resource.bucket))
    result.test_state = state
    return result
