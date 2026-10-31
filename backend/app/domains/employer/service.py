from __future__ import annotations

import copy
import json
import os
import re
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

from fastapi import HTTPException
from sqlalchemy import LargeBinary, Text, and_, cast, func, literal_column, or_, select, text, true
from sqlalchemy.orm import Session, aliased, load_only

from ... import models as core
from ...catalog_timing import catalog_span, current_catalog_timing
from ...services.basic_matching import basic_match
from ..common import payload_fingerprint, public_id, utcnow
from . import (
    admissions,
    artifacts,
    config,
    connectors,
    credits,
    models,
    role_aliases,
    schemas,
)
from .search_preferences import POLICY_VERSION, evaluate

if TYPE_CHECKING:
    from ..dispatch.service import EmployerSearchContinuation

LOCKED_APPLICATION_STATES = {"queued", "submitting", "confirmed", "unknown", "failed", "user_reported"}


def _utc(value):
    return value.replace(tzinfo=UTC) if value and value.tzinfo is None else value


def _require_enabled():
    if not config.discovery_enabled():
        raise HTTPException(503, "Employer discovery is not enabled yet")


def _owned(db, cls, identity, user_id, lock=False):
    query = db.query(cls).filter(cls.id == identity, cls.user_id == user_id)
    row = (query.with_for_update() if lock else query).first()
    if not row:
        raise HTTPException(404, "Not found")
    return row


def _posting(db, identity):
    row = db.query(models.EmployerPosting).filter(models.EmployerPosting.id == identity).first()
    if not row:
        raise HTTPException(404, "Job not found")
    return row


def _source(db, identity):
    row = db.query(models.EmployerSource).filter(models.EmployerSource.id == identity).first()
    if not row:
        raise HTTPException(404, "Employer source not found")
    return row


def _source_mode(source, form=None):
    if (source.enabled and config.submit_enabled() and source.platform == "greenhouse" and
            source.submission_enabled and source.form_parity_verified and source.submission_grant and
            source.receipt_contract and source.credential_env and os.getenv(source.credential_env) and
            (form is None or form.get("supported"))):
        return "api"
    return "manual"


def source_response(source):
    return {key: getattr(source, key) for key in (
        "id", "employer", "platform", "region", "careers_url", "enabled", "status", "verified_at", "last_success_at", "next_refresh_at", "last_error_code",
    )} | {"application_mode": _source_mode(source), "employer_key": admissions.employer_identity(source),
          "admission_policy": config.employer_limits(source)}


def catalog(db, user_id):
    # Advisory catalog reads can reuse the authenticated owner in this session.
    # Financial/admission mutations retain their authoritative locked refreshes.
    with catalog_span("catalog_owner_lookup"):
        user = db.get(core.User, user_id)
        if user is None:
            user = db.query(core.User).filter(core.User.id == user_id).one()
    with catalog_span("catalog_sources"):
        sources = db.query(models.EmployerSource).filter(models.EmployerSource.enabled.is_(True)).order_by(models.EmployerSource.employer).limit(500).all()
    with catalog_span("catalog_render"):
        return _catalog_response(user.job_service_credits, sources)


def catalog_for_verified_subject(db: Session, user_id: int, *, credential_epoch=None, native_validated: bool = False):
    """Read owner existence/balance and bounded sources in one fresh SQL snapshot.

    The route validates the JWT first. This advisory read never replaces financial
    locks or uses a cached ORM owner; password/contact columns are not projected.
    """
    if current_catalog_timing() is not None:
        with catalog_span("db_acquire"):
            db.connection()
    with catalog_span("catalog_snapshot"):
        bounded = db.query(models.EmployerSource).filter(
            models.EmployerSource.enabled.is_(True),
        ).order_by(models.EmployerSource.employer, models.EmployerSource.id).limit(500).subquery()
        source = aliased(models.EmployerSource, bounded)
        rows = db.query(core.User.id, core.User.job_service_credits, source, core.User.credential_epoch,
                        core.CandidatePasswordAccount.user_id).select_from(
            core.User,
        ).outerjoin(core.CandidatePasswordAccount, core.CandidatePasswordAccount.user_id == core.User.id).outerjoin(source, true()).filter(core.User.id == user_id).order_by(
            source.employer, source.id,
        ).populate_existing().all()
    if (not rows or rows[0][4] is not None and not native_validated
            or rows[0][4] is None and int(rows[0][3] or 0) > 0
            and (type(credential_epoch) is not int or credential_epoch != rows[0][3])):
        raise HTTPException(401, "Not authenticated", headers={"WWW-Authenticate": "Bearer"})
    with catalog_span("catalog_owner_lookup"):
        balance = rows[0][1]
    with catalog_span("catalog_materialize"):
        sources = [row[2] for row in rows if row[2] is not None]
    with catalog_span("catalog_render"):
        return _catalog_response(balance, sources)


def _catalog_response(balance, sources):
    from ...billing.cost_policy import CostPolicyUnavailable
    try:
        prices = config.prices()
        ready = True
        message = None
    except CostPolicyUnavailable as exc:
        prices = {"search_credits_per_job": 0, "apply_credits_per_job": 0, "max_search_jobs": 100,
                  "pricing_version": "unavailable"}
        ready = False
        message = str(exc)
    prices.pop("cost_policy_snapshot", None)
    return prices | {
        "balance": int(balance or 0), "enabled": ready and config.discovery_enabled(),
        "availability_message": message,
        "auto_submit_enabled": ready and config.submit_enabled(), "sources": [source_response(source) for source in sources],
        "admission_limits": config.admission_limits(),
        "credit_policy": "New unique verified jobs are charged once. Automatic applications are charged only after a verified complete receipt. Promotional analysis units and Premium are separate.",
        "coverage": {"scope": "Verified enabled employer sources", "worldwide_recall_verified": False},
    }


def posting_response(posting, source):
    return {key: getattr(posting, key) for key in (
        "id", "source_id", "external_id", "title", "employer", "location", "description", "canonical_url", "apply_url", "publication_at", "first_seen_at", "last_checked_at", "remote", "is_open", "content_sha256", "preference_metadata",
    )} | {"platform": source.platform, "application_mode": _source_mode(source),
          "opening_key": admissions.opening_identity(posting, source),
          "publication_kind": "release_or_republication" if source.platform == "smartrecruiters" else "publication" if posting.publication_at else "unknown"}


def _json_posting(posting, source):
    result = posting_response(posting, source)
    return {key: value.isoformat() if isinstance(value, datetime) else value for key, value in result.items()}


def _search_input(payload: schemas.SearchCreate) -> dict:
    # Keep the exact historical fingerprint for clients which omit preferences.
    excluded = {"idempotency_key", "accepted_quote"} | ({"preferences"} if payload.preferences is None else set())
    return payload.model_dump(mode="json", exclude=excluded)


BOUNDED_SEARCH_PROTOCOL = "funded-bounded-search-v1"
_BOUNDED_LEASE = timedelta(minutes=5)
_BOUNDED_ABANDONMENT = timedelta(minutes=30)
_BOUNDED_QUERY_MAX_BYTES = 32_768
_BOUNDED_SEARCH_FIELDS = ("id", "user_id", "resume_id", "idempotency_key", "input_fingerprint", "status",
    "desired_count", "delivered_count", "reserved_credits", "charged_credits", "refunded_credits",
    "created_at", "scope", "history_revision", "_legacy_query", "native_snapshot_id")
_BOUNDED_POSTING_BODY = ("id", "source_id", "external_id", "requisition_id", "title", "employer", "location",
    "description", "canonical_url", "apply_url", "publication_at", "first_seen_at", "last_checked_at",
    "remote", "is_open", "content_sha256", "preference_metadata")
_BOUNDED_SOURCE_BODY = ("id", "enabled", "platform", "region", "board_token", "employer_key", "submission_enabled",
    "form_parity_verified", "submission_grant", "receipt_contract", "credential_env")
_BOUNDED_POSTING_SCALARS = tuple(name for name in _BOUNDED_POSTING_BODY if name not in
    {"description", "preference_metadata"})
_BOUNDED_SOURCE_SCALARS = tuple(name for name in _BOUNDED_SOURCE_BODY if name != "receipt_contract")


def _bounded_row(db, user_id, search_id, *, lock=False):
    query = db.query(models.EmployerSearch).options(load_only(*(getattr(models.EmployerSearch, name)
        for name in _BOUNDED_SEARCH_FIELDS))).filter_by(id=search_id, user_id=user_id).populate_existing()
    return (query.with_for_update(nowait=True) if lock else query).first()


