"""Current Source27 exact-ID email retry on the owned PostgreSQL erasure fixture."""
from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session
from test_privacy_erasure_postgres import owned_erasure as owned_erasure
from test_schema25_app_compatibility_postgres import app25_database as app25_database

from app.domains.notifications import privacy_erasure as mail
from app.domains.notifications import service as notifications
from app.models import NotificationOutbox


def test_exact_erasure_email_retry_preserves_delayed_and_unknown_delivery(owned_erasure, monkeypatch):
    from app.domains.notifications.mail_budget import MailBudgetError

    engine, _ = owned_erasure
    with Session(engine, autoflush=False) as db:
        item = db.query(NotificationOutbox).filter_by(notification_type=mail.TYPE).one()
        identity = item.id
        db.rollback()
        attempts = []

        def send(outgoing):
            assert not db.in_transaction()
            attempts.append(outgoing.idempotency_key)
            raise MailBudgetError("known_retryable" if len(attempts) == 1 else "unknown")

        monkeypatch.setattr(notifications, "_send", send)
        first = notifications.retry_security_email(db, identity)
        assert first.status == "retry" and first.available_at > datetime.now(UTC)
        assert notifications.retry_security_email(db, identity) == first and len(attempts) == 1
        item = db.get(NotificationOutbox, identity)
        item.available_at = datetime.now(UTC) - timedelta(seconds=1)
        db.commit()
        assert notifications.retry_security_email(db, identity).status == "finished"
        assert notifications.retry_security_email(db, identity).status == "finished"
        assert len(attempts) == 2 and attempts[0] == attempts[1]
        item = db.get(NotificationOutbox, identity)
        assert item.status == mail.UNKNOWN and item.claimed_at is None and item.attempt_count == 2
