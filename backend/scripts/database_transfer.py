"""Whole-database PostgreSQL17 transfer; no provider acquisition or release authority.

This ordinary library is called with separately observed source/destination native
identities and transient credentials. No JSON/CLI receipt or Boolean grants final
writer-cut authority. Rehearsal is usable independently; a final transfer requires
the release owner's authenticated native cut verifier before/after every phase.
No admission, credential rotation, schema upgrade or traffic promotion occurs here.
"""

from __future__ import annotations

import hashlib
import os
import re
import resource
import signal
import sqlite3
import stat
import struct
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal, Protocol

import psycopg
from psycopg import sql

SCHEMA9 = "20261008_0009"
COPY_HEADER = b"PGCOPY\n\xff\r\n\x00"
LOCAL_DATABASE = re.compile(r"hw_transfer_[a-f0-9]{32}_(source|target)")
SESSION_OPTIONS = (
    "-c statement_timeout=300000 -c lock_timeout=4000 "
    "-c idle_in_transaction_session_timeout=1810000 -c search_path=pg_catalog"
)


class TransferDenied(RuntimeError):
    """Finite nonsecret phase; never expose libpq/vendor exception bodies."""


@dataclass(frozen=True, slots=True)
class NativeDatabaseIdentity:
    # These expectations are supplied by the reviewed native identity adapter,
    # not acquired by this library or inferred from the credential's URL.
    host: str
    port: int
    database: str
    cluster_sha256: str
    database_oid: int
    actor: str
    actor_oid: int
    root_certificate: str
    transport: Literal["verify-full", "disposable-local"] = "verify-full"

    def validate(self) -> None:
        if (
            type(self.host) is not str or not re.fullmatch(r"[a-z0-9.-]{1,253}", self.host)
            or type(self.port) is not int or not 0 < self.port < 65536
            or not re.fullmatch(r"[a-zA-Z_][a-zA-Z0-9_]{0,62}", self.database)
            or not re.fullmatch(r"[a-f0-9]{64}", self.cluster_sha256)
            or type(self.database_oid) is not int or not 0 < self.database_oid < 2**32
            or type(self.actor_oid) is not int or not 0 < self.actor_oid < 2**32
            or not re.fullmatch(r"[a-zA-Z_][a-zA-Z0-9_]{0,62}", self.actor)
        ):
            raise TransferDenied("identity_shape_refused")
        if self.transport == "disposable-local":
            if self.host != "127.0.0.1" or self.port not in {55433, 55435} or not LOCAL_DATABASE.fullmatch(self.database):
                raise TransferDenied("disposable_scope_refused")
        elif self.transport != "verify-full" or not Path(self.root_certificate).is_absolute():
            raise TransferDenied("transport_shape_refused")


@dataclass(frozen=True, slots=True, repr=False)
class NativeRestoreRole:
    name: str
    oid: int

    def validate(self) -> None:
        if not re.fullmatch(r"[a-zA-Z_][a-zA-Z0-9_]{0,62}", self.name) or type(self.oid) is not int or not 0 < self.oid < 2**32:
            raise TransferDenied("restore_role_identity_refused")


@dataclass(frozen=True, slots=True, repr=False)
class BoundDatabase:
    identity: NativeDatabaseIdentity
    password: str = field(repr=False)
    restore_role: NativeRestoreRole | None = None

    def environment(self, *, readonly: bool) -> dict[str, str]:
        self.identity.validate()
        if type(self.password) is not str or not 1 <= len(self.password) <= 4096 or "\x00" in self.password:
            raise TransferDenied("transient_credential_shape_refused")
        identity = self.identity
        if any(key.startswith("PG") for key in os.environ):
            raise TransferDenied("ambient_libpq_refused")
        options = "-c default_transaction_read_only=" + ("on " if readonly else "off ") + SESSION_OPTIONS
        result = {
            "PGHOST": identity.host, "PGPORT": str(identity.port), "PGDATABASE": identity.database,
            "PGUSER": identity.actor, "PGPASSWORD": self.password, "PGOPTIONS": options,
            "PGCONNECT_TIMEOUT": "5", "PGAPPNAME": "hirewiz_owned_database_transfer",
            "PGSSLMODE": "verify-full" if identity.transport == "verify-full" else "disable",
            "PGSSLCERTMODE": "disable", "PGCHANNELBINDING": "prefer",
        }
        if identity.transport == "verify-full":
            result["PGSSLROOTCERT"] = identity.root_certificate
        return result

    def connect(self) -> psycopg.Connection[Any]:
        env = self.environment(readonly=True)
        mapping = {
            "PGHOST": "host", "PGPORT": "port", "PGDATABASE": "dbname", "PGUSER": "user",
            "PGPASSWORD": "password", "PGOPTIONS": "options", "PGCONNECT_TIMEOUT": "connect_timeout",
            "PGAPPNAME": "application_name", "PGSSLMODE": "sslmode", "PGSSLCERTMODE": "sslcertmode",
            "PGCHANNELBINDING": "channel_binding", "PGSSLROOTCERT": "sslrootcert",
        }
        try:
            connection = psycopg.connect(autocommit=True, **{mapping[key]: value for key, value in env.items()})
        except Exception:
            raise TransferDenied("native_connection_unavailable") from None
        try:
            info = connection.info
            effective = info.get_parameters()
            if (
                info.host != self.identity.host or info.port != self.identity.port
                or info.dbname != self.identity.database or info.user != self.identity.actor
                or effective.get("options") != env["PGOPTIONS"]
                or effective.get("sslmode") != env["PGSSLMODE"]
                or effective.get("service") or "hostaddr" in env
                or self.identity.transport == "verify-full" and (
                    not connection.pgconn.ssl_in_use
                    or effective.get("sslrootcert") != self.identity.root_certificate
                )
            ):
                raise TransferDenied("effective_transport_refused")
            verify_identity(connection, self.identity)
            if self.restore_role is not None:
                self.restore_role.validate()
                connection.execute(sql.SQL("SET ROLE {}").format(sql.Identifier(self.restore_role.name)))
                verify_effective_role(connection, self.identity, self.restore_role)
            connection.autocommit = False
            return connection
        except Exception:
            connection.close()
            raise TransferDenied("native_connection_refused") from None


