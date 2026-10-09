"""Real committed credentials, native original ACK, V3 activation and scoped logout."""

from __future__ import annotations

import json
import os
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import make_url
from test_employer_admissions_postgres import _migrate
from test_gcp_pairing_emulator import case as case
from test_gcp_password_lifetime_emulator import lifetime as lifetime
from test_gcp_scoped_authority_emulator import scoped as scoped

from app.domains.candidate_accounts.retention import NativeCandidateLifetimeRetention
from app.domains.candidate_accounts.service import CandidateAccountService, credential_digest
from app.domains.recovery.gcp_candidate_lifetimes import GcpProtectedCandidateLifetimes
from app.domains.recovery.gcp_contracts import AmbiguousCommit
from app.domains.recovery.store import GuardDenied, GuardUnavailable
from app.models import Base, CandidatePasswordAccount, User
from app.security import hash_password


@pytest.fixture(params=("sqlite", "postgresql"))
def service(scoped, tmp_path, monkeypatch, request):
    c = scoped
    admin = None
    if request.param == "sqlite":
        engine = create_engine(f"sqlite:///{tmp_path / 'only-synthetic-owned-credentials.sqlite'}", hide_parameters=True)
        Base.metadata.create_all(engine)
    else:
        configured = os.getenv("HIREWIZ_TEST_POSTGRES_URL")
        if not configured:
            pytest.fail("Actual scoped lifecycle PostgreSQL cases require the owned local test URL; no skip")
        url = make_url(configured)
        if url.host != "127.0.0.1" or url.port != 55433 or url.database != "hirewiz_admission_test":
            pytest.fail("Only the owned local synthetic PostgreSQL database is permitted")
        schema = "candidate_scoped_" + uuid4().hex
        admin = create_engine(url, hide_parameters=True)
        with admin.begin() as connection:
            connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        engine = create_engine(url, hide_parameters=True, connect_args={
            "options": f"-csearch_path={schema} -clock_timeout=4000 -cstatement_timeout=10000"})
        _migrate(engine, "head")
    active = set()
    event.listen(engine, "begin", lambda connection: active.add(id(connection)))
    event.listen(engine, "commit", lambda connection: active.discard(id(connection)))
    event.listen(engine, "rollback", lambda connection: active.discard(id(connection)))
    original = c.lifetime.execute
    def execute(plan):
        assert not active, "Native SDK entered a SQL credential transaction"
        return original(plan)
    monkeypatch.setattr(c.lifetime, "execute", execute)
    adapter = GcpProtectedCandidateLifetimes(c.scoped)
    c.account_service = CandidateAccountService(engine, NativeCandidateLifetimeRetention(c.lifetime, adapter), now_ms=lambda: c.now)
    c.account_engine = engine
    yield c
    engine.dispose()
    if admin is not None:
        # Preserve this uniquely owned schema as evidence. Never reset/drop a
        # shared database/schema or operate on production.
        admin.dispose()


def register(c, email="candidate-a@example.invalid"):
    return c.account_service.register(email=email, password="synthetic-password-only-20261009", policy_version="synthetic")


def login(c, email="candidate-a@example.invalid"):
    return c.account_service.login(email=email, password="synthetic-password-only-20261009")


def test_committed_credential_to_native_owned_ack_to_protected_activation_login_logout_and_other_candidate(service):
    c = service
    aid = register(c)
    a1, a2 = login(c), login(c)
    bid = register(c, "candidate-b@example.invalid")
    b1 = login(c, "candidate-b@example.invalid")
    assert aid != bid and a1.context.session_id != a2.context.session_id
    c.account_service.logout(a1.context)
    with pytest.raises(GuardDenied):
        c.account_service.validate(a1.context, auth_generation=1)
    c.account_service.validate(a2.context, auth_generation=1)
    c.account_service.validate(b1.context, auth_generation=1)
    a3 = login(c)
    assert a3.context.session_id not in {a1.context.session_id, a2.context.session_id}
    assert not c.active.any()


def test_unknown_final_protected_activation_never_sets_active_sql_or_returns_session(service, monkeypatch):
    c = service
    original = c.v3.registry.rpc.commit
    def lose(tx, writes):
        if any(json.loads(w.raw)["namespace"] == "v3_activations" for w in writes):
            c.v3.registry.rpc.rollback(tx)
            raise AmbiguousCommit("Synthetic final protected activation unpersisted")
        return original(tx, writes)
    monkeypatch.setattr(c.v3.registry.rpc, "commit", lose)
    with pytest.raises(GuardUnavailable):
        register(c)
    monkeypatch.setattr(c.v3.registry.rpc, "commit", original)
    with c.account_service.sessions() as db:
        assert db.query(CandidatePasswordAccount).one().state == "PENDING"
    with pytest.raises(GuardDenied):
        login(c)


def test_same_uid_protected_generation_advancement_denies_fresh_login_from_restored_old_sql_hash(service):
    c = service
    register(c)
    old = login(c)
    assert c.scoped.deny(old.context, scope="generation", next_credential_sha256="e" * 64) is not None
    with c.account_service.sessions() as db:
        row, user = db.query(CandidatePasswordAccount).one(), db.query(User).one()
        original = row.auth_generation, row.credential_sha256, user.password_hash
        user.password_hash = hash_password("synthetic-new-password-only-20261009")
        row.auth_generation, row.credential_sha256 = 2, credential_digest(user.password_hash)
        db.commit()
    # Actually restore the old SQL generation AND exact old salted hash.
    with c.account_service.sessions() as db:
        if c.account_engine.dialect.name == "postgresql":
            # Privileged restore simulation applies only in this owned schema;
            # ordinary SQL updates correctly reject a generation rollback.
            db.execute(text("SET LOCAL session_replication_role = replica"))
        row, user = db.query(CandidatePasswordAccount).one(), db.query(User).one()
        row.auth_generation, row.credential_sha256, user.password_hash = original
        db.commit()
    with pytest.raises(GuardDenied):
        login(c)
    with c.account_service.sessions() as db:
        assert db.query(CandidatePasswordAccount).one().auth_generation == 1
        assert db.query(User).one().password_hash


def test_normal_reset_is_explicitly_unavailable_without_actual_native_projection(service):
    c = service
    register(c)
    old = login(c)
    with pytest.raises(GuardUnavailable, match="projection"):
        c.account_service.change_password(old.context, current_password="synthetic-password-only-20261009",
            new_password="synthetic-new-password-only-20261009")
    c.account_service.validate(old.context, auth_generation=1)
