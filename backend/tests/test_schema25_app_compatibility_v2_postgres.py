"""Actual ab2/current app processes on owned PostgreSQL17; no provider calls."""
from __future__ import annotations

import json
import os
from uuid import uuid4

import pytest
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.pool import NullPool
from test_schema25_app_compatibility_postgres import ROOT, app25_database, run_app  # noqa: F401
from test_supported_evaluation_postgres import owned_postgres  # noqa: F401

from alembic import command
from scripts import schema25_app_release_contract_v2 as contract


@pytest.fixture
def v2_database(request):
    if os.getenv("HIREWIZ_TEST_POSTGRES_URL") or os.getenv("HIREWIZ_APP25_TEST_ROOT"):
        return request.getfixturevalue("app25_database")
    if os.getenv("HIREWIZ_APP_COMPATIBILITY_REQUIRED") == "true":
        pytest.fail("CI must provide its exact dedicated PostgreSQL17 database")
    owned = request.getfixturevalue("owned_postgres")
    url = make_url(owned["url"])
    assert url.host == "127.0.0.1" and url.port not in {55433, 55434}
    admin = create_engine(url, poolclass=NullPool, isolation_level="AUTOCOMMIT", hide_parameters=True)
    name = "app25_v2_" + uuid4().hex
    with admin.connect() as db:
        assert int(db.scalar(text("SHOW server_version_num"))) // 10000 == 17
        db.exec_driver_sql(f'CREATE DATABASE "{name}"')
    engine = create_engine(url.set(database=name), poolclass=NullPool, hide_parameters=True)
    config = Config(str(ROOT / "backend/alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "backend/alembic"))
    with engine.begin() as db:
        config.attributes["connection"] = db
        command.upgrade(config, contract.v1.schema25.SCHEMA)
    request.addfinalizer(lambda: dispose(engine, admin, name))
    return engine


def dispose(engine, admin, name):
    engine.dispose()
    with admin.connect() as db:
        db.exec_driver_sql(f'DROP DATABASE "{name}"')
    admin.dispose()


@pytest.fixture
def v2_roots(tmp_path):
    import sys
    sys.path.insert(0, str(ROOT / "infra/gcp"))
    import unified_credit_schema25_release as release
    files = release.prior_app_files(ROOT, contract.PRIOR_RELEASE)
    assert release.prior_app_digest(files) == contract.PRIOR_APP_SHA256
    for name, raw in files.items():
        target = tmp_path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(raw)
    assert contract.source_fingerprint(ROOT, files)
    return {"prior": tmp_path / "backend", "candidate": ROOT / "backend"}


@pytest.mark.parametrize("producer,consumer", [("prior", "candidate"), ("candidate", "prior")])
def test_actual_ab2_candidate_exchange_preserves_pilot_ownership_and_liabilities(v2_database, v2_roots, producer, consumer, record_property):
    record_property("actual_prior_source", contract.PRIOR_RELEASE)
    identity = "app25-v2-" + uuid4().hex
    receipt = run_app(v2_roots[producer], v2_database, "produce", identity)
    with v2_database.connect() as db:
        signature = contract.v1.observe(db)
        liabilities = contract.v1.liability_pins(db)
    assert run_app(v2_roots[consumer], v2_database, "consume", identity, receipt) == {
        "status": "actual-source-compatibility-observed"}
    with v2_database.connect() as db:
        assert contract.v1.observe(db) == signature
        contract.v1.verify_liability_pins(db, liabilities)
        assert db.scalar(text("SELECT count(*) FROM public.credit_execution_holds")) == 0


def test_exact_eight_response_additions_preserve_old_model_defaults(v2_roots):
    import subprocess
    import sys
    program = """import json,sys;from app.schemas import DimensionScore,JobMatchResponse
value=json.loads(sys.argv[1]);d=DimensionScore.model_validate(value['dimension']);r=JobMatchResponse.model_validate(value['match']);print(json.dumps({'dimension':d.model_dump(),'match':r.model_dump()}))"""
    value = {"dimension": {"name": "skills", "score": .5, "feedback": "Synthetic comparison"},
             "match": {"match_id": 1, "match_score": .5, "grade": "C", "required_skills": [],
                 "full_matches": [], "partial_matches": [], "true_gaps": [], "skill_verification_rate": 0,
                 "dimensions": [], "fit_summary": "Synthetic comparison", "improvement_tips": []}}
    observed = {}
    for side, root in v2_roots.items():
        result = subprocess.run([sys.executable, "-I", "-B", "-c", "import sys;sys.path.insert(0,sys.argv.pop(1));" + program,
            str(root), json.dumps(value)], cwd=root, capture_output=True, text=True, timeout=15, check=True)
        observed[side] = json.loads(result.stdout)
    for key, name in (("dimension", "DimensionScore"), ("match", "JobMatchResponse")):
        assert {k: v for k, v in observed["candidate"][key].items() if k not in contract.ADDITIONS[name]} == observed["prior"][key]

