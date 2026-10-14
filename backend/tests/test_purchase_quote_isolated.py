"""Run ordinary purchase bodies without aggregate models, conftest or a provider."""
from __future__ import annotations

import ast
import importlib.util
import sys
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

from fastapi import HTTPException
from pydantic import ValidationError

BACKEND = Path(__file__).resolve().parents[1]
ROUTE = BACKEND / "app" / "routers" / "billing.py"
spec = importlib.util.spec_from_file_location("isolated_purchase_quote", BACKEND / "app" / "billing" / "purchase_quote.py")
assert spec is not None and spec.loader is not None
quote = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = quote
spec.loader.exec_module(quote)


def production_body(name, dependencies):
    node = next(item for item in ast.parse(ROUTE.read_text()).body
                if isinstance(item, ast.FunctionDef) and item.name == name)
    node.decorator_list, node.returns = [], None
    if name == "create_order":
        node.args.defaults = []
    for arg in node.args.args + node.args.kwonlyargs:
        arg.annotation = None
    node.args.kw_defaults = [None for _ in node.args.kwonlyargs]
    exec(compile(ast.fix_missing_locations(ast.Module(body=[node], type_ignores=[])), str(ROUTE), "exec"), dependencies)
    return dependencies[name]


class Field:
    def __init__(self, name):
        self.name = name

    def __eq__(self, other):
        return (self.name, "eq", other)

    def in_(self, other):
        return (self.name, "in", other)

    def desc(self):
        return self


class ModelMeta(type):
    def __getattr__(cls, field):
        return Field(field)


class Order(metaclass=ModelMeta):
    def __init__(self, **values):
        self.__dict__.update(values)
        self.provider_order_id = None
        self.created_at = datetime.now(UTC)


class User(metaclass=ModelMeta):
    pass


class Query:
    def __init__(self, db, model):
        self.db, self.model = db, model
        self.conditions = []

    def filter(self, *args):
        self.conditions.extend(args)
        return self

    def with_for_update(self):
        return self

    def order_by(self, *args):
        return self

    def first(self):
        rows = [self.db.owner] if self.model is User else self.all()
        return rows[0] if rows else None

    def all(self):
        def condition_matches(row, condition):
            if len(condition) == 2:
                kind, values = condition
                outcomes = [condition_matches(row, item) for item in values]
                return all(outcomes) if kind == "and" else any(outcomes)
            name, operation, value = condition
            return getattr(row, name, None) == value if operation == "eq" else getattr(row, name, None) in value

        def matches(row):
            return all(condition_matches(row, item) for item in self.conditions)
        return [row for row in self.db.orders if matches(row)]


class Storage:
    def __init__(self, orders=None):
        self.owner = SimpleNamespace(id=1, email="synthetic@example.test")
        self.orders = list(orders or [])
        self.writes, self.commits, self.provider_calls = [], 0, []

    def query(self, model):
        return Query(self, model)

    def add(self, value):
        self.writes.append(value)
        if value not in self.orders:
            self.orders.append(value)

    def commit(self):
        self.commits += 1

    def refresh(self, value):
        pass


