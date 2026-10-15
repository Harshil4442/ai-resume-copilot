"""Actual owned PG17, restricted creator; preparation never changes a login password."""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

import pytest
from backend.tests.test_employer_admissions_postgres import _migrate
from backend.tests.test_employer_admissions_postgres import pg_engine as owned_pg_engine
from psycopg import ConnectionInfo
from sqlalchemy import create_engine, event, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.pool import NullPool

from scripts import nologin_role_preparation as preparation


@dataclass
class Fixture:
    admin: Any
    actor: Any
    namespace: str
    actor_name: str
    names: dict[str,str]
    extra: str
    manifest: preparation.PreparationManifest


@pytest.fixture
def pg_engine():
    yield from owned_pg_engine.__wrapped__()


@pytest.fixture
def prepared_context(pg_engine):
    _migrate(pg_engine,"20261008_0009")
    token=uuid4().hex
    actor_name="prep_actor_"+token
    names={alias:"prep_"+alias+"_"+token for alias in ("runtime","migration","observer")}
    extra="prep_extra_"+token
    with pg_engine.begin() as connection:
        namespace=connection.execute(text("SELECT current_schema()")).scalar_one()
        admin_name=connection.execute(text("SELECT current_user")).scalar_one()
        connection.exec_driver_sql(f"CREATE ROLE \"{actor_name}\" LOGIN PASSWORD 'synthetic-local-prep-only' NOINHERIT NOSUPERUSER CREATEDB CREATEROLE REPLICATION BYPASSRLS")
        connection.exec_driver_sql(f'GRANT CONNECT ON DATABASE "hirewiz_admission_test" TO "{actor_name}" WITH GRANT OPTION')
        connection.exec_driver_sql(f'ALTER SCHEMA "{namespace}" OWNER TO "{actor_name}"')
        rows=connection.execute(text("SELECT c.relname,c.relkind FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname=:ns AND c.relkind IN ('r','S') ORDER BY CASE WHEN c.relkind='r' THEN 0 ELSE 1 END,c.oid"),{"ns":namespace}).all()
        for name,kind in rows:
            if kind=='S' and connection.execute(text("SELECT c.relowner=r.oid FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace CROSS JOIN pg_catalog.pg_roles r WHERE n.nspname=:ns AND c.relname=:name AND r.rolname=:role"),{'ns':namespace,'name':name,'role':actor_name}).scalar_one():
                continue
            connection.exec_driver_sql(f'ALTER {"SEQUENCE" if kind=="S" else "TABLE"} "{namespace}"."{name}" OWNER TO "{actor_name}"')
    actor=create_engine(pg_engine.url.set(username=actor_name,password="synthetic-local-prep-only"),poolclass=NullPool,hide_parameters=True,connect_args={"options":f"-csearch_path={namespace}"})
    try:
        with actor.begin() as connection:
            manifest=preparation.review_manifest(connection,namespace,**names)
        fixture=Fixture(pg_engine,actor,namespace,actor_name,names,extra,manifest)
        yield fixture
    finally:
        with actor.begin() as connection:
            present=set(connection.execute(text("SELECT rolname FROM pg_catalog.pg_roles WHERE rolname=ANY(:names)"),{"names":[*names.values(),extra]}).scalars())
            for name in present:
                connection.exec_driver_sql(f'REVOKE ALL PRIVILEGES ON DATABASE "hirewiz_admission_test" FROM "{name}"')
        actor.dispose()
        with pg_engine.begin() as connection:
            # Exact random-owned roles/objects only; no DROP OWNED or global reassignment.
            present=set(connection.execute(text("SELECT rolname FROM pg_catalog.pg_roles WHERE rolname=ANY(:names)"),{"names":[*names.values(),extra]}).scalars())
            for name in present:
                connection.exec_driver_sql(f'REVOKE ALL PRIVILEGES ON DATABASE "hirewiz_admission_test" FROM "{name}"')
                connection.exec_driver_sql(f'REVOKE ALL PRIVILEGES ON SCHEMA "{namespace}" FROM "{name}"')
                connection.exec_driver_sql(f'REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA "{namespace}" FROM "{name}"')
                connection.exec_driver_sql(f'REVOKE ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA "{namespace}" FROM "{name}"')
                connection.exec_driver_sql(f'DROP ROLE "{name}"')
            connection.exec_driver_sql(f'ALTER SCHEMA "{namespace}" OWNER TO "{admin_name}"')
            for name,kind in rows:
                if kind=='S' and connection.execute(text("SELECT c.relowner=r.oid FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace CROSS JOIN pg_catalog.pg_roles r WHERE n.nspname=:ns AND c.relname=:name AND r.rolname=:role"),{'ns':namespace,'name':name,'role':admin_name}).scalar_one():
                    continue
                connection.exec_driver_sql(f'ALTER {"SEQUENCE" if kind=="S" else "TABLE"} "{namespace}"."{name}" OWNER TO "{admin_name}"')
            connection.exec_driver_sql(f'REVOKE ALL PRIVILEGES ON DATABASE "hirewiz_admission_test" FROM "{actor_name}"')
            connection.exec_driver_sql(f'DROP ROLE "{actor_name}"')


