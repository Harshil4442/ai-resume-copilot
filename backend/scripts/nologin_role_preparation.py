"""Bounded schema0009 NOLOGIN role/grant preparation; never release authority.

This action has no credential/provider transport or CLI. A separately reviewed
manifest and existing authenticated SQL engine are required. It changes only three
new object-free roles and explicit existing-object grants in one transaction.
"""
from __future__ import annotations

import ast
import hashlib
import json
import re
from pathlib import Path
from typing import Any, Literal

from psycopg import Connection as PsycopgConnection
from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr, model_validator
from sqlalchemy import text

from scripts import monetary_cutover_preflight as session_preflight
from scripts import neon_direct_identity as neon_identity

SOURCE_HEAD = "1c2519bc3989c60405dce5154d8e6bd73067d826"
SCHEMA = "20261008_0009"
RELEASE_SOURCE_SHA256 = "727c08d35520ca9ed84ff250686306e7d15bf4fb7673121d18009a9abe8993d2"
MAX_ROWS = 4096
TABLE_NAMES = frozenset({
    "admin_audit_events", "alembic_version", "analysis_request_keys", "analysis_runs",
    "application_events", "career_memory_entries", "dispatch_outbox", "employer_admissions",
    "employer_application_approvals", "employer_application_attempts", "employer_application_batches",
    "employer_applications", "employer_artifact_deletions", "employer_artifact_uploads",
    "employer_job_deliveries", "employer_postings", "employer_searches", "employer_sources",
    "entitlement_ledger", "evidence_items", "job_matches", "model_call_events", "notification_outbox",
    "opportunities", "opportunity_contacts", "payment_events", "payment_orders", "payment_refunds",
    "payment_transactions", "prompt_versions", "reminders", "resume_versions", "resumes",
    "sealed_application_artifacts", "service_credit_events", "service_credit_reservations",
    "skill_coverage", "usage_events", "user_profiles", "users",
})
SEQUENCE_NAMES = frozenset({
    "entitlement_ledger_id_seq", "job_matches_id_seq", "payment_events_id_seq", "payment_orders_id_seq",
    "payment_refunds_id_seq", "payment_transactions_id_seq", "resumes_id_seq", "user_profiles_id_seq",
    "users_id_seq",
})
ROLE_FLAGS = ("rolsuper", "rolinherit", "rolcreaterole", "rolcreatedb", "rolcanlogin", "rolreplication", "rolbypassrls")


class PreparationDenied(RuntimeError):
    """Fixed nonsecret refusal only. Commit uncertainty must be reconciled, never retried."""


def _digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()