@dataclass(frozen=True, slots=True)
class Limits:
    archive_bytes: int = 512 * 1024 * 1024
    content_bytes: int = 512 * 1024 * 1024
    row_bytes: int = 16 * 1024 * 1024
    rows: int = 1_000_000
    objects: int = 256
    total_seconds: int = 1800
    command_seconds: int = 600

    def validate(self) -> None:
        maximum = Limits()
        if any(type(getattr(self, name)) is not int or not 0 < getattr(self, name) <= getattr(maximum, name)
               for name in self.__dataclass_fields__):
            raise TransferDenied("transfer_limits_refused")


@dataclass(slots=True)
class _ContentBudget:
    bytes_left: int
    rows_left: int

    def charge(self, size: int, rows: int = 0) -> None:
        self.bytes_left -= size
        self.rows_left -= rows
        if self.bytes_left < 0 or self.rows_left < 0:
            raise TransferDenied("aggregate_content_bound")


@dataclass(frozen=True, slots=True)
class PostgresTools:
    dump: Path
    restore: Path
    dump_sha256: str
    restore_sha256: str

    def verify(self) -> None:
        for path, expected in ((self.dump, self.dump_sha256), (self.restore, self.restore_sha256)):
            if not path.is_absolute() or path.is_symlink() or not path.is_file() or not re.fullmatch(r"[a-f0-9]{64}", expected):
                raise TransferDenied("postgres_tools_refused")
            with path.open("rb") as handle:
                if hashlib.file_digest(handle, "sha256").hexdigest() != expected:
                    raise TransferDenied("postgres_tools_changed")


@dataclass(frozen=True, slots=True)
class NativeCutObservation:
    observed_at: datetime
    retired_since: datetime


class NativeCutBoundary(Protocol):
    def verify_native_cut(
        self, source: NativeDatabaseIdentity, target: NativeDatabaseIdentity,
    ) -> NativeCutObservation:
        """Authenticated native release verifier; not a file/caller assertion."""
        ...


def verify_identity(connection: psycopg.Connection[Any], identity: NativeDatabaseIdentity) -> None:
    control = connection.execute(
        "SELECT p.oid,p.proowner,p.prosecdef,p.prosrc,l.lanname FROM pg_catalog.pg_proc p "
        "JOIN pg_catalog.pg_namespace n ON n.oid=p.pronamespace "
        "JOIN pg_catalog.pg_language l ON l.oid=p.prolang "
        "WHERE n.nspname='pg_catalog' AND p.proname='pg_control_system' AND p.pronargs=0 LIMIT 2"
    ).fetchall()
    if control != [(3441, 10, False, "pg_control_system", "internal")]:
        raise TransferDenied("native_control_function_mismatch")
    row = connection.execute(
        "SELECT d.oid,r.oid,current_user,session_user,"
        "(SELECT system_identifier::text FROM pg_catalog.pg_control_system()),"
        "current_setting('server_version_num')::int "
        "FROM pg_catalog.pg_database d JOIN pg_catalog.pg_roles r ON r.rolname=current_user "
        "WHERE d.datname=pg_catalog.current_database()"
    ).fetchone()
    if (
        row is None or tuple(row[:4]) != (identity.database_oid, identity.actor_oid, identity.actor, identity.actor)
        or hashlib.sha256(row[4].encode()).hexdigest() != identity.cluster_sha256
        or not 170000 <= row[5] < 180000
    ):
        raise TransferDenied("native_identity_mismatch")


