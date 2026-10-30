"""Deterministic synthetic object/HTTP ports, not provider funding or receipts."""
from __future__ import annotations

import json
from threading import Lock

import pytest

from app.domains.analysis import native_input_acquisition as acquisition
from app.domains.analysis import quality_packet as packet
from scripts import native_acquisition_channel as channel
from tests.test_native_input_acquisition import capture as capture  # noqa: F401
from tests.test_supported_evaluation import evaluation as evaluation  # noqa: F401


class SyntheticObjects:
    def __init__(self, namespace):
        self.namespace, self.data, self.calls, self.failures = namespace, {}, [], {}
        self.lock = Lock()

    def create(self, name, raw):
        with self.lock:
            self.calls.append(("create", name))
            failure = self.failures.pop(name, None)
            if failure == "before":
                raise TimeoutError("synthetic-secret-must-not-be-retained")
            if name in self.data:
                raise FileExistsError("synthetic atomic create-only conflict")
            ref = channel.ObjectRef(name=name, generation=str(len(self.data) + 1), size=len(raw), sha256=packet.digest(raw))
            self.data[name] = channel.Object(ref, raw)
            if failure == "after":
                raise TimeoutError("synthetic-secret-must-not-be-retained")
            return ref

    def fetch(self, name, *, generation=None, maximum):
        with self.lock:
            self.calls.append(("fetch", name))
            value = self.data.get(name)
            if value is None:
                return None
            if generation is not None and value.reference.generation != generation:
                return None
            assert len(value.raw) <= maximum
            return value


@pytest.fixture
def durable(capture):  # noqa: F811
    """Only a fake external dispatcher creates these local synthetic ACKs."""
    report = acquisition.plan(*capture["common"])
    namespace = "gs://synthetic-private-bucket/quality-development/" + capture["auth"].experiment_id + "/"
    store = SyntheticObjects(namespace)
    configuration = channel.binding(report, namespace)
    binding_ref = store.create("channel-binding.json", packet.canonical(configuration.model_dump(mode="json")))
    evidence = capture["common"][3] / "synthetic-dispatch-review.json"
    evidence.write_bytes(b'{"purpose":"synthetic-only-not-native-dispatch-authority"}')
    state = {"parent": None, "checkpoint": packet.digest(packet.canonical({})), "number": 0}
    def issue(*, maximum_requests=1):
        state["number"] += 1
        # Temporary test-only ACK ref is excluded from ACK bytes. The returned
        # generation/hash below comes from the synthetic object-port operation.
        temporary = channel.ObjectRef(name=f"dispatch.{state['number']}.json", generation="1", size=1, sha256="0" * 64)
        value = channel.Dispatch(schema_version="reviewed-native-acquisition-dispatch-v1",
            purpose="unreviewed_service_capture_external_authority_required", reviewed_by="synthetic-test-only",
            external_authority_review={"path": evidence.name, "sha256": packet.file_digest(evidence)},
            channel_binding=binding_ref, acknowledgment=temporary, before_checkpoint_sha256=state["checkpoint"],
            parent_terminal=state["parent"], maximum_requests=maximum_requests)
        acknowledgment = store.create(temporary.name, channel.dispatch_bytes(value))
        return value.model_copy(update={"acknowledgment": acknowledgment})
    def build(dispatch):
        return channel.Channel(store, configuration, dispatch, authorization_root=capture["common"][3], authorization=capture["auth"])
    def execute(*, initialize=False, maximum_requests=1, dispatch=None, transport=None):
        dispatch = dispatch or issue(maximum_requests=maximum_requests)
        adapter = build(dispatch)
        result = channel.execute(capture["common"], capture["journal"], adapter, initialize=initialize,
            maximum_requests=maximum_requests, transport=transport or capture["transport"])
        state["parent"] = channel.ObjectRef.model_validate(result["terminal"])
        state["checkpoint"] = json.loads(store.fetch(state["parent"].name, maximum=channel.MAX_METADATA).raw)["after_checkpoint_sha256"]
        return result, adapter
    return {**capture, "report": report, "store": store, "configuration": configuration,
        "binding": binding_ref, "issue": issue, "build": build, "execute": execute, "state": state}


def test_plan_and_binding_are_inert_and_bind_original_full_split(durable, monkeypatch):
    monkeypatch.setattr(acquisition, "ServiceTransport", lambda: pytest.fail("inert planning made a service client"))
    before = list(durable["store"].calls)
    result = channel.binding(durable["report"], durable["store"].namespace)
    assert result == durable["configuration"] and durable["store"].calls == before
    assert durable["report"]["split_counts"] == {"development": 40, "held_out": 160}
    assert len(durable["report"]["complete_case_registry"]) == 200
    assert not durable["journal"].exists()


