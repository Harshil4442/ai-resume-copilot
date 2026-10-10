"""Canonical opening claims, immutable batch quotes and durable safety admissions."""
import hashlib
import json
import uuid

import sqlalchemy as sa

from alembic import op

revision = "20261008_0009"
down_revision = "20261008_0008"
branch_labels = None
depends_on = None


def _fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()


def upgrade() -> None:
    op.add_column("employer_sources", sa.Column("employer_key", sa.String(120)))
    op.add_column("employer_sources", sa.Column("admission_policy", sa.JSON()))
    op.create_index("ix_employer_sources_employer_key", "employer_sources", ["employer_key"])
    op.add_column("employer_postings", sa.Column("opening_key", sa.String(64)))
    op.create_index("ix_employer_postings_opening_key", "employer_postings", ["opening_key"])
    op.add_column("employer_job_deliveries", sa.Column("opening_key", sa.String(64)))
    op.create_index("ix_employer_job_deliveries_opening_key", "employer_job_deliveries", ["opening_key"])
    op.create_table("employer_application_batches",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("idempotency_key", sa.String(160), nullable=False),
        sa.Column("input_fingerprint", sa.String(64), nullable=False),
        sa.Column("package_digest", sa.String(64), nullable=False),
        sa.Column("items", sa.JSON(), nullable=False),
        sa.Column("admission_snapshot", sa.JSON(), nullable=False),
        sa.Column("quoted_credits", sa.Integer(), nullable=False),
        sa.Column("max_total_credits", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("approved_at", sa.DateTime(timezone=True)),
        sa.Column("cancelled_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("user_id", "idempotency_key", name="uq_employer_batch_user_key"),
    )
    op.create_index("ix_employer_application_batches_user_id", "employer_application_batches", ["user_id"])
    for name, kind in (("opening_key", sa.String(64)), ("employer_key", sa.String(64)),
                       ("admission_snapshot", sa.JSON()), ("pricing_snapshot", sa.JSON()),
                       ("batch_id", sa.String(64))):
        op.add_column("employer_applications", sa.Column(name, kind))
    with op.batch_alter_table("employer_applications") as batch:
        batch.create_foreign_key("fk_employer_application_batch", "employer_application_batches", ["batch_id"], ["id"])
    for name in ("opening_key", "employer_key", "batch_id"):
        op.create_index("ix_employer_applications_" + name, "employer_applications", [name])
    op.create_table("employer_admissions",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("application_id", sa.String(64), sa.ForeignKey("employer_applications.id", ondelete="SET NULL")),
        sa.Column("employer_key", sa.String(64), nullable=False),
        sa.Column("opening_key", sa.String(64), nullable=False),
        sa.Column("active_key", sa.String(160), unique=True),
        sa.Column("credit_cost", sa.Integer(), nullable=False),
        sa.Column("policy_snapshot", sa.JSON(), nullable=False),
        sa.Column("state", sa.String(24), nullable=False),
        sa.Column("admitted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("possible_send_at", sa.DateTime(timezone=True)),
        sa.Column("settled_at", sa.DateTime(timezone=True)),
        sa.Column("release_reason", sa.String(80)),
        sa.UniqueConstraint("application_id", name="uq_employer_admission_application"),
    )
    op.create_index("ix_employer_admissions_user_id", "employer_admissions", ["user_id"])
    op.create_index("ix_employer_admission_user_window", "employer_admissions", ["user_id", "state", "possible_send_at"])
    op.create_index("ix_employer_admission_employer_window", "employer_admissions", ["user_id", "employer_key", "state", "possible_send_at"])
    _backfill()
    with op.batch_alter_table("employer_job_deliveries") as batch:
        batch.create_unique_constraint("uq_employer_delivery_user_opening", ["user_id", "opening_key"])


def _backfill():
    bind = op.get_bind()
    metadata = sa.MetaData()
    sources, postings, applications, deliveries, attempts, admissions = [
        sa.Table(name, metadata, autoload_with=bind) for name in (
            "employer_sources", "employer_postings", "employer_applications", "employer_job_deliveries",
            "employer_application_attempts", "employer_admissions")
    ]
    source_rows = {row.id: row for row in bind.execute(sa.select(sources))}
    employers = {row.id: _fingerprint({"platform": row.platform, "region": row.region, "tenant": row.board_token})
                 for row in source_rows.values()}
    openings = {}
    for row in bind.execute(sa.select(postings)):
        requisition = str(row.requisition_id or "").strip()
        source = source_rows[row.source_id].platform
        opening = _fingerprint({"employer": employers[row.source_id], "identity_kind": "requisition" if requisition else "external_posting",
                                "identity": requisition or str(row.external_id), "provider": None if requisition else source})
        openings[row.id] = (opening, employers[row.source_id])
        bind.execute(postings.update().where(postings.c.id == row.id).values(opening_key=opening))
    seen = set()
    for row in bind.execute(sa.select(deliveries).order_by(deliveries.c.created_at, deliveries.c.id)):
        opening = openings[row.posting_id][0]
        key = (row.user_id, opening)
        # Preserve historical money events, including any previously duplicated delivery.
        if key not in seen:
            bind.execute(deliveries.update().where(deliveries.c.id == row.id).values(opening_key=opening))
        seen.add(key)
    active, admission_active = set(), set()
    for row in bind.execute(sa.select(applications).order_by(applications.c.created_at, applications.c.id)):
        opening, employer = openings[row.posting_id]
        key = f"{row.user_id}:{opening}"
        values = {"opening_key": opening, "employer_key": employer}
        if row.active_key and key not in active:
            values["active_key"] = key
            active.add(key)
        bind.execute(applications.update().where(applications.c.id == row.id).values(**values))
        attempted_at = bind.execute(sa.select(sa.func.min(attempts.c.started_at)).where(attempts.c.application_id == row.id)).scalar_one()
        if row.status not in {"queued", "submitting", "unknown", "confirmed"} and not attempted_at:
            continue
        state = "reserved" if row.status == "queued" and not attempted_at else "unknown" if row.status in {"submitting", "unknown"} else "consumed"
        claim = key if row.active_key and key not in admission_active else None
        if claim:
            admission_active.add(key)
        bind.execute(admissions.insert().values(
            id="admission_" + uuid.uuid4().hex, user_id=row.user_id, application_id=row.id,
            employer_key=employer, opening_key=opening, active_key=claim,
            credit_cost=row.credit_cost, policy_snapshot={"legacy": True, "requires_fresh_review": True},
            state=state, admitted_at=row.created_at,
            possible_send_at=None if state == "reserved" else attempted_at or row.updated_at,
        ))
    # Legacy quotes remain null: they cannot acquire a new launch without fresh review.


def downgrade() -> None:
    op.drop_table("employer_admissions")
    for name in ("opening_key", "employer_key", "batch_id"):
        op.drop_index("ix_employer_applications_" + name, table_name="employer_applications")
    with op.batch_alter_table("employer_applications") as batch:
        batch.drop_constraint("fk_employer_application_batch", type_="foreignkey")
        for name in ("batch_id", "pricing_snapshot", "admission_snapshot", "employer_key", "opening_key"):
            batch.drop_column(name)
    op.drop_table("employer_application_batches")
    op.drop_index("ix_employer_job_deliveries_opening_key", table_name="employer_job_deliveries")
    with op.batch_alter_table("employer_job_deliveries") as batch:
        batch.drop_constraint("uq_employer_delivery_user_opening", type_="unique")
        batch.drop_column("opening_key")
    op.drop_index("ix_employer_postings_opening_key", table_name="employer_postings")
    op.drop_column("employer_postings", "opening_key")
    op.drop_index("ix_employer_sources_employer_key", table_name="employer_sources")
    op.drop_column("employer_sources", "admission_policy")
    op.drop_column("employer_sources", "employer_key")
