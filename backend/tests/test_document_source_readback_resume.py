from __future__ import annotations

import importlib
import io
import json
import socket
import subprocess
import sys
import zipfile
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / "infra/gcp")]
resume = importlib.import_module("infra.gcp.document_source_readback_resume")
prior = importlib.import_module("infra.gcp.document_refresh_probe")


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def denied(*args, **kwargs):
        raise AssertionError("network and ports forbidden")
    for name in ("connect", "connect_ex", "bind", "listen"):
        monkeypatch.setattr(socket.socket, name, denied)
    monkeypatch.setattr(socket, "create_connection", denied)


def write(path, value):
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    path.write_bytes(value if type(value) is bytes else resume.canonical(value))
    path.chmod(0o600)


def fake_build():
    def write_new(path, value):
        with path.open("xb") as stream:
            stream.write(resume.canonical(value))
        path.chmod(0o600)
    return SimpleNamespace(PROJECT="ai-resume-parser-482412", write_new=write_new)


def native():
    result = {}
    for mode, short, suffix in [("inventory", "1f2186a3d9a0", "7vc5n"), ("corpus", "0e31d2e468f2", "cshdp")]:
        job = "projects/ai-resume-parser-482412/locations/us-central1/jobs/hirewiz-doc-six-" + short
        result[mode] = {"job": job, "job_uid": resume.JOBS[mode][0],
            "execution": job + "/executions/hirewiz-doc-six-" + short + "-" + suffix, "execution_uid": resume.JOBS[mode][1]}
    return result


def body():
    return resume.claim_body({"image": resume.IMAGE}, native(), {"actual": "preimage"},
                            {"run": 999, "attempt": 1, "head": "a" * 40, "path": resume.WORKFLOW})


class Http:
    def __init__(self, *, exists=404, post=201, change=None, wrong_read=False, uncertain=False):
        self.exists, self.post, self.change = exists, post, change
        self.wrong_read, self.uncertain, self.calls, self.body = wrong_read, uncertain, [], None

    def request(self, uri, method="GET", body=None, **kwargs):
        self.calls.append((method, uri))
        if method == "POST":
            assert parse_qs(urlsplit(uri).query) == {"uploadType": ["media"], "name": [resume.CLAIM_OBJECT], "ifGenerationMatch": ["0"]}
            self.body, self.exists = body, 200
            if self.uncertain:
                raise TimeoutError("synthetic unknown acknowledgment")
            ack = {"name": resume.CLAIM_OBJECT, "bucket": "ai-resume-parser-482412_cloudbuild", "generation": "123"}
            ack.update(self.change or {})
            return SimpleNamespace(status=self.post), resume.canonical(ack)
        if "/download/" in uri:
            assert parse_qs(urlsplit(uri).query) == {"alt": ["media"], "generation": ["123"]}
            return SimpleNamespace(status=200), b"changed" if self.wrong_read else self.body
        return SimpleNamespace(status=self.exists), b"{}"


def test_fixed_scope_and_every_original_worker_source_byte_unchanged():
    profile = importlib.import_module("infra.gcp.document_source_candidate")
    assert len(resume.FILES) == 21
    assert resume.sha(resume.canonical(resume.FILES)) == "79bf3b8761fd51125a2dc785b408d7c96eed685d624c07735e9ea412f8ae9cc0"
    assert len(profile.SOURCES) == 22 and len(profile.CONTRACT_PATHS) == 14
    for name in set(profile.CONTRACT_PATHS) | set(profile.SOURCES) | {profile.POLICY_PATH}:
        assert (ROOT / name).read_bytes() == subprocess.check_output(["git", "show", resume.ORIGINAL_SOURCE + ":" + name], cwd=ROOT)
    assert subprocess.check_output(["git", "diff", resume.ORIGINAL_SOURCE, "--", "backend/app"], cwd=ROOT) == b""


def test_create_only_claim_fits_existing_namespace_and_second_directory_cannot_resend(tmp_path):
    auth = importlib.import_module("infra.gcp.document_refresh_auth")
    http = Http()
    assert resume.claim(http, body(), tmp_path, fake_build())["generation"] == "123"
    assert [m for m, _ in http.calls] == ["GET", "POST", "GET"]
    assert all(auth.allowed(m, u) for m, u in http.calls)
    other = tmp_path / "next"
    other.mkdir(mode=0o700)
    with pytest.raises(resume.ResumeDenied, match="claim_exists_or_unknown"):
        resume.claim(http, body(), other, fake_build())
    assert sum(m == "POST" for m, _ in http.calls) == 1


