"""Frozen two-hold accounting shape; synthetic owners/prices, no SQL or providers."""
from __future__ import annotations

import copy
import hashlib
from datetime import UTC, datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from scripts import email_password_schema21_contract as contract
from scripts import email_password_schema21_step as step


def cohort():
    # Actual observed dimensions are retained: two failed/waived single attempts,
    # 39,534 estimated input tokens, 8,192 output limit and 60,371 micros each.
    # Identifiers and this price policy are synthetic; this is not invoice proof.
    price = dict(provider='google', model='synthetic-model', api_base='https://generativelanguage.googleapis.com',
        version='synthetic-historical-price', price_source_url='https://example.com/price',
        output_limit_source_url='https://example.com/limit', usage_source_url='https://example.com/usage',
        valid_from='2026-10-01T00:00:00Z', expires_at='2026-10-23T00:00:00Z',
        input_rate_micros_per_million=750_000, output_rate_micros_per_million=3_750_000,
        max_input_tokens=100_000, max_output_tokens=8192, output_limit_parameter='max_output_tokens',
        token_estimator='utf8-json-bytes-framing-v1')
    frozen = dict(version='synthetic-historical-policy', currency='USD', expense_funding='legacy_premium',
        expense_policy_version='synthetic-historical-expense', operation_limits_micros={'resume_tailor':500_000},
        pricing_quotes=[price])
    attempt = price | dict(operation_policy_version=frozen['version'], admission_policy_version=frozen['version'],
        authorized_ceiling_micros=500_000, expense_policy_version=frozen['expense_policy_version'],
        expense_funding='legacy_premium', expense_pool='legacy_premium')
    runs, events, liabilities = [], [], []
    for number in (1, 2):
        created = datetime(2026, 10, 20 + number, 20, 41, 38)
        run_id, event_id, liability_id, group = (f'{prefix}_synthetic{number}' for prefix in ('run','mdl','mcl','fin'))
        runs.append(dict(id=run_id, user_id=number, operation='resume_tailor', status='failed', usage_state='waived',
            estimated_units=2, committed_units=0, attempt_count=1, generation_attempt_count=1,
            provider='google', model='synthetic-model', model_cost_group_id=group, model_cost_quote=copy.deepcopy(frozen),
            model_cost_ceiling_micros=500_000, model_cost_reserved_micros=60371, model_cost_settled_micros=0,
            model_cost_state='active', created_at=created, completed_at=created+timedelta(seconds=8)))
        events.append(dict(id=event_id, user_id=number, analysis_run_id=run_id, liability_id=liability_id,
            provider='google', model='synthetic-model', status='failed', cost_state='outcome_unknown', attempt_number=1,
            pricing_quote=copy.deepcopy(attempt), input_tokens=39534, output_tokens=0, tokens_estimated=True,
            estimated_cost_micros=60371, reserved_cost_micros=60371, settled_cost_micros=None, usage_provenance=None,
            token_estimate_provenance=price['token_estimator'], output_token_limit=8192,
            created_at=created+timedelta(seconds=7), settled_at=None))
        liability_quote = {key: value for key,value in attempt.items() if key in step.financial.LiabilityQuote.model_fields}
        liability_quote['expense_pool'] = 'failed_work'
        liabilities.append(dict(id=liability_id, financial_group_id=group, attempt_number=1, provider='google',
            model='synthetic-model', endpoint_key=hashlib.sha256(price['api_base'].encode()).hexdigest(), currency='USD',
            pricing_quote=liability_quote, input_token_estimate=39534, reserved_cost_micros=60371,
            settled_cost_micros=None, input_tokens=None, output_tokens=None, usage_provenance=None,
            cost_state='outcome_unknown', created_at=created+timedelta(seconds=6), settled_at=None))
    return runs, events, liabilities


def test_exact_terminal_two_hold_shape_is_retained_without_mutating_accounting():
    rows = cohort()
    before = copy.deepcopy(rows)
    assert step.historical_holds(*rows) == ({'run_synthetic1','run_synthetic2'}, {'mdl_synthetic1','mdl_synthetic2'})
    assert rows == before
    assert sum(row['reserved_cost_micros'] for row in rows[2]) == 120742
    assert all(row['settled_cost_micros'] is None for row in rows[2])


def test_actual_mixed_native_utc_and_naive_cost_times_are_comparison_copies_only():
    rows = cohort()
    for row in rows[0]:
        row['created_at'] = row['created_at'].replace(tzinfo=UTC)
        row['completed_at'] = row['completed_at'].replace(tzinfo=UTC)
    for row in rows[1]:
        row['created_at'] = row['created_at'].replace(tzinfo=UTC)
    before = copy.deepcopy(rows)
    assert step.historical_holds(*rows) == ({'run_synthetic1','run_synthetic2'}, {'mdl_synthetic1','mdl_synthetic2'})
    assert rows == before
    assert rows[2][0]['created_at'].tzinfo is None


@pytest.mark.parametrize(('collection','field','value'), [
    (0,'created_at','2026-10-21T20:41:38Z'),
    (0,'completed_at',datetime(2026,10,20)),
    (1,'created_at',datetime(2026,10,21,20,41,37,tzinfo=UTC)),
    (2,'created_at',datetime(2026,10,21,20,41,44,tzinfo=timezone(timedelta(hours=5,minutes=30)))),
])
def test_invalid_reversed_or_unobserved_non_utc_timestamp_is_denied(collection,field,value):
    rows = cohort()
    rows[collection][0][field] = value
    with pytest.raises(contract.Schema21Denied,match='quote_refused'):
        step.historical_holds(*rows)


