"""Incarnation-loss boundaries through genuine SDK-intercepted pairing.

Internal ledger writes can precede a failed final observation. These tests
instead require fresh privileged barriers, quarantine, and no authority export.
No provider traffic or synthetic candidate/device enrollment shortcut is used.
"""
from __future__ import annotations

from uuid import uuid4

import pytest
from test_browser_pairing_production import (
    candidate,
    complete,
    configured,  # noqa: F401 -- transitive intercepted fixtures
    confirm,
    device,
    kms,  # noqa: F401 -- transitive intercepted fixtures
    pairing,  # noqa: F401 -- production composition fixture
    rows,
    start,
    transport,
)

from app.domains.browser_assistance.models import BrowserAssistanceDevice
from app.domains.recovery.store import GuardDenied, GuardUnavailable


def replace(c, dependency: str) -> None:
    pin = getattr(c, dependency)
    c.native.identities[pin.database] = str(uuid4())


def ownership(c):
    coordinator = c.browser._service().coordinator
    return tuple(dict(value) for value in (coordinator._owned_pairing, coordinator._password_admitted,
        c.browser._service().paired_devices.scoped._begins))


def assert_quarantined(c, original=({}, {}, {})) -> None:
    # Preserve earlier requests' exact handles; remove only this failed scope's
    # newly minted authority. Clearing the shared issuer maps would be unsafe.
    for current, before in zip(ownership(c), original, strict=True):
        assert current.keys() == before.keys()
        assert all(current[key] is before[key] for key in before)
    assert not c.native.views


def assert_no_sql_device(c, prepared) -> None:
    identity = str(prepared["request"]["device_id"])
    with c.accounts.sessions() as db:
        row = db.get(BrowserAssistanceDevice, identity)
        assert row is None or row.state != "active"


@pytest.mark.parametrize("dependency", ["execution", "protected"])
@pytest.mark.parametrize("namespace", ["v3_begins", "pairing_confirmations"])
def test_private_commit_uid_loss_cannot_export_confirmation_or_later_authority(
    pairing, dependency, namespace,
):
    c = pairing
    context, prepared, challenge = start(c)
    fired = []

    def change(names):
        if namespace in names and not fired:
            fired.append(True)
            replace(c, dependency)

    c.native.mutate = change
    with pytest.raises((GuardDenied, GuardUnavailable)):
        confirm(c, context, challenge)
    assert fired == [True]
    assert_quarantined(c)
    assert_no_sql_device(c, prepared)
    assert not any(key[0] == "v3_submit_devices" for key in rows(c))
    if namespace == "v3_begins":
        assert not c.kms.calls
    # A failed operation's observations never authorize the next request.
    committed = len(c.native.calls)
    c.native.mutate = None
    with pytest.raises((GuardDenied, GuardUnavailable)):
        candidate(c, context, "challenge", {"pairing_id": prepared["request"]["pairing_id"]})
    assert len(c.native.calls) == committed


@pytest.mark.parametrize("dependency", ["execution", "protected"])
def test_post_kms_uid_loss_withholds_signature_and_quarantines_signing_ownership(pairing, dependency):
    c = pairing
    context, prepared, challenge = start(c)
    c.kms.sign_hook = lambda _reply: replace(c, dependency)
    with pytest.raises((GuardDenied, GuardUnavailable)):
        confirm(c, context, challenge)
    assert [name for name, _, _ in c.kms.calls] == ["public", "sign"]
    assert_quarantined(c)
    assert_no_sql_device(c, prepared)
    assert not any(key[0] == "pairing_confirmations" for key in rows(c))


