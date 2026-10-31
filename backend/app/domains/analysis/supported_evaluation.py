"""Durable, operator-funded v2 evaluation of actual production operations.

Loopback-only independent schemas; no customer credits, retail qualification or
native receipt fabrication. Never redispatch running/unknown/failed work.
"""
from __future__ import annotations

import asyncio
import contextlib
import contextvars
import json
import os
from pathlib import Path
from threading import get_ident
from types import ModuleType

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker

from ...services import generation_budget, interview_contract, llm_client, model_cost_policy
from . import development_runner as legacy
from . import quality_packet as packet
from . import supported_evaluation_native as native_import
from . import supported_evaluation_plan as inputs

VERSION = inputs.VERSION
_authority: contextvars.ContextVar[tuple | None] = contextvars.ContextVar("supported_v2_runner_authority", default=None)


def _sync():
    # Metadata planning must remain inert even when the app DSN is invalid.
    from . import supported_evaluation_synchronous

    return supported_evaluation_synchronous


def _schema(auth):
    return "hwqv2_" + inputs.digest(auth.experiment_id)[:20]


def attempts(operation):
    return 3 if operation == "resume_tailor" else 1


def contract_digest(operation):
    from ...services import (
        delivery_contracts,
        document_ingestion,
        docx_artifact,
        generation_gate,
        matching_contract,
        native_tex,
        personal_claims,
        prompt_privacy,
        result_commit,
        resume_artifacts,
        resume_layout,
        tailoring_contract,
    )
    from ..career import evidence_fence_definition, evidence_write_fence
    from ..credits import canary
    from . import operations, service

    if operation not in inputs.OPERATIONS:
        raise ValueError("Only the explicitly implemented production adapters are supported")
    modules: tuple[ModuleType, ...] = (operations, service, generation_budget, model_cost_policy, llm_client,
               interview_contract, tailoring_contract, personal_claims, prompt_privacy,
               resume_artifacts, resume_layout, inputs, canary, document_ingestion,
               docx_artifact, native_tex, delivery_contracts, result_commit, generation_gate, native_import, matching_contract,
               evidence_fence_definition, evidence_write_fence)
    if _sync().supports(operation) or operation in {"job_match", "resume_tailor"}:
        from ...services import provider_input_guard, source_transcription
        from ..career import transcription_review

        # These actual inputs/dispatch helpers are part of the frozen operation,
        # even when their direct consumer module bytes have not changed.
        modules += (source_transcription, transcription_review, provider_input_guard)
    return inputs.digest({"operation": operation, "runner": packet.file_digest(Path(__file__)),
                          "sources": {m.__name__: packet.file_digest(Path(str(m.__file__)))
                                      for m in (*modules, _sync(), *_sync().contract_sources())}})


def _current(auth):
    from ...services.matching_contract import PROMPT_VERSION as MATCH_PROMPT_VERSION
    from ...services.tailoring_contract import COMPLETE_PROMPT_VERSION

    version = _sync().prompt_version(auth.operation) if _sync().supports(auth.operation) else {
               "interview_questions": interview_contract.PROMPT_VERSION,
               "resume_tailor": COMPLETE_PROMPT_VERSION, "job_match": MATCH_PROMPT_VERSION}[auth.operation]
    if any(r.prompt_version != version or r.contract_sha256 != contract_digest(auth.operation) for r in auth.routes.values()):
        raise ValueError("The frozen operation, complete-context prompt or runner changed")


def native_runtime_digest():
    """Bind existing native clients' deployment inputs; never print their values."""
    keys = ("DOCUMENT_WORKER_URL", "DOCUMENT_WORKER_IMAGE_DIGEST", "DOCUMENT_WORKER_POLICY_SHA256",
            "DOCUMENT_WORKER_DOCX_RENDERER_VERSION", "DOCUMENT_WORKER_DOCX_FONT_MANIFEST_SHA256",
            "NATIVE_TEX_COMPILER_URL", "NATIVE_TEX_IMAGE_DIGEST", "NATIVE_TEX_IMAGE",
            "NATIVE_TEX_ISOLATION_POLICY_SHA256")
    return inputs.digest({key: os.getenv(key, "") for key in keys})


