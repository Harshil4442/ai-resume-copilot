"""Owned stdin bridge for Chrome integration; actual SQL/crypto, no HTTP provider."""
from __future__ import annotations

import ast
import base64
import copy
import json
import os
import re
import runpy
import sys
import types
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException

loaded = runpy.run_path(str(Path(__file__).with_name("test_browser_assistance_isolated.py")))
fixture = loaded["BrowserAssistanceFlow"]()
fixture.setUp()
service, schemas = loaded["service"], loaded["schemas"]


def save_resume_choice(choice):
    """Use the shipping package update and renderer with owned test data only."""
    from pypdf import PdfReader
    from reportlab.pdfgen.canvas import Canvas

    assert choice in {"custom", "tailored"}
    root, prefix, load = (loaded[name] for name in ("ROOT", "PREFIX", "load"))
    for suffix in (".services", ".billing", ".domains.credits"):
        module = sys.modules.setdefault(prefix + suffix, types.ModuleType(prefix + suffix))
        module.__path__ = []
    layout = load(prefix + ".services.resume_layout", root / "services/resume_layout.py")
    load(prefix + ".services.pdf_streams", root / "services/pdf_streams.py")
    load(prefix + ".services.resume_artifacts", root / "services/resume_artifacts.py")
    load(prefix + ".billing.catalog", root / "billing/catalog.py")
    load(prefix + ".billing.cost_policy", root / "billing/cost_policy.py")
    config = load(prefix + ".domains.employer.config", root / "domains/employer/config.py")
    admissions = load(prefix + ".domains.employer.admissions", root / "domains/employer/admissions.py")
    artifacts = load(prefix + ".domains.employer.artifacts", root / "domains/employer/artifacts.py")
    # Unquoted manual fixture. Use the real classifier without loading billing
    # execution, and make any attempt to enter that path fail in this test.
    credits = types.ModuleType(prefix + ".domains.credits.submission")
    credit_contract = types.ModuleType(prefix + ".domains.credits.contract")
    contract_tree = ast.parse((root / "domains/credits/submission_contract.py").read_text())
    billing_version = next(node for node in contract_tree.body if isinstance(node, ast.AnnAssign)
        and isinstance(node.target, ast.Name) and node.target.id == "BILLING_VERSION")
    credit_contract.BILLING_VERSION = ast.literal_eval(billing_version.value)
    credits.contract = credit_contract
    credit_tree = ast.parse((root / "domains/credits/submission.py").read_text())
    classifier = next(node for node in credit_tree.body if isinstance(node, ast.FunctionDef) and node.name == "is_unified")
    exec(compile(ast.Module(body=[classifier], type_ignores=[]), "owned_application_billing_classifier", "exec"), credits.__dict__)
    sys.modules[credits.__name__] = credits

    original_text = "Built scalable systems with Python and SQL."
    tailored_text = "Built systems with Python and SQL."
    content = BytesIO()
    canvas = Canvas(content, pagesize=(612, 792), invariant=True)
    canvas.setFont("Times-Bold", 12)
    canvas.drawString(48, 704, "EXPERIENCE")
    canvas.setFont("Times-Roman", 11)
    canvas.drawString(60, 662, original_text if choice == "tailored" else "Built custom reporting with Python and SQL.")
    canvas.save()
    source_bytes = content.getvalue()
    core, employer = loaded["core"], loaded["employer"]
    resume_id = 2 if choice == "custom" else fixture.app.resume_id
    resume = fixture.db.get(core.Resume, resume_id) or core.Resume(id=resume_id)
    resume.user_id, resume.source_format, resume.source_document = 1, "pdf", source_bytes
    resume.original_filename = "Synthetic-custom.pdf" if choice == "custom" else "Synthetic-source.pdf"
    fixture.db.add(resume)
    version_id = None
    if choice == "tailored":
        version_id, evidence_id = "owned_tailored", "owned_evidence"
        unit = next(row for row in layout.extract_source_units(source_bytes, "pdf") if row["text"] == original_text)
        fixture.db.add(core.EvidenceItem(id=evidence_id, user_id=1, approval_state="approved"))
        fixture.db.add(core.ResumeVersion(id=version_id, user_id=1, resume_id=resume_id,
            label="Owned tailored", version_number=1, approval_state="approved", evidence_ids=[evidence_id],
            structured_content={"format_preservation": "source", "source_format": "pdf", "source_edits": [{
                "unit_id": unit["unit_id"], "original_text": original_text,
                "replacement_text": tailored_text, "evidence_ids": [evidence_id],
                "reason": "Concise wording using the same source facts.",
            }]}))
    fixture.db.commit()

    shipping = loaded["employer_service"]
    select = {"_artifact_row", "_posting", "_quote_application", "_selection_snapshot", "_source", "_validate_answers", "missing_fields"}
    nodes = [node for node in loaded["tree"].body if isinstance(node, ast.FunctionDef) and node.name in select]
    namespace = {"core": core, "artifacts": artifacts, "config": config, "admissions": admissions,
        "SimpleNamespace": types.SimpleNamespace, "copy": copy, "re": re,
        "public_id": loaded["common"].public_id, "utcnow": loaded["common"].utcnow}
    # Persist the finite real digest dependencies for subsequent bridge reads.
    shipping.admissions, shipping.config = admissions, config
    with patch.dict(shipping.__dict__, namespace), patch.dict(os.environ, {
        "APP_ENV": "test", "EMPLOYER_ARTIFACT_BUCKET": "", "HIREWIZ_EXPENSE_POLICY_JSON": "",
    }):
        exec(compile(ast.Module(body=nodes, type_ignores=[]), "owned_application_package_update", "exec"), shipping.__dict__)
        app = shipping.update_package(fixture.db, 1, fixture.app.id, loaded["employer_schemas"].PackageUpdate(
            resume_id=resume_id, resume_choice=choice, resume_version_id=version_id, answers=fixture.app.answers, consents={}))
        artifact = fixture.db.get(employer.SealedApplicationArtifact, app.artifact_id)
        if choice == "custom":
            assert artifact.content == source_bytes
        else:
            rendered_text = PdfReader(BytesIO(artifact.content)).pages[0].extract_text()
            assert artifact.content != source_bytes and tailored_text in rendered_text and original_text not in rendered_text
        descriptor = service.attachments.descriptor(app, artifact)
        return {"package_digest": app.package_digest, "artifact": descriptor}


