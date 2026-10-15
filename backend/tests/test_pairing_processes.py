"""Real spawned-process ordering/crash evidence; authority rollback remains unproved."""
from __future__ import annotations

import multiprocessing
import os
import shutil
import sqlite3
from pathlib import Path
from uuid import UUID

import pytest
from backend.tests.fixtures.pairing_authority import (
    NOW,
    FixtureClaims,
    PairingAuthority,
    Scenario,
    jwk,
    sign,
)
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

from app.domains.recovery.contracts import Revocation
from app.domains.recovery.pairing_auth import PinnedAssertions
from app.domains.recovery.pairing_service import PairingService
from app.domains.recovery.store import GuardDenied, GuardUnavailable

CTX = multiprocessing.get_context("spawn")


def private_bytes(key):
    return key.private_bytes(serialization.Encoding.DER, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())


def process_service(path, authority_id, auth_public, claim_private):
    key = serialization.load_der_private_key(claim_private, password=None)
    assert isinstance(key, ec.EllipticCurvePrivateKey)
    return PairingService(PairingAuthority(Path(path), authority_id),
        assertions=PinnedAssertions(issuer="fixture_auth", public_key=auth_public),
        claims=FixtureClaims(key), now_ms=lambda: NOW)


def task_worker(args, action, data, barrier, wait_for, signal, results):
    service = process_service(*args)
    try:
        barrier.wait(15)
        if wait_for:
            assert wait_for.wait(15)
        if action == "complete":
            result = service.complete_device(**data)
        elif action == "confirm":
            result = service.confirm_candidate(**data)
        elif action == "refresh":
            result = service.refresh_claim(**data)
        elif action == "revoke":
            result = service.revoke_device(**data)
        elif action in {"delete", "rotate"}:
            store = service.store
            assert isinstance(store, PairingAuthority)
            subject_id, device_id = data["subject"], data["device_id"]
            subject = store.transact(lambda tx: tx.get("subjects", subject_id))
            assert subject is not None
            if action == "delete":
                scope = Revocation(subject_uuid=UUID(subject_id), kind="subject", target=subject_id, revision=0)
                updates = [("subjects", subject_id, {**subject, "active": False, "auth_generation": 2}),
                           ("revocations", scope.key, {"scope": scope.model_dump(mode="json")})]
            else:
                scope = Revocation(subject_uuid=UUID(subject_id), kind="device", target=device_id, revision=0)
                tombstone = {"scope": scope.model_dump(mode="json"), "device_id": device_id,
                             "subject_uuid": subject_id}
                updates = [("subjects", subject_id, {**subject, "auth_generation": 2}),
                           ("revocations", scope.key, tombstone),
                           ("pairing_device_tombstones", device_id, tombstone)]
            store.seed(updates, kind="FIXTURE_LIFECYCLE_DENY")
            result = {"status": action.upper()}
        else:
            raise AssertionError("Unknown synthetic operation")
        # Output only status; private keys/claim payloads never leave test process evidence.
        results.put((action, "ok", result["status"]))
    except (GuardDenied, GuardUnavailable) as exc:
        results.put((action, type(exc).__name__, None))
    finally:
        if signal:
            signal.set()


def run_pair(case, first, second, *, order="concurrent"):
    args = (str(case.store.path), case.store.authority_id, jwk(case.auth_key), private_bytes(case.claim_key))
    barrier, done, results = CTX.Barrier(2), CTX.Event(), CTX.Queue()
    actions = [first, second]
    if order == "second_first":
        actions.reverse()
    processes = [CTX.Process(target=task_worker, args=(args, action, data, barrier,
        done if index == 1 and order != "concurrent" else None,
        done if index == 0 and order != "concurrent" else None, results))
        for index, (action, data) in enumerate(actions)]
    for process in processes:
        process.start()
    for process in processes:
        process.join(20)
        if process.is_alive():
            process.terminate()
            process.join(5)
        assert process.exitcode == 0
    return [results.get(timeout=2), results.get(timeout=2)]


