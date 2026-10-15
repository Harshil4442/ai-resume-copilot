"""Actual SDK requests on intercepted local transports; no manager/ADC/provider IO."""

from __future__ import annotations

import json
import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import MagicMock, Mock, create_autospec
from uuid import uuid4

import google.auth
import google_crc32c
import pytest
import requests
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from google.api_core.exceptions import NotFound, ServiceUnavailable
from google.auth.credentials import AnonymousCredentials
from google.cloud import kms_v1, storage
from google.cloud.firestore_admin_v1.types import Database
from google.cloud.firestore_v1.types import CommitResponse, Document, RunQueryResponse, WriteResult
from google.cloud.kms_v1.services.key_management_service.transports.base import (
    DEFAULT_CLIENT_INFO,
    KeyManagementServiceTransport,
)
from google.cloud.kms_v1.services.key_management_service.transports.grpc import (
    KeyManagementServiceGrpcTransport,
)
from google.longrunning.operations_pb2 import Operation
from google.protobuf.any_pb2 import Any
from google.protobuf.timestamp_pb2 import Timestamp
from test_gcp_journal_sdk import response
from test_gcp_rpc import ScriptedSdk

from scripts import bootstrap_candidate_native_authority as bootstrap


@pytest.fixture
def plan():
    return bootstrap.BootstrapPlan(
        project_number="157225724590",
        location="us-central1",
        execution_database_id="fresh-candidate-execution",
        protected_database_id="fresh-candidate-protected",
        replay_database_id="fresh-candidate-replay",
        journal_bucket="owned-synthetic-bootstrap-journal",
        bucket_location="US-CENTRAL1",
        minimum_locked_retention_seconds=604800,
        kms_key_ring_id="fresh-candidate-ring",
        assertion_key_id="candidate-assertion",
        claim_key_id="candidate-claim",
        website_origin="https://www.hirewizhq.com",
        ingress_key_id="candidate_auth_v1",
        lanes=2,
        directory_shards=2,
    )


