"""One bounded Resend request; no scheduling, approval or automatic HTTP retry.

The worker callback is synchronous. Its HTTP operation runs on a private async
loop with a total deadline covering response streaming, rather than only an
inactivity timeout. This module never logs bodies, addresses, credentials or
provider exception text.
"""
from __future__ import annotations

import asyncio
import hashlib
import html
import json
import math
import re
import ssl
import time
from dataclasses import dataclass
from datetime import UTC
from email.utils import format_datetime, parsedate_to_datetime
from threading import Lock
from typing import Literal

import certifi
import httpx

from .mail_budget import ProviderResult

ENDPOINT = "https://api.resend.com/emails"
MAX_RESPONSE_BYTES = 16 * 1024
HTTP_DEADLINE_SECONDS = 10.0
MAX_RETRY_AFTER_SECONDS = 60
_UNKNOWN_SHA256 = hashlib.sha256(b"provider-mail-outcome-unknown").hexdigest()


@dataclass(frozen=True, slots=True, repr=False)
class _FrozenMessage:
    endpoint: str
    idempotency_key: str
    body: bytes
    sha256: str


class RetryUnavailable(RuntimeError):
    def __init__(self):
        super().__init__("provider_mail_retry_after_unavailable")


def _strict_json(raw: bytes):
    def pairs(items):
        value = {}
        for key, item in items:
            if key in value:
                raise ValueError
            value[key] = item
        return value

    return json.loads(raw.decode("utf-8"), object_pairs_hook=pairs,
        parse_constant=lambda _value: (_ for _ in ()).throw(ValueError()))


def _header(response: httpx.Response, name: str) -> str | None:
    values = response.headers.get_list(name)
    if len(values) > 1:
        raise ValueError
    return values[0] if values else None


def _retry_after(response: httpx.Response) -> int | None:
    """Only bounded actual provider intervals; never guess a missing delay."""
    try:
        value = _header(response, "Retry-After")
        if value is None:
            return None
        if re.fullmatch(r"0|[1-9][0-9]{0,3}", value):
            seconds = int(value)
        else:
            date_value = _header(response, "Date")
            if date_value is None:
                return None
            due, issued = parsedate_to_datetime(value), parsedate_to_datetime(date_value)
            if (due.tzinfo is None or issued.tzinfo is None
                    or format_datetime(due.astimezone(UTC), usegmt=True) != value
                    or format_datetime(issued.astimezone(UTC), usegmt=True) != date_value):
                return None
            # Wait the full provider-relative interval after receiving its reply.
            # No local wall-clock estimate may shorten the provider's request.
            seconds = int((due-issued).total_seconds())
        return seconds if 0 <= seconds <= MAX_RETRY_AFTER_SECONDS else None
    except (ValueError, TypeError, OverflowError):
        return None


