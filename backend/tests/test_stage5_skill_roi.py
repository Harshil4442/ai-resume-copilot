"""Contract tests for the bounded, separate same-source A/B workload."""
from __future__ import annotations

import asyncio
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from scripts import stage5_skill_roi as ab
from scripts.stage5_capacity import Plan
from scripts.stage5_seed import owned_opportunity_ids
from tests.fixtures.stage5_native import Counters

PREFLIGHT = {**ab.BASELINE_PREFLIGHT, "missing_workspace_bearer_status": 401, "foreign_workspace_status": 404}


def body(uid, *, consolidated):
    return {"opportunity": {"id": owned_opportunity_ids(uid, limit=1)[0], "resume_id": uid, "stage": "saved"},
        "resumes": {"selected_resume": {"id": uid}}, "match_source_state": "current",
        "skill_roi": ab.expected_roi(uid) if consolidated else None}


@pytest.mark.parametrize("change", [None, "status", "owner", "ranking", "projection", "transport"])
def test_both_arms_use_exact_owned_shapes_and_report_each_physical_outcome(monkeypatch, change):
    async def run():
        calls = []
        counters = Counters()
        results = []

        def handle(request):
            path = request.url.raw_path.decode()
            uid = int(request.headers["authorization"].removeprefix("Bearer synthetic-"))
            calls.append((uid, path))
            counters.add("native_account_validate")
            counters.add("synthetic_admin_list_databases", 2)
            if path == ab.STANDALONE:
                return httpx.Response(200, json=ab.expected_roi(uid))
            consolidated = path == ab.workspace_path(uid, consolidated=True)
            assert consolidated or path == ab.workspace_path(uid)
            value = body(uid, consolidated=consolidated)
            if consolidated and uid == 8:
                if change == "status":
                    return httpx.Response(429)
                if change == "transport":
                    raise httpx.ConnectError("synthetic")
                if change == "owner":
                    value["opportunity"]["resume_id"] = 7
                if change == "ranking":
                    value["skill_roi"]["items"][0]["score"] += 1
                if change == "projection":
                    value["another_field"] = "different"
            return httpx.Response(200, json=value)

        async def age_window(**_kwargs):
            return 61.0

        monkeypatch.setattr(ab, "wait_for_fresh_window", age_window)
        storage = SimpleNamespace(storage=SimpleNamespace(info=lambda _: {"total_commands_processed": 100}))
        tokens = {uid: f"synthetic-{uid}" for uid in ab.ACTORS}
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle),
                                    base_url="https://127.0.0.1:59999") as client:
            await ab.measure(client, Plan(), tokens, storage, counters, results)
        return calls, results

    calls, results = asyncio.run(run())
    assert len(calls) == 24
    assert all(calls.count((uid, path)) == 1 for uid in ab.ACTORS
               for path in (ab.workspace_path(uid), ab.workspace_path(uid, consolidated=True), ab.STANDALONE))
    assert [row["planned_http_requests"] for row in results] == [16, 8]
    assert [row["wire_and_sql"]["synthetic_admin_list_databases"] for row in results] == [32, 16]
    assert [row["journey_concurrency"] for row in results] == [4, 4]
    assert results[1]["completed_http_responses"] == (7 if change == "transport" else 8)
    validation = ab.validate(results, PREFLIGHT)
    assert validation["production_capacity_qualified"] is False
    assert validation["passed"] is (change is None)
    if change == "projection":
        assert validation["failures"] == {"workspace_projection_changed": 1}


def test_unstarted_arms_remain_in_denominator_without_network_observations(monkeypatch):
    async def no_window(**_kwargs):
        return None

    monkeypatch.setattr(ab, "wait_for_fresh_window", no_window)
    results = []
    asyncio.run(ab.measure(None, Plan(), None, None, None, results))
    assert results == [{"mode": mode, "state": "not_started_deadline", "planned_journeys": 8}
                       for mode in ab.EXPECTED_HTTP]
    assert ab.validate(results, PREFLIGHT)["failures"] == {"unstarted_or_unknown_phase": 2}


def test_plan_mode_keeps_original_report_and_separate_bounded_opt_in(tmp_path):
    script = Path(__file__).resolve().parents[1] / "scripts/run_stage5_capacity.py"

    def plan(*args):
        return subprocess.run([sys.executable, str(script), *args], capture_output=True, text=True,
                              timeout=10, env={"PATH": "/usr/bin:/bin"}, cwd=tmp_path)

    baseline = plan()
    selected = plan("--skill-roi-journey")
    assert baseline.returncode == selected.returncode == 0
    assert json.loads(baseline.stdout)["maximum_requests_including_controls"] == 303
    report = json.loads(selected.stdout)
    assert report["version"] == ab.VERSION
    assert report["maximum_requests_including_controls"] == 29
    assert report["journeys_per_arm"] == 8 and report["minimum_cooldown_seconds"] == 61
    assert report["external_calls"] == 0 and report["production_qualified"] is False
    assert plan("--fresh-window", "--skill-roi-journey").returncode != 0
