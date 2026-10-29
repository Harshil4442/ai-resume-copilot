"""Actual pinned prior↔candidate rehearsal on owned disposable PostgreSQL17.

External document/feed responses below are finite synthetic adapter fixtures.
They qualify wire/storage compatibility, never provider, model, or isolation
acceptance. The native release boundary retains those independent proofs.
"""
from __future__ import annotations

import importlib
import json
import os
import stat
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

import pytest
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.pool import NullPool

from alembic import command

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "infra/gcp"), str(ROOT / "backend")]
release = importlib.import_module("schema25_app_rolling_release")
contract = release.contract


@pytest.fixture
def app25_database():
    owned = os.getenv("HIREWIZ_APP25_TEST_ROOT")
    configured = os.getenv("HIREWIZ_TEST_POSTGRES_URL")
    directory = None
    if owned:
        directory = Path(owned)
        assert directory.is_absolute() and directory.resolve() == directory
        assert directory.name.startswith("hirewiz-app25-pg-")
        assert directory.stat().st_uid == os.getuid() and stat.S_IMODE(directory.stat().st_mode) == 0o700
        marker = json.loads((directory / "identity.json").read_text())
        assert marker["port"] not in {55433, 55434} and 1024 < marker["port"] < 65536
        assert Path(marker["data"]).resolve() == directory / "data"
        configured = f"postgresql+psycopg://app25_test@127.0.0.1:{marker['port']}/app25_test"
    if not configured:
        if os.getenv("HIREWIZ_APP_COMPATIBILITY_REQUIRED") == "true":
            pytest.fail("Required disposable schema25 compatibility database is unavailable")
        pytest.skip("Set the dedicated disposable schema25 compatibility database")
    url = make_url(configured)
    assert url.drivername == "postgresql+psycopg" and url.host == "127.0.0.1"
    if directory is None:
        assert url.port in {55433, 55434} and url.database == "hirewiz_admission_test"
    admin = create_engine(url, poolclass=NullPool, isolation_level="AUTOCOMMIT", hide_parameters=True)
    database = "app25_" + uuid4().hex
    engine = None
    try:
        with admin.connect() as db:
            assert int(db.scalar(text("SHOW server_version_num"))) // 10000 == 17
            if directory:
                assert Path(db.scalar(text("SHOW data_directory"))).resolve() == directory / "data"
            db.exec_driver_sql(f'CREATE DATABASE "{database}"')
        engine = create_engine(url.set(database=database), poolclass=NullPool, hide_parameters=True)
        cfg = Config(str(ROOT / "backend/alembic.ini"))
        cfg.set_main_option("script_location", str(ROOT / "backend/alembic"))
        with engine.begin() as db:
            cfg.attributes["connection"] = db
            command.upgrade(cfg, contract.schema25.SCHEMA)
        yield engine
    finally:
        if engine is not None:
            engine.dispose()
            with admin.connect() as db:
                # Only the unique database created by this fixture is removed.
                db.exec_driver_sql(f'DROP DATABASE "{database}"')
        admin.dispose()


@pytest.fixture
def app_roots(tmp_path):
    files = release.initial.prior_app_files(ROOT, contract.PRIOR_RELEASE)
    assert release.initial.prior_app_digest(files) == contract.PRIOR_APP_SHA256
    for name, raw in files.items():
        target = tmp_path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(raw)
    assert contract.source_fingerprint(ROOT, files)
    return {"prior": tmp_path / "backend", "candidate": ROOT / "backend"}


