"""Synthetic local fixtures only: never native/provider/reviewer qualification."""
from __future__ import annotations

import copy
import json
import os
import subprocess
import sys
import zipfile
from datetime import UTC, datetime, timedelta
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
import reportlab

from app.domains.analysis import quality_materialization as materialize
from app.domains.analysis import quality_packet as packet
from app.domains.analysis import supported_evaluation as runner
from app.domains.analysis import supported_evaluation_plan as inputs
from app.services import interview_contract, llm_client, model_cost_policy
from tests.model_cost_fixtures import synthetic_policy

ROOT = Path(__file__).resolve().parents[1] / "evals/unified_credit_v2"


def write(root, name, value):
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(packet.canonical(value))
    return {"path": name, "sha256": packet.file_digest(path)}


@pytest.fixture
def evaluation(tmp_path, monkeypatch):
    from app.billing.cost_policy import current_policy

    policy = synthetic_policy()
    for quote in policy["pricing_quotes"]:
        quote["max_input_tokens"] = 65536
    monkeypatch.setenv("LLM_MODEL_COST_POLICY_JSON", json.dumps(policy))
    monkeypatch.setenv("HIREWIZ_INTERNAL_EVALUATION_ENABLED", "true")
    monkeypatch.setenv("OPTIONAL_AI_GENERATION_ENABLED", "true")
    monkeypatch.setattr(llm_client, "LLM_MODEL", "gemini-3.6-flash")
    monkeypatch.setattr(llm_client, "LLM_API_BASE", "https://generativelanguage.googleapis.com")
    monkeypatch.setattr(llm_client, "_api_key", lambda: "synthetic-never-network")
    source_root, evidence = tmp_path / "native", tmp_path / "evidence"
    source_root.mkdir()
    evidence.mkdir()
    rows, receipts, captures = [], {}, {}
    cases = inputs.selected_cases(ROOT, "development")[1]
    for case in cases:
        identity, kind = case["case_id"], case["source_format"]
        if kind == "pdf":
            fixture = copy.deepcopy(case)
            fixture["source_recipe"]["font"] = "Bitstream Vera Sans"
            content = materialize._pdf(fixture, {"Bitstream Vera Sans": Path(reportlab.__file__).parent / "fonts/Vera.ttf"})
        elif kind == "docx":
            content = materialize._docx(case)
        elif kind == "tex":
            content = materialize._tex(case)
        else:
            buffer = BytesIO()
            with zipfile.ZipFile(buffer, "w") as archive:
                archive.writestr("main.tex", materialize._tex(case))
            content = buffer.getvalue()
        path = source_root / (identity + "." + kind)
        path.write_bytes(content)
        sha = packet.file_digest(path)
        rows.append({"case_id": identity, "input_sha256": inputs.digest(case),
                     "input_artifact": {"path": path.name, "sha256": sha}})
        raw = case["resume"]["raw_text"]
        extracted, png = evidence / (identity + ".txt"), evidence / (identity + ".png")
        extracted.write_text(raw)
        png.write_bytes(b"\x89PNG\r\n\x1a\nSYNTHETIC-TEST-NOT-NATIVE-RENDER")
        page = {"path": png.name, "sha256": packet.file_digest(png)}
        receipts[identity] = write(evidence, identity + ".json", {
            "source_sha256": sha, "artifact_sha256": sha,
            **dict.fromkeys(("structure_pass", "render_pass", "reading_order_pass", "links_pass", "format_fidelity_pass", "immutable_source_pass"), True),
            "extracted_text": {"path": extracted.name, "sha256": packet.file_digest(extracted)},
            "rendered_pages": [page], "visual_review": {"reviewer_id": "synthetic-test-only", "reviewed_at": datetime.now(UTC).isoformat(), "reviewed_page_hashes": [page["sha256"]]}})
        response = write(evidence, identity + ".worker.json", {
            "version": 1, "sha256": sha, "size_bytes": len(content), "source_format": kind,
            "worker_image": "us-central1-docker.pkg.dev/ai-resume-parser-482412/cloud-run-source-deploy/hirewiz-document-inspector@sha256:" + "a" * 64,
            "policy_sha256": "b" * 64, "scan": {"engine": "ClamAV", "version": "synthetic", "definitions": "synthetic-not-live"},
            "parsed": {"raw_text": raw, "sections": {s["heading"]: s["text"] for s in case["resume"]["sections"]},
                       "skills": [], "experience_years": 0, "contact_info": {}}})
        captures[identity] = {"source_sha256": sha, "request_correlation_id": "synthetic-not-attested-" + identity, "worker_response": response}
    write(source_root, "materialization-manifest.json", {"packet_manifest_sha256": inputs.MANIFEST_SHA,
        "corpus_sha256": inputs.CORPUS_SHA, "cases": rows})
    write(evidence, "native-map.json", receipts)
    registry = write(evidence, "capture-registry.json", {"custody": "operator_reviewed_native_capture_v1",
        "reviewed_by": "synthetic-test-only", "reviewed_at": datetime.now(UTC).isoformat(),
        "manifest_sha256": inputs.MANIFEST_SHA, "captures": captures})
    funding = write(evidence, "funding.json", {"purpose": "synthetic-not-real-funding"})
    balance = write(evidence, "balance.json", {"purpose": "synthetic-not-a-provider-balance"})
    price = model_cost_policy.current_policy().pricing_quotes[0]
    route = {"prompt_version": interview_contract.PROMPT_VERSION, "contract_sha256": runner.contract_digest("interview_questions"), "price": price.model_dump(mode="json")}
    now = datetime.now(UTC)
    auth = inputs.Authorization.model_validate({"schema_version": inputs.VERSION,
        "experiment_id": "synthetic-v2-" + uuid4().hex, "purpose": "internal_quality_evaluation_not_retail_qualification",
        "state": "authorized", "authorized_by": "synthetic-test-only", "valid_from": now - timedelta(minutes=1),
        "expires_at": now + timedelta(hours=1), "provider_balance_checked_at": now,
        "funding_evidence": funding, "provider_balance_evidence": balance, "maximum_provider_cost_micros": 100_000_000,
        "model_policy_sha256": inputs.digest(model_cost_policy.current_policy().model_dump(mode="json")),
        "expense_policy_sha256": inputs.digest(current_policy().model_dump(mode="json")),
        "native_manifest_sha256": packet.file_digest(source_root / "materialization-manifest.json"),
        "native_receipts_sha256": packet.file_digest(evidence / "native-map.json"),
        "routes": {"reference": route, "candidate": route}, "operation": "interview_questions", "split": "development",
        "database": {"host": "127.0.0.1", "port": 65432, "database": "hirewiz_quality_evaluation_v2", "username": "hwqv2_test", "system_identifier": "1", "database_oid": 1},
        "native_capture_registry": registry, "native_reserve_minor_per_run": 10, "maximum_native_reserved_minor": 800,
        "native_currency": "INR", "native_runtime_sha256": runner.native_runtime_digest(),
        "worker_image": "us-central1-docker.pkg.dev/ai-resume-parser-482412/cloud-run-source-deploy/hirewiz-document-inspector@sha256:" + "a" * 64,
        "worker_policy_sha256": "b" * 64})
    return {"packet_root": ROOT, "native_root": source_root, "auth": auth,
            "authorization_root": evidence, "native_receipts": evidence / "native-map.json"}


