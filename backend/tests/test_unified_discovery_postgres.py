"""Native PostgreSQL owner fences prevent duplicate batch charges across sessions."""

from datetime import timedelta

from backend.app import models as core
from backend.app.domains.common import utcnow
from backend.app.domains.credits import discovery, service
from backend.app.domains.credits.discovery_models import CreditDiscoveryDelivery
from backend.app.domains.credits.models import CreditReservation
from backend.app.domains.employer import models
from backend.tests.test_unified_credits import grant
from backend.tests.test_unified_credits_postgres import factory, parallel
from backend.tests.test_unified_discovery import configure, quoted

assert factory


def seed(factory, monkeypatch):
    configure(monkeypatch)
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("EMPLOYER_DISCOVERY_ENABLED", "true")
    with factory() as db:
        grant(db)
        db.add(core.Resume(id=10, user_id=1, skills=["Python"], original_filename="synthetic.pdf"))
        db.add(
            models.EmployerSource(
                id="synthetic-source",
                employer="Synthetic Employer",
                platform="greenhouse",
                board_token="synthetic",
                enabled=True,
                region="global",
                careers_url="https://example.com/careers",
                verification_url="https://example.com/careers",
                verification_note="Synthetic fixture",
                status="healthy",
            )
        )
        db.commit()
        for number in (1, 2):
            db.add(
                models.EmployerPosting(
                    id=f"pg-job-{number}",
                    source_id="synthetic-source",
                    external_id=str(number),
                    requisition_id=f"req-{number}",
                    title="Python Engineer",
                    employer="Synthetic Employer",
                    location="Bengaluru",
                    description="Python Engineer",
                    canonical_url=f"https://example.com/{number}",
                    apply_url=f"https://example.com/{number}",
                    content_sha256=str(number) * 64,
                    last_checked_at=utcnow(),
                )
            )
        db.commit()


def test_same_discovery_intent_reserves_one_credit_once(factory, monkeypatch):
    seed(factory, monkeypatch)
    with factory() as db:
        payload = quoted(db)

    def accept():
        with factory() as db:
            result = discovery.create(db, user_id=1, payload=payload)
            db.commit()
            return result.id

    identities = parallel(accept, accept)
    assert identities[0] == identities[1]
    with factory() as db:
        assert db.query(CreditReservation).count() == 1
        assert service.wallet(db, user_id=1).available_credits == 39
        result = discovery.execute(db, user_id=1, search_id=identities[0])
        assert result.status == "completed", result.scope
        assert result.delivered_count == 2 and result.charged_credits == 1


def test_competing_batches_cannot_charge_for_same_canonical_openings(factory, monkeypatch):
    seed(factory, monkeypatch)
    with factory() as db:
        first = discovery.create(db, user_id=1, payload=quoted(db, "parallel-discovery-one"))
        second = discovery.create(db, user_id=1, payload=quoted(db, "parallel-discovery-two"))

    def perform(identity):
        with factory() as db:
            # NOWAIT contention may cancel one batch before useful delivery;
            # its credit returns, while the unknown worker expense stays held.
            try:
                return discovery.execute(db, user_id=1, search_id=identity)
            except Exception:
                db.rollback()
                return discovery.cancel(
                    db, user_id=1, search_id=identity, reason="concurrent_retry_cancelled"
                )

    results = parallel(lambda: perform(first.id), lambda: perform(second.id))
    assert sum(result.charged_credits for result in results) == 1
    with factory() as db:
        assert db.query(CreditDiscoveryDelivery).count() == 2
        assert sum(row.committed_credits for row in db.query(CreditReservation).all()) == 1
        assert service.wallet(db, user_id=1).available_credits == 39
        # Each batch releases its owner locks; no unresolved worker may rescan.
        for result in results:
            if result.status == "preparing":
                row = db.get(models.EmployerSearch, result.id)
                row.created_at = utcnow() - timedelta(minutes=6)
                db.commit()
                assert discovery.execute(db, user_id=1, search_id=result.id).refunded_credits == 1


def test_discovery_runs_against_actual_migrated_schema_with_history_triggers(factory, monkeypatch):
    # A separate factory's schema already has declarative tables. Create a fresh
    # owned schema through the same fixture so real Alembic triggers are present.
    from backend.tests.test_employer_admissions_postgres import _migrate, pg_engine
    from sqlalchemy.orm import sessionmaker

    for engine in pg_engine.__wrapped__():
        _migrate(engine, "head")
        maker = sessionmaker(engine, autoflush=False)
        with maker() as db:
            db.add(
                core.User(
                    id=1,
                    email="migration-discovery@example.test",
                    ai_credits=17,
                    job_service_credits=13,
                )
            )
            db.commit()
        seed(maker, monkeypatch)
        with maker() as db:
            request = quoted(db)
            first = discovery.create(db, user_id=1, payload=request)
            result = discovery.execute(db, user_id=1, search_id=first.id)
            assert result.status == "completed", result.scope
            assert result.delivered_count == 2 and result.charged_credits == 1
            second = discovery.create(db, user_id=1, payload=quoted(db, "migrated-repeat-batch"))
            repeated = discovery.execute(db, user_id=1, search_id=second.id)
            assert repeated.status == "completed" and repeated.delivered_count == 0
            assert repeated.refunded_credits == 1
            assert db.query(CreditDiscoveryDelivery).count() == 2


def test_postgres_byte_budget_handles_literal_backslashes_and_utf8(factory, monkeypatch):
    seed(factory, monkeypatch)
    content = r"Python Engineer C:\tools regex \w+ \123" + " café Bengaluru ಕನ್ನಡ"
    with factory() as db:
        db.query(models.EmployerPosting).update({"description": content})
        db.commit()
        # Both quote's conservative aggregate and worker's exact cohort measure
        # actual UTF-8, never PostgreSQL's text-to-bytea escape interpretation.
        from backend.app.domains.employer import service as employer
        from sqlalchemy import literal

        measured = db.query(employer._utf8_bytes(db, literal(content))).scalar()
        assert measured == len(content.encode("utf-8"))
        accepted = discovery.create(db, user_id=1, payload=quoted(db))
        result = discovery.execute(db, user_id=1, search_id=accepted.id)
        assert result.status == "completed", result.scope
        assert result.delivered_count == 2 and result.charged_credits == 1
