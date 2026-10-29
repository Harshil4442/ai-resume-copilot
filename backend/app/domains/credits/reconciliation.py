"""Operator-reviewed native expense settlement, never customer authority.

Receipt bytes and completed-work evidence must be retained and reviewed by the
operator. A digest establishes custody, not the truth of an invoice. This module
neither qualifies invoices for sales nor settles unknown model-provider costs.
It uses existing cost rows and append-only privileged audit records; no wallet,
customer authorization, route or release flag is changed.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta
from typing import Annotated, Literal

from pydantic import Field, field_validator, model_validator
from sqlalchemy import text
from sqlalchemy.orm import Session

from ...models import AdminAuditEvent
from ..employer.testing_promotion import Operator
from ..free_core.models import FreeCoreRequest, FreeCoreReservation
from ..free_core.service import settle_measured_expense
from . import admission, execution, finance, service
from .models import CreditExecutionHold

Digest = Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
Identifier = Annotated[str, Field(pattern=r"^[A-Za-z0-9_.:-]{1,64}$")]
ACTION = "native_expense_reconciled_v1"
SCHEMA = "20261028_0025"


class ReconciliationDenied(ValueError):
    """Fixed reasons only; no database URLs, evidence contents or identities."""


def _need(value: bool, reason: str) -> None:
    if not value:
        raise ReconciliationDenied(reason)


def canonical(value: dict) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def digest(value: dict) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


class NativeExpenseReceipt(finance.Closed):
    kind: Literal["paid_native", "free_core"]
    target_id: Identifier
    provenance: Literal["measured_usage", "invoice_reconciled"]
    # Initial operator deliberately refuses zero instead of guessing that a
    # timeout or cancelled operation did not incur any expense.
    actual_minor: int = Field(strict=True, gt=0, le=10**9)
    currency: Literal["INR"] = "INR"
    source_sha256: Digest
    completion_sha256: Digest
    allocation: Literal["direct_operation_metering", "reviewed_invoice_allocation"]
    work_state: Literal["completed", "failed"]
    window_start: datetime
    window_end: datetime
    terminal_at: datetime
    reviewed_at: datetime

    @model_validator(mode="after")
    def bounded(self):
        values = (self.window_start, self.window_end, self.terminal_at, self.reviewed_at)
        if any(value.tzinfo is None for value in values):
            raise ValueError("Expense evidence must include timezone-aware dates")
        if not (self.window_start <= self.terminal_at <= self.window_end <= self.reviewed_at
                and self.window_start < self.window_end
                and self.window_end - self.window_start <= timedelta(days=31)):
            raise ValueError("Complete native work and a bounded measurement window are required")
        if self.allocation == "reviewed_invoice_allocation" and self.provenance != "invoice_reconciled":
            raise ValueError("Invoice allocation needs reconciled invoice evidence")
        return self


class TerminalWorkEvidence(finance.Closed):
    """Operator-reviewed execution correlation, not a provider attestation.

    The reviewer checks original execution readbacks, including descendants and
    future retries. SQL failure, lease expiry and HTTP completion are inadequate.
    """
    version: Literal["reviewed-native-terminal-v1"] = "reviewed-native-terminal-v1"
    provenance: Literal["operator_reviewed_native_execution"]
    kind: Literal["paid_native", "free_core"]
    target_id: Identifier
    financial_group_id: Identifier | None
    reservation_id: Identifier
    execution_readback_sha256: tuple[Digest, ...] = Field(min_length=1, max_length=32)
    work_state: Literal["completed", "failed"]
    all_native_attempts_terminal: Literal[True]
    no_future_native_work: Literal[True]
    terminal_at: datetime
    observed_at: datetime

    @field_validator("all_native_attempts_terminal", "no_future_native_work", mode="before")
    @classmethod
    def exact_true(cls, value):
        if value is not True:
            raise ValueError("Reviewed execution termination is required")
        return value

    @model_validator(mode="after")
    def bounded(self):
        if (self.terminal_at.tzinfo is None or self.observed_at.tzinfo is None
                or self.observed_at < self.terminal_at
                or len(set(self.execution_readback_sha256)) != len(self.execution_readback_sha256)
                or (self.kind == "paid_native") != (self.financial_group_id is not None)):
            raise ValueError("Exact reviewed native execution evidence is required")
        return self


class ReconciliationPlan(finance.Closed):
    version: Literal["native-expense-plan-v1"] = "native-expense-plan-v1"
    nonce: str = Field(pattern=r"^[a-f0-9]{32}$")
    database_sha256: Digest
    target_sha256: Digest
    receipt: NativeExpenseReceipt
    terminal: TerminalWorkEvidence
    operator_email: str = Field(min_length=3, max_length=320)
    operator_native_identity_sha256: Digest
    created_at: datetime
    expires_at: datetime

    @model_validator(mode="after")
    def bounded(self):
        Operator(self.operator_email, self.operator_native_identity_sha256).validate()
        if (self.created_at.tzinfo is None or self.expires_at.tzinfo is None
                or not self.created_at < self.expires_at <= self.created_at + timedelta(hours=24)
                or self.receipt.reviewed_at > self.created_at):
            raise ValueError("A current bounded operator review is required")
        if (self.terminal.kind != self.receipt.kind or self.terminal.target_id != self.receipt.target_id
                or self.terminal.work_state != self.receipt.work_state
                or self.terminal.terminal_at != self.receipt.terminal_at
                or self.terminal.observed_at > self.receipt.reviewed_at):
            raise ValueError("Receipt and terminal execution evidence must agree")
        return self


def database_identity(db: Session) -> str:
    """No credential values enter the binding or its emitted digest."""
    bind = db.get_bind()
    url = bind.url
    if bind.dialect.name == "postgresql":
        database, schema = db.execute(text("SELECT current_database(), current_schema()")).one()
        revisions = tuple(db.execute(text("SELECT version_num FROM alembic_version")).scalars())
        _need(revisions == (SCHEMA,), "database_schema_mismatch")
    elif bind.dialect.name == "sqlite":
        # Inert unit tests only; the operator CLI independently requires PG.
        database, schema, revisions = url.database, "main", (SCHEMA,)
    else:
        raise ReconciliationDenied("database_dialect_refused")
    return digest({"dialect": bind.dialect.name, "host": url.host, "port": url.port,
                   "database": database, "schema": schema, "revisions": revisions})


def _target(db: Session, receipt: NativeExpenseReceipt, *, lock: bool):
    model = CreditExecutionHold if receipt.kind == "paid_native" else FreeCoreReservation
    query = db.query(model).filter_by(id=receipt.target_id).populate_existing()
    row = (query.with_for_update() if lock else query).one_or_none()
    _need(row is not None, "expense_target_missing")
    return row


def _preimage(row, receipt: NativeExpenseReceipt) -> str:
    # Ownership may be erased concurrently. Its absence does not erase the
    # original operation's financial identity or grant a customer lookup path.
    base = {"kind": receipt.kind, "id": row.id, "route_id": row.route_id,
            "created_at": service._stamp(row.created_at).isoformat(),
            "reserved_minor": row.reserved_minor}
    if receipt.kind == "paid_native":
        base |= {"financial_group_id": row.financial_group_id,
                 "reservation_id": row.reservation_id, "native_reserved_minor": row.native_reserved_minor,
                 "snapshot_sha256": digest(row.snapshot), "funding_kind": row.funding_kind}
    else:
        base |= {"policy_version": row.policy_version, "funding_period_id": row.funding_period_id,
                 "operation": row.operation}
    return digest(base)


def _terminal(db: Session, row, receipt: NativeExpenseReceipt, terminal: TerminalWorkEvidence) -> None:
    created = service._stamp(row.created_at)
    _need(receipt.window_start <= created <= receipt.terminal_at, "expense_window_mismatch")
    if receipt.kind == "paid_native":
        _need(terminal.financial_group_id == row.financial_group_id
              and terminal.reservation_id == row.reservation_id, "native_execution_correlation_mismatch")
        expected = "delivered" if receipt.work_state == "completed" else "failed"
        _need(row.delivery_state == expected, "native_work_not_terminal")
    else:
        _need(terminal.financial_group_id is None and terminal.reservation_id == row.id,
              "native_execution_correlation_mismatch")
        request = db.query(FreeCoreRequest).filter_by(reservation_id=row.id).one_or_none()
        # For an erased account, terminal provider/worker evidence is the
        # explicitly reviewed authority: the owner-bound request is gone.
        _need((row.user_id is None and request is None)
              or (request is not None and request.state == "completed"
                  and receipt.work_state == "completed"), "native_work_not_terminal")


def _audit_id(receipt: NativeExpenseReceipt) -> str:
    return "aud_native_" + digest({"kind": receipt.kind, "target": receipt.target_id})[:53]


def _existing(db: Session, row, plan: ReconciliationPlan) -> AdminAuditEvent | None:
    audit = db.get(AdminAuditEvent, _audit_id(plan.receipt))
    amount = row.native_settled_minor if plan.receipt.kind == "paid_native" else row.settled_minor
    if audit is None:
        _need(amount is None, "expense_already_settled_without_receipt")
        return None
    _need(audit.action == ACTION and audit.target_type == plan.receipt.kind
          and audit.target_id == plan.receipt.target_id
          and audit.after_state.get("receipt_sha256") == digest(plan.receipt.model_dump(mode="json"))
          and audit.after_state.get("terminal") == plan.terminal.model_dump(mode="json")
          and audit.after_state.get("target_sha256") == plan.target_sha256
          and audit.after_state.get("database_sha256") == plan.database_sha256
          and audit.after_state.get("actual_minor") == plan.receipt.actual_minor == amount,
          "expense_receipt_conflict")
    if plan.receipt.kind == "free_core":
        _need(row.state == "settled" and row.settlement_receipt_sha256 == plan.receipt.source_sha256,
              "expense_receipt_conflict")
    return audit


def capture(db: Session, *, receipt: NativeExpenseReceipt, actor: Operator,
            terminal: TerminalWorkEvidence, nonce: str, now: datetime | None = None) -> ReconciliationPlan:
    """Read-only proposal. Caller retains and reviews the two evidence artifacts."""
    actor.validate()
    stamp = service._stamp(now)
    _need(receipt.reviewed_at <= stamp, "expense_review_in_future")
    row = _target(db, receipt, lock=False)
    plan = ReconciliationPlan(nonce=nonce, database_sha256=database_identity(db),
        target_sha256=_preimage(row, receipt), receipt=receipt, terminal=terminal,
        operator_email=actor.email, operator_native_identity_sha256=actor.native_identity_sha256,
        created_at=stamp, expires_at=stamp + timedelta(hours=24))
    _terminal(db, row, receipt, terminal)
    _existing(db, row, plan)
    return plan


def dry_run(db: Session, *, plan: ReconciliationPlan, now: datetime | None = None) -> dict:
    stamp = service._stamp(now)
    _need(plan.created_at <= stamp, "expense_plan_in_future")
    _need(database_identity(db) == plan.database_sha256, "expense_database_changed")
    row = _target(db, plan.receipt, lock=False)
    _need(_preimage(row, plan.receipt) == plan.target_sha256, "expense_target_changed")
    _terminal(db, row, plan.receipt, plan.terminal)
    audit = _existing(db, row, plan)
    # A late readback may prove the original transaction committed even after
    # its write authorization expired. Expiry never permits a new write.
    _need(audit is not None or stamp < plan.expires_at, "expense_plan_expired")
    return {"status": "already_reconciled" if audit else "ready_for_operator_review",
            "kind": plan.receipt.kind, "actual_minor": plan.receipt.actual_minor,
            "provider_costs_settled": False, "customer_credits_changed": False,
            "invoice_qualification_granted": False}


def apply(db: Session, *, plan: ReconciliationPlan, now: datetime | None = None) -> dict:
    """One transactional settlement; caller owns commit/rollback, never retries IO.

    Existing paid completion order is hold-row then global money locks; free
    reconciliation uses money then free-row. There is only one target per plan.
    Owner erasure may win before either lock and is allowed by the monetary pin.
    """
    stamp = service._stamp(now)
    _need(plan.created_at <= stamp < plan.expires_at, "expense_plan_expired")
    _need(database_identity(db) == plan.database_sha256, "expense_database_changed")
    if plan.receipt.kind == "free_core":
        admission.lock_money(db)
    row = _target(db, plan.receipt, lock=True)
    if plan.receipt.kind == "paid_native":
        admission.lock_money(db)
    _need(_preimage(row, plan.receipt) == plan.target_sha256, "expense_target_changed")
    _terminal(db, row, plan.receipt, plan.terminal)
    if _existing(db, row, plan) is not None:
        return {"status": "already_reconciled", "provider_costs_settled": False}
    if plan.receipt.kind == "paid_native":
        row.native_settled_minor = plan.receipt.actual_minor
        execution.refresh_hold(db, row, now=stamp)
    else:
        settle_measured_expense(db, reservation_id=row.id, actual_minor=plan.receipt.actual_minor,
                               receipt_sha256=plan.receipt.source_sha256, now=stamp)
    db.add(AdminAuditEvent(id=_audit_id(plan.receipt), actor_user_id=None,
        actor_email=plan.operator_email, action=ACTION, target_type=plan.receipt.kind,
        target_id=plan.receipt.target_id, reason="Reviewed complete native expense; provider costs remain independently protected",
        before_state={"native_expense_unsettled": True},
        after_state={"receipt_sha256": digest(plan.receipt.model_dump(mode="json")),
            "receipt": plan.receipt.model_dump(mode="json"), "actual_minor": plan.receipt.actual_minor,
            "terminal": plan.terminal.model_dump(mode="json"),
            "target_sha256": plan.target_sha256, "database_sha256": plan.database_sha256,
            "plan_sha256": digest(plan.model_dump(mode="json")),
            "operator_native_identity_sha256": plan.operator_native_identity_sha256,
            "invoice_qualification_granted": False},
        correlation_id=plan.nonce, created_at=stamp))
    db.flush()
    return {"status": "native_expense_reconciled", "provider_costs_settled": False,
            "customer_credits_changed": False, "invoice_qualification_granted": False}
