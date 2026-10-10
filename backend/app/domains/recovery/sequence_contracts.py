"""V2 local-only fill sequence; no production adapter, authentication or browser wiring.

Digest declarations do not establish actual file bytes, values, current DOM or
review authenticity. A trusted sealed-package/browser adapter must verify those
facts; no such adapter is supplied by this Python-only core. Canonical vectors
use exact Unicode scalar strings (no normalization), typed values, ASCII keys,
and JS-safe integers. Storage generations remain canonical decimal strings.
Reused v1 Python-only artifact tombstones internally convert generation to an
integer revision; those inherited records are not v2 wire/canonical vectors.
A future production adapter needs an explicit string-generation tombstone shape.
"""
from __future__ import annotations

import hashlib
from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field, field_validator, model_validator

from .contracts import Binding, Contract, Digest, Identifier, Timestamp, canonical, fingerprint

JS_SAFE_MAX = 2**53 - 1
PositiveInteger = Annotated[int, Field(strict=True, gt=0, le=JS_SAFE_MAX)]
Index = Annotated[int, Field(strict=True, ge=0, le=7)]


def _canonical_data(value: object) -> None:
    if value is None or type(value) is bool:
        return
    if type(value) is int:
        if not -JS_SAFE_MAX <= value <= JS_SAFE_MAX:
            raise ValueError("Canonical integers must be JS-safe")
        return
    if type(value) is str:
        # Reject lone UTF-16 surrogate code points rather than silently replacing
        # them or choosing a different Python/JavaScript representation.
        value.encode("utf-8", errors="strict")
        return
    if type(value) is list:
        for item in value:
            _canonical_data(item)
        return
    if type(value) is dict:
        for key, item in value.items():
            if type(key) is not str or not key.isascii():
                raise ValueError("Canonical object keys must be ASCII strings")
            _canonical_data(item)
        return
    raise ValueError("Canonical data supports only null, booleans, safe integers, strings, lists and objects")


def sequence_hash(purpose: str, payload: dict) -> str:
    if not purpose.isascii() or not purpose.replace("-", "").isalnum():
        raise ValueError("A bounded canonical purpose is required")
    _canonical_data(payload)
    return hashlib.sha256((f"hirewiz.fill.{purpose}.v2\n" + canonical(payload)).encode()).hexdigest()


def typed_value_hash(value: str | bool) -> str:
    if type(value) not in {str, bool}:
        raise ValueError("Only exact string or boolean field values are supported")
    return sequence_hash("value", {"type": "boolean" if type(value) is bool else "string", "value": value})


def nonce_hash(value: UUID) -> str:
    return sequence_hash("nonce", {"nonce": str(value)})