def _funding(auth, root, *, active):
    _current(auth)
    legacy._funding(auth, root, active=active)
    inputs.review_lock(auth, root)
    if active and auth.native_runtime_sha256 != native_runtime_digest():
        raise ValueError("The reviewed native runtime deployment bindings changed")


def local_factory(database_url: str, auth: inputs.Authorization):
    url, pin = make_url(database_url), auth.database
    if (url.drivername != "postgresql+psycopg" or url.host != pin.host or url.port != pin.port
            or url.database != pin.database or url.username != pin.username or url.query):
        raise ValueError("The exact dedicated loopback evaluation database is required")
    return sessionmaker(create_engine(url, connect_args={"options":
        f"-csearch_path={_schema(auth)} -clock_timeout=5000 -cstatement_timeout=15000"}), expire_on_commit=False)


def _identity(db, auth):
    bind, pin = db.get_bind(), auth.database
    url = bind.url
    if (bind.dialect.name != "postgresql" or url.host != pin.host or url.port != pin.port
            or url.database != pin.database or url.username != pin.username or url.query):
        raise ValueError("A remote or different database cannot authorize evaluation")
    observed = db.execute(text("SELECT (SELECT system_identifier::text FROM pg_control_system()), "
        "(SELECT oid::bigint FROM pg_database WHERE datname=current_database()), current_user, current_setting('search_path'), "
        "(SELECT oid::bigint FROM pg_namespace WHERE nspname=:schema)"), {"schema": _schema(auth)}).one()
    if tuple(observed[:4]) != (pin.system_identifier, pin.database_oid, pin.username, _schema(auth)):
        raise ValueError("The native local database incarnation or schema changed")
    return tuple(observed)


def _run_id(auth, side, case_id):
    return "qev2_" + inputs.digest([auth.experiment_id, auth.operation, auth.split, side, case_id])[:55]


def _owner(db, auth):
    from ... import models

    user = db.query(models.User).filter_by(email="synthetic-v2-" + _schema(auth) + "@example.invalid").one_or_none()
    if user is None or user.ai_credits != 0 or user.job_service_credits != 0 or user.tier != "free":
        raise ValueError("The exact zero-entitlement synthetic owner is required")
    return user


def declared_generation_configuration(run):
    """Reviewed evaluation-side expectation, never actual provider-call usage."""
    metadata = (run.input_payload or {}).get("_quality_evaluation", {})
    auth = inputs.Authorization.model_validate(metadata["authorization"])
    if run.operation != auth.operation or metadata["side"] not in inputs.SIDES:
        raise ValueError("The evaluation-side generation binding changed")
    route = auth.routes[metadata["side"]]
    return {"model": route.price.model, "api_base": route.price.api_base,
            "fallback_models": list(llm_client.GEMINI_FALLBACK_MODELS)}


def _fingerprint(db, run):
    from ..common import payload_fingerprint
    from .service import _dependency_snapshot

    if _sync().supports(run.operation):
        return _sync().fingerprint(db, run)
    dependencies = _dependency_snapshot(db, run.user_id, run.operation, run.opportunity_id,
        run.input_payload, paid_context=run.operation in {"resume_tailor", "job_match"})
    if "generation" in dependencies:
        config = declared_generation_configuration(run)
        dependencies["generation"].update({"model_policy": config["model"], "provider_base": config["api_base"]})
        if "effective_model" in dependencies["generation"]:
            dependencies["generation"].update({"effective_model": config["model"], "effective_provider_base": config["api_base"]})
    return payload_fingerprint({"operation": run.operation, "opportunity_id": run.opportunity_id,
                               "input": run.input_payload, "dependencies": dependencies})


