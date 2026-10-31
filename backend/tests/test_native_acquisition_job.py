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


def test_reused_development_bytes_keep_all_forty_owned_files_and_fresh_authority(tmp_path, monkeypatch):
    from tests import test_supported_evaluation as fixtures

    cache = fixtures._materialized_development_bytes
    cache.cache_clear()
    try:
        first_root, second_root = tmp_path / "first", tmp_path / "second"
        first_root.mkdir()
        second_root.mkdir()
        first = fixtures.evaluation.__wrapped__(first_root, monkeypatch)
        first_info = cache.cache_info()
        second = fixtures.evaluation.__wrapped__(second_root, monkeypatch)
        second_info = cache.cache_info()
        manifest = json.loads((first["native_root"] / "materialization-manifest.json").read_bytes())
        assert len(manifest["cases"]) == 40
        assert (first_info.misses, first_info.currsize) == (40, 40)
        assert second_info.misses == 40 and second_info.hits == first_info.hits + 40
        for row in manifest["cases"]:
            name = row["input_artifact"]["path"]
            original, separate = first["native_root"] / name, second["native_root"] / name
            assert original.read_bytes() == separate.read_bytes()
            assert original.stat().st_ino != separate.stat().st_ino
            assert original.stat().st_nlink == separate.stat().st_nlink == 1
        name = manifest["cases"][0]["input_artifact"]["path"]
        untouched = (second["native_root"] / name).read_bytes()
        (first["native_root"] / name).write_bytes(b"test-owned source mutation")
        assert (second["native_root"] / name).read_bytes() == untouched
        assert first["auth"].experiment_id != second["auth"].experiment_id
        assert first["auth"].native_capture_registry != second["auth"].native_capture_registry
        for a in first["authorization_root"].iterdir():
            b = second["authorization_root"] / a.name
            assert a.is_file() and b.is_file() and a.stat().st_ino != b.stat().st_ino
            assert a.stat().st_nlink == b.stat().st_nlink == 1
    finally:
        cache.cache_clear()


@pytest.mark.parametrize("change", ["recipe", "complete_case_metadata"])
def test_development_byte_cache_never_ignores_any_case_or_recipe_input(change):
    from tests import test_supported_evaluation as fixtures

    cache = fixtures._materialized_development_bytes
    cache.cache_clear()
    try:
        case = fixtures.inputs.selected_cases(fixtures.ROOT, "development")[1][0]
        fixtures.materialized_development_bytes(case)
        fixtures.materialized_development_bytes(case)
        before = cache.cache_info()
        changed = json.loads(packet.canonical(case))
        if change == "recipe":
            changed["source_recipe"]["margin_inches"] += 0.01
        else:
            changed["synthetic_cache_metadata"] = "different complete input"
        fixtures.materialized_development_bytes(changed)
        assert before.hits == 1 and cache.cache_info().misses == before.misses + 1
    finally:
        cache.cache_clear()


@pytest.mark.parametrize("source", ["materializer", "fixture"])
def test_development_byte_cache_binds_actual_helper_source_bytes(source, tmp_path, monkeypatch):
    from tests import test_supported_evaluation as fixtures

    cache = fixtures._materialized_development_bytes
    cache.cache_clear()
    try:
        module = fixtures.materialize if source == "materializer" else fixtures
        original = Path(module.__file__)
        copied = tmp_path / original.name
        copied.write_bytes(original.read_bytes())
        monkeypatch.setattr(module, "__file__", str(copied))
        case = fixtures.inputs.selected_cases(fixtures.ROOT, "development")[1][0]
        fixtures.materialized_development_bytes(case)
        copied.write_bytes(copied.read_bytes() + b"\n# different helper source bytes\n")
        fixtures.materialized_development_bytes(case)
        assert cache.cache_info().misses == 2 and copied.read_bytes() != original.read_bytes()
    finally:
        cache.cache_clear()


def test_development_byte_cache_binds_actual_font_content(tmp_path, monkeypatch):
    from tests import test_supported_evaluation as fixtures

    cache = fixtures._materialized_development_bytes
    cache.cache_clear()
    try:
        actual_font = Path(fixtures.reportlab.__file__).parent / "fonts/Vera.ttf"
        font = tmp_path / "fonts/Vera.ttf"
        font.parent.mkdir()
        font.write_bytes(actual_font.read_bytes())
        monkeypatch.setattr(fixtures.reportlab, "__file__", str(tmp_path / "__init__.py"))
        case = fixtures.inputs.selected_cases(fixtures.ROOT, "development")[1][0]
        fixtures.materialized_development_bytes(case)
        font.write_bytes(font.read_bytes() + b"\nsynthetic trailing font data\n")
        fixtures.materialized_development_bytes(case)
        assert cache.cache_info().misses == 2
    finally:
        cache.cache_clear()


