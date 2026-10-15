"""Current password proof connected to exact native and protected pairing consumption."""
from __future__ import annotations

from .contracts import canonical
from .gcp_pairing import GcpPairingCoordinator
from .gcp_password_contracts import PasswordSigningMarker
from .gcp_scoped_authority import ScopedBeginAck, ScopedCandidateAuthority
from .pairing_contracts import CandidateAssertion
from .password_reauth import (
    CandidateAssertionSigner,
    CandidateChallengeProof,
    CandidateDispatchCommand,
    CandidateWebSession,
    PasswordCredentials,
    PasswordReauthService,
    RegisteredPasswordIdentity,
)
from .store import GuardDenied, GuardUnavailable


class GcpPasswordCandidateBoundary:
    """Typed ports tied to one actual native coordinator, without core overrides."""

    def __init__(self, coordinator: GcpPairingCoordinator, *, scoped: ScopedCandidateAuthority | None = None, paired_devices=None):
        if type(coordinator) is not GcpPairingCoordinator:
            raise TypeError("The reviewed native pairing coordinator is required")
        self.coordinator = coordinator
        if scoped is not None and (type(scoped) is not ScopedCandidateAuthority
                or scoped.publication.resource.pin.authority.target != coordinator.pin):
            raise TypeError("Exact V3 scoped authority for the same execution pin required")
        self.scoped, self.paired_devices = scoped, paired_devices
        self._begins: dict[object, tuple[ScopedBeginAck, PasswordSigningMarker]] = {}

    def read(self, context: CandidateWebSession, proof: CandidateChallengeProof) -> tuple[dict, dict, int]:
        context = CandidateWebSession.model_validate_json(canonical(context.model_dump(mode="json")))
        protected = self.scoped.resolve(context) if self.scoped is not None else None
        identity, payload, now = self.coordinator.read_password_candidate(context, proof)
        if protected is not None:
            subject, session = protected
            if (identity.get("subject_uuid") != str(subject.subject_uuid)
                    or identity.get("session_id") != str(session.session_id)
                    or identity.get("principal_sha256") != subject.principal_sha256
                    or identity.get("auth_generation") != subject.auth_generation):
                raise GuardDenied("Native password identity disagrees with protected current scope")
            assert self.scoped is not None
            self.scoped.resolve(context)
        return identity, payload, now

    def admit(self, context: CandidateWebSession, proof: CandidateChallengeProof,
              assertion: CandidateAssertion) -> None:
        def begin(marker: PasswordSigningMarker) -> None:
            assert self.scoped is not None
            ack = self.scoped.begin(context, marker)
            if ack is None:
                raise GuardUnavailable("Protected signing BEGIN outcome is unknown")
            self.scoped.verify_begin(ack, marker)
            if proof.operation == "revoke_device":
                if self.paired_devices is None:
                    raise GuardUnavailable("Protected device denial composition is unavailable")
                _, payload, _ = self.coordinator.read_password_candidate(context, proof)
                self.paired_devices.deny_before(ack, marker, payload)
            self._begins[assertion.assertion_id] = ack, marker
        self.coordinator.admit_password_candidate(context, proof, assertion,
            protected_begin=begin if self.scoped is not None else None)
        if self.scoped is not None:
            ack, marker = self._begins[assertion.assertion_id]
            self.scoped.verify_begin(ack, marker)

    def consume(self, command: CandidateDispatchCommand) -> dict:
        if self.scoped is None:
            return self.coordinator.consume_password_candidate(command)
        owned = self._begins.pop(command.assertion.assertion_id, None)
        if owned is None:
            raise GuardDenied("Original protected signing BEGIN is absent or consumed")
        ack, marker = owned
        self.scoped.verify_begin(ack, marker)
        result = self.coordinator.consume_password_candidate(command)
        self.scoped.verify_begin(ack, marker)
        if command.operation == "revoke_device":
            if self.paired_devices is None:
                raise GuardUnavailable("Protected device revocation composition is unavailable")
            self.paired_devices.revoked(self.coordinator.take_password_owned_ack(command.assertion))
        return result

    def service(self, *, credentials: PasswordCredentials, signer: CandidateAssertionSigner,
                issuer: str) -> PasswordReauthService:
        # No clients, credentials, identity bindings or KMS keys are manufactured.
        return PasswordReauthService(credentials=credentials,
            identity=RegisteredPasswordIdentity(self.coordinator.core.store), pairing=self.coordinator.core,
            signer=signer, issuer=issuer, reader=self, signing_admission=self, dispatch=self)
