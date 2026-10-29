"""Read-only proof of the acknowledged final scanner transition.

The third claim and coordinator PATCH were already sent by run 40805300453.
This module has no claim or PATCH entrypoint. It verifies their immutable
receipts, the completed LRO and the exact live four-service state.
"""
from __future__ import annotations

import copy
import importlib.util
from dataclasses import asdict
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

_spec = importlib.util.spec_from_file_location(
    "document_refresh_final_readback_source", Path(__file__).with_name("document_refresh_final.py"))
assert _spec is not None and _spec.loader is not None
final = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(final)
partial = final.partial

RUN_ID = 40805300453
ARTIFACT_ID = 12868086986
ARTIFACT_DIGEST = "2704e3657731002c1ba008a18cd995d14c227d1a89dbc1f13fb4f03af7df02f6"
ARTIFACT_HEAD = "ebee72761f433c43d2e91c9ca61d736631fea8cd"
JOURNAL_TREE_SHA256 = "7e200c774ae0148965d836a3ad5ff184670b05670bb3f1340f17ff1b81b047a3"
CLAIM_GENERATION = "1793240192529106"
COORDINATOR_OPERATION = ("projects/ai-resume-parser-482412/locations/us-central1/operations/"
                         "e62b0d5e-4326-4317-954c-aa0bf5f78e50")
SOURCE_HASHES = {
    "backend/scripts/document_refresh.py": "0810e4caeb173fecc16a41ef23fc4a7be02d96f1c4af4d4f9caef948130c9aa9",
    "infra/gcp/document_refresh_auth.py": ("a5d4b0dfeba1b690398df09e6e154cc8"
                                           "7632f0053d58eefddaf490ff36231c43"),
    "infra/gcp/document_refresh_final.py": "215802e9ae0dec3730eeee09ef2a3eac321c7651e69a8922be8a41088ad01ff8",
    "infra/gcp/document_refresh_partial.py": "494b6f2e69de0bd6dc90f2a173d9ba3a56176fdb46fff5fc2cdc57f52b588a82",
    "infra/gcp/document_refresh_transition.py": "68db5a36352b4a83b1ba7ff1dc221f079c956883f2f31bf09bf650ec45023d61",
}


class ReadOnlyHttp:
    """Narrow the runner's dedicated WIF transport to exact proof GETs."""

    def __init__(self, delegate: Any, t: Any):
        self.delegate = delegate
        self.t = t

    def request(self, uri: str, method: str = "GET", body: Any = None,
                headers: Any = None, redirections: int = 0, **kwargs: Any) -> tuple[Any, bytes]:
        target = urlsplit(uri)
        run = (target.scheme == "https" and target.hostname == "run.googleapis.com"
               and target.path.startswith("/v2/" + self.t.PARENT + "/"))
        storage = (target.scheme == "https" and target.hostname == "storage.googleapis.com"
                   and target.path.startswith("/download/storage/v1/b/"
                       + self.t.PROJECT + "_cloudbuild/o/"))
        self.t.need(method == "GET" and body is None and redirections == 0
                    and (headers is None or type(headers) is dict
                         and headers == {"Content-Type": "application/json"})
                    and not kwargs and (run or storage),
                    "document_final_readback_write_route_refused")
        return self.delegate.request(uri, method=method, body=None, headers=headers,
                                     redirections=0)


def _third_claim(t: Any, http: Any, failed: Path, first_sha: str, second_sha: str,
                 acceptance_sha: str) -> dict[str, Any]:
    started = partial.source_json(failed, "final-claim.started.json", t)
    ack = partial.source_json(failed, "final-claim.acknowledged.json", t)
    proof = partial.source_json(failed, "final-claim.verified.json", t)
    digest, value = final._claim_media(t, http, final.CLAIM_OBJECT, ack, proof)
    t.need(started == {"object": final.CLAIM_OBJECT, "body_sha256": digest,
                       "automatic_replay": False}
           and ack["generation"] == CLAIM_GENERATION
           and value == {
               "version": 1,
               "kind": "exact_second_failed_v2_scanner_transition_resume_v1",
               "source_run_id": final.RUN_ID,
               "source_artifact_id": final.ARTIFACT_ID,
               "source_artifact_digest": "sha256:" + final.ARTIFACT_DIGEST,
               "source_journal_tree_sha256": final.JOURNAL_TREE_SHA256,
               "original_claim_body_sha256": first_sha,
               "second_claim_body_sha256": second_sha,
               "maintenance_plan_sha256": partial.MAINTENANCE_PLAN_SHA256,
               "maintenance_close_revision": partial.MAINTENANCE_CLOSE_REVISION,
               "native_acceptance_sha256": acceptance_sha,
               "preimages": value.get("preimages"),
               "maintenance_source_sha256": SOURCE_HASHES,
               "automatic_replay": False,
           }
           and type(value["preimages"]) is dict
           and set(value["preimages"]) == set(t.SERVICES),
           "document_final_readback_claim_changed")
    return value


