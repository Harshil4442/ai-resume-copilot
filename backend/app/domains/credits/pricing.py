"""Server-owned finite tariff and genuine campaign clock; never a client price."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import AwareDatetime, Field, model_validator

from .models import CreditCampaign
from .schemas import CampaignView, Closed, CreditCatalogView, CreditProductView

CATALOG_VERSION = "inr-unified-2026-10-27-v1"
TERMS_VERSION = "credit-pass-2026-10-27-v1"
TARIFF_VERSION = "credit-services-2026-10-27-v1"
EXECUTION_MINOR_PER_CREDIT = 45
PURCHASE_QUOTE_LIFETIME = timedelta(minutes=15)
PASS_LIFETIME = timedelta(days=30)
RESTORATION_LIFETIME = timedelta(days=7)

SERVICE_CREDITS = {
    "resume_enrichment": 2,
    "enhanced_job_match": 3,
    "interview_questions": 5,
    "resume_tailor": 8,
    "resume_tailor_extended": 15,
    "bullet_rewrite": 2,
    "enhanced_qa": 2,
    "learning_strategy": 4,
    "job_discovery_batch": 1,
    "verified_submission": 3,
    "market_report": 1,
}


class CreditContractError(ValueError):
    """A stale, ambiguous or unsupported financial contract cannot be admitted."""


PassSku = Literal["starter_pass", "growth_pass", "scale_pass"]


@dataclass(frozen=True)
class CreditProduct:
    sku: PassSku
    name: str
    offer_amount_minor: int
    standard_amount_minor: int
    credits: int


PRODUCTS = {
    p.sku: p
    for p in (
        CreditProduct("starter_pass", "Starter", 5900, 7900, 40),
        CreditProduct("growth_pass", "Growth", 17900, 22900, 130),
        CreditProduct("scale_pass", "Scale", 44900, 59900, 360),
    )
}


class ReferencePriceReview(Closed):
    standard_amount_minor: int = Field(strict=True, gt=0)
    terms_version: Literal["credit-pass-2026-10-27-v1"]
    market: Literal["IN"]
    currency: Literal["INR"]
    approved_at: AwareDatetime
    evidence_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


class PurchasePricingReview(Closed):
    catalog_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    # A null digest explicitly approves the standard catalog without a campaign.
    campaign_sha256: str | None = Field(pattern=r"^[a-f0-9]{64}$")
    reference_prices: dict[PassSku, ReferencePriceReview] = Field(default_factory=dict)

    @model_validator(mode="after")
    def exact_reference_prices(self):
        if self.reference_prices and self.campaign_sha256 is None:
            raise ValueError("Comparison prices require a reviewed campaign")
        for sku, review in self.reference_prices.items():
            if review.standard_amount_minor != PRODUCTS[sku].standard_amount_minor:
                raise ValueError("Reference evidence must match the exact pack price")
        return self


def _pricing_digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def catalog_pricing_sha256() -> str:
    return _pricing_digest({
        "catalog_version": CATALOG_VERSION, "terms_version": TERMS_VERSION, "market": "IN", "currency": "INR",
        "duration_days": PASS_LIFETIME.days, "auto_renews": False,
        "products": {sku: [item.offer_amount_minor, item.standard_amount_minor, item.credits] for sku, item in PRODUCTS.items()},
    })


def aware(value: datetime) -> datetime:
    # SQLite returns naive UTC despite timezone-aware metadata. Public inputs
    # must already have been validated; only persisted timestamps use this helper.
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def campaign_pricing_sha256(campaign: CreditCampaign) -> str:
    return _pricing_digest({
        "id": campaign.id, "version": campaign.version, "catalog_version": campaign.catalog_version,
        "starts_at": aware(campaign.starts_at).isoformat(), "ends_at": aware(campaign.ends_at).isoformat(),
        "timezone": campaign.timezone,
    })


def require_reviewed_purchase_pricing(review: PurchasePricingReview, campaign: CreditCampaign | None) -> None:
    campaign_sha256 = campaign_pricing_sha256(campaign) if campaign is not None else None
    if review.catalog_sha256 != catalog_pricing_sha256() or review.campaign_sha256 != campaign_sha256:
        raise CreditContractError("The catalog or launch campaign has changed since review.")


def campaign_view(
    campaign: CreditCampaign | None, product: CreditProduct, *, now: datetime,
    reference: ReferencePriceReview | None = None,
) -> CampaignView | None:
    if campaign is None:
        return None
    if not campaign.enabled or campaign.catalog_version != CATALOG_VERSION:
        raise CreditContractError("This campaign does not authorize the current catalog.")
    start, end, stamp = aware(campaign.starts_at), aware(campaign.ends_at), aware(now)
    if end - start != timedelta(days=30):
        raise CreditContractError("The introductory campaign requires one fixed 30-day interval.")
    try:
        ZoneInfo(campaign.timezone)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise CreditContractError("The campaign timezone is invalid.") from exc
    status = "scheduled" if stamp < start else "active" if stamp < end else "ended"
    return CampaignView(
        id=campaign.id,
        version=campaign.version,
        starts_at=start,
        ends_at=end,
        timezone=campaign.timezone,
        status=status,
        standard_amount_minor=product.standard_amount_minor,
        reference_price_verified=bool(reference and reference.approved_at <= stamp and status == "active"),
    )


def public_catalog(
    *, campaign: CreditCampaign | None, now: datetime, checkout_enabled: bool = False,
    review: PurchasePricingReview | None = None,
) -> CreditCatalogView:
    if review is not None:
        require_reviewed_purchase_pricing(review, campaign)
    references = review.reference_prices if review is not None else {}
    products = []
    for product in PRODUCTS.values():
        offer = campaign_view(campaign, product, now=now, reference=references.get(product.sku))
        amount = (
            product.offer_amount_minor
            if offer and offer.status == "active"
            else product.standard_amount_minor
        )
        products.append(
            CreditProductView(
                sku=product.sku,
                name=product.name,
                amount_minor=amount,
                credits=product.credits,
                purchase_enabled=checkout_enabled,
                terms_version=TERMS_VERSION,
                campaign=offer,
            )
        )
    return CreditCatalogView(
        catalog_version=CATALOG_VERSION,
        server_time=aware(now),
        checkout_enabled=checkout_enabled,
        products=products,
    )
