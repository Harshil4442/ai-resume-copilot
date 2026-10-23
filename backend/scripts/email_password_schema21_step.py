"""One caller-owned schema19→21 transaction; no credential acquisition or replay."""

from __future__ import annotations

import hashlib
import re
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from alembic.config import Config
from pydantic import ValidationError
from sqlalchemy import text

from alembic import command
from app.services.model_cost_policy import ModelCostPolicy, ModelPriceQuote
from scripts import email_password_schema21_contract as contract
from scripts import monetary_cutover_preflight as financial
from scripts import native_resume_schema20_step as previous
from scripts.browser_schema19_step import MigrationIdentity

BEFORE = contract.BEFORE
LOCK_ID = 721_994_373
verify_identity = previous.verify_identity
schema_acl = previous.schema_acl
effective_owner = previous.effective_owner


def marker(connection, identity: MigrationIdentity) -> str:
    return contract.marker(connection, identity.namespace)


def verify_financial_catalog(connection, module: Any) -> None:
    """Keep the fresh catalog checks; historical holds are not fresh-cutover work."""
    tables = list(connection.execute(text(
        "SELECT c.relname FROM pg_catalog.pg_class c WHERE c.relnamespace=2200 "
        "AND c.relkind IN ('r','p','v','m','f') ORDER BY c.oid LIMIT 257"
    )).scalars())
    contract.need(1 <= len(tables) <= 256 and len(set(tables)) == len(tables)
                  and set(module.TABLES) | {'alembic_version'} <= set(tables)
                  and 'model_cost_liabilities' in tables
                  and module.CANDIDATE_TABLES <= set(tables),
                  'schema21_financial_catalog_refused')
    module._candidate_projection_shape(connection, 2200)
    module._extension_shape(connection, 2200, '20261015_0018')


