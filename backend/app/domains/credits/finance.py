"""Exact monetary calculations for the version 1.1 credit contract.

These functions do not authorize execution by themselves. The caller must reserve
the complete envelope with the customer credits before dispatch, and retain money
for unknown provider outcomes. Forecasts are assumptions, never proof of funding.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from fractions import Fraction
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Minor = Annotated[int, Field(strict=True, ge=0)]
Positive = Annotated[int, Field(strict=True, gt=0)]
Bps = Annotated[int, Field(strict=True, ge=0, le=10_000)]
POLICY_VERSION = "unified-credit-economics-2026-10-27-v1"


class FundingUnavailable(ValueError):
    """No new work may be admitted under an unqualified financial envelope."""


class Closed(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class FinancialPolicy(Closed):
    version: str = Field(min_length=1, max_length=120)
    valid_from: datetime
    expires_at: datetime
    provenance: Literal["estimated", "invoice_reconciled", "synthetic_test"]
    evidence_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    execution_minor_per_credit: Positive = 45
    included_sales_tax_bps: Bps = 1800
    gateway_gross_bps: Bps = 236
    refund_gross_bps: Bps = 200
    incident_net_bps: Bps = 1000
    minimum_net_margin_bps: Bps = 1500
    target_net_margin_bps: Bps = 2000
    adverse_fx_minor_per_usd: Positive = 10_000
    unrecoverable_provider_tax_bps: Bps = 1800

    @model_validator(mode="after")
    def bounded_policy(self):
        if self.valid_from.tzinfo is None or self.expires_at.tzinfo is None:
            raise ValueError("Financial policy dates must be timezone-aware")
        if not self.valid_from < self.expires_at <= self.valid_from + timedelta(days=31):
            raise ValueError("Financial policy review must expire within 31 days")
        if self.minimum_net_margin_bps > self.target_net_margin_bps:
            raise ValueError("Target margin must not be lower than the minimum")
        if self.incident_net_bps + self.target_net_margin_bps >= 10_000:
            raise ValueError("Reserves and margin must leave delivery funding")
        return self


def require_current_policy(
    policy: FinancialPolicy, *, now: datetime | None = None, commercial: bool = False,
) -> FinancialPolicy:
    instant = now or datetime.now(UTC)
    if instant.tzinfo is None:
        raise ValueError("Admission time must be timezone-aware")
    if not policy.valid_from <= instant < policy.expires_at:
        raise FundingUnavailable("The financial policy needs a current review")
    if commercial and policy.provenance != "invoice_reconciled":
        raise FundingUnavailable("Paid activation requires reconciled expense evidence")
    return policy


def _nonnegative(value: int, name: str) -> int:
    if type(value) is not int or value < 0:
        raise ValueError(f"{name} must be a non-negative integer")
    return value


def ceil_minor(value: Fraction) -> int:
    return -(-value.numerator // value.denominator)


def forecast(
    policy: FinancialPolicy, *, gross_minor: int, consumed_credits: int,
    shared_minor: int, additional_business_minor: int = 0,
) -> dict[str, Fraction | bool]:
    """Exact forecast before income tax; reserves are not recorded as spent bills."""
    for key, value in locals().copy().items():
        if key != "policy":
            _nonnegative(value, key)
    gross = Fraction(gross_minor)
    revenue = gross * 10_000 / (10_000 + policy.included_sales_tax_bps)
    gateway = gross * policy.gateway_gross_bps / 10_000
    refund = gross * policy.refund_gross_bps / 10_000
    net = revenue - gateway - refund
    incident = net * policy.incident_net_bps / 10_000
    execution = Fraction(consumed_credits * policy.execution_minor_per_credit)
    overhead = Fraction(shared_minor + additional_business_minor)
    surplus = net - incident - execution - overhead
    minimum = net * policy.minimum_net_margin_bps / 10_000
    target = net * policy.target_net_margin_bps / 10_000
    return {
        "gross_minor": gross, "revenue_minor": revenue,
        "sales_tax_minor": gross - revenue, "gateway_minor": gateway,
        "refund_reserve_minor": refund, "net_receipts_minor": net,
        "incident_reserve_minor": incident, "execution_minor": execution,
        "overhead_minor": overhead, "surplus_minor": surplus,
        "minimum_surplus_minor": minimum, "target_surplus_minor": target,
        "minimum_margin_met": net > 0 and surplus >= minimum,
        "target_margin_met": net > 0 and surplus >= target,
    }


class ProviderAttemptEnvelope(Closed):
    """Maximum billed tokens, including output reasoning and any repair context."""
    provider: str = Field(min_length=1, max_length=40)
    model: str = Field(min_length=1, max_length=120)
    max_input_tokens: Positive
    max_output_tokens: Positive
    input_usd_micros_per_million: Minor
    output_usd_micros_per_million: Minor
    rate_evidence_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class ExecutionEnvelope(Closed):
    attempts: tuple[ProviderAttemptEnvelope, ...] = Field(max_length=4)
    native_processing_minor: Minor
    worker_processing_minor: Minor
    qualification_id: str = Field(min_length=1, max_length=120)


def execution_cost_minor(policy: FinancialPolicy, envelope: ExecutionEnvelope) -> int:
    """Round each attempt upwards, never assume cache hits or free failed calls."""
    provider_total = 0
    for attempt in envelope.attempts:
        usd_micros = (
            Fraction(attempt.max_input_tokens * attempt.input_usd_micros_per_million, 1_000_000)
            + Fraction(attempt.max_output_tokens * attempt.output_usd_micros_per_million, 1_000_000)
        )
        inr_minor = usd_micros * policy.adverse_fx_minor_per_usd / 1_000_000
        inr_minor *= Fraction(10_000 + policy.unrecoverable_provider_tax_bps, 10_000)
        provider_total += ceil_minor(inr_minor)
    return provider_total + envelope.native_processing_minor + envelope.worker_processing_minor


def require_execution_allowance(
    policy: FinancialPolicy, envelope: ExecutionEnvelope, *, charge_credits: int,
    now: datetime | None = None, commercial: bool = False,
) -> int:
    require_current_policy(policy, now=now, commercial=commercial)
    _nonnegative(charge_credits, "charge_credits")
    required = execution_cost_minor(policy, envelope)
    if required > charge_credits * policy.execution_minor_per_credit:
        raise FundingUnavailable("The qualified delivery needs a new quote or funded route")
    return required
