"""Reviewed canary audience, owner consent and database-atomic business traffic.

No forecast users, HTTP reads, shadow runs or repeated intents form a denominator.
Existing append-only audit rows hold immutable reviews/consent/acceptance proofs;
existing business rows are the source of genuine baseline acceptances. No wallet,
credential, model request or customer credit is created by this module.
"""
from __future__ import annotations

import hashlib
import os
from datetime import UTC, datetime, timedelta
from typing import Annotated, Literal

from pydantic import Field, field_validator, model_validator
from sqlalchemy import select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from ...models import AdminAuditEvent, AnalysisRun, User
from ..common import payload_fingerprint
from ..employer.models import EmployerApplication, EmployerSearch, ServiceCreditReservation
from .finance import Closed, FundingUnavailable

VERSION = "reviewed-business-canary-v1"
MAX_CENSUS = 2000
MAX_TRAFFIC = 10_000
REVIEW_ACTION = "credit_canary_review"
CONSENT_ACTION = "credit_canary_consent"
ACCEPT_ACTION = "credit_canary_accept"
Digest = Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
Identity = Annotated[str, Field(pattern=r"^[a-z][a-z0-9-]{7,47}$")]
Owner = Annotated[int, Field(strict=True, gt=0, le=2**31 - 1)]
FAMILIES = {"matching", "interview", "tailoring", "enrichment", "bullets", "qa", "learning", "discovery", "skill_roi", "market", "submission"}
OPERATIONS = {"job_match": "matching", "enhanced_job_match": "matching",
    "interview_questions": "interview", "resume_tailor": "tailoring",
    "resume_tailor_extended": "tailoring", "resume_enrichment": "enrichment",
    "bullet_rewrite": "bullets", "enhanced_qa": "qa", "learning_strategy": "learning",
    "job_discovery_batch": "discovery", "skill_roi": "skill_roi", "market_analysis": "market", "market_report": "market",
    "verified_submission": "submission"}


def need(value: object) -> None:
    if not value:
        raise FundingUnavailable("The reviewed canary audience is unavailable; no new work was accepted")


