"""Closed held-out input planning after complete native-development review.

This module reads retained, operator-reviewed evidence. Hashes and protocol
validation are not provider attestation or permission to send a request. The
separate durable channel, native Job, source qualification and funded dispatch
are deliberately not implemented or issued here.
"""
from __future__ import annotations

import ast
import fcntl
import hashlib
import json
import os
import re
import stat
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.domains.analysis import native_input_acquisition as development
from app.domains.analysis import quality_packet as packet
from app.domains.analysis import supported_evaluation_native as native
from app.domains.analysis import supported_evaluation_plan as inputs
from app.services import document_ingestion as inspection
from app.services import native_tex
from app.services.resume_layout import ResumeLayoutError, ResumeProcessingUnavailable
from evaluation.native_acquisition_v2 import bridge

VERSION = "held-out-native-service-acquisition-v2"
LOCK_VERSION = "native-development-review-lock-v2"
TRANSPORT_LOCK_VERSION = "native-development-review-lock-v3"
TRANSPORT_ENTRY = "backend/evaluation/development40_transport_v2.py"
TRANSPORT_CHANNEL_SHA256 = "89b65c3c3fb874368f28702f0048c19a3bbeccc7ab910dd12bc35a593225864e"
MAX_METADATA = 256 * 1024
MAX_BUNDLE_BYTES = 2 * 1024**2
MAX_INPUT_BYTES = 2 * 1024**3
MAX_JOURNAL_BYTES = 2 * 1024**3
MAX_AUTHORITY_FILES = 2048
MAX_PHASE_METADATA = 16 * 1024
MAX_ARTIFACT = 15 * 1024**2
PATH = r"[A-Za-z0-9_-][A-Za-z0-9._-]*(?:/[A-Za-z0-9_-][A-Za-z0-9._-]*){0,7}"
KINDS: dict[str, tuple[int, int]] = {
    "source": (40, inspection.MAX_SOURCE_BYTES),
    "source_raw": (40, inspection.MAX_RESPONSE_BYTES),
    "compile_raw": (20, MAX_ARTIFACT),
    "compiled_pdf": (20, inspection.MAX_SOURCE_BYTES),
    "pdf_raw": (20, inspection.MAX_RESPONSE_BYTES),
    "intent": (80, MAX_PHASE_METADATA),
    "retained": (80, MAX_PHASE_METADATA),
    "result": (80, MAX_PHASE_METADATA),
    "terminal": (80, MAX_METADATA),
    "validation": (40, MAX_METADATA),
    "text": (40, inspection.MAX_TEXT_BYTES),
    "page": (1024, MAX_ARTIFACT),
    "batch": (36, MAX_METADATA),
    "provider": (36, MAX_METADATA),
    "dispatch_review": (9, MAX_METADATA),
    "control": (32, MAX_METADATA),
}
TRANSPORT_ORIGIN_KINDS = {"transport_job": (9, MAX_METADATA), "transport_origin": (1, MAX_METADATA)}
AUTHORITY_KINDS = {**KINDS, **TRANSPORT_ORIGIN_KINDS}


class AcquisitionDenied(ValueError):
    """A safe public denial code, never retained source or provider diagnostics."""


def need(value: object, code: str) -> None:
    if not value:
        raise AcquisitionDenied("held_out_native_" + code)


class Closed(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, revalidate_instances="always")


class Artifact(Closed):
    path: str = Field(pattern="^" + PATH + "$", max_length=512)
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


class EvidenceFile(Artifact):
    kind: Literal["source", "source_raw", "compile_raw", "compiled_pdf", "pdf_raw",
                  "intent", "retained", "result", "terminal", "validation", "text",
                  "page", "batch", "provider", "dispatch_review", "control"]


class TransportEvidenceFile(Artifact):
    kind: Literal["source", "source_raw", "compile_raw", "compiled_pdf", "pdf_raw",
                  "intent", "retained", "result", "terminal", "validation", "text",
                  "page", "batch", "provider", "dispatch_review", "control",
                  "transport_job", "transport_origin"]


class RetainedObjectReference(Closed):
    """Original V1 object identity, retained without inventing a new generation."""
    name: str = Field(pattern=r"^[A-Za-z0-9._-]{1,160}$")
    generation: str = Field(pattern=r"^[1-9][0-9]{0,20}$")
    size: int = Field(strict=True, ge=1, le=MAX_ARTIFACT)
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


class RetainedObject(Closed):
    artifact: Artifact
    reference: RetainedObjectReference


class OriginalDevelopmentBatch(Closed):
    batch_index: int = Field(strict=True, ge=0, le=8)
    execution: Artifact
    bundle: RetainedObject
    dispatch_ack: RetainedObject
    run_intent: RetainedObject
    run_terminal: RetainedObject
    job_context: RetainedObject
    phase_terminals: dict[str, RetainedObject] = Field(min_length=1, max_length=10)


class Authorization(Closed):
    schema_version: Literal["held-out-native-service-acquisition-v2"]
    purpose: Literal["unreviewed_held_out_native_capture_no_model_no_retail"]
    state: Literal["draft", "authorized"]
    authorized_by: str = Field(min_length=1, max_length=160)
    experiment_id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{7,79}$")
    journal_path: str = Field(min_length=1, max_length=2048)
    split: Literal["held_out"]
    valid_from: datetime
    expires_at: datetime
    packet_manifest_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    native_manifest_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    native_development_lock: Artifact
    funding_evidence: Artifact
    request_limit: int = Field(strict=True, ge=320, le=320)
    reserved_minor_per_request: int = Field(strict=True, gt=0)
    maximum_native_reserved_minor: int = Field(strict=True, gt=0)
    currency: Literal["INR"]
    inspector: development.Binding
    compiler: development.Binding

    @model_validator(mode="after")
    def finite(self) -> Authorization:
        need(self.packet_manifest_sha256 == inputs.MANIFEST_SHA, "packet_changed")
        path = Path(self.journal_path)
        need(path.is_absolute() and str(path.resolve()) == self.journal_path, "journal_path")
        need(all(t.tzinfo is not None for t in (self.valid_from, self.expires_at))
             and self.valid_from < self.expires_at <= self.valid_from + timedelta(days=1), "time_window")
        from urllib.parse import urlsplit
        for binding, route in ((self.inspector, "/inspect"), (self.compiler, "/compile")):
            target = urlsplit(binding.url)
            need(target.scheme == "https" and re.fullmatch(r"[a-z0-9-]+(?:\.[a-z0-9-]+)*\.run\.app", target.hostname or "")
                 and target.username is None and target.password is None and target.port is None
                 and target.path == route and not target.query and not target.fragment, "private_service_binding")
        need(self.maximum_native_reserved_minor >= 320 * self.reserved_minor_per_request,
             "whole_split_reserve_required")
        return self


class NativeDevelopmentLock(Closed):
    schema_version: Literal["native-development-review-lock-v2"]
    purpose: Literal["reviewed_native_development_inputs_before_held_out_capture"]
    state: Literal["locked_before_held_out_native_capture"]
    manifest_sha256: str
    corpus_sha256: str
    protocol_sha256: str
    rubric_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    reviewed_by: str = Field(min_length=1, max_length=160)
    reviewed_at: datetime
    locked_at: datetime
    native_directory: str = Field(pattern="^" + PATH + "$", max_length=256)
    journal_directory: str = Field(pattern="^" + PATH + "$", max_length=256)
    review_directory: str = Field(pattern="^" + PATH + "$", max_length=256)
    authorization_directory: str = Field(pattern="^" + PATH + "$", max_length=256)
    channel_directory: str = Field(pattern="^" + PATH + "$", max_length=256)
    development_authorization: Artifact
    development_plan: Artifact
    capture_registry: Artifact
    validation_map: Artifact
    review_evidence: Artifact
    runner_receipt: Artifact
    ci_metadata: Artifact
    build_metadata: Artifact
    channel_binding: RetainedObject
    files: list[EvidenceFile] = Field(min_length=1, max_length=MAX_AUTHORITY_FILES)

    @model_validator(mode="after")
    def finite(self) -> NativeDevelopmentLock:
        need((self.manifest_sha256, self.corpus_sha256, self.protocol_sha256)
             == (inputs.MANIFEST_SHA, inputs.CORPUS_SHA, inputs.PROTOCOL_SHA), "lock_packet")
        need(all(t.tzinfo is not None for t in (self.reviewed_at, self.locked_at))
             and self.reviewed_at <= self.locked_at, "lock_time")
        paths = [row.path for row in self.files]
        need(len(paths) == len(set(paths)), "duplicate_lock_file")
        counts = Counter(row.kind for row in self.files)
        need(all(count <= KINDS[kind][0] for kind, count in counts.items()), "lock_kind_count")
        directories = (self.native_directory, self.journal_directory,
                       self.review_directory, self.authorization_directory, self.channel_directory)
        need(len(set(directories)) == 5 and not any(
            a.startswith(b + "/") for a in directories for b in directories if a != b), "lock_directories")
        return self


