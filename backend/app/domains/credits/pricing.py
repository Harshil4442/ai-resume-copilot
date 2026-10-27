"""Server-owned finite tariff and genuine campaign clock; never a client price."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .models import CreditCampaign
from .schemas import CampaignView, CreditCatalogView, CreditProductView

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


@dataclass(frozen=True)
class CreditProduct:
    sku: str
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


def aware(value: datetime) -> datetime:
    # SQLite returns naive UTC despite timezone-aware metadata. Public inputs
    # must already have been validated; only persisted timestamps use this helper.
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def campaign_view(
    campaign: CreditCampaign | None, product: CreditProduct, *, now: datetime
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
    verified = bool(campaign.reference_price_verified)
    if verified and (
        not campaign.reference_evidence_id
        or campaign.reference_approved_at is None
        or aware(campaign.reference_approved_at) > stamp
    ):
        raise CreditContractError("A crossed-out price requires reviewed reference evidence.")
    status = "scheduled" if stamp < start else "active" if stamp < end else "ended"
    return CampaignView(
        id=campaign.id,
        version=campaign.version,
        starts_at=start,
        ends_at=end,
        timezone=campaign.timezone,
        status=status,
        standard_amount_minor=product.standard_amount_minor,
        reference_price_verified=verified and status == "active",
    )


def public_catalog(
    *, campaign: CreditCampaign | None, now: datetime, checkout_enabled: bool = False
) -> CreditCatalogView:
    products = []
    for product in PRODUCTS.values():
        offer = campaign_view(campaign, product, now=now)
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
