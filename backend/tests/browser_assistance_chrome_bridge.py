"""Owned stdin bridge for Chrome integration; actual SQL/crypto, no HTTP provider."""
from __future__ import annotations

import json
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
            elif operation == "candidate":
                result = service.candidate(fixture.db, 1, incoming["action"], schemas.CandidateRequest(**incoming["body"]))
            elif operation == "audit":
                fixture.db.refresh(fixture.app)
                result = {"charged_credits": fixture.app.charged_credits, "application_status": fixture.app.status,
                          "field_count": fixture.db.query(loaded["models"].BrowserAssistanceRequest).filter_by(operation="field").count()}
            else:
                raise ValueError("Unsupported owned test operation")
            print(json.dumps({"status": 200, "body": result}), flush=True)
        except HTTPException as failure:
            fixture.db.rollback()
            print(json.dumps({"status": failure.status_code, "body": {"detail": "Owned request refused"}}), flush=True)
finally:
    fixture.tearDown()
