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
NEW_ISSUER = f"inert-runtime@{release.PROJECT}.iam.gserviceaccount.com"
NEW_ISSUER_UID = "123456789012345678901"
NEW = module.NativeKey("projects/157225724590/locations/global/keys/new-key", "b" * 32,
                       NEW_ISSUER, NEW_ISSUER_UID)
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
        self.key_value = "synthetic-google-key"

    def get(self, api, path, params):
        self.calls.append((api, path, params))
        if self.unavailable:
            raise release.ReleaseDenied("fence_native_read_unavailable")
        if api == "keys":
            if path == "keys:lookupKey":
                return {"name": NEW.name, "parent": NEW.name.rsplit("/keys/", 1)[0]}
            if path.endswith("/keyString"):
                return {"keyString": self.key_value}
            key = OLD if path == OLD.name else NEW
            row = {"name": key.name, "uid": key.uid, "etag": "native-etag",
                   "createTime": self.created.isoformat()}
            if key == OLD and not self.active:
                row["deleteTime"] = PAST
            if key == NEW:
                row["serviceAccountEmail"] = NEW_ISSUER
                row["restrictions"] = {"apiTargets": [{"service": "generativelanguage.googleapis.com"}]}
            return row
        if api == "iam":
            if path.endswith("/" + NEW_ISSUER):
                return {"email": NEW_ISSUER, "uniqueId": NEW_ISSUER_UID, "disabled": False}
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


def test_concurrent_native_timeout_refuses_and_invalidates_cut(prepared, monkeypatch):
    boundary, source, *_ = ready(prepared)
    def timeout(*_):
        raise TimeoutError("synthetic-private-vendor-detail")
    monkeypatch.setattr(boundary.reads, "get", timeout)
    with pytest.raises(release.ReleaseDenied, match="^fence_native_observation_expired$") as error:
        boundary.verify_fences(source)
    assert "synthetic-private-vendor-detail" not in str(error.value)
    assert boundary.retired_since is None


@pytest.mark.parametrize("value", ["synthetic-standard-key", "AQ." + "synthetic-auth-envelope-" * 8])
def test_opaque_google_key_is_bound_by_lookup_metadata_account_and_exact_value(prepared, value):
    boundary, _, _, responses, _ = prepared
    responses.key_value = value
    key = boundary._credential_key(value, boundary._module(
        "backend/scripts/model_key_revocation_observer.py", "opaque_key_test"))
    assert key == NEW and key.issuer_uid == NEW_ISSUER_UID
    assert responses.calls[-1] == ("keys", NEW.name + "/keyString", {})
    assert responses.calls[0] == ("keys", "keys:lookupKey", {"keyString": value})


def test_native_unbound_standard_key_remains_retirable_without_invented_issuer(prepared, monkeypatch):
    boundary, _, _, responses, _ = prepared
    original = responses.get
    def unbound(api, path, params):
        row = original(api, path, params)
        if path == NEW.name:
            row.pop("serviceAccountEmail")
        return row
    monkeypatch.setattr(responses, "get", unbound)
    key = boundary._credential_key(responses.key_value, boundary._module(
        "backend/scripts/model_key_revocation_observer.py", "standard_key_test"))
    assert key.issuer is key.issuer_uid is None
    assert not any(api == "iam" for api, _, _ in responses.calls)


@pytest.mark.parametrize("value", [None, "", "x" * 4097, "AQ.\nsecret", "AQ.\x00secret", "AQ.é"])
def test_invalid_opaque_envelope_refuses_without_native_request(prepared, value):
    boundary, _, _, responses, _ = prepared
    with pytest.raises(release.ReleaseDenied, match="fence_model_issuer_native_binding_unavailable"):
        boundary._credential_key(value, object())
    assert responses.calls == []


@pytest.mark.parametrize(("defect", "reason"), [
    ("parent", "fence_model_credential_native_resource_unmapped"),
    ("value", "fence_model_credential_native_value_mismatch"),
    ("issuer", "fence_key_issuer_identity_unmapped"),
    ("account", "fence_key_issuer_native_account_unmapped"),
    ("name", "fence_key_exact_resource_name_drift"),
])
def test_native_opaque_key_binding_mismatch_refuses(prepared, monkeypatch, defect, reason):
    boundary, _, _, responses, _ = prepared
    value = "AQ.synthetic-auth-key-in-RAM"
    responses.key_value = value
    original = responses.get
    def changed(api, path, params):
        row = original(api, path, params)
        if defect == "parent" and path == "keys:lookupKey":
            row["parent"] = "projects/999/locations/global"
        if defect == "value" and path.endswith("/keyString"):
            row["keyString"] = "AQ.different-native-value"
        if defect == "issuer" and path == NEW.name:
            row["serviceAccountEmail"] = "foreign@provider.example"
        if defect == "name" and path == NEW.name:
            row["name"] = NEW.name + "-same-parent-other-key"
        if defect == "account" and api == "iam":
            row["email"] = EMAIL
        return row
    monkeypatch.setattr(responses, "get", changed)
    with pytest.raises(release.ReleaseDenied, match=reason) as error:
        boundary._credential_key(value, boundary._module(
            "backend/scripts/model_key_revocation_observer.py", "opaque_key_test"))
    assert value not in str(error.value)


