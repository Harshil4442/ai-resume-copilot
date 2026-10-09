"""Closed full native census validation; no read/admission authority or IO."""

from __future__ import annotations

from collections import defaultdict

from .contracts import Binding, fingerprint
from .gcp_partitioned_contracts import (
    GenerationHistory,
    PartitionedRecord,
    ScopedBegin,
    ScopedDenial,
    ScopedJournalIntent,
    ScopedPasswordReset,
    ScopedSession,
    ScopedSubject,
    ScopedTombstone,
)
from .gcp_password_lifetime_contracts import (
    CreateVerifiedPasswordWebSession,
    EnrollPasswordAccount,
    LifetimeGrantEvidence,
)
from .store import GuardUnavailable

SCOPED_NAMESPACES = {"v3_activations", "v3_subjects", "v3_account_owners", "v3_candidate_owners",
    "v3_generation_history", "v3_sessions", "v3_session_tombstones", "v3_account_tombstones",
    "v3_subject_tombstones", "v3_pending_session_denials", "v3_pending_subject_denials", "v3_begins", "v3_reset_completions"}


def validate_scoped_rows(values: dict[tuple[str, str], dict]) -> set[tuple[str, str]]:
    expected: dict[tuple[str, str], dict] = {}
    subjects: dict[str, ScopedSubject] = {}
    histories: dict[str, dict[int, GenerationHistory]] = defaultdict(dict)
    records = {key: PartitionedRecord.model_validate(raw) for (namespace, key), raw in values.items()
               if namespace == "v3_records"}
    def expect(namespace: str, key: str, value: dict) -> None:
        identity = namespace, key
        if identity in expected and expected[identity] != value:
            raise GuardUnavailable("Full scoped census contains conflicting ownership or provenance")
        expected[identity] = value
    for (namespace, key), raw in values.items():
        if namespace == "v3_subjects":
            subject = ScopedSubject.model_validate(raw)
            if key != str(subject.subject_uuid):
                raise GuardUnavailable("Scoped subject census key changed")
            subjects[key] = subject
        elif namespace == "v3_generation_history":
            history = GenerationHistory.model_validate(raw)
            if key != f"{history.subject_uuid}:{history.auth_generation}":
                raise GuardUnavailable("Scoped generation census key changed")
            histories[str(history.subject_uuid)][history.auth_generation] = history
    for (namespace, key), raw in values.items():
        if namespace != "v3_activations":
            continue
        evidence = LifetimeGrantEvidence.model_validate(raw)
        intent, command = evidence.intent, evidence.intent.command
        record = records.get(key)
        if key != str(intent.operation_id) or record is None or record.intent != intent.model_dump(mode="json"):
            raise GuardUnavailable("Full scoped census activation lacks its exact retained canonical intent")
        expect(namespace, key, evidence.model_dump(mode="json"))
        if isinstance(command, EnrollPasswordAccount):
            subject = subjects.get(str(command.subject_uuid))
            if (subject is None or subject.enrollment_operation != intent.operation_id
                    or subject.account_binding_id != command.account_binding_id or subject.candidate_id != command.candidate_id
                    or subject.principal_sha256 != command.principal_sha256):
                raise GuardUnavailable("Scoped subject census lost genuine enrollment ownership")
            expect("v3_subjects", str(subject.subject_uuid), subject.model_dump(mode="json"))
            owner = {"subject_uuid": str(subject.subject_uuid), "account_binding_id": str(subject.account_binding_id),
                "candidate_id": subject.candidate_id, "enrollment_operation": key}
            expect("v3_account_owners", str(subject.account_binding_id), owner)
            expect("v3_candidate_owners", str(subject.candidate_id), owner)
            initial = GenerationHistory(subject_uuid=subject.subject_uuid, previous_generation=0, auth_generation=1,
                operation_id=intent.operation_id, credential_sha256=command.credential_sha256)
            expect("v3_generation_history", f"{subject.subject_uuid}:1", initial.model_dump(mode="json"))
        elif isinstance(command, CreateVerifiedPasswordWebSession):
            # Historical expired/denied sessions remain immutable inventory.
            session = ScopedSession.model_validate(values.get(("v3_sessions", str(command.session_id))))
            subject = subjects.get(str(intent.subject_uuid))
            if (subject is None or session.subject_uuid != subject.subject_uuid
                    or session.principal_sha256 != subject.principal_sha256 or session.activation_operation != intent.operation_id
                    or session.account_binding_id != command.account_binding_id or session.account_binding_id != subject.account_binding_id
                    or session.auth_generation != command.expected_auth_generation
                    or session.credential_sha256 != command.credential_sha256 or session.expires_at_ms != command.expires_at_ms):
                raise GuardUnavailable("Full scoped session census lost its original credential ACK")
            expect("v3_sessions", str(session.session_id), session.model_dump(mode="json"))
    for key, record in records.items():
        if record.reference.kind != "scoped_admission":
            continue
        intent = ScopedJournalIntent.model_validate(record.intent)
        command = intent.command
        subject = subjects.get(str(command.subject.subject_uuid))
        if (subject is None or command.subject.enrollment_operation != subject.enrollment_operation
                or command.subject.account_binding_id != subject.account_binding_id
                or command.subject.candidate_id != subject.candidate_id
                or command.subject.principal_sha256 != subject.principal_sha256
                or values.get(("v3_sessions", str(command.session.session_id))) != command.session.model_dump(mode="json")):
            raise GuardUnavailable("Full scoped command census lost exact owned subject/session provenance")
        if isinstance(command, ScopedBegin):
            action = command.action
            begin_key = f"action:{action.opening_claim_key}" if isinstance(action, Binding) else f"signing:{action.challenge_id}"
            expect("v3_begins", begin_key, {"operation_id": key, "command_sha256": fingerprint(command.model_dump(mode="json"))})
            continue
        assert isinstance(command, (ScopedDenial, ScopedPasswordReset))
        pending_namespace = "v3_pending_session_denials" if command.scope == "session" else "v3_pending_subject_denials"
        pending_key = str(command.session.session_id) if command.scope == "session" else f"{command.subject.subject_uuid}:{command.subject.auth_generation}"
        expect(pending_namespace, pending_key, {"operation_id": key, "intent_sha256": record.reference.sha256})
        if command.scope == "generation":
            history_key = f"{command.subject.subject_uuid}:{command.subject.auth_generation + 1}"
            if ("v3_generation_history", history_key) in values:
                assert command.next_credential_sha256 is not None
                history = GenerationHistory(subject_uuid=command.subject.subject_uuid,
                    previous_generation=command.subject.auth_generation, auth_generation=command.subject.auth_generation + 1,
                    operation_id=intent.operation_id, credential_sha256=command.next_credential_sha256)
                expect("v3_generation_history", history_key, history.model_dump(mode="json"))
                if isinstance(command, ScopedPasswordReset) and ("v3_reset_completions", key) in values:
                    from .gcp_password_reset import ResetProjectionEvidence
                    completion = ResetProjectionEvidence.model_validate(values["v3_reset_completions", key])
                    if completion.intent != intent:
                        raise GuardUnavailable("Reset completion census lost exact original full native request")
                    expect("v3_reset_completions", key, completion.model_dump(mode="json"))
        else:
            tombstone = ScopedTombstone(subject_uuid=command.subject.subject_uuid,
                account_binding_id=command.subject.account_binding_id,
                session_id=command.session.session_id if command.scope == "session" else None, operation_id=intent.operation_id)
            namespace = "v3_session_tombstones" if command.scope == "session" else "v3_subject_tombstones"
            tombstone_key = str(command.session.session_id) if command.scope == "session" else str(command.subject.subject_uuid)
            if (namespace, tombstone_key) in values:
                expect(namespace, tombstone_key, tombstone.model_dump(mode="json"))
                if command.scope == "subject":
                    expect("v3_account_tombstones", str(command.subject.account_binding_id), tombstone.model_dump(mode="json"))
    for key, subject in subjects.items():
        chain = histories[key]
        if (sorted(chain) != list(range(1, subject.auth_generation + 1))
                or not chain or chain[subject.auth_generation].credential_sha256 != subject.credential_sha256):
            raise GuardUnavailable("Full scoped generation history detects rollback, missing revision or gap")
    actual = {identity: value for identity, value in values.items() if identity[0] in SCOPED_NAMESPACES}
    if actual != expected:
        raise GuardUnavailable("Full scoped census contains omitted, unowned or mismatched retained authority rows")
    return set(expected)
