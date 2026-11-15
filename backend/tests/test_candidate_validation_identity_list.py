"""Actual account factory over intercepted native SDKs; no runtime IAM proof."""
from __future__ import annotations

from unittest.mock import Mock
from uuid import uuid4

import pytest
from google.cloud.firestore_admin_v1.types import Database, ListDatabasesResponse
from google.cloud.firestore_v1.types import BatchGetDocumentsResponse

from app.domains.recovery.store import GuardDenied, GuardUnavailable
from tests.test_candidate_production_factory import PASSWORD, configured, register  # noqa: F401
from tests.test_native_metadata_admission import inert_shared_redis


def enabled(c, monkeypatch, *, mutate=None):
    register(c)
    result = c.accounts.login(email="synthetic@example.test", password=PASSWORD)
    calls = []
    def listed(request, **kwargs):
        assert not c.sql_active and not c.native.views
        assert request.parent == "projects/hirewiz-local-authority" and request.show_deleted is False
        assert kwargs["timeout"] == 2.0
        calls.append(len(c.http_calls))
        value = ListDatabasesResponse(databases=[Database(name=name, uid=uid, type_=1)
            for name, uid in c.native.identities.items()])
        return mutate(value, len(calls)) if mutate else value
    c.native.sdk.admin.transport.list_databases.side_effect = listed
    c.native.sdk.admin.list_databases = Mock(wraps=c.native.sdk.admin.list_databases)
    c.admission = inert_shared_redis(monkeypatch, project="hirewiz-local-authority")
    monkeypatch.setenv("CANDIDATE_ACCOUNT_LIST_IDENTITY_ENABLED", "true")
    return result, calls


def test_actual_read_only_validate_is_enclosed_without_changing_fresh_session_or_gcs_checks(configured, monkeypatch):  # noqa: F811
    c = configured
    result, calls = enabled(c, monkeypatch)
    get_before = c.native.sdk.admin.transport.get_database.call_count
    commits_before = list(c.native.calls)
    c.accounts.validate(result.context, auth_generation=1)
    assert len(calls) == 2 and calls[1] > calls[0]
    assert c.native.sdk.admin.transport.get_database.call_count == get_before
    assert c.native.calls == commits_before
    assert c.native.sdk.admin.list_databases.call_args.kwargs["retry"] is None
    assert c.admission.grants == 2
    c.accounts.validate(result.context, auth_generation=1)
    assert len(calls) == 4 and calls[3] > calls[2]
    assert c.admission.grants == 4


def test_normal_validation_groups_native_reads_with_one_complete_snapshot(configured, monkeypatch):  # noqa: F811
    c = configured
    result, calls = enabled(c, monkeypatch)
    native_read = c.native.sdk.client.transport.batch_get_documents
    commits = list(c.native.calls)

    def observe():
        offset = native_read.call_count
        gcs_before = len(c.http_calls)
        c.accounts.validate(result.context, auth_generation=1)
        reads = [call.args[0] for call in native_read.call_args_list[offset:]]
        assert len({request.transaction for request in reads}) == 1 and reads[0].transaction
        return reads, c.http_calls[gcs_before:]

    with monkeypatch.context() as patch:
        patch.setattr(c.accounts._composition().service, "_session_validation", None)
        singleton, legacy_gcs = observe()
    requests, batched_gcs = observe()
    assert [len(request.documents) for request in singleton] == [1] * 16
    assert [len(request.documents) for request in requests] == [1, 1, 1, 2, 2, 2, 3, 2, 2]
    assert tuple(path for request in singleton for path in request.documents) == tuple(
        path for request in requests for path in request.documents)
    assert requests[0].transaction != singleton[0].transaction
    assert len(batched_gcs) == 18 and legacy_gcs == batched_gcs
    assert c.native.calls == commits and not c.native.views and not c.sql_active
    assert len(calls) == c.admission.grants == 4


