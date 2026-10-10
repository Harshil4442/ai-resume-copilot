"""Employer domain persistence, separate from career-analysis accounting.

Service credits are prepaid money-backed units. Promotional analysis units and
Premium access never authorize a free employer-service operation.
"""
from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    case,
    false,
    select,
)
from sqlalchemy.ext.hybrid import hybrid_property
from sqlalchemy.orm import deferred, relationship

from ...database import Base
from ..common import utcnow


class EmployerCatalogueClock(Base):
    """One committed publication watermark; never a financial owner lock."""
    __tablename__ = "employer_catalogue_clock"
    __table_args__ = (CheckConstraint("id = 1 AND watermark >= 0", name="ck_catalogue_clock_singleton"),)
    id = Column(Integer, primary_key=True, default=1)
    watermark = Column(BigInteger, nullable=False, default=0, server_default="0")
    inventory_state = Column(String(16), nullable=False, default="sources", server_default="sources")
    source_cursor = Column(String(64), nullable=True)
    posting_cursor = Column(String(64), nullable=True)
    prune_cursor = Column(String(64), nullable=True)


class EmployerCatalogueSourceRevision(Base):
    __tablename__ = "employer_catalogue_source_revisions"
    __table_args__ = (
        CheckConstraint("valid_from >= 0 AND (valid_to IS NULL OR valid_to > valid_from)", name="ck_catalogue_source_interval"),
        Index("ix_catalogue_sources_snapshot", "source_id", "valid_from", "valid_to"),
    )
    source_id = Column(String(64), primary_key=True)
    valid_from = Column(BigInteger, primary_key=True)
    valid_to = Column(BigInteger, nullable=True)
    enabled = Column(Boolean, nullable=False)
    platform = Column(String(40), nullable=False)
    region = Column(String(16), nullable=False)
    board_token = Column(String(120), nullable=False)
    employer_key = Column(String(120), nullable=True)
    employer = Column(String(200), nullable=False)


class EmployerCatalogueContent(Base):
    """Immutable shared public body, stored once per exact content hash."""
    __tablename__ = "employer_catalogue_contents"
    id = Column(String(64), primary_key=True)
    description = deferred(Column(Text, nullable=False))
    preference_metadata = deferred(Column(JSON, nullable=True))
    public_fields = deferred(Column(JSON, nullable=False))


class EmployerCatalogueProjection(Base):
    """Derived facts and cold work are immutable per content/version pair."""
    __tablename__ = "employer_catalogue_projections"
    __table_args__ = (
        CheckConstraint("projection_state IN ('pending','preparing','ready')", name="ck_catalogue_projection_projection_state"),
        CheckConstraint("projection_state <> 'ready' OR (required_skills IS NOT NULL AND CAST(required_skills AS TEXT) <> 'null' AND scoring_warnings IS NOT NULL AND CAST(scoring_warnings AS TEXT) <> 'null' AND preference_valid IS NOT NULL AND preference_facts IS NOT NULL AND CAST(preference_facts AS TEXT) <> 'null')", name="ck_catalogue_projection_ready"),
        CheckConstraint("projection_offset >= 0", name="ck_catalogue_projection_offset"),
        CheckConstraint("length(CAST(required_skills AS TEXT)) <= 16384", name="ck_catalogue_projection_skills_size"),
        CheckConstraint("length(CAST(scoring_warnings AS TEXT)) <= 16384", name="ck_catalogue_projection_warnings_size"),
        CheckConstraint("length(CAST(preference_facts AS TEXT)) <= 8192", name="ck_catalogue_projection_facts_size"),
        # Capacity follows the finite normalized preference contract, not raw input.
        CheckConstraint("length(CAST(projection_checkpoint AS TEXT)) <= 3145728", name="ck_catalogue_projection_checkpoint_size"),
    )
    content_id = Column(String(64), ForeignKey("employer_catalogue_contents.id"), primary_key=True)
    projection_version = Column(String(160), primary_key=True)
    projection_state = Column(String(16), nullable=False, default="pending", server_default="pending")
    required_skills = Column(JSON, nullable=True)
    scoring_warnings = Column(JSON, nullable=True)
    preference_valid = Column(Boolean, nullable=True)
    preference_provider = Column(String(40), nullable=True)
    preference_facts = Column(JSON, nullable=True)
    projection_phase = Column(String(16), nullable=False, default="description", server_default="description")
    projection_offset = Column(BigInteger, nullable=False, default=0, server_default="0")
    projection_checkpoint = Column(JSON, nullable=True)
    lease_token = Column(String(64), nullable=True)
    lease_until = Column(DateTime(timezone=True), nullable=True)


class EmployerCataloguePostingRevision(Base):
    __tablename__ = "employer_catalogue_posting_revisions"
    __table_args__ = (
        CheckConstraint("valid_from >= 0 AND (valid_to IS NULL OR valid_to > valid_from)", name="ck_catalogue_posting_interval"),
        Index("ix_catalogue_postings_snapshot", "posting_id", "valid_from", "valid_to"),
        Index("ix_catalogue_postings_open_checked", "is_open", "last_checked_at", "posting_id"),
    )
    posting_id = Column(String(64).with_variant(String(64, collation="C"), "postgresql"), primary_key=True)
    valid_from = Column(BigInteger, primary_key=True)
    valid_to = Column(BigInteger, nullable=True)
    content_id = Column(String(64), ForeignKey("employer_catalogue_contents.id"), nullable=False)
    source_id = Column(String(64), nullable=False)
    external_id = Column(String(160), nullable=False)
    requisition_id = Column(String(160), nullable=True)
    title = Column(String(300), nullable=False)
    employer = Column(String(200), nullable=False)
    location = Column(String(400), nullable=False)
    remote = Column(Boolean, nullable=False)
    is_open = Column(Boolean, nullable=False)
    publication_at = Column(DateTime(timezone=True), nullable=True)
    first_seen_at = Column(DateTime(timezone=True), nullable=False)
    last_checked_at = Column(DateTime(timezone=True), nullable=False)
    content_sha256 = Column(String(64), nullable=False)


