"""Exact private ingress composition; no initialization, SQL or cached clock grant.

Each authorization owns its clock anchor and replay acknowledgement. SDK clients
use their normal authenticated TLS transport; only tests inject their transports.
"""

from __future__ import annotations

import base64
import json
import os
import re
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING

from pydantic import model_validator

from ..candidate_accounts.production import CandidateNativeConfiguration
from ..recovery.contracts import canonical
from ..recovery.gcp_buffer import BufferedRegistry, BufferedTransaction
from ..recovery.gcp_pairing import pairing_control_record
from ..recovery.gcp_partitioned_contracts import PartitionedRoot
from ..recovery.gcp_service import control_record
from ..recovery.store import GuardUnavailable
from .contracts import (
    MAX_HEADER_BYTES,
    IngressAssertion,
    IngressClockSynchronizationRequired,
    IngressControl,
    IngressResource,
)
from .crypto import IngressKey
from .replay import CONTROL_NAMESPACE, CandidatePrivateIngress, GcpCandidateIngressReplay

if TYPE_CHECKING:
    from ..recovery.gcp_journal import GcsWitnessFence
    from ..recovery.gcp_partitioned_publication import PartitionedPublicationCoordinator
    from ..recovery.gcp_rpc import FirestoreRpc

FAILURE = "Private candidate ingress native configuration is unavailable"
MAX_READ_LATENCY_NS = 500_000_000
MAX_ANCHOR_AGE_NS = 2_000_000_000
MAX_OPERATION_NS = 5_000_000_000


class CandidateIngressNativeConfiguration(CandidateNativeConfiguration):
    resource: IngressResource

    @model_validator(mode="after")
    def exact_ingress(self) -> CandidateIngressNativeConfiguration:
        if self.resource.action_registries != (
            self.execution_witness_pin.registry,
            self.publication_resource.pin.authority.registry,
        ):
            raise ValueError("Ingress requires the exact two current action registry pins")
        return self


def _pairs(items):
    value = {}
    for key, child in items:
        if key in value:
            raise ValueError("Duplicate native ingress configuration field")
        value[key] = child
    return value


def _constant(_: str):
    raise ValueError("Nonfinite native ingress configuration")


def _configuration() -> tuple[CandidateIngressNativeConfiguration, IngressKey]:
    """Structural checks only. Never resolve credentials or open a connection."""
    from ..candidate_accounts.production import _configuration as account_configuration

    try:
        if (
            os.environ.get("CANDIDATE_INGRESS_NATIVE_ENABLED") != "true"
            or os.environ.get("DISABLE_GCS_PYTHON_CLIENT_OTEL_BUCKET_METADATA") != "true"
            or os.environ.get("FIRESTORE_EMULATOR_HOST")
            or os.environ.get("STORAGE_EMULATOR_HOST")
        ):
            raise ValueError("Ingress is disabled")
        raw = os.environ.get("CANDIDATE_INGRESS_NATIVE_CONFIG", "")
        if not raw or len(raw.encode("utf-8")) > 32_768:
            raise ValueError("Missing or oversized ingress configuration")
        config = CandidateIngressNativeConfiguration.model_validate(
            json.loads(raw, object_pairs_hook=_pairs, parse_constant=_constant)
        )
        account = account_configuration()
        if any(
            getattr(config, field) != getattr(account, field)
            for field in CandidateNativeConfiguration.model_fields
        ):
            raise ValueError("Private ingress and account action resources disagree")
        secret = os.environ.get("CANDIDATE_AUTH_TRANSPORT_SECRET", "")
        key_id = os.environ.get("CANDIDATE_AUTH_TRANSPORT_KEY_ID", "")
        if (
            re.fullmatch(r"[a-f0-9]{64}", secret) is None
            or key_id != config.resource.key_id
            or any(
                secret == os.environ.get(name, "").strip()
                for name in (
                    "NEXTAUTH_SECRET",
                    "SECRET_KEY",
                    "BROWSER_PAIRING_GATEWAY_SECRET",
                    "BROWSER_ASSISTANCE_SIGNING_SECRET",
                )
            )
        ):
            raise ValueError("A distinct exact private ingress key is required")
        return config, IngressKey(key_id, bytes.fromhex(secret))
    except Exception:
        raise GuardUnavailable(FAILURE) from None


@dataclass(frozen=True)
class _ClockAnchor:
    provider_ns: int
    start_ns: int
    end_ns: int


