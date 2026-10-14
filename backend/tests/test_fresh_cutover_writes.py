"""Pure native-action controls; no SDK, provider, SQL or manager-body access."""

from __future__ import annotations

import copy
import importlib
import json
import os
import sys
import urllib.parse
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(os.environ.get("HIREWIZ_ACTION_TEST_ROOT", str(Path(__file__).resolve().parents[2])))
sys.path[:0] = [str(Path(__file__).parent), str(ROOT / "infra/gcp"), str(ROOT / "backend")]
module = importlib.import_module("fresh_cutover_writes")


class HTTP:
    def __init__(self):
        self.calls = []
        self.respond = lambda *_: (200, {})

    def request(self, url, *, method, body, headers, redirections):
        assert redirections == 0 and headers["Cache-Control"] == "no-cache"
        self.calls.append((method, url, None if body is None else json.loads(body)))
        status, value = self.respond(method, url, self.calls[-1][2])
        return SimpleNamespace(status=status), json.dumps(value).encode()


@pytest.fixture
def writer(tmp_path, monkeypatch):
    tmp_path.chmod(0o700)
    http = HTTP()
    reader = module.native.SDKGoogleReads(
        Path("/synthetic-public-sdk"), "synthetic-authorized-manager@example.com"
    )
    monkeypatch.setattr(module.native.SDKGoogleReads, "_http", lambda _: http)
    boundary = object.__new__(module.native.NativeFreshFenceBoundary)
    boundary.reads = reader
    boundary.scope = SimpleNamespace(keys=(), principals=(), source=object(), foreign_keys=())
    boundary.retired_since = None
    return module.OfficialWrites(boundary, tmp_path), http, tmp_path


def test_unknown_secret_create_retains_attempt_and_cannot_repeat_post(writer, capsys):
    writes, http, workdir = writer

    def unknown(method, *_):
        if method == "GET":
            return 404, {}
        raise RuntimeError("synthetic-password-and-vendor-body")

    http.respond = unknown
    with pytest.raises(module.WriteDenied, match="write_native_result_unknown_reconcile_only"):
        writes.create_secret(module.SOURCE_SECRET)
    assert (workdir / ("create-secret-" + module.SOURCE_SECRET + ".started.json")).is_file()
    with pytest.raises(module.WriteDenied, match="write_already_attempted"):
        writes.create_secret(module.SOURCE_SECRET)
    assert sum(row[0] == "POST" for row in http.calls) == 1
    assert "synthetic-password" not in "".join(path.read_text() for path in workdir.iterdir())
    assert capsys.readouterr() == ("", "")


def test_old_secret_and_unrelated_provider_routes_refuse_without_transport(writer):
    writes, http, _ = writer
    with pytest.raises(module.WriteDenied):
        writes.create_secret("hirewiz-database-url")
    with pytest.raises(module.WriteDenied):
        writes._call(
            "iam", "POST", "projects/-/serviceAccounts/unrelated@example.com:disable", body={}
        )
    assert http.calls == []


def tex_grant_fixture(writer, monkeypatch, *, bindings=None, unknown=False):
    writes, http, _ = writer
    tex = module.native.release.native_tex
    origin = "https://hirewiz-native-tex-synthetic-uc.a.run.app"
    monkeypatch.setattr(tex, "ACCEPTED_ORIGIN", origin, raising=False)
    monkeypatch.setattr(tex, "ACCEPTED_IMAGE", tex.REPOSITORY + "@sha256:" + "f" * 64, raising=False)
    monkeypatch.setattr(module.source_sql, "verify_cloud_isolated", lambda _: None)
    runtime = module.NativeAccount(tex.INVOKER, "123456")
    policy = {"version": 3, "etag": "synthetic-etag", "bindings": bindings or []}
    def respond(method, url, body):
        if "/serviceAccounts/" in url:
            return 200, {"email": runtime.email, "uniqueId": runtime.uid, "disabled": False}
        if ":getIamPolicy" in url:
            return 200, copy.deepcopy(policy)
        if method == "POST":
            assert url.split("?")[0].endswith(tex.RESOURCE + ":setIamPolicy")
            if unknown:
                raise RuntimeError("synthetic-unknown-credential-and-body")
            policy.update(body["policy"])
            policy["etag"] = "synthetic-new-etag"
            return 200, copy.deepcopy(policy)
        return 200, {"name": tex.RESOURCE, "uid": "synthetic-service-uid", "uri": origin,
                     "reconciling": False}
    http.respond = respond
    return writes, http, runtime


