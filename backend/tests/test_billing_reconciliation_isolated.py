"""Production reconciliation bodies with isolated storage and GET fixtures only."""
from __future__ import annotations

import ast
import copy
import importlib.util
import sys
import unittest
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import patch

from fastapi import HTTPException
from test_purchase_quote_isolated import Field, ModelMeta, production_body

BACKEND = Path(__file__).resolve().parents[1]
package = ModuleType("isolated_reconciliation_billing")
package.__path__ = [str(BACKEND / "app/billing")]
sys.modules[package.__name__] = package
spec = importlib.util.spec_from_file_location(package.__name__ + ".reconciliation", BACKEND / "app/billing/reconciliation.py")
assert spec and spec.loader
reconciliation = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = reconciliation
spec.loader.exec_module(reconciliation)


class Record(metaclass=ModelMeta):
    def __init__(self, **values):
        self.__dict__.update(values)


class Order(Record):
    pass


class Event(Record):
    pass


class Transaction(Record):
    pass


class Refund(Record):
    pass


class User(Record):
    pass


class Ledger(Record):
    pass


Field.__ne__ = lambda self, value: (self.name, "ne", value)


class Query:
    def __init__(self, db, model):
        self.db, self.model, self.filters = db, model, []

    def filter(self, *conditions):
        self.filters.extend(conditions)
        return self

    def with_for_update(self, **options):
        self.db.locks.append((self.model, options))
        return self

    def populate_existing(self):
        return self

    def order_by(self, *fields):
        return self

    def first(self):
        return next(iter(self.all()), None)

    def all(self):
        def matches(row, item):
            if len(item) == 2:
                _, alternatives = item
                return any(matches(row, condition) for condition in alternatives)
            field, operation, value = item
            actual = getattr(row, field, None)
            return actual == value if operation == "eq" else actual != value if operation == "ne" else actual in value
        return [row for row in self.db.rows[self.model] if all(matches(row, item) for item in self.filters)]


class Storage:
    def __init__(self, order):
        self.owner = User(id=1)
        self.rows = {User: [self.owner], Order: [order], Event: [], Transaction: [], Refund: [], Ledger: []}
        self.commits, self.writes, self.grants, self.revokes, self.adjustments = 0, [], [], [], []
        self.locks = []
        self._remember()

    def _remember(self):
        self.saved = {model: [(row, copy.deepcopy(vars(row))) for row in rows] for model, rows in self.rows.items()}

    def query(self, model):
        return Query(self, model)

    def add(self, row):
        self.writes.append(row)
        if row not in self.rows[type(row)]:
            self.rows[type(row)].append(row)

    def flush(self):
        for rows in self.rows.values():
            for index, row in enumerate(rows, 1):
                if not hasattr(row, "id"):
                    row.id = index

    def commit(self):
        self.commits += 1
        self._remember()

    def rollback(self):
        self.locks.clear()
        for model, saved in self.saved.items():
            self.rows[model] = [row for row, values in saved]
            for row, values in saved:
                row.__dict__.clear()
                row.__dict__.update(copy.deepcopy(values))


def original_order(**changed):
    return Order(**(dict(id=1, public_id="ord_original", user_id=1, provider="razorpay", provider_mode="test",
        provider_key_id="synthetic-key", provider_order_id=None, sku="starter_bundle", billing_country="IN",
        catalog_version="original-catalog", gross_amount_minor=64_900, currency="INR", entitlement_kind="credit_bundle",
        entitlement_quantity=100, cost_policy_snapshot={"entitlements": {"job_service_credits": 100, "analysis_units": 2}},
        status="creation_unknown", active_attempt_key="1:starter_bundle:test", refunded_amount_minor=0,
        paid_at=None, refunded_at=None, created_at=datetime.now(UTC)) | changed))


def remote_order(**changed):
    return dict(id="order_original", entity="order", receipt="hw_ord_original", amount=64_900, currency="INR",
        amount_paid=0, amount_due=64_900, status="created", attempts=0, partial_payment=False,
        notes={"hirewiz_order_id": "ord_original", "sku": "starter_bundle", "billing_country": "IN"}) | changed