@pytest.mark.parametrize(('collection','field','value'), [
    (0,'status','running'), (0,'usage_state','committed'), (0,'committed_units',2),
    (0,'generation_attempt_count',2), (0,'attempt_count',True), (0,'model_cost_reserved_micros',0),
    (0,'model_cost_settled_micros',1), (0,'model_cost_group_id','different_group'),
    (0,'completed_at',None), (0,'model_cost_quote',{}),
    (1,'user_id',999), (1,'analysis_run_id','run_absent'), (1,'liability_id','mcl_absent'),
    (1,'cost_state','reserved'), (1,'status','attempted'), (1,'input_tokens',39535),
    (1,'output_tokens',1), (1,'tokens_estimated',False), (1,'output_token_limit',8193),
    (1,'token_estimate_provenance','untrusted'), (1,'settled_cost_micros',0),
    (2,'cost_state','usage_unavailable'), (2,'reserved_cost_micros',60370),
    (2,'financial_group_id','unjoined'), (2,'attempt_number',2), (2,'endpoint_key','0'*64),
    (2,'currency','INR'), (2,'settled_cost_micros',0), (2,'input_tokens',39534),
])
def test_active_stale_swapped_or_malformed_exposure_is_denied(collection, field, value):
    rows = cohort()
    rows[collection][0][field] = value
    with pytest.raises(contract.Schema21Denied):
        step.historical_holds(*rows)


def test_quote_change_or_duplicate_binding_cannot_hide_an_unresolved_liability():
    for mutate in (
        lambda rows: rows[2][0]['pricing_quote'].__setitem__('input_rate_micros_per_million',1),
        lambda rows: rows[2][0]['pricing_quote'].__setitem__('expense_pool','promotion'),
        lambda rows: rows[1].append(rows[1][0] | {'id':'mdl_other'}),
        lambda rows: rows[2].append(rows[2][0] | {'id':'mcl_detached','financial_group_id':'fin_detached'}),
        lambda rows: rows[0].clear(),
    ):
        rows = cohort()
        mutate(rows)
        with pytest.raises(contract.Schema21Denied):
            step.historical_holds(*rows)


@pytest.mark.parametrize('collection', [0, 1, 2])
def test_sql_side_oversized_quote_cannot_be_adopted_as_null_or_valid_history(collection):
    rows = cohort()
    rows[collection][0]['quote_oversized'] = True
    with pytest.raises(contract.Schema21Denied, match='history_bound'):
        step.historical_holds(*rows)


def test_expired_today_quote_is_historical_only_but_stale_at_admission_is_refused():
    rows = cohort()
    assert step.historical_holds(*rows)  # Fixed expiry is already past; no current_policy or funding call.
    rows[2][0]['created_at'] = datetime(2026, 9, 30)
    rows[0][0]['created_at'] = datetime(2026, 9, 29)
    with pytest.raises(contract.Schema21Denied, match='quote_refused'):
        step.historical_holds(*rows)


class Rows:
    def __init__(self, rows):
        self.rows = rows

    def scalar_one(self):
        return self.rows

    def mappings(self):
        return self

    def __iter__(self):
        return iter(self.rows)


@pytest.mark.parametrize('active_fragment', [
    'browser_assistance_reviews', 'browser_assistance_requests', 'pg_prepared_xacts',
    'model_call_events WHERE status', 'analysis_runs WHERE status', 'employer_applications',
    'employer_application_attempts', 'service_credit_reservations', 'resume_uploads', 'resume_upload_cleanups',
])
def test_every_existing_active_work_predicate_still_refuses_before_history(active_fragment):
    class Connection:
        def execute(self, statement):
            sql = str(statement)
            assert sql.startswith('SELECT count(*)')
            return Rows(1 if active_fragment in sql else 0)
    with pytest.raises(contract.Schema21Denied, match='active_upload_or_cleanup'):
        step.drained(Connection(), SimpleNamespace(namespace='synthetic'))


def test_drained_reads_only_accounting_projection_and_passes_coherent_history():
    runs, events, liabilities = cohort()
    queries = []
    class Connection:
        def execute(self, statement):
            sql = str(statement)
            queries.append(sql)
            if sql.startswith('SELECT count(*)'):
                return Rows(0)
            for name, rows in [('analysis_runs',runs), ('model_call_events',events), ('model_cost_liabilities',liabilities)]:
                if f'.{name} ' in sql:
                    return Rows(rows)
            pytest.fail('unexpected query')
    step.drained(Connection(), SimpleNamespace(namespace='synthetic'))
    assert len(queries) == 13
    assert all(sql.startswith('SELECT ') for sql in queries)
    assert all(word not in ''.join(queries) for word in ('input_payload','result_payload','password_hash','token_sha256'))
    assert all('CASE WHEN octet_length(' in sql and 'AS quote_oversized' in sql for sql in queries[-3:])
    assert ['<=1048576','<=16384','<=8192'] == [
        next(bound for bound in ['<=1048576','<=16384','<=8192'] if bound in sql) for sql in queries[-3:]]
