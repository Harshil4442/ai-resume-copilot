"""Lazy exact native pairing composition; no baseline, SQL adoption or local signer."""
from __future__ import annotations

import hashlib
import json
import os
import re
import time
from datetime import UTC, datetime
from threading import Lock
from typing import TYPE_CHECKING, Literal

from pydantic import Field, model_validator

from ..candidate_accounts.production import CandidateNativeConfiguration, _pairs
from .contracts import Contract, Identifier, canonical
from .native_diagnostics import native_phase
from .pairing_auth import PinnedAssertions, public_jwk
from .store import GuardDenied, GuardUnavailable

if TYPE_CHECKING:
    from .browser_pairing import NativeBrowserPairing
    from .gcp_submit_contracts import SubmitContext

FAILURE = "Native browser pairing configuration is unavailable"


class KmsSigningConfiguration(Contract):
    version_name: str = Field(pattern=r"^projects/[a-z0-9-]+/locations/[a-z0-9-]+/keyRings/[A-Za-z0-9_-]+/cryptoKeys/[A-Za-z0-9_-]+/cryptoKeyVersions/[1-9][0-9]*$")
    endpoint: str
    protection_level: Literal["SOFTWARE", "HSM"]
    public_key: dict[str, str]

    @model_validator(mode="after")
    def exact(self):
        from .pairing_kms import _VERSION
        match = _VERSION.fullmatch(self.version_name)
        if (match is None or self.endpoint not in {"cloudkms.googleapis.com:443", f"{match.group('location')}-cloudkms.googleapis.com:443"}
                or self.endpoint == "global-cloudkms.googleapis.com:443"):
            raise ValueError("Exact official KMS version and endpoint required")
        public_jwk(self.public_key)
        return self


class BrowserPairingNativeConfiguration(CandidateNativeConfiguration):
    extension_id: str = Field(pattern=r"^[a-p]{32}$")
    executor_revision: Identifier
    assertion_issuer: Identifier
    assertion_key: KmsSigningConfiguration
    claim_key: KmsSigningConfiguration


@native_phase("browser_pairing", "configuration")
def configuration() -> BrowserPairingNativeConfiguration:
    from ..candidate_accounts.production import _configuration
    if os.environ.get("BROWSER_PAIRING_NATIVE_ENABLED") != "true":
        raise GuardUnavailable(FAILURE)
    try:
        raw = os.environ.get("BROWSER_PAIRING_NATIVE_CONFIG", "")
        if not raw or len(raw.encode()) > 16_384:
            raise ValueError("Exact bounded configuration required")
        value = BrowserPairingNativeConfiguration.model_validate(json.loads(raw, object_pairs_hook=_pairs))
        shared = _configuration()
        if any(getattr(value, key) != getattr(shared, key) for key in CandidateNativeConfiguration.model_fields):
            raise ValueError("Account and pairing resources disagree")
        secret = os.environ.get("BROWSER_PAIRING_GATEWAY_SECRET", "")
        if re.fullmatch(r"[a-f0-9]{64}", secret) is None:
            raise ValueError("Dedicated gateway secret required")
        from .browser_pairing import GatewayVerifier
        GatewayVerifier(key=bytes.fromhex(secret), issuer="hirewiz_bff_v1",
            origin=os.environ.get("BROWSER_PAIRING_WEBSITE_ORIGIN", ""))
        return value
    except Exception:
        raise GuardUnavailable(FAILURE) from None


class RefreshingKmsSigner:
    """Explicit ADC refresh at a trusted runtime boundary, outside native/SQL locks."""
    def __init__(self, configuration: KmsSigningConfiguration, *, issuer: str | None = None):
        self._raw = canonical(configuration.model_dump(mode="json"))
        self.issuer = issuer

    def sign(self, payload: dict) -> dict:
        import google.auth
        from google.auth.transport.requests import Request

        from .pairing_kms import (
            KmsCandidateAssertionIssuer,
            KmsEs256Boundary,
            KmsEs256Pin,
            KmsPairingClaimIssuer,
        )
        fixed = KmsSigningConfiguration.model_validate_json(self._raw)
        credentials, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/cloudkms"])
        request = Request()
        def bounded_request(*args, **kwargs):
            kwargs["timeout"] = 2.0
            return request(*args, **kwargs)
        if not credentials.valid:
            credentials.refresh(bounded_request)
        expiry = credentials.expiry
        now = datetime.now(UTC)
        if expiry is not None and expiry.tzinfo is None:
            expiry = expiry.replace(tzinfo=UTC)
        if (not credentials.valid or not isinstance(credentials.token, str) or expiry is None
                or not 0 < (expiry - now).total_seconds() <= 3_605):
            raise GuardUnavailable("A fresh bounded KMS credential is required")
        boundary = KmsEs256Boundary(KmsEs256Pin(**fixed.model_dump()), access_token=credentials.token)
        try:
            def clock() -> int:
                return time.time_ns() // 1_000_000
            if self.issuer is not None:
                return KmsCandidateAssertionIssuer(boundary, issuer=self.issuer, now_ms=clock).sign(payload)
            return KmsPairingClaimIssuer(boundary, now_ms=clock).sign(payload)
        finally:
            boundary.close()


