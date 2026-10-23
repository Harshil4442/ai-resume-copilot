"""Exact add-only schema19/21 password-reset catalog; no DDL or credentials."""
from __future__ import annotations

import re
from typing import Any

from sqlalchemy import text

from scripts import native_resume_schema20_contract as previous

BEFORE = previous.BEFORE
INTERMEDIATE = previous.SCHEMA
SCHEMA = "20261023_0021"
CONTRACT = "hirewiz_email_password_schema21_v1"
_MAX_ROWS = 256
_COLUMNS = {
    "id": "character varying(64)", "user_id": "integer",
    "token_sha256": "character varying(64)", "email_sha256": "character varying(64)",
    "credential_sha256": "character varying(64)", "native_revision_sha256": "character varying(64)",
    "legacy_epoch": "bigint", "status": "character varying(24)",
    "created_at": "timestamp without time zone", "expires_at": "timestamp without time zone",
    "consumed_at": "timestamp without time zone",
}


class Schema21Denied(RuntimeError):
    """Only fixed nonsecret observation refusals escape."""


def need(value: Any, reason: str) -> None:
    if not value:
        raise Schema21Denied(reason)


def marker(connection: Any, namespace: str = "public") -> str:
    previous.namespace_name(namespace)
    values = list(connection.execute(text(f'SELECT version_num FROM "{namespace}".alembic_version LIMIT 3')).scalars())
    need(len(values) == 1 and type(values[0]) is str and values[0] in {BEFORE, SCHEMA}, "schema21_marker_refused")
    return values[0]


def _columns(connection: Any, namespace_oid: int) -> None:
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
                for name, kind in _COLUMNS.items()}
    expected[("users", "credential_epoch")] = ("bigint", True, "0")
    found = {}
    for row in rows:
        need(row["relkind"] == "r" and row["relpersistence"] == "p" and row["relispartition"] is False
             and type(row["attnotnull"]) is bool and row["attidentity"] == row["attgenerated"] == ""
             and row["default_collation"] is True, "schema21_column_shape_mismatch")
        key = (row["relname"], row["attname"])
        need(key not in found, "schema21_column_shape_mismatch")
        default = row["column_default"]
        if key == ("users", "credential_epoch") and default in {"0", "'0'::bigint"}:
            default = "0"
        found[key] = (row["column_type"], row["attnotnull"], default)
    need(found == expected, "schema21_column_shape_mismatch")


def _constraints(connection: Any, namespace_oid: int, namespace: str) -> None:
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
    need(len(rows) <= _MAX_ROWS, "schema21_constraint_inventory_bound")
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
            "schema21_constraint_shape_mismatch",
        )
        if row["contype"] == "c":
            need(
                row["contype"] == "c"
                and row["connoinherit"] is False
                and row["conname"] not in checks,
                "schema21_review_checks_mismatch",
            )
            checks[row["conname"]] = re.sub(r"'(?:[^']|'')*'|[\s()]", previous._keep_literal, row["definition"])
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
        need(item not in found, "schema21_constraint_shape_mismatch")
        found.add(item)
    need(found == expected, "schema21_constraint_shape_mismatch")
    expected_checks = {
        "CHECKstatus::text=ANYARRAY[" + ",".join(f"'{state}'::{cast}" for state in
            ("PENDING", "PROCESSING", "COMPLETED", "INVALIDATED", "UNAVAILABLE")) + "]::text[]"
        for cast in ("charactervarying", "text")
    }
    need(set(checks) == {"ck_password_reset_status"}
         and checks["ck_password_reset_status"] in expected_checks,
         "schema21_reset_status_check_mismatch")


