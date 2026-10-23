"""Terminal recovery boundaries; native/storage observations are synthetic."""
from __future__ import annotations

import base64
import copy
import json
from types import SimpleNamespace
from urllib.parse import parse_qs, unquote, urlencode, urlsplit

import pytest
from backend.tests import test_document_refresh as local
from backend.tests import test_document_refresh_probe as native
from backend.tests import test_document_refresh_transition as transition
from infra.gcp import document_refresh_auth as auth
from infra.gcp import document_refresh_recovery as recovery

from scripts import document_refresh as refresh

p = native.p
NEW_NONCE = "f" * 32
JOB = "hirewiz-doc-six-" + native.NONCE[:12]


class Failed(native.Fake):
    def __init__(self, *, historical_v1=False):
        super().__init__()
        self.created = self.ran = True
        self.j, self.ex = native.completed()
        if historical_v1:
            self.j["template"] = p.desired(native.IMAGE, native.NONCE, historical_v1=True)["template"]
            self.ex["template"] = copy.deepcopy(self.j["template"]["template"])
        self.ex.update(succeededCount=0, failedCount=1, retriedCount=0,
                       conditions=[{"type": "Completed", "state": "CONDITION_FAILED",
                                    "executionReason": "NON_ZERO_EXIT_CODE"}])
        self.j["latestCreatedExecution"] = {"name": self.ex["name"].rsplit("/", 1)[-1],
                                           "completionStatus": "EXECUTION_FAILED"}
        self.run_done, self.run_error = True, 10
        self.report = native.historical_payload() if historical_v1 else native.payload()
        diagnostic = native.diag(1, "not_validated", "not_started")
        diagnostic["failure_phase"] = None
        self.report.update(failure_stage="identity_preflight",
            identity_preflight={"passed": False, "cleanup_passed": True, "diagnostic": diagnostic},
            cases=[{"case": row["case"], "attempted": False, "passed": False} for row in self.report["cases"]])
        self.report["all_six_passed" if historical_v1 else "all_required_passed"] = False

    def operation(self, name):
        if not name.endswith("/run"):
            return super().operation(name)
        return {"name": name, "done": self.run_done, "error": {"code": self.run_error},
                "metadata": {"@type": "type.googleapis.com/google.cloud.run.v2.Execution", **copy.deepcopy(self.ex)}}

    def entries(self, query, token):
        assert token is None and self.ex["name"].rsplit("/", 1)[-1] in query
        return {"entries": [native.event(self.j, self.ex, copy.deepcopy(self.report))]}


class Storage:
    def __init__(self):
        self.objects, self.calls = {}, []
        self.lost_ack = False

    def request(self, uri, *, method, redirections, body=None, headers=None):
        assert redirections == 0
        self.calls.append((method, uri))
        url, query = urlsplit(uri), parse_qs(urlsplit(uri).query)
        if method == "POST":
            assert query["ifGenerationMatch"] == ["0"] and query["uploadType"] == ["media"]
            assert headers == {"Content-Type": "application/json"}
            name = query["name"][0]
            assert name == recovery.object_name(native.JOB_UID)
            if name in self.objects:
                return SimpleNamespace(status=412), b""
            self.objects[name] = body
            if self.lost_ack:
                raise RuntimeError("synthetic private unknown acknowledgment")
            return SimpleNamespace(status=200), json.dumps({"name": name,
                "bucket": refresh.PROJECT + "_cloudbuild", "generation": "1"}).encode()
        name = unquote(url.path.split("/o/", 1)[1])
        if name not in self.objects:
            return SimpleNamespace(status=404), b""
        if "/download/" in url.path:
            assert query == {"alt": ["media"], "generation": ["1"]}
            return SimpleNamespace(status=200), self.objects[name]
        return SimpleNamespace(status=200), b"{}"

    def close(self):
        pass


