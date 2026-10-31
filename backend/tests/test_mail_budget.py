"""Finite mail ledger/provider ports only; no SQL, SQLite, network or email IO."""
from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import httpx
import pytest
from sqlalchemy.engine import Engine

from app.domains.notifications import mail_budget as budget
from app.domains.notifications import password_reset as reset_mail
from app.domains.notifications import privacy_erasure as privacy_mail
from app.domains.notifications import service
from app.models import (
    AdminAuditEvent,
    EntitlementLedger,
    NotificationOutbox,
    PasswordResetToken,
    PaymentOrder,
    User,
)

NOW = datetime(2026, 10, 31, 6, tzinfo=UTC)
RECEIPT = hashlib.sha256(b"synthetic bounded provider receipt").hexdigest()


class Store:
    def __init__(self):
        self.rows = {}
        self.outbox = []
        self.business = []
        self.trace = []
        self.active = 0
        self.commits = 0
        self.fail_commit = None
        self.user = SimpleNamespace(id=1, email="synthetic@example.invalid")
        self.proof = SimpleNamespace(purpose="password_reset")

    def session(self):
        return Port(self)


class Query:
    def __init__(self, port, model):
        self.port, self.model, self.since, self.maximum = port, model, None, None
        self.values = {}

    def filter(self, *conditions):
        self.port.store.trace.append(("filter", conditions))
        for condition in conditions:
            if getattr(condition.left, "key", None) == "created_at":
                self.since = condition.right.value
            elif getattr(condition.left, "key", None) in {"idempotency_key", "source_order_id", "id"}:
                self.values[condition.left.key] = condition.right.value
        return self

    def filter_by(self, **values):
        self.values.update(values)
        return self

    def limit(self, value):
        self.maximum = value
        return self

    def order_by(self, *values):
        return self

    def with_for_update(self):
        return self

    def update(self, values, **kwargs):
        self.port.store.trace.append(("update", values))
        return 0

    def all(self):
        if self.model is AdminAuditEvent:
            rows = [row for row in self.port.store.rows.values() if row.action == budget.RESERVATION
                and (self.since is None or row.created_at >= self.since)]
        elif self.model is User:
            rows = [self.port.store.user]
        elif self.model is EntitlementLedger:
            rows = [row for row in self.port.store.business + self.port.new if isinstance(row, EntitlementLedger)
                and all(getattr(row, key) == value for key, value in self.values.items())]
        else:
            rows = [row for row in self.port.store.outbox if all(getattr(row, key) == value for key, value in self.values.items())]
        return rows if self.maximum is None else rows[:self.maximum]

    def one_or_none(self):
        return next(iter(self.all()), None)

    def first(self):
        return next(iter(self.all()), None)

    def one(self):
        rows = self.all()
        assert len(rows) == 1
        return rows[0]


class Port:
    def __init__(self, store):
        self.store, self.new = store, []

    def __enter__(self):
        self.store.active += 1
        return self

    def __exit__(self, *args):
        self.new.clear()
        self.store.active -= 1

    def get_bind(self):
        return SimpleNamespace(dialect=SimpleNamespace(name="postgresql"))

    def execute(self, statement, params=None):
        assert str(statement) == "SELECT pg_advisory_xact_lock(:key)"
        assert params == {"key": budget.LOCK}
        self.store.trace.append("lock")

    def get(self, model, identity):
        if model is AdminAuditEvent:
            return self.store.rows.get(identity)
        if model is User:
            return self.store.user
        assert model is PasswordResetToken
        return self.store.proof

    def query(self, model):
        return Query(self, model)

    def add(self, row):
        self.new.append(row)
        if isinstance(row, NotificationOutbox):
            self.store.outbox.append(row)

    def flush(self, items=None):
        assert items is not None
        self.store.trace.append(("flush", len(items)))

    def commit(self):
        self.store.commits += 1
        for row in self.new:
            if isinstance(row, AdminAuditEvent):
                assert row.id not in self.store.rows
                self.store.rows[row.id] = row
            elif not isinstance(row, NotificationOutbox):
                self.store.business.append(row)
        self.new.clear()
        self.store.trace.append("commit")
        if self.store.fail_commit == self.store.commits:
            raise RuntimeError("Synthetic unknown commit; never log credential-bearing error text")

    def rollback(self):
        self.new.clear()


