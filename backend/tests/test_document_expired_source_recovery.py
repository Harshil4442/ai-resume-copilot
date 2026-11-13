"""Expired custody versus fresh acceptance; synthetic ports, no cloud/model I/O."""
from __future__ import annotations

import base64
import copy
import json
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from urllib.parse import quote, urlencode

import pytest
import yaml
from infra.gcp import document_consumed_native as audit
from infra.gcp import document_refresh_recovery as recovery
from infra.gcp import document_refresh_transition as transition
from infra.gcp import document_source_candidate as profile
from infra.gcp import document_source_release as release
from test_document_source_revision import (
    IMAGE,
    NONCE,
    PARENT,
    accepted,
    build,
    courier,
    native,
    payload,
    source_receipt,
)
from test_document_source_revision import (
    source as original_source,
)

from scripts import document_refresh as builder


@pytest.fixture(scope="module")
def source(tmp_path_factory):
    # These controls do not mutate source files. Reuse one real committed
    # synthetic snapshot instead of creating a repository for every time case.
    context = pytest.MonkeyPatch()
    try:
        yield original_source.__wrapped__(tmp_path_factory.mktemp("expired-source"), context)
    finally:
        context.undo()


@pytest.fixture
def evidence(source, monkeypatch):
    root, current = source
    _, identity = build(source, definitions=True)
    probe = release.SourceProbe(identity, root=root)
    value = source_receipt(current)
    inventory = native.inventory()
    inventory.update(event=payload.INVENTORY_EVENT, policy_sha256=current.policy)
    inventory["identity_preflight"].update(runtime_hashes=current.runtime,
        renderer_version=courier.RENDERER, font_manifest_sha256=courier.FONT)
    def age(days):
        definition = "123/" + (datetime.now(UTC) - timedelta(days=days)).replace(microsecond=0).isoformat()
        for row in value["cases"]:
            if "scan" in row:
                row["scan"]["definitions"] = definition
    age(4)
    job = {"name": release.prior.PARENT + "/jobs/hirewiz-doc-six-" + NONCE[:12], "uid": "owned-job"}
    execution = {"name": "owned-execution", "uid": "owned-execution-uid"}
    monkeypatch.setattr(probe, "_observation", lambda _client, mode, *_args:
        (job, execution, inventory if mode == "inventory" else value))
    metadata = {"uid": probe.SERVICE_UID, "generation": "1", "observedGeneration": "1", "etag": "owned-etag",
        "template": {"containers": [{"image": IMAGE,
            "env": [{"name": "DOCUMENT_WORKER_POLICY_SHA256", "value": current.policy}]}]},
        "labels": {"hirewiz-refresh-build": identity["build_id"], "hirewiz-refresh-nonce": NONCE,
                   "hirewiz-refresh-job": "hirewiz-doc-six-" + NONCE[:12]}}
    monkeypatch.setattr(probe, "build_identity", lambda **_: identity)
    class Clients:
        def __init__(self, **_):
            pass
        def preflight(self):
            return {"service": copy.deepcopy(metadata)}
        def close(self):
            pass
    monkeypatch.setattr(release.prior, "Clients", Clients)
    return probe, metadata, value, age, current


def test_expired_structural_custody_never_becomes_processing_acceptance(evidence, monkeypatch):
    probe, metadata, _, _, _ = evidence
    for fresh in (True, False):
        with pytest.raises(release.prior.NativeProofDenied, match="definitions_stale"):
            probe._acceptance(object(), fresh=fresh)
    monkeypatch.setattr(release.prior, "NativeAcceptance", lambda *a, **k:
        pytest.fail("Expired evidence must never construct NativeAcceptance"))
    identity = probe._verify_current_source(IMAGE, metadata, expired_predecessor=True)
    assert isinstance(identity, release.ExpiredSourcePredecessor)
    assert identity.image == IMAGE and identity.contract_version == 4 and identity.required_case_count == 17
    assert identity.report()["processing_authorized"] is False
    assert identity.report()["fresh_native_acceptance"] is False
    with pytest.raises(transition.TransitionDenied, match="current_acceptance_required"):
        transition.require_current_acceptance(identity, IMAGE, probe)


def test_monitor_evidence_distinguishes_expired_from_fresh_without_authority(evidence):
    probe, metadata, _, age, _ = evidence
    expired = probe._verify_current_source(IMAGE, metadata, monitor=True)
    assert type(expired) is release.ExpiredSourcePredecessor
    assert expired.report()["processing_authorized"] is False
    with pytest.raises(release.prior.NativeProofDenied, match="definitions_stale"):
        probe._verify_current_source(IMAGE, metadata)
    age(2)
    fresh = probe._verify_current_source(IMAGE, metadata, monitor=True)
    assert type(fresh) is release.prior.NativeAcceptance


