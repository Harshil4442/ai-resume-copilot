"""Add the Career Workspace, execution, evidence, and usage platform.

Revision ID: 20260803_0002
Revises: 20260803_0001
Create Date: 2026-08-03
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20260803_0002"
down_revision: Union[str, Sequence[str], None] = "20260803_0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "opportunities",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("resume_id", sa.Integer(), sa.ForeignKey("resumes.id"), nullable=True),
        sa.Column("latest_match_id", sa.Integer(), sa.ForeignKey("job_matches.id"), nullable=True),
        sa.Column("latest_analysis_run_id", sa.String(64), nullable=True),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("company", sa.String(200), nullable=False, server_default=""),
        sa.Column("location", sa.String(200), nullable=False, server_default=""),
        sa.Column("source", sa.String(80), nullable=False, server_default="manual"),
        sa.Column("source_url", sa.Text(), nullable=True),
        sa.Column("job_description", sa.Text(), nullable=False, server_default=""),
        sa.Column("job_snapshot", sa.JSON(), nullable=False),
        sa.Column("stage", sa.String(32), nullable=False, server_default="saved"),
        sa.Column("priority", sa.String(16), nullable=False, server_default="medium"),
        sa.Column("compensation", sa.String(160), nullable=True),
        sa.Column("deadline_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("next_action", sa.String(240), nullable=True),
        sa.Column("notes", sa.Text(), nullable=False, server_default=""),
        sa.Column("outcome", sa.String(32), nullable=True),
        sa.Column("outcome_notes", sa.Text(), nullable=True),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("outcome_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_opportunities_user_id", "opportunities", ["user_id"])
    op.create_index("ix_opportunities_resume_id", "opportunities", ["resume_id"])
    op.create_index("ix_opportunities_latest_analysis_run_id", "opportunities", ["latest_analysis_run_id"])
    op.create_index("ix_opportunities_user_stage", "opportunities", ["user_id", "stage"])
    op.create_index("ix_opportunities_user_updated", "opportunities", ["user_id", "updated_at"])

    op.create_table(
        "analysis_runs",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("opportunity_id", sa.String(64), sa.ForeignKey("opportunities.id"), nullable=True),
        sa.Column("operation", sa.String(64), nullable=False),
        sa.Column("status", sa.String(24), nullable=False, server_default="queued"),
        sa.Column("idempotency_key", sa.String(160), nullable=False),
        sa.Column("input_fingerprint", sa.String(64), nullable=False),
        sa.Column("input_payload", sa.JSON(), nullable=False),
        sa.Column("result_payload", sa.JSON(), nullable=True),
        sa.Column("result_artifact_ref", sa.Text(), nullable=True),
        sa.Column("estimated_units", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("committed_units", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("usage_state", sa.String(24), nullable=False, server_default="pending"),
        sa.Column("provider", sa.String(80), nullable=True),
        sa.Column("model", sa.String(120), nullable=True),
        sa.Column("prompt_version", sa.String(64), nullable=True),
        sa.Column("error_code", sa.String(80), nullable=True),
        sa.Column("error_message", sa.String(500), nullable=True),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("cancel_requested", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("user_id", "idempotency_key", name="uq_analysis_run_user_idempotency"),
    )
    op.create_index("ix_analysis_runs_user_id", "analysis_runs", ["user_id"])
    op.create_index("ix_analysis_runs_opportunity_id", "analysis_runs", ["opportunity_id"])
    op.create_index("ix_analysis_runs_user_status", "analysis_runs", ["user_id", "status"])
    op.create_index("ix_analysis_runs_status_created", "analysis_runs", ["status", "created_at"])

    op.create_table(
        "usage_events",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("analysis_run_id", sa.String(64), sa.ForeignKey("analysis_runs.id"), nullable=True),
        sa.Column("entitlement_id", sa.Integer(), sa.ForeignKey("entitlement_ledger.id"), nullable=True),
        sa.Column("event_type", sa.String(24), nullable=False),
        sa.Column("amount", sa.Integer(), nullable=False),
        sa.Column("balance_after", sa.Integer(), nullable=False),
        sa.Column("idempotency_key", sa.String(200), nullable=False),
        sa.Column("source_type", sa.String(64), nullable=False, server_default="analysis_run"),
        sa.Column("source_id", sa.String(120), nullable=True),
        sa.Column("actor", sa.String(80), nullable=False, server_default="system"),
        sa.Column("reason", sa.String(240), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("user_id", "idempotency_key", name="uq_usage_event_user_idempotency"),
    )
    op.create_index("ix_usage_events_user_id", "usage_events", ["user_id"])
    op.create_index("ix_usage_events_analysis_run_id", "usage_events", ["analysis_run_id"])
    op.create_index("ix_usage_events_user_created", "usage_events", ["user_id", "created_at"])

    op.create_table(
        "application_events",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("opportunity_id", sa.String(64), sa.ForeignKey("opportunities.id"), nullable=False),
        sa.Column("event_type", sa.String(48), nullable=False),
        sa.Column("from_stage", sa.String(32), nullable=True),
        sa.Column("to_stage", sa.String(32), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("source", sa.String(40), nullable=False, server_default="user"),
        sa.Column("resume_version_id", sa.String(64), nullable=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_application_events_user_id", "application_events", ["user_id"])
    op.create_index("ix_application_events_opportunity_id", "application_events", ["opportunity_id"])
    op.create_index("ix_application_events_opportunity_occurred", "application_events", ["opportunity_id", "occurred_at"])

    op.create_table(
        "evidence_items",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("resume_id", sa.Integer(), sa.ForeignKey("resumes.id"), nullable=True),
        sa.Column("category", sa.String(48), nullable=False),
        sa.Column("title", sa.String(180), nullable=False),
        sa.Column("evidence_text", sa.Text(), nullable=False),
        sa.Column("metrics", sa.JSON(), nullable=False),
        sa.Column("skills", sa.JSON(), nullable=False),
        sa.Column("provenance", sa.String(40), nullable=False, server_default="user"),
        sa.Column("source_ref", sa.Text(), nullable=True),
        sa.Column("approval_state", sa.String(24), nullable=False, server_default="pending"),
        sa.Column("confidence", sa.String(16), nullable=False, server_default="user_provided"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_evidence_items_user_id", "evidence_items", ["user_id"])
    op.create_index("ix_evidence_items_resume_id", "evidence_items", ["resume_id"])
    op.create_index("ix_evidence_items_user_category", "evidence_items", ["user_id", "category"])

    op.create_table(
        "resume_versions",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("resume_id", sa.Integer(), sa.ForeignKey("resumes.id"), nullable=False),
        sa.Column("opportunity_id", sa.String(64), sa.ForeignKey("opportunities.id"), nullable=True),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("label", sa.String(160), nullable=False, server_default="Resume version"),
        sa.Column("structured_content", sa.JSON(), nullable=False),
        sa.Column("rendered_artifact_ref", sa.Text(), nullable=True),
        sa.Column("evidence_ids", sa.JSON(), nullable=False),
        sa.Column("generation_run_id", sa.String(64), sa.ForeignKey("analysis_runs.id"), nullable=True),
        sa.Column("approval_state", sa.String(24), nullable=False, server_default="draft"),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("resume_id", "version_number", name="uq_resume_version_number"),
    )
    op.create_index("ix_resume_versions_user_id", "resume_versions", ["user_id"])
    op.create_index("ix_resume_versions_resume_id", "resume_versions", ["resume_id"])
    op.create_index("ix_resume_versions_opportunity_id", "resume_versions", ["opportunity_id"])
    op.create_index("ix_resume_versions_user_created", "resume_versions", ["user_id", "created_at"])

    op.create_table(
        "reminders",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("opportunity_id", sa.String(64), sa.ForeignKey("opportunities.id"), nullable=True),
        sa.Column("reminder_type", sa.String(40), nullable=False, server_default="follow_up"),
        sa.Column("message", sa.String(300), nullable=False),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(24), nullable=False, server_default="scheduled"),
        sa.Column("delivery_channel", sa.String(24), nullable=False, server_default="in_app"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("dismissed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_reminders_user_id", "reminders", ["user_id"])
    op.create_index("ix_reminders_opportunity_id", "reminders", ["opportunity_id"])
    op.create_index("ix_reminders_user_due", "reminders", ["user_id", "due_at"])

    op.create_table(
        "opportunity_contacts",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("opportunity_id", sa.String(64), sa.ForeignKey("opportunities.id"), nullable=False),
        sa.Column("name", sa.String(160), nullable=False),
        sa.Column("role", sa.String(160), nullable=True),
        sa.Column("email", sa.String(320), nullable=True),
        sa.Column("profile_url", sa.Text(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_opportunity_contacts_user_id", "opportunity_contacts", ["user_id"])
    op.create_index("ix_opportunity_contacts_opportunity_id", "opportunity_contacts", ["opportunity_id"])

    op.create_table(
        "career_memory_entries",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("category", sa.String(48), nullable=False),
        sa.Column("memory_key", sa.String(120), nullable=False),
        sa.Column("value", sa.JSON(), nullable=False),
        sa.Column("provenance", sa.String(40), nullable=False, server_default="user"),
        sa.Column("source_ref", sa.Text(), nullable=True),
        sa.Column("approval_state", sa.String(24), nullable=False, server_default="approved"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("user_id", "memory_key", name="uq_career_memory_user_key"),
    )
    op.create_index("ix_career_memory_entries_user_id", "career_memory_entries", ["user_id"])
    op.create_index("ix_career_memory_user_category", "career_memory_entries", ["user_id", "category"])

    op.create_table(
        "model_call_events",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("analysis_run_id", sa.String(64), sa.ForeignKey("analysis_runs.id"), nullable=False),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("provider", sa.String(80), nullable=False),
        sa.Column("model", sa.String(120), nullable=False),
        sa.Column("prompt_version", sa.String(64), nullable=False),
        sa.Column("input_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("output_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("latency_ms", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("estimated_cost_micros", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("error_code", sa.String(80), nullable=True),
        sa.Column("cache_status", sa.String(24), nullable=True),
        sa.Column("fallback_from", sa.String(120), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_model_call_events_analysis_run_id", "model_call_events", ["analysis_run_id"])
    op.create_index("ix_model_call_events_user_id", "model_call_events", ["user_id"])
    op.create_index("ix_model_calls_run_created", "model_call_events", ["analysis_run_id", "created_at"])

    op.create_table(
        "prompt_versions",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("operation", sa.String(64), nullable=False),
        sa.Column("version", sa.String(64), nullable=False),
        sa.Column("template_checksum", sa.String(64), nullable=False),
        sa.Column("output_schema", sa.JSON(), nullable=False),
        sa.Column("model_config", sa.JSON(), nullable=False),
        sa.Column("evaluation_result", sa.JSON(), nullable=False),
        sa.Column("release_status", sa.String(24), nullable=False, server_default="draft"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("activated_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("operation", "version", name="uq_prompt_operation_version"),
    )
    op.create_index("ix_prompt_versions_operation", "prompt_versions", ["operation"])


def downgrade() -> None:
    op.drop_table("prompt_versions")
    op.drop_table("model_call_events")
    op.drop_table("career_memory_entries")
    op.drop_table("opportunity_contacts")
    op.drop_table("reminders")
    op.drop_table("resume_versions")
    op.drop_table("evidence_items")
    op.drop_table("application_events")
    op.drop_table("usage_events")
    op.drop_table("analysis_runs")
    op.drop_table("opportunities")