@contextlib.contextmanager
def _provider_scope(factory, auth, root, identity, packet_root):
    _funding(auth, root, active=True)
    with factory() as db:
        database = _identity(db, auth)
    if _authority.get() is not None:
        raise ValueError("Evaluation authority cannot be nested or borrowed")
    try:
        task = asyncio.current_task()
    except RuntimeError:
        task = None
    cases = {c["case_id"]: c for c in inputs.selected_cases(packet_root, auth.split)[1]}
    token = _authority.set((identity, auth, root, database, os.getpid(), get_ident(), task, cases))
    try:
        yield
    finally:
        _authority.reset(token)


def require_provider_authority(db, run):
    """Exact process-only authority, native local identity and immutable inputs."""
    value = _authority.get()
    if value is None:
        raise ValueError("Use the validated v2 runner, not a stored label or dev environment")
    identity, auth, root, database, pid, thread, owner_task, cases = value
    try:
        task = asyncio.current_task()
    except RuntimeError:
        task = None
    if os.getpid() != pid or get_ident() != thread or task is not owner_task or _identity(db, auth) != database or run.id != identity:
        raise ValueError("The exact execution process/database/run binding changed")
    _funding(auth, root, active=True)
    owner = _owner(db, auth)
    payload = run.input_payload or {}
    metadata = payload.get("_quality_evaluation", {})
    expected_keys = {"schema_version", "mode", "num_questions", "resume_id", "opportunity_id", "_quality_evaluation"}
    if auth.operation == "job_match":
        expected_keys |= {"job_description", "job_title", "company"}
    elif _sync().supports(auth.operation):
        expected_keys.add("synchronous_input")
    case = cases.get(metadata.get("case_id"))
    from ..free_core.models import FreeCoreReservation
    native = db.get(FreeCoreReservation, "qen_" + inputs.digest(run.id)[:55])
    if (run.user_id != owner.id or run.operation != auth.operation or payload.get("schema_version") != VERSION
            or set(payload) != expected_keys
            or payload.get("mode") != "enhanced" or payload.get("num_questions") != 8
            or run.generation_attempt_limit != attempts(auth.operation) or run.usage_state != "waived"
            or run.estimated_units != 0 or run.committed_units != 0
            or metadata.get("authorization") != auth.model_dump(mode="json")
            or metadata.get("side") not in inputs.SIDES
            or case is None or metadata.get("case_sha256") != inputs.digest(case)
            or native is None or native.user_id != owner.id or native.state != "reserved"
            or native.settled_minor is not None or native.reserved_minor != auth.native_reserve_minor_per_run
            or native.operation != auth.operation or native.route_id != "internal_quality_v2_not_retail"
            or native.policy_version != auth.expense_policy_sha256 or native.funding_period_id != auth.experiment_id
            or run.id != _run_id(auth, metadata.get("side"), metadata.get("case_id"))
            or run.input_fingerprint != _fingerprint(db, run)):
        raise ValueError("The internally funded production inputs changed")
    from ... import models
    resume = db.get(models.Resume, payload.get("resume_id"))
    quote = run.model_cost_quote or {}
    route = auth.routes[metadata["side"]]
    if (resume is None or resume.user_id != owner.id
            or packet.digest(resume.source_document or b"") != metadata.get("source_sha256")
            or quote.get("pricing_quotes") != [route.price.model_dump(mode="json")]
            or not 0 < run.model_cost_ceiling_micros <= model_cost_policy.quoted_cost(
                route.price.model_dump(), route.price.max_input_tokens, route.price.max_output_tokens) * attempts(auth.operation)):
        raise ValueError("The native source or exact funded side changed")
    _frozen_inputs(db, run, auth, root, case, resume)
    if _sync().supports(auth.operation):
        _sync().immutable_inputs_current(db, run)