class ReviewedDevelopmentRunner(Closed):
    """Retained V1 receipt data; this class cannot qualify or issue a receipt."""
    schema_version: Literal["native-quality-qualified-runner-v1"]
    repository: Literal["https://github.com/Harshil4442/ai-resume-copilot.git"]
    release: str = Field(pattern=r"^[a-f0-9]{40}$")
    ci_run_id: int = Field(strict=True, gt=0)
    build_id: str = Field(pattern=r"^[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}$")
    image: str = Field(pattern=r"^us-central1-docker\.pkg\.dev/ai-resume-parser-482412/cloud-run-source-deploy/hirewiz@sha256:[a-f0-9]{64}$")
    qualification_contract: Literal["hirewiz_schema26_app_rolling_v3"]
    qualification_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    app_files: int = Field(strict=True, ge=289, le=289)
    app_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    entry_path: Literal["backend/scripts/native_acquisition_job.py"]
    entry_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    runtime_dependencies_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    observed_at: datetime
    ci_metadata_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    build_metadata_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")

    @model_validator(mode="after")
    def aware(self) -> ReviewedDevelopmentRunner:
        need(self.observed_at.tzinfo is not None, "runner_receipt_time")
        return self


class ReviewedDevelopmentTransportRunner(Closed):
    """Exact final producer receipt data; no provider attestation is inferred."""
    schema_version: Literal["development40-transport-qualified-runner-v2"]
    repository: Literal["https://github.com/Harshil4442/ai-resume-copilot.git"]
    release: str = Field(pattern=r"^[a-f0-9]{40}$")
    git_tree: str = Field(pattern=r"^[a-f0-9]{40}$")
    ci_run_id: int = Field(strict=True, gt=0)
    build_id: str = Field(pattern=r"^[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}$")
    image: str = Field(pattern=r"^us-central1-docker\.pkg\.dev/ai-resume-parser-482412/cloud-run-source-deploy/hirewiz@sha256:[a-f0-9]{64}$")
    qualification_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    app_compatibility_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    app_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    app_files: int = Field(strict=True, ge=289, le=289)
    entry_path: Literal["backend/evaluation/development40_transport_v2.py"]
    entry_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    semantic_entry_path: Literal["backend/scripts/native_acquisition_job.py"]
    semantic_entry_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    channel_sha256: Literal["89b65c3c3fb874368f28702f0048c19a3bbeccc7ab910dd12bc35a593225864e"]
    runtime_dependencies_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    component_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    observed_at: datetime
    ci_metadata_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    build_metadata_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")

    @model_validator(mode="after")
    def aware(self) -> ReviewedDevelopmentTransportRunner:
        need(self.observed_at.tzinfo is not None, "runner_receipt_time")
        return self


class TransportSourceObservation(Closed):
    release: str = Field(pattern=r"^[a-f0-9]{40}$")
    git_tree: str = Field(pattern=r"^[a-f0-9]{40}$")
    qualification_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    runtime_dependencies_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    component_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    entry_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    semantic_entry_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    channel_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


class TransportResourcePlan(Closed):
    """Final producer's original Plan as closed data, never an active permit."""
    schema_version: Literal["native-quality-isolation-v1"]
    purpose: Literal["unreviewed_development_capture_no_model_no_retail"]
    evaluator: Literal["hirewiz-quality-dev@ai-resume-parser-482412.iam.gserviceaccount.com"]
    evaluator_uid: str = Field(pattern=r"^[0-9]{19,21}$")
    bucket: Literal["hirewiz-quality-dev-482412-20261031"]
    bucket_metageneration: str = Field(pattern=r"^[1-9][0-9]{0,19}$")
    experiment_id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{7,79}$")
    valid_from: datetime
    expires_at: datetime
    controller_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    job_name: str = Field(pattern=r"^hirewiz-quality-dev-[a-z0-9-]{8,32}$")
    qualified_runner_receipt_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    inspector_service_uid: str = Field(min_length=1, max_length=128)
    inspector_revision_uid: str = Field(min_length=1, max_length=128)
    compiler_service_uid: str = Field(min_length=1, max_length=128)
    compiler_revision_uid: str = Field(min_length=1, max_length=128)
    inspector_image: str = Field(pattern=r"^us-central1-docker\.pkg\.dev/ai-resume-parser-482412/cloud-run-source-deploy/hirewiz-document-inspector@sha256:[a-f0-9]{64}$")
    compiler_image: str = Field(pattern=r"^us-central1-docker\.pkg\.dev/ai-resume-parser-482412/cloud-run-source-deploy/hirewiz-native-tex@sha256:[a-f0-9]{64}$")
    inspector_release: str = Field(pattern=r"^[a-f0-9]{40}$")
    compiler_release: str = Field(pattern=r"^[a-f0-9]{40}$")
    funding_evidence_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    acquisition_authorization_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    bundle_name: str = Field(pattern=r"^input\.[A-Za-z0-9._-]{1,120}\.json$")
    bundle_generation: str = Field(pattern=r"^[1-9][0-9]{0,20}$")
    bundle_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    native_dispatch_review_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")

    @model_validator(mode="after")
    def finite(self) -> TransportResourcePlan:
        need(self.valid_from.tzinfo is not None and self.expires_at.tzinfo is not None
             and self.valid_from < self.expires_at <= self.valid_from + timedelta(days=1), "transport_plan_window")
        return self


class TransportSourceVerification(Closed):
    runner_receipt_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    fresh_source_CI_Build_verified: Literal[True]
    native_approval: Literal[False]
    processing_authorized: Literal[False]
    funding_authorized: Literal[False]


class TransportBatchOrigin(Closed):
    batch_index: int = Field(strict=True, ge=0, le=8)
    job: RetainedObject
    job_uid: str = Field(pattern=r"^[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}$")
    job_generation: str = Field(pattern=r"^[1-9][0-9]{0,18}$")
    job_template_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    execution: Artifact
    bundle: RetainedObjectReference
    resource_plan: TransportResourcePlan
    captured_at: datetime


class TransportOriginRecord(Closed):
    """Retained source/native correlation, not a fresh external verification."""
    schema_version: Literal["development40-transport-native-origin-v1"]
    runner_receipt_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    runner_source: TransportSourceObservation
    controller_release: str = Field(pattern=r"^[a-f0-9]{40}$")
    controller_source_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    controller_ci_run_id: int = Field(strict=True, gt=0)
    controller_ci_metadata_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    controller_ci_metadata: dict[str, Any]
    source_verified_at: datetime
    native_verified_at: datetime
    verification: TransportSourceVerification
    batches: list[TransportBatchOrigin] = Field(min_length=9, max_length=9)

    @model_validator(mode="after")
    def finite(self) -> TransportOriginRecord:
        need(all(t.tzinfo is not None for t in (self.source_verified_at, self.native_verified_at))
             and self.source_verified_at <= self.native_verified_at, "transport_origin_time")
        need([row.batch_index for row in self.batches] == list(range(9))
             and all(row.captured_at.tzinfo is not None for row in self.batches), "transport_origin_batches")
        need(len({row.job.artifact.path for row in self.batches}) == 9
             and len({row.job_uid for row in self.batches}) == 9
             and len({row.resource_plan.job_name for row in self.batches}) == 9,
             "transport_origin_job_alias")
        return self


class TransportOriginFiles(Closed):
    source_verification: RetainedObject
    jobs: list[RetainedObject] = Field(min_length=9, max_length=9)


class NativeDevelopmentTransportLock(Closed):
    """Separate successor; original lock-v2 and V1 receipts are never rewritten."""
    schema_version: Literal["native-development-review-lock-v3"]
    purpose: Literal["reviewed_native_development_inputs_before_held_out_capture"]
    state: Literal["locked_before_held_out_native_capture"]
    manifest_sha256: str
    corpus_sha256: str
    protocol_sha256: str
    rubric_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    reviewed_by: str = Field(min_length=1, max_length=160)
    reviewed_at: datetime
    locked_at: datetime
    native_directory: str = Field(pattern="^" + PATH + "$", max_length=256)
    journal_directory: str = Field(pattern="^" + PATH + "$", max_length=256)
    review_directory: str = Field(pattern="^" + PATH + "$", max_length=256)
    authorization_directory: str = Field(pattern="^" + PATH + "$", max_length=256)
    channel_directory: str = Field(pattern="^" + PATH + "$", max_length=256)
    origin_directory: str = Field(pattern="^" + PATH + "$", max_length=256)
    development_authorization: Artifact
    development_plan: Artifact
    capture_registry: Artifact
    validation_map: Artifact
    review_evidence: Artifact
    runner_receipt: Artifact
    ci_metadata: Artifact
    build_metadata: Artifact
    channel_binding: RetainedObject
    transport_origin: TransportOriginFiles
    definitions_bridge: bridge.PendingNative60 | None = None
    files: list[TransportEvidenceFile] = Field(min_length=1, max_length=MAX_AUTHORITY_FILES)

    @model_validator(mode="after")
    def finite(self) -> NativeDevelopmentTransportLock:
        need((self.manifest_sha256, self.corpus_sha256, self.protocol_sha256)
             == (inputs.MANIFEST_SHA, inputs.CORPUS_SHA, inputs.PROTOCOL_SHA), "lock_packet")
        need(all(t.tzinfo is not None for t in (self.reviewed_at, self.locked_at))
             and self.reviewed_at <= self.locked_at, "lock_time")
        paths = [row.path for row in self.files]
        need(len(paths) == len(set(paths)), "duplicate_lock_file")
        counts = Counter(row.kind for row in self.files)
        need(all(count <= AUTHORITY_KINDS[kind][0] for kind, count in counts.items()), "lock_kind_count")
        directories = (self.native_directory, self.journal_directory, self.review_directory,
                       self.authorization_directory, self.channel_directory, self.origin_directory)
        need(len(set(directories)) == 6 and not any(a.startswith(b + "/")
             for a in directories for b in directories if a != b), "lock_directories")
        origin = self.transport_origin
        refs = [origin.source_verification, *origin.jobs]
        need(len({row.artifact.path for row in refs}) == 10
             and len({row.reference.name for row in refs}) == 10
             and all(row.artifact.path == self.origin_directory + "/" + row.reference.name
                 and row.artifact.sha256 == row.reference.sha256 for row in refs), "transport_origin_inventory")
        expected = {row.artifact.path: (row.artifact.sha256,
            "transport_origin" if index == 0 else "transport_job") for index, row in enumerate(refs)}
        need({row.path: (row.sha256, row.kind) for row in self.files if row.kind in TRANSPORT_ORIGIN_KINDS}
             == expected, "transport_origin_inventory")
        return self


