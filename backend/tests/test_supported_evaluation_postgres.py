"""Owned disposable PG17; real operations and accounting, synthetic transports.

The fixture starts ONLY its own dynamically bound server and verifies its PID,
directory and native system identifier before stopping it. Never uses 55433/4,
production credentials, actual native workers or real model requests.
"""
from __future__ import annotations

import asyncio
import contextvars
import json
import os
import socket
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

import psycopg
import pytest

from app import models
from app.domains.analysis import supported_evaluation as runner
from app.domains.analysis import supported_evaluation_plan as inputs
from app.domains.credits import canary
from app.domains.free_core.models import FreeCoreReservation
from app.services import llm_client
from tests.test_supported_evaluation import ROOT, actual_composite_capture, evaluation  # noqa: F401


@pytest.fixture(scope="module")
def owned_postgres(tmp_path_factory):
    binary = Path("/opt/homebrew/opt/postgresql@17/bin")
    if not (binary / "initdb").is_file():
        pytest.skip("Owned disposable PostgreSQL 17 binaries are unavailable")
    version = subprocess.run([str(binary / "postgres"), "--version"], capture_output=True, text=True, check=True).stdout
    assert "PostgreSQL) 17." in version
    folder = tmp_path_factory.mktemp("owned-qev2-pg17")
    folder.chmod(0o700)
    data, logfile = folder / "data", folder / "postgres.log"
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    assert port not in {55433, 55434}
    def run(*args):
        return subprocess.run([str(binary / args[0]), *args[1:]], capture_output=True, text=True, check=True, timeout=30)

    def native_system():
        control = run("pg_controldata", "-D", str(data)).stdout
        return next(line.split(":", 1)[1].strip() for line in control.splitlines()
                    if line.startswith("Database system identifier:"))

    run("initdb", "-D", str(data), "--auth=trust", "-U", "hwqv2_test", "--no-locale", "--encoding=UTF8")
    initialized_system = native_system()
    run("pg_ctl", "-D", str(data), "-l", str(logfile), "-o", f"-h 127.0.0.1 -p {port} -k '' -c max_connections=25", "-w", "start")
    logfile.chmod(0o600)
    original_pid = (data / "postmaster.pid").read_text().splitlines()[0]
    try:
        with psycopg.connect(host="127.0.0.1", port=port, dbname="postgres", user="hwqv2_test", autocommit=True) as db:
            system = str(db.execute("SELECT system_identifier FROM pg_control_system()").fetchone()[0])
            assert system == initialized_system
            db.execute("CREATE DATABASE hirewiz_quality_evaluation_v2")
            oid = db.execute("SELECT oid FROM pg_database WHERE datname='hirewiz_quality_evaluation_v2'").fetchone()[0]
        yield {"url": f"postgresql+psycopg://hwqv2_test@127.0.0.1:{port}/hirewiz_quality_evaluation_v2",
               "pin": {"host": "127.0.0.1", "port": port, "database": "hirewiz_quality_evaluation_v2",
                       "username": "hwqv2_test", "system_identifier": system, "database_oid": oid}}
    finally:
        # Never stop a process just because it shares a port or database name.
        assert (data / "postmaster.pid").read_text().splitlines()[0] == original_pid
        assert native_system() == initialized_system
        run("pg_ctl", "-D", str(data), "-m", "fast", "-w", "stop")


@pytest.fixture
def prepared(evaluation, owned_postgres):  # noqa: F811
    values = evaluation | {"auth": evaluation["auth"].model_copy(update={"database": inputs.DatabasePin.model_validate(owned_postgres["pin"])})}
    factory = runner.local_factory(owned_postgres["url"], values["auth"])
    try:
        runner.prepare(factory, **values)
        yield factory, values
    finally:
        factory.kw["bind"].dispose()


def provider(monkeypatch, *, unknown=False, invalid=False, crash=False):
    calls = []

    def transport(messages, budget):
        record = budget.admit("google", llm_client.LLM_MODEL, messages, api_base=llm_client.LLM_API_BASE)
        calls.append(record)
        if crash:
            raise SystemExit("synthetic-process-termination")
        response = None if unknown else SimpleNamespace(usage_metadata=SimpleNamespace(
            prompt_token_count=100, candidates_token_count=80, total_token_count=200, thoughts_token_count=20))
        budget.finish(record, latency_ms=1, response=response)
        if invalid:
            return "[]"
        return json.dumps([{"question": f"How would you approach distinct role topic {n}?",
            "preparation_steps": ["Compare approaches to this role topic.", "Explain how to test the chosen approach."],
            "evidence_ids": []} for n in range(8)])

    monkeypatch.setattr(llm_client, "_chat_with_budget", transport)
    return calls


