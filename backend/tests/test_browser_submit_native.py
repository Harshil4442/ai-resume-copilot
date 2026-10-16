"""Real SDK/GAPIC request construction over intercepted local endpoints only.

Retained subject provenance and all deployment pins are synthetic fixtures.
These tests do not establish production IAM, operator enrollment or GCP durability.
"""

from __future__ import annotations

import base64
import copy
import hashlib
import json
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import MagicMock
from urllib.parse import parse_qs, unquote, urlsplit
from uuid import uuid4

import google.auth
import pytest
import requests
from cryptography.hazmat.primitives.asymmetric import ec
from google.api_core.exceptions import Aborted, ServiceUnavailable
from google.auth.credentials import AnonymousCredentials
from google.cloud.firestore_admin_v1.types import Database
from google.cloud.firestore_v1.types import (
    BatchGetDocumentsResponse,
    BeginTransactionResponse,
    CommitResponse,
    Document,
    WriteResult,
)
from google.cloud.storage import Client
from google.protobuf.empty_pb2 import Empty
from google.protobuf.timestamp_pb2 import Timestamp
from test_gcp_journal_sdk import response
from test_gcp_rpc import ScriptedSdk

from app.domains.recovery.browser_submit import (
    SubmitNativeConfiguration,
    production_submit_authority,
)
from app.domains.recovery.contracts import Binding, canonical, fingerprint
from app.domains.recovery.gcp_buffer import BufferedRegistry, BufferedTransaction
from app.domains.recovery.gcp_contracts import JournalReceipt, RegistryPin, WitnessBody, WitnessPin
from app.domains.recovery.gcp_journal import GcsJournal, GcsWitnessFence
from app.domains.recovery.gcp_partitioned_contracts import (
    DirectoryHead,
    GenerationHistory,
    LaneHead,
    PartitionedPin,
    PartitionedResource,
    PartitionedRoot,
    ScopedSubject,
)
from app.domains.recovery.gcp_partitioned_publication import PartitionedPublicationCoordinator
from app.domains.recovery.gcp_password_lifetime_contracts import (
    EnrollPasswordAccount,
    LifetimeGrantEvidence,
    LifetimeRecord,
    PasswordLifetimeEffects,
    PasswordLifetimeIntent,
)
from app.domains.recovery.gcp_publication_contracts import PublicationPin
from app.domains.recovery.gcp_rpc import FirestoreRpc
from app.domains.recovery.gcp_scoped_census import validate_scoped_rows
from app.domains.recovery.gcp_submit import GcpSubmitAuthority
from app.domains.recovery.gcp_submit_contracts import (
    SubmitBinding,
    SubmitContext,
    SubmitDeviceGrant,
    SubmitGrantPin,
)
from app.domains.recovery.pairing_auth import key_fingerprint, public_jwk
from app.domains.recovery.store import GuardDenied, GuardUnavailable

NOW = 1_800_000_000_000


