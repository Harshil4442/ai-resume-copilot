"""Typed QA clone checks; intercepted metadata plus one opt-in owned PG17 cluster.

The local marker removal simulates a checkpoint rollback; it is not Neon restore
evidence. No provider operation, normal manager, production URI or app.main runs.
"""
from __future__ import annotations

import json
import os
import secrets
import shutil
import signal
import socket
import subprocess
import time
from contextlib import contextmanager
from dataclasses import asdict, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from alembic.config import Config
from infra.gcp import schema27_restore_clone as c
from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL
from sqlalchemy.pool import NullPool

from alembic import command

ROOT = Path(__file__).resolve().parents[2]
PIN = c.ClonePin(c.production.PROJECT, "br-owned-clone", "ep-owned-clone",
    "ep-owned-clone.c-8.us-east-2.aws.neon.tech", "a" * 32)


@pytest.fixture(autouse=True)
def no_manager(monkeypatch):
    monkeypatch.setattr(c.bootstrap, "_get", lambda *_: pytest.fail("No provider/credential reads"))
    monkeypatch.setattr(c.bootstrap, "_check_manager", lambda: pytest.fail("No normal manager"))


def snapshot():
    role = c.RoleIdentity("neondb_owner", 42, True, False, False, False, False, False, False)
    identity = c.CloneIdentity("b" * 64, 71, role.name, role.oid, role.name, role.oid, 2200, (role,))
    return c.CloneSnapshot(PIN, identity, c.schema27.SCHEMA, "0/100", datetime.now(UTC).isoformat(),
        "c" * 64, (c.TableDigest("public", "users", 1, 8, "d" * 64),),
        (c.SequenceState("public", "users_id_seq", 1, True),), "e" * 64)


def test_json_roundtrip_is_typed_metadata_only_and_marker_custody_is_exact():
    before = snapshot()
    restored = replace(before, checkpoint_lsn="0/300", checkpoint_utc=(datetime.now(UTC) + timedelta(seconds=1)).isoformat())
    marker = c.CloneMarker(PIN, before.digest, before.checkpoint_lsn, "0/200")
    assert c.CloneSnapshot.from_dict(json.loads(json.dumps(asdict(before)))) == before
    assert c.CloneMarker.from_dict(json.loads(json.dumps(asdict(marker)))) == marker
    report = c.compare_restore(before, restored, marker)
    assert report["matched"] and report["qa_marker_absent"] and not report["provider_restore_proven"]
    assert not report["full_R03_qualified"] and not report["external_authority_replay_proven"]
    assert before.stable_digest == restored.stable_digest and before.digest != restored.digest


@pytest.mark.parametrize("changes", [
    {"branch": c.production.BRANCH}, {"endpoint": c.production.ENDPOINT},
    {"host": c.production.HOST}, {"project": "foreign"}, {"database": "postgres"},
    {"nonce": True}, {"host": PIN.host + ".evil.test"},
])
def test_no_production_or_ambient_target_can_be_a_qa_pin(changes):
    with pytest.raises(c.RestoreCloneDenied):
        replace(PIN, **changes).validate()


@pytest.mark.parametrize("change", ["extra", "boolean_oid", "marker_boolean", "row_secret", "sequence_boolean", "pin_extra"])
def test_strict_receipts_refuse_extra_fields_and_coercions(change):
    value = asdict(snapshot())
    if change == "extra":
        value["connection_uri"] = "secret"
    elif change == "boolean_oid":
        value["identity"]["database_oid"] = True
    elif change == "marker_boolean":
        value["marker_present"] = "false"
    elif change == "row_secret":
        value["tables"][0]["rows"] = "private candidate text"
    elif change == "sequence_boolean":
        value["sequences"][0]["last_value"] = True
    else:
        value["pin"]["password"] = "secret"
    with pytest.raises(c.RestoreCloneDenied):
        c.CloneSnapshot.from_dict(value)


