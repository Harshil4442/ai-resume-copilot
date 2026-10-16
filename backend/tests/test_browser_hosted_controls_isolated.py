"""Owned SQL/crypto Country and employment drafts, never provider applications."""
import base64
import copy
import os
from datetime import timedelta
from unittest.mock import patch

import pytest
from backend.tests import test_browser_assistance_isolated as flow
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature
from fastapi import HTTPException


@pytest.fixture
def owned():
    fixture = flow.BrowserAssistanceFlow()
    fixture.setUp()
    os.environ['BROWSER_ASSISTANCE_TYPED_CONTROLS_ENABLED'] = 'true'
    try:
        yield fixture
    finally:
        fixture.tearDown()


def inspection(owned, rows=1, current=False):
    owned.paired()
    states = [{'index': i, 'current_role': current} for i in range(rows)]
    app = owned.call('application', {'application_id': owned.app.id, 'review_protocol_version': 2, 'hosted_rows': states})
    values = {f['id']: str(current).lower() if f['type'] == 'checkbox' else str(rows) if f['type'] == 'button' else '' for f in app['field_mapping']}
    context = {'owned_hosted_form': True, 'rows': states}
    return owned.call('inspect', {'application_id': owned.app.id, 'inspection': {
        'outcome': 'inspected', 'review_protocol_version': 2, 'origin': flow.service.ORIGIN, 'url': app['target_url'],
        'frame_id': 0, 'document_id': 'owned_chrome_document', 'context': context, 'context_digest': flow.service.digest(context),
        'assisted_values': values, 'hosted_rows': states, 'manual_fields': [],
    }})


def answers(rows=1):
    result = {'country': 'in'}
    for i in range(rows):
        result.update({f'company-name-{i}': f'Explicit synthetic company {i}', f'title-{i}': 'Synthetic engineer',
                       f'start-date-month-{i}': 'January', f'start-date-year-{i}': '2020',
                       f'end-date-month-{i}': 'December', f'end-date-year-{i}': '2022', f'current-role-{i}_1': 'false'})
    return result


def revise(owned, review, values):
    return flow.service.candidate(owned.db, 1, 'revise', flow.schemas.CandidateRequest(
        identity=review['id'], review_digest=review['review_digest'], hosted_answers=values))


def approve(owned, review):
    return flow.service.candidate(owned.db, 1, 'approve', flow.schemas.CandidateRequest(
        identity=review['id'], review_digest=review['review_digest'], confirmed=True))


def test_five_rows_fit_one_v2_review_with_signed_sql_actions_and_no_fee(owned):
    review = inspection(owned, rows=5)
    original_package, original_answers = owned.app.package_digest, copy.deepcopy(owned.app.answers)
    updated = revise(owned, review, answers(5))
    assert updated['review_digest'] != review['review_digest']
    assert len(updated['payload']['fields']) == 38
    assert updated['expires_at_ms'] == review['expires_at_ms']
    approve(owned, updated)
    for index, field in enumerate(updated['payload']['fields']):
        lease = owned.call('begin', owned.begin_body(updated, index))['lease']
        raw = base64.urlsafe_b64decode(lease['signature'] + '=' * (-len(lease['signature']) % 4))
        flow.service._public_key(flow.service.public_signer())[1].verify(
            encode_dss_signature(int.from_bytes(raw[:32], 'big'), int.from_bytes(raw[32:], 'big')),
            b'hirewiz.browser-assistance.lease.v1\n' + flow.service.canonical(lease['payload']), ec.ECDSA(hashes.SHA256()))
        assert lease['payload']['field_id'] == field['id'] and lease['payload']['value'] == field['value']
        assert lease['payload']['control_type'] == field['type']
        if field['type'] == 'combobox':
            assert lease['payload']['option_label'] == field['value_label']
        result = owned.call('outcome', {'review_id': updated['id'], 'action_id': lease['payload']['action_id'], 'outcome': 'filled'})
    assert result['state'] == 'filled' and result['filled_count'] == 38
    owned.db.refresh(owned.app)
    assert owned.app.answers == original_answers and owned.app.package_digest == original_package
    assert owned.app.status == 'ready' and owned.app.receipt is None and owned.app.charged_credits == 0
    assert owned.db.query(flow.employer.ServiceCreditReservation).count() == 0


@pytest.mark.parametrize('invalid', [
    {'citizenship': 'India'}, {'work_authorization': 'true'}, {'country': 'India'}, {'country': 'injected'},
    {'current-role-0_1': 'yes'}, {'employment-add-1': '3'}, {'company-name-5': 'Outside bound'},
    {'start-date-year-0': '20'}, {'start-date-year-0': '202A'}, {'company-name-0': '  inferred  '},
    {'current-role-0_1': 'true', 'end-date-month-0': 'January'},
    {'start-date-month-0': 'December', 'start-date-year-0': '2024', 'end-date-month-0': 'January', 'end-date-year-0': '2024'},
])
def test_invalid_or_inferred_hosted_answers_refused(owned, invalid):
    review = inspection(owned)
    with pytest.raises(HTTPException):
        revise(owned, review, invalid)
    owned.db.rollback()
    assert owned.db.get(flow.models.BrowserAssistanceReview, review['id']).review_digest == review['review_digest']


