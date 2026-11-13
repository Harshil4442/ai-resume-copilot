"""Detached reviewed funding declarations and atomic loss allocations.

These are operator declarations, never automatic bank/invoice verification.
Only the fixed privileged operator path may call the writers. Candidate paths
read the guard. SQL atomicity is not independent database-restore protection.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Annotated, Literal

from pydantic import Field, model_validator

from ...models import AdminAuditEvent
from ..common import payload_fingerprint
from . import finance

Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
FUND_ACTION = "payment_funding_declared_v1"
REVIEW_ACTION = "payment_funding_reviewed_v1"
ALLOC_ACTION = "payment_loss_allocated_v1"
ACTIONS = {FUND_ACTION, REVIEW_ACTION, ALLOC_ACTION}
ACTOR = "operator@funding.hirewiz.invalid"
REASON = "Reviewed payment loss protection; not independent bank verification"
MAX_AUDITS = 20_000
MAX_AUDIT_BYTES = 16 * 1024 * 1024


def _need(value, reason):
    if not value:
        raise finance.FundingUnavailable("payment_funding_" + reason)


def _key(purpose, value):
    return payload_fingerprint({"purpose": purpose, "value": value})


def _money(value):
    _need(type(value) is int and value >= 0, "invalid_amount")
    return value


class FundingDeclaration(finance.Closed):
    version: Literal["reviewed-payment-funding-v1"] = "reviewed-payment-funding-v1"
    external_reference_sha256: Digest
    currency: Literal["INR"] = "INR"
    amount_minor: finance.Positive
    funded_at: datetime
    availability_basis: Literal["available_after_existing_obligations"]
    evidence_sha256: Digest
    reviewer_reference_sha256: Digest
    reviewed_at: datetime
    review_valid_until: datetime
    basis: Literal["reviewed_operator_declaration"] = "reviewed_operator_declaration"

    @model_validator(mode="after")
    def dates(self):
        _dates(self.reviewed_at, self.review_valid_until)
        if self.funded_at.tzinfo is None or self.funded_at > self.reviewed_at:
            raise ValueError("Funding must exist before its reviewed declaration")
        return self


class FundingIdentity(finance.Closed):
    external_reference_sha256: Digest
    currency: Literal["INR"]
    amount_minor: finance.Positive
    funded_at: datetime
    availability_basis: Literal["available_after_existing_obligations"]

    @model_validator(mode="after")
    def aware(self):
        if self.funded_at.tzinfo is None:
            raise ValueError("Funding date must be timezone-aware")
        return self


class AllocationRequest(finance.Closed):
    version: Literal["payment-loss-allocation-v1"] = "payment-loss-allocation-v1"
    component_key: Digest
    expected_facts_sha256: Digest
    funding_reference_sha256: Digest | None = None
    amount_minor: finance.Minor
    expense_evidence_sha256: Digest
    reviewer_reference_sha256: Digest
    reviewed_at: datetime
    review_valid_until: datetime
    basis: Literal["reviewed_conservative_envelope", "reviewed_invoice_declaration"]

    @model_validator(mode="after")
    def dates(self):
        _dates(self.reviewed_at, self.review_valid_until)
        if self.amount_minor and self.funding_reference_sha256 is None:
            raise ValueError("A positive allocation requires its original funding source")
        return self


def _dates(start, end):
    if start.tzinfo is None or end.tzinfo is None or not start < end <= start + timedelta(days=31):
        raise ValueError("Reviewed declaration dates must be bounded and timezone-aware")


def _current(request, now):
    _need(
        now.tzinfo is not None and request.reviewed_at <= now < request.review_valid_until,
        "review_expired_or_future",
    )


def _identity(declaration):
    return {
        key: declaration.model_dump(mode="json")[key]
        for key in (
            "external_reference_sha256",
            "currency",
            "amount_minor",
            "funded_at",
            "availability_basis",
        )
    }


def _row_value(row):
    _need(
        row.action in ACTIONS
        and row.actor_user_id is None
        and row.actor_email == ACTOR
        and row.reason == REASON
        and row.before_state == {}
        and row.correlation_id == row.id,
        "audit_shape_changed",
    )
    body = row.after_state
    _need(isinstance(body, dict), "audit_body_invalid")
    if row.action == FUND_ACTION:
        _need(
            set(body) == {"identity"} and row.target_type == "payment_funding",
            "funding_record_invalid",
        )
        parsed_identity = FundingIdentity.model_validate(body["identity"])
        _need(parsed_identity.funded_at <= _utc(row.created_at), "funding_recorded_before_funding")
        identity = parsed_identity.model_dump(mode="json")
        _need(identity == body["identity"], "funding_identity_noncanonical")
        _need(
            identity["amount_minor"] > 0
            and row.id == _key("funding", identity["external_reference_sha256"])
            and row.target_id == identity["external_reference_sha256"],
            "funding_identity_changed",
        )
    elif row.action == REVIEW_ACTION:
        _need(set(body) == {"declaration", "funding_id"}, "review_record_invalid")
        declaration = FundingDeclaration.model_validate(body["declaration"])
        _current(declaration, _utc(row.created_at))
        _need(body["declaration"] == declaration.model_dump(mode="json"), "review_noncanonical")
        _need(
            set(body) == {"declaration", "funding_id"}
            and body["funding_id"] == _key("funding", declaration.external_reference_sha256)
            and row.id == _key("funding-review", body)
            and row.target_type == "payment_funding"
            and row.target_id == body["funding_id"],
            "review_record_changed",
        )
    else:
        _need(
            set(body) == {"request", "funding_id", "required_minor", "covered_before"},
            "allocation_record_invalid",
        )
        request = AllocationRequest.model_validate(body["request"])
        _current(request, _utc(row.created_at))
        _need(body["request"] == request.model_dump(mode="json"), "allocation_noncanonical")
        _need(
            set(body) == {"request", "funding_id", "required_minor", "covered_before"}
            and row.id == _key("allocation", request.model_dump(mode="json"))
            and row.target_type == "payment_loss"
            and row.target_id == request.component_key,
            "allocation_record_changed",
        )
        _money(body["required_minor"])
        _money(body["covered_before"])
        _need(
            body["funding_id"]
            == (
                _key("funding", request.funding_reference_sha256)
                if request.funding_reference_sha256 is not None
                else None
            ),
            "allocation_source_changed",
        )
    return {
        "id": row.id,
        "action": row.action,
        "target_type": row.target_type,
        "target_id": row.target_id,
        "actor_user_id": row.actor_user_id,
        "actor_email": row.actor_email,
        "reason": row.reason,
        "before_state": row.before_state,
        "after_state": body,
        "correlation_id": row.correlation_id,
    }


def _utc(value):
    _need(isinstance(value, datetime), "audit_time_invalid")
    # Existing AdminAuditEvent uses a timezone-naive UTC SQL column.
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _row(row):
    try:
        value = _row_value(row)
        _need(len(json.dumps(value, sort_keys=True).encode()) <= 64 * 1024, "audit_body_too_large")
        return value
    except finance.FundingUnavailable:
        raise
    except (ValueError, TypeError, KeyError, AttributeError):
        raise finance.FundingUnavailable("payment_funding_audit_record_invalid") from None


def audit_rows(db, ids: tuple[str, ...]) -> dict:
    import re

    _need(
        0 < len(ids) <= 256
        and len(set(ids)) == len(ids)
        and all(type(value) is str and re.fullmatch(r"[0-9a-f]{64}", value) for value in ids),
        "audit_ids_invalid",
    )
    rows = (
        db.query(AdminAuditEvent)
        .filter(AdminAuditEvent.id.in_(ids))
        .order_by(AdminAuditEvent.id)
        .populate_existing()
        .all()
    )
    _need(len(rows) == len(ids), "audit_receipt_missing")
    return {row.id: _row(row) for row in rows}


def _write(db, identity, action, target_type, target_id, body, now):
    prior = db.get(AdminAuditEvent, identity)
    if prior is not None:
        observed = _row(prior)
        _need(
            observed["action"] == action
            and observed["target_type"] == target_type
            and observed["target_id"] == target_id
            and observed["after_state"] == body,
            "original_audit_changed",
        )
        return prior
    row = AdminAuditEvent(
        id=identity,
        actor_user_id=None,
        actor_email=ACTOR,
        action=action,
        target_type=target_type,
        target_id=target_id,
        reason=REASON,
        before_state={},
        after_state=body,
        correlation_id=identity,
        created_at=now,
    )
    db.add(row)
    db.flush([row])
    return row


def _history(db):
    rows = (
        db.query(AdminAuditEvent)
        .filter(AdminAuditEvent.action.in_(ACTIONS))
        .order_by(AdminAuditEvent.id)
        .populate_existing()
        .limit(MAX_AUDITS + 1)
        .all()
    )
    _need(len(rows) <= MAX_AUDITS, "complete_history_scope_exceeded")
    values = {row.id: _row(row) for row in rows}
    _need(
        sum(len(json.dumps(value, sort_keys=True).encode()) for value in values.values())
        <= MAX_AUDIT_BYTES,
        "complete_history_bytes_exceeded",
    )
    recorded_at = {row.id: _utc(row.created_at) for row in rows}
    funds, allocations, reviews = {}, [], []
    for value in values.values():
        if value["action"] == FUND_ACTION:
            funds[value["id"]] = value["after_state"]["identity"]
        elif value["action"] == ALLOC_ACTION:
            allocations.append(value["after_state"])
        else:
            reviews.append(value["after_state"])
    used: dict[str, int] = {}
    for body in allocations:
        request = AllocationRequest.model_validate(body["request"])
        fund = body["funding_id"]
        if request.amount_minor:
            _need(fund in funds, "original_funding_missing")
            used[fund] = used.get(fund, 0) + request.amount_minor
            at = recorded_at[_key("allocation", request.model_dump(mode="json"))]
            _need(
                recorded_at[fund] <= at
                and any(
                    review["funding_id"] == fund
                    and recorded_at[_key("funding-review", review)] <= at
                    and (
                        declaration := FundingDeclaration.model_validate(review["declaration"])
                    ).reviewed_at
                    <= at
                    < declaration.review_valid_until
                    for review in reviews
                ),
                "allocation_original_review_missing",
            )
    _need(
        all(amount <= funds[key]["amount_minor"] for key, amount in used.items()),
        "funding_overallocated",
    )
    _need(
        all(any(review["funding_id"] == fund for review in reviews) for fund in funds),
        "funding_original_review_missing",
    )
    for review in reviews:
        declaration = FundingDeclaration.model_validate(review["declaration"])
        _need(
            review["funding_id"] in funds and funds[review["funding_id"]] == _identity(declaration),
            "review_changed_original_funding",
        )
    return funds, allocations, reviews, used


def register_declaration(db, declaration: FundingDeclaration, *, now: datetime) -> dict:
    """Privileged reviewed declaration only; caller owns transaction/commit."""
    from .admission import lock_money

    _need(type(declaration) is FundingDeclaration, "exact_declaration_required")
    _current(declaration, now)
    lock_money(db)
    _history(db)
    fund = _key("funding", declaration.external_reference_sha256)
    _write(
        db,
        fund,
        FUND_ACTION,
        "payment_funding",
        declaration.external_reference_sha256,
        {"identity": _identity(declaration)},
        now,
    )
    body = {"funding_id": fund, "declaration": declaration.model_dump(mode="json")}
    review = _key("funding-review", body)
    _write(db, review, REVIEW_ACTION, "payment_funding", fund, body, now)
    return {"funding_id": fund, "review_id": review}


def allocate_loss(
    db, request: AllocationRequest, *, policy: finance.FinancialPolicy, now: datetime
) -> dict:
    """Bind exact retained facts and consume original available funds once."""
    from .admission import lock_money
    from .payment_expenses import collect_expenses

    _need(type(request) is AllocationRequest, "exact_allocation_required")
    lock_money(db)
    identity = _key("allocation", request.model_dump(mode="json"))
    funds, allocations, reviews, used = _history(db)
    prior = db.get(AdminAuditEvent, identity)
    if prior is not None:
        body = _row(prior)["after_state"]
        _need(body["request"] == request.model_dump(mode="json"), "replay_request_changed")
        return {
            "allocation_id": identity,
            "covered_minor": body["covered_before"] + request.amount_minor,
            "required_minor": body["required_minor"],
        }
    _current(request, now)
    snapshot = collect_expenses(db, policy=policy)
    _need(not snapshot.unresolved, "original_expense_facts_unresolved")
    component = next(
        (item for item in snapshot.components if item.key == request.component_key), None
    )
    if component is None or component.facts_sha256 != request.expected_facts_sha256:
        raise finance.FundingUnavailable("payment_funding_expense_facts_changed")
    covered = sum(
        body["request"]["amount_minor"]
        for body in allocations
        if body["request"]["component_key"] == component.key
    )
    remaining = max(component.required_minor - covered, 0)
    _need(request.amount_minor <= remaining, "allocation_exceeds_loss")
    fund = None
    if request.funding_reference_sha256 is not None:
        fund = _key("funding", request.funding_reference_sha256)
        _need(fund in funds, "original_funding_missing")
        valid = [
            FundingDeclaration.model_validate(row["declaration"])
            for row in reviews
            if row["funding_id"] == fund
        ]
        _need(
            any(row.reviewed_at <= now < row.review_valid_until for row in valid),
            "funding_review_expired",
        )
        _need(
            used.get(fund, 0) + request.amount_minor <= funds[fund]["amount_minor"],
            "available_funding_exhausted",
        )
    _need(request.amount_minor == 0 or fund is not None, "positive_funding_required")
    body = {
        "request": request.model_dump(mode="json"),
        "funding_id": fund,
        "required_minor": component.required_minor,
        "covered_before": covered,
    }
    _write(db, identity, ALLOC_ACTION, "payment_loss", component.key, body, now)
    return {
        "allocation_id": identity,
        "covered_minor": covered + request.amount_minor,
        "required_minor": component.required_minor,
    }


def assert_new_exposure_funded(db, *, policy: finance.FinancialPolicy, now: datetime) -> None:
    """Only new exposure; never call this from accepted settlement/refund paths."""
    from .admission import lock_money
    from .payment_expenses import collect_expenses

    finance.require_current_policy(policy, now=now)
    lock_money(db)
    snapshot = collect_expenses(db, policy=policy)
    _need(not snapshot.unresolved, "original_expense_facts_unresolved")
    _, allocations, _, _ = _history(db)
    for component in snapshot.components:
        same = [
            body["request"]
            for body in allocations
            if body["request"]["component_key"] == component.key
        ]
        covered = sum(row["amount_minor"] for row in same)
        reviewed = any(row["expected_facts_sha256"] == component.facts_sha256 for row in same)
        _need(covered >= component.required_minor and reviewed, "expense_funding_review_required")