def test_spawned_duplicate_completion_returns_exactly_one_identity_claim(tmp_path):
    case = Scenario(tmp_path)
    request = case.confirmed()
    proof = case.proof(request)
    results = run_pair(case, ("complete", proof), ("complete", proof))
    assert sum(status == "ok" for _, status, _ in results) == 1
    assert sum(status == "GuardDenied" for _, status, _ in results) == 1
    assert case.service.status(request["pairing_id"])["status"] == "COMPLETED"
    assert "device_claim" not in case.service.status(request["pairing_id"])


def test_spawned_competing_owners_one_pairing_has_one_confirmation(tmp_path):
    case = Scenario(tmp_path)
    request = case.requested()
    other_subject, other_session = case.store.subject()
    challenges = [case.service.candidate_challenge(request["pairing_id"], subject, session)
                  for subject, session in ((case.subject, case.session), (other_subject, other_session))]
    operations = [("confirm", {"challenge_id": item["payload"]["challenge_id"], "nonce": item["nonce"],
                              "envelope": case.assertion(item)}) for item in challenges]
    results = run_pair(case, *operations)
    assert sum(status == "ok" for _, status, _ in results) == 1
    confirmation = case.store.transact(lambda tx: tx.get("pairing_confirmations", request["pairing_id"]))
    assert confirmation["context"]["subject_uuid"] in {case.subject, other_subject}
    assert sum(case.store.transact(lambda tx, item=item: tx.get("pairing_challenges", item["payload"]["challenge_id"]))["consumed_by"] is not None for item in challenges) == 1


def test_spawned_two_owner_requests_same_key_cannot_namespace_ownership(tmp_path):
    case = Scenario(tmp_path)
    first = case.confirmed()
    other_subject, other_session = case.store.subject()
    second = case.confirmed(subject=other_subject, session=other_session)
    results = run_pair(case, ("complete", case.proof(first)), ("complete", case.proof(second)))
    assert sum(status == "ok" for _, status, _ in results) == 1
    owner = case.store.transact(lambda tx: tx.get("pairing_key_owners", first["key_sha256"]))
    assert owner in ({"subject_uuid": case.subject, "device_id": first["device_id"]},
                     {"subject_uuid": other_subject, "device_id": second["device_id"]})
    assert sum(case.store.transact(lambda tx, req=req: tx.get("devices", req["device_id"])) is not None for req in (first, second)) == 1


@pytest.mark.parametrize("action", ["revoke", "delete", "rotate"])
@pytest.mark.parametrize("order", ["concurrent", "first_first", "second_first"])
def test_spawned_completion_lifecycle_deny_has_serialized_boundary(tmp_path, action, order):
    case = Scenario(tmp_path)
    request = case.confirmed()
    proof = case.proof(request)
    if action == "revoke":
        challenge = case.service.revocation_challenge(request["device_id"], case.subject, case.session)
        data = {"challenge_id": challenge["payload"]["challenge_id"], "nonce": challenge["nonce"],
                "envelope": case.assertion(challenge)}
    else:
        data = {"subject": case.subject, "device_id": request["device_id"]}
    results = run_pair(case, ("complete", proof), (action, data), order=order)
    assert next(status for operation, status, _ in results if operation == action) == "ok"
    complete_status = next(status for operation, status, _ in results if operation == "complete")
    if order == "first_first":
        assert complete_status == "ok"
    elif order == "second_first":
        assert complete_status == "GuardDenied"
    else:
        assert complete_status in {"ok", "GuardDenied"}
    with pytest.raises(GuardDenied):
        case.service.refresh_challenge(request["device_id"])
    with pytest.raises(GuardDenied):
        case.service.complete_device(**proof)
    assert "device_claim" not in case.service.status(request["pairing_id"])


@pytest.mark.parametrize("order", ["concurrent", "first_first", "second_first"])
def test_spawned_claim_refresh_and_revoke_do_not_leave_offline_authority(tmp_path, order):
    case = Scenario(tmp_path)
    request, _ = case.complete()
    refresh = case.service.refresh_challenge(request["device_id"])
    proof = {"challenge_id": refresh["payload"]["challenge_id"], "nonce": refresh["nonce"],
             "signature": sign(case.device_key, "device-challenge", refresh["payload"])}
    revoke = case.service.revocation_challenge(request["device_id"], case.subject, case.session)
    revoke_proof = {"challenge_id": revoke["payload"]["challenge_id"], "nonce": revoke["nonce"],
                    "envelope": case.assertion(revoke)}
    results = run_pair(case, ("refresh", proof), ("revoke", revoke_proof), order=order)
    assert next(status for operation, status, _ in results if operation == "revoke") == "ok"
    if order == "second_first":
        assert next(status for operation, status, _ in results if operation == "refresh") == "GuardDenied"
    with pytest.raises(GuardDenied):
        case.service.refresh_claim(**proof)
    with pytest.raises(GuardDenied):
        case.service.refresh_challenge(request["device_id"])


