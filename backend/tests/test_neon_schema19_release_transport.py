"""Schema19 route separation with inert sources; no native/provider acceptance."""
from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from types import SimpleNamespace

import pytest
from test_neon_linux_execution import (
    package as linux_package,  # noqa: F401 - shared filesystem fixture
)

from scripts import neon_direct_identity as neon
from scripts import neon_execution_files as files

SCHEMA19 = '20261015_0019'


@pytest.fixture(autouse=True)
def fresh_receipt(tmp_path, monkeypatch):
    receipt = {'status': 'target_roles_created', 'stage': 'fresh_target_role_creation',
               'org_id': neon.ORGANIZATION, 'project_id': neon.FRESH_NEON_PROJECT,
               'branch_id': neon.FRESH_BRANCH, 'endpoint_id': neon.FRESH_ENDPOINT,
               'direct_host': neon.FRESH_DIRECT_HOST, 'database': neon.DATABASE,
               'commit_acknowledged': True, 'production_switch': False,
               'global_provider_fence_proven': False,
               'summary': {'role_oids': dict(neon.FRESH_RELEASE_ACTORS.values())}}
    path = tmp_path / 'schema19-inert-target-provenance.json'
    path.write_text(json.dumps(receipt))
    monkeypatch.setattr(neon, 'FRESH_CONTROL_RECEIPT', path)
    monkeypatch.setattr(neon, 'FRESH_CONTROL_RECEIPT_SHA', hashlib.sha256(path.read_bytes()).hexdigest())
    return path


class Reader:
    """Synthetic numeric-secret wiring, never actual credentials or authority."""
    def __init__(self, alias='observer', reference='schema19-inert-secret:2', execution=None):
        self.alias, self.reference, self.calls = alias, reference, []
        self.linux_execution_files = execution
        name, version = reference.split(':')
        self.metadata = {'name': f'projects/{neon.PROJECT_NUMBER}/secrets/{name}/versions/{version}',
                         'state': 'ENABLED', 'etag': 'synthetic-schema19-source-etag'}
        actor = neon.FRESH_RELEASE_ACTORS[alias][0]
        self.value = f'postgresql://{actor}:synthetic-fixture-only@{neon.FRESH_DIRECT_HOST}/neondb?sslmode=require&channel_binding=require'
        self.after = None

    def describe_release_secret(self, reference):
        assert reference == self.reference
        self.calls.append('describe')
        return dict(self.after if self.after is not None and len(self.calls) > 1 else self.metadata)

    def access_release_secret(self, reference):
        assert reference == self.reference
        self.calls.append('access')
        return self.value


def collect(reader=None, **changes):
    reader = reader or Reader()
    kwargs = {'alias': reader.alias, 'database_owner_oid': neon.FRESH_DATABASE_OWNER_OID, **changes}
    return neon.collect_schema19_release_target(reader, reader.reference, **kwargs)


@pytest.mark.parametrize('alias', tuple(neon.FRESH_RELEASE_ACTORS))
def test_schema19_factory_issues_opaque_distinct_readonly_numeric_binding(alias):
    reader = Reader(alias)
    binding, destination = collect(reader)
    assert reader.calls == ['describe', 'access', 'describe']
    assert binding.purpose == 'schema19_fresh_release_capture'
    assert neon.bound_schema(binding) == SCHEMA19
    assert (binding.actor, binding.actor_oid) == neon.FRESH_RELEASE_ACTORS[alias]
    assert binding.database_owner_oid == neon.FRESH_DATABASE_OWNER_OID
    assert binding.options == neon.CAPTURE_OPTIONS
    assert destination._url.host == neon.FRESH_DIRECT_HOST
    assert destination._url.query == {'sslmode': 'verify-full', 'sslcertmode': 'disable',
                                      'sslrootcert': neon.ROOT_CERT, 'channel_binding': 'require'}
    dump = destination.dump_environment()
    assert dump['PGOPTIONS'].startswith('-c default_transaction_read_only=on ')
    assert dump['PGHOST'] == neon.FRESH_DIRECT_HOST and dump['PGSSLMODE'] == 'verify-full'
    assert 'synthetic-fixture-only' not in repr(binding) + repr(destination) + repr(neon._issued)
    for copy in (replace(binding), replace(binding, purpose='fresh_release_capture'),
                 replace(binding, expected_schema='20261015_0018')):
        with pytest.raises(neon.NeonIdentityDenied, match='native_binding_unavailable'):
            neon.bound_schema(copy)