class Artifact(Contract):
    sha256: Digest
    generation: Annotated[str, Field(pattern=r"^[1-9][0-9]{0,30}$")]
    descriptor_sha256: Digest  # includes reviewed native filename, MIME and size
    size_bytes: Annotated[int, Field(strict=True, gt=0, le=5_000_000)]
    media_type: Literal["application/pdf", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"]


class Target(Contract):
    origin: str
    url_sha256: Digest
    tab_id: PositiveInteger
    frame_id: Literal[0]
    document_id: Identifier
    adapter_id: Identifier
    adapter_revision: Identifier
    form_version: Identifier
    form_sha256: Digest

    @field_validator("frame_id", mode="before")
    @classmethod
    def strict_frame(cls, value: object) -> object:
        if type(value) is not int:
            raise ValueError("Frame index must be an exact integer")
        return value

    @field_validator("origin")
    @classmethod
    def exact_origin(cls, value: str) -> str:
        return Binding.exact_https_origin(value)


class FillStep(Contract):
    index: Index
    step_id: Identifier
    field_id: Identifier
    action: Literal["fill"]
    control_type: Literal["text", "email", "textarea", "select-one", "checkbox"]
    value_type: Literal["string", "boolean"]
    field_descriptor_sha256: Digest
    value_sha256: Digest
    expected_before_sha256: Digest

    @model_validator(mode="after")
    def typed_control(self) -> FillStep:
        if (self.control_type == "checkbox") != (self.value_type == "boolean"):
            raise ValueError("Only explicitly reviewed boolean checkbox values are supported")
        return self


class SequenceBase(Contract):
    authority_id: UUID
    subject_uuid: UUID
    epoch_id: UUID
    epoch_generation: PositiveInteger
    application_id: Identifier
    employer_key: Digest
    tenant_id: Identifier
    opening_key: Digest
    approval_id: Identifier
    approval_revision: PositiveInteger
    admission_id: Identifier
    policy_sha256: Digest
    pricing_sha256: Digest
    package_digest: Digest
    grant_id: Identifier
    grant_revision: PositiveInteger
    device_id: Identifier
    device_key_sha256: Digest
    executor_revision: Identifier
    deadline_ms: Timestamp
    artifact: Artifact
    target: Target
    review_payload_sha256: Digest
    acknowledgement_version: Identifier


class Manifest(Contract):
    protocol_version: Literal[2]
    attempt_id: UUID
    candidate_actor_id: Identifier
    candidate_actor_digest: Digest
    base: SequenceBase
    steps: Annotated[tuple[FillStep, ...], Field(min_length=1, max_length=8)]

    @field_validator("protocol_version", mode="before")
    @classmethod
    def strict_version(cls, value: object) -> object:
        if type(value) is not int:
            raise ValueError("Protocol version must be an exact integer")
        return value

    @model_validator(mode="after")
    def ordered_unique_steps(self) -> Manifest:
        if ([step.index for step in self.steps] != list(range(len(self.steps))) or
                len({step.step_id for step in self.steps}) != len(self.steps) or
                len({step.field_id for step in self.steps}) != len(self.steps)):
            raise ValueError("A contiguous sequence of unique steps and fields is required")
        return self

    @property
    def sequence_digest(self) -> str:
        # No derived digest is included in its own canonical input.
        return sequence_hash("sequence", self.model_dump(mode="json"))

    @property
    def review_digest(self) -> str:
        return sequence_hash("review", {"sequence_digest": self.sequence_digest,
            "review_payload_sha256": self.base.review_payload_sha256,
            "acknowledgement_version": self.base.acknowledgement_version, "actions": ["fill"]})

    @property
    def context_digest(self) -> str:
        return sequence_hash("context", {"sequence_digest": self.sequence_digest,
                                          "review_digest": self.review_digest})

    def step_digest(self, index: int) -> str:
        return sequence_hash("step", {"context_digest": self.context_digest,
                                      "step": self.steps[index].model_dump(mode="json")})

    def binding(self, index: int = 0) -> Binding:
        # Reuse unchanged v1 checks for current epoch/subject/grant/device/artifact.
        base = self.base.model_dump(mode="json", exclude={"artifact", "target",
            "review_payload_sha256", "acknowledgement_version"})
        return Binding(**base, artifact_sha256=self.base.artifact.sha256,
            artifact_generation=self.base.artifact.generation, origin=self.base.target.origin,
            action="fill", field_id=self.steps[index].field_id,
            value_sha256=self.steps[index].value_sha256, review_digest=self.review_digest)

    @property
    def opening_claim_key(self) -> str:
        return self.binding().opening_claim_key


class StepMayAct(Contract):
    protocol_version: Literal[2]
    attempt_id: UUID
    permit_id: UUID
    sequence_digest: Digest
    context_digest: Digest
    step_digest: Digest
    index: Index
    decision_nonce: UUID
    begun_sequence: PositiveInteger
    issued_at_ms: Timestamp
    expires_at_ms: Timestamp

    @field_validator("protocol_version", mode="before")
    @classmethod
    def strict_version(cls, value: object) -> object:
        if type(value) is not int:
            raise ValueError("Protocol version must be an exact integer")
        return value

    @model_validator(mode="after")
    def short_decision(self) -> StepMayAct:
        if not 0 < self.expires_at_ms - self.issued_at_ms <= 2_000:
            raise ValueError("A bounded fresh decision is required")
        return self


class StepDecision(Contract):
    status: Literal["BEGUN", "ALREADY_BEGUN", "LOCAL_FILLED", "UNKNOWN"]
    may_act: StepMayAct | None = None

    @model_validator(mode="after")
    def unique_winner(self) -> StepDecision:
        if self.may_act is not None and self.status != "BEGUN":
            raise ValueError("Only the sole begin winner can receive a decision")
        return self


class OutcomeDecision(Contract):
    status: Literal["LOCAL_FILLED", "UNKNOWN"]
    next_index: Annotated[int, Field(strict=True, ge=0, le=8)]
    continuation_nonce: UUID | None = None  # never a may-act permission

    @model_validator(mode="after")
    def no_unknown_continuation(self) -> OutcomeDecision:
        if self.status == "UNKNOWN" and self.continuation_nonce is not None:
            raise ValueError("An unknown outcome cannot continue")
        return self


class Observation(Contract):
    context_digest: Digest
    index: Index
    permit_id: UUID
    begun_sequence: PositiveInteger
    document_id: Identifier
    value_sha256: Digest
    status: Literal["LOCAL_FILLED", "UNKNOWN"]

    @property
    def digest(self) -> str:
        return sequence_hash("observation", self.model_dump(mode="json"))


def actor_digest(identity: dict) -> str:
    return fingerprint(identity)
