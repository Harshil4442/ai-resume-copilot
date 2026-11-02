"""Owned toy files/ports only; no frozen corpus or native approval is produced."""
from __future__ import annotations

import ast
import base64
import builtins
import copy
import hashlib
import json
import re
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from zipfile import ZipFile

import pytest
from infra.gcp import native_quality_acquisition_isolation as isolation
from pydantic import ValidationError

from app.domains.analysis import native_input_acquisition as old
from app.domains.analysis import quality_packet as packet
from evaluation.native_acquisition_v2 import bridge as b
from evaluation.native_acquisition_v2 import engine as e
from scripts import native_acquisition_channel as original_channel
from scripts import native_acquisition_job as original_job
from scripts import qualify_native_quality_acquisition as source
from scripts import schema25_app_release_contract_v3 as contract


def private_write(root, name, value):
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.write_bytes(value if isinstance(value, bytes) else packet.canonical(value))
    path.chmod(0o600)
    return {"path": name, "sha256": packet.file_digest(path)}


def toy_source(kind, identity):
    tex = (r"\documentclass{article}\begin{document}Toy " + identity + r"\end{document}").encode()
    if kind == "pdf":
        return b"%PDF-1.4\nEXPLICIT-TOY-NOT-NATIVE " + identity.encode()
    if kind == "tex":
        return tex
    buffer = BytesIO()
    with ZipFile(buffer, "w") as archive:
        if kind == "texzip":
            archive.writestr("main.tex", tex)
        else:
            archive.writestr("[Content_Types].xml", "<Types/>")
            archive.writestr("word/document.xml", "<document>TOY " + identity + "</document>")
    return buffer.getvalue()


