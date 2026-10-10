"""A pinned catalogue scan with durable canonical winners and zero-charge waits.

Raw bodies remain native immutable content. Work reads fixed character slices;
PostgreSQL TOAST/hash/full-text execution itself is not a byte-budget guarantee.
"""
from __future__ import annotations

import copy
import json
import math
import re
import time
import unicodedata
from datetime import timedelta
from decimal import MAX_EMAX, MIN_ETINY, Decimal
from types import SimpleNamespace

from fastapi import HTTPException
from sqlalchemy import Text, and_, cast, event, func, literal_column, or_, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import load_only

from ...services.basic_matching import SCORING_VERSION
from ...services.market.skill_extractor import (
    SKILL_EXTRACTOR_VERSION,
    extract_skills_from_text,
)
from ...services.market.skill_taxonomy import canonical_skill
from ..common import payload_fingerprint, public_id, utcnow
from . import admissions, config, models, role_aliases, search_preferences
from .delivery_history_projector import IDENTITY_RECIPE_VERSION

PLAN_VERSION = "catalogue-asof-v1"
SANITIZER_VERSION = "bounded-exact-sanitation-v1"
METADATA_STREAM_VERSION = "bounded-native-preferences-v1"
RESUME_STREAM_VERSION = "bounded-native-resume-v1"
# Semantic changes to any evaluator/parser require its explicit version bump.
_CONTRACT_DIGEST = payload_fingerprint({"plan": PLAN_VERSION, "preferences": search_preferences.POLICY_VERSION,
    "identity": IDENTITY_RECIPE_VERSION, "roles": role_aliases.VERSION, "sanitizer": SANITIZER_VERSION,
    "metadata_stream": METADATA_STREAM_VERSION, "resume_stream": RESUME_STREAM_VERSION})[:16]
PROJECTION_VERSION = f"{SCORING_VERSION}|{SKILL_EXTRACTOR_VERSION}|taxonomy-e210d0c2b975|contract-{_CONTRACT_DIGEST}"
CHUNK_ROWS = 128
SLICE_CHARS = 8192
WORK_SECONDS = 8.0
ACTIVE_STATES = ("pending", "scanning", "classifying", "selecting")


def resume_declaration(skills):
    # Preserve casefold equivalences even when the original spelling is unknown.
    from ...services.market.skill_taxonomy import all_search_terms
    recognized = {canonical.casefold() for canonical in all_search_terms()}
    return sorted({canonical_skill(str(value)).casefold() for value in skills or []} & recognized)


def resume_checkpoint():
    return {"depth": 0, "top": None, "key": True, "string": False, "escape": False,
        "unicode": None, "high": None, "collect": False, "raw": "", "raw_gap": "", "raw_gap_over": False,
        "raw_over": False, "alias": "", "alias_gap": False, "alias_over": False,
        "skills": [], "scalar": "", "number": _numeric_checkpoint(quoted=False), "invalid": False}


def advance_resume(cp, piece, *, final=False):
    """Native-valid JSON; finite normalized taxonomy candidates, no raw cap.

    Only string leaves can match taxonomy names. Nested non-string values have
    Python repr delimiters and cannot match any current canonical name/alias.
    Dicts iterate keys and top-level strings iterate characters, as Basic does.
    """
    from ...services.market.skill_taxonomy import ALIASES, all_search_terms
    terms = all_search_terms()
    known = {name.casefold() for name in terms}
    raw_limit = max(map(len, known))
    alias_limit = max(len(alias) for aliases in terms.values() for alias in aliases)

    def add(value):
        skill = canonical_skill(value).casefold()
        if skill in known and skill not in cp["skills"]:
            cp["skills"].append(skill)

    def character(value):
        if cp["top"] == "string":
            add(value)
            return
        if not cp["collect"]:
            return
        # Python strip ignores unbounded leading/trailing whitespace. Keep only
        # its possible interior width, whose overflow proves a nonmatch.
        if value.isspace():
            if cp["raw"]:
                if len(cp["raw_gap"]) < raw_limit:
                    cp["raw_gap"] += value
                else:
                    cp["raw_gap_over"] = True
        elif not cp["raw_over"]:
            folded = value.casefold()
            if cp["raw_gap_over"] or len(cp["raw"]) + len(cp["raw_gap"]) + len(folded) > raw_limit:
                cp["raw_over"] = True
            else:
                cp["raw"] += cp["raw_gap"] + folded
                cp["raw_gap"] = ""
        if value.isspace() or value in "-_":
            cp["alias_gap"] = bool(cp["alias"])
        elif not cp["alias_over"]:
            lowered = value.lower()
            extra = (" " if cp["alias_gap"] else "") + lowered
            if len(cp["alias"]) + len(extra) > alias_limit:
                cp["alias_over"] = True
            else:
                cp["alias"] += extra
                cp["alias_gap"] = False

    def decoded(value):
        point = ord(value)
        if cp["high"] is not None:
            high = cp["high"]
            cp["high"] = None
            if 0xDC00 <= point <= 0xDFFF:
                character(chr(0x10000 + ((high - 0xD800) << 10) + point - 0xDC00))
                return
            character(chr(high))
        if 0xD800 <= point <= 0xDBFF:
            cp["high"] = point
        else:
            character(value)

    for value in piece:
        if cp["string"]:
            if cp["unicode"] is not None:
                cp["unicode"] += value
                if len(cp["unicode"]) == 4:
                    decoded(chr(int(cp["unicode"], 16)))
                    cp["unicode"] = None
                continue
            if cp["escape"]:
                cp["escape"] = False
                if value == "u":
                    cp["unicode"] = ""
                else:
                    decoded({"n": "\n", "r": "\r", "t": "\t", "b": "\b", "f": "\f"}.get(value, value))
                continue
            if value == "\\":
                cp["escape"] = True
                continue
            if value == '"':
                if cp["high"] is not None:
                    character(chr(cp["high"]))
                    cp["high"] = None
                if cp["collect"] and cp["top"] != "string":
                    if not cp["raw_over"] and cp["raw"] in known:
                        if cp["raw"] not in cp["skills"]:
                            cp["skills"].append(cp["raw"])
                    if not cp["alias_over"] and cp["alias"] in ALIASES:
                        add(ALIASES[cp["alias"]])
                cp["string"] = False
            else:
                decoded(value)
            continue
        if value.isspace():
            continue
        if value == '"':
            if cp["top"] is None:
                cp["top"] = "string"
            cp.update(string=True, escape=False, unicode=None, high=None,
                collect=cp["top"] == "string" or cp["depth"] == 1 and (cp["top"] == "array" or cp["top"] == "object" and cp["key"]),
                raw="", raw_gap="", raw_gap_over=False, raw_over=False, alias="", alias_gap=False, alias_over=False)
        elif value in "[{":
            if cp["top"] is None:
                cp["top"] = "array" if value == "[" else "object"
            cp["depth"] += 1
        elif value in "]}":
            cp["depth"] -= 1
        elif cp["depth"] == 1 and value == ":":
            cp["key"] = False
        elif cp["depth"] == 1 and value == ",":
            cp["key"] = True
        elif cp["top"] is None:
            # Falsey scalars behave as `skills or []`; other scalars cannot
            # iterate and require review. Retain a finite discriminator only.
            cp["scalar"] = (cp["scalar"] + value)[:16]
            _numeric_char(cp["number"], value)
    if final and cp["top"] is None:
        prefix, number = cp["scalar"], cp["number"]
        if prefix in {"null", "false"}:
            cp["invalid"] = False
        elif prefix and prefix[0] in "-0123456789":
            if not number["dot"] and not number["exp"]:
                cp["invalid"] = bool(number["sig_len"])
            else:
                cp["invalid"] = bool(_numeric_result(number, spec="amount"))
        else:
            cp["invalid"] = True
    cp["skills"].sort()
    return cp


def resume_step(db, plan_id, token):
    from . import service
    statement_budget(db)
    plan = db.query(models.EmployerSearchPreparation).filter_by(id=plan_id, lease_token=token).populate_existing().with_for_update(nowait=True).one()
    owned = db.query(service.core.Resume.id).filter_by(id=plan.resume_id, user_id=plan.user_id).with_for_update(read=True, nowait=True).one_or_none()
    revision = db.query(models.EmployerCatalogueResumeBinding.revision).filter_by(resume_id=plan.resume_id).scalar()
    if owned is None or revision != plan.resume_source_revision:
        plan.state, plan.failure_reason = "failed", "resume_changed"
        db.commit()
        return False
    piece = db.query(func.substr(cast(service.core.Resume.skills, Text), plan.resume_offset + 1, SLICE_CHARS)).filter(service.core.Resume.id == plan.resume_id).scalar()
    piece = "null" if piece is None and plan.resume_offset == 0 else piece or ""
    final = len(piece) < SLICE_CHARS
    cp = advance_resume(copy.deepcopy(plan.resume_checkpoint) or resume_checkpoint(), piece, final=final)
    if cp["invalid"]:
        plan.state, plan.failure_reason = "failed", "resume_skills_require_review"
    elif final:
        plan.resume_skills, plan.resume_ready, plan.resume_checkpoint = cp["skills"], True, None
    else:
        plan.resume_offset += len(piece)
        plan.resume_checkpoint = cp
    plan.updated_at = utcnow()
    db.commit()
    return final and not cp["invalid"]


def projected_fit(declared, required, warnings):
    declared = set(declared)
    required = set(required)
    matched = sorted((skill for skill in required if skill.casefold() in declared), key=str.casefold)
    missing = sorted((skill for skill in required if skill.casefold() not in declared), key=str.casefold)
    score = round(100 * len(matched) / len(required), 1) if required else 0.0
    reasons = [f"{len(matched)} of {len(required)} catalog skills mentioned in the role are documented in the resume."]
    if matched:
        reasons.append("Documented overlap: " + ", ".join(matched) + ".")
    if missing:
        reasons.append("Missing resume evidence: " + ", ".join(missing) + ".")
    uncertainties = [
        "This is skill mention overlap, not a prediction of selection or verified proficiency.",
        "Unlisted or unfamiliar terminology and transferable experience require review.",
    ]
    if not required:
        uncertainties.append("No catalog skills were identified; the zero score means insufficient catalog evidence, not ineligibility.")
    return {"score": score, "matched_skills": matched, "missing_evidence": missing,
            "required_skills": sorted(required, key=str.casefold), "reasons": reasons,
            "uncertainties": [*uncertainties, *warnings], "scoring_version": SCORING_VERSION, "mode": "basic"}


def compact_facts(validated):
    if validated is None:
        return None
    return {name: {"state": getattr(validated, name).state,
                   "value": getattr(validated, name).model_dump(mode="json")["value"]}
            for name in ("country_codes", "posting_languages", "employment_types", "salary",
                         "sponsorship_available", "work_authorization_required", "relocation_supported")}


def projected_preferences(preferences, projection, provider):
    facts = None
    if projection.preference_valid and projection.preference_provider == provider:
        values = {}
        for name, fact in projection.preference_facts.items():
            value = fact["value"]
            if name == "salary" and value is not None:
                value = SimpleNamespace(**dict(value, minimum=Decimal(value["minimum"]), maximum=Decimal(value["maximum"])))
            values[name] = SimpleNamespace(state=fact["state"], value=value)
        facts = SimpleNamespace(**values)
    return search_preferences.evaluate_facts(preferences, facts)


