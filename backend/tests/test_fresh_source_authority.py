"""Pure source-transition controls: no PostgreSQL, SDK, credentials or app imports."""
from __future__ import annotations

import importlib
import json
import os
import sys
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(os.environ.get("HIREWIZ_ACTION_TEST_ROOT", str(Path(__file__).resolve().parents[2])))
sys.path[:0] = [str(Path(__file__).parent), str(ROOT / "infra/gcp"), str(ROOT / "backend")]
module = importlib.import_module("fresh_source_authority")


@pytest.mark.parametrize("binding", ["prefer", "require"])
def test_original_source_preserves_channel_binding(monkeypatch, binding):
    boundary = module.native.NativeFreshFenceBoundary.__new__(module.native.NativeFreshFenceBoundary)
    boundary.reads = object()
    files = module.LinuxExecutionFiles()
    monkeypatch.setattr(module.LinuxExecutionFiles, "validate", lambda self: None)
    uri = (f"postgresql://{module.OLD_NAME}:synthetic-password@{module.SOURCE_HOST}/neondb"
           f"?sslmode=require&channel_binding={binding}")
    monkeypatch.setattr(module.retained, "_secret_value", lambda *_: ("ENABLED", uri))
    origin = module.original_owner(boundary, files)
    assert origin.identity.channel_binding == binding
    assert origin.environment(readonly=True)["PGCHANNELBINDING"] == binding
    assert origin.environment(readonly=True)["PGSSLMODE"] == "verify-full"


def test_original_source_refuses_disabled_channel_binding(monkeypatch):
    boundary = module.native.NativeFreshFenceBoundary.__new__(module.native.NativeFreshFenceBoundary)
    boundary.reads = object()
    monkeypatch.setattr(module.LinuxExecutionFiles, "validate", lambda self: None)
    uri = (f"postgresql://{module.OLD_NAME}:synthetic-password@{module.SOURCE_HOST}/neondb"
           "?sslmode=require&channel_binding=disable")
    monkeypatch.setattr(module.retained, "_secret_value", lambda *_: ("ENABLED", uri))
    with pytest.raises(module.SourceActionDenied, match="source_original_secret_target"):
        module.original_owner(boundary, module.LinuxExecutionFiles())


class Result:
    def __init__(self, rows):
        self.rows = rows

    def fetchall(self):
        return self.rows

    def fetchone(self):
        return self.rows[0] if self.rows else None


