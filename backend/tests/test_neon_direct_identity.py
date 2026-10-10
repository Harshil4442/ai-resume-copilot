"""Fake native transports only; no provider request or actual TLS acceptance claim."""

import hashlib
import json
from dataclasses import replace
from types import SimpleNamespace

import pytest

from scripts import neon_direct_identity as neon


@pytest.fixture
def native(monkeypatch, tmp_path):
    receipt = {
        "organization_id": neon.ORGANIZATION,
        "project_id": neon.NEON_PROJECT,
        "branch_id": neon.BRANCH,
        "primary_endpoint_id": neon.ENDPOINT,
    }
    file = tmp_path / "receipt.json"
    file.write_text(json.dumps(receipt))
    monkeypatch.setattr(neon, "CONTROL_RECEIPT", file)
    monkeypatch.setattr(neon, "CONTROL_RECEIPT_SHA", hashlib.sha256(file.read_bytes()).hexdigest())
    raw = f"postgresql://{neon.ACTOR}:synthetic-secret-only@{neon.ENDPOINT}-pooler.ap-southeast-1.aws.neon.tech/{neon.DATABASE}?sslmode=require"
    response = {
        "metadata": {"name": neon.REVISION},
        "status": {"imageDigest": neon.IMAGE},
        "spec": {
            "containers": [{"image": neon.IMAGE, "env": [{"name": "DATABASE_URL", "value": raw}]}]
        },
    }
    calls = []

    def read(command, **kwargs):
        calls.append((command, kwargs))
        assert command == [
            "gcloud",
            "run",
            "revisions",
            "describe",
            neon.REVISION,
            "--region",
            neon.REGION,
            "--project",
            neon.PROJECT,
            "--quiet",
            "--verbosity=error",
            "--format=json",
        ]
        assert kwargs["timeout"] == 25 and kwargs["check"] is True
        assert kwargs["env"]["CLOUDSDK_API_ENDPOINT_OVERRIDES_RUN"] == "https://run.googleapis.com/"
        return SimpleNamespace(stdout=json.dumps(response).encode())

    monkeypatch.setattr(neon.subprocess, "run", read)
    return response, calls


def test_fixed_get_issues_binding_without_credential_repr(native):
    _, calls = native
    binding, target = neon.collect_neon_direct_proxy()
    assert len(calls) == 1 and binding.purpose == "capture" and "-pooler" not in binding.host
    assert "synthetic-secret-only" not in repr(binding) + repr(target) + repr(neon._issued)
    assert "postgresql://" not in repr(binding) + repr(target)
    assert not hasattr(binding, "password") and not hasattr(binding, "url")
    assert target._url.query["sslmode"] == "verify-full"
    with pytest.raises(neon.NeonIdentityDenied, match="native_binding_unavailable"):
        neon._registered(replace(binding))


@pytest.mark.parametrize(
    "mutation",
    [
        "revision",
        "image",
        "digest",
        "secret",
        "duplicate",
        "wrong_host",
        "user",
        "database",
        "options",
        "tls",
        "password_query",
        "malformed",
    ],
)
def test_native_drifts_refuse_fixed(native, mutation):
    response, calls = native
    container = response["spec"]["containers"][0]
    if mutation == "revision":
        response["metadata"]["name"] = "other"
    elif mutation == "image":
        container["image"] = "other"
    elif mutation == "digest":
        response["status"]["imageDigest"] = "other"
    elif mutation == "secret":
        container["env"][0] = {
            "name": "DATABASE_URL",
            "valueFrom": {"secretKeyRef": {"name": "private-name", "key": "1"}},
        }
    elif mutation == "duplicate":
        container["env"].append(dict(container["env"][0]))
    else:
        raw = container["env"][0]["value"]
        if mutation == "wrong_host":
            raw = raw.replace(neon.ENDPOINT, "ep-other")
        if mutation == "user":
            raw = raw.replace(neon.ACTOR, "other")
        if mutation == "database":
            raw = raw.replace("/" + neon.DATABASE, "/other")
        if mutation == "options":
            raw += "&options=-c%20role=other"
        if mutation == "tls":
            raw = raw.replace("sslmode=require", "sslmode=disable")
        if mutation == "password_query":
            raw += "&password=query-secret-sentinel"
        if mutation == "malformed":
            raw = "postgresql://user:parse-secret-sentinel@[invalid/db"
        container["env"][0]["value"] = raw
    with pytest.raises(neon.NeonIdentityDenied) as caught:
        neon.collect_neon_direct_proxy()
    assert (
        len(calls) == 1
        and "secret" not in str(caught.value)
        and "postgresql" not in str(caught.value)
    )


