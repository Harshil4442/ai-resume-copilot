"""Retained payment-loss envelopes, never a bank balance or invoice verifier.

Customer erasure cannot hide these records. Refund principal reverses a sale;
this projection protects retained fees/tax and distinct spent/unknown work.
A conservative component is explicitly an envelope, not a claimed supplier bill.
"""

from __future__ import annotations

import json
from collections import defaultdict
from fractions import Fraction
from types import SimpleNamespace
from typing import Annotated, Literal

from pydantic import Field
from sqlalchemy import Text, cast, literal, or_, select, union_all

from ... import models
from ...billing.cost_policy import ExpensePolicy
from ...billing.cost_policy import snapshot as accepted_policy_snapshot
from ..common import payload_fingerprint
from ..employer.models import ServiceCreditReservation
from . import finance
from .models import CreditAllocation, CreditExecutionAttempt, CreditExecutionHold, CreditGrant

MAX_FACTS = 20_000
MAX_SNAPSHOT_BYTES = 16 * 1024 * 1024
VERSION: Literal["retained-payment-expenses-v1"] = "retained-payment-expenses-v1"
Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


class LossComponent(finance.Closed):
    key: Digest
    kind: Literal["payment_fee", "customer_tax", "refunded_work"]
    required_minor: finance.Minor
    facts_sha256: Digest
    basis: Literal["known", "conservative"]
    review_required: bool


class ExpenseSnapshot(finance.Closed):
    version: Literal["retained-payment-expenses-v1"] = VERSION
    facts_sha256: Digest
    components: tuple[LossComponent, ...]
    unresolved: tuple[str, ...]


