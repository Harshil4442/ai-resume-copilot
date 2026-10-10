"""Saved-search paging against real SQLite and the isolated employer router.

No aggregate application/router import, employer/model traffic or submissions.
"""
from __future__ import annotations

import base64
import copy
import hashlib
import json
import socket
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from backend.app import models as core
from backend.app import security
from backend.app.database import Base
from backend.app.domains.employer import credits, models, search_pages, service
from backend.app.routers.v1 import employer as api
from fastapi import Depends, FastAPI, HTTPException
from fastapi.encoders import jsonable_encoder
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, select, text
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

NOW = datetime(2026, 10, 11, 12, 1, 2, 123456, tzinfo=UTC)


def saved_items(count: int = 69) -> list[dict]:
    values = [{
        "posting": {"id": f"posting_{index:03d}", "opening_key": hashlib.sha256(str(index).encode()).hexdigest(),
                    "title": f"Engineer {index}", "description": "A documented requirement. " * 65},
        "fit": {"mode": "basic", "score": index / 10, "scoring_version": "basic-catalog-overlap-v1"},
        "charged_credits": 2 if index == 0 else 0,
        "legacy_annotation": {"sequence": index, "text": "Preserve all saved fields."},
    } for index in range(count)]
    if count > 1:
        values[1]["preference_evaluation"] = {"eligible": True, "states": {"country": "unknown"}}
    return values


def add_search(db: Session, identity: str, *, owner: int = 1, items=None, created_at=NOW):
    values = saved_items() if items is None else copy.deepcopy(items)
    count = len(values)
    row = models.EmployerSearch(
        id=identity, user_id=owner, resume_id=10 if owner == 1 else 20,
        idempotency_key=identity, input_fingerprint="a" * 64,
        query={"resume_id": 10 if owner == 1 else 20, "role": "Python Engineer", "desired_count": count or 7,
               "preferences": {"version": 1, "country": "IN"}},
        status="completed", desired_count=count or 7, delivered_count=count,
        reserved_credits=count * 2, charged_credits=2 if count else 0,
        refunded_credits=count * 2 - 2 if count else 0,
        scope={"kind": "verified_employer_index", "worldwide_recall_verified": False,
               "candidate_limit": 1000, "candidate_limit_reached": True,
               "price_quote": {"unit_price": 2, "requested_count": count or 7,
                               "maximum_credits": (count or 7) * 2, "pricing_version": "saved-v1",
                               "search_input_fingerprint": "a" * 64}},
        items=values, created_at=created_at,
    )
    db.add(row)
    db.commit()
    return row