@pytest.mark.parametrize("status", [200, 403, 429, 500])
def test_existing_or_unknown_claim_never_sends(tmp_path, status):
    http = Http(exists=status)
    with pytest.raises(resume.ResumeDenied, match="claim_exists_or_unknown"):
        resume.claim(http, body(), tmp_path, fake_build())
    assert [m for m, _ in http.calls] == ["GET"]


@pytest.mark.parametrize("mode", ["timeout", "server", "precondition", "readback", "bad_name", "bad_generation", "local_ack"])
def test_claim_unknown_or_ack_failure_stops_and_never_resends(tmp_path, mode):
    http = Http(uncertain=mode == "timeout", post=500 if mode == "server" else 412 if mode == "precondition" else 201,
                wrong_read=mode == "readback", change={"name": "wrong"} if mode == "bad_name" else {"generation": 123} if mode == "bad_generation" else None)
    build = fake_build()
    if mode == "local_ack":
        original = build.write_new
        def fail(path, value):
            if path.name.endswith("acknowledged.json"):
                raise OSError("synthetic disk refusal")
            return original(path, value)
        build.write_new = fail
    with pytest.raises((resume.ResumeDenied, TimeoutError, OSError)):
        resume.claim(http, body(), tmp_path, build)
    assert (tmp_path / "continuation-claim.started.json").is_file()
    other = tmp_path / "next"
    other.mkdir(mode=0o700)
    with pytest.raises(resume.ResumeDenied, match="claim_exists_or_unknown"):
        resume.claim(http, body(), other, fake_build())
    assert sum(m == "POST" for m, _ in http.calls) == 1


def test_only_exact_empty_event_read_can_retry():
    calls, sleeps = [], []
    def eventual():
        calls.append(1)
        if len(calls) < 3:
            raise prior.NativeProofDenied("document_candidate_event_missing")
        return "actual-read"
    assert resume.empty_event_retry(eventual, sleep=sleeps.append) == "actual-read"
    assert len(calls) == 3 and sleeps == [2, 2]


@pytest.mark.parametrize("kind,code", [(prior.NativeProofDenied, "document_candidate_log_scope_or_uniqueness"),
    (prior.NativeProofDenied, "document_native_outcome_unknown_reconcile_only"), (TimeoutError, "unknown")])
def test_other_error_never_retries(kind, code):
    calls = []
    def fail():
        calls.append(1)
        raise kind(code)
    with pytest.raises(kind):
        resume.empty_event_retry(fail, sleep=lambda _: pytest.fail("unexpected retry"))
    assert len(calls) == 1


def operations():
    expected, files, records = native(), {}, {}
    class Probe:
        build = {}
        def handles(self, _):
            return {m: {"nonce": m} for m in expected}
        def desired(self, image, nonce, mode):
            return {"image": image, "mode": mode}
        def _observation(self, clients, mode, build):
            n = expected[mode]
            return ({"name": n["job"], "uid": n["job_uid"], "executionCount": 1},
                    {"name": n["execution"], "uid": n["execution_uid"]}, {})
    probe = Probe()
    for mode, n in expected.items():
        p = "native-proof/" + mode
        files[p + ".create.started.json"] = resume.canonical({"body_sha256": resume.sha(resume.canonical(probe.desired(resume.IMAGE, mode, mode))), "automatic_replay": False})
        files[p + ".run.started.json"] = resume.canonical({"name": n["job"], "uid": n["job_uid"], "etag": "original-etag", "automatic_replay": False})
        for action, kind in [("create", "Job"), ("run", "Execution")]:
            name = "projects/ai-resume-parser-482412/locations/us-central1/operations/" + mode + "-" + action
            files[p + "." + action + ".acknowledged.json"] = resume.canonical({"name": name})
            records[name] = {"name": name, "done": True, "response": {"@type": "type.googleapis.com/google.cloud.run.v2." + kind,
                "name": n["job"] if action == "create" else n["execution"], "uid": n["job_uid"] if action == "create" else n["execution_uid"]}}
    return SimpleNamespace(operation=lambda name: records[name]), files, probe, records


