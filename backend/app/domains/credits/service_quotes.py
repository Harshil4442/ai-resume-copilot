"""Persisted quotes bind a qualified route and exact input fingerprint before spend.

Callers validate ownership and hash the complete dependency manifest before
calling this domain boundary. Snapshots contain no resumes, questions or answers.
Basic, curated and unchanged-result reuse use no paid quote and no model call.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from fractions import Fraction
from typing import Annotated, Literal

from pydantic import Field, model_validator
from sqlalchemy.orm import Session

from ...services.model_cost_policy import ModelPriceQuote, quoted_cost
from ..common import payload_fingerprint, public_id
from . import finance, pricing, service
from .models import CreditServiceQuote

Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
Identifier = Annotated[str, Field(min_length=1, max_length=120, pattern=r"^[A-Za-z0-9_.:-]+$")]


class QualifiedRoute(finance.Closed):
    id: Identifier
    version: Identifier
    operation: str = Field(min_length=1, max_length=64)
    tariff_version: Literal["credit-services-2026-10-27-v1"]
    credits: int = Field(strict=True, gt=0)
    mode: Literal["enhanced", "native"]
    deliverable: str = Field(min_length=1, max_length=500)
    supported_limits: dict[str, int] = Field(max_length=12)
    supported_source_formats: tuple[Literal["pdf", "docx", "tex", "texzip"], ...] = ()
    qualification_id: Identifier
    qualification_evidence_sha256: Digest
    qualified_from: datetime
    qualified_until: datetime
    native_cost_evidence_sha256: Digest
    native_processing_minor: int = Field(strict=True, ge=0)
    worker_processing_minor: int = Field(strict=True, ge=0)
    model_quotes: tuple[ModelPriceQuote, ...] = Field(max_length=4)

    @model_validator(mode="after")
    def bounded_contract(self):
        if (
            self.operation not in pricing.SERVICE_CREDITS
            or self.credits != pricing.SERVICE_CREDITS[self.operation]
        ):
            raise ValueError("The qualified route must match the reviewed service tariff")
        if self.qualified_from.tzinfo is None or self.qualified_until.tzinfo is None:
            raise ValueError("Qualification dates must have a timezone")
        if (
            not self.qualified_from
            < self.qualified_until
            <= self.qualified_from + timedelta(days=31)
        ):
            raise ValueError("A qualified route must be reviewed within 31 days")
        if (
            self.operation in {"resume_tailor", "resume_tailor_extended"}
            and not self.supported_source_formats
        ):
            raise ValueError("A tailoring route must name its qualified source formats")
        if (self.mode == "native") != (len(self.model_quotes) == 0):
            raise ValueError("The route must truthfully declare whether it uses a model")
        if any(type(n) is not int or n < 0 for n in self.supported_limits.values()):
            raise ValueError("Supported limits must be finite nonnegative integers")
        return self

    def envelope(self) -> finance.ExecutionEnvelope:
        return finance.ExecutionEnvelope(
            attempts=tuple(
                finance.ProviderAttemptEnvelope(
                    provider=q.provider,
                    model=q.model,
                    max_input_tokens=q.max_input_tokens,
                    max_output_tokens=q.max_output_tokens,
                    input_usd_micros_per_million=q.input_rate_micros_per_million,
                    output_usd_micros_per_million=q.output_rate_micros_per_million,
                    rate_evidence_sha256=payload_fingerprint(q.model_dump(mode="json")),
                )
                for q in self.model_quotes
            ),
            native_processing_minor=self.native_processing_minor,
            worker_processing_minor=self.worker_processing_minor,
            qualification_id=self.qualification_id,
        )


def require_route(
    route: QualifiedRoute,
    policy: finance.FinancialPolicy,
    *,
    now: datetime,
    commercial: bool = False,
) -> int:
    if not route.qualified_from <= now < route.qualified_until:
        raise finance.FundingUnavailable(
            "The selected delivery route needs a current qualification"
        )
    if any(not q.valid_from <= now < q.expires_at for q in route.model_quotes):
        raise finance.FundingUnavailable("The selected provider pricing needs a current review")
    required = finance.require_execution_allowance(
        policy, route.envelope(), charge_credits=route.credits, now=now, commercial=commercial
    )
    # The existing provider ledger rounds to USD micros first. Include that
    # conservative rounding before INR conversion so its held cost always fits.
    ledger_required = route.native_processing_minor + route.worker_processing_minor
    for model in route.model_quotes:
        usd = quoted_cost(model.model_dump(), model.max_input_tokens, model.max_output_tokens)
        ledger_required += finance.ceil_minor(
            Fraction(
                usd
                * policy.adverse_fx_minor_per_usd
                * (10_000 + policy.unrecoverable_provider_tax_bps),
                10_000 * 1_000_000,
            )
        )
    required = max(required, ledger_required)
    if required > route.credits * min(
        policy.execution_minor_per_credit, pricing.EXECUTION_MINOR_PER_CREDIT
    ):
        raise finance.FundingUnavailable(
            "The complete rounded execution envelope needs a new quote"
        )
    return required


def quote_view(quote: CreditServiceQuote, *, now: datetime | None = None) -> dict:
    """Expose the promised result and charge; internal money is not another currency."""
    route = QualifiedRoute.model_validate(quote.snapshot["route"])
    return {
        "schema_version": "unified-service-quote-v1",
        "quote_id": quote.id,
        "operation": quote.operation,
        "input_fingerprint": quote.input_fingerprint,
        "route_id": route.id,
        "route_version": route.version,
        "mode": route.mode,
        "tariff_version": route.tariff_version,
        "credits": route.credits,
        "deliverable": route.deliverable,
        "supported_limits": dict(route.supported_limits),
        "created_at": pricing.aware(quote.created_at),
        "expires_at": pricing.aware(quote.expires_at),
        "server_time": service._stamp(now),
    }


def issue_service_quote(
    db: Session,
    *,
    user_id: int,
    input_fingerprint: str,
    idempotency_key: str,
    route: QualifiedRoute,
    policy: finance.FinancialPolicy,
    commercial: bool = False,
    now: datetime | None = None,
) -> CreditServiceQuote:
    stamp = service._stamp(now)
    service._key(idempotency_key)
    # Validation occurs before persisting opaque caller strings in a ledger.
    from pydantic import TypeAdapter

    TypeAdapter(Digest).validate_python(input_fingerprint)
    service._lock_owner(db, user_id)
    contract = {"route": route.model_dump(mode="json"), "policy": policy.model_dump(mode="json")}
    fingerprint = payload_fingerprint(contract)
    existing = (
        db.query(CreditServiceQuote)
        .filter_by(user_id=user_id, idempotency_key=idempotency_key)
        .one_or_none()
    )
    if existing:
        if (existing.input_fingerprint, existing.contract_fingerprint) != (
            input_fingerprint,
            fingerprint,
        ):
            raise pricing.CreditContractError(
                "This request already has different input or delivery terms"
            )
        return existing
    required = require_route(route, policy, now=stamp, commercial=commercial)
    expiry = min(
        stamp + pricing.PURCHASE_QUOTE_LIFETIME,
        route.qualified_until,
        policy.expires_at,
        *(q.expires_at for q in route.model_quotes),
    )
    quote = CreditServiceQuote(
        id=public_id("csq"),
        user_id=user_id,
        idempotency_key=idempotency_key,
        operation=route.operation,
        input_fingerprint=input_fingerprint,
        contract_fingerprint=fingerprint,
        snapshot=contract | {"execution_required_minor": required},
        created_at=stamp,
        expires_at=expiry,
    )
    db.add(quote)
    db.flush([quote])
    return quote
