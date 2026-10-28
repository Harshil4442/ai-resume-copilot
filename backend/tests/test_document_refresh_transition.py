"""Maintenance ordering/unknown writes; synthetic transport, no cloud."""
from __future__ import annotations

import copy
import importlib.util
import json
import sys
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs, unquote, urlsplit

import pytest

ROOT = Path(__file__).resolve().parents[2]
def load(name):
    spec = importlib.util.spec_from_file_location('transition_fixture_' + name, ROOT / 'infra/gcp' / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module
transition = load('document_refresh_transition')
partial = load('document_refresh_partial')
final = load('document_refresh_final')
probe = load('document_refresh_probe')
profile = load('document_renderer_profiles')
NONCE = 'a' * 32
NEW = probe.REPOSITORY + '@sha256:' + 'b' * 64
OLD_V2 = probe.REPOSITORY + '@sha256:' + 'e' * 64
APP = 'us-central1-docker.pkg.dev/ai-resume-parser-482412/cloud-run-source-deploy/hirewiz@sha256:' + 'c' * 64

def service(name):
    resource = transition.PARENT + '/services/' + name
    revision = resource + '/revisions/' + name + '-00001-owned'
    env = [dict(name=k, value=v) for k, v in zip(transition.PINS,
        (profile.IMAGE, probe.POLICY, profile.RENDERER, profile.FONT), strict=True)]
    env.append({'name': 'DOCUMENT_WORKER_URL', 'value': 'https://hirewiz-document-inspector-mqff62d75a-uc.a.run.app/inspect'})
    if name != transition.WORKER:
        env.extend([{'name': k, 'value': 'true'} for k in transition.PAUSE[name]])
        env.append({'name': 'DATABASE_URL', 'valueSource': {'secretKeyRef': {'secret': 'owned-db', 'version': '2'}}})
    else:
        env = [row for row in env if row['name'] in transition.PINS[:2]]
    return {'name': resource, 'uid': probe.SERVICE_UID if name == transition.WORKER else '01234567-0123-0123-0123-012345678901',
        'generation': '1', 'observedGeneration': '1', 'etag': 'owned1',
        'template': {'revision': revision, 'serviceAccount': transition.ACCOUNTS[name], 'timeout': '90s',
                     'containers': [{'image': profile.IMAGE if name == transition.WORKER else APP, 'env': env}]},
        'terminalCondition': {'state': 'CONDITION_SUCCEEDED'}, 'latestCreatedRevision': revision, 'latestReadyRevision': revision,
        'traffic': [{'type': 'TRAFFIC_TARGET_ALLOCATION_TYPE_LATEST', 'percent': 100}],
        'trafficStatuses': [{'revision': revision, 'percent': 100}], 'labels': {'retained': 'owned'},
        'ingress': 'INGRESS_TRAFFIC_INTERNAL_ONLY' if name != transition.CALLERS[0] else 'INGRESS_TRAFFIC_ALL'}

class Http:
    def __init__(self, *, lost_at=None, claim_unknown=False, claim_race=False):
        self.services = {name: service(name) for name in transition.SERVICES}
        self.revisions = {row['latestReadyRevision']: copy.deepcopy(row['template'])
                          for row in self.services.values()}
        self.historical_revisions = False
        self.operations = {}
        self.writes, self.lost_at = [], lost_at
        self.claim_objects = {}
        self.claim_posts = 0
        self.claim_unknown, self.claim_race = claim_unknown, claim_race
    def request(self, uri, *, method, body=None, headers=None, redirections):
        assert redirections == 0
        target = urlsplit(uri)
        if target.hostname == 'storage.googleapis.com':
            names = {transition.RESCUE_CLAIM_OBJECT, partial.CLAIM_OBJECT, final.CLAIM_OBJECT}
            if method == 'POST':
                name = parse_qs(target.query)['name'][0]
            else:
                name = next((item for item in names if item in unquote(target.path)), None)
            assert name in names
            if method == 'GET' and target.path.startswith('/storage/v1/'):
                return SimpleNamespace(status=200 if name in self.claim_objects else 404), b'{}'
            if method == 'POST':
                assert parse_qs(target.query) == {'uploadType': ['media'], 'name': [name],
                                                   'ifGenerationMatch': ['0']}
                assert headers == {'Content-Type': 'application/json'}
                self.claim_posts += 1
                if self.claim_race:
                    self.claim_objects[name] = (body, '41')
                    return SimpleNamespace(status=412), b'{}'
                assert name not in self.claim_objects
                self.claim_objects[name] = (body, '41')
                if self.claim_unknown:
                    raise transition.TransitionDenied('document_transition_rescue_claim_unknown_reconcile_only')
                return SimpleNamespace(status=201), json.dumps({'name': name,
                    'bucket': transition.PROJECT + '_cloudbuild', 'generation': '41'}).encode()
            assert method == 'GET' and target.path.startswith('/download/storage/v1/')
            assert parse_qs(target.query) == {'alt': ['media'], 'generation': ['41']}
            return SimpleNamespace(status=200), self.claim_objects[name][0]
        path = target.path.removeprefix('/v2/')
        if method == 'PATCH':
            name = path.rsplit('/', 1)[-1]
            sent = json.loads(body)
            before = self.services[name]
            assert sent['etag'] == before['etag']
            self.writes.append((name, copy.deepcopy(sent)))
            current = copy.deepcopy(before)
            current.update({k: v for k, v in sent.items() if k not in {'name', 'etag'}})
            number = int(before['generation']) + 1
            current['generation'] = current['observedGeneration'] = str(number)
            current['etag'] = 'owned' + str(number)
            revision = path + '/revisions/' + sent['template']['revision']
            current['template']['revision'] = revision
            current['latestReadyRevision'] = current['latestCreatedRevision'] = revision
            current['trafficStatuses'] = [{'revision': revision, 'percent': 100}]
            self.services[name] = current
            self.revisions[revision] = copy.deepcopy(current['template'])
            operation_name = transition.PARENT + '/operations/owned-' + str(len(self.writes))
            self.operations[operation_name] = copy.deepcopy(current)
            if len(self.writes) == self.lost_at:
                raise transition.TransitionDenied('document_transition_native_outcome_unknown_reconcile_only')
            value = {'name': operation_name}
        elif '/operations/' in path:
            if path not in self.operations:
                return SimpleNamespace(status=404), b'{}'
            value = {'name': path, 'done': True, 'response': copy.deepcopy(self.operations[path])}
        elif '/revisions/' in path:
            name = path.split('/services/')[1].split('/')[0]
            if self.historical_revisions and path not in self.revisions:
                return SimpleNamespace(status=404), b'{}'
            value = copy.deepcopy(self.revisions[path] if self.historical_revisions
                                  else self.services[name]['template'])
            value.update(name=path, uid='01234567-0123-0123-0123-012345678901', service=name,
                         conditions=[{'type': 'Ready', 'state': 'CONDITION_SUCCEEDED'}])
        else:
            value = self.services[path.rsplit('/', 1)[-1]]
        return SimpleNamespace(status=200), json.dumps(value).encode()

def fixture_probe(monkeypatch, *, contract_version=2, required_case_count=10, policy=None, image=NEW):
    acceptance = probe.NativeAcceptance(image, '01234567-0123-0123-0123-012345678901', 'owned-build', 'a' * 64,
        'b' * 64, {}, transition.PARENT + '/jobs/hirewiz-doc-six-' + NONCE[:12],
        'owned-job-uid', 'owned-execution', 'owned-execution-uid', 'c' * 64, 'd' * 64,
        probe.POLICY if policy is None else policy, profile.FONT, profile.RENDERER, '2026-10-19T00:00:00+00:00',
        contract_version, required_case_count)
    monkeypatch.setattr(probe, 'verify_successor', lambda *args: acceptance)
    calls = []
    monkeypatch.setattr(probe, 'verify_current_successor', lambda image, actual: calls.append((image, actual['uid'])))
    def current_build(build_id, nonce, image):
        assert build_id == '01234567-0123-0123-0123-012345678901' and nonce == NONCE and image in {NEW, OLD_V2}
        return {'kind': 'native_cloud_build', 'immutable_image': image,
                'fixed_renderer_base': profile.IMAGE, 'source_sha256': copy.deepcopy(profile.SOURCES)}
    monkeypatch.setattr(probe, 'build_module', lambda: SimpleNamespace(
        RENDERER_BASE=profile.IMAGE, verify_current_build=current_build))
    return calls

def run(tmp_path, http):
    return transition.transition(NEW, {'nonce': NONCE, 'build_id': '01234567-0123-0123-0123-012345678901'},
        tmp_path / 'native', tmp_path / 'rollout', http=http, probe=probe)


def guarded_old_v2(http):
    for name, actual in http.services.items():
        for entry in actual['template']['containers'][0]['env']:
            if entry['name'] == 'DOCUMENT_WORKER_IMAGE_DIGEST':
                entry['value'] = OLD_V2
        if name == transition.WORKER:
            actual['template']['containers'][0]['image'] = OLD_V2
            actual['labels'].update({'hirewiz-refresh-build': '01234567-0123-0123-0123-012345678901',
                                     'hirewiz-refresh-nonce': NONCE, 'hirewiz-refresh-job': 'hirewiz-doc-six-' + NONCE[:12]})
        elif name == transition.CALLERS[0]:
            next(row for row in actual['template']['containers'][0]['env']
                 if row['name'] == 'RESUME_DIRECT_UPLOAD_ENABLED')['value'] = 'false'
            actual['template']['containers'][0]['env'].append(
                {'name': 'APP_RELEASE', 'value': transition.GUARDED_API_RELEASE})
            actual['template']['labels'] = {'hirewiz-source': transition.GUARDED_API_RELEASE}
            actual['trafficStatuses'] = [{'type': 'TRAFFIC_TARGET_ALLOCATION_TYPE_REVISION',
                                          'revision': actual['latestReadyRevision'], 'percent': 100,
                                          'tag': 'api-hotfix-candidate',
                                          'uri': 'https://api-hotfix-candidate.example.run.app'}]
        http.revisions[actual['latestReadyRevision']] = copy.deepcopy(actual['template'])
    return http


def run_rescue(tmp_path, http):
    return transition.transition(NEW, {'nonce': NONCE, 'build_id': '01234567-0123-0123-0123-012345678901'},
        tmp_path / 'native', tmp_path / 'rollout', http=http, probe=probe, rescue_expired_v2=True)


@pytest.mark.parametrize('missing', ['image', 'revision', 'tag'])
def test_rescue_requires_pinned_guarded_api_before_patch(monkeypatch, tmp_path, missing):
    fixture_probe(monkeypatch)
    monkeypatch.setattr(transition, 'GUARDED_API_IMAGE', APP)
    monkeypatch.setattr(transition, 'GUARDED_API_REVISION', 'ai-resume-parser-00001-owned')
    monkeypatch.setattr(transition, 'GUARDED_API_TAG', 'api-hotfix-candidate')
    monkeypatch.setattr(transition, {'image': 'GUARDED_API_IMAGE', 'revision': 'GUARDED_API_REVISION',
                                    'tag': 'GUARDED_API_TAG'}[missing], '')
    http = guarded_old_v2(Http())
    with pytest.raises(transition.TransitionDenied, match='guarded_api_.*_unpinned'):
        run_rescue(tmp_path, http)
    assert http.writes == []


def test_rescue_retires_exact_v2_and_reopens_guarded_api_only_after_new_pins(monkeypatch, tmp_path):
    fixture_probe(monkeypatch)
    monkeypatch.setattr(transition, 'GUARDED_API_IMAGE', APP)
    monkeypatch.setattr(transition, 'GUARDED_API_REVISION', 'ai-resume-parser-00001-owned')
    monkeypatch.setattr(transition, 'GUARDED_API_TAG', 'api-hotfix-candidate')
    retired = []
    monkeypatch.setattr(probe, 'verify_expired_current_v2_for_retirement',
                        lambda image, metadata: retired.append((image, metadata['uid'])))
    http = guarded_old_v2(Http())
    reader = transition.Controller(http, probe, None)
    with pytest.raises(probe.NativeProofDenied, match='serving_traffic_changed'):
        probe.verify_caller_pins(reader, OLD_V2)
    transition.verify_rescue_caller_pins(reader, probe, OLD_V2)
    result = run_rescue(tmp_path, http)
    assert retired == [(OLD_V2, probe.SERVICE_UID)]
    assert [name for name, _ in http.writes] == [*transition.CALLERS, transition.WORKER,
                                                 *transition.CALLERS, *transition.CALLERS]
    assert transition.environment(http.writes[0][1]['template'])['RESUME_DIRECT_UPLOAD_ENABLED']['value'] == 'false'
    assert transition.environment(http.writes[-3][1]['template'])['RESUME_DIRECT_UPLOAD_ENABLED']['value'] == 'true'
    assert result['expired_v2_rescued'] is True and result['prior_image'] == OLD_V2
    assert json.loads((tmp_path / 'rollout/rescue-admission-intent.json').read_text())['automatic_replay'] is False


def failed_rescue_after_three_closes(monkeypatch, tmp_path):
    """Use real transition markers, then retain only the failed-run prefix."""
    fixture_probe(monkeypatch)
    monkeypatch.setattr(transition, 'GUARDED_API_IMAGE', APP)
    monkeypatch.setattr(transition, 'GUARDED_API_REVISION', 'ai-resume-parser-00001-owned')
    monkeypatch.setattr(transition, 'GUARDED_API_TAG', 'api-hotfix-candidate')
    monkeypatch.setattr(transition, 'RESCUE_IMAGE', NEW)
    monkeypatch.setattr(transition, 'RESCUE_BUILD_ID', '01234567-0123-0123-0123-012345678901')
    monkeypatch.setattr(transition, 'RESCUE_NONCE', NONCE)
    monkeypatch.setattr(probe, 'verify_expired_current_v2_for_retirement', lambda *_: None)
    original = guarded_old_v2(Http())
    (tmp_path / 'original').mkdir()
    run_rescue(tmp_path / 'original', original)
    partial = guarded_old_v2(Http())
    for _, body in original.writes[:3]:
        partial.request('https://run.googleapis.com/v2/' + body['name'], method='PATCH',
                        body=json.dumps(body).encode(), headers={}, redirections=0)
    journal = tmp_path / 'retained'
    journal.mkdir(mode=0o700)
    for file in (tmp_path / 'original/rollout').glob('*.json'):
        if file.name in {'native-acceptance.json', 'captured-flags.json'} or file.name.startswith('close.'):
            if file.name == 'close.hirewiz-resume-coordinator.verified.json':
                continue
            saved = journal / file.name
            saved.write_bytes(file.read_bytes())
            saved.chmod(0o600)
    return partial, journal


def copy_failed_journal(source, target):
    """A separate dispatch downloads the same immutable original prefix."""
    target.mkdir(mode=0o700)
    for file in source.glob('*.json'):
        saved = target / file.name
        saved.write_bytes(file.read_bytes())
        saved.chmod(0o600)
    return target


def continue_rescue(http, journal, tmp_path, *, source_hash=None):
    return transition.resume_failed_rescue(NEW, {
        'build_id': transition.RESCUE_BUILD_ID, 'nonce': transition.RESCUE_NONCE},
        tmp_path / 'native', journal,
        source_journal_tree_sha256=source_hash or transition.RESCUE_JOURNAL_TREE_SHA256,
        http=http, probe=probe)


def test_resume_failed_rescue_reconciles_ack_then_only_sends_unstarted_steps(monkeypatch, tmp_path):
    http, journal = failed_rescue_after_three_closes(monkeypatch, tmp_path)
    result = continue_rescue(http, journal, tmp_path)
    assert result['status'] == 'document_refresh_serving'
    assert result['expired_v2_rescued'] is True
    assert [name for name, _ in http.writes[3:]] == [transition.WORKER,
        *transition.CALLERS, *transition.CALLERS]
    assert (journal / 'close.hirewiz-resume-coordinator.verified.json').is_file()
    assert http.claim_posts == 1 and (journal / 'rescue-claim.verified.json').is_file()
    assert (journal / 'complete.json').is_file()
    assert transition.environment(http.services[transition.CALLERS[0]]['template'])[
        'RESUME_DIRECT_UPLOAD_ENABLED']['value'] == 'true'


@pytest.mark.parametrize('drift', ['operation', 'flag', 'template'])
def test_resume_failed_rescue_refuses_changed_ack_or_live_close(monkeypatch, tmp_path, drift):
    http, journal = failed_rescue_after_three_closes(monkeypatch, tmp_path)
    name = transition.CALLERS[2]
    if drift == 'operation':
        ack = journal / ('close.' + name + '.acknowledged.json')
        changed = json.loads(ack.read_text())
        changed['operation'] = transition.PARENT + '/operations/unrelated'
        ack.write_text(json.dumps(changed))
    elif drift == 'flag':
        next(row for row in http.services[name]['template']['containers'][0]['env']
             if row['name'] == 'RESUME_DIRECT_UPLOAD_ENABLED')['value'] = 'true'
    else:
        http.services[name]['template']['timeout'] = '900s'
    with pytest.raises(transition.TransitionDenied):
        continue_rescue(http, journal, tmp_path)
    assert len(http.writes) == 3
    assert http.claim_posts == 0
    assert not (journal / 'worker.hirewiz-document-inspector.started.json').exists()


def test_resume_claim_consumes_worker_send_across_repeated_dispatch_after_unknown_patch(monkeypatch, tmp_path):
    http, first = failed_rescue_after_three_closes(monkeypatch, tmp_path)
    second = copy_failed_journal(first, tmp_path / 'retained-second')
    before_worker = copy.deepcopy(http.services[transition.WORKER])
    http.lost_at = 4
    with pytest.raises(transition.TransitionDenied, match='native_outcome_unknown'):
        continue_rescue(http, first, tmp_path)
    assert http.claim_posts == 1 and len(http.writes) == 4
    assert (first / 'worker.hirewiz-document-inspector.started.json').is_file()
    # A timeout may occur before the service changes. A second workflow run
    # must still never send the worker PATCH a second time.
    http.services[transition.WORKER] = before_worker
    with pytest.raises(transition.TransitionDenied, match='serving_phase_changed|claim_exists_or_unknown'):
        continue_rescue(http, second, tmp_path)
    assert http.claim_posts == 1 and len(http.writes) == 4
    assert not (second / 'worker.hirewiz-document-inspector.started.json').exists()


@pytest.mark.parametrize('outcome', ['unknown_ack', 'raced_412'])
def test_resume_claim_unknown_or_raced_create_never_starts_worker(monkeypatch, tmp_path, outcome):
    http, first = failed_rescue_after_three_closes(monkeypatch, tmp_path)
    second = copy_failed_journal(first, tmp_path / 'retained-second')
    http.claim_unknown = outcome == 'unknown_ack'
    http.claim_race = outcome == 'raced_412'
    with pytest.raises(transition.TransitionDenied, match='claim_unknown'):
        continue_rescue(http, first, tmp_path)
    assert http.claim_posts == 1 and len(http.writes) == 3
    assert (first / 'rescue-claim.started.json').is_file()
    assert not (first / 'worker.hirewiz-document-inspector.started.json').exists()
    with pytest.raises(transition.TransitionDenied, match='claim_exists_or_unknown'):
        continue_rescue(http, second, tmp_path)
    assert http.claim_posts == 1 and len(http.writes) == 3


def test_resume_changed_original_tree_refused_before_claim(monkeypatch, tmp_path):
    http, journal = failed_rescue_after_three_closes(monkeypatch, tmp_path)
    with pytest.raises(transition.TransitionDenied, match='candidate_changed'):
        continue_rescue(http, journal, tmp_path, source_hash='0' * 64)
    assert http.claim_posts == 0 and len(http.writes) == 3
    assert not (journal / 'owner.lock').exists()


def partially_restored_with_api_maintenance_close(monkeypatch, tmp_path):
    """Synthetic old artifact plus exact ready emergency API close."""
    http, source = failed_rescue_after_three_closes(monkeypatch, tmp_path)
    continue_rescue(http, source, tmp_path)
    http.historical_revisions = True
    assert len(http.writes) == 10
    for service, index in ((transition.CALLERS[1], 6), (transition.CALLERS[2], 7)):
        http.services[service] = copy.deepcopy(
            http.operations[transition.PARENT + '/operations/owned-' + str(index)])
        http.revisions.pop(transition.PARENT + '/services/' + service + '/revisions/'
                           + service + '-d-' + NONCE[:12] + '-open')
    for number in (9, 10):
        http.operations.pop(transition.PARENT + '/operations/owned-' + str(number))
    http.writes = http.writes[:8]
    for file in source.glob('open.*.json'):
        if file.name not in {'open.ai-resume-parser.started.json',
                             'open.ai-resume-parser.acknowledged.json'}:
            file.unlink()
    (source / 'complete.json').unlink()
    (source / 'owner.lock').unlink()
    api = transition.CALLERS[0]
    before = http.services[api]
    closed = copy.deepcopy(before['template'])
    env = transition.environment(closed)
    for flag in partial.MAINTENANCE_OFF:
        env[flag] = {'name': flag, 'value': 'false'}
    closed['containers'][0]['env'] = list(env.values())
    closed['revision'] = partial.MAINTENANCE_CLOSE_REVISION
    body = {'name': before['name'], 'etag': before['etag'], 'template': closed,
            'traffic': [{'type': 'TRAFFIC_TARGET_ALLOCATION_TYPE_LATEST', 'percent': 100}]}
    http.request('https://run.googleapis.com/v2/' + before['name'], method='PATCH',
                 body=json.dumps(body).encode(), headers={}, redirections=0)
    monkeypatch.setattr(partial, 'MAINTENANCE_CLOSE_GENERATION', int(http.services[api]['generation']))
    monkeypatch.setattr(partial, 'MAINTENANCE_CLOSE_OPERATION', transition.PARENT + '/operations/owned-9')
    return http, source


def continue_partial(http, source, tmp_path):
    return partial.resume_after_api_maintenance_close(NEW,
        {'build_id': transition.RESCUE_BUILD_ID, 'nonce': transition.RESCUE_NONCE},
        tmp_path / 'native', source, tmp_path / 'partial-continuation',
        source_tree_sha256=partial.JOURNAL_TREE_SHA256, http=http, probe=probe, t=transition)


def test_partial_rescue_reconciles_api_and_only_opens_two_callers(monkeypatch, tmp_path):
    http, source = partially_restored_with_api_maintenance_close(monkeypatch, tmp_path)
    api = copy.deepcopy(http.services[transition.CALLERS[0]])
    result = continue_partial(http, source, tmp_path)
    assert result['status'] == 'document_refresh_partial_restoration_observed'
    assert result['api_maintenance_restore_required'] is True
    assert [name for name, _ in http.writes[9:]] == list(transition.CALLERS[1:])
    assert http.services[transition.CALLERS[0]] == api
    assert http.claim_posts == 2
    journal = tmp_path / 'partial-continuation'
    assert (journal / 'partial-claim.verified.json').is_file()
    assert not (journal / 'open.ai-resume-parser.started.json').exists()
    assert (journal / 'complete.json').is_file()


@pytest.mark.parametrize('drift', ['api_flag', 'api_revision', 'api_operation', 'caller_flag'])
def test_partial_rescue_refuses_drift_before_new_claim(monkeypatch, tmp_path, drift):
    http, source = partially_restored_with_api_maintenance_close(monkeypatch, tmp_path)
    if drift == 'api_flag':
        env = transition.environment(http.services[transition.CALLERS[0]]['template'])
        env['RESUME_DIRECT_UPLOAD_ENABLED']['value'] = 'true'
        http.services[transition.CALLERS[0]]['template']['containers'][0]['env'] = list(env.values())
    elif drift == 'api_revision':
        http.services[transition.CALLERS[0]]['latestReadyRevision'] += '-changed'
    elif drift == 'api_operation':
        (source / 'open.ai-resume-parser.acknowledged.json').write_text('{}')
    else:
        env = transition.environment(http.services[transition.CALLERS[1]]['template'])
        env['OPTIONAL_AI_GENERATION_ENABLED']['value'] = 'true'
        http.services[transition.CALLERS[1]]['template']['containers'][0]['env'] = list(env.values())
    with pytest.raises((transition.TransitionDenied, probe.NativeProofDenied)):
        continue_partial(http, source, tmp_path)
    assert http.claim_posts == 1
    assert len(http.writes) == 9
    assert not (tmp_path / 'partial-continuation/partial-claim.started.json').exists()


def test_partial_claim_consumes_new_sends_after_unknown_ack(monkeypatch, tmp_path):
    http, source = partially_restored_with_api_maintenance_close(monkeypatch, tmp_path)
    http.lost_at = 10
    with pytest.raises(transition.TransitionDenied):
        continue_partial(http, source, tmp_path)
    assert http.claim_posts == 2 and len(http.writes) == 10
    assert (tmp_path / 'partial-continuation/partial-claim.verified.json').is_file()
    assert (tmp_path / 'partial-continuation/open.hirewiz-analysis-worker.started.json').is_file()
    with pytest.raises(transition.TransitionDenied, match='serving_phase_changed|claim_exists_or_unknown'):
        partial.resume_after_api_maintenance_close(NEW,
            {'build_id': transition.RESCUE_BUILD_ID, 'nonce': transition.RESCUE_NONCE},
            tmp_path / 'native', source, tmp_path / 'second-continuation',
            source_tree_sha256=partial.JOURNAL_TREE_SHA256, http=http, probe=probe, t=transition)
    assert http.claim_posts == 2


@pytest.mark.parametrize('outcome', ['unknown_ack', 'raced_412'])
def test_partial_claim_unknown_or_race_never_opens_caller(monkeypatch, tmp_path, outcome):
    http, source = partially_restored_with_api_maintenance_close(monkeypatch, tmp_path)
    http.claim_unknown = outcome == 'unknown_ack'
    http.claim_race = outcome == 'raced_412'
    with pytest.raises(transition.TransitionDenied):
        continue_partial(http, source, tmp_path)
    assert http.claim_posts == 2 and len(http.writes) == 9
    assert (tmp_path / 'partial-continuation/partial-claim.started.json').is_file()
    assert not (tmp_path / 'partial-continuation/open.hirewiz-analysis-worker.started.json').exists()


def test_partial_rescue_rechecks_api_after_claim_before_any_patch(monkeypatch, tmp_path):
    http, source = partially_restored_with_api_maintenance_close(monkeypatch, tmp_path)
    original = partial.claim
    def claim_then_drift(*args, **kwargs):
        original(*args, **kwargs)
        http.services[transition.CALLERS[0]]['etag'] = 'concurrent-change'
    monkeypatch.setattr(partial, 'claim', claim_then_drift)
    with pytest.raises(transition.TransitionDenied, match='prewrite_state_changed'):
        continue_partial(http, source, tmp_path)
    assert http.claim_posts == 2 and len(http.writes) == 9
    assert not (tmp_path / 'partial-continuation/open.hirewiz-analysis-worker.started.json').exists()


def failed_partial_after_analysis_ack(monkeypatch, tmp_path):
    """Exact one-send history: two claims, analysis ACK, no coordinator send."""
    http, source = partially_restored_with_api_maintenance_close(monkeypatch, tmp_path)
    coordinator = transition.CALLERS[2]
    pins = copy.deepcopy(http.services[coordinator])
    continue_partial(http, source, tmp_path)
    previous = tmp_path / 'partial-continuation'
    assert len(http.writes) == 11
    http.services[coordinator] = pins
    http.revisions.pop(transition.PARENT + '/services/' + coordinator + '/revisions/'
                       + coordinator + '-d-' + NONCE[:12] + '-open')
    http.operations.pop(transition.PARENT + '/operations/owned-11')
    http.writes.pop()
    for file in previous.glob('open.*.json'):
        if file.name != 'open.hirewiz-analysis-worker.started.json' and file.name != 'open.hirewiz-analysis-worker.acknowledged.json':
            file.unlink()
    (previous / 'complete.json').unlink()
    (previous / 'owner.lock').unlink()
    monkeypatch.setattr(final.partial, 'MAINTENANCE_CLOSE_GENERATION', partial.MAINTENANCE_CLOSE_GENERATION)
    monkeypatch.setattr(final.partial, 'MAINTENANCE_CLOSE_OPERATION', partial.MAINTENANCE_CLOSE_OPERATION)
    return http, source, previous


def continue_final(http, source, previous, tmp_path):
    return final.resume(NEW, {'build_id': transition.RESCUE_BUILD_ID, 'nonce': transition.RESCUE_NONCE},
        tmp_path / 'native', source, previous, tmp_path / 'final-continuation',
        source_tree_sha256=final.JOURNAL_TREE_SHA256, http=http, probe=probe, t=transition)


def test_final_rescue_reconciles_analysis_and_only_opens_coordinator(monkeypatch, tmp_path):
    http, source, previous = failed_partial_after_analysis_ack(monkeypatch, tmp_path)
    api = copy.deepcopy(http.services[transition.CALLERS[0]])
    analysis = copy.deepcopy(http.services[transition.CALLERS[1]])
    result = continue_final(http, source, previous, tmp_path)
    assert result['status'] == 'document_refresh_final_caller_restoration_observed'
    assert result['api_maintenance_restore_required'] is True
    assert [name for name, _ in http.writes[10:]] == [transition.CALLERS[2]]
    assert http.services[transition.CALLERS[0]] == api
    assert http.services[transition.CALLERS[1]] == analysis
    assert http.claim_posts == 3
    journal = tmp_path / 'final-continuation'
    assert (journal / 'final-claim.verified.json').is_file()
    assert (journal / 'open.hirewiz-resume-coordinator.verified.json').is_file()
    assert not (journal / 'open.hirewiz-analysis-worker.started.json').exists()
    assert (journal / 'complete.json').is_file()


@pytest.mark.parametrize('drift', ['analysis_template', 'analysis_operation', 'api_flag', 'coordinator_flag', 'second_claim'])
def test_final_rescue_refuses_drift_before_third_claim(monkeypatch, tmp_path, drift):
    http, source, previous = failed_partial_after_analysis_ack(monkeypatch, tmp_path)
    if drift == 'analysis_template':
        http.services[transition.CALLERS[1]]['template']['timeout'] = '17s'
    elif drift == 'analysis_operation':
        (previous / 'open.hirewiz-analysis-worker.acknowledged.json').write_text('{}')
    elif drift == 'api_flag':
        env = transition.environment(http.services[transition.CALLERS[0]]['template'])
        env['RESUME_DIRECT_UPLOAD_ENABLED']['value'] = 'true'
        http.services[transition.CALLERS[0]]['template']['containers'][0]['env'] = list(env.values())
    elif drift == 'coordinator_flag':
        env = transition.environment(http.services[transition.CALLERS[2]]['template'])
        env['RESUME_UPLOAD_COORDINATOR_SCAN_ENABLED']['value'] = 'true'
        http.services[transition.CALLERS[2]]['template']['containers'][0]['env'] = list(env.values())
    else:
        raw, generation = http.claim_objects[partial.CLAIM_OBJECT]
        http.claim_objects[partial.CLAIM_OBJECT] = (raw + b' ', generation)
    with pytest.raises((transition.TransitionDenied, probe.NativeProofDenied)):
        continue_final(http, source, previous, tmp_path)
    assert http.claim_posts == 2 and len(http.writes) == 10
    assert not (tmp_path / 'final-continuation/final-claim.started.json').exists()


@pytest.mark.parametrize('outcome', ['unknown_ack', 'raced_412'])
def test_final_claim_unknown_or_raced_never_opens_coordinator(monkeypatch, tmp_path, outcome):
    http, source, previous = failed_partial_after_analysis_ack(monkeypatch, tmp_path)
    http.claim_unknown = outcome == 'unknown_ack'
    http.claim_race = outcome == 'raced_412'
    with pytest.raises(transition.TransitionDenied):
        continue_final(http, source, previous, tmp_path)
    assert http.claim_posts == 3 and len(http.writes) == 10
    assert (tmp_path / 'final-continuation/final-claim.started.json').is_file()
    assert not (tmp_path / 'final-continuation/open.hirewiz-resume-coordinator.started.json').exists()


def test_final_rescue_unknown_coordinator_ack_cannot_replay(monkeypatch, tmp_path):
    http, source, previous = failed_partial_after_analysis_ack(monkeypatch, tmp_path)
    http.lost_at = 11
    with pytest.raises(transition.TransitionDenied):
        continue_final(http, source, previous, tmp_path)
    assert http.claim_posts == 3 and len(http.writes) == 11
    assert (tmp_path / 'final-continuation/open.hirewiz-resume-coordinator.started.json').is_file()
    with pytest.raises(transition.TransitionDenied, match='serving_phase_changed|claim_exists_or_unknown'):
        final.resume(NEW, {'build_id': transition.RESCUE_BUILD_ID, 'nonce': transition.RESCUE_NONCE},
            tmp_path / 'native', source, previous, tmp_path / 'second-final-continuation',
            source_tree_sha256=final.JOURNAL_TREE_SHA256, http=http, probe=probe, t=transition)
    assert len(http.writes) == 11


def test_done_before_ready_service_get_polls_without_another_patch(monkeypatch, tmp_path):
    http, source, previous = failed_partial_after_analysis_ack(monkeypatch, tmp_path)
    coordinator = transition.CALLERS[2]
    request = http.request
    state = {'before': None, 'done': False, 'stale': 2}
    def delayed(uri, *, method, body=None, headers=None, redirections):
        if method == 'PATCH' and urlsplit(uri).path.endswith('/services/' + coordinator):
            state['before'] = copy.deepcopy(http.services[coordinator])
        response, raw = request(uri, method=method, body=body, headers=headers, redirections=redirections)
        if method == 'GET' and '/operations/' in uri and state['before'] is not None:
            state['done'] = True
        if (method == 'GET' and uri.endswith('/services/' + coordinator)
                and state['done'] and state['stale']):
            state['stale'] -= 1
            return response, json.dumps(state['before']).encode()
        return response, raw
    monkeypatch.setattr(http, 'request', delayed)
    monkeypatch.setattr(transition.time, 'sleep', lambda _: None)
    result = continue_final(http, source, previous, tmp_path)
    assert result['status'] == 'document_refresh_final_caller_restoration_observed'
    assert state['stale'] == 0
    assert [name for name, _ in http.writes[10:]] == [coordinator]


def test_post_lro_permanent_template_mismatch_fails_without_replay(monkeypatch, tmp_path):
    http, source, previous = failed_partial_after_analysis_ack(monkeypatch, tmp_path)
    coordinator = transition.CALLERS[2]
    request = http.request
    state = {'done': False}
    def mismatched(uri, *, method, body=None, headers=None, redirections):
        response, raw = request(uri, method=method, body=body, headers=headers, redirections=redirections)
        if method == 'GET' and '/operations/' in uri and uri.endswith('owned-11'):
            state['done'] = True
        if method == 'GET' and uri.endswith('/services/' + coordinator) and state['done']:
            current = copy.deepcopy(http.services[coordinator])
            current['template']['timeout'] = '17s'
            return response, json.dumps(current).encode()
        return response, raw
    monkeypatch.setattr(http, 'request', mismatched)
    with pytest.raises(transition.TransitionDenied, match='readback_changed'):
        continue_final(http, source, previous, tmp_path)
    assert [name for name, _ in http.writes[10:]] == [coordinator]
    assert (tmp_path / 'final-continuation/open.hirewiz-resume-coordinator.acknowledged.json').is_file()
    assert not (tmp_path / 'final-continuation/open.hirewiz-resume-coordinator.verified.json').exists()


def test_post_lro_readback_deadline_is_bounded_without_replay(monkeypatch, tmp_path):
    http, source, previous = failed_partial_after_analysis_ack(monkeypatch, tmp_path)
    coordinator = transition.CALLERS[2]
    request = http.request
    state = {'before': None, 'done': False, 'now': 100.0}
    def never_ready(uri, *, method, body=None, headers=None, redirections):
        if method == 'PATCH' and urlsplit(uri).path.endswith('/services/' + coordinator):
            state['before'] = copy.deepcopy(http.services[coordinator])
        response, raw = request(uri, method=method, body=body, headers=headers, redirections=redirections)
        if method == 'GET' and '/operations/' in uri and uri.endswith('owned-11'):
            state['done'] = True
        if method == 'GET' and uri.endswith('/services/' + coordinator) and state['done']:
            return response, json.dumps(state['before']).encode()
        return response, raw
    def advance(seconds):
        state['now'] += max(2, seconds)
    monkeypatch.setattr(http, 'request', never_ready)
    monkeypatch.setattr(transition, 'time', SimpleNamespace(monotonic=lambda: state['now'], sleep=advance))
    with pytest.raises(transition.TransitionDenied, match='readback_deadline_reconcile_only'):
        continue_final(http, source, previous, tmp_path)
    assert [name for name, _ in http.writes[10:]] == [coordinator]
    assert not (tmp_path / 'final-continuation/open.hirewiz-resume-coordinator.verified.json').exists()


@pytest.mark.parametrize('drift', ['image', 'source', 'flag', 'traffic'])
def test_rescue_refuses_changed_guarded_api_without_any_patch(monkeypatch, tmp_path, drift):
    fixture_probe(monkeypatch)
    monkeypatch.setattr(transition, 'GUARDED_API_IMAGE', APP)
    monkeypatch.setattr(transition, 'GUARDED_API_REVISION', 'ai-resume-parser-00001-owned')
    monkeypatch.setattr(transition, 'GUARDED_API_TAG', 'api-hotfix-candidate')
    http = guarded_old_v2(Http())
    api = http.services[transition.CALLERS[0]]
    if drift == 'image':
        api['template']['containers'][0]['image'] = NEW
    elif drift == 'source':
        api['template']['labels']['hirewiz-source'] = 'f' * 40
    elif drift == 'flag':
        next(row for row in api['template']['containers'][0]['env']
             if row['name'] == 'RESUME_DIRECT_UPLOAD_ENABLED')['value'] = 'true'
    else:
        api['trafficStatuses'][0]['percent'] = 99
    with pytest.raises((transition.TransitionDenied, probe.NativeProofDenied)):
        run_rescue(tmp_path, http)
    assert http.writes == []

def test_actual_caller_verifier_full_resource_interface(monkeypatch, tmp_path):
    fixture_probe(monkeypatch)
    owner = transition.Journal(tmp_path / 'journal')
    try:
        controller = transition.Controller(Http(), probe, owner)
        probe.verify_caller_pins(controller, profile.IMAGE)
    finally:
        owner.close()

def test_worker_then_three_exact_caller_pins_then_captured_flags(monkeypatch, tmp_path):
    proof_calls = fixture_probe(monkeypatch)
    http = Http()
    before = copy.deepcopy(http.services)
    result = run(tmp_path, http)
    assert [name for name, _ in http.writes] == [*transition.CALLERS, transition.WORKER, *transition.CALLERS, *transition.CALLERS]
    for name, sent in http.writes[:3]:
        env = transition.environment(sent['template'])
        assert all(env[key]['value'] == 'false' for key in transition.PAUSE[name])
        assert env['DOCUMENT_WORKER_IMAGE_DIGEST']['value'] == profile.IMAGE
    for name in transition.CALLERS:
        current = http.services[name]
        env = transition.environment(current['template'])
        assert env['DOCUMENT_WORKER_IMAGE_DIGEST']['value'] == NEW
        assert env['DOCUMENT_WORKER_POLICY_SHA256']['value'] == probe.POLICY
        assert all(env[key]['value'] == 'true' for key in transition.PAUSE[name])
        assert current['template']['containers'][0]['image'] == APP
        assert env['DATABASE_URL'] == transition.environment(before[name]['template'])['DATABASE_URL']
        assert current['ingress'] == before[name]['ingress']
    assert [image for image, _ in proof_calls] == [profile.IMAGE, NEW, NEW, NEW]
    assert result['synchronous_callers_drained'] is False
    assert result['admission_restored'] is True
    assert all(path.stat().st_mode & 0o777 == 0o600 for path in (tmp_path / 'rollout').iterdir())


def test_known_prior_v1_pins_are_read_as_v1_but_worker_and_callers_move_to_v2(monkeypatch, tmp_path):
    proof_calls = fixture_probe(monkeypatch)
    http = Http()
    old_image = probe.REPOSITORY + '@sha256:' + 'e' * 64
    assert probe.POLICY != probe.LEGACY_POLICY
    for name, actual in http.services.items():
        for entry in actual['template']['containers'][0]['env']:
            if entry['name'] == 'DOCUMENT_WORKER_IMAGE_DIGEST':
                entry['value'] = old_image
            elif entry['name'] == 'DOCUMENT_WORKER_POLICY_SHA256':
                entry['value'] = probe.LEGACY_POLICY
            elif entry['name'] == 'DOCUMENT_WORKER_DOCX_RENDERER_VERSION':
                entry['value'] = probe.LEGACY_RENDERER
            elif entry['name'] == 'DOCUMENT_WORKER_DOCX_FONT_MANIFEST_SHA256':
                entry['value'] = probe.LEGACY_FONT
        if name == transition.WORKER:
            actual['template']['containers'][0]['image'] = old_image
    run(tmp_path, http)
    assert proof_calls[0][0] == old_image
    for _, sent in http.writes[:3]:
        env = transition.environment(sent['template'])
        assert env['DOCUMENT_WORKER_IMAGE_DIGEST']['value'] == old_image
        assert env['DOCUMENT_WORKER_POLICY_SHA256']['value'] == probe.LEGACY_POLICY
    worker = http.writes[3][1]['template']
    assert worker['containers'][0]['image'] == NEW
    assert transition.environment(worker)['DOCUMENT_WORKER_POLICY_SHA256']['value'] == probe.POLICY
    for actual in http.services.values():
        env = transition.environment(actual['template'])
        assert env['DOCUMENT_WORKER_IMAGE_DIGEST']['value'] == NEW
        assert env['DOCUMENT_WORKER_POLICY_SHA256']['value'] == probe.POLICY


@pytest.mark.parametrize('changed', [
    {'contract_version': 1}, {'contract_version': True}, {'contract_version': 2.0},
    {'required_case_count': 6}, {'required_case_count': 10.0},
    {'policy': '0' * 64}, {'image': APP},
])
def test_noncurrent_or_unbound_acceptance_never_authorizes_a_patch(monkeypatch, tmp_path, changed):
    fixture_probe(monkeypatch, **changed)
    http = Http()
    with pytest.raises(transition.TransitionDenied, match='current_acceptance_required'):
        run(tmp_path, http)
    assert http.writes == []


def test_each_patch_independently_requires_current_v2_ten_case_authority(monkeypatch, tmp_path):
    fixture_probe(monkeypatch)
    acceptance = probe.verify_successor()
    http = Http()
    owner = transition.Journal(tmp_path / 'journal')
    controller = transition.Controller(http, probe, owner, candidate_image=NEW)
    try:
        with pytest.raises(transition.TransitionDenied, match='current_acceptance_required'):
            controller.patch(transition.CALLERS[0], 'close', {},
                             acceptance=replace(acceptance, contract_version=1, required_case_count=6))
        assert http.writes == []
    finally:
        owner.close()


def test_a_legacy_second_verification_stops_after_close_without_worker_write(monkeypatch, tmp_path):
    fixture_probe(monkeypatch)
    accepted = probe.verify_successor()
    values = iter([accepted, replace(accepted, contract_version=1, required_case_count=6)])
    monkeypatch.setattr(probe, 'verify_successor', lambda *args: next(values))
    http = Http()
    with pytest.raises(transition.TransitionDenied, match='current_acceptance_required'):
        run(tmp_path, http)
    assert [name for name, _ in http.writes] == list(transition.CALLERS)
    for name in transition.CALLERS:
        env = transition.environment(http.services[name]['template'])
        assert all(env[key]['value'] == 'false' for key in transition.PAUSE[name])
    assert http.services[transition.WORKER]['template']['containers'][0]['image'] == profile.IMAGE


def test_unknown_observed_prior_policy_stops_before_any_write(monkeypatch, tmp_path):
    fixture_probe(monkeypatch)
    http = Http()
    for entry in http.services[transition.WORKER]['template']['containers'][0]['env']:
        if entry['name'] == 'DOCUMENT_WORKER_POLICY_SHA256':
            entry['value'] = '0' * 64
    with pytest.raises(probe.NativeProofDenied, match='serving_policy_unverified'):
        run(tmp_path, http)
    assert http.writes == []


@pytest.mark.parametrize('policy', [None, '', 'A' * 64, True])
def test_worker_image_change_requires_explicit_bounded_policy(policy):
    with pytest.raises(transition.TransitionDenied, match='worker_policy_unverified'):
        transition.mutate_template(service(transition.WORKER), transition.WORKER, 'worker', NONCE,
                                   NEW, {}, {}, worker_policy_sha256=policy)

@pytest.mark.parametrize('lost_at', [1, 4, 7, 9])
def test_unknown_write_never_repeats_or_reopens_remaining_callers(monkeypatch, tmp_path, lost_at):
    fixture_probe(monkeypatch)
    http = Http(lost_at=lost_at)
    with pytest.raises(transition.TransitionDenied, match='reconcile_only'):
        run(tmp_path, http)
    assert len(http.writes) == lost_at
    journal = tmp_path / 'rollout'
    assert len(list(journal.glob('*.started.json'))) == lost_at
    assert len(list(journal.glob('*.acknowledged.json'))) == lost_at - 1
    with pytest.raises(transition.TransitionDenied, match='existing_journal'):
        run(tmp_path, http)
    assert len(http.writes) == lost_at
    observed = transition.reconcile(journal, http=http)
    assert observed['writes'] == 0 and observed['unknown_without_ack'] == 1
    assert len(http.writes) == lost_at

def test_preexisting_mixed_pins_stop_before_any_write(monkeypatch, tmp_path):
    fixture_probe(monkeypatch)
    http = Http()
    http.services[transition.CALLERS[1]]['template']['containers'][0]['env'][0]['value'] = NEW
    with pytest.raises(transition.TransitionDenied, match='existing_mixed_pins'):
        run(tmp_path, http)
    assert http.writes == []

def test_coordinator_cleanup_and_omitted_flag_are_preserved():
    name = transition.CALLERS[2]
    current = service(name)
    current['template']['containers'][0]['env'].append({'name': 'RESUME_UPLOAD_COORDINATOR_CLEANUP_ENABLED', 'value': 'true'})
    captured = {'RESUME_DIRECT_UPLOAD_ENABLED': None,
        'RESUME_UPLOAD_COORDINATOR_SCAN_ENABLED': {'name': 'RESUME_UPLOAD_COORDINATOR_SCAN_ENABLED', 'value': 'true'}}
    closed = transition.mutate_template(current, name, 'close', NONCE, NEW, {}, captured)
    reopened = transition.mutate_template({'template': closed}, name, 'open', NONCE, NEW, {}, captured)
    env = transition.environment(reopened)
    assert 'RESUME_DIRECT_UPLOAD_ENABLED' not in env
    assert env['RESUME_UPLOAD_COORDINATOR_CLEANUP_ENABLED']['value'] == 'true'
