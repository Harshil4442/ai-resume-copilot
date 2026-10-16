"""Catalog-only admission for the separately reviewed browser schema19 release.

This does not authenticate a database, admit a migration, verify finances or
activate candidate/native authority. Those independent gates belong to callers.
The schema18 preflight and native identity allowlists remain unchanged.
"""

from __future__ import annotations

import re
from typing import Any

from sqlalchemy import text

try:
    from scripts import monetary_cutover_preflight as previous
except ModuleNotFoundError:  # Direct standalone script imports.
    import monetary_cutover_preflight as previous

SCHEMA = "20261015_0019"
CONTRACT = "hirewiz_browser_schema19_v1"

_GOOGLE_COLUMNS = {
    "candidate_google_enrollment_challenges": {
        "id": "character varying(36)",
        "user_id": "integer",
        "nonce_sha256": "character varying(64)",
        "principal_sha256": "character varying(64)",
        "email_sha256": "character varying(64)",
        "credential_sha256": "character varying(64)",
        "expires_at_ms": "bigint",
        "used": "boolean",
    },
    "candidate_google_identities": {
        "user_id": "integer",
        "principal_sha256": "character varying(64)",
        "enrollment_proof_sha256": "character varying(64)",
        "state": "character varying(24)",
    },
    "candidate_identity_history": {"registration_id": "character varying(36)"},
}
_REVIEWS = "browser_assistance_reviews"
_MAX_ROWS = 256


class Schema19Denied(RuntimeError):
    """Only a fixed nonsecret refusal escapes catalog observation."""


def _need(value: Any, reason: str) -> None:
    if not value:
        raise Schema19Denied(reason)


def _namespace(namespace: str) -> None:
    _need(
        type(namespace) is str and re.fullmatch(r"[a-z][a-z0-9_]{0,62}", namespace),
        "schema19_namespace_invalid",
    )