def test_all_four_original_operations_bind_job_and_execution_uids():
    client, files, probe, _ = operations()
    assert resume.verify_operations(client, files, probe) == native()


@pytest.mark.parametrize("mode,action,change", [("inventory", "create", "uid"), ("corpus", "run", "uid"),
    ("corpus", "create", "pending"), ("inventory", "run", "error"), ("corpus", "run", "kind")])
def test_operation_drift_or_unknown_is_not_adopted(mode, action, change):
    client, files, probe, records = operations()
    row = records[json.loads(files["native-proof/" + mode + "." + action + ".acknowledged.json"])["name"]]
    if change == "uid":
        row["response"]["uid"] = "00000000-0000-0000-0000-000000000000"
    elif change == "pending":
        row["done"] = False
    elif change == "error":
        row["error"] = {"code": 13}
    else:
        row["response"]["@type"] = "type.googleapis.com/google.cloud.run.v2.Service"
    with pytest.raises((resume.ResumeDenied, prior.NativeProofDenied)):
        resume.verify_operations(client, files, probe)


def test_legacy_history_passes_through_without_new_reads(monkeypatch, tmp_path):
    history = tmp_path / "history"
    history.mkdir(mode=0o700)
    write(history / "123456" / "legacy.json", {})
    monkeypatch.setattr(resume, "github", lambda *a, **k: pytest.fail("legacy history must be untouched"))
    target = tmp_path / "normalized"
    assert resume.select_history(history, target, root=ROOT)["history_directory"] == str(history)
    assert not target.exists()


@pytest.mark.parametrize("change", [None, "missing", "bytes", "duplicate", "unproven"])
def test_actual_artifact_id_layout_needs_verified_consumption(monkeypatch, tmp_path, change):
    history = tmp_path / "history"
    history.mkdir(mode=0o700)
    raw = b'{"synthetic":true}'
    monkeypatch.setattr(resume, "FILES", {"marker.json": resume.sha(raw)})
    original = history / "13003047976"
    write(original / "marker.json", raw)
    canonical = history / "13009999999"
    if change != "missing":
        write(canonical / resume.MANIFEST_FILE, {})
    if change == "bytes":
        write(original / "marker.json", b"changed")
    if change == "duplicate":
        write(history / "13008888888" / resume.MANIFEST_FILE, {})
    calls = []
    monkeypatch.setattr(resume, "original_metadata", lambda *a: calls.append("original"))
    monkeypatch.setattr(resume, "github", lambda *a, **k: {})
    def verified(path, *, root):
        assert path.name == "13009999999"
        if change == "unproven":
            raise resume.ResumeDenied("unproven")
        calls.append("verified-completed")
        return {"original_files": resume.FILES, "continuation": {"run": 41109999999}}
    monkeypatch.setattr(resume, "canonical_success", verified)
    target = tmp_path / "normalized"
    if change:
        with pytest.raises(resume.ResumeDenied):
            resume.select_history(history, target, root=ROOT)
        assert not target.exists()
    else:
        result = resume.select_history(history, target, root=ROOT)
        assert result["excluded_original_run"] == resume.ORIGINAL_RUN
        assert calls == ["original", "verified-completed"]
        assert (target / canonical.name / resume.MANIFEST_FILE).is_file()
        assert not (target / original.name).exists()
        assert (original / "marker.json").read_bytes() == raw


@pytest.mark.parametrize("name", ["../escape.json", "/absolute.json", "back\\slash.json"])
def test_archive_paths_never_escape(name):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as z:
        z.writestr(name, b"{}")
    with pytest.raises(resume.ResumeDenied):
        resume.archive(stream.getvalue())


