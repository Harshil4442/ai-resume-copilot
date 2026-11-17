"""Private exact-schema27 native expense capture, readback and settlement."""
from __future__ import annotations

import hashlib
import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Literal

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.domains.credits import reconciliation as expense
from app.domains.employer.testing_promotion import Operator
from app.models import AdminAuditEvent
from scripts import notification_schema27_contract as schema27
from scripts.reconcile_native_expenses import (
    _json,
    private_bytes,
    verify_artifacts,
)

PLAN_VERSION = "native-expense-plan-schema27-v1"
ACTION = "native_expense_reconciled_schema27_v1"
_need = expense._need


class ReconciliationPlan(expense.ReconciliationPlan):
    version: Literal["native-expense-plan-schema27-v1"] = PLAN_VERSION  # type: ignore[assignment]


def _checked_plan(plan: ReconciliationPlan) -> ReconciliationPlan:
    _need(type(plan) is ReconciliationPlan and plan.version == PLAN_VERSION, "schema27_exact_plan_required")
    values = plan.model_dump(mode="json")
    # Check original nested values before JSON serialization can coerce them.
    for key, fields in (("receipt", expense.NativeExpenseReceipt.model_fields),
                        ("terminal", expense.TerminalWorkEvidence.model_fields)):
        nested = getattr(plan, key)
        values[key] = {name: getattr(nested, name) for name in fields}
    return ReconciliationPlan.model_validate(values)


def _transport(db: Session) -> None:
    bind = db.get_bind()
    _need(bind.dialect.name == "postgresql", "operator_requires_postgresql")
    url = bind.url
    _need(not os.getenv("SSLKEYLOGFILE") and url.query.get("sslmode") == "verify-full"
          and url.query.get("channel_binding", "prefer") in {"prefer", "require"},
          "operator_verified_tls_required")


def database_identity(db: Session) -> str:
    _transport(db)
    tls = db.execute(text("SELECT ssl,version,bits FROM pg_catalog.pg_stat_ssl "
        "WHERE pid=pg_catalog.pg_backend_pid()")).one_or_none()
    _need(tls is not None and tls[0] is True and tls[1] in {"TLSv1.2", "TLSv1.3"}
          and type(tls[2]) is int and tls[2] >= 128, "operator_verified_tls_required")
    control = db.execute(text(
        "SELECT p.oid,p.proowner,p.prosecdef,p.prosrc,l.lanname FROM pg_catalog.pg_proc p "
        "JOIN pg_catalog.pg_namespace n ON n.oid=p.pronamespace "
        "JOIN pg_catalog.pg_language l ON l.oid=p.prolang "
        "WHERE n.nspname='pg_catalog' AND p.proname='pg_control_system' AND p.pronargs=0 LIMIT 2")).all()
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
    database, database_oid, namespace, namespace_oid, role_oid, actor, session_actor, version, cluster = observed
    _need(type(cluster) is str and bool(cluster) and str(version).isdigit() and int(version) >= 140000
          and actor == session_actor, "database_native_identity_unavailable")
    _need(namespace == "public", "schema27_namespace_unavailable")
    # Shape includes the current marker and retained finance catalog/fence.
    # Rollout-only census, empty tables and serving role grants are separate.
    schema27.verify_shape(db.connection(), namespace_oid, namespace)
    url = db.get_bind().url
    return expense.digest({
        "plan_version": PLAN_VERSION, "schema": schema27.SCHEMA, "catalog_contract": schema27.CONTRACT,
        "host": url.host, "port": url.port, "database": database, "database_oid": database_oid,
        "namespace": namespace, "namespace_oid": namespace_oid, "role_oid": role_oid,
        "actor": actor, "session_actor": session_actor, "server_version_num": version,
        "cluster_sha256": hashlib.sha256(cluster.encode()).hexdigest(),
    })


def _after(plan: ReconciliationPlan) -> dict:
    return {"schema": schema27.SCHEMA, "plan_version": PLAN_VERSION,
        "receipt_sha256": expense.digest(plan.receipt.model_dump(mode="json")),
        "receipt": plan.receipt.model_dump(mode="json"), "actual_minor": plan.receipt.actual_minor,
        "terminal": plan.terminal.model_dump(mode="json"), "target_sha256": plan.target_sha256,
        "database_sha256": plan.database_sha256, "plan_sha256": expense.digest(plan.model_dump(mode="json")),
        "operator_native_identity_sha256": plan.operator_native_identity_sha256,
        "invoice_qualification_granted": False}


def _existing(db: Session, row, plan: ReconciliationPlan) -> bool:
    audit = db.get(AdminAuditEvent, expense._audit_id(plan.receipt))
    paid = plan.receipt.kind == "paid_native"
    amount = row.native_settled_minor if paid else row.settled_minor
    if audit is None:
        _need(amount is None, "expense_already_settled_without_receipt")
        return False
    _need(audit.action == ACTION and audit.target_type == plan.receipt.kind
          and audit.target_id == plan.receipt.target_id and audit.actor_user_id is None
          and audit.actor_email == plan.operator_email and audit.correlation_id == plan.nonce
          and audit.after_state == _after(plan) and amount == plan.receipt.actual_minor
          and (paid or (row.state == "settled" and row.settlement_receipt_sha256 == plan.receipt.source_sha256)),
          "schema27_expense_receipt_conflict")
    return True


