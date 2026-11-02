"""Closed held-out input planning after complete native-development review.

This module reads retained, operator-reviewed evidence. Hashes and protocol
validation are not provider attestation or permission to send a request. The
separate durable channel, native Job, source qualification and funded dispatch
are deliberately not implemented or issued here.
"""
from __future__ import annotations

import fcntl
import json
import os
import re
import stat
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.domains.analysis import native_input_acquisition as development
from app.domains.analysis import quality_packet as packet
from app.domains.analysis import supported_evaluation_native as native
from app.domains.analysis import supported_evaluation_plan as inputs
from app.services import document_ingestion as inspection

VERSION = "held-out-native-service-acquisition-v2"
LOCK_VERSION = "native-development-review-lock-v2"
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
    development_authorization: Artifact
    development_plan: Artifact
    capture_registry: Artifact
    validation_map: Artifact
    review_evidence: Artifact
    runner_receipt: Artifact
    ci_metadata: Artifact
    build_metadata: Artifact
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
                       self.review_directory, self.authorization_directory)
        need(len(set(directories)) == 4 and not any(
            a.startswith(b + "/") for a in directories for b in directories if a != b), "lock_directories")
        return self


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
    def __init__(self, root: Path, lock: NativeDevelopmentLock):
        self.root, self.rows = root, {row.path: row for row in lock.files}
        self.used: set[str] = set()
        self.bytes = 0

    def read(self, artifact: Artifact, kind: str) -> bytes:
        row = self.rows.get(artifact.path)
        need(row is not None and row.sha256 == artifact.sha256 and row.kind == kind, "unbound_review_artifact")
        raw = read_artifact(self.root, artifact, KINDS[kind][1])
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


