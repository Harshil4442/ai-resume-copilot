"""Coordinator authentication and owned-process bounds; no native callers/providers."""
from __future__ import annotations

import os
import ssl
import sys

import pytest
from backend.app import resume_upload_worker as worker
from backend.app.services import resume_upload_coordinator as coordinator
from fastapi.testclient import TestClient

AUDIENCE = 'https://hirewiz-resume-coordinator-synthetic.run.app'
EMAIL = 'hirewiz-resume-scheduler@synthetic-project.iam.gserviceaccount.com'
SUBJECT = '123456789012345678901'
TOKEN = 'synthetic.valid.signature'


@pytest.fixture
def configured(monkeypatch):
    monkeypatch.setenv('SERVICE_ROLE', 'resume-upload-coordinator')
    monkeypatch.setenv('GOOGLE_CLOUD_PROJECT', 'synthetic-project')
    monkeypatch.setenv('RESUME_UPLOAD_COORDINATOR_AUDIENCE', AUDIENCE)
    monkeypatch.setenv('RESUME_UPLOAD_COORDINATOR_CALLER_EMAIL', EMAIL)
    monkeypatch.setenv('RESUME_UPLOAD_COORDINATOR_CALLER_SUBJECT', SUBJECT)
    monkeypatch.setenv('RESUME_UPLOAD_COORDINATOR_ENABLED', 'true')
    monkeypatch.setenv('RESUME_UPLOAD_COORDINATOR_SCAN_ENABLED', 'true')
    monkeypatch.setenv('RESUME_DIRECT_UPLOAD_ENABLED', 'true')
    monkeypatch.setattr(worker, '_verified_claims', lambda token, audience: {
        'iss': 'https://accounts.google.com', 'aud': AUDIENCE,
        'email': EMAIL, 'email_verified': True, 'sub': SUBJECT,
    })
    return TestClient(worker.app)


def auth():
    return {'Authorization': 'Bearer ' + TOKEN}


def test_default_disabled_never_verifies_or_starts_resources(monkeypatch):
    monkeypatch.delenv('RESUME_UPLOAD_COORDINATOR_ENABLED', raising=False)
    monkeypatch.setattr(worker, '_verified_claims', lambda *a: pytest.fail('auth request'))
    monkeypatch.setattr(worker, 'execute', lambda *a: pytest.fail('child invocation'))
    response = TestClient(worker.app).post('/internal/resume-uploads/scan')
    assert response.status_code == 503 and response.json() == {'detail': 'resume_coordinator_disabled'}


def test_verified_auth_calls_exact_scan_once(configured, monkeypatch):
    seen = []
    monkeypatch.setattr(worker, 'execute', lambda mode: seen.append(mode) or coordinator.Outcome(mode, 1, 0, 0))
    result = configured.post('/internal/resume-uploads/scan', headers=auth())
    assert result.status_code == 200 and seen == ['scan']
    assert result.json() == {'mode': 'scan', 'scan_completed': 1, 'swept': 0, 'cleanup_completed': 0}


def test_cleanup_works_when_scan_and_direct_admission_disabled(configured, monkeypatch):
    monkeypatch.setenv('RESUME_UPLOAD_COORDINATOR_SCAN_ENABLED', 'false')
    monkeypatch.setenv('RESUME_DIRECT_UPLOAD_ENABLED', 'false')
    seen = []
    monkeypatch.setattr(worker, 'execute', lambda mode: seen.append(mode) or coordinator.Outcome(mode, 0, 4, 1))
    assert configured.post('/internal/resume-uploads/scan', headers=auth()).status_code == 503
    result = configured.post('/internal/resume-uploads/cleanup', headers=auth())
    assert result.status_code == 200 and seen == ['cleanup']


@pytest.mark.parametrize('claims', [
    {'aud': 'https://other.run.app'}, {'email': 'other@synthetic-project.iam.gserviceaccount.com'},
    {'sub': '999999999999999999999'}, {'email_verified': 1}, {'iss': 'untrusted'},
])
def test_claim_identity_mismatch_before_child(configured, monkeypatch, claims):
    valid = worker._verified_claims(TOKEN, AUDIENCE)
    monkeypatch.setattr(worker, '_verified_claims', lambda *a: valid | claims)
    monkeypatch.setattr(worker, 'execute', lambda *a: pytest.fail('child invocation'))
    result = configured.post('/internal/resume-uploads/scan', headers=auth())
    assert result.status_code == 403 and result.json() == {'detail': 'resume_coordinator_caller_refused'}