@pytest.fixture(autouse=True)
def no_io(monkeypatch):
    import socket

    def refuse(*args, **kwargs):
        raise AssertionError("Mail controls must never connect to a database, network or provider")

    monkeypatch.setattr(Engine, "connect", refuse)
    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(budget, "utcnow", lambda: NOW)
    monkeypatch.setattr(service, "utcnow", lambda: NOW)
    monkeypatch.setattr(reset_mail, "utcnow", lambda: NOW)


def reservations(store):
    return [row for row in store.rows.values() if row.action == budget.RESERVATION]


def test_atomic_reset_pair_reserves_six_units_and_same_transaction_reuses_pending_audits():
    store, keys = Store(), ["password-reset:synthetic", "password-changed:synthetic"]
    with store.session() as db:
        first = budget.reserve_messages(db, keys, category="password_reset", expires_at=NOW+timedelta(minutes=15))
        second = budget.reserve_messages(db, [keys[0]], category="password_reset")
        assert len(db.new) == 2 and second == first[:1]
        db.commit()
    assert sum(row.after_state["units"] for row in reservations(store)) == 6
    for row in store.rows.values():
        assert row.actor_user_id is None and row.target_type == "provider_mail"
        assert row.target_id not in keys and len(row.target_id) == 64
        assert all(key not in json.dumps(row.after_state) for key in keys)


def test_shared_daily_attempt_cap_defers_new_work_and_never_revokes_existing_envelope():
    store = Store()
    with store.session() as db:
        for start, size in ((0, 10), (10, 10), (20, 10), (30, 3)):
            budget.reserve_messages(db, [f"synthetic-{i}" for i in range(start, start+size)], category="lifecycle", now=NOW)
        db.commit()
        assert sum(row.after_state["units"] for row in reservations(store)) == 99
        with pytest.raises(budget.MailBudgetError) as error:
            budget.reserve_messages(db, ["new-work"], category="password_reset")
        assert error.value.mail_outcome == "deferred"
        existing = budget.reserve_messages(db, ["synthetic-0"], category="lifecycle")
        assert existing[0].expires_at == NOW+timedelta(hours=24) and len(reservations(store)) == 33


@pytest.mark.parametrize("kind", ["payment_receipt", "analysis_completed", "welcome", "account_recovery_requested"])
def test_optional_production_enqueue_at_cap_preserves_caller_transaction_without_unfunded_outbox(monkeypatch, kind):
    store = Store()
    monkeypatch.setenv("LIFECYCLE_EMAILS_ENABLED", "true")
    with store.session() as db:
        for start, size in ((0, 10), (10, 10), (20, 10), (30, 3)):
            budget.reserve_messages(db, [f"funded-{i}" for i in range(start, start+size)], category="lifecycle")
        db.commit()
        pending_business = EntitlementLedger(user_id=1, source_order_id=12, status="active")
        db.add(pending_business)
        monkeypatch.setattr(db, "rollback", lambda: pytest.fail("Optional cap must not roll back caller business work"))
        assert service.enqueue_notification(db, user_id=1, notification_type=kind, recipient=store.user.email,
            payload={}, idempotency_key="unaccepted-optional:"+kind) is None
        assert db.new == [pending_business] and not store.outbox
        db.commit()
    assert store.business == [pending_business]
    assert len(reservations(store)) == 33 and sum(row.after_state["units"] for row in reservations(store)) == 99


def test_paid_entitlement_actual_grant_finishes_when_optional_receipt_cannot_be_funded(monkeypatch):
    from app.billing.service import _grant_entitlement
    store = Store()
    store.user.ai_credits = 7
    monkeypatch.setenv("LIFECYCLE_EMAILS_ENABLED", "true")
    with store.session() as db:
        for start, size in ((0, 10), (10, 10), (20, 10), (30, 3)):
            budget.reserve_messages(db, [f"funded-{i}" for i in range(start, start+size)], category="lifecycle")
        db.commit()
        order = PaymentOrder(id=12, public_id="synthetic-captured-order", user_id=1, provider="synthetic",
            sku="synthetic-analysis", entitlement_kind="analysis_units", entitlement_quantity=3,
            gross_amount_minor=100, currency="INR", status="paid")
        monkeypatch.setattr(db, "rollback", lambda: pytest.fail("Receipt quota cannot reverse a captured purchase"))
        assert _grant_entitlement(db, order, NOW) is True
        db.commit()
    assert store.user.ai_credits == 10 and order.status == "paid"
    assert len(store.business) == 1 and store.business[0].source_order_id == order.id
    assert store.business[0].status == "active" and store.business[0].quantity == 3
    assert not store.outbox and len(reservations(store)) == 33


