"""Compare normalized fixture invoices with signed local Razorpay settlements."""

from __future__ import annotations

import json
import sys

import pytest
from backend.app.models import PaymentOrder, PaymentTransaction
from backend.scripts.audit_purchase_invoice import (
    PurchaseInvoiceEvidence,
    PurchaseInvoiceMismatch,
    audit_purchase_invoice,
    main,
)
from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from test_razorpay_billing import _capture_payload, _post_event, _service_order
from test_settled_refund_recovery_postgres import (
    _captured_order,
)
from test_settled_refund_recovery_postgres import (
    client as client,
)
from test_settled_refund_recovery_postgres import (
    enabled_checkout as enabled_checkout,
)
from test_settled_refund_recovery_postgres import (
    pg_engine as pg_engine,
)
from test_settled_refund_recovery_postgres import (
    session_factory as session_factory,
)
from test_settled_refund_recovery_postgres import (
    unified as unified,
)


def normalized_invoice(**changes):
    return PurchaseInvoiceEvidence.model_validate({
        "schema_version": "hirewiz-purchase-invoice-v1", "mode": "test", "invoice_sha256": "f" * 64,
        "order_reference": "ord_fixture", "provider_order_id": "order_fixture",
        "provider_payment_id": "pay_fixture", "catalog_version": "inr-unified-2026-10-27-v1",
        "sku": "starter_pass", "currency": "INR", "subtotal_minor": 5000, "customer_tax_minor": 900,
        "invoice_total_minor": 5900, "gateway_captured_minor": 5900, "gateway_refunded_minor": 0,
    } | changes)


@pytest.mark.parametrize("changes", [
    {"customer_tax_minor": 0}, {"gateway_captured_minor": 5899}, {"gateway_refunded_minor": 5901},
    {"gateway_refunded_minor": False}, {"currency": "USD"}, {"name": "Never accept customer data"},
])
def test_rejects_ambiguous_or_inconsistent_invoice_records(changes):
    with pytest.raises(ValidationError):
        normalized_invoice(**changes)


def test_invoice_audit_matches_one_capture_and_later_refunds_without_database_writes(unified, session_factory):
    web, calls = unified
    reference, provider, half, remainder = _captured_order(web, session_factory)
    evidence = normalized_invoice(order_reference=reference, provider_order_id=provider["order"]["entity"]["id"],
                                  provider_payment_id=provider["payment"]["entity"]["id"])
    with session_factory() as db:
        report = audit_purchase_invoice(db, evidence)
        assert report == {
            "mode": "test", "invoice_sha256": evidence.invoice_sha256, "currency": "INR",
            "invoice_total_minor": 5900, "processed_refunds_minor": 0,
            "purchase_terms_and_payment_match": True, "customer_tax_match": None,
        }
        assert db.execute(text("SHOW transaction_read_only")).scalar_one() == "on"
        with pytest.raises(DBAPIError) as refused:
            db.execute(text("UPDATE payment_orders SET gross_amount_minor=0 WHERE false"))
        assert refused.value.orig.sqlstate == "25006"

    for event_id, refund in (("evt_invoice_partial", half), ("evt_invoice_remainder", remainder)):
        assert _post_event(web, event_id, refund).status_code == 200
    for changes, reason in (
        ({}, "gateway_refunds_do_not_match_ledger"),
        ({"mode": "live"}, "order_not_found_in_selected_mode"),
        ({"sku": "growth_pass"}, "invoice_does_not_match_purchase_terms"),
        ({"provider_payment_id": "pay_different"}, "gateway_capture_does_not_match_ledger"),
    ):
        with session_factory() as db, pytest.raises(PurchaseInvoiceMismatch, match=reason):
            audit_purchase_invoice(db, PurchaseInvoiceEvidence.model_validate(evidence.model_dump() | changes))
    with session_factory() as db:
        row = db.query(PaymentOrder).filter_by(public_id=reference).one()
        assert row.gross_amount_minor == 5900 and row.refunded_amount_minor == 5900
        row.customer_tax_amount_minor = 900
        db.commit()
    fully_refunded = PurchaseInvoiceEvidence.model_validate(evidence.model_dump() | {"gateway_refunded_minor": 5900})
    with session_factory() as db:
        report = audit_purchase_invoice(db, fully_refunded)
        assert report["invoice_total_minor"] == report["processed_refunds_minor"] == 5900
        assert report["customer_tax_match"] is True
    assert calls == [(reference, 5900)]


def test_historical_invoice_uses_the_original_order_even_when_current_packs_differ(
    client, enabled_checkout, session_factory, monkeypatch, tmp_path, capsys,
):
    original = _service_order(client)
    with session_factory() as db:
        row = db.query(PaymentOrder).filter_by(public_id=original["order_id"]).one()
        capture = _capture_payload(row, event="order.paid")
    assert _post_event(client, "evt_legacy_invoice_capture", capture).status_code == 200
    with session_factory() as db:
        row = db.query(PaymentOrder).filter_by(public_id=original["order_id"]).one()
        payment = db.query(PaymentTransaction).filter_by(order_id=row.id).one()
        evidence = normalized_invoice(
            order_reference=row.public_id, sku=row.sku, catalog_version=row.catalog_version,
            provider_order_id=row.provider_order_id, provider_payment_id=payment.provider_payment_id,
            subtotal_minor=row.gross_amount_minor, customer_tax_minor=0,
            invoice_total_minor=row.gross_amount_minor, gateway_captured_minor=payment.gross_amount_minor,
        )
        schema = db.scalar(text("SELECT current_schema()"))
        url = db.get_bind().url.update_query_dict({"options": f"-csearch_path={schema}"})
    record = tmp_path / "invoice-evidence.json"
    record.write_text(evidence.model_dump_json())
    monkeypatch.setenv("DATABASE_URL", url.render_as_string(hide_password=False))
    monkeypatch.setattr(sys, "argv", ["audit_purchase_invoice", str(record)])
    main()
    output = capsys.readouterr().out
    assert evidence.order_reference not in output and evidence.provider_payment_id not in output
    assert json.loads(output)["invoice_total_minor"] == original["amount_minor"]