@pytest.fixture
def reviewed_toys(tmp_path, monkeypatch):
    root = tmp_path / "authority"
    root.mkdir(mode=0o700)
    kinds = {}

    def put(name, value, kind="control"):
        kinds[name] = kind
        return private_write(root, name, value)

    # Explicit artificial cases, not copies/edits of the real frozen packet.
    cases: list[dict[str, Any]] = [{"case_id": f"synth-{i:03}", "split": "development", "source_format": kind,
              "resume": {"raw_text": f"Summary Toy {i}", "sections": [{"heading": "Summary", "text": f"Toy {i}"}]}}
             for i, kind in enumerate([k for k in ("pdf", "docx", "tex", "texzip") for _ in range(10)])]
    manifest = {"rubric_sha256": "a" * 64, "cases": [
        {"case_id": row["case_id"], "split": "development", "sha256": "b" * 64} for row in cases]}
    selections = []

    def selected(_root, split):
        selections.append(split)
        assert split == "development", "A bad native lock exposed held-out input selection"
        return copy.deepcopy(manifest), copy.deepcopy(cases)

    monkeypatch.setattr(e.inputs, "selected_cases", selected)
    now = datetime.now(UTC)
    start = now - timedelta(hours=1)
    original_rows = []
    for case in cases:
        name = case["case_id"] + "." + case["source_format"]
        ref = put("native/" + name, toy_source(case["source_format"], case["case_id"]), "source")
        original_rows.append({"case_id": case["case_id"], "input_sha256": e.inputs.digest(case),
                              "input_artifact": {"path": name, "sha256": ref["sha256"]}})
    native_manifest = put("native/materialization-manifest.json", {
        "packet_manifest_sha256": e.inputs.MANIFEST_SHA, "corpus_sha256": e.inputs.CORPUS_SHA, "cases": original_rows})
    put("old-authority/funding.json", {"synthetic_only": "not funding"})
    auth_data = dict(schema_version=old.VERSION, purpose="unreviewed_native_input_capture_no_model_no_retail",
        state="authorized", authorized_by="toy-owner-not-authority", experiment_id="toy-native-development",
        journal_path=str(tmp_path / "original-journal"), split="development", valid_from=start,
        expires_at=now - timedelta(minutes=10), packet_manifest_sha256=e.inputs.MANIFEST_SHA,
        native_manifest_sha256=native_manifest["sha256"], funding_evidence={"path": "funding.json",
        "sha256": packet.file_digest(root / "old-authority/funding.json")}, request_limit=80,
        reserved_minor_per_request=1, maximum_native_reserved_minor=80, currency="INR",
        inspector={"url": "https://toy-inspector.run.app/inspect", "image":
        "us-central1-docker.pkg.dev/ai-resume-parser-482412/cloud-run-source-deploy/hirewiz-document-inspector@sha256:" + "a" * 64,
        "policy": "b" * 64}, compiler={"url": "https://toy-compiler.run.app/compile", "image":
        "us-central1-docker.pkg.dev/ai-resume-parser-482412/cloud-run-source-deploy/hirewiz-native-tex@sha256:" + "c" * 64,
        "policy": "d" * 64})
    original_auth = old.Authorization.model_validate(auth_data)
    old_auth_ref = put("old-authorization.json", original_auth.model_dump(mode="json"))
    report = old.plan(tmp_path / "toy-packet-never-opened", root / "native", original_auth, root / "old-authority")
    plan_ref = put("old-plan.json", report)
    by_id = {case["case_id"]: case for case in cases}

    def inspection(raw, kind, text):
        return packet.canonical({"version": 1, "sha256": packet.digest(raw), "size_bytes": len(raw),
            "source_format": kind, "worker_image": original_auth.inspector.image,
            "policy_sha256": original_auth.inspector.policy,
            "scan": {"engine": "ClamAV", "version": "toy-only", "definitions": "toy-not-live"},
            "parsed": {"raw_text": text, "sections": {}, "skills": [], "experience_years": 0, "contact_info": {}}})

    for row in report["selected_cases"]:
        identity, kind = row["case_id"], row["source_format"]
        original = (root / "native" / row["source"]["path"]).read_bytes()
        pdf = b"%PDF-1.4\nTOY-COMPILED " + identity.encode()
        if kind in {"tex", "texzip"}:
            put("journal/" + identity + ".compiled.pdf", pdf, "compiled_pdf")
        for phase in row["phases"]:
            key = identity + "." + phase
            data = pdf if phase == "pdf" else original
            payload = old.native.compile_request(data, kind) if phase == "compile" else old.inspection_payload(data, "pdf" if phase == "pdf" else kind)
            intent = {"schema_version": old.VERSION, "local_intent_id": key, "request_sha256": packet.digest(payload),
                "source_sha256": packet.digest(data), "original_source_sha256": packet.digest(original),
                "authorization_sha256": report["authorization_sha256"], "reserved_minor": 1,
                "actual_native_cost_minor": None, "created_at": (start + timedelta(minutes=1)).isoformat(), "replay_authorized": False}
            intent_ref = put("journal/" + key + ".intent.json", intent, "intent")
            if phase == "compile":
                raw = packet.canonical({"protocol": "hirewiz-tex-v1", "pdf": base64.b64encode(pdf).decode(),
                    "sha256": packet.digest(pdf), "size": len(pdf), "renderer": "pdftex-no-shell-escape-v1",
                    "request_sha256": packet.digest(payload), "renderer_image": original_auth.compiler.image,
                    "isolation_policy_sha256": original_auth.compiler.policy})
            else:
                text = "" if phase == "source" and kind in {"tex", "texzip"} else by_id[identity]["resume"]["raw_text"]
                raw = inspection(data, "pdf" if phase == "pdf" else kind, text)
            raw_ref = put("journal/" + key + ".raw.json", raw,
                          "compile_raw" if phase == "compile" else "pdf_raw" if phase == "pdf" else "source_raw")
            put("journal/" + key + ".retained.json", {"intent_sha256": intent_ref["sha256"],
                "response": {"path": key + ".raw.json", "sha256": raw_ref["sha256"]}}, "retained")
            put("journal/" + key + ".result.json", {"state": "captured_unreviewed", "reserved_minor": 1,
                                                    "actual_native_cost_minor": None}, "result")
    reviewed_at = now - timedelta(minutes=4)
    captures = {row["case_id"]: old._capture(root / "journal", row) for row in report["selected_cases"]}
    registry_ref = put("registry.json", {"custody": "operator_reviewed_native_capture_v1",
        "manifest_sha256": e.inputs.MANIFEST_SHA, "reviewed_by": "toy-reviewer-not-human-proof",
        "reviewed_at": reviewed_at.isoformat(), "captures": captures})
    validations = {}
    for row in report["selected_cases"]:
        identity = row["case_id"]
        text = put("reviews/" + identity + ".txt", by_id[identity]["resume"]["raw_text"].encode(), "text")
        png = put("reviews/" + identity + ".png", b"\x89PNG\r\n\x1a\nEXPLICIT-TOY-NOT-RENDER", "page")
        value = {"source_sha256": row["source"]["sha256"], "artifact_sha256": row["source"]["sha256"],
            **dict.fromkeys(("structure_pass", "render_pass", "reading_order_pass", "links_pass", "format_fidelity_pass", "immutable_source_pass"), True),
            "extracted_text": {"path": Path(text["path"]).name, "sha256": text["sha256"]},
            "rendered_pages": [{"path": Path(png["path"]).name, "sha256": png["sha256"]}],
            "visual_review": {"reviewer_id": "toy-reviewer-not-human-proof", "reviewed_at": reviewed_at.isoformat(),
                              "reviewed_page_hashes": [png["sha256"]]}}
        if row["source_format"] in {"tex", "texzip"}:
            value["inspected_artifact_sha256"] = packet.file_digest(root / "journal" / (identity + ".compiled.pdf"))
        ref = put("reviews/" + identity + ".json", value, "validation")
        validations[identity] = {"path": Path(ref["path"]).name, "sha256": ref["sha256"]}
    map_ref = put("validations.json", validations)
    release = "e" * 40
    image = isolation.RUNNER_REPOSITORY + "@sha256:" + "f" * 64
    build_id = "12345678-1234-1234-1234-123456789012"
    jobs: list[dict[str, Any]] = [{"name": name, "status": "completed", "conclusion": "success", "steps": []} for name in
            ("backend", "frontend", "security-and-container", "browser-companion", "cold-browser-journey")]
    jobs[0]["steps"] = [{"name": n, "status": "completed", "conclusion": "success"} for n in
                        (contract.COMPATIBILITY_STEP, contract.PROTOCOL_STEP, contract.RETAINED_STEP)]
    ci = {"headSha": release, "workflowName": "CI", "status": "completed", "conclusion": "success", "jobs": jobs}
    tag = isolation.RUNNER_REPOSITORY + ":" + release
    build = {"id": build_id, "projectId": isolation.PROJECT, "status": "SUCCESS", "sourceProvenance": {
        "resolvedGitSource": {"url": "https://github.com/Harshil4442/ai-resume-copilot.git", "revision": release, "dir": "."}},
        "results": {"images": [{"name": tag, "digest": image.split("@")[1]}]}, "steps": [
            {"name": "gcr.io/cloud-builders/docker", "args": ["build", "--tag", tag, "."]},
            {"name": "gcr.io/cloud-builders/docker", "args": ["push", tag]}]}
    ci_ref, build_ref = put("ci.json", ci), put("build.json", build)
    runner_ref = put("runner.json", {"schema_version": "native-quality-qualified-runner-v1",
        "repository": "https://github.com/Harshil4442/ai-resume-copilot.git", "release": release,
        "ci_run_id": 1, "build_id": build_id, "image": image,
        "qualification_contract": "hirewiz_schema26_app_rolling_v3", "qualification_sha256": "1" * 64,
        "app_files": 289, "app_sha256": source.APP_SHA256, "entry_path": source.ENTRY,
        "entry_sha256": source.ENTRY_SHA256, "runtime_dependencies_sha256": "2" * 64,
        "observed_at": start.isoformat(), "ci_metadata_sha256": source.digest(ci),
        "build_metadata_sha256": source.digest(build)})
    generations = [0]

    def object_ref(name, raw):
        generations[0] += 1
        return original_channel.ObjectRef(name=name, generation=str(generations[0]), size=len(raw), sha256=packet.digest(raw))

    def retained(name, value, kind, path=None):
        raw = value if isinstance(value, bytes) else packet.canonical(value)
        artifact = put(path or "channel/" + name, raw, kind)
        return {"artifact": artifact, "reference": object_ref(name, raw).model_dump(mode="json")}

    namespace = "gs://hirewiz-quality-dev-482412-20261031/quality-development/" + original_auth.experiment_id + "/"
    configuration = original_channel.binding(report, namespace)
    channel_binding = retained("channel-binding.json", configuration.model_dump(mode="json"), "control")
    phase_records = {}
    for row in report["selected_cases"]:
        for phase in row["phases"]:
            key = row["case_id"] + "." + phase
            raw_intent = (root / "journal" / (key + ".intent.json")).read_bytes()
            names = [key + suffix for suffix in (".raw.json", ".retained.json", ".result.json")]
            if phase == "compile":
                names.append(row["case_id"] + ".compiled.pdf")
            phase_records[key] = retained(key + ".terminal.json", {
                "version": original_channel.VERSION, "key": key,
                "intent": object_ref(key + ".intent.json", raw_intent).model_dump(mode="json"),
                "files": [object_ref(name, (root / "journal" / name).read_bytes()).model_dump(mode="json") for name in names]}, "terminal")
    put("old-authority/runner-funding.json", {"synthetic_only": "no funding"})
    original_batches: list[dict[str, Any]] = []
    accumulated: dict[str, Any] = {}
    previous_dispatch, previous_terminal = None, None
    phase_keys = list(phase_records)
    cursor = 0
    for i in range(9):
        count = 1 if i == 0 else 9 if i == 8 else 10
        selected_keys = phase_keys[cursor:cursor + count]
        cursor += count
        review = put(f"old-authority/dispatch-review-{i}.json", {"synthetic_only": "no native approval"}, "dispatch_review")
        review_ref = {"path": Path(review["path"]).name, "sha256": review["sha256"]}
        placeholder = original_channel.ObjectRef(name=f"dispatch.{i}.json", generation="1", size=1, sha256="0" * 64)
        before = packet.digest(packet.canonical(accumulated))
        dispatch = original_channel.Dispatch(schema_version="reviewed-native-acquisition-dispatch-v1",
            purpose="unreviewed_service_capture_external_authority_required", reviewed_by="toy-not-authority",
            external_authority_review=review_ref, channel_binding=channel_binding["reference"], acknowledgment=placeholder,
            before_checkpoint_sha256=before, parent_terminal=previous_terminal, maximum_requests=count)
        ack = retained(placeholder.name, original_channel.dispatch_bytes(dispatch), "batch")
        dispatch = dispatch.model_copy(update={"acknowledgment": original_channel.ObjectRef.model_validate(ack["reference"])})
        authority_names = {"funding.json", "runner-funding.json", review_ref["path"]}
        if previous_dispatch:
            authority_names.add(previous_dispatch.external_authority_review.path)

        def file_inputs(directory, names, prefix):
            return [{"path": name, "object": object_ref(f"input.{prefix}.{j}", (root / directory / name).read_bytes()).model_dump(mode="json")}
                    for j, name in enumerate(sorted(names))]

        job_name = f"hirewiz-quality-dev-toy-{i:08}"
        bundle = original_job.Bundle(schema_version="development-native-job-input-v1",
            purpose="unreviewed_development_capture_no_model_no_retail", entry_sha256=source.ENTRY_SHA256,
            job_name=job_name, configuration=configuration, authorization=original_auth, dispatch=dispatch,
            previous_dispatch=previous_dispatch, runner_funding_evidence={"path": "runner-funding.json",
                "sha256": packet.file_digest(root / "old-authority/runner-funding.json")},
            native_files=file_inputs("native", {"materialization-manifest.json", *(row["source"]["path"] for row in report["selected_cases"])}, f"native-{i}"),
            authorization_files=file_inputs("old-authority", authority_names, f"authority-{i}"))
        bundle_record = retained(f"input.bundle-{i}.json", packet.canonical(bundle.model_dump(mode="json")), "control", f"bundles/batch-{i}.json")
        claim = retained("run." + before + ".intent.json", {"version": original_channel.VERSION,
            "dispatch_sha256": packet.digest(original_channel.dispatch_bytes(dispatch)), "before_checkpoint_sha256": before}, "batch")
        context = retained("job." + packet.digest(original_channel.dispatch_bytes(dispatch)) + ".context.json", {
            "job": job_name, "execution": job_name + "-exec", "task_index": 0, "task_attempt": 0,
            "custody": "environment_observation_not_provider_attestation"}, "batch")
        accumulated.update({key: phase_records[key]["reference"] for key in selected_keys})
        terminal = retained("run." + before + ".terminal.json", {"version": original_channel.VERSION,
            "state": "completed_unreviewed", "channel_binding": channel_binding["reference"],
            "dispatch": ack["reference"], "dispatch_sha256": packet.digest(original_channel.dispatch_bytes(dispatch)),
            "before_checkpoint_sha256": before, "after_checkpoint_sha256": packet.digest(packet.canonical(accumulated)),
            "parent_terminal": previous_terminal.model_dump(mode="json") if previous_terminal else None,
            "requests_sent": count, "actual_native_cost_minor": None, "authorizes_retail": False}, "batch")
        execution = put(f"execution-{i}.json", {
            "name": f"projects/ai-resume-parser-482412/locations/us-central1/jobs/{job_name}/executions/{job_name}-exec",
            "job": f"projects/ai-resume-parser-482412/locations/us-central1/jobs/{job_name}",
            "uid": f"{i:08x}-1234-1234-1234-123456789012", "taskCount": 1, "succeededCount": 1,
            "startTime": start.isoformat(), "completionTime": (now - timedelta(minutes=9)).isoformat(), "template": {
            "serviceAccount": isolation.EVALUATOR, "maxRetries": 0, "containers": [{"image": image,
            "command": ["/app/backend/.venv/bin/python"], "args": ["-B", "-m", "scripts.native_acquisition_job",
            "--namespace", namespace, "--bundle-name", bundle_record["reference"]["name"],
            "--bundle-generation", bundle_record["reference"]["generation"], "--bundle-sha256", bundle_record["reference"]["sha256"],
            "--workspace", "/app/backend/.quality-development"]}]}}, "provider")
        original_batches.append({"batch_index": i, "execution": execution, "bundle": bundle_record,
            "dispatch_ack": ack, "run_intent": claim, "run_terminal": terminal, "job_context": context,
            "phase_terminals": {key: phase_records[key] for key in selected_keys}})
        previous_dispatch = dispatch
        previous_terminal = original_channel.ObjectRef.model_validate(terminal["reference"])
    evidence_ref = put("review.json", {"state": "completed_native_development_review",
        "reviewed_by": "toy-reviewer-not-human-proof", "reviewed_at": reviewed_at.isoformat(),
        "development_plan_sha256": plan_ref["sha256"], "capture_registry_sha256": registry_ref["sha256"],
        "validation_map_sha256": map_ref["sha256"], "native_batches": original_batches})
    lock = dict(schema_version=e.LOCK_VERSION, purpose="reviewed_native_development_inputs_before_held_out_capture",
        state="locked_before_held_out_native_capture", manifest_sha256=e.inputs.MANIFEST_SHA,
        corpus_sha256=e.inputs.CORPUS_SHA, protocol_sha256=e.inputs.PROTOCOL_SHA, rubric_sha256=manifest["rubric_sha256"],
        reviewed_by="toy-reviewer-not-human-proof", reviewed_at=reviewed_at.isoformat(),
        locked_at=(now - timedelta(minutes=3)).isoformat(), native_directory="native", journal_directory="journal",
        review_directory="reviews", authorization_directory="old-authority", channel_directory="channel", development_authorization=old_auth_ref,
        development_plan=plan_ref, capture_registry=registry_ref, validation_map=map_ref, review_evidence=evidence_ref,
        runner_receipt=runner_ref, ci_metadata=ci_ref, build_metadata=build_ref, channel_binding=channel_binding)

    def seal():
        lock["files"] = [{**e.Artifact(path=name, sha256=packet.file_digest(root / name)).model_dump(), "kind": kind}
                         for name, kind in kinds.items()]
        return private_write(root, "native-lock.json", lock)

    lock_ref = seal()
    funding_ref = private_write(root, "heldout-funding.json", {"synthetic_only": "not a funded permit"})
    auth = e.Authorization.model_validate(dict(schema_version=e.VERSION,
        purpose="unreviewed_held_out_native_capture_no_model_no_retail", state="authorized",
        authorized_by="toy-owner-not-authority", experiment_id="toy-held-out-plan", journal_path=str(tmp_path / "new-journal"),
        split="held_out", valid_from=now - timedelta(minutes=2), expires_at=now + timedelta(minutes=20),
        packet_manifest_sha256=e.inputs.MANIFEST_SHA, native_manifest_sha256="0" * 64,
        native_development_lock=lock_ref, funding_evidence=funding_ref, request_limit=320,
        reserved_minor_per_request=5, maximum_native_reserved_minor=1600, currency="INR",
        inspector=original_auth.inspector, compiler=original_auth.compiler))
    selections.clear()
    return SimpleNamespace(root=root, packet=tmp_path / "toy-packet-never-opened", auth=auth, lock=lock,
                           kinds=kinds, seal=seal, put=put, selections=selections, now=now)


