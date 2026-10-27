"""Ordinary deployment contract checks; no app initializer or native calls."""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "infra/gcp"))
sys.path.insert(0, str(ROOT / "backend/scripts"))


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


release = load("hirewiz_current_chain_release", "infra/gcp/monetary_release.py")
step = load("hirewiz_current_chain_step", "backend/scripts/monetary_schema_step.py")
observer = load("hirewiz_current_chain_observer", "backend/scripts/monetary_cutover_preflight.py")


def test_typed_and_native_source_extend_frozen_schema18_release_without_expanding_its_forward_steps():
    tracked = {p.relative_to(ROOT).as_posix() for p in (ROOT / "backend/alembic/versions").glob("*.py")}
    assert release.source_guard.migration_chain(ROOT, tracked, allow_literal_sql=True) == (
        *release.CHAIN, "20261015_0019", "20261023_0020", "20261023_0021",
        "20261027_0022", "20261027_0023", "20261027_0024", "20261028_0025",
    )
    assert release.CUTOVER_CHAIN == step.CHAIN == observer.SCHEMA_ORDER
    assert step.CHAIN[0] == "20261008_0009" and step.CHAIN[-1] == "20261015_0018"
    assert len(step.CHAIN) == 10
    assert set(observer.EXTENSION_TABLES) == {
        "20261010_0014", "20261011_0015", "20261011_0016", "20261014_0017", "20261015_0018",
    }


def migration_root(tmp_path, extra):
    (tmp_path / "backend/alembic/versions").mkdir(parents=True)
    (tmp_path / "backend/alembic.ini").write_text("[alembic]\nscript_location = %(here)s/alembic\n")
    path = "backend/alembic/versions/synthetic.py"
    (tmp_path / path).write_text("revision='synthetic'\ndown_revision=None\nbranch_labels=None\ndepends_on=None\n" + extra)
    return tmp_path, {path}


def test_literal_sql_allowance_is_explicit_and_does_not_run_upgrade(tmp_path):
    root, paths = migration_root(tmp_path, "POSTGRES_PUBLICATION_SQL=['SELECT 1']\nPOSTGRES_REMOVE_SQL=['SELECT 2']\ndef upgrade():\n    raise AssertionError('must not execute')\n")
    with pytest.raises(release.source_guard.PreflightDenied, match="Unsupported module-level"):
        release.source_guard.migration_chain(root, paths)
    assert release.source_guard.migration_chain(root, paths, allow_literal_sql=True) == ("synthetic",)


@pytest.mark.parametrize("extra", [
    "OTHER_SQL=['SELECT 1']\n",
    "POSTGRES_PUBLICATION_SQL=[]\n",
    "POSTGRES_PUBLICATION_SQL=['']\n",
    "POSTGRES_PUBLICATION_SQL=[1]\n",
    "POSTGRES_PUBLICATION_SQL=('SELECT 1',)\n",
    "POSTGRES_PUBLICATION_SQL=['SELECT 1'] * 257\n",
    "POSTGRES_PUBLICATION_SQL=[str(1)]\n",
    "POSTGRES_PUBLICATION_SQL=['SELECT 1']\nPOSTGRES_PUBLICATION_SQL=['SELECT 2']\n",
    "from synthetic import POSTGRES_PUBLICATION_SQL\n",
    "def POSTGRES_PUBLICATION_SQL():\n    return []\n",
])
def test_opt_in_still_refuses_ambiguous_or_executable_constants(tmp_path, extra):
    root, paths = migration_root(tmp_path, extra)
    with pytest.raises(release.source_guard.PreflightDenied):
        release.source_guard.migration_chain(root, paths, allow_literal_sql=True)


