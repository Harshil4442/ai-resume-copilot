"""Bounded direct-HTTP model of the 9dc Billing schedule contract.

Live frontend 8a72627 inherits these Billing components byte-for-byte; 9dc is
the schedule contract reference, not the latest deployed frontend source.
This coalesces advisory frontend reads only. Every actual HTTP request still
traverses the shipping authentication and fresh native entry/exit checks.
"""
from __future__ import annotations

import asyncio
import hashlib
import time
from collections import Counter
from pathlib import Path
from typing import Any

from scripts.stage5_capacity import (
    BOOTSTRAP_PATH,
    FRESH_WINDOW_COOLDOWN_SECONDS,
    expected_capacity_refusal,
    private_no_store,
    wait_for_fresh_window,
)
from scripts.stage5_skill_roi import BASELINE_PREFLIGHT

VERSION = "stage5-owned-billing-9dc-v1"
FRONTEND_SOURCE = "9dc5627a180f257c3bdc62a9789e5444b8575324"
ACTORS = tuple(range(1, 9))
HISTORY_ACTORS = (1, 4, 5, 8)
CONCURRENCY = 8
ADMIN_LIMIT = 40
HISTORY_OPEN_AT = 15
WINDOW_SECONDS = 50
MINIMUM_SECONDS = FRESH_WINDOW_COOLDOWN_SECONDS + WINDOW_SECONDS
COLD = {
    "bootstrap": BOOTSTRAP_PATH,
    "enrollment": "/api/v1/credits/canary-enrollment",
    "catalog": "/api/v1/credits/catalog",
    "unified_recent": "/api/v1/credits/recent-order",
    "legacy_recent": "/api/billing/recent-order",
    "me": "/api/auth/me",
}
HISTORIES = {
    "unified_history": "/api/v1/credits/history?limit=20",
    "service_history": "/api/v1/employer-jobs/credits?limit=10",
    "legacy_usage": "/api/v1/usage-events?limit=20",
}
MAX_REQUESTS = len(ACTORS) * (len(COLD) + 2) + len(HISTORY_ACTORS) * len(HISTORIES) + 3


def order_id(uid, kind):
    return f"stage5-billing-{kind}-{uid}"


def checked_body(key, body, uid, buyers_percent):
    """Oracle for the existing owned seed; never manufacture payment records."""
    from scripts.stage5_seed import synthetic_email

    if key == "bootstrap" or key.startswith("refresh_"):
        from tests.fixtures.stage5_native import check_body

        assert check_body(BOOTSTRAP_PATH, body, user_id=uid,
            buyer=uid % (5 if buyers_percent == 20 else 4) == 0, heavy=uid == 8)
    elif key == "me":
        from app.schemas import UserMeResponse

        value = UserMeResponse.model_validate(body)
        assert value.id == uid and value.email == synthetic_email(uid) and value.job_service_credits == 0
    elif key == "enrollment":
        assert body == {"status": "NOT_AVAILABLE"}
    elif key.endswith("_recent"):
        if body is None:
            return False
        if key == "unified_recent":
            from app.domains.credits.schemas import CreditOrderStatusView

            value = CreditOrderStatusView.model_validate(body)
            assert value.sku in {"starter_pass", "growth_pass", "scale_pass"}
        else:
            from app.billing.purchase_quote import BillingOrderStatusResponse

            value = BillingOrderStatusResponse.model_validate(body)
            assert value.sku not in {"starter_pass", "growth_pass", "scale_pass"}
        assert value.order_id == order_id(uid, key)
        return (value.status == "paid" and value.fulfilled) or value.status == "refunded" or (
            key == "legacy_recent" and value.currency == "INR" and value.status == "paid"
            and 0 < value.refunded_amount_minor < value.amount_minor)
    elif key == "unified_history":
        from app.domains.credits.schemas import CreditHistoryView

        value = CreditHistoryView.model_validate(body)
        assert value.items == [] and value.next_cursor is None
    elif key == "service_history":
        from app.domains.employer.credit_history import ServiceCreditHistory

        value = ServiceCreditHistory.model_validate(body)
        assert value.balance == value.reserved_credits == 0
        assert value.items == value.operations == value.reservations == [] and value.next_cursor is None
    elif key == "legacy_usage":
        from app.domains.career.schemas import UsageHistoryResponse

        value = UsageHistoryResponse.model_validate(body)
        assert value.items == []
    else:
        raise ValueError("Unknown Billing read")
    return False