class InterceptedNative:
    def __init__(self):
        self.sdk = ScriptedSdk()
        self.data, self.views, self.reads, self.calls, self.versions = {}, {}, {}, [], 0
        self.fault = None
        self.mutate = None
        self.identities = {}
        transport = self.sdk.client.transport
        transport.begin_transaction.side_effect = self.begin
        transport.batch_get_documents.side_effect = self.read
        transport.commit.side_effect = self.commit
        transport.rollback.side_effect = self.rollback
        self.sdk.admin.transport.get_database.side_effect = self.identity

    def identity(self, request, **kwargs):
        assert not self.views, "Metadata IO entered native transaction"
        return Database(name=request.name, uid=self.identities[request.name], type_=1)

    def begin(self, request, **kwargs):
        token = uuid4().bytes
        self.views[token] = copy.deepcopy(self.data)
        self.reads[token] = set()
        return BeginTransactionResponse(transaction=token)

    def read(self, request, **kwargs):
        view = self.views[request.transaction]
        self.reads[request.transaction].update(request.documents)
        return iter(
            [
                BatchGetDocumentsResponse(
                    found=Document(
                        name=path,
                        fields={"body": {"bytes_value": view[path][0]}},
                        update_time=view[path][1],
                    )
                )
                if path in view
                else BatchGetDocumentsResponse(missing=path)
                for path in request.documents
            ]
        )

    def commit(self, request, **kwargs):
        names = {
            json.loads(write.update.fields["body"].bytes_value)["namespace"]
            for write in request.writes
        }
        self.calls.append(names)
        if self.mutate:
            self.mutate(names)
        fault = self.fault(names) if self.fault else None
        if fault == "abort":
            raise Aborted("Definite intercepted abort")
        if fault == "no_persist_loss":
            self.views.pop(request.transaction)
            self.reads.pop(request.transaction)
            raise ServiceUnavailable("Uncertain intercepted commit")
        for path in self.reads[request.transaction]:
            if self.data.get(path) != self.views[request.transaction].get(path):
                raise Aborted("Intercepted transactional read-set conflict")
        for write in request.writes:
            old = self.data.get(write.update.name)
            condition = write.current_document
            if (
                condition._pb.WhichOneof("condition_type") == "exists"
                and old is not None
                or condition._pb.WhichOneof("condition_type") == "update_time"
                and (old is None or condition._pb.update_time != old[1])
            ):
                raise Aborted("Intercepted read-version conflict")
        self.versions += 1
        version = Timestamp(seconds=1_700_000_000, nanos=self.versions)
        for write in request.writes:
            self.data[write.update.name] = (
                write.update.fields["body"].bytes_value,
                copy.deepcopy(version),
            )
        self.views.pop(request.transaction)
        self.reads.pop(request.transaction)
        if fault == "persist_loss":
            raise ServiceUnavailable("Lost intercepted acknowledgement after commit")
        return CommitResponse(
            commit_time=version,
            write_results=[WriteResult(update_time=version) for _ in request.writes],
        )

    def rollback(self, request, **kwargs):
        self.views.pop(request.transaction, None)
        self.reads.pop(request.transaction, None)
        return Empty()

    def seed(self, registry, namespace, key, value):
        path = BufferedTransaction(registry.rpc, b"seed").path(namespace, key)
        self.data[path] = (
            canonical({"namespace": namespace, "key": key, "value": value}).encode(),
            Timestamp(seconds=1_700_000_000),
        )

    def values(self):
        return {
            (body["namespace"], body["key"]): body["value"]
            for raw, _ in self.data.values()
            for body in [json.loads(raw)]
        }

    def remove(self, registry, namespace, key):
        self.data.pop(BufferedTransaction(registry.rpc, b"seed").path(namespace, key))


