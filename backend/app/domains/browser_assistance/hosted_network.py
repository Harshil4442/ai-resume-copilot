"""Exact reviewed Greenhouse transport proofs; DOM state never proves acceptance.

The paired companion reports Chrome-native network events under its device signature.
These validators bind that report to the original one-shot lease and the complete
reviewed application. HTTPS uses the ordinary browser trust store and validation;
this contract makes no certificate-pinning or certificate-fingerprint claim. An
HTTP status or filename alone never establishes a receipt.
"""
from __future__ import annotations

import copy
import ipaddress
import os
import re
from urllib.parse import quote, urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

PRESIGN_URL = "https://boards.greenhouse.io/uncacheable_attributes/presigned_fields?fields%5B%5D=resume"
UPLOAD_VERSION = "greenhouse_owned_upload_browser_https_v2"
RECEIPT_VERSION = "greenhouse_hosted_browser_https_200_v2"
TRANSPORT = "greenhouse-owned-upload-browser-https-v2"
HEX = re.compile(r"[a-f0-9]{64}")
NETWORK_KEYS = frozenset({
    "request_id", "method", "url", "tab_id", "frame_id", "document_id", "initiator",
    "started_at_ms", "browser_major", "status_code", "https_validation",
    "request_body_sha256", "request_body_size", "request_body_source", "native_body_crosscheck",
    "completed_at_ms", "from_cache", "redirected", "ip",
})


def enabled() -> bool:
    return os.getenv("BROWSER_ASSISTANCE_NATIVE_UPLOAD_ENABLED", "false") == "true"


def _require(value):
    from .service import require
    require(value)


def _hex(value) -> bool:
    return isinstance(value, str) and HEX.fullmatch(value) is not None


def _integer(value, minimum, maximum) -> bool:
    return type(value) is int and minimum <= value <= maximum


def native_context(value) -> dict:
    _require(isinstance(value, dict) and set(value) == {"tab_id", "time_zone"})
    _require(_integer(value["tab_id"], 0, 2**31 - 1))
    zone = value["time_zone"]
    _require(isinstance(zone, str) and 1 <= len(zone) <= 100)
    try:
        ZoneInfo(zone)
    except (ValueError, ZoneInfoNotFoundError):
        _require(False)
    return copy.deepcopy(value)


def storage_origin(value) -> str:
    _require(isinstance(value, str))
    try:
        parsed = urlsplit(value)
    except ValueError:
        _require(False)
        raise AssertionError("Unreachable invalid storage origin") from None
    _require(parsed.scheme == "https" and parsed.netloc == parsed.hostname
             and parsed.path == "" and not parsed.query and not parsed.fragment
             and re.fullmatch(r"[a-z0-9][a-z0-9.-]{0,120}\.s3(?:\.[a-z0-9-]+)?\.amazonaws\.com", parsed.netloc) is not None)
    return value


def network(value, *, url, kind, start, deadline, context=None, application_sha256=None) -> dict:
    """Only a complete native exchange under the browser's normal HTTPS policy."""
    from . import service as s
    keys = NETWORK_KEYS | ({"application_sha256"} if kind == "submit" else set())
    _require(isinstance(value, dict) and set(value) == keys)
    _require(_integer(start, 1, 2**53 - 1) and _integer(deadline, start + 1, start + s.LEASE_MS))
    _require(isinstance(value["request_id"], str) and 1 <= len(value["request_id"]) <= 160)
    _require(value["url"] == url and value["method"] == ("GET" if kind == "presign" else "POST"))
    _require(_integer(value["browser_major"], 144, 999)
             and value["https_validation"] == "browser_default"
             and value["from_cache"] is False and value["redirected"] is False)
    _require(_integer(value["started_at_ms"], start, deadline)
             and _integer(value["completed_at_ms"], value["started_at_ms"], deadline))
    _require(_integer(value["status_code"], 100, 599)
             and value["status_code"] in ({200, 201, 204} if kind == "upload" else {200}))
    _require(isinstance(value["ip"], str) and 3 <= len(value["ip"]) <= 64)
    try:
        ipaddress.ip_address(value["ip"])
    except ValueError:
        _require(False)
    if kind == "submit":
        context = native_context(context)
        _require(value["tab_id"] == context["tab_id"] and type(value["tab_id"]) is int
                 and value["frame_id"] == 0 and type(value["frame_id"]) is int
                 and isinstance(value["document_id"], str) and 1 <= len(value["document_id"]) <= 160
                 and value["initiator"] == s.ORIGIN
                 and value["application_sha256"] == application_sha256 and _hex(application_sha256))
    else:
        _require(type(value["tab_id"]) is int and value["tab_id"] == -1
                 and type(value["frame_id"]) is int and value["frame_id"] == -1
                 and value["document_id"] is None
                 and value["initiator"] == "chrome-extension://" + s.EXTENSION_ID)
    if kind == "presign":
        _require(value["request_body_sha256"] is None and type(value["request_body_size"]) is int
                 and value["request_body_size"] == 0 and value["request_body_source"] == "none"
                 and value["native_body_crosscheck"] == "none")
    else:
        _require(_hex(value["request_body_sha256"])
                 and _integer(value["request_body_size"], 1, 65536 if kind == "submit" else 10 * 1024 * 1024 + 65536))
        _require(value["request_body_source"] == ("native_raw" if kind == "submit" else "extension_owned_arraybuffer")
                 and value["native_body_crosscheck"] in ({"raw"} if kind == "submit" else {"raw", "form_fields"}))
    return copy.deepcopy(value)


