"""Durable attempt and quoted-estimated-spend admission before network calls.

No prompt, API credential, provider error body, or response body is persisted.
Unknown outcomes retain their reservation across workers and process crashes.
"""
from __future__ import annotations

import contextlib
import contextvars
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from .model_cost_policy import (
    ModelCostUnavailable,
    attempt_quote,
    input_token_estimate,
    quoted_cost,
)


class GenerationBudgetExhausted(ModelCostUnavailable):
    pass


@dataclass
class GenerationBudget:
    limit: int = 3
    used: int = 0
    run_id: str | None = None
    before: Callable[..., Any] | None = None
    after: Callable[..., None] | None = None
    attempts: list[dict] = field(default_factory=list)

    def admit(self, provider: str, model: str, messages: list[dict], *, api_base: str) -> dict:
        if self.before is None or self.after is None:
            raise GenerationBudgetExhausted("Optional AI generation requires a persisted operation budget.")
        if self.used >= self.limit:
            raise GenerationBudgetExhausted("The operation's total model-attempt budget was reached.")
        record = self.before(provider, model, messages, api_base)
        self.used = record["attempt_number"]
        self.attempts.append(record)
        return record

    def finish(self, record: dict, *, latency_ms: int, response: Any = None, error: Exception | None = None):
        record["status"] = "failed" if error else "succeeded"
        if self.after:
            self.after(record, latency_ms, response, error)


_active: contextvars.ContextVar[GenerationBudget | None] = contextvars.ContextVar("generation_budget", default=None)


def current_budget() -> GenerationBudget | None:
    return _active.get()


@contextlib.contextmanager
def generation_budget(budget: GenerationBudget | None = None):
    existing = current_budget()
    if existing is not None:
        yield existing
        return
    token = _active.set(budget or GenerationBudget())
    try:
        yield current_budget()
    finally:
        _active.reset(token)


def _count(value: Any) -> int | None:
    # bool is an int in Python; reject it and negative/overflow usage explicitly.
    return value if type(value) is int and 0 <= value <= 2_147_483_647 else None


def _usage(response: Any, provider: str) -> tuple[int, int, str] | None:
    if provider == "google":
        usage = getattr(response, "usage_metadata", None)
        if usage is None:
            return None
        prompt = _count(getattr(usage, "prompt_token_count", None))
        candidates = _count(getattr(usage, "candidates_token_count", None))
        total = _count(getattr(usage, "total_token_count", None))
        thoughts = _count(getattr(usage, "thoughts_token_count", None))
        if prompt is None or candidates is None:
            return None
        if total is not None and total >= prompt + candidates:
            if thoughts is not None and total != prompt + candidates + thoughts:
                return None
            return prompt, total - prompt, "google-total-including-thoughts-v1"
        if thoughts is not None and total is None:
            return prompt, candidates + thoughts, "google-explicit-thoughts-v1"
        # Missing thoughts/total is not evidence of zero billable reasoning.
        return None
    if isinstance(response, dict) and isinstance(response.get("usage"), dict):
        usage = response["usage"]
        prompt = _count(usage.get("prompt_tokens"))
        completion = _count(usage.get("completion_tokens"))
        if prompt is not None and completion is not None:
            return prompt, completion, "chat-completion-including-reasoning-v1"
    return None


