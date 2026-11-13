"""Actual owned PG17 snapshots and money-lock races, with synthetic receipts only."""

from concurrent.futures import ThreadPoolExecutor
from threading import Event
from uuid import uuid4

import pytest
from backend.app.database import Base
from backend.app.domains.credits import (
    finance,
)
from backend.app.domains.credits import (
    payment_expenses as expenses,
)
from backend.app.domains.credits import (
    payment_funding as funding,
)
from backend.app.models import ModelCostLiability
from backend.tests.test_mail_budget_postgres import owned_pg_url
from backend.tests.test_payment_expenses import NOW, paid, policy, work
from backend.tests.test_payment_funding import declaration, request
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import sessionmaker

assert owned_pg_url


@pytest.fixture
def pg_factory(owned_pg_url):
    schema = "payment_loss_" + uuid4().hex
    admin = create_engine(owned_pg_url)
    with admin.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine = create_engine(
        owned_pg_url,
        connect_args={
            "application_name": "payment-loss-owned",
            "options": f"-csearch_path={schema} -clock_timeout=5000 -cstatement_timeout=10000 -ctimezone=UTC",
        },
    )
    Base.metadata.create_all(engine)
    try:
        yield sessionmaker(engine, autoflush=False, expire_on_commit=False)
    finally:
        engine.dispose()
        with admin.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin.dispose()


def test_actual_pg_union_projection_is_single_statement_and_owner_detached(pg_factory):
    with pg_factory() as db:
        order, _ = paid(db, refund=1)
        hold = work(db, order, settled=100)
        hold.snapshot = {"policy": policy(unrecoverable_provider_tax_bps=0).model_dump(mode="json")}
        db.add(
            ModelCostLiability(
                id="pg-distinct-unlinked",
                financial_group_id=hold.financial_group_id,
                attempt_number=1,
                provider="synthetic",
                model="synthetic",
                endpoint_key="c" * 64,
                currency="USD",
                pricing_quote={},
                input_token_estimate=1,
                reserved_cost_micros=10000,
                settled_cost_micros=10000,
                cost_state="settled",
            )
        )
        db.commit()
        statements = []

        def observe(*args):
            statements.append(args[2])

        event.listen(db.get_bind(), "before_cursor_execute", observe)
        try:
            result = expenses.collect_expenses(db, policy=policy())
        finally:
            event.remove(db.get_bind(), "before_cursor_execute", observe)
        assert len(statements) == 1 and "UNION ALL" in statements[0]
        assert "users" not in statements[0] and not result.unresolved
        assert {row.kind: row.required_minor for row in result.components} == {
            "payment_fee": 500,
            "customer_tax": 1526,
            "refunded_work": 200,
        }


@pytest.mark.parametrize("target", ["same_component", "different_components"])
def test_actual_pg_competing_loss_allocations_cannot_spend_original_source_twice(
    pg_factory, target
):
    with pg_factory() as db:
        paid(db, name="one")
        paid(db, name="two")
        components = expenses.collect_expenses(db, policy=policy()).components
        assert len(components) == 2 and all(row.required_minor == 264 for row in components)
        funding.register_declaration(db, declaration(amount=264), now=NOW)
        db.commit()
    first_written, release, second_started = Event(), Event(), Event()

    def first():
        with pg_factory() as db:
            result = funding.allocate_loss(db, request(components[0]), policy=policy(), now=NOW)
            first_written.set()
            assert release.wait(5)
            db.commit()
            return result

    def second():
        assert first_written.wait(5)
        with pg_factory() as db:
            second_started.set()
            component = components[0] if target == "same_component" else components[1]
            try:
                funding.allocate_loss(
                    db,
                    request(component, expense_evidence_sha256="a" * 64),
                    policy=policy(),
                    now=NOW,
                )
            except finance.FundingUnavailable as error:
                db.rollback()
                return str(error)
            raise AssertionError(
                "Competing allocation unexpectedly consumed the same original capacity"
            )

    with ThreadPoolExecutor(max_workers=2) as pool:
        one = pool.submit(first)
        two = pool.submit(second)
        try:
            assert first_written.wait(5) and second_started.wait(5)
            assert not two.done()
        finally:
            release.set()
        assert one.result(timeout=10)["covered_minor"] == 264
        assert (
            "allocation_exceeds_loss"
            if target == "same_component"
            else "available_funding_exhausted"
        ) in two.result(timeout=10)
    with pg_factory() as db:
        funds, allocations, _, used = funding._history(db)
        assert len(funds) == len(allocations) == 1 and list(used.values()) == [264]
