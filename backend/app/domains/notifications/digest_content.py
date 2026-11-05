"""Deterministic updates to already obtained/saved roles, not a job search.

Reads bounded current catalogue metadata only. This module does not prove native
account/consent authority, reserve mail capacity, enqueue work or send anything.
"""

from __future__ import annotations

import hashlib
import html
import json
import re
import unicodedata
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from html.parser import HTMLParser
from types import SimpleNamespace
from urllib.parse import urlsplit

from sqlalchemy import Text, case, cast, func, or_, select
from sqlalchemy.orm import Session

from ... import models as core
from ..employer import models
from ..employer.admissions import opening_identity
from ..employer.connectors import ConnectorError, SourceContract, safe_origin_url

MAX_CANDIDATES = 50
MAX_ITEMS = 5
DASHBOARD_URL = "https://www.hirewizhq.com/dashboard"
SUBJECT = "Updates to your saved HireWiz roles"
_PLATFORMS = ("greenhouse", "lever", "ashby", "smartrecruiters", "workable", "personio", "pinpoint")
_URL_TEXT = re.compile(r"(?:https?://|www\.|mailto:|javascript:|data:)[^\s]*", re.IGNORECASE)
_HOST = re.compile(r"[a-z0-9][a-z0-9.-]{0,251}\.[a-z]{2,}")


@dataclass(frozen=True, slots=True)
class DigestContent:
    subject: str
    text: str
    sha256: str
    item_count: int

    @property
    def html(self) -> str:
        return "<p>" + html.escape(self.text).replace("\n", "<br>") + "</p>"