@pytest.fixture
def native(monkeypatch):
    monkeypatch.setenv("DISABLE_GCS_PYTHON_CLIENT_OTEL_BUCKET_METADATA", "true")
    monkeypatch.setattr(
        google.auth, "default", lambda *a, **k: pytest.fail("ADC is forbidden in intercepted tests")
    )
    c = SimpleNamespace(
        now=NOW, native=InterceptedNative(), objects={}, http_calls=[], on_http=None
    )
    execution = RegistryPin(
        database="projects/hirewiz-local-authority/databases/submit-execution",
        database_uid=uuid4(),
        authority_id=uuid4(),
        incarnation=1,
        epoch_id=uuid4(),
    )
    protected = RegistryPin(
        database="projects/hirewiz-local-authority/databases/submit-protected",
        database_uid=uuid4(),
        authority_id=uuid4(),
        incarnation=1,
        epoch_id=uuid4(),
    )
    c.native.identities = {
        execution.database: str(execution.database_uid),
        protected.database: str(protected.database_uid),
    }
    c.rpc = FirestoreRpc(
        c.native.sdk.client, c.native.sdk.admin, database_resource=protected.database
    )
    c.registry = BufferedRegistry(c.rpc)
    pin = PartitionedPin(
        authority=PublicationPin(
            registry=protected, target=execution, journal_bucket="synthetic-submit-journal"
        ),
        lanes=2,
        directory_shards=2,
    )
    c.resource = PartitionedResource(
        pin=pin,
        bucket=pin.authority.journal_bucket,
        generation="501",
        sha256=fingerprint(pin.model_dump(mode="json")),
    )
    c.witness = WitnessBody(
        registry=execution,
        state="OPEN",
        manifest_generation="1",
        manifest_sha256="a" * 64,
        partitions_sha256="b" * 64,
    )
    witness_pin = WitnessPin(
        registry=execution,
        bucket=c.resource.bucket,
        path="submit/witness",
        generation="502",
        sha256=fingerprint(c.witness.model_dump(mode="json")),
    )
    c.context = SubmitContext(
        subject_uuid=uuid4(),
        account_binding_id=uuid4(),
        candidate_id=17,
        principal_sha256="a" * 64,
        auth_generation=1,
        credential_sha256="b" * 64,
    )
    numbers = ec.generate_private_key(ec.SECP256R1()).public_key().public_numbers()
    key = public_jwk(
        {
            "kty": "EC",
            "crv": "P-256",
            **{
                name: base64.urlsafe_b64encode(getattr(numbers, name).to_bytes(32, "big"))
                .decode()
                .rstrip("=")
                for name in ("x", "y")
            },
        }
    )
    c.grant = SubmitDeviceGrant(
        pin=execution,
        epoch_generation=1,
        context=c.context,
        device_id=uuid4(),
        public_key=key,
        key_sha256=key_fingerprint(key),
        key_generation=1,
        executor_revision="companion_0_6_0",
        enrollment_provenance_sha256="c" * 64,
    )
    c.grant_pin = SubmitGrantPin(
        bucket=c.resource.bucket,
        path=f"authority-submit-device-grants/{c.grant.device_id}.json",
        generation="503",
        sha256=fingerprint(c.grant.model_dump(mode="json")),
    )
    for path, body, generation in [
        (c.resource.path, pin.model_dump(mode="json"), "501"),
        (witness_pin.path, c.witness.model_dump(mode="json"), "502"),
        (c.grant_pin.path, c.grant.model_dump(mode="json"), "503"),
    ]:
        c.objects[path] = canonical(body).encode(), generation

    def http(method, url, **kwargs):
        assert not c.native.views, "Storage IO entered native transaction"
        assert kwargs["timeout"] == 2
        c.http_calls.append((method, url))
        if c.on_http:
            c.on_http(method, url, kwargs)
        query = parse_qs(urlsplit(url).query)
        if method == "POST":
            body = kwargs["data"]
            path = json.loads(body.split(b"\r\n\r\n", 1)[1].split(b"\r\n--", 1)[0])["name"]
            if path in c.objects:
                return response(b'{"error":{"code":412}}', status=412)
            raw = body.split(b"\r\n\r\n")[2].rsplit(b"\r\n--", 1)[0]
            c.objects[path] = raw, str(600 + len(c.objects))
        else:
            path = unquote(urlsplit(url).path.split("/o/", 1)[1])
        if path not in c.objects:
            return response(b'{"error":{"code":404}}', status=404)
        raw, generation = c.objects[path]
        if query.get("alt") == ["media"]:
            return response(raw)
        return response(
            json.dumps(
                {
                    "name": path,
                    "bucket": c.resource.bucket,
                    "generation": generation,
                    "size": str(len(raw)),
                }
            ).encode()
        )

    session = MagicMock(spec=requests.Session)
    session.is_mtls = False
    session.request.side_effect = http
    c.storage = Client(
        project="hirewiz-local-authority", credentials=AnonymousCredentials(), _http=session
    )
    c.publication = PartitionedPublicationCoordinator(
        c.registry, c.resource, c.storage.bucket(c.resource.bucket)
    )
    c.journal = GcsJournal(c.storage.bucket(c.resource.bucket), publication=c.publication)
    execution_rpc = FirestoreRpc(
        c.native.sdk.client, c.native.sdk.admin, database_resource=execution.database
    )
    c.fence = GcsWitnessFence(
        c.storage.bucket(c.resource.bucket), execution_rpc, witness_pin, c.witness
    )
    c.authority = GcpSubmitAuthority(
        c.publication,
        c.journal,
        c.fence,
        c.grant_pin,
        epoch_generation=1,
        executor_revision=c.grant.executor_revision,
        now_ms=lambda: c.now,
    )
    c.config = SubmitNativeConfiguration(
        execution_witness_pin=witness_pin,
        execution_witness_body=c.witness,
        publication_resource=c.resource,
        device_grant_pin=c.grant_pin,
        epoch_generation=1,
        executor_revision=c.grant.executor_revision,
    )
    c.native.seed(
        c.registry,
        "v3_root",
        "current",
        PartitionedRoot(pin=pin, phase="OPEN").model_dump(mode="json"),
    )
    for lane in range(2):
        c.native.seed(
            c.registry,
            "v3_lanes",
            str(lane),
            LaneHead(lane=lane, segment_count=0, segment_id=None).model_dump(mode="json"),
        )
        c.native.seed(
            c.registry,
            "v3_directory",
            str(lane),
            DirectoryHead(shard=lane, count=0, digest="0" * 64).model_dump(mode="json"),
        )
    # Synthetic pre-retained enrollment provenance; genuine own-ACK activation
    # is covered by the separate scoped lifetime integration suite.
    enrollment = uuid4()
    command = EnrollPasswordAccount(
        **c.context.model_dump(exclude={"auth_generation"}), subject_registration_event_id=uuid4()
    )
    intent = PasswordLifetimeIntent(
        operation_id=enrollment,
        event_id=uuid4(),
        pin=execution,
        epoch_generation=1,
        command=command,
        subject_uuid=c.context.subject_uuid,
        created_at_ms=NOW - 1000,
        deadline_ms=NOW + 59_000,
        effects=PasswordLifetimeEffects(
            before=(
                LifetimeRecord(namespace="subjects", key=str(c.context.subject_uuid), value=None),
            ),
            after=(
                LifetimeRecord(
                    namespace="subjects", key=str(c.context.subject_uuid), value={"synthetic": True}
                ),
            ),
            event={"synthetic": True},
        ),
    )
    c.publication.retain(canonical(intent.model_dump(mode="json")).encode(), c.resource.bucket)
    path, raw = GcsJournal._intent_bytes(intent)
    receipt = JournalReceipt(
        bucket=c.resource.bucket, path=path, generation="1", sha256=hashlib.sha256(raw).hexdigest()
    )
    evidence = LifetimeGrantEvidence(
        intent=intent,
        status={
            "status": "COMMITTED",
            "operation_id": str(enrollment),
            "intent_sha256": intent.digest,
            "journal": receipt.model_dump(mode="json"),
        },
    )
    subject = ScopedSubject(**c.context.model_dump(), enrollment_operation=enrollment)
    owner = subject.model_dump(
        mode="json",
        include={"subject_uuid", "account_binding_id", "candidate_id", "enrollment_operation"},
    )
    for namespace, identity, body in [
        ("v3_activations", str(enrollment), evidence.model_dump(mode="json")),
        ("v3_subjects", str(subject.subject_uuid), subject.model_dump(mode="json")),
        ("v3_account_owners", str(subject.account_binding_id), owner),
        ("v3_candidate_owners", str(subject.candidate_id), owner),
        (
            "v3_generation_history",
            f"{subject.subject_uuid}:1",
            GenerationHistory(
                subject_uuid=subject.subject_uuid,
                previous_generation=0,
                auth_generation=1,
                operation_id=enrollment,
                credential_sha256=subject.credential_sha256,
            ).model_dump(mode="json"),
        ),
    ]:
        c.native.seed(c.registry, namespace, identity, body)
    c.authority.provision_device()
    yield c
    c.storage.close()