def test_intent_ack_precedes_original_transport_and_preserves_exact_bytes(durable):
    original = durable["transport"].inspect
    def inspected(*args):
        intents = [name for name in durable["store"].data if name.endswith(".source.intent.json")]
        assert len(intents) == 1 and len(durable["store"].data[intents[0]].raw) > 0
        return original(*args)
    durable["transport"].inspect = inspected
    result, _ = durable["execute"](initialize=True)
    assert len(durable["transport"].calls) == 1
    key = result["status"]["cases"][0]["case_id"] + ".source"
    for suffix in (".intent.json", ".raw.json", ".retained.json", ".result.json"):
        assert durable["store"].data[key + suffix].raw == acquisition._read(durable["journal"], key + suffix)
    assert result["native_approval"] == "pending"
    assert durable["store"].data[result["terminal"]["name"]].raw.count(b'"authorizes_retail":false') == 1


@pytest.mark.parametrize("moment", ["before", "after"])
def test_lost_phase_intent_ack_never_sends_and_blocks_same_or_fresh_dispatch(durable, moment):
    key = durable["report"]["selected_cases"][0]["case_id"] + ".source"
    durable["store"].failures[key + ".intent.json"] = moment
    dispatch = durable["issue"]()
    with pytest.raises(ValueError, match="unknown_or_refused_no_replay"):
        durable["execute"](initialize=True, dispatch=dispatch)
    assert not durable["transport"].calls
    assert list(durable["journal"].glob("durable-stop.*.json"))
    for incoming in (dispatch, durable["issue"]()):
        with pytest.raises(ValueError):
            durable["execute"](dispatch=incoming)
    assert not durable["transport"].calls


@pytest.mark.parametrize("suffix", [".raw.json", ".result.json", ".terminal.json"])
def test_lost_transfer_ack_preserves_unknown_and_cannot_send_again(durable, suffix):
    key = durable["report"]["selected_cases"][0]["case_id"] + ".source"
    durable["store"].failures[key + suffix] = "after"
    dispatch = durable["issue"]()
    with pytest.raises(ValueError):
        durable["execute"](initialize=True, dispatch=dispatch)
    assert len(durable["transport"].calls) == 1
    with pytest.raises(ValueError):
        durable["execute"](dispatch=dispatch)
    assert len(durable["transport"].calls) == 1
    raw = "".join(path.read_text() for path in durable["journal"].glob("durable-stop.*.json"))
    assert "synthetic-secret" not in raw and '"reserved_expense_released":false' in raw


def test_unknown_http_result_restores_original_intent_and_blocks_new_sends(durable):
    def unknown(*args):
        durable["transport"].calls.append(("original-http-once",))
        raise TimeoutError("synthetic-secret-must-not-be-retained")
    durable["transport"].inspect = unknown
    dispatch = durable["issue"]()
    with pytest.raises(ValueError):
        durable["execute"](initialize=True, dispatch=dispatch)
    durable["journal"].rename(durable["tmp"] / "preserved-original-ephemeral-evidence")
    restored = channel.restore(durable["common"], durable["journal"], durable["build"](dispatch))
    assert restored["unknown_phases"] and not restored["execution_authorized"]
    with pytest.raises(ValueError):
        durable["execute"](dispatch=dispatch)
    assert len(durable["transport"].calls) == 1
    status = acquisition.inspect_local(*durable["common"], durable["journal"])
    assert status["cases"][0]["phases"]["source"]["state"] == "unknown_no_replay"


def test_lost_batch_terminal_ack_is_not_a_parent_or_automatic_resume(durable):
    dispatch = durable["issue"]()
    durable["store"].failures["run." + dispatch.before_checkpoint_sha256 + ".terminal.json"] = "after"
    with pytest.raises(ValueError):
        durable["execute"](initialize=True, dispatch=dispatch)
    assert len(durable["transport"].calls) == 1
    # The actual terminal object may be read by exact reference for external
    # review; its mere presence does not clear the retained local stop marker.
    terminal = durable["store"].fetch("run." + dispatch.before_checkpoint_sha256 + ".terminal.json", maximum=channel.MAX_METADATA)
    assert terminal is not None
    with pytest.raises(ValueError):
        durable["execute"](dispatch=dispatch)
    assert len(durable["transport"].calls) == 1


