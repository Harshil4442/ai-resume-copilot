"""Owned toy object/HTTP ports; no provider, frozen packet or native durability proof."""
from __future__ import annotations

import base64
import copy
import json
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from app.domains.analysis import quality_packet as packet
from evaluation.native_acquisition_v2 import channel as c
from evaluation.native_acquisition_v2 import engine as e
from evaluation.native_acquisition_v2 import isolation as i
from evaluation.native_acquisition_v2 import job as j
from tests.test_native_acquisition_v2_engine import heldout_toys as heldout_toys  # noqa: F401
from tests.test_native_acquisition_v2_engine import private_write
from tests.test_native_acquisition_v2_engine import reviewed_toys as reviewed_toys  # noqa: F401
from tests.test_native_acquisition_v2_engine import transport_toys as transport_toys  # noqa: F401


class ToyObjects:
    def __init__(self, namespace):
        self.namespace, self.data, self.calls = namespace, {}, []
        self.lock, self.counter = threading.Lock(), 0
        self.lose_ack = None

    def fetch(self, name, *, generation=None, maximum):
        with self.lock:
            self.calls.append(("get", name, generation))
            value = self.data.get(name)
            if value is None or generation is not None and generation != value.reference.generation:
                return None
            assert len(value.raw) <= maximum
            return value

    def create(self, name, raw):
        with self.lock:
            self.calls.append(("create", name))
            if name in self.data:
                raise FileExistsError("TOY-CREATE-ONLY")
            self.counter += 1
            ref = c.ObjectRef(name=name, generation=str(self.counter), size=len(raw), sha256=packet.digest(raw))
            self.data[name] = c.Object(ref, raw)
            if self.lose_ack == name:
                self.lose_ack = None
                raise TimeoutError("TOY-PRIVATE-LOST-ACK")
            return ref


@pytest.fixture
def capture(heldout_toys):  # noqa: F811
    t = heldout_toys
    t.events.clear()
    report = e.plan(t.packet, t.native, t.auth, t.root)
    store = ToyObjects("gs://owned-toy-private-bucket/quality-held-out/" + t.auth.experiment_id + "/")
    configuration = c.binding(report, store.namespace)
    review = e.Artifact.model_validate(private_write(t.root, "dispatch-review.json", {
        "synthetic_only": "not provider attestation, funding or native approval"}))
    store.create("acquisition-plan.json", packet.canonical(report))
    binding_ref = store.create("channel-binding.json", packet.canonical(configuration.model_dump(mode="json")))
    context = c.JobContext(schema_version="held-out-native-job-context-v2", job="hirewiz-quality-held-out-toy-00000000",
        execution="hirewiz-quality-held-out-toy-00000000-exec1", task_index=0, task_attempt=0, task_count=1,
        custody="environment_observation_not_provider_attestation")

    def issue(index, parent=None):
        before = (json.loads(store.data[parent.name].raw)["after_checkpoint_sha256"] if parent else
                  packet.digest(packet.canonical({})))
        placeholder = c.ObjectRef(name=f"dispatch.{index:02}.json", generation="1", size=1, sha256="0" * 64)
        dispatch = c.Dispatch(schema_version="reviewed-held-out-native-dispatch-v2",
            purpose="unreviewed_held_out_capture_external_authority_required", reviewed_by="toy-only-not-authority",
            external_authority_review=review, channel_binding=binding_ref, acknowledgment=placeholder,
            batch_index=index, before_checkpoint_sha256=before, parent_terminal=parent,
            maximum_requests=c.batch_limit(index))
        ack = store.create(placeholder.name, c.dispatch_bytes(dispatch))
        return dispatch.model_copy(update={"acknowledgment": ack})

    by_hash = {row["source"]["sha256"]: row for row in report["selected_cases"]}

    class ToyTransport:
        def __init__(self):
            self.calls = []
            self.pdfs = {}
            self.failure = None

        def _claimed(self):
            assert any(name.endswith(".context.json") for name in store.data)
            assert any(name.endswith(".intent.json") and name.startswith("synth-") for name in store.data)

        def inspect(self, binding, source, kind):
            self._claimed()
            self.calls.append(("inspect", packet.digest(source), kind))
            if self.failure:
                raise self.failure
            row = by_hash.get(packet.digest(source)) or self.pdfs[packet.digest(source)]
            text = "" if kind in {"tex", "texzip"} else "Owned toy " + str(int(row["case_id"].split("-")[1]))
            return packet.canonical({"version": 1, "sha256": packet.digest(source), "size_bytes": len(source),
                "source_format": kind, "worker_image": binding.image, "policy_sha256": binding.policy,
                "scan": {"engine": "ClamAV", "version": "toy", "definitions": "toy-not-native"},
                "parsed": {"raw_text": text, "sections": {}, "skills": [], "experience_years": 0, "contact_info": {}}})

        def compile(self, binding, source, kind):
            self._claimed()
            self.calls.append(("compile", packet.digest(source), kind))
            row = by_hash[packet.digest(source)]
            pdf = b"%PDF-1.4\nOWNED-TOY-COMPILED " + row["case_id"].encode()
            self.pdfs[packet.digest(pdf)] = row
            return packet.canonical({"protocol": "hirewiz-tex-v1", "pdf": base64.b64encode(pdf).decode(),
                "sha256": packet.digest(pdf), "size": len(pdf), "renderer": "pdftex-no-shell-escape-v1",
                "request_sha256": packet.digest(e.native.compile_request(source, kind)),
                "renderer_image": binding.image, "isolation_policy_sha256": binding.policy})

    return SimpleNamespace(**vars(t), report=report, store=store, configuration=configuration, context=context,
                           issue=issue, transport=ToyTransport(), first=issue(0))


