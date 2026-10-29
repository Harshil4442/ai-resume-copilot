"""Internal synthetic interview calibration through the existing durable budget.

No retail qualification, customer entitlement, raw SDK or provider-free fallback.
Only the pinned ten development PDFs can enter this isolated local runner.
"""
from __future__ import annotations

import hashlib
import inspect
import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import create_engine, text
from sqlalchemy import inspect as sql_inspect
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker

from ...billing import cost_policy
from ...services import generation_budget, interview_contract, llm_client, model_cost_policy
from ..common import utcnow
from . import quality_packet as packet

VERSION = "synthetic-interview-development-v1"
RECEIPT_VERSION = "legacy-model-liability-v1"
MANIFEST_SHA = "b6c3d15cda3a0b10899712919be4c11a9b05441eafa055d4b7228fc2499cdddd"
CORPUS_SHA = "d8294cb7421a4a157b40f0bb6beee69c5ff598143bf12cbd9fe82625790bd538"
SIDES = ("reference", "candidate")


class Closed(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Artifact(Closed):
    path: str
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


class Route(Closed):
    # Retained authorizations are still readable after a prompt revision.
    # Execution separately requires the current prompt and implementation.
    prompt_version: Literal["interview-evidence-v5", "interview-evidence-v6", "interview-evidence-v7", "interview-evidence-v8", "interview-evidence-v9"]
    contract_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    price: model_cost_policy.ModelPriceQuote

    @model_validator(mode="after")
    def bounded(self):
        if (self.price.provider != "google" or self.price.model not in llm_client.GEMINI_FALLBACK_MODELS
                or self.price.max_input_tokens > interview_contract.MAX_CONTEXT_ESTIMATE
                or self.price.max_output_tokens > 8192):
            raise ValueError("Use an explicitly bounded existing native Gemini interview contract")
        return self


class Authorization(Closed):
    schema_version: Literal["synthetic-interview-development-v1"]
    experiment_id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{7,79}$")
    purpose: Literal["internal_calibration_not_sales_qualification"]
    state: Literal["draft", "authorized"]
    authorized_by: str = Field(min_length=1, max_length=160)
    valid_from: datetime
    expires_at: datetime
    provider_balance_checked_at: datetime
    funding_evidence: Artifact
    provider_balance_evidence: Artifact
    maximum_provider_cost_micros: int = Field(strict=True, gt=0)
    model_policy_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    expense_policy_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    native_manifest_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    native_receipts_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    routes: dict[Literal["reference", "candidate"], Route]

    @model_validator(mode="after")
    def finite(self):
        if set(self.routes) != set(SIDES):
            raise ValueError("Pin both reference and candidate contracts")
        if any(t.tzinfo is None for t in (self.valid_from, self.expires_at, self.provider_balance_checked_at)):
            raise ValueError("Use timezone-aware authorization times")
        if not self.valid_from < self.expires_at <= self.valid_from + timedelta(days=1):
            raise ValueError("An internal allocation lasts no longer than one day")
        return self


def digest(value) -> str:
    return packet.digest(packet.canonical(value))


def contract_digest() -> str:
    from ...services import prompt_privacy

    return digest({"interview_contract": packet.file_digest(Path(interview_contract.__file__)),
                   "llm_client": packet.file_digest(Path(llm_client.__file__)),
                   "context_and_runner": packet.file_digest(Path(__file__)),
                   "prompt_privacy": packet.file_digest(Path(prompt_privacy.__file__)),
                   "durable_budget": packet.file_digest(Path(generation_budget.__file__)),
                   "price_admission": packet.file_digest(Path(model_cost_policy.__file__)),
                   "entrypoint": packet.digest(inspect.getsource(llm_client.generate_interview_questions).encode())})


def _current_contract(authorization: Authorization) -> None:
    """Historical provenance may be exported, but never authorizes new work."""
    if any(route.prompt_version != interview_contract.PROMPT_VERSION for route in authorization.routes.values()):
        raise ValueError("The authorization must pin the current interview prompt")
    current = contract_digest()
    if any(route.contract_sha256 != current for route in authorization.routes.values()):
        raise ValueError("The pinned interview implementation changed")


def _schema(authorization: Authorization) -> str:
    return "hwdev_" + hashlib.sha256(authorization.experiment_id.encode()).hexdigest()[:20]


def local_factory(database_url: str, authorization: Authorization):
    """Loopback, dedicated test database, no public-schema or remote writes."""
    url = make_url(database_url)
    if (url.drivername != "postgresql+psycopg" or url.host != "127.0.0.1"
            or url.port not in {55433, 55434}
            or url.database not in {"hirewiz_quality_development", "hirewiz_admission_test"}
            or url.query):
        raise ValueError("Only the dedicated local PostgreSQL evaluation database is supported")
    engine = create_engine(url, connect_args={"options":
        f"-csearch_path={_schema(authorization)} -clock_timeout=5000 -cstatement_timeout=15000"})
    return sessionmaker(engine, expire_on_commit=False)


def _check_local(factory, authorization: Authorization) -> None:
    engine = factory.kw["bind"]
    url = engine.url
    if (engine.dialect.name != "postgresql" or url.host != "127.0.0.1"
            or url.port not in {55433, 55434}
            or url.database not in {"hirewiz_quality_development", "hirewiz_admission_test"}):
        raise ValueError("Evaluation cannot use a remote/customer database")
    with engine.connect() as conn:
        if conn.execute(text("SHOW search_path")).scalar() != _schema(authorization):
            raise ValueError("Evaluation requires its exact private schema")


def _artifact(root: Path, record: Artifact) -> Path:
    errors: list[str] = []
    path = packet._artifact(root, record.model_dump(), errors, "calibration input")
    if path is None:
        raise ValueError("Missing, changed or escaped calibration artifact")
    return path


def load_authorization(path: Path) -> Authorization:
    return Authorization.model_validate_json(path.read_bytes())


def _funding(authorization: Authorization, root: Path, *, active: bool) -> None:
    _artifact(root, authorization.funding_evidence)
    _artifact(root, authorization.provider_balance_evidence)
    if not active:
        return
    now = datetime.now(UTC)
    if (authorization.state != "authorized" or not authorization.valid_from <= now < authorization.expires_at
            or not now - timedelta(days=1) <= authorization.provider_balance_checked_at <= now):
        raise ValueError("A current explicit calibration allocation and balance observation are required")
    if os.getenv("APP_ENV", "").lower() not in {"test", "local", "development"}:
        raise ValueError("Calibration is forbidden in production and shared staging")
    if os.getenv("HIREWIZ_INTERNAL_EVALUATION_ENABLED", "") != "true":
        raise ValueError("Internal evaluation is not explicitly enabled")
    from ...services.generation_gate import require_generation

    require_generation()
    model = model_cost_policy.current_policy()
    expense = cost_policy.current_policy()
    if (digest(model.model_dump(mode="json")) != authorization.model_policy_sha256
            or digest(expense.model_dump(mode="json")) != authorization.expense_policy_sha256):
        raise ValueError("The reviewed model or funded expense policy changed")
    for route in authorization.routes.values():
        if route.price not in model.pricing_quotes or not route.price.valid_from <= now < route.price.expires_at:
            raise ValueError("The exact provider quote is not currently authorized")


def _scope(root: Path) -> list[dict]:
    if (packet.file_digest(root / "manifest.json") != MANIFEST_SHA
            or packet.file_digest(root / "cases.jsonl") != CORPUS_SHA
            or not packet.validate_packet(root).get("draft_inputs_valid")):
        raise ValueError("Only the original frozen synthetic packet can be evaluated")
    cases = [case for case in packet.load_packet(root)[2]
             if case["split"] == "development" and case["source_format"] == "pdf"]
    if len(cases) != 10:
        raise ValueError("The complete ten-PDF development scope is required")
    return cases


def _evidence(case: dict) -> list[dict]:
    return [{"id": fact["id"], "title": fact["id"], "text": fact["text"], "metrics": {}, "skills": []}
            for fact in case["resume"]["evidence"] if fact["approved_in_fixture"]]


def _case_context(case: dict) -> dict:
    return {"job_title": case["job"]["title"], "jd_text": case["job"]["description"],
            "num_questions": 8, "approved_evidence": _evidence(case)}


def preflight(packet_root: Path, native_root: Path, authorization: Authorization,
              authorization_root: Path, native_receipts: Path | None, *, active=False) -> dict:
    from pypdf import PdfReader

    _current_contract(authorization)
    _funding(authorization, authorization_root, active=active)
    cases = _scope(packet_root)
    if packet.file_digest(native_root / "materialization-manifest.json") != authorization.native_manifest_sha256:
        raise ValueError("The authorized native manifest changed")
    if native_receipts and packet.file_digest(native_receipts) != authorization.native_receipts_sha256:
        raise ValueError("The authorized native validation records changed")
    manifest = json.loads((native_root / "materialization-manifest.json").read_bytes())
    if manifest.get("packet_manifest_sha256") != MANIFEST_SHA or manifest.get("corpus_sha256") != CORPUS_SHA:
        raise ValueError("Native sources do not belong to the frozen packet")
    by_id = {row["case_id"]: row for row in manifest["cases"]}
    receipts = json.loads(native_receipts.read_bytes()) if native_receipts else {}
    rows, issues = [], []
    for case in cases:
        row = by_id[case["case_id"]]
        if row["input_sha256"] != digest(case):
            raise ValueError("Materialized case content changed")
        source = _artifact(native_root, Artifact.model_validate(row["input_artifact"]))
        if source.suffix != ".pdf" or not source.read_bytes().startswith(b"%PDF-"):
            raise ValueError("Only the pinned native PDFs are admitted")
        reader = PdfReader(source)
        extracted = " ".join(" ".join(page.extract_text().split()) for page in reader.pages)
        for section in case["resume"]["sections"]:
            if any(" ".join(line.split()) not in extracted for line in section["text"].splitlines()):
                raise ValueError("Native PDF text is incomplete")
        links = {ann.get_object()["/A"]["/URI"] for page in reader.pages for ann in page.get("/Annots", [])}
        if not set(case["source_recipe"]["required_links"]) <= links:
            raise ValueError("Native PDF hyperlinks are incomplete")
        case_errors: list[str] = []
        if native_receipts:
            packet._native_receipt(native_receipts.parent, receipts.get(case["case_id"], {}),
                source_sha=row["input_artifact"]["sha256"], target_sha=row["input_artifact"]["sha256"],
                errors=case_errors, label=case["case_id"])
        else:
            case_errors.append("Genuine native input validation receipt missing: " + case["case_id"])
        issues.extend(case_errors)
        messages = interview_contract.build_interview_messages(case["job"]["title"], case["job"]["description"], _evidence(case))
        for side, route in authorization.routes.items():
            estimate = model_cost_policy.input_token_estimate(messages)
            if estimate > route.price.max_input_tokens or route.contract_sha256 != contract_digest():
                raise ValueError("Complete input does not fit its pinned route")
            rows.append({"case_id": case["case_id"], "side": side, "input_sha256": digest(case),
                         "native_sha256": row["input_artifact"]["sha256"], "request_sha256": digest(_case_context(case)),
                         "input_estimate": estimate, "model": route.price.model,
                         "maximum_provider_cost_micros": model_cost_policy.quoted_cost(route.price.model_dump(), route.price.max_input_tokens, route.price.max_output_tokens)})
    required = sum(row["maximum_provider_cost_micros"] for row in rows)
    if required > authorization.maximum_provider_cost_micros:
        raise ValueError("The complete twenty-attempt provider envelope is not funded")
    if active and issues:
        raise ValueError("Genuine input validation is incomplete; no model work can start")
    return {"schema_version": VERSION, "state": "ready_for_internal_calibration" if not issues else "blocked",
            "operation": "interview_questions", "case_count": 10, "maximum_attempts": 20,
            "authorization_sha256": digest(authorization.model_dump(mode="json")),
            "native_receipts_sha256": packet.file_digest(native_receipts) if native_receipts else None,
            "maximum_provider_cost_micros": required, "runs": rows, "issues": issues,
            "provider_calls": 0, "authorizes_sales": False, "quality_status": "not_evaluated"}


def _run_id(authorization: Authorization, side: str, case_id: str) -> str:
    return "dev_" + hashlib.sha256(f"{authorization.experiment_id}:{side}:{case_id}".encode()).hexdigest()[:48]


def _owner(db, authorization: Authorization):
    from ... import models

    email = "synthetic-eval-" + _schema(authorization) + "@example.invalid"
    users = db.query(models.User).all()
    if len(users) != 1 or users[0].email != email or users[0].tier != "free" or users[0].ai_credits != 0:
        raise ValueError("The evaluation schema contains an unexpected owner")
    return users[0]


def prepare(factory, packet_root: Path, native_root: Path, authorization: Authorization,
            authorization_root: Path, native_receipts: Path) -> dict:
    report = preflight(packet_root, native_root, authorization, authorization_root, native_receipts, active=True)
    from ... import models
    from ...database import Base

    _check_local(factory, authorization)
    engine = factory.kw["bind"]
    with engine.begin() as conn:
        conn.execute(text(f'CREATE SCHEMA IF NOT EXISTS "{_schema(authorization)}"'))
    existing = set(sql_inspect(engine).get_table_names())
    if existing and not existing <= set(Base.metadata.tables):
        raise ValueError("Unknown data exists in this evaluation schema")
    Base.metadata.create_all(engine)
    auth = authorization.model_dump(mode="json")
    cases = {case["case_id"]: case for case in _scope(packet_root)}
    with factory() as db:
        # A bounded experiment lock protects initialization, not network work.
        db.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": int(_schema(authorization)[6:20], 16)})
        if db.query(models.User.id).count() == 0:
            db.add(models.User(email="synthetic-eval-" + _schema(authorization) + "@example.invalid",
                               password_hash="", tier="free", ai_credits=0, job_service_credits=0))
            db.flush()
        owner = _owner(db, authorization)
        expected = {_run_id(authorization, row["side"], row["case_id"]) for row in report["runs"]}
        saved = db.query(models.AnalysisRun).all()
        if any(run.id not in expected or run.input_payload.get("authorization") != auth for run in saved):
            raise ValueError("This experiment already has different inputs or authorization")
        for row in report["runs"]:
            identity = _run_id(authorization, row["side"], row["case_id"])
            if db.get(models.AnalysisRun, identity):
                continue
            payload = {"schema_version": VERSION, "authorization": auth, "case": cases[row["case_id"]],
                       "side": row["side"], "native_sha256": row["native_sha256"],
                       "native_receipts_sha256": report["native_receipts_sha256"], "request_sha256": row["request_sha256"]}
            run = models.AnalysisRun(id=identity, user_id=owner.id, operation="interview_questions", status="queued",
                idempotency_key=identity, input_fingerprint=digest(payload), input_payload=payload,
                estimated_units=0, committed_units=0, usage_state="waived", generation_attempt_limit=1,
                attempt_count=0, generation_attempt_count=0, created_at=utcnow(), updated_at=utcnow())
            model_cost_policy.freeze_run_quote(run)
            run.model_cost_ceiling_micros = min(run.model_cost_ceiling_micros, row["maximum_provider_cost_micros"])
            db.add(run)
        db.commit()
    return report


def execute(factory, packet_root: Path, native_root: Path, authorization: Authorization,
            authorization_root: Path, native_receipts: Path, *, side: Literal["reference", "candidate"], maximum_operations: int = 1) -> dict:
    if side not in SIDES or type(maximum_operations) is not int or not 1 <= maximum_operations <= 10:
        raise ValueError("Select one side and one to ten bounded operations")
    _current_contract(authorization)
    _check_local(factory, authorization)
    report = preflight(packet_root, native_root, authorization, authorization_root, native_receipts, active=True)
    from ... import models

    route = authorization.routes[side]
    frozen_cases = {case["case_id"]: case for case in _scope(packet_root)}
    planned = {row["case_id"]: row for row in report["runs"] if row["side"] == side}
    if (llm_client.LLM_MODEL != route.price.model or llm_client.LLM_API_BASE.rstrip("/") != route.price.api_base):
        raise ValueError("The process provider/model does not match the pinned side")
    if not llm_client._api_key():
        raise ValueError("A privately configured provider credential is required before claiming work")
    executed, outcomes = [], {}
    for _ in range(maximum_operations):
        _current_contract(authorization)
        with factory() as db:
            owner = _owner(db, authorization)
            run = db.query(models.AnalysisRun).filter_by(user_id=owner.id, status="queued").filter(
                models.AnalysisRun.input_payload["side"].as_string() == side).order_by(models.AnalysisRun.id).with_for_update(skip_locked=True).first()
            if run is None:
                break
            case_id = run.input_payload.get("case", {}).get("case_id")
            if (run.input_payload["authorization"] != authorization.model_dump(mode="json")
                    or run.input_fingerprint != digest(run.input_payload)
                    or run.input_payload.get("case") != frozen_cases.get(case_id)
                    or case_id not in planned
                    or run.input_payload.get("native_sha256") != planned[case_id]["native_sha256"]
                    or run.input_payload.get("request_sha256") != planned[case_id]["request_sha256"]
                    or run.input_payload["native_receipts_sha256"] != report["native_receipts_sha256"]
                    or run.id != _run_id(authorization, side, run.input_payload["case"]["case_id"])):
                raise ValueError("Prepared execution inputs changed")
            identity, context = run.id, _case_context(run.input_payload["case"])
            run.status, run.started_at, run.attempt_count = "running", utcnow(), 1
            db.commit()  # Lost process/response never makes this queued again.
        try:
            with generation_budget.persistent_run_budget(factory, identity):
                questions = llm_client.generate_interview_questions(**context)
            result = {"questions": questions, "mode": "enhanced", "provenance": "generated",
                      "contract_versions": interview_contract.contract_versions()}
            with factory() as db:
                run = db.query(models.AnalysisRun).filter_by(id=identity).with_for_update().one()
                events = db.query(models.ModelCallEvent).filter_by(analysis_run_id=identity).all()
                if (len(events) != 1 or events[0].cost_state != "settled" or events[0].status != "succeeded"
                        or run.model_cost_state == "overrun" or not packet._output_contract("interview_questions", result, run.input_payload["case"])):
                    raise ValueError("Delivery needs complete output and observed settled provider usage")
                run.result_payload, run.status = result, "succeeded"
                run.completed_at = run.updated_at = utcnow()
                db.commit()
        except Exception as exc:
            with factory() as db:
                run = db.query(models.AnalysisRun).filter_by(id=identity).with_for_update().one()
                run.status, run.error_code = "failed", type(exc).__name__
                run.error_message = "Internal calibration did not produce a complete settled result. Expense is retained."
                run.completed_at = run.updated_at = utcnow()
                db.commit()
        executed.append(identity)
        with factory() as db:
            outcomes[identity] = db.get(models.AnalysisRun, identity).status
    return {"schema_version": VERSION, "executed_run_ids": executed, "authorizes_sales": False,
            "outcomes": outcomes,
            "resume_policy": "Only never-started queued work is eligible; running/unknown/failed work is not redispatched"}


def export_results(factory, authorization: Authorization, destination: Path, authorization_root: Path) -> dict:
    """Read-only DB export remains possible after authorization/policies expire."""
    _check_local(factory, authorization)
    from ... import models

    evidence = {record.path: _artifact(authorization_root, record)
                for record in (authorization.funding_evidence, authorization.provider_balance_evidence)}
    destination.mkdir(parents=True, exist_ok=False)
    for relative, source in evidence.items():
        target = destination / relative
        if target.resolve() == (destination / "funding-authorization.json").resolve():
            raise ValueError("Evidence path conflicts with export metadata")
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("xb") as output:
            output.write(source.read_bytes())
    auth = authorization.model_dump(mode="json")
    (destination / "funding-authorization.json").write_bytes(packet.canonical(auth))
    funding = {"path": "funding-authorization.json", "sha256": packet.file_digest(destination / "funding-authorization.json")}
    records = []
    frozen_cases = {case["case_id"]: case for case in _scope(Path(__file__).resolve().parents[3] / "evals/unified_credit_v1")}
    with factory() as db:
        owner = _owner(db, authorization)
        runs = db.query(models.AnalysisRun).filter_by(user_id=owner.id).order_by(models.AnalysisRun.id).all()
        for run in runs:
            case_id = run.input_payload.get("case", {}).get("case_id")
            if (run.input_payload.get("authorization") != auth or run.input_fingerprint != digest(run.input_payload)
                    or run.input_payload.get("case") != frozen_cases.get(case_id)
                    or run.input_payload["request_sha256"] != digest(_case_context(run.input_payload["case"]))):
                raise ValueError("Stored experiment provenance changed")
            folder = destination / run.id
            folder.mkdir()
            events = db.query(models.ModelCallEvent).filter_by(analysis_run_id=run.id).order_by(models.ModelCallEvent.attempt_number).all()
            liabilities = db.query(models.ModelCostLiability).filter_by(financial_group_id=run.model_cost_group_id).all() if run.model_cost_group_id else []
            event_fields = ("id", "analysis_run_id", "liability_id", "provider", "model", "prompt_version", "attempt_number", "input_tokens", "output_tokens", "tokens_estimated", "reserved_cost_micros", "settled_cost_micros", "pricing_quote", "cost_state", "usage_provenance", "status")
            liability_fields = ("id", "financial_group_id", "attempt_number", "provider", "model", "currency", "pricing_quote", "input_token_estimate", "input_tokens", "output_tokens", "reserved_cost_micros", "settled_cost_micros", "cost_state", "usage_provenance")
            ledger = {"schema_version": RECEIPT_VERSION, "run_id": run.id, "financial_group_id": run.model_cost_group_id,
                      "input_sha256": digest(run.input_payload["case"]), "request_sha256": run.input_payload["request_sha256"],
                      "authorization_sha256": digest(auth), "side": run.input_payload["side"],
                      "events": [{field: getattr(row, field) for field in event_fields} for row in events],
                      "liabilities": [{field: getattr(row, field) for field in liability_fields} for row in liabilities]}
            (folder / "expense-ledger.json").write_bytes(packet.canonical(ledger))
            record = {"case_id": run.input_payload["case"]["case_id"], "side": run.input_payload["side"], "run_id": run.id,
                      "state": run.status, "input_sha256": digest(run.input_payload["case"]), "native_sha256": run.input_payload["native_sha256"],
                      "expense_ledger_export": {"path": str((folder / "expense-ledger.json").relative_to(destination)), "sha256": packet.file_digest(folder / "expense-ledger.json")}}
            if run.status == "succeeded":
                (folder / "output.json").write_bytes(packet.canonical(run.result_payload))
                output_sha = packet.file_digest(folder / "output.json")
                receipt = {"schema_version": RECEIPT_VERSION, "state": "completed", "run_id": run.id,
                           "input_sha256": record["input_sha256"], "output_sha256": output_sha,
                           "financial_group_id": run.model_cost_group_id, "liability_ids": [row.id for row in liabilities],
                           "provider": run.provider, "model": run.model, "prompt_version": run.prompt_version,
                           "usage_provenance": events[0].usage_provenance if events else None,
                           "provider_request_id": None, "funding_authorization": funding,
                           "expense_ledger_export": record["expense_ledger_export"], "calibration_only": True}
                (folder / "runner-receipt.json").write_bytes(packet.canonical(receipt))
                record.update(output={"path": str((folder / "output.json").relative_to(destination)), "sha256": output_sha},
                              runner_receipt={"path": str((folder / "runner-receipt.json").relative_to(destination)), "sha256": packet.file_digest(folder / "runner-receipt.json")})
            records.append(record)
    summary = {"schema_version": VERSION, "scope": "ten_frozen_pdf_development_pairs_only",
               "records": records, "quality_status": "not_evaluated", "approved_reference_count": 0,
               "authorizes_release": False, "complete_packet": False}
    (destination / "development-results.json").write_bytes(packet.canonical(summary))
    return summary
