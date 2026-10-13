"""Synthetic native-response composition; no SDK, server or application import."""

from __future__ import annotations

import hashlib
import importlib
import os
import sys
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(os.environ.get("HIREWIZ_FENCE_TEST_ROOT", str(Path(__file__).resolve().parents[2])))
sys.path[:0] = [str(Path(__file__).parent), str(ROOT / "infra/gcp"), str(ROOT / "backend")]
module = importlib.import_module("native_fresh_fence")
release = module.release
OLD = module.NativeKey("projects/157225724590/locations/global/keys/old-key", "a" * 32, None)
NEW = module.NativeKey("projects/157225724590/locations/global/keys/new-key", "b" * 32, None)
EMAIL = "157225724590-compute@developer.gserviceaccount.com"
UID = "115689643755778240830"
PAST = "2026-01-01T00:00:00Z"


class Responses:
    def __init__(self):
        self.calls = []
        self.active = False
        self.enabled = False
        self.bad_uid = False
        self.partial = False
        self.unavailable = False
        self.tag = False
        self.created = datetime.now(UTC)

    def get(self, api, path, params):
        self.calls.append((api, path, params))
        if self.unavailable:
            raise release.ReleaseDenied("fence_native_read_unavailable")
        if api == "keys":
            if path == "keys:lookupKey":
                return {"name": NEW.name, "parent": NEW.name.rsplit("/keys/", 1)[0]}
            key = OLD if path == OLD.name else NEW
            row = {"name": key.name, "uid": key.uid, "etag": "native-etag",
                   "createTime": self.created.isoformat()}
            if key == OLD and not self.active:
                row["deleteTime"] = PAST
            return row
        if api == "iam":
            return {"email": EMAIL, "uniqueId": UID + "9" if self.bad_uid else UID,
                    "disabled": not self.enabled}
        if api == "secret":
            return {"name": path, "state": "ENABLED", "etag": "native-etag",
                    "createTime": self.created.isoformat()}
        if api == "tasks":
            if path.endswith("/tasks"):
                return {"tasks": [], "nextPageToken": "partial"} if self.partial else {}
            return {"name": path, "state": "PAUSED"}
        if api == "scheduler":
            return {}
        if path.endswith("/executions"):
            return {}
        if "/revisions/" in path:
            return {"name": path, "containers": [{"image": "irrelevant"}],
                    "serviceAccount": "unmapped@gcp.example.gserviceaccount.com"}
        return {"name": path, "uid": "service-uid", "template": {
            "containers": [{"image": "irrelevant"}], "serviceAccount": EMAIL},
            "trafficStatuses": [{"tag": "zero-percent", "percent": 0, "revision": "tagged-revision"}]
            if self.tag else []}


@pytest.fixture
def prepared(tmp_path, monkeypatch):
    source = release.Plan(
        "a" * 40, "sha256:" + "b" * 64,
        "11111111-1111-1111-1111-111111111111", 7,
        "inert-old-runtime:2", "inert-old-migration:3", "inert-old-observer:4", 16387,
        "inert-expense-policy:5", "c" * 64, "inert-model-policy:6", "d" * 64,
        "inert-model-key:7", "gemini-inert-model", release.MODEL_API_BASE,
        f"inert-runtime@{release.PROJECT}.iam.gserviceaccount.com",
        f"inert-migration@{release.PROJECT}.iam.gserviceaccount.com",
        f"gs://{release.PROJECT}-hirewiz-application-artifacts/releases/inert.dump",
        tmp_path / "inert.json", "e" * 64,
    )
    target = release.FreshTargetPlan(*(ref.rsplit(":", 1)[0] + ":2" for ref in module.SETUP_REFERENCES.values()))
    responses = Responses()
    boundary = module.NativeFreshFenceBoundary(ROOT, target, responses)
    boundary.scope = module.BoundScope(
        source, target, ((module.PARENT + "/services/ai-resume-parser", "service-uid"),),
        (OLD,), None, (module.NativePrincipal(EMAIL, UID),), ("legacy_owner",),
    )
    observed = []
    monkeypatch.setattr(module.fresh.FreshNativeBoundary, "verify_database",
                        lambda self, plan, schema: observed.append((plan, schema)))
    return boundary, source, target, responses, observed


def ready(prepared):
    boundary, source, target, responses, _ = prepared
    cut = boundary.establish_retirement_cut(source)
    responses.created = cut + timedelta(microseconds=1)
    boundary.scope = replace(boundary.scope, new_key=NEW)
    boundary._authenticated_replacements = (target.runtime_secret, target.migration_secret, target.observer_secret)
    boundary._verify_issuance_after_isolation(
        boundary.scope, boundary._module("backend/scripts/model_key_revocation_observer.py", "inert_keys")
    )
    return boundary, source, target, responses