def test_optional_cap_preserves_previously_accepted_unfunded_outbox_without_adopting_it(monkeypatch):
    store = Store()
    monkeypatch.setenv("LIFECYCLE_EMAILS_ENABLED", "true")
    old = NotificationOutbox(id="accepted-legacy-item", notification_type="welcome", recipient=store.user.email,
        idempotency_key="accepted-legacy-unfunded", payload={"synthetic": "retained obligation"}, status="pending")
    store.outbox.append(old)
    with store.session() as db:
        for start, size in ((0, 10), (10, 10), (20, 10), (30, 3)):
            budget.reserve_messages(db, [f"funded-{i}" for i in range(start, start+size)], category="lifecycle")
        db.commit()
        assert service.enqueue_notification(db, user_id=1, notification_type="welcome", recipient=store.user.email,
            payload={}, idempotency_key=old.idempotency_key) is None
        assert db.new == [] and store.outbox == [old]
        assert old.status == "pending" and old.payload == {"synthetic": "retained obligation"}
        assert len(reservations(store)) == 33


@pytest.mark.parametrize("outcome", ["unknown", "permanent"])
def test_optional_enqueue_does_not_hide_corrupt_or_invalid_budget_authority(monkeypatch, outcome):
    store = Store()
    monkeypatch.setenv("LIFECYCLE_EMAILS_ENABLED", "true")
    def refuse(*args, **kwargs):
        raise budget.MailBudgetError(outcome)
    monkeypatch.setattr(budget, "reserve_messages", refuse)
    with store.session() as db:
        with pytest.raises(budget.MailBudgetError) as error:
            service.enqueue_notification(db, user_id=1, notification_type="welcome", recipient=store.user.email,
                payload={}, idempotency_key="bad-budget")
        assert error.value.mail_outcome == outcome and db.new == [] and not store.outbox


def test_actual_day_one_onboarding_enqueue_has_a_fixed_delivery_window_after_due(monkeypatch):
    store, calls = Store(), []
    due = NOW+timedelta(hours=24)
    monkeypatch.setenv("LIFECYCLE_EMAILS_ENABLED", "true")
    with store.session() as db:
        item = service.enqueue_notification(db, user_id=1, notification_type="onboarding_reminder", recipient=store.user.email,
            payload={}, idempotency_key="user:1:onboarding:day-1", available_at=due)
        db.commit()
    envelope = reservations(store)[0]
    original = dict(envelope.after_state)
    expiry = datetime.fromisoformat(envelope.after_state["expires_at"])
    assert item.available_at == due and expiry == due+timedelta(hours=24)
    assert expiry > item.available_at and envelope.after_state["funding_day"] == NOW.date().isoformat()
    monkeypatch.setattr(budget, "utcnow", lambda: due+timedelta(seconds=1))
    def provider():
        calls.append("controlled-provider")
        return budget.ProviderResult("sent", RECEIPT, 200)
    assert budget.guarded_send(item.idempotency_key, provider, category="lifecycle", session_factory=store.session).outcome == "sent"
    assert calls == ["controlled-provider"] and len(reservations(store)) == 1
    assert reservations(store)[0].after_state == original


@pytest.mark.parametrize("late", [timedelta(0), timedelta(seconds=1)])
def test_scheduled_onboarding_at_original_expiry_has_no_attempt_or_refund(monkeypatch, late):
    store = Store()
    due = NOW+timedelta(hours=24)
    monkeypatch.setenv("LIFECYCLE_EMAILS_ENABLED", "true")
    with store.session() as db:
        item = service.enqueue_notification(db, user_id=1, notification_type="onboarding_reminder", recipient=store.user.email,
            payload={}, idempotency_key="scheduled-expired", available_at=due)
        db.commit()
    original = dict(reservations(store)[0].after_state)
    monkeypatch.setattr(budget, "utcnow", lambda: due+timedelta(hours=24)+late)
    with pytest.raises(budget.MailBudgetError) as error:
        budget.guarded_send(item.idempotency_key, lambda: pytest.fail("Expired schedule cannot send"),
            category="lifecycle", session_factory=store.session)
    assert error.value.mail_outcome == "permanent"
    assert [row.action for row in store.rows.values()] == [budget.RESERVATION]
    assert reservations(store)[0].after_state == original


