"""Reviewed estimated expense authority. No invoice, tax or profit claims.

All expense rounding is upward; net receipts are downward. Provider liabilities
remain held when their outcomes are unknown, including across budget periods.
"""

from __future__ import annotations

import hashlib
import json
import os
from datetime import UTC, datetime, timedelta
from fractions import Fraction
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class CostPolicyUnavailable(RuntimeError):
    pass


Money = Annotated[int, Field(strict=True, ge=0, le=10**12)]
Positive = Annotated[int, Field(strict=True, gt=0, le=10**12)]
Bps = Annotated[int, Field(strict=True, ge=0, lt=10_000)]
Identifier = Annotated[str, Field(min_length=1, max_length=64, pattern=r"^[a-zA-Z0-9_.-]+$")]


class Closed(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PackTerms(Closed):
    sku: Identifier
    gross_minor: Positive
    service_credits: Positive
    analysis_units: Positive
    # Every permitted promotion must be reviewed. Checkout currently accepts no
    # promotion code; these bound any future discount/bonus activation.
    max_discount_minor: Money = 0
    max_bonus_service_credits: Money = 0
    max_bonus_analysis_units: Money = 0


class ExpensePolicy(Closed):
    version: Identifier
    catalog_version: Identifier
    provenance: Literal["estimated", "invoice_reconciled", "synthetic_test"]
    reviewed_by: Identifier
    review_status: Literal["candidate", "approved"]
    evidence_references: list[Identifier] = Field(min_length=1, max_length=20)
    valid_from: datetime
    expires_at: datetime
    reconciled_through: datetime
    actual_variance_reviewed: Literal[True]
    currency: Literal["INR"]
    margin_bps: Bps
    contingency_bps: Bps
    sale_tax_reserve_bps: Bps
    gateway_fee_bps: Bps
    gateway_fee_tax_bps: Annotated[int, Field(strict=True, ge=0, le=10_000)]
    gateway_fixed_minor: Money
    settlement_fx_minor: Money
    refund_chargeback_reserve_bps: Bps
    all_enabled_payment_methods_covered: Literal[True]
    adverse_fx_minor_per_usd: Positive
    monthly_platform_operations_minor: Positive
    monthly_marketing_operations_minor: Positive
    minimum_paid_packs_per_month: Positive
    search_cost_minor: Positive
    service_preparation_cost_minor: Positive
    application_cost_minor: Positive
    analysis_overhead_minor_per_unit: Money
    provider_attempt_overhead_minor: Money
    analysis_unit_model_ceiling_micros: Positive
    operation_model_ceilings_micros: dict[Identifier, Positive]
    daily_model_budget_micros: Positive
    monthly_model_budget_micros: Positive
    monthly_promotional_model_budget_micros: Positive
    monthly_legacy_premium_model_budget_micros: Money
    monthly_legacy_service_budget_minor: Money
    service_search_credits: Positive
    service_apply_credits: Positive
    packs: list[PackTerms] = Field(min_length=3, max_length=3)

    @model_validator(mode="after")
    def finite_review(self):
        dates = (self.valid_from, self.expires_at, self.reconciled_through)
        if any(d.tzinfo is None for d in dates):
            raise ValueError("Timezone-aware review dates are required")
        if not self.valid_from < self.expires_at <= self.valid_from + timedelta(days=31):
            raise ValueError("Expense review must expire within 31 days")
        if self.reconciled_through < self.valid_from - timedelta(days=31):
            raise ValueError("Monthly expense reconciliation is overdue")
        if (
            self.monthly_promotional_model_budget_micros > self.monthly_model_budget_micros
            or self.daily_model_budget_micros > self.monthly_model_budget_micros
        ):
            raise ValueError("Funded sub-budgets cannot exceed the monthly model budget")
        if len({p.sku for p in self.packs}) != 3:
            raise ValueError("Exactly three distinct finite packs are required")
        if any(p.max_discount_minor >= p.gross_minor for p in self.packs):
            raise ValueError("Discounts cannot erase pack receipts")
        if self.analysis_overhead_minor_per_unit < 3 * self.provider_attempt_overhead_minor:
            raise ValueError("Analysis allowance must fund all three worker attempts")
        if not self.operation_model_ceilings_micros:
            raise ValueError("Explicit provider operation ceilings are required")
        return self


def ceil(value: Fraction) -> int:
    return -(-value.numerator // value.denominator)


def usd_minor(policy: ExpensePolicy, micros: int) -> int:
    return ceil(Fraction(micros * policy.adverse_fx_minor_per_usd, 1_000_000))


def net_receipts(policy: ExpensePolicy, gross: int) -> int:
    # Sales-tax liability is conservatively reserved from an inclusive total;
    # this is an estimate, not a determination of the merchant's tax treatment.
    tax = ceil(Fraction(gross * policy.sale_tax_reserve_bps, 10_000 + policy.sale_tax_reserve_bps))
    fee = ceil(Fraction(gross * policy.gateway_fee_bps, 10_000))
    fee_tax = ceil(Fraction(fee * policy.gateway_fee_tax_bps, 10_000))
    risk = ceil(Fraction(gross * policy.refund_chargeback_reserve_bps, 10_000))
    return (
        gross - tax - fee - fee_tax - risk - policy.gateway_fixed_minor - policy.settlement_fx_minor
    )


def audit(policy: ExpensePolicy) -> dict:
    allocation = ceil(
        Fraction(
            policy.monthly_platform_operations_minor + policy.monthly_marketing_operations_minor,
            policy.minimum_paid_packs_per_month,
        )
    )
    ai_cost = (
        usd_minor(policy, policy.analysis_unit_model_ceiling_micros)
        + policy.analysis_overhead_minor_per_unit
    )
    # Net receipts available for raw operating costs after both planning targets.
    factor = Fraction(10_000 + policy.contingency_bps, 10_000 - policy.margin_bps)
    rows = []
    rates = []
    for pack in policy.packs:
        gross = pack.gross_minor - pack.max_discount_minor
        net = net_receipts(policy, gross)
        service = pack.service_credits + pack.max_bonus_service_credits
        units = pack.analysis_units + pack.max_bonus_analysis_units
        remainder = Fraction(net, 1) / factor - allocation - units * ai_cost
        if remainder <= 0:
            raise CostPolicyUnavailable(
                "The finite pack cannot fund its promised analysis and platform allocation."
            )
        rate = remainder / service
        rates.append(rate)
        raw = (
            allocation
            + units * ai_cost
            + service
            * max(
                Fraction(policy.search_cost_minor, policy.service_search_credits),
                Fraction(policy.application_cost_minor, policy.service_apply_credits),
            )
        )
        required = ceil(raw * factor)
        if net < required:
            raise CostPolicyUnavailable(
                "A finite pack or promotion does not cover its worst permitted workload."
            )
        rows.append(
            {
                "sku": pack.sku,
                "net_receipts_minor": net,
                "required_net_minor": required,
                "allocation_minor": allocation,
                "analysis_units": units,
                "service_credits": service,
            }
        )
    lowest = min(rates)
    search_floor = ceil(Fraction(policy.search_cost_minor, 1) / lowest)
    apply_floor = ceil(Fraction(policy.application_cost_minor, 1) / lowest)
    if policy.service_search_credits < search_floor or policy.service_apply_credits < apply_floor:
        raise CostPolicyUnavailable("Configured service prices are below their expense floor.")
    # The promotional provider budget is itself a cash cost of free/legacy use.
    if (
        usd_minor(
            policy,
            policy.monthly_promotional_model_budget_micros
            + policy.monthly_legacy_premium_model_budget_micros,
        )
        + policy.monthly_legacy_service_budget_minor
        > policy.monthly_marketing_operations_minor
    ):
        raise CostPolicyUnavailable(
            "The promotional model pool is not funded by the marketing allocation."
        )
    return {
        "version": policy.version,
        "search_floor": search_floor,
        "apply_floor": apply_floor,
        "packs": rows,
        "analysis_cost_minor_per_unit": ai_cost,
        "fixed_allocation_minor_per_pack": allocation,
    }


def current_policy(*, now: datetime | None = None) -> ExpensePolicy:
    raw = os.getenv("HIREWIZ_EXPENSE_POLICY_JSON", "")
    try:
        if not raw:
            raise ValueError("missing")
        policy = ExpensePolicy.model_validate_json(raw)
        if policy.review_status != "approved" or policy.reviewed_by == "operator-review-required":
            raise ValueError("not approved")
        if policy.provenance == "synthetic_test" and os.getenv("APP_ENV", "").lower() not in {
            "test",
            "development",
        }:
            raise ValueError("synthetic policy is not a production funding authority")
        stamp = now or datetime.now(UTC)
        if not policy.valid_from <= stamp < policy.expires_at or policy.reconciled_through > stamp:
            raise ValueError("stale")
        audit(policy)
        return policy
    except (ValueError, TypeError, CostPolicyUnavailable):
        raise CostPolicyUnavailable(
            "Paid services and optional AI are temporarily unavailable while cost funding is reviewed."
        ) from None


def snapshot(policy: ExpensePolicy) -> dict:
    payload = policy.model_dump(mode="json")
    return {
        "version": policy.version,
        "digest": hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest(),
        "expires_at": policy.expires_at.isoformat(),
        "provenance": policy.provenance,
        "policy": payload,
        "audit": audit(policy),
    }


def service_prices() -> dict:
    from .catalog import CATALOG_VERSION, PRODUCTS

    policy = current_policy()
    if policy.catalog_version != CATALOG_VERSION:
        raise CostPolicyUnavailable("The catalog requires a current expense review.")
    offered = {p.sku: p for p in PRODUCTS.values() if p.entitlement_kind == "credit_bundle"}
    if {p.sku for p in policy.packs} != set(offered):
        raise CostPolicyUnavailable("The expense review does not cover every finite pack.")
    for p in policy.packs:
        product = offered[p.sku]
        if (p.gross_minor, p.service_credits, p.analysis_units) != (
            product.amount_minor,
            product.entitlement_quantity,
            product.analysis_units,
        ):
            raise CostPolicyUnavailable(
                "The finite pack no longer matches its reviewed expense terms."
            )
    return {
        "search_credits_per_job": policy.service_search_credits,
        "apply_credits_per_job": policy.service_apply_credits,
        "pricing_version": policy.version,
        "cost_policy_snapshot": snapshot(policy),
    }


def assert_actual_variance(db, policy: ExpensePolicy) -> None:
    """Known adverse receipts since the last review suspend NEW expensive work.

    Accepted captures/refunds always settle. Unknown gateway/customer-tax facts
    remain unknown; reviewed conservative bounds continue until reconciliation.
    """
    from sqlalchemy import Numeric, cast, func, or_

    from ..models import PaymentOrder, PaymentRefund

    def rounded(value, denominator):
        return func.floor((cast(value, Numeric(30, 0)) + denominator - 1) / denominator)

    gross = PaymentOrder.gross_amount_minor
    fee = rounded(gross * policy.gateway_fee_bps, 10_000)
    bound = (
        fee
        + rounded(fee * policy.gateway_fee_tax_bps, 10_000)
        + policy.gateway_fixed_minor
        + policy.settlement_fx_minor
    )
    tax_bound = rounded(gross * policy.sale_tax_reserve_bps, 10_000 + policy.sale_tax_reserve_bps)
    if (
        db.query(PaymentOrder.id)
        .filter(
            PaymentOrder.paid_at >= policy.reconciled_through,
            or_(
                PaymentOrder.provider_fee_amount_minor > bound,
                PaymentOrder.customer_tax_amount_minor > tax_bound,
            ),
        )
        .first()
        is not None
    ):
        raise CostPolicyUnavailable(
            "New paid services and AI are paused while actual payment expenses are reconciled."
        )
    captured = int(
        db.query(func.coalesce(func.sum(gross), 0))
        .filter(PaymentOrder.paid_at >= policy.reconciled_through)
        .scalar()
        or 0
    )
    refunded = int(
        db.query(func.coalesce(func.sum(PaymentRefund.amount_minor), 0))
        .filter(PaymentRefund.processed_at >= policy.reconciled_through)
        .scalar()
        or 0
    )
    if refunded > ceil(Fraction(captured * policy.refund_chargeback_reserve_bps, 10_000)):
        raise CostPolicyUnavailable(
            "New paid services and AI are paused while refund expenses are reconciled."
        )


def assert_model_funding(
    db, *, operation: str, operation_ceiling: int, reservation: int, funding: str
) -> dict:
    """One transaction-wide fleet lock makes concurrent admission atomic.

    Unsettled liabilities from ANY date consume the current budgets; deletion
    of telemetry or a month boundary cannot erase a provider liability.
    """
    from sqlalchemy import case, func, or_, text

    from ..models import ModelCostLiability

    policy = current_policy()
    assert_actual_variance(db, policy)
    if operation_ceiling > policy.operation_model_ceilings_micros.get(operation, 0):
        raise CostPolicyUnavailable(
            "This AI operation exceeds the reviewed product funding ceiling."
        )
    if db.get_bind().dialect.name == "postgresql":
        db.execute(text("SELECT pg_advisory_xact_lock(7349186502109)"))
    now = datetime.now(UTC)
    unresolved = ModelCostLiability.cost_state.notin_(["settled", "overrun"])
    held = func.coalesce(ModelCostLiability.reserved_cost_micros, 0)
    settled = func.coalesce(ModelCostLiability.settled_cost_micros, 0)
    maximum = (
        func.max(held, settled)
        if db.get_bind().dialect.name == "sqlite"
        else func.greatest(held, settled)
    )
    provider_amount = case((unresolved, maximum), else_=settled)
    # Estimated worker/document cash is separate from the provider liability.
    # Count it even for tiny settled or failed calls; never rewrite invoices.
    overhead_micros = ceil(
        Fraction(
            policy.provider_attempt_overhead_minor * 1_000_000, policy.adverse_fx_minor_per_usd
        )
    )
    amount = provider_amount + overhead_micros
    funded_reservation = reservation + overhead_micros
    for start, limit in (
        (now.replace(hour=0, minute=0, second=0, microsecond=0), policy.daily_model_budget_micros),
        (
            now.replace(day=1, hour=0, minute=0, second=0, microsecond=0),
            policy.monthly_model_budget_micros,
        ),
    ):
        used = int(
            db.query(func.coalesce(func.sum(amount), 0))
            .filter(or_(unresolved, ModelCostLiability.created_at >= start))
            .scalar()
            or 0
        )
        if used + funded_reservation > limit:
            raise CostPolicyUnavailable(
                "The funded AI budget is exhausted. Unknown provider costs remain reserved."
            )
    month = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    period = or_(unresolved, ModelCostLiability.created_at >= month)
    origin = ModelCostLiability.pricing_quote["expense_funding"].as_string()
    pool = ModelCostLiability.pricing_quote["expense_pool"].as_string()
    # Database aggregates keep the fleet guard independent of history size.
    # Unknown origin is conservatively promotional. All unresolved attempts
    # consume risk funding until reconciliation, including previous months.
    risk_filter = or_(
        unresolved,
        pool == "failed_work",
        origin.is_(None),
        origin.notin_(["prepaid", "legacy_premium"]),
    )
    risk_used = int(
        db.query(func.coalesce(func.sum(amount), 0)).filter(period, risk_filter).scalar() or 0
    )
    if risk_used + funded_reservation > policy.monthly_promotional_model_budget_micros:
        raise CostPolicyUnavailable(
            "The finite promotional and failed-work AI funding pool is exhausted."
        )
    if funding == "legacy_premium":
        used = int(
            db.query(func.coalesce(func.sum(amount), 0))
            .filter(period, origin == "legacy_premium")
            .scalar()
            or 0
        )
        if used + funded_reservation > policy.monthly_legacy_premium_model_budget_micros:
            raise CostPolicyUnavailable("The funded legacy-access AI exposure pool is exhausted.")
    return {
        "expense_policy_version": policy.version,
        "expense_funding": funding,
        # A paid result can still fail validation after its provider response.
        # Keep risk reserved until the product usage/result commits atomically.
        "expense_pool": "failed_work" if funding == "prepaid" else funding,
    }


def current_service_funding(db, user_id: int) -> int:
    from sqlalchemy import Integer, case, cast, func

    from ..domains.employer.models import ServiceCreditEvent, ServiceCreditReservation
    from ..models import PaymentOrder

    grants = int(
        db.query(func.coalesce(func.sum(ServiceCreditEvent.amount), 0))
        .join(
            PaymentOrder,
            PaymentOrder.public_id == ServiceCreditEvent.source_id,
        )
        .filter(
            ServiceCreditEvent.user_id == user_id,
            ServiceCreditEvent.source_type == "payment_order",
            PaymentOrder.entitlement_kind == "credit_bundle",
            PaymentOrder.cost_policy_snapshot["version"].as_string().is_not(None),
        )
        .scalar()
        or 0
    )
    allocated = func.coalesce(
        cast(
            ServiceCreditReservation.cost_policy_snapshot[
                "paid_current_reserved_credits"
            ].as_string(),
            Integer,
        ),
        0,
    )
    # Within each reservation new credits are used first. Partial settlement
    # releases the unused paid allocation; legacy use never becomes paid debt.
    consumed = case(
        (ServiceCreditReservation.state == "reserved", allocated),
        (
            ServiceCreditReservation.committed_amount < allocated,
            ServiceCreditReservation.committed_amount,
        ),
        else_=allocated,
    )
    used = int(
        db.query(func.coalesce(func.sum(consumed), 0))
        .filter(ServiceCreditReservation.user_id == user_id)
        .scalar()
        or 0
    )
    return max(0, grants - used)


def service_expense_holds(
    db,
    policy: ExpensePolicy,
    *,
    operation: str,
    unit_price: int,
    count: int,
    user_id: int | None = None,
    exclude_reservation_id: str | None = None,
) -> dict[str, int]:
    """Keep accepted cheap quotes unchanged; fund their full expense separately.

    A reserve using an older/unproven credit balance or cheap accepted quote
    holds the full estimated
    cash cost in a finite legacy pool. Settling or returning customer credits
    does not erase that month's potential preparation/failure expense. Old
    unsettled holds continue across month boundaries until reconciled.
    """
    from sqlalchemy import Integer, and_, case, cast, func, or_, text

    from ..domains.employer.models import ServiceCreditReservation

    floors = audit(policy)
    if operation not in {"job_search", "job_application"} or (
        count <= 0 and operation != "job_search"
    ):
        return {
            "legacy_service_hold_minor": 0,
            "failed_service_hold_minor": 0,
            "paid_service_cost_count": 0,
        }
    floor = floors["search_floor" if operation == "job_search" else "apply_floor"]
    required = unit_price * count
    paid = min(required, current_service_funding(db, user_id)) if user_id is not None else 0
    legacy_count = count if unit_price < floor else ceil(Fraction(required - paid, unit_price))
    paid_count = count - legacy_count
    if db.get_bind().dialect.name == "postgresql":
        db.execute(text("SELECT pg_advisory_xact_lock(7349186502110)"))
    per_job = (
        policy.search_cost_minor if operation == "job_search" else policy.application_cost_minor
    )
    # Cash hold includes contingency; contribution target is a pack pricing
    # target, not another cash payment owed by legacy customers.
    hold = ceil(Fraction(legacy_count * per_job * (10_000 + policy.contingency_bps), 10_000))
    failed_cost = (
        (policy.service_preparation_cost_minor if operation == "job_search" else per_job)
        if paid_count or (operation == "job_search" and count == 0)
        else 0
    )
    failed_hold = ceil(Fraction(failed_cost * (10_000 + policy.contingency_bps), 10_000))
    month = datetime.now(UTC).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    amount = cast(
        ServiceCreditReservation.cost_policy_snapshot["legacy_service_hold_minor"].as_string(),
        Integer,
    )
    # Pre-cutover reservations have no expense snapshot. Their existing cheap
    # quote is conservatively held too; account erasure cannot discard them.
    historical_cost = case(
        (
            and_(
                ServiceCreditReservation.operation == "job_search",
                ServiceCreditReservation.cost_policy_snapshot["version"].as_string().is_(None),
            ),
            policy.search_cost_minor,
        ),
        (
            and_(
                ServiceCreditReservation.operation == "job_application",
                ServiceCreditReservation.cost_policy_snapshot["version"].as_string().is_(None),
            ),
            policy.application_cost_minor,
        ),
        else_=0,
    )
    historical_hold = cast(
        (
            historical_cost
            * ServiceCreditReservation.requested_count
            * (10_000 + policy.contingency_bps)
            + 9999
        )
        / 10_000,
        Integer,
    )
    failed = cast(
        ServiceCreditReservation.cost_policy_snapshot["failed_service_hold_minor"].as_string(),
        Integer,
    )
    retained = func.coalesce(amount, historical_hold) + func.coalesce(failed, 0)
    usage = db.query(func.coalesce(func.sum(retained), 0)).filter(
        or_(
            ServiceCreditReservation.state == "reserved",
            ServiceCreditReservation.created_at >= month,
        )
    )
    if exclude_reservation_id:
        usage = usage.filter(ServiceCreditReservation.id != exclude_reservation_id)
    used = int(usage.scalar() or 0)
    if used + hold + failed_hold > policy.monthly_legacy_service_budget_minor:
        raise CostPolicyUnavailable(
            "New service work is paused while its finite legacy and unsuccessful-work funding is reviewed."
        )
    return {
        "legacy_service_hold_minor": hold,
        "failed_service_hold_minor": failed_hold,
        "paid_service_cost_count": paid_count,
    }


def legacy_service_hold(db, policy: ExpensePolicy, **kwargs) -> int:
    """Compatibility helper; admission also accounts for paid unsuccessful work."""
    return service_expense_holds(db, policy, **kwargs)["legacy_service_hold_minor"]
