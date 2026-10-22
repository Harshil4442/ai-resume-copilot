"""Real SDK requests intercepted locally; no ADC, cloud, IAM or custody evidence."""

from __future__ import annotations

import copy
import json
import time
from concurrent.futures import ThreadPoolExecutor
from threading import RLock, get_ident
from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from google.api_core.exceptions import Aborted, ServiceUnavailable
from google.cloud.firestore_admin_v1.types import Database
from google.cloud.firestore_v1.types import (
    BatchGetDocumentsResponse,
    BeginTransactionResponse,
    CommitResponse,
    Document,
    WriteResult,
)
from google.protobuf.empty_pb2 import Empty
from google.protobuf.timestamp_pb2 import Timestamp
from test_browser_submit_native import NOW, native  # noqa: F401

from app.domains.candidate_ingress import production
from app.domains.candidate_ingress.contracts import (
    IngressAssertion,
    IngressControl,
    IngressResource,
)
from app.domains.candidate_ingress.crypto import IngressKey
from app.domains.candidate_ingress.replay import (
    CONTROL_NAMESPACE,
    REPLAY_NAMESPACE,
    production_candidate_ingress,
)
from app.domains.recovery.contracts import canonical
from app.domains.recovery.gcp_buffer import BufferedTransaction
from app.domains.recovery.gcp_contracts import RegistryPin
from app.domains.recovery.gcp_pairing import pairing_control_record
from app.domains.recovery.gcp_rpc import FirestoreRpc
from app.domains.recovery.gcp_service import control_record
from app.domains.recovery.store import GuardUnavailable

PATH = b"/api/auth/candidate/v1/login"
BODY = b'{"email":"synthetic@example.invalid","password":"owned-only"}'
AUTH = b"Bearer owned-private-header"


