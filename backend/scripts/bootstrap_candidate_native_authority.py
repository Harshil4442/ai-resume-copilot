"""One-shot operator creation of fresh candidate native authority.

Default invocation is a pure plan. Execute is reserved for the root's post-F7
operator run. Every provider write is preceded by a durable create-only local
intent. Unknown writes cannot resume, repeat or activate a runtime. Credentials
remain in the approved normal gcloud manager; no ADC or token output exists.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal
from uuid import UUID, uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pydantic import Field, model_validator  # noqa: E402

from app.domains.candidate_accounts.production import CandidateNativeConfiguration  # noqa: E402
from app.domains.candidate_ingress.contracts import IngressControl, IngressResource  # noqa: E402
from app.domains.candidate_ingress.production import (
    CandidateIngressNativeConfiguration,  # noqa: E402
)
from app.domains.candidate_ingress.replay import CONTROL_NAMESPACE  # noqa: E402
from app.domains.recovery.browser_pairing_production import (
    BrowserPairingNativeConfiguration,  # noqa: E402
)
from app.domains.recovery.browser_submit import SubmitNativeConfiguration  # noqa: E402
from app.domains.recovery.contracts import Contract, canonical, fingerprint  # noqa: E402
from app.domains.recovery.gcp_buffer import BufferedTransaction  # noqa: E402
from app.domains.recovery.gcp_contracts import RegistryPin, WitnessBody, WitnessPin  # noqa: E402
from app.domains.recovery.gcp_pairing import pairing_control_record  # noqa: E402
from app.domains.recovery.gcp_partitioned_contracts import (  # noqa: E402
    DirectoryHead,
    LaneHead,
    PartitionedPin,
    PartitionedResource,
    PartitionedRoot,
)
from app.domains.recovery.gcp_publication_contracts import PublicationPin  # noqa: E402
from app.domains.recovery.gcp_rpc import FirestoreRpc  # noqa: E402
from app.domains.recovery.gcp_service import control_record  # noqa: E402
from app.domains.recovery.pairing_auth import public_jwk  # noqa: E402

ACCOUNT = "harshilyogeshbhai886@gmail.com"
SDK_LIBRARY = Path("/opt/homebrew/share/google-cloud-sdk/lib")
MANAGER_DIRECTORY = Path("/Users/harshil/.config/gcloud")
PROJECT: Literal["ai-resume-parser-482412"] = "ai-resume-parser-482412"
EXTENSION: Literal["pnjmegppacleipajnifjmbcopjjffklb"] = "pnjmegppacleipajnifjmbcopjjffklb"
RPC_TIMEOUT = 2.0


class BootstrapDenied(RuntimeError):
    """Safe fixed refusal; vendor errors and credential values never escape."""


class BootstrapUnknown(BootstrapDenied):
    pass


def need(value: Any, code: str) -> None:
    if not value:
        raise BootstrapDenied(code)


class BootstrapPlan(Contract):
    project: Literal["ai-resume-parser-482412"] = PROJECT
    project_number: str = Field(pattern=r"^[1-9][0-9]{5,19}$")
    location: str = Field(pattern=r"^[a-z][a-z0-9-]{2,62}$")
    execution_database_id: str = Field(pattern=r"^[a-z][a-z0-9-]{2,61}[a-z0-9]$")
    protected_database_id: str = Field(pattern=r"^[a-z][a-z0-9-]{2,61}[a-z0-9]$")
    replay_database_id: str = Field(pattern=r"^[a-z][a-z0-9-]{2,61}[a-z0-9]$")
    journal_bucket: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]{1,61}[a-z0-9]$")
    bucket_location: str = Field(pattern=r"^[A-Z][A-Z0-9-]{1,62}$")
    minimum_locked_retention_seconds: int = Field(strict=True, ge=86_400, le=3_153_600_000)
    kms_key_ring_id: str = Field(pattern=r"^[a-z][a-z0-9-]{2,62}$")
    assertion_key_id: str = Field(pattern=r"^[a-z][a-z0-9-]{2,62}$")
    claim_key_id: str = Field(pattern=r"^[a-z][a-z0-9-]{2,62}$")
    kms_protection_level: Literal["SOFTWARE", "HSM"] = "SOFTWARE"
    lanes: Literal[2, 4, 8, 16] = 8
    directory_shards: Literal[2, 4, 8, 16] = 8
    segment_size: int = Field(default=128, strict=True, ge=1, le=1024)
    extension_id: Literal["pnjmegppacleipajnifjmbcopjjffklb"] = EXTENSION
    executor_revision: str = Field(default="companion_0_7_0", pattern=r"^[A-Za-z0-9_-]{1,160}$")
    assertion_issuer: str = Field(
        default="hirewiz_candidate_password_v1", pattern=r"^[A-Za-z0-9_-]{1,160}$"
    )
    website_origin: str
    ingress_key_id: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,63}$")

    @model_validator(mode="after")
    def exact(self):
        from app.domains.candidate_ingress.contracts import https_origin

        names = (self.execution_database_id, self.protected_database_id, self.replay_database_id)
        if len(set(names)) != 3 or self.assertion_key_id == self.claim_key_id:
            raise ValueError("Fresh distinct databases and signing keys required")
        if any(re.fullmatch(r"[a-f0-9-]{36}", name) for name in names):
            raise ValueError("Named operator databases required")
        https_origin(self.website_origin)
        return self

    @property
    def digest(self) -> str:
        return fingerprint(self.model_dump(mode="json"))

    @property
    def database_names(self) -> tuple[str, str, str]:
        return (
            f"projects/{self.project}/databases/{self.execution_database_id}",
            f"projects/{self.project}/databases/{self.protected_database_id}",
            f"projects/{self.project}/databases/{self.replay_database_id}",
        )


def pairs(items):
    value = {}
    for key, child in items:
        need(key not in value, "bootstrap_duplicate_json_key")
        value[key] = child
    return value


def load_plan(path: Path) -> BootstrapPlan:
    info = path.lstat()
    need(
        path.is_absolute()
        and stat.S_ISREG(info.st_mode)
        and not path.is_symlink()
        and info.st_uid == os.getuid()
        and not info.st_mode & 0o022
        and 0 < info.st_size <= 32_768,
        "bootstrap_plan_file_refused",
    )
    return BootstrapPlan.model_validate(json.loads(path.read_bytes(), object_pairs_hook=pairs))


def validate_manager(plan: BootstrapPlan) -> None:
    """Reuse the reviewed normal-manager constraints, never create a login."""
    need(plan.project == PROJECT, "bootstrap_project_refused")
    need(
        not any(
            os.getenv(name)
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
                "STORAGE_EMULATOR_HOST",
                "FIRESTORE_EMULATOR_HOST",
            )
        ),
        "bootstrap_override_proxy_or_adc_refused",
    )
    need(
        os.getenv("GOOGLE_API_USE_MTLS_ENDPOINT", "never") == "never"
        and os.getenv("GOOGLE_API_USE_CLIENT_CERTIFICATE", "false") == "false"
        and os.getenv("DISABLE_GCS_PYTHON_CLIENT_OTEL_BUCKET_METADATA") == "true",
        "bootstrap_transport_configuration_refused",
    )
    infra = Path(__file__).resolve().parents[2] / "infra" / "gcp"
    sys.path[:0] = [str(infra), str(SDK_LIBRARY), str(SDK_LIBRARY / "third_party")]
    import native_fresh_fence
    from googlecloudsdk.core import config

    need(
        config.CLOUD_SDK_VERSION == "578.0.0"
        and Path(config.__file__).resolve()
        == (SDK_LIBRARY / "googlecloudsdk" / "core" / "config.py").resolve(),
        "bootstrap_sdk_version_or_location_refused",
    )
    need(
        Path(config.Paths().global_config_dir).resolve() == MANAGER_DIRECTORY.resolve(),
        "bootstrap_normal_manager_directory_refused",
    )
    # Its stock authenticated transport validates exact retained account,
    # project, universe, disabled logs, no impersonation/override/proxy.
    native_fresh_fence.SDKGoogleReads(SDK_LIBRARY, ACCOUNT)._http()


def manager_credentials(plan: BootstrapPlan):
    validate_manager(plan)
    from google.auth.credentials import Credentials
    from googlecloudsdk.core.credentials import store

    credentials = store.Load(account=ACCOUNT, allow_account_impersonation=False)
    need(isinstance(credentials, Credentials), "bootstrap_normal_google_auth_credentials_required")
    return credentials


@dataclass(frozen=True)
class Clients:
    admin: Any
    firestore: Any
    storage: Any
    kms: Any


def clients(credentials, plan: BootstrapPlan) -> Clients:
    """Actual official SDK clients, explicit normal-manager credential object."""
    from google.auth.transport.requests import AuthorizedSession
    from google.cloud.firestore_admin_v1.services.firestore_admin import FirestoreAdminClient
    from google.cloud.firestore_admin_v1.services.firestore_admin.transports.grpc import (
        FirestoreAdminGrpcTransport,
    )
    from google.cloud.firestore_v1.services.firestore import FirestoreClient
    from google.cloud.firestore_v1.services.firestore.transports.grpc import FirestoreGrpcTransport
    from google.cloud.kms_v1.services.key_management_service import KeyManagementServiceClient
    from google.cloud.kms_v1.services.key_management_service.transports.grpc import (
        KeyManagementServiceGrpcTransport,
    )
    from google.cloud.storage import Client

    options = (("grpc.enable_retries", 0), ("grpc.enable_http_proxy", 0))

    def channel(transport, endpoint):
        return transport.create_channel(endpoint, credentials=credentials, options=options)

    admin = FirestoreAdminClient(
        transport=FirestoreAdminGrpcTransport(
            channel=channel(FirestoreAdminGrpcTransport, "firestore.googleapis.com:443")
        )
    )
    native = FirestoreClient(
        transport=FirestoreGrpcTransport(
            channel=channel(FirestoreGrpcTransport, "firestore.googleapis.com:443")
        )
    )
    kms = KeyManagementServiceClient(
        transport=KeyManagementServiceGrpcTransport(
            channel=channel(KeyManagementServiceGrpcTransport, "cloudkms.googleapis.com:443")
        )
    )
    http = AuthorizedSession(credentials, max_refresh_attempts=0)
    http.max_redirects, http.trust_env = 0, False
    storage = Client(
        project=plan.project,
        credentials=credentials,
        _http=http,
        client_options={"api_endpoint": "https://storage.googleapis.com"},
    )
    return Clients(admin, native, storage, kms)


class Journal:
    def __init__(self, directory: Path):
        info = directory.lstat()
        need(
            directory.is_absolute()
            and not directory.is_symlink()
            and stat.S_ISDIR(info.st_mode)
            and info.st_uid == os.getuid()
            and stat.S_IMODE(info.st_mode) == 0o700,
            "bootstrap_private_journal_directory_required",
        )
        self.directory = directory

    def read(self, name: str) -> dict:
        need(
            re.fullmatch(r"[a-z0-9_-]+\.(?:json|intent|ack|unknown)", name),
            "bootstrap_journal_name_refused",
        )
        path = self.directory / name
        info = path.lstat()
        need(
            stat.S_ISREG(info.st_mode)
            and not path.is_symlink()
            and info.st_uid == os.getuid()
            and stat.S_IMODE(info.st_mode) == 0o600
            and 0 < info.st_size <= 65_536,
            "bootstrap_journal_file_refused",
        )
        value = json.loads(path.read_bytes(), object_pairs_hook=pairs)
        need(type(value) is dict, "bootstrap_journal_record_refused")
        return value

    def append(self, name: str, body: dict) -> None:
        need(
            re.fullmatch(r"[a-z0-9_-]+\.(?:json|intent|ack|unknown)", name),
            "bootstrap_journal_name_refused",
        )
        fd = os.open(
            self.directory / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600
        )
        with os.fdopen(fd, "wb") as stream:
            stream.write(canonical(body).encode() + b"\n")
            stream.flush()
            os.fsync(stream.fileno())
        fd = os.open(self.directory, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


class Bootstrap:
    def __init__(self, plan: BootstrapPlan, native: Clients, journal: Journal):
        self.plan, self.native, self.journal = plan, native, journal
        self.bootstrap_id = uuid4()
        self.phase = 0

    def _write(
        self,
        kind: str,
        target: str,
        request: dict,
        operation: Callable[[], Any],
        check: Callable[[Any], Any],
    ):
        self.phase += 1
        name = f"{self.phase:03d}_{kind}"
        self.journal.append(
            name + ".intent",
            {
                "bootstrap_id": str(self.bootstrap_id),
                "plan_sha256": self.plan.digest,
                "kind": kind,
                "target": target,
                "request": request,
                "automatic_replay": False,
                "state": "UNKNOWN_UNTIL_ORIGINAL_ACK",
            },
        )
        try:
            validate_manager(self.plan)
            response = operation()
            value = check(response)
            self.journal.append(name + ".ack", {"target": target, "confirmed": True})
            return value
        except BaseException:
            self.journal.append(
                name + ".unknown",
                {
                    "target": target,
                    "state": "UNKNOWN",
                    "automatic_replay": False,
                    "runtime_activation_allowed": False,
                },
            )
            raise BootstrapUnknown("bootstrap_write_unknown_read_only_reconcile_required") from None

    def _database(self, database_id: str) -> tuple[RegistryPin, int]:
        from google.cloud.firestore_admin_v1.types import Database

        name = f"projects/{self.plan.project}/databases/{database_id}"
        body = Database(
            location_id=self.plan.location,
            type_=Database.DatabaseType.FIRESTORE_NATIVE,
            point_in_time_recovery_enablement=Database.PointInTimeRecoveryEnablement.POINT_IN_TIME_RECOVERY_ENABLED,
            delete_protection_state=Database.DeleteProtectionState.DELETE_PROTECTION_ENABLED,
        )

        def checked(operation):
            op = operation.operation
            need(
                re.fullmatch(re.escape(name) + r"/operations/[A-Za-z0-9_.~-]{1,512}", op.name),
                "bootstrap_database_operation_refused",
            )
            deadline = time.monotonic() + 300
            while not op.done and time.monotonic() < deadline:
                op = self.native.admin.transport.operations_client.get_operation(
                    op.name, retry=None, timeout=RPC_TIMEOUT
                )
                if not op.done:
                    time.sleep(0.25)
            need(op.done and not op.HasField("error"), "bootstrap_database_operation_unconfirmed")
            created = Database()
            need(
                op.HasField("response")
                and op.response.Unpack(created._pb)
                and created.name == name
                and bool(created.uid),
                "bootstrap_database_original_creation_ack_required",
            )
            actual = self.native.admin.get_database(
                request={"name": name}, retry=None, timeout=RPC_TIMEOUT
            )
            need(created.uid == actual.uid, "bootstrap_database_creation_uid_changed")
            need(
                isinstance(actual, Database)
                and actual.name == name
                and actual.type_ == Database.DatabaseType.FIRESTORE_NATIVE
                and actual.location_id == self.plan.location
                and actual.delete_protection_state
                == Database.DeleteProtectionState.DELETE_PROTECTION_ENABLED
                and actual.point_in_time_recovery_enablement
                == Database.PointInTimeRecoveryEnablement.POINT_IN_TIME_RECOVERY_ENABLED,
                "bootstrap_database_readback_refused",
            )
            uid = UUID(actual.uid)
            need(
                str(uid) == actual.uid and actual.create_time is not None,
                "bootstrap_database_native_uid_required",
            )
            return RegistryPin(
                database=name,
                database_uid=uid,
                authority_id=uuid4(),
                incarnation=1,
                epoch_id=uuid4(),
            ), int(actual.create_time.timestamp() * 1000)

        return self._write(
            "database",
            name,
            {
                "parent": f"projects/{self.plan.project}",
                "database_id": database_id,
                "location": self.plan.location,
                "type": "FIRESTORE_NATIVE",
                "delete_protection": True,
                "pitr": True,
            },
            lambda: self.native.admin.create_database(
                request={
                    "parent": f"projects/{self.plan.project}",
                    "database_id": database_id,
                    "database": body,
                },
                retry=None,
                timeout=RPC_TIMEOUT,
            ),
            checked,
        )

    def _rows(self, pin: RegistryPin, rows: list[tuple[str, str, dict]]) -> list[dict]:
        from google.cloud.firestore_v1.types import CommitResponse, Document, Precondition, Write

        rpc = FirestoreRpc(self.native.firestore, self.native.admin, database_resource=pin.database)
        writes, expected = [], {}
        for namespace, key, value in rows:
            path = BufferedTransaction(rpc, b"path-only").path(namespace, key)
            raw = canonical({"namespace": namespace, "key": key, "value": value}).encode()
            expected[path] = raw
            writes.append(
                Write(
                    update=Document(name=path, fields={"body": {"bytes_value": raw}}),
                    current_document=Precondition(exists=False),
                )
            )

        def checked(reply):
            need(
                isinstance(reply, CommitResponse)
                and len(reply.write_results) == len(writes)
                and reply._pb.HasField("commit_time")
                and all(item._pb.HasField("update_time") for item in reply.write_results),
                "bootstrap_commit_ack_required",
            )
            need(rpc.identity()["uid"] == str(pin.database_uid), "bootstrap_database_uid_changed")
            observed = {}
            stream = self.native.firestore.run_query(
                request={
                    "parent": pin.database + "/documents",
                    "structured_query": {
                        "from_": [{"collection_id": "authority_records", "all_descendants": True}],
                        "limit": len(expected) + 1,
                    },
                },
                retry=None,
                timeout=RPC_TIMEOUT,
            )
            for response in stream:
                if response.document.name:
                    document = response.document
                    need(
                        document.name not in observed and set(document.fields) == {"body"},
                        "bootstrap_initial_census_refused",
                    )
                    observed[document.name] = document.fields["body"].bytes_value
            need(observed == expected, "bootstrap_initial_census_incomplete_or_contaminated")
            return [
                {"database": pin.database, "namespace": n, "key": k, "value": v} for n, k, v in rows
            ]

        return self._write(
            "records",
            pin.database,
            {"records": [{"namespace": n, "key": k, "value": v} for n, k, v in rows]},
            lambda: self.native.firestore.commit(
                request={"database": pin.database, "writes": writes},
                retry=None,
                timeout=RPC_TIMEOUT,
            ),
            checked,
        )

    def _object(self, path: str, value: dict) -> dict:
        from app.domains.recovery.gcp_journal import _exact_bytes, _metadata

        bucket = self.native.storage.bucket(self.plan.journal_bucket)
        raw = canonical(value).encode()
        blob = bucket.blob(path)

        def checked(_):
            need(
                blob._properties.get("name") == path
                and blob._properties.get("bucket") == bucket.name,
                "bootstrap_storage_original_ack_required",
            )
            generation = _metadata(blob, len(raw))
            _exact_bytes(bucket, path, raw, generation=generation, timeout=RPC_TIMEOUT)
            return {
                "path": path,
                "generation": generation,
                "sha256": hashlib.sha256(raw).hexdigest(),
            }

        return self._write(
            "object",
            f"gs://{bucket.name}/{path}",
            {"sha256": hashlib.sha256(raw).hexdigest(), "size_bytes": len(raw)},
            lambda: blob.upload_from_string(
                raw,
                content_type="application/json",
                if_generation_match=0,
                retry=None,
                timeout=RPC_TIMEOUT,
                checksum=None,
            ),
            checked,
        )

    def _key(self, ring: str, key_id: str) -> dict:
        import base64

        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import ec
        from google.cloud import kms_v1

        from app.domains.recovery.pairing_kms import _checked_crc

        key = kms_v1.CryptoKey(
            purpose=kms_v1.CryptoKey.CryptoKeyPurpose.ASYMMETRIC_SIGN,
            version_template=kms_v1.CryptoKeyVersionTemplate(
                algorithm=kms_v1.CryptoKeyVersion.CryptoKeyVersionAlgorithm.EC_SIGN_P256_SHA256,
                protection_level=getattr(kms_v1.ProtectionLevel, self.plan.kms_protection_level),
            ),
        )
        name = ring + "/cryptoKeys/" + key_id

        def checked(reply):
            need(
                reply.name == name and reply.purpose == key.purpose, "bootstrap_kms_key_ack_refused"
            )
            page = next(
                self.native.kms.list_crypto_key_versions(
                    request={"parent": name, "page_size": 2}, retry=None, timeout=RPC_TIMEOUT
                ).pages
            )
            need(
                not page.next_page_token and len(page.crypto_key_versions) == 1,
                "bootstrap_kms_single_fresh_version_required",
            )
            version = page.crypto_key_versions[0]
            need(
                re.fullmatch(
                    re.escape(name) + r"/cryptoKeyVersions/[1-9][0-9]{0,18}", version.name
                ),
                "bootstrap_kms_version_name_refused",
            )
            deadline = time.monotonic() + 60
            while (
                version.state == kms_v1.CryptoKeyVersion.CryptoKeyVersionState.PENDING_GENERATION
                and time.monotonic() < deadline
            ):
                time.sleep(0.25)
                version = self.native.kms.get_crypto_key_version(
                    request={"name": version.name}, retry=None, timeout=RPC_TIMEOUT
                )
            need(
                version.state == kms_v1.CryptoKeyVersion.CryptoKeyVersionState.ENABLED
                and version.algorithm == key.version_template.algorithm
                and version.protection_level == key.version_template.protection_level,
                "bootstrap_kms_version_refused",
            )
            public = self.native.kms.get_public_key(
                request={"name": version.name}, retry=None, timeout=RPC_TIMEOUT
            )
            need(
                public.name == version.name
                and public.algorithm == version.algorithm
                and public.protection_level == version.protection_level,
                "bootstrap_kms_public_identity_refused",
            )
            _checked_crc(public.pem_crc32c, public.pem.encode())
            parsed = serialization.load_pem_public_key(public.pem.encode())
            need(
                isinstance(parsed, ec.EllipticCurvePublicKey)
                and isinstance(parsed.curve, ec.SECP256R1),
                "bootstrap_kms_p256_required",
            )
            coordinates = parsed.public_numbers()
            jwk = public_jwk(
                {
                    "kty": "EC",
                    "crv": "P-256",
                    **{
                        field: base64.urlsafe_b64encode(number.to_bytes(32, "big"))
                        .rstrip(b"=")
                        .decode()
                        for field, number in (("x", coordinates.x), ("y", coordinates.y))
                    },
                }
            )
            return {
                "version_name": version.name,
                "endpoint": "cloudkms.googleapis.com:443",
                "protection_level": self.plan.kms_protection_level,
                "public_key": jwk,
            }

        return self._write(
            "key",
            name,
            {
                "parent": ring,
                "key_id": key_id,
                "purpose": "ASYMMETRIC_SIGN",
                "algorithm": "EC_SIGN_P256_SHA256",
                "protection_level": self.plan.kms_protection_level,
            },
            lambda: self.native.kms.create_crypto_key(
                request={
                    "parent": ring,
                    "crypto_key_id": key_id,
                    "crypto_key": key,
                    "skip_initial_version_creation": False,
                },
                retry=None,
                timeout=RPC_TIMEOUT,
            ),
            checked,
        )

    def execute(self) -> dict:
        self.journal.append(
            "plan.json",
            {
                "plan": self.plan.model_dump(mode="json"),
                "plan_sha256": self.plan.digest,
                "bootstrap_id": str(self.bootstrap_id),
                "automatic_replay": False,
            },
        )
        bucket = self.native.storage.bucket(self.plan.journal_bucket)
        from app.domains.recovery.gcp_journal import _sdk_configuration

        _sdk_configuration(bucket)
        # get_bucket/reload populates the SDK metadata cache; journal reads require
        # a dedicated untouched cache. Read the official bounded SDK resource
        # directly so the existing strict object-read guard remains effective.
        properties = self.native.storage._get_resource(
            bucket.path, query_params={"projection": "full"}, retry=None, timeout=RPC_TIMEOUT
        )
        retention = properties.get("retentionPolicy", {})
        period = retention.get("retentionPeriod")
        need(
            properties.get("name") == bucket.name
            and properties.get("projectNumber") == self.plan.project_number
            and properties.get("location") == self.plan.bucket_location
            and properties.get("iamConfiguration", {})
            .get("uniformBucketLevelAccess", {})
            .get("enabled")
            is True
            and properties.get("versioning", {}).get("enabled") is True
            and retention.get("isLocked") is True
            and type(period) is str
            and re.fullmatch(r"[1-9][0-9]{0,10}", period)
            and int(period) >= self.plan.minimum_locked_retention_seconds,
            "bootstrap_reviewed_protected_bucket_policy_required",
        )
        from google.api_core.exceptions import NotFound

        for name in self.plan.database_names:
            try:
                self.native.admin.get_database(
                    request={"name": name}, retry=None, timeout=RPC_TIMEOUT
                )
            except NotFound:
                continue
            raise BootstrapDenied("bootstrap_database_already_exists_no_adoption")
        ring_name = f"projects/{self.plan.project}/locations/{self.plan.location}/keyRings/{self.plan.kms_key_ring_id}"
        try:
            self.native.kms.get_key_ring(
                request={"name": ring_name}, retry=None, timeout=RPC_TIMEOUT
            )
        except NotFound:
            pass
        else:
            raise BootstrapDenied("bootstrap_key_ring_already_exists_no_adoption")
        execution, now = self._database(self.plan.execution_database_id)
        protected, _ = self._database(self.plan.protected_database_id)
        replay, _ = self._database(self.plan.replay_database_id)
        partition = PartitionedPin(
            authority=PublicationPin(
                registry=protected, target=execution, journal_bucket=bucket.name
            ),
            lanes=self.plan.lanes,
            directory_shards=self.plan.directory_shards,
            segment_size=self.plan.segment_size,
        )
        ingress = IngressResource(
            registry=replay,
            action_registries=(execution, protected),
            origin=self.plan.website_origin,
            key_id=self.plan.ingress_key_id,
            custody_id=uuid4(),
            restore_generation=1,
            clock_witness_id=uuid4(),
        )
        inventory = self._rows(
            execution,
            [
                ("control", "meta", control_record(execution)),
                ("pairing_control", "current", pairing_control_record(execution, 1)),
                ("pairing_clock", "observed", {"now_ms": now}),
                ("head", "global", {"sequence": 0, "digest": "0" * 64}),
                (
                    "pairing_extensions",
                    f"{self.plan.extension_id}:{self.plan.executor_revision}",
                    {
                        "extension_id": self.plan.extension_id,
                        "executor_revision": self.plan.executor_revision,
                        "protocol_version": 2,
                        "active": True,
                    },
                ),
            ],
        )
        inventory += self._rows(
            protected,
            [
                (
                    "v3_root",
                    "current",
                    PartitionedRoot(pin=partition, phase="OPEN").model_dump(mode="json"),
                ),
                *[
                    (
                        "v3_lanes",
                        str(n),
                        LaneHead(lane=n, segment_count=0, segment_id=None).model_dump(mode="json"),
                    )
                    for n in range(partition.lanes)
                ],
                *[
                    (
                        "v3_directory",
                        str(n),
                        DirectoryHead(shard=n, count=0, digest="0" * 64).model_dump(mode="json"),
                    )
                    for n in range(partition.directory_shards)
                ],
            ],
        )
        inventory += self._rows(
            replay,
            [
                (
                    CONTROL_NAMESPACE,
                    "root",
                    IngressControl(resource=ingress, state="OPEN").model_dump(mode="json"),
                )
            ],
        )
        resource_path = f"authority-publication-v3-resources/{fingerprint(execution.model_dump(mode='json'))}.json"
        resource_receipt = self._object(resource_path, partition.model_dump(mode="json"))
        resource = PartitionedResource(
            pin=partition,
            bucket=bucket.name,
            generation=resource_receipt["generation"],
            sha256=resource_receipt["sha256"],
        )
        manifest = {
            "version": 1,
            "kind": "fresh_candidate_native_bootstrap",
            "bootstrap_id": str(self.bootstrap_id),
            "plan_sha256": self.plan.digest,
            "registries": [pin.model_dump(mode="json") for pin in (execution, protected, replay)],
            "authority_rows": inventory,
            "publication_resource": resource.model_dump(mode="json"),
            "candidate_or_device_seeded": False,
        }
        receipt = self._object(f"candidate-native-bootstrap/{self.bootstrap_id}/manifest", manifest)
        witness_body = WitnessBody(
            registry=execution,
            state="OPEN",
            manifest_generation=receipt["generation"],
            manifest_sha256=receipt["sha256"],
            partitions_sha256=fingerprint({"authority_rows": inventory}),
        )
        witness = self._object(
            f"candidate-native-bootstrap/{self.bootstrap_id}/witness",
            witness_body.model_dump(mode="json"),
        )
        witness_pin = WitnessPin(registry=execution, bucket=bucket.name, **witness)
        ring = f"projects/{self.plan.project}/locations/{self.plan.location}/keyRings/{self.plan.kms_key_ring_id}"
        self._write(
            "ring",
            ring,
            {
                "parent": f"projects/{self.plan.project}/locations/{self.plan.location}",
                "key_ring_id": self.plan.kms_key_ring_id,
            },
            lambda: self.native.kms.create_key_ring(
                request={
                    "parent": f"projects/{self.plan.project}/locations/{self.plan.location}",
                    "key_ring_id": self.plan.kms_key_ring_id,
                    "key_ring": {},
                },
                retry=None,
                timeout=RPC_TIMEOUT,
            ),
            lambda reply: need(reply.name == ring, "bootstrap_kms_ring_ack_refused"),
        )
        assertion, claim = (
            self._key(ring, self.plan.assertion_key_id),
            self._key(ring, self.plan.claim_key_id),
        )
        shared = CandidateNativeConfiguration(
            execution_witness_pin=witness_pin,
            execution_witness_body=witness_body,
            publication_resource=resource,
            epoch_generation=1,
        )
        account = shared.model_dump(mode="json")
        result = {
            "state": "BOOTSTRAPPED_NOT_ACTIVATED",
            "runtime_activation_allowed": False,
            "bootstrap_id": str(self.bootstrap_id),
            "CANDIDATE_ACCOUNT_NATIVE_CONFIG": account,
            "CANDIDATE_INGRESS_NATIVE_CONFIG": CandidateIngressNativeConfiguration(
                **account, resource=ingress
            ).model_dump(mode="json"),
            "BROWSER_PAIRING_NATIVE_CONFIG": BrowserPairingNativeConfiguration(
                **account,
                assertion_issuer=self.plan.assertion_issuer,
                assertion_key=assertion,
                claim_key=claim,
                extension_id=self.plan.extension_id,
                executor_revision=self.plan.executor_revision,
            ).model_dump(mode="json"),
            "BROWSER_SUBMIT_NATIVE_CONFIG": SubmitNativeConfiguration(
                **account, device_enrollment="paired", executor_revision=self.plan.executor_revision
            ).model_dump(mode="json"),
            "companion_nativePairing": {
                "executorRevision": self.plan.executor_revision,
                "authorityKey": claim["public_key"],
            },
            "public_environment": {
                "DISABLE_GCS_PYTHON_CLIENT_OTEL_BUCKET_METADATA": "true",
                "CANDIDATE_AUTH_TRANSPORT_KEY_ID": self.plan.ingress_key_id,
                "BROWSER_PAIRING_WEBSITE_ORIGIN": self.plan.website_origin,
                "BROWSER_ASSISTANCE_SUBMIT_EXECUTOR_REVISION": self.plan.executor_revision,
            },
            "required_secret_inputs": [
                "CANDIDATE_AUTH_TRANSPORT_SECRET",
                "BROWSER_PAIRING_GATEWAY_SECRET",
                "BROWSER_ASSISTANCE_SIGNING_SECRET",
            ],
            "enable_flags": {
                name: False
                for name in (
                    "CANDIDATE_ACCOUNT_LIFECYCLE_ENABLED",
                    "CANDIDATE_INGRESS_NATIVE_ENABLED",
                    "BROWSER_PAIRING_NATIVE_ENABLED",
                    "BROWSER_ASSISTANCE_NATIVE_PAIRING_ENABLED",
                    "BROWSER_SUBMIT_NATIVE_ENABLED",
                    "BROWSER_ASSISTANCE_ENABLED",
                    "BROWSER_ASSISTANCE_TYPED_CONTROLS_ENABLED",
                    "BROWSER_ASSISTANCE_SUBMIT_ENABLED",
                )
            },
        }
        self.journal.append("result.json", result)
        return result


def reconcile(plan: BootstrapPlan, native: Clients, journal: Journal) -> dict:
    """Read-only observation only. Never finish writes or publish usable config."""
    original = journal.read("plan.json")
    need(
        original.get("plan_sha256") == plan.digest
        and original.get("plan") == plan.model_dump(mode="json"),
        "bootstrap_reconciliation_plan_mismatch",
    )
    observed = []
    for name in plan.database_names:
        try:
            value = native.admin.get_database(
                request={"name": name}, retry=None, timeout=RPC_TIMEOUT
            )
            observed.append({"name": name, "uid": str(UUID(value.uid))})
        except Exception:
            observed.append({"name": name, "observation": "UNAVAILABLE_OR_ABSENT"})
    objects = []
    for path in sorted(journal.directory.glob("*.intent")):
        intent = journal.read(path.name)
        if intent.get("kind") != "object":
            continue
        target = intent.get("target")
        prefix = f"gs://{plan.journal_bucket}/"
        need(
            type(target) is str and target.startswith(prefix),
            "bootstrap_reconciliation_target_refused",
        )
        object_path = str(target)[len(prefix) :]
        need(
            re.fullmatch(
                r"(?:authority-publication-v3-resources/[a-f0-9]{64}\.json|candidate-native-bootstrap/[a-f0-9-]{36}/(?:manifest|witness))",
                object_path,
            ),
            "bootstrap_reconciliation_target_refused",
        )
        try:
            blob = native.storage.bucket(plan.journal_bucket).blob(object_path)
            blob.reload(retry=None, timeout=RPC_TIMEOUT)
            from app.domains.recovery.gcp_journal import _metadata

            generation = _metadata(blob, intent["request"]["size_bytes"])
            objects.append(
                {
                    "path": object_path,
                    "current_generation": generation,
                    "observation": "METADATA_ONLY_NOT_ORIGINAL_ACK",
                }
            )
        except Exception:
            objects.append({"path": object_path, "observation": "UNAVAILABLE_OR_ABSENT"})
    return {
        "state": "READ_ONLY_RECONCILIATION",
        "runtime_activation_allowed": False,
        "automatic_replay": False,
        "databases": observed,
        "objects": objects,
        "observation_scope": "Current database identity and attempted object metadata only; no content, KMS or original-ACK recovery",
        "attempt_files": sorted(path.name for path in journal.directory.iterdir()),
        "next_action": "Operator review required; do not rerun execute or adopt partial resources.",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan-file", type=Path, required=True)
    parser.add_argument("--journal-directory", type=Path)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--execute", action="store_true")
    mode.add_argument("--reconcile", action="store_true")
    args = parser.parse_args(argv)
    try:
        plan = load_plan(args.plan_file)
        if not args.execute and not args.reconcile:
            print(
                canonical(
                    {
                        "state": "PLAN_ONLY",
                        "runtime_activation_allowed": False,
                        "plan_sha256": plan.digest,
                        "plan": plan.model_dump(mode="json"),
                        "normal_manager_account": ACCOUNT,
                        "sdk_version": "578.0.0",
                        "writes_require": "Root post-F7 execute invocation with fresh private journal directory.",
                    }
                )
            )
            return 0
        need(args.journal_directory is not None, "bootstrap_private_journal_directory_required")
        journal = Journal(args.journal_directory)
        if args.execute:
            need(
                not tuple(journal.directory.iterdir()), "bootstrap_existing_attempt_reconcile_only"
            )
        credentials = manager_credentials(plan)
        native = clients(credentials, plan)
        result = (
            reconcile(plan, native, journal)
            if args.reconcile
            else Bootstrap(plan, native, journal).execute()
        )
        print(canonical(result))
        return 0
    except BaseException as error:
        code = str(error) if isinstance(error, BootstrapDenied) else "bootstrap_refused_or_unknown"
        print(
            canonical(
                {
                    "state": "REFUSED_OR_UNKNOWN",
                    "code": code,
                    "runtime_activation_allowed": False,
                    "automatic_replay": False,
                }
            )
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
