"""Owned stdin bridge for Chrome integration; actual SQL/crypto, no HTTP provider."""
from __future__ import annotations

import base64
import json
import os
import runpy
import sys
from pathlib import Path

from fastapi import HTTPException

loaded = runpy.run_path(str(Path(__file__).with_name("test_browser_assistance_isolated.py")))
fixture = loaded["BrowserAssistanceFlow"]()
fixture.setUp()
service, schemas = loaded["service"], loaded["schemas"]
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
