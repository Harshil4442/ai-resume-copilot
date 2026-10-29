"""Inert generated-GET and stock-auth boundaries; no account or provider IO."""
from __future__ import annotations

import importlib
import ssl
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
import requests.exceptions as errors
from google.auth.exceptions import RefreshError, TransportError

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "infra/gcp"))
module = importlib.import_module("rolling_native")
API = module.release.SERVICES[0]
NAME = module.rolling.PARENT + "/services/" + API


class HttpError(Exception):
    def __init__(self, status, headers=None):
        super().__init__("synthetic-private-provider-detail")
        self.status_code = status
        self.response = headers or {}


@pytest.fixture
def port(monkeypatch):
    clock, calls, sleeps, outcomes = [1.0], [], [], []
    def authorized(*_args, **_kwargs):
        return None
    session = SimpleNamespace(request=authorized)

    def get(request):
        calls.append(request.name)
        value = outcomes.pop(0)
        if callable(value):
            value = value()
        if isinstance(value, Exception):
            raise value
        return value

    def sleep(seconds):
        sleeps.append(seconds)
        clock[0] += seconds

    run = module.NativeRun.__new__(module.NativeRun)
    run.stock = SimpleNamespace(session=session)
    run._authorized_request = authorized
    run.http_error = HttpError
    run.client = SimpleNamespace(
        projects_locations_services=SimpleNamespace(Get=get),
        projects_locations_services_revisions=SimpleNamespace(Get=get),
        projects_locations_operations=SimpleNamespace(Get=get),
    )
    run.messages = SimpleNamespace(
        RunProjectsLocationsServicesGetRequest=SimpleNamespace,
        RunProjectsLocationsServicesRevisionsGetRequest=SimpleNamespace,
        RunProjectsLocationsOperationsGetRequest=SimpleNamespace,
    )
    run.decode = lambda value: value
    monkeypatch.setattr(module.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(module.time, "sleep", sleep)
    return run, outcomes, calls, sleeps, clock


@pytest.mark.parametrize("failure", [
    HttpError(429), HttpError(502), HttpError(503), HttpError(504),
    errors.ConnectionError("private"), errors.ConnectTimeout("private"), errors.ReadTimeout("private"),
])
def test_fixed_service_get_retries_known_transient_then_uses_final_response(port, failure, capsys):
    run, outcomes, calls, sleeps, _ = port
    outcomes.extend((failure, {"current": True}))
    assert run.service(API) == {"current": True}
    assert calls == [NAME, NAME] and sleeps == [0.1]
    assert capsys.readouterr() == ("", "")


@pytest.mark.parametrize("kind", ["revision", "operation"])
def test_other_fixed_get_ports_retry_with_identical_resource(port, kind):
    run, outcomes, calls, _, _ = port
    outcomes.extend((HttpError(503), {"name": "result", "done": True}))
    if kind == "revision":
        run.revision(API, API + "-inert")
    else:
        run.operation(module.rolling.PARENT + "/operations/inert")
    assert len(calls) == 2 and calls[0] == calls[1]


@pytest.mark.parametrize("failure,code", [
    (HttpError(403), "rolling_native_read_permission_denied"),
    (HttpError(401), "rolling_native_read_unauthorized"),
    (HttpError(404), "rolling_native_read_not_found"),
    (HttpError(500), "rolling_native_read_unavailable"),
    (errors.SSLError("private"), "rolling_native_read_unavailable"),
    (errors.ProxyError("private"), "rolling_native_read_unavailable"),
    (errors.ConnectionError(ssl.SSLError("private")), "rolling_native_read_unavailable"),
    (RefreshError("private"), "rolling_native_read_unavailable"),
    (TransportError("untyped private"), "rolling_native_read_unavailable"),
    (RuntimeError("private"), "rolling_native_read_unavailable"),
])
def test_permanent_or_unclassified_error_never_retries_or_echoes_vendor(port, failure, code):
    run, outcomes, calls, sleeps, _ = port
    outcomes.extend((failure, {"unused": True}))
    with pytest.raises(module.rolling.RollingDenied) as caught:
        run.service(API)
    assert str(caught.value) == code
    assert len(calls) == 1 and sleeps == [] and len(outcomes) == 1


def test_missing_revision_stays_a_single_read(port):
    run, outcomes, calls, sleeps, _ = port
    outcomes.append(HttpError(404))
    assert run.revision(API, API + "-inert") is None
    assert len(calls) == 1 and sleeps == []


def test_slow_missing_revision_cannot_escape_original_deadline(port):
    run, outcomes, calls, sleeps, clock = port
    def late():
        clock[0] += 16
        raise HttpError(404)
    outcomes.append(late)
    with pytest.raises(module.rolling.RollingDenied, match="^rolling_native_read_deadline_reconcile_only$"):
        run.revision(API, API + "-inert")
    assert len(calls) == 1 and sleeps == []


@pytest.mark.parametrize("header,code,expected_sleep", [
    ("3", None, [3.0]),
    ("20", "rolling_native_read_deadline_reconcile_only", []),
    ("private-invalid", "rolling_native_read_retry_after_invalid", []),
])
def test_retry_after_respects_provider_and_original_budget(port, header, code, expected_sleep):
    run, outcomes, calls, sleeps, _ = port
    outcomes.extend((HttpError(429, {"retry-after": header}), {"current": True}))
    if code is None:
        assert run.service(API) == {"current": True}
    else:
        with pytest.raises(module.rolling.RollingDenied, match="^" + code + "$"):
            run.service(API)
    assert sleeps == expected_sleep and len(calls) == (2 if code is None else 1)


def test_decode_proof_failure_is_outside_retry(port):
    run, outcomes, calls, sleeps, _ = port
    outcomes.append({"malformed": True})
    def decode(_value):
        raise module.rolling.RollingDenied("rolling_source_contract_refused")
    run.decode = decode
    with pytest.raises(module.rolling.RollingDenied, match="^rolling_source_contract_refused$"):
        run.service(API)
    assert len(calls) == 1 and sleeps == []


def test_rpc_proof_refusal_restores_auth_wrapper_without_retry(port):
    run, outcomes, calls, sleeps, _ = port
    original = run.stock.session.request
    def fail():
        run.stock.session.request = lambda *_args, **_kwargs: None
        raise module.rolling.RollingDenied("rolling_native_state_unverified")
    outcomes.append(fail)
    with pytest.raises(module.rolling.RollingDenied, match="^rolling_native_state_unverified$"):
        run.service(API)
    assert len(calls) == 1 and sleeps == [] and run.stock.session.request is original


def test_auth_refresh_retry_restores_same_authorized_wrapper_before_second_get(port):
    run, outcomes, calls, sleeps, _ = port
    original = run.stock.session.request
    def fail_refresh():
        run.stock.session.request = lambda *_args, **_kwargs: pytest.fail("raw auth request must not escape")
        raise TransportError(errors.ConnectTimeout("private"))
    def final():
        assert run.stock.session.request is original
        return {"authenticated": True}
    outcomes.extend((fail_refresh, final))
    assert run.service(API) == {"authenticated": True}
    assert len(calls) == 2 and sleeps == [0.1] and run.stock.session.request is original


@pytest.mark.parametrize("failure", [
    TransportError(errors.SSLError("private")),
    TransportError(errors.ProxyError("private")),
    RefreshError("private"),
    TransportError("untyped private"),
])
def test_auth_failure_restores_wrapper_even_when_no_retry_allowed(port, failure):
    run, outcomes, calls, sleeps, _ = port
    original = run.stock.session.request
    def fail():
        run.stock.session.request = lambda *_args, **_kwargs: None
        raise failure
    outcomes.append(fail)
    with pytest.raises(module.rolling.RollingDenied, match="^rolling_native_read_unavailable$"):
        run.service(API)
    assert len(calls) == 1 and sleeps == [] and run.stock.session.request is original


def test_unexpected_successful_auth_wrapper_drift_is_restored_and_refused(port):
    run, outcomes, calls, sleeps, _ = port
    original = run.stock.session.request
    def changed():
        run.stock.session.request = lambda *_args, **_kwargs: None
        return {"unsafe": True}
    outcomes.append(changed)
    with pytest.raises(module.rolling.RollingDenied, match="^rolling_native_auth_wrapper_changed$"):
        run.service(API)
    assert len(calls) == 1 and sleeps == [] and run.stock.session.request is original


def test_attempt_and_elapsed_budget_never_restart(port):
    run, outcomes, calls, sleeps, clock = port
    outcomes.extend((HttpError(503), HttpError(503), HttpError(503), {"unused": True}))
    with pytest.raises(module.rolling.RollingDenied, match="^rolling_native_read_transient_exhausted_reconcile_only$"):
        run.service(API)
    assert len(calls) == 3 and sleeps == [0.1, 0.2]
    outcomes.clear()
    calls.clear()
    sleeps.clear()
    def late():
        clock[0] += 16
        return {"late": True}
    outcomes.append(late)
    with pytest.raises(module.rolling.RollingDenied, match="^rolling_native_read_deadline_reconcile_only$"):
        run.service(API)
    assert len(calls) == 1 and sleeps == []


@pytest.mark.parametrize("failure", [HttpError(503), errors.ConnectionError("unknown private write")])
def test_patch_unknown_outcome_remains_one_generated_call(port, failure):
    run, _, _, _, _ = port
    writes = []
    def patch(request):
        writes.append(request)
        raise failure
    run.client.projects_locations_services.Patch = patch
    run.messages.RunProjectsLocationsServicesPatchRequest = SimpleNamespace
    run.messages.GoogleCloudRunV2Service = object
    run.encoding = SimpleNamespace(DictToMessage=lambda value, _type: value)
    run.armed, run.sent = None, 0
    with pytest.raises(type(failure)):
        run.patch(API, {"name": NAME, "etag": "inert", "traffic": []}, "traffic")
    assert len(writes) == 1 and run.armed is None


@pytest.mark.parametrize("failure,attempts", [
    (TransportError(errors.ConnectTimeout("private")), 2),
    (TransportError(errors.SSLError("private")), 1),
    (RefreshError("private"), 1),
])
def test_constructor_refresh_retries_only_typed_transient_with_fresh_reader(port, monkeypatch, failure, attempts):
    _, _, _, _, _ = port
    readers = []
    stock = SimpleNamespace(session=SimpleNamespace(close=lambda: None))
    class Reader:
        def __init__(self, sdk, account):
            assert sdk == module.host_auth.SDK and account == module.host_auth.ACCOUNT
            readers.append(self)
        def _http(self):
            if len(readers) == 1:
                raise failure
            return stock
    monkeypatch.setattr(module.native, "SDKGoogleReads", Reader)
    if attempts == 1:
        with pytest.raises(module.rolling.RollingDenied, match="^rolling_native_authentication_unavailable$"):
            module._stock_http()
    else:
        assert module._stock_http() is stock
    assert len(readers) == attempts and len({id(reader) for reader in readers}) == attempts


def test_constructor_retry_cap_and_late_stock_are_bounded(port, monkeypatch):
    _, _, _, sleeps, clock = port
    readers, closed = [], []
    class Reader:
        def __init__(self, *_args):
            readers.append(self)
        def _http(self):
            raise TransportError(errors.ConnectTimeout("private"))
    monkeypatch.setattr(module.native, "SDKGoogleReads", Reader)
    with pytest.raises(module.rolling.RollingDenied, match="^rolling_native_read_transient_exhausted_reconcile_only$"):
        module._stock_http()
    assert len(readers) == 3 and sleeps == [0.1, 0.2]
    def late(_self):
        clock[0] += 16
        return SimpleNamespace(session=SimpleNamespace(close=lambda: closed.append(True)))
    monkeypatch.setattr(Reader, "_http", late)
    with pytest.raises(module.rolling.RollingDenied, match="^rolling_native_read_deadline_reconcile_only$"):
        module._stock_http()
    assert closed == [True]


def test_retry_dispatch_has_no_mutation_or_unreviewed_resource_route(port):
    run, _, calls, sleeps, _ = port
    for kind, name in (("patch", NAME), ("service", NAME + "-other"),
                       ("operation", module.rolling.PARENT + "/operations/../other")):
        with pytest.raises(module.rolling.RollingDenied, match="^rolling_native_read_route_refused$"):
            run._read_get(kind, name)
    assert calls == [] and sleeps == []


@pytest.mark.parametrize("code", [
    "rolling_native_authentication_unavailable", "rolling_native_auth_wrapper_changed",
    "rolling_native_read_deadline_reconcile_only", "rolling_native_read_not_found",
    "rolling_native_read_permission_denied", "rolling_native_read_retry_after_invalid",
    "rolling_native_read_route_refused", "rolling_native_read_transient_exhausted_reconcile_only",
    "rolling_native_read_unauthorized", "rolling_native_read_unavailable",
])
def test_only_typed_fixed_read_refusals_become_actionable_cli_diagnostics(code):
    reviewed = importlib.import_module("unified_credit_schema25_release")
    assert reviewed.failure_response(module.rolling.RollingDenied(code))["code"] == code
    assert reviewed.failure_response(RuntimeError(code))["code"] == "unclassified_failure"