def _insert_absent(db, model, values, keys):
    dialect = db.get_bind().dialect.name
    maker = pg_insert if dialect == "postgresql" else sqlite_insert
    db.execute(maker(model).values(**values).on_conflict_do_nothing(index_elements=keys))


class CatalogueWorkYield(RuntimeError):
    """The work turn ended before its next SQL statement could start."""


_TURN_DEADLINE_KEY = "employer_catalogue_turn_deadline"


def _check_turn_deadline(deadline):
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise CatalogueWorkYield()
    return max(1, min(5000, math.ceil(remaining * 1000)))


def _install_turn_budget(db, deadline):
    """Fence SQL on this session's connections, including implicit ORM flushes.

    The callbacks are local to the turn and removed before lease cleanup. They
    do not bound socket/commit acquisition or Python/native CPU allocation.
    """
    if _TURN_DEADLINE_KEY in db.info:
        raise RuntimeError("Catalogue work budget is already installed")
    connections = []

    def before_statement(connection, cursor, statement, parameters, context, executemany):
        milliseconds = _check_turn_deadline(deadline)
        if connection.dialect.name == "postgresql":
            # The raw cursor avoids recursive SQLAlchemy callbacks. Each new
            # statement gets the remaining turn budget, across internal commits.
            cursor.execute(f"SET LOCAL statement_timeout = '{milliseconds}ms'")
            cursor.execute(f"SET LOCAL lock_timeout = '{min(100, milliseconds)}ms'")

    def after_begin(session, transaction, connection):
        if not event.contains(connection, "before_cursor_execute", before_statement):
            event.listen(connection, "before_cursor_execute", before_statement)
            connections.append(connection)

    def before_commit(session):
        _check_turn_deadline(deadline)

    def after_flush(session, flush_context):
        # A last implicit DML can consume the remaining budget. Refuse commit
        # after that flush, so its financial writes remain rollbackable.
        _check_turn_deadline(deadline)

    session_listeners = (("after_begin", after_begin), ("before_commit", before_commit),
        ("after_flush_postexec", after_flush))

    def remove():
        failures = []
        try:
            for name, callback in session_listeners:
                try:
                    if event.contains(db, name, callback):
                        event.remove(db, name, callback)
                except Exception as exc:
                    failures.append(exc)
        finally:
            try:
                for connection in connections:
                    try:
                        if event.contains(connection, "before_cursor_execute", before_statement):
                            event.remove(connection, "before_cursor_execute", before_statement)
                    except Exception as exc:
                        failures.append(exc)
            finally:
                connections.clear()
                db.info.pop(_TURN_DEADLINE_KEY, None)
        if failures:
            raise failures[0]

    db.info[_TURN_DEADLINE_KEY] = deadline
    try:
        for name, callback in session_listeners:
            event.listen(db, name, callback)
    except Exception:
        remove()
        raise
    return remove


def statement_budget(db):
    deadline = db.info.get(_TURN_DEADLINE_KEY)
    if deadline is not None:
        _check_turn_deadline(deadline)
    elif db.get_bind().dialect.name == "postgresql":
        # Claim/cleanup transactions retain their existing bounded SQL budget.
        db.execute(text("SET LOCAL statement_timeout = '5s'"))
        db.execute(text("SET LOCAL lock_timeout = '100ms'"))


def project_content_step(db, content_id, version=PROJECTION_VERSION):
    """One native slice, parsing outside the row lock, fenced checkpoint write."""
    statement_budget(db)
    if version != PROJECTION_VERSION:
        raise ValueError("Projection version requires its retained implementation")
    _insert_absent(db, models.EmployerCatalogueProjection,
        {"content_id": content_id, "projection_version": version}, ["content_id", "projection_version"])
    row = db.query(models.EmployerCatalogueProjection).filter_by(content_id=content_id, projection_version=version).populate_existing().with_for_update().one()
    if row.projection_state == "ready":
        db.commit()
        return True
    now = utcnow()
    if row.lease_until is not None and _utc(row.lease_until) > now:
        db.rollback()
        return False
    token = public_id("projection")
    phase, offset = row.projection_phase, row.projection_offset
    cp = copy.deepcopy(row.projection_checkpoint)
    row.lease_token, row.lease_until = token, now + timedelta(seconds=30)
    row.projection_state = "preparing"
    db.commit()
    statement_budget(db)
    content_column = models.EmployerCatalogueContent.description if phase == "description" else cast(models.EmployerCatalogueContent.preference_metadata, Text)
    piece = db.query(func.substr(content_column, offset + 1, SLICE_CHARS)).filter(models.EmployerCatalogueContent.id == content_id).scalar()
    piece = piece or "null" if phase == "metadata" and offset == 0 and piece is None else piece or ""
    final = len(piece) < SLICE_CHARS
    description_result = None
    metadata_values = None
    if phase == "description":
        cp = advance_sanitizer(cp or sanitizer_checkpoint(), piece, final=final)
        description_result = sanitizer_result(cp) if final else None
    else:
        cp = advance_metadata(cp or metadata_checkpoint(), piece, final=final)
        metadata_values = metadata_result(cp, search_preferences.PostingPreferenceMetadata) if final else None
    row = db.query(models.EmployerCatalogueProjection).filter_by(content_id=content_id, projection_version=version).populate_existing().with_for_update().one()
    if row.lease_token != token or row.projection_phase != phase or row.projection_offset != offset:
        db.rollback()
        return False
    row.lease_token, row.lease_until = None, None
    if not final:
        row.projection_offset += len(piece)
        row.projection_checkpoint = copy.deepcopy(cp)
    elif phase == "description":
        assert description_result is not None
        clean, warnings = description_result
        required, _ = extract_skills_from_text(clean)
        row.required_skills, row.scoring_warnings = sorted(required, key=str.casefold), warnings
        row.projection_phase, row.projection_offset, row.projection_checkpoint = "metadata", 0, None
    else:
        assert metadata_values is not None
        validated = search_preferences.PostingPreferenceMetadata.model_validate(metadata_values["metadata"]) if metadata_values["valid"] else None
        row.preference_valid = validated is not None
        row.preference_provider = validated.provider if validated is not None else None
        row.preference_facts = compact_facts(validated) or {}
        row.projection_state, row.projection_checkpoint = "ready", None
    db.commit()
    return row.projection_state == "ready"


def _utc(value):
    from datetime import UTC
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _id_sort(db, column):
    return column.collate("C" if db.get_bind().dialect.name == "postgresql" else "BINARY")


def _interval(model, watermark):
    return and_(model.valid_from <= watermark, or_(model.valid_to.is_(None), model.valid_to > watermark))


def inventory_step(db):
    """Inventory original rows without editing them; publisher OLD rows win races."""
    statement_budget(db)
    clock = db.query(models.EmployerCatalogueClock).filter_by(id=1).populate_existing().with_for_update(nowait=True).one()
    if clock.inventory_state == "ready":
        db.commit()
        return True
    sources = clock.inventory_state == "sources"
    model = models.EmployerSource if sources else models.EmployerPosting
    cursor = clock.source_cursor if sources else clock.posting_cursor
    query = db.query(model.id).order_by(_id_sort(db, model.id))
    if cursor is not None:
        query = query.filter(_id_sort(db, model.id) > cursor)
    ids = [row.id for row in query.limit(CHUNK_ROWS).all()]
    if db.get_bind().dialect.name == "postgresql" and ids:
        if sources:
            db.execute(text("""INSERT INTO employer_catalogue_source_revisions(source_id,valid_from,enabled,platform,region,board_token,employer_key,employer)
                SELECT s.id,0,s.enabled,s.platform,s.region,s.board_token,s.employer_key,s.employer FROM employer_sources s
                WHERE s.id=ANY(:ids) AND NOT EXISTS(SELECT 1 FROM employer_catalogue_source_revisions r WHERE r.source_id=s.id)
                ON CONFLICT DO NOTHING"""), {"ids": ids})
        else:
            db.execute(text("""INSERT INTO employer_catalogue_posting_revisions(posting_id,valid_from,content_id,source_id,external_id,requisition_id,title,employer,location,remote,is_open,publication_at,first_seen_at,last_checked_at,content_sha256)
                SELECT p.id,0,employer_catalogue_content(p),p.source_id,p.external_id,p.requisition_id,p.title,p.employer,p.location,p.remote,p.is_open,p.publication_at,p.first_seen_at,p.last_checked_at,p.content_sha256
                FROM employer_postings p WHERE p.id=ANY(:ids) AND NOT EXISTS(SELECT 1 FROM employer_catalogue_posting_revisions r WHERE r.posting_id=p.id)
                ON CONFLICT DO NOTHING"""), {"ids": ids})
    elif ids:
        # Synthetic SQLite controls exercise state/rank only, not native fences.
        for current in db.query(model).filter(model.id.in_(ids)).all():
            if sources:
                values = {name: getattr(current, name) for name in ("enabled", "platform", "region", "board_token", "employer_key", "employer")}
                _insert_absent(db, models.EmployerCatalogueSourceRevision, dict(values, source_id=current.id, valid_from=0), ["source_id", "valid_from"])
            elif db.query(models.EmployerCataloguePostingRevision.posting_id).filter_by(posting_id=current.id).first() is None:
                public = {name: getattr(current, name) for name in ("canonical_url", "apply_url", "language", "country")}
                content_id = payload_fingerprint({"description": current.description, "preference_metadata": current.preference_metadata, "public_fields": public})
                _insert_absent(db, models.EmployerCatalogueContent, {"id": content_id, "description": current.description, "preference_metadata": current.preference_metadata, "public_fields": public}, ["id"])
                values = {name: getattr(current, name) for name in ("source_id", "external_id", "requisition_id", "title", "employer", "location", "remote", "is_open", "publication_at", "first_seen_at", "last_checked_at", "content_sha256")}
                _insert_absent(db, models.EmployerCataloguePostingRevision, dict(values, posting_id=current.id, valid_from=0, content_id=content_id), ["posting_id", "valid_from"])
    if ids:
        if sources:
            clock.source_cursor = ids[-1]
        else:
            clock.posting_cursor = ids[-1]
    if len(ids) < CHUNK_ROWS:
        clock.inventory_state = "postings" if sources else "ready"
    db.commit()
    return clock.inventory_state == "ready"