def historical_holds(runs: list[dict], events: list[dict], liabilities: list[dict]) -> tuple[set[str], set[str]]:
    """Validate retained failed single-attempt holds, never settle or authorize work.

    Expiry is checked at the original admission, not today. Existing complete
    financial/retained digests bind every byte across capture, DDL and rollout.
    Detached unknown, incomplete, multi-attempt or active accounting is refused.
    """
    def utc(stamp):
        # Native legacy tables return aware UTC; detached cost evidence uses
        # the application's naive-UTC convention. Compare copies, never rows.
        contract.need(isinstance(stamp, datetime)
            and (stamp.tzinfo is None or stamp.utcoffset() == timedelta(0)),
            'schema21_historical_hold_quote_refused')
        return stamp.replace(tzinfo=UTC) if stamp.tzinfo is None else stamp.astimezone(UTC)

    def indexed(rows):
        contract.need(len(rows) <= 256 and all(type(row.get('id')) is str
            and re.fullmatch(r'[A-Za-z0-9_:-]{1,64}', row['id'])
            and row.get('quote_oversized', False) is False for row in rows),
            'schema21_model_history_bound')
        result = {row['id']: row for row in rows}
        contract.need(len(result) == len(rows), 'schema21_model_history_duplicate')
        return result

    by_run, by_event, by_liability = indexed(runs), indexed(events), indexed(liabilities)
    settled = [financial._known_settled(row) for row in liabilities]
    known, _ = financial._group_resolution(liabilities, settled)
    historical_runs, historical_events = set(), set()
    for liability, resolved in zip(liabilities, known, strict=True):
        if resolved:
            continue
        linked = [event for event in events if event.get('liability_id') == liability['id']]
        group = [row for row in liabilities if row.get('financial_group_id') == liability.get('financial_group_id')]
        contract.need(len(linked) == len(group) == 1, 'schema21_historical_hold_binding_refused')
        event = linked[0]
        run = by_run.get(event.get('analysis_run_id'))
        contract.need(run is not None, 'schema21_historical_hold_binding_refused')
        assert run is not None
        contract.need(all(type(run.get(name)) is int for name in
            ('committed_units','estimated_units','attempt_count','generation_attempt_count',
             'model_cost_settled_micros','model_cost_reserved_micros','model_cost_ceiling_micros'))
            and type(event.get('attempt_number')) is int and type(liability.get('attempt_number')) is int
            and run.get('status') == 'failed' and run.get('usage_state') == 'waived'
            and run.get('committed_units') == 0 and type(run.get('estimated_units')) is int
            and run['estimated_units'] > 0 and run.get('attempt_count') == run.get('generation_attempt_count') == 1
            and run.get('model_cost_state') == 'active' and run.get('model_cost_settled_micros') == 0
            and type(run.get('user_id')) is int and run['user_id'] > 0
            and event.get('user_id') == run['user_id']
            and type(run.get('model_cost_group_id')) is str
            and re.fullmatch(r'[A-Za-z0-9_:-]{1,64}', run['model_cost_group_id'])
            and run['model_cost_group_id'] == liability.get('financial_group_id')
            and event.get('status') == 'failed' and event.get('cost_state') == liability.get('cost_state') == 'outcome_unknown'
            and event.get('attempt_number') == liability.get('attempt_number') == 1
            and len([row for row in events if row.get('analysis_run_id') == run['id']]) == 1
            and all(liability.get(name) is None for name in
                ('input_tokens','output_tokens','usage_provenance','settled_cost_micros','settled_at'))
            and all(event.get(name) is None for name in ('usage_provenance','settled_cost_micros','settled_at'))
            and event.get('tokens_estimated') is True and event.get('output_tokens') == 0,
            'schema21_historical_hold_state_refused')
        try:
            quote = financial.LiabilityQuote.model_validate(liability['pricing_quote'])
            frozen = ModelCostPolicy.model_validate(run['model_cost_quote'])
            attempt = event['pricing_quote']
            price = ModelPriceQuote.model_validate({key: attempt[key] for key in ModelPriceQuote.model_fields})
            saved = next(item for item in frozen.pricing_quotes
                if (item.provider, item.model, item.api_base) == (price.provider, price.model, price.api_base))
            expected_quote = {key: attempt[key] for key in financial.LiabilityQuote.model_fields if key in attempt}
            expected_quote['expense_pool'] = 'failed_work'
            ceiling = run['model_cost_ceiling_micros']
            estimate = liability['input_token_estimate']
            reserved = liability['reserved_cost_micros']
            created, event_at, completed = (utc(stamp) for stamp in
                (run['created_at'], event['created_at'], run['completed_at']))
            liability_at = utc(liability['created_at'])
            contract.need(quote.model_dump() == financial.LiabilityQuote.model_validate(expected_quote).model_dump()
                and quote.expense_pool == 'failed_work'
                and quote.operation_policy_version == frozen.version
                and quote.expense_policy_version == frozen.expense_policy_version
                and quote.expense_funding == frozen.expense_funding
                and attempt.get('expense_pool') in {quote.expense_funding, 'failed_work'}
                and price.model == event.get('model') == liability.get('model') == run.get('model')
                and price.provider == event.get('provider') == liability.get('provider') == run.get('provider')
                and liability.get('currency') == 'USD'
                and liability.get('endpoint_key') == hashlib.sha256(price.api_base.encode()).hexdigest()
                and price.model_dump(exclude={'max_input_tokens','max_output_tokens'})
                    == saved.model_dump(exclude={'max_input_tokens','max_output_tokens'})
                and price.max_input_tokens <= saved.max_input_tokens and price.max_output_tokens <= saved.max_output_tokens
                and type(estimate) is int and 0 < estimate <= quote.max_input_tokens
                and event.get('input_tokens') == estimate
                and event.get('token_estimate_provenance') == quote.token_estimator
                and event.get('output_token_limit') == quote.max_output_tokens
                and type(reserved) is int and reserved == financial._quoted_cost(quote, estimate, quote.max_output_tokens)
                and reserved == event.get('reserved_cost_micros') == event.get('estimated_cost_micros') == run.get('model_cost_reserved_micros')
                and type(ceiling) is int and 0 < reserved <= quote.authorized_ceiling_micros <= ceiling
                and ceiling <= frozen.operation_limits_micros[run['operation']]
                and created <= liability_at <= event_at <= completed
                and price.valid_from.astimezone(UTC) <= liability_at < price.expires_at.astimezone(UTC),
                'schema21_historical_hold_quote_refused')
        except (ValidationError, KeyError, TypeError, ValueError, StopIteration):
            raise contract.Schema21Denied('schema21_historical_hold_quote_refused') from None
        historical_runs.add(run['id'])
        historical_events.add(event['id'])
    # Every other row retains the existing strict financial conditions.
    for run in by_run.values():
        if run['id'] not in historical_runs:
            state, cost, quote = run.get('model_cost_state'), run.get('model_cost_settled_micros'), run.get('model_cost_quote')
            contract.need(run.get('model_cost_reserved_micros') == 0 and type(cost) is int and cost >= 0
                and state in {'unquoted','active'}
                and (state != 'unquoted' or quote is None and cost == 0)
                and (state != 'active' or quote is not None and type(run.get('model_cost_ceiling_micros')) is int
                    and 0 < run['model_cost_ceiling_micros'] >= cost), 'schema21_other_run_finances_refused')
    for event in by_event.values():
        if event['id'] not in historical_events:
            state = event.get('cost_state')
            contract.need(state in {'unavailable','settled'}
                and (state != 'settled' or event.get('liability_id') in by_liability and event.get('settled_at') is not None
                    and type(event.get('settled_cost_micros')) is int and event['settled_cost_micros'] >= 0)
                and (state != 'unavailable' or all(event.get(name) is None for name in
                    ('liability_id','pricing_quote','reserved_cost_micros','settled_cost_micros','settled_at',
                     'token_estimate_provenance','usage_provenance','output_token_limit'))),
                'schema21_other_event_finances_refused')
    return historical_runs, historical_events


