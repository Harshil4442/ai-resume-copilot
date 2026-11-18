"""Read-only custody checks before normalizing V5 inventory continuation history."""
from __future__ import annotations

import os
import re
import stat
from pathlib import Path

from backend.scripts import document_refresh as build
from infra.gcp import document_source_candidate_v5 as profile
from infra.gcp import document_source_readback_resume as custody
from infra.gcp import document_v5_inventory_resume as resume

PREFIX = "document-refresh-resume-v5-inventory-"


def _need(condition: object, reason: str) -> None:
    build.need(condition, "document_v5_inventory_history_" + reason)


def _selected(history: Path, rows: Path) -> list[tuple[str, str, str, str]]:
    recovery = build.infra("document_refresh_recovery")
    recovery.private(history, build)
    info = rows.lstat()
    _need(rows.is_absolute() and rows.resolve() == rows and stat.S_ISREG(info.st_mode)
          and info.st_uid == os.getuid() and stat.S_IMODE(info.st_mode) == 0o600
          and info.st_nlink == 1 and info.st_size <= 4096, "selection_refused")
    selected: list[tuple[str, str, str, str]] = []
    for line in rows.read_text().splitlines():
        fields = line.split("\t")
        _need(len(fields) == 4, "selection_refused")
        run_id, artifact_id, name, branch = fields
        _need(branch == "main" and 0 < len(name) <= 128 and all(
            re.fullmatch(r"[1-9][0-9]{0,18}", value) for value in (run_id, artifact_id)), "selection_refused")
        selected.append((run_id, artifact_id, name, branch))
    entries = {entry.name for entry in history.iterdir()}
    _need(len(selected) <= 4 and len({row[1] for row in selected}) == len(selected)
          and entries == {row[1] for row in selected}, "selection_refused")
    return selected


def _completed_archive(path: Path, row: tuple[str, str, str, str]) -> tuple[dict[str, bytes], dict]:
    run_id, artifact_id, name, _ = row
    run = custody.github("actions/runs/" + run_id)
    artifact = custody.github("actions/artifacts/" + artifact_id)
    digest = artifact.get("digest", "")
    head = run.get("head_sha", "")
    _need(name == PREFIX + run_id + "-1" and run.get("id") == int(run_id)
          and run.get("run_attempt") == 1 and run.get("head_branch") == "main"
          and run.get("path") == custody.WORKFLOW and run.get("event") == "workflow_dispatch"
          and run.get("repository", {}).get("full_name") == custody.REPOSITORY
          and run.get("status") == "completed" and run.get("conclusion") == "success"
          and type(head) is str and re.fullmatch(r"[a-f0-9]{40}", head), "continuation_run_refused")
    linked = artifact.get("workflow_run", {})
    _need(artifact.get("id") == int(artifact_id) and artifact.get("name") == name and artifact.get("expired") is False
          and linked.get("id") == int(run_id) and linked.get("head_sha") == head and linked.get("head_branch") == "main"
          and type(digest) is str and re.fullmatch(r"sha256:[a-f0-9]{64}", digest)
          and type(artifact.get("size_in_bytes")) is int and 0 < artifact["size_in_bytes"] <= custody.MAX_BYTES,
          "continuation_artifact_refused")
    raw = custody.github("actions/artifacts/" + artifact_id + "/zip", binary=True)
    files = custody.read_tree(path)
    _need("sha256:" + custody.sha(raw) == digest and custody.archive(raw) == files,
          "continuation_archive_changed")
    return files, run