class GcpTrustedIngressEnvironment:
    """Per-invocation fresh native controls and bounded provider UTC projection."""

    def __init__(
        self,
        configuration: CandidateIngressNativeConfiguration,
        replay_rpc: FirestoreRpc,
        execution_rpc: FirestoreRpc,
        fence: GcsWitnessFence,
        publication: PartitionedPublicationCoordinator,
    ):
        self.config, self.rpc, self.execution, self.fence, self.publication = (
            configuration, replay_rpc, execution_rpc, fence, publication
        )
        self._anchor: _ClockAnchor | None = None
        self._started_ns = time.monotonic_ns()

    def _bounds(self) -> tuple[int, int]:
        anchor, now = self._anchor, time.monotonic_ns()
        if (
            anchor is None
            or not anchor.start_ns <= anchor.end_ns <= now
            or now - anchor.end_ns >= MAX_ANCHOR_AGE_NS
            or now - self._started_ns >= MAX_OPERATION_NS
        ):
            raise GuardUnavailable("Fresh trusted ingress time is unavailable")
        # The read occurred between request start and completion. Round outward;
        # admission requires issue <= lower and expiry > upper, not a guessed UTC.
        lower = (anchor.provider_ns + now - anchor.end_ns) // 1_000_000
        upper = (anchor.provider_ns + now - anchor.start_ns + 999_999) // 1_000_000
        if not 0 < lower <= upper <= 2**53 - 1:
            raise GuardUnavailable("Fresh trusted ingress time is unavailable")
        return lower, upper

    def now_ms(self) -> int:
        return self._bounds()[1]

    def not_before_ms(self) -> int:
        return self._bounds()[0]

    def close(self) -> None:
        # The production builder creates these clients for this invocation only.
        # Closing transports does not perform provider IO or recover any ACK.
        for client in (self.rpc.client.transport, self.rpc.admin.transport, self.fence.bucket.client):
            try:
                client.close()
            except Exception:
                pass

    def _clock_root(self, resource: IngressResource) -> None:
        from google.cloud.firestore_v1.types import BatchGetDocumentsResponse, Document

        path = BufferedTransaction(self.rpc, b"path-only").path(CONTROL_NAMESPACE, "root")
        start = time.monotonic_ns()
        stream = self.rpc.client.batch_get_documents(
            request={"database": resource.registry.database, "documents": (path,)},
            retry=None,
            timeout=0.5,
        )
        response = None
        for item in stream:
            if response is not None or not isinstance(item, BatchGetDocumentsResponse):
                raise GuardUnavailable("An exact fresh ingress clock/control response is required")
            response = item
        end = time.monotonic_ns()
        if response is None or not 0 <= end - start <= MAX_READ_LATENCY_NS:
            raise GuardUnavailable("Fresh ingress provider time exceeded its latency bound")
        wire = BatchGetDocumentsResponse.pb(response)
        if (
            wire.WhichOneof("result") != "found"
            or wire.transaction
            or not wire.HasField("read_time")
            or wire.found.name != path
            or set(wire.found.fields) != {"body"}
            or wire.found.fields["body"].WhichOneof("value_type") != "bytes_value"
            or not wire.found.HasField("update_time")
        ):
            raise GuardUnavailable("An exact fresh ingress clock/control response is required")
        stamp = wire.read_time
        stamp.ToDatetime()  # Validate protobuf's full UTC range and nanoseconds.
        if not 0 <= stamp.nanos < 1_000_000_000:
            raise GuardUnavailable("Fresh ingress provider time is malformed")
        provider_ns = stamp.seconds * 1_000_000_000 + stamp.nanos
        updated = Document.pb(response.found).update_time
        updated.ToDatetime()
        if updated.seconds * 1_000_000_000 + updated.nanos > provider_ns:
            raise GuardUnavailable("Ingress control update follows its provider read time")
        raw = bytes(wire.found.fields["body"].bytes_value)
        expected = canonical({
            "namespace": CONTROL_NAMESPACE,
            "key": "root",
            "value": IngressControl(resource=resource, state="OPEN").model_dump(mode="json"),
        }).encode()
        if raw != expected:
            raise GuardUnavailable("Exact independent ingress OPEN control is unavailable")
        old = self._anchor
        if old is not None and (
            start < old.end_ns
            or provider_ns < old.provider_ns + start - old.end_ns
            or provider_ns > old.provider_ns + end - old.start_ns
        ):
            raise GuardUnavailable("Fresh provider clock disagrees with its invocation bound")
        self._anchor = _ClockAnchor(provider_ns, start, end)
        self._bounds()

    def check(self, resource: IngressResource) -> None:
        try:
            if resource != self.config.resource:
                raise ValueError("Ingress resource changed")
            self.fence.check(self.config.execution_witness_pin.registry)
            controls = BufferedRegistry(self.execution).read_many(
                (("control", "meta"), ("pairing_control", "current"))
            )
            if (
                controls["control", "meta"] != control_record(resource.action_registries[0])
                or controls["pairing_control", "current"]
                != pairing_control_record(resource.action_registries[0], self.config.epoch_generation)
            ):
                raise ValueError("Execution epoch control changed")
            self.publication._fresh_open()
            root = self.publication.registry.read("v3_root", "current")
            expected = PartitionedRoot(pin=self.config.publication_resource.pin, phase="OPEN")
            if root != expected.model_dump(mode="json"):
                raise ValueError("Protected publication OPEN root changed")
            if self.rpc.identity() != {
                "name": resource.registry.database,
                "uid": str(resource.registry.database_uid),
                "type": "FIRESTORE_NATIVE",
            }:
                raise ValueError("Independent ingress database UID changed")
            self._clock_root(resource)
        except Exception:
            raise GuardUnavailable("Fresh private ingress custody, epoch or clock is unavailable") from None


