"""Disabled, attempt-bound v2 sequence core. No IO, submit, upload or settlement.

Local observations assume an honest authenticated executor. Nonce handoffs fence
ordinary lost replies, not a compromised device. Production remains unavailable.
"""
from __future__ import annotations

from collections.abc import Callable
from uuid import UUID, uuid4

from .contracts import AuthenticatedActor, Revocation
from .sequence_contracts import (
    JS_SAFE_MAX,
    FillStep,
    Manifest,
    Observation,
    OutcomeDecision,
    SequenceBase,
    StepDecision,
    StepMayAct,
    actor_digest,
    nonce_hash,
)
from .service import RecoveryGuard, _identifier, _require
from .store import AuthorityStore, GuardDenied, Transaction


def _event_reference(event: dict) -> int:
    sequence = event["sequence"]
    _require(type(sequence) is int and 0 < sequence <= JS_SAFE_MAX,
             "Event reference is outside the protocol integer range")
    return sequence


def step_key(attempt_id: UUID, index: int) -> str:
    return f"{attempt_id}:{index}"


class SequenceGuard:
    def __init__(self, store: AuthorityStore | None = None, now_ms: Callable[[], int] | None = None):
        self.guard = RecoveryGuard(store, now_ms)
        self.store = self.guard.store

    def _attempt(self, tx: Transaction, attempt_id: UUID) -> tuple[dict, Manifest]:
        row = tx.get("sequence_attempts", str(attempt_id))
        _require(row is not None, "Sealed attempt is absent")
        assert row is not None
        manifest = Manifest.model_validate(row["manifest"])
        _require(str(manifest.attempt_id) == str(attempt_id) and
                 row["context_digest"] == manifest.context_digest, "Sealed attempt changed")
        _require(type(row["next_index"]) is int and 0 <= row["next_index"] <= len(manifest.steps) and
                 (row["in_flight"] is None or (type(row["in_flight"]) is int and
                  0 <= row["in_flight"] < len(manifest.steps))), "Sequence cursor is malformed")
        return row, manifest

    def _candidate(self, tx: Transaction, candidate: AuthenticatedActor, m: Manifest) -> None:
        self.guard._actor(tx, candidate, {"candidate"})
        _require(candidate.subject_uuid == m.base.subject_uuid and
                 candidate.actor_id == m.candidate_actor_id and
                 actor_digest(candidate.model_dump(mode="json")) == m.candidate_actor_digest,
                 "Candidate identity changed")

    def _current(self, tx: Transaction, device: AuthenticatedActor, m: Manifest) -> None:
        binding = m.binding()
        self.guard._device(tx, device, binding)
        self.guard._binding(tx, binding)
        approval = tx.get("sequence_approvals", m.base.approval_id)
        _require(approval is not None and approval["context_digest"] == m.context_digest,
                 "Independent sequence approval is absent")
        assert approval is not None
        self.guard._approval(tx, approval, binding)
        _require(tx.get("sequence_cancellations", str(m.attempt_id)) is None, "Attempt was cancelled")

    def _prefix(self, tx: Transaction, row: dict, m: Manifest, index: int, continuation: UUID | None) -> None:
        _require(type(index) is int and 0 <= index < len(m.steps) and row["next_index"] == index,
                 "Only the exact next step can begin")
        _require(row["state"] in {"READY", "ACTIVE"} and row["in_flight"] is None,
                 "Attempt is stopped or a step remains unresolved")
        for prior_index in range(index):
            step = tx.get("sequence_steps", step_key(m.attempt_id, prior_index))
            _require(step is not None and step["status"] == "LOCAL_FILLED", "Predecessor is unresolved")
        current = tx.get("sequence_steps", step_key(m.attempt_id, index))
        _require(current is not None and current["status"] == "UNBEGUN", "Step cannot replay")
        if index == 0:
            _require(continuation is None, "First step has no predecessor")
        else:
            prior_step = tx.get("sequence_steps", step_key(m.attempt_id, index - 1))
            assert prior_step is not None
            _require(continuation is not None and prior_step["continuation_hash"] == nonce_hash(continuation) and
                     prior_step["ack_consumed_by"] is None, "Fresh predecessor acknowledgement is required")

    def seal(self, candidate: AuthenticatedActor, base: SequenceBase, steps: tuple[FillStep, ...]) -> Manifest:
        def operation(tx: Transaction) -> Manifest:
            self.guard._actor(tx, candidate, {"candidate"})
            _require(candidate.subject_uuid == base.subject_uuid, "Sequence belongs to another subject")
            m = Manifest(protocol_version=2, attempt_id=uuid4(), candidate_actor_id=candidate.actor_id,
                candidate_actor_digest=actor_digest(candidate.model_dump(mode="json")), base=base, steps=steps)
            m = Manifest.model_validate(m.model_dump(mode="json"))
            self.guard._binding(tx, m.binding())
            row = {"manifest": m.model_dump(mode="json"), "context_digest": m.context_digest,
                   "state": "SEALED_DRAFT", "next_index": 0, "in_flight": None}
            tx.put("sequence_attempts", str(m.attempt_id), row, immutable=True)
            for index in range(len(steps)):
                tx.put("sequence_steps", step_key(m.attempt_id, index),
                       {"step_digest": m.step_digest(index), "status": "UNBEGUN"}, immutable=True)
            tx.append(str(uuid4()), "SEQUENCE_SEALED", {"attempt_id": str(m.attempt_id),
                "context_digest": m.context_digest})
            return m
        return self.store.transact(operation)

    def challenge(self, candidate: AuthenticatedActor, attempt_id: UUID, review_digest: str) -> dict:
        def operation(tx: Transaction) -> dict:
            row, m = self._attempt(tx, attempt_id)
            self._candidate(tx, candidate, m)
            self.guard._binding(tx, m.binding())
            _require(row["state"] == "SEALED_DRAFT" and review_digest == m.review_digest,
                     "Exact presealed review is required")
            challenge: dict = {"id": str(uuid4()), "attempt_id": str(attempt_id),
                "context_digest": m.context_digest, "review_digest": review_digest,
                "actor_id": candidate.actor_id, "actor_digest": m.candidate_actor_digest,
                "expires_at_ms": min(m.base.deadline_ms, self.guard._now(tx) + 10_000), "consumed": False}
            tx.put("sequence_challenges", challenge["id"], challenge, immutable=True)
            tx.append(str(uuid4()), "SEQUENCE_CHALLENGE", challenge)
            return challenge
        return self.store.transact(operation)

    def approve(self, candidate: AuthenticatedActor, attempt_id: UUID,
                challenge_id: str, review_digest: str) -> dict:
        _identifier(challenge_id)
        def operation(tx: Transaction) -> dict:
            row, m = self._attempt(tx, attempt_id)
            self._candidate(tx, candidate, m)
            binding = m.binding()
            self.guard._binding(tx, binding)
            challenge = tx.get("sequence_challenges", challenge_id)
            _require(row["state"] == "SEALED_DRAFT" and challenge is not None and
                     not challenge["consumed"] and challenge["attempt_id"] == str(attempt_id) and
                     challenge["context_digest"] == m.context_digest and
                     challenge["review_digest"] == review_digest == m.review_digest and
                     challenge["actor_id"] == candidate.actor_id and
                     challenge["actor_digest"] == m.candidate_actor_digest and
                     self.guard._now(tx) < challenge["expires_at_ms"], "Fresh exact candidate challenge is required")
            _require(tx.get("approvals", m.base.approval_id) is None and
                     tx.get("sequence_approvals", m.base.approval_id) is None, "Approval ID cannot be reused")
            assert challenge is not None
            approval = {"fingerprint": binding.fingerprint, "context_digest": m.context_digest,
                "actor_id": candidate.actor_id, "actor_digest": m.candidate_actor_digest,
                "challenge_id": challenge_id, "attempt_id": str(attempt_id)}
            tx.put("sequence_approvals", m.base.approval_id, approval, immutable=True)
            tx.put("sequence_challenges", challenge_id, {**challenge, "consumed": True})
            tx.put("sequence_attempts", str(attempt_id), {**row, "state": "READY"})
            for revision in {0, int(m.base.artifact.generation)}:
                scope = Revocation(subject_uuid=m.base.subject_uuid, kind="artifact",
                    target=m.base.artifact.sha256, revision=revision)
                tx.put("artifact_bindings", scope.key, scope.model_dump(mode="json"), immutable=True)
            tx.append(str(uuid4()), "SEQUENCE_APPROVED", {"attempt_id": str(attempt_id),
                "approval_id": m.base.approval_id, "approval": approval})
            return {"status": "READY", "attempt_id": str(attempt_id), "context_digest": m.context_digest}
        return self.store.transact(operation)

    def prepare(self, device: AuthenticatedActor, attempt_id: UUID, index: int,
                sequence_digest: str, continuation: UUID | None = None) -> dict:
        def operation(tx: Transaction) -> dict:
            row, m = self._attempt(tx, attempt_id)
            self._current(tx, device, m)
            _require(sequence_digest == m.sequence_digest, "Sealed sequence changed")
            self._prefix(tx, row, m, index, continuation)
            claim = tx.get("claims", m.opening_claim_key)
            _require(claim is None if index == 0 else (claim is not None and
                     claim.get("attempt_id") == str(attempt_id) and
                     claim.get("context_digest") == m.context_digest), "Opening belongs to another attempt")
            permit: dict = {"id": str(uuid4()), "attempt_id": str(attempt_id), "index": index,
                "context_digest": m.context_digest, "step_digest": m.step_digest(index),
                "continuation_hash": nonce_hash(continuation) if continuation else None,
                "expires_at_ms": min(m.base.deadline_ms, self.guard._now(tx) + 10_000), "status": "PREPARED"}
            tx.put("sequence_permits", permit["id"], permit, immutable=True)
            tx.append(str(uuid4()), "SEQUENCE_PREPARED", {"permit": permit})
            return permit
        return self.store.transact(operation)

    def begin(self, device: AuthenticatedActor, permit_id: str, context_digest: str,
              step_digest: str, continuation: UUID | None = None) -> StepDecision:
        _identifier(permit_id)
        def operation(tx: Transaction) -> StepDecision:
            permit = tx.get("sequence_permits", permit_id)
            _require(permit is not None, "Step permit is absent")
            assert permit is not None
            row, m = self._attempt(tx, UUID(permit["attempt_id"]))
            self.guard._device(tx, device, m.binding())
            _require(context_digest == m.context_digest == permit["context_digest"] and
                     step_digest == m.step_digest(permit["index"]) == permit["step_digest"], "Exact step binding changed")
            if permit["status"] != "PREPARED":
                step = tx.get("sequence_steps", step_key(m.attempt_id, permit["index"]))
                assert step is not None
                return StepDecision(status="ALREADY_BEGUN" if step["status"] == "BEGUN" else step["status"])
            self._current(tx, device, m)
            index = permit["index"]
            self._prefix(tx, row, m, index, continuation)
            _require(permit["continuation_hash"] == (nonce_hash(continuation) if continuation else None),
                     "Predecessor acknowledgement changed")
            now = self.guard._now(tx)
            _require(now < permit["expires_at_ms"], "Step permit expired")
            claim = tx.get("claims", m.opening_claim_key)
            _require(claim is None if index == 0 else (claim is not None and
                     claim.get("attempt_id") == str(m.attempt_id) and
                     claim.get("context_digest") == m.context_digest), "Opening belongs to another attempt")
            nonce = uuid4()
            event = tx.append(str(uuid4()), "SEQUENCE_BEGUN", {"attempt_id": str(m.attempt_id),
                "index": index, "permit_id": permit_id, "context_digest": m.context_digest,
                "step_digest": step_digest, "opening_claim_key": m.opening_claim_key,
                "decision_hash": nonce_hash(nonce)})
            _event_reference(event)
            if index == 0:
                tx.put("claims", m.opening_claim_key, {"protocol_version": 2, "attempt_id": str(m.attempt_id),
                    "context_digest": m.context_digest, "first_begun_sequence": event["sequence"]}, immutable=True)
            else:
                key = step_key(m.attempt_id, index - 1)
                prior = tx.get("sequence_steps", key)
                assert prior is not None
                tx.put("sequence_steps", key, {**prior, "ack_consumed_by": index})
            tx.put("sequence_steps", step_key(m.attempt_id, index), {"step_digest": step_digest,
                "status": "BEGUN", "permit_id": permit_id, "begun_sequence": event["sequence"],
                "decision_hash": nonce_hash(nonce)})
            tx.put("sequence_permits", permit_id, {**permit, "status": "BEGUN"})
            tx.put("sequence_attempts", str(m.attempt_id), {**row, "state": "ACTIVE", "in_flight": index})
            issued = self.guard._now(tx)
            if issued >= permit["expires_at_ms"]:
                return StepDecision(status="ALREADY_BEGUN")
            return StepDecision(status="BEGUN", may_act=StepMayAct(protocol_version=2,
                attempt_id=m.attempt_id, permit_id=UUID(permit_id), sequence_digest=m.sequence_digest,
                context_digest=m.context_digest, step_digest=step_digest, index=index,
                decision_nonce=nonce, begun_sequence=event["sequence"], issued_at_ms=issued,
                expires_at_ms=min(permit["expires_at_ms"], issued + 2_000)))
        result = self.store.transact(operation)
        if result.may_act:
            now = self.guard.now_ms()
            if type(now) is not int or not result.may_act.issued_at_ms <= now < result.may_act.expires_at_ms:
                return StepDecision(status="ALREADY_BEGUN")
        return result

    def outcome(self, device: AuthenticatedActor, attempt_id: UUID, observation: Observation,
                event_id: str, decision_nonce: UUID | None = None) -> OutcomeDecision:
        _identifier(event_id)
        def operation(tx: Transaction) -> tuple[OutcomeDecision, int, int]:
            row, m = self._attempt(tx, attempt_id)
            self.guard._device(tx, device, m.binding(), require_active=False)
            index = observation.index
            _require(index < len(m.steps), "Step index is outside the manifest")
            step = tx.get("sequence_steps", step_key(attempt_id, index))
            _require(step is not None and step["status"] != "UNBEGUN" and
                     observation.context_digest == m.context_digest and
                     observation.document_id == m.base.target.document_id and
                     observation.value_sha256 == m.steps[index].value_sha256 and
                     str(observation.permit_id) == step["permit_id"] and
                     observation.begun_sequence == step["begun_sequence"], "Exact begun observation is required")
            assert step is not None
            if observation.status == "LOCAL_FILLED":
                _require(decision_nonce is not None and step["decision_hash"] == nonce_hash(decision_nonce),
                         "Winning begin response is required for a local filled observation")
            payload = {"attempt_id": str(attempt_id), "observation": observation.model_dump(mode="json"),
                       "decision_hash": nonce_hash(decision_nonce) if decision_nonce else None}
            prior = tx.event(event_id)
            if prior:
                _require(prior["kind"] == "SEQUENCE_OUTCOME" and
                         prior["payload"]["request"] == payload, "Outcome event ID conflicts")
            if step["status"] != "BEGUN":
                _require(step["status"] == observation.status, "Terminal outcome cannot change")
                return OutcomeDecision(status=observation.status, next_index=row["next_index"]), 0, m.base.deadline_ms
            _require(row["in_flight"] == index and row["next_index"] == index, "In-flight step changed")
            state = row["state"]
            continuation: UUID | None = None
            if observation.status == "UNKNOWN":
                state = "CANCELLED" if state == "CANCELLED" else "BLOCKED_UNKNOWN"
            elif state == "ACTIVE":
                try:
                    self._current(tx, device, m)
                except GuardDenied:
                    state = "HANDOFF"
                else:
                    if index + 1 == len(m.steps):
                        state = "LOCAL_SEQUENCE_FILLED"
                    else:
                        continuation = uuid4()
            changed = {**step, "status": observation.status, "observation_digest": observation.digest,
                       "continuation_hash": nonce_hash(continuation) if continuation else None,
                       "ack_consumed_by": None}
            next_index = index + 1 if observation.status == "LOCAL_FILLED" else index
            tx.put("sequence_steps", step_key(attempt_id, index), changed)
            tx.put("sequence_attempts", str(attempt_id), {**row, "state": state, "next_index": next_index,
                "in_flight": None if observation.status == "LOCAL_FILLED" else index})
            tx.append(event_id, "SEQUENCE_OUTCOME", {"request": payload, "state": state,
                "continuation_hash": changed["continuation_hash"]})
            clock = tx.get("clock", "observed")
            return (OutcomeDecision(status=observation.status, next_index=next_index,
                                    continuation_nonce=continuation), clock["now_ms"] if clock else 0,
                    m.base.deadline_ms)
        result, issued, deadline = self.store.transact(operation)
        if result.continuation_nonce:
            # The acknowledgement gates progress but never extends approval.
            now = self.guard.now_ms()
            # A continuation is not disclosure authority. Still discard it on
            # a delayed/uncertain clock; the fresh next step remains mandatory.
            if type(now) is not int or not issued <= now < deadline:
                return result.model_copy(update={"continuation_nonce": None})
        return result

    def revoke_approval(self, actor: AuthenticatedActor, attempt_id: UUID, event_id: str) -> dict:
        """A registered sequence approval tombstone; never a new approval."""
        _identifier(event_id)
        def operation(tx: Transaction) -> dict:
            self.guard._actor(tx, actor, {"candidate", "operator"})
            _, m = self._attempt(tx, attempt_id)
            _require(actor.role == "operator" or actor.subject_uuid == m.base.subject_uuid,
                     "Revocation belongs to another subject")
            approval = tx.get("sequence_approvals", m.base.approval_id)
            _require(approval is not None and approval["context_digest"] == m.context_digest,
                     "Sequence approval is not independently registered")
            scope = Revocation(subject_uuid=m.base.subject_uuid, kind="approval",
                target=m.base.approval_id, revision=m.base.approval_revision)
            event = tx.append(event_id, "REVOKED", {"actor_id": actor.actor_id,
                                                     "scope": scope.model_dump(mode="json")})
            _event_reference(event)
            existing = tx.get("revocations", scope.key)
            tx.put("revocations", scope.key, existing or {"scope": scope.model_dump(mode="json"),
                "sequence": event["sequence"]}, immutable=True)
            return {"status": "REVOKED", "sequence": event["sequence"]}
        return self.store.transact(operation)

    def cancel(self, candidate: AuthenticatedActor, attempt_id: UUID, event_id: str) -> dict:
        _identifier(event_id)
        def operation(tx: Transaction) -> dict:
            row, m = self._attempt(tx, attempt_id)
            self._candidate(tx, candidate, m)
            event = tx.append(event_id, "SEQUENCE_CANCELLED", {"attempt_id": str(attempt_id),
                                                               "actor_id": candidate.actor_id})
            _event_reference(event)
            existing = tx.get("sequence_cancellations", str(attempt_id))
            tx.put("sequence_cancellations", str(attempt_id), existing or {"sequence": event["sequence"]}, immutable=True)
            tx.put("sequence_attempts", str(attempt_id), {**row, "state": "CANCELLED"})
            return {"status": "CANCELLED"}
        return self.store.transact(operation)

    def status(self, device: AuthenticatedActor, attempt_id: UUID) -> dict:
        def operation(tx: Transaction) -> dict:
            row, m = self._attempt(tx, attempt_id)
            self.guard._device(tx, device, m.binding(), require_active=False)
            steps = []
            for index in range(len(m.steps)):
                step = tx.get("sequence_steps", step_key(attempt_id, index))
                assert step is not None
                steps.append({key: step[key] for key in ("status", "permit_id", "begun_sequence") if key in step})
            return {"attempt_id": str(attempt_id), "state": row["state"], "next_index": row["next_index"],
                    "in_flight": row["in_flight"], "steps": steps,
                    "issued_at_ms": self.guard._now(tx), "deadline_ms": m.base.deadline_ms}
        return self.store.transact(operation)
