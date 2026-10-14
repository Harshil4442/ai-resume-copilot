"""Pure native-action controls; no SDK, provider, SQL or manager-body access."""

from __future__ import annotations

import importlib
import json
import os
import sys
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
    boundary.scope = SimpleNamespace(keys=(), principals=(), source=object())
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
