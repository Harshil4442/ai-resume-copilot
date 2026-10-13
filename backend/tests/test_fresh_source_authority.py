"""Pure source-transition controls: no PostgreSQL, SDK, credentials or app imports."""
from __future__ import annotations

import importlib
import os
import sys
from contextlib import contextmanager
from pathlib import Path

import pytest

ROOT = Path(os.environ.get("HIREWIZ_ACTION_TEST_ROOT", str(Path(__file__).resolve().parents[2])))
sys.path[:0] = [str(Path(__file__).parent), str(ROOT / "infra/gcp"), str(ROOT / "backend")]
module = importlib.import_module("fresh_source_authority")


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
            (16387, "neondb_owner", True, True, False, True, True, False, False),
            (1884178, module.RUNTIME_NAME, False, False, False, False, False, False, False),
            (1884180, module.MIGRATOR_NAME, False, False, False, False, False, False, False),
            (1884182, module.OBSERVER_NAME, False, False, False, False, False, False, False)]
        self.members = [(1884178, 16387, 10, True, False, False), (1884180, 16387, 10, True, False, False), (1884182, 16387, 10, True, False, False)]
        self.owner = 16387
        self.created = False
        self.closed = False
        self.fail_commit = False
        self.default_acl = 0
        self.dependencies = 0
        self.otherdb = 0
        self.session_rows = []
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


def test_provider_delete_requires_native_zero_dependencies_and_sessions(prepared):
    origin, connection, _ = prepared
    connection.owner = 2000000
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
