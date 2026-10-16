"""Actual SDK-intercepted normal enrollment/pairing path; no provider calls."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import google.auth
import pytest
from backend.tests.fixtures.pairing_authority import jwk, sign
from cryptography.hazmat.primitives.asymmetric import ec
from test_candidate_production_factory import NOW, PASSWORD, configured  # noqa: F401
from test_pairing_kms import native as kms  # noqa: F401

from app.domains.recovery.browser_pairing import production_browser_pairing
from app.domains.recovery.contracts import canonical
from app.domains.recovery.gcp_scoped_census import validate_scoped_rows

EXTENSION = "pnjmegppacleipajnifjmbcopjjffklb"
REVISION = "companion_0_5_0"
SECRET = "a" * 64
ORIGIN = "https://candidate.example.test"


@pytest.fixture
def pairing(configured, kms, monkeypatch):  # noqa: F811 -- shared intercepted fixture ports
    c = configured
    value = {**c.config.model_dump(mode="json"), "assertion_issuer":"candidate_password_v1",
        "assertion_key": {"version_name":kms.pin.version_name, "endpoint":kms.pin.endpoint,
            "protection_level":kms.pin.protection_level, "public_key":kms.pin.public_key},
        "claim_key": {"version_name":kms.pin.version_name, "endpoint":kms.pin.endpoint,
            "protection_level":kms.pin.protection_level, "public_key":kms.pin.public_key},
        "extension_id":EXTENSION, "executor_revision":REVISION}
    monkeypatch.setenv("BROWSER_PAIRING_NATIVE_ENABLED", "true")
    monkeypatch.setenv("BROWSER_PAIRING_NATIVE_CONFIG", canonical(value))
    monkeypatch.setenv("BROWSER_PAIRING_GATEWAY_SECRET", SECRET)
    monkeypatch.setenv("BROWSER_PAIRING_WEBSITE_ORIGIN", ORIGIN)
    def credentials(**kwargs):
        assert not c.sql_active and not c.native.views
        assert kwargs == {"scopes":["https://www.googleapis.com/auth/cloudkms"]}
        return SimpleNamespace(valid=True, token="fixture-token",
            expiry=datetime.now(UTC)+timedelta(minutes=5)), "hirewiz-local-authority"
    monkeypatch.setattr(google.auth, "default", credentials)
    c.factory_config = value
    c.browser = production_browser_pairing()
    c.private = ec.generate_private_key(ec.SECP256R1())
    c.key = jwk(c.private)
    c.kms = kms
    c.native.seed(c.execution_registry, "pairing_extensions", f"{EXTENSION}:{REVISION}",
        {"extension_id":EXTENSION, "executor_revision":REVISION, "protocol_version":2, "active":True})
    return c


def device(c, operation, value):
    return c.browser.device(operation, canonical(value).encode(), f"chrome-extension://{EXTENSION}")


def transport(c, operation, identity=None):
    value = {"protocol_version":2, "operation":operation, "request_id":str(uuid4()), "issued_at_ms":NOW}
    if identity is not None:
        value["identity"] = identity
    else:
        value.update(key=c.key, extension_id=EXTENSION, revision=REVISION)
    return {**value,"signature":sign(c.private, "transport-request", value)}


def candidate(c, context, operation, body):
    raw = canonical(body).encode()
    envelope = {"version":1,"issuer":"hirewiz_bff_v1", "audience":"hirewiz:browser-pairing-candidate",
        "request_id":str(uuid4()),"method":"POST","path":f"/api/v1/browser-pairing/candidate/{operation}",
        "origin":ORIGIN,"body_sha256":hashlib.sha256(raw).hexdigest(),"issued_at_ms":NOW,"expires_at_ms":NOW+5000,
        "context":context.model_dump(mode="json")}
    encoded = canonical(envelope).encode()
    mac = hmac.new(bytes.fromhex(SECRET), b"hirewiz.browser-gateway.v1\n"+encoded, hashlib.sha256).hexdigest()
    return c.browser.candidate(operation,raw,base64.urlsafe_b64encode(encoded).rstrip(b"=").decode(),mac)


def start(c, *, google=False):
    uid = c.accounts.register(email=f"candidate-{uuid4()}@example.test", password=PASSWORD, policy_version="2026-10-08")
    with c.accounts.sessions() as db:
        from app.models import User
        email = db.get(User, uid).email
    context = c.accounts.login(email=email,password=PASSWORD).context
    if google:
        from app.domains.candidate_accounts.service import revision_from_row
        from app.models import CandidatePasswordAccount, User
        with c.accounts.sessions() as db:
            revision = revision_from_row(db.get(CandidatePasswordAccount,uid),db.get(User,uid).password_hash)
        context = c.accounts.retention.issue_oauth(revision, principal_sha256=revision.principal_sha256,
            verified_proof_sha256="e"*64, expires_at_ms=NOW+300_000)
        assert context is not None and context.authentication_method == "google"
    prepared = device(c,"prepare",transport(c,"prepare_request"))
    device(c,"create",{"pairing_id":prepared["request"]["pairing_id"],"nonce":prepared["nonce"],
        "signature":sign(c.private,"request",prepared["request"])})
    challenge = candidate(c,context,"challenge",{"pairing_id":prepared["request"]["pairing_id"]})
    return context, prepared, challenge


def confirm(c,context,challenge):
    return candidate(c,context,"confirm",{"protocol_version":2,"operation":"confirm_pairing","method":"password_reauth",
        "challenge_id":challenge["challenge_id"],"nonce":challenge["nonce"],"password":PASSWORD,"confirmed":True})


def complete(c,prepared):
    pairing_id = prepared["request"]["pairing_id"]
    challenge = device(c,"challenge",transport(c,"device_challenge",pairing_id))
    return device(c,"complete",{"challenge_id":challenge["payload"]["challenge_id"],"nonce":challenge["nonce"],
        "signature":sign(c.private,"device-challenge",challenge["payload"])})


def rows(c):
    return {(json.loads(raw)["namespace"],json.loads(raw)["key"]):json.loads(raw)["value"]
        for path,(raw,_) in c.native.data.items() if path.startswith(c.protected.database+"/")}


def test_genuine_registration_pairing_protected_device_no_operator_grant(pairing):
    c = pairing
    context, prepared, challenge = start(c)
    assert confirm(c,context,challenge)["status"] == "CANDIDATE_CONFIRMED"
    result = complete(c,prepared)
    assert result["status"] == "COMPLETED"
    claim = result["device_claim"]["payload"]
    all_rows = rows(c)
    registered = all_rows["v3_submit_devices",claim["device_id"]]
    assert registered["source"] == "native_pairing"
    assert registered["grant"]["context"]["candidate_id"] == context.candidate_id
    assert registered["grant"]["key_sha256"] == claim["key_sha256"]
    assert "grant_pin" not in registered
    assert len([key for key in all_rows if key[0] == "v3_pairing_completions"]) == 1
    validate_scoped_rows(all_rows)
    assert len(c.kms.calls) == 4


def test_factory_is_structural_and_resource_mismatch_closed(pairing,monkeypatch):
    c = pairing
    assert all(client.call_count == 0 for client in c.clients)
    production_browser_pairing()
    assert all(client.call_count == 0 for client in c.clients)
    changed = dict(c.factory_config)
    changed["epoch_generation"] = 2
    monkeypatch.setenv("BROWSER_PAIRING_NATIVE_CONFIG",canonical(changed))
    from app.domains.recovery.store import GuardUnavailable
    with pytest.raises(GuardUnavailable):
        production_browser_pairing()
    assert all(client.call_count == 0 for client in c.clients)


def test_wrong_password_never_begins_or_signs(pairing):
    c = pairing
    context,_,challenge = start(c)
    from app.domains.recovery.store import GuardDenied
    with pytest.raises(GuardDenied):
        candidate(c,context,"confirm",{"protocol_version":2,"operation":"confirm_pairing",
            "challenge_id":challenge["challenge_id"],"nonce":challenge["nonce"],"password":"wrong", "confirmed":True})
    assert not c.kms.calls
    assert not any(key[0] == "v3_begins" for key in rows(c))


@pytest.mark.parametrize("persist",[False,True])
def test_unknown_protected_signing_begin_never_signs_or_replays(pairing,persist):
    c = pairing
    context,_,challenge = start(c)
    def fault(names):
        if "v3_begins" in names:
            return "persist_loss" if persist else "no_persist_loss"
    c.native.fault = fault
    from app.domains.recovery.store import GuardDenied, GuardUnavailable
    with pytest.raises(GuardUnavailable):
        confirm(c,context,challenge)
    assert not c.kms.calls
    c.native.fault = None
    if persist:
        with pytest.raises((GuardUnavailable,GuardDenied)):
            confirm(c,context,challenge)
        assert not c.kms.calls


@pytest.mark.parametrize("persist",[False,True])
def test_unknown_native_completion_withholds_protected_activation_and_claim(pairing,persist):
    c = pairing
    context,prepared,challenge = start(c)
    confirm(c,context,challenge)
    c.native.fault = lambda names: ("persist_loss" if persist else "no_persist_loss") if "devices" in names else None
    outcome = complete(c,prepared)
    assert outcome["status"] == "UNKNOWN"
    assert not any(key[0] == "v3_submit_devices" for key in rows(c))
    assert len(c.kms.calls) == 2
    c.native.fault = None
    assert not c.browser._service().coordinator._owned_pairing


@pytest.mark.parametrize("persist",[False,True])
def test_unknown_protected_activation_never_exports_or_adopts(pairing,persist):
    c = pairing
    context,prepared,challenge = start(c)
    confirm(c,context,challenge)
    c.native.fault = lambda names: ("persist_loss" if persist else "no_persist_loss") if "v3_submit_devices" in names else None
    from app.domains.recovery.store import GuardUnavailable
    with pytest.raises(GuardUnavailable):
        complete(c,prepared)
    assert not c.browser._service().coordinator._owned_pairing
    c.native.fault = None
    if persist:
        validate_scoped_rows(rows(c))
    result = device(c,"status",transport(c,"status",prepared["request"]["pairing_id"]))
    assert result["status"] == "COMPLETED"
    from app.domains.recovery.gcp_pairing_contracts import PairingExecution
    from app.domains.recovery.store import GuardDenied
    forged = PairingExecution(status="COMMITTED",operation_id=uuid4(),intent_sha256="a"*64)
    with pytest.raises(GuardDenied):
        c.browser._service().coordinator.take_owned_ack(forged)


def test_normal_revocation_denies_protected_device_and_preserves_full_census(pairing):
    c = pairing
    context,prepared,challenge = start(c)
    confirm(c,context,challenge)
    completed = complete(c,prepared)
    claim = completed["device_claim"]["payload"]
    challenge = candidate(c,context,"revoke-challenge",{"device_id":claim["device_id"]})
    result = candidate(c,context,"revoke",{"protocol_version":2,"operation":"revoke_device",
        "challenge_id":challenge["challenge_id"],"nonce":challenge["nonce"],"password":PASSWORD,"confirmed":True})
    assert result["status"] == "REVOKED"
    all_rows = rows(c)
    assert ("v3_submit_device_tombstones",claim["device_id"]) in all_rows
    assert len([key for key in all_rows if key[0] == "v3_pairing_revocations"]) == 1
    validate_scoped_rows(all_rows)
    from app.domains.recovery.store import GuardDenied
    with pytest.raises(GuardDenied):
        c.browser._service().paired_devices.check_claim(claim)


def test_paired_device_enters_real_submit_authority_and_sql_projection(pairing,monkeypatch):
    from test_browser_submit_native import binding

    from app.domains.browser_assistance.models import BrowserAssistanceDevice
    from app.domains.browser_assistance.service import digest
    from app.domains.recovery.browser_submit import production_submit_authority
    from app.domains.recovery.gcp_submit_contracts import SubmitDeviceGrant
    c = pairing
    context,prepared,challenge = start(c)
    confirm(c,context,challenge)
    claim = complete(c,prepared)["device_claim"]["payload"]
    grant = SubmitDeviceGrant.model_validate(rows(c)["v3_submit_devices",claim["device_id"]]["grant"])
    with c.accounts.sessions() as db:
        row = db.get(BrowserAssistanceDevice,claim["device_id"])
        assert row.state == "active" and row.user_id == context.candidate_id
        assert row.key_sha256 == digest(grant.public_key) and row.key_sha256 != grant.key_sha256
    config = {**c.config.model_dump(mode="json"),"device_enrollment":"paired","executor_revision":REVISION}
    monkeypatch.setenv("BROWSER_SUBMIT_NATIVE_ENABLED","true")
    monkeypatch.setenv("BROWSER_ASSISTANCE_SUBMIT_EXECUTOR_REVISION",REVISION)
    monkeypatch.setenv("BROWSER_SUBMIT_NATIVE_CONFIG",canonical(config))
    authority = production_submit_authority()
    action = binding(SimpleNamespace(grant=grant),candidate_id=context.candidate_id)
    ack = authority.reserve(grant.context,action)
    assert ack is not None
    proof = authority.consume_ack(ack,action)
    assert proof["employer_receipt"] is False and proof["settlement_authorized"] is False
    from app.domains.recovery.store import GuardDenied
    with pytest.raises(GuardDenied):
        authority.consume_ack(ack,action)
    validate_scoped_rows(rows(c))


def test_denial_during_kms_signing_withholds_native_confirmation(pairing):
    from app.domains.recovery.store import GuardDenied, GuardUnavailable
    c = pairing
    context,_,challenge = start(c)
    def closed(reply):
        c.native.seed(c.protected_registry,"v3_root","current", {
            "pin":c.config.publication_resource.pin.model_dump(mode="json"),"phase":"CLOSED","close_id":str(uuid4())})
    c.kms.sign_hook = closed
    with pytest.raises((GuardDenied,GuardUnavailable)):
        confirm(c,context,challenge)
    assert len(c.kms.calls) == 2
    assert not any("pairing_confirmations" in names for names in c.native.calls)


@pytest.mark.parametrize("persist",[False,True])
def test_unknown_native_revocation_keeps_sql_and_protected_device_denied(pairing,persist):
    from app.domains.browser_assistance.models import BrowserAssistanceDevice
    from app.domains.recovery.store import GuardDenied, GuardUnavailable
    c = pairing
    context,prepared,challenge = start(c)
    confirm(c,context,challenge)
    claim = complete(c,prepared)["device_claim"]["payload"]
    challenge = candidate(c,context,"revoke-challenge",{"device_id":claim["device_id"]})
    c.native.fault = lambda names: ("persist_loss" if persist else "no_persist_loss") if "pairing_device_tombstones" in names else None
    with pytest.raises(GuardUnavailable):
        candidate(c,context,"revoke",{"protocol_version":2,"operation":"revoke_device",
            "challenge_id":challenge["challenge_id"],"nonce":challenge["nonce"],"password":PASSWORD,"confirmed":True})
    c.native.fault = None
    with c.accounts.sessions() as db:
        assert db.get(BrowserAssistanceDevice,claim["device_id"]).state == "revoked"
    assert ("v3_submit_device_tombstones",claim["device_id"]) in rows(c)
    with pytest.raises(GuardDenied):
        c.browser._service().paired_devices.check_claim(claim)
    validate_scoped_rows(rows(c))


@pytest.mark.parametrize("name,value",[("BROWSER_PAIRING_GATEWAY_SECRET","b"*64),
    ("BROWSER_PAIRING_WEBSITE_ORIGIN","https://changed.example.test")])
def test_existing_factory_refuses_rotated_gateway_pin(pairing,monkeypatch,name,value):
    from app.domains.recovery.store import GuardDenied
    c = pairing
    monkeypatch.setenv(name,value)
    with pytest.raises(GuardDenied):
        c.browser._service()
    assert all(client.call_count == 0 for client in c.clients)


def test_sql_activation_failure_withholds_claim_and_does_not_adopt(pairing,monkeypatch):
    from app.domains.recovery.store import GuardDenied, GuardUnavailable
    c = pairing
    context,prepared,challenge = start(c)
    confirm(c,context,challenge)
    bridge = c.browser._service().paired_devices
    def unavailable(*args):
        raise GuardUnavailable("Synthetic SQL activation failure")
    monkeypatch.setattr(bridge,"activated",unavailable)
    with pytest.raises(GuardUnavailable):
        complete(c,prepared)
    assert len([key for key in rows(c) if key[0] == "v3_submit_devices"]) == 1
    assert not c.browser._service().coordinator._owned_pairing
    with pytest.raises(GuardDenied):
        complete(c,prepared)


def test_protected_uid_change_prevents_signing_even_with_native_session(pairing):
    from app.domains.recovery.store import GuardDenied, GuardUnavailable
    c = pairing
    context,_,challenge = start(c)
    c.native.identities[c.protected.database] = str(uuid4())
    with pytest.raises((GuardDenied,GuardUnavailable)):
        confirm(c,context,challenge)
    assert not c.kms.calls



def test_google_native_session_still_requires_separate_current_password_pairing(pairing):
    c = pairing
    context,prepared,challenge = start(c,google=True)
    assert context.authentication_method == "google"
    assert confirm(c,context,challenge)["status"] == "CANDIDATE_CONFIRMED"
    result = complete(c,prepared)
    assert result["status"] == "COMPLETED"
    all_rows = rows(c)
    registration = all_rows["v3_submit_devices",result["device_claim"]["payload"]["device_id"]]
    full = all_rows["v3_records",registration["operation_id"]]
    assert full["intent"]["command"]["session"]["authentication_method"] == "google"
    assert full["intent"]["command"]["evidence"]["confirmation"]["assertion"]["method"] == "password_reauth"
    validate_scoped_rows(all_rows)

@pytest.mark.parametrize('mutation',['closed','generation','absent'])
def test_paired_submit_rechecks_independent_execution_control_before_any_new_claim(pairing, monkeypatch, mutation):
    from test_browser_submit_native import binding

    from app.domains.recovery.browser_submit import production_submit_authority
    from app.domains.recovery.gcp_pairing import pairing_control_record
    from app.domains.recovery.gcp_submit_contracts import SubmitDeviceGrant
    from app.domains.recovery.store import GuardUnavailable
    c=pairing
    context,prepared,challenge=start(c)
    confirm(c,context,challenge)
    claim=complete(c,prepared)['device_claim']['payload']
    grant=SubmitDeviceGrant.model_validate(rows(c)['v3_submit_devices',claim['device_id']]['grant'])
    config={**c.config.model_dump(mode='json'),'device_enrollment':'paired','executor_revision':REVISION}
    monkeypatch.setenv('BROWSER_SUBMIT_NATIVE_ENABLED','true')
    monkeypatch.setenv('BROWSER_ASSISTANCE_SUBMIT_EXECUTOR_REVISION',REVISION)
    monkeypatch.setenv('BROWSER_SUBMIT_NATIVE_CONFIG',canonical(config))
    authority=production_submit_authority()
    action=binding(SimpleNamespace(grant=grant),candidate_id=context.candidate_id)
    control=pairing_control_record(c.execution,1)
    if mutation=='closed':
        control['state']='CLOSED'
    if mutation=='generation':
        control['generation']=2
    if mutation=='absent':
        control={}
    c.native.seed(c.execution_registry,'pairing_control','current',control)
    before=len([key for key in rows(c) if key[0]=='v3_submit_openings'])
    with pytest.raises(GuardUnavailable):
        authority.reserve(grant.context,action)
    assert len([key for key in rows(c) if key[0]=='v3_submit_openings'])==before


@pytest.mark.parametrize("fault",["none","protected_denied","epoch_generation","execution_closed","wrong_key","missing_activation"])
def test_assistance_context_requires_fresh_independent_device_and_epoch(pairing,monkeypatch,fault):
    from app.domains.recovery.gcp_pairing import pairing_control_record
    from app.domains.recovery.store import GuardDenied, GuardUnavailable
    c = pairing
    context,prepared,challenge = start(c)
    confirm(c,context,challenge)
    claim = complete(c,prepared)["device_claim"]["payload"]
    monkeypatch.setenv("BROWSER_ASSISTANCE_NATIVE_PAIRING_ENABLED","true")
    key = c.key
    if fault == "protected_denied":
        c.native.seed(c.protected_registry,"v3_submit_device_tombstones",claim["device_id"],{"denied":True})
    elif fault == "epoch_generation":
        c.native.seed(c.execution_registry,"pairing_control","current",pairing_control_record(c.execution,2))
    elif fault == "execution_closed":
        c.native.seed(c.execution_registry,"control","meta",{"state":"CLOSED"})
    elif fault == "wrong_key":
        key = jwk(ec.generate_private_key(ec.SECP256R1()))
    elif fault == "missing_activation":
        device = rows(c)["v3_submit_devices",claim["device_id"]]
        c.native.seed(c.protected_registry,"v3_records",device["operation_id"],{"missing":True})
    if fault == "none":
        actual = c.browser.assistance_context(claim["device_id"],key)
        assert actual.candidate_id == context.candidate_id and actual.account_binding_id == context.account_binding_id
    else:
        from pydantic import ValidationError
        with pytest.raises((GuardDenied,GuardUnavailable,ValidationError)):
            c.browser.assistance_context(claim["device_id"],key)


def test_native_assistance_precheck_runs_after_sql_context_is_released(pairing,monkeypatch):
    c = pairing
    context,prepared,challenge = start(c)
    confirm(c,context,challenge)
    claim = complete(c,prepared)["device_claim"]["payload"]
    monkeypatch.setenv("BROWSER_ASSISTANCE_NATIVE_PAIRING_ENABLED","true")
    with c.accounts.sessions() as db:
        from app.models import User
        db.get(User,context.candidate_id)
        assert c.sql_active
        db.rollback()
        assert not c.sql_active
        actual = c.browser.assistance_context(claim["device_id"],c.key)
        assert actual.candidate_id == context.candidate_id
    assert c.browser.assistance_context(claim["device_id"],c.key).candidate_id == context.candidate_id