DevelopmentLock = NativeDevelopmentLock | NativeDevelopmentTransportLock


def load_development_lock(raw: bytes) -> DevelopmentLock:
    value = _json(raw)
    if value.get("schema_version") == LOCK_VERSION:
        return NativeDevelopmentLock.model_validate(value)
    need(value.get("schema_version") == TRANSPORT_LOCK_VERSION, "lock_schema")
    lock = NativeDevelopmentTransportLock.model_validate(value)
    if lock.definitions_bridge is not None:
        bridge.require_native60_authority(lock.definitions_bridge)
    return lock


def _json(raw: bytes) -> dict[str, Any]:
    def pairs(rows: list[tuple[str, Any]]) -> dict[str, Any]:
        need(len(rows) == len({key for key, _ in rows}), "duplicate_json_key")
        return dict(rows)
    try:
        value = json.loads(raw, object_pairs_hook=pairs,
                           parse_constant=lambda _: (_ for _ in ()).throw(AcquisitionDenied("held_out_native_json_number")))
        need(type(value) is dict, "json_object")
        pending = [(value, 0)]
        while pending:
            node, depth = pending.pop()
            need(depth <= 16, "json_depth")
            if isinstance(node, dict):
                pending.extend((child, depth + 1) for child in node.values())
            elif isinstance(node, list):
                pending.extend((child, depth + 1) for child in node)
        return value
    except (ValueError, TypeError, RecursionError):
        raise AcquisitionDenied("held_out_native_invalid_json") from None


def _path(root: Path, name: str) -> Path:
    need(root.is_absolute() and root.resolve() == root and re.fullmatch(PATH, name), "artifact_path")
    development._private(root, directory=True)
    path = root / name
    for parent in reversed(path.parents):
        if parent == root or root in parent.parents:
            development._private(parent, directory=True)
    development._private(path)
    return path


def read_artifact(root: Path, artifact: Artifact, maximum: int = MAX_METADATA) -> bytes:
    """Bounded private read with an exact hash, stable inode and no link following."""
    artifact = Artifact.model_validate(artifact.model_dump(include={"path", "sha256"}))
    need(type(maximum) is int and 0 < maximum <= MAX_ARTIFACT, "read_bound")
    path = _path(root, artifact.path)
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(descriptor, "rb") as file:
        before = os.fstat(file.fileno())
        need(0 < before.st_size <= maximum, "artifact_bound")
        raw = file.read(maximum + 1)
        after = os.fstat(file.fileno())
    def identity(value: os.stat_result) -> tuple[int, ...]:
        return (value.st_dev, value.st_ino, value.st_mode, value.st_nlink, value.st_uid,
                value.st_size, value.st_mtime_ns, value.st_ctime_ns)
    need(identity(before) == identity(after) == identity(path.lstat())
         and stat.S_ISREG(after.st_mode) and after.st_nlink == 1
         and len(raw) == before.st_size and packet.digest(raw) == artifact.sha256,
         "artifact_changed")
    return raw


@dataclass(frozen=True)
class VerifiedDevelopment:
    """Retained reviewed inputs match; explicitly not fresh provider attestation."""
    lock_sha256: str
    closure_sha256: str
    development_plan_sha256: str
    case_ids: tuple[str, ...]
    authority_bytes: int
    processing_authorized: Literal[False] = False
    native_approval: Literal[False] = False


class _Closure:
    def __init__(self, root: Path, lock: DevelopmentLock):
        self.root, self.rows = root, {row.path: row for row in lock.files}
        self.limits = AUTHORITY_KINDS if isinstance(lock, NativeDevelopmentTransportLock) else KINDS
        self.used: set[str] = set()
        self.bytes = 0

    def read(self, artifact: Artifact, kind: str) -> bytes:
        row = self.rows.get(artifact.path)
        need(row is not None and row.sha256 == artifact.sha256 and row.kind == kind, "unbound_review_artifact")
        raw = read_artifact(self.root, artifact, self.limits[kind][1])
        if artifact.path not in self.used:
            self.bytes += len(raw)
            self.used.add(artifact.path)
        need(self.bytes <= MAX_INPUT_BYTES, "authority_bytes")
        return raw

    def relative(self, prefix: str, name: str, kind: str) -> Artifact:
        path = prefix + "/" + name
        row = self.rows.get(path)
        need(row is not None, "missing_review_artifact")
        assert row is not None
        artifact = Artifact(path=path, sha256=row.sha256)
        self.read(artifact, kind)
        return artifact


def _active(auth: Authorization) -> Authorization:
    auth = Authorization.model_validate(auth)
    need(auth.state == "authorized" and auth.valid_from <= datetime.now(UTC) < auth.expires_at,
         "active_authorization_required")
    return auth