def check(toys):
    return e.verify_native_development_lock(toys.packet, toys.auth, toys.root)


def test_complete_toy_protocol_never_claims_native_or_processing_approval(reviewed_toys):
    result = check(reviewed_toys)
    assert len(result.case_ids) == 40 and not result.processing_authorized and not result.native_approval
    assert reviewed_toys.selections and set(reviewed_toys.selections) == {"development"}


def test_native_runtime_validation_does_not_import_unshipped_infra_or_controllers(reviewed_toys, monkeypatch):
    original_import = builtins.__import__

    def shipped(name, globals=None, locals=None, fromlist=(), level=0):
        assert not (name == "infra" or name.startswith("infra.")), "infra is not copied into the production image"
        assert not (name == "scripts" and {"qualify_native_quality_acquisition", "schema25_app_release_contract_v3"} & set(fromlist)), "controller import is not a runtime data read"
        return original_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", shipped)
    assert check(reviewed_toys).native_approval is False


@pytest.mark.parametrize("change", ["bundle_hash", "bundle_generation", "source_hash", "checkpoint", "context"])
def test_self_consistent_review_hashes_cannot_borrow_a_different_native_execution(reviewed_toys, change):
    t = reviewed_toys
    evidence = json.loads((t.root / "review.json").read_bytes())
    batch = evidence["native_batches"][0]
    execution = json.loads((t.root / batch["execution"]["path"]).read_bytes())
    if change in {"bundle_hash", "bundle_generation"}:
        execution["template"]["containers"][0]["args"][10 if change == "bundle_hash" else 8] = "0" * 64 if change == "bundle_hash" else "999"
    elif change == "source_hash":
        record = batch["bundle"]
        value = json.loads((t.root / record["artifact"]["path"]).read_bytes())
        value["native_files"][0]["object"]["sha256"] = "0" * 64
        raw = packet.canonical(value)
        record["artifact"] = t.put(record["artifact"]["path"], raw)
        record["reference"].update(size=len(raw), sha256=packet.digest(raw))
        execution["template"]["containers"][0]["args"][10] = packet.digest(raw)
    else:
        record = batch["run_terminal" if change == "checkpoint" else "job_context"]
        value = json.loads((t.root / record["artifact"]["path"]).read_bytes())
        value["after_checkpoint_sha256" if change == "checkpoint" else "execution"] = "0" * 64 if change == "checkpoint" else "unrelated-execution"
        raw = packet.canonical(value)
        record["artifact"] = t.put(record["artifact"]["path"], raw, "batch")
        record["reference"].update(size=len(raw), sha256=packet.digest(raw))
    batch["execution"] = t.put(batch["execution"]["path"], execution, "provider")
    t.lock["review_evidence"] = t.put("review.json", evidence)
    t.auth = t.auth.model_copy(update={"native_development_lock": e.Artifact.model_validate(t.seal())})
    with pytest.raises(ValueError, match="original_"):
        check(t)
    assert "held_out" not in t.selections


def test_complete_retained_original_closure_stays_within_unchanged_kind_limits(reviewed_toys):
    rows = e.NativeDevelopmentLock.model_validate(reviewed_toys.lock).files
    counts = {kind: sum(row.kind == kind for row in rows) for kind in e.KINDS}
    assert counts["terminal"] == 80 and counts["batch"] == 36 and counts["dispatch_review"] == 9
    assert counts["control"] == 21 <= 32 and counts["provider"] == 9 <= 36


@pytest.fixture
def heldout_toys(reviewed_toys, monkeypatch, tmp_path):
    """160 newly authored tiny toy inputs, never the actual held-out corpus."""
    t = reviewed_toys
    native = tmp_path / "heldout-owned-toys"
    native.mkdir(mode=0o700)
    cases: list[dict[str, Any]] = [{"case_id": f"synth-{i:03}", "split": "held_out", "source_format": kind,
        "resume": {"raw_text": f"Owned toy {i}", "sections": []}}
        for i, kind in enumerate([k for k in ("pdf", "docx", "tex", "texzip") for _ in range(40)], 40)]
    rows = []
    for case in cases:
        name = case["case_id"] + "." + case["source_format"]
        ref = private_write(native, name, toy_source(case["source_format"], case["case_id"]))
        rows.append({"case_id": case["case_id"], "input_sha256": e.inputs.digest(case), "input_artifact": ref})
    value = {"packet_manifest_sha256": e.inputs.MANIFEST_SHA, "corpus_sha256": e.inputs.CORPUS_SHA, "cases": rows}
    ref = private_write(native, "materialization-manifest.json", value)
    t.auth = t.auth.model_copy(update={"native_manifest_sha256": ref["sha256"]})
    original_select, original_verify, events = e.inputs.selected_cases, e.verify_native_development_lock, []

    def verify(*args, **kwargs):
        result = original_verify(*args, **kwargs)
        events.append("complete-native40-review-matched")
        return result

    def selected(root, split):
        if split == "development":
            return original_select(root, split)
        assert events and events[-1] == "complete-native40-review-matched"
        events.append("owned-toy160-selected")
        return {"cases": [{"case_id": c["case_id"], "split": "held_out"} for c in cases]}, copy.deepcopy(cases)

    monkeypatch.setattr(e, "verify_native_development_lock", verify)
    monkeypatch.setattr(e.inputs, "selected_cases", selected)
    return SimpleNamespace(**vars(t), native=native, native_manifest=value, cases=cases, events=events)


def test_all160_owned_toy_inputs_require_native40_lock_before_selection(heldout_toys):
    t = heldout_toys
    report = e.plan(t.packet, t.native, t.auth, t.root)
    assert len(report["selected_cases"]) == 160 and report["maximum_http_requests"] == 320
    assert report["reserved_minor"] == 1600 and sum(len(row["phases"]) for row in report["selected_cases"]) == 320
    assert t.events == ["complete-native40-review-matched", "owned-toy160-selected"]
    assert report["processing_authorized"] is report["native_approval"] is report["authorizes_retail"] is False
    assert report["provider_requests"] == 0 and report["actual_native_cost_minor"] is None


