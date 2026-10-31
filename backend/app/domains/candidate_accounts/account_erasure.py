"""Shared SQL privacy erasure; callers prove intent and own commit/rollback.

Preserves the existing owner/application locks, financial unlinking, retained
liabilities and artifact cleanup. This helper never authenticates a caller,
issues native authority, refunds money or reports a committed deletion.
"""
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from ...models import (
    AdminAuditEvent,
    AnalysisRun,
    ApplicationEvent,
    CandidatePasswordAccount,
    CareerMemoryEntry,
    EntitlementLedger,
    EvidenceItem,
    JobMatch,
    ModelCallEvent,
    NotificationOutbox,
    Opportunity,
    OpportunityContact,
    PaymentOrder,
    Reminder,
    Resume,
    ResumeVersion,
    UsageEvent,
    User,
    UserProfile,
)
from ..career.evidence_write_fence import evidence_write_scope
from ..employer.admissions import lock_application_set


def lock_account_erasure(db: Session, user_id: int) -> None:
    lock_application_set(db, user_id)
    db.query(AnalysisRun).filter_by(user_id=user_id).order_by(AnalysisRun.id).with_for_update().all()


def erased_owner_rows_absent(db: Session, user_id: int) -> bool:
    """Exact completion audit callers additionally verify no live owner rows."""
    if db.get(User, user_id, populate_existing=True) is not None:
        return False
    for model in (CandidatePasswordAccount, UserProfile, Resume, JobMatch, Opportunity, ApplicationEvent,
                  ResumeVersion, EvidenceItem, Reminder, OpportunityContact, CareerMemoryEntry, AnalysisRun,
                  UsageEvent, ModelCallEvent, NotificationOutbox):
        if db.query(model.user_id).filter_by(user_id=user_id).first() is not None:
            return False
    return True


def erase_account_sql(db: Session, user_id: int) -> None:
    """Authorized exact-owner SQL work only; this function does not commit."""
    uid = user_id
    now = datetime.now(UTC)

    lock_account_erasure(db, uid)

    # End access before unlinking the ledger. This is an audit transition, not
    # a refund and not a subscription cancellation.
    active_entitlements = (
        db.query(EntitlementLedger)
        .filter(
            EntitlementLedger.user_id == uid,
            EntitlementLedger.status == "active",
        )
        .all()
    )
    for entitlement in active_entitlements:
        entitlement.status = "ended_account_deleted"
        entitlement.revoked_at = now
        entitlement.user_id = None
    db.query(EntitlementLedger).filter(EntitlementLedger.user_id == uid).update(
        {EntitlementLedger.user_id: None}, synchronize_session=False
    )

    orders = db.query(PaymentOrder).filter(PaymentOrder.user_id == uid).all()
    for order in orders:
        if order.status in {"initializing", "created", "client_confirmed"}:
            order.status = "customer_deleted"
        order.active_attempt_key = None
        order.customer_deleted_at = now
        order.user_id = None

    db.query(ModelCallEvent).filter(ModelCallEvent.user_id == uid).delete(synchronize_session=False)
    from ..analysis.models import AnalysisRequestKey
    from ..credits.privacy import erase_owner_credit_links
    from ..dispatch.models import DispatchOutbox
    from ..employer.service import delete_account_data
    erase_owner_credit_links(db, user_id=uid)
    delete_account_data(db, uid)
    run_ids = [identity for (identity,) in db.query(AnalysisRun.id).filter_by(user_id=uid).all()]
    if run_ids:
        db.query(DispatchOutbox).filter(DispatchOutbox.topic == "analysis.run", DispatchOutbox.aggregate_id.in_(run_ids)).delete(synchronize_session=False)
    db.query(AnalysisRequestKey).filter_by(user_id=uid).delete(synchronize_session=False)
    db.query(NotificationOutbox).filter(NotificationOutbox.user_id == uid).delete(synchronize_session=False)
    db.query(AdminAuditEvent).filter(AdminAuditEvent.actor_user_id == uid).update(
        {AdminAuditEvent.actor_user_id: None}, synchronize_session=False
    )
    db.query(UsageEvent).filter(UsageEvent.user_id == uid).delete(synchronize_session=False)
    db.query(ApplicationEvent).filter(ApplicationEvent.user_id == uid).delete(synchronize_session=False)
    db.query(ResumeVersion).filter(ResumeVersion.user_id == uid).delete(synchronize_session=False)
    # The account/lifetime owner checks above authorize only this exact privacy
    # deletion. Do not carry its write marker into the remaining erasure work.
    with evidence_write_scope(db):
        db.query(EvidenceItem).filter(EvidenceItem.user_id == uid).delete(synchronize_session=False)
    db.query(Reminder).filter(Reminder.user_id == uid).delete(synchronize_session=False)
    db.query(OpportunityContact).filter(OpportunityContact.user_id == uid).delete(synchronize_session=False)
    db.query(CareerMemoryEntry).filter(CareerMemoryEntry.user_id == uid).delete(synchronize_session=False)
    db.query(AnalysisRun).filter(AnalysisRun.user_id == uid).delete(synchronize_session=False)
    db.query(Opportunity).filter(Opportunity.user_id == uid).delete(synchronize_session=False)
    db.query(JobMatch).filter(JobMatch.user_id == uid).delete(synchronize_session=False)
    db.query(Resume).filter(Resume.user_id == uid).delete(synchronize_session=False)
    db.query(UserProfile).filter(UserProfile.user_id == uid).delete(synchronize_session=False)
    db.query(CandidatePasswordAccount).filter_by(user_id=uid).delete(synchronize_session=False)
    db.query(User).filter(User.id == uid).delete(synchronize_session=False)
