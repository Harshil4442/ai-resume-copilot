import hashlib
import json
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..domains.notifications import enqueue_notification
from ..models import (
    EntitlementLedger,
    PaymentEvent,
    PaymentOrder,
    PaymentRefund,
    PaymentTransaction,
    User,
)
PROVIDER = "razorpay"
log = logging.getLogger("hirewiz.billing.service")
CAPTURE_EVENTS = {"payment.captured", "order.paid"}
FAILED_EVENTS = {"payment.failed"}
REFUND_EVENTS = {"refund.processed"}
IGNORED_REFUND_EVENTS = {"refund.failed", "payment.refunded"}
RETRYABLE_EVENT_STATUSES = {"retryable_unknown_order", "retryable_unknown_payment"}


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def as_aware(value: datetime | None) -> datetime | None:
    if value is None or value.tzinfo is not None:
        return value
    return value.replace(tzinfo=timezone.utc)


class WebhookValidationError(ValueError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class WebhookResult:
    status: str
    event_type: str
    order_public_id: str | None = None


def parse_webhook(raw_body: bytes) -> dict[str, Any]:
    try:
        payload = json.loads(raw_body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise WebhookValidationError("invalid_json") from exc
    if not isinstance(payload, dict):
        raise WebhookValidationError("invalid_payload")
    event_type = payload.get("event")
    if not isinstance(event_type, str) or not event_type or len(event_type) > 80:
        raise WebhookValidationError("invalid_event_type")
    return payload


def _provider_event_time(payload: dict[str, Any]) -> datetime | None:
    value = payload.get("created_at")
    if type(value) is not int or value <= 0:
        return None
    try:
        parsed = datetime.fromtimestamp(value, tz=timezone.utc)
    except (OverflowError, OSError, ValueError):
        return None
    # Reject implausible timestamps rather than corrupting accounting dates.
    if parsed.year < 2010 or parsed > utcnow() + timedelta(days=1):
        return None
    return parsed


def _entity(payload: dict[str, Any], name: str) -> dict[str, Any] | None:
    root = payload.get("payload")
    if not isinstance(root, dict):
        return None
    wrapper = root.get(name)
    if not isinstance(wrapper, dict):
        return None
    entity = wrapper.get("entity")
    return entity if isinstance(entity, dict) else None


def _required_string(entity: dict[str, Any], key: str, *, prefix: str | None = None) -> str:
    value = entity.get(key)
    if not isinstance(value, str) or not value or len(value) > 160:
        raise WebhookValidationError(f"invalid_{key}")
    if prefix and not value.startswith(prefix):
        raise WebhookValidationError(f"invalid_{key}")
    return value


def _required_minor(entity: dict[str, Any], key: str, *, positive: bool = False) -> int:
    value = entity.get(key)
    if type(value) is not int:  # bool must not pass as an integer amount
        raise WebhookValidationError(f"invalid_{key}")
    if (positive and value <= 0) or (not positive and value < 0):
        raise WebhookValidationError(f"invalid_{key}")
    return value


def _optional_minor(entity: dict[str, Any], key: str) -> int | None:
    value = entity.get(key)
    if value is None:
        return None
    if type(value) is not int or value < 0:
        raise WebhookValidationError(f"invalid_{key}")
    return value


def _lock_order_by_provider_id(db: Session, provider_order_id: str) -> PaymentOrder | None:
    return (
        db.query(PaymentOrder)
        .filter(
            PaymentOrder.provider == PROVIDER,
            PaymentOrder.provider_order_id == provider_order_id,
        )
        .with_for_update()
        .first()
    )


def _receipt_for(order: PaymentOrder) -> str:
    return f"hw_{order.public_id.replace('-', '')}"


def _validate_order_notes(notes: Any, order: PaymentOrder) -> None:
    if notes in (None, []):
        return
    if not isinstance(notes, dict):
        raise WebhookValidationError("invalid_order_notes")
    expected = {
        "hirewiz_order_id": order.public_id,
        "sku": order.sku,
        "billing_country": order.billing_country,
    }
    if order.user_id is not None:
        expected["hirewiz_user_id"] = str(order.user_id)
    for key, expected_value in expected.items():
        if key in notes and notes[key] != expected_value:
            raise WebhookValidationError("order_ownership_mismatch")


def _validate_payment(
    payment: dict[str, Any], order: PaymentOrder, *, require_captured: bool
) -> tuple[str, int | None, int | None, str | None, bool | None]:
    if payment.get("entity") != "payment":
        raise WebhookValidationError("invalid_payment_entity")
    payment_id = _required_string(payment, "id", prefix="pay_")
    if _required_string(payment, "order_id", prefix="order_") != order.provider_order_id:
        raise WebhookValidationError("payment_order_mismatch")
    if _required_minor(payment, "amount", positive=True) != order.gross_amount_minor:
        raise WebhookValidationError("amount_mismatch")
    if _required_string(payment, "currency").upper() != order.currency:
        raise WebhookValidationError("currency_mismatch")
    _validate_order_notes(payment.get("notes"), order)

    if require_captured:
        if payment.get("status") != "captured" or payment.get("captured") is not True:
            raise WebhookValidationError("payment_not_captured")
    elif payment.get("status") != "failed":
        raise WebhookValidationError("payment_not_failed")

    fee = _optional_minor(payment, "fee")
    tax = _optional_minor(payment, "tax")
    if fee is not None and fee > order.gross_amount_minor:
        raise WebhookValidationError("invalid_fee")
    if tax is not None and (fee is None or tax > fee):
        # Razorpay documents `fee` as inclusive of its GST and `tax` as the
        # GST component of that provider fee.
        raise WebhookValidationError("invalid_provider_fee_tax")
    method = payment.get("method")
    if method is not None and (not isinstance(method, str) or len(method) > 32):
        raise WebhookValidationError("invalid_payment_method")
    instrument_international = payment.get("international")
    if instrument_international is not None and type(instrument_international) is not bool:
        raise WebhookValidationError("invalid_international_flag")
    return payment_id, fee, tax, method, instrument_international


def _validate_provider_order(entity: dict[str, Any], order: PaymentOrder) -> None:
    if entity.get("entity") != "order":
        raise WebhookValidationError("invalid_order_entity")
    if _required_string(entity, "id", prefix="order_") != order.provider_order_id:
        raise WebhookValidationError("provider_order_mismatch")
    if _required_minor(entity, "amount", positive=True) != order.gross_amount_minor:
        raise WebhookValidationError("amount_mismatch")
    if _required_minor(entity, "amount_paid") != order.gross_amount_minor:
        raise WebhookValidationError("amount_paid_mismatch")
    if _required_minor(entity, "amount_due") != 0:
        raise WebhookValidationError("amount_due_mismatch")
    if _required_string(entity, "currency").upper() != order.currency:
        raise WebhookValidationError("currency_mismatch")
    if entity.get("status") != "paid":
        raise WebhookValidationError("order_not_paid")
    if entity.get("receipt") != _receipt_for(order):
        raise WebhookValidationError("receipt_mismatch")
    if entity.get("partial_payment") not in (False, None):
        raise WebhookValidationError("partial_payment_not_allowed")
    _validate_order_notes(entity.get("notes"), order)


def _payment_transaction(
    db: Session,
    *,
    order: PaymentOrder,
    payment_id: str,
    status: str,
    fee: int | None,
    tax: int | None,
    payment_method: str | None = None,
    instrument_international: bool | None = None,
) -> PaymentTransaction:
    transaction = (
        db.query(PaymentTransaction)
        .filter(
            PaymentTransaction.provider == PROVIDER,
            PaymentTransaction.provider_payment_id == payment_id,
        )
        .with_for_update()
        .first()
    )
    if transaction and transaction.order_id != order.id:
        raise WebhookValidationError("payment_ownership_mismatch")
    if transaction is None:
        transaction = PaymentTransaction(
            order_id=order.id,
            provider=PROVIDER,
            provider_payment_id=payment_id,
            entity_type="payment",
            status=status,
            gross_amount_minor=order.gross_amount_minor,
            currency=order.currency,
        )
        db.add(transaction)
    # Refund is terminal. A delayed payment.captured/order.paid event must not
    # downgrade a refunded transaction, even though it also cannot re-grant the
    # already-refunded order.
    if not (
        status == "captured"
        and transaction.status in {"partially_refunded", "refunded"}
    ):
        transaction.status = status
    transaction.provider_fee_amount_minor = fee
    transaction.provider_fee_tax_minor = tax
    transaction.estimated_net_amount_minor = (
        order.gross_amount_minor - fee if fee is not None else None
    )
    if payment_method:
        transaction.payment_method = payment_method
    if instrument_international is not None:
        transaction.instrument_international = instrument_international
    return transaction


def _grant_entitlement(db: Session, order: PaymentOrder, now: datetime) -> bool:
    existing = (
        db.query(EntitlementLedger)
        .filter(EntitlementLedger.source_order_id == order.id)
        .with_for_update()
        .first()
    )
    if existing:
        return existing.status == "active"
    if order.user_id is None:
        order.status = "paid_unfulfilled"
        log.error("Paid order %s has no linked customer and requires reconciliation", order.public_id)
        return False

    entitlement_kind = order.entitlement_kind
    entitlement_quantity = order.entitlement_quantity
    if (
        entitlement_kind not in {"premium_access", "analysis_units"}
        or type(entitlement_quantity) is not int
        or entitlement_quantity <= 0
    ):
        raise WebhookValidationError("invalid_entitlement_snapshot")
    user = db.query(User).filter(User.id == order.user_id).with_for_update().first()
    if user is None:
        order.status = "paid_unfulfilled"
        log.error("Paid order %s references a missing customer and requires reconciliation", order.public_id)
        return False

    expires_at = None
    if entitlement_kind == "premium_access":
        current_expiry = as_aware(user.premium_until)
        starts_at = current_expiry if current_expiry and current_expiry > now else now
        expires_at = starts_at + timedelta(days=entitlement_quantity)
        user.tier = "premium"
        user.premium_until = expires_at
    elif entitlement_kind == "analysis_units":
        starts_at = now
        user.ai_credits = int(user.ai_credits or 0) + entitlement_quantity

    db.add(
        EntitlementLedger(
            user_id=user.id,
            source_order_id=order.id,
            source_provider=order.provider,
            sku=order.sku,
            entitlement_kind=entitlement_kind,
            quantity=entitlement_quantity,
            status="active",
            starts_at=starts_at,
            expires_at=expires_at,
            granted_at=now,
        )
    )
    amount = order.gross_amount_minor / 100
    enqueue_notification(
        db,
        user_id=int(user.id),
        notification_type="payment_receipt",
        recipient=str(user.email),
        payload={
            "reference": order.public_id,
            "amount_display": f"{order.currency} {amount:,.2f}",
            "sku": order.sku,
        },
        idempotency_key=f"payment:{order.public_id}:receipt",
    )
    return True


def _revoke_entitlement(db: Session, order: PaymentOrder, now: datetime) -> None:
    entitlement = (
        db.query(EntitlementLedger)
        .filter(EntitlementLedger.source_order_id == order.id)
        .with_for_update()
        .first()
    )
    if entitlement is None:
        return
    if entitlement.status != "active":
        if entitlement.status in {"ended_by_user", "ended_account_deleted"}:
            entitlement.status = "refunded"
            entitlement.revoked_at = entitlement.revoked_at or now
        return
    entitlement.status = "refunded"
    entitlement.revoked_at = now
    if entitlement.user_id is None:
        return

    user = db.query(User).filter(User.id == entitlement.user_id).with_for_update().first()
    if user is None:
        return
    if entitlement.entitlement_kind == "analysis_units":
        user.ai_credits = max(0, int(user.ai_credits or 0) - entitlement.quantity)
        return

    other_entitlements = (
        db.query(EntitlementLedger)
        .filter(
            EntitlementLedger.user_id == user.id,
            EntitlementLedger.id != entitlement.id,
            EntitlementLedger.entitlement_kind == "premium_access",
            EntitlementLedger.status == "active",
        )
        .all()
    )
    future_expiries = [
        as_aware(item.expires_at)
        for item in other_entitlements
        if as_aware(item.expires_at) and as_aware(item.expires_at) > now
    ]
    user.premium_until = max(future_expiries) if future_expiries else None
    user.tier = "premium" if future_expiries else "free"


def _process_capture(
    db: Session,
    payload: dict[str, Any],
    event: PaymentEvent,
    event_type: str,
    provider_mode: str,
) -> str | None:
    payment = _entity(payload, "payment")
    if payment is None:
        raise WebhookValidationError("missing_payment_entity")
    provider_order_id = _required_string(payment, "order_id", prefix="order_")
    order = _lock_order_by_provider_id(db, provider_order_id)
    if order is None:
        return None
    if order.provider_mode != provider_mode:
        raise WebhookValidationError("provider_mode_mismatch")
    event.order_id = order.id

    payment_id, fee, tax, payment_method, instrument_international = _validate_payment(
        payment, order, require_captured=True
    )
    other_captured_payment = (
        db.query(PaymentTransaction)
        .filter(
            PaymentTransaction.order_id == order.id,
            PaymentTransaction.provider == PROVIDER,
            PaymentTransaction.provider_payment_id != payment_id,
            PaymentTransaction.status.in_(
                {"captured", "partially_refunded", "refunded"}
            ),
        )
        .with_for_update()
        .first()
    )
    if other_captured_payment is not None:
        # A non-partial Razorpay order has one successful payment. Retain the
        # unexpected signed event for reconciliation, but never create a second
        # accounting transaction or entitlement for it.
        raise WebhookValidationError("multiple_captured_payments")
    if event_type == "order.paid":
        provider_order = _entity(payload, "order")
        if provider_order is None:
            raise WebhookValidationError("missing_order_entity")
        _validate_provider_order(provider_order, order)

    now = as_aware(event.provider_created_at) or utcnow()
    transaction = _payment_transaction(
        db,
        order=order,
        payment_id=payment_id,
        status="captured",
        fee=fee,
        tax=tax,
        payment_method=payment_method,
        instrument_international=instrument_international,
    )
    if instrument_international is True:
        log.warning(
            "Order %s used an international payment instrument in the IN lane; review billing-country evidence",
            order.public_id,
        )
    captured_at = as_aware(transaction.captured_at)
    if captured_at is None or now < captured_at:
        transaction.captured_at = now
    order.provider_fee_tax_minor = tax
    order.provider_fee_amount_minor = fee
    order.estimated_net_amount_minor = transaction.estimated_net_amount_minor
    paid_at = as_aware(order.paid_at)
    if paid_at is None or now < paid_at:
        order.paid_at = now
    order.active_attempt_key = None

    if order.status == "partially_refunded":
        # A processed partial refund can precede the capture notification. The
        # delayed capture still fulfils the remaining paid order exactly once.
        _grant_entitlement(db, order, now)
    elif order.status not in {"paid", "refunded", "paid_unfulfilled"}:
        order.status = "paid"
        _grant_entitlement(db, order, now)
    return order.public_id


def _process_failure(
    db: Session,
    payload: dict[str, Any],
    event: PaymentEvent,
    provider_mode: str,
) -> str | None:
    payment = _entity(payload, "payment")
    if payment is None:
        raise WebhookValidationError("missing_payment_entity")
    provider_order_id = _required_string(payment, "order_id", prefix="order_")
    order = _lock_order_by_provider_id(db, provider_order_id)
    if order is None:
        return None
    if order.provider_mode != provider_mode:
        raise WebhookValidationError("provider_mode_mismatch")
    event.order_id = order.id
    payment_id, fee, tax, payment_method, instrument_international = _validate_payment(
        payment, order, require_captured=False
    )
    existing_transaction = (
        db.query(PaymentTransaction)
        .filter(
            PaymentTransaction.provider == PROVIDER,
            PaymentTransaction.provider_payment_id == payment_id,
        )
        .with_for_update()
        .first()
    )
    if existing_transaction and existing_transaction.status in {
        "captured",
        "partially_refunded",
        "refunded",
    }:
        raise WebhookValidationError("invalid_payment_transition")
    transaction = _payment_transaction(
        db,
        order=order,
        payment_id=payment_id,
        status="failed",
        fee=fee,
        tax=tax,
        payment_method=payment_method,
        instrument_international=instrument_international,
    )
    transaction.failed_at = transaction.failed_at or as_aware(event.provider_created_at) or utcnow()
    if order.status in {"initializing", "created", "client_confirmed", "payment_failed"}:
        order.status = "payment_failed"
    return order.public_id


def _validate_refund_payment(payment: dict[str, Any], order: PaymentOrder) -> str:
    if payment.get("entity") != "payment":
        raise WebhookValidationError("invalid_payment_entity")
    payment_id = _required_string(payment, "id", prefix="pay_")
    if _required_string(payment, "order_id", prefix="order_") != order.provider_order_id:
        raise WebhookValidationError("payment_order_mismatch")
    if _required_minor(payment, "amount", positive=True) != order.gross_amount_minor:
        raise WebhookValidationError("amount_mismatch")
    if _required_string(payment, "currency").upper() != order.currency:
        raise WebhookValidationError("currency_mismatch")
    if payment.get("captured") is not True:
        raise WebhookValidationError("payment_not_captured")
    _validate_order_notes(payment.get("notes"), order)
    return payment_id


def _process_refund(
    db: Session,
    payload: dict[str, Any],
    event: PaymentEvent,
    provider_mode: str,
) -> str | None:
    refund = _entity(payload, "refund")
    if refund is None or refund.get("entity") != "refund":
        raise WebhookValidationError("missing_refund_entity")
    refund_id = _required_string(refund, "id", prefix="rfnd_")
    payment_id = _required_string(refund, "payment_id", prefix="pay_")
    refund_amount = _required_minor(refund, "amount", positive=True)
    refund_currency = _required_string(refund, "currency").upper()
    if refund.get("status") != "processed":
        raise WebhookValidationError("refund_not_processed")

    payment = _entity(payload, "payment")
    order = None
    if payment is not None:
        # Keep lock ordering consistent with capture/failure: order first,
        # transaction second. This prevents capture/refund deadlocks.
        provider_order_id = _required_string(payment, "order_id", prefix="order_")
        order = _lock_order_by_provider_id(db, provider_order_id)
    else:
        transaction_hint = (
            db.query(PaymentTransaction)
            .filter(
                PaymentTransaction.provider == PROVIDER,
                PaymentTransaction.provider_payment_id == payment_id,
            )
            .first()
        )
        if transaction_hint is not None:
            order = (
                db.query(PaymentOrder)
                .filter(PaymentOrder.id == transaction_hint.order_id)
                .with_for_update()
                .first()
            )
    if order is None:
        return None
    transaction = (
        db.query(PaymentTransaction)
        .filter(
            PaymentTransaction.provider == PROVIDER,
            PaymentTransaction.provider_payment_id == payment_id,
        )
        .with_for_update()
        .first()
    )
    if transaction is not None and transaction.order_id != order.id:
        raise WebhookValidationError("payment_ownership_mismatch")
    if order.provider_mode != provider_mode:
        raise WebhookValidationError("provider_mode_mismatch")
    event.order_id = order.id

    if refund_currency != order.currency:
        raise WebhookValidationError("currency_mismatch")
    if refund_amount > order.gross_amount_minor:
        raise WebhookValidationError("refund_amount_mismatch")
    if payment is not None:
        verified_payment_id = _validate_refund_payment(payment, order)
        if verified_payment_id != payment_id:
            raise WebhookValidationError("refund_payment_mismatch")

    if transaction is None:
        transaction = _payment_transaction(
            db,
            order=order,
            payment_id=payment_id,
            status="captured",
            fee=None,
            tax=None,
        )
        transaction.captured_at = order.paid_at
        db.flush()

    existing_refund = (
        db.query(PaymentRefund)
        .filter(
            PaymentRefund.provider == PROVIDER,
            PaymentRefund.provider_refund_id == refund_id,
        )
        .with_for_update()
        .first()
    )
    if existing_refund:
        if existing_refund.order_id != order.id:
            raise WebhookValidationError("refund_ownership_mismatch")
        return order.public_id

    new_total = int(order.refunded_amount_minor or 0) + refund_amount
    if new_total > order.gross_amount_minor:
        raise WebhookValidationError("refund_total_mismatch")
    now = as_aware(event.provider_created_at) or utcnow()
    db.add(
        PaymentRefund(
            order_id=order.id,
            transaction_id=transaction.id,
            provider=PROVIDER,
            provider_refund_id=refund_id,
            amount_minor=refund_amount,
            currency=order.currency,
            status="processed",
            processed_at=now,
        )
    )
    transaction.refunded_amount_minor = int(transaction.refunded_amount_minor or 0) + refund_amount
    transaction.status = "refunded" if new_total == order.gross_amount_minor else "partially_refunded"
    order.refunded_amount_minor = new_total
    order.active_attempt_key = None
    if new_total == order.gross_amount_minor:
        order.status = "refunded"
        order.refunded_at = now
        _revoke_entitlement(db, order, now)
    else:
        order.status = "partially_refunded"
        order.paid_at = order.paid_at or now
        # The signed refund payload includes a captured payment entity. Grant
        # access now; a later capture event will see the unique ledger row.
        _grant_entitlement(db, order, now)
    return order.public_id


def process_razorpay_webhook(
    db: Session,
    *,
    raw_body: bytes,
    provider_event_id: str,
    provider_mode: str,
) -> WebhookResult:
    if not provider_event_id or len(provider_event_id) > 160:
        raise WebhookValidationError("invalid_event_id")
    payload = parse_webhook(raw_body)
    event_type = payload["event"]
    payload_sha256 = hashlib.sha256(raw_body).hexdigest()
    event = (
        db.query(PaymentEvent)
        .filter(
            PaymentEvent.provider == PROVIDER,
            PaymentEvent.provider_event_id == provider_event_id,
        )
        .with_for_update()
        .first()
    )
    if event is not None:
        if event.payload_sha256 != payload_sha256 or event.event_type != event_type:
            raise WebhookValidationError("event_replay_mismatch")
        if event.processing_status not in RETRYABLE_EVENT_STATUSES:
            return WebhookResult(status="duplicate", event_type=event_type)
        event.processing_status = "received"
        event.error_code = None
        event.processed_at = None
        event.provider_created_at = event.provider_created_at or _provider_event_time(payload)
    else:
        event = PaymentEvent(
            provider=PROVIDER,
            provider_event_id=provider_event_id,
            event_type=event_type,
            payload_sha256=payload_sha256,
            processing_status="received",
            provider_created_at=_provider_event_time(payload),
        )
        db.add(event)
        try:
            db.flush()
        except IntegrityError:
            # A concurrent delivery claimed the event. It will either complete
            # or leave a retryable state for the provider's next attempt.
            db.rollback()
            concurrent = (
                db.query(PaymentEvent)
                .filter(
                    PaymentEvent.provider == PROVIDER,
                    PaymentEvent.provider_event_id == provider_event_id,
                )
                .first()
            )
            if concurrent and (
                concurrent.payload_sha256 != payload_sha256
                or concurrent.event_type != event_type
            ):
                raise WebhookValidationError("event_replay_mismatch")
            return WebhookResult(status="duplicate", event_type=event_type)

    try:
        if event_type in CAPTURE_EVENTS:
            order_public_id = _process_capture(
                db, payload, event, event_type, provider_mode
            )
            processing_status = "processed" if order_public_id else "retryable_unknown_order"
        elif event_type in FAILED_EVENTS:
            order_public_id = _process_failure(db, payload, event, provider_mode)
            processing_status = "processed" if order_public_id else "retryable_unknown_order"
        elif event_type in REFUND_EVENTS:
            order_public_id = _process_refund(db, payload, event, provider_mode)
            processing_status = "processed" if order_public_id else "retryable_unknown_payment"
        elif event_type in IGNORED_REFUND_EVENTS:
            # refund.processed is authoritative for refund amounts. These events
            # are retained for reconciliation but never mutate entitlements.
            order_public_id = None
            processing_status = "ignored_non_authoritative"
        else:
            order_public_id = None
            processing_status = "ignored_unsupported"
    except WebhookValidationError as exc:
        # Roll back every order/transaction/entitlement mutation attempted by
        # the rejected event, then retain only its digest and rejection code.
        db.rollback()
        rejected_event = (
            db.query(PaymentEvent)
            .filter(
                PaymentEvent.provider == PROVIDER,
                PaymentEvent.provider_event_id == provider_event_id,
            )
            .first()
        )
        if rejected_event is None:
            rejected_event = PaymentEvent(
                provider=PROVIDER,
                provider_event_id=provider_event_id,
                event_type=event_type,
                payload_sha256=payload_sha256,
            )
            db.add(rejected_event)
        rejected_event.processing_status = "rejected"
        rejected_event.error_code = exc.code
        rejected_event.processed_at = utcnow()
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
        raise

    event.processing_status = processing_status
    event.error_code = processing_status if processing_status in RETRYABLE_EVENT_STATUSES else None
    event.processed_at = utcnow()
    db.commit()
    return WebhookResult(
        status=processing_status,
        event_type=event_type,
        order_public_id=order_public_id,
    )