def aware(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value


def stamp(now: datetime | None = None) -> datetime:
    return aware(now or datetime.now(UTC))


class Member(Closed):
    user_id: Owner
    identity_sha256: Digest


class CanaryReview(Closed):
    schema_version: Literal["reviewed-business-canary-v1"] = "reviewed-business-canary-v1"
    id: Identity
    runtime_sha256: Digest
    notice: str = Field(min_length=40, max_length=2000)
    notice_sha256: Digest
    reviewed_by: str = Field(min_length=1, max_length=120)
    review_evidence_sha256: Digest
    real_business_accounts_and_exclusions_reviewed: Literal[True]
    census_at: datetime
    valid_from: datetime
    expires_at: datetime
    traffic_started_at: datetime
    members: tuple[Member, ...] = Field(min_length=1, max_length=MAX_CENSUS)
    selected_owner_ids: tuple[Owner, ...] = Field(min_length=1, max_length=100)
    excluded_owner_ids: tuple[Owner, ...] = Field(max_length=MAX_CENSUS)
    families: tuple[str, ...] = Field(min_length=1, max_length=11)

    @field_validator("real_business_accounts_and_exclusions_reviewed", mode="before")
    @classmethod
    def explicit_operator_review(cls, value):
        if value is not True:
            raise ValueError("An explicit true operator review is required")
        return value

    @model_validator(mode="after")
    def finite_review(self):
        dates = (self.census_at, self.valid_from, self.expires_at, self.traffic_started_at)
        ids = tuple(row.user_id for row in self.members)
        if (any(value.tzinfo is None for value in dates)
                or not self.traffic_started_at <= self.census_at <= self.valid_from < self.expires_at
                or self.expires_at - self.traffic_started_at > timedelta(days=31)
                or ids != tuple(sorted(set(ids)))
                or self.selected_owner_ids != tuple(sorted(set(self.selected_owner_ids)))
                or self.excluded_owner_ids != tuple(sorted(set(self.excluded_owner_ids)))
                or not set(self.selected_owner_ids) <= set(ids)
                or set(ids) & set(self.excluded_owner_ids)
                or self.families != tuple(sorted(set(self.families)))
                or not set(self.families) <= FAMILIES
                or hashlib.sha256(self.notice.encode()).hexdigest() != self.notice_sha256):
            raise ValueError("A finite reviewed real census, exact notice and bounded selected scope are required")
        return self


def requested(db: Session | None = None) -> bool:
    """Retained rollout history cannot disappear when volatile pointers clear."""
    audience = os.getenv("UNIFIED_CREDIT_EXECUTION_AUDIENCE", "canary").strip().lower()
    declared = bool(os.getenv("UNIFIED_CREDIT_CANARY_REVIEW_ID") or os.getenv("UNIFIED_CREDIT_CANARY_REVIEW_SHA256")
        or (os.getenv("UNIFIED_CREDIT_EXECUTION_ENABLED", "").strip().lower() == "true" and audience != "reviewed_public"))
    retained = (db is not None and db.query(AdminAuditEvent.id).filter_by(
        action=REVIEW_ACTION, target_type="credit_canary").first() is not None)
    if audience == "reviewed_public":
        if not declared and not retained:
            return False  # Preserve the pre-canary public release path.
        try:
            from .runtime import current_runtime
            from .service_quotes import require_current_output_contract
            configured = current_runtime(None, db=db)
            for route in configured.routes:
                require_current_output_contract(route)
            return False  # Only a valid explicit public review ends the fence.
        except (FundingUnavailable, ValueError):
            return True
    return declared or retained


def _audit_id(kind: str, *values: object) -> str:
    return "cc_" + payload_fingerprint([kind, *values])[:60]


def _lock(db: Session) -> None:
    # Same owner -> financial locks as all existing paid admission. No other
    # owner's row lock is acquired while these global locks are held.
    from .admission import lock_money
    lock_money(db)


def _record(db: Session, *, identity: str, action: str, target: str, value: dict,
            actor_id: int | None, actor: str, now: datetime) -> AdminAuditEvent:
    old = db.get(AdminAuditEvent, identity)
    if old is not None:
        need(old.action == action and old.target_type == "credit_canary"
             and old.target_id == target and old.after_state == value)
        return old
    row = AdminAuditEvent(id=identity, actor_user_id=actor_id, actor_email=actor,
        action=action, target_type="credit_canary", target_id=target,
        reason="Explicit bounded canary review, owner choice or accepted business operation",
        before_state={}, after_state=value, created_at=now.astimezone(UTC).replace(tzinfo=None))
    db.add(row)
    db.flush([row])
    return row


def census(db: Session, *, census_at: datetime, excluded_owner_ids: tuple[int, ...]) -> tuple[Member, ...]:
    """Actual retained account identities; newly registered accounts cannot inflate this frozen frame."""
    rows = db.query(User.id, User.email, User.terms_accepted_at, User.terms_version,
        User.privacy_version, User.age_confirmed_at).filter(User.terms_accepted_at <= census_at,
        User.age_confirmed_at <= census_at).order_by(User.id).limit(MAX_CENSUS + len(excluded_owner_ids) + 1).all()
    need(len(rows) < MAX_CENSUS + len(excluded_owner_ids) + 1)
    result = []
    for identity, email, accepted, terms, privacy, age in rows:
        if identity in excluded_owner_ids:
            continue
        domain = str(email or "").rsplit("@", 1)[-1].lower()
        if (not terms or not privacy or not email or "@" not in email
                or domain in {"localhost", "example.invalid"} or domain.endswith((".invalid", ".test", ".example"))):
            continue
        result.append(Member(user_id=identity, identity_sha256=payload_fingerprint({
            "id": identity, "email": email.lower(), "terms": terms, "privacy": privacy,
            "accepted": aware(accepted).isoformat(), "age": aware(age).isoformat()})))
    need(len(result) <= MAX_CENSUS)
    return tuple(result)


def save_review(db: Session, review: CanaryReview, *, runtime, now: datetime | None = None) -> str:
    """Privileged explicit operator action; callers own commit. Never grants consent."""
    current = stamp(now)
    try:
        review = CanaryReview.model_validate(review.model_dump())
    except ValueError as exc:
        raise FundingUnavailable("A complete reviewed canary audience is required") from exc
    _lock(db)
    need(review.valid_from <= current < review.expires_at
         and review.runtime_sha256 == payload_fingerprint(runtime.model_dump(mode="json"))
         and review.expires_at <= runtime.budgets.expires_at
         and review.expires_at <= runtime.policy.expires_at
         and census(db, census_at=review.census_at, excluded_owner_ids=review.excluded_owner_ids) == review.members)
    digest = payload_fingerprint(review.model_dump(mode="json"))
    _record(db, identity=_audit_id("review", review.id), action=REVIEW_ACTION, target=review.id,
        value={"review": review.model_dump(mode="json"), "sha256": digest}, actor_id=None,
        actor=review.reviewed_by, now=current)
    return digest


def stored_review(db: Session, identity: str, digest: str) -> tuple[CanaryReview, str]:
    """Exact immutable selector only; the caller separately proves owned selection."""
    try:
        row = db.get(AdminAuditEvent, _audit_id("review", identity), populate_existing=True)
        need(row is not None and row.action == REVIEW_ACTION and row.target_type == "credit_canary"
             and row.target_id == identity and set(row.after_state) == {"review", "sha256"})
        review = CanaryReview.model_validate(row.after_state["review"])
        need(review.id == identity and row.after_state["sha256"] == digest
             and payload_fingerprint(review.model_dump(mode="json")) == digest)
        return review, digest
    except (ValueError, KeyError, TypeError, AttributeError) as exc:
        raise FundingUnavailable("Canary participation awaits an exact reviewed audience") from exc


def load_review(db: Session) -> tuple[CanaryReview, str]:
    return stored_review(db, os.getenv("UNIFIED_CREDIT_CANARY_REVIEW_ID", ""),
                         os.getenv("UNIFIED_CREDIT_CANARY_REVIEW_SHA256", ""))


def _live(db: Session, runtime, *, now: datetime, protect_census: bool = False) -> tuple[CanaryReview, str]:
    review, digest = load_review(db)
    if protect_census and db.get_bind().dialect.name == "postgresql":
        # Only these already-reviewed identities are protected through commit.
        # NOWAIT never waits for another owner while financial locks are held.
        try:
            locked = db.query(User.id).filter(User.id.in_(tuple(member.user_id for member in review.members))).order_by(
                User.id).with_for_update(read=True, nowait=True).all()
            need(len(locked) == len(review.members))
        except OperationalError as exc:
            raise FundingUnavailable("The live canary census is busy; no work was accepted") from exc
    need(review.valid_from <= now < review.expires_at
         and review.runtime_sha256 == payload_fingerprint(runtime.model_dump(mode="json"))
         and census(db, census_at=review.census_at, excluded_owner_ids=review.excluded_owner_ids) == review.members)
    from .service_quotes import require_current_output_contract
    for route in runtime.routes:
        require_current_output_contract(route)
    return review, digest


def _consents(db: Session, review: CanaryReview, digest: str, user_id: int) -> list[dict]:
    # Exact server-owned append-only records and the owner lock establish the
    # sequence. Inspect its current edge with a finite read, never cap the total
    # number of choices in a way that could prevent an eventual revocation.
    rows = db.query(AdminAuditEvent).filter_by(action=CONSENT_ACTION, target_type="credit_canary",
        target_id=f"{review.id}:{user_id}").order_by(
            AdminAuditEvent.after_state["sequence"].as_integer().desc(), AdminAuditEvent.id.desc()).limit(2).all()
    need(all(row.actor_user_id == user_id for row in rows))
    values = list(reversed([row.after_state for row in rows]))
    for value in values:
        need(set(value) == {"review_sha256", "user_id", "choice", "sequence", "previous_sha256", "request_sha256"}
             and value["review_sha256"] == digest and value["user_id"] == user_id
             and value["choice"] in {"opt_in", "revoke"} and type(value["sequence"]) is int
             and 0 < value["sequence"] <= 2**31 - 1)
    if values:
        first = values[0]
        need((first["sequence"] == 1 and first["previous_sha256"] is None)
             or (first["sequence"] > 1 and type(first["previous_sha256"]) is str
                 and len(first["previous_sha256"]) == 64))
    if len(values) == 2:
        need(values[1]["sequence"] == values[0]["sequence"] + 1
             and values[1]["previous_sha256"] == payload_fingerprint(values[0]))
    return values


def choose(db: Session, *, user_id: int, review_id: str, review_sha256: str,
           notice_sha256: str, choice: Literal["opt_in", "revoke"], idempotency_key: str,
           expected_sequence: int, runtime=None, now: datetime | None = None) -> dict:
    """Authenticated same-owner explicit choice; never a Terms or testing-permission inference."""
    need(8 <= len(idempotency_key) <= 160 and choice in {"opt_in", "revoke"}
         and type(expected_sequence) is int and 0 <= expected_sequence <= 2**31 - 2)
    user = db.query(User).filter_by(id=user_id).with_for_update().populate_existing().one_or_none()
    need(user is not None)
    _lock(db)
    review, digest = stored_review(db, review_id, review_sha256)
    need((review.id, digest, review.notice_sha256) == (review_id, review_sha256, notice_sha256)
         and user_id in review.selected_owner_ids)
    request_hash = payload_fingerprint([digest, user_id, choice, notice_sha256, idempotency_key, expected_sequence])
    identity = _audit_id("consent", review.id, user_id, idempotency_key)
    existing = db.get(AdminAuditEvent, identity)
    if existing is not None:
        need(existing.action == CONSENT_ACTION and existing.after_state.get("request_sha256") == request_hash)
        return existing.after_state
    values = _consents(db, review, digest, user_id)
    need(expected_sequence == (values[-1]["sequence"] if values else 0))
    if choice == "opt_in":
        need(runtime is not None)
        active, active_digest = _live(db, runtime, now=stamp(now))
        need((active.id, active_digest) == (review.id, digest))
    value = {"review_sha256": digest, "user_id": user_id, "choice": choice,
        "sequence": values[-1]["sequence"] + 1 if values else 1, "previous_sha256": payload_fingerprint(values[-1]) if values else None,
        "request_sha256": request_hash}
    _record(db, identity=identity, action=CONSENT_ACTION, target=f"{review.id}:{user_id}",
        value=value, actor_id=user_id, actor="server:authenticated-owner-consent", now=stamp(now))
    return value


def require_participation(db: Session | None, *, user_id: int, runtime, now: datetime | None = None,
                          protect_census: bool = False) -> dict:
    need(db is not None)
    assert db is not None
    review, digest = _live(db, runtime, now=stamp(now), protect_census=protect_census)
    need(user_id in review.selected_owner_ids)
    choices = _consents(db, review, digest, user_id)
    need(choices and choices[-1]["choice"] == "opt_in")
    return {"review_id": review.id, "review_sha256": digest, "runtime_sha256": review.runtime_sha256,
        "consent_sha256": payload_fingerprint(choices[-1]), "consent_sequence": choices[-1]["sequence"]}


def enrollment(db: Session, *, user_id: int, runtime=None, now: datetime | None = None,
               review_id: str | None = None, review_sha256: str | None = None) -> dict:
    # Selectors never authorize; exact stored review and owned selection do.
    need((review_id is None) == (review_sha256 is None))
    if review_id is not None:
        assert review_sha256 is not None
        review, digest = stored_review(db, review_id, review_sha256)
    else:
        try:
            review, digest = load_review(db)
            need(user_id in review.selected_owner_ids)
        except FundingUnavailable:
            # A paused/rotated pointer cannot hide an owner's latest retained
            # choice. Query only that authenticated owner's bounded audit scope.
            latest = db.query(AdminAuditEvent).filter_by(action=CONSENT_ACTION, target_type="credit_canary",
                actor_user_id=user_id).order_by(AdminAuditEvent.created_at.desc(), AdminAuditEvent.id.desc()).first()
            need(latest is not None and latest.after_state.get("user_id") == user_id)
            identity, separator, owner = latest.target_id.rpartition(":")
            need(separator == ":" and owner == str(user_id))
            review, digest = stored_review(db, identity, latest.after_state["review_sha256"])
    need(user_id in review.selected_owner_ids)
    choices = _consents(db, review, digest, user_id)
    can_opt_in = False
    if runtime is not None:
        try:
            active, active_digest = _live(db, runtime, now=stamp(now))
            can_opt_in = (active.id, active_digest) == (review.id, digest)
        except (FundingUnavailable, ValueError):
            pass
    return {"status": "OPTED_IN" if choices and choices[-1]["choice"] == "opt_in" else "NOT_OPTED_IN",
        "review_id": review.id, "review_sha256": digest, "notice": review.notice,
        "notice_sha256": review.notice_sha256, "expires_at": review.expires_at.isoformat(),
        "runtime_sha256": review.runtime_sha256, "can_opt_in": can_opt_in,
        "sequence": choices[-1]["sequence"] if choices else 0,
        "execution_flag_enabled": os.getenv("UNIFIED_CREDIT_EXECUTION_ENABLED", "").lower() == "true",
        "accepted_work_survives_revocation": True}


def _source_kind(family: str) -> str:
    return {"discovery": "search", "submission": "application"}.get(family, "analysis")


def _application_key(owner: int, opening: str) -> tuple:
    # A new resume, application, review or quote for the same opening does not
    # manufacture another genuine business acceptance in the denominator.
    need(type(opening) is str and len(opening) == 64 and all(c in "0123456789abcdef" for c in opening))
    return owner, "submission", payload_fingerprint({"owner": owner, "opening_key": opening})


def _application_business_keys(db: Session, review: CanaryReview, *, now: datetime,
                               exclude: tuple[str, str]) -> tuple[set[tuple], int]:
    if "submission" not in review.families:
        return set(), 0
    from ..browser_assistance.models import BrowserAssistanceReview
    from .models import CreditReservation, CreditServiceQuote

    ids = [row.user_id for row in review.members]
    query = db.query(EmployerApplication).filter(EmployerApplication.user_id.in_(ids),
        EmployerApplication.application_mode == "browser")
    legacy = query.join(ServiceCreditReservation, EmployerApplication.reservation_id == ServiceCreditReservation.id).filter(
        ServiceCreditReservation.user_id == EmployerApplication.user_id,
        ServiceCreditReservation.source_id == EmployerApplication.id,
        ServiceCreditReservation.operation == "job_application", ServiceCreditReservation.unit_price == 20,
        ServiceCreditReservation.requested_count == 1, ServiceCreditReservation.reserved_amount == 20,
        ServiceCreditReservation.created_at >= review.traffic_started_at, ServiceCreditReservation.created_at <= now,
        EmployerApplication.credit_cost == 20,
        EmployerApplication.pricing_snapshot["unit_price"].as_integer() == 20,
        select(BrowserAssistanceReview.id).where(
            BrowserAssistanceReview.application_id == EmployerApplication.id,
            BrowserAssistanceReview.user_id == EmployerApplication.user_id,
            BrowserAssistanceReview.review_protocol_version == 4,
            BrowserAssistanceReview.approved_at.is_not(None)).exists()).with_entities(
        EmployerApplication.id, EmployerApplication.user_id, EmployerApplication.opening_key).limit(MAX_TRAFFIC + 1).all()
    unified = query.join(CreditReservation, (CreditReservation.source_id == EmployerApplication.id)
        & (CreditReservation.user_id == EmployerApplication.user_id)).join(CreditServiceQuote,
        CreditServiceQuote.accepted_reservation_id == CreditReservation.id).filter(
        CreditReservation.operation == "verified_submission", CreditReservation.reserved_credits == 3,
        CreditServiceQuote.user_id == EmployerApplication.user_id,
        CreditServiceQuote.operation == "verified_submission", CreditServiceQuote.accepted_at.is_not(None),
        CreditServiceQuote.accepted_at >= review.traffic_started_at, CreditServiceQuote.accepted_at <= now,
        EmployerApplication.approved_at.is_not(None),
        EmployerApplication.reservation_id.is_(None), EmployerApplication.credit_cost == 3).with_entities(
        EmployerApplication.id, EmployerApplication.user_id, EmployerApplication.opening_key,
        EmployerApplication.pricing_snapshot, CreditReservation.input_fingerprint,
        CreditReservation.quote_snapshot, CreditServiceQuote).limit(MAX_TRAFFIC + 1).all()
    need(len(legacy) + len(unified) <= MAX_TRAFFIC)
    keys = set()
    for identity, owner, opening in legacy:
        if exclude != ("application", identity):
            keys.add(_application_key(owner, opening))
    for identity, owner, opening, billing, fingerprint, snapshot, quote in unified:
        if exclude == ("application", identity):
            continue
        # Retained paid acceptances count; drafts and self-reported manual
        # outcomes do not. Never replace the quote fingerprint with the older
        # application-creation fingerprint.
        from .submission import quote_billing
        need(quote.input_fingerprint == fingerprint
            and snapshot.get("service_quote_id") == quote.id and quote_billing(quote) == billing)
        keys.add(_application_key(owner, opening))
    return keys, len(legacy) + len(unified)


def _submission_source(db: Session, source, fingerprint: str, *, since: datetime, now: datetime) -> dict:
    from ..browser_assistance.models import BrowserAssistanceReview
    from . import submission
    from .models import CreditServiceQuote
    from .submission_contract import SubmissionBilling

    try:
        billing = SubmissionBilling.model_validate(source.pricing_snapshot).model_dump(mode="json")
    except ValueError:
        raise FundingUnavailable("The reviewed application quote is unavailable; no work was accepted") from None
    quote = db.get(CreditServiceQuote, billing["quote_id"])
    need(quote is not None and quote.user_id == source.user_id and quote.operation == "verified_submission"
        and quote.input_fingerprint == fingerprint == billing["input_fingerprint"]
        and since <= aware(quote.created_at) <= now < aware(quote.expires_at)
        and quote.accepted_reservation_id is None and submission.quote_billing(quote) == billing
        and quote.idempotency_key.startswith("browser-submit-quote:"))
    candidate = db.get(BrowserAssistanceReview, quote.idempotency_key.removeprefix("browser-submit-quote:"))
    need(candidate is not None and candidate.user_id == source.user_id and candidate.application_id == source.id
        and candidate.review_protocol_version == 5 and candidate.state == "draft"
        and aware(candidate.created_at) <= now < aware(candidate.expires_at)
        and candidate.package_digest == source.package_digest
        and candidate.review_digest == submission.contract.digest(candidate.payload)
        and candidate.payload.get("billing") == candidate.payload.get("pricing") == billing
        and source.credit_cost == 3 and source.application_mode == "browser" and source.reservation_id is None)
    return {"business_key": _application_key(source.user_id, source.opening_key)[2], "quote_id": quote.id}


def _business_keys(db: Session, review: CanaryReview, *, now: datetime, exclude: tuple[str, str]) -> set[tuple]:
    ids = [row.user_id for row in review.members]
    rows = db.execute(select(AnalysisRun.id, AnalysisRun.user_id, AnalysisRun.operation,
        AnalysisRun.input_fingerprint, AnalysisRun.input_payload["schema_version"].as_string(),
        AnalysisRun.input_payload["shadow"].as_boolean(), AnalysisRun.model_cost_quote["shadow"].as_boolean())
        .where(AnalysisRun.user_id.in_(ids), AnalysisRun.created_at >= review.traffic_started_at,
            AnalysisRun.created_at <= now).limit(MAX_TRAFFIC + 1)).all()
    searches = db.query(EmployerSearch.id, EmployerSearch.user_id, EmployerSearch.input_fingerprint,
        EmployerSearch.scope).filter(EmployerSearch.user_id.in_(ids),
        EmployerSearch.created_at >= review.traffic_started_at, EmployerSearch.created_at <= now).limit(MAX_TRAFFIC + 1).all()
    keys, applications = _application_business_keys(db, review, now=now, exclude=exclude)
    need(len(rows) + len(searches) + applications <= MAX_TRAFFIC)
    for identity, owner, operation, fingerprint, schema, shadow, quoted_shadow in rows:
        family = OPERATIONS.get(operation.removeprefix("sync_"))
        if (exclude == ("analysis", identity) or identity.startswith("dev_") or schema is not None
                or shadow is True or quoted_shadow is True or family not in review.families):
            continue
        need(type(fingerprint) is str and len(fingerprint) == 64)
        keys.add((owner, family, fingerprint))
    for identity, owner, fingerprint, scope in searches:
        if (exclude == ("search", identity) or "discovery" not in review.families
                or (scope or {}).get("shadow") or (scope or {}).get("internal_evaluation")):
            continue
        need(type(fingerprint) is str and len(fingerprint) == 64)
        keys.add((owner, "discovery", fingerprint))
    return keys


def accept(db: Session, *, user_id: int, runtime, operation: str, source_id: str,
           input_fingerprint: str, now: datetime | None = None) -> dict | None:
    """Caller owns transaction. A full canary admission is counted once atomically with its funds."""
    if os.getenv("UNIFIED_CREDIT_EXECUTION_AUDIENCE", "canary").strip().lower() != "canary":
        return None
    if not requested(db):
        return None  # Lower-level financial fixtures/public rollout do not declare a canary.
    _lock(db)
    current = stamp(now)
    proof = require_participation(db, user_id=user_id, runtime=runtime, now=current, protect_census=True)
    review, _ = load_review(db)
    family = OPERATIONS.get(operation)
    need(family in review.families)
    assert family is not None
    kind = _source_kind(family)
    source = db.get({"search": EmployerSearch, "application": EmployerApplication,
        "analysis": AnalysisRun}[kind], source_id)
    need(source is not None and source.user_id == user_id)
    extra = _submission_source(db, source, input_fingerprint,
        since=review.traffic_started_at, now=current) if kind == "application" else {}
    if kind != "application":
        need(source.input_fingerprint == input_fingerprint
             and review.traffic_started_at <= aware(source.created_at) <= current)
    if kind == "analysis":
        need(OPERATIONS.get(source.operation.removeprefix("sync_")) == family)
    identity = _audit_id("accept", kind, source_id)
    old = db.get(AdminAuditEvent, identity)
    if old is not None:
        need(old.action == ACCEPT_ACTION and old.after_state.get("user_id") == user_id
             and old.after_state.get("source_id") == source_id
             and old.after_state.get("input_fingerprint") == input_fingerprint)
        return old.after_state
    keys = _business_keys(db, review, now=current, exclude=(kind, source_id))
    previous = db.query(AdminAuditEvent.after_state).filter_by(action=ACCEPT_ACTION,
        target_type="credit_canary").filter(
            AdminAuditEvent.created_at >= review.traffic_started_at.astimezone(UTC).replace(tzinfo=None),
            AdminAuditEvent.created_at <= current.astimezone(UTC).replace(tzinfo=None)).limit(MAX_TRAFFIC + 1).all()
    need(len(previous) <= MAX_TRAFFIC)
    canary_count = 0
    members = {row.user_id for row in review.members}
    for (value,) in previous:
        if value.get("user_id") in members and value.get("family") in review.families:
            canary_count += 1
            key = value.get("business_key") if value["family"] == "submission" else value["input_fingerprint"]
            need(type(key) is str and len(key) == 64)
            keys.add((value["user_id"], value["family"], key))
    keys.add((user_id, family, extra.get("business_key", input_fingerprint)))
    need(20 * (canary_count + 1) <= len(keys))
    value = {**proof, "acceptance_id": identity, "user_id": user_id, "family": family, "source_kind": kind,
        "source_id": source_id, "input_fingerprint": input_fingerprint, **extra,
        "eligible_business_acceptances": len(keys), "canary_acceptances": canary_count + 1}
    _record(db, identity=identity, action=ACCEPT_ACTION, target=review.id, value=value,
        actor_id=None, actor="server:canary-business-admission", now=current)
    return value


def deny_new_legacy_generation(db: Session) -> None:
    need(not requested(db))


def _first_review_at(db: Session) -> datetime:
    reviews = db.query(AdminAuditEvent.after_state).filter_by(action=REVIEW_ACTION,
        target_type="credit_canary").limit(129).all()
    need(0 < len(reviews) <= 128)
    cutoffs = []
    for (value,) in reviews:
        saved = CanaryReview.model_validate(value["review"])
        need(payload_fingerprint(saved.model_dump(mode="json")) == value["sha256"])
        cutoffs.append(saved.valid_from)
    return min(cutoffs)


def require_accepted_hold(db: Session, hold) -> None:
    proof = (hold.snapshot or {}).get("canary")
    if proof is not None:
        need(type(proof) is dict and type(proof.get("acceptance_id")) is str)
        from .models import CreditReservation
        row = db.get(AdminAuditEvent, proof["acceptance_id"])
        reservation = db.get(CreditReservation, hold.reservation_id)
        need(row is not None and row.action == ACCEPT_ACTION and row.target_type == "credit_canary"
             and row.after_state == proof and proof["user_id"] == hold.user_id
             and reservation is not None and reservation.user_id == hold.user_id
             and proof["source_id"] == reservation.source_id
             and proof["input_fingerprint"] == reservation.input_fingerprint
             and proof["family"] == OPERATIONS.get(reservation.operation)
             and proof["source_kind"] == _source_kind(proof["family"])
             and proof["acceptance_id"] == _audit_id("accept", proof["source_kind"], proof["source_id"]))
        if proof["source_kind"] == "application":
            need(type(proof.get("business_key")) is str and len(proof["business_key"]) == 64
                 and proof.get("quote_id") == reservation.quote_snapshot.get("service_quote_id"))
    elif (hold.snapshot or {}).get("public_admission") is not None:
        from .models import CreditReservation
        public = hold.snapshot["public_admission"]
        reservation = db.get(CreditReservation, hold.reservation_id)
        need(type(public) is dict and set(public) == {"version", "runtime_sha256", "user_id", "source_id",
             "input_fingerprint", "route_sha256", "policy_sha256", "budgets_sha256"}
             and public["version"] == "reviewed-public-admission-v1"
             and type(public["runtime_sha256"]) is str and len(public["runtime_sha256"]) == 64
             and public["user_id"] == hold.user_id and reservation is not None
             and reservation.user_id == hold.user_id and public["source_id"] == reservation.source_id
             and public["input_fingerprint"] == reservation.input_fingerprint
             and all(public[name + "_sha256"] == payload_fingerprint(hold.snapshot[name])
                     for name in ("route", "policy", "budgets")))
    elif requested(db):
        need(aware(hold.created_at) < _first_review_at(db))


def require_accepted_provider_run(db: Session, run: AnalysisRun) -> None:
    """Existing frozen work survives pauses; new unreviewed legacy calls do not."""
    # Internal v2 evaluation never borrows a public canary exemption, including
    # when that canary is disabled. A stored label requires exact process scope.
    if run.id.startswith("qev2_") or (run.input_payload or {}).get("schema_version") == "supported-quality-evaluation-v2":
        from ..analysis.supported_evaluation import (
            require_provider_authority as require_v2_authority,
        )
        try:
            require_v2_authority(db, run)
        except (ValueError, KeyError, TypeError, AttributeError) as exc:
            raise FundingUnavailable("Internal v2 work requires its validated local runner authority") from exc
        return
    if not requested(db):
        return
    if (run.model_cost_quote or {}).get("billing_currency") == "credits":
        from .models import CreditExecutionHold, CreditReservation
        hold = db.get(CreditExecutionHold, run.model_cost_quote.get("unified_hold_id"))
        need(hold is not None and hold.user_id == run.user_id)
        reservation = db.get(CreditReservation, hold.reservation_id)
        need(reservation is not None and reservation.source_id == run.id
             and reservation.input_fingerprint == run.input_fingerprint)
        require_accepted_hold(db, hold)
        return
    # A label alone is never authority. Only the current validated local runner
    # scope can exempt an exact internally funded synthetic operation.
    if run.id.startswith("dev_") and (run.input_payload or {}).get("schema_version") == "synthetic-interview-development-v1":
        from ..analysis.development_runner import require_provider_authority
        try:
            require_provider_authority(db, run)
        except (ValueError, KeyError, TypeError, AttributeError) as exc:
            raise FundingUnavailable("Internal model work requires its validated local runner authority") from exc
        return
    need(aware(run.created_at) < _first_review_at(db))