@pytest.mark.parametrize("days", [0, 2, -1])
def test_recovery_requires_expired_not_fresh_or_future_definitions(evidence, days):
    probe, metadata, _, age, _ = evidence
    age(days)
    with pytest.raises(release.prior.NativeProofDenied, match="expired_predecessor_required|definition_identity_invalid"):
        probe._verify_current_source(IMAGE, metadata, expired_predecessor=True)


@pytest.mark.parametrize("change", ["case", "scan", "runtime", "policy", "cleanup"])
def test_expired_evidence_keeps_every_original_receipt_requirement(evidence, change):
    probe, metadata, value, _, _ = evidence
    if change == "case":
        value["cases"].pop()
    elif change == "scan":
        next(row["scan"] for row in value["cases"] if "scan" in row)["definitions"] = "124/2000-01-01T00:00:00+00:00"
    elif change == "runtime":
        value["identity_preflight"]["runtime_hashes"] = {}
    elif change == "policy":
        value["policy_sha256"] = "0" * 64
    else:
        value["cases"][0]["cleanup_passed"] = False
    with pytest.raises(release.prior.NativeProofDenied):
        probe._verify_current_source(IMAGE, metadata, expired_predecessor=True)


def test_changed_current_worker_or_old_profile_cannot_be_a_recovery_parent(evidence, monkeypatch):
    probe, metadata, _, _, _ = evidence
    wrong = copy.deepcopy(metadata)
    wrong["etag"] = "changed"
    with pytest.raises(release.prior.NativeProofDenied, match="serving_changed"):
        probe._verify_current_source(IMAGE, wrong, expired_predecessor=True)
    monkeypatch.setattr(probe, "POLICY", courier.POLICY_SHA256)
    with pytest.raises(release.prior.NativeProofDenied, match="expired_profile_refused"):
        probe._verify_current_source(IMAGE, metadata, expired_predecessor=True)


def test_two_day_serving_identity_does_not_satisfy_new_candidate_freshness(evidence):
    probe, _, _, age, _ = evidence
    age(2)
    assert isinstance(probe._acceptance(object(), fresh=False), release.prior.NativeAcceptance)
    with pytest.raises(release.prior.NativeProofDenied, match="definitions_stale"):
        probe._acceptance(object(), fresh=True)


def test_expired_audit_records_structural_identity_without_current_acceptance(monkeypatch):
    worker = {"generation": "7", "template": {"containers": [{"image": PARENT}]}}
    clients = SimpleNamespace(preflight=lambda: {"service": copy.deepcopy(worker)},
        service=lambda _: copy.deepcopy(worker))
    probe = SimpleNamespace(verify_current_successor=lambda *_: pytest.fail("Expired predecessor is not current acceptance"),
        policy_for_serving=lambda *_: profile.POLICY_SHA256, verify_caller_pins=lambda *a, **k: None)
    monkeypatch.setattr(release, "verify_expired_current_source_predecessor", lambda *a, **k:
        SimpleNamespace(report=lambda: {"image": PARENT, "processing_authorized": False}))
    boundary = audit.NativeAudit(profile.ROOT, None, clients, object(), probe, recover_expired_source=True)
    result = boundary.verify_current_document(None)
    assert result["processing_authorized"] is False and "current_native_acceptance" not in result
    assert result["expired_structural_predecessor"]["image"] == PARENT


def test_native_child_reaches_expired_parent_verifier_before_any_intent_or_upload(source, monkeypatch, tmp_path):
    root, current = source
    calls = []
    class Reached(Exception):
        pass
    monkeypatch.setattr(release.prior, "Clients", lambda: SimpleNamespace(preflight=lambda:
        {"service": {"template": {"containers": [{"image": PARENT}]}}}, close=lambda: None))
    monkeypatch.setattr(release, "current_probe", lambda *_: SimpleNamespace(
        verify_current_successor=lambda *_: pytest.fail("No fresh claim for expired parent")))
    def expired(*args):
        calls.append(args)
        raise Reached()
    monkeypatch.setattr(release, "verify_expired_current_source_predecessor", expired)
    monkeypatch.setattr(builder, "native_http", lambda: pytest.fail("No upload before structural parent proof"))
    target = tmp_path / "not-created"
    with pytest.raises(Reached):
        builder.native_build(target, profile.definition_recipe(PARENT, root), nonce=NONCE,
            source_definitions=True, source_parent=PARENT, recover_expired_source=True)
    assert len(calls) == 1 and calls[0][0] == PARENT and current.policy == profile.POLICY_SHA256
    assert not target.exists()


