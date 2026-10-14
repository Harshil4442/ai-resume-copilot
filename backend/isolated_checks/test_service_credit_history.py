"""Run directly with unittest; imports no application registry or test conftest."""
from __future__ import annotations

import importlib.util
import sys
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path

from fastapi import HTTPException
from sqlalchemy import Column, DateTime, Integer, String, create_engine, event
from sqlalchemy.orm import Session, declarative_base

MODULE_PATH = Path(__file__).parents[1] / "app/domains/employer/credit_history.py"
SPEC = importlib.util.spec_from_file_location("isolated_service_credit_history", MODULE_PATH)
credit_history = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = credit_history
SPEC.loader.exec_module(credit_history)

Base = declarative_base()


class Reservation(Base):
    __tablename__ = "reservation_fixture"
    id = Column(String, primary_key=True)
    user_id = Column(Integer, nullable=False)
    operation = Column(String, nullable=False)
    source_id = Column(String, nullable=False)
    unit_price = Column(Integer, nullable=False)
    requested_count = Column(Integer, nullable=False)
    reserved_amount = Column(Integer, nullable=False)
    committed_amount = Column(Integer, nullable=False)
    released_amount = Column(Integer, nullable=False)
    state = Column(String, nullable=False)
    pricing_version = Column(String, nullable=False)
    created_at = Column(DateTime, nullable=False)
    settled_at = Column(DateTime)


class CreditEvent(Base):
    __tablename__ = "event_fixture"
    id = Column(String, primary_key=True)
    user_id = Column(Integer, nullable=False)
    event_type = Column(String, nullable=False)
    amount = Column(Integer, nullable=False)
    balance_after = Column(Integer, nullable=False)
    source_type = Column(String, nullable=False)
    source_id = Column(String, nullable=False)
    reason = Column(String, nullable=False)
    created_at = Column(DateTime, nullable=False)


class ServiceCreditHistoryTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://")
        Base.metadata.create_all(self.engine)
        self.db = Session(self.engine)
        self.when = datetime(2026, 10, 14, 12, 30, tzinfo=UTC)

    def tearDown(self):
        self.db.close()
        self.engine.dispose()

    def add_reservation(self, identity, *, owner=7, operation="job_search", reserved=100,
                        committed=0, released=0, state="reserved", when=None):
        self.db.add(Reservation(
            id=identity, user_id=owner, operation=operation, source_id="source_" + identity,
            unit_price=20 if operation == "job_application" else 2, requested_count=50,
            reserved_amount=reserved, committed_amount=committed, released_amount=released,
            state=state, pricing_version="accepted-price-v1", created_at=when or self.when,
            settled_at=self.when + timedelta(minutes=1) if state == "settled" else None,
        ))

    def read(self, **kwargs):
        return credit_history.history(self.db, 7, balance=250, reservation_model=Reservation,
                                      event_model=CreditEvent, **kwargs)

    def test_settlement_uses_reservation_amounts_not_zero_commit_delta(self):
        self.add_reservation("resv_settled", committed=80, released=20, state="settled")
        self.db.add(CreditEvent(
            id="event_commit", user_id=7, event_type="commit", amount=0, balance_after=250,
            source_type="job_search", source_id="source_resv_settled", reason="Delivered 40 jobs",
            created_at=self.when,
        ))
        self.db.commit()
        result = self.read()
        row = result["operations"][0]
        self.assertEqual((row["unit_price"], row["requested_count"], row["pricing_version"]),
                         (2, 50, "accepted-price-v1"))
        self.assertEqual((row["reserved_amount"], row["held_amount"], row["committed_amount"],
                          row["released_amount"]), (100, 0, 80, 20))
        self.assertEqual(result["items"][0]["amount"], 0)
        self.assertEqual(row["created_at"], "2026-10-14T12:30:00+00:00")
        self.assertEqual(row["settled_at"], "2026-10-14T12:31:00+00:00")
        credit_history.ServiceCreditHistory.model_validate(result)

    def test_owner_scope_covers_rows_events_and_total_held_across_pages(self):
        self.add_reservation("resv_a", reserved=40)
        self.add_reservation("resv_b", operation="job_application", reserved=20)
        self.add_reservation("resv_foreign", owner=8, reserved=900)
        self.db.add(CreditEvent(id="foreign_event", user_id=8, event_type="release", amount=900,
                               balance_after=900, source_type="job_search", source_id="foreign",
                               reason="Foreign operation", created_at=self.when))
        self.db.commit()
        first = self.read(limit=1)
        self.assertEqual(first["balance"], 250)
        self.assertEqual(first["reserved_credits"], 60)
        self.assertEqual(first["items"], [])
        self.assertEqual({row["id"] for row in first["reservations"]}, {"resv_a", "resv_b"})
        self.assertEqual([row["id"] for row in first["operations"]], ["resv_b"])
        second = self.read(limit=1, cursor=first["next_cursor"])
        self.assertEqual([row["id"] for row in second["operations"]], ["resv_a"])
        self.assertIsNone(second["next_cursor"])
        self.assertEqual(second["reserved_credits"], 60)

    def test_filter_and_immutable_creation_cursor_survive_new_insertion(self):
        for identity in ("resv_a", "resv_b", "resv_c"):
            self.add_reservation(identity)
        self.add_reservation("resv_application", operation="job_application")
        self.db.commit()
        first = self.read(limit=2, operation="job_search")
        self.assertEqual([row["id"] for row in first["operations"]], ["resv_c", "resv_b"])
        self.add_reservation("resv_new", when=self.when + timedelta(minutes=2))
        self.db.commit()
        second = self.read(limit=2, cursor=first["next_cursor"], operation="job_search")
        self.assertEqual([row["id"] for row in second["operations"]], ["resv_a"])
        self.assertIsNone(second["next_cursor"])
        applications = self.read(operation="job_application")
        self.assertEqual([row["id"] for row in applications["operations"]], ["resv_application"])

    def test_foreign_missing_or_wrong_filter_cursor_is_same_private_error(self):
        self.add_reservation("resv_foreign", owner=8)
        self.add_reservation("resv_application", operation="job_application")
        self.db.commit()
        errors = []
        for cursor in ("resv_foreign", "missing", "resv_application"):
            with self.assertRaises(HTTPException) as raised:
                self.read(cursor=cursor, operation="job_search")
            errors.append((raised.exception.status_code, raised.exception.detail,
                           raised.exception.headers))
        self.assertEqual(errors[0], errors[1])
        self.assertEqual(errors[1], errors[2])
        self.assertEqual(errors[0][0], 400)
        self.assertEqual(errors[0][2], {"Cache-Control": "private, no-store"})

    def test_query_is_read_only_and_size_is_bounded(self):
        self.add_reservation("resv_a")
        self.db.commit()
        statements = []
        def listener(_conn, _cursor, statement, _params, _ctx, _many):
            statements.append(statement)
        event.listen(self.engine, "before_cursor_execute", listener)
        try:
            self.read(limit=1)
        finally:
            event.remove(self.engine, "before_cursor_execute", listener)
        self.assertTrue(all(statement.lstrip().startswith("SELECT") for statement in statements))
        for limit in (0, 201, True):
            with self.assertRaises(HTTPException):
                self.read(limit=limit)
        with self.assertRaises(HTTPException):
            self.read(operation="analysis")


if __name__ == "__main__":
    unittest.main(verbosity=2)
