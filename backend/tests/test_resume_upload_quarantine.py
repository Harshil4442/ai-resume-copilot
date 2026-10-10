"""Synthetic GCS/scanner only. PostgreSQL tests use a dedicated disposable schema.

These tests prove application admission/races, not native GCS/IAM/scanner deployment.
"""
from __future__ import annotations

import hashlib
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import timedelta
from threading import Barrier, Event, Lock
from types import SimpleNamespace

import pytest
from backend.app import models as core
from backend.app.database import get_db
from backend.app.domains.common import utcnow
from backend.app.domains.resume_uploads import privacy, scanner, schemas, service, storage, tasks
from backend.app.domains.resume_uploads.models import ResumeUpload, ResumeUploadCleanup
from backend.app.routers import resume_uploads as routes
from backend.app.security import get_current_user
from backend.tests import test_employer_admissions_postgres as pg_checks
from fastapi import Depends, FastAPI, HTTPException
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import inspect, text
from sqlalchemy.orm import sessionmaker

CONTENT = b"%PDF-synthetic-quarantine-test-no-real-document"
CONFIG = storage.StorageConfig("synthetic-project", "synthetic-quarantine", "synthetic-clean",
    "upload-signer@synthetic-project.iam.gserviceaccount.com", "read-signer@synthetic-project.iam.gserviceaccount.com")


class FakeStore:
    """Atomic generation0 writes/conditional retirement, not a GCS emulator."""
    config = CONFIG
    def __init__(self):
        self.objects = {}
        self.serial = 0
        self.lock = Lock()
        self.before_read = self.before_clean = self.after_clean = None
        self.signs = 0
        self.fail_retire = False

    def _write(self, bucket, name, content, media, *, create=False, retired=False):
        with self.lock:
            if create and (bucket, name) in self.objects:
                raise storage.StorageMismatch("synthetic_precondition_failed")
            self.serial += 1
            generation = str(self.serial)
            self.objects[bucket, name] = (content, storage.ObjectInfo(generation, len(content), media, retired=retired))
            return generation

    def upload(self, row, content=CONTENT):
        return self._write(row.quarantine_bucket, row.quarantine_name, content, row.media_type, create=True)

    def sign_upload(self, row, expires_at):
        self.signs += 1
        return "https://synthetic.invalid/upload", {"Content-Type": row.media_type, "x-goog-if-generation-match": "0",
                                                   "x-goog-content-length-range": f"{row.size_bytes},{row.size_bytes}"}

    def inspect_object(self, bucket, name):
        with self.lock:
            pair = self.objects.get((bucket, name))
            return pair[1] if pair else None

    def read(self, bucket, name, generation, size):
        if self.before_read:
            self.before_read()
        with self.lock:
            content, info = self.objects[bucket, name]
            if info.generation != generation or len(content) != size:
                raise storage.StorageMismatch("synthetic_generation_mismatch")
            return content

    def put_clean(self, row, content):
        if self.before_clean:
            self.before_clean()
        with self.lock:
            existing = self.objects.get((row.clean_bucket, row.clean_name))
            if existing:
                if existing[1].retired or existing[0] != content:
                    raise storage.StorageMismatch("synthetic_clean_mismatch")
                return existing[1].generation
        generation = self._write(row.clean_bucket, row.clean_name, content, row.media_type, create=True)
        if self.after_clean:
            self.after_clean()
        return generation

    def retire(self, bucket, name, upload_id):
        if self.fail_retire:
            raise storage.StorageUnavailable("synthetic_retirement_unavailable")
        with self.lock:
            existing = self.objects.get((bucket, name))
            if existing and existing[1].retired:
                return existing[1].generation
        return self._write(bucket, name, b"", "application/octet-stream", retired=True)

    def sign_read(self, row, expires_at):
        return "https://synthetic.invalid/clean?generation=" + row.clean_generation


@pytest.fixture
def pg_engine():
    yield from pg_checks.pg_engine.__wrapped__()


