"""Real concurrent transactions in an existing disposable local test schema."""
import os
import re
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import uuid4

import pytest
from backend.app import models as core
from backend.app.database import Base
from backend.app.domains.employer import models
from backend.tests.test_employer_admissions_postgres import pg_engine as owned_pg_engine
from backend.tests.test_testing_service_promotion import grant, seed
from fastapi import HTTPException
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker


@pytest.fixture
def pg_engine():
    configured = os.getenv("HIREWIZ_TEST_PROMOTION_POSTGRES_URL")
    if not configured:
        yield from owned_pg_engine.__wrapped__()
        return
    url = make_url(configured)
    if (url.host != "127.0.0.1" or url.username != "promotion_test"
            or url.port is None or not 1024 <= url.port <= 65535
            or re.fullmatch(r"hirewiz_promotion_test_[a-f0-9]{32}", url.database or "") is None):
        pytest.fail("Testing promotion concurrency accepts only its isolated local disposable database")
    schema = "promotion_" + uuid4().hex
    admin = create_engine(url, hide_parameters=True)
    with admin.begin() as connection:
        assert connection.execute(text("SELECT current_user,current_database()")).one() == (url.username, url.database)
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine = create_engine(url, hide_parameters=True, connect_args={
        "options": f"-csearch_path={schema} -clock_timeout=4000 -cstatement_timeout=10000",
    })
    try:
        yield engine
    finally:
        engine.dispose()
        with admin.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin.dispose()


@pytest.mark.parametrize("same_key", [True, False])
def test_concurrent_campaign_grants_cap_global_total_at_three(pg_engine, same_key):
    Base.metadata.create_all(pg_engine)
    factory = sessionmaker(bind=pg_engine, autoflush=False)
    seed(factory)
    gate = Barrier(2)

    def attempt(number):
        with factory() as db:
            gate.wait(timeout=10)
            key = "synthetic-owner-test-v1" if same_key else f"synthetic-review-{number}"
            try:
                result = grant(db, idempotency_key=key)
                db.commit()
                return "replay" if result["idempotent_replay"] else "granted"
            except HTTPException as error:
                db.rollback()
                assert error.status_code == 409
                return "refused"

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(attempt, (1, 2)))
    assert sorted(results) == sorted(["granted", "replay" if same_key else "refused"])
    with factory() as db:
        assert db.get(core.User, 1).job_service_credits == 3
        assert db.query(models.ServiceCreditEvent).count() == db.query(core.AdminAuditEvent).count() == 1
