"""Synthetic authorization/native review fixtures, never qualification evidence."""
from __future__ import annotations

import copy
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
import reportlab

from app.domains.analysis import development_runner as runner
from app.domains.analysis import quality_materialization as native
from app.domains.analysis import quality_packet as packet
from app.services import interview_contract, llm_client, model_cost_policy

ROOT = Path(__file__).resolve().parents[1] / "evals/unified_credit_v1"


def write_json(root, name, value):
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(packet.canonical(value))
    return {"path": name, "sha256": packet.file_digest(path)}


@pytest.fixture
def calibration(tmp_path, monkeypatch):
    from backend.tests.model_cost_fixtures import synthetic_policy

    policy = synthetic_policy()
    for quote in policy["pricing_quotes"]:
        quote["max_input_tokens"] = 65536
    monkeypatch.setenv("LLM_MODEL_COST_POLICY_JSON", json.dumps(policy))
    monkeypatch.setenv("HIREWIZ_INTERNAL_EVALUATION_ENABLED", "true")
    monkeypatch.setenv("OPTIONAL_AI_GENERATION_ENABLED", "true")
    monkeypatch.setattr(llm_client, "LLM_MODEL", "gemini-3.6-flash")
    monkeypatch.setattr(llm_client, "LLM_API_BASE", "https://generativelanguage.googleapis.com")
    monkeypatch.setattr(llm_client, "_api_key", lambda: "synthetic-never-network")
    source_dir = tmp_path / "native"
    source_dir.mkdir()
    evidence_dir = tmp_path / "evidence"
    evidence_dir.mkdir()
    rows, receipts = [], {}
    for case in runner._scope(ROOT):
        identity = case["case_id"]
        # Test-only font substitution and synthetic reviewer assertions exercise
        # receipt boundaries. These files are not the actual materialized corpus.
        fixture = copy.deepcopy(case)
        fixture["source_recipe"]["font"] = "Bitstream Vera Sans"
        pdf = native._pdf(fixture, {"Bitstream Vera Sans": Path(reportlab.__file__).parent / "fonts/Vera.ttf"})
        path = source_dir / f"{identity}.pdf"
        path.write_bytes(pdf)
        sha = packet.file_digest(path)
        rows.append({"case_id": identity, "input_sha256": runner.digest(case),
                     "input_artifact": {"path": path.name, "sha256": sha}})
        text_path, png = evidence_dir / f"{identity}.txt", evidence_dir / f"{identity}.png"
        text_path.write_text(case["resume"]["raw_text"])
        png.write_bytes(b"\x89PNG\r\n\x1a\nSYNTHETIC-TEST-ONLY")
        png_record = {"path": png.name, "sha256": packet.file_digest(png)}
        receipts[identity] = write_json(evidence_dir, f"{identity}.json", {
            "source_sha256": sha, "artifact_sha256": sha,
            **{key: True for key in ("structure_pass", "render_pass", "reading_order_pass", "links_pass", "format_fidelity_pass", "immutable_source_pass")},
            "extracted_text": {"path": text_path.name, "sha256": packet.file_digest(text_path)},
            "rendered_pages": [png_record], "visual_review": {"reviewer_id": "synthetic-test-only",
                "reviewed_at": datetime.now(UTC).isoformat(), "reviewed_page_hashes": [png_record["sha256"]]}})
    write_json(source_dir, "materialization-manifest.json", {
        "packet_manifest_sha256": runner.MANIFEST_SHA, "corpus_sha256": runner.CORPUS_SHA, "cases": rows})
    write_json(evidence_dir, "native-map.json", receipts)
    write_json(evidence_dir, "funding.json", {"purpose": "synthetic-test-not-an-invoice"})
    write_json(evidence_dir, "balance.json", {"purpose": "synthetic-test-not-an-account-observation"})
    price = model_cost_policy.current_policy().pricing_quotes[0]
    now = datetime.now(UTC)
    route = {"prompt_version": interview_contract.PROMPT_VERSION, "contract_sha256": runner.contract_digest(), "price": price.model_dump(mode="json")}
    auth = runner.Authorization.model_validate({
        "schema_version": runner.VERSION, "experiment_id": "synthetic-" + uuid4().hex,
        "purpose": "internal_calibration_not_sales_qualification", "state": "authorized", "authorized_by": "synthetic-test-only",
        "valid_from": now - timedelta(minutes=1), "expires_at": now + timedelta(hours=1), "provider_balance_checked_at": now,
        "funding_evidence": {"path": "funding.json", "sha256": packet.file_digest(evidence_dir / "funding.json")},
        "provider_balance_evidence": {"path": "balance.json", "sha256": packet.file_digest(evidence_dir / "balance.json")},
        "maximum_provider_cost_micros": 2_000_000,
        "model_policy_sha256": runner.digest(model_cost_policy.current_policy().model_dump(mode="json")),
        "expense_policy_sha256": runner.digest(runner.cost_policy.current_policy().model_dump(mode="json")),
        "native_manifest_sha256": packet.file_digest(source_dir / "materialization-manifest.json"),
        "native_receipts_sha256": packet.file_digest(evidence_dir / "native-map.json"),
        "routes": {"reference": route, "candidate": route},
    })
    return {"packet_root": ROOT, "native_root": source_dir, "authorization": auth,
            "authorization_root": evidence_dir, "native_receipts": evidence_dir / "native-map.json"}


