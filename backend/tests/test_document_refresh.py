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
        "corpus": [{"case": name, "passed": True, "scan": copy.deepcopy(scan)} for name in (
            "clean_pdf", "clean_docx", "page_counter", "active_pdf", "includetext",
            "eicar_exact_file", "eicar_zip_member", "missing_definitions")],
        "docx_layout_corpus": [{"case": "styled_multipage_fit", "passed": True, "layout": {
            "renderer": {"renderer_version": "reviewed-renderer", "font_manifest_sha256": "a" * 64,
                "policy_sha256": "b" * 64, "image_digest": "local-document-image@sha256:" + "d" * 64},
            "rendered_fonts_sha256": "e" * 64}}, {"case": "rendered_reflow_refusal", "passed": True}],
        "request_clamd_corpus": [{"case": str(i), "daemon_stopped_before_writer": True,
            "native_log_removed": True, "daemon_returncode": 0} for i in range(11)]}


def test_local_proof_keeps_native_acceptance_separate_and_font_exact():
    facts = refresh.proof_contract(proof(), contract(), {"owned-source": "c" * 64}, now=NOW)
    assert facts["local_cases"] == 21
    assert facts["expires_at_utc"] == "2026-10-19T06:24:15+00:00"


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


def test_prior_unknown_run_blocks_new_build_before_any_external_action(monkeypatch, tmp_path):
    history = tmp_path / "history"
    failed = history / "original-run"
    failed.mkdir(parents=True)
    (failed / "build-create-started.json").write_text('{"automatic_replay":false}')
    monkeypatch.setattr(refresh, "candidate", lambda *args, **kw: pytest.fail("Unknown run must not start another Build"))
    with pytest.raises(refresh.RefreshDenied, match="prior_unknown_reconcile_only"):
        refresh.refresh(tmp_path / "new", publish=True, now=NOW, history=history)


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
