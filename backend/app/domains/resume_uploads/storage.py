"""Fixed private GCS targets; all parsing stays outside this transport."""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import ssl
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

import certifi

from .schemas import MAX_BYTES


class StorageUnavailable(RuntimeError):
    pass


class StorageMismatch(RuntimeError):
    pass


@dataclass(frozen=True)
class ObjectInfo:
    generation: str
    size_bytes: int
    media_type: str
    encoding: str | None = None
    retired: bool = False


@dataclass(frozen=True)
class StorageConfig:
    project: str
    quarantine_bucket: str
    clean_bucket: str
    signer: str
    read_signer: str

    @classmethod
    def environment(cls) -> StorageConfig:
        values = [os.getenv(k, "").strip() for k in (
            "GOOGLE_CLOUD_PROJECT", "RESUME_UPLOAD_QUARANTINE_BUCKET",
            "RESUME_UPLOAD_CLEAN_BUCKET", "RESUME_UPLOAD_SIGNER", "RESUME_UPLOAD_READ_SIGNER",
        )]
        project, quarantine, clean, signer, read_signer = values
        if (not re.fullmatch(r"[a-z][a-z0-9-]{4,28}[a-z0-9]", project)
                or any(not re.fullmatch(r"[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]", b) for b in (quarantine, clean))
                or quarantine == clean
                or signer == read_signer
                or any(not re.fullmatch(r"[a-z][a-z0-9-]{4,28}@" + re.escape(project) + r"\.iam\.gserviceaccount\.com", account) for account in (signer, read_signer))
                or os.getenv("STORAGE_EMULATOR_HOST")):
            raise StorageUnavailable("storage_configuration_unavailable")
        return cls(*values)


class ObjectStore(Protocol):
    config: StorageConfig
    def sign_upload(self, row, expires_at: datetime) -> tuple[str, dict[str, str]]: ...
    def inspect_object(self, bucket: str, name: str) -> ObjectInfo | None: ...
    def read(self, bucket: str, name: str, generation: str, size: int) -> bytes: ...
    def put_clean(self, row, content: bytes) -> str: ...
    def retire(self, bucket: str, name: str, upload_id: str) -> str: ...
    def sign_read(self, row, expires_at: datetime) -> str: ...


def valid_generation(value: str) -> bool:
    return isinstance(value, str) and bool(re.fullmatch(r"[1-9][0-9]{0,19}", value)) and int(value) <= 18446744073709551615


