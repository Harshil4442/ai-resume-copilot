"""Real local PostgreSQL claims/expense receipts; synthetic provider transport only."""
from __future__ import annotations

import json
import os
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest
from sqlalchemy import text

from app import models
from app.domains.analysis import development_runner as runner
from app.domains.analysis import quality_packet as packet
from app.services import interview_contract, llm_client
from tests.test_development_runner import calibration, write_json  # noqa: F401


@pytest.fixture
def prepared(calibration):  # noqa: F811 -- Imported pytest fixture dependency.
    url = os.getenv("HIREWIZ_TEST_POSTGRES_URL")
    if not url:
        pytest.skip("Set the dedicated disposable local PostgreSQL URL")
    factory = runner.local_factory(url, calibration["authorization"])
    try:
        runner.prepare(factory, **calibration)
        yield factory, calibration
    finally:
        with factory.kw["bind"].begin() as conn:
            conn.execute(text(f'DROP SCHEMA IF EXISTS "{runner._schema(calibration["authorization"])}" CASCADE'))
        factory.kw["bind"].dispose()


def provider(monkeypatch, *, unknown=False, invalid=False):
    calls = []

    def transport(messages, budget):
        record = budget.admit("google", llm_client.LLM_MODEL, messages, api_base=llm_client.LLM_API_BASE)
        calls.append(record)
        response = None if unknown else SimpleNamespace(usage_metadata=SimpleNamespace(
            prompt_token_count=100, candidates_token_count=80, total_token_count=200, thoughts_token_count=20))
        budget.finish(record, latency_ms=1, response=response)
        if invalid:
            return "[]"
        return json.dumps([{"question": f"How would you approach distinct role topic {n}?",
                            "preparation_steps": ["Compare approaches to this role topic.",
                                                  "Explain how to test the chosen approach."],
                            "evidence_ids": []} for n in range(8)])

    monkeypatch.setattr(llm_client, "_chat_with_budget", transport)
    return calls


def test_exact_run_claims_no_double_calls_on_concurrent_resume(prepared, monkeypatch):
    factory, values = prepared
    calls = provider(monkeypatch)
    with ThreadPoolExecutor(max_workers=2) as pool:
        jobs = [pool.submit(runner.execute, factory, **values, side="candidate", maximum_operations=10) for _ in range(2)]
        results = [job.result(timeout=60) for job in jobs]
    assert sum(len(item["executed_run_ids"]) for item in results) == 10
    assert len(calls) == 10
    assert runner.execute(factory, **values, side="candidate", maximum_operations=10)["executed_run_ids"] == []
    runner.prepare(factory, **values)
    with factory() as db:
        assert db.query(models.AnalysisRun).count() == 20
        assert db.query(models.AnalysisRun).filter_by(status="succeeded").count() == 10
        assert db.query(models.ModelCostLiability).count() == 10
        assert all(row.generation_attempt_limit == 1 and row.committed_units == 0 for row in db.query(models.AnalysisRun))
        assert all(row.settled_cost_micros == 200 and row.cost_state == "settled" for row in db.query(models.ModelCostLiability))
        assert db.query(models.User).one().ai_credits == 0


@pytest.mark.parametrize("unknown,invalid", [(True, False), (False, True)])
def test_failed_or_unmeasured_attempt_never_retries_or_fakes_free_cost(prepared, monkeypatch, unknown, invalid):
    factory, values = prepared
    calls = provider(monkeypatch, unknown=unknown, invalid=invalid)
    runner.execute(factory, **values, side="reference", maximum_operations=10)
    assert len(calls) == 10  # Invalid output's corrective call is refused before transport.
    assert runner.execute(factory, **values, side="reference", maximum_operations=10)["executed_run_ids"] == []
    with factory() as db:
        assert db.query(models.AnalysisRun).filter_by(status="failed").count() == 10
        if invalid:
            assert {run.error_code for run in db.query(models.AnalysisRun).filter_by(status="failed")} == {"InterviewCountError"}
        for liability in db.query(models.ModelCostLiability):
            if unknown:
                assert liability.cost_state == "usage_unavailable"
                assert liability.reserved_cost_micros > 0
            else:
                assert liability.cost_state == "settled" and liability.settled_cost_micros == 200


