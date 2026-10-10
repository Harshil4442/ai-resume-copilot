from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import timedelta
from uuid import uuid4

import pytest
from backend.app import models as core
from backend.app.database import Base
from backend.app.domains.employer import models, service
from backend.app.security import create_access_token, get_current_user
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session
from sqlalchemy.orm.exc import NoResultFound
from sqlalchemy.schema import CreateSchema, DropSchema


@pytest.fixture
def catalog_db(monkeypatch: pytest.MonkeyPatch) -> Iterator[tuple[Engine, Session]]:
    monkeypatch.setenv("EMPLOYER_DISCOVERY_ENABLED", "true")
    monkeypatch.setenv("EMPLOYER_AUTO_SUBMIT_ENABLED", "false")
    monkeypatch.setenv("EMPLOYER_SEARCH_CREDITS_PER_JOB", "2")
    monkeypatch.setenv("EMPLOYER_APPLY_CREDITS_PER_JOB", "7")
    monkeypatch.setenv("EMPLOYER_MAX_SEARCH_JOBS", "12")
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    try:
        with Session(engine, autoflush=False) as db:
            db.add_all([
                core.User(id=1, email="catalog-one@example.invalid", job_service_credits=13),
                core.User(id=2, email="catalog-two@example.invalid", job_service_credits=89,
                          ai_credits=5000, tier="premium"),
            ])
            for identity, employer, enabled in (
                ("source_z", "Zeta Employer", True),
                ("source_hidden", "Hidden Employer", False),
                ("source_a", "Alpha Employer", True),
            ):
                db.add(models.EmployerSource(
                    id=identity, employer=employer, platform="greenhouse",
                    board_token=identity, enabled=enabled,
                    careers_url="https://employer.example/careers",
                    verification_url="https://employer.example/careers",
                    verification_note="Synthetic catalog query fixture",
                ))
            db.commit()
            yield engine, db
    finally:
        engine.dispose()


@contextmanager
def _select_statements(engine: Engine) -> Iterator[list[str]]:
    statements: list[str] = []

    def record(_connection, _cursor, statement, _parameters, _context, _executemany):
        normalized = " ".join(statement.split()).lower()
        if normalized.startswith("select "):
            statements.append(normalized)

    event.listen(engine, "before_cursor_execute", record)
    try:
        yield statements
    finally:
        event.remove(engine, "before_cursor_execute", record)


def _authenticate(db: Session, user_id: int) -> core.User:
    return get_current_user(token=create_access_token(subject=str(user_id)), db=db)


def test_catalog_reuses_authenticated_owner_and_preserves_catalog_contract(catalog_db):
    engine, db = catalog_db
    authenticated = _authenticate(db, 1)
    with _select_statements(engine) as statements:
        result = service.catalog(db, authenticated.id)

    assert len(statements) == 1
    assert "from employer_sources" in statements[0]
    assert result["balance"] == 13
    assert result["enabled"] is True
    assert result["auto_submit_enabled"] is False
    assert result["search_credits_per_job"] == 2
    assert result["apply_credits_per_job"] == 7
    assert result["max_search_jobs"] == 12
    assert result["pricing_version"] == "synthetic-expense-v1"
    assert result["admission_limits"]["day_boundary"] == "UTC"
    assert [source["id"] for source in result["sources"]] == ["source_a", "source_z"]
    assert all(source["application_mode"] == "manual" for source in result["sources"])
    assert result["coverage"]["worldwide_recall_verified"] is False


def test_catalog_loads_uncached_requested_owner_instead_of_other_cached_owner(catalog_db):
    engine, db = catalog_db
    other_owner = _authenticate(db, 2)
    with _select_statements(engine) as statements:
        result = service.catalog(db, 1)

    assert len(statements) == 2
    assert "from users" in statements[0]
    assert "from employer_sources" in statements[1]
    assert result["balance"] == 13
    assert other_owner.job_service_credits == 89
    assert other_owner.ai_credits == 5000


def test_catalog_keeps_both_cached_owners_balances_separate(catalog_db):
    engine, db = catalog_db
    first_owner = _authenticate(db, 1)
    second_owner = _authenticate(db, 2)
    with _select_statements(engine) as statements:
        second_result = service.catalog(db, second_owner.id)
        first_result = service.catalog(db, first_owner.id)

    assert len(statements) == 2
    assert all("from employer_sources" in statement for statement in statements)
    assert second_result["balance"] == 89
    assert first_result["balance"] == 13


