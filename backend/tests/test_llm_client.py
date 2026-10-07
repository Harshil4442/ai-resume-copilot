import json
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


def _question_set():
    questions = [
        "How would you design a distributed search index?",
        "How do you investigate high query latency?",
        "How do you keep search results fresh?",
        "How would you measure search quality?",
        "How do you handle a failed indexing worker?",
        "How do you test a ranking change?",
        "How do you resolve competing engineering priorities?",
        "Which project demonstrates your approach to reliability?",
    ]
    return [
        {
            "question": question,
            "coaching_angle": "Explain the context, your action, and the verified result.",
            "evidence_ids": [],
        }
        for question in questions
    ]


@pytest.mark.parametrize("format_name", ["array", "fenced", "object", "fenced_object", "prose"])
def test_interview_accepts_common_json_formats_without_a_fallback(monkeypatch, format_name):
    data = _question_set()
    content = json.dumps({"questions": data} if "object" in format_name else data)
    if "fenced" in format_name:
        content = f"```json\n{content}\n```"
    elif format_name == "prose":
        content = f"Here are the practice questions:\n{content}\nReview the evidence before practicing."
    calls = []

    def chat(messages):
        calls.append(messages)
        return content

    monkeypatch.setattr(llm_client, "_chat", chat)
    result = llm_client.generate_interview_questions("Search Engineer", "Build reliable search.")

    assert len(result) == 8
    assert [item["question"] for item in result] == [item["question"] for item in data]
    assert all(item["answer_state"] == "evidence_needed" for item in result)
    assert len(calls) == 1


@pytest.mark.parametrize(
    "bad_output", ["malformed", "short", "duplicate", "duplicate_spacing", "invalid_fields", "empty", "missing_content"]
)
def test_interview_repairs_an_unusable_first_response(monkeypatch, bad_output):
    data = _question_set()
    invalid = {
        "malformed": "Here are some interview questions, without JSON.",
        "short": json.dumps(data[:1]),
        "duplicate": json.dumps([data[0]] * 8),
        "duplicate_spacing": json.dumps([
            variant
            for item in data[:4]
            for variant in (item, {**item, "question": item["question"].replace("?", " ?")})
        ]),
        "invalid_fields": json.dumps([*data[:7], {"question": 123, "evidence_ids": []}]),
        "empty": "",
        "missing_content": None,
    }[bad_output]
    responses = iter([invalid, json.dumps(data)])
    calls = []

    def chat(messages):
        calls.append(list(messages))
        return next(responses)

    monkeypatch.setattr(llm_client, "_chat", chat)
    result = llm_client.generate_interview_questions("Search Engineer", "Build reliable search.")

    assert len(result) == 8
    assert len(calls) == 2
    assert "exactly 8 distinct questions" in calls[1][-1]["content"]


def test_interview_exhausted_repair_fails_without_becoming_a_retryable_run(monkeypatch):
    calls = []

    def chat(messages):
        calls.append(list(messages))
        return "Invalid JSON; provider prose mentions 429, 503 and timeout."

    monkeypatch.setattr(llm_client, "_chat", chat)
    with pytest.raises(llm_client.InterviewOutputError, match="complete set of 8") as error:
        llm_client.generate_interview_questions("Search Engineer", "Build reliable search.")

    assert len(calls) == 2
    assert error.value.__cause__ is None
    assert error.value.__context__ is None
    assert tasks._retryable(error.value) is False


def test_interview_filters_unknown_evidence_and_keeps_approved_facts(monkeypatch):
    data = _question_set()
    data[0]["evidence_ids"] = ["approved-1", "invented", "approved-1"]
    data[1]["evidence_ids"] = ["invented"]
    monkeypatch.setattr(llm_client, "_chat", lambda messages: json.dumps(data))

    result = llm_client.generate_interview_questions(
        "Search Engineer",
        "Build reliable search.",
        approved_evidence=[{"id": "approved-1", "title": "API project", "text": "Reduced latency by 20%."}],
    )

    assert len(result) == 8
    assert result[0]["evidence_ids"] == ["approved-1"]
    assert result[0]["answer_state"] == "evidence_backed"
    assert "Reduced latency by 20%." in result[0]["answer"]
    assert result[1]["evidence_ids"] == []
    assert result[1]["answer_state"] == "evidence_needed"


def test_interview_caps_an_oversized_response_to_the_requested_count(monkeypatch):
    monkeypatch.setattr(llm_client, "_chat", lambda messages: json.dumps(_question_set()))
    assert len(llm_client.generate_interview_questions("Search Engineer", "Build search.", 3)) == 3


def test_interview_provider_errors_are_not_hidden_by_output_repair(monkeypatch):
    def chat(messages):
        raise llm_client.LLMProviderError("Provider unavailable", retryable=True)

    monkeypatch.setattr(llm_client, "_chat", chat)
    with pytest.raises(llm_client.LLMProviderError) as error:
        llm_client.generate_interview_questions("Search Engineer", "Build search.")
    assert error.value.retryable is True
