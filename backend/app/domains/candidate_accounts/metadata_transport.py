"""Lazy opt-in process metadata transport; contains no candidate authorization.

One trusted SDK construction owns one credential object. Requests keep their own
composition, data/storage clients and identity scopes; only sealed pre-send List
cohorts share metadata. This module never initializes a client at import time.
"""
from __future__ import annotations

import os
import re
from threading import Lock
from time import monotonic
from typing import TYPE_CHECKING

from ..recovery.contracts import canonical
from ..recovery.store import GuardUnavailable

if TYPE_CHECKING:
    from ..recovery.gcp_contracts import RegistryPin
    from ..recovery.gcp_rpc import FirestoreRpc
    from ..recovery.native_identity_cohorts import PreSendIdentityCohorts
    from .production import CandidateNativeConfiguration

FLAG = "CANDIDATE_ACCOUNT_SHARED_METADATA_ENABLED"
FAILURE = "Process-owned metadata transport is unavailable"
INITIAL_RESOLUTION_SECONDS = 2.0
INITIAL_RESOLUTION_REQUESTS = 2  # One service-account info and one token request.
_PID = os.getpid()
_lock = Lock()
_state: ProcessMetadataTransport | None = None
_ENV = (FLAG, "CANDIDATE_ACCOUNT_LIFECYCLE_ENABLED", "CANDIDATE_ACCOUNT_NATIVE_CONFIG",
        "BROWSER_SUBMIT_NATIVE_CONFIG", "DISABLE_GCS_PYTHON_CLIENT_OTEL_BUCKET_METADATA",
        "CANDIDATE_ACCOUNT_LIST_IDENTITY_ENABLED", "CANDIDATE_METADATA_ADMISSION_ENABLED",
        "CANDIDATE_METADATA_CALLS_PER_MINUTE", "GOOGLE_CLOUD_PROJECT",
        "GOOGLE_APPLICATION_CREDENTIALS", "GOOGLE_CLOUD_QUOTA_PROJECT", "GOOGLE_CLOUD_UNIVERSE_DOMAIN",
        "GOOGLE_API_USE_MTLS_ENDPOINT", "GOOGLE_API_USE_CLIENT_CERTIFICATE", "FIRESTORE_EMULATOR_HOST",
        "GCE_METADATA_HOST", "GCE_METADATA_IP", "GCE_METADATA_ROOT", "GCE_METADATA_TIMEOUT")
_OPTIONS = ("api_endpoint", "credentials_file", "quota_project_id", "universe_domain", "api_audience", "api_key")
_SERVICE_ACCOUNT = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9._-]*@[a-zA-Z0-9.-]+\.iam\.gserviceaccount\.com\Z")


def enabled() -> bool:
    mode = os.environ.get(FLAG, "false")
    if mode not in {"true", "false"}:
        raise GuardUnavailable(FAILURE)
    return mode == "true"


def _environment() -> tuple[str | None, ...]:
    return tuple(os.environ.get(key) for key in _ENV)


def _credential_binding(credentials) -> tuple:
    from google.auth.compute_engine import Credentials as ComputeCredentials
    from google.oauth2.service_account import Credentials as ServiceCredentials

    if not isinstance(credentials, (ComputeCredentials, ServiceCredentials)):
        raise GuardUnavailable(FAILURE)
    principal = credentials.service_account_email
    if type(principal) is not str or not _SERVICE_ACCOUNT.fullmatch(principal):
        raise GuardUnavailable(FAILURE)
    return (type(credentials), principal, tuple(credentials.scopes or ()),
            tuple(credentials.default_scopes or ()), credentials.quota_project_id,
            getattr(credentials, "_universe_domain", None))