@pytest.mark.parametrize("change", ["identity", "acl", "data", "sequence", "obligation", "marker", "nonce", "custody"])
def test_each_restore_dimension_must_match_without_relabelling(change):
    before = snapshot()
    restored, marker = before, c.CloneMarker(PIN, before.digest, before.checkpoint_lsn)
    if change == "identity":
        restored = replace(before, identity=replace(before.identity, database_oid=72))
    elif change == "acl":
        restored = replace(before, catalogue_sha256="f" * 64)
    elif change == "data":
        restored = replace(before, tables=(replace(before.tables[0], sha256="f" * 64),))
    elif change == "sequence":
        restored = replace(before, sequences=(replace(before.sequences[0], is_called=False),))
    elif change == "obligation":
        restored = replace(before, financial_sha256="f" * 64)
    elif change == "marker":
        restored = replace(before, marker_present=True)
    elif change == "nonce":
        restored = replace(before, pin=replace(PIN, nonce="b" * 32))
    else:
        marker = replace(marker, checkpoint_sha256="f" * 64)
    with pytest.raises(c.RestoreCloneDenied):
        c.compare_restore(before, restored, marker)


def test_stream_is_incremental_length_bound_and_never_exports_private_rows(monkeypatch):
    secret = "private@example.test"
    chunks = [[(json.dumps({"email": secret}),)], [("{}",)], []]
    calls, closed = [], []
    result = SimpleNamespace(fetchmany=lambda n: (calls.append(n), chunks.pop(0))[1], close=lambda: closed.append(True))
    connection = SimpleNamespace(execute=lambda _: result)
    count, size, digest = c._stream_digest(connection, "SELECT synthetic", time.monotonic() + 5)
    assert count == 2 and calls == [64, 64, 64] and closed == [True]
    assert secret not in json.dumps((count, size, digest))
    monkeypatch.setattr(c, "MAX_ROW_BYTES", 1)
    chunks[:] = [[("private",)]]
    with pytest.raises(c.RestoreCloneDenied, match="stream_bound"):
        c._stream_digest(connection, "SELECT synthetic", time.monotonic() + 5)
    assert closed == [True, True]


def test_marker_unknown_commit_never_exports_driver_text_or_repeats_ddl(monkeypatch):
    before, sent = snapshot(), []
    connection = SimpleNamespace(exec_driver_sql=lambda sql: sent.append(sql),
        execute=lambda *_: None)
    @contextmanager
    def lost_ack(*_args, **_kwargs):
        yield connection
        raise RuntimeError("private PostgreSQL connection URI and application rows")
    monkeypatch.setattr(c, "_connection", lost_ack)
    monkeypatch.setattr(c, "_capture", lambda *_: before)
    monkeypatch.setattr(c, "_no_other_sessions", lambda *_: None)
    with pytest.raises(c.RestoreCloneDenied, match="^schema27_restore_clone_marker_unknown_reconcile_only$"):
        c.install_marker(PIN, before)
    assert sum(sql.startswith("CREATE SCHEMA") for sql in sent) == 1
    assert sum(sql.startswith("CREATE TABLE") for sql in sent) == 1


def test_old_checkpoint_is_refused_before_marker_mutation(monkeypatch):
    before = replace(snapshot(), checkpoint_utc=(datetime.now(UTC) - timedelta(hours=6)).isoformat())
    sent = []
    @contextmanager
    def connection(*_args, **_kwargs):
        yield SimpleNamespace(exec_driver_sql=sent.append)
    monkeypatch.setattr(c, "_connection", connection)
    monkeypatch.setattr(c, "_capture", lambda *_: replace(before, checkpoint_utc=datetime.now(UTC).isoformat()))
    with pytest.raises(c.RestoreCloneDenied, match="checkpoint_expired_or_future"):
        c.install_marker(PIN, before)
    assert not any(sql.startswith("CREATE") for sql in sent)


