"""Private isolated GCP document-worker transport; no public compiler fallback."""
from __future__ import annotations

import json
import re
from urllib.parse import urlsplit

import httpx

from .resume_layout import ResumeLayoutError


def remote_compile(payload: bytes, *, url: str, image: str, policy: str) -> dict:
    target = urlsplit(url)
    if (target.scheme != "https" or not re.fullmatch(r"[a-z0-9.-]+\.run\.app", target.hostname or "")
            or target.username or target.password or target.port is not None
            or target.path != "/compile" or target.query or target.fragment
            or not re.fullmatch(r"[a-f0-9]{64}", policy)
            or not re.fullmatch(r"[a-zA-Z0-9._/:-]+@sha256:[a-f0-9]{64}", image)):
        raise ResumeLayoutError("The private TeX worker isolation contract is unavailable. Use your original/custom PDF until the reviewed worker is configured.")
    from google.auth.transport.requests import Request
    from google.oauth2.id_token import fetch_id_token
    audience = f"https://{target.netloc}"
    try:
        token = fetch_id_token(Request(), audience)
        with httpx.Client(timeout=httpx.Timeout(35, connect=5), follow_redirects=False, trust_env=False) as client:
            with client.stream("POST", url, content=payload,
                    headers={"Authorization": "Bearer " + token, "Content-Type": "application/json",
                             "X-HireWiz-TeX-Policy": policy}) as response:
                if response.status_code != 200:
                    raise ValueError("worker_refused")
                output = bytearray()
                for chunk in response.iter_bytes():
                    output.extend(chunk)
                    if len(output) > 15 * 1024 * 1024:
                        raise ValueError("worker_output_too_large")
        data = json.loads(output)
        if data.get("renderer_image") != image or data.get("isolation_policy_sha256") != policy:
            raise ValueError("worker_contract_mismatch")
        return data
    except Exception as exc:
        # Token/provider details, payloads and compiler logs are never exposed.
        raise ResumeLayoutError("The private TeX worker is unavailable or refused this project. Check source dependencies or use the original/custom PDF; no host compilation was attempted.") from exc