def verify_effective_role(
    connection: psycopg.Connection[Any], identity: NativeDatabaseIdentity, role: NativeRestoreRole,
) -> None:
    # The same genuine connection has already authenticated/verified bootstrap
    # transport, control function and cluster. SET ROLE cannot change database.
    # A non-elevated migrator must not need restricted control-system EXECUTE.
    row = connection.execute(
        "SELECT d.oid,r.oid,current_user,session_user,current_setting('server_version_num')::int "
        "FROM pg_catalog.pg_database d JOIN pg_catalog.pg_roles r ON r.rolname=current_user "
        "WHERE d.datname=pg_catalog.current_database()"
    ).fetchone()
    if row is None or tuple(row[:4]) != (identity.database_oid, role.oid, role.name, identity.actor) or not 170000 <= row[4] < 180000:
        raise TransferDenied("effective_role_session_mismatch")


def _one(connection: psycopg.Connection[Any], query: Any, parameters: Any = None) -> tuple[Any, ...]:
    row = connection.execute(query, parameters).fetchone()
    if type(row) is not tuple:
        raise TransferDenied("native_catalog_row_unavailable")
    return row


def _deadline(end: float) -> None:
    if time.monotonic() >= end:
        raise TransferDenied("transfer_deadline")


def _cut(
    boundary: NativeCutBoundary | None, source: BoundDatabase, target: BoundDatabase,
    stable: datetime | None,
) -> datetime | None:
    if boundary is None:
        return None
    observed = boundary.verify_native_cut(source.identity, target.identity)
    now = datetime.now(UTC)
    if (
        type(observed) is not NativeCutObservation
        or observed.observed_at.tzinfo is None or observed.retired_since.tzinfo is None
        or not now - timedelta(seconds=30) <= observed.observed_at <= now
        or observed.retired_since > observed.observed_at
        or stable is not None and observed.retired_since != stable
    ):
        raise TransferDenied("native_writer_cut_changed_or_stale")
    return observed.retired_since


def _command(args: list[str], env: dict[str, str], end: float, limits: Limits, *, output: Path | None = None) -> None:
    _deadline(end)
    timeout = min(limits.command_seconds, end - time.monotonic())
    # Absolute reviewed binaries, no shell, ambient PG/service/hostaddr/options,
    # credential argv, stdout/vendor error body, operator retry or parallel jobs.
    safe = {key: os.environ[key] for key in ("LANG", "LC_ALL", "TZ") if key in os.environ}
    safe.update(env)
    stream = output.open("xb") if output else subprocess.DEVNULL
    process: subprocess.Popen[bytes] | None = None
    def bound_output() -> None:
        resource.setrlimit(resource.RLIMIT_FSIZE, (limits.archive_bytes, limits.archive_bytes))
    try:
        process = subprocess.Popen(args, env=safe, stdin=subprocess.DEVNULL, stdout=stream,
                                   stderr=subprocess.DEVNULL, start_new_session=True,
                                   preexec_fn=bound_output)
        process.wait(timeout=timeout)
        if process.returncode != 0:
            raise TransferDenied("postgres_command_failed_reconcile_before_retry")
        _deadline(end)
    except subprocess.TimeoutExpired:
        raise TransferDenied("postgres_command_timeout_reconcile_before_retry") from None
    finally:
        if process is not None and process.poll() is None:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            finally:
                process.wait(timeout=2)
        if output:
            stream.close()  # type: ignore[union-attr]