@pytest.mark.parametrize("value", ["sk-synthetic-openai-key", "gsk_synthetic_groq_key"])
def test_known_foreign_provider_key_refuses_before_any_google_request(prepared, value):
    boundary, _, _, responses, _ = prepared
    with pytest.raises(release.ReleaseDenied, match="^fence_non_google_model_issuer_native_retirement_unavailable$") as error:
        boundary._credential_key(value, object())
    assert responses.calls == [] and value not in str(error.value)


@pytest.mark.parametrize("value", ["gsk_synthetic_groq_key", "sk-synthetic-openai-key"])
def test_sdk_lookup_cannot_disclose_known_foreign_provider_key(monkeypatch, value):
    reads = module.SDKGoogleReads(Path("/inert/public-sdk"), "inert@example.com")
    calls = []
    monkeypatch.setattr(reads, "_http", lambda: calls.append("http") or object())
    with pytest.raises(release.ReleaseDenied, match="^fence_native_route_refused$"):
        reads.get("keys", "keys:lookupKey", {"keyString": value})
    assert calls == []


@pytest.mark.parametrize("defect", ["issuer_uid", "issuer_disabled", "restrictions"])
def test_bound_replacement_account_and_api_scope_drift_invalidate_fence(prepared, monkeypatch, defect):
    boundary, source, _, responses = ready(prepared)
    original = responses.get
    def changed(api, path, params):
        row = original(api, path, params)
        if path.endswith("/" + NEW_ISSUER):
            if defect == "issuer_uid":
                row["uniqueId"] = "999999999999999999999"
            elif defect == "issuer_disabled":
                row["disabled"] = True
        if path == NEW.name and defect == "restrictions":
            row["restrictions"] = {"apiTargets": [{"service": "unrelated.googleapis.com"}]}
        return row
    monkeypatch.setattr(responses, "get", changed)
    reason = "fence_replacement_model_api_restrictions_drift" if defect == "restrictions" else "fence_replacement_model_issuer_uid_or_state_drift"
    with pytest.raises(release.ReleaseDenied, match=reason):
        boundary.verify_fences(source)
    assert boundary.retired_since is None


def test_every_foreign_retained_capability_is_reobserved_in_same_fresh_closure(prepared, monkeypatch):
    boundary, source, *_ = ready(prepared)
    foreign = (module.model_denial.ForeignModelCredential(
        "openai", "sk-synthetic-retained-value", ("captured-origin",)),)
    boundary.scope = replace(boundary.scope, foreign_keys=foreign)
    calls = []
    monkeypatch.setattr(module.model_denial, "observe", lambda values, **kwargs:
                        calls.append((values, kwargs["deadline"])))
    boundary.verify_fences(source)
    assert len(calls) == 1 and calls[0][0] == foreign
    assert module.time.monotonic() < calls[0][1]
    assert foreign[0].value not in repr(boundary.scope)


def test_one_foreign_capability_restored_or_unavailable_invalidates_full_fence(prepared, monkeypatch):
    boundary, source, *_ = ready(prepared)
    boundary.scope = replace(boundary.scope, foreign_keys=(module.model_denial.ForeignModelCredential(
        "openai", "sk-synthetic-retained-value", ("captured-origin",)),))
    def refused(*_args, **_kwargs):
        raise module.model_denial.DenialUnavailable("provider_exact_invalid_key_denial_unavailable")
    monkeypatch.setattr(module.model_denial, "observe", refused)
    with pytest.raises(release.ReleaseDenied, match="provider_exact_invalid_key_denial_unavailable"):
        boundary.verify_fences(source)
    assert boundary.retired_since is None


