from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class EvaluationResult:
    passed: bool
    errors: tuple[str, ...]


def validate_match_output(output: Any) -> EvaluationResult:
    errors: list[str] = []
    if not isinstance(output, dict):
        return EvaluationResult(False, ("output must be an object",))
    score = output.get("match_score")
    if not isinstance(score, (int, float)) or isinstance(score, bool) or not 0 <= score <= 100:
        errors.append("match_score must be between 0 and 100")
    for name in ("required_skills", "true_gaps"):
        value = output.get(name)
        if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
            errors.append(f"{name} must be a string list")
    if not isinstance(output.get("fit_summary"), str) or not output.get("fit_summary", "").strip():
        errors.append("fit_summary must be non-empty text")
    return EvaluationResult(not errors, tuple(errors))


def validate_evidence_output(output: Any, allowed_evidence_ids: set[str]) -> EvaluationResult:
    errors: list[str] = []
    if not isinstance(output, dict):
        return EvaluationResult(False, ("output must be an object",))
    sourced_count = 0
    for collection in ("summary_items", "bullets"):
        items = output.get(collection)
        if not isinstance(items, list):
            errors.append(f"{collection} must be a list")
            continue
        for index, item in enumerate(items):
            if not isinstance(item, dict) or not str(item.get("text") or "").strip():
                errors.append(f"{collection}[{index}] must contain text")
                continue
            evidence_ids = item.get("evidence_ids")
            if not isinstance(evidence_ids, list) or not evidence_ids:
                errors.append(f"{collection}[{index}] must cite evidence")
                continue
            unsupported = {str(value) for value in evidence_ids} - allowed_evidence_ids
            if unsupported:
                errors.append(f"{collection}[{index}] cites unsupported evidence")
            else:
                sourced_count += 1
    if sourced_count == 0:
        errors.append("at least one supported claim is required")
    return EvaluationResult(not errors, tuple(errors))


def evaluate_golden_cases(path: Path) -> dict[str, Any]:
    cases = json.loads(path.read_text(encoding="utf-8"))
    failures: list[dict[str, Any]] = []
    for case in cases:
        operation = case["operation"]
        if operation == "job_match":
            result = validate_match_output(case["output"])
        elif operation == "resume_tailor":
            result = validate_evidence_output(
                case["output"],
                set(case.get("allowed_evidence_ids", [])),
            )
        else:
            result = EvaluationResult(False, (f"unsupported operation {operation}",))
        if result.passed is not bool(case["expected_pass"]):
            failures.append({"name": case["name"], "errors": result.errors})
    return {"passed": not failures, "case_count": len(cases), "failures": failures}