def test_real_pg_idempotent_prepare_native_actual_text_and_identity(prepared):
    factory, values = prepared
    runner.prepare(factory, **values)
    with factory() as db:
        assert db.query(models.AnalysisRun).count() == 80
        assert db.query(models.Resume).count() == 40
        assert db.query(FreeCoreReservation).count() == 80
        assert db.query(models.User).one().ai_credits == db.query(models.User).one().job_service_credits == 0
        assert all(r.settled_minor is None and r.state == "reserved" for r in db.query(FreeCoreReservation))
        changed = values["auth"].model_copy(update={"database": values["auth"].database.model_copy(update={"system_identifier": "42"})})
        with pytest.raises(ValueError, match="incarnation"):
            runner._identity(db, changed)


def test_two_real_pg_claimants_do_not_repeat_provider_calls(prepared, monkeypatch):
    factory, values = prepared
    calls = provider(monkeypatch)
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(runner.execute, factory, **values, side="candidate", maximum_operations=2) for _ in range(2)]
        outcomes = [future.result(timeout=60) for future in futures]
    ids = [identity for result in outcomes for identity in result["outcomes"]]
    assert len(ids) == len(set(ids)) == len(calls) == 4
    assert {state for result in outcomes for state in result["outcomes"].values()} == {"succeeded"}
    with factory() as db:
        assert db.query(models.ModelCostLiability).count() == 4
        assert all(r.settled_cost_micros == 200 for r in db.query(models.ModelCostLiability))
        assert all(r.settled_minor is None for r in db.query(FreeCoreReservation))


@pytest.mark.parametrize("unknown,invalid", [(True, False), (False, True)])
def test_real_unknown_and_failed_deliveries_never_redispatch_or_erase_liability(prepared, monkeypatch, unknown, invalid):
    factory, values = prepared
    calls = provider(monkeypatch, unknown=unknown, invalid=invalid)
    result = runner.execute(factory, **values, side="reference", maximum_operations=1)
    identity = next(iter(result["outcomes"]))
    assert result["outcomes"][identity] == "failed" and len(calls) == 1
    with factory() as db:
        db.query(models.AnalysisRun).filter_by(status="queued").update({"status": "running"})
        db.commit()
    assert runner.execute(factory, **values, side="reference")["outcomes"] == {}
    assert len(calls) == 1
    with factory() as db:
        liability = db.query(models.ModelCostLiability).one()
        assert liability.cost_state == ("usage_unavailable" if unknown else "settled")
        assert liability.reserved_cost_micros > 0
        assert db.get(FreeCoreReservation, "qen_" + inputs.digest(identity)[:55]).settled_minor is None


def test_interrupted_started_pg_work_retains_claim_and_no_redispatch(prepared, monkeypatch):
    factory, values = prepared
    calls = provider(monkeypatch, crash=True)
    with pytest.raises(SystemExit):
        runner.execute(factory, **values, side="candidate")
    with factory() as db:
        started = db.query(models.AnalysisRun).filter_by(status="running").one()
        identity = started.id
        assert started.attempt_count == 1
        db.query(models.AnalysisRun).filter_by(status="queued").update({"status": "running"})
        db.commit()
    assert runner.execute(factory, **values, side="candidate")["outcomes"] == {} and len(calls) == 1
    with factory() as db:
        assert db.get(models.AnalysisRun, identity).status == "running"
        assert db.query(models.ModelCostLiability).one().reserved_cost_micros > 0


def test_exact_scope_cannot_be_borrowed_by_thread_task_or_stored_label(prepared):
    factory, values = prepared
    with factory() as db:
        run = db.query(models.AnalysisRun).first()
        identity = run.id
        with pytest.raises(canary.FundingUnavailable):
            canary.require_accepted_provider_run(db, run)
    with runner._provider_scope(factory, values["auth"], values["authorization_root"], identity, ROOT):
        with factory() as db:
            canary.require_accepted_provider_run(db, db.get(models.AnalysisRun, identity))

        def borrowed():
            with factory() as db, pytest.raises(ValueError, match="process/database/run"):
                runner.require_provider_authority(db, db.get(models.AnalysisRun, identity))
        with ThreadPoolExecutor(max_workers=1) as pool:
            pool.submit(contextvars.copy_context().run, borrowed).result(timeout=20)
        asyncio.run(borrowed_async(factory, identity))
    assert runner._authority.get() is None


async def borrowed_async(factory, identity):
    with factory() as db, pytest.raises(ValueError, match="process/database/run"):
        runner.require_provider_authority(db, db.get(models.AnalysisRun, identity))