def _candidate_query(db, plan):
    posting, source, content = models.EmployerCataloguePostingRevision, models.EmployerCatalogueSourceRevision, models.EmployerCatalogueContent
    query = db.query(posting, source).join(source, source.source_id == posting.source_id).join(content, content.id == posting.content_id).filter(
        _interval(posting, plan.watermark), _interval(source, plan.watermark), posting.is_open.is_(True), source.enabled.is_(True),
        posting.last_checked_at >= _utc(plan.as_of) - timedelta(hours=plan.max_source_age_hours))
    params = plan.worker_query
    if plan.posting_cursor is not None:
        query = query.filter(_id_sort(db, posting.posting_id) > plan.posting_cursor)
    if db.get_bind().dialect.name == "postgresql":
        document = func.to_tsvector(text("'simple'::regconfig"), func.coalesce(posting.title, literal_column("''")) + literal_column("' '") + func.coalesce(content.description, literal_column("''")))
        query = query.filter(or_(*(document.op("@@")(func.plainto_tsquery(text("'simple'::regconfig"), value)) for value in params["role_variants"])))
    branches = []
    for value in params["role_variants"]:
        clauses = []
        for token in list(dict.fromkeys(re.findall(r"[\w+#.-]+", value)))[:12]:
            escaped = token.replace("%", "\\%").replace("_", "\\_")
            clauses.append(or_(posting.title.ilike(f"%{escaped}%", escape="\\"), content.description.ilike(f"%{escaped}%", escape="\\")))
        if clauses:
            branches.append(and_(*clauses))
    if branches:
        query = query.filter(or_(*branches))
    if params["location"]:
        escaped = params["location"].replace("%", "\\%").replace("_", "\\_")
        query = query.filter(posting.location.ilike(f"%{escaped}%", escape="\\"))
    if params["remote_only"]:
        query = query.filter(posting.remote.is_(True))
    if params["published_within_days"]:
        query = query.filter(posting.publication_at >= _utc(plan.as_of) - timedelta(days=params["published_within_days"]))
    if params["excluded_employers"]:
        query = query.filter(func.lower(posting.employer).notin_(params["excluded_employers"]))
    return query.order_by(_id_sort(db, posting.posting_id)).limit(CHUNK_ROWS)


def scan_step(db, plan_id, token):
    statement_budget(db)
    plan = db.query(models.EmployerSearchPreparation).filter_by(id=plan_id, lease_token=token).populate_existing().with_for_update(nowait=True).one_or_none()
    if plan is None or plan.state not in ("pending", "scanning"):
        return False
    rows = _candidate_query(db, plan).all()
    preferences = search_preferences.SearchPreferencesV1.model_validate(plan.worker_query["preferences"]) if plan.worker_query["preferences"] is not None else None
    # Pending rows are invisible to db.get with the production autoflush=False
    # session. Keep at most this chunk's keys, including not-yet-flushed winners.
    winners = {}
    for posting, source in rows:
        projection = db.get(models.EmployerCatalogueProjection, (posting.content_id, plan.projection_version))
        if projection is None or projection.projection_state != "ready":
            content_id, version = posting.content_id, plan.projection_version
            prior = None if projection is None else (projection.projection_phase, projection.projection_offset, projection.projection_state)
            db.commit()
            project_content_step(db, content_id, version)
            after = db.query(models.EmployerCatalogueProjection.projection_phase,
                models.EmployerCatalogueProjection.projection_offset, models.EmployerCatalogueProjection.projection_state).filter_by(content_id=content_id, projection_version=version).first()
            if after is not None and tuple(after) != prior:
                db.query(models.EmployerSearchPreparation).filter_by(id=plan_id, lease_token=token).update({"updated_at": utcnow()}, synchronize_session=False)
            db.commit()
            return True
        evaluation = projected_preferences(preferences, projection, source.platform)
        plan.candidates_before_preferences += 1
        plan.known_preference_conflicts += not evaluation["eligible"]
        plan.candidates_with_unknown_preferences += "unknown" in evaluation["states"].values()
        if evaluation["eligible"]:
            plan.eligible_candidates += 1
            frozen = SimpleNamespace(id=posting.posting_id, external_id=posting.external_id, requisition_id=posting.requisition_id)
            key = admissions.opening_identity(frozen, source)
            fit = projected_fit(plan.resume_skills, projection.required_skills, projection.scoring_warnings or [])
            rank = (-int(round(fit["score"] * 10)), -int(plan.worker_query["role"].lower() in posting.title.lower()), posting.posting_id)
            if key not in winners:
                winner = db.get(models.EmployerCatalogueWinner, (plan.id, key))
                if winner is None:
                    winner = models.EmployerCatalogueWinner(preparation_id=plan.id, opening_key=key)
                    db.add(winner)
                winners[key] = winner
            winner = winners[key]
            if winner.posting_id is None or rank < (-winner.score_tenths, -int(winner.title_hit), winner.posting_id):
                winner.posting_id, winner.posting_revision = posting.posting_id, posting.valid_from
                winner.source_id, winner.source_revision = source.source_id, source.valid_from
                winner.score_tenths, winner.title_hit = -rank[0], bool(-rank[1])
                winner.fit, winner.preference_evaluation = fit, evaluation
        plan.posting_cursor = posting.posting_id
    plan.state = "classifying" if len(rows) < CHUNK_ROWS else "scanning"
    plan.updated_at = utcnow()
    db.commit()
    return True


def classify_step(db, plan_id, token):
    from . import delivery_history
    statement_budget(db)
    plan = db.query(models.EmployerSearchPreparation).filter_by(id=plan_id, lease_token=token).one_or_none()
    if plan is None or plan.state not in ("classifying", "selecting"):
        return False
    user_id = plan.user_id
    db.commit()
    statement_budget(db)
    delivery_history.advance_owner(db, user_id, statement_timeout_ms=5000)
    db.commit()
    statement_budget(db)
    lock_owner_nowait(db, user_id)
    plan = db.query(models.EmployerSearchPreparation).filter_by(id=plan_id, lease_token=token).populate_existing().with_for_update(nowait=True).one_or_none()
    if plan is None:
        db.rollback()
        return False
    progress = db.query(models.EmployerPaidHistoryProgress).filter_by(user_id=user_id).populate_existing().first()
    generation = progress.generation if progress is not None else 0
    if plan.history_generation != generation:
        plan.history_generation, plan.classification_cursor = generation, None
        plan.state = "classifying"
    query = db.query(models.EmployerCatalogueWinner).filter_by(preparation_id=plan.id).order_by(models.EmployerCatalogueWinner.opening_key)
    if plan.classification_cursor is not None:
        query = query.filter(models.EmployerCatalogueWinner.opening_key > plan.classification_cursor)
    winners = query.limit(CHUNK_ROWS).all()
    previous, physical, state = delivery_history.membership(db, user_id, [w.opening_key for w in winners], [w.posting_id for w in winners])
    for winner in winners:
        winner.seen = winner.opening_key in previous or winner.posting_id in physical
        winner.classification_generation = generation
        plan.classification_cursor = winner.opening_key
    if len(winners) < CHUNK_ROWS:
        plan.state = "selecting"
    plan.updated_at = utcnow()
    db.commit()
    return state


def status(db, user_id, plan_id):
    plan = db.query(models.EmployerSearchPreparation).options(load_only(*(getattr(models.EmployerSearchPreparation, name) for name in (
        "id", "user_id", "state", "as_of", "desired_count", "accepted_quote", "candidates_before_preferences", "failure_reason")))).filter_by(
        id=plan_id, user_id=user_id).first()
    if plan is None:
        raise HTTPException(404, "Search preparation not found")
    charged = db.query(models.EmployerSearch.charged_credits).filter_by(id=plan.id, user_id=user_id).scalar() if plan.state == "completed" else 0
    return {"id": plan.id, "kind": "search_preparation", "status": plan.state, "as_of": plan.as_of,
            "desired_count": plan.desired_count, "accepted_quote": plan.accepted_quote,
            "candidates_examined": plan.candidates_before_preferences, "charged_credits": charged,
            "completed_search_id": plan.id if plan.state == "completed" else None,
            "failure_reason": plan.failure_reason}


def lock_owner_nowait(db, user_id):
    from ..entitlements import lock_entitlement_owner
    if db.get_bind().dialect.name == "postgresql":
        db.execute(text("SELECT id FROM users WHERE id=:owner FOR UPDATE NOWAIT"), {"owner": user_id})
    return lock_entitlement_owner(db, user_id)


def accept(db, user_id, payload):
    """Accept a fixed zero reservation, then atomically pin W with the clock."""
    from . import credits, service
    service._require_enabled()
    if config.paid_history_mode() != "indexed":
        raise HTTPException(503, "Full catalogue search requires indexed paid-delivery history")
    search_input = service._search_input(payload)
    fingerprint = payload_fingerprint(search_input)
    search_id = "search_" + payload_fingerprint({"owner": user_id, "key": payload.idempotency_key})[:48]
    existing = db.query(models.EmployerSearch).filter_by(user_id=user_id, idempotency_key=payload.idempotency_key).first()
    if existing is not None:
        if existing.input_fingerprint != fingerprint:
            raise HTTPException(409, "This request key was already used with different search settings")
        return existing
    lock_owner_nowait(db, user_id)
    existing = db.query(models.EmployerSearch).filter_by(user_id=user_id, idempotency_key=payload.idempotency_key).populate_existing().first()
    if existing is not None:
        if existing.input_fingerprint != fingerprint:
            raise HTTPException(409, "This request key was already used with different search settings")
        return existing
    plan = db.query(models.EmployerSearchPreparation).options(load_only(
        models.EmployerSearchPreparation.id, models.EmployerSearchPreparation.state,
        models.EmployerSearchPreparation.input_fingerprint)).filter_by(
        user_id=user_id, idempotency_key=payload.idempotency_key).first()
    if plan is not None:
        if plan.input_fingerprint != fingerprint:
            raise HTTPException(409, "This request key was already used with different search settings")
        db.commit()
        if plan.state == "accepting":
            _finish_acceptance(db, plan.id, user_id, search_input)
        return plan
    reservation = db.query(models.ServiceCreditReservation).filter_by(user_id=user_id, source_id=search_id, operation="job_search").first()
    if reservation is None:
        from ...billing.cost_policy import CostPolicyUnavailable
        try:
            prices = config.prices()
        except CostPolicyUnavailable as exc:
            raise HTTPException(503, detail=str(exc)) from None
        if payload.desired_count > int(prices["max_search_jobs"]):
            raise HTTPException(422, "Requested job count exceeds the current service limit")
        reservation = credits.reserve(db, user_id=user_id, operation="job_search", source_id=search_id,
            unit_price=int(prices["search_credits_per_job"]), count=0, pricing_version=str(prices["pricing_version"]), record_event=False)
        reservation.cost_policy_snapshot = dict(reservation.cost_policy_snapshot or {}) | {
            "search_preparation": True, "history_search_fingerprint": fingerprint,
            "history_desired_count": payload.desired_count, "history_accepted_max_jobs": int(prices["max_search_jobs"])}
    else:
        authority = reservation.cost_policy_snapshot or {}
        if (reservation.state != "reserved" or reservation.reserved_amount != 0 or not authority.get("search_preparation")
                or authority.get("history_search_fingerprint") != fingerprint or authority.get("history_desired_count") != payload.desired_count):
            raise HTTPException(409, "This search preparation is already in progress or needs review")
    owned_resume = db.query(service.core.Resume.id).filter_by(id=payload.resume_id, user_id=user_id).first()
    if owned_resume is None:
        raise HTTPException(404, "Not found")
    reservation_id, unit_price, pricing_version = reservation.id, reservation.unit_price, reservation.pricing_version
    db.commit()
    # A key-share preclaim avoids waiting on a User FK while holding catalogue W.
    if db.get_bind().dialect.name == "postgresql":
        db.execute(text("SELECT id FROM users WHERE id=:owner FOR KEY SHARE NOWAIT"), {"owner": user_id})
    db.query(service.core.Resume.id).filter_by(id=payload.resume_id, user_id=user_id).with_for_update(read=True, nowait=True).one()
    _insert_absent(db, models.EmployerCatalogueResumeBinding, {"resume_id": payload.resume_id, "revision": 1}, ["resume_id"])
    resume_revision = db.query(models.EmployerCatalogueResumeBinding.revision).filter_by(resume_id=payload.resume_id).scalar()
    accepted_resume = payload_fingerprint({"resume_id": payload.resume_id, "source_revision": resume_revision})
    clock = db.query(models.EmployerCatalogueClock).filter_by(id=1).populate_existing().with_for_update(nowait=True).one()
    plan = db.query(models.EmployerSearchPreparation).options(load_only(
        models.EmployerSearchPreparation.id, models.EmployerSearchPreparation.user_id,
        models.EmployerSearchPreparation.input_fingerprint)).filter_by(id=search_id).first()
    if plan is None:
        plan = models.EmployerSearchPreparation(id=search_id, user_id=user_id, resume_id=payload.resume_id,
            reservation_id=reservation_id, idempotency_key=payload.idempotency_key, input_fingerprint=fingerprint,
            query={}, worker_query={}, projection_version=PROJECTION_VERSION, state="accepting",
            accepted_quote={"unit_price": unit_price, "pricing_version": pricing_version,
                "requested_count": payload.desired_count, "maximum_credits": payload.desired_count * unit_price,
                "search_input_fingerprint": fingerprint},
            resume_fingerprint=accepted_resume, resume_skills=[], resume_source_revision=resume_revision,
            as_of=utcnow(), watermark=clock.watermark,
            max_source_age_hours=config.positive_int("EMPLOYER_MAX_SOURCE_AGE_HOURS", 24, 168), desired_count=payload.desired_count)
        db.add(plan)
    elif plan.user_id != user_id or plan.input_fingerprint != fingerprint:
        raise HTTPException(409, "This request key was already used with different search settings")
    db.commit()
    _finish_acceptance(db, plan.id, user_id, search_input)
    return plan


