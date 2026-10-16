"""Durable owner-bound paid history, independent of saved-result JSON reads."""

from __future__ import annotations

from datetime import UTC
from types import SimpleNamespace
from typing import cast

from sqlalchemy import and_, or_, text
from sqlalchemy.orm import load_only

from . import history_stream, models
from .delivery_history_projector import (
    ARCHIVE_AUTHORITY,
    DELIVERY_AUTHORITY,
    IDENTITY_RECIPE_VERSION,
    LEGACY_CONTRACT,
    OPENING_CONTRACT,
    PREFERENCE_CONTRACT,
    SEARCH_AUTHORITY,
    HistoricalIdentityEvidence,
    HistoryProjection,
    PaidDeliveryEvidence,
    SavedSearchEvidence,
    project_paid_delivery,
)

MAX_DELIVERIES_PER_CHUNK = 8
MAX_SOURCE_BYTES_PER_CHUNK = 16_384
MAX_WORK_PER_CHUNK = 32_768


def _time(value):
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _progress(db, owner, *, lock=True):
    cls = models.EmployerPaidHistoryProgress
    if db.get_bind().dialect.name == "postgresql":
        from sqlalchemy.dialects.postgresql import insert
    else:
        from sqlalchemy.dialects.sqlite import insert
    db.execute(
        insert(cls)
        .values(user_id=owner)
        .on_conflict_do_nothing(index_elements=[cls.user_id])
    )
    query = db.query(cls).filter_by(user_id=owner).populate_existing()
    return (query.with_for_update() if lock else query).one()


def _boundary(query, created, identity, *, after):
    cls = models.EmployerJobDelivery
    if after:
        return query.filter(
            or_(
                cls.created_at > created,
                and_(cls.created_at == created, cls.id > identity),
            )
        )
    return query.filter(
        or_(
            cls.created_at < created,
            and_(cls.created_at == created, cls.id <= identity),
        )
    )


def _registry(db, history):
    cls = models.EmployerPaidOpeningRegistry
    row = db.get(cls, (history.user_id, history.opening_key))
    if row is None:
        row = cls(
            user_id=history.user_id,
            opening_key=history.opening_key,
            canonical_delivery_id=history.delivery_id,
            history_state="proven",
        )
        db.add(row)
    else:
        previous = db.get(models.EmployerPaidDeliveryHistory, row.canonical_delivery_id)
        if (_time(history.delivery_created_at), history.delivery_id) < (
            _time(previous.delivery_created_at),
            previous.delivery_id,
        ):
            row.canonical_delivery_id = history.delivery_id


def _evidence(delivery):
    return PaidDeliveryEvidence(
        DELIVERY_AUTHORITY,
        delivery.user_id,
        delivery.id,
        delivery.search_id,
        delivery.posting_id,
        delivery.charged_credits,
        delivery.opening_key,
    )


def _new_history(delivery, search_owner, **values):
    return models.EmployerPaidDeliveryHistory(
        delivery_id=delivery.id,
        user_id=delivery.user_id,
        search_id=delivery.search_id,
        search_owner_id=search_owner,
        posting_id=delivery.posting_id,
        original_charged_credits=delivery.charged_credits,
        delivery_created_at=delivery.created_at,
        **values,
    )


def _scalar_search(db, search_id):
    cls = models.EmployerSearch
    return (
        db.query(cls.id, cls.user_id, cls.history_revision)
        .filter(cls.id == search_id)
        .first()
    )