def test_existing_workflow_identity_and_write_path_remain_finite():
    text = (ROOT / ".github/workflows/document-refresh.yml").read_text()
    workflow = yaml.safe_load(text)
    assert workflow["concurrency"] == {"group": "hirewiz-document-definitions-maintenance", "cancel-in-progress": False}
    job = workflow["jobs"]["maintenance"]
    assert job["environment"] == "document-maintenance"
    assert job["env"]["CLOUDSDK_CORE_DISABLE_FILE_LOGGING"] == "true"
    assert job["env"]["CLOUDSDK_CORE_LOG_HTTP"] == "false"
    steps = job["steps"]
    fast = next(i for i, s in enumerate(steps) if s.get("name") == "Verify completed-source continuation before Google auth")
    auth = next(i for i, s in enumerate(steps) if s.get("uses", "").startswith("google-github-actions/auth@"))
    assert fast < auth and "test_document_source_readback_resume.py" in steps[fast]["run"]
    native = next(s for s in steps if s.get("name") == "Build full-source inspector, prove native cases and transition exact pins")
    assert "RESUME_COMPLETED_SOURCE_READBACK != 'true'" in native["if"]
    handler = next(s for s in steps if s.get("name") == "Continue only the exact completed source-native readback")
    assert "SOURCE_REVISION == 'true'" in handler["if"] and "--mode resume" in handler["run"]
    code = (ROOT / "infra/gcp/document_source_readback_resume.py").read_text()
    assert code.count(".promote(identity,") == 1
    assert "run_successor(" not in code and "native_build(" not in code
    assert "empty_event_retry(lambda: probe.verify_successor" in code
    assert "GITHUB_WORKFLOW_REF" in code and "GITHUB_SHA" in code
    regular = (ROOT / ".github/workflows/document-refresh.yml").read_text()
    assert '--dir "$HISTORY_DIRECTORY/$artifact_id"' in regular
    assert "--mode select-history" in regular


@pytest.mark.parametrize("changed", [None, "mode", "source", "resume", "other-recovery", "retire"])
def test_resume_requires_exact_explicit_same_workflow_controls(monkeypatch, changed):
    values = {"RESUME_COMPLETED_SOURCE_READBACK": "true", "SOURCE_REVISION": "true", "REFRESH_MODE": "full-source-refresh",
              "RECOVER_FAILED_NATIVE_JOB": "", "RETIRE_EXPIRED_V2": "false", "RESCUE_EXPIRED_V2": "false",
              "UPGRADE_CURRENT_V2_TO_V3": "false"}
    if changed == "mode":
        values["REFRESH_MODE"] = "refresh"
    elif changed == "source":
        values["SOURCE_REVISION"] = "false"
    elif changed == "resume":
        values["RESUME_COMPLETED_SOURCE_READBACK"] = "false"
    elif changed == "other-recovery":
        values["RECOVER_FAILED_NATIVE_JOB"] = "hirewiz-doc-six-000000000000"
    elif changed == "retire":
        values["RETIRE_EXPIRED_V2"] = "true"
    for key, value in values.items():
        monkeypatch.setenv(key, value)
    if changed:
        with pytest.raises(resume.ResumeDenied, match="resume_controls_required"):
            resume.require_resume_controls()
    else:
        resume.require_resume_controls()