def test_receipt_drift_stops_before_get(native):
    _, calls = native
    neon.CONTROL_RECEIPT.write_text("{}")
    with pytest.raises(neon.NeonIdentityDenied, match="control_receipt_mismatch"):
        neon.collect_neon_direct_proxy()
    assert calls == []


@pytest.fixture
def simulated_transport(native, monkeypatch):
    binding, _ = neon.collect_neon_direct_proxy()
    params = {
        "host": binding.host,
        "hostaddr": "203.0.113.10",
        "port": "5432",
        "dbname": neon.DATABASE,
        "user": neon.ACTOR,
        "sslmode": "verify-full",
        "sslcertmode": "disable",
        "sslrootcert": neon.ROOT_CERT,
        "connect_timeout": "5",
        "application_name": neon.APPLICATION,
        "options": binding.options,
    }

    class Info:
        host = binding.host
        hostaddr = "203.0.113.10"
        port = 5432
        dbname = neon.DATABASE
        user = neon.ACTOR

        def get_parameters(self):
            return params

    class Driver:
        closed = False
        pgconn = SimpleNamespace(status=0, ssl_in_use=True)
        info = Info()

    monkeypatch.setattr(neon, "PsycopgConnection", Driver)
    monkeypatch.setattr(neon, "ConnectionInfo", Info)
    driver = Driver()
    neon._guarded_drivers[driver] = binding
    return binding, SimpleNamespace(connection=SimpleNamespace(driver_connection=driver)), params


def test_effective_transport_simulated_positive(simulated_transport):
    binding, connection, _ = simulated_transport
    neon.verify_transport(connection, binding)


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("host", "127.0.0.1"),
        ("port", "5433"),
        ("user", "other"),
        ("dbname", "other"),
        ("sslmode", "require"),
        ("sslrootcert", "other-ca"),
        ("options", "-c role=other"),
        ("hostaddr", "127.0.0.1"),
        ("service", "other"),
        ("sslpassword", "secret-sentinel"),
    ],
)
def test_effective_overrides_refuse(simulated_transport, key, value):
    binding, connection, params = simulated_transport
    params[key] = value
    with pytest.raises(neon.NeonIdentityDenied, match="effective_transport_mismatch"):
        neon.verify_transport(connection, binding)


def test_actual_tls_flag_required(simulated_transport):
    binding, connection, _ = simulated_transport
    connection.connection.driver_connection.pgconn.ssl_in_use = False
    with pytest.raises(neon.NeonIdentityDenied, match="tls_transport_unavailable"):
        neon.verify_transport(connection, binding)


@pytest.mark.parametrize("value", [-(2**31), -1, 0, 2**31 - 1])
def test_cancel_key_is_opaque_signed32(value):
    assert neon.cancellation_key_shape(value)


@pytest.mark.parametrize("value", [True, None, "1", -(2**31) - 1, 2**31])
def test_cancel_shape_refuses_noninteger_or_out_of_range(value):
    assert not neon.cancellation_key_shape(value)


@pytest.mark.parametrize("purpose", ["unknown", "", None])
def test_unknown_purpose_stops_before_native_get(native, purpose):
    _, calls = native
    with pytest.raises(neon.NeonIdentityDenied, match="purpose_unavailable"):
        neon.collect_neon_direct_proxy(purpose=purpose)
    assert calls == []


def test_preparation_purpose_requires_separate_explicit_collection(native):
    _, calls = native
    binding, _ = neon.collect_neon_direct_proxy(purpose="preparation")
    assert len(calls) == 1 and binding.purpose == "preparation"
    assert "default_transaction_read_only" not in binding.options
    assert binding.options == neon.TIMEOUT_OPTIONS


