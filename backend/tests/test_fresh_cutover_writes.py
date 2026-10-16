"""Pure native-action controls; no SDK, provider, SQL or manager-body access."""

from __future__ import annotations

import copy
import importlib
import json
import os
import sys
import urllib.parse
from contextlib import contextmanager
from dataclasses import replace
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
        self.session = SimpleNamespace(request=self.get_request)

    def get_request(self, method, url, *, data, headers, stream, hooks, timeout, allow_redirects):
        assert method == "GET" and data is None and stream is False and hooks == {}
        assert 0 < timeout <= 3 and allow_redirects is False
        response, raw = self.request(url, method=method, body=data, headers=headers, redirections=0)
        return SimpleNamespace(status_code=response.status, content=raw, headers={})

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
    writes.boundary.scope.principals = tuple(module.native.NativePrincipal(email, uid)
        for email, uid in module.OLD_PRINCIPALS.items())

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


class RestoreTarget(Target):
    """Synthetic permission state with transaction rollback and lost COMMIT."""

    def __init__(self):
        super().__init__()
        self.owner = [(16395, 16391, 2200, 6171)]
        self.roles = [(oid, name, True, False, False, False, False, False, False)
                      for name, oid, _ in module.TARGET_ROLES]
        self.memberships = [(24576, 16391, 10, True, False, False),
                            (24578, 16391, 10, True, False, False),
                            (24578, 16391, 16391, False, False, True),
                            (24580, 16391, 10, True, False, False)]
        self.database_create = set()
        self.schema_create = {24578}
        self.schema_usage = {24576, 24578, 24580}
        self.acl = [(16391, 0, "CONNECT", False), (16391, 0, "TEMPORARY", False),
                    (16391, 16391, "CONNECT", False), (16391, 16391, "CREATE", False),
                    (16391, 16391, "TEMPORARY", False)]
        self.schema_acl = [(["synthetic-owner=UC/synthetic-owner",
                             "synthetic-migrator=UC/synthetic-owner"],)]
        self.sessions = 0
        self.after_grant = lambda _: None
        self.unknown_commit = False
        self.ineffective_revoke = set()
        self.commits = self.rollbacks = 0

    @contextmanager
    def transaction(self):
        fields = ("database_create", "schema_create", "schema_usage", "acl", "schema_acl", "roles")
        before = {field: copy.deepcopy(getattr(self, field)) for field in fields}
        start = len(self.mutations)
        try:
            yield self
        except BaseException:
            for field, value in before.items():
                setattr(self, field, value)
            self.rollbacks += 1
            raise
        self.commits += 1
        if self.unknown_commit and len(self.mutations) > start:
            raise RuntimeError("synthetic-private-commit-result")

    @property
    def mutations(self):
        return [text for text in self.commands if text.startswith(("GRANT ", "REVOKE ", "ALTER ROLE"))]

    def execute(self, command, parameters=None):
        text = command if isinstance(command, str) else command.as_string(None)
        self.commands.append(text)
        if text.startswith(("SET ", "RESET ROLE")):
            return Result([])
        if text.startswith("SELECT d.oid,d.datdba"):
            return Result(copy.deepcopy(self.owner))
        if text.startswith("SELECT oid,rolname,rolcanlogin"):
            return Result(copy.deepcopy(self.roles))
        if "FROM pg_catalog.pg_auth_members" in text:
            return Result(copy.deepcopy(self.memberships))
        if text.startswith("SELECT oid,has_database_privilege"):
            return Result([(oid, oid in self.database_create, oid in self.schema_usage,
                            oid in self.schema_create) for oid in (24576, 24578, 24580)])
        if "SELECT count(*) FROM pg_catalog.pg_stat_activity" in text:
            return Result([(self.sessions,)])
        if "aclexplode" in text:
            return Result(copy.deepcopy(self.acl))
        if text.startswith("SELECT nspacl"):
            return Result(copy.deepcopy(self.schema_acl))
        if text.startswith("GRANT CREATE ON DATABASE neondb TO "):
            assert text == 'GRANT CREATE ON DATABASE neondb TO "hirewiz_migrator_20261013_r1"'
            self.database_create.add(24578)
            self.acl = sorted([*self.acl, (16391, 24578, "CREATE", False)])
            self.after_grant(self)
            return Result([])
        if text.startswith("SELECT version_num FROM public.alembic_version"):
            return Result([(module.native.release.CHAIN[-1],)])
        if text.startswith("ALTER ROLE "):
            assert text == 'ALTER ROLE "hirewiz_migrator_20261013_r1" NOLOGIN'
            self.roles = [(*row[:2], False, *row[3:]) if row[0] == 24578 else row for row in self.roles]
            return Result([])
        if text.startswith("REVOKE CREATE ON "):
            kind = "database" if " ON DATABASE " in text else "schema"
            if kind not in self.ineffective_revoke:
                (self.database_create if kind == "database" else self.schema_create).discard(24578)
            return Result([])
        if text.startswith("SELECT rolcanlogin"):
            return Result([(next(row[2] for row in self.roles if row[0] == 24578),)])
        if text.startswith("SELECT pg_catalog.has_schema_privilege"):
            return Result([(24578 in self.schema_create, *(oid in self.database_create
                                                          for oid in (24578, 24576, 24580)))])
        if text.startswith("SELECT d.oid,r.oid,current_user,session_user"):
            return Result([(16395, 24578, module.TARGET_ROLES[1][0], "hirewiz_bootstrap_20261013", 170000)])
        if text.startswith("SELECT pid,usesysid,datid,backend_type"):
            return Result([])
        raise AssertionError("Unexpected synthetic SQL: " + text)