@pytest.mark.parametrize("held", [False, True])
def test_uri_is_bound_to_fresh_qa_and_exact_optional_held_parent_in_ram(monkeypatch, held):
    seen = []
    parent = "br-owned-held" if held else c.production.BRANCH
    replies = {
        "/projects/" + PIN.project: {"project": {"id": PIN.project, "org_id": c.bootstrap.ORG,
            "region_id": "aws-us-east-2", "pg_version": 17}},
        "/projects/" + PIN.project + "/branches/" + PIN.branch: {"branch": {"id": PIN.branch,
            "project_id": PIN.project, "parent_id": parent, "name": "hirewiz-r03-qa-" + PIN.nonce}},
        "/projects/" + PIN.project + "/branches/br-owned-held": {"branch": {"id": parent,
            "project_id": PIN.project, "parent_id": c.production.BRANCH, "name": "hirewiz-r03-held-" + PIN.nonce}},
        "/projects/" + PIN.project + "/endpoints/" + PIN.endpoint: {"endpoint": {"id": PIN.endpoint,
            "project_id": PIN.project, "branch_id": PIN.branch, "host": PIN.host, "type": "read_write"}},
    }
    def get(route):
        seen.append(route)
        if "/connection_uri?" in route:
            assert "pooled=false" in route and "role_name=neondb_owner" in route
            return {"uri": "postgresql://neondb_owner:synthetic-password@" + PIN.host + "/neondb?sslmode=require"}
        return replies[route]
    monkeypatch.setattr(c.bootstrap, "_check_manager", lambda: None)
    monkeypatch.setattr(c.bootstrap, "_get", get)
    assert c._password(PIN, time.monotonic() + 5) == "synthetic-password"
    assert len(seen) == (5 if held else 4)
    monkeypatch.setenv("PGHOST", "foreign")
    with pytest.raises(c.RestoreCloneDenied, match="ambient_pg_override"):
        c._password(PIN, time.monotonic() + 5)


def _pg(pg_bin, tool, *args, env=None):
    with subprocess.Popen([str(pg_bin / tool), *map(str, args)], stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True,
        env={"LC_ALL": "C", **(env or {})}) as process:
        try:
            output, error = process.communicate(timeout=30)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.communicate(timeout=5)
            pytest.fail("Owned PG17 command exceeded its deadline")
    assert process.returncode == 0, (tool, error.decode())
    return output.decode()


@pytest.fixture
def owned_pg(tmp_path):
    configured = os.getenv("HIREWIZ_SCHEMA27_CLONE_PG_BIN")
    if not configured:
        pytest.skip("Requires an explicitly selected owned local PG17 binary directory")
    assert os.getenv("GITHUB_ACTIONS") != "true"
    pg_bin = Path(configured)
    assert pg_bin.is_absolute() and "PostgreSQL) 17." in _pg(pg_bin, "initdb", "--version")
    directory, password = tmp_path / "owned-cluster", secrets.token_hex(24)
    password_file = tmp_path / "password"
    password_file.write_text(password + "\n")
    password_file.chmod(0o600)
    try:
        _pg(pg_bin, "initdb", "-D", directory, "--username=neondb_owner", "--auth-local=reject",
            "--auth-host=scram-sha-256", "--pwfile", password_file, "--no-locale", "--encoding=UTF8", "--data-checksums")
    finally:
        password_file.unlink()
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    engine = None
    try:
        _pg(pg_bin, "pg_ctl", "-D", directory, "-l", tmp_path / "postgres.log", "-o",
            f"-h 127.0.0.1 -p {port} -c unix_socket_directories= -c ssl=off", "-w", "-t", "10", "start")
        url = URL.create("postgresql+psycopg", username="neondb_owner", password=password,
                         host="127.0.0.1", port=port, database="postgres")
        temporary = create_engine(url, poolclass=NullPool, isolation_level="AUTOCOMMIT")
        with temporary.connect() as db:
            assert db.exec_driver_sql("SELECT current_setting('data_directory')").scalar_one() == str(directory)
            db.exec_driver_sql("CREATE DATABASE neondb")
        temporary.dispose()
        engine = create_engine(url.set(database="neondb"), poolclass=NullPool, isolation_level="REPEATABLE READ",
            connect_args={"connect_timeout": 5, "sslmode": "disable", "options": "-c statement_timeout=2000 -c lock_timeout=1000"})
        cfg = Config(str(ROOT / "backend/alembic.ini"))
        cfg.set_main_option("script_location", str(ROOT / "backend/alembic"))
        with engine.begin() as db:
            cfg.attributes["connection"] = db
            command.upgrade(cfg, c.schema27.SCHEMA)
            db.exec_driver_sql("CREATE SCHEMA neon_auth")
            db.exec_driver_sql("CREATE TABLE neon_auth.synthetic_restored (id bigint PRIMARY KEY,payload bytea NOT NULL)")
            db.execute(text("INSERT INTO neon_auth.synthetic_restored VALUES (1,:payload)"),
                       {"payload": b"private synthetic bytes"})
            db.exec_driver_sql("CREATE ROLE owned_reader NOLOGIN")
            db.exec_driver_sql("GRANT USAGE ON SCHEMA neon_auth TO owned_reader")
            db.exec_driver_sql("GRANT SELECT ON neon_auth.synthetic_restored TO owned_reader")
        yield engine
    finally:
        if engine is not None:
            engine.dispose()
        if (directory / "postmaster.pid").exists():
            _pg(pg_bin, "pg_ctl", "-D", directory, "-m", "immediate", "-w", "-t", "10", "stop")
        assert not (directory / "postmaster.pid").exists()
        assert directory.resolve() == directory and directory.parent == tmp_path
        shutil.rmtree(directory)
        (tmp_path / "postgres.log").unlink(missing_ok=True)