def _frozen_inputs(db, run, auth, root, case, resume):
    """Stored labels/fingerprints cannot substitute for the authorized sources."""
    from ... import models

    registry_path = legacy._artifact(root, auth.native_capture_registry)
    registry = json.loads(registry_path.read_bytes())
    capture = registry["captures"][case["case_id"]]
    metadata = run.input_payload["_quality_evaluation"]
    if capture["source_sha256"] != metadata["source_sha256"]:
        raise ValueError("The authorized native source changed")
    parsed = native_import.decode(registry_path.parent, capture, resume.source_document, resume.source_format,
        worker_image=auth.worker_image, worker_policy=auth.worker_policy_sha256,
        compiler_image=auth.compiler_image, compiler_policy=auth.compiler_policy_sha256).extraction.parsed
    if parsed != (resume.raw_text, resume.sections, resume.skills, resume.experience_years, resume.contact_info):
        raise ValueError("The actual native parsing result changed")
    opportunity = db.get(models.Opportunity, run.opportunity_id)
    if (opportunity is None or opportunity.user_id != run.user_id or opportunity.resume_id != resume.id
            or opportunity.title != case["job"]["title"] or opportunity.job_description != case["job"]["description"]
            or opportunity.company != "Synthetic evaluation" or opportunity.source != "internal_evaluation"
            or {key: value for key, value in (opportunity.job_snapshot or {}).items()
                if key not in ({"required_skills", "match_mode", "scoring_version"} if auth.operation == "job_match" else set())}
                != {"case_sha256": inputs.digest(case)}
            or run.input_payload["opportunity_id"] != opportunity.id
            or db.query(models.UserProfile).filter_by(user_id=run.user_id).first() is not None):
        raise ValueError("The frozen synthetic job or preferences changed")
    if auth.operation == "job_match" and any(run.input_payload.get(key) != expected for key, expected in {
            "job_title": case["job"]["title"], "job_description": case["job"]["description"],
            "company": "Synthetic evaluation"}.items()):
        raise ValueError("The frozen matching request changed")
    approved = db.query(models.EvidenceItem).filter_by(user_id=run.user_id, resume_id=resume.id, approval_state="approved").all()
    observed = {r.id: (r.evidence_text, r.title, r.category, r.metrics, r.skills, r.provenance) for r in approved}
    expected: dict[str, tuple] = {r["id"]: (r["text"], r["id"], "synthetic", {}, [], "synthetic_fixture")
                for r in case["resume"]["evidence"] if r["approved_in_fixture"]}
    if observed != expected:
        raise ValueError("The frozen approved evidence changed")


def is_complete_context(db, run):
    if not run.id.startswith("qev2_") and (run.input_payload or {}).get("schema_version") != VERSION:
        return False
    require_provider_authority(db, run)
    return True


def preflight(packet_root, native_root, auth, authorization_root, native_receipts=None, *, active=False):
    _funding(auth, authorization_root, active=active)
    report = inputs.plan(packet_root, auth)
    rows, issues = inputs.native_inputs(packet_root, native_root, auth, authorization_root, native_receipts)
    required_model = sum(model_cost_policy.quoted_cost(r.price.model_dump(), r.price.max_input_tokens,
                         r.price.max_output_tokens) * attempts(auth.operation) for r in auth.routes.values()) * len(rows)
    required_native = auth.native_reserve_minor_per_run * len(rows) * 2
    if required_model > auth.maximum_provider_cost_micros or required_native > auth.maximum_native_reserved_minor:
        raise ValueError("Fund the entire selected split and both sides before accepting any work")
    if active and issues:
        raise ValueError("Genuine native validation/reading-order approval is incomplete; no model work may start")
    return report | {"state": "blocked" if issues else "ready_for_internal_evaluation",
        "issues": issues, "required_model_cost_micros": required_model,
        "required_native_reserved_minor": required_native, "native_actual_cost_minor": None,
        "native_custody": "operator_reviewed_capture_not_provider_signature",
        "source_reading_order_qualified": not issues, "authorizes_retail": False}