@pytest.mark.parametrize("lost_at,drift", [(None, False), (1, False), (4, False), (None, True), (None, "parent")])
def test_expired_transition_requires_new_acceptance_preserves_flags_and_never_replays(
    source, monkeypatch, tmp_path, lost_at, drift,
):
    from tests import test_document_refresh_transition as lifecycle
    root, current = source
    _, identity = build(source, definitions=True)
    probe = release.SourceProbe(identity, root=root)
    fresh = accepted(current, identity, probe)
    monkeypatch.setattr(probe, "verify_successor", lambda *_: fresh)
    monkeypatch.setattr(probe, "verify_current_successor", lambda *_: fresh)
    http = lifecycle.Http(lost_at=lost_at)
    for name, service in http.services.items():
        container = service["template"]["containers"][0]
        if name == transition.WORKER:
            container["image"] = PARENT
        pins = dict(zip(transition.PINS, (PARENT, current.policy, courier.RENDERER, courier.FONT), strict=True))
        for row in container["env"]:
            if row["name"] in pins:
                row["value"] = pins[row["name"]]
        http.revisions[service["latestReadyRevision"]] = copy.deepcopy(service["template"])
    if drift == "parent":
        other = builder.REPOSITORY + "@sha256:" + "7" * 64
        worker = http.services[transition.WORKER]
        worker["template"]["containers"][0]["image"] = other
        http.revisions[worker["latestReadyRevision"]] = copy.deepcopy(worker["template"])
    observations = []
    def structural(image, metadata):
        assert image == PARENT
        assert metadata == http.services[transition.WORKER]
        observations.append(image)
        digest = release.corpus.sha(metadata) if not drift or len(observations) == 1 else "changed"
        return SimpleNamespace(report=lambda: {"image": image, "worker_sha256": digest,
            "processing_authorized": False, "fresh_native_acceptance": False})
    monkeypatch.setattr(probe, "verify_expired_current_source_predecessor", structural)
    context = SimpleNamespace(observe=lambda: {}, phase_traffic=audit.apps.phase_traffic,
        serving=lambda clients, value, image, name, uid=None:
        lifecycle.probe.validate_serving(clients, value, image, name, uid))
    api_env = http.services[transition.CALLERS[0]]["template"]["containers"][0]["env"]
    api_env[:] = [row for row in api_env if row["name"] != "RESUME_DIRECT_UPLOAD_ENABLED"]
    before = copy.deepcopy(http.services)
    path = tmp_path / "recovery-transition"
    def run():
        return transition.transition(IMAGE, identity, tmp_path / "native", path, http=http,
            probe=probe, application_context=context, recover_expired_source=True)
    if drift == "parent":
        with pytest.raises(transition.TransitionDenied, match="expired_source_parent_changed"):
            run()
        assert http.writes == [] and observations == []
        return
    if drift:
        with pytest.raises(transition.TransitionDenied, match="expired_source_predecessor_changed"):
            run()
        assert [name for name, _ in http.writes] == list(transition.CALLERS)
        return
    if lost_at is not None:
        # The existing HTTP fixture loads its own copy of the exception class.
        with pytest.raises((transition.TransitionDenied, lifecycle.transition.TransitionDenied), match="outcome_unknown"):
            run()
        writes = len(http.writes)
        original = {item.name: item.read_bytes() for item in path.iterdir()}
        readback = transition.reconcile(path, http=http)
        assert readback["writes"] == 0 and readback["replay_authorized"] is False
        assert readback["unknown_without_ack"] == 1 and len(readback["operations"]) == lost_at - 1
        assert len(http.writes) == writes
        assert {item.name: item.read_bytes() for item in path.iterdir()} == original
        with pytest.raises(transition.TransitionDenied, match="existing_journal"):
            run()
        assert len(http.writes) == writes == lost_at
        return
    result = run()
    assert result["admission_restored"] and not result["automatic_replay"]
    assert json.loads((path / "expired-source-predecessor.json").read_text())["processing_authorized"] is False
    for name in transition.CALLERS:
        original = transition.environment(before[name]["template"])
        final = transition.environment(http.services[name]["template"])
        assert {key: value for key, value in final.items() if key not in transition.PINS} == {
            key: value for key, value in original.items() if key not in transition.PINS}


@pytest.mark.parametrize("invalid", [dict(full_source=True), dict(source_revision=True),
    dict(retire_expired_v2=True), dict(rescue_expired_v2=True), dict(upgrade_current_v2_to_v3=True),
    dict(recover_failed_native_job="one"), dict(recover_expired_source=1)])
def test_invalid_recovery_modes_refuse_before_any_provider_or_output(monkeypatch, tmp_path, invalid):
    monkeypatch.setattr(builder, "infra", lambda *_: pytest.fail("No provider setup for invalid recovery mode"))
    args = dict(publish=True, now=datetime.now(UTC), recover_expired_source=True, application_context=object())
    args.update(invalid)
    with pytest.raises(builder.RefreshDenied):
        builder.refresh(tmp_path / "not-created", **args)
    assert not (tmp_path / "not-created").exists()


