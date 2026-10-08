from dataclasses import dataclass
from typing import Dict


CATALOG_VERSION = "inr-2026-10-08-v2"


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
        }


# Service credits are closed-loop HireWiz entitlements fulfilled through the same
# approved checkout. They cannot be transferred or redeemed as money.
PRODUCTS: Dict[str, CatalogProduct] = {
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
    if not checkout_enabled:
        return False
    return True


def public_catalog(*, checkout_enabled: bool) -> dict:
    return {
        "catalog_version": CATALOG_VERSION,
        "market": "IN",
        "checkout_enabled": checkout_enabled,
        # Do not present an inactive/unapproved processor as available.
        "provider": "razorpay" if checkout_enabled else None,
        "products": [
            product.public_dict(enabled_for_purchase=product_purchase_enabled(product, checkout_enabled=checkout_enabled))
            for product in PRODUCTS.values()
        ],
    }