def test_literal_sql_byte_and_entry_limits_are_enforced(tmp_path):
    root, paths = migration_root(tmp_path, "POSTGRES_PUBLICATION_SQL=" + repr(["x" * 131073]) + "\n")
    with pytest.raises(release.source_guard.PreflightDenied):
        release.source_guard.migration_chain(root, paths, allow_literal_sql=True)
    (root / next(iter(paths))).write_text("revision='synthetic'\ndown_revision=None\nbranch_labels=None\ndepends_on=None\nPOSTGRES_PUBLICATION_SQL=" + repr(["SELECT 1"] * 257) + "\n")
    with pytest.raises(release.source_guard.PreflightDenied):
        release.source_guard.migration_chain(root, paths, allow_literal_sql=True)


def test_native_fence_remains_unavailable_before_dispatch(monkeypatch):
    boundary = release.NativeBoundary(ROOT)
    monkeypatch.setattr(boundary, "_cloud", lambda *_: pytest.fail("No cloud call is permitted"))
    with pytest.raises(release.ReleaseDenied, match="native_external_consumer_and_provider_fence_verifier_missing"):
        boundary.verify_fences(None)


def test_new_erasure_and_pruning_tables_require_delete_but_financial_ledgers_do_not():
    assert observer.EXTENSION_TABLES["20261011_0015"] <= release.ERASABLE_TABLES
    assert {"employer_search_preparations", "employer_catalogue_snapshots",
            "employer_catalogue_snapshot_items", "employer_catalogue_winners",
            "employer_catalogue_source_revisions", "employer_catalogue_posting_revisions"} <= release.ERASABLE_TABLES
    assert {"model_cost_liabilities", "service_credit_reservations", "payment_orders"}.isdisjoint(release.ERASABLE_TABLES)
    assert "employer_host_pacing" not in release.ERASABLE_TABLES


def test_schema18_registry_and_native_reader_agree_without_changing_retained_source():
    registry = load("hirewiz_current_chain_registry", "infra/gcp/fresh_cutover_registry.py")
    neon = load("hirewiz_current_chain_neon", "backend/scripts/neon_direct_identity.py")
    rolling = load("hirewiz_current_chain_rolling", "infra/gcp/rolling_release.py")
    raw, _ = registry.expected_registry("a" * 40, "sha256:" + "b" * 64, "2026-10-15T00:00:00+00:00")
    inventory = observer.Inventory.model_validate(json.loads(raw))
    assert inventory.expected_database_schema == "20261008_0009"
    assert inventory.candidate_schema == rolling.SCHEMA == step.CHAIN[-1] == "20261015_0018"
    assert {"20261008_0009", "20261014_0017", "20261015_0018"} <= neon.RELEASE_SCHEMAS
    assert inventory.provider_fencing_verified is inventory.protected_backup_verified is False


def test_schema19_source_is_refused_before_schema18_release_reaches_external_consumers(monkeypatch):
    tracked = {p.relative_to(ROOT).as_posix() for p in (ROOT / "backend/alembic/versions").glob("*.py")}
    monkeypatch.setattr(release.source_guard, "checked_files", lambda *_: tracked)
    boundary = release.NativeBoundary(ROOT)
    monkeypatch.setattr(boundary, "_command", lambda *_: pytest.fail("Schema19 must be refused before CI/cloud calls"))
    with pytest.raises(release.ReleaseDenied, match="reviewed_schema16_chain_required"):
        boundary.verify_source_image(release="a" * 40, image_digest="sha256:" + "b" * 64,
                                     build_id="synthetic", ci_run_id=1)


@pytest.mark.parametrize("field", ["expected_database_schema", "candidate_schema"])
def test_schema18_inventory_contract_refuses_schema19(field):
    from pydantic import ValidationError

    registry = load("hirewiz_typed_boundary_registry", "infra/gcp/fresh_cutover_registry.py")
    raw, _ = registry.expected_registry("a" * 40, "sha256:" + "b" * 64, "2026-10-15T00:00:00+00:00")
    inventory = json.loads(raw)
    inventory[field] = "20261015_0019"
    with pytest.raises(ValidationError):
        observer.Inventory.model_validate(inventory)