def test_explicit_mode_reaches_exact_child_and_transition_without_daily_full_rebuild(monkeypatch, tmp_path):
    context = object()
    calls = []
    def prepare(*args, **kwargs):
        assert kwargs["recover_expired_source"] is True and kwargs["application_context"] is context
        return NONCE
    def selected(*args, **kwargs):
        assert kwargs["parent"] == PARENT and kwargs["recover_expired_source"] is True
        calls.append("definitions_child")
        return {"build_identity": {"kind": profile.DEFINITIONS_KIND}}
    def promote(*args, **kwargs):
        assert kwargs == {"application_context": context, "recover_expired_source": True}
        calls.append("fresh_native_and_transition")
        return {"status": "document_refresh_serving"}
    modules = {"document_refresh_recovery": SimpleNamespace(prepare=prepare),
        "document_refresh_probe": SimpleNamespace(Clients=lambda: SimpleNamespace(preflight=lambda:
            {"service": {"template": {"containers": [{"image": PARENT}]}}}, close=lambda: None),
            policy_for_serving=lambda *_: profile.POLICY_SHA256),
        "document_source_release": SimpleNamespace(promote=promote)}
    monkeypatch.setattr(builder, "infra", modules.__getitem__)
    monkeypatch.setattr(builder, "source_definitions_candidate", selected)
    monkeypatch.setattr(builder, "native_http", lambda: pytest.fail("Synthetic dispatch only"))
    builder.refresh(tmp_path, publish=True, now=datetime.now(UTC), application_context=context,
        recover_expired_source=True)
    assert calls == ["definitions_child", "fresh_native_and_transition"]


def test_same_candidate_with_completed_native_proof_continues_once_without_new_jobs(
    source, monkeypatch, tmp_path,
):
    root, current = source
    _, identity = build(source, definitions=True)
    probe = release.SourceProbe(identity, root=root)
    fresh = accepted(current, identity, probe)
    native_journal = tmp_path / "native-proof"
    native_journal.mkdir()
    original_ack = b'{"scope":"synthetic-original-ack"}\n'
    (native_journal / "original.acknowledged.json").write_bytes(original_ack)
    calls = []
    monkeypatch.setattr(release.SourceProbe, "verify_successor", lambda *args: fresh)
    monkeypatch.setattr(release.v3, "run_successor", lambda *args, **kwargs:
        pytest.fail("A completed original native candidate must never create/run another Job"))
    monkeypatch.setattr(release.prior, "build_module", lambda: SimpleNamespace(
        write_new=builder.write_new, native_http=lambda: SimpleNamespace(close=lambda: None)))
    def continued(*args, **kwargs):
        calls.append((args, kwargs))
        args[3].mkdir()
        assert kwargs["recover_expired_source"] is True
        return {"status": "document_refresh_serving"}
    monkeypatch.setattr(release.transition, "transition", continued)
    release.promote(identity, native_journal, tmp_path / "transition", root=root,
        application_context=object(), recover_expired_source=True)
    assert len(calls) == 1 and (native_journal / "transition.started.json").is_file()
    assert (native_journal / "original.acknowledged.json").read_bytes() == original_ack
    with pytest.raises(FileExistsError):
        release.promote(identity, native_journal, tmp_path / "different-transition", root=root,
            application_context=object(), recover_expired_source=True)
    assert len(calls) == 1 and not (tmp_path / "different-transition").exists()


@pytest.mark.parametrize("event", ["schedule", "push"])
def test_recovery_cli_is_dispatch_only_before_directory_or_auth(monkeypatch, tmp_path, event):
    target = tmp_path / "not-created"
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setenv("GITHUB_EVENT_NAME", event)
    monkeypatch.setattr(sys, "argv", ["document_refresh.py", "--mode", "refresh", "--publish-candidate",
        "--reviewed-application-context", "--recover-expired-source", "--output-directory", str(target)])
    with pytest.raises(builder.RefreshDenied, match="expired_source_requires_dispatch"):
        builder.main()
    assert not target.exists()


