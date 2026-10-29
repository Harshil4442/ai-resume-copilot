"""Definition expiry and candidate refusal boundaries; no Docker/cloud calls."""
from __future__ import annotations

import copy
import hashlib
import io
import tarfile
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from scripts import document_refresh as refresh

NOW = datetime(2026, 10, 16, 6, 24, 15, tzinfo=UTC)
HEADER = b"ClamAV 1.4.3/28153/Wed Oct 14 06:24:15 2026\n"


@pytest.mark.parametrize(("elapsed", "status"), [
    (timedelta(hours=47), "fresh"), (timedelta(hours=48), "refresh_required"),
    (timedelta(days=3), "refresh_required"), (timedelta(days=3, seconds=1), "expired"),
])
def test_alert_precedes_unchanged_runtime_expiry(elapsed, status):
    issued = NOW - timedelta(days=2)
    result = refresh.definition(HEADER, now=issued + elapsed)
    assert result["status"] == status
    assert result["seconds_remaining"] == int((timedelta(days=3) - elapsed).total_seconds())
    assert result["expires_at_utc"] == "2026-10-17T06:24:15+00:00"


@pytest.mark.parametrize("header", [b"ClamAV 2.0.0/28153/Wed Oct 14 06:24:15 2026", b"bad",
    HEADER + b"unexpected additional output", b"ClamAV 1.4.3/28153/Wed Oct 99 06:24:15 2026"])
def test_missing_altered_or_new_engine_requires_review(header):
    with pytest.raises(refresh.RefreshDenied):
        refresh.definition(header, now=NOW)


def test_future_header_is_never_a_freshness_extension():
    with pytest.raises(refresh.RefreshDenied, match="future_definitions"):
        refresh.definition(HEADER, now=NOW - timedelta(days=2, seconds=1))


def contract():
    return SimpleNamespace(ACCEPTED_IMAGE=refresh.RENDERER_BASE, ACCEPTED_RENDERER="reviewed-renderer",
                           ACCEPTED_FONT_MANIFEST="a" * 64, POLICY_SHA256="b" * 64)


def proof():
    scan = {"engine": "ClamAV", "version": "1.4.3", "definitions": "28155/2026-10-16T06:24:15+00:00"}
    return {"platform": "linux/amd64", "scope": "local_native_docker_only", "cloud_run_isolation_proven": False,
        "runtime_source_bytes_verified": True, "restricted_container_metadata_verified": True,
        "source_sha256": {"owned-source": "c" * 64}, "image_id": "sha256:" + "d" * 64,
        "corpus": [{"case": name, "passed": True, "scan": copy.deepcopy(scan),
                    **({"outcome": "clean", "native_source_empty": True}
                       if name in {"clean_tex", "clean_texzip"} else {}),
                    **({"outcome": "detected"} if name in {"eicar_tex_source", "eicar_texzip_member"} else {})}
                   for name in (
            "clean_pdf", "clean_docx", "page_counter", "active_pdf", "includetext",
            "eicar_exact_file", "eicar_zip_member", "missing_definitions", "clean_tex", "clean_texzip",
            "eicar_tex_source", "eicar_texzip_member")],
        "docx_layout_corpus": [{"case": "styled_multipage_fit", "passed": True, "layout": {
            "renderer": {"renderer_version": "reviewed-renderer", "font_manifest_sha256": "a" * 64,
                "policy_sha256": "b" * 64, "image_digest": "local-document-image@sha256:" + "d" * 64},
            "rendered_fonts_sha256": "e" * 64}}, {"case": "rendered_reflow_refusal", "passed": True}],
        "request_clamd_corpus": [{"case": str(i), "daemon_stopped_before_writer": True,
            "native_log_removed": True, "daemon_returncode": 0} for i in range(11)]}


def test_local_proof_keeps_native_acceptance_separate_and_font_exact():
    facts = refresh.proof_contract(proof(), contract(), {"owned-source": "c" * 64}, now=NOW)
    assert facts["local_cases"] == 25
    assert facts["expires_at_utc"] == "2026-10-19T06:24:15+00:00"


@pytest.mark.parametrize("change", ["old_corpus", "source_facts", "no_detection"])
def test_native_source_proof_cannot_be_replaced_by_old_or_unverified_cases(change):
    value = proof()
    if change == "old_corpus":
        value["corpus"] = value["corpus"][:8]
    elif change == "source_facts":
        next(row for row in value["corpus"] if row["case"] == "clean_tex")["native_source_empty"] = False
    else:
        next(row for row in value["corpus"] if row["case"] == "eicar_texzip_member")["outcome"] = "clean"
    with pytest.raises(refresh.RefreshDenied):
        refresh.proof_contract(value, contract(), {"owned-source": "c" * 64}, now=NOW)


def test_retained_v1_local_proof_requires_exact_frozen_sources_and_explicit_profile():
    import json

    from infra.gcp import document_refresh_probe as probe

    value = proof()
    source = json.loads(probe.legacy_source("policy.json"))["source_sha256"]
    value["source_sha256"] = copy.deepcopy(source)
    value["corpus"] = value["corpus"][:8]
    value["docx_layout_corpus"][0]["layout"]["renderer"]["policy_sha256"] = probe.LEGACY_POLICY
    assert refresh.proof_contract(value, contract(), source, now=NOW, historical_v1=True)["local_cases"] == 21
    with pytest.raises(refresh.RefreshDenied, match="corpus_incomplete"):
        refresh.proof_contract(value, contract(), source, now=NOW)
    drift = {**source, "backend/app/services/native_project.py": "0" * 64}
    value["source_sha256"] = drift
    with pytest.raises(refresh.RefreshDenied, match="historical_source_changed"):
        refresh.proof_contract(value, contract(), drift, now=NOW, historical_v1=True)


