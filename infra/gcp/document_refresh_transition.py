"""Bounded fail-closed document maintenance; no credentials or actions at import.

A fresh current V2 ten-case proof gates every transition. Exact-image refusal makes
old in-flight callers fail safely during this short maintenance window; this
controller never claims those callers drained. Unknown PATCH outcomes stop the
pipeline, retain the closed flags, and permit only same-operation readback.
"""
from __future__ import annotations

import copy
import fcntl
import hashlib
import json
import os
import re
import stat
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

PROJECT, REGION = "ai-resume-parser-482412", "us-central1"
PARENT = f"projects/{PROJECT}/locations/{REGION}"
WORKER = "hirewiz-document-inspector"
CALLERS = ("ai-resume-parser", "hirewiz-analysis-worker", "hirewiz-resume-coordinator")
SERVICES = (WORKER, *CALLERS)
RUNTIME = "hirewiz-runtime-20261014@" + PROJECT + ".iam.gserviceaccount.com"
ACCOUNTS = {WORKER: WORKER + "@" + PROJECT + ".iam.gserviceaccount.com",
            CALLERS[0]: RUNTIME, CALLERS[1]: RUNTIME,
            CALLERS[2]: CALLERS[2] + "@" + PROJECT + ".iam.gserviceaccount.com"}
PAUSE = {CALLERS[0]: ("OPTIONAL_AI_GENERATION_ENABLED", "RESUME_DIRECT_UPLOAD_ENABLED"),
         CALLERS[1]: ("OPTIONAL_AI_GENERATION_ENABLED", "RESUME_DIRECT_UPLOAD_ENABLED"),
         CALLERS[2]: ("RESUME_UPLOAD_COORDINATOR_SCAN_ENABLED", "RESUME_DIRECT_UPLOAD_ENABLED")}
PINS = ("DOCUMENT_WORKER_IMAGE_DIGEST", "DOCUMENT_WORKER_POLICY_SHA256",
        "DOCUMENT_WORKER_DOCX_RENDERER_VERSION", "DOCUMENT_WORKER_DOCX_FONT_MANIFEST_SHA256")
LABELS = ("hirewiz-refresh-build", "hirewiz-refresh-nonce", "hirewiz-refresh-job")
OPERATION = re.compile(re.escape(PARENT + "/operations/") + r"[A-Za-z0-9._-]{1,200}\Z")
# The reviewed schema21 API hotfix honors the upload admission flag on the
# legacy /resume/parse route. The image is pinned to its native Build. Fill
# revision and tag only from the actual production promotion readback.
GUARDED_API_RELEASE = "d25811bf9aab85687d9786bdf6a92918826783d3"
GUARDED_API_IMAGE = ("us-central1-docker.pkg.dev/ai-resume-parser-482412/cloud-run-source-deploy/"
                     "hirewiz@sha256:e8d5d437995820970bc68b2036ad7c09a524ec9d9268be4022370b04c6bc7d51")
GUARDED_API_REVISION = "ai-resume-parser-r-d25811bf9aab-84d9a588"
GUARDED_API_TAG = "rolling-84d9a5889f2f"


class TransitionDenied(RuntimeError):
    """Fixed nonsecret refusal, including unknown write outcomes."""


def need(value: Any, code: str) -> None:
    if not value:
        raise TransitionDenied(code)


def canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def sha(value: Any) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


def environment(template: dict[str, Any]) -> dict[str, dict[str, Any]]:
    containers = template.get("containers")
    need(type(containers) is list and len(containers) == 1, "document_transition_container_changed")
    assert isinstance(containers, list)
    rows = containers[0].get("env", [])
    need(type(rows) is list and len(rows) <= 256, "document_transition_environment_changed")
    result = {}
    for row in rows:
        need(type(row) is dict and type(row.get("name")) is str
             and re.fullmatch(r"[A-Z][A-Z0-9_]{0,127}", row["name"])
             and row["name"] not in result, "document_transition_environment_changed")
        result[row["name"]] = copy.deepcopy(row)
    return result