def _stream(db, owner, search, *, byte_budget, work_budget):
    cls = models.EmployerSearchHistoryProgress
    row = db.query(cls).filter_by(search_id=search.id).with_for_update().first()
    if row is None:
        dialect = db.get_bind().dialect.name
        size = (
            "octet_length(CAST(items AS TEXT))"
            if dialect == "postgresql"
            else "length(CAST(items AS BLOB))"
        )
        source_size = db.execute(
            text(
                f"SELECT {size} FROM employer_searches WHERE id=:search AND user_id=:owner"
            ),
            {"search": search.id, "owner": owner},
        ).scalar_one()
        if dialect == "postgresql":
            from sqlalchemy.dialects.postgresql import insert
        else:
            from sqlalchemy.dialects.sqlite import insert
        db.execute(
            insert(cls)
            .values(
                search_id=search.id,
                user_id=owner,
                generation=1,
                stream_generation=1,
                source_revision=search.history_revision,
                source_size_bytes=source_size,
                byte_offset=0,
                next_ordinal=0,
                parser_checkpoint={},
                state="pending",
            )
            .on_conflict_do_nothing(index_elements=[cls.search_id])
        )
        row = (
            db.query(cls)
            .filter_by(search_id=search.id)
            .populate_existing()
            .with_for_update()
            .one()
        )
    if row.source_revision != search.history_revision:
        row.generation += 1
        row.stream_generation += 1
        row.source_revision = search.history_revision
        row.byte_offset = 0
        row.next_ordinal = 0
        row.parser_checkpoint = {}
        row.source_sha256 = None
        row.unresolved_count = 0
        row.state = "pending"
        size = (
            "octet_length(CAST(items AS TEXT))"
            if db.get_bind().dialect.name == "postgresql"
            else "length(CAST(items AS BLOB))"
        )
        row.source_size_bytes = db.execute(
            text(
                f"SELECT {size} FROM employer_searches WHERE id=:search AND user_id=:owner"
            ),
            {"search": search.id, "owner": owner},
        ).scalar_one()
    if row.state not in {"complete", "unresolved"}:
        usage = history_stream.advance_snapshot(
            db, row, byte_budget=byte_budget, work_budget=work_budget
        )
    else:
        usage = {"bytes": 0, "returned_bytes": 0, "work": 0, "complete": True}
    current = _scalar_search(db, search.id)
    if current.history_revision != row.source_revision:
        row.state = "pending"
        return row, {**usage, "complete": False, "source_changed": True}
    return row, usage


def _project_legacy(db, delivery, search, stream):
    existing = db.get(models.EmployerPaidDeliveryHistory, delivery.id)
    if existing and existing.state == "proven" and existing.review_required:
        return "unresolved"
    if (
        existing
        and existing.state == "proven"
        and existing.source_revision != search.history_revision
    ):
        # Do not replace previously captured paid authority with mutated JSON.
        existing.review_required = True
        existing.review_reason = "saved_evidence_changed_after_projection"
        return "unresolved"
    reason = None
    seen = None
    if search.user_id != delivery.user_id:
        reason = "search_owner_or_authority_invalid"
    elif stream is None or stream.state == "unresolved":
        reason = (
            (stream.parser_checkpoint or {}).get("invalid", "snapshot_json_invalid")
            if stream
            else "saved_search_missing"
        )
    else:
        seen = db.get(
            models.EmployerSearchHistorySeen,
            (search.id, stream.stream_generation, delivery.posting_id),
        )
        if seen is None:
            reason = "saved_item_missing"
        elif seen.conflicting:
            reason = "duplicate_items_conflict"
    result = None
    if reason is None:
        # The reason branches above establish both authoritative stream and item.
        seen = cast(models.EmployerSearchHistorySeen, seen)
        stream = cast(models.EmployerSearchHistoryProgress, stream)
        posting = seen.facts.get("posting", {})
        contract = OPENING_CONTRACT if "opening_key" in posting else LEGACY_CONTRACT
        # The durable stream checked the complete literal snapshot and semantic
        # concordance of every occurrence. Its bounded facts are the validated
        # reader's evidence input, not request JSON or a current posting join.
        saved = SavedSearchEvidence(
            SEARCH_AUTHORITY,
            search.user_id,
            search.id,
            contract,
            f"history:{search.id}:{stream.stream_generation}:{stream.source_sha256}",
            [seen.facts],
        )
        result = project_paid_delivery(
            owner_id=delivery.user_id, delivery=_evidence(delivery), search=saved
        )
        if not result.canonical_entry_authorized:
            reason = result.reason_codes[0]
    if existing is None:
        existing = _new_history(
            delivery, search.user_id, provenance="legacy_backfill", state="pending"
        )
        db.add(existing)
    existing.source_revision = search.history_revision
    if reason:
        existing.state = "unresolved"
        existing.static_reason = reason
        return "unresolved"
    # A missing item or rejected projection returned through the reason branch.
    result = cast(HistoryProjection, result)
    seen = cast(models.EmployerSearchHistorySeen, seen)
    existing.state = "proven"
    existing.static_reason = None
    existing.opening_key = result.opening_key
    existing.snapshot_charged_credits = delivery.charged_credits
    existing.saved_ordinal = seen.first_ordinal
    existing.content_digest = seen.content_digest
    db.flush([existing])
    _registry(db, existing)
    return "proven"