class Connection:
    def __init__(self):
        self.roles = [(10, "cloud_admin", True, True, True, True, True, True, True),
            (6181, "pg_read_all_data", False, True, False, False, False, False, False),
            (16384, "neon_superuser", False, True, False, True, True, False, False),
            (16387, "neondb_owner", True, True, False, True, True, False, False),
            (1884178, module.RUNTIME_NAME, False, False, False, False, False, False, False),
            (1884180, module.MIGRATOR_NAME, False, False, False, False, False, False, False),
            (1884182, module.OBSERVER_NAME, False, False, False, False, False, False, False)]
        self.members = [(6181, 16384, 10, True, True, True), (1884178, 16387, 10, True, False, False), (1884180, 16387, 10, True, False, False), (1884182, 16387, 10, True, False, False)]
        self.owner = 16387
        self.created = False
        self.closed = False
        self.fail_commit = False
        self.default_acl = 0
        self.dependencies = 0
        self.otherdb = 0
        self.session_rows = []
        self.export_settable = True
        self.commands = []

    @contextmanager
    def transaction(self):
        yield self
        if self.fail_commit and self.created:
            raise RuntimeError("synthetic private driver body must be suppressed")

    def close(self):
        self.closed = True

    def execute(self, command, params=None):
        text = command if isinstance(command, str) else command.as_string(None)
        self.commands.append(text)
        if text == module.ROLE_ROWS:
            return Result(self.roles[:])
        if text == module.MEMBERS:
            return Result(self.members[:])
        if text.startswith("SELECT oid,rolname,rolsuper FROM"):
            return Result([(row[0], row[1], row[4]) for row in self.roles if row[0] in params])
        if text.startswith("SELECT current_user,session_user,pg_catalog.pg_has_role"):
            return Result([(module.OLD_NAME, module.OLD_NAME, self.export_settable)])
        if text.startswith("SELECT roleid,member,admin_option,inherit_option,set_option"):
            return Result([(row[0], row[1], *row[3:]) for row in self.members if row[:2] == params])
        if text.startswith("SELECT rolname,rolcanlogin,rolinherit,rolsuper,rolcreatedb"):
            return Result([(module.OBSERVER_NAME, True, False, False, False, False, False, False)])
        if text.startswith("SELECT roleid,member,grantor,admin_option,inherit_option,set_option FROM pg_catalog.pg_auth_members WHERE member="):
            return Result([row for row in self.members if row[1] == module.OBSERVER_OID and any(row[3:])])
        if text.startswith('GRANT "pg_read_all_data"'):
            self.members.append((6181, module.OBSERVER_OID, 16384, False, True, False))
        if text.startswith("SELECT count(*) FROM pg_catalog.pg_auth_members WHERE roleid=%s AND member=%s"):
            return Result([(sum(row[:2] == params for row in self.members),)])
        if text.startswith("SELECT oid,rolname,rolcanlogin"):
            return Result([row for row in self.roles if row[0] == params[0] or row[1] == params[1]])
        if text.startswith("SELECT roleid,member,grantor"):
            return Result([row for row in self.members if params[0] in row[:3]])
        if text.startswith("SELECT (SELECT count(*) FROM pg_catalog.pg_shdepend"):
            return Result([(self.dependencies, 0, len(self.session_rows), 0)])
        if text.startswith('CREATE ROLE '):
            self.created = True
            self.roles.append((2000000, module.OWNER_NAME, False, False, False, False, False, False, False))
            self.members.append((2000000, 16387, 10, True, False, False))
        if text.startswith('REASSIGN OWNED '):
            self.owner = 2000000
        if "SELECT d.oid,d.datdba,n.oid" in text:
            return Result([(16391, self.owner, 2200, 6171)])
        if text.startswith("SELECT version_num"):
            return Result([("20261008_0009",)])
        if "FROM pg_catalog.pg_database WHERE datdba" in text:
            return Result([(self.otherdb,)])
        if "FROM pg_catalog.pg_default_acl" in text:
            return Result([(self.default_acl,)])
        if "SELECT pg_catalog.has_function_privilege" in text:
            return Result([(True,)])
        if "SELECT d.oid,r.oid,current_user,session_user" in text:
            return Result([(16391, 2000000, module.OWNER_NAME, module.OLD_NAME, 170011)])
        if "SELECT datdba FROM pg_catalog.pg_database" in text:
            return Result([(self.owner,)])
        if "SELECT oid,rolname FROM pg_catalog.pg_roles" in text:
            return Result([(16387, module.OLD_NAME)])
        if "SELECT pid,usesysid,datid,backend_type" in text:
            return Result(self.session_rows)
        if "SELECT pg_catalog.pg_terminate_backend" in text:
            assert params == (12345, 16387, 16391)
            return Result([(True,)])
        if text.startswith("SELECT count(*)"):
            return Result([(self.dependencies if "classid<>" in text else 0,)])
        return Result([])


@pytest.fixture
def prepared(tmp_path, monkeypatch):
    tmp_path.chmod(0o700)
    identity = module.transfer.NativeDatabaseIdentity(module.SOURCE_HOST, 5432, "neondb",
        module.SOURCE_CLUSTER, 16391, module.OLD_NAME, 16387, "/synthetic-public-ca.pem")
    origin = module.transfer.BoundDatabase(identity, "synthetic-source-password")
    connection = Connection()
    monkeypatch.setattr(module.transfer.BoundDatabase, "connect", lambda self: connection)
    monkeypatch.setattr(module, "verify_source_quiescent", lambda *_: None)
    return origin, connection, tmp_path


def test_one_additive_owner_native_oid_and_exact_memberships(prepared):
    origin, connection, workdir = prepared
    owner = module.prepare_owner(origin, workdir)
    assert owner.oid == 2000000 and connection.owner == 16387
    assert connection.members[-1] == (2000000, 16387, 10, True, False, False)
    assert connection.closed and (workdir / "source-owner-create.started.json").is_file()
    assert not any(text.startswith(("REASSIGN", "ALTER ROLE", "DROP", "INSERT", "UPDATE", "DELETE")) for text in connection.commands)


