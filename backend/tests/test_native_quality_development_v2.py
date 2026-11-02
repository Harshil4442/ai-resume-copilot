"""Owned runtime/source metadata only; no native data, provider or authority."""
from __future__ import annotations

import base64
import contextlib
import copy
import hashlib
import importlib.util
import json
import marshal
import os
import socket
import struct
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from urllib.parse import parse_qs, urlsplit

import google.auth
import google_crc32c
import pytest
import requests
from google.auth import compute_engine, credentials
from google.cloud import storage
from requests.adapters import HTTPAdapter

from evaluation import development40_qualification_v2 as control
from evaluation import development40_transport_v2 as runtime
from scripts import native_acquisition_channel as old_channel
from scripts import native_acquisition_job as old_job

TOY_NAMESPACE = "gs://owned-toy-private-bucket/quality-development/owned-toy-exp/"
TOY_BODY = b"{}"
TOY_NAME = "owned-probe.json"
METADATA_ROOT = "http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/"


@pytest.fixture
def runtime_offline(monkeypatch, tmp_path):
    """A test that accidentally uses the real network cannot pass by catching it."""
    attempts = []

    def denied(*args, **kwargs):
        attempts.append("external-or-process")
        raise AssertionError("Owned runtime tests forbid external I/O")

    for name in ("create_connection", "getaddrinfo", "gethostbyname"):
        monkeypatch.setattr(socket, name, denied)
    for name in ("connect", "connect_ex", "listen"):
        monkeypatch.setattr(socket.socket, name, denied)
    monkeypatch.setattr(subprocess, "Popen", denied)
    monkeypatch.setattr(google.auth, "default", denied)
    monkeypatch.setattr(os, "environ", {})
    env = {"HOME": str(tmp_path), "XDG_CONFIG_HOME": str(tmp_path),
        "CLOUD_RUN_JOB": "hirewiz-quality-dev-owned-toy", "CLOUD_RUN_EXECUTION": "hirewiz-quality-dev-owned-toy-first",
        "CLOUD_RUN_TASK_INDEX": "0", "CLOUD_RUN_TASK_ATTEMPT": "0", "CLOUD_RUN_TASK_COUNT": "1",
        runtime.NO_BUCKET_METADATA: "true"}
    os.environ.update(env)
    yield SimpleNamespace(env=env, attempts=attempts)
    assert attempts == []


@pytest.fixture
def wire_factory(monkeypatch, runtime_offline):
    """Actual pinned SDK with only its physical HTTP adapter replaced."""
    original_client = storage.Client
    original_close = requests.sessions.Session.close
    closed = []

    def close(session):
        closed.append(id(session))
        return original_close(session)

    monkeypatch.setattr(requests.sessions.Session, "close", close)

    def build(*, statuses=(200,), metadata_statuses=(200, 200), real_credentials=False,
              storage_error=False, metadata_error_at=None, metadata_flavor="Google", ack_change=None):
        state = SimpleNamespace(wire=[], clients=[], credentials=[], closed=closed,
            statuses=list(statuses), metadata_statuses=list(metadata_statuses), metadata_count=0,
            after_metadata=lambda: None)

        class OwnedAdapter(HTTPAdapter):
            def send(self, request, **kwargs):
                target = urlsplit(request.url)
                body = request.body or b""
                assert isinstance(body, bytes)
                state.wire.append({"method": request.method, "host": target.hostname,
                    "url": request.url, "body_sha256": hashlib.sha256(body).hexdigest(),
                    "timeout": kwargs.get("timeout"), "proxies": kwargs.get("proxies", {}),
                    "adapter_retries": self.max_retries.total,
                    "authorization_present": "Authorization" in request.headers})
                response = requests.Response()
                response.request, response.url = request, request.url
                response.headers["Content-Type"] = "application/json"
                response.headers["Metadata-Flavor"] = metadata_flavor
                value: dict[str, Any]
                if target.hostname == "metadata.google.internal":
                    state.metadata_count += 1
                    if metadata_error_at == state.metadata_count:
                        raise requests.exceptions.ConnectionError("owned metadata transport failure")
                    assert request.method == "GET" and request.url.startswith(METADATA_ROOT)
                    response.status_code = state.metadata_statuses.pop(0)
                    value = ({"access_token": "owned-toy-not-an-account-token", "expires_in": 3600}
                        if target.path.endswith("/token") else {"email": "owned-evaluator@example.invalid",
                            "scopes": ["https://www.googleapis.com/auth/devstorage.read_write"], "aliases": ["default"]})
                    state.after_metadata()
                elif request.method == "POST" and "/upload/" in target.path:
                    if storage_error:
                        raise requests.exceptions.ConnectionError("owned storage unknown outcome")
                    response.status_code = state.statuses.pop(0)
                    value = {"bucket": "owned-toy-private-bucket", "name": "quality-development/owned-toy-exp/" + TOY_NAME,
                        "generation": "1", "size": str(len(TOY_BODY)),
                        "crc32c": base64.b64encode(google_crc32c.Checksum(TOY_BODY).digest()).decode()}
                    value.update(ack_change or {})
                    if response.status_code in {307, 308}:
                        response.headers["Location"] = "https://owned-redirect.invalid/upload/redirected"
                else:
                    pytest.fail("Unexpected owned fake route: " + request.method + " " + target.path)
                response._content = json.dumps(value).encode()
                return response

        class ToyCredentials(credentials.Credentials):
            def __init__(self, **kwargs):
                super().__init__()
                assert kwargs == {"universe_domain": "googleapis.com"}
                self.token = "owned-toy-not-an-account-token"
                self.refreshes = 0
                state.credentials.append(self)

            def refresh(self, request):
                self.refreshes += 1
                pytest.fail("Object HTTP must not refresh and replay after401")

        def client(*args, **kwargs):
            value = original_client(*args, **kwargs)
            state.clients.append(value)
            return value

        monkeypatch.setattr(requests.adapters, "HTTPAdapter", OwnedAdapter)
        monkeypatch.setattr(requests.sessions, "HTTPAdapter", OwnedAdapter)
        monkeypatch.setattr(storage, "Client", client)
        if not real_credentials:
            monkeypatch.setattr(compute_engine, "Credentials", ToyCredentials)
        return state

    return build


