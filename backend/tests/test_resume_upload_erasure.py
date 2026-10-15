"""Account-erasure minimization; real disposable SQL and synthetic object store."""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import replace
from types import SimpleNamespace
from uuid import uuid4

import pytest
from backend.app.domains.common import utcnow
from backend.app.domains.resume_uploads import privacy, storage, tasks
from backend.app.domains.resume_uploads.models import ResumeUpload, ResumeUploadCleanup
from backend.tests import test_resume_upload_quarantine as existing
from sqlalchemy import create_engine, event, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker


@pytest.fixture
def pg_engine():
    yield from existing.pg_engine.__wrapped__()


@pytest.fixture
def context(pg_engine, monkeypatch):
    return existing.context.__wrapped__(pg_engine, monkeypatch)


def orphan(context):
    result, target = existing.admission(context)
    with context[0]() as db:
        db.execute(text('DELETE FROM users WHERE id=1'))
        db.commit()
    return result.upload_id, target


def assert_pending(context, identifier):
    with context[0]() as db:
        row = db.get(ResumeUpload, identifier)
        work = db.query(ResumeUploadCleanup).filter_by(upload_id=identifier).one()
        assert row is not None and row.user_id is None
        assert work.last_error_code == 'cleanup_unavailable' and work.payload_retired_at is None
        assert work.lease_token is None and work.next_attempt_at > utcnow()


def retry(context, identifier):
    with context[0]() as db:
        row = db.query(ResumeUploadCleanup).filter_by(upload_id=identifier).one()
        row.next_attempt_at, row.lease_until = utcnow(), None
        db.commit()
    return privacy.run_cleanup(context[0], identifier, context[1])


def assert_purged(context, identifier, target):
    with context[0]() as db:
        assert db.get(ResumeUpload, identifier) is None
        assert db.query(ResumeUploadCleanup).filter_by(upload_id=identifier).first() is None
    for bucket, name in ((target.quarantine_bucket, target.quarantine_name),
                         (target.clean_bucket, target.clean_name)):
        content, info = context[1].objects[bucket, name]
        assert content == b'' and info.retired and info.retired_upload_id == identifier
    with pytest.raises(storage.StorageMismatch):
        context[1].upload(target)
    with pytest.raises(storage.StorageMismatch):
        context[1].put_clean(target, existing.CONTENT)


def test_account_erasure_purges_fingerprints_only_after_both_fresh_markers(context, monkeypatch):
    identifier, target = orphan(context)
    inspected = []
    inspect = context[1].inspect_object

    def observed(bucket, name):
        inspected.append((bucket, name))
        return inspect(bucket, name)

    monkeypatch.setattr(context[1], 'inspect_object', observed)
    assert privacy.run_cleanup(context[0], identifier, context[1])
    assert inspected == [(target.quarantine_bucket, target.quarantine_name),
                         (target.clean_bucket, target.clean_name)]
    assert_purged(context, identifier, target)
    assert not privacy.run_cleanup(context[0], identifier, context[1])
    assert privacy.cleanup_due(context[0], context[1]) == 0


def test_one_marker_only_retains_target_and_fingerprints_for_retry(context, monkeypatch):
    identifier, target = orphan(context)
    retire = context[1].retire

    def partial(bucket, name, upload_id):
        if bucket == target.clean_bucket:
            raise storage.StorageUnavailable('synthetic_clean_unknown')
        return retire(bucket, name, upload_id)

    monkeypatch.setattr(context[1], 'retire', partial)
    assert not privacy.run_cleanup(context[0], identifier, context[1])
    assert_pending(context, identifier)
    assert context[1].inspect_object(target.quarantine_bucket, target.quarantine_name).retired
    assert context[1].inspect_object(target.clean_bucket, target.clean_name) is None
    monkeypatch.setattr(context[1], 'retire', retire)
    assert retry(context, identifier)
    assert_purged(context, identifier, target)