def _coordinator_open(t: Any, run: Any, source: Path, failed: Path,
                      captured: dict[str, Any], third: dict[str, Any]) -> dict[str, Any]:
    service = t.CALLERS[2]
    pins_started, pins_operation = partial.acknowledged_operation(
        t, run.http, source, "pins", service, verified=True)
    pins_proof = partial.source_json(source, "pins." + service + ".verified.json", t)
    prior = pins_operation["response"]
    final._preimage(t, third, service, prior)
    t.need(pins_started["service_uid"] == prior["uid"]
           and pins_proof["generation"] == str(prior["generation"])
           and pins_proof["revision"] == prior["latestReadyRevision"],
           "document_final_readback_prior_changed")
    revision = run.revision(pins_proof["revision"])
    keys = tuple(key for key in prior["template"] if key != "revision")
    t.need(all(key in revision for key in keys), "document_final_readback_prior_changed")
    prior_template = {key: copy.deepcopy(revision[key]) for key in keys}
    prior_template["revision"] = pins_proof["revision"].rsplit("/", 1)[-1]
    t.need(t.sha(prior_template) == pins_started["template_sha256"],
           "document_final_readback_prior_changed")
    expected = t.mutate_template({"template": prior_template}, service, "open", t.RESCUE_NONCE,
                                 t.RESCUE_IMAGE, {}, captured[service])
    body = {"name": prior["name"], "etag": prior["etag"], "template": expected,
            "traffic": [{"type": "TRAFFIC_TARGET_ALLOCATION_TYPE_LATEST", "percent": 100}]}
    started, operation = partial.acknowledged_operation(
        t, run.http, failed, "open", service, verified=False)
    t.need(started["service_uid"] == prior["uid"]
           and started["prior_generation"] == str(prior["generation"])
           and started["template_sha256"] == t.sha(expected)
           and started["request_sha256"] == t.sha(body)
           and partial.source_json(failed, "open." + service + ".acknowledged.json", t)["operation"]
               == COORDINATOR_OPERATION,
           "document_final_readback_request_changed")
    current = run.service(service)
    run.ready(service, current, uid=prior["uid"])
    target = t.PARENT + "/services/" + service + "/revisions/" + expected["revision"]
    normalized = copy.deepcopy(current["template"])
    normalized["revision"] = normalized["revision"].rsplit("/", 1)[-1]
    ignored = {"@type", "template", "traffic", "labels", "etag", "generation", "observedGeneration",
               "updateTime", "lastModifier", "reconciling", "terminalCondition", "conditions",
               "latestReadyRevision", "latestCreatedRevision", "trafficStatuses", "urls"}
    statuses = current.get("trafficStatuses")
    response = operation["response"]
    t.need(str(current["generation"]) == str(int(started["prior_generation"]) + 1)
           and str(current.get("observedGeneration")) == str(current["generation"])
           and current.get("reconciling", False) is False
           and current.get("terminalCondition", {}).get("state") == "CONDITION_SUCCEEDED"
           and current["latestReadyRevision"] == target
           and current["latestCreatedRevision"] == target
           and normalized == expected
           and current.get("labels", {}) == prior.get("labels", {})
           and current.get("traffic") == body["traffic"]
           and type(statuses) is list and len(statuses) == 1
           and statuses[0].get("revision") in {target, expected["revision"]}
           and statuses[0].get("percent") == 100
           and statuses[0].get("type") == "TRAFFIC_TARGET_ALLOCATION_TYPE_LATEST"
           and not statuses[0].get("tag")
           and {key: value for key, value in current.items() if key not in ignored}
               == {key: value for key, value in prior.items() if key not in ignored}
           and response.get("name") == current["name"]
           and response.get("uid") == prior["uid"]
           and str(response.get("generation")) == str(current["generation"])
           and response.get("latestCreatedRevision") == target
           and response.get("latestReadyRevision") in {pins_proof["revision"], target},
           "document_final_readback_coordinator_changed")
    opened = run.revision(target)
    t.need(all(opened.get(key) == expected[key] for key in keys)
           and any(row.get("type") == "Ready" and row.get("state") == "CONDITION_SUCCEEDED"
                   for row in opened.get("conditions", [])),
           "document_final_readback_revision_changed")
    env = t.environment(current["template"])
    t.need(all(env.get(flag) == captured[service][flag] for flag in t.PAUSE[service]),
           "document_final_readback_flags_changed")
    return current