def test_known_checkpoint_restore_needs_new_external_ack_and_does_not_replay(durable):
    _, old_adapter = durable["execute"](initialize=True)
    durable["journal"].rename(durable["tmp"] / "preserved-known-terminal")
    restored = channel.restore(durable["common"], durable["journal"], durable["build"](old_adapter.dispatch))
    assert not restored["execution_authorized"] and not restored["unknown_phases"]
    # Only this fake test dispatcher issues a new ACK for the known completed
    # checkpoint; the adapter never produces authority on restore.
    result, _ = durable["execute"]()
    assert len(durable["transport"].calls) == 2
    assert result["status"]["cases"][0]["phases"]["source"]["state"] == "captured_unreviewed"


def test_verified_refusal_is_transferred_without_fabricating_response_bytes(durable):
    from app.services.document_ingestion import DocumentInspectionError
    def refused(*args):
        durable["transport"].calls.append(("original-verified-refusal",))
        raise DocumentInspectionError(refused=True)
    durable["transport"].inspect = refused
    result, _ = durable["execute"](initialize=True)
    key = result["status"]["cases"][0]["case_id"] + ".source"
    assert result["status"]["cases"][0]["phases"]["source"]["state"] == "refused_no_replay"
    manifest = json.loads(durable["store"].data[key + ".terminal.json"].raw)
    assert [reference["name"] for reference in manifest["files"]] == [key + ".result.json"]
    assert key + ".raw.json" not in durable["store"].data


@pytest.mark.parametrize("moment", ["before", "after"])
def test_total_local_loss_does_not_turn_unknown_run_ack_into_a_dispatch_grant(durable, moment):
    dispatch = durable["issue"]()
    claim = "run." + dispatch.before_checkpoint_sha256 + ".intent.json"
    durable["store"].failures[claim] = moment
    with pytest.raises(ValueError):
        durable["execute"](initialize=True, dispatch=dispatch)
    assert not durable["transport"].calls
    durable["journal"].rename(durable["tmp"] / "preserved-original-local-stop")
    result = channel.restore(durable["common"], durable["journal"], durable["build"](dispatch))
    assert not result["execution_authorized"]
    with pytest.raises(ValueError):
        durable["execute"](dispatch=dispatch)
    assert not durable["transport"].calls
    # A replacement external ACK is NOT issued in this UNKNOWN experiment.
    # The provider Job/operator intent must refuse it even if the object is absent.


def test_original_cli_cannot_bypass_durable_marker_or_release_unknown(durable):
    dispatch = durable["issue"]()
    durable["store"].failures["run." + dispatch.before_checkpoint_sha256 + ".intent.json"] = "after"
    with pytest.raises(ValueError):
        durable["execute"](initialize=True, dispatch=dispatch)
    with pytest.raises(ValueError, match="durable_channel_required"):
        acquisition.execute(*durable["common"], durable["journal"], transport=durable["transport"])
    assert not durable["transport"].calls


def test_unwrapped_cli_rejects_dangling_marker_and_restore_rejects_symlink_parent(durable):
    journal = durable["journal"]
    journal.mkdir(mode=0o700)
    acquisition._new(journal, "plan.json", packet.canonical(durable["report"]))
    (journal / "durable-channel.json").symlink_to(durable["tmp"] / "absent-marker")
    with pytest.raises(ValueError, match="durable_channel_required"):
        acquisition.execute(*durable["common"], journal, transport=durable["transport"])
    dispatch = durable["issue"]()
    parent = durable["tmp"] / "linked-parent"
    parent.symlink_to(durable["tmp"], target_is_directory=True)
    with pytest.raises(ValueError, match="exact_private_destination_required"):
        channel.restore(durable["common"], parent / "new-output", durable["build"](dispatch))
    assert not (durable["tmp"] / "new-output").exists() and not durable["transport"].calls


def test_global_create_only_run_claim_fences_distinct_local_replicas(durable):
    dispatch = durable["issue"]()
    report = durable["report"]
    roots = [durable["tmp"] / name for name in ("replica-a", "replica-b")]
    for root in roots:
        root.mkdir(mode=0o700)
        acquisition._new(root, "plan.json", packet.canonical(report))
    first, second = durable["build"](dispatch), durable["build"](dispatch)
    first.begin(roots[0], report, initialize=True, maximum_requests=1)
    with pytest.raises(FileExistsError):
        second.begin(roots[1], report, initialize=True, maximum_requests=1)
    assert not durable["transport"].calls


