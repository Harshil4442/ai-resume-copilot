"""Explicit reviewed Neon direct route; cancellation keys are not native PIDs.

Only collect_neon_direct_proxy() issues an in-process binding. One fixed native
revision GET acquires the literal transient credential. No issuer/global fence
or role mutation is performed. SQL identity and full session checks stay in the
calling adapters. This binding currently covers only the retained schema9 actor.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
from dataclasses import dataclass
from ipaddress import ip_address
from pathlib import Path
from typing import Any, Literal
from weakref import WeakKeyDictionary

from psycopg import Connection as PsycopgConnection
from psycopg import ConnectionInfo
from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import URL, Engine, make_url
from sqlalchemy.pool import NullPool

PROJECT = "ai-resume-parser-482412"
REGION = "us-central1"
REVISION = "ai-resume-parser-e7-checkout-off-20261010"
IMAGE = "us-central1-docker.pkg.dev/ai-resume-parser-482412/cloud-run-source-deploy/hirewiz@sha256:cf2cb125a72c79a7b41f9c263c1a999521363d6c944affb3c017e0dc5e112f4d"
ORGANIZATION = "org-ancient-pond-42861142"
NEON_PROJECT = "little-pond-36610622"
BRANCH = "br-fancy-sunset-ao8i4a57"
ENDPOINT = "ep-damp-rice-aosknaqp"
CONTROL_RECEIPT = Path(
    "/tmp/hirewiz-neon-ui-prerequisite-20261010/console-inventory-sanitized.json"
)
CONTROL_RECEIPT_SHA = "b8a450d86f3e0613defac129012788b560a125ccd8ec9561d4f604150992e26c"
CLUSTER = "cabc69e0dc6343b8474c3e4d7fc4ca48e8a93e45c1694d1961a93ee56e4385f0"
ACTOR = "neondb_owner"
ACTOR_OID = 16387
DATABASE = "neondb"
DATABASE_OID = 16391
NAMESPACE = "public"
NAMESPACE_OID = 2200
ROOT_CERT = "/opt/homebrew/etc/openssl@3/cert.pem"
APPLICATION = "hirewiz_bound_neon_identity"
TIMEOUT_OPTIONS = (
    "-c statement_timeout=10000 -c lock_timeout=4000 -c idle_in_transaction_session_timeout=120000"
)
CAPTURE_OPTIONS = "-c default_transaction_read_only=on " + TIMEOUT_OPTIONS


class NeonIdentityDenied(RuntimeError):
    """Fixed nonsecret refusal; no credential or provider exception text."""


@dataclass(frozen=True, slots=True, weakref_slot=True, eq=False, repr=False)
class NeonDirectProxyBinding:
    host: str
    channel_binding: str
    options: str
    purpose: Literal["capture", "preparation"]


@dataclass(frozen=True, slots=True, repr=False)
class _TransientTarget:
    binding: NeonDirectProxyBinding
    _url: URL

    def engine(self) -> Engine:
        _registered(self.binding)
        # Explicit clean URL/kwargs; no alternate hostaddr/service/options input.
        engine = create_engine(
            self._url,
            poolclass=NullPool,
            echo=False,
            hide_parameters=True,
            connect_args={
                "connect_timeout": 5,
                "application_name": APPLICATION,
                "options": self.binding.options,
            },
        )

        def guarded_connect(
            dialect: Any, _record: Any, args: list[Any], params: dict[str, Any]
        ) -> PsycopgConnection:
            # do_connect precedes SQLAlchemy first_connect/dialect SQL. Return
            # only a real authenticated driver after effective transport proof.
            _registered(self.binding)
            if (
                args
                or "hostaddr" in params
                or "service" in params
                or any(key.startswith("PG") for key in os.environ)
            ):
                raise NeonIdentityDenied("neon_direct_connection_settings_unavailable")
            driver = dialect.connect(*args, **params)
            try:
                _verify_driver_transport(driver, self.binding)
                _guarded_drivers[driver] = self.binding
            except Exception:
                try:
                    driver.close()
                except Exception:
                    pass
                raise NeonIdentityDenied("neon_direct_connection_transport_refused") from None
            return driver

        event.listen(engine, "do_connect", guarded_connect)
        return engine


_issued: WeakKeyDictionary[NeonDirectProxyBinding, tuple[str, str, str, str]] = WeakKeyDictionary()
_guarded_drivers: WeakKeyDictionary[PsycopgConnection, NeonDirectProxyBinding] = WeakKeyDictionary()


def _registered(binding: NeonDirectProxyBinding) -> None:
    if type(binding) is not NeonDirectProxyBinding or _issued.get(binding) != (
        PROJECT,
        REGION,
        REVISION,
        IMAGE,
    ):
        raise NeonIdentityDenied("neon_direct_native_binding_unavailable")


def _control_receipt() -> None:
    raw = CONTROL_RECEIPT.read_bytes()
    if len(raw) > 131072 or hashlib.sha256(raw).hexdigest() != CONTROL_RECEIPT_SHA:
        raise NeonIdentityDenied("neon_direct_control_receipt_mismatch")
    receipt = json.loads(raw)
    expected = {
        "organization_id": ORGANIZATION,
        "project_id": NEON_PROJECT,
        "branch_id": BRANCH,
        "primary_endpoint_id": ENDPOINT,
    }
    if any(receipt.get(key) != value for key, value in expected.items()):
        raise NeonIdentityDenied("neon_direct_control_receipt_mismatch")


def _clean_environment() -> dict[str, str]:
    for key in tuple(os.environ):
        if key.startswith(
            ("PG", "GRPC_TRACE", "CLOUDSDK_CORE_LOG_HTTP", "CLOUDSDK_API_ENDPOINT_OVERRIDES_")
        ) or key.upper() in {
            "SSLKEYLOGFILE",
            "HTTP_PROXY",
            "HTTPS_PROXY",
            "ALL_PROXY",
            "NO_PROXY",
            "PYTHONINSPECT",
            "PYTHONVERBOSE",
            "CLOUDSDK_CORE_VERBOSITY",
            "GOOGLE_API_USE_MTLS_ENDPOINT",
            "GOOGLE_API_USE_CLIENT_CERTIFICATE",
            "SSL_CERT_FILE",
            "SSL_CERT_DIR",
            "REQUESTS_CA_BUNDLE",
            "CURL_CA_BUNDLE",
        }:
            os.environ.pop(key, None)
    env = os.environ.copy()
    env["CLOUDSDK_API_ENDPOINT_OVERRIDES_RUN"] = "https://run.googleapis.com/"
    env["CLOUDSDK_CORE_LOG_HTTP"] = "false"
    return env


def collect_neon_direct_proxy(
    *, purpose: Literal["capture", "preparation"] = "capture"
) -> tuple[NeonDirectProxyBinding, _TransientTarget]:
    """Explicit selection BEFORE SQL. No caller JSON/Boolean creates authority."""
    try:
        if purpose not in {"capture", "preparation"}:
            raise NeonIdentityDenied("neon_direct_purpose_unavailable")
        _control_receipt()
        result = subprocess.run(
            [
                "gcloud",
                "run",
                "revisions",
                "describe",
                REVISION,
                "--region",
                REGION,
                "--project",
                PROJECT,
                "--quiet",
                "--verbosity=error",
                "--format=json",
            ],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=25,
            env=_clean_environment(),
        )
        if len(result.stdout) > 131072:
            raise NeonIdentityDenied("neon_direct_native_response_bound")
        native = json.loads(result.stdout)
        containers = native["spec"]["containers"]
        if (
            native["metadata"]["name"] != REVISION
            or len(containers) != 1
            or containers[0]["image"] != IMAGE
            or native["status"]["imageDigest"] != IMAGE
        ):
            raise NeonIdentityDenied("neon_direct_native_source_mismatch")
        entries = [
            entry for entry in containers[0].get("env", []) if entry.get("name") == "DATABASE_URL"
        ]
        if (
            len(entries) != 1
            or set(entries[0]) != {"name", "value"}
            or type(entries[0]["value"]) is not str
        ):
            raise NeonIdentityDenied("neon_direct_literal_slot_required")
        url = make_url(entries[0]["value"])
        # Exact endpoint and region, copied from the reviewed control-plane receipt.
        if (
            url.drivername not in {"postgresql", "postgresql+psycopg"}
            or url.port not in {None, 5432}
            or url.database != DATABASE
            or url.username != ACTOR
            or not re.fullmatch(
                re.escape(ENDPOINT)
                + r"(?:-pooler)?(?:\.c-[0-9]+)?\.ap-southeast-1\.aws\.neon\.tech",
                url.host or "",
            )
        ):
            raise NeonIdentityDenied("neon_direct_target_mismatch")
        if (
            set(url.query) - {"sslmode", "channel_binding"}
            or url.query.get("sslmode", "require") not in {"require", "verify-ca", "verify-full"}
            or url.query.get("channel_binding", "prefer") not in {"prefer", "require"}
        ):
            raise NeonIdentityDenied("neon_direct_connection_settings_unavailable")
        if (
            type(url.password) is not str
            or not url.password
            or "\x00" in url.password
            or len(url.password.encode()) > 8192
        ):
            raise NeonIdentityDenied("neon_direct_credential_shape_unavailable")
        host = re.sub(r"^" + re.escape(ENDPOINT) + r"-pooler(?=\.)", ENDPOINT, url.host or "")
        channel = str(url.query.get("channel_binding", "prefer"))
        options = CAPTURE_OPTIONS if purpose == "capture" else TIMEOUT_OPTIONS
        binding = NeonDirectProxyBinding(host, channel, options, purpose)
        clean = URL.create(
            "postgresql+psycopg",
            username=ACTOR,
            password=url.password,
            host=host,
            port=5432,
            database=DATABASE,
            query={
                "sslmode": "verify-full",
                "sslcertmode": "disable",
                "sslrootcert": ROOT_CERT,
                "channel_binding": channel,
            },
        )
        _issued[binding] = (PROJECT, REGION, REVISION, IMAGE)
        return binding, _TransientTarget(binding, clean)
    except NeonIdentityDenied:
        raise
    except Exception:
        raise NeonIdentityDenied("neon_direct_collection_unavailable") from None


def verify_transport(connection: Any, binding: NeonDirectProxyBinding) -> None:
    """Recheck genuine transport before protected metadata/session observation."""
    try:
        _registered(binding)
        driver = connection.connection.driver_connection
        if _guarded_drivers.get(driver) is not binding:
            raise NeonIdentityDenied("neon_direct_guarded_driver_unavailable")
        _verify_driver_transport(driver, binding)
    except NeonIdentityDenied:
        raise
    except Exception:
        raise NeonIdentityDenied("neon_direct_transport_observation_unavailable") from None


def _verify_driver_transport(driver: Any, binding: NeonDirectProxyBinding) -> None:
    """Effective libpq proof also runs before SQLAlchemy dialect initialization."""
    try:
        _registered(binding)
        if not isinstance(driver, PsycopgConnection) or not isinstance(driver.info, ConnectionInfo):
            raise NeonIdentityDenied("neon_direct_genuine_transport_unavailable")
        if driver.closed or driver.pgconn.status != 0 or not driver.pgconn.ssl_in_use:
            raise NeonIdentityDenied("neon_direct_tls_transport_unavailable")
        params = driver.info.get_parameters()  # Values stay private; only password is excluded.
        allowed = {
            "host",
            "hostaddr",
            "port",
            "dbname",
            "user",
            "sslmode",
            "sslcertmode",
            "sslrootcert",
            "channel_binding",
            "connect_timeout",
            "application_name",
            "options",
        }
        expected = {
            "host": binding.host,
            "dbname": DATABASE,
            "user": ACTOR,
            "sslmode": "verify-full",
            "sslcertmode": "disable",
            "sslrootcert": ROOT_CERT,
            "connect_timeout": "5",
            "application_name": APPLICATION,
            "options": binding.options,
        }
        # psycopg resolves the clean hostname into one numeric attempt before
        # libpq connect. Accept that derived address only with genuine PQ proof;
        # caller hostaddr/service inputs remain refused before connect.
        hostaddr = params.get("hostaddr")
        actual_hostaddr = driver.info.hostaddr
        if (
            type(hostaddr) is not str
            or type(actual_hostaddr) is not str
            or not hostaddr
            or not actual_hostaddr
            or len(hostaddr) > 45
            or len(actual_hostaddr) > 45
            or "%" in hostaddr
            or "%" in actual_hostaddr
        ):
            raise NeonIdentityDenied("neon_direct_effective_transport_mismatch")
        try:
            address_matches = ip_address(hostaddr) == ip_address(actual_hostaddr)
        except ValueError:
            raise NeonIdentityDenied("neon_direct_effective_transport_mismatch") from None
        if (
            not address_matches
            or set(params) - allowed
            # get_parameters omits values equal to libpq compiled defaults.
            # Only port is an expected default; actual PQport is still pinned.
            or params.get("port", "5432") != "5432"
            or any(params.get(key) != value for key, value in expected.items())
            or params.get("channel_binding", "prefer") != binding.channel_binding
            or driver.info.host != binding.host
            or driver.info.port != 5432
            or driver.info.dbname != DATABASE
            or driver.info.user != ACTOR
        ):
            raise NeonIdentityDenied("neon_direct_effective_transport_mismatch")
    except NeonIdentityDenied:
        raise
    except Exception:
        raise NeonIdentityDenied("neon_direct_transport_observation_unavailable") from None


def verify_native_target(
    connection: Any, binding: NeonDirectProxyBinding, database_oid: int
) -> None:
    _registered(binding)
    control = connection.execute(
        text(
            "SELECT p.oid,p.proowner,p.prosecdef,p.prosrc,l.lanname FROM pg_catalog.pg_proc p "
            "JOIN pg_catalog.pg_namespace n ON n.oid=p.pronamespace JOIN pg_catalog.pg_language l ON l.oid=p.prolang "
            "WHERE n.nspname='pg_catalog' AND p.proname='pg_control_system' AND p.pronargs=0 LIMIT 2"
        )
    ).all()
    if [tuple(row) for row in control] != [(3441, 10, False, "pg_control_system", "internal")]:
        raise NeonIdentityDenied("neon_direct_native_control_mismatch")
    row = connection.execute(
        text(
            "SELECT d.oid,d.datdba,r.oid,current_user,session_user,n.oid,"
            "(SELECT system_identifier::text FROM pg_catalog.pg_control_system()) "
            "FROM pg_catalog.pg_database d JOIN pg_catalog.pg_roles r ON r.rolname=current_user "
            "JOIN pg_catalog.pg_namespace n ON n.nspname='public' WHERE d.datname=pg_catalog.current_database()"
        )
    ).one()
    if (
        tuple(row[:6]) != (DATABASE_OID, ACTOR_OID, ACTOR_OID, ACTOR, ACTOR, NAMESPACE_OID)
        or database_oid != DATABASE_OID
        or type(row[6]) is not str
        or hashlib.sha256(row[6].encode()).hexdigest() != CLUSTER
    ):
        raise NeonIdentityDenied("neon_direct_native_target_mismatch")


def cancellation_key_shape(value: Any) -> bool:
    # libpq stores the proxy's first random four bytes as C signed int. No
    # positivity or compute-PID equality is inferred from this opaque key.
    return type(value) is int and -(2**31) <= value < 2**31