class ConfiguredBrowserPairing:
    def __init__(self, value: BrowserPairingNativeConfiguration):
        self._raw = canonical(value.model_dump(mode="json"))
        self._gateway_digest = hashlib.sha256(os.environ["BROWSER_PAIRING_GATEWAY_SECRET"].encode()).digest()
        self._origin = os.environ["BROWSER_PAIRING_WEBSITE_ORIGIN"]
        self._native: NativeBrowserPairing | None = None
        self._lock = Lock()

    @native_phase("browser_pairing", "service")
    def _service(self) -> NativeBrowserPairing:
        current = configuration()
        if (canonical(current.model_dump(mode="json")) != self._raw
                or hashlib.sha256(os.environ["BROWSER_PAIRING_GATEWAY_SECRET"].encode()).digest() != self._gateway_digest
                or os.environ["BROWSER_PAIRING_WEBSITE_ORIGIN"] != self._origin):
            raise GuardDenied("Browser pairing deployment configuration changed")
        with self._lock:
            if self._native is None:
                from ..browser_assistance.native_pairing import activation_port
                from ..candidate_accounts.production import production_candidate_accounts
                from .browser_pairing import GatewayVerifier, NativeBrowserPairing
                from .gcp_journal import GcsJournal
                from .gcp_paired_devices import ProtectedPairingDevices
                from .gcp_pairing import GcpPairingCoordinator
                from .gcp_pairing_attempts import GcsPairingAttempts
                from .gcp_password import GcpPasswordCandidateBoundary
                from .gcp_password_attempts import GcsPasswordSigning
                from .password_reauth import SqlPasswordCredentials
                with native_phase("browser_pairing", "account_composition"):
                    accounts = production_candidate_accounts()._composition()
                lifetime, scoped = accounts.lifetime, accounts.scoped
                publication = scoped.publication
                bucket = publication.bucket
                assertions = PinnedAssertions(issuer=current.assertion_issuer, public_key=current.assertion_key.public_key)
                coordinator = GcpPairingCoordinator(lifetime.registry,
                    GcsJournal(bucket, publication=publication), lifetime.pin,
                    epoch_generation=current.epoch_generation, fence=accounts.fence,
                    assertions=assertions, claims=RefreshingKmsSigner(current.claim_key),
                    attempts=GcsPairingAttempts(bucket, publication=publication),
                    password_signing=GcsPasswordSigning(bucket), now_ms=lifetime.now_ms)
                credentials = SqlPasswordCredentials(accounts.service.sessions.kw["bind"],
                    dummy_hash=accounts.service._dummy_hash, reuse_pool=True)
                projection = activation_port(accounts.service.sessions.kw["bind"])
                devices = ProtectedPairingDevices(coordinator, scoped,
                    activated=projection.activated, deny_sql=projection.deny_sql)
                password = GcpPasswordCandidateBoundary(coordinator, scoped=scoped, paired_devices=devices).service(
                    credentials=credentials, signer=RefreshingKmsSigner(current.assertion_key, issuer=current.assertion_issuer),
                    issuer=current.assertion_issuer)
                gateway = GatewayVerifier(key=bytes.fromhex(os.environ["BROWSER_PAIRING_GATEWAY_SECRET"]),
                    issuer="hirewiz_bff_v1", origin=os.environ["BROWSER_PAIRING_WEBSITE_ORIGIN"])
                self._native = NativeBrowserPairing(coordinator, password, gateway, paired_devices=devices)
        assert self._native is not None
        return self._native

    def assistance_context(self, device_id: str, public_key: dict[str, str]) -> SubmitContext:
        if os.environ.get("BROWSER_ASSISTANCE_NATIVE_PAIRING_ENABLED") != "true":
            raise GuardUnavailable("Native assistance pairing precheck is disabled")
        from .pairing_auth import public_jwk
        fixed_key = public_jwk(public_key)
        current = configuration()
        native = self._service()
        context = native.paired_devices.assistance_context(device_id, fixed_key,
            extension_id=current.extension_id, executor_revision=current.executor_revision)
        self._service()
        if os.environ.get("BROWSER_ASSISTANCE_NATIVE_PAIRING_ENABLED") != "true":
            raise GuardUnavailable("Native assistance pairing precheck changed")
        return context

    def candidate(self, operation: str, raw: bytes, assertion: str, signature: str) -> dict:
        native = self._service()
        with native_phase("browser_pairing", "native_operation"):
            result = native.candidate(operation, raw, assertion, signature)
        self._service()
        return result

    def device(self, operation: str, raw: bytes, origin: str) -> dict:
        native = self._service()
        current = configuration()
        if operation == "prepare":
            from .browser_pairing import exact_json
            body = exact_json(raw)
            if body.get("extension_id") != current.extension_id or body.get("revision") != current.executor_revision:
                raise GuardDenied("Device extension/revision does not match the protected deployment")
        with native_phase("browser_pairing", "native_operation"):
            result = native.device(operation, raw, origin)
        if "device_claim" in result:
            claim = result["device_claim"]["payload"]
            if claim.get("extension_id") != current.extension_id or claim.get("executor_revision") != current.executor_revision:
                raise GuardDenied("Paired device claim differs from the pinned extension deployment")
            native.paired_devices.check_claim(claim)
        self._service()
        return result


def production_browser_pairing() -> ConfiguredBrowserPairing:
    return ConfiguredBrowserPairing(configuration())