@pytest.mark.parametrize("changed", [0, 1, 2, 3, 4, 5, 6, "control"])
def test_native_target_drift_refuses_fixed(native, monkeypatch, changed):
    binding, _ = neon.collect_neon_direct_proxy()
    monkeypatch.setattr(neon, "CLUSTER", hashlib.sha256(b"synthetic-native-system-id").hexdigest())

    class Result:
        def __init__(self, rows):
            self.rows = rows

        def all(self):
            return self.rows

        def one(self):
            return self.rows[0]

    class SQL:
        def execute(self, query):
            if "SELECT p.oid" in str(query):
                row = (3441, 10, False, "pg_control_system", "internal")
                return Result(
                    [
                        row
                        if changed != "control"
                        else (3441, 10, True, "pg_control_system", "internal")
                    ]
                )
            row = [
                neon.DATABASE_OID,
                neon.ACTOR_OID,
                neon.ACTOR_OID,
                neon.ACTOR,
                neon.ACTOR,
                neon.NAMESPACE_OID,
                "synthetic-native-system-id",
            ]
            if isinstance(changed, int):
                row[changed] = 1 if changed < 6 else "other-system"
            return Result([tuple(row)])

    # The synthetic hash isolates each target mismatch. This does not claim
    # acceptance of the actual retained production target.
    with pytest.raises(
        neon.NeonIdentityDenied, match="native_control_mismatch|native_target_mismatch"
    ):
        neon.verify_native_target(SQL(), binding, neon.DATABASE_OID)


def test_preflight_explicit_binding_is_checked_before_first_sql(native):
    from scripts import monetary_cutover_preflight as preflight

    binding, _ = neon.collect_neon_direct_proxy()
    events = []

    class Connection:
        def __enter__(self):
            events.append("connect")
            return self

        def __exit__(self, *args):
            events.append("close")

        def begin(self):
            raise AssertionError("SQL transaction must not start")

    class Engine:
        dialect = SimpleNamespace(name="postgresql")

        def connect(self):
            return Connection()

    inventory = SimpleNamespace(
        database_namespace=neon.NAMESPACE,
        expected_database_oid=neon.DATABASE_OID,
        expected_system_identifier_sha256=neon.CLUSTER,
        role_env={"retired": "r", "replacement_runtime": "a", "replacement_migration": "b"},
    )
    with pytest.raises(neon.NeonIdentityDenied, match="transport_observation_unavailable"):
        preflight.observe(
            Engine(),
            inventory,
            {"retired": "old", "replacement_runtime": "newr", "replacement_migration": "newm"},
            neon_binding=binding,
        )
    assert events == ["connect", "close"]


@pytest.mark.parametrize(
    "field,replacement",
    [
        ("database_namespace", "other"),
        ("expected_database_oid", 1),
        ("expected_system_identifier_sha256", "0" * 64),
    ],
)
def test_preflight_inventory_pin_mismatch_refuses_before_connect(native, field, replacement):
    from scripts import monetary_cutover_preflight as preflight

    binding, _ = neon.collect_neon_direct_proxy()
    inventory = SimpleNamespace(
        database_namespace=neon.NAMESPACE,
        expected_database_oid=neon.DATABASE_OID,
        expected_system_identifier_sha256=neon.CLUSTER,
    )
    setattr(inventory, field, replacement)

    class Engine:
        def connect(self):
            raise AssertionError("connection must not start")

    with pytest.raises(preflight.Denied, match="neon_direct_inventory_target_mismatch"):
        preflight.observe(Engine(), inventory, {}, neon_binding=binding)


@pytest.fixture
def genuine_conninfo_api(native, monkeypatch):
    """Real psycopg API/native parser semantics; deliberately NOT auth proof."""
    import importlib.util
    import os
    import sys

    from psycopg import ConnectionInfo as ActualConnectionInfo
    from psycopg import pq

    selected = neon
    if source := os.environ.get("HIREWIZ_NEON_PORT_PROBE_SOURCE"):
        spec = importlib.util.spec_from_file_location("retained_port_probe_neon", source)
        selected = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = selected
        spec.loader.exec_module(selected)
        monkeypatch.setattr(selected, "CONTROL_RECEIPT", neon.CONTROL_RECEIPT)
        monkeypatch.setattr(selected, "CONTROL_RECEIPT_SHA", neon.CONTROL_RECEIPT_SHA)
    binding, _ = selected.collect_neon_direct_proxy()

    def transport(parameter_port="5432", effective_port="5432", sslcertmode="disable"):
        raw = (
            f"host={binding.host} hostaddr=203.0.113.10 port={parameter_port} dbname={selected.DATABASE} user={selected.ACTOR} "
            f"sslmode=verify-full sslcertmode={sslcertmode} sslrootcert={selected.ROOT_CERT} connect_timeout=5 "
            f"application_name={selected.APPLICATION} options='{binding.options}' channel_binding=prefer"
        )
        parsed = pq.Conninfo.parse(raw.encode())

        class Metadata:
            _encoding = "utf-8"
            status = 0
            ssl_in_use = True
            info = parsed
            host = binding.host.encode()
            hostaddr = b"203.0.113.10"
            port = effective_port.encode()
            db = selected.DATABASE.encode()
            user = selected.ACTOR.encode()

        info = ActualConnectionInfo(Metadata())

        class Driver:
            closed = False
            pgconn = info.pgconn

        driver = Driver()
        driver.info = info
        monkeypatch.setattr(selected, "PsycopgConnection", Driver)
        return selected, binding, driver

    return transport


