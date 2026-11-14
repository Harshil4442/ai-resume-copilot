"""Database-authoritative admissions, separate from prepaid money settlement.

Caller locks application rows first, then this module locks the candidate. Every
quota mutation uses that candidate lock. No network, rendering or secret lookup
occurs here. Pending/uncertain sends do not age out merely because a day changes.
"""
from __future__ import annotations

import copy
from datetime import UTC, datetime, timedelta

from fastapi import HTTPException
from sqlalchemy import and_, func, or_, select

from ...models import User
from ..common import payload_fingerprint, public_id, utcnow
from . import config, models

PENDING = {"reserved", "submitting", "unknown"}


def lock_application_set(db, user_id):
    """Fence application creation against an erasure scan, before row locks.

    Row locks cannot protect an application that does not yet exist. Keep this
    transaction-scoped owner lock through creation or the caller's full account
    deletion; execution still takes application rows before the candidate row.
    SQLite is only a local fixture target, not the concurrent production store.
    """
    if db.get_bind().dialect.name == "postgresql":
        db.execute(select(func.pg_advisory_xact_lock(0x48575A, user_id)))


def aware(value):
    return value.replace(tzinfo=UTC) if value and value.tzinfo is None else value


def employer_identity(source) -> str:
    explicit = getattr(source, "employer_key", None)
    identity = {"verified_employer_key": explicit} if explicit else {
        "platform": source.platform, "region": source.region, "tenant": source.board_token,
    }
    return payload_fingerprint(identity)


def opening_identity(posting, source) -> str:
    """No fuzzy merging: a verified requisition wins, external ID is conservative."""
    requisition = str(posting.requisition_id or "").strip()
    return payload_fingerprint({
        "employer": employer_identity(source),
        "identity_kind": "requisition" if requisition else "external_posting",
        "identity": requisition or str(posting.external_id),
        # External posting identifiers are vendor scoped, even for grouped employers.
        "provider": None if requisition else source.platform,
    })


def quote(source, posting, *, now=None) -> dict:
    now = now or utcnow()
    candidate, employer = config.admission_limits(), config.employer_limits(source)
    policy = {"candidate": candidate, "employer": employer,
              "employer_key": employer_identity(source), "opening_key": opening_identity(posting, source)}
    return policy | {"quoted_at": now.isoformat(),
                     "expires_at": (now + timedelta(hours=candidate["quote_hours"])).isoformat(),
                     "policy_fingerprint": payload_fingerprint(policy)}


def _owner_lock(db, user_id):
    user = db.query(User).filter_by(id=user_id).with_for_update().first()
    if not user:
        raise HTTPException(409, {"code": "account_unavailable", "message": "This account is no longer available."})
    return user


def _error(code, message, *, limit=None, used=None, retry_at=None):
    raise HTTPException(429, {
        "code": code, "message": message, "limit": limit, "used": used,
        "retry_at": retry_at.isoformat() if retry_at else None,
        "uncertain_outcomes_count": True,
    })


def _usage(db, user_id, cutoff=None, employer_key=None, *, pending_only=False):
    query = db.query(func.count(models.EmployerAdmission.id),
                     func.coalesce(func.sum(models.EmployerAdmission.credit_cost), 0)).filter(models.EmployerAdmission.user_id == user_id)
    if employer_key:
        query = query.filter(models.EmployerAdmission.employer_key == employer_key)
    predicate = models.EmployerAdmission.state.in_(PENDING)
    if not pending_only:
        predicate = or_(predicate, and_(models.EmployerAdmission.state == "consumed",
                                       models.EmployerAdmission.possible_send_at >= cutoff))
    count, credits = query.filter(predicate).one()
    return int(count), int(credits)


