"""Explicit funded shared allowance. Missing policy never enables unlimited work."""
from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from typing import Annotated, Literal

from pydantic import Field, model_validator

from ..credits.admission import ExecutionBudgetPolicy
from ..credits.finance import Closed, FinancialPolicy, require_current_policy

Operation = Literal["resume_parse", "basic_match", "curated_interview", "skill_roi", "curated_learning"]
Positive = Annotated[int, Field(strict=True, gt=0, le=10**12)]


class FreeCoreUnavailable(ValueError):
    pass


class FreeCoreEnvelope(Closed):
    route_id: str = Field(min_length=1, max_length=120, pattern=r"^[a-zA-Z0-9_.-]+$")
    reserved_minor: Positive
    max_input_bytes: Positive
    max_source_records: int = Field(strict=True, ge=1, le=10_000)
    max_attempts: int = Field(strict=True, ge=1, le=3)
    per_account_30_days: int = Field(strict=True, ge=1, le=1000)


class FreeCorePolicy(Closed):
    version: str = Field(min_length=1, max_length=120)
    provenance: Literal["estimated", "invoice_reconciled", "synthetic_test"]
    funding_period_id: str = Field(min_length=1, max_length=120)
    evidence_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    valid_from: datetime
    expires_at: datetime
    # Earmarked inside shared funding, never added to that budget a second time.
    shared_period_funded_minor: Positive
    free_allocation_minor: Positive
    global_30_day_operations: Positive
    financial_policy: FinancialPolicy
    execution_budgets: ExecutionBudgetPolicy
    operations: dict[Operation, FreeCoreEnvelope]

    @model_validator(mode="after")
    def bounded(self):
        if self.valid_from.tzinfo is None or self.expires_at.tzinfo is None:
            raise ValueError("Free processing review must use timezone-aware dates")
        if not self.valid_from < self.expires_at <= self.valid_from + timedelta(days=31):
            raise ValueError("Free processing funding must be reviewed within 31 days")
        if self.free_allocation_minor > self.shared_period_funded_minor:
            raise ValueError("Free processing must be allocated inside funded shared expenses")
        if self.free_allocation_minor > self.execution_budgets.monthly_global_minor:
            raise ValueError("Free processing cannot exceed the common funded execution budget")
        if not {"resume_parse", "basic_match", "curated_interview", "skill_roi"}.issubset(self.operations):
            raise ValueError("Every supported deterministic route needs an explicit envelope")
        if self.operations["resume_parse"].per_account_30_days != 1 or self.operations["basic_match"].per_account_30_days != 10:
            raise ValueError("The initial free-core contract is one resume and ten comparisons per 30 days")
        for item in self.operations.values():
            if (item.route_id not in self.execution_budgets.monthly_route_minor
                    or item.reserved_minor > self.free_allocation_minor):
                raise ValueError("Every free route must fit its funded envelope")
        return self


def enabled() -> bool:
    return os.getenv("FREE_CORE_ENABLED", "false").lower() == "true"


def current_policy(*, now: datetime | None = None) -> FreeCorePolicy:
    instant = now or datetime.now(UTC)
    try:
        policy = FreeCorePolicy.model_validate_json(os.getenv("FREE_CORE_POLICY_JSON", ""))
        production = os.getenv("APP_ENV", "production").lower() not in {"test", "development", "dev", "local", "staging"}
        require_current_policy(policy.financial_policy, now=instant, commercial=production)
        if not policy.valid_from <= instant < policy.expires_at:
            raise ValueError("Free processing funding review expired")
        if production and policy.provenance != "invoice_reconciled":
            raise ValueError("Production free processing needs reconciled expense funding")
        if os.getenv("UNIFIED_CREDIT_EXECUTION_ENABLED", "").lower() == "true":
            from ..credits.runtime import RuntimeConfig

            paid = RuntimeConfig.model_validate_json(os.getenv("UNIFIED_CREDIT_RUNTIME_REVIEW_JSON", ""))
            if paid.policy != policy.financial_policy or paid.budgets != policy.execution_budgets:
                raise ValueError("Paid and free processing must share the same financial and global budget authority")
        return policy
    except ValueError:
        raise FreeCoreUnavailable("New free processing is paused until its funded allowance is ready. Your saved results remain available.") from None