# Each subprocess imports a different *actual* application archive; no import
# aliases or current-code stubs replace the prior implementation.
SCRIPT = r'''
import hashlib, io, json, os, sys
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import text
from app.database import SessionLocal
from app import models
from app.domains.analysis import schemas, service, tasks
from app.domains.dispatch.models import DispatchOutbox
from app.domains.employer import models as employer, service as searches
from app.main import app
from app.security import create_access_token
from app.routers import resume as resumes
from app.services import llm_client
from google import genai
from reportlab.pdfgen.canvas import Canvas

mode, identity = sys.argv[1:3]
source = io.BytesIO()
pdf = Canvas(source, invariant=True)
pdf.drawString(50, 780, 'Synthetic Compatibility Candidate - Python engineer')
pdf.save()
raw = source.getvalue()
parsed = ('Synthetic Compatibility Candidate\nPython engineer',
          {'experience':'Python engineer'}, ['Python'], 2.0, {'name':'Synthetic Compatibility Candidate'})
def refuse_model(*args, **kwargs):
    raise AssertionError('No model provider is permitted during compatibility rehearsal')
genai.Client = refuse_model
llm_client._chat = refuse_model
resumes.inspect_resume_document = lambda *args, **kwargs: parsed
headers = {'Authorization':'Bearer '+create_access_token(subject='1', credential_epoch=9)}
foreign = {'Authorization':'Bearer '+create_access_token(subject='2', credential_epoch=3)}
if mode == 'produce':
    with SessionLocal() as db:
        if db.get(models.User,1) is None:
            db.add_all([models.User(id=1,email='owner@example.invalid',ai_credits=17,job_service_credits=13,credential_epoch=9),
                        models.User(id=2,email='foreign@example.invalid',ai_credits=0,job_service_credits=0,credential_epoch=3)])
            db.commit()
    with TestClient(app) as client:
        response = client.post('/api/resume/parse', headers=headers,
            files={'file':('synthetic.pdf',raw,'application/pdf')},data={'enrich_skills':'false'})
        assert response.status_code == 200, ('parse',response.status_code)
        resume_id = response.json()['resume_id']
    with SessionLocal() as db:
        run, created = service.create_run(db,user_id=1,
            payload=schemas.AnalysisRunCreate(operation='skill_roi',idempotency_key=identity),header_idempotency_key=identity)
        assert created and run.estimated_units == 0 and run.generation_attempt_limit == 0
        outbox = db.query(DispatchOutbox).filter_by(topic='analysis.run').one()
        assert outbox.payload == {'run_id':run.id}
        item = {'posting_id':'synthetic-official-1','title':'Python Engineer','company':'Synthetic Employer',
                'url':'https://careers.example.invalid/python-engineer','location':'Bengaluru'}
        search = employer.EmployerSearch(id=identity,user_id=1,resume_id=resume_id,idempotency_key=identity,
            input_fingerprint='a'*64,query={'titles':['Python Engineer']},status='completed',desired_count=1,
            delivered_count=1,reserved_credits=1,charged_credits=1,refunded_credits=0,items=[item],
            scope={'kind':'reviewed-employer-pilot','sources':['synthetic-official-feed']})
        db.add(search)
        db.commit()
        print(json.dumps({'run':run.id,'resume':resume_id,'source_sha256':hashlib.sha256(raw).hexdigest(),
            'search':identity,'outbox':outbox.id,'search_public':searches.get_search(db,1,identity)}, default=str))
elif mode == 'consume':
    receipt = json.loads(sys.argv[3])
    with SessionLocal() as db:
        outbox = db.get(DispatchOutbox,receipt['outbox'])
        assert outbox.topic == 'analysis.run' and outbox.payload == {'run_id':receipt['run']}
    assert tasks.process_analysis_run(receipt['run']) == 'succeeded'
    assert tasks.process_analysis_run(receipt['run']) == 'succeeded'
    with TestClient(app) as client:
        assert client.get('/api/health').json()['ok'] is True
        availability = client.get('/api/v1/credits/availability',headers=headers)
        assert availability.status_code == 200
        assert availability.json()['catalog_enabled'] is False
        assert availability.json()['execution_enabled'] is False
        assert availability.json()['supported_operations'] == []
        assert availability.json()['free_core_operations'] == []
        assert client.get('/api/public/credits/catalog').status_code == 404
        assert client.get('/api/resume/'+str(receipt['resume']),headers=headers).json()['skills'] == ['Python']
        assert client.get('/api/resume/'+str(receipt['resume']),headers=foreign).status_code == 404
        listed = client.get('/api/resume/list',headers=headers)
        assert listed.status_code == 200 and any(r['id']==receipt['resume'] for r in listed.json()['resumes'])
        downloaded = client.get('/api/resume/'+str(receipt['resume'])+'/source',headers=headers)
        assert downloaded.status_code == 200 and hashlib.sha256(downloaded.content).hexdigest() == receipt['source_sha256']
        assert client.get('/api/resume/'+str(receipt['resume'])+'/source',headers=foreign).status_code == 404
    with SessionLocal() as db:
        actual = searches.get_search(db,1,receipt['search'])
        assert json.loads(json.dumps(actual,default=str)) == receipt['search_public']
        try:
            searches.get_search(db,2,receipt['search'])
            raise AssertionError('foreign search admitted')
        except HTTPException as exc:
            assert exc.status_code == 404
        run = db.get(models.AnalysisRun,receipt['run'])
        assert run.status == 'succeeded' and run.attempt_count == 1 and run.result_payload is not None
        assert (db.get(models.User,1).ai_credits,db.get(models.User,1).job_service_credits)==(17,13)
        assert db.query(models.ModelCallEvent).count() == 0
        assert db.query(models.ModelCostLiability).count() == 0
        from app.services.generation_gate import check_generation_admission
        for operation,payload in [('resume_tailor',{}),('interview_questions',{'mode':'enhanced'}),
                                  ('job_match',{'mode':'enhanced'})]:
            try:
                check_generation_admission(operation,payload)
                raise AssertionError('AI admission opened')
            except HTTPException as exc:
                assert exc.status_code == 503
    from app.worker_main import app as worker
    from app.resume_upload_worker import app as coordinator
    for role in ('analysis-worker','employer-worker'):
        os.environ['WORKER_LABEL'] = role
        with TestClient(worker) as client:
            health = client.get('/api/health').json()
            assert health['ok'] is True and health['role'] == role
    os.environ['SERVICE_ROLE'] = 'resume-upload-coordinator'
    with TestClient(coordinator) as client:
        health = client.get('/api/health').json()
        assert health['ok'] is True and health['role'] == 'resume-upload-coordinator'
        assert client.post('/internal/resume-uploads/scan').status_code == 503
        assert client.post('/internal/resume-uploads/cleanup').status_code == 503
    print(json.dumps({'status':'actual-source-compatibility-observed'}))
else:
    raise AssertionError('unknown rehearsal phase')
'''


