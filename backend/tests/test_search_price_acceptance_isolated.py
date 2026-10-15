"""Execute the ordinary admission bodies without aggregate models or conftest.

Run directly with the backend Python runtime. Dependency doubles record money
and durable writes; no provider, application entrypoint or SQL model is imported.
"""
from __future__ import annotations

import ast
import hashlib
import importlib.util
import json
import unittest
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

from fastapi import HTTPException

BACKEND = Path(__file__).resolve().parents[1]
EMPLOYER = BACKEND / "app" / "domains" / "employer"


def ordinary_config():
    spec = importlib.util.spec_from_file_location("isolated_employer_config", EMPLOYER / "config.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def admission_body(filename, name, dependencies):
    """Keep the production body; replace only its dependency import statements."""
    tree = ast.parse((EMPLOYER / filename).read_text())
    function = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == name)

    class DependencyImports(ast.NodeTransformer):
        def visit_ImportFrom(self, node):
            return None

    function = DependencyImports().visit(function)
    executable = ast.fix_missing_locations(ast.Module(body=[function], type_ignores=[]))
    exec(compile(executable, str(EMPLOYER / filename), "exec"), dependencies)
    return dependencies[name]


class ModelMeta(type):
    def __getattr__(cls, field):
        return f"{cls.__name__}.{field}"


class Model(metaclass=ModelMeta):
    def __init__(self, **values):
        self.__dict__.update(values)


def model(name):
    return ModelMeta(name, (Model,), {})


class Query:
    def __init__(self, fields):
        self.fields = fields

    def filter_by(self, **values):
        return self

    def populate_existing(self):
        return self

    def with_for_update(self, **options):
        return self

    def options(self, *options):
        return self

    def first(self):
        return SimpleNamespace(id=10) if self.fields == ("Resume.id",) else None

    def one(self):
        return SimpleNamespace(watermark=1)

    def scalar(self):
        return 1


class Storage:
    def __init__(self):
        self.reservations = []
        self.rows = []
        self.jobs = []
        self.commits = 0

    def query(self, *fields):
        return Query(fields)

    def add(self, row):
        self.rows.append(row)

    def flush(self):
        pass

    def commit(self):
        self.commits += 1

    def reserve(self, db, **values):
        self.reservations.append(values)
        return SimpleNamespace(id="resv_synthetic", reserved_amount=values["unit_price"] * values["count"],
                               cost_policy_snapshot={}, **values)

    def enqueue(self, db, **values):
        self.jobs.append(values)


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


