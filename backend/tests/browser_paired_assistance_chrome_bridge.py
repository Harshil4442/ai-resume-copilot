"""Owned stdin bridge: genuine enrollment/native pairing to actual SQL assistance.

All SDK, KMS and credential calls use the existing intercepted local fixtures.
Chrome supplies its actual generated public key and original P-256 signatures.
No provider, employer portal, pre-seeded account/device or browser claim adoption.
"""
from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT.parent), str(ROOT), str(ROOT / "tests")]

import pytest  # noqa: E402
from fastapi import HTTPException  # noqa: E402
from test_browser_pairing_production import (  # noqa: E402
    EXTENSION,
    candidate,
    pairing,
)
from test_candidate_production_factory import NOW, PASSWORD, configured  # noqa: E402
from test_pairing_kms import native as kms  # noqa: E402

# Register the real browser-assistance tables before the private Base.create_all.
from app.domains.browser_assistance import models, schemas, service  # noqa: E402
from app.domains.recovery.browser_pairing import production_browser_pairing  # noqa: E402
from app.domains.recovery.contracts import canonical  # noqa: E402
from app.domains.recovery.store import GuardDenied, GuardUnavailable  # noqa: E402

REVISION = "companion_0_7_0"
patch = pytest.MonkeyPatch()
with TemporaryDirectory(prefix="hirewiz-paired-chrome-sql-") as directory:
    account_fixture = configured.__wrapped__(patch, Path(directory))
    c = next(account_fixture)
    kms_fixture = kms.__wrapped__(patch)
    signing = next(kms_fixture)
    try:
        c = pairing.__wrapped__(c, signing, patch)
        c.factory_config["executor_revision"] = REVISION
        patch.setenv("BROWSER_PAIRING_NATIVE_CONFIG", canonical(c.factory_config))
        c.native.seed(c.execution_registry, "pairing_extensions", f"{EXTENSION}:{REVISION}",
            {"extension_id":EXTENSION, "executor_revision":REVISION,"protocol_version":2,"active":True})
        c.browser = production_browser_pairing()
        patch.setenv("BROWSER_ASSISTANCE_ENABLED", "true")
        patch.setenv("BROWSER_ASSISTANCE_NATIVE_PAIRING_ENABLED", "true")
        patch.setenv("BROWSER_ASSISTANCE_SIGNING_SECRET", "owned-synthetic-paired-browser-assistance-signing-only")
        patch.setattr(service, "now", lambda: datetime.fromtimestamp(NOW / 1000, UTC))
        uid = c.accounts.register(email="paired-browser-owned@example.test", password=PASSWORD, policy_version="2026-10-08")
        context = c.accounts.login(email="paired-browser-owned@example.test", password=PASSWORD).context
        assert context.candidate_id == uid
        for line in sys.stdin:
            request = json.loads(line)
            try:
                operation = request["operation"]
                if operation == "configuration":
                    result = {"now_ms":NOW,"authority_key":signing.pin.public_key,
                        "executor_revision":REVISION,"extension_id":EXTENSION,"candidate_id":uid}
                elif operation == "pairing_device":
                    result = c.browser.device(request["action"], canonical(request["body"]).encode(), request["origin"])
                elif operation == "candidate_fixture_approve":
                    # This server-side helper is synthetic candidate BFF input,
                    # never an installed extension action or provider API.
                    challenge = candidate(c, context, "challenge", {"pairing_id":request["pairing_id"]})
                    result = candidate(c,context,"confirm",{"protocol_version":2,"operation":"confirm_pairing",
                        "challenge_id":challenge["challenge_id"],"nonce":challenge["nonce"],
                        "password":"owned-wrong-password" if request.get("wrong_password") else PASSWORD,"confirmed":True})
                elif operation == "assistance_device":
                    with c.accounts.sessions() as db:
                        result = service.device(db,request["origin"],schemas.DeviceRequest.model_validate(request["body"]))
                elif operation == "fault":
                    if request["fault"] == "native_completion_commit_loss":
                        c.native.fault = lambda names: "persist_loss" if "devices" in names else None
                    elif request["fault"] == "clear":
                        c.native.fault = None
                    else:
                        raise ValueError("Unsupported owned fault")
                    result = {"fault_applied":True}
                elif operation == "audit":
                    with c.accounts.sessions() as db:
                        devices = db.query(models.BrowserAssistanceDevice).all()
                        result = {"devices":[{"id":device.id,"user_id":device.user_id,"state":device.state,
                            "key_sha256":device.key_sha256} for device in devices],
                            "application_actions":db.query(models.BrowserAssistanceRequest).count(),
                            "protected_devices":sum(namespace == "v3_submit_devices" for namespace,_ in c.native.values()),
                            "signatures":sum(kind == "sign" for kind,_,_ in signing.calls)}
                else:
                    raise ValueError("Unsupported owned operation")
                print(json.dumps({"status":200,"body":result}),flush=True)
            except HTTPException as error:
                print(json.dumps({"status":error.status_code,"body":{"detail":"Owned request refused"}}),flush=True)
            except GuardDenied:
                print(json.dumps({"status":403,"body":{"detail":"Owned native proof refused"}}),flush=True)
            except GuardUnavailable:
                print(json.dumps({"status":503,"body":{"detail":"Owned native boundary unavailable"}}),flush=True)
    finally:
        kms_fixture.close()
        account_fixture.close()
        patch.undo()