def _reviewed_runner(closure: _Closure, lock: DevelopmentLock) -> str:
    """Cross-bind retained metadata, never replace the fresh native verifier."""
    # Docker COPY ships backend, not the owner's infra checkout. Read finite
    # existing source constants as data rather than importing a controller and
    # accidentally requiring /app/infra inside the keyless capture Job.
    backend = Path(__file__).resolve().parents[2]

    def literals(name: str, required: set[str]) -> dict[str, Any]:
        path = backend / "scripts" / name
        need(path.is_file() and not path.is_symlink() and 0 < path.stat().st_size <= 512 * 1024, "runner_contract_path")
        values = {}
        for node in ast.parse(path.read_bytes()).body:
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name) and target.id in required:
                        need(target.id not in values, "runner_contract_duplicate")
                        values[target.id] = ast.literal_eval(node.value)
        need(set(values) == required, "runner_contract_constants")
        return values

    source = literals("qualify_native_quality_acquisition.py", {"APP_SHA256", "ENTRY", "ENTRY_SHA256", "HISTORICAL_RELEASE"})
    contract = literals("schema25_app_release_contract_v3.py", {"CANDIDATE_APP_SHA256", "CANDIDATE_APP_FILES",
        "COMPATIBILITY_STEP", "PROTOCOL_STEP", "RETAINED_STEP"})
    raw_runner = _json(closure.read(lock.runner_receipt, "control"))
    transport = isinstance(lock, NativeDevelopmentTransportLock)
    runner: ReviewedDevelopmentRunner | ReviewedDevelopmentTransportRunner
    runner = (ReviewedDevelopmentTransportRunner.model_validate(raw_runner) if transport
              else ReviewedDevelopmentRunner.model_validate(raw_runner))
    need(runner.app_sha256 == source["APP_SHA256"] == contract["CANDIDATE_APP_SHA256"]
         and runner.app_files == contract["CANDIDATE_APP_FILES"]
         and runner.release != source["HISTORICAL_RELEASE"], "original_runner_contract")
    if isinstance(runner, ReviewedDevelopmentTransportRunner):
        entry = backend / "evaluation/development40_transport_v2.py"
        need(runner.entry_path == TRANSPORT_ENTRY and entry.is_file() and not entry.is_symlink()
             and runner.entry_sha256 == packet.file_digest(entry)
             and runner.semantic_entry_path == source["ENTRY"]
             and runner.semantic_entry_sha256 == source["ENTRY_SHA256"]
             and runner.channel_sha256 == TRANSPORT_CHANNEL_SHA256, "transport_runner_contract")
    else:
        need(runner.entry_path == source["ENTRY"] and runner.entry_sha256 == source["ENTRY_SHA256"],
             "original_runner_contract")
    ci = _json(closure.read(lock.ci_metadata, "control"))
    build = _json(closure.read(lock.build_metadata, "control"))
    def metadata_digest(value: dict[str, Any]) -> str:
        return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()

    need(runner.ci_metadata_sha256 == metadata_digest(ci)
         and runner.build_metadata_sha256 == metadata_digest(build), "original_metadata_hashes")
    jobs = ci.get("jobs", [])
    names = {"backend", "frontend", "security-and-container", "browser-companion", "cold-browser-journey"}
    need(ci.get("headSha") == runner.release and ci.get("workflowName") == "CI"
         and ci.get("status") == "completed" and ci.get("conclusion") == "success"
         and type(jobs) is list and len(jobs) == 5 and {j.get("name") for j in jobs} == names
         and all(j.get("status") == "completed" and j.get("conclusion") == "success" for j in jobs),
         "original_ci_not_complete")
    steps = next(j for j in jobs if j["name"] == "backend").get("steps", [])
    for key in ("COMPATIBILITY_STEP", "PROTOCOL_STEP", "RETAINED_STEP"):
        matches = [s for s in steps if s.get("name") == contract[key]]
        need(len(matches) == 1 and matches[0].get("status") == "completed"
             and matches[0].get("conclusion") == "success", "original_qualification_steps")
    repository, digest = runner.image.split("@")
    tag = repository + ":" + runner.release
    images = build.get("results", {}).get("images", [])
    stock_steps = [{"name": "gcr.io/cloud-builders/docker", "args": ["build", "--tag", tag, "."]},
                   {"name": "gcr.io/cloud-builders/docker", "args": ["push", tag]}]
    steps_projection = [{"name": s.get("name"), "args": s.get("args")} for s in build.get("steps", [])]
    if transport:
        need(all(not any(step.get(key) for key in ("entrypoint", "script", "env", "secretEnv", "dir", "volumes"))
                 for step in build.get("steps", [])), "transport_build_override")
        # Cloud Build's stock API accepts both literal Docker --tag spellings.
        if steps_projection and steps_projection[0].get("args") == ["build", "--tag=" + tag, "."]:
            stock_steps[0]["args"] = ["build", "--tag=" + tag, "."]
    need(build.get("id") == runner.build_id and build.get("projectId") == "ai-resume-parser-482412"
         and build.get("status") == "SUCCESS"
         and build.get("sourceProvenance", {}).get("resolvedGitSource") == {
             "url": runner.repository, "revision": runner.release, "dir": "."}
         and type(images) is list and len(images) == 1 and images[0].get("name") == tag
         and images[0].get("digest") == digest
         and steps_projection == stock_steps, "original_build_binding")
    semantic_sha = (runner.semantic_entry_sha256 if isinstance(runner, ReviewedDevelopmentTransportRunner)
                    else runner.entry_sha256)
    need(semantic_sha == packet.file_digest(backend / "scripts/native_acquisition_job.py"),
         "original_entry_changed")
    return runner.image


def _review_pages(closure: _Closure, directory: str, reference: dict[str, Any], source_sha: str,
                  extracted: inspection.InspectedResume, case: dict[str, Any]) -> None:
    validation = closure.relative(directory, reference["path"], "validation")
    need(validation.sha256 == reference["sha256"], "validation_changed")
    value = _json(read_artifact(closure.root, validation))
    text = value.get("extracted_text", {})
    text_ref = closure.relative(directory, Artifact.model_validate(text).path, "text")
    need(text_ref.sha256 == text["sha256"] and read_artifact(closure.root, text_ref).decode("utf-8")
         == extracted.parsed[0] and inputs.reading_order(case, extracted.parsed[0]), "reviewed_extraction")
    pages = value.get("rendered_pages", [])
    need(type(pages) is list and pages, "review_pages_missing")
    for page in pages:
        ref = Artifact.model_validate(page)
        bound = closure.relative(directory, ref.path, "page")
        need(ref.sha256 == bound.sha256, "review_page_changed")
    errors: list[str] = []
    packet._native_receipt(closure.root / directory, reference, source_sha=source_sha,
                           target_sha=source_sha, errors=errors, label=case["case_id"])
    need(not errors, "native_page_review")
    if case["source_format"] in {"tex", "texzip"}:
        need(value.get("inspected_artifact_sha256") == extracted.sha256, "reviewed_compiled_pdf")


def _transport_template(plan: TransportResourcePlan, image: str, batch: int) -> dict[str, Any]:
    """Literal final producer ABI, checked as data inside the keyless image."""
    return {"taskCount": 1, "parallelism": 1, "template": {
        "serviceAccount": plan.evaluator, "executionEnvironment": "EXECUTION_ENVIRONMENT_GEN2",
        "maxRetries": 0, "timeout": "240s" if batch == 0 else "1200s",
        "vpcAccess": {"egress": "PRIVATE_RANGES_ONLY", "networkInterfaces": [
            {"network": "hirewiz-private", "subnetwork": "hirewiz-callers-us-central1"}]},
        "containers": [{"image": image, "command": ["/app/backend/.venv/bin/python"],
            "args": ["-I", "-B", "/app/" + TRANSPORT_ENTRY, "--namespace",
                f"gs://{plan.bucket}/quality-development/{plan.experiment_id}/",
                "--bundle-name", plan.bundle_name, "--bundle-generation", plan.bundle_generation,
                "--bundle-sha256", plan.bundle_sha256, "--workspace", "/app/backend/.quality-development"],
            "env": [{"name": "DISABLE_GCS_PYTHON_CLIENT_OTEL_BUCKET_METADATA", "value": "true"}],
            "resources": {"limits": {"cpu": "2", "memory": "4Gi"}}}]}}


def _transport_origin(closure: _Closure, lock: NativeDevelopmentTransportLock) -> TransportOriginRecord:
    """Closed retained correlation only; the owner must still verify native GETs."""
    bound = lock.transport_origin.source_verification
    raw = closure.read(bound.artifact, "transport_origin")
    need(bound.reference.size == len(raw), "transport_origin_size")
    origin = TransportOriginRecord.model_validate(_json(raw))
    runner = ReviewedDevelopmentTransportRunner.model_validate(_json(closure.read(lock.runner_receipt, "control")))
    expected_source = {key: getattr(runner, key) for key in TransportSourceObservation.model_fields}
    need(origin.runner_receipt_sha256 == lock.runner_receipt.sha256
         == origin.verification.runner_receipt_sha256
         and origin.runner_source.model_dump() == expected_source
         and runner.observed_at <= origin.source_verified_at <= origin.native_verified_at <= lock.reviewed_at,
         "transport_origin_source")
    need(bridge.digest(origin.controller_ci_metadata) == origin.controller_ci_metadata_sha256,
         "transport_controller_ci_hash")
    controller_ci = origin.controller_ci_metadata
    jobs = controller_ci.get("jobs", [])
    need(controller_ci.get("headSha") == origin.controller_release
         and controller_ci.get("workflowName") == "CI" and controller_ci.get("status") == "completed"
         and controller_ci.get("conclusion") == "success" and type(jobs) is list and len(jobs) == 5
         and {row.get("name") for row in jobs} == {"backend", "frontend", "security-and-container",
             "browser-companion", "cold-browser-journey"}
         and all(row.get("status") == "completed" and row.get("conclusion") == "success" for row in jobs),
         "transport_controller_ci")
    need([row.job for row in origin.batches] == lock.transport_origin.jobs, "transport_origin_jobs")
    for row in origin.batches:
        plan = row.resource_plan
        raw_job = closure.read(row.job.artifact, "transport_job")
        job = _json(raw_job)
        name = "projects/ai-resume-parser-482412/locations/us-central1/jobs/" + plan.job_name
        expected = _transport_template(plan, runner.image, row.batch_index)
        need(row.job.reference.size == len(raw_job) and job.get("name") == name
             and job.get("uid") == row.job_uid and str(job.get("generation")) == row.job_generation
             == str(job.get("observedGeneration")) and job.get("reconciling", False) is False
             and job.get("terminalCondition", {}).get("state") == "CONDITION_SUCCEEDED"
             and packet.canonical(job.get("template")) == packet.canonical(expected)
             and bridge.digest(expected) == row.job_template_sha256
             and not any(job.get(key) for key in ("startExecutionToken", "runExecutionToken", "binaryAuthorization"))
             and not any(key in job for key in ("overrides", "runOverrides", "containerOverrides")),
             "transport_native_job")
        need(plan.qualified_runner_receipt_sha256 == lock.runner_receipt.sha256
             and plan.controller_sha256 == origin.controller_source_sha256
             and plan.valid_from <= origin.source_verified_at < plan.expires_at
             and plan.valid_from <= row.captured_at <= origin.native_verified_at
             and (plan.bundle_name, plan.bundle_generation, plan.bundle_sha256)
                 == (row.bundle.name, row.bundle.generation, row.bundle.sha256), "transport_origin_plan")
    return origin


