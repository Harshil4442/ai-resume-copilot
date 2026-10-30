"""Synthetic entry/channel integration, never native execution or funding proof."""
from __future__ import annotations

import json
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from app.domains.analysis import native_input_acquisition as acquisition
from app.domains.analysis import quality_packet as packet
from scripts import native_acquisition_channel as channel
from scripts import native_acquisition_job as job
from tests.test_native_acquisition_channel import SyntheticObjects
from tests.test_native_input_acquisition import capture as capture  # noqa: F401
from tests.test_supported_evaluation import evaluation as evaluation  # noqa: F401


@pytest.fixture
def batch(capture):  # noqa: F811
    workspace = capture["tmp"] / "job-work"
    auth = acquisition.Authorization.model_validate(capture["auth"].model_dump() | {
        "journal_path": str(workspace / "journal")})
    common = (capture["common"][0], capture["common"][1], auth, capture["common"][3])
    report = acquisition.plan(*common)
    store = SyntheticObjects("gs://synthetic-private-bucket/quality-development/" + auth.experiment_id + "/")
    configuration = channel.binding(report, store.namespace)
    binding = store.create("channel-binding.json", packet.canonical(configuration.model_dump(mode="json")))
    authority = common[3]
    acquisition._new(authority, "dispatch-review.json", b'{"synthetic_only":"not_authority"}')
    acquisition._new(authority, "runner-funding.json", b'{"synthetic_only":"not_funded"}')
    review = acquisition._artifact(authority, "dispatch-review.json")
    runner = acquisition._artifact(authority, "runner-funding.json")
    counter = [0]

    def issue(parent=None):
        counter[0] += 1
        before = (json.loads(store.data[parent.name].raw)["after_checkpoint_sha256"]
                  if parent is not None else packet.digest(packet.canonical({})))
        placeholder = channel.ObjectRef(name=f"dispatch.{counter[0]}.json", generation="1", size=1, sha256="0" * 64)
        dispatch = channel.Dispatch(schema_version="reviewed-native-acquisition-dispatch-v1",
            purpose="unreviewed_service_capture_external_authority_required", reviewed_by="synthetic-test-only",
            external_authority_review=review, channel_binding=binding, acknowledgment=placeholder,
            before_checkpoint_sha256=before, parent_terminal=parent, maximum_requests=1)
        ack = store.create(placeholder.name, channel.dispatch_bytes(dispatch))
        return dispatch.model_copy(update={"acknowledgment": ack})

    def inputs(root, names, prefix):
        return [{"path": name, "object": store.create(f"input.{prefix}{index}", (root / name).read_bytes()).model_dump()}
                for index, name in enumerate(sorted(names))]

    native_names = {"materialization-manifest.json"} | {row["source"]["path"] for row in report["selected_cases"]}
    auth_names = {auth.funding_evidence.path, review["path"], runner["path"]}
    bundle = job.Bundle(schema_version="development-native-job-input-v1",
        purpose="unreviewed_development_capture_no_model_no_retail", entry_sha256=packet.file_digest(Path(job.__file__)),
        job_name="synthetic-quality-job", configuration=configuration, authorization=auth,
        dispatch=issue(), previous_dispatch=None, runner_funding_evidence=runner,
        native_files=inputs(common[1], native_names, "n"), authorization_files=inputs(authority, auth_names, "a"))
    environment = {"CLOUD_RUN_JOB": bundle.job_name, "CLOUD_RUN_EXECUTION": bundle.job_name + "-first",
        "CLOUD_RUN_TASK_INDEX": "0", "CLOUD_RUN_TASK_ATTEMPT": "0", "CLOUD_RUN_TASK_COUNT": "1"}
    return {**capture, "bundle": bundle, "store": store, "workspace": workspace, "environment": environment,
            "packet": common[0], "issue": issue}


def run(batch, *, bundle=None):
    return job.run(bundle or batch["bundle"], batch["store"], batch["packet"], batch["workspace"],
                   environment=batch["environment"], transport=batch["transport"])


