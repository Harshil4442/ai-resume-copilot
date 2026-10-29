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


def enabled_for(user_id: int) -> bool:
    if os.getenv("UNIFIED_CREDIT_EXECUTION_ENABLED", "").strip().lower() != "true":
        return False
    audience = os.getenv("UNIFIED_CREDIT_EXECUTION_AUDIENCE", "canary").strip().lower()
    if audience == "reviewed_public":
        # This digest covers the complete canonical policy, budgets and routes,
        # not just a name or caller-authored claim that qualification passed.
        from ..common import payload_fingerprint
        from .release import current_review

        try:
            configured = RuntimeConfig.model_validate_json(
                os.getenv("UNIFIED_CREDIT_RUNTIME_REVIEW_JSON", "")
            )
            review = current_review()
            return (
                configured.policy == review.policy
                and payload_fingerprint(configured.model_dump(mode="json"))
                == review.evidence["delivery_routes"]
            )
        except (ValueError, KeyError, finance.FundingUnavailable):
            return False
    if audience != "canary":
        return False
    raw = os.getenv("UNIFIED_CREDIT_CANARY_OWNER_IDS", "")
    parts = raw.split(",")
    if not parts or any(not value.strip().isdigit() or int(value.strip()) <= 0 for value in parts):
        return False
    return user_id in {int(value.strip()) for value in parts}


def current_runtime(user_id: int, *, now: datetime | None = None) -> RuntimeConfig:
    if not enabled_for(user_id):
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
    observed_limits: dict[str, int] | None = None,
    now: datetime | None = None,
) -> QualifiedRoute:
    config = current_runtime(user_id, now=now)
    route = config.resolve_route(operation, input_payload, observed_limits=observed_limits)
    require_route(route, config.policy, now=service._stamp(now))
    return route


def has_unified_contract(db, user_id: int) -> bool:
    """Existing-table history survives an execution pause without currency fallback.

    Accepted payment intent counts even before capture. Accepted non-purchase
    canary work counts through its durable analysis billing snapshot. This read
    does not require newly migrated credit tables during a compatibility deploy.
    """
    from ...models import AnalysisRun, PaymentOrder
    from ..employer.models import EmployerSearch

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
    return enabled_for(user_id) or has_unified_contract(db, user_id)
