from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)


class DeviceRequest(StrictModel):
    protocol_version: Literal[1]
    device_id: UUID
    request_id: UUID
    operation: Literal["prepare", "status", "application", "inspect", "begin", "outcome", "cancel"]
    issued_at_ms: int = Field(strict=True, gt=0)
    body: dict[str, Any]
    signature: str = Field(min_length=80, max_length=100)

    @field_validator("protocol_version", mode="before")
    @classmethod
    def exact_version(cls, value):
        if type(value) is not int or value != 1:
            raise ValueError("Exact protocol version required")
        return value


class CandidateRequest(StrictModel):
    identity: UUID
    review_digest: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    key_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    confirmed: bool = False

    @field_validator("confirmed", mode="before")
    @classmethod
    def exact_boolean(cls, value):
        if type(value) is not bool:
            raise ValueError("Explicit boolean confirmation required")
        return value