@pytest.fixture
def context(pg_engine, monkeypatch):
    pg_checks._migrate(pg_engine, "20261010_0014")
    factory = sessionmaker(bind=pg_engine, autoflush=False)
    with factory() as db:
        db.add_all([core.User(id=1, email="synthetic-owner@example.invalid", ai_credits=100),
                    core.User(id=2, email="synthetic-other@example.invalid", ai_credits=100)])
        db.commit()
    monkeypatch.setenv("RESUME_DIRECT_UPLOAD_ENABLED", "true")
    monkeypatch.setattr(scanner.document_ingestion, "worker_config", lambda: SimpleNamespace(image="synthetic-image", policy="synthetic-policy"))
    def inspect_document(content, *, source_format):
        return SimpleNamespace(parsed=("Synthetic approved resume", {"Summary": "Synthetic summary"}, ["Python"], 2.0, {}),
            sha256=hashlib.sha256(content).hexdigest(), size_bytes=len(content), source_format=source_format,
            worker_image="synthetic-image", policy_sha256="synthetic-policy", scan_engine="ClamAV",
            scan_version="synthetic-1.0", scan_definitions="synthetic-definition-set")
    monkeypatch.setattr(scanner.document_ingestion, "inspect_resume_document_with_receipt", inspect_document, raising=False)
    store = FakeStore()
    return factory, store


def admission(context, *, owner=1, key="synthetic-key", content=CONTENT, enrich=False, filename="resume.pdf"):
    factory, store = context
    with factory() as db:
        result = service.create(db, owner, schemas.UploadCreate(filename=filename, size_bytes=len(content),
            sha256=hashlib.sha256(content).hexdigest(), enrich_skills=enrich), key, store)
        row = db.get(ResumeUpload, result.upload_id)
        target = SimpleNamespace(**{column.name: getattr(row, column.name) for column in ResumeUpload.__table__.columns})
    return result, target


def queue(context, *, content=CONTENT, **kwargs):
    factory, store = context
    result, target = admission(context, content=content, **kwargs)
    store.upload(target, content)
    with factory() as db:
        assert service.complete(db, 1, result.upload_id).state == "queued"
    return result.upload_id, target


def assert_no_saved(context):
    with context[0]() as db:
        assert db.query(core.Resume).count() == 0
        assert db.get(core.User, 1).ai_credits == 100
        assert db.get(core.User, 1).job_service_credits == 0


def cancel(context, identifier):
    with context[0]() as db:
        service.cancel(db, 1, identifier)


def expire(context, identifier, *, lease=False):
    with context[0]() as db:
        row = db.get(ResumeUpload, identifier)
        if lease:
            row.lease_until = utcnow() - timedelta(seconds=1)
        else:
            row.expires_at = utcnow() - timedelta(seconds=1)
        db.commit()


def test_exact_five_mib_is_admitted_preserved_and_no_units_charged(context):
    content = CONTENT + b"x" * (schemas.MAX_BYTES - len(CONTENT))
    identifier, target = queue(context, content=content, enrich=True)
    assert tasks.process(context[0], identifier, context[1])
    with context[0]() as db:
        row = db.get(ResumeUpload, identifier)
        resume = db.get(core.Resume, row.result_resume_id)
        assert resume.source_document == content and row.scan_receipt["sha256"] == hashlib.sha256(content).hexdigest()
        assert row.scan_receipt["size_bytes"] == schemas.MAX_BYTES
        status = service.status(db, 1, identifier)
        assert status.resume.source_available and status.resume.enrichment_units == 0
        assert status.resume.enrichment_state == "unavailable"
        assert db.get(core.User, 1).ai_credits == 100
        source = service.source_access(db, 1, resume.id, context[1])
        assert source.size_bytes == schemas.MAX_BYTES and source.url.endswith(row.clean_generation)
    assert privacy.run_cleanup(context[0], identifier, context[1])
    assert context[1].inspect_object(target.quarantine_bucket, target.quarantine_name).retired
    assert not context[1].inspect_object(target.clean_bucket, target.clean_name).retired


