"""Owned stdin bridge: genuine enrollment/native pairing to actual SQL assistance.

All SDK, KMS and credential calls use the existing intercepted local fixtures.
Chrome supplies its actual generated public key and original P-256 signatures.
No provider, employer portal, pre-seeded account/device or browser claim adoption.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import sys
import traceback
from datetime import UTC, datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT.parent), str(ROOT), str(ROOT / "tests")]

import pytest  # noqa: E402
import test_browser_pairing_production as pairing_fixture_module  # noqa: E402
import test_candidate_production_factory as account_fixture_module  # noqa: E402
from fastapi import HTTPException  # noqa: E402
from test_browser_pairing_production import (  # noqa: E402
    EXTENSION,
    candidate,
    pairing,
)
from test_candidate_production_factory import NOW, PASSWORD, configured  # noqa: E402
from test_pairing_kms import native as kms  # noqa: E402

from app import models as core  # noqa: E402

# Register the real browser-assistance tables before the private Base.create_all.
from app.domains.browser_assistance import models, schemas, service  # noqa: E402
from app.domains.employer import models as employer  # noqa: E402
from app.domains.employer.service import _package_digest  # noqa: E402
from app.domains.recovery.browser_pairing import production_browser_pairing  # noqa: E402
from app.domains.recovery.contracts import canonical  # noqa: E402
from app.domains.recovery.store import GuardDenied, GuardUnavailable  # noqa: E402
from app.observability import JsonFormatter  # noqa: E402

# Preserve the real finite native diagnostic in this owned harness, so expected
# denial tests can assert its exact labels while every unexpected stderr fails.
native_handler = logging.StreamHandler()
native_handler.setFormatter(JsonFormatter())
native_logger = logging.getLogger("native_boundary")
native_logger.addHandler(native_handler)
native_logger.propagate = False

APPLICATION = "--application" in sys.argv
UNIFIED_SUBMISSION = "--unified" in sys.argv
FREE_ONLY = "--free-only" in sys.argv
if (UNIFIED_SUBMISSION or FREE_ONLY) and not APPLICATION or UNIFIED_SUBMISSION and FREE_ONLY:
    raise ValueError("Select one explicit owned application mode")
if UNIFIED_SUBMISSION or FREE_ONLY:
    # Register actual wallet tables BEFORE the existing owned factory creates
    # its private schema. This does not create or select any native authority.
    from app.domains.credits import models as credit_models  # noqa: E402
REVISION = "companion_0_8_0" if APPLICATION else "companion_0_7_0"
patch = pytest.MonkeyPatch()


def current_clock():
    return int(datetime.now(UTC).timestamp() * 1000)


def configure_unified_submission(db, user_id):
    """Synthetic funded test envelope; never a production release review."""
    from app.domains.common import payload_fingerprint
    from app.domains.credits import admission, finance, release, runtime, service_quotes, submission
    from app.domains.credits import service as credit_service

    stamp = service.now()
    policy = finance.FinancialPolicy(version="owned-browser-synthetic-v1",
        valid_from=stamp - timedelta(days=1), expires_at=stamp + timedelta(days=29),
        provenance="synthetic_test", evidence_sha256="a" * 64)
    route = service_quotes.QualifiedRoute(id="owned-native-submission", version="synthetic-v1",
        operation="verified_submission", tariff_version="credit-services-2026-10-27-v1",
        credits=3, mode="native", deliverable="Owned synthetic final submission validator result",
        supported_limits={"artifact_bytes": 10 * 1024 * 1024, "answer_count": 64, "review_bytes": 65536},
        qualification_id="owned-synthetic-not-native-proof", qualification_evidence_sha256="b" * 64,
        output_contract_sha256=submission.delivery_contract(), qualified_from=stamp - timedelta(days=1),
        qualified_until=stamp + timedelta(days=29), native_cost_evidence_sha256="c" * 64,
        native_processing_minor=13, worker_processing_minor=0, model_quotes=())
    budgets = admission.ExecutionBudgetPolicy(version="owned-synthetic-funding-v1", evidence_sha256="d" * 64,
        valid_from=stamp - timedelta(days=1), expires_at=stamp + timedelta(days=29),
        daily_global_minor=10000, monthly_global_minor=10000, monthly_account_minor=10000,
        monthly_incident_minor=10000, monthly_route_minor={route.id: 10000},
        legacy_search_cost_minor=10, legacy_application_cost_minor=30, legacy_preparation_minor=10,
        legacy_provider_attempt_overhead_minor=2)
    configured_runtime = runtime.RuntimeConfig(policy=policy, budgets=budgets, routes=(route,))
    patch.setenv("UNIFIED_CREDIT_EXECUTION_ENABLED", "true")
    patch.setenv("UNIFIED_BROWSER_SUBMISSION_ENABLED", "true")
    patch.setenv("UNIFIED_CREDIT_EXECUTION_AUDIENCE", "reviewed_public")
    patch.setenv("UNIFIED_CREDIT_RUNTIME_REVIEW_JSON", configured_runtime.model_dump_json())
    patch.setattr(release, "current_review", lambda **_: SimpleNamespace(policy=policy,
        evidence={"delivery_routes": payload_fingerprint(configured_runtime.model_dump(mode="json"))}))
    credit_service.grant_credits(db, user_id=user_id, quantity=40, source_type="payment_order",
        source_id="owned-synthetic-unified-browser", funding_kind="paid",
        terms_snapshot={"owned_synthetic_test_only": True}, expires_at=stamp + timedelta(days=1), now=stamp)


if APPLICATION:
    NOW = current_clock()
    patch.setattr(account_fixture_module, "NOW", NOW)
    patch.setattr(pairing_fixture_module, "NOW", NOW)
with TemporaryDirectory(prefix="hirewiz-paired-chrome-sql-") as directory:
    account_fixture = configured.__wrapped__(patch, Path(directory))
    c = next(account_fixture)
    kms_fixture = kms.__wrapped__(patch)
    signing = next(kms_fixture)
    try:
        c = pairing.__wrapped__(c, signing, patch)
        c.factory_config["executor_revision"] = REVISION
        patch.setenv("BROWSER_PAIRING_NATIVE_CONFIG", canonical(c.factory_config))
        c.native.seed(
            c.execution_registry,
            "pairing_extensions",
            f"{EXTENSION}:{REVISION}",
            {
                "extension_id": EXTENSION,
                "executor_revision": REVISION,
                "protocol_version": 2,
                "active": True,
            },
        )
        c.browser = production_browser_pairing()
        patch.setenv("BROWSER_ASSISTANCE_ENABLED", "true")
        patch.setenv("BROWSER_ASSISTANCE_NATIVE_PAIRING_ENABLED", "true")
        patch.setenv(
            "BROWSER_ASSISTANCE_SIGNING_SECRET",
            "owned-synthetic-paired-browser-assistance-signing-only",
        )
        patch.setattr(service, "now", lambda: datetime.fromtimestamp(NOW / 1000, UTC))
        uid = c.accounts.register(
            email="paired-browser-owned@example.test",
            password=PASSWORD,
            policy_version="2026-10-08",
        )
        context = c.accounts.login(
            email="paired-browser-owned@example.test", password=PASSWORD
        ).context
        assert context.candidate_id == uid
        if APPLICATION:
            from expense_policy_fixtures import synthetic_expense_policy

            patch.setenv("APP_ENV", "test")
            for key in (
                "BROWSER_ASSISTANCE_TYPED_CONTROLS_ENABLED",
                "BROWSER_ASSISTANCE_ATTACHMENT_ENABLED",
                "BROWSER_ASSISTANCE_SUBMIT_ENABLED",
                "BROWSER_ASSISTANCE_NATIVE_UPLOAD_ENABLED",
                "BROWSER_SUBMIT_NATIVE_ENABLED",
            ):
                patch.setenv(key, "true")
            patch.setenv("BROWSER_ASSISTANCE_SUBMIT_EXECUTOR_REVISION", REVISION)
            patch.setenv(
                "BROWSER_SUBMIT_NATIVE_CONFIG",
                canonical(
                    {
                        **c.config.model_dump(mode="json"),
                        "device_enrollment": "paired",
                        "executor_revision": REVISION,
                    }
                ),
            )
            policy = synthetic_expense_policy()
            policy["service_apply_credits"] = 20
            patch.setenv("HIREWIZ_EXPENSE_POLICY_JSON", json.dumps(policy))
            patch.setenv("EMPLOYER_APPLY_CREDITS_PER_JOB", "20")
            if FREE_ONLY:
                for key in ("BROWSER_ASSISTANCE_SUBMIT_ENABLED", "BROWSER_SUBMIT_NATIVE_ENABLED",
                        "EMPLOYER_AUTO_SUBMIT_ENABLED", "UNIFIED_BROWSER_SUBMISSION_ENABLED",
                        "UNIFIED_CREDIT_EXECUTION_ENABLED"):
                    patch.setenv(key, "false")
            content = b"%PDF-1.4\nOwned local candidate resume fixture only\n%%EOF\n"
            mapping = [
                {"id": identity, "label": label, "type": "text", "required": True}
                for identity, label in service.FIELDS[:3]
            ]
            values = {
                "first_name": "Owned",
                "last_name": "Synthetic",
                "email": "owned@example.test",
            }
            target = service.ORIGIN + "/" + service.TENANT + "/jobs/4740333005"
            with c.accounts.sessions() as db:
                db.get(core.User, uid).job_service_credits = 100
                db.add(
                    core.Resume(
                        id=10,
                        user_id=uid,
                        original_filename="Owned.pdf",
                        source_format="pdf",
                        source_document=content,
                        skills=[],
                        raw_text="Owned synthetic resume",
                    )
                )
                source = employer.EmployerSource(
                    id="source_owned",
                    employer="Razorpay",
                    platform="greenhouse",
                    board_token=service.TENANT,
                    region="global",
                    careers_url=target,
                    allowed_hosts=["job-boards.greenhouse.io", "boards.greenhouse.io"],
                    verification_url=target,
                    verification_note="Owned local fixture only",
                    status="healthy",
                    last_success_at=service.now(),
                )
                db.add(source)
                posting = employer.EmployerPosting(
                    id="posting_owned",
                    source_id=source.id,
                    external_id="4740333005",
                    title="Owned software opening",
                    employer="Razorpay",
                    location="Owned fixture",
                    description="Owned local fixture",
                    canonical_url=target,
                    apply_url=target,
                    content_sha256="a" * 64,
                    last_checked_at=service.now(),
                    skills=[],
                )
                db.add(posting)
                artifact = employer.SealedApplicationArtifact(
                    id="artifact_owned",
                    user_id=uid,
                    resume_id=10,
                    sha256=hashlib.sha256(content).hexdigest(),
                    filename="Owned.pdf",
                    media_type="application/pdf",
                    size_bytes=len(content),
                    content=content,
                    gcs_generation="1",
                )
                db.add(artifact)
                db.flush()
                application = employer.EmployerApplication(
                    id="apply_owned",
                    user_id=uid,
                    posting_id=posting.id,
                    idempotency_key="owned_native_application",
                    input_fingerprint="f" * 64,
                    resume_id=10,
                    resume_choice="original",
                    artifact_id=artifact.id,
                    form={"version": "owned_form", "fields": mapping},
                    answers=values,
                    consents={},
                    package_digest="d" * 64,
                    job_content_sha256=posting.content_sha256,
                    application_mode="manual",
                    status="ready",
                    credit_cost=0,
                )
                application.package_digest = _package_digest(application, posting, source, artifact)
                db.add(application)
                if UNIFIED_SUBMISSION:
                    configure_unified_submission(db, uid)
                db.commit()
        for line in sys.stdin:
            request = json.loads(line)
            if APPLICATION:
                NOW = current_clock()
                account_fixture_module.NOW = pairing_fixture_module.NOW = NOW
                signing.now = NOW
            try:
                operation = request["operation"]
                if operation == "configuration":
                    result = {
                        "now_ms": NOW,
                        "authority_key": signing.pin.public_key,
                        "executor_revision": REVISION,
                        "extension_id": EXTENSION,
                        "candidate_id": uid,
                    }
                    if APPLICATION:
                        result.update(
                            application_id="apply_owned",
                            target_url=target,
                            field_mapping=mapping,
                            answers=values,
                            unified_submission=UNIFIED_SUBMISSION,
                            free_only=FREE_ONLY,
                        )
                elif operation == "pairing_device":
                    result = c.browser.device(
                        request["action"], canonical(request["body"]).encode(), request["origin"]
                    )
                elif operation == "candidate_fixture_approve":
                    # This server-side helper is synthetic candidate BFF input,
                    # never an installed extension action or provider API.
                    challenge = candidate(
                        c, context, "challenge", {"pairing_id": request["pairing_id"]}
                    )
                    result = candidate(
                        c,
                        context,
                        "confirm",
                        {
                            "protocol_version": 2,
                            "operation": "confirm_pairing",
                            "challenge_id": challenge["challenge_id"],
                            "nonce": challenge["nonce"],
                            "password": "owned-wrong-password"
                            if request.get("wrong_password")
                            else PASSWORD,
                            "confirmed": True,
                        },
                    )
                elif operation == "assistance_device":
                    with c.accounts.sessions() as db:
                        result = service.device(
                            db,
                            request["origin"],
                            schemas.DeviceRequest.model_validate(request["body"]),
                        )
                        if isinstance(result, service.attachments.BinaryDelivery):
                            result = {
                                "binary_base64": base64.b64encode(result.content).decode(),
                                "descriptor": result.descriptor,
                            }
                elif operation == "assistance_candidate" and APPLICATION:
                    with c.accounts.sessions() as db:
                        result = service.candidate(
                            db,
                            uid,
                            request["action"],
                            schemas.CandidateRequest.model_validate(request["body"]),
                        )
                elif operation == "fault":
                    if request["fault"] == "native_completion_commit_loss":
                        c.native.fault = lambda names: (
                            "persist_loss" if "devices" in names else None
                        )
                    elif request["fault"] == "clear":
                        c.native.fault = None
                    else:
                        raise ValueError("Unsupported owned fault")
                    result = {"fault_applied": True}
                elif operation == "audit":
                    with c.accounts.sessions() as db:
                        devices = db.query(models.BrowserAssistanceDevice).all()
                        result = {
                            "devices": [
                                {
                                    "id": device.id,
                                    "user_id": device.user_id,
                                    "state": device.state,
                                    "key_sha256": device.key_sha256,
                                }
                                for device in devices
                            ],
                            "application_actions": db.query(
                                models.BrowserAssistanceRequest
                            ).count(),
                            "protected_devices": sum(
                                namespace == "v3_submit_devices"
                                for namespace, _ in c.native.values()
                            ),
                            "signatures": sum(kind == "sign" for kind, _, _ in signing.calls),
                        }
                        if APPLICATION:
                            application = db.get(employer.EmployerApplication, "apply_owned")
                            result.update(
                                application_status=application.status,
                                charged_credits=application.charged_credits,
                                receipt=application.receipt,
                                credits=db.get(core.User, uid).job_service_credits,
                                submit_attempts=db.query(
                                    employer.EmployerApplicationAttempt
                                ).count(),
                                binary_deliveries=db.query(models.BrowserAssistanceRequest)
                                .filter_by(operation="artifact")
                                .count(),
                            )
                            if UNIFIED_SUBMISSION or FREE_ONLY:
                                from app.domains.credits import service as credit_service
                                wallet = credit_service.wallet(db, user_id=uid)
                                result.update(unified_available_credits=wallet.available_credits,
                                    unified_reserved_credits=wallet.reserved_credits,
                                    unified_reservations=[{"state": row.state, "reserved": row.reserved_credits,
                                        "committed": row.committed_credits, "released": row.released_credits}
                                        for row in db.query(credit_models.CreditReservation).all()])
                                result["final_submit_enabled"] = service.submissions.enabled()
                else:
                    raise ValueError("Unsupported owned operation")
                print(json.dumps({"status": 200, "body": result}), flush=True)
            except HTTPException as error:
                print(
                    json.dumps(
                        {
                            "status": error.status_code,
                            "body": {
                                "detail": "Owned request refused",
                                "frames": [
                                    [Path(frame.filename).name, frame.lineno, frame.name]
                                    for frame in traceback.extract_tb(error.__traceback__)
                                ],
                            },
                        }
                    ),
                    flush=True,
                )
            except GuardDenied:
                print(
                    json.dumps({"status": 403, "body": {"detail": "Owned native proof refused"}}),
                    flush=True,
                )
            except GuardUnavailable:
                print(
                    json.dumps(
                        {"status": 503, "body": {"detail": "Owned native boundary unavailable"}}
                    ),
                    flush=True,
                )
    finally:
        kms_fixture.close()
        account_fixture.close()
        patch.undo()
