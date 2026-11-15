"""Fixed read-only identity probe for the November 15 Neon continuity target.

The continuity observer is a separate operator path. Historical release
collectors retain their original target and this probe issues no release token,
client connection, migration credential, or rollout decision.
"""

from __future__ import annotations

import hashlib
import os
from contextlib import closing
from dataclasses import dataclass
from enum import StrEnum
from ipaddress import ip_address

import certifi
import psycopg
from psycopg import Connection, ConnectionInfo, IsolationLevel

from scripts import neon_direct_identity as retained

PROJECT = "divine-flower-30944770"
BRANCH = "br-small-surf-bdqqplk8"
ENDPOINT = "ep-holy-sound-bdqtltxz"
HOST = f"{ENDPOINT}.c-8.us-east-2.aws.neon.tech"
DATABASE = "neondb"
DATABASE_OID = 16396
DATABASE_OWNER = "neondb_owner"
NAMESPACE_OWNER_OID = 6171
OBSERVER = "hirewiz_free_observer_20261115"
OBSERVER_OID = 49156
CLUSTER_SHA256 = "0d7476d0f01af04502bb5f3f30642420e3a3108db3ed8f5cbdd5e6e34709eab0"
APPLICATION = "hirewiz_neon_continuity_observer"


class Purpose(StrEnum):
    BEFORE_SCHEMA27 = "schema26_continuity_capture"
    AFTER_SCHEMA27 = "schema27_continuity_capture"

    @property
    def schema(self) -> str:
        if self is Purpose.BEFORE_SCHEMA27:
            return retained.SCHEMA26_RELEASE_SCHEMA
        return retained.SCHEMA27_RELEASE_SCHEMA


@dataclass(frozen=True, slots=True)
class Observation:
    purpose: Purpose
    project: str
    branch: str
    endpoint: str
    database_oid: int
    cluster_sha256: str
    observer_oid: int
    schema: str


def observe(purpose: Purpose, *, observer_password: str) -> Observation:
    """Connect using only the fixed observer. The caller retains private custody."""
    if type(purpose) is not Purpose:
        raise retained.NeonIdentityDenied("neon_continuity_purpose_invalid")
    if (type(observer_password) is not str or not observer_password or "\x00" in observer_password
            or len(observer_password) > 8192 or any(key.startswith("PG") for key in os.environ)):
        raise retained.NeonIdentityDenied("neon_continuity_connection_settings_invalid")
    try:
        settings = {
            "host": HOST,
            "dbname": DATABASE,
            "user": OBSERVER,
            "sslmode": "verify-full",
            "sslcertmode": "disable",
            "sslrootcert": certifi.where(),
            "channel_binding": "require",
            "connect_timeout": "5",
            "application_name": APPLICATION,
            "options": retained.CAPTURE_OPTIONS,
        }
        with closing(psycopg.connect(**settings, port=5432, password=observer_password)) as connection:
            _verify_transport(connection, settings)
            connection.read_only = True
            connection.isolation_level = IsolationLevel.REPEATABLE_READ
            result = _observe_catalog(connection, purpose)
            connection.rollback()
            return result
    except retained.NeonIdentityDenied:
        raise
    except Exception:
        raise retained.NeonIdentityDenied("neon_continuity_observation_unavailable") from None


def _verify_transport(connection: Connection, expected: dict[str, str]) -> None:
    if (not isinstance(connection, Connection) or not isinstance(connection.info, ConnectionInfo)
            or connection.closed or connection.pgconn.status != 0 or not connection.pgconn.ssl_in_use):
        raise retained.NeonIdentityDenied("neon_continuity_tls_transport_unavailable")
    info = connection.info
    parameters = info.get_parameters()
    raw_address = parameters.get("hostaddr")
    # psycopg's resolving driver supplies hostaddr internally. No caller can.
    if (type(raw_address) is not str or type(info.hostaddr) is not str
            or "%" in raw_address or "%" in info.hostaddr
            or ip_address(raw_address) != ip_address(info.hostaddr)
            or set(parameters) - {*expected, "hostaddr", "port"}
            or any(parameters.get(key) != value for key, value in expected.items())
            or parameters.get("port", "5432") != "5432"
            or (info.host, info.port, info.dbname, info.user) != (HOST, 5432, DATABASE, OBSERVER)):
        raise retained.NeonIdentityDenied("neon_continuity_effective_transport_mismatch")


def _observe_catalog(connection: Connection, purpose: Purpose) -> Observation:
    control = connection.execute(
        """
        SELECT p.oid, p.proowner, p.prosecdef, p.prosrc, l.lanname
        FROM pg_catalog.pg_proc p
        JOIN pg_catalog.pg_namespace n ON n.oid = p.pronamespace
        JOIN pg_catalog.pg_language l ON l.oid = p.prolang
        WHERE n.nspname = 'pg_catalog' AND p.proname = 'pg_control_system' AND p.pronargs = 0
        LIMIT 2
        """
    ).fetchall()
    if control != [(3441, 10, False, "pg_control_system", "internal")]:
        raise retained.NeonIdentityDenied("neon_continuity_native_control_mismatch")
    row = connection.execute(
        """
        SELECT d.oid, owner.rolname, r.oid, current_user, session_user, n.oid, n.nspowner,
               pg_catalog.current_setting('server_version_num')::int / 10000,
               pg_catalog.current_setting('transaction_read_only'),
               pg_catalog.current_setting('transaction_isolation'),
               r.rolcanlogin AND NOT (
                   r.rolinherit OR r.rolsuper OR r.rolcreaterole OR r.rolcreatedb OR r.rolreplication
                   OR r.rolbypassrls OR EXISTS(SELECT FROM pg_catalog.pg_auth_members m WHERE m.member = r.oid)
                   OR pg_catalog.has_schema_privilege(r.oid, n.oid, 'CREATE')
                   OR pg_catalog.has_database_privilege(r.oid, d.oid, 'CREATE')
               ),
               (SELECT system_identifier::text FROM pg_catalog.pg_control_system())
        FROM pg_catalog.pg_database d
        JOIN pg_catalog.pg_roles owner ON owner.oid = d.datdba
        JOIN pg_catalog.pg_roles r ON r.rolname = current_user
        JOIN pg_catalog.pg_namespace n ON n.nspname = 'public'
        WHERE d.datname = pg_catalog.current_database() AND d.datname = %s
        """,
        (DATABASE,),
    ).fetchone()
    if (row is None or row[:-1] != (DATABASE_OID, DATABASE_OWNER, OBSERVER_OID, OBSERVER,
            OBSERVER, 2200, NAMESPACE_OWNER_OID, 17, "on", "repeatable read", True)
            or type(row[-1]) is not str
            or hashlib.sha256(row[-1].encode()).hexdigest() != CLUSTER_SHA256):
        raise retained.NeonIdentityDenied("neon_continuity_catalog_target_mismatch")
    versions = connection.execute("SELECT version_num FROM public.alembic_version LIMIT 2").fetchall()
    if versions != [(purpose.schema,)]:
        raise retained.NeonIdentityDenied("neon_continuity_exact_schema_mismatch")
    return Observation(purpose, PROJECT, BRANCH, ENDPOINT, DATABASE_OID, CLUSTER_SHA256, OBSERVER_OID, purpose.schema)
