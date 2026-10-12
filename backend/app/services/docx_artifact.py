"""Prepare once in the existing private worker; reuse exact sealed DOCX bytes.

Only preparation can acquire an identity or call the worker. Download verification
is local, byte-bound and preserves historical receipts when the worker changes.
No renderer/parser fallback or retry exists here.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import re
from typing import Any

import httpx

from .docx_layout_validation import LAYOUT_REASONS, VALIDATOR, check_layout_receipt
from .resume_layout import ResumeLayoutError, _apply_docx

MAX_FILE = 5 * 1024 * 1024
MAX_REQUEST = 7 * 1024 * 1024
MAX_RESPONSE = 32 * 1024
_HASH = re.compile(r"[a-f0-9]{64}\Z")
_UNAVAILABLE = "The private DOCX renderer is unavailable or could not verify this edit. Your original file is preserved; use the original/custom resume or try a new version after the worker is verified."
_REFUSED = "This DOCX edit could not retain its original rendered layout. Shorten the wording, choose another passage or use the original/custom resume."
_LAYOUT_REFUSED = "Could not verify preservation of the original layout for this DOCX edit. Shorten the wording, choose another passage or use the original/custom resume."
_LINK_REFUSED = "This DOCX edit would change an existing resume link or its placement. Keep linked text and the destination unchanged, shorten surrounding wording, or use the original/custom resume."


def _sha(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False).encode()


def _pairs(items):
    result = {}
    for key, value in items:
        if key in result:
            raise ValueError
        result[key] = value
    return result


def _binding(source: bytes, candidate: bytes, edits: list[dict]) -> dict:
    return {"version": 1, "operation": "docx_layout", "sha256": _sha(source), "size_bytes": len(source),
        "source_format": "docx", "candidate_sha256": _sha(candidate), "candidate_size_bytes": len(candidate),
        "edits_sha256": _sha(_canonical(edits))}


def _check_binding(data: dict, expected: dict) -> None:
    if any(type(data.get(key)) is not type(value) or data.get(key) != value for key, value in expected.items()):
        raise ValueError


def _scan(data: Any) -> None:
    if (type(data) is not dict or set(data) != {"engine", "version", "definitions"} or data["engine"] != "ClamAV"
            or any(type(data[key]) is not str or not 0 < len(data[key]) <= 128
                or any(ord(c) < 32 or ord(c) > 126 for c in data[key]) for key in ("version", "definitions"))):
        raise ValueError


def _check_success(data: Any, expected: dict, *, image: str, policy: str,
                   version: str | None = None, fonts: str | None = None) -> dict:
    if (type(data) is not dict or set(data) != set(expected) | {"worker_image", "policy_sha256", "scan", "candidate_scan", "layout"}
            or data["worker_image"] != image or data["policy_sha256"] != policy):
        raise ValueError
    _check_binding(data, expected)
    _scan(data["scan"])
    _scan(data["candidate_scan"])
    if data["scan"] != data["candidate_scan"]:
        raise ValueError
    check_layout_receipt(data["layout"], source_sha256=expected["sha256"], source_size_bytes=expected["size_bytes"],
        candidate_sha256=expected["candidate_sha256"], candidate_size_bytes=expected["candidate_size_bytes"],
        edits_sha256=expected["edits_sha256"], image=image, policy=policy, renderer_version=version,
        font_manifest_sha256=fonts)
    return data


async def _worker_http(config, payload: bytes, token: str, expected: dict, edits: list[dict]) -> bytes:
    from .document_ingestion import _tls_context
    async with asyncio.timeout(75):
        async with httpx.AsyncClient(timeout=httpx.Timeout(75, connect=5), verify=_tls_context(),
                                     follow_redirects=False, trust_env=False) as client:
            async with client.stream("POST", config.audience + "/validate-docx", content=payload, headers={
                "Authorization": "Bearer " + token, "Content-Type": "application/json", "Accept-Encoding": "identity",
                "X-HireWiz-Document-Policy": config.policy,
            }) as response:
                if (response.status_code not in {200, 422}
                        or response.headers.get("content-type", "").split(";", 1)[0] != "application/json"
                        or response.headers.get("content-encoding", "identity").lower() != "identity"):
                    raise ValueError
                output = bytearray()
                async for chunk in response.aiter_raw(chunk_size=8192):
                    if len(output) + len(chunk) > (4096 if response.status_code == 422 else MAX_RESPONSE):
                        raise ValueError
                    output.extend(chunk)
                if response.status_code == 422:
                    data = json.loads(output, object_pairs_hook=_pairs)
                    if (type(data) is not dict or set(data) != set(expected) | {"worker_image", "policy_sha256", "error", "reason", "unit_id"}
                            or data["worker_image"] != config.image or data["policy_sha256"] != config.policy
                            or data["error"] != "docx_layout_refused" or data["reason"] not in LAYOUT_REASONS | {"document_safety"}
                            or (data["unit_id"] is not None and not any(type(edit) is dict
                                and edit.get("unit_id") == data["unit_id"] for edit in edits))):
                        raise ValueError
                    _check_binding(data, expected)
                    if data["reason"] == "hyperlink_changed":
                        message = _LINK_REFUSED
                    elif data["reason"] in LAYOUT_REASONS:
                        message = _LAYOUT_REFUSED
                    else:
                        message = _REFUSED
                    raise ResumeLayoutError(message, unit_id=data["unit_id"], repair_hint=message)
                return bytes(output)


def prepare_artifact(source: bytes, edits: list[dict]) -> dict | None:
    """Return a custody-ready seal only after exact native render validation."""
    if type(source) is not bytes or not 0 < len(source) <= MAX_FILE:
        raise ResumeLayoutError(_REFUSED)
    try:
        candidate = _apply_docx(source, edits)
    except ResumeLayoutError:
        raise
    except Exception:
        raise ResumeLayoutError(_REFUSED) from None
    if candidate == source:
        return None
    if not 0 < len(candidate) <= MAX_FILE:
        raise ResumeLayoutError(_REFUSED)
    from .document_ingestion import _identity_token, worker_config
    try:
        config = worker_config()
        version = os.environ.get("DOCUMENT_WORKER_DOCX_RENDERER_VERSION", "")
        fonts = os.environ.get("DOCUMENT_WORKER_DOCX_FONT_MANIFEST_SHA256", "")
        if (not 0 < len(version) <= 200 or any(ord(c) < 32 or ord(c) > 126 for c in version)
                or not _HASH.fullmatch(fonts)):
            raise ValueError
        expected = _binding(source, candidate, edits)
        payload = _canonical({"version": 1, "source_format": "docx", "sha256": expected["sha256"],
            "content_base64": base64.b64encode(source).decode("ascii"), "edits": edits,
            "candidate_sha256": expected["candidate_sha256"], "candidate_size_bytes": len(candidate),
            "worker_image": config.image, "policy_sha256": config.policy})
        if len(payload) > MAX_REQUEST:
            raise ValueError
        token = _identity_token(config.audience)
        response = asyncio.run(_worker_http(config, payload, token, expected, edits))
        validation = _check_success(json.loads(response, object_pairs_hook=_pairs), expected,
            image=config.image, policy=config.policy, version=version, fonts=fonts)
        return {"validator": VALIDATOR, "source_sha256": expected["sha256"], "edits_sha256": expected["edits_sha256"],
            "content_sha256": expected["candidate_sha256"], "content_size_bytes": len(candidate),
            "content_base64": base64.b64encode(candidate).decode("ascii"), "validation": validation}
    except ResumeLayoutError:
        raise
    except Exception:
        raise ResumeLayoutError(_UNAVAILABLE) from None


def sealed_bytes(seal: dict | None, source: bytes, edits: list[dict]) -> bytes:
    """No HTTP/render at download; exact prepared bytes and source/edit binding."""
    try:
        if type(source) is not bytes or not 0 < len(source) <= MAX_FILE or type(edits) is not list:
            raise ValueError
        if not edits or (seal is None and _apply_docx(source, edits) == source):
            return source
        if (type(seal) is not dict or set(seal) != {"validator", "source_sha256", "edits_sha256", "content_sha256",
                "content_size_bytes", "content_base64", "validation"} or seal["validator"] != VALIDATOR
                or seal["source_sha256"] != _sha(source) or seal["edits_sha256"] != _sha(_canonical(edits))
                or type(seal["content_base64"]) is not str or len(seal["content_base64"]) > 7 * 1024 * 1024):
            raise ValueError
        content = base64.b64decode(seal["content_base64"], validate=True)
        if (not 0 < len(content) <= MAX_FILE or type(seal["content_size_bytes"]) is not int
                or len(content) != seal["content_size_bytes"] or _sha(content) != seal["content_sha256"]):
            raise ValueError
        validation = seal["validation"]
        _check_success(validation, _binding(source, content, edits), image=validation["worker_image"],
                       policy=validation["policy_sha256"])
        return content
    except Exception:
        raise ResumeLayoutError("This DOCX version has no intact sealed render validation. Prepare a new version before review or approval; your original file is preserved.") from None
