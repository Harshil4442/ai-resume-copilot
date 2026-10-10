"""Inspect uploaded PDF/DOCX bytes outside the credential-bearing API process.

Only a configured private worker may admit a document. Its immutable image and
policy are deployment bindings, not evidence of sandbox isolation by themselves.
No local parser, scanner or model fallback exists on this production path.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import math
import os
import re
import ssl
from dataclasses import dataclass
from urllib.parse import urlsplit

import certifi
import httpx

MAX_SOURCE_BYTES = 5 * 1024 * 1024
MAX_RESPONSE_BYTES = 1024 * 1024
MAX_TEXT_BYTES = 256 * 1024
HTTP_DEADLINE_SECONDS = 75
_HASH = re.compile(r"[a-f0-9]{64}\Z")
_IMAGE = re.compile(r"[a-zA-Z0-9._/:-]+@sha256:[a-f0-9]{64}\Z")
_UNAVAILABLE = "Resume safety checks are temporarily unavailable. Please try uploading again later; no analysis units were charged."
_REFUSED = "This document could not pass the resume safety checks. Upload a plain PDF or DOCX without active content or encryption; no analysis units were charged."


class DocumentInspectionError(ValueError):
    def __init__(self, *, refused: bool = False):
        self.status_code = 422 if refused else 503
        super().__init__(_REFUSED if refused else _UNAVAILABLE)


@dataclass(frozen=True)
class WorkerConfig:
    url: str
    audience: str
    image: str
    policy: str


def worker_config() -> WorkerConfig:
    url = os.environ.get("DOCUMENT_WORKER_URL", "")
    image = os.environ.get("DOCUMENT_WORKER_IMAGE_DIGEST", "")
    policy = os.environ.get("DOCUMENT_WORKER_POLICY_SHA256", "")
    try:
        target = urlsplit(url)
        if (
            target.scheme != "https"
            or not re.fullmatch(r"[a-z0-9-]+(?:\.[a-z0-9-]+)*\.run\.app", target.hostname or "")
            or target.username is not None or target.password is not None
            or target.port is not None or target.path != "/inspect"
            or target.query or target.fragment
            or len(url) > 512 or len(image) > 512
            or not _IMAGE.fullmatch(image) or not _HASH.fullmatch(policy)
        ):
            raise ValueError
        return WorkerConfig(url, f"https://{target.netloc}", image, policy)
    except ValueError:
        raise DocumentInspectionError() from None


def _identity_token(audience: str) -> str:
    # The metadata/auth request also must not inherit HTTP proxies or custom
    # certificate overrides from the API environment.
    import requests
    from google.auth.transport.requests import Request
    from google.oauth2.id_token import fetch_id_token

    class PrivateTLSAdapter(requests.adapters.HTTPAdapter):
        def init_poolmanager(self, *args, **kwargs):
            kwargs["ssl_context"] = _tls_context()
            return super().init_poolmanager(*args, **kwargs)

    with requests.Session() as session:
        session.trust_env = False
        session.verify = certifi.where()
        session.mount("https://", PrivateTLSAdapter())
        transport = Request(session=session)

        def bounded_request(*args, **kwargs):
            kwargs["timeout"] = 5
            return transport(*args, **kwargs)

        token = fetch_id_token(bounded_request, audience)
    if not isinstance(token, str) or not token or len(token) > 16 * 1024:
        raise DocumentInspectionError()
    return token


def _tls_context() -> ssl.SSLContext:
    # create_default_context can enable SSLKEYLOGFILE from the environment.
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.load_verify_locations(cafile=certifi.where())
    return context


def _text(value: object, limit: int) -> str:
    if not isinstance(value, str) or len(value.encode("utf-8")) > limit or "\x00" in value:
        raise ValueError
    return value


def _pairs(items):
    result = {}
    for key, value in items:
        if key in result:
            raise ValueError
        result[key] = value
    return result


def _decode(response: bytes, source: bytes, kind: str, config: WorkerConfig) -> tuple:
    data = json.loads(response, object_pairs_hook=_pairs)
    if not isinstance(data, dict) or (
        type(data.get("version")) is not int or data["version"] != 1
        or data.get("sha256") != hashlib.sha256(source).hexdigest()
        or type(data.get("size_bytes")) is not int or data["size_bytes"] != len(source)
        or data.get("source_format") != kind
        or data.get("worker_image") != config.image
        or data.get("policy_sha256") != config.policy
    ):
        raise ValueError
    scan = data.get("scan")
    if not isinstance(scan, dict) or scan.get("engine") != "ClamAV":
        raise ValueError
    for name in ("version", "definitions"):
        value = _text(scan.get(name), 200)
        if not value or any(ord(character) < 32 or ord(character) > 126 for character in value):
            raise ValueError
    parsed = data.get("parsed")
    if not isinstance(parsed, dict):
        raise ValueError
    raw = _text(parsed.get("raw_text"), MAX_TEXT_BYTES)
    sections = parsed.get("sections")
    if not isinstance(sections, dict) or len(sections) > 30:
        raise ValueError
    for key, value in sections.items():
        _text(key, 80)
        _text(value, MAX_TEXT_BYTES)
    if sum(len(value.encode("utf-8")) for value in sections.values()) > MAX_TEXT_BYTES:
        raise ValueError
    skills = parsed.get("skills")
    if not isinstance(skills, list) or len(skills) > 500:
        raise ValueError
    for skill in skills:
        _text(skill, 200)
    years = parsed.get("experience_years")
    if isinstance(years, bool) or not isinstance(years, (int, float)) or not math.isfinite(years) or not 0 <= years <= 40:
        raise ValueError
    contact = parsed.get("contact_info")
    if not isinstance(contact, dict) or set(contact) - {"name", "email", "phone", "linkedin", "github"}:
        raise ValueError
    for value in contact.values():
        if value is not None:
            _text(value, 1000)
    return raw, sections, skills, float(years), contact


def _refusal_matches(response: bytes, payload: bytes, config: WorkerConfig) -> bool:
    data = json.loads(response, object_pairs_hook=_pairs)
    request = json.loads(payload)
    return (type(data) is dict
        and set(data) == {"version", "sha256", "size_bytes", "source_format", "worker_image", "policy_sha256", "error"}
        and type(data["version"]) is int and data["version"] == 1
        and data["sha256"] == request["sha256"]
        and type(data["size_bytes"]) is int
        and data["size_bytes"] == len(base64.b64decode(request["content_base64"], validate=True))
        and data["source_format"] == request["source_format"]
        and data["worker_image"] == config.image and data["policy_sha256"] == config.policy
        and data["error"] == "document_inspection_refused")


async def _inspect_http(config: WorkerConfig, payload: bytes, token: str) -> bytes:
    # Cancellation closes the async socket even when a peer keeps trickling
    # bytes. Per-read timeouts alone do not provide a total request deadline.
    async with asyncio.timeout(HTTP_DEADLINE_SECONDS):
        async with httpx.AsyncClient(timeout=httpx.Timeout(75, connect=5), verify=_tls_context(),
                                     follow_redirects=False, trust_env=False) as client:
            async with client.stream("POST", config.url, content=payload, headers={
                "Authorization": "Bearer " + token, "Content-Type": "application/json",
                "Accept-Encoding": "identity", "X-HireWiz-Document-Policy": config.policy,
            }) as response:
                if response.status_code not in {200, 422}:
                    raise DocumentInspectionError()
                if (response.headers.get("content-type", "").split(";", 1)[0] != "application/json"
                        or response.headers.get("content-encoding", "identity").lower() != "identity"):
                    raise ValueError
                limit = 4096 if response.status_code == 422 else MAX_RESPONSE_BYTES
                output = bytearray()
                async for chunk in response.aiter_raw(chunk_size=64 * 1024):
                    if len(output) + len(chunk) > limit:
                        raise ValueError
                    output.extend(chunk)
                if response.status_code == 422:
                    if not _refusal_matches(bytes(output), payload, config):
                        raise ValueError
                    raise DocumentInspectionError(refused=True)
                return bytes(output)


def inspect_resume_document(source: bytes, *, source_format: str) -> tuple:
    if source_format not in {"pdf", "docx"} or not isinstance(source, bytes) or not 0 < len(source) <= MAX_SOURCE_BYTES:
        raise DocumentInspectionError(refused=True)
    config = worker_config()
    payload = json.dumps({
        "version": 1, "source_format": source_format,
        "content_base64": base64.b64encode(source).decode("ascii"),
        "sha256": hashlib.sha256(source).hexdigest(),
    }).encode("utf-8")
    try:
        token = _identity_token(config.audience)
        output = asyncio.run(_inspect_http(config, payload, token))
        return _decode(output, source, source_format, config)
    except DocumentInspectionError:
        raise
    except Exception:
        # Do not chain parser/provider exceptions into the global API logger.
        raise DocumentInspectionError() from None
