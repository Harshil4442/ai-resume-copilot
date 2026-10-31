"""Exact additive25→26 observation, never migration or native identity authority.

The caller must independently establish the original TLS/cluster/database/role,
source/CI/Build and service guards. Historical schema25/v1/v2 readers remain
unchanged and deliberately refuse the26 marker.
"""
from __future__ import annotations

import ast
import hashlib
import json
import re
from pathlib import Path

from sqlalchemy import text

from app.domains.career import evidence_fence_definition as definition
from app.domains.career import evidence_write_fence as fence
from scripts import schema25_app_release_contract as financial
from scripts import unified_credit_schema25_contract as previous

reset21 = previous.previous
browser19 = reset21.previous.previous

BEFORE = previous.SCHEMA
SCHEMA = "20261031_0026"
CONTRACT = "hirewiz_evidence_writer_schema26_v1"
MIGRATION = "backend/alembic/versions/20261031_0026_evidence_writer_fence.py"
MIGRATION_SHA256 = "b69c7a245d5dda60fb7b508ed868d8e705b49e4a2c1c43cf3dd77c5dccb7d651"
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


def _reset_columns(connection, namespace_oid):
    rows = connection.execute(text(
        "SELECT c.relname,c.relkind,c.relpersistence,c.relispartition,a.attname,"
        "pg_catalog.format_type(a.atttypid,a.atttypmod) AS column_type,a.attnotnull,a.attidentity,a.attgenerated,"
        "a.attcollation=t.typcollation AS default_collation,pg_catalog.pg_get_expr(d.adbin,d.adrelid) AS column_default "
        "FROM pg_catalog.pg_class c JOIN pg_catalog.pg_attribute a ON a.attrelid=c.oid "
        "JOIN pg_catalog.pg_type t ON t.oid=a.atttypid LEFT JOIN pg_catalog.pg_attrdef d ON d.adrelid=c.oid AND d.adnum=a.attnum "
        "WHERE c.relnamespace=:namespace AND a.attnum>0 AND NOT a.attisdropped AND "
        "(c.relname='password_reset_tokens' OR (c.relname='users' AND a.attname='credential_epoch')) "
        "ORDER BY c.oid,a.attnum LIMIT 17"), {"namespace": namespace_oid}).mappings().all()
    expected: dict[tuple[str, str], tuple[str, bool, str | None]] = {("password_reset_tokens", name): (kind, name not in {"native_revision_sha256", "consumed_at"}, None)
                for name, kind in reset21._COLUMNS.items()}
    expected[("users", "credential_epoch")] = ("bigint", True, "0")
    expected[("password_reset_tokens", "purpose")] = ("character varying(24)", True, "password_reset")
    found = {}
    for row in rows:
        need(row["relkind"] == "r" and row["relpersistence"] == "p" and row["relispartition"] is False
             and type(row["attnotnull"]) is bool and row["attidentity"] == row["attgenerated"] == ""
             and row["default_collation"] is True, "reset_column_shape_mismatch")
        key = (row["relname"], row["attname"])
        need(key not in found, "reset_column_shape_mismatch")
        default = row["column_default"]
        if key == ("users", "credential_epoch") and default in {"0", "'0'::bigint"}:
            default = "0"
        if key == ("password_reset_tokens", "purpose") and default in {"'password_reset'::character varying", "'password_reset'::text"}:
            default = "password_reset"
        found[key] = (row["column_type"], row["attnotnull"], default)
    need(found == expected, "reset_column_shape_mismatch")