def wire_posts(state):
    return [row for row in state.wire if row["method"] == "POST"]


def request_handles(state):
    http = state.clients[0]._http
    return http, http._auth_request, http._auth_request.session


def assert_closed(state, handles):
    http, _, auth_http = handles
    assert id(http) in state.closed and id(auth_http) in state.closed


@pytest.mark.parametrize("status", [200, 401, 307, 308, 412, 429, 503])
def test_runtime_sdk_create_has_single_generation_zero_attempt(wire_factory, status):
    state = wire_factory(statuses=[status])
    with runtime.keyless_store(TOY_NAMESPACE) as store:
        assert type(store) is old_channel.GcsObjects
        handles = request_handles(state)
        if status == 200:
            result = store.create(TOY_NAME, TOY_BODY)
            assert result.generation == "1" and result.sha256 == hashlib.sha256(TOY_BODY).hexdigest()
        else:
            with pytest.raises(ValueError, match="create_unknown"):
                store.create(TOY_NAME, TOY_BODY)
    posts = wire_posts(state)
    assert len(posts) == 1 and len(state.wire) == 1
    assert parse_qs(urlsplit(posts[0]["url"]).query)["ifGenerationMatch"] == ["0"]
    assert posts[0]["host"] == "storage.googleapis.com" and not posts[0]["proxies"]
    assert posts[0]["adapter_retries"] == 0 and posts[0]["timeout"] == 15
    assert all(value.refreshes == 0 for value in state.credentials)
    assert_closed(state, handles)


@pytest.mark.parametrize("override", [None, "API_ENDPOINT_OVERRIDE", "HTTPS_PROXY", "REQUESTS_CA_BUNDLE"])
def test_runtime_explicit_factory_has_no_background_universe_or_environment_route(wire_factory, monkeypatch, override):
    state = wire_factory()
    if override:
        monkeypatch.setenv(override, "https://owned-override.invalid")
    with runtime.keyless_store(TOY_NAMESPACE) as store:
        store.create(TOY_NAME, TOY_BODY)
        http, request, auth_http = handles = request_handles(state)
        assert http.trust_env is False and http.max_redirects == 0 and http._max_refresh_attempts == 0
        assert auth_http.trust_env is False and auth_http.max_redirects == 0
        assert http.get_adapter("https://").max_retries.total == auth_http.get_adapter("https://").max_retries.total == 0
        assert request.calls == 0 and state.clients[0].api_endpoint == "https://storage.googleapis.com"
    assert len(state.wire) == 1 and wire_posts(state)[0]["host"] == "storage.googleapis.com"
    assert not wire_posts(state)[0]["proxies"]
    assert_closed(state, handles)


@pytest.mark.parametrize("failure", ["lost_ack", "generation", "size"])
def test_runtime_unknown_or_invalid_ack_never_replays(wire_factory, failure):
    state = wire_factory(storage_error=failure == "lost_ack",
        ack_change={"generation": "not-a-generation"} if failure == "generation" else {"size": "999"} if failure == "size" else {})
    with runtime.keyless_store(TOY_NAMESPACE) as store, pytest.raises(ValueError, match="create_unknown"):
        store.create(TOY_NAME, TOY_BODY)
    assert len(wire_posts(state)) == 1


def test_runtime_real_metadata_credentials_use_only_info_and_token_then_no_third_request(wire_factory):
    state = wire_factory(real_credentials=True)
    with runtime.keyless_store(TOY_NAMESPACE) as store:
        store.create(TOY_NAME, TOY_BODY)
        http, request, _ = handles = request_handles(state)
        assert isinstance(http.credentials, compute_engine.Credentials)
        assert state.metadata_count == request.calls == 2
        # A second credential refresh is refused before its first physical GET.
        with pytest.raises(ValueError, match="metadata_route|metadata_budget_exhausted"):
            http.credentials.refresh(request)
        with pytest.raises(ValueError, match="metadata_budget_exhausted"):
            request(METADATA_ROOT + "token")
        assert state.metadata_count == 2 and len(wire_posts(state)) == 1
    assert len(state.wire) == 3
    assert all(row["timeout"] <= runtime.METADATA_CALL_TIMEOUT for row in state.wire if row["method"] == "GET")
    assert_closed(state, handles)


@pytest.mark.parametrize("stage,kind", [(1, "status"), (2, "status"), (1, "redirect"), (2, "redirect"),
    (1, "transport"), (2, "transport"), (1, "flavor")])
def test_runtime_metadata_failure_does_not_enter_sdk_retry_or_object_send(wire_factory, stage, kind):
    statuses = [200, 200]
    if kind == "status":
        statuses[stage - 1] = 503
    elif kind == "redirect":
        statuses[stage - 1] = 307
    state = wire_factory(real_credentials=True, metadata_statuses=statuses,
        metadata_error_at=stage if kind == "transport" else None,
        metadata_flavor="owned-wrong-flavor" if kind == "flavor" else "Google")
    with runtime.keyless_store(TOY_NAMESPACE) as store:
        handles = request_handles(state)
        with pytest.raises(ValueError, match="create_unknown"):
            store.create(TOY_NAME, TOY_BODY)
    assert state.metadata_count == stage and not wire_posts(state)
    assert_closed(state, handles)


@pytest.mark.parametrize("url,method,body", [
    (METADATA_ROOT, "POST", None), (METADATA_ROOT, "GET", b"owned-body"),
    (METADATA_ROOT.replace("metadata.google.internal", "owned.invalid"), "GET", None),
    (METADATA_ROOT.replace("metadata.google.internal", "user@metadata.google.internal"), "GET", None),
    (METADATA_ROOT.replace("metadata.google.internal", "metadata.google.internal:80"), "GET", None),
    (METADATA_ROOT + "?recursive=true#fragment", "GET", None),
    (METADATA_ROOT + "../token", "GET", None),
    (METADATA_ROOT + "?recursive=false", "GET", None),
    (METADATA_ROOT + "token?scopes=one&scopes=two", "GET", None),
    (METADATA_ROOT + "token?audience=owned", "GET", None),
])
def test_runtime_metadata_route_denied_before_adapter(wire_factory, url, method, body):
    state = wire_factory()
    with runtime.keyless_store(TOY_NAMESPACE):
        _, request, _ = request_handles(state)
        with pytest.raises(ValueError):
            request(url, method=method, body=body)
        assert request.calls == 0
    assert state.wire == []


