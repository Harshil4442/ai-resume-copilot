"""Fixed source SQL ownership transition; provider role deletion is separate.

No SQL or credential access occurs at import. Execute only inside the reviewed
operation owner after final human approval. No DROP/DROP OWNED/customer DML.
"""
from __future__ import annotations

import json
import os
import re
import secrets
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, cast

import native_fresh_fence as native
from psycopg import sql
from sqlalchemy.dialects import postgresql
from sqlalchemy.engine import make_url

from scripts import database_transfer as transfer
from scripts import legacy_credential_inventory as retained
from scripts.neon_execution_files import LinuxExecutionFiles

SOURCE_HOST = "ep-damp-rice-aosknaqp.c-2.ap-southeast-1.aws.neon.tech"
SOURCE_CLUSTER = "cabc69e0dc6343b8474c3e4d7fc4ca48e8a93e45c1694d1961a93ee56e4385f0"
DATABASE_OID = 16391
OLD_NAME, OLD_OID = "neondb_owner", 16387
OWNER_NAME = "hirewiz_source_owner_20261014_r1"
OBSERVER_NAME, OBSERVER_OID = "hirewiz_observer_20261010_r1", 1884182
RUNTIME_NAME, RUNTIME_OID = "hirewiz_runtime_20261010_r1", 1884178
MIGRATOR_NAME, MIGRATOR_OID = "hirewiz_migrator_20261010_r1", 1884180
OLD_SECRET = "projects/157225724590/secrets/hirewiz-database-url/versions/1"
ROLE_ROWS = "SELECT oid,rolname,rolcanlogin,rolinherit,rolsuper,rolcreatedb,rolcreaterole,rolreplication,rolbypassrls FROM pg_catalog.pg_roles ORDER BY oid LIMIT 257"
MEMBERS = "SELECT roleid,member,grantor,admin_option,inherit_option,set_option FROM pg_catalog.pg_auth_members ORDER BY roleid,member,grantor LIMIT 257"


class SourceActionDenied(RuntimeError):
    """Fixed safe refusal; caller never emits vendor exceptions or values."""


def require(value: Any, code: str) -> None:
    if not value:
        raise SourceActionDenied(code)


def remaining(deadline: float) -> None:
    require(time.monotonic() < deadline, "source_action_deadline")