def role_count(context):
    with context.admin.connect() as connection:
        return connection.execute(text("SELECT count(*) FROM pg_catalog.pg_roles WHERE rolname=ANY(:names)"),{"names":list(context.names.values())}).scalar_one()


def capture(context):
    with context.actor.begin() as connection:
        snapshot=preparation._snapshot(connection,context.namespace)
        snapshot['identity'].pop('pid')
        return snapshot


def test_actual_three_nologin_grants_preserve_old_owner_and_are_not_release_authority(prepared_context):
    context=prepared_context
    before=capture(context)
    result=preparation.prepare(context.actor,context.manifest)
    assert result["state"]=="prepared_nologin_only"
    assert result["new_role_count"]==3 and result["login_enabled_count"]==0
    assert result["creator_admin_edges"]==3 and result["creator_set_edges"]==result["creator_inherit_edges"]==0
    assert result["legacy_permissions_preserved"] is True
    assert result["ownership_transferred"] is False and result["default_privileges_changed"] is False
    assert result["credentials_created"] is False and result["replacement_isolation_proven"] is False
    assert result["release_authorized"] is False and result["cutover_ready"] is False
    assert all(name not in json.dumps(result) for name in (*context.names.values(),context.actor_name))
    after=capture(context)
    assert after["protected"]["owned"]==before["protected"]["owned"]
    assert after["protected"]["types"]==before["protected"]["types"]
    assert after["protected"]["defaults"]==before["protected"]["defaults"]
    with context.admin.connect() as connection:
        for relation in context.manifest.relations:
            if relation.kind=='r':
                actual=connection.execute(text("SELECT pg_catalog.has_table_privilege(:runtime,:oid,'SELECT'),pg_catalog.has_table_privilege(:runtime,:oid,'INSERT'),pg_catalog.has_table_privilege(:runtime,:oid,'UPDATE'),pg_catalog.has_table_privilege(:runtime,:oid,'DELETE'),pg_catalog.has_table_privilege(:observer,:oid,'SELECT'),pg_catalog.has_table_privilege(:observer,:oid,'INSERT'),pg_catalog.has_table_privilege(:observer,:oid,'UPDATE'),pg_catalog.has_table_privilege(:observer,:oid,'DELETE')"),{"runtime":context.names['runtime'],"observer":context.names['observer'],"oid":relation.oid}).one()
                assert tuple(actual)==(True,relation.name!='alembic_version',relation.name!='alembic_version',relation.name in preparation._erasable(),True,False,False,False)
            else:
                assert tuple(connection.execute(text("SELECT pg_catalog.has_sequence_privilege(:runtime,:oid,'USAGE'),pg_catalog.has_sequence_privilege(:runtime,:oid,'SELECT'),pg_catalog.has_sequence_privilege(:observer,:oid,'SELECT'),pg_catalog.has_sequence_privilege(:observer,:oid,'USAGE')"),{"runtime":context.names['runtime'],"observer":context.names['observer'],"oid":relation.oid}).one())==(True,False,True,False)
        for alias,name in context.names.items():
            assert tuple(connection.execute(text("SELECT pg_catalog.has_schema_privilege(:role,:ns,'USAGE'),pg_catalog.has_schema_privilege(:role,:ns,'CREATE')"),{"role":name,"ns":context.namespace}).one())==(True,alias=='migration')
            assert connection.execute(text("SELECT pg_catalog.pg_has_role(:actor,:newrole,'MEMBER WITH ADMIN OPTION')"),{"actor":context.actor_name,"newrole":name}).scalar_one() is True
            assert connection.execute(text("SELECT pg_catalog.pg_has_role(:actor,:newrole,'SET')"),{"actor":context.actor_name,"newrole":name}).scalar_one() is False
            assert connection.execute(text("SELECT pg_catalog.pg_has_role(:actor,:newrole,'USAGE')"),{"actor":context.actor_name,"newrole":name}).scalar_one() is False