@pytest.mark.parametrize("change", ["expired_before_send", "expired_after_response", "remaining_timeout"])
def test_runtime_metadata_elapsed_checks_and_remaining_timeout_are_not_wallclock_proof(wire_factory, monkeypatch, change):
    state = wire_factory()
    clock = [100.0]
    monkeypatch.setattr(runtime.time, "monotonic", lambda: clock[0])
    with runtime.keyless_store(TOY_NAMESPACE):
        _, request, _ = request_handles(state)
        if change == "expired_before_send":
            request.started = 90.0
            with pytest.raises(ValueError, match="metadata_budget_exhausted"):
                request(METADATA_ROOT + "?recursive=true")
            assert state.wire == [] and request.calls == 0
        elif change == "expired_after_response":
            state.after_metadata = lambda: clock.__setitem__(0, 110.0)
            with pytest.raises(ValueError, match="metadata_budget_exhausted"):
                request(METADATA_ROOT + "?recursive=true")
            assert state.metadata_count == 1 and request.calls == 1
        else:
            request.started = 92.0
            request(METADATA_ROOT + "?recursive=true")
            assert state.wire[0]["timeout"] == 2.0


@pytest.mark.parametrize("stage", ["credentials", "authorized_session", "client", "adapter"])
def test_runtime_constructor_failure_closes_owned_sessions(runtime_offline, monkeypatch, stage):
    from google.auth.transport import requests as auth_requests

    original_session = requests.Session
    sessions, closed = [], []

    def session():
        value = original_session()
        sessions.append(value)
        close = value.close

        def tracked_close():
            closed.append(id(value))
            close()

        value.close = tracked_close
        return value

    def unavailable(*args, **kwargs):
        raise ValueError("owned constructor failure")

    monkeypatch.setattr(requests, "Session", session)
    if stage == "credentials":
        monkeypatch.setattr(compute_engine, "Credentials", unavailable)
    elif stage == "authorized_session":
        monkeypatch.setattr(auth_requests, "AuthorizedSession", unavailable)
    elif stage == "adapter":
        monkeypatch.setattr(requests.adapters, "HTTPAdapter", unavailable)
    else:
        monkeypatch.setattr(storage, "Client", unavailable)
    with pytest.raises(ValueError, match="owned constructor failure"), runtime.keyless_store(TOY_NAMESPACE):
        pytest.fail("A failing constructor must not yield a store")
    assert len(sessions) == 1 and id(sessions[0]) in closed


@pytest.fixture
def runtime_bundle(tmp_path, runtime_offline):
    """Minimal valid closed Bundle; no selection, materialization or plan call."""
    now = datetime.now(UTC)
    artifact = {"path": "owned.json", "sha256": "a" * 64}
    reference = {"name": "input.owned.json", "generation": "1", "size": 2, "sha256": "b" * 64}
    auth = {"schema_version": "development-native-service-acquisition-v1",
        "purpose": "unreviewed_native_input_capture_no_model_no_retail", "state": "authorized",
        "authorized_by": "owned-toy-not-authority", "experiment_id": "owned-toy-exp",
        "journal_path": str(tmp_path / "workspace/journal"), "split": "development",
        "valid_from": (now - timedelta(minutes=1)).isoformat(), "expires_at": (now + timedelta(hours=1)).isoformat(),
        "packet_manifest_sha256": old_job.acquisition.inputs.MANIFEST_SHA, "native_manifest_sha256": "c" * 64,
        "funding_evidence": artifact, "request_limit": 80, "reserved_minor_per_request": 1,
        "maximum_native_reserved_minor": 80, "currency": "INR",
        "inspector": {"url": "https://owned-inspector.run.app/inspect", "image": "owned@sha256:" + "d" * 64, "policy": "e" * 64},
        "compiler": {"url": "https://owned-compiler.run.app/compile", "image": "owned@sha256:" + "f" * 64, "policy": "1" * 64}}
    configuration = {"schema_version": old_channel.VERSION, "namespace": TOY_NAMESPACE,
        "acquisition_plan_sha256": "2" * 64, "channel_source_sha256": runtime.CHANNEL_SHA256,
        "maximum_output_bytes": 1024, "maximum_objects": 10}
    dispatch = {"schema_version": "reviewed-native-acquisition-dispatch-v1",
        "purpose": "unreviewed_service_capture_external_authority_required", "reviewed_by": "owned-toy",
        "external_authority_review": artifact, "channel_binding": reference | {"name": "channel-binding.json"},
        "acknowledgment": reference | {"name": "dispatch.owned.json"}, "before_checkpoint_sha256": "3" * 64,
        "parent_terminal": None, "maximum_requests": 1}
    bundle = old_job.Bundle.model_validate({"schema_version": "development-native-job-input-v1",
        "purpose": "unreviewed_development_capture_no_model_no_retail", "entry_sha256": runtime.SEMANTIC_SHA256,
        "job_name": runtime_offline.env["CLOUD_RUN_JOB"], "configuration": configuration, "authorization": auth,
        "dispatch": dispatch, "previous_dispatch": None, "runner_funding_evidence": artifact,
        "native_files": [{"path": "owned.txt", "object": reference}],
        "authorization_files": [{"path": "owned.json", "object": reference | {"name": "input.authority.json"}}]})
    raw = old_job.packet.canonical(bundle.model_dump(mode="json"))
    obj = old_channel.Object(old_channel.ObjectRef(name="input.bundle.json", generation="1", size=len(raw), sha256=hashlib.sha256(raw).hexdigest()), raw)
    holder = {"value": obj}
    trace: list[tuple[Any, ...]] = []

    class Store:
        namespace = TOY_NAMESPACE

        def fetch(self, name, *, generation, maximum):
            trace.append(("fetch", name, generation, maximum))
            return holder["value"]

    @contextlib.contextmanager
    def factory(namespace):
        trace.append(("enter", namespace))
        try:
            yield Store()
        finally:
            trace.append(("closed",))

    argv = ["--namespace", TOY_NAMESPACE, "--bundle-name", obj.reference.name, "--bundle-generation", "1",
        "--bundle-sha256", obj.reference.sha256, "--workspace", str(tmp_path / "workspace")]
    return SimpleNamespace(bundle=bundle, obj=obj, holder=holder, argv=argv, factory=factory, trace=trace, workspace=tmp_path / "workspace")