def _catalog(connection: psycopg.Connection[Any], limits: Limits) -> dict[str, Any]:
    namespace = "n.nspname <> 'information_schema' AND n.nspname !~ '^pg_'"
    schemas = connection.execute(
        f"SELECT nspname FROM pg_namespace WHERE nspname <> 'information_schema' AND nspname !~ '^pg_' ORDER BY 1 LIMIT {limits.objects + 1}"
    ).fetchall()
    relations = connection.execute(
        "SELECT n.nspname,c.relname,c.relkind,c.relpersistence,c.relrowsecurity,c.relforcerowsecurity,pg_get_partkeydef(c.oid) "
        f"FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE {namespace} AND c.relkind IN ('r','p','m','v','f') ORDER BY 1,2 LIMIT {limits.objects + 1}"
    ).fetchall()
    if len(relations) > limits.objects or len(schemas) > limits.objects or any(row[2] == "f" for row in relations):
        raise TransferDenied("catalog_bound_or_foreign_table_refused")
    columns = connection.execute(
        "SELECT n.nspname,c.relname,a.attname,pg_catalog.format_type(a.atttypid,a.atttypmod),"
        "a.attnotnull,a.attidentity,a.attgenerated,pg_get_expr(d.adbin,d.adrelid),"
        "cn.nspname,co.collname,a.atttypid "
        "FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace JOIN pg_attribute a ON a.attrelid=c.oid "
        "LEFT JOIN pg_attrdef d ON d.adrelid=c.oid AND d.adnum=a.attnum "
        "LEFT JOIN pg_collation co ON co.oid=a.attcollation LEFT JOIN pg_namespace cn ON cn.oid=co.collnamespace "
        f"WHERE {namespace} AND c.relkind IN ('r','p','m','v') AND a.attnum>0 AND NOT a.attisdropped ORDER BY 1,2,a.attnum LIMIT 65537"
    ).fetchall()
    if len(columns) > 65536 or any(row[-1] >= 16384 for row in columns):
        # Custom array wire encodings can embed cluster-local type OIDs. Refuse
        # rather than silently compare lossy text or declare unequal data equal.
        raise TransferDenied("nonportable_binary_column_requires_review")
    if _one(connection,
        "SELECT count(*) FROM pg_type t JOIN pg_namespace n ON n.oid=t.typnamespace "
        "LEFT JOIN pg_class c ON c.oid=t.typrelid "
        f"WHERE {namespace} AND ((t.typrelid=0 AND t.typelem=0) OR c.relkind='c')",
    )[0]:
        raise TransferDenied("standalone_type_requires_review")
    if _one(connection,
        "SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace "
        f"WHERE {namespace}"
    )[0] or _one(connection, "SELECT count(*) FROM pg_event_trigger")[0] or _one(connection, "SELECT count(*) FROM pg_foreign_server")[0]:
        raise TransferDenied("user_routine_or_event_trigger_requires_review")
    constraints = connection.execute(
        "SELECT n.nspname,c.relname,k.conname,pg_get_constraintdef(k.oid),k.convalidated "
        "FROM pg_constraint k JOIN pg_class c ON c.oid=k.conrelid JOIN pg_namespace n ON n.oid=c.relnamespace "
        f"WHERE {namespace} ORDER BY 1,2,3 LIMIT 8193"
    ).fetchall()
    indexes = connection.execute(
        "SELECT n.nspname,c.relname,pg_get_indexdef(i.indexrelid),i.indisvalid,i.indisready "
        "FROM pg_index i JOIN pg_class c ON c.oid=i.indrelid JOIN pg_namespace n ON n.oid=c.relnamespace "
        f"WHERE {namespace} ORDER BY 1,2,3 LIMIT 8193"
    ).fetchall()
    views = connection.execute(
        "SELECT n.nspname,c.relname,pg_get_viewdef(c.oid),c.relispopulated FROM pg_class c "
        f"JOIN pg_namespace n ON n.oid=c.relnamespace WHERE {namespace} AND c.relkind IN ('v','m') ORDER BY 1,2 LIMIT {limits.objects + 1}"
    ).fetchall()
    inheritance = connection.execute(
        "SELECT n.nspname,c.relname,pn.nspname,p.relname,i.inhseqno FROM pg_inherits i "
        "JOIN pg_class c ON c.oid=i.inhrelid JOIN pg_namespace n ON n.oid=c.relnamespace "
        "JOIN pg_class p ON p.oid=i.inhparent JOIN pg_namespace pn ON pn.oid=p.relnamespace "
        f"WHERE {namespace} ORDER BY 1,2,5 LIMIT 8193"
    ).fetchall()
    triggers = connection.execute(
        "SELECT n.nspname,c.relname,t.tgname,pg_get_triggerdef(t.oid),t.tgenabled FROM pg_trigger t "
        "JOIN pg_class c ON c.oid=t.tgrelid JOIN pg_namespace n ON n.oid=c.relnamespace "
        f"WHERE {namespace} AND NOT t.tgisinternal ORDER BY 1,2,3 LIMIT 8193"
    ).fetchall()
    if any(len(rows) > 8192 for rows in (constraints, indexes, inheritance, triggers)):
        raise TransferDenied("catalog_definition_bound")
    extensions = connection.execute("SELECT extname,extversion FROM pg_extension ORDER BY 1").fetchall()
    if extensions != [("plpgsql", "1.0")]:
        raise TransferDenied("extension_requires_review")
    settings, collations, index_collations = _collations(connection, limits)
    return dict(schemas=schemas, relations=relations, columns=[row[:-1] for row in columns],
                constraints=constraints, indexes=indexes, views=views, inheritance=inheritance,
                triggers=triggers, extensions=extensions, settings=settings,
                collations=collations, index_collations=index_collations)