class EmployerCatalogueResumeBinding(Base):
    """A monotonic fence for resume skill/owner edits, including edit then restore."""
    __tablename__ = "employer_catalogue_resume_bindings"
    __table_args__ = (CheckConstraint("revision >= 1", name="ck_catalogue_resume_binding_revision"),)
    resume_id = Column(Integer, ForeignKey("resumes.id", ondelete="CASCADE", onupdate="CASCADE"), primary_key=True)
    revision = Column(BigInteger, nullable=False, default=1, server_default="1")


class EmployerSearchPreparation(Base):
    __tablename__ = "employer_search_preparations"
    __table_args__ = (
        UniqueConstraint("user_id", "idempotency_key", name="uq_search_preparation_owner_key"),
        UniqueConstraint("id", "user_id", name="uq_search_preparation_owner"),
        CheckConstraint("state IN ('accepting','pending','scanning','classifying','selecting','completed','failed','cancelled')", name="ck_search_preparation_state"),
        CheckConstraint("resume_offset >= 0 AND (resume_checkpoint IS NULL OR length(CAST(resume_checkpoint AS TEXT)) <= 16384)", name="ck_search_preparation_resume_checkpoint"),
        CheckConstraint("state = 'accepting' OR resume_source_revision IS NOT NULL", name="ck_search_preparation_resume_revision"),
        CheckConstraint("resume_source_revision IS NULL OR resume_source_revision >= 1", name="ck_search_preparation_resume_revision_positive"),
        CheckConstraint("watermark >= 0 AND desired_count BETWEEN 1 AND 100", name="ck_search_preparation_bounds"),
        Index("ix_search_preparation_active_pin", "state", "watermark", "id"),
        Index("ix_search_preparation_owner_created", "user_id", "created_at"),
        Index("ix_search_preparation_owner_lease", "user_id", "lease_until", "id"),
    )
    id = Column(String(64), primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    resume_id = Column(Integer, ForeignKey("resumes.id", ondelete="CASCADE"), nullable=False)
    reservation_id = Column(String(64), ForeignKey("service_credit_reservations.id", ondelete="CASCADE"), nullable=False)
    idempotency_key = Column(String(160), nullable=False)
    input_fingerprint = Column(String(64), nullable=False)
    query = deferred(Column(JSON, nullable=False))
    worker_query = Column(JSON, nullable=False)
    projection_version = Column(String(160), nullable=False)
    accepted_quote = Column(JSON, nullable=False)
    resume_fingerprint = Column(String(64), nullable=False)
    resume_source_revision = Column(BigInteger, nullable=True)
    resume_skills = Column(JSON, nullable=False)
    resume_offset = Column(BigInteger, nullable=False, default=0, server_default="0")
    resume_checkpoint = Column(JSON, nullable=True)
    resume_ready = Column(Boolean, nullable=False, default=False, server_default=false())
    as_of = Column(DateTime(timezone=True), nullable=False)
    watermark = Column(BigInteger, nullable=False)
    max_source_age_hours = Column(Integer, nullable=False)
    desired_count = Column(Integer, nullable=False)
    state = Column(String(16), nullable=False, default="pending", server_default="pending")
    posting_cursor = Column(String(64), nullable=True)
    classification_cursor = Column(String(64), nullable=True)
    history_generation = Column(BigInteger, nullable=True)
    candidates_before_preferences = Column(BigInteger, nullable=False, default=0, server_default="0")
    known_preference_conflicts = Column(BigInteger, nullable=False, default=0, server_default="0")
    candidates_with_unknown_preferences = Column(BigInteger, nullable=False, default=0, server_default="0")
    eligible_candidates = Column(BigInteger, nullable=False, default=0, server_default="0")
    work_generation = Column(BigInteger, nullable=False, default=1, server_default="1")
    lease_token = Column(String(64), nullable=True)
    lease_until = Column(DateTime(timezone=True), nullable=True)
    failure_reason = Column(String(64), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)
    completed_at = Column(DateTime(timezone=True), nullable=True)


class EmployerCatalogueSnapshot(Base):
    """Private native JSON assembly; a completed search publishes a proven pointer."""
    __tablename__ = "employer_catalogue_snapshots"
    __table_args__ = (
        UniqueConstraint("id", "user_id", name="uq_catalogue_snapshot_owner"),
        ForeignKeyConstraint(["preparation_id", "user_id"], ["employer_search_preparations.id", "employer_search_preparations.user_id"], name="fk_catalogue_snapshot_preparation_owner", ondelete="CASCADE"),
        CheckConstraint("state IN ('building','proven')", name="ck_catalogue_snapshot_state"),
        CheckConstraint("item_count BETWEEN 0 AND 100 AND history_generation >= 0", name="ck_catalogue_snapshot_bounds"),
        CheckConstraint("state <> 'proven' OR (items IS NOT NULL AND CAST(items AS TEXT) <> 'null' AND snapshot_digest IS NOT NULL AND length(snapshot_digest) = 64)", name="ck_catalogue_snapshot_proven"),
        Index("ix_catalogue_snapshot_preparation", "preparation_id", "history_generation", "created_at", "id"),
    )
    id = Column(String(64), primary_key=True)
    user_id = Column(Integer, nullable=False)
    preparation_id = Column(String(64), nullable=False)
    query = deferred(Column(JSON, nullable=False))
    items = deferred(Column(JSON, nullable=True))
    state = Column(String(16), nullable=False, default="building", server_default="building")
    item_count = Column(Integer, nullable=False, default=0, server_default="0")
    snapshot_digest = Column(String(64), nullable=True)
    history_generation = Column(BigInteger, nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)


class EmployerCatalogueSnapshotItem(Base):
    """Bounded authority fields plus a database-owned original public item body."""
    __tablename__ = "employer_catalogue_snapshot_items"
    __table_args__ = (
        ForeignKeyConstraint(["snapshot_id", "user_id"], ["employer_catalogue_snapshots.id", "employer_catalogue_snapshots.user_id"], name="fk_catalogue_snapshot_item_owner", ondelete="CASCADE"),
        ForeignKeyConstraint(["posting_id", "posting_revision"], ["employer_catalogue_posting_revisions.posting_id", "employer_catalogue_posting_revisions.valid_from"], name="fk_catalogue_snapshot_item_posting_revision"),
        ForeignKeyConstraint(["source_id", "source_revision"], ["employer_catalogue_source_revisions.source_id", "employer_catalogue_source_revisions.valid_from"], name="fk_catalogue_snapshot_item_source_revision"),
        Index("ix_catalogue_snapshot_item_posting_revision", "posting_id", "posting_revision"),
        Index("ix_catalogue_snapshot_item_source_revision", "source_id", "source_revision"),
        CheckConstraint("ordinal BETWEEN 0 AND 99 AND unit_price >= 0", name="ck_catalogue_snapshot_item_bounds"),
        CheckConstraint("proof_state IN ('prepared','proven')", name="ck_catalogue_snapshot_item_proof_state"),
        CheckConstraint("length(item_digest) = 64 AND length(CAST(identity_proof AS TEXT)) <= 16384", name="ck_catalogue_snapshot_item_proof_size"),
    )
    snapshot_id = Column(String(64), primary_key=True)
    ordinal = Column(Integer, primary_key=True)
    user_id = Column(Integer, nullable=False)
    posting_id = Column(String(64).with_variant(String(64, collation="C"), "postgresql"), nullable=False)
    posting_revision = Column(BigInteger, nullable=False)
    source_id = Column(String(64), nullable=False)
    source_revision = Column(BigInteger, nullable=False)
    opening_key = Column(String(64), nullable=False)
    seen = Column(Boolean, nullable=False)
    unit_price = Column(Integer, nullable=False)
    item_digest = Column(String(64), nullable=False)
    identity_proof = Column(JSON, nullable=False)
    body = deferred(Column(JSON, nullable=False))
    proof_state = Column(String(16), nullable=False, default="prepared", server_default="prepared")


class EmployerCatalogueWinner(Base):
    __tablename__ = "employer_catalogue_winners"
    __table_args__ = (
        ForeignKeyConstraint(["posting_id", "posting_revision"], ["employer_catalogue_posting_revisions.posting_id", "employer_catalogue_posting_revisions.valid_from"], name="fk_catalogue_winner_posting_revision"),
        ForeignKeyConstraint(["source_id", "source_revision"], ["employer_catalogue_source_revisions.source_id", "employer_catalogue_source_revisions.valid_from"], name="fk_catalogue_winner_source_revision"),
        Index("ix_catalogue_winner_posting_revision", "posting_id", "posting_revision"),
        Index("ix_catalogue_winner_source_revision", "source_id", "source_revision"),
        CheckConstraint("score_tenths BETWEEN 0 AND 1000", name="ck_catalogue_winner_score"),
        CheckConstraint("length(CAST(fit AS TEXT)) <= 16384 AND length(CAST(preference_evaluation AS TEXT)) <= 8192", name="ck_catalogue_winner_result_size"),
        Index("ix_catalogue_winner_classify", "preparation_id", "opening_key"),
    )
    preparation_id = Column(String(64), ForeignKey("employer_search_preparations.id", ondelete="CASCADE"), primary_key=True)
    opening_key = Column(String(64), primary_key=True)
    posting_id = Column(String(64).with_variant(String(64, collation="C"), "postgresql"), nullable=False)
    posting_revision = Column(BigInteger, nullable=False)
    source_id = Column(String(64), nullable=False)
    source_revision = Column(BigInteger, nullable=False)
    score_tenths = Column(Integer, nullable=False)
    title_hit = Column(Boolean, nullable=False)
    fit = Column(JSON, nullable=False)
    preference_evaluation = Column(JSON, nullable=False)
    seen = Column(Boolean, nullable=True)
    classification_generation = Column(BigInteger, nullable=True)
    stale = Column(Boolean, nullable=False, default=False, server_default=false())
    stale_reason = Column(String(64), nullable=True)


Index("ix_catalogue_winner_selection", EmployerCatalogueWinner.preparation_id,
      EmployerCatalogueWinner.classification_generation, EmployerCatalogueWinner.stale,
      EmployerCatalogueWinner.seen, EmployerCatalogueWinner.score_tenths.desc(),
      EmployerCatalogueWinner.title_hit.desc(), EmployerCatalogueWinner.posting_id)


class EmployerSource(Base):
    __tablename__ = "employer_sources"
    __table_args__ = (
        UniqueConstraint("platform", "region", "board_token", name="uq_employer_source_tenant"),
        Index("ix_employer_sources_due", "enabled", "next_refresh_at"),
    )
    id = Column(String(64), primary_key=True)
    employer = Column(String(200), nullable=False)
    # Operator-verified identity may group multiple ATS tenants; never inferred by AI.
    employer_key = Column(String(120), nullable=True, index=True)
    admission_policy = Column(JSON, nullable=True)
    platform = Column(String(40), nullable=False)
    board_token = Column(String(120), nullable=False)
    region = Column(String(16), nullable=False, default="global")
    careers_url = Column(Text, nullable=False)
    allowed_hosts = Column(JSON, nullable=False, default=list)
    verification_url = Column(Text, nullable=False)
    verification_note = Column(String(1000), nullable=False)
    verified_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    verified_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)
    enabled = Column(Boolean, nullable=False, default=True)
    # Public read access is separate from a contracted, scoped write grant.
    submission_enabled = Column(Boolean, nullable=False, default=False)
    submission_grant = Column(String(500), nullable=True)
    credential_env = Column(String(120), nullable=True)
    form_parity_verified = Column(Boolean, nullable=False, default=False)
    receipt_contract = Column(JSON, nullable=True)
    status = Column(String(24), nullable=False, default="pending")
    last_success_at = Column(DateTime(timezone=True), nullable=True)
    last_error_code = Column(String(80), nullable=True)
    next_refresh_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)
    scan_token = Column(String(64), nullable=True)
    scan_started_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)