def reconcile(image: str, build: dict[str, Any], native: Path, source: Path, previous: Path,
              failed: Path, journal: Path, *, source_tree_sha256: str, http: Any,
              probe: Any, t: Any) -> dict[str, Any]:
    """Only read exact evidence and state; HTTP writes are denied by the caller."""
    t.need(source_tree_sha256 == JOURNAL_TREE_SHA256 and image == t.RESCUE_IMAGE
           and build.get("build_id") == t.RESCUE_BUILD_ID and build.get("nonce") == t.RESCUE_NONCE,
           "document_final_readback_exact_artifact_required")
    t.need({p.name for p in failed.glob("*.json")} == {
        "final-claim.started.json", "final-claim.acknowledged.json", "final-claim.verified.json",
        "open.hirewiz-resume-coordinator.started.json",
        "open.hirewiz-resume-coordinator.acknowledged.json",
    }, "document_final_readback_failed_journal_changed")
    owner = t.Journal(journal)
    try:
        acceptance = probe.verify_successor(image, build, native)
        t.require_current_acceptance(acceptance, image, probe)
        t.need(partial.source_json(source, "native-acceptance.json", t) == asdict(acceptance),
               "document_final_readback_native_acceptance_changed")
        captured = partial.source_json(source, "captured-flags.json", t)
        t.need(set(captured) == set(t.CALLERS)
               and all(set(captured[name]) == set(t.PAUSE[name]) for name in t.CALLERS),
               "document_final_readback_original_flags_changed")
        first_sha = partial.original_claim(t, http, source)
        second_sha, second = final.second_claim(t, http, previous, first_sha,
                                                t.sha(asdict(acceptance)))
        third = _third_claim(t, http, failed, first_sha, second_sha, t.sha(asdict(acceptance)))
        run = t.Controller(http, probe, owner, seconds=900, candidate_image=image,
                           rescue_expired_v2=True)
        worker = partial.current_phase(t, run, source, "worker", t.WORKER)
        final._preimage(t, second, t.WORKER, worker)
        final._preimage(t, third, t.WORKER, worker)
        run.observed[t.WORKER] = worker
        probe.verify_current_successor(image, worker)
        api = partial.api_maintenance_close(t, run, source, captured)
        final._preimage(t, second, t.CALLERS[0], api)
        final._preimage(t, third, t.CALLERS[0], api)
        run.observed[t.CALLERS[0]] = api
        analysis = final.analysis_open(t, run, source, previous, captured, second)
        final._preimage(t, third, t.CALLERS[1], analysis)
        run.observed[t.CALLERS[1]] = analysis
        coordinator = _coordinator_open(t, run, source, failed, captured, third)
        run.observed[t.CALLERS[2]] = coordinator
        probe.verify_caller_pins(run, image)
        probe.verify_current_successor(image, worker)
        t.need(all(run.service(name) == run.observed[name] for name in t.SERVICES),
               "document_final_readback_state_changed")
        result = {"status": "document_refresh_final_caller_restoration_observed",
                  "source_run_id": RUN_ID, "source_artifact_id": ARTIFACT_ID,
                  "source_artifact_digest": "sha256:" + ARTIFACT_DIGEST,
                  "image": image, "native_acceptance_sha256": t.sha(asdict(acceptance)),
                  "third_claim_generation": CLAIM_GENERATION,
                  "coordinator_operation": COORDINATOR_OPERATION,
                  "worker_and_all_caller_pins_verified": True,
                  "analysis_and_coordinator_admission_restored": True,
                  "api_remains_maintenance_closed": True,
                  "api_maintenance_restore_required": True,
                  "read_only_reconciliation": True,
                  "automatic_replay": False}
        owner.write("complete.json", result)
        return result
    finally:
        owner.close()
