"""Claim each pre-funded attempt once; settle only observed cost, never a timeout.

The provider caller commits claim_attempt before network I/O. A completion binding
is private in-flight authority, not a user-facing lookup or replay capability.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime
from fractions import Fraction
from typing import Any

from sqlalchemy.orm import Session

from ...models import ModelCostLiability
from ...services.generation_budget import _usage
from ...services.model_cost_policy import input_token_estimate, quoted_cost
from ..employer.admissions import lock_application_set
from . import finance, pricing, service
from .admission import lock_money
from .models import CreditExecutionAttempt, CreditExecutionHold, CreditReservation
from .service_quotes import QualifiedRoute, require_route


@dataclass(frozen=True)
class CompletionBinding:
    hold_id: str
    attempt_id: str
    liability_id: str
    financial_group_id: str
    claim_key: str
    ordinal: int
    owner_id: int


def _owned_hold(db: Session, *, user_id: int, hold_id: str) -> CreditExecutionHold:
    service._lock_owner(db, user_id)
    row = (
        db.query(CreditExecutionHold)
        .filter_by(id=hold_id, user_id=user_id)
        .with_for_update()
        .populate_existing()
        .one_or_none()
    )
    if row is None:
        raise pricing.CreditContractError("Execution not found")
    return row


def claim_attempt(
    db: Session,
    *,
    user_id: int,
    hold_id: str,
    claim_key: str,
    provider: str,
    model: str,
    api_base: str,
    messages: list[dict],
    counted_input_tokens: int | None = None,
    route: QualifiedRoute,
    policy: finance.FinancialPolicy,
    now: datetime | None = None,
) -> tuple[CompletionBinding, dict]:
    stamp = service._stamp(now)
    service._key(claim_key, 64)
    hold = _owned_hold(db, user_id=user_id, hold_id=hold_id)
    lock_money(db)
    if hold.delivery_state != "pending" or hold.state in {"overrun", "outcome_unknown", "settled"}:
        raise finance.FundingUnavailable("This operation cannot dispatch another model request")
    if hold.snapshot["route"] != route.model_dump(mode="json") or hold.snapshot[
        "policy"
    ] != policy.model_dump(mode="json"):
        raise finance.FundingUnavailable("The funded route authorization changed")
    require_route(route, policy, now=stamp)
    attempts = (
        db.query(CreditExecutionAttempt)
        .filter_by(hold_id=hold.id)
        .order_by(CreditExecutionAttempt.ordinal)
        .with_for_update()
        .populate_existing()
        .all()
    )
    if any(
        a.claim_key == claim_key
        or a.state in {"started", "outcome_unknown", "usage_unavailable", "overrun"}
        for a in attempts
    ):
        raise finance.FundingUnavailable(
            "A previous provider attempt needs reconciliation; do not retry it"
        )
    attempt = next((a for a in attempts if a.state == "planned"), None)
    if attempt is None:
        raise finance.FundingUnavailable("The complete operation attempt allowance is exhausted")
    price = route.model_quotes[attempt.ordinal - 1]
    if (provider, model, api_base.rstrip("/")) != (price.provider, price.model, price.api_base):
        raise finance.FundingUnavailable("This model endpoint is not the next qualified attempt")
    estimated = max(input_token_estimate(messages), counted_input_tokens or 0)
    if estimated > price.max_input_tokens:
        raise finance.FundingUnavailable("The prompt exceeds the accepted input bound")
    liability = (
        db.query(ModelCostLiability)
        .filter_by(id=attempt.liability_id, financial_group_id=hold.financial_group_id)
        .with_for_update()
        .populate_existing()
        .one()
    )
    if liability.cost_state != "planned":
        raise finance.FundingUnavailable("The provider liability already has an outcome")
    attempt.state, attempt.claim_key, attempt.started_at = "started", claim_key, stamp
    liability.cost_state, liability.input_token_estimate = "reserved", estimated
    hold.state = "running"
    db.flush()
    binding = CompletionBinding(
        hold.id,
        attempt.id,
        liability.id,
        hold.financial_group_id,
        claim_key,
        attempt.ordinal,
        user_id,
    )
    return binding, {
        "attempt_number": attempt.ordinal,
        "provider": price.provider,
        "model": price.model,
        "max_output_tokens": price.max_output_tokens,
        "output_limit_parameter": price.output_limit_parameter,
    }


def _cost_minor(policy: finance.FinancialPolicy, usd_micros: int) -> int:
    return finance.ceil_minor(
        Fraction(
            usd_micros
            * policy.adverse_fx_minor_per_usd
            * (10_000 + policy.unrecoverable_provider_tax_bps),
            1_000_000 * 10_000,
        )
    )


def refresh_hold(db: Session, hold: CreditExecutionHold, *, now: datetime) -> None:
    policy = finance.FinancialPolicy.model_validate(hold.snapshot["policy"])
    pairs = (
        db.query(CreditExecutionAttempt, ModelCostLiability)
        .join(ModelCostLiability, ModelCostLiability.id == CreditExecutionAttempt.liability_id)
        .filter(CreditExecutionAttempt.hold_id == hold.id)
        .all()
    )
    total = (
        hold.native_reserved_minor
        if hold.native_settled_minor is None
        else hold.native_settled_minor
    )
    unresolved = hold.native_settled_minor is None
    overrun = total > hold.native_reserved_minor
    for attempt, liability in pairs:
        final = liability.cost_state in {"settled", "overrun"}
        used = int(liability.settled_cost_micros or 0)
        if not final:
            unresolved = True
            used = max(used, int(liability.reserved_cost_micros))
        total += _cost_minor(policy, used)
        overrun = overrun or attempt.state == "overrun"
    hold.settled_minor = total
    if overrun or total > hold.reserved_minor:
        hold.state = "overrun"
    elif unresolved:
        hold.state = (
            "outcome_unknown"
            if hold.delivery_state != "pending"
            or any(a.state in {"outcome_unknown", "usage_unavailable"} for a, _ in pairs)
            else "running"
        )
    elif hold.delivery_state != "pending":
        hold.state, hold.settled_at = "settled", now
    # Pending delivery keeps the unused repair allowance held, even after a
    # cheap first response. Only the validated outcome releases that allowance.
    db.flush()


def finish_attempt(
    db: Session,
    *,
    binding: CompletionBinding,
    response: Any = None,
    error: Exception | None = None,
    provider_request_id: str | None = None,
    now: datetime | None = None,
) -> None:
    stamp = service._stamp(now)
    # Keep the same erasure fence even when the owner was deleted during I/O.
    lock_application_set(db, binding.owner_id)
    hold = (
        db.query(CreditExecutionHold)
        .filter_by(id=binding.hold_id, financial_group_id=binding.financial_group_id)
        .with_for_update()
        .populate_existing()
        .one_or_none()
    )
    if hold is None:
        raise pricing.CreditContractError("The in-flight cost binding is unavailable")
    lock_money(db)
    attempt = (
        db.query(CreditExecutionAttempt)
        .filter_by(
            id=binding.attempt_id,
            hold_id=hold.id,
            liability_id=binding.liability_id,
            ordinal=binding.ordinal,
            claim_key=binding.claim_key,
        )
        .with_for_update()
        .populate_existing()
        .one_or_none()
    )
    if attempt is None or attempt.state == "planned":
        raise pricing.CreditContractError("This callback does not own the attempted request")
    liability = (
        db.query(ModelCostLiability)
        .filter_by(id=binding.liability_id, financial_group_id=binding.financial_group_id)
        .with_for_update()
        .populate_existing()
        .one()
    )
    if liability.settled_at is not None:
        return
    if provider_request_id is not None:
        if not isinstance(provider_request_id, str) or not 1 <= len(provider_request_id) <= 1024:
            raise pricing.CreditContractError("Provider request identity is invalid")
        digest = hashlib.sha256(provider_request_id.encode()).hexdigest()
        if attempt.provider_request_id_hash not in {None, digest}:
            raise pricing.CreditContractError("Provider request identity cannot change")
        attempt.provider_request_id_hash = digest
    usage = _usage(response, liability.provider) if error is None else None
    if usage is None:
        attempt.state = liability.cost_state = "outcome_unknown" if error else "usage_unavailable"
    else:
        incoming, outgoing, provenance = usage
        cost = quoted_cost(liability.pricing_quote, incoming, outgoing)
        overrun = (
            cost > liability.reserved_cost_micros
            or incoming > liability.pricing_quote["max_input_tokens"]
            or outgoing > liability.pricing_quote["max_output_tokens"]
        )
        liability.input_tokens, liability.output_tokens = incoming, outgoing
        liability.settled_cost_micros, liability.usage_provenance, liability.settled_at = (
            cost,
            provenance,
            stamp,
        )
        attempt.state = liability.cost_state = "overrun" if overrun else "settled"
        attempt.finished_at = stamp
    db.flush()
    refresh_hold(db, hold, now=stamp)


def record_native_cost(
    db: Session, *, user_id: int, hold_id: str, actual_minor: int, now: datetime | None = None
) -> None:
    """Only call from verified metering, not an HTTP timeout or estimated zero."""
    service._count(actual_minor)
    hold = _owned_hold(db, user_id=user_id, hold_id=hold_id)
    lock_money(db)
    if hold.native_settled_minor is not None and hold.native_settled_minor != actual_minor:
        raise pricing.CreditContractError("Native expense already has a different final amount")
    hold.native_settled_minor = actual_minor
    refresh_hold(db, hold, now=service._stamp(now))


def settle_delivery(
    db: Session, *, user_id: int, hold_id: str, delivered: bool, now: datetime | None = None
) -> None:
    """A fully validated delivery charges once; failure refunds without erasing cost."""
    if type(delivered) is not bool:
        raise pricing.CreditContractError("An explicit validated delivery outcome is required")
    stamp = service._stamp(now)
    hold = _owned_hold(db, user_id=user_id, hold_id=hold_id)
    lock_money(db)
    outcome = "delivered" if delivered else "failed"
    if hold.delivery_state != "pending":
        if hold.delivery_state != outcome:
            raise pricing.CreditContractError("The delivery already has a different final outcome")
        return
    pairs = (
        db.query(CreditExecutionAttempt, ModelCostLiability)
        .join(ModelCostLiability, ModelCostLiability.id == CreditExecutionAttempt.liability_id)
        .filter(CreditExecutionAttempt.hold_id == hold.id)
        .all()
    )
    if (
        delivered
        and pairs
        and (
            any(a.state in {"started", "outcome_unknown", "overrun"} for a, _ in pairs)
            or not any(a.state in {"settled", "usage_unavailable"} for a, _ in pairs)
        )
    ):
        raise pricing.CreditContractError(
            "A delivery cannot be confirmed before a usable provider result"
        )
    for attempt, liability in pairs:
        if attempt.state == "planned":
            attempt.state, attempt.finished_at = "cancelled", stamp
            liability.cost_state, liability.settled_cost_micros = "settled", 0
            liability.usage_provenance, liability.settled_at = "not-dispatched-v1", stamp
        if delivered:
            liability.pricing_quote = dict(liability.pricing_quote) | {"expense_pool": "delivered"}
    reservation = db.get(CreditReservation, hold.reservation_id)
    service.settle_reservation(
        db,
        user_id=user_id,
        reservation_id=reservation.id,
        committed_credits=reservation.reserved_credits if delivered else 0,
        now=stamp,
    )
    hold.delivery_state = outcome
    db.flush()
    refresh_hold(db, hold, now=stamp)


def persistent_execution_budget(
    factory,
    *,
    user_id: int,
    hold_id: str,
    route: QualifiedRoute,
    policy: finance.FinancialPolicy,
    analysis_run_id: str | None = None,
):
    """Existing LLM clients consume this GenerationBudget without SDK retry bypass."""
    import contextlib

    from ...services.generation_budget import GenerationBudget, current_budget, generation_budget
    from ..common import public_id

    @contextlib.contextmanager
    def context():
        # An alternate same-operation budget cannot bypass output qualification.
        require_route(route, policy, now=service._stamp(None))
        identity = analysis_run_id or f"unified:{hold_id}"
        current = current_budget()
        if current is not None:
            if current.run_id != identity:
                raise finance.FundingUnavailable(
                    "A generation budget cannot fund another operation"
                )
            yield current
            return
        with factory() as db:
            hold = (
                db.query(CreditExecutionHold).filter_by(id=hold_id, user_id=user_id).one_or_none()
            )
            if hold is None:
                raise pricing.CreditContractError("Execution not found")
            if analysis_run_id is not None:
                from ...models import AnalysisRun

                bound = (
                    db.query(AnalysisRun)
                    .filter_by(
                        id=analysis_run_id,
                        user_id=user_id,
                        model_cost_group_id=hold.financial_group_id,
                    )
                    .one_or_none()
                )
                if (
                    bound is None
                    or (bound.model_cost_quote or {}).get("unified_hold_id") != hold.id
                ):
                    raise pricing.CreditContractError(
                        "The generation budget does not belong to this analysis"
                    )
            used = (
                db.query(CreditExecutionAttempt)
                .filter(
                    CreditExecutionAttempt.hold_id == hold_id,
                    CreditExecutionAttempt.state != "planned",
                )
                .count()
            )
        budget = GenerationBudget(limit=len(route.model_quotes), used=used, run_id=identity)
        bindings: dict[str, CompletionBinding] = {}

        def preflight(provider, model, messages, api_base):
            with factory() as db:
                hold = db.query(CreditExecutionHold).filter_by(id=hold_id, user_id=user_id).one_or_none()
                if hold is None or hold.delivery_state != "pending" or hold.state in {
                    "overrun", "outcome_unknown", "settled",
                }:
                    raise finance.FundingUnavailable("This operation cannot dispatch another model request")
                if (
                    not isinstance(hold.snapshot, dict)
                    or hold.snapshot.get("route") != route.model_dump(mode="json")
                    or hold.snapshot.get("policy") != policy.model_dump(mode="json")
                ):
                    raise finance.FundingUnavailable("The funded route authorization changed")
                # CountTokens receives the user's prompt. Recheck the accepted
                # route and provider price before sending even that free request.
                require_route(route, policy, now=service._stamp(None))
                attempts = db.query(CreditExecutionAttempt).filter_by(hold_id=hold_id).order_by(
                    CreditExecutionAttempt.ordinal
                ).all()
                if any(a.state in {"started", "outcome_unknown", "usage_unavailable", "overrun"} for a in attempts):
                    raise finance.FundingUnavailable("A previous provider attempt needs reconciliation")
                next_attempt = next((a for a in attempts if a.state == "planned"), None)
            if next_attempt is None or budget.used >= budget.limit:
                raise finance.FundingUnavailable("The complete operation attempt allowance is exhausted")
            price = route.model_quotes[next_attempt.ordinal - 1]
            if (provider, model, api_base.rstrip("/")) != (
                price.provider, price.model, price.api_base
            ):
                raise finance.FundingUnavailable("This model endpoint is not the next qualified attempt")
            return price.max_input_tokens

        def before(provider, model, messages, api_base, counted_input_tokens=None):
            with factory() as db:
                binding, record = claim_attempt(
                    db,
                    user_id=user_id,
                    hold_id=hold_id,
                    claim_key=public_id("claim"),
                    provider=provider,
                    model=model,
                    messages=messages,
                    counted_input_tokens=counted_input_tokens,
                    api_base=api_base,
                    route=route,
                    policy=policy,
                )
                db.commit()
                bindings[binding.attempt_id] = binding
                return record | {"event_id": binding.attempt_id}

        def after(record, latency_ms, response, error):
            binding = bindings.get(record["event_id"])
            if binding is None:
                raise pricing.CreditContractError("The generation callback has no private binding")
            request_id = (
                response.get("id")
                if isinstance(response, dict)
                else getattr(response, "response_id", None)
            )
            with factory() as db:
                finish_attempt(
                    db,
                    binding=binding,
                    response=response,
                    error=error,
                    provider_request_id=request_id if isinstance(request_id, str) else None,
                )
                db.commit()

        budget.before, budget.after, budget.preflight = before, after, preflight
        with generation_budget(budget):
            yield budget

    return context()
