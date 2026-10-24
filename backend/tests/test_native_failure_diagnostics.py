"""Failure labels preserve private exceptions and the original one-call flow."""
from __future__ import annotations

import json
import logging
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from google.api_core.exceptions import (
    NotFound,
    PermissionDenied,
    ResourceExhausted,
    TooManyRequests,
)

from app.domains.candidate_accounts import production as accounts
from app.domains.recovery import browser_pairing_production as pairing
from app.domains.recovery import gcp_journal as journal
from app.domains.recovery import native_diagnostics as diagnostics
from app.domains.recovery.gcp_rpc import FirestoreRpc
from app.domains.recovery.store import GuardUnavailable
from app.observability import JsonFormatter, correlation_id_var


@pytest.fixture
def events(monkeypatch):
    captured = []
    def warning(message, *, extra):
        assert message == "native_boundary_failure_v1"
        record = logging.LogRecord("native_boundary", logging.WARNING, "", 0, message, (), None)
        record.native_failure = extra["native_failure"]
        captured.append(json.loads(JsonFormatter().format(record)))
    monkeypatch.setattr(diagnostics._log, "warning", warning)
    return captured


class PrivateFailure(RuntimeError):
    def __str__(self):
        raise AssertionError("Exception text must never be inspected")

    def __repr__(self):
        raise AssertionError("Exception repr must never be inspected")


def test_secret_exception_is_never_formatted_and_original_identity_is_preserved(events):
    error = PrivateFailure("synthetic-private-password-token-resource")
    with pytest.raises(PrivateFailure) as raised:
        with diagnostics.native_phase("candidate_account", "service"):
            raise error
    assert raised.value is error
    assert events == [{"event": "native_boundary_failure_v1", "component": "candidate_account",
        "stage": "service", "exception_type": "OtherError"}]


def test_safe_formatter_never_adds_correlation_message_arguments_or_stack():
    error = PrivateFailure("synthetic-private-input")
    record = logging.LogRecord("private-logger", logging.ERROR, "private-path", 1,
        "private-message %r", (error,), (PrivateFailure, error, None))
    record.native_failure = diagnostics.NativeFailureEvent("candidate_account", "service", "GuardUnavailable")
    token = correlation_id_var.set("private-correlation-id")
    try:
        assert json.loads(JsonFormatter().format(record)) == {"event": "native_boundary_failure_v1",
            "component": "candidate_account", "stage": "service", "exception_type": "GuardUnavailable"}
    finally:
        correlation_id_var.reset(token)


def test_logging_failure_cannot_replace_original_or_replay_operation(monkeypatch):
    error = PrivateFailure("synthetic-private-input")
    logger = Mock(side_effect=RuntimeError("synthetic logger failure"))
    monkeypatch.setattr(diagnostics._log, "warning", logger)
    calls = []
    with pytest.raises(PrivateFailure) as raised:
        with diagnostics.native_phase("candidate_account", "service"):
            calls.append("native_operation")
            raise error
    assert raised.value is error and calls == ["native_operation"] and logger.call_count == 1


def test_first_inner_type_survives_existing_exception_collapse_once(events):
    with pytest.raises(GuardUnavailable):
        with diagnostics.native_phase("candidate_account", "pre_witness"):
            try:
                with diagnostics.native_phase("witness_bytes", "live_metadata"):
                    raise PermissionDenied("synthetic private resource and credential")
            except PermissionDenied:
                raise GuardUnavailable("synthetic private wrapper") from None
    assert events == [{"event": "native_boundary_failure_v1", "component": "witness_bytes",
        "stage": "live_metadata", "exception_type": "PermissionDenied"}]
    with pytest.raises(RuntimeError):
        with diagnostics.native_phase("candidate_account", "service"):
            raise RuntimeError("another request")
    assert len(events) == 2  # A new request is not suppressed by the prior failure.


def test_expected_absence_is_not_reported_or_suppressed(events):
    with diagnostics.native_phase("candidate_account", "pre_witness"):
        try:
            with diagnostics.native_phase("execution_witness", "closure", expected=(NotFound,)):
                raise NotFound("synthetic absent closure")
        except NotFound:
            pass
    assert events == []


