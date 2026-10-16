"""Real PG17 schema19 transaction through non-superuser bootstrap/NOLOGIN owner.

Only the dedicated loopback database is admitted. UUID-owned schemas and roles
are removed; the shared database/container remain untouched. No provider calls.
"""
from __future__ import annotations

import copy
import hashlib
import importlib
import os
import sys
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from alembic.config import Config
from backend.app import models as core
from backend.app.domains.employer import models as employer
from backend.tests.browser_submit_owned import OwnedSubmit
from sqlalchemy import MetaData, create_engine, event, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError
from sqlalchemy.pool import NullPool

from alembic import command
from scripts import browser_schema19_step as step

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'infra/gcp'))
reviewed = importlib.import_module('browser_schema19_release')
PASSWORD = 'owned-local-schema19-test-only'


def configured_url():
    configured = os.getenv('HIREWIZ_TEST_POSTGRES_URL')
    if not configured:
        pytest.skip('Set the dedicated local disposable PostgreSQL test URL')
    url = make_url(configured)
    if (url.drivername != 'postgresql+psycopg' or url.host != '127.0.0.1'
            or url.port != 55433 or url.database != 'hirewiz_admission_test'):
        pytest.fail('Schema19 step tests accept only the dedicated local disposable database')
    return url


def scoped_engine(url, namespace):
    return create_engine(url, poolclass=NullPool, hide_parameters=True, connect_args={
        'options': f'-csearch_path={namespace} -clock_timeout=4000 -cstatement_timeout=15000',
        'connect_timeout': 5,
    })


def seed_original_rows(engine, monkeypatch):
    owned = OwnedSubmit(monkeypatch)
    try:
        owned.connect()
        selected = owned.seed_selection()
        row = owned.db.get(owned.app.__class__, owned.app.id)
        assert row.status == 'ready' and row.application_mode == 'manual'
        from backend.app.domains.browser_assistance.models import BrowserAssistanceReview
        review = owned.db.get(BrowserAssistanceReview, selected['review_id'])
        review.payload = {'review_protocol_version': 3, 'synthetic_legacy': 'preserve'}
        now = datetime.now(UTC)
        owned.db.add(core.PaymentOrder(
            public_id='schema19_owned_order', user_id=1, sku='owned-credits', catalog_version='owned-v1',
            entitlement_kind='job_service_credits', entitlement_quantity=100,
            billing_country_confirmed_at=now, gross_amount_minor=39900, status='paid', paid_at=now,
            provider_order_id='synthetic-provider-order', provider_fee_amount_minor=798,
            provider_fee_tax_minor=144, estimated_net_amount_minor=38958,
        ))
        owned.db.add(employer.ServiceCreditEvent(
            id='schema19_owned_credit', user_id=1, event_type='grant', amount=100, balance_after=100,
            idempotency_key='schema19-owned-grant', source_type='payment', source_id='owned-order', reason='owned fixture',
        ))
        owned.db.add(employer.ServiceCreditReservation(
            id='schema19_owned_reservation', user_id=1, operation='job_search', source_id='owned-completed-search',
            unit_price=1, requested_count=3, reserved_amount=3, committed_amount=2, released_amount=1,
            state='settled', pricing_version='owned-v1', settled_at=now,
        ))
        owned.db.commit()
        metadata = MetaData()
        metadata.reflect(bind=engine)
        with owned.factory.kw['bind'].connect() as source, engine.begin() as destination:
            for original in core.Base.metadata.sorted_tables:
                rows = [dict(row) for row in source.execute(original.select()).mappings()]
                if not rows:
                    continue
                assert original.name in metadata.tables, 'The schema18 seed cannot contain new schema19 identities'
                table = metadata.tables[original.name]
                destination.execute(table.insert(), [{key: copy.deepcopy(value) for key, value in row.items() if key in table.c}
                                                     for row in rows])
    finally:
        owned.close()


