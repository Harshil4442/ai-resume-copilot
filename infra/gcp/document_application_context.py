"""Reviewed current app sources and homogeneous serving aliases for maintenance.

Document acceptance/source is separate from caller APP_RELEASE. Unknown sources,
mixed templates and unproven builds are refused before a new maintenance Build.
This module has no cloud mutation or credential loading entrypoint.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path

import rolling_release as rolling

PROJECT = "ai-resume-parser-482412"
REPOSITORY = "Harshil4442/ai-resume-copilot"
ROOT = Path(__file__).resolve().parents[2]
PARENT = rolling.PARENT
CALLERS = ("ai-resume-parser", "hirewiz-analysis-worker", "hirewiz-resume-coordinator")
UIDS = dict(zip(CALLERS, ("ff2d8503-c5cf-451d-8fef-687f17e9c7e1", "fcfccff4-104f-4f6d-9faf-adc50224155a",
                        "43e8dddd-b969-4501-a5be-24138d6bc263"), strict=True))
ACCOUNTS = {CALLERS[0]: rolling.RUNTIME, CALLERS[1]: rolling.RUNTIME,
    CALLERS[2]: CALLERS[2] + "@" + PROJECT + ".iam.gserviceaccount.com"}
OUTPUTS = frozenset({"name", "service", "uid", "generation", "observedGeneration", "createTime",
    "updateTime", "creator", "etag", "conditions", "scalingStatus", "launchStage", "logUri", "revision"})
CI_JOBS = {"backend", "frontend", "security-and-container", "browser-companion", "cold-browser-journey"}
WIRE = {
    "backend/app/services/document_ingestion.py": "6214ac7659b7521af30e6f63003526f9c10a7569db4e8394e757dbdb112eb034",
    "backend/app/domains/resume_uploads/scanner.py": "8f4c21d902754178b3eafc97645beddf6e9d79e60c6050c0a81540f2008ee2af",
    "backend/app/services/native_tex.py": "6a027b2d61b40e36869f41f9de5fbb2244c79636ee627d085adc38038bdabe22",
}


class ApplicationDenied(RuntimeError):
    pass


def need(value, code):
    if not value:
        raise ApplicationDenied("document_application_" + code)


def command(root, args):
    result = subprocess.run(args, cwd=root, stdin=subprocess.DEVNULL, capture_output=True, timeout=15, check=False)
    need(result.returncode == 0 and len(result.stdout) <= 1048576, "source_or_ci_read_unavailable")
    return result.stdout


@dataclass(frozen=True)
class Source:
    release: str
    image_digest: str
    build_id: str
    ci_run_id: int

    @property
    def image(self):
        return "us-central1-docker.pkg.dev/" + PROJECT + "/cloud-run-source-deploy/hirewiz@" + self.image_digest

    def validate(self):
        need(re.fullmatch(r"[a-f0-9]{40}", self.release)
             and re.fullmatch(r"sha256:[a-f0-9]{64}", self.image_digest)
             and re.fullmatch(r"[a-f0-9-]{36}", self.build_id)
             and type(self.ci_run_id) is int and self.ci_run_id > 0, "reviewed_source_invalid")


def sources(root):
    value = json.loads((root / "infra/document-processing/reviewed-application-builds.json").read_bytes())
    need(set(value) == {"version", "sources"} and value["version"] == 1
         and 1 <= len(value["sources"]) <= 8, "reviewed_registry_invalid")
    pins = tuple(Source(**row) for row in value["sources"])
    for pin in pins:
        pin.validate()
    need(len({pin.release for pin in pins}) == len(pins), "reviewed_registry_invalid")
    return {pin.release: pin for pin in pins}


def verify_ci(pin, run, workflow):
    jobs = run.get("jobs", [])
    need(workflow.get("path") == ".github/workflows/ci.yml" and workflow.get("head_sha") == pin.release
         and workflow.get("event") in {"push", "pull_request"}
         and workflow.get("repository", {}).get("full_name") == REPOSITORY
         and run.get("headSha") == pin.release and run.get("workflowName") == "CI"
         and run.get("status") == "completed" and run.get("conclusion") == "success"
         and len(jobs) == 5 and {row.get("name") for row in jobs} == CI_JOBS
         and all(row.get("status") == "completed" and row.get("conclusion") == "success" for row in jobs),
         "exact_five_ci_jobs_required")


def verify_build(pin, build):
    tag = pin.image.split("@")[0] + ":" + pin.release
    provenance = build.get("sourceProvenance", {}).get("resolvedGitSource", {})
    steps = build.get("steps", [])
    allowed = (["build", "--tag", tag, "."], ["build", "-t", tag, "."], ["build", "--tag=" + tag, "."])
    images = build.get("results", {}).get("images", [])
    need(build.get("projectId") == PROJECT and build.get("id") == pin.build_id
         and build.get("status") == "SUCCESS" and provenance.get("revision") == pin.release
         and provenance.get("url") == f"https://github.com/{REPOSITORY}.git"
         and provenance.get("dir", ".") in {"", "."}
         and len(steps) == 2 and steps[0].get("args") in allowed
         and steps[1].get("args") == ["push", tag]
         and all(step.get("name") == "gcr.io/cloud-builders/docker"
                 and not any(step.get(key) for key in ("env", "secretEnv", "entrypoint", "script", "volumes"))
                 and step.get("dir", ".") in {"", "."} for step in steps)
         and not any(build.get("options", {}).get(key) for key in ("env", "secretEnv", "volumes", "automapSubstitutions"))
         and not build.get("availableSecrets") and not build.get("secrets")
         and len(images) == 1 and images[0].get("name") == tag and images[0].get("digest") == pin.image_digest,
         "native_git_build_required")


def template(value):
    return {key: row for key, row in value.items() if key not in OUTPUTS}


def ready(service, name):
    need(name in CALLERS and service.get("name") == PARENT + "/services/" + name
         and re.fullmatch(r"[a-f0-9-]{36}", service.get("uid", ""))
         and type(service.get("generation")) in {int, str}
         and re.fullmatch(r"[1-9][0-9]{0,18}", str(service["generation"]))
         and str(service.get("generation")) == str(service.get("observedGeneration"))
         and service.get("reconciling", False) is False
         and type(service.get("etag")) is str and bool(service["etag"])
         and service.get("terminalCondition", {}).get("state") == "CONDITION_SUCCEEDED", "caller_not_ready")


def serving(clients, service, image, name, uid=None):
    """All currently reachable aliases must be the same immutable app template.

    latestReady may name an unserved paused revision after maintenance. Inspect
    the physical allocation and actual Ready created revision independently.
    """
    ready(service, name)
    need(uid is None or service["uid"] == uid, "caller_uid_changed")
    expected = template(service["template"])
    need(expected.get("serviceAccount") == ACCOUNTS[name]
         and expected.get("containers", [{}])[0].get("image") == image, "caller_identity_changed")
    traffic = allocations(name, service)
    active = next(row["revision"] for row in traffic if row.get("percent") == 100)
    created = rolling.short_revision(service["latestCreatedRevision"], name)
    result = None
    for revision in sorted({created} | {row["revision"] for row in traffic}):
        value = clients.revision(PARENT + "/services/" + name + "/revisions/" + revision)
        need(value.get("name") == PARENT + "/services/" + name + "/revisions/" + revision
             and value.get("service") in {name, PARENT + "/services/" + name}
             and re.fullmatch(r"[a-f0-9-]{36}", value.get("uid", ""))
             and any(row.get("type") == "Ready" and row.get("state") == "CONDITION_SUCCEEDED"
                     for row in value.get("conditions", []))
             and template(value) == expected, "mixed_or_unknown_reachable_template")
        if revision == active:
            result = value
    need(result is not None, "physical_serving_revision_required")
    return result


def allocations(name, value):
    """Resolve physical revisions without losing original LATEST/tag intent."""
    physical = rolling.pinned_traffic(name, value)
    intent = value.get("traffic")
    need(type(intent) is list and len(intent) == len(physical), "traffic_shape_changed")
    result = []
    for row in intent:
        need(type(row) is dict and set(row) <= {"type", "revision", "percent", "tag"}, "traffic_shape_changed")
        matches = [item for item in physical if item.get("tag") == row.get("tag")
                   and item.get("percent", 0) == row.get("percent", 0)]
        need(len(matches) == 1 and row.get("type") in {
            "TRAFFIC_TARGET_ALLOCATION_TYPE_REVISION", "TRAFFIC_TARGET_ALLOCATION_TYPE_LATEST"}, "traffic_shape_changed")
        item = matches[0]
        need(row["type"].endswith("_LATEST") and not row.get("revision")
             or row["type"].endswith("_REVISION")
             and rolling.short_revision(row.get("revision", ""), name) == item["revision"], "traffic_shape_changed")
        result.append({**item, "type": row["type"]})
    need(len({rolling.canonical(row) for row in result}) == len(physical), "traffic_shape_changed")
    return sorted(result, key=rolling.canonical)


def phase_traffic(original, name, revision):
    """Same explicit allocation/tag shape, moved to its homogeneous successor."""
    rows = []
    for item in allocations(name, original):
        row = copy.deepcopy(item)
        if row["type"] == "TRAFFIC_TARGET_ALLOCATION_TYPE_LATEST":
            row.pop("revision", None)
        else:
            row["revision"] = revision
        rows.append(row)
    return sorted(rows, key=rolling.canonical)


class Applications:
    def __init__(self, root, clients, get_json, uids):
        self.root, self.clients, self.get_json = root, clients, get_json
        self.registry, self.verified = sources(root), {}
        need(set(uids) == set(CALLERS) and all(re.fullmatch(r"[a-f0-9-]{36}", uid) for uid in uids.values()),
             "caller_uid_pins_required")
        self.uids = dict(uids)

    def serving(self, clients, value, image, name, uid=None):
        need(uid is None or uid == self.uids[name], "caller_uid_changed")
        release = rolling.environment(value["template"]).get("APP_RELEASE", {}).get("value")
        need(release in self.registry and self.registry[release].image == image
             and value["template"].get("labels", {}).get("hirewiz-source") == release,
             "current_app_source_binding_changed")
        self.provenance(self.registry[release])
        return serving(clients, value, image, name, self.uids[name])

    def phase_traffic(self, original, name, revision):
        return phase_traffic(original, name, revision)

    def provenance(self, pin):
        if pin.release in self.verified:
            return
        head = command(self.root, ["git", "rev-parse", "HEAD"]).decode().strip()
        need(os.getenv("GITHUB_REPOSITORY") == REPOSITORY and os.getenv("GITHUB_REF") == "refs/heads/main"
             and os.getenv("GITHUB_SHA") == head, "trusted_default_main_context_required")
        command(self.root, ["git", "merge-base", "--is-ancestor", pin.release, head])
        for path, digest in WIRE.items():
            need(hashlib.sha256(command(self.root, ["git", "show", pin.release + ":" + path])).hexdigest() == digest,
                 "document_wire_source_changed")
        run = json.loads(command(self.root, ["gh", "run", "view", str(pin.ci_run_id), "--repo", REPOSITORY,
            "--json", "headSha,status,conclusion,jobs,workflowName"]))
        workflow = json.loads(command(self.root, ["gh", "api", f"repos/{REPOSITORY}/actions/runs/{pin.ci_run_id}"]))
        verify_ci(pin, run, workflow)
        verify_build(pin, self.get_json(f"https://cloudbuild.googleapis.com/v1/{PARENT}/builds/{pin.build_id}"))
        self.verified[pin.release] = pin

    def observe(self):
        result = {}
        for name in CALLERS:
            current = self.clients.service(PARENT + "/services/" + name)
            env = rolling.environment(current["template"])
            release = env.get("APP_RELEASE", {}).get("value")
            need(release in self.registry, "unreviewed_current_app_source")
            pin = self.registry[release]
            need(current["template"]["containers"][0]["image"] == pin.image
                 and current["template"].get("labels", {}).get("hirewiz-source") == pin.release,
                 "current_app_source_binding_changed")
            self.provenance(pin)
            self.serving(self.clients, current, pin.image, name)
            result[name] = {"uid": current["uid"], "generation": str(current["generation"]),
                "source": asdict(pin), "template_sha256": rolling.sha(template(current["template"])),
                "physical_traffic": allocations(name, current)}
        return result