@pytest.mark.parametrize("enabled", [None, False, True])
def test_workflow_recovery_flag_is_manual_only_and_exact_argv(tmp_path, enabled):
    workflow = yaml.safe_load((profile.ROOT / ".github/workflows/document-refresh.yml").read_bytes())
    job = workflow["jobs"]["maintenance"]
    assert job["env"]["RECOVER_EXPIRED_SOURCE"] == (
        "${{ github.event_name == 'workflow_dispatch' && inputs.recover_expired_source || false }}")
    step = next(row for row in job["steps"] if row.get("name") == "Build, prove, transition exact pins and restore admission")
    binary = tmp_path / "backend/.venv/bin/python"
    binary.parent.mkdir(parents=True)
    binary.write_text("#!" + sys.executable + "\nimport json,os,sys\n"
        "from pathlib import Path\nPath(os.environ['CAPTURE_ARGS']).write_text(json.dumps(sys.argv[1:]))\n")
    binary.chmod(0o700)
    capture = tmp_path / "args.json"
    env = {"PATH": "/usr/bin:/bin", "CAPTURE_ARGS": str(capture), "RECOVER_FAILED_NATIVE_JOB": "",
        "HISTORY_DIRECTORY": "private history", "OUTPUT_DIRECTORY": "private output",
        "CONSUMED_BUNDLE": "private consumed", "RETIRE_EXPIRED_V2": "false", "RESCUE_EXPIRED_V2": "false",
        "UPGRADE_CURRENT_V2_TO_V3": "false"}
    if enabled is not None:
        env["RECOVER_EXPIRED_SOURCE"] = str(enabled).lower()
    result = subprocess.run(["/bin/bash", "-eu", "-c", step["run"]], cwd=tmp_path,
        env=env, capture_output=True, timeout=5)
    assert result.returncode == 0
    args = json.loads(capture.read_bytes())
    assert ("--recover-expired-source" in args) is (enabled is True)
    assert args[-3:] == ["--reviewed-application-context", "--consumed-history-bundle", "private consumed"]


def test_recovery_history_mode_rejects_nonboolean_or_other_rescue_before_native(monkeypatch, tmp_path):
    for extra in ({"recover_expired_source": 1}, {"recover_expired_source": True, "retire_expired_v2": True}):
        with pytest.raises(builder.RefreshDenied, match="expired_source_mode_refused"):
            recovery.prepare(tmp_path, None, None, refresh_module=builder, application_context=object(), **extra)


@pytest.mark.parametrize("use_archive,change", [(True, change) for change in (None, "receipt", "fresh", "execution", "pending")]
    + [(False, change) for change in (None, "receipt", "fresh", "execution", "pending", "build", "digest")])
def test_completed_nonserving_source_history_requires_expired_exact_native_receipts(evidence, monkeypatch, use_archive, change):
    probe, _, receipt, age, current = evidence
    identity = probe.build
    files = {"native-proof/binding.json": {"version": 4, "image": IMAGE, "build_identity": identity}}
    local = b"synthetic retained proof"
    identity["local_native_proof_sha256"] = recovery.sha(local)
    jobs, executions, operations = {}, {}, {}
    observations, pins = {}, {}
    for mode, handle in probe.handles(identity).items():
        wanted = probe.desired(IMAGE, handle["nonce"], mode)
        job, execution = native.historical.completed(native.historical.job() | wanted)
        execution["startTime"] = job["createTime"]
        jobs[job["name"]], executions[execution["name"]] = job, execution
        observations[mode] = (job, execution)
        prefix = f"native-proof/{mode}"
        files[prefix + ".create.started.json"] = {"body_sha256": recovery.sha(recovery.canonical(wanted)),
            "automatic_replay": False}
        files[prefix + ".run.started.json"] = {"name": job["name"], "uid": job["uid"],
            "etag": job["etag"], "automatic_replay": False}
        pins[mode] = {"request": recovery.sha(recovery.canonical(wanted)),
            "job_uid": job["uid"], "execution_uid": execution["uid"]}
        for phase, kind, observed in (("create", "Job", job), ("run", "Execution", execution)):
            operation = f"{release.prior.PARENT}/operations/{mode}-{phase}"
            pins[mode][phase] = f"{mode}-{phase}"
            files[prefix + f".{phase}.acknowledged.json"] = {"name": operation}
            operations[operation] = {"name": operation, "done": True, "response": {
                "@type": "type.googleapis.com/google.cloud.run.v2." + kind,
                "name": observed["name"], "uid": observed["uid"]}}
    inventory = native.inventory()
    inventory.update(event=payload.INVENTORY_EVENT, policy_sha256=current.policy)
    inventory["identity_preflight"].update(runtime_hashes=current.runtime,
        renderer_version=courier.RENDERER, font_manifest_sha256=courier.FONT)
    pins["inventory"]["receipt"], pins["corpus"]["receipt"] = probe.sha(inventory), probe.sha(receipt)
    stamp = datetime.fromisoformat(next(row["scan"]["definitions"].split("/", 1)[1]
        for row in receipt["cases"] if "scan" in row))
    monkeypatch.setattr(recovery, "EXPIRED_SOURCE_NATIVE", pins)
    monkeypatch.setattr(recovery, "EXPIRED_SOURCE_DEFINITIONS_ISSUED", stamp)
    monkeypatch.setattr(recovery, "EXPIRED_SOURCE_IMAGE", IMAGE.split("@", 1)[1])
    monkeypatch.setattr(recovery, "EXPIRED_SOURCE_BUILD_IDENTITY", recovery.sha(recovery.canonical(identity)))
    def verify_build(build_id, nonce, image):
        assert (build_id, nonce, image) == (recovery.EXPIRED_SOURCE_BUILD, recovery.EXPIRED_SOURCE_NONCE, IMAGE)
        return {} if change == "build" else identity
    monkeypatch.setattr(builder, "verify_current_build", verify_build)
    monkeypatch.setattr(probe, "_observation", lambda _client, mode, *_args:
        (*observations[mode], inventory if mode == "inventory" else receipt))
    monkeypatch.setattr(release, "SourceProbe", lambda *_args, **kwargs: probe if kwargs["serving_read"] else None)
    monkeypatch.setattr(release.prior, "NativeAcceptance", lambda *a, **k: pytest.fail("Unexpected acceptance"))
    clients = SimpleNamespace(operation=operations.__getitem__, job=jobs.__getitem__, execution=executions.__getitem__)
    if change == "receipt":
        receipt["cases"].pop()
    elif change == "fresh":
        age(1)
    elif change == "execution":
        operations[f"{release.prior.PARENT}/operations/corpus-run"]["response"]["uid"] = "different"
    elif change == "pending":
        operations[f"{release.prior.PARENT}/operations/corpus-run"]["done"] = False
    elif change == "digest":
        pins["corpus"]["receipt"] = "0" * 64
    raw = {name: recovery.canonical(value) for name, value in files.items()} | {"local-native-proof.json": local}
    monkeypatch.setattr(recovery, "EXPIRED_SOURCE_MANIFEST", recovery.manifest(raw))
    supplied = raw if use_archive else None
    if change is None:
        assert recovery.verify_expired_source_history(supplied, clients, release.prior, builder) == (
            IMAGE, {name.rsplit("/", 1)[-1] for name in jobs})
    else:
        with pytest.raises((builder.RefreshDenied, release.prior.NativeProofDenied)):
            recovery.verify_expired_source_history(supplied, clients, release.prior, builder)