def test_tex_invoker_grant_is_exact_once_and_desired_policy_is_readonly_reconciliation(
    writer, monkeypatch,
):
    writes, http, runtime = tex_grant_fixture(writer, monkeypatch)
    writes.grant_native_tex_invoker(runtime)
    posts = [row for row in http.calls if row[0] == "POST"]
    assert len(posts) == 1
    assert posts[0][2]["policy"]["bindings"] == [{"role": "roles/run.invoker",
        "members": ["serviceAccount:" + runtime.email]}]
    writes.grant_native_tex_invoker(runtime)
    assert sum(row[0] == "POST" for row in http.calls) == 1


@pytest.mark.parametrize("bindings", [
    [{"role": "roles/run.invoker", "members": ["allUsers"]}],
    [{"role": "roles/run.invoker", "members": [
        "serviceAccount:hirewiz-api@ai-resume-parser-482412.iam.gserviceaccount.com"]}],
])
def test_tex_invoker_refuses_public_or_old_runtime_initial_policy(writer, monkeypatch, bindings):
    writes, http, runtime = tex_grant_fixture(writer, monkeypatch, bindings=bindings)
    with pytest.raises(module.WriteDenied, match="initial_policy_refused"):
        writes.grant_native_tex_invoker(runtime)
    assert all(row[0] == "GET" for row in http.calls)


def test_lost_tex_invoker_grant_is_never_replayed(writer, monkeypatch):
    writes, http, runtime = tex_grant_fixture(writer, monkeypatch, unknown=True)
    with pytest.raises(module.WriteDenied, match="native_result_unknown"):
        writes.grant_native_tex_invoker(runtime)
    with pytest.raises(module.WriteDenied, match="already_attempted"):
        writes.grant_native_tex_invoker(runtime)
    assert sum(row[0] == "POST" for row in http.calls) == 1


def test_native_key_uid_drift_refuses_before_delete_or_marker(writer):
    writes, http, workdir = writer
    name = sorted(module.OLD_KEYS)[0]
    writes.boundary.scope.keys = (module.native.NativeKey(name, "native-bound-uid", None),)
    http.respond = lambda *_: (
        200,
        {"name": name, "uid": "different-current-uid", "etag": "native-etag"},
    )
    with pytest.raises(module.WriteDenied, match="write_old_key_uid"):
        writes.delete_old_model_keys()
    assert all(call[0] == "GET" for call in http.calls) and not list(workdir.iterdir())


def test_key_delete_joins_exact_operation_and_current_native_uid(writer):
    writes, http, _ = writer
    name = sorted(module.OLD_KEYS)[0]
    key = {"name": name, "uid": "native-bound-uid", "etag": "native-etag"}
    writes.boundary.scope.keys = (module.native.NativeKey(name, key["uid"], None),)

    def respond(method, url, _):
        if method == "DELETE":
            assert "etag=native-etag" in url
            key["deleteTime"] = "2026-10-14T00:00:00Z"
            return 200, {
                "name": "operations/exact-native-operation",
                "done": True,
                "response": key.copy(),
            }
        return 200, key.copy()

    http.respond = respond
    assert writes.delete_old_model_keys() == (name,)
    assert [call[0] for call in http.calls] == ["GET", "DELETE", "GET"]


