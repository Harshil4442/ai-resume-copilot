from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

import pytest
from backend.app import models as core
from backend.app.database import Base
from backend.app.domains.employer import models, service
from backend.app.security import create_access_token, get_current_user
from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session
from sqlalchemy.orm.exc import NoResultFound


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
    assert result["pricing_version"] == "employer-services-intro-v1"
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