def write_run(path, identity, proof, *, historical_v1=False):
    path.mkdir(mode=0o700)
    (path / "native-proof").mkdir(mode=0o700)
    source = {"bucket": identity["source_bucket"], "object": identity["source_object"],
              "generation": identity["source_generation"]}
    encoded = base64.urlsafe_b64encode(identity["build_id"].encode()).decode().rstrip("=")
    values = {
        "build-started.json": {"nonce": native.NONCE, "recipe_sha256": identity["refresh_dockerfile_sha256"],
            "source_archive_sha256": identity["source_archive_sha256"], "automatic_replay": False,
            "unknown_outcome": "reconcile_exact_native_object_and_build_only"},
        "source-upload-acknowledged.json": source,
        "build-create-started.json": {"nonce": native.NONCE, "automatic_replay": False,
            "request_sha256": recovery.sha(recovery.canonical(refresh.build_request(native.NONCE, source)))},
        "build-acknowledged.json": {"nonce": native.NONCE, "build_id": identity["build_id"],
            "operation_name": "operations/build/" + refresh.PROJECT + "/" + encoded, "automatic_replay": False},
        "native-build-identity.json": {key: value for key, value in identity.items()
                                      if key not in {"source_sha256", "local_native_proof_sha256"}},
        "local-native-proof.json": proof,
        "native-proof/binding.json": {"image": native.IMAGE, "build_identity": identity,
            "probe_sha256": recovery.sha(p.rendered_payload(native.IMAGE, historical_v1=historical_v1).encode())},
        "native-proof/create.started.json": {"body_sha256": recovery.sha(p.canonical(p.desired(native.IMAGE, native.NONCE, historical_v1=historical_v1))),
                                            "automatic_replay": False},
        "native-proof/create.acknowledged.json": {"name": p.PARENT + "/operations/create"},
        "native-proof/run.started.json": {"job_name": native.job()["name"], "job_uid": native.JOB_UID,
                                          "etag": "original-etag", "automatic_replay": False},
        "native-proof/run.acknowledged.json": {"name": p.PARENT + "/operations/run"},
    }
    for name, value in values.items():
        refresh.write_new(path / name, value)


@pytest.fixture(params=(False, True), ids=("current_v2", "frozen_v1"))
def setup(tmp_path, monkeypatch, request):
    history = tmp_path / "history"
    history.mkdir(mode=0o700)
    original = history / "original"
    proof = local.proof()
    historical_v1 = request.param
    if historical_v1:
        proof["source_sha256"] = json.loads(p.legacy_source("policy.json"))["source_sha256"]
        proof["corpus"] = proof["corpus"][:8]
        proof["docx_layout_corpus"][0]["layout"]["renderer"]["policy_sha256"] = p.LEGACY_POLICY
    raw = (json.dumps(proof, sort_keys=True, indent=2, allow_nan=False) + "\n").encode()
    identity = native.build()
    identity.update(source_sha256=proof["source_sha256"], local_native_proof_sha256=recovery.sha(raw),
        builder_service_account=refresh.BUILDER, source_bucket=refresh.PROJECT + "_cloudbuild",
        source_object="hirewiz-document-refresh-source/" + native.NONCE + "/source.tar.gz",
        source_generation="7", fixed_renderer_base=refresh.RENDERER_BASE)
    write_run(original, identity, proof, historical_v1=historical_v1)
    failed, storage, joins = Failed(historical_v1=historical_v1), Storage(), []
    monkeypatch.setattr(refresh, "accepted", local.contract)
    def verify(value, **kwargs):
        assert value == identity and kwargs == ({"historical_v1": True} if historical_v1 else {})
        return copy.deepcopy(identity)
    monkeypatch.setattr(refresh, "verify_native_build", verify)
    monkeypatch.setattr(refresh, "native_http", lambda: storage)
    monkeypatch.setattr(refresh, "infra", lambda name: p if name == "document_refresh_probe" else transition.transition)
    monkeypatch.setattr(p, "Clients", lambda: failed)
    monkeypatch.setattr(recovery, "joined", lambda *args: joins.append(args[-1]))
    monkeypatch.setattr(recovery.uuid, "uuid4", lambda: SimpleNamespace(hex=NEW_NONCE))
    return SimpleNamespace(history=history, original=original, identity=identity, native=failed,
                           storage=storage, joins=joins, root=tmp_path, historical_v1=historical_v1)


def output(setup, name="next"):
    path = setup.root / name
    path.mkdir(mode=0o700)
    return path


def test_explicit_recovery_retains_raw_bytes_and_claims_only_one_fresh_nonce(setup):
    before = recovery.snapshot(setup.original, refresh)
    target = output(setup)
    assert recovery.prepare(target, setup.history, JOB, refresh_module=refresh) == NEW_NONCE
    assert recovery.snapshot(setup.original, refresh) == before
    record = target / "terminal-recoveries" / JOB
    assert recovery.snapshot(record / "original", refresh) == before
    body = json.loads((record / "disposition.json").read_bytes())
    assert body["status"] == "terminal_failure_reconciled_not_acceptance"
    assert body["failed_job_replayed"] is False and body["successor_nonce"] == NEW_NONCE
    assert setup.joins == [{JOB}]
    assert [method for method, _ in setup.storage.calls] == ["GET", "POST", "GET"]
    assert setup.native.calls["create"] == setup.native.calls["start"] == setup.native.calls["delete"] == 0
    assert not (target / "result.json").exists() and not (target / "transition").exists()
    with pytest.raises(refresh.RefreshDenied, match="claim_unknown_or_consumed"):
        recovery.prepare(output(setup, "second"), setup.history, JOB, refresh_module=refresh)
    assert sum(method == "POST" for method, _ in setup.storage.calls) == 1


