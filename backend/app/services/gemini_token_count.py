"""Bounded Gemini input-token preflight for the exact text sent to generation.

The installed google-genai 2.16 SDK cannot pass system instructions to
count_tokens in Developer API mode. The documented REST generateContentRequest
form can, so count the same token-bearing input fields sent by the SDK.
"""

from __future__ import annotations

import re
import threading

import httpx

from .model_cost_policy import ModelCostUnavailable

_COUNT_SLOTS = threading.BoundedSemaphore(8)
_MODEL = re.compile(r"^gemini-[a-z0-9][a-z0-9.-]*$")
_BASE = "https://generativelanguage.googleapis.com/v1beta/models"


def gemini_input(messages: list[dict]) -> tuple[list[dict], str | None, dict]:
    """Build the SDK contents/config input and equivalent REST input fields."""
    system_text = "\n\n".join(m["content"] for m in messages if m["role"] == "system")
    contents = [
        {"role": "model" if m["role"] == "assistant" else "user", "parts": [{"text": m["content"]}]}
        for m in messages if m["role"] != "system"
    ]
    request_input: dict = {"contents": contents}
    if system_text:
        request_input["systemInstruction"] = {
            "parts": [{"text": system_text}], "role": "user",
        }
    return contents, system_text or None, request_input


def count_gemini_input(model: str, request_input: dict, api_key: str) -> int:
    """One no-retry REST count. Never log prompts, credentials, or response bodies."""
    if not _MODEL.fullmatch(model) or not api_key:
        raise ModelCostUnavailable("Gemini token preflight has no valid model or credential")
    if not _COUNT_SLOTS.acquire(blocking=False):
        raise ModelCostUnavailable("Gemini token preflight is busy; try again later")
    try:
        url = f"{_BASE}/{model}:countTokens"
        response = httpx.post(
            url,
            headers={"x-goog-api-key": api_key, "Content-Type": "application/json"},
            json={"generateContentRequest": {"model": f"models/{model}", **request_input}},
            timeout=httpx.Timeout(8.0, connect=2.0),
            follow_redirects=False,
        )
        if response.status_code != 200:
            raise ModelCostUnavailable("Gemini token preflight is unavailable")
        value = response.json().get("totalTokens")
        if type(value) is not int or not 0 < value <= 2_147_483_647:
            raise ModelCostUnavailable("Gemini token preflight returned no valid count")
        return value
    except (httpx.HTTPError, ValueError, TypeError, AttributeError):
        raise ModelCostUnavailable("Gemini token preflight is unavailable") from None
    finally:
        _COUNT_SLOTS.release()
