"""Exact failed third-claim reconciliation; synthetic transport, no cloud writes."""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs, unquote, urlsplit

import pytest
import test_document_refresh_transition as fixture

from scripts import document_refresh as refresh


def private_failed_artifact(root: Path, *, bad_receipt: bool = False) -> None:
    """Same 46-file shape as the failed artifact, but deliberately altered bytes."""
    root.mkdir(mode=0o700)
    counts = {"native-proof": 6, "source-transition": 29,
              "partial-transition": 5, "final-transition": 5}
    for directory, count in counts.items():
        target = root / directory
        target.mkdir(mode=0o700)
        for index in range(count):
            path = target / f"marker{index:02}.json"
            path.write_text("{}")
            path.chmod(0o600)
    receipt = {"status": "maintenance_attention_required",
               "fixed_code": "document_transition_readback_changed",
               "state_requires_read_only_reconciliation": True,
               "automatic_replay": False}
    if bad_receipt:
        receipt["fixed_code"] = "document_transition_unverified"
    failure = root / "failure.json"
    failure.write_text(json.dumps(receipt))
    failure.chmod(0o600)


@pytest.mark.parametrize("tamper", ["failure_receipt", "source_tree"])
def test_exact_46_file_artifact_refuses_tamper_before_native_http(monkeypatch, tmp_path, tamper):
    original = tmp_path / "failed-artifact"
    private_failed_artifact(original, bad_receipt=tamper == "failure_receipt")
    output = tmp_path / "output"
    output.mkdir(mode=0o700)
    monkeypatch.setattr(refresh, "native_http", lambda: pytest.fail("native HTTP before artifact proof"))
    with pytest.raises(refresh.RefreshDenied, match=(
            "failure_receipt_changed" if tamper == "failure_receipt" else "source_tree_changed")):
        refresh.reconcile_final_v2_transition(output, original)
    assert not (output / "readback/complete.json").exists()


def failed_after_coordinator_ack(monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
                                 *, short_status: bool = True):
    """Build a real fake one-send history, then retain the failed-run prefix."""
    http, source, previous = fixture.failed_partial_after_analysis_ack(monkeypatch, tmp_path)
    fixture.continue_final(http, source, previous, tmp_path)
    failed = tmp_path / "final-continuation"
    coordinator = fixture.transition.CALLERS[2]
    opened = http.services[coordinator]
    full_revision = opened["latestReadyRevision"]
    # Cloud Run's actual response used the short revision in trafficStatuses.
    opened["trafficStatuses"][0]["revision"] = (
        full_revision.rsplit("/", 1)[-1] if short_status else full_revision)
    opened["trafficStatuses"][0]["type"] = "TRAFFIC_TARGET_ALLOCATION_TYPE_LATEST"
    (failed / ("open." + coordinator + ".verified.json")).unlink()
    (failed / "complete.json").unlink()
    (failed / "owner.lock").unlink()

    readback = fixture.load("document_refresh_final_readback")
    # The synthetic maintenance close has a local generation/LRO; production
    # constants remain pinned by the separate identity test above.
    monkeypatch.setattr(readback.partial, "MAINTENANCE_CLOSE_GENERATION",
                        fixture.partial.MAINTENANCE_CLOSE_GENERATION)
    monkeypatch.setattr(readback.partial, "MAINTENANCE_CLOSE_OPERATION",
                        fixture.partial.MAINTENANCE_CLOSE_OPERATION)
    claim_ack_file = failed / "final-claim.acknowledged.json"
    claim_ack = json.loads(claim_ack_file.read_text())
    claim_ack["generation"] = str(readback.CLAIM_GENERATION)
    claim_ack_file.write_text(json.dumps(claim_ack))
    claim_proof_file = failed / "final-claim.verified.json"
    claim_proof = json.loads(claim_proof_file.read_text())
    claim_proof["acknowledgment"] = claim_ack
    claim_proof_file.write_text(json.dumps(claim_proof))
    raw, _ = http.claim_objects[fixture.final.CLAIM_OBJECT]
    claim_body = json.loads(raw)
    claim_body["maintenance_source_sha256"] = readback.SOURCE_HASHES
    raw = fixture.transition.canonical(claim_body)
    http.claim_objects[fixture.final.CLAIM_OBJECT] = (raw, str(readback.CLAIM_GENERATION))
    claim_started_file = failed / "final-claim.started.json"
    claim_started = json.loads(claim_started_file.read_text())
    claim_started["body_sha256"] = fixture.transition.sha(claim_body)
    claim_started_file.write_text(json.dumps(claim_started))
    claim_proof["body_sha256"] = fixture.transition.sha(claim_body)
    claim_proof_file.write_text(json.dumps(claim_proof))

    open_ack_file = failed / ("open." + coordinator + ".acknowledged.json")
    open_ack = json.loads(open_ack_file.read_text())
    old_operation = open_ack["operation"]
    open_ack["operation"] = readback.COORDINATOR_OPERATION
    open_ack_file.write_text(json.dumps(open_ack))
    http.operations[readback.COORDINATOR_OPERATION] = http.operations.pop(old_operation)

    request = http.request
    def exact_read(uri, *, method, body=None, headers=None, redirections):
        target = urlsplit(uri)
        if (method == "GET" and target.hostname == "storage.googleapis.com"
                and target.path.startswith("/download/storage/v1/")
                and fixture.final.CLAIM_OBJECT in unquote(target.path)):
            assert parse_qs(target.query) == {
                "alt": ["media"], "generation": [str(readback.CLAIM_GENERATION)]}
            return SimpleNamespace(status=200), raw
        return request(uri, method=method, body=body, headers=headers,
                       redirections=redirections)
    http.request = exact_read
    assert {path.name for path in failed.glob("*.json")} == {
        "final-claim.started.json", "final-claim.acknowledged.json",
        "final-claim.verified.json", "open.hirewiz-resume-coordinator.started.json",
        "open.hirewiz-resume-coordinator.acknowledged.json"}
    return readback, http, source, previous, failed


