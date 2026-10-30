"""One-send, operator-funded service HTTP captures, never native approval.

First usable scope: the frozen forty development inputs. No Job is created,
no lost response can be retrieved, and no unknown request can be replayed.
Local journal integrity is operator custody, not provider attestation.
"""
from __future__ import annotations

import asyncio
import base64
import fcntl
import hashlib
import json
import os
import re
import stat
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from pydantic import Field, model_validator

from ...services import document_ingestion as inspection
from ...services import native_project, native_tex, native_tex_transport
from ...services.resume_layout import ResumeLayoutError, ResumeProcessingUnavailable
from . import development_runner as legacy
from . import quality_packet as packet
from . import supported_evaluation_native as native
from . import supported_evaluation_plan as inputs

VERSION = "development-native-service-acquisition-v1"
PHASES = ("source", "compile", "pdf")
MAX_ARTIFACT = 15 * 1024 * 1024


def need(value, code):
    if not value:
        raise ValueError("native_acquisition_" + code)


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


class Binding(legacy.Closed):
    url: str = Field(max_length=512)
    image: str = Field(pattern=r"^[a-zA-Z0-9._/:-]+@sha256:[a-f0-9]{64}$", max_length=512)
    policy: str = Field(pattern=r"^[a-f0-9]{64}$")


class Authorization(legacy.Closed):
    schema_version: Literal["development-native-service-acquisition-v1"]
    purpose: Literal["unreviewed_native_input_capture_no_model_no_retail"]
    state: Literal["draft", "authorized"]
    authorized_by: str = Field(min_length=1, max_length=160)
    experiment_id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{7,79}$")
    journal_path: str = Field(min_length=1, max_length=2048)
    split: Literal["development"]
    valid_from: datetime
    expires_at: datetime
    packet_manifest_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    native_manifest_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    funding_evidence: legacy.Artifact
    request_limit: int = Field(strict=True, ge=1, le=400)
    reserved_minor_per_request: int = Field(strict=True, gt=0)
    maximum_native_reserved_minor: int = Field(strict=True, gt=0)
    currency: Literal["INR"]
    inspector: Binding
    compiler: Binding

    @model_validator(mode="after")
    def finite(self):
        need(self.packet_manifest_sha256 == inputs.MANIFEST_SHA, "packet_changed")
        need(Path(self.journal_path).is_absolute() and str(Path(self.journal_path).resolve()) == self.journal_path,
             "exact_absolute_journal_required")
        need(all(t.tzinfo is not None for t in (self.valid_from, self.expires_at))
             and self.valid_from < self.expires_at <= self.valid_from + timedelta(days=1), "time_window")
        for binding, route in ((self.inspector, "/inspect"), (self.compiler, "/compile")):
            target = urlsplit(binding.url)
            need(target.scheme == "https" and re.fullmatch(r"[a-z0-9-]+(?:\.[a-z0-9-]+)*\.run\.app", target.hostname or "")
                 and target.username is None and target.password is None and target.port is None
                 and target.path == route and not target.query and not target.fragment, "private_service_binding")
        return self


