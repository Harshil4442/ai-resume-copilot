"""Both worker release gates; native-shaped fixtures are not deployment proof."""
from __future__ import annotations

import copy
import importlib
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "infra/gcp"))
fresh = importlib.import_module("fresh_monetary_release")
release = fresh.release
tex = release.native_tex
SOURCE = "a" * 40
ORIGIN = "https://hirewiz-native-tex-synthetic-uc.a.run.app"
IMAGE = tex.REPOSITORY + "@sha256:" + "f" * 64


@pytest.fixture
def binding(monkeypatch):
    monkeypatch.setattr(tex, "ACCEPTED_ORIGIN", ORIGIN, raising=False)
    monkeypatch.setattr(tex, "ACCEPTED_IMAGE", IMAGE, raising=False)


def snapshot(module, image, origin, *, runtime_phase):
    value = module.render(image, SOURCE)
    service = copy.deepcopy(value["service"])
    name = module.RESOURCE + "/revisions/" + module.SERVICE + "-00001-synthetic"
    service.update(uid="synthetic-service-uid", generation="1", observedGeneration="1",
                   reconciling=False, terminalCondition={"state": "CONDITION_SUCCEEDED"},
                   latestReadyRevision=name, uri=origin)
    revision = copy.deepcopy(service["template"])
    revision.update(name=name, service=module.RESOURCE, uid="synthetic-revision-uid",
                    conditions=[{"type": "Ready", "state": "CONDITION_SUCCEEDED"}])
    return {
        "service": service, "revision": revision,
        "service_iam": {"version": 3, "bindings": ([{"role": "roles/run.invoker",
            "members": ["serviceAccount:" + release.documents.API_INVOKER]}] if runtime_phase else [])},
        "project_iam": {"version": 3, "bindings": []},
        "service_account": {"email": module.SERVICE_ACCOUNT, "disabled": False},
        "service_account_keys": {"keys": [{"keyType": "SYSTEM_MANAGED"}]},
    }


def boundary(monkeypatch, *, runtime_phase=False):
    documents = release.documents
    states = {
        documents.SERVICE: snapshot(documents, documents.ACCEPTED_IMAGE,
                                    documents.ACCEPTED_ORIGIN, runtime_phase=runtime_phase),
        tex.SERVICE: snapshot(tex, IMAGE, ORIGIN, runtime_phase=runtime_phase),
    }
    result = fresh.FreshNativeBoundary(ROOT)
    def read(api, path):
        assert api == "run"
        for row in states.values():
            if path == row["service"]["name"]:
                return row["service"]
            if path == row["revision"]["name"]:
                return row["revision"]
        pytest.fail("Unexpected worker resource")
    def cloud(*args):
        for module in (documents, tex):
            row = states[module.SERVICE]
            if args[:3] == ("run", "services", "get-iam-policy") and args[3] == module.SERVICE:
                return row["service_iam"]
            if args[:3] == ("iam", "service-accounts", "keys") and args[-1] == module.SERVICE_ACCOUNT:
                return row["service_account_keys"]["keys"]
            if args[:3] == ("iam", "service-accounts", "describe") and args[-1] == module.SERVICE_ACCOUNT:
                return row["service_account"]
        if args[:2] == ("projects", "get-iam-policy"):
            return {"version": 3, "bindings": []}
        return {}
    monkeypatch.setattr(result, "_get", read, raising=False)
    monkeypatch.setattr(result, "_cloud", cloud)
    monkeypatch.setattr(release.document_routing, "verify_network", lambda *args: None)
    plan = SimpleNamespace(release=SOURCE, runtime_service_account=documents.API_INVOKER)
    return result, plan, states


def test_both_initial_workers_must_be_ready_with_empty_invoker_policies(binding, monkeypatch):
    adapter, plan, _ = boundary(monkeypatch)
    report = adapter.verify_document_preparation(plan)
    assert report["native_tex"]["configuration_matches"] is True
    assert report["native_tex"]["native_compile_proven"] is False
    with pytest.raises(release.ReleaseDenied):
        adapter.verify_document_configuration(plan)


def test_runtime_worker_phase_requires_only_exact_replacement_invoker(binding, monkeypatch):
    adapter, plan, states = boundary(monkeypatch, runtime_phase=True)
    assert adapter.verify_document_configuration(plan)["native_authenticated_metadata_read"] is True
    with pytest.raises(release.ReleaseDenied):
        adapter.verify_document_preparation(plan)
    states[tex.SERVICE]["service_iam"]["bindings"][0]["members"].append("allAuthenticatedUsers")
    with pytest.raises(release.ReleaseDenied):
        adapter.verify_document_configuration(plan)