def test_schema19_is_outside_native_identity_reader_and_schema18_step_admission():
    neon = load("hirewiz_typed_boundary_neon", "backend/scripts/neon_direct_identity.py")
    rolling = load("hirewiz_typed_boundary_rolling", "infra/gcp/rolling_release.py")
    assert "20261015_0019" not in neon.RELEASE_SCHEMAS
    assert "20261015_0019" not in observer.SCHEMAS
    assert rolling.SCHEMA == release.CHAIN[-1] == "20261015_0018"
    with pytest.raises(observer.Denied, match="application_state_reader_identity_refused"):
        observer.unresolved_application_sql("public", "20261015_0019")
    # Only dialect metadata is available; a rejected step cannot obtain a connection.
    with pytest.raises(step.StepDenied, match="migration_step_or_identity_invalid"):
        step.upgrade_step(SimpleNamespace(dialect=SimpleNamespace(name="postgresql")),
                          expected="20261015_0018", target="20261015_0019", identifier_hash="a" * 64,
                          database_oid=1, namespace="public", migration_role="synthetic_migration")


def test_schema18_keeps_candidate_reports_separate_from_paid_application_proof():
    assert observer.unresolved_application_sql("public", "20261015_0018") == observer.unresolved_application_sql("public", "20261014_0017")
    assert "'user_reported'" not in observer.unresolved_application_sql("public", "20261011_0016")


class ExtensionMetadata:
    def __init__(self):
        self.tables = [(name, "r") for name in sorted(set().union(*observer.EXTENSION_TABLES.values()))]
        self.columns = [{"relname": table, "relkind": "r", "attname": name,
            "column_type": shape[0], "attnotnull": shape[1]}
            for columns in observer.EXTENSION_COLUMNS.values() for (table, name), shape in columns.items()]

    def execute(self, statement, parameters):
        assert parameters["namespace"] == 2200
        assert str(statement).startswith("SELECT ")
        if "format_type" in str(statement):
            return SimpleNamespace(mappings=lambda: SimpleNamespace(all=lambda: self.columns))
        return SimpleNamespace(all=lambda: self.tables)


@pytest.mark.parametrize("fault", [None, "missing-table", "missing-column", "wrong-type", "nullable-guard", "old-marker"])
def test_schema18_native_metadata_reader_refuses_missing_or_downgraded_pacing_state(fault):
    connection = ExtensionMetadata()
    if fault == "missing-table":
        connection.tables = [row for row in connection.tables if row[0] != "employer_host_pacing"]
    pacing = [row for row in connection.columns if row["relname"] == "employer_host_pacing"]
    if fault == "missing-column":
        connection.columns.remove(pacing[-1])
    elif fault == "wrong-type":
        pacing[0]["column_type"] = "text"
    elif fault == "nullable-guard":
        next(row for row in pacing if row["attname"] == "recovery_required")["attnotnull"] = False
    if fault is None:
        observer._extension_shape(connection, 2200, "20261015_0018")
    else:
        version = "20261014_0017" if fault == "old-marker" else "20261015_0018"
        with pytest.raises(observer.Denied, match="database_extension_shape_mismatch"):
            observer._extension_shape(connection, 2200, version)


@pytest.mark.parametrize("delete", [False, True])
def test_global_host_pacing_runtime_permissions_need_siu_but_forbid_delete(delete):
    class Connection:
        def execute(self, statement, parameters):
            assert parameters["runtime"] == "synthetic-runtime"
            if "can_select" in str(statement):
                row = {"oid": 4242, "relname": "employer_host_pacing", "relkind": "r",
                    "can_select": True, "can_insert": True, "can_update": True,
                    "can_delete": delete, "migration_owns": True}
                return SimpleNamespace(mappings=lambda: SimpleNamespace(all=lambda: [row]))
            return SimpleNamespace(scalars=lambda: SimpleNamespace(all=list))
    if delete:
        with pytest.raises(release.ReleaseDenied, match="runtime_global_host_pacing_delete_forbidden"):
            release.verify_runtime_permissions(Connection(), "public", "synthetic-runtime", "synthetic-migration")
    else:
        release.verify_runtime_permissions(Connection(), "public", "synthetic-runtime", "synthetic-migration")