@pytest.fixture
def stored(monkeypatch) -> Iterator[Session]:
    monkeypatch.setenv("APP_ENV", "test")

    def prohibited(*args, **kwargs):
        pytest.fail("Saved-result reads must not call credits, create a search or use external transport")

    monkeypatch.setattr(socket.socket, "connect", prohibited)
    monkeypatch.setattr(socket, "create_connection", prohibited)
    monkeypatch.setattr(service, "create_search", prohibited)
    monkeypatch.setattr(credits, "reserve", prohibited)
    monkeypatch.setattr(credits, "settle", prohibited)
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    try:
        with Session(engine, autoflush=False) as db:
            db.add_all([
                core.User(id=1, email="first@example.invalid", job_service_credits=98),
                core.User(id=2, email="second@example.invalid", job_service_credits=17),
                core.Resume(id=10, user_id=1, original_filename="synthetic.pdf", skills=["Python"]),
                core.Resume(id=20, user_id=2, original_filename="synthetic.pdf", skills=["Python"]),
                models.EmployerSource(id="source", employer="Synthetic", platform="greenhouse", board_token="synthetic",
                    careers_url="https://employer.example/careers", verification_url="https://employer.example/careers",
                    verification_note="Synthetic fixture, no provider requests", status="healthy"),
            ])
            db.commit()
            add_search(db, "search_saved")
            add_search(db, "search_other", owner=2, items=saved_items(3))
            db.add(models.EmployerPosting(id="posting_000", source_id="source", external_id="000", title="Engineer",
                employer="Synthetic", description="Synthetic", canonical_url="https://employer.example/jobs/000",
                apply_url="https://employer.example/jobs/000", content_sha256="b" * 64))
            db.add(models.ServiceCreditReservation(id="reservation", user_id=1, operation="employer_search", source_id="search_saved",
                unit_price=2, requested_count=69, reserved_amount=138, committed_amount=2, released_amount=136,
                state="completed", pricing_version="saved-v1"))
            db.add(models.ServiceCreditEvent(id="event", user_id=1, event_type="commit", amount=-2, balance_after=98,
                idempotency_key="saved-event", source_type="employer_search", source_id="search_saved", reason="Synthetic paid result"))
            db.add(models.EmployerJobDelivery(id="delivery", user_id=1, posting_id="posting_000", opening_key="c" * 64,
                search_id="search_saved", charged_credits=2))
            db.add(models.SealedApplicationArtifact(id="artifact", user_id=1, resume_id=10, sha256="d" * 64,
                filename="synthetic.pdf", media_type="application/pdf", size_bytes=9, content=b"synthetic"))
            db.add(models.EmployerApplication(id="application", user_id=1, posting_id="posting_000", idempotency_key="application-key",
                input_fingerprint="e" * 64, resume_id=10, resume_choice="original", artifact_id="artifact", form={"fields": []},
                answers={}, consents={}, package_digest="f" * 64, job_content_sha256="b" * 64, credit_cost=3))
            db.commit()
            yield db
    finally:
        engine.dispose()


def money_and_applications(db: Session):
    with db.no_autoflush:
        return {
            "owners": list(db.execute(select(core.User.id, core.User.job_service_credits)).all()),
            **{model.__tablename__: list(db.execute(select(model.__table__)).all()) for model in (
                models.ServiceCreditEvent, models.ServiceCreditReservation,
                models.EmployerJobDelivery, models.EmployerApplication, models.SealedApplicationArtifact,
            )},
        }


@pytest.fixture
def client(stored) -> Iterator[TestClient]:
    app = FastAPI()
    app.include_router(api.router, prefix="/api/v1")

    def local_db():
        yield stored

    def local_authenticated_user(token: str = Depends(api.oauth2_scheme)):
        # Real bearer validation; candidate lifetime authority is outside this isolated router test.
        owner = security.decode_access_token_user_id(token)
        with stored.no_autoflush:
            user = stored.get(core.User, owner)
        if user is None:
            raise HTTPException(401, "Not authenticated")
        return user

    app.dependency_overrides[api.get_db] = local_db
    app.dependency_overrides[api.get_current_user] = local_authenticated_user
    with TestClient(app) as browser:
        yield browser


def auth(owner=1):
    return {"Authorization": "Bearer " + security.create_access_token(subject=str(owner))}


def test_summary_projection_excludes_items_and_does_not_materialize_search_orm(stored):
    statements = []

    def record(conn, cursor, statement, params, context, many):
        statements.append(statement)

    stored.expunge_all()
    event.listen(stored.bind, "before_cursor_execute", record)
    try:
        page = search_pages.list_summaries(stored, 1)
    finally:
        event.remove(stored.bind, "before_cursor_execute", record)
    sql = [value for value in statements if "FROM employer_searches" in value]
    assert len(sql) == 1 and "employer_searches.items" not in sql[0]
    assert "LIMIT" in sql[0] and "employer_searches.user_id" in sql[0]
    assert not any(isinstance(value, models.EmployerSearch) for value in stored.identity_map.values())
    assert [value.id for value in page.items] == ["search_saved"]
    assert "items" not in page.items[0].model_dump()
    old = service.get_search(stored, 1, "search_saved")
    assert page.items[0].model_dump() == {key: value for key, value in old.items() if key != "items"}


