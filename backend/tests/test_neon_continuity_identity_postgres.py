"""Owned PG17 catalog proof. The fixture never connects to the continuity server."""

from __future__ import annotations

import hashlib
from uuid import uuid4

import psycopg
import pytest
from sqlalchemy.engine import make_url
from test_mail_budget_postgres import owned_pg_url as owned_pg_url

from scripts import neon_continuity_identity as continuity
from scripts import neon_direct_identity as retained


def test_purpose_is_distinct_and_validation_precedes_connect(monkeypatch):
    def unexpected_connection(*args, **kwargs):
        pytest.fail("invalid observations must not open a provider connection")

    monkeypatch.setattr(continuity.psycopg, "connect", unexpected_connection)
    for unbound in ("schema27_fresh_release_capture", "schema27_continuity_capture", None):
        with pytest.raises(retained.NeonIdentityDenied, match="purpose_invalid"):
            continuity.observe(unbound, observer_password="synthetic")
    monkeypatch.setenv("PGHOST", "127.0.0.1")
    with pytest.raises(retained.NeonIdentityDenied, match="connection_settings_invalid"):
        continuity.observe(continuity.Purpose.BEFORE_SCHEMA27, observer_password="synthetic")


def test_owned_catalog_checks_exact_schema_role_and_read_only(owned_pg_url, monkeypatch):
    server = make_url(owned_pg_url).set(drivername="postgresql").render_as_string(hide_password=False)
    name = "continuity_" + uuid4().hex[:12]
    actor = name + "_observer"
    with psycopg.connect(server, autocommit=True) as admin:
        admin.execute(f'CREATE ROLE "{actor}" LOGIN NOINHERIT')
        admin.execute(f'CREATE DATABASE "{name}"')
        try:
            url = make_url(server).set(database=name)
            with psycopg.connect(url.render_as_string(hide_password=False), autocommit=True) as setup:
                setup.execute("CREATE TABLE public.alembic_version (version_num VARCHAR(32) PRIMARY KEY)")
                setup.execute("INSERT INTO public.alembic_version VALUES (%s)", (retained.SCHEMA26_RELEASE_SCHEMA,))
                setup.execute(f'GRANT SELECT ON public.alembic_version TO "{actor}"')
                setup.execute(f'GRANT EXECUTE ON FUNCTION pg_catalog.pg_control_system() TO "{actor}"')
                native = setup.execute(
                    """
                    SELECT d.oid, current_user, r.oid, n.nspowner, c.system_identifier::text
                    FROM pg_catalog.pg_database d
                    JOIN pg_catalog.pg_roles r ON r.rolname = %s
                    JOIN pg_catalog.pg_namespace n ON n.nspname = 'public'
                    CROSS JOIN pg_catalog.pg_control_system() c
                    WHERE d.datname = pg_catalog.current_database()
                    """, (actor,),
                ).fetchone()
                database_oid, owner, actor_oid, namespace_owner, cluster = native
                monkeypatch.setattr(continuity, "DATABASE", name)
                monkeypatch.setattr(continuity, "DATABASE_OID", database_oid)
                monkeypatch.setattr(continuity, "DATABASE_OWNER", owner)
                monkeypatch.setattr(continuity, "OBSERVER", actor)
                monkeypatch.setattr(continuity, "OBSERVER_OID", actor_oid)
                monkeypatch.setattr(continuity, "NAMESPACE_OWNER_OID", namespace_owner)
                monkeypatch.setattr(continuity, "CLUSTER_SHA256", hashlib.sha256(cluster.encode()).hexdigest())

                # The fixture authenticates through its admin login and binds
                # the test identity at the PostgreSQL session layer. No TLS
                # or provider identity claim is made for this local connection.
                def probe(purpose, *, read_only=True, role=actor):
                    with psycopg.connect(url.render_as_string(hide_password=False)) as observer:
                        observer.read_only = read_only
                        observer.isolation_level = psycopg.IsolationLevel.REPEATABLE_READ
                        observer.execute(f'SET SESSION AUTHORIZATION "{role}"')
                        return continuity._observe_catalog(observer, purpose)

                before = continuity.Purpose.BEFORE_SCHEMA27
                after = continuity.Purpose.AFTER_SCHEMA27
                assert probe(before).schema == retained.SCHEMA26_RELEASE_SCHEMA
                with pytest.raises(retained.NeonIdentityDenied, match="exact_schema_mismatch"):
                    probe(after)
                with pytest.raises(retained.NeonIdentityDenied, match="catalog_target_mismatch"):
                    probe(before, read_only=False)
                with pytest.raises(retained.NeonIdentityDenied, match="catalog_target_mismatch"):
                    probe(before, role=owner)
                setup.execute("UPDATE public.alembic_version SET version_num = %s", (retained.SCHEMA27_RELEASE_SCHEMA,))
                assert probe(after).schema == retained.SCHEMA27_RELEASE_SCHEMA
                with pytest.raises(retained.NeonIdentityDenied, match="exact_schema_mismatch"):
                    probe(before)
                with monkeypatch.context() as changed:
                    changed.setattr(continuity, "CLUSTER_SHA256", "0" * 64)
                    with pytest.raises(retained.NeonIdentityDenied, match="catalog_target_mismatch"):
                        probe(after)

                with psycopg.connect(url.render_as_string(hide_password=False)) as no_tls:
                    with pytest.raises(retained.NeonIdentityDenied, match="tls_transport_unavailable"):
                        continuity._verify_transport(no_tls, {})
        finally:
            admin.execute(f'DROP DATABASE "{name}" WITH (FORCE)')
            admin.execute(f'DROP ROLE "{actor}"')
