"""Original native pairing ACK to protected device activation; no SQL/claim adoption."""
from __future__ import annotations

from uuid import UUID, uuid4

from .contracts import fingerprint
from .gcp_buffer import BufferedTransaction
from .gcp_paired_device_contracts import PairingOwnedEvidence
from .gcp_pairing import GcpPairingCoordinator, PairingOwnedAck
from .gcp_partitioned_contracts import (
    PartitionedRecord,
    ScopedBegin,
    ScopedJournalIntent,
    ScopedPairedDeviceActivation,
    ScopedPairedDeviceDenial,
    ScopedPairedRevocation,
    ScopedSubject,
)
from .gcp_scoped_authority import ScopedBeginAck, ScopedCandidateAuthority, current_session
from .gcp_submit_contracts import PairedSubmitDeviceRegistration, SubmitContext, SubmitDeviceGrant
from .pairing_contracts import digest
from .store import GuardDenied, GuardUnavailable


def signing_intent(value: dict, subject, session, *, operation: str) -> ScopedJournalIntent:
    intent = ScopedJournalIntent.model_validate(value)
    command = intent.command
    if (not isinstance(command, ScopedBegin) or command.subject != subject or command.session != session
            or not hasattr(command.action, "challenge_id") or command.action.operation != operation):
        raise GuardDenied("Exact original protected candidate signing BEGIN required")
    return intent


def check_signing(tx: BufferedTransaction, value: dict, subject, session, *, operation: str) -> ScopedJournalIntent:
    intent = signing_intent(value, subject, session, operation=operation)
    command = intent.command
    assert isinstance(command, ScopedBegin)
    if (PartitionedRecord.model_validate(tx.get("v3_records", str(intent.operation_id))).intent != value
            or tx.get("v3_begins", f"signing:{command.action.challenge_id}") != {
                "operation_id": str(intent.operation_id), "command_sha256": fingerprint(command.model_dump(mode="json"))}):
        raise GuardUnavailable("Protected device activation lost original candidate signing provenance")
    return intent


def denial_record(intent: ScopedJournalIntent) -> dict:
    command = intent.command
    assert isinstance(command, ScopedPairedDeviceDenial)
    return {"operation_id": str(intent.operation_id), "subject_uuid": str(command.subject.subject_uuid),
            "device_id": str(command.device_id), "reason": "candidate_revoke"}