def adapter(t, dispatch=None):
    return c.Channel(t.store, t.configuration, dispatch or t.first, authorization_root=t.root, authorization=t.auth)


def execute(t, dispatch=None, *, initialize=False, channel=None):
    t.events.clear()
    dispatch = dispatch or t.first
    return e.execute(t.packet, t.native, t.auth, t.root, Path(t.auth.journal_path),
        initialize=initialize, maximum_requests=dispatch.maximum_requests,
        channel=channel or adapter(t, dispatch), context=t.context, transport=t.transport)


def reconcile(t, dispatch=None):
    t.events.clear()
    return c.inspect((t.packet, t.native, t.auth, t.root), adapter(t, dispatch))


def restore(t, dispatch):
    t.events.clear()
    return c.restore((t.packet, t.native, t.auth, t.root), Path(t.auth.journal_path), adapter(t, dispatch))


def test_claim_and_context_ack_precede_one_native_transport_and_terminal(capture):
    t = capture
    result = execute(t, initialize=True)
    assert result["requests_sent"] == len(t.transport.calls) == 1
    assert result["status"]["native_approval"] is result["status"]["processing_authorized"] is False
    names = [row[1] for row in t.store.calls if row[0] == "create"]
    assert names.index("run.00.intent.json") < names.index("job.00.context.json") < names.index("synth-040.source.intent.json")
    assert names.index("synth-040.source.intent.json") < names.index("synth-040.source.raw.json") < names.index("run.00.terminal.json")
    state = reconcile(t)
    assert state["batch_states"] == {0: "completed_unreviewed"}
    assert not state["reserved_expense_released"] and state["actual_native_cost_minor"] is None


def test_new_ephemeral_directory_restores_completed_bytes_without_replaying(capture):
    t = capture
    first = execute(t, initialize=True)
    old = Path(t.auth.journal_path)
    old.rename(old.with_name("retained-first-job"))
    dispatch = t.issue(1, c.ObjectRef.model_validate(first["terminal"]))
    before = {name: value for name, value in t.store.data.items() if name.endswith(".raw.json")}
    assert not restore(t, dispatch)["processing_authorized"]
    second = execute(t, dispatch)
    assert second["requests_sent"] == 10 and len(t.transport.calls) == 11
    assert all(t.store.data[name] == value for name, value in before.items())
    assert reconcile(t, dispatch)["batch_states"] == {0: "completed_unreviewed", 1: "completed_unreviewed"}


@pytest.mark.parametrize("lost", ["native_response", "run.00.intent.json", "job.00.context.json",
    "synth-040.source.intent.json", "synth-040.source.raw.json", "synth-040.source.terminal.json", "run.00.terminal.json"])
def test_lost_ack_or_response_never_authorizes_a_fresh_directory_replay(capture, lost):
    t = capture
    if lost == "native_response":
        t.transport.failure = TimeoutError("TOY-PRIVATE-PROVIDER-ERROR")
    else:
        t.store.lose_ack = lost
    with pytest.raises(ValueError, match="no_replay"):
        execute(t, initialize=True)
    sent = len(t.transport.calls)
    journal = Path(t.auth.journal_path)
    assert "TOY-PRIVATE" not in "".join(p.read_text() for p in journal.iterdir() if p.suffix == ".json")
    journal.rename(journal.with_name("retained-unknown-job"))
    t.transport.failure = None
    restore(t, t.first)
    with pytest.raises(ValueError, match="no_replay"):
        execute(t)
    assert len(t.transport.calls) == sent
    state = reconcile(t)
    assert not state["reserved_expense_released"] and state["actual_native_cost_minor"] is None


@pytest.mark.parametrize("name", ["run.00.intent.json", "job.00.context.json", "dispatch.00.json"])
def test_missing_old_context_claim_or_dispatch_refuses_before_further_native_call(capture, name):
    t = capture
    result = execute(t, initialize=True)
    t.store.data.pop(name)
    dispatch = t.issue(1, c.ObjectRef.model_validate(result["terminal"]))
    old = Path(t.auth.journal_path)
    old.rename(old.with_name("retained-tamper"))
    with pytest.raises(ValueError):
        restore(t, dispatch)
    assert len(t.transport.calls) == 1


