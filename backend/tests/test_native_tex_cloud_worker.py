"""Actual TeX host boundary/cleanup behavior; no claim of native sandbox proof."""
from __future__ import annotations

import base64
import contextlib
import copy
import hashlib
import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "infra/gcp"))
import native_tex_worker as deployment  # noqa: E402

IMAGE = deployment.REPOSITORY + "@sha256:" + "1" * 64
RELEASE = "2" * 40


@pytest.fixture
def worker(monkeypatch):
    spec = importlib.util.spec_from_file_location("tex_cloud_worker_test", ROOT / "infra/native-tex/cloud_service.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "POLICY_PATH", ROOT / "infra/native-tex/policy.json")
    monkeypatch.setenv("NATIVE_TEX_IMAGE_DIGEST", IMAGE)
    monkeypatch.setenv("NATIVE_TEX_ISOLATION_POLICY_SHA256", deployment.POLICY_SHA256)
    return module


def receipt(payload=b"payload"):
    pdf = b"%PDF-synthetic-boundary-only"
    return {"protocol": "hirewiz-tex-v1", "pdf": base64.b64encode(pdf).decode(),
            "sha256": hashlib.sha256(pdf).hexdigest(), "size": len(pdf),
            "renderer": "pdftex-no-shell-escape-v1"}


def owned(monkeypatch, worker, *, result=None, error=None, cleanup_error=False):
    calls = []
    class LauncherRefused(Exception):
        def __init__(self, authorized):
            self.authorized = authorized
    def run(args, **kwargs):
        calls.append((args, kwargs))
        if args[1] == "delete":
            if cleanup_error:
                raise ValueError("secret candidate bytes must not leak")
            return 0, b""
        if error is not None:
            raise error(LauncherRefused)
        return result or (0, json.dumps(receipt()).encode())
    def remove(directory, identity):
        assert (directory.stat().st_dev, directory.stat().st_ino) == identity
        directory.rmdir()
    module = SimpleNamespace(SANDBOX="/usr/local/gcp/bin/sandbox", _run=run,
                             _remove_request_tmp=remove, _LauncherRefused=LauncherRefused,
                             _absolute_deadline=lambda _seconds: contextlib.nullcontext())
    monkeypatch.setattr(worker, "launcher", lambda: module)
    monkeypatch.setattr(worker.os.path, "isfile", lambda _path: True)
    monkeypatch.setattr(worker.os, "access", lambda *_args: True)
    return calls


def test_success_runs_only_official_sandbox_with_bounded_stdin_then_deletes(worker, monkeypatch):
    calls = owned(monkeypatch, worker)
    payload = b"candidate source private bytes"
    response = worker.compile_payload(payload)
    assert response["request_sha256"] == hashlib.sha256(payload).hexdigest()
    assert response["renderer_image"] == IMAGE
    assert response["isolation_policy_sha256"] == deployment.POLICY_SHA256
    args, options = calls[0]
    assert args[0:2] == ["/usr/local/gcp/bin/sandbox", "do"]
    assert args[-4:] == ["--", "/usr/bin/python3", "-I", "/processor.py"]
    assert len(args) == 9 and args[3] == "--mount" and args[4].endswith(",destination=/tmp")
    assert not any(flag in args for flag in ("--network", "--env", "--root-writable"))
    assert payload not in [value.encode() for value in args]
    assert options == {"timeout": 28, "limit": worker.MAX_OUTPUT, "allow_outcome": True, "input_bytes": payload}
    name = args[2].split("=", 1)[1]
    assert calls[1] == (["/usr/local/gcp/bin/sandbox", "delete", name, "--force"],
                        {"timeout": 5, "limit": 65536, "cleanup": True})
    assert not (Path("/tmp") / name).exists()


@pytest.mark.parametrize("bad", [
    (1, b'{"error":"compile_refused"}'), (0, b'{"pdf":"candidate private source"}'),
    (0, b'{"protocol":1,"protocol":2}'), (2, b'{"error":"compile_refused"}'),
])
def test_compiler_refusal_or_invalid_output_still_deletes_sandbox(worker, monkeypatch, bad):
    calls = owned(monkeypatch, worker, result=bad)
    with pytest.raises((worker.CompileRefused, RuntimeError, ValueError)):
        worker.compile_payload(b"candidate bytes")
    assert [args[1] for args, _ in calls] == ["do", "delete"]
    assert not worker.QUARANTINED.is_set()


@pytest.mark.parametrize("authorized", [True, False])
def test_launcher_uncertainty_deletes_only_authorized_sandbox(worker, monkeypatch, authorized):
    calls = owned(monkeypatch, worker, error=lambda error: error(authorized))
    with pytest.raises(RuntimeError, match="compiler_unavailable"):
        worker.compile_payload(b"candidate bytes")
    assert [args[1] for args, _ in calls] == (["do", "delete"] if authorized else ["do"])


def test_failed_delete_prevents_returning_pdf_and_quarantines_future_admission(worker, monkeypatch):
    calls = owned(monkeypatch, worker, cleanup_error=True)
    with pytest.raises(RuntimeError, match="compiler_cleanup_unverified"):
        worker.compile_payload(b"candidate bytes")
    assert worker.QUARANTINED.is_set()
    with pytest.raises(RuntimeError, match="compiler_unavailable"):
        worker.compile_payload(b"new candidate bytes")
    assert len(calls) == 2
    # Test-only removal of the empty host directory after simulated delete failure.
    name = calls[0][0][2].split("=", 1)[1]
    (Path("/tmp") / name).rmdir()


def test_missing_native_launcher_cannot_fall_back_to_host_compiler(worker, monkeypatch):
    monkeypatch.setattr(worker, "launcher", lambda: SimpleNamespace(SANDBOX="/definitely/absent"))
    with pytest.raises(RuntimeError, match="compiler_unavailable"):
        worker.compile_payload(b"candidate bytes")


def snapshot():
    proposal = deployment.render(IMAGE, RELEASE)
    service = copy.deepcopy(proposal["service"])
    revision_name = deployment.RESOURCE + "/revisions/" + deployment.SERVICE + "-00001-abc"
    service.update(uid="native-service-uid", generation="1", observedGeneration="1", reconciling=False,
                   terminalCondition={"state": "CONDITION_SUCCEEDED"}, latestReadyRevision=revision_name,
                   uri="https://hirewiz-native-tex-native-hash-uc.a.run.app")
    revision = copy.deepcopy(service["template"])
    revision.update(name=revision_name, service=deployment.RESOURCE, uid="native-revision-uid",
                    conditions=[{"type": "Ready", "state": "CONDITION_SUCCEEDED"}])
    return {"service": service, "revision": revision, "service_iam": {"version": 3, "bindings": []},
            "project_iam": {"version": 3, "bindings": []},
            "service_account": {"email": deployment.SERVICE_ACCOUNT, "disabled": False},
            "service_account_keys": {"keys": [{"keyType": "SYSTEM_MANAGED"}]}}


def test_worker_metadata_agreement_does_not_claim_runtime_or_http_proof():
    result = deployment.check_snapshot(snapshot(), IMAGE, RELEASE)
    assert result["configuration_matches"] is True
    assert result["native_compile_proven"] is result["native_http_proven"] is result["effective_iam_proven"] is False
    assert result["api_environment"]["NATIVE_TEX_IMAGE_DIGEST"] == IMAGE


@pytest.mark.parametrize("edit", [
    lambda s: s["service"]["template"]["containers"][0].update(sandboxLauncher=False),
    lambda s: s["service"]["template"]["containers"][0]["env"].append({"name":"SECRET","value":"private"}),
    lambda s: s["service"]["template"].update(vpcAccess={"networkInterfaces": []}),
    lambda s: s["service_iam"].update(bindings=[{"role":"roles/run.invoker","members":["allUsers"]}]),
    lambda s: s["project_iam"].update(bindings=[{"role":"roles/editor","members":["serviceAccount:" + deployment.SERVICE_ACCOUNT]}]),
    lambda s: s["service_account_keys"].update(keys=[{"keyType":"USER_MANAGED"}]),
    lambda s: s["revision"]["containers"][0].update(image=IMAGE[:-1] + "3"),
    lambda s: s["service"].update(observedGeneration="2"),
])
def test_worker_isolation_metadata_drift_refuses(edit):
    value = snapshot()
    edit(value)
    with pytest.raises(deployment.TexConfigurationDenied):
        deployment.check_snapshot(value, IMAGE, RELEASE)


def test_only_exact_future_runtime_invoker_is_permitted_after_isolation():
    value = snapshot()
    value["service_iam"] = deployment.render(IMAGE, RELEASE)["runtime_service_iam"]
    deployment.check_snapshot(value, IMAGE, RELEASE, runtime_phase=True)
    with pytest.raises(deployment.TexConfigurationDenied):
        deployment.check_snapshot(value, IMAGE, RELEASE)


@pytest.mark.parametrize("change", [None, "request", "policy", "image", "renderer", "extra", "redirect"])
def test_private_transport_binds_exact_request_and_runtime_receipt(change, monkeypatch):
    from backend.app.services.native_tex_transport import remote_compile
    from backend.app.services.resume_layout import ResumeLayoutError
    from google.oauth2 import id_token
    payload = b"candidate request bytes"
    data = {**receipt(), "request_sha256": hashlib.sha256(payload).hexdigest(), "renderer_image": IMAGE,
            "isolation_policy_sha256": deployment.POLICY_SHA256}
    if change in {"request", "policy", "image", "renderer"}:
        field = {"request": "request_sha256", "policy": "isolation_policy_sha256", "image": "renderer_image", "renderer": "renderer"}[change]
        data[field] = "changed"
    elif change == "extra":
        data["extra"] = "unbound"
    observed = []
    def identity(request, audience):
        assert type(request).__name__ == "BoundedIdentityRequest"
        assert audience == "https://hirewiz-native-tex-native-hash-uc.a.run.app"
        return "synthetic-token"
    monkeypatch.setattr(id_token, "fetch_id_token", identity)
    def transport(request):
        observed.append(request)
        assert request.content == payload
        assert request.headers["Authorization"] == "Bearer synthetic-token"
        return httpx.Response(302 if change == "redirect" else 200, json=data)
    real_client = httpx.Client
    monkeypatch.setattr(httpx, "Client", lambda **kwargs: real_client(
        **kwargs, transport=httpx.MockTransport(transport)))
    arguments = {"url": "https://hirewiz-native-tex-native-hash-uc.a.run.app/compile",
                 "image": IMAGE, "policy": deployment.POLICY_SHA256}
    if change is None:
        assert remote_compile(payload, **arguments) == data
    else:
        with pytest.raises(ResumeLayoutError, match="original/custom PDF"):
            remote_compile(payload, **arguments)
    assert len(observed) == 1
