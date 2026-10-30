"""Real owned Git/parse checks and synthetic control-plane tests, not native proof."""
from __future__ import annotations

import copy
import io
import json
import subprocess
import sys
from dataclasses import asdict, replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
import test_document_candidate_probe as native
from infra.gcp import document_candidate as courier
from infra.gcp import document_candidate_release as v3
from infra.gcp import document_refresh_recovery as recovery
from infra.gcp import document_source_candidate as profile
from infra.gcp import document_source_payload as payload
from infra.gcp import document_source_receipt as receipt
from infra.gcp import document_source_release as release

from scripts import document_refresh as builder
from scripts import document_test_image as engines

IMAGE = native.IMAGE
PARENT = builder.REPOSITORY + "@sha256:" + "e" * 64
NONCE = native.NONCE


@pytest.fixture
def source(tmp_path, monkeypatch):
    root = tmp_path / "owned-source"
    root.mkdir(mode=0o700)
    contents, _, _ = profile.working_snapshot()
    names = (*profile.CONTRACT_PATHS, courier.POLICY_PATH, courier.REFRESH_DOCKERFILE,
             "infra/gcp/document_candidate_payload.py")
    contents.update({name: (profile.ROOT / name).read_bytes() for name in names})
    for folder, pattern in (("infra/gcp", "*.py"), ("infra/document-processing", "*.Dockerfile")):
        contents.update({str(path.relative_to(profile.ROOT)): path.read_bytes()
            for path in (profile.ROOT / folder).glob(pattern)})
    for name, content in contents.items():
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
    for arguments in (("init", "-q"), ("config", "user.name", "Synthetic source test"),
                      ("config", "user.email", "source-test@example.invalid"), ("add", "."),
                      ("commit", "-qm", "Owned synthetic source snapshot")):
        subprocess.run(["git", *arguments], cwd=root, capture_output=True, check=True, timeout=15)
    value = profile.committed(root=root, clean=True)
    monkeypatch.setattr(builder, "ROOT", root)
    return root, value


def build(source, *, definitions=False):
    root, current = source
    storage = {"bucket": builder.PROJECT + "_cloudbuild", "object": builder.FULL_SOURCE_PREFIX + NONCE + "/source.tar.gz",
               "generation": "1234"}
    request = (builder.source_definitions_build_request(NONCE, storage, current.commit, PARENT) if definitions else
               builder.full_source_build_request(NONCE, storage, source_revision=True, source_commit=current.commit))
    value = request | {"id": native.historical.BUILD_ID, "projectId": builder.PROJECT,
        "name": release.prior.PARENT + "/builds/" + native.historical.BUILD_ID, "status": "SUCCESS",
        "sourceProvenance": {"resolvedStorageSource": copy.deepcopy(storage)},
        "results": {"images": [{"name": request["images"][0], "digest": IMAGE.split("@", 1)[1]}]}}
    recipe = (profile.definition_recipe(PARENT, root) if definitions else (root / current.dockerfile).read_bytes())
    identity = builder.build_contract(value, NONCE, profile.digest(recipe), full_source=not definitions,
        source_revision=not definitions, source_definitions=definitions, source_parent=PARENT if definitions else None,
        source_commit=current.commit)
    archive = (builder.source_archive(recipe) if definitions else builder.full_source_archive(source_revision=True,
        source_commit=current.commit))
    identity.update(source_archive_sha256=profile.digest(archive), source_sha256=current.sources,
                    local_native_proof_sha256="f" * 64)
    return value, identity


def test_only_reviewed_source_hashes_change_without_weakening_policy():
    _, previous = profile.frozen_v3()
    contents, hashes, policy = profile.working_snapshot()
    current = json.loads(contents[profile.POLICY_PATH])
    assert {key: value for key, value in current.items() if key != "source_sha256"} == {
        key: value for key, value in previous.items() if key != "source_sha256"}
    assert {name for name in hashes if hashes[name] != previous["source_sha256"].get(name)} == {
        "infra/document-processing/processor.py", "backend/app/services/parsing.py", profile.DOCKERFILE}
    assert policy != courier.POLICY_SHA256 and len(hashes) == 22