def binding(c, **changes):
    values = dict(
        action="submit",
        candidate_id=17,
        device_id=c.grant.device_id,
        device_key_sha256=c.grant.key_sha256,
        device_key_generation=1,
        executor_revision=c.grant.executor_revision,
        review_id=uuid4(),
        review_digest="1" * 64,
        application_id=str(uuid4()),
        target_url="https://job-boards.greenhouse.io/razorpaysoftwareprivatelimited/jobs/4740333005",
        employer_key="2" * 64,
        opening_key="3" * 64,
        artifact_id=str(uuid4()),
        artifact_sha256="4" * 64,
        artifact_generation="7",
        package_digest="5" * 64,
        policy_sha256="6" * 64,
        pricing_sha256="7" * 64,
        admission_id=str(uuid4()),
        admission_digest="8" * 64,
        upload_evidence_sha256="9" * 64,
        context_digest="a" * 64,
        document_id="owned-chrome-document",
        approved_at_ms=NOW - 100,
        review_expires_at_ms=NOW + 60_000,
        issued_at_ms=NOW,
        deadline_ms=NOW + 10_000,
    )
    return SubmitBinding(**{**values, **changes})


def test_exact_native_begin_and_original_single_use_export(native):
    c = native
    action = binding(c)
    ack = c.authority.reserve(c.context, action)
    assert ack is not None
    proof = c.authority.consume_ack(ack, action)
    assert (
        proof["binding_sha256"] == action.digest
        and proof["employer_receipt"] is False
        and proof["settlement_authorized"] is False
    )
    with pytest.raises(GuardDenied):
        c.authority.consume_ack(ack, action)
    with pytest.raises(GuardDenied):
        c.authority.reserve(c.context, binding(c))
    assert not c.native.views
    validate_scoped_rows(c.native.values())
    for call in c.native.sdk.client.commit.call_args_list:
        assert call.kwargs["retry"] is None and call.kwargs["timeout"] == 2.0