def test_real_psycopg_conninfo_method_omits_default_port_and_is_accepted(genuine_conninfo_api):
    selected, binding, driver = genuine_conninfo_api()
    parameters = driver.info.get_parameters()
    assert "port" not in parameters and "channel_binding" not in parameters
    assert driver.info.port == 5432 and driver.info.user == selected.ACTOR
    selected._verify_driver_transport(driver, binding)


def test_present_conflicting_port_is_refused_even_when_actual_port_matches(genuine_conninfo_api):
    selected, binding, driver = genuine_conninfo_api(parameter_port="5433")
    assert driver.info.get_parameters()["port"] == "5433" and driver.info.port == 5432
    with pytest.raises(selected.NeonIdentityDenied, match="effective_transport_mismatch"):
        selected._verify_driver_transport(driver, binding)


def test_actual_port_mismatch_is_refused_even_when_default_port_is_omitted(genuine_conninfo_api):
    selected, binding, driver = genuine_conninfo_api(effective_port="5433")
    assert "port" not in driver.info.get_parameters() and driver.info.port == 5433
    with pytest.raises(selected.NeonIdentityDenied, match="effective_transport_mismatch"):
        selected._verify_driver_transport(driver, binding)


def test_native_get_explicitly_disables_sdk_http_logging(native, monkeypatch):
    """The fake child receives an override, rather than a config-dependent omission."""
    _, calls = native
    monkeypatch.setenv("CLOUDSDK_CORE_LOG_HTTP", "true")
    monkeypatch.setenv("CLOUDSDK_CORE_LOG_HTTP_SHOW_REQUEST_BODY", "true")
    monkeypatch.setenv("CLOUDSDK_CORE_LOG_HTTP_STREAMING_BODY", "true")
    neon.collect_neon_direct_proxy()
    assert len(calls) == 1
    actual_logging = calls[0][1]["env"].get("CLOUDSDK_CORE_LOG_HTTP")
    assert actual_logging == "false"


def test_factory_disables_client_certificate_loading(native):
    """Explicit conninfo pins the client certificate policy before connection."""
    from sqlalchemy.dialects.postgresql.psycopg import PGDialect_psycopg

    _, target = neon.collect_neon_direct_proxy()
    assert target._url.query["sslcertmode"] == "disable"
    _, parameters = PGDialect_psycopg().create_connect_args(target._url)
    assert parameters["sslcertmode"] == "disable"


def test_actual_conninfo_api_reports_explicit_disable_and_guard_accepts(genuine_conninfo_api):
    """Real parser/default filtering only, not an authenticated TLS connection."""
    selected, binding, driver = genuine_conninfo_api(sslcertmode="disable")
    assert driver.info.get_parameters()["sslcertmode"] == "disable"
    selected._verify_driver_transport(driver, binding)


def test_libpq_null_compiled_default_exposes_derived_allow_and_guard_refuses(genuine_conninfo_api):
    """Primary libpq source derives allow; actual psycopg won't filter that value."""
    from psycopg import pq

    defaults = {item.keyword: item.compiled for item in pq.Conninfo.get_defaults()}
    assert defaults[b"sslcertmode"] is None
    selected, binding, driver = genuine_conninfo_api(sslcertmode="allow")
    assert driver.info.get_parameters()["sslcertmode"] == "allow"
    with pytest.raises(selected.NeonIdentityDenied, match="effective_transport_mismatch"):
        selected._verify_driver_transport(driver, binding)


