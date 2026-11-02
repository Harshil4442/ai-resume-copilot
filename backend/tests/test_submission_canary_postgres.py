"""Required owned PostgreSQL admission races, not real canary qualification."""
from backend.app import models
from backend.app.domains.browser_assistance import service as browser
from backend.app.domains.credits import canary, service, submission
from backend.tests.browser_submission_unified_fixture import configure, review
from backend.tests.test_browser_submit_postgres import pg_submit as pg_submit
from backend.tests.test_canary_admission import prepare
from backend.tests.test_employer_admissions_postgres import pg_engine as pg_engine
from backend.tests.test_submission_canary import real_baseline
from backend.tests.test_unified_browser_submission_postgres import accept_action, parallel
from backend.tests.test_unified_browser_submission_postgres import pg_unified as pg_unified

assert pg_unified and pg_submit and pg_engine


def test_concurrent_exact_application_acceptance_counts_canary_and_reserves_three_once(pg_unified, monkeypatch):
    owned = pg_unified
    config = configure(owned, monkeypatch, quantity=0)
    now = browser.now()
    _, audience, digest = prepare(owned.db, monkeypatch, now=now, config=config)
    canary.choose(owned.db, user_id=1, review_id=audience.id, review_sha256=digest,
        notice_sha256=audience.notice_sha256, choice="opt_in", idempotency_key="owned-pg-submission-opt-in",
        expected_sequence=0, runtime=config, now=now)
    owned.db.commit()
    real_baseline(owned)
    value = review(owned)
    owned.db.rollback()
    results = parallel(lambda: accept_action(owned, value), lambda: accept_action(owned, value))
    assert sum(result.get("state") == "approved" for result in results) == 1
    owned.db.expire_all()
    audits = owned.db.query(models.AdminAuditEvent).filter_by(action=canary.ACCEPT_ACTION).all()
    assert len(audits) == 1 and audits[0].after_state["source_kind"] == "application"
    row, hold = submission.held(owned.db, owned.app, pending=True)
    assert row.reserved_credits == 3 and hold.snapshot["canary"] == audits[0].after_state
    assert service.wallet(owned.db, user_id=1).available_credits == 37
    assert owned.audit()["credits"] == 100 and owned.audit()["native_calls"] == 0
