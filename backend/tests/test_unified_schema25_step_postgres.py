"""Native owner/ACL transaction rehearsal, isolated to local PG16/17.

The recorded server major is actual evidence, not a claim of PG17 qualification
when the only available local server is16. No external database is accepted.
"""

from __future__ import annotations

import hashlib
import importlib
import os
import sys
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from alembic.config import Config
from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError
from sqlalchemy.pool import NullPool

from alembic import command
from scripts import unified_credit_schema25_contract as contract
from scripts import unified_credit_schema25_step as step
from scripts.browser_schema19_step import MigrationIdentity

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "infra/gcp"))
reviewed = importlib.import_module("unified_credit_schema25_release")
PASSWORD = "isolated-schema25-test-only"
FLAGS = {name: "false" for name in contract.DISABLED_FLAGS}


@pytest.fixture
def owned25():
    configured = os.getenv("HIREWIZ_TEST_POSTGRES_URL")
    if not configured:
        pytest.skip("Set the dedicated local disposable PostgreSQL test URL")
    url = make_url(configured)
    if (
        url.drivername != "postgresql+psycopg"
        or url.host != "127.0.0.1"
        or url.port not in {55433, 55434}
        or url.database != "hirewiz_admission_test"
    ):
        pytest.fail("Schema25 rehearsal accepts only the local disposable test database")
    token = uuid4().hex[:20]
    names = {
        key: "c25_" + token + "_" + key for key in ("bootstrap", "migrator", "runtime", "observer")
    }
    namespace = "credit_step25_" + token
    engines, created = [], []
    admin = create_engine(url, poolclass=NullPool, hide_parameters=True)

    def scoped(actor=None):
        target = url.set(username=names[actor], password=PASSWORD) if actor else url
        engine = create_engine(
            target,
            poolclass=NullPool,
            hide_parameters=True,
            connect_args={
                "options": f"-csearch_path={namespace} -clock_timeout=4000 -cstatement_timeout=15000",
                "connect_timeout": 5,
            },
        )
        engines.append(engine)
        return engine

    try:
        with admin.begin() as db:
            major = int(db.scalar(text("SHOW server_version_num"))) // 10000
            assert major in {16, 17}
            for key, name in names.items():
                login = "NOLOGIN" if key == "migrator" else "LOGIN"
                db.exec_driver_sql(
                    f"CREATE ROLE \"{name}\" {login} NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS PASSWORD '{PASSWORD}'"
                )
                created.append(name)
            db.exec_driver_sql(
                f'GRANT "{names["migrator"]}" TO "{names["bootstrap"]}" WITH ADMIN FALSE, INHERIT FALSE, SET TRUE'
            )
            db.exec_driver_sql(f'CREATE SCHEMA "{namespace}" AUTHORIZATION "{names["bootstrap"]}"')
            for role in names.values():
                db.exec_driver_sql(f'GRANT USAGE ON SCHEMA "{namespace}" TO "{role}"')
            db.exec_driver_sql(f'GRANT CREATE ON SCHEMA "{namespace}" TO "{names["migrator"]}"')
            db.exec_driver_sql(
                f'GRANT EXECUTE ON FUNCTION pg_catalog.pg_control_system() TO "{names["bootstrap"]}"'
            )
        source = scoped()
        cfg = Config(str(ROOT / "backend/alembic.ini"))
        cfg.set_main_option("script_location", str(ROOT / "backend/alembic"))
        with source.begin() as db:
            db.exec_driver_sql(f'SET LOCAL ROLE "{names["migrator"]}"')
            cfg.attributes["connection"] = db
            command.upgrade(cfg, contract.BEFORE)
            db.exec_driver_sql(
                f'GRANT SELECT ON ALL TABLES IN SCHEMA "{namespace}" TO "{names["runtime"]}", "{names["observer"]}"'
            )
            db.exec_driver_sql(
                f'GRANT SELECT ON "{namespace}".alembic_version TO "{names["bootstrap"]}"'
            )
            tables = list(
                db.execute(
                    text(
                        "SELECT relname FROM pg_catalog.pg_class WHERE relnamespace=(SELECT oid FROM pg_catalog.pg_namespace WHERE nspname=:ns) AND relkind='r'"
                    ),
                    {"ns": namespace},
                ).scalars()
            )
            erasable = set(reviewed.release.ERASABLE_TABLES) | {
                "candidate_google_enrollment_challenges",
                "candidate_google_identities",
                "password_reset_tokens",
            }
            for table in tables:
                if table != "alembic_version":
                    db.exec_driver_sql(
                        f'GRANT INSERT ON "{namespace}"."{table}" TO "{names["runtime"]}"'
                    )
                if table not in {"alembic_version", "candidate_identity_history"}:
                    db.exec_driver_sql(
                        f'GRANT UPDATE ON "{namespace}"."{table}" TO "{names["runtime"]}"'
                    )
                if table in erasable:
                    db.exec_driver_sql(
                        f'GRANT DELETE ON "{namespace}"."{table}" TO "{names["runtime"]}"'
                    )
            db.exec_driver_sql(
                f'GRANT USAGE ON ALL SEQUENCES IN SCHEMA "{namespace}" TO "{names["runtime"]}"'
            )
            db.execute(
                text(
                    "INSERT INTO users(id,email,ai_credits,job_service_credits,credential_epoch) VALUES(42,'native-schema25@example.com',17,13,9)"
                )
            )
            db.execute(
                text(
                    "INSERT INTO password_reset_tokens(id,user_id,token_sha256,email_sha256,credential_sha256,legacy_epoch,status,created_at,expires_at) VALUES('synthetic-reset',42,:sha,:sha,:sha,9,'PENDING',now(),now()+interval '15 minutes')"
                ),
                {"sha": "a" * 64},
            )
            db.exec_driver_sql("RESET ROLE")
            db.exec_driver_sql(f'REVOKE CREATE ON SCHEMA "{namespace}" FROM "{names["migrator"]}"')
            actual = db.execute(
                text(
                    "SELECT d.oid,d.datdba,n.oid,n.nspowner,(SELECT system_identifier::text FROM pg_catalog.pg_control_system()) FROM pg_catalog.pg_database d JOIN pg_catalog.pg_namespace n ON n.nspname=:ns WHERE d.datname=current_database()"
                ),
                {"ns": namespace},
            ).one()
            oids = dict(
                db.execute(
                    text(
                        "SELECT rolname,oid FROM pg_catalog.pg_roles WHERE rolname=ANY(CAST(:names AS text[]))"
                    ),
                    {"names": list(names.values())},
                ).all()
            )
            identity = MigrationIdentity(
                hashlib.sha256(actual[4].encode()).hexdigest(),
                actual[0],
                actual[1],
                namespace,
                actual[2],
                actual[3],
                names["bootstrap"],
                oids[names["bootstrap"]],
                names["migrator"],
                oids[names["migrator"]],
                names["runtime"],
                oids[names["runtime"]],
                names["observer"],
                oids[names["observer"]],
            )
            before_acl = step.previous.schema_acl(db, identity)
            before_digest = step.retained_digest(db, identity.namespace_oid, namespace)
        yield SimpleNamespace(
            engine=source,
            bootstrap=scoped("bootstrap"),
            runtime=scoped("runtime"),
            observer=scoped("observer"),
            identity=identity,
            namespace=namespace,
            names=names,
            before_acl=before_acl,
            before_digest=before_digest,
            major=major,
        )
    finally:
        for engine in engines:
            engine.dispose()
        with admin.begin() as db:
            db.exec_driver_sql(f'DROP SCHEMA IF EXISTS "{namespace}" CASCADE')
            for name in reversed(created):
                db.exec_driver_sql(f'DROP OWNED BY "{name}"')
                db.exec_driver_sql(f'DROP ROLE "{name}"')
        admin.dispose()