def _indexes(connection: Any, namespace_oid: int) -> None:
    rows = (
        connection.execute(
            text(
                "SELECT c.relname,ic.relname AS index_name,am.amname,i.indisunique,i.indisprimary,"
                "i.indisvalid,i.indisready,i.indislive,i.indimmediate,i.indnullsnotdistinct,"
                "i.indnatts,i.indnkeyatts,i.indpred IS NULL AS unconditional,"
                "i.indexprs IS NULL AS plain_columns,"
                "ARRAY(SELECT a.attname::text FROM unnest(i.indkey) WITH ORDINALITY x(num,pos) "
                "LEFT JOIN pg_catalog.pg_attribute a ON a.attrelid=c.oid AND a.attnum=x.num "
                "ORDER BY x.pos) AS columns,"
                "ARRAY(SELECT x FROM unnest(i.indoption) x) AS options,"
                "ARRAY(SELECT x FROM unnest(i.indcollation) x) AS collations,"
                "ARRAY(SELECT a.attcollation FROM unnest(i.indkey) WITH ORDINALITY x(num,pos) "
                "LEFT JOIN pg_catalog.pg_attribute a ON a.attrelid=c.oid AND a.attnum=x.num "
                "ORDER BY x.pos) AS column_collations,"
                "ARRAY(SELECT n.nspname||'.'||o.opcname FROM unnest(i.indclass) WITH ORDINALITY x(num,pos) "
                "JOIN pg_catalog.pg_opclass o ON o.oid=x.num "
                "JOIN pg_catalog.pg_namespace n ON n.oid=o.opcnamespace ORDER BY x.pos) AS opclasses "
                "FROM pg_catalog.pg_index i JOIN pg_catalog.pg_class c ON c.oid=i.indrelid "
                "JOIN pg_catalog.pg_class ic ON ic.oid=i.indexrelid "
                "JOIN pg_catalog.pg_am am ON am.oid=ic.relam "
                "WHERE c.relnamespace=:namespace AND c.relname=ANY(CAST(:tables AS text[])) "
                "ORDER BY c.oid,ic.oid LIMIT 257"
            ),
            {"namespace": namespace_oid, "tables": ["password_reset_tokens"]},
        )
        .mappings()
        .all()
    )
    need(len(rows) <= _MAX_ROWS, "schema21_index_inventory_bound")
    expected = {
        ("password_reset_tokens", "password_reset_tokens_pkey", ("id",), True, True),
        ("password_reset_tokens", "password_reset_tokens_token_sha256_key", ("token_sha256",), True, False),
        ("password_reset_tokens", "ix_password_reset_owner_created", ("user_id", "created_at"), False, False),
    }
    found = set()
    for row in rows:
        need(
            row["amname"] == "btree"
            and row["indisvalid"] is True
            and row["indisready"] is True
            and row["indislive"] is True
            and row["indimmediate"] is True
            and row["indnullsnotdistinct"] is False
            and row["unconditional"] is True
            and row["plain_columns"] is True
            and row["indnatts"] == row["indnkeyatts"] == len(row["columns"])
            and row["options"] == [0] * len(row["columns"])
            and row["collations"] == row["column_collations"]
            and row["opclasses"]
            == [
                "pg_catalog.int4_ops" if column == "user_id" else "pg_catalog.timestamp_ops" if column == "created_at" else "pg_catalog.text_ops"
                for column in row["columns"]
            ]
            and type(row["indisunique"]) is bool
            and type(row["indisprimary"]) is bool,
            "schema21_index_shape_mismatch",
        )
        item = (
            row["relname"],
            row["index_name"],
            tuple(row["columns"]),
            row["indisunique"],
            row["indisprimary"],
        )
        need(item not in found, "schema21_index_shape_mismatch")
        found.add(item)
    need(found == expected, "schema21_index_shape_mismatch")

def verify_before_shape(connection: Any, namespace_oid: int, namespace: str = "public") -> None:
    previous.verify_before_shape(connection, namespace_oid, namespace)
    rows = connection.execute(text(
        "SELECT count(*) FROM pg_catalog.pg_class c LEFT JOIN pg_catalog.pg_attribute a ON a.attrelid=c.oid "
        "AND a.attnum>0 AND NOT a.attisdropped WHERE c.relnamespace=:namespace "
        "AND (c.relname='password_reset_tokens' OR (c.relname='users' AND a.attname='credential_epoch'))"),
        {"namespace": namespace_oid}).scalar_one()
    need(rows == 0, "schema21_before_reset_shape_present")


def verify_shape(connection: Any, namespace_oid: int, namespace: str = "public") -> None:
    previous.namespace_name(namespace)
    need(type(namespace_oid) is int and 0 < namespace_oid < 2**32, "schema21_namespace_oid_invalid")
    try:
        need(marker(connection, namespace) == SCHEMA, "schema21_exact_marker_required")
        previous.previous.verify_shape(connection, namespace_oid, namespace)
        previous._upload_format(connection, namespace_oid, ("pdf", "docx", "tex", "texzip"))
        _columns(connection, namespace_oid)
        _constraints(connection, namespace_oid, namespace)
        _indexes(connection, namespace_oid)
    except Schema21Denied:
        raise
    except (previous.Schema20Denied, previous.previous.Schema19Denied):
        raise Schema21Denied("schema21_preserved_schema19_20_shape_mismatch") from None
    except Exception:
        raise Schema21Denied("schema21_catalog_observation_unavailable") from None


def verify_reset_permissions(connection: Any, namespace: str, runtime: str, migrator: str, observer: str) -> None:
    previous.namespace_name(namespace)
    rows = connection.execute(text(
        "SELECT owner.rolname AS owner_name,x.grantor=c.relowner AS owner_grant,"
        "coalesce(grantee.rolname,'PUBLIC') AS grantee,x.privilege_type,x.is_grantable "
        "FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace "
        "JOIN pg_catalog.pg_roles owner ON owner.oid=c.relowner "
        "CROSS JOIN LATERAL pg_catalog.aclexplode(coalesce(c.relacl,pg_catalog.acldefault('r',c.relowner))) x "
        "LEFT JOIN pg_catalog.pg_roles grantee ON grantee.oid=x.grantee "
        "WHERE n.nspname=:namespace AND c.relname='password_reset_tokens' AND c.relkind='r' "
        "AND x.grantee<>c.relowner LIMIT 33"), {"namespace": namespace}).mappings().all()
    expected = {(runtime, privilege, False) for privilege in ("SELECT", "INSERT", "UPDATE", "DELETE")}
    expected.add((observer, "SELECT", False))
    need(len(rows) == len(expected) and all(row["owner_name"] == migrator and row["owner_grant"] is True
          and type(row["is_grantable"]) is bool for row in rows)
         and {(row["grantee"], row["privilege_type"], row["is_grantable"]) for row in rows} == expected,
         "schema21_reset_table_acl_mismatch")
