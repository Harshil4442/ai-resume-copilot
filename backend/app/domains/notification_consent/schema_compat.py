"""Read-only schema26/27 scheduling-projection compatibility, never authority.

Only the exact predecessor without this table may omit its disposable cache.
The successor must have an ordinary permanent, unshadowed table. Complete column,
constraint and role qualification belongs to the independent release contract.
No request installs/repairs DDL or accepts an unknown/malformed schema marker.
"""
from __future__ import annotations

import re

from sqlalchemy import text
from sqlalchemy.orm import Session

from ..recovery.store import GuardUnavailable

PREDECESSOR = "20261031_0026"
SUCCESSOR = "20261105_0027"
FAILURE = "Notification projection schema is unavailable"


def _ordinary(row: dict) -> bool:
    return (type(row.get("oid")) is int and row["oid"] > 0
        and row.get("relkind") == "r" and row.get("relpersistence") == "p"
        and row.get("relispartition") is False and row.get("relrowsecurity") is False
        and row.get("relforcerowsecurity") is False and row.get("inherited") is False)


def projection_available(db: Session, *, namespace: str | None = None) -> bool:
    """False means ONLY proved exact26 with no resolved projection relation.

    SQLite retains its existing local metadata behavior. PostgreSQL binds the
    actual current namespace; production's public/native SQL identity remains a
    separate release gate. The caller owns this transaction and any rollback.
    """
    try:
        dialect = db.get_bind().dialect.name
        if dialect == "sqlite":
            return True
        if dialect != "postgresql":
            raise GuardUnavailable(FAILURE)
        connection = db.connection()
        actual = connection.execute(text("SELECT current_schema()")).scalar_one()
        if (type(actual) is not str or re.fullmatch(r"[a-z_][a-z0-9_]{0,62}", actual) is None
                or namespace is not None and actual != namespace):
            raise GuardUnavailable(FAILURE)
        rows = connection.execute(text(
            "SELECT c.relname,c.oid,c.relkind,c.relpersistence,c.relispartition,"
            "c.relrowsecurity,c.relforcerowsecurity,"
            "EXISTS(SELECT 1 FROM pg_catalog.pg_inherits i "
            "WHERE i.inhrelid=c.oid OR i.inhparent=c.oid) AS inherited "
            "FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace "
            "WHERE n.nspname=:namespace AND c.relname IN ('alembic_version','notification_preferences') "
            "ORDER BY c.relname LIMIT 3"), {"namespace": actual}).mappings().all()
        relations = {row["relname"]: dict(row) for row in rows}
        marker = relations.get("alembic_version")
        if len(relations) != len(rows) or marker is None or not _ordinary(marker):
            raise GuardUnavailable(FAILURE)
        # actual has already passed the closed PostgreSQL identifier grammar.
        revisions = tuple(connection.execute(text(
            f'SELECT version_num FROM "{actual}".alembic_version LIMIT 2')).scalars())
        resolved = connection.execute(text(
            "SELECT pg_catalog.to_regclass('notification_preferences')::oid")).scalar_one()
        projection = relations.get("notification_preferences")
        if revisions == (PREDECESSOR,):
            if projection is not None or resolved is not None:
                raise GuardUnavailable(FAILURE)
            return False
        if (revisions != (SUCCESSOR,) or projection is None or not _ordinary(projection)
                or resolved != projection["oid"]):
            raise GuardUnavailable(FAILURE)
        return True
    except GuardUnavailable:
        raise
    except Exception:
        raise GuardUnavailable(FAILURE) from None