def restore_target_fixture(writer, monkeypatch):
    writes, _, _ = writer
    bootstrap, _ = target_bind(writes, monkeypatch)
    connection, events = RestoreTarget(), []

    def connect(_):
        events.append("target-connect")
        return connection

    def isolated(boundary):
        assert boundary is writes.boundary
        events.append("cloud-isolated")

    def closure(boundary, source, *, replacements):
        assert boundary is writes.boundary and source is boundary.scope.source and replacements is False
        events.append("source-closure")
        return datetime.now(UTC)

    def empty(actual):
        assert actual is connection
        events.append("target-empty")

    monkeypatch.setattr(module.transfer.BoundDatabase, "connect", connect)
    monkeypatch.setattr(module.source_sql, "verify_cloud_isolated", isolated)
    monkeypatch.setattr(module.native.NativeFreshFenceBoundary, "_observe_closure", closure)
    monkeypatch.setattr(module.transfer, "_empty", empty)
    return bootstrap, connection, events


def test_empty_target_restore_grants_only_migrator_database_create_once_and_records_exact_acl(writer, monkeypatch):
    writes, http, workdir = writer
    bootstrap, connection, events = restore_target_fixture(writer, monkeypatch)
    before_acl, schema_acl = copy.deepcopy(connection.acl), copy.deepcopy(connection.schema_acl)
    result = module.prepare_target_restore(writes, bootstrap)
    assert connection.mutations == ['GRANT CREATE ON DATABASE neondb TO "hirewiz_migrator_20261013_r1"']
    assert connection.database_create == {24578} and connection.schema_acl == schema_acl
    assert result == {"database_oid": 16395, "grantee_oid": 24578, "grantor_oid": 16391,
                      "before_acl": before_acl,
                      "after_acl": sorted([*before_acl, (16391, 24578, "CREATE", False)]),
                      "runtime_database_create": False, "observer_database_create": False}
    assert events == ["cloud-isolated", "source-closure", "target-connect", "target-empty", "target-empty"]
    assert connection.commits == 1 and connection.closed and http.calls == []
    marker = workdir / "prepare-target-restore-create.started.json"
    assert json.loads(marker.read_text()) == {"phase": "prepare-target-restore-create", "automatic_replay": False}
    with pytest.raises(module.WriteDenied, match="^write_target_restore_already_attempted_reconcile_only$"):
        module.prepare_target_restore(writes, bootstrap)
    assert len(connection.mutations) == 1


@pytest.mark.parametrize(("field", "value"), [
    ("host", "synthetic-wrong.invalid"), ("port", 5433), ("database", "synthetic_other"),
    ("database_oid", 16396), ("cluster_sha256", "0" * 64),
    ("actor", "synthetic_other"), ("actor_oid", 16392),
])
def test_restore_grant_rejects_native_identity_drift_before_connection(writer, monkeypatch, field, value):
    writes, _, workdir = writer
    bootstrap, connection, events = restore_target_fixture(writer, monkeypatch)
    bootstrap = replace(bootstrap, identity=replace(bootstrap.identity, **{field: value}))
    with pytest.raises(module.WriteDenied, match="^write_target_native_identity_drift$"):
        module.prepare_target_restore(writes, bootstrap)
    assert events == [] and connection.commands == [] and not list(workdir.iterdir())


