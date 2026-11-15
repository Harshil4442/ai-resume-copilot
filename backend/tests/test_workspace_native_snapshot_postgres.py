"""Real PostgreSQL selection updates with an intercepted TeX worker."""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Event
from types import SimpleNamespace

import pytest
from backend.app import models
from backend.app.database import get_db
from backend.app.routers.v1 import career
from backend.app.security import get_current_user
from backend.app.services import native_tex, native_tex_transport
from backend.tests.test_employer_admissions_postgres import pg_engine as pg_engine
from backend.tests.test_native_tex_inspection import PDF, SOURCE
from backend.tests.test_native_tex_inspection import composition as composition
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import sessionmaker

PATH = "/api/v1/opportunities/opp_native_snapshot"


@pytest.fixture
def native_version_context(pg_engine, composition, monkeypatch):
    models.Base.metadata.create_all(pg_engine)
    factory = sessionmaker(bind=pg_engine)
    with factory() as db:
        db.add(models.User(id=1, email="snapshot-owner@example.test"))
        db.add_all([models.Resume(id=1, user_id=1, original_filename="original.tex",
                    source_format="tex", source_document=SOURCE),
                    models.Resume(id=2, user_id=1, original_filename="other.pdf",
                    source_format="pdf", source_document=PDF)])
        db.flush()
        db.add(models.Opportunity(id="opp_native_snapshot", user_id=1, resume_id=1,
                                 title="Synthetic Engineer", job_description="Build reliable services."))
        db.add_all([models.EvidenceItem(id=identity, user_id=1, resume_id=1, category="experience",
                    title=identity, evidence_text="Synthetic experience.", approval_state=state)
                    for identity, state in (("initial-fact", "approved"), ("later-fact", "pending"))])
        db.commit()

    app = FastAPI()
    app.include_router(career.router, prefix="/api/v1")

    def database():
        with factory() as db:
            db.execute(text("SET LOCAL lock_timeout = '250ms'"))
            yield db

    app.dependency_overrides[get_db] = database
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=1)
    app.dependency_overrides[career.require_career_workspace] = lambda: None

    @app.exception_handler(DBAPIError)
    async def database_failure(_request: Request, exc: DBAPIError):
        return JSONResponse(status_code=503, content={"sqlstate": exc.orig.sqlstate})

    monkeypatch.setattr(native_tex, "validate_layout", lambda *_: {"changed_spans": 0})
    with TestClient(app) as client:
        yield client, factory


@pytest.mark.parametrize("during_compile", ["switch", "legacy_switch", "approve", "revoke"])
def test_native_snapshot_does_not_hold_selection_lock_during_compile(native_version_context, composition, monkeypatch, during_compile):
    client, factory = native_version_context
    entered, release = Event(), Event()
    compile_fixture = composition[2]

    def delayed_compile(*args, **kwargs):
        entered.set()
        assert release.wait(10), "snapshot test did not release the intercepted compiler"
        return compile_fixture(*args, **kwargs)

    monkeypatch.setattr(native_tex_transport, "remote_compile", delayed_compile)
    with ThreadPoolExecutor(max_workers=1) as pool:
        endpoint = PATH + "/workspace/source-snapshot"
        if during_compile == "legacy_switch":
            snapshot = pool.submit(client.post, endpoint)
        else:
            snapshot = pool.submit(client.post, endpoint + "/selected", json={"expected_resume_id": 1})
        try:
            assert entered.wait(5), "selected snapshot did not reach the intercepted compiler"
            if during_compile in {"switch", "legacy_switch"}:
                switched = client.patch(PATH, json={"resume_id": 2})
                assert switched.status_code == 200, switched.text
            else:
                with factory() as db:
                    if during_compile == "approve":
                        db.get(models.EvidenceItem, "later-fact").approval_state = "approved"
                    else:
                        db.get(models.EvidenceItem, "initial-fact").approval_state = "rejected"
                    db.commit()
        finally:
            release.set()
        result = snapshot.result(timeout=10)

    assert [event[0] for event in composition[0]] == ["inspect", "compile", "inspect"]
    if during_compile == "approve":
        assert result.status_code == 201, result.text
        with factory() as db:
            row = db.query(models.ResumeVersion).one()
            assert (row.resume_id, row.version_number, row.evidence_ids) == (1, 1, ["initial-fact"])
            seal = row.structured_content["sealed_native_artifact"]
            assert native_tex.sealed_bytes(seal, SOURCE, "tex", [], "pdf") == PDF
            assert native_tex.sealed_bytes(seal, SOURCE, "tex", [], "tex") == SOURCE
    else:
        if during_compile == "revoke":
            assert result.status_code == 422, result.text
            assert result.json()["detail"] == "Every referenced evidence item must be owned and approved"
        else:
            assert result.status_code == 409, result.text
            assert result.json()["detail"]["code"] == "source_snapshot_resume_changed"
        with factory() as db:
            assert db.query(models.ResumeVersion).count() == 0


@pytest.mark.parametrize("second_endpoint", ["direct", "workspace"])
def test_prepared_native_versions_allocate_distinct_numbers(native_version_context, composition, monkeypatch, second_endpoint):
    client, factory = native_version_context
    renderers = Barrier(2, timeout=5)
    compile_fixture = composition[2]

    def compile_together(*args, **kwargs):
        renderers.wait()
        return compile_fixture(*args, **kwargs)

    monkeypatch.setattr(native_tex_transport, "remote_compile", compile_together)
    direct = "/api/v1/resume-versions"
    payload = {"resume_id": 1, "opportunity_id": "opp_native_snapshot", "label": "Native source",
               "evidence_ids": ["initial-fact"]}
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(client.post, direct, json=payload)
        if second_endpoint == "direct":
            second = pool.submit(client.post, direct, json=payload)
        else:
            second = pool.submit(client.post, PATH + "/workspace/source-snapshot/selected", json={"expected_resume_id": 1})
        results = [response.result(timeout=10) for response in (first, second)]
    assert [response.status_code for response in results] == [201, 201], [
        response.json() for response in results if response.status_code != 201
    ]
    assert sorted(response.json()["version_number"] for response in results) == [1, 2]
    assert sum(event[0] == "compile" for event in composition[0]) == 2
    with factory() as db:
        assert [row.version_number for row in db.query(models.ResumeVersion).order_by(models.ResumeVersion.version_number)] == [1, 2]