class ResendAttempt:
    def __init__(self, *, api_key: str, sender: str, recipient: str, subject: str,
                 text: str, idempotency_key: str):
        self._api_key = api_key
        body = {"from": sender, "to": [recipient], "subject": subject, "text": text,
            "html": f"<p>{html.escape(text).replace(chr(10), '<br>')}</p>"}
        raw = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False).encode("ascii")
        parts = (ENDPOINT.encode("ascii"), idempotency_key.encode("utf-8"), raw)
        binding = b"hirewiz:resend-message:v1\0" + b"".join(len(part).to_bytes(8, "big") + part for part in parts)
        self._message = _FrozenMessage(ENDPOINT, idempotency_key, raw, hashlib.sha256(binding).hexdigest())
        self._last_result: ProviderResult | None = None
        self._retry_seconds: int | None = None
        self._retry_consumed = False
        self._lock = Lock()

    @property
    def message_sha256(self) -> str:
        """Exact frozen outgoing request; authentication rotation is excluded."""
        return self._message.sha256

    @property
    def last_status_code(self) -> int | None:
        return self._last_result.status_code if self._last_result is not None else None

    @property
    def last_receipt_sha256(self) -> str | None:
        return self._last_result.receipt_sha256 if self._last_result is not None else None

    @property
    def retry_after_seconds(self) -> int | None:
        return self._retry_seconds

    def __call__(self) -> ProviderResult:
        if not self._lock.acquire(blocking=False):
            return ProviderResult("unknown", _UNKNOWN_SHA256)
        try:
            self._last_result, self._retry_seconds, self._retry_consumed = None, None, False
            try:
                asyncio.get_running_loop()
            except RuntimeError:
                pass
            else:
                # Do not nest an event loop, launch a background send or return
                # an unawaited coroutine from a synchronous worker callback.
                return ProviderResult("unknown", _UNKNOWN_SHA256)
            result, retry = asyncio.run(self._request())
            self._last_result, self._retry_seconds = result, retry
            return result
        except Exception:
            return ProviderResult("unknown", _UNKNOWN_SHA256)
        finally:
            self._lock.release()

    async def _request(self) -> tuple[ProviderResult, int | None]:
        body = bytearray()
        status = None
        try:
            # Explicit context construction does not adopt SSLKEYLOGFILE,
            # SSL_CERT_FILE/DIR or arbitrary process-provided CA settings.
            tls = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
            tls.load_verify_locations(cafile=certifi.where())
            if tls.keylog_filename is not None or tls.verify_mode != ssl.CERT_REQUIRED or not tls.check_hostname:
                raise ValueError
            async with asyncio.timeout(HTTP_DEADLINE_SECONDS):
                transport = httpx.AsyncHTTPTransport(verify=tls, trust_env=False, retries=0,
                    http1=True, http2=False)
                async with httpx.AsyncClient(transport=transport, trust_env=False, follow_redirects=False,
                        timeout=httpx.Timeout(HTTP_DEADLINE_SECONDS, connect=5.0)) as client:
                    async with client.stream("POST", self._message.endpoint, headers={"Authorization": f"Bearer {self._api_key}",
                            "Idempotency-Key": self._message.idempotency_key, "Accept": "application/json",
                            "Accept-Encoding": "identity", "Content-Type": "application/json"}, content=self._message.body) as response:
                        status = response.status_code
                        if (response.url != httpx.URL(ENDPOINT) or response.history
                                or response.request.method != "POST" or response.request.url != httpx.URL(ENDPOINT)):
                            raise ValueError
                        content_type = _header(response, "Content-Type")
                        encoding, length = _header(response, "Content-Encoding"), _header(response, "Content-Length")
                        if (content_type is None or content_type.split(";", 1)[0].strip().lower() != "application/json"
                                or encoding is not None and encoding.strip().lower() != "identity"
                                or length is not None and (re.fullmatch(r"0|[1-9][0-9]{0,5}", length) is None
                                    or int(length) > MAX_RESPONSE_BYTES)):
                            raise ValueError
                        async for chunk in response.aiter_raw(chunk_size=1024):
                            if type(chunk) is not bytes or len(body)+len(chunk) > MAX_RESPONSE_BYTES:
                                raise ValueError
                            body.extend(chunk)
                        if not body or length is not None and int(length) != len(body):
                            raise ValueError
                        value = _strict_json(bytes(body))
                        outcome: Literal["sent", "unknown", "permanent", "known_retryable"] = "unknown"
                        retry = None
                        if (200 <= status < 300 and type(value) is dict and set(value) == {"id"}
                                and type(value["id"]) is str and re.fullmatch(r"[A-Za-z0-9_-]{8,200}", value["id"])):
                            outcome = "sent"
                        elif status == 429:
                            outcome, retry = "known_retryable", _retry_after(response)
                        elif 400 <= status < 500 and status != 408:
                            outcome = "permanent"
                        result = ProviderResult(outcome, hashlib.sha256(body).hexdigest(), status)
                return result, retry
        except Exception:
            # Partial/oversized/invalid bodies and timeouts never establish a
            # definite retry or rejection, even if headers already said 429.
            return ProviderResult("unknown", hashlib.sha256(body).hexdigest() if body else _UNKNOWN_SHA256, status), None

    def before_retry(self, result: ProviderResult) -> None:
        """Consume one actual 429 wait; caller must already own its native ACK."""
        if not self._lock.acquire(blocking=False):
            raise RetryUnavailable
        try:
            last = self._last_result
            if (last is None or result is not last or last.outcome != "known_retryable"
                    or last.status_code != 429 or self._retry_seconds is None or self._retry_consumed):
                raise RetryUnavailable
            self._retry_consumed = True
            started = time.monotonic()
            deadline = started+self._retry_seconds
            for _ in range(3):
                current = time.monotonic()
                if not math.isfinite(current) or current < started:
                    raise RetryUnavailable
                remaining = deadline-current
                if remaining <= 0:
                    return
                time.sleep(remaining)
            raise RetryUnavailable
        except RetryUnavailable:
            raise
        except Exception:
            raise RetryUnavailable from None
        finally:
            self._lock.release()
