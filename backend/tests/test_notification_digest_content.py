"""Local SQL fixtures only: no employer refresh, model call or message delivery."""

from __future__ import annotations

import hashlib
import json
import socket
from datetime import UTC, datetime, timedelta, timezone

import pytest
from backend.app import models as core
from backend.app.database import Base
from backend.app.domains.employer import connectors, models
from backend.app.domains.employer.admissions import opening_identity
from backend.app.domains.notifications.digest_content import DASHBOARD_URL, build_digest_content
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

NOW = datetime(2026, 11, 5, 12, tzinfo=UTC)


@pytest.fixture
def db(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Digest content must not call a provider or refresh jobs")

    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(socket, "getaddrinfo", forbidden)
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket.socket, "connect_ex", forbidden)
    monkeypatch.setattr(connectors, "fetch_postings", forbidden)
    monkeypatch.setattr(connectors, "fetch_source_scan", forbidden)
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False)
    with factory() as session:
        session.add_all(
            [
                core.User(id=1, email="private-owner@example.invalid"),
                core.User(id=2, email="other-owner@example.invalid"),
            ]
        )
        session.add_all(
            [
                core.Resume(id=1, user_id=1, raw_text="SENSITIVE_RESUME"),
                core.Resume(id=2, user_id=2, raw_text="OTHER_RESUME"),
            ]
        )
        session.add_all(
            [
                models.EmployerSearch(
                    id=f"search{owner}",
                    user_id=owner,
                    resume_id=owner,
                    idempotency_key=f"owned-search-{owner}",
                    input_fingerprint="a" * 64,
                    _legacy_query={"role": "Engineer", "private_answer": "SENSITIVE_SEARCH"},
                    _legacy_items=[],
                    scope={},
                    status="completed",
                    desired_count=5,
                    created_at=NOW - timedelta(days=3),
                )
                for owner in [1, 2]
            ]
        )
        session.commit()
        yield session
    engine.dispose()


def source(db, identity="source", **changes):
    values = dict(
        id=identity,
        employer="Acme & Co",
        employer_key="acme",
        platform="greenhouse",
        board_token="acme",
        region="global",
        careers_url="https://acme.example/careers",
        allowed_hosts=["acme.example"],
        verification_url="https://acme.example/careers",
        verification_note="Operator reviewed the employer's own careers portal.",
        verified_at=NOW - timedelta(days=30),
        enabled=True,
        status="healthy",
        last_success_at=NOW - timedelta(hours=1),
        last_error_code=None,
    )
    values.update(changes)
    row = models.EmployerSource(**values)
    db.add(row)
    db.flush()
    return row


def posting(db, owner=1, identity="job_01", origin=None, obtained=True, **changes):
    origin = origin or db.get(models.EmployerSource, "source") or source(db)
    values = dict(
        id=identity,
        source_id=origin.id,
        external_id=identity,
        requisition_id=identity,
        title=f"Engineer {identity}",
        employer=origin.employer,
        location="Bengaluru",
        description="SENSITIVE_JOB_BODY https://never-include.invalid/path",
        skills=["Python"],
        canonical_url=f"https://job-boards.greenhouse.io/acme/jobs/{identity}",
        apply_url="https://never-include.invalid/apply",
        content_sha256="b" * 64,
        first_seen_at=NOW - timedelta(days=2),
        last_checked_at=NOW - timedelta(hours=1),
        publication_at=NOW - timedelta(days=2),
        is_open=True,
        closed_at=None,
    )
    values.update(changes)
    row = models.EmployerPosting(**values)
    row.opening_key = opening_identity(row, origin)
    db.add(row)
    db.flush()
    if obtained:
        db.add(
            models.EmployerJobDelivery(
                id="delivery_" + identity,
                user_id=owner,
                posting_id=row.id,
                search_id=f"search{owner}",
                opening_key=row.opening_key,
                charged_credits=0,
                created_at=NOW - timedelta(days=1),
            )
        )
    db.commit()
    return row


def save_role(db, post, owner=1, **changes):
    values = dict(
        id="opp_" + post.id,
        user_id=owner,
        title="Saved role",
        source_url=post.canonical_url,
        stage="saved",
        archived_at=None,
        created_at=NOW - timedelta(days=1),
        updated_at=NOW,
    )
    values.update(changes)
    db.add(core.Opportunity(**values))
    db.commit()