@pytest.mark.parametrize("persisted", [False, True])
def test_unknown_begin_never_exports_or_retries_even_with_new_operation(native, persisted):
    c = native
    c.native.fault = lambda names: (
        ("persist_loss" if persisted else "no_persist_loss")
        if "v3_submit_begins" in names
        else None
    )
    assert c.authority.reserve(c.context, binding(c)) is None
    c.native.fault = None
    with pytest.raises(GuardDenied):
        c.authority.reserve(c.context, binding(c))
    assert not any(namespace == "v3_submit_exports" for namespace, _ in c.native.values())
    validate_scoped_rows(c.native.values())


@pytest.mark.parametrize("persisted", [False, True])
def test_unknown_full_publication_never_reconstructs_a_begin(native, persisted):
    c = native
    c.native.fault = lambda names: (
        ("persist_loss" if persisted else "no_persist_loss")
        if "v3_submit_reservations" in names
        else None
    )
    with pytest.raises(GuardUnavailable):
        c.authority.reserve(c.context, binding(c))
    c.native.fault = None
    assert not any(namespace == "v3_submit_begins" for namespace, _ in c.native.values())
    if persisted:
        with pytest.raises(GuardDenied):
            c.authority.reserve(c.context, binding(c))
    validate_scoped_rows(c.native.values())


def test_lost_journal_ack_cannot_export_or_create_another_attempt(native):
    c = native

    def lose(method, url, kwargs):
        if method == "POST":
            raise requests.ConnectionError("Local unknown upload acknowledgement")

    c.on_http = lose
    with pytest.raises(GuardUnavailable):
        c.authority.reserve(c.context, binding(c))
    c.on_http = None
    with pytest.raises(GuardDenied):
        c.authority.reserve(c.context, binding(c))
    assert not any(namespace == "v3_submit_begins" for namespace, _ in c.native.values())


