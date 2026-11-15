"""Finite Dashboard → Jobs comparison using the shared owned native governor."""
from __future__ import annotations

import asyncio
import hashlib
import json
import time
from collections import Counter

from scripts.stage5_capacity import (
    DASHBOARD_PATH,
    FRESH_WINDOW_COOLDOWN_SECONDS,
    expected_capacity_refusal,
    private_no_store,
    scheduled_phase,
    wait_for_fresh_window,
)

VERSION = "stage5-owned-dashboard-jobs-v1"
ACTORS = tuple(range(1, 9))
PREFIX = "/api/v1/employer-jobs"
BOOTSTRAP = PREFIX + "/bootstrap"
LEGACY = {
    "resumes": "/api/resume/list",
    "catalog": PREFIX + "/catalog",
    "saved_searches": PREFIX + "/search-summaries?limit=10",
    "applications": PREFIX + "/applications",
}
MODES = ("legacy", "bootstrap")
JOBS_AT = 27
WINDOW_END = 55
ADMIN_LIMIT = 40
MAX_REQUESTS = (len(LEGACY) + 3) * len(ACTORS) + 5
MINIMUM_SECONDS = len(MODES) * (FRESH_WINDOW_COOLDOWN_SECONDS + WINDOW_END)


def seed_jobs(engine):
    from sqlalchemy import text
    from sqlalchemy.orm import Session

    from app.domains.employer import models

    with Session(engine) as db:
        if tuple(db.execute(text("SELECT current_database(), current_schema()")).one()) != ("stage5_owned", "stage5_owned"):
            raise RuntimeError("Jobs seed requires the owned database and schema")
        db.add(models.EmployerSource(id="capacity-source", employer="Synthetic Employer", platform="greenhouse",
            board_token="stage5-synthetic", enabled=True, careers_url="https://employer.example/careers",
            verification_url="https://employer.example/careers", verification_note="Owned capacity fixture"))
        db.flush()
        db.add(models.EmployerPosting(id="capacity-posting", source_id="capacity-source", external_id="stage5",
            title="Engineer", employer="Synthetic Employer", description="Synthetic posting",
            canonical_url="https://employer.example/careers/stage5", apply_url="https://employer.example/apply/stage5",
            content_sha256="b" * 64))
        db.flush()
        for uid in ACTORS:
            db.add(models.SealedApplicationArtifact(id=f"capacity-artifact-{uid}", user_id=uid, resume_id=uid,
                sha256="c" * 64, filename=f"capacity-owner-{uid}.pdf", media_type="application/pdf", size_bytes=32,
                content=b"synthetic sealed source"))
            db.add(models.EmployerSearch(id=f"capacity-search-{uid}", user_id=uid, resume_id=uid,
                idempotency_key=f"capacity-search-{uid}", input_fingerprint="d" * 64, desired_count=2,
                _legacy_query={"role": "Engineer"}, _legacy_items=[], scope={}))
        db.flush()
        for uid in ACTORS:
            db.add(models.EmployerApplication(id=f"capacity-application-{uid}", user_id=uid, posting_id="capacity-posting",
                resume_id=uid, resume_choice="original", artifact_id=f"capacity-artifact-{uid}",
                idempotency_key=f"capacity-application-{uid}", input_fingerprint="e" * 64,
                form={"fields": [], "consents": []}, package_digest="f" * 64, job_content_sha256="b" * 64,
                credit_cost=0))
        db.commit()
    return {"jobs_searches": len(ACTORS), "jobs_applications": len(ACTORS), "jobs_sources": 1}


def controls(client, tokens):
    missing = client.get(BOOTSTRAP)
    foreign = client.get(BOOTSTRAP + "?current_resume_id=2", headers={"Authorization": "Bearer " + tokens[1]})
    return {"jobs_missing_status": missing.status_code, "jobs_foreign_status": foreign.status_code,
            "jobs_foreign_private": private_no_store(foreign.headers)}


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def _projection(key, body, uid, mode):
    if key == "resumes":
        assert [row["id"] for row in body["resumes"]] == [uid]
        if mode == "bootstrap":
            assert body["next_cursor"] is None and body["selected_resume"] is None
        return body["resumes"]
    if key == "catalog":
        assert [row["id"] for row in body["sources"]] == ["capacity-source"]
        assert body["auto_submit_enabled"] is False and body["coverage"]["worldwide_recall_verified"] is False
        return body
    if key == "saved_searches":
        assert [row["id"] for row in body["items"]] == [f"capacity-search-{uid}"]
        assert body["next_cursor"] is None
        return body
    if key != "applications":
        raise ValueError("Unknown Jobs result")
    app, = body if mode == "bootstrap" else body["items"]
    assert app["id"] == f"capacity-application-{uid}" and app["artifact"]["filename"] == f"capacity-owner-{uid}.pdf"
    if mode == "bootstrap":
        assert "answers" not in app and "form" not in app and "description" not in app["posting"]
        assert app["posting"]["has_valid_apply_url"] is True
    else:
        assert app["posting"]["apply_url"] == "https://employer.example/apply/stage5"
    selected = ("id", "status", "application_mode", "batch_id", "credit_cost", "package_digest",
                "opening_key", "employer_key", "admission_snapshot", "pricing_snapshot")
    return {field: app[field] for field in selected} | {
        "has_missing_fields": app["has_missing_fields"] if mode == "bootstrap" else bool(app["missing_fields"]),
        "artifact": {field: app["artifact"][field] for field in ("filename", "media_type")},
        "posting": {field: app["posting"][field] for field in ("title", "employer", "source_id", "application_mode")},
    }


