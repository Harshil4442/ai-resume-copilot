"""Synthetic SQL profile projections; no native/provider authorization is simulated."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import Mock

import pytest
from sqlalchemy import create_engine, delete, event, update
from sqlalchemy.orm import Session, sessionmaker

from app.domains.candidate_accounts.profile_view import (
    PROFILE_FIELDS,
    profile_response,
    profile_view,
)
from app.domains.recovery.store import GuardDenied
from app.models import User, UserProfile
from app.routers.auth import _profile_response as legacy_response


@pytest.fixture
def owned_database(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'synthetic-profile.sqlite'}")
    User.__table__.create(engine)
    UserProfile.__table__.create(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as seed:
        seed.add_all([
            User(id=11, email="owner@example.com", tier="free", ai_credits=7,
                job_service_credits=3),
            User(id=12, email="other@example.com", tier="free", ai_credits=19),
        ])
        seed.commit()
    try:
        yield engine, factory
    finally:
        engine.dispose()


def _no_writes(engine, db: Session):
    statements = []
    def observed(_connection, _cursor, statement, _parameters, _context, _many):
        statements.append(statement)
    event.listen(engine, "before_cursor_execute", observed)
    db.flush = Mock(side_effect=AssertionError("A profile read must not flush"))
    db.commit = Mock(side_effect=AssertionError("A profile read must not commit"))
    return statements, observed


def test_absent_profile_returns_faithful_default_view_without_creating_a_record(owned_database):
    engine, factory = owned_database
    with factory() as db:
        user = db.get(User, 11)
        statements, observed = _no_writes(engine, db)
        try:
            result = profile_view(db, user)
        finally:
            event.remove(engine, "before_cursor_execute", observed)
        assert len(statements) == 1 and statements[0].lstrip().upper().startswith("SELECT")
        assert not db.new and not db.dirty
        assert result.email == "owner@example.com"
        assert result.ai_credits == 7 and result.tier == "free"
        assert result.profile_completeness == 0
        assert result.missing_fields == [label for _, label in PROFILE_FIELDS]
        assert result.years_experience == 0.0 and result.skills == []
        assert all(getattr(result, field) is None for field, _ in PROFILE_FIELDS
            if field not in {"years_experience", "skills"})
        assert not {"id", "user_id", "password_hash", "candidate_lifetime"} & result.model_dump().keys()
    with factory() as verify:
        assert verify.query(UserProfile).count() == 0


@pytest.mark.parametrize("grant", ["free", "premium_lifetime", "premium_future", "premium_expired"])
def test_stored_profile_matches_existing_response_and_never_reads_relationships(owned_database, grant):
    engine, factory = owned_database
    with factory() as db:
        user = db.get(User, 11)
        user.tier = "free" if grant == "free" else "premium"
        user.premium_until = (datetime.now(UTC) + timedelta(days=10)
            if grant == "premium_future" else datetime.now(UTC) - timedelta(days=10)
            if grant == "premium_expired" else None)
        profile = UserProfile(user_id=11, full_name="Synthetic Candidate", headline="Engineer",
            phone="", location="  ", linkedin="https://example.test/profile", github="",
            portfolio=None, target_role="Backend", preferred_job_type="full-time",
            preferred_location="Remote", years_experience=1.5, bio="Built reliable services.",
            skills=["Python", "PostgreSQL"], education="Example degree", certifications="")
        db.add(profile)
        db.commit()
        expected = legacy_response(profile, user.email)
        # Detached loaded inputs prove the pure function never follows profile.user.
        db.expunge(profile)
        db.expunge(user)
        statements, observed = _no_writes(engine, db)
        try:
            actual = profile_response(profile, user)
        finally:
            event.remove(engine, "before_cursor_execute", observed)
        assert actual.model_dump() == expected.model_dump()
        assert not statements
        assert actual.tier == ("premium" if grant in {"premium_lifetime", "premium_future"} else "free")
        assert actual.profile_completeness == 67
        assert actual.missing_fields == ["Phone", "Location", "GitHub", "Portfolio", "Certifications"]
        assert actual.full_name == "Synthetic Candidate" and actual.headline == "Engineer"
        assert actual.location == "  "  # Preserve stored display values; completeness ignores blank space.
        assert actual.phone is None and actual.github is None and actual.certifications is None
        assert actual.years_experience == 1.5 and actual.skills == ["Python", "PostgreSQL"]


def test_profile_read_refreshes_cached_entitlements_and_profile_in_one_select(owned_database):
    engine, factory = owned_database
    with factory() as db:
        user = db.get(User, 11)
        row = UserProfile(user_id=11, full_name="Earlier saved name")
        db.add(row)
        db.commit()
        assert user.ai_credits == 7 and row.full_name == "Earlier saved name"
        with factory() as other:
            other.execute(update(User).where(User.id == 11).values(ai_credits=2))
            other.execute(update(UserProfile).where(UserProfile.user_id == 11).values(full_name="Current saved name"))
            other.commit()
        statements, observed = _no_writes(engine, db)
        try:
            result = profile_view(db, user)
        finally:
            event.remove(engine, "before_cursor_execute", observed)
        assert result.ai_credits == 2 and result.full_name == "Current saved name"
        assert len(statements) == 1


def test_erased_owner_cannot_export_a_cached_profile(owned_database):
    _, factory = owned_database
    with factory() as db:
        user = db.get(User, 11)
        db.add(UserProfile(user_id=11, full_name="Must not be returned"))
        db.commit()
        with factory() as erase:
            erase.execute(delete(User).where(User.id == 11))
            erase.commit()
        with pytest.raises(GuardDenied, match="Not authenticated"):
            profile_view(db, user)


def test_foreign_profile_is_never_projected_for_another_owner(owned_database):
    _, factory = owned_database
    with factory() as db:
        first, second = db.get(User, 11), db.get(User, 12)
        profile = UserProfile(user_id=11, full_name="Owner-only name")
        db.add(profile)
        db.commit()
        with pytest.raises(GuardDenied, match="Not authenticated"):
            profile_response(profile, second)
        assert profile_response(profile, first).full_name == "Owner-only name"
        assert profile_view(db, second).full_name is None


def test_read_refuses_a_fabricated_orm_identity_without_querying(owned_database):
    engine, factory = owned_database
    with factory() as db:
        fabricated = User(id=11, email="forged@example.com", ai_credits=1000)
        statements, observed = _no_writes(engine, db)
        try:
            with pytest.raises(GuardDenied, match="Not authenticated"):
                profile_view(db, fabricated)
        finally:
            event.remove(engine, "before_cursor_execute", observed)
        assert not statements


def test_read_does_not_autoflush_unrelated_pending_profile(owned_database):
    engine, factory = owned_database
    with factory() as db:
        user = db.get(User, 11)
        pending = UserProfile(user_id=12, full_name="Unrelated draft")
        db.add(pending)
        statements, observed = _no_writes(engine, db)
        try:
            result = profile_view(db, user)
        finally:
            event.remove(engine, "before_cursor_execute", observed)
        assert result.full_name is None
        assert pending in db.new and pending.id is None
        assert len(statements) == 1


@pytest.mark.parametrize("entity,field", [("user", "ai_credits"), ("profile", "bio")])
def test_pure_projection_refuses_unloaded_fields_without_lazy_sql(owned_database, entity, field):
    engine, factory = owned_database
    with factory() as db:
        user = db.get(User, 11)
        profile = UserProfile(user_id=11, full_name="Loaded source")
        db.add(profile)
        db.commit()
        db.expire(user if entity == "user" else profile, [field])
        statements, observed = _no_writes(engine, db)
        try:
            with pytest.raises(GuardDenied, match="Not authenticated"):
                profile_response(profile, user)
        finally:
            event.remove(engine, "before_cursor_execute", observed)
        assert not statements
