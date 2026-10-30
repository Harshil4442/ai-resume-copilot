"""Local guard/unit checks; genuine emulator traffic is a separate execution."""
from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
from datetime import UTC, datetime
from types import SimpleNamespace

import google.auth
import grpc
import httpx
import pytest
import requests
from google.cloud.firestore_admin_v1.types import ListDatabasesRequest
from google.cloud.firestore_v1.types import BeginTransactionResponse, CommitResponse
from pydantic import ValidationError

from app.domains.career.schemas import OpportunityListResponse, OpportunityResponse
from app.schemas import UserProfileResponse
from scripts.stage5_seed import owned_opportunity_ids, synthetic_email
from tests.fixtures.stage5_native import (
    Counters,
    EmulatorNative,
    ThreadSet,
    _request_scope,
    check_body,
    install_egress_guard,
)


def test_transaction_exclusion_is_per_request_thread_not_global():
    active = ThreadSet()
    active.add("main")
    def other():
        assert not active
        active.add("other")
        assert active
        active.discard("other")
        assert not active
    with ThreadPoolExecutor(max_workers=1) as pool:
        pool.submit(other).result(timeout=2)
    assert active
    active.discard("main")
    assert not active


def test_counter_updates_preserve_concurrent_wire_counts():
    counters = Counters()
    def add():
        for _ in range(100):
            counters.add("wire")
    with ThreadPoolExecutor(max_workers=10) as pool:
        list(pool.map(lambda _: add(), range(10)))
    assert counters.snapshot() == {"wire": 1000}


def test_sql_guard_follows_one_request_across_distinct_sync_worker_threads():
    active = ThreadSet()
    token = _request_scope.set(object())
    try:
        opened, ended = copy_context(), copy_context()
        with ThreadPoolExecutor(max_workers=1) as one, ThreadPoolExecutor(max_workers=1) as two:
            first_thread = one.submit(opened.run, lambda: (active.add("sql-connection"), threading.get_ident())[1]).result()
            assert active
            second_thread = two.submit(ended.run, lambda: (active.discard("sql-connection"), threading.get_ident())[1]).result()
            assert first_thread != second_thread and not active
    finally:
        _request_scope.reset(token)


def test_guard_blocks_credentials_external_http_and_tls_native_channel(monkeypatch):
    counters = Counters()
    install_egress_guard(monkeypatch, {59999}, counters)
    for call in (
        lambda: google.auth.default(),
        lambda: grpc.secure_channel("firestore.googleapis.com", None),
        lambda: grpc.insecure_channel("firestore.googleapis.com:443"),
        lambda: requests.get("https://example.invalid"),
        lambda: httpx.get("https://example.invalid", trust_env=False),
        lambda: httpx.get("http://127.0.0.1:59998", trust_env=False),
    ):
        with pytest.raises(AssertionError, match="forbidden"):
            call()
    assert counters.snapshot()["blocked_external_attempts"] == 6


def test_actual_gapic_transport_delegate_counts_and_never_retries():
    calls = []
    def begun(**kwargs):
        calls.append(kwargs)
        return BeginTransactionResponse(transaction=b"owned-emulator-tx")
    native = EmulatorNative(SimpleNamespace(begin_transaction=begun,
        commit=lambda **kwargs: CommitResponse()), Counters())
    reply = native.begin(SimpleNamespace(), retry=None, timeout=2)
    assert native.views and bytes(reply.transaction) == b"owned-emulator-tx"
    native.commit(SimpleNamespace(transaction=reply.transaction), retry=None, timeout=2)
    assert not native.views and calls[0]["retry"] is None
    with pytest.raises(AssertionError, match="retry/timeout"):
        native.begin(SimpleNamespace(), retry=True, timeout=2)
    with pytest.raises(AssertionError, match="retry/timeout"):
        native.begin(SimpleNamespace(), retry=None, timeout=3)