def test_default_run_reverifies_and_carries_disposition_without_relabeling_failure(setup):
    recovered = output(setup, "recovered")
    recovery.prepare(recovered, setup.history, JOB, refresh_module=refresh)
    refresh.write_new(recovered / "result.json", {"status": "document_refresh_serving", "admission_restored": True})
    recovered.rename(setup.history / "completed-recovery")
    target = output(setup)
    assert recovery.prepare(target, setup.history, None, refresh_module=refresh) is None
    original = target / "terminal-recoveries" / JOB / "original"
    assert recovery.snapshot(original, refresh) == recovery.snapshot(setup.original, refresh)
    assert not (setup.original / "result.json").exists()
    assert sum(method == "POST" for method, _ in setup.storage.calls) == 1
    with pytest.raises(refresh.RefreshDenied, match="missing_or_consumed_selection"):
        recovery.prepare(output(setup, "again"), setup.history, JOB, refresh_module=refresh)


def test_three_successful_recent_artifacts_drop_old_native_retention_dependency(setup, monkeypatch):
    import shutil

    recovered = output(setup, "recovered")
    recovery.prepare(recovered, setup.history, JOB, refresh_module=refresh)
    refresh.write_new(recovered / "result.json", {"status": "document_refresh_serving", "admission_restored": True})
    # Preserve the original artifact, outside the workflow's last-three window.
    setup.original.rename(setup.root / "retained-old-artifact")
    recovered.rename(setup.history / "success1")
    for name in ("success2", "success3"):
        shutil.copytree(setup.history / "success1", setup.history / name)
    monkeypatch.setattr(p, "Clients", lambda: pytest.fail("Old execution/logs are unavailable and unnecessary"))
    monkeypatch.setattr(refresh, "verify_native_build", lambda _: pytest.fail("Old source/LRO must not be fetched"))
    target = output(setup)
    before = list(setup.storage.calls)
    assert recovery.prepare(target, setup.history, None, refresh_module=refresh) is None
    assert not list(target.iterdir()) and setup.storage.calls == before
    assert setup.storage.objects  # Immutable consumed claim remains untouched.


def test_carried_disposition_requires_fresh_exact_claim_readback(setup):
    recovered = output(setup, "recovered")
    recovery.prepare(recovered, setup.history, JOB, refresh_module=refresh)
    refresh.write_new(recovered / "result.json", {"status": "document_refresh_serving", "admission_restored": True})
    recovered.rename(setup.history / "completed-recovery")
    name = recovery.object_name(native.JOB_UID)
    setup.storage.objects[name] += b"unverified changed bytes"
    with pytest.raises(refresh.RefreshDenied, match="claim_unverified"):
        recovery.prepare(output(setup), setup.history, None, refresh_module=refresh)
    assert sum(method == "POST" for method, _ in setup.storage.calls) == 1


def test_new_unknown_successor_is_not_hidden_by_original_failure_disposition(setup):
    recovered = output(setup, "recovered")
    recovery.prepare(recovered, setup.history, JOB, refresh_module=refresh)
    recovered.rename(setup.history / "new-unknown-run")
    before = list(setup.storage.calls)
    with pytest.raises(refresh.RefreshDenied, match="prior_unknown_reconcile_only"):
        recovery.prepare(output(setup), setup.history, None, refresh_module=refresh)
    assert setup.storage.calls == before


def test_scheduled_default_cannot_recover_unselected_failure(setup):
    with pytest.raises(refresh.RefreshDenied, match="prior_unknown_reconcile_only"):
        recovery.prepare(output(setup), setup.history, None, refresh_module=refresh)
    assert not setup.storage.calls


@pytest.mark.parametrize("change", ("unknown_policy", "foreign_image"))
def test_saved_recovery_cannot_choose_profile_over_actual_native_job(setup, change):
    container = setup.native.j["template"]["template"]["containers"][0]
    if change == "unknown_policy":
        next(row for row in container["env"] if row["name"] == "DOCUMENT_WORKER_POLICY_SHA256")["value"] = "0" * 64
    else:
        container["image"] = native.NEXT
    with pytest.raises(p.NativeProofDenied, match="serving_policy_unverified"):
        recovery.prepare(output(setup), setup.history, JOB, refresh_module=refresh)
    assert not setup.storage.calls and setup.native.calls["create"] == setup.native.calls["start"] == 0