try:
    for line in sys.stdin:
        incoming = json.loads(line)
        try:
            operation = incoming["operation"]
            if operation == "device":
                result = service.device(fixture.db, incoming["origin"], schemas.DeviceRequest(**incoming["body"]))
                if isinstance(result, service.attachments.BinaryDelivery):
                    result = {"binary_base64": base64.b64encode(result.content).decode("ascii"), "descriptor": result.descriptor}
            elif operation == "candidate":
                result = service.candidate(fixture.db, 1, incoming["action"], schemas.CandidateRequest(**incoming["body"]))
            elif operation == "audit":
                fixture.db.refresh(fixture.app)
                result = {"charged_credits": fixture.app.charged_credits, "application_status": fixture.app.status,
                          "field_count": fixture.db.query(loaded["models"].BrowserAssistanceRequest).filter_by(operation="field").count()}
                if incoming.get("detailed"):
                    review = fixture.db.query(loaded["models"].BrowserAssistanceReview).one()
                    result.update(review_state=review.state, next_index=review.next_index,
                                  review_protocol_version=review.review_protocol_version,
                                  receipt=fixture.app.receipt,
                                  reservation_count=fixture.db.query(loaded["employer"].ServiceCreditReservation).count())
                    if incoming.get("attachment"):
                        actions = fixture.db.query(loaded["models"].BrowserAssistanceRequest).filter_by(operation="attachment").all()
                        result.update(attachment_count=len(actions),
                            delivery_count=fixture.db.query(loaded["models"].BrowserAssistanceRequest).filter_by(operation="artifact").count(),
                            delivery_claimed=any(action.response.get("delivery_claimed") for action in actions))
            elif operation == "configure_attachment":
                os.environ["BROWSER_ASSISTANCE_ATTACHMENT_ENABLED"] = "true"
                result = {"field_mapping": service.field_mapping(fixture.app, 3), "answers": fixture.app.answers}
            elif operation == "save_resume_choice":
                result = save_resume_choice(incoming["choice"])
            elif operation == "attachment_fault":
                fault = incoming["fault"]
                if fault == "revoke":
                    fixture.db.query(loaded["models"].BrowserAssistanceDevice).one().state = "revoked"
                elif fault == "cancel":
                    review = fixture.db.query(loaded["models"].BrowserAssistanceReview).one()
                    review.state, review.payload = "cancelled", {}
                elif fault == "expired":
                    fixture.db.query(loaded["models"].BrowserAssistanceReview).one().expires_at = service.now() - service.timedelta(seconds=1)
                elif fault == "flag_off":
                    os.environ["BROWSER_ASSISTANCE_ATTACHMENT_ENABLED"] = "false"
                elif fault == "artifact_hash":
                    fixture.db.query(loaded["employer"].SealedApplicationArtifact).one().content = b"Tampered synthetic bytes"
                else:
                    raise ValueError("Unsupported owned attachment fault")
                fixture.db.commit()
                result = {"fault_applied": True}
            elif operation == "configure_hosted":
                os.environ["BROWSER_ASSISTANCE_TYPED_CONTROLS_ENABLED"] = "true"
                fixture.configure_typed_application()
                rows = [{"index": i, "current_role": False} for i in range(incoming.get("rows", 1))]
                result = {"field_mapping": service.field_mapping(fixture.app, 2, rows), "answers": fixture.app.answers}
            elif operation == "configure_typed":
                # Owned synthetic application only; no browser/candidate authority
                # is manufactured here. Pairing and approval still use the real service.
                os.environ["BROWSER_ASSISTANCE_TYPED_CONTROLS_ENABLED"] = "true"
                fixture.configure_typed_application(omit_gender=incoming.get("omit_gender", False))
                result = {"field_mapping": service.field_mapping(fixture.app, 2), "answers": fixture.app.answers}
            else:
                raise ValueError("Unsupported owned test operation")
            print(json.dumps({"status": 200, "body": result}), flush=True)
        except HTTPException as failure:
            fixture.db.rollback()
            print(json.dumps({"status": failure.status_code, "body": {"detail": "Owned request refused"}}), flush=True)
finally:
    fixture.tearDown()
