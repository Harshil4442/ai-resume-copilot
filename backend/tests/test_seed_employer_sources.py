from __future__ import annotations

import json

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from test_evidence_writer_fence_postgres import app25_database as app25_database
from test_evidence_writer_fence_postgres import fenced as fenced
from test_evidence_writer_fence_postgres import pg25 as pg25

from app.database import Base
from scripts import seed_employer_sources as seeder

REVIEWER = "reviewer@example.invalid"


@pytest.fixture(params=["sqlite", "postgres"])
def registry_db(request):
    if request.param == "postgres":
        yield request.getfixturevalue("fenced")
        return
    engine = create_engine("sqlite://")
    try:
        Base.metadata.create_all(engine)
        yield engine
    finally:
        engine.dispose()


def sources():
    reviewed = seeder.load_reviewed_sources(seeder.ROOT / "resources/employer_sources.reviewed.json")
    return (next(item for item in reviewed if item.employer == "Linear"),
            next(item for item in reviewed if item.platform == "greenhouse"))


def test_reviewed_file_refuses_a_stale_uppercase_linear_manifest(tmp_path):
    linear, unrelated = sources()
    stale = linear.model_copy(update={"board_token": "Linear"})
    path = tmp_path / "stale-reviewed.json"
    path.write_text(json.dumps([unrelated.model_dump(), stale.model_dump()]))
    with pytest.raises(ValueError, match="exact lowercase careers record"):
        seeder.load_reviewed_sources(path)


def test_stale_manifest_cannot_add_duplicate_after_repair(registry_db):
    linear, unrelated = sources()
    stale = linear.model_copy(update={"board_token": "Linear"})
    factory = sessionmaker(bind=registry_db, autoflush=False)
    with factory() as db:
        assert seeder.seed(db, [linear], reviewer_email=REVIEWER) == {"created": 1, "updated": 0}
        same_id = db.query(seeder.EmployerSource.id).scalar()
        with pytest.raises(ValueError, match="exact lowercase careers record"):
            seeder.seed(db, [unrelated, stale], reviewer_email=REVIEWER)
        assert db.query(seeder.EmployerSource.id, seeder.EmployerSource.board_token).all() == [(same_id, "linear")]
        assert db.query(seeder.core.AdminAuditEvent.id).count() == 1
        assert seeder.seed(db, [linear, unrelated], reviewer_email=REVIEWER) == {"created": 1, "updated": 1}
        assert db.query(seeder.EmployerSource.id).filter_by(board_token="linear").scalar() == same_id


@pytest.mark.parametrize("legacy_token", ["Linear", "LINEAR", "retired-linear-tenant"])
def test_generic_seeder_cannot_create_new_identity_while_historical_source_remains(registry_db, legacy_token):
    linear, unrelated = sources()
    factory = sessionmaker(bind=registry_db, autoflush=False)
    with factory() as db:
        db.add(seeder.EmployerSource(id="historical-source", **linear.model_dump(exclude={"board_token"}),
                                     board_token=legacy_token))
        db.commit()
        with pytest.raises(ValueError, match="same-ID correction before seeding"):
            seeder.seed(db, [unrelated, linear], reviewer_email=REVIEWER)
        assert db.query(seeder.EmployerSource.id, seeder.EmployerSource.board_token).all() == [
            ("historical-source", legacy_token)]
        assert db.query(seeder.core.AdminAuditEvent.id).count() == 0


def test_seed_without_linear_keeps_other_case_sensitive_board_tokens(registry_db):
    _, unrelated = sources()
    exact = unrelated.model_copy(update={"board_token": "IndependentCaseSensitiveTenant"})
    factory = sessionmaker(bind=registry_db, autoflush=False)
    with factory() as db:
        assert seeder.seed(db, [exact], reviewer_email=REVIEWER) == {"created": 1, "updated": 0}
        assert db.query(seeder.EmployerSource.board_token).scalar() == "IndependentCaseSensitiveTenant"
