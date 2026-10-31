"""Canonical schema26 evidence fence DDL; no database access at import time.

Only the explicit migration and an already-authorized private evaluation-schema
initializer call install. Normal request adapters verify the catalog and never
install or repair it. The custom GUC distinguishes reviewed source versions; it
is not an authorization mechanism against arbitrary SQL or privileged roles.
"""
from __future__ import annotations

import re

from sqlalchemy import text

SETTING = "hirewiz.evidence_writer_version"
VERSION = "cas_v1"
FUNCTION_NAME = "evidence_writer_fence"
TRIGGER_NAME = "evidence_writer_fence"
TRIGGER_TYPE = 30  # Statement-level BEFORE INSERT | UPDATE | DELETE.
TRIGGER_ENABLED = "A"
FUNCTION_BODY = """
BEGIN
  IF pg_catalog.current_setting('hirewiz.evidence_writer_version', true)
     IS DISTINCT FROM 'cas_v1' THEN
    RAISE EXCEPTION USING ERRCODE = '42501',
      MESSAGE = 'evidence_writer_version_required';
  END IF;
  RETURN NULL;
END;
"""
CREATE_FUNCTION_SQL = (
    "CREATE FUNCTION {namespace}.evidence_writer_fence() RETURNS trigger "
    "LANGUAGE plpgsql SECURITY INVOKER SET search_path = pg_catalog "
    "AS $evidence_fence$" + FUNCTION_BODY + "$evidence_fence$"
)
CREATE_TRIGGER_SQL = (
    "CREATE TRIGGER evidence_writer_fence BEFORE INSERT OR UPDATE OR DELETE "
    "ON {namespace}.evidence_items FOR EACH STATEMENT "
    "EXECUTE FUNCTION {namespace}.evidence_writer_fence()"
)


def install(connection, namespace: str) -> None:
    """Atomically drain and install; duplicate or unqualified installs refuse.

    The caller must hold a transaction in its previously authorized schema.
    A lock timeout aborts it before any function/trigger is installed. No
    CREATE OR REPLACE, IF NOT EXISTS, data rewrite or implicit commit is used.
    """
    if connection.dialect.name != "postgresql":
        raise ValueError("evidence_fence_postgresql_required")
    if not isinstance(namespace, str) or not re.fullmatch(r"[a-z_][a-z0-9_]{0,62}", namespace):
        raise ValueError("evidence_fence_namespace_invalid")
    if not connection.in_transaction():
        raise RuntimeError("evidence_fence_install_transaction_required")
    quoted = f'"{namespace}"'
    connection.execute(text("SET LOCAL lock_timeout = '4s'"))
    connection.execute(text("SET LOCAL statement_timeout = '10s'"))
    connection.execute(text(f"LOCK TABLE {quoted}.evidence_items IN ACCESS EXCLUSIVE MODE"))
    connection.execute(text(CREATE_FUNCTION_SQL.format(namespace=quoted)))
    connection.execute(text(CREATE_TRIGGER_SQL.format(namespace=quoted)))
    connection.execute(text(
        f"ALTER TABLE {quoted}.evidence_items ENABLE ALWAYS TRIGGER evidence_writer_fence"
    ))