def test_operation_resource_drift_is_not_a_delete_success(writer):
    writes, _, _ = writer
    with pytest.raises(module.WriteDenied, match="write_operation_failed_or_resource_drift"):
        writes._operation(
            {
                "name": "operations/native",
                "done": True,
                "response": {"name": "unrelated", "uid": "same"},
            },
            resource="expected",
            uid="same",
            deadline=module.time.monotonic() + 10,
        )


def test_live_build_prevents_all_service_account_disables(writer):
    writes, http, workdir = writer
    email, uid = next(iter(module.OLD_PRINCIPALS.items()))
    writes.boundary.scope.principals = (module.native.NativePrincipal(email, uid),)

    def respond(_method, url, _body):
        if "/triggers/" in url:
            return 200, held_trigger()
        if url.endswith("/builds/" + module.LAST_OPERATOR_BUILD):
            return 200, {
                "id": module.LAST_OPERATOR_BUILD,
                "projectId": module.PROJECT,
                "name": "projects/"
                + module.NUMBER
                + "/locations/us-central1/builds/"
                + module.LAST_OPERATOR_BUILD,
                "serviceAccount": "projects/"
                + module.PROJECT
                + "/serviceAccounts/157225724590-compute@developer.gserviceaccount.com",
                "status": "SUCCESS",
            }
        return 200, {"builds": [{"status": "WORKING"}]}

    http.respond = respond
    with pytest.raises(module.WriteDenied, match="write_live_builds"):
        writes.disable_old_principals()
    assert all(call[0] == "GET" for call in http.calls) and not list(workdir.iterdir())


def test_secret_version_crc_and_actual_readback_before_return(writer, monkeypatch, capsys):
    writes, http, workdir = writer
    path = f"projects/{module.NUMBER}/secrets/{module.SOURCE_SECRET}/versions/2"
    value = "synthetic-password-only-in-ram"

    def response(_, __, body):
        payload = body["payload"]
        decoded = module.base64.b64decode(payload["data"])
        assert decoded.decode() == value and payload["dataCrc32c"] == str(
            module.retained._crc32c(decoded)
        )
        return 200, {"name": path, "state": "ENABLED"}

    http.respond = response
    monkeypatch.setattr(module.retained, "_secret_value", lambda *args: ("ENABLED", value))
    assert (
        writes.add_version(module.SOURCE_SECRET, value, phase="source")
        == module.SOURCE_SECRET + ":2"
    )
    assert value not in "".join(
        f.read_text() for f in workdir.iterdir()
    ) and capsys.readouterr() == ("", "")


def test_crc_valid_but_changed_readback_refuses_version(writer, monkeypatch):
    writes, http, _ = writer
    path = f"projects/{module.NUMBER}/secrets/{module.SOURCE_SECRET}/versions/2"
    http.respond = lambda *_: (200, {"name": path, "state": "ENABLED"})
    monkeypatch.setattr(module.retained, "_secret_value", lambda *args: ("ENABLED", "different"))
    with pytest.raises(module.WriteDenied, match="write_secret_version_readback_mismatch"):
        writes.add_version(module.SOURCE_SECRET, "original", phase="source")


def test_runtime_cannot_receive_migration_secret_access(writer):
    writes, http, _ = writer
    account = module.NativeAccount(
        module.NEW_ACCOUNTS[0] + "@" + module.PROJECT + ".iam.gserviceaccount.com", "123456789"
    )
    with pytest.raises(module.WriteDenied, match="write_new_account_name_refused"):
        writes.grant_secret_access(module.TARGET_ROLES[1][2], (account,))
    assert http.calls == []


class Result:
    def __init__(self, rows):
        self.rows = rows

    def fetchall(self):
        return self.rows

    def fetchone(self):
        return self.rows[0] if self.rows else None