class EmployerPosting(Base):
    __tablename__ = "employer_postings"
    __table_args__ = (
        UniqueConstraint("source_id", "external_id", name="uq_employer_source_posting"),
        Index("ix_employer_postings_open_checked", "is_open", "last_checked_at"),
        Index("ix_employer_postings_location", "location"),
    )
    id = Column(String(64), primary_key=True)
    source_id = Column(String(64), ForeignKey("employer_sources.id"), nullable=False, index=True)
    external_id = Column(String(160), nullable=False)
    requisition_id = Column(String(160), nullable=True)
    opening_key = Column(String(64), nullable=True, index=True)
    title = Column(String(300), nullable=False, index=True)
    employer = Column(String(200), nullable=False)
    location = Column(String(400), nullable=False, default="")
    country = Column(String(2), nullable=True)
    remote = Column(Boolean, nullable=False, default=False)
    description = Column(Text, nullable=False)
    skills = Column(JSON, nullable=False, default=list)
    canonical_url = Column(Text, nullable=False)
    apply_url = Column(Text, nullable=False)
    language = Column(String(20), nullable=False, default="en")
    preference_metadata = Column(JSON, nullable=True)
    publication_at = Column(DateTime(timezone=True), nullable=True)
    source_updated_at = Column(DateTime(timezone=True), nullable=True)
    first_seen_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)
    last_checked_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)
    closed_at = Column(DateTime(timezone=True), nullable=True)
    content_sha256 = Column(String(64), nullable=False)
    is_open = Column(Boolean, nullable=False, default=True)