def _check_collation_versions(settings: tuple[Any, ...], collations: list[tuple[Any, ...]]) -> None:
    if len(settings) != 8 or settings[1] not in {"b", "c", "i"} or settings[6] != settings[7]:
        raise TransferDenied("database_collation_version_stale_or_untracked")
    for row in collations:
        if len(row) != 11 or row[3] not in {"b", "c", "i", "d"}:
            raise TransferDenied("collation_metadata_refused")
        # Database-default collation delegates its effective version to the
        # separately checked database row, not its NULL object collversion.
        if row[3] != "d" and row[9] != row[10]:
            raise TransferDenied("referenced_collation_version_stale_or_untracked")


def _collations(connection: psycopg.Connection[Any], limits: Limits) -> tuple[tuple[Any, ...], list[tuple[Any, ...]], list[tuple[Any, ...]]]:
    settings = _one(connection,
        "SELECT pg_encoding_to_char(encoding),datlocprovider,datcollate,datctype,datlocale,"
        "daticurules,datcollversion,pg_database_collation_actual_version(oid) "
        "FROM pg_database WHERE datname=current_database()",
    )
    rows = connection.execute(
        "SELECT n.nspname,co.collname,co.collencoding,co.collprovider,co.collisdeterministic,"
        "co.collcollate,co.collctype,co.colllocale,co.collicurules,co.collversion,"
        "CASE WHEN co.collprovider='d' THEN pg_database_collation_actual_version("
        "(SELECT oid FROM pg_database WHERE datname=current_database())) "
        "ELSE pg_collation_actual_version(co.oid) END "
        "FROM pg_collation co JOIN pg_namespace n ON n.oid=co.collnamespace WHERE co.oid IN ("
        "SELECT a.attcollation FROM pg_attribute a JOIN pg_class c ON c.oid=a.attrelid "
        "JOIN pg_namespace ns ON ns.oid=c.relnamespace "
        "WHERE ns.nspname<>'information_schema' AND ns.nspname !~ '^pg_' AND a.attnum>0 AND NOT a.attisdropped "
        "UNION SELECT unnest(i.indcollation::oid[]) FROM pg_index i JOIN pg_class c ON c.oid=i.indrelid "
        "JOIN pg_namespace ns ON ns.oid=c.relnamespace WHERE ns.nspname<>'information_schema' AND ns.nspname !~ '^pg_' "
        "UNION SELECT cc.oid FROM pg_collation cc JOIN pg_namespace ns ON ns.oid=cc.collnamespace "
        "WHERE ns.nspname<>'information_schema' AND ns.nspname !~ '^pg_') "
        f"ORDER BY 1,2,3 LIMIT {limits.objects + 1}"
    ).fetchall()
    index_collations = connection.execute(
        "SELECT n.nspname,c.relname,ic.relname,key.ordinality,cn.nspname,co.collname,co.collencoding "
        "FROM pg_index i JOIN pg_class c ON c.oid=i.indrelid JOIN pg_namespace n ON n.oid=c.relnamespace "
        "JOIN pg_class ic ON ic.oid=i.indexrelid "
        "CROSS JOIN LATERAL unnest(i.indcollation::oid[]) WITH ORDINALITY AS key(collation_oid,ordinality) "
        "LEFT JOIN pg_collation co ON co.oid=key.collation_oid LEFT JOIN pg_namespace cn ON cn.oid=co.collnamespace "
        "WHERE n.nspname<>'information_schema' AND n.nspname !~ '^pg_' ORDER BY 1,2,3,4 LIMIT 8193"
    ).fetchall()
    if len(rows) > limits.objects or len(index_collations) > 8192:
        raise TransferDenied("referenced_collation_bound")
    _check_collation_versions(settings, rows)
    return settings, rows, index_collations


def _matching_catalog(source: dict[str, Any], target: dict[str, Any]) -> None:
    if any(source[key] != target[key] for key in ("settings", "collations", "index_collations")):
        raise TransferDenied("restored_collation_mismatch")
    if source != target:
        raise TransferDenied("restored_catalog_mismatch")


def _sequences(connection: psycopg.Connection[Any], limits: Limits) -> list[tuple[Any, ...]]:
    rows = connection.execute(
        "SELECT n.nspname,c.relname,s.seqtypid,s.seqstart,s.seqincrement,s.seqmax,s.seqmin,s.seqcache,s.seqcycle "
        "FROM pg_sequence s JOIN pg_class c ON c.oid=s.seqrelid JOIN pg_namespace n ON n.oid=c.relnamespace "
        f"WHERE n.nspname <> 'information_schema' AND n.nspname !~ '^pg_' ORDER BY 1,2 LIMIT {limits.objects + 1}"
    ).fetchall()
    if len(rows) > limits.objects:
        raise TransferDenied("sequence_bound")
    result = []
    for row in rows:
        state = _one(connection, sql.SQL("SELECT last_value,is_called FROM {}.{}").format(
            sql.Identifier(row[0]), sql.Identifier(row[1]),
        ))
        result.append((*row, *state))
    return result


