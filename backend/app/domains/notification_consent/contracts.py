"""Pure F07 consent drafts, deliberately incapable of authorizing a send.

Every input is an ordinary caller-supplied value, not a native receipt. Even a
consistent candidate returns runtime_authority=UNAVAILABLE. Future integration
must independently perform fresh protected transactions and provider admission.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass, field
from datetime import UTC, date, datetime
from typing import Literal
from uuid import UUID

MAX_GENERATION = 2**53 - 1
MAX_SEND_WINDOW_MS = 24 * 60 * 60 * 1000
MESSAGE_ATTEMPT_UNITS = 3
RuntimeAuthority = Literal["UNAVAILABLE"]
DraftOperation = Literal["notification_preferences_change", "notification_digest_send_begin"]


def _positive(value: int, name: str) -> None:
    if type(value) is not int or not 0 < value <= MAX_GENERATION:
        raise ValueError(name)


def _digest(value: object, name: str) -> None:
    if type(value) is not str or re.fullmatch(r"[a-f0-9]{64}", value) is None:
        raise ValueError(name)


def _boolean(value: bool, name: str) -> None:
    if type(value) is not bool:
        raise ValueError(name)


@dataclass(frozen=True, slots=True)
class AccountBinding:
    candidate_id: int
    subject_uuid: str
    account_binding_id: str
    principal_sha256: str

    def __post_init__(self) -> None:
        _positive(self.candidate_id, "candidate_id")
        _digest(self.principal_sha256, "principal_sha256")
        for value in (self.subject_uuid, self.account_binding_id):
            if type(value) is not str or str(UUID(value)) != value or UUID(value).version != 4:
                raise ValueError("account_uuid")


@dataclass(frozen=True, slots=True)
class CredentialRevision:
    binding: AccountBinding
    auth_generation: int
    credential_sha256: str

    def __post_init__(self) -> None:
        if type(self.binding) is not AccountBinding:
            raise ValueError("binding")
        _positive(self.auth_generation, "auth_generation")
        _digest(self.credential_sha256, "credential_sha256")


@dataclass(frozen=True, slots=True)
class AccountFacts:
    """Untrusted synthetic observations; true booleans are never permissions."""

    native_revision: CredentialRevision
    sql_revision: CredentialRevision
    current_email_sha256: str
    native_mailbox_sha256: str
    closure_open: bool
    subject_active: bool
    pending_denial: bool
    session_current: bool = False

    def __post_init__(self) -> None:
        if type(self.native_revision) is not CredentialRevision or type(self.sql_revision) is not CredentialRevision:
            raise ValueError("revision")
        _digest(self.current_email_sha256, "current_email_sha256")
        _digest(self.native_mailbox_sha256, "native_mailbox_sha256")
        for name in ("closure_open", "subject_active", "pending_denial", "session_current"):
            _boolean(getattr(self, name), name)


def _account_refusal(facts: AccountFacts | None, *, session: bool) -> str | None:
    if type(facts) is not AccountFacts:
        return "account_facts_missing"
    if not facts.closure_open or not facts.subject_active or facts.pending_denial:
        return "account_closed_or_denied"
    if facts.native_revision != facts.sql_revision:
        return "credential_projection_mismatch"
    if facts.current_email_sha256 != facts.native_mailbox_sha256:
        return "mailbox_mismatch"
    if session and not facts.session_current:
        return "current_session_required"
    return None


@dataclass(frozen=True, slots=True)
class ConsentHead:
    binding: AccountBinding
    generation: int
    recipient_sha256: str
    weekly_digest: bool
    policy_version: str
    previous_sha256: str | None

    def __post_init__(self) -> None:
        if type(self.binding) is not AccountBinding:
            raise ValueError("binding")
        _positive(self.generation, "generation")
        _digest(self.recipient_sha256, "recipient_sha256")
        _boolean(self.weekly_digest, "weekly_digest")
        if type(self.policy_version) is not str or re.fullmatch(r"[a-zA-Z0-9._:-]{1,80}", self.policy_version) is None:
            raise ValueError("policy_version")
        if self.generation == 1:
            if self.previous_sha256 is not None:
                raise ValueError("initial_consent_history")
        else:
            _digest(self.previous_sha256, "previous_sha256")

    @property
    def sha256(self) -> str:
        raw = json.dumps(asdict(self), sort_keys=True, separators=(",", ":")).encode()
        return hashlib.sha256(b"hirewiz:notification-consent-draft:v1\0" + raw).hexdigest()


@dataclass(frozen=True, slots=True)
class ConsentChange:
    binding: AccountBinding
    expected_generation: int
    recipient_sha256: str
    weekly_digest: bool
    policy_version: str
    explicit_confirmation: bool

    def __post_init__(self) -> None:
        if type(self.binding) is not AccountBinding:
            raise ValueError("binding")
        if type(self.expected_generation) is not int or not 0 <= self.expected_generation < MAX_GENERATION:
            raise ValueError("expected_generation")
        _digest(self.recipient_sha256, "recipient_sha256")
        _boolean(self.weekly_digest, "weekly_digest")
        _boolean(self.explicit_confirmation, "explicit_confirmation")
        if type(self.policy_version) is not str or re.fullmatch(r"[a-zA-Z0-9._:-]{1,80}", self.policy_version) is None:
            raise ValueError("policy_version")


@dataclass(frozen=True, slots=True)
class ConsentDraft:
    reason: str
    proposed: ConsentHead | None = None
    runtime_authority: RuntimeAuthority = field(default="UNAVAILABLE", init=False)


def draft_consent_change(current: ConsentHead | None, request: ConsentChange,
                         facts: AccountFacts | None, *,
                         native_later_generation_present: bool = False) -> ConsentDraft:
    """Propose one CAS successor; never write it or claim a native ACK."""
    _boolean(native_later_generation_present, "native_later_generation_present")
    if native_later_generation_present:
        return ConsentDraft("consent_history_rolled_back")
    if type(request) is not ConsentChange or (current is not None and type(current) is not ConsentHead):
        return ConsentDraft("invalid_contract_type")
    refusal = _account_refusal(facts, session=True)
    if refusal:
        return ConsentDraft(refusal)
    assert facts is not None
    if request.binding != facts.native_revision.binding or current is not None and current.binding != request.binding:
        return ConsentDraft("owner_binding_mismatch")
    if not request.explicit_confirmation:
        return ConsentDraft("explicit_confirmation_required")
    if request.recipient_sha256 != facts.native_mailbox_sha256:
        return ConsentDraft("recipient_mismatch")
    generation = current.generation if current is not None else 0
    if request.expected_generation != generation:
        return ConsentDraft("consent_generation_mismatch")
    proposed = ConsentHead(request.binding, generation + 1, request.recipient_sha256,
                           request.weekly_digest, request.policy_version, current.sha256 if current else None)
    return ConsentDraft("candidate_transition_only", proposed)


@dataclass(frozen=True, slots=True)
class DigestEnvelope:
    binding: AccountBinding
    consent_generation: int
    consent_sha256: str
    recipient_sha256: str
    iso_week: str
    message_sha256: str
    created_at_ms: int
    expires_at_ms: int

    def __post_init__(self) -> None:
        if type(self.binding) is not AccountBinding:
            raise ValueError("binding")
        _positive(self.consent_generation, "consent_generation")
        for name in ("consent_sha256", "recipient_sha256", "message_sha256"):
            _digest(getattr(self, name), name)
        if type(self.iso_week) is not str or re.fullmatch(r"[0-9]{4}-W[0-9]{2}", self.iso_week) is None:
            raise ValueError("iso_week")
        date.fromisocalendar(int(self.iso_week[:4]), int(self.iso_week[-2:]), 1)
        _positive(self.created_at_ms, "created_at_ms")
        _positive(self.expires_at_ms, "expires_at_ms")
        if not 0 < self.expires_at_ms - self.created_at_ms <= MAX_SEND_WINDOW_MS:
            raise ValueError("send_window")

    @property
    def cadence_key(self) -> str:
        # A preference/email/credential change cannot mint a second weekly slot.
        return f"weekly-digest:{self.binding.subject_uuid}:{self.iso_week}"


@dataclass(frozen=True, slots=True)
class MailCapacityFacts:
    """Logical optional-category capacity, not a reserved provider envelope."""

    day_units_available: int
    month_units_available: int
    security_day_headroom: int
    security_month_headroom: int
    optional_day_units_left: int
    optional_month_units_left: int

    def __post_init__(self) -> None:
        for name in self.__dataclass_fields__:
            value = getattr(self, name)
            if type(value) is not int or not 0 <= value <= MAX_GENERATION:
                raise ValueError(name)

    def refusal(self) -> str | None:
        if (self.security_day_headroom < MESSAGE_ATTEMPT_UNITS
                or self.security_month_headroom < MESSAGE_ATTEMPT_UNITS):
            return "security_headroom_missing"
        if (self.optional_day_units_left < MESSAGE_ATTEMPT_UNITS
                or self.optional_month_units_left < MESSAGE_ATTEMPT_UNITS
                or self.day_units_available - self.security_day_headroom < MESSAGE_ATTEMPT_UNITS
                or self.month_units_available - self.security_month_headroom < MESSAGE_ATTEMPT_UNITS):
            return "optional_mail_capacity_unavailable"
        return None


@dataclass(frozen=True, slots=True)
class DigestDraft:
    reason: str
    candidate_consistent: bool = False
    runtime_authority: RuntimeAuthority = field(default="UNAVAILABLE", init=False)


def inspect_digest_candidate(head: ConsentHead | None, projection: ConsentHead | None,
                             envelope: DigestEnvelope, facts: AccountFacts | None, *,
                             now_ms: int, period_already_begun: bool,
                             native_later_generation_present: bool = False,
                             capacity: MailCapacityFacts | None = None) -> DigestDraft:
    """Check logical consistency only. No positive value is a send permission."""
    _positive(now_ms, "now_ms")
    _boolean(period_already_begun, "period_already_begun")
    _boolean(native_later_generation_present, "native_later_generation_present")
    if type(envelope) is not DigestEnvelope:
        return DigestDraft("invalid_contract_type")
    refusal = _account_refusal(facts, session=False)
    if refusal:
        return DigestDraft(refusal)
    assert facts is not None
    if type(head) is not ConsentHead or type(projection) is not ConsentHead:
        return DigestDraft("consent_missing")
    if native_later_generation_present or head != projection:
        return DigestDraft("consent_projection_mismatch")
    if not head.weekly_digest:
        return DigestDraft("digest_opted_out")
    if head.binding != facts.native_revision.binding or envelope.binding != head.binding:
        return DigestDraft("owner_binding_mismatch")
    if envelope.consent_generation != head.generation or envelope.consent_sha256 != head.sha256:
        return DigestDraft("queued_consent_superseded")
    if head.recipient_sha256 != facts.native_mailbox_sha256 or envelope.recipient_sha256 != head.recipient_sha256:
        return DigestDraft("recipient_mismatch")
    if not envelope.created_at_ms <= now_ms < envelope.expires_at_ms:
        return DigestDraft("send_window_expired")
    current_week = datetime.fromtimestamp(now_ms / 1000, UTC).isocalendar()
    if envelope.iso_week != f"{current_week.year:04d}-W{current_week.week:02d}":
        return DigestDraft("delivery_week_mismatch")
    if type(capacity) is not MailCapacityFacts:
        return DigestDraft("mail_capacity_facts_missing")
    capacity_refusal = capacity.refusal()
    if capacity_refusal:
        return DigestDraft(capacity_refusal)
    if period_already_begun:
        return DigestDraft("period_send_already_begun")
    return DigestDraft("candidate_consistent_only", True)