def capture(db: Session, *, receipt: expense.NativeExpenseReceipt, actor: Operator,
            terminal: expense.TerminalWorkEvidence, nonce: str, now: datetime | None = None) -> ReconciliationPlan:
    actor.validate()
    stamp = expense.service._stamp(now)
    _need(receipt.reviewed_at <= stamp, "expense_review_in_future")
    identity = database_identity(db)
    row = expense._target(db, receipt, lock=False)
    plan = ReconciliationPlan(nonce=nonce, database_sha256=identity, target_sha256=expense._preimage(row, receipt),
        receipt=receipt, terminal=terminal, operator_email=actor.email,
        operator_native_identity_sha256=actor.native_identity_sha256, created_at=stamp,
        expires_at=stamp + timedelta(hours=24))
    plan = _checked_plan(plan)
    expense._terminal(db, row, plan.receipt, plan.terminal)
    _existing(db, row, plan)
    return plan


def _readback(db: Session, plan: ReconciliationPlan, *, lock: bool):
    _need(database_identity(db) == plan.database_sha256, "expense_database_changed")
    # Match the established order for each target: money→free or hold→money.
    if lock and plan.receipt.kind == "free_core":
        expense.admission.lock_money(db)
    row = expense._target(db, plan.receipt, lock=lock)
    if lock and plan.receipt.kind == "paid_native":
        expense.admission.lock_money(db)
    _need(expense._preimage(row, plan.receipt) == plan.target_sha256, "expense_target_changed")
    expense._terminal(db, row, plan.receipt, plan.terminal)
    return row, _existing(db, row, plan)


def _outcome(status: str) -> dict:
    return {"status": status, "provider_costs_settled": False,
        "customer_credits_changed": False, "invoice_qualification_granted": False}


def dry_run(db: Session, *, plan: ReconciliationPlan, now: datetime | None = None) -> dict:
    plan = _checked_plan(plan)
    stamp = expense.service._stamp(now)
    _need(plan.created_at <= stamp, "expense_plan_in_future")
    _, settled = _readback(db, plan, lock=False)
    _need(settled or stamp < plan.expires_at, "expense_plan_expired")
    return _outcome("already_reconciled" if settled else "ready_for_operator_review") | {
        "kind": plan.receipt.kind, "actual_minor": plan.receipt.actual_minor}


def apply(db: Session, *, plan: ReconciliationPlan, now: datetime | None = None) -> dict:
    plan = _checked_plan(plan)
    stamp = expense.service._stamp(now)
    _need(plan.created_at <= stamp < plan.expires_at, "expense_plan_expired")
    row, settled = _readback(db, plan, lock=True)
    if settled:
        return _outcome("already_reconciled")
    if plan.receipt.kind == "paid_native":
        row.native_settled_minor = plan.receipt.actual_minor
        expense.execution.refresh_hold(db, row, now=stamp)
    else:
        expense.settle_measured_expense(db, reservation_id=row.id, actual_minor=plan.receipt.actual_minor,
            receipt_sha256=plan.receipt.source_sha256, now=stamp)
    db.add(AdminAuditEvent(id=expense._audit_id(plan.receipt), actor_user_id=None,
        actor_email=plan.operator_email, action=ACTION, target_type=plan.receipt.kind,
        target_id=plan.receipt.target_id, reason="Reviewed schema27 native expense",
        before_state={"native_expense_unsettled": True}, after_state=_after(plan),
        correlation_id=plan.nonce, created_at=stamp))
    db.flush()
    return _outcome("native_expense_reconciled")


def decode_plan(raw: bytes) -> tuple[ReconciliationPlan, Path, Path]:
    value = _json(raw)
    _need(type(value) is dict and set(value) == {"plan", "source_path", "completion_path"},
          "native_plan_fields_invalid")
    _need(type(value["plan"]) is dict and value["plan"].get("version") == PLAN_VERSION, "schema27_exact_plan_required")
    return ReconciliationPlan.model_validate(value["plan"]), Path(value["source_path"]), Path(value["completion_path"])


def _receipt_review(path: Path):
    value = _json(private_bytes(path, 65536))
    _need(type(value) is dict and set(value) == {"receipt", "operator_email", "operator_native_identity_sha256",
          "source_path", "completion_path"}, "receipt_review_fields_invalid")
    receipt = expense.NativeExpenseReceipt.model_validate(value["receipt"])
    actor = Operator(value["operator_email"], value["operator_native_identity_sha256"])
    source, completion = Path(value["source_path"]), Path(value["completion_path"])
    terminal = expense.TerminalWorkEvidence.model_validate(verify_artifacts(receipt, source, completion))
    return receipt, actor, source, completion, terminal
