from dataclasses import dataclass
from typing import Dict


CATALOG_VERSION = "inr-2026-07-11-v1"


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
        }


# This catalog is intentionally narrow for the first domestic launch. Generic
# credits/top-ups are not offered unless Razorpay approves that model in writing.
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
}


def get_product(sku: str) -> CatalogProduct | None:
    return PRODUCTS.get(sku)


def public_catalog(*, checkout_enabled: bool) -> dict:
    return {
        "catalog_version": CATALOG_VERSION,
        "market": "IN",
        "checkout_enabled": checkout_enabled,
        # Do not present an inactive/unapproved processor as available.
        "provider": "razorpay" if checkout_enabled else None,
        "products": [
            product.public_dict(enabled_for_purchase=checkout_enabled)
            for product in PRODUCTS.values()
        ],
    }