@pytest.fixture
def owned_step(monkeypatch):
    url = configured_url()
    token = uuid4().hex[:20]
    names = {key: 'b19_' + token + '_' + key for key in ('bootstrap', 'migrator', 'runtime', 'observer')}
    namespace = 'browser_step19_' + token
    admin = create_engine(url, poolclass=NullPool, hide_parameters=True)
    source, bootstrap = None, None
    created = []
    try:
        with admin.begin() as db:
            assert 170000 <= int(db.scalar(text('SHOW server_version_num'))) < 180000
            for key, name in names.items():
                login = 'NOLOGIN' if key == 'migrator' else 'LOGIN'
                db.exec_driver_sql(f'CREATE ROLE "{name}" {login} NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS PASSWORD \'{PASSWORD}\'')
                created.append(name)
            db.exec_driver_sql(f'GRANT "{names["migrator"]}" TO "{names["bootstrap"]}" WITH ADMIN FALSE, INHERIT FALSE, SET TRUE')
            db.exec_driver_sql(f'CREATE SCHEMA "{namespace}" AUTHORIZATION "{names["bootstrap"]}"')
            for role in names.values():
                db.exec_driver_sql(f'GRANT USAGE ON SCHEMA "{namespace}" TO "{role}"')
            db.exec_driver_sql(f'GRANT CREATE ON SCHEMA "{namespace}" TO "{names["migrator"]}"')
            db.exec_driver_sql(f'GRANT EXECUTE ON FUNCTION pg_catalog.pg_control_system() TO "{names["bootstrap"]}"')
        source = scoped_engine(url, namespace)
        cfg = Config(str(ROOT / 'backend/alembic.ini'))
        cfg.set_main_option('script_location', str(ROOT / 'backend/alembic'))
        with source.begin() as db:
            db.exec_driver_sql(f'SET LOCAL ROLE "{names["migrator"]}"')
            cfg.attributes['connection'] = db
            command.upgrade(cfg, step.BEFORE)
        seed_original_rows(source, monkeypatch)
        with source.begin() as db:
            db.exec_driver_sql(f'GRANT SELECT ON ALL TABLES IN SCHEMA "{namespace}" TO "{names["runtime"]}", "{names["observer"]}"')
            db.exec_driver_sql(f'GRANT SELECT ON "{namespace}".alembic_version TO "{names["bootstrap"]}"')
            tables = list(db.execute(text('SELECT relname FROM pg_class WHERE relnamespace=(SELECT oid FROM pg_namespace WHERE nspname=:ns) AND relkind=\'r\''), {'ns': namespace}).scalars())
            for table in tables:
                if table != 'alembic_version':
                    db.exec_driver_sql(f'GRANT INSERT,UPDATE ON "{namespace}"."{table}" TO "{names["runtime"]}"')
                if table in reviewed.release.ERASABLE_TABLES:
                    db.exec_driver_sql(f'GRANT DELETE ON "{namespace}"."{table}" TO "{names["runtime"]}"')
            db.exec_driver_sql(f'GRANT USAGE ON ALL SEQUENCES IN SCHEMA "{namespace}" TO "{names["runtime"]}"')
            db.exec_driver_sql(f'REVOKE CREATE ON SCHEMA "{namespace}" FROM "{names["migrator"]}"')
            actual = db.execute(text('SELECT d.oid,d.datdba,n.oid,n.nspowner,(SELECT system_identifier::text FROM pg_control_system()) FROM pg_database d JOIN pg_namespace n ON n.nspname=:ns WHERE d.datname=current_database()'), {'ns': namespace}).one()
            oids = dict(db.execute(text('SELECT rolname,oid FROM pg_roles WHERE rolname=ANY(CAST(:names AS text[]))'), {'names': list(names.values())}).all())
            identity = step.MigrationIdentity(hashlib.sha256(actual[4].encode()).hexdigest(), actual[0], actual[1],
                namespace, actual[2], actual[3], names['bootstrap'], oids[names['bootstrap']],
                names['migrator'], oids[names['migrator']], names['runtime'], oids[names['runtime']],
                names['observer'], oids[names['observer']])
            before_acl = step.schema_acl(db, identity)
            before_digest = step.retained_digest(db, identity)
            assert db.scalar(text('SELECT rolsuper FROM pg_roles WHERE rolname=:name'), {'name': names['bootstrap']}) is False
            assert db.scalar(text('SELECT rolcanlogin FROM pg_roles WHERE rolname=:name'), {'name': names['migrator']}) is False
        bootstrap = scoped_engine(url.set(username=names['bootstrap'], password=PASSWORD), namespace)
        runtime = scoped_engine(url.set(username=names['runtime'], password=PASSWORD), namespace)
        observer = scoped_engine(url.set(username=names['observer'], password=PASSWORD), namespace)
        yield SimpleNamespace(admin=admin, engine=source, bootstrap=bootstrap, runtime=runtime, observer=observer,
                              identity=identity, namespace=namespace, names=names, before_acl=before_acl,
                              before_digest=before_digest, base_url=url)
        runtime.dispose()
        observer.dispose()
    finally:
        if bootstrap is not None:
            bootstrap.dispose()
        if source is not None:
            source.dispose()
        with admin.begin() as db:
            db.exec_driver_sql(f'DROP SCHEMA IF EXISTS "{namespace}" CASCADE')
            for name in reversed(created):
                db.exec_driver_sql(f'DROP OWNED BY "{name}"')
                db.exec_driver_sql(f'DROP ROLE "{name}"')
        admin.dispose()


