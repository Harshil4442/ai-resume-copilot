"""Finite source/catalog-port refusals, never native SQL/role acceptance."""
from pathlib import Path

import pytest

from scripts import evidence_writer_schema26_contract as contract

ROOT = Path(__file__).resolve().parents[2]


class Rows:
    def __init__(self, value):
        self.value = value

    def mappings(self):
        return self

    def scalars(self):
        return self

    def all(self):
        return self.value

    def scalar_one(self):
        return self.value


def acl():
    return {"may_trigger": False, "may_truncate": False, "owns_table": False, "may_create": False,
        "exact_function_owner": True, "rolsuper": False, "rolcreaterole": False, "rolcreatedb": False,
        "rolreplication": False, "rolbypassrls": False}


class CatalogPort:
    def __init__(self):
        self.rows, self.defaults, self.marker, self.version = [acl()], 0, None, contract.SCHEMA
        self.calls = []

    def execute(self, statement, params=None):
        sql = str(statement)
        self.calls.append((sql, params))
        assert sql.startswith("SELECT ")
        if "version_num" in sql:
            return Rows([self.version])
        if "pg_db_role_setting" in sql:
            return Rows(self.defaults)
        if "current_setting" in sql:
            return Rows(self.marker)
        assert "has_table_privilege" in sql and params == {
            "namespace": "public", "runtime": "synthetic_runtime", "migrator": "synthetic_migrator"}
        return Rows(self.rows)


def test_exact_reviewed26_sources_include_the_original25_chain_without_changing_it():
    assert contract.verify_source(ROOT)


@pytest.mark.parametrize("name", [contract.MIGRATION, contract.DEFINITION])
def test_changed_migration_or_canonical_ddl_cannot_reuse_reviewed_source(monkeypatch, name):
    original = Path.read_bytes
    monkeypatch.setattr(Path, "read_bytes", lambda path: original(path) + b"\n# different source\n"
        if path == ROOT / name else original(path))
    with pytest.raises(contract.Schema26Denied, match="source_changed"):
        contract.verify_source(ROOT)


def test26_and_frozen25_marker_contracts_are_disjoint():
    port = CatalogPort()
    assert contract.marker(port) == contract.SCHEMA
    with pytest.raises(contract.previous.Schema25Denied, match="exact_marker_required"):
        contract.previous.marker(port)
    port.version = contract.BEFORE
    assert contract.previous.marker(port) == contract.BEFORE
    with pytest.raises(contract.Schema26Denied, match="exact_marker_required"):
        contract.marker(port)


def test_runtime_cannot_bypass_trigger_or_inherit_old_writer_marker():
    port = CatalogPort()
    contract.verify_writer_permissions(port, runtime="synthetic_runtime", migrator="synthetic_migrator")
    assert len(port.calls) == 3


@pytest.mark.parametrize("field", tuple(acl()))
def test_each_privilege_or_function_owner_change_refuses_before_native_acceptance(field):
    port = CatalogPort()
    port.rows[0][field] = not port.rows[0][field]
    with pytest.raises(contract.Schema26Denied, match="writer_bypass_privilege"):
        contract.verify_writer_permissions(port, runtime="synthetic_runtime", migrator="synthetic_migrator")
    assert len(port.calls) == 1


@pytest.mark.parametrize("kind", ["marker", "defaults", "missing", "duplicate"])
def test_prearmed_defaults_or_ambiguous_catalog_do_not_authorize_legacy_code(kind):
    port = CatalogPort()
    if kind == "marker":
        port.marker = "cas_v1"
    elif kind == "defaults":
        port.defaults = 1
    elif kind == "missing":
        port.rows = []
    else:
        port.rows.append(acl())
    with pytest.raises(contract.Schema26Denied):
        contract.verify_writer_permissions(port, runtime="synthetic_runtime", migrator="synthetic_migrator")