def _register_prompt(db, auth):
    """Register observed production metadata before concurrent work is accepted.

    Runtime registration is not quality qualification. Doing it once within the
    prepare advisory lock avoids a first-use unique-key race between executors.
    """
    from ...services.delivery_contracts import current_output_contract
    from ...services.tailoring_contract import COMPLETE_PROMPT_VERSION
    from .operations import _ensure_prompt_version

    linked: tuple
    function: object
    if auth.operation == "interview_questions":
        function, linked = llm_client.generate_interview_questions, (interview_contract.build_interview_messages,)
        version = interview_contract.PROMPT_VERSION
    elif auth.operation == "resume_tailor":
        function = llm_client.tailor_resume_from_evidence_full
        linked = (llm_client.build_tailoring_messages, llm_client.tailor_resume_from_evidence)
        version = COMPLETE_PROMPT_VERSION
    elif _sync().supports(auth.operation):
        from ...services import synchronous_contracts
        from ..credits import synchronous as executor

        function, linked = executor.generate_prepared, (synchronous_contracts.messages, synchronous_contracts.validate)
        version = _sync().prompt_version(auth.operation)
    else:
        from ...services import matching_contract

        function = llm_client.analyze_job_match_complete_llm
        linked = (matching_contract.messages, matching_contract.validate)
        version = matching_contract.PROMPT_VERSION
    _ensure_prompt_version(db, operation=auth.operation, version=version, callable_object=function,
        linked_callables=linked, contract_identity={"output_contract_sha256": current_output_contract(auth.operation)})