def _unprojected(db, owner):
    delivery, history = models.EmployerJobDelivery, models.EmployerPaidDeliveryHistory
    return (
        db.query(delivery.id)
        .outerjoin(history, history.delivery_id == delivery.id)
        .filter(
            delivery.user_id == owner,
            delivery.charged_credits > 0,
            history.delivery_id.is_(None),
        )
        .limit(1)
        .first()
        is not None
    )


def _readiness(db, owner, progress):
    # An older writer may finish across a rollout without a dual write. Recheck
    # authoritative scalar rows under the financial owner lock, never JSON.
    if (
        db.get_bind().dialect.name != "postgresql"
        and progress.state == "ready"
        and _unprojected(db, owner)
    ):
        progress.state = "pending"
    return progress.state


def _position(progress):
    return (
        progress.generation,
        progress.checkpoint_created_at,
        progress.checkpoint_delivery_id,
        progress.highwater_created_at,
        progress.highwater_delivery_id,
    )


def advance_owner(
    db,
    owner,
    *,
    delivery_budget=MAX_DELIVERIES_PER_CHUNK,
    byte_budget=MAX_SOURCE_BYTES_PER_CHUNK,
    work_budget=MAX_WORK_PER_CHUNK,
    statement_timeout_ms=None,
):
    """Commit a brief claim, parse without money locks, then stage proof writes.

    This accepts only a clean request/backfill session. The first two phases
    commit their own durable checkpoints. The caller commits or rolls back the
    final bounded proof/readiness phase. Source key-share precedes owner/progress
    locks there; the source-edit trigger uses NOWAIT for inverse prerequisites.
    """
    from ..entitlements import lock_entitlement_owner

    def phase_budget():
        if statement_timeout_ms is not None:
            if type(statement_timeout_ms) is not int or not 1 <= statement_timeout_ms <= 5000:
                raise ValueError("Invalid history statement deadline")
            if db.get_bind().dialect.name == "postgresql":
                db.execute(text("SELECT set_config('statement_timeout', :budget, true)"), {"budget": str(statement_timeout_ms)})
                db.execute(text("SET LOCAL lock_timeout = '100ms'"))

    if not 1 <= delivery_budget <= 64:
        raise ValueError("Invalid delivery chunk budget")
    if byte_budget < 32 or work_budget < history_stream.FIXED_ITEM_WORK + 16:
        raise ValueError("Invalid aggregate history chunk budget")
    if db.new or db.dirty or db.deleted:
        raise ValueError("History advancement requires a clean transaction")
    phase_budget()
    lock_entitlement_owner(db, owner)
    progress = _progress(db, owner)
    if _readiness(db, owner, progress) == "ready":
        return {"state": "ready", "bytes": 0, "work": 0, "deliveries": 0}
    if progress.state == "pending":
        progress.checkpoint_created_at = None
        progress.checkpoint_delivery_id = None
        progress.examined_count = 0
        progress.proven_count = 0
        progress.unresolved_count = 0
        progress.highwater_created_at = None
        progress.highwater_delivery_id = None
    if progress.highwater_delivery_id is None:
        top = (
            db.query(
                models.EmployerJobDelivery.created_at, models.EmployerJobDelivery.id
            )
            .filter(
                models.EmployerJobDelivery.user_id == owner,
                models.EmployerJobDelivery.charged_credits > 0,
            )
            .order_by(
                models.EmployerJobDelivery.created_at.desc(),
                models.EmployerJobDelivery.id.desc(),
            )
            .limit(1)
            .first()
        )
        if top is None:
            progress.state = "ready"
            return {"state": "ready", "bytes": 0, "work": 0, "deliveries": 0}
        progress.highwater_created_at, progress.highwater_delivery_id = top
    progress.state = "backfilling"
    query = db.query(models.EmployerJobDelivery).filter(
        models.EmployerJobDelivery.user_id == owner,
        models.EmployerJobDelivery.charged_credits > 0,
    )
    query = _boundary(
        query,
        progress.highwater_created_at,
        progress.highwater_delivery_id,
        after=False,
    )
    if progress.checkpoint_delivery_id is not None:
        query = _boundary(
            query,
            progress.checkpoint_created_at,
            progress.checkpoint_delivery_id,
            after=True,
        )
    originals = (
        query.order_by(
            models.EmployerJobDelivery.created_at, models.EmployerJobDelivery.id
        )
        .limit(delivery_budget + 1)
        .all()
    )
    fields = (
        "id",
        "user_id",
        "search_id",
        "posting_id",
        "charged_credits",
        "opening_key",
        "created_at",
    )
    rows = [
        SimpleNamespace(**{field: getattr(row, field) for field in fields})
        for row in originals
    ]
    claimed = _position(progress)
    db.commit()  # No saved JSON is decoded while the owner/progress are locked.
    phase_budget()

    used_bytes, used_work = 0, 0
    plans = []
    for delivery in rows[:delivery_budget]:
        search = _scalar_search(db, delivery.search_id)
        existing = db.get(models.EmployerPaidDeliveryHistory, delivery.id)
        if search is None:
            plans.append((delivery, None, None))
            continue
        if search.user_id != owner or (
            existing
            and existing.state == "proven"
            and (
                existing.review_required
                or existing.source_revision == search.history_revision
            )
        ):
            plans.append((delivery, search.history_revision, None))
            continue
        remaining_bytes, remaining_work = (
            byte_budget - used_bytes,
            work_budget - used_work,
        )
        if remaining_bytes < 32 or remaining_work < history_stream.FIXED_ITEM_WORK + 16:
            break
        stream, usage = _stream(
            db, owner, search, byte_budget=remaining_bytes, work_budget=remaining_work
        )
        used_bytes += usage.get("returned_bytes", usage["bytes"])
        used_work += usage["work"]
        if not usage["complete"]:
            break
        plans.append((delivery, search.history_revision, stream.stream_generation))
    db.commit()  # Persist parser progress and release all parser/FK locks.
    phase_budget()

    cls = models.EmployerSearch
    search_ids = sorted({delivery.search_id for delivery, _, _ in plans})
    if search_ids:
        # Acquire FK prerequisites before the financial owner lock. This select
        # deliberately excludes items and does not grant authority by itself.
        db.query(cls.id).filter(cls.id.in_(search_ids)).order_by(
            cls.id
        ).with_for_update(read=True, key_share=True).all()
    lock_entitlement_owner(db, owner)
    progress = _progress(db, owner)
    completed = 0
    if _position(progress) == claimed and progress.state == "backfilling":
        for delivery, revision, stream_generation in plans:
            search = _scalar_search(db, delivery.search_id)
            if search is None:
                state = "unresolved"
            elif revision != search.history_revision:
                progress.state = "pending"
                break
            else:
                existing = db.get(models.EmployerPaidDeliveryHistory, delivery.id)
                if (
                    existing
                    and existing.state == "proven"
                    and not existing.review_required
                    and existing.source_revision == revision
                ):
                    state = "proven"
                else:
                    stream = (
                        db.get(models.EmployerSearchHistoryProgress, delivery.search_id)
                        if stream_generation is not None
                        else None
                    )
                    if stream is not None and (
                        stream.stream_generation != stream_generation
                        or stream.source_revision != revision
                    ):
                        progress.state = "pending"
                        break
                    state = _project_legacy(db, delivery, search, stream)
            progress.examined_count += 1
            progress.proven_count += int(state == "proven")
            progress.unresolved_count += int(state == "unresolved")
            progress.checkpoint_created_at = delivery.created_at
            progress.checkpoint_delivery_id = delivery.id
            completed += 1
        if (
            completed == len(rows)
            and len(rows) <= delivery_budget
            and progress.state == "backfilling"
        ):
            progress.state = "unresolved" if progress.unresolved_count else "ready"
    return {
        "state": progress.state,
        "bytes": used_bytes,
        "work": used_work,
        "deliveries": completed,
        "generation": progress.generation,
        "unresolved_count": progress.unresolved_count,
    }


