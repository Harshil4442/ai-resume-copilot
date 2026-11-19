"""Controlled request deadline and projection denial are separate fail-closed paths."""
from sqlalchemy import update

from app.domains.candidate_accounts import dashboard
from app.models import User
from tests.test_candidate_production_factory import configured as configured
from tests.test_owned_bootstrap import api as api
from tests.test_owned_dashboard import request


def test_real_request_deadline_precedes_projection_and_restoration_keeps_subject_denial(api, monkeypatch, caplog):
    registry = api.c.accounts._composition().scoped.publication.registry
    original_clock = registry.monotonic
    assert registry.deadline_seconds == 5.0 and registry.max_attempts == 3
    calls = []
    original_reader = dashboard._reminders
    def drift(db, user_id):
        calls.append(user_id)
        result = original_reader(db, user_id)
        db.execute(update(User).where(User.id == user_id).values(credential_epoch=100))
        return result
    monkeypatch.setattr(dashboard, "_reminders", drift)
    writes = list(api.c.native.calls)
    ticks = iter((0.0, 5.0))
    with monkeypatch.context() as deadline_clock:
        deadline_clock.setattr(registry, "monotonic", lambda: next(ticks))
        response = request(api)
    assert registry.monotonic is original_clock
    assert response.status_code == 503 and "bootstrap" not in response.text
    assert calls == [] and api.validation.call_count == 1
    failures = [record.native_failure.payload() for record in caplog.records
                if hasattr(record, "native_failure")]
    assert failures == [{"event": "native_boundary_failure_v1", "component": "candidate_account",
        "stage": "service", "exception_type": "GuardUnavailable", "guard_code": "transaction_deadline"}]
    response = request(api)
    assert response.status_code == 401 and "bootstrap" not in response.text
    assert calls == [api.owner] and api.validation.call_count == 2
    assert api.c.native.calls == writes