def test_dirty_native_or_unversioned_helper_bytes_never_authorize_new_work(source):
    root, current = source
    target = root / "infra/gcp/document_source_payload.py"
    target.write_bytes(target.read_bytes() + b"\n# unversioned helper change\n")
    with pytest.raises(profile.SourceDenied, match="committed_clean_source"):
        profile.committed(root=root, clean=True)
    with pytest.raises(profile.SourceDenied, match="working_contract_changed"):
        profile.require_working_contract(current, root)
    target = root / "backend/app/services/parsing.py"
    target.write_bytes(target.read_bytes() + b"\n# source drift\n")
    with pytest.raises(profile.SourceDenied, match="working_source_changed"):
        profile.working_snapshot(root)


def test_rehashed_safety_policy_cannot_weaken_existing_bounds(monkeypatch):
    raw = (profile.ROOT / profile.POLICY_PATH).read_bytes()
    value = json.loads(raw)
    value["processor_rlimits"]["cpu_seconds"] = 999
    changed = profile.canonical(value)
    monkeypatch.setattr(profile, "POLICY_SHA256", profile.digest(changed))
    with pytest.raises(profile.SourceDenied, match="policy_semantics_changed"):
        profile.validate_policy(changed)


@pytest.mark.parametrize("definitions", [False, True])
def test_distinct_full_source_and_definition_child_builds_bind_git_and_archive(source, definitions):
    root, current = source
    value, identity = build(source, definitions=definitions)
    assert identity["kind"] == (profile.DEFINITIONS_KIND if definitions else profile.KIND)
    assert identity["source_commit"] == current.commit and identity["qualification_sha256"] == current.qualification_sha256
    assert identity["fixed_renderer_base"] == (PARENT if definitions else courier.IMAGE)
    if definitions:
        recipe = profile.definition_recipe(PARENT, root)
        assert recipe.count(PARENT.encode()) == 2 and b"apt-get" not in recipe and b"COPY --from=definitions /var/lib/clamav /var/lib/clamav" in recipe
        with pytest.raises(builder.RefreshDenied):
            builder.build_contract(value, NONCE, profile.digest(recipe), full_source=True, source_revision=True,
                source_commit=current.commit)
    else:
        raw = builder.full_source_archive(source_revision=True, source_commit=current.commit)
        assert builder.archive_full_source(raw, source_revision=True, source_commit=current.commit) == profile.digest(raw)
    probe = release.SourceProbe(identity, root=root)
    assert probe.source_contract() == current.runtime
    old = identity | {"kind": builder.FULL_SOURCE_KIND}
    with pytest.raises(release.prior.NativeProofDenied, match="wrong_build_kind"):
        release.SourceProbe(old, root=root)


@pytest.mark.parametrize("change", ["git", "qualification", "generation", "runtime_label", "builder", "kind"])
def test_wrong_source_or_supplier_build_provenance_refuses(source, change):
    _, current = source
    value, identity = build(source)
    if change == "git":
        value["tags"][1] = "0" * 40
    elif change == "qualification":
        value["tags"][2] = "0" * 64
    elif change == "generation":
        value["sourceProvenance"]["resolvedStorageSource"]["generation"] = "5678"
    elif change == "runtime_label":
        position = value["steps"][0]["args"].index("--label") + 1
        value["steps"][0]["args"][position] += "wrong"
    elif change == "builder":
        value["serviceAccount"] = "projects/other/serviceAccounts/other@example.test"
    else:
        value["results"]["images"][0]["name"] = builder.REPOSITORY + ":refresh-" + NONCE
    with pytest.raises((builder.RefreshDenied, profile.SourceDenied)):
        builder.build_contract(value, NONCE, identity["refresh_dockerfile_sha256"], full_source=True,
            source_revision=True, source_commit=current.commit)