def _finish_acceptance(db, plan_id, user_id, search_input):
    from ..dispatch.service import enqueue
    # Original and worker query data are formed/written after User/clock locks
    # release. FK keys do not change; outbox publication is atomic with this.
    params = dict(search_input)
    params["preferences"] = search_input.get("preferences")
    # Native postings have <=200 employer codepoints; lower() has <=3x growth.
    params["excluded_employers"] = [name for raw in search_input.get("excluded_employers", [])
        if len(name := raw.lower()) <= 600]
    params["role_variants"] = role_aliases.variants(search_input["role"])
    plan = db.query(models.EmployerSearchPreparation).options(load_only(
        models.EmployerSearchPreparation.id, models.EmployerSearchPreparation.state,
        models.EmployerSearchPreparation.input_fingerprint)).filter_by(
        id=plan_id, user_id=user_id).populate_existing().with_for_update(nowait=True).one()
    if plan.state == "accepting":
        if payload_fingerprint(search_input) != plan.input_fingerprint:
            raise ValueError("Accepted query fingerprint changed")
        plan.query, plan.worker_query, plan.state = search_input, params, "pending"
        enqueue(db, topic="employer.search", aggregate_id=plan_id,
            payload={"search_id": plan_id}, key=f"employer-search:{plan_id}")
    db.commit()


def _selected(db, plan):
    winner = models.EmployerCatalogueWinner
    return db.query(winner).filter_by(preparation_id=plan.id, stale=False,
        classification_generation=plan.history_generation).order_by(winner.seen,
        winner.score_tenths.desc(), winner.title_hit.desc(), _id_sort(db, winner.posting_id)).limit(plan.desired_count).all()


def _current_selected(db, plan, selected, *, fence=False):
    """Completion-time freshness and exact canonical recipe, bounded to 100."""
    source_ids = sorted({winner.source_id for winner in selected})
    posting_ids = sorted(winner.posting_id for winner in selected)
    source_fields = ("id", "enabled", "platform", "region", "board_token", "employer_key", "submission_enabled",
        "form_parity_verified", "submission_grant", "credential_env")
    receipt = text("COALESCE(employer_sources.receipt_contract::jsonb NOT IN ('null'::jsonb,'{}'::jsonb,'[]'::jsonb,'false'::jsonb,'0'::jsonb,'\"\"'::jsonb),false)") if db.get_bind().dialect.name == "postgresql" else text("COALESCE(employer_sources.receipt_contract NOT IN ('null','{}','[]','false','0','\"\"'),0)")
    sources = db.query(*(getattr(models.EmployerSource, field) for field in source_fields), receipt).filter(models.EmployerSource.id.in_(source_ids)).order_by(models.EmployerSource.id)
    postings = db.query(models.EmployerPosting).options(load_only(*(getattr(models.EmployerPosting, field) for field in
        ("id", "source_id", "external_id", "requisition_id", "last_checked_at", "is_open")))).filter(
        models.EmployerPosting.id.in_(posting_ids)).order_by(_id_sort(db, models.EmployerPosting.id)).populate_existing()
    if fence:
        sources, postings = sources.with_for_update(read=True, nowait=True), postings.with_for_update(read=True, nowait=True)
    source_map = {row[0]: SimpleNamespace(**dict(zip(source_fields, row[:-1], strict=True)), receipt_contract=bool(row[-1])) for row in sources.all()}
    posting_map = {posting.id: posting for posting in postings.all()}
    stale = []
    cutoff = utcnow() - timedelta(hours=plan.max_source_age_hours)
    for winner in selected:
        source, posting = source_map.get(winner.source_id), posting_map.get(winner.posting_id)
        if (source is None or posting is None or not source.enabled or not posting.is_open
                or posting.source_id != winner.source_id or _utc(posting.last_checked_at) < cutoff
                or admissions.opening_identity(posting, source) != winner.opening_key):
            stale.append(winner)
    return source_map, posting_map, stale


def _discard_snapshot(db, snapshot):
    db.query(models.EmployerCatalogueSnapshotItem).filter_by(snapshot_id=snapshot.id).delete(synchronize_session=False)
    db.delete(snapshot)
    db.flush()


def _item_recipe(posting, source):
    from .delivery_history_projector import IDENTITY_RECIPE_VERSION
    return {"recipe_version": IDENTITY_RECIPE_VERSION, "source_id": source.source_id,
        "platform": source.platform, "region": source.region, "tenant": source.board_token,
        "verified_employer_key": source.employer_key, "requisition_id": posting.requisition_id,
        "external_id": posting.external_id}


def _native_item(db, snapshot, plan, ordinal, winner, current_source):
    from . import service
    posting = db.get(models.EmployerCataloguePostingRevision, (winner.posting_id, winner.posting_revision))
    source = db.get(models.EmployerCatalogueSourceRevision, (winner.source_id, winner.source_revision))
    amount = 0 if winner.seen else plan.accepted_quote["unit_price"]
    bounded_posting = {"id": posting.posting_id, "source_id": posting.source_id, "external_id": posting.external_id,
        "title": posting.title, "employer": posting.employer, "location": posting.location,
        "publication_at": posting.publication_at.isoformat() if posting.publication_at else None,
        "first_seen_at": posting.first_seen_at.isoformat(), "last_checked_at": posting.last_checked_at.isoformat(),
        "remote": posting.remote, "is_open": posting.is_open, "content_sha256": posting.content_sha256,
        "platform": source.platform, "application_mode": service._source_mode(current_source),
        "opening_key": winner.opening_key,
        "publication_kind": "release_or_republication" if source.platform == "smartrecruiters" else "publication" if posting.publication_at else "unknown"}
    values = {"snapshot": snapshot.id, "owner": plan.user_id, "ordinal": ordinal, "posting": winner.posting_id,
        "posting_revision": winner.posting_revision, "source": winner.source_id, "source_revision": winner.source_revision,
        "key": winner.opening_key, "seen": winner.seen, "amount": amount,
        "recipe": json.dumps(_item_recipe(posting, source)), "content": posting.content_id,
        "public_posting": json.dumps(bounded_posting), "fit": json.dumps(winner.fit),
        "evaluation": json.dumps(winner.preference_evaluation)}
    if db.get_bind().dialect.name == "postgresql":
        db.execute(text("""WITH native AS (
            SELECT jsonb_build_object('posting', CAST(:public_posting AS jsonb) || jsonb_build_object(
              'description',c.description,'canonical_url',c.public_fields->'canonical_url',
              'apply_url',c.public_fields->'apply_url','preference_metadata',c.preference_metadata),
              'fit',CAST(:fit AS jsonb),'charged_credits',:amount,
              'preference_evaluation',CAST(:evaluation AS jsonb)) AS body
            FROM employer_catalogue_contents c WHERE c.id=:content)
            INSERT INTO employer_catalogue_snapshot_items(snapshot_id,ordinal,user_id,posting_id,posting_revision,
              source_id,source_revision,opening_key,seen,unit_price,identity_proof,body,item_digest,proof_state)
            SELECT :snapshot,:ordinal,:owner,:posting,:posting_revision,:source,:source_revision,:key,:seen,:amount,
              CAST(:recipe AS jsonb),body,encode(sha256(convert_to(body::text,'UTF8')),'hex'),'prepared' FROM native
            ON CONFLICT DO NOTHING"""), values)
    else:
        content = db.get(models.EmployerCatalogueContent, posting.content_id)
        body = {"posting": dict(bounded_posting, description=content.description,
            canonical_url=content.public_fields["canonical_url"], apply_url=content.public_fields["apply_url"],
            preference_metadata=content.preference_metadata), "fit": winner.fit,
            "charged_credits": amount, "preference_evaluation": winner.preference_evaluation}
        db.add(models.EmployerCatalogueSnapshotItem(snapshot_id=snapshot.id, ordinal=ordinal, user_id=plan.user_id,
            posting_id=winner.posting_id, posting_revision=winner.posting_revision, source_id=winner.source_id,
            source_revision=winner.source_revision, opening_key=winner.opening_key, seen=winner.seen,
            unit_price=amount, identity_proof=_item_recipe(posting, source), body=body,
            item_digest=payload_fingerprint(body), proof_state="prepared"))


