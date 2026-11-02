"""Owned toy files/ports only; no frozen corpus or native approval is produced."""
from __future__ import annotations

import ast
import base64
import copy
import json
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
from evaluation.native_acquisition_v2 import engine as e
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
    executions = [put(f"execution-{i}.json", {
        "name": f"projects/ai-resume-parser-482412/locations/us-central1/jobs/hirewiz-quality-dev-toy-{i:08}/executions/toy-{i}",
        "uid": f"{i:08x}-1234-1234-1234-123456789012", "taskCount": 1, "succeededCount": 1,
        "startTime": start.isoformat(), "completionTime": (now - timedelta(minutes=9)).isoformat(), "template": {
            "serviceAccount": isolation.EVALUATOR, "maxRetries": 0, "containers": [{"image": image,
            "command": ["/app/backend/.venv/bin/python"], "args": ["-B", "-m", "scripts.native_acquisition_job",
            "--namespace", "gs://hirewiz-quality-dev-482412-20261031/quality-development/" + original_auth.experiment_id + "/",
            "--bundle-name", f"input.toy-{i}.json", "--bundle-generation", "1", "--bundle-sha256", "a" * 64,
            "--workspace", "/app/backend/.quality-development"]}]}}, "provider") for i in range(9)]
    evidence_ref = put("review.json", {"state": "completed_native_development_review",
        "reviewed_by": "toy-reviewer-not-human-proof", "reviewed_at": reviewed_at.isoformat(),
        "development_plan_sha256": plan_ref["sha256"], "capture_registry_sha256": registry_ref["sha256"],
        "validation_map_sha256": map_ref["sha256"], "native_execution_readbacks": executions})
    lock = dict(schema_version=e.LOCK_VERSION, purpose="reviewed_native_development_inputs_before_held_out_capture",
        state="locked_before_held_out_native_capture", manifest_sha256=e.inputs.MANIFEST_SHA,
        corpus_sha256=e.inputs.CORPUS_SHA, protocol_sha256=e.inputs.PROTOCOL_SHA, rubric_sha256=manifest["rubric_sha256"],
        reviewed_by="toy-reviewer-not-human-proof", reviewed_at=reviewed_at.isoformat(),
        locked_at=(now - timedelta(minutes=3)).isoformat(), native_directory="native", journal_directory="journal",
        review_directory="reviews", authorization_directory="old-authority", development_authorization=old_auth_ref,
        development_plan=plan_ref, capture_registry=registry_ref, validation_map=map_ref, review_evidence=evidence_ref,
        runner_receipt=runner_ref, ci_metadata=ci_ref, build_metadata=build_ref)

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

    def verify(*args):
        result = original_verify(*args)
        events.append("complete-native40-review-matched")
        return result

    def selected(root, split):
        if split == "development":
            return original_select(root, split)
        assert events == ["complete-native40-review-matched"]
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