def test_runtime_main_delegates_original_bundle_store_environment_without_relabeling(runtime_bundle, runtime_offline, monkeypatch, capsys):
    t = runtime_bundle
    monkeypatch.setattr(runtime, "keyless_store", t.factory)
    calls = []

    def run(bundle, store, packet_root, workspace, *, environment):
        calls.append((bundle, store, packet_root, workspace, environment))
        return {"state": "completed_unreviewed", "native_approval": "pending", "authorizes_retail": False}

    monkeypatch.setattr(old_job, "run", run)
    actual_environment = dict(os.environ)
    assert runtime.main(t.argv) == 0
    bundle, store, packet_root, workspace, environment = calls[0]
    assert type(bundle) is old_job.Bundle and bundle == t.bundle
    assert bundle.entry_sha256 == runtime.SEMANTIC_SHA256 != old_job.packet.file_digest(Path(runtime.__file__))
    assert store.namespace == TOY_NAMESPACE and workspace == t.workspace
    assert packet_root == Path(runtime.__file__).resolve().parents[1] / "evals/unified_credit_v2"
    assert environment == actual_environment
    assert t.trace == [("enter", TOY_NAMESPACE), ("fetch", "input.bundle.json", "1", old_channel.MAX_METADATA), ("closed",)]
    assert json.loads(capsys.readouterr().out)["native_approval"] == "pending"
    assert not t.workspace.exists()


@pytest.mark.parametrize("key,value", [("API_ENDPOINT_OVERRIDE", "https://owned.invalid"), ("HTTPS_PROXY", "http://owned.invalid"),
    ("OTEL_EXPORTER_OTLP_ENDPOINT", "https://owned.invalid"), ("ENABLE_GCS_PYTHON_CLIENT_OTEL_TRACES", "true"),
    ("PYTHONPATH", "/owned"), ("LD_PRELOAD", "/owned.so"), ("DYLD_INSERT_LIBRARIES", "/owned"),
    ("GOOGLE_APPLICATION_CREDENTIALS", "/owned.json"), ("GCE_METADATA_HOST", "owned.invalid"),
    ("CLOUD_RUN_TASK_ATTEMPT", "1"), (runtime.NO_BUCKET_METADATA, "false")])
def test_runtime_bad_environment_stops_before_factory_or_fetch(runtime_bundle, monkeypatch, capsys, key, value):
    t = runtime_bundle
    monkeypatch.setattr(runtime, "keyless_store", t.factory)
    monkeypatch.setenv(key, value)
    assert runtime.main(t.argv) == 2 and not t.trace
    assert not t.workspace.exists() and json.loads(capsys.readouterr().err)["native_approval"] == "pending"


@pytest.mark.parametrize("namespace", [TOY_NAMESPACE.replace("quality-development", "quality-held-out"),
    "gs://owned-toy-private-bucket/", TOY_NAMESPACE + "nested/", TOY_NAMESPACE.replace("gs://", "https://")])
def test_runtime_namespace_denied_before_credential_or_client_construction(runtime_offline, monkeypatch, namespace):
    def denied(*args, **kwargs):
        pytest.fail("Invalid namespace must not construct a credential/client")
    monkeypatch.setattr(compute_engine, "Credentials", denied)
    monkeypatch.setattr(storage, "Client", denied)
    with pytest.raises(ValueError, match="development_namespace_required"), runtime.keyless_store(namespace):
        pytest.fail("Invalid namespace must not yield")


@pytest.mark.parametrize("change", ["generation", "hash", "empty", "semantic_source", "run_unknown"])
def test_runtime_main_changed_input_or_semantics_stays_refused_and_closed(runtime_bundle, monkeypatch, capsys, change):
    t = runtime_bundle
    monkeypatch.setattr(runtime, "keyless_store", t.factory)
    if change == "generation":
        t.argv[t.argv.index("--bundle-generation") + 1] = "2"
    elif change == "hash":
        t.argv[t.argv.index("--bundle-sha256") + 1] = "0" * 64
    elif change == "empty":
        t.argv[t.argv.index("--bundle-generation") + 1] = "0"
    elif change == "semantic_source":
        monkeypatch.setattr(runtime, "SEMANTIC_SHA256", "0" * 64)
    else:
        def unknown(*args, **kwargs):
            raise TimeoutError("owned-sensitive-provider-detail")
        monkeypatch.setattr(old_job, "run", unknown)
    assert runtime.main(t.argv) == 2
    captured = capsys.readouterr()
    assert not captured.out and "sensitive-provider" not in captured.err
    assert json.loads(captured.err)["state"] == "refused_or_unknown_no_replay"
    assert not t.workspace.exists()
    if change in {"empty", "semantic_source"}:
        assert not t.trace
    else:
        assert t.trace[-1] == ("closed",)


