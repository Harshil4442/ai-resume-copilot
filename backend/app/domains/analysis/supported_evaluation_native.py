"""Pure validation of operator-reviewed captures of unchanged native protocols.

TeX source admission is intentionally blank. Only its separately inspected,
source-correlated compiled PDF may supply parsed candidate facts. No network,
compiler execution, expected fixture text or attestation fabrication occurs here.
"""
from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from pathlib import Path

from ...services import document_ingestion, native_project, native_tex
from . import development_runner as legacy
from . import quality_packet as packet


@dataclass(frozen=True)
class ImportedInspection:
    source: document_ingestion.InspectedResume
    extraction: document_ingestion.InspectedResume


def compile_request(source: bytes, kind: str) -> bytes:
    """Exact existing native_tex._compile_project request, without executing it."""
    project = native_project.read_project(source, kind)
    return json.dumps({"protocol": "hirewiz-tex-v1", "main": project.main,
        "files": [{"path": name, "data": base64.b64encode(value).decode()}
                  for name, value in sorted(project.files.items())]}).encode()


def decode(root: Path, capture: dict, source: bytes, kind: str, *, worker_image: str,
           worker_policy: str, compiler_image: str, compiler_policy: str) -> ImportedInspection:
    config = document_ingestion.WorkerConfig("", "", worker_image, worker_policy)
    original = legacy._artifact(root, legacy.Artifact.model_validate(capture["worker_response"]))
    if len(source) > document_ingestion.MAX_SOURCE_BYTES or original.stat().st_size > document_ingestion.MAX_RESPONSE_BYTES:
        raise ValueError("The original native import exceeds its existing bounds")
    inspected = document_ingestion._decode_receipt(original.read_bytes(), source, kind, config)
    if kind not in native_project.NATIVE_FORMATS:
        return ImportedInspection(inspected, inspected)
    parsed = inspected.parsed
    # Same source-admission constraint as native_tex._inspected; source text is
    # never interpreted as extraction or replaced with expected fixture text.
    if (parsed[0] != "" or parsed[1] != {} or parsed[2] != [] or parsed[3] != 0
            or any(value is not None for value in parsed[4].values())):
        raise ValueError("TeX source inspection must remain blank admission")
    compilation = capture["compilation"]
    if compilation.get("source_sha256") != packet.digest(source) or not compilation.get("request_correlation_id"):
        raise ValueError("The compilation does not belong to this exact original source")
    raw = legacy._artifact(root, legacy.Artifact.model_validate(compilation["worker_response"]))
    if raw.stat().st_size > 15 * 1024 * 1024:
        raise ValueError("The captured compiler response exceeds its existing bound")
    response = json.loads(raw.read_bytes())
    # Exact existing remote_compile response contract. This is a capture import,
    # not a new worker protocol or a provider signature.
    if (type(response) is not dict or set(response) != {"protocol", "pdf", "sha256", "size", "renderer",
            "request_sha256", "renderer_image", "isolation_policy_sha256"}
            or response.get("protocol") != "hirewiz-tex-v1"
            or response.get("renderer") != "pdftex-no-shell-escape-v1"
            or response.get("request_sha256") != packet.digest(compile_request(source, kind))
            or response.get("renderer_image") != compiler_image
            or response.get("isolation_policy_sha256") != compiler_policy):
        raise ValueError("The captured compilation provenance differs from its pinned source/runtime")
    pdf = native_tex._compiler_pdf(response)
    artifact = legacy._artifact(root, legacy.Artifact.model_validate(compilation["pdf"]))
    if artifact.read_bytes() != pdf or len(pdf) > document_ingestion.MAX_SOURCE_BYTES:
        raise ValueError("The actual compiled PDF differs from the compiler result")
    pdf_response = legacy._artifact(root, legacy.Artifact.model_validate(compilation["pdf_inspection_response"]))
    if pdf_response.stat().st_size > document_ingestion.MAX_RESPONSE_BYTES:
        raise ValueError("The compiled PDF inspection exceeds its existing bound")
    extraction = document_ingestion._decode_receipt(pdf_response.read_bytes(), pdf, "pdf", config)
    return ImportedInspection(inspected, extraction)