def payment(**changed):
    return dict(id="pay_original", entity="payment", order_id="order_original", amount=64_900, currency="INR",
        status="captured", captured=True, amount_refunded=0, notes={}, fee=500, tax=76,
        method="upi", international=False) | changed


def collection(*items):
    return {"entity": "collection", "count": len(items), "items": list(items)}


SETTINGS = SimpleNamespace(mode="test", key_id="synthetic-key", credentials_complete=True,
                           mode_allowed_for_environment=True)


def service_body(name, dependencies):
    source = ast.parse((BACKEND / "app/billing/service.py").read_text())
    node = next(item for item in source.body if isinstance(item, ast.FunctionDef) and item.name == name)
    node.decorator_list, node.returns = [], None
    for arg in node.args.args + node.args.kwonlyargs:
        arg.annotation = None
    exec(compile(ast.fix_missing_locations(ast.Module(body=[node], type_ignores=[])), "ordinary_billing_service", "exec"), dependencies)


def setup(db, replies, *, race=None):
    calls = []

    def read(settings, path, params, timeout):
        assert not db.locks, "Provider GET must run outside locked transactions."
        calls.append((path, params))
        value = replies[path]
        if isinstance(value, Exception):
            raise value
        return copy.deepcopy(value)

    def fetch(original, settings):
        proof = reconciliation.fetch_reconciliation(original, settings, get_json=read)
        if race:
            race()
            db._remember()  # Simulate a concurrently committed webhook.
        return proof

    def transaction(db, *, order, payment_id, status, fee, tax, **options):
        existing = next((row for row in db.rows[Transaction] if row.provider_payment_id == payment_id), None)
        if existing and existing.order_id != order.id:
            raise dependencies["WebhookValidationError"]("payment_ownership_mismatch")
        if existing is None:
            existing = Transaction(order_id=order.id, provider="razorpay", provider_payment_id=payment_id,
                status=status, captured_at=None, provider_fee_amount_minor=fee, provider_fee_tax_minor=tax,
                estimated_net_amount_minor=order.gross_amount_minor - (fee or 0), refunded_amount_minor=0)
            db.add(existing)
        return existing

    class ValidationError(ValueError):
        def __init__(self, code):
            self.code = code

    dependencies = {"HTTPException": HTTPException, "PaymentOrder": Order, "PaymentEvent": Event,
        "PaymentTransaction": Transaction, "PaymentRefund": Refund, "User": User,
        "OriginalOrder": reconciliation.OriginalOrder, "ReconciliationUnavailable": reconciliation.ReconciliationUnavailable,
        "fetch_reconciliation": fetch, "_settings": lambda: SETTINGS, "_no_store": lambda value: None,
        "_utcnow": lambda: datetime.now(UTC), "utcnow": lambda: datetime.now(UTC), "as_aware": lambda value: value,
        "UNCERTAIN_ORDER_STATUSES": {"initializing", "creation_unknown", "create_failed"},
        "OPEN_ORDER_STATUSES": {"initializing", "creation_unknown", "create_failed", "created", "client_confirmed", "payment_failed"},
        "IntegrityError": type("IntegrityError", (Exception,), {}), "WebhookValidationError": ValidationError,
        "OperationalError": type("OperationalError", (Exception,), {}),
        "PROVIDER": "razorpay", "Any": object, "_payment_transaction": transaction,
        "or_": lambda *conditions: ("or", conditions),
        "_entity": lambda payload, key: payload.get("payload", {}).get(key, {}).get("entity"),
        "_grant_entitlement": lambda db, order, now: db.grants.append((order.entitlement_quantity,
            order.cost_policy_snapshot["entitlements"]["analysis_units"])),
        "_adjust_service_credit_refund": lambda db, order, now: db.adjustments.append(order.refunded_amount_minor),
        "_revoke_entitlement": lambda db, order, now: db.revokes.append(order.public_id),
        "log": SimpleNamespace(warning=lambda *args: None),
    }
    for name in ("_required_string", "_required_minor", "_optional_minor", "_lock_order_by_provider_id", "_receipt_for",
                 "_validate_order_notes", "_validate_payment_expenses", "_validate_payment", "_validate_provider_order",
                 "_validate_refund_payment", "_process_capture", "_process_refund"):
        service_body(name, dependencies)
    dependencies.update({"EntitlementLedger": Ledger, "order_quote": reconciliation.order_quote})
    for name in ("_owned_order", "_normalized_order_status", "_order_status_payload", "_reconciliation_reply"):
        production_body(name, dependencies)
    return production_body("reconcile_order", dependencies), calls


