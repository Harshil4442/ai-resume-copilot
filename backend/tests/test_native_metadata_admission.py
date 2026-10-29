"""Strict limits/Redis dispatch with an inert Lua endpoint, not a Redis service.

The installed RedisStorage and its exact TLS/retry configuration are real. Its
atomic Lua endpoint is explicitly intercepted, so these tests do not establish
provider atomicity, runtime reachability or Redis command allowance.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from threading import Lock
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi import HTTPException, Request
from limits.storage import MemoryStorage, RedisStorage
from redis.backoff import NoBackoff
from redis.exceptions import ConnectionError as RedisConnectionError
from redis.retry import Retry

from app.domains.recovery import native_metadata_admission as admission
from app.domains.recovery.native_metadata_admission import (
    MetadataAdmissionUnavailable,
    MetadataCapacityUnavailable,
)
from app.routers import candidate_accounts, candidate_ingress
from app.routers.native_metadata_http import metadata_http_error
from tests.test_candidate_production_factory import PASSWORD, configured, register  # noqa: F401


def inert_shared_redis(monkeypatch, *, project="local-proof", budget=40):
    storage = RedisStorage("rediss://synthetic.invalid:6379/0", socket_timeout=1.0,
        socket_connect_timeout=1.0, retry=Retry(NoBackoff(), 0), retry_on_timeout=False)
    state = SimpleNamespace(storage=storage, calls=[], entries={}, grants=0, lock=Lock())
    def acquire(_storage, key, amount):
        with state.lock:
            state.calls.append(([key], [amount]))
            count = state.entries.get(key, 0)
            if count >= amount:
                return 0
            state.entries[key] = count + 1
            state.grants += 1
            return 1
    monkeypatch.setattr(admission, "_acquire", acquire)
    monkeypatch.setattr(admission, "_shared_storage", lambda: storage)
    monkeypatch.setenv("CANDIDATE_METADATA_ADMISSION_ENABLED", "true")
    monkeypatch.setenv("CANDIDATE_METADATA_CALLS_PER_MINUTE", str(budget))
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", project)
    return state


def test_default_off_does_not_touch_storage_and_unknown_mode_fails_closed(monkeypatch):
    monkeypatch.delenv("CANDIDATE_METADATA_ADMISSION_ENABLED", raising=False)
    monkeypatch.setattr(admission, "_shared_storage", lambda: pytest.fail("No storage access"))
    admission.before_metadata_rpc("local-proof")
    monkeypatch.setenv("CANDIDATE_METADATA_ADMISSION_ENABLED", "TRUE")
    with pytest.raises(MetadataAdmissionUnavailable):
        admission.before_metadata_rpc("local-proof")


def test_atomic_script_uses_one_project_namespace_fixed_one_cost_and_server_time(monkeypatch):
    state = inert_shared_redis(monkeypatch)
    for _ in range(40):
        admission.before_metadata_rpc("local-proof")
    with pytest.raises(MetadataCapacityUnavailable):
        admission.before_metadata_rpc("local-proof")
    assert state.grants == 40 and len(state.calls) == 41
    assert len({row[0][0] for row in state.calls}) == 1
    assert state.calls[0][1] == [40]
    assert "local-proof" in state.calls[0][0][0]


def test_concurrent_inert_script_dispatches_cannot_admit_more_than_shared_budget(monkeypatch):
    state = inert_shared_redis(monkeypatch)
    def attempt(_):
        try:
            admission.before_metadata_rpc("local-proof")
            return True
        except MetadataCapacityUnavailable:
            return False
    with ThreadPoolExecutor(max_workers=8) as pool:
        outcomes = list(pool.map(attempt, range(80)))
    assert sum(outcomes) == state.grants == 40


def test_different_lower_budget_does_not_create_another_namespace(monkeypatch):
    state = inert_shared_redis(monkeypatch, budget=20)
    for _ in range(20):
        admission.before_metadata_rpc("local-proof")
    monkeypatch.setenv("CANDIDATE_METADATA_CALLS_PER_MINUTE", "40")
    for _ in range(20):
        admission.before_metadata_rpc("local-proof")
    with pytest.raises(MetadataCapacityUnavailable):
        admission.before_metadata_rpc("local-proof")
    assert state.grants == 40 and len(state.entries) == 1


@pytest.mark.parametrize("value", ["", "0", "41", "60", "-1", "1.0", "True", " 40", "040"])
def test_missing_or_unreviewed_budget_never_reaches_storage(monkeypatch, value):
    state = inert_shared_redis(monkeypatch)
    monkeypatch.setenv("CANDIDATE_METADATA_CALLS_PER_MINUTE", value)
    with pytest.raises(MetadataAdmissionUnavailable):
        admission.before_metadata_rpc("local-proof")
    assert not state.calls


@pytest.mark.parametrize("ack", [None, True, False, -1, 2, "1", b"1", {}])
def test_unknown_lua_ack_is_not_a_grant_or_automatic_retry(monkeypatch, ack):
    inert_shared_redis(monkeypatch)
    execute = Mock(return_value=ack)
    monkeypatch.setattr(admission, "_acquire", execute)
    with pytest.raises(MetadataAdmissionUnavailable):
        admission.before_metadata_rpc("local-proof")
    execute.assert_called_once()


def test_unknown_storage_failure_is_fixed_private_and_never_retried(monkeypatch):
    inert_shared_redis(monkeypatch)
    failure = Mock(side_effect=RedisConnectionError("synthetic-untrusted-provider-detail"))
    monkeypatch.setattr(admission, "_acquire", failure)
    with pytest.raises(MetadataAdmissionUnavailable) as caught:
        admission.before_metadata_rpc("local-proof")
    failure.assert_called_once()
    assert "provider-detail" not in str(caught.value)


@pytest.mark.parametrize("changed", ["memory", "retry", "timeout", "tls", "certificate", "hostname", "project"])
def test_unbounded_or_nonshared_configuration_is_not_a_fallback(monkeypatch, changed):
    state = inert_shared_redis(monkeypatch)
    if changed == "memory":
        monkeypatch.setattr(admission, "_shared_storage", lambda: MemoryStorage())
    elif changed == "project":
        monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "other-proof")
    elif changed == "tls":
        from redis.connection import Connection
        state.storage.storage.connection_pool.connection_class = Connection
    elif changed == "certificate":
        state.storage.storage.connection_pool.connection_kwargs["ssl_cert_reqs"] = "none"
    elif changed == "hostname":
        state.storage.storage.connection_pool.connection_kwargs["ssl_check_hostname"] = False
    elif changed == "retry":
        state.storage.storage.connection_pool.connection_kwargs["retry"] = Retry(NoBackoff(), 1)
    else:
        state.storage.storage.connection_pool.connection_kwargs["socket_timeout"] = None
    with pytest.raises(MetadataAdmissionUnavailable):
        admission.before_metadata_rpc("local-proof")
    assert not state.calls


def test_atomic_script_preserves_exact_installed_body_and_refuses_unreviewed_dependency(monkeypatch):
    body = RedisStorage.SCRIPT_ACQUIRE_MOVING_WINDOW
    script = admission._atomic_window_script()
    assert script.count(b"redis.call('TIME')") == 1
    assert body in script
    assert b"if result == false then return 0 end" in script
    monkeypatch.setattr(RedisStorage, "SCRIPT_ACQUIRE_MOVING_WINDOW", body + b"\n")
    with pytest.raises(MetadataAdmissionUnavailable):
        admission._atomic_window_script()


@pytest.mark.parametrize("error,status,retry", [(MetadataCapacityUnavailable(), 429, "60"), (MetadataAdmissionUnavailable(), 503, "30")])
def test_candidate_http_helpers_keep_capacity_distinct_without_provider_detail(error, status, retry):
    mapped = metadata_http_error(error)
    assert mapped.status_code == status and mapped.headers["Retry-After"] == retry
    assert mapped.headers["Cache-Control"] == "private, no-store"
    assert candidate_accounts._failure(error).status_code == status
    recovery = candidate_accounts._recovery_failure(error)
    assert recovery.status_code == status and recovery.headers["retry-after"] == retry


@pytest.mark.parametrize("error,status", [(MetadataCapacityUnavailable(), 429), (MetadataAdmissionUnavailable(), 503)])
def test_private_candidate_ingress_preserves_typed_capacity_before_handler(monkeypatch, error, status):
    def refused(*_args, **_kwargs):
        raise error
    monkeypatch.setattr(candidate_ingress.replay, "production_candidate_ingress", lambda: SimpleNamespace(authorize=refused))
    path = "/api/auth/candidate/v1/availability"
    request = Request({"type": "http", "method": "GET", "scheme": "https", "path": path,
        "raw_path": path.encode(), "root_path": "", "query_string": b"",
        "headers": [(b"x-hirewiz-candidate-ingress", b"synthetic-header")]})
    request.state._bounded_sensitive_body = b""
    with pytest.raises(HTTPException) as caught:
        candidate_ingress.require_candidate_ingress(request)
    assert caught.value.status_code == status and caught.value.headers["Retry-After"] in {"30", "60"}


@pytest.mark.parametrize("error,status", [(MetadataCapacityUnavailable(), 429), (MetadataAdmissionUnavailable(), 503)])
def test_signed_current_account_dependency_refuses_before_business_handler(configured, monkeypatch, error, status):  # noqa: F811
    from app import security
    from app.domains.candidate_accounts import service

    c = configured
    owner = register(c)
    session = c.accounts.login(email="synthetic@example.test", password=PASSWORD)
    token = security.create_access_token(subject=str(owner), candidate_lifetime={
        "version": 1, "context": session.context.model_dump(mode="json"), "auth_generation": 1})
    def denied(*_args, **_kwargs):
        assert not c.sql_active
        raise error
    monkeypatch.setattr(service, "production_candidate_accounts", lambda: SimpleNamespace(validate=denied))
    with c.accounts.sessions() as db, pytest.raises(HTTPException) as caught:
        security.get_current_user(token, db)
    assert caught.value.status_code == status
    assert caught.value.headers["Retry-After"] == ("60" if status == 429 else "30")
    assert caught.value.detail["code"] in {"NATIVE_METADATA_CAPACITY", "NATIVE_METADATA_UNAVAILABLE"}