@pytest.mark.parametrize("stage", ["journal", "begin_conflict", "export_conflict"])
def test_current_subject_denial_wins_between_native_phases(native, stage):
    c = native
    action = binding(c)

    def deny():
        c.native.seed(c.registry, "v3_subject_tombstones", str(c.context.subject_uuid), {})

    if stage == "journal":

        def during_journal(method, url, kwargs):
            if method == "POST":
                deny()

        c.on_http = during_journal
    else:
        namespace = "v3_submit_begins" if stage == "begin_conflict" else "v3_submit_exports"

        def conflicting_commit(names):
            if namespace in names:
                deny()
                c.native.mutate = None

        c.native.mutate = conflicting_commit
    if stage == "export_conflict":
        ack = c.authority.reserve(c.context, action)
        with pytest.raises(GuardDenied):
            c.authority.consume_ack(ack, action)
        with pytest.raises(GuardDenied):
            c.authority.consume_ack(ack, action)
    else:
        with pytest.raises(GuardDenied):
            c.authority.reserve(c.context, action)
    assert not any(namespace == "v3_submit_exports" for namespace, _ in c.native.values())


def test_server_or_browser_device_binding_cannot_implicitly_provision_native_device(native):
    c = native
    c.native.remove(c.registry, "v3_submit_devices", str(c.grant.device_id))
    c.native.remove(c.registry, "v3_submit_key_owners", c.grant.key_sha256)
    with pytest.raises((GuardDenied, ValueError)):
        c.authority.reserve(c.context, binding(c))
    assert ("v3_submit_devices", str(c.grant.device_id)) not in c.native.values()


@pytest.mark.parametrize(
    "fault",
    [
        "subject_denied",
        "pending_denial",
        "generation",
        "device_denied",
        "key_owner",
        "epoch",
        "grant",
        "deadline",
    ],
)
def test_fresh_native_lifetime_device_and_epoch_fail_closed(native, fault):
    c = native
    action = binding(c)
    if fault == "subject_denied":
        c.native.seed(c.registry, "v3_subject_tombstones", str(c.context.subject_uuid), {})
    if fault == "pending_denial":
        c.native.seed(c.registry, "v3_pending_subject_denials", f"{c.context.subject_uuid}:1", {})
    if fault == "generation":
        c.native.seed(c.registry, "v3_generation_history", f"{c.context.subject_uuid}:2", {})
    if fault == "device_denied":
        c.native.seed(c.registry, "v3_submit_device_tombstones", str(c.grant.device_id), {})
    if fault == "key_owner":
        c.native.seed(c.registry, "v3_submit_key_owners", c.grant.key_sha256, {})
    if fault == "epoch":
        c.native.identities[c.resource.pin.authority.registry.database] = str(uuid4())
    if fault == "grant":
        c.objects[c.grant_pin.path] = b"{}", "999"
    if fault == "deadline":
        c.now = action.deadline_ms
    with pytest.raises((GuardDenied, GuardUnavailable, ValueError)):
        c.authority.reserve(c.context, action)
    assert not any(namespace == "v3_submit_begins" for namespace, _ in c.native.values())


def test_cloned_ack_and_restart_cannot_reconstruct_authority(native):
    c = native
    action = binding(c)
    ack = c.authority.reserve(c.context, action)
    with pytest.raises(GuardDenied):
        c.authority.consume_ack(replace(ack), action)
    with pytest.raises(GuardDenied):
        c.authority.consume_ack(ack, action)
    other = GcpSubmitAuthority(
        c.publication,
        c.journal,
        c.fence,
        c.grant_pin,
        epoch_generation=1,
        executor_revision=c.grant.executor_revision,
        now_ms=lambda: c.now,
    )
    with pytest.raises(GuardDenied):
        other.consume_ack(ack, action)
    with pytest.raises(GuardDenied):
        other.reserve(c.context, binding(c))


