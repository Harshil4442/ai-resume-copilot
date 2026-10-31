"""Immutable object-channel seam for original development service acquisition.

No Job dispatcher, native proof, funding grant, model or page renderer. An exact
reviewed external dispatch receipt is required; this adapter never issues one.
Object-store durability is not worker/result attestation or protection against
an administrator replacing the entire evidence prefix.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol

from pydantic import ConfigDict, Field, model_validator

from app.domains.analysis import native_input_acquisition as acquisition
from app.domains.analysis import quality_packet as packet

VERSION = "development-native-object-channel-v1"
MAX_BYTES = 512 * 1024 * 1024
MAX_OBJECTS = 1024
MAX_METADATA = 256 * 1024
NAME = r"[A-Za-z0-9._-]{1,160}"


def need(value, code):
    if not value:
        raise ValueError("native_channel_" + code)


class ObjectRef(acquisition.legacy.Closed):
    model_config = ConfigDict(revalidate_instances="always")
    name: str = Field(pattern="^" + NAME + "$")
    generation: str = Field(pattern=r"^[1-9][0-9]{0,20}$")
    size: int = Field(strict=True, ge=1, le=acquisition.MAX_ARTIFACT)
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


@dataclass(frozen=True)
class Object:
    reference: ObjectRef
    raw: bytes


class Objects(Protocol):
    namespace: str

    def fetch(self, name: str, *, generation: str | None = None, maximum: int) -> Object | None: ...
    def create(self, name: str, raw: bytes) -> ObjectRef: ...


class Binding(acquisition.legacy.Closed):
    model_config = ConfigDict(revalidate_instances="always")
    schema_version: Literal["development-native-object-channel-v1"]
    namespace: str = Field(pattern=r"^gs://[a-z0-9][a-z0-9._-]{2,221}/quality-development/[a-z0-9][a-z0-9-]{7,79}/$")
    acquisition_plan_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    channel_source_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    maximum_output_bytes: int = Field(strict=True, gt=0, le=MAX_BYTES)
    maximum_objects: int = Field(strict=True, ge=1, le=MAX_OBJECTS)


class Dispatch(acquisition.legacy.Closed):
    """Reviewed external handoff, explicitly NOT provider Job/Execution proof."""
    model_config = ConfigDict(revalidate_instances="always")
    schema_version: Literal["reviewed-native-acquisition-dispatch-v1"]
    purpose: Literal["unreviewed_service_capture_external_authority_required"]
    reviewed_by: str = Field(min_length=1, max_length=160)
    external_authority_review: acquisition.legacy.Artifact
    channel_binding: ObjectRef
    acknowledgment: ObjectRef
    before_checkpoint_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    parent_terminal: ObjectRef | None
    maximum_requests: int = Field(strict=True, ge=1, le=10)

    @model_validator(mode="after")
    def separate(self):
        need(self.channel_binding.name == "channel-binding.json"
             and self.acknowledgment.name.startswith("dispatch.")
             and self.acknowledgment.name.endswith(".json"), "dispatch_names")
        return self


def binding(report, namespace, *, maximum_output_bytes=MAX_BYTES, maximum_objects=MAX_OBJECTS):
    """Pure proposal bytes; never creates an object, receipt or provider identity."""
    return Binding(schema_version=VERSION, namespace=namespace,
        acquisition_plan_sha256=packet.digest(packet.canonical(report)),
        channel_source_sha256=packet.file_digest(Path(__file__)),
        maximum_output_bytes=maximum_output_bytes, maximum_objects=maximum_objects)


def dispatch_bytes(value: Dispatch):
    # The ACK object cannot contain its own hash/generation recursively.
    return packet.canonical(value.model_dump(mode="json", exclude={"acknowledgment"}))


def _checked(value: Object, *, name, maximum):
    need(type(value) is Object and value.reference.name == name and type(value.raw) is bytes
         and 0 < len(value.raw) <= maximum and value.reference.size == len(value.raw)
         and value.reference.sha256 == packet.digest(value.raw), "object_changed")
    return value


class Channel:
    def __init__(self, store: Objects, configuration: Binding, dispatch: Dispatch, *, authorization_root: Path,
                 authorization: acquisition.Authorization):
        configuration, dispatch = Binding.model_validate(configuration), Dispatch.model_validate(dispatch)
        self.store, self.configuration, self.dispatch = store, configuration, dispatch
        acquisition.legacy._artifact(authorization_root, dispatch.external_authority_review)
        need(store.namespace == configuration.namespace
             and configuration.namespace.endswith("/quality-development/" + authorization.experiment_id + "/"), "namespace_changed")
        self._authorization_sha = packet.digest(packet.canonical(authorization.model_dump(mode="json")))
        self._started = False
        self._references: dict[str, ObjectRef] = {}
        self._journal: Path | None = None
        self._report = None
        self._sent: list[str] = []

    def _get(self, name, *, reference=None, maximum=acquisition.MAX_ARTIFACT):
        value = self.store.fetch(name, generation=reference.generation if reference is not None else None, maximum=maximum)
        if value is None:
            need(reference is None, "acknowledged_object_missing")
            return None
        _checked(value, name=name, maximum=maximum)
        if reference is not None:
            need(value.reference == reference, "generation_or_hash_changed")
        need(name not in self._references or self._references[name] == value.reference, "object_reference_conflict")
        self._references[name] = value.reference
        self._bounds()
        return value

    def _bounds(self):
        need(len(self._references) <= self.configuration.maximum_objects
             and sum(r.size for r in self._references.values()) <= self.configuration.maximum_output_bytes,
             "output_budget")

    def _create(self, name, raw):
        need(type(raw) is bytes and 0 < len(raw) <= acquisition.MAX_ARTIFACT and re.fullmatch(NAME, name), "object_bound")
        need(name not in self._references, "create_only_conflict")
        need(len(self._references) + 1 <= self.configuration.maximum_objects
             and sum(r.size for r in self._references.values()) + len(raw) <= self.configuration.maximum_output_bytes,
             "output_budget")
        reference = ObjectRef.model_validate(self.store.create(name, raw))
        need(reference.name == name and reference.size == len(raw) and reference.sha256 == packet.digest(raw), "create_ack_changed")
        self._references[name] = reference
        return reference

    def _keys(self, report):
        return [(row["case_id"] + "." + phase, row, phase)
            for row in report["selected_cases"] for phase in row["phases"]]

    def _phase(self, key, row, phase):
        intent = self._get(key + ".intent.json", maximum=16384)
        commit = self._get(key + ".terminal.json", maximum=MAX_METADATA)
        if intent is None:
            need(commit is None, "orphan_terminal")
            return None
        if commit is None:
            return None, {intent.reference.name: intent.raw}
        value = json.loads(commit.raw)
        need(type(value) is dict and set(value) == {"version", "key", "intent", "files"}
             and value["version"] == VERSION and value["key"] == key
             and ObjectRef.model_validate(value["intent"]) == intent.reference, "terminal_changed")
        references = [ObjectRef.model_validate(ref) for ref in value["files"]]
        allowed = {key + suffix for suffix in (".raw.json", ".retained.json", ".result.json")}
        if phase == "compile":
            allowed.add(row["case_id"] + ".compiled.pdf")
        need(len(references) == len({r.name for r in references}) and {r.name for r in references} <= allowed
             and key + ".result.json" in {r.name for r in references}, "terminal_files_changed")
        files = {intent.reference.name: intent.raw}
        for ref in references:
            files[ref.name] = self._get(ref.name, reference=ref).raw
        result = json.loads(files[key + ".result.json"])
        need(result.get("state") in {"captured_unreviewed", "refused_no_replay", "invalid_response_no_replay"}, "result_state")
        expected = {key + ".result.json"}
        if result["state"] != "refused_no_replay":
            expected |= {key + ".raw.json", key + ".retained.json"}
        if result["state"] == "captured_unreviewed" and phase == "compile":
            expected.add(row["case_id"] + ".compiled.pdf")
        need({r.name for r in references} == expected, "incomplete_terminal")
        return commit.reference, files

    def _remote(self, report):
        self._references.clear()
        configured = self._get("channel-binding.json", reference=self.dispatch.channel_binding, maximum=MAX_METADATA)
        need(configured.raw == packet.canonical(self.configuration.model_dump(mode="json")), "binding_changed")
        phases, files, unknown = {}, {"plan.json": packet.canonical(report)}, []
        for key, row, phase in self._keys(report):
            captured = self._phase(key, row, phase)
            if captured is not None:
                if captured[0] is None:
                    unknown.append(key)
                else:
                    phases[key] = captured[0].model_dump(mode="json")
                files.update(captured[1])
        return packet.digest(packet.canonical(phases)), files, unknown

    def _validate_configuration(self, report):
        need(self.configuration.acquisition_plan_sha256 == packet.digest(packet.canonical(report))
             and self.configuration.channel_source_sha256 == packet.file_digest(Path(__file__))
             and report["authorization_sha256"] == self._authorization_sha, "source_or_plan_changed")

    def _parent(self, reference, checkpoint, report):
        seen: set[str] = set()
        while reference is not None:
            need(reference.name not in seen and len(seen) < report["maximum_http_requests"], "parent_chain_bound")
            seen.add(reference.name)
            prior = self._get(reference.name, reference=reference, maximum=MAX_METADATA)
            value = json.loads(prior.raw)
            expected = {"version", "state", "channel_binding", "dispatch", "dispatch_sha256", "before_checkpoint_sha256",
                "after_checkpoint_sha256", "parent_terminal", "requests_sent", "actual_native_cost_minor", "authorizes_retail"}
            need(type(value) is dict and set(value) == expected and value["version"] == VERSION
                 and value["state"] == "completed_unreviewed" and value["channel_binding"] == self.dispatch.channel_binding.model_dump(mode="json")
                 and value["after_checkpoint_sha256"] == checkpoint and type(value["requests_sent"]) is int
                 and 1 <= value["requests_sent"] <= 10 and value["actual_native_cost_minor"] is None
                 and value["authorizes_retail"] is False and re.fullmatch(r"[a-f0-9]{64}", value["before_checkpoint_sha256"])
                 and reference.name == "run." + value["before_checkpoint_sha256"] + ".terminal.json", "parent_unknown_or_changed")
            ack_ref = ObjectRef.model_validate(value["dispatch"])
            ack = self._get(ack_ref.name, reference=ack_ref, maximum=MAX_METADATA)
            need(packet.digest(ack.raw) == value["dispatch_sha256"], "parent_dispatch_changed")
            checkpoint = value["before_checkpoint_sha256"]
            reference = ObjectRef.model_validate(value["parent_terminal"]) if value["parent_terminal"] is not None else None
        need(checkpoint == packet.digest(packet.canonical({})), "parent_genesis_changed")

    def begin(self, journal, report, *, initialize, maximum_requests):
        need(not self._started and maximum_requests <= self.dispatch.maximum_requests, "dispatch_already_used_or_cap")
        self._validate_configuration(report)
        need(not any(journal.glob("durable-stop.*.json")), "local_unknown_requires_review")
        marker = packet.canonical({"version": VERSION, "binding": self.dispatch.channel_binding.model_dump(mode="json")})
        if initialize:
            need(not (journal / "durable-channel.json").exists(), "restore_required")
            acquisition._new(journal, "durable-channel.json", marker)
        else:
            need(acquisition._read(journal, "durable-channel.json") == marker, "local_binding_changed")
        self._journal, self._report = journal, report
        checkpoint, files, unknown = self._remote(report)
        need(not unknown, "unknown_no_replay")
        need(checkpoint == self.dispatch.before_checkpoint_sha256, "checkpoint_changed")
        ack = self._get(self.dispatch.acknowledgment.name, reference=self.dispatch.acknowledgment, maximum=MAX_METADATA)
        need(ack.raw == dispatch_bytes(self.dispatch), "dispatch_ack_changed")
        parent = self.dispatch.parent_terminal
        if parent is None:
            need(len(files) == 1, "parent_terminal_required")
        else:
            self._parent(parent, checkpoint, report)
        if initialize:
            need(len(files) == 1, "restore_required")
        for name, raw in files.items():
            need(acquisition._read(journal, name) == raw, "local_remote_changed")
        for key, _, _ in self._keys(report):
            for suffix in (".intent.json", ".result.json", ".retained.json", ".raw.json"):
                name = key + suffix
                need(not (journal / name).exists() or name in files, "unpublished_local_unknown")
        restored = journal / "durable-restore.json"
        if restored.exists():
            need(json.loads(acquisition._read(journal, restored.name))["blocked_dispatch_sha256"] != packet.digest(dispatch_bytes(self.dispatch)), "restored_dispatch_requires_new_authority")
        claim = packet.canonical({"version": VERSION, "dispatch_sha256": packet.digest(dispatch_bytes(self.dispatch)),
            "before_checkpoint_sha256": checkpoint})
        self._create("run." + checkpoint + ".intent.json", claim)
        self._started = True

    def before_send(self, journal, key):
        need(self._started and journal == self._journal and len(self._sent) < self.dispatch.maximum_requests, "dispatch_not_active")
        need(key in {k for k, _, _ in self._keys(self._report)} and key not in self._sent, "phase_changed")
        raw = acquisition._read(journal, key + ".intent.json", 16384)
        # An ACK must return before control can reach the original transport.
        self._create(key + ".intent.json", raw)
        self._sent.append(key)

    def terminal(self, journal, key):
        need(self._started and journal == self._journal and key in self._sent, "phase_not_owned")
        intent = self._references[key + ".intent.json"]
        raw_result = acquisition._read(journal, key + ".result.json", 16384)
        state = json.loads(raw_result)["state"]
        names = [key + ".result.json"]
        if state != "refused_no_replay":
            names += [key + ".raw.json", key + ".retained.json"]
        if state == "captured_unreviewed" and key.endswith(".compile"):
            names.append(key.removesuffix(".compile") + ".compiled.pdf")
        refs = [self._create(name, acquisition._read(journal, name)).model_dump(mode="json") for name in names]
        self._create(key + ".terminal.json", packet.canonical({"version": VERSION, "key": key,
            "intent": intent.model_dump(mode="json"), "files": refs}))

    def finish(self):
        need(self._started, "dispatch_not_started")
        checkpoint, _, unknown = self._remote(self._report)
        need(not unknown and self._sent, "unknown_or_empty_dispatch")
        raw = packet.canonical({"version": VERSION, "state": "completed_unreviewed",
            "channel_binding": self.dispatch.channel_binding.model_dump(mode="json"),
            "dispatch": self.dispatch.acknowledgment.model_dump(mode="json"),
            "dispatch_sha256": packet.digest(dispatch_bytes(self.dispatch)),
            "before_checkpoint_sha256": self.dispatch.before_checkpoint_sha256,
            "after_checkpoint_sha256": checkpoint, "requests_sent": len(self._sent),
            "parent_terminal": self.dispatch.parent_terminal.model_dump(mode="json") if self.dispatch.parent_terminal is not None else None,
            "actual_native_cost_minor": None, "authorizes_retail": False})
        return self._create("run." + self.dispatch.before_checkpoint_sha256 + ".terminal.json", raw)

    def stop(self, journal):
        if journal != self._journal:
            return
        acquisition._private(journal, directory=True)
        name = "durable-stop." + packet.digest(dispatch_bytes(self.dispatch)) + ".json"
        if not (journal / name).exists():
            acquisition._new(journal, name, packet.canonical({"version": VERSION,
                "state": "unknown_no_replay", "reserved_expense_released": False}))


def execute(common: tuple[Path, Path, acquisition.Authorization, Path], journal, channel, *, initialize=False, maximum_requests=1, transport=None):
    """Requires external reviewed ACK; no automatic retry or authority issuance."""
    try:
        result = acquisition.execute(*common, journal, initialize=initialize, maximum_requests=maximum_requests,
            transport=transport, channel=channel)
        terminal = channel.finish()
        return {"status": result, "terminal": terminal.model_dump(mode="json"), "native_approval": "pending"}
    except BaseException:
        channel.stop(journal)
        raise ValueError("native_channel_unknown_or_refused_no_replay") from None


def restore(common: tuple[Path, Path, acquisition.Authorization, Path], destination, channel):
    """Read-only object reconciliation into a NEW private, still-quarantined copy."""
    report = acquisition.plan(*common)
    channel._validate_configuration(report)
    checkpoint, files, unknown = channel._remote(report)
    need(str(destination.absolute()) == str(destination.resolve()), "exact_private_destination_required")
    destination.mkdir(mode=0o700, parents=False, exist_ok=False)
    acquisition._private(destination, directory=True)
    for name, raw in files.items():
        acquisition._new(destination, name, raw)
    acquisition._new(destination, "durable-channel.json", packet.canonical({"version": VERSION,
        "binding": channel.dispatch.channel_binding.model_dump(mode="json")}))
    acquisition._new(destination, "durable-restore.json", packet.canonical({"version": VERSION,
        "checkpoint_sha256": checkpoint, "blocked_dispatch_sha256": packet.digest(dispatch_bytes(channel.dispatch)),
        "requires_new_reviewed_external_dispatch": True}))
    acquisition._verify_journal(destination, report, common[1], common[2])
    return {"checkpoint_sha256": checkpoint, "execution_authorized": False,
        "unknown_phases": unknown, "native_review": "pending", "authorizes_retail": False}


def inspect(common: tuple[Path, Path, acquisition.Authorization, Path], channel):
    """Exact-object reads only; no missing-object inference grants execution."""
    report = acquisition.plan(*common)
    channel._validate_configuration(report)
    checkpoint, files, unknown = channel._remote(report)
    return {"checkpoint_sha256": checkpoint, "files": sorted(files), "unknown_phases": unknown,
        "execution_authorized": False, "native_review": "pending", "actual_native_cost_minor": None}


def export_unreviewed(common: tuple[Path, Path, acquisition.Authorization, Path], journal, destination, channel):
    report = acquisition.plan(*common)
    channel._validate_configuration(report)
    _, files, _ = channel._remote(report)
    for name, raw in files.items():
        need(acquisition._read(journal, name) == raw, "local_remote_changed")
    return acquisition.export_unreviewed(*common, journal, destination)


class GcsObjects:
    """Exact-prefix SDK port; constructing it receives an already supplied client.

    No ADC, client construction, listing, IAM, delete or overwrite is performed.
    Use only after native bucket/principal/prefix/dispatch review; not at import.
    """
    def __init__(self, client, namespace):
        match = re.fullmatch(r"gs://([a-z0-9][a-z0-9._-]{2,221})/(quality-development/[a-z0-9][a-z0-9-]{7,79}/)", namespace)
        need(match is not None, "private_prefix_required")
        assert match is not None
        self.namespace = namespace
        self._bucket = client.bucket(match[1])
        self._prefix = match[2]

    def fetch(self, name, *, generation=None, maximum):
        from google.api_core.exceptions import NotFound
        need(re.fullmatch(NAME, name) and 0 < maximum <= acquisition.MAX_ARTIFACT, "object_bound")
        blob = self._bucket.blob(self._prefix + name, generation=int(generation) if generation is not None else None)
        try:
            blob.reload(retry=None, timeout=15)
        except NotFound:
            return None
        except Exception:
            raise ValueError("native_channel_object_read_unavailable") from None
        try:
            need(type(blob.size) is int and 0 < blob.size <= maximum and str(blob.generation).isdigit(), "object_bound")
            raw = blob.download_as_bytes(if_generation_match=int(blob.generation), retry=None, timeout=15)
            need(type(raw) is bytes and len(raw) == blob.size, "object_size_changed")
            reference = ObjectRef(name=name, generation=str(blob.generation), size=len(raw), sha256=packet.digest(raw))
            return _checked(Object(reference, raw), name=name, maximum=maximum)
        except Exception:
            raise ValueError("native_channel_object_read_unavailable") from None

    def create(self, name, raw):
        need(re.fullmatch(NAME, name) and type(raw) is bytes and 0 < len(raw) <= acquisition.MAX_ARTIFACT, "object_bound")
        blob = self._bucket.blob(self._prefix + name)
        try:
            blob.upload_from_string(raw, if_generation_match=0, retry=None, timeout=15, content_type="application/octet-stream")
            need(str(blob.generation).isdigit() and blob.size == len(raw), "create_ack_changed")
            return ObjectRef(name=name, generation=str(blob.generation), size=len(raw), sha256=packet.digest(raw))
        except Exception:
            raise ValueError("native_channel_object_create_unknown") from None