class Target:
    def __init__(self, fail=False):
        self.commands = []
        self.closed = False
        self.fail = fail

    @contextmanager
    def transaction(self):
        before = len(self.commands)
        yield self
        if self.fail and any(text.startswith("ALTER ROLE") for text in self.commands[before:]):
            raise RuntimeError("synthetic unknown commit body")

    def close(self):
        self.closed = True

    def execute(self, command):
        text = command if isinstance(command, str) else command.as_string(None)
        self.commands.append(text)
        if "SELECT datdba" in text:
            return Result([(16391,)])
        if "SELECT oid,rolname,rolcanlogin" in text:
            return Result(
                [
                    (oid, name, True, False, False, False, False, False, False)
                    for name, oid, _ in module.TARGET_ROLES
                ]
            )
        return Result([])


def target_bind(writes, monkeypatch, *, fail=False):
    identity = module.transfer.NativeDatabaseIdentity(
        module.TARGET_HOST,
        5432,
        "neondb",
        module.TARGET_CLUSTER,
        16395,
        "hirewiz_bootstrap_20261013",
        16391,
        "/synthetic-public-ca.pem",
    )
    bootstrap = module.transfer.BoundDatabase(identity, "synthetic-bootstrap")
    connection = Target(fail)
    monkeypatch.setattr(module.transfer.BoundDatabase, "connect", lambda _: connection)
    monkeypatch.setattr(module.transfer, "_empty", lambda _: None)
    monkeypatch.setattr(module.source_sql, "verify_cloud_isolated", lambda _: None)
    writes.boundary.retired_since = datetime.now(UTC)
    writes.boundary._source_old_passwords = ("old-source", "old-temporary-observer")
    writes.boundary._setup_passwords = {
        "runtime": "old-runtime",
        "migration": "old-migration",
        "observer": "old-observer",
    }
    monkeypatch.setattr(
        module.native.NativeFreshFenceBoundary,
        "_observe_closure",
        lambda *_args, **_kwargs: datetime.now(UTC),
    )
    return bootstrap, connection


def test_cross_alias_precut_password_refuses_before_target_auth_or_secret_write(
    writer, monkeypatch
):
    writes, http, _ = writer
    bootstrap, connection = target_bind(writes, monkeypatch)
    values = iter(["old-migration", "different-runtime", "different-observer"])
    monkeypatch.setattr(module.secrets, "token_urlsafe", lambda _: next(values))
    with pytest.raises(module.WriteDenied, match="write_target_password_reuses_precut_value"):
        module.rotate_empty_target(writes, bootstrap)
    assert connection.commands == [] and http.calls == []


def test_new_versions_precede_password_transaction_and_unknown_commit_never_replays(
    writer, monkeypatch
):
    writes, _, workdir = writer
    bootstrap, connection = target_bind(writes, monkeypatch, fail=True)
    calls = []

    def version(_self, name, value, *, phase):
        assert not any(command.startswith("ALTER ROLE") for command in connection.commands)
        assert "synthetic-bootstrap" not in value
        calls.append(name)
        return name + ":2"

    monkeypatch.setattr(module.OfficialWrites, "add_version", version)
    with pytest.raises(module.WriteDenied, match="write_target_rotation_unknown_reconcile_only"):
        module.rotate_empty_target(writes, bootstrap)
    assert calls == [row[2] for row in module.TARGET_ROLES]
    assert connection.closed and (workdir / "rotate-fresh-target-passwords.started.json").is_file()
    before = sum(command.startswith("ALTER ROLE") for command in connection.commands)
    with pytest.raises(module.WriteDenied, match="write_target_rotation_already_attempted"):
        module.rotate_empty_target(writes, bootstrap)
    assert sum(command.startswith("ALTER ROLE") for command in connection.commands) == before


def held_trigger():
    return {
        "id": module.TRIGGER_ID,
        "resourceName": module.TRIGGER_PATH,
        "name": "Trigger-for-github-updates",
        "filename": "cloudbuild.yaml",
        "disabled": True,
        "github": {
            "owner": "Harshil4442",
            "name": "ai-resume-copilot",
            "push": {"branch": "^main$"},
        },
        "serviceAccount": "projects/"
        + module.PROJECT
        + "/serviceAccounts/157225724590-compute@developer.gserviceaccount.com",
    }


