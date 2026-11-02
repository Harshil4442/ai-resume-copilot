"""Owned toy object/HTTP ports; no provider, frozen packet or native durability proof."""
from __future__ import annotations

import base64
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.domains.analysis import quality_packet as packet
from evaluation.native_acquisition_v2 import channel as c
from evaluation.native_acquisition_v2 import engine as e
from tests.test_native_acquisition_v2_engine import heldout_toys as heldout_toys  # noqa: F401
from tests.test_native_acquisition_v2_engine import private_write
from tests.test_native_acquisition_v2_engine import reviewed_toys as reviewed_toys  # noqa: F401


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