def test_changed_stored_case_refuses_even_if_fingerprint_recomputed(prepared, monkeypatch):
    factory, values = prepared
    with factory() as db:
        run = db.query(models.AnalysisRun).first()
        payload = json.loads(json.dumps(run.input_payload))
        payload["_quality_evaluation"]["case_sha256"] = "f" * 64
        run.input_payload = payload
        run.input_fingerprint = runner._fingerprint(db, run)
        db.commit()
        identity = run.id
    with runner._provider_scope(factory, values["auth"], values["authorization_root"], identity, ROOT):
        with factory() as db, pytest.raises(ValueError, match="inputs changed"):
            runner.require_provider_authority(db, db.get(models.AnalysisRun, identity))
    monkeypatch.setattr(llm_client, "_chat_with_budget", lambda *a: pytest.fail("No provider call"))


@pytest.mark.parametrize("changed", ["job", "native_text", "native_hold", "model_route"])
def test_frozen_source_and_obligation_cannot_be_forged_by_rehashing(prepared, changed):
    factory, values = prepared
    with factory() as db:
        run = db.query(models.AnalysisRun).first()
        identity = run.id
        if changed == "job":
            db.get(models.Opportunity, run.opportunity_id).job_description += " Unsupported synthetic change."
        elif changed == "native_text":
            db.get(models.Resume, run.input_payload["resume_id"]).raw_text += " Synthetic unsupported change."
        elif changed == "native_hold":
            hold = db.get(FreeCoreReservation, "qen_" + inputs.digest(identity)[:55])
            hold.settled_minor, hold.state = 0, "settled"
        else:
            value = dict(run.model_cost_quote)
            value["pricing_quotes"] = []
            run.model_cost_quote = value
        db.flush()
        run.input_fingerprint = runner._fingerprint(db, run)
        db.commit()
    with runner._provider_scope(factory, values["auth"], values["authorization_root"], identity, ROOT):
        with factory() as db, pytest.raises(ValueError):
            runner.require_provider_authority(db, db.get(models.AnalysisRun, identity))


def test_same_schema_different_allocation_is_refused_without_replacing_original(prepared):
    factory, values = prepared
    changed = values["auth"].model_copy(update={"maximum_provider_cost_micros": 100_000_001})
    with pytest.raises(ValueError, match="different experiment/allocation"):
        runner.prepare(factory, **(values | {"auth": changed}))
    with factory() as db:
        assert db.query(models.AnalysisRun).count() == db.query(FreeCoreReservation).count() == 80
        assert all(r.input_payload["_quality_evaluation"]["authorization"] == values["auth"].model_dump(mode="json") for r in db.query(models.AnalysisRun))


def test_export_keeps_all_slots_and_unknown_native_expense_without_qualification(prepared, monkeypatch, tmp_path):
    factory, values = prepared
    provider(monkeypatch)
    runner.execute(factory, **values, side="reference")
    monkeypatch.setenv("HIREWIZ_INTERNAL_EVALUATION_ENABLED", "false")
    destination = tmp_path / "private-export"
    result = runner.export_results(factory, values["auth"], destination, values["authorization_root"])
    report = json.loads((destination / "evaluation-export.json").read_bytes())
    assert result["records"] == len(report["records"]) == 80
    assert sum(r["available"] for r in report["records"]) == 1
    assert all(r["native_obligation"]["settled_minor"] is None for r in report["records"])
    assert all(not r["retail_qualified"] for r in report["records"])
    assert report["human_scores"] is None and not report["native_cost_unknown_is_zero"]
    assert os.stat(destination).st_mode & 0o777 == 0o700
    assert os.stat(destination / "evaluation-export.json").st_mode & 0o777 == 0o600
    with pytest.raises(ValueError, match="fresh absolute"):
        runner.export_results(factory, values["auth"], destination, values["authorization_root"])