@pytest.fixture
def native(monkeypatch, plan, tmp_path):
    monkeypatch.setattr(google.auth, "default", lambda *a, **k: pytest.fail("ADC forbidden"))
    monkeypatch.setenv("DISABLE_GCS_PYTHON_CLIENT_OTEL_BUCKET_METADATA", "true")
    c = SimpleNamespace(
        sdk=ScriptedSdk(),
        databases={},
        documents={},
        objects={},
        keys={},
        rings={},
        writes=[],
        native_calls=[],
        fault=None,
        fault_after=None,
        invalid_uid=False,
        changed_creation_uid=False,
        missing_creation_ack=False,
        contaminated=False,
        bucket_locked=True,
    )
    journal = bootstrap.Journal(tmp_path)
    c.guard = Mock()
    monkeypatch.setattr(bootstrap, "validate_manager", c.guard)

    def check_write(kind):
        intents = list(tmp_path.glob("*.intent"))
        assert len(intents) == len(c.writes) + 1, "Fsynced intent precedes each provider write"
        assert json.loads(sorted(intents)[-1].read_bytes())["state"] == "UNKNOWN_UNTIL_ORIGINAL_ACK"
        c.writes.append(kind)
        if c.fault == kind:
            raise ServiceUnavailable("Synthetic lost provider acknowledgement")

    def create_database(request, **kwargs):
        check_write("database")
        name = request.parent + "/databases/" + request.database_id
        assert request.database.type_ == Database.DatabaseType.FIRESTORE_NATIVE
        assert (
            request.database.delete_protection_state
            == Database.DeleteProtectionState.DELETE_PROTECTION_ENABLED
        )
        actual = Database(
            name=name,
            uid="not-an-actual-uid" if c.invalid_uid else str(uuid4()),
            location_id=request.database.location_id,
            type_=request.database.type_,
            delete_protection_state=request.database.delete_protection_state,
            point_in_time_recovery_enablement=request.database.point_in_time_recovery_enablement,
            create_time=Timestamp(seconds=1_800_000_000),
        )
        c.databases[name] = actual
        packed = Any()
        packed.Pack(actual._pb)
        return Operation(
            name=name + "/operations/owned-create",
            done=True,
            response=None if c.missing_creation_ack else packed,
        )

    def get_database(request, **kwargs):
        if request.name not in c.databases:
            raise NotFound("Owned absent database")
        value = Database(c.databases[request.name])
        if c.changed_creation_uid:
            value.uid = str(uuid4())
        return value

    c.sdk.admin.transport.create_database.side_effect = create_database
    c.sdk.admin.transport.get_database.side_effect = get_database

    def commit(request, **kwargs):
        check_write("records")
        for write in request.writes:
            assert write.current_document._pb.WhichOneof("condition_type") == "exists"
            assert write.current_document.exists is False
            assert write.update.name not in c.documents
            body = bytes(write.update.fields["body"].bytes_value)
            envelope = json.loads(body)
            assert envelope["namespace"] not in {
                "subjects",
                "devices",
                "v3_subjects",
                "v3_submit_devices",
                "pairing_account_bindings",
            }
            c.documents[write.update.name] = body
        if c.fault_after == "records":
            raise ServiceUnavailable("Synthetic acknowledgement lost after durable commit")
        return CommitResponse(
            commit_time=Timestamp(seconds=1_800_000_000),
            write_results=[
                WriteResult(update_time=Timestamp(seconds=1_800_000_000)) for _ in request.writes
            ],
        )

    def run_query(request, **kwargs):
        assert request.structured_query.from_[0].collection_id == "authority_records"
        assert request.structured_query.from_[0].all_descendants is True
        found = [
            RunQueryResponse(document=Document(name=name, fields={"body": {"bytes_value": raw}}))
            for name, raw in sorted(c.documents.items())
            if name.startswith(request.parent + "/")
        ]
        if c.contaminated:
            found.append(
                RunQueryResponse(
                    document=Document(
                        name=request.parent + "/authority_records/unexpected",
                        fields={"body": {"bytes_value": b"{}"}},
                    )
                )
            )
        assert len(found) <= request.structured_query.limit
        return iter(found)

    c.sdk.client.transport.commit.side_effect = commit
    c.sdk.client.transport.run_query.side_effect = run_query

    def http(method, url, **kwargs):
        from urllib.parse import parse_qs, unquote, urlsplit

        assert kwargs["timeout"] == 2.0
        parsed = urlsplit(url)
        query = parse_qs(parsed.query)
        if parsed.path.endswith("/b/" + plan.journal_bucket):
            assert method == "GET"
            return response(
                json.dumps(
                    {
                        "name": plan.journal_bucket,
                        "projectNumber": plan.project_number,
                        "location": plan.bucket_location,
                        "iamConfiguration": {"uniformBucketLevelAccess": {"enabled": True}},
                        "versioning": {"enabled": True},
                        "retentionPolicy": {
                            "isLocked": c.bucket_locked,
                            "retentionPeriod": str(plan.minimum_locked_retention_seconds),
                        },
                    }
                ).encode()
            )
        if method == "POST":
            check_write("object")
            assert query["ifGenerationMatch"] == ["0"]
            data = kwargs["data"]
            path = json.loads(data.split(b"\r\n\r\n", 1)[1].split(b"\r\n--", 1)[0])["name"]
            assert path not in c.objects
            c.objects[path] = (
                data.split(b"\r\n\r\n")[2].rsplit(b"\r\n--", 1)[0],
                str(100 + len(c.objects)),
            )
        else:
            path = unquote(parsed.path.split("/o/", 1)[1])
        raw, generation = c.objects[path]
        if query.get("alt") == ["media"]:
            return response(raw)
        return response(
            json.dumps(
                {
                    "name": path,
                    "bucket": plan.journal_bucket,
                    "generation": generation,
                    "size": str(len(raw)),
                }
            ).encode()
        )

    http_session = MagicMock(spec=requests.Session)
    http_session.is_mtls = False
    http_session.request.side_effect = http
    c.storage = storage.Client(
        project=plan.project, credentials=AnonymousCredentials(), _http=http_session
    )
    transport = create_autospec(KeyManagementServiceGrpcTransport, instance=True)
    transport.host = "local-contract.invalid:443"
    transport._credentials = None

    def ring(request, **kwargs):
        check_write("ring")
        name = request.parent + "/keyRings/" + request.key_ring_id
        c.rings[name] = kms_v1.KeyRing(name=name)
        return c.rings[name]

    def get_ring(request, **kwargs):
        if request.name not in c.rings:
            raise NotFound("Owned absent ring")
        return c.rings[request.name]

    def key(request, **kwargs):
        check_write("key")
        name = request.parent + "/cryptoKeys/" + request.crypto_key_id
        assert not request.skip_initial_version_creation
        c.keys[name] = ec.generate_private_key(ec.SECP256R1())
        return kms_v1.CryptoKey(
            name=name,
            purpose=request.crypto_key.purpose,
            version_template=request.crypto_key.version_template,
        )

    def versions(request, **kwargs):
        return kms_v1.ListCryptoKeyVersionsResponse(
            crypto_key_versions=[
                kms_v1.CryptoKeyVersion(
                    name=request.parent + "/cryptoKeyVersions/1",
                    state=kms_v1.CryptoKeyVersion.CryptoKeyVersionState.ENABLED,
                    algorithm=kms_v1.CryptoKeyVersion.CryptoKeyVersionAlgorithm.EC_SIGN_P256_SHA256,
                    protection_level=kms_v1.ProtectionLevel.SOFTWARE,
                )
            ]
        )

    def public(request, **kwargs):
        key_name = request.name.split("/cryptoKeyVersions/")[0]
        pem = (
            c.keys[key_name]
            .public_key()
            .public_bytes(
                serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
            )
            .decode()
        )
        return kms_v1.PublicKey(
            name=request.name,
            pem=pem,
            pem_crc32c=google_crc32c.value(pem.encode()),
            algorithm=kms_v1.CryptoKeyVersion.CryptoKeyVersionAlgorithm.EC_SIGN_P256_SHA256,
            protection_level=kms_v1.ProtectionLevel.SOFTWARE,
        )

    transport.create_key_ring.side_effect = ring
    transport.get_key_ring.side_effect = get_ring
    transport.create_crypto_key.side_effect = key
    transport.list_crypto_key_versions.side_effect = versions
    transport.get_public_key.side_effect = public
    KeyManagementServiceTransport._prep_wrapped_messages(transport, DEFAULT_CLIENT_INFO)
    c.kms = kms_v1.KeyManagementServiceClient(transport=transport)
    c.clients = bootstrap.Clients(c.sdk.admin, c.sdk.client, c.storage, c.kms)
    c.runner = bootstrap.Bootstrap(plan, c.clients, journal)
    c.journal = journal
    for client, names in [
        (c.sdk.admin, ("create_database", "get_database")),
        (c.sdk.client, ("commit", "run_query")),
        (
            c.kms,
            (
                "create_key_ring",
                "get_key_ring",
                "create_crypto_key",
                "list_crypto_key_versions",
                "get_public_key",
            ),
        ),
    ]:
        for name in names:
            original = getattr(client, name)

            def checked(*args, _method=original, _name=name, **kwargs):
                assert kwargs.get("retry", "missing") is None and kwargs["timeout"] == 2.0
                c.native_calls.append(_name)
                return _method(*args, **kwargs)

            setattr(client, name, checked)
    try:
        yield c
    finally:
        c.storage.close()