@pytest.mark.parametrize('schema', [None, [], True, '20261015_0018', '20261015_0020', 'latest'])
def test_new_route_accepts_only_exact_schema19_before_secret_reads(schema):
    reader = Reader()
    with pytest.raises(neon.NeonIdentityDenied, match='identity_input_invalid'):
        collect(reader, expected_schema=schema)
    assert reader.calls == []


def test_previous_schema_set_and_collectors_still_refuse_schema19():
    assert SCHEMA19 not in neon.RELEASE_SCHEMAS
    assert max(neon.RELEASE_SCHEMAS) == '20261015_0018'
    for factory in (neon.collect_fresh_release_target, neon.collect_release_target,
                    neon.collect_source_release_target):
        reader = Reader()
        with pytest.raises(neon.NeonIdentityDenied, match='identity_input_invalid'):
            factory(reader, reader.reference, alias='observer', database_owner_oid=16391,
                    expected_schema=SCHEMA19)
        assert reader.calls == []
    reader = Reader()
    binding, _ = neon.collect_fresh_release_target(reader, reader.reference, alias='observer',
        database_owner_oid=16391, expected_schema='20261015_0018')
    assert binding.purpose == 'fresh_release_capture'
    assert neon.bound_schema(binding) == '20261015_0018'


@pytest.mark.parametrize('change', ['disabled', 'alias_name', 'etag_drift', 'source_host', 'pooler', 'hostaddr', 'wrong_actor', 'owner'])
def test_schema19_has_same_source_host_actor_and_owner_refusals(change):
    reader = Reader()
    kwargs = {}
    if change == 'disabled':
        reader.metadata['state'] = 'DISABLED'
    elif change == 'alias_name':
        reader.metadata['name'] = reader.metadata['name'].rsplit('/', 1)[0] + '/latest'
    elif change == 'etag_drift':
        reader.after = {**reader.metadata, 'etag': 'changed'}
    elif change == 'source_host':
        reader.value = reader.value.replace(neon.FRESH_DIRECT_HOST, neon.RELEASE_DIRECT_HOST)
    elif change == 'pooler':
        reader.value = reader.value.replace(neon.FRESH_DIRECT_HOST, neon.FRESH_DIRECT_HOST.replace(neon.FRESH_ENDPOINT, neon.FRESH_ENDPOINT + '-pooler'))
    elif change == 'hostaddr':
        reader.value += '&hostaddr=203.0.113.9'
    elif change == 'wrong_actor':
        reader.value = reader.value.replace(neon.FRESH_RELEASE_ACTORS['observer'][0], neon.RELEASE_ACTORS['observer'][0])
    elif change == 'owner':
        kwargs['database_owner_oid'] = 16387
    with pytest.raises(neon.NeonIdentityDenied) as error:
        collect(reader, **kwargs)
    assert 'synthetic-fixture-only' not in str(error.value)
    if change == 'owner':
        assert reader.calls == []
    assert len(reader.calls) <= 3


class Rows:
    def __init__(self, rows):
        self.rows = rows

    def all(self):
        return self.rows

    def one(self):
        assert len(self.rows) == 1
        return self.rows[0]

    def scalars(self):
        return iter(row[0] for row in self.rows)