def prepare(factory, packet_root, native_root, auth, authorization_root, native_receipts):
    report = preflight(packet_root, native_root, auth, authorization_root, native_receipts, active=True)
    from ... import models
    from ...database import Base
    from ..common import utcnow
    from ..free_core.models import FreeCoreReservation

    with factory() as db:
        _identity(db, auth)
    engine = factory.kw["bind"]
    schema = _schema(auth)
    with engine.begin() as conn:
        conn.execute(text(f'CREATE SCHEMA IF NOT EXISTS "{schema}"'))
    if not set(inspect(engine).get_table_names(schema=schema)) <= set(Base.metadata.tables):
        raise ValueError("Unknown tables exist in this evaluation schema")
    Base.metadata.create_all(engine)
    # This already-authorized owned evaluation namespace has no complete
    # Alembic catalog. Install the identical real fence, never a no-op marker or
    # a claim that metadata.create_all qualified the public migration chain.
    from ..career import evidence_fence_definition, evidence_write_fence

    with engine.begin() as connection:
        exists = connection.execute(text("SELECT count(*) FROM pg_catalog.pg_trigger t "
            "JOIN pg_catalog.pg_class c ON c.oid=t.tgrelid "
            "JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace "
            "WHERE n.nspname=:schema AND c.relname='evidence_items' AND NOT t.tgisinternal"),
            {"schema": schema}).scalar_one()
        if exists == 0:
            evidence_fence_definition.install(connection, schema)
        evidence_write_fence.require_installed(connection, schema)
    rows, _ = inputs.native_inputs(packet_root, native_root, auth, authorization_root, native_receipts)
    expected = {_run_id(auth, side, row["case_id"]) for row in rows for side in inputs.SIDES}
    with factory() as db:
        _identity(db, auth)
        db.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": int(inputs.digest(schema)[:15], 16)})
        saved = db.query(models.AnalysisRun).all()
        if not saved and (db.query(FreeCoreReservation).first() is not None
                          or db.query(models.ModelCostLiability).first() is not None):
            raise ValueError("Detached obligations forbid reinitializing an erased experiment")
        if any(r.id not in expected or r.input_payload.get("_quality_evaluation", {}).get("authorization")
               != auth.model_dump(mode="json") for r in saved):
            raise ValueError("This schema already holds a different experiment/allocation")
        if db.query(models.User).count() == 0:
            db.add(models.User(email="synthetic-v2-" + schema + "@example.invalid", password_hash="", tier="free",
                               ai_credits=0, job_service_credits=0))
            db.flush()
        owner = _owner(db, auth)
        _register_prompt(db, auth)
        for row in rows:
            case, case_id = row["case"], row["case_id"]
            stem = inputs.digest([auth.experiment_id, case_id])[:35]
            opportunity_id = "qeo_" + stem
            opportunity = db.get(models.Opportunity, opportunity_id)
            if opportunity is None:
                raw, sections, skills, years, contact = row["parsed"]
                resume = models.Resume(user_id=owner.id, original_filename=case_id + "." + row["source_format"],
                    raw_text=raw, sections=sections,
                    source_document=row["source_bytes"], source_format=row["source_format"],
                    skills=skills, experience_years=years, contact_info=contact)
                db.add(resume)
                db.flush()
                opportunity = models.Opportunity(id=opportunity_id, user_id=owner.id, resume_id=resume.id,
                    title=case["job"]["title"], company="Synthetic evaluation", source="internal_evaluation",
                    job_description=case["job"]["description"], job_snapshot={"case_sha256": inputs.digest(case)})
                db.add(opportunity)
                from ..career.evidence_write_fence import flush_evidence_writes

                new_evidence = []
                for fact in case["resume"]["evidence"]:
                    if fact["approved_in_fixture"]:
                        item = models.EvidenceItem(id=fact["id"], user_id=owner.id, resume_id=resume.id,
                            category="synthetic", title=fact["id"], evidence_text=fact["text"], metrics={}, skills=[],
                            provenance="synthetic_fixture", approval_state="approved")
                        db.add(item)
                        new_evidence.append(item)
                if new_evidence:
                    flush_evidence_writes(db, new_evidence)
                db.flush()
            for side, route in auth.routes.items():
                identity = _run_id(auth, side, case_id)
                if db.get(models.AnalysisRun, identity) is not None:
                    continue
                payload = {"schema_version": VERSION, "mode": "enhanced", "num_questions": 8,
                    "resume_id": opportunity.resume_id, "opportunity_id": opportunity.id,
                    "_quality_evaluation": {"authorization": auth.model_dump(mode="json"), "side": side,
                        "case_id": case_id, "case_sha256": inputs.digest(case), "source_sha256": row["source_sha256"]}}
                if auth.operation == "job_match":
                    payload.update({"job_title": opportunity.title, "job_description": opportunity.job_description,
                                    "company": opportunity.company})
                run = models.AnalysisRun(id=identity, user_id=owner.id, opportunity_id=opportunity.id,
                    operation=auth.operation, status="queued", idempotency_key=identity, input_fingerprint="pending",
                    input_payload=payload, estimated_units=0, committed_units=0, usage_state="waived",
                    generation_attempt_limit=attempts(auth.operation), attempt_count=0, generation_attempt_count=0,
                    created_at=utcnow(), updated_at=utcnow())
                unsupported = False
                if _sync().supports(auth.operation):
                    from fastapi import HTTPException

                    from ...services.synchronous_contracts import SynchronousOutputUnavailable

                    try:
                        _sync().prepare_inputs(db, run)
                    except (SynchronousOutputUnavailable, HTTPException) as exc:
                        if isinstance(exc, HTTPException) and not (
                            exc.status_code in {409, 422} and isinstance(exc.detail, dict)
                            and exc.detail.get("code") == "synchronous_service_rejected"
                        ):
                            raise
                        # The full population remains visible. Missing current
                        # resources/support is not fabricated or sent to a model.
                        unsupported = True
                        run.status, run.error_code = "failed", "evaluation_unsupported_inputs"
                        run.error_message = "The fixed complete request lacks approved source support or fresh curated resources; no model attempt was made."
                        run.completed_at = utcnow()
                run.input_fingerprint = inputs.digest({"unsupported_input": run.input_payload}) if unsupported else _fingerprint(db, run)
                model_cost_policy.freeze_run_quote(run)
                run.model_cost_quote = dict(run.model_cost_quote) | {"pricing_quotes": [route.price.model_dump(mode="json")]}
                run.model_cost_ceiling_micros = min(run.model_cost_ceiling_micros,
                    model_cost_policy.quoted_cost(route.price.model_dump(), route.price.max_input_tokens,
                        route.price.max_output_tokens) * attempts(auth.operation))
                db.add(run)
                db.add(FreeCoreReservation(id="qen_" + inputs.digest(identity)[:55], user_id=owner.id,
                    operation=auth.operation, route_id="internal_quality_v2_not_retail",
                    policy_version=auth.expense_policy_sha256, funding_period_id=auth.experiment_id,
                    reserved_minor=auth.native_reserve_minor_per_run, settled_minor=None,
                    state="reserved", created_at=utcnow()))
        db.commit()
    return report