def run_app(root, engine, mode, identity, receipt=None):
    completed = subprocess.run([sys.executable, "-I", "-B", "-c",
        "import sys;sys.path.insert(0,sys.argv.pop(1));" + SCRIPT,
        str(root), mode, identity, *([json.dumps(receipt)] if receipt else [])],
        cwd=root, timeout=45, capture_output=True, text=True,
        env={"PATH": os.environ.get("PATH", ""), "APP_ENV": "test",
            "DATABASE_URL": engine.url.render_as_string(hide_password=False),
            "JWT_SECRET": "schema25-app-compatibility-synthetic-only", "RATE_LIMIT_STORAGE_URL": "memory://",
            "AUTO_DB_MIGRATE": "false", "OTEL_SDK_DISABLED": "true",
            **{name: "false" for name in release.initial.OFF}, "RESUME_DIRECT_UPLOAD_ENABLED": "true"})
    assert completed.returncode == 0, (mode, completed.stderr[-6000:])
    return json.loads(completed.stdout.strip().splitlines()[-1])


@pytest.mark.parametrize("producer,consumer", [("prior", "candidate"), ("candidate", "prior")])
def test_actual_sources_exchange_pilot_resume_search_outbox_without_ai_or_money_loss(
    app25_database, app_roots, producer, consumer, record_property,
):
    engine = app25_database
    identity = "compatibility-" + uuid4().hex
    record_property("prior_application_source", contract.PRIOR_RELEASE)
    record_property("postgres_major", 17)
    receipt = run_app(app_roots[producer], engine, "produce", identity)
    with engine.connect() as db:
        signature = contract.observe(db)
        liabilities = contract.liability_pins(db)
    assert run_app(app_roots[consumer], engine, "consume", identity, receipt) == {
        "status": "actual-source-compatibility-observed"}
    with engine.connect() as db:
        assert contract.observe(db) == signature
        contract.verify_liability_pins(db, liabilities)
        assert db.scalar(text("SELECT count(*) FROM public.credit_execution_holds")) == 0


def seed_unknown(db):
    db.execute(text("INSERT INTO public.model_cost_liabilities(id,financial_group_id,attempt_number,provider,model,endpoint_key,currency,pricing_quote,input_token_estimate,reserved_cost_micros,cost_state,created_at) VALUES('detached-liability','synthetic-group',1,'google','synthetic-model',:endpoint,'USD','{}',100,12345,'outcome_unknown',now())"),
        {"endpoint": "d" * 64})


@pytest.mark.parametrize("change", ["delete", "reserve", "quote", "new_liability"])
def test_unknown_obligation_identity_quote_and_reserve_cannot_be_reduced_or_adopted(app25_database, change):
    engine = app25_database
    with engine.begin() as db:
        seed_unknown(db)
        before = contract.observe(db)
        pins = contract.liability_pins(db)
        if change == "delete":
            db.execute(text("DELETE FROM public.model_cost_liabilities"))
        elif change == "reserve":
            db.execute(text("UPDATE public.model_cost_liabilities SET reserved_cost_micros=1"))
        elif change == "quote":
            db.execute(text("UPDATE public.model_cost_liabilities SET pricing_quote='{}',model='different-model'"))
        else:
            db.execute(text("INSERT INTO public.model_cost_liabilities SELECT 'new-liability','new-group',attempt_number,provider,model,endpoint_key,currency,pricing_quote,input_token_estimate,reserved_cost_micros,settled_cost_micros,input_tokens,output_tokens,usage_provenance,cost_state,created_at,settled_at FROM public.model_cost_liabilities"))
        # A catalog/invariant proof alone is insufficient to protect liabilities.
        assert contract.observe(db) == before
        with pytest.raises(contract.schema25.Schema25Denied, match="protected_liability_changed"):
            contract.verify_liability_pins(db, pins)