def test_dry_run_complete_twenty_bounded_attempts_without_db(calibration):
    result = runner.preflight(**calibration)
    assert result["state"] == "ready_for_internal_calibration"
    assert result["provider_calls"] == 0 and result["maximum_attempts"] == 20
    assert len({row["case_id"] for row in result["runs"]}) == 10
    assert result["maximum_provider_cost_micros"] == 20 * (65536 + 1024)
    assert not result["authorizes_sales"]
    runner.preflight(**calibration, active=True)


def test_missing_or_changed_native_receipts_do_not_admit(calibration):
    values = {**calibration, "native_receipts": None}
    assert runner.preflight(**values)["state"] == "blocked"
    with pytest.raises(ValueError, match="validation is incomplete"):
        runner.preflight(**values, active=True)
    calibration["native_receipts"].write_text("{}")
    with pytest.raises(ValueError, match="records changed"):
        runner.preflight(**calibration)


def test_exact_frozen_scope_and_native_hash_are_not_overridable(calibration, tmp_path):
    path = tmp_path / "custom"
    path.mkdir()
    (path / "manifest.json").write_text("{}")
    with pytest.raises(ValueError, match="original frozen"):
        runner.preflight(**{**calibration, "packet_root": path})
    pdf = next(calibration["native_root"].glob("*.pdf"))
    pdf.write_bytes(b"%PDF-arbitrary-customer-resume")
    with pytest.raises(ValueError, match="artifact"):
        runner.preflight(**calibration)


def test_allocation_expiry_flag_and_policy_fail_closed(calibration, monkeypatch):
    auth = calibration["authorization"]
    with pytest.raises(ValueError, match="twenty-attempt"):
        runner.preflight(**{**calibration, "authorization": auth.model_copy(update={"maximum_provider_cost_micros": 1})})
    with pytest.raises(ValueError, match="current explicit"):
        runner.preflight(**{**calibration, "authorization": auth.model_copy(update={"state": "draft"})}, active=True)
    monkeypatch.setenv("HIREWIZ_INTERNAL_EVALUATION_ENABLED", "false")
    with pytest.raises(ValueError, match="explicitly enabled"):
        runner.preflight(**calibration, active=True)
    monkeypatch.setenv("HIREWIZ_INTERNAL_EVALUATION_ENABLED", "true")
    monkeypatch.setenv("APP_ENV", "production")
    with pytest.raises(ValueError, match="production"):
        runner.preflight(**calibration, active=True)


def test_remote_and_public_database_urls_are_refused(calibration):
    for url in ("sqlite://", "postgresql+psycopg://localhost/production",
                "postgresql+psycopg://127.0.0.1:55434/hirewiz_admission_test?options=public"):
        with pytest.raises(ValueError, match="dedicated local"):
            runner.local_factory(url, calibration["authorization"])


def test_generated_authorization_schema_preserves_historical_readability():
    schema = json.loads((ROOT / "development-authorization.schema.json").read_bytes())
    assert schema == runner.Authorization.model_json_schema()
    assert schema["$defs"]["Route"]["properties"]["prompt_version"]["enum"] == [
        "interview-evidence-v5", "interview-evidence-v6", "interview-evidence-v7", "interview-evidence-v8", "interview-evidence-v9"]


@pytest.mark.parametrize("prompt,source", [
    ("interview-evidence-v5", "old"), ("interview-evidence-v5", "current"),
    ("interview-evidence-v6", "old"), ("interview-evidence-v6", "current"),
    ("interview-evidence-v7", "old"), ("interview-evidence-v7", "current"), ("current", "old"),
])
@pytest.mark.parametrize("operation", ["preflight", "prepare", "execute"])
def test_historical_or_changed_contract_loads_but_cannot_start_work(calibration, monkeypatch, prompt, source, operation):
    data = calibration["authorization"].model_dump(mode="json")
    for route in data["routes"].values():
        route["prompt_version"] = interview_contract.PROMPT_VERSION if prompt == "current" else prompt
        route["contract_sha256"] = runner.contract_digest() if source == "current" else "a" * 64
    path = calibration["authorization_root"] / "historical-authorization.json"
    path.write_bytes(packet.canonical(data))
    auth = runner.load_authorization(path)
    assert auth.model_dump(mode="json") == data
    monkeypatch.setattr(runner, "_check_local", lambda *args: pytest.fail("Contract refusal must precede database access"))
    monkeypatch.setattr(llm_client, "generate_interview_questions", lambda **kwargs: pytest.fail("No provider call"))
    values = {**calibration, "authorization": auth}
    expected = "current interview prompt" if prompt != "current" else "implementation changed"
    with pytest.raises(ValueError, match=expected):
        if operation == "preflight":
            runner.preflight(**values)
        elif operation == "prepare":
            runner.prepare(None, **values)
        else:
            runner.execute(None, **values, side="candidate", maximum_operations=1)
