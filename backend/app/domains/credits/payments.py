"""Immutable unified purchase terms shared by checkout and verified settlement."""
from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy.orm import Session

from ...billing.catalog import CatalogProduct
from ...models import PaymentOrder
from .models import CreditPurchaseQuote
from .pricing import CreditContractError
from .schemas import PurchaseQuoteView
from .service import fulfill_purchase_quote, quote_response, revoke_origin


def order_quote(order: PaymentOrder, *, now: datetime | None = None) -> PurchaseQuoteView:
    snapshot = (order.cost_policy_snapshot or {}).get("unified_purchase_quote")
    if order.entitlement_kind != "credit_pass" or not isinstance(snapshot, dict):
        raise CreditContractError("This original purchase requires billing reconciliation.")
    try:
        view = PurchaseQuoteView.model_validate(snapshot | {"server_time": now or datetime.now(UTC)})
    except ValueError as exc:
        raise CreditContractError("This original purchase requires billing reconciliation.") from exc
    if (view.amount_minor, view.currency, view.sku, view.catalog_version, view.credits) != (
        order.gross_amount_minor, order.currency, order.sku, order.catalog_version, order.entitlement_quantity
    ):
        raise CreditContractError("This original purchase has inconsistent immutable terms.")
    return view


def provider_product(quote: PurchaseQuoteView) -> CatalogProduct:
    return CatalogProduct(
        sku=quote.sku, name=quote.name, description=f"{quote.credits} HireWiz credits; 30-day prepaid pass.",
        amount_minor=quote.amount_minor, amount_display=f"₹{quote.amount_minor / 100:.2f}", currency="INR",
        billing_type="one_time", duration_days=30, auto_renews=False,
        entitlement_kind="credit_pass", entitlement_quantity=quote.credits,
    )


def fulfill_order(db: Session, order: PaymentOrder, *, now: datetime):
    view = order_quote(order, now=now)
    quote = db.query(CreditPurchaseQuote).filter_by(id=view.quote_id, user_id=order.user_id).one_or_none()
    if quote is None or quote_response(quote, now=now) != view:
        raise CreditContractError("The original accepted quote needs reconciliation.")
    return fulfill_purchase_quote(db, user_id=order.user_id, quote_id=view.quote_id,
                                  order_id=order.public_id, captured_at=now, now=datetime.now(UTC))


def refund_order(db: Session, order: PaymentOrder, *, now: datetime) -> None:
    if order.user_id is None:
        return
    view = order_quote(order, now=now)
    refunded = int(order.refunded_amount_minor or 0)
    target = min(view.credits, (refunded * view.credits + view.amount_minor - 1) // view.amount_minor)
    revoke_origin(db, user_id=order.user_id, origin_type="payment_order", origin_id=order.public_id,
                  target_credits=target, now=now)
