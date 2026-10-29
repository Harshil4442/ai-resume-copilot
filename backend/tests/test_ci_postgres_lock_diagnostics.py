"""CI diagnostic guards and encrypted evidence; no production connections."""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import psycopg
import pytest
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from sqlalchemy.engine import make_url

from scripts import ci_postgres_lock_diagnostics as diagnostic


def environment(tmp_path):
    return {"GITHUB_ACTIONS": "true", "RUNNER_OS": "Linux", "GITHUB_JOB": "backend",
            "GITHUB_RUN_ID": "12345", "GITHUB_RUN_ATTEMPT": "1", "RUNNER_TEMP": str(tmp_path),
            "CI_POSTGRES_CONTAINER_ID": "a" * 64,
            "HIREWIZ_TEST_POSTGRES_URL": "postgresql+psycopg://hirewiz:hirewiz@127.0.0.1:55433/hirewiz_admission_test"}


@pytest.mark.parametrize(("key", "value"), [
    ("GITHUB_ACTIONS", "false"), ("RUNNER_OS", "Darwin"), ("GITHUB_JOB", "frontend"),
    ("GITHUB_RUN_ID", "../other"), ("GITHUB_RUN_ATTEMPT", "0"),
    ("CI_POSTGRES_CONTAINER_ID", "a" * 12), ("RUNNER_TEMP", "relative"),
    ("HIREWIZ_TEST_POSTGRES_URL", "postgresql+psycopg://hirewiz:hirewiz@company.example:55433/hirewiz_admission_test"),
    ("HIREWIZ_TEST_POSTGRES_URL", "postgresql+psycopg://hirewiz:hirewiz@127.0.0.1:5432/hirewiz_admission_test"),
    ("HIREWIZ_TEST_POSTGRES_URL", "postgresql+psycopg://hirewiz:hirewiz@127.0.0.1:55433/postgres"),
    ("HIREWIZ_TEST_POSTGRES_URL", "postgresql+psycopg://other:hirewiz@127.0.0.1:55433/hirewiz_admission_test"),
    ("HIREWIZ_TEST_POSTGRES_URL", "postgresql+psycopg://hirewiz:other@127.0.0.1:55433/hirewiz_admission_test"),
    ("HIREWIZ_TEST_POSTGRES_URL", "postgresql+psycopg://hirewiz:hirewiz@127.0.0.1:55433/hirewiz_admission_test?options=x"),
])
def test_scope_refuses_other_hosts_jobs_credentials_ports_and_paths(tmp_path, key, value):
    env = environment(tmp_path)
    env[key] = value
    with pytest.raises(ValueError):
        diagnostic.scope(env)


def test_canonical_scope_and_symlink_refusal(tmp_path):
    result = diagnostic.scope(environment(tmp_path))
    assert result.port == 55433 and result.root.parent == tmp_path
    link = tmp_path / "link"
    link.symlink_to(tmp_path, target_is_directory=True)
    env = environment(tmp_path)
    env["RUNNER_TEMP"] = str(link)
    with pytest.raises(ValueError):
        diagnostic.scope(env)


def test_plugin_is_opt_in_and_refuses_bad_scope_before_any_connection(monkeypatch, capsys):
    monkeypatch.setattr(diagnostic, "_active", None)
    monkeypatch.setattr(diagnostic.psycopg, "connect", lambda **_: pytest.fail("must not connect"))
    monkeypatch.delenv("HIREWIZ_CI_LOCK_DIAGNOSTICS", raising=False)
    diagnostic.pytest_configure(None)
    assert not capsys.readouterr().out
    monkeypatch.setenv("HIREWIZ_CI_LOCK_DIAGNOSTICS", "1")
    monkeypatch.setenv("RUNNER_OS", "Darwin")
    diagnostic.pytest_configure(None)
    assert diagnostic._active is None
    assert "test behavior is unchanged" in capsys.readouterr().out


def test_container_image_port_and_cluster_identity_are_bound_before_connect(tmp_path, monkeypatch):
    scope = diagnostic.scope(environment(tmp_path))
    meta = {"id": scope.container, "image": diagnostic.IMAGE, "running": True,
            "ports": {"5432/tcp": [{"HostIp": "0.0.0.0", "HostPort": "55433"}]}}
    calls = []

    def native(command):
        calls.append(command)
        return json.dumps(meta) if "inspect" in command else "123456789"

    monkeypatch.setattr(diagnostic, "native", native)
    assert diagnostic.container_identity(scope) == "123456789"
    assert len(calls) == 2 and all(scope.container in call for call in calls)
    for field, value in [("id", "b" * 64), ("image", "postgres:latest"), ("running", False),
                         ("ports", {"5432/tcp": [{"HostIp": "0.0.0.0", "HostPort": "5432"}]})]:
        original = meta[field]
        meta[field] = value
        with pytest.raises(ValueError):
            diagnostic.container_identity(scope)
        meta[field] = original