@pytest.mark.parametrize("field", ["retired_since", "scope"])
def test_restore_grant_requires_native_source_cut_before_connection(writer, monkeypatch, field):
    writes, _, workdir = writer
    bootstrap, connection, events = restore_target_fixture(writer, monkeypatch)
    setattr(writes.boundary, field, None)
    with pytest.raises(module.WriteDenied, match="^write_target_restore_native_cut_missing$"):
        module.prepare_target_restore(writes, bootstrap)
    assert events == ["cloud-isolated"] and connection.commands == [] and not list(workdir.iterdir())


@pytest.mark.parametrize("role_index", [0, 1, 2])
@pytest.mark.parametrize("attribute", [2, 3, 4, 5, 6, 7, 8])
def test_restore_grant_refuses_login_inheritance_or_elevated_role_drift_before_mutation(
    writer, monkeypatch, role_index, attribute,
):
    writes, _, workdir = writer
    bootstrap, connection, _ = restore_target_fixture(writer, monkeypatch)
    row = list(connection.roles[role_index])
    row[attribute] = not row[attribute]
    connection.roles[role_index] = tuple(row)
    with pytest.raises(module.WriteDenied, match="^write_target_restore_role_drift$"):
        module.prepare_target_restore(writes, bootstrap)
    assert connection.mutations == [] and connection.rollbacks == 1 and connection.closed
    assert not list(workdir.iterdir())


@pytest.mark.parametrize("defect", ["missing", "extra", "grantor", "admin", "inherit", "set"])
def test_restore_grant_refuses_changed_exact_membership_before_mutation(writer, monkeypatch, defect):
    writes, _, workdir = writer
    bootstrap, connection, _ = restore_target_fixture(writer, monkeypatch)
    if defect == "missing":
        connection.memberships.pop()
    elif defect == "extra":
        connection.memberships.append((24578, 24576, 16391, False, False, True))
    else:
        row = list(connection.memberships[2])
        index = {"grantor": 2, "admin": 3, "inherit": 4, "set": 5}[defect]
        row[index] = 10 if defect == "grantor" else not row[index]
        connection.memberships[2] = tuple(row)
    with pytest.raises(module.WriteDenied, match="^write_target_restore_membership_drift$"):
        module.prepare_target_restore(writes, bootstrap)
    assert connection.mutations == [] and not list(workdir.iterdir())


@pytest.mark.parametrize("role_oid", [24576, 24578, 24580])
def test_restore_grant_refuses_preexisting_database_create_for_any_role(writer, monkeypatch, role_oid):
    writes, _, workdir = writer
    bootstrap, connection, _ = restore_target_fixture(writer, monkeypatch)
    connection.database_create.add(role_oid)
    with pytest.raises(module.WriteDenied, match="^write_target_restore_permissions_drift$"):
        module.prepare_target_restore(writes, bootstrap)
    assert connection.mutations == [] and not list(workdir.iterdir())


@pytest.mark.parametrize("defect", ["grant_option", "foreign_grantee", "missing_original", "extra_runtime", "schema_acl"])
def test_restore_grant_rolls_back_nonexact_acl_delta_and_retains_attempt(writer, monkeypatch, defect):
    writes, _, workdir = writer
    bootstrap, connection, _ = restore_target_fixture(writer, monkeypatch)
    original = copy.deepcopy((connection.acl, connection.schema_acl))

    def change(actual):
        if defect == "schema_acl":
            actual.schema_acl.append((["synthetic-foreign=UC/synthetic-owner"],))
        elif defect == "missing_original":
            actual.acl.remove((16391, 0, "CONNECT", False))
        elif defect == "extra_runtime":
            actual.acl.append((16391, 24576, "CREATE", False))
            actual.database_create.add(24576)
        else:
            actual.acl.remove((16391, 24578, "CREATE", False))
            actual.acl.append((16391, 24578 if defect == "grant_option" else 24580,
                               "CREATE", defect == "grant_option"))
        actual.acl.sort()

    connection.after_grant = change
    with pytest.raises(module.WriteDenied, match="^write_target_restore_grant_unconfirmed$"):
        module.prepare_target_restore(writes, bootstrap)
    assert (connection.acl, connection.schema_acl) == original and connection.database_create == set()
    assert connection.rollbacks == 1 and connection.commits == 0 and connection.closed
    assert (workdir / "prepare-target-restore-create.started.json").is_file()
    with pytest.raises(module.WriteDenied, match="^write_target_restore_already_attempted_reconcile_only$"):
        module.prepare_target_restore(writes, bootstrap)
    assert len(connection.mutations) == 1


