"""Schedule/contract preparation only: MockTransport, no native services or credentials."""
from __future__ import annotations

import asyncio
import copy
import json
import socket
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest

from scripts import stage5_billing_journey as billing
from scripts.stage5_capacity import Plan
from scripts.stage5_skill_roi import BASELINE_PREFLIGHT

HEADERS = {"Cache-Control": "private, no-store", "Pragma": "no-cache", "Retry-After": "60"}


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def deny(*_args, **_kwargs):
        pytest.fail("This preparation test cannot contact a native service or provider")

    monkeypatch.setattr(socket.socket, "connect", deny)
    monkeypatch.setattr(socket, "getaddrinfo", deny)


def order(uid, kind, status="paid"):
    stamp = datetime.now(UTC)
    shared = dict(order_id=billing.order_id(uid, kind), provider_order_id=None, checkout_available=False,
        status=status, fulfilled=True, amount_minor=5900, refunded_amount_minor=5900 if status == "refunded" else 0,
        currency="INR", created_at=stamp.isoformat(), paid_at=stamp.isoformat())
    if kind == "unified_recent":
        shared.update(schema_version="unified-credits-v1", payment_status="captured", sku="starter_pass",
            pass_expires_at=(stamp + timedelta(days=30)).isoformat(), accepted_quote=dict(
                quote_id="synthetic-quote", sku="starter_pass", name="Synthetic only", catalog_version="synthetic",
                terms_version="synthetic", amount_minor=5900, credits=40, created_at=stamp.isoformat(),
                expires_at=(stamp + timedelta(minutes=15)).isoformat(),
                pass_expires_at=(stamp + timedelta(days=30)).isoformat(), server_time=stamp.isoformat()))
    else:
        shared.update(payment_reference=None, sku="legacy_synthetic", entitlement_kind="job_service_credits",
            entitlement_quantity=1, refunded_at=None, accepted_quote=None)
    return shared


def body(key, uid, buyers=25):
    if key == "bootstrap" or key.startswith("refresh_"):
        return {"schema_version": "owned-bootstrap-v1", "profile": {"email": f"capacity-{uid}@example.com"},
            "credit_wallet": {"available_credits": 90 if uid % (5 if buyers == 20 else 4) == 0 else 0}, "unavailable": []}
    if key == "me":
        return {"id": uid, "email": f"capacity-{uid}@example.com", "tier": "free", "ai_credits": 3, "job_service_credits": 0}
    if key == "enrollment":
        return {"status": "NOT_AVAILABLE"}
    if key.endswith("_recent"):
        return None
    if key == "unified_history":
        return {"schema_version": "unified-credits-v1", "items": [], "next_cursor": None}
    if key == "service_history":
        return {"balance": 0, "reserved_credits": 0, "items": [], "operations": [], "reservations": [], "next_cursor": None}
    if key == "legacy_usage":
        return {"balance": 3, "items": []}
    raise AssertionError("Unknown synthetic route")


