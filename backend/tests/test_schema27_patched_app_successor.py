"""Owned synthetic adapter ports only; never native worker qualification."""
from __future__ import annotations

import copy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from infra.gcp import schema27_patched_app_successor as op
from test_schema26_app_successor import context as context
from test_schema26_app_successor import no_network as no_network
from test_schema26_app_successor import owned_app_roots as owned_app_roots
from test_schema27_api_identity_operator import controlled as controlled
from test_schema27_api_identity_promotion import handoff as handoff
from test_schema27_app_successor import application as application
from test_schema27_continuity_bridge import lane as lane
from test_schema27_continuity_bridge_plan import current as current
from test_schema27_mixed_app_successor import mixed_app as mixed_app
from test_schema27_mixed_app_successor import profile as profile

ROOT = Path(op.__file__).resolve().parents[2]


@pytest.fixture
def selected(mixed_app):
    c = mixed_app
    c.boundary.root = ROOT
    c.boundary.target_root, c.boundary.prior_root = ROOT / 'unit-target', ROOT / 'unit-prior'
    for slot, root in (('observer', c.boundary.target_root), ('prior_observer', c.boundary.prior_root)):
        observer = object.__new__(op.SourceBoundary)
        # Retain established owned fixture ports and explicitly synthetic proof.
        observer.__dict__.update(getattr(c.boundary, slot).__dict__)
        observer.root, observer.identity_profile = root, c.plan.identities
        setattr(c.boundary, slot, observer)
    return c


def run(c, mode):
    return op.execute(mode, c.plan, c.boundary, c.journal)


def test_stock_plan_stage_worker_first_advance_reconcile_rollback(selected):
    c = selected
    before = copy.deepcopy(c.fixture.states)
    assert type(c.boundary) is op.mixed.NativeBoundary and type(c.plan) is op.mixed.Plan
    assert run(c, 'stage')['migration_authorized'] is False and len(c.fixture.writes) == 3
    for service, body, _mask in c.fixture.writes:
        old_env = op.rolling.environment(before[service]['template'])
        new_env = op.rolling.environment(body['template'])
        # Every retained expense/model/secret reference and paused admission
        # value is identical; only the reviewed APP_RELEASE changes.
        assert all(new_env[key] == value for key, value in old_env.items() if key != 'APP_RELEASE')
        assert all(new_env[key]['value'] == 'false' for key in set(new_env) - set(old_env) - {'PGSSLROOTCERT'})
    assert run(c, 'advance')['rolling_release_complete'] is True
    assert [s for s, _, mask in c.fixture.writes if mask == 'traffic'] == [
        *op.rolling.release.SERVICES[1:], op.mixed.API]
    count = len(c.fixture.writes)
    assert run(c, 'reconcile')['native_mutations'] == 0 and len(c.fixture.writes) == count
    assert run(c, 'rollback')['payment_activation_authorized'] is False
    assert all(c.fixture.states[s]['template']['serviceAccount'] == before[s]['template']['serviceAccount']
               for s in op.rolling.release.SERVICES)


@pytest.mark.parametrize('slot', ['observer', 'prior_observer'])
@pytest.mark.parametrize('fault', ['generic', 'subclass', 'root', 'profile'])
def test_wrong_selected_observer_refuses_before_patch(selected, slot, fault):
    c = selected
    observer = getattr(c.boundary, slot)
    if fault in {'generic', 'subclass'}:
        kind = op.patched.MixedSourceBoundary if fault == 'generic' else type('ForeignSubclass', (op.SourceBoundary,), {})
        changed = object.__new__(kind)
        changed.__dict__.update(observer.__dict__)
        setattr(c.boundary, slot, changed)
    elif fault == 'root':
        observer.root = ROOT / 'foreign'
    else:
        observer.identity_profile = SimpleNamespace(foreign=True)
    with pytest.raises(op.rolling.RollingDenied, match='patched_observer_required'):
        run(c, 'stage')
    assert not c.fixture.writes


@pytest.mark.parametrize('drift', ['principal', 'acl', 'liability'])
def test_existing_mixed_global_guards_remain_required(selected, drift):
    c = selected
    if drift == 'principal':
        c.identities[c.plan.identities.transition.new_api.email]['uid'] = '9' * 21
    elif drift == 'acl':
        c.source.observed.retained_acl_sha256 = 'f' * 64
    else:
        c.source.observed.protected_liabilities = (('foreign', 'e' * 64, 1),)
    with pytest.raises(op.rolling.RollingDenied):
        run(c, 'stage')
    assert not c.fixture.writes