class CrashClaims:
    def sign(self, payload):
        # This callback is reached only after the durable mutation/verified journal commit.
        os._exit(73)


def crash_worker(args, operation, proof):
    service = process_service(*args)
    service.claims = CrashClaims()
    if operation == "complete":
        service.complete_device(**proof)
    else:
        service.refresh_claim(**proof)
    raise AssertionError("Crash hook did not run")


@pytest.mark.parametrize("operation", ["complete", "refresh"])
def test_spawned_death_after_commit_before_claim_reply_is_status_only(tmp_path, operation):
    case = Scenario(tmp_path)
    if operation == "complete":
        request = case.confirmed()
        proof = case.proof(request)
    else:
        request, _ = case.complete()
        refresh = case.service.refresh_challenge(request["device_id"])
        proof = {"challenge_id": refresh["payload"]["challenge_id"], "nonce": refresh["nonce"],
                 "signature": sign(case.device_key, "device-challenge", refresh["payload"])}
    args = (str(case.store.path), case.store.authority_id, jwk(case.auth_key), private_bytes(case.claim_key))
    process = CTX.Process(target=crash_worker, args=(args, operation, proof))
    process.start()
    process.join(20)
    assert process.exitcode == 73
    reopened = process_service(*args)
    assert reopened.status(request["pairing_id"])["status"] == "COMPLETED"
    assert "device_claim" not in reopened.status(request["pairing_id"])
    with pytest.raises(GuardDenied):
        if operation == "complete":
            reopened.complete_device(**proof)
        else:
            reopened.refresh_claim(**proof)
    fresh = reopened.refresh_challenge(request["device_id"])
    assert reopened.refresh_claim(fresh["payload"]["challenge_id"], fresh["nonce"], sign(case.device_key, "device-challenge", fresh["payload"]))["status"] == "IDENTIFIED"


@pytest.mark.parametrize("retained", ["device", "subject", "auth_generation"])
def test_application_only_restore_cannot_erase_independent_identity_denials(tmp_path, retained):
    case = Scenario(tmp_path)
    app, backup = tmp_path / "application.sqlite", tmp_path / "application-backup.sqlite"
    with sqlite3.connect(app) as db:
        db.execute("CREATE TABLE users(id INTEGER PRIMARY KEY,subject_uuid TEXT,active INTEGER)")
        db.execute("INSERT INTO users VALUES(1,?,1)", (case.subject,))
    shutil.copyfile(app, backup)
    request, _ = case.complete()
    if retained == "device":
        challenge = case.service.revocation_challenge(request["device_id"], case.subject, case.session)
        case.service.revoke_device(challenge["payload"]["challenge_id"], challenge["nonce"], case.assertion(challenge))
    else:
        subject = case.store.transact(lambda tx: tx.get("subjects", case.subject))
        updated = {**subject, "auth_generation": 2}
        if retained == "subject":
            updated["active"] = False
        case.store.seed([("subjects", case.subject, updated)])
    with sqlite3.connect(app) as db:
        db.execute("DELETE FROM users")
    shutil.copyfile(backup, app)
    with sqlite3.connect(app) as db:
        assert db.execute("SELECT active FROM users").fetchone() == (1,)
    with pytest.raises(GuardDenied):
        case.service.refresh_challenge(request["device_id"])
    # Recreated account is a new lifetime; it cannot inherit the old key ownership.
    other_subject, other_session = case.store.subject()
    other_request = case.confirmed(subject=other_subject, session=other_session)
    with pytest.raises(GuardDenied):
        case.service.complete_device(**case.proof(other_request))
    owner = case.store.transact(lambda tx: tx.get("pairing_key_owners", request["key_sha256"]))
    assert owner == {"subject_uuid": case.subject, "device_id": request["device_id"]}