def _original_batches(closure: _Closure, lock: DevelopmentLock, evidence: dict[str, Any],
                      original: development.Authorization, report: dict[str, Any], runner_image: str) -> None:
    """Validate original V1 data as V1, never authorize V2 through V1 code."""
    from scripts import native_acquisition_channel as original_channel
    from scripts import native_acquisition_job as original_job

    origin = _transport_origin(closure, lock) if isinstance(lock, NativeDevelopmentTransportLock) else None

    def retained(value: RetainedObject, kind: str, *, channel_object: bool = True) -> bytes:
        need(value.reference.sha256 == value.artifact.sha256, "original_object_hash")
        if channel_object:
            need(value.artifact.path == lock.channel_directory + "/" + value.reference.name, "original_object_path")
        raw = closure.read(value.artifact, kind)
        need(value.reference.size == len(raw), "original_object_size")
        return raw

    binding_raw = retained(lock.channel_binding, "control")
    configuration = original_channel.Binding.model_validate(_json(binding_raw))
    need(binding_raw == packet.canonical(configuration.model_dump(mode="json"))
         and configuration.namespace.endswith("/quality-development/" + original.experiment_id + "/")
         and configuration.acquisition_plan_sha256 == packet.digest(packet.canonical(report))
         and configuration.channel_source_sha256 == packet.file_digest(Path(original_channel.__file__)), "original_channel_binding")
    batches = evidence.get("native_batches", [])
    need(type(batches) is list and len(batches) == 9, "original_native_executions_required")
    phases = [(row, phase, row["case_id"] + "." + phase) for row in report["selected_cases"] for phase in row["phases"]]
    native_map = {"materialization-manifest.json": original.native_manifest_sha256,
                  **{row["source"]["path"]: row["source"]["sha256"] for row in report["selected_cases"]}}
    accumulated: dict[str, Any] = {}
    prior_terminal = None
    prior_dispatch = None
    names: set[str] = set()
    transport_execution_uids: set[str] = set()
    cursor = 0
    for index, raw_batch in enumerate(batches):
        batch = OriginalDevelopmentBatch.model_validate(raw_batch)
        count = 1 if index == 0 else 9 if index == 8 else 10
        wanted = phases[cursor:cursor + count]
        cursor += count
        need(batch.batch_index == index and set(batch.phase_terminals) == {key for _, _, key in wanted}, "original_batch_population")
        raw_bundle = retained(batch.bundle, "control", channel_object=False)
        bundle = original_job.Bundle.model_validate(_json(raw_bundle))
        need(bundle.authorization == original and bundle.configuration == configuration
             and re.fullmatch(r"hirewiz-quality-dev-[a-z0-9-]{8,32}", bundle.job_name)
             and bundle.entry_sha256 == packet.file_digest(Path(original_job.__file__))
             and bundle.dispatch.maximum_requests == count
             and bundle.dispatch.channel_binding.model_dump() == lock.channel_binding.reference.model_dump()
             and bundle.previous_dispatch == prior_dispatch
             and len(bundle.native_files) == 41
             and {row.path: row.object.sha256 for row in bundle.native_files} == native_map,
             "original_bundle_sources_or_authority")
        for row in bundle.native_files:
            artifact = closure.relative(lock.native_directory, row.path, "control" if row.path == "materialization-manifest.json" else "source")
            need(row.object.size == len(read_artifact(closure.root, artifact,
                MAX_METADATA if row.path == "materialization-manifest.json" else inspection.MAX_SOURCE_BYTES)), "original_input_size")
        reviews = {bundle.dispatch.external_authority_review.path}
        if prior_dispatch is not None:
            reviews.add(prior_dispatch.external_authority_review.path)
        required_authority = {original.funding_evidence.path, bundle.runner_funding_evidence.path, *reviews}
        need({row.path for row in bundle.authorization_files} == required_authority, "original_authority_files")
        for row in bundle.authorization_files:
            ref = closure.relative(lock.authorization_directory, row.path, "dispatch_review" if row.path in reviews else "control")
            need(row.object.sha256 == ref.sha256 and row.object.size == len(read_artifact(closure.root, ref)), "original_authority_input")
        artifacts = [original.funding_evidence, bundle.runner_funding_evidence, bundle.dispatch.external_authority_review]
        if prior_dispatch is not None:
            artifacts.append(prior_dispatch.external_authority_review)
        for artifact in artifacts:
            ref = closure.relative(lock.authorization_directory, artifact.path,
                "dispatch_review" if artifact.path in reviews else "control")
            need(ref.sha256 == artifact.sha256, "original_review_reference")
        ack = retained(batch.dispatch_ack, "batch")
        need(ack == original_channel.dispatch_bytes(bundle.dispatch)
             and bundle.dispatch.acknowledgment.model_dump() == batch.dispatch_ack.reference.model_dump()
             and bundle.dispatch.parent_terminal == prior_terminal
             and bundle.dispatch.before_checkpoint_sha256 == packet.digest(packet.canonical(accumulated)), "original_dispatch_binding")
        claim = _json(retained(batch.run_intent, "batch"))
        need(batch.run_intent.reference.name == "run." + bundle.dispatch.before_checkpoint_sha256 + ".intent.json"
             and claim == {"version": original_channel.VERSION, "dispatch_sha256": packet.digest(ack),
                           "before_checkpoint_sha256": bundle.dispatch.before_checkpoint_sha256}, "original_run_claim")
        execution = _json(closure.read(batch.execution, "provider"))
        name = execution.get("name", "")
        job_resource = "projects/ai-resume-parser-482412/locations/us-central1/jobs/" + bundle.job_name
        need(type(name) is str and re.fullmatch(re.escape(job_resource + "/executions/" + bundle.job_name + "-") + r"[a-z0-9]{1,20}", name)
             and name not in names and type(execution.get("uid")) is str
             and re.fullmatch(r"[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}", execution["uid"])
             and type(execution.get("taskCount")) is int and execution["taskCount"] == 1
             and type(execution.get("succeededCount")) is int and execution["succeededCount"] == 1
             and all(type(execution.get(k, 0)) is int and execution.get(k, 0) == 0
                     for k in ("failedCount", "cancelledCount", "runningCount", "retriedCount"))
             and execution.get("reconciling", False) is False, "original_execution_not_complete")
        need(execution.get("job") in {job_resource, bundle.job_name}, "original_execution_job")
        started, finished = (datetime.fromisoformat(execution.get(key, "")) for key in ("startTime", "completionTime"))
        need(started.tzinfo is not None and finished.tzinfo is not None
             and original.valid_from <= started <= finished <= lock.reviewed_at, "original_execution_time")
        template = execution.get("template", {})
        containers = template.get("containers", [])
        expected_args = ["-B", "-m", "scripts.native_acquisition_job", "--namespace", configuration.namespace,
            "--bundle-name", batch.bundle.reference.name, "--bundle-generation", batch.bundle.reference.generation,
            "--bundle-sha256", batch.bundle.reference.sha256, "--workspace", "/app/backend/.quality-development"]
        if origin is None:
            need(template.get("serviceAccount") == "hirewiz-quality-dev@ai-resume-parser-482412.iam.gserviceaccount.com"
                 and type(template.get("maxRetries")) is int and template["maxRetries"] == 0
                 and type(containers) is list and len(containers) == 1 and containers[0].get("image") == runner_image
                 and containers[0].get("command") == ["/app/backend/.venv/bin/python"]
                 and containers[0].get("args") == expected_args
                 and not containers[0].get("env") and not containers[0].get("envFrom"), "original_execution_bundle")
        else:
            proof = origin.batches[index]
            resource = proof.resource_plan
            need(execution["uid"] not in transport_execution_uids
                 and not any(key in execution for key in ("overrides", "runOverrides", "containerOverrides")),
                 "transport_execution_identity")
            transport_execution_uids.add(execution["uid"])
            need(proof.execution == batch.execution and proof.bundle == batch.bundle.reference
                 and resource.job_name == bundle.job_name and resource.experiment_id == original.experiment_id
                 and configuration.namespace == f"gs://{resource.bucket}/quality-development/{resource.experiment_id}/"
                 and original.valid_from <= resource.valid_from < resource.expires_at <= original.expires_at
                 and resource.valid_from <= started < resource.expires_at and finished <= proof.captured_at
                 and resource.acquisition_authorization_sha256 == bridge.digest(original.model_dump(mode="json"))
                 and resource.funding_evidence_sha256 == original.funding_evidence.sha256
                 and resource.native_dispatch_review_sha256 == bundle.dispatch.external_authority_review.sha256
                 and resource.inspector_image == original.inspector.image
                 and resource.compiler_image == original.compiler.image
                 and packet.canonical(template) == packet.canonical(_transport_template(resource, runner_image, index)["template"]),
                 "transport_execution_bundle")
        context = _json(retained(batch.job_context, "batch"))
        need(batch.job_context.reference.name == "job." + packet.digest(ack) + ".context.json"
             and type(context.get("task_index")) is int and type(context.get("task_attempt")) is int
             and context == {"job": bundle.job_name, "execution": name.rsplit("/", 1)[-1], "task_index": 0,
                             "task_attempt": 0, "custody": "environment_observation_not_provider_attestation"}, "original_execution_context")
        for row, phase, key in wanted:
            record = batch.phase_terminals[key]
            terminal = _json(retained(record, "terminal"))
            intent_raw = development._read(closure.root / lock.journal_directory, key + ".intent.json", MAX_PHASE_METADATA)
            stamp = datetime.fromisoformat(_json(intent_raw)["created_at"])
            need(started <= stamp <= finished and record.reference.name == key + ".terminal.json"
                 and set(terminal) == {"version", "key", "intent", "files"}
                 and terminal["version"] == original_channel.VERSION and terminal["key"] == key, "original_phase_terminal")
            intent = RetainedObjectReference.model_validate(terminal["intent"])
            need(intent.name == key + ".intent.json" and intent.sha256 == packet.digest(intent_raw)
                 and intent.size == len(intent_raw), "original_phase_intent")
            expected = {key + suffix for suffix in (".raw.json", ".retained.json", ".result.json")}
            if phase == "compile":
                expected.add(row["case_id"] + ".compiled.pdf")
            refs = [RetainedObjectReference.model_validate(ref) for ref in terminal["files"]]
            need(len(refs) == len(expected) and {ref.name for ref in refs} == expected, "original_phase_files")
            for ref in refs:
                raw = development._read(closure.root / lock.journal_directory, ref.name)
                need(ref.sha256 == packet.digest(raw) and ref.size == len(raw), "original_phase_response_binding")
            accumulated[key] = record.reference.model_dump(mode="json")
        terminal_raw = retained(batch.run_terminal, "batch")
        terminal = _json(terminal_raw)
        need(batch.run_terminal.reference.name == "run." + bundle.dispatch.before_checkpoint_sha256 + ".terminal.json"
             and type(terminal.get("requests_sent")) is int and terminal.get("authorizes_retail") is False
             and terminal == {"version": original_channel.VERSION, "state": "completed_unreviewed",
                 "channel_binding": bundle.dispatch.channel_binding.model_dump(mode="json"),
                 "dispatch": batch.dispatch_ack.reference.model_dump(mode="json"), "dispatch_sha256": packet.digest(ack),
                 "before_checkpoint_sha256": bundle.dispatch.before_checkpoint_sha256,
                 "after_checkpoint_sha256": packet.digest(packet.canonical(accumulated)),
                 "parent_terminal": prior_terminal.model_dump(mode="json") if prior_terminal else None,
                 "requests_sent": count, "actual_native_cost_minor": None, "authorizes_retail": False}, "original_checkpoint_chain")
        prior_terminal = original_channel.ObjectRef.model_validate(batch.run_terminal.reference.model_dump())
        prior_dispatch = bundle.dispatch
        names.add(name)
    need(cursor == len(phases) == 80, "original_complete_phase_chain")


