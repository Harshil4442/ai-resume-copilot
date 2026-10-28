"""One-time continuation after the second rescue acknowledged analysis open.

The first two claims stay consumed. This path reconciles the completed analysis
operation, then permits only the coordinator open under a third create-only
claim. The emergency maintenance journal still owns the API restoration.
"""
from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlencode

_spec = importlib.util.spec_from_file_location(
    "document_refresh_final_partial", Path(__file__).with_name("document_refresh_partial.py"))
assert _spec is not None and _spec.loader is not None
partial = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(partial)

RUN_ID = 40788512340
ARTIFACT_ID = 12860975525
ARTIFACT_DIGEST = "ffe242705f3bc036de6f41e8cfa4a85f2b447804f40ad926d38edc046fafefe3"
ARTIFACT_HEAD = "cbad4abb39017dcf744ede77ce6bfc2826395257"
JOURNAL_TREE_SHA256 = "27283c86c7f723fcdb41175c448311233ddb78c478397c600b42b72d4e2b6c84"
CLAIM_OBJECT = ("hirewiz-document-refresh-source/5436da554bc5437cadccd296f3489bdd/"
                f"final-transition-{RUN_ID}-{ARTIFACT_ID}.json")


def _claim_media(t: Any, http: Any, object_name: str, ack: dict[str, Any],
                 proof: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    t.need(ack.get("name") == object_name and ack.get("bucket") == t.PROJECT + "_cloudbuild"
           and type(ack.get("generation")) is str and ack["generation"].isdigit()
           and proof.get("acknowledgment") == ack
           and type(proof.get("body_sha256")) is str and len(proof["body_sha256"]) == 64,
           "document_final_prior_claim_changed")
    url = ("https://storage.googleapis.com/download/storage/v1/b/" + ack["bucket"] + "/o/"
           + quote(object_name, safe="") + "?"
           + urlencode({"alt": "media", "generation": ack["generation"]}))
    response, raw = http.request(url, method="GET", redirections=0)
    t.need(response.status == 200 and hashlib.sha256(raw).hexdigest() == proof["body_sha256"],
           "document_final_prior_claim_changed")
    value = json.loads(raw)
    t.need(type(value) is dict, "document_final_prior_claim_changed")
    return proof["body_sha256"], value


def second_claim(t: Any, http: Any, journal: Path, first_sha: str,
                 acceptance_sha: str) -> tuple[str, dict[str, Any]]:
    ack = partial.source_json(journal, "partial-claim.acknowledged.json", t)
    proof = partial.source_json(journal, "partial-claim.verified.json", t)
    started = partial.source_json(journal, "partial-claim.started.json", t)
    digest, value = _claim_media(t, http, partial.CLAIM_OBJECT, ack, proof)
    t.need(started == {"object": partial.CLAIM_OBJECT, "body_sha256": digest,
                       "automatic_replay": False}
           and value.get("kind") == "exact_partially_completed_v2_scanner_transition_resume_v1"
           and value.get("source_run_id") == partial.RUN_ID
           and value.get("source_artifact_id") == partial.ARTIFACT_ID
           and value.get("source_artifact_digest") == "sha256:" + partial.ARTIFACT_DIGEST
           and value.get("source_journal_tree_sha256") == partial.JOURNAL_TREE_SHA256
           and value.get("original_claim_body_sha256") == first_sha
           and value.get("maintenance_plan_sha256") == partial.MAINTENANCE_PLAN_SHA256
           and value.get("maintenance_close_revision") == partial.MAINTENANCE_CLOSE_REVISION
           and value.get("native_acceptance_sha256") == acceptance_sha
           and value.get("automatic_replay") is False
           and set(value.get("preimages", {})) == set(t.SERVICES),
           "document_final_second_claim_changed")
    return digest, value


def _preimage(t: Any, claim: dict[str, Any], name: str, state: dict[str, Any]) -> None:
    expected = claim["preimages"].get(name)
    t.need(type(expected) is dict and expected == {
        "uid": state["uid"], "generation": str(state["generation"]),
        "etag_sha256": hashlib.sha256(state["etag"].encode()).hexdigest(),
        "template_sha256": t.sha(state["template"]),
    }, "document_final_second_claim_preimage_changed")


def analysis_open(t: Any, run: Any, original: Path, previous: Path,
                  captured: dict[str, Any], second: dict[str, Any]) -> dict[str, Any]:
    name = t.CALLERS[1]
    pins_started = partial.source_json(original, "pins." + name + ".started.json", t)
    pins_proof = partial.source_json(original, "pins." + name + ".verified.json", t)
    _, pins_operation = partial.acknowledged_operation(t, run.http, original, "pins", name, verified=True)
    prior = pins_operation["response"]
    _preimage(t, second, name, prior)
    started, operation = partial.acknowledged_operation(t, run.http, previous, "open", name, verified=False)
    t.need(started["prior_generation"] == pins_proof["generation"]
           and started["service_uid"] == pins_started["service_uid"]
           and str(prior["generation"]) == pins_proof["generation"]
           and prior["latestReadyRevision"] == pins_proof["revision"],
           "document_final_analysis_chain_changed")
    revision = run.revision(pins_proof["revision"])
    keys = tuple(key for key in prior["template"] if key != "revision")
    t.need(all(key in revision for key in keys), "document_final_analysis_pins_changed")
    pins_template = {key: copy.deepcopy(revision[key]) for key in keys}
    pins_template["revision"] = pins_proof["revision"].rsplit("/", 1)[-1]
    t.need(t.sha(pins_template) == pins_started["template_sha256"],
           "document_final_analysis_pins_changed")
    expected = t.mutate_template({"template": pins_template}, name, "open", t.RESCUE_NONCE,
                                 t.RESCUE_IMAGE, {}, captured[name])
    body = {"name": prior["name"], "etag": prior["etag"], "template": expected,
            "traffic": [{"type": "TRAFFIC_TARGET_ALLOCATION_TYPE_LATEST", "percent": 100}]}
    t.need(t.sha(expected) == started["template_sha256"]
           and t.sha(body) == started["request_sha256"],
           "document_final_analysis_request_changed")
    current = run.service(name)
    run.ready(name, current, uid=started["service_uid"])
    target = t.PARENT + "/services/" + name + "/revisions/" + expected["revision"]
    normalized = copy.deepcopy(current["template"])
    normalized["revision"] = normalized["revision"].rsplit("/", 1)[-1]
    response = operation["response"]
    ignored = {"@type", "template", "traffic", "labels", "etag", "generation", "observedGeneration",
               "updateTime", "lastModifier", "reconciling", "terminalCondition", "conditions",
               "latestReadyRevision", "latestCreatedRevision", "trafficStatuses", "urls"}
    t.need(str(current["generation"]) == str(int(started["prior_generation"]) + 1)
           and current["latestReadyRevision"] == target
           and current["latestCreatedRevision"] == target
           and normalized == expected
           and current.get("labels", {}) == prior.get("labels", {})
           and current.get("traffic") == body["traffic"]
           and {key: value for key, value in current.items() if key not in ignored}
               == {key: value for key, value in prior.items() if key not in ignored}
           and response.get("name") == current["name"]
           and response.get("uid") == current["uid"]
           and str(response.get("generation")) == str(current["generation"])
           and response.get("latestCreatedRevision") == target
           and response.get("latestReadyRevision") in {pins_proof["revision"], target},
           "document_final_analysis_open_changed")
    opened = run.revision(target)
    t.need(all(opened.get(key) == expected[key] for key in keys)
           and any(row.get("type") == "Ready" and row.get("state") == "CONDITION_SUCCEEDED"
                   for row in opened.get("conditions", [])),
           "document_final_analysis_open_revision_changed")
    env = t.environment(current["template"])
    t.need(all(env.get(flag) == captured[name][flag] for flag in t.PAUSE[name]),
           "document_final_analysis_admission_changed")
    return current


def claim(t: Any, http: Any, owner: Any, acceptance: Any, preimages: dict[str, dict[str, Any]],
          first_sha: str, second_sha: str) -> None:
    bucket = t.PROJECT + "_cloudbuild"
    root = Path(__file__).resolve().parents[2]
    sources = ("backend/scripts/document_refresh.py", "infra/gcp/document_refresh_final.py",
               "infra/gcp/document_refresh_partial.py", "infra/gcp/document_refresh_transition.py",
               "infra/gcp/document_refresh_auth.py")
    hashes = {name: hashlib.sha256((root / name).read_bytes()).hexdigest() for name in sources}
    body = {"version": 1, "kind": "exact_second_failed_v2_scanner_transition_resume_v1",
            "source_run_id": RUN_ID, "source_artifact_id": ARTIFACT_ID,
            "source_artifact_digest": "sha256:" + ARTIFACT_DIGEST,
            "source_journal_tree_sha256": JOURNAL_TREE_SHA256,
            "original_claim_body_sha256": first_sha,
            "second_claim_body_sha256": second_sha,
            "maintenance_plan_sha256": partial.MAINTENANCE_PLAN_SHA256,
            "maintenance_close_revision": partial.MAINTENANCE_CLOSE_REVISION,
            "native_acceptance_sha256": t.sha(asdict(acceptance)),
            "preimages": {name: {"uid": row["uid"], "generation": str(row["generation"]),
                                   "etag_sha256": hashlib.sha256(row["etag"].encode()).hexdigest(),
                                   "template_sha256": t.sha(row["template"])}
                          for name, row in preimages.items()},
            "maintenance_source_sha256": hashes, "automatic_replay": False}
    t.need(set(preimages) == set(t.SERVICES), "document_final_claim_preimages_changed")
    metadata_uri = "https://storage.googleapis.com/storage/v1/b/" + bucket + "/o/" + quote(CLAIM_OBJECT, safe="")
    response, _ = http.request(metadata_uri, method="GET", redirections=0)
    t.need(response.status == 404, "document_final_claim_exists_or_unknown_reconcile_only")
    owner.write("final-claim.started.json", {"object": CLAIM_OBJECT, "body_sha256": t.sha(body),
                                              "automatic_replay": False})
    response, raw = http.request("https://storage.googleapis.com/upload/storage/v1/b/" + bucket + "/o?"
        + urlencode({"uploadType": "media", "name": CLAIM_OBJECT, "ifGenerationMatch": "0"}),
        method="POST", body=t.canonical(body), headers={"Content-Type": "application/json"}, redirections=0)
    t.need(response.status in (200, 201), "document_final_claim_unknown_reconcile_only")
    value = json.loads(raw)
    t.need(type(value) is dict and value.get("name") == CLAIM_OBJECT and value.get("bucket") == bucket
           and type(value.get("generation")) is str and value["generation"].isdigit(),
           "document_final_claim_ack_changed")
    ack = {key: value[key] for key in ("name", "bucket", "generation")}
    owner.write("final-claim.acknowledged.json", ack)
    response, raw = http.request("https://storage.googleapis.com/download/storage/v1/b/" + bucket + "/o/"
        + quote(CLAIM_OBJECT, safe="") + "?" + urlencode({"alt": "media", "generation": ack["generation"]}),
        method="GET", redirections=0)
    t.need(response.status == 200 and raw == t.canonical(body), "document_final_claim_readback_changed")
    owner.write("final-claim.verified.json", {"body_sha256": t.sha(body), "acknowledgment": ack})


def resume(image: str, build: dict[str, Any], native: Path, original: Path, previous: Path,
           journal: Path, *, source_tree_sha256: str, http: Any, probe: Any, t: Any) -> dict[str, Any]:
    t.need(source_tree_sha256 == JOURNAL_TREE_SHA256 and image == t.RESCUE_IMAGE
           and build.get("build_id") == t.RESCUE_BUILD_ID and build.get("nonce") == t.RESCUE_NONCE,
           "document_final_exact_artifact_required")
    t.need({path.name for path in previous.glob("*.json")} == {
        "partial-claim.started.json", "partial-claim.acknowledged.json", "partial-claim.verified.json",
        "open.hirewiz-analysis-worker.started.json", "open.hirewiz-analysis-worker.acknowledged.json"},
        "document_final_previous_journal_changed")
    owner = t.Journal(journal)
    try:
        acceptance = probe.verify_successor(image, build, native)
        t.require_current_acceptance(acceptance, image, probe)
        t.need(partial.source_json(original, "native-acceptance.json", t) == asdict(acceptance),
               "document_final_native_acceptance_changed")
        captured = partial.source_json(original, "captured-flags.json", t)
        t.need(set(captured) == set(t.CALLERS)
               and all(set(captured[name]) == set(t.PAUSE[name]) for name in t.CALLERS),
               "document_final_original_flags_changed")
        first_sha = partial.original_claim(t, http, original)
        second_sha, second = second_claim(t, http, previous, first_sha, t.sha(asdict(acceptance)))
        run = t.Controller(http, probe, owner, seconds=4200, candidate_image=image, rescue_expired_v2=True)
        worker = partial.current_phase(t, run, original, "worker", t.WORKER)
        _preimage(t, second, t.WORKER, worker)
        run.observed[t.WORKER] = worker
        probe.verify_current_successor(image, worker)
        api = partial.api_maintenance_close(t, run, original, captured)
        _preimage(t, second, t.CALLERS[0], api)
        run.observed[t.CALLERS[0]] = api
        analysis = analysis_open(t, run, original, previous, captured, second)
        run.observed[t.CALLERS[1]] = analysis
        coordinator = partial.current_phase(t, run, original, "pins", t.CALLERS[2])
        _preimage(t, second, t.CALLERS[2], coordinator)
        env = t.environment(coordinator["template"])
        t.need(all(env.get(flag) == {"name": flag, "value": "false"}
                   for flag in t.PAUSE[t.CALLERS[2]]), "document_final_coordinator_admission_changed")
        run.observed[t.CALLERS[2]] = coordinator
        partial.absent_revision(t, http, t.CALLERS[2])
        probe.verify_caller_pins(run, image)
        t.need(all(run.service(name) == run.observed[name] for name in t.SERVICES),
               "document_final_preclaim_state_changed")
        claim(t, http, owner, acceptance, run.observed, first_sha, second_sha)
        prewrite_deadline = min(run.deadline, time.monotonic() + 45)
        t.need(all(run.service_retry(name, deadline=prewrite_deadline,
                                     timeout_code="document_final_prewrite_read_deadline_reconcile_only")
                   == run.observed[name] for name in t.SERVICES),
               "document_final_prewrite_state_changed")
        template = t.mutate_template(coordinator, t.CALLERS[2], "open", t.RESCUE_NONCE,
                                     image, {}, captured[t.CALLERS[2]])
        run.patch(t.CALLERS[2], "open", template, acceptance=acceptance)
        probe.verify_caller_pins(run, image)
        probe.verify_current_successor(image, run.observed[t.WORKER])
        t.need(all(run.service(name) == run.observed[name] for name in t.SERVICES),
               "document_final_state_changed")
        result = {"status": "document_refresh_final_caller_restoration_observed",
                  "source_run_id": RUN_ID, "source_artifact_id": ARTIFACT_ID,
                  "source_artifact_digest": "sha256:" + ARTIFACT_DIGEST,
                  "image": image, "native_acceptance_sha256": t.sha(asdict(acceptance)),
                  "worker_and_all_caller_pins_verified": True,
                  "analysis_and_coordinator_admission_restored": True,
                  "api_remains_maintenance_closed": True,
                  "api_maintenance_restore_required": True,
                  "automatic_replay": False}
        owner.write("complete.json", result)
        return result
    finally:
        owner.close()