def _resolve_principal(credentials) -> None:
    from google.auth.compute_engine import Credentials as ComputeCredentials
    from google.auth.transport.requests import Request

    # A metadata credential initially called 'default' is not a principal pin.
    # Resolve that SAME SDK credential before publishing the process transport.
    if isinstance(credentials, ComputeCredentials) and credentials.service_account_email == "default":
        deadline = monotonic() + INITIAL_RESOLUTION_SECONDS
        calls = 0
        request = Request()
        request.session.trust_env = False
        try:
            def bounded_request(*args, **kwargs):
                nonlocal calls
                remaining = deadline - monotonic()
                if calls >= INITIAL_RESOLUTION_REQUESTS or remaining <= 0:
                    raise GuardUnavailable(FAILURE)
                calls += 1
                try:
                    kwargs.update(timeout=remaining, allow_redirects=False)
                    response = request(*args, **kwargs)
                    if type(response.status) is not int or response.status != 200 or monotonic() >= deadline:
                        raise GuardUnavailable(FAILURE)
                    return response
                except Exception:
                    # google-auth's metadata helper retries TransportError and
                    # retryable statuses with backoff. Neither escapes this
                    # callback, so initial resolution never enters that loop.
                    raise GuardUnavailable(FAILURE) from None
            credentials.refresh(bounded_request)
            if monotonic() >= deadline:
                raise GuardUnavailable(FAILURE)
        finally:
            request.session.close()
    _credential_binding(credentials)


class ProcessMetadataTransport:
    """One process/configuration-owned Admin client, never an allow decision."""

    def __init__(self, configuration: CandidateNativeConfiguration):
        from google.cloud.firestore_admin_v1.services.firestore_admin import FirestoreAdminClient

        if os.getpid() != _PID or not enabled():
            raise GuardUnavailable(FAILURE)
        self.pid, self.configuration = os.getpid(), canonical(configuration.model_dump(mode="json"))
        self.environment = _environment()
        self.pins = (configuration.execution_witness_pin.registry,
                     configuration.publication_resource.pin.authority.registry)
        self.pin_bytes = tuple(canonical(pin.model_dump(mode="json")) for pin in self.pins)
        self.admin = FirestoreAdminClient()
        self.transport = self.admin.transport
        self.credentials = self.transport._credentials
        try:
            _resolve_principal(self.credentials)
            self.credential_binding = _credential_binding(self.credentials)
            self.options = self._options()
            self.cohorts: PreSendIdentityCohorts | None = None
            self.lock = Lock()
            self.guard()
        except Exception:
            try:
                self.transport.close()
            except Exception:
                pass
            raise GuardUnavailable(FAILURE) from None

    def _options(self) -> tuple:
        from google.api_core.client_options import ClientOptions

        options = self.admin._client_options
        if not isinstance(options, ClientOptions):
            raise GuardUnavailable(FAILURE)
        return (self.admin.api_endpoint, self.admin.universe_domain, self.transport.host,
                tuple(getattr(options, key, None) for key in _OPTIONS),
                tuple(options.scopes or ()), self.admin._use_client_cert, self.admin._use_mtls_endpoint,
                self.admin._client_cert_source)

    def guard(self) -> None:
        try:
            if (os.getpid() != self.pid or self.pid != _PID or not enabled()
                    or _environment() != self.environment or self.admin.transport is not self.transport
                    or self.transport._credentials is not self.credentials
                    or tuple(canonical(pin.model_dump(mode="json")) for pin in self.pins) != self.pin_bytes
                    or self._options() != self.options
                    or _credential_binding(self.credentials) != self.credential_binding):
                raise GuardUnavailable(FAILURE)
        except Exception:
            raise GuardUnavailable(FAILURE) from None

    def for_bindings(self, bindings: tuple[tuple[FirestoreRpc, RegistryPin], ...]) -> PreSendIdentityCohorts:
        from ..recovery.native_identity_cohorts import PreSendIdentityCohorts

        self.guard()
        with self.lock:
            if self.cohorts is None:
                self.cohorts = PreSendIdentityCohorts(bindings, shared_transport=self)
            return self.cohorts


def process_metadata_transport(configuration: CandidateNativeConfiguration) -> ProcessMetadataTransport:
    global _state
    # Check before touching an inherited lock: gRPC clients/locks never cross fork.
    if os.getpid() != _PID or not enabled():
        raise GuardUnavailable(FAILURE)
    with _lock:
        if _state is None:
            _state = ProcessMetadataTransport(configuration)
        _state.guard()
        if canonical(configuration.model_dump(mode="json")) != _state.configuration:
            raise GuardUnavailable(FAILURE)
        return _state


def shared_metadata_snapshot() -> dict[str, int]:
    """Measurement only; no SDK initialization, identity values or auth output."""
    if os.getpid() != _PID:
        raise GuardUnavailable(FAILURE)
    current = _state
    if current is None:
        return {}
    current.guard()
    return current.cohorts.snapshot() if current.cohorts is not None else {}
