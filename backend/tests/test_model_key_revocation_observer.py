"""Scoped synthetic native responses: no model, cloud, database or credential calls."""

import io
import json
import ssl
import urllib.request
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from scripts import model_key_revocation_observer as observer

PARENT = "projects/157225724590/locations/global"
NAME = PARENT + "/keys/122de5ba-8ec6-498f-9e97-777188d18fcf"
OTHER = PARENT + "/keys/4c39725c-a68a-44c7-8f5f-2c5ddfaa9ee4"
VALUE = "synthetic-never-a-real-key"
TOKEN = "synthetic-never-a-real-bearer"


def metadata(name=NAME, deleted=True):
    result = {"name": name, "uid": "synthetic_uid", "etag": "synthetic-etag"}
    if deleted:
        result["deleteTime"] = (datetime.now(UTC) - timedelta(minutes=1)).isoformat()
    return result


class FakeNative:
    def __init__(self, deleted=True):
        self.row = metadata(deleted=deleted)
        self.lookup = {"parent": PARENT, "name": NAME}
        self.pages = [{"keys": [deepcopy(self.row)]}]
        self.fresh = deepcopy(self.row)
        self.calls: list[tuple[str, dict[str, str]]] = []

    def get(self, path, params, timeout):
        assert 0 < timeout <= 5
        self.calls.append((path, dict(params)))
        if path == "keys:lookupKey":
            return deepcopy(self.lookup)
        if path == PARENT + "/keys":
            assert params["showDeleted"] == "true"
            assert params["pageSize"] == "100"
            index = int(params.get("pageToken", "0"))
            return deepcopy(self.pages[index])
        assert path == NAME and not params
        return deepcopy(self.fresh)


def run(transport=None):
    return observer.inspect_google_credentials(
        [observer.GoogleCredential("old_model_1", VALUE)], transport or FakeNative(),
    )


def test_deleted_positive_is_native_bound_and_never_global_permission():
    transport = FakeNative()
    result = run(transport)
    assert result["all_inspected_google_keys_deleted"] is True
    assert result["keys"][0]["name"] == NAME
    assert result["release_permission"] is False
    assert result["scope"] == "supplied_google_credentials_only"
    assert "historical_credential_inventory_completeness" in result["unverified"]
    assert "replacement_credential_isolation" in result["unverified"]
    assert transport.calls == [("keys:lookupKey", {"keyString": VALUE}),
                               (PARENT + "/keys", {"showDeleted": "true", "pageSize": "100"}),
                               (NAME, {})]
    assert VALUE not in json.dumps(result) and TOKEN not in json.dumps(result)
    assert VALUE not in repr(observer.GoogleCredential("old_model_1", VALUE))


def test_active_key_is_observed_as_active_not_revoked():
    result = run(FakeNative(deleted=False))
    assert result["all_inspected_google_keys_deleted"] is False
    assert result["keys"][0]["delete_time"] is None


def test_pagination_reaches_later_page_and_does_not_truncate():
    transport = FakeNative()
    transport.pages = [{"keys": [metadata(OTHER)], "nextPageToken": "1"},
                       {"keys": [deepcopy(transport.row)]}]
    result = run(transport)
    assert result["all_inspected_google_keys_deleted"] is True
    assert result["projects"][0]["native_listed_key_count"] == 2
    assert transport.calls[2][1]["pageToken"] == "1"


@pytest.mark.parametrize("lookup", [
    {"parent": PARENT}, {"parent": PARENT, "name": ""},
    {"parent": "projects/999/locations/global", "name": NAME},
    {"parent": PARENT, "name": "https://attacker.test/key"},
])
def test_missing_purged_or_mismatched_resource_is_unresolved(lookup):
    transport = FakeNative()
    transport.lookup = lookup
    with pytest.raises(observer.ObservationDenied, match="native_credential_resource_unresolved"):
        run(transport)


@pytest.mark.parametrize("page", [
    {"keys": {}, "nextPageToken": ""}, {"keys": [], "nextPageToken": None},
    {"keys": [metadata()] * 101}, {"keys": [], "nextPageToken": "x" * 4097},
])
def test_invalid_pagination_refuses(page):
    transport = FakeNative()
    transport.pages = [page]
    with pytest.raises(observer.ObservationDenied, match="native_pagination_invalid"):
        run(transport)


def test_repeated_page_token_refuses():
    transport = FakeNative()
    transport.pages = [{"keys": [], "nextPageToken": "1"},
                       {"keys": [], "nextPageToken": "1"}]
    with pytest.raises(observer.ObservationDenied, match="native_pagination_invalid"):
        run(transport)


def test_page_bound_refuses_instead_of_accepting_partial(monkeypatch):
    monkeypatch.setattr(observer, "MAX_PAGES", 1)
    transport = FakeNative()
    transport.pages = [{"keys": [metadata()], "nextPageToken": "1"}]
    with pytest.raises(observer.ObservationDenied, match="native_page_bound_exceeded"):
        run(transport)


