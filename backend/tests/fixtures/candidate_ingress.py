"""Actual unique loopback native replay DB; synthetic labelled custody/clock."""

from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import grpc
from backend.tests.fixtures.firestore_emulator import local_firestore_endpoint
from google.auth.credentials import AnonymousCredentials
from google.cloud.firestore_admin_v1.types import Database
from google.cloud.firestore_v1.services.firestore import FirestoreClient
from google.cloud.firestore_v1.services.firestore.transports.grpc import FirestoreGrpcTransport
from google.cloud.firestore_v1.types import Document, Write

from app.domains.candidate_ingress.contracts import (
    MAX_FRESHNESS_MS,
    IngressAssertion,
    IngressControl,
    IngressResource,
)
from app.domains.candidate_ingress.crypto import IngressKey
from app.domains.candidate_ingress.replay import (
    CONTROL_NAMESPACE,
    CandidatePrivateIngress,
    GcpCandidateIngressReplay,
)
from app.domains.recovery.contracts import canonical
from app.domains.recovery.gcp_buffer import BufferedTransaction
from app.domains.recovery.gcp_contracts import RegistryPin
from app.domains.recovery.gcp_rpc import FirestoreRpc
from app.domains.recovery.store import GuardUnavailable

NOW = 2_000_000_000_000


def pin(prefix="candidate-ingress"):
    return RegistryPin(
        database=f"projects/hirewiz-local-authority/databases/{prefix}-{uuid4().hex[:12]}",
        database_uid=uuid4(),
        authority_id=uuid4(),
        incarnation=1,
        epoch_id=uuid4(),
    )


class SyntheticEnvironment:
    """Not production UID/IAM/restore/clock evidence; actual transactions below."""

    def __init__(self, resource, now_ms=None):
        self.resource, self.time = resource, NOW
        self.clock = now_ms
        self.available = True

    def check(self, resource):
        if not self.available or resource != self.resource:
            raise GuardUnavailable("Synthetic ingress custody unavailable")

    def now_ms(self):
        return self.clock() if self.clock is not None else self.time


def connect(resource, *, clock=None):
    endpoint = local_firestore_endpoint()
    channel = grpc.insecure_channel(endpoint)
    grpc.channel_ready_future(channel).result(timeout=3)
    client = FirestoreClient(
        transport=FirestoreGrpcTransport(
            host=endpoint, channel=channel, credentials=AnonymousCredentials()
        )
    )

    def metadata(*, request, retry, timeout):
        assert request == {"name": resource.registry.database} and retry is None
        return Database(
            name=resource.registry.database,
            uid=str(resource.registry.database_uid),
            type_=Database.DatabaseType.FIRESTORE_NATIVE,
        )

    rpc = FirestoreRpc(
        client,
        SimpleNamespace(get_database=metadata),
        database_resource=resource.registry.database,
        rpc_timeout=1,
    )
    environment = SyntheticEnvironment(resource, clock)
    replay = GcpCandidateIngressReplay(rpc, resource, environment)
    key = IngressKey(resource.key_id, b"n" * 32)
    return SimpleNamespace(
        channel=channel,
        client=client,
        rpc=rpc,
        environment=environment,
        replay=replay,
        key=key,
        ingress=CandidatePrivateIngress(replay, key),
        resource=resource,
    )


def create(*, origin="https://www.hirewiz.example", action_pins=None, clock=None):
    resource = IngressResource(
        registry=pin(),
        action_registries=action_pins or (pin("action-ordinary"), pin("action-protected")),
        origin=origin,
        key_id="fixture_ingress_v1",
        custody_id=uuid4(),
        restore_generation=1,
        clock_witness_id=uuid4(),
    )
    c = connect(resource, clock=clock)
    raw = canonical(
        {
            "namespace": CONTROL_NAMESPACE,
            "key": "root",
            "value": IngressControl(resource=resource, state="OPEN").model_dump(mode="json"),
        }
    ).encode()
    path = BufferedTransaction(c.rpc, b"path-only").path(CONTROL_NAMESPACE, "root")
    result = c.client.commit(
        request={
            "database": resource.registry.database,
            "writes": [
                Write(
                    update=Document(name=path, fields={"body": {"bytes_value": raw}}),
                    current_document={"exists": False},
                )
            ],
        },
        retry=None,
        timeout=2,
    )
    assert len(result.write_results) == 1 and result.commit_time
    return c


def phase_classes(error):
    """Bounded diagnostic classes/codes only; never error strings/inputs."""
    result = []
    seen = set()
    while error is not None and len(result) < 5 and id(error) not in seen:
        seen.add(id(error))
        entry = type(error).__name__
        if isinstance(error, grpc.RpcError):
            entry += ":" + error.code().name
        result.append(entry)
        error = error.__cause__ or error.__context__
    return result


def signed_headers(c, path, raw=b"", *, method="POST", authorization=""):
    """Actual test-only server signing, never a fixture-issued candidate context."""
    request_id, now = uuid4(), c.environment.now_ms()
    assertion = IngressAssertion(
        key_id=c.resource.key_id,
        request_id=request_id,
        method=method,
        path=path,
        origin=c.resource.origin,
        issued_at_ms=now,
        expires_at_ms=now + MAX_FRESHNESS_MS,
        body_commitment=c.key.commitment(request_id, raw),
        authorization_commitment=c.key.commitment(
            request_id, authorization.encode(), authorization=True
        ),
    )
    return {
        "Content-Type": "application/json",
        "x-hirewiz-candidate-ingress": c.key.header(assertion),
        **({"Authorization": authorization} if authorization else {}),
    }
