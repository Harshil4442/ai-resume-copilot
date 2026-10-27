"""Fail-closed activation of the new contract, independent of old billing flags.

The review is an operator-controlled deployment artifact. Evidence hashes point
to retained review artifacts; this module never manufactures those artifacts or
interprets an estimate as a paid-production reconciliation.
"""
from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from typing import Literal

from pydantic import Field, model_validator

from ...billing.razorpay import RazorpaySettings
from .finance import Closed, FinancialPolicy, FundingUnavailable, forecast, require_current_policy
from .pricing import CATALOG_VERSION, PRODUCTS


class ReleaseReview(Closed):
    catalog_version: Literal["inr-unified-2026-10-27-v1"]
    policy: FinancialPolicy
    reviewed_by: str = Field(min_length=1, max_length=120)
    evidence: dict[str, str]
    shadow_started_at: datetime
    shadow_ended_at: datetime
    shadow_successful_operations: int = Field(strict=True, ge=200)
    monthly_shared_minor: int = Field(strict=True, ge=0)
    monthly_additional_business_minor: int = Field(strict=True, ge=0)
    planning_paid_packs: int = Field(strict=True, gt=0, le=2000)
    funded_runway_minor: int = Field(strict=True, gt=0)

    @model_validator(mode="after")
    def complete_review(self):
        import re

        required = {"quality", "legacy_rights", "commercial_hosting", "invoices",
                    "security", "shadow_accounting", "delivery_routes"}
        if set(self.evidence) != required or any(
            not re.fullmatch(r"[a-f0-9]{64}", value) for value in self.evidence.values()
        ):
            raise ValueError("Every release gate needs a retained evidence artifact digest")
        if self.shadow_started_at.tzinfo is None or self.shadow_ended_at.tzinfo is None:
            raise ValueError("Shadow evidence dates must be timezone-aware")
        if self.shadow_ended_at - self.shadow_started_at < timedelta(days=7):
            raise ValueError("At least seven days of representative shadow accounting are required")
        if self.policy.execution_minor_per_credit > 45:
            raise ValueError("The reviewed tariff funds at most 45 paise per credit")
        overhead = self.monthly_shared_minor + self.monthly_additional_business_minor
        if self.funded_runway_minor < overhead:
            raise ValueError("A month of fixed costs must be funded even if nobody purchases")
        # Preserve the approved 20%-of-10k adverse case even when the current
        # invoice is lower. Forecast registrations cannot relax a sales gate.
        # Each all-one-pack mix covers its fully redeemed launch allowance.
        for product in PRODUCTS.values():
            result = forecast(self.policy, gross_minor=product.offer_amount_minor * self.planning_paid_packs,
                              consumed_credits=product.credits * self.planning_paid_packs,
                              shared_minor=max(self.monthly_shared_minor, 3_500_000),
                              additional_business_minor=self.monthly_additional_business_minor)
            if not result["minimum_margin_met"]:
                raise ValueError("A launch pack mix misses the reviewed minimum margin")
        return self


def rollout_enabled() -> bool:
    return os.getenv("UNIFIED_CREDITS_ENABLED", "").lower() == "true"


def current_review(*, now: datetime | None = None) -> ReleaseReview:
    instant = now or datetime.now(UTC)
    try:
        review = ReleaseReview.model_validate_json(os.getenv("UNIFIED_CREDIT_RELEASE_REVIEW_JSON", ""))
        production = os.getenv("APP_ENV", "production").lower() not in {"test", "development", "dev", "local", "staging"}
        require_current_policy(review.policy, now=instant, commercial=production)
        if review.catalog_version != CATALOG_VERSION or review.shadow_ended_at > instant:
            raise ValueError("Release evidence does not match this release")
        if instant - review.shadow_ended_at > timedelta(days=31):
            raise ValueError("The release evidence needs a fresh review")
        return review
    except (ValueError, FundingUnavailable) as exc:
        raise FundingUnavailable("Unified credit purchases await a current release review.") from exc


def checkout_ready() -> bool:
    if not rollout_enabled() or not RazorpaySettings.from_env().checkout_enabled:
        return False
    try:
        current_review()
        return True
    except FundingUnavailable:
        return False
