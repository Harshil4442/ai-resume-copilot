"""Versioned public contracts, independent of legacy dual-wallet responses."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

SCHEMA_VERSION: Literal["unified-credits-v1"] = "unified-credits-v1"


class Closed(BaseModel):
    model_config = ConfigDict(extra="forbid", from_attributes=True)


class CampaignView(Closed):
    id: str
    version: str
    starts_at: datetime
    ends_at: datetime
    timezone: str
    status: Literal["scheduled", "active", "ended"]
    standard_amount_minor: int = Field(gt=0, strict=True)
    reference_price_verified: bool


class CreditProductView(Closed):
    sku: Literal["starter_pass", "growth_pass", "scale_pass"]
    name: str
    amount_minor: int = Field(gt=0, strict=True)
    credits: int = Field(gt=0, strict=True)
    duration_days: Literal[30] = 30
    auto_renews: Literal[False] = False
    purchase_enabled: bool
    terms_version: str
    campaign: CampaignView | None = None


class CreditServiceOfferView(Closed):
    operation: str
    credits: int = Field(gt=0)
    deliverable: str
    supported_limits: dict[str, int]
    source_formats: list[str]
    route_version: str


class CreditCatalogView(Closed):
    schema_version: Literal["unified-credits-v1"] = SCHEMA_VERSION
    catalog_version: str
    server_time: datetime
    market: Literal["IN"] = "IN"
    currency: Literal["INR"] = "INR"
    checkout_enabled: bool
    products: list[CreditProductView]
    eligible_services: list[CreditServiceOfferView] = Field(default_factory=list)


class PurchaseQuoteRequest(Closed):
    sku: Literal["starter_pass", "growth_pass", "scale_pass"]
    idempotency_key: str = Field(min_length=8, max_length=160)


class PurchaseQuoteView(Closed):
    schema_version: Literal["unified-credits-v1"] = SCHEMA_VERSION
    quote_id: str
    sku: Literal["starter_pass", "growth_pass", "scale_pass"]
    name: str
    catalog_version: str
    terms_version: str
    amount_minor: int = Field(gt=0, strict=True)
    currency: Literal["INR"] = "INR"
    credits: int = Field(gt=0, strict=True)
    duration_days: Literal[30] = 30
    auto_renews: Literal[False] = False
    created_at: datetime
    expires_at: datetime
    pass_expires_at: datetime
    server_time: datetime
    campaign: CampaignView | None = None
    eligible_services: list[CreditServiceOfferView] = Field(default_factory=list)


class CreditLotView(Closed):
    grant_id: str
    remaining_credits: int
    expires_at: datetime | None
    funding_kind: str


class CreditWalletView(Closed):
    schema_version: Literal["unified-credits-v1"] = SCHEMA_VERSION
    server_time: datetime
    available_credits: int = Field(ge=0)
    debt_credits: int = Field(ge=0)
    reserved_credits: int = Field(ge=0)
    lots: list[CreditLotView]


class FreeCoreLimitView(Closed):
    per_account_30_days: int = Field(gt=0)
    max_input_bytes: int = Field(gt=0)
    max_source_records: int = Field(gt=0)


class CreditAvailabilityView(Closed):
    catalog_enabled: bool
    execution_enabled: bool
    supported_operations: list[str]
    free_core_operations: list[str] = Field(default_factory=list)
    free_core_limits: dict[str, FreeCoreLimitView] = Field(default_factory=dict)


class ServiceQuoteView(Closed):
    schema_version: Literal["unified-service-quote-v1"] = "unified-service-quote-v1"
    quote_id: str
    operation: str
    input_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    route_id: str
    route_version: str
    mode: Literal["enhanced", "native"]
    tariff_version: str
    credits: int = Field(gt=0)
    deliverable: str
    supported_limits: dict[str, int]
    created_at: datetime
    expires_at: datetime
    server_time: datetime


class CreditHistoryItem(Closed):
    id: str
    event_type: str
    amount: int
    balance_after: int
    created_at: datetime
    details: dict


class CreditHistoryView(Closed):
    schema_version: Literal["unified-credits-v1"] = SCHEMA_VERSION
    items: list[CreditHistoryItem]
    next_cursor: str | None


class CreditOrderStatusView(Closed):
    schema_version: Literal["unified-credits-v1"] = SCHEMA_VERSION
    order_id: str
    provider_order_id: str | None
    status: str
    payment_status: str
    fulfilled: bool
    checkout_available: bool
    amount_minor: int
    refunded_amount_minor: int
    currency: Literal["INR"] = "INR"
    sku: str
    created_at: datetime
    paid_at: datetime | None
    pass_expires_at: datetime | None
    accepted_quote: PurchaseQuoteView


class CreditCheckoutView(Closed):
    schema_version: Literal["unified-credits-v1"] = SCHEMA_VERSION
    order_id: str
    provider: Literal["razorpay"] = "razorpay"
    provider_order_id: str
    key_id: str
    amount_minor: int
    currency: Literal["INR"] = "INR"
    name: str
    description: str
    accepted_quote: PurchaseQuoteView
    prefill: dict[str, str]