def test_full_plan_is_200_cases_400_slots_without_loading_heldout(evaluation, monkeypatch):
    original = inputs.json.loads
    parsed_cases = []

    def load(value, *args, **kwargs):
        result = original(value, *args, **kwargs)
        if isinstance(result, dict) and "case_id" in result:
            parsed_cases.append(result)
            assert result["split"] == "development", "Held-out content must never be deserialized in development"
        return result

    monkeypatch.setattr(inputs.json, "loads", load)
    result = inputs.plan(ROOT, evaluation["auth"])
    assert result["case_count"] == 200 and result["split_counts"] == {"development": 40, "held_out": 160}
    assert result["complete_plan_slots"] == len(result["slots"]) == 400
    assert result["selected_slots"] == 80 and len(parsed_cases) == 40
    assert set(result["formats"].values()) == {50} and not result["authorizes_retail"]


def test_complete_native_preflight_but_no_retail_qualification(evaluation):
    result = runner.preflight(**evaluation, active=True)
    assert result["state"] == "ready_for_internal_evaluation"
    assert result["native_actual_cost_minor"] is None and not result["authorizes_retail"]
    assert result["required_native_reserved_minor"] == 800 and result["provider_calls"] == 0
    assert inputs.Authorization.model_validate_json(evaluation["auth"].model_dump_json()) == evaluation["auth"]


