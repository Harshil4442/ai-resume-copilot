"""Funded deterministic discovery: one credit for one completed batch of 1–5 new jobs.

The existing bounded selector, source fences and history clock remain authoritative.
Batch membership has its own proof: it never pretends every job cost one legacy unit.
"""

from __future__ import annotations

import json
from datetime import timedelta

from fastapi import HTTPException
from sqlalchemy import Text, cast, func, or_
from sqlalchemy.orm import Session

from ... import models as core
from ..common import payload_fingerprint, public_id, utcnow
from ..employer import admissions, catalogue_search, delivery_history
from ..employer import config as employer_config
from ..employer import models as employer_models
from ..employer import schemas as employer_schemas
from ..employer import service as employer
from . import admission, execution, finance, pricing, runtime, service, service_quotes
from .discovery_models import CreditDiscoveryBatch, CreditDiscoveryDelivery
from .discovery_schemas import DiscoveryBatchView, DiscoveryCreate, DiscoveryRequest
from .models import CreditExecutionHold, CreditReservation, CreditServiceQuote

PROTOCOL = "unified-discovery-batch-v1"
OPERATION = "job_discovery_batch"
CANDIDATE_LIMIT = 1000


def _reject(message: str, *, created: bool = False) -> HTTPException:
    return HTTPException(
        409,
        {
            "code": "discovery_batch_in_use" if created else "credit_service_quote_rejected",
            "run_created": created,
            "message": message,
        },
    )


def _input(payload: DiscoveryRequest) -> dict:
    return payload.model_dump(mode="json", exclude={"accepted_service_quote_id", "idempotency_key"})


def _intent(payload: DiscoveryCreate) -> str:
    return payload_fingerprint(
        {"input": _input(payload), "quote_id": payload.accepted_service_quote_id}
    )


def _identity(user_id: int, key: str) -> str:
    return "search_" + payload_fingerprint({"owner": user_id, "key": "unified:" + key})[:48]


def membership(db, owner, opening_keys, posting_ids):
    """Bounded union input; legacy proof rows retain their original contract."""
    if len(opening_keys) > 1000 or len(posting_ids) > 1000:
        raise ValueError("Batch membership requires a bounded candidate set")
    rows = (
        db.query(
            CreditDiscoveryDelivery,
            CreditExecutionHold.delivery_state,
            CreditReservation.committed_credits,
        )
        .join(
            CreditDiscoveryBatch,
            (CreditDiscoveryBatch.search_id == CreditDiscoveryDelivery.search_id)
            & (CreditDiscoveryBatch.user_id == CreditDiscoveryDelivery.user_id),
        )
        .join(CreditExecutionHold, CreditExecutionHold.id == CreditDiscoveryBatch.hold_id)
        .join(CreditReservation, CreditReservation.id == CreditExecutionHold.reservation_id)
        .filter(
            CreditDiscoveryDelivery.user_id == owner,
            or_(
                CreditDiscoveryDelivery.opening_key.in_(opening_keys),
                CreditDiscoveryDelivery.posting_id.in_(posting_ids),
            ),
        )
        .all()
    )
    if any(state != "delivered" or amount != 1 for _, state, amount in rows):
        raise HTTPException(
            409,
            {
                "code": "batch_delivery_history_requires_review",
                "message": "The saved batch delivery requires reconciliation before another search.",
            },
        )
    return {row.opening_key for row, _, _ in rows}, {row.posting_id for row, _, _ in rows}