def test_all33_owned_batches_capture320_once_with_exact1814_object_inventory(capture):
    t = capture
    parent = None
    counts = []
    for index in range(33):
        dispatch = t.first if index == 0 else t.issue(index, parent)
        if index:
            previous = Path(t.auth.journal_path)
            previous.rename(previous.with_name(f"retained-batch-{index - 1}"))
            restore(t, dispatch)
        result = execute(t, dispatch, initialize=index == 0)
        parent = c.ObjectRef.model_validate(result["terminal"])
        counts.append(result["requests_sent"])
    assert counts == [1, *([10] * 31), 9]
    assert len(t.transport.calls) == 320 and len(set(t.transport.calls)) == 320
    assert len(t.store.data) == 1814
    state = reconcile(t, dispatch)
    assert state["object_count"] == 1814 and len(state["batch_states"]) == 33
    assert not state["unknown_phases"] and state["output_bytes"] <= c.MAX_BYTES
    assert not state["native_approval"] and not state["processing_authorized"]


def test_two_source_replicas_compete_for_one_remote_claim(capture, tmp_path):
    t = capture
    directories = [tmp_path / "replica-a", tmp_path / "replica-b"]
    for directory in directories:
        directory.mkdir(mode=0o700)
        e.journal_new(directory, "plan.json", packet.canonical(t.report))
    barrier = threading.Barrier(2)

    def claim(directory):
        channel = adapter(t)
        barrier.wait()
        try:
            channel.begin(directory, t.report, initialize=True, maximum_requests=1, context=t.context)
            return True
        except (ValueError, OSError):
            return False

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(claim, directories))
    assert outcomes.count(True) == 1
    assert not t.transport.calls
    assert len([name for name in t.store.data if name == "run.00.intent.json"]) == 1


def test_granular_object_caps_and_whole_envelope_are_not_generic_metadata_multiplication(capture):
    channel = adapter(capture)
    channel._prepare(capture.report)
    assert len(channel._specs) == 1814
    assert sum(channel._specs.values()) == int(1968.5 * 1024**2) <= c.MAX_BYTES
    assert channel._specs["synth-040.source.intent.json"] == 16 * 1024
    assert channel._specs["synth-120.compile.raw.json"] == 15 * 1024**2
    assert channel._specs["synth-120.compiled.pdf"] == 5 * 1024**2
    with pytest.raises(ValueError, match="object_bound"):
        channel._create("synth-040.source.intent.json", b"x" * (16 * 1024 + 1))
    with pytest.raises(ValueError, match="object_bound"):
        channel._create("extra.json", b"{}")


def test_lower_output_budget_refuses_before_transport_and_keeps_unknown_claim(capture):
    t = capture
    t.configuration = c.binding(t.report, t.store.namespace, maximum_output_bytes=1024 * 1024)
    old_ref = t.first.channel_binding
    raw = packet.canonical(t.configuration.model_dump(mode="json"))
    ref = c.ObjectRef(name=old_ref.name, generation=old_ref.generation, size=len(raw), sha256=packet.digest(raw))
    t.store.data[ref.name] = c.Object(ref, raw)
    # Explicit fake metadata reissue; no provider credential or native approval.
    t.first = t.first.model_copy(update={"channel_binding": ref})
    raw = c.dispatch_bytes(t.first)
    ack = c.ObjectRef(name=t.first.acknowledgment.name, generation=t.first.acknowledgment.generation,
                      size=len(raw), sha256=packet.digest(raw))
    t.store.data[ack.name] = c.Object(ack, raw)
    t.first = t.first.model_copy(update={"acknowledgment": ack})
    with pytest.raises(ValueError, match="no_replay"):
        execute(t, initialize=True)
    assert not t.transport.calls
    assert "run.00.intent.json" in t.store.data and "synth-040.source.raw.json" not in t.store.data


def test_partial_local_restore_is_not_overwritten_or_native_authority(capture, monkeypatch):
    t = capture
    first = execute(t, initialize=True)
    old = Path(t.auth.journal_path)
    old.rename(old.with_name("retained-complete"))
    dispatch = t.issue(1, c.ObjectRef.model_validate(first["terminal"]))
    original, writes = e.journal_new, []

    def partial(root, name, raw):
        writes.append(name)
        if len(writes) == 3:
            raise OSError("TOY-DISK-FAILURE")
        return original(root, name, raw)

    with monkeypatch.context() as patch:
        patch.setattr(e, "journal_new", partial)
        with pytest.raises(OSError):
            restore(t, dispatch)
    before = {p.name: p.read_bytes() for p in old.iterdir()}
    with pytest.raises(FileExistsError):
        restore(t, dispatch)
    with pytest.raises(ValueError):
        execute(t, dispatch)
    assert len(t.transport.calls) == 1
    assert all((old / name).read_bytes() == raw for name, raw in before.items())


@pytest.mark.parametrize("tag", ["development-native-object-channel-v1", "native-quality-isolation-v1"])
def test_old_protocol_tag_cannot_be_used_as_v2(capture, tag):
    with pytest.raises(ValueError):
        c.Binding.model_validate(capture.configuration.model_dump() | {"schema_version": tag})


def test_expired_permit_can_only_reconcile_without_another_native_send(capture, monkeypatch):
    t = capture
    execute(t, initialize=True)

    class Later(datetime):
        @classmethod
        def now(cls, tz=None):
            return t.auth.expires_at + timedelta(seconds=1)

    monkeypatch.setattr(e, "datetime", Later)
    state = reconcile(t)
    assert state["batch_states"] == {0: "completed_unreviewed"}
    with pytest.raises(ValueError):
        execute(t)
    assert len(t.transport.calls) == 1 and not state["processing_authorized"]


