"""Private isolated GCP document-worker transport; no public compiler fallback."""
from __future__ import annotations

import hashlib
import json
import re
from urllib.parse import urlsplit

import httpx

from .resume_layout import ResumeLayoutError, ResumeProcessingUnavailable


def remote_compile(payload: bytes, *, url: str, image: str, policy: str) -> dict:
    return _remote_compile(payload, url=url, image=image, policy=policy)[0]


def remote_compile_capture(payload: bytes, *, url: str, image: str, policy: str) -> bytes:
    """Retain the actual successful HTTP body through the unchanged transport.

    Internal acquisition only; this is neither a provider signature nor an
    idempotent operation. A lost response must never cause another POST.
    """
    return _remote_compile(payload, url=url, image=image, policy=policy)[1]


def _remote_compile(payload: bytes, *, url: str, image: str, policy: str) -> tuple[dict, bytes]:
    try:
        target = urlsplit(url)
        invalid = (target.scheme != "https" or not re.fullmatch(r"[a-z0-9.-]+\.run\.app", target.hostname or "")
            or target.username or target.password or target.port is not None
            or target.path != "/compile" or target.query or target.fragment
            or not re.fullmatch(r"[a-f0-9]{64}", policy)
            or not re.fullmatch(r"[a-zA-Z0-9._/:-]+@sha256:[a-f0-9]{64}", image))
    except (ValueError, TypeError):
        invalid = True
    if invalid:
        raise ResumeProcessingUnavailable("The private TeX worker isolation contract is unavailable. Use your original/custom PDF until the reviewed worker is configured.")
    from google.auth.transport.requests import Request
    from google.oauth2.id_token import fetch_id_token
    audience = f"https://{target.netloc}"
    try:
        class BoundedIdentityRequest(Request):
            def __call__(self, *args, **kwargs):
                kwargs["timeout"] = 5
                return super().__call__(*args, **kwargs)

        token = fetch_id_token(BoundedIdentityRequest(), audience)
        with httpx.Client(timeout=httpx.Timeout(50, connect=5), follow_redirects=False, trust_env=False) as client:
            with client.stream("POST", url, content=payload,
                    headers={"Authorization": "Bearer " + token, "Content-Type": "application/json",
                             "X-HireWiz-TeX-Policy": policy}) as response:
                if response.status_code not in {200, 422}:
                    raise ValueError("worker_unavailable")
                output = bytearray()
                for chunk in response.iter_bytes():
                    output.extend(chunk)
                    if len(output) > (4096 if response.status_code == 422 else 15 * 1024 * 1024):
                        raise ValueError("worker_output_too_large")
        data = json.loads(output)
        if response.status_code == 422:
            if data != {"error": "compile_refused"}:
                raise ValueError("worker_refusal_unverified")
            raise ResumeLayoutError("The private TeX worker refused this project. Check source dependencies or use the original/custom PDF.")
        if (type(data) is not dict or set(data) != {"protocol", "pdf", "sha256", "size", "renderer",
                "request_sha256", "renderer_image", "isolation_policy_sha256"}
                or data.get("protocol") != "hirewiz-tex-v1" or data.get("renderer") != "pdftex-no-shell-escape-v1"
                or data.get("request_sha256") != hashlib.sha256(payload).hexdigest()
                or data.get("renderer_image") != image or data.get("isolation_policy_sha256") != policy):
            raise ValueError("worker_contract_mismatch")
        return data, bytes(output)
    except ResumeLayoutError:
        raise
    except Exception as exc:
        # Token/provider details, payloads and compiler logs are never exposed.
        raise ResumeProcessingUnavailable("The private TeX worker is unavailable. Keep the original/custom PDF and try again later; no host compilation was attempted.") from exc