def test_summary_keyset_traverses_66_rows_with_tied_dates_and_large_valid_bodies(stored):
    body = saved_items()
    assert len(json.dumps(body).encode()) > 64 * 1024
    for index in range(66):
        add_search(stored, f"history_{index:03d}", items=body, created_at=NOW + timedelta(seconds=1))
    first = search_pages.list_summaries(stored, 1, limit=20)
    assert [item.id for item in first.items] == [f"history_{index:03d}" for index in range(65, 45, -1)]
    add_search(stored, "newer_after_first_page", items=[], created_at=NOW + timedelta(days=1))
    seen = [item.id for item in first.items]
    cursor = first.next_cursor
    while cursor:
        page = search_pages.list_summaries(stored, 1, limit=7, cursor=cursor)
        seen.extend(item.id for item in page.items)
        cursor = page.next_cursor
    assert seen == [f"history_{index:03d}" for index in range(65, -1, -1)] + ["search_saved"]
    assert len(seen) == len(set(seen)) == 67


def test_saved_pages_traverse_exact_original_order_metadata_and_financial_state(stored, client, monkeypatch):
    original = jsonable_encoder(service.get_search(stored, 1, "search_saved"))
    before = money_and_applications(stored)
    monkeypatch.setenv("EMPLOYER_SEARCH_CREDITS_PER_JOB", "19")
    seen, cursor, versions = [], None, set()
    while True:
        params = {"limit": 10}
        if cursor:
            params["cursor"] = cursor
        response = client.get("/api/v1/employer-jobs/searches/search_saved/results", params=params, headers=auth())
        assert response.status_code == 200
        assert response.headers["cache-control"] == "private, no-store"
        page = response.json()
        assert page["start_index"] == len(seen) and page["total_items"] == 69
        assert page["search"] == {key: value for key, value in original.items() if key != "items"}
        assert len(page["items"]) <= 10
        versions.add(page["snapshot_version"])
        seen.extend(page["items"])
        cursor = page["next_cursor"]
        if cursor is None:
            break
    assert seen == original["items"] and len(versions) == 1
    assert money_and_applications(stored) == before
    assert jsonable_encoder(service.get_search(stored, 1, "search_saved")) == original
    assert client.get("/api/v1/employer-jobs/searches/search_saved", headers=auth()).json() == original
    assert client.get("/api/v1/employer-jobs/searches", headers=auth()).json() == {"items": [original]}


def test_empty_saved_search_and_last_page_have_no_continuation_or_charge(stored):
    add_search(stored, "empty_saved", items=[])
    before = money_and_applications(stored)
    page = search_pages.saved_results(stored, 1, "empty_saved")
    assert page.items == [] and page.total_items == 0 and page.start_index == 0 and page.next_cursor is None
    assert page.search.desired_count == 7 and page.search.scope["price_quote"]["maximum_credits"] == 14
    assert money_and_applications(stored) == before


@pytest.mark.parametrize("kind", ["summaries", "results"])
def test_read_only_calls_do_not_autoflush_dirty_caller_state(stored, kind):
    row = stored.get(models.EmployerSearch, "search_saved")
    owner = stored.get(core.User, 1)
    original = copy.deepcopy(row.items)
    row.items = []
    owner.job_service_credits = 1
    stored.autoflush = True
    statements = []

    def record(conn, cursor, statement, params, context, many):
        statements.append(statement)

    event.listen(stored.bind, "before_cursor_execute", record)
    try:
        if kind == "summaries":
            page = search_pages.list_summaries(stored, 1)
            assert page.items[0].delivered_count == len(original)
        else:
            page = search_pages.saved_results(stored, 1, "search_saved")
            assert page.total_items == len(original)
    finally:
        event.remove(stored.bind, "before_cursor_execute", record)
    assert not any(value.lstrip().upper().startswith(("UPDATE", "INSERT", "DELETE")) for value in statements)
    assert row in stored.dirty and owner in stored.dirty
    assert row.items == [] and owner.job_service_credits == 1
    stored.rollback()
    assert stored.get(core.User, 1).job_service_credits == 98


