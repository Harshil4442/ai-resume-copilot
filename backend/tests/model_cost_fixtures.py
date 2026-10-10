"""Synthetic prices for admission invariants, never a deployment price policy."""
from backend.app.services.llm_client import GEMINI_FALLBACK_MODELS


def synthetic_policy(ceiling=10_000_000, output_limit=1024):
    operations = (
        "job_match", "interview_questions", "resume_tailor", "resume_enrichment",
        "resume_tailor_legacy", "rewrite_bullets", "interview_questions_legacy",
        "job_match_legacy", "match_question", "market_analysis_legacy", "learning_strategy",
    )
    quotes = []
    for provider, model, endpoint, parameter in [
        *(("google", model, "https://generativelanguage.googleapis.com", "max_output_tokens")
          for model in GEMINI_FALLBACK_MODELS),
        ("openai", "gpt-4o-mini", "https://api.openai.com/v1", "max_completion_tokens"),
        ("openai_compatible", "synthetic-compatible", "https://provider.example/v1", "max_tokens"),
    ]:
        quotes.append({
            "provider": provider, "model": model, "api_base": endpoint,
            "version": "synthetic-price-v1", "price_source_url": "https://example.com/synthetic-test-prices",
            "output_limit_source_url": "https://example.com/synthetic-test-output-limits",
            "usage_source_url": "https://example.com/synthetic-test-usage",
            "valid_from": "2020-01-01T00:00:00Z", "expires_at": "2099-01-01T00:00:00Z",
            "input_rate_micros_per_million": 1_000_000,
            "output_rate_micros_per_million": 1_000_000,
            "max_input_tokens": 1_000_000, "max_output_tokens": output_limit,
            "output_limit_parameter": parameter, "token_estimator": "utf8-json-bytes-framing-v1",
        })
    return {"version": "synthetic-policy-v1", "currency": "USD",
            "operation_limits_micros": {operation: ceiling for operation in operations},
            "pricing_quotes": quotes}
