"""Catalog-only observations. Durations never authorize or change an operation."""
from __future__ import annotations

import asyncio
import contextvars
import logging
import math
import re
import threading
import time
import uuid
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass

from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

CATALOG_PATH = "/api/v1/employer-jobs/catalog"
PHASES = frozenset({
    "middleware_jwt", "dependency_jwt", "db_acquire", "auth_lookup",
    "catalog_owner_lookup", "catalog_sources", "catalog_render",
    "catalog_snapshot", "catalog_materialize",
})
HEADER_PHASES = PHASES | {"backend_headers"}
DURATION_FIELDS = {f"{phase}_ms" for phase in HEADER_PHASES} | {
    "backend_body_send_ms", "backend_cleanup_ms", "backend_total_ms",
}
MAX_DURATION_MS = 3_600_000
_ID = re.compile(r"^[0-9a-f]{32}$")
_current: contextvars.ContextVar[CatalogTiming | None] = contextvars.ContextVar(
    "catalog_timing", default=None,
)
_log = logging.getLogger("catalog_latency")


def finite_duration(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        numeric = float(value)
    except OverflowError:
        return None
    return numeric if math.isfinite(numeric) and 0 <= numeric <= MAX_DURATION_MS else None


@dataclass(frozen=True)
class CatalogTimingEvent:
    request_id: str
    outcome: str
    status_class: str
    error_phase: str | None
    durations: Mapping[str, object]

    def payload(self) -> dict[str, object]:
        # Rebuild the schema even for a malformed record; never merge caller extras.
        return {
            "event": "catalog_latency_v2",
            "request_id": self.request_id if _ID.fullmatch(self.request_id) else None,
            "outcome": self.outcome if self.outcome in {"complete", "incomplete", "error", "cancelled"} else "incomplete",
            "status_class": self.status_class if self.status_class in {"1xx", "2xx", "3xx", "4xx", "5xx"} else "not_started",
            "error_phase": self.error_phase if self.error_phase in PHASES else None,
            "acquisition_measurement": "composite",
            "connect_measurement": "unavailable",
            "ping_measurement": "unavailable",
            "db_connect_ms": None,
            "db_ping_ms": None,
            **{name: finite_duration(self.durations.get(name)) for name in sorted(DURATION_FIELDS)},
        }


class CatalogTiming:
    def __init__(self) -> None:
        self.request_id = uuid.uuid4().hex
        self._lock = threading.Lock()
        self._closed = False
        self._durations: dict[str, float] = {}
        self._error_phase: str | None = None

    def add(self, phase: str, elapsed_ms: float) -> None:
        name = f"{phase}_ms"
        value = finite_duration(elapsed_ms)
        if name not in DURATION_FIELDS or value is None:
            return
        with self._lock:
            if not self._closed:
                self._durations[name] = self._durations.get(name, 0.0) + value

    def failed(self, phase: str) -> None:
        with self._lock:
            if not self._closed and self._error_phase is None and phase in PHASES:
                self._error_phase = phase

    def headers(self) -> str:
        with self._lock:
            values = dict(self._durations)
        return ", ".join(
            f"{phase};dur={value:.1f}"
            for phase in sorted(HEADER_PHASES)
            if (value := finite_duration(values.get(f"{phase}_ms"))) is not None
        )

    def finish(self, outcome: str, status: int | None) -> CatalogTimingEvent:
        with self._lock:
            self._closed = True
            return CatalogTimingEvent(
                self.request_id, outcome, f"{status // 100}xx" if status else "not_started",
                self._error_phase, dict(self._durations),
            )


def current_catalog_timing() -> CatalogTiming | None:
    return _current.get()


@contextmanager
def catalog_span(phase: str) -> Iterator[None]:
    collector = current_catalog_timing()
    if collector is None or phase not in PHASES:
        yield
        return
    started = time.perf_counter_ns()
    try:
        yield
    except BaseException:
        collector.failed(phase)
        raise
    finally:
        collector.add(phase, (time.perf_counter_ns() - started) / 1_000_000)


class CatalogTimingMiddleware:
    """Observe the complete wrapped ASGI call without consuming its body."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope.get("method") != "GET" or scope.get("path") != CATALOG_PATH:
            await self.app(scope, receive, send)
            return
        collector = CatalogTiming()
        scope.setdefault("state", {})["catalog_timing"] = collector
        token = _current.set(collector)
        started = time.perf_counter_ns()
        headers_at: int | None = None
        body_at: int | None = None
        status: int | None = None
        outcome = "incomplete"

        async def timed_send(message: Message) -> None:
            nonlocal headers_at, body_at, status
            if message["type"] == "http.response.start":
                raw_status = message["status"]
                status = raw_status if type(raw_status) is int and 100 <= raw_status <= 599 else None
                headers_at = time.perf_counter_ns()
                collector.add("backend_headers", (headers_at - started) / 1_000_000)
                headers = MutableHeaders(scope=message)
                headers["X-Correlation-ID"] = collector.request_id
                headers["Server-Timing"] = collector.headers()
            await send(message)
            if message["type"] == "http.response.body" and not message.get("more_body", False):
                body_at = time.perf_counter_ns()
                if headers_at is not None:
                    collector.add("backend_body_send", (body_at - headers_at) / 1_000_000)

        try:
            await self.app(scope, receive, timed_send)
            outcome = "complete" if body_at is not None else "incomplete"
        except BaseException as error:
            # Type only, never exception text or request data.
            outcome = "cancelled" if isinstance(error, asyncio.CancelledError) else "error"
            raise
        finally:
            ended = time.perf_counter_ns()
            if body_at is not None:
                collector.add("backend_cleanup", (ended - body_at) / 1_000_000)
            collector.add("backend_total", (ended - started) / 1_000_000)
            event = collector.finish(outcome, status)
            _current.reset(token)
            try:
                _log.info("catalog_latency_v2", extra={"catalog_timing": event})
            except Exception:
                # Observability cannot fail an authenticated read.
                pass
