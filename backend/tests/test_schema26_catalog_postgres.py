"""Exact SQL/catalog26 on CI-owned PG17; never production role acceptance."""
import pytest
from sqlalchemy import text
from test_evidence_writer_fence_postgres import fenced as fenced  # noqa: F401
from test_evidence_writer_fence_postgres import pg25 as pg25  # noqa: F401
from test_schema25_app_compatibility_postgres import app25_database as app25_database  # noqa: F401

from app.domains.career.evidence_write_fence import EvidenceWriteFenceDenied, require_installed
from scripts import evidence_writer_schema26_contract as contract
from scripts import schema25_app_release_contract as historical


def test_schema26_actual_catalog_preserves_financial_shape_and_old25_reader_refuses(fenced):
    with fenced.connect() as connection, connection.begin():
        connection.exec_driver_sql("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        connection.exec_driver_sql("SET LOCAL search_path=pg_catalog")
        before = connection.scalar(text("SELECT current_setting('search_path')"))
        assert contract.observe(connection)
        assert connection.scalar(text("SELECT current_setting('search_path')")) == before
        with pytest.raises(historical.schema25.Schema25Denied):
            historical.observe(connection)


def test_actual_privileged_fixture_role_is_not_a_qualified_runtime_writer(fenced):
    # The disposable service administrator owns the fixture. The real release
    # must independently prove the distinct restricted runtime/migrator roles.
    with fenced.connect() as connection:
        actor = connection.scalar(text("SELECT current_user"))
        with pytest.raises(contract.Schema26Denied, match="writer_bypass_privilege"):
            contract.verify_writer_permissions(connection, runtime=actor, migrator=actor)


@pytest.mark.parametrize("changed", ["disabled", "security_definer"])
def test_actual_catalog_drift_refuses_then_rollback_restores_the_original_fence(fenced, changed):
    with fenced.connect() as connection:
        if changed == "disabled":
            connection.execute(text("ALTER TABLE public.evidence_items DISABLE TRIGGER evidence_writer_fence"))
        else:
            connection.execute(text("ALTER FUNCTION public.evidence_writer_fence() SECURITY DEFINER"))
        with pytest.raises(EvidenceWriteFenceDenied, match="schema26_fence_required"):
            require_installed(connection)
        connection.rollback()
        assert require_installed(connection) == "public"
