"""Pinned v2 evaluation inputs; no network, database or qualification authority.

Native imports are explicitly trusted operator-reviewed captures, not supplier
signatures. Their hashes and provenance must be separately reviewed into the
execution authorization. Draft materialization/render observations alone fail.
"""
from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator

from ...services import llm_client, model_cost_policy
from . import development_runner as legacy
from . import quality_packet as packet
from . import supported_evaluation_native as native_import

VERSION = "supported-quality-evaluation-v2"
MANIFEST_SHA = "9fb7672f0a809d5d0d18dabb515a2afd32054f27411691ac5a85e34cd8d11bc6"
CORPUS_SHA = "65e8207002a07daa9c7d643b310c1a1dae5177413f3f639809cd76b1b212903e"
PROTOCOL_SHA = "66eef835ade3dc9417ca58f8ccd7f3cea883090f26f23022dc5a1ad3596177b9"
OPERATIONS = ("interview_questions", "resume_tailor")
SIDES = legacy.SIDES


class DatabasePin(legacy.Closed):
    host: Literal["127.0.0.1"]
    port: int = Field(strict=True, ge=1024, le=65535)
    database: Literal["hirewiz_quality_evaluation_v2"]
    username: str = Field(pattern=r"^[a-z_][a-z0-9_]{0,62}$")
    system_identifier: str = Field(pattern=r"^[0-9]{1,20}$")
    database_oid: int = Field(strict=True, gt=0)


class Route(legacy.Closed):
    prompt_version: str = Field(min_length=1, max_length=120)
    contract_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    price: model_cost_policy.ModelPriceQuote

    @model_validator(mode="after")
    def finite(self):
        if (self.price.provider != "google" or self.price.model not in llm_client.GEMINI_FALLBACK_MODELS
                or self.price.max_output_tokens > 8192):
            raise ValueError("Use the existing bounded Google model route")
        return self


class Authorization(legacy.Authorization):
    schema_version: Literal["supported-quality-evaluation-v2"]
    purpose: Literal["internal_quality_evaluation_not_retail_qualification"]
    operation: Literal["interview_questions", "resume_tailor"]
    split: Literal["development", "held_out"]
    routes: dict[Literal["reference", "candidate"], Route]
    database: DatabasePin
    native_capture_registry: legacy.Artifact
    native_reserve_minor_per_run: int = Field(strict=True, gt=0)
    maximum_native_reserved_minor: int = Field(strict=True, gt=0)
    native_currency: Literal["INR"]
    native_runtime_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    held_out_lock: legacy.Artifact | None = None
    worker_image: str = Field(pattern=r"^us-central1-docker\.pkg\.dev/ai-resume-parser-482412/cloud-run-source-deploy/hirewiz-document-inspector@sha256:[a-f0-9]{64}$")
    worker_policy_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    compiler_image: str = Field(pattern=r"^us-central1-docker\.pkg\.dev/ai-resume-parser-482412/cloud-run-source-deploy/hirewiz-native-tex@sha256:[a-f0-9]{64}$")
    compiler_policy_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")

    @model_validator(mode="after")
    def separated(self):
        if (self.split == "held_out") != (self.held_out_lock is not None):
            raise ValueError("Held-out work requires its distinct locked-development review")
        return self


def digest(value) -> str:
    return packet.digest(packet.canonical(value))


def selected_cases(root: Path, split: str) -> tuple[dict, list[dict]]:
    """Hash every line, but deserialize only the explicitly authorized split.

    Development planning never loads held-out résumé/job text into case objects
    or exports it. Case order and each digest are pinned by the frozen manifest.
    """
    if split not in {"development", "held_out"}:
        raise ValueError("An explicit frozen split is required")
    if (packet.file_digest(root / "manifest.json") != MANIFEST_SHA
            or packet.file_digest(root / "cases.jsonl") != CORPUS_SHA
            or packet.file_digest(root / "protocol.json") != PROTOCOL_SHA):
        raise ValueError("Only the reviewed full v2 packet can be evaluated")
    manifest = json.loads((root / "manifest.json").read_bytes())
    if packet.file_digest(root / "rubric.md") != manifest["rubric_sha256"]:
        raise ValueError("The frozen rubric changed")
    records = manifest["cases"]
    if (manifest.get("schema_version") != packet.SUPPORTED_VERSION or len(records) != 200
            or sum(r["split"] == "development" for r in records) != 40
            or sum(r["split"] == "held_out" for r in records) != 160):
        raise ValueError("The complete 200-case split is required")
    cases = []
    with (root / "cases.jsonl").open("rb") as file:
        lines = file.readlines()
    if len(lines) != 200:
        raise ValueError("The complete frozen case stream is required")
    for raw, record in zip(lines, records, strict=True):
        if packet.digest(raw) != record["sha256"]:
            raise ValueError("Frozen case ordering or bytes changed")
        if record["split"] == split:
            case = json.loads(raw)
            if case["case_id"] != record["case_id"] or case["split"] != split:
                raise ValueError("The case does not match its frozen split")
            cases.append(case)
    return manifest, cases