def _reset_constraints(connection, namespace_oid, namespace):
    rows = (
        connection.execute(
            text(
                "SELECT c.relname,k.conname,k.contype,k.convalidated,k.condeferrable,"
                "k.condeferred,k.conislocal,k.coninhcount,k.connoinherit,"
                "ARRAY(SELECT a.attname::text FROM unnest(k.conkey) WITH ORDINALITY x(num,pos) "
                "JOIN pg_catalog.pg_attribute a ON a.attrelid=c.oid AND a.attnum=x.num "
                "ORDER BY x.pos) AS columns,"
                "rn.nspname AS referenced_namespace,r.relname AS referenced_table,"
                "ARRAY(SELECT a.attname::text FROM unnest(k.confkey) WITH ORDINALITY x(num,pos) "
                "JOIN pg_catalog.pg_attribute a ON a.attrelid=r.oid AND a.attnum=x.num "
                "ORDER BY x.pos) AS referenced_columns,"
                "k.confupdtype,k.confdeltype,k.confmatchtype,"
                "pg_catalog.pg_get_constraintdef(k.oid) AS definition "
                "FROM pg_catalog.pg_constraint k JOIN pg_catalog.pg_class c ON c.oid=k.conrelid "
                "LEFT JOIN pg_catalog.pg_class r ON r.oid=k.confrelid "
                "LEFT JOIN pg_catalog.pg_namespace rn ON rn.oid=r.relnamespace "
                "WHERE c.relnamespace=:namespace AND "
                "c.relname=ANY(CAST(:tables AS text[])) "
                "ORDER BY c.oid,k.oid LIMIT 257"
            ),
            {"namespace": namespace_oid, "tables": ["password_reset_tokens"]},
        )
        .mappings()
        .all()
    )
    need(len(rows) <= 256, "reset_constraint_inventory_bound")
    expected = {
        ("password_reset_tokens", "p", ("id",), None, None, (), None),
        ("password_reset_tokens", "u", ("token_sha256",), None, None, (), None),
        ("password_reset_tokens", "f", ("user_id",), namespace, "users", ("id",), ("a", "c", "s")),
    }
    found, checks = set(), {}
    for row in rows:
        need(
            row["convalidated"] is True
            and row["condeferrable"] is False
            and row["condeferred"] is False
            and row["conislocal"] is True
            and row["coninhcount"] == 0,
            "reset_constraint_shape_mismatch",
        )
        if row["contype"] == "c":
            need(
                row["contype"] == "c"
                and row["connoinherit"] is False
                and row["conname"] not in checks,
                "reset_review_checks_mismatch",
            )
            checks[row["conname"]] = re.sub(r"'(?:[^']|'')*'|[\s()]", reset21.previous._keep_literal, row["definition"])
            continue
        item = (
            row["relname"],
            row["contype"],
            tuple(row["columns"]),
            row["referenced_namespace"],
            row["referenced_table"],
            tuple(row["referenced_columns"]),
            (row["confupdtype"], row["confdeltype"], row["confmatchtype"])
            if row["contype"] == "f"
            else None,
        )
        need(item not in found, "reset_constraint_shape_mismatch")
        found.add(item)
    need(found == expected, "reset_constraint_shape_mismatch")
    expected_checks = {
        "CHECKstatus::text=ANYARRAY[" + ",".join(f"'{state}'::{cast}" for state in
            ("PENDING", "PROCESSING", "COMPLETED", "INVALIDATED", "UNAVAILABLE")) + "]::text[]"
        for cast in ("charactervarying", "text")
    }
    purpose_checks = {
        "CHECKpurpose::text=ANYARRAY[" + ",".join(f"'{state}'::{cast}" for state in
            ("password_reset", "privacy_erasure")) + "]::text[]"
        for cast in ("charactervarying", "text")
    }
    need(set(checks) == {"ck_password_reset_status", "ck_password_reset_purpose"}
         and checks["ck_password_reset_status"] in expected_checks
         and checks["ck_password_reset_purpose"] in purpose_checks,
         "reset_purpose_or_status_check_mismatch")
