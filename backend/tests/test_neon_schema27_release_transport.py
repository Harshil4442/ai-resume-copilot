"""Closed27 source/transport controls; synthetic ports, no provider acceptance."""
from dataclasses import replace
from hashlib import sha256
from types import SimpleNamespace

import pytest
from test_neon_linux_execution import package as linux_package  # noqa: F401
from test_neon_schema19_release_transport import Reader, Session
from test_neon_schema19_release_transport import fresh_receipt as fresh_receipt  # noqa: F401

from scripts import neon_direct_identity as neon
from scripts import neon_execution_files as files


def collect(reader=None, **changes):
    reader = reader or Reader()
    kwargs = {"alias": reader.alias, "database_owner_oid": neon.FRESH_DATABASE_OWNER_OID, **changes}
    return neon.collect_schema27_release_target(reader, reader.reference, **kwargs)


@pytest.mark.parametrize("alias", tuple(neon.FRESH_RELEASE_ACTORS))
def test27_exact_numeric_secret_issues_separate_readonly_binding(alias):
    reader = Reader(alias)
    binding, target = collect(reader)
    assert binding.purpose == "schema27_fresh_release_capture"
    assert neon.bound_schema(binding) == "20261105_0027"
    assert reader.calls == ["describe", "access", "describe"]
    assert binding.options == neon.CAPTURE_OPTIONS
    assert (binding.actor, binding.actor_oid) == neon.FRESH_RELEASE_ACTORS[alias]
    assert target._url.host == neon.FRESH_DIRECT_HOST
    assert target._url.query == {"sslmode": "verify-full", "sslcertmode": "disable",
        "sslrootcert": neon.ROOT_CERT, "channel_binding": "require"}
    assert target.dump_environment()["PGOPTIONS"] == neon.CAPTURE_OPTIONS
    assert "synthetic-fixture-only" not in repr(binding) + repr(target)
    for forged in (replace(binding), replace(binding, purpose="schema26_fresh_release_capture"),
                   replace(binding, expected_schema=neon.SCHEMA26_RELEASE_SCHEMA)):
        with pytest.raises(neon.NeonIdentityDenied, match="native_binding_unavailable"):
            neon.bound_schema(forged)


@pytest.mark.parametrize("schema", [None, True, [], "latest", "20261031_0026", "20261106_0028"])
def test27_wrong_schema_refuses_before_reading_any_secret(schema):
    reader = Reader()
    with pytest.raises(neon.NeonIdentityDenied, match="identity_input_invalid"):
        collect(reader, expected_schema=schema)
    assert reader.calls == []


def test_every_previous_purpose_keeps_its_original_schema_and_refuses27():
    assert neon.RELEASE_SCHEMAS == frozenset({"20261008_0009", "20261009_0010", "20261009_0011",
        "20261009_0012", "20261009_0013", "20261010_0014", "20261011_0015", "20261011_0016",
        "20261014_0017", "20261015_0018"})
    old = [(neon.collect_fresh_release_target, "fresh_release_capture", "20261015_0018"),
           (neon.collect_schema19_release_target, "schema19_fresh_release_capture", "20261015_0019"),
           (neon.collect_schema20_release_target, "schema20_fresh_release_capture", "20261023_0020"),
           (neon.collect_schema21_release_target, "schema21_fresh_release_capture", "20261023_0021"),
           (neon.collect_schema25_release_target, "schema25_fresh_release_capture", "20261028_0025"),
           (neon.collect_schema26_release_target, "schema26_fresh_release_capture", "20261031_0026")]
    for factory, purpose, schema in old:
        reader = Reader()
        binding, _ = factory(reader, reader.reference, alias=reader.alias,
            database_owner_oid=neon.FRESH_DATABASE_OWNER_OID, expected_schema=schema)
        assert binding.purpose == purpose and neon.bound_schema(binding) == schema
        denied = Reader()
        with pytest.raises(neon.NeonIdentityDenied, match="identity_input_invalid"):
            factory(denied, denied.reference, alias=denied.alias,
                database_owner_oid=neon.FRESH_DATABASE_OWNER_OID, expected_schema=neon.SCHEMA27_RELEASE_SCHEMA)
        assert denied.calls == []
    for factory in (neon.collect_release_target, neon.collect_source_release_target):
        denied = Reader()
        with pytest.raises(neon.NeonIdentityDenied, match="identity_input_invalid"):
            factory(denied, denied.reference, alias=denied.alias,
                database_owner_oid=neon.FRESH_DATABASE_OWNER_OID, expected_schema=neon.SCHEMA27_RELEASE_SCHEMA)
        assert denied.calls == []