def drained(connection, identity: MigrationIdentity) -> None:
    """Zero live work; coherent terminal cost uncertainty is retained, not drained."""
    namespace = '"' + identity.namespace + '"'
    # Do not weaken or repurpose the old fresh/schema19/schema20 validators.
    queries = (
        f"SELECT count(*) FROM {namespace}.browser_assistance_reviews WHERE state IN ('approved','running')",
        f"SELECT count(*) FROM {namespace}.browser_assistance_requests WHERE state NOT IN ('completed','filled','blocked','unknown')",
        "SELECT count(*) FROM pg_catalog.pg_prepared_xacts WHERE database=current_database()",
        f"SELECT count(*) FROM {namespace}.model_call_events WHERE status NOT IN ('succeeded','failed','invalid_output')",
        f"SELECT count(*) FROM {namespace}.analysis_runs WHERE status NOT IN ('succeeded','failed','cancelled')",
        financial.unresolved_application_sql(identity.namespace, '20261015_0018'),
        f"SELECT count(*) FROM {namespace}.employer_application_attempts WHERE state NOT IN ('confirmed','failed')",
        f"SELECT count(*) FROM {namespace}.service_credit_reservations WHERE state <> 'settled' OR reserved_amount <> committed_amount + released_amount OR reserved_amount < 0 OR committed_amount < 0 OR released_amount < 0",
    )
    contract.need(
        all(
            connection.execute(text(query)).scalar_one() == 0
            for query in (*queries,
                f"SELECT count(*) FROM {namespace}.resume_uploads WHERE state IN ('awaiting_upload','queued','inspecting') OR lease_token IS NOT NULL OR lease_until IS NOT NULL",
                f"SELECT count(*) FROM {namespace}.resume_upload_cleanups WHERE lease_token IS NOT NULL OR lease_until IS NOT NULL",
            )
        ),
        "schema21_active_upload_or_cleanup_refused",
    )
    def records(table, fields, quote, maximum):
        # Bound untrusted JSON inside PostgreSQL before transfer/decoding. An
        # oversized NULL projection is explicitly refused, never mistaken for
        # a legitimate absent quote on an old unquoted/unavailable row.
        fields = fields.replace(quote,
            f'CASE WHEN octet_length({quote}::text)<={maximum} THEN {quote} ELSE NULL END AS {quote},'
            f'({quote} IS NOT NULL AND octet_length({quote}::text)>{maximum}) AS quote_oversized')
        return [dict(row) for row in connection.execute(text(
            f'SELECT {fields} FROM {namespace}.{table} ORDER BY id LIMIT 257')).mappings()]
    runs = records('analysis_runs', 'id,user_id,operation,status,usage_state,estimated_units,committed_units,attempt_count,generation_attempt_count,provider,model,model_cost_group_id,model_cost_quote,model_cost_ceiling_micros,model_cost_reserved_micros,model_cost_settled_micros,model_cost_state,created_at,completed_at', 'model_cost_quote', 1048576)
    events = records('model_call_events', 'id,user_id,analysis_run_id,liability_id,provider,model,status,cost_state,attempt_number,pricing_quote,input_tokens,output_tokens,tokens_estimated,estimated_cost_micros,reserved_cost_micros,settled_cost_micros,usage_provenance,token_estimate_provenance,output_token_limit,created_at,settled_at', 'pricing_quote', 16384)
    liabilities = records('model_cost_liabilities', 'id,financial_group_id,attempt_number,provider,model,endpoint_key,currency,pricing_quote,input_token_estimate,reserved_cost_micros,settled_cost_micros,input_tokens,output_tokens,usage_provenance,cost_state,created_at,settled_at', 'pricing_quote', 8192)
    historical_holds(runs, events, liabilities)