def execute(factory, packet_root, native_root, auth, authorization_root, native_receipts, *, side, maximum_operations=1):
    if side not in inputs.SIDES or type(maximum_operations) is not int or not 1 <= maximum_operations <= 10:
        raise ValueError("Select one side and one to ten finite operations")
    preflight(packet_root, native_root, auth, authorization_root, native_receipts, active=True)
    from ... import models
    from ..common import utcnow
    from . import operations

    route = auth.routes[side]
    if llm_client.LLM_MODEL != route.price.model or llm_client.LLM_API_BASE.rstrip("/") != route.price.api_base:
        raise ValueError("The process model/endpoint differs from its reviewed side")
    if not llm_client._api_key():
        raise ValueError("A private provider credential is required before claiming work")
    outcomes = {}
    for _ in range(maximum_operations):
        with factory() as db:
            _identity(db, auth)
            owner = _owner(db, auth)
            run = db.query(models.AnalysisRun).filter_by(user_id=owner.id, operation=auth.operation,
                status="queued").filter(models.AnalysisRun.input_payload["_quality_evaluation"]["side"].as_string() == side
                ).order_by(models.AnalysisRun.id).with_for_update(skip_locked=True).first()
            if run is None:
                break
            identity = run.id
            with _provider_scope(factory, auth, authorization_root, identity, packet_root):
                require_provider_authority(db, run)
            run.status, run.started_at, run.attempt_count = "running", utcnow(), 1
            db.commit()
        try:
            with _provider_scope(factory, auth, authorization_root, identity, packet_root):
                with generation_budget.persistent_run_budget(factory, identity):
                    with factory() as db:
                        run = db.get(models.AnalysisRun, identity)
                        require_provider_authority(db, run)
                        result = _sync().execute(db, run) if _sync().supports(auth.operation) else operations.execute_operation(db, run)
                        require_provider_authority(db, run)
                        events = db.query(models.ModelCallEvent).filter_by(analysis_run_id=identity).all()
                        if (not events or any(e.cost_state != "settled" or e.status != "succeeded" for e in events)
                                or run.model_cost_state == "overrun"):
                            raise ValueError("Completed delivery needs genuinely observed settled provider usage")
                        output = result["content"] if auth.operation == "resume_tailor" else result
                        case_id = run.input_payload["_quality_evaluation"]["case_id"]
                        case = next(c for c in inputs.selected_cases(packet_root, auth.split)[1] if c["case_id"] == case_id)
                        if not packet._output_contract("enhanced_job_match" if auth.operation == "job_match" else auth.operation, output, case):
                            raise ValueError("The production result failed the complete quality contract")
                        run.result_payload, run.status = result, "succeeded"
                        run.completed_at = run.updated_at = utcnow()
                        db.commit()
        except Exception:
            with factory() as db:
                run = db.query(models.AnalysisRun).filter_by(id=identity).with_for_update().one()
                if run.status == "running":
                    run.status, run.error_code = "failed", "evaluation_delivery_unavailable"
                    run.error_message = "No complete settled result; model/native obligations remain retained."
                    run.completed_at = run.updated_at = utcnow()
                    db.commit()
        with factory() as db:
            outcomes[identity] = db.get(models.AnalysisRun, identity).status
    return {"schema_version": VERSION, "outcomes": outcomes, "authorizes_retail": False,
            "native_actual_cost_minor": None, "resume_policy": "Only never-started queued slots; no running/failed/unknown redispatch"}