def resume_payload():
    return dict(module.RESUME_VALUES)


@pytest.mark.parametrize("change", [
    {"owner_oid": True}, {"owner_oid": 2080768.0}, {"owner_oid": 2080769},
    {"prior_nonce": "f" * 32}, {"prior_release": "f" * 40},
    {"prior_operator_image": "unrelated"}, {"prior_config_sha256": "f" * 64},
])
def test_resume_instruction_rejects_other_roles_operations_and_untyped_oids(change):
    with pytest.raises(module.SourceActionDenied, match="source_resume_fixed_prior_identity"):
        module.PreparedOwnerResume.from_payload({**resume_payload(), **change})


def test_resume_instruction_requires_all_and_only_explicit_fixed_fields():
    value = resume_payload()
    assert module.PreparedOwnerResume.from_payload(value).volume.endswith(value["prior_nonce"])
    for malformed in (None, {}, {**value, "allow_replay": True},
                      {key: item for key, item in value.items() if key != "owner_oid"}):
        with pytest.raises(module.SourceActionDenied, match="source_resume_instruction_fields"):
            module.PreparedOwnerResume.from_payload(malformed)


@pytest.mark.parametrize("drift", [None, "writable", "later_phase", "marker_body", "file_owner", "hardlink", "symlink", "directory_owner", "directory_mode", "platform"])
def test_prior_marker_requires_exact_owned_readonly_first_phase_only(tmp_path, monkeypatch, drift):
    marker = tmp_path / "source-owner-create.started.json"
    marker.write_bytes(json.dumps({"phase": "source-owner-create", "automatic_replay": False}).encode() + b"\n")
    marker.chmod(0o600)
    if drift == "later_phase":
        (tmp_path / "source-owner-transition.started.json").write_text("{}")
    elif drift == "marker_body":
        marker.write_text('{"phase":"source-owner-create","automatic_replay":true}\n')
    elif drift == "hardlink":
        os.link(marker, tmp_path.parent / (tmp_path.name + "-outside-hardlink"))
    elif drift == "symlink":
        target = tmp_path.parent / (tmp_path.name + "-outside-marker")
        marker.rename(target)
        marker.symlink_to(target)

    class PriorDirectory:
        def lstat(self):
            return SimpleNamespace(st_uid=501 if drift == "directory_owner" else 0,
                                   st_mode=0o040755 if drift == "directory_mode" else 0o040700)

        def iterdir(self):
            return tmp_path.iterdir()

        def __truediv__(self, name):
            return tmp_path / name

    class MetadataOS:
        def __getattr__(self, name):
            return getattr(os, name)

        def statvfs(self, _):
            return SimpleNamespace(f_flag=0 if drift == "writable" else os.ST_RDONLY)

        def fstat(self, descriptor):
            real = os.fstat(descriptor)
            return SimpleNamespace(st_mode=real.st_mode, st_uid=501 if drift == "file_owner" else 0,
                                   st_nlink=real.st_nlink, st_size=real.st_size)

    monkeypatch.setattr(module, "PRIOR_WORK", PriorDirectory())
    monkeypatch.setattr(module, "os", MetadataOS())
    monkeypatch.setattr(module, "sys", SimpleNamespace(platform="darwin" if drift == "platform" else "linux"))
    resume = module.PreparedOwnerResume.from_payload(resume_payload())
    if drift:
        with pytest.raises(module.SourceActionDenied):
            module.verify_prior_owner_preparation(resume)
    else:
        module.verify_prior_owner_preparation(resume)


@pytest.fixture
def resumable(prepared, monkeypatch):
    origin, connection, workdir = prepared
    resume = module.PreparedOwnerResume.from_payload(resume_payload())
    connection.roles.append((resume.owner_oid, module.OWNER_NAME, False, False, False, False, False, False, False))
    connection.members.append((resume.owner_oid, module.OLD_OID, 10, True, False, False))
    boundary = object.__new__(module.native.NativeFreshFenceBoundary)
    boundary.scope = SimpleNamespace(source=SimpleNamespace(database_owner_oid=resume.owner_oid))
    boundary.retired_since = boundary._source_isolated_at = None
    monkeypatch.setattr(module, "verify_prior_owner_preparation", lambda _: None)
    return origin, connection, workdir, resume, boundary


