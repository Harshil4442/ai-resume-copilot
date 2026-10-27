"""Workspace scoring stays owner-bound while match reads remain constant."""
from __future__ import annotations

import pytest
from backend.app.database import Base
from backend.app.domains.career.service import calculate_skill_roi
from backend.app.models import EvidenceItem, JobMatch, Opportunity, Resume, User
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker


@pytest.fixture
def database():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine, tables=[
        User.__table__, Resume.__table__, JobMatch.__table__,
        Opportunity.__table__, EvidenceItem.__table__,
    ])
    factory = sessionmaker(bind=engine, autoflush=False)
    with factory() as db:
        db.add_all([User(id=1, email="owner@example.invalid"),
                    User(id=2, email="other@example.invalid")])
        db.commit()
        yield db, engine
    engine.dispose()


def _opportunity(identity, *, match=None, snapshot=None, user=1, **kwargs):
    return Opportunity(id=identity, user_id=user, title="Synthetic role",
                       latest_match_id=match,
                       job_snapshot={"required_skills": snapshot or []}, **kwargs)


def _read_count(db, engine):
    statements = []

    def record(connection, cursor, statement, parameters, context, executemany):
        if statement.lstrip().upper().startswith("SELECT"):
            statements.append(statement)

    event.listen(engine, "before_cursor_execute", record)
    try:
        result = calculate_skill_roi(db, 1)
    finally:
        event.remove(engine, "before_cursor_execute", record)
    return result, statements


@pytest.mark.parametrize("count", [1, 10, 500])
def test_workspace_size_does_not_add_match_queries(database, count):
    db, engine = database
    db.add_all([JobMatch(id=i, user_id=1, required_skills=["Python", "SQL"])
                for i in range(1, count + 1)])
    db.flush()
    db.add_all([_opportunity(f"opp-{i}", match=i) for i in range(1, count + 1)])
    db.commit()

    result, statements = _read_count(db, engine)

    assert len(statements) == 3
    assert sum("FROM job_matches" in statement for statement in statements) == 1
    assert result.opportunity_count == count
    assert [item.skill for item in result.items] == ["python", "sql"]
    assert all(item.opportunity_count == count and item.demand_ratio == 1
               and item.evidence_strength == 0 and item.score == 92.67
               for item in result.items)


def test_foreign_missing_empty_and_shared_matches_keep_snapshot_and_score_semantics(database):
    db, engine = database
    db.add_all([
        JobMatch(id=1, user_id=1, required_skills=["Python", " python ", " SQL "]),
        JobMatch(id=2, user_id=2, required_skills=["Private skill"]),
        JobMatch(id=3, user_id=1, required_skills=[]),
    ])
    db.flush()
    db.add_all([
        _opportunity("owned-one", match=1, outcome="offer_accepted"),
        _opportunity("owned-two", match=1),
        _opportunity("foreign-match", match=2, snapshot=["React"], outcome="rejected"),
        # SQLite's disabled FK enforcement represents a legacy dangling match.
        _opportunity("missing-match", match=999, snapshot=["Go"]),
        _opportunity("empty-match", match=3, snapshot=["Rust"], outcome="offer_declined"),
        _opportunity("archived", match=1, snapshot=["Archived skill"], stage="archived"),
        _opportunity("another-owner", match=2, snapshot=["Private skill"], user=2),
    ])
    for identity, owner, skills, state in [
        ("python-one", 1, ["Python", " python "], "approved"),
        ("python-two", 1, ["Python"], "approved"),
        ("react", 1, ["React"], "approved"),
        ("pending", 1, ["SQL", "Go"], "pending"),
        ("foreign", 2, ["SQL", "Go", "Private skill"], "approved"),
    ]:
        db.add(EvidenceItem(id=identity, user_id=owner, category="experience",
                            title="Synthetic evidence", evidence_text="Fictional only",
                            skills=skills, approval_state=state))
    db.commit()

    result, statements = _read_count(db, engine)

    assert len(statements) == 3 and result.opportunity_count == 5
    assert [item.skill for item in result.items] == ["sql", "python", "go", "rust", "react"]
    assert [(item.opportunity_count, item.demand_ratio, item.evidence_strength,
             item.outcome_signal, item.estimated_hours, item.score) for item in result.items] == [
        (2, 0.4, 0, 0.5, 30, 55.67),
        (2, 0.4, 1, 0.5, 8, 43),
        (1, 0.2, 0, 0, 30, 36.67),
        (1, 0.2, 0, -0.25, 30, 34.17),
        (1, 0.2, 0.5, -0.25, 16, 26.5),
    ]
    assert "Private skill" not in result.model_dump_json()
    assert result.items[0].reason == (
        "Appears in 2 of 5 active opportunities; no approved evidence exists yet."
        " Your recorded outcomes add a positive signal."
    )


def test_no_matches_and_empty_workspace_do_not_query_match_table(database):
    db, engine = database
    result, statements = _read_count(db, engine)
    assert result.opportunity_count == 0 and result.items == []
    assert len(statements) == 2
    db.add(_opportunity("snapshot-only", snapshot=["Python"]))
    db.commit()
    result, statements = _read_count(db, engine)
    assert len(statements) == 2 and result.opportunity_count == 1
    assert [(item.skill, item.opportunity_count) for item in result.items] == [("python", 1)]