def setup(db, *, product=None, version="catalog-original", mode="test", key_id="synthetic-key", provider_failure=False):
    product = product or SimpleNamespace(sku="starter_bundle", amount_minor=64_900, currency="INR",
        billing_type="one_time", entitlement_kind="credit_bundle", entitlement_quantity=100, analysis_units=2)
    settings = SimpleNamespace(checkout_enabled=True, mode=mode, key_id=key_id)
    provider_error = type("RazorpayProviderError", (Exception,), {})

    class Provider:
        def __init__(self, current):
            pass

        def create_order(self, **values):
            assert db.commits >= 1
            assert db.orders[-1].public_id == quote.purchase_request_id(db.owner.id, db.request_key)
            db.provider_calls.append(values)
            if provider_failure:
                raise provider_error("synthetic transport uncertainty")
            return {"provider_order_id": "synthetic-provider-order"}

    dependencies = {
        "HTTPException": HTTPException, "PaymentOrder": Order, "User": User, "_no_store": lambda value: None,
        "get_product": lambda sku: product, "_settings": lambda: settings,
        "product_purchase_enabled": lambda value, **options: True,
        "order_quote": quote.order_quote, "product_quote": quote.product_quote,
        "validate_purchase_quote": quote.validate_purchase_quote, "CATALOG_VERSION": version,
        "purchase_request_id": quote.purchase_request_id,
        "UNCERTAIN_ORDER_STATUSES": {"initializing", "creation_unknown", "create_failed"},
        "OPEN_ORDER_STATUSES": {"initializing", "creation_unknown", "create_failed", "created", "client_confirmed", "payment_failed"},
        "_utcnow": lambda: datetime.now(UTC),
        "and_": lambda *conditions: ("and", conditions), "or_": lambda *conditions: ("or", conditions),
        "as_aware": lambda value: value, "service_prices": lambda: {"cost_policy_snapshot": {"policy": "synthetic"}},
        "assert_actual_variance": lambda *args: None, "current_policy": lambda: None,
        "CostPolicyUnavailable": type("CostPolicyUnavailable", (Exception,), {}),
        "IntegrityError": type("IntegrityError", (Exception,), {}),
        "RazorpayProviderError": provider_error,
        "RazorpayAdapter": Provider,
        "log": SimpleNamespace(warning=lambda *args: None),
    }
    production_body("_checkout_response", dependencies)
    production_body("_order_status_required", dependencies)
    production_body("_normalized_order_status", dependencies)
    return production_body("create_order", dependencies), product


def create(create_order, db, accepted, *, key="synthetic-original-request", reference=None, sku="starter_bundle"):
    db.request_key = key
    return create_order(payload=SimpleNamespace(sku=sku, billing_country="IN", accepted_quote=accepted,
        idempotency_key=key, order_reference=reference),
        request=None, response=None, db=db, current_user=db.owner)


