"""Migration SQL/source controls only; no PostgreSQL or draining proof."""
from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy.dialects import postgresql

from app.domains.career import evidence_fence_definition as definition

ROOT = Path(__file__).resolve().parents[2]
MIGRATION = ROOT / "backend/alembic/versions/20261031_0026_evidence_writer_fence.py"


def load_migration():
    spec = importlib.util.spec_from_file_location("evidence_writer_fence_migration", MIGRATION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_upgrade_orders_bounded_exclusive_drain_before_exact_always_enabled_trigger(monkeypatch):
    migration = load_migration()
    statements = []

    class Bind:
        dialect = postgresql.dialect()

        def execute(self, statement):
            statements.append(" ".join(str(statement).split()))

        def scalar(self, statement):
            assert str(statement) == "SELECT current_schema()"
            return "owned_schema"

        def in_transaction(self):
            return True

    monkeypatch.setattr(migration, "op", SimpleNamespace(get_bind=lambda: Bind()))
    migration.upgrade()
    assert migration.revision == "20261031_0026"
    assert migration.down_revision == "20261028_0025"
    assert statements[:3] == ["SET LOCAL lock_timeout = '4s'", "SET LOCAL statement_timeout = '10s'",
        'LOCK TABLE "owned_schema".evidence_items IN ACCESS EXCLUSIVE MODE']
    assert len(statements) == 6
    function, trigger, enabled = statements[3:]
    assert 'CREATE FUNCTION "owned_schema".evidence_writer_fence()' in function
    assert "SECURITY INVOKER SET search_path = pg_catalog" in function
    assert "pg_catalog.current_setting('hirewiz.evidence_writer_version', true) IS DISTINCT FROM 'cas_v1'" in function
    assert "ERRCODE = '42501'" in function and "MESSAGE = 'evidence_writer_version_required'" in function
    assert definition.FUNCTION_BODY.strip().endswith("END;")
    assert "BEFORE INSERT OR UPDATE OR DELETE" in trigger and "FOR EACH STATEMENT" in trigger
    assert 'ON "owned_schema".evidence_items' in trigger
    assert enabled == 'ALTER TABLE "owned_schema".evidence_items ENABLE ALWAYS TRIGGER evidence_writer_fence'
    assert all("DROP " not in statement and "TRUNCATE " not in statement for statement in statements)


@pytest.mark.parametrize("namespace", [None, "", "public; DROP TABLE evidence_items", 'owned"schema', "UPPER", "x" * 64])
def test_install_rejects_unqualified_namespace_before_any_sql(namespace):
    bind = SimpleNamespace(dialect=SimpleNamespace(name="postgresql"))
    with pytest.raises(ValueError, match="namespace_invalid"):
        definition.install(bind, namespace)


def test_install_requires_an_existing_transaction_before_any_sql():
    bind = SimpleNamespace(dialect=SimpleNamespace(name="postgresql"), in_transaction=lambda: False)
    with pytest.raises(RuntimeError, match="install_transaction_required"):
        definition.install(bind, "public")


def test_local_sqlite_does_not_pretend_to_install_a_postgresql_fence(monkeypatch):
    migration = load_migration()
    bind = SimpleNamespace(dialect=SimpleNamespace(name="sqlite"))
    monkeypatch.setattr(migration, "op", SimpleNamespace(get_bind=lambda: bind))
    migration.upgrade()


def test_downgrade_refuses_before_any_database_or_ddl_access(monkeypatch):
    migration = load_migration()

    def forbidden():
        raise AssertionError("Unsafe fence removal must not reach a database")

    monkeypatch.setattr(migration, "op", SimpleNamespace(get_bind=forbidden))
    with pytest.raises(RuntimeError, match="downgrade is unsupported.*safe replacement"):
        migration.downgrade()
