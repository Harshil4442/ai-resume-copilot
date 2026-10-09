from dataclasses import dataclass

CATALOG_VERSION = "inr-finite-2026-10-09-v3"


@dataclass(frozen=True)
class CatalogProduct:
    sku: str
    name: str
    description: str
    amount_minor: int
    amount_display: str
    currency: str
    billing_type: str
    duration_days: int
    auto_renews: bool
    entitlement_kind: str
    entitlement_quantity: int
    analysis_units: int = 0

    def public_dict(self, *, enabled_for_purchase: bool) -> dict:
        return {
            "sku": self.sku,
            "name": self.name,
            "description": self.description,
            "amount_minor": self.amount_minor,
            "amount_display": self.amount_display,
            "currency": self.currency,
            "billing_type": self.billing_type,
            "duration_days": self.duration_days,
            "auto_renews": self.auto_renews,
            "catalog_visible": True,
            "enabled_for_purchase": enabled_for_purchase,
            "entitlement_kind": self.entitlement_kind,
            "entitlement_quantity": self.entitlement_quantity,
            "analysis_units": self.analysis_units,
            "job_service_credits": self.entitlement_quantity if self.entitlement_kind == "credit_bundle" else 0,
        }


# Service credits are closed-loop HireWiz entitlements fulfilled through the same
# approved checkout. They cannot be transferred or redeemed as money.
PRODUCTS: dict[str, CatalogProduct] = {
    **{sku: CatalogProduct(sku=sku, name=name, description=f"{credits} job-service credits and {units} AI analysis units. Finite prepaid balances; no subscription or unlimited usage.", amount_minor=price, amount_display=f"₹{price // 100:,}", currency="INR", billing_type="one_time", duration_days=0, auto_renews=False, entitlement_kind="credit_bundle", entitlement_quantity=credits, analysis_units=units) for sku, name, price, credits, units in (
        ("starter_bundle", "Starter", 64_900, 100, 2),
        ("growth_bundle", "Growth", 109_900, 300, 6),
        ("scale_bundle", "Scale", 219_900, 700, 15),
    )},
    # Historical products remain available ONLY to immutable order fulfillment.
    "premium_30d": CatalogProduct(
        sku="premium_30d",
        name="HireWiz Premium — 30 days",
        description="One-time purchase of 30 days of HireWiz Premium access.",
        amount_minor=99_900,
        amount_display="₹999",
        currency="INR",
        billing_type="one_time",
        duration_days=30,
        auto_renews=False,
        entitlement_kind="premium_access",
        entitlement_quantity=30,
    ),
    "job_service_500": CatalogProduct(
        sku="job_service_500",
        name="HireWiz Job Service — 500 credits",
        description="500 prepaid credits for employer job search and supported auto-apply.",
        amount_minor=49_900,
        amount_display="₹499",
        currency="INR",
        billing_type="one_time",
        duration_days=0,
        auto_renews=False,
        entitlement_kind="job_service_credits",
        entitlement_quantity=500,
    ),
}


def get_product(sku: str) -> CatalogProduct | None:
    return PRODUCTS.get(sku)


def product_purchase_enabled(product: CatalogProduct, *, checkout_enabled: bool) -> bool:
    if not checkout_enabled or product.entitlement_kind != "credit_bundle":
        return False
    from .cost_policy import CostPolicyUnavailable, service_prices
    try:
        service_prices()
        return True
    except CostPolicyUnavailable:
        return False


def public_catalog(*, checkout_enabled: bool) -> dict:
    from .cost_policy import CostPolicyUnavailable, service_prices
    try:
        prices = service_prices()
        authority = prices["cost_policy_snapshot"]
        cost_review = {k: authority[k] for k in ("version", "expires_at", "provenance")}
        availability = None
    except CostPolicyUnavailable as exc:
        prices = None
        cost_review = None
        availability = str(exc)
    available = bool(checkout_enabled and prices)
    return {
        "catalog_version": CATALOG_VERSION,
        "market": "IN",
        "checkout_enabled": available,
        "provider": "razorpay" if available else None,
        "availability_message": availability,
        "cost_review": cost_review,
        "service_prices": {k: prices[k] for k in ("search_credits_per_job", "apply_credits_per_job", "pricing_version")} if prices else None,
        "analysis_operation_units": {"job_match": 1, "interview_questions": 1, "market_analysis": 5, "resume_tailor": 2, "skill_roi": 0},
        "products": [p.public_dict(enabled_for_purchase=available) for p in PRODUCTS.values() if p.entitlement_kind == "credit_bundle"],
    }
