"""Disjoint schema26 native-expense operator; historical schema25 stays closed.

The original receipt, terminal-work and financial business checks remain in
reconciliation. This successor issues its own exact plan and audit action. It
uses the original target's deterministic audit ID, so a settled schema25 target
conflicts rather than being relabelled or settled again. Unknown outcomes require
observation of the same schema26 plan. No customer grant or feature is enabled.
"""
from __future__ import annotations

import hashlib
import os
import re
from datetime import datetime, timedelta
from typing import Literal

from sqlalchemy import text
from sqlalchemy.orm import Session

from ...models import AdminAuditEvent
from ..career import evidence_fence_definition as definition
from ..career.evidence_write_fence import require_installed
from ..employer.testing_promotion import Operator
from . import reconciliation as original
from .reconciliation import NativeExpenseReceipt, TerminalWorkEvidence

SCHEMA = "20261031_0026"
PLAN_VERSION = "native-expense-plan-schema26-v1"
ACTION = "native_expense_reconciled_schema26_v1"
ReconciliationDenied = original.ReconciliationDenied
digest = original.digest
_need = original._need
_lock_money = original.admission.lock_money
_refresh_hold = original.execution.refresh_hold
_settle_measured_expense = original.settle_measured_expense


class ReconciliationPlan(original.ReconciliationPlan):
    # Deliberately disjoint wire type; never substitute this into original.apply.
    version: Literal["native-expense-plan-schema26-v1"] = PLAN_VERSION  # type: ignore[assignment]


def _checked_plan(plan: ReconciliationPlan) -> ReconciliationPlan:
    _need(type(plan) is ReconciliationPlan and plan.version == PLAN_VERSION,
        "schema26_exact_plan_required")
    # Revalidate model_copy/construct input, including every nested receipt field.
    value = plan.model_dump(mode="json")
    # Read raw model values: an integer serializer may coerce a copied bool to 1
    # before strict validation gets to see the malformed receipt.
    value["receipt"] = {name: getattr(plan.receipt, name) for name in NativeExpenseReceipt.model_fields}
    value["terminal"] = {name: getattr(plan.terminal, name) for name in TerminalWorkEvidence.model_fields}
    return ReconciliationPlan.model_validate(value)


def database_identity(db: Session) -> str:
    """Actual native identity, exact26 marker and installed canonical fence.

    Same native control-function and observed cluster/database/schema/role pins
    as the original operator. TLS configuration is refused before SQL, then the
    established connection is checked. SQLite and synthetic TLS bypasses cannot
    issue a schema26 operator identity.
    """
    bind = db.get_bind()
    url = bind.url
    _need(bind.dialect.name == "postgresql", "schema26_postgresql_required")
    _need(not os.getenv("SSLKEYLOGFILE") and url.query.get("sslmode") == "verify-full"
        and url.query.get("channel_binding", "prefer") in {"prefer", "require"},
        "operator_verified_tls_required")
    tls = db.execute(text("SELECT ssl,version,bits FROM pg_catalog.pg_stat_ssl "
        "WHERE pid=pg_catalog.pg_backend_pid()")).one_or_none()
    _need(tls is not None and tls[0] is True and tls[1] in {"TLSv1.2", "TLSv1.3"}
        and type(tls[2]) is int and tls[2] >= 128, "operator_verified_tls_required")
    control = db.execute(text(
        "SELECT p.oid,p.proowner,p.prosecdef,p.prosrc,l.lanname FROM pg_catalog.pg_proc p "
        "JOIN pg_catalog.pg_namespace n ON n.oid=p.pronamespace "
        "JOIN pg_catalog.pg_language l ON l.oid=p.prolang "
        "WHERE n.nspname='pg_catalog' AND p.proname='pg_control_system' "
        "AND p.pronargs=0 LIMIT 2")).all()
    _need([tuple(row) for row in control] == [(3441, 10, False, "pg_control_system", "internal")],
        "database_native_control_unavailable")
    observed = db.execute(text(
        "SELECT d.datname,d.oid,n.nspname,n.oid,r.oid,current_user,session_user,"
        "pg_catalog.current_setting('server_version_num'),"
        "(SELECT system_identifier::text FROM pg_catalog.pg_control_system()) "
        "FROM pg_catalog.pg_database d "
        "JOIN pg_catalog.pg_namespace n ON n.nspname=pg_catalog.current_schema() "
        "JOIN pg_catalog.pg_roles r ON r.rolname=current_user "
        "WHERE d.datname=pg_catalog.current_database()")).one()
    database, database_oid, schema, schema_oid, role_oid, actor, session_actor, version, cluster = observed
    _need(type(cluster) is str and bool(cluster) and str(version).isdigit()
        and int(version) >= 140000, "database_native_identity_unavailable")
    _need(type(schema) is str and re.fullmatch(r"[a-z_][a-z0-9_]{0,62}", schema) is not None,
        "schema26_namespace_unavailable")
    revisions = tuple(db.execute(text(f'SELECT version_num FROM "{schema}".alembic_version')).scalars())
    _need(revisions == (SCHEMA,), "schema26_exact_marker_required")
    require_installed(db.connection(), schema)
    native_identity = {"database_oid": database_oid, "schema_oid": schema_oid,
        "role_oid": role_oid, "actor": actor, "session_actor": session_actor,
        "server_version_num": version, "cluster_sha256": hashlib.sha256(cluster.encode()).hexdigest()}
    return digest({"version": PLAN_VERSION, "dialect": bind.dialect.name, "host": url.host,
        "port": url.port, "database": database, "schema": schema, "revisions": revisions,
        "native_identity": native_identity,
        "fence_body_sha256": hashlib.sha256(definition.FUNCTION_BODY.encode()).hexdigest()})


