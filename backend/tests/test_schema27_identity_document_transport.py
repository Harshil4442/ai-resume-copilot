"""Real promotion boundary construction with only the SDK wire intercepted."""
from __future__ import annotations

import json
import sys
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest

from infra.gcp import rolling_native
from infra.gcp import schema27_api_identity_promotion as promotion
from infra.gcp import schema27_mixed_document_proof as documents


@pytest.fixture
def native_boundary(monkeypatch):
    calls, delegated = [], []

    def response(method, url, **kwargs):
        calls.append((method, url, kwargs))
        resource = url.removeprefix("https://run.googleapis.com/v2/")
        body = {"name": resource, "etag": "unchanged-native-etag", "native_extra": ["retain"],
                "template": {"labels": {"hirewiz-source": "a" * 40}}}
        return SimpleNamespace(status_code=200, content=json.dumps(body).encode())

    def http(reader):
        assert reader.account == rolling_native.host_auth.ACCOUNT
        assert reader.sdk_library == rolling_native.host_auth.SDK
        session = SimpleNamespace(request=response, send=response, adapters={}, close=lambda: None)
        return SimpleNamespace(session=session, connections={})

    # The generated client does not send requests during construction. Supplying
    # its API surface keeps this constructor regression independent of SDK install.
    for name in ("apitools", "apitools.base", "apitools.base.py", "googlecloudsdk",
                 "googlecloudsdk.generated_clients", "googlecloudsdk.generated_clients.apis",
                 "googlecloudsdk.generated_clients.apis.run", "googlecloudsdk.generated_clients.apis.run.v2"):
        module = ModuleType(name)
        module.__path__ = []
        monkeypatch.setitem(sys.modules, name, module)
    encoding: Any = ModuleType("apitools.base.py.encoding")
    encoding.MessageToDict = lambda value: value
    exceptions: Any = ModuleType("apitools.base.py.exceptions")
    exceptions.HttpError = type("HttpError", (Exception,), {})
    client: Any = ModuleType("googlecloudsdk.generated_clients.apis.run.v2.run_v2_client")
    client.RunV2 = lambda **kwargs: SimpleNamespace()
    messages = ModuleType("googlecloudsdk.generated_clients.apis.run.v2.run_v2_messages")
    for module in (encoding, exceptions, client, messages):
        monkeypatch.setitem(sys.modules, module.__name__, module)
    monkeypatch.setattr(rolling_native.native.SDKGoogleReads, "_http", http)
    def cloud(delegate, *args):
        delegated.append(args)
        return {"actual_policy": "unchanged"}
    monkeypatch.setattr(rolling_native.release.NativeBoundary, "_cloud", cloud)
    boundary = promotion.NativeBoundary()
    yield boundary, calls, delegated
    boundary.close()


def test_real_constructor_supplies_metadata_reader_for_capture(native_boundary):
    boundary, calls, _ = native_boundary
    assert isinstance(boundary, promotion.NativeBoundary)
    assert not hasattr(boundary.observer._delegate, "_get")
    assert boundary.document_release() == "a" * 40
    assert calls[0][0:2] == ("GET", "https://run.googleapis.com/v2/" + promotion.rolling.release.documents.RESOURCE)
    assert calls[0][2]["allow_redirects"] is False


def test_document_proof_receives_same_narrow_observer_and_unchanged_cloud(native_boundary, monkeypatch):
    boundary, calls, delegated = native_boundary
    def proof(observer, source, profile):
        assert observer is boundary.observer and source.release == "b" * 40
        for resource in (promotion.rolling.release.documents.RESOURCE, promotion.rolling.release.native_tex.RESOURCE):
            value = observer._get("run", resource)
            assert value["name"] == resource and value["native_extra"] == ["retain"]
            revision = resource + "/revisions/" + resource.rsplit("/", 1)[-1] + "-owned"
            assert observer._get("run", revision)["name"] == revision
        return observer._cloud("run", "services", "get-iam-policy", "hirewiz-native-tex", "--region", "us-central1")
    monkeypatch.setattr(documents, "verify_document_profile", proof)
    assert boundary.document_authorization(SimpleNamespace(workers_release="b" * 40)) == {"actual_policy": "unchanged"}
    assert len(calls) == 4 and len(delegated) == 1


@pytest.mark.parametrize("api,path,params", [
    ("secret", promotion.rolling.release.documents.RESOURCE, None),
    ("run", promotion.rolling.PARENT + "/services/ai-resume-parser", None),
    ("run", promotion.rolling.release.documents.RESOURCE + "/executions/owned", None),
    ("run", promotion.rolling.release.documents.RESOURCE + "/revisions/other-owned", None),
    ("run", promotion.rolling.release.documents.RESOURCE, {"alt": "json"}),
    ("run", promotion.rolling.release.documents.RESOURCE + "?token=blocked", None),
])
def test_unreviewed_document_routes_refuse_before_transport(native_boundary, api, path, params):
    boundary, calls, _ = native_boundary
    with pytest.raises(Exception, match="document_metadata_route_refused"):
        boundary.observer._get(api, path, params)
    assert calls == []