@pytest.mark.parametrize("denied", [True, False])
def test_mixed_retained_census_keeps_all_foreign_values_and_origins_before_scope_admission(
    prepared, monkeypatch, denied,
):
    boundary, source, _, responses, _ = prepared
    boundary.scope = None
    responses.active = True
    original_modules = boundary._module
    retained = original_modules("backend/scripts/legacy_credential_inventory.py", "mixed_retained_test")
    neon = original_modules("backend/scripts/neon_direct_identity.py", "mixed_neon_test")
    path = module.PARENT + "/services/ai-resume-parser"
    origin = retained.Origin(path, "service-uid", "service", 0, "LLM_API_KEY", 0)
    values = ("gsk_synthetic_retained_groq", "sk-first-synthetic-retained-openai",
              "sk-second-synthetic-retained-openai")
    model_items = tuple(retained.Credential("model", (origin,), value)
                        for value in (responses.key_value, *values))
    db_origin = retained.Origin(path, "service-uid", "service", 0, "DATABASE_URL", 1)
    database = retained.Credential("database", (db_origin,),
        f"postgresql://legacy_owner:synthetic-password@{neon.RELEASE_DIRECT_HOST}/neondb")
    inventory = retained.RetainedInventory((*model_items, database), (), (),
                                          (("service", path, "service-uid"),))
    def collect(capture):
        capture.get("run", path, {})
        return inventory
    monkeypatch.setattr(retained, "collect_retained_credentials", collect)
    reviewer_inventory = SimpleNamespace(role_env={"retired": "HIREWIZ_TEST_MIXED_OLD_ROLE"},
        database_operator_role_env=("HIREWIZ_TEST_MIXED_OPERATOR",))
    observer = SimpleNamespace(load_inventory=lambda *args: (reviewer_inventory, {}))
    def modules(relative, name):
        if relative.endswith("legacy_credential_inventory.py"):
            return retained
        if relative.endswith("monetary_cutover_preflight.py"):
            return observer
        if relative.endswith("neon_direct_identity.py"):
            return neon
        return original_modules(relative, name)
    monkeypatch.setattr(boundary, "_module", modules)
    monkeypatch.setenv("HIREWIZ_TEST_MIXED_OLD_ROLE", "legacy_owner")
    monkeypatch.setenv("HIREWIZ_TEST_MIXED_OPERATOR", "synthetic_operator")
    monkeypatch.setattr(boundary, "_capture_setup_passwords", lambda *_: None)
    native_get = responses.get
    def google(api, key_path, params):
        if api == "keys" and key_path == "keys:lookupKey":
            responses.calls.append((api, key_path, params))
            return {"name": OLD.name, "parent": OLD.name.rsplit("/keys/", 1)[0]}
        return native_get(api, key_path, params)
    monkeypatch.setattr(responses, "get", google)
    observed = []
    def foreign(credentials, **kwargs):
        observed.extend(credentials)
        if not denied:
            raise module.model_denial.DenialUnavailable("provider_exact_invalid_key_denial_unavailable")
    monkeypatch.setattr(module.model_denial, "observe", foreign)
    if denied:
        boundary.bind_authorities_once(source)
        assert tuple(item.value for item in boundary.scope.foreign_keys) == values
        assert all(item.origins == (origin,) for item in boundary.scope.foreign_keys)
    else:
        with pytest.raises(release.ReleaseDenied, match="provider_exact_invalid_key_denial_unavailable"):
            boundary.bind_authorities_once(source)
        assert boundary.scope is None
    assert tuple(item.value for item in observed) == values
    assert not any(params.get("keyString") in values for api, _, params in responses.calls if api == "keys")


