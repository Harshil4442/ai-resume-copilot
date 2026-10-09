"""Local synthetic application artifact and stale-seal HTTP boundaries.
Provider form reads are mocked; no execute/send endpoint or cloud storage is used.
"""

import pytest
from backend.app.domains.employer.models import (
    EmployerApplication,
    SealedApplicationArtifact,
    ServiceCreditEvent,
)
from backend.app.models import EvidenceItem, Resume, ResumeVersion
from backend.app.routers.v1.career import router as career_router
from backend.app.services import native_tex
from backend.app.services.resume_layout import extract_source_units
from backend.tests.native_tex_ci_fixtures import configure_native_compiler
from backend.tests.test_employer_services import ANSWERS
from backend.tests.test_employer_services import context as employer_context
from fastapi.testclient import TestClient

# Pytest consumes this imported fixture by parameter name during collection.
__all__ = ["employer_context"]


def prepared_version(factory, client, monkeypatch):
    configure_native_compiler(monkeypatch)
    client.app.include_router(career_router, prefix="/api/v1")
    original = "Developed Python reports for customer teams."
    data = (
        r"\documentclass{article}\usepackage[T1]{fontenc}\usepackage{lmodern}\pagestyle{empty}\begin{document}"
        + "\n"
        + original
        + "\n"
        + r"\end{document}"
    ).encode()
    u = next(u for u in extract_source_units(data, "tex") if u["text"] == original)
    edit = {
        "unit_id": u["unit_id"],
        "original_text": original,
        "replacement_text": "Built Python reports for customer teams.",
        "evidence_ids": ["native-fact"],
        "reason": "Supported creation synonym",
    }
    with factory() as db:
        r = db.get(Resume, 10)
        r.source_document = data
        r.source_format = "tex"
        r.original_filename = "source.tex"
        db.add(
            EvidenceItem(
                id="native-fact",
                user_id=1,
                resume_id=10,
                category="experience",
                title="Reviewed source fact",
                evidence_text=original,
                approval_state="approved",
                skills=["Python"],
            )
        )
        db.commit()
    result = client.post(
        "/api/v1/resume-versions",
        json={
            "resume_id": 10,
            "evidence_ids": ["native-fact"],
            "structured_content": {
                "format_preservation": "source",
                "source_format": "tex",
                "source_edits": [edit],
            },
        },
    )
    assert result.status_code == 201, result.text
    row = result.json()
    assert (
        client.patch(
            f"/api/v1/resume-versions/{row['id']}", json={"approval_state": "approved"}
        ).status_code
        == 200
    )
    return row, data


def selection(row, key):
    return {
        "posting_id": "job_1",
        "resume_id": 10,
        "resume_choice": "tailored",
        "resume_version_id": row["id"],
        "idempotency_key": key,
    }


def make_stale(factory, identity):
    # Represents an already approved pre-v2 version retained across upgrade.
    with factory() as db:
        v = db.get(ResumeVersion, identity)
        c = dict(v.structured_content)
        s = dict(c["sealed_native_artifact"])
        s["validator"] = "hirewiz-native-tex-v1"
        c["sealed_native_artifact"] = s
        v.structured_content = c
        db.commit()


def test_actual_application_preview_download_and_approval_reuse_exact_v2_pdf(
    employer_context, monkeypatch
):
    factory, client, _ = employer_context
    try:
        row, data = prepared_version(factory, client, monkeypatch)
        pdf = client.get(f"/api/v1/resume-versions/{row['id']}/download?format=pdf").content
        monkeypatch.setattr(
            native_tex,
            "compile_project",
            lambda *a, **kw: pytest.fail("application must use sealed PDF without regeneration"),
        )
        created = client.post(
            "/api/v1/employer-jobs/applications", json=selection(row, "native-exact-v2")
        )
        assert created.status_code == 201, created.text
        app = created.json()
        assert app["artifact"]["sha256"] == native_tex.digest(pdf)
        preview = client.get(app["artifact"]["preview_url"])
        assert preview.status_code == 200 and preview.content == pdf
        download = client.get(app["artifact"]["preview_url"] + "?download=true")
        assert download.status_code == 200 and download.content == pdf
        update = client.put(
            f"/api/v1/employer-jobs/applications/{app['id']}/package",
            json={
                "resume_id": 10,
                "resume_choice": "tailored",
                "resume_version_id": row["id"],
                "answers": ANSWERS,
                "consents": {},
            },
        )
        assert update.status_code == 200, update.text
        approved = client.post(
            f"/api/v1/employer-jobs/applications/{app['id']}/approve",
            json={
                "package_digest": update.json()["package_digest"],
                "allowed_actions": ["upload", "submit"],
            },
        )
        assert approved.status_code == 200 and approved.json()["artifact"][
            "sha256"
        ] == native_tex.digest(pdf)
        with factory() as db:
            assert db.get(Resume, 10).source_document == data
            assert db.query(ServiceCreditEvent).count() == 0
    finally:
        client.close()
        factory.kw["bind"].dispose()


@pytest.mark.parametrize("operation", ["create", "update_package"])
def test_pre_v2_approved_version_application_boundary_returns_guided_409(
    operation, employer_context, monkeypatch
):
    factory, client, _ = employer_context
    http = None
    try:
        row, data = prepared_version(factory, client, monkeypatch)
        app = None
        if operation == "update_package":
            result = client.post(
                "/api/v1/employer-jobs/applications", json=selection(row, "native-before-stale")
            )
            assert result.status_code == 201, result.text
            app = result.json()
        make_stale(factory, row["id"])
        with factory() as db:
            counts = (
                db.query(EmployerApplication).count(),
                db.query(SealedApplicationArtifact).count(),
                db.query(ServiceCreditEvent).count(),
            )
        monkeypatch.setattr(
            native_tex,
            "compile_project",
            lambda *a, **kw: pytest.fail("stale version must not regenerate"),
        )
        http = TestClient(client.app, raise_server_exceptions=False)
        if operation == "create":
            result = http.post(
                "/api/v1/employer-jobs/applications", json=selection(row, "native-stale-create")
            )
        else:
            result = http.put(
                f"/api/v1/employer-jobs/applications/{app['id']}/package",
                json={
                    "resume_id": 10,
                    "resume_choice": "tailored",
                    "resume_version_id": row["id"],
                    "answers": ANSWERS,
                    "consents": {},
                },
            )
        # State must remain exact even if the diagnostic response is incorrect.
        with factory() as db:
            assert (
                db.query(EmployerApplication).count(),
                db.query(SealedApplicationArtifact).count(),
                db.query(ServiceCreditEvent).count(),
            ) == counts
            assert db.get(Resume, 10).source_document == data
        assert result.status_code == 409, {
            "operation": operation,
            "status": result.status_code,
            "body": result.text[:300],
        }
        assert "review it again" in result.json()["detail"]
    finally:
        if http:
            http.close()
        client.close()
        factory.kw["bind"].dispose()