def once(workdir: Path, phase: str) -> None:
    st = workdir.lstat()
    require(workdir.is_absolute() and not workdir.is_symlink() and workdir.is_dir()
            and st.st_uid == os.getuid() and st.st_mode & 0o777 == 0o700,
            "source_action_private_owner_directory")
    fd = os.open(workdir / (phase + ".started.json"), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(json.dumps({"phase": phase, "automatic_replay": False}).encode() + b"\n")
        stream.flush()
        os.fsync(stream.fileno())
    fd = os.open(workdir, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def validate_origin(origin: transfer.BoundDatabase) -> None:
    require(type(origin) is transfer.BoundDatabase and origin.restore_role is None,
            "source_original_actor_binding")
    ident = origin.identity
    require((ident.host, ident.port, ident.database, ident.cluster_sha256, ident.database_oid,
             ident.actor, ident.actor_oid, ident.transport) ==
            (SOURCE_HOST, 5432, "neondb", SOURCE_CLUSTER, DATABASE_OID, OLD_NAME, OLD_OID, "verify-full"),
            "source_original_native_identity")
    ident.validate()


def original_owner(boundary: native.NativeFreshFenceBoundary, files: LinuxExecutionFiles) -> transfer.BoundDatabase:
    """Actual numeric SM metadata/access/checksum joins; URL stays in RAM."""
    require(type(boundary) is native.NativeFreshFenceBoundary and type(files) is LinuxExecutionFiles,
            "source_original_native_reader")
    files.validate()
    try:
        _, value = retained._secret_value(boundary.reads, OLD_SECRET)
        require(type(value) is str, "source_original_secret_unavailable")
        uri = make_url(cast(str, value))
        require((uri.host, uri.database, uri.username) == (SOURCE_HOST, "neondb", OLD_NAME)
                and uri.port in (None, 5432) and uri.drivername in {"postgresql", "postgresql+psycopg"}
                and set(uri.query) <= {"sslmode", "channel_binding"}
                and uri.query.get("sslmode", "require") in {"require", "verify-ca", "verify-full"}
                and uri.query.get("channel_binding", "prefer") in {"prefer", "require"}
                and type(uri.password) is str and bool(uri.password), "source_original_secret_target")
        binding: Literal["prefer", "require"] = (
            "require" if uri.query.get("channel_binding", "prefer") == "require" else "prefer"
        )
        identity = transfer.NativeDatabaseIdentity(SOURCE_HOST, 5432, "neondb", SOURCE_CLUSTER,
            DATABASE_OID, OLD_NAME, OLD_OID, files.root_certificate,
            channel_binding=binding)
        result = transfer.BoundDatabase(identity, cast(str, uri.password))
        validate_origin(result)
        return result
    except SourceActionDenied:
        raise
    except Exception:
        raise SourceActionDenied("source_original_secret_or_binding_unavailable") from None


@dataclass(frozen=True, slots=True, repr=False)
class PreparedOwner:
    oid: int
    name: str = OWNER_NAME

    def validate(self) -> None:
        require(self.name == OWNER_NAME and type(self.oid) is int and 0 < self.oid < 2**32
                and self.oid not in {10, 16387, 1884182, 1884178, 1884180}, "source_prepared_owner_identity")


@dataclass(frozen=True, slots=True, repr=False)
class SourcePasswords:
    observer: str = field(repr=False)


def new_source_passwords() -> SourcePasswords:
    # The trusted caller keeps these until every uncertain SQL/SM outcome is
    # reconciled. Nothing is written to a receipt or passed on an argv.
    return SourcePasswords(secrets.token_urlsafe(48))


@dataclass(frozen=True, slots=True, repr=False)
class SQLTransition:
    owner: PreparedOwner
    observer_password: str = field(repr=False)
    provider_role_deletion_required: bool = True


def snapshot(connection: Any) -> tuple[list[tuple[Any, ...]], list[tuple[Any, ...]]]:
    roles = [tuple(row) for row in connection.execute(ROLE_ROWS).fetchall()]
    members = [tuple(row) for row in connection.execute(MEMBERS).fetchall()]
    require(len(roles) <= 256 and len(members) <= 256, "source_role_metadata_bound")
    names = {row[1]: row for row in roles}
    for name, oid in ((OLD_NAME, OLD_OID), (OBSERVER_NAME, OBSERVER_OID),
                      (RUNTIME_NAME, RUNTIME_OID), (MIGRATOR_NAME, MIGRATOR_OID)):
        require(name in names and names[name][0] == oid, "source_role_oid_drift")
    require(names[OLD_NAME][5] is True and names[OLD_NAME][6] is True,
            "source_controller_create_authority_missing")
    for name in (OBSERVER_NAME, RUNTIME_NAME, MIGRATOR_NAME):
        require(names[name][2:] == (False, False, False, False, False, False, False),
                "source_prepared_role_attributes_drift")
    observer_paths = connection.execute("SELECT count(*) FROM pg_catalog.pg_auth_members WHERE member=1884182 AND (admin_option OR inherit_option OR set_option)").fetchone()
    definers = connection.execute("SELECT count(*) FROM pg_catalog.pg_proc p JOIN pg_catalog.pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname='public' AND p.prosecdef").fetchone()
    require(tuple(observer_paths or ()) == (0,) and tuple(definers or ()) == (0,),
            "source_observer_membership_or_definer_path")
    row = connection.execute("SELECT d.oid,d.datdba,n.oid,n.nspowner FROM pg_catalog.pg_database d CROSS JOIN pg_catalog.pg_namespace n WHERE d.datname=pg_catalog.current_database() AND n.nspname='public'").fetchone()
    require(row is not None and tuple(row[:3]) == (DATABASE_OID, OLD_OID, 2200)
            and row[3] in {OLD_OID, 6171}, "source_before_owner_drift")
    require(connection.execute("SELECT version_num FROM public.alembic_version LIMIT 2").fetchall() == [("20261008_0009",)], "source_schema_nine_required")
    outside = connection.execute("SELECT count(*) FROM pg_catalog.pg_shdepend WHERE refclassid='pg_catalog.pg_authid'::regclass AND refobjid=16387 AND dbid NOT IN (0,16391)").fetchone()
    otherdb = connection.execute("SELECT count(*) FROM pg_catalog.pg_database WHERE datdba=16387 AND oid<>16391").fetchone()
    require(tuple(outside or ()) == (0,) and tuple(otherdb or ()) == (0,), "source_other_database_dependencies")
    require(not connection.execute("SELECT 1 FROM pg_catalog.pg_prepared_xacts WHERE database='neondb' LIMIT 1").fetchone(), "source_prepared_transaction_present")
    return roles, members


def prepare_owner(origin: transfer.BoundDatabase, workdir: Path, *, seconds: int = 30) -> PreparedOwner:
    """One additive NOLOGIN role, actual observed OID; current data/owner unchanged."""
    require(type(seconds) is int and 1 <= seconds <= 55, "source_action_budget")
    deadline = time.monotonic() + seconds
    validate_origin(origin)
    connection = origin.connect()  # Genuine TLS/control/cluster/actor proof first.
    try:
        with connection.transaction():
            connection.execute("SET TRANSACTION READ ONLY")
            connection.execute("SET LOCAL statement_timeout='1000ms'")
            before_roles, before_members = snapshot(connection)
            require(all(row[1] != OWNER_NAME for row in before_roles), "source_owner_exists_reconcile_only")
        remaining(deadline)
        once(workdir, "source-owner-create")
        with connection.transaction():
            connection.execute("SET TRANSACTION READ WRITE")
            connection.execute("SET LOCAL statement_timeout='5000ms'")
            connection.execute(sql.SQL("CREATE ROLE {} NOLOGIN NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS").format(sql.Identifier(OWNER_NAME)))
            roles, members = snapshot(connection)
            added = [row for row in roles if row not in before_roles]
            require(len(added) == 1 and added[0][1:] == (OWNER_NAME, False, False, False, False, False, False, False)
                    and [row for row in roles if row[1] != OWNER_NAME] == before_roles,
                    "source_owner_role_delta")
            owner = PreparedOwner(added[0][0])
            owner.validate()
            require(sorted(members) == sorted([*before_members, (owner.oid, OLD_OID, 10, True, False, False)]),
                    "source_owner_membership_delta")
            remaining(deadline)
        return owner
    except SourceActionDenied:
        raise
    except Exception:
        raise SourceActionDenied("source_owner_create_unknown_reconcile_only") from None
    finally:
        connection.close()


def verify_cloud_isolated(boundary: native.NativeFreshFenceBoundary) -> None:
    """Real native old UID/deletion/disable reads, not a supplied cut boolean."""
    scope = boundary.scope
    require(type(boundary) is native.NativeFreshFenceBoundary and scope is not None,
            "source_retained_native_scope_missing")
    assert scope is not None
    key_module = boundary._module("backend/scripts/model_key_revocation_observer.py", "source_retirement_keys")
    for key in scope.keys:
        row = boundary._get("keys", key.name)
        metadata = key_module._metadata(row, key.name.rsplit("/keys/", 1)[0], native.datetime.now(native.UTC))
        require(metadata["uid"] == key.uid and metadata["delete_time"] is not None
                and (row.get("serviceAccountEmail") or None) == key.issuer, "source_old_key_not_retired")
    for principal in scope.principals:
        row = boundary._get("iam", "projects/-/serviceAccounts/" + principal.email)
        require(row.get("email") == principal.email and row.get("uniqueId") == principal.uid
                and row.get("disabled") is True, "source_old_principal_not_isolated")


def verify_source_quiescent(boundary: native.NativeFreshFenceBoundary, connection: Any) -> None:
    """Reuse actual admission and existing schema9 aggregate settlement checks."""
    boundary.verify_admission_closed()
    observer = boundary._module("backend/scripts/monetary_cutover_preflight.py", "source_action_financial")
    # Existing helper uses SQLAlchemy execute(text); the native driver below is
    # psycopg, so only this tiny execution shim adapts its fixed SELECT results.
    class Rows:
        def __init__(self, cursor: Any):
            self.cursor = cursor

        def scalars(self) -> Any:
            cursor = self.cursor
            class Scalars:
                def all(self) -> list[Any]:
                    return [row[0] for row in cursor.fetchall()]
            return Scalars()

        def scalar_one(self) -> Any:
            rows = self.cursor.fetchall()
            require(len(rows) == 1 and len(rows[0]) == 1, "source_financial_scalar_shape")
            return rows[0][0]

        def all(self) -> list[Any]:
            return self.cursor.fetchall()

        def mappings(self) -> Any:
            cursor = self.cursor
            class Mappings:
                def all(self) -> list[dict[str, Any]]:
                    names = [item.name for item in cursor.description]
                    return [dict(zip(names, row, strict=True)) for row in cursor.fetchall()]
            return Mappings()

    class Readonly:
        def execute(self, statement: Any, parameters: Any = None) -> Rows:
            prepared = statement.bindparams(**(parameters or {}))
            compiled = prepared.compile(dialect=postgresql.dialect(paramstyle="pyformat"))
            require(str(compiled).lstrip().startswith("SELECT"), "source_financial_select_contract")
            return Rows(connection.execute(str(compiled), compiled.params))

    native.fresh.verify_financial_state(Readonly(), observer, native.release.CUTOVER_CHAIN[0])


def transition_source(origin: transfer.BoundDatabase, owner: PreparedOwner,
                      boundary: native.NativeFreshFenceBoundary, workdir: Path, passwords: SourcePasswords, *, seconds: int = 55) -> SQLTransition:
    """Ownership/ACL transition only; not a Fence and not provider role deletion.

    Passwords are returned only to the trusted same-process Secret Manager writer.
    No source rows are updated/deleted and no owned objects are dropped. Existing
    old role ADMIN edges are intentionally not claimed removed by SQL REVOKE.
    """
    require(type(seconds) is int and 1 <= seconds <= 55, "source_action_budget")
    deadline = time.monotonic() + seconds
    validate_origin(origin)
    owner.validate()
    require(type(passwords) is SourcePasswords and type(passwords.observer) is str
            and re.fullmatch(r"[A-Za-z0-9_-]{64}", passwords.observer), "source_password_generation_shape")
    observer_password = passwords.observer
    verify_cloud_isolated(boundary)
    remaining(deadline)
    connection = origin.connect()
    try:
        with connection.transaction():
            connection.execute("SET TRANSACTION READ ONLY")
            connection.execute("SET LOCAL statement_timeout='1000ms'")
            roles, _ = snapshot(connection)
            verify_source_quiescent(boundary, connection)
            require((owner.oid, OWNER_NAME, False, False, False, False, False, False, False) in roles,
                    "source_prepared_owner_drift")
            defaults = connection.execute("SELECT count(*) FROM pg_catalog.pg_default_acl WHERE defaclrole=16387").fetchone()
            require(tuple(defaults or ()) == (0,), "source_old_default_acl_requires_exact_cleanup")
            require(connection.execute("SELECT pg_catalog.has_function_privilege(%s,'pg_catalog.pg_control_system()','EXECUTE')", (OBSERVER_OID,)).fetchone() == (True,), "source_observer_control_permission_missing")
        once(workdir, "source-owner-transition")
        with connection.transaction():
            connection.execute("SET TRANSACTION READ WRITE")
            connection.execute("SET LOCAL statement_timeout='5000ms'")
            connection.execute(sql.SQL("ALTER ROLE {} LOGIN PASSWORD {}").format(sql.Identifier(OBSERVER_NAME), sql.Literal(observer_password)))
            connection.execute(sql.SQL("GRANT {} TO {} WITH SET TRUE").format(sql.Identifier(OWNER_NAME), sql.Identifier(OLD_NAME)))
            connection.execute(sql.SQL("GRANT {} TO {} WITH INHERIT FALSE").format(sql.Identifier(OWNER_NAME), sql.Identifier(OLD_NAME)))
            connection.execute(sql.SQL("REASSIGN OWNED BY {} TO {}").format(sql.Identifier(OLD_NAME), sql.Identifier(OWNER_NAME)))
            connection.execute(sql.SQL("SET ROLE {}").format(sql.Identifier(OWNER_NAME)))
            transfer.verify_effective_role(connection, origin.identity, transfer.NativeRestoreRole(OWNER_NAME, owner.oid))
            for role in (OLD_NAME, RUNTIME_NAME, MIGRATOR_NAME, OBSERVER_NAME):
                for target in ("ALL TABLES IN SCHEMA public", "ALL SEQUENCES IN SCHEMA public", "SCHEMA public", "DATABASE neondb"):
                    connection.execute(sql.SQL("REVOKE ALL PRIVILEGES ON " + target + " FROM {}").format(sql.Identifier(role)))
            connection.execute("REVOKE CREATE ON SCHEMA public FROM PUBLIC")
            connection.execute(sql.SQL("GRANT CONNECT ON DATABASE neondb TO {}").format(sql.Identifier(OBSERVER_NAME)))
            connection.execute(sql.SQL("GRANT USAGE ON SCHEMA public TO {}").format(sql.Identifier(OBSERVER_NAME)))
            connection.execute(sql.SQL("GRANT SELECT ON ALL TABLES IN SCHEMA public TO {}").format(sql.Identifier(OBSERVER_NAME)))
            connection.execute(sql.SQL("GRANT SELECT ON ALL SEQUENCES IN SCHEMA public TO {}").format(sql.Identifier(OBSERVER_NAME)))
            row = connection.execute("SELECT datdba FROM pg_catalog.pg_database WHERE oid=16391").fetchone()
            require(tuple(row or ()) == (owner.oid,), "source_new_database_owner_not_observed")
            # Database objects/ACL dependencies must all be gone before the later
            # control-plane role DELETE. Role-membership edges are handled by it.
            dependent = connection.execute("SELECT count(*) FROM pg_catalog.pg_shdepend WHERE refclassid='pg_catalog.pg_authid'::regclass AND refobjid=16387 AND classid<>'pg_catalog.pg_auth_members'::regclass").fetchone()
            require(tuple(dependent or ()) == (0,), "source_old_object_or_acl_dependencies_remain")
            connection.execute("RESET ROLE")
            connection.execute("ALTER ROLE neondb_owner PASSWORD NULL")  # Own password; no false self-ADMIN assumption.
            remaining(deadline)
        # The own-password change has committed. This still-open authenticated
        # old session can terminate only its own exact source client sessions;
        # no broader pg_signal_backend grant or provider process exclusion.
        with connection.transaction():
            connection.execute("SET TRANSACTION READ ONLY")
            sessions = connection.execute("SELECT pid,usesysid,datid,backend_type FROM pg_catalog.pg_stat_activity WHERE usesysid IN (16387,1884178,1884180) AND pid<>pg_catalog.pg_backend_pid() LIMIT 65").fetchall()
            require(len(sessions) <= 64 and all(row[1:] == (OLD_OID, DATABASE_OID, "client backend") for row in sessions),
                    "source_unexpected_retired_role_session")
            for pid, role, database, _ in sessions:
                remaining(deadline)
                terminated = connection.execute("SELECT pg_catalog.pg_terminate_backend(pid,1000) FROM pg_catalog.pg_stat_activity WHERE pid=%s AND usesysid=%s AND datid=%s AND backend_type='client backend'", (pid, role, database)).fetchall()
                require(terminated in ([], [(True,)]), "source_old_session_termination_unconfirmed")
            remaining(deadline)
        return SQLTransition(owner, observer_password)
    except SourceActionDenied:
        raise
    except Exception:
        raise SourceActionDenied("source_transition_unknown_reconcile_only") from None
    finally:
        connection.close()



def observer_target(origin: transfer.BoundDatabase, transition: SQLTransition) -> transfer.BoundDatabase:
    """The source owner stays NOLOGIN. Only the known observer can authenticate."""
    validate_origin(origin)
    transition.owner.validate()
    identity = transfer.NativeDatabaseIdentity(SOURCE_HOST, 5432, "neondb", SOURCE_CLUSTER,
        DATABASE_OID, OBSERVER_NAME, OBSERVER_OID, origin.identity.root_certificate)
    return transfer.BoundDatabase(identity, transition.observer_password)


def verify_provider_delete_ready(observer: transfer.BoundDatabase, owner: PreparedOwner) -> None:
    """Actual native no-dependency/no-old-session proof before fixed API DELETE."""
    owner.validate()
    require(type(observer) is transfer.BoundDatabase and observer.identity.actor == OBSERVER_NAME
            and observer.identity.actor_oid == OBSERVER_OID and observer.identity.host == SOURCE_HOST
            and observer.identity.cluster_sha256 == SOURCE_CLUSTER
            and observer.identity.database_oid == DATABASE_OID and observer.restore_role is None,
            "source_observer_binding")
    connection = observer.connect()
    try:
        with connection.transaction():
            connection.execute("SET TRANSACTION READ ONLY")
            row = connection.execute("SELECT datdba FROM pg_catalog.pg_database WHERE oid=16391").fetchone()
            require(tuple(row or ()) == (owner.oid,), "source_new_database_owner_not_observed")
            role = connection.execute("SELECT oid,rolname FROM pg_catalog.pg_roles WHERE rolname='neondb_owner'").fetchall()
            require(role == [(OLD_OID, OLD_NAME)], "source_old_role_oid_drift")
            dependencies = connection.execute("SELECT count(*) FROM pg_catalog.pg_shdepend WHERE refclassid='pg_catalog.pg_authid'::regclass AND refobjid=16387 AND classid<>'pg_catalog.pg_auth_members'::regclass").fetchone()
            sessions = connection.execute("SELECT count(*) FROM pg_catalog.pg_stat_activity WHERE usesysid=16387").fetchone()
            require(tuple(dependencies or ()) == (0,) and tuple(sessions or ()) == (0,),
                    "source_provider_delete_dependencies_or_sessions")
    except SourceActionDenied:
        raise
    except Exception:
        raise SourceActionDenied("source_provider_delete_readiness_unavailable") from None
    finally:
        connection.close()



def verify_provider_delete_complete(observer: transfer.BoundDatabase, owner: PreparedOwner) -> None:
    """Fresh actual role/OID and member/grantor absence; no deletion receipt flag."""
    owner.validate()
    require(type(observer) is transfer.BoundDatabase and observer.identity.actor == OBSERVER_NAME
            and observer.identity.actor_oid == OBSERVER_OID and observer.identity.host == SOURCE_HOST
            and observer.identity.cluster_sha256 == SOURCE_CLUSTER
            and observer.identity.database_oid == DATABASE_OID and observer.restore_role is None,
            "source_observer_binding")
    connection = observer.connect()
    try:
        with connection.transaction():
            connection.execute("SET TRANSACTION READ ONLY")
            row = connection.execute("SELECT datdba FROM pg_catalog.pg_database WHERE oid=16391").fetchone()
            require(tuple(row or ()) == (owner.oid,), "source_new_database_owner_not_observed")
            roles = connection.execute("SELECT count(*) FROM pg_catalog.pg_roles WHERE oid=16387 OR rolname='neondb_owner'").fetchone()
            memberships = connection.execute("SELECT count(*) FROM pg_catalog.pg_auth_members WHERE roleid=16387 OR member=16387 OR grantor=16387").fetchone()
            sessions = connection.execute("SELECT count(*) FROM pg_catalog.pg_stat_activity WHERE usesysid=16387").fetchone()
            require(tuple(roles or ()) == tuple(memberships or ()) == tuple(sessions or ()) == (0,),
                    "source_old_role_memberships_or_sessions_remain")
    except SourceActionDenied:
        raise
    except Exception:
        raise SourceActionDenied("source_provider_delete_result_unavailable") from None
    finally:
        connection.close()


def preflight_source(origin: transfer.BoundDatabase) -> dict[str, Any]:
    """Bound native read-only prerequisites; no marker/mutation/customer output."""
    validate_origin(origin)
    connection = origin.connect()
    try:
        with connection.transaction():
            connection.execute("SET TRANSACTION READ ONLY")
            connection.execute("SET LOCAL statement_timeout='1000ms'")
            roles, members = snapshot(connection)
            defaults = connection.execute("SELECT count(*) FROM pg_catalog.pg_default_acl WHERE defaclrole=16387").fetchone()
            creator = next(row for row in roles if row[0] == OLD_OID)
            return {"source_database_oid": DATABASE_OID, "source_owner_oid": OLD_OID,
                    "source_role_count": len(roles), "source_membership_count": len(members),
                    "original_actor_createrole": creator[6], "original_actor_createdb": creator[5],
                    "planned_owner_absent": all(row[1] != OWNER_NAME for row in roles),
                    "old_default_acl_count": defaults[0] if defaults else None,
                    "source_mutation_performed": False, "native_fence_complete": False}
    except SourceActionDenied:
        raise
    except Exception:
        raise SourceActionDenied("source_preflight_unavailable") from None
    finally:
        connection.close()