def reconcile(readback, http, source, previous, failed, tmp_path):
    transport = readback.ReadOnlyHttp(http, fixture.transition)
    return readback.reconcile(
        fixture.NEW,
        {"build_id": fixture.transition.RESCUE_BUILD_ID,
         "nonce": fixture.transition.RESCUE_NONCE},
        tmp_path / "native", source, previous, failed,
        tmp_path / "readback",
        source_tree_sha256=readback.JOURNAL_TREE_SHA256,
        http=transport, probe=fixture.probe, t=fixture.transition)


def test_exact_failed_artifact_identity_is_pinned():
    readback = fixture.load("document_refresh_final_readback")
    assert readback.RUN_ID == 40805300453
    assert readback.ARTIFACT_ID == 12868086986
    assert readback.ARTIFACT_DIGEST == (
        "2704e3657731002c1ba008a18cd995d14c227d1a89dbc1f13fb4f03af7df02f6")
    assert readback.JOURNAL_TREE_SHA256 == (
        "7e200c774ae0148965d836a3ad5ff184670b05670bb3f1340f17ff1b81b047a3")
    assert readback.CLAIM_GENERATION == "1793240192529106"
    assert readback.COORDINATOR_OPERATION.endswith(
        "e62b0d5e-4326-4317-954c-aa0bf5f78e50")


@pytest.mark.parametrize(("uri", "method"), [
    ("https://run.googleapis.com/v2/projects/ai-resume-parser-482412/locations/us-central1/services/hirewiz-resume-coordinator", "PATCH"),
    ("https://storage.googleapis.com/upload/storage/v1/b/ai-resume-parser-482412_cloudbuild/o", "POST"),
    ("https://example.invalid/read", "GET"),
])
def test_read_only_transport_refuses_write_or_unreviewed_route(uri, method):
    readback = fixture.load("document_refresh_final_readback")
    calls = []
    delegate = SimpleNamespace(request=lambda *args, **kwargs: calls.append((args, kwargs)))
    transport = readback.ReadOnlyHttp(delegate, fixture.transition)
    with pytest.raises(fixture.transition.TransitionDenied, match="write_route_refused"):
        transport.request(uri, method=method, redirections=0)
    assert calls == []


