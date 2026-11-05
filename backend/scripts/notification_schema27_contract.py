"""Exact additive26→27 SQL observation; no native/rollout/commit authority.

Immutable26 protected catalog/fence and financial helpers remain unchanged.
A caller must separately establish real TLS/cluster/database/role, source/CI,
service/IAM/flags and one-shot migration custody. Supplied labels prove none.
"""
from __future__ import annotations

import ast
import hashlib
import json
import re
from pathlib import Path

from sqlalchemy import text

from scripts import browser_schema19_step as identity_step
from scripts import evidence_writer_schema26_contract as before

BEFORE = before.SCHEMA
SCHEMA = "20261105_0027"
CONTRACT = "hirewiz_notification_schema27_v1"
TABLE = "notification_preferences"
MIGRATION = "backend/alembic/versions/20261105_0027_notification_preferences.py"
MIGRATION_SHA256 = "d22ec324634c38b6d76c677492171fb9f77458df02ec0848fd62121dcf02ab43"
DISABLED_FLAGS = (*before.previous.DISABLED_FLAGS, "NOTIFICATION_DIGESTS_ENABLED", "LIFECYCLE_EMAILS_ENABLED")
COLUMNS = {
    "user_id": ("integer", True), "subject_uuid": ("character varying(36)", True),
    "account_binding_id": ("character varying(36)", True), "principal_sha256": ("character varying(64)", True),
    "native_generation": ("bigint", True), "native_head_sha256": ("character varying(64)", True),
    "recipient_sha256": ("character varying(64)", True), "policy_version": ("character varying(80)", True),
    "enabled": ("boolean", True), "next_due_at": ("timestamp with time zone", False),
    "operation_id": ("character varying(36)", True), "updated_at": ("timestamp with time zone", True),
}


class Schema27Denied(RuntimeError):
    pass


def need(value, reason):
    if not value:
        raise Schema27Denied("schema27_" + reason)


def require_disabled(flags):
    need(type(flags) is dict and all(type(flags.get(name)) is str and flags[name] == "false"
         for name in DISABLED_FLAGS), "new_admission_must_remain_closed")


def verify_source(root: Path) -> str:
    previous = before.verify_source(root)
    path = root / MIGRATION
    need(path.is_file() and not path.is_symlink() and path.resolve() == path, "migration_source_required")
    raw = path.read_bytes()
    need(hashlib.sha256(raw).hexdigest() == MIGRATION_SHA256, "migration_source_changed")
    tree = ast.parse(raw)
    revisions = {node.targets[0].id: ast.literal_eval(node.value) for node in tree.body
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name)
        and node.targets[0].id in {"revision", "down_revision"}}
    need(revisions == {"revision": SCHEMA, "down_revision": BEFORE}, "migration_chain_changed")
    return hashlib.sha256(json.dumps({"prior": previous, "migration": MIGRATION_SHA256}, sort_keys=True).encode()).hexdigest()


def marker(connection, namespace="public"):
    before.previous.namespace_name(namespace)
    values = connection.execute(text(f'SELECT version_num FROM "{namespace}".alembic_version LIMIT 2')).scalars().all()
    need(values == [SCHEMA], "exact_marker_required")
    return SCHEMA


def preserved_shape(connection, namespace_oid, namespace="public"):
    """Compose protected26 shape directly; never relabel its26 marker as27."""
    before._browser_shape(connection, namespace_oid, namespace)
    before.reset21.previous._upload_format(connection, namespace_oid, ("pdf", "docx", "tex", "texzip"))
    before._reset_columns(connection, namespace_oid)
    before._reset_constraints(connection, namespace_oid, namespace)
    before.reset21._indexes(connection, namespace_oid)
    manifest = json.loads(before.previous.MANIFEST.read_text())
    need(before.previous.catalog_digest(connection, namespace_oid, namespace) == manifest["new_catalog_sha256"],
         "preserved_financial_catalog_changed")
    savepoint = connection.begin_nested()
    try:
        connection.exec_driver_sql(f'SET LOCAL search_path="{namespace}"')
        before.fence.require_installed(connection, namespace)
    finally:
        savepoint.rollback()


def _relation(connection, namespace_oid):
    rows = connection.execute(text("SELECT c.oid,c.relowner,c.relkind,c.relpersistence,c.relispartition,"
        "c.relrowsecurity,c.relforcerowsecurity,NOT EXISTS(SELECT 1 FROM pg_catalog.pg_inherits h "
        "WHERE h.inhrelid=c.oid OR h.inhparent=c.oid) AS no_inheritance FROM pg_catalog.pg_class c "
        "WHERE c.relnamespace=:ns AND c.relname=:table LIMIT 2"),
        {"ns": namespace_oid, "table": TABLE}).all()
    need(len(rows) == 1 and tuple(rows[0][2:]) == ("r", "p", False, False, False, True), "projection_relation_changed")
    return int(rows[0][0]), int(rows[0][1])


