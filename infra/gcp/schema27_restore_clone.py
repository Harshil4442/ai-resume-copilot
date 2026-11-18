"""QA-only schema27 clone observations; no production binding or restore authority.

Only install_marker writes, into one nonce-owned schema outside the application.
Provider ownership, restore once/ACK custody and cleanup belong to the caller.
Receipts contain metadata and digests, never connection URIs or application rows.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import time
from contextlib import contextmanager
from dataclasses import asdict, dataclass, fields
from datetime import UTC, datetime
from ipaddress import ip_address
from urllib.parse import unquote, urlencode, urlsplit

import certifi
import psycopg
from infra.gcp import schema27_continuity_bootstrap as bootstrap
from sqlalchemy import create_engine, text
from sqlalchemy.pool import NullPool

from scripts import neon_continuity_identity as production
from scripts import notification_schema27_contract as schema27

MAX_SECONDS = 120
MAX_TABLES = 256
MAX_ROWS = 10_000_000
MAX_BYTES = 2 * 1024**3
MAX_ROW_BYTES = 16 * 1024**2
MAX_CHECKPOINT_AGE = 6 * 3600
HASH = re.compile(r"[a-f0-9]{64}")
LSN = re.compile(r"[0-9A-F]{1,8}/[0-9A-F]{1,8}")
NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_$-]{0,62}")
FINANCIAL_TABLES = frozenset({
    "users", "resumes", "resume_uploads", "resume_versions", "evidence_items",
    "payment_orders", "payment_transactions", "payment_events", "entitlement_ledger",
    "credit_purchase_quotes", "credit_accounts", "credit_grants", "credit_events",
    "credit_reservations", "credit_allocations", "credit_execution_holds",
    "credit_execution_attempts", "model_cost_liabilities", "service_credit_reservations",
    "service_credit_events", "employer_applications", "employer_application_approvals",
    "employer_application_attempts", "employer_admissions", "sealed_application_artifacts",
    "dispatch_outbox",
})


class RestoreCloneDenied(RuntimeError):
    """Fixed nonsecret refusal, including unknown QA marker mutation outcomes."""


def need(value, code):
    if not value:
        raise RestoreCloneDenied("schema27_restore_clone_" + code)


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def sha(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def _record(cls, value):
    need(type(value) is dict and set(value) == {f.name for f in fields(cls)}, "receipt_fields")
    return dict(value)


def _integer(value, low=1, high=2**32 - 1):
    return type(value) is int and low <= value <= high


def _identifier(value):
    need(type(value) is str and NAME.fullmatch(value), "identifier_invalid")
    return '"' + value + '"'


def _lsn(value):
    need(type(value) is str and LSN.fullmatch(value), "lsn_invalid")
    high, low = value.split("/")
    return (int(high, 16) << 32) + int(low, 16)


def _utc(value):
    need(type(value) is str and len(value) <= 40, "checkpoint_time_invalid")
    try:
        stamp = datetime.fromisoformat(value)
    except ValueError:
        raise RestoreCloneDenied("schema27_restore_clone_checkpoint_time_invalid") from None
    offset = stamp.utcoffset()
    if stamp.tzinfo is None or offset is None:
        raise RestoreCloneDenied("schema27_restore_clone_checkpoint_time_invalid")
    need(offset.total_seconds() == 0, "checkpoint_time_invalid")
    return stamp


@dataclass(frozen=True, slots=True)
class ClonePin:
    project: str
    branch: str
    endpoint: str
    host: str
    nonce: str
    database: str = "neondb"

    def validate(self):
        need(self.project == production.PROJECT and self.database == production.DATABASE
             and type(self.nonce) is str and re.fullmatch(r"[a-f0-9]{32}", self.nonce)
             and type(self.branch) is str and re.fullmatch(r"br-[a-z0-9-]{1,80}", self.branch)
             and self.branch != production.BRANCH
             and type(self.endpoint) is str and re.fullmatch(r"ep-[a-z0-9-]{1,80}", self.endpoint)
             and self.endpoint != production.ENDPOINT
             and type(self.host) is str
             and self.host == self.endpoint + ".c-8.us-east-2.aws.neon.tech"
             and self.host != production.HOST, "qa_pin_required")

    @classmethod
    def from_dict(cls, value):
        result = cls(**_record(cls, value))
        result.validate()
        return result


@dataclass(frozen=True, slots=True)
class RoleIdentity:
    name: str
    oid: int
    can_login: bool
    inherit: bool
    superuser: bool
    create_db: bool
    create_role: bool
    replication: bool
    bypass_rls: bool

    def validate(self):
        _identifier(self.name)
        need(_integer(self.oid) and all(type(getattr(self, f.name)) is bool
             for f in fields(self) if f.name not in {"name", "oid"}), "role_identity_invalid")

    @classmethod
    def from_dict(cls, value):
        result = cls(**_record(cls, value))
        result.validate()
        return result


@dataclass(frozen=True, slots=True)
class CloneIdentity:
    cluster_sha256: str
    database_oid: int
    database_owner: str
    database_owner_oid: int
    actor: str
    actor_oid: int
    public_oid: int
    roles: tuple[RoleIdentity, ...]

    def validate(self):
        need(type(self.cluster_sha256) is str and HASH.fullmatch(self.cluster_sha256)
             and all(_integer(getattr(self, key)) for key in
                     ("database_oid", "database_owner_oid", "actor_oid", "public_oid"))
             and self.actor == self.database_owner == production.DATABASE_OWNER
             and type(self.roles) is tuple and 1 <= len(self.roles) <= 256, "identity_invalid")
        for role in self.roles:
            need(type(role) is RoleIdentity, "role_identity_invalid")
            role.validate()
        need(len({r.name for r in self.roles}) == len(self.roles)
             and len({r.oid for r in self.roles}) == len(self.roles)
             and any((r.name, r.oid) == (self.actor, self.actor_oid) for r in self.roles), "role_identity_invalid")

    @classmethod
    def from_dict(cls, value):
        row = _record(cls, value)
        need(type(row["roles"]) in (list, tuple), "receipt_fields")
        row["roles"] = tuple(RoleIdentity.from_dict(r) for r in row["roles"])
        result = cls(**row)
        result.validate()
        return result


@dataclass(frozen=True, slots=True)
class TableDigest:
    schema: str
    name: str
    rows: int
    bytes: int
    sha256: str

    def validate(self):
        need(type(self.schema) is str and self.schema in {"public", "neon_auth"}, "table_schema_invalid")
        _identifier(self.name)
        need(_integer(self.rows, 0, MAX_ROWS) and _integer(self.bytes, 0, MAX_BYTES)
             and type(self.sha256) is str and HASH.fullmatch(self.sha256), "table_digest_invalid")

    @classmethod
    def from_dict(cls, value):
        result = cls(**_record(cls, value))
        result.validate()
        return result


@dataclass(frozen=True, slots=True)
class SequenceState:
    schema: str
    name: str
    last_value: int
    is_called: bool

    def validate(self):
        need(type(self.schema) is str and self.schema in {"public", "neon_auth"}, "sequence_schema_invalid")
        _identifier(self.name)
        need(_integer(self.last_value, -(2**63), 2**63 - 1)
             and type(self.is_called) is bool, "sequence_state_invalid")

    @classmethod
    def from_dict(cls, value):
        result = cls(**_record(cls, value))
        result.validate()
        return result


@dataclass(frozen=True, slots=True)
class CloneSnapshot:
    pin: ClonePin
    identity: CloneIdentity
    schema: str
    checkpoint_lsn: str
    checkpoint_utc: str
    catalogue_sha256: str
    tables: tuple[TableDigest, ...]
    sequences: tuple[SequenceState, ...]
    financial_sha256: str
    marker_present: bool = False

    def validate(self):
        need(type(self.pin) is ClonePin and type(self.identity) is CloneIdentity, "snapshot_identity_invalid")
        self.pin.validate()
        self.identity.validate()
        _lsn(self.checkpoint_lsn)
        _utc(self.checkpoint_utc)
        need(self.schema == schema27.SCHEMA and type(self.marker_present) is bool
             and all(type(v) is str and HASH.fullmatch(v) for v in
                     (self.catalogue_sha256, self.financial_sha256))
             and type(self.tables) is tuple and 1 <= len(self.tables) <= MAX_TABLES
             and type(self.sequences) is tuple and len(self.sequences) <= MAX_TABLES, "snapshot_invalid")
        for rows, cls in ((self.tables, TableDigest), (self.sequences, SequenceState)):
            for row in rows:
                need(type(row) is cls, "snapshot_rows_invalid")
                row.validate()
            need(len({(r.schema, r.name) for r in rows}) == len(rows), "snapshot_rows_invalid")
        need(sum(t.bytes for t in self.tables) <= MAX_BYTES, "snapshot_bytes_exceeded")

    @property
    def digest(self):
        self.validate()
        return sha(asdict(self))

    @property
    def stable_digest(self):
        self.validate()
        return sha({k: v for k, v in asdict(self).items()
                    if k not in {"checkpoint_lsn", "checkpoint_utc", "marker_present"}})

    @classmethod
    def from_dict(cls, value):
        row = _record(cls, value)
        row["pin"] = ClonePin.from_dict(row["pin"])
        row["identity"] = CloneIdentity.from_dict(row["identity"])
        for name, item in (("tables", TableDigest), ("sequences", SequenceState)):
            need(type(row[name]) in (list, tuple), "receipt_fields")
            row[name] = tuple(item.from_dict(r) for r in row[name])
        result = cls(**row)
        result.validate()
        return result


@dataclass(frozen=True, slots=True)
class CloneMarker:
    pin: ClonePin
    checkpoint_sha256: str
    checkpoint_lsn: str
    commit_lsn: str | None = None

    @property
    def schema(self):
        return "hirewiz_restore_qa_" + self.pin.nonce

    def validate(self):
        need(type(self.pin) is ClonePin, "marker_pin_invalid")
        self.pin.validate()
        need(type(self.checkpoint_sha256) is str and HASH.fullmatch(self.checkpoint_sha256), "marker_hash_invalid")
        point = _lsn(self.checkpoint_lsn)
        need(self.commit_lsn is None or _lsn(self.commit_lsn) > point, "marker_lsn_invalid")

    @classmethod
    def from_dict(cls, value):
        row = _record(cls, value)
        row["pin"] = ClonePin.from_dict(row["pin"])
        result = cls(**row)
        result.validate()
        return result


def _deadline(deadline):
    now = time.monotonic()
    need(deadline is None or type(deadline) in (int, float) and math.isfinite(deadline), "deadline_invalid")
    result = now + MAX_SECONDS if deadline is None else min(deadline, now + MAX_SECONDS)
    need(result > now, "deadline_expired")
    return result


def _remaining(deadline):
    need(time.monotonic() < deadline, "deadline_expired")


def _password(pin, deadline):
    """Read the original normal-manager connection URI only into RAM."""
    pin.validate()
    need(not any(key.startswith("PG") for key in os.environ), "ambient_pg_override")
    _remaining(deadline)
    bootstrap._check_manager()
    prefix = "/projects/" + pin.project
    project = bootstrap._get(prefix)["project"]
    need((project["id"], project["org_id"], project["region_id"], project["pg_version"]) ==
         (pin.project, bootstrap.ORG, "aws-us-east-2", 17), "provider_project_changed")
    _remaining(deadline)
    branch = bootstrap._get(prefix + "/branches/" + pin.branch)["branch"]
    need((branch["id"], branch["project_id"], branch["name"]) ==
         (pin.branch, pin.project, "hirewiz-r03-qa-" + pin.nonce), "provider_qa_branch_changed")
    if branch["parent_id"] != production.BRANCH:
        _remaining(deadline)
        held = bootstrap._get(prefix + "/branches/" + branch["parent_id"])["branch"]
        need((held["id"], held["project_id"], held["parent_id"], held["name"]) ==
             (branch["parent_id"], pin.project, production.BRANCH, "hirewiz-r03-held-" + pin.nonce)
             and held["id"] != pin.branch, "provider_qa_parent_changed")
    _remaining(deadline)
    endpoint = bootstrap._get(prefix + "/endpoints/" + pin.endpoint)["endpoint"]
    need((endpoint["id"], endpoint["project_id"], endpoint["branch_id"], endpoint["host"], endpoint["type"]) ==
         (pin.endpoint, pin.project, pin.branch, pin.host, "read_write"), "provider_qa_endpoint_changed")
    query = urlencode({"branch_id": pin.branch, "endpoint_id": pin.endpoint,
                       "database_name": pin.database, "role_name": production.DATABASE_OWNER, "pooled": "false"})
    _remaining(deadline)
    uri = urlsplit(bootstrap._get(prefix + "/connection_uri?" + query)["uri"])
    need(uri.scheme in {"postgres", "postgresql"} and unquote(uri.username or "") == production.DATABASE_OWNER
         and uri.hostname == pin.host and uri.port in (None, 5432) and uri.path == "/" + pin.database
         and uri.password and not uri.fragment, "provider_uri_changed")
    password = unquote(uri.password)
    need(0 < len(password.encode()) <= 8192 and "\x00" not in password, "provider_uri_changed")
    _remaining(deadline)
    return password


def _engine(pin, deadline, *, write=False):
    password = _password(pin, deadline)
    settings = {"host": pin.host, "dbname": pin.database, "user": production.DATABASE_OWNER,
        "sslmode": "verify-full", "sslcertmode": "disable", "sslrootcert": certifi.where(),
        "channel_binding": "require", "connect_timeout": "5", "application_name": "hirewiz_schema27_restore_qa",
        "options": "-c statement_timeout=2000 -c lock_timeout=1000 -c idle_in_transaction_session_timeout=120000"}

    def connect():
        _remaining(deadline)
        raw = psycopg.connect(**settings, port=5432, password=password)
        try:
            need(type(raw) is psycopg.Connection and not raw.closed and raw.pgconn.status == 0
                 and raw.pgconn.ssl_in_use, "tls_transport_unavailable")
            effective = raw.info.get_parameters()
            address = effective.get("hostaddr")
            need(type(address) is str and "%" not in address
                 and ip_address(address) == ip_address(raw.info.hostaddr)
                 and set(effective) <= {*settings, "port", "hostaddr"}
                 and all(effective.get(k) == v for k, v in settings.items())
                 and effective.get("port", "5432") == "5432"
                 and (raw.info.host, raw.info.dbname, raw.info.user) ==
                     (pin.host, pin.database, production.DATABASE_OWNER), "effective_transport_changed")
            raw.read_only = not write
            raw.isolation_level = psycopg.IsolationLevel.REPEATABLE_READ
            return raw
        except BaseException:
            raw.close()
            raise
    return create_engine("postgresql+psycopg://", creator=connect, poolclass=NullPool,
                         echo=False, hide_parameters=True)


@contextmanager
def _connection(pin, deadline, *, write=False):
    pin.validate()
    engine = None
    try:
        engine = _engine(pin, deadline, write=write)
        with engine.connect() as connection, connection.begin():
            connection.exec_driver_sql("SET LOCAL timezone='UTC'")
            connection.exec_driver_sql("SET LOCAL search_path=pg_catalog,public")
            connection.exec_driver_sql("SET LOCAL DateStyle='ISO, YMD'")
            connection.exec_driver_sql("SET LOCAL IntervalStyle='postgres'")
            connection.exec_driver_sql("SET LOCAL extra_float_digits=3")
            need(connection.exec_driver_sql("SHOW transaction_read_only").scalar_one() ==
                 ("off" if write else "on"), "transaction_mode_changed")
            yield connection
            _remaining(deadline)
    except RestoreCloneDenied:
        raise
    except Exception:  # noqa: BLE001 - SQL/provider errors can contain private rows/URIs.
        raise RestoreCloneDenied("schema27_restore_clone_observation_unavailable") from None
    finally:
        if engine is not None:
            engine.dispose()


def _no_other_sessions(connection):
    # A just-closed NullPool backend may still be exiting. Only repeat this
    # observation, never a write; a continuing second session always refuses.
    for attempt in range(4):
        connection.execute(text("SELECT pg_catalog.pg_stat_clear_snapshot()"))
        count = connection.execute(text("SELECT count(*) FROM pg_catalog.pg_stat_activity "
            "WHERE datid=(SELECT oid FROM pg_catalog.pg_database WHERE datname=current_database()) "
            "AND backend_type='client backend' AND pid<>pg_backend_pid()")).scalar_one()
        if count == 0:
            return
        if attempt < 3:
            time.sleep(0.05)
    need(False, "other_qa_sessions_present")


def _identity(connection):
    control = connection.execute(text("SELECT p.oid,p.proowner,p.prosecdef,p.prosrc,l.lanname "
        "FROM pg_catalog.pg_proc p JOIN pg_catalog.pg_namespace n ON n.oid=p.pronamespace "
        "JOIN pg_catalog.pg_language l ON l.oid=p.prolang "
        "WHERE n.nspname='pg_catalog' AND p.proname='pg_control_system' AND p.pronargs=0")).all()
    need([tuple(r) for r in control] == [(3441, 10, False, "pg_control_system", "internal")], "native_control_changed")
    row = connection.execute(text("SELECT (SELECT system_identifier::text FROM pg_catalog.pg_control_system()),"
        "d.oid,owner.rolname,owner.oid,current_user,r.oid,n.oid,session_user,current_database(),"
        "current_setting('server_version_num')::int/10000,current_setting('transaction_isolation') "
        "FROM pg_catalog.pg_database d JOIN pg_catalog.pg_roles owner ON owner.oid=d.datdba "
        "JOIN pg_catalog.pg_roles r ON r.rolname=current_user "
        "JOIN pg_catalog.pg_namespace n ON n.nspname='public' WHERE d.datname=current_database()")).one()
    need(type(row[0]) is str and re.fullmatch(r"[0-9]{1,30}", row[0])
         and tuple(row[7:]) == (production.DATABASE_OWNER, production.DATABASE, 17, "repeatable read"), "qa_identity_changed")
    roles = tuple(RoleIdentity(*r) for r in connection.execute(text("SELECT rolname,oid,rolcanlogin,rolinherit,"
        "rolsuper,rolcreatedb,rolcreaterole,rolreplication,rolbypassrls FROM pg_catalog.pg_roles ORDER BY rolname")))
    identity = CloneIdentity(cluster_sha256=hashlib.sha256(row[0].encode()).hexdigest(),
        database_oid=row[1], database_owner=row[2], database_owner_oid=row[3], actor=row[4],
        actor_oid=row[5], public_oid=row[6], roles=roles)
    identity.validate()
    return identity


def _stream_digest(connection, query, deadline):
    """PG17 canonical JSON text, C ordering, UTC/ISO session; never retain rows."""
    result = connection.execute(text(query).execution_options(stream_results=True, yield_per=64, max_row_buffer=64))
    digest, count, size = hashlib.sha256(), 0, 0
    try:
        while True:
            _remaining(deadline)
            batch = result.fetchmany(64)
            if not batch:
                break
            for row in batch:
                need(type(row[0]) is str, "canonical_row_invalid")
                raw = row[0].encode()
                count, size = count + 1, size + len(raw)
                need(count <= MAX_ROWS and size <= MAX_BYTES and len(raw) <= MAX_ROW_BYTES, "stream_bound_exceeded")
                digest.update(len(raw).to_bytes(8, "big"))
                digest.update(raw)
    finally:
        result.close()
    return count, size, digest.hexdigest()


CATALOGUE_QUERIES = (
    "SELECT oid,nspname,nspowner,nspacl FROM pg_catalog.pg_namespace WHERE nspname IN ('public','neon_auth')",
    "SELECT d.oid,d.datname,d.datdba,d.datacl,d.datcollate,d.datctype,d.datlocprovider FROM pg_catalog.pg_database d WHERE datname=current_database()",
    "SELECT oid,rolname,rolcanlogin,rolinherit,rolsuper,rolcreatedb,rolcreaterole,rolreplication,rolbypassrls,rolconnlimit,rolvaliduntil,rolconfig FROM pg_catalog.pg_roles",
    "SELECT * FROM pg_catalog.pg_auth_members",
    "SELECT * FROM pg_catalog.pg_db_role_setting WHERE setdatabase IN (0,(SELECT oid FROM pg_catalog.pg_database WHERE datname=current_database()))",
    "SELECT c.oid,pg_catalog.pg_get_viewdef(c.oid,true) AS viewdef FROM pg_catalog.pg_class c WHERE c.relnamespace IN (SELECT oid FROM pg_catalog.pg_namespace WHERE nspname IN ('public','neon_auth')) AND c.relkind IN ('v','m')",
    "SELECT c.oid,c.relname,c.relnamespace,c.reltype,c.reloftype,c.relowner,c.relam,c.reltablespace,c.relhasindex,c.relisshared,c.relpersistence,c.relkind,c.relnatts,c.relchecks,c.relhasrules,c.relhastriggers,c.relhassubclass,c.relrowsecurity,c.relforcerowsecurity,c.relispartition,c.relreplident,c.relacl,c.reloptions,c.relpartbound FROM pg_catalog.pg_class c WHERE c.relnamespace IN (SELECT oid FROM pg_catalog.pg_namespace WHERE nspname IN ('public','neon_auth'))",
    "SELECT a.*,pg_catalog.format_type(a.atttypid,a.atttypmod) AS formatted_type FROM pg_catalog.pg_attribute a JOIN pg_catalog.pg_class c ON c.oid=a.attrelid WHERE c.relnamespace IN (SELECT oid FROM pg_catalog.pg_namespace WHERE nspname IN ('public','neon_auth'))",
    "SELECT d.*,pg_catalog.pg_get_expr(d.adbin,d.adrelid) AS expression FROM pg_catalog.pg_attrdef d JOIN pg_catalog.pg_class c ON c.oid=d.adrelid WHERE c.relnamespace IN (SELECT oid FROM pg_catalog.pg_namespace WHERE nspname IN ('public','neon_auth'))",
    "SELECT c.*,pg_catalog.pg_get_constraintdef(c.oid) AS definition FROM pg_catalog.pg_constraint c WHERE c.connamespace IN (SELECT oid FROM pg_catalog.pg_namespace WHERE nspname IN ('public','neon_auth'))",
    "SELECT i.*,pg_catalog.pg_get_indexdef(i.indexrelid) AS definition FROM pg_catalog.pg_index i JOIN pg_catalog.pg_class c ON c.oid=i.indrelid WHERE c.relnamespace IN (SELECT oid FROM pg_catalog.pg_namespace WHERE nspname IN ('public','neon_auth'))",
    "SELECT t.*,pg_catalog.pg_get_triggerdef(t.oid) AS definition FROM pg_catalog.pg_trigger t JOIN pg_catalog.pg_class c ON c.oid=t.tgrelid WHERE c.relnamespace IN (SELECT oid FROM pg_catalog.pg_namespace WHERE nspname IN ('public','neon_auth'))",
    "SELECT p.*,pg_catalog.pg_get_functiondef(p.oid) AS definition FROM pg_catalog.pg_proc p WHERE p.pronamespace IN (SELECT oid FROM pg_catalog.pg_namespace WHERE nspname IN ('public','neon_auth')) AND p.prokind IN ('f','p')",
    "SELECT t.* FROM pg_catalog.pg_type t WHERE t.typnamespace IN (SELECT oid FROM pg_catalog.pg_namespace WHERE nspname IN ('public','neon_auth'))",
    "SELECT e.* FROM pg_catalog.pg_enum e JOIN pg_catalog.pg_type t ON t.oid=e.enumtypid WHERE t.typnamespace IN (SELECT oid FROM pg_catalog.pg_namespace WHERE nspname IN ('public','neon_auth'))",
    "SELECT r.*,pg_catalog.pg_get_ruledef(r.oid) AS definition FROM pg_catalog.pg_rewrite r JOIN pg_catalog.pg_class c ON c.oid=r.ev_class WHERE c.relnamespace IN (SELECT oid FROM pg_catalog.pg_namespace WHERE nspname IN ('public','neon_auth'))",
    "SELECT p.* FROM pg_catalog.pg_policy p JOIN pg_catalog.pg_class c ON c.oid=p.polrelid WHERE c.relnamespace IN (SELECT oid FROM pg_catalog.pg_namespace WHERE nspname IN ('public','neon_auth'))",
    "SELECT s.* FROM pg_catalog.pg_sequence s JOIN pg_catalog.pg_class c ON c.oid=s.seqrelid WHERE c.relnamespace IN (SELECT oid FROM pg_catalog.pg_namespace WHERE nspname IN ('public','neon_auth'))",
    "SELECT * FROM pg_catalog.pg_default_acl WHERE defaclnamespace=0 OR defaclnamespace IN (SELECT oid FROM pg_catalog.pg_namespace WHERE nspname IN ('public','neon_auth'))",
    "SELECT * FROM pg_catalog.pg_inherits WHERE inhrelid IN (SELECT c.oid FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname IN ('public','neon_auth'))",
    "SELECT * FROM pg_catalog.pg_extension WHERE extnamespace IN (SELECT oid FROM pg_catalog.pg_namespace WHERE nspname IN ('public','neon_auth'))",
    "SELECT * FROM pg_catalog.pg_collation WHERE collnamespace IN (SELECT oid FROM pg_catalog.pg_namespace WHERE nspname IN ('public','neon_auth'))",
    "SELECT * FROM pg_catalog.pg_conversion WHERE connamespace IN (SELECT oid FROM pg_catalog.pg_namespace WHERE nspname IN ('public','neon_auth'))",
    "SELECT * FROM pg_catalog.pg_operator WHERE oprnamespace IN (SELECT oid FROM pg_catalog.pg_namespace WHERE nspname IN ('public','neon_auth'))",
    "SELECT * FROM pg_catalog.pg_opclass WHERE opcnamespace IN (SELECT oid FROM pg_catalog.pg_namespace WHERE nspname IN ('public','neon_auth'))",
    "SELECT * FROM pg_catalog.pg_opfamily WHERE opfnamespace IN (SELECT oid FROM pg_catalog.pg_namespace WHERE nspname IN ('public','neon_auth'))",
    "SELECT a.* FROM pg_catalog.pg_aggregate a JOIN pg_catalog.pg_proc p ON p.oid=a.aggfnoid WHERE p.pronamespace IN (SELECT oid FROM pg_catalog.pg_namespace WHERE nspname IN ('public','neon_auth'))",
    "SELECT r.* FROM pg_catalog.pg_range r JOIN pg_catalog.pg_type t ON t.oid=r.rngtypid WHERE t.typnamespace IN (SELECT oid FROM pg_catalog.pg_namespace WHERE nspname IN ('public','neon_auth'))",
    "SELECT p.* FROM pg_catalog.pg_partitioned_table p JOIN pg_catalog.pg_class c ON c.oid=p.partrelid WHERE c.relnamespace IN (SELECT oid FROM pg_catalog.pg_namespace WHERE nspname IN ('public','neon_auth'))",
    "SELECT * FROM pg_catalog.pg_statistic_ext WHERE stxnamespace IN (SELECT oid FROM pg_catalog.pg_namespace WHERE nspname IN ('public','neon_auth'))",
)


def _catalogue(connection, deadline):
    parts = []
    for query in CATALOGUE_QUERIES:
        parts.append(_stream_digest(connection, "SELECT to_jsonb(item)::text FROM (" + query + ") item "
            "ORDER BY (to_jsonb(item)::text) COLLATE \"C\"", deadline))
    return sha(parts)


def _marker_present(connection, pin):
    namespaces = connection.execute(text("SELECT nspname FROM pg_catalog.pg_namespace "
        "WHERE left(nspname,19)='hirewiz_restore_qa_' ORDER BY nspname")).scalars().all()
    expected = "hirewiz_restore_qa_" + pin.nonce
    need(namespaces in ([], [expected]), "foreign_qa_marker_present")
    return namespaces == [expected]


def _capture(connection, pin, deadline, *, require_marker_absent=True):
    _remaining(deadline)
    _no_other_sessions(connection)
    identity = _identity(connection)
    need(connection.execute(text("SELECT nspname FROM pg_catalog.pg_namespace "
        "WHERE nspname IN ('public','neon_auth') ORDER BY nspname")).scalars().all() ==
        ["neon_auth", "public"], "protected_namespace_missing")
    schema27.verify_shape(connection, identity.public_oid)
    marker = _marker_present(connection, pin)
    need(not require_marker_absent or not marker, "qa_marker_still_present")
    relations = connection.execute(text("SELECT n.nspname,c.relname,c.relkind FROM pg_catalog.pg_class c "
        "JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname IN ('public','neon_auth') "
        "AND c.relkind IN ('r','p','m','S','f') ORDER BY n.nspname,c.relname")).all()
    need(1 <= len(relations) <= MAX_TABLES and all(kind != 'f' for _, _, kind in relations), "relation_inventory_invalid")
    tables, sequences = [], []
    for namespace, name, kind in relations:
        relation = _identifier(namespace) + "." + _identifier(name)
        if kind == "S":
            row = connection.execute(text("SELECT last_value,is_called FROM " + relation)).one()
            sequences.append(SequenceState(namespace, name, *row))
        else:
            query = "SELECT to_jsonb(item)::text FROM ONLY " + relation + " item ORDER BY (to_jsonb(item)::text) COLLATE \"C\""
            tables.append(TableDigest(namespace, name, *_stream_digest(connection, query, deadline)))
    need(FINANCIAL_TABLES <= {t.name for t in tables if t.schema == "public"}, "obligation_tables_missing")
    invariants = []
    for query in schema27.before.financial.INVARIANTS:
        _remaining(deadline)
        result = connection.execute(text(query)).scalar_one()
        need(type(result) is int and result == 0, "financial_invariant_failed")
        invariants.append((hashlib.sha256(query.encode()).hexdigest(), result))
    financial = sha({"tables": [asdict(t) for t in tables if t.schema == "public" and t.name in FINANCIAL_TABLES],
                     "invariants": invariants})
    catalogue = _catalogue(connection, deadline)
    _no_other_sessions(connection)
    point, stamp = connection.execute(text("SELECT pg_catalog.pg_current_wal_lsn()::text,clock_timestamp()")).one()
    result = CloneSnapshot(pin, identity, schema27.SCHEMA, point, stamp.astimezone(UTC).isoformat(),
                           catalogue, tuple(tables), tuple(sequences), financial, marker)
    result.validate()
    _remaining(deadline)
    return result


def capture(pin: ClonePin, *, deadline=None, require_marker_absent=True) -> CloneSnapshot:
    need(type(pin) is ClonePin and type(require_marker_absent) is bool, "capture_arguments_invalid")
    limit = _deadline(deadline)
    with _connection(pin, limit) as connection:
        return _capture(connection, pin, limit, require_marker_absent=require_marker_absent)


def install_marker(pin: ClonePin, checkpoint: CloneSnapshot, *, deadline=None) -> CloneMarker:
    """One QA DDL transaction; unknown commit is read-only reconciliation only."""
    need(type(checkpoint) is CloneSnapshot and type(pin) is ClonePin, "checkpoint_required")
    checkpoint.validate()
    need(checkpoint.pin == pin and not checkpoint.marker_present, "checkpoint_pin_changed")
    limit = _deadline(deadline)
    marker = CloneMarker(pin, checkpoint.digest, checkpoint.checkpoint_lsn)
    marker.validate()
    attempted = False
    try:
        with _connection(pin, limit, write=True) as connection:
            # Lock application relations before the second capture; no application writes occur.
            for table in checkpoint.tables:
                if table.schema in {"public", "neon_auth"}:
                    relation = _identifier(table.schema) + "." + _identifier(table.name)
                    connection.exec_driver_sql("LOCK TABLE " + relation + " IN SHARE MODE")
            current = _capture(connection, pin, limit)
            need(current.stable_digest == checkpoint.stable_digest, "checkpoint_changed")
            age = (_utc(current.checkpoint_utc) - _utc(checkpoint.checkpoint_utc)).total_seconds()
            need(0 <= age < MAX_CHECKPOINT_AGE and _lsn(checkpoint.checkpoint_lsn) <= _lsn(current.checkpoint_lsn),
                 "checkpoint_expired_or_future")
            attempted = True
            name = _identifier(marker.schema)
            connection.exec_driver_sql("CREATE SCHEMA " + name + " AUTHORIZATION CURRENT_USER")
            connection.exec_driver_sql("CREATE TABLE " + name + ".checkpoint_marker "
                "(nonce text NOT NULL,checkpoint_sha256 text NOT NULL,checkpoint_lsn pg_lsn NOT NULL)")
            connection.execute(text("INSERT INTO " + name + ".checkpoint_marker VALUES (:nonce,:sha,CAST(:lsn AS pg_lsn))"),
                {"nonce": pin.nonce, "sha": marker.checkpoint_sha256, "lsn": marker.checkpoint_lsn})
            _no_other_sessions(connection)
        with _connection(pin, limit) as connection:
            need(_verify_marker(connection, marker), "marker_missing_after_commit")
            committed = connection.execute(text("SELECT pg_current_wal_lsn()::text")).scalar_one()
        result = CloneMarker(pin, marker.checkpoint_sha256, marker.checkpoint_lsn, committed)
        result.validate()
        return result
    except Exception:
        if attempted:
            raise RestoreCloneDenied("schema27_restore_clone_marker_unknown_reconcile_only") from None
        raise


def _verify_marker(connection, marker):
    _no_other_sessions(connection)
    identity = _identity(connection)
    if not _marker_present(connection, marker.pin):
        return False
    owner = connection.execute(text("SELECT nspowner FROM pg_catalog.pg_namespace WHERE nspname=:name"),
                               {"name": marker.schema}).scalar_one()
    rows = connection.execute(text("SELECT c.relname,c.relkind,c.relowner,c.relhastriggers "
        "FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname=:name"),
        {"name": marker.schema}).all()
    need(owner == identity.actor_oid and [tuple(r) for r in rows] ==
         [("checkpoint_marker", "r", identity.actor_oid, False)], "marker_shape_changed")
    columns = connection.execute(text("SELECT a.attname,pg_catalog.format_type(a.atttypid,a.atttypmod),"
        "a.attnotnull,a.attidentity,a.attgenerated,d.adbin IS NOT NULL "
        "FROM pg_catalog.pg_attribute a JOIN pg_catalog.pg_class c ON c.oid=a.attrelid "
        "JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace "
        "LEFT JOIN pg_catalog.pg_attrdef d ON d.adrelid=a.attrelid AND d.adnum=a.attnum "
        "WHERE n.nspname=:name AND c.relname='checkpoint_marker' AND a.attnum>0 AND NOT a.attisdropped "
        "ORDER BY a.attnum"), {"name": marker.schema}).all()
    need([tuple(r) for r in columns] == [("nonce", "text", True, "", "", False),
        ("checkpoint_sha256", "text", True, "", "", False),
        ("checkpoint_lsn", "pg_lsn", True, "", "", False)], "marker_shape_changed")
    payload = connection.execute(text("SELECT nonce,checkpoint_sha256,checkpoint_lsn::text FROM " +
        _identifier(marker.schema) + ".checkpoint_marker LIMIT 2")).all()
    need([tuple(r) for r in payload] == [(marker.pin.nonce, marker.checkpoint_sha256, marker.checkpoint_lsn)], "marker_payload_changed")
    return True


def verify_marker(pin: ClonePin, marker: CloneMarker, *, deadline=None) -> bool:
    need(type(marker) is CloneMarker and type(pin) is ClonePin, "marker_required")
    marker.validate()
    need(marker.pin == pin, "marker_pin_changed")
    limit = _deadline(deadline)
    with _connection(pin, limit) as connection:
        return _verify_marker(connection, marker)


def compare_restore(checkpoint: CloneSnapshot, restored: CloneSnapshot, marker: CloneMarker) -> dict:
    need(type(checkpoint) is CloneSnapshot and type(restored) is CloneSnapshot and type(marker) is CloneMarker,
         "typed_compare_required")
    checkpoint.validate()
    restored.validate()
    marker.validate()
    need(checkpoint.pin == restored.pin == marker.pin and marker.checkpoint_sha256 == checkpoint.digest
         and marker.checkpoint_lsn == checkpoint.checkpoint_lsn, "restore_custody_changed")
    need(not checkpoint.marker_present and not restored.marker_present, "qa_marker_still_present")
    need(checkpoint.stable_digest == restored.stable_digest, "restored_state_changed")
    need(_utc(restored.checkpoint_utc) >= _utc(checkpoint.checkpoint_utc), "restore_observation_time_invalid")
    return {"kind": "hirewiz_schema27_qa_clone_restore_comparison_v1", "matched": True,
        "checkpoint_sha256": checkpoint.digest, "restored_sha256": restored.digest,
        "stable_sha256": checkpoint.stable_digest, "qa_marker_absent": True,
        "public_tables": sum(t.schema == "public" for t in restored.tables),
        "neon_auth_tables": sum(t.schema == "neon_auth" for t in restored.tables),
        "sequence_count": len(restored.sequences), "financial_sha256": restored.financial_sha256,
        "production_target_modified": False, "provider_restore_proven": False,
        "external_authority_replay_proven": False, "full_R03_qualified": False}