def test_request_payload_is_not_domain_input(configured, monkeypatch):
    monkeypatch.setattr(worker, 'execute', lambda *a: pytest.fail('child invocation'))
    result = configured.post('/internal/resume-uploads/scan', headers=auth(), content=b'private filename and signedURL')
    assert result.status_code == 400 and 'private' not in result.text


def test_supervisor_actual_hung_child_is_killed_without_output(monkeypatch, tmp_path):
    pidfile = tmp_path / 'pid'
    code = 'import os,time;open(' + repr(str(pidfile)) + ',"w").write(str(os.getpid()));time.sleep(30)'
    monkeypatch.setattr(coordinator, '_child_command', lambda mode: [sys.executable, '-c', code])
    monkeypatch.setattr(coordinator, 'EXECUTION_SECONDS', 0.2)
    with pytest.raises(coordinator.CoordinatorUnavailable, match='resume_coordinator_unavailable'):
        coordinator.execute('cleanup')
    assert pidfile.exists()
    with pytest.raises(ProcessLookupError):
        os.kill(int(pidfile.read_text()), 0)


@pytest.mark.parametrize('header', ['', 'Bearer', 'Basic synthetic', 'Bearer a.b.c\n', 'Bearer a.b.c extra'])
def test_header_is_required_not_scheduler_marker(configured, monkeypatch, header):
    monkeypatch.setattr(worker, '_verified_claims', lambda *a: pytest.fail('verifier'))
    monkeypatch.setattr(worker, 'execute', lambda *a: pytest.fail('child invocation'))
    result = configured.post('/internal/resume-uploads/scan', headers={
        'Authorization': header, 'X-CloudScheduler': 'true', 'X-CloudTasks-TaskName': 'synthetic'})
    assert result.status_code == 401


def test_duplicate_authorization_is_refused(configured, monkeypatch):
    monkeypatch.setattr(worker, '_verified_claims', lambda *a: pytest.fail('verifier'))
    result = configured.post('/internal/resume-uploads/scan', headers=[
        ('Authorization', 'Bearer ' + TOKEN), ('Authorization', 'Bearer ' + TOKEN)])
    assert result.status_code == 401


@pytest.mark.parametrize('name,value', [
    ('SERVICE_ROLE', 'analysis-worker'),
    ('RESUME_UPLOAD_COORDINATOR_AUDIENCE', 'https://untrusted.invalid'),
    ('RESUME_UPLOAD_COORDINATOR_AUDIENCE', AUDIENCE + '/internal/resume-uploads/scan'),
    ('RESUME_UPLOAD_COORDINATOR_AUDIENCE', AUDIENCE + ':443'),
    ('RESUME_UPLOAD_COORDINATOR_CALLER_EMAIL', 'other@other-project.iam.gserviceaccount.com'),
    ('RESUME_UPLOAD_COORDINATOR_CALLER_SUBJECT', 'identity-by-email-only'),
    ('RESUME_UPLOAD_COORDINATOR_ENABLED', 'TRUE'),
])
def test_invalid_runtime_identity_cannot_start_work(configured, monkeypatch, name, value):
    monkeypatch.setenv(name, value)
    monkeypatch.setattr(worker, 'execute', lambda *a: pytest.fail('child invocation'))
    result = configured.post('/internal/resume-uploads/scan', headers=auth())
    assert result.status_code == 503


def test_verifier_and_child_errors_never_reflect_private_values(configured, monkeypatch):
    private = 'private-token-filename-signedurl-database-password'
    def failure(*a):
        raise ValueError(private)
    monkeypatch.setattr(worker, '_verified_claims', failure)
    result = configured.post('/internal/resume-uploads/scan', headers=auth())
    assert result.status_code == 401 and private not in result.text
    monkeypatch.setattr(worker, '_verified_claims', lambda *a: {
        'iss': 'accounts.google.com', 'aud': AUDIENCE, 'email': EMAIL, 'sub': SUBJECT, 'email_verified': True})
    monkeypatch.setattr(worker, 'execute', lambda *a: (_ for _ in ()).throw(coordinator.CoordinatorUnavailable()))
    result = configured.post('/internal/resume-uploads/scan', headers=auth())
    assert result.status_code == 503 and private not in result.text
    assert result.headers['cache-control'] == 'no-store'