@pytest.mark.parametrize("marker", ["transition", "native-proof/acceptance.json", "native-proof/delete.started.json"])
def test_transition_acceptance_or_cleanup_marker_blocks_recovery(setup, marker):
    path = setup.original / marker
    path.mkdir(mode=0o700) if marker == "transition" else refresh.write_new(path, {})
    with pytest.raises(refresh.RefreshDenied):
        recovery.prepare(output(setup), setup.history, JOB, refresh_module=refresh)
    assert not setup.storage.calls and not setup.joins


@pytest.mark.parametrize("name", ["native-proof/create.acknowledged.json", "native-proof/run.acknowledged.json",
                                  "build-acknowledged.json", "source-upload-acknowledged.json"])
def test_missing_original_ack_refuses_without_new_namespace_or_launch(setup, name):
    (setup.original / name).unlink()
    with pytest.raises(refresh.RefreshDenied, match="prior_unknown_reconcile_only"):
        recovery.prepare(output(setup), setup.history, JOB, refresh_module=refresh)
    assert not setup.storage.calls


@pytest.mark.parametrize("change", ["inflight", "other_error", "multiple", "retry", "success", "template",
                                    "foreign_latest", "foreign_uid", "cleanup", "attempted", "passed"])
def test_native_failure_boundaries_remain_closed(setup, change):
    value = setup.native
    if change == "inflight":
        value.run_done = False
    elif change == "other_error":
        value.run_error = 3
    elif change == "multiple":
        value.j["executionCount"] = 2
    elif change == "retry":
        value.ex["retriedCount"] = 1
    elif change == "success":
        value.ex.update(failedCount=0, succeededCount=1)
    elif change == "template":
        value.ex["template"]["maxRetries"] = 1
    elif change == "foreign_latest":
        value.j["latestCreatedExecution"]["name"] = "foreign-job-abcde"
    elif change == "foreign_uid":
        value.ex["uid"] = "not-a-uuid"
    elif change == "cleanup":
        value.report["identity_preflight"]["cleanup_passed"] = False
    elif change == "attempted":
        value.report["cases"][0]["attempted"] = True
    else:
        value.report["all_six_passed" if setup.historical_v1 else "all_required_passed"] = True
    with pytest.raises((refresh.RefreshDenied, p.NativeProofDenied, ValueError)):
        recovery.prepare(output(setup), setup.history, JOB, refresh_module=refresh)
    assert not setup.storage.calls
    assert value.calls["create"] == value.calls["start"] == value.calls["delete"] == 0


def test_unknown_claim_ack_never_authorizes_retry_or_pipeline(setup):
    setup.storage.lost_ack = True
    with pytest.raises(RuntimeError):
        recovery.prepare(output(setup), setup.history, JOB, refresh_module=refresh)
    setup.storage.lost_ack = False
    with pytest.raises(refresh.RefreshDenied, match="claim_unknown_or_consumed"):
        recovery.prepare(output(setup, "again"), setup.history, JOB, refresh_module=refresh)
    assert sum(method == "POST" for method, _ in setup.storage.calls) == 1
    assert setup.native.calls["start"] == 0


def test_strict_current_four_service_join_and_caller_pins_remain_required(monkeypatch):
    http = transition.Http()
    controller = transition.transition.Controller(http, p, None)
    reader = SimpleNamespace(http=http, revision=controller.revision,
        service=lambda name: http.services[name.rsplit("/", 1)[-1]],
        preflight=lambda: {"service": http.services["hirewiz-document-inspector"]})
    monkeypatch.setattr(refresh, "infra", lambda _: transition.transition)
    recovery.joined(reader, p, refresh, {JOB})
    http.services["ai-resume-parser"]["template"]["containers"][0]["env"][0]["value"] = native.IMAGE
    with pytest.raises(p.NativeProofDenied):
        recovery.joined(reader, p, refresh, {JOB})
    assert http.writes == []


def test_invalid_explicit_nonce_refuses_before_any_provider(monkeypatch, tmp_path):
    monkeypatch.setattr(refresh, "native_http", lambda: pytest.fail("Invalid nonce must not access a provider"))
    for value in ("", "old-job", "a" * 31, "g" * 32):
        with pytest.raises(refresh.RefreshDenied, match="build_nonce_invalid"):
            refresh.native_build(tmp_path, b"public recipe", nonce=value)
    request = refresh.build_request(NEW_NONCE, {"bucket": "synthetic", "object": "owned", "generation": "1"})
    arguments = request["steps"][0]["args"]
    assert arguments[arguments.index("--build-arg") + 1] == "DOCUMENT_DEFINITIONS_NONCE=" + NEW_NONCE