def unchanged(owned):
    with owned.engine.connect() as db:
        contract.verify_before_shape(db, owned.identity.namespace_oid, owned.namespace)
        assert step.previous.schema_acl(db, owned.identity) == owned.before_acl
        assert (
            step.retained_digest(db, owned.identity.namespace_oid, owned.namespace)
            == owned.before_digest
        )


def test_native25_non_superuser_bootstrap_nologin_owner_and_exact_acl(owned25, record_property):
    owned = owned25
    record_property("postgres_major", owned.major)
    observations = []

    def observe(db, _cursor, statement, _parameters, _context, _many):
        if statement.lstrip().startswith("CREATE TABLE credit_"):
            observations.append(
                tuple(
                    db.execute(
                        text(
                            "SELECT current_user,session_user,current_setting('transaction_isolation'),(SELECT rolcanlogin FROM pg_catalog.pg_roles WHERE rolname=current_user)"
                        )
                    ).one()
                )
            )

    event.listen(owned.bootstrap, "before_cursor_execute", observe)
    try:
        with owned.bootstrap.begin() as db:
            result = step.upgrade_transaction(db, owned.identity, root=ROOT, flags=FLAGS)
    finally:
        event.remove(owned.bootstrap, "before_cursor_execute", observe)
    assert result["schema"] == contract.SCHEMA
    assert result["retained_sha256"] == owned.before_digest
    assert result["migration_login_reenabled"] is result["replay_authorized"] is False
    assert observations and all(
        row == (owned.names["migrator"], owned.names["bootstrap"], "serializable", False)
        for row in observations
    )
    with owned.observer.connect() as db, db.begin():
        db.exec_driver_sql("SET TRANSACTION READ ONLY")
        db.exec_driver_sql("SET LOCAL search_path=pg_catalog")
        contract.verify_shape(db, owned.identity.namespace_oid, owned.namespace)
        step.verify_new_permissions(db, owned.identity)
        contract.verify_empty_new_tables(db, owned.namespace)
    with owned.engine.connect() as db:
        assert step.previous.schema_acl(db, owned.identity) == owned.before_acl
        assert (
            step.retained_digest(db, owned.identity.namespace_oid, owned.namespace)
            == owned.before_digest
        )
        reviewed.verify_permissions(
            db,
            owned.namespace,
            owned.names["runtime"],
            owned.names["migrator"],
            owned.names["observer"],
        )
        reviewed.previous.contract.verify_reset_permissions(
            db,
            owned.namespace,
            owned.names["runtime"],
            owned.names["migrator"],
            owned.names["observer"],
        )
    for engine, sql in (
        (owned.observer, "DELETE FROM credit_accounts"),
        (owned.runtime, "TRUNCATE credit_accounts"),
        (owned.runtime, "DELETE FROM candidate_identity_history"),
    ):
        with pytest.raises(DBAPIError), engine.begin() as db:
            db.exec_driver_sql(sql)