def test_completed_batches_restore_and_export_using_unchanged_composite_importer(durable):
    count, first, adapter = 0, True, None
    while count < durable["report"]["maximum_http_requests"]:
        cap = min(10, durable["report"]["maximum_http_requests"] - count)
        result, adapter = durable["execute"](initialize=first, maximum_requests=cap)
        first, count = False, count + cap
    assert len(durable["transport"].calls) == count
    last = adapter.dispatch
    durable["journal"].rename(durable["tmp"] / "preserved-complete-local")
    restored = channel.restore(durable["common"], durable["journal"], durable["build"](last))
    assert not restored["unknown_phases"] and not restored["execution_authorized"]
    destination = durable["tmp"] / "restored-unreviewed-export"
    result = channel.export_unreviewed(durable["common"], durable["journal"], destination, durable["build"](last))
    assert result["captured_cases"] == 40 and not result["quality_qualified"]
    registry = json.loads((destination / "capture-registry.unreviewed.json").read_bytes())
    assert registry["custody"] == "unreviewed_service_http_capture_v1" and "reviewed_by" not in registry
    assert any("compilation" in value for value in registry["captures"].values())
    assert len(durable["transport"].calls) == count


@pytest.mark.parametrize("mutation", ["generation", "hash", "terminal", "source"])
def test_changed_generation_hash_terminal_or_source_cannot_restore_or_send(durable, mutation):
    _, adapter = durable["execute"](initialize=True)
    store = durable["store"]
    if mutation == "source":
        source = durable["common"][1] / durable["report"]["selected_cases"][0]["source"]["path"]
        source.write_bytes(b"changed-synthetic-source")
    else:
        name = next(name for name in store.data if name.endswith(".source.terminal.json" if mutation == "terminal" else ".source.raw.json"))
        old = store.data[name]
        if mutation == "generation":
            store.data[name] = channel.Object(old.reference.model_copy(update={"generation": "999"}), old.raw)
        elif mutation == "hash":
            store.data[name] = channel.Object(old.reference, b"{}")
        else:
            store.data[name] = channel.Object(old.reference, packet.canonical({"version": channel.VERSION, "files": []}))
    with pytest.raises((ValueError, OSError)):
        channel.restore(durable["common"], durable["tmp"] / "refused-restore", durable["build"](adapter.dispatch))
    assert len(durable["transport"].calls) == 1


@pytest.mark.parametrize("change", ["authority", "acknowledgment", "cap", "output_budget"])
def test_missing_external_authority_ack_or_bounds_refuses_before_http(durable, change):
    dispatch = durable["issue"]()
    if change == "authority":
        (durable["common"][3] / dispatch.external_authority_review.path).write_bytes(b"changed-review")
        with pytest.raises(ValueError):
            durable["build"](dispatch)
    else:
        if change == "acknowledgment":
            del durable["store"].data[dispatch.acknowledgment.name]
        elif change == "cap":
            with pytest.raises(ValueError):
                durable["execute"](initialize=True, maximum_requests=2, dispatch=dispatch)
            assert not durable["transport"].calls
            return
        else:
            durable["configuration"] = durable["configuration"].model_copy(update={"maximum_output_bytes": 1})
            # Directly build the changed binding; its previously acknowledged
            # remote bytes remain exact, so it cannot be adopted.
            adapter = channel.Channel(durable["store"], durable["configuration"], dispatch,
                authorization_root=durable["common"][3], authorization=durable["auth"])
            with pytest.raises(ValueError):
                channel.execute(durable["common"], durable["journal"], adapter, initialize=True, transport=durable["transport"])
            assert not durable["transport"].calls
            return
        with pytest.raises(ValueError):
            durable["execute"](initialize=True, dispatch=dispatch)
    assert not durable["transport"].calls


def test_gcs_port_uses_exact_prefix_generations_and_never_adc_or_listing(monkeypatch):
    import google.auth
    monkeypatch.setattr(google.auth, "default", lambda *a, **k: pytest.fail("ADC constructed"))
    calls = []
    class Blob:
        generation, size = None, None
        def upload_from_string(self, raw, **kwargs):
            calls.append(("create", kwargs))
            assert kwargs == {"if_generation_match": 0, "retry": None, "timeout": 15, "content_type": "application/octet-stream"}
            self.raw, self.generation, self.size = raw, "17", len(raw)
        def reload(self, **kwargs):
            calls.append(("metadata", kwargs))
        def download_as_bytes(self, **kwargs):
            calls.append(("download", kwargs))
            assert kwargs == {"if_generation_match": 17, "retry": None, "timeout": 15}
            return self.raw
    blob = Blob()
    class Bucket:
        def blob(self, name, **kwargs):
            assert name == "quality-development/synthetic-channel/test.raw.json"
            return blob
    class Client:
        def bucket(self, name):
            assert name == "synthetic-private-bucket"
            return Bucket()
    store = channel.GcsObjects(Client(), "gs://synthetic-private-bucket/quality-development/synthetic-channel/")
    ref = store.create("test.raw.json", b"actual-synthetic-body")
    got = store.fetch(ref.name, generation=ref.generation, maximum=100)
    assert got.reference == ref and len(calls) == 3