def test_cursors_are_binary_signed_and_cross_owner_kind_search_scoped(stored, client):
    add_search(stored, "search_saved_older", items=saved_items(2), created_at=NOW - timedelta(seconds=1))
    summary = search_pages.list_summaries(stored, 1, limit=1).next_cursor
    result = search_pages.saved_results(stored, 1, "search_saved", limit=1).next_cursor
    assert summary and result
    assert b"{" not in base64.urlsafe_b64decode(summary + "=" * (-len(summary) % 4))[:10]
    misuse = [
        ("/search-summaries", summary, 2), ("/search-summaries", result, 1),
        ("/searches/search_saved/results", result, 2),
        ("/searches/search_other/results", result, 1),
        ("/searches/search_saved/results", summary, 1),
    ]
    before = money_and_applications(stored)
    for path, cursor, owner in misuse:
        response = client.get("/api/v1/employer-jobs" + path, params={"cursor": cursor}, headers=auth(owner))
        assert response.status_code == 400 and response.json()["detail"]["code"] == "invalid_search_cursor"
        assert response.headers["cache-control"] == "private, no-store"
        assert "Engineer" not in response.text and "saved-v1" not in response.text
    assert money_and_applications(stored) == before


@pytest.mark.parametrize("damage", ["forged", "truncated", "padding", "empty", "unicode", "long", "version", "offset_zero", "offset_end", "trailing", "identity_utf8"])
def test_result_cursor_strict_validation(stored, damage):
    first = search_pages.saved_results(stored, 1, "search_saved", limit=1)
    cursor = first.next_cursor
    assert cursor
    if damage == "forged":
        cursor = ("A" if cursor[0] != "A" else "B") + cursor[1:]
    elif damage == "truncated":
        cursor = cursor[:-9]
    elif damage == "padding":
        cursor += "="
    elif damage == "empty":
        cursor = ""
    elif damage == "unicode":
        cursor = "résumé"
    elif damage == "long":
        cursor = "a" * 513
    else:
        identity = b"search_saved" if damage != "identity_utf8" else b"\xff"
        version = 2 if damage == "version" else 1
        offset = 0 if damage == "offset_zero" else 69 if damage == "offset_end" else 1
        payload = search_pages._HEAD.pack(version, 2, 1) + search_pages._RESULT.pack(offset, len(identity)) + identity + bytes.fromhex(first.snapshot_version.split(":")[1])
        if damage == "trailing":
            payload += b"unexpected"
        cursor = search_pages._seal(payload)
    with pytest.raises(HTTPException) as refused:
        search_pages.saved_results(stored, 1, "search_saved", cursor=cursor)
    assert refused.value.status_code == 400 and refused.value.detail["code"] == "invalid_search_cursor"


@pytest.mark.parametrize("damage", ["date", "length", "version", "identity_utf8"])
def test_summary_cursor_rejects_signed_but_invalid_boundary(stored, damage):
    identity = b"search_saved" if damage != "identity_utf8" else b"\xff"
    date = 2**63 - 1 if damage == "date" else search_pages._micros(NOW)
    version = 2 if damage == "version" else 1
    payload = search_pages._HEAD.pack(version, 1, 1) + search_pages._SUMMARY.pack(date, len(identity)) + identity
    if damage == "length":
        payload += b"extra"
    with pytest.raises(HTTPException) as refused:
        search_pages.list_summaries(stored, 1, cursor=search_pages._seal(payload))
    assert refused.value.status_code == 400


