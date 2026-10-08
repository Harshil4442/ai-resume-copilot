"""Operator-reviewed prices; no inferred, default, or purported invoice prices.

All money is integer USD millionths. Admission is a conservative *estimate*,
not a claim about a provider invoice or an exact provider tokenizer.
"""
from __future__ import annotations

import json
import os
import re
from datetime import UTC, datetime
from typing import Annotated, Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class ModelCostUnavailable(RuntimeError):
    retryable = False
    budget_exhausted = True


Positive = Annotated[int, Field(strict=True, gt=0, le=10**12)]
TokenBound = Annotated[int, Field(strict=True, gt=0, le=1_000_000)]
Identifier = Annotated[str, Field(min_length=1, max_length=120, pattern=r"^[A-Za-z0-9_.:/-]+$")]


class ModelPriceQuote(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: Literal["google", "openai", "openai_compatible"]
    model: Identifier
    api_base: str
    version: Identifier
    price_source_url: str
    output_limit_source_url: str
    usage_source_url: str
    valid_from: datetime
    expires_at: datetime
    input_rate_micros_per_million: Positive
    output_rate_micros_per_million: Positive
    max_input_tokens: TokenBound
    max_output_tokens: TokenBound
    output_limit_parameter: Literal["max_output_tokens", "max_completion_tokens", "max_tokens"]
    token_estimator: Literal["utf8-json-bytes-framing-v1"]

    @field_validator("api_base", "price_source_url", "output_limit_source_url", "usage_source_url")
    @classmethod
    def safe_url(cls, value: str) -> str:
        if len(value) > 2048 or any(character.isspace() or ord(character) < 32 for character in value):
            raise ValueError("Public quote URLs cannot contain whitespace or control characters")
        parts = urlsplit(value)
        if (parts.scheme != "https" or not parts.hostname or parts.username or parts.password
                or parts.query or parts.fragment or parts.port not in (None, 443)):
            raise ValueError("A public HTTPS URL without credentials or query parameters is required")
        return value.rstrip("/")

    @model_validator(mode="after")
    def provider_contract(self):
        if (self.valid_from.tzinfo is None or self.expires_at.tzinfo is None
                or self.expires_at <= self.valid_from):
            raise ValueError("A finite timezone-aware price validity interval is required")
        if self.provider == "google" and (
            self.api_base != "https://generativelanguage.googleapis.com"
            or self.output_limit_parameter != "max_output_tokens"
        ):
            raise ValueError("Google uses the native generateContent output limit")
        if self.provider == "openai" and (
            self.api_base != "https://api.openai.com/v1"
            or self.output_limit_parameter != "max_completion_tokens"
        ):
            raise ValueError("OpenAI uses the completion-token limit")
        if self.provider == "openai_compatible" and self.output_limit_parameter == "max_output_tokens":
            raise ValueError("Compatible chat providers need their explicit supported chat output parameter")
        return self


class ModelCostPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: Identifier
    currency: Literal["USD"]
    operation_limits_micros: dict[str, Positive]
    pricing_quotes: list[ModelPriceQuote] = Field(min_length=1, max_length=100)

    @field_validator("operation_limits_micros")
    @classmethod
    def known_operation_identifiers(cls, value):
        if not value or any(not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", key) for key in value):
            raise ValueError("Explicit operation identifiers and positive ceilings are required")
        return value

    @model_validator(mode="after")
    def unique_models(self):
        keys = [(item.provider, item.model, item.api_base) for item in self.pricing_quotes]
        if len(keys) != len(set(keys)):
            raise ValueError("A model endpoint must have exactly one pricing quote")
        return self


def current_policy() -> ModelCostPolicy:
    raw = os.getenv("LLM_MODEL_COST_POLICY_JSON", "")
    if not raw:
        raise ModelCostUnavailable("Optional AI generation is unavailable: configure a reviewed model-cost policy.")
    try:
        return ModelCostPolicy.model_validate_json(raw)
    except (ValueError, TypeError):
        # Never expose invalid configuration, prices, or provider strings.
        raise ModelCostUnavailable("Optional AI generation is unavailable: the model-cost policy is invalid.") from None


def freeze_run_quote(run) -> None:
    """Called before admission/commit; deliberately independent of product units."""
    if run.model_cost_quote is not None:
        return
    if run.generation_attempt_count or 0:
        raise ModelCostUnavailable("This legacy operation has unquoted model attempts. Start a new reviewed operation.")
    policy = current_policy()
    ceiling = policy.operation_limits_micros.get(run.operation)
    if ceiling is None:
        raise ModelCostUnavailable("Optional AI generation is unavailable: this operation has no cost ceiling.")
    now = datetime.now(UTC)
    if not any(q.valid_from <= now < q.expires_at for q in policy.pricing_quotes):
        raise ModelCostUnavailable("Optional AI generation is unavailable: the reviewed price quotes are expired or not yet valid.")
    run.model_cost_quote = policy.model_dump(mode="json")
    run.model_cost_ceiling_micros = ceiling
    run.model_cost_reserved_micros = 0
    run.model_cost_settled_micros = 0
    run.model_cost_state = "active"


def attempt_quote(run, provider: str, model: str, api_base: str) -> tuple[dict, int]:
    freeze_run_quote(run)
    frozen = ModelCostPolicy.model_validate(run.model_cost_quote)
    current = current_policy()
    ceiling = current.operation_limits_micros.get(run.operation)
    if ceiling is None:
        raise ModelCostUnavailable("The operation's model-cost authorization was revoked.")
    key = (provider, model, api_base.rstrip("/"))
    saved = next((q for q in frozen.pricing_quotes if (q.provider, q.model, q.api_base) == key), None)
    active = next((q for q in current.pricing_quotes if (q.provider, q.model, q.api_base) == key), None)
    if saved is None or active is None:
        raise ModelCostUnavailable("Optional AI generation is unavailable: the exact model endpoint has no authorized price quote.")
    now = datetime.now(UTC)
    if not (saved.valid_from <= now < saved.expires_at and active.valid_from <= now < active.expires_at):
        raise ModelCostUnavailable("The exact model-price quote is expired or not yet valid. Refresh the reviewed policy and start a new operation.")
    # New prices/model permissions cannot silently rewrite the old operation's
    # economics. A reduced output/input bound may tighten future admissions.
    saved_values = saved.model_dump(mode="json")
    active_values = active.model_dump(mode="json")
    for field in ("max_input_tokens", "max_output_tokens"):
        active_values[field] = saved_values[field]
    if active_values != saved_values:
        raise ModelCostUnavailable("This operation's model-price authorization changed. Start a new reviewed operation.")
    quote = dict(saved_values)
    quote["max_input_tokens"] = min(saved.max_input_tokens, active.max_input_tokens)
    quote["max_output_tokens"] = min(saved.max_output_tokens, active.max_output_tokens)
    effective_ceiling = min(run.model_cost_ceiling_micros, ceiling)
    quote["operation_policy_version"] = frozen.version
    quote["admission_policy_version"] = current.version
    quote["authorized_ceiling_micros"] = effective_ceiling
    return quote, effective_ceiling


def input_token_estimate(messages: list[dict]) -> int:
    # UTF-8 bytes (rather than len/4) plus explicit framing. This is a versioned
    # conservative estimate, not a certified tokenizer or provider invoice cap.
    payload = json.dumps(messages, ensure_ascii=False, separators=(",", ":"))
    return len(payload.encode("utf-8")) + 64 * len(messages) + 128


def quoted_cost(quote: dict, input_tokens: int, output_tokens: int) -> int:
    numerator = (input_tokens * quote["input_rate_micros_per_million"]
                 + output_tokens * quote["output_rate_micros_per_million"])
    return (numerator + 999_999) // 1_000_000