def _reviewed_runner(closure: _Closure, lock: NativeDevelopmentLock) -> str:
    """Cross-bind retained metadata, never replace the fresh native verifier."""
    from infra.gcp import native_quality_acquisition_isolation as isolation

    from scripts import qualify_native_quality_acquisition as source
    from scripts import schema25_app_release_contract_v3 as contract

    runner = isolation.QualifiedRunner.model_validate_json(closure.read(lock.runner_receipt, "control"))
    ci = _json(closure.read(lock.ci_metadata, "control"))
    build = _json(closure.read(lock.build_metadata, "control"))
    need(runner.ci_metadata_sha256 == source.digest(ci)
         and runner.build_metadata_sha256 == source.digest(build), "original_metadata_hashes")
    jobs = ci.get("jobs", [])
    names = {"backend", "frontend", "security-and-container", "browser-companion", "cold-browser-journey"}
    need(ci.get("headSha") == runner.release and ci.get("workflowName") == "CI"
         and ci.get("status") == "completed" and ci.get("conclusion") == "success"
         and type(jobs) is list and len(jobs) == 5 and {j.get("name") for j in jobs} == names
         and all(j.get("status") == "completed" and j.get("conclusion") == "success" for j in jobs),
         "original_ci_not_complete")
    steps = next(j for j in jobs if j["name"] == "backend").get("steps", [])
    for name in (contract.COMPATIBILITY_STEP, contract.PROTOCOL_STEP, contract.RETAINED_STEP):
        matches = [s for s in steps if s.get("name") == name]
        need(len(matches) == 1 and matches[0].get("status") == "completed"
             and matches[0].get("conclusion") == "success", "original_qualification_steps")
    repository, digest = runner.image.split("@")
    tag = repository + ":" + runner.release
    images = build.get("results", {}).get("images", [])
    need(build.get("id") == runner.build_id and build.get("projectId") == isolation.PROJECT
         and build.get("status") == "SUCCESS"
         and build.get("sourceProvenance", {}).get("resolvedGitSource") == {
             "url": runner.repository, "revision": runner.release, "dir": "."}
         and type(images) is list and len(images) == 1 and images[0].get("name") == tag
         and images[0].get("digest") == digest
         and [{"name": s.get("name"), "args": s.get("args")} for s in build.get("steps", [])]
         == [{"name": "gcr.io/cloud-builders/docker", "args": ["build", "--tag", tag, "."]},
             {"name": "gcr.io/cloud-builders/docker", "args": ["push", tag]}], "original_build_binding")
    need(runner.entry_path == "backend/scripts/native_acquisition_job.py"
         and runner.entry_sha256 == packet.file_digest(Path(__file__).resolve().parents[2] / "scripts/native_acquisition_job.py"),
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


def verify_native_development_lock(packet_root: Path, auth: Authorization,
                                   authorization_root: Path) -> VerifiedDevelopment:
    """No held-out selection/materialization, model lock or provider access."""
    auth = _active(auth)
    funding = read_artifact(authorization_root, auth.funding_evidence)
    raw_lock = read_artifact(authorization_root, auth.native_development_lock)
    lock = NativeDevelopmentLock.model_validate(_json(raw_lock))
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
    executions = evidence.get("native_execution_readbacks", [])
    need(type(executions) is list and len(executions) == 9, "original_native_executions_required")
    names: set[str] = set()
    phase_times = [datetime.fromisoformat(_json(development._read(journal,
        row["case_id"] + "." + phase + ".intent.json", MAX_PHASE_METADATA))["created_at"])
        for row in report["selected_cases"] for phase in row["phases"]]
    cursor = 0
    for batch, ref in enumerate(executions):
        value = _json(closure.read(Artifact.model_validate(ref), "provider"))
        name = value.get("name", "")
        need(type(name) is str and re.fullmatch(
            r"projects/ai-resume-parser-482412/locations/us-central1/jobs/hirewiz-quality-dev-[a-z0-9-]{8,32}/executions/[a-z0-9-]{1,63}", name)
            and name not in names and type(value.get("uid")) is str
            and re.fullmatch(r"[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}", value["uid"])
            and type(value.get("taskCount")) is int and value["taskCount"] == 1
            and type(value.get("succeededCount")) is int and value["succeededCount"] == 1
            and all(type(value.get(k, 0)) is int and value.get(k, 0) == 0
                    for k in ("failedCount", "cancelledCount", "runningCount", "retriedCount"))
            and value.get("reconciling", False) is False, "original_execution_not_complete")
        stamp = datetime.fromisoformat(value.get("completionTime", ""))
        started = datetime.fromisoformat(value.get("startTime", ""))
        count = 1 if batch == 0 else 9 if batch == 8 else 10
        need(stamp.tzinfo is not None and started.tzinfo is not None
             and original.valid_from <= started <= stamp <= lock.reviewed_at
             and all(started <= t <= stamp for t in phase_times[cursor:cursor + count]), "original_execution_time")
        cursor += count
        template = value.get("template", {})
        containers = template.get("containers", [])
        args = containers[0].get("args", []) if type(containers) is list and len(containers) == 1 else []
        need(template.get("serviceAccount") == "hirewiz-quality-dev@ai-resume-parser-482412.iam.gserviceaccount.com"
             and type(template.get("maxRetries")) is int and template["maxRetries"] == 0
             and type(containers) is list and len(containers) == 1 and containers[0].get("image") == runner_image
             and containers[0].get("command") == ["/app/backend/.venv/bin/python"]
             and type(args) is list and len(args) == 13 and all(type(arg) is str for arg in args)
             and args[:5] == ["-B", "-m", "scripts.native_acquisition_job", "--namespace",
                 "gs://hirewiz-quality-dev-482412-20261031/quality-development/" + original.experiment_id + "/"],
             "original_execution_template")
        need(args[5] == "--bundle-name" and re.fullmatch(r"input\.[A-Za-z0-9._-]{1,120}\.json", args[6])
             and args[7] == "--bundle-generation" and re.fullmatch(r"[1-9][0-9]{0,20}", args[8])
             and args[9] == "--bundle-sha256" and re.fullmatch(r"[a-f0-9]{64}", args[10])
             and args[11:] == ["--workspace", "/app/backend/.quality-development"]
             and not containers[0].get("env") and not containers[0].get("envFrom"), "original_execution_arguments")
        names.add(name)
    need(closure.used == set(closure.rows), "unused_or_missing_review_artifact")
    # Re-read exact hashes after decoders/page checks; do not cache an approved
    # object across a changed underlying file or use hashes as reviewer intent.
    for row in lock.files:
        closure.read(row, row.kind)
    need(read_artifact(authorization_root, auth.native_development_lock) == raw_lock, "lock_changed_during_review")
    _active(auth)
    authority_bytes = closure.bytes + len(raw_lock) + len(funding)
    need(authority_bytes + MAX_BUNDLE_BYTES <= MAX_INPUT_BYTES, "authority_bytes")
    return VerifiedDevelopment(auth.native_development_lock.sha256,
        inputs.digest({name: closure.rows[name].sha256 for name in sorted(closure.used)}),
        lock.development_plan.sha256, tuple(c["case_id"] for c in cases), authority_bytes)


def plan(packet_root: Path, native_root: Path, auth: Authorization,
         authorization_root: Path) -> dict[str, Any]:
    """Select no held-out text until the full native40 lock has matched."""
    reviewed = verify_native_development_lock(packet_root, auth, authorization_root)
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
    replay_authorized: Literal[False]

    @model_validator(mode="after")
    def aware(self) -> PhaseIntent:
        need(self.created_at.tzinfo is not None, "intent_time")
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