def test_unknown_restore_grant_commit_cannot_repeat_grant_or_reflect_error_body(writer, monkeypatch, capsys):
    writes, http, workdir = writer
    bootstrap, connection, _ = restore_target_fixture(writer, monkeypatch)
    connection.unknown_commit = True
    with pytest.raises(module.WriteDenied, match="^write_target_restore_unknown_reconcile_only$") as error:
        module.prepare_target_restore(writes, bootstrap)
    assert connection.database_create == {24578} and connection.closed
    assert str(error.value) == "write_target_restore_unknown_reconcile_only"
    assert (workdir / "prepare-target-restore-create.started.json").is_file()
    before = list(connection.commands)
    with pytest.raises(module.WriteDenied, match="^write_target_restore_already_attempted_reconcile_only$"):
        module.prepare_target_restore(writes, bootstrap)
    assert connection.commands == before and len(connection.mutations) == 1 and http.calls == []
    assert capsys.readouterr() == ("", "")
    assert "synthetic-private-commit-result" not in "".join(path.read_text() for path in workdir.iterdir())


def finalization_fixture(writer, monkeypatch):
    writes, http, _ = writer
    bootstrap, connection, _ = restore_target_fixture(writer, monkeypatch)
    connection.database_create.add(24578)
    release = module.native.release
    source = release.Plan(
        release="a" * 40, image_digest="sha256:" + "b" * 64,
        build_id="11111111-1111-1111-1111-111111111111", ci_run_id=7,
        runtime_secret="inert-old-runtime:2", migration_secret="inert-old-migration:3",
        observer_secret="inert-old-observer:4", database_owner_oid=16387,
        expense_secret="inert-expense-policy:5", expense_sha256="c" * 64,
        model_policy_secret="inert-model-policy:6", model_policy_sha256="d" * 64,
        model_key_secret="inert-model-key:7", model_primary="gemini-inert-model",
        model_api_base=release.MODEL_API_BASE,
        runtime_service_account=f"inert-runtime@{release.PROJECT}.iam.gserviceaccount.com",
        migration_service_account=f"inert-migration@{release.PROJECT}.iam.gserviceaccount.com",
        backup_uri=f"gs://{release.PROJECT}-hirewiz-application-artifacts/releases/inert.dump",
        inventory_path=writer[2] / "inert.json", inventory_sha256="e" * 64,
    )
    target = release.FreshTargetPlan(*(row[2] + ":2" for row in module.TARGET_ROLES))
    phases, state = [], {"value": "ENABLED"}
    resource = f"projects/{module.NUMBER}/secrets/{module.TARGET_ROLES[1][2]}/versions/2"

    def fences(actual, plan):
        assert actual is writes.boundary and plan is source

    def phase(actual, plan, destination, schema, *, phase):
        assert actual is writes.boundary and plan is source and destination is target
        assert schema == release.CHAIN[-1]
        if phase == "serving":
            assert state["value"] == "DISABLED" and connection.closed
        phases.append(phase)

    def respond(method, url, body):
        if method == "POST":
            assert url.endswith(resource + ":disable") and body == {"etag": "synthetic-selected-version"}
            assert connection.closed and connection.database_create == connection.schema_create == set()
            state["value"] = "DISABLED"
        else:
            assert method == "GET" and url.endswith(resource)
        return 200, {"name": resource, "etag": "synthetic-selected-version", "state": state["value"]}

    monkeypatch.setattr(module.native.NativeFreshFenceBoundary, "verify_fences", fences)
    monkeypatch.setattr(module.native.NativeFreshFenceBoundary, "verify_fresh_database_phase", phase)
    http.respond = respond
    return bootstrap, connection, source, target, phases