def test_completed_export_matches_real_ledger_and_cannot_qualify_retail(prepared, monkeypatch, tmp_path):
    factory, values = prepared
    provider(monkeypatch)
    runner.execute(factory, **values, side="reference", maximum_operations=1)
    # Export/recovery remains available while provider funding is paused.
    monkeypatch.setenv("HIREWIZ_INTERNAL_EVALUATION_ENABLED", "false")
    target = tmp_path / "export"
    result = runner.export_results(factory, values["authorization"], target, values["authorization_root"])
    assert not result["authorizes_release"] and result["approved_reference_count"] == 0
    row = next(row for row in result["records"] if row["state"] == "succeeded")
    params = {"input_sha": row["input_sha256"], "output_sha": row["output"]["sha256"], "label": "test"}
    errors = []
    packet._runner_receipt(target, row["runner_receipt"], **params, errors=errors, allow_calibration=True)
    assert errors == []
    packet._runner_receipt(target, row["runner_receipt"], **params, errors=errors)
    assert errors == ["Internal calibration receipt cannot qualify a retail route: test"]
    # A freshly rehashed receipt cannot hide a forged retained expense record.
    ledger_path = target / row["expense_ledger_export"]["path"]
    ledger = json.loads(ledger_path.read_bytes())
    ledger["liabilities"][0]["settled_cost_micros"] = 0
    bad_ledger = write_json(target, row["expense_ledger_export"]["path"], ledger)
    receipt = json.loads((target / row["runner_receipt"]["path"]).read_bytes())
    receipt["expense_ledger_export"] = bad_ledger
    bad_receipt = write_json(target, row["runner_receipt"]["path"], receipt)
    errors = []
    packet._runner_receipt(target, bad_receipt, **params, errors=errors, allow_calibration=True)
    assert errors == ["Legacy calibration liability receipt incomplete: test"]


def test_interrupted_started_run_is_not_claimed_again(prepared, monkeypatch):
    factory, values = prepared
    with factory() as db:
        runs = db.query(models.AnalysisRun).filter(models.AnalysisRun.input_payload["side"].as_string() == "candidate").all()
        for run in runs:
            run.status = "running"
        db.commit()
    calls = provider(monkeypatch)
    assert runner.execute(factory, **values, side="candidate", maximum_operations=10)["executed_run_ids"] == []
    assert calls == []


def test_native_google_sdk_failure_has_one_attempt_without_fallback(prepared, monkeypatch):
    from google import genai
    from google.genai.errors import APIError

    factory, values = prepared
    calls, clients = [], []

    def generate(**kwargs):
        calls.append(kwargs)
        raise APIError(503, {"error": {"message": "synthetic transient unavailable"}})

    def client(**kwargs):
        assert kwargs["http_options"].retry_options.attempts == 1
        clients.append(kwargs)
        return SimpleNamespace(models=SimpleNamespace(generate_content=generate), close=lambda: None)

    monkeypatch.setattr(genai, "Client", client)
    result = runner.execute(factory, **values, side="candidate", maximum_operations=1)
    assert list(result["outcomes"].values()) == ["failed"]
    assert len(calls) == len(clients) == 1
    assert calls[0]["model"] == "gemini-3.6-flash"
    with factory() as db:
        assert db.query(models.ModelCallEvent).count() == 1
        liability = db.query(models.ModelCostLiability).one()
        assert liability.cost_state == "outcome_unknown" and liability.reserved_cost_micros > 0


def test_changed_stored_case_is_refused_even_with_recomputed_fingerprint(prepared, monkeypatch):
    import copy

    factory, values = prepared
    with factory() as db:
        run = db.query(models.AnalysisRun).filter(models.AnalysisRun.input_payload["side"].as_string() == "candidate").order_by(models.AnalysisRun.id).first()
        payload = copy.deepcopy(run.input_payload)
        payload["case"]["job"]["description"] = "CUSTOM CUSTOMER INPUT IS FORBIDDEN"
        run.input_payload, run.input_fingerprint = payload, runner.digest(payload)
        db.commit()
    calls = provider(monkeypatch)
    with pytest.raises(ValueError, match="Prepared execution inputs changed"):
        runner.execute(factory, **values, side="candidate", maximum_operations=1)
    assert calls == []
    with factory() as db:
        assert db.query(models.AnalysisRun).filter_by(status="queued").count() == 20
        assert db.query(models.ModelCostLiability).count() == 0


