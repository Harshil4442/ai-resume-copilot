"""Actual account factory over intercepted native SDKs; no runtime IAM proof."""
from __future__ import annotations

from unittest.mock import Mock
from uuid import uuid4

import pytest
from google.cloud.firestore_admin_v1.types import Database, ListDatabasesResponse

from app.domains.recovery.store import GuardUnavailable
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
    c.accounts.logout(result.context)
    assert not calls and c.native.sdk.admin.transport.get_database.call_count > before


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
