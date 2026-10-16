"""Dedicated maintenance identity; no retained human login or private keys.

Standard Google metadata/WIF credentials must identify the exact configured
maintenance service account. Its independently read native UID must match the
root-provisioned deployment pin. No production action occurs at import.
"""
from __future__ import annotations

import json
import re
from typing import Any
from urllib.parse import unquote, urlsplit

import certifi
import google.auth
import requests
from google.auth.transport.requests import AuthorizedSession, Request

PROJECT = "ai-resume-parser-482412"
REGION = "us-central1"
PARENT = f"projects/{PROJECT}/locations/{REGION}"
ACCOUNT = f"hirewiz-document-refresh@{PROJECT}.iam.gserviceaccount.com"
WORKER_ACCOUNT = f"hirewiz-document-inspector@{PROJECT}.iam.gserviceaccount.com"
COORDINATOR_ACCOUNT = f"hirewiz-resume-coordinator@{PROJECT}.iam.gserviceaccount.com"
CALLERS = ("ai-resume-parser", "hirewiz-analysis-worker", "hirewiz-resume-coordinator")
SERVICES = ("hirewiz-document-inspector", *CALLERS)
MAX_BODY = 1024 * 1024


class IdentityDenied(RuntimeError):
    pass


def need(value: Any, code: str = "document_refresh_service_identity_unverified") -> None:
    if not value:
        raise IdentityDenied(code)


class Response(dict):
    """The small httplib2-compatible envelope used by official SDK clients."""
    @property
    def status(self) -> int:
        return int(self["status"])

    @property
    def reason(self) -> str:
        return ""


def allowed(method: str, uri: str) -> bool:
    """Only fixed Google projects/resources; no redirect, user origin or token API."""
    target = urlsplit(uri)
    if (target.scheme != "https" or target.username is not None or target.password is not None
            or target.port is not None or target.fragment or method not in {"GET", "POST", "PATCH", "DELETE"}):
        return False
    path = unquote(target.path)
    if target.hostname == "run.googleapis.com":
        prefix = "/v2/" + PARENT
        service = any(path == prefix + "/services/" + name for name in SERVICES)
        revision = any(re.fullmatch(re.escape(prefix + "/services/" + name + "/revisions/")
                       + r"[a-z0-9-]{1,63}", path) for name in SERVICES)
        if path == prefix + "/services/hirewiz-document-inspector:getIamPolicy":
            return method == "GET"
        job = prefix + r"/jobs/hirewiz-doc-six-[a-f0-9]{12}"
        jobs = (method in {"GET", "DELETE"} and re.fullmatch(job, path)
                or method == "POST" and re.fullmatch(job + r":run", path)
                or method == "GET" and re.fullmatch(job + r"/executions(?:/[a-z0-9-]{1,63})?", path))
        operations = re.fullmatch(re.escape(prefix) + r"/operations(?:/[A-Za-z0-9._-]{1,200})?", path)
        return bool(service and method in {"GET", "PATCH"}
                    or revision and method == "GET" or jobs or operations and method == "GET"
                    or path == prefix + "/jobs" and method == "POST")
    if target.hostname == "iam.googleapis.com":
        return any(method == "GET" and path in {
            "/v1/projects/" + PROJECT + "/serviceAccounts/" + account,
            "/v1/projects/" + PROJECT + "/serviceAccounts/" + account + "/keys",
        } or method == "POST" and path == "/v1/projects/" + PROJECT + "/serviceAccounts/"
                   + account + ":getIamPolicy" for account in (ACCOUNT, WORKER_ACCOUNT, COORDINATOR_ACCOUNT))
    if target.hostname == "cloudresourcemanager.googleapis.com":
        return method in {"GET", "POST"} and path in {
            "/v1/projects/" + PROJECT + ":getIamPolicy", "/v1/projects/" + PROJECT,
            "/v3/projects/" + PROJECT}
    if target.hostname == "cloudbuild.googleapis.com":
        return bool(method == "POST" and path == "/v1/" + PARENT + "/builds"
            or method == "GET" and re.fullmatch(
                r"/v1/projects/(?:ai-resume-parser-482412|157225724590)/locations/us-central1/builds/[a-f0-9-]{36}", path))
    if target.hostname == "logging.googleapis.com":
        return method == "POST" and path in {"/v2/entries:list", "/v2/entries:write"}
    if target.hostname == "storage.googleapis.com":
        if method == "POST" and path == "/upload/storage/v1/b/" + PROJECT + "_cloudbuild/o":
            from urllib.parse import parse_qs
            try:
                query = parse_qs(target.query, strict_parsing=True)
            except ValueError:
                return False
            return set(query) == {"uploadType", "name", "ifGenerationMatch"} and query["uploadType"] == ["media"] \
                and query["ifGenerationMatch"] == ["0"] and len(query["name"]) == 1 and bool(re.fullmatch(
                    r"hirewiz-document-refresh-source/[a-f0-9]{32}/source.tar.gz", query["name"][0]))
        return method == "GET" and path.startswith(
            ("/storage/v1/b/" + PROJECT + "_cloudbuild/o/hirewiz-document-refresh-source/",
             "/download/storage/v1/b/" + PROJECT + "_cloudbuild/o/hirewiz-document-refresh-source/"))
    return False


