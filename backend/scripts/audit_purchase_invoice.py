"""Compare an issued invoice and reviewed gateway facts with one retained purchase."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Annotated, Literal

from backend.app.domains.credits.schemas import Closed
from fastapi import HTTPException
from pydantic import Field, ValidationError, model_validator
from sqlalchemy import create_engine, make_url, select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

MAX_INPUT_BYTES = 16_384
Minor = Annotated[int, Field(strict=True, ge=0, le=2_147_483_647)]


class PurchaseInvoiceEvidence(Closed):
    schema_version: Literal["hirewiz-purchase-invoice-v1"]
    mode: Literal["test", "live"]
    invoice_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    order_reference: str = Field(pattern=r"^ord_[A-Za-z0-9_-]{1,60}$")
    provider_order_id: str = Field(pattern=r"^order_[A-Za-z0-9_-]{1,114}$")
    provider_payment_id: str = Field(pattern=r"^pay_[A-Za-z0-9_-]{1,116}$")
    catalog_version: str = Field(min_length=1, max_length=64)
    sku: str = Field(min_length=1, max_length=64)
    currency: Literal["INR"]
    subtotal_minor: Minor
    customer_tax_minor: Minor
    invoice_total_minor: Annotated[Minor, Field(gt=0)]
    gateway_captured_minor: Minor
    gateway_refunded_minor: Minor

    @model_validator(mode="after")
    def amounts_are_consistent(self):
        if self.subtotal_minor + self.customer_tax_minor != self.invoice_total_minor:
            raise ValueError("Invoice subtotal and customer tax must sum to the invoice total")
        if self.gateway_captured_minor != self.invoice_total_minor or self.gateway_refunded_minor > self.gateway_captured_minor:
            raise ValueError("Gateway capture and refunds must agree with the original invoice total")
        return self


class PurchaseInvoiceMismatch(ValueError):
    """An incomplete or inconsistent record needs review."""


def audit_purchase_invoice(db: Session, evidence: PurchaseInvoiceEvidence) -> dict:
    if db.in_transaction() or db.get_bind().dialect.name != "postgresql":
        raise PurchaseInvoiceMismatch("requires_fresh_postgres_session")
    from backend.app.billing.purchase_quote import order_quote as legacy_order_quote
    from backend.app.domains.credits.payments import order_quote as pass_order_quote
    from backend.app.domains.credits.pricing import CreditContractError
    from backend.app.models import (
        EntitlementLedger,
        PaymentOrder,
        PaymentRefund,
        PaymentTransaction,
    )

    db.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"))
    db.execute(text("SET LOCAL statement_timeout = '5000ms'"))
    order = db.execute(select(
        PaymentOrder.id, PaymentOrder.user_id, PaymentOrder.provider, PaymentOrder.provider_order_id,
        PaymentOrder.catalog_version, PaymentOrder.sku, PaymentOrder.currency, PaymentOrder.paid_at,
        PaymentOrder.gross_amount_minor, PaymentOrder.refunded_amount_minor, PaymentOrder.customer_tax_amount_minor,
        PaymentOrder.entitlement_kind, PaymentOrder.entitlement_quantity, PaymentOrder.cost_policy_snapshot,
    ).where(
        PaymentOrder.public_id == evidence.order_reference, PaymentOrder.provider_mode == evidence.mode,
    )).one_or_none()
    if order is None:
        raise PurchaseInvoiceMismatch("order_not_found_in_selected_mode")
    if order.provider != "razorpay" or order.provider_order_id != evidence.provider_order_id or order.paid_at is None:
        raise PurchaseInvoiceMismatch("original_provider_order_unverified")

    try:
        quote = pass_order_quote(order) if order.entitlement_kind == "credit_pass" else legacy_order_quote(order)
    except (CreditContractError, HTTPException, ValidationError) as exc:
        raise PurchaseInvoiceMismatch("retained_quote_invalid") from exc
    if (quote.amount_minor, order.sku, quote.catalog_version, quote.currency) != (
        evidence.invoice_total_minor, evidence.sku, evidence.catalog_version, evidence.currency,
    ):
        raise PurchaseInvoiceMismatch("invoice_does_not_match_purchase_terms")

    captured = db.execute(select(
        PaymentTransaction.id, PaymentTransaction.provider, PaymentTransaction.provider_payment_id,
        PaymentTransaction.gross_amount_minor, PaymentTransaction.currency, PaymentTransaction.refunded_amount_minor,
    ).where(PaymentTransaction.order_id == order.id, PaymentTransaction.captured_at.is_not(None)).limit(2)).all()
    if len(captured) != 1:
        raise PurchaseInvoiceMismatch("expected_one_recorded_capture")
    payment = captured[0]
    if (payment.provider, payment.provider_payment_id, payment.gross_amount_minor, payment.currency) != (
        order.provider, evidence.provider_payment_id, evidence.gateway_captured_minor, evidence.currency,
    ):
        raise PurchaseInvoiceMismatch("gateway_capture_does_not_match_ledger")

    recorded_refunds = db.execute(select(
        PaymentRefund.transaction_id, PaymentRefund.provider, PaymentRefund.amount_minor,
        PaymentRefund.currency, PaymentRefund.processed_at,
    ).where(PaymentRefund.order_id == order.id, PaymentRefund.status == "processed").limit(101)).all()
    if len(recorded_refunds) > 100 or any(
        row.transaction_id != payment.id or row.provider != order.provider or row.currency != evidence.currency
        or row.processed_at is None or row.amount_minor <= 0 for row in recorded_refunds
    ):
        raise PurchaseInvoiceMismatch("refund_records_require_review")
    refund_total = sum(row.amount_minor for row in recorded_refunds)
    if not refund_total == order.refunded_amount_minor == payment.refunded_amount_minor == evidence.gateway_refunded_minor:
        raise PurchaseInvoiceMismatch("gateway_refunds_do_not_match_ledger")

    entitlement = db.execute(select(
        EntitlementLedger.source_provider, EntitlementLedger.sku, EntitlementLedger.entitlement_kind,
        EntitlementLedger.quantity, EntitlementLedger.user_id,
    ).where(EntitlementLedger.source_order_id == order.id)).one_or_none()
    if entitlement is None or tuple(entitlement) != (
        order.provider, order.sku, order.entitlement_kind, order.entitlement_quantity, order.user_id,
    ):
        raise PurchaseInvoiceMismatch("original_entitlement_requires_review")

    tax_recorded = order.customer_tax_amount_minor is not None
    if tax_recorded and order.customer_tax_amount_minor != evidence.customer_tax_minor:
        raise PurchaseInvoiceMismatch("customer_tax_does_not_match_ledger")
    return {
        "mode": evidence.mode,
        "invoice_sha256": evidence.invoice_sha256,
        "currency": evidence.currency,
        "invoice_total_minor": evidence.invoice_total_minor,
        "processed_refunds_minor": refund_total,
        "purchase_terms_and_payment_match": True,
        "customer_tax_match": True if tax_recorded else None,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("evidence_file", type=Path)
    args = parser.parse_args()
    database_url = (os.getenv("DATABASE_URL") or "").strip()
    if not database_url:
        parser.error("Set DATABASE_URL for the authorized read-only PostgreSQL role")
    try:
        with args.evidence_file.open("rb") as stream:
            raw = stream.read(MAX_INPUT_BYTES + 1)
        if len(raw) > MAX_INPUT_BYTES:
            parser.error("Invoice evidence exceeds the input limit")
        evidence = PurchaseInvoiceEvidence.model_validate_json(raw)
    except (OSError, ValidationError):
        parser.error("Provide one valid, normalized invoice evidence record")
    try:
        url = make_url(database_url)
        if url.drivername in {"postgres", "postgresql"}:
            url = url.set(drivername="postgresql+psycopg")
        if url.drivername != "postgresql+psycopg":
            parser.error("Use PostgreSQL with the configured psycopg driver")
        engine = create_engine(url, hide_parameters=True, connect_args={"connect_timeout": 5})
        try:
            with Session(engine) as db:
                result = audit_purchase_invoice(db, evidence)
        finally:
            engine.dispose()
    except PurchaseInvoiceMismatch as exc:
        raise SystemExit(f"Invoice reconciliation needs review ({exc}).") from None
    except SQLAlchemyError:
        raise SystemExit("Could not read the invoice's payment and entitlement records.") from None
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