def test_payload_keeps_every_v3_safety_check_and_adds_versioned_parser_cases(source):
    root, current = source
    rendered = payload.rendered(IMAGE, current, root=root)
    assert "_base_main" in rendered and "pdf_spanning_columns_refused" in rendered
    assert current.runtime["/opt/document/app/services/parsing.py"] in rendered
    assert current.policy in rendered and payload.CORPUS_EVENT in rendered
    assert "max_page_edge_clip_delta" not in rendered  # No altered comparison policy injected.
    assert profile.digest((profile.ROOT / "infra/gcp/document_candidate_payload.py").read_bytes()) == profile.BASE_PAYLOAD_SHA256
    assert len(payload.EXTRA_CASES) == 6 and len(receipt.NAMES) == 17
    assert receipt.NAMES[:len(receipt.base.NAMES)] == receipt.base.NAMES
    assert "pdf_earlier_unaligned_sections" in payload.EXTRA_CASES
    assert {name for name in payload.EXTRA_CASES if name.endswith("_refused")} == {
        "pdf_spanning_columns_refused", "pdf_unaligned_gutter_refused"}
    assert release.SourceProbe.CONTRACT_VERSION == 4 and release.SourceProbe.REQUIRED_CASE_COUNT == 17


def test_rendered_inventory_executes_one_new_event_without_old_stdout_or_none_return(source, monkeypatch):
    root, current = source
    namespace = {"__name__": "owned_inventory_transport_test"}
    exec(compile(payload.rendered(IMAGE, current, root=root), "<owned-source-probe>", "exec"), namespace)
    identity = native.inventory()["identity_preflight"]
    identity = {key: row for key, row in identity.items() if key not in {"passed", "diagnostic", "cleanup_passed"}}
    identity.update(runtime_hashes=current.runtime, renderer_version=courier.RENDERER, font_manifest_sha256=courier.FONT)
    diagnostic = native.value()["identity_preflight"]["diagnostic"]
    worker = SimpleNamespace(configuration=lambda: (IMAGE, current.policy), _RequestDiagnostic=SimpleNamespace)
    monkeypatch.setitem(sys.modules, "service", worker)
    output = io.BytesIO()
    namespace.update(sys=SimpleNamespace(argv=["owned", "--inventory"], path=[], stdout=SimpleNamespace(buffer=output)),
        os=SimpleNamespace(environ={"CLOUD_RUN_EXECUTION": "hirewiz-doc-six-" + NONCE[:12] + "-owned"},
            getuid=lambda: 65534, getgid=lambda: 65534), auxiliary=lambda *_: identity,
        diagnostic_projection=lambda *_: diagnostic, cleanup_success=lambda *_: True)
    with pytest.raises(SystemExit) as stopped:
        namespace["source_main"]()
    assert stopped.value.code == 0 and len(output.getvalue().splitlines()) == 1
    report = json.loads(output.getvalue())
    assert report["event"] == payload.INVENTORY_EVENT and report["cases"] == []
    release.corpus.validate_inventory(report, IMAGE, current.runtime, policy=current.policy, event=payload.INVENTORY_EVENT)


def test_local_new_source_layout_requests_bind_new_policy_without_relabeling_old_controls():
    current = engines.candidate_docx_layout_corpus("sha256:" + "a" * 64, policy=profile.POLICY_SHA256)
    previous = engines.candidate_docx_layout_corpus("sha256:" + "a" * 64)
    assert [row[0] for row in current] == [row[0] for row in previous] == [
        "styled_multipage_fit", "rendered_reflow_refusal", "unsupported_source_font_refusal"]
    assert all(json.loads(raw)["policy_sha256"] == profile.POLICY_SHA256 for _, raw, _ in current)
    assert all(json.loads(raw)["policy_sha256"] == courier.POLICY_SHA256 for _, raw, _ in previous)


