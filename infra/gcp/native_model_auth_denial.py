"""Fresh exact-provider authentication denial for retained model capabilities.

Only fixed, read-only model-list requests are sent. No inference, mutation,
manager credential export, caller verdict or saved receipt authorizes closure.
"""
from __future__ import annotations

import json
import ssl
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

import certifi
import httpx

Provider = Literal["groq", "openai"]
ENDPOINTS = {
    "groq": "https://api.groq.com/openai/v1/models",
    "openai": "https://api.openai.com/v1/models",
}
MAX_CREDENTIALS = 32
MAX_RESPONSE_BYTES = 16384
WINDOW_SECONDS = 30


class DenialUnavailable(RuntimeError):
    """Only fixed, nonsecret errors may leave this verifier."""


def need(value: Any, reason: str) -> None:
    if not value:
        raise DenialUnavailable(reason)


def provider_for(value: Any) -> Provider | None:
    if type(value) is not str:
        return None
    need(not value.startswith("sk-admin"), "provider_admin_issuance_authority_unsupported")
    if value.startswith("gsk_"):
        return "groq"
    return "openai" if value.startswith("sk-") else None


@dataclass(frozen=True)
class ForeignModelCredential:
    provider: Provider
    value: str = field(repr=False)
    origins: tuple[Any, ...] = field(repr=False)

    def validate(self) -> None:
        need(self.provider in ENDPOINTS and provider_for(self.value) == self.provider
             and 8 <= len(self.value) <= 4096
             and all(33 <= ord(character) <= 126 for character in self.value)
             and type(self.origins) is tuple and bool(self.origins),
             "provider_retained_credential_invalid")


class Transport(Protocol):
    def get(self, credential: ForeignModelCredential, deadline: float) -> tuple[int, bytes]: ...


class NativeTransport:
    def get(self, credential: ForeignModelCredential, deadline: float) -> tuple[int, bytes]:
        credential.validate()
        remaining = deadline - time.monotonic()
        need(remaining > 0, "provider_observation_expired")
        url = ENDPOINTS[credential.provider]
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.load_verify_locations(cafile=certifi.where())
        headers = {
            "Authorization": "Bearer " + credential.value,
            "Accept": "application/json", "Accept-Encoding": "identity", "Cache-Control": "no-cache",
        }
        try:
            # Groq's normal supported SDK uses httpx. Keep this library's own
            # default User-Agent; do not imitate a browser or relax auth errors.
            transport = httpx.HTTPTransport(verify=context, retries=0, trust_env=False)
            with httpx.Client(transport=transport, verify=context, trust_env=False,
                              follow_redirects=False,
                              timeout=httpx.Timeout(min(5.0, remaining))) as client:
                with client.stream("GET", url, headers=headers) as response:
                    need(str(response.url) == url and response.status_code == 401,
                         "provider_exact_invalid_key_denial_unavailable")
                    need(response.headers.get("Content-Encoding", "identity") == "identity"
                         and response.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
                         == "application/json", "provider_denial_response_shape_invalid")
                    raw = bytearray()
                    for chunk in response.iter_raw():
                        need(type(chunk) is bytes, "provider_denial_response_shape_invalid")
                        need(len(raw) + len(chunk) <= MAX_RESPONSE_BYTES
                             and time.monotonic() < deadline,
                             "provider_denial_response_bound_or_deadline")
                        raw.extend(chunk)
                    need(time.monotonic() < deadline, "provider_observation_expired")
                    return response.status_code, bytes(raw)
        except DenialUnavailable:
            raise
        except Exception:
            # Provider/network errors can contain bearer values or a reflected
            # key in their message. Never expose or retain their representations.
            raise DenialUnavailable("provider_native_auth_denial_unavailable") from None


def _object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for name, value in pairs:
        need(name not in result, "provider_duplicate_denial_field")
        result[name] = value
    return result


def observe(credentials: tuple[ForeignModelCredential, ...], *, deadline: float | None = None,
            transport: Transport | None = None) -> None:
    """All exact captured capabilities must be denied inside one fresh window.

    An outer closure passes its original deadline; this verifier never refreshes
    that clock. The injected transport is used only by offline tests.
    """
    need(type(credentials) is tuple and len(credentials) <= MAX_CREDENTIALS,
         "provider_retained_scope_bound_exceeded")
    if not credentials:
        return
    for credential in credentials:
        need(type(credential) is ForeignModelCredential, "provider_retained_credential_invalid")
        credential.validate()
    need(len({(item.provider, item.value) for item in credentials}) == len(credentials),
         "provider_retained_scope_duplicate")
    absolute = min(deadline, time.monotonic() + WINDOW_SECONDS) if deadline is not None else time.monotonic() + WINDOW_SECONDS
    need(time.monotonic() < absolute, "provider_observation_expired")
    client = transport if transport is not None else NativeTransport()
    with ThreadPoolExecutor(max_workers=min(4, len(credentials))) as pool:
        futures = [pool.submit(client.get, credential, absolute) for credential in credentials]
        for future in futures:
            try:
                status, raw = future.result(timeout=max(0.001, absolute - time.monotonic()))
                need(status == 401 and type(raw) is bytes and len(raw) <= MAX_RESPONSE_BYTES,
                     "provider_exact_invalid_key_denial_unavailable")
                value = json.loads(raw, object_pairs_hook=_object)
                need(type(value) is dict and type(value.get("error")) is dict
                     and value["error"].get("code") == "invalid_api_key",
                     "provider_exact_invalid_key_denial_unavailable")
            except DenialUnavailable:
                raise
            except Exception:
                raise DenialUnavailable("provider_native_auth_denial_unavailable") from None
            need(time.monotonic() < absolute, "provider_observation_expired")
