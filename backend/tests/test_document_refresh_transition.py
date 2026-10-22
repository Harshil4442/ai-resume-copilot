"""Maintenance ordering/unknown writes; synthetic transport, no cloud."""
from __future__ import annotations

import copy
import importlib.util
import json
import sys
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlsplit

import pytest

ROOT = Path(__file__).resolve().parents[2]
def load(name):
    spec = importlib.util.spec_from_file_location('transition_fixture_' + name, ROOT / 'infra/gcp' / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module
transition = load('document_refresh_transition')
probe = load('document_refresh_probe')
NONCE = 'a' * 32
NEW = probe.REPOSITORY + '@sha256:' + 'b' * 64
APP = 'us-central1-docker.pkg.dev/ai-resume-parser-482412/cloud-run-source-deploy/hirewiz@sha256:' + 'c' * 64

def service(name):
    resource = transition.PARENT + '/services/' + name
    revision = resource + '/revisions/' + name + '-00001-owned'
    env = [dict(name=k, value=v) for k, v in zip(transition.PINS,
        (probe.BASE_IMAGE, probe.POLICY, probe.RENDERER, probe.FONT), strict=True)]
    env.append({'name': 'DOCUMENT_WORKER_URL', 'value': 'https://hirewiz-document-inspector-mqff62d75a-uc.a.run.app/inspect'})
    if name != transition.WORKER:
        env.extend([{'name': k, 'value': 'true'} for k in transition.PAUSE[name]])
        env.append({'name': 'DATABASE_URL', 'valueSource': {'secretKeyRef': {'secret': 'owned-db', 'version': '2'}}})
    else:
        env = [row for row in env if row['name'] in transition.PINS[:2]]
    return {'name': resource, 'uid': probe.SERVICE_UID if name == transition.WORKER else '01234567-0123-0123-0123-012345678901',
        'generation': '1', 'observedGeneration': '1', 'etag': 'owned1',
        'template': {'revision': revision, 'serviceAccount': transition.ACCOUNTS[name], 'timeout': '90s',
                     'containers': [{'image': probe.BASE_IMAGE if name == transition.WORKER else APP, 'env': env}]},
        'terminalCondition': {'state': 'CONDITION_SUCCEEDED'}, 'latestCreatedRevision': revision, 'latestReadyRevision': revision,
        'traffic': [{'type': 'TRAFFIC_TARGET_ALLOCATION_TYPE_LATEST', 'percent': 100}],
        'trafficStatuses': [{'revision': revision, 'percent': 100}], 'labels': {'retained': 'owned'},
        'ingress': 'INGRESS_TRAFFIC_INTERNAL_ONLY' if name != transition.CALLERS[0] else 'INGRESS_TRAFFIC_ALL'}

class Http:
    def __init__(self, *, lost_at=None):
        self.services = {name: service(name) for name in transition.SERVICES}
        self.writes, self.lost_at = [], lost_at
    def request(self, uri, *, method, body, headers, redirections):
        assert redirections == 0
        path = urlsplit(uri).path.removeprefix('/v2/')
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
            if len(self.writes) == self.lost_at:
                raise transition.TransitionDenied('document_transition_native_outcome_unknown_reconcile_only')
            value = {'name': transition.PARENT + '/operations/owned-' + str(len(self.writes))}
        elif '/operations/' in path:
            value = {'name': path, 'done': True}
        elif '/revisions/' in path:
            name = path.split('/services/')[1].split('/')[0]
            value = copy.deepcopy(self.services[name]['template'])
            value.update(name=path, uid='01234567-0123-0123-0123-012345678901', service=name,
                         conditions=[{'type': 'Ready', 'state': 'CONDITION_SUCCEEDED'}])
        else:
            value = self.services[path.rsplit('/', 1)[-1]]
        return SimpleNamespace(status=200), json.dumps(value).encode()

def fixture_probe(monkeypatch, *, contract_version=2, required_case_count=10, policy=None, image=NEW):
    acceptance = probe.NativeAcceptance(image, '01234567-0123-0123-0123-012345678901', 'owned-build', 'a' * 64,
        'b' * 64, {}, transition.PARENT + '/jobs/hirewiz-doc-six-' + NONCE[:12],
        'owned-job-uid', 'owned-execution', 'owned-execution-uid', 'c' * 64, 'd' * 64,
        probe.POLICY if policy is None else policy, probe.FONT, probe.RENDERER, '2026-10-19T00:00:00+00:00',
        contract_version, required_case_count)
    monkeypatch.setattr(probe, 'verify_successor', lambda *args: acceptance)
    calls = []
    monkeypatch.setattr(probe, 'verify_current_successor', lambda image, actual: calls.append((image, actual['uid'])))
    return calls

def run(tmp_path, http):
    return transition.transition(NEW, {'nonce': NONCE, 'build_id': '01234567-0123-0123-0123-012345678901'},
        tmp_path / 'native', tmp_path / 'rollout', http=http, probe=probe)

def test_actual_caller_verifier_full_resource_interface(monkeypatch, tmp_path):
    fixture_probe(monkeypatch)
    owner = transition.Journal(tmp_path / 'journal')
    try:
        controller = transition.Controller(Http(), probe, owner)
        probe.verify_caller_pins(controller, probe.BASE_IMAGE)
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
        assert env['DOCUMENT_WORKER_IMAGE_DIGEST']['value'] == probe.BASE_IMAGE
    for name in transition.CALLERS:
        current = http.services[name]
        env = transition.environment(current['template'])
        assert env['DOCUMENT_WORKER_IMAGE_DIGEST']['value'] == NEW
        assert env['DOCUMENT_WORKER_POLICY_SHA256']['value'] == probe.POLICY
        assert all(env[key]['value'] == 'true' for key in transition.PAUSE[name])
        assert current['template']['containers'][0]['image'] == APP
        assert env['DATABASE_URL'] == transition.environment(before[name]['template'])['DATABASE_URL']
        assert current['ingress'] == before[name]['ingress']
    assert len(proof_calls) == 3 and result['synchronous_callers_drained'] is False
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
    assert http.services[transition.WORKER]['template']['containers'][0]['image'] == probe.BASE_IMAGE


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
