"""Native Build proof-boundary tests over owned source and read-only ports."""
from __future__ import annotations

import copy
import hashlib
import json
from types import SimpleNamespace

import pytest
from infra.gcp import document_cloud_build_qa as qa
from test_document_patched_engine_release import patched_source as patched_source


def receipt(current, root):
    image = qa.REPOSITORY + "@sha256:" + "1" * 64
    identity = current.identity | {"immutable_image": image, "nonce": "a" * 32,
        "build_id": "01234567-0123-0123-0123-012345678901"}
    proof = {"image_id": "sha256:" + "2" * 64}
    value = {"event": qa.EVENT, "version": 1, "nonce": identity["nonce"],
        "source_commit": current.commit, "qualification_sha256": current.qualification_sha256,
        "image_id": proof["image_id"], "helper_sha256": qa.helper_hashes(root),
        "shared_scratch": {"native_daemon_round_trip_verified": True, "restricted_container_verified": True},
        "proof": proof, "proof_sha256": hashlib.sha256(qa.canonical(proof)).hexdigest(),
        "immutable_image": image, "cloud_run_isolation_proven": False, "native_acceptance": False}
    entry = {"resource": {"type": "build", "labels": {"build_id": identity["build_id"],
        "project_id": "ai-resume-parser-482412"}},
        "logName": "projects/ai-resume-parser-482412/logs/cloudbuild", "insertId": "native-entry-1",
        "timestamp": "2026-11-24T12:00:00Z", "jsonPayload": value}
    return identity, entry


@pytest.mark.parametrize("fault", [None, "missing", "duplicate", "wrong_image", "wrong_source", "wrong_helper", "wrong_build", "proof_body", "scratch", "pagination", "failure_log"])
def test_native_log_proof_requires_exact_build_source_image_and_one_entry(patched_source, monkeypatch, fault):
    root, current = patched_source
    identity, entry = receipt(current, root)
    if fault == "wrong_image":
        entry["jsonPayload"]["immutable_image"] = qa.REPOSITORY + "@sha256:" + "3" * 64
    elif fault == "wrong_source":
        entry["jsonPayload"]["source_commit"] = "e" * 40
    elif fault == "wrong_helper":
        entry["jsonPayload"]["helper_sha256"][qa.CHECKER] = "f" * 64
    elif fault == "wrong_build":
        entry["resource"]["labels"]["build_id"] = "different-build"
    elif fault == "proof_body":
        entry["jsonPayload"]["proof"]["image_id"] = "sha256:" + "4" * 64
    elif fault == "scratch":
        entry["jsonPayload"]["shared_scratch"]["native_daemon_round_trip_verified"] = False
    elif fault == "failure_log":
        entry["jsonPayload"] = {"event": qa.EVENT, "error": "native-check-failed"}
    entries = [] if fault == "missing" else [entry, copy.deepcopy(entry)] if fault == "duplicate" else [entry]
    calls = []
    def read(http, method, url, body):
        calls.append((http, method, url, body))
        return {"entries": entries} | ({"nextPageToken": "more"} if fault == "pagination" else {})
    monkeypatch.setattr(qa, "_read_retry_delay", lambda: None)
    if fault:
        with pytest.raises(ValueError, match="document_cloud_build_qa_unverified"):
            qa.read_native(object(), identity, read, root=root)
        assert len(calls) == (3 if fault == "missing" else 1)
    else:
        proof, native = qa.read_native(object(), identity, read, root=root)
        assert proof == entry["jsonPayload"]["proof"]
        assert native["build_id"] == identity["build_id"] and native["insert_id"] == "native-entry-1"
    assert all(call[1:3] == ("POST", "https://logging.googleapis.com/v2/entries:list") for call in calls)
    assert all(identity["build_id"] in call[3]["filter"] and qa.EVENT in call[3]["filter"] for call in calls)


def test_native_logging_delay_only_retries_same_read(patched_source, monkeypatch):
    root, current = patched_source
    identity, entry = receipt(current, root)
    calls, sleeps = [], []
    def read(http, method, url, body):
        calls.append(copy.deepcopy(body))
        return {"entries": [] if len(calls) < 3 else [entry]}
    monkeypatch.setattr(qa, "_read_retry_delay", lambda: sleeps.append(2))
    qa.read_native(None, identity, read, root=root)
    assert sleeps == [2, 2] and calls[0] == calls[1] == calls[2]


