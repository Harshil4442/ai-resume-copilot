"""No live Gemini calls: REST counting, SDK input parity, and funding guards."""

from __future__ import annotations

from types import SimpleNamespace

import httpx
import pytest
from backend.app import models
from backend.app.services import llm_client
from backend.app.services.gemini_token_count import count_gemini_input, gemini_input
from backend.app.services.generation_budget import persistent_run_budget
from backend.app.services.model_cost_policy import ModelCostUnavailable
from backend.tests.test_ai_efficiency import _database
from backend.tests.test_model_cost_budgets import new_run
from google import genai
from google.genai import models as genai_models
from google.genai import types


def _response(url: str, status: int, payload: dict) -> httpx.Response:
    return httpx.Response(status, request=httpx.Request("POST", url), json=payload)


def test_full_rest_count_input_matches_pinned_sdk_generation_input(monkeypatch):
    messages = [
        {"role": "system", "content": "Keep evidence. 日本語"},
        {"role": "user", "content": "Résumé and हिन्दी"},
        {"role": "assistant", "content": "Prior answer"},
        {"role": "user", "content": "Repair the answer 🔧"},
    ]
    contents, system, request_input = gemini_input(messages)
    client = genai.Client(vertexai=False, api_key="synthetic", http_options=types.HttpOptions(api_version="v1beta"))
    try:
        config = types.GenerateContentConfig(system_instruction=system, max_output_tokens=1024)
        params = types._GenerateContentParameters(
            model="gemini-3.6-flash", contents=contents, config=config,
        )
        wire = genai_models._GenerateContentParameters_to_mldev(client._api_client, params, None, params)
    finally:
        client.close()
    assert {key: wire[key] for key in request_input} == request_input
    assert wire["generationConfig"]["maxOutputTokens"] == 1024

    seen = []

    def post(url, **kwargs):
        seen.append((url, kwargs))
        return _response(url, 200, {"totalTokens": 93})

    monkeypatch.setattr(httpx, "post", post)
    assert count_gemini_input("gemini-3.6-flash", request_input, "synthetic") == 93
    assert len(seen) == 1
    assert seen[0][0].endswith("/models/gemini-3.6-flash:countTokens")
    counted = seen[0][1]["json"]
    assert set(counted) == {"generateContentRequest"}
    assert counted["generateContentRequest"] == {"model": "models/gemini-3.6-flash", **request_input}
    assert seen[0][1]["follow_redirects"] is False
    assert seen[0][1]["timeout"].connect == 2.0


@pytest.mark.parametrize("failure", ["rate", "timeout", "malformed"])
def test_count_transport_fails_closed_without_retry_or_prompt_in_error(monkeypatch, failure):
    calls = []

    def post(url, **kwargs):
        calls.append(kwargs)
        if failure == "timeout":
            raise httpx.ReadTimeout("private résumé text")
        if failure == "rate":
            return _response(url, 429, {"error": "private résumé text"})
        return _response(url, 200, {"totalTokens": "12"})

    monkeypatch.setattr(httpx, "post", post)
    with pytest.raises(ModelCostUnavailable, match="token preflight") as error:
        count_gemini_input("gemini-3.6-flash", {"contents": []}, "synthetic")
    assert len(calls) == 1
    assert "private" not in str(error.value)


@pytest.mark.parametrize("counted", [None, 1_000_000])
def test_count_failure_or_oversize_prevents_provider_attempt_and_cost(monkeypatch, counted):
    engine, factory = _database()
    try:
        run_id = new_run(factory)
        monkeypatch.setenv("LLM_API_KEY", "synthetic")
        monkeypatch.setattr(llm_client, "LLM_MODEL", "gemini-3.6-flash")
        monkeypatch.setattr(genai, "Client", lambda **_: pytest.fail("No paid generation on failed preflight"))
        if counted is None:
            def fail(*_):
                raise ModelCostUnavailable("Gemini token preflight is unavailable")
            monkeypatch.setattr(llm_client, "count_gemini_input", fail)
        else:
            monkeypatch.setattr(llm_client, "count_gemini_input", lambda *_: counted)
        with persistent_run_budget(factory, run_id):
            with pytest.raises(llm_client.LLMProviderError):
                llm_client._chat([{"role": "user", "content": "Synthetic prompt"}])
        with factory() as db:
            run = db.get(models.AnalysisRun, run_id)
            assert run.generation_attempt_count == 0
            assert run.model_cost_reserved_micros == 0
            assert db.query(models.ModelCallEvent).count() == 0
            assert db.get(models.User, 1).ai_credits == 100
    finally:
        engine.dispose()