def invoke(operation, db, reference="ord_original"):
    return operation(reference, None, None, db, db.owner)


class ReconciliationTest(unittest.TestCase):
    def test_receipt_exactly_reproduces_creation_without_normalizing_public_identity(self):
        original = reconciliation.OriginalOrder.from_order(original_order(public_id="ord_original-with-hyphen"))
        self.assertEqual(original.receipt, f"hw_{original.public_id}")
        self.assertEqual(original.receipt, "hw_ord_original-with-hyphen")

    def test_created_order_binds_only_original_and_retains_reservation_and_api_provenance(self):
        order = original_order()
        db = Storage(order)
        rejected = Event(provider="razorpay", provider_event_id="rejected_original", payload_sha256="original-digest",
                         processing_status="rejected", event_type="payment.captured")
        db.rows[Event].append(rejected)
        db._remember()
        operation, calls = setup(db, {"orders": collection(remote_order()), "orders/order_original": remote_order(),
                                    "orders/order_original/payments": collection()})
        result = invoke(operation, db)
        self.assertEqual(result["resolution"], "checkout_ready")
        self.assertTrue(result["order"]["checkout_available"])
        reconciliation.ReconciliationResponse.model_validate(result)
        self.assertEqual((order.provider_order_id, order.status, order.active_attempt_key),
                         ("order_original", "created", "1:starter_bundle:test"))
        self.assertEqual(db.grants, [])
        self.assertEqual(db.rows[Event][-1].provider, "razorpay_api")
        self.assertEqual(db.rows[Event][-1].event_type, "api.order_reconciliation")
        self.assertEqual((rejected.processing_status, rejected.payload_sha256), ("rejected", "original-digest"))
        self.assertEqual([path for path, params in calls], ["orders", "orders/order_original", "orders/order_original/payments"])
        self.assertEqual(calls[0][1]["receipt"], "hw_ord_original")
        self.assertEqual([model for model, options in db.locks[:2]], [Order, User])
        self.assertTrue(all(options == {"nowait": True} for model, options in db.locks[:2]))
        event_count = len(db.rows[Event])
        self.assertEqual(invoke(operation, db)["resolution"], "checkout_ready")
        self.assertEqual(len(db.rows[Event]), event_count)
        self.assertEqual(rejected.payload_sha256, "original-digest")

    def test_other_owner_reference_refuses_before_any_provider_read(self):
        db = Storage(original_order(user_id=2))
        operation, calls = setup(db, {})
        with self.assertRaises(HTTPException) as error:
            invoke(operation, db)
        self.assertEqual(error.exception.status_code, 404)
        self.assertEqual((calls, db.writes, db.commits), ([], [], 0))

    def test_remote_other_owner_same_price_pack_and_crossowner_bound_id_never_bind(self):
        for notes in ({"hirewiz_order_id": "ord_other", "sku": "starter_bundle", "billing_country": "IN"},
                      {"hirewiz_order_id": "ord_original", "sku": "starter_bundle", "billing_country": "IN", "hirewiz_user_id": "2"},
                      None):
            db = Storage(original_order())
            operation, _ = setup(db, {"orders": collection(remote_order(notes=notes))})
            self.assertEqual(invoke(operation, db)["resolution"], "unresolved")
            self.assertEqual((db.rows[Order][0].provider_order_id, db.writes, db.commits, db.grants), (None, [], 0, []))
        order = original_order()
        db = Storage(order)
        db.rows[Order].append(original_order(id=2, public_id="ord_other", user_id=2, provider_order_id="order_original"))
        db._remember()
        operation, _ = setup(db, {"orders": collection(remote_order()), "orders/order_original": remote_order(),
                                 "orders/order_original/payments": collection()})
        self.assertEqual(invoke(operation, db)["resolution"], "unresolved")
        self.assertIsNone(order.provider_order_id)
        self.assertEqual((db.commits, db.grants), (0, []))

    def test_empty_duplicate_truncated_error_and_changed_amount_remain_unresolved(self):
        for reply in (collection(), collection(remote_order(), remote_order()),
                      collection(*[remote_order() for _ in range(100)]),
                      ValueError("synthetic invalid response"), collection(remote_order(amount=64_800))):
            with self.subTest(reply=type(reply)):
                order = original_order()
                db = Storage(order)
                operation, calls = setup(db, {"orders": reply})
                self.assertEqual(invoke(operation, db)["resolution"], "unresolved")
                self.assertEqual((order.status, order.active_attempt_key, order.provider_order_id),
                                 ("creation_unknown", "1:starter_bundle:test", None))
                self.assertLessEqual(len(calls), 2)
                self.assertEqual((db.commits, db.grants), (0, []))

    def test_original_key_or_mode_rotation_refuses_without_a_read(self):
        for changed in ({"provider_key_id": "rotated-key"}, {"provider_mode": "live"}):
            db = Storage(original_order(**changed))
            operation, calls = setup(db, {})
            self.assertEqual(invoke(operation, db)["resolution"], "unresolved")
            self.assertEqual((calls, db.commits), ([], 0))

    def test_concurrent_captured_or_refunded_outcome_is_preserved_before_binding(self):
        for terminal in ("paid", "refunded"):
            order = original_order()
            db = Storage(order)
            def race(order=order, terminal=terminal):
                order.status, order.provider_order_id, order.active_attempt_key = terminal, "order_original", None
                order.refunded_amount_minor = 64_900 if terminal == "refunded" else 0
            operation, _ = setup(db, {"orders": collection(remote_order()), "orders/order_original": remote_order(),
                                     "orders/order_original/payments": collection()}, race=race)
            self.assertEqual(invoke(operation, db)["resolution"], "settled")
            self.assertEqual((order.status, order.active_attempt_key, db.commits, db.rows[Event], db.grants),
                             (terminal, None, 0, [], []))

    def test_attempted_order_binds_but_never_releases_or_resumes(self):
        remote = remote_order(status="attempted", attempts=1)
        failed = payment(status="failed", captured=False)
        for state in ("creation_unknown", "created"):
            order = original_order(status=state)
            db = Storage(order)
            operation, _ = setup(db, {"orders": collection(remote), "orders/order_original": remote,
                                     "orders/order_original/payments": collection(failed)})
            result = invoke(operation, db)
            self.assertEqual(result["resolution"], "unresolved")
            self.assertFalse(result["order"]["checkout_available"])
            self.assertEqual((order.status, order.active_attempt_key), ("creation_unknown", "1:starter_bundle:test"))
            self.assertEqual(db.grants, [])

    def test_concurrent_immutable_terms_change_refuses_binding(self):
        order = original_order()
        db = Storage(order)
        def race():
            order.cost_policy_snapshot["entitlements"]["analysis_units"] = 3
        operation, _ = setup(db, {"orders": collection(remote_order()), "orders/order_original": remote_order(),
                                 "orders/order_original/payments": collection()}, race=race)
        self.assertEqual(invoke(operation, db)["resolution"], "unresolved")
        self.assertIsNone(order.provider_order_id)
        self.assertEqual((order.status, db.commits, db.rows[Event]), ("creation_unknown", 0, []))

    def test_actual_capture_uses_production_settlement_and_immutable_allowances_once(self):
        remote = remote_order(status="paid", attempts=1, amount_paid=64_900, amount_due=0)
        order = original_order()
        db = Storage(order)
        operation, calls = setup(db, {"orders": collection(remote), "orders/order_original": remote,
            "orders/order_original/payments": collection(payment()), "payments/pay_original": payment()})
        self.assertEqual(invoke(operation, db)["resolution"], "settled")
        self.assertEqual((order.status, order.active_attempt_key, db.grants), ("paid", None, [(100, 2)]))
        before = len(calls)
        self.assertEqual(invoke(operation, db)["resolution"], "settled")
        self.assertEqual((len(calls), db.grants), (before, [(100, 2)]))

    def test_actual_full_refund_before_capture_never_fabricates_capture_or_grants(self):
        remote = remote_order(status="paid", attempts=1, amount_paid=64_900, amount_due=0)
        refunded = payment(status="refunded", amount_refunded=64_900)
        refund = dict(id="rfnd_original", entity="refund", payment_id="pay_original", amount=64_900,
                      currency="INR", status="processed")
        order = original_order()
        db = Storage(order)
        operation, _ = setup(db, {"orders": collection(remote), "orders/order_original": remote,
            "orders/order_original/payments": collection(refunded), "payments/pay_original": refunded,
            "payments/pay_original/refunds": collection(refund)})
        self.assertEqual(invoke(operation, db)["resolution"], "settled")
        self.assertEqual((order.status, order.active_attempt_key, db.grants, db.revokes),
                         ("refunded", None, [], ["ord_original"]))
        self.assertEqual(order.refunded_amount_minor, 64_900)

    def test_actual_partial_refund_uses_original_allowances_and_recorded_refund(self):
        remote = remote_order(status="paid", attempts=1, amount_paid=64_900, amount_due=0)
        partial = payment(amount_refunded=16_225)
        refund = dict(id="rfnd_partial", entity="refund", payment_id="pay_original", amount=16_225,
                      currency="INR", status="processed")
        order = original_order()
        db = Storage(order)
        operation, _ = setup(db, {"orders": collection(remote), "orders/order_original": remote,
            "orders/order_original/payments": collection(partial), "payments/pay_original": partial,
            "payments/pay_original/refunds": collection(refund)})
        self.assertEqual(invoke(operation, db)["resolution"], "settled")
        self.assertEqual((order.status, order.active_attempt_key, db.grants, db.adjustments),
                         ("partially_refunded", None, [(100, 2)], [16_225]))

    def test_transport_uses_get_only_and_limits_response_bytes(self):
        calls = []
        @contextmanager
        def stream(method, url, **options):
            calls.append((method, url))
            yield SimpleNamespace(raise_for_status=lambda: None,
                                  iter_bytes=lambda: iter([b"x" * (reconciliation.MAX_RESPONSE_BYTES + 1)]))
        settings = SimpleNamespace(key_id="synthetic-key", key_secret="synthetic-secret")
        with patch.object(reconciliation.httpx, "stream", stream), self.assertRaises(reconciliation.ReconciliationUnavailable):
            reconciliation._provider_json(settings, "orders", {"receipt": "hw_ord_original"}, 5)
        self.assertEqual(calls, [("GET", "https://api.razorpay.com/v1/orders")])

    def test_transport_slow_chunks_cannot_extend_the_read_budget(self):
        @contextmanager
        def stream(*args, **options):
            yield SimpleNamespace(raise_for_status=lambda: None, iter_bytes=lambda: iter([b"{} "]))
        settings = SimpleNamespace(key_id="synthetic-key", key_secret="synthetic-secret")
        with patch.object(reconciliation.httpx, "stream", stream), \
                patch.object(reconciliation.time, "monotonic", side_effect=[0, 6]), \
                self.assertRaises(reconciliation.ReconciliationUnavailable):
            reconciliation._provider_json(settings, "orders", {}, 5)


if __name__ == "__main__":
    unittest.main()