def _source_snapshot(db, user_id: int, payload: DiscoveryRequest):
    resume = (
        db.query(core.Resume.id, core.Resume.skills)
        .filter_by(id=payload.resume_id, user_id=user_id)
        .one_or_none()
    )
    if resume is None:
        raise _reject("The selected resume is no longer available")
    revision = employer._bounded_resume_revision(db, user_id, payload.resume_id)
    context = {
        "request": _input(payload),
        "resume": {"id": resume.id, "skills": resume.skills or [], "revision": revision},
    }
    encoded = json.dumps(
        context, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode()
    if len(encoded) > 32768:
        raise _reject("These resume skills and search choices exceed the supported request size")
    return context, payload_fingerprint(context), len(encoded), revision


def prepare_quote_context(db: Session, *, user_id: int, payload: DiscoveryRequest):
    employer._require_enabled()
    if employer_config.paid_history_mode() != "indexed":
        raise _reject("Discovery batch history is not ready for reviewed execution")
    progress = delivery_history._progress(db, user_id)
    old_delivery = (
        db.query(employer_models.EmployerJobDelivery.id)
        .filter(
            employer_models.EmployerJobDelivery.user_id == user_id,
            employer_models.EmployerJobDelivery.charged_credits > 0,
        )
        .first()
    )
    if old_delivery is not None and delivery_history._readiness(db, user_id, progress) != "ready":
        raise _reject("Saved delivery history is being prepared. Review a quote after it is ready.")
    context, fingerprint, size, _ = _source_snapshot(db, user_id, payload)
    # Conservatively cover any 1,001-row cohort, including rows below filtered
    # title/location branches. SQL lengths avoid reading candidate bodies here.
    body_size = employer._utf8_bytes(
        db,
        func.coalesce(employer_models.EmployerPosting.description, "")
        + func.coalesce(cast(employer_models.EmployerPosting.preference_metadata, Text), ""),
    )
    count, largest = (
        db.query(func.count(employer_models.EmployerPosting.id), func.max(body_size))
        .join(
            employer_models.EmployerSource,
            employer_models.EmployerSource.id == employer_models.EmployerPosting.source_id,
        )
        .filter(
            employer_models.EmployerPosting.is_open.is_(True),
            employer_models.EmployerSource.enabled.is_(True),
        )
        .one()
    )
    observed = {
        "context_bytes": size,
        "candidate_limit": CANDIDATE_LIMIT,
        "candidate_content_bytes": min(int(count), CANDIDATE_LIMIT + 1) * int(largest or 0),
        "maximum_results": payload.desired_count,
    }
    try:
        configured = runtime.current_runtime(user_id, db=db)
        route = configured.resolve_route(OPERATION, context, observed_limits=observed)
        service_quotes.require_route(route, configured.policy, now=utcnow())
        if route.mode != "native" or route.model_quotes or route.credits != 1:
            raise finance.FundingUnavailable(
                "Discovery requires the qualified deterministic one-credit route"
            )
    except (finance.FundingUnavailable, pricing.CreditContractError) as exc:
        raise _reject(str(exc)) from None
    return context, fingerprint, configured, route


def quote(db: Session, *, user_id: int, payload: DiscoveryRequest, idempotency_key: str):
    _, fingerprint, configured, route = prepare_quote_context(db, user_id=user_id, payload=payload)
    return service_quotes.issue_service_quote(
        db,
        user_id=user_id,
        input_fingerprint=fingerprint,
        idempotency_key=idempotency_key,
        route=route,
        policy=configured.policy,
    )


def _batch(db, user_id: int, identity: str):
    batch = (
        db.query(CreditDiscoveryBatch).filter_by(search_id=identity, user_id=user_id).one_or_none()
    )
    if batch is None:
        raise HTTPException(404, "Discovery batch not found")
    return batch


def public_summary(values: dict) -> dict:
    """Adapt saved searches without exposing worker leases or relabeling money."""
    scope = values.get("scope", {})
    if not isinstance(scope, dict) or scope.get("bounded_protocol") != PROTOCOL:
        return values
    allowed = {
        "kind",
        "candidate_limit",
        "candidate_limit_reached",
        "catalogue_complete",
        "worldwide_recall_verified",
        "max_source_age_hours",
        "unknown_publication_dates_excluded",
        "candidates_before_preferences",
        "known_preference_conflicts",
        "candidates_with_unknown_preferences",
        "unknown_preference_metadata",
        "partial_batch",
        "new_qualifying_count",
        "failure_reason",
    }
    public = {key: value for key, value in scope.items() if key in allowed}
    public.update(
        {
            "billing_currency": "credits",
            "price_quote": {
                "billing_currency": "credits",
                "pricing_unit": "completed_batch",
                "maximum_credits": 1,
                "maximum_results": values["desired_count"],
                "charge_condition": "at_least_one_new_verified_job",
            },
        }
    )
    return dict(values, billing_currency="credits", scope=public)


def status(db: Session, *, user_id: int, search_id: str) -> DiscoveryBatchView:
    _batch(db, user_id, search_id)
    row = (
        db.query(employer_models.EmployerSearch)
        .filter_by(id=search_id, user_id=user_id)
        .populate_existing()
        .one()
    )
    return DiscoveryBatchView(
        id=row.id,
        status=row.status,
        desired_count=row.desired_count,
        delivered_count=row.delivered_count,
        charged_credits=row.charged_credits,
        reserved_credits=row.reserved_credits if row.status == "preparing" else 0,
        refunded_credits=row.refunded_credits,
        partial_batch=row.status == "completed" and 0 < row.delivered_count < row.desired_count,
        scope=public_summary({"scope": row.scope, "desired_count": row.desired_count})["scope"],
        items=row.items if row.status == "completed" else [],
    )


def create(db: Session, *, user_id: int, payload: DiscoveryCreate) -> DiscoveryBatchView:
    identity = _identity(user_id, payload.idempotency_key)
    admissions.lock_application_set(db, user_id)
    service._lock_owner(db, user_id)
    existing = (
        db.query(CreditDiscoveryBatch).filter_by(search_id=identity, user_id=user_id).one_or_none()
    )
    if existing is not None:
        if existing.request_fingerprint != _intent(payload):
            raise _reject(
                "This request identity already belongs to a different reviewed batch", created=True
            )
        return status(db, user_id=user_id, search_id=identity)
    accepted = (
        db.query(CreditDiscoveryBatch)
        .filter_by(quote_id=payload.accepted_service_quote_id, user_id=user_id)
        .one_or_none()
    )
    if accepted is not None:
        if accepted.request_fingerprint != _intent(payload):
            raise _reject("This quote already belongs to another reviewed batch", created=True)
        return status(db, user_id=user_id, search_id=accepted.search_id)
    _, fingerprint, configured, route = prepare_quote_context(db, user_id=user_id, payload=payload)
    saved = (
        db.query(CreditServiceQuote)
        .filter_by(id=payload.accepted_service_quote_id, user_id=user_id)
        .one_or_none()
    )
    now = utcnow()
    if (
        saved is None
        or saved.operation != OPERATION
        or saved.input_fingerprint != fingerprint
        or saved.accepted_reservation_id
        or not pricing.aware(saved.created_at) <= now < pricing.aware(saved.expires_at)
    ):
        raise _reject("Review a current quote for these exact search choices")
    # No credit hold or source result exists before this point. Later uncertain
    # errors deliberately do not claim run_created:false; the same intent recovers.
    revision = employer._bounded_resume_revision(db, user_id, payload.resume_id, fence=True)
    row = employer_models.EmployerSearch(
        id=identity,
        user_id=user_id,
        resume_id=payload.resume_id,
        idempotency_key="unified:" + payload_fingerprint(payload.idempotency_key),
        input_fingerprint=fingerprint,
        query=_input(payload),
        items=[],
        status="preparing",
        desired_count=payload.desired_count,
        delivered_count=0,
        reserved_credits=1,
        charged_credits=0,
        refunded_credits=0,
        scope={
            "bounded_protocol": PROTOCOL,
            "billing_currency": "credits",
            "resume_source_revision": revision,
            "paid_history_mode": "indexed",
            "price_quote": {
                "unit_price": 0,
                "batch_credits": 1,
                "maximum_results": payload.desired_count,
                "maximum_credits": 1,
                "service_quote_id": saved.id,
                "tariff_version": route.tariff_version,
            },
            "max_source_age_hours": employer_config.positive_int(
                "EMPLOYER_MAX_SOURCE_AGE_HOURS", 24, 168
            ),
            "worldwide_recall_verified": False,
            "candidate_limit": CANDIDATE_LIMIT,
            "catalogue_complete": False,
        },
    )
    db.add(row)
    db.flush([row])
    hold = admission.admit(
        db,
        user_id=user_id,
        quote_id=saved.id,
        input_fingerprint=fingerprint,
        idempotency_key="discovery:" + payload_fingerprint(payload.idempotency_key),
        source_id=identity,
        route=route,
        policy=configured.policy,
        budgets=configured.budgets,
        dispatch_topic="employer.search",
        dispatch_payload={"search_id": identity},
    )
    db.add(
        CreditDiscoveryBatch(
            search_id=identity,
            user_id=user_id,
            hold_id=hold.id,
            quote_id=saved.id,
            request_fingerprint=_intent(payload),
        )
    )
    delivery_history._progress(db, user_id)
    db.commit()
    return status(db, user_id=user_id, search_id=identity)


def _cancel(db, user_id: int, identity: str, *, reason: str):
    admissions.lock_application_set(db, user_id)
    service._lock_owner(db, user_id)
    batch = _batch(db, user_id, identity)
    row = employer._bounded_row(db, user_id, identity, lock=True)
    if row.status == "preparing":
        execution.settle_delivery(db, user_id=user_id, hold_id=batch.hold_id, delivered=False)
        row.status = "cancelled" if reason == "cancelled_by_owner" else "failed"
        row.refunded_credits, row.charged_credits, row.delivered_count = 1, 0, 0
        row.items = []
        row.scope = dict(row.scope) | {
            "failure_reason": reason,
            "lease_token": None,
            "lease_until": None,
        }
        db.commit()
    return status(db, user_id=user_id, search_id=identity)


def cancel(
    db: Session, *, user_id: int, search_id: str, reason: str = "cancelled_by_owner"
) -> DiscoveryBatchView:
    return _cancel(db, user_id, search_id, reason=reason)


def maintenance(db: Session) -> None:
    """One abandoned batch per existing source-maintenance turn; never rescan."""
    row = (
        db.query(employer_models.EmployerSearch.id, employer_models.EmployerSearch.user_id)
        .filter(
            employer_models.EmployerSearch.status == "preparing",
            employer_models.EmployerSearch.scope["bounded_protocol"].as_string() == PROTOCOL,
            employer_models.EmployerSearch.created_at < utcnow() - timedelta(minutes=30),
        )
        .order_by(employer_models.EmployerSearch.created_at, employer_models.EmployerSearch.id)
        .limit(1)
        .first()
    )
    db.commit()
    if row is not None:
        _cancel(db, row.user_id, row.id, reason="abandoned_without_completion")


def _claim(db, user_id: int, identity: str):
    admissions.lock_application_set(db, user_id)
    service._lock_owner(db, user_id)
    batch = _batch(db, user_id, identity)
    row = employer._bounded_row(db, user_id, identity, lock=True)
    hold = (
        db.query(CreditExecutionHold)
        .filter_by(id=batch.hold_id, user_id=user_id)
        .populate_existing()
        .with_for_update()
        .one()
    )
    if row.status != "preparing" or hold.delivery_state != "pending":
        return row, None, hold
    if pricing.aware(row.created_at) < utcnow() - timedelta(minutes=30):
        _cancel(db, user_id, identity, reason="expired_before_delivery")
        return row, None, hold
    if hold.state != "reserved":
        # Native work is single-claim. A crash or lost callback cannot initiate
        # another unbudgeted scan; a stale claim is refunded, money stays held.
        if pricing.aware(row.created_at) < utcnow() - timedelta(minutes=5):
            _cancel(db, user_id, identity, reason="interrupted_execution_requires_review")
        return row, None, hold
    token = public_id("dscan")
    hold.state = "running"
    row.scope = dict(row.scope) | {
        "lease_token": token,
        "lease_until": (utcnow() + timedelta(minutes=5)).isoformat(),
    }
    db.commit()
    return row, token, hold


def _finish(db, *, user_id: int, identity: str, token: str, prepared):
    # Acquire the same source/history/owner fences as existing paid discovery.
    admissions.lock_application_set(db, user_id)
    row = employer._bounded_row(db, user_id, identity, lock=True)
    if row is None or row.status != "preparing" or row.scope.get("lease_token") != token:
        db.rollback()
        return status(db, user_id=user_id, search_id=identity)
    employer._fence_bounded_results(db, row, prepared)
    progress = delivery_history._progress(db, user_id)
    catalogue_search.lock_owner_nowait(db, user_id)
    batch = _batch(db, user_id, identity)
    if (
        progress.generation != prepared.history_generation
        or row.history_revision != prepared.search_revision
        or row.scope.get("staged_body_fingerprint") != prepared.body_fingerprint
    ):
        raise _reject("Delivery history changed during preparation", created=True)
    keys = [posting.opening_key for posting, _, _ in prepared.selected]
    ids = [posting.id for posting, _, _ in prepared.selected]
    previous, physical, history_state = delivery_history.membership(db, user_id, keys, ids)
    if any(
        posting.opening_key in previous or posting.id in physical
        for posting, _, _ in prepared.selected
    ):
        raise _reject("A selected opening was already delivered", created=True)
    if prepared.selected and history_state != "ready":
        raise _reject("Paid history requires review before another delivery", created=True)
    if not 0 <= len(prepared.items) <= row.desired_count <= 5 or len(prepared.items) != len(
        prepared.selected
    ):
        raise ValueError("Discovery batch exceeded its accepted result count")
    for ordinal, ((posting, source, _), item) in enumerate(
        zip(prepared.selected, prepared.items, strict=True)
    ):
        if (
            item["charged_credits"] != 0
            or not item.get("included_in_batch")
            or item["posting"]["opening_key"] != posting.opening_key
        ):
            raise ValueError("Batch item monetary/proof contract changed")
        db.add(
            CreditDiscoveryDelivery(
                user_id=user_id,
                opening_key=posting.opening_key,
                search_id=identity,
                posting_id=posting.id,
                ordinal=ordinal,
                item_digest=payload_fingerprint(item),
                identity_proof={
                    "recipe_version": delivery_history.IDENTITY_RECIPE_VERSION,
                    "source_id": source.id,
                    "platform": source.platform,
                    "region": source.region,
                    "tenant": source.board_token,
                    "verified_employer_key": source.employer_key,
                    "requisition_id": posting.requisition_id,
                    "external_id": posting.external_id,
                },
            )
        )
    count = len(prepared.items)
    execution.settle_delivery(db, user_id=user_id, hold_id=batch.hold_id, delivered=count > 0)
    row.status, row.delivered_count = "completed", count
    row.charged_credits, row.refunded_credits = int(count > 0), int(count == 0)
    row.scope = (
        dict(row.scope)
        | prepared.scope_facts
        | {
            "lease_token": None,
            "lease_until": None,
            "partial_batch": 0 < count < row.desired_count,
            "new_qualifying_count": count,
            "batch_charge_policy": "One credit only when this completed batch contains at least one new verified job.",
        }
    )
    progress.generation += int(count > 0)
    db.commit()
    return status(db, user_id=user_id, search_id=identity)


def execute(db: Session, *, user_id: int, search_id: str) -> DiscoveryBatchView:
    row, token, hold = _claim(db, user_id, search_id)
    if token is None:
        return status(db, user_id=user_id, search_id=search_id)
    try:
        if not employer_config.discovery_enabled():
            return _cancel(db, user_id, search_id, reason="discovery_disabled_before_delivery")
        payload = DiscoveryRequest.model_validate(row.query)
        _, fingerprint, _, revision = _source_snapshot(db, user_id, payload)
        if fingerprint != row.input_fingerprint or revision != row.scope["resume_source_revision"]:
            raise ValueError("Accepted discovery inputs changed")
        route = service_quotes.QualifiedRoute.model_validate(hold.snapshot["route"])
        policy = finance.FinancialPolicy.model_validate(hold.snapshot["policy"])
        service_quotes.require_route(route, policy, now=utcnow())
        worker_payload = employer_schemas.SearchCreate.model_validate(
            _input(payload) | {"idempotency_key": "unified-worker"}
        )
        prepared = employer._prepare_bounded_search(
            db,
            row,
            worker_payload,
            only_new=True,
            maximum_candidate_bytes=route.supported_limits["candidate_content_bytes"],
        )
        for item in prepared.items:
            item["included_in_batch"], item["billing_currency"] = True, "credits"
        prepared.body_fingerprint = payload_fingerprint(prepared.items)
        if employer._stage_bounded_search(db, user_id, search_id, token, prepared) is None:
            return status(db, user_id=user_id, search_id=search_id)
        return _finish(db, user_id=user_id, identity=search_id, token=token, prepared=prepared)
    except Exception:
        db.rollback()
        # No native metering receipt exists here. Customer credits return while
        # every incurred/unknown backend expense remains reserved for accounting.
        return _cancel(db, user_id, search_id, reason="delivery_failed_before_completion")