@pytest.mark.parametrize("edit", ["items", "totals", "quote", "query", "status"])
def test_changed_saved_snapshot_is_409_and_first_page_can_be_reloaded(stored, client, edit):
    first = search_pages.saved_results(stored, 1, "search_saved", limit=10)
    row = stored.get(models.EmployerSearch, "search_saved")
    if edit == "items":
        changed = copy.deepcopy(row.items)
        changed[0]["posting"]["title"] = "Edited original snapshot"
        row.items = changed
    elif edit == "totals":
        row.charged_credits = 3
    elif edit == "quote":
        changed = copy.deepcopy(row.scope)
        changed["price_quote"]["unit_price"] = 4
        row.scope = changed
    elif edit == "query":
        row.query = {**row.query, "role": "Different role"}
    else:
        row.status = "reviewed"
    stored.commit()
    before = money_and_applications(stored)
    response = client.get("/api/v1/employer-jobs/searches/search_saved/results", params={"cursor": first.next_cursor}, headers=auth())
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "saved_search_snapshot_changed"
    assert response.headers["cache-control"] == "private, no-store"
    assert "Edited original" not in response.text
    reload = search_pages.saved_results(stored, 1, "search_saved")
    assert reload.snapshot_version != first.snapshot_version and reload.start_index == 0
    assert money_and_applications(stored) == before


def test_route_auth_owner_404_and_typed_openapi(stored, client):
    base = "/api/v1/employer-jobs"
    unauthenticated = client.get(base + "/search-summaries")
    assert unauthenticated.status_code == 401 and unauthenticated.headers["cache-control"] == "private, no-store"
    assert client.get(base + "/search-summaries", headers={"Authorization": "Bearer invalid"}).status_code == 401
    summaries = client.get(base + "/search-summaries", headers=auth(2))
    assert summaries.status_code == 200 and [item["id"] for item in summaries.json()["items"]] == ["search_other"]
    assert summaries.headers["cache-control"] == "private, no-store"
    refused = client.get(base + "/searches/search_saved/results", headers=auth(2))
    missing = client.get(base + "/searches/not_found/results", headers=auth(2))
    assert refused.status_code == missing.status_code == 404 and refused.json() == missing.json()
    assert refused.headers["cache-control"] == "private, no-store"
    schema = client.get("/openapi.json").json()
    for path, typename in [(base + "/search-summaries", "SearchSummaryPage"), (base + "/searches/{identity}/results", "SavedSearchResultsPage")]:
        assert schema["paths"][path]["get"]["responses"]["200"]["content"]["application/json"]["schema"] == {"$ref": f"#/components/schemas/{typename}"}
    assert "items" not in schema["components"]["schemas"]["SearchSummary"]["properties"]


@pytest.mark.parametrize("limit", [0, 21, True])
def test_service_page_limit_is_finite_even_without_transport_validation(stored, limit):
    for function, args in [(search_pages.list_summaries, (stored, 1)), (search_pages.saved_results, (stored, 1, "search_saved"))]:
        with pytest.raises(HTTPException) as refused:
            function(*args, limit=limit)
        assert refused.value.status_code == 400


def test_route_limit_and_cursor_length_bounds(client):
    base = "/api/v1/employer-jobs"
    for path in [base + "/search-summaries", base + "/searches/search_saved/results"]:
        for params in [{"limit": 0}, {"limit": 21}, {"cursor": "a" * 513}, {"cursor": ""}]:
            invalid = client.get(path, params=params, headers=auth())
            assert invalid.status_code == 422 and invalid.headers["cache-control"] == "private, no-store"


def test_saved_totals_are_preserved_instead_of_rederived_from_array_length(stored):
    row = stored.get(models.EmployerSearch, "search_saved")
    row.delivered_count = 68
    stored.commit()
    before = money_and_applications(stored)
    page = search_pages.saved_results(stored, 1, "search_saved")
    assert page.total_items == 69 and page.search.delivered_count == 68
    assert page.search.scope["price_quote"]["requested_count"] == 69
    assert money_and_applications(stored) == before