def test_enabled_trigger_prevents_any_old_principal_mutation(writer):
    writes, http, workdir = writer
    email, uid = next(iter(module.OLD_PRINCIPALS.items()))
    writes.boundary.scope.principals = (module.native.NativePrincipal(email, uid),)
    http.respond = lambda *_: (200, {**held_trigger(), "disabled": False})
    with pytest.raises(module.WriteDenied, match="write_trigger_not_exactly_held"):
        writes.disable_old_principals()
    assert len(http.calls) == 1 and http.calls[0][0] == "GET" and not list(workdir.iterdir())


def test_artifact_policy_preserves_existing_bindings_and_excludes_release_backups(writer):
    writes, http, _ = writer
    original = {
        "version": 3,
        "etag": "actual-bound-etag",
        "bindings": [
            {
                "role": "roles/storage.objectViewer",
                "members": ["user:existing-owner@example.com"],
                "condition": {"title": "existing", "expression": "true"},
            }
        ],
    }
    current = json.loads(json.dumps(original))
    writes_seen = []

    def respond(method, _url, body):
        nonlocal current
        if method == "PUT":
            assert body["etag"] == original["etag"]
            current = json.loads(json.dumps(body))
            writes_seen.append(current)
        return 200, current

    http.respond = respond
    member = (
        "serviceAccount:"
        + module.NEW_ACCOUNTS[0]
        + "@"
        + module.PROJECT
        + ".iam.gserviceaccount.com"
    )
    writes._grant_resource(
        "storage",
        "b/" + module.BUCKET + "/iam",
        "roles/storage.objectUser",
        member,
        condition=module.ARTIFACT_CONDITION,
    )
    assert len(writes_seen) == 1 and current["bindings"][0] == original["bindings"][0]
    added = current["bindings"][1]
    assert added["members"] == [member] and added["condition"] == module.ARTIFACT_CONDITION
    expression = added["condition"]["expression"]
    assert "resource.type == 'storage.googleapis.com/Object'" in expression
    assert "/objects/releases/'" in expression and "!resource.name.startsWith" in expression


def test_retained_source_observer_password_cannot_be_reused_for_fresh_target(writer, monkeypatch):
    writes, http, _ = writer
    bootstrap, connection = target_bind(writes, monkeypatch)
    values = iter(["old-temporary-observer", "new-migration", "new-observer"])
    monkeypatch.setattr(module.secrets, "token_urlsafe", lambda _: next(values))
    with pytest.raises(module.WriteDenied, match="write_target_password_reuses_precut_value"):
        module.rotate_empty_target(writes, bootstrap)
    assert connection.commands == [] and http.calls == []