def test_runtime_delegated_semantic_entry_rejects_transport_hash_in_original_bundle(runtime_bundle, monkeypatch, capsys):
    t = runtime_bundle
    changed = t.bundle.model_copy(update={"entry_sha256": old_job.packet.file_digest(Path(runtime.__file__))})
    raw = old_job.packet.canonical(changed.model_dump(mode="json"))
    ref = t.obj.reference.model_copy(update={"size": len(raw), "sha256": hashlib.sha256(raw).hexdigest()})
    t.holder["value"] = old_channel.Object(ref, raw)
    t.argv[t.argv.index("--bundle-sha256") + 1] = ref.sha256
    monkeypatch.setattr(runtime, "keyless_store", t.factory)
    # Use the actual delegated run: its source check precedes all corpus/input I/O.
    assert runtime.main(t.argv) == 2
    assert t.trace[-1] == ("closed",) and not t.workspace.exists()
    assert json.loads(capsys.readouterr().err)["state"] == "refused_or_unknown_no_replay"


def test_runtime_duplicate_bundle_key_refuses_before_delegation(runtime_bundle, monkeypatch, capsys):
    t = runtime_bundle
    raw = t.obj.raw.rstrip(b"\n")[:-1] + b',"entry_sha256":"' + runtime.SEMANTIC_SHA256.encode() + b'"}'
    ref = t.obj.reference.model_copy(update={"size": len(raw), "sha256": hashlib.sha256(raw).hexdigest()})
    t.holder["value"] = old_channel.Object(ref, raw)
    t.argv[t.argv.index("--bundle-sha256") + 1] = ref.sha256
    monkeypatch.setattr(runtime, "keyless_store", t.factory)
    monkeypatch.setattr(old_job, "run", lambda *args, **kwargs: pytest.fail("Ambiguous bundle must not delegate"))
    assert runtime.main(t.argv) == 2 and t.trace[-1] == ("closed",) and not t.workspace.exists()
    assert json.loads(capsys.readouterr().err)["state"] == "refused_or_unknown_no_replay"


def _receipt(**changes):
    return {"schema_version": control.VERSION, "repository": "https://github.com/Harshil4442/ai-resume-copilot.git",
        "release": "c" * 40, "git_tree": "d" * 40, "ci_run_id": 1,
        "build_id": "11111111-1111-1111-1111-111111111111",
        "image": control.original.RUNNER_REPOSITORY + "@sha256:" + "e" * 64,
        "qualification_sha256": "a" * 64, "app_compatibility_sha256": "b" * 64,
        "app_sha256": control.q.APP_SHA256, "app_files": 289, "entry_path": control.ENTRY,
        "entry_sha256": "f" * 64, "semantic_entry_path": control.SEMANTIC_ENTRY,
        "semantic_entry_sha256": control.SEMANTIC_SHA256, "channel_sha256": control.CHANNEL_SHA256,
        "runtime_dependencies_sha256": "1" * 64, "component_sha256": "2" * 64,
        "observed_at": "2026-11-03T00:00:00Z", "ci_metadata_sha256": "3" * 64,
        "build_metadata_sha256": "4" * 64} | changes


def _resource_plan(raw, **changes):
    now = datetime.now(UTC)
    d = control.original.documents
    return control.original.Plan(**({"purpose": "unreviewed_development_capture_no_model_no_retail",
        "evaluator": control.original.EVALUATOR, "evaluator_uid": "109100000000000000001",
        "bucket": control.original.BUCKET, "bucket_metageneration": "1", "experiment_id": "owned-toy-exp",
        "valid_from": now - timedelta(seconds=20), "expires_at": now + timedelta(minutes=30),
        "controller_sha256": "a" * 64, "job_name": "hirewiz-quality-dev-owned-toy",
        "qualified_runner_receipt_sha256": hashlib.sha256(raw).hexdigest(),
        "inspector_service_uid": "owned-inspector", "inspector_revision_uid": "owned-revision",
        "compiler_service_uid": "owned-compiler", "compiler_revision_uid": "owned-revision",
        "inspector_image": f"{d.REGION}-docker.pkg.dev/{d.PROJECT}/cloud-run-source-deploy/{d.SERVICE}@sha256:" + "d" * 64,
        "compiler_image": control.original.tex.REPOSITORY + "@sha256:" + "f" * 64,
        "inspector_release": "c" * 40, "compiler_release": "c" * 40, "funding_evidence_sha256": "a" * 64,
        "acquisition_authorization_sha256": "1" * 64, "native_dispatch_review_sha256": "a" * 64,
        "bundle_name": "input.bundle.json", "bundle_generation": "1", "bundle_sha256": "3" * 64} | changes))


def _raw_receipt(**changes):
    return json.dumps(_receipt(**changes), sort_keys=True, separators=(",", ":")).encode()


def test_new_receipt_binds_two_different_entries_and_old_verifier_still_refuses(runtime_offline):
    raw = _raw_receipt()
    plan = _resource_plan(raw)
    result = control.bound_runner(plan, raw)
    assert result.entry_sha256 != result.semantic_entry_sha256 == runtime.SEMANTIC_SHA256
    assert result.entry_path == control.ENTRY and result.semantic_entry_path == control.SEMANTIC_ENTRY
    with pytest.raises(ValueError):
        control.original.bound_runner(plan, raw)
    with pytest.raises(ValueError):
        control.original.expected_job(plan, raw, batch=0)


@pytest.mark.parametrize("changes", [{"schema_version": "native-quality-qualified-runner-v1"},
    {"entry_path": control.SEMANTIC_ENTRY}, {"semantic_entry_path": control.ENTRY},
    {"semantic_entry_sha256": "f" * 64}, {"channel_sha256": "0" * 64},
    {"release": control.q.HISTORICAL_RELEASE}, {"extra_authority": True}, {"observed_at": "2026-11-03T00:00:00"}])
def test_changed_receipt_cannot_adopt_old_entry_or_authority(runtime_offline, changes):
    raw = _raw_receipt(**changes)
    with pytest.raises(ValueError):
        control.bound_runner(_resource_plan(raw), raw)


def test_duplicate_receipt_and_changed_raw_bytes_are_denied(runtime_offline):
    raw = _raw_receipt()
    with pytest.raises(ValueError, match="receipt_bytes"):
        control.bound_runner(_resource_plan(raw), raw + b" ")
    raw = raw[:-1] + b',"entry_sha256":"' + b"f" * 64 + b'"}'
    with pytest.raises(ValueError, match="duplicate_json_key"):
        control.bound_runner(_resource_plan(raw), raw)


