"""Independent candidate password enrollment projection; no legacy backfill.

Revision ID: 20261009_0011
Revises: 20261009_0010
"""
from alembic import op
import sqlalchemy as sa

revision = "20261009_0011"
down_revision = "20261009_0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table("candidate_lifetime_history", sa.Column("registration_id", sa.String(36), primary_key=True))
    op.create_table("candidate_password_accounts",
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("subject_uuid", sa.String(36), nullable=False, unique=True),
        sa.Column("account_binding_id", sa.String(36), nullable=False, unique=True),
        sa.Column("principal_sha256", sa.String(64), nullable=False),
        sa.Column("credential_sha256", sa.String(64), nullable=False),
        sa.Column("auth_generation", sa.BigInteger(), nullable=False),
        sa.Column("state", sa.String(24), nullable=False),
        sa.CheckConstraint("auth_generation > 0 AND auth_generation <= 9007199254740991", name="ck_candidate_auth_generation"),
        sa.CheckConstraint("state IN ('PENDING', 'ACTIVE', 'DELETE_DENIED')", name="ck_candidate_enrollment_state"))
    if op.get_bind().dialect.name == "postgresql":
        op.execute("""CREATE FUNCTION candidate_owner_immutable() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          IF ROW(NEW.user_id, NEW.subject_uuid, NEW.account_binding_id, NEW.principal_sha256)
             IS DISTINCT FROM ROW(OLD.user_id, OLD.subject_uuid, OLD.account_binding_id, OLD.principal_sha256) THEN
             RAISE EXCEPTION 'Candidate immutable lifetime ownership cannot change';
          END IF;
          IF NEW.auth_generation < OLD.auth_generation THEN
             RAISE EXCEPTION 'Candidate authentication generation cannot decrease';
          END IF;
          RETURN NEW;
        END $$""")
        op.execute("CREATE TRIGGER candidate_owner_immutable BEFORE UPDATE ON candidate_password_accounts FOR EACH ROW EXECUTE FUNCTION candidate_owner_immutable()")


def downgrade() -> None:
    if (op.get_bind().execute(sa.text("SELECT 1 FROM candidate_password_accounts LIMIT 1")).first()
            or op.get_bind().execute(sa.text("SELECT 1 FROM candidate_lifetime_history LIMIT 1")).first()):
        raise RuntimeError("Cannot discard candidate lifetime projections; disable enrollment and retain revision 0011")
    if op.get_bind().dialect.name == "postgresql":
        op.execute("DROP TRIGGER candidate_owner_immutable ON candidate_password_accounts")
        op.execute("DROP FUNCTION candidate_owner_immutable()")
    op.drop_table("candidate_password_accounts")
    op.drop_table("candidate_lifetime_history")
