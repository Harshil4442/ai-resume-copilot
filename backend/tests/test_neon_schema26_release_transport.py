"""Disjoint26 issuer controls with inert sources; no provider/native acceptance."""
from dataclasses import replace
from hashlib import sha256

import pytest
from test_neon_schema19_release_transport import Reader, Session
from test_neon_schema19_release_transport import fresh_receipt as fresh_receipt  # noqa: F401

from scripts import neon_direct_identity as neon


def collect(reader=None, **changes):
    reader = reader or Reader()
    return neon.collect_schema26_release_target(reader, reader.reference, alias=reader.alias,
        database_owner_oid=neon.FRESH_DATABASE_OWNER_OID, **changes)


@pytest.mark.parametrize("alias", tuple(neon.FRESH_RELEASE_ACTORS))
def test_exact26_uses_original_numeric_secret_transport_without_relabeling25(alias):
    reader = Reader(alias)
    binding, target = collect(reader)
    assert binding.purpose == "schema26_fresh_release_capture"
    assert neon.bound_schema(binding) == "20261031_0026"
    assert binding.options == neon.CAPTURE_OPTIONS
    assert reader.calls == ["describe", "access", "describe"]
    assert target._url.host == neon.FRESH_DIRECT_HOST
    assert target._url.query["sslmode"] == "verify-full"
    assert target.dump_environment()["PGOPTIONS"] == neon.CAPTURE_OPTIONS
    assert "synthetic-fixture-only" not in repr(binding) + repr(target)
    for forged in (replace(binding), replace(binding, purpose="schema25_fresh_release_capture"),
                   replace(binding, expected_schema="20261028_0025")):
        with pytest.raises(neon.NeonIdentityDenied, match="native_binding_unavailable"):
            neon.bound_schema(forged)


@pytest.mark.parametrize("schema", [None, True, [], "20261028_0025", "latest", "20261031_0027"])
def test26_refuses_every_other_schema_before_secret_reads(schema):
    reader = Reader()
    with pytest.raises(neon.NeonIdentityDenied, match="identity_input_invalid"):
        collect(reader, expected_schema=schema)
    assert reader.calls == []


def test25_and_older_collectors_do_not_gain26_authority():
    assert neon.SCHEMA26_RELEASE_SCHEMA not in neon.RELEASE_SCHEMAS
    for factory in (neon.collect_fresh_release_target, neon.collect_schema19_release_target,
                    neon.collect_schema20_release_target, neon.collect_schema21_release_target,
                    neon.collect_schema25_release_target):
        reader = Reader()
        with pytest.raises(neon.NeonIdentityDenied, match="identity_input_invalid"):
            factory(reader, reader.reference, alias=reader.alias, database_owner_oid=neon.FRESH_DATABASE_OWNER_OID,
                expected_schema=neon.SCHEMA26_RELEASE_SCHEMA)
        assert reader.calls == []


def test26_native_identity_keeps_original_target_guards_and_refuses25_catalog(monkeypatch):
    binding, _ = collect()
    monkeypatch.setattr(neon, "FRESH_CLUSTER", sha256(b"schema19-synthetic-native-cluster").hexdigest())
    session = Session(binding)
    neon.verify_native_target(session, binding, neon.FRESH_DATABASE_OID)
    assert len(session.queries) == 3
    for changed in ("control", "database", "owner", "actor", "namespace_owner", "cluster", "schema"):
        with pytest.raises(neon.NeonIdentityDenied):
            neon.verify_native_target(Session(binding, changed), binding, neon.FRESH_DATABASE_OID)
    old, _ = neon.collect_schema25_release_target(Reader(), "schema19-inert-secret:2", alias="observer",
        database_owner_oid=neon.FRESH_DATABASE_OWNER_OID)
    with pytest.raises(neon.NeonIdentityDenied, match="exact_schema_mismatch"):
        neon.verify_release_schema(Session(binding), old)
    with pytest.raises(neon.NeonIdentityDenied, match="exact_schema_mismatch"):
        neon.verify_release_schema(Session(old), binding)


@pytest.mark.parametrize("change", ["disabled", "etag", "host", "actor", "options"])
def test26_retains_original_source_and_credential_shape_refusals(change):
    reader = Reader()
    if change == "disabled":
        reader.metadata["state"] = "DISABLED"
    elif change == "etag":
        reader.after = {**reader.metadata, "etag": "other-inert-etag"}
    elif change == "host":
        reader.value = reader.value.replace(neon.FRESH_DIRECT_HOST, neon.RELEASE_DIRECT_HOST)
    elif change == "actor":
        reader.value = reader.value.replace(neon.FRESH_RELEASE_ACTORS["observer"][0], "other_role")
    else:
        reader.value += "&options=-csearch_path=other"
    with pytest.raises(neon.NeonIdentityDenied) as error:
        collect(reader)
    assert "synthetic-fixture-only" not in str(error.value)
