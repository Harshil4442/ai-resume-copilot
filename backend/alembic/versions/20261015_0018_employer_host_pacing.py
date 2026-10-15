"""Primary-owned global host pacing; two bounded operational rows, no backfill."""
from alembic import op
import sqlalchemy as sa

revision = "20261015_0018"
down_revision = "20261014_0017"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("employer_host_pacing",
        sa.Column("host", sa.String(32), primary_key=True),
        sa.Column("authority", sa.String(32), nullable=False),
        sa.Column("server_started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("owner_token", sa.String(32), nullable=True),
        sa.Column("phase", sa.String(8), nullable=False),
        sa.Column("pending_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lease_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_observed_db_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("recovery_required", sa.Boolean, nullable=False),
        sa.CheckConstraint("host IN ('api.lever.co','api.eu.lever.co')", name="ck_host_pacing_host"),
        sa.CheckConstraint("length(authority) = 32 AND (owner_token IS NULL OR length(owner_token) = 32)", name="ck_host_pacing_tokens"),
        sa.CheckConstraint("phase IN ('idle','waiting','held')", name="ck_host_pacing_phase"),
        sa.CheckConstraint("(phase = 'idle' AND owner_token IS NULL AND pending_until IS NULL AND lease_until IS NULL) OR (phase = 'waiting' AND owner_token IS NOT NULL AND pending_until IS NOT NULL AND lease_until IS NULL) OR (phase = 'held' AND owner_token IS NOT NULL AND pending_until IS NULL AND lease_until IS NOT NULL)", name="ck_host_pacing_state"))


def downgrade():
    # Operators must pause/drain Lever before dropping/reseeding this authority.
    if op.get_bind().execute(sa.text("SELECT 1 FROM employer_host_pacing LIMIT 1")).first():
        raise RuntimeError("Pacing authority is populated; pause/drain and explicitly reseed before downgrade.")
    op.drop_table("employer_host_pacing")
