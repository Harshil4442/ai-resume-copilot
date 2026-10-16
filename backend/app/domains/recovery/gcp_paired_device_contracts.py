"""Full native pairing provenance for protected normal-user device activation."""
from __future__ import annotations

from pydantic import model_validator

from .contracts import Contract, fingerprint
from .gcp_contracts import JournalReceipt
from .gcp_pairing_contracts import PairingInvocationClaim, PairingJournalIntent
from .gcp_password_contracts import PasswordSigningClaim
from .pairing_contracts import CandidateAssertion, digest
from .password_reauth import CandidateWebSession


class PairingOwnedEvidence(Contract):
    intent: PairingJournalIntent
    journal: JournalReceipt
    invocation: PairingInvocationClaim
    projection: dict
    device: dict
    confirmation: dict
    context: CandidateWebSession
    signing: PasswordSigningClaim | None = None

    @model_validator(mode="after")
    def exact(self):
        from .pairing_auth import key_fingerprint, public_jwk
        i, d, c = self.intent, self.device, self.confirmation
        assertion = CandidateAssertion.model_validate(c.get("assertion"))
        event = self.projection.get("event", {})
        changes = self.projection.get("changes")
        kind = "PAIRING_COMPLETED" if i.command == "complete_device" else "PAIRING_DEVICE_REVOKED"
        if (i.command not in {"complete_device", "revoke_device"}
                or not self.invocation.matches(i, self.journal)
                or set(self.projection) != {"changes", "event"} or type(changes) is not list
                or event.get("kind") != kind or event.get("digest") != fingerprint({k:v for k,v in event.items() if k != "digest"})
                or event.get("payload", {}).get("device_id") != d.get("device_id")
                or set(d) != {"device_id", "subject_uuid", "key_sha256", "public_key", "key_generation", "auth_generation", "extension_id", "executor_revision", "pairing_id", "confirmation_sha256", "active"}
                or d["active"] is not (i.command == "complete_device") or d["key_generation"] != 1
                or key_fingerprint(public_jwk(d["public_key"])) != d["key_sha256"]
                or str(assertion.subject_uuid) != d["subject_uuid"] or assertion.auth_generation != d["auth_generation"]
                or assertion.operation != "confirm_pairing" or (i.command == "complete_device" and assertion.session_id != self.context.session_id)
                or c.get("context", {}).get("pairing_id") != d["pairing_id"]
                or c["context"].get("device_id") != d["device_id"] or c["context"].get("key_sha256") != d["key_sha256"]
                or digest("confirmation", c) != d["confirmation_sha256"]):
            raise ValueError("Exact full committed pairing device/owner provenance required")
        if i.command == "revoke_device":
            signing = self.signing
            if (i.password_signing is None or signing is None or signing.digest != i.password_signing.claim_sha256
                    or signing.marker.challenge_id != i.password_signing.challenge_id
                    or signing.marker.context != self.context or signing.marker.operation != "revoke_device"
                    or signing.marker.subject_uuid != assertion.subject_uuid or signing.marker.principal_sha256 != assertion.principal_sha256):
                raise ValueError("Revocation requires its full exact original native signing evidence")
        elif self.signing is not None:
            raise ValueError("Completion cannot substitute revocation signing evidence")
        if i.command == "complete_device" and not any(item == {"namespace":"devices", "key":d["device_id"], "value":d} for item in changes):
            raise ValueError("Completion must retain its exact native device after-image")
        return self

    @property
    def digest(self) -> str:
        return fingerprint(self.model_dump(mode="json"))