def test_same_build_checker_archive_contains_only_explicit_public_helpers(patched_source):
    import io
    import tarfile

    from scripts import document_refresh as refresh

    root, current = patched_source
    raw = refresh._full_source_tar(source_revision=True, source_commit=current.commit, source_patched_engine=True)
    with tarfile.open(fileobj=io.BytesIO(raw)) as archive:
        names = set(archive.getnames())
    assert names == {*current.sources, current.policy_path, *qa.FILES}
    assert not any(".env" in name or "identity-profile" in name or "journal" in name for name in names)
    request = refresh.full_source_build_request("a" * 32, {}, source_revision=True,
        source_commit=current.commit, source_patched_engine=True)
    assert request["timeout"] == "900s" and len(request["steps"]) == 2
    assert request["options"]["enableStructuredLogging"] is True
    assert request["steps"][0]["entrypoint"] == request["steps"][1]["entrypoint"] == "/bin/bash"
    assert request["steps"][0]["args"][:3] == [qa.DRIVER, "build", "build"]
    assert request["steps"][1]["args"][:2] == [qa.DRIVER, "qa"]
    driver = (root / qa.DRIVER).read_text()
    assert driver.index("run_controller check") < driver.index('/usr/bin/docker push "$tag"')
    assert "--network=none" in driver and "CgroupVersion" in driver and "run_controller emit" in driver


def test_shared_scratch_requires_real_restricted_child_roundtrip(tmp_path, monkeypatch):
    scratch = tmp_path / ".document-build-qa/tmp"
    scratch.mkdir(parents=True)
    monkeypatch.setattr(qa.tempfile, "gettempdir", lambda: str(scratch))
    def container(docker, image, platform, args, mounts):
        assert platform == "linux/amd64" and mounts[0][1] == "/tmp/challenge"
        assert mounts[0][0].is_file()
        return 0, hashlib.sha256(mounts[0][0].read_bytes()).hexdigest().encode() + b"\n"
    helper = SimpleNamespace(container=container)
    assert qa.shared_scratch(helper, ["docker"], "sha256:" + "a" * 64, str(tmp_path))["native_daemon_round_trip_verified"]
    helper.container = lambda *_: (0, b"wrong-host-volume\n")
    with pytest.raises(ValueError):
        qa.shared_scratch(helper, ["docker"], "sha256:" + "a" * 64, str(tmp_path))


def test_patched_controller_uses_native_proof_and_never_mac_docker(patched_source, monkeypatch, tmp_path):
    from scripts import document_refresh as refresh
    from scripts import document_test_image as checker

    root, current = patched_source
    image = qa.REPOSITORY + "@sha256:" + "1" * 64
    identity = current.identity | {"immutable_image": image}
    proof = {"image_id": "sha256:" + "2" * 64}
    calls = []
    monkeypatch.setattr(refresh, "native_build", lambda *_a, **_kw: identity)
    monkeypatch.setattr(refresh, "native_http", lambda: SimpleNamespace(close=lambda: calls.append("closed")))
    monkeypatch.setattr(qa, "read_native", lambda *_a, **_kw: (proof, {"same_native_build_log": True}))
    monkeypatch.setattr(checker, "docker_command", lambda: pytest.fail("new profile reached Mac Docker"))
    monkeypatch.setattr(refresh, "proof_contract", lambda actual, *_a, **kw: calls.append((actual, kw)) or {"fresh": True})
    monkeypatch.setattr(refresh, "accepted", lambda: SimpleNamespace(POLICY_SHA256="b" * 64,
        ACCEPTED_RENDERER="fixture", ACCEPTED_FONT_MANIFEST="c" * 64))
    from infra.gcp import schema27_patched_engine_recovery as recovery
    context = object.__new__(recovery.Applications)
    output = tmp_path / "build-output"
    output.mkdir(mode=0o700)
    result = refresh.full_source_candidate(output, publish=True, now=None, nonce="a" * 32,
        source_revision=True, source_patched_engine=True, application_context=context)
    assert result["proof_execution"] == "same_native_cloud_build_daemon"
    assert calls[1][0] == proof and calls[1][1]["source_patched_engine"] is True
    assert (output / "native-build-qa-log.json").is_file()
    assert json.loads((output / "local-native-proof.json").read_bytes()) == proof