def _columns(connection, relation):
    rows = connection.execute(text("SELECT a.attname,pg_catalog.format_type(a.atttypid,a.atttypmod),"
        "a.attnotnull,a.attidentity,a.attgenerated,a.attcollation=t.typcollation,"
        "pg_catalog.pg_get_expr(d.adbin,d.adrelid),a.attacl "
        "FROM pg_catalog.pg_attribute a JOIN pg_catalog.pg_type t ON t.oid=a.atttypid "
        "LEFT JOIN pg_catalog.pg_attrdef d ON d.adrelid=a.attrelid AND d.adnum=a.attnum "
        "WHERE a.attrelid=:relation AND a.attnum>0 AND NOT a.attisdropped ORDER BY a.attnum LIMIT 14"),
        {"relation": relation}).all()
    expected = [(name, kind, required, "", "", True, None, None) for name, (kind, required) in COLUMNS.items()]
    need([tuple(row) for row in rows] == expected, "projection_columns_changed")


def _check(value):
    # Only conjunction-only checks discard redundant parentheses. Schedule OR
    # grouping below is compared intact, so changed precedence cannot hide.
    value = re.sub(r"'(?:[^']|'')*'|[\s()]", before.reset21.previous._keep_literal, value)
    return value.replace("'9007199254740991'::bigint", "9007199254740991")


def _constraints(connection, relation, namespace):
    rows = connection.execute(text("SELECT k.oid,k.conname,k.contype,k.convalidated,k.condeferrable,"
        "k.condeferred,k.conislocal,k.coninhcount,k.connoinherit,"
        "ARRAY(SELECT a.attname::text FROM unnest(k.conkey) WITH ORDINALITY x(num,pos) "
        "JOIN pg_catalog.pg_attribute a ON a.attrelid=k.conrelid AND a.attnum=x.num ORDER BY x.pos) AS columns,"
        "rn.nspname AS referenced_namespace,r.relname AS referenced_table,"
        "ARRAY(SELECT a.attname::text FROM unnest(k.confkey) WITH ORDINALITY x(num,pos) "
        "JOIN pg_catalog.pg_attribute a ON a.attrelid=r.oid AND a.attnum=x.num ORDER BY x.pos) AS referenced_columns,"
        "k.confupdtype,k.confdeltype,k.confmatchtype,pg_catalog.pg_get_constraintdef(k.oid) AS definition "
        "FROM pg_catalog.pg_constraint k LEFT JOIN pg_catalog.pg_class r ON r.oid=k.confrelid "
        "LEFT JOIN pg_catalog.pg_namespace rn ON rn.oid=r.relnamespace "
        "WHERE k.conrelid=:relation ORDER BY k.conname LIMIT 11"), {"relation": relation}).mappings().all()
    need(len(rows) == 10, "projection_constraint_inventory_changed")
    expected = {"notification_preferences_pkey": ("p", ("user_id",)),
        "uq_notification_pref_subject": ("u", ("subject_uuid",)),
        "uq_notification_pref_binding": ("u", ("account_binding_id",)),
        "uq_notification_pref_operation": ("u", ("operation_id",)),
        "fk_notification_pref_owner": ("f", ("user_id",))}
    checks = {
        "ck_notification_pref_generation": "CHECKnative_generation>=1ANDnative_generation<=9007199254740991",
        "ck_notification_pref_uuid_length": "CHECKlengthsubject_uuid::text=36ANDlengthaccount_binding_id::text=36ANDlengthoperation_id::text=36",
        "ck_notification_pref_hash_length": "CHECKlengthprincipal_sha256::text=64ANDlengthnative_head_sha256::text=64ANDlengthrecipient_sha256::text=64",
        "ck_notification_pref_policy": "CHECKlengthpolicy_version::text>=1ANDlengthpolicy_version::text<=80",
    }
    found, fk = set(), None
    for row in rows:
        name, kind = row["conname"], row["contype"]
        need(row["convalidated"] is True and row["condeferrable"] is False and row["condeferred"] is False
             and row["conislocal"] is True and row["coninhcount"] == 0 and name not in found,
             "projection_constraint_changed")
        found.add(name)
        if kind == "c":
            need(row["connoinherit"] is False, "projection_check_changed")
            if name == "ck_notification_pref_schedule":
                need(re.sub(r"\s+", "", row["definition"]) ==
                    "CHECK(((enabledAND(next_due_atISNOTNULL))OR((NOTenabled)AND(next_due_atISNULL))))",
                    "projection_schedule_check_changed")
            else:
                need(name in checks and _check(row["definition"]) == checks[name], "projection_check_changed")
        else:
            need(name in expected and (kind, tuple(row["columns"])) == expected[name], "projection_key_changed")
            if kind == "f":
                need((row["referenced_namespace"], row["referenced_table"], tuple(row["referenced_columns"]),
                    row["confupdtype"], row["confdeltype"], row["confmatchtype"])
                    == (namespace, "users", ("id",), "a", "c", "s"), "projection_owner_fk_changed")
                fk = row["oid"]
    need(found == set(expected) | set(checks) | {"ck_notification_pref_schedule"} and fk is not None,
         "projection_constraints_changed")
    return fk