@pytest.mark.parametrize("change", [None, "different", "extra", "missing", "predecessor", "serving"])
def test_nonserving_history_uses_archive_if_present_and_native_proof_if_missing(monkeypatch, tmp_path, change):
    history, output = tmp_path / "history", tmp_path / "output"
    history.mkdir(mode=0o700)
    output.mkdir(mode=0o700)
    original = history / recovery.EXPIRED_SOURCE_ARTIFACT
    files = {name: b"{}" for name in recovery.EXPIRED_SOURCE_FILES}
    if change not in {"missing", "predecessor"}:
        recovery.retain(files, original)
    monkeypatch.setattr(recovery, "EXPIRED_SOURCE_MANIFEST", recovery.manifest(files))
    if change == "different":
        (original / "build-started.json").write_bytes(b'{"different":true}')
    elif change == "extra":
        builder.write_new(original / "result.json", {"status": "document_refresh_serving"})
    serving = {"predecessor": PARENT, "serving": IMAGE}.get(change, builder.REPOSITORY + "@" + recovery.EXPIRED_SOURCE_PARENT)
    service = {"template": {"containers": [{"image": serving}]}}
    clients = SimpleNamespace(preflight=lambda: {"service": service}, close=lambda: None, http=None,
        service=lambda _: service)
    observed = []
    fake_probe = SimpleNamespace(Clients=lambda: clients, BASE_IMAGE="other", PARENT=release.prior.PARENT,
        SERVICE_UID=release.prior.SERVICE_UID, policy_for_serving=lambda *_: profile.POLICY_SHA256,
        verify_caller_pins=lambda *_args, **_kwargs: None)
    modules = {"document_refresh_probe": fake_probe, "document_source_release": release,
        "document_refresh_transition": transition}
    monkeypatch.setattr(builder, "infra", modules.__getitem__)
    monkeypatch.setattr(release, "verify_expired_current_source_predecessor", lambda *args: observed.append(args))
    monkeypatch.setattr(transition, "Controller", lambda *_args, **_kwargs: SimpleNamespace(ready=lambda *_a, **_k: None))
    def verify(actual, *_args):
        assert actual == (None if change == "missing" else files)
        observed.append("history")
        return IMAGE, {"inventory-job", "corpus-job"}
    monkeypatch.setattr(recovery, "verify_expired_source_history", verify)
    def prepare():
        return recovery.prepare(output, history, None, refresh_module=builder,
            application_context=SimpleNamespace(serving=None), recover_expired_source=True)
    if change in {None, "predecessor", "missing"}:
        assert prepare() is None
        assert ("history" in observed) is (change in {None, "missing"})
    else:
        with pytest.raises(builder.RefreshDenied, match="expired_source_history_(changed|serving)"):
            prepare()
        assert observed == (["history"] if change == "serving" else [])
    assert not list(output.iterdir())