@pytest.mark.parametrize("change", ["extra_file", "duplicate_row", "missing_row", "foreign_case", "wrong_case_hash"])
def test_no_extra_missing_duplicate_or_changed_native_case_is_silently_dropped(heldout_toys, change):
    t = heldout_toys
    if change == "extra_file":
        private_write(t.native, "extra.pdf", b"%PDF-TOY")
    else:
        rows = t.native_manifest["cases"]
        if change == "duplicate_row":
            rows[-1] = copy.deepcopy(rows[0])
        elif change == "missing_row":
            rows.pop()
        elif change == "foreign_case":
            rows[-1]["case_id"] = "synth-999"
        else:
            rows[-1]["input_sha256"] = "0" * 64
        ref = private_write(t.native, "materialization-manifest.json", t.native_manifest)
        t.auth = t.auth.model_copy(update={"native_manifest_sha256": ref["sha256"]})
    with pytest.raises(ValueError):
        e.plan(t.packet, t.native, t.auth, t.root)


def test_combined_authority_native_and_bundle_reserve_is_checked_before_acceptance(heldout_toys, monkeypatch):
    t = heldout_toys
    reviewed = check(t)
    total = reviewed.authority_bytes + e.MAX_BUNDLE_BYTES + sum(p.stat().st_size for p in t.native.iterdir())
    t.events.clear()
    monkeypatch.setattr(e, "MAX_INPUT_BYTES", total - 1)
    with pytest.raises(ValueError, match="source_format_or_bound"):
        e.plan(t.packet, t.native, t.auth, t.root)


@pytest.mark.parametrize("change", ["missing", "tampered", "late", "partial", "unreviewed", "bare_approval", "model_lock"])
def test_invalid_native_lock_blocks_heldout_selection_and_native_source_reads(reviewed_toys, tmp_path, change):
    t = reviewed_toys
    if change == "missing":
        (t.root / "native-lock.json").unlink()
    elif change == "tampered":
        (t.root / "reviews/synth-000.png").write_bytes(b"different")
    elif change == "late":
        t.lock["locked_at"] = t.now.isoformat()
    elif change == "partial":
        t.lock["files"] = t.lock["files"][:-1]
        ref = private_write(t.root, "native-lock.json", t.lock)
        t.auth = t.auth.model_copy(update={"native_development_lock": e.Artifact.model_validate(ref)})
    elif change == "unreviewed":
        t.lock["state"] = "unreviewed_service_capture"
    elif change == "bare_approval":
        t.lock.clear()
        t.lock.update(approved=True, reviewed_by="toy")
    else:
        t.lock.clear()
        t.lock.update(state="locked_before_held_out", operation="interview_questions", development_export={})
    if change in {"late", "unreviewed", "bare_approval", "model_lock"}:
        ref = private_write(t.root, "native-lock.json", t.lock)
        t.auth = t.auth.model_copy(update={"native_development_lock": e.Artifact.model_validate(ref)})
    native = tmp_path / "heldout-must-not-be-read"
    native.mkdir()
    (native / "materialization-manifest.json").write_bytes(b"POISON")
    original_read, native_reads = e.read_artifact, []

    def read(root, *args, **kwargs):
        if root == native:
            native_reads.append(root)
        return original_read(root, *args, **kwargs)

    # Local monkeypatch context does not alter the frozen source module/corpus.
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(e, "read_artifact", read)
        with pytest.raises((ValueError, OSError, KeyError)):
            e.plan(t.packet, native, t.auth, t.root)
    assert "held_out" not in t.selections
    assert not native_reads


@pytest.mark.parametrize("target", ["journal/synth-000.source.raw.json", "journal/synth-000.source.result.json",
    "journal/synth-020.compile.raw.json", "journal/synth-020.compiled.pdf", "reviews/synth-020.json", "execution-0.json"])
def test_changed_referenced_native_artifact_cannot_hide_behind_new_lock_hash(reviewed_toys, target):
    t = reviewed_toys
    value = (t.root / target).read_bytes()
    if target.endswith(".pdf"):
        (t.root / target).write_bytes(value + b"changed")
    else:
        payload = json.loads(value)
        if "state" in payload:
            payload["state"] = "refused_no_replay"
        elif "source_sha256" in payload:
            payload["source_sha256"] = "0" * 64
        elif "succeededCount" in payload:
            payload["succeededCount"] = 0
        else:
            payload["sha256"] = "0" * 64
        (t.root / target).write_bytes(packet.canonical(payload))
    ref = t.seal()
    t.auth = t.auth.model_copy(update={"native_development_lock": e.Artifact.model_validate(ref)})
    with pytest.raises(ValueError):
        check(t)
    assert "held_out" not in t.selections


def test_consistent_hashes_cannot_replace_actual_rendered_page_review(reviewed_toys):
    t = reviewed_toys
    path = t.root / "reviews/synth-000.json"
    value = json.loads(path.read_bytes())
    value["visual_review"]["reviewed_page_hashes"] = []
    ref = t.put("reviews/synth-000.json", value, "validation")
    validations = json.loads((t.root / "validations.json").read_bytes())
    validations["synth-000"] = {"path": "synth-000.json", "sha256": ref["sha256"]}
    t.lock["validation_map"] = t.put("validations.json", validations)
    review = json.loads((t.root / "review.json").read_bytes())
    review["validation_map_sha256"] = t.lock["validation_map"]["sha256"]
    t.lock["review_evidence"] = t.put("review.json", review)
    t.auth = t.auth.model_copy(update={"native_development_lock": e.Artifact.model_validate(t.seal())})
    with pytest.raises(ValueError, match="native_page_review"):
        check(t)
    assert "held_out" not in t.selections


def test_consistent_capture_hashes_cannot_turn_tex_source_into_candidate_facts(reviewed_toys):
    t = reviewed_toys
    name = "synth-020.source"
    raw = json.loads((t.root / ("journal/" + name + ".raw.json")).read_bytes())
    raw["parsed"]["raw_text"] = "invented source extraction"
    ref = t.put("journal/" + name + ".raw.json", raw, "source_raw")
    retained = json.loads((t.root / ("journal/" + name + ".retained.json")).read_bytes())
    retained["response"]["sha256"] = ref["sha256"]
    t.put("journal/" + name + ".retained.json", retained, "retained")
    registry = json.loads((t.root / "registry.json").read_bytes())
    registry["captures"]["synth-020"]["worker_response"]["sha256"] = ref["sha256"]
    t.lock["capture_registry"] = t.put("registry.json", registry)
    review = json.loads((t.root / "review.json").read_bytes())
    review["capture_registry_sha256"] = t.lock["capture_registry"]["sha256"]
    t.lock["review_evidence"] = t.put("review.json", review)
    t.auth = t.auth.model_copy(update={"native_development_lock": e.Artifact.model_validate(t.seal())})
    with pytest.raises(ValueError, match="blank admission"):
        check(t)
    assert "held_out" not in t.selections


@pytest.mark.parametrize("field,value", [("split", "development"), ("schema_version", old.VERSION),
    ("request_limit", 319), ("request_limit", 321), ("request_limit", 320.0), ("maximum_native_reserved_minor", 1599),
    ("reserved_minor_per_request", True), ("reserved_minor_per_request", 0)])
def test_closed_exact_whole_split_permit(reviewed_toys, field, value):
    with pytest.raises(ValueError):
        e.Authorization.model_validate(reviewed_toys.auth.model_dump() | {field: value})


def test_expired_or_draft_permit_stops_before_any_case_selection(reviewed_toys):
    for change in ({"state": "draft"}, {"expires_at": reviewed_toys.now - timedelta(minutes=1)}):
        reviewed_toys.auth = reviewed_toys.auth.model_copy(update=change)
        with pytest.raises(ValueError):
            check(reviewed_toys)
    assert not reviewed_toys.selections


@pytest.mark.parametrize("change", ["link", "hardlink", "public_mode", "duplicate_json", "unused_file"])
def test_private_exact_reference_closure(reviewed_toys, change):
    t = reviewed_toys
    target = t.root / "reviews/synth-000.png"
    if change == "link":
        target.rename(target.with_suffix(".retained"))
        target.symlink_to(target.with_suffix(".retained"))
    elif change == "hardlink":
        (t.root / "extra-link").hardlink_to(target)
    elif change == "public_mode":
        target.chmod(0o644)
    elif change == "duplicate_json":
        raw = (t.root / "native-lock.json").read_bytes().rstrip()
        raw = raw[:-1] + b',"state":"locked_before_held_out_native_capture"}'
        ref = private_write(t.root, "native-lock.json", raw)
        t.auth = t.auth.model_copy(update={"native_development_lock": e.Artifact.model_validate(ref)})
    else:
        t.put("unrelated.json", {"unused": True})
        ref = t.seal()
        t.auth = t.auth.model_copy(update={"native_development_lock": e.Artifact.model_validate(ref)})
    with pytest.raises(ValueError):
        check(t)


