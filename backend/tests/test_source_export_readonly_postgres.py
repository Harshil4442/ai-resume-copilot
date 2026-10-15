"""Actual disposable PG17 managed-style read grant and complete two-schema dump.

Only UUID-owned localhost databases/roles are changed. The source actor and its
surviving ADMIN grantor are ordinary non-superusers; no provider behavior is mocked.
This tests PostgreSQL semantics, not Neon's independent native permission hooks.
"""
from __future__ import annotations

import importlib
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
from psycopg import sql
from sqlalchemy.engine import make_url

from scripts import ci_database_transfer_clients as transfer_clients

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "infra/gcp"), str(ROOT / "backend")]
source = importlib.import_module("fresh_source_authority")
PASSWORD = "synthetic-owned-export-only"


def _tool(tool, arguments, *, database, actor, port, content=None):
    """Use installed PG17 clients, or the explicitly selected owned local fixture."""
    container = os.getenv("HIREWIZ_TEST_POSTGRES_CONTAINER")
    environment = {key: value for key, value in os.environ.items() if not key.startswith("PG")}
    environment.update(PGHOST="127.0.0.1", PGPORT=str(port), PGDATABASE=database,
                       PGUSER=actor, PGPASSWORD=PASSWORD if actor != "hirewiz" else "hirewiz", PGSSLMODE="disable")
    docker = shutil.which("docker") or "/Applications/Docker.app/Contents/Resources/bin/docker"
    if os.getenv("GITHUB_ACTIONS") == "true":
        # The transfer wrapper deliberately admits only its custom-format UUID
        # transfer contract. This separate regression needs the actual PG17
        # pg_dump/psql clients in the already-owned CI service instead.
        container = os.getenv("CI_POSTGRES_CONTAINER_ID", "")
        assert os.getenv("RUNNER_OS") == "Linux" and re.fullmatch(r"[a-f0-9]{64}", container)
        work = Path(os.environ["HIREWIZ_TRANSFER_WORKDIR"])
        config = work.parent / "docker-config"
        assert work.is_absolute() and work.is_dir() and not work.is_symlink()
        assert config.is_dir() and not config.is_symlink()
        prefix = [docker, "--config", str(config), "--host", "unix:///var/run/docker.sock"]
        observed = subprocess.run([*prefix, "inspect", "--format",
            "[{{json .Id}},{{json .Config.Image}},{{json .State.Running}},{{json .State.Health.Status}}]",
            container], capture_output=True, env=environment, timeout=10, check=True)
        assert len(observed.stdout) <= 4096
        assert json.loads(observed.stdout) == [container, transfer_clients.IMAGE, True, "healthy"]
        environment["PGPORT"] = "5432"
        command = [*prefix, "exec", "-i"]
        for name in ("PGHOST", "PGPORT", "PGDATABASE", "PGUSER", "PGPASSWORD", "PGSSLMODE"):
            command.extend(["--env", name])
        command.extend([container, tool, *arguments])
    elif container:
        assert re.fullmatch(r"hirewiz-source-export-local-[a-z0-9-]{1,48}", container)
        environment["PGPORT"] = "5432"
        command = [docker, "exec", "-i"]
        for name in ("PGHOST", "PGPORT", "PGDATABASE", "PGUSER", "PGPASSWORD", "PGSSLMODE"):
            command.extend(["--env", name])
        command.extend([container, tool, *arguments])
    else:
        executable = shutil.which(tool)
        assert executable, "Actual matching PG17 dump and psql clients are required"
        command = [executable, *arguments]
    return subprocess.run(command, input=content, capture_output=True,
                          env=environment, timeout=20, check=False)