@pytest.mark.parametrize("change", ["font", "renderer", "source", "image", "missing_case", "cleanup", "cloud_claim", "stale"])
def test_image_or_engine_drift_never_becomes_an_automatic_successor(change):
    value = proof()
    if change == "font":
        value["docx_layout_corpus"][0]["layout"]["renderer"]["font_manifest_sha256"] = "f" * 64
    elif change == "renderer":
        value["docx_layout_corpus"][0]["layout"]["renderer"]["renderer_version"] = "changed"
    elif change == "source":
        value["source_sha256"]["owned-source"] = "f" * 64
    elif change == "image":
        value["docx_layout_corpus"][0]["layout"]["renderer"]["image_digest"] = "local-document-image@sha256:" + "f" * 64
    elif change == "missing_case":
        value["corpus"].pop()
    elif change == "cleanup":
        value["request_clamd_corpus"][0]["native_log_removed"] = False
    elif change == "cloud_claim":
        value["cloud_run_isolation_proven"] = True
    else:
        for row in value["corpus"]:
            row["scan"]["definitions"] = "28153/2026-10-14T06:24:15+00:00"
    with pytest.raises(refresh.RefreshDenied):
        refresh.proof_contract(value, contract(), {"owned-source": "c" * 64}, now=NOW)


def service():
    name = f"projects/{refresh.PROJECT}/locations/{refresh.REGION}/services/{refresh.SERVICE}"
    ready = name + "/revisions/" + refresh.SERVICE + "-8-owned"
    return {"name": name, "uid": "7014a5ba-faee-4fbf-8272-a437b1de49ad", "generation": "8",
        "observedGeneration": "8", "latestCreatedRevision": ready, "latestReadyRevision": ready,
        "template": {"containers": [{"image": refresh.RENDERER_BASE}]},
        "terminalCondition": {"state": "CONDITION_SUCCEEDED"},
        "trafficStatuses": [{"revision": ready, "percent": 100}]}


def test_monitor_binds_the_actual_serving_revision_not_a_candidate_tag():
    assert refresh.serving_image(service(), contract()) == refresh.RENDERER_BASE
    value = service()
    value["trafficStatuses"].append({"revision": "unaccepted-tag", "percent": 0, "tag": "candidate"})
    with pytest.raises(refresh.RefreshDenied, match="traffic_drift"):
        refresh.serving_image(value, contract())


def test_candidate_builder_always_uses_one_fixed_renderer_base():
    recipe = (refresh.ROOT / "infra/document-processing/refresh.Dockerfile").read_text()
    lines = [line for line in recipe.splitlines() if line.startswith("FROM ")]
    assert lines == ["FROM " + refresh.RENDERER_BASE + " AS definitions", "FROM " + refresh.RENDERER_BASE]
    assert [line for line in recipe.splitlines() if line.startswith("COPY ")] == [
        "COPY --from=definitions /var/lib/clamav /var/lib/clamav"]
    assert "apt-get" not in recipe and "DOCUMENT_DEFINITIONS_NONCE" in recipe


def test_private_receipts_are_exclusive_and_cannot_overwrite_unknown_publish(tmp_path):
    receipt = tmp_path / "publish-started.json"
    refresh.write_new(receipt, {"automatic_replay": False})
    with pytest.raises(FileExistsError):
        refresh.write_new(receipt, {"automatic_replay": True})
    assert receipt.stat().st_mode & 0o777 == 0o600


def build():
    nonce = "a" * 32
    tag = refresh.REPOSITORY + ":refresh-" + nonce
    source = {"bucket": refresh.PROJECT + "_cloudbuild",
        "object": "hirewiz-document-refresh-source/" + nonce + "/source.tar.gz", "generation": "1234"}
    return {"id": "01234567-0123-0123-0123-012345678901", "projectId": refresh.PROJECT,
        "name": "projects/" + refresh.PROJECT + "/locations/us-central1/builds/01234567-0123-0123-0123-012345678901",
        "status": "SUCCESS", "timeout": "900s", "options": {"logging": "CLOUD_LOGGING_ONLY"}, "serviceAccount": f"projects/{refresh.PROJECT}/serviceAccounts/{refresh.BUILDER}",
        "steps": [{"name": refresh.DOCKER_BUILDER, "args": ["build", "--pull=false", "--platform=linux/amd64",
            "--build-arg", "DOCUMENT_DEFINITIONS_NONCE=" + nonce, "--tag", tag, "."]},
            {"name": refresh.DOCKER_BUILDER, "args": ["push", tag]}],
        "results": {"images": [{"name": tag, "digest": "sha256:" + "b" * 64}]},
        "source": {"storageSource": source}, "sourceProvenance": {"resolvedStorageSource": copy.deepcopy(source)}}


