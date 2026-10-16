"""Production composition for separately approved submit; closed without exact native pins.

Factory construction validates configuration only. No SDK clients, credential
resolution, subjects or devices are created until an explicitly authorized call.
"""

from __future__ import annotations

import os
import time
from threading import Lock
from typing import TYPE_CHECKING, Literal

from pydantic import Field, model_serializer, model_validator

from .contracts import canonical, fingerprint
from .gcp_contracts import WitnessBody, WitnessPin
from .gcp_partitioned_contracts import PartitionedResource
from .gcp_submit_contracts import SubmitBinding, SubmitContext, SubmitContract, SubmitGrantPin
from .pairing_contracts import PositiveInteger
from .store import GuardDenied, GuardUnavailable

if TYPE_CHECKING:
    from .gcp_submit import GcpSubmitAuthority, SubmitBeginAck

__all__ = [
    "SubmitBinding",
    "SubmitContext",
    "SubmitNativeConfiguration",
    "production_submit_authority",
]


class SubmitNativeConfiguration(SubmitContract):
    execution_witness_pin: WitnessPin
    execution_witness_body: WitnessBody
    publication_resource: PartitionedResource
    device_grant_pin: SubmitGrantPin | None = Field(default=None, exclude_if=lambda value: value is None)
    device_enrollment: Literal["operator", "paired"] = "operator"
    epoch_generation: PositiveInteger
    executor_revision: str

    @model_validator(mode="after")
    def exact(self) -> SubmitNativeConfiguration:
        pin, body, resource = (
            self.execution_witness_pin,
            self.execution_witness_body,
            self.publication_resource,
        )
        if ((self.device_enrollment == "operator") != (self.device_grant_pin is not None)):
            raise ValueError("Operator mode requires its exact grant; paired mode forbids it")
        if (
            pin.registry != body.registry
            or pin.registry != resource.pin.authority.target
            or body.state != "OPEN"
            or pin.sha256 != fingerprint(body.model_dump(mode="json"))
            or not self.executor_revision
            or len(self.executor_revision) > 160
            or any(
                char not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-"
                for char in self.executor_revision
            )
        ):
            raise ValueError(
                "Exact independent native submit resources, OPEN witness and executor pins required"
            )
        return self


    @model_serializer(mode="wrap")
    def old_operator_configuration(self, handler):
        value = handler(self)
        if self.device_enrollment == "operator":
            value.pop("device_enrollment", None)
        return value


def _sdk_authority(config: SubmitNativeConfiguration) -> GcpSubmitAuthority:
    # This is the only ambient-credential path, reached after complete strict
    # structural validation and never during an SQL transaction in the caller.
    from google.cloud.firestore_admin_v1.services.firestore_admin import FirestoreAdminClient
    from google.cloud.firestore_v1.services.firestore import FirestoreClient
    from google.cloud.storage import Client

    from .gcp_buffer import BufferedRegistry
    from .gcp_journal import GcsJournal, GcsWitnessFence
    from .gcp_partitioned_publication import PartitionedPublicationCoordinator
    from .gcp_rpc import FirestoreRpc
    from .gcp_submit import GcpSubmitAuthority

    client, admin = FirestoreClient(), FirestoreAdminClient()
    resource = config.publication_resource
    native_rpc = FirestoreRpc(
        client, admin, database_resource=resource.pin.authority.registry.database
    )
    execution_rpc = FirestoreRpc(
        client, admin, database_resource=config.execution_witness_pin.registry.database
    )
    project = resource.pin.authority.registry.database.split("/")[1]
    storage = Client(project=project)
    publication = PartitionedPublicationCoordinator(
        BufferedRegistry(native_rpc, deadline_seconds=5), resource, storage.bucket(resource.bucket)
    )
    journal = GcsJournal(storage.bucket(resource.bucket), publication=publication)
    fence = GcsWitnessFence(
        storage.bucket(config.execution_witness_pin.bucket),
        execution_rpc,
        config.execution_witness_pin,
        config.execution_witness_body,
    )
    return GcpSubmitAuthority(
        publication,
        journal,
        fence,
        config.device_grant_pin,
        epoch_generation=config.epoch_generation,
        executor_revision=config.executor_revision,
        device_enrollment=config.device_enrollment,
        now_ms=lambda: time.time_ns() // 1_000_000,
    )


class ConfiguredSubmitAuthority:
    def __init__(self, configuration: SubmitNativeConfiguration):
        self._raw = canonical(configuration.model_dump(mode="json"))
        self._native: GcpSubmitAuthority | None = None
        self._lock = Lock()

    def _configured(self) -> SubmitNativeConfiguration:
        if (
            os.environ.get("BROWSER_SUBMIT_NATIVE_ENABLED") != "true"
            or os.environ.get("DISABLE_GCS_PYTHON_CLIENT_OTEL_BUCKET_METADATA") != "true"
        ):
            raise GuardUnavailable("Independent native submit composition is disabled")
        config = SubmitNativeConfiguration.model_validate_json(self._raw)
        if (
            os.environ.get("BROWSER_ASSISTANCE_SUBMIT_EXECUTOR_REVISION")
            != config.executor_revision
        ):
            raise GuardDenied("Submit executor revision changed from its native deployment pin")
        current = os.environ.get("BROWSER_SUBMIT_NATIVE_CONFIG", "")
        if (
            not current
            or canonical(
                SubmitNativeConfiguration.model_validate_json(current).model_dump(mode="json")
            )
            != self._raw
        ):
            raise GuardDenied("Native submit deployment configuration changed")
        return config

    def _authority(self) -> GcpSubmitAuthority:
        config = self._configured()
        with self._lock:
            if self._native is None:
                self._native = _sdk_authority(config)
            return self._native

    def reserve(self, context: SubmitContext, binding: SubmitBinding) -> SubmitBeginAck | None:
        return self._authority().reserve(context, binding)

    def consume_ack(self, ack: SubmitBeginAck, binding: SubmitBinding) -> dict:
        return self._authority().consume_ack(ack, binding)

    def provision_device(self) -> None:
        """Explicit root-owned provisioning call; never part of normal reserve."""
        self._authority().provision_device()


def production_submit_authority() -> ConfiguredSubmitAuthority:
    if (
        os.environ.get("BROWSER_SUBMIT_NATIVE_ENABLED") != "true"
        or os.environ.get("DISABLE_GCS_PYTHON_CLIENT_OTEL_BUCKET_METADATA") != "true"
    ):
        raise GuardUnavailable("Independent native submit composition is disabled")
    raw = os.environ.get("BROWSER_SUBMIT_NATIVE_CONFIG", "")
    if not raw or len(raw.encode()) > 16_384:
        raise GuardUnavailable("Exact native submit deployment configuration is unavailable")
    try:
        configured = ConfiguredSubmitAuthority(SubmitNativeConfiguration.model_validate_json(raw))
        configured._configured()
        return configured
    except Exception:
        raise GuardUnavailable("Exact native submit deployment configuration is invalid") from None