def _empty(connection: psycopg.Connection[Any]) -> None:
    counts = connection.execute(
        "SELECT (SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
        "WHERE n.nspname <> 'information_schema' AND n.nspname !~ '^pg_'),"
        "(SELECT count(*) FROM pg_largeobject_metadata),"
        "(SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace "
        "WHERE n.nspname <> 'information_schema' AND n.nspname !~ '^pg_'),"
        "(SELECT count(*) FROM pg_prepared_xacts WHERE database=current_database()),"
        "(SELECT count(*) FROM pg_type t JOIN pg_namespace n ON n.oid=t.typnamespace "
        "WHERE n.nspname<>'information_schema' AND n.nspname !~ '^pg_'),"
        "(SELECT count(*) FROM pg_collation c JOIN pg_namespace n ON n.oid=c.collnamespace "
        "WHERE n.nspname<>'information_schema' AND n.nspname !~ '^pg_')"
    ).fetchone()
    schemas = connection.execute(
        "SELECT nspname FROM pg_namespace WHERE nspname <> 'information_schema' AND nspname !~ '^pg_' ORDER BY 1"
    ).fetchall()
    if counts != (0, 0, 0, 0, 0, 0) or schemas != [("public",)]:
        raise TransferDenied("destination_not_empty")


def _rows(chunks: Any, limits: Limits, end: float, budget: _ContentBudget | None = None):
    """Parse COPY binary rows without relying on libpq's chunk boundaries."""
    buffer = bytearray()
    header = False
    trailer = False
    total = 0
    for chunk in chunks:
        _deadline(end)
        if trailer and chunk:
            raise TransferDenied("copy_after_trailer")
        total += len(chunk)
        if budget is not None:
            budget.charge(len(chunk))
        if total > limits.content_bytes:
            raise TransferDenied("content_byte_bound")
        buffer.extend(chunk)
        if not header:
            if len(buffer) < 19:
                continue
            if bytes(buffer[:11]) != COPY_HEADER or struct.unpack("!II", buffer[11:19]) != (0, 0):
                raise TransferDenied("copy_header_refused")
            del buffer[:19]
            header = True
        while len(buffer) >= 2:
            count = struct.unpack("!h", buffer[:2])[0]
            if count == -1:
                del buffer[:2]
                trailer = True
                if buffer:
                    raise TransferDenied("copy_after_trailer")
                break
            if not 0 <= count <= 2048:
                raise TransferDenied("copy_column_bound")
            offset = 2
            complete = True
            for _ in range(count):
                if len(buffer) < offset + 4:
                    complete = False
                    break
                size = struct.unpack("!i", buffer[offset:offset + 4])[0]
                if size < -1 or size > limits.row_bytes:
                    raise TransferDenied("copy_field_bound")
                offset += 4 + max(size, 0)
                if offset > limits.row_bytes:
                    raise TransferDenied("copy_row_bound")
                if len(buffer) < offset:
                    complete = False
                    break
            if not complete:
                break
            if budget is not None:
                budget.charge(0, 1)
            yield bytes(buffer[:offset])
            del buffer[:offset]
        if len(buffer) > limits.row_bytes:
            raise TransferDenied("copy_row_bound")
    if not header or not trailer or buffer:
        raise TransferDenied("copy_incomplete")


