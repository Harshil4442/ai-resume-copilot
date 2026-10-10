"""Independent local replay projection checks; never a production adapter."""
from __future__ import annotations

from typing import Protocol

from app.domains.recovery.contracts import canonical
from app.domains.recovery.sequence_contracts import JS_SAFE_MAX, Manifest, Observation
from app.domains.recovery.store import GuardUnavailable


class ProjectionStore(Protocol):
    def get(self, namespace: str, key: str) -> dict | None: ...
    def keys(self, namespace: str) -> set[str]: ...


def verify_sequences(tx: ProjectionStore, events: list[dict]) -> None:
    try:
        _verify_sequences(tx, events)
    except (ValueError, KeyError, TypeError, IndexError) as exc:
        raise GuardUnavailable("Sequence journal/projection evidence is malformed") from exc


def _verify_sequences(tx: ProjectionStore, events: list[dict]) -> None:
    attempts: dict[str, dict] = {}
    manifests: dict[str, Manifest] = {}
    steps: dict[str, dict] = {}
    permits: dict[str, dict] = {}
    challenges: dict[str, dict] = {}
    approvals: dict[str, dict] = {}
    cancellations: dict[str, dict] = {}
    claims: dict[str, dict] = {}

    def require(condition: bool) -> None:
        if not condition:
            raise GuardUnavailable("Sequence journal/projection evidence is incomplete")

    for event in events:
        kind, p, sequence = event["kind"], event["payload"], event["sequence"]
        if not kind.startswith("SEQUENCE_"):
            continue
        require(type(sequence) is int and 0 < sequence <= JS_SAFE_MAX)
        if kind == "SEQUENCE_SEALED":
            attempt_id = p["attempt_id"]
            stored = tx.get("sequence_attempts", attempt_id)
            require(stored is not None and attempt_id not in attempts)
            assert stored is not None
            m = Manifest.model_validate(stored["manifest"])
            require(str(m.attempt_id) == attempt_id and m.context_digest == p["context_digest"])
            manifests[attempt_id] = m
            attempts[attempt_id] = {"manifest": m.model_dump(mode="json"), "context_digest": m.context_digest,
                "state": "SEALED_DRAFT", "next_index": 0, "in_flight": None}
            for index in range(len(m.steps)):
                steps[f"{attempt_id}:{index}"] = {"step_digest": m.step_digest(index), "status": "UNBEGUN"}
            continue
        if kind == "SEQUENCE_CHALLENGE":
            m = manifests[p["attempt_id"]]
            require(p["context_digest"] == m.context_digest and p["review_digest"] == m.review_digest and
                    p["actor_id"] == m.candidate_actor_id and p["actor_digest"] == m.candidate_actor_digest and
                    p["consumed"] is False and p["id"] not in challenges)
            challenges[p["id"]] = dict(p)
            continue
        if kind == "SEQUENCE_PREPARED":
            permit = p["permit"]
            m = manifests[permit["attempt_id"]]
            require(permit["id"] not in permits and permit["context_digest"] == m.context_digest and
                    permit["step_digest"] == m.step_digest(permit["index"]) and permit["status"] == "PREPARED")
            permits[permit["id"]] = dict(permit)
            continue
        attempt_id = p["request"]["attempt_id"] if kind == "SEQUENCE_OUTCOME" else p["attempt_id"]
        attempt, m = attempts[attempt_id], manifests[attempt_id]
        if kind == "SEQUENCE_APPROVED":
            approval = p["approval"]
            challenge = challenges[approval["challenge_id"]]
            require(attempt["state"] == "SEALED_DRAFT" and challenge["consumed"] is False and
                    challenge["attempt_id"] == attempt_id and p["approval_id"] == m.base.approval_id and
                    p["approval_id"] not in approvals and approval["attempt_id"] == attempt_id and
                    approval["context_digest"] == m.context_digest and approval["fingerprint"] == m.binding().fingerprint and
                    approval["actor_id"] == m.candidate_actor_id and approval["actor_digest"] == m.candidate_actor_digest)
            challenges[approval["challenge_id"]] = {**challenge, "consumed": True}
            approvals[p["approval_id"]] = dict(approval)
            attempt["state"] = "READY"
        elif kind == "SEQUENCE_BEGUN":
            index, key = p["index"], f"{attempt_id}:{p['index']}"
            step, permit = steps[key], permits[p["permit_id"]]
            require(attempt["state"] in {"READY", "ACTIVE"} and attempt["next_index"] == index and
                    attempt["in_flight"] is None and step["status"] == "UNBEGUN" and
                    permit["status"] == "PREPARED" and permit["attempt_id"] == attempt_id and
                    permit["index"] == index and p["context_digest"] == m.context_digest and
                    p["step_digest"] == m.step_digest(index) and p["opening_claim_key"] == m.opening_claim_key)
            if index == 0:
                require(m.opening_claim_key not in claims)
                claims[m.opening_claim_key] = {"protocol_version": 2, "attempt_id": attempt_id,
                    "context_digest": m.context_digest, "first_begun_sequence": sequence}
            else:
                previous = steps[f"{attempt_id}:{index-1}"]
                require(previous["status"] == "LOCAL_FILLED" and previous["continuation_hash"] is not None and
                        previous["ack_consumed_by"] is None and
                        permit["continuation_hash"] == previous["continuation_hash"] and
                        claims[m.opening_claim_key]["attempt_id"] == attempt_id)
                previous["ack_consumed_by"] = index
            steps[key] = {"step_digest": p["step_digest"], "status": "BEGUN", "permit_id": p["permit_id"],
                "begun_sequence": sequence, "decision_hash": p["decision_hash"]}
            permit["status"] = "BEGUN"
            attempt.update(state="ACTIVE", in_flight=index)
        elif kind == "SEQUENCE_OUTCOME":
            request = p["request"]
            o = Observation.model_validate(request["observation"])
            step = steps[f"{attempt_id}:{o.index}"]
            require(step["status"] == "BEGUN" and attempt["next_index"] == o.index and attempt["in_flight"] == o.index and
                    o.context_digest == m.context_digest and o.document_id == m.base.target.document_id and
                    o.value_sha256 == m.steps[o.index].value_sha256 and str(o.permit_id) == step["permit_id"] and
                    o.begun_sequence == step["begun_sequence"])
            if o.status == "UNKNOWN":
                require(p["state"] == ("CANCELLED" if attempt["state"] == "CANCELLED" else "BLOCKED_UNKNOWN") and
                        p["continuation_hash"] is None)
            else:
                require(request["decision_hash"] == step["decision_hash"])
                if attempt["state"] == "CANCELLED":
                    require(p["state"] == "CANCELLED" and p["continuation_hash"] is None)
                else:
                    require(p["state"] in {"ACTIVE", "HANDOFF", "LOCAL_SEQUENCE_FILLED"})
                    if p["state"] == "ACTIVE":
                        require(o.index+1 < len(m.steps) and p["continuation_hash"] is not None)
                    else:
                        require(p["continuation_hash"] is None)
                    if p["state"] == "LOCAL_SEQUENCE_FILLED":
                        require(o.index+1 == len(m.steps))
                attempt.update(next_index=o.index+1, in_flight=None)
            step.update(status=o.status, observation_digest=o.digest,
                        continuation_hash=p["continuation_hash"], ack_consumed_by=None)
            attempt["state"] = p["state"]
        elif kind == "SEQUENCE_CANCELLED":
            require(p["actor_id"] == m.candidate_actor_id)
            cancellations.setdefault(attempt_id, {"sequence": sequence})
            attempt["state"] = "CANCELLED"
        else:
            raise GuardUnavailable("Unknown sequence journal event")

    for namespace, records in (("sequence_attempts", attempts), ("sequence_steps", steps),
        ("sequence_permits", permits), ("sequence_challenges", challenges),
        ("sequence_approvals", approvals), ("sequence_cancellations", cancellations)):
        require(tx.keys(namespace) == set(records))
        for key, expected in records.items():
            actual = tx.get(namespace, key)
            require(actual is not None)
            assert actual is not None
            require(canonical(actual) == canonical(expected))
    for key, expected in claims.items():
        actual = tx.get("claims", key)
        require(actual is not None)
        assert actual is not None
        require(canonical(actual) == canonical(expected))
    for key in tx.keys("claims"):
        claim = tx.get("claims", key)
        assert claim is not None
        if claim.get("protocol_version") == 2:
            require(key in claims)
