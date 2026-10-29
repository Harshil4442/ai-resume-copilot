"""Exact lazy candidate lifecycle composition; no native initialization or adoption."""

from __future__ import annotations

import json
import os
import time
from collections.abc import Callable
from dataclasses import dataclass
from threading import Lock
from typing import TYPE_CHECKING, TypeVar
from uuid import UUID

from pydantic import ConfigDict, model_validator
from sqlalchemy import Engine

from ..recovery.contracts import Contract, canonical, fingerprint
from ..recovery.gcp_contracts import WitnessBody, WitnessPin
from ..recovery.gcp_partitioned_contracts import PartitionedResource
from ..recovery.native_diagnostics import native_phase
from ..recovery.pairing_contracts import PositiveInteger
from ..recovery.store import GuardDenied, GuardUnavailable

if TYPE_CHECKING:
    from sqlalchemy.orm import Session, sessionmaker

    from ..recovery.gcp_journal import GcsWitnessFence
    from ..recovery.gcp_password_lifetime import GcpPasswordLifetimeCoordinator
    from ..recovery.gcp_scoped_authority import ScopedCandidateAuthority
    from ..recovery.password_reauth import CandidateWebSession
    from .contracts import CandidateLoginResult
    from .retention import CandidateLifetimeRetention
    from .service import CandidateAccountService

FAILURE = "Candidate account lifetime configuration is unavailable"
Result = TypeVar("Result")


@dataclass(frozen=True)
class _Composition:
    service: CandidateAccountService
    fence: GcsWitnessFence
    lifetime: GcpPasswordLifetimeCoordinator
    scoped: ScopedCandidateAuthority


class CandidateNativeConfiguration(Contract):
    model_config = ConfigDict(
        extra="forbid", frozen=True, hide_input_in_errors=True, revalidate_instances="always"
    )
    execution_witness_pin: WitnessPin
    execution_witness_body: WitnessBody
    publication_resource: PartitionedResource
    epoch_generation: PositiveInteger

    @model_validator(mode="after")
    def exact(self) -> CandidateNativeConfiguration:
        pin, body, resource = (
            self.execution_witness_pin,
            self.execution_witness_body,
            self.publication_resource,
        )
        if (
            pin.registry != body.registry
            or pin.registry != resource.pin.authority.target
            or body.state != "OPEN"
            or pin.sha256 != fingerprint(body.model_dump(mode="json"))
        ):
            raise ValueError(
                "Exact independent candidate authority resources and OPEN witness required"
            )
        return self


def _pairs(items):
    result = {}
    for key, value in items:
        if key in result:
            raise ValueError("Duplicate configuration field")
        result[key] = value
    return result


def _configuration() -> CandidateNativeConfiguration:
    if (
        os.environ.get("CANDIDATE_ACCOUNT_LIFECYCLE_ENABLED") != "true"
        or os.environ.get("DISABLE_GCS_PYTHON_CLIENT_OTEL_BUCKET_METADATA") != "true"
    ):
        raise GuardUnavailable(FAILURE)
    raw = os.environ.get("CANDIDATE_ACCOUNT_NATIVE_CONFIG", "")
    try:
        if not raw or len(raw.encode("utf-8")) > 16_384:
            raise ValueError("Missing or oversized configuration")
        config = CandidateNativeConfiguration.model_validate(
            json.loads(raw, object_pairs_hook=_pairs)
        )
        shared = os.environ.get("BROWSER_SUBMIT_NATIVE_CONFIG", "")
        if shared:
            from ..recovery.browser_submit import SubmitNativeConfiguration

            if len(shared.encode("utf-8")) > 16_384:
                raise ValueError("Oversized shared authority configuration")
            submit = SubmitNativeConfiguration.model_validate(
                json.loads(shared, object_pairs_hook=_pairs)
            )
            if any(
                getattr(config, key) != getattr(submit, key)
                for key in CandidateNativeConfiguration.model_fields
            ):
                raise ValueError("Candidate and submit authority resources disagree")
        return config
    except Exception:
        raise GuardUnavailable(FAILURE) from None


