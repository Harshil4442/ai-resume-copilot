"""Read-only additive21→25 schema contract; never grants release authority.

The native operator must independently bind database/cluster/roles, source image,
observed disabled service flags and its one-shot journal. This module validates
only its explicitly supplied transaction and reviewed on-disk migration sources.
"""

from __future__ import annotations

import ast
import hashlib
import json
from pathlib import Path

from sqlalchemy import text

from scripts import email_password_schema21_contract as previous

BEFORE = "20261023_0021"
SCHEMA = "20261028_0025"
CONTRACT = "hirewiz_unified_credit_schema25_v1"
REVISIONS = ("20261027_0022", "20261027_0023", "20261027_0024", SCHEMA)
DISABLED_FLAGS = (
    "UNIFIED_CREDITS_ENABLED",
    "UNIFIED_CREDIT_EXECUTION_ENABLED",
    "FREE_CORE_ENABLED",
)
TABLES = (
    "credit_campaigns",
    "credit_purchase_quotes",
    "credit_accounts",
    "credit_grants",
    "credit_reservations",
    "credit_allocations",
    "credit_events",
    "credit_service_quotes",
    "credit_execution_holds",
    "credit_execution_attempts",
    "free_core_reservations",
    "free_core_requests",
    "credit_discovery_batches",
    "credit_discovery_deliveries",
)
MANIFEST = Path(__file__).with_name("unified_credit_schema25_manifest.json")


class Schema25Denied(RuntimeError):
    """Only fixed nonsecret refusals escape the observation boundary."""


def need(value, reason):
    if not value:
        raise Schema25Denied(reason)


def namespace_name(namespace):
    previous.previous.namespace_name(namespace)


def marker(connection, namespace="public"):
    namespace_name(namespace)
    values = (
        connection.execute(text(f'SELECT version_num FROM "{namespace}".alembic_version LIMIT 2'))
        .scalars()
        .all()
    )
    need(len(values) == 1 and values[0] in {BEFORE, SCHEMA}, "schema25_exact_marker_required")
    return values[0]


def require_disabled(flags):
    need(
        type(flags) is dict
        and all(type(flags.get(key)) is str and flags[key] == "false" for key in DISABLED_FLAGS),
        "schema25_new_admission_must_remain_closed",
    )