def test_legitimate_pilot_credit_change_is_not_a_frozen_full_financial_digest(app25_database):
    with app25_database.begin() as db:
        db.execute(text("INSERT INTO public.users(id,email,ai_credits,job_service_credits) VALUES(1,'synthetic@example.invalid',17,13)"))
        seed_unknown(db)
        before, pins = contract.observe(db), contract.liability_pins(db)
        db.execute(text("UPDATE public.users SET job_service_credits=12 WHERE id=1"))
        assert contract.observe(db) == before
        contract.verify_liability_pins(db, pins)
        row = db.execute(text("SELECT reserved_cost_micros,settled_cost_micros,cost_state FROM public.model_cost_liabilities")).one()
        assert tuple(row) == (12345, None, "outcome_unknown")


@pytest.mark.parametrize("activity", ["wallet", "ai_run", "active_model", "negative_legacy_balance", "schema_drift"])
def test_active_ai_or_new_wallet_or_financial_catalog_drift_is_refused(app25_database, activity):
    with app25_database.begin() as db:
        db.execute(text("INSERT INTO public.users(id,email,ai_credits,job_service_credits) VALUES(1,'synthetic@example.invalid',17,13)"))
        if activity == "wallet":
            db.execute(text("INSERT INTO public.credit_accounts(user_id,debt_credits,created_at) VALUES(1,0,now())"))
        elif activity in {"ai_run", "active_model"}:
            db.execute(text("INSERT INTO public.analysis_runs(id,user_id,operation,status,idempotency_key,input_fingerprint,input_payload,estimated_units,committed_units,usage_state,attempt_count,generation_attempt_limit,generation_attempt_count,model_cost_reserved_micros,model_cost_settled_micros,model_cost_state,cancel_requested,created_at,updated_at) VALUES('run',1,'job_match',:state,'synthetic-analysis-key',:sha,'{}',0,0,'pending',0,:limit,0,0,0,'unquoted',false,now(),now())"),
                {"state": "queued" if activity == "ai_run" else "failed", "sha": "a" * 64,
                 "limit": 1 if activity == "ai_run" else 0})
            if activity == "active_model":
                db.execute(text("INSERT INTO public.model_call_events(id,analysis_run_id,user_id,provider,model,prompt_version,input_tokens,output_tokens,tokens_estimated,latency_ms,cost_state,status,created_at) VALUES('call','run',1,'google','synthetic-model','synthetic-v1',0,0,true,0,'reserved','attempted',now())"))
        elif activity == "negative_legacy_balance":
            db.execute(text("UPDATE public.users SET ai_credits=-1 WHERE id=1"))
        else:
            db.execute(text("ALTER TABLE public.credit_accounts ADD COLUMN unexpected text"))
        with pytest.raises(contract.schema25.Schema25Denied):
            contract.observe(db)


def test_settled_label_without_measured_usage_cannot_release_unknown_obligation(app25_database):
    with app25_database.begin() as db:
        seed_unknown(db)
        pins = contract.liability_pins(db)
        db.execute(text("UPDATE public.model_cost_liabilities SET cost_state='settled',settled_cost_micros=0,settled_at=now()"))
        with pytest.raises(contract.schema25.Schema25Denied, match="settlement_invalid"):
            contract.verify_liability_pins(db, pins)


def test_valid_late_usage_settlement_preserves_identity_without_freezing_money(app25_database):
    quote = {"version": "synthetic-v1", "input_rate_micros_per_million": 1_000_000,
        "output_rate_micros_per_million": 1_000_000, "max_input_tokens": 1000,
        "max_output_tokens": 100, "output_limit_parameter": "max_output_tokens",
        "token_estimator": "utf8-json-bytes-framing-v1", "operation_policy_version": "synthetic-v1",
        "admission_policy_version": "synthetic-v1", "authorized_ceiling_micros": 1000}
    with app25_database.begin() as db:
        seed_unknown(db)
        db.execute(text("UPDATE public.model_cost_liabilities SET pricing_quote=CAST(:quote AS json),reserved_cost_micros=200"),
                   {"quote": json.dumps(quote)})
        pins, before = contract.liability_pins(db), contract.observe(db)
        db.execute(text("UPDATE public.model_cost_liabilities SET cost_state='settled',input_tokens=10,output_tokens=5,usage_provenance='google-total-including-thoughts-v1',settled_cost_micros=15,settled_at=now()"))
        contract.verify_liability_pins(db, pins)
        assert contract.observe(db) == before
