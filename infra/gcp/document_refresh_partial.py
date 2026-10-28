"""One-time continuation after the acknowledged V2 rescue stopped at API open.

The retained artifact, original claim, Cloud Run LROs and live revisions are
read back before a separate create-only claim. This controller sends only the
two caller opens that the first continuation never started. The emergency
schema25 maintenance operator owns the API close and eventual restoration.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import stat
from dataclasses import asdict
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlencode

RUN_ID = 40781707554
ARTIFACT_ID = 12858021573
ARTIFACT_DIGEST = "e67577ef8f59495554fba9c27c59748d40fb38e80c9c52d61af69fc3b839cde4"
ARTIFACT_HEAD = "e680b522a12410d597a02a653a0c8cdb9ec5b3ea"
JOURNAL_TREE_SHA256 = "c18cf6e3b2fd7d9992d8223776de018103680009ded20f5909547301fdd85211"
MAINTENANCE_NONCE = "5c8888070c0e8ff8e3ca93bf7784fbe0"
MAINTENANCE_PLAN_SHA256 = "7e68d55d76e3da6bf5bbea33721d34a4385a3ab8482e5a4d54b809c5fbe67d50"
MAINTENANCE_CLOSE_REVISION = "ai-resume-parser-m25-" + MAINTENANCE_NONCE[:12] + "-close"
MAINTENANCE_CLOSE_GENERATION = 344
MAINTENANCE_CLOSE_OPERATION = (
    "projects/ai-resume-parser-482412/locations/us-central1/operations/"
    "392cebba-48f1-48ce-b623-f09b5274b252")
CLAIM_OBJECT = ("hirewiz-document-refresh-source/5436da554bc5437cadccd296f3489bdd/"
                f"partial-transition-{RUN_ID}-{ARTIFACT_ID}.json")

# The exact finite flags closed by schema25_maintenance on this API source.
# They are copied from its pinned release contract, not inferred from a live
# response. A changed maintenance implementation needs a new review.
MAINTENANCE_OFF = (
    "OPTIONAL_AI_GENERATION_ENABLED", "EMPLOYER_AUTO_SUBMIT_ENABLED",
    "RAZORPAY_CHECKOUT_ENABLED", "CANDIDATE_ACCOUNT_LIFECYCLE_ENABLED",
    "BROWSER_ASSISTANCE_ENABLED", "RESUME_DIRECT_UPLOAD_ENABLED",
    "EMPLOYER_DISCOVERY_ENABLED", "BROWSER_ASSISTANCE_TYPED_CONTROLS_ENABLED",
    "BROWSER_ASSISTANCE_ATTACHMENT_ENABLED", "BROWSER_ASSISTANCE_SUBMIT_ENABLED",
    "BROWSER_ASSISTANCE_NATIVE_UPLOAD_ENABLED", "BROWSER_ASSISTANCE_NATIVE_PAIRING_ENABLED",
    "BROWSER_PAIRING_NATIVE_ENABLED", "BROWSER_SUBMIT_NATIVE_ENABLED",
    "CANDIDATE_INGRESS_NATIVE_ENABLED", "UNIFIED_CREDITS_ENABLED",
    "UNIFIED_CREDIT_EXECUTION_ENABLED", "FREE_CORE_ENABLED",
)


def source_json(path: Path, name: str, t: Any) -> dict[str, Any]:
    t.need(re.fullmatch(r"[a-z0-9.-]{1,100}\.json", name), "document_partial_source_name_refused")
    file = path / name
    state = file.lstat()
    t.need(stat.S_ISREG(state.st_mode) and state.st_nlink == 1 and state.st_uid == os.getuid()
           and stat.S_IMODE(state.st_mode) == 0o600 and 0 < state.st_size <= 65536,
           "document_partial_source_file_changed")
    value = json.loads(file.read_bytes())
    t.need(type(value) is dict, "document_partial_source_file_changed")
    return value


def acknowledged_operation(t: Any, http: Any, journal: Path, phase: str, service: str,
                           *, verified: bool) -> tuple[dict[str, Any], dict[str, Any]]:
    prefix = phase + "." + service
    started = source_json(journal, prefix + ".started.json", t)
    ack = source_json(journal, prefix + ".acknowledged.json", t)
    t.need(type(started.get("prior_generation")) is str and started["prior_generation"].isdigit()
           and type(started.get("request_sha256")) is str
           and re.fullmatch(r"[a-f0-9]{64}", started["request_sha256"])
           and type(started.get("template_sha256")) is str
           and re.fullmatch(r"[a-f0-9]{64}", started["template_sha256"])
           and started.get("automatic_replay") is False
           and ack == {**started, "operation": ack.get("operation")}
           and type(ack.get("operation")) is str and t.OPERATION.fullmatch(ack["operation"]),
           "document_partial_operation_marker_changed")
    operation = t.checked_json(http, "GET", ack["operation"])
    t.need(operation.get("name") == ack["operation"] and operation.get("done") is True
           and "error" not in operation and type(operation.get("response")) is dict,
           "document_partial_operation_unresolved")
    if verified:
        proof = source_json(journal, prefix + ".verified.json", t)
        t.need(proof == {**ack, "revision": proof.get("revision"),
                          "generation": proof.get("generation")}
               and type(proof["revision"]) is str and type(proof["generation"]) is str
               and proof["generation"].isdigit()
               and int(proof["generation"]) == int(started["prior_generation"]) + 1,
               "document_partial_verified_marker_changed")
    return started, operation


def current_phase(t: Any, run: Any, journal: Path, phase: str, service: str) -> dict[str, Any]:
    started, operation = acknowledged_operation(t, run.http, journal, phase, service, verified=True)
    proof = source_json(journal, phase + "." + service + ".verified.json", t)
    current = run.service(service)
    run.ready(service, current, uid=started["service_uid"])
    normalized = copy.deepcopy(current["template"])
    normalized["revision"] = normalized["revision"].rsplit("/", 1)[-1]
    t.need(current["latestReadyRevision"] == proof["revision"]
           and str(current["generation"]) == proof["generation"]
           and t.sha(normalized) == started["template_sha256"]
           and operation["response"].get("name") == current["name"]
           and operation["response"].get("uid") == current["uid"]
           and str(operation["response"].get("generation")) == proof["generation"],
           "document_partial_serving_phase_changed")
    return current


def original_claim(t: Any, http: Any, journal: Path) -> str:
    ack = source_json(journal, "rescue-claim.acknowledged.json", t)
    proof = source_json(journal, "rescue-claim.verified.json", t)
    t.need(ack.get("name") == t.RESCUE_CLAIM_OBJECT
           and ack.get("bucket") == t.PROJECT + "_cloudbuild"
           and type(ack.get("generation")) is str and ack["generation"].isdigit()
           and proof.get("acknowledgment") == ack
           and type(proof.get("body_sha256")) is str
           and re.fullmatch(r"[a-f0-9]{64}", proof["body_sha256"]),
           "document_partial_original_claim_changed")
    url = ("https://storage.googleapis.com/download/storage/v1/b/" + ack["bucket"] + "/o/"
           + quote(ack["name"], safe="") + "?"
           + urlencode({"alt": "media", "generation": ack["generation"]}))
    response, raw = http.request(url, method="GET", redirections=0)
    t.need(response.status == 200 and hashlib.sha256(raw).hexdigest() == proof["body_sha256"],
           "document_partial_original_claim_changed")
    value = json.loads(raw)
    t.need(type(value) is dict and value.get("kind") == "exact_failed_v2_scanner_transition_resume_v1"
           and value.get("source_run_id") == t.RESCUE_RUN_ID
           and value.get("source_artifact_id") == t.RESCUE_ARTIFACT_ID
           and value.get("source_journal_tree_sha256") == t.RESCUE_JOURNAL_TREE_SHA256
           and value.get("automatic_replay") is False,
           "document_partial_original_claim_changed")
    return proof["body_sha256"]


def api_maintenance_close(t: Any, run: Any, journal: Path, captured: dict[str, Any]) -> dict[str, Any]:
    api = t.CALLERS[0]
    pin_proof = source_json(journal, "pins." + api + ".verified.json", t)
    acknowledged_operation(t, run.http, journal, "pins", api, verified=True)
    open_started, operation = acknowledged_operation(t, run.http, journal, "open", api, verified=False)
    t.need(open_started["prior_generation"] == pin_proof["generation"]
           and open_started["service_uid"] == source_json(journal, "pins." + api + ".started.json", t)["service_uid"],
           "document_partial_api_open_chain_changed")
    base = t.PARENT + "/services/" + api + "/revisions/"
    pins_revision = run.revision(pin_proof["revision"])
    current = run.service(api)
    run.ready(api, current, uid=open_started["service_uid"])
    keys = tuple(key for key in current["template"] if key != "revision")
    t.need(all(key in pins_revision for key in keys), "document_partial_api_pins_changed")
    pins_template = {key: copy.deepcopy(pins_revision[key]) for key in keys}
    pins_template["revision"] = pin_proof["revision"]
    t.need(t.sha({**pins_template, "revision": pin_proof["revision"].rsplit("/", 1)[-1]})
           == source_json(journal, "pins." + api + ".started.json", t)["template_sha256"],
           "document_partial_api_pins_changed")
    restored = copy.deepcopy(captured[api])
    restored["RESUME_DIRECT_UPLOAD_ENABLED"] = {"name": "RESUME_DIRECT_UPLOAD_ENABLED", "value": "true"}
    open_template = t.mutate_template({"template": pins_template}, api, "open", t.RESCUE_NONCE,
                                      t.RESCUE_IMAGE, {}, restored)
    t.need(t.sha(open_template) == open_started["template_sha256"],
           "document_partial_api_open_template_changed")
    open_revision_name = api + "-d-" + t.RESCUE_NONCE[:12] + "-open"
    open_revision = run.revision(base + open_revision_name)
    t.need({key: open_revision.get(key) for key in keys} == {key: open_template[key] for key in keys}
           and open_revision.get("name") == base + open_revision_name
           and any(row.get("type") == "Ready" and row.get("state") == "CONDITION_SUCCEEDED"
                   for row in open_revision.get("conditions", [])),
           "document_partial_api_open_revision_changed")
    response = operation["response"]
    t.need(response.get("name") == current["name"] and response.get("uid") == current["uid"]
           and str(response.get("generation")) == str(int(open_started["prior_generation"]) + 1)
           and response.get("latestCreatedRevision") == base + open_revision_name
           and response.get("latestReadyRevision") in {pin_proof["revision"], base + open_revision_name},
           "document_partial_api_open_operation_changed")
    close_operation = t.checked_json(run.http, "GET", MAINTENANCE_CLOSE_OPERATION)
    close_response = close_operation.get("response")
    t.need(close_operation.get("name") == MAINTENANCE_CLOSE_OPERATION
           and close_operation.get("done") is True and "error" not in close_operation
           and type(close_response) is dict
           and close_response.get("name") == current["name"]
           and close_response.get("uid") == current["uid"]
           and str(close_response.get("generation")) == str(MAINTENANCE_CLOSE_GENERATION)
           and close_response.get("latestCreatedRevision") == base + MAINTENANCE_CLOSE_REVISION
           and close_response.get("latestReadyRevision") == base + MAINTENANCE_CLOSE_REVISION,
           "document_partial_api_maintenance_operation_changed")
    t.need(str(current["generation"]) == str(MAINTENANCE_CLOSE_GENERATION)
           and int(current["generation"]) == int(open_started["prior_generation"]) + 2
           and current["latestReadyRevision"] == base + MAINTENANCE_CLOSE_REVISION,
           "document_partial_api_maintenance_close_changed")
    closed = copy.deepcopy(open_template)
    env = t.environment(closed)
    for flag in MAINTENANCE_OFF:
        env[flag] = {"name": flag, "value": "false"}
    closed["containers"][0]["env"] = list(env.values())
    closed["revision"] = MAINTENANCE_CLOSE_REVISION
    observed = copy.deepcopy(current["template"])
    observed["revision"] = observed["revision"].rsplit("/", 1)[-1]
    t.need(observed == closed and current.get("labels", {}) == response.get("labels", {})
           and current["template"]["containers"][0]["image"] == t.GUARDED_API_IMAGE
           and close_response.get("template") == current["template"],
           "document_partial_api_maintenance_close_changed")
    return current


def absent_revision(t: Any, http: Any, service: str) -> None:
    revision = service + "-d-" + t.RESCUE_NONCE[:12] + "-open"
    resource = t.PARENT + "/services/" + service + "/revisions/" + revision
    response, _ = http.request("https://run.googleapis.com/v2/" + resource,
                               method="GET", redirections=0)
    t.need(response.status == 404, "document_partial_open_revision_already_exists")


def claim(t: Any, http: Any, owner: Any, acceptance: Any,
          preimages: dict[str, dict[str, Any]], original_claim_sha256: str) -> None:
    bucket = t.PROJECT + "_cloudbuild"
    root = Path(__file__).resolve().parents[2]
    sources = ("backend/scripts/document_refresh.py", "infra/gcp/document_refresh_partial.py",
               "infra/gcp/document_refresh_transition.py", "infra/gcp/document_refresh_auth.py")
    hashes = {name: hashlib.sha256((root / name).read_bytes()).hexdigest() for name in sources}
    body = {"version": 1, "kind": "exact_partially_completed_v2_scanner_transition_resume_v1",
            "source_run_id": RUN_ID, "source_artifact_id": ARTIFACT_ID,
            "source_artifact_digest": "sha256:" + ARTIFACT_DIGEST,
            "source_journal_tree_sha256": JOURNAL_TREE_SHA256,
            "original_claim_body_sha256": original_claim_sha256,
            "maintenance_plan_sha256": MAINTENANCE_PLAN_SHA256,
            "maintenance_close_revision": MAINTENANCE_CLOSE_REVISION,
            "native_acceptance_sha256": t.sha(asdict(acceptance)),
            "preimages": {name: {"uid": row["uid"], "generation": str(row["generation"]),
                                 "etag_sha256": hashlib.sha256(row["etag"].encode()).hexdigest(),
                                 "template_sha256": t.sha(row["template"])}
                          for name, row in preimages.items()},
            "maintenance_source_sha256": hashes, "automatic_replay": False}
    t.need(set(preimages) == set(t.SERVICES), "document_partial_claim_preimages_changed")
    metadata_uri = "https://storage.googleapis.com/storage/v1/b/" + bucket + "/o/" + quote(CLAIM_OBJECT, safe="")
    response, _ = http.request(metadata_uri, method="GET", redirections=0)
    t.need(response.status == 404, "document_partial_claim_exists_or_unknown_reconcile_only")
    owner.write("partial-claim.started.json", {"object": CLAIM_OBJECT, "body_sha256": t.sha(body),
                                                "automatic_replay": False})
    response, raw = http.request("https://storage.googleapis.com/upload/storage/v1/b/" + bucket + "/o?"
        + urlencode({"uploadType": "media", "name": CLAIM_OBJECT, "ifGenerationMatch": "0"}),
        method="POST", body=t.canonical(body), headers={"Content-Type": "application/json"}, redirections=0)
    t.need(response.status in (200, 201), "document_partial_claim_unknown_reconcile_only")
    value = json.loads(raw)
    t.need(type(value) is dict and value.get("name") == CLAIM_OBJECT and value.get("bucket") == bucket
           and type(value.get("generation")) is str and re.fullmatch(r"[1-9][0-9]{0,19}", value["generation"]),
           "document_partial_claim_ack_changed")
    ack = {key: value[key] for key in ("name", "bucket", "generation")}
    owner.write("partial-claim.acknowledged.json", ack)
    response, raw = http.request("https://storage.googleapis.com/download/storage/v1/b/" + bucket + "/o/"
        + quote(CLAIM_OBJECT, safe="") + "?" + urlencode({"alt": "media", "generation": ack["generation"]}),
        method="GET", redirections=0)
    t.need(response.status == 200 and raw == t.canonical(body), "document_partial_claim_readback_changed")
    owner.write("partial-claim.verified.json", {"body_sha256": t.sha(body), "acknowledgment": ack})


def resume_after_api_maintenance_close(image: str, build: dict[str, Any], native: Path,
                                       source: Path, journal: Path, *, source_tree_sha256: str,
                                       http: Any, probe: Any, t: Any) -> dict[str, Any]:
    """Reconcile old actions, then one-send only analysis/coordinator opens."""
    t.need(source_tree_sha256 == JOURNAL_TREE_SHA256 and image == t.RESCUE_IMAGE
           and build.get("build_id") == t.RESCUE_BUILD_ID and build.get("nonce") == t.RESCUE_NONCE,
           "document_partial_exact_artifact_required")
    owner = t.Journal(journal)
    try:
        acceptance = probe.verify_successor(image, build, native)
        t.require_current_acceptance(acceptance, image, probe)
        t.need(source_json(source, "native-acceptance.json", t) == asdict(acceptance),
               "document_partial_native_acceptance_changed")
        captured = source_json(source, "captured-flags.json", t)
        t.need(set(captured) == set(t.CALLERS)
               and all(set(captured[name]) == set(t.PAUSE[name]) for name in t.CALLERS),
               "document_partial_original_flags_changed")
        original_claim_sha256 = original_claim(t, http, source)
        run = t.Controller(http, probe, owner, seconds=4200, candidate_image=image, rescue_expired_v2=True)
        worker = current_phase(t, run, source, "worker", t.WORKER)
        run.observed[t.WORKER] = worker
        probe.verify_current_successor(image, worker)
        api = api_maintenance_close(t, run, source, captured)
        run.observed[t.CALLERS[0]] = api
        for name in t.CALLERS[1:]:
            current = current_phase(t, run, source, "pins", name)
            env = t.environment(current["template"])
            t.need(all(env.get(flag) == {"name": flag, "value": "false"}
                       for flag in t.PAUSE[name]), "document_partial_caller_admission_changed")
            run.observed[name] = current
            absent_revision(t, http, name)
        probe.verify_caller_pins(run, image)
        for name in (t.WORKER, *t.CALLERS):
            t.need(run.service(name) == run.observed[name], "document_partial_preclaim_state_changed")
        claim(t, http, owner, acceptance, run.observed, original_claim_sha256)
        for name in t.CALLERS[1:]:
            # The emergency close must remain in force while each caller opens.
            t.need(all(run.service(service) == run.observed[service] for service in t.SERVICES),
                   "document_partial_prewrite_state_changed")
            template = t.mutate_template(run.observed[name], name, "open", t.RESCUE_NONCE,
                                         image, {}, captured[name])
            run.patch(name, "open", template, acceptance=acceptance)
        probe.verify_caller_pins(run, image)
        probe.verify_current_successor(image, run.observed[t.WORKER])
        t.need(run.service(t.CALLERS[0]) == api
               and all(run.service(name) == run.observed[name] for name in (t.WORKER, *t.CALLERS[1:])),
               "document_partial_final_join_changed")
        result = {"status": "document_refresh_partial_restoration_observed",
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