def _financial_rows(db):
    """One portable SQL statement = one RC statement snapshot of every fact.

    No second connection after the money lock, no lock-all-orders, and no PII.
    Typed text slots keep PostgreSQL and SQLite UNION semantics identical.
    """
    processed = (
        select(models.PaymentRefund.id)
        .where(
            models.PaymentRefund.order_id == models.PaymentOrder.id,
            models.PaymentRefund.status == "processed",
        )
        .exists()
    )
    orders = select(models.PaymentOrder.id).where(
        or_(
            models.PaymentOrder.provider_mode != "test", models.PaymentOrder.provider_mode.is_(None)
        ),
        or_(
            models.PaymentOrder.paid_at.is_not(None),
            models.PaymentOrder.refunded_amount_minor > 0,
            models.PaymentOrder.status.in_(
                ("paid", "paid_unfulfilled", "partially_refunded", "refunded")
            ),
            processed,
        ),
    )
    refunded = select(models.PaymentOrder.public_id).where(
        models.PaymentOrder.id.in_(orders),
        or_(models.PaymentOrder.refunded_amount_minor > 0, processed),
    )
    grants = select(CreditGrant.id).where(
        CreditGrant.origin_type == "payment_order", CreditGrant.origin_id.in_(refunded)
    )
    allocations = select(CreditAllocation.reservation_id).where(
        CreditAllocation.grant_id.in_(grants)
    )
    legacy_refunded = (
        select(models.PaymentOrder.id)
        .where(
            models.PaymentOrder.id.in_(orders),
            models.PaymentOrder.id.in_(
                select(models.PaymentRefund.order_id).where(
                    models.PaymentRefund.status == "processed"
                )
            ),
            models.PaymentOrder.entitlement_kind != "credit_pass",
        )
        .exists()
    )
    touched_groups = select(CreditExecutionHold.financial_group_id).where(
        CreditExecutionHold.reservation_id.in_(allocations)
    )
    specs = (
        (
            "order",
            models.PaymentOrder,
            (
                "id",
                "public_id",
                "provider",
                "provider_mode",
                "status",
                "gross_amount_minor",
                "currency",
                "refunded_amount_minor",
                "provider_fee_amount_minor",
                "provider_fee_tax_minor",
                "entitlement_kind",
                "entitlement_quantity",
                "sku",
                "catalog_version",
                "customer_tax_amount_minor",
                "cost_policy_snapshot",
            ),
            models.PaymentOrder.id.in_(orders),
        ),
        (
            "transaction",
            models.PaymentTransaction,
            (
                "id",
                "order_id",
                "provider",
                "status",
                "gross_amount_minor",
                "currency",
                "provider_fee_amount_minor",
                "provider_fee_tax_minor",
                "customer_tax_amount_minor",
            ),
            models.PaymentTransaction.order_id.in_(orders),
        ),
        (
            "refund",
            models.PaymentRefund,
            ("id", "order_id", "transaction_id", "provider", "amount_minor", "currency"),
            models.PaymentRefund.order_id.in_(orders)
            & (models.PaymentRefund.status == "processed"),
        ),
        (
            "conflict",
            models.PaymentEvent,
            ("id", "order_id", "provider", "payload_sha256"),
            models.PaymentEvent.order_id.in_(orders)
            & (models.PaymentEvent.error_code == "provider_expense_conflict"),
        ),
        ("grant", CreditGrant, ("id", "origin_id"), CreditGrant.id.in_(grants)),
        (
            "allocation",
            CreditAllocation,
            ("reservation_id", "grant_id"),
            CreditAllocation.grant_id.in_(grants),
        ),
        (
            "hold",
            CreditExecutionHold,
            (
                "id",
                "reservation_id",
                "financial_group_id",
                "state",
                "delivery_state",
                "funding_kind",
                "reserved_minor",
                "native_reserved_minor",
                "settled_minor",
                "native_settled_minor",
                "snapshot",
            ),
            CreditExecutionHold.reservation_id.in_(allocations),
        ),
        (
            "legacy_model",
            models.ModelCostLiability,
            (
                "id",
                "financial_group_id",
                "currency",
                "cost_state",
                "reserved_cost_micros",
                "settled_cost_micros",
            ),
            or_(legacy_refunded, models.ModelCostLiability.financial_group_id.in_(touched_groups))
            & ~models.ModelCostLiability.id.in_(select(CreditExecutionAttempt.liability_id)),
        ),
        (
            "legacy_service",
            ServiceCreditReservation,
            ("id", "operation", "requested_count", "state", "cost_policy_snapshot"),
            legacy_refunded,
        ),
    )
    width = max(len(fields) for _, _, fields, _ in specs)
    statements = []
    for kind, model, fields, predicate in specs:
        columns = [cast(getattr(model, name), Text) for name in fields]
        columns.extend(cast(literal(None), Text) for _ in range(width - len(columns)))
        statements.append(
            select(
                literal(kind).label("kind"),
                *[column.label("v" + str(i)) for i, column in enumerate(columns)],
            ).where(predicate)
        )
    rows = db.execute(union_all(*statements).limit(MAX_FACTS + 1)).all()
    if len(rows) > MAX_FACTS:
        raise finance.FundingUnavailable(
            "Payment expense history exceeds the complete review scope"
        )
    fields_by_kind = {kind: fields for kind, _, fields, _ in specs}
    result = defaultdict(list)
    numeric = {
        "reserved_cost_micros",
        "settled_cost_micros",
        "requested_count",
        "entitlement_quantity",
        "gross_amount_minor",
        "refunded_amount_minor",
        "provider_fee_amount_minor",
        "provider_fee_tax_minor",
        "customer_tax_amount_minor",
        "amount_minor",
        "reserved_minor",
        "native_reserved_minor",
        "settled_minor",
        "native_settled_minor",
        "order_id",
        "transaction_id",
    }
    total_bytes = 0
    for row in rows:
        values = {}
        for name, value in zip(fields_by_kind[row.kind], row[1:], strict=False):
            if value is not None:
                total_bytes += len(value.encode())
                if len(value.encode()) > 256 * 1024 or total_bytes > MAX_SNAPSHOT_BYTES:
                    raise finance.FundingUnavailable(
                        "Payment expense facts exceed the complete review scope"
                    )
            if name in numeric or (
                name == "id" and row.kind in {"order", "transaction", "refund", "conflict"}
            ):
                values[name] = int(value) if value is not None else None
            elif name in {"cost_policy_snapshot", "snapshot"}:
                try:
                    values[name] = json.loads(value) if value is not None else None
                except (ValueError, TypeError):
                    values[name] = None
            else:
                values[name] = value
        result[row.kind].append(SimpleNamespace(**values))
    return result


def _money(value):
    if type(value) is not int or value < 0:
        raise ValueError("invalid_money")
    return value


def _maximum(values):
    known = [_money(value) for value in values if value is not None]
    return max(known) if known else None


