"""Installed MV3 bridge over actual synthetic ORM, cryptography and credit ledger."""

import base64
import json
import sys
import warnings

warnings.filterwarnings("ignore", message="Using .*starlette.testclient.*")
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from pytest import MonkeyPatch
from fastapi import HTTPException
from backend.tests.browser_submit_owned import OwnedSubmit
from backend.app.domains.browser_assistance import schemas, service

patch = MonkeyPatch()
owned = OwnedSubmit(patch)
owned.target = owned.target.replace("4740333005", "4723040005")
posting = owned.db.get(
    __import__("backend.app.domains.employer.models", fromlist=["EmployerPosting"]).EmployerPosting,
    "job_1",
)
posting.external_id = "4723040005"
posting.apply_url = posting.canonical_url = owned.target
from backend.app.domains.employer.service import _package_digest

source = owned.db.get(
    __import__("backend.app.domains.employer.models", fromlist=["EmployerSource"]).EmployerSource,
    "source_test",
)
artifact = owned.db.get(
    __import__(
        "backend.app.domains.employer.models", fromlist=["SealedApplicationArtifact"]
    ).SealedApplicationArtifact,
    "artifact_owned",
)
owned.app.package_digest = _package_digest(owned.app, posting, source, artifact)
owned.db.commit()
try:
    for line in sys.stdin:
        message = json.loads(line)
        try:
            if message["operation"] == "configure_submit":
                result = {"field_mapping": owned.mapping, "answers": owned.values}
            elif message["operation"] == "device":
                request = schemas.DeviceRequest(**message["body"])
                if request.operation == "prepare":
                    owned.device_id = str(request.device_id)
                result = service.device(owned.db, message["origin"], request)
                if isinstance(result, service.attachments.BinaryDelivery):
                    result = {
                        "binary_base64": base64.b64encode(result.content).decode(),
                        "descriptor": result.descriptor,
                    }
            elif message["operation"] == "candidate":
                result = service.candidate(
                    owned.db, 1, message["action"], schemas.CandidateRequest(**message["body"])
                )
            elif message["operation"] == "audit":
                result = owned.audit()
            else:
                raise ValueError("Unsupported owned submit bridge operation")
            print(json.dumps({"status": 200, "body": result}), flush=True)
        except HTTPException as error:
            owned.db.rollback()
            print(
                json.dumps(
                    {"status": error.status_code, "body": {"detail": "Owned request refused"}}
                ),
                flush=True,
            )
finally:
    owned.close()
    patch.undo()
