"""Capture/review an exact canary audience; never enroll an owner or open a flag.

Use the retained credential manager/environment for the existing database. A
private read-only capture is followed by an explicit immutable audit insertion.
An uncertain commit is observed using the same plan, never a new audience ID.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=("capture", "observe", "approve"))
    parser.add_argument("--spec", type=Path)
    parser.add_argument("--review-evidence", type=Path, required=True)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--plan-sha256")
    parser.add_argument("--accept-reviewed-audience", action="store_true")
    args = parser.parse_args()
    commit_started = False
    try:
        import os

        from app.database import SessionLocal
        from app.domains.common import payload_fingerprint
        from app.domains.credits import canary, reconciliation
        from app.domains.credits.runtime import RuntimeConfig
        from app.domains.employer.testing_promotion import Operator
        from scripts.reconcile_native_expenses import _json, _write, private_bytes, verify_transport

        evidence_sha = hashlib.sha256(private_bytes(args.review_evidence, 16 * 1024 * 1024)).hexdigest()
        configured = (RuntimeConfig.model_validate_json(os.getenv("UNIFIED_CREDIT_RUNTIME_REVIEW_JSON", ""))
                      if args.phase != "observe" else None)
        with SessionLocal() as db:
            canary.need(db.get_bind().dialect.name == "postgresql")
            verify_transport(db, verify_live=False)
            if args.phase != "approve":
                db.connection().exec_driver_sql("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
            verify_transport(db)
            database_sha = reconciliation.database_identity(db)
            if args.phase == "capture":
                canary.need(configured is not None)
                assert configured is not None
                canary.need(args.spec is not None and not args.plan_sha256 and not args.accept_reviewed_audience)
                spec = _json(private_bytes(args.spec, 32_768))
                canary.need(set(spec) == {"id", "notice", "reviewed_by", "operator_native_identity_sha256",
                    "review_evidence_sha256", "real_business_accounts_and_exclusions_reviewed", "expires_at",
                    "traffic_started_at", "selected_owner_ids", "excluded_owner_ids", "families"})
                Operator(spec["reviewed_by"], spec["operator_native_identity_sha256"]).validate()
                canary.need(spec["review_evidence_sha256"] == evidence_sha)
                current = canary.stamp()
                excluded = tuple(spec["excluded_owner_ids"])
                members = canary.census(db, census_at=current, excluded_owner_ids=excluded)
                review = canary.CanaryReview.model_validate({
                    **{key: value for key, value in spec.items() if key != "operator_native_identity_sha256"},
                    "runtime_sha256": payload_fingerprint(configured.model_dump(mode="json")),
                    "notice_sha256": hashlib.sha256(spec["notice"].encode()).hexdigest(),
                    "census_at": current, "valid_from": current,
                    "members": [member.model_dump() for member in members],
                })
                canary.need(review.expires_at <= configured.policy.expires_at
                    and review.expires_at <= configured.budgets.expires_at)
                plan = {"version": "canary-audience-plan-v1", "database_sha256": database_sha,
                    "review": review.model_dump(mode="json"),
                    "operator_native_identity_sha256": spec["operator_native_identity_sha256"]}
                db.rollback()
                digest = _write(args.plan, plan)
                print(json.dumps({"status": "canary_audience_captured_readonly", "plan_sha256": digest,
                    "real_census_count": len(members), "selected_count": len(review.selected_owner_ids),
                    "database_mutations": 0, "consent_granted": False, "execution_enabled": False}))
                return 0
            canary.need(args.spec is None and type(args.plan_sha256) is str)
            raw = private_bytes(args.plan, 512_000)
            canary.need(hashlib.sha256(raw).hexdigest() == args.plan_sha256)
            plan = _json(raw)
            canary.need(set(plan) == {"version", "database_sha256", "review", "operator_native_identity_sha256"}
                and plan["version"] == "canary-audience-plan-v1" and plan["database_sha256"] == database_sha)
            review = canary.CanaryReview.model_validate(plan["review"])
            canary.need(review.review_evidence_sha256 == evidence_sha)
            Operator(review.reviewed_by, plan["operator_native_identity_sha256"]).validate()
            if args.phase == "approve":
                canary.need(args.accept_reviewed_audience and configured is not None)
                digest = canary.save_review(db, review, runtime=configured)
                commit_started = True
                db.commit()
                status = "canary_audience_review_recorded"
            else:
                canary.need(not args.accept_reviewed_audience)
                from app.models import AdminAuditEvent

                row = db.get(AdminAuditEvent, canary._audit_id("review", review.id))
                digest = payload_fingerprint(review.model_dump(mode="json"))
                canary.need(row is not None and row.action == canary.REVIEW_ACTION
                    and row.after_state == {"review": review.model_dump(mode="json"), "sha256": digest})
                db.rollback()
                status = "same_canary_audience_review_observed"
            print(json.dumps({"status": status, "review_id": review.id, "review_sha256": digest,
                "consent_granted": False, "execution_enabled": False, "credits_granted": 0}))
            return 0
    except Exception:
        # Do not reflect database URLs, operator evidence, owner lists or errors.
        print(json.dumps({"status": "canary_audience_commit_unknown_observe_only" if commit_started
                          else "canary_audience_unavailable", "execution_enabled": False,
                          "new_audience_retry_authorized": False}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