@pytest.mark.parametrize("historical_prompt,historical_validator", [
    ("interview-evidence-v5", "interview-complete-v1"),
    ("interview-evidence-v6", "interview-complete-v2"),
    ("interview-evidence-v7", "interview-complete-v3"),
])
def test_historical_export_survives_new_prompt_and_implementation(
    request, monkeypatch, tmp_path, historical_prompt, historical_validator,
):
    url = os.getenv("HIREWIZ_TEST_POSTGRES_URL")
    if not url:
        pytest.skip("Set the dedicated disposable local PostgreSQL URL")
    historical = request.getfixturevalue("calibration")
    data = historical["authorization"].model_dump(mode="json")
    for route in data["routes"].values():
        route.update(prompt_version=historical_prompt, contract_sha256="a" * 64)
    auth_path = historical["authorization_root"] / "original-authorization.json"
    auth_path.write_bytes(packet.canonical(data))
    auth = runner.load_authorization(auth_path)
    values = {**historical, "authorization": auth}
    factory = runner.local_factory(url, auth)
    try:
        # Create isolated synthetic history under the earlier version identity.
        # No saved experiment or historical output artifact is rewritten.
        with monkeypatch.context() as old:
            old.setattr(interview_contract, "PROMPT_VERSION", historical_prompt)
            old.setattr(interview_contract, "VALIDATOR_VERSION", historical_validator)
            old.setattr(runner, "contract_digest", lambda: "a" * 64)
            runner.prepare(factory, **values)
            calls = provider(old)
            outcome = runner.execute(factory, **values, side="reference", maximum_operations=1)
            assert list(outcome["outcomes"].values()) == ["succeeded"] and len(calls) == 1

        assert interview_contract.PROMPT_VERSION == "interview-evidence-v8"
        assert interview_contract.VALIDATOR_VERSION == "interview-complete-v3"
        assert runner.contract_digest() != auth.routes["reference"].contract_sha256
        with factory() as db:
            saved = {run.id: packet.canonical({"input": run.input_payload, "output": run.result_payload,
                "fingerprint": run.input_fingerprint, "status": run.status}) for run in db.query(models.AnalysisRun)}
            retained_event = db.query(models.ModelCallEvent).one()
            retained_liability = db.query(models.ModelCostLiability).one()
            old_cost = (retained_event.id, retained_event.settled_cost_micros,
                        retained_liability.id, retained_liability.settled_cost_micros)
        monkeypatch.setattr(llm_client, "generate_interview_questions", lambda **kwargs: pytest.fail("Historical export cannot call a provider"))
        with pytest.raises(ValueError, match="current interview prompt"):
            runner.preflight(**values)
        with pytest.raises(ValueError, match="current interview prompt"):
            runner.execute(factory, **values, side="reference", maximum_operations=1)
        monkeypatch.setenv("HIREWIZ_INTERNAL_EVALUATION_ENABLED", "false")
        destination = tmp_path / "historical-export"
        result = runner.export_results(factory, auth, destination, values["authorization_root"])
        assert json.loads((destination / "funding-authorization.json").read_bytes()) == data
        completed = next(row for row in result["records"] if row["state"] == "succeeded")
        receipt = json.loads((destination / completed["runner_receipt"]["path"]).read_bytes())
        output = json.loads((destination / completed["output"]["path"]).read_bytes())
        assert receipt["prompt_version"] == historical_prompt
        assert output["contract_versions"]["prompt"] == historical_prompt
        assert output["contract_versions"]["validator"] == historical_validator
        with factory() as db:
            assert saved == {run.id: packet.canonical({"input": run.input_payload, "output": run.result_payload,
                "fingerprint": run.input_fingerprint, "status": run.status}) for run in db.query(models.AnalysisRun)}
            event = db.query(models.ModelCallEvent).one()
            liability = db.query(models.ModelCostLiability).one()
            assert old_cost == (event.id, event.settled_cost_micros, liability.id, liability.settled_cost_micros)
    finally:
        with factory.kw["bind"].begin() as conn:
            conn.execute(text(f'DROP SCHEMA IF EXISTS "{runner._schema(auth)}" CASCADE'))
        factory.kw["bind"].dispose()


def test_implementation_change_stops_before_another_run_claim(prepared, monkeypatch):
    factory, values = prepared
    calls = provider(monkeypatch)
    transport = llm_client._chat_with_budget

    def changed_after_first_result(messages, budget):
        result = transport(messages, budget)
        monkeypatch.setattr(runner, "contract_digest", lambda: "a" * 64)
        return result

    monkeypatch.setattr(llm_client, "_chat_with_budget", changed_after_first_result)
    with pytest.raises(ValueError, match="implementation changed"):
        runner.execute(factory, **values, side="candidate", maximum_operations=10)
    assert len(calls) == 1
    with factory() as db:
        assert db.query(models.AnalysisRun).filter_by(status="succeeded").count() == 1
        assert db.query(models.AnalysisRun).filter_by(status="queued").count() == 19
        assert db.query(models.ModelCallEvent).count() == db.query(models.ModelCostLiability).count() == 1
