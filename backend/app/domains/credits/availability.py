"""Pure owned credit availability; shared by legacy reads and account bootstrap."""
from __future__ import annotations

import os
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from ...models import User
from . import release
from .finance import FundingUnavailable


def route_available(route, user_id: int | None, email: str = "") -> bool:
    """A financial qualification does not override a product kill switch."""
    from ...feature_flags import decide_feature
    from ...services.generation_gate import generation_enabled
    from ..employer import config as employer_config

    asynchronous = {"enhanced_job_match", "interview_questions", "resume_tailor",
                    "resume_tailor_extended", "market_report"}
    synchronous = {"resume_enrichment", "bullet_rewrite", "enhanced_qa", "learning_strategy"}
    if route.operation == "verified_submission":
        from ..browser_assistance import attachments, hosted_network, submissions
        from ..browser_assistance import service as browser
        from . import submission

        # Product support only; the owner/quote/cash/native checks still run
        # independently at approval and immediately before the one-shot send.
        return (submission.enabled() and browser.enabled() and browser.typed_controls_enabled()
            and attachments.enabled() and hosted_network.enabled() and submissions.enabled()
            and os.getenv("BROWSER_SUBMIT_NATIVE_ENABLED", "false").lower() == "true"
            and route.mode == "native" and not route.model_quotes and route.credits == 3
            and route.output_contract_sha256 == submission.delivery_contract())
    if route.operation not in asynchronous | synchronous | {"job_discovery_batch"}:
        return False  # A future tariff alone never makes a service purchasable.
    if route.mode == "enhanced" and not generation_enabled():
        return False
    if route.operation == "job_discovery_batch":
        return employer_config.discovery_enabled() and employer_config.paid_history_mode() == "indexed"
    if route.operation in asynchronous:
        features = ["async_analysis"]
        if route.operation in {"resume_tailor", "resume_tailor_extended"}:
            features.append("evidence_tailoring")
        for feature in features:
            decision = decide_feature(feature, user_id=user_id or 0, email=email)
            if not decision.enabled or (user_id is None and decision.rollout_percent != 100):
                return False
    return True


def availability_view(db: Session, current_user: User) -> dict:
    from ..free_core import policy as free_policy
    from .runtime import current_runtime
    from .service_quotes import require_route

    supported = []
    try:
        runtime = current_runtime(current_user.id, db=db)
        for route in runtime.routes:
            try:
                require_route(route, runtime.policy, now=datetime.now(UTC))
                if route_available(route, current_user.id, current_user.email):
                    supported.append(route.operation)
            except FundingUnavailable:
                continue
    except FundingUnavailable:
        pass
    free_limits = {}
    if free_policy.enabled():
        try:
            funded = free_policy.current_policy()
            free_limits = {name: {"per_account_30_days": limits.per_account_30_days,
                "max_input_bytes": limits.max_input_bytes, "max_source_records": limits.max_source_records}
                for name, limits in funded.operations.items()}
        except free_policy.FreeCoreUnavailable:
            pass
    return {"catalog_enabled": release.rollout_enabled(), "execution_enabled": bool(supported),
            "supported_operations": sorted(supported), "free_core_operations": sorted(free_limits),
            "free_core_limits": free_limits}