def test_native_resume_reuses_actual_unused_role_without_create_or_sql_write(resumable):
    origin, connection, workdir, resume, boundary = resumable
    owner = module.resume_prepared_owner(origin, boundary, resume, workdir)
    assert owner.oid == resume.owner_oid and connection.owner == module.OLD_OID
    assert connection.closed and not connection.created
    assert json.loads((workdir / "source-owner-resume.started.json").read_text()) == {
        "phase": "source-owner-resume", "automatic_replay": False,
    }
    assert not (workdir / "source-owner-create.started.json").exists()
    assert not any(text.startswith(("CREATE", "ALTER", "DROP", "REASSIGN", "GRANT", "REVOKE", "INSERT", "UPDATE", "DELETE", "SET TRANSACTION READ WRITE")) for text in connection.commands)


@pytest.mark.parametrize("drift", ["login", "oid", "membership_set", "membership_extra", "dependency", "session", "schema_owner"])
def test_resume_rejects_used_changed_or_partially_transitioned_role(resumable, drift):
    origin, connection, workdir, resume, boundary = resumable
    if drift == "login":
        connection.roles[-1] = (*connection.roles[-1][:2], True, *connection.roles[-1][3:])
    elif drift == "oid":
        connection.roles[-1] = (2080769, *connection.roles[-1][1:])
    elif drift == "membership_set":
        connection.members[-1] = (*connection.members[-1][:5], True)
    elif drift == "membership_extra":
        connection.members.append((resume.owner_oid, 10, 10, True, False, False))
    elif drift == "dependency":
        connection.dependencies = 1
    elif drift == "session":
        connection.session_rows = [(12345, resume.owner_oid, 16391, "client backend")]
    else:
        connection.owner = resume.owner_oid
    with pytest.raises(module.SourceActionDenied):
        module.resume_prepared_owner(origin, boundary, resume, workdir)
    assert not list(workdir.iterdir()) and not connection.created


@pytest.mark.parametrize("change", ["scope", "oid", "retired", "isolated"])
def test_resume_requires_unretired_full_native_scope_before_read(resumable, change):
    origin, connection, workdir, resume, boundary = resumable
    if change == "scope":
        boundary.scope = None
    elif change == "oid":
        boundary.scope.source.database_owner_oid = 2080769
    elif change == "retired":
        boundary.retired_since = object()
    else:
        boundary._source_isolated_at = object()
    with pytest.raises(module.SourceActionDenied, match="source_resume_fresh_precut_scope_required"):
        module.resume_prepared_owner(origin, boundary, resume, workdir)
    assert connection.commands == [] and not list(workdir.iterdir())


def test_native_resume_marker_never_replays(resumable):
    origin, connection, workdir, resume, boundary = resumable
    module.resume_prepared_owner(origin, boundary, resume, workdir)
    with pytest.raises(module.SourceActionDenied, match="source_resume_current_phase_exists_reconcile_only"):
        module.resume_prepared_owner(origin, boundary, resume, workdir)
    assert not connection.created


def test_resume_rejects_any_current_partial_phase_before_source_read(resumable):
    origin, connection, workdir, resume, boundary = resumable
    (workdir / "unresolved-provider-write.started.json").write_text("{}")
    with pytest.raises(module.SourceActionDenied, match="source_resume_current_phase_exists_reconcile_only"):
        module.resume_prepared_owner(origin, boundary, resume, workdir)
    assert connection.commands == [] and not connection.created


def test_unknown_commit_keeps_once_marker_and_never_replays(prepared):
    origin, connection, workdir = prepared
    connection.fail_commit = True
    with pytest.raises(module.SourceActionDenied) as error:
        module.prepare_owner(origin, workdir)
    assert str(error.value) == "source_owner_create_unknown_reconcile_only"
    assert (workdir / "source-owner-create.started.json").is_file()
    before = sum(text.startswith("CREATE ROLE") for text in connection.commands)
    with pytest.raises(module.SourceActionDenied):
        module.prepare_owner(origin, workdir)
    assert sum(text.startswith("CREATE ROLE") for text in connection.commands) == before