def _browser_constraints(connection, namespace_oid: int, namespace: str) -> None:
    """Keep the frozen19 key/FK checks, with only the reviewed protocol bound5."""
    rows = (
        connection.execute(
            text(
                "SELECT c.relname,k.conname,k.contype,k.convalidated,k.condeferrable,"
                "k.condeferred,k.conislocal,k.coninhcount,k.connoinherit,"
                "ARRAY(SELECT a.attname::text FROM unnest(k.conkey) WITH ORDINALITY x(num,pos) "
                "JOIN pg_catalog.pg_attribute a ON a.attrelid=c.oid AND a.attnum=x.num "
                "ORDER BY x.pos) AS columns,"
                "rn.nspname AS referenced_namespace,r.relname AS referenced_table,"
                "ARRAY(SELECT a.attname::text FROM unnest(k.confkey) WITH ORDINALITY x(num,pos) "
                "JOIN pg_catalog.pg_attribute a ON a.attrelid=r.oid AND a.attnum=x.num "
                "ORDER BY x.pos) AS referenced_columns,"
                "k.confupdtype,k.confdeltype,k.confmatchtype,"
                "pg_catalog.pg_get_constraintdef(k.oid) AS definition "
                "FROM pg_catalog.pg_constraint k JOIN pg_catalog.pg_class c ON c.oid=k.conrelid "
                "LEFT JOIN pg_catalog.pg_class r ON r.oid=k.confrelid "
                "LEFT JOIN pg_catalog.pg_namespace rn ON rn.oid=r.relnamespace "
                "WHERE c.relnamespace=:namespace AND "
                "(c.relname=ANY(CAST(:tables AS text[])) OR (c.relname=:reviews "
                "AND k.conname IN ('ck_browser_review_protocol','ck_browser_review_index'))) "
                "ORDER BY c.oid,k.oid LIMIT 257"
            ),
            {"namespace": namespace_oid, "tables": sorted(browser19._GOOGLE_COLUMNS), "reviews": browser19._REVIEWS},
        )
        .mappings()
        .all()
    )
    need(len(rows) <= 256, "browser_constraint_inventory_bound")
    expected = {
        ("candidate_google_enrollment_challenges", "p", ("id",), None, None, (), None),
        (
            "candidate_google_enrollment_challenges",
            "f",
            ("user_id",),
            namespace,
            "users",
            ("id",),
            ("a", "c", "s"),
        ),
        ("candidate_google_identities", "p", ("user_id",), None, None, (), None),
        ("candidate_google_identities", "u", ("principal_sha256",), None, None, (), None),
        (
            "candidate_google_identities",
            "f",
            ("user_id",),
            namespace,
            "users",
            ("id",),
            ("a", "c", "s"),
        ),
        ("candidate_identity_history", "p", ("registration_id",), None, None, (), None),
    }
    found, checks = set(), {}
    for row in rows:
        need(
            row["convalidated"] is True
            and row["condeferrable"] is False
            and row["condeferred"] is False
            and row["conislocal"] is True
            and row["coninhcount"] == 0,
            "browser_constraint_shape_mismatch",
        )
        if row["relname"] == browser19._REVIEWS:
            need(
                row["contype"] == "c"
                and row["connoinherit"] is False
                and row["conname"] not in checks,
                "browser_review_checks_mismatch",
            )
            checks[row["conname"]] = re.sub(r"[\s()]", "", row["definition"])
            continue
        item = (
            row["relname"],
            row["contype"],
            tuple(row["columns"]),
            row["referenced_namespace"],
            row["referenced_table"],
            tuple(row["referenced_columns"]),
            (row["confupdtype"], row["confdeltype"], row["confmatchtype"])
            if row["contype"] == "f"
            else None,
        )
        need(item not in found, "browser_constraint_shape_mismatch")
        found.add(item)
    need(found == expected, "browser_constraint_shape_mismatch")
    need(
        checks
        == {
            "ck_browser_review_protocol": "CHECKreview_protocol_version>=1ANDreview_protocol_version<=5",
            "ck_browser_review_index": "CHECKnext_index>=0ANDnext_index<=64",
        },
        "browser_review_checks_mismatch",
    )


def _browser_shape(connection, namespace_oid, namespace):
    browser19._namespace(namespace)
    need(type(namespace_oid) is int and 0 < namespace_oid < 2**32, "browser_namespace_oid_invalid")
    rows = connection.execute(text("SELECT oid,nspname FROM pg_catalog.pg_namespace WHERE oid=:namespace"),
        {"namespace": namespace_oid}).all()
    need([tuple(row) for row in rows] == [(namespace_oid, namespace)], "browser_namespace_identity_mismatch")
    browser19.previous._candidate_projection_shape(connection, namespace_oid)
    browser19.previous._extension_shape(connection, namespace_oid, "20261015_0018")
    browser19._columns(connection, namespace_oid)
    _browser_constraints(connection, namespace_oid, namespace)
    browser19._indexes(connection, namespace_oid)


def verify_shape(connection, namespace_oid=2200, namespace="public"):
    marker(connection, namespace)
    # Original19/21 remain exact and refuse the new review/purpose checks.
    # This distinct pending26 validator admits ONLY those explicit deltas,
    # preserving actual old projection/column/index/FK guards without relabeling.
    _browser_shape(connection, namespace_oid, namespace)
    reset21.previous._upload_format(connection, namespace_oid, ("pdf", "docx", "tex", "texzip"))
    _reset_columns(connection, namespace_oid)
    _reset_constraints(connection, namespace_oid, namespace)
    reset21._indexes(connection, namespace_oid)
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
        "fence_definition": DEFINITION_SHA256, "migration_source": MIGRATION_SHA256,
        "invariants": financial.INVARIANTS,
        "new_wallet_activity": "none"}, sort_keys=True).encode()).hexdigest()
