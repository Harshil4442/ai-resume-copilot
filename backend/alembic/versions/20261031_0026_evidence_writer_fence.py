"""Fence unversioned evidence writers before admitting reviewed evidence writes.

The exclusive table lock drains transactions that already touched evidence.
Admitted old handlers that have not reached SQL encounter the committed trigger
later. The transaction-local marker identifies the reviewed new write adapters;
it is not an authorization boundary against arbitrary SQL or a privileged role.
"""

import sqlalchemy as sa

from alembic import op
from app.domains.career.evidence_fence_definition import install

revision = "20261031_0026"
down_revision = "20261028_0025"
branch_labels = None
depends_on = None


def upgrade():
    connection = op.get_bind()
    if connection.dialect.name != "postgresql":
        with op.batch_alter_table("password_reset_tokens") as batch:
            batch.add_column(sa.Column("purpose", sa.String(24), nullable=False, server_default="password_reset"))
            batch.create_check_constraint("ck_password_reset_purpose", "purpose IN ('password_reset','privacy_erasure')")
        # SQLite supports local development; it provides no native fence proof.
        return
    namespace = connection.scalar(sa.text("SELECT current_schema()"))
    install(connection, namespace)
    quoted = connection.dialect.identifier_preparer.quote(namespace)
    connection.execute(sa.text(f"LOCK TABLE {quoted}.password_reset_tokens IN ACCESS EXCLUSIVE MODE"))
    op.add_column("password_reset_tokens", sa.Column("purpose", sa.String(24), nullable=False,
        server_default="password_reset"), schema=namespace)
    op.create_check_constraint("ck_password_reset_purpose", "password_reset_tokens",
        "purpose IN ('password_reset','privacy_erasure')", schema=namespace)


def downgrade():
    raise RuntimeError(
        "Evidence writer fence downgrade is unsupported: a reviewed safe replacement "
        "must retain old-writer denial before this fence can be removed; live privacy "
        "tokens, outbox deliveries and unknown completion obligations also block safe removal"
    )