class GCSObjectStore:
    """No key file, user-selected host, environment proxy or parser fallback."""
    def __init__(self, config: StorageConfig | None = None):
        self.config = config or StorageConfig.environment()

    def _target(self, bucket, name):
        expected = "quarantine" if bucket == self.config.quarantine_bucket else "clean" if bucket == self.config.clean_bucket else None
        if expected is None or not re.fullmatch(expected + r"/rup_[a-f0-9]{32}/source", name):
            raise StorageMismatch("storage_target_mismatch")

    @contextmanager
    def _transport(self):
        if os.getenv("STORAGE_EMULATOR_HOST"):
            raise StorageUnavailable("storage_configuration_unavailable")
        import google.auth
        import requests
        from google.auth.transport.requests import AuthorizedSession, Request
        from google.cloud import storage

        class TLSAdapter(requests.adapters.HTTPAdapter):
            def init_poolmanager(self, *args, **kwargs):
                context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
                context.load_verify_locations(cafile=certifi.where())
                kwargs["ssl_context"] = context
                return super().init_poolmanager(*args, **kwargs)

        auth = requests.Session()
        auth.trust_env = False
        auth.verify = certifi.where()
        auth.mount("https://", TLSAdapter())
        transport = Request(session=auth)
        def bounded_request(*args, **kwargs):
            kwargs["timeout"] = 5
            return transport(*args, **kwargs)
        session = None
        try:
            credentials, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"], request=bounded_request)
            session = AuthorizedSession(credentials, auth_request=bounded_request, max_refresh_attempts=1)
            session.trust_env = False
            session.verify = certifi.where()
            session.mount("https://", TLSAdapter())
            client = storage.Client(project=self.config.project, credentials=credentials, _http=session,
                                    client_options={"api_endpoint": "https://storage.googleapis.com"})
            yield client, session, credentials
        except (StorageUnavailable, StorageMismatch):
            raise
        except Exception:
            raise StorageUnavailable("storage_unavailable") from None
        finally:
            if session is not None:
                session.close()
            auth.close()

    def _signer(self, session, *, read=False):
        from google.auth import credentials as auth_credentials
        service_account = self.config.read_signer if read else self.config.signer
        class KeylessSigner(auth_credentials.Credentials, auth_credentials.Signing):
            @property
            def signer_email(self):
                return service_account
            @property
            def signer(self):
                return self
            def refresh(self, request):
                raise StorageUnavailable("signing_refresh_unavailable")
            def sign(self, message):
                return self.sign_bytes(message)
            def sign_bytes(self, message):
                uri = "https://iamcredentials.googleapis.com/v1/projects/-/serviceAccounts/" + service_account + ":signBlob"
                # SDK URL construction stays local; only a bounded fixed-host IAM
                # request obtains a signature, with no private key exposure.
                with session.post(uri, json={"payload": base64.b64encode(message).decode("ascii")},
                                  timeout=(5, 5), headers={"Accept-Encoding": "identity"}, allow_redirects=False, stream=True) as response:
                    if response.status_code != 200 or response.headers.get("Content-Encoding", "identity") != "identity":
                        raise StorageUnavailable("signing_unavailable")
                    payload = bytearray()
                    for chunk in response.iter_content(1024):
                        if len(payload) + len(chunk) > 8192:
                            raise StorageUnavailable("signing_unavailable")
                        payload.extend(chunk)
                decoded = json.loads(payload)
                signature = base64.b64decode(decoded["signedBlob"], validate=True)
                if not 64 <= len(signature) <= 1024:
                    raise StorageUnavailable("signing_unavailable")
                return signature
        return KeylessSigner()

    def sign_upload(self, row, expires_at):
        self._target(row.quarantine_bucket, row.quarantine_name)
        headers = {"Content-Type": row.media_type, "x-goog-if-generation-match": "0",
                   "x-goog-content-length-range": f"{row.size_bytes},{row.size_bytes}"}
        with self._transport() as (client, session, _):
            url = client.bucket(row.quarantine_bucket).blob(row.quarantine_name).generate_signed_url(
                version="v4", expiration=expires_at, method="PUT", content_type=row.media_type,
                headers={k: v for k, v in headers.items() if k != "Content-Type"}, credentials=self._signer(session))
        return url, headers

    def inspect_object(self, bucket, name):
        self._target(bucket, name)
        from google.api_core.exceptions import NotFound
        with self._transport() as (client, _, _):
            blob = client.bucket(bucket).blob(name)
            try:
                blob.reload(timeout=(5, 15), retry=None)
            except NotFound:
                return None
            generation = str(blob.generation)
            if not valid_generation(generation) or type(blob.size) is not int or blob.size < 0:
                raise StorageMismatch("storage_metadata_mismatch")
            return ObjectInfo(generation, blob.size, blob.content_type or "", blob.content_encoding,
                              (blob.metadata or {}).get("resume-retired") == "1")

    def read(self, bucket, name, generation, size):
        self._target(bucket, name)
        if not valid_generation(generation) or type(size) is not int or not 0 < size <= MAX_BYTES:
            raise StorageMismatch("storage_metadata_mismatch")
        with self._transport() as (client, _, _):
            blob = client.bucket(bucket).blob(name, generation=int(generation))
            # Raw data only. Declared size is verified against native metadata
            # before this call, preventing auto gzip expansion in the API/coordinator.
            content = blob.download_as_bytes(raw_download=True, if_generation_match=int(generation),
                                             timeout=(5, 20), retry=None, checksum="auto")
        if len(content) != size or len(content) > MAX_BYTES:
            raise StorageMismatch("storage_size_mismatch")
        return content

    def put_clean(self, row, content):
        self._target(row.clean_bucket, row.clean_name)
        if len(content) != row.size_bytes or hashlib.sha256(content).hexdigest() != row.source_sha256:
            raise StorageMismatch("storage_content_mismatch")
        from google.api_core.exceptions import PreconditionFailed
        with self._transport() as (client, _, _):
            blob = client.bucket(row.clean_bucket).blob(row.clean_name)
            blob.metadata = {"resume-upload-id": row.id, "sha256": row.source_sha256}
            try:
                blob.upload_from_string(content, content_type=row.media_type, if_generation_match=0,
                                        timeout=(5, 20), retry=None, checksum="auto")
                generation = str(blob.generation)
            except PreconditionFailed:
                info = self.inspect_object(row.clean_bucket, row.clean_name)
                if not info or info.retired or info.size_bytes != row.size_bytes or info.encoding or info.media_type != row.media_type:
                    raise StorageMismatch("clean_object_mismatch") from None
                generation = info.generation
                if self.read(row.clean_bucket, row.clean_name, generation, row.size_bytes) != content:
                    raise StorageMismatch("clean_object_mismatch") from None
        if not valid_generation(generation):
            raise StorageMismatch("clean_object_mismatch")
        return generation

    def retire(self, bucket, name, upload_id):
        """Atomically replace payload by a zero-byte closure marker.

        The live marker prevents still-valid generation0 capabilities and late
        generation0 clean writes from recreating sensitive payload. It is retained;
        finite marker disposal requires a separately verified grant-retirement policy.
        Bucket versioning/soft-delete acceptance is a native deployment gate.
        """
        self._target(bucket, name)
        from google.api_core.exceptions import PreconditionFailed
        for _ in range(3):
            info = self.inspect_object(bucket, name)
            if info and info.retired and info.size_bytes == 0:
                return info.generation
            with self._transport() as (client, _, _):
                blob = client.bucket(bucket).blob(name)
                blob.metadata = {"resume-retired": "1", "resume-upload-id": upload_id}
                try:
                    blob.upload_from_string(b"", content_type="application/octet-stream",
                                            if_generation_match=int(info.generation) if info else 0,
                                            timeout=(5, 15), retry=None)
                except PreconditionFailed:
                    continue
                generation = str(blob.generation)
                if not valid_generation(generation):
                    raise StorageMismatch("retirement_metadata_mismatch")
                return generation
        raise StorageUnavailable("retirement_race_unavailable")

    def sign_read(self, row, expires_at):
        self._target(row.clean_bucket, row.clean_name)
        if not valid_generation(row.clean_generation):
            raise StorageMismatch("clean_object_mismatch")
        with self._transport() as (client, session, _):
            return client.bucket(row.clean_bucket).blob(row.clean_name).generate_signed_url(
                version="v4", expiration=expires_at, method="GET", credentials=self._signer(session, read=True),
                query_parameters={"generation": row.clean_generation}, response_type=row.media_type)