def test_missing_captures_block_before_database_or_generation(evaluation, monkeypatch):
    values = evaluation | {"native_receipts": None}
    assert runner.preflight(**values)["state"] == "blocked"
    monkeypatch.setattr(runner, "_identity", lambda *a: pytest.fail("No database access"))
    monkeypatch.setattr(llm_client, "_api_key", lambda: pytest.fail("No provider credential access"))
    for method in (runner.prepare, runner.execute):
        with pytest.raises(ValueError, match="Genuine native"):
            method(None, **values, **({"side": "candidate"} if method is runner.execute else {}))


def test_actual_interleaved_native_text_fails_even_with_all_pass_booleans(evaluation):
    evidence = evaluation["authorization_root"]
    registry = json.loads((evidence / "capture-registry.json").read_bytes())
    case = inputs.selected_cases(ROOT, "development")[1][0]
    identity = case["case_id"]
    entry = registry["captures"][identity]
    response = json.loads((evidence / entry["worker_response"]["path"]).read_bytes())
    raw = response["parsed"]["raw_text"]
    # Reverse actual text paragraphs. A claimed reading_order_pass cannot repair
    # the parser result by replacing it with expected fixture text.
    response["parsed"]["raw_text"] = "\n".join(reversed(raw.splitlines()))
    entry["worker_response"] = write(evidence, entry["worker_response"]["path"], response)
    registry_record = write(evidence, "capture-registry.json", registry)
    evaluation["auth"] = evaluation["auth"].model_copy(update={"native_capture_registry": inputs.legacy.Artifact.model_validate(registry_record)})
    result = runner.preflight(**evaluation)
    assert result["state"] == "blocked"
    assert any("interleaved reading order" in s for s in result["issues"])
    assert any("actual native response" in s for s in result["issues"])


@pytest.mark.parametrize("field", ["native_manifest_sha256", "native_receipts_sha256", "model_policy_sha256", "expense_policy_sha256", "maximum_provider_cost_micros", "maximum_native_reserved_minor"])
def test_funding_or_immutable_input_drift_refuses(evaluation, field):
    value = 1 if field.startswith("maximum") else "f" * 64
    evaluation["auth"] = evaluation["auth"].model_copy(update={field: value})
    with pytest.raises(ValueError):
        runner.preflight(**evaluation, active=True)


def test_stored_label_never_bypasses_disabled_public_canary(monkeypatch):
    from app.domains.credits import canary

    monkeypatch.setattr(canary, "requested", lambda *a: pytest.fail("Internal guard precedes public flag"))
    run = SimpleNamespace(id="qev2_forged", input_payload={"schema_version": inputs.VERSION})
    with pytest.raises(canary.FundingUnavailable, match="validated local"):
        canary.require_accepted_provider_run(None, run)
    assert runner._authority.get() is None


def test_remote_query_or_other_database_is_refused(evaluation):
    for url in ("sqlite://", "postgresql+psycopg://hwqv2_test@localhost:65432/hirewiz_quality_evaluation_v2",
                "postgresql+psycopg://hwqv2_test@127.0.0.1:65432/production",
                "postgresql+psycopg://hwqv2_test@127.0.0.1:65432/hirewiz_quality_evaluation_v2?options=public"):
        with pytest.raises(ValueError):
            runner.local_factory(url, evaluation["auth"])


def test_heldout_requires_distinct_reviewed_development_lock(evaluation):
    value = evaluation["auth"].model_dump(mode="json")
    value["split"] = "held_out"
    with pytest.raises(ValueError, match="locked-development"):
        inputs.Authorization.model_validate(value)
    value["held_out_lock"] = {"path": "missing-lock.json", "sha256": "f" * 64}
    auth = inputs.Authorization.model_validate(value)
    with pytest.raises(ValueError, match="artifact"):
        inputs.review_lock(auth, evaluation["authorization_root"])