def test_owned_pg17_catalogue_data_sequence_acl_and_marker_are_independent(owned_pg, monkeypatch):
    # Only this test port substitutes local synthetic SQL for authenticated Neon.
    # creator/read-only settings are transport responsibilities; use fresh local engines
    # with explicit modes, while the real _connection transaction checks remain active.
    def engine(_pin, _deadline, *, write=False):
        return create_engine(owned_pg.url, poolclass=NullPool, isolation_level="REPEATABLE READ",
            connect_args={"connect_timeout": 5, "sslmode": "disable", "options":
                "-c statement_timeout=2000 -c lock_timeout=1000 -c default_transaction_read_only=" +
                ("off" if write else "on")})
    monkeypatch.setattr(c, "_engine", engine)
    before = c.capture(PIN)
    assert before.schema == c.schema27.SCHEMA
    assert any(t.schema == "neon_auth" and t.rows == 1 for t in before.tables)
    assert b"private synthetic bytes" not in c.canonical(asdict(before))
    marker = c.install_marker(PIN, before)
    assert c.verify_marker(PIN, marker)
    with pytest.raises(c.RestoreCloneDenied, match="qa_marker_still_present"):
        c.capture(PIN)
    with pytest.raises(c.RestoreCloneDenied, match="qa_marker_still_present"):
        # The fresh capture refuses before DDL, so the existing marker never replays.
        c.install_marker(PIN, before)
    with owned_pg.connect() as extra:
        extra.exec_driver_sql("SELECT 1")
        with pytest.raises(c.RestoreCloneDenied, match="other_qa_sessions_present"):
            c.verify_marker(PIN, marker)
    with owned_pg.begin() as db:
        db.exec_driver_sql('DROP SCHEMA "' + marker.schema + '" CASCADE')
    assert not c.verify_marker(PIN, marker)
    restored = c.capture(PIN)
    assert c.compare_restore(before, restored, marker)["matched"]
    with owned_pg.begin() as db:
        db.exec_driver_sql("REVOKE SELECT ON neon_auth.synthetic_restored FROM owned_reader")
    changed = c.capture(PIN)
    assert changed.catalogue_sha256 != before.catalogue_sha256
    with pytest.raises(c.RestoreCloneDenied, match="restored_state_changed"):
        c.compare_restore(before, changed, marker)
    with owned_pg.begin() as db:
        db.exec_driver_sql("GRANT SELECT ON neon_auth.synthetic_restored TO owned_reader")
        db.exec_driver_sql("UPDATE neon_auth.synthetic_restored SET payload='new bytes'::bytea")
        db.exec_driver_sql("SELECT setval('public.users_id_seq',29,true)")
    changed = c.capture(PIN)
    assert changed.tables != before.tables and changed.sequences != before.sequences
    with pytest.raises(c.RestoreCloneDenied, match="restored_state_changed"):
        c.compare_restore(before, changed, marker)