class ServiceCreditEvent(Base):
    __tablename__ = "service_credit_events"
    __table_args__ = (
        UniqueConstraint("user_id", "idempotency_key", name="uq_service_credit_user_key"),
        Index("ix_service_credit_user_created", "user_id", "created_at"),
    )
    id = Column(String(64), primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)
    event_type = Column(String(24), nullable=False)
    amount = Column(Integer, nullable=False)
    balance_after = Column(Integer, nullable=False)
    idempotency_key = Column(String(200), nullable=False)
    source_type = Column(String(48), nullable=False)
    source_id = Column(String(120), nullable=False)
    reason = Column(String(240), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)


class ServiceCreditReservation(Base):
    __tablename__ = "service_credit_reservations"
    __table_args__ = (UniqueConstraint("operation", "source_id", name="uq_service_reservation_source"),
                      Index("ix_service_reservations_state_created", "state", "created_at"))
    id = Column(String(64), primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)
    operation = Column(String(40), nullable=False)
    source_id = Column(String(64), nullable=False)
    unit_price = Column(Integer, nullable=False)
    requested_count = Column(Integer, nullable=False)
    reserved_amount = Column(Integer, nullable=False)
    committed_amount = Column(Integer, nullable=False, default=0)
    released_amount = Column(Integer, nullable=False, default=0)
    state = Column(String(24), nullable=False, default="reserved")
    pricing_version = Column(String(64), nullable=False)
    cost_policy_snapshot = Column(JSON, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)
    settled_at = Column(DateTime(timezone=True), nullable=True)