def test_duplicate_resource_across_pages_refuses():
    transport = FakeNative()
    transport.pages = [{"keys": [metadata()], "nextPageToken": "1"},
                       {"keys": [deepcopy(transport.row)]}]
    with pytest.raises(observer.ObservationDenied, match="native_duplicate_key_resource"):
        run(transport)


def test_missing_inspected_key_in_full_listing_refuses():
    transport = FakeNative()
    transport.pages = [{"keys": [metadata(OTHER)]}]
    with pytest.raises(observer.ObservationDenied, match="native_bound_key_missing"):
        run(transport)


@pytest.mark.parametrize("change", [{"etag": "changed"}, {"uid": "changed"},
                                   {"deleteTime": None}, {"name": OTHER}])
def test_independent_get_detects_reactivation_or_identity_change(change):
    transport = FakeNative()
    transport.fresh.update(change)
    with pytest.raises(observer.ObservationDenied, match="native_key_changed_during_observation"):
        run(transport)


def test_nanosecond_deletion_time_mismatch_is_not_collapsed_to_microseconds():
    transport = FakeNative()
    transport.pages[0]["keys"][0]["deleteTime"] = "2026-10-01T00:00:00.000000001Z"
    transport.fresh["deleteTime"] = "2026-10-01T00:00:00.000000999Z"
    with pytest.raises(observer.ObservationDenied, match="native_key_changed_during_observation"):
        run(transport)


def test_equivalent_timestamp_offsets_and_fraction_precision_are_normalized():
    transport = FakeNative()
    transport.pages[0]["keys"][0]["deleteTime"] = "2026-10-01T01:00:00.01+01:00"
    transport.fresh["deleteTime"] = "2026-10-01T00:00:00.010000000Z"
    assert run(transport)["keys"][0]["delete_time"] == "2026-10-01T00:00:00.010000000Z"


@pytest.mark.parametrize("change", [
    {"name": "projects/999/locations/global/keys/other"}, {"uid": ""}, {"etag": ""},
    {"etag": "contains spaces"}, {"keyString": VALUE},
    {"deleteTime": "not-a-time"}, {"deleteTime": "2020-01-01T00:00:00"},
    {"deleteTime": "2099-01-01T00:00:00Z"}, {"deleteTime": 123},
])
def test_invalid_metadata_and_key_value_disclosure_refused(change):
    transport = FakeNative()
    transport.pages[0]["keys"][0].update(change)
    with pytest.raises(observer.ObservationDenied):
        run(transport)


def test_key_bound_refuses(monkeypatch):
    monkeypatch.setattr(observer, "MAX_KEYS", 1)
    transport = FakeNative()
    transport.pages = [{"keys": [metadata(), metadata(OTHER)]}]
    with pytest.raises(observer.ObservationDenied, match="native_key_bound_exceeded"):
        run(transport)


def test_project_bound_refuses(monkeypatch):
    monkeypatch.setattr(observer, "MAX_PROJECTS", 0)
    with pytest.raises(observer.ObservationDenied, match="native_project_bound_exceeded"):
        run()


def test_slow_observation_cannot_return_a_green_result(monkeypatch):
    values = iter([0, 0, 31])
    monkeypatch.setattr(observer.time, "monotonic", lambda: next(values))
    with pytest.raises(observer.ObservationDenied, match="native_observation_window_exceeded"):
        run()


@pytest.mark.parametrize("credentials", [[], [observer.GoogleCredential("bad label", VALUE)],
    [observer.GoogleCredential("old", "")], [observer.GoogleCredential("old", "has spaces")],
    [observer.GoogleCredential("old", VALUE)] * 2,
    [observer.GoogleCredential(str(i), VALUE) for i in range(33)],
])
def test_invalid_or_empty_subset_refuses(credentials):
    with pytest.raises(observer.ObservationDenied, match="credential_input_invalid"):
        observer.inspect_google_credentials(credentials, FakeNative())


def test_library_rejects_key_value_in_any_caller_label():
    with pytest.raises(observer.ObservationDenied, match="credential_label_reflection_refused"):
        observer.inspect_google_credentials([observer.GoogleCredential("synthetic_key", "synthetic_key")], FakeNative())


def test_library_rejects_native_etag_reflecting_key_value():
    transport = FakeNative()
    transport.pages[0]["keys"][0]["etag"] = VALUE
    transport.fresh["etag"] = VALUE
    with pytest.raises(observer.ObservationDenied, match="native_secret_reflection_refused"):
        run(transport)


class FakeResponse(io.BytesIO):
    status = 200

    def __init__(self, raw, url):
        super().__init__(raw)
        self.url = url

    def geturl(self):
        return self.url


class FakeOpener:
    def __init__(self, raw=b"{}", error=None, redirect=False):
        self.raw = raw
        self.error = error
        self.redirect = redirect
        self.requests: list[Any] = []

    def open(self, request, timeout):
        self.requests.append(request)
        if self.error:
            raise self.error
        url = "https://attacker.test" if self.redirect else request.full_url
        return FakeResponse(self.raw, url)