@pytest.mark.parametrize('body', [
    b'{"mode":"cleanup","scan_completed":true,"swept":0,"cleanup_completed":0}',
    b'{"mode":"cleanup","scan_completed":0,"swept":101,"cleanup_completed":0}',
    b'{"mode":"cleanup","scan_completed":0,"swept":0,"cleanup_completed":2}',
    b'{"mode":"scan","scan_completed":0,"swept":0,"cleanup_completed":0}',
    b'{"mode":"cleanup","scan_completed":0,"swept":0,"cleanup_completed":0,"filename":"private"}',
    b'{"mode":"cleanup","scan_completed":0,"swept":0,"cleanup_completed":0,"mode":"cleanup"}',
])
def test_child_result_is_bounded_counts_only(body):
    with pytest.raises(coordinator.CoordinatorUnavailable):
        coordinator._decode(body, 'cleanup')


def test_supervisor_actual_success_and_secret_error_output(monkeypatch):
    command = [sys.executable, '-c', 'print(\'{"mode":"cleanup","scan_completed":0,"swept":1,"cleanup_completed":1}\')']
    monkeypatch.setattr(coordinator, '_child_command', lambda mode: command)
    assert coordinator.execute('cleanup') == coordinator.Outcome('cleanup', 0, 1, 1)
    command[:] = [sys.executable, '-c', 'import sys;print("private-secret", file=sys.stderr);print("private-secret");sys.exit(1)']
    with pytest.raises(coordinator.CoordinatorUnavailable) as failure:
        coordinator.execute('cleanup')
    assert str(failure.value) == 'resume_coordinator_unavailable'


def test_supervisor_kills_oversized_output_and_accepts_no_retry(monkeypatch):
    calls = []
    def command(mode):
        calls.append(mode)
        return [sys.executable, '-c', 'print("x"*2000)']
    monkeypatch.setattr(coordinator, '_child_command', command)
    with pytest.raises(coordinator.CoordinatorUnavailable):
        coordinator.execute('cleanup')
    assert calls == ['cleanup']


def test_local_overlap_refuses_without_starting_second_child(monkeypatch):
    coordinator._LOCK.acquire()
    try:
        monkeypatch.setattr(coordinator, '_child_command', lambda *a: pytest.fail('second child'))
        with pytest.raises(coordinator.CoordinatorUnavailable):
            coordinator.execute('cleanup')
    finally:
        coordinator._LOCK.release()


def test_disabled_real_child_imports_no_database_and_has_empty_output(monkeypatch):
    monkeypatch.delenv('RESUME_UPLOAD_COORDINATOR_ENABLED', raising=False)
    monkeypatch.setenv('DATABASE_URL', 'invalid-private-database-url')
    with pytest.raises(coordinator.CoordinatorUnavailable) as failure:
        coordinator.execute('cleanup')
    assert str(failure.value) == 'resume_coordinator_unavailable'