def test_positive_native_composition_and_stable_cut(prepared):
    boundary, source, _, responses = ready(prepared)
    cut = boundary.retired_since
    first, second = boundary.verify_fences(source), boundary.verify_fences(source)
    assert first.retired_since == second.retired_since == cut
    assert cut <= first.observed_at <= second.observed_at <= datetime.now(UTC)
    assert first.complete_external_consumers and first.old_provider_credentials_revoked
    assert first.old_cloud_writers_retired
    assert prepared[-1] and all(schema == release.CUTOVER_CHAIN[0] for _, schema in prepared[-1])
    assert not any(api in {"asset", "project"} for api, _, _ in responses.calls)
    assert not any("showDeleted" in params for _, _, params in responses.calls)


@pytest.mark.parametrize(("field", "reason"), [
    ("active", "fence_old_provider_key_active_or_replacement_retired"),
    ("enabled", "fence_old_automated_principal_still_enabled"),
    ("bad_uid", "fence_native_principal_uid_drift"),
    ("unavailable", "fence_native_read_unavailable"),
])
def test_actual_authority_drift_refuses_and_invalidates_cut(prepared, field, reason):
    boundary, source, _, responses = ready(prepared)
    setattr(responses, field, True)
    with pytest.raises(release.ReleaseDenied, match=reason):
        boundary.verify_fences(source)
    assert boundary.retired_since is None


def test_replacement_issuance_before_isolation_refuses(prepared):
    boundary, source, _, responses, _ = prepared
    boundary.establish_retirement_cut(source)
    responses.created = boundary.retired_since - timedelta(microseconds=1)
    boundary.scope = replace(boundary.scope, new_key=NEW)
    with pytest.raises(release.ReleaseDenied, match="fence_replacement_credential_issued_before_native_isolation"):
        boundary._verify_issuance_after_isolation(
            boundary.scope, boundary._module("backend/scripts/model_key_revocation_observer.py", "inert_keys")
        )


def test_initial_closure_is_not_itself_a_fence(prepared):
    boundary, source, *_ = prepared
    with pytest.raises(release.ReleaseDenied, match="fence_native_retirement_cut_not_established"):
        boundary.verify_fences(source)
    cut = boundary.establish_retirement_cut(source)
    assert type(cut) is datetime
    with pytest.raises(release.ReleaseDenied, match="fence_replacement_native_binding_unavailable"):
        boundary.verify_fences(source)


def test_source_sql_failure_is_not_replaced_by_cloud_metadata(prepared, monkeypatch):
    boundary, source, *_ = ready(prepared)
    def refusal(*_):
        raise release.ReleaseDenied("inert_source_sql_writer_present")
    monkeypatch.setattr(module.fresh.FreshNativeBoundary, "verify_database", refusal)
    with pytest.raises(release.ReleaseDenied, match="inert_source_sql_writer_present"):
        boundary.verify_fences(source)
    assert boundary.retired_since is None


def test_oldest_observation_expires_instead_of_final_stamp_refresh(prepared, monkeypatch):
    boundary, source, *_ = ready(prepared)
    stamp = [1.0]
    monkeypatch.setattr(module.time, "monotonic", lambda: stamp[0])
    monkeypatch.setattr(module.fresh.FreshNativeBoundary, "verify_database",
                        lambda *_: stamp.__setitem__(0, 32.0))
    with pytest.raises(release.ReleaseDenied, match="fence_native_observation_expired"):
        boundary.verify_fences(source)
    assert boundary.retired_since is None


def test_tagged_zero_traffic_revision_is_checked(prepared):
    boundary, source, _, responses = ready(prepared)
    responses.tag = True
    with pytest.raises(release.ReleaseDenied, match="fence_new_reachable_principal_unmapped"):
        boundary.verify_fences(source)
    assert any("/revisions/tagged-revision" in path for _, path, _ in responses.calls)


def test_partial_queue_and_source_plan_drift_refuse(prepared):
    boundary, source, _, responses = ready(prepared)
    responses.partial = True
    with pytest.raises(release.ReleaseDenied, match="fence_queue_not_paused_and_drained"):
        boundary.verify_fences(source)
    boundary.retired_since = datetime.now(UTC) - timedelta(seconds=1)
    with pytest.raises(release.ReleaseDenied, match="fence_native_scope_not_bound_or_plan_drift"):
        boundary.verify_fences(replace(source, release="f" * 40))