def test_sdk_port_uses_generation_match_no_retry_and_no_create_retry():
    calls = []

    class Blob:
        generation, size = "17", 2

        def reload(self, **kwargs):
            calls.append(("reload", kwargs))

        def download_as_bytes(self, **kwargs):
            calls.append(("download", kwargs))
            return b"{}"

        def upload_from_string(self, raw, **kwargs):
            calls.append(("upload", kwargs))
            raise TimeoutError("TOY-LOST-ACK")

    class Bucket:
        def blob(self, name, **kwargs):
            calls.append((name, kwargs))
            return Blob()

    class Client:
        def bucket(self, name):
            calls.append(("bucket", name))
            return Bucket()

    store = c.GcsObjects(Client(), "gs://owned-toy-private-bucket/quality-held-out/owned-toy-exp/")
    value = store.fetch("object.json", generation="17", maximum=2)
    assert value is not None
    assert value.raw == b"{}" and value.reference.generation == "17"
    assert ("download", {"if_generation_match": 17, "retry": None, "timeout": 15}) in calls
    with pytest.raises(ValueError, match="create_unknown"):
        store.create("new.json", b"{}")
    uploads = [value for kind, value in calls if kind == "upload"]
    assert len(uploads) == 1 and uploads[0]["if_generation_match"] == 0 and uploads[0]["retry"] is None
    with pytest.raises(ValueError, match="held_out_prefix"):
        c.GcsObjects(Client(), "gs://owned-toy-private-bucket/quality-development/owned-toy-exp/")


# Job entry controls share the explicit owned channel fixtures above.
@pytest.fixture
def job_task(capture, tmp_path):  # noqa: F811
    t = capture
    workspace = tmp_path / "job-work"
    t.auth = e.Authorization.model_validate(t.auth.model_dump() | {"journal_path": str(workspace / "journal")})
    t.events.clear()
    report = e.plan(t.packet, t.native, t.auth, t.root)
    t.store.data.clear()  # Explicit disposable fake, not provider object deletion.
    configuration = c.binding(report, t.store.namespace)
    t.store.create("acquisition-plan.json", e.packet.canonical(report))
    binding_ref = t.store.create("channel-binding.json", e.packet.canonical(configuration.model_dump(mode="json")))
    review = e.Artifact(path="dispatch-review.json", sha256=e.packet.file_digest(t.root / "dispatch-review.json"))
    placeholder = c.ObjectRef(name="dispatch.00.json", generation="1", size=1, sha256="0" * 64)
    dispatch = c.Dispatch(schema_version="reviewed-held-out-native-dispatch-v2",
        purpose="unreviewed_held_out_capture_external_authority_required", reviewed_by="toy-not-authority",
        external_authority_review=review, channel_binding=binding_ref, acknowledgment=placeholder,
        batch_index=0, before_checkpoint_sha256=e.packet.digest(e.packet.canonical({})), parent_terminal=None,
        maximum_requests=1)
    ack = t.store.create(placeholder.name, c.dispatch_bytes(dispatch))
    dispatch = dispatch.model_copy(update={"acknowledgment": ack})
    runner = e.Artifact.model_validate(private_write(t.root, "heldout-runner-funding.json", {
        "synthetic_only": "not a funding grant"}))

    def inputs(root, rows, prefix, *, authority=False):
        result = []
        for index, (name, kind) in enumerate(sorted(rows.items())):
            ref = t.store.create(f"input.{prefix}-{index}", (root / name).read_bytes())
            row = {"path": name, "object": ref}
            if authority:
                row["kind"] = kind
            result.append(row)
        return result

    authority = {row["path"]: row["kind"] for row in t.lock["files"]}
    for artifact in (t.auth.native_development_lock, t.auth.funding_evidence, runner, review):
        authority[artifact.path] = "control"
    native = {"materialization-manifest.json": "source", **{row["source"]["path"]: "source" for row in report["selected_cases"]}}
    bundle = j.Bundle(schema_version=j.VERSION, purpose="unreviewed_held_out_capture_no_model_no_retail",
        entry_sha256=e.packet.file_digest(Path(j.__file__)), job_name=t.context.job, configuration=configuration,
        authorization=t.auth, dispatch=dispatch, runner_funding_evidence=runner,
        native_files=inputs(t.native, native, "native"), authority_files=inputs(t.root, authority, "authority", authority=True))
    environment = {"CLOUD_RUN_JOB": bundle.job_name, "CLOUD_RUN_EXECUTION": bundle.job_name + "-exec1",
        "CLOUD_RUN_TASK_INDEX": "0", "CLOUD_RUN_TASK_ATTEMPT": "0", "CLOUD_RUN_TASK_COUNT": "1",
        j.NO_BUCKET_METADATA: "true"}
    return SimpleNamespace(**vars(t), workspace=workspace, bundle=bundle, environment=environment)


def run_job(t, *, bundle=None):
    t.events.clear()
    return j.run(bundle or t.bundle, t.store, t.packet, t.workspace, environment=t.environment, transport=t.transport)