@pytest.mark.parametrize("damage", ["not_array", "bad_posting", "bool_charge"])
def test_damaged_snapshot_read_is_fixed_409_without_raw_data_or_financial_coercion(stored, client, damage):
    row = stored.get(models.EmployerSearch, "search_saved")
    changed = copy.deepcopy(row.items)
    if damage == "not_array":
        changed = {"private": "Do not reflect this saved data"}
    elif damage == "bad_posting":
        changed[0]["posting"] = "Do not reflect this saved data"
    else:
        changed[0]["charged_credits"] = True
    row.items = changed
    stored.commit()
    before = money_and_applications(stored)
    response = client.get("/api/v1/employer-jobs/searches/search_saved/results", headers=auth())
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "saved_search_snapshot_unavailable"
    assert "Do not reflect" not in response.text and "Engineer" not in response.text
    assert money_and_applications(stored) == before


def test_signing_secret_rotation_invalidates_existing_saved_result_cursor(stored, monkeypatch):
    cursor = search_pages.saved_results(stored, 1, "search_saved", limit=1).next_cursor
    monkeypatch.setattr(security, "JWT_SECRET", "different-synthetic-test-secret-20261011")
    with pytest.raises(HTTPException) as refused:
        search_pages.saved_results(stored, 1, "search_saved", cursor=cursor)
    assert refused.value.status_code == 400


PAGING_PATHS = [
    "/api/v1/employer-jobs/search-summaries",
    "/api/v1/employer-jobs/searches/search_saved/results",
]
UNAVAILABLE_BODY = {"detail": {
    "code": "saved_search_snapshot_unavailable", "message": "This saved result snapshot needs review.",
}}
INVALID_REQUEST_BODY = {"detail": [{
    "loc": ["request"],
    "msg": "Invalid saved-search page request. Check the page size, cursor and saved search.",
    "type": "invalid_search_page_request",
}]}


@pytest.mark.parametrize("field", ["query", "scope"])
@pytest.mark.parametrize("damage", ["string", "array", "null"])
def test_malformed_persisted_metadata_is_static_private_409(stored, client, field, damage):
    sentinel = "synthetic-private-metadata-sentinel"
    row = stored.get(models.EmployerSearch, "search_saved")
    setattr(row, field, {"string": sentinel, "array": [sentinel], "null": None}[damage])
    stored.commit()
    before = money_and_applications(stored)
    for path in PAGING_PATHS:
        response = client.get(path, headers=auth())
        assert response.status_code == 409 and response.json() == UNAVAILABLE_BODY
        assert response.headers["cache-control"] == "private, no-store"
        assert sentinel not in response.text and "Engineer" not in response.text
    assert money_and_applications(stored) == before


def test_valid_nested_metadata_and_quotes_remain_exact_on_both_routes(stored, client):
    row = stored.get(models.EmployerSearch, "search_saved")
    query = copy.deepcopy(row.query)
    query["role"] = {"legacy": ["工程師", {"values": [None, True, 3.5]}]}
    query["unrecognized"] = {"keep": "exact"}
    scope = copy.deepcopy(row.scope)
    scope["price_quote"]["unit_price"] = {"legacy": ["nested quote", 2]}
    scope["legacy"] = [False, None, {"unicode": "résumé"}]
    row.query, row.scope = query, scope
    stored.commit()
    before = money_and_applications(stored)
    for path in PAGING_PATHS:
        response = client.get(path, headers=auth())
        assert response.status_code == 200 and response.headers["cache-control"] == "private, no-store"
        metadata = response.json()["items"][0] if path == PAGING_PATHS[0] else response.json()["search"]
        assert metadata["query"] == query and metadata["scope"] == scope
        assert metadata["charged_credits"] == 2 and metadata["delivered_count"] == 69
    assert money_and_applications(stored) == before


