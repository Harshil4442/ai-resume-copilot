"""Real two-database PG17 transfer; only UUID-owned disposable local databases.

Set HIREWIZ_TEST_POSTGRES_URL and matching PG17 client paths. No provider,
production URL, app aggregate import or old database mutation is permitted.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import time
from dataclasses import replace
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
from psycopg import sql
from sqlalchemy.engine import make_url

from scripts import database_transfer as transfer


def _digest(path: Path) -> str:
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


@pytest.fixture
def databases(tmp_path):
    configured = os.getenv("HIREWIZ_TEST_POSTGRES_URL")
    if not configured:
        if os.getenv("HIREWIZ_TRANSFER_POSTGRES_REQUIRED") == "true":
            pytest.fail("CI requires every native PostgreSQL transfer case")
        pytest.skip("Requires the existing dedicated disposable PostgreSQL cluster")
    url = make_url(configured)
    if (url.drivername != "postgresql+psycopg" or url.host != "127.0.0.1"
            or url.port not in {55433, 55435} or url.database != "hirewiz_admission_test"
            or url.username != "hirewiz" or url.password != "hirewiz"):
        pytest.fail("Transfer tests accept only the dedicated synthetic local fixture")
    clients = [os.getenv("HIREWIZ_TRANSFER_PG_DUMP") or shutil.which("pg_dump"),
               os.getenv("HIREWIZ_TRANSFER_PG_RESTORE") or shutil.which("pg_restore")]
    if any(path is None for path in clients):
        pytest.fail("PG17 dump/restore clients are required for genuine transfer acceptance")
    tools = transfer.PostgresTools(*(Path(path).resolve() for path in clients),
                                   *(_digest(Path(path).resolve()) for path in clients))
    common = dict(host=url.host, port=url.port, user=url.username, password=url.password,
                  sslmode="disable", connect_timeout=5)
    source_name = "hw_transfer_" + uuid4().hex + "_source"
    target_name = source_name.removesuffix("source") + "target"
    writer = "hw_transfer_writer_" + uuid4().hex
    migrator = "hw_transfer_migrator_" + uuid4().hex
    writer_password = "synthetic-disposable-only"
    configured_work = os.getenv("HIREWIZ_TRANSFER_WORKDIR")
    if configured_work:
        root = Path(configured_work)
        if (not root.is_absolute() or root.is_symlink() or not root.is_dir()
                or root.stat().st_uid != os.getuid() or root.stat().st_mode & 0o777 != 0o700):
            pytest.fail("Transfer work root must be private and owned by this test runner")
        workdir = root / (source_name + "_attempt")
    else:
        workdir = tmp_path / "attempt"
    workdir.mkdir(mode=0o700)
    with psycopg.connect(dbname="postgres", autocommit=True, **common) as admin:
        assert 170000 <= admin.info.server_version < 180000
        admin.execute(sql.SQL("CREATE ROLE {} LOGIN PASSWORD {} ").format(sql.Identifier(writer), sql.Literal(writer_password)))
        admin.execute(sql.SQL("CREATE ROLE {} NOLOGIN").format(sql.Identifier(migrator)))
        admin.execute(sql.SQL("ALTER ROLE {} SET search_path=public,pg_catalog").format(sql.Identifier(writer)))
        admin.execute(sql.SQL("GRANT {} TO {} WITH ADMIN FALSE, INHERIT FALSE, SET TRUE").format(
            sql.Identifier(migrator), sql.Identifier(writer),
        ))
        admin.execute(sql.SQL("CREATE DATABASE {} OWNER {}").format(sql.Identifier(source_name), sql.Identifier(url.username)))
        admin.execute(sql.SQL("CREATE DATABASE {} OWNER {}").format(sql.Identifier(target_name), sql.Identifier(writer)))
        try:
            with psycopg.connect(dbname=target_name, **common) as target_admin:
                target_admin.execute("REVOKE ALL ON FUNCTION pg_catalog.pg_control_system() FROM PUBLIC")
                target_admin.execute(sql.SQL("GRANT EXECUTE ON FUNCTION pg_catalog.pg_control_system() TO {}").format(sql.Identifier(writer)))
                target_admin.execute(sql.SQL("GRANT USAGE,CREATE ON SCHEMA public TO {}").format(sql.Identifier(migrator)))
                migrator_oid = target_admin.execute("SELECT oid FROM pg_roles WHERE rolname=%s", (migrator,)).fetchone()[0]
            def access(name, actor, password):
                with psycopg.connect(dbname=name, user=actor, password=password,
                                     **{key: value for key, value in common.items() if key not in {"user", "password"}}) as connection:
                    row = connection.execute(
                        "SELECT d.oid,r.oid,(SELECT system_identifier::text FROM pg_control_system()) "
                        "FROM pg_database d JOIN pg_roles r ON r.rolname=current_user WHERE d.datname=current_database()"
                    ).fetchone()
                identity = transfer.NativeDatabaseIdentity(url.host, url.port, name,
                    hashlib.sha256(row[2].encode()).hexdigest(), row[0], actor, row[1], "", "disposable-local")
                return transfer.BoundDatabase(identity, password)
            source = access(source_name, url.username, url.password)
            target = access(target_name, writer, writer_password)
            target = replace(target, restore_role=transfer.NativeRestoreRole(migrator, migrator_oid))
            with psycopg.connect(dbname=source_name, **common) as connection:
                connection.execute(sql.SQL("ALTER SCHEMA public OWNER TO {}").format(sql.Identifier(url.username)))
                connection.execute("CREATE TABLE alembic_version(version_num varchar(32) PRIMARY KEY)")
                connection.execute("INSERT INTO alembic_version VALUES (%s)", (transfer.SCHEMA9,))
                connection.execute("CREATE TABLE users(id serial PRIMARY KEY,credits integer NOT NULL,email text UNIQUE)")
                connection.execute("CREATE TABLE payment_orders(id serial PRIMARY KEY,user_id integer REFERENCES users(id),currency text,amount_minor bigint,provider_key_id text,accepted_terms jsonb)")
                connection.execute("CREATE TABLE resumes(id serial PRIMARY KEY,user_id integer REFERENCES users(id),artifact_ref text,payload bytea)")
                connection.execute("CREATE TABLE duplicates(value text,other integer)")
                connection.execute("CREATE TABLE public.zz_collision(id integer)")
                connection.execute("CREATE VIEW balances AS SELECT id,credits FROM public.users")
                connection.execute("INSERT INTO users(credits,email) VALUES(17,'synthetic@example.invalid')")
                connection.execute("INSERT INTO payment_orders(user_id,currency,amount_minor,provider_key_id,accepted_terms) VALUES(1,'INR',99000,'synthetic-original-key','{\"sku\":\"retired-pack\",\"amount_minor\":99000}')")
                connection.execute("INSERT INTO resumes(user_id,artifact_ref,payload) VALUES(1,'synthetic/unchanged.docx',%s)", (b"\x00\xff\ncomplete-source-bytes",))
                connection.execute("INSERT INTO duplicates VALUES('नमस्ते · NULL',NULL),('नमस्ते · NULL',NULL),(NULL,0)")
                connection.execute("SELECT setval('public.users_id_seq',81,true)")
                connection.execute("SELECT lo_from_bytea(24001,%s)", (b"\x00\xfflarge-object",))
            yield source, target, tools, workdir, common
        finally:
            # Owned disposable databases only; this cleanup is never in product.
            for name in (source_name, target_name):
                admin.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(name)))
            admin.execute(sql.SQL("DROP ROLE {}").format(sql.Identifier(writer)))
            admin.execute(sql.SQL("DROP ROLE {}").format(sql.Identifier(migrator)))
            if configured_work:
                shutil.rmtree(workdir)


def test_full_transfer_preserves_financial_artifact_duplicate_and_large_object_rows(databases):
    source, target, tools, workdir, _ = databases
    result = transfer.transfer(source, target, tools, workdir)
    assert result["transfer"] == "verified" and result["schema"] == transfer.SCHEMA9
    assert result["table_rows"]["public.duplicates"] == 3
    assert not result["production_released"] and not result["native_cut_observed"]
    with target.connect() as connection:
        assert connection.execute("SHOW search_path").fetchone() == ("pg_catalog",)
        assert connection.execute("SELECT amount_minor,provider_key_id,accepted_terms->>'sku' FROM public.payment_orders").fetchone() == (99000, "synthetic-original-key", "retired-pack")
        assert connection.execute("SELECT artifact_ref,payload FROM public.resumes").fetchone() == ("synthetic/unchanged.docx", b"\x00\xff\ncomplete-source-bytes")
        assert connection.execute("SELECT last_value,is_called FROM public.users_id_seq").fetchone() == (81, True)
        assert connection.execute("SELECT lo_get(24001)").fetchone()[0] == b"\x00\xfflarge-object"
        assert connection.execute("SELECT pg_get_userbyid(relowner) FROM pg_class WHERE oid='public.users'::regclass").fetchone()[0] == target.restore_role.name
        assert connection.execute("SELECT current_user,session_user").fetchone() == (target.restore_role.name, target.identity.actor)
        assert connection.execute("SELECT rolcanlogin FROM pg_roles WHERE rolname=%s", (target.restore_role.name,)).fetchone() == (False,)
        assert connection.execute("SELECT has_function_privilege(current_user,'pg_catalog.pg_control_system()','EXECUTE')").fetchone() == (False,)
        assert connection.execute("SELECT admin_option,inherit_option,set_option FROM pg_auth_members WHERE roleid=%s AND member=%s",
                                  (target.restore_role.oid, target.identity.actor_oid)).fetchone() == (False, False, True)
    with source.connect() as connection:
        assert connection.execute("SELECT credits FROM public.users").fetchone() == (17,)


def test_exporter_snapshot_excludes_a_concurrent_later_source_update(databases, monkeypatch):
    source, target, tools, workdir, common = databases
    command = transfer._command
    changed = False
    def update_after_exported_snapshot(args, *rest, **kwargs):
        nonlocal changed
        if args[0] == str(tools.dump):
            with psycopg.connect(dbname=source.identity.database, **common) as writer:
                writer.execute("UPDATE public.users SET credits=777")
            changed = True
        return command(args, *rest, **kwargs)
    monkeypatch.setattr(transfer, "_command", update_after_exported_snapshot)
    result = transfer.transfer(source, target, tools, workdir)
    assert changed and result["purpose"] == "rehearsal" and not result["native_cut_observed"]
    with target.connect() as connection:
        assert connection.execute("SELECT credits FROM public.users").fetchone() == (17,)
    with source.connect() as connection:
        assert connection.execute("SELECT credits FROM public.users").fetchone() == (777,)


@pytest.mark.parametrize("kind", ["table", "type", "collation"])
def test_occupied_destination_is_preserved_without_a_restore_attempt(databases, kind):
    source, target, tools, workdir, _ = databases
    with target.connect() as connection:
        connection.rollback()
        connection.execute("SET default_transaction_read_only=off")
        connection.commit()
        if kind == "table":
            connection.execute("CREATE TABLE public.existing(id integer)")
            connection.execute("INSERT INTO public.existing VALUES(44)")
        elif kind == "type":
            connection.execute("CREATE TYPE public.existing AS ENUM ('kept')")
        else:
            connection.execute("CREATE COLLATION public.existing (provider=builtin,locale='C')")
    with pytest.raises(transfer.TransferDenied, match="^destination_not_empty$"):
        transfer.transfer(source, target, tools, workdir)
    assert not (workdir / "transfer.started").exists()
    with target.connect() as connection:
        if kind == "table":
            assert connection.execute("SELECT id FROM public.existing").fetchone() == (44,)
        elif kind == "type":
            assert connection.execute("SELECT enumlabel FROM pg_enum WHERE enumtypid='public.existing'::regtype").fetchone() == ("kept",)
        else:
            assert connection.execute("SELECT colllocale FROM pg_collation WHERE collname='existing' AND collnamespace='public'::regnamespace").fetchone() == ("C",)


def test_atomic_restore_failure_leaves_no_partial_transferred_tables(databases, monkeypatch):
    source, target, tools, workdir, _ = databases
    command = transfer._command
    def collision_before_restore(args, *rest, **kwargs):
        if args[0] == str(tools.restore) and "--single-transaction" in args:
            with target.connect() as collision:
                collision.rollback()
                collision.execute("SET default_transaction_read_only=off")
                collision.commit()
                collision.execute("CREATE TABLE public.zz_collision(id integer)")
                collision.execute("INSERT INTO public.zz_collision VALUES(52)")
        return command(args, *rest, **kwargs)
    monkeypatch.setattr(transfer, "_command", collision_before_restore)
    with pytest.raises(transfer.TransferDenied, match="^postgres_command_failed_reconcile_before_retry$"):
        transfer.transfer(source, target, tools, workdir)
    assert (workdir / "transfer.started").exists()
    with target.connect() as connection:
        assert connection.execute("SELECT to_regclass('public.users')").fetchone() == (None,)
        assert connection.execute("SELECT id FROM public.zz_collision").fetchone() == (52,)
    with pytest.raises(transfer.TransferDenied, match="^attempt_already_started_reconcile_before_retry$"):
        transfer.transfer(source, target, tools, workdir)


@pytest.mark.parametrize("tamper,reason", [
    ("UPDATE public.users SET credits=16", "restored_row_mismatch"),
    ("DELETE FROM public.duplicates WHERE ctid=(SELECT ctid FROM public.duplicates LIMIT 1)", "restored_row_mismatch"),
    ("SELECT setval('public.users_id_seq',82,true)", "restored_sequence_mismatch"),
    ("SELECT lo_put(24001,0,'changed'::bytea)", "restored_large_object_mismatch"),
])
def test_exact_comparison_catches_credit_duplicate_sequence_or_blob_drift(databases, tmp_path, tamper, reason):
    source, target, tools, workdir, _ = databases
    transfer.transfer(source, target, tools, workdir)
    with target.connect() as connection:
        connection.rollback()
        connection.execute("SET default_transaction_read_only=off")
        connection.commit()
        connection.execute(tamper)
    comparison = tmp_path / "compare"
    comparison.mkdir(mode=0o700)
    with source.connect() as left, target.connect() as right:
        with pytest.raises(transfer.TransferDenied, match="^" + reason + "$"):
            transfer.compare_content(left, right, comparison, transfer.Limits(), time.monotonic() + 30)


def test_wrong_database_pin_alias_and_final_authority_are_refused(databases):
    source, target, tools, workdir, _ = databases
    with pytest.raises(transfer.TransferDenied, match="^source_destination_alias$"):
        transfer.transfer(source, source, tools, workdir)
    wrong = replace(target, identity=replace(target.identity, database_oid=target.identity.database_oid + 1))
    with pytest.raises(transfer.TransferDenied, match="^native_connection_refused$"):
        transfer.transfer(source, wrong, tools, workdir)
    with pytest.raises(transfer.TransferDenied, match="^final_native_cut_required$"):
        transfer.transfer(source, target, tools, workdir, purpose="final")
    assert not (workdir / "transfer.started").exists()
