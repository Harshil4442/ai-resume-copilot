"""Distinct disabled v3 publication and scoped denial wire contracts."""

from __future__ import annotations

from typing import Annotated, Literal
from uuid import UUID

from pydantic import ConfigDict, Field, model_validator

from .contracts import Binding, Contract, Digest, Identifier, Timestamp, canonical, fingerprint
from .gcp_contracts import Generation, RegistryPin
from .gcp_password_contracts import PasswordSigningMarker
from .gcp_password_lifetime_contracts import PasswordLifetimeEffects
from .gcp_publication_contracts import PublicationPin
from .pairing_contracts import PositiveInteger

Ordinal = Annotated[int, Field(strict=True, ge=0, le=2**53 - 1)]
Lane = Annotated[int, Field(strict=True, ge=0, le=255)]


class PartitionedContract(Contract):
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True,
                              revalidate_instances="always")


class PartitionedPin(PartitionedContract):
    protocol_version: Literal[3] = 3
    authority: PublicationPin
    lanes: Literal[2, 4, 8, 16, 32, 64, 128, 256] = 64
    directory_shards: Literal[2, 4, 8, 16, 32, 64] = 8
    segment_size: Annotated[int, Field(strict=True, ge=1, le=1024)] = 128


class PartitionedResource(PartitionedContract):
    pin: PartitionedPin
    bucket: Identifier
    generation: Generation
    sha256: Digest

    @model_validator(mode="after")
    def exact(self) -> PartitionedResource:
        if self.bucket != self.pin.authority.journal_bucket or self.sha256 != fingerprint(self.pin.model_dump(mode="json")):
            raise ValueError("V3 resource must pin its full configuration and authority")
        return self

    @property
    def path(self) -> str:
        return f"authority-publication-v3-resources/{fingerprint(self.pin.authority.target.model_dump(mode='json'))}.json"

    @property
    def close_path(self) -> str:
        return f"authority-publication-v3-closures/{fingerprint(self.pin.model_dump(mode='json'))}.json"


class PartitionedRoot(PartitionedContract):
    pin: PartitionedPin
    phase: Literal["OPEN", "CLOSED"]
    close_id: UUID | None = None

    @model_validator(mode="after")
    def exact(self) -> PartitionedRoot:
        if (self.phase == "CLOSED") != (self.close_id is not None):
            raise ValueError("V3 closed root requires its exact closure identity")
        return self


class LaneHead(PartitionedContract):
    lane: Lane
    segment_count: Ordinal
    segment_id: Digest | None

    @model_validator(mode="after")
    def exact(self) -> LaneHead:
        if (self.segment_count == 0) != (self.segment_id is None):
            raise ValueError("Lane registration count and current segment disagree")
        return self


class DirectoryHead(PartitionedContract):
    shard: Lane
    count: Ordinal
    digest: Digest


class SegmentRegistration(PartitionedContract):
    segment_id: Digest
    lane: Lane
    lane_ordinal: PositiveInteger
    directory_shard: Lane
    directory_ordinal: PositiveInteger
    previous: Digest
    digest: Digest

    @model_validator(mode="after")
    def exact(self) -> SegmentRegistration:
        if self.digest != fingerprint(self.model_dump(mode="json", exclude={"digest"})):
            raise ValueError("Segment registration lacks its exact chain binding")
        return self


class SegmentState(PartitionedContract):
    registration: SegmentRegistration
    phase: Literal["OPEN", "SEALED"]
    count: Ordinal
    digest: Digest


class ScopedCredentialRevision(PartitionedContract):
    subject_uuid: UUID
    account_binding_id: UUID
    candidate_id: PositiveInteger
    principal_sha256: Digest
    auth_generation: PositiveInteger
    credential_sha256: Digest


class ScopedSubject(ScopedCredentialRevision):
    enrollment_operation: UUID


class ScopedSession(PartitionedContract):
    subject_uuid: UUID
    account_binding_id: UUID
    session_id: UUID
    auth_generation: PositiveInteger
    principal_sha256: Digest
    expires_at_ms: Timestamp
    credential_sha256: Digest
    activation_operation: UUID


class GenerationHistory(PartitionedContract):
    subject_uuid: UUID
    previous_generation: Ordinal
    auth_generation: PositiveInteger
    operation_id: UUID | None
    credential_sha256: Digest

    @model_validator(mode="after")
    def exact(self) -> GenerationHistory:
        if self.auth_generation != self.previous_generation + 1:
            raise ValueError("Scoped authentication history must advance exactly once")
        return self


class ScopedTombstone(PartitionedContract):
    subject_uuid: UUID
    account_binding_id: UUID
    session_id: UUID | None = None
    operation_id: UUID


