"""Synthetic phase/identity sequencing controls; no native readiness claimed."""
from __future__ import annotations

import importlib.util
import sys
from contextlib import nullcontext
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'infra/gcp'))
spec = importlib.util.spec_from_file_location('fresh_phase_release_fixture', ROOT / 'infra/gcp/monetary_release.py')
release = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = release
spec.loader.exec_module(release)
ACTORS = {'observer': ('hirewiz_observer_20261013_r1', 24580),
          'replacement_runtime': ('hirewiz_runtime_20261013_r1', 24576),
          'replacement_migration': ('hirewiz_migrator_20261013_r1', 24578)}


@pytest.fixture
def plan(tmp_path):
    return release.Plan(release='a' * 40, image_digest='sha256:' + 'b' * 64,
        build_id='11111111-1111-1111-1111-111111111111', ci_run_id=7,
        runtime_secret='inert-source-runtime:2', migration_secret='inert-source-migration:3',
        observer_secret='inert-source-observer:7', database_owner_oid=16387,
        expense_secret='inert-expense-policy:4', expense_sha256='c' * 64,
        model_policy_secret='inert-model-policy:5', model_policy_sha256='e' * 64,
        model_key_secret='inert-model-key:6', model_primary='gemini-inert-model',
        model_api_base=release.MODEL_API_BASE,
        runtime_service_account=f'inert-runtime@{release.PROJECT}.iam.gserviceaccount.com',
        migration_service_account=f'inert-migration@{release.PROJECT}.iam.gserviceaccount.com',
        backup_uri=f'gs://{release.PROJECT}-hirewiz-application-artifacts/releases/inert.dump',
        inventory_path=tmp_path / 'inert.json', inventory_sha256='d' * 64)


@pytest.fixture
def target():
    return release.FreshTargetPlan('inert-fresh-runtime:2', 'inert-fresh-migration:3', 'inert-fresh-observer:4')


def rows(phase):
    return [{'oid': oid, 'rolname': name, 'rolinherit': False, 'elevated': False,
             'elevated_membership': False, 'administration': False,
             'rolcanlogin': alias != 'replacement_migration' or phase == 'migration',
             'schema_create': alias == 'replacement_migration' and phase == 'migration'}
            for alias, (name, oid) in ACTORS.items()]


class Connection:
    def __init__(self, state, alias):
        self.state, self.alias = state, alias

    def __enter__(self):
        return self

    def __exit__(self, *unused):
        self.state.events.append(('closed', self.alias))

    def begin(self):
        return nullcontext()

    def exec_driver_sql(self, sql):
        self.state.events.append(('readonly', self.alias, sql))

    def execute(self, sql, parameters):
        assert ':observer' in str(sql) and parameters == {
            'observer': ACTORS['observer'][0], 'runtime': ACTORS['replacement_runtime'][0],
            'migration': ACTORS['replacement_migration'][0]}
        return SimpleNamespace(mappings=lambda: SimpleNamespace(all=lambda: self.state.rows))


class Engine:
    def __init__(self, state, alias):
        self.state, self.alias = state, alias

    def connect(self):
        return Connection(self.state, self.alias)

    def dispose(self):
        self.state.events.append(('disposed', self.alias))


class State:
    def __init__(self, phase):
        self.events, self.rows, self.sessions = [], rows(phase), []
        self.permission_error = None
        self.metadata = {'name': 'projects/157225724590/secrets/inert-fresh-migration/versions/3', 'state': 'DISABLED'}
        self.inventory = SimpleNamespace(expected_database_oid=16391, expected_system_identifier_sha256='c' * 64)
        self.source_binding = SimpleNamespace(purpose='release_capture')
        self.neon = SimpleNamespace(DATABASE_OID=16391, CLUSTER='c' * 64,
            FRESH_DATABASE_OID=16395, FRESH_CLUSTER='f' * 64, FRESH_DATABASE_OWNER_OID=16391,
            FRESH_RELEASE_ACTORS=ACTORS, NAMESPACE='public', PROJECT_NUMBER='157225724590',
            verify_transport=self.transport, verify_native_target=self.identity,
            collect_fresh_release_target=self.collect)
        self.module = SimpleNamespace(neon_identity=self.neon, _native_session_ids=self.native_sessions)

    def transport(self, connection, binding):
        self.events.append(('transport', connection.alias))

    def identity(self, connection, binding, oid):
        self.events.append(('identity', connection.alias, oid))
        assert oid == (16391 if connection.alias == 'source' else 16395)

    def native_sessions(self, connection, oid, **kwargs):
        self.events.append(('sessions', connection.alias, oid))
        return self.sessions if connection.alias == 'observer' else [], {}

    def collect(self, reader, reference, **kwargs):
        self.events.append(('credential', kwargs['alias'], reference, kwargs['expected_schema']))
        assert kwargs['database_owner_oid'] == 16391
        alias = kwargs['alias']
        return SimpleNamespace(purpose='fresh_release_capture'), SimpleNamespace(engine=lambda: Engine(self, alias))

    def context(self, plan, schema):
        self.events.append(('source-context', schema))
        assert schema == '20261008_0009'
        return self.module, (Engine(self, 'source'), self.inventory, self.source_binding, None), {}, ()

    def permissions(self, connection, namespace, runtime, migration):
        self.events.append(('complete-runtime-permissions', namespace, runtime, migration))
        if self.permission_error:
            raise release.ReleaseDenied(self.permission_error)

    def describe(self, reference):
        self.events.append(('metadata-only', reference))
        return self.metadata