def test_native25_ddl_failure_rolls_back_temporary_authority_and_old_rows(owned25):
    owned = owned25

    def fault(_db, _cursor, statement, parameters, _context, _many):
        if statement.lstrip().startswith("CREATE TABLE credit_accounts"):
            return "CREATE TABLE broken_credit_test (id missing_owned_type)", parameters
        return statement, parameters

    event.listen(owned.bootstrap, "before_cursor_execute", fault, retval=True)
    try:
        with pytest.raises(DBAPIError), owned.bootstrap.begin() as db:
            step.upgrade_transaction(db, owned.identity, root=ROOT, flags=FLAGS)
    finally:
        event.remove(owned.bootstrap, "before_cursor_execute", fault)
    unchanged(owned)


def test_native25_wrong_identity_and_outer_rollback_never_commit(owned25):
    owned = owned25
    with pytest.raises(step.previous.previous.previous.Step19Denied), owned.bootstrap.begin() as db:
        step.upgrade_transaction(
            db, replace(owned.identity, cluster_sha256="a" * 64), root=ROOT, flags=FLAGS
        )
    unchanged(owned)
    with owned.bootstrap.connect() as db:
        transaction = db.begin()
        step.upgrade_transaction(db, owned.identity, root=ROOT, flags=FLAGS)
        transaction.rollback()
    unchanged(owned)