@pytest.mark.parametrize("change", ["release", "origin", "user-key", "sandbox"])
def test_tex_mismatch_refuses_before_any_cutover_authority(binding, monkeypatch, change):
    adapter, plan, states = boundary(monkeypatch)
    value = states[tex.SERVICE]
    if change == "release":
        value["service"]["template"]["labels"]["hirewiz-source"] = "b" * 40
    elif change == "origin":
        value["service"]["uri"] = "https://other.run.app"
    elif change == "user-key":
        value["service_account_keys"]["keys"] = [{"keyType": "USER_MANAGED"}]
    else:
        value["revision"]["containers"][0]["sandboxLauncher"] = False
    with pytest.raises(release.ReleaseDenied):
        adapter.verify_document_preparation(plan)


def test_unbound_worker_cannot_configure_api_or_analysis(monkeypatch):
    monkeypatch.delattr(tex, "ACCEPTED_ORIGIN", raising=False)
    monkeypatch.delattr(tex, "ACCEPTED_IMAGE", raising=False)
    plan = SimpleNamespace(release=SOURCE, runtime_secret="synthetic:1", expense_secret="synthetic-expense:1")
    for service in release.SERVICES[:2]:
        with pytest.raises(release.ReleaseDenied, match="accepted_worker_binding_missing"):
            release.runtime_environment(plan, service)
    env, _, removed = release.runtime_environment(plan, release.SERVICES[2])
    assert set(release.NATIVE_ENV) <= set(removed)
    assert set(release.NATIVE_ENV).isdisjoint(env)


@pytest.mark.parametrize("change", [None, "uid", "key", "extra-invoker", "tex-invoker", "tex-extra-invoker"])
def test_runtime_gate_freshly_reads_only_exact_additional_upload_caller(binding, monkeypatch, change):
    adapter, plan, states = boundary(monkeypatch, runtime_phase=True)
    documents = release.documents
    principal = documents.UPLOAD_COORDINATOR
    account = {"name": f"projects/{documents.PROJECT}/serviceAccounts/{principal}",
               "projectId": documents.PROJECT, "email": principal,
               "uniqueId": documents.UPLOAD_COORDINATOR_UID}
    keys = [{"keyType": "SYSTEM_MANAGED"}]
    states[documents.SERVICE]["service_iam"]["bindings"][0]["members"].append("serviceAccount:" + principal)
    states[documents.SERVICE]["service_iam"]["bindings"][0]["members"].sort()
    before = adapter._cloud
    reads = []
    def read(*args):
        if args == ("iam", "service-accounts", "describe", principal):
            reads.append("identity")
            return account
        if args == ("iam", "service-accounts", "keys", "list", "--iam-account", principal):
            reads.append("keys")
            return keys
        return before(*args)
    monkeypatch.setattr(adapter, "_cloud", read)
    if change == "uid":
        account["uniqueId"] = "109155185167233588130"
    if change == "key":
        keys.append({"keyType": "USER_MANAGED"})
    if change == "extra-invoker":
        states[documents.SERVICE]["service_iam"]["bindings"][0]["members"].append("allAuthenticatedUsers")
    if change in {"tex-invoker", "tex-extra-invoker"}:
        states[tex.SERVICE]["service_iam"]["bindings"][0]["members"].append("serviceAccount:" + principal)
        states[tex.SERVICE]["service_iam"]["bindings"][0]["members"].sort()
    if change == "tex-extra-invoker":
        states[tex.SERVICE]["service_iam"]["bindings"][0]["members"].append("allUsers")
    if change in {None, "tex-invoker"}:
        report = adapter.verify_document_configuration(plan)
        assert report["service_iam_phase"] == "api-and-resume-coordinator"
        assert report["native_tex"]["configuration_matches"] is True
        assert report["effective_iam_proven"] is report["runtime_isolation_proven"] is False
    else:
        with pytest.raises(release.ReleaseDenied):
            adapter.verify_document_configuration(plan)
    assert reads == ["identity", "keys"]


def test_compiler_coordinator_cannot_skip_private_inspector_caller_admission(binding, monkeypatch):
    adapter, plan, states = boundary(monkeypatch, runtime_phase=True)
    states[tex.SERVICE]["service_iam"]["bindings"][0]["members"].append(
        "serviceAccount:" + release.documents.UPLOAD_COORDINATOR)
    with pytest.raises(release.ReleaseDenied, match="tex_coordinator_inspector_binding_missing"):
        adapter.verify_document_configuration(plan)
