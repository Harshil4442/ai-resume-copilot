"""Current Source27 exact-ID email retry on the owned PostgreSQL erasure fixture."""
from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session
from test_privacy_erasure_postgres import owned_erasure as owned_erasure
from test_schema25_app_compatibility_postgres import app25_database as app25_database

from app.domains.notifications import privacy_erasure as mail
from app.domains.notifications import service as notifications
from app.domains.notifications import wake
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

def test_erasure_retry_recovers_lost_queue_ack_and_delivers_before_original_expiry(owned_erasure, monkeypatch):
    from app.domains.candidate_accounts import email_erasure
    from app.domains.notifications.mail_budget import MailBudgetError
    from app.models import PasswordResetToken

    engine, token = owned_erasure
    with engine.connect() as connection, Session(connection, autoflush=False) as db:
        connection.exec_driver_sql("SET TIME ZONE 'Asia/Kolkata'")
        connection.commit()
        previous = db.query(NotificationOutbox).filter_by(notification_type=mail.TYPE).one()
        assert email_erasure.valid_token(db.get(PasswordResetToken, previous.payload["proof_id"]), token)
        db.rollback()

        clock = [datetime.now(UTC) + timedelta(seconds=61)]
        identity = email_erasure.request_erasure(db, "synthetic-erasure@example.invalid",
                                                 now=clock[0], read_native=lambda *_: "b" * 64)
        item = db.get(NotificationOutbox, identity)
        proof = db.get(PasswordResetToken, item.payload["proof_id"])
        token = mail.message(item)[1].split("#token=", 1)[1].split("\n", 1)[0]
        expiry = datetime.fromtimestamp(item.payload["expires_at_ms"] / 1000, UTC)
        assert item.available_at.utcoffset() == timedelta(hours=5, minutes=30)
        assert proof.expires_at == (clock[0] + timedelta(minutes=10)).replace(tzinfo=None)
        assert email_erasure.valid_token(proof, token)
        db.rollback()
        scheduled = []
        attempts = []

        def confirm(parent, origin, notification_id, due, deadline):
            assert not db.in_transaction()
            assert notification_id == identity and deadline == expiry
            scheduled.append(due)
            return len(scheduled) == 2

        def send(outgoing):
            assert not db.in_transaction()
            attempts.append(outgoing.idempotency_key)
            if len(attempts) == 1:
                raise MailBudgetError("known_retryable")

        monkeypatch.setenv("SECURITY_MAIL_TASK_WAKE_ENABLED", "true")
        monkeypatch.setattr(wake, "_target", lambda: ("synthetic-queue", "https://worker.example.invalid"))
        monkeypatch.setattr(wake, "_confirm", confirm)
        monkeypatch.setattr(wake, "utcnow", lambda: clock[0])
        monkeypatch.setattr(mail, "utcnow", lambda: clock[0])
        monkeypatch.setattr(notifications, "utcnow", lambda: clock[0])
        monkeypatch.setattr(notifications, "_send", send)

        retry = wake.deliver_link_with_wake(db, identity)
        assert retry == notifications.SecurityEmailRetry("retry", clock[0] + timedelta(minutes=1))
        assert scheduled == [clock[0] + timedelta(seconds=30), retry.available_at]
        clock[0] = retry.available_at + timedelta(seconds=1)
        assert clock[0] < expiry
        assert notifications.retry_security_email(db, identity, link_only=True).status == "sent"
        assert notifications.retry_security_email(db, identity, link_only=True).status == "finished"
        assert len(attempts) == 2 and attempts[0] == attempts[1]
        item = db.get(NotificationOutbox, identity)
        assert item.status == "erasure_sent" and item.payload == {} and item.attempt_count == 2


def test_password_reset_proof_uses_same_utc_binding_across_database_sessions(owned_erasure, monkeypatch):
    from app.domains.candidate_accounts import email_reset
    from app.domains.notifications import password_reset
    from app.models import PasswordResetToken, User

    engine, _ = owned_erasure
    monkeypatch.setattr(password_reset, "configured", lambda: True)
    issued = datetime.now(UTC)
    with engine.connect() as connection, Session(connection, autoflush=False) as db:
        connection.exec_driver_sql("SET TIME ZONE 'Asia/Kolkata'")
        connection.commit()
        identity = email_reset.request_reset(db, "foreign-erasure@example.invalid", now=issued)
        item = db.get(NotificationOutbox, identity)
        proof = db.get(PasswordResetToken, item.payload["reset_id"])
        token = password_reset.message(item)[1].split("#token=", 1)[1].split("\n", 1)[0]
        assert proof.expires_at == (issued + timedelta(minutes=15)).replace(tzinfo=None)
        assert email_reset._valid_token(proof, token)
    with engine.connect() as connection, Session(connection, autoflush=False) as db:
        connection.exec_driver_sql("SET TIME ZONE 'America/Los_Angeles'")
        connection.commit()
        email_reset.confirm_reset(db, token, "synthetic-verified-reset", now=issued + timedelta(seconds=30))
        assert db.get(User, 2).credential_epoch == 2
        assert db.query(PasswordResetToken).filter_by(user_id=2, purpose="password_reset").one().status == "COMPLETED"
