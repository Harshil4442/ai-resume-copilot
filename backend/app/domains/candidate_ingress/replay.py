"""Independent native replay authority; original ACK only, default unavailable."""

from __future__ import annotations

from dataclasses import dataclass, field
from threading import Lock
from typing import Protocol

from pydantic import ValidationError

from ..recovery.contracts import canonical
from ..recovery.gcp_buffer import BufferedRegistry, BufferedTransaction
from ..recovery.gcp_rpc import FirestoreRpc
from ..recovery.store import GuardDenied, GuardUnavailable
from .contracts import IngressAssertion, IngressControl, IngressResource, RetainedIngress
from .crypto import IngressKey

CONTROL_NAMESPACE = "candidate_auth_ingress_control"
REPLAY_NAMESPACE = "candidate_auth_ingress_replays"


class TrustedIngressEnvironment(Protocol):
    """Custody/restore/clock proof belongs to a separately reviewed provision."""

    def check(self, resource: IngressResource) -> None: ...
    def now_ms(self) -> int: ...


@dataclass(repr=False)
class _OriginalReplayAck:
    owner: object = field(repr=False)
    assertion: IngressAssertion = field(repr=False)
    used: bool = False
    lock: Lock = field(default_factory=Lock, repr=False)


class GcpCandidateIngressReplay:
    def __init__(
        self, rpc: FirestoreRpc, resource: IngressResource, environment: TrustedIngressEnvironment
    ):
        if (
            not isinstance(rpc, FirestoreRpc)
            or not isinstance(resource, IngressResource)
            or rpc.database_resource != resource.registry.database
            or environment is None
        ):
            raise GuardUnavailable("Explicit independent ingress replay custody is required")
        self.rpc, self.resource, self.environment = rpc, resource, environment
        self._registry = BufferedRegistry(rpc, max_attempts=3, deadline_seconds=3)
        self._owner = object()

    def now_ms(self) -> int:
        value = self.environment.now_ms()
        if type(value) is not int or not 0 < value <= 2**53 - 1:
            raise GuardUnavailable("Trusted ingress clock is unavailable")
        return value

    def _custody(self) -> None:
        self.environment.check(self.resource)
        identity = self.rpc.identity()
        if identity != {
            "name": self.resource.registry.database,
            "uid": str(self.resource.registry.database_uid),
            "type": "FIRESTORE_NATIVE",
        }:
            raise GuardUnavailable("Independent ingress database identity is unavailable")

    def _fresh(self, assertion: IngressAssertion) -> int:
        now = self.now_ms()
        if (
            assertion.origin != self.resource.origin
            or assertion.key_id != self.resource.key_id
            or not assertion.issued_at_ms <= now < assertion.expires_at_ms
        ):
            raise GuardDenied("Private candidate transport authentication failed")
        return now

    def _admit(self, assertion: IngressAssertion) -> _OriginalReplayAck:
        if not isinstance(assertion, IngressAssertion):
            raise GuardDenied("Private candidate transport authentication failed")

        def transaction(tx: BufferedTransaction) -> None:
            try:
                raw = tx.get(CONTROL_NAMESPACE, "root")
                if raw is None:
                    raise GuardUnavailable("Independent ingress OPEN root is required")
                control = IngressControl.model_validate(raw)
                if (
                    canonical(raw) != canonical(control.model_dump(mode="json"))
                    or control.resource != self.resource
                    or control.state != "OPEN"
                ):
                    raise GuardUnavailable("Independent ingress OPEN root is unavailable")
                if tx.get(REPLAY_NAMESPACE, str(assertion.request_id)) is not None:
                    raise GuardDenied("Private candidate transport authentication failed")
                retained = RetainedIngress(
                    resource=self.resource,
                    assertion=assertion,
                    accepted_at_ms=self._fresh(assertion),
                )
                tx.put(
                    REPLAY_NAMESPACE,
                    str(assertion.request_id),
                    retained.model_dump(mode="json"),
                    immutable=True,
                )
            except ValidationError:
                raise GuardUnavailable("Independent ingress control is unavailable") from None

        self._registry.run(transaction, before_attempt=self._custody)
        # This invocation writes exactly one fresh row or raises. The ACK is
        # minted only after a complete native Commit, never a status read.
        self._fresh(assertion)
        return _OriginalReplayAck(self._owner, assertion)

    def _consume(self, ack: _OriginalReplayAck, assertion: IngressAssertion) -> None:
        if (
            type(ack) is not _OriginalReplayAck
            or ack.owner is not self._owner
            or ack.assertion != assertion
        ):
            raise GuardDenied("An original ingress acknowledgement is required")
        with ack.lock:
            if ack.used:
                raise GuardDenied("An original ingress acknowledgement is required")
            ack.used = True
        self._fresh(assertion)


class CandidatePrivateIngress:
    """Only authorizes this private transport invocation, never an action/context."""

    def __init__(self, replay: GcpCandidateIngressReplay, key: IngressKey):
        if (
            not isinstance(replay, GcpCandidateIngressReplay)
            or not isinstance(key, IngressKey)
            or replay.resource.key_id != key.key_id
        ):
            raise GuardUnavailable("Explicit matching private ingress components are required")
        self.replay, self.key = replay, key

    def authorize(
        self, header: str, *, method: str, path: bytes, raw: bytes, authorization: bytes
    ) -> None:
        assertion = self.key.verify(
            header,
            resource=self.replay.resource,
            method=method,
            path=path,
            raw=raw,
            authorization=authorization,
            now_ms=self.replay.now_ms(),
        )
        ack = self.replay._admit(assertion)
        self.replay._consume(ack, assertion)


def production_candidate_ingress() -> CandidatePrivateIngress:
    # An env key cannot establish independent UID/custody/OPEN/restore, clock,
    # retention or an original replay ACK. No automatic production clients.
    raise GuardUnavailable("Private candidate ingress is unavailable pending reviewed provision")