@pytest.mark.parametrize('alias',['runtime','migration','observer'])
def test_created_role_has_no_login_or_password_and_actual_login_refuses(prepared_context,alias):
    context=prepared_context
    preparation.prepare(context.actor,context.manifest)
    with context.admin.connect() as connection:
        # Existence only: neither load nor persist any password value/hash.
        assert tuple(connection.execute(text("SELECT rolcanlogin,rolpassword IS NULL FROM pg_catalog.pg_authid WHERE rolname=:role"),{"role":context.names[alias]}).one())==(False,True)
    engine=create_engine(context.admin.url.set(username=context.names[alias],password='synthetic-unused'),poolclass=NullPool,hide_parameters=True)
    try:
        with pytest.raises(OperationalError):
            with engine.connect() as connection:
                connection.execute(text('SELECT 1'))
    finally:
        engine.dispose()


@pytest.mark.parametrize('field,value',[('database_oid',1),('database_owner',1),('namespace_oid',1),('namespace_owner',1),('system_identifier_sha256','0'*64),('actor_oid',1),('actor_name','different_actor'),('protected_state_sha256','0'*64),('dependency_state_sha256','0'*64),('acl_state_sha256','0'*64)])
def test_wrong_native_identity_or_review_pin_refuses_without_roles(prepared_context,field,value):
    context=prepared_context
    data=context.manifest.model_dump(mode='json')
    data[field]=value
    if field=='actor_oid':
        for relation in data['relations']:
            relation['owner']=value
    if field=='namespace_oid':
        for relation in data['relations']:
            relation['namespace_oid']=value
    candidate=preparation.PreparationManifest.model_validate(data)
    with pytest.raises(preparation.PreparationDenied):
        preparation.prepare(context.actor,candidate)
    assert role_count(context)==0


@pytest.mark.parametrize('change',['collision','rename','acl','column_acl','default_acl','dependency','schema_marker'])
def test_actual_intervening_catalog_drift_refuses_and_preserves_preexisting_state(prepared_context,change):
    context=prepared_context
    with context.admin.begin() as connection:
        if change=='collision':
            connection.exec_driver_sql(f'CREATE ROLE "{context.names["runtime"]}" NOLOGIN')
        elif change=='rename':
            connection.exec_driver_sql(f'ALTER TABLE "{context.namespace}".payment_orders RENAME TO payment_orders_changed')
        elif change=='acl':
            connection.exec_driver_sql(f'GRANT SELECT ON "{context.namespace}".payment_orders TO PUBLIC')
        elif change=='column_acl':
            connection.exec_driver_sql(f'GRANT SELECT(id) ON "{context.namespace}".payment_orders TO PUBLIC')
        elif change=='default_acl':
            connection.exec_driver_sql(f'ALTER DEFAULT PRIVILEGES FOR ROLE "{context.actor_name}" IN SCHEMA "{context.namespace}" GRANT SELECT ON TABLES TO PUBLIC')
        elif change=='dependency':
            connection.exec_driver_sql(f'ALTER SEQUENCE "{context.namespace}".payment_orders_id_seq OWNED BY NONE')
        else:
            connection.exec_driver_sql(f"UPDATE \"{context.namespace}\".alembic_version SET version_num='20261009_0010'")
    try:
        with pytest.raises(preparation.PreparationDenied):
            preparation.prepare(context.actor,context.manifest)
        assert role_count(context)==(1 if change=='collision' else 0)
    finally:
        with context.admin.begin() as connection:
            if change=='rename':
                connection.exec_driver_sql(f'ALTER TABLE "{context.namespace}".payment_orders_changed RENAME TO payment_orders')
            elif change=='default_acl':
                connection.exec_driver_sql(f'ALTER DEFAULT PRIVILEGES FOR ROLE "{context.actor_name}" IN SCHEMA "{context.namespace}" REVOKE SELECT ON TABLES FROM PUBLIC')


