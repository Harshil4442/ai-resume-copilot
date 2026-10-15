"""Real PG17 receiving-role privileges, using only UUID-owned local resources.

Execute the production ownership statements as an ordinary authenticated role.
This exercises SQL authorization, ownership and data preservation; it does not
replace or claim the production native identity/cloud/once admission checks.
"""
from __future__ import annotations

import ast
import copy
import os
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
from psycopg import sql
from sqlalchemy.engine import make_url

SOURCE = Path(__file__).resolve().parents[2] / "infra/gcp/fresh_source_authority.py"
PASSWORD = "synthetic-local-ownership-only"


def ownership_action(creator: str, owner: str, *, former_inherit: bool = False):
    """Use actual production SQL composition; substitute owned fixture names only."""
    tree = ast.parse(SOURCE.read_bytes())
    transition = next(node for node in tree.body if isinstance(node, ast.FunctionDef)
                      and node.name == "transition_source")
    handler = next(node for node in transition.body if isinstance(node, ast.Try))
    block = [node for node in handler.body if isinstance(node, ast.With)][1]
    start = next(index for index, node in enumerate(block.body)
                 if isinstance(node, ast.Expr) and "GRANT {} TO {} WITH SET TRUE" in ast.unparse(node))
    stop = next(index for index, node in enumerate(block.body)
                if isinstance(node, ast.Expr) and "SET ROLE {}" in ast.unparse(node))
    statements = copy.deepcopy(block.body[start:stop + 1])
    if former_inherit:
        # Reproduce the former failure, without an independent copy of the SQL.
        for statement in statements:
            for node in ast.walk(statement):
                if isinstance(node, ast.Constant) and node.value == "GRANT {} TO {} WITH INHERIT TRUE":
                    node.value = "GRANT {} TO {} WITH INHERIT FALSE"
        reassignment = next(index for index, node in enumerate(statements)
                            if "REASSIGN OWNED BY {} TO {}" in ast.unparse(node))
        if "WITH INHERIT FALSE" in ast.unparse(statements[reassignment + 1]):
            statements.pop(reassignment + 1)
    function = ast.FunctionDef(name="apply_ownership", args=ast.arguments(
        posonlyargs=[], args=[ast.arg(arg="connection")], kwonlyargs=[], kw_defaults=[], defaults=[]),
        body=statements, decorator_list=[])
    namespace = {"sql": sql, "OLD_NAME": creator, "OWNER_NAME": owner}
    exec(compile(ast.fix_missing_locations(ast.Module(body=[function], type_ignores=[])),
                 str(SOURCE), "exec"), namespace)
    return namespace["apply_ownership"]


@pytest.fixture
def owned_source():
    configured = os.getenv("HIREWIZ_TEST_POSTGRES_URL")
    if not configured:
        if os.getenv("HIREWIZ_TRANSFER_POSTGRES_REQUIRED") == "true":
            pytest.fail("CI requires the disposable PostgreSQL ownership regression")
        pytest.skip("Requires the dedicated disposable local PostgreSQL cluster")
    url = make_url(configured)
    if (url.drivername != "postgresql+psycopg" or url.host != "127.0.0.1"
            or url.port not in {55433, 55435} or url.database != "hirewiz_admission_test"
            or url.username != "hirewiz" or url.password != "hirewiz"):
        pytest.fail("Only the dedicated synthetic local fixture is accepted")
    token = uuid4().hex
    database, creator, owner = (prefix + token for prefix in ("hw_own_db_", "hw_own_actor_", "hw_own_target_"))
    transport = {"host": url.host, "port": url.port, "sslmode": "disable", "connect_timeout": 5}
    with psycopg.connect(dbname="postgres", user="hirewiz", password="hirewiz",
                         autocommit=True, **transport) as controller:
        assert 170000 <= controller.info.server_version < 180000
        try:
            controller.execute(sql.SQL("CREATE ROLE {} LOGIN INHERIT NOSUPERUSER CREATEDB CREATEROLE NOREPLICATION NOBYPASSRLS PASSWORD {}")
                               .format(sql.Identifier(creator), sql.Literal(PASSWORD)))
            controller.execute(sql.SQL("CREATE DATABASE {} OWNER {}")
                               .format(sql.Identifier(database), sql.Identifier(creator)))
            with psycopg.connect(dbname=database, user=creator, password=PASSWORD,
                                 autocommit=True, **transport) as connection:
                assert connection.execute("SELECT current_user,session_user,rolsuper FROM pg_catalog.pg_roles WHERE rolname=current_user").fetchone() == (creator, creator, False)
                connection.execute(sql.SQL("CREATE ROLE {} NOLOGIN NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS")
                                   .format(sql.Identifier(owner)))
                connection.execute("CREATE TABLE public.ownership_fixture (id integer GENERATED ALWAYS AS IDENTITY PRIMARY KEY, value text NOT NULL)")
                connection.execute("INSERT INTO public.ownership_fixture(value) VALUES ('synthetic original one'),('synthetic original two')")
                initial = connection.execute("SELECT m.admin_option,m.inherit_option,m.set_option FROM pg_catalog.pg_auth_members m JOIN pg_catalog.pg_roles r ON r.oid=m.roleid JOIN pg_catalog.pg_roles a ON a.oid=m.member WHERE r.rolname=%s AND a.rolname=%s", (owner, creator)).fetchall()
                assert initial == [(True, False, False)]
                yield connection, controller, database, creator, owner
        finally:
            controller.execute(sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(database)))
            for role in (owner, creator):
                controller.execute(sql.SQL("DROP ROLE IF EXISTS {}").format(sql.Identifier(role)))