def test_concurrent_idempotency_and_metadata_conflict(context):
    start = Barrier(2)
    def same(_):
        start.wait(timeout=5)
        return admission(context)[0].upload_id
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(same, range(2)))
    assert results[0] == results[1]
    with context[0]() as db:
        assert db.query(ResumeUpload).count() == 1
    with pytest.raises(HTTPException) as failure:
        admission(context, filename="changed.pdf")
    assert failure.value.status_code == 409


def test_concurrent_active_limit_is_not_oversold(context, monkeypatch):
    monkeypatch.setattr(service, "ACTIVE_LIMIT", 1)
    start = Barrier(2)
    def compete(index):
        start.wait(timeout=5)
        try:
            return admission(context, key=f"slot-{index}")[0].state
        except HTTPException as exc:
            return exc.status_code
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert set(pool.map(compete, range(2))) == {"awaiting_upload", 429}


def test_complete_and_worker_replay_save_exactly_one_resume(context, monkeypatch):
    identifier, _ = queue(context)
    with context[0]() as db:
        assert service.complete(db, 1, identifier).state == "queued"
    entered, release = Event(), Event()
    original = scanner.inspect
    def paused(*args, **kwargs):
        entered.set()
        assert release.wait(timeout=5)
        return original(*args, **kwargs)
    monkeypatch.setattr(scanner, "inspect", paused)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(tasks.process, context[0], identifier, context[1])
        assert entered.wait(timeout=5)
        assert not tasks.process(context[0], identifier, context[1])
        release.set()
        assert first.result(timeout=5)
    assert not tasks.process(context[0], identifier, context[1])
    with context[0]() as db:
        assert db.query(core.Resume).count() == 1
        assert service.complete(db, 1, identifier).state == "released"


@pytest.mark.parametrize("stage", ["read", "scan", "before_clean", "after_clean"])
def test_cancel_during_work_cannot_release(context, monkeypatch, stage):
    identifier, target = queue(context)
    if stage == "scan":
        original = scanner.inspect
        def cancelled(*args, **kwargs):
            cancel(context, identifier)
            return original(*args, **kwargs)
        monkeypatch.setattr(scanner, "inspect", cancelled)
    else:
        setattr(context[1], {"read": "before_read", "before_clean": "before_clean", "after_clean": "after_clean"}[stage],
                lambda: cancel(context, identifier))
    assert not tasks.process(context[0], identifier, context[1])
    assert_no_saved(context)
    assert privacy.run_cleanup(context[0], identifier, context[1])
    assert context[1].inspect_object(target.clean_bucket, target.clean_name).retired
    assert context[1].inspect_object(target.quarantine_bucket, target.quarantine_name).retired


def test_cleanup_closes_late_signed_upload_and_late_clean_write(context):
    result, target = admission(context)
    cancel(context, result.upload_id)
    assert privacy.run_cleanup(context[0], result.upload_id, context[1])
    with pytest.raises(storage.StorageMismatch):
        context[1].upload(target)
    with pytest.raises(storage.StorageMismatch):
        context[1].put_clean(target, CONTENT)
    assert_no_saved(context)


def test_cleanup_racing_before_clean_commit_blocks_payload_resurrection(context):
    identifier, target = queue(context)
    def closure():
        cancel(context, identifier)
        assert privacy.run_cleanup(context[0], identifier, context[1])
    context[1].before_clean = closure
    assert not tasks.process(context[0], identifier, context[1])
    assert context[1].inspect_object(target.clean_bucket, target.clean_name).retired
    assert_no_saved(context)


