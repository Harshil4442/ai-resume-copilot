"""Atomic credits, whole-path monetary exposure and existing durable outbox.

No route is enabled here. A qualified adapter must create its owned work record
in this same transaction, call admit(), then commit before dispatch. These guards
share legacy financial locks and conservatively include retained legacy exposure.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from fractions import Fraction
from typing import Annotated

from pydantic import Field, model_validator
from sqlalchemy import Integer, case, cast, func, or_, text
from sqlalchemy.orm import Session

from ...database import Base
from ...models import ModelCostLiability
from ...services.model_cost_policy import quoted_cost
from ..common import public_id
from ..dispatch.service import enqueue
from ..employer.models import ServiceCreditReservation
from . import finance, pricing, service
from .models import (
    CreditAllocation,
    CreditExecutionAttempt,
    CreditExecutionHold,
    CreditGrant,
    CreditServiceQuote,
)
from .service_quotes import QualifiedRoute, require_route

Positive = Annotated[int, Field(strict=True, gt=0, le=10**12)]


class ExecutionBudgetPolicy(finance.Closed):
    version: str = Field(min_length=1, max_length=120)
    evidence_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    valid_from: datetime
    expires_at: datetime
    daily_global_minor: Positive
    monthly_global_minor: Positive
    monthly_account_minor: Positive
    monthly_incident_minor: Positive
    monthly_route_minor: dict[str, Positive] = Field(min_length=1)
    legacy_search_cost_minor: Positive
    legacy_application_cost_minor: Positive
    legacy_preparation_minor: Positive
    legacy_provider_attempt_overhead_minor: int = Field(strict=True, ge=0)

    @model_validator(mode="after")
    def reviewed_budgets(self):
        if self.valid_from.tzinfo is None or self.expires_at.tzinfo is None:
            raise ValueError("Budget dates must be timezone-aware")
        if not self.valid_from < self.expires_at <= self.valid_from + timedelta(days=31):
            raise ValueError("Funding budgets require a review within 31 days")
        if (
            max(
                self.daily_global_minor,
                self.monthly_account_minor,
                self.monthly_incident_minor,
                *self.monthly_route_minor.values(),
            )
            > self.monthly_global_minor
        ):
            raise ValueError("Funded sub-budgets cannot exceed the global monthly budget")
        return self


def lock_money(db: Session) -> None:
    """Same ordered locks as existing provider and service money admission."""
    if db.get_bind().dialect.name == "postgresql":
        db.execute(text("SELECT pg_advisory_xact_lock(7349186502109)"))
        db.execute(text("SELECT pg_advisory_xact_lock(7349186502110)"))


def _maximum(db, left, right):
    return (
        func.max(left, right)
        if db.get_bind().dialect.name == "sqlite"
        else func.greatest(left, right)
    )


def _hold_amount(db):
    actual = func.coalesce(CreditExecutionHold.settled_minor, 0)
    return case(
        (CreditExecutionHold.state == "settled", actual),
        else_=_maximum(db, CreditExecutionHold.reserved_minor, actual),
    )


def _hold_usage(
    db,
    *,
    start: datetime,
    owner: int | None = None,
    route: str | None = None,
    incident: bool = False,
) -> int:
    rows = db.query(func.coalesce(func.sum(_hold_amount(db)), 0)).filter(
        or_(CreditExecutionHold.state != "settled", CreditExecutionHold.created_at >= start)
    )
    if owner is not None:
        rows = rows.filter(CreditExecutionHold.user_id == owner)
    if route is not None:
        rows = rows.filter(CreditExecutionHold.route_id == route)
    if incident:
        rows = rows.filter(
            or_(
                CreditExecutionHold.delivery_state != "delivered",
                CreditExecutionHold.funding_kind != "prepaid",
            )
        )
    used = int(rows.scalar() or 0)
    # The optional free-core domain shares this guard and its money locks.
    # Its holds are shared infrastructure funding, never paid incident funding.
    table = Base.metadata.tables.get("free_core_reservations")
    if table is not None and not incident:
        actual = func.coalesce(table.c.settled_minor, 0)
        amount = case(
            (table.c.state == "settled", actual), else_=_maximum(db, table.c.reserved_minor, actual)
        )
        free = db.query(func.coalesce(func.sum(amount), 0)).filter(
            or_(table.c.state != "settled", table.c.created_at >= start)
        )
        if owner is not None:
            free = free.filter(table.c.user_id == owner)
        if route is not None:
            free = free.filter(table.c.route_id == route)
        used += int(free.scalar() or 0)
    return used


def _legacy_usage(
    db,
    *,
    start: datetime,
    policy: finance.FinancialPolicy,
    budgets: ExecutionBudgetPolicy,
    exclude_service_reservation_id: str | None = None,
) -> int:
    # New attempts already belong to whole-operation holds. Count legacy
    # liabilities once, including unknown costs from any earlier period.
    unresolved = ModelCostLiability.cost_state.notin_(["settled", "overrun"])
    held = func.coalesce(ModelCostLiability.reserved_cost_micros, 0)
    paid = func.coalesce(ModelCostLiability.settled_cost_micros, 0)
    amount = case((unresolved, _maximum(db, held, paid)), else_=paid)
    rows = db.query(func.coalesce(func.sum(amount), 0), func.count(ModelCostLiability.id)).filter(
        ~ModelCostLiability.id.in_(db.query(CreditExecutionAttempt.liability_id)),
        or_(unresolved, ModelCostLiability.created_at >= start),
    )
    micros, attempts = rows.one()
    provider = finance.ceil_minor(
        Fraction(
            int(micros or 0)
            * policy.adverse_fx_minor_per_usd
            * (10_000 + policy.unrecoverable_provider_tax_bps),
            10_000 * 1_000_000,
        )
    )
    provider += int(attempts) * budgets.legacy_provider_attempt_overhead_minor
    # Keep old service liabilities even after erasure. Their accepted policy
    # supplies the estimate; missing historical fields use reviewed fallback.
    snapshot = ServiceCreditReservation.cost_policy_snapshot
    search = func.coalesce(
        cast(snapshot["policy"]["search_cost_minor"].as_string(), Integer),
        budgets.legacy_search_cost_minor,
    )
    application = func.coalesce(
        cast(snapshot["policy"]["application_cost_minor"].as_string(), Integer),
        budgets.legacy_application_cost_minor,
    )
    preparation = func.coalesce(
        cast(snapshot["policy"]["service_preparation_cost_minor"].as_string(), Integer),
        budgets.legacy_preparation_minor,
    )
    historical = case(
        (
            ServiceCreditReservation.operation == "job_search",
            _maximum(db, search * ServiceCreditReservation.requested_count, preparation),
        ),
        else_=application * ServiceCreditReservation.requested_count,
    )
    explicit = func.coalesce(
        cast(snapshot["legacy_service_hold_minor"].as_string(), Integer), 0
    ) + func.coalesce(cast(snapshot["failed_service_hold_minor"].as_string(), Integer), 0)
    services_query = db.query(
        func.coalesce(func.sum(_maximum(db, historical, explicit)), 0)
    ).filter(
        or_(
            ServiceCreditReservation.state == "reserved",
            ServiceCreditReservation.created_at >= start,
        )
    )
    if exclude_service_reservation_id:
        services_query = services_query.filter(
            ServiceCreditReservation.id != exclude_service_reservation_id
        )
    services = services_query.scalar()
    return provider + int(services or 0)


def assert_headroom(
    db: Session,
    *,
    user_id: int,
    route_id: str,
    required_minor: int,
    policy: finance.FinancialPolicy,
    budgets: ExecutionBudgetPolicy,
    now: datetime,
    funding_kind: str = "prepaid",
) -> None:
    """Also callable by the legacy-admission integration while holding money locks."""
    service._count(required_minor)
    if funding_kind not in {"prepaid", "promotion", "shared"}:
        raise finance.FundingUnavailable("An explicit funded pool is required")
    if not budgets.valid_from <= now < budgets.expires_at:
        raise finance.FundingUnavailable("The execution budget needs a current funding review")
    if route_id not in budgets.monthly_route_minor:
        raise finance.FundingUnavailable("This route has no funded execution budget")
    lock_money(db)
    outcomes = [
        row[0]
        for row in db.query(CreditExecutionHold.delivery_state)
        .filter(
            CreditExecutionHold.route_id == route_id,
            CreditExecutionHold.delivery_state.in_(["delivered", "failed"]),
        )
        .order_by(CreditExecutionHold.created_at.desc(), CreditExecutionHold.id.desc())
        .limit(20)
        .all()
    ]
    if (len(outcomes) >= 3 and all(value == "failed" for value in outcomes[:3])) or (
        len(outcomes) == 20 and outcomes.count("failed") > 2
    ):
        raise finance.FundingUnavailable("This route is paused for repeated delivery failures")
    day = now.replace(hour=0, minute=0, second=0, microsecond=0)
    month = day.replace(day=1)
    for start, ceiling in (
        (day, budgets.daily_global_minor),
        (month, budgets.monthly_global_minor),
    ):
        used = _hold_usage(db, start=start) + _legacy_usage(
            db, start=start, policy=policy, budgets=budgets
        )
        if used + required_minor > ceiling:
            raise finance.FundingUnavailable("The funded global execution budget is exhausted")
    checks = (
        (_hold_usage(db, start=month, owner=user_id), budgets.monthly_account_minor, "account"),
        (
            _hold_usage(db, start=month, route=route_id),
            budgets.monthly_route_minor[route_id],
            "route",
        ),
        (_hold_usage(db, start=month, incident=True), budgets.monthly_incident_minor, "incident"),
    )
    for used, ceiling, label in checks:
        if label == "incident" and funding_kind == "shared":
            continue
        if used + required_minor > ceiling:
            raise finance.FundingUnavailable(f"The funded {label} execution budget is exhausted")


def admit(
    db: Session,
    *,
    user_id: int,
    quote_id: str,
    input_fingerprint: str,
    idempotency_key: str,
    source_id: str,
    route: QualifiedRoute,
    policy: finance.FinancialPolicy,
    budgets: ExecutionBudgetPolicy,
    dispatch_topic: str | None = None,
    dispatch_payload: dict[str, str] | None = None,
    inline: bool = False,
    commercial: bool = False,
    now: datetime | None = None,
) -> CreditExecutionHold:
    """Caller owns commit; any failure must roll back the entire transaction."""
    if inline:
        if dispatch_topic is not None or dispatch_payload is not None:
            raise pricing.CreditContractError("Inline execution cannot also enqueue worker intent")
    elif dispatch_topic is None or dispatch_payload is None:
        raise pricing.CreditContractError("Worker execution requires its complete dispatch intent")
    if dispatch_payload is not None and (
        not dispatch_payload or any(value != source_id for value in dispatch_payload.values())
    ):
        raise pricing.CreditContractError("Dispatch intent must reference only its owned source")
    stamp = service._stamp(now)
    service._lock_owner(db, user_id)
    quote = (
        db.query(CreditServiceQuote)
        .filter_by(id=quote_id, user_id=user_id)
        .with_for_update()
        .populate_existing()
        .one_or_none()
    )
    if quote is None or quote.input_fingerprint != input_fingerprint:
        raise pricing.CreditContractError("The service quote does not match these inputs")
    if quote.accepted_reservation_id:
        hold = (
            db.query(CreditExecutionHold)
            .filter_by(reservation_id=quote.accepted_reservation_id, user_id=user_id)
            .one()
        )
        from .models import CreditReservation

        reservation = db.get(CreditReservation, quote.accepted_reservation_id)
        if (
            reservation.source_id != source_id
            or reservation.idempotency_key != idempotency_key
            or hold.snapshot.get("execution_mode", "worker") != ("inline" if inline else "worker")
        ):
            raise pricing.CreditContractError("The quote already belongs to another operation")
        return hold
    if not pricing.aware(quote.created_at) <= stamp < pricing.aware(quote.expires_at):
        raise pricing.CreditContractError("The service quote expired; review a new quote")
    if quote.snapshot["route"] != route.model_dump(mode="json") or quote.snapshot[
        "policy"
    ] != policy.model_dump(mode="json"):
        raise pricing.CreditContractError(
            "The qualified route or financial policy changed; review a new quote"
        )
    required = require_route(route, policy, now=stamp, commercial=commercial)
    if required != quote.snapshot["execution_required_minor"]:
        raise pricing.CreditContractError("The execution envelope needs reconciliation")
    assert_headroom(
        db,
        user_id=user_id,
        route_id=route.id,
        required_minor=required,
        policy=policy,
        budgets=budgets,
        now=stamp,
    )
    reservation = service.reserve_credits(
        db,
        user_id=user_id,
        operation=route.operation,
        source_id=source_id,
        idempotency_key=idempotency_key,
        credits=route.credits,
        input_fingerprint=input_fingerprint,
        quote_snapshot={
            "service_quote_id": quote.id,
            "route_version": route.version,
            "tariff_version": route.tariff_version,
            "credits": route.credits,
        },
        execution_allowance_minor=required,
        now=stamp,
    )
    kinds = {
        row[0]
        for row in db.query(CreditGrant.funding_kind)
        .join(CreditAllocation, CreditAllocation.grant_id == CreditGrant.id)
        .filter(CreditAllocation.reservation_id == reservation.id)
        .all()
    }
    funding = "prepaid" if kinds == {"paid"} else "promotion"
    group = public_id("fin")
    hold = CreditExecutionHold(
        id=public_id("ceh"),
        user_id=user_id,
        reservation_id=reservation.id,
        financial_group_id=group,
        route_id=route.id,
        funding_kind=funding,
        reserved_minor=required,
        native_reserved_minor=route.native_processing_minor + route.worker_processing_minor,
        state="reserved",
        delivery_state="pending",
        snapshot={
            "route": route.model_dump(mode="json"),
            "policy": policy.model_dump(mode="json"),
            "budgets": budgets.model_dump(mode="json"),
            "execution_mode": "inline" if inline else "worker",
        },
        created_at=stamp,
    )
    db.add(hold)
    db.flush([hold])
    import hashlib

    for ordinal, model in enumerate(route.model_quotes, 1):
        saved = model.model_dump(mode="json")
        usd_micros = quoted_cost(saved, model.max_input_tokens, model.max_output_tokens)
        liability = ModelCostLiability(
            id=public_id("mcl"),
            financial_group_id=group,
            attempt_number=ordinal,
            provider=model.provider,
            model=model.model,
            endpoint_key=hashlib.sha256(model.api_base.encode()).hexdigest(),
            currency="USD",
            pricing_quote=saved
            | {
                "expense_funding": funding,
                "expense_pool": "failed_work",
                "expense_policy_version": policy.version,
            },
            input_token_estimate=model.max_input_tokens,
            reserved_cost_micros=usd_micros,
            cost_state="planned",
            created_at=stamp,
        )
        db.add(liability)
        db.flush([liability])
        db.add(
            CreditExecutionAttempt(
                id=public_id("cea"),
                hold_id=hold.id,
                liability_id=liability.id,
                ordinal=ordinal,
                state="planned",
            )
        )
    quote.accepted_reservation_id, quote.accepted_at = reservation.id, stamp
    if not inline:
        assert dispatch_topic is not None and dispatch_payload is not None
        enqueue(
            db,
            topic=dispatch_topic,
            aggregate_id=source_id,
            payload=dispatch_payload,
            key=f"unified:{reservation.id}",
        )
    db.flush()
    return hold
