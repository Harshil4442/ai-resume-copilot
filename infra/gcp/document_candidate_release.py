"""Exact Courier V3 promotion adapter, with fresh native proof before writes.

The V2 accepted policy, payload, receipt and renderer profile remain historical
contracts. Only the fixed V3 base and its fixed-recipe definitions-only children
qualify. Each successor needs fresh build, inventory and eleven-case evidence.
Unknown writes stop; a saved success or prior execution is never replayed.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "backend") not in sys.path:
    sys.path.insert(0, str(ROOT / "backend"))

from infra.gcp import document_candidate as profile  # noqa: E402
from infra.gcp import document_candidate_probe as candidate  # noqa: E402
from infra.gcp import document_refresh_probe as prior  # noqa: E402
from infra.gcp import document_refresh_transition as transition  # noqa: E402

NativeProofDenied = prior.NativeProofDenied
POLICY = profile.POLICY_SHA256
BASE_IMAGE = prior.BASE_IMAGE
SERVICE_UID = prior.SERVICE_UID
validate_serving = prior.validate_serving
policy_for_serving = prior.policy_for_serving
BUILD_ID = "53c0db04-2ddf-4221-9702-b7c566c5598c"
BUILD_NONCE = "f4128678f163440f9bbf46669d7d641a"
BUILD_SHA256 = "5b7bf19d2833a47236f749b724f87074e6ea766b53409bd8bbc8a3e3ec557742"
PAYLOAD_SHA256 = "f0f36d91d979a876b544d3e37da75008fb71aae13d3338b9d16dc305a66c3602"
RECEIPT_SOURCE_SHA256 = "55577a07ecba96b9cc1c900e19e83bcb5819e85d6710e4b0aecdd18284290c99"
INVENTORY_RECEIPT_SHA256 = "35c58d60ac087ccb80655783cdc46ebab744c85d4da11e0a8717436f630112cd"
HANDLES = {
    "inventory": {"nonce": "9018d279450844a39d3942a5fa436165",
                  "job_uid": "13f9e9b3-2518-4204-b990-97a0ea82c6ca",
                  "execution": "hirewiz-doc-six-9018d2794508-wsjgx",
                  "execution_uid": "24e2dcfc-ce7e-4713-80e7-01d9d94d61f2",
                  "request_sha256": "e63e44750b3d829346908e7c1c971bd714c623cd3f0dcfda06988fc623c0c3bb"},
    "corpus": {"nonce": "5dcb811dac92449da9890f7d2fc7bd18",
               "job_uid": "0b2b67f7-2f8b-4433-8940-92a800a9c029",
               "execution": "hirewiz-doc-six-5dcb811dac92-dthlf",
               "execution_uid": "2a3a0064-3a16-4f91-8f32-70ef5b253f8e",
               "request_sha256": "170d8ae9219e16a16073859f996c7fa3135660f9214ec2db6ec2ff88fc09ad4b"},
}
LABELS = {"hirewiz-refresh-build": BUILD_ID, "hirewiz-refresh-nonce": BUILD_NONCE,
          "hirewiz-refresh-job": "hirewiz-doc-six-" + HANDLES["corpus"]["nonce"][:12]}


def need(value: Any, code: str) -> None:
    prior.need(value, "document_candidate_release_" + code)


def environment(image: str = profile.IMAGE) -> dict[str, str]:
    return {"DOCUMENT_WORKER_URL": prior.WORKER_URL,
            "DOCUMENT_WORKER_IMAGE_DIGEST": image,
            "DOCUMENT_WORKER_POLICY_SHA256": POLICY,
            "DOCUMENT_WORKER_DOCX_RENDERER_VERSION": profile.RENDERER,
            "DOCUMENT_WORKER_DOCX_FONT_MANIFEST_SHA256": profile.FONT}


def source_contract() -> dict[str, str]:
    need(hashlib.sha256(candidate.rendered_payload(profile.IMAGE).encode()).hexdigest() == PAYLOAD_SHA256
         and hashlib.sha256((ROOT / "infra/gcp/document_candidate_receipt.py").read_bytes()).hexdigest()
         == RECEIPT_SOURCE_SHA256, "proof_sources_changed")
    need(hashlib.sha256((ROOT / profile.REFRESH_DOCKERFILE).read_bytes()).hexdigest() == profile.REFRESH_SHA256,
         "refresh_recipe_changed")
    runtime = candidate.source_contract()
    for mode, handle in HANDLES.items():
        need(candidate.sha(candidate.desired(profile.IMAGE, handle["nonce"], mode)) == handle["request_sha256"],
             "job_request_changed")
    return runtime


def build_identity(*, read_only_release: bool, build_id: str = BUILD_ID,
                   nonce: str = BUILD_NONCE, image: str = profile.IMAGE) -> dict[str, Any]:
    builds = prior.build_module()
    verifier = builds.verify_current_build_release if read_only_release else builds.verify_current_build
    if image == profile.IMAGE:
        need(build_id == BUILD_ID and nonce == BUILD_NONCE, "build_changed")
        build = verifier(build_id, nonce, image)
        need(candidate.sha(build) == BUILD_SHA256, "build_changed")
    else:
        build = verifier(build_id, nonce, image, candidate_v3=True)
        need(build.get("kind") == "native_cloud_build" and build.get("fixed_renderer_base") == profile.IMAGE
             and build.get("refresh_dockerfile_sha256") == profile.REFRESH_SHA256,
             "refresh_parent_changed")
    need(build.get("policy_sha256") == POLICY, "build_changed")
    return build


def handles(build: dict[str, Any]) -> dict[str, dict[str, str]]:
    if build["immutable_image"] == profile.IMAGE:
        need(build["build_id"] == BUILD_ID and build["nonce"] == BUILD_NONCE, "build_changed")
        return HANDLES
    nonce = build["nonce"]
    need(type(nonce) is str and re.fullmatch(r"[a-f0-9]{32}", nonce), "build_changed")
    return {"inventory": {"nonce": hashlib.sha256(("inventory:" + nonce).encode()).hexdigest()[:32]},
            "corpus": {"nonce": nonce}}


def _observation(clients: Any, mode: str, build: dict[str, Any] | None = None) -> tuple[dict, dict, dict]:
    image = profile.IMAGE if build is None else build["immutable_image"]
    handle = HANDLES[mode] if build is None else handles(build)[mode]
    plan = {"image": image, "mode": mode, "job_nonce": handle["nonce"]}
    job_name = candidate.desired(image, handle["nonce"], mode)["name"]
    job = clients.job(job_name)
    candidate.validate_job(job, plan)
    need("job_uid" not in handle or job["uid"] == handle["job_uid"], "job_replaced")
    execution_name = (job_name + "/executions/" + handle["execution"] if "execution" in handle else
                      prior.execution_reference(job, job.get("latestCreatedExecution", {}).get("name")))
    execution = clients.execution(execution_name)
    candidate.validate_execution(execution, job, plan)
    need(execution["name"] == execution_name
         and ("execution_uid" not in handle or execution["uid"] == handle["execution_uid"]), "execution_replaced")
    return job, execution, candidate.native_payload(clients, job, execution, mode)


def validate_evidence(inventory: Any, corpus: Any, runtime: dict[str, str], *, now: datetime,
                      image: str = profile.IMAGE, fresh_candidate: bool = True) -> str:
    """Validate both actual payloads; saved booleans and ten-case V2 are invalid."""
    observed = candidate.validate_inventory(inventory, image, runtime)
    need((image != profile.IMAGE or candidate.sha(inventory) == INVENTORY_RECEIPT_SHA256)
         and observed == {"renderer_version": profile.RENDERER, "font_manifest_sha256": profile.FONT},
         "inventory_changed")
    try:
        complete = candidate.receipt.validate(corpus, image, POLICY, runtime)
    except (ValueError, TypeError, KeyError, OverflowError):
        raise prior.NativeProofDenied("document_candidate_release_corpus_invalid") from None
    need(complete is True
         and all(corpus["identity_preflight"][key] == expected for key, expected in observed.items()),
         "corpus_invalid")
    scans = [row["scan"] for row in corpus["cases"] if "scan" in row]
    need(len(scans) == 5 and all(row == scans[0] for row in scans), "scans_disagree")
    number, issued = scans[0]["definitions"].split("/", 1)
    stamp = datetime.fromisoformat(issued)
    need(type(fresh_candidate) is bool and re.fullmatch(r"[0-9]{1,10}", number) and stamp.tzinfo is not None
         and now.tzinfo is not None and timedelta(0) <= now - stamp < timedelta(days=1 if fresh_candidate else 3),
         "definitions_stale")
    return (stamp + timedelta(days=3)).isoformat()


def _acceptance(clients: Any, build: dict[str, Any], runtime: dict[str, str], *, fresh_candidate: bool = True) -> prior.NativeAcceptance:
    image = build["immutable_image"]
    _, _, inventory = _observation(clients, "inventory", build)
    job, execution, corpus = _observation(clients, "corpus", build)
    expires = validate_evidence(inventory, corpus, runtime, now=datetime.now(UTC), image=image,
                                fresh_candidate=fresh_candidate)
    return prior.NativeAcceptance(image, build["build_id"], build["build_resource"],
        build["source_archive_sha256"], build["refresh_dockerfile_sha256"], build["source_sha256"],
        job["name"], job["uid"], execution["name"], execution["uid"],
        hashlib.sha256(candidate.rendered_payload(image).encode()).hexdigest(),
        candidate.sha(corpus), POLICY, profile.FONT, profile.RENDERER, expires, 3, 11)


def verify_successor(image: str, build: dict[str, Any], journal: Path, *, read_only_release: bool = False) -> prior.NativeAcceptance:
    """Transition entry: freshly reread the already completed candidate only."""
    need(type(build) is dict and build.get("immutable_image") == image, "candidate_changed")
    if image == profile.IMAGE:
        need(candidate.sha(build) == BUILD_SHA256, "candidate_changed")
        plan = candidate.load_plan(journal / "plan.json")
        need(plan["mode"] == "corpus" and plan["image"] == image and plan["build_id"] == BUILD_ID
             and plan["build_nonce"] == BUILD_NONCE and plan["build_identity_sha256"] == BUILD_SHA256
             and plan["job_nonce"] == HANDLES["corpus"]["nonce"]
             and plan["inventory_receipt_sha256"] == INVENTORY_RECEIPT_SHA256, "plan_changed")
    else:
        binding = journal / "binding.json"
        need(binding.is_file() and not binding.is_symlink() and binding.stat().st_size <= 65536
             and json.loads(binding.read_bytes()) == {"version": 3, "image": image, "build_identity": build},
             "plan_changed")
    runtime = source_contract()
    actual = build_identity(read_only_release=read_only_release, build_id=build["build_id"], nonce=build["nonce"], image=image)
    need(actual == build, "build_changed")
    clients = prior.Clients(read_only_release=read_only_release)
    try:
        clients.preflight()
        return _acceptance(clients, actual, runtime)
    finally:
        clients.close()


def _verify_current(image: str, metadata: dict[str, Any], *, read_only_release: bool) -> prior.NativeAcceptance:
    need(prior.policy_for_serving(metadata, image) == POLICY,
         "serving_profile_changed")
    runtime = source_contract()
    labels = metadata.get("labels", {})
    build = build_identity(read_only_release=read_only_release, build_id=labels.get("hirewiz-refresh-build"),
                          nonce=labels.get("hirewiz-refresh-nonce"), image=image)
    selected = {"hirewiz-refresh-build": build["build_id"], "hirewiz-refresh-nonce": build["nonce"],
                "hirewiz-refresh-job": "hirewiz-doc-six-" + handles(build)["corpus"]["nonce"][:12]}
    need(all(labels.get(key) == value for key, value in selected.items()), "serving_profile_changed")
    clients = prior.Clients(read_only_release=read_only_release)
    try:
        snapshot = clients.preflight()
        actual = snapshot["service"]
        need(actual.get("uid") == SERVICE_UID
             and actual.get("template", {}).get("containers", [{}])[0].get("image") == image
             and all(actual.get("labels", {}).get(key) == value for key, value in selected.items())
             and prior.policy_for_serving(actual, image) == POLICY,
             "serving_profile_changed")
        return _acceptance(clients, build, runtime, fresh_candidate=False)
    finally:
        clients.close()


def verify_current_successor(image: str, metadata: dict[str, Any]) -> prior.NativeAcceptance:
    if prior.policy_for_serving(metadata, image) != POLICY:
        return prior.verify_current_successor(image, metadata)
    return _verify_current(image, metadata, read_only_release=False)


def verify_current_successor_for_release(image: str, metadata: dict[str, Any]) -> prior.NativeAcceptance:
    return _verify_current(image, metadata, read_only_release=True)


def verify_expired_current_v2_for_retirement(image: str, metadata: dict[str, Any]) -> None:
    """Maintenance-identity retirement proof; never emits stale serving authority."""
    prior.verify_expired_current_v2_for_retirement(image, metadata, read_only_release=False)


def verify_caller_pins(clients: Any, image: str, *, policy_sha256: str | None = None,
                       caller_serving_validator: Any = None) -> None:
    if policy_sha256 not in (None, POLICY):
        need(image != profile.IMAGE, "cross_profile_pins")
        return prior.verify_caller_pins(clients, image, policy_sha256=policy_sha256,
                                        caller_serving_validator=caller_serving_validator)
    need(type(image) is str and prior.IMAGE.fullmatch(image), "cross_profile_pins")
    for name in transition.CALLERS:
        service = clients.service(prior.PARENT + "/services/" + name)
        containers = service.get("template", {}).get("containers", [])
        need(type(containers) is list and len(containers) == 1, "caller_configuration_changed")
        serving = caller_serving_validator or prior.validate_serving
        revision = serving(clients, service, containers[0].get("image"), name)
        for row in (containers[0], revision["containers"][0]):
            env = row.get("env", [])
            need(type(env) is list and all(
                [entry for entry in env if entry.get("name") == key] == [{"name": key, "value": value}]
                for key, value in environment(image).items()), "caller_pins_changed")


def run_successor(build: dict[str, Any], journal: Path, *, adapter: Any = None) -> prior.NativeAcceptance:
    """Two fresh zero-retry Jobs, one create/run each; unknown outcomes stop."""
    target = sys.modules[__name__] if adapter is None else adapter
    version = 3 if adapter is None else 4
    if adapter is not None:
        from infra.gcp.document_source_release import SourceProbe
        need(isinstance(adapter, SourceProbe) and adapter.build == build, "source_adapter_changed")
    image = build["immutable_image"]
    need(image != target.profile.IMAGE and journal.is_absolute() and not journal.exists(), "fresh_journal_required")
    runtime = target.source_contract()
    need(target.build_identity(read_only_release=False, build_id=build["build_id"], nonce=build["nonce"], image=image) == build,
         "build_changed")
    journal.mkdir(mode=0o700)
    write = prior.build_module().write_new
    write(journal / "binding.json", {"version": version, "image": image, "build_identity": build})
    clients = prior.Clients()
    try:
        for mode, handle in target.handles(build).items():
            clients.preflight()
            body = target.candidate.desired(image, handle["nonce"], mode)
            try:
                clients.job(body["name"])
            except clients.errors.HttpError as error:
                need(error.status_code == 404, "fresh_job_absence_unverified")
            else:
                raise prior.NativeProofDenied("document_candidate_release_job_exists_reconcile_only")
            write(journal / (mode + ".create.started.json"), {"body_sha256": target.candidate.sha(body), "automatic_replay": False})
            created = clients.create(body)
            write(journal / (mode + ".create.acknowledged.json"), {"name": created.get("name")})
            deadline = time.monotonic() + 900
            prior.wait_operation(clients, created, "Job", deadline)
            job = clients.job(body["name"])
            plan = {"image": image, "mode": mode, "job_nonce": handle["nonce"]}
            target.candidate.validate_job(job, plan)
            need(prior.integer(job.get("executionCount", 0), 0)
                 and job.get("latestCreatedExecution") in (None, {}), "job_previously_executed")
            write(journal / (mode + ".run.started.json"), {"name": job["name"], "uid": job["uid"],
                  "etag": job["etag"], "automatic_replay": False})
            started = clients.start(job)
            write(journal / (mode + ".run.acknowledged.json"), {"name": started.get("name")})
            prior.wait_operation(clients, started, "Execution", deadline)
            _, _, observed = target._observation(clients, mode, build)
            if mode == "inventory":
                identity = target.candidate.validate_inventory(observed, image, runtime)
                need(identity == {"renderer_version": target.profile.RENDERER, "font_manifest_sha256": target.profile.FONT},
                     "refresh_renderer_changed")
        accepted = target.verify_successor(image, build, journal)
        from dataclasses import asdict
        write(journal / "acceptance.json", asdict(accepted))
        return accepted
    finally:
        clients.close()


def refresh(directory: Path, *, publish: bool, now: datetime, nonce: str | None = None,
            retire_expired_v2: bool = False, upgrade_current_v2_to_v3: bool = False) -> dict[str, Any]:
    """Fresh V3 path; a V2 predecessor needs an explicit, proof-bound dispatch."""
    need(publish is True and now.tzinfo is not None and type(retire_expired_v2) is bool
         and type(upgrade_current_v2_to_v3) is bool
         and not (retire_expired_v2 and upgrade_current_v2_to_v3),
         "explicit_refresh_required")
    source_contract()
    clients = prior.Clients()
    try:
        service = clients.preflight()["service"]
        current = service["template"]["containers"][0]["image"]
        current_policy = policy_for_serving(service, current)
        if retire_expired_v2:
            need(current_policy == prior.POLICY and current != prior.BASE_IMAGE,
                 "expired_v2_predecessor_required")
            try:
                prior.verify_current_successor(current, service)
            except prior.NativeProofDenied as error:
                need(str(error) == "document_native_definitions_expired",
                     "expired_v2_predecessor_unverified")
            else:
                need(False, "expired_v2_predecessor_not_expired")
        elif upgrade_current_v2_to_v3:
            need(current_policy == prior.POLICY and current != prior.BASE_IMAGE,
                 "fresh_v2_predecessor_required")
            # This is a serving acceptance, not the retirement-only path. It
            # must pass again inside transition before any caller is closed.
            prior.verify_current_successor(current, service)
        else:
            need(current_policy == POLICY, "serving_profile_changed")
            verify_current_successor(current, service)
        verify_caller_pins(clients, current, policy_sha256=current_policy)
    finally:
        clients.close()
    builder = prior.build_module()
    recipe = (ROOT / profile.REFRESH_DOCKERFILE).read_bytes()
    build = builder.native_build(directory, recipe, nonce=nonce, candidate_v3=True)
    image = build["immutable_image"]
    need(image not in {current, profile.IMAGE}, "refresh_image_reused")
    accepted = run_successor(build, directory / "native-proof")
    http = builder.native_http()
    try:
        result = transition.transition(image, build, directory / "native-proof", directory / "transition",
                                       http=http, probe=sys.modules[__name__],
                                       retire_expired_v2=retire_expired_v2)
    finally:
        http.close()
    result.update(definition_expires_at=accepted.definition_expires_at, renderer_contract_version=3,
                  required_case_count=11, renderer_parent=profile.IMAGE,
                  prior_evidence_retained=True,
                  fresh_v2_upgraded=upgrade_current_v2_to_v3)
    return result


class ReleaseProbe:
    """Normal-manager read-only proof route; mutations still use maintenance IAM."""
    POLICY, BASE_IMAGE, SERVICE_UID = POLICY, BASE_IMAGE, SERVICE_UID
    NativeProofDenied = prior.NativeProofDenied
    validate_serving = staticmethod(validate_serving)
    policy_for_serving = staticmethod(policy_for_serving)
    verify_caller_pins = staticmethod(verify_caller_pins)

    @staticmethod
    def verify_successor(image, build, journal):
        return verify_successor(image, build, journal, read_only_release=True)

    @staticmethod
    def verify_current_successor(image, metadata):
        if policy_for_serving(metadata, image) == POLICY:
            return verify_current_successor_for_release(image, metadata)
        return prior.verify_current_successor_for_release(image, metadata)

    @staticmethod
    def verify_expired_current_v2_for_retirement(image, metadata):
        return prior.verify_expired_current_v2_for_retirement(image, metadata, read_only_release=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("verify", "promote"))
    parser.add_argument("--corpus-journal", type=Path, required=True)
    parser.add_argument("--transition-journal", type=Path)
    parser.add_argument("--retire-expired-v2", action="store_true",
                        help="Use the separate retirement-only proof after closing all callers")
    args = parser.parse_args()
    need(not args.retire_expired_v2 or args.command == "promote", "recovery_mode_requires_promotion")
    binding_path = args.corpus_journal / "binding.json"
    if binding_path.exists():
        need(not binding_path.is_symlink() and binding_path.stat().st_size <= 65536,
             "candidate_binding_invalid")
        binding = json.loads(binding_path.read_bytes())
        need(type(binding) is dict and set(binding) == {"version", "image", "build_identity"}
             and binding["version"] == 3 and type(binding["image"]) is str
             and type(binding["build_identity"]) is dict,
             "candidate_binding_invalid")
        image = binding["image"]
        recorded = binding["build_identity"]
        build = build_identity(read_only_release=True, build_id=recorded.get("build_id"),
                               nonce=recorded.get("nonce"), image=image)
        need(build == recorded, "candidate_binding_changed")
    else:
        image = profile.IMAGE
        build = build_identity(read_only_release=True)
    if args.command == "verify":
        from dataclasses import asdict
        print(json.dumps(asdict(verify_successor(image, build, args.corpus_journal, read_only_release=True)), sort_keys=True))
        return
    need(args.transition_journal is not None, "transition_journal_required")
    http = prior.build_module().native_http()
    try:
        print(json.dumps(transition.transition(image, build, args.corpus_journal,
            args.transition_journal, http=http, probe=ReleaseProbe(),
            retire_expired_v2=args.retire_expired_v2), sort_keys=True))
    finally:
        http.close()


if __name__ == "__main__":
    try:
        main()
    except Exception as error:  # noqa: BLE001 - never disclose provider bodies or tokens.
        reason = str(error) if isinstance(error, (prior.NativeProofDenied, transition.TransitionDenied)) else "document_candidate_release_unavailable"
        print(json.dumps({"status": "not_verified", "reason": reason, "automatic_replay": False}))
        raise SystemExit(1) from None