def native_with(opener):
    transport = observer.NativeGoogleTransport(TOKEN)
    transport._opener = opener
    return transport


def test_ambient_tls_session_key_logging_is_never_enabled_or_created(monkeypatch, tmp_path):
    keylog = tmp_path / "must-not-be-created.keylog"
    monkeypatch.setenv("SSLKEYLOGFILE", str(keylog))
    monkeypatch.setenv("SSL_CERT_FILE", str(tmp_path / "missing-ca"))
    monkeypatch.setenv("HTTPS_PROXY", "http://attacker.test")
    transport = observer.NativeGoogleTransport(TOKEN)
    https = next(handler for handler in transport._opener.handlers
                 if isinstance(handler, urllib.request.HTTPSHandler))
    assert https._context.check_hostname is True
    assert https._context.verify_mode == ssl.CERT_REQUIRED
    assert https._context.keylog_filename is None
    assert not keylog.exists()
    assert not any(isinstance(handler, urllib.request.ProxyHandler) and handler.proxies
                   for handler in transport._opener.handlers)


def test_native_transport_fixed_origin_get_and_authentication():
    opener = FakeOpener(b'{"parent":"projects/1/locations/global"}')
    result = native_with(opener).get("keys:lookupKey", {"keyString": VALUE}, 5)
    assert result["parent"] == "projects/1/locations/global"
    request = opener.requests[0]
    assert request.full_url.startswith(observer.HOST + "keys:lookupKey?")
    assert request.get_method() == "GET" and request.data is None
    assert request.get_header("Authorization") == "Bearer " + TOKEN


@pytest.mark.parametrize("path,params,timeout", [
    ("https://attacker.test", {}, 5), (NAME + ":delete", {}, 5),
    (NAME, {"keyString": VALUE}, 5), (NAME, {}, 6),
    (PARENT + "/keys", {"showDeleted": "false", "pageSize": "100"}, 5),
    ("../keys:lookupKey", {"keyString": VALUE}, 5),
])
def test_native_mutation_host_path_and_request_shape_refused(path, params, timeout):
    opener = FakeOpener()
    with pytest.raises(observer.ObservationDenied, match="native_request_shape_refused"):
        native_with(opener).get(path, params, timeout)
    assert not opener.requests


def test_redirect_refused_without_following():
    with pytest.raises(observer.ObservationDenied, match="native_response_origin"):
        native_with(FakeOpener(redirect=True)).get(NAME, {}, 5)
    with pytest.raises(observer.ObservationDenied, match="native_redirect_refused"):
        observer.NoRedirect().redirect_request(None, None, 302, None, None, "https://attacker.test")


@pytest.mark.parametrize("raw", [b"not json", b"[]", b'{"name":"one","name":"two"}',
                                 b"x" * (observer.MAX_PAYLOAD + 1)])
def test_invalid_duplicate_or_overbound_http_json_refused(raw):
    with pytest.raises(observer.ObservationDenied):
        native_with(FakeOpener(raw)).get(NAME, {}, 5)


def test_error_sanitization_does_not_echo_key_url_or_bearer():
    opener = FakeOpener(error=RuntimeError(VALUE + TOKEN))
    with pytest.raises(observer.ObservationDenied) as caught:
        native_with(opener).get("keys:lookupKey", {"keyString": VALUE}, 5)
    assert str(caught.value) == "native_metadata_unavailable"
    assert VALUE not in str(caught.value) and TOKEN not in str(caught.value)


@pytest.mark.parametrize("reflected", [VALUE, TOKEN])
def test_native_response_cannot_reflect_lookup_secret_or_bearer(reflected):
    raw = json.dumps({"native": reflected}).encode()
    with pytest.raises(observer.ObservationDenied, match="native_secret_reflection_refused"):
        native_with(FakeOpener(raw)).get("keys:lookupKey", {"keyString": VALUE}, 5)


@pytest.mark.parametrize("deleted,expected", [(True, 0), (False, 65)])
def test_cli_has_positive_subset_success_and_active_refusal(monkeypatch, capsys, deleted, expected):
    alias = "HIREWIZ_FENCE_GOOGLE_KEY_OLD_1"
    monkeypatch.setenv(alias, VALUE)
    monkeypatch.setenv("HIREWIZ_FENCE_OBSERVER_ACCESS_TOKEN", TOKEN)
    monkeypatch.setattr(observer, "NativeGoogleTransport", lambda token: FakeNative(deleted=deleted))
    monkeypatch.setattr(observer.sys, "argv", ["observer", "--google-key-env", alias])
    assert observer.main() == expected
    output = capsys.readouterr().out
    assert VALUE not in output and TOKEN not in output
    assert json.loads(output)["release_permission"] is False


def test_cli_does_not_accept_resource_file_or_revocation_boolean(monkeypatch, capsys):
    monkeypatch.setattr(observer.sys, "argv", ["observer", "--google-key-env", "RAZORPAY_KEY_SECRET"])
    assert observer.main() == 65
    assert json.loads(capsys.readouterr().out)["reason"] == "credential_environment_alias_invalid"