def test_initializers_have_only_docstrings_and_old_entry_never_imports_new_namespace():
    backend = Path(e.__file__).resolve().parents[2]
    for path in (backend / "evaluation/__init__.py", Path(e.__file__).with_name("__init__.py")):
        tree = ast.parse(path.read_bytes())
        assert len(tree.body) == 1 and isinstance(tree.body[0], ast.Expr)
        assert isinstance(tree.body[0].value, ast.Constant) and isinstance(tree.body[0].value.value, str)
    entry = (backend / "scripts/native_acquisition_job.py").read_text()
    assert "evaluation.native_acquisition_v2" not in entry
    assert not (backend / "__init__.py").exists()


def test_intent_is_create_only_and_unknown_keeps_entire_reserve(tmp_path):
    root = tmp_path / "journal"
    root.mkdir(mode=0o700)
    key = "synth-101.source"
    value = dict(schema_version=e.VERSION, local_intent_id=key, request_sha256="1" * 64,
        source_sha256="2" * 64, original_source_sha256="2" * 64, authorization_sha256="3" * 64,
        reserved_minor=7, actual_native_cost_minor=None, created_at=datetime.now(UTC), replay_authorized=False)
    ref = e.create_phase_intent(root, key, value)
    before = (root / ref.path).read_bytes()
    state = e.phase_state(root, key, reserved_minor=7)
    assert state == {"state": "unknown_no_replay", "reserved_minor": 7, "actual_native_cost_minor": None,
                     "replay_authorized": False, "reserved_expense_released": False}
    with pytest.raises(ValueError, match="intent_not_new"):
        e.create_phase_intent(root, key, value)
    assert (root / ref.path).read_bytes() == before


@pytest.mark.parametrize("change", [{"replay_authorized": True}, {"reserved_minor": True},
    {"actual_native_cost_minor": 0}, {"request_sha256": "wrong"}, {"created_at": datetime(2026, 1, 1)}])
def test_unbound_or_replay_intent_is_not_written(tmp_path, change):
    root = tmp_path / "journal"
    root.mkdir(mode=0o700)
    value = dict(schema_version=e.VERSION, local_intent_id="synth-101.source", request_sha256="1" * 64,
        source_sha256="2" * 64, original_source_sha256="2" * 64, authorization_sha256="3" * 64,
        reserved_minor=7, actual_native_cost_minor=None, created_at=datetime.now(UTC), replay_authorized=False)
    with pytest.raises(ValidationError):
        e.create_phase_intent(root, "synth-101.source", value | change)
    assert not list(root.iterdir())


def test_parallel_phase_intents_cannot_overrun_one_shared_journal_budget(tmp_path, monkeypatch):
    root = tmp_path / "journal"
    root.mkdir(mode=0o700)
    value = dict(schema_version=e.VERSION, local_intent_id="synth-101.source", request_sha256="1" * 64,
        source_sha256="2" * 64, original_source_sha256="2" * 64, authorization_sha256="3" * 64,
        reserved_minor=7, actual_native_cost_minor=None, created_at=datetime.now(UTC), replay_authorized=False)
    limit = len(packet.canonical(e.PhaseIntent.model_validate(value).model_dump(mode="json")))
    monkeypatch.setattr(e, "MAX_JOURNAL_BYTES", limit)

    def create(key):
        try:
            e.create_phase_intent(root, key, value | {"local_intent_id": key})
            return True
        except ValueError:
            return False

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(create, ("synth-101.source", "synth-102.source")))
    assert outcomes.count(True) == 1
    assert sum(path.stat().st_size for path in root.iterdir()) == limit
    assert len(list(root.glob("*.intent.json"))) == 1


