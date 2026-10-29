"""Owned unified-credit transport. Legacy payment callbacks remain authoritative."""
import base64
import json
import os
from datetime import UTC, datetime
from typing import Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import and_, or_
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session

from ..billing.purchase_quote import purchase_request_id
from ..billing.razorpay import RazorpayAdapter, RazorpayProviderError, RazorpaySettings
from ..database import get_db
from ..domains.analysis.schemas import AnalysisRunCreate, AnalysisRunResponse
from ..domains.credits import payments, pricing, release, service
from ..domains.credits.availability import availability_view
from ..domains.credits.availability import route_available as _route_available
from ..domains.credits.discovery_schemas import (
    DiscoveryBatchView,
    DiscoveryCreate,
    DiscoveryRequest,
)
from ..domains.credits.finance import FundingUnavailable
from ..domains.credits.models import CreditEvent, CreditGrant
from ..domains.credits.schemas import (
    CreditAvailabilityView,
    CreditCatalogView,
    CreditCheckoutView,
    CreditHistoryView,
    CreditOrderStatusView,
    CreditWalletView,
    PurchaseQuoteRequest,
    PurchaseQuoteView,
    ServiceQuoteView,
)
from ..domains.credits.synchronous import SynchronousServiceRequest
from ..models import EntitlementLedger, PaymentOrder, User
from ..rate_limiter import limiter
from ..security import get_current_user
from .billing import (
    OPEN_ORDER_STATUSES,
    UNCERTAIN_ORDER_STATUSES,
    _normalized_order_status,
    _order_status_required,
)

router = APIRouter(prefix="/v1/credits", tags=["credits"])
public_router = APIRouter(prefix="/public/credits", tags=["public-credits"])


class CanaryChoiceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    review_id: str = Field(pattern=r"^[a-z][a-z0-9-]{7,47}$")
    review_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    notice_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    choice: Literal["opt_in", "revoke"]
    expected_sequence: int = Field(strict=True, ge=0, le=2**31 - 2)
    confirmed_notice: bool = Field(strict=True, default=False)
    idempotency_key: str = Field(min_length=8, max_length=160)


class CanaryEnrollmentView(BaseModel):
    status: Literal["NOT_AVAILABLE", "NOT_OPTED_IN", "OPTED_IN"]
    review_id: str | None = None
    review_sha256: str | None = None
    notice: str | None = None
    notice_sha256: str | None = None
    expires_at: datetime | None = None
    runtime_sha256: str | None = None
    sequence: int | None = Field(default=None, strict=True, ge=0, le=2**31 - 1)
    can_opt_in: bool | None = None
    execution_flag_enabled: bool | None = None
    accepted_work_survives_revocation: bool | None = None


class CanaryChoiceView(BaseModel):
    status: Literal["CHOICE_RECORDED"]
    choice: Literal["opt_in", "revoke"]
    sequence: int
    review_sha256: str
    idempotency_key: str