def snapshot_step(db, plan_id, token):
    """Native per-item and aggregate work outside financial locks; no Search yet."""
    statement_budget(db)
    plan = db.query(models.EmployerSearchPreparation).filter_by(id=plan_id, lease_token=token).populate_existing().with_for_update(nowait=True).one()
    selected = _selected(db, plan)
    sources, _, stale = _current_selected(db, plan, selected)
    if stale:
        for winner in stale:
            winner.stale, winner.stale_reason = True, "no_longer_current_eligible"
        db.commit()
        return False
    snapshot = db.query(models.EmployerCatalogueSnapshot).filter_by(preparation_id=plan.id).order_by(
        models.EmployerCatalogueSnapshot.created_at.desc()).first()
    if snapshot is not None:
        rows = db.query(models.EmployerCatalogueSnapshotItem).filter_by(snapshot_id=snapshot.id).order_by(
            models.EmployerCatalogueSnapshotItem.ordinal).limit(101).all()
        if (snapshot.history_generation != plan.history_generation or snapshot.item_count != len(selected)
                or any(item.posting_id != selected[item.ordinal].posting_id or item.seen != selected[item.ordinal].seen
                    for item in rows if item.ordinal < len(selected)) or len(rows) > len(selected)):
            _discard_snapshot(db, snapshot)
            snapshot = None
    if snapshot is None:
        snapshot = models.EmployerCatalogueSnapshot(id=public_id("snapshot"), user_id=plan.user_id,
            preparation_id=plan.id, query={}, items=[], state="building", item_count=len(selected),
            history_generation=plan.history_generation)
        db.add(snapshot)
        db.flush([snapshot])
        if db.get_bind().dialect.name == "postgresql":
            db.execute(text("""UPDATE employer_catalogue_snapshots s SET query=p.query
                FROM employer_search_preparations p WHERE s.id=:snapshot AND p.id=s.preparation_id"""), {"snapshot": snapshot.id})
        else:
            snapshot.query = plan.query
        rows = []
    if snapshot.state == "proven":
        identity = snapshot.id
        db.commit()
        return identity
    if len(rows) < len(selected):
        ordinal = len(rows)
        _native_item(db, snapshot, plan, ordinal, selected[ordinal], sources[selected[ordinal].source_id])
        plan.updated_at = utcnow()
        db.commit()
        return False
    if db.get_bind().dialect.name == "postgresql":
        valid = db.execute(text("""SELECT count(*) FROM employer_catalogue_snapshot_items i
            WHERE i.snapshot_id=:snapshot AND i.user_id=:owner AND
              i.body->'posting'->>'id'=i.posting_id AND i.body->'posting'->>'source_id'=i.source_id AND
              i.body->'posting'->>'opening_key'=i.opening_key AND
              i.body::jsonb->'charged_credits'=to_jsonb(i.unit_price) AND
              i.item_digest=encode(sha256(convert_to(i.body::jsonb::text,'UTF8')),'hex')"""),
            {"snapshot": snapshot.id, "owner": plan.user_id}).scalar()
        if valid != len(selected):
            raise ValueError("Native result scalar/body binding mismatch")
        db.execute(text("""UPDATE employer_catalogue_snapshot_items SET proof_state='proven' WHERE snapshot_id=:snapshot"""), {"snapshot": snapshot.id})
        db.execute(text("""WITH assembled AS (SELECT COALESCE(jsonb_agg(body ORDER BY ordinal),'[]'::jsonb) AS items
            FROM employer_catalogue_snapshot_items WHERE snapshot_id=:snapshot)
            UPDATE employer_catalogue_snapshots s SET items=a.items,
              snapshot_digest=encode(sha256(convert_to(jsonb_build_object('query',s.query,'items',a.items)::text,'UTF8')),'hex'),
              state='proven' FROM assembled a WHERE s.id=:snapshot AND s.state='building'"""), {"snapshot": snapshot.id})
    else:
        for item in rows:
            if item.item_digest != payload_fingerprint(item.body) or item.body["posting"]["id"] != item.posting_id or item.body["charged_credits"] != item.unit_price:
                raise ValueError("Native result scalar/body binding mismatch")
            item.proof_state = "proven"
        snapshot.items = [item.body for item in rows]
        snapshot.snapshot_digest = payload_fingerprint({"query": snapshot.query, "items": snapshot.items})
        snapshot.state = "proven"
    identity = snapshot.id
    db.commit()
    return identity


def finish(db, plan_id, token):
    from . import credits, delivery_history, service
    identity = snapshot_step(db, plan_id, token)
    if not identity:
        return "pending"
    statement_budget(db)
    owner = db.query(models.EmployerSearchPreparation.user_id).filter_by(id=plan_id).scalar()
    lock_owner_nowait(db, owner)
    plan = db.query(models.EmployerSearchPreparation).options(load_only(*(getattr(models.EmployerSearchPreparation, name) for name in (
        "id", "user_id", "state", "lease_token", "history_generation", "resume_id", "resume_source_revision", "projection_version",
        "resume_ready", "reservation_id", "accepted_quote", "input_fingerprint", "idempotency_key",
        "desired_count", "as_of", "watermark", "max_source_age_hours", "candidates_before_preferences",
        "known_preference_conflicts", "candidates_with_unknown_preferences")))).filter_by(id=plan_id, lease_token=token).populate_existing().with_for_update(nowait=True).one_or_none()
    if plan is None or plan.state != "selecting":
        db.rollback()
        return "pending"
    existing = db.query(models.EmployerSearch.id).filter_by(id=plan.id, user_id=owner).first()
    if existing:
        plan.state = "completed"
        db.commit()
        return "completed"
    if plan.projection_version != PROJECTION_VERSION:
        db.rollback()
        cancel(db, None, plan_id, reason="projection_version_unavailable")
        return "failed"
    snapshot = db.query(models.EmployerCatalogueSnapshot).filter_by(id=identity, user_id=owner, state="proven").populate_existing().with_for_update(read=True, nowait=True).one()
    selected = _selected(db, plan)
    sources, _, stale = _current_selected(db, plan, selected, fence=True)
    resume = db.query(service.core.Resume.id).filter_by(id=plan.resume_id, user_id=owner).with_for_update(read=True, nowait=True).one_or_none()
    revision = db.query(models.EmployerCatalogueResumeBinding.revision).filter_by(resume_id=plan.resume_id).scalar()
    if resume is None or revision != plan.resume_source_revision or not plan.resume_ready:
        plan.state, plan.failure_reason = "failed", "resume_changed"
        db.commit()
        return "failed"
    if stale:
        for winner in stale:
            winner.stale, winner.stale_reason = True, "no_longer_current_eligible"
        db.commit()
        return "pending"
    progress = delivery_history._progress(db, owner)
    previous, physical, history_state = delivery_history.membership(db, owner,
        [winner.opening_key for winner in selected], [winner.posting_id for winner in selected])
    if progress.generation != plan.history_generation:
        plan.state, plan.classification_cursor = "classifying", None
        db.commit()
        return "pending"
    items = db.query(models.EmployerCatalogueSnapshotItem).filter_by(snapshot_id=identity, user_id=owner).order_by(
        models.EmployerCatalogueSnapshotItem.ordinal).limit(101).all()
    if (snapshot.history_generation != plan.history_generation or len(items) != len(selected)
            or any(item.ordinal != ordinal or item.posting_id != selected[ordinal].posting_id
                or item.seen != (item.opening_key in previous or item.posting_id in physical)
                for ordinal, item in enumerate(items))):
        plan.state, plan.classification_cursor = "classifying", None
        db.commit()
        return "pending"
    new_count = sum(not item.seen for item in items)
    if new_count and history_state != "ready":
        if history_state == "unresolved":
            plan.state, plan.failure_reason = "failed", "paid_history_requires_review"
            db.commit()
            return "failed"
        plan.state, plan.classification_cursor = "classifying", None
        db.commit()
        return "pending"
    preparation = db.query(models.ServiceCreditReservation).filter_by(id=plan.reservation_id, user_id=owner).populate_existing().one()
    quote = plan.accepted_quote
    if (preparation.unit_price != quote["unit_price"] or preparation.pricing_version != quote["pricing_version"]
            or preparation.state != "reserved" or preparation.reserved_amount != 0
            or (preparation.cost_policy_snapshot or {}).get("history_search_fingerprint") != plan.input_fingerprint):
        raise ValueError("Accepted search quote changed")
    scope_inputs = db.query(models.EmployerSearchPreparation.worker_query["role_variants"],
        models.EmployerSearchPreparation.worker_query["published_within_days"]).filter_by(id=plan.id).one()
    reservation = credits.reserve(db, user_id=owner, operation="job_search", source_id=plan.id,
        unit_price=quote["unit_price"], count=plan.desired_count if new_count else 0,
        pricing_version=quote["pricing_version"], preparation=preparation)
    search = models.EmployerSearch(id=plan.id, user_id=owner, resume_id=plan.resume_id,
        idempotency_key=plan.idempotency_key, input_fingerprint=plan.input_fingerprint, query={}, items=[],
        desired_count=plan.desired_count, delivered_count=len(items), status="completed",
        reserved_credits=reservation.reserved_amount, charged_credits=new_count * reservation.unit_price,
        refunded_credits=reservation.reserved_amount - new_count * reservation.unit_price,
        scope={"kind": "verified_employer_index", "candidate_limit": None, "candidate_limit_reached": False,
            "catalogue_complete": True, "catalogue_as_of": plan.as_of.isoformat(), "catalogue_watermark": plan.watermark,
            "catalogue_plan_version": PLAN_VERSION,
            "max_source_age_hours": plan.max_source_age_hours, "worldwide_recall_verified": False,
            "unknown_publication_dates_excluded": bool(scope_inputs[1]),
            "role_alias_policy": role_aliases.VERSION, "role_variants": scope_inputs[0],
            "preference_policy": search_preferences.POLICY_VERSION, "unknown_preference_metadata": "include",
            "candidates_before_preferences": plan.candidates_before_preferences,
            "known_preference_conflicts": plan.known_preference_conflicts,
            "candidates_with_unknown_preferences": plan.candidates_with_unknown_preferences,
            "search_input_fingerprint": plan.input_fingerprint, "price_quote": quote},
        native_snapshot_id=identity)
    db.add(search)
    db.flush([search])
    deliveries = [models.EmployerJobDelivery(id=public_id("delivery"), user_id=owner, posting_id=item.posting_id,
        opening_key=item.opening_key, search_id=plan.id, charged_credits=item.unit_price) for item in items if not item.seen]
    db.add_all(deliveries)
    db.flush(deliveries)
    delivery_history.write_completed_native(db, search_id=plan.id, snapshot_id=identity,
        reservation_id=reservation.id, deliveries=deliveries, restore_ready=history_state == "ready")
    credits.settle(db, reservation, completed_count=new_count, reason="Unique useful employer jobs delivered")
    plan.state, plan.completed_at, plan.updated_at = "completed", utcnow(), utcnow()
    db.commit()
    return "completed"


