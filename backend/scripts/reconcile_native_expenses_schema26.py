"""Explicit schema26 expense successor; schema25 plans/audits are never adopted.

Uses the existing private artifact and verified-TLS utilities. No credential,
role, customer credit, sales flag or provider cost is granted by this tool.
An unknown commit is observed with this same pinned schema26 plan, never retried.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def decode_plan(raw: bytes):
    from app.domains.credits import reconciliation_schema26 as native
    from scripts.reconcile_native_expenses import _json

    value = _json(raw)
    native._need(type(value) is dict and set(value) == {"plan", "source_path", "completion_path"},
        "native_plan_fields_invalid")
    native._need(type(value["plan"]) is dict and value["plan"].get("version") == native.PLAN_VERSION,
        "schema26_exact_plan_required")
    return native.ReconciliationPlan.model_validate(value["plan"]), Path(value["source_path"]), Path(value["completion_path"])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=("capture", "dry-run", "apply"))
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--plan-sha256")
    parser.add_argument("--receipt-review", type=Path)
    parser.add_argument("--accepted-reviewed-native-expense", action="store_true")
    args = parser.parse_args()
    commit_started = False
    denied_type = None
    try:
        from app.domains.credits import reconciliation_schema26 as native
        from app.domains.employer.testing_promotion import Operator
        from scripts.reconcile_native_expenses import (
            _json,
            _write,
            private_bytes,
            verify_artifacts,
            verify_transport,
        )

        denied_type = native.ReconciliationDenied
        plan = source = completion = None
        if args.phase == "capture":
            native._need(args.receipt_review is not None and not args.plan_sha256
                and not args.accepted_reviewed_native_expense, "capture_arguments_invalid")
        else:
            native._need(args.receipt_review is None and type(args.plan_sha256) is str
                and re.fullmatch(r"[a-f0-9]{64}", args.plan_sha256) is not None, "pinned_plan_required")
            raw = private_bytes(args.plan, 65536)
            native._need(hashlib.sha256(raw).hexdigest() == args.plan_sha256, "native_plan_changed")
            plan, source, completion = decode_plan(raw)
            terminal = native.TerminalWorkEvidence.model_validate(verify_artifacts(plan.receipt, source, completion))
            native._need(terminal == plan.terminal, "native_terminal_evidence_changed")
            native._need(args.accepted_reviewed_native_expense == (args.phase == "apply"),
                "explicit_receipt_acceptance_required")
        # Cross-version plans fail above, before even constructing a DB session.
        from app.database import SessionLocal

        with SessionLocal() as db:
            native._need(db.get_bind().dialect.name == "postgresql", "operator_requires_postgresql")
            verify_transport(db, verify_live=False)
            if args.phase != "apply":
                db.connection().exec_driver_sql("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
            verify_transport(db)
            if args.phase == "capture":
                review = _json(private_bytes(args.receipt_review, 65536))
                native._need(type(review) is dict and set(review) == {"receipt", "operator_email",
                    "operator_native_identity_sha256", "source_path", "completion_path"}, "receipt_review_fields_invalid")
                receipt = native.NativeExpenseReceipt.model_validate(review["receipt"])
                source, completion = Path(review["source_path"]), Path(review["completion_path"])
                terminal = native.TerminalWorkEvidence.model_validate(verify_artifacts(receipt, source, completion))
                plan = native.capture(db, receipt=receipt, actor=Operator(review["operator_email"],
                    review["operator_native_identity_sha256"]), terminal=terminal, nonce=uuid4().hex)
                db.rollback()
                sha = _write(args.plan, {"plan": plan.model_dump(mode="json"),
                    "source_path": str(source), "completion_path": str(completion)})
                print(json.dumps({"status": "native_expense_schema26_plan_captured", "plan_sha256": sha,
                    "database_mutations": 0, "invoice_qualification_granted": False}))
                return 0
            assert plan is not None
            if args.phase == "dry-run":
                result = native.dry_run(db, plan=plan)
                db.rollback()
            else:
                result = native.apply(db, plan=plan)
                commit_started = True
                db.commit()
            print(json.dumps(result, sort_keys=True))
        return 0
    except Exception as error:
        from scripts.reconcile_native_expenses import OperatorRefused

        code = (str(error) if (type(error) is OperatorRefused or type(error) is denied_type)
            and re.fullmatch(r"[a-z_]{1,80}", str(error)) else "native_expense_unavailable")
        print(json.dumps({"status": "native_expense_schema26_stopped_reconcile_same_plan", "code": code,
            "database_outcome_known": not commit_started, "replay_authorized": False}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
