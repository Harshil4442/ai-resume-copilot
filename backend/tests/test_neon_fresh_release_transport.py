"""Fixed fresh identity/source separation controls; no provider or SQL calls."""
import ast
import hashlib
import importlib.util
import json
import sys
from dataclasses import replace
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('fresh_neon_identity_fixture', ROOT / 'backend/scripts/neon_direct_identity.py')
neon = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = neon
spec.loader.exec_module(neon)


@pytest.fixture(autouse=True)
def fresh_receipt(tmp_path, monkeypatch):
    value = {'status': 'target_roles_created', 'stage': 'fresh_target_role_creation',
             'org_id': neon.ORGANIZATION, 'project_id': neon.FRESH_NEON_PROJECT,
             'branch_id': neon.FRESH_BRANCH, 'endpoint_id': neon.FRESH_ENDPOINT,
             'direct_host': neon.FRESH_DIRECT_HOST, 'database': neon.DATABASE,
             'commit_acknowledged': True, 'production_switch': False,
             'global_provider_fence_proven': False,
             'summary': {'role_oids': dict(neon.FRESH_RELEASE_ACTORS.values())}}
    path = tmp_path / 'synthetic-native-provenance.json'
    path.write_text(json.dumps(value))
    monkeypatch.setattr(neon, 'FRESH_CONTROL_RECEIPT', path)
    monkeypatch.setattr(neon, 'FRESH_CONTROL_RECEIPT_SHA', hashlib.sha256(path.read_bytes()).hexdigest())
    return path


class Reader:
    def __init__(self, alias='observer'):
        self.calls = []
        self.metadata = {'name': f'projects/{neon.PROJECT_NUMBER}/secrets/inert-fresh-secret/versions/7',
                         'state': 'ENABLED', 'etag': 'inert-etag'}
        self.value = f'postgresql://{neon.FRESH_RELEASE_ACTORS[alias][0]}:synthetic-secret-only@{neon.FRESH_DIRECT_HOST}/neondb?sslmode=require&channel_binding=require'
        self.after = None

    def describe_release_secret(self, reference):
        self.calls.append('metadata')
        return self.after if self.after is not None and len(self.calls) > 1 else dict(self.metadata)

    def access_release_secret(self, reference):
        self.calls.append('access')
        return self.value


def collect(reader=None, alias='observer', schema='20261008_0009'):
    return neon.collect_fresh_release_target(reader or Reader(alias), 'inert-fresh-secret:7',
        alias=alias, database_owner_oid=neon.FRESH_DATABASE_OWNER_OID, expected_schema=schema)


@pytest.mark.parametrize('alias', tuple(neon.FRESH_RELEASE_ACTORS))
@pytest.mark.parametrize('schema', ['20261008_0009', '20261011_0016'])
def test_fresh_numeric_source_and_distinct_exact_actor(alias, schema):
    reader = Reader(alias)
    binding, target = collect(reader, alias, schema)
    assert reader.calls == ['metadata', 'access', 'metadata']
    assert binding.purpose == 'fresh_release_capture'
    assert (binding.actor, binding.actor_oid) == neon.FRESH_RELEASE_ACTORS[alias]
    assert binding.database_owner_oid == 16391
    assert target._url.host == neon.FRESH_DIRECT_HOST and target._url.query['sslmode'] == 'verify-full'
    assert 'default_transaction_read_only=on' in binding.options
    assert 'synthetic-secret-only' not in repr(binding) + repr(target) + repr(neon._issued)
    assert target.dump_environment()['PGHOST'] == neon.FRESH_DIRECT_HOST
    with pytest.raises(neon.NeonIdentityDenied):
        neon._registered(replace(binding))


@pytest.mark.parametrize('change', ['reference', 'owner', 'alias', 'schema'])
def test_bad_fresh_inputs_stop_before_credential_source(change):
    reader = Reader()
    args = {'alias': 'observer', 'database_owner_oid': 16391, 'expected_schema': '20261008_0009'}
    reference = 'inert-fresh-secret:7'
    if change == 'reference':
        reference = 'inert-fresh-secret:latest'
    elif change == 'owner':
        args['database_owner_oid'] = 16387
    elif change == 'alias':
        args['alias'] = 'neondb_owner'
    else:
        args['expected_schema'] = []
    with pytest.raises(neon.NeonIdentityDenied):
        neon.collect_fresh_release_target(reader, reference, **args)
    assert reader.calls == []