class Session:
    def __init__(self, binding, change=None):
        self.binding, self.change, self.queries = binding, change, []

    def execute(self, statement):
        source = str(statement)
        self.queries.append(source)
        if 'pg_proc' in source:
            return Rows([(3441, 10, self.change == 'control', 'pg_control_system', 'internal')])
        if 'pg_database' in source:
            row = [16395, 16391, self.binding.actor_oid, self.binding.actor, self.binding.actor,
                   2200, 6171, 'schema19-synthetic-native-cluster']
            if self.change in {'database', 'owner', 'actor', 'namespace_owner'}:
                index = {'database': 0, 'owner': 1, 'actor': 2, 'namespace_owner': 6}[self.change]
                row[index] += 1
            elif self.change == 'cluster':
                row[-1] = 'other-synthetic-cluster'
            return Rows([tuple(row)])
        assert source == 'SELECT version_num FROM public.alembic_version LIMIT 3'
        return Rows([('20261015_0018' if self.change == 'schema' else self.binding.expected_schema,)])


def test_schema19_native_target_requires_same_fresh_cluster_and_exact19(monkeypatch):
    binding, _ = collect()
    monkeypatch.setattr(neon, 'FRESH_CLUSTER', hashlib.sha256(b'schema19-synthetic-native-cluster').hexdigest())
    session = Session(binding)
    neon.verify_native_target(session, binding, 16395)
    assert len(session.queries) == 3 and all(not query.startswith(('UPDATE', 'INSERT', 'SET')) for query in session.queries)
    for change in ('control', 'database', 'owner', 'actor', 'namespace_owner', 'cluster', 'schema'):
        with pytest.raises(neon.NeonIdentityDenied):
            neon.verify_native_target(Session(binding, change), binding, 16395)
    with pytest.raises(neon.NeonIdentityDenied, match='native_target_mismatch'):
        neon.verify_native_target(Session(binding), binding, neon.DATABASE_OID)
    old, _ = neon.collect_fresh_release_target(Reader(), 'schema19-inert-secret:2', alias='observer',
        database_owner_oid=16391, expected_schema='20261015_0018')
    with pytest.raises(neon.NeonIdentityDenied, match='exact_schema_mismatch'):
        neon.verify_release_schema(Session(binding), old)


def test_schema19_engine_retains_genuine_driver_guard_and_no_sql_before_transport():
    binding, destination = collect()
    engine = destination.engine()
    try:
        assert len(list(engine.dialect.dispatch.do_connect)) == 1
        class UnauthenticatedDriver:
            pass
        fake = SimpleNamespace(connection=SimpleNamespace(driver_connection=UnauthenticatedDriver()))
        with pytest.raises(neon.NeonIdentityDenied, match='guarded_driver_unavailable'):
            neon.verify_transport(fake, binding)
        listener = next(iter(engine.dialect.dispatch.do_connect))
        with pytest.raises(neon.NeonIdentityDenied, match='connection_settings_unavailable'):
            listener(SimpleNamespace(), None, [], {'hostaddr': '203.0.113.8'})
    finally:
        engine.dispose()


@pytest.mark.parametrize('alias', tuple(files.SECRET_NAMES))
def test_schema19_linux_files_exact_reference_and_ca_are_preserved(request, alias):
    execution, _, _ = request.getfixturevalue("linux_package")
    reference = files.SECRET_NAMES[alias] + ':2'
    reader = Reader(alias, reference, execution)
    binding, destination = collect(reader)
    assert binding.execution_files is execution and binding.purpose == 'schema19_fresh_release_capture'
    assert neon.bound_schema(binding) == SCHEMA19
    assert destination._url.query['sslrootcert'] == execution.root_certificate
    assert destination.dump_environment()['PGSSLROOTCERT'] == execution.root_certificate
    assert reader.calls == ['describe', 'access', 'describe']
    wrong = Reader(alias, 'schema19-inert-secret:2', execution)
    with pytest.raises(neon.NeonIdentityDenied):
        collect(wrong)
    assert wrong.calls == []