def test_catalog_missing_owner_does_not_reuse_cached_owner(catalog_db):
    engine, db = catalog_db
    other_owner = _authenticate(db, 2)
    with _select_statements(engine) as statements, pytest.raises(NoResultFound):
        service.catalog(db, 999)

    assert other_owner.job_service_credits == 89
    assert statements
    assert all("from users" in statement for statement in statements)


@pytest.fixture(params=["sqlite", "postgresql"])
def snapshot_db(request, tmp_path, monkeypatch):
    """Real queries on both engines; PostgreSQL owns a unique disposable schema."""
    monkeypatch.setenv("EMPLOYER_DISCOVERY_ENABLED", "true")
    monkeypatch.setenv("EMPLOYER_AUTO_SUBMIT_ENABLED", "false")
    schema = None
    if request.param == "postgresql":
        url = os.getenv("HIREWIZ_TEST_POSTGRES_URL")
        if not url:
            pytest.skip("Disposable PostgreSQL URL is not configured")
        schema = "catalog_snapshot_" + uuid4().hex
        engine = create_engine(url, execution_options={"schema_translate_map": {None: schema}})
        with engine.begin() as connection:
            connection.execute(CreateSchema(schema))
    else:
        engine = create_engine(f"sqlite:///{tmp_path / 'catalog.db'}", connect_args={"check_same_thread": False})
    try:
        Base.metadata.create_all(engine)
        with Session(engine, autoflush=False) as db:
            db.add_all([
                core.User(id=1, email="snapshot-one@example.invalid", job_service_credits=13),
                core.User(id=2, email="snapshot-two@example.invalid", job_service_credits=89,
                          ai_credits=5000, tier="premium"),
            ])
            for identity, employer, enabled in (
                ("source_z", "Zeta", True), ("source_a", "Alpha", True),
                ("source_hidden", "Hidden", False),
            ):
                db.add(_snapshot_source(identity, employer, enabled))
            db.commit()
            yield engine, db
    finally:
        if schema is not None:
            with engine.begin() as connection:
                connection.execute(DropSchema(schema, cascade=True))
        engine.dispose()


def _snapshot_source(identity, employer, enabled=True):
    return models.EmployerSource(
        id=identity, employer=employer, platform="greenhouse", board_token=identity,
        enabled=enabled, careers_url="https://employer.example/careers",
        verification_url="https://employer.example/careers",
        verification_note="Owned synthetic snapshot fixture",
    )


@contextmanager
def _snapshot_client(db):
    from backend.app.database import get_db
    from backend.app.main import app

    def database():
        yield db

    previous = dict(app.dependency_overrides)
    app.dependency_overrides[get_db] = database
    try:
        with TestClient(app) as client:
            yield client
    finally:
        app.dependency_overrides = previous


def test_actual_snapshot_route_one_select_and_owner_isolation(snapshot_db):
    engine, db = snapshot_db
    with _snapshot_client(db) as client:
        for owner, balance in ((2, 89), (1, 13)):
            with _select_statements(engine) as statements:
                response = client.get("/api/v1/employer-jobs/catalog", headers={
                    "Authorization": "Bearer " + create_access_token(subject=str(owner)),
                })
            assert response.status_code == 200
            assert response.json()["balance"] == balance
            assert response.headers["cache-control"] == "no-store, private"
            assert response.headers["pragma"] == "no-cache"
            assert len(statements) == 1
            assert "users.password_hash" not in statements[0] and "users.email" not in statements[0]
            assert [s["id"] for s in response.json()["sources"]] == ["source_a", "source_z"]


def test_snapshot_matches_existing_catalog_contract(snapshot_db):
    engine, db = snapshot_db
    expected = service.catalog(db, _authenticate(db, 1).id)
    with _select_statements(engine) as statements:
        actual = service.catalog_for_verified_subject(db, 1)
    assert actual == expected
    assert len(statements) == 1


