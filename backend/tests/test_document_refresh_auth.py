"""Dedicated-controller credential and transport boundaries; no provider calls."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlencode

import pytest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("document_refresh_auth_test", ROOT / "infra/gcp/document_refresh_auth.py")
assert spec is not None and spec.loader is not None
auth = importlib.util.module_from_spec(spec)
spec.loader.exec_module(auth)


@pytest.mark.parametrize(("method", "url"), [
    ("PATCH", "https://run.googleapis.com/v2/projects/another/locations/us-central1/services/ai-resume-parser"),
    ("GET", "https://run.googleapis.com.evil.test/v2/" + auth.PARENT + "/services/ai-resume-parser"),
    ("GET", "http://run.googleapis.com/v2/" + auth.PARENT + "/services/ai-resume-parser"),
    ("PATCH", "https://run.googleapis.com/v2/" + auth.PARENT + "/services/hirewiz-employer-worker"),
    ("POST", "https://iam.googleapis.com/v1/projects/" + auth.PROJECT + "/serviceAccounts/" + auth.ACCOUNT + ":setIamPolicy"),
    ("DELETE", "https://run.googleapis.com/v2/" + auth.PARENT + "/jobs/unrelated-job"),
    ("PATCH", "https://run.googleapis.com/v2/" + auth.PARENT + "/jobs/hirewiz-doc-six-0123456789ab"),
    ("DELETE", "https://run.googleapis.com/v2/" + auth.PARENT + "/jobs/hirewiz-doc-six-0123456789ab/executions/synthetic"),
])
def test_other_projects_identities_services_or_mutation_surfaces_are_denied(method, url):
    assert not auth.allowed(method, url)


def test_only_nonce_owned_probe_jobs_and_fixed_service_changes_are_routable():
    base = "https://run.googleapis.com/v2/" + auth.PARENT
    assert auth.allowed("POST", base + "/jobs")
    assert auth.allowed("POST", base + "/jobs/hirewiz-doc-six-0123456789ab:run")
    assert auth.allowed("DELETE", base + "/jobs/hirewiz-doc-six-0123456789ab")
    assert auth.allowed("PATCH", base + "/services/hirewiz-document-inspector")
    assert auth.allowed("GET", base + "/services/hirewiz-document-inspector:getIamPolicy")


def test_failed_rescue_claim_create_is_one_exact_object_and_create_only():
    bucket = auth.PROJECT + "_cloudbuild"
    endpoint = "https://storage.googleapis.com/upload/storage/v1/b/" + bucket + "/o?"
    name = ("hirewiz-document-refresh-source/5436da554bc5437cadccd296f3489bdd/"
            "rescue-transition-40771705772-12852818465.json")
    query = {"uploadType": "media", "name": name, "ifGenerationMatch": "0"}
    assert auth.allowed("POST", endpoint + urlencode(query))
    assert not auth.allowed("POST", endpoint + urlencode({**query, "ifGenerationMatch": "1"}))
    assert not auth.allowed("POST", endpoint + urlencode({**query, "name": name + "-other"}))
    assert not auth.allowed("POST", endpoint + urlencode({**query, "name": name.replace("12852818465", "12852818466")}))


def test_partial_rescue_claim_is_separate_exact_create_only_object():
    bucket = auth.PROJECT + "_cloudbuild"
    endpoint = "https://storage.googleapis.com/upload/storage/v1/b/" + bucket + "/o?"
    name = ("hirewiz-document-refresh-source/5436da554bc5437cadccd296f3489bdd/"
            "partial-transition-40781707554-12858021573.json")
    query = {"uploadType": "media", "name": name, "ifGenerationMatch": "0"}
    assert auth.allowed("POST", endpoint + urlencode(query))
    assert not auth.allowed("POST", endpoint + urlencode({**query, "ifGenerationMatch": "1"}))
    assert not auth.allowed("POST", endpoint + urlencode({**query, "name": name + "-other"}))
    assert not auth.allowed("POST", endpoint + urlencode({**query, "name": name.replace("12858021573", "12858021574")}))


def test_final_rescue_claim_is_third_exact_create_only_object():
    bucket = auth.PROJECT + "_cloudbuild"
    endpoint = "https://storage.googleapis.com/upload/storage/v1/b/" + bucket + "/o?"
    name = ("hirewiz-document-refresh-source/5436da554bc5437cadccd296f3489bdd/"
            "final-transition-40788512340-12860975525.json")
    query = {"uploadType": "media", "name": name, "ifGenerationMatch": "0"}
    assert auth.allowed("POST", endpoint + urlencode(query))
    assert not auth.allowed("POST", endpoint + urlencode({**query, "ifGenerationMatch": "1"}))
    assert not auth.allowed("POST", endpoint + urlencode({**query, "name": name + "-other"}))
    assert not auth.allowed("POST", endpoint + urlencode({**query, "name": name.replace("12860975525", "12860975526")}))


@pytest.mark.parametrize("email", ["human@example.test", "default", "other@project.iam.gserviceaccount.com", None])
def test_adc_cannot_substitute_a_human_or_another_service_identity(monkeypatch, email):
    credential = SimpleNamespace(service_account_email=email, refresh=lambda request: None)
    monkeypatch.setattr(auth.google.auth, "default", lambda **kw: (credential, auth.PROJECT))
    with pytest.raises(auth.IdentityDenied, match="service_identity_unverified"):
        auth.ServiceHttp("123456789012345678901")


@pytest.mark.parametrize(("actual_uid", "disabled"), [("987654321098765432109", False), ("123456789012345678901", True)])
def test_fresh_native_uid_and_enabled_state_must_match_the_provisioned_pin(monkeypatch, actual_uid, disabled):
    credential = SimpleNamespace(service_account_email=auth.ACCOUNT, refresh=lambda request: None)
    monkeypatch.setattr(auth.google.auth, "default", lambda **kw: (credential, auth.PROJECT))
    calls = []
    def identity_read(self, uri, **kwargs):
        calls.append((uri, kwargs))
        return auth.Response(status="200"), json.dumps({"email": auth.ACCOUNT,
            "uniqueId": actual_uid, "disabled": disabled}).encode()
    monkeypatch.setattr(auth.ServiceHttp, "request", identity_read)
    with pytest.raises(auth.IdentityDenied):
        auth.ServiceHttp("123456789012345678901")
    assert len(calls) == 1 and calls[0][1]["_identity_read"] is True


def test_session_ignores_proxy_ca_and_retry_environment(monkeypatch):
    monkeypatch.setenv("HTTPS_PROXY", "https://untrusted.invalid")
    monkeypatch.setenv("REQUESTS_CA_BUNDLE", "/untrusted")
    session = auth.requests.Session()
    auth.ServiceHttp._harden(session)
    assert session.trust_env is False and session.max_redirects == 0
    assert session.verify == auth.certifi.where()
    assert all(adapter.max_retries.total == 0 for adapter in session.adapters.values())
    session.close()


def test_unexpected_session_adapter_is_refused_before_any_request():
    session = auth.requests.Session()
    session.adapters["https://"] = SimpleNamespace(close=lambda: None)
    with pytest.raises(auth.IdentityDenied, match="^document_refresh_transport_adapter_refused$"):
        auth.ServiceHttp._harden(session)
    session.close()


class _ReadResult:
    def __init__(self, status: int):
        self.status_code = status
        self.headers = {"content-type": "application/json"}
        self.raw = SimpleNamespace(read=lambda *args, **kwargs: b"{}")
        self.closed = False

    def close(self):
        self.closed = True


class _ReadSession:
    def __init__(self, outcomes):
        self.outcomes = iter(outcomes)
        self.calls = []
        self.responses = []

    def request(self, method, uri, **kwargs):
        self.calls.append((method, uri))
        outcome = next(self.outcomes)
        if isinstance(outcome, Exception):
            raise outcome
        response = _ReadResult(outcome)
        self.responses.append(response)
        return response


@pytest.mark.parametrize(("method", "uri", "outcomes"), [
    ("GET", "https://run.googleapis.com/v2/" + auth.PARENT + "/services/ai-resume-parser", [503, 200]),
    ("GET", "https://cloudbuild.googleapis.com/v1/projects/" + auth.PROJECT
     + "/locations/us-central1/builds/01234567-1234-1234-1234-0123456789ab", [502, 200]),
    ("GET", "https://storage.googleapis.com/storage/v1/b/" + auth.PROJECT
     + "_cloudbuild/o/hirewiz-document-refresh-source/5436da554bc5437cadccd296f3489bdd/"
     + "final-transition-40788512340-12860975525.json", [504, 200]),
    ("POST", "https://logging.googleapis.com/v2/entries:list", [429, 200]),
    ("GET", "https://run.googleapis.com/v2/" + auth.PARENT + "/services/ai-resume-parser",
     [auth.requests.exceptions.ConnectionError("private transport detail"), 200]),
    ("GET", "https://run.googleapis.com/v2/" + auth.PARENT + "/services/ai-resume-parser",
     [auth.requests.exceptions.Timeout("private timeout detail"), 200]),
    ("GET", "https://run.googleapis.com/v2/" + auth.PARENT + "/services/ai-resume-parser",
     [auth.ProtocolError("private truncated stream"), 200]),
    ("GET", "https://run.googleapis.com/v2/" + auth.PARENT + "/services/ai-resume-parser",
     [auth.ReadTimeoutError(None, "fixed-url", "private slow stream"), 200]),
])
def test_only_classified_native_reads_retry_without_exposing_vendor_text(monkeypatch, method, uri, outcomes):
    monkeypatch.setattr(auth.time, "sleep", lambda _: None)
    client = auth.ServiceHttp.__new__(auth.ServiceHttp)
    client._verified = True
    client.session = _ReadSession(outcomes)
    response, raw = client.request(uri, method=method, redirections=0)
    assert response.status == 200 and raw == b"{}"
    assert len(client.session.calls) == 2
    assert all(row.closed for row in client.session.responses)


@pytest.mark.parametrize(("method", "uri", "status"), [
    ("PATCH", "https://run.googleapis.com/v2/" + auth.PARENT + "/services/ai-resume-parser", 503),
    ("POST", "https://run.googleapis.com/v2/" + auth.PARENT + "/jobs", 503),
    ("POST", "https://storage.googleapis.com/upload/storage/v1/b/" + auth.PROJECT
     + "_cloudbuild/o?" + urlencode({"uploadType": "media", "name": "hirewiz-document-refresh-source/"
         "5436da554bc5437cadccd296f3489bdd/final-transition-40788512340-12860975525.json",
         "ifGenerationMatch": "0"}), 503),
    ("GET", "https://run.googleapis.com/v2/" + auth.PARENT + "/services/ai-resume-parser", 403),
])
def test_unknown_or_mutating_outcome_is_never_retried(method, uri, status):
    client = auth.ServiceHttp.__new__(auth.ServiceHttp)
    client._verified = True
    client.session = _ReadSession([status])
    response, _ = client.request(uri, method=method, redirections=0)
    assert response.status == status and len(client.session.calls) == 1


def test_read_retry_deadline_has_fixed_code_and_never_retries_write(monkeypatch):
    monkeypatch.setattr(auth, "READ_ONLY_RETRY_SECONDS", 0)
    client = auth.ServiceHttp.__new__(auth.ServiceHttp)
    client._verified = True
    client.session = _ReadSession([503])
    uri = "https://run.googleapis.com/v2/" + auth.PARENT + "/services/ai-resume-parser"
    with pytest.raises(auth.IdentityDenied, match="^document_refresh_read_only_transient_exhausted_reconcile_only$"):
        client.request(uri, redirections=0)
    assert len(client.session.calls) == 1 and client.session.responses[0].closed


@pytest.mark.parametrize(("method", "uri"), [
    ("PATCH", "https://run.googleapis.com/v2/" + auth.PARENT + "/services/ai-resume-parser"),
    ("POST", "https://storage.googleapis.com/upload/storage/v1/b/" + auth.PROJECT
     + "_cloudbuild/o?" + urlencode({"uploadType": "media", "name": "hirewiz-document-refresh-source/"
         "5436da554bc5437cadccd296f3489bdd/final-transition-40788512340-12860975525.json",
         "ifGenerationMatch": "0"})),
])
def test_uncertain_write_transport_never_retries(method, uri):
    client = auth.ServiceHttp.__new__(auth.ServiceHttp)
    client._verified = True
    client.session = _ReadSession([auth.requests.exceptions.ConnectionError("private uncertain send")])
    with pytest.raises(auth.IdentityDenied, match="^document_refresh_transport_unavailable_reconcile_only$"):
        client.request(uri, method=method, redirections=0)
    assert len(client.session.calls) == 1


def test_nontransient_request_error_stops_without_retry(monkeypatch):
    monkeypatch.setattr(auth.time, "sleep", lambda _: pytest.fail("must not wait"))
    client = auth.ServiceHttp.__new__(auth.ServiceHttp)
    client._verified = True
    client.session = _ReadSession([auth.requests.RequestException("private permanent detail")])
    uri = "https://run.googleapis.com/v2/" + auth.PARENT + "/services/ai-resume-parser"
    with pytest.raises(auth.IdentityDenied, match="^document_refresh_transport_unavailable_reconcile_only$"):
        client.request(uri, redirections=0)
    assert len(client.session.calls) == 1


def test_identity_refresh_retries_only_known_transport_failure(monkeypatch):
    calls = []
    def refresh(request):
        calls.append("refresh")
        if len(calls) == 1:
            raise auth.TransportError("private WIF transport detail")
    credential = SimpleNamespace(service_account_email=auth.ACCOUNT, refresh=refresh)
    monkeypatch.setattr(auth.google.auth, "default", lambda **kw: (credential, auth.PROJECT))
    monkeypatch.setattr(auth.requests, "Session", lambda: SimpleNamespace(close=lambda: None))
    monkeypatch.setattr(auth, "Request", lambda **kw: None)
    monkeypatch.setattr(auth, "AuthorizedSession", lambda *args, **kw: SimpleNamespace(close=lambda: None))
    monkeypatch.setattr(auth.ServiceHttp, "_harden", staticmethod(lambda session: None))
    monkeypatch.setattr(auth.time, "sleep", lambda _: None)
    monkeypatch.setattr(auth.ServiceHttp, "request", lambda self, uri, **kw: (
        auth.Response(status="200"), json.dumps({"email": auth.ACCOUNT,
            "uniqueId": "123456789012345678901", "disabled": False}).encode()))
    client = auth.ServiceHttp("123456789012345678901")
    assert calls == ["refresh", "refresh"] and client.uid == "123456789012345678901"
    client.close()


@pytest.mark.parametrize("account", [auth.ACCOUNT, auth.WORKER_ACCOUNT, auth.COORDINATOR_ACCOUNT])
def test_key_inventory_cannot_create_private_credentials(account):
    root = "https://iam.googleapis.com/v1/projects/" + auth.PROJECT + "/serviceAccounts/" + account
    assert auth.allowed("GET", root) and auth.allowed("GET", root + "/keys")
    assert auth.allowed("POST", root + ":getIamPolicy")
    assert not auth.allowed("POST", root) and not auth.allowed("POST", root + "/keys")


def test_coordinator_is_an_exact_third_consumer_but_revision_patch_is_never_authorized():
    root = "https://run.googleapis.com/v2/" + auth.PARENT + "/services/hirewiz-resume-coordinator"
    assert auth.allowed("PATCH", root)
    assert auth.allowed("GET", root + "/revisions/hirewiz-resume-coordinator-00001-owned")
    assert not auth.allowed("PATCH", root + "/revisions/hirewiz-resume-coordinator-00001-owned")


@pytest.mark.parametrize(("method", "path"), [("PATCH", "/v2/" + auth.PARENT + "/services/hirewiz-document-inspector"),
    ("POST", "/v2/" + auth.PARENT + "/jobs"), ("POST", "/v2/" + auth.PARENT + "/jobs/hirewiz-doc-six-0123456789ab:run"),
    ("DELETE", "/v2/" + auth.PARENT + "/jobs/hirewiz-doc-six-0123456789ab")])
def test_release_proof_transport_never_mutates_cloud_run(method, path):
    assert not auth.release_allowed(method, "https://run.googleapis.com" + path)


def test_release_proof_cannot_publish_build_or_log_but_retains_read_policy_endpoints():
    assert not auth.release_allowed("POST", "https://cloudbuild.googleapis.com/v1/" + auth.PARENT + "/builds")
    assert not auth.release_allowed("POST", "https://logging.googleapis.com/v2/entries:write")
    assert auth.release_allowed("POST", "https://logging.googleapis.com/v2/entries:list")
    assert auth.release_allowed("POST", "https://iam.googleapis.com/v1/projects/" + auth.PROJECT
        + "/serviceAccounts/" + auth.WORKER_ACCOUNT + ":getIamPolicy")
    assert auth.release_allowed("GET", "https://run.googleapis.com/v2/" + auth.PARENT + "/services/hirewiz-document-inspector")


def test_exact_updater_role_definition_is_readable_but_other_roles_or_role_writes_are_not():
    uri = "https://iam.googleapis.com/v1/" + auth.MAINTENANCE_ROLE
    assert auth.allowed("GET", uri) and auth.release_allowed("GET", uri)
    for method in ("POST", "PATCH", "DELETE"):
        assert not auth.allowed(method, uri)
    assert not auth.allowed("GET", uri + "-other")
