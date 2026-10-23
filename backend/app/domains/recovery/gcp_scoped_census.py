"""Closed full native census validation; no read/admission authority or IO."""

from __future__ import annotations

import re
from collections import defaultdict

from .contracts import Binding, fingerprint
from .gcp_contracts import JournalReceipt
from .gcp_partitioned_contracts import (
    GenerationHistory,
    PartitionedRecord,
    ScopedBegin,
    ScopedDenial,
    ScopedJournalIntent,
    ScopedPairedDeviceActivation,
    ScopedPairedDeviceDenial,
    ScopedPairedRevocation,
    ScopedPasswordReset,
    ScopedSession,
    ScopedSubject,
    ScopedSubmitBegin,
    ScopedSubmitDeviceEnrollment,
    ScopedTombstone,
)
from .gcp_password_lifetime_contracts import (
    CreateVerifiedOAuthWebSession,
    CreateVerifiedPasswordWebSession,
    EnrollPasswordAccount,
    LifetimeGrantEvidence,
    verified_session_matches,
)
from .gcp_submit_contracts import PairedSubmitDeviceRegistration, SubmitDeviceRegistration
from .store import GuardUnavailable

SCOPED_NAMESPACES = {"v3_activations", "v3_subjects", "v3_account_owners", "v3_candidate_owners",
    "v3_generation_history", "v3_sessions", "v3_session_tombstones", "v3_account_tombstones",
    "v3_subject_tombstones", "v3_pending_session_denials", "v3_pending_subject_denials", "v3_begins", "v3_reset_completions",
    "v3_submit_devices", "v3_submit_key_owners", "v3_submit_device_tombstones",
    "v3_submit_reservations", "v3_submit_begins", "v3_submit_exports", "v3_pairing_completions", "v3_pairing_revocations",
    "v3_recovery_mailboxes"}


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
        elif isinstance(command, (CreateVerifiedPasswordWebSession, CreateVerifiedOAuthWebSession)):
            # Historical expired/denied sessions remain immutable inventory.
            session = ScopedSession.model_validate(values.get(("v3_sessions", str(command.session_id))))
            subject = subjects.get(str(intent.subject_uuid))
            if (subject is None or session.subject_uuid != subject.subject_uuid
                    or not verified_session_matches(command, authentication_method=session.authentication_method,
                        verified_proof_sha256=session.verified_proof_sha256, principal_sha256=session.principal_sha256)
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
        if isinstance(command, (ScopedPairedDeviceActivation, ScopedPairedDeviceDenial, ScopedPairedRevocation)):
            from .gcp_paired_devices import denial_record, signing_intent
            if subject is None or subject.enrollment_operation != command.subject.enrollment_operation:
                raise GuardUnavailable("Paired device command lost protected account origin")
            operation = "confirm_pairing" if isinstance(command, ScopedPairedDeviceActivation) else "revoke_device"
            signing = signing_intent(command.signing_begin, command.subject, command.session, operation=operation)
            retained = records.get(str(signing.operation_id))
            if retained is None or retained.intent != command.signing_begin:
                raise GuardUnavailable("Paired device census lost exact full protected signing BEGIN")
            if isinstance(command, ScopedPairedDeviceActivation):
                grant, evidence = command.grant, command.evidence
                device = PairedSubmitDeviceRegistration(operation_id=intent.operation_id, grant=grant,
                    pairing_operation_id=evidence.intent.operation_id, signing_operation_id=signing.operation_id,
                    evidence_sha256=evidence.digest)
                if ("v3_submit_devices", str(grant.device_id)) in values:
                    expect("v3_submit_devices", str(grant.device_id), device.model_dump(mode="json"))
                    expect("v3_submit_key_owners", grant.key_sha256, {"device_id":str(grant.device_id),
                        "subject_uuid":str(command.subject.subject_uuid), "registration_operation":key})
                    expect("v3_pairing_completions", str(evidence.intent.operation_id), {
                        "operation_id":key, "evidence_sha256":evidence.digest})
            elif isinstance(command, ScopedPairedDeviceDenial):
                if ("v3_submit_device_tombstones", str(command.device_id)) in values:
                    expect("v3_submit_device_tombstones", str(command.device_id), denial_record(intent))
            else:
                denial = records.get(str(command.denial_operation))
                if denial is None or not isinstance(ScopedJournalIntent.model_validate(denial.intent).command, ScopedPairedDeviceDenial):
                    raise GuardUnavailable("Native revocation census lost its original protected denial")
                if ("v3_pairing_revocations", str(command.evidence.intent.operation_id)) in values:
                    expect("v3_pairing_revocations", str(command.evidence.intent.operation_id), {
                        "operation_id":key, "evidence_sha256":command.evidence.digest})
            continue
        if isinstance(command, (ScopedSubmitDeviceEnrollment, ScopedSubmitBegin)):
            if (subject is None or command.subject.enrollment_operation != subject.enrollment_operation
                    or command.subject.account_binding_id != subject.account_binding_id
                    or command.subject.candidate_id != subject.candidate_id
                    or command.subject.principal_sha256 != subject.principal_sha256):
                raise GuardUnavailable("Submit census lost its exact protected subject enrollment")
            if isinstance(command, ScopedSubmitDeviceEnrollment):
                device_key = "v3_submit_devices", str(command.grant.device_id)
                if device_key in values:
                    device = SubmitDeviceRegistration(operation_id=intent.operation_id,
                        grant=command.grant, grant_pin=command.grant_pin)
                    expect(*device_key, device.model_dump(mode="json"))
                    expect("v3_submit_key_owners", command.grant.key_sha256, {"device_id": str(command.grant.device_id),
                        "subject_uuid": str(command.subject.subject_uuid), "registration_operation": key})
            else:
                device = command.device
                if values.get(("v3_submit_devices", str(device.grant.device_id))) != device.model_dump(mode="json"):
                    raise GuardUnavailable("Submit census lost its original protected device registration")
                expected_begin = {"operation_id": key, "intent_sha256": record.reference.sha256}
                expect("v3_submit_reservations", command.action.opening_claim_key(subject.subject_uuid), expected_begin)
                begin = values.get(("v3_submit_begins", key))
                if begin is not None:
                    receipt = JournalReceipt.model_validate(begin.get("journal"))
                    if receipt.path != record.reference.path or receipt.sha256 != record.reference.sha256:
                        raise GuardUnavailable("Submit census journal receipt lost its exact full original intent")
                    expect("v3_submit_begins", key, {**expected_begin, "journal": receipt.model_dump(mode="json")})
                if ("v3_submit_exports", key) in values:
                    if begin is None:
                        raise GuardUnavailable("Submit census export has no original native BEGIN")
                    expect("v3_submit_exports", key, expected_begin)
            continue
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
    for (namespace, key), raw in values.items():
        if namespace != "v3_recovery_mailboxes":
            continue
        subject = subjects.get(key)
        if (subject is None or ("v3_subjects", key) not in expected
                or type(raw) is not dict or type(raw.get("version")) is not int or raw.get("version") != 1
                or type(raw.get("candidate_id")) is not int
                or type(raw.get("email_sha256")) is not str
                or re.fullmatch(r"[a-f0-9]{64}", raw["email_sha256"]) is None):
            raise GuardUnavailable("Recovery mailbox census lost exact protected enrollment ownership")
        mailbox = {"version": 1, "subject_uuid": str(subject.subject_uuid), "candidate_id": subject.candidate_id,
            "account_binding_id": str(subject.account_binding_id), "principal_sha256": subject.principal_sha256,
            "email_sha256": raw["email_sha256"]}
        if raw != mailbox:
            raise GuardUnavailable("Recovery mailbox census contains an altered or unowned binding")
        expect(namespace, key, mailbox)
    actual = {identity: value for identity, value in values.items() if identity[0] in SCOPED_NAMESPACES}
    if actual != expected:
        raise GuardUnavailable("Full scoped census contains omitted, unowned or mismatched retained authority rows")
    return set(expected)