def _bounded_resume_revision(db, user_id, resume_id, *, fence=False):
    query = db.query(core.Resume.id).filter_by(id=resume_id, user_id=user_id)
    if fence:
        query = query.with_for_update(read=True, nowait=True)
    if query.first() is None:
        raise HTTPException(404, "Not found")
    if db.get_bind().dialect.name == "postgresql":
        from sqlalchemy.dialects.postgresql import insert
    else:
        from sqlalchemy.dialects.sqlite import insert
    binding = models.EmployerCatalogueResumeBinding
    db.execute(insert(binding).values(resume_id=resume_id, revision=1).on_conflict_do_nothing(index_elements=[binding.resume_id]))
    return db.query(binding.revision).filter_by(resume_id=resume_id).scalar()


def _bounded_reservation(db, row, *, lock=False):
    query = db.query(models.ServiceCreditReservation).filter_by(user_id=row.user_id, source_id=row.id,
        operation="job_search").populate_existing()
    reservation = (query.with_for_update(nowait=True) if lock else query).first()
    authority = (reservation.cost_policy_snapshot or {}) if reservation is not None else {}
    quote = row.scope.get("price_quote", {})
    if (reservation is None or reservation.state != "reserved"
            or reservation.requested_count != row.desired_count
            or reservation.unit_price != quote.get("unit_price")
            or reservation.pricing_version != quote.get("pricing_version")
            or reservation.reserved_amount != row.desired_count * reservation.unit_price
            or reservation.reserved_amount != quote.get("maximum_credits")
            or reservation.committed_amount != 0 or reservation.released_amount != 0
            or authority.get("history_search_fingerprint") != row.input_fingerprint
            or authority.get("history_desired_count") != row.desired_count):
        raise HTTPException(409, {"code": "search_reservation_requires_review",
            "message": "This search has no unchanged funded reservation. Start a new search after review."})
    return reservation


