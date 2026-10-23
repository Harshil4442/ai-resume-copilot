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
API_INVOKER = f"hirewiz-runtime-20261014@{PROJECT}.iam.gserviceaccount.com"
UPLOAD_COORDINATOR = f"hirewiz-resume-coordinator@{PROJECT}.iam.gserviceaccount.com"
UPLOAD_COORDINATOR_UID = "109155185167233588129"
MAINTENANCE_ACCOUNT = f"hirewiz-document-refresh@{PROJECT}.iam.gserviceaccount.com"
MAINTENANCE_ROLE = f"projects/{PROJECT}/roles/hirewizDocumentServiceMaintenance"
MAINTENANCE_PERMISSIONS = frozenset({"run.services.get", "run.services.getIamPolicy",
                                     "run.services.update", "run.revisions.get"})
MAINTENANCE_SERVICES = frozenset({SERVICE, "ai-resume-parser", "hirewiz-analysis-worker",
                                 "hirewiz-resume-coordinator"})
RESOURCE = f"projects/{PROJECT}/locations/{REGION}/services/{SERVICE}"
POLICY_SHA256 = "d3baf46026934f48b8def219b48d348d7b03bc3b4d0cb713a6f222c7299df7f2"
LEGACY_V1_POLICY_SHA256 = "dbd15ce3120bb6211bb6c1d7b166aaddbd8b80e00a72d564e6274ef10db3aaf1"
ACCEPTED_IMAGE = (
    f"{REGION}-docker.pkg.dev/{PROJECT}/cloud-run-source-deploy/{SERVICE}"
    "@sha256:624597726ce2fefa2b3574fbf0a8ec4ccdc02a826380d76c5edc8ebbd59aa107"
)
ACCEPTED_RENDERER = "LibreOffice 25.2.3.2 520(Build:2)"
ACCEPTED_FONT_MANIFEST = "abaf429caeeeb5fbcc000c878a315c89e23ecd3dc912bb74df10761c144f7d60"
ACCEPTED_ORIGIN = "https://hirewiz-document-inspector-mqff62d75a-uc.a.run.app"
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
        if policy.get("version") != 3 or policy.get("deployment_proven") is not False:
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


def api_environment(origin: str) -> dict[str, str]:
    """The accepted immutable renderer contract; this does not activate wiring."""
    from urllib.parse import urlsplit

    parsed = urlsplit(origin) if type(origin) is str else None
    if (parsed is None or origin not in {ACCEPTED_ORIGIN, ACCEPTED_ORIGIN + "/"}
            or parsed.scheme != "https" or not parsed.hostname
            or parsed.username is not None or parsed.password is not None or parsed.port is not None
            or parsed.path not in {"", "/"} or parsed.query or parsed.fragment):
        _deny()
    return {
        "DOCUMENT_WORKER_URL": ACCEPTED_ORIGIN + "/inspect",
        "DOCUMENT_WORKER_IMAGE_DIGEST": ACCEPTED_IMAGE,
        "DOCUMENT_WORKER_POLICY_SHA256": POLICY_SHA256,
        "DOCUMENT_WORKER_DOCX_RENDERER_VERSION": ACCEPTED_RENDERER,
        "DOCUMENT_WORKER_DOCX_FONT_MANIFEST_SHA256": ACCEPTED_FONT_MANIFEST,
    }


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
        "scaling": {"maxInstanceCount": 2},
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


def _service_scaling(actual: Any, expected: dict[str, Any]) -> None:
    # The v2 default is AUTOMATIC; an omitted zero minimum remains scale-to-zero.
    # Only these explicit defaults may accompany the declared service-wide cap.
    if type(actual) is not dict or not _same(
            {"minInstanceCount": 0, "scalingMode": "AUTOMATIC", **actual},
            {"minInstanceCount": 0, "scalingMode": "AUTOMATIC", **expected}):
        _deny()


def _traffic_statuses(statuses: Any, ready: str) -> None:
    # `ready` is already bound to this exact service. Preserve either API spelling;
    # an arbitrary resource prefix or another revision's basename is never stripped.
    if (type(statuses) is not list or len(statuses) != 1 or type(statuses[0]) is not dict):
        _deny()
    row = statuses[0]
    if (set(row) - {"type", "revision", "percent", "tag", "uri"}
            or row.get("type") != "TRAFFIC_TARGET_ALLOCATION_TYPE_LATEST"
            or row.get("revision") not in (ready, ready.rsplit("/", 1)[-1])
            or type(row.get("percent")) is not int or row["percent"] != 100
            or not _same(row.get("tag", ""), "")):
        _deny()