def upload_plan(value, *, now_ms) -> dict:
    _require(enabled())
    _require(isinstance(value, dict) and set(value) == {
        "transport", "presign_url", "storage_origin", "storage_key", "policy_sha256", "fields_sha256",
        "presign_response_sha256", "expires_at_ms", "presign_evidence",
        "resume_component_sha256", "body_provenance",
    })
    _require(value["transport"] == TRANSPORT and value["presign_url"] == PRESIGN_URL)
    _require(value["body_provenance"] == "extension_owned_arraybuffer")
    storage_origin(value["storage_origin"])
    key = value["storage_key"]
    _require(isinstance(key, str) and 1 <= len(key) <= 1024 and not key.startswith("/")
             and not any(part in {"", ".", ".."} for part in key.split("/"))
             and all(ord(char) >= 32 and char not in "\\?#%" for char in key)
             and "${" not in key)
    _require(all(_hex(value[name]) for name in ("policy_sha256", "fields_sha256", "presign_response_sha256", "resume_component_sha256")))
    _require(_integer(value["expires_at_ms"], now_ms + 1, now_ms + 300_000))
    evidence = value["presign_evidence"]
    _require(isinstance(evidence, dict) and _integer(evidence.get("started_at_ms"), now_ms - 30_000, now_ms))
    network(evidence, url=PRESIGN_URL, kind="presign", start=evidence["started_at_ms"],
            deadline=min(now_ms, evidence["started_at_ms"] + 10_000))
    return copy.deepcopy(value)


def uploaded_url(plan) -> str:
    return plan["storage_origin"] + "/" + quote(plan["storage_key"], safe="/-._~")


def upload_evidence(value, *, review, action, artifact, lease) -> dict:
    from . import service as s
    _require(isinstance(value, dict) and set(value) == {
        "version", "review_id", "action_id", "review_digest", "artifact_sha256", "artifact_size_bytes",
        "filename", "target_url", "document_id", "tab_id", "uploaded_url", "multipart_sha256", "multipart_size", "native",
    })
    plan = review.payload["upload_plan"]
    context = native_context(review.payload["native_context"])
    _require(value["version"] == UPLOAD_VERSION and value["review_id"] == review.id and value["action_id"] == action.id
             and value["review_digest"] == review.review_digest and value["artifact_sha256"] == artifact.sha256
             and type(value["artifact_size_bytes"]) is int and value["artifact_size_bytes"] == artifact.size_bytes
             and value["filename"] == artifact.filename and value["target_url"] == review.payload["target_url"]
             and value["document_id"] == review.payload["document_id"]
             and type(value["tab_id"]) is int and value["tab_id"] == context["tab_id"]
             and value["uploaded_url"] == uploaded_url(plan) and _hex(value["multipart_sha256"])
             and _integer(value["multipart_size"], artifact.size_bytes + 1, artifact.size_bytes + 65536))
    proof = network(value["native"], url=plan["storage_origin"] + "/", kind="upload",
                    start=lease["issued_at_ms"], deadline=lease["deadline"])
    _require(proof["request_body_sha256"] == value["multipart_sha256"]
             and proof["request_body_size"] == value["multipart_size"]
             and proof["completed_at_ms"] <= plan["expires_at_ms"]
             and proof["completed_at_ms"] <= s._ms(s.now()))
    return copy.deepcopy(value)


