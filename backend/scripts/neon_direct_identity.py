"""Explicit reviewed Neon direct route; cancellation keys are not native PIDs.

Collectors issue in-process bindings from fixed native revision or numeric
secret reads. No global fence or role mutation is performed. SQL identity and
full session checks stay in the calling adapters. Schema19 has a distinct fresh
read-only purpose; retained and existing release routes keep their old schemas.
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
from typing import Any, Literal, Protocol
from weakref import WeakKeyDictionary

from psycopg import Connection as PsycopgConnection
from psycopg import ConnectionInfo
from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import URL, Engine, make_url
from sqlalchemy.pool import NullPool

try:
    from scripts.neon_execution_files import LinuxExecutionFiles
except ModuleNotFoundError:  # Direct standalone script invocation.
    from neon_execution_files import LinuxExecutionFiles

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
PROJECT_NUMBER = "157225724590"
RELEASE_CONTROL_RECEIPT = Path(
    "/Users/harshil/.codex/hirewiz-evidence/20261012/root/neon-controlplane-identity-ui-current.json"
)
RELEASE_CONTROL_RECEIPT_SHA = "875747de81988a2504b1d75b78813b96f274f2e338005bc1f69c7b4bb730ad0a"
RELEASE_DIRECT_HOST = "ep-damp-rice-aosknaqp.c-2.ap-southeast-1.aws.neon.tech"
RELEASE_POOLER_HOST = "ep-damp-rice-aosknaqp-pooler.c-2.ap-southeast-1.aws.neon.tech"
RELEASE_SCHEMAS = frozenset({
    "20261008_0009", "20261009_0010", "20261009_0011", "20261009_0012",
    "20261009_0013", "20261010_0014", "20261011_0015", "20261011_0016",
    "20261014_0017",
    "20261015_0018",
})
SCHEMA19_RELEASE_SCHEMA = "20261015_0019"
SCHEMA20_RELEASE_SCHEMA = "20261023_0020"
SCHEMA21_RELEASE_SCHEMA = "20261023_0021"
RELEASE_ACTORS = {
    "observer": ("hirewiz_observer_20261010_r1", 1884182),
    "replacement_runtime": ("hirewiz_runtime_20261010_r1", 1884178),
    "replacement_migration": ("hirewiz_migrator_20261010_r1", 1884180),
}
RELEASE_REFERENCE = re.compile(r"[a-z][a-z0-9-]{2,62}:[1-9][0-9]{0,8}")

# Distinct fresh target. Never overwrite the retained source identity above.
FRESH_NEON_PROJECT = "shiny-cell-70214683"
FRESH_BRANCH = "br-square-mode-b4g6sxrz"
FRESH_ENDPOINT = "ep-weathered-tooth-b43oa1n6"
FRESH_DIRECT_HOST = "ep-weathered-tooth-b43oa1n6.c-6.us-east-2.aws.neon.tech"
FRESH_DATABASE_OID = 16395
FRESH_DATABASE_OWNER_OID = 16391
FRESH_NAMESPACE_OWNER_OID = 6171
FRESH_CLUSTER = "61273750d6782a6bc688488b47d274ce81589d8066d54f389e7773a834e7ccd7"
FRESH_CONTROL_RECEIPT = Path(
    "/Users/harshil/.codex/hirewiz-evidence/20261013/neon-normal-manager-metadata-route-private-g1xawwzt/"
    "fresh-project/role-creation.terminal.json"
)
FRESH_CONTROL_RECEIPT_SHA = "44af0a94b0912c3649e4fc757e972c863dca2f03c423d9c1e9a14266c7c46c39"
FRESH_RELEASE_ACTORS = {
    "observer": ("hirewiz_observer_20261013_r1", 24580),
    "replacement_runtime": ("hirewiz_runtime_20261013_r1", 24576),
    "replacement_migration": ("hirewiz_migrator_20261013_r1", 24578),
}


class NativeReleaseSecretReader(Protocol):
    # Production uses NativeBoundary, under its reviewed normal native command
    # route. No CLI/manifest callback or credential/provenance bool is accepted.
    def describe_release_secret(self, reference: str) -> dict[str, Any]: ...
    def access_release_secret(self, reference: str) -> str: ...


class NeonIdentityDenied(RuntimeError):
    """Fixed nonsecret refusal; no credential or provider exception text."""


@dataclass(frozen=True, slots=True, weakref_slot=True, eq=False, repr=False)
class NeonDirectProxyBinding:
    host: str
    channel_binding: str
    options: str
    purpose: Literal["capture", "preparation", "release_capture", "fresh_release_capture",
                     "schema19_fresh_release_capture", "schema20_fresh_release_capture", "schema21_fresh_release_capture"]
    actor: str = ACTOR
    actor_oid: int = ACTOR_OID
    database_owner_oid: int = ACTOR_OID
    expected_schema: str = "20261008_0009"
    execution_files: LinuxExecutionFiles | None = None


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

    def dump_environment(self) -> dict[str, str]:
        _registered(self.binding)
        # The caller passes this dictionary only to the owned pg_dump process.
        # No ambient PG setting, alternate host or source query reaches libpq.
        return {
            "PGHOST": self.binding.host, "PGPORT": "5432",
            "PGUSER": self.binding.actor, "PGPASSWORD": self._url.password or "",
            "PGDATABASE": DATABASE, "PGSSLMODE": "verify-full",
            "PGSSLCERTMODE": "disable", "PGSSLROOTCERT": _root_certificate(self.binding),
            "PGCHANNELBINDING": self.binding.channel_binding,
            "PGCONNECT_TIMEOUT": "5", "PGAPPNAME": APPLICATION,
            "PGOPTIONS": self.binding.options,
        }


_issued: WeakKeyDictionary[NeonDirectProxyBinding, tuple[str, str, str, str]] = WeakKeyDictionary()
_guarded_drivers: WeakKeyDictionary[PsycopgConnection, NeonDirectProxyBinding] = WeakKeyDictionary()


def _registered(binding: NeonDirectProxyBinding) -> None:
    if type(binding) is not NeonDirectProxyBinding:
        raise NeonIdentityDenied("neon_direct_native_binding_unavailable")
    if binding.execution_files is not None:
        if (type(binding.execution_files) is not LinuxExecutionFiles
                or binding.purpose not in {"release_capture", "fresh_release_capture", "schema19_fresh_release_capture", "schema20_fresh_release_capture", "schema21_fresh_release_capture"}):
            raise NeonIdentityDenied("neon_linux_execution_files_unavailable")
        try:
            binding.execution_files.validate()
        except Exception:
            raise NeonIdentityDenied("neon_linux_execution_files_unavailable") from None
    provenance = _issued.get(binding)
    legacy = (
        binding.purpose in {"capture", "preparation"}
        and provenance == (PROJECT, REGION, REVISION, IMAGE)
        and (binding.actor, binding.actor_oid, binding.database_owner_oid, binding.expected_schema)
        == (ACTOR, ACTOR_OID, ACTOR_OID, "20261008_0009")
        and binding.options == (CAPTURE_OPTIONS if binding.purpose == "capture" else TIMEOUT_OPTIONS)
    )
    release = (
        binding.purpose == "release_capture"
        and type(provenance) is tuple and len(provenance) == 4
        and provenance[0] == PROJECT and provenance[3] == RELEASE_CONTROL_RECEIPT_SHA
        and type(provenance[1]) is str
        and re.fullmatch(r"projects/" + PROJECT_NUMBER + r"/secrets/[a-z][a-z0-9-]{2,62}/versions/[1-9][0-9]{0,8}", provenance[1]) is not None
        and type(provenance[2]) is str and 1 <= len(provenance[2]) <= 256
        and (binding.actor, binding.actor_oid) in RELEASE_ACTORS.values()
        and type(binding.actor_oid) is int
        and type(binding.database_owner_oid) is int
        and 0 < binding.database_owner_oid <= 4294967295
        and binding.expected_schema in RELEASE_SCHEMAS
        and binding.host == RELEASE_DIRECT_HOST
        and binding.channel_binding in {"prefer", "require"}
        and binding.options == CAPTURE_OPTIONS
    )
    fresh_release = (
        binding.purpose in {"fresh_release_capture", "schema19_fresh_release_capture", "schema20_fresh_release_capture", "schema21_fresh_release_capture"}
        and type(provenance) is tuple and len(provenance) == 4
        and provenance[0] == PROJECT and provenance[3] == FRESH_CONTROL_RECEIPT_SHA
        and type(provenance[1]) is str
        and re.fullmatch(r"projects/" + PROJECT_NUMBER + r"/secrets/[a-z][a-z0-9-]{2,62}/versions/[1-9][0-9]{0,8}", provenance[1]) is not None
        and type(provenance[2]) is str and 1 <= len(provenance[2]) <= 256
        and (binding.actor, binding.actor_oid) in FRESH_RELEASE_ACTORS.values()
        and type(binding.actor_oid) is int and type(binding.database_owner_oid) is int
        and binding.database_owner_oid == FRESH_DATABASE_OWNER_OID
        and ((binding.purpose == "fresh_release_capture" and binding.expected_schema in RELEASE_SCHEMAS)
             or (binding.purpose == "schema19_fresh_release_capture"
                 and binding.expected_schema == SCHEMA19_RELEASE_SCHEMA)
             or (binding.purpose == "schema20_fresh_release_capture"
                 and binding.expected_schema == SCHEMA20_RELEASE_SCHEMA)
             or (binding.purpose == "schema21_fresh_release_capture"
                 and binding.expected_schema == SCHEMA21_RELEASE_SCHEMA))
        and binding.host == FRESH_DIRECT_HOST and binding.channel_binding in {"prefer", "require"}
        and binding.options == CAPTURE_OPTIONS
    )
    if not legacy and not release and not fresh_release:
        raise NeonIdentityDenied("neon_direct_native_binding_unavailable")


def _execution_files(reader: NativeReleaseSecretReader) -> LinuxExecutionFiles | None:
    files = getattr(reader, "linux_execution_files", None)
    if files is None:
        return None
    if type(files) is not LinuxExecutionFiles:
        raise NeonIdentityDenied("neon_linux_execution_files_unavailable")
    files.validate()
    return files


def _root_certificate(binding: NeonDirectProxyBinding) -> str:
    return binding.execution_files.root_certificate if binding.execution_files is not None else ROOT_CERT


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
                "sslrootcert": _root_certificate(binding),
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
            "user": binding.actor,
            "sslmode": "verify-full",
            "sslcertmode": "disable",
            "sslrootcert": _root_certificate(binding),
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
            or driver.info.user != binding.actor
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
    if binding.purpose in {"fresh_release_capture", "schema19_fresh_release_capture", "schema20_fresh_release_capture", "schema21_fresh_release_capture"}:
        _verify_fresh_native_target(connection, binding, database_oid)
        return
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
        tuple(row[:6]) != (DATABASE_OID, binding.database_owner_oid, binding.actor_oid,
                           binding.actor, binding.actor, NAMESPACE_OID)
        or database_oid != DATABASE_OID
        or type(row[6]) is not str
        or hashlib.sha256(row[6].encode()).hexdigest() != CLUSTER
    ):
        raise NeonIdentityDenied("neon_direct_native_target_mismatch")
    if binding.purpose == "release_capture":
        verify_release_schema(connection, binding)


def cancellation_key_shape(value: Any) -> bool:
    # libpq stores the proxy's first random four bytes as C signed int. No
    # positivity or compute-PID equality is inferred from this opaque key.
    return type(value) is int and -(2**31) <= value < 2**31



def bound_schema(binding: NeonDirectProxyBinding) -> str:
    _registered(binding)
    return binding.expected_schema


def _release_control_receipt(files: LinuxExecutionFiles | None = None) -> None:
    raw = files.read("source") if files is not None else RELEASE_CONTROL_RECEIPT.read_bytes()
    if ((files is None and RELEASE_CONTROL_RECEIPT.is_symlink()) or len(raw) > 131072
            or hashlib.sha256(raw).hexdigest() != RELEASE_CONTROL_RECEIPT_SHA):
        raise NeonIdentityDenied("neon_release_control_receipt_mismatch")
    receipt = json.loads(raw)
    expected = {
        "organization_id_from_actual_billing_href": ORGANIZATION,
        "project_id": NEON_PROJECT, "branch_id": BRANCH, "endpoint_id": ENDPOINT,
        "direct_host": RELEASE_DIRECT_HOST, "pooler_host": RELEASE_POOLER_HOST,
        "database_name": DATABASE, "postgres_major": 17,
    }
    if any(type(receipt.get(k)) is not type(v) or receipt[k] != v for k, v in expected.items()):
        raise NeonIdentityDenied("neon_release_control_receipt_mismatch")
    # This dated native provider target projection is NOT provider fencing or
    # admin authority. Authentication/TLS/native SQL proof is still required.


def collect_release_target(
    reader: NativeReleaseSecretReader, reference: str, *, alias: str,
    database_owner_oid: int, expected_schema: str,
) -> tuple[NeonDirectProxyBinding, _TransientTarget]:
    """Read exact native secret source; issue only a read-only transport target.

    Production calls this internally with NativeBoundary. No input DSN or
    manifest Boolean can substitute for native secret reads/real SQL identity.
    This does not issue native fencing, backup, migration or release authority.
    """
    try:
        if (type(reference) is not str or RELEASE_REFERENCE.fullmatch(reference) is None
                or type(alias) is not str or alias not in RELEASE_ACTORS
                or type(expected_schema) is not str or expected_schema not in RELEASE_SCHEMAS
                or type(database_owner_oid) is not int
                or not 0 < database_owner_oid <= 4294967295):
            raise NeonIdentityDenied("neon_release_identity_input_invalid")
        files = _execution_files(reader)
        _release_control_receipt(files)
        name, version = reference.split(":")
        full = f"projects/{PROJECT_NUMBER}/secrets/{name}/versions/{version}"
        before = reader.describe_release_secret(reference)
        if (type(before) is not dict or before.get("name") != full
                or before.get("state") != "ENABLED"
                or type(before.get("etag")) is not str
                or not 1 <= len(before["etag"]) <= 256
                or any(c.isspace() for c in before["etag"])):
            raise NeonIdentityDenied("neon_release_secret_source_mismatch")
        value = reader.access_release_secret(reference)
        if type(value) is not str or not 1 <= len(value.encode()) <= 8192 or "\x00" in value:
            raise NeonIdentityDenied("neon_release_credential_shape_unavailable")
        if reader.describe_release_secret(reference) != before:
            raise NeonIdentityDenied("neon_release_secret_source_changed")
        url = make_url(value.strip())
        actor, actor_oid = RELEASE_ACTORS[alias]
        if (url.drivername not in {"postgresql", "postgresql+psycopg"}
                or url.port not in {None, 5432} or url.database != DATABASE
                or url.username != actor
                or url.host not in {RELEASE_DIRECT_HOST, RELEASE_POOLER_HOST}):
            raise NeonIdentityDenied("neon_release_target_mismatch")
        if (set(url.query) - {"sslmode", "channel_binding"}
                or url.query.get("sslmode", "require") not in {"require", "verify-ca", "verify-full"}
                or url.query.get("channel_binding", "prefer") not in {"prefer", "require"}):
            raise NeonIdentityDenied("neon_release_connection_settings_unavailable")
        if (type(url.password) is not str or not url.password or "\x00" in url.password
                or len(url.password.encode()) > 8192):
            raise NeonIdentityDenied("neon_release_credential_shape_unavailable")
        channel = str(url.query.get("channel_binding", "prefer"))
        binding = NeonDirectProxyBinding(
            RELEASE_DIRECT_HOST, channel, CAPTURE_OPTIONS, "release_capture",
            actor, actor_oid, database_owner_oid, expected_schema, execution_files=files,
        )
        clean = URL.create(
            "postgresql+psycopg", username=actor, password=url.password,
            host=RELEASE_DIRECT_HOST, port=5432, database=DATABASE,
            query={"sslmode": "verify-full", "sslcertmode": "disable",
                   "sslrootcert": _root_certificate(binding), "channel_binding": channel},
        )
        _issued[binding] = (PROJECT, full, before["etag"], RELEASE_CONTROL_RECEIPT_SHA)
        _registered(binding)
        return binding, _TransientTarget(binding, clean)
    except NeonIdentityDenied:
        raise
    except Exception:
        raise NeonIdentityDenied("neon_release_source_or_target_unavailable") from None



def verify_release_schema(connection: Any, binding: NeonDirectProxyBinding) -> None:
    _registered(binding)
    if binding.purpose not in {"release_capture", "fresh_release_capture", "schema19_fresh_release_capture", "schema20_fresh_release_capture", "schema21_fresh_release_capture"}:
        raise NeonIdentityDenied("neon_release_schema_route_unavailable")
    versions = list(connection.execute(
        text("SELECT version_num FROM public.alembic_version LIMIT 3")
    ).scalars())
    if len(versions) != 1 or type(versions[0]) is not str or versions[0] != binding.expected_schema:
        raise NeonIdentityDenied("neon_release_exact_schema_mismatch")



def collect_source_release_target(
    reader: NativeReleaseSecretReader, reference: str, *, alias: str,
    database_owner_oid: int, expected_schema: str,
) -> tuple[NeonDirectProxyBinding, _TransientTarget]:
    """Explicit retained-source route; never select the fresh target implicitly."""
    return collect_release_target(reader, reference, alias=alias,
        database_owner_oid=database_owner_oid, expected_schema=expected_schema)


def _fresh_control_receipt(files: LinuxExecutionFiles | None = None) -> None:
    # Historical native target provenance only, never fencing or release authority.
    raw = files.read("roles") if files is not None else FRESH_CONTROL_RECEIPT.read_bytes()
    if ((files is None and FRESH_CONTROL_RECEIPT.is_symlink()) or len(raw) > 131072
            or hashlib.sha256(raw).hexdigest() != FRESH_CONTROL_RECEIPT_SHA):
        raise NeonIdentityDenied("neon_fresh_control_receipt_mismatch")
    receipt = json.loads(raw)
    expected = {"status": "target_roles_created", "stage": "fresh_target_role_creation",
        "org_id": ORGANIZATION, "project_id": FRESH_NEON_PROJECT, "branch_id": FRESH_BRANCH,
        "endpoint_id": FRESH_ENDPOINT, "direct_host": FRESH_DIRECT_HOST, "database": DATABASE,
        "commit_acknowledged": True, "production_switch": False, "global_provider_fence_proven": False}
    if (type(receipt) is not dict
            or any(type(receipt.get(k)) is not type(v) or receipt[k] != v for k, v in expected.items())
            or receipt.get("summary", {}).get("role_oids") != {name: oid for name, oid in FRESH_RELEASE_ACTORS.values()}):
        raise NeonIdentityDenied("neon_fresh_control_receipt_mismatch")


def collect_fresh_release_target(
    reader: NativeReleaseSecretReader, reference: str, *, alias: str,
    database_owner_oid: int, expected_schema: str,
) -> tuple[NeonDirectProxyBinding, _TransientTarget]:
    """Genuine numeric-secret source and fixed fresh target; read-only identity only.

    No transfer, old credential retirement, privilege finalization or promotion is
    implied. Newly prepared NOLOGIN roles need separately approved credential work.
    """
    return _collect_fresh_release_target(reader, reference, alias=alias,
        database_owner_oid=database_owner_oid, expected_schema=expected_schema,
        purpose="fresh_release_capture")


def collect_schema19_release_target(
    reader: NativeReleaseSecretReader, reference: str, *, alias: str,
    database_owner_oid: int, expected_schema: str = SCHEMA19_RELEASE_SCHEMA,
) -> tuple[NeonDirectProxyBinding, _TransientTarget]:
    """Distinct schema19 read-only capture; never a schema18 release admission.

    Issue directly through the same native numeric-secret and fixed-target proof.
    Existing release purposes remain unable to acquire or verify schema19.
    """
    return _collect_fresh_release_target(reader, reference, alias=alias,
        database_owner_oid=database_owner_oid, expected_schema=expected_schema,
        purpose="schema19_fresh_release_capture")


def collect_schema20_release_target(
    reader: NativeReleaseSecretReader, reference: str, *, alias: str,
    database_owner_oid: int, expected_schema: str = SCHEMA20_RELEASE_SCHEMA,
) -> tuple[NeonDirectProxyBinding, _TransientTarget]:
    """Distinct exact-schema20 reader with the same native source/TLS identity.

    Neither this purpose nor an issued binding authorizes DDL or account work.
    All previous collectors retain their original exact schema allowlists.
    """
    return _collect_fresh_release_target(reader, reference, alias=alias,
        database_owner_oid=database_owner_oid, expected_schema=expected_schema,
        purpose="schema20_fresh_release_capture")



def collect_schema21_release_target(
    reader: NativeReleaseSecretReader, reference: str, *, alias: str,
    database_owner_oid: int, expected_schema: str = SCHEMA21_RELEASE_SCHEMA,
) -> tuple[NeonDirectProxyBinding, _TransientTarget]:
    """Disjoint exact-schema21 observation; previous purposes keep their schemas."""
    return _collect_fresh_release_target(reader, reference, alias=alias,
        database_owner_oid=database_owner_oid, expected_schema=expected_schema,
        purpose="schema21_fresh_release_capture")


def _collect_fresh_release_target(
    reader: NativeReleaseSecretReader, reference: str, *, alias: str,
    database_owner_oid: int, expected_schema: str,
    purpose: Literal["fresh_release_capture", "schema19_fresh_release_capture", "schema20_fresh_release_capture", "schema21_fresh_release_capture"],
) -> tuple[NeonDirectProxyBinding, _TransientTarget]:
    """One native source/transport issuer with disjoint exact schema purposes."""
    try:
        schema_allowed = type(expected_schema) is str and (
            purpose == "fresh_release_capture" and expected_schema in RELEASE_SCHEMAS
            or purpose == "schema19_fresh_release_capture" and expected_schema == SCHEMA19_RELEASE_SCHEMA
            or purpose == "schema20_fresh_release_capture" and expected_schema == SCHEMA20_RELEASE_SCHEMA
            or purpose == "schema21_fresh_release_capture" and expected_schema == SCHEMA21_RELEASE_SCHEMA
        )
        if (type(reference) is not str or RELEASE_REFERENCE.fullmatch(reference) is None
                or type(alias) is not str or alias not in FRESH_RELEASE_ACTORS
                or not schema_allowed
                or type(database_owner_oid) is not int or database_owner_oid != FRESH_DATABASE_OWNER_OID):
            raise NeonIdentityDenied("neon_fresh_identity_input_invalid")
        files = _execution_files(reader)
        _fresh_control_receipt(files)
        if files is not None:
            files.verify_fresh_reference(alias, reference)
        name, version = reference.split(":")
        full = f"projects/{PROJECT_NUMBER}/secrets/{name}/versions/{version}"
        before = reader.describe_release_secret(reference)
        if (type(before) is not dict or before.get("name") != full or before.get("state") != "ENABLED"
                or type(before.get("etag")) is not str or not 1 <= len(before["etag"]) <= 256
                or any(c.isspace() for c in before["etag"])):
            raise NeonIdentityDenied("neon_fresh_secret_source_mismatch")
        value = reader.access_release_secret(reference)
        if type(value) is not str or not 1 <= len(value.encode()) <= 8192 or "\x00" in value:
            raise NeonIdentityDenied("neon_fresh_credential_shape_unavailable")
        if reader.describe_release_secret(reference) != before:
            raise NeonIdentityDenied("neon_fresh_secret_source_changed")
        url = make_url(value.strip())
        actor, actor_oid = FRESH_RELEASE_ACTORS[alias]
        if (url.drivername not in {"postgresql", "postgresql+psycopg"}
                or url.port not in {None, 5432} or url.database != DATABASE
                or url.username != actor or url.host != FRESH_DIRECT_HOST):
            raise NeonIdentityDenied("neon_fresh_target_mismatch")
        if (set(url.query) - {"sslmode", "channel_binding"}
                or url.query.get("sslmode", "require") not in {"require", "verify-ca", "verify-full"}
                or url.query.get("channel_binding", "prefer") not in {"prefer", "require"}):
            raise NeonIdentityDenied("neon_fresh_connection_settings_unavailable")
        if (type(url.password) is not str or not url.password or "\x00" in url.password
                or len(url.password.encode()) > 8192):
            raise NeonIdentityDenied("neon_fresh_credential_shape_unavailable")
        channel = str(url.query.get("channel_binding", "prefer"))
        binding = NeonDirectProxyBinding(FRESH_DIRECT_HOST, channel, CAPTURE_OPTIONS,
            purpose, actor, actor_oid, database_owner_oid, expected_schema,
            execution_files=files)
        clean = URL.create("postgresql+psycopg", username=actor, password=url.password,
            host=FRESH_DIRECT_HOST, port=5432, database=DATABASE,
            query={"sslmode": "verify-full", "sslcertmode": "disable", "sslrootcert": _root_certificate(binding),
                   "channel_binding": channel})
        _issued[binding] = (PROJECT, full, before["etag"], FRESH_CONTROL_RECEIPT_SHA)
        _registered(binding)
        return binding, _TransientTarget(binding, clean)
    except NeonIdentityDenied:
        raise
    except Exception:
        raise NeonIdentityDenied("neon_fresh_source_or_target_unavailable") from None


def _verify_fresh_native_target(
    connection: Any, binding: NeonDirectProxyBinding, database_oid: int,
) -> None:
    _registered(binding)
    if binding.purpose not in {"fresh_release_capture", "schema19_fresh_release_capture", "schema20_fresh_release_capture", "schema21_fresh_release_capture"}:
        raise NeonIdentityDenied("neon_fresh_native_binding_unavailable")
    control = connection.execute(text(
        "SELECT p.oid,p.proowner,p.prosecdef,p.prosrc,l.lanname FROM pg_catalog.pg_proc p "
        "JOIN pg_catalog.pg_namespace n ON n.oid=p.pronamespace JOIN pg_catalog.pg_language l ON l.oid=p.prolang "
        "WHERE n.nspname='pg_catalog' AND p.proname='pg_control_system' AND p.pronargs=0 LIMIT 2"
    )).all()
    if [tuple(row) for row in control] != [(3441, 10, False, "pg_control_system", "internal")]:
        raise NeonIdentityDenied("neon_fresh_native_control_mismatch")
    row = connection.execute(text(
        "SELECT d.oid,d.datdba,r.oid,current_user,session_user,n.oid,n.nspowner,"
        "(SELECT system_identifier::text FROM pg_catalog.pg_control_system()) "
        "FROM pg_catalog.pg_database d JOIN pg_catalog.pg_roles r ON r.rolname=current_user "
        "JOIN pg_catalog.pg_namespace n ON n.nspname='public' WHERE d.datname=pg_catalog.current_database()"
    )).one()
    if (tuple(row[:7]) != (FRESH_DATABASE_OID, FRESH_DATABASE_OWNER_OID, binding.actor_oid,
            binding.actor, binding.actor, NAMESPACE_OID, FRESH_NAMESPACE_OWNER_OID)
            or type(database_oid) is not int or database_oid != FRESH_DATABASE_OID
            or type(row[7]) is not str or hashlib.sha256(row[7].encode()).hexdigest() != FRESH_CLUSTER):
        raise NeonIdentityDenied("neon_fresh_native_target_mismatch")
    verify_release_schema(connection, binding)