def test_only_exact_uid_claim_create_with_generation_zero_is_routable():
    prefix = "https://storage.googleapis.com/upload/storage/v1/b/" + refresh.PROJECT + "_cloudbuild/o?"
    query = {"uploadType": "media", "name": recovery.object_name(native.JOB_UID), "ifGenerationMatch": "0"}
    assert auth.allowed("POST", prefix + urlencode(query))
    for change in ({"ifGenerationMatch": "1"}, {"name": "hirewiz-document-refresh-source/terminal-recovery/arbitrary.json"},
                   {"name": "hirewiz-document-refresh-source/terminal-recovery/../source.tar.gz"},
                   {"uploadType": "resumable"}, {"unrelated": "1"}):
        assert not auth.allowed("POST", prefix + urlencode({**query, **change}))
    assert not auth.allowed("DELETE", prefix + urlencode(query))
    assert not auth.release_allowed("POST", prefix + urlencode(query))


def test_refresh_binds_claim_nonce_to_new_full_pipeline_and_never_reruns_failed_job(setup, monkeypatch):
    calls = []
    def candidate(path, *, publish, now, nonce):
        assert publish is True and nonce == NEW_NONCE
        calls.append("new_candidate_21")
        return {"candidate_registry_digest": native.NEXT, "build_identity": {"nonce": nonce}}
    def successor(image, build, journal):
        assert image == native.NEXT and build == {"nonce": NEW_NONCE}
        assert journal == setup.root / "next/native-proof"
        calls.append("new_native_6")
        return SimpleNamespace(definition_expires_at="2026-10-24T06:24:00+00:00")
    def promote(image, build, journal, target, **kwargs):
        assert calls == ["new_candidate_21", "new_native_6"]
        calls.append("transition")
        return {"status": "document_refresh_serving", "admission_restored": True,
                "prior_image": refresh.RENDERER_BASE}
    monkeypatch.setattr(refresh, "candidate", candidate)
    monkeypatch.setattr(p, "run_successor", successor)
    monkeypatch.setattr(refresh, "infra", lambda name: {
        "document_refresh_probe": p, "document_refresh_recovery": recovery,
        "document_refresh_transition": SimpleNamespace(transition=promote),
    }[name])
    result = refresh.refresh(output(setup), publish=True, now=local.NOW,
                             history=setup.history, recover_failed_native_job=JOB)
    assert result["status"] == "document_refresh_serving"
    assert calls == ["new_candidate_21", "new_native_6", "transition"]
    assert setup.native.calls["start"] == 0


def test_workflow_recovery_is_dispatch_only_and_passes_user_input_as_inert_data(tmp_path):
    import os
    import subprocess
    import sys

    import yaml

    workflow = yaml.safe_load((refresh.ROOT / ".github/workflows/document-refresh.yml").read_bytes())
    job = workflow["jobs"]["maintenance"]
    assert job["env"]["RECOVER_FAILED_NATIVE_JOB"] == (
        "${{ github.event_name == 'workflow_dispatch' && inputs.recover_failed_native_job || '' }}")
    step = next(row for row in job["steps"] if row.get("name") == "Build, prove, transition exact pins and restore admission")
    binary = tmp_path / "backend/.venv/bin/python"
    binary.parent.mkdir(parents=True)
    binary.write_text("#!" + sys.executable + "\nimport json,os,sys\n"
                      "from pathlib import Path\nPath(os.environ['CAPTURE_ARGS']).write_text(json.dumps(sys.argv[1:]))\n")
    binary.chmod(0o700)
    capture = tmp_path / "args.json"
    payload = "$(touch SHOULD_NOT_EXIST) ; exact job data"
    result = subprocess.run(["/bin/bash", "-eu", "-c", step["run"]], cwd=tmp_path,
        env={**os.environ, "RECOVER_FAILED_NATIVE_JOB": payload, "HISTORY_DIRECTORY": "private history",
             "OUTPUT_DIRECTORY": "private output", "CAPTURE_ARGS": str(capture)}, capture_output=True, timeout=5)
    assert result.returncode == 0 and not (tmp_path / "SHOULD_NOT_EXIST").exists()
    arguments = json.loads(capture.read_bytes())
    assert arguments[-2:] == ["--recover-failed-native-job", payload]