@pytest.mark.parametrize("change", ["disabled", "name", "etag", "host", "pooler", "actor", "options", "owner"])
def test27_preserves_secret_source_endpoint_actor_owner_and_url_refusals(change):
    reader, kwargs = Reader(), {}
    if change == "disabled":
        reader.metadata["state"] = "DISABLED"
    elif change == "name":
        reader.metadata["name"] = reader.metadata["name"].rsplit("/", 1)[0] + "/latest"
    elif change == "etag":
        reader.after = {**reader.metadata, "etag": "changed"}
    elif change == "host":
        reader.value = reader.value.replace(neon.FRESH_DIRECT_HOST, neon.RELEASE_DIRECT_HOST)
    elif change == "pooler":
        reader.value = reader.value.replace(neon.FRESH_ENDPOINT, neon.FRESH_ENDPOINT + "-pooler")
    elif change == "actor":
        reader.value = reader.value.replace(neon.FRESH_RELEASE_ACTORS["observer"][0], "wrong_role")
    elif change == "options":
        reader.value += "&options=-csearch_path=other"
    else:
        kwargs["database_owner_oid"] = neon.FRESH_DATABASE_OWNER_OID + 1
    with pytest.raises(neon.NeonIdentityDenied) as error:
        collect(reader, **kwargs)
    assert "synthetic-fixture-only" not in str(error.value)
    assert len(reader.calls) <= 3
    if change == "owner":
        assert reader.calls == []


def test27_native_catalog_guard_and26_schema_cannot_be_relabelled(monkeypatch):
    binding, _ = collect()
    monkeypatch.setattr(neon, "FRESH_CLUSTER", sha256(b"schema19-synthetic-native-cluster").hexdigest())
    session = Session(binding)
    neon.verify_native_target(session, binding, neon.FRESH_DATABASE_OID)
    assert len(session.queries) == 3
    for changed in ("control", "database", "owner", "actor", "namespace_owner", "cluster", "schema"):
        with pytest.raises(neon.NeonIdentityDenied):
            neon.verify_native_target(Session(binding, changed), binding, neon.FRESH_DATABASE_OID)
    old, _ = neon.collect_schema26_release_target(Reader(), "schema19-inert-secret:2", alias="observer",
        database_owner_oid=neon.FRESH_DATABASE_OWNER_OID)
    with pytest.raises(neon.NeonIdentityDenied, match="exact_schema_mismatch"):
        neon.verify_release_schema(Session(binding), old)
    with pytest.raises(neon.NeonIdentityDenied, match="exact_schema_mismatch"):
        neon.verify_release_schema(Session(old), binding)


def test27_engine_requires_registered_genuine_driver_before_any_sql():
    binding, destination = collect()
    engine = destination.engine()
    try:
        class UnauthenticatedDriver:
            pass
        fake = SimpleNamespace(connection=SimpleNamespace(driver_connection=UnauthenticatedDriver()))
        with pytest.raises(neon.NeonIdentityDenied, match="guarded_driver_unavailable"):
            neon.verify_transport(fake, binding)
        listener = next(iter(engine.dialect.dispatch.do_connect))
        with pytest.raises(neon.NeonIdentityDenied, match="connection_settings_unavailable"):
            listener(SimpleNamespace(), None, [], {"hostaddr": "203.0.113.8"})
    finally:
        engine.dispose()


@pytest.mark.parametrize("alias", tuple(files.SECRET_NAMES))
def test27_linux_fixed_ca_and_numeric_reference_proof_remain_required(request, alias):
    execution, _, _ = request.getfixturevalue("linux_package")
    reader = Reader(alias, files.SECRET_NAMES[alias] + ":2", execution)
    binding, target = collect(reader)
    assert binding.execution_files is execution
    assert neon.bound_schema(binding) == neon.SCHEMA27_RELEASE_SCHEMA
    assert target._url.query["sslrootcert"] == execution.root_certificate
    assert target.dump_environment()["PGSSLROOTCERT"] == execution.root_certificate
    wrong = Reader(alias, "schema19-inert-secret:2", execution)
    with pytest.raises(neon.NeonIdentityDenied):
        collect(wrong)
    assert wrong.calls == []