def test_wrong_native_origin_refuses_before_connection(prepared, monkeypatch):
    origin, connection, workdir = prepared
    different = module.transfer.BoundDatabase(module.transfer.NativeDatabaseIdentity(
        module.SOURCE_HOST, 5432, "neondb", "f" * 64, 16391, module.OLD_NAME, 16387, "/synthetic-ca"), "synthetic")
    with pytest.raises(module.SourceActionDenied, match="source_original_native_identity"):
        module.prepare_owner(different, workdir)
    assert connection.commands == [] and not list(workdir.iterdir())


def test_other_database_dependency_refuses_without_mutation(prepared):
    origin, connection, workdir = prepared
    connection.otherdb = 1
    with pytest.raises(module.SourceActionDenied, match="source_other_database_dependencies"):
        module.prepare_owner(origin, workdir)
    assert not connection.created and not list(workdir.iterdir())


def test_native_isolation_required_before_source_transition(prepared, monkeypatch):
    origin, connection, workdir = prepared
    owner = module.prepare_owner(origin, workdir)
    def refuse(_):
        raise module.SourceActionDenied("source_old_principal_not_isolated")
    monkeypatch.setattr(module, "verify_cloud_isolated", refuse)
    before = len(connection.commands)
    with pytest.raises(module.SourceActionDenied, match="source_old_principal_not_isolated"):
        module.transition_source(origin, owner, object(), workdir, module.new_source_passwords())
    assert len(connection.commands) == before and not (workdir / "source-owner-transition.started.json").exists()


def test_ownership_acl_change_and_only_exact_old_sessions(prepared, monkeypatch):
    origin, connection, workdir = prepared
    owner = module.prepare_owner(origin, workdir)
    monkeypatch.setattr(module, "verify_cloud_isolated", lambda _: None)
    connection.session_rows = [(12345, 16387, 16391, "client backend")]
    passwords = module.new_source_passwords()
    result = module.transition_source(origin, owner, object(), workdir, passwords)
    assert result.owner.oid == connection.owner == 2000000
    assert result.provider_role_deletion_required and result.observer_password == passwords.observer
    assert len(passwords.observer) == 64
    assert any(text.startswith('REASSIGN OWNED BY "neondb_owner"') for text in connection.commands)
    assert any("REVOKE ALL PRIVILEGES ON ALL TABLES" in text for text in connection.commands)
    assert "ALTER ROLE neondb_owner PASSWORD NULL" in connection.commands
    assert not any(text.startswith(("DROP", "INSERT", "UPDATE", "DELETE")) for text in connection.commands)
    assert "synthetic-source-password" not in (workdir / "source-owner-transition.started.json").read_text()
    assert passwords.observer not in repr(result) and passwords.observer not in repr(passwords)


def test_default_acl_refuses_without_drop_owned_or_write(prepared, monkeypatch):
    origin, connection, workdir = prepared
    owner = module.prepare_owner(origin, workdir)
    monkeypatch.setattr(module, "verify_cloud_isolated", lambda _: None)
    connection.default_acl = 1
    with pytest.raises(module.SourceActionDenied, match="source_old_default_acl_requires_exact_cleanup"):
        module.transition_source(origin, owner, object(), workdir, module.new_source_passwords())
    assert connection.owner == 16387 and not (workdir / "source-owner-transition.started.json").exists()