def test_development_byte_cache_binds_replaced_helper_callables(monkeypatch):
    from tests import test_supported_evaluation as fixtures

    cache = fixtures._materialized_development_bytes
    cache.cache_clear()
    try:
        case = next(c for c in fixtures.inputs.selected_cases(fixtures.ROOT, "development")[1]
                    if c["source_format"] == "pdf")
        fixtures.materialized_development_bytes(case)
        original, calls = fixtures.materialize._pdf, []

        def first(*args):
            calls.append("first")
            return original(*args)

        def second(*args):
            calls.append("second")
            return original(*args)

        monkeypatch.setattr(fixtures.materialize, "_pdf", first)
        fixtures.materialized_development_bytes(case)
        monkeypatch.setattr(fixtures.materialize, "_pdf", second)
        fixtures.materialized_development_bytes(case)
        assert calls == ["first", "second"] and cache.cache_info().misses == 3
    finally:
        cache.cache_clear()


def test_development_byte_cache_refuses_helper_drift_during_generation(monkeypatch):
    from tests import test_supported_evaluation as fixtures

    cache = fixtures._materialized_development_bytes
    cache.cache_clear()
    try:
        case = next(c for c in fixtures.inputs.selected_cases(fixtures.ROOT, "development")[1]
                    if c["source_format"] == "pdf")
        original = fixtures.materialize._pdf

        def changing(*args):
            monkeypatch.setattr(fixtures.materialize, "_tex", lambda value: b"changed helper")
            return original(*args)

        monkeypatch.setattr(fixtures.materialize, "_pdf", changing)
        with pytest.raises(AssertionError, match="materializer changed"):
            fixtures.materialized_development_bytes(case)
        assert cache.cache_info().currsize == 0
    finally:
        cache.cache_clear()


def test_fixture_batch_uses_two_actual_complete_bindings_and_preserves_cache_keys(monkeypatch):
    from tests import test_supported_evaluation as fixtures

    cases = fixtures.inputs.selected_cases(fixtures.ROOT, "development")[1]
    original, bindings = fixtures._materialization_binding, []
    def observed(font):
        value = original(font)
        bindings.append(value)
        return value
    monkeypatch.setattr(fixtures, "_materialization_binding", observed)
    cache = fixtures._materialized_development_bytes
    cache.cache_clear()
    try:
        first = fixtures.materialized_development_batch(cases)
        cold = cache.cache_info()
        second = fixtures.materialized_development_batch(cases)
        warm = cache.cache_info()
        assert len(first) == len(second) == 40 and first == second
        assert len(bindings) == 4 and all(value == bindings[0] for value in bindings)
        assert cold.misses == warm.misses == 40 and warm.hits == cold.hits + 40
        # The unchanged standalone helper uses the exact same complete key.
        assert fixtures.materialized_development_bytes(cases[0]) == first[0]
        assert len(bindings) == 6 and cache.cache_info().hits == warm.hits + 1
    finally:
        cache.cache_clear()


@pytest.mark.parametrize("change", ["helper", "source", "font", "tool_version", "exception"])
def test_fixture_batch_drift_or_failure_clears_bytes_before_publishing_any_authority(change, tmp_path, monkeypatch):
    from tests import test_supported_evaluation as fixtures

    original = fixtures._materialized_development_bytes
    cache = original
    cache.cache_clear()
    calls = []
    source_copy = tmp_path / "fixture-source.py"
    source_copy.write_bytes(Path(fixtures.__file__).read_bytes())
    font = tmp_path / "fonts/Vera.ttf"
    font.parent.mkdir()
    font.write_bytes((Path(fixtures.reportlab.__file__).parent / "fonts/Vera.ttf").read_bytes())
    if change == "source":
        monkeypatch.setattr(fixtures, "__file__", str(source_copy))
    if change == "font":
        monkeypatch.setattr(fixtures.reportlab, "__file__", str(tmp_path / "__init__.py"))
    prepared = []
    def materialized(*args):
        # Only the first byte preparation is real. Later values are deliberately
        # inert duplicates inside a batch that MUST refuse before any file write.
        result = original(*args) if not prepared else prepared[0]
        prepared.append(result)
        calls.append(1)
        if len(calls) == 1:
            if change == "helper":
                monkeypatch.setattr(fixtures.materialize, "_tex", lambda value: b"changed synthetic helper")
            elif change == "source":
                source_copy.write_bytes(source_copy.read_bytes() + b"\n# changed source\n")
            elif change == "font":
                font.write_bytes(font.read_bytes() + b"synthetic changed font bytes")
            elif change == "tool_version":
                monkeypatch.setattr(fixtures, "version", lambda name: "changed-synthetic-version")
            else:
                raise ValueError("synthetic generation failure")
        return result
    materialized.cache_clear = cache.cache_clear
    monkeypatch.setattr(fixtures, "_materialized_development_bytes", materialized)
    monkeypatch.setattr(fixtures, "write", lambda *a, **k: pytest.fail("Published authority before batch binding check"))
    with pytest.raises((AssertionError, ValueError)):
        fixtures.evaluation.__wrapped__(tmp_path, monkeypatch)
    assert cache.cache_info().currsize == 0
    assert not list((tmp_path / "native").iterdir()) and not list((tmp_path / "evidence").iterdir())
