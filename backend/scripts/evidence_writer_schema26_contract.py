"""Exact additive25→26 observation, never migration or native identity authority.

The caller must independently establish the original TLS/cluster/database/role,
source/CI/Build and service guards. Historical schema25/v1/v2 readers remain
unchanged and deliberately refuse the26 marker.
"""
from __future__ import annotations

import ast
import hashlib
import json
from pathlib import Path

from sqlalchemy import text

from app.domains.career import evidence_fence_definition as definition
from app.domains.career import evidence_write_fence as fence
from scripts import schema25_app_release_contract as financial
from scripts import unified_credit_schema25_contract as previous

BEFORE = previous.SCHEMA
SCHEMA = "20261031_0026"
CONTRACT = "hirewiz_evidence_writer_schema26_v1"
MIGRATION = "backend/alembic/versions/20261031_0026_evidence_writer_fence.py"
MIGRATION_SHA256 = "5449e1b78a1db701a9b9bfe635a4d54aa268f79be0c253e098092619739cb324"
DEFINITION = "backend/app/domains/career/evidence_fence_definition.py"
DEFINITION_SHA256 = "1f87cfc84b63fd0b35b3c74bbc4069f094256d14a9f5bdc5af36bc4691ee2794"


class Schema26Denied(RuntimeError):
    pass


def need(value, reason):
    if not value:
        raise Schema26Denied("schema26_" + reason)


def verify_source(root: Path) -> str:
    previous.verify_source(root)
    observed = {}
    for name, expected in ((MIGRATION, MIGRATION_SHA256), (DEFINITION, DEFINITION_SHA256)):
        path = root / name
        need(path.is_file() and not path.is_symlink(), "source_required")
        observed[name] = hashlib.sha256(path.read_bytes()).hexdigest()
        need(observed[name] == expected, "source_changed")
    tree = ast.parse((root / MIGRATION).read_bytes())
    revisions = {node.targets[0].id: ast.literal_eval(node.value) for node in tree.body
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name)
        and node.targets[0].id in {"revision", "down_revision"}}
    need(revisions == {"revision": SCHEMA, "down_revision": BEFORE}, "migration_chain_changed")
    return hashlib.sha256(json.dumps(observed, sort_keys=True).encode()).hexdigest()


def marker(connection, namespace="public"):
    previous.namespace_name(namespace)
    rows = connection.execute(text(f'SELECT version_num FROM "{namespace}".alembic_version LIMIT 2')).scalars().all()
    need(rows == [SCHEMA], "exact_marker_required")
    return SCHEMA


def verify_shape(connection, namespace_oid=2200, namespace="public"):
    marker(connection, namespace)
    previous.preserved_shape(connection, namespace_oid, namespace)
    old = json.loads(previous.MANIFEST.read_text())
    need(previous.catalog_digest(connection, namespace_oid, namespace) == old["new_catalog_sha256"],
        "preserved_financial_catalog_changed")
    # The fixed observer normally uses pg_catalog; restore it even after refusal.
    savepoint = connection.begin_nested()
    try:
        connection.exec_driver_sql(f'SET LOCAL search_path="{namespace}"')
        fence.require_installed(connection, namespace)
    finally:
        savepoint.rollback()


def verify_writer_permissions(connection, *, runtime: str, migrator: str, namespace="public"):
    """Known-source capability boundary; no arbitrary SQL or owner bypass claim."""
    previous.namespace_name(namespace)
    params = {"namespace": namespace, "runtime": runtime, "migrator": migrator}
    rows = connection.execute(text(
        "SELECT has_table_privilege(:runtime,c.oid,'TRIGGER') AS may_trigger,"
        "has_table_privilege(:runtime,c.oid,'TRUNCATE') AS may_truncate,"
        "pg_has_role(:runtime,c.relowner,'USAGE') AS owns_table,"
        "has_schema_privilege(:runtime,n.oid,'CREATE') AS may_create,"
        "p.proowner=m.oid AS exact_function_owner,r.rolsuper,r.rolcreaterole,r.rolcreatedb,r.rolreplication,r.rolbypassrls "
        "FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace "
        "JOIN pg_catalog.pg_proc p ON p.pronamespace=n.oid AND p.proname='evidence_writer_fence' AND p.pronargs=0 "
        "JOIN pg_catalog.pg_roles r ON r.rolname=:runtime JOIN pg_catalog.pg_roles m ON m.rolname=:migrator "
        "WHERE n.nspname=:namespace AND c.relname='evidence_items' LIMIT 2"), params).mappings().all()
    need(len(rows) == 1 and dict(rows[0]) == {"may_trigger": False, "may_truncate": False, "owns_table": False,
        "may_create": False, "exact_function_owner": True, "rolsuper": False, "rolcreaterole": False,
        "rolcreatedb": False, "rolreplication": False, "rolbypassrls": False}, "writer_bypass_privilege")
    # A server/database/role default must never pre-arm the old immutable code.
    inherited = connection.execute(text("SELECT current_setting(:setting,true)"),
        {"setting": definition.SETTING}).scalar_one()
    need(inherited in (None, ""), "marker_already_inherited")
    defaults = connection.execute(text(
        "SELECT count(*) FROM pg_catalog.pg_db_role_setting d, LATERAL unnest(d.setconfig) s "
        "WHERE split_part(lower(s),'=',1)=:setting"), {"setting": definition.SETTING}).scalar_one()
    need(defaults == 0, "marker_default_unqualified")


def observe(connection, *, namespace_oid=2200):
    verify_shape(connection, namespace_oid)
    previous.verify_empty_new_tables(connection)
    for statement in financial.INVARIANTS:
        need(connection.execute(text(statement)).scalar_one() == 0, "live_financial_invariant_failed")
    financial.liability_rows(connection)
    return hashlib.sha256(json.dumps({"contract": CONTRACT, "schema": SCHEMA,
        "catalog": previous.catalog_digest(connection, namespace_oid, "public"),
        "fence_definition": DEFINITION_SHA256, "invariants": financial.INVARIANTS,
        "new_wallet_activity": "none"}, sort_keys=True).encode()).hexdigest()
