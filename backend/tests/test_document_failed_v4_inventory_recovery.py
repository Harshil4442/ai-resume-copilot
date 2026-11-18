"""Failed V4 inventory custody through local native and Storage test doubles."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, dataclass
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs, unquote, urlsplit

import pytest
from infra.gcp import document_refresh_probe as probe
from infra.gcp import document_refresh_recovery as recovery

from scripts import document_refresh as refresh


@pytest.fixture
def observed(monkeypatch):
    image = refresh.REPOSITORY + "@" + recovery.FAILED_V4_IMAGE
    job_name = probe.PARENT + "/jobs/" + recovery.FAILED_V4_JOB
    execution_name = job_name + "/executions/" + recovery.FAILED_V4_EXECUTION
    create = {"name": probe.PARENT + "/operations/create"}
    run = {"name": probe.PARENT + "/operations/run"}
    wanted = {"name": job_name, "template": {"template": {"one": "synthetic task"}}}
    identity = {"build_id": recovery.FAILED_V4_BUILD, "source_commit": recovery.FAILED_V4_SOURCE,
                "nonce": recovery.FAILED_V4_NONCE, "immutable_image": image}
    values = dict.fromkeys(recovery.FAILED_V4_FILES, {})
    values.update({"native-build-identity.json": identity,
        "native-proof/binding.json": {"version": 4, "image": image, "build_identity": {
            **identity, "local_native_proof_sha256": recovery.sha(recovery.canonical({}))}},
        "native-proof/inventory.create.started.json": {
            "body_sha256": recovery.sha(recovery.canonical(wanted)), "automatic_replay": False},
        "native-proof/inventory.create.acknowledged.json": create,
        "native-proof/inventory.run.started.json": {"name": job_name, "uid": recovery.FAILED_V4_JOB_UID,
            "etag": "before", "automatic_replay": False},
        "native-proof/inventory.run.acknowledged.json": run})
    files = {key: recovery.canonical(value) for key, value in values.items()}
    monkeypatch.setattr(recovery, "FAILED_V4_MANIFEST", recovery.manifest(files))
    report = {"failure_stage": "identity_preflight", "all_required_passed": False,
        "cases": [{"case": "synthetic", "attempted": False, "passed": False}], "identity_preflight": {
            "passed": False, "cleanup_passed": False, "diagnostic": {"launcher": "returned", "launcher_code": 0,
                "cleanup": "unexpected_exit", "cleanup_code": 1, "failure_phase": "cleanup"}}}
    monkeypatch.setattr(recovery, "FAILED_V4_RECEIPT", recovery.sha(recovery.canonical(report)))

    adapter = SimpleNamespace(build_identity=lambda **_: values["native-proof/binding.json"]["build_identity"],
        handles=lambda _: {"inventory": {"nonce": "b" * 32}, "corpus": {"nonce": recovery.FAILED_V4_NONCE}},
        desired=lambda *_: wanted, validate_job=lambda value, plan: None)
    module = SimpleNamespace(SourceProbe=lambda *_, **__: adapter, corpus=SimpleNamespace(
        native_payload=lambda *_, **__: report), payload=SimpleNamespace(INVENTORY_EVENT="synthetic"))
    monkeypatch.setattr(refresh, "infra", lambda name: module if name == "document_source_release" else probe)
    monkeypatch.setattr(probe, "task_matches", lambda received, expected: received == expected)
    job = {"name": job_name, "uid": recovery.FAILED_V4_JOB_UID, "executionCount": 1,
        "latestCreatedExecution": {"name": recovery.FAILED_V4_EXECUTION, "completionStatus": "EXECUTION_FAILED"}}
    execution = {"name": execution_name, "uid": recovery.FAILED_V4_EXECUTION_UID,
        "completionTime": recovery.FAILED_V4_COMPLETED, "taskCount": 1, "failedCount": 1,
        "template": wanted["template"]["template"], "conditions": [{"type": "Completed", "state": "CONDITION_FAILED",
            "executionReason": "NON_ZERO_EXIT_CODE"}]}
    started = {**run, "done": True, "error": {"code": 10}, "metadata": {
        "@type": "type.googleapis.com/google.cloud.run.v2.Execution", **deepcopy(execution)}}

    class NativeError(RuntimeError):
        def __init__(self, status_code):
            self.status_code = status_code

    class Reader:
        errors = SimpleNamespace(HttpError=NativeError)
        corpus_status = 404

        def job(self, name):
            if name == job_name:
                return deepcopy(job)
            assert name == probe.PARENT + "/jobs/hirewiz-doc-six-" + recovery.FAILED_V4_NONCE[:12]
            if self.corpus_status == 200:
                return {"name": name}
            raise NativeError(self.corpus_status)

        def execution(self, name):
            assert name == execution_name
            return deepcopy(execution)

        def operation(self, name):
            if name == create["name"]:
                return {**create, "done": True, "response": {"@type": "type.googleapis.com/google.cloud.run.v2.Job",
                    "name": job_name, "uid": recovery.FAILED_V4_JOB_UID}}
            assert name == run["name"]
            return deepcopy(started)

    return files, Reader(), job, execution, started, report


def test_exact_failed_v4_inventory_never_becomes_acceptance(observed):
    files, reader, *_ = observed
    result = recovery.verify_failed_v4(files, reader, probe, refresh)
    assert result["artifact"] == recovery.FAILED_V4_ARTIFACT
    assert result["corpus_absent"] and not result["transition_intent_present"]
    assert not any(result[key] for key in ("native_acceptance", "failed_job_replayed", "promotion_performed"))


@pytest.mark.parametrize("changed,expected", [
    ("log", "receipt_changed"), ("job", "run_unverified"), ("execution", "execution_changed"),
    ("operation", "run_unverified"), ("no_permission", "corpus_absence_unverified"),
    ("corpus_exists", "corpus_exists"), ("manifest", "manifest_changed"),
])
def test_failed_v4_rejects_changed_native_or_journal_facts(observed, changed, expected):
    files, reader, job, execution, operation, report = observed
    if changed == "log":
        report["identity_preflight"]["cleanup_passed"] = True
    elif changed == "job":
        job["executionCount"] = 2
    elif changed == "execution":
        execution["retriedCount"] = 1
    elif changed == "operation":
        operation["done"] = False
    elif changed == "no_permission":
        reader.corpus_status = 403
    elif changed == "corpus_exists":
        reader.corpus_status = 200
    else:
        files["native-proof/inventory.run.acknowledged.json"] += b" "
    with pytest.raises(refresh.RefreshDenied, match=expected):
        recovery.verify_failed_v4(files, reader, probe, refresh)


def test_v5_prepare_claims_once_after_native_checks_and_retains_old_failure(tmp_path, monkeypatch, observed):
    files, reader, *_ = observed
    history = tmp_path / "history"
    history.mkdir(mode=0o700)
    recovery.retain(files, history / recovery.FAILED_V4_ARTIFACT)
    checks, stored = [], {}

    class Storage:
        def request(self, uri, *, method, redirections, body=None, headers=None):
            assert redirections == 0
            url = urlsplit(uri)
            query = parse_qs(url.query)
            name = recovery.object_name(recovery.FAILED_V4_JOB_UID)
            if method == "POST":
                assert checks[-2:] == ["joined", "serving"]
                assert query == {"uploadType": ["media"], "name": [name], "ifGenerationMatch": ["0"]}
                assert headers == {"Content-Type": "application/json"} and not stored
                stored[name] = body
                return SimpleNamespace(status=201), recovery.canonical({"name": name,
                    "bucket": refresh.PROJECT + "_cloudbuild", "generation": "1"})
            assert unquote(url.path.split("/o/", 1)[-1]) == name
            status = 200 if name in stored else 404
            return SimpleNamespace(status=status), stored.get(name, b"")

        def close(self):
            pass

    reader.close = lambda: None
    monkeypatch.setattr(probe, "Clients", lambda: reader)
    monkeypatch.setattr(refresh, "native_http", Storage)
    monkeypatch.setattr(recovery, "verify_failed_v4_serving", lambda *_: checks.append("serving"))
    monkeypatch.setattr(recovery, "joined", lambda *_args, **_kwargs: checks.append("joined"))
    options = {"refresh_module": refresh, "application_context": object(),
        "recover_expired_source": True, "source_v5": True, "source_revision": True}
    first = tmp_path / "attempt-first"
    first.mkdir(mode=0o700)
    nonce = recovery.prepare(first, history, None, **options)
    root = first / "failed-v4-inventory"
    body = refresh.decode((root / "disposition.json").read_bytes())
    assert body["successor_nonce"] == nonce != recovery.FAILED_V4_NONCE
    assert body["manifest_sha256"] == recovery.FAILED_V4_MANIFEST
    assert body["target_contract"] == {"kind": "expired-source-v4-to-v5", "renderer_contract_version": 5,
        "required_case_count": 17}
    assert recovery.failed_v4_snapshot(root / "original", refresh) == files
    assert len(stored) == 1 and checks == ["serving", "joined", "serving"]

    second = tmp_path / "attempt-second"
    second.mkdir(mode=0o700)
    with pytest.raises(refresh.RefreshDenied, match="claim_unknown_or_consumed"):
        recovery.prepare(second, history, None, **options)
    assert not (second / "failed-v4-inventory/disposition.json").exists() and len(stored) == 1


def test_v5_prepare_requires_exact_original_and_explicit_expired_mode(tmp_path: Path):
    with pytest.raises(refresh.RefreshDenied, match="mode_refused"):
        recovery.prepare(tmp_path, tmp_path, None, refresh_module=refresh, source_v5=True)
    with pytest.raises(refresh.RefreshDenied, match="artifact_required"):
        recovery.prepare(tmp_path, tmp_path, None, refresh_module=refresh, application_context=object(),
            recover_expired_source=True, source_v5=True)


def test_routine_refresh_defers_unconsumed_v4_and_accepts_one_verified_consumer(tmp_path, monkeypatch, observed):
    files, reader, *_ = observed
    history = tmp_path / "history"
    history.mkdir(mode=0o700)
    recovery.retain(files, history / recovery.FAILED_V4_ARTIFACT)
    output = tmp_path / "result"
    output.mkdir(mode=0o700)
    options = {"refresh_module": refresh, "application_context": object()}
    with pytest.raises(refresh.RefreshDenied, match="completed_consumer_required"):
        recovery.prepare(output, history, None, **options)

    successor = history / "123"
    successor.mkdir(mode=0o700)
    (successor / "failed-v4-inventory").mkdir(mode=0o700)
    with pytest.raises(refresh.RefreshDenied, match="prior_unknown_reconcile_only"):
        recovery.prepare(output, history, None, **options)
    refresh.write_new(successor / "result.json", {"status": "document_refresh_serving", "admission_restored": True})
    reader.close = lambda: None
    monkeypatch.setattr(probe, "Clients", lambda: reader)
    monkeypatch.setattr(refresh, "native_http", lambda: pytest.fail("Routine history may not create a claim"))
    def verified(run, failure, original, *_):
        assert run == successor and original == files and failure["job_uid"] == recovery.FAILED_V4_JOB_UID
        return "verified-current-image"
    monkeypatch.setattr(recovery, "verify_completed_v5_consumer", verified)
    def joined(_client, _probe, _build, jobs, images, **mode):
        assert recovery.FAILED_V4_JOB in jobs
        assert refresh.REPOSITORY + "@" + recovery.FAILED_V4_IMAGE in images
        assert mode["expected_image"] == "verified-current-image"
    monkeypatch.setattr(recovery, "joined", joined)
    assert recovery.prepare(output, history, None, **options) is None and not list(output.iterdir())


@pytest.mark.parametrize("current_kind,consumed", [("same", True), ("child", True), ("unrelated", False)])
def test_completed_v5_claim_joins_only_its_current_image_or_exact_child(
        tmp_path, monkeypatch, observed, current_kind, consumed):
    files, reader, *_ = observed
    failure = recovery.verify_failed_v4(files, reader, probe, refresh)
    original_image = refresh.REPOSITORY + "@" + recovery.EXPIRED_SOURCE_PARENT
    successor_image = refresh.REPOSITORY + "@sha256:" + "1" * 64
    definition_image = refresh.REPOSITORY + "@sha256:" + "2" * 64
    identity = {"nonce": "c" * 32, "kind": "v5-build", "immutable_image": successor_image}
    body = recovery.failed_v4_claim_body(failure, identity["nonce"])
    claim = tmp_path / "failed-v4-inventory"
    (tmp_path / "native-proof").mkdir(mode=0o700)
    (tmp_path / "transition").mkdir(mode=0o700)
    claim.mkdir(mode=0o700)
    recovery.retain(files, claim / "original")

    @dataclass
    class NativeAcceptance:
        image: str

    accepted = NativeAcceptance(successor_image)
    records = {"native-proof/binding.json": {"version": 5, "build_identity": identity},
        "native-proof/acceptance.json": asdict(accepted), "transition/native-acceptance.json": asdict(accepted),
        "transition/complete.json": {"prior_image": original_image}, "result.json": {},
        "failed-v4-inventory/disposition.json": body,
        "failed-v4-inventory/claim.started.json": {"body_sha256": recovery.sha(recovery.canonical(body)),
            "automatic_replay": False}, "failed-v4-inventory/claim.acknowledged.json": {"generation": "1"}}
    for path, value in records.items():
        refresh.write_new(tmp_path / path, value)
    def completed(_run, binding, *_, version):
        assert version == binding["version"] == 5
    monkeypatch.setattr(recovery, "completed_serving_source", completed)
    def verified_claim(received, acknowledgment, _):
        assert received == body and acknowledgment == {"generation": "1"}
    monkeypatch.setattr(recovery, "verify_claim", verified_claim)
    current_image = successor_image if current_kind == "same" else definition_image
    parent = successor_image if current_kind == "child" else original_image
    current = SimpleNamespace(CONTRACT_VERSION=5, build={"kind": "v5-definition", "fixed_renderer_base": parent})
    module = refresh.infra("document_source_release")
    module.v5_build_kind = lambda: "v5-build"
    module.v5_definitions_kind = lambda: "v5-definition"
    module.SourceProbe = lambda *_, **__: SimpleNamespace(_acceptance=lambda *_args, **_kwargs: accepted)
    module.current_probe = lambda *_, **__: current
    reader.service = lambda _: {"template": {"containers": [{"image": current_image}]}}
    if consumed:
        assert recovery.verify_completed_v5_consumer(tmp_path, failure, files, reader, probe, refresh) == current_image
    else:
        with pytest.raises(refresh.RefreshDenied, match="completed_lineage_changed"):
            recovery.verify_completed_v5_consumer(tmp_path, failure, files, reader, probe, refresh)