class EmployerSearch(Base):
    __tablename__ = "employer_searches"
    __table_args__ = (
        UniqueConstraint("user_id", "idempotency_key", name="uq_employer_search_user_key"),
        UniqueConstraint("id", "user_id", name="uq_employer_search_history_owner"),
        ForeignKeyConstraint(["native_snapshot_id", "user_id"], ["employer_catalogue_snapshots.id", "employer_catalogue_snapshots.user_id"], name="fk_employer_search_native_snapshot_owner"),
        Index("ix_employer_search_native_snapshot", "native_snapshot_id"),
        Index("ix_employer_search_user_created", "user_id", "created_at"),
    )
    id = Column(String(64), primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    resume_id = Column(Integer, ForeignKey("resumes.id"), nullable=False)
    idempotency_key = Column(String(160), nullable=False)
    input_fingerprint = Column(String(64), nullable=False)
    _legacy_query = Column("query", JSON, nullable=False)
    status = Column(String(24), nullable=False, default="completed")
    desired_count = Column(Integer, nullable=False)
    delivered_count = Column(Integer, nullable=False, default=0)
    reserved_credits = Column(Integer, nullable=False, default=0)
    charged_credits = Column(Integer, nullable=False, default=0)
    refunded_credits = Column(Integer, nullable=False, default=0)
    _legacy_items = Column("items", JSON, nullable=False, default=list)
    native_snapshot_id = Column(String(64), nullable=True)
    _native_snapshot = relationship("EmployerCatalogueSnapshot", viewonly=True, lazy="select", uselist=False,
                                    foreign_keys=[native_snapshot_id, user_id])
    scope = Column(JSON, nullable=False, default=dict)
    history_revision = Column(BigInteger, nullable=False, default=1, server_default="1")
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)


    def _resolved_native_snapshot(self):
        cached = self.__dict__.get("_native_snapshot")
        if cached is not None and (cached.id != self.native_snapshot_id or cached.user_id != self.user_id):
            self.__dict__.pop("_native_snapshot", None)
        snapshot = self._native_snapshot
        if snapshot is None or snapshot.state != "proven":
            raise RuntimeError("Completed catalogue snapshot is unavailable")
        return snapshot

    @hybrid_property
    def items(self):
        if self.native_snapshot_id is None:
            return self._legacy_items
        return self._resolved_native_snapshot().items

    @items.inplace.setter
    def _set_items(self, value):
        self._legacy_items = value
        self.native_snapshot_id = None
        self.__dict__.pop("_native_snapshot", None)

    @items.inplace.expression
    @classmethod
    def _items_expression(cls):
        body = select(EmployerCatalogueSnapshot.items).where(
            EmployerCatalogueSnapshot.id == cls.native_snapshot_id,
            EmployerCatalogueSnapshot.user_id == cls.user_id,
        ).correlate(cls.__table__).scalar_subquery()
        return case((cls.native_snapshot_id.is_(None), cls._legacy_items), else_=body).label("items")

    @hybrid_property
    def query(self):
        if self.native_snapshot_id is None:
            return self._legacy_query
        return self._resolved_native_snapshot().query

    @query.inplace.setter
    def _set_query(self, value):
        self._legacy_query = value
        self.native_snapshot_id = None
        self.__dict__.pop("_native_snapshot", None)

    @query.inplace.expression
    @classmethod
    def _query_expression(cls):
        body = select(EmployerCatalogueSnapshot.query).where(
            EmployerCatalogueSnapshot.id == cls.native_snapshot_id,
            EmployerCatalogueSnapshot.user_id == cls.user_id,
        ).correlate(cls.__table__).scalar_subquery()
        return case((cls.native_snapshot_id.is_(None), cls._legacy_query), else_=body).label("query")