def _after(plan: ReconciliationPlan) -> dict:
    return {"schema": SCHEMA, "plan_version": PLAN_VERSION,
        "receipt_sha256": digest(plan.receipt.model_dump(mode="json")),
        "receipt": plan.receipt.model_dump(mode="json"), "actual_minor": plan.receipt.actual_minor,
        "terminal": plan.terminal.model_dump(mode="json"), "target_sha256": plan.target_sha256,
        "database_sha256": plan.database_sha256, "plan_sha256": digest(plan.model_dump(mode="json")),
        "operator_native_identity_sha256": plan.operator_native_identity_sha256,
        "invoice_qualification_granted": False}


def _existing(db: Session, row, plan: ReconciliationPlan):
    # This original ID is intentional: no new schema-specific ID may bypass a
    # settled historical target's immutable receipt/claim.
    audit = db.get(AdminAuditEvent, original._audit_id(plan.receipt))
    amount = row.native_settled_minor if plan.receipt.kind == "paid_native" else row.settled_minor
    if audit is None:
        _need(amount is None, "expense_already_settled_without_receipt")
        return None
    _need(audit.action == ACTION and audit.target_type == plan.receipt.kind
        and audit.target_id == plan.receipt.target_id and audit.actor_user_id is None
        and audit.actor_email == plan.operator_email and audit.correlation_id == plan.nonce
        and audit.after_state == _after(plan) and amount == plan.receipt.actual_minor,
        "schema26_expense_receipt_conflict")
    if plan.receipt.kind == "free_core":
        _need(row.state == "settled" and row.settlement_receipt_sha256 == plan.receipt.source_sha256,
            "schema26_expense_receipt_conflict")
    return audit


def capture(db: Session, *, receipt: NativeExpenseReceipt, actor: Operator,
            terminal: TerminalWorkEvidence, nonce: str, now: datetime | None = None) -> ReconciliationPlan:
    actor.validate()
    stamp = original.service._stamp(now)
    _need(receipt.reviewed_at <= stamp, "expense_review_in_future")
    identity = database_identity(db)
    row = original._target(db, receipt, lock=False)
    plan = ReconciliationPlan.model_validate({"version": PLAN_VERSION, "nonce": nonce,
        "database_sha256": identity, "target_sha256": original._preimage(row, receipt),
        "receipt": {name: getattr(receipt, name) for name in NativeExpenseReceipt.model_fields},
        "terminal": {name: getattr(terminal, name) for name in TerminalWorkEvidence.model_fields},
        "operator_email": actor.email, "operator_native_identity_sha256": actor.native_identity_sha256,
        "created_at": stamp, "expires_at": stamp + timedelta(hours=24)})
    original._terminal(db, row, plan.receipt, plan.terminal)
    _existing(db, row, plan)
    return plan


def dry_run(db: Session, *, plan: ReconciliationPlan, now: datetime | None = None) -> dict:
    plan = _checked_plan(plan)
    stamp = original.service._stamp(now)
    _need(plan.created_at <= stamp, "expense_plan_in_future")
    _need(database_identity(db) == plan.database_sha256, "expense_database_changed")
    row = original._target(db, plan.receipt, lock=False)
    _need(original._preimage(row, plan.receipt) == plan.target_sha256, "expense_target_changed")
    original._terminal(db, row, plan.receipt, plan.terminal)
    audit = _existing(db, row, plan)
    _need(audit is not None or stamp < plan.expires_at, "expense_plan_expired")
    return {"status": "already_reconciled" if audit else "ready_for_operator_review",
        "kind": plan.receipt.kind, "actual_minor": plan.receipt.actual_minor,
        "provider_costs_settled": False, "customer_credits_changed": False,
        "invoice_qualification_granted": False}


def apply(db: Session, *, plan: ReconciliationPlan, now: datetime | None = None) -> dict:
    """Original single-target lock order; caller alone owns commit and rollback."""
    plan = _checked_plan(plan)
    stamp = original.service._stamp(now)
    _need(plan.created_at <= stamp < plan.expires_at, "expense_plan_expired")
    _need(database_identity(db) == plan.database_sha256, "expense_database_changed")
    if plan.receipt.kind == "free_core":
        _lock_money(db)
    row = original._target(db, plan.receipt, lock=True)
    if plan.receipt.kind == "paid_native":
        _lock_money(db)
    _need(original._preimage(row, plan.receipt) == plan.target_sha256, "expense_target_changed")
    original._terminal(db, row, plan.receipt, plan.terminal)
    if _existing(db, row, plan) is not None:
        return {"status": "already_reconciled", "provider_costs_settled": False}
    if plan.receipt.kind == "paid_native":
        row.native_settled_minor = plan.receipt.actual_minor
        _refresh_hold(db, row, now=stamp)
    else:
        _settle_measured_expense(db, reservation_id=row.id, actual_minor=plan.receipt.actual_minor,
            receipt_sha256=plan.receipt.source_sha256, now=stamp)
    db.add(AdminAuditEvent(id=original._audit_id(plan.receipt), actor_user_id=None,
        actor_email=plan.operator_email, action=ACTION, target_type=plan.receipt.kind,
        target_id=plan.receipt.target_id, reason="Reviewed schema26 native expense; provider costs remain independently protected",
        before_state={"native_expense_unsettled": True}, after_state=_after(plan),
        correlation_id=plan.nonce, created_at=stamp))
    db.flush()
    return {"status": "native_expense_reconciled", "provider_costs_settled": False,
        "customer_credits_changed": False, "invoice_qualification_granted": False}