class ServiceHttp:
    """Official clients may use this transport; mutations have zero retry budget."""
    def __init__(self, expected_uid: str):
        need(type(expected_uid) is str and re.fullmatch(r"[1-9][0-9]{20}", expected_uid))
        credentials, project = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
        need(project in (None, PROJECT))
        auth_session = requests.Session()
        self._harden(auth_session)
        auth_request = Request(session=auth_session)
        credentials.refresh(lambda *a, **kw: auth_request(*a, **{**kw, "timeout": 5}))
        need(getattr(credentials, "service_account_email", None) == ACCOUNT)
        self.session = AuthorizedSession(credentials, auth_request=auth_request, max_refresh_attempts=0)
        self._auth_session = auth_session
        self._harden(self.session)
        self.connections: dict[str, Any] = {}
        self._verified = False
        response, raw = self.request("https://iam.googleapis.com/v1/projects/" + PROJECT
            + "/serviceAccounts/" + ACCOUNT, method="GET", _identity_read=True)
        need(response.status == 200)
        value = json.loads(raw)
        need(value.get("email") == ACCOUNT and value.get("uniqueId") == expected_uid
             and value.get("disabled", False) is False)
        self.uid, self._verified = expected_uid, True

    @staticmethod
    def _harden(session: requests.Session) -> None:
        session.trust_env, session.max_redirects, session.verify = False, 0, certifi.where()
        for adapter in session.adapters.values():
            adapter.max_retries = adapter.max_retries.__class__(total=0, connect=0, read=0, redirect=0, status=0)

    def request(self, uri: str, method: str = "GET", body: Any = None, headers: Any = None,
                redirections: int = 0, _identity_read: bool = False, **kwargs: Any) -> tuple[Response, bytes]:
        need((self._verified or _identity_read and method == "GET"
              and uri == "https://iam.googleapis.com/v1/projects/" + PROJECT + "/serviceAccounts/" + ACCOUNT)
             and allowed(method, uri) and redirections == 0, "document_refresh_transport_scope_refused")
        need(body is None or type(body) in (bytes, str), "document_refresh_transport_body_refused")
        need(body is None or len(body) <= MAX_BODY, "document_refresh_transport_body_refused")
        try:
            response = self.session.request(method, uri, data=body, headers=headers or {},
                timeout=(5, 30), allow_redirects=False, stream=True)
            try:
                raw = response.raw.read(MAX_BODY + 1, decode_content=True)
                need(len(raw) <= MAX_BODY and not 300 <= response.status_code < 400,
                     "document_refresh_transport_response_refused")
                result = Response({**dict(response.headers), "status": str(response.status_code)})
                return result, raw
            finally:
                response.close()
        except requests.RequestException:
            raise IdentityDenied("document_refresh_transport_unavailable_reconcile_only") from None

    def close(self) -> None:
        self.session.close()
        self._auth_session.close()


RELEASE_ACCOUNT = "harshilyogeshbhai886@gmail.com"


def release_allowed(method: str, uri: str) -> bool:
    """The same finite proof resources; never Build/Create/Run/Delete/PATCH/log-write."""
    if not allowed(method, uri):
        return False
    target = urlsplit(uri)
    return method == "GET" or method == "POST" and (
        target.hostname in {"iam.googleapis.com", "cloudresourcemanager.googleapis.com"}
        and target.path.endswith(":getIamPolicy")
        or target.hostname == "logging.googleapis.com" and target.path == "/v2/entries:list")


class ReleaseReadOnlyHttp:
    """Existing normal retained SDK manager, explicit read-only proof entrypoint.

    Its original SDKGoogleReads manager/configuration checks are unchanged. This
    adapter neither loads ADC nor permits selecting another account/transport.
    It cannot Create/Run/Delete a probe, publish an image or PATCH a service.
    """
    def __init__(self):
        import shutil
        import subprocess
        import sys
        from pathlib import Path

        executable = shutil.which("gcloud")
        need(executable is not None, "document_refresh_release_sdk_unavailable")
        assert isinstance(executable, str)
        result = subprocess.run([executable, "info", "--format=value(installation.sdk_root)"],
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            check=False, timeout=5)
        need(result.returncode == 0 and len(result.stdout) <= 4096,
             "document_refresh_release_sdk_unavailable")
        root = Path(result.stdout.decode().strip())
        need(root.is_absolute() and (root / "lib/googlecloudsdk").is_dir()
             and (root / "lib/third_party").is_dir(), "document_refresh_release_sdk_unavailable")
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from native_fresh_fence import SDKGoogleReads

        self._manager = SDKGoogleReads(root / "lib", RELEASE_ACCOUNT)
        # Constructing its real stock HTTP transport performs the original
        # canonical account/project/logging/CA/proxy/impersonation checks.
        self._transport = self._manager._http()
        self.connections: dict[str, Any] = {}

    def request(self, uri: str, method: str = "GET", body: Any = None,
                headers: Any = None, redirections: int = 0, **kwargs: Any) -> tuple[Any, bytes]:
        need(release_allowed(method, uri) and redirections == 0
             and (body is None or type(body) in (bytes, str) and len(body) <= MAX_BODY),
             "document_refresh_release_read_only_route_refused")
        try:
            response, raw = self._transport.request(uri, method=method, body=body,
                headers=headers or {}, redirections=0)
        except Exception:  # noqa: BLE001 - raw auth/HTTP errors can contain credentials.
            raise IdentityDenied("document_refresh_release_read_unavailable") from None
        need(type(raw) is bytes and len(raw) <= MAX_BODY and not 300 <= int(response.status) < 400,
             "document_refresh_release_read_response_refused")
        return response, raw

    def close(self) -> None:
        self._transport.session.close()
