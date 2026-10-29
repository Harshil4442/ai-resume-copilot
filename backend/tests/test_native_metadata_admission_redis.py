"""Real owned TLS Redis/RESP2 qualification; never a provider or shared server.

Set HIREWIZ_OWNED_REDIS_SERVER to a TLS-enabled redis-server executable. The
mandatory app compatibility gate fails if it cannot run; ordinary local suites
may skip when the executable is absent. No caller-supplied Redis URI is used.
"""
from __future__ import annotations

import ipaddress
import os
import shutil
import socket
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Event

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from limits.storage import RedisStorage
from redis.backoff import NoBackoff
from redis.exceptions import RedisError
from redis.retry import Retry

from app.domains.recovery import native_metadata_admission as admission
from app.domains.recovery.native_metadata_admission import MetadataCapacityUnavailable


@pytest.fixture(scope="module")
def owned_tls_redis(tmp_path_factory):
    configured = os.environ.get("HIREWIZ_OWNED_REDIS_SERVER")
    binary = configured or shutil.which("redis-server")
    if not binary or not Path(binary).is_file() or not os.access(binary, os.X_OK):
        if os.getenv("HIREWIZ_APP_COMPATIBILITY_REQUIRED") == "true":
            pytest.fail("Mandatory native metadata qualification requires an owned TLS Redis executable")
        pytest.skip("Owned TLS Redis executable is unavailable")
    directory = tmp_path_factory.mktemp("owned-native-metadata-redis")
    directory.chmod(0o700)
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")])
    now = datetime.now(UTC)
    certificate = (x509.CertificateBuilder().subject_name(name).issuer_name(name)
        .public_key(key.public_key()).serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1)).not_valid_after(now + timedelta(hours=1))
        .add_extension(x509.SubjectAlternativeName([x509.DNSName("localhost"),
            x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]), critical=False)
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .sign(key, hashes.SHA256()))
    key_path, cert_path = directory / "key.pem", directory / "cert.pem"
    key_path.write_bytes(key.private_bytes(serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    cert_path.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
    key_path.chmod(0o600)
    cert_path.chmod(0o600)
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]
    log_path = directory / "server.log"
    storages = []
    with log_path.open("wb") as log:
        log_path.chmod(0o600)
        process = subprocess.Popen([str(binary), "--port", "0", "--tls-port", str(port),
            "--bind", "127.0.0.1", "--protected-mode", "yes", "--tls-cert-file", str(cert_path),
            "--tls-key-file", str(key_path), "--tls-ca-cert-file", str(cert_path),
            "--tls-auth-clients", "no", "--dir", str(directory), "--save", "", "--appendonly", "no"],
            stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT)
        def storage():
            value = RedisStorage(f"rediss://127.0.0.1:{port}/0", protocol=2,
                ssl_ca_certs=str(cert_path), ssl_cert_reqs="required", ssl_check_hostname=True,
                socket_timeout=1.0, socket_connect_timeout=1.0, retry=Retry(NoBackoff(), 0),
                retry_on_timeout=False)
            storages.append(value)
            return value
        initial = storage()
        try:
            deadline = time.monotonic() + 10
            while True:
                if process.poll() is not None:
                    pytest.fail("Owned Redis process exited before readiness")
                try:
                    if initial.storage.ping():
                        break
                except RedisError:
                    pass
                if time.monotonic() >= deadline:
                    pytest.fail("Owned TLS Redis readiness deadline exceeded")
                time.sleep(0.05)
            yield storage
        finally:
            for value in storages:
                value.storage.close()
                value.storage.connection_pool.disconnect()
            if process.poll() is None:
                process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
            # Only this fixture's process and private directory were touched.
            assert process.poll() is not None
            key_path.unlink(missing_ok=True)
            cert_path.unlink(missing_ok=True)


def configure(monkeypatch, storage, project):
    monkeypatch.setattr(admission, "_shared_storage", lambda: storage)
    monkeypatch.setenv("CANDIDATE_METADATA_ADMISSION_ENABLED", "true")
    monkeypatch.setenv("CANDIDATE_METADATA_CALLS_PER_MINUTE", "40")
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", project)


def test_actual_resp2_nil_from_installed_body_is_wrapped_to_integer_denial(owned_tls_redis, monkeypatch):
    storage = owned_tls_redis()
    timestamp = storage.storage.time()[0]
    raw_key = storage.prefixed_key("owned-raw-body")
    assert storage.lua_acquire_moving_window([raw_key], [timestamp, 1, 60, 1]) == 1
    assert storage.lua_acquire_moving_window([raw_key], [timestamp, 1, 60, 1]) is None
    configure(monkeypatch, storage, "wire-exhaustion")
    for _ in range(40):
        admission.before_metadata_rpc("wire-exhaustion")
    with pytest.raises(MetadataCapacityUnavailable):
        admission.before_metadata_rpc("wire-exhaustion")
    key = storage.prefixed_key("hirewiz-native-metadata-v1/wire-exhaustion")
    assert storage.storage.llen(key) == 40
    assert admission._acquire(storage, key, 40) == 0
    assert storage.storage.connection_pool.connection_kwargs["protocol"] == 2


def test_actual_concurrent_wire_admission_never_exceeds_shared_40(owned_tls_redis, monkeypatch):
    storage = owned_tls_redis()
    configure(monkeypatch, storage, "wire-concurrent")
    def attempt(_):
        try:
            admission.before_metadata_rpc("wire-concurrent")
            return 1
        except MetadataCapacityUnavailable:
            return 0
    with ThreadPoolExecutor(max_workers=8) as executor:
        outcomes = list(executor.map(attempt, range(80)))
    assert sum(outcomes) == 40
    key = storage.prefixed_key("hirewiz-native-metadata-v1/wire-concurrent")
    assert storage.storage.llen(key) == 40


def test_actual_expired_key_stale_clock_counterexample_cannot_enter_atomic_interface(owned_tls_redis, monkeypatch):
    storage = owned_tls_redis()
    # Reproduce the old split-TIME bug on the genuine installed Lua and RESP2.
    # An absent key is the exact state after TTL expiry during a process pause.
    current = storage.storage.time()[0]
    old_key = storage.prefixed_key("owned-stale-clock-counterexample")
    stale = [storage.lua_acquire_moving_window([old_key], [current - 61, 40, 60, 1]) for _ in range(40)]
    fresh = [storage.lua_acquire_moving_window([old_key], [current, 40, 60, 1]) for _ in range(40)]
    assert sum(stale) + sum(fresh) == 80
    configure(monkeypatch, storage, "wire-no-client-clock")
    monkeypatch.setattr(storage.storage, "time", lambda: pytest.fail("Client TIME must not be sampled"))
    for _ in range(40):
        admission.before_metadata_rpc("wire-no-client-clock")
    with pytest.raises(MetadataCapacityUnavailable):
        admission.before_metadata_rpc("wire-no-client-clock")


def test_actual_paused_caller_samples_clock_only_when_atomic_script_runs(owned_tls_redis, monkeypatch):
    storage = owned_tls_redis()
    configure(monkeypatch, storage, "wire-paused-caller")
    started, release = Event(), Event()
    actual_acquire = admission._acquire
    def delayed_acquire(value, key, amount):
        if not started.is_set():
            started.set()
            assert release.wait(5), "Owned paused caller was not released"
        return actual_acquire(value, key, amount)
    monkeypatch.setattr(admission, "_acquire", delayed_acquire)
    def paused():
        with pytest.raises(MetadataCapacityUnavailable):
            admission.before_metadata_rpc("wire-paused-caller")
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(paused)
        try:
            assert started.wait(2)
            for _ in range(40):
                admission.before_metadata_rpc("wire-paused-caller")
        finally:
            release.set()
        future.result(timeout=5)
    key = storage.prefixed_key("hirewiz-native-metadata-v1/wire-paused-caller")
    assert storage.storage.llen(key) == 40