def test_entry_preserves_original_body_and_durable_phase_receipt(batch):
    result = run(batch)
    assert len(batch["transport"].calls) == 1
    assert result["state"] == "completed_unreviewed" and result["native_approval"] == "pending"
    assert result["actual_native_cost_minor"] is None and not result["authorizes_retail"]
    terminal = channel.ObjectRef.model_validate(result["terminal"])
    assert batch["store"].data[terminal.name].reference == terminal
    raw = next(batch["workspace"].joinpath("journal").glob("*.raw.json"))
    assert batch["store"].data[raw.name].raw == raw.read_bytes()
    assert len(list((batch["workspace"] / "native").rglob("*.*"))) >= 41
    with pytest.raises(FileExistsError):
        run(batch)
    assert len(batch["transport"].calls) == 1


def test_new_ephemeral_batch_restores_old_acknowledged_bytes_without_resend(batch):
    first = run(batch)
    before = {key: value for key, value in batch["store"].data.items() if key.endswith(".raw.json")}
    batch["workspace"].rename(batch["workspace"].with_name("first-execution-retained"))
    old = batch["bundle"].dispatch
    dispatch = batch["issue"](channel.ObjectRef.model_validate(first["terminal"]))
    following = batch["bundle"].model_copy(update={"previous_dispatch": old, "dispatch": dispatch})
    batch["environment"]["CLOUD_RUN_EXECUTION"] = following.job_name + "-second"
    result = run(batch, bundle=following)
    assert result["state"] == "completed_unreviewed" and len(batch["transport"].calls) == 2
    assert all(batch["store"].data[key] == value for key, value in before.items())


def test_lost_processing_response_stays_unknown_after_ephemeral_restore(batch):
    def lost(*args):
        batch["transport"].calls.append(("sent-once",))
        raise TimeoutError("synthetic-sensitive-provider-error")
    batch["transport"].inspect = lost
    with pytest.raises(ValueError):
        run(batch)
    assert len(batch["transport"].calls) == 1
    batch["workspace"].rename(batch["workspace"].with_name("unknown-execution-retained"))
    following = batch["bundle"].model_copy(update={"previous_dispatch": batch["bundle"].dispatch,
        "dispatch": batch["issue"]()})
    with pytest.raises(ValueError):
        run(batch, bundle=following)
    assert len(batch["transport"].calls) == 1
    assert any(name.endswith(".source.intent.json") for name in batch["store"].data)
    assert not any("sensitive-provider" in obj.raw.decode(errors="replace") for obj in batch["store"].data.values())


@pytest.mark.parametrize("key,value", [("CLOUD_RUN_TASK_ATTEMPT", "1"), ("CLOUD_RUN_TASK_INDEX", "1"),
    ("CLOUD_RUN_TASK_COUNT", "2"), ("CLOUD_RUN_JOB", "other-job"), ("CLOUD_RUN_EXECUTION", "unrelated-first"),
    ("GOOGLE_APPLICATION_CREDENTIALS", "/tmp/key.json"), ("GOOGLE_API_KEY", "synthetic"),
    ("DATABASE_URL", "synthetic"), ("GEMINI_API_KEY", "synthetic"), ("GOOGLE_CLOUD_PROJECT", "other-project"),
    ("HTTPS_PROXY", "https://unrelated.invalid"), ("STORAGE_EMULATOR_HOST", "http://unrelated.invalid"),
    ("GCE_METADATA_HOST", "unrelated.invalid"), ("SSLKEYLOGFILE", "/tmp/synthetic-tls-keys")])
def test_inappropriate_context_or_credentials_stop_before_any_fetch_or_send(batch, key, value):
    batch["environment"][key] = value
    before = list(batch["store"].calls)
    with pytest.raises(ValueError):
        run(batch)
    assert batch["store"].calls == before and not batch["transport"].calls
    assert not batch["workspace"].exists()


@pytest.mark.parametrize("path", ["../escape", "sources/../escape", "./same", "/absolute", "sources/./same"])
def test_escaped_or_ambiguous_input_paths_are_never_materialized(batch, path):
    data = batch["bundle"].model_dump()
    data["native_files"][0]["path"] = path
    with pytest.raises(ValueError):
        run(batch, bundle=data)
    assert not batch["workspace"].exists() and not batch["transport"].calls