def _columns(connection: Any, namespace_oid: int) -> None:
    rows = (
        connection.execute(
            text(
                "SELECT c.relname,c.relkind,c.relpersistence,c.relispartition,"
                "a.attname,pg_catalog.format_type(a.atttypid,a.atttypmod) AS column_type,"
                "a.attnotnull,a.attidentity,a.attgenerated,a.attcollation=t.typcollation AS default_collation,"
                "pg_catalog.pg_get_expr(d.adbin,d.adrelid) AS column_default "
                "FROM pg_catalog.pg_class c JOIN pg_catalog.pg_attribute a ON a.attrelid=c.oid "
                "JOIN pg_catalog.pg_type t ON t.oid=a.atttypid "
                "LEFT JOIN pg_catalog.pg_attrdef d ON d.adrelid=c.oid AND d.adnum=a.attnum "
                "WHERE c.relnamespace=:namespace AND a.attnum>0 AND NOT a.attisdropped "
                "AND (c.relname=ANY(CAST(:tables AS text[])) "
                "OR (c.relname=:reviews AND a.attname IN ('next_index','review_protocol_version'))) "
                "ORDER BY c.oid,a.attnum LIMIT 257"
            ),
            {"namespace": namespace_oid, "tables": sorted(_GOOGLE_COLUMNS), "reviews": _REVIEWS},
        )
        .mappings()
        .all()
    )
    _need(len(rows) <= _MAX_ROWS, "schema19_column_inventory_bound")
    expected: dict[tuple[str, str], tuple[str, bool, str | None]] = {
        (table, column): (kind, True, None)
        for table, columns in _GOOGLE_COLUMNS.items()
        for column, kind in columns.items()
    }
    expected.update(
        {
            (_REVIEWS, "next_index"): ("integer", True, None),
            (_REVIEWS, "review_protocol_version"): ("integer", True, "1"),
        }
    )
    found = {}
    for row in rows:
        _need(
            row["relkind"] == "r"
            and row["relpersistence"] == "p"
            and row["relispartition"] is False
            and type(row["attnotnull"]) is bool
            and row["attidentity"] == row["attgenerated"] == "",
            "schema19_column_shape_mismatch",
        )
        _need(row["default_collation"] is True, "schema19_column_shape_mismatch")
        key = (row["relname"], row["attname"])
        _need(key not in found, "schema19_column_shape_mismatch")
        found[key] = (row["column_type"], row["attnotnull"], row["column_default"])
    _need(found == expected, "schema19_column_shape_mismatch")


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
                "(c.relname=ANY(CAST(:tables AS text[])) OR (c.relname=:reviews "
                "AND k.conname IN ('ck_browser_review_protocol','ck_browser_review_index'))) "
                "ORDER BY c.oid,k.oid LIMIT 257"
            ),
            {"namespace": namespace_oid, "tables": sorted(_GOOGLE_COLUMNS), "reviews": _REVIEWS},
        )
        .mappings()
        .all()
    )
    _need(len(rows) <= _MAX_ROWS, "schema19_constraint_inventory_bound")
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
        _need(
            row["convalidated"] is True
            and row["condeferrable"] is False
            and row["condeferred"] is False
            and row["conislocal"] is True
            and row["coninhcount"] == 0,
            "schema19_constraint_shape_mismatch",
        )
        if row["relname"] == _REVIEWS:
            _need(
                row["contype"] == "c"
                and row["connoinherit"] is False
                and row["conname"] not in checks,
                "schema19_review_checks_mismatch",
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
        _need(item not in found, "schema19_constraint_shape_mismatch")
        found.add(item)
    _need(found == expected, "schema19_constraint_shape_mismatch")
    _need(
        checks
        == {
            "ck_browser_review_protocol": "CHECKreview_protocol_version>=1ANDreview_protocol_version<=4",
            "ck_browser_review_index": "CHECKnext_index>=0ANDnext_index<=64",
        },
        "schema19_review_checks_mismatch",
    )


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
            {"namespace": namespace_oid, "tables": sorted(_GOOGLE_COLUMNS)},
        )
        .mappings()
        .all()
    )
    _need(len(rows) <= _MAX_ROWS, "schema19_index_inventory_bound")
    expected = {
        (
            "candidate_google_enrollment_challenges",
            "candidate_google_enrollment_challenges_pkey",
            ("id",),
            True,
            True,
        ),
        (
            "candidate_google_enrollment_challenges",
            "ix_candidate_google_enrollment_challenges_user_id",
            ("user_id",),
            False,
            False,
        ),
        (
            "candidate_google_identities",
            "candidate_google_identities_pkey",
            ("user_id",),
            True,
            True,
        ),
        (
            "candidate_google_identities",
            "candidate_google_identities_principal_sha256_key",
            ("principal_sha256",),
            True,
            False,
        ),
        (
            "candidate_identity_history",
            "candidate_identity_history_pkey",
            ("registration_id",),
            True,
            True,
        ),
    }
    found = set()
    for row in rows:
        _need(
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
                "pg_catalog.int4_ops" if column == "user_id" else "pg_catalog.text_ops"
                for column in row["columns"]
            ]
            and type(row["indisunique"]) is bool
            and type(row["indisprimary"]) is bool,
            "schema19_index_shape_mismatch",
        )
        item = (
            row["relname"],
            row["index_name"],
            tuple(row["columns"]),
            row["indisunique"],
            row["indisprimary"],
        )
        _need(item not in found, "schema19_index_shape_mismatch")
        found.add(item)
    _need(found == expected, "schema19_index_shape_mismatch")


def verify_shape(connection: Any, namespace_oid: int, namespace: str = "public") -> None:
    """Read bounded native catalog metadata; never application/credential rows.

    The caller must independently establish PostgreSQL/native target identity,
    exact schema19 marker, read-only snapshot/timeouts and financial invariants.
    """
    _namespace(namespace)
    _need(
        type(namespace_oid) is int and 0 < namespace_oid < 2**32, "schema19_namespace_oid_invalid"
    )
    try:
        observed = connection.execute(
            text("SELECT oid,nspname FROM pg_catalog.pg_namespace WHERE oid=:namespace"),
            {"namespace": namespace_oid},
        ).all()
        _need(
            [tuple(row) for row in observed] == [(namespace_oid, namespace)],
            "schema19_namespace_identity_mismatch",
        )
        previous._candidate_projection_shape(connection, namespace_oid)
        previous._extension_shape(connection, namespace_oid, "20261015_0018")
        _columns(connection, namespace_oid)
        _constraints(connection, namespace_oid, namespace)
        _indexes(connection, namespace_oid)
    except Schema19Denied:
        raise
    except previous.Denied:
        raise Schema19Denied("schema19_preserved_projection_shape_mismatch") from None
    except Exception:
        raise Schema19Denied("schema19_catalog_observation_unavailable") from None


def unresolved_application_sql(namespace: str, version: str = SCHEMA) -> str:
    """Keep candidate manual reports distinct from paid employer receipt proof."""
    _namespace(namespace)
    _need(type(version) is str and version == SCHEMA, "schema19_application_reader_invalid")
    return previous.unresolved_application_sql(namespace, "20261015_0018")