def _indexes(connection, relation):
    rows = connection.execute(text("SELECT c.relname,am.amname,i.indisunique,i.indisprimary,"
        "i.indisvalid,i.indisready,i.indislive,i.indimmediate,i.indnullsnotdistinct,i.indnatts,i.indnkeyatts,"
        "i.indpred IS NULL AS unconditional,i.indexprs IS NULL AS plain_columns,"
        "ARRAY(SELECT a.attname::text FROM unnest(i.indkey) WITH ORDINALITY x(num,pos) "
        "LEFT JOIN pg_catalog.pg_attribute a ON a.attrelid=i.indrelid AND a.attnum=x.num ORDER BY x.pos) AS columns,"
        "ARRAY(SELECT x FROM unnest(i.indoption) x) AS options,"
        "ARRAY(SELECT x FROM unnest(i.indcollation) x) AS collations,"
        "ARRAY(SELECT a.attcollation FROM unnest(i.indkey) WITH ORDINALITY x(num,pos) "
        "LEFT JOIN pg_catalog.pg_attribute a ON a.attrelid=i.indrelid AND a.attnum=x.num ORDER BY x.pos) AS column_collations,"
        "ARRAY(SELECT n.nspname||'.'||o.opcname FROM unnest(i.indclass) WITH ORDINALITY x(num,pos) "
        "JOIN pg_catalog.pg_opclass o ON o.oid=x.num JOIN pg_catalog.pg_namespace n ON n.oid=o.opcnamespace ORDER BY x.pos) AS opclasses "
        "FROM pg_catalog.pg_index i JOIN pg_catalog.pg_class c ON c.oid=i.indexrelid "
        "JOIN pg_catalog.pg_am am ON am.oid=c.relam WHERE i.indrelid=:relation ORDER BY c.relname LIMIT 6"),
        {"relation": relation}).mappings().all()
    expected = {"notification_preferences_pkey": (True, True, ["user_id"], ["pg_catalog.int4_ops"]),
        "uq_notification_pref_subject": (True, False, ["subject_uuid"], ["pg_catalog.text_ops"]),
        "uq_notification_pref_binding": (True, False, ["account_binding_id"], ["pg_catalog.text_ops"]),
        "uq_notification_pref_operation": (True, False, ["operation_id"], ["pg_catalog.text_ops"]),
        "ix_notification_preferences_due": (False, False, ["enabled", "next_due_at", "user_id"],
            ["pg_catalog.bool_ops", "pg_catalog.timestamptz_ops", "pg_catalog.int4_ops"])}
    found = set()
    for row in rows:
        name = row["relname"]
        need(name in expected and name not in found, "projection_indexes_changed")
        found.add(name)
        unique, primary, columns, classes = expected[name]
        need(row["amname"] == "btree" and row["indisunique"] is unique and row["indisprimary"] is primary
             and all(row[key] is True for key in ("indisvalid", "indisready", "indislive", "indimmediate", "unconditional", "plain_columns"))
             and row["indnullsnotdistinct"] is False and row["indnatts"] == row["indnkeyatts"] == len(columns)
             and row["columns"] == columns and row["options"] == [0]*len(columns)
             and row["collations"] == row["column_collations"] and row["opclasses"] == classes,
             "projection_index_shape_changed")
    need(found == set(expected), "projection_indexes_changed")


def _triggers(connection, relation, fk):
    rows = connection.execute(text("SELECT c.relname,t.tgisinternal,t.tgenabled,t.tgtype,t.tgdeferrable,t.tginitdeferred,"
        "n.nspname,p.proname FROM pg_catalog.pg_trigger t JOIN pg_catalog.pg_class c ON c.oid=t.tgrelid "
        "JOIN pg_catalog.pg_proc p ON p.oid=t.tgfoid JOIN pg_catalog.pg_namespace n ON n.oid=p.pronamespace "
        "WHERE t.tgrelid=:relation OR t.tgconstraint=:fk LIMIT 5"), {"relation": relation, "fk": fk}).all()
    expected = {(TABLE, True, "O", 5, False, False, "pg_catalog", "RI_FKey_check_ins"),
        (TABLE, True, "O", 17, False, False, "pg_catalog", "RI_FKey_check_upd"),
        ("users", True, "O", 9, False, False, "pg_catalog", "RI_FKey_cascade_del"),
        ("users", True, "O", 17, False, False, "pg_catalog", "RI_FKey_noaction_upd")}
    need(len(rows) == 4 and {tuple(row) for row in rows} == expected, "projection_triggers_changed")
    need(connection.execute(text("SELECT count(*) FROM pg_catalog.pg_rewrite WHERE ev_class=:relation"),
         {"relation": relation}).scalar_one() == 0, "projection_rules_changed")


