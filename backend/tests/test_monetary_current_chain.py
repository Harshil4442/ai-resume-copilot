"""Ordinary deployment contract checks; no app initializer or native calls."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

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


def test_exact_current_chain_and_forward_steps_agree():
    tracked = {p.relative_to(ROOT).as_posix() for p in (ROOT / "backend/alembic/versions").glob("*.py")}
    assert release.source_guard.migration_chain(ROOT, tracked, allow_literal_sql=True) == release.CHAIN
    assert release.CUTOVER_CHAIN == step.CHAIN == observer.SCHEMA_ORDER
    assert step.CHAIN[0] == "20261008_0009" and step.CHAIN[-1] == "20261011_0016"
    assert len(step.CHAIN) == 8
    assert set(observer.EXTENSION_TABLES) == {"20261010_0014", "20261011_0015", "20261011_0016"}


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
