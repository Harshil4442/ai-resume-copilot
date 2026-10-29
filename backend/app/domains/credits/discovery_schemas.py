"""One explicitly reviewed discovery batch, never an implicit series of purchases."""

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from ..employer.search_preferences import SearchPreferencesV1


class DiscoveryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    resume_id: int = Field(gt=0)
    role: str = Field(min_length=2, max_length=200)
    location: str = Field(default="", max_length=200)
    desired_count: int = Field(default=5, ge=1, le=5, strict=True)
    remote_only: bool = False
    published_within_days: int | None = Field(default=None, ge=1, le=365)
    excluded_employers: list[Annotated[str, Field(max_length=200)]] = Field(
        default_factory=list, max_length=100
    )
    preferences: SearchPreferencesV1 | None = None


class DiscoveryCreate(DiscoveryRequest):
    accepted_service_quote_id: str = Field(min_length=1, max_length=64)
    idempotency_key: str = Field(min_length=8, max_length=160, pattern=r"^[A-Za-z0-9_.:-]+$")


class DiscoveryBatchView(BaseModel):
    id: str
    status: str
    billing_currency: Literal["credits"] = "credits"
    desired_count: int
    delivered_count: int
    charged_credits: int
    reserved_credits: int
    refunded_credits: int
    partial_batch: bool
    maximum_batch_size: Literal[5] = 5
    scope: dict[str, Any]
    items: list[dict[str, Any]]
