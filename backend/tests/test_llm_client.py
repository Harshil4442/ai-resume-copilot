from types import SimpleNamespace

import pytest
from backend.app.domains.analysis import tasks
from backend.app.services import llm_client
from google import genai


def test_gemini_transient_failure_uses_next_stable_model(monkeypatch):
    attempts = []

    class FakeModels:
        def generate_content(self, *, model, contents, config):
            attempts.append(model)
            assert config.temperature is None
            if model == "gemini-3.5-flash":
                raise RuntimeError("503 UNAVAILABLE: model is experiencing high demand")
            return SimpleNamespace(text="completed")

    monkeypatch.setenv("LLM_API_KEY", "test-key")
    monkeypatch.setattr(llm_client, "LLM_MODEL", "gemini-3.5-flash")
    monkeypatch.setattr(genai, "Client", lambda **kwargs: SimpleNamespace(models=FakeModels()))

    result = llm_client._chat([{"role": "user", "content": "Analyze this role"}])

    assert result == "completed"
    assert attempts == ["gemini-3.5-flash", "gemini-3.6-flash"]


def test_exhausted_transient_models_remain_retryable(monkeypatch):
    class UnavailableModels:
        def generate_content(self, *, model, contents, config):
            raise RuntimeError("503 UNAVAILABLE: temporary provider capacity issue")

    monkeypatch.setenv("LLM_API_KEY", "test-key")
    monkeypatch.setattr(llm_client, "LLM_MODEL", "gemini-3.6-flash")
    monkeypatch.setattr(
        genai,
        "Client",
        lambda **kwargs: SimpleNamespace(models=UnavailableModels()),
    )

    with pytest.raises(llm_client.LLMProviderError) as exc_info:
        llm_client._chat([{"role": "user", "content": "Analyze this role"}])

    assert exc_info.value.retryable is True
    assert tasks._retryable(exc_info.value) is True


def test_retryable_detection_inspects_wrapped_provider_error():
    provider_error = RuntimeError("503 UNAVAILABLE")
    wrapped_error = RuntimeError("LLM provider request failed")
    wrapped_error.__cause__ = provider_error

    assert tasks._retryable(wrapped_error) is True