@pytest.mark.parametrize("definitions", [False, True])
def test_fresh_build_readback_preserves_exact_full_source_or_child_kind(source, monkeypatch, definitions):
    root, current = source
    value, identity = build(source, definitions=definitions)
    archive = (builder.source_archive(profile.definition_recipe(PARENT, root)) if definitions else
        builder.full_source_archive(source_revision=True, source_commit=current.commit))
    monkeypatch.setattr(builder, "native_http", lambda: SimpleNamespace(close=lambda: None))
    monkeypatch.setattr(builder, "native_json", lambda *_: value)
    monkeypatch.setattr(builder, "read_native_source", lambda *_: archive)
    monkeypatch.setattr(release.prior, "build_module", lambda: builder)
    probe = release.SourceProbe(identity, root=root)
    assert probe.build_identity(read_only_release=False, build_id=identity["build_id"], nonce=NONCE, image=IMAGE) == identity
    value["tags"][2] = "0" * 64
    with pytest.raises(builder.RefreshDenied, match="steps_changed"):
        probe.build_identity(read_only_release=False, build_id=identity["build_id"], nonce=NONCE, image=IMAGE)


def source_receipt(current):
    value = native.value()
    value.update(event=payload.CORPUS_EVENT, version=4, policy_sha256=current.policy)
    value["identity_preflight"].update(runtime_hashes=current.runtime,
        renderer_version=courier.RENDERER, font_manifest_sha256=courier.FONT)
    for name in payload.EXTRA_CASES:
        refused = name.endswith("_refused")
        example = copy.deepcopy(value["cases"][2 if refused else 0])
        example.update(case=name)
        example["binding"]["source_format"] = "pdf"
        if refused:
            example.pop("scan", None)
        else:
            example["reading_order_pass"] = True
        value["cases"].append(example)
    return value


def accepted(source, identity, probe):
    return release.prior.NativeAcceptance(IMAGE, identity["build_id"], identity["build_resource"],
        identity["source_archive_sha256"], identity["refresh_dockerfile_sha256"], source.sources,
        release.prior.PARENT + "/jobs/hirewiz-doc-six-" + NONCE[:12], "owned-job-uid", "owned-execution", "owned-execution-uid",
        profile.digest(probe._rendered(IMAGE).encode()), "d" * 64, source.policy, courier.FONT, courier.RENDERER,
        (datetime.now(UTC) + timedelta(days=2)).isoformat(), 4, 17)


@pytest.mark.parametrize("change", [None, "probe", "policy", "count", "intent", "result"])
def test_completed_source_history_is_joined_identity_never_replayed_authority(source, tmp_path, change):
    root, current = source
    _, identity = build(source, definitions=True)
    proof = b'{"scope":"synthetic-transport-only"}'
    identity["local_native_proof_sha256"] = profile.digest(proof)
    probe = release.SourceProbe(identity, root=root)
    acceptance = asdict(accepted(current, identity, probe))
    run = tmp_path / "owned-history"
    (run / "native-proof").mkdir(parents=True)
    (run / "local-native-proof.json").write_bytes(proof)
    (run / "local-native-proof.json").chmod(0o600)
    intent = {"build_identity_sha256": recovery.sha(recovery.canonical(identity)),
        "transition_journal": str(run / "transition"), "automatic_replay": False,
        "unknown_outcome": "read_only_reconcile_exact_transition_only"}
    complete = {"status": "document_refresh_serving", "image": IMAGE, "prior_image": PARENT,
        "native_acceptance_sha256": recovery.sha(recovery.canonical(acceptance)),
        "all_three_caller_pins_verified": True, "admission_restored": True,
        "synchronous_callers_drained": False, "current_native_proof_retained": True, "automatic_replay": False}
    result = complete | {"source_commit": current.commit, "renderer_contract_version": 4, "required_case_count": 17,
        "definition_expires_at": acceptance["definition_expires_at"], "prior_evidence_retained": True, "prior_jobs_retained": True}
    if change in {"probe", "policy", "count"}:
        key, value = {"probe": ("probe_sha256", "0" * 64), "policy": ("policy_sha256", courier.POLICY_SHA256),
                      "count": ("required_case_count", 11)}[change]
        acceptance[key] = value
        complete["native_acceptance_sha256"] = result["native_acceptance_sha256"] = recovery.sha(recovery.canonical(acceptance))
    elif change == "intent":
        intent["automatic_replay"] = True
    elif change == "result":
        result["source_commit"] = "0" * 40
    (run / "native-proof/transition.started.json").write_bytes(recovery.canonical(intent))
    (run / "native-proof/transition.started.json").chmod(0o600)
    reader = SimpleNamespace(ROOT=root, MAX_OUTPUT=builder.MAX_OUTPUT, infra=lambda name: release, need=builder.need, decode=builder.decode,
        verify_native_build=lambda _: {key: value for key, value in identity.items() if key != "local_native_proof_sha256"})
    args = (run, {"version": 4, "image": IMAGE, "build_identity": identity}, acceptance,
            copy.deepcopy(acceptance), complete, result, reader)
    if change is None:
        recovery.completed_serving_source(*args)
    else:
        with pytest.raises(builder.RefreshDenied, match="completed_source_"):
            recovery.completed_serving_source(*args)