async def example(monkeypatch, *, settled=False, buyers=25, late_legacy=False, deadline_jump=False, unpaced=False):
    from tests.fixtures.stage5_native import Counters

    counters, phases, observed = Counters(), [], []
    now = 0.0
    admitted = 0
    first_refresh = asyncio.Event()
    bootstrap_reads = 0

    async def fresh(**_kwargs):
        nonlocal now
        now += 61
        return 61

    async def sleep(seconds):
        nonlocal now
        await asyncio.sleep(0)
        now += 100 if deadline_jump else seconds

    async def handle(request):
        nonlocal admitted, bootstrap_reads
        assert request.method == "GET" and not request.content and request.url.host == "127.0.0.1"
        uid = int(request.headers["authorization"].split("-")[-1])
        path = request.url.raw_path.decode()
        key = next(k for k, p in (billing.COLD | billing.HISTORIES).items() if p == path)
        observed.append((uid, key, now))
        if uid == 1 and key == "bootstrap":
            bootstrap_reads += 1
            if bootstrap_reads == 2:
                first_refresh.set()
        counters.add("native_account_validate")
        if admitted >= 40 and not unpaced:
            return httpx.Response(429, json={"detail": {"code": "NATIVE_METADATA_CAPACITY",
                "message": "Request capacity is temporarily allocated. Please try again later."}}, headers=HEADERS)
        admitted += 2
        counters.add("synthetic_admin_list_databases", 2)
        if key == "catalog":
            return httpx.Response(404, json={"detail": "Unified credits are not available yet."}, headers=HEADERS)
        if late_legacy and uid == 1 and key == "legacy_recent":
            await first_refresh.wait()
        value = order(uid, key) if settled and uid == 1 and key.endswith("_recent") else body(key, uid, buyers)
        await asyncio.sleep(0)
        if value is None:
            return httpx.Response(200, content=b"null", headers=HEADERS)
        return httpx.Response(200, json=value, headers=HEADERS)

    monkeypatch.setattr(billing, "wait_for_fresh_window", fresh)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle), base_url="https://127.0.0.1:59999") as client:
        await billing.measure(client, Plan(buyers_percent=buyers), {i: f"synthetic-{i}" for i in billing.ACTORS},
            counters, phases, clock=lambda: now, sleep=sleep)
    return phases, observed


@pytest.mark.parametrize("buyers", [20, 25])
def test_real_contracts_six_cold_routes_and_only_explicit_history_open(monkeypatch, buyers):
    phases, observed = asyncio.run(example(monkeypatch, buyers=buyers))
    assert phases[0]["billing_schedule_contract_source"] == billing.FRONTEND_SOURCE
    assert 8 * 6 + 4 * 3 <= len(observed) <= billing.MAX_REQUESTS - 3
    for uid in billing.ACTORS:
        keys = [key for actor, key, _ in observed if actor == uid]
        assert set(billing.COLD).issubset(keys)
        assert set(keys) & set(billing.HISTORIES) == (set(billing.HISTORIES) if uid in billing.HISTORY_ACTORS else set())
    result = billing.validate(phases, BASELINE_PREFLIGHT)
    assert result["passed"] is True and result["production_capacity_qualified"] is False, result
    assert result["governed_refusals"] > 0 and result["closed_catalog_responses"] > 0
    assert phases[0]["wire_and_sql"]["synthetic_admin_list_databases"] == 40
    assert phases[0]["actual_peak_inflight_http"] <= 8
    assert all(row["scheduled_at"] >= 15 for row in phases[0]["requests"] if row["view"] in billing.HISTORIES)
    assert any("missing_advisory_snapshot" in s["bootstrap_reasons"].values() for s in phases[0]["actors"].values())


def test_settled_opening_refreshes_wait_for_pending_snapshot_without_replaying_orders(monkeypatch):
    phases, observed = asyncio.run(example(monkeypatch, settled=True))
    phase = phases[0]
    state = phase["actors"]["1"]
    assert set(state["refresh_bindings"]) == {"unified_recent", "legacy_recent"}
    fresh = [r for r in phase["requests"] if r["actor"] == 1 and r["view"].startswith("refresh_")]
    assert 1 <= len(fresh) <= 2
    rows = {r["view"]: r for r in phase["requests"] if r["actor"] == 1}
    assert all(r["scheduled_at"] >= rows["bootstrap"]["completed_at"] for r in fresh)
    assert len([x for x in observed if x[:2] == (1, "unified_recent")]) == 1
    assert len([x for x in observed if x[:2] == (1, "legacy_recent")]) == 1
    result = billing.validate(phases, BASELINE_PREFLIGHT)
    assert result["passed"] is True, result


def test_later_legacy_settlement_cannot_reuse_the_earlier_inflight_snapshot(monkeypatch):
    phases, _ = asyncio.run(example(monkeypatch, settled=True, late_legacy=True))
    rows = {r["view"]: r for r in phases[0]["requests"] if r["actor"] == 1}
    assert "refresh_2" in rows
    assert rows["refresh_2"]["scheduled_at"] >= rows["legacy_recent"]["completed_at"]
    assert phases[0]["actors"]["1"]["refresh_bindings"]["legacy_recent"] == "refresh_2"
    assert billing.validate(phases, BASELINE_PREFLIGHT)["passed"] is True