class PurchaseAcceptanceTest(unittest.TestCase):
    def test_missing_and_each_stale_quote_refuse_before_any_order_or_provider_write(self):
        original = dict(catalog_version="catalog-original", amount_minor=64_900, currency="INR",
                        job_service_credits=100, analysis_units=2)
        for changed in (None, {"catalog_version": "old"}, {"amount_minor": 64_800},
                        {"job_service_credits": 101}, {"analysis_units": 3}):
            with self.subTest(changed=changed):
                db = Storage()
                operation, _ = setup(db)
                accepted = quote.PurchaseQuoteAcceptance(**(original | changed)) if changed is not None else None
                with self.assertRaises(HTTPException) as error:
                    create(operation, db, accepted)
                self.assertEqual(error.exception.status_code, 409)
                self.assertEqual(error.exception.detail["code"], "billing_quote_requires_review")
                self.assertEqual((db.writes, db.commits, db.provider_calls), ([], 0, []))

    def test_fresh_acceptance_snapshots_both_entitlements_and_creates_once(self):
        db = Storage()
        operation, product = setup(db)
        accepted = quote.product_quote(product, "catalog-original")
        response = create(operation, db, accepted)
        self.assertEqual(response["accepted_quote"], accepted.model_dump())
        self.assertEqual(len(db.orders), 1)
        self.assertEqual(len(db.provider_calls), 1)
        self.assertEqual(db.orders[0].cost_policy_snapshot["entitlements"],
                         {"job_service_credits": 100, "analysis_units": 2})

    def test_lost_response_recovers_original_order_after_same_price_entitlement_change(self):
        db = Storage()
        operation, product = setup(db)
        accepted = quote.product_quote(product, "catalog-original")
        original_response = create(operation, db, accepted)
        changed = SimpleNamespace(**(vars(product) | {"entitlement_quantity": 50, "analysis_units": 1}))
        retry, _ = setup(db, product=changed, version="catalog-new")
        before = (len(db.writes), db.commits, len(db.provider_calls))
        recovered = create(retry, db, accepted)
        self.assertEqual(recovered, original_response)
        self.assertEqual((len(db.writes), db.commits, len(db.provider_calls)), before)
        self.assertIn("100 job-service credits and 2 AI analysis units", recovered["description"])
        with self.assertRaises(HTTPException) as error:
            create(retry, db, quote.product_quote(changed, "catalog-new"))
        self.assertEqual(error.exception.status_code, 409)
        self.assertEqual(len(db.provider_calls), 1)

    def test_capture_between_lost_response_and_retry_recovers_terminal_identity_without_new_order(self):
        db = Storage()
        operation, product = setup(db)
        accepted = quote.product_quote(product, "catalog-original")
        response = create(operation, db, accepted)
        db.orders[0].status = "paid"  # Another device completed the original attempt.
        before = (len(db.writes), db.commits, len(db.provider_calls))
        with self.assertRaises(HTTPException) as error:
            create(operation, db, accepted)
        self.assertEqual(error.exception.detail["code"], "billing_order_requires_status")
        self.assertEqual(error.exception.detail["order_id"], response["order_id"])
        self.assertEqual((len(db.writes), db.commits, len(db.provider_calls)), before)
        self.assertEqual(len(db.orders), 1)
        with self.assertRaises(HTTPException):
            create(operation, db, quote.PurchaseQuoteAcceptance(**(accepted.model_dump() | {"analysis_units": 3})))
        self.assertEqual((len(db.writes), db.commits, len(db.provider_calls)), before)

    def test_provider_mode_or_key_rotation_requires_original_status_without_reopening_checkout(self):
        for current in ({"mode": "live"}, {"key_id": "synthetic-rotated-key"}):
            with self.subTest(current=current):
                db = Storage()
                operation, product = setup(db)
                accepted = quote.product_quote(product, "catalog-original")
                response = create(operation, db, accepted)
                recovery, _ = setup(db, **current)
                before = (len(db.writes), db.commits, len(db.provider_calls))
                with self.assertRaises(HTTPException) as error:
                    create(recovery, db, accepted)
                self.assertEqual(error.exception.detail["code"], "billing_order_requires_status")
                self.assertEqual(error.exception.detail["order_id"], response["order_id"])
                self.assertEqual((len(db.writes), db.commits, len(db.provider_calls)), before)
    def test_currency_and_numeric_acceptance_are_strict(self):
        original = dict(catalog_version="catalog-original", amount_minor=64_900, currency="INR",
                        job_service_credits=100, analysis_units=2)
        for changed in ({"currency": "USD"}, {"amount_minor": "64900"}, {"job_service_credits": True}):
            with self.subTest(changed=changed), self.assertRaises(ValidationError):
                quote.PurchaseQuoteAcceptance(**(original | changed))

    def test_missing_request_identity_cannot_create_order(self):
        db = Storage()
        operation, product = setup(db)
        with self.assertRaises(HTTPException) as error:
            create(operation, db, quote.product_quote(product, "catalog-original"), key=None)
        self.assertEqual(error.exception.detail["code"], "billing_request_requires_review")
        self.assertEqual((db.writes, db.commits, db.provider_calls), ([], 0, []))

    def test_uncertain_provider_failure_retains_attempt_and_same_identity_does_not_replay(self):
        db = Storage()
        operation, product = setup(db, provider_failure=True)
        accepted = quote.product_quote(product, "catalog-original")
        with self.assertRaises(HTTPException) as error:
            create(operation, db, accepted)
        original = db.orders[0]
        self.assertEqual(error.exception.detail["order_id"], original.public_id)
        self.assertEqual(original.status, "creation_unknown")
        self.assertEqual(original.active_attempt_key, "1:starter_bundle:test")
        self.assertIsNone(original.provider_order_id)
        self.assertEqual(operation.__globals__["_normalized_order_status"](original), "pending")
        before = (len(db.writes), db.commits, len(db.provider_calls))
        with self.assertRaises(HTTPException):
            create(operation, db, accepted)
        self.assertEqual((len(db.writes), db.commits, len(db.provider_calls)), before)
        self.assertEqual(len(db.orders), 1)

    def test_changed_identity_pack_or_mode_cannot_replace_uncertain_provider_attempt(self):
        db = Storage()
        failed, product = setup(db, provider_failure=True)
        accepted = quote.product_quote(product, "catalog-original")
        with self.assertRaises(HTTPException):
            create(failed, db, accepted)
        before = (len(db.writes), db.commits, len(db.provider_calls))
        for changes in ({}, {"mode": "live"}, {"product": SimpleNamespace(**(vars(product) | {"sku": "growth_bundle"}))}):
            operation, selected_product = setup(db, **changes)
            with self.subTest(changes=changes), self.assertRaises(HTTPException) as error:
                create(operation, db, accepted, key="synthetic-replacement-request", sku=selected_product.sku)
            self.assertEqual(error.exception.detail["code"], "billing_order_requires_status")
            self.assertEqual(error.exception.detail["order_id"], db.orders[0].public_id)
            self.assertEqual((len(db.writes), db.commits, len(db.provider_calls)), before)

    def test_aged_unresolved_initialization_and_legacy_failure_are_never_abandoned(self):
        for state in ("initializing", "creation_unknown", "create_failed"):
            with self.subTest(state=state):
                db = Storage()
                operation, product = setup(db)
                accepted = quote.product_quote(product, "catalog-original")
                create(operation, db, accepted)
                original = db.orders[0]
                original.status, original.provider_order_id = state, None
                if state == "create_failed":
                    original.active_attempt_key = None  # Legacy failures released this key.
                original.created_at = datetime.now(UTC) - timedelta(days=7)
                original_attempt_key = original.active_attempt_key
                before = (len(db.writes), db.commits, len(db.provider_calls))
                with self.assertRaises(HTTPException) as error:
                    create(operation, db, accepted, key="synthetic-aged-replacement")
                self.assertEqual(error.exception.detail["order_id"], original.public_id)
                self.assertEqual(original.status, state)
                self.assertEqual(original.active_attempt_key, original_attempt_key)
                self.assertEqual(operation.__globals__["_normalized_order_status"](original), "pending")
                self.assertEqual((len(db.writes), db.commits, len(db.provider_calls)), before)

    def test_existing_historical_allowances_and_access_use_original_terms(self):
        for kind, quantity, credits, units, text in (
            ("job_service_credits", 500, 500, 0, "500 job-service credits"),
            ("analysis_units", 100, 0, 100, "100 AI analysis units"),
            ("premium_access", 30, 0, 0, "30 days of Premium access"),
        ):
            with self.subTest(kind=kind):
                original = Order(public_id="synthetic-original", user_id=1, sku="starter_bundle", provider_mode="test", provider_key_id="synthetic-key",
                    catalog_version="historical-catalog", gross_amount_minor=49_900, currency="INR",
                    entitlement_kind=kind, entitlement_quantity=quantity, cost_policy_snapshot=None, status="created")
                original.provider_order_id = "synthetic-original-provider-order"
                db = Storage([original])
                operation, _ = setup(db, version="catalog-new")
                with self.assertRaises(HTTPException):
                    create(operation, db, None, reference=original.public_id)
                recovered = create(operation, db, quote.order_quote(original), reference=original.public_id)
                self.assertEqual(recovered["accepted_quote"]["job_service_credits"], credits)
                self.assertEqual(recovered["accepted_quote"]["analysis_units"], units)
                self.assertIn(text, recovered["description"])
                self.assertEqual((db.writes, db.commits, db.provider_calls), ([], 0, []))


if __name__ == "__main__":
    unittest.main()
