"""Real foreign keys retain liabilities while erasing completed discovery data."""

from backend.app.domains.credits import discovery
from backend.app.domains.credits.discovery_models import (
    CreditDiscoveryBatch,
    CreditDiscoveryDelivery,
)
from backend.app.domains.credits.models import CreditExecutionHold, CreditReservation
from backend.app.domains.credits.privacy import erase_owner_credit_links, export_account_data
from backend.app.domains.employer.service import delete_account_data
from backend.app.models import Resume, User
from backend.tests.test_unified_credits_postgres import factory
from backend.tests.test_unified_discovery import quoted
from backend.tests.test_unified_discovery_postgres import seed

assert factory


def test_completed_discovery_erasure_retains_detached_financial_obligations(factory, monkeypatch):
    seed(factory, monkeypatch)
    with factory() as db:
        accepted = discovery.create(db, user_id=1, payload=quoted(db))
        completed = discovery.execute(db, user_id=1, search_id=accepted.id)
        assert completed.delivered_count == 2 and completed.charged_credits == 1
        exported = export_account_data(db, 1)
        assert len(exported["discovery_deliveries"]) == 2
        erase_owner_credit_links(db, user_id=1)
        delete_account_data(db, 1)
        db.query(Resume).filter_by(user_id=1).delete()
        db.query(User).filter_by(id=1).delete()
        db.commit()
        assert db.query(CreditDiscoveryDelivery).count() == db.query(CreditDiscoveryBatch).count() == 0
        hold = db.query(CreditExecutionHold).one()
        assert hold.user_id is None and hold.delivery_state == "delivered"
        assert hold.native_settled_minor is None and hold.reserved_minor > 0
        reservation = db.query(CreditReservation).one()
        assert reservation.user_id is None and reservation.committed_credits == 1
