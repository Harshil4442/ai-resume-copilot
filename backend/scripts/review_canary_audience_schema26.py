"""Schema26 canary-review successor; no owner consent, grants or activation.

A new database-bound plan and companion audit distinguish this operator from
historical schema25 review plans. An old review without its exact schema26
companion conflicts; it is never adopted. The original runtime-compatible review
format, census checks and financial lock remain authoritative and unchanged.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
PLAN_VERSION = "canary-audience-plan-schema26-v1"
ACTION = "credit_canary_schema26_review"
FIELDS = {"version", "database_sha256", "review", "operator_native_identity_sha256"}


def check_envelope(plan):
    from app.domains.credits import canary

    canary.need(type(plan) is dict and set(plan) == FIELDS and plan["version"] == PLAN_VERSION)


def validate_plan(plan, *, database_sha: str, evidence_sha: str):
    from app.domains.credits import canary
    from app.domains.employer.testing_promotion import Operator

    check_envelope(plan)
    canary.need(plan["database_sha256"] == database_sha)
    review = canary.CanaryReview.model_validate(plan["review"])
    canary.need(review.review_evidence_sha256 == evidence_sha)
    Operator(review.reviewed_by, plan["operator_native_identity_sha256"]).validate()
    return review


def _proof(plan, review):
    from app.domains.common import payload_fingerprint

    return {"version": PLAN_VERSION, "database_sha256": plan["database_sha256"],
        "plan_sha256": payload_fingerprint(plan),
        "review_sha256": payload_fingerprint(review.model_dump(mode="json")),
        "operator_native_identity_sha256": plan["operator_native_identity_sha256"]}


def _existing(db, plan, review, *, required: bool):
    from app.domains.common import payload_fingerprint
    from app.domains.credits import canary
    from app.models import AdminAuditEvent

    row = db.get(AdminAuditEvent, canary._audit_id("review", review.id))
    proof = db.get(AdminAuditEvent, canary._audit_id("schema26-operator", review.id))
    if row is None and proof is None:
        canary.need(not required)
        return False
    # A historical review alone never becomes a schema26 approval by readback.
    canary.need(row is not None and proof is not None)
    digest = payload_fingerprint(review.model_dump(mode="json"))
    canary.need(row.action == canary.REVIEW_ACTION and row.target_type == "credit_canary"
        and row.target_id == review.id and row.actor_user_id is None
        and row.actor_email == review.reviewed_by
        and row.after_state == {"review": review.model_dump(mode="json"), "sha256": digest}
        and proof.action == ACTION and proof.target_type == "credit_canary"
        and proof.target_id == review.id and proof.actor_user_id is None
        and proof.actor_email == review.reviewed_by and proof.after_state == _proof(plan, review))
    return True


def record_review(db, plan, *, runtime, evidence_sha: str):
    from app.domains.common import payload_fingerprint
    from app.domains.credits import canary, reconciliation_schema26
    from app.models import AdminAuditEvent

    check_envelope(plan)  # Reject old plans before SQL, locks or a business write.
    review = validate_plan(plan, database_sha=reconciliation_schema26.database_identity(db), evidence_sha=evidence_sha)
    canary._lock(db)
    present = _existing(db, plan, review, required=False)
    # Reuses exact runtime/census/expiry checks and append-only original review.
    digest = canary.save_review(db, review, runtime=runtime)
    if not present:
        db.add(AdminAuditEvent(id=canary._audit_id("schema26-operator", review.id), actor_user_id=None,
            actor_email=review.reviewed_by, action=ACTION, target_type="credit_canary", target_id=review.id,
            reason="Explicit schema26 database-bound canary review; no owner consent or execution activation",
            before_state={}, after_state=_proof(plan, review), created_at=canary.stamp()))
        db.flush()
    canary.need(digest == payload_fingerprint(review.model_dump(mode="json")))
    return digest


def observe_review(db, plan, *, evidence_sha: str):
    from app.domains.credits import reconciliation_schema26

    check_envelope(plan)
    review = validate_plan(plan, database_sha=reconciliation_schema26.database_identity(db), evidence_sha=evidence_sha)
    _existing(db, plan, review, required=True)
    return _proof(plan, review)["review_sha256"]


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

        from app.domains.common import payload_fingerprint
        from app.domains.credits import canary, reconciliation_schema26
        from app.domains.credits.runtime import RuntimeConfig
        from app.domains.employer.testing_promotion import Operator
        from scripts.reconcile_native_expenses import _json, _write, private_bytes, verify_transport

        evidence_sha = hashlib.sha256(private_bytes(args.review_evidence, 16 * 1024 * 1024)).hexdigest()
        plan = None
        if args.phase == "capture":
            canary.need(args.spec is not None and not args.plan_sha256 and not args.accept_reviewed_audience)
        else:
            canary.need(args.spec is None and type(args.plan_sha256) is str
                and re.fullmatch(r"[a-f0-9]{64}", args.plan_sha256) is not None)
            raw = private_bytes(args.plan, 512_000)
            canary.need(hashlib.sha256(raw).hexdigest() == args.plan_sha256)
            plan = _json(raw)
            check_envelope(plan)
            canary.need(args.accept_reviewed_audience == (args.phase == "approve"))
        configured = (RuntimeConfig.model_validate_json(os.getenv("UNIFIED_CREDIT_RUNTIME_REVIEW_JSON", ""))
            if args.phase != "observe" else None)
        from app.database import SessionLocal

        with SessionLocal() as db:
            canary.need(db.get_bind().dialect.name == "postgresql")
            verify_transport(db, verify_live=False)
            if args.phase != "approve":
                db.connection().exec_driver_sql("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
            verify_transport(db)
            if args.phase == "capture":
                assert configured is not None
                database_sha = reconciliation_schema26.database_identity(db)
                spec = _json(private_bytes(args.spec, 32_768))
                canary.need(type(spec) is dict and set(spec) == {"id", "notice", "reviewed_by",
                    "operator_native_identity_sha256", "review_evidence_sha256",
                    "real_business_accounts_and_exclusions_reviewed", "expires_at", "traffic_started_at",
                    "selected_owner_ids", "excluded_owner_ids", "families"})
                Operator(spec["reviewed_by"], spec["operator_native_identity_sha256"]).validate()
                canary.need(spec["review_evidence_sha256"] == evidence_sha)
                current = canary.stamp()
                members = canary.census(db, census_at=current, excluded_owner_ids=tuple(spec["excluded_owner_ids"]))
                review = canary.CanaryReview.model_validate({
                    **{key: value for key, value in spec.items() if key != "operator_native_identity_sha256"},
                    "runtime_sha256": payload_fingerprint(configured.model_dump(mode="json")),
                    "notice_sha256": hashlib.sha256(spec["notice"].encode()).hexdigest(),
                    "census_at": current, "valid_from": current,
                    "members": [member.model_dump() for member in members]})
                canary.need(review.expires_at <= configured.policy.expires_at
                    and review.expires_at <= configured.budgets.expires_at)
                plan = {"version": PLAN_VERSION, "database_sha256": database_sha,
                    "review": review.model_dump(mode="json"),
                    "operator_native_identity_sha256": spec["operator_native_identity_sha256"]}
                # Capture cannot adopt an existing historical or conflicting review.
                _existing(db, plan, review, required=False)
                db.rollback()
                digest = _write(args.plan, plan)
                print(json.dumps({"status": "canary_audience_schema26_captured_readonly", "plan_sha256": digest,
                    "real_census_count": len(members), "selected_count": len(review.selected_owner_ids),
                    "database_mutations": 0, "consent_granted": False, "execution_enabled": False}))
                return 0
            assert plan is not None
            review = validate_plan(plan, database_sha=reconciliation_schema26.database_identity(db), evidence_sha=evidence_sha)
            if args.phase == "approve":
                assert configured is not None
                digest = record_review(db, plan, runtime=configured, evidence_sha=evidence_sha)
                commit_started = True
                db.commit()
                status = "canary_audience_schema26_review_recorded"
            else:
                digest = observe_review(db, plan, evidence_sha=evidence_sha)
                db.rollback()
                status = "same_canary_audience_schema26_review_observed"
            print(json.dumps({"status": status, "review_id": review.id, "review_sha256": digest,
                "consent_granted": False, "execution_enabled": False, "credits_granted": 0}))
            return 0
    except Exception:
        print(json.dumps({"status": "canary_audience_schema26_commit_unknown_observe_only" if commit_started
            else "canary_audience_schema26_unavailable", "execution_enabled": False,
            "new_audience_retry_authorized": False}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