def test_new_source_native_acceptance_keeps_old_safety_and_exact_new_identity(source, monkeypatch):
    root, current = source
    _, identity = build(source)
    probe = release.SourceProbe(identity, root=root)
    value = source_receipt(current)
    inventory = native.inventory()
    inventory.update(event=payload.INVENTORY_EVENT, policy_sha256=current.policy)
    inventory["identity_preflight"].update(runtime_hashes=current.runtime,
        renderer_version=courier.RENDERER, font_manifest_sha256=courier.FONT)
    definition = "123/" + datetime.now(UTC).replace(microsecond=0).isoformat()
    for row in value["cases"]:
        if "scan" in row:
            row["scan"]["definitions"] = definition
    job = {"name": release.prior.PARENT + "/jobs/hirewiz-doc-six-" + NONCE[:12], "uid": "owned-job-uid"}
    execution = {"name": "owned-execution", "uid": "owned-execution-uid"}
    monkeypatch.setattr(probe, "_observation", lambda _client, mode, *_args: (job, execution, inventory if mode == "inventory" else value))
    result = probe._acceptance(object())
    assert result.source_sha256 == current.sources and result.policy_sha256 == current.policy
    assert result.contract_version == 4 and result.required_case_count == 17
    release.transition.require_current_acceptance(result, IMAGE, probe)
    with pytest.raises(release.transition.TransitionDenied, match="current_acceptance_required"):
        release.transition.require_current_acceptance(replace(result, contract_version=3, required_case_count=11), IMAGE, probe)


@pytest.mark.parametrize("lost_at", [None, 4])
def test_source_transition_preserves_pins_secrets_flags_and_unknown_outcome(source, monkeypatch, tmp_path, lost_at):
    from tests import test_document_refresh_transition as lifecycle
    root, current = source
    _, identity = build(source)
    probe = release.SourceProbe(identity, root=root)
    acceptance = accepted(current, identity, probe)
    monkeypatch.setattr(probe, "verify_successor", lambda *_: acceptance)
    monkeypatch.setattr(probe, "verify_current_successor", lambda *_: acceptance)
    http = lifecycle.Http(lost_at=lost_at)
    api = http.services[release.transition.CALLERS[0]]["template"]["containers"][0]["env"]
    api[:] = [row for row in api if row["name"] not in {"RESUME_DIRECT_UPLOAD_ENABLED", "OPTIONAL_AI_GENERATION_ENABLED"}]
    api.extend([{"name": "PAYMENTS_ENABLED", "value": "false"}, {"name": "OPTIONAL_AI_GENERATION_ENABLED", "value": "false"}])
    before = copy.deepcopy(http.services)
    journal = tmp_path / "transition"
    if lost_at:
        with pytest.raises(lifecycle.transition.TransitionDenied, match="outcome_unknown"):
            release.transition.transition(IMAGE, identity, tmp_path / "native", journal, http=http, probe=probe)
        assert len(http.writes) == lost_at
        original = {p.name: p.read_bytes() for p in journal.iterdir()}
        with pytest.raises(release.transition.TransitionDenied):
            release.transition.transition(IMAGE, identity, tmp_path / "native", journal, http=http, probe=probe)
        assert len(http.writes) == lost_at and {p.name: p.read_bytes() for p in journal.iterdir()} == original
        return
    result = release.transition.transition(IMAGE, identity, tmp_path / "native", journal, http=http, probe=probe)
    assert result["admission_restored"] is True and result["automatic_replay"] is False
    assert [name for name, _ in http.writes] == [*release.transition.CALLERS, release.transition.WORKER,
        *release.transition.CALLERS, *release.transition.CALLERS]
    for name in release.transition.CALLERS:
        original = release.transition.environment(before[name]["template"])
        final = release.transition.environment(http.services[name]["template"])
        assert {key: row for key, row in final.items() if key not in release.transition.PINS} == {
            key: row for key, row in original.items() if key not in release.transition.PINS}
        assert final["DOCUMENT_WORKER_POLICY_SHA256"]["value"] == current.policy
        assert final["DOCUMENT_WORKER_IMAGE_DIGEST"]["value"] == IMAGE


