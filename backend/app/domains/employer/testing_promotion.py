"""One owner-only, unpaid, noncommercial search acceptance promotion.

This does not manufacture a purchase or reserve provider cash. Existing service
admission independently reserves its finite nonpayment operating exposure on use.
The campaign has one deterministic ledger identity, including after account unlink.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Any

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.orm import Session

from ... import models as core
from ...billing import cost_policy
from ..common import payload_fingerprint, utcnow
from ..entitlements import lock_entitlement_owner
from .models import ServiceCreditEvent

CAMPAIGN = "hirewiz-owner-prelaunch-search-20261023-v1"
AMOUNT = 3
SOURCE_TYPE = "testing_promotion"
_SUFFIX = hashlib.sha256(CAMPAIGN.encode()).hexdigest()[:56]
EVENT_ID = "svc_test" + _SUFFIX
AUDIT_ID = "aud_test" + _SUFFIX


def _refused(code: str, message: str, status: int = 409) -> HTTPException:
    return HTTPException(status, detail={"code": code, "message": message})


@dataclass(frozen=True)
class Operator:
    """Trusted operator transport input, never issued by a customer request."""
    email: str
    native_identity_sha256: str

    def validate(self) -> None:
        if (not isinstance(self.email, str) or re.fullmatch(r"[^\s@]{1,64}@[^\s@]{1,253}", self.email) is None
                or self.email != self.email.strip().lower()
                or not isinstance(self.native_identity_sha256, str)
                or re.fullmatch(r"[a-f0-9]{64}", self.native_identity_sha256) is None):
            raise _refused("testing_promotion_operator_unverified", "The reviewed operator identity is required.")


def _owner(db: Session, owner_id: int, opportunity_id: str, *, lock: bool) -> core.User:
    if (type(owner_id) is not int or owner_id <= 0 or not isinstance(opportunity_id, str)
            or re.fullmatch(r"opp_[a-f0-9]{32}", opportunity_id) is None):
        raise _refused("testing_promotion_owner_mismatch", "The reviewed owner binding is required.")
    owner = lock_entitlement_owner(db, owner_id) if lock else db.get(core.User, owner_id)
    if owner is None:
        raise _refused("testing_promotion_owner_mismatch", "The reviewed owner binding is required.")
    query = db.query(core.Opportunity).filter_by(id=opportunity_id, user_id=owner_id, resume_id=28)
    resume = db.query(core.Resume).filter_by(id=28, user_id=owner_id)
    if lock:
        query, resume = query.with_for_update(), resume.with_for_update()
    if query.one_or_none() is None or resume.one_or_none() is None:
        raise _refused("testing_promotion_owner_mismatch", "The reviewed owner binding is required.")
    return owner


def _existing(db: Session) -> tuple[ServiceCreditEvent, core.AdminAuditEvent] | None:
    event = db.get(ServiceCreditEvent, EVENT_ID)
    audit = db.get(core.AdminAuditEvent, AUDIT_ID)
    if event is None and audit is None:
        return None
    if (event is None or audit is None or event.source_type != SOURCE_TYPE
            or event.source_id != CAMPAIGN or event.event_type != "grant" or event.amount != AMOUNT
            or audit.action != "testing_service_credit_grant"
            or audit.target_type != "user" or audit.after_state.get("service_credit_event_id") != EVENT_ID
            or audit.after_state.get("campaign") != CAMPAIGN or audit.after_state.get("amount") != AMOUNT
            or audit.after_state.get("unpaid") is not True
            or audit.after_state.get("job_service_credits") != event.balance_after
            or event.user_id is not None and audit.target_id != str(event.user_id)):
        raise _refused("testing_promotion_history_conflict", "The testing grant needs accounting review.")
    return event, audit


def _terms(campaign: str, amount: int, reason: str, accepted: bool,
           actor: Operator, owner_id: int, opportunity_id: str) -> dict[str, Any]:
    return {"campaign": campaign, "amount": amount, "reason": reason,
            "accepted_noncommercial_testing": accepted, "operator_email": actor.email,
            "owner_id": owner_id, "opportunity_id": opportunity_id, "resume_id": 28}


def _result(owner: core.User, event: ServiceCreditEvent, audit: core.AdminAuditEvent,
            *, replay: bool) -> dict[str, Any]:
    return {"campaign": CAMPAIGN, "amount": AMOUNT, "event_id": event.id,
            "audit_id": audit.id, "balance": int(owner.job_service_credits or 0),
            "idempotent_replay": replay, "unpaid": True}


def review(db: Session, *, actor: Operator, owner_id: int, opportunity_id: str) -> dict[str, Any]:
    actor.validate()
    owner = _owner(db, owner_id, opportunity_id, lock=False)
    existing = _existing(db)
    if existing is not None:
        event, audit = existing
        if event.user_id != owner.id or audit.actor_email != actor.email:
            raise _refused("testing_promotion_exhausted", "This one-time testing campaign has already been used.")
        return {**_result(owner, event, audit, replay=True), "status": "granted"}
    return {"campaign": CAMPAIGN, "amount": AMOUNT, "balance": int(owner.job_service_credits or 0),
            "status": "available", "unpaid": True}


def grant(db: Session, *, actor: Operator, owner_id: int, opportunity_id: str, campaign: str,
          amount: int, reason: str, accepted: bool, idempotency_key: str,
          correlation_id: str | None) -> dict[str, Any]:
    actor.validate()
    owner = _owner(db, owner_id, opportunity_id, lock=True)
    if db.get_bind().dialect.name == "postgresql":
        # Match normal service accounting's owner-before-fleet lock order.
        db.execute(text("SELECT pg_advisory_xact_lock(7349186502111)"))
    supplied = _terms(campaign, amount, reason, accepted, actor, owner_id, opportunity_id)
    fingerprint = payload_fingerprint(supplied)
    existing = _existing(db)
    if existing is not None:
        event, audit = existing
        if (event.user_id != owner.id or audit.actor_email != actor.email
                or event.idempotency_key != "testing:" + idempotency_key
                or audit.after_state.get("request_sha256") != fingerprint):
            raise _refused("testing_promotion_conflict", "The testing grant is already bound to its original review.")
        return _result(owner, event, audit, replay=True)
    if (campaign != CAMPAIGN or type(amount) is not int or amount != AMOUNT or accepted is not True
            or not isinstance(reason, str) or not 10 <= len(reason) <= 240
            or not isinstance(idempotency_key, str)
            or re.fullmatch(r"[A-Za-z0-9_.:-]{8,160}", idempotency_key) is None):
        raise _refused("invalid_testing_promotion", "Only the reviewed three-credit testing grant is available.", 422)
    before = int(owner.job_service_credits or 0)
    if before != 0:
        raise _refused("testing_promotion_balance_changed", "Review the existing service balance before testing.")
    try:
        policy = cost_policy.current_policy()
        cost_policy.assert_actual_variance(db, policy)
        if policy.service_search_credits != 1:
            raise cost_policy.CostPolicyUnavailable("The testing campaign's accepted search price has changed.")
        funding = cost_policy.service_expense_holds(
            db, policy, operation="job_search", unit_price=1, count=AMOUNT, user_id=owner.id,
        )
    except cost_policy.CostPolicyUnavailable as exc:
        raise _refused("testing_promotion_funding_unavailable", str(exc), 503) from None
    owner.job_service_credits = before + AMOUNT
    now = utcnow()
    event = ServiceCreditEvent(
        id=EVENT_ID, user_id=owner.id, event_type="grant", amount=AMOUNT,
        balance_after=owner.job_service_credits, idempotency_key="testing:" + idempotency_key,
        source_type=SOURCE_TYPE, source_id=CAMPAIGN, reason=reason, created_at=now,
    )
    operator_user_id = db.query(core.User.id).filter(core.User.email == actor.email).scalar()
    audit = core.AdminAuditEvent(
        id=AUDIT_ID, actor_user_id=operator_user_id, actor_email=actor.email,
        action="testing_service_credit_grant", target_type="user", target_id=str(owner.id),
        reason=reason, before_state={"job_service_credits": before},
        after_state={"job_service_credits": owner.job_service_credits,
                     "service_credit_event_id": EVENT_ID, "campaign": CAMPAIGN, "amount": AMOUNT,
                     "request_sha256": fingerprint, "unpaid": True,
                     "expense_policy_version": policy.version, "funding_review": funding,
                     "operator_native_identity_sha256": actor.native_identity_sha256,
                     "actual_work_admission_required": True},
        correlation_id=correlation_id, created_at=now,
    )
    db.add_all([event, audit])
    db.flush()
    return _result(owner, event, audit, replay=False)