def _job(plan, raw):
    return {"name": f"projects/{control.original.PROJECT}/locations/{control.original.REGION}/jobs/{plan.job_name}",
        "uid": "owned-job-uid", "generation": "1", "observedGeneration": "1", "reconciling": False,
        "terminalCondition": {"state": "CONDITION_SUCCEEDED"}, "template": control.expected_job(plan, raw, batch=0)}


def test_exact_new_job_preserves_old_resources_and_has_no_dispatch_authority(runtime_offline):
    raw = _raw_receipt()
    plan = _resource_plan(raw)
    job = _job(plan, raw)
    before = copy.deepcopy(job)
    control.check_job(plan, raw, job, batch=0)
    assert job == before
    task = job["template"]["template"]
    assert task["containers"][0]["args"][:3] == ["-I", "-B", "/app/" + control.ENTRY]
    assert task["containers"][0]["env"] == [{"name": runtime.NO_BUCKET_METADATA, "value": "true"}]
    assert task["maxRetries"] == 0 and job["template"]["taskCount"] == job["template"]["parallelism"] == 1
    assert control.expected_job(plan, raw, batch=8)["template"]["timeout"] == "1200s"


@pytest.mark.parametrize("change", [
    lambda j: j["template"].update(taskCount=2),
    lambda j: j["template"]["template"].update(maxRetries=1),
    lambda j: j["template"]["template"].update(serviceAccount="other@example.invalid"),
    lambda j: j["template"]["template"]["containers"][0].pop("env"),
    lambda j: j["template"]["template"]["containers"][0]["env"].append({"name": "API_ENDPOINT_OVERRIDE", "value": "https://owned.invalid"}),
    lambda j: j["template"]["template"]["containers"][0]["args"].__setitem__(2, "/app/" + control.SEMANTIC_ENTRY),
    lambda j: j["template"]["template"]["containers"][0]["args"].__setitem__(0, "-m"),
    lambda j: j.update(runExecutionToken="not-a-token"),
    lambda j: j.update(observedGeneration="2"),
    lambda j: j["template"]["template"]["vpcAccess"].update(egress="ALL_TRAFFIC"),
])
def test_job_entry_environment_retry_and_identity_drift_refuses(runtime_offline, change):
    raw = _raw_receipt()
    plan = _resource_plan(raw)
    job = _job(plan, raw)
    change(job)
    with pytest.raises(ValueError):
        control.check_job(plan, raw, job, batch=0)


def _bundle_for_plan(runtime_bundle, raw_runner):
    preliminary = _resource_plan(raw_runner)
    body = runtime_bundle.bundle.model_dump(mode="json")
    body["configuration"]["namespace"] = preliminary.namespace
    auth = body["authorization"]
    auth["journal_path"] = control.original.WORKSPACE + "/journal"
    auth["inspector"] = {"url": control.original.documents.ACCEPTED_ORIGIN + "/inspect",
        "image": preliminary.inspector_image, "policy": "22a012f2fb7b6d3b696d8772563e9363566b9f03fb83385cfeffbc84a26459eb"}
    auth["compiler"] = {"url": control.original.tex.ACCEPTED_ORIGIN + "/compile",
        "image": preliminary.compiler_image, "policy": control.original.tex.POLICY_SHA256}
    body["native_files"] = [{"path": "materialization-manifest.json" if n == 0 else f"owned-{n}.pdf",
        "object": {"name": f"input.owned-{n}.json", "generation": "1", "size": 2, "sha256": "b" * 64}}
        for n in range(41)]
    return body


def test_original_bundle_semantic_hash_is_not_replaced_by_wrapper_hash(runtime_bundle):
    runner = _raw_receipt()
    body = _bundle_for_plan(runtime_bundle, runner)
    raw = old_job.packet.canonical(body)
    plan = _resource_plan(runner, bundle_sha256=hashlib.sha256(raw).hexdigest(),
        acquisition_authorization_sha256=control.digest(old_job.Bundle.model_validate(body).authorization.model_dump(mode="json")))
    control.check_future_bundle(plan, runner, raw, batch=0)
    body["entry_sha256"] = _receipt()["entry_sha256"]
    changed = old_job.packet.canonical(body)
    changed_plan = plan.model_copy(update={"bundle_sha256": hashlib.sha256(changed).hexdigest()})
    with pytest.raises(ValueError, match="bundle_authorization"):
        control.check_future_bundle(changed_plan, runner, changed, batch=0)


def test_controller_duplicate_bundle_key_cannot_differ_from_runtime_parse(runtime_bundle):
    runner = _raw_receipt()
    body = _bundle_for_plan(runtime_bundle, runner)
    raw = old_job.packet.canonical(body).rstrip(b"\n")[:-1] + b',"entry_sha256":"' + runtime.SEMANTIC_SHA256.encode() + b'"}'
    plan = _resource_plan(runner, bundle_sha256=hashlib.sha256(raw).hexdigest())
    with pytest.raises(ValueError, match="duplicate_json_key"):
        control.check_future_bundle(plan, runner, raw, batch=0)


@pytest.mark.parametrize("value", [0, -1, True, None])
def test_native_verifier_requires_real_controller_CI_before_any_source_or_provider(runtime_offline, monkeypatch, tmp_path, value):
    raw = _raw_receipt()
    monkeypatch.setattr(control, "source_identity", lambda root: pytest.fail("No source/native work should start"))
    with pytest.raises(ValueError, match="active_controller_CI_required"):
        control.verify_qualified_runner(_resource_plan(raw), raw, runner_root=tmp_path, controller_ci_run_id=value)


