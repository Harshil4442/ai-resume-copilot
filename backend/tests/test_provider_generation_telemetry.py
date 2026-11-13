import json
import logging

import pytest
from backend.app.observability import JsonFormatter, correlation_id_var
from backend.app.services.generation_budget import GenerationBudget


def test_generation_finish_emits_only_bounded_provider_observations(caplog):
    budget = GenerationBudget()
    record = {"provider": "google", "model": "private-model-name", "event_id": "private-event-id"}
    token = correlation_id_var.set("private-correlation-id")
    try:
        with caplog.at_level(logging.INFO, logger="model_generation"):
            budget.finish(record, latency_ms=451, response={"result": "private-result"})
            budget.finish(record, latency_ms=400_000, error=RuntimeError("private-error"))
            budget.finish({"provider": "private-custom-provider"}, latency_ms=200)
            budget.finish(record, latency_ms=-1)
            budget.finish(record, latency_ms=True)
            events = [json.loads(JsonFormatter().format(row)) for row in caplog.records]
    finally:
        correlation_id_var.reset(token)

    assert events == [
        {
            "event": "hirewiz_model_generation_latency_v1",
            "severity": "INFO",
            "provider": "google",
            "outcome": "succeeded",
            "latency_ms": 451,
        },
        {
            "event": "hirewiz_model_generation_latency_v1",
            "severity": "INFO",
            "provider": "google",
            "outcome": "failed",
            "latency_ms": 300_000,
        },
    ]


def test_generation_telemetry_does_not_mask_settlement_failure(monkeypatch):
    settlement_error = RuntimeError("settlement unavailable")

    def reject_settlement(*_):
        raise settlement_error

    def reject_telemetry(*_, **__):
        raise RuntimeError("logging unavailable")

    monkeypatch.setattr(logging.getLogger("model_generation"), "info", reject_telemetry)
    record = {"provider": "google"}
    with pytest.raises(RuntimeError) as raised:
        GenerationBudget(after=reject_settlement).finish(record, latency_ms=300)

    assert raised.value is settlement_error
    assert record["status"] == "succeeded"