@pytest.mark.parametrize('change', ['valid', 'expired', 'wrong-audience', 'wrong-issuer', 'bad-signature'])
def test_real_google_signature_time_and_audience_verifier(configured, monkeypatch, change):
    import json
    import time
    from datetime import UTC, datetime, timedelta
    from types import SimpleNamespace

    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID
    from google.auth import crypt, jwt
    from google.auth.transport import requests as google_requests

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'synthetic-verifier')])
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
        .serial_number(1).not_valid_before(datetime.now(UTC) - timedelta(days=1))
        .not_valid_after(datetime.now(UTC) + timedelta(days=1)).sign(key, hashes.SHA256()))
    private = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                serialization.NoEncryption())
    signer = crypt.RSASigner.from_string(private, key_id='synthetic')
    claims = {'iss': 'https://accounts.google.com', 'aud': AUDIENCE, 'email': EMAIL,
              'sub': SUBJECT, 'email_verified': True, 'iat': int(time.time()) - 5, 'exp': int(time.time()) + 60}
    if change == 'expired':
        claims['exp'] = int(time.time()) - 2
    if change == 'wrong-audience':
        claims['aud'] = 'https://other.run.app'
    if change == 'wrong-issuer':
        claims['iss'] = 'https://untrusted.invalid'
    signed = jwt.encode(signer, claims).decode('ascii')
    if change == 'bad-signature':
        head, body, signature = signed.split('.')
        signed = head + '.' + body + '.' + ('A' if signature[0] != 'A' else 'B') + signature[1:]
    seen = []
    class FakeRequest:
        def __init__(self, session):
            assert session.trust_env is False and session.verify
            context = session.adapters['https://'].poolmanager.connection_pool_kw['ssl_context']
            assert context.verify_mode == ssl.CERT_REQUIRED and context.check_hostname
            assert context.keylog_filename is None
        def __call__(self, url, **kwargs):
            seen.append((url, kwargs['timeout'], kwargs['allow_redirects']))
            return SimpleNamespace(status=200, data=json.dumps({'synthetic': cert.public_bytes(serialization.Encoding.PEM).decode()}).encode())
    monkeypatch.setattr(google_requests, 'Request', FakeRequest)
    # Restore real verifier hidden by the HTTP fixture, without network.
    monkeypatch.undo()
    # undo also removes configuration; only these explicit synthetic fields return.
    for name, value in {
        'SERVICE_ROLE': 'resume-upload-coordinator', 'GOOGLE_CLOUD_PROJECT': 'synthetic-project',
        'RESUME_UPLOAD_COORDINATOR_AUDIENCE': AUDIENCE,
        'RESUME_UPLOAD_COORDINATOR_CALLER_EMAIL': EMAIL,
        'RESUME_UPLOAD_COORDINATOR_CALLER_SUBJECT': SUBJECT,
        'RESUME_UPLOAD_COORDINATOR_ENABLED': 'true', 'RESUME_UPLOAD_COORDINATOR_SCAN_ENABLED': 'true',
        'RESUME_DIRECT_UPLOAD_ENABLED': 'true', 'SSLKEYLOGFILE': '/nonexistent/synthetic-keylog'}.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setattr(google_requests, 'Request', FakeRequest)
    monkeypatch.setattr(worker, 'execute', lambda mode: coordinator.Outcome(mode))
    response = configured.post('/internal/resume-uploads/scan', headers={'Authorization': 'Bearer ' + signed})
    assert response.status_code == (200 if change == 'valid' else 401)
    assert seen == [('https://www.googleapis.com/oauth2/v1/certs', 5, False)]


def test_query_cannot_be_a_signedurl_or_filename_input(configured, monkeypatch):
    monkeypatch.setattr(worker, 'execute', lambda *a: pytest.fail('child invocation'))
    result = configured.post('/internal/resume-uploads/cleanup?source=private-signedurl', headers=auth())
    assert result.status_code == 400 and 'private' not in result.text


def test_disabled_health_needs_no_database_storage_or_auth(monkeypatch):
    monkeypatch.delenv('RESUME_UPLOAD_COORDINATOR_ENABLED', raising=False)
    monkeypatch.setenv('DATABASE_URL', 'invalid-private-value')
    monkeypatch.setattr(worker, '_verified_claims', lambda *a: pytest.fail('auth request'))
    result = TestClient(worker.app).get('/api/health')
    assert result.status_code == 200 and result.json() == {
        'ok': True, 'role': 'resume-upload-coordinator', 'scan_enabled': False, 'cleanup_enabled': False}
    assert result.headers['cache-control'] == 'no-store'


def test_cleanup_failure_has_fixed_error_and_releases_local_lock(monkeypatch):
    monkeypatch.setattr(coordinator, '_child_command', lambda mode: [sys.executable, '-c',
        'print(\'{"mode":"cleanup","scan_completed":0,"swept":0,"cleanup_completed":0}\')'])
    real = os.killpg
    def failure(pid, sig):
        try:
            real(pid, sig)
        except ProcessLookupError:
            pass
        raise OSError('private-cleanup-error')
    monkeypatch.setattr(coordinator.os, 'killpg', failure)
    with pytest.raises(coordinator.CoordinatorUnavailable) as outcome:
        coordinator.execute('cleanup')
    assert str(outcome.value) == 'resume_coordinator_unavailable'
    assert coordinator._LOCK.acquire(blocking=False)
    coordinator._LOCK.release()
