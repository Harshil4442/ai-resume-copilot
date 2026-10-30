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


def reset_columns():
    columns = dict(contract.reset21._COLUMNS) | {"purpose": "character varying(24)"}
    rows = [{"relname": "password_reset_tokens", "attname": name, "relkind": "r", "relpersistence": "p",
        "relispartition": False, "column_type": kind, "attnotnull": name not in {"native_revision_sha256", "consumed_at"},
        "attidentity": "", "attgenerated": "", "default_collation": True,
        "column_default": "'password_reset'::character varying" if name == "purpose" else None}
        for name, kind in columns.items()]
    rows.append({"relname": "users", "attname": "credential_epoch", "relkind": "r", "relpersistence": "p",
        "relispartition": False, "column_type": "bigint", "attnotnull": True, "attidentity": "", "attgenerated": "",
        "default_collation": True, "column_default": "0"})
    return rows


class ResetPort:
    def __init__(self, rows):
        self.rows = rows

    def execute(self, statement, params):
        assert "pg_catalog.pg_attribute" in str(statement) and params == {"namespace": 2200}
        return Rows(self.rows)


def test_only_the_exact26_purpose_column_is_added_and_old21_validator_stays_strict():
    rows = reset_columns()
    contract._reset_columns(ResetPort(rows), 2200)
    with pytest.raises(contract.reset21.Schema21Denied, match="column_shape_mismatch"):
        contract.reset21._columns(ResetPort(rows), 2200)


@pytest.mark.parametrize("field,value", [("column_type", "text"), ("attnotnull", False),
    ("column_default", "'privacy_erasure'::character varying"), ("attidentity", "a"), ("default_collation", False)])
def test_purpose_type_default_nullability_or_identity_drift_refuses(field, value):
    rows = reset_columns()
    next(row for row in rows if row["attname"] == "purpose")[field] = value
    with pytest.raises(contract.Schema26Denied, match="column_shape_mismatch"):
        contract._reset_columns(ResetPort(rows), 2200)


def test_an_extra_reset_column_cannot_hide_behind_the_accepted_purpose_delta():
    rows = reset_columns()
    rows.append({**rows[0], "attname": "unreviewed_authority"})
    with pytest.raises(contract.Schema26Denied, match="column_shape_mismatch"):
        contract._reset_columns(ResetPort(rows), 2200)


def reset_constraints():
    common = {"relname": "password_reset_tokens", "convalidated": True, "condeferrable": False,
        "condeferred": False, "conislocal": True, "coninhcount": 0, "connoinherit": False,
        "referenced_namespace": None, "referenced_table": None, "referenced_columns": [],
        "confupdtype": "a", "confdeltype": "a", "confmatchtype": "s", "definition": "", "columns": []}
    rows = [{**common, "contype": "p", "conname": "password_reset_tokens_pkey", "columns": ["id"]},
        {**common, "contype": "u", "conname": "password_reset_tokens_token_sha256_key", "columns": ["token_sha256"]},
        {**common, "contype": "f", "conname": "password_reset_tokens_user_id_fkey", "columns": ["user_id"],
            "referenced_namespace": "public", "referenced_table": "users", "referenced_columns": ["id"], "confdeltype": "c"}]
    for field, values in (("status", ("PENDING", "PROCESSING", "COMPLETED", "INVALIDATED", "UNAVAILABLE")),
                          ("purpose", ("password_reset", "privacy_erasure"))):
        definition = f"CHECK (({field})::text = ANY (ARRAY[" + ",".join(
            f"'{value}'::character varying" for value in values) + "]::text[]))"
        rows.append({**common, "contype": "c", "conname": "ck_password_reset_" + field, "definition": definition})
    return rows


class ConstraintsPort:
    def __init__(self, rows):
        self.rows = rows

    def execute(self, statement, params):
        assert "pg_catalog.pg_constraint" in str(statement)
        assert params == {"namespace": 2200, "tables": ["password_reset_tokens"]}
        return Rows(self.rows)


def test26_keeps_exact_status_pk_unique_fk_and_adds_only_the_bound_purpose_check():
    rows = reset_constraints()
    contract._reset_constraints(ConstraintsPort(rows), 2200, "public")
    with pytest.raises(contract.reset21.Schema21Denied, match="reset_status_check_mismatch"):
        contract.reset21._constraints(ConstraintsPort(rows), 2200, "public")


@pytest.mark.parametrize("kind", ["missing_purpose", "unknown_check", "loosen_purpose", "foreign_fk", "deferred"])
def test_no_other_constraint_change_is_admitted_by_the_purpose_delta(kind):
    rows = reset_constraints()
    if kind == "missing_purpose":
        rows.pop()
    elif kind == "unknown_check":
        rows.append({**rows[-1], "conname": "unreviewed_check"})
    elif kind == "loosen_purpose":
        rows[-1]["definition"] = rows[-1]["definition"][:-1] + " OR true)"
    elif kind == "foreign_fk":
        rows[2]["referenced_namespace"] = "other_owner"
    else:
        rows[2]["condeferrable"] = True
    with pytest.raises(contract.Schema26Denied):
        contract._reset_constraints(ConstraintsPort(rows), 2200, "public")