def mutate_template(current: dict[str, Any], service: str, phase: str, nonce: str,
                    image: str, pins: dict[str, str], captured: dict[str, Any], *,
                    worker_policy_sha256: str | None = None) -> dict[str, Any]:
    """Only documented image/pin/flag/revision fields may change; all else stays exact."""
    need(service in SERVICES and phase in {"close", "worker", "pins", "open"}
         and re.fullmatch(r"[a-f0-9]{32}", nonce), "document_transition_action_refused")
    need((phase == "worker") == (service == WORKER), "document_transition_action_refused")
    template = copy.deepcopy(current["template"])
    need(template.get("serviceAccount") == ACCOUNTS[service], "document_transition_caller_identity_changed")
    env = environment(template)
    if service == WORKER:
        need(type(worker_policy_sha256) is str and re.fullmatch(r"[a-f0-9]{64}", worker_policy_sha256),
             "document_transition_worker_policy_unverified")
        need(set(env) == {"DOCUMENT_WORKER_IMAGE_DIGEST", "DOCUMENT_WORKER_POLICY_SHA256"},
             "document_transition_worker_environment_changed")
        template["containers"][0]["image"] = image
        env["DOCUMENT_WORKER_IMAGE_DIGEST"] = {"name": "DOCUMENT_WORKER_IMAGE_DIGEST", "value": image}
        env["DOCUMENT_WORKER_POLICY_SHA256"] = {"name": "DOCUMENT_WORKER_POLICY_SHA256", "value": worker_policy_sha256}
    else:
        need(worker_policy_sha256 is None, "document_transition_action_refused")
        for key in PAUSE[service]:
            if phase == "open":
                if captured[key] is None:
                    env.pop(key, None)
                else:
                    env[key] = copy.deepcopy(captured[key])
            else:
                env[key] = {"name": key, "value": "false"}
        if phase == "pins":
            for key, value in pins.items():
                need(key in PINS and type(value) is str, "document_transition_pin_refused")
                env[key] = {"name": key, "value": value}
    template["containers"][0]["env"] = list(env.values())
    # A new explicit revision prevents an implicit LATEST allocation from racing.
    template["revision"] = service + "-d-" + nonce[:12] + "-" + phase
    return template