def _private_engine(source: Engine) -> Engine:
    if not isinstance(source, Engine):
        raise GuardUnavailable(FAILURE)
    if source.hide_parameters is True:
        return source
    # A view retains the trusted pool, dialect, options and engine events without
    # registering new dialect listeners on a pool already serving other threads.
    # Reconstructing an Engine with pool=source.pool mutates its connect-event
    # deque and can interrupt an in-flight checkout. No connection is acquired.
    private = source.execution_options()
    private.hide_parameters = True
    private.echo = False
    return private


def _build(config: CandidateNativeConfiguration) -> _Composition:
    from google.cloud.firestore_admin_v1.services.firestore_admin import FirestoreAdminClient
    from google.cloud.firestore_v1.services.firestore import FirestoreClient
    from google.cloud.storage import Client

    from ...database import engine
    from ..recovery.gcp_buffer import BufferedRegistry
    from ..recovery.gcp_candidate_lifetimes import GcpProtectedCandidateLifetimes
    from ..recovery.gcp_candidate_logout import GcpCandidateCookieLogout
    from ..recovery.gcp_journal import GcsWitnessFence
    from ..recovery.gcp_partitioned_publication import PartitionedPublicationCoordinator
    from ..recovery.gcp_password_lifetime import GcpPasswordLifetimeCoordinator
    from ..recovery.gcp_password_lifetime_journal import GcsPasswordLifetimeJournal
    from ..recovery.gcp_password_reset import GcpPasswordCredentialProjection
    from ..recovery.gcp_rpc import FirestoreRpc
    from ..recovery.gcp_scoped_authority import ScopedCandidateAuthority
    from .retention import NativeCandidateLifetimeRetention
    from .service import CandidateAccountService

    sql = _private_engine(engine)
    client, admin = FirestoreClient(), FirestoreAdminClient()
    resource, witness = config.publication_resource, config.execution_witness_pin
    execution_rpc = FirestoreRpc(client, admin, database_resource=witness.registry.database)
    protected_rpc = FirestoreRpc(
        client, admin, database_resource=resource.pin.authority.registry.database
    )
    storage = Client(project=resource.pin.authority.registry.database.split("/")[1])
    publication = PartitionedPublicationCoordinator(
        BufferedRegistry(protected_rpc), resource, storage.bucket(resource.bucket)
    )
    fence = GcsWitnessFence(
        storage.bucket(witness.bucket), execution_rpc, witness, config.execution_witness_body
    )
    journal = GcsPasswordLifetimeJournal(storage.bucket(resource.bucket), publication=publication)

    def clock() -> int:
        return time.time_ns() // 1_000_000

    lifetime = GcpPasswordLifetimeCoordinator(
        BufferedRegistry(execution_rpc),
        witness.registry,
        epoch_generation=config.epoch_generation,
        journal=journal,
        fence=fence,
        now_ms=clock,
    )
    scoped = ScopedCandidateAuthority(publication, now_ms=clock)
    reset = GcpPasswordCredentialProjection(lifetime, scoped)
    retention = NativeCandidateLifetimeRetention(
        lifetime, GcpProtectedCandidateLifetimes(scoped, reset=reset)
    )
    service = CandidateAccountService(
        sql, retention, now_ms=clock, cookie_logout=GcpCandidateCookieLogout(scoped)
    )
    return _Composition(service, fence, lifetime, scoped)