@pytest.mark.parametrize("change", [None, "old_kind", "missing_parser_case", "reading", "cleanup", "runtime", "policy"])
def test_native_proof_requires_exact_new_kind_and_all_safety_and_parser_cases(source, change):
    _, current = source
    value = source_receipt(current)
    if change == "old_kind":
        value.update(event="hirewiz_document_candidate_required_v3", version=3)
    elif change == "missing_parser_case":
        value["cases"].pop()
    elif change == "reading":
        value["cases"][-4]["reading_order_pass"] = False
    elif change == "cleanup":
        value["cases"][-1]["diagnostic"]["cleanup_code"] = 1
    elif change == "runtime":
        value["identity_preflight"]["runtime_hashes"] = native.p.source_contract()
    elif change == "policy":
        value["policy_sha256"] = courier.POLICY_SHA256
    if change is None:
        assert receipt.validate(value, IMAGE, current.policy, current.runtime) is True
    else:
        with pytest.raises(ValueError):
            receipt.validate(value, IMAGE, current.policy, current.runtime)


def test_six_fixed_pdf_cases_use_actual_parser_without_live_scanner_or_models(monkeypatch):
    from tests.test_document_processor import load
    from tests.test_document_processor import payload as envelope
    processor = load("processor")
    monkeypatch.setattr(processor, "scan_original", lambda _: {"engine": "ClamAV", "version": "synthetic", "definitions": "not-native-evidence"})
    for name, content in engines.reading_order_corpus():
        if name.endswith("_refused"):
            with pytest.raises(processor.InspectionDenied):
                processor.inspect(envelope(content))
        else:
            result = processor.inspect(envelope(content))
            engines.validate_reading_order_result(name, content, 0, result)


def test_unaligned_positive_control_rejects_missing_mixed_or_reordered_facts(monkeypatch):
    from tests.test_document_processor import load
    from tests.test_document_processor import payload as envelope
    processor = load("processor")
    monkeypatch.setattr(processor, "scan_original", lambda _: {"engine": "ClamAV", "version": "synthetic", "definitions": "not-native-evidence"})
    content = dict(engines.reading_order_corpus())["pdf_earlier_unaligned_sections"]
    actual = processor.inspect(envelope(content))
    engines.validate_reading_order_result("pdf_earlier_unaligned_sections", content, 0, actual)
    for change in ("education", "experience", "projects", "skills", "years", "order"):
        altered = copy.deepcopy(actual)
        if change in {"education", "experience", "projects", "skills"}:
            altered["parsed"]["sections"][change] = "A missing or misplaced source fact"
        elif change == "years":
            altered["parsed"]["experience_years"] = 6
        else:
            altered["parsed"]["raw_text"] = "EXPERIENCE\nPROJECTS\nEDUCATION\nSKILLS"
        with pytest.raises(engines.DocumentImageError):
            engines.validate_reading_order_result("pdf_earlier_unaligned_sections", content, 0, altered)


