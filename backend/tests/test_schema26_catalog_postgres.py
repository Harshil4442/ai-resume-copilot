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


def test26_native_review_bound_is_five_and_catalog_drift_rolls_back_without_history_change(fenced):
    """Owned native SQL only; no browser approval or employer execution."""
    with fenced.connect() as connection:
        before = connection.scalar(text("SELECT count(*) FROM public.browser_assistance_reviews"))
        assert contract.observe(connection)
        with pytest.raises(contract.browser19.Schema19Denied, match="review_checks_mismatch"):
            contract.browser19.verify_shape(connection, 2200, "public")
        connection.rollback()
        for expression in ("review_protocol_version BETWEEN 1 AND 4",
                           "review_protocol_version BETWEEN 1 AND 6",
                           "review_protocol_version>=1 OR review_protocol_version<=5"):
            with connection.begin() as transaction:
                connection.exec_driver_sql("ALTER TABLE public.browser_assistance_reviews "
                    "DROP CONSTRAINT ck_browser_review_protocol")
                connection.exec_driver_sql("ALTER TABLE public.browser_assistance_reviews "
                    "ADD CONSTRAINT ck_browser_review_protocol CHECK (" + expression + ")")
                with pytest.raises(contract.Schema26Denied, match="browser_review_checks_mismatch"):
                    contract.observe(connection)
                transaction.rollback()
            assert contract.observe(connection)
            assert connection.scalar(text("SELECT count(*) FROM public.browser_assistance_reviews")) == before
            connection.rollback()


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