@pytest.mark.parametrize('target_kind', ['quarantine', 'clean'])
@pytest.mark.parametrize('fault', ['missing', 'generation', 'retired', 'upload_id', 'metadata', 'size', 'media', 'encoding'])
def test_invalid_fresh_closure_metadata_never_purges(context, monkeypatch, target_kind, fault):
    identifier, target = orphan(context)
    inspect = context[1].inspect_object
    selected = (getattr(target, target_kind + '_bucket'), getattr(target, target_kind + '_name'))

    def bad(bucket, name):
        info = inspect(bucket, name)
        if (bucket, name) != selected:
            return info
        if fault == 'missing':
            return None
        fields = {'generation': {'generation': str(int(info.generation) + 1)},
                  'retired': {'retired': False}, 'upload_id': {'retired_upload_id': 'rup_' + 'a' * 32},
                  'metadata': {'retired_metadata_only': False},
                  'size': {'size_bytes': 1}, 'media': {'media_type': 'application/pdf'},
                  'encoding': {'encoding': 'gzip'}}
        return replace(info, **fields[fault])

    monkeypatch.setattr(context[1], 'inspect_object', bad)
    assert not privacy.run_cleanup(context[0], identifier, context[1])
    assert_pending(context, identifier)
    monkeypatch.setattr(context[1], 'inspect_object', inspect)
    assert retry(context, identifier)
    assert_purged(context, identifier, target)


@pytest.mark.parametrize('after_commit', [False, True])
def test_purge_commit_failure_keeps_retry_safe_and_markers_live(context, pg_engine, after_commit):
    identifier, target = orphan(context)
    failed = []

    class FaultSession(Session):
        def commit(self):
            if not failed and any(isinstance(row, ResumeUpload) for row in self.deleted):
                failed.append(True)
                if after_commit:
                    super().commit()
                raise SQLAlchemyError('synthetic_purge_commit_unknown')
            return super().commit()

    factory = sessionmaker(bind=pg_engine, class_=FaultSession, autoflush=False)
    with pytest.raises(SQLAlchemyError, match='synthetic_purge_commit_unknown'):
        privacy.run_cleanup(factory, identifier, context[1])
    assert failed
    if not after_commit:
        with context[0]() as db:
            assert db.get(ResumeUpload, identifier) is not None
            work = db.query(ResumeUploadCleanup).filter_by(upload_id=identifier).one()
            assert work.lease_token is not None and work.lease_until > utcnow()
        assert retry(context, identifier)
    else:
        assert not privacy.run_cleanup(context[0], identifier, context[1])
    assert_purged(context, identifier, target)


def test_linked_cancelled_upload_keeps_quota_record_and_fingerprints(context):
    result, target = existing.admission(context)
    existing.cancel(context, result.upload_id)
    assert privacy.run_cleanup(context[0], result.upload_id, context[1])
    with context[0]() as db:
        row = db.get(ResumeUpload, result.upload_id)
        assert row.user_id == 1 and row.source_sha256 == target.source_sha256
        assert db.query(ResumeUpload).filter_by(user_id=1).count() == 1
        assert db.query(ResumeUploadCleanup).filter_by(upload_id=result.upload_id).count() == 1


def test_resume_only_deletion_keeps_existing_owner_quota_semantics(context):
    identifier, _ = existing.queue(context)
    assert tasks.process(context[0], identifier, context[1])
    with context[0]() as db:
        row = db.get(ResumeUpload, identifier)
        db.execute(text('DELETE FROM resumes WHERE id=:id'), {'id': row.result_resume_id})
        db.commit()
    assert privacy.run_cleanup(context[0], identifier, context[1])
    with context[0]() as db:
        row = db.get(ResumeUpload, identifier)
        assert row.user_id == 1 and row.result_resume_id is None and row.state == 'cancelled'
        assert db.query(ResumeUpload).filter_by(user_id=1).count() == 1