def verify_native_development_lock(packet_root: Path, auth: Authorization,
                                   authorization_root: Path, *, require_active: bool = True) -> VerifiedDevelopment:
    """No held-out selection/materialization, model lock or provider access."""
    auth = _active(auth) if require_active else Authorization.model_validate(auth)
    need(auth.state == "authorized", "authorized_record_required")
    funding = read_artifact(authorization_root, auth.funding_evidence)
    raw_lock = read_artifact(authorization_root, auth.native_development_lock)
    lock = load_development_lock(raw_lock)
    need(lock.locked_at <= auth.valid_from, "lock_after_permit")
    closure = _Closure(authorization_root, lock)
    runner_image = _reviewed_runner(closure, lock)
    original = development.Authorization.model_validate(_json(closure.read(lock.development_authorization, "control")))
    need(original.state == "authorized" and original.split == "development" and original.request_limit == 80
         and original.inspector == auth.inspector and original.compiler == auth.compiler, "development_binding")
    native_root = authorization_root / lock.native_directory
    journal = authorization_root / lock.journal_directory
    source_authority = authorization_root / lock.authorization_directory
    # This is the original forty DEVELOPMENT plan and original DEVELOPMENT
    # journal, never a fabricated old authorization for held-out work.
    materialized = closure.relative(lock.native_directory, "materialization-manifest.json", "control")
    need(materialized.sha256 == original.native_manifest_sha256, "development_manifest")
    closure.relative(lock.authorization_directory, original.funding_evidence.path, "control")
    report = development.plan(packet_root, native_root, original, source_authority)
    need(closure.read(lock.development_plan, "control") == packet.canonical(report), "development_plan_changed")
    manifest, cases = inputs.selected_cases(packet_root, "development")
    need(lock.rubric_sha256 == manifest["rubric_sha256"] and len(cases) == 40
         and Counter(c["source_format"] for c in cases) == dict.fromkeys(("pdf", "docx", "tex", "texzip"), 10),
         "complete_development_population")
    registry = _json(closure.read(lock.capture_registry, "control"))
    validations = _json(closure.read(lock.validation_map, "control"))
    expected_ids = {c["case_id"] for c in cases}
    need(registry.get("custody") == "operator_reviewed_native_capture_v1"
         and registry.get("manifest_sha256") == inputs.MANIFEST_SHA
         and registry.get("reviewed_by") == lock.reviewed_by
         and registry.get("reviewed_at") == lock.reviewed_at.isoformat()
         and set(registry.get("captures", {})) == expected_ids and set(validations) == expected_ids,
         "complete_review_registry")
    by_id = {case["case_id"]: case for case in cases}
    for row in report["selected_cases"]:
        source = closure.relative(lock.native_directory, row["source"]["path"], "source")
        need(source.sha256 == row["source"]["sha256"], "development_source_changed")
        for phase in row["phases"]:
            key = row["case_id"] + "." + phase
            for suffix, kind in (("intent", "intent"), ("retained", "retained"), ("result", "result"),
                                 ("raw", "compile_raw" if phase == "compile" else "pdf_raw" if phase == "pdf" else "source_raw")):
                closure.relative(lock.journal_directory, key + "." + suffix + ".json", kind)
        if row["source_format"] in {"tex", "texzip"}:
            closure.relative(lock.journal_directory, row["case_id"] + ".compiled.pdf", "compiled_pdf")
        capture = registry["captures"][row["case_id"]]
        need(capture == development._capture(journal, row), "capture_correlation_changed")
        imported = native.decode(journal, capture, read_artifact(authorization_root, source, inspection.MAX_SOURCE_BYTES),
            row["source_format"], worker_image=auth.inspector.image, worker_policy=auth.inspector.policy,
            compiler_image=auth.compiler.image, compiler_policy=auth.compiler.policy)
        _review_pages(closure, lock.review_directory, validations[row["case_id"]], source.sha256,
                      imported.extraction, by_id[row["case_id"]])
    development._verify_journal(journal, report, native_root, original)
    status = development._status(journal, report)
    phases = [phase for row in status["cases"] for phase in row["phases"].values()]
    need(len(phases) == 80 and all(p["state"] == "captured_unreviewed" for p in phases), "development_capture_incomplete")
    evidence = _json(closure.read(lock.review_evidence, "control"))
    need(evidence.get("state") == "completed_native_development_review"
         and evidence.get("reviewed_by") == lock.reviewed_by
         and evidence.get("reviewed_at") == lock.reviewed_at.isoformat()
         and evidence.get("development_plan_sha256") == lock.development_plan.sha256
         and evidence.get("capture_registry_sha256") == lock.capture_registry.sha256
         and evidence.get("validation_map_sha256") == lock.validation_map.sha256, "review_evidence_binding")
    _original_batches(closure, lock, evidence, original, report, runner_image)
    need(closure.used == set(closure.rows), "unused_or_missing_review_artifact")
    # Re-read exact hashes after decoders/page checks; do not cache an approved
    # object across a changed underlying file or use hashes as reviewer intent.
    for row in lock.files:
        closure.read(row, row.kind)
    need(read_artifact(authorization_root, auth.native_development_lock) == raw_lock, "lock_changed_during_review")
    if require_active:
        _active(auth)
    authority_bytes = closure.bytes + len(raw_lock) + len(funding)
    need(authority_bytes + MAX_BUNDLE_BYTES <= MAX_INPUT_BYTES, "authority_bytes")
    return VerifiedDevelopment(auth.native_development_lock.sha256,
        inputs.digest({name: closure.rows[name].sha256 for name in sorted(closure.used)}),
        lock.development_plan.sha256, tuple(c["case_id"] for c in cases), authority_bytes)


