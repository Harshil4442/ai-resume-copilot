"""Offline draft/evidence consistency tests; no real model or reviewer outputs."""
import csv
import json
from pathlib import Path

import pytest
from backend.app.domains.analysis import quality_packet as packet


@pytest.fixture
def draft(tmp_path):
    root = tmp_path / "packet"
    packet.build_draft(root)
    packet.write_templates(root)
    return root


def artifact(root, name, value):
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(packet.canonical(value))
    return {"path": name, "sha256": packet.file_digest(path)}


def test_draft_has_200_varied_pairs_and_frozen_disjoint_risk_cohorts(draft):
    report = packet.validate_packet(draft)
    assert report["draft_inputs_valid"]
    assert report["case_count"] == 200 and report["development_pairs"] == 40 and report["held_out_pairs"] == 160
    assert all(count >= 30 for count in report["coverage"]["held_out"].values())
    assert report["quality_status"] == "not_evaluated" and report["authorizes_release"] is False
    _, _, cases = packet.load_packet(draft)
    assert all(case["source_recipe"]["native_artifact_state"] == "not_materialized" for case in cases)
    assert len({case["job"]["title"] for case in cases}) == 5
    assert len({case["resume"]["raw_text"] for case in cases}) == 200
    assert packet.draft_cases() == cases


@pytest.mark.parametrize("name", ["cases.jsonl", "protocol.json", "rubric.md"])
def test_frozen_artifact_mutation_fails_input_validation(draft, name):
    with (draft / name).open("a") as file:
        file.write("\nchanged\n")
    assert packet.validate_packet(draft)["draft_inputs_valid"] is False


def test_rebuild_cannot_silently_replace_frozen_inputs(draft):
    before = packet.file_digest(draft / "manifest.json")
    with pytest.raises(ValueError, match="already exists"):
        packet.build_draft(draft)
    assert packet.file_digest(draft / "manifest.json") == before


def test_no_outputs_or_review_scores_are_fabricated_by_templates(draft):
    bundle = json.loads((draft / "bundle.template.json").read_bytes())
    assert all(row["baseline"] is None and row["candidate"] is None for row in bundle["cases"])
    with (draft / "reviews.template.csv").open() as handle:
        rows = list(csv.DictReader(handle))
    assert len(rows) == 800
    assert all(row[metric] == "" for row in rows for metric in packet.METRICS)
    result = packet.evaluate_evidence(draft, draft / "missing")
    assert not result["evidence_complete"] and not result["authorizes_release"]
    assert "No imported evidence bundle" in result["errors"][0]


def test_empty_template_is_incomplete_not_a_successful_qualification(draft, tmp_path):
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    (evidence / "bundle.json").write_bytes((draft / "bundle.template.json").read_bytes())
    report = packet.evaluate_evidence(draft, evidence)
    assert not report["evidence_complete"] and not report["authorizes_release"]
    assert report["error_count"] > 200
    assert any("Independent approval" in error for error in report["errors"])


def test_artifact_import_rejects_path_escape_and_digest_tampering(tmp_path):
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    outside = tmp_path / "outside.json"
    outside.write_text("{}")
    errors = []
    assert packet._artifact(evidence, {"path": "../outside.json", "sha256": packet.file_digest(outside)}, errors, "escape") is None
    inside = artifact(evidence, "inside.json", {"synthetic": True})
    (evidence / "inside.json").write_text("changed")
    assert packet._artifact(evidence, inside, errors, "tampered") is None
    assert len(errors) == 2


def test_native_signature_filter_does_not_claim_visual_validation(tmp_path):
    file = tmp_path / "wrong.docx"
    file.write_text("%PDF-not-a-DOCX")
    assert not packet._native(file, "docx")
    errors = []
    packet._native_receipt(tmp_path, artifact(tmp_path, "receipt.json", {"structure_pass": True}),
        source_sha="a" * 64, target_sha="b" * 64, errors=errors, label="synthetic")
    assert any("Native validation incomplete" in error for error in errors)
    assert any("Rendered pages missing" in error for error in errors)


def test_one_question_and_unknown_evidence_fail_output_contract():
    case = packet.draft_cases()[0]
    assert not packet._output_contract("interview_questions", {"questions": [{"question": "Only one?", "answer": "one", "evidence_ids": []}]}, case)
    rows = [{"question": f"How would you address scenario {i}?", "answer": "Explain the relevant task and decisions.", "evidence_ids": []} for i in range(8)]
    assert packet._output_contract("interview_questions", {"questions": rows}, case)
    rows[1]["evidence_ids"] = ["unapproved"]
    assert not packet._output_contract("interview_questions", {"questions": rows}, case)