def test_native_spacing_control_detects_absolute_threshold_word_collapse(monkeypatch):
    from app.services import parsing
    from tests.test_document_processor import load
    from tests.test_document_processor import payload as envelope
    processor = load("processor")
    monkeypatch.setattr(processor, "scan_original", lambda _: {"engine": "ClamAV", "version": "synthetic", "definitions": "not-native-evidence"})
    content = dict(engines.reading_order_corpus())["pdf_font_relative_spacing"]
    current = processor.inspect(envelope(content))
    engines.validate_reading_order_result("pdf_font_relative_spacing", content, 0, current)
    # pdfplumber's None selects its original absolute3pt default. No alternate
    # parser or expected extraction is injected into the deliverable.
    monkeypatch.setattr(parsing, "PDF_WORD_X_TOLERANCE_RATIO", None)
    previous = processor.inspect(envelope(content))
    assert previous["parsed"]["sections"].get("experience") != current["parsed"]["sections"]["experience"]
    with pytest.raises(engines.DocumentImageError):
        engines.validate_reading_order_result("pdf_font_relative_spacing", content, 0, previous)


def test_retained_readback_does_not_authorize_new_work_after_helper_changes(source):
    root, _ = source
    _, identity = build(source)
    execution = release.SourceProbe(identity, root=root)
    before = execution.desired(IMAGE, NONCE, "corpus")
    path = root / "infra/gcp/document_source_payload.py"
    path.write_bytes(path.read_bytes() + b"\n# An unversioned helper edit\n")
    with pytest.raises(profile.SourceDenied, match="working_contract_changed"):
        execution.source_contract()
    historical = release.SourceProbe({k: v for k, v in identity.items() if k != "local_native_proof_sha256"},
                                     root=root, serving_read=True)
    assert historical.desired(IMAGE, NONCE, "corpus") == before
    with pytest.raises(release.prior.NativeProofDenied, match="candidate_changed"):
        historical.verify_successor(IMAGE, historical.build, root)


@pytest.mark.parametrize("change", ["not_current", "policy", "runtime", "font", "version"])
def test_definition_child_checks_actual_parent_before_any_upload(source, monkeypatch, tmp_path, change):
    _, current = source
    observed = IMAGE if change == "not_current" else PARENT
    class Clients:
        def preflight(self):
            return {"service": {"template": {"containers": [{"image": observed}]}}}
        def close(self):
            pass
    accepted = SimpleNamespace(image=PARENT, policy_sha256=current.policy, source_sha256=current.sources,
        contract_version=4, required_case_count=17, renderer_version=courier.RENDERER, font_manifest_sha256=courier.FONT)
    if change == "policy":
        accepted.policy_sha256 = courier.POLICY_SHA256
    elif change == "runtime":
        accepted.source_sha256 = {}
    elif change == "font":
        accepted.font_manifest_sha256 = "0" * 64
    elif change == "version":
        accepted.contract_version = 3
    monkeypatch.setattr(release.prior, "Clients", Clients)
    monkeypatch.setattr(release, "current_probe", lambda *_: SimpleNamespace(verify_current_successor=lambda *_: accepted))
    monkeypatch.setattr(builder, "native_http", lambda: pytest.fail("No source upload or Build before parent proof"))
    with pytest.raises(release.prior.NativeProofDenied, match="parent_"):
        builder.native_build(tmp_path / "uncreated", profile.definition_recipe(PARENT, source[0]),
            nonce=NONCE, source_definitions=True, source_parent=PARENT)
    assert not (tmp_path / "uncreated").exists()