def test_definition_update_is_writable_only_for_updater_then_restored_readonly():
    recipe = profile.definition_recipe(PARENT).decode()
    updater = recipe.index("chown -R 100:101 /var/lib/clamav")
    writable_dirs = recipe.index("chmod 0755")
    writable_files = recipe.index("chmod 0644")
    refresh = recipe.index("freshclam --stdout --datadir=/var/lib/clamav")
    owner = recipe.index("chown -R 0:0 /var/lib/clamav")
    readonly_dirs = recipe.index("chmod 0555")
    readonly_files = recipe.index("chmod 0444")
    second_stage = recipe.index("FROM ", recipe.index("FROM ") + 1)
    assert updater < writable_dirs < writable_files < refresh < owner < readonly_dirs < readonly_files < second_stage
    assert "freshclam --stdout --datadir=/var/lib/clamav \\\n    && chown -R 0:0" in recipe
    assert recipe.rstrip().endswith("USER 65534:65534")
    assert recipe.count(PARENT) == 2 and "DOCUMENT_DEFINITIONS_NONCE" in recipe
    assert "apt-get" not in recipe and "fc-cache" not in recipe


def test_original_failed_recipe_is_reconstructed_from_original_committed_helper():
    old = "5452f39cd1431404688db174043b0bd0a395e891"
    parent = builder.REPOSITORY + "@sha256:e2880102f6149cd62c800fbd33c775ed371c98a2ca74dce4935374685488fbef"
    recipe = profile.definition_recipe(parent, source_commit=old)
    assert profile.digest(recipe) == "a34ae506d9eb916bf445db226642010d4b92a8ae5b28274813e02e12b6550fe4"
    assert b"chown" not in recipe and recipe != profile.definition_recipe(parent)


def test_unknown_definition_recipe_version_cannot_select_build_bytes(monkeypatch):
    monkeypatch.setattr(profile, "git", lambda *_: b"DEFINITION_RECIPE_VERSION = 3\n")
    with pytest.raises(profile.SourceDenied, match="recipe_version_unreviewed"):
        profile.definition_recipe(PARENT, source_commit="f" * 40)


@pytest.fixture(scope="module")
def failed_build(source):
    root, current = source
    nonce, identity = "9" * 32, "12345678-1234-1234-1234-123456789abc"
    source_ref = {"bucket": builder.PROJECT + "_cloudbuild", "object": "hirewiz-document-refresh-source/" + nonce + "/source.tar.gz",
                  "generation": "1234567"}
    request = builder.source_definitions_build_request(nonce, source_ref, current.commit, PARENT)
    recipe = profile.definition_recipe(PARENT, root, source_commit=current.commit)
    archive = builder.source_archive(recipe)
    actual = copy.deepcopy(request)
    actual.update(id=identity, projectId=builder.PROJECT, status="FAILURE",
        name="projects/157225724590/locations/us-central1/builds/" + identity,
        sourceProvenance={"resolvedStorageSource": source_ref, "fileHashes": {
            "gs://" + source_ref["bucket"] + "/" + source_ref["object"] + "#" + source_ref["generation"]:
            {"fileHash": [{"type": "SHA256", "value": base64.urlsafe_b64encode(bytes.fromhex(profile.digest(archive))).decode()}]}}},
        results={"buildStepImages": [builder.DOCKER_BUILDER.split("@", 1)[1], ""]})
    actual["steps"][0].update(status="FAILURE", exitCode=10)
    actual["steps"][1].update(status="QUEUED")
    values = {"build-started.json": {"nonce": nonce, "recipe_sha256": profile.digest(recipe),
        "source_archive_sha256": profile.digest(archive), "automatic_replay": False,
        "unknown_outcome": "reconcile_exact_native_object_and_build_only"},
        "source-upload-acknowledged.json": source_ref,
        "build-create-started.json": {"nonce": nonce, "automatic_replay": False, "request_sha256": recovery.sha(recovery.canonical(request))},
        "build-acknowledged.json": {"nonce": nonce, "build_id": identity,
            "operation_name": "operations/build/" + builder.PROJECT + "/" + base64.urlsafe_b64encode(identity.encode()).decode().rstrip("="),
            "automatic_replay": False}}
    return values, actual, archive


def failed_reader(values, actual, archive):
    calls = []
    source_ref = values["source-upload-acknowledged.json"]
    build_url = "https://cloudbuild.googleapis.com/v1/projects/" + builder.PROJECT + "/locations/" + builder.REGION + "/builds/" + values.get("build-acknowledged.json", {}).get("build_id", "missing")
    source_url = "https://storage.googleapis.com/download/storage/v1/b/" + source_ref["bucket"] + "/o/" + quote(source_ref["object"], safe="") + "?" + urlencode({"alt": "media", "generation": source_ref["generation"]})
    class Http:
        def request(self, uri, *, method, body=None, headers=None, redirections=0):
            assert method == "GET" and body is None and redirections == 0
            assert uri in {build_url, source_url}
            calls.append(uri)
            raw = recovery.canonical(actual) if uri == build_url else archive
            return SimpleNamespace(status=200), raw
        def close(self):
            pass
    names = ("PROJECT", "REGION", "REPOSITORY", "ROOT", "MAX_OUTPUT", "RefreshDenied", "need", "decode",
             "native_json", "source_definitions_build_request", "read_native_source", "archive_recipe", "infra", "write_new")
    reader = SimpleNamespace(**{name: getattr(builder, name) for name in names}, native_http=Http)
    files = {name: recovery.canonical(value) for name, value in values.items()}
    return reader, files, calls


