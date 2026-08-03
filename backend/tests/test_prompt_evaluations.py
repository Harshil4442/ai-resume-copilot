from __future__ import annotations

from pathlib import Path

from backend.app.domains.analysis.evaluation import (
    evaluate_golden_cases,
    validate_evidence_output,
)


def test_golden_prompt_contracts_pass():
    path = Path(__file__).resolve().parents[1] / "evals" / "golden_cases.json"
    result = evaluate_golden_cases(path)
    assert result == {"passed": True, "case_count": 4, "failures": []}


def test_evidence_contract_rejects_claim_without_citation():
    result = validate_evidence_output(
        {"summary_items": [{"text": "Unsupported", "evidence_ids": []}], "bullets": []},
        {"ev_1"},
    )
    assert result.passed is False
    assert "must cite evidence" in " ".join(result.errors)