@contextlib.contextmanager
def persistent_run_budget(factory, run_id: str):
    from .. import models
    from ..domains.common import public_id, utcnow

    existing = current_budget()
    if existing is not None:
        if existing.run_id != run_id:
            raise GenerationBudgetExhausted("A model budget cannot be shared across distinct operations.")
        yield existing
        return

    with factory() as db:
        run = db.get(models.AnalysisRun, run_id)
        if not run:
            raise RuntimeError("Analysis run was not found")
        budget = GenerationBudget(limit=run.generation_attempt_limit, used=run.generation_attempt_count, run_id=run_id)
        user_id = run.user_id
        prompt_version = {"job_match": "match-mega-v2", "interview_questions": "interview-evidence-v4", "resume_tailor": "resume-source-v5"}.get(run.operation, "legacy-v1")

    def before(provider, model, messages, api_base):
        with factory() as db:
            run = db.query(models.AnalysisRun).filter(models.AnalysisRun.id == run_id).with_for_update().one()
            if run.cancel_requested or run.status not in {"queued", "running"}:
                raise GenerationBudgetExhausted("The operation is no longer authorized for model requests.")
            if run.generation_attempt_count >= run.generation_attempt_limit:
                raise GenerationBudgetExhausted("The operation's total model-attempt budget was reached.")
            if run.model_cost_state == "overrun":
                raise GenerationBudgetExhausted("Returned usage exceeded the authorized estimate. Further model attempts are blocked.")
            if run.model_cost_quote is None and db.query(models.ModelCallEvent.id).filter_by(analysis_run_id=run_id).first():
                raise ModelCostUnavailable("This legacy operation has unquoted model history. Start a new reviewed operation.")
            quote, current_ceiling = attempt_quote(run, provider, model, api_base)
            input_tokens = input_token_estimate(messages)
            if input_tokens > quote["max_input_tokens"]:
                raise GenerationBudgetExhausted("The prompt exceeds the operation's authorized input estimate.")
            reservation = quoted_cost(quote, input_tokens, quote["max_output_tokens"])
            spent = run.model_cost_reserved_micros + run.model_cost_settled_micros
            if spent + reservation > current_ceiling:
                raise GenerationBudgetExhausted("The operation's quoted maximum authorized estimated spend was reached.")
            run.generation_attempt_count += 1
            run.model_cost_reserved_micros += reservation
            run.provider, run.model, run.prompt_version = provider, model, prompt_version
            event_id = public_id("mdl")
            db.add(models.ModelCallEvent(
                id=event_id, analysis_run_id=run_id, user_id=user_id,
                provider=provider, model=model, prompt_version=prompt_version,
                attempt_number=run.generation_attempt_count,
                input_tokens=input_tokens, output_tokens=0, tokens_estimated=True,
                estimated_cost_micros=reservation, reserved_cost_micros=reservation,
                pricing_quote=quote, cost_state="reserved",
                token_estimate_provenance=quote["token_estimator"],
                output_token_limit=quote["max_output_tokens"], status="attempted", created_at=utcnow(),
            ))
            # Only this short transaction holds the run lock. It ends before
            # SDK construction/network; even a killed process leaves the hold.
            attempt_number = run.generation_attempt_count
            db.commit()
            return {"event_id": event_id, "provider": provider, "model": model,
                    "attempt_number": attempt_number,
                    "max_output_tokens": quote["max_output_tokens"],
                    "output_limit_parameter": quote["output_limit_parameter"]}

    def after(record, latency_ms, response, error):
        with factory() as db:
            # All monetary mutations use run -> event ordering, including late
            # reconciliation. Duplicate callbacks cannot double-release holds.
            run = db.query(models.AnalysisRun).filter_by(id=run_id).with_for_update().one()
            event = db.query(models.ModelCallEvent).filter_by(
                id=record["event_id"], analysis_run_id=run_id, user_id=user_id,
            ).with_for_update().one_or_none()
            if event is None:
                raise ModelCostUnavailable("The model-cost event does not belong to this operation.")
            if event.settled_at is not None:
                return
            event.status = "failed" if error else "succeeded"
            event.latency_ms = max(0, latency_ms)
            event.error_code = "provider_error" if error else None
            usage = _usage(response, event.provider) if error is None else None
            if usage is None:
                event.cost_state = "outcome_unknown" if error else "usage_unavailable"
                # Keep *all* reserved cost. No error classification proves free.
                db.commit()
                return
            input_tokens, output_tokens, provenance = usage
            cost = quoted_cost(event.pricing_quote, input_tokens, output_tokens)
            event.input_tokens, event.output_tokens = input_tokens, output_tokens
            event.tokens_estimated = False
            event.usage_provenance = provenance
            event.estimated_cost_micros = event.settled_cost_micros = cost
            event.settled_at = utcnow()
            run.model_cost_reserved_micros -= event.reserved_cost_micros
            run.model_cost_settled_micros += cost
            overrun = (cost > event.reserved_cost_micros
                       or output_tokens > event.output_token_limit
                       or input_tokens > event.pricing_quote["max_input_tokens"]
                       or run.model_cost_reserved_micros + run.model_cost_settled_micros > run.model_cost_ceiling_micros)
            event.cost_state = "overrun" if overrun else "settled"
            if overrun:
                run.model_cost_state = "overrun"
            db.commit()

    budget.before, budget.after = before, after
    with generation_budget(budget):
        yield budget