def _project_condition(value: Any) -> None:
    """Bounded IAM Expr metadata, never CEL evaluation or access authorization."""
    if (type(value) is not dict or "expression" not in value
            or set(value) - {"expression", "title", "description", "location"}
            or any(type(item) is not str for item in value.values())):
        _deny()
    sizes = [len(item.encode("utf-8")) for item in value.values()]
    if (not value["expression"].strip() or any(size > 4096 for size in sizes)
            or sum(sizes) > 8192):
        _deny()


def _bindings(policy: dict[str, Any], *, project_conditions: bool = False) -> list[dict[str, Any]]:
    if (not isinstance(policy, dict) or type(policy.get("version", 1)) is not int
            or policy.get("version", 1) not in (1, 3) or type(project_conditions) is not bool):
        _deny()
    bindings = policy.get("bindings", [])
    if not isinstance(bindings, list) or len(bindings) > 1000:
        _deny()
    for binding in bindings:
        allowed = {"role", "members", "condition"} if project_conditions else {"role", "members"}
        if not isinstance(binding, dict) or set(binding) - allowed:
            _deny()
        if not isinstance(binding.get("role"), str) or not isinstance(binding.get("members"), list):
            _deny()
        members = binding["members"]
        if len(members) > 1000 or any(not isinstance(member, str) for member in members):
            _deny()
        if len(set(members)) != len(members):
            _deny()
        if "condition" in binding:
            if policy.get("version") != 3:
                _deny()
            _project_condition(binding["condition"])
    return bindings


def _coordinator_identity(snapshot: dict[str, Any]) -> None:
    """Only the natively recorded keyless upload principal may join API calls.

    The caller must freshly read these account/key bodies from IAM. Offline
    agreement still does not prove its SQL/storage grants or runtime acceptance.
    """
    account = snapshot["upload_coordinator_service_account"]
    keys = snapshot["upload_coordinator_service_account_keys"].get("keys", [])
    if (not isinstance(account, dict)
            or account.get("name") != f"projects/{PROJECT}/serviceAccounts/{UPLOAD_COORDINATOR}"
            or account.get("projectId") != PROJECT
            or account.get("email") != UPLOAD_COORDINATOR
            or account.get("uniqueId") != UPLOAD_COORDINATOR_UID
            or account.get("disabled", False) is not False
            or not isinstance(keys, list) or len(keys) > 100
            or any(not isinstance(key, dict) or key.get("keyType") != "SYSTEM_MANAGED" for key in keys)):
        _deny()


def service_bindings_without_maintenance(policy: dict[str, Any], service: str) -> tuple[list[dict[str, Any]], bool]:
    """An exact non-invoking updater binding never changes the invoker allowlist."""
    bindings, remaining, found = _bindings(policy, project_conditions=True), [], False
    member = "serviceAccount:" + MAINTENANCE_ACCOUNT
    for binding in bindings:
        related = binding["role"] == MAINTENANCE_ROLE or any(
            MAINTENANCE_ACCOUNT in value for value in binding["members"])
        if not related:
            remaining.append(binding)
            continue
        if (found or service not in MAINTENANCE_SERVICES
                or binding != {"role": MAINTENANCE_ROLE, "members": [member]}):
            _deny()
        found = True
    return remaining, found


def validate_maintenance_identity(snapshot: dict[str, Any], *, expected_uid: str | None = None) -> str:
    """Native readers supply fresh account/key/role bodies; offline data stays offline."""
    try:
        account = snapshot["maintenance_service_account"]
        keys = snapshot["maintenance_service_account_keys"].get("keys", [])
        role = snapshot["maintenance_role"]
        uid = account.get("uniqueId")
        permissions = role.get("includedPermissions")
        if (account.get("name") != f"projects/{PROJECT}/serviceAccounts/{MAINTENANCE_ACCOUNT}"
                or account.get("email") != MAINTENANCE_ACCOUNT or account.get("projectId") != PROJECT
                or account.get("disabled", False) is not False
                or type(uid) is not str or not re.fullmatch(r"[1-9][0-9]{20}", uid)
                or expected_uid is not None and uid != expected_uid
                or type(keys) is not list or len(keys) > 100
                or any(type(key) is not dict or key.get("keyType") != "SYSTEM_MANAGED" for key in keys)
                or role.get("name") != MAINTENANCE_ROLE or role.get("deleted", False) is not False
                or role.get("stage") != "GA" or type(role.get("etag")) is not str or not role["etag"]
                or type(permissions) is not list or len(permissions) != len(MAINTENANCE_PERMISSIONS)
                or set(permissions) != MAINTENANCE_PERMISSIONS):
            _deny()
        return uid
    except (KeyError, TypeError, AttributeError):
        _deny()


