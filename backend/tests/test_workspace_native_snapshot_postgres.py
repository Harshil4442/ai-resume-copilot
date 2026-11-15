"""Real PostgreSQL selection updates with an intercepted TeX worker."""
from concurrent.futures import ThreadPoolExecutor
from threading import Event
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


@pytest.mark.parametrize("during_compile", ["switch", "legacy_switch", "approve", "revoke"])
def test_native_snapshot_does_not_hold_selection_lock_during_compile(pg_engine, composition, monkeypatch, during_compile):
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

    entered, release = Event(), Event()
    compile_fixture = composition[2]

    def delayed_compile(*args, **kwargs):
        entered.set()
        assert release.wait(10), "snapshot test did not release the intercepted compiler"
        return compile_fixture(*args, **kwargs)

    monkeypatch.setattr(native_tex_transport, "remote_compile", delayed_compile)
    monkeypatch.setattr(native_tex, "validate_layout", lambda *_: {"changed_spans": 0})
    with TestClient(app) as client, ThreadPoolExecutor(max_workers=1) as pool:
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