def test_changed_generation_stops_before_processing(batch):
    value = batch["bundle"].native_files[0]
    batch["store"].data.pop(value.object.name)
    with pytest.raises(ValueError):
        run(batch)
    assert not batch["transport"].calls
    assert not any(name.endswith(".intent.json") for name in batch["store"].data)


def test_changed_channel_source_never_writes_context_or_processing_intent(batch):
    data = batch["bundle"].model_dump()
    data["configuration"]["channel_source_sha256"] = "0" * 64
    with pytest.raises(ValueError, match="source_or_plan_changed"):
        run(batch, bundle=data)
    assert not batch["transport"].calls
    assert not any(name.endswith((".intent.json", ".context.json")) for name in batch["store"].data)


@pytest.mark.parametrize("group,limit", [("native_files", acquisition.inspection.MAX_SOURCE_BYTES),
    ("authorization_files", channel.MAX_METADATA)])
def test_input_size_bounds_apply_before_fetch(batch, group, limit):
    data = batch["bundle"].model_dump()
    data[group][0]["object"]["size"] = limit + 1
    before = list(batch["store"].calls)
    with pytest.raises(ValueError):
        run(batch, bundle=data)
    assert not batch["workspace"].exists() and batch["store"].calls == before
    assert not batch["transport"].calls


def test_expired_authorization_never_constructs_workspace(batch):
    data = batch["bundle"].model_dump()
    now = datetime.now(UTC)
    data["authorization"]["valid_from"] = now - timedelta(hours=2)
    data["authorization"]["expires_at"] = now - timedelta(hours=1)
    with pytest.raises(ValueError, match="active_original_authorization"):
        run(batch, bundle=data)
    assert not batch["workspace"].exists() and not batch["transport"].calls


def test_expiry_during_input_transfer_stops_before_context_or_processing_intent(batch, monkeypatch):
    current = [datetime.now(UTC)]
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            assert tz is UTC
            return current[0]
    monkeypatch.setattr(job, "datetime", Clock)
    original = batch["store"].fetch
    last = batch["bundle"].authorization_files[-1].object.name
    def fetch(name, **kwargs):
        result = original(name, **kwargs)
        if name == last:
            current[0] = batch["bundle"].authorization.expires_at
        return result
    batch["store"].fetch = fetch
    with pytest.raises(ValueError, match="active_original_authorization"):
        run(batch)
    assert not batch["transport"].calls
    assert not any(name.endswith((".intent.json", ".context.json")) for name in batch["store"].data)


def test_file_main_rejects_non_job_environment_before_creating_any_provider_client(monkeypatch, capsys):
    from google.cloud import storage
    monkeypatch.setattr(storage, "Client", lambda **kwargs: pytest.fail("Unexpected provider client"))
    monkeypatch.delenv("CLOUD_RUN_JOB", raising=False)
    monkeypatch.setenv("GOOGLE_API_KEY", "synthetic-sensitive-not-printed")
    monkeypatch.setattr(sys, "argv", ["native_acquisition_job", "--namespace",
        "gs://synthetic-private-bucket/quality-development/synthetic-service-http-test/",
        "--bundle-name", "input.bundle.json", "--bundle-generation", "1", "--bundle-sha256", "0" * 64])
    assert job.main() == 2
    captured = capsys.readouterr()
    assert captured.out == "" and "synthetic-sensitive" not in captured.err
    assert json.loads(captured.err)["state"] == "refused_or_unknown_no_replay"


def test_file_entry_help_is_inert_and_bootstraps_package_from_unrelated_directory(tmp_path):
    result = subprocess.run([sys.executable, "-I", "-B", str(Path(job.__file__)), "--help"],
        cwd=tmp_path, text=True, capture_output=True, timeout=20)
    assert result.returncode == 0 and "--bundle-generation" in result.stdout
    assert not list(tmp_path.iterdir())