def _admit_bounded_search(db, user_id, payload):
    from ...billing.cost_policy import CostPolicyUnavailable
    from ..dispatch.service import enqueue
    from . import catalogue_search, delivery_history
    search_input = _search_input(payload)
    fingerprint = payload_fingerprint(search_input)
    canonical_query = json.dumps(search_input, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    if len(canonical_query) > _BOUNDED_QUERY_MAX_BYTES:
        raise HTTPException(422, {"code": "search_query_too_large",
            "message": "These search choices exceed the supported request size. Reduce the employer exclusions."})
    search_id = "search_" + payload_fingerprint({"owner": user_id, "key": payload.idempotency_key})[:48]
    # Shared resume/FK prerequisites precede the NOWAIT financial owner fence.
    revision = _bounded_resume_revision(db, user_id, payload.resume_id, fence=True)
    catalogue_search.lock_owner_nowait(db, user_id)
    existing = _bounded_row(db, user_id, search_id, lock=True)
    if existing is not None:
        if existing.input_fingerprint != fingerprint:
            raise HTTPException(409, "This request key was already used with different search settings")
        db.commit()
        return existing
    try:
        prices = config.prices()
    except CostPolicyUnavailable as exc:
        raise HTTPException(503, detail=str(exc)) from None
    config.validate_search_price(payload, prices)
    old = db.query(models.ServiceCreditReservation).filter_by(user_id=user_id, source_id=search_id,
        operation="job_search").populate_existing().with_for_update(nowait=True).first()
    if old is not None:
        # Legacy zero-preparation markers are not authority to perform paid work.
        if old.state == "reserved" and old.reserved_amount == 0:
            credits.settle(db, old, completed_count=0, reason="Legacy unfunded search requires renewed admission")
            db.commit()
        raise HTTPException(409, {"code": "search_reservation_requires_review",
            "message": "This earlier search preparation needs review. Start a new search request."})
    if payload.desired_count > int(prices["max_search_jobs"]):
        raise HTTPException(422, "Requested job count exceeds the current service limit")
    reservation = credits.reserve(db, user_id=user_id, operation="job_search", source_id=search_id,
        unit_price=int(prices["search_credits_per_job"]), count=payload.desired_count,
        pricing_version=str(prices["pricing_version"]))
    reservation.cost_policy_snapshot = dict(reservation.cost_policy_snapshot or {}) | {
        "history_search_fingerprint": fingerprint, "history_desired_count": payload.desired_count,
        "history_accepted_max_jobs": int(prices["max_search_jobs"])}
    quote = {"unit_price": reservation.unit_price, "pricing_version": reservation.pricing_version,
        "requested_count": payload.desired_count, "maximum_credits": reservation.reserved_amount,
        "search_input_fingerprint": fingerprint}
    row = models.EmployerSearch(id=search_id, user_id=user_id, resume_id=payload.resume_id,
        idempotency_key=payload.idempotency_key, input_fingerprint=fingerprint, query=search_input, items=[],
        status="preparing", desired_count=payload.desired_count, delivered_count=0,
        reserved_credits=reservation.reserved_amount, charged_credits=0, refunded_credits=0,
        scope={"bounded_protocol": BOUNDED_SEARCH_PROTOCOL, "resume_source_revision": revision,
            "paid_history_mode": config.paid_history_mode(), "price_quote": quote,
            "max_source_age_hours": config.positive_int("EMPLOYER_MAX_SOURCE_AGE_HOURS", 24, 168),
            "worldwide_recall_verified": False})
    db.add(row)
    delivery_history._progress(db, user_id)  # Existing scalar invalidation clock, even in legacy-reader mode.
    enqueue(db, topic="employer.search", aggregate_id=search_id, payload={"search_id": search_id},
        key="employer-search:" + search_id)
    db.commit()  # Funds, immutable admission and recovery intent become durable together.
    return row


def create_search(db: Session, user_id: int, payload: schemas.SearchCreate):
    _require_enabled()
    search_id = "search_" + payload_fingerprint({"owner": user_id, "key": payload.idempotency_key})[:48]
    existing = _bounded_row(db, user_id, search_id)
    if existing is not None:
        if existing.input_fingerprint != payload_fingerprint(_search_input(payload)):
            raise HTTPException(409, "This request key was already used with different search settings")
        if existing.status != "preparing":
            return existing
        if existing.scope.get("bounded_protocol") != BOUNDED_SEARCH_PROTOCOL:
            raise HTTPException(409, "This unfinished search needs review")
        db.commit()
        return resume_bounded_search(db, user_id, search_id)
    accepted = db.query(models.EmployerSearchPreparation).options(load_only(
        models.EmployerSearchPreparation.id, models.EmployerSearchPreparation.user_id,
        models.EmployerSearchPreparation.input_fingerprint, models.EmployerSearchPreparation.state)).filter_by(
        id=search_id, user_id=user_id).first()
    if accepted is not None:
        if accepted.input_fingerprint != payload_fingerprint(_search_input(payload)):
            raise HTTPException(409, "This request key was already used with different search settings")
        return accepted
    from ..credits.runtime import requires_unified_billing
    if requires_unified_billing(db, user_id):
        raise HTTPException(409, {"code": "unified_discovery_review_required",
            "message": "Review the one-credit discovery batch before starting a new search."})
    if config.catalogue_search_enabled():
        from . import catalogue_search
        return catalogue_search.accept(db, user_id, payload)
    row = _admit_bounded_search(db, user_id, payload)
    return resume_bounded_search(db, user_id, row.id) if row.status == "preparing" else row


def _claim_bounded_search(db, user_id, identity):
    from . import catalogue_search
    catalogue_search.lock_owner_nowait(db, user_id)
    row = _bounded_row(db, user_id, identity, lock=True)
    if row is None:
        raise HTTPException(404, "Search preparation not found")
    if row.status != "preparing":
        db.commit()
        return row, None
    if row.scope.get("bounded_protocol") != BOUNDED_SEARCH_PROTOCOL:
        raise HTTPException(409, "This unfinished search needs review")
    _bounded_reservation(db, row, lock=True)
    now = utcnow()
    lease_until = row.scope.get("lease_until")
    if lease_until and _utc(datetime.fromisoformat(lease_until)) > now:
        db.commit()
        return row, None
    token = public_id("bsearch")
    row.scope = dict(row.scope) | {"lease_token": token, "lease_until": (now + _BOUNDED_LEASE).isoformat()}
    db.commit()
    return row, token


def _utf8_bytes(db, expression):
    # A PostgreSQL text::bytea cast parses backslash escapes. convert_to instead
    # measures the real UTF-8 text, including literal Windows paths and regexes.
    if db.get_bind().dialect.name == "postgresql":
        return func.octet_length(func.convert_to(expression, "UTF8"))
    return func.length(cast(expression, LargeBinary))


def _prepare_bounded_search(db, row, payload, *, only_new=False, maximum_candidate_bytes=None):
    from . import delivery_history
    history_mode = row.scope["paid_history_mode"]
    if history_mode == "indexed":
        delivery_history.advance_owner(db, row.user_id)
        db.commit()
    resume_row = db.query(core.Resume.id, core.Resume.skills,
        models.EmployerCatalogueResumeBinding.revision).join(models.EmployerCatalogueResumeBinding,
        models.EmployerCatalogueResumeBinding.resume_id == core.Resume.id).filter(core.Resume.id == row.resume_id,
        core.Resume.user_id == row.user_id).first()
    if resume_row is None or resume_row[2] != row.scope["resume_source_revision"]:
        raise HTTPException(409, {"code": "resume_changed", "message": "The resume changed during preparation. Start a new search."})
    resume = SimpleNamespace(id=resume_row[0], skills=resume_row[1])
    progress = db.query(models.EmployerPaidHistoryProgress.generation, models.EmployerPaidHistoryProgress.state).filter_by(user_id=row.user_id).one()
    history_generation, progress_state = progress
    user_id = row.user_id
    stale_before = utcnow() - timedelta(hours=row.scope["max_source_age_hours"])
    stale_before = utcnow() - timedelta(hours=config.positive_int("EMPLOYER_MAX_SOURCE_AGE_HOURS", 24, 168))
    query = db.query(models.EmployerPosting, models.EmployerSource).join(models.EmployerSource, models.EmployerSource.id == models.EmployerPosting.source_id).filter(
        models.EmployerPosting.is_open.is_(True), models.EmployerSource.enabled.is_(True),
        models.EmployerPosting.last_checked_at >= stale_before,
    )
    role_variants = role_aliases.variants(payload.role)
    if db.get_bind().dialect.name == "postgresql":
        document = func.to_tsvector(text("'simple'::regconfig"),
            func.coalesce(models.EmployerPosting.title, literal_column("''")) + literal_column("' '") +
            func.coalesce(models.EmployerPosting.description, literal_column("''")))
        query = query.filter(or_(*(document.op("@@")(func.plainto_tsquery(text("'simple'::regconfig"), variant)) for variant in role_variants)))
    # Every significant token must match within one explicit alias branch.
    # Qualifiers (seniority, tools, specialty) are retained in every branch.
    branches = []
    for variant in role_variants:
        clauses = []
        for token in list(dict.fromkeys(re.findall(r"[\w+#.-]+", variant)))[:12]:
            escaped = token.replace("%", "\\%").replace("_", "\\_")
            clauses.append(or_(models.EmployerPosting.title.ilike(f"%{escaped}%", escape="\\"),
                               models.EmployerPosting.description.ilike(f"%{escaped}%", escape="\\")))
        if clauses:
            branches.append(and_(*clauses))
    if branches:
        query = query.filter(or_(*branches))
    if payload.location:
        escaped = payload.location.replace("%", "\\%").replace("_", "\\_")
        query = query.filter(models.EmployerPosting.location.ilike(f"%{escaped}%", escape="\\"))
    if payload.remote_only:
        query = query.filter(models.EmployerPosting.remote.is_(True))
    if payload.published_within_days:
        query = query.filter(models.EmployerPosting.publication_at >= utcnow() - timedelta(days=payload.published_within_days))
    if payload.excluded_employers:
        query = query.filter(func.lower(models.EmployerPosting.employer).notin_([name.lower() for name in payload.excluded_employers]))
    source_revision = select(models.EmployerCatalogueSourceRevision.valid_from).where(
        models.EmployerCatalogueSourceRevision.source_id == models.EmployerSource.id,
        models.EmployerCatalogueSourceRevision.valid_to.is_(None)).scalar_subquery()
    posting_revision = select(models.EmployerCataloguePostingRevision.valid_from).where(
        models.EmployerCataloguePostingRevision.posting_id == models.EmployerPosting.id,
        models.EmployerCataloguePostingRevision.valid_to.is_(None)).scalar_subquery()
    ordered = query.order_by(models.EmployerPosting.last_checked_at.desc(), models.EmployerPosting.id).limit(1001)
    if maximum_candidate_bytes is not None:
        encoded_size = _utf8_bytes(db, func.coalesce(models.EmployerPosting.description, "") + func.coalesce(
            cast(models.EmployerPosting.preference_metadata, Text), ""))
        bounded = ordered.with_entities(encoded_size.label("bytes")).subquery()
        observed = db.query(func.coalesce(func.sum(bounded.c.bytes), 0)).scalar()
        if observed > maximum_candidate_bytes:
            raise HTTPException(409, {"code": "candidate_scope_changed",
                "message": "The indexed content exceeds this reviewed batch's processing limit."})
    rows = ordered.add_columns(source_revision, posting_revision).all()
    # Copy complete public records once outside financial locks. ORM entities
    # must not become dirty just because canonical identity is calculated.
    candidates = [(SimpleNamespace(**{name: getattr(posting, name) for name in _BOUNDED_POSTING_BODY}),
                   SimpleNamespace(**{name: getattr(source, name) for name in _BOUNDED_SOURCE_BODY}))
                  for posting, source, _, _ in rows]
    stamps = {posting.id: (source_mark, posting_mark) for posting, _, source_mark, posting_mark in rows}
    truncated = len(candidates) > 1000
    evaluations = {posting.id: evaluate(payload.preferences, posting.preference_metadata, provider=source.platform) for posting, source in candidates[:1000]}
    ranked = [(posting, source, basic_match(resume.skills or [], posting.description, posting.title)) for posting, source in candidates[:1000]
              if evaluations[posting.id]["eligible"] and role_aliases.title_relevance(payload.role, posting.title)]
    ranked.sort(key=lambda item: (-int(role_aliases.title_relevance(payload.role, item[0].title) == 2), -item[2]["score"], item[0].id))
    unique: dict[str, tuple[SimpleNamespace, SimpleNamespace, dict]] = {}
    for item in ranked:
        item[0].opening_key = admissions.opening_identity(item[0], item[1])
        unique.setdefault(item[0].opening_key, item)
    if history_mode == "indexed":
        from . import delivery_history
        previous, legacy, history_state = delivery_history.membership(
            db, user_id, list(unique), [item[0].id for item in unique.values()])
        legacy_identity_unavailable = history_state != "ready"
    else:
        previous = {row.opening_key for row in db.query(models.EmployerJobDelivery).filter_by(user_id=user_id).filter(models.EmployerJobDelivery.opening_key.in_(list(unique))).all()}
        # Recover legacy canonical identity only from the saved paid delivery's
        # owner-bound search snapshot, never from today's mutable posting fields.
        legacy_rows = db.query(models.EmployerJobDelivery, models.EmployerSearch).outerjoin(
            models.EmployerSearch, and_(models.EmployerSearch.id == models.EmployerJobDelivery.search_id,
                                       models.EmployerSearch.user_id == user_id),
        ).filter(models.EmployerJobDelivery.user_id == user_id,
                 models.EmployerJobDelivery.opening_key.is_(None)).all()
        legacy = {delivery.posting_id for delivery, _ in legacy_rows}
        legacy_identity_unavailable = False
        for delivery, saved_search in legacy_rows:
            saved_items = saved_search.items if saved_search is not None else None
            matches = [item for item in saved_items if isinstance(item, dict)
                       and isinstance(item.get("posting"), dict)
                       and item["posting"].get("id") == delivery.posting_id] if isinstance(saved_items, list) else []
            saved_key = matches[0]["posting"].get("opening_key") if len(matches) == 1 else None
            saved_charge = matches[0].get("charged_credits") if len(matches) == 1 else None
            if (not isinstance(saved_key, str) or not re.fullmatch(r"[0-9a-f]{64}", saved_key)
                    or type(saved_charge) is not int or saved_charge <= 0
                    or type(delivery.charged_credits) is not int or saved_charge != delivery.charged_credits):
                legacy_identity_unavailable = True
            else:
                previous.add(saved_key)
        from ..credits.discovery import membership as batch_membership
        batch_keys, batch_postings = batch_membership(db, user_id, list(unique), [item[0].id for item in unique.values()])
        previous.update(batch_keys)
        legacy.update(batch_postings)
    # Deliver unseen openings first; stable sorting retains deterministic fit
    # order within new and free saved matches from the same bounded index pool.
    selected = sorted(unique.values(), key=lambda item:
        item[0].opening_key in previous or item[0].id in legacy)[:payload.desired_count]
    if only_new:
        selected = [item for item in selected if item[0].opening_key not in previous and item[0].id not in legacy]
    new_count = sum(posting.opening_key not in previous and posting.id not in legacy for posting, _, _ in selected)
    if new_count and legacy_identity_unavailable:
        pending = history_mode == "indexed" and history_state in {"pending", "backfilling"}
        raise HTTPException(409, {"code": "paid_delivery_history_preparing" if pending else "legacy_delivery_identity_unavailable",
            "message": "Saved delivery history is being prepared. Retry this request to resume it." if pending
                       else "Saved delivery history needs review before charging for more openings."})
    items = [{"posting": _json_posting(posting, source), "fit": fit,
        "charged_credits": 0 if posting.opening_key in previous or posting.id in legacy else row.scope["price_quote"]["unit_price"],
        "preference_evaluation": evaluations[posting.id]} for posting, source, fit in selected]
    body_fingerprint = payload_fingerprint(items)
    scope_facts = {"kind": "verified_employer_index", "candidate_limit": 1000,
        "candidate_limit_reached": truncated, "unknown_publication_dates_excluded": bool(payload.published_within_days),
        "role_alias_policy": role_aliases.VERSION, "role_variants": role_variants,
        "preference_policy": POLICY_VERSION, "unknown_preference_metadata": "include",
        "candidates_before_preferences": len(evaluations),
        "known_preference_conflicts": sum(not value["eligible"] for value in evaluations.values()),
        "candidates_with_unknown_preferences": sum("unknown" in value["states"].values() for value in evaluations.values()),
        "search_input_fingerprint": row.input_fingerprint}
    return SimpleNamespace(items=items, body_fingerprint=body_fingerprint, scope_facts=scope_facts,
        selected=selected, stamps=stamps, evaluations=evaluations,
        role_variants=role_variants, truncated=truncated, history_generation=history_generation,
        progress_state=progress_state, history_state=history_state if history_mode == "indexed" else None,
        new_count=new_count, history_mode=history_mode)


def _stage_bounded_search(db, user_id, identity, token, prepared):
    # JSON serialization and history-source parsing are outside the User money
    # lock. The scalar history clock excludes overlapping evidence edits here.
    progress = db.query(models.EmployerPaidHistoryProgress).filter_by(user_id=user_id).populate_existing().with_for_update(nowait=True).one()
    if progress.generation != prepared.history_generation:
        raise HTTPException(409, {"code": "paid_history_changed", "message": "Paid job history changed during preparation. Start a new search."})
    row = _bounded_row(db, user_id, identity, lock=True)
    if row is None or row.status != "preparing" or row.scope.get("lease_token") != token:
        db.rollback()
        return None
    row.items = prepared.items
    row.scope = dict(row.scope) | {"staged_body_fingerprint": prepared.body_fingerprint}
    db.flush([row])
    db.refresh(progress, attribute_names=["generation", "state"])
    # Only this unfinished, undelivered row changed while the clock was fenced.
    progress.state = prepared.progress_state
    prepared.history_generation = progress.generation
    prepared.search_revision = db.query(models.EmployerSearch.history_revision).filter_by(id=identity, user_id=user_id).scalar()
    db.commit()
    return identity


def _fence_bounded_results(db, row, prepared):
    source_ids = sorted({source.id for _, source, _ in prepared.selected})
    posting_ids = sorted(posting.id for posting, _, _ in prepared.selected)
    source_mark = select(models.EmployerCatalogueSourceRevision.valid_from).where(
        models.EmployerCatalogueSourceRevision.source_id == models.EmployerSource.id,
        models.EmployerCatalogueSourceRevision.valid_to.is_(None)).scalar_subquery()
    posting_mark = select(models.EmployerCataloguePostingRevision.valid_from).where(
        models.EmployerCataloguePostingRevision.posting_id == models.EmployerPosting.id,
        models.EmployerCataloguePostingRevision.valid_to.is_(None)).scalar_subquery()
    sources = {record[0]: record for record in db.query(*(getattr(models.EmployerSource, name) for name in
        _BOUNDED_SOURCE_SCALARS), source_mark).filter(models.EmployerSource.id.in_(source_ids)).order_by(models.EmployerSource.id)
        .with_for_update(read=True, nowait=True, of=models.EmployerSource).all()}
    postings = {record[0]: record for record in db.query(*(getattr(models.EmployerPosting, name) for name in
        _BOUNDED_POSTING_SCALARS), posting_mark).filter(models.EmployerPosting.id.in_(posting_ids)).order_by(models.EmployerPosting.id)
        .with_for_update(read=True, nowait=True, of=models.EmployerPosting).all()}
    cutoff = utcnow() - timedelta(hours=row.scope["max_source_age_hours"])
    for posting, source, _ in prepared.selected:
        current_source, current_posting = sources.get(source.id), postings.get(posting.id)
        if (current_source is None or current_posting is None
                or tuple(current_source[:-1]) != tuple(getattr(source, name) for name in _BOUNDED_SOURCE_SCALARS)
                or tuple(current_posting[:-1]) != tuple(getattr(posting, name) for name in _BOUNDED_POSTING_SCALARS)
                or (current_source[-1], current_posting[-1]) != prepared.stamps[posting.id]
                or not source.enabled or not posting.is_open or _utc(posting.last_checked_at) < cutoff):
            raise HTTPException(409, {"code": "job_changed", "message": "A selected employer or job changed during preparation. Start a new search."})
    if _bounded_resume_revision(db, row.user_id, row.resume_id, fence=True) != row.scope["resume_source_revision"]:
        raise HTTPException(409, {"code": "resume_changed", "message": "The resume changed during preparation. Start a new search."})


def _finish_bounded_search(db, user_id, identity, token, prepared):
    from . import catalogue_search, delivery_history
    row = _bounded_row(db, user_id, identity, lock=True)
    if row is None or row.status != "preparing" or row.scope.get("lease_token") != token:
        db.rollback()
        return _bounded_row(db, user_id, identity)
    # Source/resume prerequisites, then scalar progress, then NOWAIT owner. No
    # public body is projected or ranked after the financial owner fence.
    if _utc(row.created_at) < utcnow() - _BOUNDED_ABANDONMENT:
        raise HTTPException(409, {"code": "search_preparation_expired", "message": "This search preparation expired without completion."})
    _fence_bounded_results(db, row, prepared)
    progress = db.query(models.EmployerPaidHistoryProgress).filter_by(user_id=user_id).populate_existing().with_for_update(nowait=True).one()
    catalogue_search.lock_owner_nowait(db, user_id)
    if (progress.generation != prepared.history_generation or row.history_revision != prepared.search_revision
            or row.scope.get("staged_body_fingerprint") != prepared.body_fingerprint):
        raise HTTPException(409, {"code": "paid_history_changed", "message": "Paid job history changed during preparation. Start a new search."})
    reservation = _bounded_reservation(db, row, lock=True)
    if prepared.history_mode == "indexed":
        previous, physical, state = delivery_history.membership(db, user_id,
            [posting.opening_key for posting, _, _ in prepared.selected], [posting.id for posting, _, _ in prepared.selected])
    else:
        previous = {record[0] for record in db.query(models.EmployerJobDelivery.opening_key).filter_by(user_id=user_id).filter(
            models.EmployerJobDelivery.opening_key.in_([posting.opening_key for posting, _, _ in prepared.selected])).all()}
        physical = {record[0] for record in db.query(models.EmployerJobDelivery.posting_id).filter_by(user_id=user_id).filter(
            models.EmployerJobDelivery.posting_id.in_([posting.id for posting, _, _ in prepared.selected])).all()}
        from ..credits.discovery import membership as batch_membership
        batch_keys, batch_postings = batch_membership(db, user_id,
            [posting.opening_key for posting, _, _ in prepared.selected], [posting.id for posting, _, _ in prepared.selected])
        previous.update(batch_keys)
        physical.update(batch_postings)
        state = None
    # The history clock covers native insertion/source changes. This bounded
    # reread also protects independent-session physical/canonical paid replays.
    for item in prepared.items:
        already_paid = item["posting"]["opening_key"] in previous or item["posting"]["id"] in physical
        if item["charged_credits"] > 0 and already_paid:
            raise HTTPException(409, {"code": "paid_history_changed", "message": "A job was already delivered during preparation. Start a new search."})
    if prepared.new_count and prepared.history_mode == "indexed" and state != "ready":
        raise HTTPException(409, {"code": "paid_history_changed", "message": "Paid job history needs review before charging."})
    deliveries = [models.EmployerJobDelivery(id=public_id("delivery"), user_id=user_id, posting_id=item["posting"]["id"],
        opening_key=item["posting"]["opening_key"], search_id=identity, charged_credits=item["charged_credits"])
        for item in prepared.items if item["charged_credits"]]
    row.status, row.delivered_count = "completed", len(prepared.items)
    row.charged_credits = prepared.new_count * reservation.unit_price
    row.refunded_credits = reservation.reserved_amount - row.charged_credits
    row.scope = dict(row.scope) | prepared.scope_facts | {"lease_token": None, "lease_until": None}
    db.add_all(deliveries)
    db.flush(deliveries)
    if prepared.history_mode != "off":
        # Use the exact already-staged body as proof; never decode it under the
        # owner lock. The scalar revision and private digest bind that body.
        proof_search = SimpleNamespace(id=row.id, user_id=user_id, history_revision=row.history_revision, items=prepared.items)
        delivery_history.write_completed(db, proof_search, deliveries, prepared.selected,
            restore_ready=prepared.history_mode == "indexed" and state == "ready")
    credits.settle(db, reservation, completed_count=prepared.new_count, reason="Unique useful employer jobs delivered")
    db.commit()
    return row


def cancel_bounded_search(db, user_id, identity, *, reason="cancelled_by_owner"):
    from ..dispatch.models import DispatchOutbox
    from . import catalogue_search
    owner = db.query(models.EmployerSearch.user_id).filter_by(id=identity).scalar()
    if owner is None or (user_id is not None and owner != user_id):
        raise HTTPException(404, "Search preparation not found")
    db.commit()
    catalogue_search.lock_owner_nowait(db, owner)
    row = _bounded_row(db, owner, identity, lock=True)
    if row is None or row.scope.get("bounded_protocol") != BOUNDED_SEARCH_PROTOCOL:
        raise HTTPException(404, "Search preparation not found")
    if row.status == "preparing":
        reservation = _bounded_reservation(db, row, lock=True)
        credits.settle(db, reservation, completed_count=0, reason="Bounded search ended before useful delivery")
        row.status, row.charged_credits, row.delivered_count = "cancelled" if reason == "cancelled_by_owner" else "failed", 0, 0
        row.refunded_credits = reservation.released_amount
        row.scope = dict(row.scope) | {"failure_reason": reason, "lease_token": None, "lease_until": None}
    db.query(DispatchOutbox).filter_by(topic="employer.search", aggregate_id=identity).filter(
        ~DispatchOutbox.status.in_(("completed", "failed"))).update({"status": "completed", "completed_at": utcnow(),
            "lease_token": None, "lease_until": None, "last_error": "bounded_search_ended"}, synchronize_session=False)
    db.commit()
    return row


def bounded_search_status(db, user_id, identity):
    row = _bounded_row(db, user_id, identity)
    if row is None or row.scope.get("bounded_protocol") != BOUNDED_SEARCH_PROTOCOL:
        raise HTTPException(404, "Search preparation not found")
    hold = db.query(models.ServiceCreditReservation.state, models.ServiceCreditReservation.reserved_amount,
        models.ServiceCreditReservation.released_amount).filter_by(user_id=user_id, source_id=identity, operation="job_search").first()
    return {"id": row.id, "kind": "search_preparation", "status": "pending" if row.status == "preparing" else row.status,
        "as_of": row.created_at, "desired_count": row.desired_count, "accepted_quote": row.scope["price_quote"],
        "candidates_examined": row.scope.get("candidates_before_preferences", 0), "charged_credits": row.charged_credits,
        "completed_search_id": row.id if row.status == "completed" else None, "failure_reason": row.scope.get("failure_reason"),
        "reserved_credits": hold[1] if hold is not None and hold[0] == "reserved" else 0,
        "refunded_credits": hold[2] if hold is not None else row.refunded_credits}


def bounded_search_maintenance(db):
    row = db.query(models.EmployerSearch.id, models.EmployerSearch.user_id).filter(
        models.EmployerSearch.status == "preparing", models.EmployerSearch.created_at < utcnow() - _BOUNDED_ABANDONMENT,
        models.EmployerSearch.scope["bounded_protocol"].as_string() == BOUNDED_SEARCH_PROTOCOL).order_by(
        models.EmployerSearch.created_at, models.EmployerSearch.id).limit(1).first()
    db.commit()
    if row is not None:
        cancel_bounded_search(db, row.user_id, row.id, reason="abandoned_without_completion")
    from ..credits import discovery
    discovery.maintenance(db)


def search_preparation_status(db, user_id, identity):
    bounded = db.query(models.EmployerSearch.id).filter_by(id=identity, user_id=user_id).filter(
        models.EmployerSearch.scope["bounded_protocol"].as_string() == BOUNDED_SEARCH_PROTOCOL).first()
    if bounded is not None:
        return bounded_search_status(db, user_id, identity)
    from . import catalogue_search
    return catalogue_search.status(db, user_id, identity)


def cancel_search_preparation(db, user_id, identity, *, reason="cancelled_by_owner"):
    bounded = db.query(models.EmployerSearch.id).filter_by(id=identity, user_id=user_id).filter(
        models.EmployerSearch.scope["bounded_protocol"].as_string() == BOUNDED_SEARCH_PROTOCOL).first()
    if bounded is not None:
        cancel_bounded_search(db, user_id, identity, reason=reason)
        return bounded_search_status(db, user_id, identity)
    from . import catalogue_search
    return catalogue_search.cancel(db, user_id, identity, reason=reason)


def resume_bounded_search(db, user_id, identity):
    row, token = _claim_bounded_search(db, user_id, identity)
    if token is None:
        return row
    try:
        if not config.discovery_enabled():
            return cancel_bounded_search(db, user_id, identity, reason="discovery_disabled_before_delivery")
        if _utc(row.created_at) < utcnow() - _BOUNDED_ABANDONMENT:
            return cancel_bounded_search(db, user_id, identity, reason="abandoned_without_completion")
        payload = schemas.SearchCreate.model_validate(dict(row.query) | {"idempotency_key": row.idempotency_key})
        if payload_fingerprint(_search_input(payload)) != row.input_fingerprint:
            raise ValueError("Accepted bounded search query changed")
        prepared = _prepare_bounded_search(db, row, payload)
        if _stage_bounded_search(db, user_id, identity, token, prepared) is None:
            return _bounded_row(db, user_id, identity)
        return _finish_bounded_search(db, user_id, identity, token, prepared)
    except HTTPException as exc:
        db.rollback()
        if isinstance(exc.detail, dict) and exc.detail.get("code") == "paid_delivery_history_preparing":
            from . import catalogue_search
            catalogue_search.lock_owner_nowait(db, user_id)
            pending = _bounded_row(db, user_id, identity, lock=True)
            if pending is not None and pending.status == "preparing" and pending.scope.get("lease_token") == token:
                pending.scope = dict(pending.scope) | {"lease_token": None, "lease_until": None}
            db.commit()
            return pending
        cancel_bounded_search(db, user_id, identity, reason=exc.detail.get("code", "preparation_failed")
            if isinstance(exc.detail, dict) else "preparation_failed")
        raise
    except Exception:
        db.rollback()
        cancel_bounded_search(db, user_id, identity, reason="preparation_failed")
        raise


def search_response(row):
    result = {key: getattr(row, key) for key in (
        "id", "status", "desired_count", "delivered_count", "reserved_credits", "charged_credits", "refunded_credits", "created_at", "query", "scope",
    )}
    result["items"] = row.items if row.status == "completed" else []
    from ..credits.discovery import public_summary
    return public_summary(result)


def finish_catalogue_preparation(db, identity, token):
    from . import catalogue_search
    return catalogue_search.finish(db, identity, token)


def list_searches(db, user_id, limit=30):
    return [search_response(row) for row in db.query(models.EmployerSearch).filter_by(user_id=user_id).order_by(models.EmployerSearch.created_at.desc()).limit(limit).all()]


def get_search(db, user_id, identity):
    return search_response(_owned(db, models.EmployerSearch, identity, user_id))


def _selection_snapshot(db, user_id, selection):
    resume = _owned(db, core.Resume, selection.resume_id, user_id)
    resume_snapshot = SimpleNamespace(**{key: copy.deepcopy(getattr(resume, key)) for key in ("id", "source_document", "source_format", "original_filename")})
    version_snapshot = None
    if selection.resume_choice == "tailored":
        version = _owned(db, core.ResumeVersion, selection.resume_version_id, user_id)
        if version.resume_id != resume.id or version.approval_state != "approved":
            raise HTTPException(422, "Select an approved tailored version of this resume")
        content = version.structured_content or {}
        if not content.get("source_edits"):
            raise HTTPException(422, "An unchanged source snapshot is not a tailored resume")
        for identity in version.evidence_ids or []:
            if not db.query(core.EvidenceItem.id).filter_by(id=identity, user_id=user_id, approval_state="approved").first():
                raise HTTPException(409, "Tailored resume evidence needs approval")
        version_snapshot = SimpleNamespace(**{key: copy.deepcopy(getattr(version, key)) for key in ("structured_content", "label", "version_number")})
    return resume_snapshot, version_snapshot


def _artifact_row(sealed, user_id, selection):
    return models.SealedApplicationArtifact(
        id=public_id("artifact"), user_id=user_id, resume_id=selection.resume_id,
        resume_version_id=selection.resume_version_id, sha256=sealed.sha256,
        filename=sealed.filename, media_type=sealed.media_type, size_bytes=len(sealed.content),
        content=None if sealed.gcs_object else sealed.content,
        gcs_object=sealed.gcs_object, gcs_generation=sealed.gcs_generation,
    )


def _package_digest(application, posting, source, artifact):
    content = {
        "employer": source.employer, "source_id": source.id, "platform": source.platform,
        "tenant": source.board_token, "region": source.region, "recipient_hosts": source.allowed_hosts,
        "submission_grant": source.submission_grant, "receipt_contract": source.receipt_contract,
        "credential_reference": source.credential_env,
        "posting_id": posting.id, "job_sha256": application.job_content_sha256,
        "destination": posting.apply_url, "form_version": application.form["version"],
        "artifact_sha256": artifact.sha256, "resume_choice": application.resume_choice,
        "answers": application.answers, "consents": application.consents,
        "credit_cost": application.credit_cost, "application_mode": application.application_mode,
    }
    if application.admission_snapshot:
        content.update({"admission_snapshot": application.admission_snapshot,
                        "pricing_snapshot": application.pricing_snapshot,
                        "opening_key": admissions.opening_identity(posting, source),
                        "employer_policy": config.employer_limits(source)})
    return payload_fingerprint(content)


def _reviewed_prices():
    from ...billing.cost_policy import CostPolicyUnavailable
    try:
        return config.prices()
    except CostPolicyUnavailable as exc:
        raise HTTPException(503, detail=str(exc)) from None


def _quote_application(application, posting, source):
    application.employer_key = admissions.employer_identity(source)
    application.opening_key = admissions.opening_identity(posting, source)
    posting.opening_key = application.opening_key
    application.admission_snapshot = admissions.quote(source, posting)
    from ...billing.cost_policy import CostPolicyUnavailable
    try:
        prices = config.prices()
    except CostPolicyUnavailable as exc:
        if application.application_mode != "manual":
            raise HTTPException(503, detail=str(exc)) from None
        prices = {"pricing_version": "manual-zero", "cost_policy_snapshot": None}
    application.pricing_snapshot = {"unit_price": application.credit_cost,
                                    "pricing_version": prices["pricing_version"],
                                    "cost_policy_snapshot": prices["cost_policy_snapshot"]}


def missing_fields(application):
    answers = application.answers or {}
    form = application.form or {}
    provided = {key for key, value in answers.items() if value not in ("", [], None)} | {"resume"}
    missing = [field["id"] for field in form.get("fields", []) if field.get("required") and field["id"] not in provided]
    for group in form.get("required_groups", []):
        if not any(identity in provided for identity in group):
            missing.append(group[0] if group else "unknown-required-question")
    for consent in form.get("consents", []):
        if consent.get("required") and (application.consents or {}).get(consent["id"]) is not True:
            missing.append(consent["id"])
    return list(dict.fromkeys(missing))


def _validate_answers(form, answers, consents):
    fields = {field["id"]: field for field in form.get("fields", [])}
    if set(answers) - set(fields):
        raise HTTPException(422, "Answers include questions that are not in the current form")
    if set(consents) - {item["id"] for item in form.get("consents", [])}:
        raise HTTPException(422, "Consent choices do not match the current employer policies")
    for identity, value in answers.items():
        field = fields[identity]
        if field["type"] in {"file", "hidden"}:
            raise HTTPException(422, "File and hidden fields cannot be supplied as text answers")
        if field["type"] == "multi_select":
            if not isinstance(value, list):
                raise HTTPException(422, f"{field['label']} requires a list of selections")
        elif not isinstance(value, str):
            raise HTTPException(422, f"{field['label']} requires one text value")
        if field["type"] in {"single_select", "multi_select"}:
            valid = {option["value"] for option in field.get("options", [])}
            if any(item not in valid for item in (value if isinstance(value, list) else [value])):
                raise HTTPException(422, f"Choose a valid option for {field['label']}")
        if field["type"] == "email" and value and not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", value):
            raise HTTPException(422, "Enter a valid email address")
        if identity in {"first_name", "last_name", "email", "phone"} and len(value) > 255:
            raise HTTPException(422, f"{field['label']} exceeds the employer's maximum length")


def create_application(db: Session, user_id: int, payload: schemas.ApplicationCreate):
    _require_enabled()
    fingerprint = payload_fingerprint(payload.model_dump(exclude={"idempotency_key"}))
    existing = db.query(models.EmployerApplication).filter_by(user_id=user_id, idempotency_key=payload.idempotency_key).first()
    if existing:
        if existing.input_fingerprint != fingerprint:
            raise HTTPException(409, "This request key was already used with another application")
        return existing
    posting = _posting(db, payload.posting_id)
    source = _source(db, posting.source_id)
    if _source_mode(source) == "api":
        from ..credits.runtime import requires_unified_billing
        if requires_unified_billing(db, user_id):
            raise HTTPException(409, "Verified unified-credit submission is not enabled. Use reviewed browser assistance or the employer portal.")
    if not posting.is_open or not source.enabled:
        raise HTTPException(409, "This employer job is no longer available")
    source_snapshot = connectors.source_contract(source)
    posting_external_id = posting.external_id
    resume_snapshot, version_snapshot = _selection_snapshot(db, user_id, payload)
    db.rollback()  # No rendering/storage/provider call while holding a transaction.
    try:
        form = connectors.load_form(source_snapshot, posting_external_id)
        prepared = artifacts.materialize_for_application(resume_snapshot, version_snapshot)
    except connectors.ConnectorError as exc:
        raise HTTPException(503 if exc.safe_to_retry else 409, "Employer form could not be verified: " + exc.code) from exc
    with artifacts.guarded_storage(db, prepared, user_id=user_id) as (sealed, upload_id):
        admissions.lock_application_set(db, user_id)
        if not db.query(core.User).filter(core.User.id == user_id).with_for_update().first():
            raise HTTPException(409, "Account was deleted while the application was being prepared")
        existing = db.query(models.EmployerApplication).filter_by(user_id=user_id, idempotency_key=payload.idempotency_key).first()
        if existing:
            if existing.input_fingerprint != fingerprint:
                raise HTTPException(409, "This request key was already used with another application")
            return existing
        opening_key = admissions.opening_identity(posting, source)
        active_key = f"{user_id}:{opening_key}"
        if db.query(models.EmployerApplication.id).filter_by(user_id=user_id).filter(
            or_(models.EmployerApplication.opening_key == opening_key, models.EmployerApplication.posting_id == posting.id),
            or_(models.EmployerApplication.active_key.is_not(None), models.EmployerApplication.status == "user_reported"),
        ).first():
            raise HTTPException(409, {"code": "canonical_opening_conflict", "message": "An application already exists for this opening, including another source or language version. Review its status before creating another."})
        open_count = db.query(models.EmployerApplication.id).filter_by(user_id=user_id).filter(
            models.EmployerApplication.status.notin_(["confirmed", "failed", "cancelled", "user_reported"]),
        ).count()
        if open_count >= config.positive_int("EMPLOYER_MAX_OPEN_APPLICATIONS", 100, 1000):
            raise HTTPException(429, "Complete or cancel existing prepared applications before preparing more")
        posting = _posting(db, payload.posting_id)
        source = _source(db, posting.source_id)
        if not posting.is_open or not source.enabled:
            raise HTTPException(409, "This employer job was closed or disabled while the application was being prepared")
        # Candidate-owned selections and evidence may have changed during rendering.
        _selection_snapshot(db, user_id, payload)
        artifact = _artifact_row(sealed, user_id, payload)
        db.add(artifact)
        db.flush([artifact])
        mode = _source_mode(source, form)
        application = models.EmployerApplication(
            id=public_id("apply"), user_id=user_id, posting_id=posting.id, active_key=active_key,
            idempotency_key=payload.idempotency_key, input_fingerprint=fingerprint,
            resume_id=payload.resume_id, resume_choice=payload.resume_choice, resume_version_id=payload.resume_version_id,
            artifact_id=artifact.id, form=form, answers={}, consents={}, package_digest="",
            job_content_sha256=posting.content_sha256, application_mode=mode,
            status="needs_action" if form["fields"] else "ready", credit_cost=0 if mode == "manual" else int(_reviewed_prices()["apply_credits_per_job"]),
        )
        _quote_application(application, posting, source)
        application.package_digest = _package_digest(application, posting, source, artifact)
        db.add(application)
        artifacts.attach_upload(db, upload_id, user_id=user_id)
        db.commit()
        return application


def update_package(db, user_id, identity, payload: schemas.PackageUpdate):
    application = _owned(db, models.EmployerApplication, identity, user_id)
    if application.status in LOCKED_APPLICATION_STATES or application.status == "cancelled":
        raise HTTPException(409, "This application can no longer be edited")
    from ..credits import submission as unified

    if unified.is_unified(application):
        unified.require_editable(db, application)
    _validate_answers(application.form, payload.answers, payload.consents)
    resume_snapshot, version_snapshot = _selection_snapshot(db, user_id, payload)
    db.rollback()
    prepared = artifacts.materialize_for_application(resume_snapshot, version_snapshot)
    with artifacts.guarded_storage(db, prepared, user_id=user_id) as (sealed, upload_id):
        application = _owned(db, models.EmployerApplication, identity, user_id, lock=True)
        if application.status in LOCKED_APPLICATION_STATES or application.status == "cancelled":
            raise HTTPException(409, "Application execution started while this package was being edited")
        if unified.is_unified(application):
            unified.require_editable(db, application)
        _selection_snapshot(db, user_id, payload)
        artifact = _artifact_row(sealed, user_id, payload)
        db.add(artifact)
        db.flush([artifact])
        application.artifact_id = artifact.id
        application.resume_id, application.resume_choice = payload.resume_id, payload.resume_choice
        application.resume_version_id = payload.resume_version_id
        application.answers, application.consents = payload.answers, payload.consents
        application.approved_at, application.approved_digest, application.approval_expires_at = None, None, None
        db.query(models.EmployerApplicationApproval).filter_by(application_id=application.id, revoked_at=None).update({"revoked_at": utcnow()}, synchronize_session=False)
        application.allowed_actions = []
        application.batch_id = None
        application.cancel_requested = False
        application.error_code, application.error_message = None, None
        application.status = "needs_action" if missing_fields(application) else "ready"
        posting = _posting(db, application.posting_id)
        source = _source(db, posting.source_id)
        if unified.is_unified(application):
            unified.reset_unquoted(db, application, posting, source)
        else:
            _quote_application(application, posting, source)
        application.package_digest = _package_digest(application, posting, source, artifact)
        artifacts.attach_upload(db, upload_id, user_id=user_id)
        db.commit()
        return application


def application_response(db, application):
    from ..credits import submission as unified

    posting = _posting(db, application.posting_id)
    source = _source(db, posting.source_id)
    artifact = _owned(db, models.SealedApplicationArtifact, application.artifact_id, application.user_id)
    result = {key: getattr(application, key) for key in (
        "id", "resume_id", "resume_choice", "resume_version_id", "status", "application_mode", "form", "package_digest", "answers", "consents", "credit_cost", "charged_credits", "receipt", "approval_expires_at", "cancel_requested", "created_at", "updated_at", "opening_key", "employer_key", "admission_snapshot", "pricing_snapshot", "batch_id",
    )}
    result.update({
        "posting": posting_response(posting, source), "missing_fields": missing_fields(application),
        "artifact": {"sha256": artifact.sha256, "filename": artifact.filename, "size_bytes": artifact.size_bytes,
                     "media_type": artifact.media_type, "preview_url": f"/api/v1/employer-jobs/applications/{application.id}/artifact"},
        "requires_user_action": application.status in {"needs_action", "unknown", "manual_handoff", "failed"},
        "error": {"code": application.error_code, "message": application.error_message} if application.error_code else None,
        "handoff_url": posting.apply_url if application.application_mode == "manual" else None,
        "reserved_credits": 0,
        "admission": admissions.response(db, application),
    })
    report = application.receipt if isinstance(application.receipt, dict) and application.receipt.get("source") == "candidate_report" else None
    result["candidate_report"] = copy.deepcopy(report)
    if report is not None:
        result["receipt"] = None  # Candidate assertions are never provider receipts.
    if application.reservation_id:
        reservation = db.query(models.ServiceCreditReservation).filter_by(id=application.reservation_id).one()
        result["reserved_credits"] = reservation.reserved_amount if reservation.state == "reserved" else 0
    elif unified.is_unified(application):
        result["billing_currency"] = "credits"
        if (application.pricing_snapshot or {}).get("quote_id"):
            result["billing"] = copy.deepcopy(application.pricing_snapshot)
        reservation = unified.reservation(db, application)
        if reservation is not None:
            reservation, _ = unified.held(db, application)
            result["reserved_credits"] = reservation.reserved_credits if reservation.state == "reserved" else 0
    return result


def approve_application(db, user_id, identity, payload: schemas.ApprovalCreate, *, commit=True):
    application = _owned(db, models.EmployerApplication, identity, user_id, lock=True)
    if application.status in LOCKED_APPLICATION_STATES or application.status == "cancelled":
        raise HTTPException(409, "This application cannot be approved in its current state")
    from ..credits import submission as unified

    if unified.is_unified(application):
        raise HTTPException(409, "Review and accept the exact three-credit quote in browser assistance")
    if payload.package_digest != application.package_digest:
        raise HTTPException(409, "The reviewed application has changed; review it again")
    if missing_fields(application):
        raise HTTPException(422, "Complete every required employer question before approval")
    _validate_answers(application.form, application.answers, application.consents)
    if application.application_mode == "api" and not {"upload", "submit"}.issubset(set(payload.allowed_actions)):
        raise HTTPException(422, "Automatic submission requires explicit resume upload and submit approval")
    application.allowed_actions = sorted(set(payload.allowed_actions))
    application.approved_digest = payload.package_digest
    application.approved_at = utcnow()
    application.approval_expires_at = utcnow() + timedelta(hours=24)
    application.status = "approved"
    posting = _posting(db, application.posting_id)
    source = _source(db, posting.source_id)
    artifact = _owned(db, models.SealedApplicationArtifact, application.artifact_id, user_id)
    admissions._policies(application, source, utcnow())
    if _package_digest(application, posting, source, artifact) != application.package_digest:
        raise HTTPException(409, {"code": "package_policy_changed", "message": "The employer policy or application package changed. Save and review the package again."})
    db.add(models.EmployerApplicationApproval(
        id=public_id("approval"), application_id=application.id, package_digest=application.package_digest,
        review_snapshot={"employer": source.employer, "tenant": source.board_token, "source_id": source.id,
                        "job": _json_posting(posting, source), "form": copy.deepcopy(application.form),
                        "answers": copy.deepcopy(application.answers), "consents": copy.deepcopy(application.consents),
                        "artifact_sha256": artifact.sha256, "artifact_id": artifact.id, "filename": artifact.filename,
                        "credit_cost": application.credit_cost, "resume_choice": application.resume_choice,
                        "receipt_contract": copy.deepcopy(source.receipt_contract), "submission_grant": source.submission_grant,
                        "admission_snapshot": copy.deepcopy(application.admission_snapshot),
                        "pricing_snapshot": copy.deepcopy(application.pricing_snapshot), "batch_id": application.batch_id},
        allowed_actions=application.allowed_actions, approved_at=application.approved_at,
        expires_at=application.approval_expires_at,
    ))
    if commit:
        db.commit()
    return application


def request_execution(db, user_id, identity, payload: schemas.ExecuteCreate, *, commit=True):
    application = _owned(db, models.EmployerApplication, identity, user_id, lock=True)
    if application.status in {"queued", "submitting", "confirmed", "unknown"}:
        if payload.package_digest != application.package_digest:
            raise HTTPException(409, "The application package does not match")
        return application
    if application.status != "approved" or application.approved_digest != payload.package_digest:
        raise HTTPException(409, "Review and approve this exact application before execution")
    if application.cancel_requested or not application.approval_expires_at or _utc(application.approval_expires_at) <= utcnow():
        raise HTTPException(409, "Application approval has expired or was cancelled")
    if application.application_mode == "manual":
        application.status = "manual_handoff"
        if commit:
            db.commit()
        return application
    from ..credits.runtime import requires_unified_billing
    if requires_unified_billing(db, user_id):
        raise HTTPException(409, "Verified unified-credit submission is not enabled. Existing submitted applications remain recoverable.")
    posting, source = _posting(db, application.posting_id), None
    source = _source(db, posting.source_id)
    if _source_mode(source, application.form) != "api":
        raise HTTPException(409, "Automatic submission is no longer enabled for this employer")
    artifact = _owned(db, models.SealedApplicationArtifact, application.artifact_id, user_id)
    if _package_digest(application, posting, source, artifact) != application.package_digest:
        raise HTTPException(409, {"code": "package_policy_changed", "message": "The employer policy or application package changed. Save and review the package again."})
    from .batches import validate_binding
    validate_binding(db, application)
    admissions.reserve(db, application, source)
    reservation = credits.reserve(db, user_id=user_id, operation="job_application", source_id=application.id,
                                  unit_price=application.credit_cost, count=1,
                                  pricing_version=application.pricing_snapshot["pricing_version"])
    db.flush([reservation])
    application.reservation_id = reservation.id
    application.status = "queued"
    from ..dispatch.service import enqueue
    enqueue(db, topic="employer.apply", aggregate_id=application.id, payload={"application_id": application.id},
            key=f"employer.apply:{application.id}:{application.package_digest}")
    if commit:
        db.commit()
    return application


def cancel_application(db, user_id, identity, *, commit=True):
    application = _owned(db, models.EmployerApplication, identity, user_id, lock=True)
    if application.status == "user_reported":
        raise HTTPException(409, "Your manual submission report is retained. It is not an employer-confirmed receipt; further execution is stopped.")
    if application.status == "confirmed":
        raise HTTPException(409, "The employer already received this application; future work can be stopped but it cannot be recalled here")
    from ..credits import submission as unified

    if unified.is_unified(application):
        unified.cancel_application(db, application, now=utcnow())
        db.query(models.EmployerApplicationApproval).filter_by(application_id=application.id, revoked_at=None).update({"revoked_at": utcnow()}, synchronize_session=False)
        if commit:
            db.commit()
        return application
    if application.status in {"submitting", "unknown"}:
        application.cancel_requested = True
        application.approved_digest = None
        application.error_message = "Cancellation requested. Data may already have reached the employer; outcome must be reconciled."
        admissions.finish(db, application, "unknown")
    else:
        application.status = "cancelled"
        application.cancel_requested = True
        application.cancelled_at = utcnow()
        application.approved_digest = None
        application.active_key = None
        admissions.finish(db, application, "cancelled")
        if application.reservation_id:
            reservation = db.query(models.ServiceCreditReservation).filter_by(id=application.reservation_id).with_for_update().one()
            credits.settle(db, reservation, completed_count=0, reason="Cancelled before automatic submission")
    db.query(models.EmployerApplicationApproval).filter_by(application_id=application.id, revoked_at=None).update({"revoked_at": utcnow()}, synchronize_session=False)
    if commit:
        db.commit()
    return application


def create_source(db, admin_id, payload: schemas.SourceCreate):
    for value in (payload.careers_url, payload.verification_url):
        parsed = urlsplit(value)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.port not in {None, 443}:
            raise HTTPException(422, "Employer verification and careers URLs must use HTTPS")
    allowed = []
    for host in payload.allowed_hosts:
        if not re.fullmatch(r"[a-z0-9][a-z0-9.-]+\.[a-z]{2,}", host) or host.startswith("."):
            raise HTTPException(422, "Only explicit DNS hostnames may be allowed")
        allowed.append(host.lower())
    if urlsplit(payload.careers_url).hostname not in allowed:
        raise HTTPException(422, "The verified employer careers hostname must be explicitly allowed")
    source = models.EmployerSource(id=public_id("source"), verified_by=admin_id,
                                   **payload.model_dump(exclude={"allowed_hosts"}), allowed_hosts=allowed)
    db.add(source)
    db.add(core.AdminAuditEvent(id=public_id("audit"), actor_user_id=admin_id,
        actor_email=db.query(core.User.email).filter_by(id=admin_id).scalar(), action="verify_employer_source",
        target_type="employer_source", target_id=source.id, reason=payload.verification_note,
        after_state={"employer": source.employer, "platform": source.platform, "submission_enabled": source.submission_enabled}))
    db.commit()
    return source


def enqueue_source_refresh(db, identity):
    source = _source(db, identity)
    if not source.enabled:
        raise HTTPException(409, "This source is disabled")
    from ..dispatch.service import enqueue
    # One pending source refresh per five-minute scheduling window.
    window = int(utcnow().timestamp()) // 300
    event = enqueue(db, topic="employer.refresh", aggregate_id=source.id, payload={"source_id": source.id},
                    key=f"employer.refresh:{source.id}:{window}")
    db.commit()
    return event


def enqueue_due_sources(db: Session, limit: int = 100) -> int:
    if not config.discovery_enabled():
        return 0
    from ..dispatch.service import enqueue
    due = db.query(models.EmployerSource).filter(
        models.EmployerSource.enabled.is_(True), models.EmployerSource.next_refresh_at <= utcnow(),
    ).order_by(models.EmployerSource.next_refresh_at).with_for_update(skip_locked=True).limit(min(limit, 500)).all()
    window = int(utcnow().timestamp()) // 300
    for source in due:
        enqueue(db, topic="employer.refresh", aggregate_id=source.id, payload={"source_id": source.id},
                key=f"employer.refresh:{source.id}:{window}")
        source.next_refresh_at = utcnow() + timedelta(minutes=15)
    return len(due)


def reconcile_application(db: Session, identity: str, admin, payload: schemas.ReconciliationCreate):
    application = db.query(models.EmployerApplication).filter_by(id=identity).with_for_update().first()
    if not application:
        raise HTTPException(404, "Application not found")
    if application.status != "unknown":
        raise HTTPException(409, "Only an unknown application needs this reconciliation")
    if application.application_mode != "api":
        raise HTTPException(409, {"code": "native_browser_reconciliation_required",
            "message": "Browser submissions require their original native recovery evidence. This API reconciliation cannot release a possible browser send."})
    posting = _posting(db, application.posting_id)
    if payload.employer_posting_id != posting.external_id:
        raise HTTPException(422, "The verified employer receipt does not match this posting")
    reservation = db.query(models.ServiceCreditReservation).filter_by(id=application.reservation_id).with_for_update().one()
    source = _source(db, posting.source_id)
    before = {"status": application.status, "held_credits": reservation.reserved_amount}
    if payload.outcome == "confirmed":
        approval = db.query(models.EmployerApplicationApproval).filter_by(
            application_id=application.id, package_digest=application.package_digest,
        ).order_by(models.EmployerApplicationApproval.approved_at.desc()).first()
        contract = (approval.review_snapshot.get("receipt_contract") if approval else source.receipt_contract) or {}
        provider_receipt = payload.provider_receipt or {}
        receipt_id = provider_receipt.get(contract.get("receipt_id_field", ""))
        complete = provider_receipt.get(contract.get("completion_field", ""))
        if not receipt_id or "completion_value" not in contract or type(complete) is not type(contract["completion_value"]) or complete != contract["completion_value"] or provider_receipt.get("errors"):
            raise HTTPException(422, "Provider receipt does not prove a complete application under this employer's verified contract")
        application.receipt = {"provider": source.platform, "application_id": str(receipt_id),
            "completion_verified": True, "received_at": utcnow().isoformat(), "reconciliation_proof": payload.proof_reference,
            "response_sha256": payload_fingerprint(provider_receipt)}
        credits.settle(db, reservation, completed_count=1, reason="Complete employer application independently verified")
        application.status, application.charged_credits = "confirmed", reservation.committed_amount
        application.error_code, application.error_message = None, None
        admissions.finish(db, application, "confirmed")
    else:
        attempts = db.query(models.EmployerApplicationAttempt).filter_by(
            application_id=application.id).with_for_update().limit(2).all()
        if (len(attempts) != 1 or attempts[0].package_digest != application.package_digest
                or attempts[0].completed_at is None or attempts[0].state != "unknown"):
            # Redelivery can mark the application unknown while its original
            # sender is still inside the one external POST. Neither elapsed
            # time nor a missing worker proves that sender has stopped.
            raise HTTPException(409, {"code": "application_sender_outcome_unresolved",
                "message": "The original sender has no recorded completed outcome. Credits and the application hold remain reserved; use manual support without resending."})
        credits.settle(db, reservation, completed_count=0, reason="Provider verified no application was submitted")
        application.status, application.active_key = "failed", None
        application.approved_digest = None
        application.error_code = "verified_not_submitted"
        application.error_message = "The employer independently verified that no application was submitted; reserved credits were returned."
        admissions.finish(db, application, "verified_not_submitted", proven_not_submitted=True)
    db.add(core.AdminAuditEvent(id=public_id("audit"), actor_user_id=admin.id, actor_email=admin.email,
        action="reconcile_employer_application", target_type="employer_application", target_id=application.id,
        reason=payload.reason, before_state=before,
        after_state={"status": application.status, "charged_credits": application.charged_credits,
                     "proof_kind": payload.proof_kind, "proof_reference": payload.proof_reference}))
    db.commit()
    return application


def execute_search(identity: str) -> str | EmployerSearchContinuation:
    from .tasks import execute_search as execute
    return execute(identity)


def refresh_source(identity: str) -> str:
    from .tasks import refresh_source as execute
    return execute(identity)


def execute_application(identity: str) -> str:
    from .tasks import execute_application as execute
    return execute(identity)


def fail_dispatched_work(topic: str, identity: str) -> None:
    from .tasks import fail_dispatched_work as fail
    fail(topic, identity)


def delete_artifact(identity: str) -> str:
    from .tasks import delete_artifact as execute
    return execute(identity)


def delete_account_data(db, user_id):
    from .privacy import delete_account_data as delete
    delete(db, user_id)


def export_account_data(db, user_id):
    from .privacy import export_account_data as export
    return export(db, user_id)