@pytest.mark.parametrize("field", ["query", "scope", "items"])
@pytest.mark.parametrize("damage", ["nan", "infinity", "surrogate"])
def test_persisted_unserializable_json_is_private_409_without_touching_finances(stored, client, field, damage):
    value = {"nan": float("nan"), "infinity": float("inf"), "surrogate": "\ud800"}[damage]
    row = stored.get(models.EmployerSearch, "search_saved")
    changed = copy.deepcopy(getattr(row, field))
    if field == "items":
        changed[0]["posting"]["synthetic-private-serialization-sentinel"] = value
    else:
        changed["synthetic-private-serialization-sentinel"] = value
    setattr(row, field, changed)
    stored.commit()
    before = money_and_applications(stored)
    for path in PAGING_PATHS:
        response = client.get(path, headers=auth())
        if field == "items" and path == PAGING_PATHS[0]:
            # Summary selection must not load or serialize the saved result array.
            assert response.status_code == 200 and "items" not in response.json()["items"][0]
        else:
            assert response.status_code == 409 and response.json() == UNAVAILABLE_BODY
            assert "synthetic-private-serialization-sentinel" not in response.text
        assert response.headers["cache-control"] == "private, no-store"
    assert money_and_applications(stored) == before


@pytest.mark.parametrize("field", ["query", "scope", "items"])
def test_invalid_saved_json_decoder_failure_is_static_private_409(stored, client, field):
    # This finite field list is test-owned SQL; corrupt actual persisted JSON bytes,
    # rather than mocking an exception before the SQLAlchemy JSON decoder runs.
    assert field in {"query", "scope", "items"}
    stored.execute(text(f"UPDATE employer_searches SET {field}=:value WHERE id=:identity"), {
        "value": '{"synthetic-private-json-decoder-sentinel":', "identity": "search_saved",
    })
    stored.commit()
    before = money_and_applications(stored)
    for path in PAGING_PATHS:
        response = client.get(path, headers=auth())
        if field == "items" and path == PAGING_PATHS[0]:
            assert response.status_code == 200 and "items" not in response.json()["items"][0]
        else:
            assert response.status_code == 409 and response.json() == UNAVAILABLE_BODY
            assert "synthetic-private-json-decoder-sentinel" not in response.text
        assert response.headers["cache-control"] == "private, no-store"
    assert money_and_applications(stored) == before


@pytest.mark.parametrize("field", ["cursor", "limit", "identity"])
@pytest.mark.parametrize("size", [513, 4096, 32768])
def test_request_validation_redacts_raw_query_and_path_at_multiple_sizes(stored, client, field, size):
    sentinel = "synthetic-raw-validation-sentinel-"
    value = sentinel + "a" * size
    before = money_and_applications(stored)
    paths = PAGING_PATHS if field != "identity" else [f"/api/v1/employer-jobs/searches/{value}/results"]
    bodies = []
    for path in paths:
        response = client.get(path, params={field: value} if field != "identity" else {}, headers=auth())
        assert response.status_code == 422 and response.json() == INVALID_REQUEST_BODY
        assert response.headers["cache-control"] == "private, no-store"
        assert sentinel not in response.text and value not in response.text
        assert len(response.content) < 256
        bodies.append(response.content)
    assert len(set(bodies)) == 1 and money_and_applications(stored) == before


def test_numeric_and_empty_request_validation_uses_same_finite_envelope(stored, client):
    before = money_and_applications(stored)
    for path in PAGING_PATHS:
        for params in [{"limit": "0"}, {"limit": "21"}, {"limit": "9" * 512}, {"cursor": ""}]:
            response = client.get(path, params=params, headers=auth())
            assert response.status_code == 422 and response.json() == INVALID_REQUEST_BODY
            assert response.headers["cache-control"] == "private, no-store"
    assert money_and_applications(stored) == before
