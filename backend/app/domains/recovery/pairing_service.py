"""Disabled two-party identity core; no routes, employer actions or fallback store.

Cryptographic verification runs outside transactions. Injected assertion verification
and claim signing must be local trusted adapters, not generic bearer authentication.
Claim signing follows confirmed independent commit; signing/reply loss is status-only.
"""
from __future__ import annotations

import re
from collections.abc import Callable
from time import time_ns
from uuid import UUID, uuid4

from .contracts import Revocation
from .pairing_auth import (
    AssertionVerifier,
    ClaimIssuer,
    UnavailableAssertions,
    UnavailableClaims,
    key_fingerprint,
    public_jwk,
    verify_signature,
)
from .pairing_contracts import (
    CANDIDATE_MS,
    CLAIM_MS,
    DEVICE_MS,
    JS_SAFE_MAX,
    REQUEST_MS,
    CandidateAssertion,
    DeviceClaim,
    PairingRequest,
    canonical_bytes,
    digest,
    nonce_digest,
)
from .store import AuthorityStore, GuardDenied, GuardUnavailable, Transaction, production_store


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise GuardDenied(message)


def _uuid(value: str) -> str:
    _require(type(value) is str and str(UUID(value)) == value, "Canonical UUID is required")
    return value


def _exact(value: dict | None, expected: dict) -> bool:
    if type(value) is not dict:
        return False
    try:
        return canonical_bytes("registry", value) == canonical_bytes("registry", expected)
    except (TypeError, ValueError):
        return False


