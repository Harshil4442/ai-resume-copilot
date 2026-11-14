"""Compare the legacy and consolidated Skill ROI reads on the owned dataset."""
from __future__ import annotations

import asyncio
import hashlib
import json
import math
import time
from collections import Counter

from scripts.stage5_capacity import (
    FRESH_WINDOW_COOLDOWN_SECONDS,
    percentile,
    scheduled_phase,
    wait_for_fresh_window,
)

VERSION = "stage5-owned-skill-roi-journey-v1"
ACTORS = tuple(range(1, 9))
CONCURRENCY = 4
EXPECTED_HTTP = {"legacy_pair": 2, "consolidated": 1}
MAX_REQUESTS = len(ACTORS) * sum(EXPECTED_HTTP.values()) + 5
STANDALONE = "/api/v1/skill-roi"
BASELINE_PREFLIGHT = {"missing_bearer_status": 401, "foreign_resume_status": 404,
                      "bootstrap_status": 200, "bootstrap_contract_valid": True}


def workspace_path(uid: int, *, consolidated: bool = False) -> str:
    from scripts.stage5_seed import owned_opportunity_ids

    role = owned_opportunity_ids(uid, limit=1)[0]
    path = f"/api/v1/opportunities/{role}/workspace?tab=learning&limit=20"
    return path + "&include_skill_roi=true" if consolidated else path


def expected_roi(uid: int) -> dict:
    total = 500 if uid == 8 else 10
    return {"opportunity_count": total, "items": [
        {"skill": skill, "opportunity_count": total, "demand_ratio": 1.0,
         "evidence_strength": float(approved), "outcome_signal": 0.0,
         "estimated_hours": 8 if approved else 30, "score": 80.0 if approved else 92.67,
         "reason": f"Appears in {total} of {total} active opportunities; "
                   + ("approved evidence already exists." if approved else "no approved evidence exists yet.")}
        for skill, approved in (("distributed systems", False), ("rest", False),
                                ("postgresql", True), ("python", True))
    ]}


