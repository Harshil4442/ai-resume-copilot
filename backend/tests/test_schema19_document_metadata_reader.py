"""Offline coverage of the ordinary schema19 document metadata boundary."""

from __future__ import annotations

import importlib
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from backend.tests.test_document_release_integration import (
    native_configuration,
    native_tex,
    snapshots,
)

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "infra/gcp"))
reviewed = importlib.import_module("browser_schema19_release")
DOCUMENTS = reviewed.release.documents
TEX = reviewed.release.native_tex


@pytest.fixture
def reader(monkeypatch):
    calls = []
    constructions = []

    class Reads:
        def __init__(self, sdk, account):
            constructions.append((sdk, account))

        def get(self, api, path, params):
            calls.append((api, path, params))
            return {"name": path, "native_fixture": True}

    monkeypatch.setattr(reviewed.native.native, "SDKGoogleReads", Reads)
    return reviewed.Source19Boundary(ROOT), calls, constructions


@pytest.mark.parametrize("worker", [DOCUMENTS, TEX], ids=["document", "tex"])
@pytest.mark.parametrize("revision", [False, True], ids=["service", "revision"])
@pytest.mark.parametrize("params", [None, {}], ids=["default-query", "empty-query"])
def test_only_exact_document_metadata_gets_use_normal_manager(reader, worker, revision, params):
    boundary, calls, constructions = reader
    path = worker.RESOURCE
    if revision:
        path += "/revisions/" + worker.SERVICE + "-00001-abc"

    assert boundary._get("run", path, params) == {"name": path, "native_fixture": True}
    assert calls == [("run", path, {})]
    assert constructions == [
        (reviewed.native.host_auth.SDK, reviewed.native.host_auth.ACCOUNT)
    ]


@pytest.mark.parametrize(
    ("api", "path", "params"),
    [
        ("secret", DOCUMENTS.RESOURCE, None),
        ("keys", DOCUMENTS.RESOURCE, None),
        ("iam", DOCUMENTS.RESOURCE, None),
        ("compute", DOCUMENTS.RESOURCE, None),
        ("run:POST", DOCUMENTS.RESOURCE, None),
        ("run", DOCUMENTS.RESOURCE, {"pageSize": 1}),
        ("run", DOCUMENTS.RESOURCE, {"alt": "json"}),
        ("run", DOCUMENTS.RESOURCE, {"method": "DELETE"}),
        ("run", DOCUMENTS.RESOURCE, []),
        ("run", DOCUMENTS.RESOURCE, ""),
        ("run", DOCUMENTS.RESOURCE.replace(DOCUMENTS.PROJECT, "foreign-project"), None),
        ("run", DOCUMENTS.RESOURCE.replace(DOCUMENTS.REGION, "europe-west1"), None),
        ("run", DOCUMENTS.RESOURCE.rsplit("/", 1)[0] + "/ai-resume-parser", None),
        ("run", DOCUMENTS.RESOURCE.rsplit("/", 1)[0], None),
        ("run", DOCUMENTS.RESOURCE + "/revisions", None),
        ("run", DOCUMENTS.RESOURCE + "/revisions/" + TEX.SERVICE + "-00001-abc", None),
        ("run", DOCUMENTS.RESOURCE + "/revisions/" + DOCUMENTS.SERVICE + "-", None),
        ("run", DOCUMENTS.RESOURCE + "/revisions/" + DOCUMENTS.SERVICE + "-UPPER", None),
        ("run", DOCUMENTS.RESOURCE + "/revisions/" + DOCUMENTS.SERVICE + "-x/extra", None),
        ("run", DOCUMENTS.RESOURCE + "/revisions/prefix-" + DOCUMENTS.SERVICE + "-x", None),
        ("run", DOCUMENTS.RESOURCE + "/../hirewiz-native-tex", None),
        ("run", DOCUMENTS.RESOURCE + ":delete", None),
        ("run", DOCUMENTS.RESOURCE + ":run", None),
        ("run", DOCUMENTS.RESOURCE + "?alt=json", None),
        ("run", "https://run.googleapis.com/v2/" + DOCUMENTS.RESOURCE, None),
        ("run", "/" + DOCUMENTS.RESOURCE, None),
        ("run", DOCUMENTS.RESOURCE + "\n", None),
        ("run", None, None),
    ],
)
def test_foreign_or_non_get_routes_refuse_before_transport(reader, api, path, params):
    boundary, calls, constructions = reader
    with pytest.raises(reviewed.release.ReleaseDenied, match="metadata_route_refused"):
        boundary._get(api, path, params)
    assert calls == constructions == []


def test_actual_inherited_document_verifier_uses_the_scoped_native_hook(monkeypatch):
    fixture, legacy_reads = native_configuration(snapshots(), snapshots(native_tex))
    calls = []

    class Reads:
        def __init__(self, sdk, account):
            assert (sdk, account) == (
                reviewed.native.host_auth.SDK,
                reviewed.native.host_auth.ACCOUNT,
            )

        def get(self, api, path, params):
            assert params == {}
            calls.append((api, path))
            return fixture._get(api, path)

    monkeypatch.setattr(reviewed.native.native, "SDKGoogleReads", Reads)
    boundary = reviewed.Source19Boundary(ROOT)
    monkeypatch.setattr(boundary, "_cloud", fixture._cloud)
    report = boundary.verify_document_configuration(
        SimpleNamespace(release="a" * 40, runtime_service_account=DOCUMENTS.API_INVOKER)
    )

    assert report["native_authenticated_metadata_read"] is True
    assert report["configuration_matches"] is True
    assert report["runtime_isolation_proven"] is False
    assert report["native_tex"]["native_http_proven"] is False
    for worker in (DOCUMENTS, TEX):
        assert ("run", worker.RESOURCE) in calls
        assert any(path.startswith(worker.RESOURCE + "/revisions/") for _, path in calls)
    assert len(calls) == 4
    assert len(legacy_reads) > len(calls)