def test_heldout_lock_binds_complete_development_pairs_routes_and_output_files(evaluation):
    evidence = evaluation["authorization_root"]
    auth = evaluation["auth"]
    routes = {side: route.model_dump(mode="json") for side, route in auth.routes.items()}
    manifest = json.loads((ROOT / "manifest.json").read_bytes())
    output = write(evidence, "synthetic-development-output.json", {"fixture": "not-real-quality-acceptance"})
    records = [{"case_id": row["case_id"], "side": side, "available": True, "output": output}
               for row in manifest["cases"] if row["split"] == "development" for side in inputs.SIDES]
    export = {"schema_version": inputs.VERSION, "operation": auth.operation, "split": "development",
        "manifest_sha256": inputs.MANIFEST_SHA, "protocol_sha256": inputs.PROTOCOL_SHA,
        "rubric_sha256": manifest["rubric_sha256"], "records": records, "routes": routes}
    lock = {"state": "locked_before_held_out", "manifest_sha256": inputs.MANIFEST_SHA,
        "protocol_sha256": inputs.PROTOCOL_SHA, "rubric_sha256": manifest["rubric_sha256"],
        "operation": auth.operation, "routes": routes, "reviewed_by": "synthetic-test-only",
        "locked_at": (datetime.now(UTC) - timedelta(minutes=2)).isoformat(),
        "development_native_and_human_review": "approved_for_held_out_execution",
        "review_evidence": write(evidence, "synthetic-review.json", {"fixture": "not-a-human-approval"}),
        "development_export": write(evidence, "synthetic-development-export.json", export)}

    def reviewed():
        record = write(evidence, "synthetic-heldout-lock.json", lock)
        return auth.model_copy(update={"split": "held_out", "held_out_lock": inputs.legacy.Artifact.model_validate(record)})

    inputs.review_lock(reviewed(), evidence)
    export["records"] = records[:-1] + [records[0]]  # Rehashed duplicate cannot hide a missing pair.
    lock["development_export"] = write(evidence, "synthetic-development-export.json", export)
    with pytest.raises(ValueError, match="exact development"):
        inputs.review_lock(reviewed(), evidence)
    heldout_id = next(row["case_id"] for row in manifest["cases"] if row["split"] == "held_out")
    export["records"] = records[:-1] + [records[-1] | {"case_id": heldout_id}]
    lock["development_export"] = write(evidence, "synthetic-development-export.json", export)
    with pytest.raises(ValueError, match="exact development"):
        inputs.review_lock(reviewed(), evidence)
    export["records"] = records
    export["routes"] = copy.deepcopy(routes)
    export["routes"]["candidate"]["contract_sha256"] = "f" * 64
    lock["development_export"] = write(evidence, "synthetic-development-export.json", export)
    with pytest.raises(ValueError, match="exact development"):
        inputs.review_lock(reviewed(), evidence)
    export["routes"] = routes
    lock["development_export"] = write(evidence, "synthetic-development-export.json", export)
    (evidence / output["path"]).write_text("synthetic changed output")
    with pytest.raises(ValueError, match="artifact"):
        inputs.review_lock(reviewed(), evidence)


def test_native_runtime_change_stops_before_any_generation(evaluation, monkeypatch):
    monkeypatch.setenv("DOCUMENT_WORKER_POLICY_SHA256", "f" * 64)
    with pytest.raises(ValueError, match="runtime deployment"):
        runner.preflight(**evaluation, active=True)


def test_cli_help_and_initialization_error_are_private(evaluation, tmp_path):
    script = Path(__file__).resolve().parents[1] / "scripts/run_supported_evaluation.py"
    auth_path = tmp_path / "authorization.json"
    auth_path.write_text(evaluation["auth"].model_dump_json())
    env = os.environ.copy() | {"DATABASE_URL": "unknown+secret-driver://synthetic-secret-do-not-log@host/db"}
    help_result = subprocess.run([sys.executable, str(script), "--help"], env=env, capture_output=True, text=True, timeout=20)
    assert help_result.returncode == 0
    result = subprocess.run([sys.executable, str(script), "plan", "--authorization", str(auth_path)], env=env, capture_output=True, text=True, timeout=20)
    assert result.returncode == 0  # Metadata planning is inert even with a bad app DSN.
    result = subprocess.run([sys.executable, str(script), "dry-run", "--authorization", str(auth_path), "--native", str(evaluation["native_root"])], env=env, capture_output=True, text=True, timeout=20)
    assert result.returncode == 2
    assert "synthetic-secret" not in result.stdout + result.stderr and "Traceback" not in result.stderr
    assert json.loads(result.stderr)["authorizes_retail"] is False