def test_native_build_joins_exact_nonce_source_generation_builder_and_image():
    result = refresh.build_contract(build(), "a" * 32, "c" * 64)
    assert result["immutable_image"] == refresh.REPOSITORY + "@sha256:" + "b" * 64
    assert result["source_generation"] == "1234"
    assert result["fixed_renderer_base"] == refresh.RENDERER_BASE


@pytest.mark.parametrize("change", ["nonce", "builder", "source_generation", "step_env", "extra_image", "unfinished"])
def test_native_build_drift_cannot_be_adopted_by_a_receipt(change):
    value = build()
    if change == "nonce":
        value["steps"][0]["args"][4] = "DOCUMENT_DEFINITIONS_NONCE=" + "d" * 32
    elif change == "builder":
        value["serviceAccount"] = "projects/other/serviceAccounts/other@example.test"
    elif change == "source_generation":
        value["sourceProvenance"]["resolvedStorageSource"]["generation"] = "5678"
    elif change == "step_env":
        value["steps"][0]["env"] = ["UNREVIEWED=true"]
    elif change == "extra_image":
        value["results"]["images"].append(copy.deepcopy(value["results"]["images"][0]))
    else:
        value["status"] = "WORKING"
    with pytest.raises(refresh.RefreshDenied):
        refresh.build_contract(value, "a" * 32, "c" * 64)


def archive(name="Dockerfile", *, link=False):
    raw = b"reviewed fixed recipe\n"
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w:gz") as tar:
        row = tarfile.TarInfo(name)
        if link:
            row.type = tarfile.SYMTYPE
            row.linkname = "/unreviewed"
        else:
            row.size = len(raw)
        tar.addfile(row, None if link else io.BytesIO(raw))
    return output.getvalue(), hashlib.sha256(raw).hexdigest()


def test_source_archive_hash_is_verified_without_extracting_files():
    raw, expected = archive()
    assert refresh.archive_recipe(raw, expected) == hashlib.sha256(raw).hexdigest()


@pytest.mark.parametrize(("name", "link"), [("../Dockerfile", False), ("Dockerfile", True), ("credential.json", False)])
def test_non_recipe_or_link_source_archive_refuses(name, link):
    raw, expected = archive(name, link=link)
    with pytest.raises(refresh.RefreshDenied):
        refresh.archive_recipe(raw, expected)


@pytest.mark.parametrize("change", ["entrypoint", "global_env", "available_secrets", "global_volume", "substitutions", "timeout", "logging", "allowed_failure"])
def test_native_build_extra_execution_inputs_are_not_definition_only(change):
    value = build()
    if change == "entrypoint":
        value["steps"][0]["entrypoint"] = "sh"
    elif change == "global_env":
        value["options"]["env"] = ["DOCKER_HOST=unreviewed"]
    elif change == "available_secrets":
        value["availableSecrets"] = {"secretManager": [{"env": "unreviewed"}]}
    elif change == "global_volume":
        value["options"]["volumes"] = [{"name": "unreviewed", "path": "/workspace"}]
    elif change == "substitutions":
        value["substitutions"] = {"_UNREVIEWED": "value"}
    elif change == "timeout":
        value["timeout"] = "1800s"
    elif change == "logging":
        value["options"]["logging"] = "NONE"
    else:
        value["steps"][0]["allowFailure"] = True
    with pytest.raises(refresh.RefreshDenied):
        refresh.build_contract(value, "a" * 32, "c" * 64)


def test_scanner_failure_emits_fixed_immediate_unavailable_without_repeating_operation(monkeypatch, capsys):
    calls = []
    def failed_main():
        calls.append("one attempted observation")
        raise RuntimeError("private DSN and candidate body")
    monkeypatch.setattr(refresh, "main", failed_main)
    monkeypatch.setattr(refresh, "emit_status", lambda value: calls.append(value))
    assert refresh.run_cli() == 2
    assert calls == ["one attempted observation", {"mode": "monitor", "definitions": {"status": "unavailable"}}]
    assert "private" not in capsys.readouterr().out


@pytest.mark.parametrize(("error", "code"), [
    ("native", "document_native_readonly_transient_exhausted"),
    ("transition", "document_final_preclaim_state_changed"),
    ("private", "document_refresh_unavailable"),
])
def test_final_continuation_failure_retains_only_allowlisted_fixed_diagnostic(
        monkeypatch, capsys, tmp_path, error, code):
    import json
    import sys

    from infra.gcp.document_refresh_probe import NativeProofDenied
    from infra.gcp.document_refresh_transition import TransitionDenied

    failures = {
        "native": NativeProofDenied("document_native_readonly_transient_exhausted"),
        "transition": TransitionDenied("document_final_preclaim_state_changed"),
        "private": NativeProofDenied("private credential-bearing vendor body"),
    }
    output = tmp_path / "private-result"
    output.mkdir(mode=0o700)
    monkeypatch.setattr(sys, "argv", ["document_refresh.py", "--mode", "resume-final-v2-transition",
        "--output-directory", str(output)])
    monkeypatch.setattr(refresh, "main", lambda: (_ for _ in ()).throw(failures[error]))
    monkeypatch.setattr(refresh, "emit_status", lambda value: None)
    assert refresh.run_cli() == 2
    receipt = json.loads((output / "failure.json").read_text())
    console = json.loads(capsys.readouterr().out)
    assert receipt["fixed_code"] == console["fixed_code"] == code
    assert receipt["automatic_replay"] is False and receipt["state_requires_read_only_reconciliation"] is True
    assert "private credential" not in (output / "failure.json").read_text()