@pytest.mark.parametrize("message,code", [
    ("A fresh dedicated pinned Storage SDK client is required", "storage_client_pin"),
    ("A fresh dedicated pinned Storage SDK client is required synthetic-private-suffix", None),
])
def test_guard_code_requires_exact_allowlisted_string_never_outputs_arguments(events, message, code):
    with pytest.raises(GuardUnavailable):
        with diagnostics.native_phase("execution_witness", "check"):
            raise GuardUnavailable(message)
    assert events[0].get("guard_code") == code
    assert message not in json.dumps(events)


def test_unknown_event_labels_and_exception_names_are_not_serialized():
    event = diagnostics.NativeFailureEvent("private-owner", "private-resource", "PrivateSecretType", "private-token")
    assert event.payload() == {"event": "native_boundary_failure_v1", "component": "unclassified",
        "stage": "unclassified", "exception_type": "OtherError"}


@pytest.mark.parametrize("failure", [None, "composition", "pre_witness", "service", "post_configuration", "post_witness"])
def test_account_invoke_preserves_exact_order_counts_and_original_failure(events, monkeypatch, failure):
    instance = accounts.ConfiguredCandidateAccounts.__new__(accounts.ConfiguredCandidateAccounts)
    calls = []
    error = RuntimeError("synthetic private data")
    def visit(stage):
        calls.append(stage)
        if stage == failure:
            raise error
    composed = []
    def composition():
        visit("composition" if not composed else "post_configuration")
        composed.append(True)
        return current
    witnessed = []
    def witness(_pin):
        visit("pre_witness" if not witnessed else "post_witness")
        witnessed.append(True)
    current = SimpleNamespace(service=object(), fence=SimpleNamespace(check=witness, pin=SimpleNamespace(registry=object())))
    monkeypatch.setattr(instance, "_composition", composition)
    def operation(service):
        assert service is current.service
        visit("service")
        return 42
    order = ["composition", "pre_witness", "service", "post_configuration", "post_witness"]
    if failure is None:
        assert instance._invoke(operation) == 42 and events == [] and calls == order
    else:
        with pytest.raises(RuntimeError) as raised:
            instance._invoke(operation)
        assert raised.value is error and calls == order[:order.index(failure)+1]
        assert events == [{"event": "native_boundary_failure_v1", "component": "candidate_account",
            "stage": failure, "exception_type": "RuntimeError"}]


@pytest.mark.parametrize("factory,flag,component", [
    (accounts.production_candidate_accounts, "CANDIDATE_ACCOUNT_LIFECYCLE_ENABLED", "candidate_account"),
    (pairing.configuration, "BROWSER_PAIRING_NATIVE_ENABLED", "browser_pairing"),
])
def test_closed_configuration_is_reported_without_constructing_native_clients(events, monkeypatch, factory, flag, component):
    monkeypatch.setenv(flag, "false")
    with pytest.raises(GuardUnavailable):
        factory()
    assert events[0]["component"] == component and events[0]["stage"] == "configuration"


@pytest.mark.parametrize("operation", ["candidate", "device"])
@pytest.mark.parametrize("failure", [False, True])
def test_pairing_native_call_is_never_replayed_and_postcheck_stays_in_order(events, monkeypatch, operation, failure):
    instance = pairing.ConfiguredBrowserPairing.__new__(pairing.ConfiguredBrowserPairing)
    calls = []
    error = PermissionDenied("synthetic private resource")
    def native_call(*args):
        calls.append("native_operation")
        if failure:
            raise error
        return {"status": "synthetic"}
    native = SimpleNamespace(candidate=native_call, device=native_call)
    def service():
        calls.append("service")
        return native
    monkeypatch.setattr(instance, "_service", service)
    monkeypatch.setattr(pairing, "configuration", lambda: SimpleNamespace())
    invoke = (lambda: instance.candidate("challenge", b"{}", "private", "private")) if operation == "candidate" else (lambda: instance.device("status", b"{}", "private"))
    if failure:
        with pytest.raises(PermissionDenied) as raised:
            invoke()
        assert raised.value is error and calls == ["service", "native_operation"]
        assert events == [{"event": "native_boundary_failure_v1", "component": "browser_pairing",
            "stage": "native_operation", "exception_type": "PermissionDenied"}]
    else:
        assert invoke() == {"status": "synthetic"}
        assert calls == ["service", "native_operation", "service"] and events == []