@pytest.mark.parametrize("fault", ["partial", "explicitly_missing_owner"])
def test_batched_validation_refuses_unknown_or_missing_required_rows(configured, monkeypatch, fault):  # noqa: F811
    c = configured
    result, calls = enabled(c, monkeypatch)
    native_read = c.native.sdk.client.transport.batch_get_documents
    original = native_read.side_effect
    batch_sizes = []

    def changed(request, **kwargs):
        responses = list(original(request, **kwargs))
        if len(request.documents) > 1:
            batch_sizes.append(len(request.documents))
            if fault == "partial" and len(batch_sizes) == 1:
                responses.pop()
            if fault == "explicitly_missing_owner" and len(batch_sizes) == 2:
                responses[0] = BatchGetDocumentsResponse(missing=request.documents[0])
        return iter(responses)

    native_read.side_effect = changed
    commits = list(c.native.calls)
    detail = "Complete transactional document evidence is unavailable" if fault == "partial" else "Protected account ownership is missing or restored"
    with pytest.raises(GuardUnavailable, match=detail):
        c.accounts.validate(result.context, auth_generation=1)
    assert batch_sizes == ([2] if fault == "partial" else [2, 2])
    assert len(calls) == c.admission.grants == 2
    assert c.native.calls == commits and not c.native.views and not c.sql_active


def test_revoked_session_preserves_denial_when_group_is_complete(configured, monkeypatch):  # noqa: F811
    c = configured
    result, calls = enabled(c, monkeypatch)
    c.accounts.logout(result.context)
    composition = c.accounts._composition()
    native_read = c.native.sdk.client.transport.batch_get_documents
    original = native_read.side_effect
    commits = list(c.native.calls)
    grants_before = c.admission.grants
    incomplete_requests = []

    with pytest.raises(GuardDenied, match="Protected session has been revoked"):
        c.accounts.validate(result.context, auth_generation=1)

    assert ("v3_session_tombstones", str(result.context.session_id)) in c.native.values()
    tombstone_path = native_read.call_args.args[0].documents[0]

    def omit_pending(request, **kwargs):
        responses = list(original(request, **kwargs))
        if tuple(request.documents)[:1] == (tombstone_path,) and len(request.documents) == 2:
            assert responses[0].found.name == tombstone_path
            incomplete_requests.append(request.transaction)
            return iter(responses[:1])
        return iter(responses)

    native_read.side_effect = omit_pending
    with monkeypatch.context() as patch:
        patch.setattr(composition.service, "_session_validation", None)
        with pytest.raises(GuardDenied, match="Protected session has been revoked"):
            c.accounts.validate(result.context, auth_generation=1)
    with pytest.raises(GuardUnavailable, match="Complete transactional document evidence is unavailable"):
        c.accounts.validate(result.context, auth_generation=1)
    assert len(incomplete_requests) == 1
    assert len(calls) == c.admission.grants - grants_before == 6
    assert c.native.calls == commits and not c.native.views and not c.sql_active


def test_prefetched_transaction_cannot_stage_authority_mutation(configured, monkeypatch):  # noqa: F811
    c = configured
    enabled(c, monkeypatch)
    commits = list(c.native.calls)

    def attempt_write(tx):
        tx.prefetch_read_only((("v3_root", "current"), ("v3_sessions", "synthetic-missing")))
        tx.put("v3_sessions", "synthetic-missing", {"forbidden": True})

    with pytest.raises(GuardDenied, match="must be read-only"):
        c.accounts._composition().scoped._run(attempt_write)
    assert c.native.calls == commits and not c.native.views


@pytest.mark.parametrize("stage", ["before", "after"])
def test_missing_or_replaced_uid_prevents_account_validation_success(configured, monkeypatch, stage):  # noqa: F811
    c = configured
    def mutate(value, count):
        if count == (1 if stage == "before" else 2):
            value.databases[0].uid = str(uuid4())
        return value
    result, calls = enabled(c, monkeypatch, mutate=mutate)
    http_before = len(c.http_calls)
    with pytest.raises(GuardUnavailable):
        c.accounts.validate(result.context, auth_generation=1)
    if stage == "before":
        assert len(calls) == 1 and len(c.http_calls) == http_before
    else:
        assert len(calls) == 2 and len(c.http_calls) > http_before


