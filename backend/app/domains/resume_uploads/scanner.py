"""Consume only the root-owned validated receipt API; never synthesize a verdict."""
from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass, field
from typing import Any

from ...services import document_ingestion


class ScanUnavailable(RuntimeError):
    pass


class ScanRefused(RuntimeError):
    pass


@dataclass(frozen=True)
class VerifiedInspection:
    parsed: tuple = field(repr=False)
    sha256: str
    size_bytes: int
    source_format: str
    worker_image: str
    policy_sha256: str
    scan_engine: str
    scan_version: str
    scan_definitions: str

    def receipt(self) -> dict[str, Any]:
        return {name: getattr(self, name) for name in ("sha256", "size_bytes", "source_format", "worker_image", "policy_sha256", "scan_engine", "scan_version", "scan_definitions")}


def validate_receipt(result: Any, content: bytes, kind: str) -> VerifiedInspection:
    config = document_ingestion.worker_config()
    if (result.sha256 != hashlib.sha256(content).hexdigest()
            or type(result.size_bytes) is not int or result.size_bytes != len(content)
            or result.source_format != kind or result.worker_image != config.image
            or result.policy_sha256 != config.policy or result.scan_engine != "ClamAV"):
        raise ScanUnavailable("scan_binding_unavailable")
    for value in (result.scan_version, result.scan_definitions):
        if not isinstance(value, str) or not 0 < len(value) <= 200 or any(ord(c) < 32 or ord(c) > 126 for c in value):
            raise ScanUnavailable("scan_binding_unavailable")
    parts = result.parsed
    if not isinstance(parts, tuple) or len(parts) != 5:
        raise ScanUnavailable("scan_binding_unavailable")
    raw, sections, skills, years, contact = parts
    def text(value, maximum):
        return isinstance(value, str) and "\x00" not in value and len(value.encode("utf-8")) <= maximum
    if (not text(raw, 262144) or not isinstance(sections, dict) or len(sections) > 30
            or any(not text(k, 80) or not text(v, 262144) for k, v in sections.items())
            or sum(len(v.encode("utf-8")) for v in sections.values()) > 262144
            or not isinstance(skills, list) or len(skills) > 500 or any(not text(v, 200) for v in skills)
            or type(years) not in {int, float} or not math.isfinite(years) or not 0 <= years <= 40
            or not isinstance(contact, dict) or set(contact) - {"name", "email", "phone", "linkedin", "github"}
            or any(v is not None and not text(v, 1000) for v in contact.values())):
        raise ScanUnavailable("scan_binding_unavailable")
    return VerifiedInspection(parts, result.sha256, result.size_bytes, result.source_format, result.worker_image,
                              result.policy_sha256, result.scan_engine, result.scan_version, result.scan_definitions)


def inspect(content: bytes, *, source_format: str) -> VerifiedInspection:
    try:
        # The six-path V3 client is independently owned. Root supplies this exact
        # approved interface after its freeze; missing interface refuses, never falls back.
        function = document_ingestion.inspect_resume_document_with_receipt
        return validate_receipt(function(content, source_format=source_format), content, source_format)
    except document_ingestion.DocumentInspectionError as exc:
        if exc.status_code == 422:
            raise ScanRefused("document_refused") from None
        raise ScanUnavailable("scan_unavailable") from None
    except ScanUnavailable:
        raise
    except Exception:
        raise ScanUnavailable("scan_unavailable") from None