@pytest.mark.parametrize(
    "source_release, app_digest",
    [
        (reviewed.ROLLBACK_SOURCE_RELEASE, reviewed.ROLLBACK_APP_SHA256),
        (
            reviewed.ROLLBACK_WORKER_SOURCE_RELEASE,
            reviewed.ROLLBACK_WORKER_APP_SHA256,
        ),
        pytest.param(
            reviewed.ROLLBACK_API_HOTFIX_SOURCE_RELEASE,
            reviewed.ROLLBACK_API_HOTFIX_APP_SHA256,
            id="api-admission-hotfix",
        ),
    ],
)
def test_rehearsed_prior_app_and_worker_still_serve_old_accounts_on25(
    owned25, tmp_path, record_property, source_release, app_digest
):
    """Execute the actual pinned old application, not a current-code substitute."""
    import subprocess

    owned = owned25
    record_property("postgres_major", owned.major)
    record_property("prior_app_release", source_release)
    files = reviewed.prior_app_files(ROOT, source_release)
    assert reviewed.prior_app_digest(files) == app_digest
    for path, raw in files.items():
        target = tmp_path / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(raw)
    with owned.bootstrap.begin() as db:
        step.upgrade_transaction(db, owned.identity, root=ROOT, flags=FLAGS)
    runtime_url = owned.runtime.url.update_query_dict(
        {"options": "-csearch_path=" + owned.namespace}
    )
    script = """
import os
from fastapi.testclient import TestClient
from app.main import app
from app.worker_main import app as worker
from app.resume_upload_worker import app as coordinator
from app.domains.resume_uploads.models import ResumeUpload, ResumeUploadCleanup
from app.database import SessionLocal
from app.models import User
from app.security import create_access_token
with TestClient(app) as client:
    assert client.get('/api/health').json()['ok'] is True
    token = create_access_token(subject='42', credential_epoch=9)
    response = client.get('/api/auth/me', headers={'Authorization':'Bearer '+token})
    assert response.status_code == 200, response.status_code
    body = response.json()
    assert body['ai_credits'] == 17 and body['job_service_credits'] == 13
for role in ('analysis-worker','employer-worker'):
    os.environ['WORKER_LABEL'] = role
    with TestClient(worker) as client:
        state = client.get('/api/health').json()
        assert state['ok'] is True and state['role'] == role
os.environ['SERVICE_ROLE'] = 'resume-upload-coordinator'
os.environ['RESUME_UPLOAD_COORDINATOR_ENABLED'] = 'false'
os.environ['RESUME_UPLOAD_COORDINATOR_SCAN_ENABLED'] = 'false'
with TestClient(coordinator) as client:
    state = client.get('/api/health').json()
    assert state == {'ok':True,'role':'resume-upload-coordinator','scan_enabled':False,'cleanup_enabled':False}
    assert client.post('/internal/resume-uploads/scan').status_code == 503
    assert client.post('/internal/resume-uploads/cleanup').status_code == 503
with SessionLocal() as db:
    assert db.query(ResumeUpload).count() == 0
    assert db.query(ResumeUploadCleanup).count() == 0
    user = db.get(User,42)
    assert user.credential_epoch == 9
    user.credential_epoch = 10
    db.flush()
    db.rollback()
    assert db.get(User,42).credential_epoch == 9
print('prior-app-and-worker-schema25-compatible')
"""
    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=tmp_path / "backend",
        timeout=30,
        capture_output=True,
        text=True,
        env={
            "PATH": os.environ.get("PATH", ""),
            "APP_ENV": "test",
            "PYTHONPATH": str(tmp_path / "backend"),
            "DATABASE_URL": runtime_url.render_as_string(hide_password=False),
            "JWT_SECRET": "schema25-local-rollback-test-not-for-production",
            "RATE_LIMIT_STORAGE_URL": "memory://",
            "AUTO_DB_MIGRATE": "false",
            **{name: "false" for name in reviewed.OFF},
        },
    )
    assert completed.returncode == 0, completed.stderr[-4000:]
    assert "prior-app-and-worker-schema25-compatible" in completed.stdout
    with owned.engine.connect() as db:
        assert (
            step.retained_digest(db, owned.identity.namespace_oid, owned.namespace)
            == owned.before_digest
        )
        contract.verify_empty_new_tables(db, owned.namespace)