def test_deadline_preserves_unsent_reads_and_never_dispatches_them(monkeypatch):
    phases, _ = asyncio.run(example(monkeypatch, deadline_jump=True))
    rows = phases[0]["requests"]
    assert any(not r["sent"] and r["status"] == "deadline" for r in rows)
    assert all(r["sent_at"] < billing.WINDOW_SECONDS for r in rows if r["sent"])
    assert billing.validate(phases, BASELINE_PREFLIGHT)["passed"] is False


@pytest.mark.parametrize("problem", ["early_history", "early_refresh", "foreign_owner", "bad_429", "get_fallback",
    "sql_write", "unknown_outcome", "missing_read", "unexpected_actor", "wrong_controls", "closed_404"])
def test_changed_schedule_security_and_unknown_outcomes_fail(monkeypatch, problem):
    phases, _ = asyncio.run(example(monkeypatch, settled=True))
    phases = copy.deepcopy(phases)
    phase, preflight = phases[0], dict(BASELINE_PREFLIGHT)
    if problem == "early_history":
        next(r for r in phase["requests"] if r["view"] in billing.HISTORIES)["scheduled_at"] = -1
    elif problem == "early_refresh":
        next(r for r in phase["requests"] if r["view"].startswith("refresh_"))["scheduled_at"] = -1
    elif problem == "foreign_owner":
        next(r for r in phase["requests"] if r["status"] == 200)["contract_valid"] = False
    elif problem == "bad_429":
        next(r for r in phase["requests"] if r["status"] == 429)["refusal_valid"] = False
    elif problem in {"get_fallback", "sql_write"}:
        phase["wire_and_sql"]["synthetic_admin_get_database" if problem == "get_fallback" else "sql_UPDATE"] = 1
    elif problem == "unknown_outcome":
        phase["requests"][0]["status"] = "deadline"
    elif problem == "missing_read":
        phase["requests"].pop()
    elif problem == "unexpected_actor":
        phase["requests"][0]["actor"] = 999
    elif problem == "wrong_controls":
        preflight["foreign_resume_status"] = 200
    else:
        next(r for r in phase["requests"] if r["status"] == 404)["closed_catalog_valid"] = False
    result = billing.validate(phases, preflight)
    assert result["passed"] is False and result["production_capacity_qualified"] is False


@pytest.mark.parametrize("key", ["bootstrap", "me", "unified_recent", "legacy_recent"])
def test_billing_oracles_refuse_another_owner(key):
    value = order(2, key) if key.endswith("_recent") else body(key, 2)
    with pytest.raises(AssertionError):
        billing.checked_body(key, value, 1, 25)


def test_plan_mode_is_dependency_free_bounded_and_mutually_exclusive(tmp_path):
    script = Path(__file__).resolve().parents[1] / "scripts/run_stage5_capacity.py"
    result = subprocess.run([sys.executable, "-I", "-B", str(script), "--billing-journey"],
        cwd=tmp_path, env={"PATH": "/usr/bin:/bin"}, capture_output=True, timeout=10, check=False)
    assert result.returncode == 0
    plan = json.loads(result.stdout)
    assert plan["maximum_requests_including_controls"] == 79 and plan["base_distinct_cold_routes"] == 6
    assert plan["shared_admin_limit"] == 40 and plan["frontend_schedule_source"] == billing.FRONTEND_SOURCE
    assert plan["external_calls"] == 0 and plan["production_qualified"] is False
    conflict = subprocess.run([sys.executable, "-I", "-B", str(script), "--billing-journey", "--jobs-journey"],
        cwd=tmp_path, env={"PATH": "/usr/bin:/bin"}, capture_output=True, timeout=10, check=False)
    assert conflict.returncode != 0


