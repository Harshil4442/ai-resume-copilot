"""Secretless private TeX worker contract; no deployment or IAM writes here."""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, NoReturn
from urllib.parse import urlsplit

import document_worker as documents

PROJECT = documents.PROJECT
REGION = documents.REGION
SERVICE = "hirewiz-native-tex"
RESOURCE = f"projects/{PROJECT}/locations/{REGION}/services/{SERVICE}"
SERVICE_ACCOUNT = f"{SERVICE}@{PROJECT}.iam.gserviceaccount.com"
INVOKER = documents.API_INVOKER
REPOSITORY = f"{REGION}-docker.pkg.dev/{PROJECT}/cloud-run-source-deploy/{SERVICE}"
ACCEPTED_IMAGE = (
    REPOSITORY
    + "@sha256:5feb099b9d2eb3f8bbf29ce3bc99130930fa09e194dc5a7695436c347281e291"
)
ACCEPTED_ORIGIN = "https://hirewiz-native-tex-mqff62d75a-uc.a.run.app"
ROOT = Path(__file__).resolve().parents[2]
POLICY_SHA256 = hashlib.sha256((ROOT / "infra/native-tex/policy.json").read_bytes()).hexdigest()


class TexConfigurationDenied(ValueError):
    pass


def fail() -> NoReturn:
    raise TexConfigurationDenied("native_tex_worker_configuration_unverified")


def image_valid(image: Any) -> bool:
    return type(image) is str and re.fullmatch(re.escape(REPOSITORY) + r"@sha256:[a-f0-9]{64}", image) is not None


def api_environment(origin: str, image: str) -> dict[str, str]:
    """Call only with the native verified worker URI and immutable image."""
    target = urlsplit(origin)
    if (not image_valid(image) or target.scheme != "https" or target.username or target.password
            or target.port is not None or target.path not in {"", "/"} or target.query or target.fragment
            or re.fullmatch(SERVICE + r"-[a-z0-9-]+(?:\.[a-z0-9-]+)*\.run\.app", target.hostname or "") is None):
        fail()
    return {"NATIVE_TEX_COMPILER_URL": origin.rstrip("/") + "/compile",
            "NATIVE_TEX_IMAGE_DIGEST": image, "NATIVE_TEX_ISOLATION_POLICY_SHA256": POLICY_SHA256}


def render(image: str, release: str) -> dict[str, Any]:
    if not image_valid(image) or type(release) is not str or re.fullmatch(r"[a-f0-9]{40}", release) is None:
        fail()
    return {"service": {
        "name": RESOURCE, "launchStage": "BETA", "ingress": "INGRESS_TRAFFIC_INTERNAL_ONLY",
        "invokerIamDisabled": False, "defaultUriDisabled": False,
        "traffic": [{"type": "TRAFFIC_TARGET_ALLOCATION_TYPE_LATEST", "percent": 100}],
        "scaling": {"maxInstanceCount": 2},
        "template": {"labels": {"hirewiz-source": release}, "serviceAccount": SERVICE_ACCOUNT,
            "executionEnvironment": "EXECUTION_ENVIRONMENT_GEN2", "maxInstanceRequestConcurrency": 1,
            "timeout": "60s", "scaling": {"minInstanceCount": 0, "maxInstanceCount": 2},
            "containers": [{"name": "native-tex", "image": image, "sandboxLauncher": True,
                "command": ["/usr/bin/python3"], "args": ["-I", "/opt/tex/cloud_service.py"],
                "ports": [{"name": "http1", "containerPort": 8080}],
                "resources": {"limits": {"cpu": "1", "memory": "1Gi"},
                              "cpuIdle": True, "startupCpuBoost": False},
                "startupProbe": {"tcpSocket": {"port": 8080}, "periodSeconds": 10,
                                 "timeoutSeconds": 1, "failureThreshold": 24},
                "env": [{"name": "NATIVE_TEX_IMAGE_DIGEST", "value": image},
                        {"name": "NATIVE_TEX_ISOLATION_POLICY_SHA256", "value": POLICY_SHA256}]}]}},
        "initial_service_iam": {"version": 3, "bindings": []},
        "runtime_service_iam": {"version": 3, "bindings": [
            {"role": "roles/run.invoker", "members": ["serviceAccount:" + INVOKER]}]},
        "native_deployment_proven": False, "native_compile_proven": False}


def check_snapshot(snapshot: dict, image: str, release: str, *, runtime_phase: bool = False) -> dict:
    """Metadata agreement only; caller connectivity/child isolation need execution."""
    try:
        expected = render(image, release)
        service, revision = snapshot["service"], snapshot["revision"]
        if (type(runtime_phase) is not bool or service.get("name") != RESOURCE
                or not service.get("uid") or service.get("reconciling", False) is not False
                or str(service.get("generation")) != str(service.get("observedGeneration"))
                or re.fullmatch(r"[1-9][0-9]{0,18}", str(service.get("generation"))) is None
                or service.get("terminalCondition", {}).get("state") != "CONDITION_SUCCEEDED"):
            fail()
        for key in ("launchStage", "ingress", "invokerIamDisabled", "defaultUriDisabled"):
            default = False if key in {"invokerIamDisabled", "defaultUriDisabled"} else None
            if not documents._same(service.get(key, default), expected["service"][key]):
                fail()
        if any(service.get(key) for key in ("iapEnabled", "multiRegionSettings", "sshEnabled", "customAudiences", "buildConfig")):
            fail()
        documents._service_scaling(service.get("scaling"), expected["service"]["scaling"])
        documents._configuration(service["template"], expected["service"]["template"])
        ready = service.get("latestReadyRevision")
        if (type(ready) is not str or re.fullmatch(re.escape(RESOURCE + "/revisions/" + SERVICE + "-")
                + r"[a-z0-9-]{1,36}", ready) is None or revision.get("name") != ready
                or revision.get("service") not in {RESOURCE, SERVICE} or not revision.get("uid")):
            fail()
        rows = [row for row in revision.get("conditions", []) if row.get("type") == "Ready"]
        if len(rows) != 1 or rows[0].get("state") != "CONDITION_SUCCEEDED":
            fail()
        documents._configuration(revision, expected["service"]["template"])
        if service.get("traffic") != expected["service"]["traffic"]:
            fail()
        if service.get("trafficStatuses") is not None:
            documents._traffic_statuses(service["trafficStatuses"], ready)
        phase = "runtime_service_iam" if runtime_phase else "initial_service_iam"
        if documents._bindings(snapshot["service_iam"]) != expected[phase]["bindings"]:
            fail()
        for binding in documents._bindings(snapshot["project_iam"], project_conditions=True):
            if set(binding["members"]) & {"allUsers", "allAuthenticatedUsers", "serviceAccount:" + SERVICE_ACCOUNT}:
                fail()
        account = snapshot["service_account"]
        keys = snapshot["service_account_keys"].get("keys", [])
        if (account.get("email") != SERVICE_ACCOUNT or account.get("disabled", False) is not False
                or type(keys) is not list or len(keys) > 100
                or any(key.get("keyType") != "SYSTEM_MANAGED" for key in keys)):
            fail()
        env = api_environment(service["uri"], image)
        return {"configuration_matches": True, "api_environment": env, "native_calls": 0,
                "effective_iam_proven": False, "native_compile_proven": False, "native_http_proven": False}
    except (KeyError, TypeError, ValueError, AttributeError):
        fail()


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", required=True)
    parser.add_argument("--release", required=True)
    arguments = parser.parse_args()
    print(json.dumps(render(arguments.image, arguments.release), sort_keys=True))