def compare_content(
    source: psycopg.Connection[Any], target: psycopg.Connection[Any], directory: Path,
    limits: Limits, end: float,
) -> dict[str, int]:
    source_catalog = _catalog(source, limits)
    _matching_catalog(source_catalog, _catalog(target, limits))
    if _sequences(source, limits) != _sequences(target, limits):
        raise TransferDenied("restored_sequence_mismatch")
    counts: dict[str, int] = {}
    budget = _ContentBudget(limits.content_bytes * 2, limits.rows * 2)
    # Store exact binary row frames in a temporary private SQLite multiset.
    # Preserve duplicate/no-PK rows, NULLs, exact integers/numerics, bytea and
    # JSON/Unicode without a hash collision or stringification shortcut.
    with sqlite3.connect(directory / "rows.sqlite") as multiset:
        multiset.execute("PRAGMA journal_mode=OFF")
        multiset.execute("CREATE TABLE rows (value BLOB PRIMARY KEY,n INTEGER NOT NULL) WITHOUT ROWID")
        for schema, table, kind, *_ in source_catalog["relations"]:
            if kind not in {"r", "m"}:
                continue  # Partition leaves are relkind=r; views have no data.
            query = sql.SQL("COPY (SELECT * FROM ONLY {}.{}) TO STDOUT (FORMAT BINARY)").format(
                sql.Identifier(schema), sql.Identifier(table),
            )
            count = 0
            with source.cursor().copy(query) as stream:
                for row in _rows(stream, limits, end, budget):
                    count += 1
                    if count > limits.rows:
                        raise TransferDenied("content_row_bound")
                    multiset.execute("INSERT INTO rows VALUES (?,1) ON CONFLICT(value) DO UPDATE SET n=n+1", (row,))
            with target.cursor().copy(query) as stream:
                for row in _rows(stream, limits, end, budget):
                    changed = multiset.execute("UPDATE rows SET n=n-1 WHERE value=? AND n>0", (row,)).rowcount
                    if changed != 1:
                        raise TransferDenied("restored_row_mismatch")
            if multiset.execute("SELECT count(*) FROM rows WHERE n<>0").fetchone()[0]:
                raise TransferDenied("restored_row_mismatch")
            counts[f"{schema}.{table}"] = count
            multiset.execute("DELETE FROM rows")
        source_oids = source.execute(f"SELECT oid FROM pg_largeobject_metadata ORDER BY oid LIMIT {limits.objects + 1}").fetchall()
        if len(source_oids) > limits.objects or source_oids != target.execute(f"SELECT oid FROM pg_largeobject_metadata ORDER BY oid LIMIT {limits.objects + 1}").fetchall():
            raise TransferDenied("restored_large_object_identity_mismatch")
        for (oid,) in source_oids:
            left = _one(source, "SELECT lo_get(%s,0,%s)", (oid, limits.row_bytes + 1))[0]
            right = _one(target, "SELECT lo_get(%s,0,%s)", (oid, limits.row_bytes + 1))[0]
            budget.charge(len(left) + len(right))
            if len(left) > limits.row_bytes or left != right:
                raise TransferDenied("restored_large_object_mismatch")
    return counts