def test_full_actual_sdk_bootstrap_has_only_operator_baselines_and_public_disabled_configs(
    native, plan
):
    result = native.runner.execute()
    assert (
        result["state"] == "BOOTSTRAPPED_NOT_ACTIVATED"
        and result["runtime_activation_allowed"] is False
    )
    assert not any(result["enable_flags"].values())
    assert len(native.databases) == 3 and len(native.keys) == 2 and len(native.objects) == 3
    assert (
        native.writes
        == ["database"] * 3 + ["records"] * 3 + ["object"] * 3 + ["ring"] + ["key"] * 2
    )
    assert native.guard.call_count == len(native.writes)
    account = result["CANDIDATE_ACCOUNT_NATIVE_CONFIG"]
    assert (
        account["execution_witness_pin"]["registry"]["database_uid"]
        == native.databases[plan.database_names[0]].uid
    )
    assert result["BROWSER_SUBMIT_NATIVE_CONFIG"]["device_enrollment"] == "paired"
    assert "device_grant_pin" not in result["BROWSER_SUBMIT_NATIVE_CONFIG"]
    assert (
        result["companion_nativePairing"]["authorityKey"]
        == result["BROWSER_PAIRING_NATIVE_CONFIG"]["claim_key"]["public_key"]
    )
    assert "access_token" not in json.dumps(result) and "refresh_token" not in json.dumps(result)
    assert len(list(native.journal.directory.glob("*.intent"))) == 12
    with pytest.raises(FileExistsError):
        native.runner.execute()
    assert len(native.writes) == 12


