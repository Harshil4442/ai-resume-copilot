"""Finite owned Dashboard/Jobs contrast with transport-blocked synthetic counters."""
from __future__ import annotations

import asyncio
import json
import subprocess
import sys
from pathlib import Path

import httpx
import pytest

from scripts import stage5_jobs_journey as diagnostic
from scripts.stage5_capacity import DASHBOARD_PATH, Plan
from tests.fixtures import stage5_native

PREFLIGHT = {"jobs_missing_status": 401, "jobs_foreign_status": 404, "jobs_foreign_private": True}
HEADERS = {"Cache-Control": "private, no-store", "Retry-After": "60", "Pragma": "no-cache"}


def payload(uid, key, *, compact):
    if key == "resumes":
        return {"resumes": [{"id": uid}], "next_cursor": None, "selected_resume": None}
    if key == "catalog":
        return {"sources": [{"id": "capacity-source"}], "auto_submit_enabled": False,
                "coverage": {"worldwide_recall_verified": False}}
    if key == "saved_searches":
        return {"items": [{"id": f"capacity-search-{uid}"}], "next_cursor": None}
    row = dict.fromkeys(("batch_id", "opening_key", "employer_key", "admission_snapshot", "pricing_snapshot"))
    row.update(id=f"capacity-application-{uid}", status="needs_action", application_mode="manual", credit_cost=0,
               package_digest="f" * 64, artifact={"filename": f"capacity-owner-{uid}.pdf", "media_type": "application/pdf"},
               posting={"title": "Engineer", "employer": "Synthetic Employer", "source_id": "capacity-source",
                        "application_mode": "manual"})
    if compact:
        row["posting"]["has_valid_apply_url"] = True
        row["has_missing_fields"] = False
        return [row]
    row["posting"]["apply_url"] = "https://employer.example/apply/stage5"
    row["missing_fields"] = []
    return {"items": [row]}


@pytest.mark.parametrize("problem", [None, "cross_owner", "projection", "bad_refusal", "fallback", "candidate_429"])
def test_legacy_pressure_and_candidate_budget(monkeypatch, problem):
    async def run():
        counters = stage5_native.Counters()
        calls, phases = [], []
        now = 0.0
        admitted = 0

        async def fresh(**_):
            nonlocal admitted, now
            admitted = 0
            now += 61
            return 61

        async def sleep(seconds):
            nonlocal now
            now += seconds

        def get(request):
            nonlocal admitted
            actor = int(request.headers["authorization"].split("-")[-1])
            path = request.url.raw_path.decode()
            calls.append((actor, path))
            counters.add("native_account_validate")
            refused = admitted >= 40 or (problem == "candidate_429" and path == diagnostic.BOOTSTRAP)
            if refused:
                msg = "Request capacity is temporarily allocated. Please try again later."
                headers = HEADERS if problem != "bad_refusal" else HEADERS | {"Retry-After": "1"}
                return httpx.Response(429, json={"detail": {"code": "NATIVE_METADATA_CAPACITY", "message": msg}},
                                      headers=headers)
            admitted += 2
            counters.add("synthetic_admin_list_databases", 2)
            if path == DASHBOARD_PATH:
                body = {"actor": actor}
            elif path == diagnostic.BOOTSTRAP:
                body = {key: payload(actor, key, compact=True) for key in diagnostic.LEGACY}
                body["schema_version"] = "owned-jobs-bootstrap-v1"
                if problem == "cross_owner":
                    body["resumes"]["resumes"][0]["id"] = 999
                if problem == "projection":
                    body["applications"][0]["status"] = "cancelled"
                if problem == "fallback":
                    counters.add("synthetic_admin_get_database")
            else:
                key = next(key for key, value in diagnostic.LEGACY.items() if path == value)
                body = payload(actor, key, compact=False)
            return httpx.Response(200, json=body, headers=HEADERS)

        monkeypatch.setattr(diagnostic, "wait_for_fresh_window", fresh)
        monkeypatch.setattr(stage5_native, "check_body", lambda _, body, *, user_id, **_options: body == {"actor": user_id})
        tokens = {uid: f"owner-{uid}" for uid in diagnostic.ACTORS}
        async with httpx.AsyncClient(transport=httpx.MockTransport(get), base_url="https://127.0.0.1:59999") as client:
            await diagnostic.measure(client, Plan(), tokens, counters, phases, clock=lambda: now, sleep=sleep)
        return calls, phases

    calls, phases = asyncio.run(run())
    assert len(calls) == 56
    assert sum(row["status"] == 429 for row in phases[0]["jobs"]) == 20
    assert phases[0]["wire_and_sql"]["synthetic_admin_list_databases"] == 40
    if problem is None:
        assert phases[1]["wire_and_sql"]["synthetic_admin_list_databases"] == 32
    result = diagnostic.validate(phases, PREFLIGHT)
    assert result["passed"] is (problem is None)
    assert result["production_capacity_qualified"] is False


