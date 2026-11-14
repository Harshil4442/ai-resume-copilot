"""Offline checks for the fixed multi-journey schedule and its evidence oracle."""
from __future__ import annotations

import asyncio
import json
import subprocess
import sys
from collections import Counter
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from scripts import stage5_first_minute as diagnostic
from scripts.stage5_capacity import Plan
from tests.fixtures.stage5_native import Counters


@pytest.mark.parametrize("buyers_percent, buyers", [(20, (5,)), (25, (4, 8))])
def test_first_minute_plan_uses_existing_visible_poll_schedules(buyers_percent, buyers):
    plan = diagnostic.schedule(buyers_percent)
    assert len(plan) == 33 + len(buyers) * 4
    assert {read.actor for read in plan if read.journey == "payment"} == set(buyers)
    for buyer in buyers:
        assert [read.second for read in plan if read.journey == "payment" and read.actor == buyer] == [0, 5, 15, 35]
    for actor in diagnostic.UPLOAD_ACTORS:
        assert [read.second for read in plan if read.journey == "upload" and read.actor == actor] == [5, 15, 35]


def test_plan_preview_is_dependency_free_and_mode_is_exclusive(tmp_path):
    script = Path(__file__).resolve().parents[1] / "scripts/run_stage5_capacity.py"
    base = [sys.executable, str(script), "--first-minute"]
    result = subprocess.run(base, capture_output=True, text=True, timeout=10, cwd=tmp_path, check=True)
    preview = json.loads(result.stdout)
    assert preview["version"] == diagnostic.VERSION and preview["external_calls"] == 0
    assert preview["maximum_requests_including_controls"] == 49
    assert preview["production_qualified"] is False
    assert subprocess.run(base + ["--fresh-window"], capture_output=True, timeout=10, cwd=tmp_path).returncode == 2


@pytest.mark.parametrize("case", ["valid", "wrong_body", "wrong_retry", "wrong_sql", "wrong_preflight", "wrong_cache",
                                  "contradictory_cache"])
def test_measure_keeps_each_outcome_and_checks_capacity_contract(monkeypatch, case):
    async def exercise():
        loop = asyncio.get_running_loop()
        starting = loop.time()
        # Short synthetic wall while respecting the fixed arrival/snapshot order.
        scale = 0.014
        def clock():
            return (loop.time() - starting) / scale

        async def sleep(seconds):
            await asyncio.sleep(seconds * scale)

        async def aged(**_kwargs):
            return 61.0

        counters = Counters()
        requests = Counter()
        redis_observations = 0

        def redis_stats(section):
            nonlocal redis_observations
            assert section == "stats"
            redis_observations += 1
            return {"total_commands_processed": redis_observations}

        synthetic_plan = Plan(buyers_percent=25)
        allowed = {(item.actor, diagnostic.route(item)) for item in diagnostic.schedule(25)}
        accepted = 0
        refused = 0

        def response(request):
            nonlocal accepted, refused
            actor = int(request.headers["authorization"].removeprefix("Bearer synthetic-"))
            assert request.method == "GET" and (actor, request.url.raw_path.decode()) in allowed
            requests[(actor, request.url.raw_path)] += 1
            # Refuse one early payment stream explicitly; later admission
            # reaches 40 observed List calls. The scheduler must not send its
            # remaining automatic ticks even under interleaved dashboard reads.
            early_refusal = actor == 4 and "/credits/orders/" in request.url.raw_path.decode()
            if accepted < 20 and not early_refusal:
                accepted += 1
                counters.add("synthetic_admin_list_databases", 2)
                if case == "wrong_sql":
                    counters.add("sql_UPDATE")
                cache = "public, private, no-store" if case == "contradictory_cache" else "private, no-store"
                return httpx.Response(200, json={"actor": actor if case != "wrong_body" else 99},
                    headers={"Cache-Control": cache} if case != "wrong_cache" else {})
            refused += 1
            return httpx.Response(429, json={"detail": {"code": "NATIVE_METADATA_CAPACITY",
                "message": "Request capacity is temporarily allocated. Please try again later."}},
                headers={"Retry-After": "60" if case != "wrong_retry" else "1",
                         "Cache-Control": "private, no-store", "Pragma": "no-cache"})

        monkeypatch.setattr(diagnostic, "wait_for_fresh_window", aged)
        monkeypatch.setattr(diagnostic, "owned_response", lambda item, body, _: body == {"actor": item.actor})
        storage = SimpleNamespace(storage=SimpleNamespace(info=redis_stats))
        async with httpx.AsyncClient(transport=httpx.MockTransport(response), base_url="https://127.0.0.1:59999") as client:
            report = await diagnostic.measure(client, synthetic_plan, {uid: f"synthetic-{uid}" for uid in range(1, 9)},
                                              storage, counters, clock=clock, sleep=sleep)
        return report, requests, accepted, refused

    report, requests, accepted, refused = asyncio.run(exercise())
    assert accepted == 20 and refused > 0
    assert report["planned_ticks"] == 41
    assert report["observed_requests"] == sum(requests.values()) == accepted + refused
    assert len(report["samples"]) == 61
    assert report["journeys"]["payment"]["suppressed_after_capacity"] >= 3 or case == "wrong_retry"
    controls = diagnostic.READ_CONTROLS if case != "wrong_preflight" else {**diagnostic.READ_CONTROLS, "foreign_order_status": 200}
    validation = diagnostic.validate(report, controls, 25)
    assert validation["passed"] is (case == "valid")
    assert validation["production_capacity_qualified"] is False


def test_owned_controls_require_strict_cache_without_inventing_foreign_contract():
    seen = []

    def response(request):
        actor = int(request.headers["authorization"].removeprefix("Bearer synthetic-"))
        seen.append(actor)
        if actor == 1:
            return httpx.Response(404)
        if actor == 2:
            return httpx.Response(200, json={"upload_id": diagnostic.upload_id(2), "state": "queued",
                "error_code": None, "resume": None}, headers={"cache-control": "public, private, no-store"})
        order = diagnostic.order_id(actor)
        return httpx.Response(200, json={"schema_version": "unified-credits-v1", "order_id": order,
            "status": "pending", "payment_status": "client_confirmed", "provider_order_id": None,
            "fulfilled": False, "checkout_available": False, "amount_minor": 7900, "refunded_amount_minor": 0,
            "currency": "INR", "accepted_quote": {"quote_id": f"{order}-quote", "credits": 40}},
            headers={"cache-control": "no-store, private"})

    with httpx.Client(transport=httpx.MockTransport(response), base_url="https://127.0.0.1:59999") as client:
        controls = diagnostic.control_reads(client, {uid: f"synthetic-{uid}" for uid in range(1, 9)}, 20)
    assert len(seen) == 5
    assert controls == {"foreign_workspace_status": 404, "foreign_order_status": 404, "foreign_upload_status": 404,
        "owned_pending_order_valid": True, "owned_queued_upload_valid": False}