def test_revision_requires_new_digest_then_explicit_approval(owned):
    review = inspection(owned)
    revised = revise(owned, review, answers())
    assert revised['state'] == 'draft'
    with pytest.raises(HTTPException):
        approve(owned, review)
    owned.db.rollback()
    with pytest.raises(HTTPException):
        owned.call('begin', owned.begin_body(revised))
    owned.db.rollback()
    approve(owned, revised)
    with pytest.raises(HTTPException):
        revise(owned, revised, {'country': 'us'})


@pytest.mark.parametrize('mutation', ['owner', 'revoke', 'cancel', 'expired', 'flag', 'source'])
def test_revision_rechecks_authority_and_original_expiry(owned, mutation):
    review = inspection(owned)
    candidate = flow.schemas.CandidateRequest(identity=review['id'], review_digest=review['review_digest'], hosted_answers=answers())
    if mutation == 'revoke':
        owned.db.get(flow.models.BrowserAssistanceDevice, owned.device_id).state = 'revoked'
    if mutation == 'cancel':
        owned.db.get(flow.models.BrowserAssistanceReview, review['id']).state = 'cancelled'
    if mutation == 'expired':
        owned.db.get(flow.models.BrowserAssistanceReview, review['id']).expires_at = flow.service.now() - timedelta(seconds=1)
    if mutation == 'flag':
        os.environ['BROWSER_ASSISTANCE_TYPED_CONTROLS_ENABLED'] = 'false'
    if mutation == 'source':
        owned.db.get(flow.employer.EmployerSource, 'source_test').enabled = False
    owned.db.commit()
    with pytest.raises(HTTPException):
        flow.service.candidate(owned.db, 2 if mutation == 'owner' else 1, 'revise', candidate)


def test_checkbox_and_add_exact_single_lease_unknown_never_replays(owned):
    updated = revise(owned, inspection(owned), {'current-role-0_1': 'true', 'employment-add-1': '2'})
    approve(owned, updated)
    index = len(updated['payload']['fields']) - 2
    for position in range(index):
        lease = owned.call('begin', owned.begin_body(updated, position))['lease']['payload']
        owned.call('outcome', {'review_id': updated['id'], 'action_id': lease['action_id'], 'outcome': 'filled'})
    request = owned.request('begin', owned.begin_body(updated, index))
    begun = flow.service.device(owned.db, owned.origin, request)
    assert begun['lease']['payload']['control_type'] == 'checkbox'
    assert flow.service.device(owned.db, owned.origin, request)['state'] == 'unknown'
    owned.call('outcome', {'review_id': updated['id'], 'action_id': begun['lease']['payload']['action_id'], 'outcome': 'unknown'})
    with pytest.raises(HTTPException):
        owned.call('begin', owned.begin_body(updated, index + 1))


def test_old_reviews_never_acquire_hosted_controls(owned):
    review = inspection(owned)
    assert review['payload']['hosted']['controls'][0]['id'] == 'country'
    assert len(flow.service.field_mapping(owned.app, 1)) == 7 and len(flow.service.field_mapping(owned.app, 2)) == 7
    for version, rows in [(1, [{'index': 0, 'current_role': False}]), (2, [{'index': 1, 'current_role': False}]),
                          (2, [{'index': i, 'current_role': False} for i in range(6)])]:
        with pytest.raises(HTTPException):
            flow.service.field_mapping(owned.app, version, rows)


def test_near_review_expiry_caps_field_lease(owned):
    review = revise(owned, inspection(owned), answers())
    approve(owned, review)
    fixed = flow.service.now()
    row = owned.db.get(flow.models.BrowserAssistanceReview, review['id'])
    row.expires_at = fixed + timedelta(milliseconds=1750)
    owned.db.commit()
    with patch.object(flow.service, 'now', return_value=fixed):
        lease = owned.call('begin', owned.begin_body(review))['lease']['payload']
    assert lease['deadline'] == flow.service._ms(row.expires_at)


def reseal(owned):
    posting = owned.db.get(flow.employer.EmployerPosting, owned.app.posting_id)
    source = owned.db.get(flow.employer.EmployerSource, posting.source_id)
    artifact = owned.db.get(flow.employer.SealedApplicationArtifact, owned.app.artifact_id)
    owned.app.package_digest = flow.employer_service._package_digest(owned.app, posting, source, artifact)
    owned.db.commit()


def test_hosted_answers_never_inherit_stale_application_answer_keys(owned):
    owned.app.answers = {**owned.app.answers, 'country': 'in', 'company-name-0': 'Unreviewed stale company'}
    reseal(owned)
    review = inspection(owned)
    assert not {'country', 'company-name-0'}.intersection(review['payload']['reviewed_answers'])
    assert any('does not state citizenship or work authorization' in item for item in review['payload']['disclosures'])


def test_clearing_revised_draft_restores_required_manual_controls(owned):
    first = revise(owned, inspection(owned), answers())
    assert not any(field['id'] == 'company-name-0' for field in first['payload']['manual_fields'])
    second = revise(owned, first, {'country': 'in'})
    assert any(field['id'] == 'company-name-0' and field['required'] for field in second['payload']['manual_fields'])
    assert 'company-name-0' not in second['payload']['reviewed_answers']


def test_zero_action_draft_can_be_saved_but_never_approved(owned):
    owned.app.answers = {}
    reseal(owned)
    review = inspection(owned)
    assert review['payload']['fields'] == []
    saved = revise(owned, review, {})
    assert saved['state'] == 'draft' and saved['payload']['fields'] == []
    with pytest.raises(HTTPException):
        approve(owned, saved)