@pytest.mark.parametrize("tool", ["pg_dump", "psql"])
def test_ci_export_client_routes_to_exact_service_without_transfer_wrapper(monkeypatch, tmp_path, tool):
    """Inert command selection only; the database test below proves actual SQL."""
    container = "c" * 64
    work, config = tmp_path / "work", tmp_path / "docker-config"
    work.mkdir()
    config.mkdir()
    for name, value in {"GITHUB_ACTIONS": "true", "RUNNER_OS": "Linux",
                        "CI_POSTGRES_CONTAINER_ID": container, "HIREWIZ_TRANSFER_WORKDIR": str(work),
                        "HIREWIZ_TRANSFER_PG_DUMP": "/never-select-the-restricted-transfer-wrapper"}.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setattr(shutil, "which", lambda name: "/owned-ci/docker" if name == "docker" else None)
    calls = []
    def run(command, **kwargs):
        calls.append((command, kwargs))
        output = (json.dumps([container, transfer_clients.IMAGE, True, "healthy"]).encode()
                  if "inspect" in command else b"inert-command-route-only")
        return subprocess.CompletedProcess(command, 0, output, b"")
    monkeypatch.setattr(subprocess, "run", run)
    arguments = ["--format=plain", "--no-owner", "--no-privileges", "--no-password"] if tool == "pg_dump" else ["-X", "--set", "ON_ERROR_STOP=1", "--no-password"]
    result = _tool(tool, arguments, database="hw_export_db_" + "a" * 20,
                   actor="hw_export_observer_" + "a" * 20, port=55433, content=None)
    assert result.returncode == 0 and len(calls) == 2
    assert all(command[:5] == ["/owned-ci/docker", "--config", str(config), "--host", "unix:///var/run/docker.sock"] for command, _ in calls)
    command, kwargs = calls[-1]
    assert command[-len(arguments)-2:] == [container, tool, *arguments]
    assert kwargs["env"]["PGPORT"] == "5432" and kwargs["env"]["PGUSER"].startswith("hw_export_observer_")
    assert PASSWORD not in command and "/never-select-the-restricted-transfer-wrapper" not in command


@pytest.mark.parametrize("change", ["id", "image", "running", "health"])
def test_ci_export_client_refuses_service_identity_drift_before_exec(monkeypatch, tmp_path, change):
    container = "c" * 64
    work, config = tmp_path / "work", tmp_path / "docker-config"
    work.mkdir()
    config.mkdir()
    for name, value in {"GITHUB_ACTIONS": "true", "RUNNER_OS": "Linux",
                        "CI_POSTGRES_CONTAINER_ID": container, "HIREWIZ_TRANSFER_WORKDIR": str(work)}.items():
        monkeypatch.setenv(name, value)
    native = [container, transfer_clients.IMAGE, True, "healthy"]
    native[{"id": 0, "image": 1, "running": 2, "health": 3}[change]] = {
        "id": "d" * 64, "image": "unreviewed:latest", "running": False, "health": "starting",
    }[change]
    calls = []
    def run(command, **kwargs):
        calls.append(command)
        assert "inspect" in command, "A changed service must never dispatch a PG client"
        return subprocess.CompletedProcess(command, 0, json.dumps(native).encode(), b"")
    monkeypatch.setattr(subprocess, "run", run)
    with pytest.raises(AssertionError):
        _tool("pg_dump", ["--format=plain"], database="hw_export_db_" + "a" * 20,
              actor="hw_export_observer_" + "a" * 20, port=55433)
    assert len(calls) == 1


