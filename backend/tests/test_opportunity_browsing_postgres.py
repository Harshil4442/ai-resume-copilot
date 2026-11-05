"""One real owned PG17 dialect control; no live database or native authority."""

from datetime import datetime
from uuid import uuid4

import pytest
from backend.tests.test_mail_budget_postgres import owned_pg_url
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import sessionmaker

from app import models
from app.domains.career import browsing

assert owned_pg_url


@pytest.fixture
def pg_factory(owned_pg_url):
    schema = "opportunity_page_" + uuid4().hex
    admin = create_engine(owned_pg_url)
    with admin.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine = create_engine(
        owned_pg_url,
        connect_args={
            "application_name": "opportunity-page-owned",
            "options": f"-csearch_path={schema} -clock_timeout=5000 -cstatement_timeout=10000 -ctimezone=UTC",
        },
    )
    tables = [models.User, models.Resume, models.JobMatch, models.Opportunity]
    models.Base.metadata.create_all(engine, tables=[model.__table__ for model in tables])
    try:
        yield sessionmaker(engine)
    finally:
        engine.dispose()
        with admin.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin.dispose()


def test_actual_pg_literal_filters_counts_keyset_owner_and_metadata_only(pg_factory):
    stamp = datetime(2026, 11, 5, 12)
    with pg_factory() as db:
        db.add_all(
            [
                models.User(id=1, email="pg-owner@example.invalid"),
                models.User(id=2, email="pg-other@example.invalid"),
            ]
        )
        db.flush()
        for identity, owner, stage, title in [
            ("opp_a", 1, "saved", "100%_\\Engineer"),
            ("opp_b", 1, "interviewing", "100%_\\Engineer"),
            ("opp_c", 1, "rejected", "100%_\\Engineer"),
            ("opp_archived", 1, "archived", "100%_\\Engineer"),
            ("opp_wild", 1, "saved", "100XaZEngineer"),
            ("opp_other", 2, "saved", "100%_\\Engineer"),
        ]:
            db.add(
                models.Opportunity(
                    id=identity,
                    user_id=owner,
                    title=title,
                    company="Synthetic",
                    location="Remote",
                    stage=stage,
                    priority="medium",
                    job_description="private-large-body-" + "x" * 100000,
                    job_snapshot={"private": "snapshot"},
                    notes="private-notes",
                    created_at=stamp,
                    updated_at=stamp,
                )
            )
        db.commit()
        statements = []

        def observe(_c, _cur, statement, _params, _ctx, _many):
            statements.append(statement)

        event.listen(db.get_bind(), "before_cursor_execute", observe)
        try:
            first = browsing.page(db, 1, search="100%_\\engineer", limit=2)
            assert len(statements) == 2
            assert not any(
                field in sql
                for sql in statements
                for field in (
                    "job_description",
                    "job_snapshot",
                    "outcome_notes",
                    "opportunities.notes",
                )
            )
            assert [row.id for row in first.items] == ["opp_c", "opp_b"]
            assert (first.total, first.total_owned, first.active_count, first.interview_count) == (
                3,
                5,
                3,
                1,
            )
            assert "private" not in first.model_dump_json()
            assert first.next_cursor is not None
            statements.clear()
            older = browsing.page(
                db, 1, search="100%_\\engineer", limit=2, cursor=first.next_cursor
            )
            assert len(statements) == 2
            assert [row.id for row in older.items] == ["opp_a"] and older.next_cursor is None
            assert older.total == 3 and all(
                row.id != "opp_other" for row in [*first.items, *older.items]
            )
        finally:
            event.remove(db.get_bind(), "before_cursor_execute", observe)