class ScopedDenial(PartitionedContract):
    kind: Literal["scoped_denial"] = "scoped_denial"
    scope: Literal["session", "generation", "subject"]
    subject: ScopedSubject
    session: ScopedSession
    next_credential_sha256: Digest | None = None

    @model_validator(mode="after")
    def exact(self) -> ScopedDenial:
        if (self.subject.subject_uuid != self.session.subject_uuid
                or self.subject.account_binding_id != self.session.account_binding_id
                or self.subject.auth_generation != self.session.auth_generation
                or self.subject.principal_sha256 != self.session.principal_sha256
                or self.subject.credential_sha256 != self.session.credential_sha256
                or (self.scope == "generation") != (self.next_credential_sha256 is not None)):
            raise ValueError("Scoped denial requires exact current subject/session and credential revision")
        return self


class ScopedPasswordReset(PartitionedContract):
    """Fresh server request plus full fixed ordinary Native projection plan."""
    kind: Literal["scoped_password_reset"] = "scoped_password_reset"
    scope: Literal["generation"] = "generation"
    subject: ScopedSubject
    session: ScopedSession
    next_credential_sha256: Digest
    request_id: UUID
    event_id: UUID
    native_effects: PasswordLifetimeEffects

    @model_validator(mode="after")
    def exact(self) -> ScopedPasswordReset:
        ScopedDenial(scope="generation", subject=self.subject, session=self.session,
                     next_credential_sha256=self.next_credential_sha256)
        if self.subject.auth_generation >= 2**53 - 1:
            raise ValueError("Reset authentication generation is exhausted")
        before = {(v.namespace, v.key): v.value for v in self.native_effects.before}
        after = {(v.namespace, v.key): v.value for v in self.native_effects.after}
        subject_id = str(self.subject.subject_uuid)
        old = {"subject_uuid": subject_id, "auth_generation": self.subject.auth_generation,
               "credential_sha256": self.subject.credential_sha256}
        new = {**old, "auth_generation": self.subject.auth_generation + 1,
               "credential_sha256": self.next_credential_sha256}
        original = before.get(("subjects", subject_id))
        owner = before.get(("pairing_account_bindings", str(self.subject.account_binding_id)))
        if (original is None or owner is None
                or original.get("auth_generation") != self.subject.auth_generation
                or original.get("principal_sha256") != self.subject.principal_sha256
                or original.get("active") is not True
                or any(owner.get(k) != v for k, v in self.subject.model_dump(mode="json",
                    include={"subject_uuid", "account_binding_id", "candidate_id", "principal_sha256"}).items())
                or before.get(("password_credential_revisions", subject_id)) != old
                or after.get(("password_credential_revisions", subject_id)) != new
                or after.get(("subjects", subject_id)) != {**original, "auth_generation": new["auth_generation"]}
                or after.get(("pairing_auth_high_water", subject_id)) != {
                    "subject_uuid": subject_id, "auth_generation": new["auth_generation"],
                    "principal_sha256": self.subject.principal_sha256}):
            raise ValueError("Reset projection must bind exact native owner and old/new credential commitments")
        event = self.native_effects.event
        if (event.get("event_id") != str(self.event_id)
                or event.get("kind") != "PASSWORD_CREDENTIAL_REVISION_PROJECTED"
                or event.get("payload") != {"request_id": str(self.request_id),
                    "subject_uuid": subject_id, "account_binding_id": str(self.subject.account_binding_id),
                    "candidate_id": self.subject.candidate_id, "principal_sha256": self.subject.principal_sha256,
                    "old_auth_generation": self.subject.auth_generation,
                    "old_credential_sha256": self.subject.credential_sha256,
                    "auth_generation": new["auth_generation"], "credential_sha256": self.next_credential_sha256}
                or before.get(("head", "global")) != {"sequence": event.get("sequence", 0) - 1,
                    "digest": event.get("previous")}
                or fingerprint({k: v for k, v in event.items() if k != "digest"}) != event.get("digest")
                or after.get(("events", str(self.event_id))) != event
                or after.get(("head", "global")) != {"sequence": event.get("sequence"), "digest": event.get("digest")}
                or set(after) != {("subjects", subject_id), ("pairing_auth_high_water", subject_id),
                    ("password_credential_revisions", subject_id), ("pairing_clock", "observed"),
                    ("events", str(self.event_id)), ("head", "global")}):
            raise ValueError("Reset projection must preserve its full fixed native event/after-images")
        return self