def export_results(factory, auth, destination, authorization_root):
    """Immutable private evidence; exact availability and expenses, no scores."""
    from ... import models
    from ...services.resume_artifacts import render_resume_version
    from ..free_core.models import FreeCoreReservation

    # Historical evidence remains exportable after prompt/code changes. This
    # does not grant execution authority or inspect any held-out output here.
    legacy._artifact(authorization_root, auth.funding_evidence)
    legacy._artifact(authorization_root, auth.provider_balance_evidence)
    if destination.exists() or not destination.is_absolute():
        raise ValueError("Use a fresh absolute private export directory")
    destination.mkdir(mode=0o700)
    records = []
    with factory() as db:
        _identity(db, auth)
        owner = _owner(db, auth)
        rows = db.query(models.AnalysisRun).filter_by(user_id=owner.id).order_by(models.AnalysisRun.id).all()
        if len(rows) != (80 if auth.split == "development" else 320):
            raise ValueError("Every selected split slot must remain visible")
        for run in rows:
            metadata = run.input_payload["_quality_evaluation"]
            if metadata["authorization"] != auth.model_dump(mode="json"):
                raise ValueError("An export cannot mix experiment authorizations")
            folder = destination / run.id
            folder.mkdir(mode=0o700)
            liabilities = db.query(models.ModelCostLiability).filter_by(financial_group_id=run.model_cost_group_id).all() if run.model_cost_group_id else []
            native = db.get(FreeCoreReservation, "qen_" + inputs.digest(run.id)[:55])
            record = {"run_id": run.id, "case_id": metadata["case_id"], "side": metadata["side"], "state": run.status,
                "case_sha256": metadata["case_sha256"], "source_sha256": metadata["source_sha256"],
                "model_liabilities": [{"id": liability.id, "cost_state": liability.cost_state,
                    "reserved_cost_micros": liability.reserved_cost_micros, "settled_cost_micros": liability.settled_cost_micros,
                    "usage_provenance": liability.usage_provenance} for liability in liabilities],
                "native_obligation": {"id": native.id, "reserved_minor": native.reserved_minor,
                    "settled_minor": native.settled_minor, "state": native.state},
                "available": run.status == "succeeded", "quality_qualified": False, "retail_qualified": False}
            if run.status == "succeeded":
                output = run.result_payload["content"] if auth.operation == "resume_tailor" else run.result_payload
                path = folder / "output.json"
                path.write_bytes(packet.canonical(output))
                path.chmod(0o600)
                record["output"] = {"path": str(path.relative_to(destination)), "sha256": packet.file_digest(path)}
                if auth.operation == "resume_tailor":
                    version = db.get(models.ResumeVersion, run.result_payload["resume_version_id"])
                    resume = db.get(models.Resume, version.resume_id)
                    artifact = render_resume_version(version, resume, resume.source_format)
                    path = folder / ("tailored." + resume.source_format)
                    path.write_bytes(artifact.content)
                    path.chmod(0o600)
                    record["native_artifact"] = {"path": str(path.relative_to(destination)), "sha256": packet.file_digest(path)}
                    record["native_output_review"] = "pending_independent_native_and_human_validation"
            records.append(record)
    report = {"schema_version": VERSION, "operation": auth.operation, "split": auth.split,
              "routes": {side: route.model_dump(mode="json") for side, route in auth.routes.items()},
              "manifest_sha256": inputs.MANIFEST_SHA, "protocol_sha256": inputs.PROTOCOL_SHA,
              "rubric_sha256": json.loads((Path(__file__).resolve().parents[3] / "evals/unified_credit_v2/manifest.json").read_bytes())["rubric_sha256"],
              "authorization_sha256": inputs.digest(auth.model_dump(mode="json")),
              "records": records, "authorizes_retail": False, "quality_qualified": False,
              "native_cost_unknown_is_zero": False, "human_scores": None,
              "limitations": ["Operator-reviewed native imports are not supplier signatures.",
                             "This internal expense export cannot qualify retail unit economics."]}
    path = destination / "evaluation-export.json"
    path.write_bytes(packet.canonical(report))
    path.chmod(0o600)
    return {"records": len(records), "manifest_sha256": packet.file_digest(path), "authorizes_retail": False}