def test_runtime_entry_validates_native40_before_fetching_any_heldout_source(job_task, monkeypatch):
    t = job_task
    original_verify, original_fetch, gate = e.verify_native_development_lock, t.store.fetch, []
    native_names = {row.object.name for row in t.bundle.native_files}

    def verify(*args, **kwargs):
        result = original_verify(*args, **kwargs)
        gate.append("complete-native40")
        return result

    def fetch(name, **kwargs):
        if name in native_names:
            assert gate
        return original_fetch(name, **kwargs)

    monkeypatch.setattr(e, "verify_native_development_lock", verify)
    t.store.fetch = fetch
    result = run_job(t)
    assert gate and result["requests_sent"] == len(t.transport.calls) == 1
    assert result["state"] == "completed_unreviewed" and not result["native_approval"]
    assert not result["processing_authorized"] and not result["reserved_expense_released"]
    assert len(e.packet.canonical(result)) < 16 * 1024
    assert len(list((t.workspace / "native").rglob("*.*"))) == 161
    with pytest.raises(FileExistsError):
        run_job(t)
    assert len(t.transport.calls) == 1


@pytest.mark.parametrize("change", ["missing_lock", "tampered_lock", "extra_authority", "wrong_kind"])
def test_invalid_review_closure_never_fetches_heldout_sources_or_reaches_transport(job_task, change):
    t = job_task
    data = t.bundle.model_dump()
    if change == "missing_lock":
        data["authority_files"] = [row for row in data["authority_files"] if row["path"] != t.auth.native_development_lock.path]
    elif change == "tampered_lock":
        row = next(row for row in t.bundle.authority_files if row.path == t.auth.native_development_lock.path)
        t.store.data.pop(row.object.name)
    elif change == "wrong_kind":
        data["authority_files"][0]["kind"] = "page"
    else:
        ref = t.store.create("input.extra-authority", b"{}")
        data["authority_files"].append({"path": "unrelated.json", "object": ref.model_dump(), "kind": "control"})
    native_names = {row.object.name for row in t.bundle.native_files}
    before = len(t.store.calls)
    with pytest.raises((ValueError, OSError)):
        run_job(t, bundle=data)
    assert not [row for row in t.store.calls[before:] if row[0] == "get" and row[1] in native_names]
    assert not t.transport.calls


@pytest.mark.parametrize("key,value", [("CLOUD_RUN_TASK_ATTEMPT", "1"), ("CLOUD_RUN_TASK_INDEX", "1"),
    ("CLOUD_RUN_TASK_COUNT", "2"), ("CLOUD_RUN_JOB", "unrelated-job"), ("CLOUD_RUN_EXECUTION", "unrelated-execution"),
    ("GOOGLE_APPLICATION_CREDENTIALS", "/tmp/never-read"), ("DATABASE_URL", "not-a-url"), ("GEMINI_API_KEY", "toy"),
    ("GOOGLE_CLOUD_PROJECT", "other-project"), ("HTTPS_PROXY", "https://unrelated.invalid"),
    ("GCE_METADATA_HOST", "unrelated.invalid"), ("STORAGE_EMULATOR_HOST", "http://unrelated.invalid"),
    ("API_ENDPOINT_OVERRIDE", "https://unrelated.invalid"), (j.NO_BUCKET_METADATA, "false"),
    ("OTEL_EXPORTER_OTLP_ENDPOINT", "https://unrelated.invalid"), ("ENABLE_GCS_PYTHON_CLIENT_OTEL_TRACES", "true"),
    ("SSLKEYLOGFILE", "/tmp/never-write"), ("PYTHONPATH", "/tmp/never-import"), ("LD_PRELOAD", "/tmp/never-load")])
def test_wrong_task_credentials_or_redirects_stop_before_any_fetch(job_task, key, value):
    t = job_task
    t.environment[key] = value
    before = list(t.store.calls)
    with pytest.raises(ValueError):
        run_job(t)
    assert t.store.calls == before and not t.workspace.exists() and not t.transport.calls


@pytest.mark.parametrize("path", ["../escape", "nested/../escape", "/absolute", "./alias"])
def test_escaped_input_paths_are_not_created(job_task, path):
    data = job_task.bundle.model_dump()
    data["authority_files"][0]["path"] = path
    with pytest.raises(ValueError):
        run_job(job_task, bundle=data)
    assert not job_task.workspace.exists()


@pytest.mark.parametrize("change", ["missing_native", "duplicate_native", "native_oversize", "wrong_entry", "combined_size"])
def test_exact_native_count_source_and_byte_bounds(job_task, change):
    t = job_task
    data = t.bundle.model_dump()
    if change == "missing_native":
        data["native_files"].pop()
    elif change == "duplicate_native":
        data["native_files"][-1] = copy.deepcopy(data["native_files"][0])
    elif change == "native_oversize":
        row = next(row for row in data["native_files"] if row["path"] != "materialization-manifest.json")
        row["object"]["size"] = e.inspection.MAX_SOURCE_BYTES + 1
    elif change == "wrong_entry":
        data["entry_sha256"] = "0" * 64
    else:
        # Each row remains independently bounded; their declared aggregate is
        # not allowed to silently fit by omitting a required source or review.
        for row in data["authority_files"]:
            row["object"]["size"] = e.KINDS[row["kind"]][1]
        for row in data["native_files"]:
            row["object"]["size"] = e.MAX_METADATA if row["path"] == "materialization-manifest.json" else e.inspection.MAX_SOURCE_BYTES
    before = list(t.store.calls)
    with pytest.raises(ValueError):
        run_job(t, bundle=data)
    assert t.store.calls == before and not t.workspace.exists() and not t.transport.calls