def test_positive_fresh_fence_never_authorizes_legacy_migration(prepared):
    boundary, source, *_ = ready(prepared)
    boundary.verify_fences(source)
    with pytest.raises(release.ReleaseDenied, match="fresh_native_fence_cannot_authorize_inplace_migration"):
        boundary.migrate(source, release.CUTOVER_CHAIN[0], release.CUTOVER_CHAIN[1])


def test_sdk_route_is_readonly_and_vendor_failure_is_sanitized(monkeypatch):
    reads = module.SDKGoogleReads(Path("/inert/public-sdk"), "inert@example.com")
    class HTTP:
        def request(self, *args, **kwargs):
            raise RuntimeError("synthetic-private-value-must-not-escape")
    monkeypatch.setattr(reads, "_http", lambda: HTTP())
    with pytest.raises(release.ReleaseDenied) as error:
        reads.get("keys", OLD.name, {})
    assert str(error.value) == "fence_native_read_unavailable"
    with pytest.raises(release.ReleaseDenied, match="fence_native_route_refused"):
        reads.get("keys", OLD.name + ":delete", {})


class Result:
    def __init__(self, value):
        self.value = value

    def all(self):
        return self.value

    def one(self):
        return self.value


class Connection:
    def __init__(self, binding, *, identity_bad=False, sessions=0):
        self.binding, self.identity_bad, self.sessions = binding, identity_bad, sessions
        self.commands = []
        self.reads = 0

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return None

    def begin(self):
        return self

    def exec_driver_sql(self, sql):
        self.commands.append(sql)

    def execute(self, sql):
        self.reads += 1
        if self.reads == 1:
            assert "pg_catalog.pg_proc" in sql
            return Result([(3441, 10, False, "pg_control_system", "internal")])
        if self.reads == 2:
            assert "pg_catalog.pg_control_system()" in sql
            return Result((16395 if not self.identity_bad else 16396, 16391,
                self.binding.actor_oid, self.binding.actor, self.binding.actor, 2200, 6171, "native-cluster"))
        assert self.reads == 3 and "pg_catalog.pg_stat_activity" in sql
        return Result((0, 0, self.sessions))


class Target:
    def __init__(self, password, connection=None):
        self.password, self.connection = password, connection
        self.disposed = False
        self.authenticated = False

    def dump_environment(self):
        return {"PGPASSWORD": self.password}

    def engine(self):
        self.authenticated = True
        return self

    def connect(self):
        return self.connection

    def dispose(self):
        self.disposed = True


def replacement_neon(targets):
    actors = {"replacement_runtime": ("hirewiz_runtime_20261013_r1", 24576),
              "replacement_migration": ("hirewiz_migrator_20261013_r1", 24578),
              "observer": ("hirewiz_observer_20261013_r1", 24580)}
    checks = []
    def collect(reader, reference, *, alias, database_owner_oid, expected_schema):
        assert database_owner_oid == 16391 and expected_schema == release.CUTOVER_CHAIN[0]
        target = targets[alias]
        binding = SimpleNamespace(actor=actors[alias][0], actor_oid=actors[alias][1])
        if target.connection is None:
            target.connection = Connection(binding)
        return binding, target
    return SimpleNamespace(collect_fresh_release_target=collect,
        FRESH_DATABASE_OID=16395, FRESH_DATABASE_OWNER_OID=16391,
        FRESH_NAMESPACE_OWNER_OID=6171, NAMESPACE_OID=2200,
        FRESH_CLUSTER=hashlib.sha256(b"native-cluster").hexdigest(),
        verify_transport=lambda connection, binding: checks.append(binding.actor_oid),
        text=lambda sql: sql, checks=checks)


def test_precut_capture_uses_each_exact_initial_reference(prepared):
    boundary, *_ = prepared
    calls = []
    def collect(reader, reference, **kwargs):
        assert reader is boundary
        calls.append((reference, kwargs["alias"]))
        return None, Target("synthetic-before-" + kwargs["alias"])
    neon = SimpleNamespace(collect_fresh_release_target=collect, FRESH_DATABASE_OWNER_OID=16391)
    boundary._capture_setup_passwords(neon)
    assert calls == [(ref, alias) for alias, ref in module.SETUP_REFERENCES.items()]
    assert len(boundary._setup_passwords) == 3
    with pytest.raises(release.ReleaseDenied, match="fence_precut_password_capture_not_available"):
        boundary._capture_setup_passwords(neon)