async def measure(client, plan, tokens, counters, results, *, clock=time.perf_counter, sleep=asyncio.sleep):
    started = clock()
    deadline = started + plan.measurement_seconds
    phase: dict[str, Any] = {"mode": VERSION, "state": "not_started_deadline", "requests": [], "actors": {},
             "wire_and_sql": {}, "billing_schedule_contract_source": FRONTEND_SOURCE,
             "schedule_source_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
    results.append(phase)
    cooldown = await wait_for_fresh_window(deadline=deadline, clock=clock, sleep=sleep)
    if cooldown is None:
        return clock() - started
    origin = clock()
    expires = min(deadline, origin + WINDOW_SECONDS)
    before = Counter(counters.snapshot())
    semaphore = asyncio.Semaphore(CONCURRENCY)
    opening = asyncio.get_running_loop().create_future()
    active = peak = 0

    async def read(uid, key):
        nonlocal active, peak
        row = {"actor": uid, "view": key, "index": len(phase["requests"]), "status": "deadline",
               "sent": False, "bytes": 0, "scheduled_at": clock() - origin}
        phase["requests"].append(row)
        async with semaphore:
            row["sent_at"] = clock() - origin
            if clock() < expires:
                path = BOOTSTRAP_PATH if key.startswith("refresh_") else (COLD | HISTORIES)[key]
                row["sent"] = True
                active += 1
                peak = max(peak, active)
                try:
                    response = await asyncio.wait_for(client.get(path,
                        headers={"Authorization": "Bearer " + tokens[uid]}), expires - clock())
                    row.update(status=response.status_code, bytes=len(response.content))
                    try:
                        body = response.json()
                        if response.status_code == 429:
                            row["refusal_valid"] = expected_capacity_refusal(
                                "list_governed_40", response.status_code, body, response.headers)
                        elif response.status_code == 404 and key == "catalog":
                            row["closed_catalog_valid"] = (body == {"detail": "Unified credits are not available yet."}
                                                            and private_no_store(response.headers))
                        elif response.status_code == 200:
                            assert private_no_store(response.headers)
                            row["opening_refresh"] = checked_body(key, body, uid, plan.buyers_percent)
                            row["contract_valid"] = True
                    except (AssertionError, AttributeError, KeyError, TypeError, ValueError):
                        row["contract_valid"] = False
                except TimeoutError:
                    pass
                except Exception:
                    row["status"] = "transport_error"
                finally:
                    active -= 1
        row["completed_at"] = clock() - origin
        return row

    async def actor(uid):
        state: dict[str, Any] = {"history_open": uid in HISTORY_ACTORS, "refresh_bindings": {}, "bootstrap_reasons": {}}
        phase["actors"][str(uid)] = state
        pending_key = "bootstrap"
        pending = asyncio.create_task(read(uid, pending_key))
        initial = pending
        cached = None
        refresh_count = 0

        async def advisory_bootstrap(branch, force):
            nonlocal pending, pending_key, refresh_count, cached
            # Match fetchOwnedBootstrapAfterPending: wait for the snapshot
            # already in flight; concurrent callbacks may join the same NEW
            # fetch, but a later settled lookup cannot reuse an older snapshot.
            if force:
                existing = pending
                await existing
            else:
                if cached is not None:
                    return
                if initial.done() and initial.result().get("contract_valid") is True:
                    cached = initial.result()
                    return
            if pending.done():
                if not force and pending.result().get("contract_valid") is True:
                    cached = pending.result()
                    return
                refresh_count += 1
                pending_key = f"refresh_{refresh_count}"
                pending = asyncio.create_task(read(uid, pending_key))
            if pending_key != "bootstrap":
                state["refresh_bindings"][branch] = pending_key
                state["bootstrap_reasons"][branch] = "settled_order" if force else "missing_advisory_snapshot"
            value = await pending
            if value.get("contract_valid") is True:
                cached = value

        async def recent(kind):
            result = await read(uid, kind)
            if kind == "legacy_recent":
                balance = asyncio.create_task(read(uid, "me"))
            await advisory_bootstrap(kind, result.get("opening_refresh") is True)
            if kind == "legacy_recent":
                await balance
            state[kind + "_ready_at"] = clock() - origin

        unified = asyncio.create_task(recent("unified_recent"))
        legacy = asyncio.create_task(recent("legacy_recent"))
        enrollment = asyncio.create_task(read(uid, "enrollment"))
        catalog = asyncio.create_task(read(uid, "catalog"))
        if state["history_open"]:
            state["history_intent_at"] = await opening
            await unified
            history = asyncio.create_task(read(uid, "unified_history"))
            await legacy
            await asyncio.gather(history, read(uid, "service_history"), read(uid, "legacy_usage"))
        await asyncio.gather(unified, legacy, enrollment, catalog, pending)

    workers = [asyncio.create_task(actor(uid)) for uid in ACTORS]
    await asyncio.sleep(0)
    await sleep(max(0, min(expires, origin + HISTORY_OPEN_AT) - clock()))
    opening.set_result(clock() - origin)
    await asyncio.gather(*workers)
    after = counters.snapshot()
    phase.update(state="completed", fresh_seconds=cooldown, duration_seconds=clock() - origin,
                 actual_peak_inflight_http=peak,
                 wire_and_sql={name: value - before[name] for name, value in after.items()})
    return clock() - started


def validate(results, preflight):
    failures: Counter[str] = Counter()
    if any(preflight.get(key) != value for key, value in BASELINE_PREFLIGHT.items()):
        failures["owner_controls"] += 1
    if len(results) != 1 or results[0].get("mode") != VERSION or results[0].get("state") != "completed":
        failures["incomplete_phase"] += 1
        return {"passed": False, "failures": dict(failures),
                "production_capacity_qualified": False}
    phase = results[0]
    rows = phase["requests"]
    if ([row.get("index") for row in rows] != list(range(len(rows)))
            or any(type(row.get("actor")) is not int or row["actor"] not in ACTORS for row in rows)):
        failures["request_identity"] += 1
    if not (phase["fresh_seconds"] >= FRESH_WINDOW_COOLDOWN_SECONDS
            and 0 <= phase["actual_peak_inflight_http"] <= CONCURRENCY
            and 0 <= phase["duration_seconds"] <= WINDOW_SECONDS and len(rows) + 3 <= MAX_REQUESTS):
        failures["window_or_request_bound"] += 1
    for uid in ACTORS:
        owned = [row for row in rows if row["actor"] == uid]
        by_key = {row["view"]: row for row in owned}
        state = phase["actors"].get(str(uid), {})
        expected = set(COLD) | (set(HISTORIES) if uid in HISTORY_ACTORS else set())
        refreshes = {key for key in by_key if key.startswith("refresh_")}
        if (len(by_key) != len(owned) or set(by_key) != expected | refreshes
                or refreshes not in (set(), {"refresh_1"}, {"refresh_1", "refresh_2"})):
            failures["journey_denominator"] += 1
            continue
        if by_key["me"]["scheduled_at"] < by_key["legacy_recent"]["completed_at"]:
            failures["balance_before_recent"] += 1
        bindings = state.get("refresh_bindings", {})
        reasons = state.get("bootstrap_reasons", {})
        if set(bindings) - {"unified_recent", "legacy_recent"} or set(reasons) != set(bindings):
            failures["unrequested_refresh"] += 1
        for key in ("unified_recent", "legacy_recent"):
            target = bindings.get(key)
            if by_key[key].get("opening_refresh") is True:
                if (reasons.get(key) != "settled_order" or target not in refreshes or by_key[target]["scheduled_at"] < max(
                        by_key["bootstrap"]["completed_at"], by_key[key]["completed_at"])):
                    failures["fresh_bootstrap_order"] += 1
            elif target is not None and (reasons.get(key) != "missing_advisory_snapshot" or target not in refreshes
                    or by_key["bootstrap"].get("contract_valid") is True
                    or by_key[target]["scheduled_at"] < by_key["bootstrap"]["completed_at"]):
                failures["unrequested_refresh"] += 1
            required = max(by_key[key]["completed_at"], by_key["bootstrap"]["completed_at"],
                by_key[target]["completed_at"] if target in refreshes else 0,
                by_key["me"]["completed_at"] if key == "legacy_recent" else 0)
            if state.get(key + "_ready_at", -1) < required:
                failures["recent_branch_readiness"] += 1
        if refreshes != set(bindings.values()):
            failures["unrequested_refresh"] += 1
        for key in HISTORIES if uid in HISTORY_ACTORS else ():
            ready = "unified_recent_ready_at" if key == "unified_history" else "legacy_recent_ready_at"
            if (state.get("history_open") is not True or state.get("history_intent_at", -1) < HISTORY_OPEN_AT
                    or by_key[key]["scheduled_at"] < max(state.get(ready, float("inf")), state["history_intent_at"])):
                failures["history_before_open_or_recent"] += 1
    valid = 0
    for row in rows:
        good = ((row["status"] == 200 and row.get("contract_valid") is True)
                or (row["view"] == "catalog" and row["status"] == 404 and row.get("closed_catalog_valid") is True))
        valid += good
        if not good and not (row["status"] == 429 and row.get("refusal_valid") is True):
            failures["response_contract_or_unknown_outcome"] += 1
    wire = phase["wire_and_sql"]
    if not valid:
        failures["no_owned_success_observed"] += 1
    if (wire.get("native_account_validate") != sum(row["sent"] for row in rows)
            or not 2 * valid <= wire.get("synthetic_admin_list_databases", -1) <= ADMIN_LIMIT
            or any(wire.get(key, 0) for key in ("synthetic_admin_get_database", "sql_INSERT", "sql_UPDATE",
                                               "sql_DELETE", "data_commit", "blocked_external_attempts"))):
        failures["native_auth_budget_or_effects"] += 1
    return {"passed": not failures, "failures": dict(failures), "production_capacity_qualified": False,
            "successful_owned_responses": sum(row["status"] == 200 and row.get("contract_valid") is True for row in rows),
            "closed_catalog_responses": sum(row.get("closed_catalog_valid") is True for row in rows),
            "governed_refusals": sum(row["status"] == 429 and row.get("refusal_valid") is True for row in rows)}


def validate_pre_send_candidate(results, preflight):
    """Separate local accounting oracle; retain the original validation result.

    No physical count is rewritten to satisfy the unbatched two-RPC predicate.
    The original schedule/ownership/response gates remain mandatory. Aggregate
    counters describe this synthetic experiment, never issue native authority.
    """
    baseline = validate(results, preflight)
    failures = dict(baseline["failures"])
    failures.pop("native_auth_budget_or_effects", None)
    phase = results[0] if len(results) == 1 else {}
    rows, wire = phase.get("requests", []), phase.get("wire_and_sql", {})
    names = ("validation_started", "validation_entered", "validation_succeeded", "validation_failed",
             "barriers_started", "barriers_succeeded", "barriers_failed", "cohorts_started", "cohorts_completed",
             "pending", "active")
    counts = {name: wire.get("candidate_" + name) for name in names}
    physical = wire.get("synthetic_admin_list_databases")
    valid = sum((row["status"] == 200 and row.get("contract_valid") is True)
                or (row["view"] == "catalog" and row["status"] == 404 and row.get("closed_catalog_valid") is True)
                for row in rows)
    if (any(type(value) is not int or value < 0 for value in counts.values())
            or type(physical) is not int or not 1 <= physical <= ADMIN_LIMIT):
        failures["candidate_counter_shape_or_physical_budget"] = 1
    else:
        if (counts["validation_started"] != sum(row["sent"] for row in rows)
                or wire.get("native_account_validate") != counts["validation_started"]
                or counts["validation_started"] != counts["validation_succeeded"] + counts["validation_failed"]
                or counts["validation_succeeded"] != valid
                or not counts["validation_succeeded"] <= counts["validation_entered"] <= counts["validation_started"]
                or counts["barriers_started"] != counts["barriers_succeeded"] + counts["barriers_failed"]
                or counts["barriers_succeeded"] < counts["validation_entered"] + counts["validation_succeeded"]
                or counts["cohorts_started"] != counts["cohorts_completed"]
                or not physical <= counts["cohorts_completed"] <= counts["barriers_started"]
                or counts["barriers_succeeded"] > 8 * physical
                or counts["pending"] != 0 or counts["active"] != 0
                or any(wire.get(key, 0) for key in ("synthetic_admin_get_database", "sql_INSERT", "sql_UPDATE",
                                                   "sql_DELETE", "data_commit", "blocked_external_attempts"))):
            failures["candidate_logical_auth_accounting_or_effects"] = 1
    return {"passed": not failures, "failures": failures,
            "validation_mode": "pre_send_identity_batching_local_experiment_v1",
            "production_capacity_qualified": False, "production_transport_sharing_proven": False,
            "baseline_validation": baseline, "logical_observations": counts,
            "physical_admin_list_rpcs": physical,
            "successful_owned_responses": baseline.get("successful_owned_responses", 0),
            "closed_catalog_responses": baseline.get("closed_catalog_responses", 0),
            "governed_refusals": baseline.get("governed_refusals", 0)}
