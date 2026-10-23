"""Exact schema19/20 native-upload catalog observation, never DDL authority."""

from __future__ import annotations

import re
from typing import Any, cast

from sqlalchemy import text

from scripts import browser_schema19_contract as previous

BEFORE = previous.SCHEMA
SCHEMA = "20261023_0020"
CONTRACT = "hirewiz_native_resume_schema20_v1"


class Schema20Denied(RuntimeError):
    """A fixed nonsecret observation refusal."""


def need(value: Any, reason: str) -> None:
    if not value:
        raise Schema20Denied(reason)


def _exact(row: dict, expected: dict) -> bool:
    return row == expected and all(type(row[key]) is type(value) for key, value in expected.items())


def _keep_literal(match: re.Match[str]) -> str:
    return match[0] if match[0].startswith("'") else ""


def namespace_name(namespace: str) -> None:
    need(
        type(namespace) is str and re.fullmatch(r"[a-z][a-z0-9_]{0,62}", namespace),
        "schema20_namespace_invalid",
    )


def marker(connection: Any, namespace: str = "public") -> str:
    namespace_name(namespace)
    values = list(
        connection.execute(
            text(f'SELECT version_num FROM "{namespace}".alembic_version LIMIT 3')
        ).scalars()
    )
    need(
        len(values) == 1 and type(values[0]) is str and values[0] in {BEFORE, SCHEMA},
        "schema20_marker_refused",
    )
    return values[0]


def _upload_format(connection: Any, namespace_oid: int, formats: tuple[str, ...]) -> None:
    columns = (
        connection.execute(
            text(
                "SELECT c.relkind,c.relpersistence,c.relispartition,"
                "pg_catalog.format_type(a.atttypid,a.atttypmod) AS column_type,"
                "a.attnotnull,a.attidentity,a.attgenerated,a.attcollation=t.typcollation AS default_collation,"
                "pg_catalog.pg_get_expr(d.adbin,d.adrelid) AS column_default "
                "FROM pg_catalog.pg_class c JOIN pg_catalog.pg_attribute a ON a.attrelid=c.oid "
                "JOIN pg_catalog.pg_type t ON t.oid=a.atttypid "
                "LEFT JOIN pg_catalog.pg_attrdef d ON d.adrelid=c.oid AND d.adnum=a.attnum "
                "WHERE c.relnamespace=:namespace AND c.relname='resume_uploads' "
                "AND a.attname='source_format' AND NOT a.attisdropped LIMIT 3"
            ),
            {"namespace": namespace_oid},
        )
        .mappings()
        .all()
    )
    need(
        len(columns) == 1
        and _exact(
            dict(columns[0]),
            {
                "relkind": "r",
                "relpersistence": "p",
                "relispartition": False,
                "column_type": "character varying(8)",
                "attnotnull": True,
                "attidentity": "",
                "attgenerated": "",
                "default_collation": True,
                "column_default": None,
            },
        ),
        "schema20_upload_format_column_mismatch",
    )
    checks = (
        connection.execute(
            text(
                "SELECT k.conname,k.contype,k.convalidated,k.condeferrable,k.condeferred,"
                "k.conislocal,k.coninhcount,k.connoinherit,"
                "ARRAY(SELECT a.attname::text FROM unnest(k.conkey) WITH ORDINALITY x(num,pos) "
                "JOIN pg_catalog.pg_attribute a ON a.attrelid=c.oid AND a.attnum=x.num ORDER BY x.pos) AS columns,"
                "pg_catalog.pg_get_constraintdef(k.oid) AS definition "
                "FROM pg_catalog.pg_constraint k JOIN pg_catalog.pg_class c ON c.oid=k.conrelid "
                "WHERE c.relnamespace=:namespace AND c.relname='resume_uploads' "
                "AND k.contype='c' AND (k.conname='ck_resume_upload_format' OR EXISTS "
                "(SELECT 1 FROM pg_catalog.pg_attribute a WHERE a.attrelid=c.oid "
                "AND a.attname='source_format' AND a.attnum=ANY(k.conkey))) LIMIT 3"
            ),
            {"namespace": namespace_oid},
        )
        .mappings()
        .all()
    )
    need(len(checks) == 1, "schema20_upload_format_constraint_mismatch")
    row = dict(checks[0])
    definition = row.pop("definition", None)
    need(
        _exact(
            row,
            {
                "conname": "ck_resume_upload_format",
                "contype": "c",
                "convalidated": True,
                "condeferrable": False,
                "condeferred": False,
                "conislocal": True,
                "coninhcount": 0,
                "connoinherit": False,
                "columns": ["source_format"],
            },
        ),
        "schema20_upload_format_constraint_mismatch",
    )
    # Only finite PG17 deparser forms of the exact literal allowlist. This is
    # comparison, never evaluation or rewriting of an unknown SQL expression.
    expected = {
        "CHECKsource_format::text=ANYARRAY["
        + ",".join(f"'{value}'::{sql_cast}" for value in formats)
        + "]::text[]"
        for sql_cast in ("charactervarying", "text")
    }
    # Preserve literal contents; removing whitespace/parentheses inside a
    # quoted format would silently admit a different constraint.
    normalized = (
        re.sub(
            r"'(?:[^']|'')*'|[\s()]",
            _keep_literal,
            cast(str, definition),
        )
        if type(definition) is str
        else None
    )
    need(normalized in expected, "schema20_upload_format_constraint_mismatch")


def _verify(connection: Any, namespace_oid: int, namespace: str, *, schema: str) -> None:
    namespace_name(namespace)
    need(type(namespace_oid) is int and 0 < namespace_oid < 2**32, "schema20_namespace_oid_invalid")
    try:
        need(marker(connection, namespace) == schema, "schema20_exact_marker_required")
        previous.verify_shape(connection, namespace_oid, namespace)
        _upload_format(
            connection,
            namespace_oid,
            ("pdf", "docx") if schema == BEFORE else ("pdf", "docx", "tex", "texzip"),
        )
    except Schema20Denied:
        raise
    except previous.Schema19Denied:
        raise Schema20Denied("schema20_preserved_schema19_shape_mismatch") from None
    except Exception:
        raise Schema20Denied("schema20_catalog_observation_unavailable") from None


def verify_before_shape(connection: Any, namespace_oid: int, namespace: str = "public") -> None:
    _verify(connection, namespace_oid, namespace, schema=BEFORE)


def verify_shape(connection: Any, namespace_oid: int, namespace: str = "public") -> None:
    _verify(connection, namespace_oid, namespace, schema=SCHEMA)


def require_schema19_downgrade_safe(connection: Any, namespace: str = "public") -> None:
    """Only proven PDF/DOCX parents permit retained cleanup across a DDL downgrade.

    Application/flag rollback can keep schema20; this never deletes custody rows.
    The existing migration20 independently locks and enforces the same rule.
    """
    namespace_name(namespace)
    rows = connection.execute(
        text(
            f'SELECT EXISTS(SELECT 1 FROM "{namespace}".resume_uploads '
            "WHERE source_format IN ('tex','texzip')) OR EXISTS(SELECT 1 FROM "
            f'"{namespace}".resume_upload_cleanups c LEFT JOIN "{namespace}".resume_uploads u '
            "ON u.id=c.upload_id WHERE u.source_format IS NULL OR u.source_format NOT IN ('pdf','docx'))"
        )
    ).all()
    need(
        len(rows) == 1 and len(rows[0]) == 1 and rows[0][0] is False,
        "schema20_retained_native_or_cleanup_downgrade_refused",
    )