def plan(packet_root: Path, native_root: Path, auth: Authorization,
         authorization_root: Path, *, require_active: bool = True) -> dict[str, Any]:
    """Select no held-out text until the full native40 lock has matched."""
    # Expired allocations may be reconciled read-only. Native execution always
    # uses the active default and rechecks again immediately before each send.
    reviewed = (verify_native_development_lock(packet_root, auth, authorization_root) if require_active
                else verify_native_development_lock(packet_root, auth, authorization_root, require_active=False))
    manifest, cases = inputs.selected_cases(packet_root, "held_out")
    need(len(cases) == 160 and Counter(c["source_format"] for c in cases)
         == dict.fromkeys(("pdf", "docx", "tex", "texzip"), 40), "complete_held_out_population")
    artifact = Artifact(path="materialization-manifest.json", sha256=auth.native_manifest_sha256)
    materialized = _json(read_artifact(native_root, artifact))
    need(materialized.get("packet_manifest_sha256") == inputs.MANIFEST_SHA
         and materialized.get("corpus_sha256") == inputs.CORPUS_SHA, "materialization_corpus")
    rows = materialized.get("cases", [])
    need(type(rows) is list and len(rows) == 160, "exact_native_membership")
    indexed = {r["case_id"]: r for r in rows}
    need(len(indexed) == 160 and set(indexed) == {c["case_id"] for c in cases}, "exact_native_membership")
    selected, total = [], reviewed.authority_bytes + MAX_BUNDLE_BYTES + len(read_artifact(native_root, artifact))
    for case in cases:
        row = indexed[case["case_id"]]
        need(row.get("input_sha256") == inputs.digest(case), "case_changed")
        source = Artifact.model_validate(row["input_artifact"])
        raw = read_artifact(native_root, source, inspection.MAX_SOURCE_BYTES)
        total += len(raw)
        need(total <= MAX_INPUT_BYTES and packet._native(native_root / source.path, case["source_format"]), "source_format_or_bound")
        need(read_artifact(native_root, source, inspection.MAX_SOURCE_BYTES) == raw, "source_changed_during_format_check")
        selected.append({"case_id": case["case_id"], "source_format": case["source_format"],
                         "source": source.model_dump(), "phases": list(development.PHASES if case["source_format"]
                                                                 in {"tex", "texzip"} else development.PHASES[:1])})
    need(sum(len(row["phases"]) for row in selected) == auth.request_limit == 320, "request_population")
    expected_files = {"materialization-manifest.json", *(row["source"]["path"] for row in selected)}
    need(len(expected_files) == 161, "duplicate_native_source")
    expected_directories = {str(parent) for name in expected_files for parent in Path(name).parents if str(parent) != "."}
    pending = [native_root]
    observed: set[str] = set()
    while pending:
        directory = pending.pop()
        development._private(directory, directory=True)
        for path in directory.iterdir():
            relative = str(path.relative_to(native_root))
            need(not path.is_symlink(), "native_link")
            if path.is_dir():
                need(relative in expected_directories, "extra_native_directory")
                pending.append(path)
            else:
                need(relative in expected_files, "extra_native_file")
                development._private(path)
                observed.add(relative)
    need(observed == expected_files, "exact_native_membership")
    need(read_artifact(authorization_root, auth.native_development_lock)
         and read_artifact(native_root, artifact), "input_changed")
    if require_active:
        _active(auth)
    return {"schema_version": VERSION, "split": "held_out", "case_count": 200,
        "split_counts": {"development": 40, "held_out": 160}, "selected_cases": selected,
        "complete_case_registry": manifest["cases"], "maximum_http_requests": 320,
        "reserved_minor": 320 * auth.reserved_minor_per_request,
        "reserved_minor_per_request": auth.reserved_minor_per_request,
        "journal_path": auth.journal_path, "authorization_sha256": inputs.digest(auth.model_dump(mode="json")),
        "native_development_lock_sha256": reviewed.lock_sha256, "reviewed_closure_sha256": reviewed.closure_sha256,
        "tool_sha256": packet.file_digest(Path(__file__)), "provider_requests": 0,
        "processing_authorized": False, "native_approval": False, "actual_native_cost_minor": None,
        "authorizes_retail": False}


def phase_state(root: Path, key: str, *, reserved_minor: int) -> dict[str, Any]:
    """An intent alone is UNKNOWN with its entire native expense still held."""
    need(re.fullmatch(r"synth-[0-9]{3}\.(?:source|compile|pdf)", key) and type(reserved_minor) is int
         and reserved_minor > 0, "phase_identity")
    intent, result = root / (key + ".intent.json"), root / (key + ".result.json")
    if not intent.exists():
        need(not any((root / (key + suffix)).exists() for suffix in (".result.json", ".raw.json", ".retained.json")),
             "orphan_result")
        return {"state": "never_sent"}
    value = PhaseIntent.model_validate(_json(development._read(root, intent.name, MAX_PHASE_METADATA)))
    need(value.local_intent_id == key and value.reserved_minor == reserved_minor, "intent_changed")
    need(not result.exists(), "terminal_requires_durable_channel")
    return {"state": "unknown_no_replay", "reserved_minor": reserved_minor,
            "actual_native_cost_minor": None, "replay_authorized": False, "reserved_expense_released": False}


class PhaseIntent(Closed):
    schema_version: Literal["held-out-native-service-acquisition-v2"]
    local_intent_id: str = Field(pattern=r"^synth-[0-9]{3}\.(?:source|compile|pdf)$")
    request_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    source_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    original_source_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    authorization_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    reserved_minor: int = Field(strict=True, gt=0)
    actual_native_cost_minor: None
    created_at: datetime
    replay_authorized: bool = Field(strict=True)

    @model_validator(mode="after")
    def aware(self) -> PhaseIntent:
        need(self.created_at.tzinfo is not None, "intent_time")
        need(self.replay_authorized is False, "intent_replay_forbidden")
        return self