def _build(config: CandidateIngressNativeConfiguration) -> tuple[GcpCandidateIngressReplay, GcpTrustedIngressEnvironment]:
    from google.cloud.firestore_admin_v1.services.firestore_admin import FirestoreAdminClient
    from google.cloud.firestore_v1.services.firestore import FirestoreClient
    from google.cloud.storage import Client

    from ..recovery.gcp_journal import GcsWitnessFence
    from ..recovery.gcp_partitioned_publication import PartitionedPublicationCoordinator
    from ..recovery.gcp_rpc import FirestoreRpc

    client, admin = FirestoreClient(), FirestoreAdminClient()
    witness, resource = config.execution_witness_pin, config.publication_resource
    execution = FirestoreRpc(client, admin, database_resource=witness.registry.database)
    protected = FirestoreRpc(client, admin, database_resource=resource.pin.authority.registry.database)
    replay_rpc = FirestoreRpc(client, admin, database_resource=config.resource.registry.database)
    storage = Client(project=resource.pin.authority.registry.database.split("/")[1])
    fence = GcsWitnessFence(storage.bucket(witness.bucket), execution, witness, config.execution_witness_body)
    publication = PartitionedPublicationCoordinator(BufferedRegistry(protected), resource, storage.bucket(resource.bucket))
    environment = GcpTrustedIngressEnvironment(config, replay_rpc, execution, fence, publication)
    return GcpCandidateIngressReplay(replay_rpc, config.resource, environment), environment


class ConfiguredCandidateIngress:
    """Immutable config only; every call gets fresh clients, environment and ACK."""

    def __init__(self, configuration: CandidateIngressNativeConfiguration, key: IngressKey):
        self._raw = canonical(configuration.model_dump(mode="json"))
        self._key = key

    def _configured(self) -> tuple[CandidateIngressNativeConfiguration, IngressKey]:
        config, key = _configuration()
        if canonical(config.model_dump(mode="json")) != self._raw or key != self._key:
            raise GuardUnavailable("Private ingress deployment configuration changed")
        return config, key

    def authorize(self, header: str, *, method: str, path: bytes, raw: bytes, authorization: bytes) -> None:
        environment = None
        try:
            config, key = self._configured()
            # Authenticate bounded physical input before allocating SDK clients.
            # This claimed issue time is used only for a signature precheck; it
            # is never freshness/custody evidence or an admission decision.
            if type(header) is not str or not header.isascii() or not 0 < len(header) <= MAX_HEADER_BYTES:
                raise ValueError("Private ingress header is malformed")
            encoded = header.split(".", 1)[0]
            claimed = IngressAssertion.model_validate_json(
                base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4))
            )
            key.verify(header, resource=config.resource, method=method, path=path, raw=raw,
                       authorization=authorization, now_ms=claimed.issued_at_ms)
            replay, environment = _build(config)
            environment.check(config.resource)
            # Check the lower UTC bound once before the inner verifier uses the
            # upper bound. An assertion dated in the uncertain future cannot pass.
            lower, upper = environment.not_before_ms(), environment.now_ms()
            if claimed.issued_at_ms > lower or claimed.expires_at_ms <= upper:
                # The HMAC/input binding already passed above and native custody
                # is fresh. A time estimate is safe to return, never an admission.
                raise IngressClockSynchronizationRequired(lower, upper)
            key.verify(header, resource=config.resource, method=method, path=path, raw=raw,
                       authorization=authorization, now_ms=lower)
            CandidatePrivateIngress(replay, key).authorize(
                header, method=method, path=path, raw=raw, authorization=authorization
            )
            self._configured()
        except IngressClockSynchronizationRequired:
            raise
        except Exception:
            raise GuardUnavailable("Private candidate ingress authorization is unavailable") from None
        finally:
            if environment is not None:
                environment.close()


def configured_candidate_ingress() -> ConfiguredCandidateIngress:
    return ConfiguredCandidateIngress(*_configuration())
