"""Private exact-schema27 native expense capture, readback and settlement."""
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

from scripts.reconcile_native_expenses import OperatorRefused  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=("capture", "dry-run", "apply"))
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--plan-sha256")
    parser.add_argument("--receipt-review", type=Path)
    parser.add_argument("--accepted-reviewed-native-expense", action="store_true")
    args = parser.parse_args()
    writing = args.phase == "apply"
    capturing = args.phase == "capture"
    commit_started = False
    denied: tuple[type[Exception], ...] = (OperatorRefused,)
    try:
        from app.domains.career.evidence_write_fence import EvidenceWriteFenceDenied
        from scripts import native_expense_schema27 as native
        from scripts.notification_schema27_contract import Schema27Denied
        from scripts.reconcile_native_expenses import _write, private_bytes, verify_artifacts

        denied = (*denied, native.expense.ReconciliationDenied, Schema27Denied, EvidenceWriteFenceDenied)
        _need = native._need
        _need(args.accepted_reviewed_native_expense == writing, "explicit_receipt_acceptance_required")
        if capturing:
            _need(args.receipt_review is not None and args.plan_sha256 is None, "capture_arguments_invalid")
            receipt, actor, source, completion, terminal = native._receipt_review(args.receipt_review)
        else:
            _need(args.receipt_review is None and isinstance(args.plan_sha256, str)
                  and re.fullmatch(r"[a-f0-9]{64}", args.plan_sha256) is not None, "pinned_plan_required")
            raw = private_bytes(args.plan, 65536)
            _need(hashlib.sha256(raw).hexdigest() == args.plan_sha256, "native_plan_changed")
            plan, source, completion = native.decode_plan(raw)
            terminal = native.expense.TerminalWorkEvidence.model_validate(verify_artifacts(plan.receipt, source, completion))
            _need(terminal == plan.terminal, "native_terminal_evidence_changed")
        from app.database import SessionLocal

        with SessionLocal() as db:
            native._transport(db)
            if not writing:
                db.connection().exec_driver_sql("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
            if capturing:
                plan = native.capture(db, receipt=receipt, actor=actor, terminal=terminal, nonce=uuid4().hex)
                db.rollback()
                sha = _write(args.plan, {"plan": plan.model_dump(mode="json"),
                    "source_path": str(source), "completion_path": str(completion)})
                result = {"status": "native_expense_schema27_plan_captured", "plan_sha256": sha,
                    "database_mutations": 0, "invoice_qualification_granted": False}
            elif writing:
                result = native.apply(db, plan=plan)
                commit_started = True
                db.commit()
            else:
                result = native.dry_run(db, plan=plan)
                db.rollback()
        print(json.dumps(result, sort_keys=True))
        return 0
    except Exception as error:
        code = (str(error) if type(error) in denied and re.fullmatch(r"[a-z0-9_]{1,80}", str(error))
                else "native_expense_unavailable")
        print(json.dumps({"status": "native_expense_schema27_stopped_reconcile_same_plan", "code": code,
            "database_outcome_known": not commit_started, "replay_authorized": False}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
