"""Offline evidence preparation and consistency checks, never model qualification.

No SDK, network, provider credential, inference, statistical score generation or
production activation is implemented here. Native artifacts and funded model
outputs are imported from separately authorized durable runners.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import math
import random
import re
import secrets
import shutil
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any
from zipfile import BadZipFile, ZipFile

VERSION = "unified-quality-packet-v1"
SUPPORTED_VERSION = "unified-quality-packet-v2-pdftex"
METRICS = ("relevance", "completeness", "clarity", "usefulness")
CRITICAL = ("factual_error", "privacy_error", "document_corruption")
FORMATS = ("pdf", "docx", "tex", "texzip")
ROLES = (
    ("backend_engineer", "Backend Engineer", ["Python", "PostgreSQL", "REST APIs"], "Kubernetes"),
    ("frontend_engineer", "Frontend Engineer", ["TypeScript", "React", "Accessibility"], "Playwright"),
    ("data_analyst", "Data Analyst", ["SQL", "Python", "Tableau"], "dbt"),
    ("quality_engineer", "Quality Engineer", ["Java", "Selenium", "API testing"], "Performance testing"),
    ("customer_operations", "Customer Operations Specialist", ["CRM", "Excel", "Support operations"], "Salesforce"),
)
LEVELS = (("entry", 1), ("mid", 3), ("senior", 6), ("lead", 10))
REVIEW_FIELDS = ("case_id", "pair_id", "reviewer_id", "label", *METRICS, *CRITICAL, "notes")
ADJUDICATION_FIELDS = ("case_id", "pair_id", "label", "metric", "adjudicator_id", "resolved_score", "reason")


def canonical(value) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n").encode()


def digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def file_digest(path: Path) -> str:
    hashed = hashlib.sha256()
    with path.open("rb") as handle:
        for part in iter(lambda: handle.read(1024 * 1024), b""):
            hashed.update(part)
    return hashed.hexdigest()


def _write_json(path, value):
    path.write_bytes(canonical(value))


def _case(role_index: int, level_index: int, variant: int) -> dict:
    number = role_index * 40 + level_index * 10 + variant + 1
    identity = f"synth-{number:03d}"
    role, title, skills, gap = ROLES[role_index]
    level, years = LEVELS[level_index]
    source_format = FORMATS[(level_index + variant) % len(FORMATS)]
    risks = ["numeric_claims"]
    if variant in {0, 1, 5, 6}:
        risks.append("long_documents")
    if variant in {1, 2, 6, 7}:
        risks.append("tables")
    if variant in {2, 3, 7, 8}:
        risks.append("multicolumn")
    if variant in {3, 4, 8, 9}:
        risks.append("unusual_fonts")
    if variant % 2:
        risks.append("incomplete_evidence")
    metrics = (12 + number % 29, 2 + number % 11)
    facts: list[dict[str, Any]] = [
        {"id": f"{identity}-e1", "text": f"Used {skills[0]} and {skills[1]} to complete {metrics[1]} internal projects for Fictional Organization {role_index + 1}.", "approved_in_fixture": True},
        {"id": f"{identity}-e2", "text": f"Reduced the measured processing time by {metrics[0]}% through documented workflow changes.", "approved_in_fixture": "incomplete_evidence" not in risks},
        {"id": f"{identity}-e3", "text": f"Worked with {skills[2]} and reviewed implementation decisions with the project team.", "approved_in_fixture": True},
    ]
    projects = [f"Project {i + 1}: investigated a distinct {title.lower()} scenario, recorded assumptions, and documented test results without claiming an unmeasured outcome." for i in range(36 if "long_documents" in risks else 2)]
    sections: list[dict[str, str]] = [
        {"heading": "Summary", "text": f"Synthetic {level} {title.lower()} with {years} year(s) of documented experience. All identities and claims in this fixture are fictional."},
        {"heading": "Experience", "text": "\n".join(item["text"] for item in facts)},
        {"heading": "Projects", "text": "\n".join(projects)},
        {"heading": "Education", "text": f"Fictional Institute {role_index + 1}; undergraduate degree; completed {2026 - years}."},
        {"heading": "Skills", "text": ", ".join(skills)},
    ]
    return {
        "case_id": identity, "dataset_state": "synthetic_draft_not_approved", "contains_real_person_data": False,
        "role": role, "seniority": level, "language": "en", "source_format": source_format,
        "cohorts": [f"format:{source_format}", f"role:{role}", f"seniority:{level}", *[f"risk:{risk}" for risk in risks]],
        "resume": {"name": f"Synthetic Candidate {number:03d}", "email": f"candidate-{number:03d}@example.invalid",
                   "experience_years": years, "skills": skills, "sections": sections,
                   "raw_text": "\n\n".join(item["heading"] + "\n" + item["text"] for item in sections),
                   "evidence": facts, "approval_semantics": "Fixture truth labels only; no real candidate or reviewer approval has occurred."},
        "job": {"title": title, "employer": f"Fictional Employer {number:03d}",
                "location": ("Bengaluru, India", "Remote, India", "London, UK", "Toronto, Canada")[number % 4],
                "minimum_experience_years": years + (1 if number % 3 == 0 else 0),
                "required_skills": [*skills, gap], "description": f"Work on {title.lower()} tasks using {', '.join(skills)}. The role also requests {gap}; the candidate has not claimed that skill. Describe evidence gaps without inventing experience.",
                "source_url": f"https://careers.example.invalid/jobs/{identity}", "work_authorization": "unknown"},
        "source_recipe": {"format": source_format, "native_artifact_state": "not_materialized",
                          "columns": 2 if "multicolumn" in risks else 1,
                          "table_sections": ["Experience", "Education"] if "tables" in risks else [],
                          "font": "Courier Prime" if "unusual_fonts" in risks else "Times New Roman",
                          "required_links": [f"https://portfolio.example.invalid/{identity}"],
                          "sections_in_order": [item["heading"] for item in sections],
                          "margin_inches": 0.7 if number % 2 else 0.85,
                          "long_document": "long_documents" in risks},
        "invariants": {"source_must_remain_immutable": True, "preserve_source_numbers": True,
                       "unclaimed_skill": gap, "approved_evidence_ids": [item["id"] for item in facts if item["approved_in_fixture"]],
                       "missing_evidence_is_not_lack_of_capability": True, "paid_interview_questions": 8,
                       "may_infer_work_authorization": False, "may_invent_personal_claims": False},
    }


def draft_cases() -> list[dict]:
    cases = [_case(role, level, variant) for role in range(5) for level in range(4) for variant in range(10)]
    # Freeze an 80% held-out split stratified by role AND format. The test set
    # remains public synthetic material: no hidden-data claim is made.
    strata = defaultdict(list)
    for item in cases:
        strata[(item["role"], item["source_format"])].append(item)
    for members in strata.values():
        ordered = sorted(members, key=lambda item: digest((VERSION + item["case_id"]).encode()))
        for index, item in enumerate(ordered):
            item["split"] = "development" if index < 2 else "held_out"
    return cases


def coverage(cases):
    result: dict[str, Counter[str]] = {"all": Counter(), "development": Counter(), "held_out": Counter()}
    for item in cases:
        for cohort in item["cohorts"]:
            result["all"][cohort] += 1
            result[item["split"]][cohort] += 1
    return {name: dict(sorted(value.items())) for name, value in result.items()}


def build_draft(root: Path) -> dict:
    if (root / "manifest.json").exists():
        raise ValueError("The frozen packet already exists; create a new version instead of overwriting it")
    root.mkdir(parents=True, exist_ok=True)
    if not (root / "rubric.md").exists():
        shutil.copyfile(Path(__file__).resolve().parents[3] / "evals/unified_credit_v1/rubric.md", root / "rubric.md")
    cases = draft_cases()
    cohorts = coverage(cases)
    protocol = {
        "schema_version": VERSION, "state": "draft_requires_independent_approval",
        "minimum_pairs": 200, "minimum_held_out_per_cohort": 30,
        "important_cohorts": sorted(cohorts["all"]), "metrics": list(METRICS),
        "critical_defects": list(CRITICAL), "tolerated_quality_degradation": 0,
        "confidence": {"level": 0.95, "sidedness": "one_sided", "difference": "candidate_minus_reference", "required_lower_bound": 0},
        "statistical_method": "Must be prespecified and independently reviewed before model execution; no method or power is approved by this draft.",
        "split_policy": "40 development and 160 held-out, stratified by role and format; never tune on held-out output or reviewer scores.",
        "human_review": {"reviewers_per_output": 2, "blinded_labels": ["A", "B"], "distinct_adjudicator": True,
                         "reviewer_roles": ["reviewer_1", "reviewer_2", "adjudicator"]},
        "execution_policy": "Import only actual outputs from an explicitly funded durable runner. No raw SDK runner or new money ledger is provided.",
        "native_inputs": "Source recipes are draft specifications only. Materialize, inspect and hash actual PDF/DOCX/TeX/TeXZIP sources before reference approval or model evaluation.",
        "release_authority": "This tool checks evidence consistency only. It never authorizes production release or certifies model quality.",
    }
    raw = b"".join(canonical(item) for item in cases)
    (root / "cases.jsonl").write_bytes(raw)
    _write_json(root / "protocol.json", protocol)
    manifest = {"schema_version": VERSION, "dataset_id": "hirewiz-synthetic-draft-200-v1",
                "state": "frozen_synthetic_draft_not_approved_reference", "case_count": len(cases),
                "corpus_sha256": digest(raw), "protocol_sha256": file_digest(root / "protocol.json"),
                "coverage": cohorts, "rubric_sha256": file_digest(root / "rubric.md"),
                "cases": [{"case_id": item["case_id"], "split": item["split"], "sha256": digest(canonical(item))} for item in cases]}
    _write_json(root / "manifest.json", manifest)
    return manifest


def load_packet(root: Path):
    manifest = json.loads((root / "manifest.json").read_bytes())
    protocol = json.loads((root / "protocol.json").read_bytes())
    cases = [json.loads(line) for line in (root / "cases.jsonl").read_text().splitlines() if line.strip()]
    return manifest, protocol, cases


def validate_packet(root: Path) -> dict:
    errors = []
    try:
        manifest, protocol, cases = load_packet(root)
        schema_version = manifest.get("schema_version")
        if schema_version not in {VERSION, SUPPORTED_VERSION} or protocol.get("schema_version") != schema_version:
            errors.append("Packet and protocol versions do not match a supported frozen schema")
        if file_digest(root / "cases.jsonl") != manifest["corpus_sha256"]:
            errors.append("Corpus digest changed")
        if file_digest(root / "protocol.json") != manifest["protocol_sha256"]:
            errors.append("Frozen protocol digest changed")
        if file_digest(root / "rubric.md") != manifest["rubric_sha256"]:
            errors.append("Frozen rubric digest changed")
        by_id = {item["case_id"]: item for item in cases}
        if len(cases) < 200 or len(cases) != len(by_id) or len(cases) != manifest["case_count"]:
            errors.append("At least 200 distinct pairs are required")
        if len(manifest["cases"]) != len(cases) or {x["case_id"] for x in manifest["cases"]} != set(by_id):
            errors.append("Manifest cases differ from corpus")
        for record in manifest["cases"]:
            item = by_id.get(record["case_id"], {})
            if record["sha256"] != digest(canonical(item)) or record["split"] != item.get("split"):
                errors.append(f"Case digest/split mismatch: {record['case_id']}")
        actual = coverage(cases)
        if actual != manifest["coverage"]:
            errors.append("Coverage differs from frozen manifest")
        for cohort in protocol["important_cohorts"]:
            if actual["held_out"].get(cohort, 0) < protocol["minimum_held_out_per_cohort"]:
                errors.append(f"Held-out risk cohort requires more cases: {cohort}")
        for item in cases:
            if item["contains_real_person_data"] is not False or item["dataset_state"] != "synthetic_draft_not_approved":
                errors.append(f"Draft provenance changed: {item['case_id']}")
            if item["source_recipe"]["native_artifact_state"] != "not_materialized":
                errors.append("A recipe cannot attest that native validation happened")
        return {"schema_version": schema_version, "draft_inputs_valid": not errors,
                "case_count": len(cases), "development_pairs": sum(item["split"] == "development" for item in cases),
                "held_out_pairs": sum(item["split"] == "held_out" for item in cases), "coverage": actual,
                "quality_status": "not_evaluated", "approved_reference_count": 0,
                "native_artifacts_materialized_by_this_tool": 0, "authorizes_release": False, "errors": errors}
    except (OSError, ValueError, KeyError, TypeError) as exc:
        return {"draft_inputs_valid": False, "authorizes_release": False, "errors": [f"Invalid packet: {type(exc).__name__}"]}


def _artifact(root: Path, record: dict, errors: list[str], label: str) -> Path | None:
    try:
        relative = Path(record["path"])
        path = (root / relative).resolve()
        if relative.is_absolute() or root.resolve() not in path.parents or path.is_symlink():
            raise ValueError("Artifact path escapes evidence directory")
        if not path.is_file() or path.stat().st_size > 50 * 1024 * 1024:
            raise ValueError("Missing or oversized evidence artifact")
        if not re.fullmatch(r"[a-f0-9]{64}", record["sha256"]) or file_digest(path) != record["sha256"]:
            raise ValueError("Artifact hash mismatch")
        return path
    except (OSError, ValueError, KeyError, TypeError):
        errors.append(f"Missing or invalid artifact: {label}")
        return None


def _native(path: Path, expected_format: str) -> bool:
    try:
        if expected_format == "pdf":
            return path.read_bytes().startswith(b"%PDF-")
        if expected_format == "tex":
            return "\\documentclass" in path.read_text() and "\\begin{document}" in path.read_text()
        with ZipFile(path) as archive:
            names = set(archive.namelist())
            if any(name.startswith("/") or ".." in Path(name).parts for name in names):
                return False
            return {"[Content_Types].xml", "word/document.xml"}.issubset(names) if expected_format == "docx" else "main.tex" in names
    except (OSError, UnicodeError, BadZipFile):
        return False


def _json_artifact(root, record, errors, label):
    path = _artifact(root, record, errors, label)
    if path is None:
        return None
    try:
        result = json.loads(path.read_bytes())
        if not isinstance(result, dict):
            raise ValueError("Evidence must be a JSON object")
        return result
    except (OSError, ValueError):
        errors.append(f"Invalid JSON artifact: {label}")
        return None


def _output_contract(operation: str, output: dict, case: dict) -> bool:
    from .evaluation import validate_evidence_output, validate_match_output

    allowed = set(case["invariants"]["approved_evidence_ids"])
    if not isinstance(output, dict):
        return False
    if operation == "enhanced_job_match":
        return validate_match_output(output).passed
    if operation == "resume_tailor":
        return validate_evidence_output(output, allowed).passed
    if operation == "interview_questions":
        rows = output.get("questions")
        if not isinstance(rows, list) or len(rows) != 8:
            return False
        seen = set()
        for row in rows:
            if not isinstance(row, dict) or not isinstance(row.get("question"), str) or not row["question"].strip() or not row.get("answer"):
                return False
            normalized = " ".join(re.findall(r"\w+", row["question"].casefold()))
            if normalized in seen or not set(row.get("evidence_ids", [])).issubset(allowed):
                return False
            seen.add(normalized)
        return True
    if operation == "bullet_rewrite":
        edits = output.get("evidence_links")
        if not isinstance(edits, list) or not edits:
            return False
        return all(isinstance(row, dict) and row.get("original_text") in case["resume"]["raw_text"]
                   and isinstance(row.get("replacement_text"), str) and row["replacement_text"].strip()
                   and row.get("evidence_ids") and set(row["evidence_ids"]).issubset(allowed) for row in edits)
    if operation == "enhanced_qa":
        return bool(output.get("answer")) and output.get("confidence") in {"high", "medium", "low"} and set(output.get("sources", [])).issubset(allowed | {"job", "match"})
    if operation == "learning_strategy":
        return all(output.get(name) for name in ("readiness_summary", "learning_priorities", "project_recommendations", "timeline"))
    if operation == "resume_enrichment":
        return isinstance(output.get("skills"), list) and all(isinstance(skill, str) and skill.strip() for skill in output["skills"])
    return False


def write_templates(root: Path) -> None:
    manifest, protocol, cases = load_packet(root)
    _write_json(root / "approval.template.json", {
        "state": "awaiting_independent_human_approval", "manifest_sha256": file_digest(root / "manifest.json"),
        "protocol_sha256": manifest["protocol_sha256"], "approved_by": None, "approved_at": None,
        "rubric": {"path": "rubric.md", "sha256": file_digest(root / "rubric.md")}, "reference_registry_sha256": None,
        "statistical_method": None,
        "reviewers": {"reviewer_1": None, "reviewer_2": None, "adjudicator": None},
        "statistical_design": {"path": "", "sha256": ""}})
    _write_json(root / "bundle.template.json", {"schema_version": manifest["schema_version"], "operation": None,
        "manifest_sha256": file_digest(root / "manifest.json"), "protocol_sha256": manifest["protocol_sha256"],
        "approval": {"path": "", "sha256": ""}, "reference_registry": {"path": "", "sha256": ""},
        "reviews": {"path": "", "sha256": ""}, "adjudications": {"path": "", "sha256": ""},
        "blinding": {"path": "", "sha256": ""}, "statistical_analysis": {"path": "", "sha256": ""},
        "cases": [{"case_id": item["case_id"], "input_sha256": digest(canonical(item)),
                   "input_artifact": {"path": "", "sha256": ""}, "baseline": None, "candidate": None}
                  for item in cases]})
    _write_json(root / "reference_registry.template.json", {"state": "unapproved", "route_id": None,
        "prompt_version": None, "model_version": None, "rubric_version": None, "outputs": []})
    _write_json(root / "statistical_analysis.template.json", {"state": "not_computed", "method": None,
        "primary_method_reference": None, "protocol_sha256": manifest["protocol_sha256"],
        "reviews_sha256": None, "adjudications_sha256": None, "confidence_level": 0.95,
        "sidedness": "one_sided", "difference": "candidate_minus_reference", "noninferiority_margin": 0,
        "held_out_case_ids": [item["case_id"] for item in cases if item["split"] == "held_out"],
        "analysis_code": {"path": "", "sha256": ""}, "powered_design_review": {"path": "", "sha256": ""},
        "analyst_id": None, "cohort_results": []})
    with (root / "reviews.template.csv").open("w", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=REVIEW_FIELDS, lineterminator="\n")
        writer.writeheader()
        for item in cases:
            for reviewer in ("reviewer_1", "reviewer_2"):
                for label in ("A", "B"):
                    writer.writerow({"case_id": item["case_id"], "reviewer_id": reviewer, "label": label})
    with (root / "adjudications.template.csv").open("w", newline="") as file:
        csv.DictWriter(file, fieldnames=ADJUDICATION_FIELDS, lineterminator="\n").writeheader()


def _native_receipt(root, record, *, source_sha, target_sha, errors, label):
    receipt = _json_artifact(root, record, errors, label)
    if not isinstance(receipt, dict):
        return
    required = ("structure_pass", "render_pass", "reading_order_pass", "links_pass", "format_fidelity_pass", "immutable_source_pass")
    if (receipt.get("source_sha256") != source_sha or receipt.get("artifact_sha256") != target_sha
            or any(receipt.get(key) is not True for key in required)):
        errors.append(f"Native validation incomplete: {label}")
    _artifact(root, receipt.get("extracted_text", {}), errors, label + ":text")
    pages = receipt.get("rendered_pages", [])
    if not isinstance(pages, list) or not pages:
        errors.append(f"Rendered pages missing: {label}")
        return
    hashes = []
    for index, page in enumerate(pages):
        path = _artifact(root, page, errors, f"{label}:page:{index}")
        if path and not path.read_bytes().startswith(b"\x89PNG\r\n\x1a\n"):
            errors.append(f"Rendered artifact is not PNG: {label}:page:{index}")
        hashes.append(page.get("sha256"))
    review = receipt.get("visual_review", {})
    if (not review.get("reviewer_id") or not review.get("reviewed_at")
            or review.get("reviewed_page_hashes") != hashes):
        errors.append(f"Every rendered page needs a bound visual-review record: {label}")


def _runner_receipt(root, record, *, input_sha, output_sha, errors, label, allow_calibration=False):
    receipt = _json_artifact(root, record, errors, label)
    if not isinstance(receipt, dict):
        return
    legacy = receipt.get("schema_version") == "legacy-model-liability-v1"
    required = ("run_id", "provider", "model", "prompt_version", "usage_provenance")
    if (receipt.get("state") != "completed" or receipt.get("input_sha256") != input_sha
            or receipt.get("output_sha256") != output_sha
            or any(not receipt.get(key) for key in required)
            or (not legacy and not receipt.get("execution_hold_id"))):
        errors.append(f"Funded durable runner receipt incomplete: {label}")
    if legacy:
        _legacy_calibration_receipt(root, receipt, errors, label)
        if not allow_calibration:
            errors.append(f"Internal calibration receipt cannot qualify a retail route: {label}")
    else:
        _artifact(root, receipt.get("funding_authorization", {}), errors, label + ":funding")
        _artifact(root, receipt.get("expense_ledger_export", {}), errors, label + ":expense")


def _legacy_calibration_receipt(root, receipt, errors, label):
    """Import genuine pre-unified liabilities without fabricating an execution hold.

    This supports development output review only. It cannot qualify retail routes:
    it has no unified money hold or per-service unit-economics evidence.
    """
    from ...services.model_cost_policy import quoted_cost

    auth = _json_artifact(root, receipt.get("funding_authorization", {}), errors, label + ":funding")
    ledger = _json_artifact(root, receipt.get("expense_ledger_export", {}), errors, label + ":expense")
    if not isinstance(auth, dict) or not isinstance(ledger, dict):
        return
    try:
        event, = ledger["events"]
        liability, = ledger["liabilities"]
        route = auth["routes"][ledger["side"]]
        for field in ("funding_evidence", "provider_balance_evidence"):
            _artifact(root, auth[field], errors, label + ":" + field)
        if (auth["state"] != "authorized" or auth["purpose"] != "internal_calibration_not_sales_qualification"
                or receipt.get("calibration_only") is not True or receipt.get("execution_hold_id")
                or ledger["schema_version"] != "legacy-model-liability-v1"
                or ledger["authorization_sha256"] != digest(canonical(auth))
                or ledger["input_sha256"] != receipt["input_sha256"]
                or ledger["run_id"] != receipt["run_id"] or event["analysis_run_id"] != ledger["run_id"]
                or not receipt["financial_group_id"] or ledger["financial_group_id"] != receipt["financial_group_id"]
                or liability["financial_group_id"] != receipt["financial_group_id"]
                or event["liability_id"] != liability["id"] or receipt["liability_ids"] != [liability["id"]]
                or event["status"] != "succeeded" or event["tokens_estimated"] is not False
                or event["prompt_version"] != receipt["prompt_version"] or route["prompt_version"] != receipt["prompt_version"]
                or event["attempt_number"] != 1 or liability["attempt_number"] != 1
                or liability["currency"] != "USD"
                or any(event["pricing_quote"].get(key) != value for key, value in route["price"].items())
                or any(liability["pricing_quote"].get(key) != event["pricing_quote"].get(key)
                       for key in ("version", "input_rate_micros_per_million", "output_rate_micros_per_million",
                                   "max_input_tokens", "max_output_tokens", "output_limit_parameter", "token_estimator"))):
            raise ValueError("Expense provenance mismatch")
        for field in ("provider", "model", "usage_provenance"):
            if event[field] != receipt[field] or liability[field] != receipt[field]:
                raise ValueError("Provider provenance mismatch")
        for field in ("input_tokens", "output_tokens", "reserved_cost_micros", "settled_cost_micros"):
            if type(event[field]) is not int or event[field] < 0 or event[field] != liability[field]:
                raise ValueError("Actual usage required")
        if (event["cost_state"] != "settled" or liability["cost_state"] != "settled"
                or event["settled_cost_micros"] > event["reserved_cost_micros"]
                or event["settled_cost_micros"] != quoted_cost(liability["pricing_quote"], event["input_tokens"], event["output_tokens"])):
            raise ValueError("Unsettled or inconsistent provider cost")
    except (KeyError, ValueError, TypeError):
        errors.append(f"Legacy calibration liability receipt incomplete: {label}")


def _reviews(root, bundle, blinding, approval, cases, errors):
    scored: dict[tuple[str, str, str], int] = {}
    path = _artifact(root, bundle.get("reviews", {}), errors, "two human reviews")
    adjudication_path = _artifact(root, bundle.get("adjudications", {}), errors, "adjudications")
    if path is None or adjudication_path is None:
        return scored
    assigned = approval.get("reviewers", {})
    identities = [assigned.get(role) for role in ("reviewer_1", "reviewer_2", "adjudicator")]
    if any(not isinstance(value, str) or not value.strip() for value in identities) or len(set(identities)) != 3:
        errors.append("Two independent reviewers and a distinct adjudicator must be assigned")
        return scored
    rows = list(csv.DictReader(io.StringIO(path.read_text())))
    adjudications = list(csv.DictReader(io.StringIO(adjudication_path.read_text())))
    indexed = defaultdict(list)
    for row in rows:
        indexed[(row.get("case_id"), row.get("label"))].append(row)
    expected = {(item["case_id"], label) for item in cases for label in ("A", "B")}
    if set(indexed) != expected:
        errors.append("Human review coverage differs from frozen case/label population")
    for identity, label in expected:
        values = indexed[(identity, label)]
        if len(values) != 2 or {row.get("reviewer_id") for row in values} != set(identities[:2]):
            errors.append(f"Both independent reviewers required: {identity}:{label}")
            continue
        pair_id = blinding.get("pairs", {}).get(identity, {}).get("pair_id")
        if not pair_id or any(row.get("pair_id") != pair_id or not row.get("notes", "").strip() for row in values):
            errors.append(f"Review not bound to blinded pair or missing rationale: {identity}:{label}")
        for field in CRITICAL:
            if any(row.get(field) not in {"false", "true"} for row in values):
                errors.append(f"Critical-defect judgment missing: {identity}:{label}:{field}")
            if any(row.get(field) == "true" for row in values):
                errors.append(f"Critical defect blocks route pending independent resolution: {identity}:{label}:{field}")
        for metric in METRICS:
            scores = [row.get(metric, "") for row in values]
            if any(value not in {"1", "2", "3", "4", "5"} for value in scores):
                errors.append(f"Human score missing: {identity}:{label}:{metric}")
                continue
            if scores[0] != scores[1]:
                decisions = [row for row in adjudications if row.get("case_id") == identity and row.get("label") == label and row.get("metric") == metric]
                if (len(decisions) != 1 or decisions[0].get("adjudicator_id") != identities[2]
                        or decisions[0].get("pair_id") != pair_id or not decisions[0].get("reason", "").strip()
                        or decisions[0].get("resolved_score") not in {"1", "2", "3", "4", "5"}):
                    errors.append(f"Independent adjudication missing: {identity}:{label}:{metric}")
                else:
                    scored[(identity, label, metric)] = int(decisions[0]["resolved_score"])
            else:
                scored[(identity, label, metric)] = int(scores[0])

    return scored

def _statistics(root, bundle, protocol, manifest, cases, approval, blinding, scores, errors):
    result = _json_artifact(root, bundle.get("statistical_analysis", {}), errors, "external statistical analysis")
    if not isinstance(result, dict):
        return
    held = [item for item in cases if item["split"] == "held_out"]
    if (result.get("state") != "complete" or result.get("protocol_sha256") != manifest["protocol_sha256"]
            or result.get("reviews_sha256") != bundle.get("reviews", {}).get("sha256")
            or result.get("adjudications_sha256") != bundle.get("adjudications", {}).get("sha256")
            or sorted(result.get("held_out_case_ids", [])) != sorted(item["case_id"] for item in held)
            or result.get("confidence_level") != 0.95 or result.get("sidedness") != "one_sided"
            or result.get("difference") != "candidate_minus_reference" or result.get("noninferiority_margin") != 0):
        errors.append("Statistical evidence must bind held-out reviews and the frozen zero-degradation 95% design")
    if not result.get("method") or not result.get("analyst_id") or not str(result.get("primary_method_reference", "")).startswith("https://"):
        errors.append("Statistical method, responsible analyst and primary method reference required")
    if result.get("method") != approval.get("statistical_method"):
        errors.append("Statistical method differs from independently approved design")
    _artifact(root, result.get("analysis_code", {}), errors, "statistical analysis code")
    _artifact(root, result.get("powered_design_review", {}), errors, "power/design review")
    if result.get("powered_design_review") != approval.get("statistical_design"):
        errors.append("The analysis must use the independently approved powered design")
    counts = coverage(held)["all"]
    expected = {(cohort, metric) for cohort in protocol["important_cohorts"] for metric in METRICS}
    records = result.get("cohort_results", [])
    found = {(row.get("cohort"), row.get("metric")) for row in records if isinstance(row, dict)}
    if found != expected or len(records) != len(expected):
        errors.append("Every prespecified important cohort and metric needs a paired analysis; pooled-only results are insufficient")
    for row in records:
        if not isinstance(row, dict):
            errors.append("Invalid cohort result record")
            continue
        estimate, bound = row.get("paired_difference"), row.get("lower_confidence_bound")
        if (isinstance(estimate, bool) or isinstance(bound, bool)
                or not isinstance(estimate, (int, float)) or not isinstance(bound, (int, float))):
            errors.append("Statistical bounds must be finite numeric values")
            continue
        finite = math.isfinite(estimate) and math.isfinite(bound)
        if (not finite or bound < 0 or bound > estimate or not -4 <= estimate <= 4
                or row.get("paired_n") != counts.get(row.get("cohort"))
                or row.get("paired_n", 0) < protocol["minimum_held_out_per_cohort"]):
            errors.append(f"Inconclusive or incomplete paired quality evidence: {row.get('cohort')}:{row.get('metric')}")
        differences = []
        for item in held:
            if row.get("cohort") not in item["cohorts"]:
                continue
            mapping = blinding.get("pairs", {}).get(item["case_id"], {})
            labels = {mapping.get(label): label for label in ("A", "B")}
            baseline = scores.get((item["case_id"], labels.get("baseline"), row.get("metric")))
            candidate = scores.get((item["case_id"], labels.get("candidate"), row.get("metric")))
            if baseline is not None and candidate is not None:
                differences.append(candidate - baseline)
        if (not differences or len(differences) != counts.get(row.get("cohort")) or not finite
                or not math.isclose(estimate, sum(differences) / len(differences), abs_tol=1e-9, rel_tol=0)):
            errors.append(f"Reported paired mean differs from adjudicated human scores: {row.get('cohort')}:{row.get('metric')}")


def evaluate_evidence(packet_root: Path, evidence_root: Path) -> dict:
    report = validate_packet(packet_root)
    errors = list(report.get("errors", []))
    if not report["draft_inputs_valid"]:
        return report | {"evidence_complete": False}
    if report["schema_version"] == SUPPORTED_VERSION:
        from .quality_supported_v2 import verify_lineage

        if not verify_lineage(packet_root.parent / "unified_credit_v1", packet_root)["source_lineage_valid"]:
            return report | {"evidence_complete": False, "authorizes_release": False,
                             "errors": ["Supported packet differs from its frozen v1 source lineage"]}
    manifest, protocol, cases = load_packet(packet_root)
    try:
        bundle = json.loads((evidence_root / "bundle.json").read_bytes())
        if not isinstance(bundle, dict):
            raise ValueError("Evidence bundle must be an object")
    except (OSError, ValueError):
        return report | {"evidence_complete": False, "errors": ["No imported evidence bundle. Draft inputs are not approved reference outputs, completed native tests, human judgments or statistical evidence."]}
    if bundle.get("manifest_sha256") != file_digest(packet_root / "manifest.json") or bundle.get("protocol_sha256") != manifest["protocol_sha256"]:
        errors.append("Evidence bundle differs from frozen input manifest/protocol")
    approval = _json_artifact(evidence_root, bundle.get("approval", {}), errors, "independent baseline/protocol approval") or {}
    registry = _json_artifact(evidence_root, bundle.get("reference_registry", {}), errors, "approved reference outputs") or {}
    blinding = _json_artifact(evidence_root, bundle.get("blinding", {}), errors, "coordinator blinding map") or {}
    if (approval.get("state") != "approved" or not approval.get("approved_by") or not approval.get("approved_at")
            or approval.get("manifest_sha256") != bundle.get("manifest_sha256")
            or approval.get("protocol_sha256") != manifest["protocol_sha256"]
            or approval.get("reference_registry_sha256") != bundle.get("reference_registry", {}).get("sha256")):
        errors.append("Independent approval of frozen inputs, references and protocol is incomplete")
    rubric_path = _artifact(evidence_root, approval.get("rubric", {}), errors, "frozen scoring rubric")
    if rubric_path and file_digest(rubric_path) != file_digest(packet_root / "rubric.md"):
        errors.append("Reviewed rubric differs from packet rubric")
    if registry.get("state") != "approved" or any(not registry.get(key) for key in ("route_id", "prompt_version", "model_version", "rubric_version")):
        errors.append("Reference model, prompt, rubric and route have not been approved")
    if blinding.get("reviewer_blinding_checked") is not True:
        errors.append("A coordinator must inspect reviewer copies for provider/route identity leakage")
    references = {row.get("case_id"): row for row in registry.get("outputs", []) if isinstance(row, dict)}
    records = bundle.get("cases", [])
    indexed = {row.get("case_id"): row for row in records if isinstance(row, dict)}
    if len(records) != len(cases) or set(indexed) != {item["case_id"] for item in cases}:
        errors.append("Every frozen pair needs actual imported reference and candidate outputs")
    checked = 0
    for case in cases:
        identity = case["case_id"]
        row = indexed.get(identity, {})
        mapping = blinding.get("pairs", {}).get(identity, {})
        if {mapping.get("A"), mapping.get("B")} != {"baseline", "candidate"} or any(
            mapping.get(label + "_source_sha256") != (row.get(mapping.get(label)) or {}).get("output", {}).get("sha256") for label in ("A", "B")
        ):
            errors.append(f"Blinded labels do not bind imported output hashes: {identity}")
        expected = digest(canonical(case))
        if row.get("input_sha256") != expected:
            errors.append(f"Imported source context differs from frozen pair: {identity}")
        original = _artifact(evidence_root, row.get("input_artifact", {}), errors, identity + ":native source")
        if original and not _native(original, case["source_format"]):
            errors.append(f"Native source signature/structure does not match declared format: {identity}")
        _native_receipt(evidence_root, row.get("input_validation", {}), source_sha=row.get("input_artifact", {}).get("sha256"),
                        target_sha=row.get("input_artifact", {}).get("sha256"), errors=errors, label=identity + ":input validation")
        for side in ("baseline", "candidate"):
            outcome = row.get(side) or {}
            output = _json_artifact(evidence_root, outcome.get("output", {}), errors, identity + ":" + side)
            if output is not None:
                try:
                    valid = _output_contract(bundle.get("operation", ""), output, case)
                except (ValueError, KeyError, TypeError):
                    valid = False
                if not valid:
                    errors.append(f"Deterministic output contract failed: {identity}:{side}")
            _runner_receipt(evidence_root, outcome.get("runner_receipt", {}), input_sha=expected,
                            output_sha=outcome.get("output", {}).get("sha256"), errors=errors, label=identity + ":" + side + ":runner")
            if side == "baseline" and references.get(identity, {}).get("sha256") != outcome.get("output", {}).get("sha256"):
                errors.append(f"Reference output differs from approved registry: {identity}")
            if bundle.get("operation") == "resume_tailor":
                native = outcome.get("native_artifact", {})
                path = _artifact(evidence_root, native, errors, identity + ":" + side + ":native")
                if path and not _native(path, case["source_format"]):
                    errors.append(f"Tailoring substituted the source format: {identity}:{side}")
                if native.get("sha256") == row.get("input_artifact", {}).get("sha256"):
                    errors.append(f"Unchanged snapshot cannot be a successful tailoring result: {identity}:{side}")
                if outcome.get("reviewed_artifact_sha256") != native.get("sha256"):
                    errors.append(f"Downloaded artifact differs from reviewed artifact: {identity}:{side}")
                _native_receipt(evidence_root, outcome.get("native_validation", {}), source_sha=row.get("input_artifact", {}).get("sha256"),
                                target_sha=native.get("sha256"), errors=errors, label=identity + ":" + side + ":native validation")
        checked += 1
    scores = _reviews(evidence_root, bundle, blinding, approval, cases, errors)
    _statistics(evidence_root, bundle, protocol, manifest, cases, approval, blinding, scores, errors)
    return report | {"evidence_complete": not errors, "imported_pairs_checked": checked,
                     "quality_status": "independent_release_review_required" if not errors else "incomplete_evidence",
                     "authorizes_release": False, "error_count": len(errors), "errors": errors[:100],
                     "limitations": ["Consistency checks do not authenticate a human identity, a supplier invoice or the statistical method.",
                                     "No model noninferiority or native layout quality is claimed by this tool."]}


def make_blinded(packet_root: Path, evidence_root: Path, destination: Path) -> dict:
    """Separate coordinator identity mapping from reviewer output copies.

    This is a packaging operation, not a claim that the artifacts contain no
    identity leaks. The coordinator must inspect copies before distributing.
    """
    if destination.exists():
        raise ValueError("Use a new destination; never overwrite distributed review packets")
    manifest, _, cases = load_packet(packet_root)
    bundle = json.loads((evidence_root / "bundle.json").read_bytes())
    if bundle.get("manifest_sha256") != file_digest(packet_root / "manifest.json"):
        raise ValueError("Imported outputs do not bind this frozen packet")
    indexed = {row["case_id"]: row for row in bundle["cases"]}
    errors: list[str] = []
    prepared: list[tuple[dict[str, Any], dict[str, Path | None], Path | None, dict[str, Path | None]]] = []
    for case in cases:
        record = indexed.get(case["case_id"], {})
        paths = {side: _artifact(evidence_root, (record.get(side) or {}).get("output", {}), errors, case["case_id"] + ":" + side) for side in ("baseline", "candidate")}
        source = _artifact(evidence_root, record.get("input_artifact", {}), errors, case["case_id"] + ":source")
        native = {side: _artifact(evidence_root, (record.get(side) or {}).get("native_artifact", {}), errors, case["case_id"] + ":native:" + side) for side in ("baseline", "candidate")} if bundle.get("operation") == "resume_tailor" else {}
        prepared.append((case, paths, source, native))
    if errors:
        raise ValueError("Actual hashed outputs and source artifacts are required before blinding")
    reviewer = destination / "reviewer"
    coordinator = destination / "coordinator"
    reviewer.mkdir(parents=True)
    coordinator.mkdir(mode=0o700)
    mappings, assignments = {}, []
    randomizer = random.Random(secrets.randbits(256))
    for case, paths, source, native in prepared:
        identity = case["case_id"]
        pair = "pair-" + secrets.token_hex(8)
        sides = ["baseline", "candidate"]
        randomizer.shuffle(sides)
        mappings[identity] = {"pair_id": pair, "A": sides[0], "B": sides[1]}
        source_name = f"{pair}-source.{case['source_format']}"
        assert source is not None
        shutil.copyfile(source, reviewer / source_name)
        native_outputs = {}
        for label, side in zip(("A", "B"), sides, strict=True):
            source_output = paths[side]
            assert source_output is not None
            value = json.loads(source_output.read_bytes())
            # Only operational envelope fields are removed from presentation;
            # original bytes remain untouched and bound in the coordinator map.
            presentation = {key: data for key, data in value.items() if key not in {"credit_execution", "run_id", "provider", "model", "model_cost_quote"}}
            name = f"{pair}-{label}.json"
            _write_json(reviewer / name, presentation)
            mappings[identity][label + "_source_sha256"] = file_digest(source_output)
            mappings[identity][label + "_presentation_sha256"] = file_digest(reviewer / name)
            native_path = native.get(side)
            if native_path is not None:
                native_name = f"{pair}-{label}.{case['source_format']}"
                shutil.copyfile(native_path, reviewer / native_name)
                native_outputs[label] = native_name
                mappings[identity][label + "_native_sha256"] = file_digest(native_path)
        assignments.append({"case_id": identity, "pair_id": pair, "source": source_name, "job": case["job"], "labels": ["A", "B"], "native_outputs": native_outputs})
    _write_json(reviewer / "assignments.json", assignments)
    _write_json(coordinator / "blinding.json", {"manifest_sha256": file_digest(packet_root / "manifest.json"),
        "corpus_sha256": manifest["corpus_sha256"], "reviewer_blinding_checked": False, "pairs": mappings})
    shutil.copyfile(packet_root / "rubric.md", reviewer / "rubric.md")
    for role in ("reviewer_1", "reviewer_2"):
        with (reviewer / f"{role}.csv").open("w", newline="") as file:
            writer = csv.DictWriter(file, fieldnames=REVIEW_FIELDS, lineterminator="\n")
            writer.writeheader()
            for item in assignments:
                for label in ("A", "B"):
                    writer.writerow({"case_id": item["case_id"], "pair_id": item["pair_id"], "reviewer_id": "", "label": label})
    return {"pairs_packaged": len(assignments), "scores_generated": 0, "reviewer_blinding_checked": False,
            "coordinator_map": str(coordinator / "blinding.json"), "reviewer_directory": str(reviewer)}