class SearchPriceAcceptanceTests(unittest.TestCase):
    def setup_admission(self, path, price, *, existing=None):
        store = Storage()
        config = ordinary_config()
        config.prices = lambda: dict(price)
        config.paid_history_mode = lambda: "indexed"
        models = SimpleNamespace(**{name: model(name) for name in (
            "EmployerSearch", "ServiceCreditReservation", "EmployerSearchPreparation",
            "EmployerCatalogueResumeBinding", "EmployerCatalogueClock")})
        def search_input(payload):
            return {"resume_id": payload.resume_id, "role": "Python Engineer", "desired_count": payload.desired_count}

        namespace = {
            "config": config, "HTTPException": HTTPException, "CostPolicyUnavailable": type("CostPolicyUnavailable", (Exception,), {}),
            "json": json, "payload_fingerprint": fingerprint, "models": models,
            "credits": SimpleNamespace(reserve=store.reserve), "enqueue": store.enqueue,
            "utcnow": lambda: datetime.now(UTC), "load_only": lambda *fields: None,
            "_search_input": search_input, "_bounded_row": lambda *args, **kwargs: existing,
            "_bounded_resume_revision": lambda *args, **kwargs: 1,
            "_BOUNDED_QUERY_MAX_BYTES": 32768, "BOUNDED_SEARCH_PROTOCOL": "funded-bounded-search-v1",
            "catalogue_search": SimpleNamespace(lock_owner_nowait=lambda *args: None),
            "delivery_history": SimpleNamespace(_progress=lambda *args: None),
            "_bounded_worker_query": lambda value: value, "lock_owner_nowait": lambda *args: None,
            "_insert_absent": lambda *args: None, "PROJECTION_VERSION": "synthetic-projection",
            "service": SimpleNamespace(_require_enabled=lambda: None, _search_input=search_input,
                                       core=SimpleNamespace(Resume=model("Resume"))),
        }
        filename, function = ("service.py", "_admit_bounded_search") if path == "bounded" else ("catalogue_search.py", "accept")
        return store, config, admission_body(filename, function, namespace)

    def test_locked_existing_request_recovers_its_original_quote_without_repricing(self):
        original_quote = {"unit_price": 1, "pricing_version": "original-v1", "maximum_credits": 3}
        existing = SimpleNamespace(id="search_existing", scope={"price_quote": original_quote},
            input_fingerprint=fingerprint({"resume_id": 10, "role": "Python Engineer", "desired_count": 3}))
        store, config, admit = self.setup_admission("bounded", {"search_credits_per_job": 2,
            "pricing_version": "changed-v2", "max_search_jobs": 100}, existing=existing)

        def no_repricing():
            self.fail("A locked existing request must recover before reading a new price")

        config.prices = no_repricing
        payload = SimpleNamespace(resume_id=10, desired_count=3, idempotency_key="concurrent-retry",
            accepted_quote=SimpleNamespace(**original_quote))
        recovered = admit(store, 1, payload)
        self.assertIs(recovered, existing)
        self.assertEqual(recovered.scope["price_quote"], original_quote)
        self.assertEqual((store.reservations, store.rows, store.jobs), ([], [], []))
        self.assertEqual(store.commits, 1)  # Release the owner lock after recovery.

    def test_price_change_between_catalog_and_post_never_reserves_or_writes(self):
        for path in ("bounded", "catalogue"):
            # A deployment override can change price without changing version.
            for version in ("displayed-v1", "renewed-v2"):
                with self.subTest(path=path, version=version):
                    store, _, admit = self.setup_admission(path, {"search_credits_per_job": 2,
                        "pricing_version": version, "max_search_jobs": 100})
                    payload = SimpleNamespace(resume_id=10, desired_count=3, idempotency_key="synthetic-click",
                        accepted_quote=SimpleNamespace(unit_price=1, pricing_version="displayed-v1", maximum_credits=3))
                    with self.assertRaises(HTTPException) as raised:
                        admit(store, 1, payload)
                    self.assertEqual(raised.exception.status_code, 409)
                    self.assertEqual(raised.exception.detail["code"], "search_price_requires_review")
                    self.assertEqual((store.reservations, store.rows, store.jobs, store.commits), ([], [], [], 0))

    def test_missing_acceptance_and_changed_count_cannot_start_paid_work(self):
        for path in ("bounded", "catalogue"):
            for accepted in (None, SimpleNamespace(unit_price=2, pricing_version="current-v2", maximum_credits=4),
                             SimpleNamespace(unit_price=2, pricing_version="old-v1", maximum_credits=6)):
                with self.subTest(path=path, accepted=accepted):
                    store, _, admit = self.setup_admission(path, {"search_credits_per_job": 2,
                        "pricing_version": "current-v2", "max_search_jobs": 100})
                    with self.assertRaises(HTTPException):
                        admit(store, 1, SimpleNamespace(resume_id=10, desired_count=3,
                            idempotency_key="synthetic-click", accepted_quote=accepted))
                    self.assertEqual((store.reservations, store.rows, store.jobs, store.commits), ([], [], [], 0))

    def test_fresh_explicit_acceptance_uses_exact_maximum_on_both_paths(self):
        for path in ("bounded", "catalogue"):
            with self.subTest(path=path):
                store, config, admit = self.setup_admission(path, {"search_credits_per_job": 2,
                    "pricing_version": "current-v2", "max_search_jobs": 100})
                shown = config.prices()  # The catalog supplies these same prices.
                payload = SimpleNamespace(resume_id=10, desired_count=3, idempotency_key="fresh-explicit-click",
                    accepted_quote=SimpleNamespace(unit_price=shown["search_credits_per_job"],
                        pricing_version=shown["pricing_version"], maximum_credits=6))
                row = admit(store, 1, payload)
                self.assertEqual(len(store.reservations), 1)
                self.assertEqual(store.reservations[0]["unit_price"] * store.reservations[0]["count"], 6)
                quote = row.scope["price_quote"] if path == "bounded" else row.accepted_quote
                self.assertEqual((quote["unit_price"], quote["maximum_credits"], quote["requested_count"]), (2, 6, 3))
                self.assertEqual((len(store.rows), len(store.jobs), store.commits), (1, 1, 1))


if __name__ == "__main__":
    unittest.main()