def cancel(db, user_id, plan_id, *, reason="cancelled_by_owner"):
    """Explicit terminal policy; an unfinished plan has only a zero reserve."""
    from ..dispatch.models import DispatchOutbox
    from . import credits
    owner = db.query(models.EmployerSearchPreparation.user_id).filter_by(id=plan_id).scalar()
    if owner is None or (user_id is not None and owner != user_id):
        raise HTTPException(404, "Search preparation not found")
    db.commit()
    lock_owner_nowait(db, owner)
    plan = db.query(models.EmployerSearchPreparation).options(load_only(*(getattr(models.EmployerSearchPreparation, name) for name in (
        "id", "user_id", "state", "reservation_id", "failure_reason", "lease_token", "lease_until", "updated_at")))).filter_by(
        id=plan_id, user_id=owner).populate_existing().with_for_update(nowait=True).one()
    if plan.state == "completed":
        db.commit()
        return status(db, owner, plan.id)
    reservation = db.query(models.ServiceCreditReservation).filter_by(id=plan.reservation_id, user_id=owner).populate_existing().one()
    if reservation.reserved_amount != 0 or reservation.committed_amount != 0:
        raise ValueError("Incomplete catalogue plan has a financial effect")
    if reservation.state == "reserved":
        credits.settle(db, reservation, completed_count=0, reason="Search preparation ended before delivery")
    if plan.state not in {"failed", "cancelled"}:
        plan.state = "cancelled" if reason == "cancelled_by_owner" else "failed"
        plan.failure_reason, plan.lease_token, plan.lease_until = reason, None, None
        plan.updated_at = utcnow()
    db.query(DispatchOutbox).filter_by(topic="employer.search", aggregate_id=plan_id).filter(
        ~DispatchOutbox.status.in_(("completed", "failed"))).update({"status": "completed", "completed_at": utcnow(),
            "lease_token": None, "lease_until": None, "last_error": "search_preparation_ended"}, synchronize_session=False)
    db.commit()
    cleanup_terminal(db, plan.id)
    return status(db, owner, plan.id)


def erase_owner_state(db, owner_id, *, resume_id=None):
    """Bounded ordinary hook after cancellation and completed Search erasure.

    Each call removes at most 128 winners or one snapshot (<=100 items) or
    one plan. Return True to continue; the caller authorizes erasure and commits.
    This does not erase reservations, deliveries or paid history evidence.
    """
    statement_budget(db)
    query = db.query(models.EmployerSearchPreparation).options(load_only(
        models.EmployerSearchPreparation.id, models.EmployerSearchPreparation.state)).filter_by(user_id=owner_id)
    if resume_id is not None:
        query = query.filter_by(resume_id=resume_id)
    plan = query.order_by(models.EmployerSearchPreparation.id).limit(1).with_for_update(nowait=True).one_or_none()
    if plan is None:
        return False
    if db.query(models.EmployerSearch.id).filter_by(user_id=owner_id, id=plan.id).first():
        raise ValueError("Erase completed searches/deliveries before catalogue preparation state")
    if plan.state not in {"failed", "cancelled", "completed"}:
        raise ValueError("Cancel active preparation before lifecycle erasure")
    keys = [row[0] for row in db.query(models.EmployerCatalogueWinner.opening_key).filter_by(
        preparation_id=plan.id).order_by(models.EmployerCatalogueWinner.opening_key).limit(CHUNK_ROWS).all()]
    if keys:
        db.query(models.EmployerCatalogueWinner).filter_by(preparation_id=plan.id).filter(
            models.EmployerCatalogueWinner.opening_key.in_(keys)).delete(synchronize_session=False)
    else:
        snapshot = db.query(models.EmployerCatalogueSnapshot).filter_by(preparation_id=plan.id).order_by(
            models.EmployerCatalogueSnapshot.id).limit(1).with_for_update(nowait=True).one_or_none()
        if snapshot is not None:
            _discard_snapshot(db, snapshot)
        else:
            db.delete(plan)
    db.flush()
    return True


def cleanup_terminal(db, plan_id):
    """Release abandoned winners/bodies outside owner locks; completed proof stays."""
    statement_budget(db)
    plan = db.query(models.EmployerSearchPreparation).filter_by(id=plan_id).populate_existing().with_for_update(nowait=True).one_or_none()
    if plan is None or plan.state not in {"completed", "failed", "cancelled"}:
        db.rollback()
        return
    keys = [row[0] for row in db.query(models.EmployerCatalogueWinner.opening_key).filter_by(
        preparation_id=plan_id).order_by(models.EmployerCatalogueWinner.opening_key).limit(CHUNK_ROWS).all()]
    if keys:
        db.query(models.EmployerCatalogueWinner).filter_by(preparation_id=plan_id).filter(
            models.EmployerCatalogueWinner.opening_key.in_(keys)).delete(synchronize_session=False)
    if plan.state != "completed":
        for snapshot in db.query(models.EmployerCatalogueSnapshot).filter_by(preparation_id=plan_id).order_by(
                models.EmployerCatalogueSnapshot.id).limit(1).all():
            _discard_snapshot(db, snapshot)
    db.commit()


def maintenance_step(db):
    """Existing refresh workers perform one bounded retention/abandonment turn."""
    statement_budget(db)
    plan = models.EmployerSearchPreparation
    # Progress updates refresh updated_at. Catalogue churn and total plan age
    # never expire an actively advancing plan; only 30 days of inactivity do.
    abandoned = db.query(plan.id, plan.user_id).filter(plan.state.in_(("accepting", *ACTIVE_STATES)),
        plan.updated_at < utcnow() - timedelta(days=30),
        or_(plan.lease_until.is_(None), plan.lease_until < utcnow())).order_by(plan.updated_at, plan.id).limit(1).first()
    db.commit()
    if abandoned:
        cancel(db, abandoned.user_id, abandoned.id, reason="abandoned_without_progress")
    else:
        winner = models.EmployerCatalogueWinner
        terminal = db.query(plan.id).filter(plan.state.in_(("completed", "failed", "cancelled")),
            db.query(winner.preparation_id).filter(winner.preparation_id == plan.id).exists()).order_by(plan.id).limit(1).first()
        db.commit()
        if terminal:
            cleanup_terminal(db, terminal.id)
    prune_revisions(db)


def prune_revisions(db):
    """One clock-gated batch; active as-of pins and durable FK proofs survive."""
    statement_budget(db)
    clock = db.query(models.EmployerCatalogueClock).filter_by(id=1).populate_existing().with_for_update(nowait=True).one()
    watermark = db.query(func.min(models.EmployerSearchPreparation.watermark)).filter(
        models.EmployerSearchPreparation.state.in_(("accepting", *ACTIVE_STATES))).scalar()
    watermark = clock.watermark if watermark is None else watermark
    for revision, identity, winner_identity, winner_revision in (
        (models.EmployerCataloguePostingRevision, "posting_id", "posting_id", "posting_revision"),
        (models.EmployerCatalogueSourceRevision, "source_id", "source_id", "source_revision"),
    ):
        column = getattr(revision, identity)
        winner, item = models.EmployerCatalogueWinner, models.EmployerCatalogueSnapshotItem
        rows = db.query(column, revision.valid_from).filter(revision.valid_to <= watermark,
            ~db.query(winner.preparation_id).filter(getattr(winner, winner_identity) == column,
                getattr(winner, winner_revision) == revision.valid_from).exists(),
            ~db.query(item.snapshot_id).filter(getattr(item, winner_identity) == column,
                getattr(item, winner_revision) == revision.valid_from).exists()).order_by(column, revision.valid_from).limit(CHUNK_ROWS).all()
        for row in rows:
            db.query(revision).filter(column == row[0], revision.valid_from == row[1]).delete(synchronize_session=False)
    db.commit()


def execute_plan(db, plan_id):
    """One cooperative worker turn; dispatch retries pending work automatically."""
    from sqlalchemy.exc import DBAPIError

    from . import service
    try:
        statement_budget(db)
        owner = db.query(models.EmployerSearchPreparation.user_id).filter_by(id=plan_id).scalar()
        if owner is None:
            db.rollback()
            return "missing"
        # Serialize only the brief per-owner work claim, then release before any
        # catalogue/native body work. A live plan lease prevents parallel scans.
        if db.get_bind().dialect.name == "postgresql":
            db.execute(text("SELECT id FROM users WHERE id=:owner FOR UPDATE NOWAIT"), {"owner": owner})
        plan = db.query(models.EmployerSearchPreparation).options(load_only(*(getattr(models.EmployerSearchPreparation, name) for name in (
            "id", "user_id", "state", "lease_token", "lease_until", "work_generation", "updated_at", "projection_version")))).filter_by(
            id=plan_id).populate_existing().with_for_update(nowait=True).one_or_none()
        if plan is None:
            db.rollback()
            return "missing"
        if plan.state not in ACTIVE_STATES:
            state = plan.state
            db.rollback()
            return state
        if plan.projection_version != PROJECTION_VERSION:
            db.rollback()
            cancel(db, None, plan_id, reason="projection_version_unavailable")
            return "failed"
        now = utcnow()
        if plan.lease_until is not None and _utc(plan.lease_until) > now:
            db.rollback()
            return "pending"
        other = db.query(models.EmployerSearchPreparation.id).filter(
            models.EmployerSearchPreparation.user_id == owner, models.EmployerSearchPreparation.id != plan_id,
            models.EmployerSearchPreparation.lease_until > now).order_by(models.EmployerSearchPreparation.lease_until).limit(1).first()
        if other:
            db.rollback()
            return "pending"
        prior_progress = _utc(plan.updated_at)
        token = public_id("searchwork")
        plan.lease_token, plan.lease_until = token, now + timedelta(seconds=30)
        plan.work_generation += 1
        db.commit()
    except DBAPIError as exc:
        db.rollback()
        code = getattr(exc.orig, "sqlstate", None) or getattr(exc.orig, "pgcode", None)
        if code not in {"40001", "55P03", "57014"}:
            raise
        return "pending"
    deadline = time.monotonic() + WORK_SECONDS
    result = "pending"
    remove_budget = None
    try:
        remove_budget = _install_turn_budget(db, deadline)
        while time.monotonic() < deadline:
            if not inventory_step(db):
                db.query(models.EmployerSearchPreparation).filter_by(id=plan_id, lease_token=token).update({"updated_at": utcnow()}, synchronize_session=False)
                db.commit()
                continue
            plan = db.query(models.EmployerSearchPreparation).filter_by(id=plan_id, lease_token=token).populate_existing().first()
            if plan is None:
                break
            state = plan.state
            resume_ready = plan.resume_ready
            db.commit()
            if not resume_ready:
                resume_step(db, plan_id, token)
            elif state in ("pending", "scanning"):
                scan_step(db, plan_id, token)
            elif state == "classifying":
                classify_step(db, plan_id, token)
            elif state == "selecting":
                result = service.finish_catalogue_preparation(db, plan_id, token)
                if result != "pending":
                    break
            else:
                result = state
                break
    except CatalogueWorkYield:
        db.rollback()
    except DBAPIError as exc:
        db.rollback()
        code = getattr(exc.orig, "sqlstate", None) or getattr(exc.orig, "pgcode", None)
        if code not in {"40001", "55P03", "57014"}:
            raise
    except Exception:
        # Proof, quote and business failures must roll back reserve/Search/
        # Delivery writes before releasing the worker lease.
        db.rollback()
        raise
    finally:
        try:
            if remove_budget is not None:
                remove_budget()
        except Exception:
            # Never execute lease/money SQL or return a connection to the pool
            # through possibly residual callbacks. The bounded lease expires.
            try:
                db.invalidate()
            finally:
                db.rollback()
            raise
        # Cleanup cannot mistake uncommitted work for persisted progress.
        db.rollback()
        statement_budget(db)
        db.query(models.EmployerSearchPreparation).filter_by(id=plan_id, lease_token=token).update(
            {"lease_token": None, "lease_until": None}, synchronize_session=False)
        db.commit()
    if result in {"completed", "failed", "cancelled"}:
        cleanup_terminal(db, plan_id)
    if result == "pending":
        progressed = db.query(models.EmployerSearchPreparation.updated_at).filter_by(id=plan_id).scalar()
        db.commit()
        if progressed is not None and _utc(progressed) > prior_progress:
            from ..dispatch.service import EmployerSearchContinuation
            return EmployerSearchContinuation(plan_id)
    return result