def _check_policy(db, user_id, policy, *, now, increment, employer_key=None):
    day = now.astimezone(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    next_day = day + timedelta(days=1)
    prefix = "employer" if employer_key else "candidate"
    pending_count, pending_amount = _usage(db, user_id, employer_key=employer_key, pending_only=True)
    for kind, cutoff, retry_at in (
        ("daily", day, next_day),
        ("rolling", now - timedelta(days=policy["rolling_days"]), None),
    ):
        count, amount = _usage(db, user_id, cutoff, employer_key)
        if count + increment[0] > policy[kind + "_limit"]:
            _error(prefix + "_" + kind + "_limit", f"The {prefix} {kind} application-attempt limit is reached. Queued and uncertain applications count; cancel work that has not started or reconcile uncertain outcomes.",
                   limit=policy[kind + "_limit"], used=count,
                   retry_at=retry_at if pending_count + increment[0] <= policy[kind + "_limit"] else None)
        credit_limit = policy.get(kind + "_credit_limit")
        if credit_limit is not None and amount + increment[1] > credit_limit:
            _error(prefix + "_" + kind + "_credit_limit", f"The {kind} application-attempt credit budget is reached. This safety budget is separate from the prepaid balance; uncertain outcomes remain held.",
                   limit=credit_limit, used=amount,
                   retry_at=retry_at if pending_amount + increment[1] <= credit_limit else None)
    if not employer_key:
        pending = pending_count
        if pending + increment[0] > policy["pending_limit"]:
            _error("candidate_pending_limit", "Too many application attempts are queued, sending or uncertain. Cancel unstarted work or resolve uncertain outcomes before starting more.",
                   limit=policy["pending_limit"], used=pending)


def _policies(application, source, now):
    snapshot = application.admission_snapshot or {}
    try:
        expiry = aware(datetime.fromisoformat(snapshot["expires_at"]))
        if (expiry <= now or snapshot["employer_key"] != employer_identity(source) or
                snapshot["opening_key"] != application.opening_key):
            raise ValueError("expired or changed quote")
        return snapshot, config.admission_limits(), config.employer_limits(source)
    except (KeyError, TypeError, ValueError) as exc:
        raise HTTPException(409, {"code": "admission_quote_expired", "message": "The application safety quote expired or changed. Save and review this package again before execution."}) from exc


def reserve(db, application, source, *, now=None):
    """Reserve with queued state/credit/outbox in the caller's one transaction."""
    now = now or utcnow()
    _owner_lock(db, application.user_id)
    existing = db.query(models.EmployerAdmission).filter_by(application_id=application.id).first()
    if existing:
        if existing.state != "released":
            return existing
        raise HTTPException(409, {"code": "admission_already_released", "message": "This attempt was stopped. Prepare and review a new application rather than reusing its launch."})
    snapshot, current_candidate, current_employer = _policies(application, source, now)
    active = f"{application.user_id}:{application.opening_key}"
    if db.query(models.EmployerAdmission.id).filter_by(active_key=active).first():
        raise HTTPException(409, {"code": "canonical_opening_conflict", "message": "A pending, uncertain or completed attempt already exists for this opening, including another source or language version."})
    increment = (1, application.credit_cost)
    for candidate in (snapshot["candidate"], current_candidate):
        _check_policy(db, application.user_id, candidate, now=now, increment=increment)
    # Also enforce caps of other verified tenants grouped under this employer.
    employers = [snapshot["employer"], current_employer]
    if source.employer_key:
        employers.extend(config.employer_limits(row) for row in db.query(models.EmployerSource).filter_by(employer_key=source.employer_key).all())
    for employer in employers:
        _check_policy(db, application.user_id, employer, now=now, increment=increment,
                      employer_key=application.employer_key)
    row = models.EmployerAdmission(
        id=public_id("admission"), user_id=application.user_id, application_id=application.id,
        employer_key=application.employer_key, opening_key=application.opening_key,
        active_key=active, credit_cost=application.credit_cost, admitted_at=now,
        policy_snapshot=copy.deepcopy(snapshot), state="reserved",
    )
    db.add(row)
    db.flush([row])  # Subsequent items in an atomic batch see the new slot.
    return row


def launch(db, application, source, *, now=None):
    """Final reservation check and possible-send transition; caller commits first."""
    now = now or utcnow()
    _owner_lock(db, application.user_id)
    row = db.query(models.EmployerAdmission).filter_by(application_id=application.id).with_for_update().first()
    if not row or row.state != "reserved" or row.active_key != f"{application.user_id}:{application.opening_key}":
        raise HTTPException(409, {"code": "admission_unavailable", "message": "This application has no current unstarted admission reservation."})
    snapshot, current_candidate, current_employer = _policies(application, source, now)
    for candidate in (snapshot["candidate"], current_candidate):
        _check_policy(db, application.user_id, candidate, now=now, increment=(0, 0))
    employers = [snapshot["employer"], current_employer]
    if source.employer_key:
        employers.extend(config.employer_limits(item) for item in db.query(models.EmployerSource).filter_by(employer_key=source.employer_key).all())
    for employer in employers:
        _check_policy(db, application.user_id, employer, now=now, increment=(0, 0), employer_key=application.employer_key)
    row.state, row.possible_send_at = "submitting", now
    return row


def finish(db, application, outcome, *, proven_not_submitted=False):
    """A money refund does not erase a possible-send attempt from safety budgets."""
    _owner_lock(db, application.user_id)
    row = db.query(models.EmployerAdmission).filter_by(application_id=application.id).with_for_update().first()
    if not row:
        return
    if outcome == "unknown":
        row.state = "unknown"
    elif proven_not_submitted or row.possible_send_at is None:
        row.state, row.active_key = "released", None
        row.release_reason, row.settled_at = outcome[:80], utcnow()
    else:
        row.state, row.settled_at = "consumed", utcnow()
        if outcome != "confirmed":
            row.active_key = None  # A verified rejection allows a newly reviewed intent.


def response(db, application):
    row = db.query(models.EmployerAdmission).filter_by(application_id=application.id).first()
    return None if not row else {key: getattr(row, key) for key in (
        "id", "state", "admitted_at", "possible_send_at", "settled_at", "release_reason",
    )}