def model_key_fixture(writer, monkeypatch, *, defect=None):
    writes, http, _ = writer
    monkeypatch.setattr(module.source_sql, "verify_cloud_isolated", lambda _: None)
    writes.boundary.retired_since = datetime.now(UTC)
    runtime = module.NativeAccount(module.NEW_ACCOUNTS[0] + "@" + module.PROJECT
                                   + ".iam.gserviceaccount.com", "123456789012345678901")
    path = f"projects/{module.NUMBER}/locations/global/keys/{module.MODEL_KEY_ID}"
    row = {"name": path, "uid": "synthetic-native-key-uid", "etag": "synthetic-etag",
           "createTime": datetime.now(UTC).isoformat(), "serviceAccountEmail": runtime.email,
           "restrictions": {"apiTargets": [{"service": "generativelanguage.googleapis.com"}]}}
    value = "AQ." + "synthetic-auth-key-" * 8
    created = False
    published = []
    monkeypatch.setattr(writes, "create_secret", lambda name: published.append(("secret", name)))
    monkeypatch.setattr(writes, "add_version", lambda name, key, **kw: published.append(
        ("version", name, key, kw)) or name + ":2")
    def respond(method, url, body):
        nonlocal created
        clean = url.split("?")[0]
        if "/serviceAccounts/" in url:
            account = {"email": runtime.email, "uniqueId": runtime.uid,
                       "projectId": module.PROJECT, "disabled": False}
            if defect == "account_uid":
                account["uniqueId"] = "999999999999999999999"
            return 200, account
        if clean.endswith("keys:lookupKey"):
            assert urllib.parse.parse_qs(urllib.parse.urlsplit(url).query) == {"keyString": [value]}
            return 200, {"name": path if defect != "lookup" else path + "-foreign",
                         "parent": path.rsplit("/keys/", 1)[0]}
        if clean.endswith("/keyString"):
            return 200, {"keyString": value if defect != "value" else ""}
        if method == "POST":
            assert body == {"displayName": module.MODEL_KEY_ID,
                            "serviceAccountEmail": runtime.email,
                            "restrictions": row["restrictions"]}
            if defect == "unknown":
                raise RuntimeError(value)
            created = True
            return 200, {"name": "operations/synthetic-key-create", "done": True,
                         "response": copy.deepcopy(row)}
        if not created:
            return 404, {}
        observed = copy.deepcopy(row)
        if defect == "issuer":
            observed["serviceAccountEmail"] = "foreign@example.iam.gserviceaccount.com"
        if defect == "restriction":
            observed["restrictions"] = {}
        return 200, observed
    http.respond = respond
    return writes, http, runtime, value, published


def test_model_replacement_is_authorization_key_bound_to_exact_new_native_account(writer, monkeypatch, capsys):
    writes, http, runtime, value, published = model_key_fixture(writer, monkeypatch)
    assert writes.create_model_replacement(runtime) == module.MODEL_SECRET + ":2"
    assert published == [("secret", module.MODEL_SECRET), ("version", module.MODEL_SECRET, value,
                         {"phase": "post-isolation-model"})]
    assert sum(method == "POST" for method, _, _ in http.calls) == 1
    assert capsys.readouterr() == ("", "")
    assert value not in "".join(path.read_text() for path in writer[2].iterdir())


@pytest.mark.parametrize(("defect", "reason"), [
    ("account_uid", "write_native_account_identity_drift"),
    ("issuer", "write_new_model_key_readback_drift"),
    ("restriction", "write_new_model_key_readback_drift"),
    ("lookup", "write_new_model_key_native_value_unmapped"),
    ("value", "write_new_model_key_value_unavailable"),
])
def test_model_authorization_binding_drift_never_publishes_secret(writer, monkeypatch, defect, reason):
    writes, http, runtime, value, published = model_key_fixture(writer, monkeypatch, defect=defect)
    with pytest.raises(module.WriteDenied, match=reason) as error:
        writes.create_model_replacement(runtime)
    assert published == [] and value not in str(error.value)
    if defect == "account_uid":
        assert all(method == "GET" for method, _, _ in http.calls)


def test_unknown_authorization_key_create_retains_marker_and_cannot_replay(writer, monkeypatch):
    writes, http, runtime, value, published = model_key_fixture(writer, monkeypatch, defect="unknown")
    with pytest.raises(module.WriteDenied, match="write_native_result_unknown_reconcile_only"):
        writes.create_model_replacement(runtime)
    with pytest.raises(module.WriteDenied, match="write_already_attempted"):
        writes.create_model_replacement(runtime)
    assert sum(method == "POST" for method, _, _ in http.calls) == 1 and published == []
    assert value not in "".join(path.read_text() for path in writer[2].iterdir())