def review_lock(authorization: Authorization, root: Path) -> None:
    if authorization.split != "held_out":
        return
    assert authorization.held_out_lock is not None
    path = legacy._artifact(root, authorization.held_out_lock)
    value = json.loads(path.read_bytes())
    export_path = legacy._artifact(root, legacy.Artifact.model_validate(value.get("development_export")))
    export = json.loads(export_path.read_bytes())
    legacy._artifact(root, legacy.Artifact.model_validate(value.get("review_evidence")))
    # Manifest metadata alone is sufficient to prove all development pair IDs;
    # this does not deserialize or inspect any held-out input/output.
    manifest_path = Path(__file__).resolve().parents[3] / "evals/unified_credit_v2/manifest.json"
    if packet.file_digest(manifest_path) != MANIFEST_SHA:
        raise ValueError("The frozen development pair registry changed")
    manifest = json.loads(manifest_path.read_bytes())
    expected = {(r["case_id"], side) for r in manifest["cases"] if r["split"] == "development" for side in SIDES}
    records = export.get("records", [])
    if (value.get("state") != "locked_before_held_out"
            or value.get("manifest_sha256") != MANIFEST_SHA
            or value.get("protocol_sha256") != PROTOCOL_SHA
            or value.get("rubric_sha256") != manifest["rubric_sha256"]
            or value.get("operation") != authorization.operation
            or value.get("routes") != {k: v.model_dump(mode="json") for k, v in authorization.routes.items()}
            or not value.get("reviewed_by")
            or value.get("development_native_and_human_review") != "approved_for_held_out_execution"
            or export.get("schema_version") != VERSION or export.get("operation") != authorization.operation
            or export.get("split") != "development" or export.get("manifest_sha256") != MANIFEST_SHA
            or export.get("protocol_sha256") != PROTOCOL_SHA
            or export.get("rubric_sha256") != manifest["rubric_sha256"]
            or export.get("routes") != value.get("routes")
            or len(records) != 80 or {(r.get("case_id"), r.get("side")) for r in records} != expected
            or any(r.get("available") is not True for r in records)):
        raise ValueError("The exact development/reference/rubric lock is required")
    for record in records:
        legacy._artifact(export_path.parent, legacy.Artifact.model_validate(record.get("output")))
        if authorization.operation == "resume_tailor":
            legacy._artifact(export_path.parent, legacy.Artifact.model_validate(record.get("native_artifact")))
    stamp = datetime.fromisoformat(value["locked_at"])
    if stamp.tzinfo is None or not stamp <= authorization.valid_from:
        raise ValueError("The held-out lock must precede this execution allocation")


def reading_order(case: dict, raw: str) -> bool:
    """Require actual complete paragraphs in source order; never sort an oracle."""
    normalized = " ".join(raw.split())
    cursor = 0
    for section in case["resume"]["sections"]:
        for line in (section["heading"], *section["text"].splitlines()):
            expected = " ".join(line.split())
            if not expected:
                continue
            found = normalized.find(expected, cursor)
            if found < 0:
                return False
            cursor = found + len(expected)
    return True