@pytest.mark.parametrize("equals_spelling", [False, True])
def test_final_readback_failure_never_writes_cloud_logging(monkeypatch, capsys, tmp_path, equals_spelling):
    import json
    import sys

    output = tmp_path / "read-only-result"
    output.mkdir(mode=0o700)
    argv = (["document_refresh.py", "--mode=reconcile-final-v2-transition",
             "--output-directory=" + str(output)] if equals_spelling else
            ["document_refresh.py", "--mode", "reconcile-final-v2-transition",
             "--output-directory", str(output)])
    monkeypatch.setattr(sys, "argv", argv)
    monkeypatch.setattr(refresh, "main", lambda: (_ for _ in ()).throw(
        refresh.RefreshDenied("document_final_readback_state_changed")))
    monkeypatch.setattr(refresh, "emit_status",
                        lambda value: pytest.fail("read-only failure must not write a heartbeat"))
    assert refresh.run_cli() == 2
    receipt = json.loads((output / "failure.json").read_text())
    assert receipt["fixed_code"] == "document_final_readback_state_changed"
    assert json.loads(capsys.readouterr().out)["fixed_code"] == receipt["fixed_code"]


@pytest.mark.parametrize("full_source", [False, True])
def test_prior_unknown_run_blocks_new_build_before_any_external_action(monkeypatch, tmp_path, full_source):
    history = tmp_path / "history"
    failed = history / "original-run"
    failed.mkdir(parents=True)
    (failed / "build-create-started.json").write_text('{"automatic_replay":false}')
    monkeypatch.setattr(refresh, "candidate", lambda *args, **kw: pytest.fail("Unknown run must not start another Build"))
    monkeypatch.setattr(refresh, "full_source_candidate", lambda *args, **kw: pytest.fail("Unknown source run must not start another Build"))
    with pytest.raises(refresh.RefreshDenied, match="prior_unknown_reconcile_only"):
        refresh.refresh(tmp_path / "new", publish=True, now=NOW, history=history, full_source=full_source)


@pytest.mark.parametrize("full_source", [False, True])
@pytest.mark.parametrize("nonce", [None, "f" * 32])
def test_refresh_selects_one_profile_and_passes_exact_candidate_to_native_transition(
    monkeypatch, tmp_path, full_source, nonce
):
    calls = []
    image = refresh.REPOSITORY + "@sha256:" + "a" * 64
    build = {"immutable_image": image, "nonce": nonce or "b" * 32,
             "kind": refresh.FULL_SOURCE_KIND if full_source else "native_cloud_build"}
    chosen = "full_source_candidate" if full_source else "candidate"
    forbidden = "candidate" if full_source else "full_source_candidate"

    def prepare(directory, history, selected, **kwargs):
        assert directory == tmp_path and history == tmp_path / "history" and selected == "hirewiz-doc-six-" + "c" * 12
        calls.append("recovery")
        return nonce

    def candidate(directory, *, publish, now, **kwargs):
        assert directory == tmp_path and publish is True and now == NOW
        assert kwargs == ({"nonce": nonce} if nonce is not None else {})
        calls.append(chosen)
        return {"candidate_registry_digest": image, "build_identity": build}

    def successor(actual_image, actual_build, journal):
        assert actual_image == image and actual_build is build and journal == tmp_path / "native-proof"
        calls.append("native_ten")
        return SimpleNamespace(definition_expires_at="2026-10-25T06:24:00+00:00")

    def transition(actual_image, actual_build, native_journal, journal, **kwargs):
        assert actual_image == image and actual_build is build
        assert native_journal == tmp_path / "native-proof" and journal == tmp_path / "transition"
        calls.append("transition")
        return {"status": "document_refresh_serving", "admission_restored": True,
                "prior_image": refresh.RENDERER_BASE}

    probe = SimpleNamespace(Clients=lambda: SimpleNamespace(preflight=lambda: calls.append("preflight"),
        close=lambda: calls.append("preflight_closed")), run_successor=successor)
    modules = {"document_refresh_recovery": SimpleNamespace(prepare=prepare),
               "document_refresh_probe": probe,
               "document_refresh_transition": SimpleNamespace(transition=transition)}
    monkeypatch.setattr(refresh, "infra", modules.__getitem__)
    monkeypatch.setattr(refresh, chosen, candidate)
    monkeypatch.setattr(refresh, forbidden, lambda *args, **kwargs: pytest.fail("A second/old-overlay build is forbidden"))
    monkeypatch.setattr(refresh, "native_http", lambda: SimpleNamespace(close=lambda: calls.append("http_closed")))
    result = refresh.refresh(tmp_path, publish=True, now=NOW, history=tmp_path / "history",
        recover_failed_native_job="hirewiz-doc-six-" + "c" * 12, full_source=full_source)
    assert result["status"] == "document_refresh_serving"
    assert calls == ["recovery", "preflight", "preflight_closed", chosen, "native_ten", "transition", "http_closed"]