def membership(db, owner, opening_keys, posting_ids):
    """Finite indexed membership for the already bounded candidate cohort."""
    if len(opening_keys) > 1000 or len(posting_ids) > 1000:
        raise ValueError("Membership must use the current bounded candidate cohort")
    keys = {
        row[0]
        for row in db.query(models.EmployerPaidOpeningRegistry.opening_key)
        .filter(
            models.EmployerPaidOpeningRegistry.user_id == owner,
            models.EmployerPaidOpeningRegistry.opening_key.in_(opening_keys),
        )
        .all()
    }
    # A paid physical posting remains a free replay even while a canonical
    # identity is unresolved. This never mints canonical paid authority.
    aliases = {
        row[0]
        for row in db.query(models.EmployerJobDelivery.posting_id)
        .filter(
            models.EmployerJobDelivery.user_id == owner,
            models.EmployerJobDelivery.charged_credits > 0,
            models.EmployerJobDelivery.posting_id.in_(posting_ids),
        )
        .all()
    }
    progress = _progress(db, owner)
    return keys, aliases, _readiness(db, owner, progress)


def write_completed(db, search, deliveries, selected, *, restore_ready=False):
    """Atomic dual writes; caller retains settlement, owner lock and commit."""
    progress = _progress(db, search.user_id)
    by_posting = {posting.id: (posting, source) for posting, source, _ in selected}
    by_item = {
        item["posting"]["id"]: (ordinal, item)
        for ordinal, item in enumerate(search.items)
    }
    for delivery in deliveries:
        ordinal, item = by_item[delivery.posting_id]
        posting, source = by_posting[delivery.posting_id]
        archive = HistoricalIdentityEvidence(
            ARCHIVE_AUTHORITY,
            True,
            True,
            f"completed:{search.id}:{delivery.id}",
            IDENTITY_RECIPE_VERSION,
            search.user_id,
            delivery.id,
            search.id,
            posting.id,
            source.id,
            source.platform,
            source.region,
            source.board_token,
            source.employer_key,
            posting.requisition_id,
            posting.external_id,
            delivery.opening_key,
        )
        facts = {
            "posting": {
                key: item["posting"][key]
                for key in ("id", "source_id", "external_id", "platform", "opening_key")
            },
            "charged_credits": item["charged_credits"],
        }
        saved = SavedSearchEvidence(
            SEARCH_AUTHORITY,
            search.user_id,
            search.id,
            PREFERENCE_CONTRACT,
            f"completed:{search.id}",
            [facts],
        )
        result = project_paid_delivery(
            owner_id=search.user_id,
            delivery=_evidence(delivery),
            search=saved,
            identity_archive=archive,
        )
        if not result.canonical_entry_authorized:
            raise ValueError("New paid history proof failed: " + result.reason_codes[0])
        proof = {
            "recipe_version": IDENTITY_RECIPE_VERSION,
            "source_id": source.id,
            "platform": source.platform,
            "region": source.region,
            "tenant": source.board_token,
            "verified_employer_key": source.employer_key,
            "requisition_id": posting.requisition_id,
            "external_id": posting.external_id,
            "preimage": result.preimage.__dict__,
        }
        history = _new_history(
            delivery,
            search.user_id,
            provenance="new_search",
            state="proven",
            opening_key=result.opening_key,
            saved_ordinal=ordinal,
            snapshot_charged_credits=delivery.charged_credits,
            content_digest=result.item_digest,
            source_revision=search.history_revision,
            identity_proof=proof,
        )
        db.add(history)
        db.flush([history])
        _registry(db, history)
    progress.generation += bool(deliveries)
    if restore_ready:
        progress.state = "ready"


