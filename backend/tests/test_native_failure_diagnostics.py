"""Failure labels preserve private exceptions and the original one-call flow."""
from __future__ import annotations

import json
import logging
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

import pytest
from google.api_core import exceptions as api_errors
from google.api_core.exceptions import NotFound, PermissionDenied, ResourceExhausted
from google.auth import exceptions as auth_errors
from test_browser_pairing_production import candidate as candidate_request
from test_browser_pairing_production import (  # noqa: F401 -- intercepted fixture dependencies
    configured,
    kms,
    transport,
)
from test_browser_pairing_production import (
    pairing as native_pairing,  # noqa: F401 -- intercepted pairing fixture
)
from test_candidate_production_factory import PASSWORD

from app.domains.candidate_accounts import production as accounts
from app.domains.recovery import browser_pairing_production as pairing
from app.domains.recovery import gcp_journal as journal
from app.domains.recovery import native_diagnostics as diagnostics
from app.domains.recovery.contracts import canonical
from app.domains.recovery.gcp_rpc import FirestoreRpc
from app.domains.recovery.store import GuardDenied, GuardUnavailable
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
def test_pairing_native_call_is_never_replayed_and_postcheck_stays_in_order(
    events, monkeypatch, operation, failure, native_pairing,  # noqa: F811 -- imported intercepted fixture
):
    # Real intercepted composition keeps local preflight and identity scope active;
    # only this diagnostic's target operation is replaced, without replay or writes.
    c = native_pairing
    instance = c.browser
    original_service = instance._service
    native = original_service()
    if operation == "candidate":
        email = f"diagnostic-{uuid4()}@example.test"
        c.accounts.register(email=email, password=PASSWORD, policy_version="2026-10-08")
        context = c.accounts.login(email=email, password=PASSWORD).context
    calls = []
    captured = []  # Keep the real opaque handle alive through the cleanup check.
    error = PermissionDenied("synthetic private resource")
    def native_call():
        calls.append("native_operation")
        if failure:
            raise error
        return {"status": "synthetic"}
    def service():
        calls.append("service")
        return original_service()
    monkeypatch.setattr(instance, "_service", service)
    metadata = c.native.sdk.admin.transport.get_database
    original_identity = metadata.side_effect
    def identity(request, **kwargs):
        names = {c.execution.database: "identity.execution", c.protected.database: "identity.protected"}
        calls.append(names[request.name])
        return original_identity(request, **kwargs)
    monkeypatch.setattr(metadata, "side_effect", identity)
    if operation == "candidate":
        original_preflight = native.gateway.verify
        def candidate_preflight(*args, **kwargs):
            calls.append("preflight")
            return original_preflight(*args, **kwargs)
        monkeypatch.setattr(native.gateway, "verify", candidate_preflight)
        original_capture = native.gateway.admit_composition
        def candidate_capture(*args, **kwargs):
            admission = original_capture(*args, **kwargs)
            calls.append("capture")
            captured.append(admission)
            return admission
        monkeypatch.setattr(native.gateway, "admit_composition", candidate_capture)
        candidate_body = {"pairing_id": str(uuid4())}
        body = canonical(candidate_body).encode()
        def candidate_admitted(current_operation, raw, admission):
            assert current_operation == "challenge" and raw == body and captured == [admission]
            envelope = native.gateway.admitted(admission, owner=native,
                path=f"/api/v1/browser-pairing/candidate/{current_operation}", raw=raw,
                now_ms=native.coordinator.now_ms(), start=True)
            assert envelope.context == context
            return native_call()
        monkeypatch.setattr(native, "candidate_admitted", candidate_admitted)
        def invoke():
            return candidate_request(c, context, "challenge", candidate_body)
    else:
        original_preflight = pairing._device_preflight
        def device_preflight(*args, **kwargs):
            calls.append("preflight")
            return original_preflight(*args, **kwargs)
        monkeypatch.setattr(pairing, "_device_preflight", device_preflight)
        body = canonical(transport(c, "status", str(uuid4()))).encode()
        origin = f"chrome-extension://{c.factory_config['extension_id']}"
        original_capture = native.device_transport.capture_composition
        def device_capture(*args, **kwargs):
            ingress = original_capture(*args, **kwargs)
            calls.append("capture")
            captured.append(ingress)
            return ingress
        monkeypatch.setattr(native.device_transport, "capture_composition", device_capture)
        def device_captured(current_operation, raw, current_origin, ingress):
            assert current_operation == "status" and raw == body and current_origin == origin
            assert captured == [ingress]
            # Timing is not device authority: this diagnostic stops before the
            # native key lookup, ECDSA/nonce checks and business command.
            native.device_transport.check_timing(ingress, native=native,
                operation=current_operation, raw=raw, origin=current_origin)
            return native_call()
        monkeypatch.setattr(native, "device_captured", device_captured)
        def invoke():
            return instance.device("status", body, origin)
    entry = ["preflight", "capture", "service", "identity.execution", "identity.protected", "native_operation"]
    final = ["identity.execution", "identity.protected"]
    prior_native_writes = len(c.native.calls)
    if failure:
        with pytest.raises(PermissionDenied) as raised:
            invoke()
        assert raised.value is error and calls == [*entry, *final]
        assert events == [{"event": "native_boundary_failure_v1", "component": "browser_pairing",
            "stage": "native_operation", "exception_type": "PermissionDenied"}]
    else:
        assert invoke() == {"status": "synthetic"}
        assert calls == [*entry, "service", *final] and events == []
    assert calls.count("native_operation") == 1
    assert len(captured) == 1
    if operation == "candidate":
        assert not native.gateway._admissions
        with pytest.raises(GuardDenied):
            native.gateway.admitted(captured[0], owner=native,
                path="/api/v1/browser-pairing/candidate/challenge", raw=body,
                now_ms=native.coordinator.now_ms(), start=True)
    else:
        assert not native.device_transport._timings and not native.device_transport._admissions
        with pytest.raises(GuardDenied):
            native.device_transport.check_timing(captured[0], native=native,
                operation="status", raw=body, origin=origin)
    assert len(c.native.calls) == prior_native_writes and c.kms.calls == []


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