class FakeConnection:
    def __init__(self, identity="123456789", version="170011", fail_alter=0):
        self.identity, self.version = identity, version
        self.calls = []
        self.closed = False
        self.alters, self.fail_alter = 0, fail_alter
        self.settings = {"log_lock_waits": "off", "deadlock_timeout": "1s",
                         "log_parameter_max_length": "-1", "log_parameter_max_length_on_error": "0",
                         "log_min_error_statement": "error"}

    def execute(self, query, parameters=None):
        rendered = query if isinstance(query, str) else query.as_string()
        self.calls.append((rendered, parameters))
        if rendered.startswith("ALTER SYSTEM"):
            self.alters += 1
            if self.alters == self.fail_alter:
                raise RuntimeError("synthetic settings failure")
            match = re.fullmatch(r'ALTER SYSTEM SET "([a-z_]+)" TO \'([^\']*)\'', rendered)
            assert match
            self.settings[match[1]] = match[2]
        value = self.identity if "pg_control_system" in rendered else self.version
        if parameters:
            value = self.settings[parameters[0]]
        if rendered == "SELECT pg_reload_conf()":
            value = True
        return SimpleNamespace(fetchone=lambda: (value,), fetchall=list)

    def close(self):
        self.closed = True


@pytest.mark.parametrize(("identity", "version"), [("other", "170011"), ("123456789", "160015")])
def test_socket_identity_mismatch_closes_without_altering_server(tmp_path, monkeypatch, identity, version):
    connection = FakeConnection(identity, version)
    monkeypatch.setattr(diagnostic, "container_identity", lambda _: "123456789")
    monkeypatch.setattr(diagnostic.psycopg, "connect", lambda **_: connection)
    probe = diagnostic.Diagnostics(diagnostic.scope(environment(tmp_path)))
    with pytest.raises(ValueError):
        probe.start()
    assert connection.closed
    assert not any(call[0].startswith("ALTER SYSTEM") for call in connection.calls)


def test_start_stop_restores_only_diagnostic_settings_and_closes_sampler(tmp_path, monkeypatch):
    connection = FakeConnection()
    monkeypatch.setattr(diagnostic, "container_identity", lambda _: "123456789")
    monkeypatch.setattr(diagnostic.psycopg, "connect", lambda **_: connection)
    probe = diagnostic.Diagnostics(diagnostic.scope(environment(tmp_path)))
    probe.start()
    probe.close()
    assert connection.closed and not probe.thread.is_alive()
    alterations = [call[0] for call in connection.calls if call[0].startswith("ALTER SYSTEM")]
    assert len(alterations) == len(diagnostic.SETTINGS) * 2
    assert all("lock_timeout" not in call.replace("deadlock_timeout", "")
               and "statement_timeout" not in call for call in alterations)


def test_partial_configuration_failure_restores_acknowledged_or_unknown_setting(tmp_path, monkeypatch):
    connection = FakeConnection(fail_alter=2)
    monkeypatch.setattr(diagnostic, "container_identity", lambda _: "123456789")
    monkeypatch.setattr(diagnostic.psycopg, "connect", lambda **_: connection)
    probe = diagnostic.Diagnostics(diagnostic.scope(environment(tmp_path)))
    with pytest.raises(RuntimeError):
        probe.start()
    assert connection.closed
    assert any('"log_lock_waits" TO \'off\'' in call[0] for call in connection.calls)
    assert any('"deadlock_timeout" TO \'1s\'' in call[0] for call in connection.calls)


def test_stalled_observer_cancel_has_a_deadline_and_closes_only_its_connection(tmp_path):
    connection = FakeConnection()
    probe = diagnostic.Diagnostics(diagnostic.scope(environment(tmp_path)))
    probe.connection = connection

    class Stalled:
        alive = True

        def join(self, timeout):
            assert timeout <= 2

        def is_alive(self):
            return self.alive

    thread = Stalled()

    def cancel_safe(*, timeout):
        assert timeout == 0.5
        thread.alive = False

    connection.cancel_safe = cancel_safe
    probe.thread = thread
    probe.close()
    assert connection.closed and not thread.alive


