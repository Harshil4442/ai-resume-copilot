"""Scoped fresh-input checks around existing provider budget callbacks.

This adds refusals only. It never creates a budget, issues a claim, completes a
supplier event, or changes the financial record produced by an original callback.
"""
from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from threading import Lock

from .generation_budget import GenerationBudget, GenerationBudgetExhausted, current_budget

_lock = Lock()
_owners: dict[int, tuple[GenerationBudget, object]] = {}


@contextmanager
def guard_provider_inputs(check: Callable[[], None], *, run_id: str) -> Iterator[None]:
    budget = current_budget()
    if budget is None or budget.run_id != run_id or budget.before is None or budget.after is None:
        raise GenerationBudgetExhausted("Fresh source checks require the operation's existing persistent budget.")
    token = object()
    with _lock:
        if id(budget) in _owners:
            raise GenerationBudgetExhausted("This provider budget already has an active input-check owner.")
        _owners[id(budget)] = (budget, token)
    before, preflight, after = budget.before, budget.preflight, budget.after
    callback_lock = Lock()

    def owned() -> None:
        with _lock:
            owner = _owners.get(id(budget))
        if (owner != (budget, token) or current_budget() is not budget or budget.run_id != run_id
                or budget.before is not wrapped_before or budget.after is not after
                or budget.preflight is not (wrapped_preflight if preflight is not None else None)):
            raise GenerationBudgetExhausted("The provider input-check owner changed.")

    def invoke(callback, *args, **kwargs):
        if not callback_lock.acquire(blocking=False):
            raise GenerationBudgetExhausted("Concurrent provider callbacks cannot share one input-check owner.")
        try:
            owned()
            check()
            result = callback(*args, **kwargs)
            # Native admission can perform IO. If inputs changed during it,
            # retain its claim/outcome exactly; no supplier request follows.
            owned()
            check()
            return result
        finally:
            callback_lock.release()

    def wrapped_before(*args, **kwargs):
        return invoke(before, *args, **kwargs)

    def wrapped_preflight(*args, **kwargs):
        assert preflight is not None
        return invoke(preflight, *args, **kwargs)

    budget.before = wrapped_before
    budget.preflight = wrapped_preflight if preflight is not None else None
    try:
        owned()
        check()
        yield
        owned()
    finally:
        # Restore exact original identities even on failed checks. Outstanding
        # claims remain in the original durable ledger and are never replayed.
        budget.before, budget.preflight, budget.after = before, preflight, after
        with _lock:
            if _owners.get(id(budget)) == (budget, token):
                del _owners[id(budget)]