def test_borrowed_C6_or_changed_source_receipt_denied_before_observer(runtime_offline, monkeypatch, tmp_path):
    raw = _raw_receipt()
    monkeypatch.setenv("CLOUDSDK_CORE_DISABLE_FILE_LOGGING", "true")
    monkeypatch.setenv("CLOUDSDK_CORE_LOG_HTTP", "false")
    monkeypatch.setattr(control, "source_identity", lambda root: {"qualification_sha256": "a" * 64})
    monkeypatch.setattr(control, "runner_source", lambda root: {"entry_sha256": runtime.SEMANTIC_SHA256})
    monkeypatch.setattr(control.q, "_source_modules", lambda: pytest.fail("Changed source must deny before native observer"))
    with pytest.raises(ValueError, match="actual_runner_source"):
        control.verify_qualified_runner(_resource_plan(raw), raw, runner_root=tmp_path, controller_ci_run_id=1)


def _git(directory, *argv):
    env = {"PATH": "/usr/bin:/bin", "HOME": str(directory), "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_CONFIG_NOSYSTEM": "1", "GIT_TERMINAL_PROMPT": "0", "GIT_NO_LAZY_FETCH": "1",
        "GIT_NO_REPLACE_OBJECTS": "1"}
    return subprocess.run(["/usr/bin/git", "-c", "core.fsmonitor=false", "-C", str(directory), *argv],
        env=env, stdin=subprocess.DEVNULL, capture_output=True, check=True, timeout=15).stdout


def _owned_code_checkout(directory):
    """Copy only code/proof/lock bytes, never evaluation data or case selection."""
    root = Path(control.__file__).resolve().parents[2]
    tracked = set(_git(root, "ls-tree", "-r", "--name-only", "HEAD").decode().splitlines())
    names = {n for n in tracked if n.startswith(control.CODE_ROOTS) and Path(n).suffix in {".py", ".json"}}
    names |= set(control.FIXED) | set(control.COMPONENTS)
    directory.mkdir(mode=0o700)
    for name in sorted(names):
        path = directory / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes((root / name).read_bytes())
    _git(directory, "init", "-q")
    _git(directory, "add", ".")
    _git(directory, "-c", "user.name=Owned source test", "-c", "user.email=owned@example.invalid", "commit", "-qm", "owned code only")
    return directory


def _source_cli(controller_root, runner_root):
    env = {"PATH": "/usr/bin:/bin", "HOME": str(controller_root), "XDG_CONFIG_HOME": str(controller_root),
        "PYTHONDONTWRITEBYTECODE": "1"}
    return subprocess.run([sys.executable, "-I", "-B", "-X",
        "pycache_prefix=" + str(controller_root.parent / "private-empty-child-cache"),
        str(controller_root / control.COMPONENTS[1]),
        "--root", str(runner_root), "--source-child"], env=env, cwd=controller_root,
        stdin=subprocess.DEVNULL, capture_output=True, timeout=45)


def test_real_source_child_owns_only_code_and_binds_both_entries(tmp_path):
    root = _owned_code_checkout(tmp_path / "owned")
    result = _source_cli(root, root)
    assert result.returncode == 0, result.stderr.decode()
    value = json.loads(result.stdout)
    assert value["release"] == _git(root, "rev-parse", "HEAD").decode().strip()
    assert value["entry_sha256"] == hashlib.sha256((root / control.ENTRY).read_bytes()).hexdigest()
    assert value["semantic_entry_sha256"] == runtime.SEMANTIC_SHA256 != value["entry_sha256"]
    assert not (root / "backend/evals").exists()


def test_source_child_refuses_foreign_committed_entry_without_executing_it(tmp_path):
    trusted = _owned_code_checkout(tmp_path / "trusted")
    foreign = _owned_code_checkout(tmp_path / "foreign")
    marker = foreign / "foreign-executed.txt"
    with (foreign / control.ENTRY).open("a") as stream:
        stream.write("\nPath(" + repr(str(marker)) + ").write_text('executed')\n")
    _git(foreign, "add", control.ENTRY)
    _git(foreign, "-c", "user.name=Owned source test", "-c", "user.email=owned@example.invalid", "commit", "-qm", "foreign source")
    result = _source_cli(trusted, foreign)
    assert result.returncode == 1 and not marker.exists()
    assert json.loads(result.stdout)["processing_authorized"] is False


def test_source_guard_checks_bytes_when_Git_assumes_semantic_entry_unchanged(tmp_path):
    root = _owned_code_checkout(tmp_path / "owned")
    _git(root, "update-index", "--assume-unchanged", control.SEMANTIC_ENTRY)
    with (root / control.SEMANTIC_ENTRY).open("a") as stream:
        stream.write("\n# owned changed semantic source\n")
    assert _git(root, "status", "--porcelain") == b""
    result = _source_cli(root, root)
    assert result.returncode == 1 and json.loads(result.stdout)["native_approval"] is False


def test_ignored_initializer_is_denied_before_it_can_run(tmp_path):
    root = _owned_code_checkout(tmp_path / "owned")
    (root / ".git/info/exclude").write_text("backend/scripts/__init__.py\n")
    marker = root / "ignored-executed.txt"
    (root / "backend/scripts/__init__.py").write_text("from pathlib import Path\nPath(" + repr(str(marker)) + ").write_text('executed')\n")
    assert _git(root, "status", "--porcelain") == b""
    result = _source_cli(root, root)
    assert result.returncode != 0 and not marker.exists()


@pytest.mark.parametrize("name", ["infra.py", "backend/scripts.py"])
def test_ignored_namespace_shadow_cannot_execute_before_child_guards(tmp_path, name):
    root = _owned_code_checkout(tmp_path / "owned")
    (root / ".git/info/exclude").write_text(name + "\n")
    marker = root / "shadow-executed.txt"
    (root / name).write_text("from pathlib import Path\nPath(" + repr(str(marker)) + ").write_text('executed')\n")
    assert _git(root, "status", "--porcelain") == b""
    result = _source_cli(root, root)
    assert result.returncode != 0 and not marker.exists()


def test_ignored_linked_namespace_shadow_is_refused_before_foreign_code_executes(tmp_path):
    root = _owned_code_checkout(tmp_path / "owned")
    (root / ".git/info/exclude").write_text("backend/scripts.py\n")
    marker = tmp_path / "linked-shadow-executed.txt"
    foreign = tmp_path / "foreign-shadow.py"
    foreign.write_text("from pathlib import Path\nPath(" + repr(str(marker)) + ").write_text('executed')\n")
    (root / "backend/scripts.py").symlink_to(foreign)
    assert _git(root, "status", "--porcelain") == b""
    result = _source_cli(root, root)
    assert result.returncode != 0 and not marker.exists()