def test_finalization_removes_both_create_grants_before_disabling_only_selected_migrator_secret(writer, monkeypatch):
    writes, http, workdir = writer
    bootstrap, connection, source, target, phases = finalization_fixture(writer, monkeypatch)
    module.finalize_target_migrator(writes, bootstrap, source, target)
    assert connection.mutations == [
        'ALTER ROLE "hirewiz_migrator_20261013_r1" NOLOGIN',
        'REVOKE CREATE ON SCHEMA public FROM "hirewiz_migrator_20261013_r1"',
        'REVOKE CREATE ON DATABASE neondb FROM "hirewiz_migrator_20261013_r1"',
    ]
    assert connection.database_create == connection.schema_create == set()
    assert next(row[2] for row in connection.roles if row[0] == 24578) is False
    assert [method for method, _, _ in http.calls] == ["GET", "POST", "GET"]
    assert phases == ["migration", "serving"]
    assert {path.name for path in workdir.iterdir()} == {
        "finalize-target-migrator-sql.started.json", "disable-selected-migration-version-2.started.json"}


@pytest.mark.parametrize("remaining", ["schema", "database", "runtime", "observer"])
def test_finalization_refuses_any_remaining_create_authority_without_disabling_secret(writer, monkeypatch, remaining):
    writes, http, workdir = writer
    bootstrap, connection, source, target, phases = finalization_fixture(writer, monkeypatch)
    if remaining in {"schema", "database"}:
        connection.ineffective_revoke.add(remaining)
    else:
        connection.database_create.add(24576 if remaining == "runtime" else 24580)
    with pytest.raises(module.WriteDenied, match="^write_migrator_nologin_nocreate_unconfirmed$"):
        module.finalize_target_migrator(writes, bootstrap, source, target)
    assert connection.rollbacks == 1 and connection.closed and phases == ["migration"]
    assert [method for method, _, _ in http.calls] == ["GET"]
    assert {path.name for path in workdir.iterdir()} == {"finalize-target-migrator-sql.started.json"}


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
    writes.boundary.scope.principals = tuple(module.native.NativePrincipal(email, uid)
        for email, uid in module.OLD_PRINCIPALS.items())
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
    assert len(http.calls) == 20 and all(row[0] == "GET" for row in http.calls)
    assert any(url.endswith("/serviceAccounts/hirewiz-worker@" + module.PROJECT + ".iam.gserviceaccount.com")
               for _, url, _ in http.calls)
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


@pytest.mark.parametrize("operation", ["pristine", "disable"])
@pytest.mark.parametrize("defect", ["missing_worker", "worker_uid", "unknown_actor", "duplicate_worker"])
@pytest.mark.parametrize("worker", ["hirewiz-worker", "hirewiz-employer-worker"])
def test_seven_actor_scope_must_be_complete_and_exact_before_any_native_action(
    writer, monkeypatch, operation, defect, worker,
):
    writes, http = pristine_owner_fixture(writer, monkeypatch)
    worker += "@ai-resume-parser-482412.iam.gserviceaccount.com"
    principals = list(writes.boundary.scope.principals)
    if defect == "missing_worker":
        principals = [principal for principal in principals if principal.email != worker]
    elif defect == "worker_uid":
        principals = [module.native.NativePrincipal(principal.email, "900000000000000000001")
                      if principal.email == worker else principal for principal in principals]
    elif defect == "unknown_actor":
        principals.append(module.native.NativePrincipal("unknown@" + module.PROJECT + ".iam.gserviceaccount.com",
                                                       "900000000000000000002"))
    else:
        principals.append(next(principal for principal in principals if principal.email == worker))
    writes.boundary.scope.principals = tuple(principals)
    reason = "write_owner_resume_captured_authority_incomplete" if operation == "pristine" else "write_old_principal_unmapped"
    with pytest.raises(module.WriteDenied, match=reason):
        (writes.verify_prepared_owner_resume_pristine if operation == "pristine" else writes.disable_old_principals)()
    assert http.calls == [] and not list(writer[2].iterdir())


@pytest.mark.parametrize("defect", ["uid", "enabled"])
@pytest.mark.parametrize("worker", ["hirewiz-worker", "hirewiz-employer-worker"])
def test_prepared_owner_pristine_rechecks_actual_workers_uid_and_enabled_state(
    writer, monkeypatch, defect, worker,
):
    writes, http = pristine_owner_fixture(writer, monkeypatch)
    original = http.respond
    def respond(method, url, body):
        status, row = original(method, url, body)
        if url.endswith("/serviceAccounts/" + worker + "@" + module.PROJECT + ".iam.gserviceaccount.com"):
            row["uniqueId" if defect == "uid" else "disabled"] = "900000000000000000001" if defect == "uid" else True
        return status, row
    http.respond = respond
    reason = "write_native_account_identity_drift" if defect == "uid" else "write_owner_resume_old_principal_not_enabled"
    with pytest.raises(module.WriteDenied, match=reason):
        writes.verify_prepared_owner_resume_pristine()
    assert all(method == "GET" for method, _, _ in http.calls) and not list(writer[2].iterdir())


