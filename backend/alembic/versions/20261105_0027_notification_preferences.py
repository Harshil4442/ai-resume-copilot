"""Add only the non-authoritative native-consent scheduling projection.

No row is backfilled and no native/mail feature is activated. Native consent,
cadence and unknown-send history remain independent of this disposable SQL table.
"""

import sqlalchemy as sa

from alembic import op

revision = "20261105_0027"
down_revision = "20261031_0026"
branch_labels = None
depends_on = None


def upgrade():
    connection = op.get_bind()
    namespace = (
        connection.scalar(sa.text("SELECT current_schema()"))
        if connection.dialect.name == "postgresql"
        else None
    )
    if connection.dialect.name == "postgresql" and (type(namespace) is not str or not namespace):
        raise RuntimeError("Exact current PostgreSQL schema is required before projection DDL")
    owner = f"{namespace}.users.id" if namespace else "users.id"
    op.create_table(
        "notification_preferences",
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("subject_uuid", sa.String(36), nullable=False),
        sa.Column("account_binding_id", sa.String(36), nullable=False),
        sa.Column("principal_sha256", sa.String(64), nullable=False),
        sa.Column("native_generation", sa.BigInteger(), nullable=False),
        sa.Column("native_head_sha256", sa.String(64), nullable=False),
        sa.Column("recipient_sha256", sa.String(64), nullable=False),
        sa.Column("policy_version", sa.String(80), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("next_due_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("operation_id", sa.String(36), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("user_id"),
        sa.ForeignKeyConstraint(
            ["user_id"], [owner], name="fk_notification_pref_owner", ondelete="CASCADE"
        ),
        sa.UniqueConstraint("subject_uuid", name="uq_notification_pref_subject"),
        sa.UniqueConstraint("account_binding_id", name="uq_notification_pref_binding"),
        sa.UniqueConstraint("operation_id", name="uq_notification_pref_operation"),
        sa.CheckConstraint(
            "native_generation BETWEEN 1 AND 9007199254740991",
            name="ck_notification_pref_generation",
        ),
        sa.CheckConstraint(
            "length(subject_uuid) = 36 AND length(account_binding_id) = 36 AND length(operation_id) = 36",
            name="ck_notification_pref_uuid_length",
        ),
        sa.CheckConstraint(
            "length(principal_sha256) = 64 AND length(native_head_sha256) = 64 AND length(recipient_sha256) = 64",
            name="ck_notification_pref_hash_length",
        ),
        sa.CheckConstraint(
            "length(policy_version) BETWEEN 1 AND 80", name="ck_notification_pref_policy"
        ),
        sa.CheckConstraint(
            "(enabled AND next_due_at IS NOT NULL) OR (NOT enabled AND next_due_at IS NULL)",
            name="ck_notification_pref_schedule",
        ),
        schema=namespace,
    )
    op.create_index(
        "ix_notification_preferences_due",
        "notification_preferences",
        ["enabled", "next_due_at", "user_id"],
        schema=namespace,
    )


def downgrade():
    raise RuntimeError(
        "Notification preference downgrade requires reviewed native-retirement evidence: "
        "retained consent, cadence and unknown-send obligations can outlive empty/restored SQL; "
        "SQL row absence is not proof that this table is safe to remove"
    )