@pytest.mark.parametrize("mode", ["allow", "require", "prefer", "", None, "MISSING"])
def test_missing_or_conflicting_client_certificate_policy_refuses(simulated_transport, mode):
    binding, connection, params = simulated_transport
    if mode == "MISSING":
        params.pop("sslcertmode")
    else:
        params["sslcertmode"] = mode
    with pytest.raises(neon.NeonIdentityDenied, match="effective_transport_mismatch"):
        neon.verify_transport(connection, binding)


@pytest.mark.parametrize("address", ["203.0.113.10", "2001:db8::10"])
def test_single_driver_resolved_address_retains_hostname_and_tls(simulated_transport, address):
    binding, connection, params = simulated_transport
    params["hostaddr"] = address
    connection.connection.driver_connection.info.hostaddr = address
    neon.verify_transport(connection, binding)
    assert params["host"] == binding.host and params["sslmode"] == "verify-full"


@pytest.mark.parametrize(
    "address",
    [None, "", "MISSING", "other.example", "203.0.113.10,203.0.113.11", " 203.0.113.10", "fe80::1%en0", "0" * 46, 2130706433],
)
def test_invalid_effective_address_refuses(simulated_transport, address):
    binding, connection, params = simulated_transport
    if address == "MISSING":
        params.pop("hostaddr")
    else:
        params["hostaddr"] = address
    with pytest.raises(neon.NeonIdentityDenied, match="effective_transport_mismatch"):
        neon.verify_transport(connection, binding)


@pytest.mark.parametrize("actual", [None, "", "203.0.113.11", "other.example", "fe80::1%en0"])
def test_effective_address_requires_matching_genuine_driver_address(simulated_transport, actual):
    binding, connection, _ = simulated_transport
    connection.connection.driver_connection.info.hostaddr = actual
    with pytest.raises(neon.NeonIdentityDenied, match="effective_transport_mismatch"):
        neon.verify_transport(connection, binding)


@pytest.mark.parametrize("field", ["sslcert", "sslkey", "sslnegotiation", "ssl_min_protocol_version", "unexpected"])
def test_other_unknown_effective_fields_still_refuse(simulated_transport, field):
    binding, connection, params = simulated_transport
    params[field] = "synthetic-only"
    with pytest.raises(neon.NeonIdentityDenied, match="effective_transport_mismatch"):
        neon.verify_transport(connection, binding)


@pytest.mark.parametrize("field", ["hostaddr", "service", "POSITIONAL"])
def test_controlled_connect_refuses_injected_address_before_driver_call(native, monkeypatch, field):
    from sqlalchemy.dialects.postgresql.psycopg import PGDialect_psycopg

    _, target = neon.collect_neon_direct_proxy()
    hooks = []
    original_listen = neon.event.listen

    def listen(target, name, fn, **kwargs):
        if name == "do_connect":
            hooks.append(fn)
        else:
            original_listen(target, name, fn, **kwargs)

    monkeypatch.setattr(neon.event, "listen", listen)
    engine = target.engine()
    args, params = PGDialect_psycopg().create_connect_args(target._url)
    params.update(connect_timeout=5, application_name=neon.APPLICATION, options=target.binding.options)
    if field == "POSITIONAL":
        args = ["caller-conninfo"]
    else:
        params[field] = "synthetic-only"
    calls = []

    class Dialect:
        def connect(self, *args, **kwargs):
            calls.append(1)
            raise AssertionError("driver must not be called")

    try:
        with pytest.raises(neon.NeonIdentityDenied, match="connection_settings_unavailable"):
            hooks[0](Dialect(), None, args, params)
        assert calls == []
    finally:
        engine.dispose()


def test_factory_still_refuses_hostaddr_in_native_uri(native):
    response, _ = native
    env = response["spec"]["containers"][0]["env"][0]
    env["value"] += "&hostaddr=203.0.113.10"
    with pytest.raises(neon.NeonIdentityDenied, match="connection_settings_unavailable"):
        neon.collect_neon_direct_proxy()


def test_binding_cannot_authorize_unrelated_driver(simulated_transport):
    binding, connection, _ = simulated_transport
    driver = connection.connection.driver_connection
    neon._guarded_drivers.pop(driver)
    with pytest.raises(neon.NeonIdentityDenied, match="guarded_driver_unavailable"):
        neon.verify_transport(connection, binding)