def test_filtered_rows_exclude_sql_parameters_and_bound_pids_and_row_count():
    secret = "SELECT candidate_email FROM private WHERE password='do-not-record'"
    row = (123, "active", "Lock", secret, list(range(1, 40)), 4500, "advisory", "ExclusiveLock",
           True, 1, None, 123, 456, 1)
    rows = diagnostic.filter_rows([row] * 100)
    assert len(rows) == diagnostic.MAX_ROWS
    assert rows[0]["blockers"] == list(range(1, 9))
    assert rows[0]["wait_event"] is None
    assert secret not in json.dumps(rows)
    assert "query" not in json.dumps(rows) and "parameters" not in json.dumps(rows)


def test_node_hash_correlates_sampling_phases_and_failure_without_raw_parameters():
    node = "tests/private.py::test_case[do-not-record-password]"
    for phase in ("setup", "call", "teardown"):
        identity = diagnostic.node_identity(node + f" ({phase})")
        assert identity == {"node_sha256": hashlib.sha256(node.encode()).hexdigest(), "phase": phase}
        assert node not in json.dumps(identity)


def test_host_summary_does_not_read_or_record_arguments_environ_or_command_name(tmp_path):
    (tmp_path / "meminfo").write_text("MemTotal: 1000 kB\nMemAvailable: 500 kB\nOther: secret\n")
    (tmp_path / "stat").write_text("cpu 1 2 3 4 5 6 7 8 9 10\n")
    process = tmp_path / "123"
    process.mkdir()
    fields = ["R", "1"] + ["0"] * 22
    fields[11], fields[12], fields[21] = "10", "20", "5"
    (process / "stat").write_text("123 (private-command-secret) " + " ".join(fields))
    (process / "cmdline").write_text("never-read-password")
    (process / "environ").write_text("never-read-token")
    value = diagnostic.host_snapshot(tmp_path)
    assert value["top_cpu_processes"][0]["cpu_ticks"] == 30
    assert "secret" not in json.dumps(value) and "password" not in json.dumps(value)


@pytest.fixture(scope="module")
def encryption_key():
    return rsa.generate_private_key(public_exponent=65537, key_size=3072)


def public_bytes(key):
    return key.public_key().public_bytes(serialization.Encoding.PEM,
                                         serialization.PublicFormat.SubjectPublicKeyInfo)


def decrypt(raw, key):
    envelope = json.loads(raw)
    label = envelope["format"].encode()
    symmetric = key.decrypt(base64.b64decode(envelope["wrapped_key"]), padding.OAEP(
        mgf=padding.MGF1(hashes.SHA256()), algorithm=hashes.SHA256(), label=label))
    return AESGCM(symmetric).decrypt(base64.b64decode(envelope["nonce"]),
                                    base64.b64decode(envelope["ciphertext"]), label)


def test_envelope_roundtrip_tamper_and_public_key_validation(encryption_key):
    raw = b'{"pid":123,"blockers":[456]}'
    sealed = diagnostic.encrypt(raw, public_bytes(encryption_key))
    assert decrypt(sealed, encryption_key) == raw and raw not in sealed
    envelope = json.loads(sealed)
    envelope["ciphertext"] = base64.b64encode(b"invalid").decode()
    with pytest.raises(InvalidTag):
        decrypt(json.dumps(envelope).encode(), encryption_key)
    with pytest.raises(ValueError):
        diagnostic.encrypt(raw, b"not a public key")
    with pytest.raises(ValueError):
        diagnostic.encrypt(raw, public_bytes(rsa.generate_private_key(public_exponent=65537, key_size=2048)))
    with pytest.raises(ValueError):
        diagnostic.encrypt(b"x" * (diagnostic.MAX_PLAINTEXT + 1), public_bytes(encryption_key))


def test_payload_ring_envelope_and_private_file_permissions_are_bounded(tmp_path, monkeypatch, encryption_key):
    key_path = tmp_path / "public.pem"
    key_path.write_bytes(public_bytes(encryption_key))
    monkeypatch.setattr(diagnostic, "PUBLIC_KEY", key_path)
    monkeypatch.setattr(diagnostic, "host_snapshot", dict)
    probe = diagnostic.Diagnostics(diagnostic.scope(environment(tmp_path)))
    for number in range(300):
        probe.samples.append({"sequence": number, "activity": [{"pid": 123}] * 24})
    assert len(probe.samples) == 128
    path = probe.retain()
    assert path.stat().st_mode & 0o777 == 0o600
    assert path.parent.stat().st_mode & 0o777 == 0o700
    assert len(path.read_bytes()) <= diagnostic.MAX_ENVELOPE
    decoded = decrypt(path.read_bytes(), encryption_key)
    assert len(decoded) <= diagnostic.MAX_PLAINTEXT and json.loads(decoded)["samples"]
    assert list(path.parent.iterdir()) == [path]


