"""Exact digest additions to the existing closed scoped authority census."""
from __future__ import annotations

from collections import defaultdict

from .contracts import fingerprint
from .gcp_notification_digest import (
    BEGINS,
    HEADS,
    HISTORY,
    OUTCOMES,
    begin_key,
    consent_after,
    iso_week,
)
from .gcp_partitioned_contracts import (
    DigestConsentHead,
    DigestSendOutcome,
    PartitionedRecord,
    ScopedDigestConsentChange,
    ScopedDigestEmailDisable,
    ScopedDigestSendBegin,
    ScopedDigestSendOutcome,
    ScopedJournalIntent,
    ScopedSubject,
)
from .store import GuardUnavailable

DIGEST_NAMESPACES = {HEADS, HISTORY, BEGINS, OUTCOMES}
DIGEST_COMMANDS = (ScopedDigestConsentChange, ScopedDigestEmailDisable, ScopedDigestSendBegin, ScopedDigestSendOutcome)


def validate_digest_rows(values: dict[tuple[str, str], dict], records: dict[str, PartitionedRecord],
                         subjects: dict[str, ScopedSubject]) -> dict[tuple[str, str], dict]:
    expected: dict[tuple[str, str], dict] = {}
    chains: dict[str, dict[int, tuple[DigestConsentHead, DigestConsentHead | None]]] = defaultdict(dict)

    def expect(namespace: str, key: str, value: dict) -> None:
        if (namespace, key) in expected:
            raise GuardUnavailable("Digest census contains duplicate authority ownership")
        expected[namespace, key] = value

    intents = {key: ScopedJournalIntent.model_validate(record.intent)
        for key, record in records.items() if record.reference.kind == "scoped_admission"}
    for key, intent in intents.items():
        command = intent.command
        if not isinstance(command, DIGEST_COMMANDS):
            continue
        subject = subjects.get(str(command.subject.subject_uuid))
        # Historical credential revisions must retain their own enrollment and
        # exact generation provenance; they are never current send authority.
        if (subject is None or command.subject.model_dump(exclude={"auth_generation", "credential_sha256"})
                != subject.model_dump(exclude={"auth_generation", "credential_sha256"})):
            raise GuardUnavailable("Digest census lost protected original enrollment")
        history = values.get(("v3_generation_history", f"{subject.subject_uuid}:{command.subject.auth_generation}"))
        if history is None or history.get("credential_sha256") != command.subject.credential_sha256:
            raise GuardUnavailable("Digest census lost exact historical credential provenance")
        mailbox = {"version": 1, "subject_uuid": str(subject.subject_uuid),
            "candidate_id": subject.candidate_id, "account_binding_id": str(subject.account_binding_id),
            "principal_sha256": subject.principal_sha256}
        if isinstance(command, (ScopedDigestConsentChange, ScopedDigestEmailDisable)):
            head = consent_after(intent)
            email_sha256 = head.email_sha256
            if isinstance(command, ScopedDigestConsentChange):
                if values.get(("v3_sessions", str(command.session.session_id))) != command.session.model_dump(mode="json"):
                    raise GuardUnavailable("Digest consent census lost its exact original current session")
            owner = str(subject.subject_uuid)
            if head.generation in chains[owner]:
                raise GuardUnavailable("Digest consent history contains a generation fork")
            chains[owner][head.generation] = head, command.previous
            expect(HISTORY, f"{owner}:{head.generation}", head.model_dump(mode="json"))
        elif isinstance(command, ScopedDigestSendBegin):
            email_sha256 = command.consent.email_sha256
            retained = values.get((HISTORY, f"{subject.subject_uuid}:{command.consent.generation}"))
            if (retained != command.consent.model_dump(mode="json")
                    or command.iso_week != iso_week(intent.created_at_ms)):
                raise GuardUnavailable("Digest BEGIN census lost its exact consent or server week")
            if command.ordinal > 1:
                prior_outcome = command.previous_outcome
                assert prior_outcome is not None
                prior = intents.get(str(prior_outcome.operation_id))
                if prior is None or not isinstance(prior.command, ScopedDigestSendBegin):
                    raise GuardUnavailable("Digest retry census lost its original previous BEGIN")
                if (prior.command.ordinal != command.ordinal - 1
                        or prior.command.model_dump(exclude={"ordinal", "previous_outcome"})
                            != command.model_dump(exclude={"ordinal", "previous_outcome"})
                        or values.get((OUTCOMES, str(prior.operation_id))) != prior_outcome.model_dump(mode="json")
                        or prior_outcome.recorded_at_ms > intent.created_at_ms):
                    raise GuardUnavailable("Digest retry census lost its exact known 429 outcome")
            expect(BEGINS, begin_key(command), {"operation_id": key, "intent_sha256": fingerprint(intent.model_dump(mode="json"))})
        else:
            assert isinstance(command, ScopedDigestSendOutcome)
            original = intents.get(str(command.result.operation_id))
            if original is None or original.model_dump(mode="json") != command.begin_intent:
                raise GuardUnavailable("Digest outcome census lost its full exact original BEGIN")
            assert isinstance(original.command, ScopedDigestSendBegin)
            email_sha256 = original.command.consent.email_sha256
            DigestSendOutcome.model_validate(command.result)
            expect(OUTCOMES, str(original.operation_id), command.result.model_dump(mode="json"))
        if values.get(("v3_recovery_mailboxes", str(subject.subject_uuid))) != {**mailbox, "email_sha256": email_sha256}:
            raise GuardUnavailable("Digest census lost its exact immutable recipient binding")
    for owner, chain in chains.items():
        previous = None
        for index, generation in enumerate(sorted(chain), 1):
            if generation != index:
                raise GuardUnavailable("Digest consent census contains a missing generation")
            head, declared = chain[generation]
            if declared != previous:
                raise GuardUnavailable("Digest consent census lost its exact predecessor")
            previous = head
        assert previous is not None
        expect(HEADS, owner, previous.model_dump(mode="json"))
    actual = {identity: value for identity, value in values.items() if identity[0] in DIGEST_NAMESPACES}
    if actual != expected:
        raise GuardUnavailable("Digest census contains missing, rolled-back or unowned authority rows")
    return expected
