"""Disabled recovery-authority core; no HTTP, employer or financial operations.

This deliberately narrow slice allows one synthetic fill step per canonical
opening. A begin is possible disclosure, never evidence of completion. Its claim
is permanent here: no retry, refund, proven-no-action or submit implementation.
AuthenticatedActor must originate in a trusted adapter, which is not supplied.
"""
from __future__ import annotations

import time
from collections.abc import Callable
from uuid import uuid4

from pydantic import TypeAdapter, ValidationError

from .contracts import (
    AuthenticatedActor,
    BeginDecision,
    Binding,
    Identifier,
    MayAct,
    ReplayBarrier,
    Revocation,
    fingerprint,
)
from .store import AuthorityStore, GuardDenied, Transaction, production_store

APPROVAL_LIFETIME_MS = 120_000
CHALLENGE_LIFETIME_MS = 10_000
PERMIT_LIFETIME_MS = 10_000
DECISION_LIFETIME_MS = 2_000
IDENTIFIER_ADAPTER = TypeAdapter(Identifier)


def _require(condition: bool, reason: str) -> None:
    if not condition:
        raise GuardDenied(reason)


def _identifier(value: str) -> None:
    try:
        IDENTIFIER_ADAPTER.validate_python(value, strict=True)
    except ValidationError as exc:
        raise GuardDenied("An opaque bounded identifier is required") from exc