def test_oversized_ring_drops_old_samples_without_unbounded_output():
    value = {"samples": [{"n": n, "data": "x" * 4000} for n in range(128)], "truncated": False}
    raw = diagnostic.bounded_payload(value)
    assert len(raw) <= diagnostic.MAX_PLAINTEXT and value["truncated"] is True
    assert json.loads(raw)["samples"][-1]["n"] == 127


def test_failure_window_is_frozen_before_later_tests_evict_the_live_ring(tmp_path, monkeypatch, encryption_key):
    key_path = tmp_path / "public.pem"
    key_path.write_bytes(public_bytes(encryption_key))
    monkeypatch.setattr(diagnostic, "PUBLIC_KEY", key_path)
    monkeypatch.setattr(diagnostic, "host_snapshot", dict)
    monkeypatch.setattr(diagnostic, "server_waits", lambda _: [])
    probe = diagnostic.Diagnostics(diagnostic.scope(environment(tmp_path)))
    probe.samples.append({"sequence": 1, "pid": 123})
    probe.failed("private-node-with-password-parameter", "call")
    for number in range(300):
        probe.samples.append({"sequence": number + 2})
    value = json.loads(decrypt(probe.retain().read_bytes(), encryption_key))
    assert value["samples"] == [{"sequence": 1, "pid": 123}]
    assert "private-node" not in json.dumps(value)


def test_encryption_failure_writes_no_plaintext_or_directory(tmp_path, monkeypatch):
    probe = diagnostic.Diagnostics(diagnostic.scope(environment(tmp_path)))
    monkeypatch.setattr(diagnostic, "host_snapshot", dict)
    monkeypatch.setattr(diagnostic, "encrypt", lambda *_: (_ for _ in ()).throw(ValueError("private")))
    with pytest.raises(ValueError):
        probe.retain()
    assert not probe.bound.root.exists()


def test_evidence_never_follows_a_directory_symlink_or_overwrites_a_file(tmp_path, monkeypatch, encryption_key):
    key_path = tmp_path / "public.pem"
    key_path.write_bytes(public_bytes(encryption_key))
    monkeypatch.setattr(diagnostic, "PUBLIC_KEY", key_path)
    monkeypatch.setattr(diagnostic, "host_snapshot", dict)
    probe = diagnostic.Diagnostics(diagnostic.scope(environment(tmp_path)))
    other = tmp_path / "other"
    other.mkdir()
    probe.bound.root.symlink_to(other, target_is_directory=True)
    with pytest.raises(ValueError):
        probe.retain()
    assert list(other.iterdir()) == []
    probe.bound.root.unlink()
    path = probe.retain()
    first = path.read_bytes()
    with pytest.raises(FileExistsError):
        probe.retain()
    assert path.read_bytes() == first


def test_financial_exit_status_is_unchanged_when_encryption_or_cleanup_fails(monkeypatch, capsys):
    class Broken:
        def close(self):
            raise RuntimeError("do-not-print-secret")

        def retain(self):
            raise RuntimeError("do-not-print-secret")

    monkeypatch.setattr(diagnostic, "_active", Broken())
    session = SimpleNamespace(exitstatus=1)
    diagnostic.pytest_sessionfinish(session, 1)
    assert session.exitstatus == 1 and diagnostic._active is None
    assert "do-not-print-secret" not in capsys.readouterr().out


def test_configuration_abort_cleanup_is_idempotent(monkeypatch):
    calls = []
    fake = SimpleNamespace(close=lambda: calls.append("close"), retain=lambda: calls.append("encrypted"))
    monkeypatch.setattr(diagnostic, "_active", fake)
    diagnostic.pytest_unconfigure(None)
    diagnostic.pytest_unconfigure(None)
    assert calls == ["close", "encrypted"] and diagnostic._active is None


def test_server_log_filter_retains_only_numeric_holder_evidence(monkeypatch):
    original = subprocess.Popen
    raw = ("2026-10-29 UTC [123] LOG: process 123 still waiting for ExclusiveLock on advisory lock after 100.5 ms\n"
           "2026-10-29 UTC [123] DETAIL: Process holding the lock: 456. Wait queue: 123, 789.\n"
           "STATEMENT: SELECT password FROM private_accounts WHERE token='never-record'\n")
    monkeypatch.setattr(diagnostic.subprocess, "Popen", lambda *_a, **_k: original(
        [sys.executable, "-c", "import sys;sys.stdout.write(" + repr(raw) + ")"],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT))
    result = diagnostic.server_waits("a" * 64)
    assert result == [{"pid": 123, "wait_ms": 100}, {"pid": 123, "holder_pid": 456, "waiters": [123, 789]}]
    assert "password" not in json.dumps(result) and "never-record" not in json.dumps(result)