def test_actual_complete_tailor_operation_preserves_pdf_and_exports_real_artifact(evaluation, owned_postgres, monkeypatch, tmp_path):  # noqa: F811
    from app.services.tailoring_contract import COMPLETE_PROMPT_VERSION

    data = evaluation["auth"].model_dump(mode="json")
    data["operation"], data["database"] = "resume_tailor", owned_postgres["pin"]
    for route in data["routes"].values():
        route["prompt_version"] = COMPLETE_PROMPT_VERSION
        route["contract_sha256"] = runner.contract_digest("resume_tailor")
    values = evaluation | {"auth": inputs.Authorization.model_validate(data)}
    factory = runner.local_factory(owned_postgres["url"], values["auth"])
    try:
        runner.prepare(factory, **values)
        identity = runner._run_id(values["auth"], "candidate", "synth-001")
        with factory() as db:
            db.query(models.AnalysisRun).filter(models.AnalysisRun.id != identity).update({"status": "running"})
            original = bytes(db.get(models.Resume, db.get(models.AnalysisRun, identity).input_payload["resume_id"]).source_document)
            db.commit()
        calls = []

        def transport(messages, budget):
            assert llm_client.full_tailoring_context_enabled(), "Use the actual full-context wrapper"
            text = messages[1]["content"]
            evidence_json, units_json = text.split("APPROVED EVIDENCE:\n", 1)[1].split("\n\nEDITABLE SOURCE UNITS:\n", 1)
            evidence, units = json.loads(evidence_json), json.loads(units_json)
            unit = next(u for u in units if "documented " in u["text"] and len(u["text"]) > 30)
            record = budget.admit("google", llm_client.LLM_MODEL, messages, api_base=llm_client.LLM_API_BASE)
            calls.append(record)
            budget.finish(record, latency_ms=1, response=SimpleNamespace(usage_metadata=SimpleNamespace(
                prompt_token_count=100, candidates_token_count=80, total_token_count=200, thoughts_token_count=20)))
            return json.dumps({"source_edits": [{"unit_id": unit["unit_id"], "original_text": unit["text"],
                "replacement_text": unit["text"].replace("documented ", "", 1),
                "evidence_ids": [evidence[0]["id"]], "reason": "Use concise wording."}], "evidence_needed": []})

        monkeypatch.setattr(llm_client, "_chat_with_budget", transport)
        outcome = runner.execute(factory, **values, side="candidate")
        assert outcome["outcomes"] == {identity: "succeeded"} and len(calls) == 1
        with factory() as db:
            run = db.get(models.AnalysisRun, identity)
            resume = db.get(models.Resume, run.input_payload["resume_id"])
            assert resume.source_document == original and run.committed_units == 0
            assert "billing_currency" not in run.model_cost_quote
            assert db.query(models.ResumeVersion).one().generation_run_id == identity
        target = tmp_path / "tailor-export"
        runner.export_results(factory, values["auth"], target, values["authorization_root"])
        report = json.loads((target / "evaluation-export.json").read_bytes())
        row = next(r for r in report["records"] if r["available"])
        content = (target / row["native_artifact"]["path"]).read_bytes()
        assert content.startswith(b"%PDF-") and content != original
        assert row["native_output_review"].startswith("pending_") and not row["quality_qualified"]
    finally:
        factory.kw["bind"].dispose()


def test_real_account_erase_retains_detached_model_and_native_obligations(prepared, monkeypatch):
    from app.routers.auth import delete_account

    factory, values = prepared
    provider(monkeypatch, unknown=True)
    runner.execute(factory, **values, side="candidate")
    with factory() as db:
        owner = db.query(models.User).one()
        assert delete_account(request=None, db=db, current_user=owner, token="synthetic-not-a-jwt") == {"status": "deleted"}
    with factory() as db:
        assert db.query(models.AnalysisRun).count() == db.query(models.Resume).count() == db.query(models.User).count() == 0
        assert db.query(models.ModelCallEvent).count() == 0
        assert db.query(models.ModelCostLiability).one().cost_state == "usage_unavailable"
        assert db.query(FreeCoreReservation).count() == 80
        assert all(r.user_id is None and r.settled_minor is None and r.reserved_minor > 0 for r in db.query(FreeCoreReservation))
    with pytest.raises(ValueError, match="erased experiment"):
        runner.prepare(factory, **values)


@pytest.mark.parametrize("kind", ["tex", "texzip"])
def test_real_pg_tex_input_uses_actual_pdf_inspection_in_production_operation(evaluation, owned_postgres, monkeypatch, kind):  # noqa: F811
    case, _, source, _, parsed = actual_composite_capture(evaluation, monkeypatch, kind=kind, columns=1)
    values = evaluation | {"auth": evaluation["auth"].model_copy(update={"database": inputs.DatabasePin.model_validate(owned_postgres["pin"])})}
    factory = runner.local_factory(owned_postgres["url"], values["auth"])
    try:
        runner.prepare(factory, **values)
        identity = runner._run_id(values["auth"], "candidate", case["case_id"])
        with factory() as db:
            run = db.get(models.AnalysisRun, identity)
            resume = db.get(models.Resume, run.input_payload["resume_id"])
            assert resume.source_format == kind and resume.source_document == source
            assert resume.raw_text == parsed["raw_text"] and resume.sections == parsed["sections"]
            assert resume.raw_text != case["resume"]["raw_text"]
            db.query(models.AnalysisRun).filter(models.AnalysisRun.id != identity).update({"status": "running"})
            db.commit()
        calls = provider(monkeypatch)
        assert runner.execute(factory, **values, side="candidate")["outcomes"] == {identity: "succeeded"}
        assert len(calls) == 1
        with factory() as db:
            assert db.query(models.ModelCostLiability).one().cost_state == "settled"
            assert db.query(FreeCoreReservation).count() == 80
            assert all(r.settled_minor is None for r in db.query(FreeCoreReservation))
    finally:
        factory.kw["bind"].dispose()