def test_synthetic_metadata_is_explicitly_typed_and_preserves_all_pins():
    native = EmulatorNative(SimpleNamespace(), Counters())
    native.identities = {"projects/hirewiz-local-authority/databases/execution": "a" * 36,
                         "projects/hirewiz-local-authority/databases/protected": "b" * 36}
    reply = native.listed(ListDatabasesRequest(parent="projects/hirewiz-local-authority"),
                          retry=None, timeout=2)
    assert {item.name: item.uid for item in reply.databases} == native.identities
    assert native.counters.snapshot() == {"synthetic_admin_list_databases": 1}


def opportunity_page(user_id, *, offset=0, limit=50):
    rows = [OpportunityResponse(id=identity, resume_id=user_id, latest_match_id=1,
        latest_analysis_run_id=None, title="Backend Engineer", company="Fictional Employer",
        location="Bengaluru", source="manual", source_url=None, job_description="Synthetic role.",
        job_snapshot={}, stage="saved", priority="medium", compensation=None, deadline_at=None,
        next_action=None, notes="", outcome=None, outcome_notes=None, archived_at=None,
        outcome_at=None, created_at=datetime(2026, 1, 1, tzinfo=UTC),
        updated_at=datetime(2026, 1, 1, tzinfo=UTC))
        for identity in owned_opportunity_ids(user_id, offset=offset, limit=limit)]
    return OpportunityListResponse(items=rows, total=500 if user_id == 8 else 10).model_dump(mode="json")


def test_owned_response_contract_rejects_wrong_owner_empty_page_and_fake_wallet():
    ordinary = opportunity_page(1)
    assert "user_id" not in OpportunityResponse.model_fields
    assert check_body("/api/v1/opportunities?limit=50", ordinary, user_id=1, buyer=False, heavy=False)
    assert not check_body("/api/v1/opportunities?limit=50", ordinary, user_id=2, buyer=False, heavy=False)
    assert not check_body("/api/v1/opportunities?limit=50", {"total": 10, "items": []},
                          user_id=1, buyer=False, heavy=False)
    assert not check_body("/api/v1/credits/wallet", {"available_credits": 90},
                          user_id=1, buyer=False, heavy=False)
    for row in ordinary["items"]:
        row["resume_id"] = 2
    assert not check_body("/api/v1/opportunities?limit=50", ordinary, user_id=1, buyer=False, heavy=False)


@pytest.mark.parametrize("user_id,offset,limit", [(i, 0, 50) for i in range(1, 9)] +
    [(8, 50, 200), (8, 490, 50), (8, 500, 50), (10000, 3, 5)])
def test_actual_public_opportunity_schema_has_exact_owned_ids_across_actors_and_pages(user_id, offset, limit):
    body = opportunity_page(user_id, offset=offset, limit=limit)
    path = f"/api/v1/opportunities?limit={limit}&offset={offset}"
    assert check_body(path, body, user_id=user_id, buyer=False, heavy=user_id == 8)
    if body["items"]:
        body["items"][0]["id"] = "capacity-opp-99999" if user_id != 8 else "capacity-opp-1"
        assert not check_body(path, body, user_id=user_id, buyer=False, heavy=user_id == 8)


def test_reserved_synthetic_address_matches_real_profile_schema_without_dns(monkeypatch):
    import socket
    monkeypatch.setattr(socket, "getaddrinfo", lambda *args, **kwargs: pytest.fail("No DNS permitted"))
    fields = {"email": synthetic_email(1), "profile_completeness": 0, "tier": "free", "ai_credits": 0}
    profile = UserProfileResponse(**fields).model_dump(mode="json")
    with pytest.raises(ValidationError):
        UserProfileResponse(**{**fields, "email": "capacity-1@example.test"})
    body = {"schema_version": "owned-bootstrap-v1", "profile": profile,
            "credit_wallet": {"available_credits": 0}, "unavailable": []}
    assert check_body("/api/auth/bootstrap", body, user_id=1, buyer=False, heavy=False)
    assert not check_body("/api/auth/bootstrap", {**body, "profile": None}, user_id=1, buyer=False, heavy=False)
    assert not check_body("/api/auth/bootstrap", {**body, "profile": {**profile, "email": synthetic_email(2)}},
                          user_id=1, buyer=False, heavy=False)
    assert not check_body("/api/auth/bootstrap", {**body, "unavailable": ["profile"]},
                          user_id=1, buyer=False, heavy=False)
