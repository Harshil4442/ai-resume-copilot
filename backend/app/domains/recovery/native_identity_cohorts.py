"""Opt-in pre-send metadata cohorts; never a retained identity or authority cache.

Only already-waiting calls on one exact metadata transport can share a List.
Every caller keeps its own observation scope, UID checks and original deadline.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from threading import Condition
from time import monotonic
from typing import TYPE_CHECKING, cast

from .gcp_contracts import RegistryPin
from .native_metadata_admission import (
    MetadataAdmissionUnavailable,
    MetadataCapacityUnavailable,
)
from .store import GuardUnavailable

if TYPE_CHECKING:
    from ..candidate_accounts.metadata_transport import ProcessMetadataTransport
    from .gcp_rpc import FirestoreIdentityList, FirestoreRpc

WINDOW_SECONDS = 0.002
MAX_PENDING = 32
MAX_COHORT_SIZE = 8
MAX_COHORTS = 8
FAILURE = "Fresh pre-send identity metadata is unavailable"
Values = tuple[tuple[str, tuple[str, str, str]], ...]


@dataclass(frozen=True)
class _Ticket:
    rpcs: tuple[FirestoreRpc, ...]
    clients: tuple[object, ...]


@dataclass
class _Cohort:
    seal_at: float
    members: int = 0
    complete: bool = False
    values: Values | None = None
    failure: str | None = None


class PreSendIdentityCohorts:
    """One bounded, transport-owned rendezvous with no after-send joining.

    The first waiting request owns the physical RPC; no background task or copied
    Context runs authorization. Completed values survive only for sealed waiters.
    """

    def __init__(self, bindings: tuple[tuple[FirestoreRpc, RegistryPin], ...], *,
                 shared_transport: ProcessMetadataTransport | None = None):
        from .gcp_rpc import FirestoreIdentityList, FirestoreRpc

        if (type(bindings) is not tuple or len(bindings) != 2
                or any(type(rpc) is not FirestoreRpc or type(pin) is not RegistryPin
                       or rpc.database_resource != pin.database for rpc, pin in bindings)):
            raise GuardUnavailable(FAILURE)
        rpcs = cast(tuple[FirestoreRpc, FirestoreRpc], tuple(rpc for rpc, _ in bindings))
        self.pins = tuple(pin for _, pin in bindings)
        checked = FirestoreIdentityList(rpcs)
        if shared_transport is not None:
            from ..candidate_accounts.metadata_transport import ProcessMetadataTransport

            if (type(shared_transport) is not ProcessMetadataTransport
                    or checked.admin is not shared_transport.admin or self.pins != shared_transport.pins):
                raise GuardUnavailable(FAILURE)
            shared_transport.guard()
        self.shared_transport = shared_transport
        # The process coordinator retains no request RPC/data client references.
        self.rpcs: tuple[FirestoreRpc, ...] = rpcs if shared_transport is None else ()
        self.admin, self.transport = checked.admin, checked.admin.transport
        self.clients = tuple(rpc.client for rpc in rpcs) if shared_transport is None else ()
        self.names, self.parent, self.timeout = checked.names, checked.parent, checked.timeout
        self.timeouts = tuple(rpc.rpc_timeout for rpc in rpcs)
        self.configuration = self._configuration()
        self.condition = Condition()
        self.open: _Cohort | None = None
        self.pending = self.active = 0
        # Aggregate local experiment measurements only, never authorization.
        self.counts = dict.fromkeys(("validation_started", "validation_entered", "validation_succeeded",
                                    "validation_failed", "barriers_started", "barriers_succeeded",
                                    "barriers_failed", "cohorts_started", "cohorts_completed",
                                    "admission_rejections"), 0)

    def validation(self, phase: str) -> None:
        if phase not in {"started", "entered", "succeeded", "failed"}:
            raise GuardUnavailable(FAILURE)
        with self.condition:
            self.counts["validation_" + phase] += 1

    def snapshot(self) -> dict[str, int]:
        with self.condition:
            return {"candidate_" + key: value for key, value in self.counts.items()} | {
                "candidate_pending": self.pending, "candidate_active": self.active}

    @staticmethod
    def _configuration() -> tuple[str | None, ...]:
        return tuple(os.environ.get(key) for key in (
            "CANDIDATE_ACCOUNT_LIST_IDENTITY_ENABLED", "CANDIDATE_METADATA_ADMISSION_ENABLED",
            "CANDIDATE_METADATA_CALLS_PER_MINUTE", "GOOGLE_CLOUD_PROJECT"))

    def _guard(self, batch: FirestoreIdentityList, ticket: _Ticket | None = None) -> None:
        from .gcp_rpc import FirestoreIdentityList, FirestoreRpc

        if self.shared_transport is not None:
            self.shared_transport.guard()

        if (type(batch) is not FirestoreIdentityList or batch.cohorts is not self
                or batch.pins != self.pins or len(batch.rpcs) != 2
                or any(type(rpc) is not FirestoreRpc for rpc in batch.rpcs)
                or (self.shared_transport is None and any(actual is not expected
                    for actual, expected in zip(batch.rpcs, self.rpcs, strict=True)))
                or self.admin.transport is not self.transport or batch.admin is not self.admin
                or batch.names != self.names or batch.parent != self.parent or batch.timeout != self.timeout
                or any(rpc.admin is not self.admin
                       or rpc.database_resource != name or rpc.rpc_timeout != timeout
                       for rpc, name, timeout in zip(batch.rpcs, self.names, self.timeouts, strict=True))
                or (self.shared_transport is None and any(rpc.client is not client
                    for rpc, client in zip(batch.rpcs, self.clients, strict=True)))
                or (ticket is not None and any(rpc is not original or rpc.client is not client
                    for rpc, original, client in zip(batch.rpcs, ticket.rpcs, ticket.clients, strict=True)))
                or self._configuration() != self.configuration
                or self.configuration[:2] != ("true", "true")):
            raise GuardUnavailable(FAILURE)

    def _freeze(self, batch: FirestoreIdentityList, values: dict) -> Values:
        if type(values) is not dict or set(values) != {id(rpc) for rpc in batch.rpcs}:
            raise GuardUnavailable(FAILURE)
        frozen = []
        for rpc, pin in zip(batch.rpcs, self.pins, strict=True):
            value = values[id(rpc)]
            expected = (pin.database, str(pin.database_uid), "FIRESTORE_NATIVE")
            if (type(value) is not dict or set(value) != {"name", "uid", "type"}
                    or any(type(value[key]) is not str for key in ("name", "uid", "type"))
                    or tuple(value[key] for key in ("name", "uid", "type")) != expected):
                raise GuardUnavailable(FAILURE)
            frozen.append((pin.database, expected))
        return tuple(frozen)

    def _publish(self, cohort: _Cohort, values: Values | None, failure: str | None) -> None:
        with self.condition:
            cohort.values, cohort.failure, cohort.complete = values, failure, True
            self.active -= 1
            self.counts["cohorts_completed"] += 1
            self.condition.notify_all()

    def read(self, batch: FirestoreIdentityList) -> dict[int, dict]:
        with self.condition:
            self.counts["barriers_started"] += 1
        try:
            result = self._read(batch)
        except BaseException:
            with self.condition:
                self.counts["barriers_failed"] += 1
            raise
        else:
            with self.condition:
                self.counts["barriers_succeeded"] += 1
            return result

    def _read(self, batch: FirestoreIdentityList) -> dict[int, dict]:
        self._guard(batch)
        ticket = _Ticket(tuple(batch.rpcs), tuple(rpc.client for rpc in batch.rpcs))
        # Redis admission remains <=1s and this exact SDK call retains its own
        # finite timeout. Waiting never resets the caller's separate 30s scope.
        deadline = monotonic() + self.timeout + 1.1
        with self.condition:
            self._guard(batch, ticket)
            if (self.pending >= MAX_PENDING or (self.open is None and self.active >= MAX_COHORTS)
                    or (self.open is not None and self.open.members >= MAX_COHORT_SIZE)):
                self.counts["admission_rejections"] += 1
                raise MetadataCapacityUnavailable()
            leader = self.open is None
            if leader:
                self.open = _Cohort(monotonic() + WINDOW_SECONDS)
                self.active += 1
                self.counts["cohorts_started"] += 1
            cohort = self.open
            assert cohort is not None
            cohort.members += 1
            self.pending += 1
            self.condition.notify_all()
        try:
            if leader:
                try:
                    with self.condition:
                        while monotonic() < cohort.seal_at and cohort.members < MAX_COHORT_SIZE:
                            self.condition.wait(max(0, cohort.seal_at - monotonic()))
                        # Detach BEFORE admission and SDK send. No in-flight or
                        # completed cohort is reachable by a newly arriving call.
                        if self.open is not cohort:
                            raise GuardUnavailable(FAILURE)
                        self.open = None
                        self._guard(batch, ticket)
                    observed = batch._identities_uncached()
                    self._guard(batch, ticket)
                    values = self._freeze(batch, observed)
                except BaseException as exc:
                    with self.condition:
                        if self.open is cohort:
                            self.open = None
                    failure = ("capacity" if type(exc) is MetadataCapacityUnavailable else
                               "admission" if type(exc) is MetadataAdmissionUnavailable else "unavailable")
                    self._publish(cohort, None, failure)
                    raise
                else:
                    self._publish(cohort, values, None)
            with self.condition:
                while not cohort.complete and monotonic() < deadline:
                    self.condition.wait(max(0, deadline - monotonic()))
                self._guard(batch, ticket)
                if not cohort.complete or monotonic() >= deadline:
                    raise GuardUnavailable(FAILURE)
                if cohort.failure == "capacity":
                    raise MetadataCapacityUnavailable()
                if cohort.failure == "admission":
                    raise MetadataAdmissionUnavailable()
                if cohort.failure is not None or cohort.values is None:
                    raise GuardUnavailable(FAILURE)
                by_name = dict(cohort.values)
                return {id(rpc): dict(zip(("name", "uid", "type"), by_name[name], strict=True))
                        for rpc, name in zip(ticket.rpcs, self.names, strict=True)}
        finally:
            with self.condition:
                self.pending -= 1
                cohort.members -= 1
                if cohort.members == 0:
                    cohort.values = cohort.failure = None
