"""Capture the pre-roadmap HireWiz schema as an idempotent baseline.

Revision ID: 20260803_0001
Revises: None
Create Date: 2026-08-03
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20260803_0001"
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _legacy_metadata() -> sa.MetaData:
    metadata = sa.MetaData()

    sa.Table(
        "users",
        metadata,
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("email", sa.String(), nullable=True),
        sa.Column("password_hash", sa.String(), nullable=True, server_default=""),
        sa.Column("tier", sa.String(), nullable=True, server_default="free"),
        sa.Column("ai_credits", sa.Integer(), nullable=False, server_default="50"),
        sa.Column("premium_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("terms_accepted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("terms_version", sa.String(32), nullable=True),
        sa.Column("privacy_version", sa.String(32), nullable=True),
        sa.Column("age_confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("email", name="uq_users_email"),
    )

    sa.Table(
        "user_profiles",
        metadata,
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("full_name", sa.String(), nullable=True, server_default=""),
        sa.Column("headline", sa.String(), nullable=True, server_default=""),
        sa.Column("phone", sa.String(), nullable=True, server_default=""),
        sa.Column("location", sa.String(), nullable=True, server_default=""),
        sa.Column("linkedin", sa.String(), nullable=True, server_default=""),
        sa.Column("github", sa.String(), nullable=True, server_default=""),
        sa.Column("portfolio", sa.String(), nullable=True, server_default=""),
        sa.Column("target_role", sa.String(), nullable=True, server_default=""),
        sa.Column("preferred_job_type", sa.String(), nullable=True, server_default=""),
        sa.Column("preferred_location", sa.String(), nullable=True, server_default=""),
        sa.Column("years_experience", sa.Float(), nullable=True, server_default="0"),
        sa.Column("bio", sa.Text(), nullable=True, server_default=""),
        sa.Column("skills", sa.JSON(), nullable=True),
        sa.Column("education", sa.Text(), nullable=True, server_default=""),
        sa.Column("certifications", sa.Text(), nullable=True, server_default=""),
        sa.UniqueConstraint("user_id", name="uq_user_profiles_user_id"),
    )

    sa.Table(
        "resumes",
        metadata,
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("original_filename", sa.String(), nullable=True, server_default=""),
        sa.Column("raw_text", sa.Text(), nullable=True, server_default=""),
        sa.Column("skills", sa.JSON(), nullable=True),
        sa.Column("experience_years", sa.Float(), nullable=True, server_default="0"),
        sa.Column("sections", sa.JSON(), nullable=True),
        sa.Column("contact_info", sa.JSON(), nullable=True),
    )

    sa.Table(
        "job_matches",
        metadata,
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("resume_id", sa.Integer(), sa.ForeignKey("resumes.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("job_title", sa.String(), nullable=True, server_default=""),
        sa.Column("company", sa.String(), nullable=True, server_default=""),
        sa.Column("job_description", sa.Text(), nullable=True, server_default=""),
        sa.Column("required_skills", sa.JSON(), nullable=True),
        sa.Column("full_matches", sa.JSON(), nullable=True),
        sa.Column("partial_matches", sa.JSON(), nullable=True),
        sa.Column("true_gaps", sa.JSON(), nullable=True),
        sa.Column("match_score", sa.Float(), nullable=True, server_default="0"),
        sa.Column("fit_summary", sa.Text(), nullable=True, server_default=""),
        sa.Column("dimension_scores", sa.JSON(), nullable=True),
        sa.Column("skill_verification_rate", sa.Float(), nullable=True, server_default="0"),
        sa.Column("improvement_tips", sa.JSON(), nullable=True),
    )

    sa.Table(
        "skill_coverage",
        metadata,
        sa.Column("skill_from", sa.String(120), primary_key=True),
        sa.Column("skill_to", sa.String(120), primary_key=True),
        sa.Column("weight", sa.Float(), nullable=False),
        sa.Column("source", sa.String(20), nullable=True, server_default="llm"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
    )

    sa.Table(
        "payment_orders",
        metadata,
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("public_id", sa.String(64), nullable=False),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("provider", sa.String(32), nullable=False, server_default="razorpay"),
        sa.Column("provider_mode", sa.String(16), nullable=False, server_default="live"),
        sa.Column("provider_key_id", sa.String(120), nullable=True),
        sa.Column("provider_order_id", sa.String(120), nullable=True),
        sa.Column("provider_customer_id", sa.String(120), nullable=True),
        sa.Column("provider_subscription_id", sa.String(120), nullable=True),
        sa.Column("sku", sa.String(64), nullable=False),
        sa.Column("catalog_version", sa.String(64), nullable=False),
        sa.Column("billing_type", sa.String(32), nullable=False, server_default="one_time"),
        sa.Column("entitlement_kind", sa.String(40), nullable=False),
        sa.Column("entitlement_quantity", sa.Integer(), nullable=False),
        sa.Column("billing_country", sa.String(2), nullable=False, server_default="IN"),
        sa.Column("billing_country_confirmed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("gross_amount_minor", sa.Integer(), nullable=False),
        sa.Column("customer_tax_amount_minor", sa.Integer(), nullable=True),
        sa.Column("provider_fee_amount_minor", sa.Integer(), nullable=True),
        sa.Column("provider_fee_tax_minor", sa.Integer(), nullable=True),
        sa.Column("estimated_net_amount_minor", sa.Integer(), nullable=True),
        sa.Column("refunded_amount_minor", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("currency", sa.String(3), nullable=False, server_default="INR"),
        sa.Column("status", sa.String(32), nullable=False, server_default="initializing"),
        sa.Column("active_attempt_key", sa.String(160), nullable=True),
        sa.Column("client_confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("paid_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("refunded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("customer_deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("public_id", name="uq_payment_orders_public_id"),
        sa.UniqueConstraint("provider_order_id", name="uq_payment_orders_provider_order_id"),
        sa.UniqueConstraint("active_attempt_key", name="uq_payment_orders_active_attempt_key"),
    )

    sa.Table(
        "payment_transactions",
        metadata,
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("order_id", sa.Integer(), sa.ForeignKey("payment_orders.id"), nullable=False),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("provider_payment_id", sa.String(120), nullable=False),
        sa.Column("entity_type", sa.String(32), nullable=False, server_default="payment"),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("gross_amount_minor", sa.Integer(), nullable=False),
        sa.Column("customer_tax_amount_minor", sa.Integer(), nullable=True),
        sa.Column("provider_fee_amount_minor", sa.Integer(), nullable=True),
        sa.Column("provider_fee_tax_minor", sa.Integer(), nullable=True),
        sa.Column("estimated_net_amount_minor", sa.Integer(), nullable=True),
        sa.Column("refunded_amount_minor", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("payment_method", sa.String(32), nullable=True),
        sa.Column("instrument_international", sa.Boolean(), nullable=True),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("failed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("provider", "provider_payment_id", name="uq_payment_provider_payment"),
    )

    sa.Table(
        "payment_refunds",
        metadata,
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("order_id", sa.Integer(), sa.ForeignKey("payment_orders.id"), nullable=False),
        sa.Column("transaction_id", sa.Integer(), sa.ForeignKey("payment_transactions.id"), nullable=False),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("provider_refund_id", sa.String(120), nullable=False),
        sa.Column("amount_minor", sa.Integer(), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("provider", "provider_refund_id", name="uq_payment_provider_refund"),
    )

    sa.Table(
        "payment_events",
        metadata,
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("order_id", sa.Integer(), sa.ForeignKey("payment_orders.id"), nullable=True),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("provider_event_id", sa.String(160), nullable=False),
        sa.Column("event_type", sa.String(80), nullable=False),
        sa.Column("payload_sha256", sa.String(64), nullable=False),
        sa.Column("processing_status", sa.String(32), nullable=False, server_default="received"),
        sa.Column("error_code", sa.String(80), nullable=True),
        sa.Column("provider_created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("provider", "provider_event_id", name="uq_payment_provider_event"),
    )

    sa.Table(
        "entitlement_ledger",
        metadata,
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("source_order_id", sa.Integer(), sa.ForeignKey("payment_orders.id"), nullable=False),
        sa.Column("source_provider", sa.String(32), nullable=False),
        sa.Column("sku", sa.String(64), nullable=False),
        sa.Column("entitlement_kind", sa.String(40), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="active"),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("granted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("source_order_id", name="uq_entitlement_source_order"),
    )

    return metadata


def upgrade() -> None:
    # Existing production databases already contain these tables. `checkfirst`
    # lets Alembic adopt them while still creating a clean database from zero.
    _legacy_metadata().create_all(bind=op.get_bind(), checkfirst=True)


def downgrade() -> None:
    # This baseline adopts pre-Alembic production tables, so dropping it must
    # never delete customer or payment history.
    pass