def _completed_manifest(files: dict[str, bytes], run: dict) -> dict:
    manifest = build.decode(files[resume.MANIFEST_FILE])
    result = build.decode(files["result.json"])
    _need(type(manifest) is dict and type(result) is dict, "continuation_result_refused")
    body, acknowledgment = manifest.get("claim_body"), manifest.get("claim_acknowledgment")
    _need(set(manifest) == {
        "version", "kind", "claim_body", "claim_acknowledgment", "result_sha256", "automatic_replay"
    } and type(manifest["version"]) is int and manifest["version"] == 1
        and manifest["kind"] == "completed_v5_inventory_continuation_v1"
        and manifest["automatic_replay"] is False and type(body) is dict and type(acknowledgment) is dict
        and manifest["result_sha256"] == custody.sha(custody.canonical(result))
        and result.get("status") == "document_refresh_serving" and result.get("admission_restored") is True,
        "continuation_result_refused")

    head = run["head_sha"]
    profile.git(build.ROOT, "merge-base", "--is-ancestor", resume.ORIGINAL_SOURCE, head)
    profile.git(build.ROOT, "merge-base", "--is-ancestor", head, "HEAD")
    sources = {name: custody.sha(profile.git(build.ROOT, "show", f"{head}:{name}")) for name in (
        "infra/gcp/document_v5_inventory_resume.py", custody.WORKFLOW,
    )}
    context = {"run": run["id"], "attempt": 1, "head": head, "path": custody.WORKFLOW,
               "operator_sha256": custody.sha(custody.canonical(sources))}
    claims = resume.CLAIM_DIR + "/"
    _need(run["id"] > resume.ORIGINAL_RUN and body.get("continuation") == context
          and body.get("original_run") == resume.ORIGINAL_RUN
          and body.get("original_artifact") == resume.ORIGINAL_ARTIFACT
          and body.get("original_zip_sha256") == resume.ORIGINAL_ZIP_SHA256
          and body.get("original_tree_sha256") == resume.ORIGINAL_TREE_SHA256
          and body.get("job_uid") == resume.INVENTORY_JOB_UID and body.get("candidate_nonce") == resume.NONCE
          and body.get("automatic_replay") is False and body.get("new_build_or_inventory") is False
          and build.decode(files[claims + "disposition.json"]) == body
          and build.decode(files[claims + "claim.acknowledged.json"]) == acknowledgment
          and build.decode(files[claims + "claim.started.json"]) == {
              "body_sha256": custody.sha(custody.canonical(body)), "automatic_replay": False,
          }, "continuation_claim_changed")
    return manifest


def _verify_consumption(completed: Path, files: dict[str, bytes], complete_files: dict[str, bytes],
                        manifest: dict) -> None:
    original_build, prior, source = custody.modules(build.ROOT)
    recovery = original_build.infra("document_refresh_recovery")
    _need(all(complete_files.get(name) == raw for name, raw in files.items()), "original_copy_changed")
    identity = original_build.decode(files["native-proof/binding.json"])["build_identity"]
    probe = source.SourceProbe(identity, root=build.ROOT, serving_read=True)
    image = identity["immutable_image"]
    wanted = probe.desired(image, probe.handles(identity)["corpus"]["nonce"], "corpus")
    old = recovery.failed_v4_snapshot(completed / "failed-v4-inventory" / "original", original_build)
    clients = prior.Clients()
    try:
        fact = recovery.verify_failed_v4(old, clients, prior, original_build)
        inventory = resume.verify_inventory(clients, prior, files, probe, identity)
        expected = resume.claim_body(
            identity, inventory, original_build.decode(complete_files["continuation-preimage.json"]),
            manifest["claim_body"]["continuation"], recovery.failed_v4_claim_body(fact, resume.NONCE), wanted,
        )
        _need(expected == manifest["claim_body"], "continuation_claim_changed")
        recovery.verify_claim(expected, manifest["claim_acknowledgment"], original_build)
        recovery.verify_completed_v5_consumer(completed, fact, old, clients, prior, original_build)
    finally:
        clients.close()


def normalize(history: Path, rows: Path) -> None:
    """Keep all uncertain history and move only the verified consumed local copy."""
    selected = _selected(history, rows)
    original_id = str(resume.ORIGINAL_ARTIFACT)
    original = next((row for row in selected if row[1] == original_id), None)
    continuations = [row for row in selected if row[2].startswith(PREFIX)]
    final_size = len(selected) - (1 if original is not None and continuations else 0)
    _need(len(continuations) <= 1 and final_size <= 3, "ambiguous_history")
    if not continuations:
        return

    continuation = continuations[0]
    complete = history / continuation[1]
    complete_files, run = _completed_archive(complete, continuation)
    manifest = _completed_manifest(complete_files, run)
    if original is None:
        return

    _need(original == (str(resume.ORIGINAL_RUN), original_id, resume.ORIGINAL_NAME, "main"),
          "original_row_changed")
    partial = history / original_id
    retained = history.parent / ("document-v5-inventory-original-" + original_id)
    _need(not os.path.lexists(retained), "retained_original_already_exists")
    resume.verify_original_metadata()
    files = resume.read_tree(partial)
    _verify_consumption(complete, files, complete_files, manifest)
    _need(resume.read_tree(partial) == files and custody.read_tree(complete) == complete_files,
          "verified_history_changed")
    partial.rename(retained)