@pytest.mark.parametrize("short_status", [True, False])
def test_exact_third_claim_and_ready_traffic_reconcile_without_provider_write(
        monkeypatch, tmp_path, short_status):
    readback, http, source, previous, failed = failed_after_coordinator_ack(
        monkeypatch, tmp_path, short_status=short_status)
    prior_writes, prior_claim_posts = len(http.writes), http.claim_posts
    result = reconcile(readback, http, source, previous, failed, tmp_path)
    assert result["read_only_reconciliation"] is True
    assert result["source_run_id"] == readback.RUN_ID
    assert result["source_artifact_id"] == readback.ARTIFACT_ID
    assert result["analysis_and_coordinator_admission_restored"] is True
    assert result["api_remains_maintenance_closed"] is True
    assert len(http.writes) == prior_writes == 11
    assert http.claim_posts == prior_claim_posts == 3
    assert (tmp_path / "readback/complete.json").is_file()


@pytest.mark.parametrize("drift", [
    "claim_generation", "claim_media", "operation_name", "operation_unknown",
    "operation_not_done", "coordinator_traffic", "api_flag", "extra_marker",
])
def test_changed_third_claim_or_lro_or_serving_state_refuses_without_write(
        monkeypatch, tmp_path, drift):
    readback, http, source, previous, failed = failed_after_coordinator_ack(monkeypatch, tmp_path)
    coordinator = fixture.transition.CALLERS[2]
    if drift == "claim_generation":
        path = failed / "final-claim.acknowledged.json"
        changed = json.loads(path.read_text())
        changed["generation"] = str(int(readback.CLAIM_GENERATION) + 1)
        path.write_text(json.dumps(changed))
    elif drift == "claim_media":
        raw, generation = http.claim_objects[fixture.final.CLAIM_OBJECT]
        http.claim_objects[fixture.final.CLAIM_OBJECT] = (raw + b" ", generation)
        request = http.request
        def changed_media(uri, *, method, body=None, headers=None, redirections):
            target = urlsplit(uri)
            if (method == "GET" and target.hostname == "storage.googleapis.com"
                    and target.path.startswith("/download/storage/v1/")
                    and fixture.final.CLAIM_OBJECT in unquote(target.path)):
                return SimpleNamespace(status=200), raw + b" "
            return request(uri, method=method, body=body, headers=headers,
                           redirections=redirections)
        http.request = changed_media
    elif drift == "operation_name":
        path = failed / ("open." + coordinator + ".acknowledged.json")
        changed = json.loads(path.read_text())
        changed["operation"] = fixture.transition.PARENT + "/operations/unrelated"
        path.write_text(json.dumps(changed))
    elif drift == "operation_unknown":
        del http.operations[readback.COORDINATOR_OPERATION]
    elif drift == "operation_not_done":
        request = http.request
        def pending(uri, *, method, body=None, headers=None, redirections):
            if method == "GET" and uri.endswith(readback.COORDINATOR_OPERATION):
                return SimpleNamespace(status=200), json.dumps({
                    "name": readback.COORDINATOR_OPERATION, "done": False}).encode()
            return request(uri, method=method, body=body, headers=headers,
                           redirections=redirections)
        http.request = pending
    elif drift == "coordinator_traffic":
        http.services[coordinator]["trafficStatuses"][0]["revision"] = "unrelated-open"
    elif drift == "api_flag":
        api = http.services[fixture.transition.CALLERS[0]]
        env = fixture.transition.environment(api["template"])
        env["RESUME_DIRECT_UPLOAD_ENABLED"]["value"] = "true"
        api["template"]["containers"][0]["env"] = list(env.values())
    else:
        extra = failed / "open.hirewiz-analysis-worker.started.json"
        extra.write_text("{}")
        extra.chmod(0o600)
    prior_writes, prior_claim_posts = len(http.writes), http.claim_posts
    with pytest.raises((fixture.transition.TransitionDenied,
                        fixture.probe.NativeProofDenied, ValueError, KeyError)):
        reconcile(readback, http, source, previous, failed, tmp_path)
    assert len(http.writes) == prior_writes == 11
    assert http.claim_posts == prior_claim_posts == 3
    assert not (tmp_path / "readback/complete.json").exists()