def version(owned):
    with owned.engine.connect() as db:
        return step.marker(db, owned.identity)


def execute(owned, identity=None):
    with owned.bootstrap.begin() as db:
        return step.upgrade_transaction(db, identity or owned.identity, root=ROOT)


def permissions(owned, db):
    reviewed.verify_permissions(db, owned.namespace, owned.names['runtime'], owned.names['migrator'], owned.names['observer'])


def assert_unchanged18(owned):
    with owned.engine.connect() as db:
        assert step.marker(db, owned.identity) == step.BEFORE
        assert step.schema_acl(db, owned.identity) == owned.before_acl
        assert step.retained_digest(db, owned.identity) == owned.before_digest
        assert db.scalar(text('SELECT count(*) FROM pg_class WHERE relnamespace=:ns AND relname=ANY(CAST(:names AS text[]))'),
                         {'ns': owned.identity.namespace_oid, 'names': sorted(step.NEW_TABLES)}) == 0
        assert db.scalar(text('SELECT rolcanlogin FROM pg_roles WHERE oid=:oid'), {'oid': owned.identity.migrator_oid}) is False


def test_real_non_superuser_owner_transaction_preserves_rows_money_and_append_only_grants(owned_step):
    owned = owned_step
    observations = []
    def before_ddl(connection, _cursor, statement, _parameters, _context, _many):
        if statement.lstrip().startswith('CREATE TABLE candidate_google_'):
            row = connection.execute(text('SELECT current_user,session_user,current_setting(\'transaction_isolation\'),(SELECT rolcanlogin FROM pg_roles WHERE rolname=current_user),has_schema_privilege(current_user,current_schema(),\'CREATE\')')).one()
            observations.append(tuple(row))
    event.listen(owned.bootstrap, 'before_cursor_execute', before_ddl)
    try:
        result = execute(owned)
    finally:
        event.remove(owned.bootstrap, 'before_cursor_execute', before_ddl)
    assert result['schema'] == '20261015_0019' and result['retained_sha256'] == owned.before_digest
    assert result['migration_login_reenabled'] is result['replay_authorized'] is False
    assert observations and all(row == (owned.names['migrator'], owned.names['bootstrap'], 'serializable', False, True) for row in observations)
    with owned.engine.connect() as db:
        assert step.marker(db, owned.identity) == '20261015_0019'
        assert step.schema_acl(db, owned.identity) == owned.before_acl
        assert step.retained_digest(db, owned.identity) == owned.before_digest
        assert db.scalar(text('SELECT job_service_credits FROM users WHERE id=1')) == 100
        assert db.scalar(text('SELECT gross_amount_minor FROM payment_orders WHERE public_id=\'schema19_owned_order\'')) == 39900
        assert db.scalar(text('SELECT committed_amount FROM service_credit_reservations WHERE id=\'schema19_owned_reservation\'')) == 2
        assert db.scalar(text('SELECT review_protocol_version FROM browser_assistance_reviews')) == 3
        permissions(owned, db)
    with owned.runtime.begin() as db:
        db.execute(text('INSERT INTO candidate_identity_history(registration_id) VALUES(:id)'), {'id': str(uuid4())})
        db.execute(text("INSERT INTO candidate_google_enrollment_challenges(id,user_id,nonce_sha256,principal_sha256,email_sha256,credential_sha256,expires_at_ms,used) VALUES(:id,1,:sha,:sha,:sha,:sha,1800000000000,false)"), {'id': str(uuid4()), 'sha': 'a' * 64})
        db.execute(text('UPDATE candidate_google_enrollment_challenges SET used=true'))
        assert db.scalar(text('SELECT used FROM candidate_google_enrollment_challenges')) is True
        db.execute(text('DELETE FROM candidate_google_enrollment_challenges'))
        db.execute(text("INSERT INTO candidate_google_identities(user_id,principal_sha256,enrollment_proof_sha256,state) VALUES(1,:principal,:proof,'ACTIVE')"), {'principal': 'b' * 64, 'proof': 'c' * 64})
        db.execute(text("UPDATE candidate_google_identities SET state='DETACHED'"))
        assert db.scalar(text('SELECT state FROM candidate_google_identities')) == 'DETACHED'
        db.execute(text('DELETE FROM candidate_google_identities'))
    for command_text in ('UPDATE candidate_identity_history SET registration_id=registration_id', 'DELETE FROM candidate_identity_history', 'TRUNCATE candidate_identity_history'):
        with pytest.raises(DBAPIError) as failure, owned.runtime.begin() as db:
            db.exec_driver_sql(command_text)
        assert failure.value.orig.sqlstate == '42501'
    with owned.observer.connect() as db:
        assert db.scalar(text('SELECT count(*) FROM candidate_identity_history')) == 1
    with owned.bootstrap.connect() as db:
        assert db.execute(text('SELECT current_user,session_user')).one() == (owned.names['bootstrap'], owned.names['bootstrap'])
        assert db.scalar(text('SELECT has_schema_privilege(:owner,:ns,\'CREATE\')'), {'owner': owned.names['migrator'], 'ns': owned.namespace}) is False