def test_scheduled_refresh_uses_only_source_definition_child_without_downgrade(monkeypatch, tmp_path):
    calls = []
    context = object()
    identity = {"kind": profile.DEFINITIONS_KIND, "immutable_image": IMAGE}
    def selected(directory, **kwargs):
        assert directory == tmp_path and kwargs["parent"] == PARENT
        assert kwargs["publish"] is True and kwargs["nonce"] == NONCE
        calls.append("definitions_child")
        return {"build_identity": identity}
    def promote(actual, native_journal, transition_journal, **kwargs):
        assert actual is identity and kwargs["application_context"] is context
        calls.append("native_and_transition")
        return {"status": "document_refresh_serving"}
    modules = {"document_refresh_recovery": SimpleNamespace(prepare=lambda *a, **k: NONCE),
        "document_refresh_probe": SimpleNamespace(Clients=lambda: SimpleNamespace(
            preflight=lambda: {"service": {"template": {"containers": [{"image": PARENT}]}}}, close=lambda: None),
            policy_for_serving=lambda *_: profile.POLICY_SHA256),
        "document_source_release": SimpleNamespace(promote=promote)}
    monkeypatch.setattr(builder, "infra", modules.__getitem__)
    monkeypatch.setattr(builder, "source_definitions_candidate", selected)
    monkeypatch.setattr(builder, "candidate", lambda *a, **k: pytest.fail("No V2 downgrade"))
    monkeypatch.setattr(builder, "full_source_candidate", lambda *a, **k: pytest.fail("No daily full-source rebuild"))
    result = builder.refresh(tmp_path, publish=True, now=native.historical.NOW, application_context=context)
    assert result["status"] == "document_refresh_serving" and calls == ["definitions_child", "native_and_transition"]


def test_unknown_source_policy_is_refused_without_a_build(monkeypatch):
    monkeypatch.setattr(builder, "native_http", lambda: pytest.fail("No native mutation for unknown policy"))
    metadata = {"template": {"containers": [{"image": PARENT, "env": [
        {"name": "DOCUMENT_WORKER_POLICY_SHA256", "value": "f" * 64}]}]}}
    with pytest.raises(release.prior.NativeProofDenied, match="serving_policy_unverified"):
        release.prior.policy_for_serving(metadata, PARENT)


@pytest.mark.parametrize("phase", ["create", "run"])
def test_unknown_native_ack_never_replays_or_relabels_old_image(source, monkeypatch, tmp_path, phase):
    root, _ = source
    _, identity = build(source)
    probe = release.SourceProbe(identity, root=root)
    calls = []
    class Missing(Exception):
        status_code = 404
    class Clients:
        errors = SimpleNamespace(HttpError=Missing)
        def preflight(self):
            return {}
        def job(self, name):
            if phase == "create" or not any(call == "create" for call in calls):
                raise Missing()
            result = native.historical.job(IMAGE, probe.handles(identity)["inventory"]["nonce"])
            result.update(probe.desired(IMAGE, probe.handles(identity)["inventory"]["nonce"], "inventory"))
            result.update(executionCount=0, latestCreatedExecution=None)
            return result
        def create(self, _):
            calls.append("create")
            if phase == "create":
                raise RuntimeError("Synthetic unknown acknowledgement")
            return {"name": "owned-create-operation"}
        def start(self, _):
            calls.append("run")
            raise RuntimeError("Synthetic unknown acknowledgement")
        def close(self):
            pass
    monkeypatch.setattr(probe, "build_identity", lambda **_: identity)
    monkeypatch.setattr(v3.prior, "Clients", Clients)
    monkeypatch.setattr(v3.prior, "wait_operation", lambda *_: None)
    journal = tmp_path / "owned-native-journal"
    with pytest.raises(RuntimeError, match="unknown acknowledgement"):
        v3.run_successor(identity, journal, adapter=probe)
    original = {p.name: p.read_bytes() for p in journal.iterdir()}
    assert "inventory." + phase + ".started.json" in original
    with pytest.raises(release.prior.NativeProofDenied, match="fresh_journal_required"):
        v3.run_successor(identity, journal, adapter=probe)
    assert {p.name: p.read_bytes() for p in journal.iterdir()} == original
    assert calls == (["create"] if phase == "create" else ["create", "run"])