def test_blinding_requires_actual_hashed_output_files(draft, tmp_path):
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    (evidence / "bundle.json").write_bytes((draft / "bundle.template.json").read_bytes())
    destination = tmp_path / "reviewers"
    with pytest.raises(ValueError, match="Actual hashed outputs"):
        packet.make_blinded(draft, evidence, destination)
    assert not destination.exists()


def test_blind_packaging_separates_mapping_and_leaves_every_score_empty(draft, tmp_path):
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    # Packaging fixture only. These are deliberately not approved/model outputs.
    baseline = artifact(evidence, "reference.json", {"answer": "Synthetic reference", "provider": "synthetic-baseline", "credit_execution": {"run_id": "internal"}})
    candidate = artifact(evidence, "candidate.json", {"answer": "Synthetic candidate", "model": "synthetic-candidate"})
    source = artifact(evidence, "source.json", {"synthetic": True})
    bundle = json.loads((draft / "bundle.template.json").read_bytes())
    for case in bundle["cases"]:
        case.update(input_artifact=source, baseline={"output": baseline}, candidate={"output": candidate})
    (evidence / "bundle.json").write_bytes(packet.canonical(bundle))
    result = packet.make_blinded(draft, evidence, tmp_path / "review-pack")
    assert result["pairs_packaged"] == 200 and result["scores_generated"] == 0
    coordinator = Path(result["coordinator_map"])
    assert coordinator.parent.stat().st_mode & 0o777 == 0o700
    mapping = json.loads(coordinator.read_bytes())
    assert mapping["reviewer_blinding_checked"] is False
    first = next(iter(mapping["pairs"].values()))
    assert {first["A"], first["B"]} == {"baseline", "candidate"}
    reviewer = Path(result["reviewer_directory"])
    view = json.loads((reviewer / f"{first['pair_id']}-A.json").read_bytes())
    assert set(view) == {"answer"}
    with (reviewer / "reviewer_1.csv").open() as handle:
        assert all(row["reviewer_id"] == "" and all(row[name] == "" for name in packet.METRICS) for row in csv.DictReader(handle))


def test_statistical_point_estimate_must_match_actual_review_arithmetic(draft, tmp_path):
    manifest, protocol, cases = packet.load_packet(draft)
    held = [case for case in cases if case["split"] == "held_out"]
    code = artifact(tmp_path, "analysis-code.json", {"synthetic_test": True})
    design = artifact(tmp_path, "design.json", {"synthetic_test": True})
    counts = packet.coverage(held)["all"]
    scores = {(case["case_id"], label, metric): 3 for case in held for label in ("A", "B") for metric in packet.METRICS}
    blinding = {"pairs": {case["case_id"]: {"A": "baseline", "B": "candidate"} for case in cases}}
    result = {"state": "complete", "protocol_sha256": manifest["protocol_sha256"], "reviews_sha256": "a" * 64,
        "adjudications_sha256": "b" * 64, "held_out_case_ids": [case["case_id"] for case in held],
        "confidence_level": 0.95, "sidedness": "one_sided", "difference": "candidate_minus_reference", "noninferiority_margin": 0,
        "method": "synthetic-test-only", "analyst_id": "synthetic-analyst", "primary_method_reference": "https://example.invalid/synthetic",
        "analysis_code": code, "powered_design_review": design,
        "cohort_results": [{"cohort": cohort, "metric": metric, "paired_n": counts[cohort], "paired_difference": 0.5, "lower_confidence_bound": 0.1} for cohort in protocol["important_cohorts"] for metric in packet.METRICS]}
    bundle = {"statistical_analysis": artifact(tmp_path, "analysis.json", result), "reviews": {"sha256": "a" * 64}, "adjudications": {"sha256": "b" * 64}}
    approval = {"statistical_method": "synthetic-test-only", "statistical_design": design}
    errors = []
    packet._statistics(tmp_path, bundle, protocol, manifest, cases, approval, blinding, scores, errors)
    assert any("mean differs from adjudicated human scores" in error for error in errors)


def test_non_object_imported_evidence_is_incomplete_without_crashing(draft, tmp_path):
    (tmp_path / "bundle.json").write_text("[]")
    result = packet.evaluate_evidence(draft, tmp_path)
    assert result["evidence_complete"] is False and result["authorizes_release"] is False
    errors = []
    record = artifact(tmp_path, "array.json", ["not a receipt"])
    assert packet._json_artifact(tmp_path, record, errors, "receipt") is None
    assert errors == ["Invalid JSON artifact: receipt"]