def test_full_source_unknown_native_outcome_never_builds_or_sends_twice(monkeypatch, tmp_path):
    calls = []
    build = {"nonce": "a" * 32}

    def candidate(*args, **kwargs):
        calls.append("one_build")
        return {"candidate_registry_digest": "exact-image", "build_identity": build}

    def unknown(image, actual, journal):
        assert actual is build
        calls.append("one_native_attempt")
        raise RuntimeError("Synthetic unknown native outcome")

    probe = SimpleNamespace(Clients=lambda: SimpleNamespace(preflight=lambda: None, close=lambda: None),
                            run_successor=unknown)
    modules = {"document_refresh_recovery": SimpleNamespace(prepare=lambda *args, **kwargs: None),
               "document_refresh_probe": probe}
    monkeypatch.setattr(refresh, "infra", modules.__getitem__)
    monkeypatch.setattr(refresh, "full_source_candidate", candidate)
    monkeypatch.setattr(refresh, "candidate", lambda *args, **kwargs: pytest.fail("No overlay retry"))
    with pytest.raises(RuntimeError, match="Synthetic unknown"):
        refresh.refresh(tmp_path, publish=True, now=NOW, full_source=True)
    assert calls == ["one_build", "one_native_attempt"]


@pytest.mark.parametrize("nonce", [None, "f" * 32])
def test_full_source_candidate_passes_reserved_recovery_nonce_to_exact_native_builder(monkeypatch, tmp_path, nonce):
    calls = []
    monkeypatch.setattr(refresh, "full_source_snapshot", lambda: (
        {refresh.FULL_SOURCE_DOCKERFILE: b"synthetic recipe"}, {}, "a" * 64))

    def native(directory, recipe, **kwargs):
        assert directory == tmp_path and recipe == b"synthetic recipe"
        assert kwargs == {"full_source": True, **({"nonce": nonce} if nonce is not None else {})}
        calls.append("one_full_source_build")
        raise RuntimeError("Synthetic stop before Docker or provider activity")

    monkeypatch.setattr(refresh, "native_build", native)
    with pytest.raises(RuntimeError, match="Synthetic stop"):
        refresh.full_source_candidate(tmp_path, publish=True, now=NOW, nonce=nonce)
    assert calls == ["one_full_source_build"]


def test_full_source_cli_selects_same_pipeline_with_history_and_recovery(monkeypatch, tmp_path):
    import sys

    output, history = tmp_path / "new", tmp_path / "history"
    calls = []

    def pipeline(directory, **kwargs):
        assert directory == output and kwargs["full_source"] is True and kwargs["publish"] is True
        assert kwargs["history"] == history and kwargs["recover_failed_native_job"] == "hirewiz-doc-six-" + "a" * 12
        calls.append("pipeline")
        return {"status": "synthetic_only"}

    monkeypatch.setattr(sys, "argv", ["document_refresh.py", "--mode", "full-source-refresh", "--publish-candidate",
        "--output-directory", str(output), "--prior-history-directory", str(history),
        "--recover-failed-native-job", "hirewiz-doc-six-" + "a" * 12])
    monkeypatch.setattr(refresh, "refresh", pipeline)
    monkeypatch.setattr(refresh, "emit_status", lambda value: calls.append("status"))
    refresh.main()
    assert calls == ["pipeline", "status"]


def test_prior_native_journal_and_existing_once_markers_are_retained_byte_exact(tmp_path):
    source = tmp_path / "prior"
    source.mkdir(mode=0o700)
    original = b'{"actual":"unchanged native ACK"}\n'
    for name in ("binding.json", "create.started.json", "run.acknowledged.json", "delete.started.json"):
        (source / name).write_bytes(original)
        (source / name).chmod(0o600)
    copied = refresh.retain_prior_journal(source, tmp_path / "current-retirement")
    assert {p.name: p.read_bytes() for p in copied.iterdir()} == {p.name: original for p in source.iterdir()}
    assert (copied / "delete.started.json").stat().st_mode & 0o777 == 0o600
    with pytest.raises(FileExistsError):
        refresh.write_new(copied / "delete.started.json", {"resend": True})


def test_successor_monitor_uses_fresh_native_immutable_receipt_without_hourly_large_image_pull(monkeypatch):
    value = service()
    image = refresh.REPOSITORY + "@sha256:" + "f" * 64
    value["template"]["containers"][0]["image"] = image
    selected = SimpleNamespace(image=image, definition_expires_at=(NOW + timedelta(hours=23)).isoformat())
    import sys
    monkeypatch.setitem(sys.modules, "document_refresh_probe", SimpleNamespace(
        verify_current_successor=lambda actual_image, metadata: selected))
    monkeypatch.setattr(refresh, "native_http", lambda: SimpleNamespace(close=lambda: None))
    monkeypatch.setattr(refresh, "native_json", lambda *args: value)
    monkeypatch.setattr(refresh, "command", lambda *args, **kwargs: pytest.fail("No hourly image download after native proof"))
    result = refresh.monitor(now=NOW)
    assert result["definitions"]["status"] == "refresh_required"
    assert result["definitions"]["seconds_remaining"] == 23 * 3600
    assert result["definitions"]["source"] == "fresh_native_immutable_image_receipt"