# Verified native-slice projection helpers; source pin is retained in the packet.
"""Pure, serializable streaming projections; no application imports or IO.

Sanitizer equivalence proof for the seven pinned patterns:
* Match spans of different patterns do not overlap. Their literal start words
  are absent inside the other word sequences, with one exception: `system`
  inside `disregard ... system prompt`. That occurrence cannot simultaneously
  be followed by `message`, as the system-message pattern requires.
* The replacement marker has no pattern start/match. Its brackets prevent a
  match from crossing a replacement boundary.
Consequently substitutions cannot destroy/create another pattern's match, and
one union scan produces the same string and the same deduplicated warning.

The NFA below uses Python re for one-character IGNORECASE and whitespace
predicates, preserving its Unicode semantics. Its literal state is finite;
unbounded whitespace runs loop without retaining their raw content. Only the
first remaining output-capacity characters of an unresolved span are retained.
"""




SANITIZER_LIMIT = 12_000


SANITIZER_MARKER = "[removed untrusted instruction]"


SANITIZER_WARNING = "Removed suspicious instruction-like text from one or more job descriptions."


_WS = "\0"


_SANITIZER_BRANCHES = tuple(
    tuple(branch.replace(" ", _WS))
    for branch in (
        "ignore previous instructions",
        "ignore all previous instructions",
        "disregard system prompt",
        "disregard the system prompt",
        "you are now ",
        "developer message",
        "system message",
        "return only ",
        "do not follow ",
    )
)


_LETTER_TESTS = {
    char: re.compile(re.escape(char), re.IGNORECASE)
    for branch in _SANITIZER_BRANCHES
    for char in branch
    if char != _WS
}


_SPACE_TEST = re.compile(r"\s")


def sanitizer_checkpoint() -> dict:
    return {
        "version": SANITIZER_VERSION,
        "offset": 0,
        "pending_start": 0,
        "clean": "",
        "pending": "",
        "candidates": [],
        "suppress_whitespace": False,
        "warning": False,
        "done": False,
    }


def _sanitizer_emit(cp: dict, value: str) -> None:
    cp["clean"] += value[: SANITIZER_LIMIT - len(cp["clean"])]


def _sanitizer_flush(cp: dict, end: int) -> None:
    count = end - cp["pending_start"]
    _sanitizer_emit(cp, cp["pending"][:count])
    cp["pending"] = cp["pending"][count:]
    cp["pending_start"] = end


def _sanitizer_transitions(states: list, char: str, space: bool) -> tuple[list, list]:
    next_states, complete = set(), []
    for branch_index, position in states:
        branch = _SANITIZER_BRANCHES[branch_index]
        token = branch[position]
        if token == _WS:
            if space:
                next_states.add((branch_index, position))
                if position + 1 == len(branch):
                    complete.append(branch_index)
                else:
                    next_states.add((branch_index, position + 1))
        elif _LETTER_TESTS[token].fullmatch(char):
            if position + 1 == len(branch):
                complete.append(branch_index)
            else:
                next_states.add((branch_index, position + 1))
    return sorted(next_states), complete


def advance_sanitizer(cp: dict, text: str, *, final: bool = False) -> dict:
    """Consume a caller-bounded decoded character chunk into an existing checkpoint.

    `clean` and `pending` together never exceed 12000 characters. Candidate
    starts occur only in a fixed number of literal characters of an unfinished
    match; arbitrary whitespace creates no new starts. All state is JSON-safe.
    Native byte offsets / incremental UTF8 decoding belong to the caller.
    """
    if cp.get("version") != SANITIZER_VERSION or cp.get("done"):
        raise ValueError("Invalid or completed sanitizer checkpoint")
    for char in text:
        index = cp["offset"]
        cp["offset"] += 1
        space = bool(_SPACE_TEST.fullmatch(char))
        if cp["suppress_whitespace"]:
            if space:
                cp["pending_start"] = cp["offset"]
                continue
            cp["suppress_whitespace"] = False
        if len(cp["pending"]) < SANITIZER_LIMIT - len(cp["clean"]):
            cp["pending"] += char
        candidates, complete = [], []
        for candidate in cp["candidates"]:
            states, accepted = _sanitizer_transitions(candidate["states"], char, space)
            if states:
                candidates.append({"start": candidate["start"], "states": states})
            for branch_index in accepted:
                complete.append((candidate["start"], branch_index))
        initial = [[i, 0] for i in range(len(_SANITIZER_BRANCHES))]
        states, accepted = _sanitizer_transitions(initial, char, space)
        if states:
            candidates.append({"start": index, "states": states})
        for branch_index in accepted:
            complete.append((index, branch_index))
        if complete:
            start, branch_index = min(complete)
            _sanitizer_flush(cp, start)
            cp["pending"] = ""
            cp["pending_start"] = cp["offset"]
            cp["candidates"] = []
            _sanitizer_emit(cp, SANITIZER_MARKER)
            cp["warning"] = True
            cp["suppress_whitespace"] = _SANITIZER_BRANCHES[branch_index][-1] == _WS
        else:
            cp["candidates"] = candidates
            end = min((item["start"] for item in candidates), default=cp["offset"])
            _sanitizer_flush(cp, end)
        if len(cp["clean"]) + len(cp["pending"]) > SANITIZER_LIMIT:
            raise AssertionError("Sanitizer output state exceeded its fixed bound")
    if final:
        _sanitizer_flush(cp, cp["offset"])
        cp["candidates"] = []
        cp["suppress_whitespace"] = False
        cp["done"] = True
    return cp


def sanitizer_result(cp: dict) -> tuple[str, list[str]]:
    if not cp.get("done"):
        raise ValueError("Sanitizer remains unfinished")
    return cp["clean"], [SANITIZER_WARNING] if cp["warning"] else []




_BAD = {"__invalid_metadata_stream__": True}


_JSON_WHITESPACE = " \r\n\t"


_MODEL_WHITESPACE = frozenset("\t\n\v\f\r \u0085\u00a0\u1680\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007\u2008\u2009\u200a\u2028\u2029\u202f\u205f\u3000")


_FACT_TYPES = {
    "country_codes": "array:country",
    "posting_languages": "array:language",
    "employment_types": "array:employment",
    "salary": "salary",
    "sponsorship_available": "bool",
    "work_authorization_required": "array:country",
    "relocation_supported": "bool",
}


_OBJECT_FIELDS = {
    "root": {"version": "version", "normalization_version": "policy", "provider": "provider"}
    | {name: "fact:" + name for name in _FACT_TYPES},
    "evidence": {"field": "field", "document": "document", "raw_sha256": "digest"},
    "salary": {"minimum": "amount", "maximum": "amount", "currency": "currency", "period": "period"},
}


_STRING_BOUNDS = {
    "country": (2, True), "language": (6, True), "currency": (3, True),
    "field": (200, True), "document": (1000, True), "digest": (64, True),
    "reason": (300, True), "state": (7, False), "policy": (23, False),
    "provider": (15, False), "employment": (11, False), "period": (5, False),
    "key": (64, False),
}


_ARRAY_LIMITS = {"country": 20, "language": 20, "employment": 8, "evidence": 20}


_EXPONENT_CAP = 10**20


_FLOAT_DIGITS = 1200  # More than the decimal precision of any binary64 midpoint.


def _numeric_checkpoint(*, quoted: bool) -> dict:
    return {
        "quoted": quoted, "phase": "leading" if quoted else "body",
        "part": "start", "sign": "", "digits": 0, "fraction": 0,
        "sig_len": 0, "prefix": "", "tail_nonzero": False,
        "trailing_zeros": 0, "exp_sign": 1, "exponent": 0,
        "exp_digits": 0, "exponent_big": False, "bad": False,
        "dot": False, "exp": False,
    }


def _numeric_char(number: dict, char: str) -> None:
    if number["bad"]:
        return
    if number["quoted"]:
        if number["phase"] == "leading" and char.isspace():
            return
        if char.isspace():
            number["phase"] = "trailing"
            return
        if number["phase"] == "trailing":
            number["bad"] = True
            return
        number["phase"] = "body"
        if char == "_":
            return
    part = number["part"]
    if char in "+-" and part == "start":
        number["sign"] = char
        number["part"] = "integer"
        return
    if char in "+-" and part == "exp_start":
        number["exp_sign"] = -1 if char == "-" else 1
        number["part"] = "exp_digits"
        return
    if char == "." and part in {"start", "integer"} and not number["dot"]:
        number["dot"] = True
        number["part"] = "fraction"
        return
    if char in "eE" and part in {"integer", "fraction"} and number["digits"]:
        number["exp"] = True
        number["part"] = "exp_start"
        return
    try:
        digit = unicodedata.decimal(char) if number["quoted"] else int(char) if char in "0123456789" else None
    except (TypeError, ValueError):
        digit = None
    if digit is None:
        number["bad"] = True
        return
    if part in {"exp_start", "exp_digits"}:
        number["part"] = "exp_digits"
        number["exp_digits"] += 1
        if number["exponent"] <= _EXPONENT_CAP:
            number["exponent"] = min(_EXPONENT_CAP + 1, number["exponent"] * 10 + digit)
        if number["exponent"] > _EXPONENT_CAP:
            number["exponent_big"] = True
        return
    if part not in {"start", "integer", "fraction"}:
        number["bad"] = True
        return
    number["part"] = "fraction" if number["dot"] else "integer"
    number["digits"] += 1
    if number["dot"]:
        number["fraction"] += 1
    if digit or number["sig_len"]:
        number["sig_len"] += 1
        cap = 15 if number["quoted"] else _FLOAT_DIGITS
        if len(number["prefix"]) < cap:
            number["prefix"] += str(digit)
        elif digit:
            number["tail_nonzero"] = True
        number["trailing_zeros"] = number["trailing_zeros"] + 1 if digit == 0 else 0