class EmployerJobDelivery(Base):
    __tablename__ = "employer_job_deliveries"
    __table_args__ = (
        UniqueConstraint("user_id", "posting_id", name="uq_employer_delivery_user_posting"),
        UniqueConstraint("user_id", "opening_key", name="uq_employer_delivery_user_opening"),
        UniqueConstraint("id", "user_id", "search_id", "posting_id", "charged_credits", "created_at", name="uq_employer_delivery_history_identity"),
    )
    id = Column(String(64), primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    posting_id = Column(String(64), ForeignKey("employer_postings.id"), nullable=False)
    opening_key = Column(String(64), nullable=True, index=True)
    search_id = Column(String(64), ForeignKey("employer_searches.id"), nullable=False)
    charged_credits = Column(Integer, nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)


class SealedApplicationArtifact(Base):
    __tablename__ = "sealed_application_artifacts"
    id = Column(String(64), primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    resume_id = Column(Integer, ForeignKey("resumes.id"), nullable=False)
    resume_version_id = Column(String(64), ForeignKey("resume_versions.id"), nullable=True)
    sha256 = Column(String(64), nullable=False)
    filename = Column(String(240), nullable=False)
    media_type = Column(String(160), nullable=False)
    size_bytes = Column(Integer, nullable=False)
    # Inline bytes only in development/tests. Production uses private immutable GCS generations.
    content = deferred(Column(LargeBinary, nullable=True))
    gcs_object = Column(Text, nullable=True)
    gcs_generation = Column(String(80), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)


class EmployerApplication(Base):
    __tablename__ = "employer_applications"
    __table_args__ = (
        UniqueConstraint("user_id", "idempotency_key", name="uq_employer_application_user_key"),
        Index("ix_employer_application_user_created", "user_id", "created_at"),
    )
    id = Column(String(64), primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    posting_id = Column(String(64), ForeignKey("employer_postings.id"), nullable=False, index=True)
    opening_key = Column(String(64), nullable=True, index=True)
    employer_key = Column(String(64), nullable=True, index=True)
    admission_snapshot = Column(JSON, nullable=True)
    pricing_snapshot = Column(JSON, nullable=True)
    batch_id = Column(String(64), ForeignKey("employer_application_batches.id"), nullable=True, index=True)
    idempotency_key = Column(String(160), nullable=False)
    input_fingerprint = Column(String(64), nullable=False)
    # Cleared on safely cancelled/failed intents; retained for confirmed/unknown sends.
    active_key = Column(String(160), unique=True, nullable=True, index=True)
    resume_id = Column(Integer, ForeignKey("resumes.id"), nullable=False)
    resume_choice = Column(String(16), nullable=False)
    resume_version_id = Column(String(64), ForeignKey("resume_versions.id"), nullable=True)
    artifact_id = Column(String(64), ForeignKey("sealed_application_artifacts.id"), nullable=False)
    form = Column(JSON, nullable=False)
    answers = Column(JSON, nullable=False, default=dict)
    consents = Column(JSON, nullable=False, default=dict)
    package_digest = Column(String(64), nullable=False)
    job_content_sha256 = Column(String(64), nullable=False)
    application_mode = Column(String(24), nullable=False, default="manual")
    status = Column(String(32), nullable=False, default="needs_action")
    allowed_actions = Column(JSON, nullable=False, default=list)
    approved_digest = Column(String(64), nullable=True)
    approved_at = Column(DateTime(timezone=True), nullable=True)
    approval_expires_at = Column(DateTime(timezone=True), nullable=True)
    cancelled_at = Column(DateTime(timezone=True), nullable=True)
    cancel_requested = Column(Boolean, nullable=False, default=False)
    credit_cost = Column(Integer, nullable=False)
    charged_credits = Column(Integer, nullable=False, default=0)
    reservation_id = Column(String(64), ForeignKey("service_credit_reservations.id"), nullable=True)
    receipt = Column(JSON, nullable=True)
    error_code = Column(String(80), nullable=True)
    error_message = Column(String(500), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)


class EmployerApplicationBatch(Base):
    __tablename__ = "employer_application_batches"
    __table_args__ = (UniqueConstraint("user_id", "idempotency_key", name="uq_employer_batch_user_key"),)
    id = Column(String(64), primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    idempotency_key = Column(String(160), nullable=False)
    input_fingerprint = Column(String(64), nullable=False)
    package_digest = Column(String(64), nullable=False)
    # Immutable enumerated packages, actions, prices and admission configuration.
    items = Column(JSON, nullable=False)
    admission_snapshot = Column(JSON, nullable=False)
    quoted_credits = Column(Integer, nullable=False)
    max_total_credits = Column(Integer, nullable=False)
    status = Column(String(24), nullable=False, default="quoted")
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    approved_at = Column(DateTime(timezone=True), nullable=True)
    cancelled_at = Column(DateTime(timezone=True), nullable=True)


class EmployerAdmission(Base):
    """A pending slot or possible-send budget; uncertainty never expires itself."""
    __tablename__ = "employer_admissions"
    __table_args__ = (
        UniqueConstraint("application_id", name="uq_employer_admission_application"),
        Index("ix_employer_admission_user_window", "user_id", "state", "possible_send_at"),
        Index("ix_employer_admission_employer_window", "user_id", "employer_key", "state", "possible_send_at"),
    )
    id = Column(String(64), primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    application_id = Column(String(64), ForeignKey("employer_applications.id", ondelete="SET NULL"), nullable=True)
    employer_key = Column(String(64), nullable=False)
    opening_key = Column(String(64), nullable=False)
    active_key = Column(String(160), unique=True, nullable=True)
    credit_cost = Column(Integer, nullable=False)
    policy_snapshot = Column(JSON, nullable=False)
    state = Column(String(24), nullable=False, default="reserved")
    admitted_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)
    possible_send_at = Column(DateTime(timezone=True), nullable=True)
    settled_at = Column(DateTime(timezone=True), nullable=True)
    release_reason = Column(String(80), nullable=True)


class EmployerApplicationAttempt(Base):
    __tablename__ = "employer_application_attempts"
    id = Column(String(64), primary_key=True)
    application_id = Column(String(64), ForeignKey("employer_applications.id"), nullable=False, index=True)
    launch_token = Column(String(64), unique=True, nullable=False)
    package_digest = Column(String(64), nullable=False)
    state = Column(String(24), nullable=False, default="submitting")
    response_status = Column(Integer, nullable=True)
    receipt = Column(JSON, nullable=True)
    error_code = Column(String(80), nullable=True)
    started_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)
    completed_at = Column(DateTime(timezone=True), nullable=True)


class EmployerApplicationApproval(Base):
    __tablename__ = "employer_application_approvals"
    id = Column(String(64), primary_key=True)
    application_id = Column(String(64), ForeignKey("employer_applications.id"), nullable=False, index=True)
    package_digest = Column(String(64), nullable=False)
    review_snapshot = Column(JSON, nullable=False)
    allowed_actions = Column(JSON, nullable=False)
    approved_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    revoked_at = Column(DateTime(timezone=True), nullable=True)


class EmployerArtifactDeletion(Base):
    __tablename__ = "employer_artifact_deletions"
    id = Column(String(64), primary_key=True)
    gcs_object = Column(Text, nullable=False)
    gcs_generation = Column(String(80), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)
    completed_at = Column(DateTime(timezone=True), nullable=True)
    next_attempt_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)
    attempt_count = Column(Integer, nullable=False, default=0)
    last_error_code = Column(String(80), nullable=True)
    absence_confirmed_at = Column(DateTime(timezone=True), nullable=True)


class EmployerArtifactUpload(Base):
    """A cleanup guard committed before an object can be uploaded to GCS."""
    __tablename__ = "employer_artifact_uploads"
    id = Column(String(64), primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)
    gcs_object = Column(Text, nullable=False)
    gcs_generation = Column(String(80), nullable=True)
    lease_until = Column(DateTime(timezone=True), nullable=False, index=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)


class EmployerPaidDeliveryHistory(Base):
    __tablename__ = "employer_paid_delivery_history"
    __table_args__ = (
        ForeignKeyConstraint(
            ["delivery_id", "user_id", "search_id", "posting_id", "original_charged_credits", "delivery_created_at"],
            ["employer_job_deliveries.id", "employer_job_deliveries.user_id", "employer_job_deliveries.search_id", "employer_job_deliveries.posting_id", "employer_job_deliveries.charged_credits", "employer_job_deliveries.created_at"],
            name="fk_paid_history_delivery_identity",
        ),
        ForeignKeyConstraint(["search_id", "search_owner_id"], ["employer_searches.id", "employer_searches.user_id"], name="fk_paid_history_search_owner"),
        UniqueConstraint("user_id", "delivery_id", "opening_key", "state", name="uq_paid_history_registry_proof"),
        CheckConstraint("state IN ('pending','proven','unresolved')", name="ck_paid_history_state"),
        CheckConstraint("saved_ordinal IS NULL OR saved_ordinal >= 0", name="ck_paid_history_ordinal"),
        CheckConstraint("source_revision IS NULL OR source_revision >= 1", name="ck_paid_history_source_revision"),
        CheckConstraint("content_digest IS NULL OR length(content_digest) = 64", name="ck_paid_history_digest"),
        CheckConstraint("provenance IN ('legacy_backfill','new_search')", name="ck_paid_history_provenance"),
        CheckConstraint("state != 'proven' OR (search_owner_id = user_id AND original_charged_credits > 0 AND snapshot_charged_credits IS NOT NULL AND snapshot_charged_credits = original_charged_credits AND opening_key IS NOT NULL AND length(opening_key) = 64 AND saved_ordinal IS NOT NULL AND content_digest IS NOT NULL AND source_revision IS NOT NULL AND static_reason IS NULL)", name="ck_paid_history_proven"),
        CheckConstraint("state != 'unresolved' OR static_reason IS NOT NULL", name="ck_paid_history_unresolved"),
        CheckConstraint("identity_proof IS NULL OR length(CAST(identity_proof AS TEXT)) <= 16384", name="ck_paid_history_identity_size"),
        CheckConstraint("state != 'proven' OR provenance != 'new_search' OR (identity_proof IS NOT NULL AND CAST(identity_proof AS TEXT) != 'null')", name="ck_paid_history_new_identity"),
        CheckConstraint("(review_required = false AND review_reason IS NULL) OR (review_required = true AND review_reason IS NOT NULL)", name="ck_paid_history_review_reason"),
        Index("ix_paid_history_owner_time", "user_id", "delivery_created_at", "delivery_id"),
        Index("ix_paid_history_owner_key_time", "user_id", "opening_key", "state", "delivery_created_at", "delivery_id"),
        Index("ix_paid_history_search_state", "search_id", "state", "delivery_id"),
    )
    delivery_id = Column(String(64), primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    search_id = Column(String(64), nullable=False)
    search_owner_id = Column(Integer, nullable=False)
    posting_id = Column(String(64), ForeignKey("employer_postings.id"), nullable=False)
    original_charged_credits = Column(Integer, nullable=False)
    delivery_created_at = Column(DateTime(timezone=True), nullable=False)
    state = Column(String(24), nullable=False, default="pending", server_default="pending")
    opening_key = Column(String(64), nullable=True)
    saved_ordinal = Column(BigInteger, nullable=True)
    snapshot_charged_credits = Column(Integer, nullable=True)
    content_digest = Column(String(64), nullable=True)
    source_revision = Column(BigInteger, nullable=True)
    static_reason = Column(String(64), nullable=True)
    provenance = Column(String(24), nullable=False)
    identity_proof = Column(JSON, nullable=True)
    review_required = Column(Boolean, nullable=False, default=False, server_default=false())
    review_reason = Column(String(64), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)


class EmployerPaidOpeningRegistry(Base):
    __tablename__ = "employer_paid_opening_registry"
    __table_args__ = (
        ForeignKeyConstraint(["user_id", "canonical_delivery_id", "opening_key", "history_state"], ["employer_paid_delivery_history.user_id", "employer_paid_delivery_history.delivery_id", "employer_paid_delivery_history.opening_key", "employer_paid_delivery_history.state"], name="fk_paid_registry_proven_history"),
        CheckConstraint("history_state = 'proven'", name="ck_paid_registry_proven"),
        CheckConstraint("length(opening_key) = 64", name="ck_paid_registry_opening"),
    )
    user_id = Column(Integer, ForeignKey("users.id"), primary_key=True)
    opening_key = Column(String(64), primary_key=True)
    canonical_delivery_id = Column(String(64), nullable=False)
    history_state = Column(String(24), nullable=False, default="proven", server_default="proven")
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)


class EmployerPaidHistoryProgress(Base):
    __tablename__ = "employer_paid_history_progress"
    __table_args__ = (
        CheckConstraint("state IN ('pending','backfilling','ready','unresolved')", name="ck_paid_progress_state"),
        CheckConstraint("generation >= 1", name="ck_paid_progress_generation"),
        CheckConstraint("examined_count >= 0 AND proven_count >= 0 AND unresolved_count >= 0 AND proven_count + unresolved_count <= examined_count", name="ck_paid_progress_counts"),
        CheckConstraint("(checkpoint_created_at IS NULL) = (checkpoint_delivery_id IS NULL)", name="ck_paid_progress_checkpoint"),
        CheckConstraint("(highwater_created_at IS NULL) = (highwater_delivery_id IS NULL)", name="ck_paid_progress_highwater"),
        CheckConstraint("state != 'ready' OR unresolved_count = 0", name="ck_paid_progress_ready"),
    )
    user_id = Column(Integer, ForeignKey("users.id"), primary_key=True)
    generation = Column(BigInteger, nullable=False, default=1, server_default="1")
    state = Column(String(24), nullable=False, default="pending", server_default="pending")
    checkpoint_created_at = Column(DateTime(timezone=True), nullable=True)
    checkpoint_delivery_id = Column(String(64), nullable=True)
    highwater_created_at = Column(DateTime(timezone=True), nullable=True)
    highwater_delivery_id = Column(String(64), nullable=True)
    examined_count = Column(BigInteger, nullable=False, default=0, server_default="0")
    proven_count = Column(BigInteger, nullable=False, default=0, server_default="0")
    unresolved_count = Column(BigInteger, nullable=False, default=0, server_default="0")
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)


class EmployerSearchHistoryProgress(Base):
    __tablename__ = "employer_search_history_progress"
    __table_args__ = (
        ForeignKeyConstraint(["search_id", "user_id"], ["employer_searches.id", "employer_searches.user_id"], name="fk_search_history_progress_owner"),
        CheckConstraint("state IN ('pending','scanning','reducing','complete','unresolved')", name="ck_search_history_progress_state"),
        CheckConstraint("generation >= 1 AND stream_generation >= 1 AND source_revision >= 1", name="ck_search_history_progress_generation"),
        CheckConstraint("source_size_bytes >= 0 AND byte_offset >= 0 AND byte_offset <= source_size_bytes AND next_ordinal >= 0 AND unresolved_count >= 0", name="ck_search_history_progress_work"),
        CheckConstraint("source_sha256 IS NULL OR length(source_sha256) = 64", name="ck_search_history_progress_digest"),
        CheckConstraint("length(CAST(parser_checkpoint AS TEXT)) <= 8192", name="ck_search_history_progress_parser_size"),
        Index("ix_search_history_progress_owner_state", "user_id", "state", "search_id"),
    )
    search_id = Column(String(64), primary_key=True)
    user_id = Column(Integer, nullable=False)
    generation = Column(BigInteger, nullable=False, default=1, server_default="1")
    stream_generation = Column(BigInteger, nullable=False, default=1, server_default="1")
    source_revision = Column(BigInteger, nullable=False, default=1, server_default="1")
    state = Column(String(24), nullable=False, default="pending", server_default="pending")
    source_size_bytes = Column(BigInteger, nullable=False, default=0, server_default="0")
    byte_offset = Column(BigInteger, nullable=False, default=0, server_default="0")
    next_ordinal = Column(BigInteger, nullable=False, default=0, server_default="0")
    parser_checkpoint = Column(JSON, nullable=False, default=dict, server_default="{}")
    source_sha256 = Column(String(64), nullable=True)
    unresolved_count = Column(BigInteger, nullable=False, default=0, server_default="0")
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)