def test_malformed_native_checker_proof_stops_before_saved_candidate(patched_source, monkeypatch, tmp_path):
    from infra.gcp import schema27_patched_engine_recovery as recovery

    from scripts import document_refresh as refresh
    from scripts import document_test_image as checker

    _, current = patched_source
    identity = current.identity | {"immutable_image": qa.REPOSITORY + "@sha256:" + "1" * 64}
    monkeypatch.setattr(refresh, "native_build", lambda *_a, **_kw: identity)
    monkeypatch.setattr(refresh, "native_http", lambda: SimpleNamespace(close=lambda: None))
    monkeypatch.setattr(qa, "read_native", lambda *_a, **_kw: ({"image_id": "sha256:" + "2" * 64}, {}))
    monkeypatch.setattr(checker, "docker_command", lambda: pytest.fail("invalid native proof reached Mac Docker"))
    monkeypatch.setattr(refresh, "accepted", lambda: SimpleNamespace(POLICY_SHA256="b" * 64,
        ACCEPTED_RENDERER="fixture", ACCEPTED_FONT_MANIFEST="c" * 64))
    output = tmp_path / "rejected-native-proof"
    output.mkdir(mode=0o700)
    context = object.__new__(recovery.Applications)
    with pytest.raises(refresh.RefreshDenied, match="local_proof_unverified"):
        refresh.full_source_candidate(output, publish=True, now=None, nonce="a" * 32,
            source_revision=True, source_patched_engine=True, application_context=context)
    assert not (output / "local-native-proof.json").exists()
    assert not (output / "build.completed.json").exists()


@pytest.fixture
def finalizable(patched_source, tmp_path, monkeypatch):
    from infra.gcp import document_patched_engine_release as patched
    from infra.gcp import schema27_patched_engine_recovery as recovery
    from infra.gcp.document_refresh_auth import ReleaseReadOnlyHttp

    from scripts import document_refresh as refresh

    root, current = patched_source
    monkeypatch.setattr(recovery.identities, "ROOT", root)
    directory = tmp_path / "owned-build"
    directory.mkdir(mode=0o700)
    closure_journal = tmp_path / "closure-journal"
    closure_journal.mkdir(mode=0o700)
    context = object.__new__(recovery.Applications)
    context.stage_directory, context.identity_profile = directory, SimpleNamespace(digest="e" * 64)
    context.restored = SimpleNamespace(closed=SimpleNamespace(verify_custody=lambda: None,
        plan=SimpleNamespace(digest="f" * 64), journal=SimpleNamespace(directory=closure_journal)))
    nonce = "a" * 32
    archive = refresh.full_source_archive(source_revision=True, source_commit=current.commit, source_patched_engine=True)
    identity = current.identity | {"version": 1, "nonce": nonce, "immutable_image": qa.REPOSITORY + "@sha256:" + "1" * 64,
        "build_id": "01234567-0123-0123-0123-012345678901", "source_archive_sha256": hashlib.sha256(archive).hexdigest(),
        "refresh_dockerfile_sha256": current.sources[current.dockerfile], "source_bucket": refresh.PROJECT + "_cloudbuild",
        "source_object": refresh.FULL_SOURCE_PREFIX + nonce + "/source.tar.gz", "source_generation": "1234"}
    upload = {"bucket": identity["source_bucket"], "object": identity["source_object"], "generation": identity["source_generation"]}
    request = refresh.full_source_build_request(nonce, upload, source_revision=True,
        source_commit=current.commit, source_patched_engine=True)
    values = {"native-build-identity.json": identity,
        "build-owner.json": recovery.build_owner_scope(directory, current, nonce, identity["source_archive_sha256"], context),
        "build-started.json": {"nonce": nonce, "recipe_sha256": identity["refresh_dockerfile_sha256"],
            "source_archive_sha256": identity["source_archive_sha256"], "automatic_replay": False,
            "unknown_outcome": "reconcile_exact_native_object_and_build_only"},
        "source-upload-acknowledged.json": upload,
        "build-create-started.json": {"nonce": nonce, "request_sha256": recovery.source.corpus.sha(request), "automatic_replay": False},
        "build-acknowledged.json": {"nonce": nonce, "build_id": identity["build_id"],
            "operation_name": "operations/retained-original", "automatic_replay": False}}
    for name, value in values.items():
        refresh.write_new(directory / name, value)
    calls = []
    monkeypatch.setattr(refresh, "native_build", lambda *_a, **_k: pytest.fail("finalize reached Build writer"))
    monkeypatch.setattr(refresh, "native_http", lambda: pytest.fail("finalize constructed native writer transport"))
    monkeypatch.setattr(patched, "run_candidate", lambda *_a, **_k: pytest.fail("finalize reached a Job writer"))
    monkeypatch.setattr(recovery, "promote", lambda *_a, **_k: pytest.fail("finalize reached PATCH path"))
    monkeypatch.setattr(refresh, "verify_current_build_release", lambda *args: calls.append(("read-build", args)) or copy.deepcopy(identity))
    monkeypatch.setattr(ReleaseReadOnlyHttp, "__init__", lambda self: calls.append("readonly-http"))
    monkeypatch.setattr(ReleaseReadOnlyHttp, "close", lambda self: None)
    monkeypatch.setattr(qa, "read_native", lambda *_a, **_k: ({"image_id": "sha256:" + "2" * 64}, {"native-log": True}))
    monkeypatch.setattr(refresh, "accepted", lambda: None)
    monkeypatch.setattr(refresh, "proof_contract", lambda *_a, **_k: {"checked-native-proof": True})
    return SimpleNamespace(directory=directory, context=context, identity=identity, calls=calls, root=root, values=values)