def _numeric_result(number: dict, *, spec: str):
    if number["bad"] or not number["digits"] or number["exp"] and not number["exp_digits"]:
        return dict(_BAD)
    exponent = number["exp_sign"] * number["exponent"]
    if spec == "version":
        return 1 if not number["quoted"] and not number["dot"] and not number["exp"] and number["sign"] != "-" and number["sig_len"] == 1 and number["prefix"] == "1" else dict(_BAD)
    if spec != "amount":
        return dict(_BAD)
    if not number["quoted"] and (number["dot"] or number["exp"]):
        # Native JSON decoders produce a float for decimal/exponent tokens.
        # Preserve binary64 rounding, including valid underflow to zero.
        if not number["sig_len"]:
            return -0.0 if number["sign"] == "-" else 0.0
        if number["exponent_big"]:
            return 0.0 if number["exp_sign"] < 0 else dict(_BAD)
        prefix = number["prefix"]
        if number["tail_nonzero"]:
            prefix += "1"
        scale = exponent - number["fraction"] + number["sig_len"] - len(prefix)
        try:
            value = float(number["sign"] + prefix + "e" + str(scale))
        except (ValueError, OverflowError):
            return dict(_BAD)
        return value if math.isfinite(value) else dict(_BAD)
    if number["exponent_big"]:
        return dict(_BAD)
    raw_scale = exponent - number["fraction"]
    if not MIN_ETINY <= raw_scale <= MAX_EMAX:
        return dict(_BAD)
    if not number["sig_len"]:
        return "-0" if number["sign"] == "-" else "0"
    if raw_scale + number["sig_len"] - 1 > MAX_EMAX:
        return dict(_BAD)
    significant = number["sig_len"] - number["trailing_zeros"]
    if significant > 14:
        return dict(_BAD)
    prefix = number["prefix"][:significant]
    return number["sign"] + prefix + "e" + str(raw_scale + number["trailing_zeros"])


def metadata_checkpoint() -> dict:
    return {
        "version": METADATA_STREAM_VERSION, "stack": [], "lex": None,
        "root": None, "root_set": False, "syntax_invalid": False, "done": False,
    }


def _metadata_object_fields(spec: str):
    if spec.startswith("fact:"):
        return {"state": "state", "value": _FACT_TYPES[spec[5:]], "evidence": "array:evidence", "reason": "reason"}
    return _OBJECT_FIELDS.get(spec)


def _metadata_next_spec(cp: dict) -> str:
    if not cp["stack"]:
        return "root"
    frame = cp["stack"][-1]
    if frame["kind"] == "array":
        return frame["spec"][6:] if frame["count"] < _ARRAY_LIMITS[frame["spec"][6:]] else "skip"
    return _metadata_object_fields(frame["spec"]).get(frame["key"], "skip")


def _metadata_attach(cp: dict, value) -> None:
    if not cp["stack"]:
        if cp["root_set"]:
            cp["syntax_invalid"] = True
        cp["root"], cp["root_set"] = value, True
        return
    frame = cp["stack"][-1]
    if frame["kind"] == "array":
        frame["count"] += 1
        if frame["count"] <= _ARRAY_LIMITS[frame["spec"][6:]]:
            frame["value"].append(value)
        else:
            frame["bad"] = True
    else:
        if frame["key"] in _metadata_object_fields(frame["spec"]):
            frame["value"][frame["key"]] = value  # Native JSON object last-wins semantics.
        else:
            frame["bad"] = True
        frame["key"] = None
    frame["expect"] = "comma_or_end"


def _metadata_start_value(cp: dict, char: str, spec: str, *, key: bool = False) -> None:
    if char in "{[":
        proper = bool(_metadata_object_fields(spec)) if char == "{" else spec.startswith("array:")
        if proper:
            kind = "object" if char == "{" else "array"
            cp["stack"].append({"kind": kind, "spec": spec, "value": {} if kind == "object" else [], "key": None, "expect": "key_or_end" if kind == "object" else "value_or_end", "count": 0, "bad": False})
        else:
            cp["lex"] = {"kind": "skip", "depth": 1, "quoted": False, "escape": False}
        return
    if char == '"':
        bound, strip = _STRING_BOUNDS.get(spec, (0, False))
        cp["lex"] = {"kind": "string", "spec": spec, "key": key, "capture": "", "trailing": "", "leading": True, "strip": strip, "bound": bound, "bad": spec not in _STRING_BOUNDS and spec != "amount", "escape": False, "unicode": None, "surrogate": None, "number": _numeric_checkpoint(quoted=True) if spec == "amount" else None}
        return
    cp["lex"] = {"kind": "primitive", "spec": spec, "capture": "", "number": _numeric_checkpoint(quoted=False), "numeric": char in "-0123456789"}
    _metadata_primitive_char(cp["lex"], char)


def _metadata_primitive_char(lex: dict, char: str) -> None:
    if lex["numeric"]:
        _numeric_char(lex["number"], char)
    elif len(lex["capture"]) < 8:
        lex["capture"] += char
    else:
        lex["capture"] = "invalid!"


def _metadata_string_char(lex: dict, char: str) -> None:
    if lex["number"] is not None:
        _numeric_char(lex["number"], char)
        return
    if lex["bad"]:
        return
    if lex["strip"] and char in _MODEL_WHITESPACE:
        if not lex["leading"] and len(lex["trailing"]) <= lex["bound"]:
            lex["trailing"] += char
        return
    lex["leading"] = False
    if len(lex["capture"]) + len(lex["trailing"]) + 1 > lex["bound"]:
        lex["bad"] = True
        lex["capture"] = lex["trailing"] = ""
    else:
        lex["capture"] += lex["trailing"] + char
        lex["trailing"] = ""


def _metadata_decoded_char(lex: dict, char: str) -> None:
    code = ord(char)
    pending = lex["surrogate"]
    if pending is not None:
        if 0xDC00 <= code <= 0xDFFF:
            _metadata_string_char(lex, chr(0x10000 + ((pending - 0xD800) << 10) + code - 0xDC00))
            lex["surrogate"] = None
            return
        _metadata_string_char(lex, chr(pending))
        lex["surrogate"] = None
    if 0xD800 <= code <= 0xDBFF:
        lex["surrogate"] = code
    else:
        _metadata_string_char(lex, char)


def _metadata_finish_lex(cp: dict) -> None:
    lex = cp["lex"]
    if lex["kind"] == "primitive":
        if lex["numeric"]:
            value = _numeric_result(lex["number"], spec=lex["spec"])
        else:
            value = {"true": True, "false": False, "null": None}.get(lex["capture"], dict(_BAD))
        _metadata_attach(cp, value)
    elif lex["kind"] == "string":
        if lex["surrogate"] is not None:
            _metadata_string_char(lex, chr(lex["surrogate"]))
        value = _numeric_result(lex["number"], spec="amount") if lex["number"] is not None else dict(_BAD) if lex["bad"] else lex["capture"]
        if lex["key"]:
            frame = cp["stack"][-1]
            frame["key"] = value if isinstance(value, str) else "__unknown_oversized_key__"
            frame["expect"] = "colon"
        else:
            _metadata_attach(cp, value)
    cp["lex"] = None


def advance_metadata(cp: dict, text: str, *, final: bool = False) -> dict:
    """Reduce chunks of a DB-native, syntactically valid JSON representation.

    This is deliberately not a general arbitrary-input JSON syntax validator.
    Invalid-shaped containers are skipped using a depth counter, relying on
    native JSON's syntax guarantee. Valid model structure is at most four
    containers deep. Provenance strings are trimmed incrementally; literal
    fields retain whitespace exactly. Duplicated known keys preserve last-wins.
    No projection becomes authoritative before exact full-model validation.
    """
    if cp.get("version") != METADATA_STREAM_VERSION or cp.get("done"):
        raise ValueError("Invalid or completed metadata checkpoint")
    index = 0
    while index < len(text) and not cp["syntax_invalid"]:
        char = text[index]
        lex = cp["lex"]
        if lex:
            if lex["kind"] == "skip":
                if lex["quoted"]:
                    if lex["escape"]:
                        lex["escape"] = False
                    elif char == "\\":
                        lex["escape"] = True
                    elif char == '"':
                        lex["quoted"] = False
                elif char == '"':
                    lex["quoted"] = True
                elif char in "{[":
                    lex["depth"] += 1
                elif char in "}]":
                    lex["depth"] -= 1
                    if not lex["depth"]:
                        cp["lex"] = None
                        _metadata_attach(cp, dict(_BAD))
            elif lex["kind"] == "primitive":
                if char in _JSON_WHITESPACE + ",]}":
                    _metadata_finish_lex(cp)
                    continue
                _metadata_primitive_char(lex, char)
            elif lex["unicode"] is not None:
                if char not in "0123456789abcdefABCDEF":
                    cp["syntax_invalid"] = True
                else:
                    lex["unicode"] += char
                    if len(lex["unicode"]) == 4:
                        _metadata_decoded_char(lex, chr(int(lex["unicode"], 16)))
                        lex["unicode"] = None
            elif lex["escape"]:
                lex["escape"] = False
                if char == "u":
                    lex["unicode"] = ""
                else:
                    escaped = {'"': '"', "\\": "\\", "/": "/", "b": "\b", "f": "\f", "n": "\n", "r": "\r", "t": "\t"}.get(char)
                    if escaped is None:
                        cp["syntax_invalid"] = True
                    else:
                        _metadata_decoded_char(lex, escaped)
            elif char == "\\":
                lex["escape"] = True
            elif char == '"':
                _metadata_finish_lex(cp)
            elif ord(char) < 32:
                cp["syntax_invalid"] = True
            else:
                _metadata_decoded_char(lex, char)
        elif char not in _JSON_WHITESPACE:
            frame = cp["stack"][-1] if cp["stack"] else None
            if frame is None:
                if cp["root_set"]:
                    cp["syntax_invalid"] = True
                else:
                    _metadata_start_value(cp, char, _metadata_next_spec(cp))
                index += 1
                continue
            expect = frame["expect"]
            if expect == "colon":
                if char != ":":
                    cp["syntax_invalid"] = True
                else:
                    frame["expect"] = "value"
            elif expect == "comma_or_end":
                closing = "}" if frame["kind"] == "object" else "]"
                if char == closing:
                    cp["stack"].pop()
                    _metadata_attach(cp, dict(_BAD) if frame["bad"] else frame["value"])
                elif char == ",":
                    frame["expect"] = "key" if frame["kind"] == "object" else "value"
                else:
                    cp["syntax_invalid"] = True
            elif expect in {"key", "key_or_end"}:
                if char == '"':
                    _metadata_start_value(cp, char, "key", key=True)
                elif char == "}" and expect == "key_or_end":
                    cp["stack"].pop()
                    _metadata_attach(cp, dict(_BAD) if frame["bad"] else frame["value"])
                else:
                    cp["syntax_invalid"] = True
            elif expect == "value_or_end" and char == "]":
                cp["stack"].pop()
                _metadata_attach(cp, dict(_BAD) if frame["bad"] else frame["value"])
            elif expect in {"value", "value_or_end"}:
                _metadata_start_value(cp, char, _metadata_next_spec(cp))
            else:
                cp["syntax_invalid"] = True
        index += 1
    if final:
        if cp["lex"] and cp["lex"]["kind"] == "primitive":
            _metadata_finish_lex(cp)
        if cp["lex"] or cp["stack"] or not cp["root_set"]:
            cp["syntax_invalid"] = True
        cp["done"] = True
    return cp


def metadata_result(cp: dict, posting_metadata_model) -> dict:
    """Validate the complete bounded normalized object with the pinned model."""
    if not cp.get("done"):
        raise ValueError("Metadata normalization remains unfinished")
    if cp["syntax_invalid"] or cp["root"] is None:
        return {"valid": False, "metadata": None}
    try:
        validated = posting_metadata_model.model_validate(cp["root"])
    except (ValueError, TypeError):
        return {"valid": False, "metadata": None}
    return {"valid": True, "metadata": validated.model_dump(mode="json")}