def test_actual_ddl_error_rolls_back_marker_new_tables_and_temporary_acl(owned_step):
    owned = owned_step
    injected = []
    def fault(_connection, _cursor, statement, parameters, _context, _many):
        if statement.lstrip().startswith('CREATE TABLE candidate_google_identities'):
            injected.append(True)
            return 'CREATE TABLE "' + owned.namespace + '".broken_ddl (id missing_owned_type)', parameters
        return statement, parameters
    event.listen(owned.bootstrap, 'before_cursor_execute', fault, retval=True)
    try:
        with pytest.raises(DBAPIError) as failure:
            execute(owned)
        assert failure.value.orig.sqlstate == '42704'
    finally:
        event.remove(owned.bootstrap, 'before_cursor_execute', fault)
    assert injected == [True]
    assert_unchanged18(owned)
    with owned.bootstrap.begin() as db:
        assert db.scalar(text('SELECT pg_try_advisory_xact_lock(:lock)'), {'lock': step.LOCK_ID}) is True


def test_outer_rollback_undoes_successful_ddl_and_grants_without_premature_commit(owned_step):
    owned = owned_step
    with owned.bootstrap.connect() as db:
        transaction = db.begin()
        result = step.upgrade_transaction(db, owned.identity, root=ROOT)
        assert result['schema'] == '20261015_0019' and db.in_transaction()
        transaction.rollback()
    assert_unchanged18(owned)


def test_actual_advisory_lock_conflict_refuses_before_ddl(owned_step):
    owned = owned_step
    with owned.engine.begin() as owner:
        owner.execute(text('SELECT pg_advisory_xact_lock(:lock)'), {'lock': step.LOCK_ID})
        with pytest.raises(step.Step19Denied, match='migration_already_running'):
            execute(owned)
    assert_unchanged18(owned)