def test_pending_ACK_reconciles_without_repeating_patch(selected):
    c = selected
    c.fixture.pause = True
    assert run(c, 'stage')['status'] == 'same_native_operation_pending'
    assert run(c, 'advance')['status'] == 'same_native_operation_pending'
    assert len(c.fixture.writes) == 1
    assert c.journal.read('stage.' + c.plan.services[0].service + '.ack') is not None
    for operation in c.fixture.operations.values():
        operation['done'] = True
    assert run(c, 'reconcile')['native_mutations'] == 0 and len(c.fixture.writes) == 1


def test_original_foreign_journal_cannot_be_adopted(selected, application):
    with pytest.raises(op.rolling.RollingDenied):
        op.execute('stage', selected.plan, selected.boundary, application.journal)
    assert not selected.fixture.writes


def test_origin_and_profile_checked_before_stock_auth_constructor(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(op.mixed.NativeBoundary, '__init__', lambda *_a, **_kw: calls.append('auth'))
    with pytest.raises(op.rolling.RollingDenied, match='module_origin'):
        op.build_boundary(tmp_path, tmp_path, tmp_path, SimpleNamespace())
    with pytest.raises(op.rolling.RollingDenied, match='profile_kind'):
        op.build_boundary(ROOT, ROOT, ROOT, SimpleNamespace())
    assert calls == []


def test_factory_selects_both_typed_observers_without_global_replacement(monkeypatch, tmp_path):
    calls, original = [], op.mixed.MixedSourceBoundary
    profile = object.__new__(op.identities.Profile)
    def stock(self, root, target, prior, identity):
        calls.append('stock')
        self.root, self.target_root, self.prior_root, self.identity_profile = root, target, prior, identity
    def observer(self, root, identity):
        calls.append('observer')
        self.root, self.identity_profile = root, identity
    monkeypatch.setattr(op.mixed.NativeBoundary, '__init__', stock)
    monkeypatch.setattr(op.SourceBoundary, '__init__', observer)
    value = op.build_boundary(ROOT, tmp_path / 'target', tmp_path / 'prior', profile)
    assert type(value) is op.mixed.NativeBoundary
    assert type(value.observer) is type(value.prior_observer) is op.SourceBoundary
    assert value.observer.root == tmp_path / 'target' and value.prior_observer.root == tmp_path / 'prior'
    assert op.mixed.MixedSourceBoundary is original and calls == ['stock', 'observer', 'observer']


def descriptor(digest='a' * 64, count=316):
    return op.app.sources.QualifiedSource('1' * 40, digest, count, 'b' * 64, 'c' * 64)


@pytest.fixture
def contracts(monkeypatch):
    from scripts import schema27_app_release_contract as legacy
    r02 = SimpleNamespace(CONTRACT='synthetic_r02_family', CANDIDATE_APP_SHA256='8' * 64, CANDIDATE_APP_FILES=316)
    monkeypatch.setattr(op, '_application_contracts', lambda: (legacy, r02))
    return legacy, r02


@pytest.mark.parametrize('fault', ['exit', 'extra', 'changed', 'oversize', 'unknown'])
def test_isolated_qualification_refuses_without_auth_or_secret_disclosure(monkeypatch, tmp_path, contracts, fault):
    heads = []
    def git(root, *args):
        assert root == tmp_path.resolve()
        if args[0] == 'rev-parse':
            heads.append(True)
            return (('2' if fault == 'changed' and len(heads) > 1 else '1') * 40).encode()
        return b''
    def child(args, **kw):
        assert args[1:4] == ['-I', '-B', '-c']
        assert set(kw['env']) == {'PATH', 'PYTHONDONTWRITEBYTECODE'}
        assert kw['stdin'] is op.subprocess.DEVNULL
        assert json.loads(args[-1]) == [[c.CONTRACT, c.CANDIDATE_APP_SHA256, c.CANDIDATE_APP_FILES] for c in contracts]
        value = dict(release='1' * 40, app_sha256='f' * 64 if fault == 'unknown' else contracts[1].CANDIDATE_APP_SHA256, app_files=316,
                     qualification_sha256='b' * 64, operator_sha256='c' * 64)
        if fault == 'extra':
            value['secret_field'] = 'synthetic-private-value'
        raw = b'x' * 2049 if fault == 'oversize' else json.dumps(value).encode()
        return SimpleNamespace(returncode=1 if fault == 'exit' else 0, stdout=raw)
    monkeypatch.setattr(op.app.successor, '_git', git)
    monkeypatch.setattr(op.subprocess, 'run', child)
    with pytest.raises(op.rolling.RollingDenied) as caught:
        op.qualify_selected(tmp_path, '1' * 40)
    assert 'synthetic-private' not in str(caught.value)


def test_exact_old_and_new_family_without_changing_legacy_pins(contracts):
    legacy, r02 = contracts
    before = legacy.CANDIDATE_APP_SHA256
    assert op._application_contract(descriptor(before, legacy.CANDIDATE_APP_FILES)) is legacy
    assert op._application_contract(descriptor(r02.CANDIDATE_APP_SHA256)) is r02
    assert legacy.CANDIDATE_APP_SHA256 == before
    for value in (descriptor('f' * 64), descriptor(r02.CANDIDATE_APP_SHA256, 315)):
        with pytest.raises(op.rolling.RollingDenied, match='source_profile'):
            op._application_contract(value)


def test_selected_native_CI_and_unchanged_provenance_both_required(monkeypatch, contracts):
    _, r02 = contracts
    events, value = [], descriptor(r02.CANDIDATE_APP_SHA256)
    boundary = object.__new__(op.SourceBoundary)
    boundary.root = ROOT
    monkeypatch.setattr(op, 'qualify_selected', lambda root, release: value)
    r02.verify_native_ci = lambda observer, identity: events.append(('CI', observer, identity))
    boundary._verify_source_image_provenance = lambda **fields: events.append(('provenance', fields))
    fields = dict(release='1' * 40, image_digest='sha256:' + 'd' * 64, build_id='synthetic-build', ci_run_id=123)
    boundary.verify_source_image(**fields)
    assert boundary.descriptor is value and events == [('CI', boundary, 123), ('provenance', fields)]
    def denied(*_a):
        raise op.rolling.RollingDenied('synthetic_ci_refused')
    r02.verify_native_ci = denied
    with pytest.raises(op.rolling.RollingDenied):
        boundary.verify_source_image(**fields)
    assert len(events) == 2



def test_lost_ACK_reconciles_effect_without_replay_or_inventing_ACK(selected):
    c = selected
    c.fixture.lose_ack = True
    with pytest.raises(RuntimeError):
        run(c, 'stage')
    writes = list(c.fixture.writes)
    marker = 'stage.' + c.plan.services[0].service
    started = c.journal.read(marker + '.started')
    run(c, 'reconcile')
    run(c, 'reconcile')
    assert c.fixture.writes == writes and len(writes) == 1
    assert c.journal.read(marker + '.ack') is None
    assert c.journal.read(marker + '.checked')['operation_ack_retained'] is False
    assert c.journal.read(marker + '.started') == started


def test_real_contract_modules_keep_exact_legacy_and_R02_inventory():
    legacy, r02 = op._application_contracts()
    assert legacy.CANDIDATE_APP_SHA256 != r02.CANDIDATE_APP_SHA256
    assert legacy.CANDIDATE_APP_FILES == r02.CANDIDATE_APP_FILES == 316
    assert op._application_contract(descriptor(legacy.CANDIDATE_APP_SHA256)) is legacy
    assert op._application_contract(descriptor(r02.CANDIDATE_APP_SHA256)) is r02


def test_foreign_CLI_origin_denies_before_profile_read_or_auth(monkeypatch, tmp_path, capsys):
    def forbidden(*_a, **_kw):
        pytest.fail('Credential/profile work occurred before origin validation')
    monkeypatch.setattr(op.app.bridge.bridge.app26.initial, '_read_pinned', forbidden)
    monkeypatch.setattr(op, 'build_boundary', forbidden)
    monkeypatch.setattr('sys.argv', ['app', 'observe', '--root', str(tmp_path),
        '--target-root', str(tmp_path), '--prior-root', str(tmp_path), '--plan', str(tmp_path / 'plan'),
        '--journal', str(tmp_path / 'journal'), '--identity-profile', str(tmp_path / 'private-profile'),
        '--identity-profile-sha256', 'a' * 64])
    assert op.main() == 1
    output = capsys.readouterr()
    assert json.loads(output.out)['denial_code'] == 'schema27_patched_app_module_origin'
    assert str(tmp_path) not in output.out + output.err


def test_help_never_constructs_auth_or_changes_application(monkeypatch, capsys):
    monkeypatch.setattr(op, 'build_boundary', lambda *_a: pytest.fail('Auth during help'))
    monkeypatch.setattr('sys.argv', ['app', '--help'])
    with pytest.raises(SystemExit) as caught:
        op.main()
    assert caught.value.code == 0
    assert '{capture,stage,advance,observe,reconcile,rollback}' in capsys.readouterr().out