def test_fallback_recounts_for_each_model(monkeypatch):
    engine, factory = _database()
    try:
        run_id = new_run(factory)
        monkeypatch.setenv("LLM_API_KEY", "synthetic")
        monkeypatch.setattr(llm_client, "LLM_MODEL", "gemini-3.6-flash")
        counted = []

        def count(model, request_input, _key):
            counted.append((model, request_input["contents"][-1]["parts"][0]["text"]))
            return 10

        class FakeModels:
            def generate_content(self, *, model, **_):
                if model == "gemini-3.6-flash":
                    raise RuntimeError("503 temporary provider capacity")
                return SimpleNamespace(
                    text="done",
                    usage_metadata=SimpleNamespace(
                        prompt_token_count=10, candidates_token_count=4,
                        thoughts_token_count=1, total_token_count=15,
                    ),
                )

        monkeypatch.setattr(llm_client, "count_gemini_input", count)
        monkeypatch.setattr(genai, "Client", lambda **_: SimpleNamespace(models=FakeModels()))
        with persistent_run_budget(factory, run_id):
            assert llm_client._chat([{"role": "user", "content": "Initial"}]) == "done"
        assert counted == [
            ("gemini-3.6-flash", "Initial"),
            ("gemini-3.5-flash-lite", "Initial"),
        ]
    finally:
        engine.dispose()


def test_second_repair_call_counts_its_changed_full_prompt(monkeypatch):
    engine, factory = _database()
    try:
        run_id = new_run(factory)
        monkeypatch.setenv("LLM_API_KEY", "synthetic")
        monkeypatch.setattr(llm_client, "LLM_MODEL", "gemini-3.6-flash")
        counted = []

        def count(model, request_input, _key):
            counted.append((model, request_input))
            return 10

        class FakeModels:
            def generate_content(self, **_):
                return SimpleNamespace(
                    text="done", usage_metadata=SimpleNamespace(
                        prompt_token_count=10, candidates_token_count=4,
                        thoughts_token_count=1, total_token_count=15,
                    ),
                )

        monkeypatch.setattr(llm_client, "count_gemini_input", count)
        monkeypatch.setattr(genai, "Client", lambda **_: SimpleNamespace(models=FakeModels()))
        initial = [{"role": "system", "content": "Use facts"}, {"role": "user", "content": "Draft"}]
        repaired = [*initial, {"role": "user", "content": "Fix validation issue"}]
        with persistent_run_budget(factory, run_id):
            assert llm_client._chat(initial) == "done"
            assert llm_client._chat(repaired) == "done"
        assert [item[0] for item in counted] == ["gemini-3.6-flash"] * 2
        assert counted[0][1] != counted[1][1]
        assert counted[0][1]["systemInstruction"] == counted[1][1]["systemInstruction"]
        with factory() as db:
            assert db.get(models.AnalysisRun, run_id).generation_attempt_count == 2
    finally:
        engine.dispose()


def test_unreviewed_sdk_version_fails_before_count_or_generation(monkeypatch):
    engine, factory = _database()
    try:
        run_id = new_run(factory)
        monkeypatch.setenv("LLM_API_KEY", "synthetic")
        monkeypatch.setattr(llm_client, "LLM_MODEL", "gemini-3.6-flash")
        monkeypatch.setattr(genai, "__version__", "2.17.0")
        monkeypatch.setattr(llm_client, "count_gemini_input", lambda *_: pytest.fail("No count on unreviewed SDK"))
        monkeypatch.setattr(genai, "Client", lambda **_: pytest.fail("No generation on unreviewed SDK"))
        with persistent_run_budget(factory, run_id):
            with pytest.raises(llm_client.LLMProviderError, match="reviewed SDK version"):
                llm_client._chat([{"role": "user", "content": "Synthetic"}])
        with factory() as db:
            assert db.get(models.AnalysisRun, run_id).generation_attempt_count == 0
    finally:
        engine.dispose()