def completed_fixture(monkeypatch, tmp_path, *, change=None):
    """Synthetic provider-shaped artifact; no native acceptance is claimed."""
    from unittest.mock import create_autospec
    recovery = importlib.import_module("infra.gcp.document_refresh_recovery")
    target = tmp_path / "13009999999"
    target.mkdir(mode=0o700)
    raw = b'{"original":true}'
    monkeypatch.setattr(resume, "FILES", {"marker.json": resume.sha(raw)})
    identity, preimage = {"image": resume.IMAGE}, {"actual": "preimage"}
    context = {"run": 41109999999, "attempt": 1, "head": resume.ORIGINAL_SOURCE,
               "path": resume.WORKFLOW, "operator_sha256": "a" * 64}
    claim_body = resume.claim_body(identity, native(), preimage, context)
    ack = {"name": resume.CLAIM_OBJECT, "bucket": "ai-resume-parser-482412_cloudbuild", "generation": "123"}
    result = {"status": "document_refresh_serving", "admission_restored": True}
    manifest = {"version": 1, "kind": "completed_source_readback_v1", "original_run": resume.ORIGINAL_RUN,
        "original_artifact": resume.ORIGINAL_ARTIFACT, "original_zip_sha256": resume.ORIGINAL_ZIP_SHA,
        "original_files": resume.FILES, "candidate_nonce": resume.NONCE,
        "build_identity_sha256": resume.sha(resume.canonical(identity)), "continuation": context,
        "claim_body": claim_body, "claim_acknowledgment": ack, "result_sha256": resume.sha(resume.canonical(result))}
    files = {"marker.json": raw, resume.MANIFEST_FILE: resume.canonical(manifest),
        "result.json": resume.canonical(result), "continuation-preimage.json": json.dumps(preimage, indent=2).encode(),
        "continuation-claim.started.json": resume.canonical({"body_sha256": resume.sha(resume.canonical(claim_body)), "automatic_replay": False}),
        "continuation-claim.acknowledged.json": resume.canonical(ack), "native-proof/acceptance.json": b"{}"}
    if change == "original":
        files["marker.json"] = b"changed"
    if change == "missing-acceptance":
        files.pop("native-proof/acceptance.json")
    for name, value in files.items():
        write(target / name, value)
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        for name, value in files.items():
            archive.writestr(name, value)
    run = {"id": context["run"], "head_sha": context["head"], "run_attempt": 1, "path": resume.WORKFLOW,
        "head_branch": "main", "status": "completed", "conclusion": "failure" if change == "failed-run" else "success",
        "event": "workflow_dispatch", "repository": {"full_name": resume.REPOSITORY}}
    artifact = {"id": int(target.name), "name": f"document-refresh-full-source-refresh-{context['run']}-1",
        "expired": False, "digest": "sha256:" + resume.sha(stream.getvalue()), "workflow_run": {
            "id": context["run"], "head_sha": context["head"], "head_branch": "main"}}
    def get(path, **kwargs):
        if path.endswith("/zip"):
            return stream.getvalue()
        if path.endswith("/artifacts?per_page=100"):
            return {"artifacts": [artifact]}
        return run
    monkeypatch.setattr(resume, "github", get)
    monkeypatch.setattr(resume, "operator_contract", lambda *a, **k: "b" * 64 if change == "operator" else "a" * 64)
    http = Http(wrong_read=change == "claim")
    http.body = resume.canonical(claim_body)
    http.close = lambda: None
    def stock_check(path, saved, journal, build, probe, *, allow_v3=False):
        assert path == target and saved == result and journal == target / "native-proof" and allow_v3 is True
        if not (path / "native-proof/acceptance.json").is_file():
            raise resume.ResumeDenied("stock_canonical_acceptance_missing")
    stock = create_autospec(recovery.completed_serving, side_effect=stock_check)
    build = SimpleNamespace(native_http=lambda: http, infra=lambda _: SimpleNamespace(completed_serving=stock))
    def checked_local(value, root, *, historical=False):
        assert historical is True
        return build, None, identity
    monkeypatch.setattr(resume, "verify_local", checked_local)
    return target, stock


def test_canonical_success_uses_actual_artifact_id_bytes_claim_and_stock_reader(monkeypatch, tmp_path):
    target, stock = completed_fixture(monkeypatch, tmp_path)
    assert resume.canonical_success(target, root=ROOT)["original_artifact"] == resume.ORIGINAL_ARTIFACT
    stock.assert_called_once()


@pytest.mark.parametrize("change", ["original", "failed-run", "operator", "claim", "missing-acceptance"])
def test_canonical_partial_changed_or_unproven_receipt_cannot_consume_original(monkeypatch, tmp_path, change):
    target, _ = completed_fixture(monkeypatch, tmp_path, change=change)
    with pytest.raises(resume.ResumeDenied):
        resume.canonical_success(target, root=ROOT)


def test_operator_identity_binds_actual_working_files(monkeypatch, tmp_path):
    root = tmp_path / "operator"
    root.mkdir(mode=0o700)
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    for path in resume.OPERATOR_PATHS:
        write(root / path, b"original source\n")
    subprocess.run(["git", "add", "."], cwd=root, check=True)
    subprocess.run(["git", "-c", "user.name=Synthetic", "-c", "user.email=synthetic@example.com", "commit", "-qm", "synthetic"], cwd=root, check=True)
    expected = resume.operator_contract(root, "HEAD", working=True)
    path = root / resume.OPERATOR_PATHS[0]
    path.write_bytes(b"changed executed helper\n")
    assert resume.operator_contract(root, "HEAD") == expected
    with pytest.raises(resume.ResumeDenied, match="operator_working_bytes_changed"):
        resume.operator_contract(root, "HEAD", working=True)
