"""Local guard/unit checks; genuine emulator traffic is a separate execution."""
from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
from types import SimpleNamespace

import google.auth
import grpc
import httpx
import pytest
import requests
from google.cloud.firestore_admin_v1.types import ListDatabasesRequest
from google.cloud.firestore_v1.types import BeginTransactionResponse, CommitResponse

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


def test_owned_response_contract_rejects_wrong_owner_empty_page_and_fake_wallet():
    ordinary = {"total": 10, "items": [{"user_id": 1}] * 10}
    assert check_body("/api/v1/opportunities?limit=50", ordinary, user_id=1, buyer=False, heavy=False)
    assert not check_body("/api/v1/opportunities?limit=50", ordinary, user_id=2, buyer=False, heavy=False)
    assert not check_body("/api/v1/opportunities?limit=50", {"total": 10, "items": []},
                          user_id=1, buyer=False, heavy=False)
    assert not check_body("/api/v1/credits/wallet", {"available_credits": 90},
                          user_id=1, buyer=False, heavy=False)