def test_plan_is_fixed_and_does_not_require_any_runtime(tmp_path):
    script = Path(__file__).resolve().parents[1] / "scripts/run_stage5_capacity.py"

    def run(*args):
        return subprocess.run([sys.executable, str(script), *args], capture_output=True, text=True,
                              timeout=10, env={"PATH": "/usr/bin:/bin"}, cwd=tmp_path)

    report = json.loads(run("--jobs-journey").stdout)
    assert report["version"] == diagnostic.VERSION
    assert report["maximum_requests_including_controls"] == 61
    assert report["jobs_start_seconds"] == 27 and report["minimum_cooldown_seconds"] == 61
    assert report["shared_admin_limit"] == 40 and not report["production_qualified"]
    assert run("--first-minute", "--jobs-journey").returncode != 0
    assert json.loads(run().stdout)["maximum_requests_including_controls"] == 303


def test_owned_pg17_seed_matches_real_jobs_projections(tmp_path):
    import os

    from fastapi.encoders import jsonable_encoder
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import Session

    from app import database, models
    from app.domains.employer import schemas, search_pages, service
    from app.domains.resume_uploads import browsing
    from app.routers import resume
    from app.routers.v1 import employer
    from scripts.run_stage5_capacity import migrate
    from scripts.stage5_resources import Resources

    configured = os.getenv("HIREWIZ_STAGE5_PG_BIN")
    if not configured:
        if os.getenv("HIREWIZ_STAGE5_PG_REQUIRED") == "true":
            pytest.fail("Jobs fixture requires explicit fresh owned PG17 binaries")
        pytest.skip("Set HIREWIZ_STAGE5_PG_BIN for the migrated Jobs seed check")
    resource = Resources(tmp_path / "owned-jobs", Path(configured), Path("/unused-for-sql"))
    engine = None
    original_database = database.DATABASE_URL
    try:
        resource.start_postgres()
        database.DATABASE_URL = resource.dsn
        migrate(resource)
        engine = create_engine(resource.dsn, hide_parameters=True)
        with Session(engine) as writer:
            for actor in diagnostic.ACTORS:
                writer.add(models.User(id=actor, email=f"candidate-{actor}@example.invalid", job_service_credits=actor))
            writer.flush()
            for actor in diagnostic.ACTORS:
                writer.add(models.Resume(id=actor, user_id=actor, original_filename="synthetic.pdf",
                                         raw_text="synthetic owned source"))
            writer.commit()
        assert diagnostic.seed_jobs(engine) == {"jobs_searches": 8, "jobs_applications": 8, "jobs_sources": 1}
        with Session(engine, autoflush=False) as reader:
            reader.execute(text("SET TRANSACTION READ ONLY"))
            for uid in diagnostic.ACTORS:
                user = reader.get(models.User, uid)
                catalog = service.catalog_for_verified_subject(reader, uid)
                owned = schemas.JobsBootstrapView(catalog=jsonable_encoder(catalog),
                    resumes=browsing.page(reader, uid), saved_searches=search_pages.list_summaries(reader, uid),
                    applications=service.list_bootstrap_application_views(reader, uid)).model_dump(mode="json")
                originals = {
                    "resumes": resume.list_resumes(db=reader, current_user=user),
                    "catalog": catalog,
                    "saved_searches": search_pages.list_summaries(reader, uid),
                    "applications": employer.list_applications(30, db=reader, user=user),
                }
                for key, original in originals.items():
                    assert diagnostic._projection(key, jsonable_encoder(original), uid, "legacy") == (
                        diagnostic._projection(key, owned[key], uid, "bootstrap"))
    finally:
        database.DATABASE_URL = original_database
        if engine is not None:
            engine.dispose()
        resource.close()
        assert not (resource.directory / "pg-data").exists()
