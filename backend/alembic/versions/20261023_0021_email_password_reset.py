"""Single-use email password reset and legacy bearer revocation epoch."""
import sqlalchemy as sa

from alembic import op

revision = "20261023_0021"
down_revision = "20261023_0020"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("users", sa.Column("credential_epoch", sa.BigInteger(), nullable=False, server_default="0"))
    op.create_table("password_reset_tokens",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("token_sha256", sa.String(64), nullable=False, unique=True),
        sa.Column("email_sha256", sa.String(64), nullable=False),
        sa.Column("credential_sha256", sa.String(64), nullable=False),
        sa.Column("native_revision_sha256", sa.String(64), nullable=True),
        sa.Column("legacy_epoch", sa.BigInteger(), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("consumed_at", sa.DateTime(), nullable=True),
        sa.CheckConstraint("status IN ('PENDING','PROCESSING','COMPLETED','INVALIDATED','UNAVAILABLE')", name="ck_password_reset_status"))
    op.create_index("ix_password_reset_owner_created", "password_reset_tokens", ["user_id", "created_at"])


def downgrade():
    connection = op.get_bind()
    if connection.dialect.name == "postgresql":
        connection.execute(sa.text("LOCK TABLE users,password_reset_tokens IN SHARE ROW EXCLUSIVE MODE"))
    if connection.execute(sa.text("SELECT 1 FROM users WHERE credential_epoch<>0 LIMIT 1")).first() or connection.execute(
        sa.text("SELECT 1 FROM password_reset_tokens LIMIT 1")).first():
        raise RuntimeError("Password reset history/epochs must be retained; downgrade refused")
    op.drop_table("password_reset_tokens")
    op.drop_column("users", "credential_epoch")