@router.get("/canary-enrollment", response_model=CanaryEnrollmentView, response_model_exclude_none=True)
def canary_enrollment(response: Response,
                      review_id: str | None = Query(default=None, pattern=r"^[a-z][a-z0-9-]{7,47}$"),
                      review_sha256: str | None = Query(default=None, pattern=r"^[a-f0-9]{64}$"),
                      db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    from ..domains.credits import canary
    from ..domains.credits.runtime import RuntimeConfig

    _headers(response)
    if (review_id is None) != (review_sha256 is None):
        raise HTTPException(422, "Select both the review identity and its exact digest")
    try:
        try:
            configured = RuntimeConfig.model_validate_json(os.getenv("UNIFIED_CREDIT_RUNTIME_REVIEW_JSON", ""))
        except ValueError:
            configured = None
        return canary.enrollment(db, user_id=current_user.id, runtime=configured,
                                 review_id=review_id, review_sha256=review_sha256)
    except (FundingUnavailable, ValueError):
        return {"status": "NOT_AVAILABLE"}


@router.post("/canary-enrollment", response_model=CanaryChoiceView)
@limiter.limit("10/minute")
def choose_canary(payload: CanaryChoiceRequest, request: Request, response: Response,
                  db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    from ..domains.credits import canary
    from ..domains.credits.runtime import RuntimeConfig

    _headers(response)
    if payload.choice == "opt_in" and payload.confirmed_notice is not True:
        raise HTTPException(422, "Review the canary notice and explicitly choose participation")
    try:
        configured = (RuntimeConfig.model_validate_json(os.getenv("UNIFIED_CREDIT_RUNTIME_REVIEW_JSON", ""))
                      if payload.choice == "opt_in" else None)
        receipt = canary.choose(db, user_id=current_user.id, review_id=payload.review_id,
            review_sha256=payload.review_sha256, notice_sha256=payload.notice_sha256,
            choice=payload.choice, idempotency_key=payload.idempotency_key,
            expected_sequence=payload.expected_sequence, runtime=configured)
        db.commit()
        return {"status": "CHOICE_RECORDED", "choice": receipt["choice"], "sequence": receipt["sequence"],
                "review_sha256": receipt["review_sha256"], "idempotency_key": payload.idempotency_key}
    except (FundingUnavailable, ValueError):
        db.rollback()
        raise HTTPException(409, "Canary participation could not be confirmed; read its current status") from None


def _headers(response: Response) -> None:
    response.headers["Cache-Control"] = "no-store, private"


def _purchase_enabled(user_id: int | None = None, email: str = "", *, db: Session | None = None) -> bool:
    if not release.checkout_ready():
        return False
    if user_id is None and os.getenv("UNIFIED_CREDIT_EXECUTION_AUDIENCE", "canary").strip().lower() != "reviewed_public":
        return False
    from ..domains.credits.runtime import current_runtime
    try:
        config = current_runtime(user_id, db=db)
        if config.policy != release.current_review().policy:
            return False
        from ..domains.credits.service_quotes import require_route
        for route in config.routes:
            require_route(route, config.policy, now=datetime.now(UTC))
        return bool(_service_offers(user_id, email, db=db))
    except FundingUnavailable:
        return False


def _service_offers(user_id: int | None = None, email: str = "", *, db: Session | None = None) -> list[dict]:
    from ..domains.credits.runtime import current_runtime
    from ..domains.credits.service_quotes import require_route

    if user_id is None and os.getenv("UNIFIED_CREDIT_EXECUTION_AUDIENCE", "canary").strip().lower() != "reviewed_public":
        return []
    try:
        config = current_runtime(user_id, db=db)
        offers = []
        for route in config.routes:
            require_route(route, config.policy, now=datetime.now(UTC))
            if not _route_available(route, user_id, email):
                continue
            offers.append({"operation": route.operation, "credits": route.credits,
                "deliverable": route.deliverable, "supported_limits": route.supported_limits,
                "source_formats": list(route.supported_source_formats), "route_version": route.version})
        return sorted(offers, key=lambda offer: offer["operation"])
    except FundingUnavailable:
        return []


def _catalog(db: Session, user_id: int | None = None, email: str = "") -> CreditCatalogView:
    if not release.rollout_enabled():
        raise HTTPException(404, "Unified credits are not available yet.")
    try:
        result = pricing.public_catalog(campaign=service.configured_campaign(db), now=datetime.now(UTC),
                                        checkout_enabled=_purchase_enabled(user_id, email, db=db))
        return CreditCatalogView.model_validate(result.model_dump() | {"eligible_services": _service_offers(user_id, email, db=db)})
    except pricing.CreditContractError:
        raise HTTPException(503, "Credit pricing is temporarily unavailable.") from None


@public_router.get("/catalog", response_model=CreditCatalogView)
def public_catalog(response: Response, db: Session = Depends(get_db)):
    _headers(response)
    return _catalog(db)


@router.get("/catalog", response_model=CreditCatalogView)
def catalog(response: Response, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    _headers(response)
    return _catalog(db, current_user.id, current_user.email)


@router.post("/purchase-quotes", response_model=PurchaseQuoteView)
@limiter.limit("10/minute")
def purchase_quote(payload: PurchaseQuoteRequest, request: Request, response: Response,
                   db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    _headers(response)
    if not _purchase_enabled(current_user.id, current_user.email, db=db):
        raise HTTPException(503, "Credit purchases are not available yet.")
    try:
        offers = _service_offers(current_user.id, current_user.email, db=db)
        if not offers:
            raise FundingUnavailable("No qualified service is available for this pass.")
        quote = service.issue_purchase_quote(db, user_id=current_user.id, sku=payload.sku,
                                             idempotency_key=payload.idempotency_key, checkout_enabled=True,
                                             eligible_services=offers)
        view = service.quote_response(quote)
        db.commit()
        return view
    except pricing.CreditContractError as exc:
        db.rollback()
        raise HTTPException(409, str(exc)) from None
    except FundingUnavailable:
        db.rollback()
        raise HTTPException(503, "Credit purchases are not available yet.") from None


@router.get("/wallet", response_model=CreditWalletView)
def wallet(response: Response, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    _headers(response)
    return service.wallet(db, user_id=current_user.id)


@router.get("/availability", response_model=CreditAvailabilityView)
def availability(response: Response, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    _headers(response)
    return availability_view(db, current_user)


@router.post("/service-quotes", response_model=ServiceQuoteView)
@limiter.limit("10/minute")
def analysis_quote(payload: AnalysisRunCreate, request: Request, response: Response,
                   idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
                   db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    from ..domains.credits.runtime import current_runtime
    from ..domains.credits.service_quotes import issue_service_quote, quote_view
    from ..feature_flags import decide_feature

    _headers(response)
    if payload.accepted_quote is not None or payload.accepted_service_quote_id is not None:
        raise HTTPException(422, "Request a quote before accepting it.")
    key = (idempotency_key or payload.idempotency_key or "").strip()
    if not 8 <= len(key) <= 160:
        raise HTTPException(422, "Provide an Idempotency-Key between 8 and 160 characters.")
    for feature in ("async_analysis", "evidence_tailoring") if payload.operation == "resume_tailor" else ("async_analysis",):
        if not decide_feature(feature, user_id=current_user.id, email=current_user.email).enabled:
            raise HTTPException(404, "This analysis is not enabled.")
    try:
        current_runtime(current_user.id, db=db)  # Refuse before inspecting a disabled route's source.
        from ..domains.credits.analysis import prepare_quote_context
        _, fingerprint, runtime, route = prepare_quote_context(db, user_id=current_user.id, payload=payload)
        if not _route_available(route, current_user.id, current_user.email):
            raise FundingUnavailable("This service is temporarily paused")
        quote = issue_service_quote(db, user_id=current_user.id, input_fingerprint=fingerprint,
                                    idempotency_key=key, route=route, policy=runtime.policy)
        result = quote_view(quote)
        db.commit()
        return result
    except FundingUnavailable:
        db.rollback()
        raise HTTPException(503, "This service is not ready for a new paid request.") from None
    except pricing.CreditContractError as exc:
        db.rollback()
        raise HTTPException(409, str(exc)) from None


class AnalysisReuseView(BaseModel):
    reused_run: AnalysisRunResponse | None


class SynchronousReuseView(AnalysisReuseView):
    result: dict | None


@router.post("/discovery-quotes", response_model=ServiceQuoteView)
@limiter.limit("10/minute")
def discovery_quote(payload: DiscoveryRequest, request: Request, response: Response,
                    idempotency_key: str = Header(alias="Idempotency-Key", min_length=8, max_length=160),
                    db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    from ..domains.credits import discovery
    from ..domains.credits.service_quotes import quote_view

    _headers(response)
    try:
        quote = discovery.quote(db, user_id=current_user.id, payload=payload, idempotency_key=idempotency_key)
        result = quote_view(quote)
        db.commit()
        return result
    except pricing.CreditContractError as exc:
        db.rollback()
        raise HTTPException(409, str(exc)) from None
    except FundingUnavailable:
        db.rollback()
        raise HTTPException(503, "Job discovery is not ready for a new paid request.") from None


@router.post("/discovery-batches", response_model=DiscoveryBatchView)
@limiter.limit("10/minute")
def create_discovery_batch(payload: DiscoveryCreate, request: Request, response: Response,
                           db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    from ..domains.credits import discovery

    _headers(response)
    try:
        return discovery.create(db, user_id=current_user.id, payload=payload)
    except pricing.CreditContractError as exc:
        db.rollback()
        raise HTTPException(409, str(exc)) from None
    except FundingUnavailable:
        db.rollback()
        raise HTTPException(503, "Job discovery is not ready for a new paid request.") from None


@router.get("/discovery-batches/{search_id}", response_model=DiscoveryBatchView)
def discovery_batch_status(search_id: str, response: Response,
                           db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    from ..domains.credits import discovery

    _headers(response)
    return discovery.status(db, user_id=current_user.id, search_id=search_id)


@router.post("/discovery-batches/{search_id}/cancel", response_model=DiscoveryBatchView)
@limiter.limit("10/minute")
def cancel_discovery_batch(search_id: str, request: Request, response: Response,
                          db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    from ..domains.credits import discovery

    _headers(response)
    return discovery.cancel(db, user_id=current_user.id, search_id=search_id)


@router.post("/synchronous-reuse", response_model=SynchronousReuseView)
@limiter.limit("30/minute")
def synchronous_reuse(payload: SynchronousServiceRequest, request: Request, response: Response,
                      idempotency_key: str | None = Header(default=None, alias="Idempotency-Key", min_length=8, max_length=160),
                      db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    from ..domains.credits.synchronous import lookup_result

    _headers(response)
    return lookup_result(db, user_id=current_user.id, payload=payload, idempotency_key=idempotency_key)


@router.post("/synchronous-service-quotes", response_model=ServiceQuoteView)
@limiter.limit("10/minute")
def synchronous_quote(payload: SynchronousServiceRequest, request: Request, response: Response,
                      idempotency_key: str = Header(alias="Idempotency-Key", min_length=8, max_length=160),
                      db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    from ..domains.credits.service_quotes import issue_service_quote, quote_view
    from ..domains.credits.synchronous import prepare_quote_context

    _headers(response)
    try:
        _, fingerprint, config, route = prepare_quote_context(db, user_id=current_user.id, payload=payload)
        if not _route_available(route, current_user.id, current_user.email):
            raise FundingUnavailable("This service is temporarily paused")
        quote = issue_service_quote(db, user_id=current_user.id, input_fingerprint=fingerprint,
            idempotency_key=idempotency_key, route=route, policy=config.policy)
        result = quote_view(quote)
        db.commit()
        return result
    except FundingUnavailable:
        db.rollback()
        raise HTTPException(503, "This service is not ready for a new paid request.") from None
    except pricing.CreditContractError as exc:
        db.rollback()
        raise HTTPException(409, str(exc)) from None


@router.post("/analysis-reuse", response_model=AnalysisReuseView)
@limiter.limit("30/minute")
def analysis_reuse(payload: AnalysisRunCreate, request: Request, response: Response,
                   db: Session = Depends(get_db), current_user: User = Depends(get_current_user),
                   include_legacy: bool = False):
    """Quote preflight reuses only the current contract; legacy load is explicit."""
    from ..domains.analysis.service import _dependency_snapshot, validate_analysis_input
    from ..domains.common import payload_fingerprint
    from ..models import AnalysisRun

    _headers(response)
    if payload.accepted_quote is not None or payload.accepted_service_quote_id is not None:
        raise HTTPException(422, "A result lookup does not accept a payment quote.")
    normalized = dict(payload.input)
    validate_analysis_input(db, user_id=current_user.id, operation=payload.operation,
                            opportunity_id=payload.opportunity_id, payload=normalized)
    complete_match = payload.operation == "job_match" and normalized.get("mode") == "enhanced"
    paid_tailoring = payload.operation == "resume_tailor"
    # An old mega-match remains an owned historical read, not a current complete
    # matching deliverable. Its missing evidence/output contract cannot be reused
    # by a new paid preflight, even when old tailoring reads are requested.
    contexts = (True,) if complete_match else (
        ((True, False) if include_legacy else (True,)) if paid_tailoring else (False,))
    identities = [payload_fingerprint({"operation": payload.operation,
        "opportunity_id": payload.opportunity_id, "input": normalized,
        "dependencies": _dependency_snapshot(db, current_user.id, payload.operation, payload.opportunity_id, normalized,
            paid_context=paid)}) for paid in contexts]
    run = db.query(AnalysisRun).filter(
        AnalysisRun.user_id == current_user.id, AnalysisRun.operation == payload.operation,
        AnalysisRun.input_fingerprint.in_(identities),
        AnalysisRun.status.in_(["queued", "running", "succeeded"]), AnalysisRun.cancel_requested.is_(False),
        AnalysisRun.input_purged_at.is_(None), AnalysisRun.result_purged_at.is_(None),
    ).order_by(AnalysisRun.created_at.desc()).first()
    return {"reused_run": AnalysisRunResponse.model_validate(run).model_dump(mode="json") if run else None}


@router.get("/history", response_model=CreditHistoryView)
def history(response: Response, limit: int = Query(default=25, ge=1, le=50),
            cursor: str | None = Query(default=None, max_length=512),
            db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    _headers(response)
    rows = db.query(CreditEvent).filter_by(user_id=current_user.id)
    if cursor:
        try:
            date_text, identity = json.loads(base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)))
            stamp = datetime.fromisoformat(date_text)
            if stamp.tzinfo is None or not isinstance(identity, str) or len(identity) > 64:
                raise ValueError("invalid cursor")
        except (ValueError, TypeError, KeyError):
            raise HTTPException(400, "Invalid history cursor.") from None
        rows = rows.filter(or_(CreditEvent.created_at < stamp,
                              and_(CreditEvent.created_at == stamp, CreditEvent.id < identity)))
    result = rows.order_by(CreditEvent.created_at.desc(), CreditEvent.id.desc()).limit(limit + 1).all()
    items = result[:limit]
    next_cursor = None
    if len(result) > limit:
        last = items[-1]
        next_cursor = base64.urlsafe_b64encode(json.dumps([pricing.aware(last.created_at).isoformat(), last.id]).encode()).decode().rstrip("=")
    return {"schema_version": "unified-credits-v1", "items": [
        {"id": row.id, "event_type": row.event_type, "amount": row.amount,
         "balance_after": row.balance_after, "created_at": pricing.aware(row.created_at),
         "details": row.details} for row in items], "next_cursor": next_cursor}


class CreateCreditOrder(BaseModel):
    model_config = ConfigDict(extra="forbid")
    quote_id: str = Field(min_length=5, max_length=64)
    billing_country: Literal["IN"]
    idempotency_key: str = Field(min_length=8, max_length=160, pattern=r"^[A-Za-z0-9_.:-]+$")
    order_reference: str | None = Field(default=None, min_length=5, max_length=64)


def _owned_order(db: Session, owner: int, reference: str) -> PaymentOrder:
    order = db.query(PaymentOrder).filter_by(user_id=owner, public_id=reference,
                                            entitlement_kind="credit_pass").one_or_none()
    if order is None:
        raise HTTPException(404, "Order not found.")
    return order


def _offers_remain_available(view: PurchaseQuoteView, user_id: int, email: str, *, db: Session | None = None) -> bool:
    current = _service_offers(user_id, email, db=db)
    return bool(view.eligible_services) and all(
        offer.model_dump(mode="json") in current for offer in view.eligible_services)


def _status(db: Session, order: PaymentOrder, email: str) -> dict:
    view = payments.order_quote(order)
    grant = db.query(CreditGrant).filter_by(user_id=order.user_id, source_type="payment_order",
                                          source_id=order.public_id).one_or_none()
    entitlement = db.query(EntitlementLedger).filter_by(source_order_id=order.id, status="active").first()
    return {"schema_version": "unified-credits-v1", "order_id": order.public_id,
            "provider_order_id": order.provider_order_id, "status": _normalized_order_status(order),
            "payment_status": order.status, "fulfilled": bool(grant and entitlement),
            "checkout_available": bool(_purchase_enabled(order.user_id, email, db=db) and order.status == "created"
                and order.provider_order_id and _offers_remain_available(view, order.user_id, email, db=db)),
            "amount_minor": order.gross_amount_minor, "refunded_amount_minor": order.refunded_amount_minor,
            "currency": order.currency, "sku": order.sku, "created_at": pricing.aware(order.created_at),
            "paid_at": pricing.aware(order.paid_at) if order.paid_at else None,
            "pass_expires_at": pricing.aware(grant.expires_at) if grant and grant.expires_at else None,
            "accepted_quote": view.model_dump(mode="json")}


@router.get("/recent-order", response_model=CreditOrderStatusView | None)
def recent_order(response: Response, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    _headers(response)
    order = db.query(PaymentOrder).filter_by(user_id=current_user.id, entitlement_kind="credit_pass").order_by(PaymentOrder.created_at.desc()).first()
    return _status(db, order, current_user.email) if order else None


@router.get("/orders/{reference}", response_model=CreditOrderStatusView)
def order_status(reference: str, response: Response, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    _headers(response)
    return _status(db, _owned_order(db, current_user.id, reference), current_user.email)


def _checkout(order: PaymentOrder, user: User, settings: RazorpaySettings) -> dict:
    if not order.provider_order_id or order.provider_key_id != settings.key_id or order.provider_mode != settings.mode:
        raise _order_status_required(order)
    view = payments.order_quote(order)
    return {"schema_version": "unified-credits-v1", "order_id": order.public_id,
            "provider": "razorpay", "provider_order_id": order.provider_order_id, "key_id": settings.key_id,
            "amount_minor": order.gross_amount_minor, "currency": "INR", "name": "HireWiz",
            "description": f"{view.credits} credits. 30-day prepaid pass; no automatic renewal.",
            "accepted_quote": view.model_dump(mode="json"), "prefill": {"email": user.email}}


@router.post("/orders", response_model=CreditCheckoutView)
@limiter.limit("5/minute")
def create_order(payload: CreateCreditOrder, request: Request, response: Response,
                 db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    _headers(response)
    if not _purchase_enabled(current_user.id, current_user.email, db=db):
        raise HTTPException(503, "Credit purchases are not available yet.")
    settings = RazorpaySettings.from_env()
    identity = payload.order_reference or purchase_request_id(current_user.id, "unified:" + payload.idempotency_key)
    try:
        original = db.query(PaymentOrder).filter_by(user_id=current_user.id, public_id=identity).with_for_update(nowait=True).first()
        if payload.order_reference and original is None:
            raise HTTPException(404, "Order not found.")
        if original:
            try:
                view = payments.order_quote(original)
            except pricing.CreditContractError:
                raise _order_status_required(original) from None
            if view.quote_id != payload.quote_id or original.status != "created":
                raise _order_status_required(original)
            if not _offers_remain_available(view, current_user.id, current_user.email, db=db):
                raise _order_status_required(original)
            return _checkout(original, current_user, settings)
        user = db.query(User).filter_by(id=current_user.id).with_for_update().one()
        # Prevent a second payable attempt, including an unknown legacy order.
        pending = db.query(PaymentOrder).filter(PaymentOrder.user_id == user.id,
            PaymentOrder.status.in_(OPEN_ORDER_STATUSES | {"paid_unfulfilled"})).with_for_update(nowait=True).all()
        for order in pending:
            if order.provider_order_id or order.status in UNCERTAIN_ORDER_STATUSES:
                raise _order_status_required(order)
        review = release.current_review()
        quote = service.accept_purchase_quote(db, user_id=user.id, quote_id=payload.quote_id, order_id=identity)
        view = service.quote_response(quote)
        if not _offers_remain_available(view, current_user.id, current_user.email, db=db):
            raise pricing.CreditContractError("Supported services changed. Review a fresh purchase quote.")
        now = datetime.now(UTC)
        order = PaymentOrder(public_id=identity, user_id=user.id, provider="razorpay", provider_mode=settings.mode,
            provider_key_id=settings.key_id, sku=view.sku, catalog_version=view.catalog_version,
            cost_policy_snapshot={"unified_purchase_quote": dict(quote.snapshot), "release_review": review.model_dump(mode="json")},
            billing_type="one_time", entitlement_kind="credit_pass", entitlement_quantity=view.credits,
            billing_country="IN", billing_country_confirmed_at=now, gross_amount_minor=view.amount_minor,
            refunded_amount_minor=0, currency="INR", status="initializing", active_attempt_key=f"{user.id}:unified:{settings.mode}")
        db.add(order)
        db.commit()  # Durable identity and accepted quote precede any payable request.
    except (IntegrityError, OperationalError):
        db.rollback()
        raise HTTPException(409, "Another payment update is in progress. Check your original order.") from None
    except pricing.CreditContractError as exc:
        db.rollback()
        raise HTTPException(409, {"code": "credit_quote_rejected", "message": str(exc),
                                 "order_created": False}) from None
    except FundingUnavailable:
        db.rollback()
        raise HTTPException(503, {"code": "credit_release_unavailable", "order_created": False,
                                 "message": "Credit purchases await a current review."}) from None
    try:
        result = RazorpayAdapter(settings).create_order(product=payments.provider_product(view), local_order_id=identity, receipt=f"hw_{identity}")
    except RazorpayProviderError:
        # Reconciliation may have already bound/captured this order while the
        # original HTTP request was in flight. Never regress its durable state.
        order = db.query(PaymentOrder).filter_by(public_id=identity).populate_existing().with_for_update().one()
        if order.status == "initializing":
            order.status = "creation_unknown"
        db.commit()
        raise _order_status_required(order) from None
    order = db.query(PaymentOrder).filter_by(public_id=identity).populate_existing().with_for_update().one()
    if order.provider_order_id not in (None, result["provider_order_id"]):
        db.rollback()
        raise _order_status_required(order, "The original checkout needs provider reconciliation.")
    order.provider_order_id = result["provider_order_id"]
    if order.status in UNCERTAIN_ORDER_STATUSES:
        order.status = "created"
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "Check the original payment status before continuing.") from None
    if order.status != "created":
        raise _order_status_required(order)
    return _checkout(order, user, settings)