@pytest.mark.parametrize("persisted", [False, True])
def test_unknown_export_burns_original_ack_and_opening(native, persisted):
    c = native
    action = binding(c)
    ack = c.authority.reserve(c.context, action)
    c.native.fault = lambda names: (
        ("persist_loss" if persisted else "no_persist_loss")
        if "v3_submit_exports" in names
        else None
    )
    with pytest.raises(GuardUnavailable):
        c.authority.consume_ack(ack, action)
    c.native.fault = None
    with pytest.raises(GuardDenied):
        c.authority.consume_ack(ack, action)
    with pytest.raises(GuardDenied):
        c.authority.reserve(c.context, binding(c))


def test_factory_is_closed_by_default_and_validates_without_sdk_io(native, monkeypatch):
    c = native
    with pytest.raises(GuardUnavailable):
        production_submit_authority()
    monkeypatch.setenv("BROWSER_SUBMIT_NATIVE_ENABLED", "true")
    monkeypatch.setenv("BROWSER_SUBMIT_NATIVE_CONFIG", canonical(c.config.model_dump(mode="json")))
    monkeypatch.setenv("BROWSER_ASSISTANCE_SUBMIT_EXECUTOR_REVISION", c.grant.executor_revision)
    from app.domains.recovery import browser_submit

    build = MagicMock(
        side_effect=AssertionError("Structural factory must not construct SDK clients")
    )
    monkeypatch.setattr(browser_submit, "_sdk_authority", build)
    configured = production_submit_authority()
    build.assert_not_called()
    monkeypatch.setenv("BROWSER_SUBMIT_NATIVE_ENABLED", "false")
    with pytest.raises(GuardUnavailable):
        configured.reserve(c.context, binding(c))
    build.assert_not_called()


def test_real_factory_composes_native_sdk_clients_only_at_reserve(native, monkeypatch):
    c = native
    from google.cloud import storage
    from google.cloud.firestore_admin_v1.services import firestore_admin
    from google.cloud.firestore_v1.services import firestore

    from app.domains.recovery import browser_submit

    monkeypatch.setenv("BROWSER_SUBMIT_NATIVE_ENABLED", "true")
    monkeypatch.setenv("BROWSER_SUBMIT_NATIVE_CONFIG", canonical(c.config.model_dump(mode="json")))
    monkeypatch.setenv("BROWSER_ASSISTANCE_SUBMIT_EXECUTOR_REVISION", c.grant.executor_revision)
    client = MagicMock(return_value=c.native.sdk.client)
    admin = MagicMock(return_value=c.native.sdk.admin)
    storage_client = MagicMock(return_value=c.storage)
    monkeypatch.setattr(firestore, "FirestoreClient", client)
    monkeypatch.setattr(firestore_admin, "FirestoreAdminClient", admin)
    monkeypatch.setattr(storage, "Client", storage_client)
    monkeypatch.setattr(browser_submit.time, "time_ns", lambda: NOW * 1_000_000)
    gate = production_submit_authority()
    client.assert_not_called()
    admin.assert_not_called()
    storage_client.assert_not_called()
    action = binding(c)
    ack = gate.reserve(c.context, action)
    assert ack is not None
    proof = gate.consume_ack(ack, action)
    assert proof["binding_sha256"] == action.digest
    client.assert_called_once_with()
    admin.assert_called_once_with()
    storage_client.assert_called_once_with(project="hirewiz-local-authority")
    for rpc in (
        c.native.sdk.client.begin_transaction,
        c.native.sdk.client.batch_get_documents,
        c.native.sdk.client.commit,
        c.native.sdk.client.rollback,
        c.native.sdk.admin.get_database,
    ):
        for call in rpc.call_args_list:
            assert call.kwargs["retry"] is None and call.kwargs["timeout"] == 2.0
    validate_scoped_rows(c.native.values())


def test_submit_contract_does_not_broaden_legacy_fill_binding(native):
    with pytest.raises(ValueError):
        Binding.model_validate(binding(native).model_dump(mode="json"))
    with pytest.raises(ValueError):
        binding(native, action="fill")
    with pytest.raises(ValueError):
        binding(native, deadline_ms=NOW + 10_001)