def _rows(connection: Any, query: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    rows = [dict(row) for row in connection.execute(text(query + f" LIMIT {MAX_ROWS + 1}"), params or {}).mappings()]
    if len(rows) > MAX_ROWS:
        raise PreparationDenied("preparation_catalog_bound")
    return rows


def _identifier(name: str) -> str:
    if not isinstance(name, str) or not re.fullmatch(r"[a-z][a-z0-9_]{0,62}", name):
        raise PreparationDenied("preparation_identifier_invalid")
    return '"' + name + '"'


def _erasable() -> frozenset[str]:
    path = Path(__file__).resolve().parents[2] / "infra/gcp/monetary_release.py"
    if hashlib.sha256(path.read_bytes()).hexdigest() != RELEASE_SOURCE_SHA256:
        raise PreparationDenied("preparation_source_contract_unavailable")
    tree = ast.parse(path.read_text())
    values = [node for node in tree.body if isinstance(node, ast.Assign)
              and any(isinstance(target, ast.Name) and target.id == "ERASABLE_TABLES" for target in node.targets)]
    if len(values) != 1:
        raise PreparationDenied("preparation_source_contract_unavailable")
    result = ast.literal_eval(values[0].value)
    if not isinstance(result, set) or any(not isinstance(name, str) for name in result):
        raise PreparationDenied("preparation_source_contract_unavailable")
    return frozenset(result)


class RelationPin(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    oid: StrictInt = Field(gt=0, le=4294967295)
    name: StrictStr
    kind: Literal["r", "S"]
    owner: StrictInt = Field(gt=0, le=4294967295)
    namespace_oid: StrictInt = Field(gt=0, le=4294967295)
    acl: tuple[StrictStr, ...] | None


class PreparationManifest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    format_version: Literal[1] = 1
    source_head: Literal["1c2519bc3989c60405dce5154d8e6bd73067d826"]
    schema: Literal["20261008_0009"]
    namespace: StrictStr
    namespace_oid: StrictInt = Field(gt=0, le=4294967295)
    namespace_owner: StrictInt = Field(gt=0, le=4294967295)
    database_oid: StrictInt = Field(gt=0, le=4294967295)
    database_name: StrictStr
    database_owner: StrictInt = Field(gt=0, le=4294967295)
    actor_name: StrictStr
    actor_oid: StrictInt = Field(gt=0, le=4294967295)
    system_identifier_sha256: StrictStr = Field(pattern=r"^[a-f0-9]{64}$")
    protected_state_sha256: StrictStr = Field(pattern=r"^[a-f0-9]{64}$")
    acl_state_sha256: StrictStr = Field(pattern=r"^[a-f0-9]{64}$")
    dependency_state_sha256: StrictStr = Field(pattern=r"^[a-f0-9]{64}$")
    relations: tuple[RelationPin, ...]
    runtime: StrictStr
    migration: StrictStr
    observer: StrictStr

    @model_validator(mode="after")
    def exact_scope(self) -> PreparationManifest:
        names = (self.runtime, self.migration, self.observer)
        for name in (*names, self.namespace, self.actor_name):
            _identifier(name)
        if len(set(names)) != 3 or self.actor_name in names or any(name.startswith(("pg_", "neon_")) for name in names):
            raise PreparationDenied("preparation_role_identity_invalid")
        tables = [row for row in self.relations if row.kind == "r"]
        sequences = [row for row in self.relations if row.kind == "S"]
        if (len(self.relations) != 49 or len({row.oid for row in self.relations}) != 49
                or {row.name for row in tables} != TABLE_NAMES or len(tables) != 40
                or {row.name for row in sequences} != SEQUENCE_NAMES or len(sequences) != 9
                or any(row.namespace_oid != self.namespace_oid or row.owner != self.actor_oid for row in self.relations)):
            raise PreparationDenied("preparation_relation_manifest_invalid")
        return self


def _authenticated(connection: Any, neon_binding: neon_identity.NeonDirectProxyBinding | None = None) -> dict[str, Any]:
    if neon_binding is not None:
        neon_identity.verify_transport(connection, neon_binding)
    try:
        driver = connection.connection.driver_connection
        if not isinstance(driver, PsycopgConnection):
            raise PreparationDenied("preparation_protocol_unavailable")
        info = driver.info
        user, pid, version = info.user, info.backend_pid, info.server_version
        if (not isinstance(user, str) or not user or len(user.encode()) > 63 or "\x00" in user
                or (neon_binding is None and (type(pid) is not int or pid <= 0))
                or (neon_binding is not None and not neon_identity.cancellation_key_shape(pid))
                or type(version) is not int or not 170000 <= version < 180000):
            raise PreparationDenied("preparation_protocol_unavailable")
    except PreparationDenied:
        raise
    except Exception:
        raise PreparationDenied("preparation_protocol_unavailable") from None
    identity = dict(connection.execute(text(
        "SELECT current_user::text AS current_name, session_user::text AS session_name, "
        "pg_catalog.pg_backend_pid() AS pid, current_setting('server_version_num')::int AS version, "
        "d.oid::bigint AS database_oid,d.datname AS database_name,d.datdba::bigint AS database_owner, "
        "r.oid::bigint AS actor_oid,r.rolcreaterole AS createrole,r.rolsuper AS superuser, "
        "(SELECT system_identifier::text FROM pg_catalog.pg_control_system()) AS system_identifier "
        "FROM pg_catalog.pg_database d JOIN pg_catalog.pg_roles r ON r.rolname=current_user "
        "WHERE d.datname=current_database()"
    )).mappings().one())
    if (identity["current_name"] != user or identity["session_name"] != user or (neon_binding is None and identity["pid"] != pid)
            or identity["version"] != version or not identity["createrole"] or identity["superuser"]):
        raise PreparationDenied("preparation_authenticated_actor_mismatch")
    function = _rows(connection,
        "SELECT p.oid::bigint AS oid,p.proowner::bigint AS owner,p.prosecdef,p.prosrc,l.lanname "
        "FROM pg_catalog.pg_proc p JOIN pg_catalog.pg_namespace n ON n.oid=p.pronamespace "
        "JOIN pg_catalog.pg_language l ON l.oid=p.prolang "
        "WHERE n.nspname='pg_catalog' AND p.proname='pg_control_system' AND p.pronargs=0 ORDER BY p.oid")
    if function != [{"oid":3441,"owner":10,"prosecdef":False,"prosrc":"pg_control_system","lanname":"internal"}]:
        raise PreparationDenied("preparation_native_control_contract_mismatch")
    identity["system_identifier_sha256"] = hashlib.sha256(identity.pop("system_identifier").encode()).hexdigest()
    if neon_binding is not None:
        session_preflight._native_session_ids(connection, identity["database_oid"], neon_binding=neon_binding)
    return identity


def _snapshot(connection: Any, namespace: str, *, neon_binding: neon_identity.NeonDirectProxyBinding | None = None) -> dict[str, Any]:
    identity = _authenticated(connection, neon_binding)
    if neon_binding is not None and namespace != neon_identity.NAMESPACE:
        raise PreparationDenied("preparation_neon_namespace_mismatch")
    params = {"namespace": namespace, "actor": identity["actor_oid"]}
    schemas = _rows(connection,"SELECT oid::bigint AS oid,nspname AS name,nspowner::bigint AS owner,nspacl::text[] AS acl FROM pg_catalog.pg_namespace WHERE nspname=:namespace ORDER BY oid",params)
    if len(schemas) != 1:
        raise PreparationDenied("preparation_namespace_missing")
    schema = schemas[0]
    relations = _rows(connection,
        "SELECT c.oid::bigint AS oid,c.relname AS name,c.relkind AS kind,c.relowner::bigint AS owner,c.relnamespace::bigint AS namespace_oid,c.relacl::text[] AS acl "
        "FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace "
        "WHERE n.nspname=:namespace AND c.relkind IN ('r','p','S','v','m','f') ORDER BY c.oid",params)
    quoted = _identifier(namespace)
    marker = list(connection.execute(text(f"SELECT version_num FROM {quoted}.alembic_version LIMIT 3")).scalars())
    if marker != [SCHEMA]:
        raise PreparationDenied("preparation_schema_marker_mismatch")
    roles = _rows(connection,
        "SELECT oid::bigint AS oid,rolname AS name,rolsuper,rolinherit,rolcreaterole,rolcreatedb,rolcanlogin,rolreplication,rolbypassrls,rolvaliduntil::text AS valid_until FROM pg_catalog.pg_roles ORDER BY oid")
    memberships = _rows(connection,
        "SELECT roleid::bigint AS role,member::bigint AS member,grantor::bigint AS grantor,admin_option,inherit_option,set_option FROM pg_catalog.pg_auth_members ORDER BY roleid,member,grantor")
    owned = _rows(connection,
        "SELECT oid::bigint AS oid,relname AS name,relnamespace::bigint AS namespace_oid,relkind AS kind,relowner::bigint AS owner,relrowsecurity,relforcerowsecurity FROM pg_catalog.pg_class WHERE relowner=:actor ORDER BY oid",params)
    types = _rows(connection,"SELECT oid::bigint AS oid,typowner::bigint AS owner,typname,typtype,typrelid::bigint AS typrelid,typnamespace::bigint AS namespace_oid FROM pg_catalog.pg_type WHERE typowner=:actor ORDER BY oid",params)
    routines = _rows(connection,"SELECT oid::bigint AS oid,pronamespace::bigint AS namespace_oid,proowner::bigint AS owner,prosecdef,proacl::text[] AS acl FROM pg_catalog.pg_proc WHERE proowner=:actor OR prosecdef ORDER BY oid",params)
    defaults = _rows(connection,"SELECT oid::bigint AS oid,defaclrole::bigint AS owner,defaclnamespace::bigint AS namespace_oid,defaclobjtype,defaclacl::text[] AS acl FROM pg_catalog.pg_default_acl ORDER BY oid")
    policies = _rows(connection,"SELECT oid::bigint AS oid,polrelid::bigint AS relation_oid,polname,polroles::bigint[] AS roles FROM pg_catalog.pg_policy ORDER BY oid")
    extensions = _rows(connection,"SELECT oid::bigint AS oid,extname,extowner::bigint AS owner,extnamespace::bigint AS namespace_oid,extversion FROM pg_catalog.pg_extension ORDER BY oid")
    events = _rows(connection,"SELECT oid::bigint AS oid,evtname,evtowner::bigint AS owner,evtenabled FROM pg_catalog.pg_event_trigger ORDER BY oid")
    databases = _rows(connection,"SELECT oid::bigint AS oid,datname AS name,datdba::bigint AS owner FROM pg_catalog.pg_database WHERE datdba=:actor ORDER BY oid",params)
    dependencies = _rows(connection,
        "SELECT d.classid::bigint AS classid,d.objid::bigint AS objid,d.objsubid,d.refclassid::bigint AS refclassid,d.refobjid::bigint AS refobjid,d.refobjsubid,d.deptype "
        "FROM pg_catalog.pg_depend d WHERE (d.classid='pg_catalog.pg_class'::regclass AND d.objid IN (SELECT oid FROM pg_catalog.pg_class WHERE relnamespace=:namespace_oid)) "
        "OR (d.refclassid='pg_catalog.pg_class'::regclass AND d.refobjid IN (SELECT oid FROM pg_catalog.pg_class WHERE relnamespace=:namespace_oid)) ORDER BY classid,objid,objsubid,refclassid,refobjid,refobjsubid,deptype",{"namespace_oid":schema["oid"]})
    columns = _rows(connection,
        "SELECT a.attrelid::bigint AS relation_oid,a.attnum,a.attname,a.atttypid::bigint AS type_oid,a.attnotnull,a.attisdropped,a.attidentity,a.attgenerated,a.attacl::text[] AS acl "
        "FROM pg_catalog.pg_attribute a JOIN pg_catalog.pg_class c ON c.oid=a.attrelid "
        "WHERE c.relnamespace=:namespace_oid AND a.attnum>0 ORDER BY a.attrelid,a.attnum",{"namespace_oid":schema["oid"]})
    acl = _rows(connection,
        "SELECT 'relation' AS scope,c.oid::bigint AS oid,c.relowner::bigint AS owner,x.grantor::bigint AS grantor,x.grantee::bigint AS grantee,x.privilege_type AS privilege,x.is_grantable AS grantable "
        "FROM pg_catalog.pg_class c CROSS JOIN LATERAL pg_catalog.aclexplode(COALESCE(c.relacl,pg_catalog.acldefault(CASE WHEN c.relkind='S' THEN 's'::\"char\" ELSE 'r'::\"char\" END,c.relowner))) x WHERE c.relnamespace=:namespace_oid AND c.relkind IN ('r','S') "
        "UNION ALL SELECT 'namespace',n.oid::bigint,n.nspowner::bigint,x.grantor::bigint,x.grantee::bigint,x.privilege_type,x.is_grantable FROM pg_catalog.pg_namespace n CROSS JOIN LATERAL pg_catalog.aclexplode(COALESCE(n.nspacl,pg_catalog.acldefault('n',n.nspowner))) x WHERE n.oid=:namespace_oid "
        "UNION ALL SELECT 'database',d.oid::bigint,d.datdba::bigint,x.grantor::bigint,x.grantee::bigint,x.privilege_type,x.is_grantable FROM pg_catalog.pg_database d CROSS JOIN LATERAL pg_catalog.aclexplode(COALESCE(d.datacl,pg_catalog.acldefault('d',d.datdba))) x WHERE d.datname=current_database() "
        "ORDER BY scope,oid,grantor,grantee,privilege,grantable",{"namespace_oid":schema["oid"]})
    grantors = {"database": identity["actor_oid"], "namespace":identity["actor_oid"], "relation":identity["actor_oid"]}
    for scope,owner in (("database",identity["database_owner"]),("namespace",schema["owner"])):
        if connection.execute(text("SELECT pg_catalog.pg_has_role(:actor,:owner,'USAGE')"),{"actor":identity["actor_oid"],"owner":owner}).scalar_one():
            grantors[scope] = owner
    outside_acl = _rows(connection,"SELECT oid::bigint AS oid,relacl::text[] AS acl FROM pg_catalog.pg_class WHERE relowner=:actor AND NOT (relnamespace=:namespace_oid AND relkind IN ('r','S')) ORDER BY oid",{"namespace_oid":schema["oid"],"actor":identity["actor_oid"]})
    protected = {"outside_acl":outside_acl,"roles":roles,"memberships":memberships,"owned":owned,"types":types,"routines":routines,"defaults":defaults,"policies":policies,"extensions":extensions,"events":events,"databases":databases,
                 "schema":{key: value for key,value in schema.items() if key!="acl"},
                 "database":{key:identity[key] for key in ("database_oid","database_name","database_owner")}}
    return {"identity":identity,"schema":schema,"relations":relations,"protected":protected,"dependencies":{"dependencies":dependencies,"columns":columns},"acl":acl,"grantors":grantors}


def review_manifest(connection: Any, namespace: str, *, runtime: str, migration: str, observer: str,
                    neon_binding: neon_identity.NeonDirectProxyBinding | None = None) -> PreparationManifest:
    """Metadata-only capture for private review. This is not permission to apply."""
    connection.exec_driver_sql("SET LOCAL search_path=pg_catalog")
    snapshot = _snapshot(connection, namespace, neon_binding=neon_binding)
    identity, schema = snapshot["identity"], snapshot["schema"]
    return PreparationManifest.model_validate({
        "source_head":SOURCE_HEAD,"schema":SCHEMA,"namespace":namespace,"namespace_oid":schema["oid"],"namespace_owner":schema["owner"],
        "database_oid":identity["database_oid"],"database_name":identity["database_name"],"database_owner":identity["database_owner"],
        "actor_name":identity["current_name"],"actor_oid":identity["actor_oid"],"system_identifier_sha256":identity["system_identifier_sha256"],
        "protected_state_sha256":_digest(snapshot["protected"]),"acl_state_sha256":_digest(snapshot["acl"]),"dependency_state_sha256":_digest(snapshot["dependencies"]),
        "relations":snapshot["relations"],"runtime":runtime,"migration":migration,"observer":observer})


def _preconditions(snapshot: dict[str, Any], manifest: PreparationManifest) -> None:
    identity, schema = snapshot["identity"], snapshot["schema"]
    if any(identity[key] != getattr(manifest,key) for key in ("database_oid","database_name","database_owner","actor_oid","system_identifier_sha256")) or identity["current_name"] != manifest.actor_name:
        raise PreparationDenied("preparation_identity_drift")
    if schema["oid"] != manifest.namespace_oid or schema["owner"] != manifest.namespace_owner:
        raise PreparationDenied("preparation_namespace_drift")
    actual = [RelationPin.model_validate(row).model_dump(mode="json") for row in snapshot["relations"]]
    expected = [row.model_dump(mode="json") for row in manifest.relations]
    if actual != expected or _digest(snapshot["dependencies"]) != manifest.dependency_state_sha256:
        raise PreparationDenied("preparation_object_or_dependency_drift")
    if _digest(snapshot["protected"]) != manifest.protected_state_sha256 or _digest(snapshot["acl"]) != manifest.acl_state_sha256:
        raise PreparationDenied("preparation_protected_state_drift")
    proposed = {manifest.runtime,manifest.migration,manifest.observer}
    if any(row["name"] in proposed for row in snapshot["protected"]["roles"]):
        raise PreparationDenied("preparation_role_collision")


def _grant_specs(manifest: PreparationManifest, erasable: frozenset[str]) -> list[tuple[str,int,str,str]]:
    specs = [("database",manifest.database_oid,role,"CONNECT") for role in (manifest.runtime,manifest.migration,manifest.observer)]
    specs += [("namespace",manifest.namespace_oid,role,"USAGE") for role in (manifest.runtime,manifest.migration,manifest.observer)]
    specs.append(("namespace",manifest.namespace_oid,manifest.migration,"CREATE"))
    for relation in manifest.relations:
        specs.append(("relation",relation.oid,manifest.observer,"SELECT"))
        if relation.kind == "S":
            specs.append(("relation",relation.oid,manifest.runtime,"USAGE"))
        else:
            specs.append(("relation",relation.oid,manifest.runtime,"SELECT"))
            if relation.name != "alembic_version":
                specs += [("relation",relation.oid,manifest.runtime,privilege) for privilege in ("INSERT","UPDATE")]
            if relation.name in erasable:
                specs.append(("relation",relation.oid,manifest.runtime,"DELETE"))
    return specs


def _postconditions(before: dict[str, Any], after: dict[str, Any], manifest: PreparationManifest, specs: list[tuple[str,int,str,str]]) -> dict[str, Any]:
    expected_names = {manifest.runtime,manifest.migration,manifest.observer}
    new_roles = [row for row in after["protected"]["roles"] if row["name"] in expected_names]
    if len(new_roles)!=3 or any(any(row[flag] for flag in ROLE_FLAGS) or row["valid_until"] is not None for row in new_roles):
        raise PreparationDenied("preparation_new_role_state_mismatch")
    by_name={row["name"]:row["oid"] for row in new_roles}
    new_oids=set(by_name.values())
    expected_memberships=[{"role":oid,"member":manifest.actor_oid,"grantor":10,"admin_option":True,"inherit_option":False,"set_option":False} for oid in sorted(new_oids)]
    added=[row for row in after["protected"]["memberships"] if row not in before["protected"]["memberships"]]
    if added != expected_memberships:
        raise PreparationDenied("preparation_creator_membership_mismatch")
    preserved={**after["protected"],"roles":[row for row in after["protected"]["roles"] if row["oid"] not in new_oids],"memberships":[row for row in after["protected"]["memberships"] if row not in expected_memberships]}
    if preserved != before["protected"] or after["dependencies"] != before["dependencies"]:
        raise PreparationDenied("preparation_protected_poststate_drift")
    if (after["identity"] != before["identity"] or after["schema"]["oid"] != before["schema"]["oid"]
            or [{k:v for k,v in row.items() if k!="acl"} for row in after["relations"]]
            != [{k:v for k,v in row.items() if k!="acl"} for row in before["relations"]]):
        raise PreparationDenied("preparation_identity_or_owner_poststate_drift")
    baseline={_digest(row):row for row in before["acl"]}
    expected=list(baseline.values())
    owner_by_scope={('database',manifest.database_oid):manifest.database_owner,('namespace',manifest.namespace_oid):manifest.namespace_owner,**{('relation',row.oid):row.owner for row in manifest.relations}}
    for scope,oid,name,privilege in specs:
        expected.append({"scope":scope,"oid":oid,"owner":owner_by_scope[(scope,oid)],"grantor":before["grantors"][scope],"grantee":by_name[name],"privilege":privilege,"grantable":False})
    if {_digest(row) for row in after["acl"]} != {_digest(row) for row in expected}:
        raise PreparationDenied("preparation_acl_poststate_mismatch")
    return {"state":"prepared_nologin_only","source_head":SOURCE_HEAD,"schema":SCHEMA,"new_role_count":3,"login_enabled_count":0,
            "table_count":40,"sequence_count":9,"creator_admin_edges":3,"creator_set_edges":0,"creator_inherit_edges":0,
            "legacy_permissions_preserved":True,"ownership_transferred":False,"default_privileges_changed":False,
            "credentials_created":False,"replacement_isolation_proven":False,"release_authorized":False,"cutover_ready":False}


def prepare(engine: Any, manifest: PreparationManifest, *, neon_binding: neon_identity.NeonDirectProxyBinding | None = None) -> dict[str, Any]:
    """Atomic preparation only; a failed/uncertain commit never reports success."""
    manifest = PreparationManifest.model_validate(manifest)
    if neon_binding is not None and neon_binding.purpose != "preparation":
        raise PreparationDenied("preparation_neon_purpose_mismatch")
    erasable = _erasable()
    mutation_started = False
    try:
        with engine.connect() as connection:
            transaction = connection.begin()
            try:
                connection.exec_driver_sql("SET LOCAL search_path=pg_catalog")
                connection.exec_driver_sql("SET LOCAL lock_timeout='4s'")
                connection.exec_driver_sql("SET LOCAL statement_timeout='10s'")
                connection.exec_driver_sql("SET LOCAL idle_in_transaction_session_timeout='120s'")
                connection.exec_driver_sql("SET LOCAL createrole_self_grant=''")
                before = _snapshot(connection,manifest.namespace,neon_binding=neon_binding)
                _preconditions(before,manifest)
                namespace = _identifier(manifest.namespace)
                # Bounded table locks hold every table's OID/name/owner through commit.
                tables = ','.join(namespace+'.'+_identifier(row.name) for row in manifest.relations if row.kind=='r')
                connection.exec_driver_sql(f"LOCK TABLE {tables} IN ACCESS SHARE MODE")
                _preconditions(_snapshot(connection,manifest.namespace,neon_binding=neon_binding),manifest)
                names=(manifest.runtime,manifest.migration,manifest.observer)
                for name in names:
                    mutation_started = True
                    connection.exec_driver_sql(f"CREATE ROLE {_identifier(name)} NOLOGIN NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS")
                specs = _grant_specs(manifest,erasable)
                relations={row.oid:row for row in manifest.relations}
                for scope,oid,name,privilege in specs:
                    if scope=='database':
                        target='DATABASE '+_identifier(manifest.database_name)
                    elif scope=='namespace':
                        target='SCHEMA '+namespace
                    else:
                        row=relations[oid]
                        target=('SEQUENCE ' if row.kind=='S' else 'TABLE ')+namespace+'.'+_identifier(row.name)
                    connection.exec_driver_sql(f"GRANT {privilege} ON {target} TO {_identifier(name)}")
                after = _snapshot(connection,manifest.namespace,neon_binding=neon_binding)
                created_oids=[row["oid"] for row in after["protected"]["roles"] if row["name"] in names]
                owned_count=connection.execute(text("SELECT (SELECT count(*) FROM pg_catalog.pg_class WHERE relowner=ANY(:oids))+(SELECT count(*) FROM pg_catalog.pg_type WHERE typowner=ANY(:oids))+(SELECT count(*) FROM pg_catalog.pg_proc WHERE proowner=ANY(:oids))+(SELECT count(*) FROM pg_catalog.pg_namespace WHERE nspowner=ANY(:oids))+(SELECT count(*) FROM pg_catalog.pg_database WHERE datdba=ANY(:oids))+(SELECT count(*) FROM pg_catalog.pg_default_acl WHERE defaclrole=ANY(:oids))"),{"oids":created_oids}).scalar_one()
                if owned_count != 0:
                    raise PreparationDenied("preparation_new_role_ownership_unexpected")
                result = _postconditions(before,after,manifest,specs)
            except Exception as error:
                try:
                    transaction.rollback()
                except Exception:
                    raise PreparationDenied("preparation_transaction_outcome_unknown") from None
                if isinstance(error,PreparationDenied):
                    raise PreparationDenied(str(error)) from None
                raise PreparationDenied("preparation_failed_rolled_back") from None
            try:
                transaction.commit()
            except Exception:
                raise PreparationDenied("preparation_transaction_outcome_unknown") from None
    except PreparationDenied:
        raise
    except Exception:
        reason = "preparation_transaction_outcome_unknown" if mutation_started else "preparation_connection_unavailable"
        raise PreparationDenied(reason) from None
    return result