class RecoveryGuard:
    def __init__(self, store: AuthorityStore | None = None,
                 now_ms: Callable[[], int] | None = None):
        self.store = store if store is not None else production_store()
        self.now_ms = now_ms or (lambda: time.time_ns() // 1_000_000)

    def _actor(self, tx: Transaction, actor: AuthenticatedActor,
               roles: set[str], *, require_active: bool = True) -> None:
        registered = tx.get("actors", actor.actor_id)
        _require(registered is not None and registered.get("identity") == actor.model_dump(mode="json"),
                 "Authenticated identity does not match the authority registry")
        assert registered is not None
        _require(actor.role in roles and (registered["active"] or not require_active),
                 "Actor is not authorized")

    def _control(self, tx: Transaction, *, require_open: bool = True) -> dict:
        control = tx.get("control", "current")
        _require(control is not None, "Authority control is absent")
        assert control is not None
        if require_open:
            _require(control["status"] == "OPEN", "Authority is closed")
        return control

    def _now(self, tx: Transaction) -> int:
        now = self.now_ms()
        _require(type(now) is int and 0 < now <= 2**53 - 1, "Authority clock is invalid")
        previous = tx.get("clock", "observed")
        _require(previous is None or now >= previous["now_ms"], "Authority clock moved backward")
        tx.put("clock", "observed", {"now_ms": now})
        return now

    def _subject(self, tx: Transaction, subject: str) -> None:
        row = tx.get("subjects", subject)
        _require(row is not None and bool(row["active"]), "Subject is absent or revoked")
        _require(tx.get("revocations", Revocation(subject_uuid=subject, kind="subject",
                 target=subject, revision=0).key) is None, "Subject is revoked")

    def _binding(self, tx: Transaction, binding: Binding) -> None:
        control = self._control(tx)
        _require(str(binding.authority_id) == control["authority_id"] and
                 str(binding.epoch_id) == control["epoch_id"] and
                 binding.epoch_generation == control["generation"], "Epoch is not current")
        self._subject(tx, str(binding.subject_uuid))
        now = self._now(tx)
        _require(now < binding.deadline_ms <= now + APPROVAL_LIFETIME_MS,
                 "Approval deadline is expired or too distant")
        device = tx.get("devices", binding.device_id)
        _require(device is not None and device["active"] and
                 device["subject_uuid"] == str(binding.subject_uuid) and
                 device["key_sha256"] == binding.device_key_sha256 and
                 device["executor_revision"] == binding.executor_revision,
                 "Device binding is not current")
        grant = tx.get("grants", binding.grant_id)
        expected_grant = {
            "subject_uuid": str(binding.subject_uuid), "employer_key": binding.employer_key,
            "tenant_id": binding.tenant_id, "origin": binding.origin,
            "action": binding.action, "revision": binding.grant_revision,
        }
        _require(grant is not None and grant["active"] and grant["scope"] == expected_grant,
                 "Exact tenant/origin/action grant is absent")
        scopes = (("approval", binding.approval_id, binding.approval_revision),
                  ("device", binding.device_id, 0),
                  ("grant", binding.grant_id, binding.grant_revision),
                  ("artifact", binding.artifact_sha256, int(binding.artifact_generation)))
        for kind, target, revision in scopes:
            for revoked_revision in {0, revision}:
                scope = Revocation(subject_uuid=binding.subject_uuid, kind=kind,
                                   target=target, revision=revoked_revision)
                _require(tx.get("revocations", scope.key) is None, "Binding is revoked")

    def _device(self, tx: Transaction, actor: AuthenticatedActor, binding: Binding,
                *, require_active: bool = True) -> None:
        self._actor(tx, actor, {"device"}, require_active=require_active)
        _require(actor.subject_uuid == binding.subject_uuid and
                 actor.device_id == binding.device_id and
                 actor.key_sha256 == binding.device_key_sha256 and
                 actor.executor_revision == binding.executor_revision,
                 "Executor does not own this exact action")

    def _approval(self, tx: Transaction, approval: dict, binding: Binding) -> None:
        candidate = tx.get("actors", approval["actor_id"])
        _require(candidate is not None and candidate["active"] and
                 fingerprint(candidate["identity"]) == approval["actor_digest"] and
                 candidate["identity"]["role"] == "candidate" and
                 candidate["identity"]["subject_uuid"] == str(binding.subject_uuid) and
                 approval["fingerprint"] == binding.fingerprint,
                 "Approving identity or immutable approval is not current")

    def barrier(self, operator: AuthenticatedActor) -> ReplayBarrier:
        """Read local fixture evidence; this is not a production journal replay."""
        def operation(tx: Transaction) -> ReplayBarrier:
            self._actor(tx, operator, {"operator"})
            control = self._control(tx, require_open=False)
            sequence, digest = tx.head()
            return ReplayBarrier(authority_id=control["authority_id"], sequence=sequence,
                                 event_digest=digest, evidence_id="local-fixture-replay")
        return self.store.transact(operation)

    def open(self, operator: AuthenticatedActor, barrier: ReplayBarrier) -> dict:
        """Explicit operator-only opening; no production adapter can invoke it."""
        def operation(tx: Transaction) -> dict:
            self._actor(tx, operator, {"operator"})
            control = self._control(tx, require_open=False)
            _require(control["status"] == "CLOSED", "Authority must first be closed")
            _require(str(barrier.authority_id) == control["authority_id"] and
                     (barrier.sequence, barrier.event_digest) == tx.head(),
                     "Replay barrier does not cover the current authority")
            control = {**control, "status": "OPEN", "barrier": barrier.model_dump(mode="json")}
            tx.put("control", "current", control)
            tx.append(str(uuid4()), "EPOCH_OPENED", {
                "actor_id": operator.actor_id, "epoch_id": control["epoch_id"],
                "generation": control["generation"], "barrier": barrier.model_dump(mode="json"),
            })
            return control
        return self.store.transact(operation)

    def close(self, operator: AuthenticatedActor, event_id: str) -> dict:
        """Advance CLOSED; retries never generate another epoch or remove claims."""
        _identifier(event_id)
        def operation(tx: Transaction) -> dict:
            self._actor(tx, operator, {"operator"})
            prior = tx.event(event_id)
            if prior:
                _require(prior["kind"] == "EPOCH_CLOSED" and
                         prior["payload"]["actor_id"] == operator.actor_id,
                         "Event ID conflicts with another operation")
                return self._control(tx, require_open=False)
            control = self._control(tx, require_open=False)
            control = {"authority_id": control["authority_id"], "epoch_id": str(uuid4()),
                       "generation": control["generation"] + 1, "status": "CLOSED"}
            tx.put("control", "current", control)
            tx.append(event_id, "EPOCH_CLOSED", {
                "actor_id": operator.actor_id, "epoch_id": control["epoch_id"],
                "generation": control["generation"],
            })
            return control
        return self.store.transact(operation)

    def challenge(self, candidate: AuthenticatedActor) -> dict:
        def operation(tx: Transaction) -> dict:
            self._actor(tx, candidate, {"candidate"})
            _require(candidate.subject_uuid is not None, "Candidate subject is absent")
            self._subject(tx, str(candidate.subject_uuid))
            control = self._control(tx)
            row = {"id": str(uuid4()), "actor_id": candidate.actor_id,
                   "actor_digest": fingerprint(candidate.model_dump(mode="json")),
                   "subject_uuid": str(candidate.subject_uuid),
                   "epoch_id": control["epoch_id"], "generation": control["generation"],
                   "expires_at_ms": self._now(tx) + CHALLENGE_LIFETIME_MS, "consumed": False}
            tx.put("challenges", row["id"], row, immutable=True)
            tx.append(str(uuid4()), "CHALLENGE_CREATED", row)
            return row
        return self.store.transact(operation)

    def register_approval(self, candidate: AuthenticatedActor,
                          challenge_id: str, binding: Binding) -> dict:
        _identifier(challenge_id)
        def operation(tx: Transaction) -> dict:
            self._actor(tx, candidate, {"candidate"})
            _require(candidate.subject_uuid == binding.subject_uuid, "Approval belongs to another subject")
            self._binding(tx, binding)
            challenge = tx.get("challenges", challenge_id)
            _require(challenge is not None and not challenge["consumed"] and
                     challenge["actor_id"] == candidate.actor_id and
                     challenge["actor_digest"] == fingerprint(candidate.model_dump(mode="json")) and
                     challenge["subject_uuid"] == str(binding.subject_uuid) and
                     challenge["epoch_id"] == str(binding.epoch_id) and
                     challenge["generation"] == binding.epoch_generation and
                     self._now(tx) < challenge["expires_at_ms"], "Fresh challenge is required")
            _require(tx.get("approvals", binding.approval_id) is None, "Approval ID cannot be reused")
            assert challenge is not None
            tx.put("challenges", challenge_id, {**challenge, "consumed": True})
            row = {"binding": binding.model_dump(mode="json"), "fingerprint": binding.fingerprint,
                   "actor_id": candidate.actor_id, "challenge_id": challenge_id,
                   "actor_digest": fingerprint(candidate.model_dump(mode="json"))}
            tx.put("approvals", binding.approval_id, row, immutable=True)
            for revision in {0, int(binding.artifact_generation)}:
                scope = Revocation(subject_uuid=binding.subject_uuid, kind="artifact",
                                   target=binding.artifact_sha256, revision=revision)
                tx.put("artifact_bindings", scope.key, scope.model_dump(mode="json"), immutable=True)
            tx.append(str(uuid4()), "APPROVAL_REGISTERED", {
                "approval_id": binding.approval_id, "subject_uuid": str(binding.subject_uuid),
                "binding_digest": binding.fingerprint, "challenge_id": challenge_id,
                "actor_id": candidate.actor_id, "actor_digest": row["actor_digest"],
            })
            return {"status": "REGISTERED", "approval_id": binding.approval_id,
                    "binding_digest": binding.fingerprint}
        return self.store.transact(operation)

    def prepare(self, device: AuthenticatedActor, approval_id: str) -> dict:
        _identifier(approval_id)
        def operation(tx: Transaction) -> dict:
            approval = tx.get("approvals", approval_id)
            _require(approval is not None, "Current independent approval is required")
            assert approval is not None
            binding = Binding.model_validate(approval["binding"])
            self._device(tx, device, binding)
            self._approval(tx, approval, binding)
            self._binding(tx, binding)
            _require(tx.get("claims", binding.opening_claim_key) is None,
                     "Opening already has a possible disclosure")
            row = {"id": str(uuid4()), "binding": binding.model_dump(mode="json"),
                   "fingerprint": binding.fingerprint, "status": "PREPARED",
                   "expires_at_ms": min(binding.deadline_ms, self._now(tx) + PERMIT_LIFETIME_MS)}
            tx.put("permits", row["id"], row, immutable=True)
            tx.append(str(uuid4()), "ACTION_PREPARED", {
                "permit_id": row["id"], "binding_digest": binding.fingerprint,
                "expires_at_ms": row["expires_at_ms"],
            })
            return {"permit_id": row["id"], "status": "PREPARED",
                    "binding_digest": binding.fingerprint, "expires_at_ms": row["expires_at_ms"]}
        return self.store.transact(operation)

    def begin(self, device: AuthenticatedActor, permit_id: str,
              expected_binding: Binding) -> BeginDecision:
        _identifier(permit_id)
        def operation(tx: Transaction) -> BeginDecision:
            permit = tx.get("permits", permit_id)
            _require(permit is not None, "Permit is absent")
            assert permit is not None
            binding = Binding.model_validate(permit["binding"])
            self._device(tx, device, binding)
            _require(expected_binding.fingerprint == permit["fingerprint"],
                     "Exact action binding changed")
            if permit["status"] != "PREPARED":
                status = "ALREADY_BEGUN" if permit["status"] == "BEGUN" else permit["status"]
                return BeginDecision(status=status)
            self._binding(tx, binding)
            _require(self._now(tx) < permit["expires_at_ms"], "Prepared permit expired")
            approval = tx.get("approvals", binding.approval_id)
            _require(approval is not None and approval["fingerprint"] == binding.fingerprint,
                     "Independent approval changed")
            assert approval is not None
            self._approval(tx, approval, binding)
            _require(tx.get("claims", binding.opening_claim_key) is None,
                     "Opening already has a possible disclosure")
            event = tx.append(str(uuid4()), "ACTION_BEGUN", {
                "permit_id": permit_id, "binding_digest": binding.fingerprint,
                "opening_claim_key": binding.opening_claim_key,
                "subject_uuid": str(binding.subject_uuid),
            })
            tx.put("claims", binding.opening_claim_key, {
                "permit_id": permit_id, "binding_digest": binding.fingerprint,
                "begun_sequence": event["sequence"],
            }, immutable=True)
            tx.put("permits", permit_id, {**permit, "status": "BEGUN",
                                          "begun_sequence": event["sequence"]})
            issued_at = self._now(tx)
            if issued_at >= permit["expires_at_ms"]:
                return BeginDecision(status="ALREADY_BEGUN")
            return BeginDecision(status="BEGUN", may_act=MayAct(
                decision_nonce=uuid4(), permit_id=permit_id, binding=binding,
                begun_sequence=event["sequence"],
                issued_at_ms=issued_at,
                expires_at_ms=min(permit["expires_at_ms"], issued_at + DECISION_LIFETIME_MS),
            ))
        # The adapter must not release this result until its commit is confirmed.
        # A lost/ambiguous response is never reconstructed from stored events.
        decision = self.store.transact(operation)
        if decision.may_act is not None:
            reply_now = self.now_ms()
            if (type(reply_now) is not int or
                    not decision.may_act.issued_at_ms <= reply_now < decision.may_act.expires_at_ms):
                # A delayed commit/reply or uncertain clock cannot mint a fresh
                # decision for a consumed begin.
                return BeginDecision(status="ALREADY_BEGUN")
        return decision

    def revoke(self, actor: AuthenticatedActor, scope: Revocation, event_id: str) -> dict:
        _identifier(event_id)
        def operation(tx: Transaction) -> dict:
            self._actor(tx, actor, {"candidate", "operator"})
            _require(actor.role == "operator" or actor.subject_uuid == scope.subject_uuid,
                     "Revocation belongs to another subject")
            if scope.kind == "subject":
                _require(scope.target == str(scope.subject_uuid) and scope.revision == 0,
                         "Subject tombstone must bind the stable UUID")
                _require(tx.get("subjects", scope.target) is not None, "Subject is absent")
            elif scope.kind == "device":
                target = tx.get("devices", scope.target)
                _require(target is not None and target["subject_uuid"] == str(scope.subject_uuid) and
                         scope.revision == 0, "Device revocation scope is invalid")
            elif scope.kind == "grant":
                target = tx.get("grants", scope.target)
                _require(target is not None and target["scope"]["subject_uuid"] == str(scope.subject_uuid) and
                         scope.revision in {0, target["scope"]["revision"]}, "Grant scope is invalid")
            elif scope.kind == "artifact":
                _require(tx.get("artifact_bindings", scope.key) == scope.model_dump(mode="json"),
                         "Artifact is not independently registered")
            else:
                # Targets must be independently registered, never supplied from
                # restored application SQL.
                approval = tx.get("approvals", scope.target)
                _require(approval is not None, "Revocation target is not independently registered")
                assert approval is not None
                target_binding = Binding.model_validate(approval["binding"])
                _require(target_binding.subject_uuid == scope.subject_uuid and
                         scope.revision in {0, target_binding.approval_revision},
                         "Revocation revision is invalid")
            payload = {"actor_id": actor.actor_id, "scope": scope.model_dump(mode="json")}
            event = tx.append(event_id, "REVOKED", payload)
            existing = tx.get("revocations", scope.key)
            tombstone = existing or {"scope": scope.model_dump(mode="json"),
                                     "sequence": event["sequence"]}
            tx.put("revocations", scope.key, tombstone, immutable=True)
            return {"status": "REVOKED", "sequence": event["sequence"]}
        return self.store.transact(operation)

    def outcome(self, device: AuthenticatedActor, permit_id: str,
                status: str) -> BeginDecision:
        """Late local observations are non-authorizing and cannot release claims."""
        _identifier(permit_id)
        _require(status in {"UNKNOWN", "LOCAL_FILLED"}, "No supported local outcome")
        def operation(tx: Transaction) -> BeginDecision:
            permit = tx.get("permits", permit_id)
            _require(permit is not None, "Permit is absent")
            assert permit is not None
            binding = Binding.model_validate(permit["binding"])
            self._device(tx, device, binding, require_active=False)
            _require(permit["status"] in {"BEGUN", status}, "Terminal outcome cannot reopen or change")
            if permit["status"] == "BEGUN":
                tx.put("permits", permit_id, {**permit, "status": status})
                tx.append(str(uuid4()), "LOCAL_OUTCOME", {
                    "permit_id": permit_id, "status": status, "binding_digest": binding.fingerprint,
                })
            return BeginDecision(status=status)
        return self.store.transact(operation)