# These are artificial metadata/OCI objects, not captured native evidence.
@pytest.fixture
def bridge_toys():
    import gzip
    import tarfile

    now = datetime(2026, 11, 3, 12, tzinfo=UTC)
    raw: dict[str, bytes] = {}

    def put(name, value):
        content = value if isinstance(value, bytes) else b.canonical(value)
        raw[name] = content
        return b.Artifact(path=name, size=len(content), sha256=hashlib.sha256(content).hexdigest())

    def archive_bytes(recipe):
        stream = BytesIO()
        with gzip.GzipFile(fileobj=stream, mode="wb", mtime=0) as zipped, tarfile.open(fileobj=zipped, mode="w") as archive:
            row = tarfile.TarInfo("Dockerfile")
            row.size, row.mode = len(recipe), 0o644
            archive.addfile(row, BytesIO(recipe))
        return stream.getvalue()

    template_raw = (Path(__file__).resolve().parents[2] / "infra/document-processing/refresh-courier-v3.Dockerfile").read_bytes()
    template = put("refresh-template.Dockerfile", template_raw)

    maps = {kind: {"owned-toy-" + kind: b.digest(kind)} for kind in
            ("source", "parser", "runtime", "fonts", "scanner_binary", "compiler_runtime")}
    execution_config = {"User": "65534:65534", "Entrypoint": ["/usr/local/bin/python3", "-I", "/opt/document/service.py"],
        "WorkingDir": "/opt/document", "Labels": {}}
    material = b.MaterialIdentity(app_sha256=b.digest("owned-toy-app"), app_files=289,
        original_entry_path="backend/scripts/native_acquisition_job.py", original_entry_sha256=b.digest("owned-toy-entry"),
        compiler_image=b.REPOSITORY + "hirewiz-native-tex@sha256:" + "c" * 64,
        compiler_policy_sha256=b.digest("owned-toy-compiler-policy"), inspector_policy_sha256=b.POLICY,
        **{key + "_sha256": b.digest(maps[key]) for key in ("source", "parser", "runtime", "fonts", "scanner_binary", "compiler_runtime")},
        execution_configuration_sha256=b.digest(execution_config), non_definition_tree_sha256=b.digest("owned-toy-tree"),
        renderer_version="Owned toy renderer, not native evidence", scanner_version="1.4.3")

    def materials(name):
        return put(name, {"schema_version": "native-material-inventory-v1", "identity": material.model_dump(mode="json"),
                          "files": maps, "execution_configuration": execution_config})

    def oci(index, layers, diff_ids, labels):
        config = put(f"oci/config-{index}.json", {"architecture": "amd64", "os": "linux",
            "rootfs": {"type": "layers", "diff_ids": diff_ids}, "config": execution_config | {"Labels": labels}})
        manifest = put(f"oci/manifest-{index}.json", {"schemaVersion": 2,
            "config": {"digest": "sha256:" + config.sha256, "size": config.size}, "layers": layers})
        return b.OciEvidence(manifest=manifest, configuration=config), b.INSPECTOR + manifest.sha256

    layers = [{"digest": "sha256:" + b.digest("owned-toy-parent-layer"), "size": 7}]
    diff_ids = ["sha256:" + b.digest("owned-toy-parent-diff")]
    original_oci, original_image = oci(0, layers, diff_ids, {})
    anchor_nonce, anchor_commit, anchor_qualification = "f" * 32, "e" * 40, b.digest("owned-toy-anchor-qualification")
    anchor_parent = b.INSPECTOR + "f" * 64
    anchor_archive = put("original/inspector-source.tar.gz", archive_bytes(b._rendered_recipe(template_raw, anchor_parent)))
    anchor_source = {"bucket": b.PROJECT + "_cloudbuild", "object": "hirewiz-document-refresh-source/" + anchor_nonce + "/source.tar.gz", "generation": "100"}
    anchor_tag = b.REPOSITORY + "hirewiz-document-inspector:source-definitions-" + anchor_nonce
    anchor_args = ["build", "--pull=false", "--platform=linux/amd64", "--build-arg", "DOCUMENT_DEFINITIONS_NONCE=" + anchor_nonce,
        "--label", "hirewiz.document-source.commit=" + anchor_commit, "--label", "hirewiz.document-source.qualification=" + anchor_qualification,
        "--tag", anchor_tag, "."]
    anchor_build = put("original/inspector-build.json", {"id": "ffffffff-ffff-ffff-ffff-ffffffffffff", "status": "SUCCESS", "projectId": b.PROJECT,
        "source": {"storageSource": anchor_source}, "sourceProvenance": {"resolvedStorageSource": anchor_source},
        "serviceAccount": "projects/" + b.PROJECT + "/serviceAccounts/hirewiz-builder-20261014@" + b.PROJECT + ".iam.gserviceaccount.com",
        "tags": [b.KIND, anchor_commit, anchor_qualification, "f" * 64],
        "steps": [{"name": b.DOCKER_BUILDER, "args": anchor_args}, {"name": b.DOCKER_BUILDER, "args": ["push", anchor_tag]}],
        "results": {"images": [{"name": anchor_tag, "digest": "sha256:" + original_image.split("@sha256:")[1]}]}})
    cases = [b.Case(case_id=f"synth-{i:03}", source_format=kind, source_sha256=b.digest(["source", i]), source_size=100 + i,
        compiled_pdf_sha256=b.digest(["pdf", i]) if kind in {"tex", "texzip"} else None,
        compiled_pdf_size=200 + i if kind in {"tex", "texzip"} else None,
        extracted_text_sha256=b.digest(["text", i]), page_sha256=[b.digest(["page", i])])
        for i, kind in enumerate([kind for kind in ("pdf", "docx", "tex", "texzip") for _ in range(10)])]
    pages = put("original/pages.json", {case.case_id: {"source_sha256": case.source_sha256,
        "artifact_sha256": case.compiled_pdf_sha256 or case.source_sha256,
        "extracted_text_sha256": case.extracted_text_sha256, "page_sha256": case.page_sha256} for case in cases})
    original_execution = put("original/renderer-execution.json", {"name": "owned-toy-render-execution", "uid": "owned-toy-not-provider",
        "succeededCount": 1, "template": {"containers": [{"image": original_image}]}})
    origin_inventory = put("original/renderer-inventory.json", {"material_sha256": b.digest(material.model_dump(mode="json")),
        "source_sha256": material.source_sha256, "fonts_sha256": material.fonts_sha256})
    origin = put("original/renderer-origin.json", {"schema_version": "native-renderer-origin-v1", "image": original_image,
        "execution_sha256": original_execution.sha256, "inventory_sha256": origin_inventory.sha256,
        "page_manifest_sha256": pages.sha256, "execution_name": "owned-toy-render-execution",
        "inspector_build_sha256": anchor_build.sha256, "inspector_source_archive_sha256": anchor_archive.sha256})
    ci = put("original/ci.json", {"headSha": "a" * 40, "workflowName": "CI", "conclusion": "success", "status": "completed",
        "jobs": [{"name": name, "status": "completed", "conclusion": "success", "steps": [
            {"name": step, "status": "completed", "conclusion": "success"} for step in
            ("Qualify schema26 app v3 compatibility", "Qualify schema26 app v3 native protocol", "Qualify retained actual-source schema25 app v2")]
            if name == "backend" else []} for name in ("backend", "frontend", "security-and-container", "browser-companion", "cold-browser-journey")]})
    tag = b.REPOSITORY + "hirewiz:" + "a" * 40
    build = put("original/build.json", {"id": "11111111-1111-1111-1111-111111111111", "status": "SUCCESS", "projectId": b.PROJECT,
        "sourceProvenance": {"resolvedGitSource": {"url": "https://github.com/Harshil4442/ai-resume-copilot.git", "revision": "a" * 40, "dir": "."}},
        "steps": [{"name": "gcr.io/cloud-builders/docker", "args": ["build", "--tag", tag, "."]},
                  {"name": "gcr.io/cloud-builders/docker", "args": ["push", tag]}],
        "results": {"images": [{"name": tag, "digest": "sha256:" + "d" * 64, "pushTiming": {}}]}})
    runner = put("original/runner.json", {"schema_version": "native-quality-qualified-runner-v1",
        "qualification_contract": "hirewiz_schema26_app_rolling_v3", "app_sha256": material.app_sha256, "app_files": material.app_files,
        "entry_path": material.original_entry_path, "entry_sha256": material.original_entry_sha256,
        "repository": "https://github.com/Harshil4442/ai-resume-copilot.git", "release": "a" * 40,
        "image": b.REPOSITORY + "hirewiz@sha256:" + "d" * 64, "build_id": "11111111-1111-1111-1111-111111111111",
        "ci_metadata_sha256": ci.sha256, "build_metadata_sha256": build.sha256})
    development_plan = put("original/plan.json", {"schema_version": "development-native-service-acquisition-v1", "split": "development",
        "selected_cases": [{"case_id": case.case_id, "source_format": case.source_format, "source": {"sha256": case.source_sha256}} for case in cases]})
    captures = put("original/captures.json", {"custody": "operator_reviewed_native_capture_v1", "captures": {
        case.case_id: {"source_sha256": case.source_sha256, **({"compilation": {"source_sha256": case.source_sha256,
            "pdf": {"sha256": case.compiled_pdf_sha256}}} if case.compiled_pdf_sha256 else {})} for case in cases}})
    lock_rows = [{"kind": "source", "sha256": case.source_sha256} for case in cases]
    lock_rows += [{"kind": "compiled_pdf", "sha256": case.compiled_pdf_sha256} for case in cases if case.compiled_pdf_sha256]
    lock_rows += [{"kind": "text", "sha256": case.extracted_text_sha256} for case in cases]
    lock_rows += [{"kind": "page", "sha256": sha} for case in cases for sha in case.page_sha256]
    origin_refs = b.RendererOrigin(record=origin, execution=original_execution, inventory=origin_inventory, pages=pages)
    review = put("original/review.json", {"state": "completed_native_development_review",
        "reviewed_at": (now - timedelta(hours=2)).isoformat(), "reviewed_by": "owned-toy-reviewer-not-authority",
        "development_plan_sha256": development_plan.sha256, "capture_registry_sha256": captures.sha256,
        "native_renderer_origin_refs": origin_refs.model_dump(mode="json")})
    lock = put("original/lock.json", {"schema_version": "native-development-review-lock-v2", "state": "locked_before_held_out_native_capture",
        "locked_at": (now - timedelta(hours=2)).isoformat(), "runner_receipt": {"sha256": runner.sha256},
        "reviewed_at": (now - timedelta(hours=2)).isoformat(), "reviewed_by": "owned-toy-reviewer-not-authority",
        "review_evidence": {"sha256": review.sha256},
        "ci_metadata": {"sha256": ci.sha256}, "build_metadata": {"sha256": build.sha256},
        "development_plan": {"sha256": development_plan.sha256}, "capture_registry": {"sha256": captures.sha256}, "files": lock_rows})
    original = b.OriginalEvidence(review_lock=lock, review_evidence=review, development_plan=development_plan, capture_registry=captures,
        runner_receipt=runner, ci_metadata=ci, runner_build=build, inspector_build=anchor_build,
        inspector_source_archive=b.GenerationArtifact(bucket=anchor_source["bucket"], name=anchor_source["object"],
            generation=anchor_source["generation"], artifact=anchor_archive),
        renderer_origin=origin_refs,
        inspector_image=original_image, material=material, material_inventory=materials("original/material.json"), oci=original_oci, cases=cases)

    def assemble(count=1, purpose="already_serving", age=timedelta(hours=1)):
        working = dict(raw)
        edges, parent, parent_layers, parent_diffs = [], original_image, list(layers), list(diff_ids)
        template_parent = re.findall(rb"^FROM ([^ ]+)(?: AS definitions)?$", template_raw, re.M)[0]
        for index in range(count):
            nonce, commit, qualification = f"{index + 1:032x}", "b" * 40, b.digest("owned-toy-qualification")
            recipe = put(f"chain-{index}/Dockerfile", template_raw.replace(template_parent, parent.encode()))
            stream = BytesIO()
            with gzip.GzipFile(fileobj=stream, mode="wb", mtime=0) as zipped, tarfile.open(fileobj=zipped, mode="w") as archive:
                row = tarfile.TarInfo("Dockerfile")
                row.size, row.mode = recipe.size, 0o644
                archive.addfile(row, BytesIO(raw[recipe.path]))
            archive_ref = put(f"chain-{index}/source.tar.gz", stream.getvalue())
            new_layers = [{"digest": "sha256:" + b.digest([index, "layer", n]), "size": 7} for n in range(2)]
            new_diffs = ["sha256:" + b.digest([index, "diff", n]) for n in range(2)]
            child_oci, child_image = oci(index + 1, parent_layers + new_layers, parent_diffs + new_diffs,
                {"hirewiz.document-source.commit": commit, "hirewiz.document-source.qualification": qualification})
            delta = [put(f"chain-{index}/layer-{n}.json", {"schema_version": "oci-layer-path-inventory-v1",
                "layer_digest": new_layers[n]["digest"], "diff_id": new_diffs[n], "parent_image": parent, "image": child_image,
                "non_definition_tree_sha256": material.non_definition_tree_sha256,
                "changed_paths": ["/var/lib/clamav" if n == 0 else "/var/lib/clamav/daily.cld"]}) for n in range(2)]
            source = {"bucket": b.PROJECT + "_cloudbuild", "object": "hirewiz-document-refresh-source/" + nonce + "/source.tar.gz", "generation": str(index + 1)}
            tag = b.REPOSITORY + "hirewiz-document-inspector:source-definitions-" + nonce
            build_id = f"00000000-0000-0000-0000-{index + 1:012x}"
            args = ["build", "--pull=false", "--platform=linux/amd64", "--build-arg", "DOCUMENT_DEFINITIONS_NONCE=" + nonce,
                "--label", "hirewiz.document-source.commit=" + commit, "--label", "hirewiz.document-source.qualification=" + qualification,
                "--tag", tag, "."]
            build_ref = put(f"chain-{index}/build.json", {"id": build_id, "projectId": b.PROJECT, "status": "SUCCESS",
                "source": {"storageSource": source}, "sourceProvenance": {"resolvedStorageSource": source},
                "serviceAccount": "projects/" + b.PROJECT + "/serviceAccounts/hirewiz-builder-20261014@" + b.PROJECT + ".iam.gserviceaccount.com",
                "steps": [{"name": b.DOCKER_BUILDER, "args": args}, {"name": b.DOCKER_BUILDER, "args": ["push", tag]}],
                "tags": [b.KIND, commit, qualification, parent.split("@sha256:")[1]],
                "results": {"images": [{"name": tag, "digest": "sha256:" + child_image.split("@sha256:")[1], "pushTiming": {}}]}})
            edges.append(b.DerivativeEdge(kind=b.KIND, parent_image=parent, image=child_image, build_id=build_id, nonce=nonce,
                source_commit=commit, qualification_sha256=qualification, raw_build=build_ref,
                source_archive=b.GenerationArtifact(bucket=source["bucket"], name=source["object"], generation=source["generation"], artifact=archive_ref),
                recipe=recipe, material_inventory=materials(f"chain-{index}/material.json"), oci=child_oci, layer_inventories=delta))
            parent, parent_layers, parent_diffs = child_image, parent_layers + new_layers, parent_diffs + new_diffs
        issued = now - age
        native_execution = put("current/execution.json", {"name": "owned-toy-current-execution", "uid": "owned-toy-not-provider",
            "succeededCount": 1, "template": {"containers": [{"image": parent}]}})
        inventory = put("current/inventory.json", {"material_sha256": b.digest(material.model_dump(mode="json"))})
        native = put("current/native.json", {"contract_version": 4, "required_case_count": 17, "image": parent,
            "build_id": edges[-1].build_id, "source_archive_sha256": edges[-1].source_archive.artifact.sha256,
            "source_generation": edges[-1].source_archive.generation,
            "policy_sha256": material.inspector_policy_sha256, "execution_sha256": native_execution.sha256,
            "inventory_sha256": inventory.sha256, "definition_issued_at": issued.isoformat(), "execution_name": "owned-toy-current-execution",
            "acceptance_purpose": purpose, "verifier": "SourceProbe.verify_successor" if purpose == "new_candidate_and_promotion" else "SourceProbe.verify_current_successor"})
        service = put("current/service.json", {"uid": "owned-toy-service", "template": {"containers": [{"image": parent}]},
            "terminalCondition": {"state": "CONDITION_SUCCEEDED"}})
        local = put("current/local.json", {"image": parent, "definition_issued_at": issued.isoformat(),
            "material_sha256": b.digest(material.model_dump(mode="json"))}) if purpose == "new_candidate_and_promotion" else None
        current = b.CurrentAcceptance(purpose=purpose, profile="source_v4_17_cases", image=parent, material=material,
            definition_issued_at=issued, native_record=native, execution=native_execution, inventory=inventory, service=service, local_candidate_proof=local)
        # Each invocation returns an independent exact metadata closure.
        names = {name for name in raw if name in working or name.startswith("current/") or any(name.startswith(f"chain-{i}/") for i in range(count))
                 or any(name == f"oci/{part}-{i}.json" for i in range(1, count + 1) for part in ("config", "manifest"))}
        if local is None:
            names.discard("current/local.json")
        artifacts = [b.Artifact(path=name, size=len(raw[name]), sha256=hashlib.sha256(raw[name]).hexdigest()) for name in sorted(names)]
        evidence = b.BridgeEvidence(schema_version=b.SCHEMA, original=original, refresh_template=template,
            chain=edges, current=current, files=artifacts)
        return SimpleNamespace(evidence=evidence, raw={name: raw[name] for name in names}, now=now)

    return assemble