def test_failed_pre_image_build_preserves_expense_and_all_original_four_files(failed_build):
    values, actual, archive = failed_build
    reader, files, calls = failed_reader(values, actual, archive)
    before = copy.deepcopy(files)
    fact = recovery.verify_pre_native(files, object(), object(), reader)
    assert files == before and len(calls) == 2
    assert fact["failed_pre_image_build"] and fact["terminal_status"] == "FAILURE"
    assert fact["image_produced"] is False and fact["original_flow_native_phase_reached"] is False
    assert fact["expense_state"] == "unreconciled_provider_expense_retained"
    assert "image" not in fact and "native_build_identity.json" not in files


@pytest.mark.parametrize("change", ["working", "success", "source", "builder", "push_started", "image", "args", "body", "archive", "missing"])
def test_failed_build_disposition_refuses_unknown_forged_or_changed_custody(failed_build, change):
    original_values, original_actual, original_archive = failed_build
    values, actual, archive = copy.deepcopy(original_values), copy.deepcopy(original_actual), original_archive
    if change in {"working", "success"}:
        actual["status"] = "WORKING" if change == "working" else "SUCCESS"
    elif change == "source":
        actual["source"]["storageSource"]["generation"] = "8"
    elif change == "builder":
        actual["serviceAccount"] = "projects/other/serviceAccounts/unreviewed"
    elif change == "push_started":
        actual["steps"][1]["status"] = "SUCCESS"
    elif change == "image":
        actual["results"]["images"] = [{"digest": "sha256:" + "1" * 64}]
    elif change == "args":
        actual["steps"][0]["env"] = ["EXTRA=unreviewed"]
    elif change == "body":
        values["build-create-started.json"]["request_sha256"] = "0" * 64
    elif change == "archive":
        archive += b"changed"
    else:
        values.pop("build-acknowledged.json")
    reader, files, _ = failed_reader(values, actual, archive)
    with pytest.raises(builder.RefreshDenied):
        recovery.verify_failed_pre_image_build(files, reader)


@pytest.mark.parametrize("unknown", [False, True])
def test_prepare_retains_four_file_failure_only_after_fresh_terminal_proof(
    failed_build, monkeypatch, tmp_path, unknown,
):
    values, actual, archive = failed_build
    actual = copy.deepcopy(actual)
    if unknown:
        actual["status"] = "WORKING"
    reader, files, calls = failed_reader(values, actual, archive)
    history = tmp_path / "history"
    original = history / "original"
    original.mkdir(parents=True, mode=0o700)
    history.chmod(0o700)
    for name, raw in files.items():
        path = original / name
        path.write_bytes(raw)
        path.chmod(0o600)
    output = tmp_path / "new-owned-operation"
    output.mkdir(mode=0o700)
    original_infra = reader.infra
    reader.infra = lambda name: SimpleNamespace(Clients=lambda: SimpleNamespace(close=lambda: None)) if name == "document_refresh_probe" else original_infra(name)
    joined = []
    monkeypatch.setattr(recovery, "joined", lambda *a, **k: joined.append((a, k)))
    if unknown:
        with pytest.raises(builder.RefreshDenied):
            recovery.prepare(output, history, None, refresh_module=reader, application_context=object(),
                recover_expired_source=True)
        assert not joined and list(output.iterdir()) == []
    else:
        nonce = recovery.prepare(output, history, None, refresh_module=reader, application_context=object(),
            recover_expired_source=True)
        assert nonce != values["build-started.json"]["nonce"] and len(calls) == 2
        saved = output / "pre-native-reconciliations" / values["build-started.json"]["nonce"]
        assert {p.name: p.read_bytes() for p in (saved / "original").iterdir()} == files
        disposition = json.loads((saved / "reconciliation.json").read_bytes())
        assert disposition["status"] == "pre_native_failed_build_reconciled_no_image"
        assert disposition["successor_nonce"] == nonce and disposition["native_acceptance"] is False
        assert disposition["old_build_replayed"] is False and disposition["promotion_performed"] is False
        assert joined[0][1]["recover_expired_source"] is True
    assert {p.name: p.read_bytes() for p in original.iterdir()} == files
