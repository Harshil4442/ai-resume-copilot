"""Server-owned immutable account/credential/session facts, never request identities."""
from __future__ import annotations

from uuid import UUID

from ..recovery.contracts import Digest, Timestamp
from ..recovery.pairing_contracts import PairingContract, PositiveInteger
from ..recovery.password_reauth import CandidateWebSession


class CandidateAccountIdentity(PairingContract):
    candidate_id: PositiveInteger
    subject_uuid: UUID
    account_binding_id: UUID
    principal_sha256: Digest


class CandidateCredentialRevision(CandidateAccountIdentity):
    auth_generation: PositiveInteger
    credential_sha256: Digest


class CandidateLoginResult(PairingContract):
    context: CandidateWebSession
    auth_generation: PositiveInteger
    expires_at_ms: Timestamp