def native_inputs(packet_root: Path, native_root: Path, authorization: Authorization,
                  authorization_root: Path, native_receipts: Path | None) -> tuple[list[dict], list[str]]:
    _, cases = selected_cases(packet_root, authorization.split)
    if packet.file_digest(native_root / "materialization-manifest.json") != authorization.native_manifest_sha256:
        raise ValueError("The materialized source manifest changed")
    materialized = json.loads((native_root / "materialization-manifest.json").read_bytes())
    if materialized.get("packet_manifest_sha256") != MANIFEST_SHA or materialized.get("corpus_sha256") != CORPUS_SHA:
        raise ValueError("Native files do not belong to the frozen v2 packet")
    indexed = {r["case_id"]: r for r in materialized["cases"]}
    receipts = {}
    registry = {}
    registry_root = authorization_root
    if native_receipts is not None:
        if packet.file_digest(native_receipts) != authorization.native_receipts_sha256:
            raise ValueError("The native input evidence changed")
        receipts = json.loads(native_receipts.read_bytes())
        registry_path = legacy._artifact(authorization_root, authorization.native_capture_registry)
        registry_root = registry_path.parent
        registry = json.loads(registry_path.read_bytes())
    rows, issues = [], []
    for case in cases:
        identity = case["case_id"]
        row = indexed.get(identity, {})
        if row.get("input_sha256") != digest(case):
            raise ValueError("The native source case changed")
        source = legacy._artifact(native_root, legacy.Artifact.model_validate(row["input_artifact"]))
        source_bytes = source.read_bytes()
        if packet.digest(source_bytes) != row["input_artifact"]["sha256"]:
            raise ValueError("The native source changed during import")
        if not packet._native(source, case["source_format"]):
            raise ValueError("The native source format changed")
        errors: list[str] = []
        record = receipts.get(identity, {})
        capture = (registry.get("captures") or {}).get(identity, {})
        parsed = None
        try:
            if native_receipts is None:
                raise ValueError("Native captures are absent")
            if (registry.get("custody") != "operator_reviewed_native_capture_v1"
                    or not registry.get("reviewed_by") or not registry.get("reviewed_at")
                    or registry.get("manifest_sha256") != MANIFEST_SHA
                    or capture.get("source_sha256") != row["input_artifact"]["sha256"]
                    or not capture.get("request_correlation_id")):
                raise ValueError
            packet._native_receipt(native_receipts.parent, record,
                source_sha=row["input_artifact"]["sha256"], target_sha=row["input_artifact"]["sha256"],
                errors=errors, label=identity)
            imported = native_import.decode(registry_root, capture, source_bytes, case["source_format"],
                worker_image=authorization.worker_image, worker_policy=authorization.worker_policy_sha256,
                compiler_image=authorization.compiler_image, compiler_policy=authorization.compiler_policy_sha256)
            inspected = imported.extraction
            validation_path = legacy._artifact(native_receipts.parent, legacy.Artifact.model_validate(record))
            validation = json.loads(validation_path.read_bytes())
            if case["source_format"] in {"tex", "texzip"} and validation.get("inspected_artifact_sha256") != inspected.sha256:
                errors.append("Native review is not bound to the actual inspected compiled PDF: " + identity)
            extracted = legacy._artifact(native_receipts.parent, legacy.Artifact.model_validate(validation["extracted_text"]))
            if extracted.read_text() != inspected.parsed[0]:
                errors.append("Reviewed text differs from the actual native response: " + identity)
            if not reading_order(case, inspected.parsed[0]):
                errors.append("Production extraction has incomplete or interleaved reading order: " + identity)
            if inspected.scan_engine != "ClamAV" or not re.fullmatch(r"[a-f0-9]{64}", capture["worker_response"]["sha256"]):
                raise ValueError
            parsed = inspected.parsed
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            errors.append("Reviewed genuine native capture is unavailable: " + identity)
        issues.extend(errors)
        rows.append({"case_id": identity, "source_format": case["source_format"], "source_sha256": packet.digest(source_bytes),
                     "source_path": source, "source_bytes": source_bytes, "case": case, "parsed": parsed, "native_input_ready": not errors})
    return rows, issues


def plan(packet_root: Path, authorization: Authorization) -> dict:
    manifest, cases = selected_cases(packet_root, authorization.split)
    return {"schema_version": VERSION, "operation": authorization.operation, "case_count": 200,
            "split_counts": {"development": 40, "held_out": 160}, "formats": {k: 50 for k in packet.FORMATS},
            "selected_split": authorization.split, "selected_cases": len(cases), "selected_slots": len(cases) * 2,
            "complete_plan_slots": 400, "manifest_sha256": MANIFEST_SHA, "protocol_sha256": PROTOCOL_SHA,
            "rubric_sha256": manifest["rubric_sha256"],
            "slots": [{"case_id": r["case_id"], "split": r["split"], "input_sha256": r["sha256"], "side": side}
                      for r in manifest["cases"] for side in SIDES],
            "authorizes_retail": False, "provider_calls": 0}