@pytest.mark.parametrize("stage", ["scan", "after_clean"])
def test_owner_deleted_during_work_revokes_lease_and_durable_cleanup(context, monkeypatch, stage):
    identifier, _ = queue(context)
    def delete():
        with context[0]() as db:
            db.execute(text("DELETE FROM users WHERE id=1"))
            db.commit()
    if stage == "scan":
        original = scanner.inspect
        def removed(*args, **kwargs):
            delete()
            return original(*args, **kwargs)
        monkeypatch.setattr(scanner, "inspect", removed)
    else:
        context[1].after_clean = delete
    assert not tasks.process(context[0], identifier, context[1])
    with context[0]() as db:
        row = db.get(ResumeUpload, identifier)
        assert row.user_id is None and row.original_filename is None and row.state == "cancelled"
        assert row.lease_token is None and row.scan_receipt is None
        assert db.query(core.Resume).count() == 0
        assert db.query(ResumeUploadCleanup).filter_by(upload_id=identifier).count() == 1
    assert privacy.run_cleanup(context[0], identifier, context[1])


def test_saved_resume_deleted_closes_clean_source_via_fk_trigger(context):
    identifier, target = queue(context)
    assert tasks.process(context[0], identifier, context[1])
    assert privacy.run_cleanup(context[0], identifier, context[1])
    with context[0]() as db:
        row = db.get(ResumeUpload, identifier)
        db.execute(text("DELETE FROM resumes WHERE id=:id"), {"id": row.result_resume_id})
        db.commit()
        db.refresh(row)
        assert row.state == "cancelled" and row.result_resume_id is None and row.original_filename is None
    assert privacy.run_cleanup(context[0], identifier, context[1])
    assert context[1].inspect_object(target.clean_bucket, target.clean_name).retired


def test_expiry_during_clean_write_cannot_release(context):
    identifier, _ = queue(context)
    context[1].after_clean = lambda: expire(context, identifier)
    assert not tasks.process(context[0], identifier, context[1])
    assert_no_saved(context)
    with context[0]() as db:
        privacy.sweep(db)
        assert db.get(ResumeUpload, identifier).state == "expired"
    assert privacy.run_cleanup(context[0], identifier, context[1])


def test_stale_lease_replay_only_new_owner_can_release(context, monkeypatch):
    identifier, _ = queue(context)
    original = scanner.inspect
    called = False
    def stolen(*args, **kwargs):
        nonlocal called
        if not called:
            called = True
            expire(context, identifier, lease=True)
            assert tasks.process(context[0], identifier, context[1])
        return original(*args, **kwargs)
    monkeypatch.setattr(scanner, "inspect", stolen)
    assert not tasks.process(context[0], identifier, context[1])
    with context[0]() as db:
        assert db.get(ResumeUpload, identifier).state == "released"
        assert db.query(core.Resume).count() == 1


@pytest.mark.parametrize("field,value", [("sha256", "0"*64), ("size_bytes", True), ("source_format", "docx"),
    ("worker_image", "changed"), ("policy_sha256", "changed"), ("scan_engine", "unknown"),
    ("scan_version", ""), ("scan_definitions", "x"*201), ("parsed", ("x", {}, [], float("nan"), {}))])
def test_unbound_scanner_result_never_saves_or_charges(context, monkeypatch, field, value):
    identifier, _ = queue(context)
    original = scanner.document_ingestion.inspect_resume_document_with_receipt
    def wrong(*args, **kwargs):
        result = original(*args, **kwargs)
        setattr(result, field, value)
        return result
    monkeypatch.setattr(scanner.document_ingestion, "inspect_resume_document_with_receipt", wrong)
    assert not tasks.process(context[0], identifier, context[1])
    assert_no_saved(context)
    with context[0]() as db:
        assert db.get(ResumeUpload, identifier).state == "queued"