def test_server_log_collection_is_bounded_even_with_large_or_hanging_output(monkeypatch):
    original = subprocess.Popen
    monkeypatch.setattr(diagnostic.subprocess, "Popen", lambda *_a, **_k: original(
        [sys.executable, "-c", "import sys,time;sys.stdout.write('x'*1000000);sys.stdout.flush();time.sleep(20)"],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT))
    start = time.monotonic()
    assert diagnostic.server_waits("a" * 64) == []
    assert time.monotonic() - start < 4


def test_committed_file_is_a_public_key_only():
    raw = diagnostic.PUBLIC_KEY.read_bytes()
    assert b"PRIVATE" not in raw
    key = serialization.load_pem_public_key(raw)
    assert isinstance(key, rsa.RSAPublicKey) and key.key_size >= 3072


def test_owned_pg17_settings_restore_without_changing_financial_deadlines():
    configured = os.getenv("HIREWIZ_DIAGNOSTIC_OWNED_PG_URL")
    if not configured:
        pytest.skip("Requires the explicitly owned local PostgreSQL 17 diagnostic server")
    url = make_url(configured)
    assert url.host == "127.0.0.1" and url.port not in {5432, 55433, 55434}
    assert url.database == "hirewiz_admission_test" and url.username == "hirewiz_profile" and not url.query
    with psycopg.connect(host=url.host, port=url.port, user=url.username, dbname=url.database,
                         autocommit=True, options="-clock_timeout=4000 -cstatement_timeout=10000") as connection:
        assert int(connection.execute("SELECT current_setting('server_version_num')").fetchone()[0]) // 10000 == 17
        before = {key: connection.execute("SELECT current_setting(%s)", (key,)).fetchone()[0]
                  for key in diagnostic.SETTINGS}
        previous = diagnostic.configure(connection)
        try:
            assert connection.execute("SELECT current_setting('lock_timeout')").fetchone()[0] == "4s"
            assert connection.execute("SELECT current_setting('statement_timeout')").fetchone()[0] == "10s"
            assert connection.execute("SELECT current_setting('log_lock_waits')").fetchone()[0] == "on"
        finally:
            diagnostic.restore(connection, previous)
        after = {key: connection.execute("SELECT current_setting(%s)", (key,)).fetchone()[0]
                 for key in diagnostic.SETTINGS}
        assert after == before


def test_owned_pg17_sampler_identifies_actual_holder_and_cleans_up_transactions():
    configured = os.getenv("HIREWIZ_DIAGNOSTIC_OWNED_PG_URL")
    if not configured:
        pytest.skip("Requires the explicitly owned local PostgreSQL 17 diagnostic server")
    url = make_url(configured)
    assert url.host == "127.0.0.1" and url.port not in {5432, 55433, 55434}
    assert url.database == "hirewiz_admission_test" and url.username == "hirewiz_profile" and not url.query
    arguments = dict(host=url.host, port=url.port, user=url.username, dbname=url.database,
                     options="-clock_timeout=4000 -cstatement_timeout=10000")
    with psycopg.connect(**arguments) as holder, psycopg.connect(**arguments, autocommit=True) as observer:
        assert int(observer.execute("SELECT current_setting('server_version_num')").fetchone()[0]) // 10000 == 17
        holder.execute("SELECT pg_advisory_xact_lock(982345671)")
        pid = holder.info.backend_pid

        def wait():
            with psycopg.connect(**arguments) as waiter:
                waiter.execute("SELECT pg_advisory_xact_lock(982345671)")

        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(wait)
            try:
                deadline = time.monotonic() + 2
                found = []
                while time.monotonic() < deadline:
                    found = [row for row in diagnostic.filter_rows(observer.execute(diagnostic.SAMPLE).fetchall())
                             if row["wait_type"] == "Lock" and pid in row["blockers"]]
                    if found:
                        break
                    time.sleep(0.025)
                assert found and found[0]["pid"] != pid
                assert "SELECT" not in json.dumps(found)
            finally:
                holder.rollback()
            future.result(timeout=2)
        assert observer.execute("SELECT count(*) FROM pg_locks WHERE locktype='advisory' AND granted").fetchone()[0] == 0
