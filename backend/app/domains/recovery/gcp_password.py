"""Explicit trusted composition; account/session provisioning and HTTP remain disabled."""
from __future__ import annotations

from .contracts import canonical
from .gcp_pairing import GcpPairingCoordinator
from .gcp_scoped_authority import ScopedCandidateAuthority
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

    def __init__(self, coordinator: GcpPairingCoordinator, *, scoped: ScopedCandidateAuthority | None = None):
        if type(coordinator) is not GcpPairingCoordinator:
            raise TypeError("The reviewed native pairing coordinator is required")
        self.coordinator = coordinator
        if scoped is not None and (type(scoped) is not ScopedCandidateAuthority
                or scoped.publication.resource.pin.authority.target != coordinator.pin):
            raise TypeError("Exact V3 scoped authority for the same execution pin required")
        self.scoped = scoped

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
        if self.scoped is not None:
            self.scoped.resolve(context)
            raise GuardUnavailable("V3 signing challenge/approval guards require one protected consuming transaction")
        self.coordinator.admit_password_candidate(context, proof, assertion)

    def consume(self, command: CandidateDispatchCommand) -> dict:
        if self.scoped is not None:
            self.scoped.resolve(command.context)
            raise GuardUnavailable("V3 native action guards/transport are not integrated")
        return self.coordinator.consume_password_candidate(command)

    def service(self, *, credentials: PasswordCredentials, signer: CandidateAssertionSigner,
                issuer: str) -> PasswordReauthService:
        # No clients, credentials, identity bindings or KMS keys are manufactured.
        return PasswordReauthService(credentials=credentials,
            identity=RegisteredPasswordIdentity(self.coordinator.core.store), pairing=self.coordinator.core,
            signer=signer, issuer=issuer, reader=self, signing_admission=self, dispatch=self)