@pytest.mark.parametrize('channel',['role','session_authorization','startup_role'])
def test_privileged_protocol_login_cannot_masquerade_as_reviewed_actor(prepared_context,channel):
    context=prepared_context
    candidate=create_engine(context.admin.url,poolclass=NullPool,hide_parameters=True,connect_args={'options':f'-c role={context.actor_name}'} if channel=='startup_role' else {})
    if channel!='startup_role':
        def configure(dbapi_connection,_record):
            with dbapi_connection.cursor() as cursor:
                cursor.execute(f'{"SET ROLE" if channel=="role" else "SET SESSION AUTHORIZATION"} "{context.actor_name}"')
        event.listen(candidate,'connect',configure)
    try:
        with pytest.raises(preparation.PreparationDenied,match='authenticated_actor_mismatch'):
            preparation.prepare(candidate,context.manifest)
        assert role_count(context)==0
    finally:
        candidate.dispose()


@pytest.mark.parametrize('attribute,replacement',[('user','other_actor'),('backend_pid',1),('server_version',170000)])
def test_driver_native_identity_disagreement_refuses(prepared_context,monkeypatch,attribute,replacement):
    context=prepared_context
    monkeypatch.setattr(ConnectionInfo,attribute,property(lambda self:replacement))
    with pytest.raises(preparation.PreparationDenied,match='authenticated_actor_mismatch'):
        preparation.prepare(context.actor,context.manifest)
    monkeypatch.undo()
    assert role_count(context)==0


@pytest.mark.parametrize('failure',['mid_grant','post_extra_role','post_extra_acl','post_role_login','post_creator_set'])
def test_partial_or_invalid_poststate_rolls_back_entire_new_state(prepared_context,monkeypatch,failure):
    context=prepared_context
    before=capture(context)
    original=preparation._snapshot
    calls=0
    def modified(connection,namespace):
        nonlocal calls
        calls+=1
        if calls==3:
            if failure=='post_extra_role':
                connection.exec_driver_sql(f'CREATE ROLE "{context.extra}" NOLOGIN')
            elif failure=='post_extra_acl':
                connection.exec_driver_sql(f'GRANT DELETE ON "{context.namespace}".payment_orders TO "{context.names["runtime"]}"')
            elif failure=='post_role_login':
                connection.exec_driver_sql(f'ALTER ROLE "{context.names["runtime"]}" LOGIN')
            elif failure=='post_creator_set':
                connection.exec_driver_sql(f'GRANT "{context.names["migration"]}" TO "{context.actor_name}" WITH SET TRUE')
        return original(connection,namespace)
    monkeypatch.setattr(preparation,'_snapshot',modified)
    if failure=='mid_grant':
        def fail(connection,cursor,statement,parameters,context_,executemany):
            if statement.startswith('GRANT SELECT ON TABLE'):
                raise RuntimeError('synthetic-secret-must-not-surface')
        event.listen(context.actor,'before_cursor_execute',fail)
    try:
        with pytest.raises(preparation.PreparationDenied) as error:
            preparation.prepare(context.actor,context.manifest)
        assert 'synthetic-secret' not in str(error.value)
    finally:
        if failure=='mid_grant':
            event.remove(context.actor,'before_cursor_execute',fail)
        monkeypatch.undo()
    assert role_count(context)==0
    assert capture(context)==before


def test_commit_uncertainty_never_returns_success_or_retries_or_auto_deletes(prepared_context,monkeypatch):
    context=prepared_context
    from sqlalchemy.engine import Transaction
    calls=0
    def unknown(self):
        nonlocal calls
        calls+=1
        raise RuntimeError('synthetic-private-commit-error')
    with monkeypatch.context() as scoped:
        scoped.setattr(Transaction,'commit',unknown)
        with pytest.raises(preparation.PreparationDenied,match='transaction_outcome_unknown') as error:
            preparation.prepare(context.actor,context.manifest)
        assert calls==1 and 'synthetic-private' not in str(error.value)
    assert role_count(context)==0


def test_replay_after_actual_commit_refuses_without_regranting_or_mutating(prepared_context):
    context=prepared_context
    preparation.prepare(context.actor,context.manifest)
    before=capture(context)
    with pytest.raises(preparation.PreparationDenied):
        preparation.prepare(context.actor,context.manifest)
    assert role_count(context)==3 and capture(context)==before