@pytest.mark.parametrize("refused,expected", [(True, "rejected"), (False, "queued")])
def test_fixed_scanner_refusal_vs_unavailable(context, monkeypatch, refused, expected):
    identifier, _ = queue(context)
    def refusal(*args, **kwargs):
        raise scanner.document_ingestion.DocumentInspectionError(refused=refused)
    monkeypatch.setattr(scanner.document_ingestion, "inspect_resume_document_with_receipt", refusal)
    assert not tasks.process(context[0], identifier, context[1])
    assert_no_saved(context)
    with context[0]() as db:
        row = db.get(ResumeUpload, identifier)
        assert row.state == expected
        assert "synthetic" not in row.last_error_code


@pytest.mark.parametrize("change", ["hash", "size", "media", "encoding", "generation"])
def test_native_metadata_byte_binding_failure_never_saves(context, change):
    identifier, target = queue(context)
    key = target.quarantine_bucket, target.quarantine_name
    content, info = context[1].objects[key]
    if change == "hash":
        context[1].objects[key] = (b"x"*len(content), info)
    elif change == "size":
        context[1].objects[key] = (content, replace(info, size_bytes=info.size_bytes+1))
    elif change == "media":
        context[1].objects[key] = (content, replace(info, media_type="application/octet-stream"))
    elif change == "encoding":
        context[1].objects[key] = (content, replace(info, encoding="gzip"))
    else:
        context[1].before_read = lambda: context[1]._write(key[0], key[1], content, info.media_type)
    assert not tasks.process(context[0], identifier, context[1])
    assert_no_saved(context)


def test_abandoned_owner_deleted_before_upload_keeps_durable_work(context):
    result, target = admission(context)
    with context[0]() as db:
        db.execute(text("DELETE FROM users WHERE id=1"))
        db.commit()
        assert db.query(ResumeUploadCleanup).filter_by(upload_id=result.upload_id).count() == 1
    assert privacy.run_cleanup(context[0], result.upload_id, context[1])
    with pytest.raises(storage.StorageMismatch):
        context[1].upload(target)


def test_cleanup_provider_failure_keeps_retry_work(context):
    result, _ = admission(context)
    cancel(context, result.upload_id)
    context[1].fail_retire = True
    assert not privacy.run_cleanup(context[0], result.upload_id, context[1])
    with context[0]() as db:
        work = db.query(ResumeUploadCleanup).filter_by(upload_id=result.upload_id).one()
        assert work.payload_retired_at is None and work.last_error_code == "cleanup_unavailable"
        assert work.lease_token is None and work.next_attempt_at > utcnow()
        privacy.sweep(db)
        assert work.next_attempt_at > utcnow()
        work.next_attempt_at = utcnow()
        db.commit()
    context[1].fail_retire = False
    assert privacy.cleanup_due(context[0], context[1]) == 1


def test_expired_abandonment_has_bounded_sweep_and_closure(context):
    result, target = admission(context)
    expire(context, result.upload_id)
    with context[0]() as db:
        assert privacy.sweep(db) == 1
        assert db.get(ResumeUpload, result.upload_id).state == "expired"
    assert privacy.cleanup_due(context[0], context[1]) == 1
    assert context[1].inspect_object(target.quarantine_bucket, target.quarantine_name).retired
    with pytest.raises(ValueError):
        privacy.cleanup_due(context[0], context[1], limit=101)


def test_migration_populated_downgrade_refuses_and_empty_roundtrip(context, pg_engine):
    admission(context)
    with pytest.raises(RuntimeError, match="retained resume-upload cleanup obligations"):
        pg_checks._migrate(pg_engine, "20261009_0013", "downgrade")
    assert "resume_uploads" in inspect(pg_engine).get_table_names()
    with pg_engine.begin() as connection:
        connection.execute(text("DELETE FROM resume_uploads"))
        connection.execute(text("DELETE FROM resume_upload_cleanups"))
    pg_checks._migrate(pg_engine, "20261009_0013", "downgrade")
    assert "resume_uploads" not in inspect(pg_engine).get_table_names()
    pg_checks._migrate(pg_engine, "20261010_0014")
    assert "resume_uploads" in inspect(pg_engine).get_table_names()