def verify_shape(connection, namespace_oid=2200, namespace="public"):
    marker(connection, namespace)
    preserved_shape(connection, namespace_oid, namespace)
    relation, _ = _relation(connection, namespace_oid)
    _columns(connection, relation)
    fk = _constraints(connection, relation, namespace)
    _indexes(connection, relation)
    _triggers(connection, relation, fk)


def verify_permissions(connection, identity):
    need(type(identity) is identity_step.MigrationIdentity, "exact_identity_required")
    identity.validate()
    relation, owner = _relation(connection, identity.namespace_oid)
    need(owner == identity.migrator_oid, "projection_owner_changed")
    rows = connection.execute(text("SELECT x.grantor,x.grantee,x.privilege_type,x.is_grantable "
        "FROM pg_catalog.pg_class c,LATERAL pg_catalog.aclexplode(coalesce(c.relacl,pg_catalog.acldefault('r',c.relowner))) x "
        "WHERE c.oid=:relation AND x.grantee<>c.relowner ORDER BY x.grantee,x.privilege_type LIMIT 7"),
        {"relation": relation}).all()
    expected = {(identity.migrator_oid, identity.runtime_oid, value, False) for value in ("SELECT", "INSERT", "UPDATE", "DELETE")}
    expected.add((identity.migrator_oid, identity.observer_oid, "SELECT", False))
    need(len(rows) == len(expected) and {tuple(row) for row in rows} == expected, "projection_acl_changed")
    roles = connection.execute(text("SELECT oid,rolname,rolcanlogin,rolinherit,rolsuper,rolcreatedb,"
        "rolcreaterole,rolreplication,rolbypassrls FROM pg_catalog.pg_roles WHERE oid IN (:runtime,:observer) ORDER BY oid"),
        {"runtime": identity.runtime_oid, "observer": identity.observer_oid}).all()
    expected_roles = sorted([(oid, actor, True, False, False, False, False, False, False)
        for oid, actor in ((identity.runtime_oid, identity.runtime), (identity.observer_oid, identity.observer))])
    need([tuple(row) for row in roles] == expected_roles, "projection_serving_roles_changed")
    for actor, writable in ((identity.runtime, True), (identity.observer, False)):
        privileges = connection.execute(text("SELECT has_table_privilege(:actor,:relation,'SELECT'),"
            "has_table_privilege(:actor,:relation,'INSERT'),has_table_privilege(:actor,:relation,'UPDATE'),"
            "has_table_privilege(:actor,:relation,'DELETE'),has_table_privilege(:actor,:relation,'TRIGGER'),"
            "has_table_privilege(:actor,:relation,'TRUNCATE'),has_table_privilege(:actor,:relation,'REFERENCES'),"
            "pg_has_role(:actor,:owner,'USAGE'),pg_has_role(:actor,:owner,'SET'),"
            "has_schema_privilege(:actor,:namespace,'CREATE'),pg_has_role(:actor,:runtime,'SET')"),
            {"actor": actor, "relation": relation, "owner": owner, "namespace": identity.namespace_oid,
             "runtime": identity.runtime_oid}).one()
        need(tuple(privileges) == (True, writable, writable, writable, False, False, False,
             False, False, False, writable), "projection_role_bypass")
    before.verify_writer_permissions(connection, runtime=identity.runtime, migrator=identity.migrator, namespace=identity.namespace)


def observe(connection, identity):
    verify_shape(connection, identity.namespace_oid, identity.namespace)
    verify_permissions(connection, identity)
    before.previous.verify_empty_new_tables(connection, identity.namespace)
    for statement in before.financial.INVARIANTS:
        need(connection.execute(text(statement)).scalar_one() == 0, "live_financial_invariant_failed")
    before.financial.liability_rows(connection)
    return hashlib.sha256(json.dumps({"contract": CONTRACT, "schema": SCHEMA,
        "migration": MIGRATION_SHA256, "financial_catalog": before.previous.catalog_digest(connection, identity.namespace_oid, identity.namespace)},
        sort_keys=True).encode()).hexdigest()