async def _request(client, tokens, uid, key, mode, buyers_percent):
    path = DASHBOARD_PATH if key == "dashboard" else BOOTSTRAP if key == "bootstrap" else LEGACY[key]
    response = await client.get(path, headers={"Authorization": "Bearer " + tokens[uid]})
    row = {"actor": uid, "view": key, "status": response.status_code, "bytes": len(response.content)}
    try:
        body = response.json()
        if response.status_code == 429:
            row["refusal_valid"] = expected_capacity_refusal("list_governed_40", response.status_code, body, response.headers)
        if response.status_code == 200:
            assert private_no_store(response.headers)
            if key == "dashboard":
                from tests.fixtures.stage5_native import check_body

                assert check_body(DASHBOARD_PATH, body, user_id=uid,
                    buyer=uid % (5 if buyers_percent == 20 else 4) == 0, heavy=uid == 8)
            elif key == "bootstrap":
                assert body["schema_version"] == "owned-jobs-bootstrap-v1"
                row["digests"] = {name: _digest(_projection(name, body[name], uid, mode)) for name in LEGACY}
            else:
                row["digests"] = {key: _digest(_projection(key, body, uid, mode))}
            row["contract_valid"] = True
    except (AssertionError, IndexError, KeyError, TypeError, ValueError):
        row["contract_valid"] = False
    return row


async def measure(client, plan, tokens, counters, results, *, clock=time.perf_counter, sleep=asyncio.sleep):
    started = clock()
    deadline = started + plan.measurement_seconds
    for mode in MODES:
        cooldown = await wait_for_fresh_window(deadline=deadline)
        if cooldown is None:
            break
        phase_start = clock()
        before = Counter(counters.snapshot())

        async def wave(planned, expires, *, arm=mode):
            async def send(index):
                uid, key = planned[index]
                return await _request(client, tokens, uid, key, arm, plan.buyers_percent)

            return await scheduled_phase(count=len(planned), concurrency=4,
                arrivals_per_second=None, deadline=expires, request=send, clock=clock)

        dashboard = await wave([(uid, "dashboard") for uid in ACTORS], phase_start + JOBS_AT)
        await sleep(max(0, phase_start + JOBS_AT - clock()))
        offset = clock() - phase_start
        jobs = await wave([(uid, key) for uid in ACTORS for key in (LEGACY if mode == "legacy" else ("bootstrap",))],
                          min(deadline, phase_start + WINDOW_END))
        after = counters.snapshot()
        results.append({"mode": mode, "fresh_seconds": cooldown, "jobs_start_seconds": offset,
                        "duration_seconds": clock() - phase_start, "dashboard": dashboard, "jobs": jobs,
                        "wire_and_sql": {name: value - before[name] for name, value in after.items()}})
    return clock() - started


def validate(results, preflight):
    failures: Counter[str] = Counter()
    if (preflight.get("jobs_missing_status"), preflight.get("jobs_foreign_status"),
            preflight.get("jobs_foreign_private")) != (401, 404, True):
        failures["owner_controls"] += 1
    if [phase["mode"] for phase in results] != list(MODES):
        failures["unstarted_or_unknown_phase"] += 1
    observed = {}
    for phase in results:
        mode = phase["mode"]
        dashboards, jobs, wire = phase["dashboard"], phase["jobs"], phase["wire_and_sql"]
        expected = [(uid, key) for uid in ACTORS for key in (LEGACY if mode == "legacy" else ("bootstrap",))]
        if ([row.get("actor") for row in dashboards] != list(ACTORS)
                or [(row.get("actor"), row.get("view")) for row in jobs] != expected
                or not all(row["status"] == 200 and row.get("contract_valid") is True for row in dashboards)):
            failures["journey_denominator_or_dashboard"] += 1
        if not (phase["fresh_seconds"] >= FRESH_WINDOW_COOLDOWN_SECONDS
                and JOBS_AT <= phase["jobs_start_seconds"] <= phase["duration_seconds"] <= WINDOW_END):
            failures["window_bounds"] += 1
        lists, refused = (ADMIN_LIMIT, True) if mode == "legacy" else (len(ACTORS) * 4, False)
        if (wire.get("native_account_validate") != len(dashboards) + len(jobs)
                or wire.get("synthetic_admin_list_databases") != lists
                or any(wire.get(key, 0) for key in ("synthetic_admin_get_database", "sql_INSERT", "sql_UPDATE",
                                                    "sql_DELETE", "data_commit", "blocked_external_attempts"))):
            failures["native_budget_or_effects"] += 1
        if (any(row["status"] != 200 or row.get("contract_valid") is not True for row in jobs
                if not (refused and row["status"] == 429 and row.get("refusal_valid") is True))
                or any(row["status"] == 429 for row in jobs) != refused):
            failures["jobs_result_or_refusal"] += 1
        observed[mode] = {(row["actor"], key): digest for row in jobs for key, digest in row.get("digests", {}).items()}
    legacy, new = (observed.get(mode, {}) for mode in MODES)
    if {key for _, key in legacy} != set(LEGACY) or any(new.get(key) != value for key, value in legacy.items()):
        failures["owned_projection_mismatch"] += 1
    return {"passed": not failures, "failures": dict(failures), "production_capacity_qualified": False}
