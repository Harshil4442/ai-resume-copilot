"""One budget across fallback models, output repair, and retried workers.

Attempt admission is committed before the external call. No prompt content is
stored here. Context-local budgets also cover legacy calls without a run.
"""
from __future__ import annotations

import contextlib
import contextvars
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any


class GenerationBudgetExhausted(RuntimeError):
    retryable = False
    budget_exhausted = True


@dataclass
class GenerationBudget:
    limit: int = 3
    used: int = 0
    before: Callable[..., Any] | None = None
    after: Callable[..., None] | None = None
    attempts: list[dict] = field(default_factory=list)

    def admit(self, provider: str, model: str, messages: list[dict]) -> dict:
        if self.used >= self.limit:
            raise GenerationBudgetExhausted("The operation's total model-attempt budget was reached.")
        record = {"provider": provider, "model": model, "attempt_number": self.used + 1}
        if self.before:
            record["event_id"] = self.before(provider, model, messages)
        self.used += 1
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
    budget = budget or GenerationBudget()
    token = _active.set(budget)
    try:
        yield budget
    finally:
        _active.reset(token)


def _usage(response: Any) -> tuple[int | None, int | None]:
    usage = getattr(response, "usage_metadata", None)
    if usage is not None:
        return getattr(usage, "prompt_token_count", None), getattr(usage, "candidates_token_count", None)
    if isinstance(response, dict):
        usage = response.get("usage", {})
        return usage.get("prompt_tokens"), usage.get("completion_tokens")
    return None, None


@contextlib.contextmanager
def persistent_run_budget(factory, run_id: str):
    from .. import models
    from ..domains.common import public_id, utcnow

    with factory() as db:
        run = db.get(models.AnalysisRun, run_id)
        if not run:
            raise RuntimeError("Analysis run was not found")
        budget = GenerationBudget(limit=run.generation_attempt_limit, used=run.generation_attempt_count)
        user_id = run.user_id
        prompt_version = {"job_match": "match-mega-v2", "interview_questions": "interview-evidence-v4", "resume_tailor": "resume-source-v5"}.get(run.operation, "legacy-v1")

    def before(provider, model, messages):
        # Increment under a short row lock and commit before network access;
        # a crash or rollback cannot erase an already admitted attempt.
        with factory() as db:
            run = db.query(models.AnalysisRun).filter(models.AnalysisRun.id == run_id).with_for_update().one()
            if run.cancel_requested:
                raise GenerationBudgetExhausted("The operation was cancelled before the model request.")
            if run.generation_attempt_count >= run.generation_attempt_limit:
                raise GenerationBudgetExhausted("The operation's total model-attempt budget was reached.")
            run.generation_attempt_count += 1
            run.provider = provider
            run.model = model
            run.prompt_version = prompt_version
            event_id = public_id("mdl")
            db.add(models.ModelCallEvent(
                id=event_id, analysis_run_id=run_id, user_id=user_id,
                provider=provider, model=model, prompt_version=prompt_version,
                attempt_number=run.generation_attempt_count,
                input_tokens=max(1, sum(len(str(m.get("content", ""))) for m in messages) // 4),
                output_tokens=0, tokens_estimated=True, status="attempted", created_at=utcnow(),
            ))
            db.commit()
            return event_id

    def after(record, latency_ms, response, error):
        with factory() as db:
            event = db.get(models.ModelCallEvent, record["event_id"])
            event.status = "failed" if error else "succeeded"
            event.latency_ms = latency_ms
            event.error_code = type(error).__name__[:80] if error else None
            input_tokens, output_tokens = _usage(response)
            if isinstance(input_tokens, int) and isinstance(output_tokens, int):
                event.input_tokens = max(0, input_tokens)
                event.output_tokens = max(0, output_tokens)
                event.tokens_estimated = False
            elif response is not None:
                output = response.get("choices", []) if isinstance(response, dict) else getattr(response, "text", "")
                event.output_tokens = len(str(output)) // 4
            # Rates are deployment configuration; zero denotes unavailable,
            # not evidence that the provider request was free.
            import os
            def rate(name):
                try:
                    return max(0, int(os.getenv(name, "0")))
                except ValueError:
                    return 0
            event.estimated_cost_micros = round((
                event.input_tokens * rate("LLM_INPUT_COST_MICROS_PER_MILLION") +
                event.output_tokens * rate("LLM_OUTPUT_COST_MICROS_PER_MILLION")
            ) / 1_000_000)
            db.commit()

    budget.before = before
    budget.after = after
    with generation_budget(budget):
        yield budget