def test_former_set_only_membership_cannot_receive_owned_objects(owned_source):
    connection, controller, database, creator, owner = owned_source
    with pytest.raises(psycopg.errors.InsufficientPrivilege) as error:
        with connection.transaction():
            ownership_action(creator, owner, former_inherit=True)(connection)
    assert error.value.sqlstate == "42501"
    assert controller.execute("SELECT pg_catalog.pg_get_userbyid(datdba) FROM pg_catalog.pg_database WHERE datname=%s", (database,)).fetchone() == (creator,)
    assert connection.execute("SELECT pg_catalog.pg_get_userbyid(relowner) FROM pg_catalog.pg_class WHERE oid='public.ownership_fixture'::regclass").fetchone() == (creator,)
    assert connection.execute("SELECT id,value FROM public.ownership_fixture ORDER BY id").fetchall() == [(1, "synthetic original one"), (2, "synthetic original two")]


def test_production_ownership_segment_transfers_without_retaining_inherited_privileges(owned_source):
    connection, controller, database, creator, owner = owned_source
    with connection.transaction():
        ownership_action(creator, owner)(connection)
        assert connection.execute("SELECT current_user,session_user").fetchone() == (owner, creator)
        assert connection.execute("SELECT id,value FROM public.ownership_fixture ORDER BY id").fetchall() == [(1, "synthetic original one"), (2, "synthetic original two")]
    connection.execute("RESET ROLE")
    assert controller.execute("SELECT pg_catalog.pg_get_userbyid(datdba) FROM pg_catalog.pg_database WHERE datname=%s", (database,)).fetchone() == (owner,)
    assert connection.execute("SELECT pg_catalog.pg_get_userbyid(relowner) FROM pg_catalog.pg_class WHERE oid='public.ownership_fixture'::regclass").fetchone() == (owner,)
    assert connection.execute("SELECT pg_catalog.pg_get_userbyid(relowner) FROM pg_catalog.pg_class WHERE oid='public.ownership_fixture_id_seq'::regclass").fetchone() == (owner,)
    assert connection.execute("SELECT pg_catalog.pg_has_role(%s,%s,'SET'),pg_catalog.pg_has_role(%s,%s,'USAGE')", (creator, owner, creator, owner)).fetchone() == (True, False)
    assert connection.execute("SELECT bool_or(m.inherit_option) FROM pg_catalog.pg_auth_members m JOIN pg_catalog.pg_roles r ON r.oid=m.roleid JOIN pg_catalog.pg_roles a ON a.oid=m.member WHERE r.rolname=%s AND a.rolname=%s", (owner, creator)).fetchone() == (False,)
    assert connection.execute("SELECT rolcanlogin,rolinherit,rolsuper,rolcreatedb,rolcreaterole,rolreplication,rolbypassrls FROM pg_catalog.pg_roles WHERE rolname=%s", (owner,)).fetchone() == (False, False, False, False, False, False, False)
    with psycopg.connect(dbname=database, user="hirewiz", password="hirewiz", **{
            "host": "127.0.0.1", "port": connection.info.port, "sslmode": "disable", "connect_timeout": 5}) as verifying:
        assert verifying.execute("SELECT id,value FROM public.ownership_fixture ORDER BY id").fetchall() == [(1, "synthetic original one"), (2, "synthetic original two")]