def plan(packet_root, native_root, auth, authorization_root):
    manifest, cases = inputs.selected_cases(packet_root, auth.split)
    path = native_root / "materialization-manifest.json"
    need(packet.file_digest(path) == auth.native_manifest_sha256, "materialization_changed")
    materialized = json.loads(path.read_bytes())
    need(materialized.get("packet_manifest_sha256") == inputs.MANIFEST_SHA
         and materialized.get("corpus_sha256") == inputs.CORPUS_SHA, "wrong_corpus")
    rows = materialized.get("cases", [])
    need(type(rows) is list and len({r["case_id"] for r in rows}) == len(rows), "duplicate_sources")
    indexed = {r["case_id"]: r for r in rows}
    selected = []
    for case in cases:
        row = indexed.get(case["case_id"], {})
        need(row.get("input_sha256") == inputs.digest(case), "case_changed")
        source = legacy._artifact(native_root, legacy.Artifact.model_validate(row.get("input_artifact")))
        need(source.stat().st_size <= inspection.MAX_SOURCE_BYTES and packet._native(source, case["source_format"]), "source_format")
        selected.append({"case_id": case["case_id"], "source_format": case["source_format"],
                         "source": row["input_artifact"], "phases": list(PHASES if case["source_format"] in {"tex", "texzip"} else PHASES[:1])})
    requests = sum(len(row["phases"]) for row in selected)
    need(requests <= auth.request_limit and requests * auth.reserved_minor_per_request <= auth.maximum_native_reserved_minor,
         "whole_split_reserve_required")
    legacy._artifact(authorization_root, auth.funding_evidence)
    return {"schema_version": VERSION, "split": auth.split, "case_count": 200,
            "split_counts": {"development": 40, "held_out": 160}, "selected_cases": selected,
            "complete_case_registry": manifest["cases"], "maximum_http_requests": requests,
            "reserved_minor": requests * auth.reserved_minor_per_request,
            "reserved_minor_per_request": auth.reserved_minor_per_request,
            "journal_path": auth.journal_path,
            "actual_native_cost_minor": None, "authorization_sha256": inputs.digest(auth.model_dump(mode="json")),
            "tool_sha256": packet.file_digest(Path(__file__)),
            "client_sources_sha256": {Path(str(module.__file__)).name: packet.file_digest(Path(str(module.__file__)))
                                      for module in (inspection, native_tex, native_tex_transport, native, native_project)},
            "source_custody": "local_operator_http_capture_not_provider_attestation",
            "held_out_acquisition": "not_enabled_in_first_scope_requires_separate_locked_review",
            "provider_requests": 0, "authorizes_retail": False}


def _private(path, *, directory=False):
    info = path.lstat()
    need(not stat.S_ISLNK(info.st_mode) and info.st_uid == os.getuid()
         and stat.S_IMODE(info.st_mode) & 0o077 == 0
         and (stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode) and info.st_nlink == 1), "private_owned_path_required")


def _new(root, name, raw):
    need(re.fullmatch(r"[a-zA-Z0-9._-]{1,160}", name) and len(raw) <= MAX_ARTIFACT, "artifact_bound")
    descriptor = os.open(root / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "wb") as file:
        file.write(raw)
        file.flush()
        os.fsync(file.fileno())
    descriptor = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    return {"path": name, "sha256": digest(raw)}


def _read(root, name, limit=MAX_ARTIFACT):
    path = root / name
    _private(path)
    need(path.stat().st_size <= limit, "artifact_bound")
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(descriptor, "rb") as file:
        raw = file.read(limit + 1)
    need(len(raw) <= limit, "artifact_bound")
    return raw