@pytest.mark.parametrize("due", [NOW+timedelta(hours=24, microseconds=1), NOW.replace(tzinfo=None), "tomorrow"])
def test_unbounded_or_unzoned_optional_schedule_is_not_accepted_or_funded(monkeypatch, due):
    store = Store()
    monkeypatch.setenv("LIFECYCLE_EMAILS_ENABLED", "true")
    with store.session() as db:
        assert service.enqueue_notification(db, user_id=1, notification_type="onboarding_reminder", recipient=store.user.email,
            payload={}, idempotency_key="invalid-schedule", available_at=due) is None
        assert db.new == [] and not store.outbox and not store.rows and not store.trace


def test_existing_day_one_envelope_never_gains_a_later_expiry_or_fresh_funding(monkeypatch):
    store = Store()
    key, due = "accepted-before-fix", NOW+timedelta(hours=24)
    monkeypatch.setenv("LIFECYCLE_EMAILS_ENABLED", "true")
    with store.session() as db:
        prior = budget.reserve_messages(db, [key], category="lifecycle")[0]
        db.commit()
        assert prior.expires_at == due
        assert service.enqueue_notification(db, user_id=1, notification_type="onboarding_reminder", recipient=store.user.email,
            payload={}, idempotency_key=key, available_at=due) is None
        db.commit()
    assert len(reservations(store)) == 1 and not store.outbox
    assert datetime.fromisoformat(reservations(store)[0].after_state["expires_at"]) == prior.expires_at
    monkeypatch.setattr(budget, "utcnow", lambda: due+timedelta(seconds=1))
    with pytest.raises(budget.MailBudgetError):
        budget.guarded_send(key, lambda: pytest.fail("An old expired envelope cannot be reauthorized"),
            category="lifecycle", session_factory=store.session)


def test_existing_accepted_day_one_outbox_is_preserved_even_when_original_window_has_expired(monkeypatch):
    store = Store()
    key, due = "already-accepted-before-fix", NOW+timedelta(hours=24)
    old = NotificationOutbox(id="original-accepted", notification_type="onboarding_reminder", recipient=store.user.email,
        idempotency_key=key, payload={"synthetic": "original obligation"}, status="pending", available_at=due)
    store.outbox.append(old)
    monkeypatch.setenv("LIFECYCLE_EMAILS_ENABLED", "true")
    with store.session() as db:
        original = budget.reserve_messages(db, [key], category="lifecycle")[0]
        db.commit()
        assert service.enqueue_notification(db, user_id=1, notification_type="onboarding_reminder", recipient=store.user.email,
            payload={}, idempotency_key=key, available_at=due) is old
        assert store.outbox == [old] and old.payload == {"synthetic": "original obligation"}
        assert len(reservations(store)) == 1
        assert datetime.fromisoformat(reservations(store)[0].after_state["expires_at"]) == original.expires_at


def test_final_send_defers_unfunded_legacy_work_but_uses_original_accepted_envelope():
    store, calls = Store(), []
    with store.session() as db:
        for start, size in ((0, 10), (10, 10), (20, 10), (30, 3)):
            budget.reserve_messages(db, [f"funded-{i}" for i in range(start, start+size)], category="lifecycle")
        db.commit()

    def provider():
        calls.append("provider")
        return budget.ProviderResult("sent", RECEIPT, 200)

    with pytest.raises(budget.MailBudgetError) as error:
        budget.guarded_send("accepted-legacy-unfunded", provider, category="lifecycle", session_factory=store.session)
    assert error.value.mail_outcome == "deferred" and not calls
    assert budget.guarded_send("funded-0", provider, category="lifecycle", session_factory=store.session).outcome == "sent"
    assert calls == ["provider"] and len(reservations(store)) == 33