def retained_digest(connection, identity: MigrationIdentity) -> str:
    """Retain every row/column, including schema19 identities and detached cleanup."""
    tables = list(
        connection.execute(
            text(
                "SELECT c.relname FROM pg_catalog.pg_class c WHERE c.relnamespace=:namespace "
                "AND c.relkind='r' ORDER BY c.relname LIMIT 257"
            ),
            {"namespace": identity.namespace_oid},
        ).scalars()
    )
    contract.need(1 <= len(tables) <= 256, "schema21_retained_table_bound")
    observed = marker(connection, identity)
    if observed == contract.SCHEMA:
        contract.need(
            connection.execute(text(f'SELECT count(*) FROM "{identity.namespace}".password_reset_tokens')).scalar_one() == 0
            and connection.execute(text(f'SELECT count(*) FROM "{identity.namespace}".users WHERE credential_epoch<>0 OR credential_epoch IS NULL')).scalar_one() == 0,
            "schema21_new_reset_state_changed",
        )
    else:
        contract.need("password_reset_tokens" not in tables, "schema21_before_reset_table_present")
    digest, count, deadline = hashlib.sha256(), 0, time.monotonic() + 30
    for table in tables:
        if table == "alembic_version" or table == "password_reset_tokens" and observed == contract.SCHEMA:
            continue
        contract.need(
            type(table) is str and re.fullmatch(r"[a-z][a-z0-9_]{0,62}", table),
            "schema21_retained_table_name_refused",
        )
        digest.update(table.encode() + b"\0")
        projection = "to_jsonb(t)-'credential_epoch'" if table == "users" and observed == contract.SCHEMA else "to_jsonb(t)"
        query = f"SELECT encode(pg_catalog.sha256(pg_catalog.convert_to(({projection})::text,'UTF8')),'hex') AS digest FROM \"{identity.namespace}\".\"{table}\" AS t ORDER BY digest LIMIT 250001"
        for row in connection.execute(text(query)):
            count += 1
            contract.need(
                count <= 250000 and time.monotonic() < deadline, "schema21_retained_rows_bound"
            )
            digest.update(bytes.fromhex(row[0]))
        digest.update(b"\0")
    digest.update(str(count).encode())
    return digest.hexdigest()