@contextmanager
def locked(root, report, *, create=False):
    if create:
        root.mkdir(mode=0o700, parents=False, exist_ok=False)
    _private(root, directory=True)
    descriptor = os.open(root / "lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        _private(root / "lock")
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if create:
            _new(root, "plan.json", packet.canonical(report))
        need(_read(root, "plan.json") == packet.canonical(report), "plan_changed")
        yield
    finally:
        os.close(descriptor)


class ServiceTransport:
    def inspect(self, binding, source, kind):
        config = inspection.WorkerConfig(binding.url, "https://" + urlsplit(binding.url).netloc, binding.image, binding.policy)
        payload = inspection_payload(source, kind)
        token = inspection._identity_token(config.audience)
        return asyncio.run(inspection._inspect_http(config, payload, token))

    def compile(self, binding, source, kind):
        return native_tex_transport.remote_compile_capture(native.compile_request(source, kind),
            url=binding.url, image=binding.image, policy=binding.policy)


def inspection_payload(source, kind):
    # Exact existing production inspection request serialization.
    return json.dumps({"version": 1, "source_format": kind,
        "content_base64": base64.b64encode(source).decode("ascii"), "sha256": digest(source)}).encode("utf-8")


def _artifact(root, name):
    raw = _read(root, name)
    return {"path": name, "sha256": digest(raw)}


def _capture(root, row):
    identity, kind = row["case_id"], row["source_format"]
    capture = {"source_sha256": row["source"]["sha256"],
        "request_correlation_id": digest(_read(root, identity + ".source.intent.json", 16384)),
        "worker_response": _artifact(root, identity + ".source.raw.json")}
    if kind in {"tex", "texzip"}:
        capture["compilation"] = {"source_sha256": row["source"]["sha256"],
            "request_correlation_id": digest(_read(root, identity + ".compile.intent.json", 16384)),
            "worker_response": _artifact(root, identity + ".compile.raw.json"),
            "pdf": _artifact(root, identity + ".compiled.pdf"),
            "pdf_inspection_response": _artifact(root, identity + ".pdf.raw.json")}
    return capture


def _validate(root, row, phase, source, raw, auth, *, create_pdf=False):
    if phase == "compile":
        response = json.loads(raw)
        need(type(response) is dict and set(response) == {"protocol", "pdf", "sha256", "size", "renderer", "request_sha256", "renderer_image", "isolation_policy_sha256"}
             and response.get("request_sha256") == digest(native.compile_request(source, row["source_format"]))
             and response.get("renderer_image") == auth.compiler.image and response.get("isolation_policy_sha256") == auth.compiler.policy
             and response.get("renderer") == "pdftex-no-shell-escape-v1", "compiler_response")
        pdf = native_tex._compiler_pdf(response)
        need(len(pdf) <= inspection.MAX_SOURCE_BYTES, "compiled_pdf_bound")
        name = row["case_id"] + ".compiled.pdf"
        if (root / name).exists():
            need(_read(root, name) == pdf, "compiled_pdf_changed")
        else:
            need(create_pdf, "compiled_pdf_missing")
            _new(root, name, pdf)
        return
    kind = "pdf" if phase == "pdf" else row["source_format"]
    config = inspection.WorkerConfig(auth.inspector.url, "", auth.inspector.image, auth.inspector.policy)
    inspected = inspection._decode_receipt(raw, source, kind, config)
    if kind in {"tex", "texzip"}:
        parsed = inspected.parsed
        need(parsed[0] == "" and parsed[1] == {} and parsed[2] == [] and parsed[3] == 0
             and all(v is None for v in parsed[4].values()), "source_admission_must_be_blank")


def _status(root, report):
    rows = []
    for row in report["selected_cases"]:
        phases = {}
        for phase in row["phases"]:
            key = row["case_id"] + "." + phase
            if (root / (key + ".result.json")).exists():
                phases[phase] = json.loads(_read(root, key + ".result.json", 16384))
            elif (root / (key + ".intent.json")).exists():
                phases[phase] = {"state": "unknown_no_replay", "reserved_minor": report["reserved_minor_per_request"], "actual_native_cost_minor": None}
            else:
                phases[phase] = {"state": "never_sent"}
        rows.append({"case_id": row["case_id"], "phases": phases})
    return {"schema_version": VERSION, "cases": rows, "native_approval": "pending", "actual_native_cost_minor": None,
            "request_replay_authorized": False, "job_created": False, "authorizes_retail": False}


def _journal_matches(journal, auth):
    need(str(journal.absolute()) == auth.journal_path and str(journal.resolve()) == auth.journal_path,
         "journal_authorization_mismatch")


def _verify_journal(root, report, native_root, auth):
    """A result label cannot bypass its one-send intent or immutable response."""
    for row in report["selected_cases"]:
        original = legacy._artifact(native_root, legacy.Artifact.model_validate(row["source"])).read_bytes()
        need(digest(original) == row["source"]["sha256"], "source_changed")
        previous = True
        for phase in row["phases"]:
            key = row["case_id"] + "." + phase
            suffixes = (".intent.json", ".result.json", ".raw.json", ".retained.json")
            names = {suffix: root / (key + suffix) for suffix in suffixes}
            if not names[".intent.json"].exists():
                need(not any(p.exists() for suffix, p in names.items() if suffix != ".intent.json"), "orphaned_result")
                previous = False
                continue
            need(previous, "phase_predecessor_not_captured")
            source = _read(root, row["case_id"] + ".compiled.pdf") if phase == "pdf" else original
            request = native.compile_request(source, row["source_format"]) if phase == "compile" else inspection_payload(source, "pdf" if phase == "pdf" else row["source_format"])
            intent_raw = _read(root, key + ".intent.json", 16384)
            intent = json.loads(intent_raw)
            stamp = datetime.fromisoformat(intent["created_at"])
            expected = {"schema_version": VERSION, "local_intent_id": key, "request_sha256": digest(request),
                "source_sha256": digest(source), "original_source_sha256": digest(original),
                "authorization_sha256": report["authorization_sha256"], "reserved_minor": auth.reserved_minor_per_request,
                "actual_native_cost_minor": None, "created_at": intent["created_at"], "replay_authorized": False}
            need(intent == expected and stamp.tzinfo is not None and auth.valid_from <= stamp < auth.expires_at, "intent_changed")
            if not names[".result.json"].exists():
                previous = False
                continue
            result = json.loads(_read(root, key + ".result.json", 16384))
            need(result.get("state") in {"captured_unreviewed", "refused_no_replay", "invalid_response_no_replay"}
                 and result == {"state": result["state"], "reserved_minor": auth.reserved_minor_per_request,
                                "actual_native_cost_minor": None}, "result_changed")
            previous = result["state"] == "captured_unreviewed"
            if result["state"] == "refused_no_replay":
                need(not names[".raw.json"].exists() and not names[".retained.json"].exists(), "refusal_artifact_conflict")
                continue
            raw = _read(root, key + ".raw.json")
            retained = json.loads(_read(root, key + ".retained.json", 16384))
            need(retained == {"intent_sha256": digest(intent_raw), "response": {"path": key + ".raw.json", "sha256": digest(raw)}}, "retained_response_changed")
            if previous:
                _validate(root, row, phase, source, raw, auth)


def execute(packet_root, native_root, auth, authorization_root, journal, *, maximum_requests=1, transport=None, initialize=False, channel=None):
    need(type(maximum_requests) is int and 1 <= maximum_requests <= 10, "invocation_bound")
    report = plan(packet_root, native_root, auth, authorization_root)
    now = datetime.now(UTC)
    need(auth.state == "authorized" and auth.valid_from <= now < auth.expires_at, "active_authorization_required")
    _journal_matches(journal, auth)
    with locked(journal, report, create=initialize):
        _verify_journal(journal, report, native_root, auth)
        if channel is not None:
            from scripts.native_acquisition_channel import Channel
            need(type(channel) is Channel, "exact_durable_channel_required")
            channel.begin(journal, report, initialize=initialize, maximum_requests=maximum_requests)
        else:
            marker = journal / "durable-channel.json"
            need(not marker.exists() and not marker.is_symlink(), "durable_channel_required")
        sent = 0
        status = _status(journal, report)
        need(not any(p["state"] == "unknown_no_replay" for r in status["cases"] for p in r["phases"].values()), "unknown_reconcile_only")
        transport = transport or ServiceTransport()
        for row in report["selected_cases"]:
            original = legacy._artifact(native_root, legacy.Artifact.model_validate(row["source"])).read_bytes()
            need(digest(original) == row["source"]["sha256"], "source_changed")
            for phase in row["phases"]:
                key = row["case_id"] + "." + phase
                if (journal / (key + ".result.json")).exists():
                    if json.loads(_read(journal, key + ".result.json", 16384))["state"] != "captured_unreviewed":
                        break
                    continue
                if sent >= maximum_requests:
                    return _status(journal, report)
                need(auth.valid_from <= datetime.now(UTC) < auth.expires_at, "authorization_expired")
                source = _read(journal, row["case_id"] + ".compiled.pdf") if phase == "pdf" else original
                request = native.compile_request(source, row["source_format"]) if phase == "compile" else inspection_payload(source, "pdf" if phase == "pdf" else row["source_format"])
                intent = {"schema_version": VERSION, "local_intent_id": key, "request_sha256": digest(request),
                    "source_sha256": digest(source), "original_source_sha256": digest(original),
                    "authorization_sha256": report["authorization_sha256"], "reserved_minor": auth.reserved_minor_per_request,
                    "actual_native_cost_minor": None, "created_at": datetime.now(UTC).isoformat(), "replay_authorized": False}
                _new(journal, key + ".intent.json", packet.canonical(intent))
                if channel is not None:
                    channel.before_send(journal, key)
                sent += 1
                try:
                    raw = transport.compile(auth.compiler, source, row["source_format"]) if phase == "compile" else transport.inspect(auth.inspector, source, "pdf" if phase == "pdf" else row["source_format"])
                    need(type(raw) is bytes and 0 < len(raw) <= (MAX_ARTIFACT if phase == "compile" else inspection.MAX_RESPONSE_BYTES), "response_bound")
                    artifact = _new(journal, key + ".raw.json", raw)
                    _new(journal, key + ".retained.json", packet.canonical({"intent_sha256": digest(packet.canonical(intent)), "response": artifact}))
                except ResumeProcessingUnavailable:
                    raise ValueError("native_acquisition_unknown_reconcile_only") from None
                except (inspection.DocumentInspectionError, ResumeLayoutError) as exc:
                    if isinstance(exc, inspection.DocumentInspectionError) and exc.status_code != 422:
                        raise ValueError("native_acquisition_unknown_reconcile_only") from None
                    _new(journal, key + ".result.json", packet.canonical({"state": "refused_no_replay", "reserved_minor": auth.reserved_minor_per_request, "actual_native_cost_minor": None}))
                    if channel is not None:
                        channel.terminal(journal, key)
                    break
                except BaseException:
                    # Durable intent remains UNKNOWN, including interruption or
                    # response-storage failure. No raw exception is persisted.
                    raise ValueError("native_acquisition_unknown_reconcile_only") from None
                try:
                    _validate(journal, row, phase, source, raw, auth, create_pdf=True)
                    state = "captured_unreviewed"
                except Exception:
                    state = "invalid_response_no_replay"
                _new(journal, key + ".result.json", packet.canonical({"state": state, "reserved_minor": auth.reserved_minor_per_request, "actual_native_cost_minor": None}))
                if channel is not None:
                    channel.terminal(journal, key)
                if state != "captured_unreviewed":
                    break
        return _status(journal, report)


def inspect_local(packet_root, native_root, auth, authorization_root, journal):
    report = plan(packet_root, native_root, auth, authorization_root)
    _journal_matches(journal, auth)
    with locked(journal, report):
        _verify_journal(journal, report, native_root, auth)
        return _status(journal, report)


def export_unreviewed(packet_root, native_root, auth, authorization_root, journal, destination):
    report = plan(packet_root, native_root, auth, authorization_root)
    _journal_matches(journal, auth)
    with locked(journal, report):
        _verify_journal(journal, report, native_root, auth)
        destination.mkdir(mode=0o700, parents=False, exist_ok=False)
        _new(destination, "acquisition-plan.json", packet.canonical(report))
        for row in report["selected_cases"]:
            for phase in row["phases"]:
                key = row["case_id"] + "." + phase
                for suffix in (".intent.json", ".retained.json", ".result.json", ".raw.json"):
                    if (journal / (key + suffix)).exists():
                        _new(destination, key + suffix, _read(journal, key + suffix))
        captures = {}
        cases = {c["case_id"]: c for c in inputs.selected_cases(packet_root, auth.split)[1]}
        quality = {}
        for row in report["selected_cases"]:
            if not all((journal / (row["case_id"] + "." + p + ".result.json")).exists()
                       and json.loads(_read(journal, row["case_id"] + "." + p + ".result.json", 16384))["state"] == "captured_unreviewed"
                       for p in row["phases"]):
                continue
            capture = _capture(journal, row)
            original = legacy._artifact(native_root, legacy.Artifact.model_validate(row["source"])).read_bytes()
            imported = native.decode(journal, capture, original, row["source_format"], worker_image=auth.inspector.image,
                worker_policy=auth.inspector.policy, compiler_image=auth.compiler.image, compiler_policy=auth.compiler.policy)
            quality[row["case_id"]] = {"reading_order_pass": inputs.reading_order(cases[row["case_id"]], imported.extraction.parsed[0]),
                "native_page_review": "pending", "native_approval": "pending"}
            artifacts = [capture["worker_response"]]
            if "compilation" in capture:
                artifacts += [capture["compilation"][k] for k in ("worker_response", "pdf", "pdf_inspection_response")]
            for artifact in artifacts:
                raw = _read(journal, artifact["path"])
                need(digest(raw) == artifact["sha256"], "export_artifact_changed")
                if (destination / artifact["path"]).exists():
                    need(_read(destination, artifact["path"]) == raw, "export_artifact_changed")
                else:
                    _new(destination, artifact["path"], raw)
            captures[row["case_id"]] = capture
        registry = {"custody": "unreviewed_service_http_capture_v1", "manifest_sha256": inputs.MANIFEST_SHA,
            "correlation_kind": "durable_local_intent_not_provider_operation_id", "captures": captures,
            "native_review": "pending_no_admission_authority", "quality": quality}
        _new(destination, "capture-registry.unreviewed.json", packet.canonical(registry))
        _new(destination, "acquisition-status.json", packet.canonical(_status(journal, report)))
        return {"state": "unreviewed_export", "captured_cases": len(captures), "selected_cases": 40,
                "quality_qualified": False, "authorizes_retail": False}