class ConfiguredCandidateAccounts:
    """One exact configured composition, with SDK clients deferred to first use."""

    def __init__(self, configuration: CandidateNativeConfiguration):
        self._raw = canonical(configuration.model_dump(mode="json"))
        self._instance: _Composition | None = None
        self._lock = Lock()

    def _composition(self) -> _Composition:
        config = _configuration()
        if canonical(config.model_dump(mode="json")) != self._raw:
            raise GuardDenied("Candidate native deployment configuration changed")
        with self._lock:
            if self._instance is None:
                self._instance = _build(config)
            return self._instance

    def _invoke(self, operation: Callable[[CandidateAccountService], Result]) -> Result:
        with native_phase("candidate_account", "composition"):
            current = self._composition()
        with native_phase("candidate_account", "pre_witness"):
            current.fence.check(current.fence.pin.registry)
        with native_phase("candidate_account", "service"):
            result = operation(current.service)
        with native_phase("candidate_account", "post_configuration"):
            self._composition()  # Retain exact deployment configuration after IO.
        with native_phase("candidate_account", "post_witness"):
            current.fence.check(current.fence.pin.registry)
        return result

    def __getattr__(self, name: str):
        # New explicit lifecycle methods retain the same configuration/witness
        # boundary. This is a trusted Python service port, never HTTP dispatch.
        from .service import CandidateAccountService
        if name.startswith("_") or not callable(getattr(CandidateAccountService, name, None)):
            raise AttributeError(name)
        def invoke(*args, **kwargs):
            return self._invoke(lambda service: getattr(service, name)(*args, **kwargs))
        return invoke

    @property
    def sessions(self) -> sessionmaker[Session]:
        """Resolve composition before the caller opens its SQL transaction."""
        return self._composition().service.sessions

    @property
    def retention(self) -> CandidateLifetimeRetention:
        """Trusted enrollment port, never an HTTP/browser declaration."""
        return self._composition().service.retention

    def register(self, *, email: str, password: str, policy_version: str) -> int:
        return self._invoke(
            lambda service: service.register(
                email=email, password=password, policy_version=policy_version
            )
        )

    def registration_status(self, *, email: str, password: str) -> str:
        return self._invoke(
            lambda service: service.registration_status(email=email, password=password)
        )

    def login(self, *, email: str, password: str) -> CandidateLoginResult:
        return self._invoke(lambda service: service.login(email=email, password=password))

    def validate(self, context: CandidateWebSession, *, auth_generation: int) -> None:
        mode = os.environ.get("CANDIDATE_ACCOUNT_LIST_IDENTITY_ENABLED", "false")
        if mode not in {"true", "false"}:
            raise GuardUnavailable("Candidate account identity observation mode is unavailable")
        if mode == "false":
            self._invoke(lambda service: service.validate(context, auth_generation=auth_generation))
            return
        from ..recovery.gcp_rpc import FirestoreIdentityList
        from ..recovery.native_identity_observation import identity_observation_scope
        from ..recovery.native_metadata_admission import require_enabled

        require_enabled()
        current = self._composition()
        fixed = CandidateNativeConfiguration.model_validate_json(self._raw)
        bindings = ((current.lifetime.registry.rpc, fixed.execution_witness_pin.registry),
                    (current.scoped.publication.registry.rpc, fixed.publication_resource.pin.authority.registry))
        batch = FirestoreIdentityList(tuple(rpc for rpc, _ in bindings))
        with identity_observation_scope(bindings, batch=batch):
            # Only ordinary validation is read-only. Lifecycle mutations and
            # signing retain their existing fresh, independent native barriers.
            self._invoke(lambda service: service.validate(context, auth_generation=auth_generation))
            if os.environ.get("CANDIDATE_ACCOUNT_LIST_IDENTITY_ENABLED", "false") != mode:
                raise GuardUnavailable("Candidate account identity observation mode changed")

    def logout(self, context: CandidateWebSession) -> None:
        self._invoke(lambda service: service.logout(context))

    def logout_cookie(
        self, context: CandidateWebSession, *, auth_generation: int, operation_id: UUID
    ) -> None:
        self._invoke(
            lambda service: service.logout_cookie(
                context, auth_generation=auth_generation, operation_id=operation_id
            )
        )

    def change_password(
        self, context: CandidateWebSession, *, current_password: str, new_password: str
    ) -> None:
        self._invoke(
            lambda service: service.change_password(
                context, current_password=current_password, new_password=new_password
            )
        )

    def prepare_delete(self, context: CandidateWebSession) -> None:
        self._invoke(lambda service: service.prepare_delete(context))


@native_phase("candidate_account", "configuration")
def production_candidate_accounts() -> ConfiguredCandidateAccounts:
    return ConfiguredCandidateAccounts(_configuration())