class _Label(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.hidden = 0

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag in {"script", "style"}:
            self.hidden += 1
        elif tag in {"br", "p", "div"}:
            self.parts.append(" ")

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style"} and self.hidden:
            self.hidden -= 1

    def handle_data(self, data: str) -> None:
        if not self.hidden:
            self.parts.append(data)


def _label(value: object, limit: int) -> str:
    parser = _Label()
    parser.feed(html.unescape(str(value or "")))
    text = _URL_TEXT.sub("", "".join(parser.parts))
    text = "".join(" " if unicodedata.category(char).startswith("C") else char for char in text)
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _bounded_text(column, maximum: int):
    # Malformed legacy metadata must not become an unbounded selected body.
    return case((func.length(column) <= maximum, column), else_=None)


def _https(value: str | None) -> bool:
    try:
        parsed = urlsplit(value or "")
        return bool(
            parsed.scheme == "https"
            and parsed.hostname
            and not parsed.username
            and not parsed.password
            and parsed.port in {None, 443}
        )
    except ValueError:
        return False


def _current_origin(row) -> bool:
    hosts = row.allowed_hosts
    if (
        row.region not in {"global", "eu"}
        or row.region == "eu"
        and row.platform != "lever"
        or type(hosts) is not list
        or not 1 <= len(hosts) <= 20
        or any(type(host) is not str or _HOST.fullmatch(host) is None for host in hosts)
        or not _https(row.careers_url)
        or not _https(row.verification_url)
        or urlsplit(row.careers_url).hostname not in hosts
        or type(row.board_token) is not str
        or re.fullmatch(r"[A-Za-z0-9_-]{1,120}", row.board_token) is None
        or row.platform == "greenhouse"
        and row.board_token.lower() == "internal"
    ):
        return False
    source = SourceContract(
        row.source_id,
        row.source_employer,
        row.platform,
        row.board_token,
        row.region,
        tuple(hosts),
        row.careers_url,
    )
    try:
        if not row.canonical_url:
            return False
        safe_origin_url(row.canonical_url, source)
    except (ConnectorError, KeyError, ValueError):
        return False
    identity_source = SimpleNamespace(
        platform=row.platform,
        region=row.region,
        board_token=row.board_token,
        employer_key=row.employer_key,
    )
    identity_posting = SimpleNamespace(
        requisition_id=row.requisition_id, external_id=row.external_id
    )
    return (
        type(row.opening_key) is str
        and re.fullmatch(r"[a-f0-9]{64}", row.opening_key) is not None
        and row.opening_key == opening_identity(identity_posting, identity_source)
    )


def build_digest_content(db: Session, owner_id: int, *, now: datetime) -> DigestContent | None:
    """At most five current updates from one owner's obtained/saved-job cohort.

    Freshness is a cached catalogue observation, not real-time employer acceptance.
    A posting has no expiry field: is_open/closed_at and a recent complete healthy
    scan are required; an owner's known passed role deadline excludes that posting.
    Caller retains ownership of the transaction and all send authorization.
    """
    if type(owner_id) is not int or not 0 < owner_id < 2**63:
        raise ValueError("Exact positive owner ID is required")
    if type(now) is not datetime or now.utcoffset() is None:
        raise ValueError("An aware trusted clock value is required")
    now = now.astimezone(UTC)
    core_now = now.replace(tzinfo=None)  # Core Opportunity DateTime columns store UTC without TZ.
    fresh = now - timedelta(hours=24)
    recent = now - timedelta(days=7)
    posting, source = models.EmployerPosting, models.EmployerSource
    delivery, search = models.EmployerJobDelivery, models.EmployerSearch
    opportunity = core.Opportunity
    obtained = (
        select(delivery.id)
        .join(search, search.id == delivery.search_id)
        .where(
            delivery.user_id == owner_id,
            search.user_id == owner_id,
            search.status == "completed",
            delivery.posting_id == posting.id,
            delivery.opening_key == posting.opening_key,
            delivery.created_at <= now,
            search.created_at <= now,
        )
        .exists()
    )
    saved = (
        select(opportunity.id)
        .where(
            opportunity.user_id == owner_id,
            opportunity.archived_at.is_(None),
            opportunity.stage.in_(("saved", "evaluating", "preparing")),
            opportunity.source_url == posting.canonical_url,
            or_(opportunity.deadline_at.is_(None), opportunity.deadline_at >= core_now),
            opportunity.created_at <= core_now,
        )
        .exists()
    )
    expired_owner_role = (
        select(opportunity.id)
        .where(
            opportunity.user_id == owner_id,
            opportunity.source_url == posting.canonical_url,
            opportunity.deadline_at.is_not(None),
            opportunity.deadline_at < core_now,
        )
        .exists()
    )
    current_owner = select(core.User.id).where(core.User.id == owner_id).exists()
    updated = case(
        (posting.source_updated_at > posting.first_seen_at, posting.source_updated_at),
        else_=posting.first_seen_at,
    )
    rows = (
        db.query(
            posting.id,
            posting.external_id,
            posting.requisition_id,
            posting.opening_key,
            func.substr(posting.title, 1, 301).label("title"),
            func.substr(posting.location, 1, 401).label("location"),
            _bounded_text(posting.canonical_url, 2000).label("canonical_url"),
            source.id.label("source_id"),
            func.substr(source.employer, 1, 201).label("source_employer"),
            source.platform,
            source.region,
            source.board_token,
            source.employer_key,
            _bounded_text(source.careers_url, 2000).label("careers_url"),
            _bounded_text(source.verification_url, 2000).label("verification_url"),
            case(
                (func.length(cast(source.allowed_hosts, Text)) <= 8192, source.allowed_hosts),
                else_=None,
            ).label("allowed_hosts"),
        )
        .join(source, source.id == posting.source_id)
        .filter(
            current_owner,
            or_(obtained, saved),
            ~expired_owner_role,
            posting.is_open.is_(True),
            posting.closed_at.is_(None),
            posting.last_checked_at >= fresh,
            posting.last_checked_at <= now,
            posting.first_seen_at <= now,
            or_(posting.source_updated_at.is_(None), posting.source_updated_at <= now),
            or_(posting.publication_at.is_(None), posting.publication_at <= now),
            or_(posting.first_seen_at >= recent, posting.source_updated_at >= recent),
            source.enabled.is_(True),
            source.status == "healthy",
            source.platform.in_(_PLATFORMS),
            source.verified_at.is_not(None),
            source.verified_at <= now,
            func.length(source.verification_note) >= 20,
            source.last_error_code.is_(None),
            source.last_success_at >= fresh,
            source.last_success_at <= now,
        )
        .order_by(updated.desc(), posting.id.asc())
        .limit(MAX_CANDIDATES)
        .all()
    )
    items: list[str] = []
    seen: set[str] = set()
    for row in rows:
        if row.opening_key in seen or not _current_origin(row):
            continue
        title = _label(row.title, 120)
        employer = _label(row.source_employer, 80)
        location = _label(row.location, 80)
        if not title or not employer:
            continue
        seen.add(row.opening_key)
        items.append(
            f"{len(items) + 1}. {title} — {employer}" + (f" · {location}" if location else "")
        )
        if len(items) == MAX_ITEMS:
            break
    if not items:
        return None
    text = (
        "Here are current updates to roles you already saved or discovered in HireWiz.\n\n"
        + "\n".join(items)
        + "\n\nReview current details in your dashboard:\n"
        + DASHBOARD_URL
        + "\n\nAvailability can change after the last employer check."
    )
    rendered = "<p>" + html.escape(text).replace("\n", "<br>") + "</p>"
    raw = json.dumps(
        {"subject": SUBJECT, "text": text, "html": rendered},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode()
    return DigestContent(SUBJECT, text, hashlib.sha256(raw).hexdigest(), len(items))