def test_ignored_sourceless_shadow_with_innocent_filename_is_refused_before_execution(tmp_path):
    root = _owned_code_checkout(tmp_path / "owned")
    (root / ".git/info/exclude").write_text("backend/scripts.pyc\n")
    marker = root / "sourceless-shadow-executed.txt"
    # Spoof a real tracked co_filename: a source-hash/exec-only fence cannot
    # establish the loader origin of this otherwise valid legacy pyc.
    code = compile("from pathlib import Path\nPath(" + repr(str(marker)) + ").write_text('executed')\n",
        str(root / "backend/scripts/qualify_native_quality_acquisition.py"), "exec")
    header = importlib.util.MAGIC_NUMBER + struct.pack("<III", 0, 1, 1)
    (root / "backend/scripts.pyc").write_bytes(header + marshal.dumps(code))
    assert _git(root, "status", "--porcelain") == b""
    result = _source_cli(root, root)
    assert result.returncode != 0 and not marker.exists()


def test_valid_ignored_poisoned_bytecode_is_not_loaded_by_source_child(tmp_path):
    root = _owned_code_checkout(tmp_path / "owned")
    (root / ".git/info/exclude").write_text("**/__pycache__/\n")
    source = root / "backend/scripts/qualify_native_quality_acquisition.py"
    marker = root / "cached-code-executed.txt"
    code = compile("from pathlib import Path\nPath(" + repr(str(marker)) + ").write_text('executed')\n"
        + "exec(compile(" + repr(source.read_text()) + ", __file__, 'exec'))\n", str(source), "exec")
    cached = Path(importlib.util.cache_from_source(str(source)))
    cached.parent.mkdir()
    header = importlib.util.MAGIC_NUMBER + struct.pack("<III", 0, int(source.stat().st_mtime), source.stat().st_size)
    cached.write_bytes(header + marshal.dumps(code))
    assert cached.read_bytes()[:16] == header and marshal.loads(cached.read_bytes()[16:]).co_filename == str(source)
    assert _git(root, "status", "--porcelain") == b""
    result = _source_cli(root, root)
    assert result.returncode == 0, result.stderr.decode()
    assert not marker.exists()


def test_source_child_requires_explicit_private_empty_cache_prefix(tmp_path):
    root = _owned_code_checkout(tmp_path / "owned")
    result = subprocess.run([sys.executable, "-I", "-B", str(root / control.COMPONENTS[1]),
        "--root", str(root), "--source-child"], env={"PATH": "/usr/bin:/bin", "HOME": str(root)},
        cwd=root, stdin=subprocess.DEVNULL, capture_output=True, timeout=30)
    assert result.returncode != 0


def test_committed_bootstrap_helper_successor_requires_explicit_new_binding(tmp_path):
    root = _owned_code_checkout(tmp_path / "owned")
    source = root / "backend/scripts/qualify_native_quality_acquisition.py"
    marker = root / "changed-helper-executed.txt"
    with source.open("a") as stream:
        stream.write("\nfrom pathlib import Path\nPath(" + repr(str(marker)) + ").write_text('executed')\n")
    _git(root, "add", str(source.relative_to(root)))
    _git(root, "-c", "user.name=Owned source test", "-c", "user.email=owned@example.invalid", "commit", "-qm", "owned helper successor")
    result = _source_cli(root, root)
    assert result.returncode != 0 and not marker.exists()


def test_runner_source_resolves_private_temp_alias_before_cache_and_HOME(runtime_offline, monkeypatch, tmp_path):
    real = tmp_path / "real-private"
    real.mkdir(mode=0o700)
    alias = tmp_path / "private-alias"
    alias.symlink_to(real, target_is_directory=True)

    @contextlib.contextmanager
    def temporary(**kwargs):
        yield str(alias)

    expected = {key: "a" * 64 for key in ("qualification_sha256", "runtime_dependencies_sha256",
        "component_sha256", "entry_sha256", "semantic_entry_sha256", "channel_sha256")}
    expected.update(release="b" * 40, git_tree="c" * 40)
    observed = []

    def run(argv, **kwargs):
        observed.append((argv, kwargs))
        return SimpleNamespace(returncode=0, stdout=json.dumps(expected).encode(), stderr=b"")

    monkeypatch.setattr(control.tempfile, "TemporaryDirectory", temporary)
    monkeypatch.setattr(subprocess, "run", run)
    assert control.runner_source(tmp_path) == expected
    argv, kwargs = observed[0]
    assert argv[3:5] == ["-X", "pycache_prefix=" + str(real / "cache")]
    assert kwargs["cwd"] == real and kwargs["env"]["HOME"] == kwargs["env"]["XDG_CONFIG_HOME"] == str(real)


def test_in_root_interpreter_dependencies_are_distinct_from_project_links(runtime_offline, tmp_path, monkeypatch):
    root = tmp_path / "project"
    installed = root / "backend/.venv/lib/python3.12/site-packages"
    installed.mkdir(parents=True)
    sdk = installed / "owned_sdk.py"
    sdk.write_text("SDK = True\n")
    monkeypatch.setattr(sys, "prefix", str(root / "backend/.venv"))
    monkeypatch.setattr(control.sysconfig, "get_paths", lambda: {"purelib": str(installed), "platlib": str(installed),
        "userlib": str(root / "user-site")})
    dependencies = control._dependency_roots()
    assert dependencies == ((installed, installed),)
    assert not control._project_source(sdk, root, dependencies)
    project = root / "backend/scripts.py"
    project.symlink_to(sdk)
    assert control._project_source(project, root, dependencies)
    assert control._project_source(root / "backend/evaluation/owned.py", root, dependencies)
    assert control._project_source(root / "user-site/owned.py", root, dependencies)