@pytest.fixture
def owned_export(monkeypatch):
    configured = os.getenv("HIREWIZ_TEST_POSTGRES_URL")
    if not configured:
        if os.getenv("HIREWIZ_TRANSFER_POSTGRES_REQUIRED") == "true":
            pytest.fail("CI requires the complete managed-schema export regression")
        pytest.skip("Requires the dedicated disposable local PostgreSQL cluster")
    url = make_url(configured)
    if (url.drivername != "postgresql+psycopg" or url.host != "127.0.0.1"
            or url.port not in {55433, 55435} or url.database != "hirewiz_admission_test"
            or url.username != "hirewiz" or url.password != "hirewiz"):
        pytest.fail("Only the dedicated synthetic local fixture is accepted")
    token = uuid4().hex[:20]
    names = {key: f"hw_export_{key}_{token}" for key in ("actor", "grantor", "observer", "owner", "auth", "db", "restored")}
    connection_args = {"host": url.host, "port": url.port, "sslmode": "disable", "connect_timeout": 5}
    with psycopg.connect(dbname="postgres", user="hirewiz", password="hirewiz",
                         autocommit=True, **connection_args) as admin:
        assert 170000 <= admin.info.server_version < 180000
        try:
            for key in ("actor", "grantor", "observer", "owner", "auth"):
                attributes = ("LOGIN INHERIT CREATEDB CREATEROLE" if key == "actor" else
                              "NOLOGIN NOINHERIT CREATEDB CREATEROLE" if key == "grantor" else
                              "LOGIN NOINHERIT NOCREATEDB NOCREATEROLE" if key == "observer" else
                              "NOLOGIN NOINHERIT NOCREATEDB NOCREATEROLE")
                admin.execute(sql.SQL("CREATE ROLE {} " + attributes + " NOSUPERUSER NOREPLICATION NOBYPASSRLS PASSWORD {}")
                              .format(sql.Identifier(names[key]), sql.Literal(PASSWORD)))
            admin.execute(sql.SQL("GRANT pg_read_all_data TO {} WITH ADMIN TRUE, INHERIT TRUE, SET TRUE")
                          .format(sql.Identifier(names["grantor"])))
            admin.execute(sql.SQL("GRANT {} TO {} WITH ADMIN FALSE, INHERIT TRUE, SET TRUE")
                          .format(sql.Identifier(names["grantor"]), sql.Identifier(names["actor"])))
            admin.execute(sql.SQL("CREATE DATABASE {} OWNER {}")
                          .format(sql.Identifier(names["db"]), sql.Identifier(names["owner"])))
            admin.execute(sql.SQL("CREATE DATABASE {} OWNER hirewiz").format(sql.Identifier(names["restored"])))
            with psycopg.connect(dbname=names["db"], user="hirewiz", password="hirewiz",
                                 autocommit=True, **connection_args) as setup:
                setup.execute(sql.SQL("SET ROLE {}").format(sql.Identifier(names["owner"])))
                setup.execute("CREATE TABLE public.export_fixture (id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY, body text)")
                setup.execute("INSERT INTO public.export_fixture(body) VALUES ('synthetic public original')")
                setup.execute("REVOKE CREATE ON SCHEMA public FROM PUBLIC")
                setup.execute(sql.SQL("GRANT USAGE ON SCHEMA public TO {}").format(sql.Identifier(names["observer"])))
                setup.execute(sql.SQL("GRANT SELECT ON ALL TABLES IN SCHEMA public TO {}").format(sql.Identifier(names["observer"])))
                setup.execute(sql.SQL("GRANT SELECT ON ALL SEQUENCES IN SCHEMA public TO {}").format(sql.Identifier(names["observer"])))
                setup.execute("RESET ROLE")
                setup.execute(sql.SQL("CREATE SCHEMA neon_auth AUTHORIZATION {}").format(sql.Identifier(names["auth"])))
                setup.execute(sql.SQL("SET ROLE {}").format(sql.Identifier(names["auth"])))
                setup.execute("CREATE TABLE neon_auth.export_identity (id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY, body text)")
                setup.execute("INSERT INTO neon_auth.export_identity(body) VALUES ('synthetic managed auth original')")
                setup.execute("RESET ROLE")
                oids = dict(setup.execute("SELECT rolname,oid FROM pg_catalog.pg_roles WHERE rolname=ANY(%s)", (list(names.values()),)).fetchall())
            for constant, value in {"OLD_NAME": names["actor"], "OBSERVER_NAME": names["observer"],
                                    "OBSERVER_OID": oids[names["observer"]], "EXPORT_GRANTOR": names["grantor"],
                                    "EXPORT_GRANTOR_OID": oids[names["grantor"]]}.items():
                monkeypatch.setattr(source, constant, value)
            yield names, admin, connection_args, url.port
        finally:
            for key in ("db", "restored"):
                admin.execute(sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(names[key])))
            for key in ("observer", "actor", "grantor", "owner", "auth"):
                admin.execute(sql.SQL("DROP ROLE IF EXISTS {}").format(sql.Identifier(names[key])))