def test_new_version_with_copied_password_refuses_before_any_auth(prepared, monkeypatch):
    boundary, source, target, responses, _ = prepared
    boundary.establish_retirement_cut(source)
    responses.created = boundary.retired_since + timedelta(microseconds=1)
    boundary._setup_passwords = {alias: "synthetic-before-" + alias for alias in module.SETUP_REFERENCES}
    targets = {alias: Target(password) for alias, password in boundary._setup_passwords.items()}
    neon = replacement_neon(targets)
    original = boundary._module
    monkeypatch.setattr(boundary, "_module", lambda path, name: neon if path.endswith("neon_direct_identity.py") else original(path, name))
    key_module = original("backend/scripts/model_key_revocation_observer.py", "inert_keys")
    boundary._verify_issuance_after_isolation(replace(boundary.scope, new_key=NEW), key_module)
    with pytest.raises(release.ReleaseDenied, match="fence_replacement_reuses_precut_password"):
        boundary._bind_distinct_replacement_passwords(target)
    assert not any(item.authenticated for item in targets.values())
    assert boundary._authenticated_replacements is None


@pytest.mark.parametrize(("new_alias", "old_alias"), [
    (new_alias, old_alias) for new_alias in module.SETUP_REFERENCES
    for old_alias in module.SETUP_REFERENCES if new_alias != old_alias
])
def test_cross_alias_old_password_refuses_before_any_auth(prepared, monkeypatch, new_alias, old_alias):
    boundary, source, target, responses, _ = prepared
    boundary.establish_retirement_cut(source)
    responses.created = boundary.retired_since + timedelta(microseconds=1)
    captured = {alias: "synthetic-before-" + alias for alias in module.SETUP_REFERENCES}
    boundary._setup_passwords = dict(captured)
    targets = {alias: Target("synthetic-after-" + alias) for alias in module.SETUP_REFERENCES}
    targets[new_alias] = Target(captured[old_alias])
    assert targets[new_alias].password != captured[new_alias]
    neon = replacement_neon(targets)
    original = boundary._module
    monkeypatch.setattr(boundary, "_module", lambda path, name: neon if path.endswith("neon_direct_identity.py") else original(path, name))
    with pytest.raises(release.ReleaseDenied, match="fence_replacement_reuses_precut_password"):
        boundary._bind_distinct_replacement_passwords(target)
    assert neon.checks == []
    assert not any(item.authenticated for item in targets.values())
    assert boundary._authenticated_replacements is None
    assert boundary._setup_passwords == captured


def test_different_passwords_require_exact_native_auth_once(prepared, monkeypatch):
    boundary, source, target, responses, _ = prepared
    boundary.establish_retirement_cut(source)
    responses.created = boundary.retired_since + timedelta(microseconds=1)
    boundary._setup_passwords = {alias: "synthetic-before-" + alias for alias in module.SETUP_REFERENCES}
    targets = {alias: Target("synthetic-after-" + alias) for alias in module.SETUP_REFERENCES}
    neon = replacement_neon(targets)
    original = boundary._module
    class Secret:
        _config = original("backend/scripts/legacy_credential_inventory.py", "inert_config")._config

        @staticmethod
        def _secret_value(*_):
            return None, "synthetic-google-key"
    def modules(path, name):
        if path.endswith("neon_direct_identity.py"):
            return neon
        if path.endswith("legacy_credential_inventory.py"):
            return Secret
        return original(path, name)
    monkeypatch.setattr(boundary, "_module", modules)
    boundary.bind_replacements_after_isolation(source, target)
    assert neon.checks == [24576, 24578, 24580]
    assert all(item.authenticated and item.disposed for item in targets.values())
    assert all(item.connection.reads == 3 and "READ ONLY" in item.connection.commands[0] for item in targets.values())
    assert boundary._setup_passwords == {}
    before = len(responses.calls)
    for _ in range(2):
        boundary.verify_fences(source)
    assert neon.checks == [24576, 24578, 24580]
    assert not any(path.endswith("hirewiz-fresh-migrator-db-20261013/versions/2")
                   for api, path, _ in responses.calls[before:] if api == "secret")


@pytest.mark.parametrize("defect", ["identity", "sessions"])
def test_auth_refuses_wrong_target_or_surviving_old_role_session(prepared, defect):
    boundary, *_ = prepared
    target = Target("synthetic-replacement")
    neon = replacement_neon({alias: target for alias in module.SETUP_REFERENCES})
    binding, _ = neon.collect_fresh_release_target(boundary, "inert:2", alias="observer",
        database_owner_oid=16391, expected_schema=release.CUTOVER_CHAIN[0])
    target.connection.identity_bad = defect == "identity"
    target.connection.sessions = int(defect == "sessions")
    reason = "fence_replacement_native_target_mismatch" if defect == "identity" else "fence_replacement_target_not_empty_or_sessions_present"
    with pytest.raises(release.ReleaseDenied, match=reason):
        boundary._authenticate_empty_replacement(neon, binding, target)
    assert target.disposed