@pytest.mark.parametrize("dependency", ["execution", "protected"])
@pytest.mark.parametrize("stage", ["native_device", "kms_claim", "protected_grant"])
def test_device_activation_uid_loss_never_reaches_sql_or_exports_claim(
    pairing, dependency, stage,
):
    c = pairing
    context, prepared, challenge = start(c)
    assert confirm(c, context, challenge)["status"] == "CANDIDATE_CONFIRMED"
    prior_signing = len(c.kms.calls)
    prior_ownership = ownership(c)
    fired = []
    namespace = "devices" if stage == "native_device" else "v3_submit_devices"

    def change(names):
        if namespace in names and not fired:
            fired.append(True)
            replace(c, dependency)

    if stage == "kms_claim":
        def signed(_reply):
            fired.append(True)
            replace(c, dependency)
        c.kms.sign_hook = signed
    else:
        c.native.mutate = change
    with pytest.raises((GuardDenied, GuardUnavailable)):
        complete(c, prepared)
    assert fired == [True]
    assert_quarantined(c, prior_ownership)
    assert_no_sql_device(c, prepared)
    if stage == "native_device":
        assert len(c.kms.calls) == prior_signing


@pytest.mark.parametrize("dependency", ["execution", "protected"])
def test_uid_loss_after_owner_read_prevents_sql_denial(pairing, monkeypatch, dependency):
    c = pairing
    context, prepared, challenge = start(c)
    confirm(c, context, challenge)
    claim = complete(c, prepared)["device_claim"]["payload"]
    challenge = candidate(c, context, "revoke-challenge", {"device_id": claim["device_id"]})
    prior_ownership = ownership(c)
    bridge = c.browser._service().paired_devices
    original = bridge.scoped._run
    fired = []

    def change(stage):
        result = original(stage)
        if stage.__name__ == "validate_owner" and not fired:
            fired.append(True)
            replace(c, dependency)
        return result

    monkeypatch.setattr(bridge.scoped, "_run", change)
    from test_browser_pairing_production import PASSWORD
    with pytest.raises((GuardDenied, GuardUnavailable)):
        candidate(c, context, "revoke", {"protocol_version": 2, "operation": "revoke_device",
            "challenge_id": challenge["challenge_id"], "nonce": challenge["nonce"],
            "password": PASSWORD, "confirmed": True})
    assert fired == [True]
    with c.accounts.sessions() as db:
        assert db.get(BrowserAssistanceDevice, claim["device_id"]).state == "active"
    assert ("v3_submit_device_tombstones", claim["device_id"]) not in rows(c)
    assert_quarantined(c, prior_ownership)


def test_uid_observations_do_not_outlive_a_successful_request(pairing):
    c = pairing
    _, prepared, _ = start(c)
    replace(c, "execution")
    before = len(c.native.calls)
    with pytest.raises((GuardDenied, GuardUnavailable)):
        device(c, "status", transport(c, "status", prepared["request"]["pairing_id"]))
    assert len(c.native.calls) == before


@pytest.mark.parametrize("assertion,signature", [("", ""), ("not-an-envelope", "0" * 64)])
def test_invalid_candidate_gateway_cannot_spend_native_metadata(pairing, assertion, signature):
    c = pairing
    with pytest.raises((GuardDenied, GuardUnavailable)):
        c.browser.candidate("challenge", b"{}", assertion, signature)
    assert c.native.sdk.admin.transport.get_database.call_count == 0
    assert c.native.sdk.client.transport.begin_transaction.call_count == 0
    assert not c.native.calls and not c.http_calls and not c.kms.calls


@pytest.mark.parametrize("operation,request_operation", [("prepare", "prepare_request"), ("inspect-key", "key_status")])
def test_invalid_public_device_signature_cannot_spend_native_metadata(pairing, operation, request_operation):
    c = pairing
    body = transport(c, request_operation)
    first = "A" if body["signature"][0] != "A" else "B"
    body["signature"] = first + body["signature"][1:]
    with pytest.raises((GuardDenied, GuardUnavailable)):
        device(c, operation, body)
    assert c.native.sdk.admin.transport.get_database.call_count == 0
    assert c.native.sdk.client.transport.begin_transaction.call_count == 0
    assert not c.native.calls and not c.http_calls and not c.kms.calls