@pytest.mark.parametrize("fault", [None, "missing_upload_ack", "missing_build_ack", "wrong_identity", "wrong_source", "wrong_nonce", "copied_root", "helper_drift", "paused"])
def test_finalize_is_native_read_only_and_preserves_exact_original_custody(finalizable, monkeypatch, tmp_path, fault):
    import shutil

    from infra.gcp import document_source_candidate as base
    from infra.gcp import schema27_patched_engine_recovery as recovery

    c = finalizable
    if fault in {"missing_upload_ack", "missing_build_ack"}:
        name = "source-upload-acknowledged.json" if fault == "missing_upload_ack" else "build-acknowledged.json"
        (c.directory / name).unlink()
    elif fault == "wrong_identity":
        from scripts import document_refresh as refresh

        monkeypatch.setattr(refresh, "verify_current_build_release",
            lambda *_: c.identity | {"immutable_image": qa.REPOSITORY + "@sha256:" + "3" * 64})
    elif fault in {"wrong_source", "wrong_nonce"}:
        changed = copy.deepcopy(c.identity)
        changed["source_generation" if fault == "wrong_source" else "nonce"] = "9999" if fault == "wrong_source" else "b" * 32
        (c.directory / "native-build-identity.json").write_bytes(qa.canonical(changed))
    elif fault == "copied_root":
        other = tmp_path / "copied-build"
        shutil.copytree(c.directory, other)
        c.directory, c.context.stage_directory = other, other
    elif fault == "helper_drift":
        path = c.root / qa.DRIVER
        path.write_bytes(path.read_bytes() + b"\n# uncommitted drift\n")
    elif fault == "paused":
        (c.directory / "operator.pause-requested").touch(mode=0o600)
    if fault and fault != "paused":
        with pytest.raises((recovery.rolling.RollingDenied, base.SourceDenied, FileNotFoundError)):
            recovery.finalize_build(c.directory, application_context=c.context)
        assert not (c.directory / "build.completed.json").exists()
    else:
        result = recovery.finalize_build(c.directory, application_context=c.context)
        assert result["completed_stage"] == "build" and result["next_stage_started"] is False
        assert result["next_stage_required"] == "inventory"
        assert (c.directory / "candidate-build.json").is_file()
        assert not (c.directory / "native-proof").exists()
        again = recovery.finalize_build(c.directory, application_context=c.context)
        assert again == result


def test_late_logging_then_finalize_completes_same_build_without_new_native_writes(finalizable, monkeypatch):
    from infra.gcp import schema27_patched_engine_recovery as recovery

    c = finalizable
    reads = []
    def logs(*args, **kwargs):
        reads.append("read-existing-native-log")
        if len(reads) == 1:
            raise ValueError("document_cloud_build_qa_unverified")
        return {"image_id": "sha256:" + "2" * 64}, {"native-log": True}
    monkeypatch.setattr(qa, "read_native", logs)
    retained = {name: (c.directory / name).read_bytes() for name in c.values}
    with pytest.raises(ValueError):
        recovery.finalize_build(c.directory, application_context=c.context)
    assert not (c.directory / "build.completed.json").exists()
    result = recovery.finalize_build(c.directory, application_context=c.context)
    assert result["completed_stage"] == "build" and len(reads) == 2
    assert all((c.directory / name).read_bytes() == raw for name, raw in retained.items())
    assert not (c.directory / "native-proof").exists()
