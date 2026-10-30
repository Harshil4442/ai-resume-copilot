"""Prospective, pinned actual-source compatibility on owned PostgreSQL17.

The historical e23 writer has no evidence CAS. Its successful stale writes are
UNSAFE negative controls, never qualification for arbitrary mixed-version writes.
Native promotion must separately fence actual old-writer reachability and deploy
the new API before the new frontend. This module proves neither serving identity
nor that fence. All records and source bytes here are finite synthetic fixtures.
Existing native CAS/transcription/consumer suites retain their independent scope.
The actual schema26 fence must contain the old writer; these tests do not prove
that the migration has been applied to a serving production database.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from uuid import uuid4

import pytest
from sqlalchemy import text
from test_schema25_app_compatibility_postgres import (  # noqa: F401
    ROOT,
    app25_database,
    release,
    run_app,
)

from scripts import schema25_app_release_contract_v3 as contract


def upgrade26(engine):
    from alembic.config import Config

    from alembic import command

    config = Config(str(ROOT / "backend/alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "backend/alembic"))
    with engine.begin() as db:
        config.attributes["connection"] = db
        command.upgrade(config, "20261031_0026")


@pytest.fixture
def v3_database(request):
    # No request for owned_postgres, service discovery or local-server fallback.
    # A mandatory CI invocation must fail before the imported fixture can skip.
    if not os.getenv("HIREWIZ_TEST_POSTGRES_URL"):
        pytest.fail("App25V3 requires its exact dedicated PostgreSQL17 URL")
    if os.getenv("HIREWIZ_APP25_TEST_ROOT"):
        pytest.fail("App25V3 must not override its exact PostgreSQL17 URL with a local test root")
    return request.getfixturevalue("app25_database")


def test_required_ci_missing_postgres_url_fails_before_requesting_any_database(monkeypatch):
    monkeypatch.setenv("HIREWIZ_APP_COMPATIBILITY_REQUIRED", "true")
    monkeypatch.delenv("HIREWIZ_TEST_POSTGRES_URL", raising=False)

    class NoDatabaseFallback:
        def getfixturevalue(self, name):
            raise AssertionError("Missing URL must never request a fallback fixture")

    with pytest.raises(pytest.fail.Exception, match="exact dedicated PostgreSQL17 URL"):
        v3_database.__wrapped__(NoDatabaseFallback())


@pytest.fixture
def v3_roots(tmp_path):
    files = release.initial.prior_app_files(ROOT, contract.PRIOR_RELEASE)
    assert release.initial.prior_app_digest(files) == contract.PRIOR_APP_SHA256
    assert contract.source_fingerprint(ROOT, files)
    for name, raw in files.items():
        target = tmp_path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(raw)
    return {"prior": tmp_path / "backend", "candidate": ROOT / "backend"}


@pytest.mark.parametrize("producer,consumer", [("prior", "candidate"), ("candidate", "prior")])
def test_pinned_historical_and_candidate_processes_exchange_pilot_storage(
    v3_database, v3_roots, producer, consumer, record_property,
):
    record_property("prospective_prior_source", contract.PRIOR_RELEASE)
    record_property("prior_app_sha256", contract.PRIOR_APP_SHA256)
    record_property("candidate_app_sha256", contract.CANDIDATE_APP_SHA256)
    identity = "app25-v3-" + uuid4().hex
    receipt = run_app(v3_roots[producer], v3_database, "produce", identity)
    with v3_database.connect() as db:
        signature, pins = contract.v1.observe(db), contract.v1.liability_pins(db)
    assert run_app(v3_roots[consumer], v3_database, "consume", identity, receipt) == {
        "status": "actual-source-compatibility-observed"}
    with v3_database.connect() as db:
        assert contract.v1.observe(db) == signature
        contract.v1.verify_liability_pins(db, pins)
        assert db.scalar(text("SELECT count(*) FROM public.credit_execution_holds")) == 0


# Every invocation imports its own real app archive, including its real HTTP
# validation, authentication, evidence writer and DB session. No current-code
# adapter substitutes for the old writer; the old source cannot issue CAS tokens.
EVIDENCE_SCRIPT = r'''
import hashlib, io, json, os, sys
from pathlib import Path
from fastapi.testclient import TestClient
from sqlalchemy import text
import app as actual_app
from app import models
from app.database import SessionLocal
from app.main import app
from app.security import create_access_token
from app.services import llm_client
from google import genai
from reportlab.pdfgen.canvas import Canvas

assert Path(actual_app.__file__).resolve().parent == Path(sys.path[0]).resolve() / 'app'
mode, identity = sys.argv[1:3]
receipt = json.loads(sys.argv[3]) if len(sys.argv) > 3 else {}
original = 'Developed clear docu-\nmented records.'
corrected = 'Developed clear documented records.'
def refuse_provider(*args, **kwargs):
    raise AssertionError('No model provider is permitted during compatibility rehearsal')
genai.Client = refuse_provider
llm_client._chat = refuse_provider
headers = {'Authorization': 'Bearer ' + create_access_token(subject='1', credential_epoch=9)}
foreign = {'Authorization': 'Bearer ' + create_access_token(subject='2', credential_epoch=3)}
def listed(client, auth=headers):
    response = client.get('/api/v1/evidence-items', headers=auth,
        params={'resume_id': receipt['resume']})
    assert response.status_code == 200
    return response.json()
def public(item):
    return {key: value for key, value in item.items() if key != 'snapshot_fingerprint'}

with TestClient(app, raise_server_exceptions=False) as client:
    if mode == 'seed':
        # Owned synthetic extraction state, not a native parser/worker receipt.
        source = io.BytesIO()
        pdf = Canvas(source, invariant=True)
        for index, line in enumerate(('Synthetic Compatibility Candidate\nEXPERIENCE\n' + original).splitlines()):
            pdf.drawString(48, 740 - index * 16, line)
        pdf.save()
        raw = source.getvalue()
        with SessionLocal() as db:
            db.add_all([models.User(id=1, email=identity+'@example.invalid', ai_credits=17,
                job_service_credits=13, credential_epoch=9),
                models.User(id=2, email='foreign-'+identity+'@example.invalid', ai_credits=0,
                job_service_credits=0, credential_epoch=3)])
            db.commit()
            resume = models.Resume(user_id=1, original_filename='synthetic-compatibility.pdf',
                raw_text='Synthetic Compatibility Candidate\nEXPERIENCE\n'+original,
                sections={'experience':original}, skills=['Python'], experience_years=2.0,
                contact_info={'name':'Synthetic Compatibility Candidate'}, source_document=raw, source_format='pdf')
            db.add(resume)
            db.commit()
            receipt = {'resume':resume.id, 'source_sha256':hashlib.sha256(raw).hexdigest()}
        imported = client.post('/api/v1/evidence-items/import-resume/'+str(receipt['resume']), headers=headers)
        assert imported.status_code == 200
        assert len(imported.json()['created']) == 1 and imported.json()['skipped'] == 0
        receipt['evidence'] = imported.json()['created'][0]
        result = receipt
    elif mode == 'read':
        rows = listed(client)
        assert len(rows) == 1 and public(rows[0]) == public(receipt['evidence'])
        assert listed(client, foreign) == []
        downloaded = client.get('/api/resume/'+str(receipt['resume'])+'/source', headers=headers)
        assert downloaded.status_code == 200
        assert hashlib.sha256(downloaded.content).hexdigest() == receipt['source_sha256']
        assert client.get('/api/resume/'+str(receipt['resume'])+'/source', headers=foreign).status_code == 404
        result = {'status':'actual-source-evidence-read-observed'}
    elif mode == 'review':
        row = listed(client)[0]
        shown = client.get('/api/v1/evidence-items/transcription-review/'+str(receipt['resume']), headers=headers)
        assert shown.status_code == 200 and shown.json()['status'] == 'needs_review'
        reviewed = client.post('/api/v1/evidence-items/'+row['id']+'/transcription-review', headers=headers,
            json={'expected_fingerprint':row['snapshot_fingerprint'],
                'expected_assessment_fingerprint':shown.json()['assessment_fingerprint'],
                'section':'experience', 'action':'correct_transcription', 'evidence_text':corrected})
        assert reviewed.status_code == 200, reviewed.text
        value = reviewed.json()
        assert value['approval_state'] == 'approved' and value['evidence_text'] == corrected
        assert '\ntranscription-review:v1:' in value['source_ref']
        receipt.update(evidence=value, shown_fingerprint=row['snapshot_fingerprint'])
        result = receipt
    elif mode == 'patch':
        response = client.patch('/api/v1/evidence-items/'+receipt['evidence']['id'], headers=headers,
            json=receipt['changes'])
        result = {'status':response.status_code, 'body':response.json() if response.headers.get('content-type','').startswith('application/json') else None}
    elif mode == 'context':
        from app.domains.career import transcription_review
        with SessionLocal() as db:
            context = transcription_review.load_effective_transcription(db, 1, receipt['resume'])
            result = {'status':context.status, 'raw_text':context.raw_text,
                'physical_edit_compatible':context.physical_edit_compatible}
    else:
        raise AssertionError('unknown actual-source evidence phase')
    availability = client.get('/api/v1/credits/availability', headers=headers)
    assert availability.status_code == 200
    assert availability.json()['catalog_enabled'] is False
    assert availability.json()['execution_enabled'] is False
    assert client.get('/api/public/credits/catalog').status_code == 404
    assert os.environ['OPTIONAL_AI_GENERATION_ENABLED'] == 'false'
    assert os.environ['RAZORPAY_CHECKOUT_ENABLED'] == 'false'
with SessionLocal() as db:
    assert (db.get(models.User,1).ai_credits, db.get(models.User,1).job_service_credits) == (17,13)
    assert db.query(models.ModelCallEvent).count() == 0
    assert db.query(models.ModelCostLiability).count() == 0
    assert db.scalar(text('SELECT count(*) FROM public.credit_execution_holds')) == 0
print(json.dumps(result))
'''


def run_evidence(root, engine, mode, identity, receipt=None):
    completed = subprocess.run([sys.executable, "-I", "-B", "-c",
        "import sys;sys.path.insert(0,sys.argv.pop(1));" + EVIDENCE_SCRIPT,
        str(root), mode, identity, *([json.dumps(receipt)] if receipt is not None else [])],
        cwd=root, timeout=45, capture_output=True, text=True,
        env={"PATH": os.environ.get("PATH", ""), "APP_ENV": "test",
            "DATABASE_URL": engine.url.render_as_string(hide_password=False),
            "JWT_SECRET": "schema25-app-v3-compatibility-synthetic-only",
            "RATE_LIMIT_STORAGE_URL": "memory://", "AUTO_DB_MIGRATE": "false",
            "OTEL_SDK_DISABLED": "true", "PYTHON_DOTENV_DISABLED": "1",
            **{name: "false" for name in release.initial.OFF}})
    assert completed.returncode == 0, (mode, completed.stderr[-6000:])
    return json.loads(completed.stdout.strip().splitlines()[-1])


def storage_snapshot(engine, receipt):
    """Every persisted evidence/source field, including the original bytea."""
    with engine.connect() as db:
        row = db.execute(text("SELECT to_jsonb(e) AS evidence, to_jsonb(r) AS resume "
            "FROM public.evidence_items e JOIN public.resumes r ON r.id=e.resume_id "
            "WHERE e.id=:id AND e.user_id=1 AND r.user_id=1"),
            {"id": receipt["evidence"]["id"]}).mappings().one()
        return dict(row)


@pytest.mark.parametrize("producer,consumer", [("prior", "candidate"), ("candidate", "prior")])
def test_actual_processes_read_imported_evidence_without_changing_exact_storage(
    v3_database, v3_roots, producer, consumer,
):
    identity = "app25-v3-evidence-" + uuid4().hex
    if producer == "candidate":
        upgrade26(v3_database)
    receipt = run_evidence(v3_roots[producer], v3_database, "seed", identity)
    if producer == "prior":
        upgrade26(v3_database)
    before = storage_snapshot(v3_database, receipt)
    assert run_evidence(v3_roots[consumer], v3_database, "read", identity, receipt) == {
        "status": "actual-source-evidence-read-observed"}
    assert storage_snapshot(v3_database, receipt) == before


@pytest.mark.parametrize("version", ["missing", "stale"])
@pytest.mark.parametrize("changes", [{"approval_state": "approved"}, {"evidence_text": "Stale legacy edit."}])
def test_candidate_refuses_legacy_missing_or_stale_versions_and_preserves_reviewed_storage(
    v3_database, v3_roots, version, changes,
):
    identity = "app25-v3-refusal-" + uuid4().hex
    receipt = run_evidence(v3_roots["prior"], v3_database, "seed", identity)
    upgrade26(v3_database)
    receipt = run_evidence(v3_roots["candidate"], v3_database, "review", identity, receipt)
    before = storage_snapshot(v3_database, receipt)
    payload = dict(changes)
    if version == "stale":
        payload["expected_fingerprint"] = receipt["shown_fingerprint"]
    refused = run_evidence(v3_roots["candidate"], v3_database, "patch", identity,
        {**receipt, "changes": payload})
    assert refused["status"] == 409
    assert refused["body"]["detail"]["code"] == "evidence_refresh_required"
    assert "snapshot_fingerprint" not in json.dumps(refused["body"])
    assert storage_snapshot(v3_database, receipt) == before
    assert run_evidence(v3_roots["prior"], v3_database, "read", identity, receipt) == {
        "status": "actual-source-evidence-read-observed"}
    context = run_evidence(v3_roots["candidate"], v3_database, "context", identity, receipt)
    assert context["status"] == "ready" and context["physical_edit_compatible"] is False
    assert "Developed clear documented records." in context["raw_text"]
    assert storage_snapshot(v3_database, receipt) == before


@pytest.mark.parametrize("legacy_action", ["approve_unseen_edit", "overwrite_reviewed_text"])
def test_fenced_historical_writer_cannot_mutate_newer_evidence_without_any_displayed_version(
    v3_database, v3_roots, legacy_action, record_property,
):
    record_property("mixed_version_writer_safety", "requires actual schema26 trigger and archived old writer")
    identity = "app25-v3-unsafe-" + uuid4().hex
    receipt = run_evidence(v3_roots["prior"], v3_database, "seed", identity)
    assert "snapshot_fingerprint" not in receipt["evidence"]
    upgrade26(v3_database)
    receipt = run_evidence(v3_roots["candidate"], v3_database, "review", identity, receipt)
    if legacy_action == "approve_unseen_edit":
        changed = run_evidence(v3_roots["candidate"], v3_database, "patch", identity,
            {**receipt, "changes": {"expected_fingerprint": receipt["evidence"]["snapshot_fingerprint"],
                "evidence_text": "New candidate fact the legacy reviewer never displayed."}})
        assert changed["status"] == 200 and changed["body"]["approval_state"] == "pending"
        receipt["evidence"] = changed["body"]
        changes = {"approval_state": "approved"}
    else:
        changes = {"evidence_text": "UNSAFE stale legacy overwrite of reviewed text."}
    before = storage_snapshot(v3_database, receipt)
    refused = run_evidence(v3_roots["prior"], v3_database, "patch", identity, {**receipt, "changes": changes})
    assert refused["status"] == 500  # Old API has no new typed database-fence handler.
    assert storage_snapshot(v3_database, receipt) == before
    assert run_evidence(v3_roots["candidate"], v3_database, "read", identity, receipt) == {
        "status": "actual-source-evidence-read-observed"}
    context = run_evidence(v3_roots["candidate"], v3_database, "context", identity, receipt)
    assert context["status"] == ("needs_review" if legacy_action == "approve_unseen_edit" else "ready")