def checked_workspace(body: object, uid: int) -> str | None:
    """Validate ownership and compare the exact remaining projection across arms."""
    if type(body) is not dict or "skill_roi" not in body:
        return None
    from scripts.stage5_seed import owned_opportunity_ids

    role = owned_opportunity_ids(uid, limit=1)[0]
    opportunity, resumes = body.get("opportunity"), body.get("resumes")
    if (type(opportunity) is not dict or opportunity.get("id") != role
            or opportunity.get("resume_id") != uid or opportunity.get("stage") != "saved"
            or "job_description" in opportunity or "job_snapshot" in opportunity
            or type(resumes) is not dict or type(resumes.get("selected_resume")) is not dict
            or resumes["selected_resume"].get("id") != uid or body.get("match_source_state") != "current"):
        return None
    projection = {key: value for key, value in body.items() if key != "skill_roi"}
    return hashlib.sha256(json.dumps(projection, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


async def journey(client, token: str, uid: int, *, mode: str) -> dict:
    if mode not in EXPECTED_HTTP:
        raise ValueError("Unknown Skill ROI journey")
    headers = {"Authorization": "Bearer " + token}
    combined = mode == "consolidated"
    paths = (workspace_path(uid, consolidated=True),) if combined else (workspace_path(uid), STANDALONE)
    # Retain both physical outcomes and wait for both before the next counter
    # snapshot. A transport failure never retries the other request.
    responses = await asyncio.gather(*(client.get(path, headers=headers) for path in paths), return_exceptions=True)
    statuses = [response.status_code if not isinstance(response, BaseException) else "transport_error"
                for response in responses]
    fingerprint = None
    valid = False
    if all(status == 200 for status in statuses):
        try:
            workspace = responses[0].json()
            fingerprint = checked_workspace(workspace, uid)
            roi = workspace["skill_roi"] if combined else responses[1].json()
            valid = (fingerprint is not None and roi == expected_roi(uid)
                     and (combined or workspace["skill_roi"] is None))
        except (AttributeError, KeyError, TypeError, ValueError):
            valid = False
    return {"actor": uid, "status": 200 if all(status == 200 for status in statuses) else
            next(status for status in statuses if status != 200), "http_statuses": statuses,
            "contract_valid": valid, "workspace_sha256": fingerprint,
            "bytes": sum(len(response.content) for response in responses if not isinstance(response, BaseException))}


async def measure(client, plan, tokens, storage, counters, results: list[dict]) -> float:
    start = time.perf_counter()
    deadline = start + plan.measurement_seconds
    for mode, requests_per_journey in EXPECTED_HTTP.items():
        cooldown = await wait_for_fresh_window(deadline=deadline)
        if cooldown is None:
            results.append({"mode": mode, "state": "not_started_deadline", "planned_journeys": len(ACTORS)})
            continue
        before = Counter(counters.snapshot())
        redis_before = storage.storage.info("stats")["total_commands_processed"]

        async def request(index, *, phase_mode=mode):
            uid = ACTORS[index]
            return await journey(client, tokens[uid], uid, mode=phase_mode)

        rows = await scheduled_phase(count=len(ACTORS), concurrency=CONCURRENCY,
            arrivals_per_second=None, deadline=deadline, request=request)
        redis_after = storage.storage.info("stats")["total_commands_processed"]
        after = counters.snapshot()
        wire = {name: after.get(name, 0) - before.get(name, 0) for name in set(before) | set(after)}
        successful = [row for row in rows if row.get("contract_valid") is True]
        results.append({"mode": mode, "planned_journeys": len(ACTORS), "journey_concurrency": CONCURRENCY,
            "planned_http_requests": len(ACTORS) * requests_per_journey,
            "completed_http_responses": sum(type(status) is int for row in rows for status in row.get("http_statuses", ())),
            "successful_owned_journeys": len(successful), "cooldown_seconds_before_phase": cooldown,
            "success_journey_p95_ms": percentile([row["arrival_to_completion_ms"] for row in successful], .95),
            "response_body_bytes": sum(row["bytes"] for row in rows),
            "wire_and_sql": wire,
            "redis_server_command_delta_including_one_stats_observation": redis_after - redis_before,
            "journeys": rows})
    return time.perf_counter() - start


def validate(results: list[dict], preflight: dict) -> dict:
    failures: Counter[str] = Counter()
    if preflight != {**BASELINE_PREFLIGHT, "missing_workspace_bearer_status": 401,
                     "foreign_workspace_status": 404}:
        failures["preflight_boundary_or_contract"] += 1
    if [row.get("mode") for row in results] != list(EXPECTED_HTTP):
        failures["missing_or_duplicate_phase"] += 1
    projections = {}
    for phase in results:
        mode = phase.get("mode")
        if mode not in EXPECTED_HTTP or phase.get("state") == "not_started_deadline":
            failures["unstarted_or_unknown_phase"] += 1
            continue
        calls = EXPECTED_HTTP[mode]
        total = len(ACTORS) * calls
        rows = phase.get("journeys", [])
        if (phase.get("planned_journeys") != len(ACTORS) or phase.get("planned_http_requests") != total
                or phase.get("completed_http_responses") != total or phase.get("successful_owned_journeys") != len(ACTORS)
                or [row.get("actor") for row in rows] != list(ACTORS)):
            failures["journey_denominator"] += 1
        if any(row.get("status") != 200 or row.get("http_statuses") != [200] * calls
               or row.get("contract_valid") is not True or not row.get("workspace_sha256") for row in rows):
            failures["owned_response_contract"] += 1
        cooldown = phase.get("cooldown_seconds_before_phase")
        if type(cooldown) not in (int, float) or not math.isfinite(cooldown) or cooldown < FRESH_WINDOW_COOLDOWN_SECONDS:
            failures["fresh_window_cooldown"] += 1
        wire = phase.get("wire_and_sql", {})
        if (wire.get("synthetic_admin_list_databases") != 2 * total
                or wire.get("native_account_validate") != total or wire.get("synthetic_admin_get_database", 0) != 0):
            failures["exact_native_account_reads"] += 1
        if any(wire.get(name, 0) != 0 for name in
               ("sql_INSERT", "sql_UPDATE", "sql_DELETE", "data_commit", "blocked_external_attempts")):
            failures["unexpected_write_or_external_attempt"] += 1
        projections[mode] = [row.get("workspace_sha256") for row in rows]
    if projections.get("legacy_pair") != projections.get("consolidated"):
        failures["workspace_projection_changed"] += 1
    return {"passed": not failures, "failures": dict(failures), "production_capacity_qualified": False}
