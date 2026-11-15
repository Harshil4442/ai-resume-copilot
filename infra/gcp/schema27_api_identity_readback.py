"""Validate a zero-traffic API identity transition from private local observations.

The operator uses existing native readers to collect metadata separately.
Project full responses in memory with project_service before saving a review
bundle. This command reads only that local bundle and makes no provider call.
In --prepare mode the reviewed bundle pins the prior API projection and ETag.
Pass the independent native-shaped observations on private stdin. The request
is assembled in memory and only its digest is printed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import sys
from dataclasses import dataclass
from pathlib import Path

from infra.gcp import schema27_continuity_scope as scope

# isort: split
import document_application_context as application

rolling = scope.rolling
API, *WORKERS = rolling.release.SERVICES
COORDINATOR = "hirewiz-resume-coordinator"
COORDINATOR_ACCOUNT = f"{COORDINATOR}@{rolling.release.PROJECT}.iam.gserviceaccount.com"
DOCUMENTS = ("hirewiz-document-inspector", "hirewiz-native-tex")
SERVICES = (API, *WORKERS, COORDINATOR)
KIND = "hirewiz_schema27_api_identity_readback_v1"
PREPARE_KIND = "hirewiz_schema27_api_identity_prepare_v1"
MAX_REVIEW = 131072
MAX_NATIVE_INPUT = 1048576
REVISION_TRAFFIC = "TRAFFIC_TARGET_ALLOCATION_TYPE_REVISION"
IMAGE = f"{rolling.release.REGION}-docker.pkg.dev/{rolling.release.PROJECT}/cloud-run-source-deploy/hirewiz"


def need(condition, code):
    rolling.need(condition, "schema27_api_identity_" + code)


def _hash(value) -> bool:
    return type(value) is str and rolling.HASH.fullmatch(value) is not None


@dataclass(frozen=True, slots=True)
class Identity:
    email: str
    uid: str

    def matches(self, observed):
        return (observed == {"email": self.email, "uid": self.uid, "keyless": True, "enabled": True}
                and observed["enabled"] is True and observed["keyless"] is True)


@dataclass(frozen=True, slots=True)
class Transition:
    old_api: Identity
    new_api: Identity
    service_uid: str
    old_revision: str
    candidate_revision: str
    candidate_tag: str | None = None

    def validate(self):
        suffix = re.escape("@" + rolling.release.PROJECT + ".iam.gserviceaccount.com")
        need(self.old_api.email == rolling.RUNTIME
             and re.fullmatch(r"hirewiz-api-[a-z0-9](?:[a-z0-9-]{0,16}[a-z0-9])?" + suffix, self.new_api.email)
             and self.old_api.uid != self.new_api.uid
             and all(re.fullmatch(r"[1-9][0-9]{20}", item.uid) for item in (self.old_api, self.new_api))
             and self.service_uid == application.UIDS[API], "principal_pin")
        need(self.old_revision != self.candidate_revision
             and all(rolling.short_revision(revision, API) == revision for revision in
                     (self.old_revision, self.candidate_revision))
             and (self.candidate_tag is None or re.fullmatch(r"[a-z][a-z0-9-]{0,62}", self.candidate_tag)),
             "revision_pin")


def _transition(data):
    plan = Transition(**(data | {"old_api": Identity(**data["old_api"]), "new_api": Identity(**data["new_api"])}))
    plan.validate()
    return plan


def project_identity(account, keys):
    """Retain no credential or key-material fields."""
    return {"email": account["email"], "uid": account["uniqueId"],
            "enabled": account.get("disabled", False) is False,
            "keyless": type(keys) is list and all(
                item.get("keyType") == "SYSTEM_MANAGED" for item in keys
            )}


def _template(name, template):
    env = rolling.environment(template)
    if name == API:
        need(re.fullmatch(re.escape(IMAGE) + r"@sha256:[a-f0-9]{64}", template["containers"][0]["image"])
             and re.fullmatch(r"[a-f0-9]{40}", env.get("APP_RELEASE", {}).get("value", "")), "api_source_shape")
    return {
        "account": template["serviceAccount"],
        "common_sha256": rolling.sha({key: value for key, value in template.items()
                                      if key not in {"serviceAccount", "revision"}}),
        "checkout_closed": env.get("RAZORPAY_CHECKOUT_ENABLED", {
            "name": "RAZORPAY_CHECKOUT_ENABLED", "value": "false",
        }) == {"name": "RAZORPAY_CHECKOUT_ENABLED", "value": "false"},
    }


def project_service(name, service, revisions):
    """Turn native service/revision responses into credential-free fingerprints."""
    resource = rolling.PARENT + "/services/" + name
    need(name in SERVICES and service.get("name") == resource
         and service.get("reconciling", False) is False
         and re.fullmatch(r"[1-9][0-9]{0,18}", str(service["generation"]))
         and str(service["generation"]) == str(service["observedGeneration"])
         and service.get("terminalCondition", {}).get("state") == "CONDITION_SUCCEEDED", "service_not_ready")
    template = service["template"]
    ready = rolling.short_revision(service["latestReadyRevision"], name)
    created = rolling.short_revision(service["latestCreatedRevision"], name)
    if "revision" in template:
        need(rolling.short_revision(template["revision"], name) == created, "template_revision")
    traffic = application.allocations(name, service)
    targets = {ready, created, *(row["revision"] for row in traffic)}
    need(set(revisions) == targets, "revision_inventory")
    projected = {}
    for revision, row in revisions.items():
        need(row.get("name") == resource + "/revisions/" + revision
             and row.get("service") in {name, resource}
             and re.fullmatch(r"[a-f0-9-]{36}", row.get("uid", ""))
             and any(condition.get("type") == "Ready" and condition.get("state") == "CONDITION_SUCCEEDED"
                     for condition in row.get("conditions", [])), "revision_not_ready")
        projected[revision] = _template(name, application.template(row, revision=True)) | {"uid": row["uid"]}
    return {"uid": service["uid"], "generation": int(service["generation"]), "ready": ready, "created": created,
            "static_sha256": rolling.static_sha(service), "template": _template(name, application.template(template)),
            "traffic": traffic, "revisions": projected}


def _prior_api(plan, prior):
    need(prior["uid"] == plan.service_uid and _hash(prior["static_sha256"])
         and type(prior["generation"]) is int and prior["generation"] > 0
         and prior["ready"] == prior["created"] == plan.old_revision, "api_prior_service")
    old = prior["template"]
    need(set(old) == {"account", "common_sha256", "checkout_closed"} and old["account"] == plan.old_api.email
         and _hash(old["common_sha256"]) and old["checkout_closed"] is True, "api_prior_template")
    traffic = rolling.pinned_traffic(API, {"trafficStatuses": prior["traffic"]}, prior_revision=plan.old_revision)
    need(len(traffic) == len(prior["traffic"]) and all(row.get("type") in {
        REVISION_TRAFFIC, "TRAFFIC_TARGET_ALLOCATION_TYPE_LATEST",
    } for row in prior["traffic"]) and not any(
        row["revision"] == plan.candidate_revision
        or (plan.candidate_tag is not None and row.get("tag") == plan.candidate_tag) for row in traffic
    ), "prior_traffic")
    retained = prior["revisions"]
    need(set(retained) == {row["revision"] for row in traffic}
         and retained[plan.old_revision] == old | {"uid": retained[plan.old_revision]["uid"]}
         and all(row["account"] == plan.old_api.email and row["checkout_closed"] is True
                 for row in retained.values()), "api_prior_revisions")
    return old, traffic, retained


def _validate_services(plan, before, after):
    need(set(before) == set(after) == set(SERVICES), "service_scope")
    for service, account in {
        **dict.fromkeys(WORKERS, rolling.RUNTIME), COORDINATOR: COORDINATOR_ACCOUNT,
    }.items():
        other = before[service]
        need(other == after[service] and other["template"]["account"] == account
             and all(revision["account"] == account for revision in other["revisions"].values()),
             "worker_or_coordinator_changed")
    prior, target = before[API], after[API]
    need(set(prior) == set(target) == {
        "uid", "generation", "ready", "created", "static_sha256", "template", "traffic", "revisions",
    } and prior["uid"] == target["uid"]
         and prior["static_sha256"] == target["static_sha256"]
         and type(target["generation"]) is int
         and target["generation"] == prior["generation"] + 1
         and target["ready"] == target["created"] == plan.candidate_revision, "api_service_changed")
    old, traffic, retained = _prior_api(plan, prior)
    new = target["template"]
    need(new["checkout_closed"] is True and new == (old | {"account": plan.new_api.email}), "api_template_diff")
    if plan.candidate_tag is not None:
        traffic.append({"type": REVISION_TRAFFIC, "revision": plan.candidate_revision, "tag": plan.candidate_tag})
    need(target["traffic"] == sorted(traffic, key=rolling.canonical), "api_traffic_changed")
    candidate = target["revisions"][plan.candidate_revision]
    need(re.fullmatch(r"[a-f0-9-]{36}", candidate["uid"])
         and all(row["uid"] != candidate["uid"] for row in retained.values())
         and candidate == new | {"uid": candidate["uid"]}
         and target["revisions"] == retained | {plan.candidate_revision: candidate}, "api_revision_diff")


def _validate_callers(plan, before, after):
    need(set(before) == set(after) == {*DOCUMENTS, *SERVICES}, "caller_scope")
    old, new = ("serviceAccount:" + identity.email for identity in (plan.old_api, plan.new_api))
    for name in SERVICES:
        need(before[name] == after[name], "unchanged_service_callers")
    for name in DOCUMENTS:
        previous, actual = before[name], after[name]
        need(set(previous) <= {"version", "etag", "bindings"} and set(actual) <= {"version", "etag", "bindings"}
             and previous.get("version") == actual.get("version"), "document_policy_shape")
        bindings = previous["bindings"]
        need(type(bindings) is list and all(type(row["members"]) is list
             and len(row["members"]) == len(set(row["members"]))
             and (row["role"] != "roles/run.invoker"
                  or not {"allUsers", "allAuthenticatedUsers"}.intersection(row["members"])) for row in bindings),
             "document_prior_members")
        invokers = [row for row in bindings if row["role"] == "roles/run.invoker" and "condition" not in row]
        need(len(invokers) == 1 and set(invokers[0]) == {"role", "members"}
             and old in invokers[0]["members"] and all(new not in row["members"] for row in bindings),
             "document_prior_callers")
        updated = [row | {"members": sorted([*row["members"], new])} if row is invokers[0] else row
                   for row in bindings]
        normalized = [row | {"members": sorted(row["members"])} for row in actual["bindings"]]
        checked = [row | {"members": sorted(row["members"])} for row in updated]
        need(sorted(normalized, key=rolling.canonical) == sorted(checked, key=rolling.canonical),
             "document_caller_diff")


def prepare(review, native):
    """Compute the exact request hash from the reviewed API preimage and fresh observation."""
    need(type(review) is dict and set(review) == {"kind", "plan", "before", "etag"}
         and review["kind"] == PREPARE_KIND and type(native) is dict
         and set(native) == {"service", "revisions", "candidate_lookup"}, "prepare_kind")
    plan = _transition(review["plan"])
    need(plan.candidate_tag is None, "prepare_requires_untagged")
    service = native["service"]
    etag = review["etag"]
    need(type(etag) is str and re.fullmatch(r"[!-~]{1,256}", etag) and service.get("etag") == etag,
         "prepare_etag_changed")
    candidate = service["name"] + "/revisions/" + plan.candidate_revision
    lookup = native["candidate_lookup"]
    need(lookup == {"name": candidate, "status": 404} and type(lookup["status"]) is int
         and plan.candidate_revision not in native["revisions"], "prepare_candidate_not_absent")
    prior = project_service(API, service, native["revisions"])
    need(prior == review["before"], "prepare_preimage_changed")
    _, traffic, _ = _prior_api(plan, prior)
    template = service["template"]
    need(rolling.environment(template).get("AUTO_DB_MIGRATE") == {"name": "AUTO_DB_MIGRATE", "value": "false"},
         "prepare_startup_migration_not_closed")
    body = {"name": service["name"], "etag": etag, "traffic": traffic,
            "template": template | {"revision": plan.candidate_revision, "serviceAccount": plan.new_api.email}}
    return {"status": "api_identity_request_prepared", "request_sha256": rolling.sha(body),
            "native_approval": False, "native_mutations": 0}


def validate(review):
    need(type(review) is dict and set(review) == {"kind", "plan", "before", "after"}
         and review["kind"] == KIND, "review_kind")
    plan = _transition(review["plan"])
    before, after = review["before"], review["after"]
    need(set(before) == set(after) == {"services", "identities", "callers"}
         and before["identities"] == after["identities"] and set(before["identities"]) == {"old_api", "new_api"}
         and plan.old_api.matches(before["identities"]["old_api"])
         and plan.new_api.matches(before["identities"]["new_api"]), "identity_readback")
    _validate_services(plan, before["services"], after["services"])
    _validate_callers(plan, before["callers"], after["callers"])
    return {"status": "api_identity_readback_validated", "native_approval": False, "native_mutations": 0}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--review", type=Path, required=True)
    parser.add_argument("--sha256", required=True)
    parser.add_argument("--prepare", action="store_true", help="hash an untagged request using bounded private stdin")
    args = parser.parse_args()
    try:
        descriptor = os.open(args.review, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(descriptor, "rb") as stream:
            info = os.fstat(stream.fileno())
            need(stat.S_ISREG(info.st_mode) and info.st_uid == os.getuid()
                 and stat.S_IMODE(info.st_mode) == 0o600 and 0 < info.st_size <= MAX_REVIEW, "private_review")
            raw = stream.read(MAX_REVIEW + 1)
        need(len(raw) == info.st_size and _hash(args.sha256)
             and hashlib.sha256(raw).hexdigest() == args.sha256, "review_hash")
        review = rolling.release._json(raw)
        if args.prepare:
            need(not sys.stdin.isatty(), "prepare_requires_private_pipe")
            native = sys.stdin.buffer.read(MAX_NATIVE_INPUT + 1)
            need(0 < len(native) <= MAX_NATIVE_INPUT, "prepare_input_bound")
            result = prepare(review, rolling.release._json(native))
        else:
            result = validate(review)
        print(json.dumps(result))
        return 0
    except (OSError, RuntimeError, ValueError, TypeError, KeyError, AttributeError):
        print(json.dumps({"status": "api_identity_readback_refused", "native_approval": False}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