def test_public_only_reader_fails_full_backup_then_surviving_readonly_grant_preserves_all_schemas(owned_export):
    names, admin, arguments, port = owned_export
    with psycopg.connect(dbname=names["db"], user=names["observer"], password=PASSWORD,
                         autocommit=True, **arguments) as observer:
        assert observer.execute("SELECT body FROM public.export_fixture").fetchall() == [("synthetic public original",)]
        with pytest.raises(psycopg.errors.InsufficientPrivilege) as denied:
            observer.execute("SELECT * FROM neon_auth.export_identity")
        assert denied.value.sqlstate == "42501"
    failed = _tool("pg_dump", ["--format=plain", "--no-owner", "--no-privileges", "--no-password"],
                   database=names["db"], actor=names["observer"], port=port)
    assert failed.returncode != 0 and b"permission denied for schema neon_auth" in failed.stderr
    with psycopg.connect(dbname=names["db"], user=names["actor"], password=PASSWORD,
                         autocommit=True, **arguments) as actor:
        assert actor.execute("SELECT rolsuper FROM pg_catalog.pg_roles WHERE rolname=current_user").fetchone() == (False,)
        source.verify_export_grant_authority(actor)
        with actor.transaction():
            source.grant_export_readonly(actor)
            raise psycopg.Rollback()  # Forced rollback never changes source export capability.
        source.verify_export_grant_authority(actor)
        with actor.transaction():
            source.grant_export_readonly(actor)
        assert actor.execute("SELECT current_user,session_user").fetchone() == (names["actor"], names["actor"])
    # No grant depends on the retired actor. Removing it must leave export intact.
    admin.execute(sql.SQL("DROP ROLE {}").format(sql.Identifier(names["actor"])))
    with psycopg.connect(dbname=names["db"], user=names["observer"], password=PASSWORD,
                         autocommit=True, **arguments) as observer:
        source.verify_export_readonly(observer)
        assert observer.execute("SELECT body FROM neon_auth.export_identity").fetchall() == [("synthetic managed auth original",)]
        assert observer.execute(
            "SELECT bool_or(pg_catalog.has_table_privilege(current_user,c.oid,'INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER')) "
            "FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace "
            "WHERE n.nspname IN ('public','neon_auth') AND c.relkind='r'").fetchone() == (False,)
        assert observer.execute(
            "SELECT bool_or(pg_catalog.has_sequence_privilege(current_user,c.oid,'USAGE,UPDATE')) "
            "FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace "
            "WHERE n.nspname IN ('public','neon_auth') AND c.relkind='S'").fetchone() == (False,)
        for command in ("INSERT INTO public.export_fixture(body) VALUES ('must not write')",
                        "INSERT INTO neon_auth.export_identity(body) VALUES ('must not write')",
                        "CREATE TABLE public.must_not_create(id int)", "CREATE TABLE neon_auth.must_not_create(id int)",
                        "SET ROLE pg_read_all_data", "GRANT pg_read_all_data TO CURRENT_USER"):
            with pytest.raises(psycopg.errors.InsufficientPrivilege) as denied:
                with observer.transaction():
                    observer.execute(command)
            assert denied.value.sqlstate == "42501"
        catalog = source.transfer._catalog(observer, source.transfer.Limits())
        assert catalog["schemas"] == [("neon_auth",), ("public",)]
        assert {row[:2] for row in catalog["relations"]} == {("public", "export_fixture"), ("neon_auth", "export_identity")}
    dumped = _tool("pg_dump", ["--format=plain", "--no-owner", "--no-privileges", "--no-password"],
                   database=names["db"], actor=names["observer"], port=port)
    assert dumped.returncode == 0 and 100 < len(dumped.stdout) < 1_000_000
    assert b"COPY public.export_fixture" in dumped.stdout and b"COPY neon_auth.export_identity" in dumped.stdout
    restored = _tool("psql", ["-X", "--set", "ON_ERROR_STOP=1", "--no-password"], database=names["restored"],
                     actor="hirewiz", port=port, content=dumped.stdout)
    assert restored.returncode == 0, "The complete owned two-schema dump must restore without exclusions"
    with psycopg.connect(dbname=names["restored"], user="hirewiz", password="hirewiz", **arguments) as restored_db:
        assert restored_db.execute("SELECT body FROM public.export_fixture").fetchall() == [("synthetic public original",)]
        assert restored_db.execute("SELECT body FROM neon_auth.export_identity").fetchall() == [("synthetic managed auth original",)]