def test_authenticated_routes_bound_control_body_and_owner(context):
    factory, store = context
    app = FastAPI()
    app.include_router(routes.router, prefix="/api")
    def database():
        with factory() as db:
            yield db
    def owner(db=Depends(get_db)):
        return db.get(core.User, 1)
    app.dependency_overrides[get_db] = database
    app.dependency_overrides[get_current_user] = owner
    app.dependency_overrides[routes.storage] = lambda: store
    body = {"filename": "resume.pdf", "size_bytes": len(CONTENT), "sha256": hashlib.sha256(CONTENT).hexdigest()}
    with TestClient(app) as client:
        result = client.post("/api/resume/uploads", json=body, headers={"Idempotency-Key": "route-key"})
        assert result.status_code == 200 and result.headers["cache-control"] == "private, no-store"
        identifier = result.json()["upload_id"]
        assert client.post(f"/api/resume/uploads/{identifier}/complete").json()["state"] == "queued"
        assert client.post("/api/resume/uploads", content=b"x"*4097).status_code == 413
        assert client.post("/api/resume/uploads", content=b"{}", headers={"Content-Encoding": "gzip"}).status_code == 400
        other = admission(context, owner=2, key="route-other")[0].upload_id
        assert client.get(f"/api/resume/uploads/{other}").status_code == 404
        assert client.delete(f"/api/resume/uploads/{other}").status_code == 404
        assert client.delete(f"/api/resume/uploads/{identifier}").json()["state"] == "cancelled"


def test_disabled_route_does_not_query_new_tables_or_sign(context, monkeypatch):
    monkeypatch.delenv("RESUME_DIRECT_UPLOAD_ENABLED")
    class NoSQL:
        def query(self, *args):
            raise AssertionError("disabled SQL")
    with pytest.raises(HTTPException) as exc:
        service.status(NoSQL(), 1, "rup_" + "a"*32)
    assert exc.value.status_code == 503
    assert context[1].signs == 0


@pytest.mark.parametrize("body", [{"size_bytes": schemas.MAX_BYTES+1}, {"size_bytes": True}, {"size_bytes": 0},
    {"sha256": "A"*64}, {"filename": "source.tex"}, {"filename": "bad\x00.pdf"}, {"owner_id": 2}])
def test_upload_metadata_strict_and_additional_formats_stay_closed(body):
    base = {"filename": "resume.pdf", "size_bytes": 100, "sha256": "a"*64}
    with pytest.raises(ValidationError):
        schemas.UploadCreate(**(base | body))


def test_upload_filename_is_reduced_to_basename():
    body = schemas.UploadCreate(filename="C:\\candidate\\resume.pdf", size_bytes=100, sha256="a"*64)
    assert body.filename == "resume.pdf"


def test_missing_validated_receipt_api_has_no_fallback(context, monkeypatch):
    identifier, _ = queue(context)
    monkeypatch.delattr(scanner.document_ingestion, "inspect_resume_document_with_receipt")
    assert not tasks.process(context[0], identifier, context[1])
    assert_no_saved(context)


def test_gcs_environment_requires_private_fixed_targets_and_two_signers(monkeypatch):
    for name,value in {"GOOGLE_CLOUD_PROJECT": CONFIG.project, "RESUME_UPLOAD_QUARANTINE_BUCKET": CONFIG.quarantine_bucket,
        "RESUME_UPLOAD_CLEAN_BUCKET": CONFIG.clean_bucket, "RESUME_UPLOAD_SIGNER": CONFIG.signer,
        "RESUME_UPLOAD_READ_SIGNER": CONFIG.read_signer}.items():
        monkeypatch.setenv(name, value)
    assert storage.StorageConfig.environment() == CONFIG
    monkeypatch.setenv("RESUME_UPLOAD_READ_SIGNER", CONFIG.signer)
    with pytest.raises(storage.StorageUnavailable):
        storage.StorageConfig.environment()
    monkeypatch.setenv("RESUME_UPLOAD_READ_SIGNER", CONFIG.read_signer)
    monkeypatch.setenv("STORAGE_EMULATOR_HOST", "http://synthetic.invalid")
    with pytest.raises(storage.StorageUnavailable):
        storage.StorageConfig.environment()


