"""Explicit schema26 evidence-write scopes; never request authorization.

The database trigger rejects unmarked legacy writers. Callers must complete their
existing ownership/CAS or new-evidence validation before entering this scope. The
marker is removed immediately after the exact flush/statement, not at the end of
an HTTP request or pooled session. SQL-capable code is trusted; this custom GUC is
not a boundary against arbitrary SQL or a privileged database administrator.
"""
from __future__ import annotations

from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from typing import TYPE_CHECKING

from sqlalchemy import text
from sqlalchemy.orm import Session

from . import evidence_fence_definition as definition

if TYPE_CHECKING:
    from ... import models

SETTING = definition.SETTING
VERSION = definition.VERSION


class EvidenceWriteFenceDenied(RuntimeError):
    pass


def require_installed(connection, namespace: str | None = None) -> str:
    """Read the exact current table's fence; never install or repair it.

    Full public Alembic/catalog/role identity is a separate native release gate.
    Owned private evaluation schemas install the identical fence without
    pretending that Base.metadata constitutes a complete Alembic migration.
    """
    if connection.dialect.name != "postgresql":
        raise EvidenceWriteFenceDenied("evidence_writer_postgresql_required")
    actual = connection.execute(text("SELECT current_schema()")).scalar_one()
    if namespace is not None and actual != namespace:
        raise EvidenceWriteFenceDenied("evidence_writer_namespace_changed")
    rows = connection.execute(text(
        "SELECT t.tgtype,t.tgenabled,t.tgnargs,t.tgargs,t.tgqual IS NULL AS unconditional,"
        "t.tgattr::text AS columns,t.tgconstraint,t.tgdeferrable,t.tginitdeferred,"
        "t.tgname,p.proname,p.pronargs,p.prorettype='pg_catalog.trigger'::regtype AS returns_trigger,"
        "p.prosecdef,p.proconfig,p.prosrc,l.lanname,pn.nspname AS function_namespace "
        "FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace "
        "JOIN pg_catalog.pg_trigger t ON t.tgrelid=c.oid "
        "JOIN pg_catalog.pg_proc p ON p.oid=t.tgfoid "
        "JOIN pg_catalog.pg_namespace pn ON pn.oid=p.pronamespace "
        "JOIN pg_catalog.pg_language l ON l.oid=p.prolang "
        "WHERE n.nspname=:namespace AND c.relname='evidence_items' "
        "AND c.relkind='r' AND NOT t.tgisinternal ORDER BY t.oid LIMIT 2"),
        {"namespace": actual}).mappings().all()
    expected = {"tgtype": definition.TRIGGER_TYPE, "tgenabled": definition.TRIGGER_ENABLED,
        "tgnargs": 0, "tgargs": b"", "unconditional": True, "columns": "", "tgconstraint": 0,
        "tgdeferrable": False, "tginitdeferred": False, "tgname": definition.TRIGGER_NAME,
        "proname": definition.FUNCTION_NAME, "pronargs": 0, "returns_trigger": True,
        "prosecdef": False, "proconfig": ["search_path=pg_catalog"], "prosrc": definition.FUNCTION_BODY,
        "lanname": "plpgsql", "function_namespace": actual}
    if len(rows) != 1 or dict(rows[0]) != expected:
        raise EvidenceWriteFenceDenied("evidence_writer_schema26_fence_required")
    return actual


@contextmanager
def evidence_write_scope(db: Session) -> Iterator[None]:
    """Arm only one validated native write; no commit, retry or authorization.

    SQLite retains the existing local test/development behavior. Unknown
    dialects fail closed. A failed PostgreSQL statement aborts its transaction;
    rollback clears its SET LOCAL value. Failure to clear a still-live marker
    forces rollback rather than returning a session with inherited authority.
    """
    dialect = db.get_bind().dialect.name
    if dialect == "sqlite":
        yield
        return
    if dialect != "postgresql":
        raise EvidenceWriteFenceDenied("evidence_writer_dialect_unqualified")
    connection = db.connection()
    require_installed(connection)
    previous = connection.execute(text("SELECT current_setting(:setting, true)"), {"setting": SETTING}).scalar_one()
    if previous not in (None, ""):
        db.rollback()
        raise EvidenceWriteFenceDenied("evidence_writer_scope_already_active")
    try:
        armed = connection.execute(text("SELECT set_config(:setting, :version, true)"),
            {"setting": SETTING, "version": VERSION}).scalar_one()
    except BaseException:
        db.rollback()
        raise
    if armed != VERSION:
        db.rollback()
        raise EvidenceWriteFenceDenied("evidence_writer_scope_unavailable")
    try:
        yield
    except BaseException:
        # Raw SQL errors need rollback too: Session.is_active alone does not
        # prove that PostgreSQL has not aborted the underlying transaction.
        db.rollback()
        raise
    else:
        try:
            if not db.is_active:
                raise EvidenceWriteFenceDenied("evidence_writer_transaction_failed")
            cleared = connection.execute(text("SELECT set_config(:setting, '', true)"),
                {"setting": SETTING}).scalar_one()
            if cleared != "":
                raise EvidenceWriteFenceDenied("evidence_writer_scope_clear_failed")
        except BaseException:
            db.rollback()
            raise


def flush_evidence_writes(db: Session, items: Sequence[models.EvidenceItem]) -> None:
    """Flush only the explicitly validated ORM rows while the marker is armed."""
    from ... import models

    if not items or any(not isinstance(item, models.EvidenceItem) for item in items):
        raise EvidenceWriteFenceDenied("evidence_writer_rows_required")
    with evidence_write_scope(db):
        db.flush(items)