def test_account_deleted_during_cleanup_rearms_fresh_orphan_proof(context, monkeypatch):
    result, target = existing.admission(context)
    existing.cancel(context, result.upload_id)
    retire = context[1].retire
    deleted = []

    def during(bucket, name, upload_id):
        generation = retire(bucket, name, upload_id)
        if not deleted:
            deleted.append(True)
            with context[0]() as db:
                db.execute(text('DELETE FROM users WHERE id=1'))
                db.commit()
        return generation

    monkeypatch.setattr(context[1], 'retire', during)
    assert privacy.run_cleanup(context[0], result.upload_id, context[1])
    with context[0]() as db:
        assert db.get(ResumeUpload, result.upload_id).user_id is None
        assert db.query(ResumeUploadCleanup).filter_by(upload_id=result.upload_id).one().next_attempt_at <= utcnow()
    assert privacy.run_cleanup(context[0], result.upload_id, context[1])
    assert_purged(context, result.upload_id, target)


def test_actual_role_needs_only_both_upload_table_delete_grants(context, pg_engine):
    identifier, target = orphan(context)
    role = 'upload_cleanup_' + uuid4().hex
    with pg_engine.begin() as connection:
        schema = connection.scalar(text('SELECT current_schema()'))
        connection.execute(text(f'CREATE ROLE "{role}" NOLOGIN'))
        connection.execute(text(f'GRANT USAGE ON SCHEMA "{schema}" TO "{role}"'))
        for table in ('resume_uploads', 'resume_upload_cleanups'):
            connection.execute(text(f'GRANT SELECT,INSERT,UPDATE ON "{schema}".{table} TO "{role}"'))
        connection.execute(text(f'GRANT DELETE ON "{schema}".resume_upload_cleanups TO "{role}"'))
    restricted = create_engine(pg_engine.url, connect_args={'options': f'-csearch_path={schema}'})

    @event.listens_for(restricted, 'begin')
    def activate(connection):
        connection.exec_driver_sql(f'SET LOCAL ROLE "{role}"')

    factory = sessionmaker(bind=restricted, autoflush=False)
    try:
        with pytest.raises(SQLAlchemyError):
            privacy.run_cleanup(factory, identifier, context[1])
        with context[0]() as db:
            assert db.get(ResumeUpload, identifier) is not None
            work = db.query(ResumeUploadCleanup).filter_by(upload_id=identifier).one()
            work.next_attempt_at, work.lease_until = utcnow(), None
            db.commit()
        with pg_engine.begin() as connection:
            connection.execute(text(f'GRANT DELETE ON "{schema}".resume_uploads TO "{role}"'))
            for table in ('users', 'resumes', 'payment_orders', 'entitlement_ledger'):
                assert connection.scalar(text("SELECT has_table_privilege(:role,:table,'DELETE')"),
                                         {'role': role, 'table': f'{schema}.{table}'}) is False
        assert privacy.run_cleanup(factory, identifier, context[1])
        assert_purged(context, identifier, target)
    finally:
        restricted.dispose()
        with pg_engine.begin() as connection:
            connection.execute(text(f'DROP OWNED BY "{role}"'))
            connection.execute(text(f'DROP ROLE "{role}"'))


@pytest.mark.parametrize('fault', [None, 'extra_metadata', 'filename_disposition'])
def test_native_metadata_projection_rejects_retained_sensitive_extras(monkeypatch, fault):
    identifier = 'rup_' + 'a' * 32
    metadata = {'resume-retired': '1', 'resume-upload-id': identifier}
    if fault == 'extra_metadata':
        metadata['sha256'] = 'b' * 64

    class Blob:
        generation = 123
        size = 0
        content_type = 'application/octet-stream'
        content_encoding = None
        content_disposition = 'attachment; filename="private.pdf"' if fault == 'filename_disposition' else None

        def reload(self, **kwargs):
            assert kwargs == {'timeout': (5, 15), 'retry': None}

    blob = Blob()
    blob.metadata = metadata
    client = SimpleNamespace(bucket=lambda bucket: SimpleNamespace(blob=lambda name: blob))
    store = storage.GCSObjectStore(existing.CONFIG)

    @contextmanager
    def transport():
        yield client, None, None

    monkeypatch.setattr(store, '_transport', transport)
    info = store.inspect_object(existing.CONFIG.quarantine_bucket, f'quarantine/{identifier}/source')
    assert info.retired and info.retired_upload_id == identifier
    assert info.retired_metadata_only is (fault is None)