def test_default_and_lifecycle_mutations_keep_existing_metadata_path(configured, monkeypatch):  # noqa: F811
    c = configured
    result, calls = enabled(c, monkeypatch)
    before = c.native.sdk.admin.transport.get_database.call_count
    monkeypatch.delenv("CANDIDATE_ACCOUNT_LIST_IDENTITY_ENABLED")
    c.accounts.validate(result.context, auth_generation=1)
    assert not calls and c.native.sdk.admin.transport.get_database.call_count > before
    monkeypatch.setenv("CANDIDATE_ACCOUNT_LIST_IDENTITY_ENABLED", "true")
    native_read = c.native.sdk.client.transport.batch_get_documents
    offset = native_read.call_count
    c.accounts.logout(result.context)
    assert not calls and c.native.sdk.admin.transport.get_database.call_count > before
    assert native_read.call_count > offset
    assert all(len(call.args[0].documents) == 1 for call in native_read.call_args_list[offset:])


def test_invalid_mode_never_exports_validation(configured, monkeypatch):  # noqa: F811
    c = configured
    result, calls = enabled(c, monkeypatch)
    monkeypatch.setenv("CANDIDATE_ACCOUNT_LIST_IDENTITY_ENABLED", "invalid")
    with pytest.raises(GuardUnavailable):
        c.accounts.validate(result.context, auth_generation=1)
    assert not calls


def test_mode_change_during_dependent_read_is_denied_after_fresh_checks(configured, monkeypatch):  # noqa: F811
    c = configured
    result, calls = enabled(c, monkeypatch)
    original = c.native.sdk.client.transport.batch_get_documents.side_effect
    def changed(*args, **kwargs):
        monkeypatch.setenv("CANDIDATE_ACCOUNT_LIST_IDENTITY_ENABLED", "false")
        return original(*args, **kwargs)
    c.native.sdk.client.transport.batch_get_documents.side_effect = changed
    with pytest.raises(GuardUnavailable):
        c.accounts.validate(result.context, auth_generation=1)
    assert len(calls) == 2 and calls[1] > calls[0]


def test_list_activation_requires_shared_admission_to_be_explicitly_enabled(configured, monkeypatch):  # noqa: F811
    c = configured
    result, calls = enabled(c, monkeypatch)
    monkeypatch.setenv("CANDIDATE_METADATA_ADMISSION_ENABLED", "false")
    with pytest.raises(GuardUnavailable):
        c.accounts.validate(result.context, auth_generation=1)
    assert not calls and c.admission.grants == 0


@pytest.mark.parametrize("list_mode", [True, False])
def test_real_account_factory_shared_refusal_precedes_any_next_metadata_call(configured, monkeypatch, list_mode):  # noqa: F811
    from app.domains.recovery.native_metadata_admission import MetadataCapacityUnavailable

    c = configured
    result, calls = enabled(c, monkeypatch)
    monkeypatch.setenv("CANDIDATE_ACCOUNT_LIST_IDENTITY_ENABLED", "true" if list_mode else "false")
    c.admission.entries["LIMITS:hirewiz-native-metadata-v1/hirewiz-local-authority"] = 40
    get_before = c.native.sdk.admin.transport.get_database.call_count
    with pytest.raises(MetadataCapacityUnavailable):
        c.accounts.validate(result.context, auth_generation=1)
    assert not calls and c.native.sdk.admin.transport.get_database.call_count == get_before


@pytest.mark.parametrize("change", ["capacity", "disabled"])
def test_late_capacity_or_disabled_admission_cannot_export_earlier_validation(configured, monkeypatch, change):  # noqa: F811
    from app.domains.recovery.native_metadata_admission import MetadataAdmissionError

    c = configured
    result, calls = enabled(c, monkeypatch)
    original = c.native.sdk.client.transport.batch_get_documents.side_effect
    def changed(*args, **kwargs):
        if change == "capacity":
            c.admission.entries["LIMITS:hirewiz-native-metadata-v1/hirewiz-local-authority"] = 40
        else:
            monkeypatch.setenv("CANDIDATE_METADATA_ADMISSION_ENABLED", "false")
        return original(*args, **kwargs)
    c.native.sdk.client.transport.batch_get_documents.side_effect = changed
    with pytest.raises(MetadataAdmissionError):
        c.accounts.validate(result.context, auth_generation=1)
    assert len(calls) == c.admission.grants == 1