def test_workflow_keeps_same_run_different_attempt_artifacts_separate(tmp_path, monkeypatch):
    import os
    import shutil
    import subprocess
    import sys

    import yaml

    workflow = yaml.safe_load((refresh.ROOT / ".github/workflows/document-refresh.yml").read_text())
    step = next(row for row in workflow["jobs"]["maintenance"]["steps"]
                if row.get("name") == "Retrieve prior owned native proof journals")
    tools = tmp_path / "tools"
    tools.mkdir()
    (tools / "python").symlink_to(sys.executable)
    gh = tools / "gh"
    gh.write_text("""#!/bin/bash
if [ "$1" = api ]; then
  printf '42\t1001\tdocument-refresh-refresh-42-1\tmain\n42\t1002\tdocument-refresh-refresh-42-2\tmain\n'
  exit 0
fi
while [ "$#" -gt 0 ]; do
  case "$1" in
    --name) artifact="$2"; shift 2 ;;
    --dir) destination="$2"; shift 2 ;;
    *) shift ;;
  esac
done
mkdir -p "$destination"
printf '%s' "$artifact" > "$destination/observed"
""")
    gh.chmod(0o700)
    history = tmp_path / "history"
    env = {**os.environ, "PATH": str(tools) + os.pathsep + os.environ["PATH"],
           "HISTORY_DIRECTORY": str(history), "RUNNER_TEMP": str(tmp_path),
           "GITHUB_REF_NAME": "main", "GITHUB_REPOSITORY": "synthetic/owned"}
    result = subprocess.run([shutil.which("bash"), "-c", step["run"]], env=env, capture_output=True, timeout=10)
    assert result.returncode == 0, result.stderr.decode()
    assert (history / "1001/observed").read_text() == "document-refresh-refresh-42-1"
    assert (history / "1002/observed").read_text() == "document-refresh-refresh-42-2"
    assert {path.name for path in history.iterdir()} == {"1001", "1002"}


def test_workflow_initializes_runner_paths_after_job_expression_validation(tmp_path):
    import shutil
    import subprocess

    import yaml

    workflow = yaml.safe_load((refresh.ROOT / ".github/workflows/document-refresh.yml").read_text())
    job = workflow["jobs"]["maintenance"]
    # GitHub rejects runner context in jobs.<job>.env before any job is created.
    assert all("runner." not in str(value) for value in job["env"].values())
    first = job["steps"][0]
    assert first["name"] == "Initialize private maintenance paths"
    env_file = tmp_path / "github-env"
    runner_temp = tmp_path / "private runner temp"
    runner_temp.mkdir()
    result = subprocess.run([shutil.which("bash"), "-eu", "-c", first["run"]], env={
        "RUNNER_TEMP": str(runner_temp), "GITHUB_ENV": str(env_file),
        "GITHUB_RUN_ID": "42", "GITHUB_RUN_ATTEMPT": "2",
    }, capture_output=True, timeout=5)
    assert result.returncode == 0, result.stderr.decode()
    assert env_file.read_text().splitlines() == [
        f"RESUME_DIRECTORY={runner_temp}/document-refresh-original-39901631586",
        f"TRANSITION_DIRECTORY={runner_temp}/document-refresh-original-40771705772",
        f"PARTIAL_DIRECTORY={runner_temp}/document-refresh-original-40781707554",
        f"FINAL_DIRECTORY={runner_temp}/document-refresh-original-40788512340",
        f"READBACK_DIRECTORY={runner_temp}/document-refresh-original-40805300453",
        f"HISTORY_DIRECTORY={runner_temp}/document-refresh-history",
        f"OUTPUT_DIRECTORY={runner_temp}/document-refresh-42-2",
    ]
    assert not list(runner_temp.iterdir())  # Controller owns fresh directory creation.
    artifact = next(step for step in job["steps"] if step.get("name") == "Retain bounded maintenance evidence")
    assert artifact["if"] == "always() && env.OUTPUT_DIRECTORY != ''"


