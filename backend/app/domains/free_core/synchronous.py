"""Funded deterministic learning, with free reuse independent of policy renewal."""
from __future__ import annotations

import json

from fastapi import HTTPException

from ... import models
from ...services.result_commit import begin_result_commit
from ..common import payload_fingerprint, public_id, utcnow
from ..credits import service as credits
from ..employer.admissions import lock_application_set
from . import policy, service


def curated_learning(db, *, user_id: int, context: dict, create):
    if not policy.enabled():
        return create()
    fingerprint = payload_fingerprint({"contract": "curated-learning-v1", "context": context})
    lock_application_set(db, user_id)
    credits._lock_owner(db, user_id)
    prior = db.query(models.AnalysisRun).filter_by(user_id=user_id,
        operation="curated_learning", input_fingerprint=fingerprint, result_purged_at=None).order_by(models.AnalysisRun.created_at.desc()).first()
    if prior is not None:
        if prior.status == "succeeded" and prior.result_payload is not None:
            return prior.result_payload
        if prior.status == "running":
            raise HTTPException(409, "This curated plan is already being prepared; check its status before retrying")
    stamp = utcnow()
    run = models.AnalysisRun(id=public_id("run"), user_id=user_id, operation="curated_learning",
        status="running", idempotency_key=public_id("curated"), input_fingerprint=fingerprint,
        input_payload={"match_id": context["match"]["id"]}, estimated_units=0, committed_units=0,
        usage_state="waived", generation_attempt_limit=0, attempt_count=1,
        created_at=stamp, updated_at=stamp, started_at=stamp)
    db.add(run)
    try:
        service.admit(db, user_id=user_id, operation="curated_learning", source_type="analysis_run",
            source_id=run.id, key=run.idempotency_key, fingerprint=fingerprint,
            input_bytes=len(json.dumps(context, default=str, ensure_ascii=False).encode()), attempts=1)
        identity = run.id
        db.commit()
    except Exception:
        db.rollback()
        raise
    try:
        result = create()
        begin_result_commit(db, user_id, identity)
        current = db.get(models.AnalysisRun, identity, populate_existing=True)
        current.result_payload, current.status = result, "succeeded"
        current.completed_at = current.updated_at = utcnow()
        service.complete(db, user_id=user_id, source_type="analysis_run", source_id=identity, result_id=identity)
        db.commit()
        return result
    except Exception:
        db.rollback()
        # Cost is still held. A failed local render does not prove zero expense.
        begin_result_commit(db, user_id, identity, allow_cancelled=True)
        current = db.get(models.AnalysisRun, identity, populate_existing=True)
        current.status, current.error_code = "failed", "curated_delivery_failed"
        current.completed_at = current.updated_at = utcnow()
        db.commit()
        raise
