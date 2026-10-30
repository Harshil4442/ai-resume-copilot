"""Actual schema26 bridge/drain schedules on CI's owned PostgreSQL17 only.

No database/service is discovered or started. Archived e23 HTTP handlers below
are the real old writer, paused at SQLAlchemy boundaries rather than replaced.
Native serving/source/role and replication-bypass proofs remain separate gates.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager

import pytest
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session
from test_schema25_app_compatibility_postgres import ROOT, app25_database, release  # noqa: F401

from alembic import command
from app import models
from app.domains.career import schemas, service
from app.domains.career.evidence_fence_definition import FUNCTION_BODY
from app.domains.career.evidence_write_fence import (
    EvidenceWriteFenceDenied,
    evidence_write_scope,
    flush_evidence_writes,
)

PRIOR = "e23c940e6133323c3c1ff11e646a4ca9b2b0b165"
PRIOR_SHA256 = "fa3b778e5fa1b9fe900dc5625e18682a0f0987b68259bb8eed8ff3ae5f40800a"
SETTING = "hirewiz.evidence_writer_version"


@pytest.fixture
def pg25(request):
    if not os.getenv("HIREWIZ_TEST_POSTGRES_URL"):
        pytest.fail("Schema26 bridge requires its exact dedicated PostgreSQL17 URL")
    if os.getenv("HIREWIZ_APP25_TEST_ROOT"):
        pytest.fail("Schema26 bridge must not override its dedicated PostgreSQL17 URL")
    engine = request.getfixturevalue("app25_database")
    with engine.begin() as db:
        db.execute(text("INSERT INTO users(id,email,ai_credits,job_service_credits,credential_epoch) "
            "VALUES (1,'bridge-owner@example.invalid',17,13,9),(2,'bridge-other@example.invalid',0,0,3)"))
        db.execute(text("INSERT INTO resumes(id,user_id,raw_text,source_document,source_format,sections,skills) "
            "VALUES (1,1,'Exact synthetic source',:source,'pdf','{}','[]')"),
            {"source": b"Synthetic owned source bytes; no parser or worker receipt"})
        db.execute(text("INSERT INTO evidence_items(id,user_id,resume_id,category,title,evidence_text,metrics,skills,"
            "provenance,source_ref,approval_state,confidence,created_at,updated_at) VALUES "
            "('evd_bridge',1,1,'experience','Synthetic fact','Original shown fact.','{}','[]',"
            "'user','synthetic:bridge','pending','user_provided',now(),now()),"
            "('evd_unrelated',2,NULL,'projects','Unrelated fact','Unrelated owner fact.','{}','[]',"
            "'user','synthetic:unrelated','approved','user_provided',now(),now())"))
    return engine


def upgrade26(engine, started=None):
    config = Config(str(ROOT / "backend/alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "backend/alembic"))
    with engine.begin() as db:
        if started is not None:
            started.append(db.scalar(text("SELECT pg_backend_pid()")))
        config.attributes["connection"] = db
        command.upgrade(config, "20261031_0026")


@pytest.fixture
def fenced(pg25):
    upgrade26(pg25)
    return pg25


def setting(db):
    return db.scalar(text("SELECT current_setting(:setting,true)"), {"setting": SETTING})


def snapshot(engine):
    with engine.connect() as db:
        return {
            table: db.execute(text(f"SELECT to_jsonb(t) FROM public.{table} t ORDER BY id")).scalars().all()
            for table in ("users", "resumes", "evidence_items")
        }


def test_required_bridge_never_skips_or_starts_a_service_without_its_url(monkeypatch):
    monkeypatch.setenv("HIREWIZ_APP_COMPATIBILITY_REQUIRED", "true")
    monkeypatch.delenv("HIREWIZ_TEST_POSTGRES_URL", raising=False)

    class NoFallback:
        def getfixturevalue(self, name):
            raise AssertionError("No fallback is permitted")

    with pytest.raises(pytest.fail.Exception, match="exact dedicated PostgreSQL17 URL"):
        pg25.__wrapped__(NoFallback())


def test_catalog_and_installation_preserve_every_owned_customer_row(pg25):
    before = snapshot(pg25)
    upgrade26(pg25)
    assert snapshot(pg25) == before
    with pg25.connect() as db:
        assert db.scalar(text("SELECT version_num FROM alembic_version")) == "20261031_0026"
        trigger = db.execute(text("SELECT t.tgtype,t.tgenabled,p.prosecdef,p.proconfig,p.prosrc,"
            "pg_get_triggerdef(t.oid) AS definition FROM pg_trigger t JOIN pg_proc p ON p.oid=t.tgfoid "
            "WHERE t.tgrelid='public.evidence_items'::regclass AND t.tgname='evidence_writer_fence'"
            " AND NOT t.tgisinternal")).mappings().one()
        assert trigger["tgtype"] == 30 and trigger["tgenabled"] == "A"
        assert trigger["prosecdef"] is False and trigger["proconfig"] == ["search_path=pg_catalog"]
        assert trigger["prosrc"] == FUNCTION_BODY
        assert "FOR EACH STATEMENT" in trigger["definition"]


@pytest.mark.parametrize("sql", [
    "UPDATE evidence_items SET approval_state='approved' WHERE id='evd_bridge'",
    "DELETE FROM evidence_items WHERE id='evd_bridge'",
    "INSERT INTO evidence_items SELECT * FROM evidence_items WHERE false",
    "UPDATE evidence_items SET title='No matching row' WHERE false",
    "DELETE FROM evidence_items WHERE false",
])
def test_unmarked_dml_including_empty_statements_is_denied_without_data_change(fenced, sql):
    before = snapshot(fenced)
    with fenced.connect() as db:
        with pytest.raises(DBAPIError) as error:
            db.execute(text(sql))
        assert error.value.orig.sqlstate == "42501"
        assert error.value.orig.diag.message_primary == "evidence_writer_version_required"
        db.rollback()
    assert snapshot(fenced) == before


def test_exact_candidate_flush_cannot_arm_a_later_unrelated_pending_item(fenced):
    before = snapshot(fenced)
    with Session(fenced, autoflush=False) as db:
        selected = db.get(models.EvidenceItem, "evd_bridge")
        unrelated = db.get(models.EvidenceItem, "evd_unrelated")
        selected.evidence_text = "An explicitly validated selected write."
        unrelated.evidence_text = "An unintended later commit flush."
        flush_evidence_writes(db, [selected])
        assert setting(db) in (None, "")  # Cleared before COMMIT, not merely on pool return.
        with pytest.raises(DBAPIError) as error:
            db.commit()
        assert error.value.orig.sqlstate == "42501"
        db.rollback()
    assert snapshot(fenced) == before


def test_actual_candidate_create_update_delete_adapters_use_only_temporary_marker(fenced):
    before = snapshot(fenced)
    with Session(fenced, autoflush=False) as db:
        created = service.create_evidence(db, 1, schemas.EvidenceCreate(
            resume_id=1, category="projects", title="New synthetic fact", evidence_text="A new owned fact."))
        identifier = created.id
        assert setting(db) in (None, "")
        changed = service.update_evidence(db, 1, identifier, schemas.EvidenceUpdate(
            expected_fingerprint=schemas.evidence_snapshot_fingerprint(created),
            evidence_text="Updated after exact current review."))
        assert changed.evidence_text == "Updated after exact current review."
        assert setting(db) in (None, "")
        service.delete_evidence(db, 1, identifier,
            expected_fingerprint=schemas.evidence_snapshot_fingerprint(changed))
        assert setting(db) in (None, "") and db.get(models.EvidenceItem, identifier) is None
    assert snapshot(fenced) == before


def test_scope_commit_rollback_and_reused_connection_never_leak_marker(fenced):
    pooled = create_engine(fenced.url, pool_size=1, max_overflow=0, hide_parameters=True)
    try:
        with Session(pooled, autoflush=False) as db:
            pid = db.scalar(text("SELECT pg_backend_pid()"))
            with evidence_write_scope(db):
                assert setting(db) == "cas_v1"
                db.execute(text("UPDATE evidence_items SET evidence_text='Committed marked fact.' WHERE id='evd_bridge'"))
            assert setting(db) in (None, "")
            db.commit()
        committed = snapshot(fenced)
        with Session(pooled, autoflush=False) as db:
            assert db.scalar(text("SELECT pg_backend_pid()")) == pid
            assert setting(db) in (None, "")
            with pytest.raises(ValueError, match="synthetic abort"):
                with evidence_write_scope(db):
                    db.execute(text("UPDATE evidence_items SET evidence_text='Must roll back.' WHERE id='evd_bridge'"))
                    raise ValueError("synthetic abort")
            assert not db.in_transaction()
        with Session(pooled, autoflush=False) as db:
            assert db.scalar(text("SELECT pg_backend_pid()")) == pid
            assert setting(db) in (None, "")
            with pytest.raises(DBAPIError) as error:
                db.execute(text("UPDATE evidence_items SET approval_state='approved' WHERE id='evd_bridge'"))
            assert error.value.orig.sqlstate == "42501"
            db.rollback()
        assert snapshot(fenced) == committed
    finally:
        pooled.dispose()


@pytest.mark.parametrize("preexisting", ["cas_v1", "unqualified_v0"])
def test_preexisting_and_nested_scopes_refuse_and_rollback(fenced, preexisting):
    before = snapshot(fenced)
    with Session(fenced, autoflush=False) as db:
        db.execute(text("SELECT set_config(:name,:value,true)"), {"name": SETTING, "value": preexisting})
        with pytest.raises(EvidenceWriteFenceDenied, match="scope_already_active"):
            with evidence_write_scope(db):
                raise AssertionError("Inherited marker must not enter a writer")
        assert not db.in_transaction()
        assert setting(db) in (None, "")
        db.rollback()
        with pytest.raises(EvidenceWriteFenceDenied, match="scope_already_active"):
            with evidence_write_scope(db):
                with evidence_write_scope(db):
                    raise AssertionError("Nested scope must not enter a writer")
        assert not db.in_transaction()
        assert setting(db) in (None, "")
    assert snapshot(fenced) == before


def test_failed_sql_rolls_back_and_preserves_original_error_and_no_marker(fenced):
    before = snapshot(fenced)
    with Session(fenced, autoflush=False) as db:
        with pytest.raises(DBAPIError) as error:
            with evidence_write_scope(db):
                db.execute(text("UPDATE evidence_items SET evidence_text='Rolled back before failure.' WHERE id='evd_bridge'"))
                db.execute(text("INSERT INTO evidence_items SELECT * FROM evidence_items WHERE id='evd_bridge'"))
        assert error.value.orig.sqlstate == "23505"
        assert not db.in_transaction()
        assert setting(db) in (None, "")
    assert snapshot(fenced) == before


@pytest.fixture
def old_root(tmp_path):
    files = release.initial.prior_app_files(ROOT, PRIOR)
    assert release.initial.prior_app_digest(files) == PRIOR_SHA256
    for name, raw in files.items():
        target = tmp_path / "prior" / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(raw)
    return tmp_path / "prior/backend"


OLD_WRITER = r'''
import json,sys,time
from pathlib import Path
from fastapi.testclient import TestClient
from sqlalchemy import event
from sqlalchemy.orm import Session
from app import models
from app.database import engine
from app.main import app
from app.security import create_access_token

mode,ready_path,release_path=sys.argv[1:4]
ready,release=Path(ready_path),Path(release_path)
states=[]
paused=False
def pause():
    global paused
    if paused:
        return
    paused=True
    ready.write_text('actual-old-handler-paused')
    deadline=time.monotonic()+15
    while not release.exists():
        assert time.monotonic()<deadline,'old-handler barrier timed out'
        time.sleep(.01)
def before_flush(session,*args):
    if any(isinstance(row,models.EvidenceItem) for row in session.dirty):
        pause()
def before_sql(connection,cursor,statement,parameters,context,executemany):
    normalized=' '.join(statement.lower().split())
    if normalized.startswith('select ') and 'from evidence_items' in normalized:
        # Authentication and the career feature guard have already completed;
        # this is the actual old service's first evidence read, not auth SQL.
        pause()
def failed(context):
    states.append(getattr(context.original_exception,'sqlstate',None))
event.listen(engine,'handle_error',failed)
if mode=='late_old':
    event.listen(Session,'before_flush',before_flush)
elif mode=='pre_sql':
    event.listen(engine,'before_cursor_execute',before_sql)
else:
    raise AssertionError('unknown old-writer barrier')
headers={'Authorization':'Bearer '+create_access_token(subject='1',credential_epoch=9)}
with TestClient(app,raise_server_exceptions=False) as client:
    result=client.patch('/api/v1/evidence-items/evd_bridge',headers=headers,
        json={'evidence_text':'Old admitted unversioned write.','approval_state':'approved'})
assert paused
print(json.dumps({'status':result.status_code,'sqlstates':states}))
'''


@contextmanager
def paused_old_writer(root, engine, tmp_path, mode):
    ready, release_path = tmp_path / "old.ready", tmp_path / "old.release"
    process = subprocess.Popen([sys.executable, "-I", "-B", "-c",
        "import sys;sys.path.insert(0,sys.argv.pop(1));" + OLD_WRITER,
        str(root), mode, str(ready), str(release_path)], cwd=root,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        env={"PATH": os.environ.get("PATH", ""), "APP_ENV": "test",
            "DATABASE_URL": engine.url.render_as_string(hide_password=False),
            "JWT_SECRET": "schema26-old-writer-synthetic-only-secret",
            "RATE_LIMIT_STORAGE_URL": "memory://", "AUTO_DB_MIGRATE": "false",
            "OTEL_SDK_DISABLED": "true", "PYTHON_DOTENV_DISABLED": "1",
            **{name: "false" for name in release.initial.OFF}})
    try:
        deadline = time.monotonic() + 10
        while not ready.exists():
            assert process.poll() is None, "Archived old handler exited before its barrier"
            assert time.monotonic() < deadline, "Archived old handler did not reach its barrier"
            time.sleep(.01)
        yield process, release_path
    finally:
        release_path.write_text("release-owned-old-handler")
        try:
            stdout, stderr = process.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.communicate(timeout=5)
            raise AssertionError("Owned old-handler process did not finish") from None
        assert process.returncode == 0, stderr[-3000:]
        process.observed = json.loads(stdout.strip().splitlines()[-1])


def wait_for_install_lock(engine, installer_pids):
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        if installer_pids:
            with engine.connect() as db:
                waiting = db.scalar(text("SELECT count(*) FROM pg_locks WHERE pid=:pid "
                    "AND relation='public.evidence_items'::regclass AND mode='AccessExclusiveLock' AND NOT granted"),
                    {"pid": installer_pids[0]})
                if waiting == 1:
                    return
        time.sleep(.01)
    raise AssertionError("Installer never waited for the actual old evidence transaction")


def test_contested_install_times_out_atomically_without_partial_fence(pg25, old_root, tmp_path):
    before = snapshot(pg25)
    with paused_old_writer(old_root, pg25, tmp_path, "late_old") as (old, release_old):
        with pytest.raises(DBAPIError) as error:
            upgrade26(pg25)
        assert error.value.orig.sqlstate == "55P03"
        with pg25.connect() as db:
            assert db.scalar(text("SELECT version_num FROM alembic_version")) == "20261028_0025"
            assert db.scalar(text("SELECT count(*) FROM pg_trigger WHERE tgrelid='public.evidence_items'::regclass "
                "AND tgname='evidence_writer_fence'")) == 0
            assert db.scalar(text("SELECT to_regprocedure('public.evidence_writer_fence()')")) is None
        assert snapshot(pg25) == before
        release_old.write_text("old-commit-after-refused-install")
    assert old.observed == {"status": 200, "sqlstates": []}


def test_install_waits_for_real_old_commit_before_new_cas_admission(pg25, old_root, tmp_path):
    before = snapshot(pg25)
    with Session(pg25, autoflush=False) as db:
        stale = schemas.evidence_snapshot_fingerprint(db.get(models.EvidenceItem, "evd_bridge"))
    installer_pids = []
    with ThreadPoolExecutor(max_workers=1) as pool:
        with paused_old_writer(old_root, pg25, tmp_path, "late_old") as (old, release_old):
            installation = pool.submit(upgrade26, pg25, installer_pids)
            wait_for_install_lock(pg25, installer_pids)
            assert not installation.done()
            release_old.write_text("drain-old-before-fence-commit")
        assert old.observed == {"status": 200, "sqlstates": []}
        installation.result(timeout=10)
    with Session(pg25, autoflush=False) as db:
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as error:
            service.update_evidence(db, 1, "evd_bridge", schemas.EvidenceUpdate(
                expected_fingerprint=stale, approval_state="approved"))
        assert error.value.status_code == 409
        row = db.get(models.EvidenceItem, "evd_bridge")
        fresh = schemas.evidence_snapshot_fingerprint(row)
        service.update_evidence(db, 1, row.id, schemas.EvidenceUpdate(
            expected_fingerprint=fresh, evidence_text="Fresh candidate review after old transaction drained."))
    after = snapshot(pg25)
    assert after["users"] == before["users"] and after["resumes"] == before["resumes"]
    assert after["evidence_items"][1] == before["evidence_items"][1]


def test_already_admitted_pre_sql_old_handler_cannot_write_after_committed_fence(pg25, old_root, tmp_path):
    with paused_old_writer(old_root, pg25, tmp_path, "pre_sql") as (old, release_old):
        upgrade26(pg25)
        with Session(pg25, autoflush=False) as db:
            row = db.get(models.EvidenceItem, "evd_bridge")
            service.update_evidence(db, 1, row.id, schemas.EvidenceUpdate(
                expected_fingerprint=schemas.evidence_snapshot_fingerprint(row),
                evidence_text="Fresh reviewed candidate fact.", approval_state="approved"))
        reviewed = snapshot(pg25)
        release_old.write_text("resume-admitted-old-handler-after-fence")
    assert old.observed["status"] == 500 and "42501" in old.observed["sqlstates"]
    assert snapshot(pg25) == reviewed