def bridge_replace(t, ref, value):
    content = value if isinstance(value, bytes) else b.canonical(value)
    replacement = b.Artifact(path=ref.path, size=len(content), sha256=hashlib.sha256(content).hexdigest())
    t.raw[ref.path] = content
    data = t.evidence.model_dump(mode="json")

    def replace(node):
        if isinstance(node, dict):
            if node == ref.model_dump(mode="json"):
                return replacement.model_dump(mode="json")
            return {key: replace(value) for key, value in node.items()}
        return [replace(value) for value in node] if isinstance(node, list) else node

    t.evidence = b.BridgeEvidence.model_validate(replace(data))
    return replacement


def bridge_permit(t):
    quote = b.ExpenseQuote(currency="INR", native_per_request_minor=2, native_reserved_minor=120,
        render_reserved_minor=10, runner_reserved_minor=20, provenance_io_reserved_minor=5,
        build_acceptance_reserved_minor=0, build_acceptance_mode="retained_current_proof", total_reserved_minor=155)
    namespace = "gs://owned-toy-private-bucket/quality-native-equivalence/owned-toy-epoch/"
    data = {"schema_version": "native40-bridge-funding-metadata-v1", "purpose": "unreviewed_native40_rescan_no_model_no_heldout",
        "epoch_id": "owned-toy-epoch", "namespace": namespace, "bridge_evidence_sha256": b.digest(t.evidence.model_dump(mode="json")),
        "request_limit": 60, "quote": quote.model_dump(mode="json")}
    content = b.canonical(data)
    funding = b.Artifact(path="funding.json", size=len(content), sha256=hashlib.sha256(content).hexdigest())
    t.raw[funding.path] = content
    return b.BridgePermit(schema_version="native40-definitions-bridge-permit-v1", purpose=data["purpose"], epoch_id=data["epoch_id"],
        namespace=namespace, bridge_evidence_sha256=data["bridge_evidence_sha256"],
        original_review_lock_sha256=t.evidence.original.review_lock.sha256, inspector_image=t.evidence.current.image,
        valid_from=t.now - timedelta(minutes=1), expires_at=t.now + timedelta(minutes=30), request_limit=60,
        quote=quote, funding_evidence=funding)


@pytest.mark.parametrize("count", [1, 3, 32])
def test_bridge_complete_contiguous_chain_is_only_unverified_metadata(bridge_toys, count):
    t = bridge_toys(count)
    before = copy.deepcopy(t.raw)
    result = b.validate_metadata(t.evidence, lambda ref: t.raw[ref.path], now=t.now)
    assert result.chain_edges == count and result.state == "consistent_unverified_metadata"
    assert not result.native_approval and not result.processing_authorized and not result.authorizes_scan and not result.authorizes_retail
    assert result.requires_native_provenance_verification and t.raw == before


def test_bridge_all60_intents_bind_original_review_origin_current_profile_and_separate_permit(bridge_toys):
    t = bridge_toys(3)
    permit = bridge_permit(t)
    plan = b.scan_plan(t.evidence, permit, lambda ref: t.raw[ref.path], now=t.now)
    assert len(plan.intents) == plan.maximum_http_requests == 60 and plan.new_compilations == 0
    assert Counter(intent.phase for intent in plan.intents) == {"bridge_source": 40, "bridge_compiled_pdf": 20}
    assert len({intent.case_id for intent in plan.intents}) == 40
    assert all(intent.original_review_lock == t.evidence.original.review_lock
               and intent.original_renderer_origin == t.evidence.original.renderer_origin.record
               and intent.current_image == t.evidence.current.image and not intent.replay_authorized
               and intent.bridge_evidence_sha256 == plan.evidence_sha256 for intent in plan.intents)
    assert not any((plan.processing_authorized, plan.native_approval, plan.authorizes_scan, plan.authorizes_retail, plan.reserved_expense_released))
    assert plan.total_reserved_minor == 155 and plan.actual_native_cost_minor is None


@pytest.mark.parametrize("purpose,age,accepted", [("new_candidate_and_promotion", timedelta(hours=23, minutes=59), True),
    ("new_candidate_and_promotion", timedelta(hours=24), False), ("already_serving", timedelta(hours=25), True),
    ("already_serving", timedelta(days=3), False), ("already_serving", timedelta(seconds=-1), False)])
def test_bridge_candidate24h_and_actual_serving3days_are_distinct(bridge_toys, purpose, age, accepted):
    t = bridge_toys(purpose=purpose, age=age)
    if accepted:
        assert not b.validate_metadata(t.evidence, lambda ref: t.raw[ref.path], now=t.now).processing_authorized
    else:
        with pytest.raises(ValueError, match="acceptance_age"):
            b.validate_metadata(t.evidence, lambda ref: t.raw[ref.path], now=t.now)


@pytest.mark.parametrize("change", ["missing_edge", "cycle", "duplicate_build", "wrong_generation", "outside_database",
    "recipe_changed", "changed_material", "missing_origin", "bare_origin", "compiled_pdf", "raw_bytes", "extra_artifact", "bool_execution"])
