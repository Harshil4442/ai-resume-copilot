"""Explicit reviewed canary configuration; absence never activates paid execution."""

from __future__ import annotations

import os
from datetime import datetime

from pydantic import Field, model_validator

from . import finance, service
from .admission import ExecutionBudgetPolicy
from .service_quotes import QualifiedRoute, require_current_output_contract, require_route


class RuntimeConfig(finance.Closed):
    policy: finance.FinancialPolicy
    budgets: ExecutionBudgetPolicy
    routes: tuple[QualifiedRoute, ...] = Field(min_length=1, max_length=20)

    @model_validator(mode="after")
    def distinct_routes(self):
        if len({route.operation for route in self.routes}) != len(self.routes):
            raise ValueError("Each reviewed service requires one unambiguous current route")
        if len({route.id for route in self.routes}) != len(self.routes):
            raise ValueError("Route identities must be distinct")
        if any(route.id not in self.budgets.monthly_route_minor for route in self.routes):
            raise ValueError("Every qualified route requires an explicit funded budget")
        return self

    def resolve_route(
        self, operation: str, input_payload: dict, *, observed_limits: dict[str, int] | None = None
    ) -> QualifiedRoute:
        service_name = {"job_match": "enhanced_job_match", "market_analysis": "market_report"}.get(
            operation, operation
        )
        if (
            operation in {"job_match", "interview_questions"}
            and input_payload.get("mode") != "enhanced"
        ):
            raise finance.FundingUnavailable(
                "Basic and curated results do not require a paid quote"
            )
        if operation == "resume_tailor":
            scope = input_payload.get("tailoring_scope", "standard")
            if scope not in {"standard", "extended"}:
                raise finance.FundingUnavailable("Review a supported tailoring scope")
            service_name = "resume_tailor_extended" if scope == "extended" else "resume_tailor"
        selected = next((route for route in self.routes if route.operation == service_name), None)
        if selected is None:
            raise finance.FundingUnavailable("This service has no qualified delivery route")
        require_current_output_contract(selected)
        for name, value in (observed_limits or {}).items():
            # Measurements originate from owned source artifacts, never from a
            # client claim of its own size. A new scope always needs new review.
            maximum = selected.supported_limits.get(name)
            if type(value) is not int or value < 0 or maximum is None or value > maximum:
                raise finance.FundingUnavailable(
                    "The selected scope does not support this input; review a different quote"
                )
        return selected


def _audience_enabled(db, user_id: int | None, configured: RuntimeConfig, *, now: datetime) -> bool:
    audience = os.getenv("UNIFIED_CREDIT_EXECUTION_AUDIENCE", "canary").strip().lower()
    if audience == "reviewed_public":
        # This digest covers the complete canonical policy, budgets and routes,
        # not just a name or caller-authored claim that qualification passed.
        from ..common import payload_fingerprint
        from .release import current_review

        try:
            review = current_review()
            return (
                configured.policy == review.policy
                and payload_fingerprint(configured.model_dump(mode="json"))
                == review.evidence["delivery_routes"]
            )
        except (ValueError, KeyError, finance.FundingUnavailable):
            return False
    if audience != "canary" or user_id is None:
        return False
    from .canary import require_participation
    require_participation(db, user_id=user_id, runtime=configured, now=now)
    return True


def enabled_for(user_id: int, *, db=None, now: datetime | None = None) -> bool:
    try:
        current_runtime(user_id, db=db, now=now)
        return True
    except finance.FundingUnavailable:
        return False


def current_runtime(user_id: int | None, *, db=None, now: datetime | None = None) -> RuntimeConfig:
    if os.getenv("UNIFIED_CREDIT_EXECUTION_ENABLED", "").strip().lower() != "true":
        raise finance.FundingUnavailable("Unified paid execution is not enabled for this account")
    stamp = service._stamp(now)
    try:
        runtime = RuntimeConfig.model_validate_json(
            os.getenv("UNIFIED_CREDIT_RUNTIME_REVIEW_JSON", "")
        )
        commercial = os.getenv("APP_ENV", "production").lower() not in {
            "test",
            "development",
            "dev",
            "local",
            "staging",
        }
        finance.require_current_policy(runtime.policy, now=stamp, commercial=commercial)
        if not runtime.budgets.valid_from <= stamp < runtime.budgets.expires_at:
            raise ValueError("The execution budget review expired")
        if not _audience_enabled(db, user_id, runtime, now=stamp):
            raise finance.FundingUnavailable("Unified paid execution is not enabled for this account")
        return runtime
    except (ValueError, finance.FundingUnavailable) as exc:
        raise finance.FundingUnavailable(
            "Paid execution awaits a current reviewed delivery configuration"
        ) from exc


def resolve_route(
    user_id: int,
    operation: str,
    input_payload: dict,
    *,
    db=None,
    observed_limits: dict[str, int] | None = None,
    now: datetime | None = None,
) -> QualifiedRoute:
    config = current_runtime(user_id, db=db, now=now)
    route = config.resolve_route(operation, input_payload, observed_limits=observed_limits)
    require_route(route, config.policy, now=service._stamp(now))
    return route


def has_unified_contract(db, user_id: int) -> bool:
    """Existing-table history survives an execution pause without currency fallback.

    Accepted payment intent counts even before capture. Accepted non-purchase
    canary work counts through its durable analysis billing snapshot. This read
    does not require newly migrated credit tables during a compatibility deploy.
    """
    from ...models import AdminAuditEvent, AnalysisRun, PaymentOrder
    from ..employer.models import EmployerSearch

    if db.query(AdminAuditEvent.id).filter_by(action="credit_canary_consent", target_type="credit_canary",
            actor_user_id=user_id).filter(AdminAuditEvent.after_state["choice"].as_string() == "opt_in").first():
        return True  # Retained explicit participation cannot fall back to another currency.
    if db.query(PaymentOrder.id).filter_by(user_id=user_id, entitlement_kind="credit_pass").first():
        return True
    if db.query(EmployerSearch.id).filter(EmployerSearch.user_id == user_id,
            EmployerSearch.scope["billing_currency"].as_string() == "credits").first():
        return True
    return (
        db.query(AnalysisRun.id)
        .filter(
            AnalysisRun.user_id == user_id,
            AnalysisRun.model_cost_quote["billing_currency"].as_string() == "credits",
        )
        .first()
        is not None
    )


def requires_unified_billing(db, user_id: int) -> bool:
    from . import canary

    if canary.requested(db):
        try:
            reviewed, _ = canary.load_review(db)
        except (finance.FundingUnavailable, ValueError, KeyError, TypeError):
            return True  # An unverifiable declaration cannot switch currency.
        if user_id in reviewed.selected_owner_ids:
            return True
    return enabled_for(user_id, db=db) or has_unified_contract(db, user_id)