class NativeTransport:
    def __init__(self, case):
        self.case, self.lock = case, RLock()
        self.data = copy.deepcopy(case.native.data)
        self.views, self.owners, self.reads = {}, {}, {}
        self.clock_start = time.monotonic_ns()
        self.clock_offset_ns = 0
        self.clock_reads = []
        self.clock_fault = None
        self.before_clock = None
        self.commit_fault = None
        self.before_commit = None
        self.clock_seen = []
        self.mutations = []
        sdk = case.native.sdk
        sdk.client.transport.begin_transaction.side_effect = self.begin
        sdk.client.transport.batch_get_documents.side_effect = self.read
        sdk.client.transport.commit.side_effect = self.commit
        sdk.client.transport.rollback.side_effect = self.rollback
        sdk.admin.transport.get_database.side_effect = self.identity

    def outside(self):
        assert get_ident() not in self.owners.values(), "Provider IO entered its own native transaction"

    def identity(self, request, **kwargs):
        self.outside()
        return Database(name=request.name, uid=self.case.native.identities[request.name], type_=1)

    def begin(self, request, **kwargs):
        with self.lock:
            token = uuid4().bytes
            self.views[token] = copy.deepcopy(self.data)
            self.owners[token] = get_ident()
            self.reads[token] = set()
            return BeginTransactionResponse(transaction=token)

    def read(self, request, **kwargs):
        with self.lock:
            if request.transaction:
                view = self.views[request.transaction]
                self.reads[request.transaction].update(request.documents)
                return iter([
                    BatchGetDocumentsResponse(found=Document(name=path,
                        fields={"body": {"bytes_value": view[path][0]}}, update_time=view[path][1]))
                    if path in view else BatchGetDocumentsResponse(missing=path)
                    for path in request.documents
                ])
            self.outside()
            assert request._pb.WhichOneof("consistency_selector") is None
            assert len(request.documents) == 1 and not request._pb.HasField("mask")
            assert kwargs["timeout"] <= 0.5
            self.clock_reads.append(request)
            if self.before_clock:
                self.before_clock()
            now = NOW * 1_000_000 + time.monotonic_ns() - self.clock_start + self.clock_offset_ns
            stamp = Timestamp(seconds=now // 1_000_000_000, nanos=now % 1_000_000_000)
            path = request.documents[0]
            if path not in self.data:
                return iter([BatchGetDocumentsResponse(missing=path, read_time=stamp)])
            result = BatchGetDocumentsResponse(found=Document(name=path,
                fields={"body": {"bytes_value": self.data[path][0]}}, update_time=self.data[path][1]),
                read_time=stamp)
            if self.clock_fault == "missing_time":
                result._pb.ClearField("read_time")
            elif self.clock_fault == "extra_document":
                return iter([result, result])
            elif self.clock_fault == "slow":
                time.sleep(0.01)
            return iter([result])

    def commit(self, request, **kwargs):
        with self.lock:
            if self.before_commit:
                self.before_commit()
            for path in self.reads[request.transaction]:
                if self.data.get(path) != self.views[request.transaction].get(path):
                    raise Aborted("Owned read-set contention")
            if self.commit_fault == "no_persist":
                self.rollback(SimpleNamespace(transaction=request.transaction))
                raise ServiceUnavailable("Owned commit response lost")
            stamp = Timestamp(seconds=NOW // 1000, nanos=len(self.mutations) + 1)
            for write in request.writes:
                assert not write.delete and write.update.fields["body"].bytes_value
                body = json.loads(write.update.fields["body"].bytes_value)
                assert body["namespace"] == REPLAY_NAMESPACE
                assert write.current_document._pb.WhichOneof("condition_type") == "exists"
                assert write.current_document.exists is False
                assert write.update.name not in self.data
                self.data[write.update.name] = (write.update.fields["body"].bytes_value, stamp)
                self.mutations.append(body)
            self.rollback(SimpleNamespace(transaction=request.transaction))
            if self.commit_fault == "persist":
                raise ServiceUnavailable("Owned commit ACK lost after persistence")
            return CommitResponse(commit_time=stamp, write_results=[WriteResult(update_time=stamp) for _ in request.writes])

    def rollback(self, request, **kwargs):
        with self.lock:
            for values in (self.views, self.owners, self.reads):
                values.pop(request.transaction, None)
            return Empty()

    def seed(self, rpc, namespace, key, value):
        path = BufferedTransaction(rpc, b"owned-seed").path(namespace, key)
        self.data[path] = (canonical({"namespace": namespace, "key": key, "value": value}).encode(), Timestamp(seconds=1_700_000_000))


@pytest.fixture
def ingress(native, monkeypatch):  # noqa: F811
    c = native
    c.ingress_resource = IngressResource(
        registry=RegistryPin(database="projects/hirewiz-local-authority/databases/ingress-replay",
            database_uid=uuid4(), authority_id=uuid4(), incarnation=1, epoch_id=uuid4()),
        action_registries=(c.witness.registry, c.resource.pin.authority.registry),
        origin="https://www.hirewizhq.com", key_id="owned_private_v1", custody_id=uuid4(),
        restore_generation=1, clock_witness_id=uuid4(),
    )
    c.native.identities[c.ingress_resource.registry.database] = str(c.ingress_resource.registry.database_uid)
    c.transport = NativeTransport(c)
    http = c.storage._http.request.side_effect
    def outside_http(*args, **kwargs):
        c.transport.outside()
        return http(*args, **kwargs)
    c.storage._http.request.side_effect = outside_http
    c.replay_rpc = FirestoreRpc(c.native.sdk.client, c.native.sdk.admin, database_resource=c.ingress_resource.registry.database)
    c.execution_rpc = FirestoreRpc(c.native.sdk.client, c.native.sdk.admin, database_resource=c.witness.registry.database)
    c.transport.seed(c.replay_rpc, CONTROL_NAMESPACE, "root", IngressControl(resource=c.ingress_resource, state="OPEN").model_dump(mode="json"))
    c.transport.seed(c.execution_rpc, "control", "meta", control_record(c.witness.registry))
    c.transport.seed(c.execution_rpc, "pairing_control", "current", pairing_control_record(c.witness.registry, 1))
    shared = {field: c.config.model_dump(mode="json")[field]
        for field in production.CandidateNativeConfiguration.model_fields}
    c.ingress_config = production.CandidateIngressNativeConfiguration(**shared, resource=c.ingress_resource)
    c.key = IngressKey(c.ingress_resource.key_id, b"k" * 32)
    for name, value in {
        "CANDIDATE_ACCOUNT_LIFECYCLE_ENABLED": "true",
        "CANDIDATE_ACCOUNT_NATIVE_CONFIG": canonical(shared),
        "CANDIDATE_INGRESS_NATIVE_ENABLED": "true",
        "CANDIDATE_INGRESS_NATIVE_CONFIG": canonical(c.ingress_config.model_dump(mode="json")),
        "CANDIDATE_AUTH_TRANSPORT_SECRET": c.key.secret.hex(),
        "CANDIDATE_AUTH_TRANSPORT_KEY_ID": c.key.key_id,
        "BROWSER_SUBMIT_NATIVE_CONFIG": canonical(c.config.model_dump(mode="json")),
    }.items():
        monkeypatch.setenv(name, value)
    from google.cloud import storage
    from google.cloud.firestore_admin_v1.services import firestore_admin
    from google.cloud.firestore_v1.services import firestore
    c.client_constructor = MagicMock(return_value=c.native.sdk.client)
    c.admin_constructor = MagicMock(return_value=c.native.sdk.admin)
    c.storage_constructor = MagicMock(return_value=c.storage)
    monkeypatch.setattr(firestore, "FirestoreClient", c.client_constructor)
    monkeypatch.setattr(firestore_admin, "FirestoreAdminClient", c.admin_constructor)
    monkeypatch.setattr(storage, "Client", c.storage_constructor)
    c.gate = production_candidate_ingress()
    return c


def assertion(c, *, request_id=None, issued=None, expires=None):
    request_id = request_id or uuid4()
    return IngressAssertion(key_id=c.key.key_id, request_id=request_id, method="POST", path=PATH.decode(),
        origin=c.ingress_resource.origin, issued_at_ms=NOW if issued is None else issued,
        expires_at_ms=NOW + 5000 if expires is None else expires,
        body_commitment=c.key.commitment(request_id, BODY),
        authorization_commitment=c.key.commitment(request_id, AUTH, authorization=True))


def send(c, value=None):
    value = value or assertion(c)
    return c.gate.authorize(c.key.header(value), method="POST", path=PATH, raw=BODY, authorization=AUTH)


def test_real_factory_is_structural_then_composes_sdk_and_retains_one_safe_ack(ingress):
    c = ingress
    for constructor in (c.client_constructor, c.admin_constructor, c.storage_constructor):
        constructor.assert_not_called()
    value = assertion(c)
    assert send(c, value) is None
    assert len(c.transport.mutations) == 1 and len(c.transport.clock_reads) == 2
    assert c.transport.mutations[0]["value"]["resource"] == c.ingress_resource.model_dump(mode="json")
    retained = canonical(c.transport.mutations[0]).encode()
    assert BODY not in retained and AUTH not in retained and c.key.secret not in retained
    with pytest.raises(GuardUnavailable):
        send(c, value)
    assert len(c.transport.mutations) == 1
    for rpc in (c.native.sdk.client.begin_transaction, c.native.sdk.client.batch_get_documents,
                c.native.sdk.client.commit, c.native.sdk.client.rollback, c.native.sdk.admin.get_database):
        assert all(call.kwargs["retry"] is None for call in rpc.call_args_list)


def test_clock_synchronization_retains_five_second_expiry_and_requires_a_new_nonce(ingress):
    from app.domains.candidate_ingress.contracts import IngressClockSynchronizationRequired

    c = ingress
    future = assertion(c, issued=NOW + 15000, expires=NOW + 20000)
    with pytest.raises(IngressClockSynchronizationRequired) as denied:
        send(c, future)
    assert not c.transport.mutations
    hint = denied.value
    assert 0 < hint.lower_ms <= hint.upper_ms and hint.upper_ms - hint.lower_ms <= 501
    corrected = assertion(c, issued=hint.lower_ms, expires=hint.lower_ms + 5000)
    assert corrected.request_id != future.request_id
    assert send(c, corrected) is None
    assert len(c.transport.mutations) == 1
    with pytest.raises(GuardUnavailable):
        send(c, corrected)
    assert len(c.transport.mutations) == 1


@pytest.mark.parametrize("header", ["", "malformed", "e30.e30", "a" * 2049])
def test_unauthenticated_header_never_allocates_sdk_clients(ingress, header):
    c = ingress
    with pytest.raises(GuardUnavailable):
        c.gate.authorize(header, method="POST", path=PATH, raw=BODY, authorization=AUTH)
    c.client_constructor.assert_not_called()
    assert not c.transport.clock_reads and not c.transport.mutations


@pytest.mark.parametrize("fault", ["disabled", "malformed", "duplicate", "shared", "same_database", "secret", "key", "reused_secret", "emulator"])
def test_malformed_or_unprovisioned_configuration_never_constructs_sdk(ingress, monkeypatch, fault):
    c = ingress
    raw = c.ingress_config.model_dump(mode="json")
    if fault == "disabled":
        monkeypatch.setenv("CANDIDATE_INGRESS_NATIVE_ENABLED", "false")
    elif fault == "malformed":
        monkeypatch.setenv("CANDIDATE_INGRESS_NATIVE_CONFIG", "{}")
    elif fault == "duplicate":
        monkeypatch.setenv("CANDIDATE_INGRESS_NATIVE_CONFIG", '{"resource":{},"resource":{}}')
    elif fault == "shared":
        raw["epoch_generation"] = 2
    elif fault == "same_database":
        raw["resource"]["registry"] = raw["resource"]["action_registries"][0]
    elif fault == "secret":
        monkeypatch.setenv("CANDIDATE_AUTH_TRANSPORT_SECRET", "unprovisioned")
    elif fault == "key":
        monkeypatch.setenv("CANDIDATE_AUTH_TRANSPORT_KEY_ID", "other_private_v1")
    elif fault == "emulator":
        monkeypatch.setenv("FIRESTORE_EMULATOR_HOST", "127.0.0.1:8080")
    else:
        monkeypatch.setenv("NEXTAUTH_SECRET", c.key.secret.hex())
    if fault in {"shared", "same_database"}:
        monkeypatch.setenv("CANDIDATE_INGRESS_NATIVE_CONFIG", canonical(raw))
    with pytest.raises(GuardUnavailable):
        production_candidate_ingress()
    with pytest.raises(GuardUnavailable):
        send(c)
    c.client_constructor.assert_not_called()
    assert not c.transport.mutations


@pytest.mark.parametrize("fault", ["uid", "closed", "restore", "custody", "clock_witness", "protected", "epoch", "witness", "resource"])
def test_fresh_native_resource_epoch_control_and_custody_drift_refuse_before_replay(ingress, fault):
    c = ingress
    if fault == "uid":
        c.native.identities[c.ingress_resource.registry.database] = str(uuid4())
    elif fault in {"closed", "restore", "custody", "clock_witness"}:
        resource = c.ingress_resource.model_dump(mode="json")
        if fault == "restore":
            resource["restore_generation"] = 2
        elif fault in {"custody", "clock_witness"}:
            resource[fault + "_id"] = str(uuid4())
        c.transport.seed(c.replay_rpc, CONTROL_NAMESPACE, "root", IngressControl(
            resource=IngressResource.model_validate(resource), state="CLOSED" if fault == "closed" else "OPEN").model_dump(mode="json"))
    elif fault == "protected":
        c.transport.seed(c.rpc, "v3_root", "current", {**c.publication.registry.read("v3_root", "current"), "phase": "CLOSED", "close_id": str(uuid4())})
    elif fault == "epoch":
        c.transport.seed(c.execution_rpc, "pairing_control", "current", pairing_control_record(c.witness.registry, 2))
    else:
        path = c.fence.pin.path if fault == "witness" else c.resource.path
        c.objects[path] = (b"changed", "999")
    with pytest.raises(GuardUnavailable):
        send(c)
    assert not c.transport.mutations


@pytest.mark.parametrize("fault", ["missing_time", "extra_document", "slow", "future", "expired"])
def test_provider_clock_missing_slow_or_outside_assertion_interval_never_admits(ingress, monkeypatch, fault):
    c = ingress
    value = assertion(c)
    if fault == "future":
        value = assertion(c, issued=NOW + 3000, expires=NOW + 5000)
    elif fault == "expired":
        c.transport.clock_offset_ns = 5_000_000_000
    else:
        c.transport.clock_fault = fault
        if fault == "slow":
            monkeypatch.setattr(production, "MAX_READ_LATENCY_NS", 1_000_000)
    with pytest.raises(GuardUnavailable):
        send(c, value)
    assert not c.transport.mutations


@pytest.mark.parametrize("persist", [False, True])
def test_unknown_native_commit_never_authorizes_or_reconstructs_original_ack(ingress, persist):
    c = ingress
    c.transport.commit_fault = "persist" if persist else "no_persist"
    value = assertion(c)
    with pytest.raises(GuardUnavailable):
        send(c, value)
    assert len(c.transport.mutations) == int(persist)
    if persist:
        c.transport.commit_fault = None
        with pytest.raises(GuardUnavailable):
            send(c, value)
        assert len(c.transport.mutations) == 1


def test_concurrent_invocations_keep_separate_clock_anchors_and_one_replay_winner(ingress, monkeypatch):
    c = ingress
    environments = []
    original = production.GcpTrustedIngressEnvironment.__init__
    def capture(self, *args, **kwargs):
        original(self, *args, **kwargs)
        environments.append(self)
    monkeypatch.setattr(production.GcpTrustedIngressEnvironment, "__init__", capture)
    value = assertion(c)
    def contender():
        try:
            send(c, value)
            return "authorized"
        except GuardUnavailable:
            return "refused"
    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(lambda _: contender(), range(2)))
    assert outcomes.count("authorized") == 1 and len(c.transport.mutations) == 1
    assert len(environments) == 2 and environments[0] is not environments[1]
    assert environments[0]._anchor is not environments[1]._anchor


def test_stale_projection_and_late_commit_refuse_without_clock_io_inside_transaction(ingress, monkeypatch):
    c = ingress
    monkeypatch.setattr(production, "MAX_ANCHOR_AGE_NS", 50_000_000)
    c.transport.before_commit = lambda: time.sleep(0.1)
    with pytest.raises(GuardUnavailable):
        send(c)
    assert len(c.transport.mutations) == 1


def test_definite_abort_refreshes_current_epoch_before_any_second_attempt(ingress):
    c = ingress
    calls = []
    def abort_and_close_epoch():
        calls.append(True)
        c.transport.seed(c.execution_rpc, "pairing_control", "current", pairing_control_record(c.witness.registry, 2))
        raise Aborted("Owned definite abort concurrent with operator epoch change")
    c.transport.before_commit = abort_and_close_epoch
    with pytest.raises(GuardUnavailable):
        send(c)
    assert len(calls) == 1 and not c.transport.mutations


@pytest.mark.parametrize("offset", [-100_000_000, 100_000_000])
def test_refreshed_provider_clock_cannot_jump_outside_previous_monotonic_bound(ingress, offset):
    c = ingress
    def jump():
        if len(c.transport.clock_reads) == 2:
            c.transport.clock_offset_ns = offset
    c.transport.before_clock = jump
    with pytest.raises(GuardUnavailable):
        send(c)
    assert not c.transport.mutations


def test_config_disable_after_durable_commit_cannot_export_authorization(ingress, monkeypatch):
    c = ingress
    c.transport.before_commit = lambda: monkeypatch.setenv("CANDIDATE_INGRESS_NATIVE_ENABLED", "false")
    with pytest.raises(GuardUnavailable):
        send(c)
    assert len(c.transport.mutations) == 1
