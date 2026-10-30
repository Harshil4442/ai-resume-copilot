"""Finite, scheduled-arrival measurements; no application/provider imports."""
from __future__ import annotations

import asyncio
import math
import time
from collections import Counter
from dataclasses import asdict, dataclass
from typing import Any

VERSION = "stage5-owned-http-v1"
_RETAINED_NATIVE_CONTEXTS: list = []
PATHS = (
    "/api/auth/bootstrap", "/api/resume/list", "/api/v1/opportunities?limit=50",
    "/api/v1/skill-roi", "/api/v1/credits/wallet",
)


@dataclass(frozen=True)
class Plan:
    requests_per_phase: int = 50
    arrivals_per_second: int = 10
    measurement_seconds: int = 600
    buyers_percent: int = 25
    users: int = 10_000
    ordinary_opportunities: int = 10
    heavy_opportunities: int = 500
    concurrency: tuple[int, ...] = (1, 10, 25)

    def validate(self) -> None:
        for value in (self.requests_per_phase, self.arrivals_per_second,
                      self.measurement_seconds, self.buyers_percent, self.users,
                      self.ordinary_opportunities, self.heavy_opportunities):
            if type(value) is not int:
                raise ValueError("Capacity plan integers must be exact")
        if (not 5 <= self.requests_per_phase <= 500 or
                not 1 <= self.arrivals_per_second <= 100 or
                not 1 <= self.measurement_seconds <= 600 or
                self.buyers_percent not in (20, 25) or self.users != 10_000 or
                self.ordinary_opportunities != 10 or self.heavy_opportunities != 500 or
                self.concurrency != (1, 10, 25) or
                self.requests_per_phase * 6 + 3 > 3000 or
                6 * self.requests_per_phase / self.arrivals_per_second >= self.measurement_seconds):
            raise ValueError("Capacity plan exceeds the fixed owned-local envelope")

    @classmethod
    def parse(cls, value: dict[str, Any]) -> Plan:
        if type(value) is not dict or set(value) != set(asdict(cls())):
            raise ValueError("Capacity plan fields differ")
        if value.get("concurrency") != [1, 10, 25]:
            raise ValueError("Capacity concurrency must be 1, 10, 25")
        result = cls(**{**value, "concurrency": (1, 10, 25)})
        result.validate()
        return result


def percentile(values: list[float], quantile: float) -> float | None:
    return sorted(values)[max(0, math.ceil(len(values) * quantile) - 1)] if values else None


async def scheduled_phase(*, count: int, concurrency: int, arrivals_per_second: int,
                          deadline: float, request, clock=time.perf_counter,
                          sleep=asyncio.sleep) -> list[dict]:
    """Latency begins at planned arrival, before semaphore admission (no omission)."""
    origin = clock()
    semaphore = asyncio.Semaphore(concurrency)

    async def one(index):
        scheduled = origin + index / arrivals_per_second
        await sleep(max(0, min(scheduled, deadline) - clock()))
        async with semaphore:
            sent = clock()
            result = {"index": index, "status": "deadline", "bytes": 0}
            if sent < deadline:
                try:
                    result.update(await asyncio.wait_for(request(index), deadline - sent))
                except TimeoutError:
                    result["status"] = "deadline"
                except Exception:
                    result["status"] = "transport_error"
            done = clock()
            result.update(queue_ms=max(0, sent - scheduled) * 1000,
                          http_ms=max(0, done - sent) * 1000,
                          arrival_to_completion_ms=max(0, done - scheduled) * 1000)
            return result

    return list(await asyncio.gather(*(one(i) for i in range(count))))


def summarize(rows: list[dict]) -> dict:
    successful = [r for r in rows if r["status"] == 200 and r.get("contract_valid") is True]
    return {
        "offered": len(rows), "successful_owned_responses": len(successful),
        "status_counts": dict(Counter(str(r["status"]) for r in rows)),
        "contract_failures": sum(r["status"] == 200 and not r.get("contract_valid") for r in rows),
        "validated_capacity_refusals": sum(r["status"] == 429 and r.get("capacity_refusal_valid") is True for r in rows),
        "response_body_bytes": sum(r["bytes"] for r in rows),
        "all_arrival_to_completion_p95_ms": percentile([r["arrival_to_completion_ms"] for r in rows], .95),
        "success_arrival_to_completion_p95_ms": percentile([r["arrival_to_completion_ms"] for r in successful], .95),
        "queue_p95_ms": percentile([r["queue_ms"] for r in rows], .95),
        "http_p95_ms": percentile([r["http_ms"] for r in rows], .95),
    }


def expected_capacity_refusal(mode: str, status: int, body: object, headers) -> bool:
    return (mode == "list_governed_40" and status == 429
        and body == {"detail": {"code": "NATIVE_METADATA_CAPACITY",
            "message": "Request capacity is temporarily allocated. Please try again later."}}
        and headers.get("retry-after") == "60"
        and headers.get("cache-control") == "private, no-store"
        and headers.get("pragma") == "no-cache")


def workload_validation(results: list[dict], *, requests_per_phase: int, preflight: dict) -> dict:
    failures: Counter[str] = Counter()
    expected = {(mode, concurrency) for mode in ("get_unpaced_diagnostic", "list_governed_40")
                for concurrency in (1, 10, 25)}
    actual = [(row.get("mode"), row.get("concurrency")) for row in results]
    if set(actual) != expected or len(actual) != len(expected):
        failures["missing_or_duplicate_phase"] += 1
    if preflight != {"missing_bearer_status": 401, "foreign_resume_status": 404,
                     "bootstrap_status": 200, "bootstrap_contract_valid": True}:
        failures["preflight_boundary_or_contract"] += 1
    for phase in results:
        if phase.get("state") == "not_started_deadline":
            failures["unstarted_phase"] += 1
        rows = phase.get("requests", [])
        if len(rows) != requests_per_phase or phase.get("offered") != requests_per_phase:
            failures["request_denominator"] += 1
        for row in rows:
            if row["status"] == 200:
                if row.get("contract_valid") is not True:
                    failures["owned_response_contract"] += 1
            elif (row["status"] != 429 or phase["mode"] != "list_governed_40"
                    or row.get("capacity_refusal_valid") is not True):
                failures["unexpected_status_or_error"] += 1
    return {"passed": not failures, "failures": dict(failures),
            "production_capacity_qualified": False}


def finish_owned_http(*, server, thread, guards, finalizers) -> None:
    """Always close owned dependencies; unrestricted egress needs terminal HTTP.

    A stuck daemon worker is a failed process-level experiment. Its sockets stay
    guarded until this CLI process exits; dependency cleanup is still attempted.
    Each finalizer receives whether instrumentation can safely be detached.
    """
    failures = []
    terminal = thread is None
    try:
        if server is not None:
            server.should_exit = True
        if thread is not None:
            thread.join(timeout=15)
            terminal = not thread.is_alive()
    except BaseException:
        failures.append("http_shutdown")
    for finalize in finalizers:
        try:
            finalize(terminal)
        except BaseException:
            failures.append("owned_finalizer")
    if terminal:
        try:
            guards.undo()
        except BaseException:
            failures.append("guard_restore")
    if not terminal or failures:
        raise RuntimeError("Owned HTTP or dependency finalization is incomplete")


def release_native_context(context, terminal: bool) -> None:
    """Do not unwind native fixtures while HTTP can still call their factories.

    A failed CLI keeps the stack alive until process termination. No background
    retry, deferred factory restore or new native request is scheduled here.
    """
    if terminal:
        context.close()
    else:
        _RETAINED_NATIVE_CONTEXTS.append(context)