def check_snapshot(snapshot: dict[str, Any], image: str, release: str, *, api_phase: bool = False,
                   coordinator_phase: bool = False, historical_v1: bool = False) -> dict[str, Any]:
    """Check bounded native-shaped inputs while explicitly retaining their unknown provenance."""
    try:
        if (type(api_phase) is not bool or type(coordinator_phase) is not bool or type(historical_v1) is not bool
                or coordinator_phase and not api_phase):
            _deny()
        expected = render(image, release)
        if historical_v1:
            # Only the fixed pre-native policy may describe an existing V1
            # service. Rendering new proposals and API pins stays current-only;
            # this offline comparison grants no native or promotion authority.
            source = ROOT / "infra/gcp/document_legacy_v1/policy.json"
            if source.is_symlink():
                _deny()
            raw = source.read_bytes()
            if len(raw) > MAX_INPUT_BYTES or hashlib.sha256(raw).hexdigest() != LEGACY_V1_POLICY_SHA256:
                _deny()
            expected["service"]["template"]["containers"][0]["env"][1]["value"] = LEGACY_V1_POLICY_SHA256
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
        if any(service.get(field) for field in ("iapEnabled", "multiRegionSettings", "sshEnabled",
                                                "customAudiences", "buildConfig")):
            _deny()
        _service_scaling(service.get("scaling"), desired["scaling"])
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
        if statuses is not None:
            _traffic_statuses(statuses, ready)
        _configuration(revision, desired["template"])
        phase = "after_isolation_service_iam" if api_phase else "initial_service_iam"
        wanted = expected[phase]["bindings"]
        if coordinator_phase:
            _coordinator_identity(snapshot)
            wanted = [{"role": "roles/run.invoker", "members": sorted(
                ["serviceAccount:" + API_INVOKER, "serviceAccount:" + UPLOAD_COORDINATOR])}]
        bindings, maintenance = service_bindings_without_maintenance(snapshot["service_iam"], SERVICE)
        if maintenance:
            validate_maintenance_identity(snapshot)
        if bindings != wanted:
            _deny()
        # A project snapshot cannot establish organization/folder/group/custom
        # role access closure. Known public or runtime grants are refused here.
        for binding in _bindings(snapshot["project_iam"], project_conditions=True):
            forbidden = {"allUsers", "allAuthenticatedUsers", "serviceAccount:" + SERVICE_ACCOUNT}
            # Upload SQL/GCS/secret permissions are granted on exact resources,
            # never a project-wide role for this credential-bearing coordinator.
            if coordinator_phase:
                forbidden.add("serviceAccount:" + UPLOAD_COORDINATOR)
            if set(binding["members"]) & forbidden:
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
                "service_iam_phase": ("api-and-resume-coordinator" if coordinator_phase
                                      else "api-only" if api_phase else "service-no-bindings"),
                "traffic_status_checked": statuses is not None,
                "historical_v1": historical_v1,
                "effective_iam_proven": False, "runtime_isolation_proven": False,
                "api_wiring_enabled": False, "native_calls": 0}
    except (OSError, KeyError, TypeError, AttributeError, ValueError):
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
    parser.add_argument("--coordinator-phase", action="store_true")
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
            result = check_snapshot(decode(raw), args.image, args.release, api_phase=args.api_phase,
                                    coordinator_phase=args.coordinator_phase)
        print(json.dumps(result, sort_keys=True))
        return 0
    except (ConfigurationDenied, OSError):
        print(json.dumps({"status": "unverified", "reason": "document_worker_configuration_unverified"}))
        return 65


if __name__ == "__main__":
    raise SystemExit(main())