class ScopedBegin(PartitionedContract):
    kind: Literal["scoped_begin"] = "scoped_begin"
    subject: ScopedSubject
    session: ScopedSession
    action: Binding | PasswordSigningMarker

    @model_validator(mode="after")
    def exact(self) -> ScopedBegin:
        if (self.subject.subject_uuid != self.session.subject_uuid
                or self.subject.account_binding_id != self.session.account_binding_id
                or self.subject.auth_generation != self.session.auth_generation
                or self.subject.principal_sha256 != self.session.principal_sha256
                or self.subject.credential_sha256 != self.session.credential_sha256
                or self.action.subject_uuid != self.subject.subject_uuid):
            raise ValueError("Protected BEGIN requires one exact current subject/session/action scope")
        if isinstance(self.action, PasswordSigningMarker) and (
                self.action.context.candidate_id != self.subject.candidate_id
                or self.action.context.account_binding_id != self.subject.account_binding_id
                or self.action.context.session_id != self.session.session_id
                or self.action.principal_sha256 != self.subject.principal_sha256
                or self.action.auth_generation != self.subject.auth_generation):
            raise ValueError("Protected signing BEGIN cannot substitute a different session or principal")
        return self


class ScopedJournalIntent(PartitionedContract):
    version: Literal[3] = 3
    kind: Literal["scoped_admission"] = "scoped_admission"
    operation_id: UUID
    pin: RegistryPin
    command: Annotated[ScopedDenial | ScopedPasswordReset | ScopedBegin, Field(discriminator="kind")]
    created_at_ms: Timestamp
    deadline_ms: Timestamp

    @model_validator(mode="after")
    def exact(self) -> ScopedJournalIntent:
        if not self.created_at_ms < self.deadline_ms <= self.created_at_ms + 60_000:
            raise ValueError("Scoped protected admission requires a bounded interval")
        if isinstance(self.command, ScopedPasswordReset):
            cmd = self.command
            if (len({self.operation_id, cmd.request_id, cmd.event_id}) != 3
                    or next(v.value for v in cmd.native_effects.after
                        if v.namespace == "pairing_clock") != {"now_ms": self.created_at_ms}):
                raise ValueError("Reset request, operation/event and exact native clock must be bound")
        return self

    @property
    def partition(self) -> str:
        return fingerprint({"subject_uuid": str(self.command.subject.subject_uuid)})


class PartitionedReference(PartitionedContract):
    operation_id: UUID
    sha256: Digest
    partition: Digest
    path: Annotated[str, Field(pattern=r"^authority-intents/[a-f0-9]{64}/[a-f0-9]{64}/[a-f0-9-]{36}\.json$")]
    byte_size: Annotated[int, Field(strict=True, gt=0, le=65_536)]
    kind: Literal["safety_effect", "pairing_command", "password_lifetime_effects", "scoped_admission"]


class PartitionedRecord(PartitionedContract):
    reference: PartitionedReference
    segment_id: Digest
    ordinal: PositiveInteger
    previous: Digest
    chain_digest: Digest
    intent: dict

    @model_validator(mode="after")
    def exact(self) -> PartitionedRecord:
        raw = canonical(self.intent).encode()
        if len(raw) != self.reference.byte_size or fingerprint(self.intent) != self.reference.sha256:
            raise ValueError("V3 publication lacks its complete unchanged canonical intent")
        binding = self.model_dump(mode="json", exclude={"chain_digest", "intent"})
        if fingerprint(binding) != self.chain_digest:
            raise ValueError("V3 publication slot lacks its exact chain binding")
        stored = {"namespace": "v3_records", "key": str(self.reference.operation_id), "value": self.model_dump(mode="json")}
        if len(canonical(stored).encode()) > 65_536:
            raise ValueError("V3 complete record envelope exceeds its total byte budget")
        return self


class PartitionedCut(PartitionedContract):
    root: PartitionedRoot
    directory_heads: tuple[DirectoryHead, ...]
    segments: tuple[SegmentState, ...]
    records_sha256: Digest
    records_count: Ordinal
    authority_rows_sha256: Digest
    authority_rows_count: Ordinal
    complete_native_census: Literal[True] = True
    projection_complete: Literal[False] = False

    @model_validator(mode="after")
    def exact(self) -> PartitionedCut:
        if (self.root.phase != "CLOSED" or any(s.phase != "SEALED" for s in self.segments)
                or sum(s.count for s in self.segments) != self.records_count
                or sum(h.count for h in self.directory_heads) != len(self.segments)):
            raise ValueError("V3 complete cut requires the exact closed census and every sealed segment")
        return self


class PartitionedCompleteReceipt(PartitionedContract):
    pin: PartitionedPin
    close_id: UUID
    native_cut_sha256: Digest
    records_count: Ordinal
    authority_rows_count: Ordinal
    manifest_path: Annotated[str, Field(pattern=r"^authority-v3-complete-cuts/[a-f0-9]{64}/[a-f0-9-]{36}\.json$")]
    manifest_generation: Generation
    manifest_sha256: Digest
    complete_prefix: Literal[True] = True
    projection_complete: Literal[False] = False
