from __future__ import annotations

import contextvars
import json
import logging
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import sentry_sdk

from .catalog_timing import CatalogTimingEvent
from .domains.recovery.native_diagnostics import NativeFailureEvent

correlation_id_var: contextvars.ContextVar[str] = contextvars.ContextVar(
    "correlation_id",
    default="unassigned",
)

_generation_log = logging.getLogger("model_generation")
_generation_providers = frozenset({"google", "openai", "openai_compatible"})
_max_generation_latency_ms = 300_000


@dataclass(frozen=True)
class ProviderGenerationTiming:
    provider: str
    latency_ms: int
    failed: bool

    def payload(self) -> dict[str, str | int]:
        return {
            "event": "hirewiz_model_generation_latency_v1",
            "severity": "INFO",
            "provider": self.provider,
            "outcome": "failed" if self.failed else "succeeded",
            "latency_ms": self.latency_ms,
        }


def record_provider_generation(provider: object, latency_ms: int, *, failed: bool) -> None:
    if type(provider) is not str or provider not in _generation_providers:
        return
    if type(latency_ms) is not int or latency_ms < 0:
        return
    event = ProviderGenerationTiming(provider, min(latency_ms, _max_generation_latency_ms), failed)
    try:
        _generation_log.info("model generation latency", extra={"generation_timing": event})
    except Exception:
        # Telemetry must never change generation or ledger settlement.
        pass


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        native = getattr(record, "native_failure", None)
        if isinstance(native, NativeFailureEvent):
            return json.dumps(native.payload(), separators=(",", ":"), allow_nan=False)
        timing = getattr(record, "catalog_timing", None)
        if isinstance(timing, CatalogTimingEvent):
            return json.dumps(timing.payload(), separators=(",", ":"), allow_nan=False)
        generation = getattr(record, "generation_timing", None)
        if isinstance(generation, ProviderGenerationTiming):
            return json.dumps(generation.payload(), separators=(",", ":"), allow_nan=False)
        payload: dict[str, Any] = {
            "timestamp": datetime.now(UTC).isoformat(),
            "severity": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "correlation_id": correlation_id_var.get(),
        }
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, separators=(",", ":"), default=str)


def configure_observability() -> None:
    root = logging.getLogger()
    root.setLevel(os.getenv("LOG_LEVEL", "INFO").upper())
    if (os.getenv("LOG_FORMAT") or "json").lower() == "json":
        handler = logging.StreamHandler()
        handler.setFormatter(JsonFormatter())
        root.handlers.clear()
        root.addHandler(handler)

    dsn = (os.getenv("SENTRY_DSN") or "").strip()
    if dsn:
        sentry_sdk.init(
            dsn=dsn,
            environment=os.getenv("APP_ENV", "production"),
            release=os.getenv("APP_RELEASE"),
            traces_sample_rate=float(os.getenv("SENTRY_TRACES_SAMPLE_RATE", "0.1")),
            send_default_pii=False,
        )