class ProtectedPairingDevices:
    def __init__(self, coordinator: GcpPairingCoordinator, scoped: ScopedCandidateAuthority, *, activated=None, deny_sql=None):
        if type(coordinator) is not GcpPairingCoordinator or type(scoped) is not ScopedCandidateAuthority:
            raise GuardDenied("Exact connected native pairing/protected components required")
        if coordinator.pin != scoped.publication.resource.pin.authority.target:
            raise GuardDenied("Pairing and protected device authority pins disagree")
        self.coordinator, self.scoped = coordinator, scoped
        self.activated, self.deny_sql = activated, deny_sql

    def _signing(self, evidence: PairingOwnedEvidence, *, challenge_id: str) -> dict:
        scoped = self.scoped
        found = scoped.publication.registry.read("v3_begins", f"signing:{challenge_id}")
        if found is None:
            raise GuardDenied("Device has no protected candidate signing BEGIN")
        full = PartitionedRecord.model_validate(scoped.publication.registry.read("v3_records", found["operation_id"]))
        return full.intent

    def activate(self, ack: PairingOwnedAck) -> None:
        evidence = self.coordinator.consume_owned_ack(ack)
        if evidence.intent.command != "complete_device":
            raise GuardDenied("Original device completion acknowledgement required")
        scoped = self.scoped
        subject, session = scoped.resolve(evidence.context)
        signing = self._signing(evidence, challenge_id=evidence.confirmation["challenge_id"])
        begin = signing_intent(signing, subject, session, operation="confirm_pairing")
        command = begin.command
        assert isinstance(command, ScopedBegin)
        marker = command.action
        if (marker.assertion_sha256 != fingerprint(evidence.confirmation["assertion"])
                or marker.challenge_sha256 != digest("candidate-context", evidence.confirmation["context"])
                or begin.pin != evidence.intent.pin or evidence.intent.epoch_generation != self.coordinator.epoch_generation):
            raise GuardDenied("Exact original candidate approval does not match native completion")
        native = evidence.device
        grant = SubmitDeviceGrant(pin=evidence.intent.pin, epoch_generation=evidence.intent.epoch_generation,
            context=SubmitContext.model_validate(subject.model_dump(exclude={"enrollment_operation"})),
            device_id=native["device_id"], public_key=native["public_key"], key_sha256=native["key_sha256"],
            key_generation=native["key_generation"], executor_revision=native["executor_revision"],
            enrollment_provenance_sha256=evidence.digest)
        now = scoped._now()
        intent = ScopedJournalIntent(operation_id=uuid4(), pin=evidence.intent.pin,
            command=ScopedPairedDeviceActivation(subject=subject, session=session, grant=grant,
                evidence=evidence, signing_begin=signing), created_at_ms=now, deadline_ms=now+60_000)
        device = PairedSubmitDeviceRegistration(operation_id=intent.operation_id, grant=grant,
            pairing_operation_id=evidence.intent.operation_id, signing_operation_id=begin.operation_id,
            evidence_sha256=evidence.digest)
        def activate(tx: BufferedTransaction, fixed: ScopedJournalIntent) -> None:
            if current_session(tx, evidence.context, scoped._now()) != (subject, session):
                raise GuardDenied("Protected pairing owner/session changed")
            check_signing(tx, signing, subject, session, operation="confirm_pairing")
            if any(tx.get(namespace, key) is not None for namespace, key in (
                ("v3_submit_devices", str(grant.device_id)), ("v3_submit_key_owners", grant.key_sha256),
                ("v3_submit_device_tombstones", str(grant.device_id)), ("v3_pairing_completions", str(evidence.intent.operation_id)))):
                raise GuardDenied("Paired device/key/completion cannot be adopted or replayed")
            tx.put("v3_submit_devices", str(grant.device_id), device.model_dump(mode="json"), immutable=True)
            tx.put("v3_submit_key_owners", grant.key_sha256, {"device_id":str(grant.device_id),
                "subject_uuid":str(subject.subject_uuid), "registration_operation":str(intent.operation_id)}, immutable=True)
            tx.put("v3_pairing_completions", str(evidence.intent.operation_id), {"operation_id":str(intent.operation_id),
                "evidence_sha256":evidence.digest}, immutable=True)
        if scoped._execute(intent, activate) is None:
            raise GuardUnavailable("Protected pairing activation outcome unknown; no claim export or adoption")
        scoped.resolve(evidence.context)
        if self.activated is not None:
            self.activated(grant, device, native["extension_id"])

    def deny_before(self, ack: ScopedBeginAck, marker, challenge: dict) -> None:
        scoped = self.scoped
        begin = scoped.verify_begin(ack, marker)
        command = begin.command
        assert isinstance(command, ScopedBegin)
        if marker.operation != "revoke_device" or marker.challenge_sha256 != digest("candidate-context", challenge):
            raise GuardDenied("Exact protected candidate revocation challenge required")
        device_id = UUID(challenge["device_id"])
        now = scoped._now()
        intent = ScopedJournalIntent(operation_id=uuid4(), pin=begin.pin,
            command=ScopedPairedDeviceDenial(subject=command.subject, session=command.session,
                device_id=device_id, signing_begin=begin.model_dump(mode="json"), candidate_challenge=challenge),
            created_at_ms=now, deadline_ms=min(now+60_000, marker.deadline_ms))
        def validate_owner(tx: BufferedTransaction) -> None:
            if current_session(tx, marker.context, scoped._now()) != (command.subject, command.session):
                raise GuardDenied("Protected device denial owner changed")
            raw = tx.get("v3_submit_devices", str(device_id))
            if raw is None or raw.get("grant", {}).get("context", {}).get("subject_uuid") != str(command.subject.subject_uuid):
                raise GuardDenied("Candidate cannot deny another protected device")
        scoped._run(validate_owner)
        if self.deny_sql is not None:
            self.deny_sql(marker.context, device_id)
        def deny(tx: BufferedTransaction, fixed: ScopedJournalIntent) -> None:
            if current_session(tx, marker.context, scoped._now()) != (command.subject, command.session):
                raise GuardDenied("Protected candidate revocation owner changed")
            check_signing(tx, begin.model_dump(mode="json"), command.subject, command.session, operation="revoke_device")
            raw = tx.get("v3_submit_devices", str(device_id))
            if raw is None or raw.get("grant", {}).get("context", {}).get("subject_uuid") != str(command.subject.subject_uuid):
                raise GuardDenied("Candidate cannot deny another protected device")
            if tx.get("v3_submit_device_tombstones", str(device_id)) is not None:
                raise GuardDenied("Protected device revocation is already consumed")
            tx.put("v3_submit_device_tombstones", str(device_id), denial_record(intent), immutable=True)
        if scoped._execute(intent, deny) is None:
            raise GuardUnavailable("Protected device denial outcome unknown; native revocation withheld")

    def revoked(self, ack: PairingOwnedAck) -> None:
        evidence = self.coordinator.consume_owned_ack(ack)
        if evidence.intent.command != "revoke_device" or evidence.intent.password_signing is None:
            raise GuardDenied("Original native revocation acknowledgement required")
        scoped = self.scoped
        subject, session = scoped.resolve(evidence.context)
        signing = self._signing(evidence, challenge_id=str(evidence.intent.password_signing.challenge_id))
        signing_intent(signing, subject, session, operation="revoke_device")
        tombstone = scoped.publication.registry.read("v3_submit_device_tombstones", evidence.device["device_id"])
        if tombstone is None:
            raise GuardUnavailable("Original protected device denial is absent")
        now = scoped._now()
        intent = ScopedJournalIntent(operation_id=uuid4(), pin=evidence.intent.pin,
            command=ScopedPairedRevocation(subject=subject, session=session, evidence=evidence,
                signing_begin=signing, denial_operation=tombstone["operation_id"]),
            created_at_ms=now, deadline_ms=now+60_000)
        def complete(tx: BufferedTransaction, fixed: ScopedJournalIntent) -> None:
            if current_session(tx, evidence.context, scoped._now()) != (subject, session):
                raise GuardDenied("Protected revocation owner changed")
            check_signing(tx, signing, subject, session, operation="revoke_device")
            full = PartitionedRecord.model_validate(tx.get("v3_records", tombstone["operation_id"]))
            denial = ScopedJournalIntent.model_validate(full.intent)
            if tx.get("v3_submit_device_tombstones", evidence.device["device_id"]) != denial_record(denial):
                raise GuardUnavailable("Protected revocation lost its original device denial")
            key = str(evidence.intent.operation_id)
            if tx.get("v3_pairing_revocations", key) is not None:
                raise GuardDenied("Original native revocation is already consumed")
            tx.put("v3_pairing_revocations", key, {"operation_id":str(intent.operation_id),
                "evidence_sha256":evidence.digest}, immutable=True)
        if scoped._execute(intent, complete) is None:
            raise GuardUnavailable("Protected native revocation outcome unknown; status only")

    def _current_device(self, tx: BufferedTransaction, device_id: UUID):
        from .gcp_scoped_authority import current_subject
        scoped = self.scoped
        scoped.publication._root(tx, opened=True)
        device = PairedSubmitDeviceRegistration.model_validate(tx.get("v3_submit_devices", str(device_id)))
        grant = device.grant
        subject = current_subject(tx, grant.context.subject_uuid)
        full = PartitionedRecord.model_validate(tx.get("v3_records", str(device.operation_id)))
        activation = ScopedJournalIntent.model_validate(full.intent).command
        if (not isinstance(activation, ScopedPairedDeviceActivation) or activation.grant != grant
                or activation.evidence.digest != device.evidence_sha256
                or activation.evidence.intent.operation_id != device.pairing_operation_id
                or tx.get("v3_pairing_completions", str(device.pairing_operation_id)) != {
                    "operation_id":str(device.operation_id), "evidence_sha256":device.evidence_sha256}
                or tx.get("v3_submit_key_owners", grant.key_sha256) != {
                    "device_id":str(grant.device_id),"subject_uuid":str(subject.subject_uuid),
                    "registration_operation":str(device.operation_id)}):
            raise GuardUnavailable("Native device lost full protected paired activation provenance")
        begin = check_signing(tx, activation.signing_begin, activation.subject, activation.session, operation="confirm_pairing")
        if (device.signing_operation_id != begin.operation_id
                or grant.context.model_dump(mode="json") != subject.model_dump(mode="json",exclude={"enrollment_operation"})
                or grant.pin != self.coordinator.pin or grant.epoch_generation != self.coordinator.epoch_generation
                or grant.device_id != device_id or tx.get("v3_submit_device_tombstones", str(device_id)) is not None):
            raise GuardDenied("Exact current protected paired device/credential revision required")
        return subject, device, activation

    def assistance_context(self, device_id: UUID, public_key: dict, *, extension_id: str,
                           executor_revision: str) -> SubmitContext:
        """Fresh independent owner/device gate; no SQL row, claim or session adoption."""
        from .pairing_auth import key_fingerprint, public_jwk
        device_id = UUID(str(device_id))
        key = public_jwk(public_key)
        captured: list[tuple[ScopedSubject, PairedSubmitDeviceRegistration, ScopedPairedDeviceActivation]] = []
        def protected(tx: BufferedTransaction) -> None:
            subject, device, activation = self._current_device(tx, device_id)
            grant = device.grant
            if (grant.public_key != key or grant.key_sha256 != key_fingerprint(key)
                    or grant.executor_revision != executor_revision
                    or activation.evidence.device["extension_id"] != extension_id):
                raise GuardDenied("Assistance key/executor differs from the original protected pairing")
            value = subject, device, activation
            if captured and captured[0] != value:
                raise GuardDenied("Protected assistance ownership changed during native checks")
            captured[:] = [value]
        self.scoped._run(protected)
        subject, device, activation = captured[0]
        def execution(tx: BufferedTransaction) -> None:
            c = self.coordinator
            c._control(tx)
            c.core._extension(tx, extension_id, executor_revision)
            native = tx.get("devices", str(device_id))
            if (native != activation.evidence.device or native is None or native.get("active") is not True
                    or tx.get("pairing_key_owners", device.grant.key_sha256) != {
                        "device_id":str(device_id),"subject_uuid":str(subject.subject_uuid)}):
                raise GuardDenied("Current execution device/key provenance is missing or restored")
            c.core._device_denied(tx, str(subject.subject_uuid), str(device_id))
        c = self.coordinator
        c.registry.run(execution, before_attempt=lambda:c.fence.check(c.pin))
        c.fence.check(c.pin)
        self.scoped._run(protected)
        return SubmitContext.model_validate(captured[0][0].model_dump(exclude={"enrollment_operation"}))

    def check_claim(self, claim: dict) -> None:
        def check(tx: BufferedTransaction) -> None:
            subject, device, _ = self._current_device(tx, UUID(claim["device_id"]))
            grant, pin = device.grant, self.coordinator.pin
            if (claim.get("subject_uuid") != str(subject.subject_uuid)
                    or claim.get("authority_id") != str(pin.authority_id) or claim.get("authority_incarnation") != pin.incarnation
                    or claim.get("epoch_id") != str(pin.epoch_id) or claim.get("epoch_generation") != self.coordinator.epoch_generation
                    or claim.get("auth_generation") != subject.auth_generation
                    or not claim.get("issued_at_ms", 0) <= self.scoped._now() < claim.get("expires_at_ms", 0)
                    or grant.key_sha256 != claim["key_sha256"] or grant.key_generation != claim["key_generation"]
                    or grant.executor_revision != claim["executor_revision"]):
                raise GuardDenied("Native claim epoch/generation, key or original lifetime changed")
        self.scoped._run(check)
