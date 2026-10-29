"""Legacy requests share fleet limits with unified and bounded-free workloads.

This additional guard changes neither a customer's old quote nor wallet rights.
It runs before new exposure is accepted; accepted monetary liabilities remain
visible to the common guard until an authoritative settlement releases them.
"""
from __future__ import annotations

import os
from datetime import UTC, datetime
from fractions import Fraction

from . import admission, finance


def common_authority(now: datetime):
    paid_on = os.getenv("UNIFIED_CREDIT_EXECUTION_ENABLED", "").lower() == "true"
    free_on = os.getenv("FREE_CORE_ENABLED", "").lower() == "true"
    if not paid_on and not free_on:
        return None
    try:
        from .runtime import RuntimeConfig
        paid = RuntimeConfig.model_validate_json(os.getenv("UNIFIED_CREDIT_RUNTIME_REVIEW_JSON", "")) if paid_on else None
        if free_on:
            from ..free_core.policy import current_policy
            free = current_policy(now=now)
            policy, budgets = free.financial_policy, free.execution_budgets
            if paid and (paid.policy != policy or paid.budgets != budgets):
                raise ValueError("Funding authorities differ")
        else:
            assert paid is not None
            policy, budgets = paid.policy, paid.budgets
        production = os.getenv("APP_ENV", "production").lower() not in {"test", "development", "dev", "local", "staging"}
        finance.require_current_policy(policy, now=now, commercial=production)
        if not budgets.valid_from <= now < budgets.expires_at:
            raise ValueError("Funding review expired")
        return policy, budgets
    except ValueError as exc:
        raise finance.FundingUnavailable("The common execution funding policy requires review.") from exc


def assert_legacy_headroom(db, *, provider_micros: int = 0, native_minor: int = 0,
                           exclude_service_reservation_id: str | None = None,
                           now: datetime | None = None) -> None:
    stamp = now or datetime.now(UTC)
    finance._nonnegative(provider_micros, "provider_micros")
    finance._nonnegative(native_minor, "native_minor")
    authority = common_authority(stamp)
    if authority is None:
        return
    policy, budgets = authority
    required = finance.ceil_minor(Fraction(provider_micros * policy.adverse_fx_minor_per_usd *
        (10_000 + policy.unrecoverable_provider_tax_bps), 10_000 * 1_000_000)) + native_minor
    admission.lock_money(db)
    day = stamp.replace(hour=0, minute=0, second=0, microsecond=0)
    for start, maximum in ((day, budgets.daily_global_minor), (day.replace(day=1), budgets.monthly_global_minor)):
        used = admission._hold_usage(db, start=start) + admission._legacy_usage(
            db, start=start, policy=policy, budgets=budgets,
            exclude_service_reservation_id=exclude_service_reservation_id)
        if used + required > maximum:
            raise finance.FundingUnavailable("The common execution budget is fully reserved.")