def expected_application(app, mapping, answers, context, upload) -> dict:
    """Mirror the known official hosted serializer; unknown fields fail closed."""
    context = native_context(context)
    _require(not app.form.get("consents") and not getattr(app, "consents", {}))
    by_id = {field["id"]: field for field in mapping if field["type"] != "button"}
    _require(set(answers) == set(by_id))
    result: dict = {"first_name": answers["first_name"], "last_name": answers["last_name"], "email": answers["email"],
              "answers_attributes": {}, "demographic_answers": [], "data_compliance": {}, "attachments": {},
              "from_job_board_renderer": True, "employments": [], "mapped_url_token": None,
              "appcast_click_id": None, "time_zone": context["time_zone"],
              "resume_url": upload["uploaded_url"], "resume_url_filename": upload["filename"]}
    if answers.get("phone"):
        result["phone"] = answers["phone"]
    known = {"first_name", "last_name", "email", "phone", "country"}
    priority = 0
    for field in app.form["fields"]:
        identity = field.get("id")
        if isinstance(identity, str) and identity.startswith("question_"):
            _require(identity in by_id and re.fullmatch(r"question_[0-9]{1,20}", identity) is not None)
            known.add(identity)
            question = identity.split("_")[1]
            value = answers[identity]
            entry: dict = {"question_id": question, "priority": priority}
            priority += 1
            if by_id[identity]["type"] in {"text", "email"}:
                entry["text_value"] = value
            else:
                _require(by_id[identity]["type"] == "combobox")
                if value in {"0", "1"}:
                    entry["boolean_value"] = int(value)
                else:
                    entry["answer_selected_options_attributes"] = {"0": {"question_option_id": value}} if value else {}
            result["answers_attributes"][question] = entry
        else:
            _require(identity in {"first_name", "last_name", "email", "phone", "resume", "employment"})
    indexes = sorted({int(match[1]) for identity in answers
                      if (match := re.fullmatch(r"company-name-([0-4])", identity))})
    _require(indexes == list(range(len(indexes))))
    for index in indexes:
        names = {f"{name}-{index}" for name in ("company-name", "title", "start-date-month", "start-date-year")}
        role_id = f"current-role-{index}_1"
        names.add(role_id)
        _require(names <= set(answers) and answers[role_id] in {"true", "false"})
        current = answers[role_id] == "true"
        if not current:
            names |= {f"end-date-month-{index}", f"end-date-year-{index}"}
            _require(names <= set(answers))
        known |= names
        row: dict = {"company_name": answers[f"company-name-{index}"], "title": answers[f"title-{index}"],
               "start_date": {"month": answers[f"start-date-month-{index}"] or None,
                              "year": answers[f"start-date-year-{index}"] or None}, "current": current}
        if not current:
            row["end_date"] = {"month": answers[f"end-date-month-{index}"] or None,
                               "year": answers[f"end-date-year-{index}"] or None}
        result["employments"].append(row)
    _require(set(answers) <= known)
    return result


def submit_receipt(value, *, review, action) -> dict:
    from . import service as s
    _require(isinstance(value, dict) and set(value) == {
        "version", "review_id", "action_id", "review_digest", "application_sha256", "native",
    })
    binding = action.response["binding"]
    saved = action.response.get("hosted_receipt_binding")
    _require(isinstance(saved, dict) and saved["receipt_contract"] == RECEIPT_VERSION)
    _require(value["version"] == RECEIPT_VERSION and value["review_id"] == review.id
             and value["action_id"] == action.id and value["review_digest"] == binding["review_digest"]
             and value["application_sha256"] == saved["expected_application_sha256"])
    proof = network(value["native"], url=binding["target_url"].replace(s.ORIGIN, "https://boards.greenhouse.io", 1),
                    kind="submit", start=binding["issued_at_ms"], deadline=binding["deadline_ms"],
                    context=saved["native_context"], application_sha256=saved["expected_application_sha256"])
    _require(proof["document_id"] == binding["document_id"] and proof["completed_at_ms"] <= s._ms(s.now()))
    return {"provider": "greenhouse", "contract": RECEIPT_VERSION, "completion_verified": True,
            "received_at": s.now().isoformat(), "application_sha256": value["application_sha256"],
            "network": proof, "evidence_sha256": s.digest(value), "upload_evidence_sha256": saved["upload_evidence_sha256"]}