def test_full_source_workflow_reuses_trusted_scope_and_retained_history_without_ref_bypass():
    import yaml

    workflow = yaml.safe_load((refresh.ROOT / ".github/workflows/document-refresh.yml").read_text())
    trigger = workflow.get("on", workflow.get(True))
    assert trigger["workflow_dispatch"]["inputs"]["mode"]["options"] == [
        "monitor", "refresh", "full-source-refresh", "resume-full-source-refresh",
        "resume-expired-v2-transition", "resume-partial-v2-transition", "resume-final-v2-transition",
        "reconcile-final-v2-transition"]
    job = workflow["jobs"]["maintenance"]
    assert job["if"] == (
        "github.repository == 'Harshil4442/ai-resume-copilot' && github.ref == format('refs/heads/{0}', github.event.repository.default_branch)")
    assert job["environment"] == "document-maintenance"
    assert workflow["permissions"] == {"contents": "read", "id-token": "write", "actions": "read"}
    auth = next(step for step in job["steps"] if step.get("uses", "").startswith("google-github-actions/auth@"))
    assert auth["with"] == {
        "workload_identity_provider": "${{ vars.DOCUMENT_REFRESH_WIF_PROVIDER }}",
        "service_account": "${{ vars.DOCUMENT_REFRESH_SERVICE_ACCOUNT }}"}
    candidate = next(step for step in job["steps"] if step.get("name") ==
                     "Build full-source inspector, prove native cases and transition exact pins")
    assert candidate["if"] == "env.REFRESH_MODE == 'full-source-refresh'"
    assert "--prior-history-directory" in candidate["run"] and "--recover-failed-native-job" in candidate["run"]
    history = next(step for step in job["steps"] if step.get("name") == "Retrieve prior owned native proof journals")
    assert history["if"] == "env.REFRESH_MODE == 'refresh' || env.REFRESH_MODE == 'full-source-refresh' || env.REFRESH_MODE == 'resume-full-source-refresh'"
    assert 'startswith("document-refresh-refresh-") or startswith("document-refresh-full-source-refresh-")' in history["run"]
    assert 'startswith("document-refresh-full-source-candidate-' not in history["run"]
    retained = next(step for step in job["steps"] if step.get("name") ==
                    "Retrieve the exact failed V2 transition journal")
    assert retained["if"] == "env.REFRESH_MODE == 'resume-expired-v2-transition'"
    assert "12852818465" in retained["run"] and "40771705772" in retained["run"]
    assert "sha256:39f09c28b9a745041b3052c8604fb1a420622de291ff6b2275fa9b4abdefb144" in retained["run"]
    continuation = next(step for step in job["steps"] if step.get("name") ==
                        "Read back completed closes and continue the exact V2 rescue")
    assert continuation["if"] == "env.REFRESH_MODE == 'resume-expired-v2-transition'"
    assert "--mode resume-expired-v2-transition" in continuation["run"]
    assert "--publish-candidate" not in continuation["run"]
    partial_artifact = next(step for step in job["steps"] if step.get("name") ==
                            "Retrieve the exact partially completed V2 continuation")
    assert partial_artifact["if"] == "env.REFRESH_MODE == 'resume-partial-v2-transition'"
    assert "12858021573" in partial_artifact["run"] and "40781707554" in partial_artifact["run"]
    assert "sha256:e67577ef8f59495554fba9c27c59748d40fb38e80c9c52d61af69fc3b839cde4" in partial_artifact["run"]
    partial_step = next(step for step in job["steps"] if step.get("name") ==
                        "Reconcile API open and continue only two remaining callers")
    assert partial_step["if"] == "env.REFRESH_MODE == 'resume-partial-v2-transition'"
    assert "--mode resume-partial-v2-transition" in partial_step["run"]
    assert "--publish-candidate" not in partial_step["run"]
    final_artifact = next(step for step in job["steps"] if step.get("name") ==
                          "Retrieve the exact failed second V2 continuation")
    assert final_artifact["if"] == "env.REFRESH_MODE == 'resume-final-v2-transition'"
    assert "12860975525" in final_artifact["run"] and "40788512340" in final_artifact["run"]
    assert "sha256:ffe242705f3bc036de6f41e8cfa4a85f2b447804f40ad926d38edc046fafefe3" in final_artifact["run"]
    final_step = next(step for step in job["steps"] if step.get("name") ==
                      "Reconcile analysis open and continue only the coordinator")
    assert final_step["if"] == "env.REFRESH_MODE == 'resume-final-v2-transition'"
    assert "--mode resume-final-v2-transition" in final_step["run"]
    assert "--publish-candidate" not in final_step["run"]
    readback_artifact = next(step for step in job["steps"] if step.get("name") ==
                             "Retrieve the exact acknowledged third continuation for read-only reconciliation")
    assert readback_artifact["if"] == "env.REFRESH_MODE == 'reconcile-final-v2-transition'"
    assert "12868086986" in readback_artifact["run"] and "40805300453" in readback_artifact["run"]
    assert "sha256:2704e3657731002c1ba008a18cd995d14c227d1a89dbc1f13fb4f03af7df02f6" in readback_artifact["run"]
    readback_step = next(step for step in job["steps"] if step.get("name") ==
                         "Prove the acknowledged coordinator open without another write")
    assert readback_step["if"] == "env.REFRESH_MODE == 'reconcile-final-v2-transition'"
    assert "--mode reconcile-final-v2-transition" in readback_step["run"]
    assert "--publish-candidate" not in readback_step["run"]
    guard = next(step for step in job["steps"] if step.get("name") == "Require explicitly provisioned maintenance identity")
    assert 'test "$REFRESH_MODE" = refresh || test "$REFRESH_MODE" = full-source-refresh' in guard["run"]
    artifact = next(step for step in job["steps"] if step.get("name") == "Retain bounded maintenance evidence")
    assert artifact["with"]["name"] == "document-refresh-${{ env.REFRESH_MODE }}-${{ github.run_id }}-${{ github.run_attempt }}"
    assert artifact["with"]["path"] == "${{ env.OUTPUT_DIRECTORY }}/**/*.json"
    assert artifact["with"]["retention-days"] == 14
    summary = next(step for step in job["steps"] if step.get("name") == "Surface required action")
    assert "six-case" not in summary["run"] and "native V2 ten-case acceptance" in summary["run"]


def test_exact_transition_artifact_refuses_changed_journal_before_native_http(monkeypatch, tmp_path):
    original = tmp_path / "original"
    original.mkdir(mode=0o700)
    for name in ("native-proof", "transition"):
        directory = original / name
        directory.mkdir(mode=0o700)
        marker = directory / "binding.json"
        marker.write_text("{}\n")
        marker.chmod(0o600)
    output = tmp_path / "output"
    output.mkdir(mode=0o700)
    monkeypatch.setattr(refresh, "native_http", lambda: pytest.fail("native HTTP before exact artifact proof"))
    with pytest.raises(refresh.RefreshDenied, match="original_journal_changed"):
        refresh.resume_expired_v2_transition(output, original)