class PairingService:
    def __init__(self, store: AuthorityStore | None = None, *,
                 assertions: AssertionVerifier | None = None, claims: ClaimIssuer | None = None,
                 now_ms: Callable[[], int] | None = None):
        self.store = store if store is not None else production_store()
        self.assertions = assertions if assertions is not None else UnavailableAssertions()
        self.claims = claims if claims is not None else UnavailableClaims()
        self.now_ms = now_ms if now_ms is not None else lambda: time_ns() // 1_000_000

    def _now(self, tx: Transaction, *, record: bool = True) -> int:
        now = self.now_ms()
        _require(type(now) is int and 0 < now <= JS_SAFE_MAX - CLAIM_MS,
                 "Clock is outside safe protocol bounds")
        prior = tx.get("clock", "observed")
        _require(prior is None or (type(prior.get("now_ms")) is int and
                 0 < prior["now_ms"] <= now), "Authority clock moved backward or is malformed")
        if record:
            tx.put("clock", "observed", {"now_ms": now})
        return now

    @staticmethod
    def _control(tx: Transaction) -> dict:
        control = tx.get("control", "current")
        if not control or control.get("status") != "OPEN":
            raise GuardUnavailable("Independent authority is absent or closed")
        _require(all(type(control.get(name)) is int and 0 < control[name] <= JS_SAFE_MAX
                     for name in ("incarnation", "generation")), "Authority generation is unavailable")
        for name in ("authority_id", "epoch_id"):
            _uuid(control[name])
        return control

    @staticmethod
    def _epoch(control: dict, payload: dict) -> None:
        _require(all(payload[key] == control[source] for key, source in (
            ("authority_id", "authority_id"), ("authority_incarnation", "incarnation"),
            ("epoch_id", "epoch_id"), ("epoch_generation", "generation"),
        )), "Authority/epoch changed")

    @staticmethod
    def _extension(tx: Transaction, extension_id: str, revision: str) -> None:
        approved = tx.get("pairing_extensions", f"{extension_id}:{revision}")
        _require(_exact(approved, {"extension_id": extension_id, "executor_revision": revision,
                             "protocol_version": 2, "active": True}),
                 "Extension identity/release is not approved")

    @staticmethod
    def _subject(tx: Transaction, subject_id: str) -> dict:
        subject = tx.get("subjects", subject_id)
        scope = Revocation(subject_uuid=UUID(subject_id), kind="subject", target=subject_id, revision=0)
        _require(subject is not None and subject.get("active") is True and
                 subject.get("registration_event_id") is not None and
                 tx.get("revocations", scope.key) is None, "Subject lifetime is absent or revoked")
        assert subject is not None
        _require(type(subject.get("auth_generation")) is int and
                 0 < subject["auth_generation"] <= JS_SAFE_MAX and
                 type(subject.get("principal_sha256")) is str and
                 re.fullmatch(r"[a-f0-9]{64}", subject["principal_sha256"]) is not None,
                 "Subject authentication metadata is malformed")
        registration = tx.event(subject["registration_event_id"])
        _require(registration is not None and registration["kind"] == "SUBJECT_REGISTERED" and
                 registration["payload"] == {"subject_uuid": subject_id},
                 "Independent subject registration provenance is absent")
        return subject

    def _candidate(self, tx: Transaction, assertion: CandidateAssertion, now: int) -> dict:
        subject_id = str(assertion.subject_uuid)
        subject = self._subject(tx, subject_id)
        _require(subject["auth_generation"] == assertion.auth_generation and
                 subject["principal_sha256"] == assertion.principal_sha256,
                 "Candidate principal/authentication generation changed")
        session = tx.get("pairing_sessions", str(assertion.session_id))
        _require(_exact(session, {"subject_uuid": subject_id, "principal_sha256": assertion.principal_sha256,
                             "auth_generation": assertion.auth_generation, "active": True}),
                 "Independent candidate session is absent or revoked")
        _require(assertion.authenticated_at_ms <= assertion.issued_at_ms <= now < assertion.expires_at_ms
                 and now - assertion.authenticated_at_ms <= CANDIDATE_MS,
                 "Fresh candidate authentication expired")
        _require(tx.get("pairing_assertions", str(assertion.assertion_id)) is None,
                 "Candidate assertion was consumed")
        return subject

    def _request_record(self, tx: Transaction, pairing_id: str) -> tuple[dict, PairingRequest]:
        row = tx.get("pairing_requests", _uuid(pairing_id))
        _require(row is not None, "Pairing request is absent")
        assert row is not None
        request = PairingRequest.model_validate(row["request"])
        _require(request.fingerprint == row["request_sha256"] and str(request.pairing_id) == pairing_id,
                 "Pairing request digest changed")
        return row, request

    def _request(self, tx: Transaction, pairing_id: str, now: int) -> tuple[dict, PairingRequest]:
        row, request = self._request_record(tx, pairing_id)
        _require(now < request.expires_at_ms, "Pairing request expired")
        self._epoch(self._control(tx), request.model_dump(mode="json"))
        self._extension(tx, request.extension_id, request.executor_revision)
        _require(key_fingerprint(request.public_key) == request.key_sha256, "Public key changed")
        return row, request

    def prepare_request(self, key: dict, extension_id: str, revision: str) -> dict:
        """Server allocation only. This unproved request cannot be candidate-confirmed."""
        key = public_jwk(key)
        def operation(tx: Transaction) -> dict:
            control, now = self._control(tx), self._now(tx)
            self._extension(tx, extension_id, revision)
            nonce = str(uuid4())
            request = PairingRequest(operation_id=uuid4(), pairing_id=uuid4(), device_id=uuid4(),
                authority_id=control["authority_id"], authority_incarnation=control["incarnation"],
                epoch_id=control["epoch_id"], epoch_generation=control["generation"],
                extension_id=extension_id, executor_revision=revision, public_key=key,
                key_sha256=key_fingerprint(key), challenge_id=uuid4(), nonce_sha256=nonce_digest(nonce),
                issued_at_ms=now, expires_at_ms=now + REQUEST_MS)
            payload = request.model_dump(mode="json")
            tx.put("pairing_requests", str(request.pairing_id), {"request": payload,
                "request_sha256": request.fingerprint, "state": "UNPROVEN"}, immutable=True)
            tx.put("pairing_device_requests", str(request.device_id),
                   {"pairing_id": str(request.pairing_id)}, immutable=True)
            tx.put("pairing_challenges", str(request.challenge_id), {"kind": "create",
                "payload": payload, "expires_at_ms": now + DEVICE_MS, "consumed_by": None}, immutable=True)
            tx.append(str(uuid4()), "PAIRING_PREPARED", {"pairing_id": str(request.pairing_id)})
            return {"request": payload, "nonce": nonce}
        return self.store.transact(operation)

    def _challenge(self, challenge_id: str) -> dict:
        def read(tx: Transaction) -> dict:
            self._control(tx)
            row = tx.get("pairing_challenges", _uuid(challenge_id))
            _require(row is not None, "Challenge is absent")
            assert row is not None
            return row
        return self.store.transact(read)

    def create_request(self, pairing_id: str, nonce: str, signature: str) -> dict:
        def read(tx: Transaction) -> PairingRequest:
            return self._request(tx, pairing_id, self._now(tx, record=False))[1]
        request = self.store.transact(read)
        verify_signature(request.public_key, "request", request.model_dump(mode="json"), signature)
        def operation(tx: Transaction) -> dict:
            now = self._now(tx)
            row, current = self._request(tx, pairing_id, now)
            challenge = tx.get("pairing_challenges", str(current.challenge_id))
            _require(row["state"] == "UNPROVEN" and request == current and challenge is not None and
                     challenge["kind"] == "create" and challenge["consumed_by"] is None and
                     now < challenge["expires_at_ms"] and current.nonce_sha256 == nonce_digest(nonce),
                     "Fresh creation proof is required")
            assert challenge is not None
            tx.put("pairing_challenges", str(current.challenge_id), {**challenge, "consumed_by": pairing_id})
            tx.put("pairing_requests", pairing_id, {**row, "state": "REQUESTED"})
            tx.append(str(uuid4()), "PAIRING_REQUESTED", {"pairing_id": pairing_id})
            return {"pairing_id": pairing_id, "device_id": str(current.device_id), "status": "REQUESTED"}
        return self.store.transact(operation)

    def _candidate_challenge(self, tx: Transaction, *, subject_id: str, session_id: str,
                             operation: str, context: dict, expires: int, now: int) -> dict:
        subject = self._subject(tx, _uuid(subject_id))
        _uuid(session_id)
        nonce, challenge_id = str(uuid4()), str(uuid4())
        payload = {**context, "protocol_version": 2, "operation": operation,
            "audience": "hirewiz:pairing-only", "challenge_id": challenge_id,
            "subject_uuid": subject_id, "session_id": session_id,
            "principal_sha256": subject["principal_sha256"], "auth_generation": subject["auth_generation"],
            "nonce_sha256": nonce_digest(nonce), "issued_at_ms": now, "expires_at_ms": expires}
        tx.put("pairing_challenges", challenge_id, {"kind": "candidate", "payload": payload,
               "expires_at_ms": expires, "consumed_by": None}, immutable=True)
        tx.append(str(uuid4()), "PAIRING_CANDIDATE_CHALLENGE", {"challenge_id": challenge_id})
        return {"payload": payload, "nonce": nonce, "binding_sha256": digest("candidate-context", payload)}

    def candidate_challenge(self, pairing_id: str, subject_id: str, session_id: str) -> dict:
        def operation(tx: Transaction) -> dict:
            now = self._now(tx)
            row, request = self._request(tx, pairing_id, now)
            _require(row["state"] == "REQUESTED", "Pairing is not awaiting candidate confirmation")
            context = {key: request.model_dump(mode="json")[key] for key in (
                "pairing_id", "device_id", "key_sha256", "authority_id", "authority_incarnation",
                "epoch_id", "epoch_generation",
            )}
            context["request_sha256"] = request.fingerprint
            return self._candidate_challenge(tx, subject_id=subject_id, session_id=session_id,
                operation="confirm_pairing", context=context,
                expires=min(request.expires_at_ms, now + CANDIDATE_MS), now=now)
        return self.store.transact(operation)

    def confirm_candidate(self, challenge_id: str, nonce: str, envelope: dict) -> dict:
        challenge = self._challenge(challenge_id)
        payload = challenge["payload"]
        assertion = self.assertions.verify(envelope, binding_sha256=digest("candidate-context", payload),
                                           operation="confirm_pairing", now_ms=self.now_ms())
        def operation(tx: Transaction) -> dict:
            now = self._now(tx)
            _require(tx.get("pairing_challenges", challenge_id) == challenge and challenge["kind"] == "candidate"
                     and payload["operation"] == "confirm_pairing" and challenge["consumed_by"] is None
                     and now < challenge["expires_at_ms"] and payload["nonce_sha256"] == nonce_digest(nonce),
                     "Fresh exact candidate challenge is required")
            row, request = self._request(tx, payload["pairing_id"], now)
            _require(row["state"] == "REQUESTED" and request.fingerprint == payload["request_sha256"],
                     "Exact awaiting pairing request is required")
            self._candidate(tx, assertion, now)
            _require(all(str(getattr(assertion, name)) == str(payload[name]) for name in (
                "subject_uuid", "session_id", "principal_sha256", "auth_generation",
            )), "Candidate assertion belongs to another challenge principal/session")
            confirmation = {"confirmation_id": str(uuid4()), "challenge_id": challenge_id,
                "context": payload, "assertion": assertion.model_dump(mode="json")}
            confirmation_sha = digest("confirmation", confirmation)
            tx.put("pairing_confirmations", payload["pairing_id"], confirmation, immutable=True)
            tx.put("pairing_assertions", str(assertion.assertion_id),
                   {"confirmation_sha256": confirmation_sha}, immutable=True)
            tx.put("pairing_challenges", challenge_id, {**challenge, "consumed_by": confirmation_sha})
            tx.put("pairing_requests", payload["pairing_id"], {**row, "state": "CANDIDATE_CONFIRMED",
                   "confirmation_sha256": confirmation_sha})
            tx.append(str(uuid4()), "PAIRING_CANDIDATE_CONFIRMED", {"pairing_id": payload["pairing_id"],
                "confirmation_sha256": confirmation_sha})
            return {"pairing_id": payload["pairing_id"], "device_id": str(request.device_id),
                    "status": "CANDIDATE_CONFIRMED"}
        return self.store.transact(operation)

    def _confirmation_evidence(self, tx: Transaction, row: dict, request: PairingRequest) -> dict:
        confirmation = tx.get("pairing_confirmations", str(request.pairing_id))
        _require(confirmation is not None and digest("confirmation", confirmation) == row["confirmation_sha256"],
                 "Exact confirmation evidence is absent")
        assert confirmation is not None
        challenge = tx.get("pairing_challenges", confirmation["challenge_id"])
        _require(challenge is not None and challenge["payload"] == confirmation["context"] and
                 challenge["consumed_by"] == row["confirmation_sha256"] and
                 tx.get("pairing_assertions", confirmation["assertion"]["assertion_id"]) ==
                 {"confirmation_sha256": row["confirmation_sha256"]},
                 "Candidate challenge was not consumed by this exact confirmation")
        _require(confirmation["context"]["request_sha256"] == request.fingerprint and
                 confirmation["context"]["pairing_id"] == str(request.pairing_id),
                 "Confirmation belongs to another request")
        return confirmation

    def _confirmed(self, tx: Transaction, row: dict, request: PairingRequest) -> dict:
        confirmation = self._confirmation_evidence(tx, row, request)
        assertion = CandidateAssertion.model_validate(confirmation["assertion"])
        subject = self._subject(tx, str(assertion.subject_uuid))
        _require(subject["auth_generation"] == assertion.auth_generation and
                 subject["principal_sha256"] == assertion.principal_sha256 and
                 _exact(tx.get("pairing_sessions", str(assertion.session_id)), {
                     "subject_uuid": str(assertion.subject_uuid), "principal_sha256": assertion.principal_sha256,
                     "auth_generation": assertion.auth_generation, "active": True}),
                 "Confirmed candidate lifetime/session changed")
        self._epoch(self._control(tx), confirmation["context"])
        return confirmation

    def _device_denied(self, tx: Transaction, subject_id: str, device_id: str) -> None:
        scope = Revocation(subject_uuid=UUID(subject_id), kind="device", target=device_id, revision=0)
        _require(tx.get("revocations", scope.key) is None and tx.get("pairing_device_tombstones", device_id) is None,
                 "Device was revoked")

    def device_challenge(self, pairing_id: str) -> dict:
        def operation(tx: Transaction) -> dict:
            now = self._now(tx)
            row, request = self._request(tx, pairing_id, now)
            _require(row["state"] == "CANDIDATE_CONFIRMED", "Candidate confirmation is required")
            confirmation = self._confirmed(tx, row, request)
            self._device_denied(tx, confirmation["context"]["subject_uuid"], str(request.device_id))
            _require(row.get("device_challenge_id") is None, "Device completion challenge is single-use")
            nonce, challenge_id = str(uuid4()), str(uuid4())
            payload = {**confirmation["context"], "operation": "complete_pairing",
                "challenge_id": challenge_id, "nonce_sha256": nonce_digest(nonce),
                "confirmation_sha256": row["confirmation_sha256"], "issued_at_ms": now,
                "expires_at_ms": min(request.expires_at_ms, now + DEVICE_MS)}
            tx.put("pairing_challenges", challenge_id, {"kind": "device", "payload": payload,
                "public_key": request.public_key, "expires_at_ms": payload["expires_at_ms"],
                "consumed_by": None}, immutable=True)
            tx.put("pairing_requests", pairing_id, {**row, "device_challenge_id": challenge_id})
            tx.append(str(uuid4()), "PAIRING_DEVICE_CHALLENGE", {"challenge_id": challenge_id})
            return {"payload": payload, "nonce": nonce}
        return self.store.transact(operation)

    def _claim(self, device: dict, control: dict, event: dict, now: int) -> dict:
        return DeviceClaim(claim_id=uuid4(), device_id=device["device_id"], subject_uuid=device["subject_uuid"],
            key_sha256=device["key_sha256"], key_generation=device["key_generation"],
            auth_generation=device["auth_generation"], authority_id=control["authority_id"],
            authority_incarnation=control["incarnation"], epoch_id=control["epoch_id"],
            epoch_generation=control["generation"], extension_id=device["extension_id"],
            executor_revision=device["executor_revision"], event_sequence=event["sequence"],
            event_sha256=event["digest"], issued_at_ms=now, expires_at_ms=now + CLAIM_MS).model_dump(mode="json")

    def complete_device(self, challenge_id: str, nonce: str, signature: str) -> dict:
        challenge = self._challenge(challenge_id)
        _require(challenge["kind"] == "device", "Device completion challenge is required")
        verify_signature(challenge["public_key"], "device-challenge", challenge["payload"], signature)
        def operation(tx: Transaction) -> dict:
            now, payload = self._now(tx), challenge["payload"]
            _require(tx.get("pairing_challenges", challenge_id) == challenge and challenge["consumed_by"] is None
                     and payload["operation"] == "complete_pairing" and now < challenge["expires_at_ms"]
                     and payload["nonce_sha256"] == nonce_digest(nonce), "Fresh device proof is required")
            row, request = self._request(tx, payload["pairing_id"], now)
            _require(row["state"] == "CANDIDATE_CONFIRMED" and row["device_challenge_id"] == challenge_id,
                     "Pairing request cannot replay")
            confirmation = self._confirmed(tx, row, request)
            _require(payload["confirmation_sha256"] == row["confirmation_sha256"] and
                     payload["subject_uuid"] == confirmation["context"]["subject_uuid"],
                     "Device proof belongs to another confirmation")
            subject_id, device_id = payload["subject_uuid"], str(request.device_id)
            self._device_denied(tx, subject_id, device_id)
            _require(tx.get("pairing_key_owners", request.key_sha256) is None and tx.get("devices", device_id) is None,
                     "Device/key ownership is already permanently claimed")
            device = {"device_id": device_id, "subject_uuid": subject_id, "key_sha256": request.key_sha256,
                "public_key": request.public_key, "key_generation": 1, "auth_generation": payload["auth_generation"],
                "extension_id": request.extension_id, "executor_revision": request.executor_revision,
                "pairing_id": str(request.pairing_id), "confirmation_sha256": row["confirmation_sha256"],
                "active": True}
            tx.put("devices", device_id, device, immutable=True)
            tx.put("pairing_key_owners", request.key_sha256,
                   {"device_id": device_id, "subject_uuid": subject_id}, immutable=True)
            tx.put("pairing_challenges", challenge_id, {**challenge, "consumed_by": device_id})
            tx.put("pairing_requests", payload["pairing_id"], {**row, "state": "COMPLETED"})
            event = tx.append(str(uuid4()), "PAIRING_COMPLETED", {"device_id": device_id,
                "pairing_id": payload["pairing_id"], "confirmation_sha256": row["confirmation_sha256"]})
            return self._claim(device, self._control(tx), event, now)
        claim = self.store.transact(operation)
        return {"status": "COMPLETED", "device_claim": self.claims.sign(claim)}

    def _device(self, tx: Transaction, device_id: str) -> dict:
        device = tx.get("devices", _uuid(device_id))
        _require(device is not None and device.get("active") is True, "Active device is absent")
        assert device is not None
        _require(type(device.get("auth_generation")) is int and
                 type(device.get("key_generation")) is int and
                 0 < device["key_generation"] <= JS_SAFE_MAX and
                 key_fingerprint(device["public_key"]) == device["key_sha256"],
                 "Device identity metadata is malformed")
        subject = self._subject(tx, device["subject_uuid"])
        self._device_denied(tx, device["subject_uuid"], device_id)
        self._extension(tx, device["extension_id"], device["executor_revision"])
        _require(subject["auth_generation"] == device["auth_generation"] and
                 _exact(tx.get("pairing_key_owners", device["key_sha256"]), {
                     "subject_uuid": device["subject_uuid"], "device_id": device_id}), "Device generation/owner changed")
        return device

    def refresh_challenge(self, device_id: str) -> dict:
        def operation(tx: Transaction) -> dict:
            control, now = self._control(tx), self._now(tx)
            device = self._device(tx, device_id)
            nonce, challenge_id = str(uuid4()), str(uuid4())
            payload = {"protocol_version": 2, "operation": "refresh_claim", "audience": "hirewiz:pairing-only",
                "challenge_id": challenge_id, "device_id": device_id, "subject_uuid": device["subject_uuid"],
                "key_sha256": device["key_sha256"], "key_generation": device["key_generation"],
                "auth_generation": device["auth_generation"], "authority_id": control["authority_id"],
                "authority_incarnation": control["incarnation"], "epoch_id": control["epoch_id"],
                "epoch_generation": control["generation"], "nonce_sha256": nonce_digest(nonce),
                "issued_at_ms": now, "expires_at_ms": now + DEVICE_MS}
            tx.put("pairing_challenges", challenge_id, {"kind": "refresh", "payload": payload,
                "public_key": device["public_key"], "expires_at_ms": payload["expires_at_ms"],
                "consumed_by": None}, immutable=True)
            tx.append(str(uuid4()), "PAIRING_REFRESH_CHALLENGE", {"challenge_id": challenge_id})
            return {"payload": payload, "nonce": nonce}
        return self.store.transact(operation)

    def refresh_claim(self, challenge_id: str, nonce: str, signature: str) -> dict:
        challenge = self._challenge(challenge_id)
        _require(challenge["kind"] == "refresh", "Fresh refresh challenge is required")
        verify_signature(challenge["public_key"], "device-challenge", challenge["payload"], signature)
        def operation(tx: Transaction) -> dict:
            now, payload = self._now(tx), challenge["payload"]
            _require(tx.get("pairing_challenges", challenge_id) == challenge and challenge["consumed_by"] is None
                     and payload["operation"] == "refresh_claim" and now < challenge["expires_at_ms"]
                     and payload["nonce_sha256"] == nonce_digest(nonce), "Refresh proof expired or consumed")
            control, device = self._control(tx), self._device(tx, payload["device_id"])
            self._epoch(control, payload)
            _require(all(device[key] == payload[key] for key in (
                "subject_uuid", "key_sha256", "key_generation", "auth_generation",
            )), "Refresh ownership changed")
            tx.put("pairing_challenges", challenge_id, {**challenge, "consumed_by": str(uuid4())})
            event = tx.append(str(uuid4()), "PAIRING_CLAIM_REFRESHED", {"device_id": payload["device_id"],
                "challenge_id": challenge_id})
            return self._claim(device, control, event, now)
        claim = self.store.transact(operation)
        return {"status": "IDENTIFIED", "device_claim": self.claims.sign(claim)}

    def revocation_challenge(self, device_id: str, subject_id: str, session_id: str) -> dict:
        def operation(tx: Transaction) -> dict:
            control, now = self._control(tx), self._now(tx)
            context = {"device_id": _uuid(device_id), "authority_id": control["authority_id"],
                "authority_incarnation": control["incarnation"], "epoch_id": control["epoch_id"],
                "epoch_generation": control["generation"]}
            return self._candidate_challenge(tx, subject_id=subject_id, session_id=session_id,
                operation="revoke_device", context=context, expires=now + CANDIDATE_MS, now=now)
        return self.store.transact(operation)

    def revoke_device(self, challenge_id: str, nonce: str, envelope: dict) -> dict:
        challenge = self._challenge(challenge_id)
        payload = challenge["payload"]
        assertion = self.assertions.verify(envelope, binding_sha256=digest("candidate-context", payload),
                                           operation="revoke_device", now_ms=self.now_ms())
        def operation(tx: Transaction) -> dict:
            now = self._now(tx)
            _require(tx.get("pairing_challenges", challenge_id) == challenge and challenge["kind"] == "candidate"
                     and payload["operation"] == "revoke_device" and challenge["consumed_by"] is None
                     and now < challenge["expires_at_ms"] and payload["nonce_sha256"] == nonce_digest(nonce),
                     "Fresh revocation challenge is required")
            self._epoch(self._control(tx), payload)
            self._candidate(tx, assertion, now)
            _require(all(str(getattr(assertion, name)) == str(payload[name]) for name in (
                "subject_uuid", "session_id", "principal_sha256", "auth_generation",
            )), "Revocation belongs to another challenge principal/session")
            device_id, subject_id = payload["device_id"], payload["subject_uuid"]
            device = tx.get("devices", device_id)
            if device is None:
                lookup = tx.get("pairing_device_requests", device_id)
                _require(lookup is not None, "Device request is absent")
                assert lookup is not None
                row, request = self._request_record(tx, lookup["pairing_id"])
                _require(row["state"] == "CANDIDATE_CONFIRMED", "Only confirmed owned pairing can be cancelled")
                # Current fresh owner authentication authorizes cancellation. Historical
                # confirmation establishes ownership even after its session/deadline stops.
                confirmation = self._confirmation_evidence(tx, row, request)
                _require(confirmation["context"]["subject_uuid"] == subject_id, "Pairing belongs to another owner")
                tx.put("pairing_requests", lookup["pairing_id"], {**row, "state": "REVOKED"})
            else:
                _require(device["subject_uuid"] == subject_id, "Device belongs to another owner")
                tx.put("devices", device_id, {**device, "active": False})
            scope = Revocation(subject_uuid=UUID(subject_id), kind="device", target=device_id, revision=0)
            tombstone = {"scope": scope.model_dump(mode="json"), "device_id": device_id,
                         "subject_uuid": subject_id}
            tx.put("revocations", scope.key, tombstone, immutable=True)
            tx.put("pairing_device_tombstones", device_id, tombstone, immutable=True)
            tx.put("pairing_assertions", str(assertion.assertion_id), {"revoked_device_id": device_id}, immutable=True)
            tx.put("pairing_challenges", challenge_id, {**challenge, "consumed_by": device_id})
            event = tx.append(str(uuid4()), "PAIRING_DEVICE_REVOKED", tombstone)
            return {"status": "REVOKED", "device_id": device_id, "event_sequence": event["sequence"]}
        return self.store.transact(operation)

    def status(self, pairing_id: str) -> dict:
        """Redacted status only: no identity claim reconstruction or raw nonce/key."""
        def read(tx: Transaction) -> dict:
            self._control(tx)
            now = self._now(tx, record=False)
            row = tx.get("pairing_requests", _uuid(pairing_id))
            _require(row is not None, "Pairing request is absent")
            assert row is not None
            request = PairingRequest.model_validate(row["request"])
            state = row["state"]
            confirmation = tx.get("pairing_confirmations", pairing_id)
            if confirmation:
                subject_id = confirmation["context"]["subject_uuid"]
                try:
                    self._subject(tx, subject_id)
                    self._device_denied(tx, subject_id, str(request.device_id))
                except GuardDenied:
                    state = "REVOKED"
            if state not in {"COMPLETED", "REVOKED"} and now >= request.expires_at_ms:
                state = "EXPIRED"
            return {"pairing_id": pairing_id, "device_id": str(request.device_id), "status": state}
        return self.store.transact(read)