def candidate_measurement(monkeypatch):
    """Synthetic validator input only; these are not observed cohort counters."""
    phases, _ = asyncio.run(example(monkeypatch, unpaced=True))
    count = len(phases[0]["requests"])
    wire = phases[0]["wire_and_sql"]
    wire["synthetic_admin_list_databases"] = 16
    wire.update({"candidate_" + name: value for name, value in {
        "validation_started": count, "validation_entered": count, "validation_succeeded": count,
        "validation_failed": 0, "barriers_started": 2 * count, "barriers_succeeded": 2 * count,
        "barriers_failed": 0, "cohorts_started": 16, "cohorts_completed": 16, "pending": 0, "active": 0}.items()})
    return phases


def test_candidate_distinguishes_logical_fresh_barriers_from_unchanged_baseline_physical_oracle(monkeypatch):
    phases = candidate_measurement(monkeypatch)
    baseline = billing.validate(phases, BASELINE_PREFLIGHT)
    assert baseline["passed"] is False and baseline["failures"] == {"native_auth_budget_or_effects": 1}
    result = billing.validate_pre_send_candidate(phases, BASELINE_PREFLIGHT)
    assert result["passed"] is True and result["baseline_validation"] == baseline
    assert result["successful_owned_responses"] + result["closed_catalog_responses"] > 20
    assert result["physical_admin_list_rpcs"] == 16
    assert result["production_capacity_qualified"] is result["production_transport_sharing_proven"] is False
    assert billing.validate(phases, BASELINE_PREFLIGHT) == baseline  # No count rewriting/mutation.


@pytest.mark.parametrize("change", ["entry", "exit", "unfinished", "barrier", "cohort", "physical", "missing", "write", "ownership"])
def test_candidate_logical_accounting_and_original_effect_guards_fail_closed(monkeypatch, change):
    phases = candidate_measurement(monkeypatch)
    wire = phases[0]["wire_and_sql"]
    preflight = dict(BASELINE_PREFLIGHT)
    if change == "entry":
        wire["candidate_validation_entered"] -= 1
    elif change == "exit":
        wire["candidate_validation_succeeded"] -= 1
    elif change == "unfinished":
        wire["candidate_pending"] = 1
    elif change == "barrier":
        wire["candidate_barriers_succeeded"] -= 1
    elif change == "cohort":
        wire["candidate_cohorts_completed"] -= 1
    elif change == "physical":
        wire["synthetic_admin_list_databases"] = 41
    elif change == "missing":
        del wire["candidate_validation_entered"]
    elif change == "write":
        wire["sql_UPDATE"] = 1
    else:
        preflight["foreign_resume_status"] = 200
    assert billing.validate_pre_send_candidate(phases, preflight)["passed"] is False


@pytest.mark.parametrize("wrong", [None, "candidate_fresh_factory_objects", "candidate_private_data_clients", "candidate_private_storage_clients"])
def test_fresh_factory_mode_refuses_shared_private_clients_or_missing_construction(monkeypatch, wrong):
    phases = candidate_measurement(monkeypatch)
    wire = phases[0]["wire_and_sql"]
    keys = ("candidate_fresh_factory_objects", "candidate_private_data_clients", "candidate_private_storage_clients")
    count = sum(row["sent"] for row in phases[0]["requests"])
    wire.update({key: count for key in keys})
    if wrong is not None:
        wire[wrong] = 1
    result = billing.validate_pre_send_candidate(phases, BASELINE_PREFLIGHT, fresh_factories=True)
    assert result["passed"] is (wrong is None)
    assert result["production_capacity_qualified"] is result["production_transport_sharing_proven"] is False
    assert result["validation_mode"] == "pre_send_identity_fresh_factory_local_v2"
    # The original constructor-only diagnostic cannot be mislabeled as fresh.
    for key in keys:
        wire.pop(key)
    assert billing.validate_pre_send_candidate(phases, BASELINE_PREFLIGHT, fresh_factories=True)["passed"] is False
    assert billing.validate_pre_send_candidate(phases, BASELINE_PREFLIGHT)["passed"] is True