def test_bridge_refuses_incomplete_changed_or_self_approved_metadata(bridge_toys, change):
    t = bridge_toys(3)
    if change == "missing_edge":
        t.evidence = t.evidence.model_copy(update={"chain": [t.evidence.chain[0], t.evidence.chain[2]]})
    elif change == "cycle":
        edge = t.evidence.chain[1].model_copy(update={"image": t.evidence.original.inspector_image})
        t.evidence = t.evidence.model_copy(update={"chain": [t.evidence.chain[0], edge, t.evidence.chain[2]]})
    elif change == "duplicate_build":
        edge = t.evidence.chain[1].model_copy(update={"build_id": t.evidence.chain[0].build_id})
        t.evidence = t.evidence.model_copy(update={"chain": [t.evidence.chain[0], edge, t.evidence.chain[2]]})
    elif change == "wrong_generation":
        edge = t.evidence.chain[0].model_copy(update={"source_archive": t.evidence.chain[0].source_archive.model_copy(update={"generation": "999"})})
        t.evidence = t.evidence.model_copy(update={"chain": [edge, *t.evidence.chain[1:]]})
    elif change == "outside_database":
        ref = t.evidence.chain[0].layer_inventories[0]
        bridge_replace(t, ref, json.loads(t.raw[ref.path]) | {"changed_paths": ["/opt/document/processor.py"]})
    elif change == "recipe_changed":
        bridge_replace(t, t.evidence.chain[0].recipe, b"FROM unrelated\nRUN arbitrary\n")
    elif change == "changed_material":
        ref = t.evidence.chain[0].material_inventory
        value = json.loads(t.raw[ref.path])
        value["identity"]["fonts_sha256"] = "0" * 64
        bridge_replace(t, ref, value)
    elif change == "missing_origin":
        t.raw.pop(t.evidence.original.renderer_origin.record.path)
    elif change == "bare_origin":
        bridge_replace(t, t.evidence.original.renderer_origin.record, {"passed": True, "approved": True})
    elif change == "compiled_pdf":
        case = t.evidence.original.cases[-1].model_copy(update={"compiled_pdf_sha256": "0" * 64})
        original = t.evidence.original.model_copy(update={"cases": [*t.evidence.original.cases[:-1], case]})
        t.evidence = t.evidence.model_copy(update={"original": original})
    elif change == "raw_bytes":
        t.raw[t.evidence.chain[0].raw_build.path] += b" "
    elif change == "bool_execution":
        ref = t.evidence.current.execution
        updated = bridge_replace(t, ref, json.loads(t.raw[ref.path]) | {"succeededCount": True})
        native = t.evidence.current.native_record
        bridge_replace(t, native, json.loads(t.raw[native.path]) | {"execution_sha256": updated.sha256})
    else:
        ref = b.Artifact(path="unrelated.json", size=2, sha256=hashlib.sha256(b"{}").hexdigest())
        t.raw[ref.path] = b"{}"
        t.evidence = t.evidence.model_copy(update={"files": [*t.evidence.files, ref]})
    with pytest.raises((ValueError, KeyError)):
        b.validate_metadata(t.evidence, lambda ref: t.raw[ref.path], now=t.now)


@pytest.mark.parametrize("change", ["old320_permit", "old_heldout_namespace", "underfunded60", "unquoted_render", "new_build_free", "expired", "late_review"])
def test_bridge_never_reuses320_permit_or_omits_full_costs(bridge_toys, change):
    t = bridge_toys()
    permit = bridge_permit(t)
    data = permit.model_dump(mode="json")
    if change == "old320_permit":
        data["request_limit"] = 320
    elif change == "old_heldout_namespace":
        data["namespace"] = data["namespace"].replace("quality-native-equivalence", "quality-held-out")
    elif change == "underfunded60":
        data["quote"]["native_reserved_minor"] = 119
    elif change == "unquoted_render":
        data["quote"]["render_reserved_minor"] = 0
    elif change == "new_build_free":
        data["quote"]["build_acceptance_mode"] = "new_work_reserved"
    elif change == "late_review":
        data["valid_from"] = (t.now - timedelta(hours=3)).isoformat()
    else:
        data["expires_at"] = t.now.isoformat()
    with pytest.raises(ValueError):
        b.scan_plan(t.evidence, b.BridgePermit.model_validate(data), lambda ref: t.raw[ref.path], now=t.now)


def test_bridge_depth33_missing_renderer_and_authority_flags_are_not_accepted(bridge_toys):
    t = bridge_toys()
    with pytest.raises(ValueError):
        b.BridgeEvidence.model_validate(t.evidence.model_dump() | {"chain": t.evidence.chain * 33})
    data = t.evidence.original.model_dump()
    data.pop("renderer_origin")
    with pytest.raises(ValueError):
        b.OriginalEvidence.model_validate(data)
    with pytest.raises(ValueError):
        b.MetadataReport.model_validate({"evidence_sha256": "a" * 64, "original_review_lock_sha256": "b" * 64,
            "current_image": t.evidence.current.image, "material_sha256": "c" * 64, "chain_edges": 1, "native_approval": True})


@pytest.mark.parametrize("field", ["app_sha256", "app_files", "original_entry_sha256", "compiler_image",
    "compiler_policy_sha256", "source_sha256", "parser_sha256", "runtime_sha256", "fonts_sha256",
    "scanner_binary_sha256", "compiler_runtime_sha256", "execution_configuration_sha256",
    "non_definition_tree_sha256", "renderer_version", "scanner_version", "inspector_policy_sha256"])
def test_bridge_changed_current_app_entry_compiler_or_material_stops_before_reader(bridge_toys, field):
    t = bridge_toys()
    data = t.evidence.model_dump()
    value = data["current"]["material"][field]
    data["current"]["material"][field] = value + 1 if type(value) is int else ("0" * 64 if field.endswith("sha256") else value + "changed")
    reads = []

    def no_read(ref):
        reads.append(ref.path)
        pytest.fail("Changed approved material reached a metadata reader")

    with pytest.raises(ValueError):
        b.validate_metadata(b.BridgeEvidence.model_validate(data), no_read, now=t.now)
    assert not reads


def test_bridge_aggregate_metadata_limit_and_duplicate_original_ids_are_upfront(bridge_toys):
    t = bridge_toys()
    extras = [b.Artifact(path=f"extra/large-{i}.json", size=b.MAX_METADATA, sha256="a" * 64) for i in range(129)]
    with pytest.raises(ValueError, match="metadata_closure_bound"):
        b.BridgeEvidence.model_validate(t.evidence.model_dump() | {"files": [*t.evidence.files, *extras]})
    original = t.evidence.original.model_dump()
    original["cases"][-1] = original["cases"][0]
    with pytest.raises(ValueError, match="original40"):
        b.OriginalEvidence.model_validate(original)


def test_bridge_serving_verifier_metadata_cannot_be_relabeled_as_new_candidate(bridge_toys):
    t = bridge_toys(age=timedelta(hours=1))
    current = t.evidence.current.model_copy(update={"purpose": "new_candidate_and_promotion"})
    t.evidence = t.evidence.model_copy(update={"current": current})
    with pytest.raises(ValueError, match="current_native_references"):
        b.validate_metadata(t.evidence, lambda ref: t.raw[ref.path], now=t.now)


@pytest.mark.parametrize("change", ["anchor_generation", "anchor_archive", "build_override", "missing_material_group",
    "unsealed_origin", "current_build_generation"])
def test_bridge_anchor_source_generation_sealed_origin_and_current_build_closure(bridge_toys, change):
    t = bridge_toys()
    if change == "anchor_generation":
        source = t.evidence.original.inspector_source_archive.model_copy(update={"generation": "999"})
        t.evidence = t.evidence.model_copy(update={"original": t.evidence.original.model_copy(update={"inspector_source_archive": source})})
    elif change == "anchor_archive":
        ref = t.evidence.original.inspector_source_archive.artifact
        bridge_replace(t, ref, t.raw[ref.path] + b"unbound extra bytes")
    elif change == "build_override":
        ref = t.evidence.chain[0].raw_build
        value = json.loads(t.raw[ref.path])
        value["steps"][0]["entrypoint"] = "sh"
        bridge_replace(t, ref, value)
    elif change == "missing_material_group":
        ref = t.evidence.chain[0].material_inventory
        value = json.loads(t.raw[ref.path])
        value["files"].pop("runtime")
        bridge_replace(t, ref, value)
    elif change == "unsealed_origin":
        ref = t.evidence.original.review_evidence
        value = json.loads(t.raw[ref.path])
        value.pop("native_renderer_origin_refs")
        updated = bridge_replace(t, ref, value)
        lock = t.evidence.original.review_lock
        value = json.loads(t.raw[lock.path])
        value["review_evidence"]["sha256"] = updated.sha256
        bridge_replace(t, lock, value)
    else:
        ref = t.evidence.current.native_record
        bridge_replace(t, ref, json.loads(t.raw[ref.path]) | {"source_generation": "999"})
    with pytest.raises(ValueError):
        b.validate_metadata(t.evidence, lambda ref: t.raw[ref.path], now=t.now)


@pytest.mark.parametrize("change", ["symlink", "duplicate", "expansion"])
def test_bridge_source_archives_cannot_escape_alias_or_exceed_decompression_bound(change):
    import gzip
    import tarfile

    if change == "expansion":
        raw = gzip.compress(b"A" * 65537)
    else:
        stream = BytesIO()
        with gzip.GzipFile(fileobj=stream, mode="wb", mtime=0) as zipped, tarfile.open(fileobj=zipped, mode="w") as archive:
            for _ in range(2 if change == "duplicate" else 1):
                row = tarfile.TarInfo("Dockerfile")
                if change == "symlink":
                    row.type, row.linkname = tarfile.SYMTYPE, "../escape"
                    archive.addfile(row)
                else:
                    row.size = 1
                    archive.addfile(row, BytesIO(b"x"))
        raw = stream.getvalue()
    with pytest.raises(ValueError):
        b._archive_entries(raw, 65536)
