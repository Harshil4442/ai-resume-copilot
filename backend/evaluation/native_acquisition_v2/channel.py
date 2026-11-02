"""Create-only held-out capture custody; never Job/funding/native attestation.

The object port must provide generation-bound reads and create-if-absent ACKs.
An uncertain ACK is not retried. Neither a fresh local directory nor a new
dispatch can reuse an acknowledged batch/phase claim.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Protocol

from pydantic import Field, model_validator

from app.domains.analysis import quality_packet as packet
from evaluation.native_acquisition_v2 import engine as e

VERSION = "held-out-native-object-channel-v2"
MAX_BYTES = 2 * 1024**3
MAX_OBJECTS = 2048
MAX_METADATA = 256 * 1024
NAME = r"[A-Za-z0-9._-]{1,160}"


def need(value: object, code: str) -> None:
    if not value:
        raise e.AcquisitionDenied("held_out_channel_" + code)


class ObjectRef(e.Closed):
    name: str = Field(pattern="^" + NAME + "$")
    generation: str = Field(pattern=r"^[1-9][0-9]{0,20}$")
    size: int = Field(strict=True, ge=1, le=e.MAX_ARTIFACT)
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


@dataclass(frozen=True)
class Object:
    reference: ObjectRef
    raw: bytes


class Objects(Protocol):
    namespace: str

    def fetch(self, name: str, *, generation: str | None = None, maximum: int) -> Object | None: ...
    def create(self, name: str, raw: bytes) -> ObjectRef: ...


class Binding(e.Closed):
    schema_version: Literal["held-out-native-object-channel-v2"]
    namespace: str = Field(pattern=r"^gs://[a-z0-9][a-z0-9._-]{2,221}/quality-held-out/[a-z0-9][a-z0-9-]{7,79}/$")
    acquisition_plan_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    channel_source_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    engine_source_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    maximum_output_bytes: int = Field(strict=True, ge=1, le=MAX_BYTES)
    maximum_objects: int = Field(strict=True, ge=1, le=MAX_OBJECTS)


def batch_limit(index: int) -> int:
    need(type(index) is int and 0 <= index < 33, "batch_index")
    return 1 if index == 0 else 9 if index == 32 else 10


class Dispatch(e.Closed):
    schema_version: Literal["reviewed-held-out-native-dispatch-v2"]
    purpose: Literal["unreviewed_held_out_capture_external_authority_required"]
    reviewed_by: str = Field(min_length=1, max_length=160)
    external_authority_review: e.Artifact
    channel_binding: ObjectRef
    acknowledgment: ObjectRef
    batch_index: int = Field(strict=True, ge=0, le=32)
    before_checkpoint_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    parent_terminal: ObjectRef | None
    maximum_requests: int = Field(strict=True, ge=1, le=10)

    @model_validator(mode="after")
    def exact(self) -> Dispatch:
        need(self.channel_binding.name == "channel-binding.json"
             and self.acknowledgment.name == f"dispatch.{self.batch_index:02}.json"
             and self.maximum_requests == batch_limit(self.batch_index), "dispatch_identity")
        need((self.batch_index == 0) == (self.parent_terminal is None), "dispatch_parent")
        if self.parent_terminal is not None:
            need(self.parent_terminal.name == f"run.{self.batch_index - 1:02}.terminal.json", "dispatch_parent")
        return self


class JobContext(e.Closed):
    schema_version: Literal["held-out-native-job-context-v2"]
    job: str = Field(pattern=r"^hirewiz-quality-held-out-[a-z0-9-]{8,32}$")
    execution: str = Field(pattern=r"^[a-z0-9-]{1,83}$")
    task_index: int = Field(strict=True, ge=0, le=0)
    task_attempt: int = Field(strict=True, ge=0, le=0)
    task_count: int = Field(strict=True, ge=1, le=1)
    custody: Literal["environment_observation_not_provider_attestation"]

    @model_validator(mode="after")
    def prefix(self) -> JobContext:
        need(re.fullmatch(re.escape(self.job) + r"-[a-z0-9]{1,20}", self.execution), "execution_context")
        return self


def binding(report: dict[str, Any], namespace: str, *, maximum_output_bytes: int = MAX_BYTES,
            maximum_objects: int = MAX_OBJECTS) -> Binding:
    """Pure configuration, never creates an ACK, grant or approved dispatch."""
    return Binding(schema_version=VERSION, namespace=namespace,
        acquisition_plan_sha256=packet.digest(packet.canonical(report)),
        channel_source_sha256=packet.file_digest(Path(__file__)),
        engine_source_sha256=packet.file_digest(Path(e.__file__)),
        maximum_output_bytes=maximum_output_bytes, maximum_objects=maximum_objects)


def dispatch_bytes(value: Dispatch) -> bytes:
    return packet.canonical(value.model_dump(mode="json", exclude={"acknowledgment"}))


def _checked(value: Object, name: str, maximum: int) -> Object:
    need(type(value) is Object and type(value.reference) is ObjectRef
         and value.reference.name == name and type(value.raw) is bytes
         and 0 < len(value.raw) <= maximum and value.reference.size == len(value.raw)
         and value.reference.sha256 == packet.digest(value.raw), "object_changed")
    ObjectRef.model_validate(value.reference)
    return value


@dataclass
class Snapshot:
    checkpoint: str
    files: dict[str, bytes]
    unknown_phases: list[str]
    batches: dict[int, dict[str, Any]]
    unowned_phase_keys: set[str]


class Channel:
    def __init__(self, store: Objects, configuration: Binding, dispatch: Dispatch, *,
                 authorization_root: Path, authorization: e.Authorization):
        self.store = store
        self.configuration = Binding.model_validate(configuration)
        self.dispatch = Dispatch.model_validate(dispatch)
        self.authorization = e.Authorization.model_validate(authorization)
        need(store.namespace == self.configuration.namespace
             and store.namespace.endswith("/quality-held-out/" + authorization.experiment_id + "/"), "namespace")
        e.read_artifact(authorization_root, self.dispatch.external_authority_review)
        self._authorization_sha = e.inputs.digest(authorization.model_dump(mode="json"))
        self._references: dict[str, ObjectRef] = {}
        self._specs: dict[str, int] = {}
        self._rows: dict[str, tuple[dict[str, Any], str]] = {}
        self._report: dict[str, Any] = {}
        self._journal: Path | None = None
        self._claim: ObjectRef | None = None
        self._context: ObjectRef | None = None
        self._sent: list[str] = []
        self._started = False

    def _prepare(self, report: dict[str, Any]) -> None:
        need(report.get("schema_version") == e.VERSION and report.get("split") == "held_out"
             and report.get("maximum_http_requests") == 320
             and report.get("authorization_sha256") == self._authorization_sha
             and report.get("native_development_lock_sha256") == self.authorization.native_development_lock.sha256
             and self.configuration.acquisition_plan_sha256 == packet.digest(packet.canonical(report))
             and self.configuration.channel_source_sha256 == packet.file_digest(Path(__file__))
             and self.configuration.engine_source_sha256 == report.get("tool_sha256")
             == packet.file_digest(Path(e.__file__)), "source_or_plan_changed")
        rows = report.get("selected_cases", [])
        need(type(rows) is list and len(rows) == 160
             and len({r["case_id"] for r in rows}) == 160, "exact_cases")
        self._report, self._rows = report, {}
        self._specs = {"channel-binding.json": MAX_METADATA, "acquisition-plan.json": MAX_METADATA}
        for row in rows:
            need(re.fullmatch(r"synth-[0-9]{3}", row["case_id"]), "case_identity")
            phases = ["source", "compile", "pdf"] if row["source_format"] in {"tex", "texzip"} else ["source"]
            need(row["source_format"] in {"pdf", "docx", "tex", "texzip"} and row["phases"] == phases, "case_phases")
            for phase in phases:
                key = row["case_id"] + "." + phase
                self._rows[key] = row, phase
                for suffix in ("intent", "retained", "result"):
                    self._specs[key + "." + suffix + ".json"] = e.MAX_PHASE_METADATA
                self._specs[key + ".raw.json"] = e.MAX_ARTIFACT if phase == "compile" else e.inspection.MAX_RESPONSE_BYTES
                self._specs[key + ".terminal.json"] = MAX_METADATA
            if len(phases) == 3:
                self._specs[row["case_id"] + ".compiled.pdf"] = e.inspection.MAX_SOURCE_BYTES
        for index in range(33):
            for name in (f"dispatch.{index:02}.json", f"run.{index:02}.intent.json",
                         f"run.{index:02}.terminal.json", f"job.{index:02}.context.json"):
                self._specs[name] = MAX_METADATA
        need(len(self._rows) == 320 and len(self._specs) == 1814, "complete_output_inventory")

    def _bounds(self, *, extra_objects: int = 0, extra_bytes: int = 0) -> None:
        need(len(self._references) + extra_objects <= self.configuration.maximum_objects
             and sum(ref.size for ref in self._references.values()) + extra_bytes
             <= self.configuration.maximum_output_bytes, "output_budget")

    def _get(self, name: str, reference: ObjectRef | None = None) -> Object | None:
        need(name in self._specs, "undeclared_object")
        value = self.store.fetch(name, generation=reference.generation if reference else None, maximum=self._specs[name])
        if value is None:
            need(reference is None, "acknowledged_object_missing")
            return None
        _checked(value, name, self._specs[name])
        need(reference is None or value.reference == reference, "object_generation_changed")
        need(name not in self._references or self._references[name] == value.reference, "object_reference_conflict")
        self._references[name] = value.reference
        self._bounds()
        return value

    def _required(self, reference: ObjectRef) -> Object:
        value = self._get(reference.name, reference)
        assert value is not None
        return value

    def _create(self, name: str, raw: bytes) -> ObjectRef:
        need(name in self._specs and type(raw) is bytes and 0 < len(raw) <= self._specs[name], "object_bound")
        need(name not in self._references, "create_only_conflict")
        self._bounds(extra_objects=1, extra_bytes=len(raw))
        reference = ObjectRef.model_validate(self.store.create(name, raw))
        need(reference.name == name and reference.size == len(raw) and reference.sha256 == packet.digest(raw), "create_ack_changed")
        self._references[name] = reference
        return reference

    def _phase(self, key: str, row: dict[str, Any], phase: str) -> tuple[ObjectRef | None, dict[str, bytes], bool]:
        intent = self._get(key + ".intent.json")
        terminal = self._get(key + ".terminal.json")
        payload: dict[str, Object] = {}
        for suffix in ("raw", "retained", "result"):
            value = self._get(key + "." + suffix + ".json")
            if value is not None:
                payload[value.reference.name] = value
        if phase == "compile":
            value = self._get(row["case_id"] + ".compiled.pdf")
            if value is not None:
                payload[value.reference.name] = value
        if intent is None:
            need(terminal is None and not payload, "orphan_phase_artifacts")
            return None, {}, False
        local = {intent.reference.name: intent.raw, **{name: item.raw for name, item in payload.items()}}
        if terminal is None:
            return None, local, True
        body = e._json(terminal.raw)
        need(set(body) == {"schema_version", "key", "intent", "files"} and body["schema_version"] == VERSION
             and body["key"] == key and ObjectRef.model_validate(body["intent"]) == intent.reference, "phase_terminal")
        refs = [ObjectRef.model_validate(ref) for ref in body["files"]]
        need(len(refs) == len({ref.name for ref in refs}) and {r.name for r in refs} == set(payload), "phase_terminal_files")
        for ref in refs:
            need(payload[ref.name].reference == ref, "phase_terminal_generation")
        result_name = key + ".result.json"
        need(result_name in local, "phase_result_missing")
        result = e.PhaseResult.model_validate(e._json(local[result_name]))
        expected = {result_name}
        if result.state != "refused_no_replay":
            expected |= {key + ".raw.json", key + ".retained.json"}
        if result.state == "captured_unreviewed" and phase == "compile":
            expected.add(row["case_id"] + ".compiled.pdf")
        need(set(payload) == expected and result.reserved_minor == self.authorization.reserved_minor_per_request, "incomplete_phase")
        return terminal.reference, local, False

    def _snapshot(self, report: dict[str, Any]) -> Snapshot:
        self._prepare(report)
        self._references.clear()
        configured = self._required(self.dispatch.channel_binding)
        need(configured.raw == packet.canonical(self.configuration.model_dump(mode="json")), "binding_changed")
        plan = self._get("acquisition-plan.json")
        need(plan is not None and plan.raw == packet.canonical(report), "remote_plan_changed")
        files = {"plan.json": packet.canonical(report)}
        terminals: dict[str, ObjectRef] = {}
        unknown = []
        for key, (row, phase) in self._rows.items():
            phase_terminal, payload, uncertain = self._phase(key, row, phase)
            files.update(payload)
            if phase_terminal is not None:
                terminals[key] = phase_terminal
            if uncertain:
                unknown.append(key)
        batches: dict[int, dict[str, Any]] = {}
        prior: ObjectRef | None = None
        accumulated: dict[str, dict[str, Any]] = {}
        for index in range(33):
            ack = self._get(f"dispatch.{index:02}.json")
            claim = self._get(f"run.{index:02}.intent.json")
            context = self._get(f"job.{index:02}.context.json")
            terminal = self._get(f"run.{index:02}.terminal.json")
            if ack is None:
                need(claim is None and context is None and terminal is None, "orphan_batch")
                continue
            dispatch = Dispatch.model_validate({**e._json(ack.raw), "acknowledgment": ack.reference.model_dump()})
            need(ack.raw == dispatch_bytes(dispatch) and dispatch.batch_index == index
                 and dispatch.channel_binding == self.dispatch.channel_binding, "batch_dispatch_changed")
            if claim is None:
                need(context is None and terminal is None, "orphan_batch")
                batches[index] = {"state": "unclaimed", "dispatch": dispatch}
                continue
            claim_body = e._json(claim.raw)
            expected_claim = {"schema_version": VERSION, "batch_index": index,
                "dispatch": ack.reference.model_dump(mode="json"), "dispatch_sha256": packet.digest(ack.raw),
                "before_checkpoint_sha256": dispatch.before_checkpoint_sha256,
                "parent_terminal": dispatch.parent_terminal.model_dump(mode="json") if dispatch.parent_terminal else None,
                "authorization_sha256": self._authorization_sha, "context_sha256": claim_body.get("context_sha256")}
            need(claim_body == expected_claim and re.fullmatch(r"[a-f0-9]{64}", claim_body.get("context_sha256", "")), "batch_claim_changed")
            if context is not None:
                observed = JobContext.model_validate(e._json(context.raw))
                need(context.raw == packet.canonical(observed.model_dump(mode="json"))
                     and packet.digest(context.raw) == claim_body["context_sha256"], "batch_context_changed")
            if terminal is None:
                batches[index] = {"state": "unknown_no_replay", "dispatch": dispatch,
                                  "claim": claim.reference, "context": context.reference if context else None}
                continue
            need(context is not None and index == len([b for b in batches.values() if b["state"] == "completed_unreviewed"]),
                 "batch_chain_gap")
            assert context is not None
            value = e._json(terminal.raw)
            refs = {key: ObjectRef.model_validate(ref) for key, ref in value.get("phase_terminals", {}).items()}
            need(type(value.get("requests_sent")) is int and 1 <= value["requests_sent"] <= batch_limit(index)
                 and len(refs) == value["requests_sent"] and not set(refs) & set(accumulated)
                 and all(key in terminals and ref == terminals[key] for key, ref in refs.items()), "batch_phase_refs")
            before = packet.digest(packet.canonical(accumulated))
            accumulated.update({key: ref.model_dump(mode="json") for key, ref in refs.items()})
            after = packet.digest(packet.canonical(accumulated))
            expected_terminal = {"schema_version": VERSION, "state": "completed_unreviewed", "batch_index": index,
                "channel_binding": self.dispatch.channel_binding.model_dump(mode="json"),
                "dispatch": ack.reference.model_dump(mode="json"), "run_intent": claim.reference.model_dump(mode="json"),
                "job_context": context.reference.model_dump(mode="json"),
                "parent_terminal": prior.model_dump(mode="json") if prior else None,
                "before_checkpoint_sha256": before, "after_checkpoint_sha256": after,
                "phase_terminals": {key: ref.model_dump(mode="json") for key, ref in refs.items()},
                "requests_sent": len(refs), "actual_native_cost_minor": None, "authorizes_retail": False}
            need(value == expected_terminal and dispatch.parent_terminal == prior
                 and dispatch.before_checkpoint_sha256 == before, "batch_terminal_changed")
            batches[index] = {"state": "completed_unreviewed", "dispatch": dispatch,
                              "claim": claim.reference, "context": context.reference, "terminal": terminal.reference}
            prior = terminal.reference
        # A completed phase may belong to the currently claimed unfinished
        # batch, but cannot silently appear outside that unique claim.
        dangling = set(terminals) - set(accumulated)
        need(not dangling or any(b["state"] == "unknown_no_replay" for b in batches.values()), "unowned_phase_terminal")
        return Snapshot(packet.digest(packet.canonical({k: v.model_dump(mode="json") for k, v in terminals.items()})),
                        files, unknown, batches, dangling)

    def _unclaimed(self, snapshot: Snapshot) -> None:
        index = self.dispatch.batch_index
        need(not snapshot.unknown_phases
             and all(i < index and b["state"] == "completed_unreviewed" or i == index and b["state"] == "unclaimed"
                     for i, b in snapshot.batches.items()), "unknown_or_future_batch_no_replay")
        need(set(snapshot.batches) == set(range(index + 1)), "batch_chain_gap")
        current = snapshot.batches[index]
        need(current["dispatch"] == self.dispatch and snapshot.checkpoint == self.dispatch.before_checkpoint_sha256,
             "dispatch_or_checkpoint_changed")
        if index:
            need(snapshot.batches[index - 1]["terminal"] == self.dispatch.parent_terminal, "parent_changed")

    def begin(self, journal: Path, report: dict[str, Any], *, initialize: bool,
              maximum_requests: int, context: JobContext) -> None:
        need(not self._started and maximum_requests == self.dispatch.maximum_requests, "dispatch_already_used_or_cap")
        snapshot = self._snapshot(report)
        self._unclaimed(snapshot)
        need(not list(journal.glob("durable-stop.*.json")), "local_unknown_no_replay")
        for name, raw in snapshot.files.items():
            need(e.development._read(journal, name) == raw, "local_remote_changed")
        allowed = {*snapshot.files, "lock", "intent.lock", "durable-restore.json"}
        need({p.name for p in journal.iterdir()} <= allowed, "unpublished_local_unknown")
        if initialize:
            need(self.dispatch.batch_index == 0 and len(snapshot.files) == 1, "restore_required")
        context = JobContext.model_validate(context)
        context_raw = packet.canonical(context.model_dump(mode="json"))
        claim = packet.canonical({"schema_version": VERSION, "batch_index": self.dispatch.batch_index,
            "dispatch": self.dispatch.acknowledgment.model_dump(mode="json"),
            "dispatch_sha256": packet.digest(dispatch_bytes(self.dispatch)),
            "before_checkpoint_sha256": snapshot.checkpoint,
            "parent_terminal": self.dispatch.parent_terminal.model_dump(mode="json") if self.dispatch.parent_terminal else None,
            "authorization_sha256": self._authorization_sha, "context_sha256": packet.digest(context_raw)})
        self._journal = journal
        self._claim = self._create(f"run.{self.dispatch.batch_index:02}.intent.json", claim)
        self._context = self._create(f"job.{self.dispatch.batch_index:02}.context.json", context_raw)
        self._journal, self._started = journal, True

    def before_send(self, journal: Path, key: str) -> None:
        need(self._started and journal == self._journal and key in self._rows
             and key not in self._sent and len(self._sent) < self.dispatch.maximum_requests, "phase_not_owned")
        raw = e.development._read(journal, key + ".intent.json", e.MAX_PHASE_METADATA)
        intent = e.PhaseIntent.model_validate(e._json(raw))
        need(intent.local_intent_id == key and intent.authorization_sha256 == self._authorization_sha
             and intent.reserved_minor == self.authorization.reserved_minor_per_request, "intent_changed")
        phase = self._rows[key][1]
        names = [key + suffix for suffix in (".intent.json", ".raw.json", ".retained.json", ".result.json", ".terminal.json")]
        if phase == "compile":
            names.append(self._rows[key][0]["case_id"] + ".compiled.pdf")
        # Leave the batch terminal's maximum size/count available as well.
        self._bounds(extra_objects=len(names) + 1, extra_bytes=sum(self._specs[name] for name in names) + MAX_METADATA)
        self._create(key + ".intent.json", raw)
        self._sent.append(key)

    def terminal(self, journal: Path, key: str) -> None:
        need(self._started and journal == self._journal and key in self._sent, "phase_not_owned")
        result_raw = e.development._read(journal, key + ".result.json", e.MAX_PHASE_METADATA)
        result = e.PhaseResult.model_validate(e._json(result_raw))
        names = [key + ".result.json"]
        if result.state != "refused_no_replay":
            names += [key + ".raw.json", key + ".retained.json"]
        if result.state == "captured_unreviewed" and self._rows[key][1] == "compile":
            names.append(self._rows[key][0]["case_id"] + ".compiled.pdf")
        refs = [self._create(name, e.development._read(journal, name)).model_dump(mode="json") for name in names]
        self._create(key + ".terminal.json", packet.canonical({"schema_version": VERSION, "key": key,
            "intent": self._references[key + ".intent.json"].model_dump(mode="json"), "files": refs}))

    def finish(self) -> ObjectRef:
        need(self._started and self._claim is not None and self._context is not None and self._sent, "dispatch_not_started")
        assert self._claim is not None and self._context is not None
        snapshot = self._snapshot(self._report)
        batch = snapshot.batches.get(self.dispatch.batch_index, {})
        need(not snapshot.unknown_phases and batch.get("claim") == self._claim
             and batch.get("context") == self._context and batch.get("state") == "unknown_no_replay"
             and snapshot.unowned_phase_keys == set(self._sent), "dispatch_ownership_changed")
        refs = {key: self._references[key + ".terminal.json"].model_dump(mode="json") for key in self._sent}
        return self._create(f"run.{self.dispatch.batch_index:02}.terminal.json", packet.canonical({
            "schema_version": VERSION, "state": "completed_unreviewed", "batch_index": self.dispatch.batch_index,
            "channel_binding": self.dispatch.channel_binding.model_dump(mode="json"),
            "dispatch": self.dispatch.acknowledgment.model_dump(mode="json"), "run_intent": self._claim.model_dump(mode="json"),
            "job_context": self._context.model_dump(mode="json"),
            "parent_terminal": self.dispatch.parent_terminal.model_dump(mode="json") if self.dispatch.parent_terminal else None,
            "before_checkpoint_sha256": self.dispatch.before_checkpoint_sha256,
            "after_checkpoint_sha256": snapshot.checkpoint, "phase_terminals": refs, "requests_sent": len(self._sent),
            "actual_native_cost_minor": None, "authorizes_retail": False}))

    def stop(self, journal: Path) -> None:
        # Do not create a replacement remote claim/terminal or release exposure.
        if journal == self._journal and journal.is_dir():
            name = f"durable-stop.{self.dispatch.batch_index:02}.json"
            if not (journal / name).exists():
                e.journal_new(journal, name, packet.canonical({"schema_version": VERSION,
                    "state": "unknown_no_replay", "reserved_expense_released": False}))


def restore(common: tuple[Path, Path, e.Authorization, Path], destination: Path, channel: Channel) -> dict[str, Any]:
    """Exact read-only remote reconciliation into a fresh quarantined directory."""
    need(type(channel) is Channel and destination.is_absolute() and destination.resolve() == destination, "restore_destination")
    report = e.plan(*common, require_active=False)
    snapshot = channel._snapshot(report)
    destination.mkdir(mode=0o700, parents=False, exist_ok=False)
    e.development._private(destination, directory=True)
    for name, raw in snapshot.files.items():
        e.journal_new(destination, name, raw)
    e.journal_new(destination, "durable-restore.json", packet.canonical({"schema_version": VERSION,
        "checkpoint_sha256": snapshot.checkpoint, "native_approval": False,
        "unknown_phases": snapshot.unknown_phases, "processing_authorized": False}))
    # Preserve partial response/result files as quarantined evidence. They may
    # be incomplete by definition and must not be normalized into a success.
    # A later execute still rejects the original remote claim or local gap.
    if not snapshot.unknown_phases and not any(b["state"] == "unknown_no_replay" for b in snapshot.batches.values()):
        e.verify_journal(destination, report, common[1], common[2])
    return {"checkpoint_sha256": snapshot.checkpoint, "unknown_phases": snapshot.unknown_phases,
            "batch_states": {index: row["state"] for index, row in snapshot.batches.items()},
            "processing_authorized": False, "reserved_expense_released": False}


def inspect(common: tuple[Path, Path, e.Authorization, Path], channel: Channel) -> dict[str, Any]:
    need(type(channel) is Channel, "exact_channel_required")
    snapshot = channel._snapshot(e.plan(*common, require_active=False))
    return {"checkpoint_sha256": snapshot.checkpoint, "unknown_phases": snapshot.unknown_phases,
            "batch_states": {index: row["state"] for index, row in snapshot.batches.items()},
            "object_count": len(channel._references), "output_bytes": sum(ref.size for ref in channel._references.values()),
            "processing_authorized": False, "native_approval": False, "reserved_expense_released": False,
            "actual_native_cost_minor": None}


class GcsObjects:
    """Supplied keyless client only; no listing, IAM, delete, overwrite or retry."""
    def __init__(self, client: Any, namespace: str):
        match = re.fullmatch(r"gs://([a-z0-9][a-z0-9._-]{2,221})/(quality-held-out/[a-z0-9][a-z0-9-]{7,79}/)", namespace)
        need(match is not None, "held_out_prefix_required")
        assert match is not None
        self.namespace, self._prefix = namespace, match[2]
        self._bucket = client.bucket(match[1])

    def fetch(self, name: str, *, generation: str | None = None, maximum: int) -> Object | None:
        from google.api_core.exceptions import NotFound
        need(re.fullmatch(NAME, name) and type(maximum) is int and 0 < maximum <= e.MAX_ARTIFACT, "object_bound")
        need(generation is None or re.fullmatch(r"[1-9][0-9]{0,20}", generation), "object_generation")
        blob = self._bucket.blob(self._prefix + name, generation=int(generation) if generation else None)
        try:
            blob.reload(retry=None, timeout=15)
        except NotFound:
            return None
        except Exception:
            raise e.AcquisitionDenied("held_out_channel_object_read_unavailable") from None
        try:
            need(type(blob.size) is int and 0 < blob.size <= maximum and str(blob.generation).isdigit(), "object_bound")
            raw = blob.download_as_bytes(if_generation_match=int(blob.generation), retry=None, timeout=15)
            ref = ObjectRef(name=name, generation=str(blob.generation), size=len(raw), sha256=packet.digest(raw))
            need(len(raw) == blob.size, "object_size_changed")
            return _checked(Object(ref, raw), name, maximum)
        except Exception:
            raise e.AcquisitionDenied("held_out_channel_object_read_unavailable") from None

    def create(self, name: str, raw: bytes) -> ObjectRef:
        need(re.fullmatch(NAME, name) and type(raw) is bytes and 0 < len(raw) <= e.MAX_ARTIFACT, "object_bound")
        blob = self._bucket.blob(self._prefix + name)
        try:
            blob.upload_from_string(raw, if_generation_match=0, retry=None, timeout=15, content_type="application/octet-stream")
            ref = ObjectRef(name=name, generation=str(blob.generation), size=len(raw), sha256=packet.digest(raw))
            need(blob.size == len(raw), "create_ack_changed")
            return ref
        except Exception:
            raise e.AcquisitionDenied("held_out_channel_object_create_unknown") from None