def _accepted_bounds(order):
    snapshot = order.cost_policy_snapshot
    if not isinstance(snapshot, dict):
        raise ValueError("original_policy_unavailable")
    gross = _money(order.gross_amount_minor)
    if gross <= 0 or order.currency != "INR":
        raise ValueError("original_payment_currency_or_amount")
    if "unified_purchase_quote" in snapshot:
        from .payments import order_quote

        order_quote(order)  # Original immutable amount/currency/credits must still agree.
        policy = finance.FinancialPolicy.model_validate(snapshot["release_review"]["policy"])
        fee = (gross * policy.gateway_gross_bps + 9999) // 10_000
        tax = (gross * policy.included_sales_tax_bps + 9999 + policy.included_sales_tax_bps) // (
            10_000 + policy.included_sales_tax_bps
        )
        identity = policy.model_dump(mode="json")
    else:
        policy = ExpensePolicy.model_validate(snapshot["policy"])
        recorded = accepted_policy_snapshot(policy)
        if any(
            snapshot.get(name) != recorded[name]
            for name in ("version", "digest", "expires_at", "provenance")
        ):
            raise ValueError("original_policy_snapshot_changed")
        fee_base = (gross * policy.gateway_fee_bps + 9999) // 10_000
        fee = fee_base + (fee_base * policy.gateway_fee_tax_bps + 9999) // 10_000
        fee += policy.gateway_fixed_minor + policy.settlement_fx_minor
        tax = (gross * policy.sale_tax_reserve_bps + 9999 + policy.sale_tax_reserve_bps) // (
            10_000 + policy.sale_tax_reserve_bps
        )
        identity = policy.model_dump(mode="json")
    return fee, tax, payload_fingerprint(identity)


def _component(kind, identity, amount, facts, *, conservative=False, review=False):
    return LossComponent(
        key=payload_fingerprint({"kind": kind, "identity": identity}),
        kind=kind,
        required_minor=amount,
        facts_sha256=payload_fingerprint(facts),
        basis="conservative" if conservative else "known",
        review_required=review,
    )


