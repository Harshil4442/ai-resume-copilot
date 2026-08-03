from __future__ import annotations

import os
from dataclasses import asdict, dataclass
from datetime import timedelta

from sqlalchemy.orm import Session

from ... import models
from ..common import utcnow
from ..notifications import deliver_pending_notifications, queue_due_reminder_emails


def _days(name: str, default: int, *, minimum: int = 1) -> int:
    try:
        return max(minimum, int(os.getenv(name, str(default))))
    except ValueError:
        return default


@dataclass(frozen=True)
class MaintenanceSummary:
    reminder_notifications_queued: int
    notifications: dict[str, object]
    analysis_inputs_purged: int
    analysis_results_purged: int
    model_events_deleted: int
    notification_rows_deleted: int

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def run_maintenance(db: Session) -> MaintenanceSummary:
    now = utcnow()
    queued = queue_due_reminder_emails(db)
    notification_summary = deliver_pending_notifications(db).to_dict()

    input_before = now - timedelta(days=_days("ANALYSIS_INPUT_RETENTION_DAYS", 30))
    result_before = now - timedelta(days=_days("ANALYSIS_RESULT_RETENTION_DAYS", 90))
    model_before = now - timedelta(days=_days("MODEL_TELEMETRY_RETENTION_DAYS", 365))
    notification_before = now - timedelta(days=_days("NOTIFICATION_RETENTION_DAYS", 90))

    inputs = (
        db.query(models.AnalysisRun)
        .filter(
            models.AnalysisRun.completed_at < input_before,
            models.AnalysisRun.input_purged_at.is_(None),
        )
        .update(
            {
                models.AnalysisRun.input_payload: {"purged": True},
                models.AnalysisRun.input_purged_at: now,
                models.AnalysisRun.updated_at: now,
            },
            synchronize_session=False,
        )
    )
    results = (
        db.query(models.AnalysisRun)
        .filter(
            models.AnalysisRun.completed_at < result_before,
            models.AnalysisRun.result_purged_at.is_(None),
        )
        .update(
            {
                models.AnalysisRun.result_payload: None,
                models.AnalysisRun.result_artifact_ref: None,
                models.AnalysisRun.result_purged_at: now,
                models.AnalysisRun.updated_at: now,
            },
            synchronize_session=False,
        )
    )
    model_events = db.query(models.ModelCallEvent).filter(
        models.ModelCallEvent.created_at < model_before
    ).delete(synchronize_session=False)
    notification_rows = db.query(models.NotificationOutbox).filter(
        models.NotificationOutbox.status.in_(["sent", "failed"]),
        models.NotificationOutbox.updated_at < notification_before,
    ).delete(synchronize_session=False)
    db.commit()
    return MaintenanceSummary(
        reminder_notifications_queued=queued,
        notifications=notification_summary,
        analysis_inputs_purged=inputs,
        analysis_results_purged=results,
        model_events_deleted=model_events,
        notification_rows_deleted=notification_rows,
    )