def _transfer(
    source: BoundDatabase, target: BoundDatabase, tools: PostgresTools, workdir: Path, *,
    purpose: Literal["rehearsal", "final"] = "rehearsal", limits: Limits = Limits(),
    native_cut: NativeCutBoundary | None = None,
) -> dict[str, Any]:
    """Perform one complete transfer; unknown/failure never triggers restore replay.

    workdir must be a fresh caller-owned 0700 directory. Keep its started marker
    on success/failure. A new attempt requires separately reconciled empty target
    and explicit operator review; this function never truncates/drops/reset tables.
    """
    source.identity.validate()
    target.identity.validate()
    limits.validate()
    if (source.identity.cluster_sha256, source.identity.database_oid) == (target.identity.cluster_sha256, target.identity.database_oid):
        raise TransferDenied("source_destination_alias")
    if source.restore_role is not None:
        raise TransferDenied("source_effective_role_override_refused")
    if purpose not in {"rehearsal", "final"} or purpose == "final" and (
        native_cut is None or source.identity.transport != "verify-full" or target.identity.transport != "verify-full"
    ):
        raise TransferDenied("final_native_cut_required")
    metadata = workdir.lstat()
    if not stat.S_ISDIR(metadata.st_mode) or metadata.st_uid != os.getuid() or stat.S_IMODE(metadata.st_mode) != 0o700:
        raise TransferDenied("private_workdir_refused")
    if (workdir / "transfer.started").exists() or (workdir / "transfer.started").is_symlink():
        raise TransferDenied("attempt_already_started_reconcile_before_retry")
    tools.verify()
    end = time.monotonic() + limits.total_seconds
    stable = _cut(native_cut, source, target, None)
    with source.connect() as exporter, target.connect() as destination:
        exporter.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        destination.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        _empty(destination)
        destination.commit()
        versions = exporter.execute("SELECT version_num FROM public.alembic_version LIMIT 3").fetchall()
        if versions != [(SCHEMA9,)]:
            raise TransferDenied("source_schema9_required")
        source_catalog = _catalog(exporter, limits)
        destination_settings, _unused_collations, _unused_indexes = _collations(destination, limits)
        if source_catalog["settings"] != destination_settings:
            raise TransferDenied("destination_database_collation_mismatch")
        destination.commit()
        sequences_before = _sequences(exporter, limits)
        snapshot = _one(exporter, "SELECT pg_export_snapshot()")[0]
        if type(snapshot) is not str or not re.fullmatch(r"[a-fA-F0-9-]{1,96}", snapshot):
            raise TransferDenied("snapshot_identity_refused")
        stable = _cut(native_cut, source, target, stable)
        try:
            descriptor = os.open(workdir / "transfer.started", os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        except FileExistsError:
            raise TransferDenied("attempt_already_started_reconcile_before_retry") from None
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        parent_descriptor = os.open(workdir, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(parent_descriptor)
        finally:
            os.close(parent_descriptor)
        with tempfile.TemporaryDirectory(prefix="transfer-", dir=workdir) as temporary:
            private = Path(temporary)
            archive = private / "database.dump"
            _command([str(tools.dump), "--format=custom", "--no-password", "--no-privileges",
                      "--lock-wait-timeout=4000ms", "--snapshot", snapshot, "--file", str(archive)],
                     source.environment(readonly=True), end, limits)
            archive.chmod(0o600)
            if not 100 <= archive.stat().st_size <= limits.archive_bytes:
                raise TransferDenied("archive_byte_bound")
            if _sequences(exporter, limits) != sequences_before:
                raise TransferDenied("source_sequence_changed_during_export")
            stable = _cut(native_cut, source, target, stable)
            listing = private / "toc.txt"
            _command([str(tools.restore), "--list", str(archive)], {}, end, limits, output=listing)
            if listing.stat().st_size > 1024 * 1024:
                raise TransferDenied("archive_catalog_bound")
            # Existing empty public is preserved; all other TOC entries remain.
            # Never --clean/--create or drop/overwrite an existing schema/table.
            filtered = private / "restore.txt"
            lines = listing.read_text().splitlines(keepends=True)
            public = re.compile(r"^[0-9]+; [0-9]+ [0-9]+ SCHEMA - public [^\r\n]+\n?$")
            matches = sum(public.fullmatch(line) is not None for line in lines)
            if matches > 1:
                raise TransferDenied("archive_public_schema_ambiguous")
            filtered.write_text("".join(line for line in lines if public.fullmatch(line) is None))
            with target.connect() as fresh:
                _empty(fresh)
            tools.verify()
            stable = _cut(native_cut, source, target, stable)
            role_args = ["--role", target.restore_role.name] if target.restore_role is not None else []
            _command([str(tools.restore), "--single-transaction", "--exit-on-error", "--no-password",
                      "--no-owner", "--no-acl", "--use-list", str(filtered), "--dbname", target.identity.database,
                      *role_args, str(archive)], target.environment(readonly=False), end, limits)
            stable = _cut(native_cut, source, target, stable)
            # New transaction after the independent restore commit.
            destination.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
            # Renew genuine bootstrap cluster proof on a new authenticated
            # connection before SET; the existing data session stays migrator.
            with target.connect():
                pass
            if target.restore_role is None:
                verify_identity(destination, target.identity)
            else:
                verify_effective_role(destination, target.identity, target.restore_role)
            expected_owner = target.restore_role.oid if target.restore_role is not None else target.identity.actor_oid
            wrong_owner = _one(destination,
                "SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
                "WHERE n.nspname <> 'information_schema' AND n.nspname !~ '^pg_' AND c.relowner<>%s",
                (expected_owner,),
            )[0]
            wrong_large_object_owner = _one(destination,
                "SELECT count(*) FROM pg_largeobject_metadata WHERE lomowner<>%s", (expected_owner,),
            )[0]
            wrong_schema_owner = _one(destination,
                "SELECT count(*) FROM pg_namespace WHERE nspname <> 'information_schema' "
                "AND nspname !~ '^pg_' AND nspname <> 'public' AND nspowner<>%s", (expected_owner,),
            )[0]
            if wrong_owner or wrong_large_object_owner or wrong_schema_owner:
                raise TransferDenied("restored_object_ownership_mismatch")
            counts = compare_content(exporter, destination, private, limits, end)
            if _sequences(exporter, limits) != sequences_before:
                raise TransferDenied("source_sequence_changed_during_verification")
            stable = _cut(native_cut, source, target, stable)
            _deadline(end)
            return {"transfer": "verified", "purpose": purpose, "schema": SCHEMA9,
                    "table_rows": counts, "sequence_count": len(sequences_before),
                    "native_cut_observed": stable is not None, "production_released": False}


def transfer(
    source: BoundDatabase, target: BoundDatabase, tools: PostgresTools, workdir: Path, *,
    purpose: Literal["rehearsal", "final"] = "rehearsal", limits: Limits = Limits(),
    native_cut: NativeCutBoundary | None = None,
) -> dict[str, Any]:
    try:
        return _transfer(source, target, tools, workdir, purpose=purpose, limits=limits,
                         native_cut=native_cut)
    except TransferDenied:
        raise
    except Exception:
        raise TransferDenied("observation_or_execution_unavailable_reconcile_before_retry") from None