_GOOGLE_FAILURES = (
    api_errors.GoogleAPIError, api_errors.DuplicateCredentialArgs, api_errors.RetryError,
    api_errors.GoogleAPICallError, api_errors.Redirection, api_errors.MovedPermanently,
    api_errors.NotModified, api_errors.TemporaryRedirect, api_errors.ResumeIncomplete,
    api_errors.ClientError, api_errors.BadRequest, api_errors.InvalidArgument,
    api_errors.FailedPrecondition, api_errors.OutOfRange, api_errors.Unauthorized,
    api_errors.Unauthenticated, api_errors.Forbidden, api_errors.PermissionDenied,
    api_errors.NotFound, api_errors.MethodNotAllowed, api_errors.Conflict,
    api_errors.AlreadyExists, api_errors.Aborted, api_errors.LengthRequired,
    api_errors.PreconditionFailed, api_errors.RequestRangeNotSatisfiable,
    api_errors.TooManyRequests, api_errors.ResourceExhausted, api_errors.Cancelled,
    api_errors.ServerError, api_errors.InternalServerError, api_errors.Unknown,
    api_errors.DataLoss, api_errors.MethodNotImplemented, api_errors.BadGateway,
    api_errors.ServiceUnavailable, api_errors.GatewayTimeout, api_errors.DeadlineExceeded,
    auth_errors.GoogleAuthError, auth_errors.TransportError, auth_errors.RefreshError,
    auth_errors.UserAccessTokenError, auth_errors.DefaultCredentialsError,
    auth_errors.MutualTLSChannelError, auth_errors.ClientCertError, auth_errors.OAuthError,
    auth_errors.ReauthFailError, auth_errors.ReauthSamlChallengeFailError,
    auth_errors.MalformedError, auth_errors.InvalidResource, auth_errors.InvalidOperation,
    auth_errors.InvalidValue, auth_errors.InvalidType, auth_errors.OSError,
    auth_errors.TimeoutError, auth_errors.ResponseError,
)


@pytest.mark.parametrize("exception", _GOOGLE_FAILURES, ids=lambda exception: exception.__name__)
def test_firestore_identity_preserves_provider_type_before_existing_safe_wrapper(events, monkeypatch, exception):
    if exception is api_errors.RetryError:
        error = exception("synthetic private database", RuntimeError("synthetic private cause"))
    elif issubclass(exception, api_errors.GoogleAPICallError):
        error = exception("synthetic private database", details=("synthetic private provider metadata",))
    else:
        error = exception("synthetic private database")

    def forbidden_text(*args):
        raise AssertionError("Provider exception contents must never be inspected")

    monkeypatch.setattr(exception, "__str__", forbidden_text)
    monkeypatch.setattr(exception, "__repr__", forbidden_text)
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


@pytest.mark.parametrize("base", [ResourceExhausted, auth_errors.RefreshError, auth_errors.DefaultCredentialsError])
def test_unknown_google_exception_name_and_private_details_stay_redacted(events, base):
    class PrivateSdkFailure(base):
        def __str__(self):
            raise AssertionError("Provider error text must never be inspected")

        def __repr__(self):
            raise AssertionError("Provider error repr must never be inspected")

    error = PrivateSdkFailure("synthetic private database and credential owner")
    admin = SimpleNamespace(get_database=Mock(side_effect=error))
    rpc = FirestoreRpc(object(), admin, database_resource="projects/synthetic-project/databases/registry-db")
    with pytest.raises(GuardUnavailable) as raised:
        rpc.identity()
    assert raised.value.__cause__ is error and admin.get_database.call_count == 1
    assert events == [{"event": "native_boundary_failure_v1", "component": "firestore_rpc",
        "stage": "identity", "exception_type": "OtherError"}]