@pytest.mark.parametrize("failure", [None, "live_metadata", "exact_metadata", "bounded_media"])
def test_exact_witness_io_labels_keep_existing_calls_cleanup_and_no_retry(events, monkeypatch, failure):
    calls = []
    error = PermissionDenied("synthetic private object")
    class Blob:
        _properties = {"generation": "7", "size": "2"}
        def reload(self, **kwargs):
            stage = "live_metadata" if "live_metadata" not in calls else "exact_metadata"
            calls.append(stage)
            assert kwargs["retry"] is None
            if stage == failure:
                raise error
        def download_to_file(self, sink, **kwargs):
            calls.append("bounded_media")
            assert kwargs["retry"] is None
            if failure == "bounded_media":
                raise error
            sink.write(b"{}")
    bucket = SimpleNamespace(name="synthetic", client=object(), blob=lambda *args, **kwargs: Blob())
    monkeypatch.setattr(journal, "DownloadClient", lambda *args: SimpleNamespace(close_responses=lambda: calls.append("cleanup")))
    if failure is None:
        assert journal._exact_bytes(bucket, "private-path", b"{}", generation="7", timeout=2) == "7"
        assert calls == ["live_metadata", "exact_metadata", "bounded_media", "cleanup"] and events == []
    else:
        with pytest.raises(PermissionDenied) as raised:
            journal._exact_bytes(bucket, "private-path", b"{}", generation="7", timeout=2)
        assert raised.value is error
        expected = ["live_metadata", "exact_metadata", "bounded_media"]
        assert calls == expected[:expected.index(failure)+1] + (["cleanup"] if failure == "bounded_media" else [])
        assert events == [{"event": "native_boundary_failure_v1", "component": "witness_bytes",
            "stage": failure, "exception_type": "PermissionDenied"}]


@pytest.mark.parametrize("exception", [PermissionDenied, ResourceExhausted, TooManyRequests])
def test_firestore_identity_preserves_provider_type_before_existing_safe_wrapper(events, exception):
    error = exception("synthetic private database", details=("synthetic private quota metadata",))
    admin = SimpleNamespace(get_database=Mock(side_effect=error))
    rpc = FirestoreRpc(object(), admin, database_resource="projects/synthetic-project/databases/registry-db")
    with pytest.raises(GuardUnavailable) as raised:
        rpc.identity()
    assert raised.value.__cause__ is error and admin.get_database.call_count == 1
    admin.get_database.assert_called_once_with(
        request={"name": "projects/synthetic-project/databases/registry-db"}, retry=None, timeout=2.0)
    assert events == [{"event": "native_boundary_failure_v1", "component": "firestore_rpc",
        "stage": "identity", "exception_type": exception.__name__}]
    assert "synthetic private" not in json.dumps(events)


def test_unknown_quota_exception_name_and_private_details_stay_redacted(events):
    class PrivateQuotaFailure(ResourceExhausted):
        def __str__(self):
            raise AssertionError("Quota error text must never be inspected")

        def __repr__(self):
            raise AssertionError("Quota error repr must never be inspected")

    error = PrivateQuotaFailure("synthetic private database", details=("private quota owner",))
    admin = SimpleNamespace(get_database=Mock(side_effect=error))
    rpc = FirestoreRpc(object(), admin, database_resource="projects/synthetic-project/databases/registry-db")
    with pytest.raises(GuardUnavailable) as raised:
        rpc.identity()
    assert raised.value.__cause__ is error and admin.get_database.call_count == 1
    assert events == [{"event": "native_boundary_failure_v1", "component": "firestore_rpc",
        "stage": "identity", "exception_type": "OtherError"}]
