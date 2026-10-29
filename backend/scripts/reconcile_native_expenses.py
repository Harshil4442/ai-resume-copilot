"""Private operator tool: capture, inspect, then explicitly settle one native cost.

The configured database credential is the existing operator authority. The tool
does not grant a role or infer that supplied evidence is true. Capture/dry-run
are read-only; apply requires the reviewed plan hash and explicit acceptance.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import sys
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.database import SessionLocal  # noqa: E402
from app.domains.credits import reconciliation as native  # noqa: E402
from app.domains.employer.testing_promotion import Operator  # noqa: E402


def private_bytes(path: Path, maximum: int) -> bytes:
    if (not path.is_absolute() or path.parent.resolve() != path.parent
            or path.parent.stat().st_uid != os.getuid()
            or stat.S_IMODE(path.parent.stat().st_mode) & 0o077):
        raise native.ReconciliationDenied("private_artifact_required")
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, "rb") as stream:
        info = os.fstat(stream.fileno())
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                or stat.S_IMODE(info.st_mode) != 0o600 or not 0 < info.st_size <= maximum):
            raise native.ReconciliationDenied("private_artifact_required")
        raw = stream.read(maximum + 1)
    if len(raw) > maximum:
        raise native.ReconciliationDenied("private_artifact_required")
    return raw


def _pairs(items):
    result = {}
    for key, value in items:
        if key in result:
            raise native.ReconciliationDenied("ambiguous_plan_json")
        result[key] = value
    return result


def _invalid_constant(_):
    raise native.ReconciliationDenied("ambiguous_plan_json")


def _json(raw):
    return json.loads(raw, object_pairs_hook=_pairs, parse_constant=_invalid_constant)


def verify_artifacts(receipt, source: Path, completion: Path) -> native.TerminalWorkEvidence:
    source_bytes = private_bytes(source, 16 * 1024 * 1024)
    completion_bytes = private_bytes(completion, 65536)
    for raw, expected in ((source_bytes, receipt.source_sha256), (completion_bytes, receipt.completion_sha256)):
        if hashlib.sha256(raw).hexdigest() != expected:
            raise native.ReconciliationDenied("native_evidence_artifact_changed")
    return native.TerminalWorkEvidence.model_validate(_json(completion_bytes))


def _write(path: Path, value) -> str:
    if (not path.is_absolute() or path.parent.resolve() != path.parent
            or path.parent.stat().st_uid != os.getuid()
            or stat.S_IMODE(path.parent.stat().st_mode) != 0o700):
        raise native.ReconciliationDenied("private_plan_directory_required")
    raw = native.canonical(value) + b"\n"
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)
    return hashlib.sha256(raw).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=("capture", "dry-run", "apply"))
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--plan-sha256")
    parser.add_argument("--receipt-review", type=Path)
    parser.add_argument("--accepted-reviewed-native-expense", action="store_true")
    args = parser.parse_args()
    commit_started = False
    try:
        with SessionLocal() as db:
            if db.get_bind().dialect.name != "postgresql":
                raise native.ReconciliationDenied("operator_requires_postgresql")
            if args.phase != "apply":
                db.connection().exec_driver_sql("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
            if args.phase == "capture":
                if args.receipt_review is None or args.plan_sha256 or args.accepted_reviewed_native_expense:
                    raise native.ReconciliationDenied("capture_arguments_invalid")
                review = _json(private_bytes(args.receipt_review, 65536))
                if set(review) != {"receipt", "operator_email", "operator_native_identity_sha256", "source_path", "completion_path"}:
                    raise native.ReconciliationDenied("receipt_review_fields_invalid")
                receipt = native.NativeExpenseReceipt.model_validate(review["receipt"])
                source, completion = Path(review["source_path"]), Path(review["completion_path"])
                terminal = verify_artifacts(receipt, source, completion)
                plan = native.capture(db, receipt=receipt, actor=Operator(
                    review["operator_email"], review["operator_native_identity_sha256"]),
                    terminal=terminal, nonce=uuid4().hex)
                db.rollback()
                sha = _write(args.plan, {"plan": plan.model_dump(mode="json"),
                    "source_path": str(source), "completion_path": str(completion)})
                print(json.dumps({"status": "native_expense_plan_captured", "plan_sha256": sha,
                                  "database_mutations": 0, "invoice_qualification_granted": False}))
                return 0
            if (args.receipt_review is not None or not args.plan_sha256
                    or re.fullmatch(r"[a-f0-9]{64}", args.plan_sha256) is None):
                raise native.ReconciliationDenied("pinned_plan_required")
            raw = private_bytes(args.plan, 65536)
            if hashlib.sha256(raw).hexdigest() != args.plan_sha256:
                raise native.ReconciliationDenied("native_plan_changed")
            value = _json(raw)
            if set(value) != {"plan", "source_path", "completion_path"}:
                raise native.ReconciliationDenied("native_plan_fields_invalid")
            plan = native.ReconciliationPlan.model_validate(value["plan"])
            terminal = verify_artifacts(plan.receipt, Path(value["source_path"]), Path(value["completion_path"]))
            if terminal != plan.terminal:
                raise native.ReconciliationDenied("native_terminal_evidence_changed")
            if args.phase == "dry-run":
                if args.accepted_reviewed_native_expense:
                    raise native.ReconciliationDenied("dry_run_acceptance_refused")
                result = native.dry_run(db, plan=plan)
                db.rollback()
            else:
                if not args.accepted_reviewed_native_expense:
                    raise native.ReconciliationDenied("explicit_receipt_acceptance_required")
                result = native.apply(db, plan=plan)
                commit_started = True
                db.commit()
            print(json.dumps(result, sort_keys=True))
        return 0
    except Exception as error:
        # Exception text may contain database credentials or private invoices.
        # A failed/unknown commit is reconciled with dry-run of this same plan.
        code = (str(error) if type(error) is native.ReconciliationDenied
                and re.fullmatch(r"[a-z_]{1,80}", str(error)) else "native_expense_unavailable")
        print(json.dumps({"status": "native_expense_stopped_reconcile_same_plan", "code": code,
                          "database_outcome_known": not commit_started, "replay_authorized": False}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