def test_rejected_reset_pair_does_not_partially_reserve_remaining_daily_capacity():
    store = Store()
    with store.session() as db:
        for start in (0, 10, 20):
            budget.reserve_messages(db, [f"funded-{i}" for i in range(start, start+10)], category="lifecycle")
        budget.reserve_messages(db, ["funded-30", "funded-31"], category="lifecycle")
        db.commit()
        with pytest.raises(budget.MailBudgetError) as error:
            budget.reserve_messages(db, ["new-reset", "new-changed"], category="password_reset")
        assert error.value.mail_outcome == "deferred" and db.new == []
    assert sum(row.after_state["units"] for row in reservations(store)) == 96


@pytest.mark.parametrize("privacy", [False, True])
def test_monthly_cap_charges_attempt_units_and_privacy_intents_separately(privacy):
    store = Store()
    category, total, per_day = ("privacy_erasure", 300, 20) if privacy else ("lifecycle", 333, 33)
    with store.session() as db:
        for index in range(total):
            stamp = NOW.replace(day=1) + timedelta(days=index//per_day)
            budget.reserve_messages(db, [f"synthetic-month-{index}"], category=category, now=stamp)
        db.commit()
        stamp = NOW.replace(day=1) + timedelta(days=16)
        with pytest.raises(budget.MailBudgetError) as error:
            budget.reserve_messages(db, ["one-too-many"], category=category, now=stamp)
        assert error.value.mail_outcome == "deferred"
    assert len(reservations(store)) == total
    assert sum(row.after_state["units"] for row in reservations(store)) == total*3


def test_privacy_daily_subset_cannot_use_remaining_shared_capacity():
    store = Store()
    with store.session() as db:
        for start in (0, 10):
            budget.reserve_messages(db, [f"privacy-{index}" for index in range(start, start+10)], category="privacy_erasure")
        db.commit()
        with pytest.raises(budget.MailBudgetError) as error:
            budget.reserve_messages(db, ["privacy-extra"], category="privacy_erasure")
        assert error.value.mail_outcome == "deferred"
        budget.reserve_messages(db, ["lifecycle-remaining"], category="lifecycle")
        db.commit()
    assert len(reservations(store)) == 21


def test_calendar_boundary_keeps_original_funding_and_expiry_without_refund(monkeypatch):
    store, key = Store(), "cross-month-original-message"
    original = NOW.replace(hour=23, minute=59)
    with store.session() as db:
        first = budget.reserve_messages(db, [key], category="lifecycle", now=original)[0]
        db.commit()
    monkeypatch.setattr(budget, "utcnow", lambda: original+timedelta(minutes=2))
    assert budget.guarded_send(key, lambda: budget.ProviderResult("sent", RECEIPT, 200),
        category="lifecycle", session_factory=store.session).outcome == "sent"
    assert len(reservations(store)) == 1
    row = reservations(store)[0]
    assert row.after_state["funding_month"] == "2026-10" and row.after_state["funding_day"] == "2026-10-31"
    assert row.actor_user_id is None and row.after_state["units"] == 3
    assert first.expires_at == original+timedelta(hours=24)


@pytest.mark.parametrize("explicit_time", [False, True])
def test_default_clock_is_refreshed_after_advisory_lock_crosses_funding_boundary(monkeypatch, explicit_time):
    store = Store()
    before = NOW.replace(hour=23, minute=59, second=59)
    after = before+timedelta(seconds=2)
    clock = [before]
    original_execute = Port.execute
    if not explicit_time:
        with store.session() as db:
            for start, size in ((0, 10), (10, 10), (20, 10), (30, 3)):
                budget.reserve_messages(db, [f"prior-period-{i}" for i in range(start, start+size)],
                    category="lifecycle", now=before)
            db.commit()

    def acquire_after_boundary(self, statement, params=None):
        original_execute(self, statement, params)
        clock[0] = after

    monkeypatch.setattr(Port, "execute", acquire_after_boundary)
    monkeypatch.setattr(budget, "utcnow", lambda: clock[0])
    with store.session() as db:
        reserved = budget.reserve_messages(db, ["lock-boundary"], category="lifecycle",
            **({"now": before} if explicit_time else {}))[0]
        db.commit()
    funded = before if explicit_time else after
    row = next(row for row in reservations(store) if row.target_id == reserved.message_sha256)
    assert row.created_at == funded
    assert row.after_state["funding_day"] == funded.date().isoformat()
    assert row.after_state["funding_month"] == funded.strftime("%Y-%m")
    assert reserved.expires_at == funded+timedelta(hours=24)


def test_lock_wait_reaching_explicit_expiry_refuses_new_reservation(monkeypatch):
    store, clock = Store(), [NOW]
    deadline = NOW+timedelta(seconds=1)
    original_execute = Port.execute

    def delayed_lock(self, statement, params=None):
        original_execute(self, statement, params)
        clock[0] = deadline

    monkeypatch.setattr(Port, "execute", delayed_lock)
    monkeypatch.setattr(budget, "utcnow", lambda: clock[0])
    with store.session() as db:
        with pytest.raises(budget.MailBudgetError) as error:
            budget.reserve_messages(db, ["expired-lock-wait"], category="privacy_erasure", expires_at=deadline)
        assert error.value.mail_outcome == "permanent" and db.new == [] and not store.rows


@pytest.mark.parametrize("late", [timedelta(0), timedelta(seconds=1)])
def test_commit_delay_reaching_original_expiry_never_calls_provider_or_fabricates_outcome(monkeypatch, late):
    store, clock = Store(), [NOW]
    original_commit = Port.commit

    def delayed_commit(self):
        original_commit(self)
        clock[0] = NOW+timedelta(minutes=10)+late

    monkeypatch.setattr(Port, "commit", delayed_commit)
    monkeypatch.setattr(budget, "utcnow", lambda: clock[0])
    for _ in range(2):
        with pytest.raises(budget.MailBudgetError) as error:
            budget.guarded_send("expired-after-commit", lambda: pytest.fail("Expired attempt cannot call provider"),
                category="privacy_erasure", session_factory=store.session)
        assert error.value.mail_outcome == "unknown"
    assert [row.action for row in store.rows.values()] == [budget.RESERVATION, budget.BEGIN]
    assert reservations(store)[0].after_state["expires_at"] == (NOW+timedelta(minutes=10)).isoformat()


def test_attempt_begin_is_committed_and_sessions_closed_before_callback_and_sent_replay_is_read_only():
    store, calls = Store(), []

    def provider():
        assert store.active == 0 and store.commits == 1
        assert [row.action for row in store.rows.values()] == [budget.RESERVATION, budget.BEGIN]
        calls.append("provider")
        return budget.ProviderResult("sent", RECEIPT, 200)

    first = budget.guarded_send("synthetic-send", provider, category="lifecycle", session_factory=store.session)
    assert first.outcome == "sent" and store.active == 0
    before = dict(store.rows)
    assert budget.guarded_send("synthetic-send", provider, category="lifecycle", session_factory=store.session) == first
    assert calls == ["provider"] and store.rows == before


def test_known_retryable_envelope_allows_only_three_explicit_attempts():
    store, calls = Store(), []
    for index in range(3):
        def provider():
            assert store.active == 0
            calls.append("provider")
            return budget.ProviderResult("known_retryable", RECEIPT, 429)
        with pytest.raises(budget.MailBudgetError) as error:
            budget.guarded_send("known-retry", provider, category="lifecycle", session_factory=store.session)
        assert error.value.mail_outcome == "known_retryable" and len(calls) == index+1
    with pytest.raises(budget.MailBudgetError) as error:
        budget.guarded_send("known-retry", provider, category="lifecycle", session_factory=store.session)
    assert error.value.mail_outcome == "permanent" and len(calls) == 3
    assert len([row for row in store.rows.values() if row.action == budget.BEGIN]) == 3
    assert sum(row.after_state["units"] for row in reservations(store)) == 3


@pytest.mark.parametrize("failure", ["exception", "unknown_receipt", "begin_commit_unknown"])
def test_unknown_or_missing_outcome_never_replays_provider(failure):
    store, calls = Store(), []
    if failure == "begin_commit_unknown":
        store.fail_commit = 1

    def provider():
        calls.append("provider")
        if failure == "exception":
            raise TimeoutError("Synthetic provider outcome unknown")
        return budget.ProviderResult("unknown", RECEIPT, 503)

    with pytest.raises(budget.MailBudgetError) as error:
        budget.guarded_send("unknown-message", provider, category="lifecycle", session_factory=store.session)
    assert error.value.mail_outcome == "unknown"
    store.fail_commit = None
    with pytest.raises(budget.MailBudgetError) as error:
        budget.guarded_send("unknown-message", provider, category="lifecycle", session_factory=store.session)
    assert error.value.mail_outcome == "unknown"
    assert len(calls) == (0 if failure == "begin_commit_unknown" else 1)


def test_inflight_attempt_blocks_a_competing_send_without_callback():
    store, calls = Store(), []

    def provider():
        calls.append("provider")
        with pytest.raises(budget.MailBudgetError) as error:
            budget.guarded_send("inflight-message", provider, category="lifecycle", session_factory=store.session)
        assert error.value.mail_outcome == "unknown"
        return budget.ProviderResult("sent", RECEIPT, 200)

    budget.guarded_send("inflight-message", provider, category="lifecycle", session_factory=store.session)
    assert calls == ["provider"]


def test_unknown_outcome_commit_rejoins_exact_sent_receipt_without_another_provider_call():
    store, calls = Store(), []
    store.fail_commit = 2

    def provider():
        calls.append("provider")
        return budget.ProviderResult("sent", RECEIPT, 200)

    with pytest.raises(budget.MailBudgetError) as error:
        budget.guarded_send("outcome-commit", provider, category="lifecycle", session_factory=store.session)
    assert error.value.mail_outcome == "unknown" and calls == ["provider"]
    store.fail_commit = None
    assert budget.guarded_send("outcome-commit", provider, category="lifecycle", session_factory=store.session).outcome == "sent"
    assert calls == ["provider"]


def test_wrong_category_and_non_postgres_port_never_authorize_provider(monkeypatch):
    store = Store()
    with store.session() as db:
        budget.reserve_messages(db, ["bound-category"], category="privacy_erasure")
        db.commit()
    with pytest.raises(budget.MailBudgetError) as error:
        budget.guarded_send("bound-category", lambda: pytest.fail("Purpose cannot change"),
            category="lifecycle", session_factory=store.session)
    assert error.value.mail_outcome == "unknown"
    monkeypatch.setattr(Port, "get_bind", lambda self: SimpleNamespace(dialect=SimpleNamespace(name="sqlite")))
    with pytest.raises(budget.MailBudgetError) as error:
        budget.guarded_send("new-message", lambda: pytest.fail("Non-PG cannot send"),
            category="lifecycle", session_factory=store.session)
    assert error.value.mail_outcome == "deferred"


def test_original_expiry_cannot_be_extended_or_retried_after_it(monkeypatch):
    store = Store()
    with store.session() as db:
        original = budget.reserve_messages(db, ["expiry"], category="password_reset", expires_at=NOW+timedelta(minutes=1))[0]
        db.commit()
        second = budget.reserve_messages(db, ["expiry"], category="password_reset", expires_at=NOW+timedelta(hours=1))[0]
        assert second == original
    monkeypatch.setattr(budget, "utcnow", lambda: NOW+timedelta(minutes=2))
    with pytest.raises(budget.MailBudgetError) as error:
        budget.guarded_send("expiry", lambda: pytest.fail("Expired envelope must not call provider"),
            category="password_reset", session_factory=store.session)
    assert error.value.mail_outcome == "permanent"


def test_reset_reserves_both_messages_and_legacy_changed_obligation_survives_quota(monkeypatch):
    store = Store()
    with store.session() as db:
        reset_mail.enqueue(db, user_id=1, recipient=store.user.email, reset_id="synthetic-reset",
            token="T"*43, expires_at=NOW+timedelta(minutes=15))
        db.commit()
        assert len(reservations(store)) == 2 and len(store.outbox) == 1
        existing = reset_mail.enqueue_changed(db, user_id=1, recipient=store.user.email, reset_id="synthetic-reset")
        assert existing.notification_type == "password_changed"
        assert len(reservations(store)) == 2
    legacy = Store()

    def quota(*args, **kwargs):
        raise budget.MailBudgetError("deferred")

    monkeypatch.setattr(budget, "reserve_messages", quota)
    with legacy.session() as db:
        item = reset_mail.enqueue_changed(db, user_id=1, recipient=legacy.user.email, reset_id="accepted-old-reset")
        assert item.status == "pending" and item.payload == {"reset_id": "accepted-old-reset"}
        assert not legacy.rows


def test_reset_purpose_and_new_privacy_message_are_not_interchangeable(monkeypatch):
    store = Store()
    item = NotificationOutbox(id="synthetic-mail", user_id=1, notification_type="password_reset", status="pending",
        recipient=store.user.email, payload={"reset_id": "privacy-proof"}, available_at=NOW, attempt_count=0)
    store.outbox.append(item)
    store.proof = SimpleNamespace(purpose="privacy_erasure")
    monkeypatch.setattr(reset_mail, "configured", lambda: True)
    monkeypatch.setattr(service, "_send", lambda item: pytest.fail("Wrong-purpose proof must not send"))
    with store.session() as db:
        assert reset_mail.deliver(db, item.id) is False
    assert item.status == "cancelled" and item.payload == {}
    with pytest.raises(ValueError):
        reset_mail.message(NotificationOutbox(notification_type="privacy_erasure", payload={}))
    monkeypatch.setattr(privacy_mail, "message", lambda item: ("Explicit privacy purpose", "synthetic privacy text"))
    assert service._message(NotificationOutbox(notification_type="privacy_erasure", payload={})) == (
        "Explicit privacy purpose", "synthetic privacy text")


def test_send_failure_classification_preserves_deferred_and_unknown_payload():
    for outcome, expected, attempts in (("deferred", "pending", 0), ("unknown", "mail_unknown", 1),
                                       ("known_retryable", "pending", 1), ("permanent", "failed", 1)):
        item = NotificationOutbox(payload={"ciphertext": "synthetic retained"}, attempt_count=1)
        assert service._send_failure(item, budget.MailBudgetError(outcome)) == expected
        assert item.attempt_count == attempts and item.payload == {"ciphertext": "synthetic retained"}


def test_worker_never_rewinds_stale_send_and_explicitly_excludes_privacy_from_generic_lane(monkeypatch):
    store = Store()
    monkeypatch.setenv("RESEND_API_KEY", "synthetic-provider-key")
    monkeypatch.setenv("EMAIL_FROM", "synthetic-sender@example.invalid")
    monkeypatch.setenv("LIFECYCLE_EMAILS_ENABLED", "true")
    privacy_calls = []
    monkeypatch.setattr(privacy_mail, "deliver_pending", lambda db, **kwargs: privacy_calls.append(kwargs) or 0)
    with store.session() as db:
        summary = service.deliver_pending_notifications(db, limit=7)
    assert privacy_calls == [{"limit": 7}] and summary.sent == 0
    updates = [entry[1] for entry in store.trace if isinstance(entry, tuple) and entry[0] == "update"]
    assert len(updates) == 2
    assert all(values[NotificationOutbox.status] == "mail_unknown" for values in updates)
    selectors = [str(condition.compile(compile_kwargs={"literal_binds": True}))
        for entry in store.trace if isinstance(entry, tuple) and entry[0] == "filter" for condition in entry[1]]
    assert any("NOT IN ('password_reset', 'password_changed', 'privacy_erasure')" in value for value in selectors)


def test_central_send_wraps_actual_provider_callback_with_original_key_and_no_open_sql_session(monkeypatch):
    store, calls = Store(), []
    original = budget.guarded_send
    monkeypatch.setenv("RESEND_API_KEY", "synthetic-provider-key")
    monkeypatch.setenv("EMAIL_FROM", "synthetic-sender@example.invalid")
    monkeypatch.setattr(budget, "guarded_send", lambda key, callback, **kwargs:
        original(key, callback, session_factory=store.session, **kwargs))

    class Client:
        def __init__(self, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def post(self, url, *, headers, json):
            assert store.active == 0
            calls.append((url, headers, json))
            return httpx.Response(200, content=b'{"id":"synthetic-provider-receipt"}')

    monkeypatch.setattr(service.httpx, "Client", Client)
    item = NotificationOutbox(notification_type="welcome", recipient=store.user.email, payload={}, idempotency_key="original-provider-key")
    service._send(item)
    service._send(item)
    assert len(calls) == 1 and calls[0][1]["Idempotency-Key"] == "original-provider-key"
    assert calls[0][0] == "https://api.resend.com/emails"
    serialized = json.dumps([row.after_state for row in store.rows.values()])
    assert store.user.email not in serialized and "original-provider-key" not in serialized and "synthetic-provider-key" not in serialized