@pytest.fixture
def historical_service_actor(prepared, monkeypatch):
    """The employer actor is reachable but its DSN belongs to another cluster."""
    boundary, source, _, responses, _ = prepared
    boundary.scope = None
    responses.active = True
    original_modules = boundary._module
    retained = original_modules("backend/scripts/legacy_credential_inventory.py", "actor_retained_test")
    neon = original_modules("backend/scripts/neon_direct_identity.py", "actor_neon_test")
    api = module.PARENT + "/services/ai-resume-parser"
    employer = module.PARENT + "/services/hirewiz-employer-worker"
    revision = employer + "/revisions/historical-employer"
    actor = "hirewiz-employer-worker@ai-resume-parser-482412.iam.gserviceaccount.com"
    actor_uid = "105594666905475527509"
    configs = {
        api: {"name": api, "uid": "service-uid", "template": {
            "containers": [{"image": "retained-api"}], "serviceAccount": EMAIL}},
        employer: {"name": employer, "uid": "employer-uid", "template": {
            "containers": [{"image": "retained-employer"}], "serviceAccount": EMAIL},
            "trafficStatuses": [{"revision": "historical-employer", "percent": 0, "tag": "old"}]},
        revision: {"name": revision, "uid": "revision-uid", "service": employer,
            "containers": [{"image": "retained-employer"}], "serviceAccount": EMAIL},
    }
    model_origin = retained.Origin(api, "service-uid", "service", 0, "LLM_API_KEY", 0)
    db_origin = retained.Origin(api, "service-uid", "service", 0, "DATABASE_URL", 1)
    historical_origin = retained.Origin(employer, "employer-uid", "service", 0, "DATABASE_URL", 0)
    resources = [("service", api, "service-uid"), ("service", employer, "employer-uid"),
                 ("revision", revision, "revision-uid")]
    credentials = (
        retained.Credential("model", (model_origin,), responses.key_value),
        retained.Credential("database", (db_origin,),
            f"postgresql://legacy_owner:synthetic-password@{neon.RELEASE_DIRECT_HOST}/neondb"),
        retained.Credential("database", (historical_origin,),
            "postgresql://historical_owner:synthetic-historical@historical.example.invalid/neondb"),
    )
    def collect(capture):
        for _, name, _ in resources:
            capture.get("run", name, {})
        return retained.RetainedInventory(credentials, (), (), tuple(resources))
    monkeypatch.setattr(retained, "collect_retained_credentials", collect)
    registry = SimpleNamespace(role_env={"retired": "HIREWIZ_TEST_ACTOR_OLD_ROLE"},
        database_operator_role_env=("HIREWIZ_TEST_ACTOR_OPERATOR",))
    def modules(relative, name):
        if relative.endswith("legacy_credential_inventory.py"):
            return retained
        if relative.endswith("monetary_cutover_preflight.py"):
            return SimpleNamespace(load_inventory=lambda *args: (registry, {}))
        if relative.endswith("neon_direct_identity.py"):
            return neon
        return original_modules(relative, name)
    monkeypatch.setattr(boundary, "_module", modules)
    monkeypatch.setenv("HIREWIZ_TEST_ACTOR_OLD_ROLE", "legacy_owner")
    monkeypatch.setenv("HIREWIZ_TEST_ACTOR_OPERATOR", "synthetic_operator")
    monkeypatch.setattr(boundary, "_capture_setup_passwords", lambda *_: None)
    native_get = responses.get
    account = {"email": actor, "uniqueId": actor_uid, "disabled": True}
    def get(api_name, path, params):
        if api_name == "run" and path in configs:
            responses.calls.append((api_name, path, params))
            return configs[path]
        if api_name == "iam" and path.endswith("/" + actor):
            responses.calls.append((api_name, path, params))
            return dict(account)
        if api_name == "keys" and path == "keys:lookupKey":
            return {"name": OLD.name, "parent": OLD.name.rsplit("/keys/", 1)[0]}
        return native_get(api_name, path, params)
    monkeypatch.setattr(responses, "get", get)
    return boundary, source, responses, configs, resources, actor, actor_uid, account


@pytest.mark.parametrize("location", ["template", "routed_revision"])
def test_reachable_actor_with_historical_database_is_acquired_and_freshly_fenced(
    historical_service_actor, location,
):
    boundary, source, responses, configs, _, actor, uid, account = historical_service_actor
    employer = module.PARENT + "/services/hirewiz-employer-worker"
    config = (configs[employer]["template"] if location == "template"
              else configs[employer + "/revisions/historical-employer"])
    config["serviceAccount"] = actor
    boundary.bind_authorities_once(source)
    assert {item.email: item.uid for item in boundary.scope.principals} == {EMAIL: UID, actor: uid}
    assert boundary.scope.retired_source_actors == ("legacy_owner",)
    assert ("iam", "projects/-/serviceAccounts/" + actor, {}) in responses.calls
    responses.active = False
    boundary.establish_retirement_cut(source)
    account["disabled"] = False
    with pytest.raises(release.ReleaseDenied, match="^fence_old_automated_principal_still_enabled$"):
        boundary._observe_closure(source, replacements=False)


@pytest.mark.parametrize("defect", ["missing_revision", "cross_service_revision", "uid", "missing_actor"])
def test_historical_reachable_actor_requires_complete_snapshot_and_native_identity(
    historical_service_actor, defect,
):
    boundary, source, responses, configs, resources, actor, _, account = historical_service_actor
    employer = module.PARENT + "/services/hirewiz-employer-worker"
    configs[employer]["template"]["serviceAccount"] = actor
    if defect == "missing_revision":
        resources.pop()
    elif defect == "cross_service_revision":
        configs[employer]["trafficStatuses"][0]["revision"] = (
            module.PARENT + "/services/ai-resume-parser/revisions/historical-employer")
    elif defect == "uid":
        account["uniqueId"] = "not-a-native-uid"
    else:
        del configs[employer]["template"]["serviceAccount"]
    reason = ("fence_native_principal_identity_unmapped" if defect == "uid" else
              "fence_retained_automated_principal_unmapped" if defect == "missing_actor" else
              "fence_routed_revision_snapshot_unmapped")
    with pytest.raises(release.ReleaseDenied, match="^" + reason + "$"):
        boundary.bind_authorities_once(source)
    assert boundary.scope is None