@pytest.mark.parametrize("generation", ["0", "-1", "01", "1.0", "18446744073709551616", None, True])
def test_generation_is_native_canonical_positive_uint64(generation):
    assert not storage.valid_generation(generation)


def test_signing_failure_leaves_durable_bounded_target_for_cleanup(context, monkeypatch):
    def unavailable(*args):
        raise storage.StorageUnavailable("synthetic_signer_denied")
    monkeypatch.setattr(context[1], "sign_upload", unavailable)
    with pytest.raises(HTTPException) as exc:
        admission(context)
    assert exc.value.status_code == 503 and exc.value.detail == {"code": "direct_upload_unavailable"}
    with context[0]() as db:
        row = db.query(ResumeUpload).one()
        assert row.state == "awaiting_upload" and row.source_sha256 == hashlib.sha256(CONTENT).hexdigest()
        row.expires_at = utcnow() - timedelta(seconds=1)
        db.commit()
        privacy.sweep(db)
    assert privacy.cleanup_due(context[0], context[1]) == 1
    assert_no_saved(context)


def test_missing_upload_stops_after_three_attempts_and_keeps_cleanup(context):
    result, _ = admission(context)
    with context[0]() as db:
        service.complete(db, 1, result.upload_id)
    for attempt in range(3):
        assert not tasks.process(context[0], result.upload_id, context[1])
        with context[0]() as db:
            row = db.get(ResumeUpload, result.upload_id)
            assert row.attempt_count == attempt+1
            row.next_attempt_at = utcnow()
            db.commit()
    with context[0]() as db:
        assert db.get(ResumeUpload, result.upload_id).state == "failed"
        assert db.query(ResumeUploadCleanup).count() == 1
    assert_no_saved(context)


def test_generation_pin_is_stable_on_retry(context, monkeypatch):
    identifier, target = queue(context)
    original = scanner.inspect
    def unavailable(*args, **kwargs):
        raise scanner.ScanUnavailable("synthetic_outage")
    monkeypatch.setattr(scanner, "inspect", unavailable)
    assert not tasks.process(context[0], identifier, context[1])
    with context[0]() as db:
        row = db.get(ResumeUpload, identifier)
        assert row.quarantine_generation == "1"
        row.next_attempt_at = utcnow()
        db.commit()
    context[1]._write(target.quarantine_bucket, target.quarantine_name, CONTENT, target.media_type)
    monkeypatch.setattr(scanner, "inspect", original)
    assert not tasks.process(context[0], identifier, context[1])
    with context[0]() as db:
        assert db.get(ResumeUpload, identifier).state == "rejected"
    assert_no_saved(context)


def test_native_adapter_signs_exact_write_and_pinned_read_scopes(context, monkeypatch):
    from contextlib import contextmanager
    _, target = admission(context)
    target.clean_generation = "123"
    calls = []
    class Blob:
        def generate_signed_url(self, **kwargs):
            calls.append(kwargs)
            return "https://synthetic.invalid/signed"
    class Bucket:
        def blob(self, name):
            calls.append(name)
            return Blob()
    class Client:
        def bucket(self, name):
            calls.append(name)
            return Bucket()
    @contextmanager
    def transport():
        yield Client(), object(), object()
    store = storage.GCSObjectStore(CONFIG)
    monkeypatch.setattr(store, "_transport", transport)
    expiry = utcnow() + timedelta(seconds=60)
    url, headers = store.sign_upload(target, expiry)
    write = calls[-1]
    assert url.startswith("https://synthetic.invalid") and headers["x-goog-if-generation-match"] == "0"
    assert write["method"] == "PUT" and write["expiration"] == expiry and write["version"] == "v4"
    assert write["content_type"] == target.media_type
    assert write["headers"]["x-goog-content-length-range"] == f"{len(CONTENT)},{len(CONTENT)}"
    assert write["credentials"].signer_email == CONFIG.signer
    store.sign_read(target, expiry)
    read = calls[-1]
    assert read["method"] == "GET" and read["query_parameters"] == {"generation": "123"}
    assert read["credentials"].signer_email == CONFIG.read_signer
    assert read["response_type"] == target.media_type