def collect_expenses(db, *, policy: finance.FinancialPolicy) -> ExpenseSnapshot:
    """Fresh complete bounded metadata, no user join, cache, provider or mutation.

    Only live payment receipts can affect live funding. Synthetic tests use live-
    labelled owned rows, never real money. Existing release gates separately
    validate policy/invoices; this reader does not turn a policy into funding.
    """
    finance.FinancialPolicy.model_validate(policy)
    with db.no_autoflush:
        rows = _financial_rows(db)
        orders = sorted(rows["order"], key=lambda row: row.id)
        transactions, refunds, conflicts = rows["transaction"], rows["refund"], rows["conflict"]
        by_tx, by_refund, by_conflict = defaultdict(list), defaultdict(list), defaultdict(list)
        for row in transactions:
            by_tx[row.order_id].append(row)
        for row in refunds:
            by_refund[row.order_id].append(row)
        for row in conflicts:
            by_conflict[row.order_id].append(row)
        facts, components, unresolved, refunded_origins = [], [], [], {}
        legacy_policies = {}
        groups = defaultdict(list)
        for order in orders:
            key = payload_fingerprint({"provider": order.provider, "order": order.public_id})
            try:
                if order.provider != "razorpay" or order.provider_mode != "live":
                    raise ValueError("unknown_live_payment_provider_or_mode")
                if order.status not in {
                    "paid",
                    "paid_unfulfilled",
                    "partially_refunded",
                    "refunded",
                }:
                    raise ValueError("payment_state_unresolved")
                fee_bound, tax_bound, accepted = _accepted_bounds(order)
                txs = sorted(by_tx[order.id], key=lambda row: row.id)
                successful = [
                    row
                    for row in txs
                    if row.status in {"captured", "partially_refunded", "refunded"}
                ]
                if len(successful) != 1 or any(
                    row.status not in {"captured", "partially_refunded", "refunded", "failed"}
                    for row in txs
                ):
                    raise ValueError("captured_payment_manifest_unresolved")
                for row in txs:
                    if (
                        row.provider != order.provider
                        or row.currency != order.currency
                        or _money(row.gross_amount_minor) != order.gross_amount_minor
                    ):
                        raise ValueError("transaction_terms_mismatch")
                actual_fee = _maximum(
                    [
                        order.provider_fee_amount_minor,
                        *(row.provider_fee_amount_minor for row in txs),
                    ]
                )
                actual_fee_tax = _maximum(
                    [order.provider_fee_tax_minor, *(row.provider_fee_tax_minor for row in txs)]
                )
                actual_tax = _maximum(
                    [
                        order.customer_tax_amount_minor,
                        *(row.customer_tax_amount_minor for row in txs),
                    ]
                )
                if (
                    actual_fee_tax is not None
                    and actual_fee is not None
                    and actual_fee_tax > actual_fee
                ):
                    raise ValueError("inclusive_gateway_fee_inconsistent")
                processed = []
                for row in sorted(by_refund[order.id], key=lambda row: row.id):
                    if (
                        row.provider != order.provider
                        or row.currency != order.currency
                        or row.transaction_id != successful[0].id
                    ):
                        raise ValueError("refund_identity_mismatch")
                    processed.append(
                        {
                            "id": row.id,
                            "provider": row.provider,
                            "transaction": row.transaction_id,
                            "amount": _money(row.amount_minor),
                        }
                    )
                total = sum(row["amount"] for row in processed)
                if total != _money(order.refunded_amount_minor) or total > order.gross_amount_minor:
                    raise ValueError("refund_projection_mismatch")
                if any(row.provider != order.provider for row in by_conflict[order.id]):
                    raise ValueError("conflict_provider_mismatch")
                conflict = [
                    {"event": row.id, "payload": row.payload_sha256}
                    for row in sorted(by_conflict[order.id], key=lambda row: row.id)
                ]
                body = {
                    "order": key,
                    "gross": order.gross_amount_minor,
                    "currency": order.currency,
                    "accepted_policy": accepted,
                    "transactions": [
                        {
                            "id": row.id,
                            "status": row.status,
                            "gross": row.gross_amount_minor,
                            "currency": row.currency,
                            "fee": row.provider_fee_amount_minor,
                            "fee_tax_breakdown": row.provider_fee_tax_minor,
                            "customer_tax": row.customer_tax_amount_minor,
                        }
                        for row in txs
                    ],
                    "fee_bound": fee_bound,
                    "tax_bound": tax_bound,
                    "fee": actual_fee,
                    "fee_tax_breakdown": actual_fee_tax,
                    "customer_tax": actual_tax,
                    "refunds": processed,
                    "conflicts": conflict,
                }
                facts.append(body)
                # Razorpay's fee already includes its fee GST: never fee+tax.
                fee_excess = max(0, (actual_fee or 0) - fee_bound)
                retained_fee = max(fee_bound, actual_fee or 0) if total else 0
                fee_amount = max(fee_excess, retained_fee)
                if fee_amount or conflict:
                    components.append(
                        _component(
                            "payment_fee",
                            key,
                            fee_amount,
                            body,
                            conservative=bool(total) or bool(conflict),
                            review=bool(conflict),
                        )
                    )
                tax_excess = max(0, (actual_tax or 0) - tax_bound)
                # Refund tax recovery is not a fact in this schema. Retain the
                # original complete tax envelope until separately reviewed;
                # never invent a credit note or a recovered supplier payment.
                tax_amount = max(tax_excess, max(tax_bound, actual_tax or 0) if total else 0)
                if tax_amount:
                    components.append(
                        _component(
                            "customer_tax",
                            key,
                            tax_amount,
                            body,
                            conservative=bool(total) or actual_tax is None,
                        )
                    )
                if total:
                    refunded_origins[order.public_id] = payload_fingerprint(body)
                    if "unified_purchase_quote" not in order.cost_policy_snapshot:
                        legacy_policies[order.public_id] = ExpensePolicy.model_validate(
                            order.cost_policy_snapshot["policy"]
                        )
            except (ValueError, TypeError, KeyError, AttributeError):
                unresolved.append("unresolved_payment_facts:" + key)
        if refunded_origins:
            grants = {row.id: row.origin_id for row in rows["grant"]}
            reservations = defaultdict(set)
            for allocation in rows["allocation"]:
                if allocation.grant_id not in grants:
                    raise finance.FundingUnavailable(
                        "Refund allocation lost its retained original grant"
                    )
                reservations[allocation.reservation_id].add(grants[allocation.grant_id])
            holds = sorted(rows["hold"], key=lambda row: row.id)
            missing = set(reservations) - {hold.reservation_id for hold in holds}
            unresolved.extend(
                "unresolved_missing_work_hold:" + payload_fingerprint(value)
                for value in sorted(missing)
            )
            origins = {hold.id: reservations[hold.reservation_id] for hold in holds}
            for hold in holds:
                groups[hold.financial_group_id].append(hold)
            for group, group_holds in sorted(groups.items()):
                try:
                    if len(group_holds) != 1:
                        raise ValueError("duplicate_credit_execution_group")
                    amounts, group_facts = [], []
                    for hold in group_holds:
                        reserved = _money(hold.reserved_minor)
                        settled = None if hold.settled_minor is None else _money(hold.settled_minor)
                        native_reserved = _money(hold.native_reserved_minor)
                        native_settled = (
                            None
                            if hold.native_settled_minor is None
                            else _money(hold.native_settled_minor)
                        )
                        if native_reserved > reserved or (
                            native_settled is not None
                            and settled is not None
                            and native_settled > settled
                        ):
                            raise ValueError("native_cost_inconsistent")
                        if hold.state == "settled" and settled is None:
                            raise ValueError("settled_cost_missing")
                        fully_settled = hold.state == "settled" and native_settled is not None
                        amounts.append(
                            _money(settled)
                            if fully_settled
                            else max(reserved, settled or 0, native_settled or 0)
                        )
                        group_facts.append(
                            {
                                "hold": hold.id,
                                "state": hold.state,
                                "reserved": reserved,
                                "settled": settled,
                                "native_reserved": native_reserved,
                                "native_settled": native_settled,
                                "refunded_origins": {
                                    key: refunded_origins[key] for key in sorted(origins[hold.id])
                                },
                            }
                        )
                    body = {"group": group, "holds": group_facts}
                    facts.append(body)
                    # Multiple origin-allocation paths resolve this actual
                    # unique whole-operation hold only once.
                    amount = max(amounts)
                    if amount:
                        components.append(
                            _component("refunded_work", group, amount, body, conservative=True)
                        )
                except (ValueError, TypeError, KeyError, AttributeError):
                    unresolved.append("unresolved_refunded_work:" + payload_fingerprint(group))
        if legacy_policies or rows["legacy_model"]:
            # Legacy service/model records have no durable per-order origin.
            # Retain their complete global exposure once; this is an explicit
            # conservative capital envelope, not another booked expense.
            policies = tuple(legacy_policies.values())
            legacy_origins = {key: refunded_origins[key] for key in sorted(legacy_policies)}
            model_groups = defaultdict(list)
            for row in sorted(rows["legacy_model"], key=lambda row: row.id):
                model_groups[row.financial_group_id].append(row)
            for group, entries in sorted(model_groups.items()):
                try:
                    tied = [
                        finance.FinancialPolicy.model_validate(hold.snapshot["policy"])
                        for hold in groups.get(group, ())
                    ]
                    model_policies: tuple[ExpensePolicy | finance.FinancialPolicy, ...] = (*policies, *tied)
                    if not model_policies:
                        raise ValueError("original_model_conversion_policy_unavailable")
                    costs = []
                    group_origins = dict(legacy_origins)
                    for hold in groups.get(group, ()):
                        group_origins.update({key: refunded_origins[key] for key in origins[hold.id]})
                    body = {
                        "legacy_group": group,
                        "origins": dict(sorted(group_origins.items())),
                        "attempts": [],
                        "original_policy_fingerprints": sorted(
                            payload_fingerprint(value.model_dump(mode="json"))
                            for value in model_policies
                        ),
                    }
                    for row in entries:
                        if row.currency != "USD":
                            raise ValueError("legacy_currency_unavailable")
                        reserved = _money(row.reserved_cost_micros)
                        settled = (
                            None
                            if row.settled_cost_micros is None
                            else _money(row.settled_cost_micros)
                        )
                        if row.cost_state in {"settled", "overrun"} and settled is None:
                            raise ValueError("legacy_settled_cost_missing")
                        micros = (
                            _money(settled)
                            if row.cost_state in {"settled", "overrun"}
                            else max(reserved, settled or 0)
                        )
                        costs.append(
                            max(
                                finance.ceil_minor(
                                    Fraction(
                                        micros
                                        * accepted.adverse_fx_minor_per_usd
                                        * (
                                            10_000
                                            + (
                                                accepted.unrecoverable_provider_tax_bps
                                                if isinstance(accepted, finance.FinancialPolicy)
                                                else accepted.contingency_bps
                                            )
                                        ),
                                        10_000 * 1_000_000,
                                    )
                                )
                                + (
                                    0
                                    if isinstance(accepted, finance.FinancialPolicy)
                                    else accepted.provider_attempt_overhead_minor
                                )
                                for accepted in model_policies
                            )
                        )
                        body["attempts"].append(
                            {
                                "id": row.id,
                                "state": row.cost_state,
                                "reserved_micros": reserved,
                                "settled_micros": settled,
                            }
                        )
                    facts.append(body)
                    if sum(costs):
                        components.append(
                            _component(
                                "refunded_work",
                                group,
                                sum(costs),
                                body,
                                conservative=True,
                            )
                        )
                except (ValueError, TypeError, KeyError, AttributeError):
                    unresolved.append("unresolved_legacy_model:" + payload_fingerprint(group))
            for row in sorted(rows["legacy_service"], key=lambda row: row.id):
                try:
                    if row.operation not in {"job_search", "job_application"}:
                        raise ValueError("legacy_service_unknown")
                    snapshot = row.cost_policy_snapshot or {}
                    accepted = (
                        (ExpensePolicy.model_validate(snapshot["policy"]),)
                        if "policy" in snapshot
                        else policies
                    )
                    count = _money(row.requested_count)
                    amount = max(
                        finance.ceil_minor(
                            Fraction(
                                max(
                                    count
                                    * (
                                        p.search_cost_minor
                                        if row.operation == "job_search"
                                        else p.application_cost_minor
                                    ),
                                    p.service_preparation_cost_minor
                                    if row.operation == "job_search"
                                    else 0,
                                )
                                * (10_000 + p.contingency_bps),
                                10_000,
                            )
                        )
                        for p in accepted
                    )
                    explicit = _money(snapshot.get("legacy_service_hold_minor", 0)) + _money(
                        snapshot.get("failed_service_hold_minor", 0)
                    )
                    amount = max(amount, explicit)
                    body = {
                        "legacy_service": row.id,
                        "operation": row.operation,
                        "count": count,
                        "accepted": payload_fingerprint(snapshot),
                        "explicit": explicit,
                        "origins": legacy_origins,
                    }
                    facts.append(body)
                    if amount:
                        components.append(
                            _component(
                                "refunded_work",
                                "legacy-service:" + row.id,
                                amount,
                                body,
                                conservative=True,
                            )
                        )
                except (ValueError, TypeError, KeyError, AttributeError):
                    unresolved.append("unresolved_legacy_service:" + payload_fingerprint(row.id))
        # These model IDs explicitly exclude attempts already aggregated by a
        # whole-operation hold. A shared group label alone cannot erase their
        # separate exposure. One conservative group protects the sum of those
        # distinct envelopes; repeated origin paths still select each row once.
        by_component = defaultdict(list)
        for item in components:
            by_component[item.key].append(item)
        components = [
            values[0]
            if len(values) == 1
            else LossComponent(
                key=key,
                kind=values[0].kind,
                required_minor=sum(item.required_minor for item in values),
                facts_sha256=payload_fingerprint(
                    {"overlapping_group_facts": sorted(item.facts_sha256 for item in values)}
                ),
                basis="conservative",
                review_required=any(item.review_required for item in values),
            )
            for key, values in sorted(by_component.items())
        ]
        components.sort(key=lambda item: item.key)
        body = {
            "version": VERSION,
            "facts": facts,
            "components": [item.model_dump(mode="json") for item in components],
            "unresolved": sorted(unresolved),
        }
        if len(json.dumps(body, sort_keys=True, default=str).encode()) > MAX_SNAPSHOT_BYTES:
            raise finance.FundingUnavailable(
                "Payment expense facts exceed the complete review scope"
            )
        return ExpenseSnapshot(
            facts_sha256=payload_fingerprint(body),
            components=tuple(components),
            unresolved=tuple(sorted(unresolved)),
        )


def assert_new_exposure_funded(db, *, policy: finance.FinancialPolicy, now) -> None:
    """Candidate paths can read the guard, never register or allocate funding."""
    from .payment_funding import assert_new_exposure_funded as guard

    guard(db, policy=policy, now=now)