def seven_actor_retirement_fixture(writer, monkeypatch, *, unknown_worker=None):
    writes, http, workdir = writer
    writes.boundary.scope.principals = tuple(module.native.NativePrincipal(email, uid)
        for email, uid in module.OLD_PRINCIPALS.items())
    monkeypatch.setattr(writes.boundary, "verify_admission_closed", lambda: None)
    states = {email: False for email in module.OLD_PRINCIPALS}
    def respond(method, url, _body):
        clean = url.split("?")[0]
        if "/triggers/" in clean:
            return 200, held_trigger()
        if clean.endswith("/builds/" + module.LAST_OPERATOR_BUILD):
            return 200, {"id": module.LAST_OPERATOR_BUILD, "projectId": module.PROJECT,
                         "name": "projects/" + module.NUMBER + "/locations/us-central1/builds/" + module.LAST_OPERATOR_BUILD,
                         "serviceAccount": held_trigger()["serviceAccount"], "status": "SUCCESS"}
        if clean.endswith("/builds"):
            return 200, {}
        if method == "POST":
            assert clean.endswith(":disable")
            uid = clean.rsplit("/", 1)[-1].removesuffix(":disable")
            email = next(email for email, expected_uid in module.OLD_PRINCIPALS.items() if expected_uid == uid)
            states[email] = True
            if email == unknown_worker:
                raise RuntimeError("synthetic-unknown-worker-disable")
            return 200, {}
        email = clean.rsplit("/", 1)[-1]
        return 200, {"email": email, "uniqueId": module.OLD_PRINCIPALS[email], "disabled": states[email]}
    http.respond = respond
    return writes, http, workdir, states


def test_all_seven_actual_native_actors_are_disabled_with_exact_uid_and_durable_attempt(writer, monkeypatch):
    writes, http, workdir, states = seven_actor_retirement_fixture(writer, monkeypatch)
    observed = writes.disable_old_principals()
    worker = "hirewiz-worker@ai-resume-parser-482412.iam.gserviceaccount.com"
    assert dict((account.email, account.uid) for account in observed) == module.OLD_PRINCIPALS
    assert len(observed) == 7 and module.OLD_PRINCIPALS[worker] == "115123377998883908933"
    assert module.OLD_PRINCIPALS["hirewiz-employer-worker@" + module.PROJECT + ".iam.gserviceaccount.com"] == "105594666905475527509"
    assert all(states.values()) and len(list(workdir.glob("disable-*.started.json"))) == 7
    mutations = [(index, url) for index, (method, url, _) in enumerate(http.calls) if method == "POST"]
    assert len(mutations) == 7 and any(url.endswith("/115123377998883908933:disable") for _, url in mutations)
    assert any(url.endswith("/105594666905475527509:disable") for _, url in mutations)
    compute_index = next(index for index, url in mutations if url.endswith("/115689643755778240830:disable"))
    before_compute = http.calls[compute_index - 4:compute_index]
    assert all(method == "GET" for method, _, _ in before_compute)
    assert "/triggers/" in before_compute[0][1] and "/builds/" in before_compute[1][1]
    assert all("/builds?" in url for _, url, _ in before_compute[2:])


@pytest.mark.parametrize("worker", ["hirewiz-worker", "hirewiz-employer-worker"])
def test_unknown_worker_disable_keeps_its_attempt_and_never_replays_any_disable(writer, monkeypatch, worker):
    worker += "@ai-resume-parser-482412.iam.gserviceaccount.com"
    writes, http, workdir, states = seven_actor_retirement_fixture(writer, monkeypatch, unknown_worker=worker)
    with pytest.raises(module.WriteDenied, match="write_native_result_unknown_reconcile_only"):
        writes.disable_old_principals()
    marker = workdir / ("disable-" + module.hashlib.sha256(worker.encode()).hexdigest()[:16] + ".started.json")
    assert marker.is_file() and states[worker]
    count = sum(method == "POST" for method, _, _ in http.calls)
    with pytest.raises(module.WriteDenied, match="write_old_principal_already_disabled_reconcile_only"):
        writes.disable_old_principals()
    assert sum(method == "POST" for method, _, _ in http.calls) == count
    assert sum(method == "POST" and url.endswith("/" + module.OLD_PRINCIPALS[worker] + ":disable")
               for method, url, _ in http.calls) == 1