class Journal:
    """Exclusive metadata-only journal; a missing ACK never permits another PATCH."""
    def __init__(self, path: Path):
        need(path.is_absolute() and path.resolve() == path and not path.exists(),
             "document_transition_existing_journal_reconcile_only")
        path.mkdir(mode=0o700)
        self.path = path
        self.fd = os.open(path / "owner.lock", os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)

    def write(self, name: str, value: Any) -> None:
        need(re.fullmatch(r"[a-z0-9.-]{1,100}\.json", name), "document_transition_journal_name_refused")
        raw = canonical(value) + b"\n"
        need(len(raw) <= 65536, "document_transition_journal_bound")
        fd = os.open(self.path / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        fd = os.open(self.path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)

    def close(self) -> None:
        os.close(self.fd)


def checked_json(http: Any, method: str, resource: str, body: Any = None, *, query: dict[str, str] | None = None) -> dict[str, Any]:
    uri = "https://run.googleapis.com/v2/" + resource
    if query:
        uri += "?" + urlencode(query)
    response, raw = http.request(uri, method=method, body=None if body is None else canonical(body),
                                 headers={"Content-Type": "application/json"}, redirections=0)
    need(response.status == 200, "document_transition_native_outcome_unknown_reconcile_only")
    try:
        value = json.loads(raw)
    except (ValueError, UnicodeError, RecursionError):
        raise TransitionDenied("document_transition_native_metadata_invalid") from None
    need(type(value) is dict, "document_transition_native_metadata_invalid")
    return value


def require_current_acceptance(acceptance: Any, image: str | None, probe: Any) -> None:
    need(type(image) is str and getattr(acceptance, "image", None) == image
         and type(getattr(acceptance, "contract_version", None)) is int and acceptance.contract_version == 2
         and type(getattr(acceptance, "required_case_count", None)) is int and acceptance.required_case_count == 10
         and getattr(acceptance, "policy_sha256", None) == probe.POLICY,
         "document_transition_current_acceptance_required")


def guarded_api_serving(clients: Any, value: Any, image: str, name: str, *, probe: Any) -> dict[str, Any]:
    """Accept only the reviewed tagged hotfix revision during manual rescue."""
    need(type(GUARDED_API_IMAGE) is str and re.fullmatch(
        r"us-central1-docker\.pkg\.dev/ai-resume-parser-482412/"
        r"cloud-run-source-deploy/hirewiz@sha256:[a-f0-9]{64}", GUARDED_API_IMAGE),
        "document_rescue_guarded_api_image_unpinned")
    need(type(GUARDED_API_REVISION) is str and re.fullmatch(r"ai-resume-parser-[a-z0-9-]{1,36}", GUARDED_API_REVISION)
         and type(GUARDED_API_TAG) is str and re.fullmatch(r"[a-z][a-z0-9-]{0,62}", GUARDED_API_TAG),
         "document_rescue_guarded_api_revision_unpinned")
    expected = PARENT + "/services/ai-resume-parser/revisions/" + GUARDED_API_REVISION
    statuses = value.get("trafficStatuses")
    tagged = statuses[0] if type(statuses) is list and len(statuses) == 1 else {}
    need(name == CALLERS[0] and image == GUARDED_API_IMAGE
         and value.get("latestReadyRevision") == expected
         and value.get("latestCreatedRevision") == expected
         and type(tagged) is dict and tagged.get("revision") in {expected, GUARDED_API_REVISION}
         and type(tagged.get("percent")) is int and tagged["percent"] == 100
         and tagged.get("tag") == GUARDED_API_TAG
         and tagged.get("type") in (None, "TRAFFIC_TARGET_ALLOCATION_TYPE_REVISION")
         and set(tagged) <= {"revision", "percent", "tag", "type", "uri"},
         "document_rescue_guarded_api_tagged_traffic_changed")
    # Only normalize the already-exact tagged traffic to reuse the standard
    # native revision/identity check; the original service object remains intact.
    checked = copy.deepcopy(value)
    checked["trafficStatuses"] = [{"revision": expected, "percent": 100}]
    revision = probe.validate_serving(clients, checked, image, name)
    for row in (value["template"], revision):
        env = environment(row)
        need(row["containers"][0]["image"] == GUARDED_API_IMAGE
             and env.get("APP_RELEASE") == {"name": "APP_RELEASE", "value": GUARDED_API_RELEASE}
             and env.get("RESUME_DIRECT_UPLOAD_ENABLED") == {
                 "name": "RESUME_DIRECT_UPLOAD_ENABLED", "value": "false"},
             "document_rescue_guarded_api_not_serving_closed")
    need(value["template"].get("labels", {}).get("hirewiz-source") == GUARDED_API_RELEASE,
         "document_rescue_guarded_api_source_changed")
    return revision


def verify_guarded_api(clients: Any, probe: Any) -> None:
    """The serving API must be the exact closed-upload hotfix before rescue."""
    value = clients.service(PARENT + "/services/ai-resume-parser")
    guarded_api_serving(clients, value, value.get("template", {}).get("containers", [{}])[0].get("image"),
                        CALLERS[0], probe=probe)


def verify_rescue_caller_pins(clients: Any, probe: Any, image: str) -> None:
    """Old V2 pins with only the exact tagged API exception."""
    probe.verify_caller_pins(clients, image, policy_sha256=probe.POLICY,
        api_serving_validator=lambda client, value, observed_image, name:
            guarded_api_serving(client, value, observed_image, name, probe=probe))


class Controller:
    def __init__(self, http: Any, probe: Any, owner: Journal, *, seconds: int = 1800,
                 candidate_image: str | None = None, rescue_expired_v2: bool = False):
        self.http, self.probe, self.owner = http, probe, owner
        self.candidate_image = candidate_image
        self.rescue_expired_v2 = rescue_expired_v2
        self.deadline = time.monotonic() + seconds
        self.observed: dict[str, dict[str, Any]] = {}

    def service(self, name: str) -> dict[str, Any]:
        prefix = PARENT + "/services/"
        short = name.removeprefix(prefix)
        need(short in SERVICES and name in {short, prefix + short}, "document_transition_service_refused")
        return checked_json(self.http, "GET", prefix + short)

    def revision(self, name: str) -> dict[str, Any]:
        need(any(re.fullmatch(re.escape(PARENT + "/services/" + service + "/revisions/" + service + "-")
                             + r"[a-z0-9-]{1,36}", name) for service in SERVICES),
             "document_transition_revision_refused")
        return checked_json(self.http, "GET", name)

    def ready(self, service: str, value: dict[str, Any], *, uid: str | None = None) -> None:
        image = value.get("template", {}).get("containers", [{}])[0].get("image")
        need(type(image) is str and re.fullmatch(r"[A-Za-z0-9._/:-]+@sha256:[a-f0-9]{64}", image),
             "document_transition_immutable_image_required")
        statuses = value.get("trafficStatuses")
        tagged = type(statuses) is list and len(statuses) == 1 and type(statuses[0]) is dict and statuses[0].get("tag")
        if self.rescue_expired_v2 and service == CALLERS[0] and tagged:
            revision = guarded_api_serving(self, value, image, service, probe=self.probe)
        else:
            revision = self.probe.validate_serving(self, value, image, service, uid)
        template = value["template"]
        need({key: revision.get(key) for key in template if key != "revision"}
             == {key: row for key, row in template.items() if key != "revision"},
             "document_transition_serving_template_changed")
        need(type(value.get("etag")) is str and value["etag"]
             and value["template"].get("serviceAccount") == ACCOUNTS[service],
             "document_transition_service_identity_changed")

    def patch(self, service: str, phase: str, template: dict[str, Any], *, acceptance: Any,
              labels: dict[str, str] | None = None) -> None:
        """A single etag-fenced PATCH, then only its native LRO GETs and exact readbacks."""
        require_current_acceptance(acceptance, self.candidate_image, self.probe)
        need(time.monotonic() < self.deadline, "document_transition_deadline_reconcile_only")
        before = self.observed[service]
        need(self.service(service) == before, "document_transition_preimage_changed")
        self.ready(service, before)
        body = {"name": before["name"], "etag": before["etag"], "template": template,
                "traffic": [{"type": "TRAFFIC_TARGET_ALLOCATION_TYPE_LATEST", "percent": 100}]}
        mask = "template,traffic"
        if labels is not None:
            body["labels"] = {**before.get("labels", {}), **labels}
            mask += ",labels"
        marker = phase + "." + service
        expected = {"service_uid": before["uid"], "prior_generation": str(before["generation"]),
                    "request_sha256": sha(body), "template_sha256": sha(template),
                    "automatic_replay": False}
        self.owner.write(marker + ".started.json", expected)
        operation = checked_json(self.http, "PATCH", before["name"], body, query={"updateMask": mask})
        name = operation.get("name")
        need(type(name) is str and OPERATION.fullmatch(name), "document_transition_ack_unknown_reconcile_only")
        assert isinstance(name, str)
        self.owner.write(marker + ".acknowledged.json", {"operation": name, **expected})
        step_deadline = min(self.deadline, time.monotonic() + 300)
        while True:
            observed = checked_json(self.http, "GET", name)
            need(observed.get("name") == name and "error" not in observed,
                 "document_transition_operation_failed_reconcile_only")
            if observed.get("done", False) is True:
                break
            need(time.monotonic() < step_deadline, "document_transition_deadline_reconcile_only")
            time.sleep(min(2, max(0, step_deadline - time.monotonic())))
        current = self.service(service)
        self.ready(service, current, uid=before["uid"])
        normalized = copy.deepcopy(current["template"])
        if "revision" in normalized:
            normalized["revision"] = normalized["revision"].rsplit("/", 1)[-1]
        need(int(current["generation"]) == int(before["generation"]) + 1
             and normalized == template
             and current.get("labels", {}) == body.get("labels", before.get("labels", {})),
             "document_transition_readback_changed")
        # Service-level identity, ingress, IAM setting and every unrelated field
        # remain exact. Native output/status fields legitimately change.
        ignored = {"template", "traffic", "labels", "etag", "generation", "observedGeneration", "updateTime",
                   "lastModifier", "reconciling", "terminalCondition", "conditions", "latestReadyRevision",
                   "latestCreatedRevision", "trafficStatuses", "urls"}
        need({k: v for k, v in current.items() if k not in ignored}
             == {k: v for k, v in before.items() if k not in ignored},
             "document_transition_unrelated_configuration_changed")
        self.observed[service] = current
        self.owner.write(marker + ".verified.json", {**expected, "operation": name,
            "revision": current["latestReadyRevision"], "generation": str(current["generation"])})


def transition(image: str, build: dict[str, Any], native_journal: Path, journal: Path,
               *, http: Any, probe: Any, rescue_expired_v2: bool = False) -> dict[str, Any]:
    """Trusted controller entry: actual fresh native acceptance, never a boolean receipt."""
    need(type(rescue_expired_v2) is bool, "document_transition_rescue_mode_invalid")
    owner = Journal(journal)
    try:
        acceptance = probe.verify_successor(image, build, native_journal)
        require_current_acceptance(acceptance, image, probe)
        owner.write("native-acceptance.json", asdict(acceptance))
        run = Controller(http, probe, owner, candidate_image=image, rescue_expired_v2=rescue_expired_v2)
        if rescue_expired_v2:
            verify_guarded_api(run, probe)
        captured = {}
        prior_image = None
        prior_policy = None
        for name in SERVICES:
            value = run.service(name)
            run.ready(name, value, uid=probe.SERVICE_UID if name == WORKER else None)
            run.observed[name] = value
            env = environment(value["template"])
            if name == WORKER:
                prior_image = value["template"]["containers"][0]["image"]
                if prior_image != probe.BASE_IMAGE:
                    if rescue_expired_v2:
                        probe.verify_expired_current_v2_for_retirement(prior_image, value)
                    else:
                        probe.verify_current_successor(prior_image, value)
                prior_policy = probe.policy_for_serving(value, prior_image)
            else:
                need(env.get("DOCUMENT_WORKER_IMAGE_DIGEST") == {"name": "DOCUMENT_WORKER_IMAGE_DIGEST", "value": prior_image},
                     "document_transition_existing_mixed_pins_reconcile_only")
                captured[name] = {key: copy.deepcopy(env.get(key)) for key in PAUSE[name]}
                need(all(row is None or set(row) == {"name", "value"} and row["value"] in {"true", "false"}
                         for row in captured[name].values()), "document_transition_admission_flag_unverified")
        need(prior_image != image, "document_transition_already_current_reconcile_only")
        need(not rescue_expired_v2 or prior_image != probe.BASE_IMAGE and prior_policy == probe.POLICY,
             "document_transition_rescue_v2_predecessor_required")
        if rescue_expired_v2:
            verify_rescue_caller_pins(run, probe, prior_image)
        else:
            probe.verify_caller_pins(run, prior_image, policy_sha256=prior_policy)
        owner.write("captured-flags.json", captured)
        # No claim of quiescence: old in-flight callers can finish or safely503.
        for name in CALLERS:
            template = mutate_template(run.observed[name], name, "close", build["nonce"], image, {}, captured[name])
            run.patch(name, "close", template, acceptance=acceptance)
        acceptance = probe.verify_successor(image, build, native_journal)
        require_current_acceptance(acceptance, image, probe)
        labels = dict(zip(LABELS, (build["build_id"], build["nonce"], acceptance.job_name.rsplit("/", 1)[-1]), strict=True))
        run.patch(WORKER, "worker", mutate_template(run.observed[WORKER], WORKER, "worker", build["nonce"], image, {}, {},
            worker_policy_sha256=acceptance.policy_sha256), acceptance=acceptance, labels=labels)
        probe.verify_current_successor(image, run.observed[WORKER])
        pins = dict(zip(PINS, (image, acceptance.policy_sha256, acceptance.renderer_version,
                               acceptance.font_manifest_sha256), strict=True))
        for name in CALLERS:
            template = mutate_template(run.observed[name], name, "pins", build["nonce"], image, pins, captured[name])
            run.patch(name, "pins", template, acceptance=acceptance)
        probe.verify_caller_pins(run, image)
        probe.verify_current_successor(image, run.observed[WORKER])
        if rescue_expired_v2:
            # The old API was closed before this transition. Reopen its
            # guarded route only after the new worker and all caller pins are
            # natively proven. The captured original remains in the journal.
            owner.write("rescue-admission-intent.json", {"api_release": GUARDED_API_RELEASE,
                "worker_image": image, "native_acceptance_sha256": sha(asdict(acceptance)),
                "resume_direct_upload_enabled": True, "automatic_replay": False})
            captured = copy.deepcopy(captured)
            captured[CALLERS[0]]["RESUME_DIRECT_UPLOAD_ENABLED"] = {
                "name": "RESUME_DIRECT_UPLOAD_ENABLED", "value": "true"}
        for name in CALLERS:
            template = mutate_template(run.observed[name], name, "open", build["nonce"], image, {}, captured[name])
            run.patch(name, "open", template, acceptance=acceptance)
        probe.verify_caller_pins(run, image)
        probe.verify_current_successor(image, run.observed[WORKER])
        need(all(run.service(name) == run.observed[name] for name in SERVICES),
             "document_transition_final_join_changed")
        result = {"status": "document_refresh_serving", "image": image, "prior_image": prior_image,
                  "native_acceptance_sha256": sha(asdict(acceptance)), "all_three_caller_pins_verified": True,
                  "admission_restored": True, "synchronous_callers_drained": False,
                  "current_native_proof_retained": True, "automatic_replay": False}
        if rescue_expired_v2:
            result["expired_v2_rescued"] = True
        owner.write("complete.json", result)
        return result
    finally:
        owner.close()


def reconcile(journal: Path, *, http: Any) -> dict[str, Any]:
    """Read the EXACT retained handles. Never PATCH, restore flags, or start a new run."""
    info = journal.lstat()
    need(journal.is_absolute() and journal.resolve() == journal and stat.S_ISDIR(info.st_mode)
         and info.st_uid == os.getuid() and stat.S_IMODE(info.st_mode) == 0o700,
         "document_transition_private_journal_required")
    rows = []
    for path in sorted(journal.glob("*.acknowledged.json")):
        status = path.lstat()
        need(stat.S_ISREG(status.st_mode) and status.st_nlink == 1 and status.st_uid == os.getuid()
             and stat.S_IMODE(status.st_mode) == 0o600 and status.st_size <= 65536,
             "document_transition_journal_unverified")
        ack = json.loads(path.read_bytes())
        name = ack.get("operation")
        need(type(name) is str and OPERATION.fullmatch(name), "document_transition_handle_refused")
        value = checked_json(http, "GET", name)
        need(value.get("name") == name, "document_transition_handle_changed")
        rows.append({"operation": name, "done": value.get("done", False) is True,
                     "error_present": "error" in value})
    return {"status": "read_only_reconciliation", "operations": rows,
            "unknown_without_ack": len(list(journal.glob("*.started.json"))) - len(rows),
            "writes": 0, "replay_authorized": False}