def verify_source(root: Path):
    manifest = json.loads(MANIFEST.read_text())
    need(manifest.get("contract") == CONTRACT, "schema25_source_manifest_invalid")
    observed, predecessor = {}, BEFORE
    for revision in REVISIONS:
        paths = list((root / "backend/alembic/versions").glob(revision + "_*.py"))
        need(len(paths) == 1, "schema25_exact_migration_source_required")
        path = paths[0]
        raw = path.read_bytes()
        tree = ast.parse(raw)
        constants = {
            node.targets[0].id: ast.literal_eval(node.value)
            for node in tree.body
            if isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
            and node.targets[0].id in {"revision", "down_revision"}
        }
        need(
            constants == {"revision": revision, "down_revision": predecessor},
            "schema25_source_chain_invalid",
        )
        upgrade = [
            node
            for node in tree.body
            if isinstance(node, ast.FunctionDef) and node.name == "upgrade"
        ]
        need(len(upgrade) == 1 and bool(upgrade[0].body), "schema25_upgrade_missing")
        for statement in upgrade[0].body:
            need(
                isinstance(statement, ast.Expr)
                and isinstance(statement.value, ast.Call)
                and isinstance(statement.value.func, ast.Attribute)
                and isinstance(statement.value.func.value, ast.Name)
                and statement.value.func.value.id == "op"
                and statement.value.func.attr in {"create_table", "create_index"},
                "schema25_add_only_ddl_required",
            )
        observed[path.name] = hashlib.sha256(raw).hexdigest()
        predecessor = revision
    need(observed == manifest.get("migration_sources"), "schema25_source_pin_changed")
    return hashlib.sha256(
        json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def preserved_shape(connection, namespace_oid, namespace):
    """Reuse exact old catalog checks without pretending its21 marker is still current."""
    namespace_name(namespace)
    previous.previous.previous.verify_shape(connection, namespace_oid, namespace)
    previous.previous._upload_format(connection, namespace_oid, ("pdf", "docx", "tex", "texzip"))
    previous._columns(connection, namespace_oid)
    previous._constraints(connection, namespace_oid, namespace)
    previous._indexes(connection, namespace_oid)


def catalog_snapshot(connection, namespace_oid, namespace):
    namespace_name(namespace)
    params = {"ns": namespace_oid, "tables": list(TABLES)}
    queries = {
        "relations": "SELECT c.relname,c.relkind,c.relpersistence,c.relispartition,c.relrowsecurity,c.relforcerowsecurity FROM pg_catalog.pg_class c WHERE c.relnamespace=:ns AND c.relname=ANY(CAST(:tables AS text[])) ORDER BY c.relname",
        "columns": "SELECT c.relname,a.attname,pg_catalog.format_type(a.atttypid,a.atttypmod),a.attnotnull,a.attidentity,a.attgenerated,a.attcollation=t.typcollation,pg_catalog.pg_get_expr(d.adbin,d.adrelid) FROM pg_catalog.pg_class c JOIN pg_catalog.pg_attribute a ON a.attrelid=c.oid JOIN pg_catalog.pg_type t ON t.oid=a.atttypid LEFT JOIN pg_catalog.pg_attrdef d ON d.adrelid=c.oid AND d.adnum=a.attnum WHERE c.relnamespace=:ns AND c.relname=ANY(CAST(:tables AS text[])) AND a.attnum>0 AND NOT a.attisdropped ORDER BY c.relname,a.attnum",
        "constraints": "SELECT c.relname,k.conname,k.contype,k.convalidated,k.condeferrable,k.condeferred,k.conislocal,k.coninhcount,k.connoinherit,pg_catalog.pg_get_constraintdef(k.oid) FROM pg_catalog.pg_constraint k JOIN pg_catalog.pg_class c ON c.oid=k.conrelid WHERE c.relnamespace=:ns AND c.relname=ANY(CAST(:tables AS text[])) ORDER BY c.relname,k.conname",
        "indexes": "SELECT c.relname,ic.relname,i.indisunique,i.indisprimary,i.indisvalid,i.indisready,i.indislive,i.indimmediate,i.indnullsnotdistinct,pg_catalog.pg_get_indexdef(i.indexrelid) FROM pg_catalog.pg_index i JOIN pg_catalog.pg_class c ON c.oid=i.indrelid JOIN pg_catalog.pg_class ic ON ic.oid=i.indexrelid WHERE c.relnamespace=:ns AND c.relname=ANY(CAST(:tables AS text[])) ORDER BY c.relname,ic.relname",
        "triggers": "SELECT c.relname,t.tgname,t.tgenabled,pg_catalog.pg_get_triggerdef(t.oid) FROM pg_catalog.pg_trigger t JOIN pg_catalog.pg_class c ON c.oid=t.tgrelid WHERE c.relnamespace=:ns AND c.relname=ANY(CAST(:tables AS text[])) AND NOT t.tgisinternal ORDER BY c.relname,t.tgname",
        "rules": "SELECT c.relname,r.rulename,pg_catalog.pg_get_ruledef(r.oid) FROM pg_catalog.pg_rewrite r JOIN pg_catalog.pg_class c ON c.oid=r.ev_class WHERE c.relnamespace=:ns AND c.relname=ANY(CAST(:tables AS text[])) ORDER BY c.relname,r.rulename",
    }
    result = {}
    for name, query in queries.items():
        rows = connection.execute(text(query + " LIMIT 1025"), params).all()
        need(len(rows) <= 1024, "schema25_catalog_inventory_bound")
        result[name] = [
            [
                value.replace('"' + namespace + '".', "{namespace}.").replace(
                    namespace + ".", "{namespace}."
                )
                if isinstance(value, str)
                else value
                for value in row
            ]
            for row in rows
        ]
    return result


def catalog_digest(connection, namespace_oid, namespace):
    raw = json.dumps(
        catalog_snapshot(connection, namespace_oid, namespace),
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return hashlib.sha256(raw).hexdigest()


def verify_shape(connection, namespace_oid, namespace="public"):
    need(marker(connection, namespace) == SCHEMA, "schema25_target_marker_required")
    preserved_shape(connection, namespace_oid, namespace)
    manifest = json.loads(MANIFEST.read_text())
    need(
        catalog_digest(connection, namespace_oid, namespace) == manifest["new_catalog_sha256"],
        "schema25_new_catalog_shape_mismatch",
    )


def verify_before_shape(connection, namespace_oid, namespace="public"):
    need(marker(connection, namespace) == BEFORE, "schema25_exact_21_required_never_replay")
    previous.verify_shape(connection, namespace_oid, namespace)
    need(
        not catalog_snapshot(connection, namespace_oid, namespace)["relations"],
        "schema25_before_new_tables_present",
    )