def test_snapshot_missing_owner_refuses_cached_other_owner(snapshot_db):
    engine, db = snapshot_db
    other = _authenticate(db, 2)
    with _select_statements(engine) as statements, pytest.raises(HTTPException) as error:
        service.catalog_for_verified_subject(db, 999)
    assert error.value.status_code == 401 and error.value.detail == "Not authenticated"
    assert error.value.headers == {"WWW-Authenticate": "Bearer"}
    assert other.job_service_credits == 89
    assert len(statements) == 1


def test_snapshot_owner_with_no_enabled_sources_gets_empty_catalog(snapshot_db):
    engine, db = snapshot_db
    db.query(models.EmployerSource).update({"enabled": False})
    db.commit()
    with _select_statements(engine) as statements:
        result = service.catalog_for_verified_subject(db, 1)
    assert result["balance"] == 13 and result["sources"] == []
    assert result["auto_submit_enabled"] is False
    assert len(statements) == 1


def test_snapshot_reads_new_balance_and_source_values_despite_loaded_objects(snapshot_db):
    engine, db = snapshot_db
    owner = db.get(core.User, 1)
    source = db.get(models.EmployerSource, "source_a")
    with engine.begin() as connection:
        connection.execute(core.User.__table__.update().where(core.User.id == 1).values(job_service_credits=7))
        connection.execute(models.EmployerSource.__table__.update().where(
            models.EmployerSource.id == "source_a",
        ).values(status="healthy"))
    assert owner.job_service_credits == 13 and source.status != "healthy"
    with _select_statements(engine) as statements:
        result = service.catalog_for_verified_subject(db, 1)
    assert result["balance"] == 7
    assert next(s for s in result["sources"] if s["id"] == "source_a")["status"] == "healthy"
    assert len(statements) == 1


def test_snapshot_source_cap_and_tie_order_are_stable(snapshot_db):
    engine, db = snapshot_db
    db.query(models.EmployerSource).delete()
    db.add_all([_snapshot_source(f"tie_{i:04d}", "Same Employer") for i in range(501)])
    db.commit()
    with _select_statements(engine) as statements:
        result = service.catalog_for_verified_subject(db, 1)
    assert [s["id"] for s in result["sources"]] == [f"tie_{i:04d}" for i in range(500)]
    assert len(statements) == 1


def test_snapshot_does_not_cache_configuration_or_prices(snapshot_db, monkeypatch):
    _engine, db = snapshot_db
    service.catalog_for_verified_subject(db, 1)
    monkeypatch.setenv("EMPLOYER_SEARCH_CREDITS_PER_JOB", "3")
    monkeypatch.setenv("EMPLOYER_APPLY_CREDITS_PER_JOB", "11")
    monkeypatch.setenv("EMPLOYER_DISCOVERY_ENABLED", "false")
    result = service.catalog_for_verified_subject(db, 1)
    assert result["search_credits_per_job"] == 3 and result["apply_credits_per_job"] == 11
    assert result["enabled"] is False and result["balance"] == 13
    assert all(s["application_mode"] == "manual" for s in result["sources"])


@pytest.mark.parametrize("token", [None, "invalid", create_access_token(subject="1", expires_delta=timedelta(seconds=-10))])
def test_snapshot_route_rejected_tokens_execute_no_sql(snapshot_db, token):
    engine, db = snapshot_db
    executed = []

    def count(*args):
        executed.append(True)

    event.listen(engine, "before_cursor_execute", count)
    try:
        with _snapshot_client(db) as client:
            response = client.get("/api/v1/employer-jobs/catalog", headers={} if token is None else {
                "Authorization": "Bearer " + token,
            })
        assert response.status_code == 401 and executed == []
    finally:
        event.remove(engine, "before_cursor_execute", count)


def test_snapshot_dependency_rechecks_jwt_without_trusting_middleware(snapshot_db, monkeypatch):
    from backend.app import security

    engine, db = snapshot_db
    calls = []

    def decode(*args, **kwargs):
        calls.append(True)
        if len(calls) == 1:
            return {"sub": "1"}
        raise security.JWTError("Expired between middleware and dependency")

    monkeypatch.setattr(security.jwt, "decode", decode)
    with _select_statements(engine) as statements, _snapshot_client(db) as client:
        response = client.get("/api/v1/employer-jobs/catalog", headers={"Authorization": "Bearer synthetic"})
    assert response.status_code == 401 and len(calls) == 2 and statements == []