def write_completed_native(db, *, search_id, snapshot_id, reservation_id, deliveries, restore_ready=False):
    """Bounded server-only proof; caller retains owner/fences/settlement/commit.

    Native immutable bodies were verified before this financial transaction.
    This entry point reads only <=100 fixed-contract scalar proof records.
    """
    search = db.query(models.EmployerSearch).options(load_only(*(getattr(models.EmployerSearch, name) for name in (
        "id", "user_id", "native_snapshot_id", "input_fingerprint", "desired_count", "delivered_count",
        "charged_credits", "reserved_credits", "refunded_credits", "scope", "history_revision")))).filter_by(id=search_id).one()
    snapshot = db.query(models.EmployerCatalogueSnapshot).filter_by(
        id=snapshot_id, user_id=search.user_id, state="proven").one()
    plan = db.query(models.EmployerSearchPreparation).options(load_only(
        models.EmployerSearchPreparation.id, models.EmployerSearchPreparation.user_id,
        models.EmployerSearchPreparation.reservation_id, models.EmployerSearchPreparation.input_fingerprint,
        models.EmployerSearchPreparation.accepted_quote, models.EmployerSearchPreparation.history_generation)).filter_by(
        id=snapshot.preparation_id, user_id=search.user_id).one()
    reservation = db.query(models.ServiceCreditReservation).options(load_only(*(getattr(models.ServiceCreditReservation, name) for name in (
        "id", "user_id", "source_id", "operation", "unit_price", "pricing_version", "reserved_amount", "state")))).filter_by(
        id=reservation_id, user_id=search.user_id, source_id=search.id, operation="job_search").one()
    quote = plan.accepted_quote
    if (search.native_snapshot_id != snapshot.id or plan.id != search.id
            or plan.reservation_id != reservation.id or search.input_fingerprint != plan.input_fingerprint
            or quote["search_input_fingerprint"] != plan.input_fingerprint
            or quote["unit_price"] != reservation.unit_price
            or quote["pricing_version"] != reservation.pricing_version
            or quote["requested_count"] != search.desired_count
            or quote["maximum_credits"] != search.desired_count * reservation.unit_price
            or search.scope["price_quote"] != quote
            or snapshot.item_count != search.delivered_count or not 0 <= snapshot.item_count <= 100
            or reservation.state != "reserved" or snapshot.history_generation != plan.history_generation):
        raise ValueError("Native completed-search authority mismatch")
    rows = db.query(models.EmployerCatalogueSnapshotItem).filter_by(
        snapshot_id=snapshot.id, user_id=search.user_id).order_by(
        models.EmployerCatalogueSnapshotItem.ordinal).limit(101).all()
    if len(rows) != snapshot.item_count or any(
        item.ordinal != ordinal or item.proof_state != "proven"
        or len(item.item_digest or "") != 64 or item.unit_price != (0 if item.seen else reservation.unit_price)
        for ordinal, item in enumerate(rows)
    ):
        raise ValueError("Native completed-search proof is incomplete")
    by_posting = {item.posting_id: item for item in rows if item.unit_price > 0}
    if (len(by_posting) != len(deliveries) or {d.posting_id for d in deliveries} != set(by_posting)
            or search.charged_credits != len(deliveries) * reservation.unit_price
            or search.reserved_credits != reservation.reserved_amount
            or search.refunded_credits != reservation.reserved_amount - search.charged_credits):
        raise ValueError("Native completed-search delivery/settlement mismatch")
    progress = _progress(db, search.user_id)
    for delivery in deliveries:
        item = by_posting[delivery.posting_id]
        posting = db.query(models.EmployerCataloguePostingRevision).options(load_only(
            models.EmployerCataloguePostingRevision.posting_id, models.EmployerCataloguePostingRevision.valid_from,
            models.EmployerCataloguePostingRevision.source_id, models.EmployerCataloguePostingRevision.requisition_id,
            models.EmployerCataloguePostingRevision.external_id)).filter_by(posting_id=item.posting_id, valid_from=item.posting_revision).one_or_none()
        source = db.query(models.EmployerCatalogueSourceRevision).options(load_only(
            models.EmployerCatalogueSourceRevision.source_id, models.EmployerCatalogueSourceRevision.valid_from,
            models.EmployerCatalogueSourceRevision.platform, models.EmployerCatalogueSourceRevision.region,
            models.EmployerCatalogueSourceRevision.board_token, models.EmployerCatalogueSourceRevision.employer_key)).filter_by(source_id=item.source_id, valid_from=item.source_revision).one_or_none()
        if (posting is None or source is None or posting.source_id != source.source_id
                or delivery.user_id != search.user_id or delivery.search_id != search.id
                or delivery.charged_credits != item.unit_price or delivery.opening_key != item.opening_key
                or _time(delivery.created_at) < _time(snapshot.created_at)):
            raise ValueError("Native paid-delivery lineage mismatch")
        recipe = {"recipe_version": IDENTITY_RECIPE_VERSION, "source_id": source.source_id,
            "platform": source.platform, "region": source.region, "tenant": source.board_token,
            "verified_employer_key": source.employer_key, "requisition_id": posting.requisition_id,
            "external_id": posting.external_id}
        if item.identity_proof != recipe:
            raise ValueError("Native identity recipe mismatch")
        archive = HistoricalIdentityEvidence(ARCHIVE_AUTHORITY, True, True,
            f"completed:{search.id}:{delivery.id}", IDENTITY_RECIPE_VERSION, search.user_id,
            delivery.id, search.id, posting.posting_id, source.source_id, source.platform,
            source.region, source.board_token, source.employer_key, posting.requisition_id,
            posting.external_id, item.opening_key)
        facts = {"posting": {"id": posting.posting_id, "source_id": source.source_id,
            "external_id": posting.external_id, "platform": source.platform,
            "opening_key": item.opening_key}, "charged_credits": item.unit_price}
        saved = SavedSearchEvidence(SEARCH_AUTHORITY, search.user_id, search.id,
            PREFERENCE_CONTRACT, f"completed:{search.id}", [facts])
        result = project_paid_delivery(owner_id=search.user_id, delivery=_evidence(delivery),
            search=saved, identity_archive=archive)
        if not result.canonical_entry_authorized or result.opening_key != item.opening_key:
            raise ValueError("Native paid history proof failed")
        history = _new_history(delivery, search.user_id, provenance="new_search", state="proven",
            opening_key=result.opening_key, saved_ordinal=item.ordinal,
            snapshot_charged_credits=delivery.charged_credits, content_digest=item.item_digest,
            source_revision=search.history_revision,
            identity_proof=dict(recipe, preimage=result.preimage.__dict__,
                native_snapshot_id=snapshot.id, native_item_digest=item.item_digest,
                native_digest_recipe=("postgresql-jsonb-sha256-v1" if db.get_bind().dialect.name == "postgresql"
                    else "sqlite-fixture-canonical-json-sha256-v1")))
        db.add(history)
        db.flush([history])
        _registry(db, history)
    progress.generation += bool(deliveries)
    if restore_ready:
        progress.state = "ready"