@pytest.mark.parametrize("bucket,name", [("other-bucket", "quarantine/rup_"+"a"*32+"/source"),
    (CONFIG.quarantine_bucket, "quarantine/rup_"+"a"*32+"/../source"),
    (CONFIG.clean_bucket, "quarantine/rup_"+"a"*32+"/source"),
    (CONFIG.quarantine_bucket, "quarantine/rup_"+"a"*32+"/source?x")])
def test_native_adapter_rejects_unfixed_target_before_auth(bucket, name):
    store = storage.GCSObjectStore(CONFIG)
    with pytest.raises(storage.StorageMismatch):
        store.inspect_object(bucket, name)


def test_bearer_auth_required_before_upload_capability(context):
    factory, store = context
    app = FastAPI()
    app.include_router(routes.router, prefix="/api")
    def database():
        with factory() as db:
            yield db
    app.dependency_overrides[get_db] = database
    app.dependency_overrides[routes.storage] = lambda: store
    with TestClient(app) as client:
        result = client.post("/api/resume/uploads", json={"filename": "resume.pdf", "size_bytes": len(CONTENT),
                            "sha256": hashlib.sha256(CONTENT).hexdigest()}, headers={"Idempotency-Key": "unauthenticated"})
    assert result.status_code == 401 and store.signs == 0
    with factory() as db:
        assert db.query(ResumeUpload).count() == 0


def test_validated_wrapper_keeps_private_parsed_content_out_of_repr(monkeypatch):
    monkeypatch.setattr(scanner.document_ingestion, "worker_config", lambda: SimpleNamespace(image="synthetic-image", policy="synthetic-policy"))
    result = SimpleNamespace(parsed=("private-raw-marker", {"Summary": "private-section-marker"},
        ["private-skill-marker"], 2.0, {"name": "private-contact-marker"}),
        sha256=hashlib.sha256(CONTENT).hexdigest(), size_bytes=len(CONTENT), source_format="pdf",
        worker_image="synthetic-image", policy_sha256="synthetic-policy", scan_engine="ClamAV",
        scan_version="synthetic-1.0", scan_definitions="synthetic-definition-set")
    verified = scanner.validate_receipt(result, CONTENT, "pdf")
    assert all(value not in repr(verified) for value in ("private-raw-marker", "private-section-marker", "private-skill-marker", "private-contact-marker"))


def test_deletion_during_quarantine_only_cleanup_keeps_clean_work_due(context, monkeypatch):
    factory, store = context
    identifier, target = queue(context)
    assert tasks.process(factory, identifier, store)
    original = store.retire
    deleted: list[bool] = []
    def during_retirement(bucket, name, upload_id):
        if bucket == target.quarantine_bucket and not deleted:
            with factory() as db:
                db.delete(db.query(core.Resume).one())
                db.commit()
            deleted.append(True)
        return original(bucket, name, upload_id)
    monkeypatch.setattr(store, "retire", during_retirement)
    assert privacy.run_cleanup(factory, identifier, store)
    with factory() as db:
        work = db.query(ResumeUploadCleanup).filter_by(upload_id=identifier).one()
        assert work.clean_closed_generation is None and work.next_attempt_at <= utcnow()
    assert privacy.cleanup_due(factory, store) == 1
    assert store.inspect_object(target.clean_bucket, target.clean_name).retired
