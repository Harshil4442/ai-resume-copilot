"""Remove owner bindings without deleting money, payment or liability history."""

from sqlalchemy.orm import Session

from ..employer.admissions import lock_application_set
from .discovery_models import CreditDiscoveryBatch, CreditDiscoveryDelivery
from .models import (
    CreditAccount,
    CreditEvent,
    CreditExecutionHold,
    CreditGrant,
    CreditPurchaseQuote,
    CreditReservation,
    CreditServiceQuote,
)


def erase_owner_credit_links(db: Session, *, user_id: int) -> None:
    """Called inside account erasure before deleting User; caller owns commit.

    In-flight costs stay conservatively held. Trusted provider callbacks can
    still settle their private completion binding after the account is removed.
    """
    from ...models import User

    lock_application_set(db, user_id)
    db.query(User.id).filter_by(id=user_id).with_for_update().one_or_none()
    # Erase owner-bound discovery proofs before their EmployerSearch parent;
    # accounting holds remain detached below, including unknown native costs.
    db.query(CreditDiscoveryDelivery).filter_by(user_id=user_id).delete(synchronize_session="fetch")
    db.query(CreditDiscoveryBatch).filter_by(user_id=user_id).delete(synchronize_session="fetch")
    db.query(CreditServiceQuote).filter_by(user_id=user_id).delete(synchronize_session="fetch")
    db.query(CreditPurchaseQuote).filter_by(user_id=user_id).delete(synchronize_session="fetch")
    for model in (CreditExecutionHold, CreditReservation, CreditGrant, CreditEvent):
        db.query(model).filter_by(user_id=user_id).update(
            {model.user_id: None}, synchronize_session="fetch"
        )
    db.query(CreditAccount).filter_by(user_id=user_id).delete(synchronize_session="fetch")
    db.flush()


def export_account_data(db: Session, user_id: int) -> dict:
    """Explicit customer fields; never dump internal funding/route snapshots."""
    from .service import wallet

    grants = db.query(CreditGrant).filter_by(user_id=user_id).order_by(CreditGrant.issued_at).all()
    events = db.query(CreditEvent).filter_by(user_id=user_id).order_by(CreditEvent.created_at, CreditEvent.id).all()
    batches = db.query(CreditDiscoveryBatch).filter_by(user_id=user_id).order_by(CreditDiscoveryBatch.created_at).all()
    deliveries = db.query(CreditDiscoveryDelivery).filter_by(user_id=user_id).order_by(
        CreditDiscoveryDelivery.created_at, CreditDiscoveryDelivery.ordinal).all()
    return {
        "wallet": wallet(db, user_id=user_id).model_dump(mode="json"),
        "grants": [{"id": g.id, "quantity": g.quantity, "remaining_credits": g.remaining_credits,
                    "funding_kind": g.funding_kind, "issued_at": g.issued_at, "expires_at": g.expires_at,
                    "purchase_reference": g.origin_id if g.origin_type == "payment_order" else None}
                   for g in grants],
        "history": [{"id": e.id, "type": e.event_type, "amount": e.amount,
                     "balance_after": e.balance_after, "created_at": e.created_at} for e in events],
        "discovery_batches": [{"search_id": b.search_id, "created_at": b.created_at} for b in batches],
        "discovery_deliveries": [{"search_id": d.search_id, "posting_id": d.posting_id,
                                  "position": d.ordinal + 1, "created_at": d.created_at} for d in deliveries],
    }