def test_delayed_iam_disabled_readback_polls_reads_without_repeating_posts(writer, monkeypatch):
    writes, http, workdir, states = seven_actor_retirement_fixture(writer, monkeypatch)
    original = http.respond
    reads = dict.fromkeys(states, 0)
    sleeps = []
    monkeypatch.setattr(module.time, "sleep", sleeps.append)

    def delayed(method, url, body):
        status, row = original(method, url, body)
        email = url.rsplit("/", 1)[-1]
        if method == "GET" and email in states and states[email]:
            reads[email] += 1
            if reads[email] <= 3:
                row["disabled"] = False
        return status, row

    http.respond = delayed
    assert len(writes.disable_old_principals()) == 7
    assert set(reads.values()) == {4}
    assert sleeps == [0.5] * 21
    assert len(list(workdir.glob("disable-*.started.json"))) == 7
    assert sum(method == "POST" for method, _, _ in http.calls) == 7


def test_unconfirmed_iam_disable_stops_at_read_bound_and_never_replays_post(writer, monkeypatch):
    writes, http, workdir, states = seven_actor_retirement_fixture(writer, monkeypatch)
    original = http.respond
    monkeypatch.setattr(module.time, "sleep", lambda _: None)

    def unconfirmed(method, url, body):
        status, row = original(method, url, body)
        if method == "GET" and url.rsplit("/", 1)[-1] in states:
            row["disabled"] = False
        return status, row

    http.respond = unconfirmed
    with pytest.raises(module.WriteDenied, match="write_old_principal_disable_unconfirmed"):
        writes.disable_old_principals()
    assert sum(states.values()) == 1
    assert sum(method == "POST" for method, _, _ in http.calls) == 1
    assert len(list(workdir.glob("disable-*.started.json"))) == 1
    first = next(iter(states))
    assert sum(method == "GET" and url.endswith("/serviceAccounts/" + first)
               for method, url, _ in http.calls) == 17
    with pytest.raises(module.WriteDenied, match="write_already_attempted_or_private_directory_refused"):
        writes.disable_old_principals()
    assert sum(method == "POST" for method, _, _ in http.calls) == 1


def test_iam_disabled_readback_uid_drift_is_not_polled_away(writer, monkeypatch):
    writes, http, _, states = seven_actor_retirement_fixture(writer, monkeypatch)
    original = http.respond
    sleeps = []
    monkeypatch.setattr(module.time, "sleep", sleeps.append)

    def drift(method, url, body):
        status, row = original(method, url, body)
        email = url.rsplit("/", 1)[-1]
        if method == "GET" and email in states and states[email]:
            row["uniqueId"] = "900000000000000000001"
        return status, row

    http.respond = drift
    with pytest.raises(module.WriteDenied, match="write_native_account_identity_drift"):
        writes.disable_old_principals()
    assert sum(method == "POST" for method, _, _ in http.calls) == 1
    assert sleeps == []


def test_iam_readback_does_not_refresh_the_original_action_deadline(writer, monkeypatch):
    writes, http, _, states = seven_actor_retirement_fixture(writer, monkeypatch)
    original = http.respond
    clock = [0.0]
    monkeypatch.setattr(module.time, "monotonic", lambda: clock[0])

    def late(method, url, body):
        status, row = original(method, url, body)
        email = url.rsplit("/", 1)[-1]
        if method == "GET" and email in states and states[email]:
            clock[0] = 56.0
        return status, row

    http.respond = late
    with pytest.raises(module.WriteDenied, match="write_action_deadline"):
        writes.disable_old_principals()
    assert sum(method == "POST" for method, _, _ in http.calls) == 1


def test_builder_restoration_requires_the_same_exact_seven_retired_actors():
    builder = importlib.import_module("github_builder")
    assert builder.OLD_PRINCIPALS == module.OLD_PRINCIPALS
    assert len(builder.OLD_PRINCIPALS) == 7
    assert builder.OLD_PRINCIPALS["hirewiz-worker@ai-resume-parser-482412.iam.gserviceaccount.com"] == "115123377998883908933"
    assert builder.OLD_PRINCIPALS["hirewiz-employer-worker@ai-resume-parser-482412.iam.gserviceaccount.com"] == "105594666905475527509"
