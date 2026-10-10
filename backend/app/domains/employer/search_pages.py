"""Read saved employer searches without creating new deliveries or credits.

Summary SQL excludes result JSON. Proven native snapshots page immutable item
rows without decoding the complete saved array. Legacy result pagination still
loads and hashes its saved array so edits cannot silently skip or repeat results.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import re
import struct
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import and_, or_
from sqlalchemy.orm import Session

from ... import security
from . import models, schemas

MAX_CURSOR_LENGTH = 512
MAX_PAGE_SIZE = 20
_VERSION = 1
_SUMMARIES = 1
_RESULTS = 2
_DOMAIN = b"hirewiz:employer-search-pages:v1\x00"
_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
_HEAD = struct.Struct(">BBQ")
_SUMMARY = struct.Struct(">qH")
_RESULT = struct.Struct(">IH")
_METADATA_FIELDS = (
    "id", "status", "desired_count", "delivered_count", "reserved_credits",
    "charged_credits", "refunded_credits", "created_at", "query", "scope",
)
_PRIVATE = {"Cache-Control": "private, no-store"}


def _error(status: int, code: str, message: str) -> HTTPException:
    return HTTPException(status, {"code": code, "message": message}, headers=_PRIVATE)


def _invalid() -> HTTPException:
    return _error(400, "invalid_search_cursor", "This saved-search cursor is invalid. Start from the first page.")


def _unavailable() -> HTTPException:
    return _error(409, "saved_search_snapshot_unavailable", "This saved result snapshot needs review.")


def _json_bytes(value: Any) -> bytes:
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"),
                          ensure_ascii=False, allow_nan=False).encode("utf-8")
    except (TypeError, ValueError, UnicodeError, RecursionError):
        raise _unavailable() from None


def _limit(limit: int) -> None:
    if type(limit) is not int or not 1 <= limit <= MAX_PAGE_SIZE:
        raise _error(400, "invalid_search_page_size", "Saved-search pages support 1 to 20 items.")


def _key() -> bytes:
    # Separate these signatures from access tokens and every other cursor domain.
    return hmac.digest(security.JWT_SECRET.encode("utf-8"), _DOMAIN + b"signing-key", "sha256")


def _seal(payload: bytes) -> str:
    signature = hmac.digest(_key(), _DOMAIN + payload, "sha256")
    return base64.urlsafe_b64encode(payload + signature).rstrip(b"=").decode("ascii")


def _open(cursor: str, owner_id: int, kind: int) -> bytes:
    if (not isinstance(cursor, str) or not 1 <= len(cursor) <= MAX_CURSOR_LENGTH
            or re.fullmatch(r"[A-Za-z0-9_-]+", cursor) is None):
        raise _invalid()
    try:
        raw = base64.b64decode(cursor + "=" * (-len(cursor) % 4), altchars=b"-_", validate=True)
        if base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii") != cursor:
            raise ValueError("Noncanonical encoding")
        payload, supplied = raw[:-32], raw[-32:]
        expected = hmac.digest(_key(), _DOMAIN + payload, "sha256")
        if len(payload) < _HEAD.size or not hmac.compare_digest(expected, supplied):
            raise ValueError("Invalid signature")
        version, payload_kind, payload_owner = _HEAD.unpack(payload[:_HEAD.size])
        if version != _VERSION or payload_kind != kind or payload_owner != owner_id:
            raise ValueError("Invalid scope")
    except (ValueError, binascii.Error, struct.error):
        raise _invalid() from None
    return payload[_HEAD.size:]


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _micros(value: datetime) -> int:
    delta = _utc(value) - _EPOCH
    return (delta.days * 86400 + delta.seconds) * 1_000_000 + delta.microseconds


def _identity(raw: bytes) -> str:
    try:
        identity = raw.decode("utf-8")
    except UnicodeDecodeError:
        raise _invalid() from None
    if not 1 <= len(identity) <= 64:
        raise _invalid()
    return identity


def _summary_cursor(owner_id: int, metadata: schemas.SearchSummary) -> str:
    identity = metadata.id.encode("utf-8")
    return _seal(_HEAD.pack(_VERSION, _SUMMARIES, owner_id)
                 + _SUMMARY.pack(_micros(metadata.created_at), len(identity)) + identity)


def _summary_boundary(cursor: str, owner_id: int) -> tuple[datetime, str]:
    payload = _open(cursor, owner_id, _SUMMARIES)
    try:
        micros, size = _SUMMARY.unpack(payload[:_SUMMARY.size])
        if not 1 <= size <= 256 or len(payload) != _SUMMARY.size + size:
            raise ValueError("Invalid boundary")
        boundary = _EPOCH + timedelta(microseconds=micros)
    except (ValueError, OverflowError, struct.error):
        raise _invalid() from None
    return boundary, _identity(payload[_SUMMARY.size:])


def _metadata(row: Any) -> schemas.SearchSummary:
    try:
        metadata = schemas.SearchSummary.model_validate({key: row._mapping[key] for key in _METADATA_FIELDS})
    except ValidationError:
        raise _unavailable() from None
    body = metadata.model_dump()
    body["created_at"] = _utc(metadata.created_at).isoformat(timespec="microseconds")
    _json_bytes(body)
    return metadata


def list_summaries(db: Session, owner_id: int, *, limit: int = 10,
                   cursor: str | None = None) -> schemas.SearchSummaryPage:
    _limit(limit)
    boundary = _summary_boundary(cursor, owner_id) if cursor is not None else None
    with db.no_autoflush:
        query = db.query(*(getattr(models.EmployerSearch, key) for key in _METADATA_FIELDS)).filter(
            models.EmployerSearch.user_id == owner_id,
        )
        if boundary is not None:
            created_at, identity = boundary
            query = query.filter(or_(models.EmployerSearch.created_at < created_at, and_(
                models.EmployerSearch.created_at == created_at, models.EmployerSearch.id < identity,
            )))
        try:
            rows = query.order_by(models.EmployerSearch.created_at.desc(), models.EmployerSearch.id.desc()).limit(limit + 1).all()
        except (json.JSONDecodeError, UnicodeError, RecursionError):
            raise _unavailable() from None
    items = [_metadata(row) for row in rows[:limit]]
    next_cursor = _summary_cursor(owner_id, items[-1]) if len(rows) > limit else None
    return schemas.SearchSummaryPage(items=items, next_cursor=next_cursor)


def _snapshot_version(metadata: schemas.SearchSummary, items: list[Any]) -> str:
    body = metadata.model_dump()
    body["created_at"] = _utc(metadata.created_at).isoformat(timespec="microseconds")
    body["items"] = items
    # Bind counts, the saved price quote, query, scope, array order and all content.
    encoded = _json_bytes(body)
    return "saved-search-v1:" + hashlib.sha256(encoded).hexdigest()


def _result_cursor(owner_id: int, identity: str, offset: int, version: str) -> str:
    raw_id = identity.encode("utf-8")
    return _seal(_HEAD.pack(_VERSION, _RESULTS, owner_id) + _RESULT.pack(offset, len(raw_id))
                 + raw_id + bytes.fromhex(version.removeprefix("saved-search-v1:")))


def _result_boundary(cursor: str, owner_id: int, identity: str) -> tuple[int, str]:
    payload = _open(cursor, owner_id, _RESULTS)
    try:
        offset, size = _RESULT.unpack(payload[:_RESULT.size])
        if not 1 <= size <= 256 or len(payload) != _RESULT.size + size + 32 or offset == 0:
            raise ValueError("Invalid result offset")
    except (ValueError, struct.error):
        raise _invalid() from None
    if _identity(payload[_RESULT.size:_RESULT.size + size]) != identity:
        raise _invalid()
    return offset, "saved-search-v1:" + payload[-32:].hex()


def _native_version(metadata: schemas.SearchSummary, snapshot_id: str,
                    digest: str, total: int) -> str:
    # The immutable native digest covers the original query and all item bodies.
    # Bind mutable public search metadata without hashing that complete array.
    body = metadata.model_dump(exclude={"query"})
    body["created_at"] = _utc(metadata.created_at).isoformat(timespec="microseconds")
    body["native_snapshot"] = {"id": snapshot_id, "digest": digest, "count": total}
    return "saved-search-v1:" + hashlib.sha256(_json_bytes(body)).hexdigest()


def _native_results(db: Session, owner_id: int, identity: str, row: Any, *, limit: int,
                    offset: int, expected_version: str | None,
                    has_cursor: bool) -> schemas.SavedSearchResultsPage:
    metadata = _metadata(row)
    values = row._mapping
    snapshot_id, total, digest = (values["native_snapshot_id"], values["_snapshot_count"],
                                  values["_snapshot_digest"])
    if (metadata.status != "completed" or values["_snapshot_state"] != "proven"
            or values["_snapshot_owner"] != owner_id or values["_snapshot_id"] != snapshot_id
            or type(total) is not int or total < 0 or total != metadata.delivered_count
            or not isinstance(digest, str) or re.fullmatch(r"[a-f0-9]{64}", digest) is None):
        raise _unavailable()
    version = _native_version(metadata, snapshot_id, digest, total)
    if expected_version is not None and expected_version != version:
        raise _error(409, "saved_search_snapshot_changed", "This saved search changed. Reload its first page.")
    if has_cursor and offset >= total:
        raise _invalid()
    end = min(offset + limit, total)
    with db.no_autoflush:
        try:
            item = models.EmployerCatalogueSnapshotItem
            rows = db.query(item.ordinal, item.body, item.item_digest, item.proof_state).filter(
                item.snapshot_id == snapshot_id, item.user_id == owner_id,
                item.ordinal >= offset, item.ordinal < end,
            ).order_by(item.ordinal).limit(limit).all()
        except (json.JSONDecodeError, UnicodeError, RecursionError):
            raise _unavailable() from None
    if len(rows) != end - offset or any(
            saved.ordinal != offset + index or saved.proof_state != "proven"
            or not isinstance(saved.item_digest, str)
            or re.fullmatch(r"[a-f0-9]{64}", saved.item_digest) is None
            for index, saved in enumerate(rows)):
        raise _unavailable()
    next_cursor = _result_cursor(owner_id, identity, end, version) if end < total else None
    try:
        return schemas.SavedSearchResultsPage(
            search=metadata, items=[saved.body for saved in rows], total_items=total,
            start_index=offset, next_cursor=next_cursor, snapshot_version=version,
        )
    except ValidationError:
        raise _unavailable() from None


def saved_results(db: Session, owner_id: int, identity: str, *, limit: int = 10,
                  cursor: str | None = None) -> schemas.SavedSearchResultsPage:
    _limit(limit)
    offset, expected_version = _result_boundary(cursor, owner_id, identity) if cursor is not None else (0, None)
    # Select only metadata and proof scalars first. Neither hybrid items nor the
    # aggregate native body may appear in this query or in summary SQL.
    with db.no_autoflush:
        try:
            snapshot = models.EmployerCatalogueSnapshot
            native = db.query(*(getattr(models.EmployerSearch, key) for key in _METADATA_FIELDS),
                              models.EmployerSearch.native_snapshot_id,
                              snapshot.id.label("_snapshot_id"), snapshot.user_id.label("_snapshot_owner"),
                              snapshot.state.label("_snapshot_state"), snapshot.item_count.label("_snapshot_count"),
                              snapshot.snapshot_digest.label("_snapshot_digest")).outerjoin(snapshot, and_(
                snapshot.id == models.EmployerSearch.native_snapshot_id,
                snapshot.user_id == models.EmployerSearch.user_id,
            )).filter(models.EmployerSearch.id == identity, models.EmployerSearch.user_id == owner_id).first()
        except (json.JSONDecodeError, UnicodeError, RecursionError):
            raise _unavailable() from None
    if native is None:
        raise _error(404, "saved_search_not_found", "Saved search not found.")
    if native._mapping["native_snapshot_id"] is not None:
        return _native_results(db, owner_id, identity, native, limit=limit, offset=offset,
                               expected_version=expected_version, has_cursor=cursor is not None)
    with db.no_autoflush:
        try:
            row = db.query(*(getattr(models.EmployerSearch, key) for key in _METADATA_FIELDS), models.EmployerSearch.items).filter(
                models.EmployerSearch.id == identity, models.EmployerSearch.user_id == owner_id,
            ).first()
        except (json.JSONDecodeError, UnicodeError, RecursionError):
            raise _unavailable() from None
    if row is None:
        raise _error(404, "saved_search_not_found", "Saved search not found.")
    metadata = _metadata(row)
    saved_items = row._mapping["items"]
    if not isinstance(saved_items, list):
        raise _unavailable()
    version = _snapshot_version(metadata, saved_items)
    if expected_version is not None and expected_version != version:
        raise _error(409, "saved_search_snapshot_changed", "This saved search changed. Reload its first page.")
    total = len(saved_items)
    if cursor is not None and offset >= total:
        raise _invalid()
    end = min(offset + limit, total)
    next_cursor = _result_cursor(owner_id, identity, end, version) if end < total else None
    try:
        return schemas.SavedSearchResultsPage(
            search=metadata, items=saved_items[offset:end], total_items=total,
            start_index=offset, next_cursor=next_cursor, snapshot_version=version,
        )
    except ValidationError:
        # Historical damage is not a reason to coerce financial values or expose
        # raw saved data through an exception. The legacy endpoints stay intact.
        raise _unavailable() from None