@pytest.mark.parametrize("change", ["path", "sha256"])
def test_job_matches_selected_manifest_references_before_fetching_original_sources(job_task, change):
    t = job_task
    data = t.bundle.model_dump()
    row = next(row for row in data["native_files"] if row["path"] != "materialization-manifest.json")
    if change == "path":
        row["path"] = "unrelated-source.pdf"
    else:
        row["object"]["sha256"] = "0" * 64
    originals = {row.object.name for row in t.bundle.native_files if row.path != "materialization-manifest.json"}
    before = len(t.store.calls)
    with pytest.raises(ValueError, match="exact_held_out_sources"):
        run_job(t, bundle=data)
    assert not [call for call in t.store.calls[before:] if call[0] == "get" and call[1] in originals]
    assert not t.transport.calls


@pytest.mark.parametrize("lost", ["run.00.intent.json", "synth-040.source.intent.json"])
def test_job_lost_ack_cannot_replay_from_fresh_workspace(job_task, lost):
    t = job_task
    t.store.lose_ack = lost
    with pytest.raises(ValueError, match="no_replay"):
        run_job(t)
    assert not t.transport.calls
    t.workspace.rename(t.workspace.with_name("retained-unknown-job-work"))
    with pytest.raises(ValueError, match="no_replay"):
        run_job(t)
    assert not t.transport.calls and "run.00.terminal.json" not in t.store.data


def test_job_later_batch_restores_parent_without_replaying_completed_phase(job_task):
    t = job_task
    first = run_job(t)
    parent = c.ObjectRef.model_validate(first["terminal"])
    terminal = e._json(t.store.data[parent.name].raw)
    dispatch = t.bundle.dispatch.model_copy(update={"batch_index": 1, "maximum_requests": 10,
        "parent_terminal": parent, "before_checkpoint_sha256": terminal["after_checkpoint_sha256"],
        "acknowledgment": c.ObjectRef(name="dispatch.01.json", generation="1", size=1, sha256="0" * 64)})
    ack = t.store.create("dispatch.01.json", c.dispatch_bytes(dispatch))
    dispatch = c.Dispatch.model_validate(dispatch.model_copy(update={"acknowledgment": ack}))
    t.bundle = t.bundle.model_copy(update={"dispatch": dispatch})
    t.environment["CLOUD_RUN_EXECUTION"] = t.bundle.job_name + "-exec2"
    before = {name: value for name, value in t.store.data.items() if name.endswith(".raw.json")}
    t.workspace.rename(t.workspace.with_name("retained-completed-first-job"))
    second = run_job(t)
    assert second["requests_sent"] == 10 and len(t.transport.calls) == 11
    assert all(t.store.data[name] == value for name, value in before.items())
    assert {"run.00.intent.json", "job.00.context.json", "run.01.intent.json", "job.01.context.json"} <= set(t.store.data)
    assert not second["reserved_expense_released"] and second["actual_native_cost_minor"] is None


def test_keyless_factory_disables_auth_refresh_http_retry_proxy_and_redirects(monkeypatch):
    import requests
    from google.auth import compute_engine
    from google.auth.transport import requests as auth_requests
    from google.cloud import storage

    calls: list[tuple[str, Any]] = []

    class FakeSession:
        def __init__(self, *args, **kwargs):
            calls.append(("session", kwargs))
            self.mounts = []

        def mount(self, prefix, adapter):
            self.mounts.append((prefix, adapter.max_retries.total))

        def close(self):
            calls.append(("closed", self))

    class FakeClient:
        def __init__(self, **kwargs):
            calls.append(("client", kwargs))
            self.http = kwargs["_http"]

        def bucket(self, name):
            return SimpleNamespace(name=name)

        def close(self):
            calls.append(("client_closed", self))

    def metadata_credentials(**kwargs):
        calls.append(("credentials", kwargs))
        return "toy-metadata-identity-not-a-key"

    monkeypatch.setattr(compute_engine, "Credentials", metadata_credentials)
    monkeypatch.setattr(requests, "Session", FakeSession)
    monkeypatch.setattr(auth_requests, "AuthorizedSession", FakeSession)
    monkeypatch.setattr(storage, "Client", FakeClient)
    monkeypatch.setenv(j.NO_BUCKET_METADATA, "true")
    with j.keyless_store("gs://owned-toy-private-bucket/quality-held-out/owned-toy-exp/") as store:
        assert store.namespace.endswith("/quality-held-out/owned-toy-exp/")
        session_args = next(value for kind, value in calls if kind == "session" and "max_refresh_attempts" in value)
        assert session_args["max_refresh_attempts"] == 0
        assert next(value for kind, value in calls if kind == "credentials") == {"universe_domain": "googleapis.com"}
        http = next(value for kind, value in calls if kind == "client")["_http"]
        assert http.trust_env is False and http.max_redirects == 0
        assert http.mounts == [("https://", 0), ("http://", 0)]
        assert next(value for kind, value in calls if kind == "client")["client_options"] == {
            "api_endpoint": "https://storage.googleapis.com", "universe_domain": "googleapis.com"}
    assert len([row for row in calls if row[0] == "closed"]) == 2
    assert len([row for row in calls if row[0] == "client_closed"]) == 1


