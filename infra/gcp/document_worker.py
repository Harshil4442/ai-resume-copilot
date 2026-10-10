"""Render/check a disabled document worker proposal without any cloud transport.

Offline snapshot agreement is never native IAM, sandbox or release evidence.
This module cannot deploy, grant IAM, enable API wiring or override monetary gates.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from typing import Any, NoReturn

PROJECT = "ai-resume-parser-482412"
REGION = "us-central1"
SERVICE = "hirewiz-document-inspector"
SERVICE_ACCOUNT = f"{SERVICE}@{PROJECT}.iam.gserviceaccount.com"
API_INVOKER = f"hirewiz-api@{PROJECT}.iam.gserviceaccount.com"
RESOURCE = f"projects/{PROJECT}/locations/{REGION}/services/{SERVICE}"
POLICY_SHA256 = "425fd4b326554d8fee8d2686f423c8e2b499eaaa1bd7858eb69f7759ac526bdf"
ROOT = Path(__file__).resolve().parents[2]
MAX_INPUT_BYTES = 256 * 1024
_IMAGE = re.compile(re.escape(f"{REGION}-docker.pkg.dev/{PROJECT}/cloud-run-source-deploy/{SERVICE}")
                    + r"@sha256:[a-f0-9]{64}\Z")
_RELEASE = re.compile(r"[a-f0-9]{40}\Z")


class ConfigurationDenied(ValueError):
    """A fixed reason only; external names, values and metadata are never echoed."""


def _deny() -> NoReturn:
    raise ConfigurationDenied("document_worker_configuration_unverified") from None


def _same(actual: Any, expected: Any) -> bool:
    if type(actual) is not type(expected):
        return False
    if isinstance(expected, dict):
        return set(actual) == set(expected) and all(_same(actual[key], value) for key, value in expected.items())
    if isinstance(expected, list):
        return len(actual) == len(expected) and all(_same(left, right) for left, right in zip(actual, expected, strict=True))
    return bool(actual == expected)


def _pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            _deny()
        result[key] = value
    return result


def decode(raw: bytes) -> Any:
    try:
        if type(raw) is not bytes or len(raw) > MAX_INPUT_BYTES:
            _deny()
        return json.loads(raw, object_pairs_hook=_pairs)
    except (ValueError, UnicodeError, RecursionError):
        _deny()


def _proposal() -> dict[str, Any]:
    try:
        source = ROOT / "infra/document-processing/policy.json"
        draft = ROOT / "infra/gcp/document-worker.disabled.json"
        if source.is_symlink() or draft.is_symlink():
            _deny()
        raw = source.read_bytes()
        if len(raw) > MAX_INPUT_BYTES or hashlib.sha256(raw).hexdigest() != POLICY_SHA256:
            _deny()
        policy = decode(raw)
        if policy.get("version") != 2 or policy.get("deployment_proven") is not False:
            _deny()
        proposal = decode(draft.read_bytes())
        if not _same(proposal, {
                        "version": 1, "project": PROJECT, "region": REGION, "service": SERVICE,
                        "service_account": SERVICE_ACCOUNT, "api_invoker": API_INVOKER,
                        "policy_sha256": POLICY_SHA256, "image": None,
                        "api_wiring_enabled": False, "deployment_proven": False}):
            _deny()
        return proposal
    except (OSError, AttributeError, TypeError, ValueError):
        _deny()
    raise AssertionError("unreachable")


def plan() -> dict[str, Any]:
    _proposal()
    return {"proposal_version": 1, "image_bound": False, "native_calls": 0,
            "api_wiring_enabled": False, "deployment_proven": False,
            "next_required": "reviewed_amd64_image_and_native_isolation_evidence"}


def render(image: str, release: str) -> dict[str, Any]:
    _proposal()
    if not isinstance(image, str) or not _IMAGE.fullmatch(image):
        _deny()
    if not isinstance(release, str) or not _RELEASE.fullmatch(release):
        _deny()
    # Cloud Run v2 request shape, not a gcloud YAML export. No deployment occurs.
    service = {
        "name": RESOURCE, "launchStage": "BETA", "ingress": "INGRESS_TRAFFIC_INTERNAL_ONLY",
        "invokerIamDisabled": False, "defaultUriDisabled": False,
        "traffic": [{"type": "TRAFFIC_TARGET_ALLOCATION_TYPE_LATEST", "percent": 100}],
        "template": {
            "labels": {"hirewiz-source": release}, "serviceAccount": SERVICE_ACCOUNT,
            "executionEnvironment": "EXECUTION_ENVIRONMENT_GEN2",
            "maxInstanceRequestConcurrency": 1, "timeout": "90s",
            "scaling": {"minInstanceCount": 0, "maxInstanceCount": 2},
            "containers": [{
                "name": "document-inspector", "image": image, "sandboxLauncher": True,
                "command": ["/usr/local/bin/python3"], "args": ["-I", "/opt/document/service.py"],
                "ports": [{"name": "http1", "containerPort": 8080}],
                "resources": {"limits": {"cpu": "2", "memory": "4Gi"},
                              "cpuIdle": True, "startupCpuBoost": False},
                "startupProbe": {"tcpSocket": {"port": 8080}, "periodSeconds": 10,
                                 "timeoutSeconds": 1, "failureThreshold": 24},
                "env": [{"name": "DOCUMENT_WORKER_IMAGE_DIGEST", "value": image},
                        {"name": "DOCUMENT_WORKER_POLICY_SHA256", "value": POLICY_SHA256}],
            }],
        },
    }
    return {"service": service,
            "initial_service_iam": {"version": 3, "bindings": []},
            "after_isolation_service_iam": {"version": 3, "bindings": [
                {"role": "roles/run.invoker", "members": ["serviceAccount:" + API_INVOKER]}]},
            "api_wiring_enabled": False, "runtime_isolation_proven": False}


def _configuration(actual: dict[str, Any], expected: dict[str, Any]) -> None:
    for field in ("serviceAccount", "executionEnvironment", "maxInstanceRequestConcurrency", "timeout"):
        if not _same(actual.get(field), expected[field]):
            _deny()
    scaling = actual.get("scaling")
    if not isinstance(scaling, dict) or not _same({"minInstanceCount": 0, **scaling}, expected["scaling"]):
        _deny()
    if actual.get("labels", {}).get("hirewiz-source") != expected["labels"]["hirewiz-source"]:
        _deny()
    # No sidecars, mounts, connector, mesh or alternative execution source. These
    # are additional access paths even if the two allowed environment pins match.
    for field in ("volumes", "vpcAccess", "serviceMesh", "encryptionKey", "nodeSelector"):
        if actual.get(field):
            _deny()
    containers = actual.get("containers")
    if not isinstance(containers, list) or len(containers) != 1 or not isinstance(containers[0], dict):
        _deny()
    container = dict(containers[0])
    if isinstance(container.get("resources"), dict):
        container["resources"] = {"startupCpuBoost": False, **container["resources"]}
    desired = expected["containers"][0]
    for field, value in desired.items():
        if not _same(container.get(field), value):
            _deny()
    if set(container) - set(desired):
        _deny()


def _bindings(policy: dict[str, Any]) -> list[dict[str, Any]]:
    if (not isinstance(policy, dict) or type(policy.get("version", 1)) is not int
            or policy.get("version", 1) not in (1, 3)):
        _deny()
    bindings = policy.get("bindings", [])
    if not isinstance(bindings, list) or len(bindings) > 1000:
        _deny()
    for binding in bindings:
        if not isinstance(binding, dict) or set(binding) - {"role", "members"}:
            _deny()
        if not isinstance(binding.get("role"), str) or not isinstance(binding.get("members"), list):
            _deny()
        members = binding["members"]
        if len(members) > 1000 or any(not isinstance(member, str) for member in members):
            _deny()
        if len(set(members)) != len(members):
            _deny()
    return bindings


def check_snapshot(snapshot: dict[str, Any], image: str, release: str, *, api_phase: bool = False) -> dict[str, Any]:
    """Check bounded native-shaped inputs while explicitly retaining their unknown provenance."""
    try:
        if type(api_phase) is not bool:
            _deny()
        expected = render(image, release)
        service, revision = snapshot["service"], snapshot["revision"]
        desired = expected["service"]
        generation = service.get("generation")
        observed = service.get("observedGeneration")
        if (type(generation) not in (int, str) or type(observed) not in (int, str)
                or not re.fullmatch(r"[1-9][0-9]{0,18}", str(generation))
                or str(generation) != str(observed)):
            _deny()
        if (service.get("name") != RESOURCE or not isinstance(service.get("uid"), str) or not service["uid"]
                or service.get("reconciling", False) is not False
                or service.get("terminalCondition", {}).get("state") != "CONDITION_SUCCEEDED"):
            _deny()
        for field in ("launchStage", "ingress", "invokerIamDisabled", "defaultUriDisabled"):
            default = False if field in {"invokerIamDisabled", "defaultUriDisabled"} else None
            if not _same(service.get(field, default), desired[field]):
                _deny()
        if any(service.get(field) for field in ("iapEnabled", "multiRegionSettings", "scaling", "sshEnabled",
                                                "customAudiences", "buildConfig")):
            _deny()
        _configuration(service["template"], desired["template"])
        ready = service.get("latestReadyRevision")
        if (not isinstance(ready, str) or not re.fullmatch(
                re.escape(RESOURCE + "/revisions/" + SERVICE + "-") + r"[a-z0-9-]{1,36}", ready)):
            _deny()
        if (revision.get("name") != ready or revision.get("service") not in (RESOURCE, SERVICE)
                or not isinstance(revision.get("uid"), str) or not revision["uid"]):
            _deny()
        ready_rows = [row for row in revision.get("conditions", []) if row.get("type") == "Ready"]
        if len(ready_rows) != 1 or ready_rows[0].get("state") != "CONDITION_SUCCEEDED":
            _deny()
        if not _same(service.get("traffic"), desired["traffic"]):
            _deny()
        statuses = service.get("trafficStatuses")
        if statuses is not None and (not isinstance(statuses, list) or len(statuses) != 1
                or statuses[0].get("revision") != ready
                or type(statuses[0].get("percent")) is not int or statuses[0]["percent"] != 100
                or statuses[0].get("tag")):
            _deny()
        _configuration(revision, desired["template"])
        phase = "after_isolation_service_iam" if api_phase else "initial_service_iam"
        if _bindings(snapshot["service_iam"]) != expected[phase]["bindings"]:
            _deny()
        # A project snapshot cannot establish organization/folder/group/custom
        # role access closure. Known public or runtime grants are refused here.
        for binding in _bindings(snapshot["project_iam"]):
            if set(binding["members"]) & {"allUsers", "allAuthenticatedUsers", "serviceAccount:" + SERVICE_ACCOUNT}:
                _deny()
        account = snapshot["service_account"]
        if account.get("email") != SERVICE_ACCOUNT or account.get("disabled", False) is not False:
            _deny()
        keys = snapshot["service_account_keys"].get("keys", [])
        if not isinstance(keys, list) or len(keys) > 100:
            _deny()
        if any(key.get("keyType") != "SYSTEM_MANAGED" for key in keys):
            _deny()
        return {"configuration_matches": True, "input_authority": "untrusted_offline_snapshot",
                "service_iam_phase": "api-only" if api_phase else "service-no-bindings",
                "traffic_status_checked": statuses is not None,
                "effective_iam_proven": False, "runtime_isolation_proven": False,
                "api_wiring_enabled": False, "native_calls": 0}
    except (KeyError, TypeError, AttributeError, ValueError):
        _deny()
    raise AssertionError("unreachable")


def main() -> int:
    class FixedParser(argparse.ArgumentParser):
        def error(self, message: str) -> NoReturn:
            _deny()

    parser = FixedParser(description=__doc__)
    parser.add_argument("mode", nargs="?", choices=("plan", "render", "check"), default="plan")
    parser.add_argument("--image")
    parser.add_argument("--release")
    parser.add_argument("--snapshot", type=Path)
    parser.add_argument("--api-phase", action="store_true")
    try:
        args = parser.parse_args()
        if args.mode == "plan":
            result = plan()
        elif args.mode == "render":
            result = render(args.image, args.release)
        else:
            if args.snapshot is None or args.snapshot.is_symlink() or args.snapshot.stat().st_size > MAX_INPUT_BYTES:
                _deny()
            with args.snapshot.open("rb") as handle:
                raw = handle.read(MAX_INPUT_BYTES + 1)
            result = check_snapshot(decode(raw), args.image, args.release, api_phase=args.api_phase)
        print(json.dumps(result, sort_keys=True))
        return 0
    except (ConfigurationDenied, OSError):
        print(json.dumps({"status": "unverified", "reason": "document_worker_configuration_unverified"}))
        return 65


if __name__ == "__main__":
    raise SystemExit(main())
