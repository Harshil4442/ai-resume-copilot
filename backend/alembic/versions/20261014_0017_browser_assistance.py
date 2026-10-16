"""Durable candidate-reviewed browser assistance; no money or native authority changes."""
from alembic import op
import sqlalchemy as sa

revision = "20261014_0017"
down_revision = "20261011_0016"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("browser_assistance_devices",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=True),
        sa.Column("extension_id", sa.String(32), nullable=False),
        sa.Column("public_key", sa.JSON(), nullable=False),
        sa.Column("key_sha256", sa.String(64), nullable=False, unique=True),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("pairing_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("state IN ('pending','active','revoked')", name="ck_browser_device_state"))
    op.create_index("ix_browser_device_owner_state", "browser_assistance_devices", ["user_id", "state"])
    op.create_index("ix_browser_device_key_created", "browser_assistance_devices", ["key_sha256", "created_at"])
    op.create_table("browser_assistance_reviews",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("device_id", sa.String(36), sa.ForeignKey("browser_assistance_devices.id", ondelete="CASCADE"), nullable=False),
        sa.Column("application_id", sa.String(64), sa.ForeignKey("employer_applications.id", ondelete="CASCADE"), nullable=False),
        *[sa.Column(name, sa.String(64), nullable=False) for name in ("package_digest", "artifact_sha256", "context_digest", "review_digest")],
        sa.Column("payload", sa.JSON(), nullable=False), sa.Column("state", sa.String(16), nullable=False),
        sa.Column("next_index", sa.Integer(), nullable=False), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False), sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("state IN ('draft','approved','running','filled','unknown','cancelled','expired')", name="ck_browser_review_state"),
        sa.CheckConstraint("next_index BETWEEN 0 AND 7", name="ck_browser_review_index"))
    op.create_index("ix_browser_review_owner_application", "browser_assistance_reviews", ["user_id", "application_id", "created_at"])
    op.create_index("ix_browser_review_expiry", "browser_assistance_reviews", ["expires_at"])
    op.create_table("browser_assistance_requests",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("device_id", sa.String(36), sa.ForeignKey("browser_assistance_devices.id", ondelete="CASCADE"), nullable=False),
        sa.Column("request_id", sa.String(36), nullable=False), sa.Column("operation", sa.String(24), nullable=False),
        sa.Column("request_sha256", sa.String(64), nullable=False),
        sa.Column("review_id", sa.String(36), sa.ForeignKey("browser_assistance_reviews.id", ondelete="CASCADE"), nullable=True),
        sa.Column("field_index", sa.Integer(), nullable=True), sa.Column("state", sa.String(16), nullable=False),
        sa.Column("response", sa.JSON(), nullable=False), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("device_id", "request_id", name="uq_browser_device_request"),
        sa.UniqueConstraint("review_id", "field_index", name="uq_browser_review_field"),
        sa.CheckConstraint("state IN ('completed','begun','filled','blocked','unknown')", name="ck_browser_request_state"))
    op.create_index("ix_browser_request_created", "browser_assistance_requests", ["created_at"])


def downgrade():
    connection = op.get_bind()
    for name in ("browser_assistance_devices", "browser_assistance_reviews", "browser_assistance_requests"):
        if connection.execute(sa.text(f"SELECT 1 FROM {name} LIMIT 1")).first():
            raise RuntimeError("Browser assistance records must be retained; downgrade refused.")
    op.drop_table("browser_assistance_requests")
    op.drop_table("browser_assistance_reviews")
    op.drop_table("browser_assistance_devices")