@pytest.mark.parametrize("status", [401, 403, 429, 500, 503, 307, 308])
def test_real_storage_sdk_error_or_redirect_does_not_replay_create(monkeypatch, status):
    from google.auth import compute_engine, credentials
    from requests import Response, adapters

    wire = []

    class OwnedAdapter(adapters.HTTPAdapter):
        def send(self, request, **kwargs):
            wire.append((request.method, request.url, kwargs["timeout"]))
            response = Response()
            response.status_code, response.request, response.url = status, request, request.url
            response.headers["Content-Type"] = "application/json"
            response.headers["Location"] = "https://unrelated.invalid/never-follow"
            response._content = b'{"error":{"message":"TOY-PRIVATE-SDK-ERROR","code":500}}'
            return response

    monkeypatch.setattr(compute_engine, "Credentials", lambda **kwargs: credentials.AnonymousCredentials())
    monkeypatch.setattr(adapters, "HTTPAdapter", OwnedAdapter)
    monkeypatch.setenv(j.NO_BUCKET_METADATA, "true")
    with j.keyless_store("gs://owned-toy-private-bucket/quality-held-out/owned-toy-exp/") as store:
        with pytest.raises(ValueError, match="held_out_channel_object_create_unknown") as error:
            store.create("synth-040.source.intent.json", b"{}")
    assert "TOY-PRIVATE" not in str(error.value)
    assert len(wire) == 1 and wire[0][0] == "POST" and wire[0][2] == 15
    assert "ifGenerationMatch=0" in wire[0][1]


def test_real_metadata_credentials_and_sdk_constructor_do_not_make_implicit_requests(monkeypatch):
    from google.auth.transport.requests import Request

    def no_metadata(*args, **kwargs):
        pytest.fail("The constructor attempted an implicit metadata request")

    monkeypatch.setenv(j.NO_BUCKET_METADATA, "true")
    monkeypatch.setattr(Request, "__call__", no_metadata)
    with j.keyless_store("gs://owned-toy-private-bucket/quality-held-out/owned-toy-exp/") as store:
        assert store.namespace.endswith("/quality-held-out/owned-toy-exp/")


def test_main_denies_wrong_context_before_constructing_a_store(monkeypatch, capsys):
    def no_store(*args):
        pytest.fail("Store construction was reached")

    monkeypatch.setattr(j, "keyless_store", no_store)
    with monkeypatch.context() as patch:
        patch.setattr(os, "environ", {})
        code = j.main(["--namespace", "gs://owned/quality-held-out/owned-toy-exp/", "--bundle-name", "input.bundle.json",
                       "--bundle-generation", "1", "--bundle-sha256", "0" * 64])
    assert code == 2 and "refused_or_unknown_no_replay" in capsys.readouterr().err


def test_main_never_prints_a_raw_provider_error(monkeypatch, capsys):
    @contextmanager
    def lost(*args):
        raise TimeoutError("TOY-PRIVATE-DO-NOT-LOG")
        yield

    environment = {"CLOUD_RUN_JOB": "hirewiz-quality-held-out-toy-00000000",
        "CLOUD_RUN_EXECUTION": "hirewiz-quality-held-out-toy-00000000-exec1",
        "CLOUD_RUN_TASK_INDEX": "0", "CLOUD_RUN_TASK_ATTEMPT": "0", "CLOUD_RUN_TASK_COUNT": "1",
        j.NO_BUCKET_METADATA: "true"}
    monkeypatch.setattr(j, "keyless_store", lost)
    monkeypatch.setattr(os, "environ", environment)
    code = j.main(["--namespace", "gs://owned/quality-held-out/owned-toy-exp/", "--bundle-name", "input.bundle.json",
                   "--bundle-generation", "1", "--bundle-sha256", "0" * 64])
    output = capsys.readouterr()
    assert code == 2 and "TOY-PRIVATE" not in output.err + output.out


def _transport_catalog(extra):
    """Metadata-only union: no original input files or provider resources."""
    rows: list[dict[str, Any]] = [{"group": "native", "path": path, "kind": "source", "object": {
        "name": "input.pending", "generation": "1", "size": 1, "sha256": "a" * 64}}
        for path in ["materialization-manifest.json", *(f"owned-{index:03}.pdf" for index in range(160))]]
    rows.extend({"group": "authority", "path": f"owned-origin/{kind}-{index}.json", "kind": kind,
        "object": {"name": "input.pending", "generation": "1", "size": size, "sha256": "b" * 64}}
        for index, (kind, size) in enumerate(extra))
    rows.sort(key=lambda row: (row["group"], row["path"]))
    for index, row in enumerate(rows):
        row["object"]["name"] = f"input.common.{index:04}"
    return {"schema_version": "held-out-native-input-catalog-v2",
        "provenance": "mirrored_bytes_original_references_retained",
        "namespace": "gs://hirewiz-quality-dev-482412-20261031/quality-held-out/owned-transport-test/",
        "native_development_lock_sha256": "c" * 64, "common_files": rows}


