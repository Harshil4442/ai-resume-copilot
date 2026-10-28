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