def upgrade_transaction(connection, identity: MigrationIdentity, *, root: Path) -> dict:
    """Existing additive migrations20+21 only, inside the original authenticated outer transaction."""
    contract.need(connection.in_transaction(), "schema21_external_transaction_required")
    connection.exec_driver_sql("SET TRANSACTION ISOLATION LEVEL SERIALIZABLE READ WRITE")
    connection.exec_driver_sql("SET LOCAL statement_timeout='300000ms'")
    connection.exec_driver_sql("SET LOCAL lock_timeout='5000ms'")
    connection.exec_driver_sql("SET LOCAL idle_in_transaction_session_timeout='10000ms'")
    verify_identity(connection, identity)
    contract.need(
        connection.execute(
            text("SELECT pg_try_advisory_xact_lock(:lock)"), {"lock": LOCK_ID}
        ).scalar_one()
        is True,
        "schema21_migration_already_running",
    )
    contract.need(marker(connection, identity) == BEFORE, "schema21_exact_19_required_never_replay")
    contract.verify_before_shape(connection, identity.namespace_oid, identity.namespace)
    acl = schema_acl(connection, identity)
    connection.exec_driver_sql(f'SET LOCAL ROLE "{identity.migrator}"')
    effective_owner(connection, identity)
    connection.exec_driver_sql(f'SET LOCAL search_path TO "{identity.namespace}"')
    drained(connection, identity)
    retained = retained_digest(connection, identity)
    contract.need(
        connection.execute(
            text(
                "SELECT count(*) FROM pg_catalog.pg_class WHERE relnamespace=:ns "
                "AND relkind='r' AND relowner<>:owner"
            ),
            {"ns": identity.namespace_oid, "owner": identity.migrator_oid},
        ).scalar_one()
        == 0,
        "schema21_existing_table_owner_changed",
    )
    connection.exec_driver_sql("RESET ROLE")
    connection.exec_driver_sql(f'GRANT CREATE ON SCHEMA "{identity.namespace}" TO "{identity.migrator}"')
    connection.exec_driver_sql(f'SET LOCAL ROLE "{identity.migrator}"')
    effective_owner(connection, identity)
    configuration = Config(str(root / "backend/alembic.ini"))
    configuration.set_main_option("script_location", str(root / "backend/alembic"))
    configuration.attributes["connection"] = connection
    command.upgrade(configuration, contract.SCHEMA)
    contract.need(
        connection.in_transaction() and marker(connection, identity) == contract.SCHEMA,
        "schema21_target_or_external_transaction_lost",
    )
    connection.exec_driver_sql(f'GRANT SELECT,INSERT,UPDATE,DELETE ON TABLE "{identity.namespace}".password_reset_tokens TO "{identity.runtime}"')
    connection.exec_driver_sql(f'GRANT SELECT ON TABLE "{identity.namespace}".password_reset_tokens TO "{identity.observer}"')
    contract.verify_shape(connection, identity.namespace_oid, identity.namespace)
    contract.verify_reset_permissions(connection, identity.namespace, identity.runtime, identity.migrator, identity.observer)
    contract.need(
        retained_digest(connection, identity) == retained, "schema21_retained_history_changed"
    )
    drained(connection, identity)
    connection.exec_driver_sql("RESET ROLE")
    connection.exec_driver_sql(f'REVOKE CREATE ON SCHEMA "{identity.namespace}" FROM "{identity.migrator}"')
    verify_identity(connection, identity)
    contract.need(schema_acl(connection, identity) == acl, "schema21_schema_acl_changed")
    return {
        "contract": contract.CONTRACT,
        "schema": contract.SCHEMA,
        "retained_sha256": retained,
        "schema_acl": acl,
        "migration_login_reenabled": False,
        "replay_authorized": False,
    }
