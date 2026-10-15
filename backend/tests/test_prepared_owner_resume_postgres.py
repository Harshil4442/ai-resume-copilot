"""Real PG17 unused-owner predicates on UUID-owned disposable local resources.

No native-source credentials, cloud API, migration or customer database access.
The production wrapper separately binds its fixed source/OIDs/previous attempt.
"""
from __future__ import annotations

import importlib
import os
import sys
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
from psycopg import sql
from sqlalchemy.engine import make_url

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "infra/gcp"), str(ROOT / "backend")]
module = importlib.import_module("fresh_source_authority")


@pytest.fixture
def catalogue():
    configured = os.getenv("HIREWIZ_TEST_POSTGRES_URL")
    if not configured:
        if os.getenv("HIREWIZ_TRANSFER_POSTGRES_REQUIRED") == "true":
            pytest.fail("CI requires actual prepared-owner PostgreSQL controls")
        pytest.skip("Requires the dedicated disposable local PostgreSQL cluster")
    url = make_url(configured)
    if (url.drivername != "postgresql+psycopg" or url.host != "127.0.0.1"
            or url.port not in {55433, 55435} or url.database != "hirewiz_admission_test"
            or url.username != "hirewiz" or url.password != "hirewiz"):
        pytest.fail("Only the dedicated synthetic local fixture is accepted")
    nonce = uuid4().hex
    database, creator, owner, outsider = (
        prefix + nonce for prefix in ("hw_resume_", "hw_creator_", "hw_owner_", "hw_other_")
    )
    common = dict(host=url.host, port=url.port, user="hirewiz", password="hirewiz",
                  sslmode="disable", connect_timeout=5)
    active = None
    with psycopg.connect(dbname="postgres", autocommit=True, **common) as controller:
        assert 170000 <= controller.info.server_version < 180000
        controller.execute(sql.SQL("CREATE ROLE {} LOGIN CREATEROLE NOINHERIT PASSWORD 'synthetic-resume-only'").format(sql.Identifier(creator)))
        controller.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(database)))
        try:
            with psycopg.connect(dbname=database, user=creator, password="synthetic-resume-only",
                                 **{key: value for key, value in common.items() if key not in {"user", "password"}},
                                 autocommit=True) as creating:
                creating.execute(sql.SQL("CREATE ROLE {} NOLOGIN NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS").format(sql.Identifier(owner)))
            with psycopg.connect(dbname=database, autocommit=True, **common) as connection:
                owner_oid = connection.execute("SELECT oid FROM pg_catalog.pg_roles WHERE rolname=%s", (owner,)).fetchone()[0]
                creator_oid = connection.execute("SELECT oid FROM pg_catalog.pg_roles WHERE rolname=%s", (creator,)).fetchone()[0]
                def activate():
                    nonlocal active
                    connection.execute(sql.SQL("ALTER ROLE {} LOGIN PASSWORD 'synthetic-owner-only'").format(sql.Identifier(owner)))
                    active = psycopg.connect(dbname=database, user=owner, password="synthetic-owner-only",
                        **{key: value for key, value in common.items() if key not in {"user", "password"}}, autocommit=True)
                    connection.execute(sql.SQL("ALTER ROLE {} NOLOGIN").format(sql.Identifier(owner)))
                yield connection, owner_oid, owner, creator_oid, creator, outsider, activate
        finally:
            if active is not None:
                active.close()
            controller.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(database)))
            for role in (owner, outsider, creator):
                controller.execute(sql.SQL("DROP ROLE IF EXISTS {}").format(sql.Identifier(role)))


@pytest.mark.parametrize("drift", [None, "login", "oid", "set", "membership", "ownership", "acl", "default_acl", "setting", "session"])
def test_real_unused_role_and_authority_drift_are_checked_without_mutation(catalogue, drift):
    connection, owner_oid, owner, creator_oid, creator, outsider, activate = catalogue
    if drift == "login":
        connection.execute(sql.SQL("ALTER ROLE {} LOGIN").format(sql.Identifier(owner)))
    elif drift == "oid":
        owner_oid += 1
    elif drift == "set":
        connection.execute(sql.SQL("GRANT {} TO {} WITH SET TRUE").format(sql.Identifier(owner), sql.Identifier(creator)))
    elif drift == "membership":
        connection.execute(sql.SQL("CREATE ROLE {} NOLOGIN").format(sql.Identifier(outsider)))
        connection.execute(sql.SQL("GRANT {} TO {} WITH ADMIN FALSE,INHERIT FALSE,SET FALSE").format(sql.Identifier(owner), sql.Identifier(outsider)))
    elif drift in {"ownership", "acl"}:
        connection.execute("CREATE TABLE public.resume_predicate_fixture(id integer)")
        if drift == "ownership":
            connection.execute(sql.SQL("ALTER TABLE public.resume_predicate_fixture OWNER TO {}").format(sql.Identifier(owner)))
        else:
            connection.execute(sql.SQL("GRANT SELECT ON public.resume_predicate_fixture TO {}").format(sql.Identifier(owner)))
    elif drift == "default_acl":
        connection.execute(sql.SQL("ALTER DEFAULT PRIVILEGES FOR ROLE {} GRANT SELECT ON TABLES TO {}").format(sql.Identifier(owner), sql.Identifier(creator)))
    elif drift == "setting":
        connection.execute(sql.SQL("ALTER ROLE {} SET statement_timeout='1s'").format(sql.Identifier(owner)))
    elif drift == "session":
        activate()
    with connection.transaction():
        connection.execute("SET TRANSACTION READ ONLY")
        connection.execute("SET LOCAL statement_timeout='1000ms'")
        if drift:
            with pytest.raises(module.SourceActionDenied):
                module._unused_owner_catalogue(connection, owner_oid, owner, creator_oid)
        else:
            module._unused_owner_catalogue(connection, owner_oid, owner, creator_oid)
            assert connection.execute("SHOW transaction_read_only").fetchone() == ("on",)