def pristine_owner_fixture(writer, monkeypatch, defect=None):
    writes, http, _ = writer
    key_module = importlib.import_module("scripts.model_key_revocation_observer")
    monkeypatch.setattr(writes.boundary, "_module", lambda *_: key_module)
    writes.boundary._setup_passwords = {name: "synthetic-setup" for name in module.native.SETUP_REFERENCES}
    writes.boundary.scope.principals = tuple(module.native.NativePrincipal(email, uid)
        for email, uid in module.OLD_PRINCIPALS.items())
    writes.boundary.scope.keys = tuple(module.native.NativeKey(name, "native-key-uid", None)
        for name in module.OLD_KEYS)
    refs = tuple(module.native.SETUP_REFERENCES.values())
    writes.boundary.scope.target = SimpleNamespace(runtime_secret=refs[0], migration_secret=refs[1],
                                                   observer_secret=refs[2])
    def respond(method, url, _body):
        assert method == "GET"
        clean = url.split("?")[0]
        if "/serviceAccounts/" in clean:
            email = clean.rsplit("/", 1)[-1]
            if email in module.OLD_PRINCIPALS:
                return 200, {"email": email, "uniqueId": module.OLD_PRINCIPALS[email],
                             "disabled": defect == "old_disabled"}
            return (200, {"email": email}) if defect == "new_account" else (404, {})
        if clean.endswith("/versions"):
            parent = clean.split("/v1/", 1)[1]
            versions = [{"name": parent + "/1", "state": "ENABLED"}]
            if defect == "rotated_target":
                versions.append({"name": parent + "/2", "state": "ENABLED"})
            row = {"versions": versions}
            if defect == "partial_versions":
                row["nextPageToken"] = "synthetic-more"
            return 200, row
        if "/keys/" in clean and clean.rsplit("/", 1)[-1] != module.MODEL_KEY_ID:
            row = {"name": clean.split("/v2/", 1)[1], "uid": "native-key-uid", "etag": "native-etag"}
            if defect == "old_deleted":
                row["deleteTime"] = "2026-01-01T00:00:00Z"
            return 200, row
        if defect == "replacement_issued":
            return 200, {"name": clean}
        return 404, {}
    http.respond = respond
    return writes, http


def test_prepared_owner_resume_pristine_uses_only_native_metadata_reads(writer, monkeypatch):
    writes, http = pristine_owner_fixture(writer, monkeypatch)
    writes.verify_prepared_owner_resume_pristine()
    assert len(http.calls) == 18 and all(row[0] == "GET" for row in http.calls)
    assert not list(writer[2].iterdir())


@pytest.mark.parametrize(("defect", "reason"), [
    ("old_disabled", "write_owner_resume_old_principal_not_enabled"),
    ("old_deleted", "write_owner_resume_old_key_not_active"),
    ("new_account", "write_owner_resume_replacement_account_exists"),
    ("replacement_issued", "write_owner_resume_replacement_issuance_exists"),
    ("rotated_target", "write_owner_resume_target_versions_not_pristine"),
    ("partial_versions", "write_owner_resume_target_versions_not_pristine"),
])
def test_prepared_owner_resume_refuses_any_nonpristine_authority(writer, monkeypatch, defect, reason):
    writes, http = pristine_owner_fixture(writer, monkeypatch, defect)
    with pytest.raises(module.WriteDenied, match=reason):
        writes.verify_prepared_owner_resume_pristine()
    assert all(row[0] == "GET" for row in http.calls) and not list(writer[2].iterdir())


@pytest.mark.parametrize("operation", ["disable_old_principals", "delete_old_model_keys"])
def test_foreign_authentication_not_retired_prevents_any_google_isolation_write(writer, monkeypatch, operation):
    writes, http, workdir = writer
    writes.boundary.scope.principals = (module.native.NativePrincipal(
        next(iter(module.OLD_PRINCIPALS)), next(iter(module.OLD_PRINCIPALS.values()))),)
    writes.boundary.scope.keys = (module.native.NativeKey(next(iter(module.OLD_KEYS)), "uid", None),)
    def refused(*_):
        raise module.native.release.ReleaseDenied("provider_exact_invalid_key_denial_unavailable")
    monkeypatch.setattr(writes.boundary, "verify_foreign_model_retirement", refused)
    with pytest.raises(module.native.release.ReleaseDenied, match="provider_exact_invalid_key_denial_unavailable"):
        getattr(writes, operation)()
    assert http.calls == [] and not list(workdir.iterdir())
