"""Genuine row-lock races on CI's owned disposable PostgreSQL only."""
import os
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from backend.app.database import Base
from backend.app.domains.career import schemas, service
from backend.app.models import EvidenceItem
from backend.tests.fixtures.evidence_fence import install_owned_fence
from backend.tests.test_employer_admissions_postgres import pg_engine as owned_pg_engine
from backend.tests.test_evidence_review import seed
from fastapi import HTTPException
from sqlalchemy.orm import sessionmaker


@pytest.fixture
def pg_engine():
    if os.getenv("CI") == "true":
        assert os.getenv("HIREWIZ_TEST_POSTGRES_URL"), "CI evidence review requires its owned PostgreSQL URL"
    for engine in owned_pg_engine.__wrapped__():
        install_owned_fence(engine)
        yield engine


def test_ci_cannot_silently_skip_missing_native_postgres(monkeypatch):
    monkeypatch.setenv("CI", "true")
    monkeypatch.delenv("HIREWIZ_TEST_POSTGRES_URL", raising=False)
    with pytest.raises(AssertionError, match="requires its owned PostgreSQL URL"):
        next(pg_engine.__wrapped__())


@pytest.mark.parametrize("second", [{"approval_state": "approved"}, {"evidence_text": "Competing edit."}])
def test_one_exact_snapshot_cannot_authorize_two_concurrent_mutations(pg_engine, second):
    Base.metadata.create_all(pg_engine)
    factory = sessionmaker(bind=pg_engine, autoflush=False)
    identifier, shown = seed(factory)
    barrier = Barrier(2)
    def write(changes):
        with factory() as db:
            # Both requests have genuinely cached the old row before either lock.
            row = db.get(EvidenceItem, identifier)
            assert schemas.evidence_snapshot_fingerprint(row) == shown
            barrier.wait(timeout=5)
            try:
                result = service.update_evidence(db, 1, identifier, schemas.EvidenceUpdate(
                    expected_fingerprint=shown, **changes))
                return 200, result.evidence_text, result.approval_state
            except HTTPException as exc:
                db.rollback()
                assert exc.detail["code"] == "evidence_refresh_required"
                return exc.status_code, None, None
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(write, [{"evidence_text": "First competing edit."}, second]))
    assert sorted(result[0] for result in results) == [200, 409]
    winner = next(result for result in results if result[0] == 200)
    with factory() as db:
        row = db.get(EvidenceItem, identifier)
        assert (row.evidence_text, row.approval_state) == winner[1:]
        assert not (row.evidence_text != "Built reliable systems." and row.approval_state == "approved")