class EmployerSearchHistorySeen(Base):
    __tablename__ = "employer_search_history_seen"
    __table_args__ = (
        CheckConstraint("generation >= 1 AND first_ordinal >= 0 AND occurrence_count >= 1", name="ck_search_history_seen_counts"),
        CheckConstraint("content_digest IS NULL OR length(content_digest) = 64", name="ck_search_history_seen_digest"),
        CheckConstraint("length(CAST(facts AS TEXT)) <= 4096", name="ck_search_history_seen_facts_size"),
    )
    search_id = Column(String(64), ForeignKey("employer_search_history_progress.search_id"), primary_key=True)
    generation = Column(BigInteger, primary_key=True)
    posting_id = Column(String(64), primary_key=True)
    first_ordinal = Column(BigInteger, nullable=False)
    occurrence_count = Column(BigInteger, nullable=False, default=1, server_default="1")
    content_digest = Column(String(64), nullable=True)
    opening_key = Column(String(64), nullable=True)
    facts = Column(JSON, nullable=False, default=dict, server_default="{}")
    static_reason = Column(String(64), nullable=True)
    conflicting = Column(Boolean, nullable=False, default=False, server_default=false())


class EmployerSearchHistoryNode(Base):
    __tablename__ = "employer_search_history_nodes"
    __table_args__ = (
        ForeignKeyConstraint(["search_id", "generation", "parent_node_id"], ["employer_search_history_nodes.search_id", "employer_search_history_nodes.generation", "employer_search_history_nodes.node_id"], name="fk_search_history_node_parent"),
        CheckConstraint("generation >= 1 AND node_id >= 0 AND child_reduce_cursor >= 0 AND child_count >= 0 AND (member_index IS NULL OR member_index >= 0)", name="ck_search_history_node_counts"),
        CheckConstraint("parent_node_id IS NULL OR (parent_node_id >= 0 AND parent_node_id < node_id)", name="ck_search_history_node_parent"),
        CheckConstraint("kind IN ('object','array','string','number','boolean','null')", name="ck_search_history_node_kind"),
        CheckConstraint("content_digest IS NULL OR length(content_digest) = 64", name="ck_search_history_node_digest"),
        CheckConstraint("member_key_hash IS NULL OR length(member_key_hash) = 64", name="ck_search_history_node_key_digest"),
        CheckConstraint("small_value IS NULL OR length(CAST(small_value AS TEXT)) <= 4096", name="ck_search_history_node_value_size"),
        CheckConstraint("hash_checkpoint IS NULL OR length(CAST(hash_checkpoint AS TEXT)) <= 4096", name="ck_search_history_node_hash_size"),
        Index("ix_search_history_nodes_parent", "search_id", "generation", "parent_node_id", "node_id"),
        Index("ix_search_history_nodes_array", "search_id", "generation", "parent_node_id", "member_index"),
        Index("ix_search_history_nodes_key", "search_id", "generation", "parent_node_id", "member_key_hash"),
    )
    search_id = Column(String(64), ForeignKey("employer_search_history_progress.search_id"), primary_key=True)
    generation = Column(BigInteger, primary_key=True)
    node_id = Column(BigInteger, primary_key=True)
    parent_node_id = Column(BigInteger, nullable=True)
    member_key_hash = Column(String(64), nullable=True)
    member_key = Column(String(64), nullable=True)
    member_index = Column(BigInteger, nullable=True)
    kind = Column(String(12), nullable=False)
    parser_expect = Column(String(24), nullable=True)
    content_digest = Column(String(64), nullable=True)
    small_value = Column(JSON, nullable=True)
    hash_checkpoint = Column(JSON, nullable=True)
    child_reduce_cursor = Column(BigInteger, nullable=False, default=0, server_default="0")
    child_count = Column(BigInteger, nullable=False, default=0, server_default="0")