def test_owned_obtained_roles_render_only_bounded_metadata_with_fixed_destination(db):
    posting(db)
    statements = []

    def observe(_conn, _cursor, statement, _params, _context, _many):
        statements.append(statement)

    event.listen(db.get_bind(), "before_cursor_execute", observe)
    try:
        content = build_digest_content(db, 1, now=NOW)
    finally:
        event.remove(db.get_bind(), "before_cursor_execute", observe)
    assert content is not None and content.item_count == 1
    assert "Engineer job_01" in content.text and "already saved or discovered" in content.text
    assert DASHBOARD_URL in content.text
    assert "never-include" not in content.text and "SENSITIVE" not in content.text
    assert "private-owner" not in content.text and "greenhouse.io" not in content.text
    assert len(statements) == 1
    projection = statements[0].split("\nFROM", 1)[0].lower()
    for forbidden in [
        "description",
        "raw_text",
        "source_document",
        "answers",
        "password",
        "legacy_query",
    ]:
        assert forbidden not in projection
    expected = json.dumps(
        {"subject": content.subject, "text": content.text, "html": content.html},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode()
    assert content.sha256 == hashlib.sha256(expected).hexdigest()
    assert content == build_digest_content(db, 1, now=NOW)


def test_owner_saved_opportunity_exact_origin_is_relevant_without_delivery(db):
    row = posting(db, obtained=False)
    save_role(db, row)
    assert build_digest_content(db, 1, now=NOW).item_count == 1
    assert build_digest_content(db, 2, now=NOW) is None


@pytest.mark.parametrize(
    "kind",
    ["foreign_delivery", "foreign_search", "deleted_search", "pending_search", "deleted_owner"],
)
def test_cross_owner_or_missing_relevance_is_not_borrowed(db, kind):
    row = posting(db)
    delivery = db.query(models.EmployerJobDelivery).filter_by(posting_id=row.id).one()
    if kind == "foreign_delivery":
        delivery.user_id = 2
    elif kind == "foreign_search":
        delivery.search_id = "search2"
    elif kind == "deleted_search":
        db.query(models.EmployerSearch).filter_by(id="search1").delete()
    elif kind == "pending_search":
        db.query(models.EmployerSearch).filter_by(id="search1").update({"status": "preparing"})
    else:
        db.query(core.User).filter_by(id=1).delete()
    db.commit()
    assert build_digest_content(db, 1, now=NOW) is None


@pytest.mark.parametrize(
    "changes",
    [
        {"is_open": False},
        {"closed_at": NOW - timedelta(minutes=1)},
        {"last_checked_at": NOW - timedelta(hours=24, microseconds=1)},
        {"last_checked_at": NOW + timedelta(seconds=1)},
        {"first_seen_at": NOW + timedelta(seconds=1)},
        {"publication_at": NOW + timedelta(seconds=1)},
        {"source_updated_at": NOW + timedelta(seconds=1)},
        {"first_seen_at": NOW - timedelta(days=7, microseconds=1)},
        {"canonical_url": "https://job-boards.greenhouse.io/another-tenant/jobs/job_01"},
        {"canonical_url": "https://unreviewed.invalid/jobs/job_01"},
    ],
)
def test_stale_closed_future_or_untrusted_postings_are_excluded(db, changes):
    posting(db, **changes)
    assert build_digest_content(db, 1, now=NOW) is None


@pytest.mark.parametrize(
    "changes",
    [
        {"enabled": False},
        {"status": "unavailable"},
        {"last_error_code": "unavailable"},
        {"last_success_at": NOW - timedelta(hours=24, microseconds=1)},
        {"last_success_at": NOW + timedelta(seconds=1)},
        {"verified_at": NOW + timedelta(seconds=1)},
        {"verification_note": "unreviewed"},
        {"platform": "job_board"},
        {"region": "unknown"},
        {"careers_url": "http://acme.example/careers"},
        {"allowed_hosts": ["other.example"]},
    ],
)
def test_only_current_healthy_operator_reviewed_origins_are_used(db, changes):
    posting(db, origin=source(db, **changes))
    assert build_digest_content(db, 1, now=NOW) is None


def test_source_update_window_includes_existing_role_without_recrawling(db):
    posting(db, first_seen_at=NOW - timedelta(days=90), source_updated_at=NOW - timedelta(days=1))
    assert build_digest_content(db, 1, now=NOW).item_count == 1


@pytest.mark.parametrize(
    "changes",
    [
        {"stage": "archived"},
        {"archived_at": NOW - timedelta(days=1)},
        {"deadline_at": NOW - timedelta(seconds=1)},
    ],
)
def test_inactive_or_expired_saved_roles_do_not_establish_relevance(db, changes):
    row = posting(db, obtained=False)
    save_role(db, row, **changes)
    assert build_digest_content(db, 1, now=NOW) is None


def test_known_passed_deadline_excludes_an_obtained_role_too(db):
    row = posting(db)
    save_role(db, row, deadline_at=NOW - timedelta(seconds=1))
    assert build_digest_content(db, 1, now=NOW) is None


def test_distinct_openings_have_stable_recent_then_identity_order_and_maximum_five(db):
    origin = source(db)
    for index in reversed(range(8)):
        posting(db, identity=f"job_{index:02d}", origin=origin)
    duplicate = posting(
        db, identity="job_duplicate", origin=origin, obtained=False, requisition_id="job_00"
    )
    save_role(db, duplicate)
    assert duplicate.opening_key == db.get(models.EmployerPosting, "job_00").opening_key
    content = build_digest_content(db, 1, now=NOW)
    assert content is not None and content.item_count == 5
    assert [f"Engineer job_{index:02d}" in content.text for index in range(5)] == [True] * 5
    assert "job_05" not in content.text and "job_duplicate" not in content.text
    assert content == build_digest_content(db, 1, now=NOW)


def test_only_fifty_metadata_candidates_are_examined_without_automatic_traversal(db):
    origin = source(db)
    for index in range(51):
        row = posting(
            db,
            identity=f"job_{index:02d}",
            origin=origin,
            obtained=False,
            requisition_id="same-opening" if index < 50 else "another-opening",
        )
        save_role(db, row)
    content = build_digest_content(db, 1, now=NOW)
    assert content is not None and content.item_count == 1 and "job_50" not in content.text


def test_bad_or_unmapped_opening_key_does_not_become_a_duplicate_fallback(db):
    row = posting(db)
    row.opening_key = "f" * 64
    db.commit()
    assert build_digest_content(db, 1, now=NOW) is None


def test_labels_are_plain_bounded_and_html_is_escaped_without_external_links(db):
    posting(
        db,
        title="<script>steal()</script><b>Engineer &amp; Builder</b> https://evil.invalid/a\r\n"
        + "X" * 300,
        location='<a href="https://evil.invalid">Remote & Anywhere</a>',
    )
    content = build_digest_content(db, 1, now=NOW)
    assert content is not None
    assert "steal()" not in content.text and "evil.invalid" not in content.text
    assert "<script>" not in content.html and "<b>" not in content.html
    assert "Acme &amp; Co" in content.html and "Remote &amp; Anywhere" in content.html
    assert "…" in content.text and len(content.text) < 1000
    assert content.text.count("https://") == 1 and DASHBOARD_URL in content.text


def test_zero_item_skip_and_invalid_owner_clock_are_explicit(db):
    assert build_digest_content(db, 1, now=NOW) is None
    with pytest.raises(ValueError):
        build_digest_content(db, True, now=NOW)
    with pytest.raises(ValueError):
        build_digest_content(db, 1, now=NOW.replace(tzinfo=None))


def test_deleted_posting_is_not_revived_from_saved_delivery_or_opportunity(db):
    row = posting(db)
    save_role(db, row)
    db.delete(row)
    db.commit()
    assert build_digest_content(db, 1, now=NOW) is None


def test_content_hash_changes_with_visible_update_but_not_scan_timestamp(db):
    row = posting(db)
    first = build_digest_content(db, 1, now=NOW)
    row.last_checked_at = NOW - timedelta(minutes=1)
    db.commit()
    assert build_digest_content(db, 1, now=NOW) == first
    row.title = "Updated visible title"
    db.commit()
    changed = build_digest_content(db, 1, now=NOW)
    assert changed is not None and first is not None and changed.sha256 != first.sha256


def test_reused_posting_id_does_not_replace_the_owner_obtained_opening(db):
    row = posting(db)
    row.requisition_id = "a-different-opening"
    row.opening_key = opening_identity(row, db.get(models.EmployerSource, row.source_id))
    db.commit()
    assert build_digest_content(db, 1, now=NOW) is None


def test_missing_delivery_opening_identity_does_not_establish_relevance(db):
    row = posting(db)
    db.query(models.EmployerJobDelivery).filter_by(posting_id=row.id).update({"opening_key": None})
    db.commit()
    assert build_digest_content(db, 1, now=NOW) is None


def test_core_deadline_predicates_bind_naive_utc_independently_of_postgres_session_zone(db):
    from sqlalchemy.dialects import postgresql

    parameters = []

    def observe(state):
        parameters.append(state.statement.compile(dialect=postgresql.dialect()).params)

    event.listen(db, "do_orm_execute", observe)
    try:
        clock = NOW.astimezone(timezone(timedelta(hours=5, minutes=30)))
        assert build_digest_content(db, 1, now=clock) is None
    finally:
        event.remove(db, "do_orm_execute", observe)
    assert len(parameters) == 1
    deadlines = [value for key, value in parameters[0].items() if key.startswith("deadline_at")]
    assert len(deadlines) == 2 and all(value == NOW.replace(tzinfo=None) for value in deadlines)
    assert all(value.tzinfo is None for value in deadlines)