def test_transport_origins_share_existing_authority_and_input_union_limits():
    assert e.KINDS["control"] == (32, 256 * 1024)
    assert len(e.KINDS) == 16 and set(e.AUTHORITY_KINDS) - set(e.KINDS) == {"transport_job", "transport_origin"}
    assert e.MAX_AUTHORITY_FILES == 2048 and e.MAX_INPUT_BYTES == 2 * 1024**3
    assert i.MAX_COMMON_FILES == 1837 and i.MAX_INPUT_OBJECTS == 1903
    extra = [("transport_job", e.MAX_METADATA)] * 9 + [("transport_origin", e.MAX_METADATA)]
    catalog = i.InputCatalog.model_validate(_transport_catalog(extra))
    assert len(catalog.common_files) == 171
    assert sum(row.object.size for row in catalog.common_files if row.group == "authority") == 2560 * 1024
    assert sum(row.object.size for row in catalog.common_files) + i.RESERVED_VARIABLE_INPUT_BYTES < e.MAX_INPUT_BYTES
    for kind in ("transport_job", "transport_origin"):
        j.AuthorityInput(path="owned-origin.json", kind=kind, object=c.ObjectRef(
            name="input.origin", generation="1", size=e.MAX_METADATA, sha256="a" * 64))
        with pytest.raises(ValueError, match="authority_kind_or_size"):
            j.AuthorityInput(path="owned-origin.json", kind=kind, object=c.ObjectRef(
                name="input.origin", generation="1", size=e.MAX_METADATA + 1, sha256="a" * 64))


@pytest.mark.parametrize("extra", [
    [("transport_job", 1)] * 10, [("transport_origin", 1)] * 2,
    [("control", 1)] * 32,
    [("page", e.MAX_ARTIFACT)] * 137 + [("transport_job", e.MAX_METADATA)] * 9 + [("transport_origin", e.MAX_METADATA)],
])
def test_transport_origin_metadata_cannot_expand_kind_control_or_two_gib_union(extra):
    with pytest.raises(ValueError, match="catalog_authority_count|experiment_input_union_bound"):
        i.InputCatalog.model_validate(_transport_catalog(extra))


def test_transport_authority_mirror_exhausts_all_ten_original_generation_references(transport_toys):
    t = transport_toys
    runner_funding = e.Artifact.model_validate(private_write(t.root, "heldout-runner-funding.json", {"owned": "not funding"}))
    review = e.Artifact.model_validate(private_write(t.root, "heldout-review.json", {"owned": "not approval"}))
    rows = {row["path"]: (row["sha256"], row["kind"]) for row in t.lock["files"]}
    for control in (t.auth.native_development_lock, t.auth.funding_evidence, runner_funding, review):
        rows[control.path] = control.sha256, "control"
    files = [j.AuthorityInput(path=path, kind=kind, object=c.ObjectRef(
        name=f"input.mirror-{index}", generation="999", size=(t.root / path).stat().st_size, sha256=sha))
        for index, (path, (sha, kind)) in enumerate(sorted(rows.items()))]
    bundle: Any = SimpleNamespace(authorization=t.auth, runner_funding_evidence=runner_funding,
        dispatch=SimpleNamespace(external_authority_review=review), authority_files=files)
    # Mirror generation999 is transport custody only; original retained native
    # generations remain inside the unchanged lock/origin records.
    j._exact_authority(bundle, t.root)
    origin_rows = [row for row in files if row.kind in e.TRANSPORT_ORIGIN_KINDS]
    assert len(origin_rows) == 10
    bundle.authority_files = [row for row in files if row != origin_rows[0]]
    with pytest.raises(ValueError, match="exact_review_and_funding_closure"):
        j._exact_authority(bundle, t.root)
    bundle.authority_files = [*files, j.AuthorityInput(path="unbound-origin.json", kind="transport_job",
        object=c.ObjectRef(name="input.unbound", generation="1", size=1, sha256="a" * 64))]
    with pytest.raises(ValueError, match="exact_review_and_funding_closure"):
        j._exact_authority(bundle, t.root)


@pytest.mark.parametrize("change", ["account", "sdk"])
def test_development_native_get_factory_refuses_nonstandard_manager_before_sdk_auth(tmp_path, monkeypatch, change):
    from infra.gcp import document_refresh_auth, document_refresh_probe, native_fresh_fence

    sdk = tmp_path / "owned-official-sdk-not-opened"
    calls: list[Any] = []
    monkeypatch.setattr(document_refresh_probe, "sdk_library", lambda: sdk)
    monkeypatch.setattr(native_fresh_fence, "SDKGoogleReads", lambda *args: calls.append(args))
    account = document_refresh_auth.RELEASE_ACCOUNT
    with pytest.raises(ValueError, match="development_normal_manager_required"):
        i._development_reads(tmp_path / "foreign-sdk" if change == "sdk" else sdk,
            "owned-foreign@example.invalid" if change == "account" else account)
    assert not calls
