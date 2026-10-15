"""Real disposable PostgreSQL coordinator invocation; synthetic GCS/inspection only."""
from __future__ import annotations

from datetime import timedelta

import pytest
from backend.app.domains.common import utcnow
from backend.app.domains.resume_uploads import scanner
from backend.app.domains.resume_uploads.models import ResumeUpload, ResumeUploadCleanup
from backend.app.services import resume_upload_coordinator as coordinator
from backend.tests import test_resume_upload_quarantine as domain


@pytest.fixture
def pg_engine():
    yield from domain.pg_engine.__wrapped__()


@pytest.fixture
def context(pg_engine, monkeypatch):
    monkeypatch.setenv('SERVICE_ROLE', 'resume-upload-coordinator')
    monkeypatch.setenv('RESUME_UPLOAD_COORDINATOR_ENABLED', 'true')
    monkeypatch.setenv('RESUME_UPLOAD_COORDINATOR_SCAN_ENABLED', 'true')
    return domain.context.__wrapped__(pg_engine, monkeypatch)


def run(context, mode):
    return coordinator._run_domain(context[0], context[1], mode)


def test_real_invocation_releases_once_and_cleanup_preserves_live_source(context):
    identifier, target = domain.queue(context)
    assert run(context, 'scan') == coordinator.Outcome('scan', 1, 0, 0)
    assert run(context, 'scan') == coordinator.Outcome('scan', 0, 0, 0)
    with context[0]() as db:
        assert db.query(domain.core.Resume).count() == 1
        assert db.get(domain.core.User, 1).ai_credits == 100
        assert db.get(ResumeUpload, identifier).attempt_count == 1
    result = run(context, 'cleanup')
    assert result.swept == 1 and result.cleanup_completed == 1
    assert context[1].inspect_object(target.quarantine_bucket, target.quarantine_name).retired
    assert not context[1].inspect_object(target.clean_bucket, target.clean_name).retired


def test_scan_batch_processes_one_and_later_tick_processes_second(context):
    first, _ = domain.queue(context, key='one')
    second, _ = domain.queue(context, key='two')
    assert run(context, 'scan').scan_completed == 1
    with context[0]() as db:
        assert {db.get(ResumeUpload, i).state for i in (first, second)} == {'released', 'queued'}
    assert run(context, 'scan').scan_completed == 1
    with context[0]() as db:
        assert db.query(domain.core.Resume).count() == 2


def test_permanent_refusal_schedules_exact_cleanup_and_saves_no_resume(context, monkeypatch):
    identifier, target = domain.queue(context)
    def refused(*a, **k):
        raise scanner.ScanRefused('synthetic-not-a-provider-output')
    monkeypatch.setattr(scanner, 'inspect', refused)
    assert run(context, 'scan').scan_completed == 0
    domain.assert_no_saved(context)
    with context[0]() as db:
        assert db.get(ResumeUpload, identifier).state == 'rejected'
        assert db.query(ResumeUploadCleanup).count() == 1
    assert run(context, 'cleanup').cleanup_completed == 1
    assert context[1].inspect_object(target.quarantine_bucket, target.quarantine_name).retired
    assert context[1].inspect_object(target.clean_bucket, target.clean_name).retired


def test_unavailable_scan_backoff_survives_ticks_and_exhaustion(context, monkeypatch):
    identifier, _ = domain.queue(context)
    def unavailable(*a, **k):
        raise scanner.ScanUnavailable('synthetic-unavailable')
    monkeypatch.setattr(scanner, 'inspect', unavailable)
    assert run(context, 'scan').scan_completed == 0
    assert run(context, 'scan').scan_completed == 0
    with context[0]() as db:
        row = db.get(ResumeUpload, identifier)
        assert row.attempt_count == 1 and row.state == 'queued' and row.next_attempt_at > utcnow()
    for expected in (2, 3):
        with context[0]() as db:
            db.get(ResumeUpload, identifier).next_attempt_at = utcnow() - timedelta(seconds=1)
            db.commit()
        assert run(context, 'scan').scan_completed == 0
        with context[0]() as db:
            assert db.get(ResumeUpload, identifier).attempt_count == expected
    with context[0]() as db:
        assert db.get(ResumeUpload, identifier).state == 'failed'
    assert run(context, 'cleanup').cleanup_completed == 1


def test_cleanup_retains_retry_delay_then_reconciles_without_scan(context, monkeypatch):
    identifier, _ = domain.queue(context)
    domain.cancel(context, identifier)
    context[1].fail_retire = True
    assert run(context, 'cleanup').cleanup_completed == 0
    with context[0]() as db:
        work = db.query(ResumeUploadCleanup).one()
        assert work.attempt_count == 1 and work.next_attempt_at > utcnow()
        assert work.last_error_code == 'cleanup_unavailable' and work.lease_until is None
    assert run(context, 'cleanup').cleanup_completed == 0
    with context[0]() as db:
        assert db.query(ResumeUploadCleanup).one().attempt_count == 1
        db.query(ResumeUploadCleanup).one().next_attempt_at = utcnow() - timedelta(seconds=1)
        db.commit()
    context[1].fail_retire = False
    monkeypatch.setenv('RESUME_DIRECT_UPLOAD_ENABLED', 'false')
    monkeypatch.setenv('RESUME_UPLOAD_COORDINATOR_SCAN_ENABLED', 'false')
    assert run(context, 'cleanup').cleanup_completed == 1
    with context[0]() as db:
        assert db.query(ResumeUploadCleanup).one().last_error_code is None


def test_abandoned_expired_upload_is_swept_and_both_capabilities_closed(context):
    result, target = domain.admission(context)
    domain.expire(context, result.upload_id)
    outcome = run(context, 'cleanup')
    assert outcome.swept == 1 and outcome.cleanup_completed == 1
    with context[0]() as db:
        assert db.get(ResumeUpload, result.upload_id).state == 'expired'
    assert context[1].inspect_object(target.quarantine_bucket, target.quarantine_name).retired
    assert context[1].inspect_object(target.clean_bucket, target.clean_name).retired


def test_interrupted_scan_lease_is_not_retried_until_expiry(context):
    identifier, _ = domain.queue(context)
    original = context[1].inspect_object
    def broken(*a, **k):
        raise RuntimeError('synthetic-unknown-interruption')
    context[1].inspect_object = broken
    with pytest.raises(RuntimeError):
        run(context, 'scan')
    context[1].inspect_object = original
    with context[0]() as db:
        row = db.get(ResumeUpload, identifier)
        assert row.state == 'inspecting' and row.attempt_count == 1 and row.lease_until > utcnow()
    assert run(context, 'scan').scan_completed == 0
    domain.expire(context, identifier, lease=True)
    assert run(context, 'scan').scan_completed == 1
    with context[0]() as db:
        assert db.get(ResumeUpload, identifier).attempt_count == 2
        assert db.query(domain.core.Resume).count() == 1


def test_entirely_disabled_domain_invocation_has_no_sql_or_storage(monkeypatch):
    monkeypatch.delenv('RESUME_UPLOAD_COORDINATOR_ENABLED', raising=False)
    with pytest.raises(coordinator.CoordinatorUnavailable):
        coordinator._run_domain(lambda: pytest.fail('SQL resource'), object(), 'cleanup')