def boundary(tmp_path, monkeypatch, phase):
    state = State(phase)
    value = release.NativeBoundary(tmp_path)
    monkeypatch.setattr(value, '_database_context', state.context)
    monkeypatch.setattr(value, 'describe_release_secret', state.describe)
    monkeypatch.setattr(release, 'verify_runtime_permissions', state.permissions)
    return value, state


@pytest.mark.parametrize(('phase', 'schema'), [('migration', '20261011_0016'), ('serving', '20261008_0009')])
def test_phase_is_explicit_and_source_schema_stays_nine(plan, target, tmp_path, monkeypatch, phase, schema):
    value, state = boundary(tmp_path, monkeypatch, phase)
    value.verify_fresh_database_phase(plan, target, schema, phase=phase)
    credentials = [event for event in state.events if event[0] == 'credential']
    assert [event[1] for event in credentials] == (['observer', 'replacement_runtime', 'replacement_migration']
                                                  if phase == 'migration' else ['observer', 'replacement_runtime'])
    assert all(event[3] == schema for event in credentials)
    assert state.events[0] == ('source-context', '20261008_0009')
    assert ('identity', 'source', 16391) in state.events and ('identity', 'observer', 16395) in state.events
    assert any(event[0] == 'complete-runtime-permissions' for event in state.events)
    assert ('metadata-only', target.migration_secret) in state.events if phase == 'serving' else not any(event[0] == 'metadata-only' for event in state.events)
    assert state.events[-1] == ('disposed', 'source')


@pytest.mark.parametrize('phase', ['', 'inferred', None, [], True])
def test_unknown_phase_refuses_before_provider_context(plan, target, tmp_path, monkeypatch, phase):
    value, state = boundary(tmp_path, monkeypatch, 'migration')
    with pytest.raises(release.ReleaseDenied, match='fresh_target_phase_invalid'):
        value.verify_fresh_database_phase(plan, target, '20261011_0016', phase=phase)
    assert not state.events


@pytest.mark.parametrize('change', ['latest', 'source-name', 'same-name', 'schema'])
def test_numeric_versions_and_separate_source_target_classes(plan, target, tmp_path, monkeypatch, change):
    value, state = boundary(tmp_path, monkeypatch, 'migration')
    schema = '20261008_0009'
    if change == 'latest':
        target = replace(target, observer_secret='inert-fresh-observer:latest')
    elif change == 'source-name':
        target = replace(target, runtime_secret='inert-source-runtime:9')
    elif change == 'same-name':
        target = replace(target, migration_secret='inert-fresh-runtime:9')
    else:
        schema = 'unreviewed_schema'
    with pytest.raises(release.ReleaseDenied):
        value.verify_fresh_database_phase(plan, target, schema, phase='migration')
    assert not state.events


@pytest.mark.parametrize(('phase', 'change'), [('migration', 'no-login'), ('migration', 'no-create'),
    ('serving', 'login'), ('serving', 'create'), ('serving', 'session'),
    ('serving', 'old-oid'), ('migration', 'elevated'), ('migration', 'administration')])
def test_migration_and_serving_native_role_refusals(plan, target, tmp_path, monkeypatch, phase, change):
    value, state = boundary(tmp_path, monkeypatch, phase)
    migration = next(row for row in state.rows if row['oid'] == 24578)
    if change == 'no-login':
        migration['rolcanlogin'] = False
    elif change == 'no-create':
        migration['schema_create'] = False
    elif change == 'login':
        migration['rolcanlogin'] = True
    elif change == 'create':
        migration['schema_create'] = True
    elif change == 'session':
        state.sessions = [(77, 24578, 16395)]
    elif change == 'old-oid':
        migration['oid'] = 1884180
    else:
        migration[change] = True
    with pytest.raises(release.ReleaseDenied):
        value.verify_fresh_database_phase(plan, target, '20261011_0016', phase=phase)
    assert not any(event[0] == 'credential' and event[1] == 'replacement_migration' for event in state.events)
    assert ('disposed', 'observer') in state.events and state.events[-1] == ('disposed', 'source')


@pytest.mark.parametrize('metadata', [
    {'name': 'projects/157225724590/secrets/inert-fresh-migration/versions/3', 'state': 'ENABLED'},
    {'name': 'projects/157225724590/secrets/inert-fresh-migration/versions/4', 'state': 'DISABLED'},
    {'name': 'projects/999/secrets/inert-fresh-migration/versions/3', 'state': 'DISABLED'}])
def test_serving_requires_disabled_exact_numeric_version(plan, target, tmp_path, monkeypatch, metadata):
    value, state = boundary(tmp_path, monkeypatch, 'serving')
    state.metadata = metadata
    with pytest.raises(release.ReleaseDenied, match='fresh_migration_numeric_version_not_disabled'):
        value.verify_fresh_database_phase(plan, target, '20261011_0016', phase='serving')
    assert [event[1] for event in state.events if event[0] == 'credential'] == ['observer']


def test_permission_failure_and_source_target_confusion_never_advances(plan, target, tmp_path, monkeypatch):
    value, state = boundary(tmp_path, monkeypatch, 'migration')
    state.permission_error = 'runtime_sequence_usage_missing'
    with pytest.raises(release.ReleaseDenied, match='runtime_sequence_usage_missing'):
        value.verify_fresh_database_phase(plan, target, '20261011_0016', phase='migration')
    assert [event[1] for event in state.events if event[0] == 'credential'] == ['observer']
    value, state = boundary(tmp_path, monkeypatch, 'migration')
    state.inventory.expected_database_oid = 16395
    with pytest.raises(release.ReleaseDenied, match='source_and_fresh_native_classes_overlap'):
        value.verify_fresh_database_phase(plan, target, '20261008_0009', phase='migration')
    assert not any(event[0] == 'credential' for event in state.events)
    assert state.events[-1] == ('disposed', 'source')
