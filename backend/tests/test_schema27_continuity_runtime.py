"""The continuity credential can only verify the exact serving target."""
from dataclasses import replace
from types import SimpleNamespace
from urllib.parse import urlencode

import pytest
from infra.gcp import schema27_continuity_runtime as runtime
from infra.gcp import schema27_continuity_scope as scope

from scripts import neon_continuity_identity as neon


@pytest.fixture
def scope_journal(tmp_path):
    custody = scope.BridgeCustody("a" * 32, "b" * 64, scope.ContinuityDatabasePin("c" * 64))
    folder = tmp_path.resolve() / "continuity"
    folder.mkdir(mode=0o700)
    journal = scope.rolling.Journal(folder, custody)
    journal.record(scope.JOURNAL_BINDING, custody.journal_binding())
    return custody, journal


def make_reader(*, rewrite=lambda url: url, drift=False):
    calls = []
    name, version = scope.RUNTIME_REFERENCE.split(":")
    metadata = {"name": f"projects/{runtime.PROJECT_NUMBER}/secrets/{name}/versions/{version}",
                "etag": "owned-metadata", "state": "ENABLED"}
    value = f"postgresql://{neon.RUNTIME}:synthetic@{neon.HOST}:5432/{neon.DATABASE}?{urlencode(runtime.PRODUCTION_TLS)}"

    def describe(reference):
        calls.append(("describe", reference))
        return metadata | {"etag": "changed"} if drift and len(calls) > 1 else metadata

    def access(reference):
        calls.append(("access", reference))
        return rewrite(value)

    return SimpleNamespace(describe_release_secret=describe, access_release_secret=access), calls


def test_exact_runtime_reference_is_verified_on_both_sides_of_access(scope_journal, monkeypatch):
    reader, calls = make_reader()

    def read_bound_role(purpose, *, runtime_password):
        assert purpose is neon.Purpose.BEFORE_SCHEMA27
        assert runtime_password == "synthetic"
        assert [name for name, _ in calls] == ["describe", "access", "describe"]
        assert all(reference == scope.RUNTIME_REFERENCE for _, reference in calls)
        return neon.RUNTIME_OID

    monkeypatch.setattr(neon, "observe_runtime", read_bound_role)
    assert runtime.observe_runtime(*scope_journal, neon.Purpose.BEFORE_SCHEMA27, reader) == neon.RUNTIME_OID


def test_wrong_scope_runtime_ref_and_transport_drift_refuse_before_connect(scope_journal, monkeypatch):
    monkeypatch.setattr(neon, "observe_runtime", lambda *a, **k: pytest.fail("invalid target connection"))
    custody, journal = scope_journal
    reader, calls = make_reader()
    wrong = replace(custody, database=replace(custody.database,
                    runtime_reference="hirewiz-fresh-runtime-db-20261013:2"))
    with pytest.raises(scope.rolling.RollingDenied, match="database_pin_changed"):
        runtime.observe_runtime(wrong, journal, neon.Purpose.BEFORE_SCHEMA27, reader)
    assert not calls
    for candidate, reason in (
        (make_reader(drift=True)[0], "runtime_source_changed"),
        (make_reader(rewrite=lambda url: url.replace("verify-full", "require"))[0], "runtime_target_mismatch"),
    ):
        with pytest.raises(scope.rolling.RollingDenied, match=reason):
            runtime.observe_runtime(custody, journal, neon.Purpose.BEFORE_SCHEMA27, candidate)