@pytest.mark.parametrize('change', ['session', 'database_owner', 'namespace_owner', 'cluster', 'existing_table_owner'])
def test_wrong_real_session_owner_or_cluster_cannot_run_ddl(owned_step, change):
    owned, identity = owned_step, owned_step.identity
    if change == 'database_owner':
        identity = replace(identity, database_owner_oid=identity.database_owner_oid + 1)
    elif change == 'namespace_owner':
        identity = replace(identity, namespace_owner_oid=identity.namespace_owner_oid + 1)
    elif change == 'cluster':
        identity = replace(identity, cluster_sha256='0' * 64)
    if change == 'existing_table_owner':
        with owned.engine.begin() as db:
            current = db.scalar(text('SELECT current_user'))
            db.exec_driver_sql(f'ALTER TABLE "{owned.namespace}".service_credit_events OWNER TO "{current}"')
            db.exec_driver_sql(f'GRANT SELECT ON "{owned.namespace}".service_credit_events TO "{identity.migrator}"')
    with pytest.raises(step.Step19Denied, match='existing_table_owner_changed' if change == 'existing_table_owner' else 'native_identity_changed'):
        if change == 'session':
            with owned.engine.begin() as db:
                step.upgrade_transaction(db, identity, root=ROOT)
        else:
            execute(owned, identity)
    assert_unchanged18(owned)


def test_schema19_is_not_replayed_even_with_genuine_bootstrap_session(owned_step):
    owned = owned_step
    execute(owned)
    statements = []
    def capture(_connection, _cursor, statement, _parameters, _context, _many):
        statements.append(statement)
    event.listen(owned.bootstrap, 'before_cursor_execute', capture)
    try:
        with pytest.raises(step.Step19Denied, match='exact_18_required_never_replay'):
            execute(owned)
    finally:
        event.remove(owned.bootstrap, 'before_cursor_execute', capture)
    assert not any(statement.lstrip().startswith(('CREATE TABLE', 'ALTER TABLE', 'GRANT CREATE')) for statement in statements)
    assert version(owned) == '20261015_0019'


@pytest.mark.parametrize('fault', ['history_update', 'history_delete', 'history_truncate', 'observer_read', 'google_owner'])
def test_actual_post_upgrade_permissions_refuse_broadened_history_or_wrong_owner(owned_step, fault):
    owned = owned_step
    execute(owned)
    with owned.engine.connect() as db, db.begin() as transaction:
        permissions(owned, db)
        if fault.startswith('history_'):
            privilege = fault.split('_', 1)[1].upper()
            db.exec_driver_sql(f'GRANT {privilege} ON candidate_identity_history TO "{owned.names["runtime"]}"')
        elif fault == 'observer_read':
            db.exec_driver_sql(f'REVOKE SELECT ON candidate_google_identities FROM "{owned.names["observer"]}"')
        else:
            surrogate = 'b19_surrogate_' + uuid4().hex[:20]
            db.exec_driver_sql(f'CREATE ROLE "{surrogate}" NOLOGIN NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS')
            db.exec_driver_sql(f'GRANT "{surrogate}" TO "{owned.names["migrator"]}" WITH ADMIN FALSE, INHERIT TRUE, SET FALSE')
            db.exec_driver_sql(f'ALTER TABLE candidate_google_identities OWNER TO "{surrogate}"')
            # Generic inherited ownership is deliberately true: the separate
            # exact new-table owner check must still refuse this different OID.
            assert db.scalar(text("SELECT pg_has_role(:migration,:owner,'USAGE')"), {'migration': owned.names['migrator'], 'owner': surrogate}) is True
        reason = 'identity_history_must_be_append_only' if fault.startswith('history_') else 'google_exact_owner_or_observer_missing'
        with pytest.raises(reviewed.rolling.RollingDenied, match=reason):
            permissions(owned, db)
        transaction.rollback()
    with owned.engine.connect() as db:
        permissions(owned, db)