@pytest.mark.parametrize("drift", ["managed_oid", "managed_superuser", "unreachable", "missing_admin", "existing_export"])
def test_export_authority_drift_refuses_before_transition_marker_or_mutation(prepared, monkeypatch, drift):
    origin, connection, workdir = prepared
    owner = module.prepare_owner(origin, workdir)
    monkeypatch.setattr(module, "verify_cloud_isolated", lambda _: None)
    if drift == "managed_oid":
        connection.roles = [row for row in connection.roles if row[0] != 16384]
    elif drift == "managed_superuser":
        connection.roles = [(*row[:4], True, *row[5:]) if row[0] == 16384 else row for row in connection.roles]
    elif drift == "unreachable":
        connection.export_settable = False
    elif drift == "missing_admin":
        connection.members = [row for row in connection.members if row[:2] != (6181, 16384)]
    else:
        connection.members.append((6181, module.OBSERVER_OID, 16384, False, False, False))
    before = len(connection.commands)
    with pytest.raises(module.SourceActionDenied, match="source_export_"):
        module.transition_source(origin, owner, object(), workdir, module.new_source_passwords())
    assert connection.owner == module.OLD_OID and not (workdir / "source-owner-transition.started.json").exists()
    assert not any(command.startswith(("GRANT", "REVOKE", "REASSIGN", "ALTER")) for command in connection.commands[before:])


def test_provider_delete_requires_native_zero_dependencies_and_sessions(prepared):
    origin, connection, _ = prepared
    connection.owner = 2000000
    connection.members.append((6181, module.OBSERVER_OID, 16384, False, True, False))
    owner = module.PreparedOwner(2000000)
    transition = module.SQLTransition(owner, "synthetic-observer")
    observer = module.observer_target(origin, transition)
    module.verify_provider_delete_ready(observer, owner)
    connection.dependencies = 1
    with pytest.raises(module.SourceActionDenied, match="source_provider_delete_dependencies_or_sessions"):
        module.verify_provider_delete_ready(observer, owner)
    assert not any("DROP" in text for text in connection.commands)



def test_unsettled_source_refuses_before_transition_marker(prepared, monkeypatch):
    origin, connection, workdir = prepared
    owner = module.prepare_owner(origin, workdir)
    monkeypatch.setattr(module, "verify_cloud_isolated", lambda _: None)
    def unsettled(*_):
        raise module.SourceActionDenied("source_unsettled_native_aggregate")
    monkeypatch.setattr(module, "verify_source_quiescent", unsettled)
    with pytest.raises(module.SourceActionDenied, match="source_unsettled_native_aggregate"):
        module.transition_source(origin, owner, object(), workdir, module.new_source_passwords())
    assert connection.owner == 16387 and not (workdir / "source-owner-transition.started.json").exists()


def test_financial_bridge_keeps_bind_values_out_of_sql(monkeypatch):
    from sqlalchemy import text
    calls = []
    class Cursor:
        def fetchall(self):
            return [(1,), (2,)]
    class Driver:
        def execute(self, statement, parameters):
            calls.append((statement, parameters))
            return Cursor()
    class Boundary:
        def verify_admission_closed(self):
            calls.append("native-admission")
        def _module(self, *_):
            return "existing-observer-source"
    def financial(connection, observer, schema):
        assert observer == "existing-observer-source" and schema == "20261008_0009"
        result = connection.execute(text("SELECT :namespace WHERE 'synthetic' = ANY(:tables)"),
            {"namespace": 2200, "tables": ["synthetic"]})
        assert result.scalars().all() == [1, 2]
    monkeypatch.setattr(module.native.fresh, "verify_financial_state", financial)
    module.verify_source_quiescent(Boundary(), Driver())
    assert calls[0] == "native-admission"
    statement, parameters = calls[1]
    assert "%(namespace)s" in statement and "%(tables)s" in statement
    assert parameters == {"namespace": 2200, "tables": ["synthetic"]}
    assert "2200" not in statement



def test_malformed_password_refuses_before_cloud_or_database(prepared, monkeypatch):
    origin, connection, workdir = prepared
    owner = module.prepare_owner(origin, workdir)
    before = len(connection.commands)
    def unexpected(*_):
        raise AssertionError("must not authenticate or read cloud")
    monkeypatch.setattr(module.transfer.BoundDatabase, "connect", unexpected)
    monkeypatch.setattr(module, "verify_cloud_isolated", unexpected)
    with pytest.raises(module.SourceActionDenied, match="source_password_generation_shape"):
        module.transition_source(origin, owner, object(), workdir, module.SourcePasswords("synthetic-short"))
    assert len(connection.commands) == before and not (workdir / "source-owner-transition.started.json").exists()