@pytest.mark.parametrize('change', ['source_host', 'source_actor', 'hostaddr', 'disabled', 'etag'])
def test_cross_source_and_native_secret_drift_refuse_without_fallback(change):
    reader = Reader()
    if change == 'source_host':
        reader.value = reader.value.replace(neon.FRESH_DIRECT_HOST, neon.RELEASE_DIRECT_HOST)
    elif change == 'source_actor':
        reader.value = reader.value.replace(neon.FRESH_RELEASE_ACTORS['observer'][0], neon.RELEASE_ACTORS['observer'][0])
    elif change == 'hostaddr':
        reader.value += '&hostaddr=203.0.113.1'
    elif change == 'disabled':
        reader.metadata['state'] = 'DISABLED'
    else:
        reader.after = {**reader.metadata, 'etag': 'changed'}
    with pytest.raises(neon.NeonIdentityDenied) as error:
        collect(reader)
    assert 'synthetic-secret-only' not in str(error.value) and 'postgresql://' not in str(error.value)
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


class Connection:
    def __init__(self, binding, change=None):
        self.binding, self.change, self.queries = binding, change, []

    def execute(self, query):
        source = str(query)
        self.queries.append(source)
        if 'pg_proc' in source:
            return Rows([(3441, 10, self.change == 'control', 'pg_control_system', 'internal')])
        if 'pg_database' in source:
            row = [16395, 16391, self.binding.actor_oid, self.binding.actor, self.binding.actor, 2200, 6171, 'synthetic-fresh-cluster']
            for key, index in {'database': 0, 'owner': 1, 'actor': 2, 'schema_owner': 6}.items():
                if self.change == key:
                    row[index] += 1
            if self.change == 'source_cluster':
                row[7] = 'synthetic-source-cluster'
            return Rows([tuple(row)])
        assert source == 'SELECT version_num FROM public.alembic_version LIMIT 3'
        return Rows([('other' if self.change == 'schema' else self.binding.expected_schema,)])


def test_fresh_native_cluster_owner_schema_and_marker_are_independent(monkeypatch):
    binding, _ = collect()
    monkeypatch.setattr(neon, 'FRESH_CLUSTER', hashlib.sha256(b'synthetic-fresh-cluster').hexdigest())
    connection = Connection(binding)
    neon.verify_native_target(connection, binding, 16395)
    assert len(connection.queries) == 3
    for change in ('control', 'database', 'owner', 'actor', 'schema_owner', 'source_cluster', 'schema'):
        with pytest.raises(neon.NeonIdentityDenied):
            neon.verify_native_target(Connection(binding, change), binding, 16395)
    with pytest.raises(neon.NeonIdentityDenied):
        neon.verify_native_target(Connection(binding), binding, neon.DATABASE_OID)


def test_receipt_drift_is_not_a_provider_or_fence_override(fresh_receipt):
    fresh_receipt.write_text('{}')
    reader = Reader()
    with pytest.raises(neon.NeonIdentityDenied):
        collect(reader)
    assert reader.calls == []


def test_legacy_profile_and_executable_source_route_remain_separate():
    assert neon.NEON_PROJECT == 'little-pond-36610622' and neon.DATABASE_OID == 16391
    assert neon.FRESH_NEON_PROJECT == 'shiny-cell-70214683' and neon.FRESH_DATABASE_OID == 16395
    assert set(neon.RELEASE_ACTORS.values()).isdisjoint(neon.FRESH_RELEASE_ACTORS.values())
    tree = ast.parse((ROOT / 'infra/gcp/monetary_release.py').read_text())
    boundary = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'NativeBoundary')
    context = next(n for n in boundary.body if isinstance(n, ast.FunctionDef) and n.name == '_database_context')
    calls = [n.func.attr for n in ast.walk(context) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)]
    assert 'collect_source_release_target' in calls and 'collect_fresh_release_target' not in calls
    source = next(n for n in ast.parse((ROOT / 'backend/scripts/neon_direct_identity.py').read_text()).body
                  if isinstance(n, ast.FunctionDef) and n.name == 'collect_source_release_target')
    assert [n.func.id for n in ast.walk(source) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)] == ['collect_release_target']
