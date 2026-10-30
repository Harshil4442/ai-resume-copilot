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
        # SQLite supports local development; it provides no native fence proof.
        return
    install(connection, connection.scalar(sa.text("SELECT current_schema()")))


def downgrade():
    raise RuntimeError(
        "Evidence writer fence downgrade is unsupported: a reviewed safe replacement "
        "must retain old-writer denial before this fence can be removed"
    )