def test_partial_transition_refuses_changed_artifact_before_native_http(monkeypatch, tmp_path):
    original = tmp_path / "original"
    original.mkdir(mode=0o700)
    for name in ("native-proof", "transition"):
        directory = original / name
        directory.mkdir(mode=0o700)
        marker = directory / "binding.json"
        marker.write_text("{}\n")
        marker.chmod(0o600)
    output = tmp_path / "output"
    output.mkdir(mode=0o700)
    monkeypatch.setattr(refresh, "native_http", lambda: pytest.fail("native HTTP before exact artifact proof"))
    with pytest.raises(refresh.RefreshDenied, match="source_tree_changed"):
        refresh.resume_partial_v2_transition(output, original)


def test_final_transition_refuses_changed_artifact_before_native_http(monkeypatch, tmp_path):
    original = tmp_path / "original"
    original.mkdir(mode=0o700)
    for name in ("native-proof", "source-transition", "partial-transition"):
        directory = original / name
        directory.mkdir(mode=0o700)
        marker = directory / "binding.json"
        marker.write_text("{}\n")
        marker.chmod(0o600)
    output = tmp_path / "output"
    output.mkdir(mode=0o700)
    monkeypatch.setattr(refresh, "native_http", lambda: pytest.fail("native HTTP before exact artifact proof"))
    with pytest.raises(refresh.RefreshDenied, match="source_tree_changed"):
        refresh.resume_final_v2_transition(output, original)


@pytest.mark.parametrize("recovery", ["", "$(touch SHOULD_NOT_EXIST) ; exact job data"])
def test_full_source_workflow_sends_only_reviewed_full_pipeline_argv(tmp_path, recovery):
    import json
    import os
    import subprocess
    import sys

    import yaml

    workflow = yaml.safe_load((refresh.ROOT / ".github/workflows/document-refresh.yml").read_text())
    step = next(row for row in workflow["jobs"]["maintenance"]["steps"] if row.get("name") ==
                "Build full-source inspector, prove native cases and transition exact pins")
    binary = tmp_path / "backend/.venv/bin/python"
    binary.parent.mkdir(parents=True)
    binary.write_text("#!" + sys.executable + "\nimport json,os,sys\n"
                      "from pathlib import Path\nPath(os.environ['CAPTURE_ARGS']).write_text(json.dumps(sys.argv[1:]))\n")
    binary.chmod(0o700)
    capture = tmp_path / "args.json"
    directory = "private output $(touch SHOULD_NOT_EXIST)"
    result = subprocess.run(["/bin/bash", "-eu", "-c", step["run"]], cwd=tmp_path,
        env={**os.environ, "OUTPUT_DIRECTORY": directory, "HISTORY_DIRECTORY": "private history",
             "RECOVER_FAILED_NATIVE_JOB": recovery, "CAPTURE_ARGS": str(capture)},
        capture_output=True, timeout=5)
    assert result.returncode == 0 and not (tmp_path / "SHOULD_NOT_EXIST").exists()
    expected = ["backend/scripts/document_refresh.py", "--mode", "full-source-refresh", "--publish-candidate",
                "--prior-history-directory", "private history", "--output-directory", directory]
    assert json.loads(capture.read_bytes()) == expected + (["--recover-failed-native-job", recovery] if recovery else [])


def test_retained_candidate_workflow_sends_only_fixed_resume_argv(tmp_path):
    import json
    import os
    import subprocess
    import sys

    import yaml
    from infra.gcp import document_renderer_profiles as profile

    workflow = yaml.safe_load((refresh.ROOT / ".github/workflows/document-refresh.yml").read_text())
    steps = workflow["jobs"]["maintenance"]["steps"]
    step = next(row for row in steps if row.get("name") ==
                "Reverify the retained candidate and continue once without a new Build")
    retrieve = next(row for row in steps if row.get("name") == "Retrieve the exact retained already-built candidate")
    assert step["if"] == retrieve["if"] == "env.REFRESH_MODE == 'resume-full-source-refresh'"
    assert "gh run download 39901631586" in retrieve["run"]
    assert "document-refresh-full-source-refresh-39901631586-1" in retrieve["run"]
    binary = tmp_path / "backend/.venv/bin/python"
    binary.parent.mkdir(parents=True)
    binary.write_text("#!" + sys.executable + "\nimport json,os,sys\n"
                      "from pathlib import Path\nPath(os.environ['CAPTURE_ARGS']).write_text(json.dumps(sys.argv[1:]))\n")
    binary.chmod(0o700)
    capture = tmp_path / "args.json"
    original = "original $(touch SHOULD_NOT_EXIST)"
    result = subprocess.run(["/bin/bash", "-eu", "-c", step["run"]], cwd=tmp_path,
        env={**os.environ, "RESUME_DIRECTORY": original, "HISTORY_DIRECTORY": "private history",
             "OUTPUT_DIRECTORY": "private output", "CAPTURE_ARGS": str(capture)}, capture_output=True, timeout=5)
    assert result.returncode == 0 and not (tmp_path / "SHOULD_NOT_EXIST").exists()
    assert json.loads(capture.read_bytes()) == ["backend/scripts/document_refresh.py", "--mode", "resume-full-source-refresh",
        "--publish-candidate", "--resume-candidate-directory", original, "--resume-manifest-sha256", profile.PARTIAL_MANIFEST_SHA256,
        "--prior-history-directory", "private history", "--output-directory", "private output"]