def test_guarded_driver_origin_requires_exact_binding(simulated_transport, native):
    binding, connection, _ = simulated_transport
    other, _ = neon.collect_neon_direct_proxy()
    neon._guarded_drivers[connection.connection.driver_connection] = other
    with pytest.raises(neon.NeonIdentityDenied, match="guarded_driver_unavailable"):
        neon.verify_transport(connection, binding)


def test_actual_connection_class_supports_weak_registry_without_connecting():
    """Uninitialized genuine object proves weak-key API shape, not authentication."""
    from weakref import WeakKeyDictionary, ref

    from psycopg import Connection

    driver = Connection.__new__(Connection)
    assert ref(driver)() is driver
    registry = WeakKeyDictionary()
    marker = object()
    registry[driver] = marker
    assert registry.pop(driver) is marker
    assert not hasattr(driver, "pgconn")


@pytest.mark.parametrize("field", ["PGHOSTADDR", "PGSERVICE", "PGHOST", "PGSSLCERTMODE"])
def test_controlled_connect_refuses_ambient_overrides_before_driver_call(native, monkeypatch, field):
    from sqlalchemy.dialects.postgresql.psycopg import PGDialect_psycopg

    _, target = neon.collect_neon_direct_proxy()
    hooks = []
    original_listen = neon.event.listen

    def listen(target, name, fn, **kwargs):
        if name == "do_connect":
            hooks.append(fn)
        else:
            original_listen(target, name, fn, **kwargs)

    monkeypatch.setattr(neon.event, "listen", listen)
    engine = target.engine()
    args, params = PGDialect_psycopg().create_connect_args(target._url)
    params.update(connect_timeout=5, application_name=neon.APPLICATION, options=target.binding.options)
    monkeypatch.setenv(field, "synthetic-only")
    calls = []

    class Dialect:
        def connect(self, *args, **kwargs):
            calls.append(1)
            raise AssertionError("driver must not be called")

    try:
        with pytest.raises(neon.NeonIdentityDenied, match="connection_settings_unavailable"):
            hooks[0](Dialect(), None, args, params)
        assert calls == []
    finally:
        engine.dispose()


@pytest.mark.parametrize("accept", [True, False])
def test_guarded_hook_registers_only_after_effective_proof(simulated_transport, monkeypatch, accept):
    from sqlalchemy.dialects.postgresql.psycopg import PGDialect_psycopg

    binding, connection, _ = simulated_transport
    driver = connection.connection.driver_connection
    neon._guarded_drivers.pop(driver)
    events = []
    driver.close = lambda: events.append("close")
    hooks = []
    original_listen = neon.event.listen

    def listen(target, name, fn, **kwargs):
        if name == "do_connect":
            hooks.append(fn)
        else:
            original_listen(target, name, fn, **kwargs)

    monkeypatch.setattr(neon.event, "listen", listen)
    # The clean conninfo here is synthetic. No native factory or connection runs.
    from sqlalchemy.engine import URL

    target = neon._TransientTarget(
        binding,
        URL.create("postgresql+psycopg", username=neon.ACTOR, password="synthetic-only", host=binding.host, port=5432, database=neon.DATABASE, query={"sslmode": "verify-full", "sslcertmode": "disable", "sslrootcert": neon.ROOT_CERT, "channel_binding": binding.channel_binding}),
    )
    engine = target.engine()
    args, params = PGDialect_psycopg().create_connect_args(target._url)
    params.update(connect_timeout=5, application_name=neon.APPLICATION, options=binding.options)

    class Dialect:
        def connect(self, *args, **kwargs):
            events.append("connect")
            return driver

    if not accept:
        connection.connection.driver_connection.pgconn.ssl_in_use = False
    try:
        if accept:
            assert hooks[0](Dialect(), None, args, params) is driver
            assert neon._guarded_drivers.get(driver) is binding
            neon.verify_transport(connection, binding)
            assert events == ["connect"]
        else:
            with pytest.raises(neon.NeonIdentityDenied, match="connection_transport_refused"):
                hooks[0](Dialect(), None, args, params)
            assert neon._guarded_drivers.get(driver) is None
            assert events == ["connect", "close"]
    finally:
        engine.dispose()
