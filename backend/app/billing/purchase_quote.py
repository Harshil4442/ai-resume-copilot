"""Pure purchase acceptance for new orders and immutable checkout recovery."""
from __future__ import annotations

import hashlib
from datetime import datetime
from typing import Any, Literal

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field


class PurchaseQuoteAcceptance(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    catalog_version: str = Field(min_length=1, max_length=160)
    amount_minor: int = Field(gt=0, strict=True)
    currency: Literal["INR"]
    job_service_credits: int = Field(ge=0, strict=True)
    analysis_units: int = Field(ge=0, strict=True)


class CheckoutOrderResponse(BaseModel):
    order_id: str
    provider: Literal["razorpay"]
    provider_order_id: str
    key_id: str
    amount_minor: int
    currency: Literal["INR"]
    name: str
    description: str
    accepted_quote: PurchaseQuoteAcceptance
    prefill: dict[str, str] | None = None


class BillingOrderStatusResponse(BaseModel):
    order_id: str
    provider_order_id: str | None
    checkout_available: bool = False
    payment_reference: str | None
    sku: str
    entitlement_kind: str
    entitlement_quantity: int
    status: Literal["pending", "paid", "failed", "refunded"]
    fulfilled: bool
    amount_minor: int
    refunded_amount_minor: int
    currency: Literal["INR"]
    created_at: datetime
    paid_at: datetime | None
    refunded_at: datetime | None
    accepted_quote: PurchaseQuoteAcceptance | None


def purchase_request_id(owner_id: int, key: str) -> str:
    """The existing unique public ID binds even captured/terminal attempts."""
    return "ord_" + hashlib.sha256(f"{owner_id}:{key}".encode()).hexdigest()[:32]


def product_quote(product: Any, catalog_version: str) -> PurchaseQuoteAcceptance:
    return PurchaseQuoteAcceptance(
        catalog_version=catalog_version, amount_minor=product.amount_minor,
        currency=product.currency, job_service_credits=product.entitlement_quantity,
        analysis_units=product.analysis_units,
    )


def order_quote(order: Any) -> PurchaseQuoteAcceptance:
    """Use the purchased snapshot, including historical zero-AI service packs."""
    credits = order.entitlement_quantity if order.entitlement_kind in {"credit_bundle", "job_service_credits"} else 0
    units = order.entitlement_quantity if order.entitlement_kind == "analysis_units" else 0
    if order.entitlement_kind == "credit_bundle":
        entitlements = (order.cost_policy_snapshot or {}).get("entitlements", {})
        units = entitlements.get("analysis_units")
        if (entitlements.get("job_service_credits") != credits or type(units) is not int or units < 0):
            raise HTTPException(409, "This original checkout requires entitlement reconciliation.")
    return PurchaseQuoteAcceptance(
        catalog_version=order.catalog_version, amount_minor=order.gross_amount_minor,
        currency=order.currency, job_service_credits=credits, analysis_units=units,
    )


def validate_purchase_quote(accepted: PurchaseQuoteAcceptance | None,
                            expected: PurchaseQuoteAcceptance) -> None:
    if accepted != expected:
        raise HTTPException(409, {
            "code": "billing_quote_requires_review",
            "message": "The pack price or included credits changed or need review. No new checkout was created. Refresh the catalog, review the pack, then click Pay again.",
        })
