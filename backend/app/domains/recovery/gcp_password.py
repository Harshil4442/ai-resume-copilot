"""Explicit trusted composition; account/session provisioning and HTTP remain disabled."""
from __future__ import annotations

from .gcp_pairing import GcpPairingCoordinator
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


class GcpPasswordCandidateBoundary:
    """Typed ports tied to one actual native coordinator, without core overrides."""

    def __init__(self, coordinator: GcpPairingCoordinator):
        if type(coordinator) is not GcpPairingCoordinator:
            raise TypeError("The reviewed native pairing coordinator is required")
        self.coordinator = coordinator

    def read(self, context: CandidateWebSession, proof: CandidateChallengeProof) -> tuple[dict, dict, int]:
        return self.coordinator.read_password_candidate(context, proof)

    def admit(self, context: CandidateWebSession, proof: CandidateChallengeProof,
              assertion: CandidateAssertion) -> None:
        self.coordinator.admit_password_candidate(context, proof, assertion)

    def consume(self, command: CandidateDispatchCommand) -> dict:
        return self.coordinator.consume_password_candidate(command)

    def service(self, *, credentials: PasswordCredentials, signer: CandidateAssertionSigner,
                issuer: str) -> PasswordReauthService:
        # No clients, credentials, identity bindings or KMS keys are manufactured.
        return PasswordReauthService(credentials=credentials,
            identity=RegisteredPasswordIdentity(self.coordinator.core.store), pairing=self.coordinator.core,
            signer=signer, issuer=issuer, reader=self, signing_admission=self, dispatch=self)