@pytest.mark.parametrize("phase", ["database", "records", "object", "ring", "key"])
def test_uncertain_write_is_durable_unknown_never_repeated_or_adopted(native, phase):
    native.fault = phase
    with pytest.raises(bootstrap.BootstrapUnknown):
        native.runner.execute()
    assert len(list(native.journal.directory.glob("*.unknown"))) == 1
    previous = len(native.writes)
    with pytest.raises(FileExistsError):
        native.runner.execute()
    assert len(native.writes) == previous
    assert not (native.journal.directory / "result.json").exists()


def test_readonly_reconciliation_never_writes_or_returns_usable_config(native, plan):
    native.fault = "records"
    with pytest.raises(bootstrap.BootstrapUnknown):
        native.runner.execute()
    before = (
        tuple(native.writes),
        sorted(path.name for path in native.journal.directory.iterdir()),
    )
    result = bootstrap.reconcile(plan, native.clients, native.journal)
    assert (
        result["state"] == "READ_ONLY_RECONCILIATION"
        and result["runtime_activation_allowed"] is False
    )
    assert "CANDIDATE_ACCOUNT_NATIVE_CONFIG" not in result
    assert before == (
        tuple(native.writes),
        sorted(path.name for path in native.journal.directory.iterdir()),
    )


def test_default_plan_does_not_resolve_manager_adc_clients_or_write_files(
    plan, tmp_path, monkeypatch, capsys
):
    path = tmp_path / "public-plan.json"
    path.write_text(plan.model_dump_json())
    path.chmod(0o600)
    monkeypatch.setattr(
        bootstrap, "manager_credentials", lambda *_: pytest.fail("No manager in plan")
    )
    monkeypatch.setattr(bootstrap, "clients", lambda *_: pytest.fail("No clients in plan"))
    assert bootstrap.main(["--plan-file", str(path)]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["state"] == "PLAN_ONLY" and result["runtime_activation_allowed"] is False
    assert sorted(child.name for child in tmp_path.iterdir()) == ["public-plan.json"]


def test_commit_ack_loss_after_durable_rows_cannot_resume_or_adopt(native):
    native.fault_after = "records"
    with pytest.raises(bootstrap.BootstrapUnknown):
        native.runner.execute()
    assert len(native.documents) == 5 and len(native.databases) == 3
    marker = native.journal.read("004_records.unknown")
    assert marker["state"] == "UNKNOWN" and marker["automatic_replay"] is False
    assert not (native.journal.directory / "004_records.ack").exists()
    with pytest.raises(FileExistsError):
        native.runner.execute()
    result = bootstrap.reconcile(native.runner.plan, native.clients, native.journal)
    assert result["runtime_activation_allowed"] is False
    assert "CANDIDATE_ACCOUNT_NATIVE_CONFIG" not in result


@pytest.mark.parametrize(
    "fault", ["invalid_uid", "changed_creation_uid", "missing_creation_ack", "contaminated"]
)
def test_changed_native_identity_or_unexpected_initial_record_refuses_config(native, fault):
    setattr(native, fault, True)
    with pytest.raises(bootstrap.BootstrapUnknown):
        native.runner.execute()
    assert not (native.journal.directory / "result.json").exists()
    assert not native.objects and not native.keys


def test_bucket_must_have_reviewed_locked_retention_before_any_write(native):
    native.bucket_locked = False
    with pytest.raises(bootstrap.BootstrapDenied, match="protected_bucket_policy"):
        native.runner.execute()
    assert not native.writes


def test_existing_database_is_never_adopted(native, plan):
    native.databases[plan.database_names[0]] = Database(
        name=plan.database_names[0], uid=str(uuid4())
    )
    with pytest.raises(bootstrap.BootstrapDenied, match="already_exists_no_adoption"):
        native.runner.execute()
    assert not native.writes


def test_readonly_reconciliation_observes_attempted_objects_without_ack_recovery(native, plan):
    native.fault = "ring"
    with pytest.raises(bootstrap.BootstrapUnknown):
        native.runner.execute()
    before = tuple(native.writes)
    result = bootstrap.reconcile(plan, native.clients, native.journal)
    assert len(result["objects"]) == 3
    assert {v["observation"] for v in result["objects"]} == {"METADATA_ONLY_NOT_ORIGINAL_ACK"}
    assert tuple(native.writes) == before and "BROWSER_PAIRING_NATIVE_CONFIG" not in result
    changed = plan.model_copy(update={"executor_revision": "other"})
    with pytest.raises(bootstrap.BootstrapDenied, match="plan_mismatch"):
        bootstrap.reconcile(changed, native.clients, native.journal)


def fake_manager(monkeypatch):
    modules = {
        name: ModuleType(name)
        for name in (
            "googlecloudsdk",
            "googlecloudsdk.core",
            "googlecloudsdk.core.config",
            "googlecloudsdk.core.credentials",
            "googlecloudsdk.core.credentials.store",
            "native_fresh_fence",
        )
    }
    config = modules["googlecloudsdk.core.config"]
    config.__file__ = str(bootstrap.SDK_LIBRARY / "googlecloudsdk" / "core" / "config.py")
    config.CLOUD_SDK_VERSION = "578.0.0"
    config.Paths = lambda: SimpleNamespace(global_config_dir=str(bootstrap.MANAGER_DIRECTORY))
    reads = Mock()
    modules["native_fresh_fence"].SDKGoogleReads = reads
    load = Mock(return_value=AnonymousCredentials())
    modules["googlecloudsdk.core.credentials.store"].Load = load
    for name, module in modules.items():
        monkeypatch.setitem(sys.modules, name, module)
    return SimpleNamespace(config=config, reads=reads, load=load)


def test_manager_exact_account_path_version_and_no_impersonation(plan, monkeypatch):
    fake = fake_manager(monkeypatch)
    for name in (
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "ALL_PROXY",
        "http_proxy",
        "https_proxy",
        "all_proxy",
        "GOOGLE_APPLICATION_CREDENTIALS",
        "CLOUDSDK_AUTH_IMPERSONATE_SERVICE_ACCOUNT",
        "CLOUDSDK_AUTH_CREDENTIAL_FILE_OVERRIDE",
        "CLOUDSDK_AUTH_ACCESS_TOKEN_FILE",
        "REQUESTS_CA_BUNDLE",
        "CURL_CA_BUNDLE",
        "GRPC_DEFAULT_SSL_ROOTS_FILE_PATH",
        "GRPC_TRACE",
        "GRPC_VERBOSITY",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("DISABLE_GCS_PYTHON_CLIENT_OTEL_BUCKET_METADATA", "true")
    monkeypatch.setattr(google.auth, "default", lambda *a, **k: pytest.fail("ADC forbidden"))
    assert bootstrap.manager_credentials(plan) is fake.load.return_value
    fake.reads.assert_called_once_with(bootstrap.SDK_LIBRARY, bootstrap.ACCOUNT)
    fake.reads.return_value._http.assert_called_once_with()
    fake.load.assert_called_once_with(
        account="harshilyogeshbhai886@gmail.com", allow_account_impersonation=False
    )
    fake.config.Paths = lambda: SimpleNamespace(global_config_dir="/tmp/not-normal-manager")
    with pytest.raises(bootstrap.BootstrapDenied, match="manager_directory"):
        bootstrap.manager_credentials(plan)
    assert fake.load.call_count == 1
    fake.config.Paths = lambda: SimpleNamespace(global_config_dir=str(bootstrap.MANAGER_DIRECTORY))
    fake.config.CLOUD_SDK_VERSION = "0.0.0"
    with pytest.raises(bootstrap.BootstrapDenied, match="sdk_version_or_location"):
        bootstrap.manager_credentials(plan)
    assert fake.load.call_count == 1


@pytest.mark.parametrize(
    "name",
    [
        "GOOGLE_APPLICATION_CREDENTIALS",
        "HTTPS_PROXY",
        "CLOUDSDK_AUTH_IMPERSONATE_SERVICE_ACCOUNT",
        "CLOUDSDK_AUTH_CREDENTIAL_FILE_OVERRIDE",
        "GRPC_TRACE",
        "GRPC_DEFAULT_SSL_ROOTS_FILE_PATH",
        "STORAGE_EMULATOR_HOST",
        "FIRESTORE_EMULATOR_HOST",
    ],
)
def test_manager_refuses_override_before_loading_credentials(plan, monkeypatch, name):
    fake = fake_manager(monkeypatch)
    monkeypatch.setenv(name, "forbidden")
    with pytest.raises(bootstrap.BootstrapDenied, match="override_proxy_or_adc"):
        bootstrap.manager_credentials(plan)
    fake.load.assert_not_called()
    fake.reads.assert_not_called()


def test_private_journal_refuses_symlink_or_nonprivate_directory(tmp_path):
    tmp_path.chmod(0o755)
    with pytest.raises(bootstrap.BootstrapDenied, match="private_journal"):
        bootstrap.Journal(tmp_path)
    tmp_path.chmod(0o700)
    link = tmp_path / "link"
    link.symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(bootstrap.BootstrapDenied, match="private_journal"):
        bootstrap.Journal(link)
    journal = bootstrap.Journal(tmp_path)
    journal.append("plan.json", {"safe": True})
    assert journal.read("plan.json") == {"safe": True}
    with pytest.raises(FileExistsError):
        journal.append("plan.json", {"overwrite": True})
    (tmp_path / "plan.json").chmod(0o644)
    with pytest.raises(bootstrap.BootstrapDenied, match="journal_file_refused"):
        journal.read("plan.json")


def test_real_client_factory_uses_only_explicit_credential_and_official_bounded_channels(
    plan, monkeypatch
):
    import grpc
    from google.cloud.firestore_admin_v1.services.firestore_admin import FirestoreAdminClient
    from google.cloud.firestore_admin_v1.services.firestore_admin.transports.grpc import (
        FirestoreAdminGrpcTransport,
    )
    from google.cloud.firestore_v1.services.firestore import FirestoreClient
    from google.cloud.firestore_v1.services.firestore.transports.grpc import FirestoreGrpcTransport

    monkeypatch.setattr(google.auth, "default", lambda *a, **k: pytest.fail("ADC forbidden"))
    monkeypatch.setenv("DISABLE_GCS_PYTHON_CLIENT_OTEL_BUCKET_METADATA", "true")
    credential = AnonymousCredentials()
    observed = []
    for transport in (
        FirestoreAdminGrpcTransport,
        FirestoreGrpcTransport,
        KeyManagementServiceGrpcTransport,
    ):

        def channel(endpoint, *, credentials, options, _transport=transport):
            assert credentials is credential
            assert options == (("grpc.enable_retries", 0), ("grpc.enable_http_proxy", 0))
            value = create_autospec(grpc.Channel, instance=True)
            observed.append((_transport, endpoint, value))
            return value

        monkeypatch.setattr(transport, "create_channel", staticmethod(channel))
    native = bootstrap.clients(credential, plan)
    try:
        assert isinstance(native.admin, FirestoreAdminClient) and isinstance(
            native.firestore, FirestoreClient
        )
        assert isinstance(native.kms, kms_v1.KeyManagementServiceClient) and isinstance(
            native.storage, storage.Client
        )
        assert [value[1] for value in observed] == [
            "firestore.googleapis.com:443",
            "firestore.googleapis.com:443",
            "cloudkms.googleapis.com:443",
        ]
        assert native.storage._connection.API_BASE_URL == "https://storage.googleapis.com"
        assert native.storage._credentials is credential
        assert native.storage._http.credentials is credential
        assert native.storage._http.max_redirects == 0 and native.storage._http.trust_env is False
        assert native.storage._http._max_refresh_attempts == 0
    finally:
        native.admin.transport.close()
        native.firestore.transport.close()
        native.kms.transport.close()
        native.storage.close()