def create_phase_intent(root: Path, key: str, value: dict[str, Any]) -> Artifact:
    """Create-only local custody; not a channel ACK or permission to send."""
    value = PhaseIntent.model_validate(value).model_dump(mode="json")
    development._private(root, directory=True)
    raw = packet.canonical(value)
    need(0 < len(raw) <= MAX_PHASE_METADATA, "intent_bound")
    descriptor = os.open(root / "intent.lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        development._private(root / "intent.lock")
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise AcquisitionDenied("held_out_native_journal_busy") from None
        state = phase_state(root, key, reserved_minor=value["reserved_minor"])
        need(state["state"] == "never_sent" and value["local_intent_id"] == key, "intent_not_new")
        count, size = 0, 0
        for path in root.iterdir():
            development._private(path)
            count += 1
            size += path.stat().st_size
            need(count < 2048 and size + len(raw) <= MAX_JOURNAL_BYTES, "journal_bound")
        return Artifact.model_validate(development._new(root, key + ".intent.json", raw))
    finally:
        os.close(descriptor)


class PhaseResult(Closed):
    state: Literal["captured_unreviewed", "refused_no_replay", "invalid_response_no_replay"]
    reserved_minor: int = Field(strict=True, gt=0)
    actual_native_cost_minor: None
    replay_authorized: bool = Field(strict=True)

    @model_validator(mode="after")
    def no_replay(self) -> PhaseResult:
        need(self.replay_authorized is False, "result_replay_forbidden")
        return self


class NativeTransport(Protocol):
    def inspect(self, binding: development.Binding, source: bytes, kind: str) -> bytes: ...
    def compile(self, binding: development.Binding, source: bytes, kind: str) -> bytes: ...


def journal_new(root: Path, name: str, raw: bytes) -> Artifact:
    """Bounded create-only write under the caller's exact journal/restore owner."""
    development._private(root, directory=True)
    need(type(raw) is bytes and 0 < len(raw) <= MAX_ARTIFACT, "journal_artifact_bound")
    total, count = len(raw), 1
    for path in root.iterdir():
        development._private(path)
        total += path.stat().st_size
        count += 1
        need(total <= MAX_JOURNAL_BYTES and count <= 2048, "journal_bound")
    return Artifact.model_validate(development._new(root, name, raw))


def _request(source: bytes, row: dict[str, Any], phase: str) -> bytes:
    return (native.compile_request(source, row["source_format"]) if phase == "compile"
            else development.inspection_payload(source, "pdf" if phase == "pdf" else row["source_format"]))


def _validate_capture(root: Path, row: dict[str, Any], phase: str, source: bytes,
                      raw: bytes, auth: Authorization, *, create_pdf: bool = False) -> None:
    if phase == "compile":
        response = _json(raw)
        need(set(response) == {"protocol", "pdf", "sha256", "size", "renderer", "request_sha256",
                               "renderer_image", "isolation_policy_sha256"}
             and response.get("request_sha256") == packet.digest(native.compile_request(source, row["source_format"]))
             and response.get("renderer_image") == auth.compiler.image
             and response.get("isolation_policy_sha256") == auth.compiler.policy, "compiler_response")
        pdf = native_tex._compiler_pdf(response)
        need(len(pdf) <= inspection.MAX_SOURCE_BYTES, "compiled_pdf_bound")
        name = row["case_id"] + ".compiled.pdf"
        if (root / name).exists():
            need(development._read(root, name, inspection.MAX_SOURCE_BYTES) == pdf, "compiled_pdf_changed")
        else:
            need(create_pdf, "compiled_pdf_missing")
            journal_new(root, name, pdf)
        return
    kind = "pdf" if phase == "pdf" else row["source_format"]
    inspected = inspection._decode_receipt(raw, source, kind,
        inspection.WorkerConfig(auth.inspector.url, "", auth.inspector.image, auth.inspector.policy))
    if kind in {"tex", "texzip"}:
        parsed = inspected.parsed
        need(parsed[0] == "" and parsed[1] == {} and parsed[2] == [] and parsed[3] == 0
             and all(value is None for value in parsed[4].values()), "source_admission_must_be_blank")


def verify_journal(root: Path, report: dict[str, Any], native_root: Path, auth: Authorization) -> None:
    """V2 intent/retained/result validation; no V1 authorization or journal reuse."""
    for row in report["selected_cases"]:
        original = read_artifact(native_root, Artifact.model_validate(row["source"]), inspection.MAX_SOURCE_BYTES)
        previous = True
        for phase in row["phases"]:
            key = row["case_id"] + "." + phase
            names = {suffix: root / (key + suffix) for suffix in
                     (".intent.json", ".raw.json", ".retained.json", ".result.json")}
            if not names[".intent.json"].exists():
                need(not any(path.exists() for suffix, path in names.items() if suffix != ".intent.json"), "orphan_result")
                if phase == "compile":
                    need(not (root / (row["case_id"] + ".compiled.pdf")).exists(), "orphan_compilation")
                previous = False
                continue
            need(previous, "phase_predecessor_not_captured")
            source = (development._read(root, row["case_id"] + ".compiled.pdf", inspection.MAX_SOURCE_BYTES)
                      if phase == "pdf" else original)
            raw_intent = development._read(root, key + ".intent.json", MAX_PHASE_METADATA)
            intent = PhaseIntent.model_validate(_json(raw_intent))
            need(intent.local_intent_id == key and intent.request_sha256 == packet.digest(_request(source, row, phase))
                 and intent.source_sha256 == packet.digest(source) and intent.original_source_sha256 == packet.digest(original)
                 and intent.authorization_sha256 == report["authorization_sha256"]
                 and intent.reserved_minor == auth.reserved_minor_per_request
                 and auth.valid_from <= intent.created_at < auth.expires_at, "intent_changed")
            if not names[".result.json"].exists():
                previous = False
                continue
            result = PhaseResult.model_validate(_json(development._read(root, key + ".result.json", MAX_PHASE_METADATA)))
            need(result.reserved_minor == auth.reserved_minor_per_request, "result_changed")
            previous = result.state == "captured_unreviewed"
            if result.state == "refused_no_replay":
                need(not names[".raw.json"].exists() and not names[".retained.json"].exists(), "refusal_artifact_conflict")
                continue
            raw = development._read(root, key + ".raw.json", MAX_ARTIFACT if phase == "compile" else inspection.MAX_RESPONSE_BYTES)
            retained = _json(development._read(root, key + ".retained.json", MAX_PHASE_METADATA))
            need(retained == {"intent_sha256": packet.digest(raw_intent), "response": {
                "path": key + ".raw.json", "sha256": packet.digest(raw)}}, "retained_response_changed")
            if previous:
                _validate_capture(root, row, phase, source, raw, auth)


def status(root: Path, report: dict[str, Any], auth: Authorization) -> dict[str, Any]:
    rows = []
    for row in report["selected_cases"]:
        phases = {}
        for phase in row["phases"]:
            key = row["case_id"] + "." + phase
            if (root / (key + ".result.json")).exists():
                phases[phase] = PhaseResult.model_validate(_json(development._read(root,
                    key + ".result.json", MAX_PHASE_METADATA))).model_dump(mode="json")
            elif (root / (key + ".intent.json")).exists():
                phases[phase] = {"state": "unknown_no_replay", "reserved_minor": auth.reserved_minor_per_request,
                                 "actual_native_cost_minor": None, "replay_authorized": False}
            else:
                phases[phase] = {"state": "never_sent"}
        rows.append({"case_id": row["case_id"], "phases": phases})
    return {"schema_version": VERSION, "cases": rows, "native_approval": False,
            "processing_authorized": False, "actual_native_cost_minor": None,
            "reserved_expense_released": False, "authorizes_retail": False}


def execute(packet_root: Path, native_root: Path, auth: Authorization, authorization_root: Path,
            journal: Path, *, channel: Any, context: Any, initialize: bool = False,
            maximum_requests: int = 1, transport: NativeTransport | None = None) -> dict[str, Any]:
    """One source-level batch; native Job/dispatch qualification is still separate."""
    from evaluation.native_acquisition_v2.channel import Channel, JobContext

    need(type(channel) is Channel and type(maximum_requests) is int
         and maximum_requests == channel.dispatch.maximum_requests, "exact_durable_channel_required")
    context = JobContext.model_validate(context)
    report = plan(packet_root, native_root, auth, authorization_root)
    _active(auth)
    need(journal.is_absolute() and str(journal.resolve()) == str(journal) == auth.journal_path, "journal_authorization_mismatch")
    with development.locked(journal, report, create=initialize):
        try:
            verify_journal(journal, report, native_root, auth)
            observed = status(journal, report, auth)
            need(not any(p["state"] == "unknown_no_replay" for r in observed["cases"] for p in r["phases"].values()),
                 "unknown_reconcile_only")
            channel.begin(journal, report, initialize=initialize, maximum_requests=maximum_requests, context=context)
            sender = transport or development.ServiceTransport()
            sent = 0
            for row in report["selected_cases"]:
                original = read_artifact(native_root, Artifact.model_validate(row["source"]), inspection.MAX_SOURCE_BYTES)
                for phase in row["phases"]:
                    key = row["case_id"] + "." + phase
                    result_path = journal / (key + ".result.json")
                    if result_path.exists():
                        prior = PhaseResult.model_validate(_json(development._read(journal, result_path.name, MAX_PHASE_METADATA)))
                        if prior.state != "captured_unreviewed":
                            break
                        continue
                    if sent == maximum_requests:
                        terminal = channel.finish()
                        return {"status": status(journal, report, auth), "terminal": terminal.model_dump(mode="json"), "requests_sent": sent}
                    source = development._read(journal, row["case_id"] + ".compiled.pdf", inspection.MAX_SOURCE_BYTES) if phase == "pdf" else original
                    payload = _request(source, row, phase)
                    _active(auth)
                    create_phase_intent(journal, key, {"schema_version": VERSION, "local_intent_id": key,
                        "request_sha256": packet.digest(payload), "source_sha256": packet.digest(source),
                        "original_source_sha256": packet.digest(original), "authorization_sha256": report["authorization_sha256"],
                        "reserved_minor": auth.reserved_minor_per_request, "actual_native_cost_minor": None,
                        "created_at": datetime.now(UTC), "replay_authorized": False})
                    channel.before_send(journal, key)
                    _active(auth)
                    sent += 1
                    try:
                        raw = (sender.compile(auth.compiler, source, row["source_format"]) if phase == "compile"
                               else sender.inspect(auth.inspector, source, "pdf" if phase == "pdf" else row["source_format"]))
                        need(type(raw) is bytes and 0 < len(raw) <= (MAX_ARTIFACT if phase == "compile" else inspection.MAX_RESPONSE_BYTES), "response_bound")
                        response = journal_new(journal, key + ".raw.json", raw)
                        journal_new(journal, key + ".retained.json", packet.canonical({
                            "intent_sha256": packet.digest(development._read(journal, key + ".intent.json", MAX_PHASE_METADATA)),
                            "response": response.model_dump(mode="json")}))
                    except ResumeProcessingUnavailable:
                        raise AcquisitionDenied("held_out_native_unknown_reconcile_only") from None
                    except (inspection.DocumentInspectionError, ResumeLayoutError) as error:
                        if isinstance(error, inspection.DocumentInspectionError) and error.status_code != 422:
                            raise AcquisitionDenied("held_out_native_unknown_reconcile_only") from None
                        result = PhaseResult(state="refused_no_replay", reserved_minor=auth.reserved_minor_per_request,
                                             actual_native_cost_minor=None, replay_authorized=False)
                        journal_new(journal, key + ".result.json", packet.canonical(result.model_dump(mode="json")))
                        channel.terminal(journal, key)
                        break
                    try:
                        _validate_capture(journal, row, phase, source, raw, auth, create_pdf=True)
                        state: Literal["captured_unreviewed", "invalid_response_no_replay"] = "captured_unreviewed"
                    except Exception:
                        state = "invalid_response_no_replay"
                    result = PhaseResult(state=state, reserved_minor=auth.reserved_minor_per_request,
                                         actual_native_cost_minor=None, replay_authorized=False)
                    journal_new(journal, key + ".result.json", packet.canonical(result.model_dump(mode="json")))
                    channel.terminal(journal, key)
                    if state != "captured_unreviewed":
                        break
            terminal = channel.finish()
            return {"status": status(journal, report, auth), "terminal": terminal.model_dump(mode="json"), "requests_sent": sent}
        except BaseException:
            try:
                channel.stop(journal)
            except BaseException:
                pass  # Original durable claims remain; never repair by POST.
            raise AcquisitionDenied("held_out_native_unknown_or_refused_no_replay") from None
